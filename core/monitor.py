import asyncio
import logging
import uuid
from typing import Dict, Optional, Set
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaAudio, InputMediaPhoto, InputMediaVideo

from core.fetcher import FeedFetcher
from core.models import Config, Entry, FeedConfig
from core.state import StateManager
from processing.base import ProcessingPipeline

logger = logging.getLogger(__name__)

# Telegram limits
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024

# delay between requests to the same domain
DEFAULT_DOMAIN_DELAY = 2.0


class DomainRateLimiter:
    def __init__(self, default_delay: float = DEFAULT_DOMAIN_DELAY):
        self._default_delay = default_delay
        self._domain_locks: Dict[str, asyncio.Lock] = {}
        self._last_request: Dict[str, float] = {}
        self._meta_lock = asyncio.Lock()

    @staticmethod
    def get_domain(url: str) -> str:
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except Exception:
            return url  # Fallback to full URL as key

    async def acquire(self, url: str, delay: Optional[float] = None) -> None:
        domain = self.get_domain(url)
        delay = delay if delay is not None else self._default_delay

        async with self._meta_lock:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = asyncio.Lock()
            lock = self._domain_locks[domain]

        async with lock:
            async with self._meta_lock:
                last = self._last_request.get(domain, 0)

            now = asyncio.get_event_loop().time()
            elapsed = now - last

            if elapsed < delay:
                wait_time = delay - elapsed
                logger.debug(f"Rate limiting {domain}: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)

            async with self._meta_lock:
                self._last_request[domain] = asyncio.get_event_loop().time()

    async def clear(self):
        async with self._meta_lock:
            self._domain_locks.clear()
            self._last_request.clear()


class FeedMonitor:
    _instance_id: Optional[str] = None
    _active_instance: Optional["FeedMonitor"] = None

    def __init__(self, config: Config, state_manager: StateManager, bot: Bot, pipeline: ProcessingPipeline):
        self.config = config
        self.state = state_manager
        self.bot = bot
        self.pipeline = pipeline
        self.fetcher = FeedFetcher()
        self._id = str(uuid.uuid4())[:8]
        self._tasks: Dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()
        self._registry_lock = asyncio.Lock()
        self._channel_locks: Dict[int, asyncio.Lock] = {}
        domain_delay = config.global_config.domain_delay if hasattr(config.global_config, "domain_delay") else DEFAULT_DOMAIN_DELAY
        self._rate_limiter = DomainRateLimiter(default_delay=domain_delay)

        logger.info(f"FeedMonitor created with ID: {self._id}, domain_delay: {domain_delay}s")

    async def start(self):
        if FeedMonitor._active_instance is not None and FeedMonitor._active_instance._id != self._id:
            logger.warning(f"Another monitor instance exists (ID: {FeedMonitor._active_instance._id}). Stopping it first.")
            await FeedMonitor._active_instance.stop()

        FeedMonitor._active_instance = self
        FeedMonitor._instance_id = self._id

        self._shutdown_event.clear()

        logger.info(f"[{self._id}] Starting feed monitor")

        feed_urls: Set[str] = set()
        for channel in self.config.channels:
            for feed_url in channel.feeds.keys():
                feed_urls.add(feed_url)

        async with self._registry_lock:
            for feed_url in feed_urls:
                if feed_url not in self._tasks:
                    task = asyncio.create_task(self._monitor_feed_loop(feed_url), name=f"monitor_{self._id}_{feed_url[:50]}")
                    self._tasks[feed_url] = task
                    logger.debug(f"[{self._id}] Created task for: {feed_url}")

        logger.info(f"[{self._id}] Monitoring {len(self._tasks)} feeds")

    async def stop(self, timeout: float = 10.0):
        logger.info(f"[{self._id}] Stopping feed monitor...")

        self._shutdown_event.set()

        async with self._registry_lock:
            if not self._tasks:
                logger.info(f"[{self._id}] No tasks to stop")
                return

            for feed_url, task in self._tasks.items():
                if not task.done():
                    task.cancel()
                    logger.debug(f"[{self._id}] Cancelled task for: {feed_url}")

            tasks_list = list(self._tasks.values())

        if tasks_list:
            try:
                done, pending = await asyncio.wait(tasks_list, timeout=timeout)

                if pending:
                    logger.warning(f"[{self._id}] {len(pending)} tasks didn't stop in time, forcing cancellation")
                    for task in pending:
                        task.cancel()
                    # Wait a bit more for forced cancellation
                    await asyncio.wait(pending, timeout=2.0)

                logger.info(f"[{self._id}] All {len(done)} tasks stopped")

            except Exception as e:
                logger.error(f"[{self._id}] Error waiting for tasks: {e}")

        async with self._registry_lock:
            self._tasks.clear()
            self._channel_locks.clear()
        await self._rate_limiter.clear()

        if FeedMonitor._active_instance is self:
            FeedMonitor._active_instance = None
            FeedMonitor._instance_id = None

        logger.info(f"[{self._id}] Feed monitor stopped")

    async def _monitor_feed_loop(self, feed_url: str):
        logger.debug(f"[{self._id}] Starting monitor loop for: {feed_url}")

        while not self._shutdown_event.is_set():
            interval = 300

            try:
                feed_config = self.config.get_feed_config(feed_url)
                if not feed_config:
                    logger.warning(f"[{self._id}] No config for feed: {feed_url}, will retry")
                    await self._interruptible_sleep(60)
                    continue

                channel = self.config.get_channel_for_feed(feed_url)
                if not channel:
                    logger.warning(f"[{self._id}] No channel for feed: {feed_url}, will retry")
                    await self._interruptible_sleep(60)
                    continue

                interval = feed_config.check_interval or 300

                await self._check_feed(channel.id, feed_url, feed_config)

            except asyncio.CancelledError:
                logger.debug(f"[{self._id}] Task cancelled for: {feed_url}")
                return
            except Exception as e:
                logger.error(f"[{self._id}] Error checking feed {feed_url}: {e}", exc_info=True)
                await self.state.increment_error(feed_url)

                state = self.state.get_state(feed_url)
                if state.error_count >= 5:
                    interval = max(interval, 600)

            await self._interruptible_sleep(interval)

        logger.debug(f"[{self._id}] Monitor loop ended for: {feed_url}")

    async def _interruptible_sleep(self, seconds: float):
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _check_feed(self, channel_id: int, feed_url: str, feed_config: FeedConfig):
        logger.debug(f"[{self._id}] Checking feed: {feed_url}")

        state = self.state.get_state(feed_url)
        is_first_run = not state.last_check

        await self._rate_limiter.acquire(feed_url)

        try:
            entries, etag, last_modified, feed_title, feed_link = await self.fetcher.fetch(feed_url, state.etag, state.last_modified)
        except Exception as e:
            logger.error(f"[{self._id}] Fetch error for {feed_url}: {e}")
            await self.state.increment_error(feed_url)
            return

        await self.state.update_metadata(feed_url, etag, last_modified, feed_title, feed_link)

        if not entries:
            return

        new_entries = [e for e in entries if not await self.state.is_processed(feed_url, e.guid)]

        if is_first_run and new_entries:
            logger.info(f"[{self._id}] First run for {feed_url}: marking {len(new_entries)} entries as seen")
            for entry in new_entries:
                await self.state.mark_processed(feed_url, entry.guid)
            return

        if not new_entries:
            return

        logger.info(f"[{self._id}] Found {len(new_entries)} new entries in {feed_config.name or feed_url}")

        for entry in reversed(new_entries):
            if self._shutdown_event.is_set():
                logger.debug(f"[{self._id}] Shutdown during entry processing, stopping")
                return

            try:
                await self._process_and_send_entry(channel_id, feed_url, entry, feed_config)
            except Exception as e:
                logger.error(f"[{self._id}] Error processing entry {entry.link}: {e}", exc_info=True)

    async def process_entry(self, entry: Entry, feed_url: str, feed_config: FeedConfig) -> Entry:
        global_config = {
            "feed_link": feed_url,
            "feed_name": feed_config.name or entry.feed_title or "RSS Feed",
            "extra_flags": feed_config.extra_flags,
        }

        channel = self.config.get_channel_for_feed(feed_url)
        if channel:
            global_config["channel_name"] = channel.name

        try:
            processed = await self.pipeline.process(entry, feed_config.processing, global_config)
        except Exception as e:
            logger.error(f"[{self._id}] Pipeline error: {e}", exc_info=True)
            processed = entry
            processed.formatted_message = self._minimal_fallback(entry)

        if not processed.formatted_message:
            processed.formatted_message = self._minimal_fallback(processed)

        return processed

    async def _get_channel_lock(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._channel_locks:
            self._channel_locks[channel_id] = asyncio.Lock()
        return self._channel_locks[channel_id]

    async def _process_and_send_entry(self, channel_id: int, feed_url: str, entry: Entry, feed_config: FeedConfig):
        processed = await self.process_entry(entry, feed_url, feed_config)

        if processed.filtered:
            logger.info(f"[{self._id}] Entry filtered: {entry.title}")
            await self.state.mark_processed(feed_url, entry.guid)
            return

        lock = await self._get_channel_lock(channel_id)

        async with lock:
            try:
                await self.send_entry_with_media(
                    chat_id=channel_id,
                    message=processed.formatted_message,
                    entry=processed,
                    enable_preview=feed_config.enable_preview,
                )
                logger.info(f"[{self._id}] Sent: {entry.title[:50]}")
                await self.state.mark_processed(feed_url, entry.guid)

                await asyncio.sleep(self.config.global_config.send_delay)

            except TelegramRetryAfter as e:
                logger.warning(f"[{self._id}] Flood control, waiting {e.retry_after}s")
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                logger.error(f"[{self._id}] Bot blocked from channel {channel_id}")
            except TelegramBadRequest as e:
                logger.error(f"[{self._id}] Bad request: {e}")
                await self.state.mark_processed(feed_url, entry.guid)
            except Exception as e:
                logger.error(f"[{self._id}] Send error: {e}", exc_info=True)

    def _minimal_fallback(self, entry: Entry) -> str:
        title = self._escape_html(entry.title)
        feed = entry.feed_title or "RSS Feed"
        return f"<b>{title}</b>\n\nvia <a href='{entry.link}'>{self._escape_html(feed)}</a>"

    def _escape_html(self, text: str) -> str:
        if not text:
            return ""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

    async def send_entry_with_media(self, chat_id: int, message: str, entry: Entry, enable_preview: bool = False):
        images = entry.image_buffers[:10] if entry.image_buffers else entry.images[:10]
        videos = entry.video_buffers[:10] if entry.video_buffers else entry.videos[:10]
        audios = entry.audio_buffers[:10] if entry.audio_buffers else entry.audios[:10]

        has_media = bool(images or videos or audios)
        max_length = MAX_CAPTION_LENGTH if has_media else MAX_MESSAGE_LENGTH

        if len(message) > max_length:
            title = self._escape_html(entry.title)
            message = f"<b>{title}</b>\n\n<a href='{entry.link}'>Read more</a>"
            if len(message) > max_length:
                available = max_length - 50
                message = f"<b>{title[:available]}...</b>\n\n<a href='{entry.link}'>Read more</a>"

        has_visual = bool(images or videos)
        has_audio = bool(audios)
        use_img_buf = bool(entry.image_buffers)
        use_vid_buf = bool(entry.video_buffers)
        use_aud_buf = bool(entry.audio_buffers)

        try:
            if has_visual and has_audio:
                await self._send_visual_media(chat_id, images, videos, message, use_img_buf, use_vid_buf)
                await self._send_audio_media(chat_id, audios, None, use_aud_buf)
            elif has_visual:
                await self._send_visual_media(chat_id, images, videos, message, use_img_buf, use_vid_buf)
            elif has_audio:
                await self._send_audio_media(chat_id, audios, message, use_aud_buf)
            else:
                await self.bot.send_message(chat_id=chat_id, text=message, disable_web_page_preview=not enable_preview)
        except Exception as e:
            logger.error(f"[{self._id}] Media send error: {e}", exc_info=True)
            raise

    async def _send_visual_media(self, chat_id: int, images, videos, caption: str, use_img_buf: bool, use_vid_buf: bool):
        total = len(images) + len(videos)

        if total > 1:
            media = []
            for i, img in enumerate(images[:10]):
                if use_img_buf:
                    data, url, filename = img
                    file = BufferedInputFile(data, filename=filename or f"photo_{i}.jpg")
                    media.append(InputMediaPhoto(media=file, caption=caption if i == 0 else None))
                else:
                    media.append(InputMediaPhoto(media=img, caption=caption if i == 0 else None))

            for i, vid in enumerate(videos[: 10 - len(images)]):
                cap_idx = len(images) + i
                if use_vid_buf:
                    data, url, filename = vid
                    file = BufferedInputFile(data, filename=filename or f"video_{i}.mp4")
                    media.append(InputMediaVideo(media=file, caption=caption if cap_idx == 0 else None))
                else:
                    media.append(InputMediaVideo(media=vid, caption=caption if cap_idx == 0 else None))

            await self.bot.send_media_group(chat_id=chat_id, media=media)
        elif images:
            if use_img_buf:
                data, url, filename = images[0]
                file = BufferedInputFile(data, filename=filename or "photo.jpg")
                await self.bot.send_photo(chat_id=chat_id, photo=file, caption=caption)
            else:
                await self.bot.send_photo(chat_id=chat_id, photo=images[0], caption=caption)
        elif videos:
            if use_vid_buf:
                data, url, filename = videos[0]
                file = BufferedInputFile(data, filename=filename or "video.mp4")
                await self.bot.send_video(chat_id=chat_id, video=file, caption=caption, supports_streaming=True)
            else:
                await self.bot.send_video(chat_id=chat_id, video=videos[0], caption=caption, supports_streaming=True)

    async def _send_audio_media(self, chat_id: int, audios, caption: str, use_buf: bool):
        if len(audios) > 1:
            media = []
            for i, audio in enumerate(audios[:10]):
                if use_buf:
                    data, url, filename, thumb = audio
                    file = BufferedInputFile(data, filename=filename or f"audio_{i}.mp3")
                    thumb_file = BufferedInputFile(thumb, filename="thumb.jpg") if thumb else None
                    media.append(InputMediaAudio(media=file, caption=caption if i == 0 else None, thumbnail=thumb_file))
                else:
                    media.append(InputMediaAudio(media=audio, caption=caption if i == 0 else None))

            await self.bot.send_media_group(chat_id=chat_id, media=media)
        elif audios:
            if use_buf:
                data, url, filename, thumb = audios[0]
                file = BufferedInputFile(data, filename=filename or "audio.mp3")
                thumb_file = BufferedInputFile(thumb, filename="thumb.jpg") if thumb else None
                await self.bot.send_audio(chat_id=chat_id, audio=file, caption=caption or "", thumbnail=thumb_file)
            else:
                await self.bot.send_audio(chat_id=chat_id, audio=audios[0], caption=caption or "")
