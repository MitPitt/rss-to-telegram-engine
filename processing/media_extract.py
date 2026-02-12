import asyncio
import contextlib
import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from PIL import Image

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)


class MediaExtractProcessor(Processor):
    name = "media_extract"

    MAX_CONCURRENT_DOWNLOADS = 5
    DEFAULT_MAX_SIZE = 20 * 1024 * 1024
    DEFAULT_MIN_IMAGE_MEGAPIXELS = 0.0625  # 250x250 = 62,500 pixels = 0.0625 MP
    DEFAULT_MAX_ASPECT_RATIO = 20.0  # 20:1 or 1:20
    DEFAULT_DOWNSCALE_IMAGES = True
    DEFAULT_MAX_IMAGE_MEGAPIXELS = 4.0  # 4MP max to stay under Telegram's 10MB limit

    # Patterns for srcset parsing
    SRCSET_PATTERN = re.compile(r"(?:^|,\s*)(?P<url>\S+)(?:\s+(?P<number>\d+(\.\d+)?)(?P<unit>[wx]))?\s*(?=,|$)")

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        skip_if_has_media = config.get("skip_if_has_media", False)
        if skip_if_has_media:
            has_media = entry.images or entry.videos or entry.audios or entry.image_buffers or entry.video_buffers or entry.audio_buffers
            if has_media:
                logger.info(
                    f"Skipping media extraction for '{entry.title[:50]}': "
                    f"entry already has media (videos={len(entry.videos)}, images={len(entry.images)}, "
                    f"audios={len(entry.audios)}, buffers={len(entry.image_buffers) + len(entry.video_buffers) + len(entry.audio_buffers)})"
                )
                return entry

        feed_link = config.get("feed_link")
        remove_media_tags = config.get("remove_media_tags", True)

        logger.debug(f"Extracting media from entry: {entry.title[:50]}")

        # Extract media from enclosures (RSS/Atom attachments)
        self._extract_from_enclosures(entry)

        # Extract media from HTML content
        if entry.content:
            images, videos, audios, cleaned_content = self._extract_from_html(entry.content, feed_link, remove_media_tags)

            logger.info(f"Extracted from HTML: {len(images)} images, {len(videos)} videos, {len(audios)} audios")

            # Merge with existing media (from enclosures)
            for img in images:
                if img not in entry.images:
                    entry.images.append(img)

            for vid in videos:
                if vid not in entry.videos:
                    entry.videos.append(vid)

            for aud in audios:
                if aud not in entry.audios:
                    entry.audios.append(aud)

            if remove_media_tags:
                entry.content = cleaned_content

        # Deduplication
        entry.images = list(dict.fromkeys(entry.images))
        entry.videos = list(dict.fromkeys(entry.videos))
        entry.audios = list(dict.fromkeys(entry.audios))

        logger.info(
            f"Media extraction complete for '{entry.title[:50]}': {len(entry.images)} images, {len(entry.videos)} videos, {len(entry.audios)} audios"
        )

        download_media = config.get("download_media", True)
        if download_media:
            max_size = config.get("max_media_size", self.DEFAULT_MAX_SIZE)
            timeout = config.get("download_timeout", 30)
            min_image_megapixels = config.get("min_image_megapixels", self.DEFAULT_MIN_IMAGE_MEGAPIXELS)
            max_aspect_ratio = config.get("max_aspect_ratio", self.DEFAULT_MAX_ASPECT_RATIO)
            downscale_images = config.get("downscale_images", self.DEFAULT_DOWNSCALE_IMAGES)
            max_image_megapixels = config.get("max_image_megapixels", self.DEFAULT_MAX_IMAGE_MEGAPIXELS)
            await self._download_media(entry, max_size, timeout, min_image_megapixels, max_aspect_ratio, downscale_images, max_image_megapixels)

        return entry

    def _extract_from_enclosures(self, entry: Entry) -> None:
        """Extract media URLs from RSS/Atom enclosures."""
        if not entry.enclosures:
            return

        logger.debug(f"Processing {len(entry.enclosures)} enclosures")

        for enclosure_url in entry.enclosures:
            if not enclosure_url or not enclosure_url.startswith(("http://", "https://")):
                continue

            url_lower = enclosure_url.lower()

            # Images
            if any(url_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".svg")):
                if enclosure_url not in entry.images:
                    entry.images.append(enclosure_url)
                    logger.debug(f"Found image enclosure: {enclosure_url[:80]}")

            # Videos and GIFs
            elif any(url_lower.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".avi", ".gif", ".gifv", ".m4v")):
                if enclosure_url not in entry.videos:
                    entry.videos.append(enclosure_url)
                    logger.debug(f"Found video enclosure: {enclosure_url[:80]}")

            # Audio
            elif any(url_lower.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav")):
                if enclosure_url not in entry.audios:
                    entry.audios.append(enclosure_url)
                    logger.debug(f"Found audio enclosure: {enclosure_url[:80]}")

            # Heuristic: if contains image keywords, assume it's an image
            elif any(keyword in url_lower for keyword in ("image", "thumb", "img", "photo", "pic")):
                if enclosure_url not in entry.images:
                    entry.images.append(enclosure_url)
                    logger.debug(f"Found image enclosure (by keyword): {enclosure_url[:80]}")

    def _extract_from_html(self, html: str, feed_link: Optional[str], remove_tags: bool) -> Tuple[List[str], List[str], List[str], str]:
        """
        Extract media URLs from HTML content.

        Returns:
            Tuple of (images, videos, audios, cleaned_html)
        """
        images: List[str] = []
        videos: List[str] = []
        audios: List[str] = []

        try:
            soup = BeautifulSoup(html, "lxml")

            # Extract images
            for img in soup.find_all("img"):
                url = self._get_best_image_url(img, feed_link)
                if url and not self._is_emoticon(img):
                    images.append(url)
                if remove_tags:
                    img.decompose()

            # Extract videos
            for video in soup.find_all("video"):
                urls = self._get_media_sources(video, feed_link)
                videos.extend(urls)
                if remove_tags:
                    video.decompose()

            # Extract audio
            for audio in soup.find_all("audio"):
                urls = self._get_media_sources(audio, feed_link)
                audios.extend(urls)
                if remove_tags:
                    audio.decompose()

            cleaned_html = str(soup) if remove_tags else html

        except Exception as e:
            logger.error(f"Error parsing HTML for media: {e}", exc_info=True)
            cleaned_html = html

        return images, videos, audios, cleaned_html

    def _get_best_image_url(self, img_tag, feed_link: Optional[str]) -> Optional[str]:
        """Get the best quality image URL from an img tag."""
        src = img_tag.get("src")
        srcset = img_tag.get("srcset")

        if srcset:
            # Parse srcset and get highest resolution
            best_url = self._parse_srcset(srcset, src)
            if best_url:
                return self._resolve_url(best_url, feed_link)

        if src:
            return self._resolve_url(src, feed_link)

        return None

    def _parse_srcset(self, srcset: str, fallback_src: Optional[str] = None) -> Optional[str]:
        """Parse srcset attribute and return the highest resolution URL."""
        matches = []
        for match in self.SRCSET_PATTERN.finditer(srcset):
            url = match.group("url")
            number = float(match.group("number")) if match.group("number") else 1
            unit = match.group("unit") or "x"
            matches.append({"url": url, "number": number, "unit": unit})

        if fallback_src:
            matches.append({"url": fallback_src, "number": 1, "unit": "x"})

        if not matches:
            return None

        # Prefer width-based, then pixel density
        w_matches = sorted([m for m in matches if m["unit"] == "w"], key=lambda m: m["number"], reverse=True)
        x_matches = sorted([m for m in matches if m["unit"] == "x"], key=lambda m: m["number"], reverse=True)

        if w_matches:
            return w_matches[0]["url"]
        if x_matches:
            return x_matches[0]["url"]

        return matches[0]["url"]

    def _get_media_sources(self, tag, feed_link: Optional[str]) -> List[str]:
        """Get all source URLs from a video/audio tag."""
        urls = []

        # Check src attribute
        src = tag.get("src")
        if src:
            resolved = self._resolve_url(src, feed_link)
            if resolved:
                urls.append(resolved)

        # Check source children
        for source in tag.find_all("source"):
            src = source.get("src")
            if src:
                resolved = self._resolve_url(src, feed_link)
                if resolved:
                    urls.append(resolved)

        return urls

    def _resolve_url(self, url: str, base: Optional[str]) -> Optional[str]:
        if not url:
            return None
        if url.startswith(("http://", "https://")):
            return url
        if base and base.startswith(("http://", "https://")):
            return urljoin(base, url)
        return None

    def _is_emoticon(self, img_tag) -> bool:
        """Check if an image is likely an emoticon/emoji."""
        src = img_tag.get("src", "")
        alt = img_tag.get("alt", "")
        class_ = img_tag.get("class", "")
        style = img_tag.get("style", "")
        width = img_tag.get("width", "")
        height = img_tag.get("height", "")

        # Check dimensions
        try:
            if width and str(width).isdigit() and int(width) <= 30:
                return True
            if height and str(height).isdigit() and int(height) <= 30:
                return True
        except (ValueError, TypeError):
            pass

        # Check style for small dimensions
        if style and re.search(r"(width|height):\s*([012]?\d|30)(\.\d)?px", style):
            return True

        # Check class names
        class_str = " ".join(class_) if isinstance(class_, list) else str(class_)
        if "emoji" in class_str or "emoticon" in class_str:
            return True

        # Check alt text pattern
        if alt.startswith(":") and alt.endswith(":"):
            return True

        # Check for data URLs
        if src.startswith("data:"):
            return True

        return False

    def _validate_image_dimensions(self, data: bytes, url: str, min_megapixels: float, max_ratio: float) -> bool:
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size

                # Check minimum resolution
                megapixels = (width * height) / 1_000_000
                if megapixels < min_megapixels:
                    logger.debug(f"Image too small: {width}x{height} = {megapixels:.4f} MP < {min_megapixels} MP: {url[:80]}")
                    return False

                # Check aspect ratio
                if width > 0 and height > 0:
                    ratio = max(width / height, height / width)
                    if ratio > max_ratio:
                        logger.debug(f"Image aspect ratio too extreme: {width}x{height} = {ratio:.1f}:1 > {max_ratio}:1: {url[:80]}")
                        return False

                return True
        except Exception as e:
            logger.warning(f"Could not validate image dimensions for {url[:80]}: {e}")
            return True

    def _downscale_image(self, data: bytes, url: str, max_megapixels: float) -> bytes:
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                current_megapixels = (width * height) / 1_000_000

                if current_megapixels <= max_megapixels:
                    return data

                scale_factor = (max_megapixels / current_megapixels) ** 0.5
                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)

                if img.mode in ("RGBA", "P", "LA"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                resized = img.resize((new_width, new_height), Image.LANCZOS)

                output = io.BytesIO()
                resized.save(output, format="JPEG", quality=85, optimize=True)
                result = output.getvalue()

                logger.info(
                    f"Downscaled image from {width}x{height} ({current_megapixels:.2f}MP) "
                    f"to {new_width}x{new_height} ({(new_width * new_height) / 1_000_000:.2f}MP), "
                    f"size: {len(data)} -> {len(result)} bytes: {url[:80]}"
                )

                return result

        except Exception as e:
            logger.warning(f"Could not downscale image {url[:80]}: {e}")
            return data

    async def _download_media(
        self,
        entry: Entry,
        max_size: int,
        timeout: int,
        min_image_megapixels: float,
        max_aspect_ratio: float,
        downscale_images: bool,
        max_image_megapixels: float,
    ) -> None:
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_DOWNLOADS)

        async def download_with_limit(url: str, media_type: str):
            async with semaphore:
                return await self._download_single_media(url, media_type, max_size, timeout)

        download_tasks = []

        for url in entry.images:
            download_tasks.append(("image", url, download_with_limit(url, "image")))
        for url in entry.videos:
            download_tasks.append(("video", url, download_with_limit(url, "video")))
        for url in entry.audios:
            download_tasks.append(("audio", url, download_with_limit(url, "audio")))

        if not download_tasks:
            return

        logger.debug(f"Downloading {len(download_tasks)} media files for '{entry.title[:50]}'")

        results = await asyncio.gather(*[task for _, _, task in download_tasks], return_exceptions=True)

        # Track successfully downloaded URLs
        successful_images = []
        successful_videos = []
        successful_audios = []
        filtered_count = 0

        for (media_type, url, _), result in zip(download_tasks, results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to download {media_type} from {url[:80]}: {result}")
                continue

            if result is None:
                # Download failed (HTTP error, timeout, or size limit) - skip this URL
                continue

            data, filename = result

            if media_type == "image":
                if not self._validate_image_dimensions(data, url, min_image_megapixels, max_aspect_ratio):
                    filtered_count += 1
                    continue
                if downscale_images:
                    data = self._downscale_image(data, url, max_image_megapixels)
                    if filename and not filename.lower().endswith((".jpg", ".jpeg")):
                        filename = filename.rsplit(".", 1)[0] + ".jpg" if "." in filename else filename + ".jpg"
                entry.image_buffers.append((data, url, filename))
                successful_images.append(url)
            elif media_type == "video":
                entry.video_buffers.append((data, url, filename))
                successful_videos.append(url)
            elif media_type == "audio":
                entry.audio_buffers.append((data, url, filename))
                successful_audios.append(url)

        # Replace URL lists with only successfully downloaded URLs
        # This prevents failed downloads from being passed to Telegram
        entry.images = successful_images
        entry.videos = successful_videos
        entry.audios = successful_audios

        log_msg = (
            f"Downloaded media for '{entry.title[:50]}': "
            f"{len(entry.image_buffers)} images, {len(entry.video_buffers)} videos, "
            f"{len(entry.audio_buffers)} audios"
        )
        if filtered_count > 0:
            log_msg += f" ({filtered_count} images filtered due to dimensions)"
        logger.info(log_msg)

    async def _download_single_media(self, url: str, media_type: str, max_size: int, timeout: int) -> Optional[Tuple[bytes, Optional[str]]]:
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)

            async with aiohttp.ClientSession(timeout=timeout_obj) as session, session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"Failed to download {url[:80]}: HTTP {response.status}")
                    return None

                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_size:
                    logger.warning(f"Media too large ({int(content_length)} bytes > {max_size}): {url[:80]}")
                    return None

                chunks = []
                total_size = 0

                async for chunk in response.content.iter_chunked(8192):
                    total_size += len(chunk)
                    if total_size > max_size:
                        logger.warning(f"Media exceeded size limit during download ({total_size} > {max_size}): {url[:80]}")
                        return None
                    chunks.append(chunk)

                data = b"".join(chunks)

                filename = None

                content_disposition = response.headers.get("Content-Disposition")
                if content_disposition and "filename=" in content_disposition:
                    with contextlib.suppress(Exception):
                        filename = content_disposition.split("filename=")[1].strip("\"'")

                if not filename:
                    parsed = urlparse(url)
                    path = parsed.path
                    if path and "/" in path:
                        filename = path.split("/")[-1]

                logger.debug(f"Downloaded {media_type} ({len(data)} bytes): {url[:80]}")
                return (data, filename)

        except asyncio.TimeoutError:
            logger.warning(f"Timeout downloading {url[:80]}")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"HTTP error downloading {url[:80]}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading {url[:80]}: {e}", exc_info=True)
            return None
