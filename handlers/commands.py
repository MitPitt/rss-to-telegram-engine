import asyncio
import logging
import re

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import ConfigLoader
from core.fetcher import FeedFetcher
from core.models import Config, Entry, FeedConfig

logger = logging.getLogger(__name__)

router = Router()


def setup_admin_filter(admin_ids: list[int]):
    admin_filter = (F.from_user.id.in_(admin_ids)) | (F.sender_chat.id.in_(admin_ids))
    router.message.filter(admin_filter)
    router.channel_post.filter(admin_filter)
    logger.info(f"Admin filter configured for {len(admin_ids)} admin(s)")


@router.channel_post(Command("list"))
@router.message(Command("list"))
async def cmd_list(message: Message, dispatcher: Dispatcher, state_manager):
    config: Config = dispatcher["config"]

    if not config.channels:
        await message.answer("No channels configured yet.\n\nEdit config/config.json to add feeds.")
        return

    await message.answer("Configured Feeds:")
    await asyncio.sleep(0.3)

    total_feeds = 0

    for channel in config.channels:
        channel_msg = f"<blockquote expandable><b>Channel:</b> {channel.name} (ID: {channel.id})\n"

        if not channel.feeds:
            channel_msg += "No feeds"
        else:
            for i, (feed_url, feed) in enumerate(channel.feeds.items(), 1):
                feed_config = config.get_feed_config(feed_url)
                name = feed_config.name
                if not name:
                    state = state_manager.get_state(feed_url)
                    name = state.feed_title or "Unnamed"
                interval = feed_config.check_interval or config.global_config.check_interval

                feed_line = f"  {i}. <code>{name}</code>\n     {feed_url}\n"
                if len(channel_msg) + len(feed_line) > 3800:
                    channel_msg += "</blockquote>"
                    await message.answer(channel_msg, disable_web_page_preview=True)
                    await asyncio.sleep(0.3)
                    channel_msg = f"<blockquote expandable><b>Channel:</b> {channel.name} (continued)\n"

                channel_msg += feed_line
                total_feeds += 1

        channel_msg += "</blockquote>"
        await message.answer(channel_msg, disable_web_page_preview=True)
        await asyncio.sleep(0.3)

    summary = f"Total: {total_feeds} feeds in {len(config.channels)} channels"
    await message.answer(summary, disable_web_page_preview=True)


@router.channel_post(Command("start"))
@router.channel_post(Command("help"))
@router.channel_post(Command("status"))
@router.message(Command("start"))
@router.message(Command("help"))
@router.message(Command("status"))
async def cmd_status(message: Message, dispatcher: Dispatcher, state_manager, monitor):
    config: Config = dispatcher["config"]

    total_channels = len(config.channels)
    total_feeds = sum(len(c.feeds) for c in config.channels)
    feeds_with_state = len(state_manager.states)

    # Count active monitor tasks
    active_tasks = len(monitor._tasks) if hasattr(monitor, "_tasks") else 0
    monitor_id = monitor._id if hasattr(monitor, "_id") else "unknown"

    status = (
        f"<b>Monitor ID:</b> <code>{monitor_id}</code>\n"
        f"<b>Active tasks:</b> {active_tasks}\n"
        f"<b>Channels:</b> {total_channels}\n"
        f"<b>Feeds:</b> {total_feeds}\n"
        f"<b>Feeds with state:</b> {feeds_with_state}\n"
        f"<b>Check interval:</b> {config.global_config.check_interval}s\n"
    )
    await message.answer(
        "Available commands:\n"
        "• /list - List all feeds\n"
        "• /test &lt;url&gt; - Test feed URL\n"
        "• /status - Show bot status\n"
        "• /reload - Reload configuration\n"
        "• /help - Show this help\n\n"
        "Note: Most configuration is done via json config file."
    )

    await message.answer(status)


@router.channel_post(Command("reload"))
@router.message(Command("reload"))
async def cmd_reload(message: Message, state_manager, pipeline, monitor, dispatcher: Dispatcher, settings):
    status_msg = await message.answer("⏳ Reloading configuration...")

    try:
        bot = message.bot
        old_monitor = monitor

        logger.info("Stopping current monitor...")
        await status_msg.edit_text("⏳ Stopping current monitor...")
        await old_monitor.stop(timeout=15.0)  # Give enough time for graceful shutdown

        logger.info(f"Loading config from {settings.config_path}")
        await status_msg.edit_text("⏳ Loading new configuration...")
        config_loader = ConfigLoader(settings.config_path)
        new_config = config_loader.load()

        from core.monitor import FeedMonitor

        new_monitor = FeedMonitor(new_config, state_manager, bot, pipeline)

        dispatcher.workflow_data["config"] = new_config
        dispatcher.workflow_data["monitor"] = new_monitor
        dispatcher["config"] = new_config
        dispatcher["monitor"] = new_monitor

        logger.info(f"Starting new monitor with {sum(len(c.feeds) for c in new_config.channels)} feeds...")
        await status_msg.edit_text("⏳ Starting new monitor...")
        await new_monitor.start()

        # Success message
        total_channels = len(new_config.channels)
        total_feeds = sum(len(c.feeds) for c in new_config.channels)

        await status_msg.edit_text(
            f"✅ <b>Configuration reloaded!</b>\n\n"
            f"<b>Channels:</b> {total_channels}\n"
            f"<b>Feeds:</b> {total_feeds}\n"
            f"<b>Monitor ID:</b> <code>{new_monitor._id}</code>\n\n"
            "Use /list to see feeds."
        )

        logger.info(f"Reload complete: {total_channels} channels, {total_feeds} feeds, monitor {new_monitor._id}")

    except FileNotFoundError as e:
        await status_msg.edit_text(f"❌ Config file not found:\n<code>{str(e)}</code>")
        logger.error(f"Config file not found: {e}")
    except ValueError as e:
        await status_msg.edit_text(f"❌ Invalid config format:\n<code>{str(e)}</code>")
        logger.error(f"Invalid config: {e}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error reloading config:\n<code>{str(e)}</code>")
        logger.error(f"Error reloading config: {e}", exc_info=True)


@router.channel_post(Command("test"))
@router.message(Command("test"))
async def cmd_test(message: Message, dispatcher: Dispatcher, pipeline, monitor):
    config: Config = dispatcher["config"]
    args = message.text.split(maxsplit=2)[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.answer(
            "Test Feed Command\n\n"
            "Usage: /test [N or N-M] &lt;url&gt;\n\n"
            "Examples:\n"
            "<code>/test https://hnrss.org/newest</code> (latest entry)\n"
            "<code>/test 2 https://hnrss.org/newest</code> (3rd entry)\n"
            "<code>/test 0-2 https://hnrss.org/newest</code> (entries 0, 1, 2)\n\n"
            "This will fetch the feed and send the specified entry/entries as formatted posts to test template and processing"
        )
        return

    entry_indices = [0]  # Default to latest (0-indexed)
    url = None

    if len(args) == 1:
        # Only URL provided
        url = args[0]
    elif len(args) == 2:
        # Range/index and URL provided
        range_arg = args[0]
        url = args[1]

        # Parse range (e.g., "0-2" or "3")
        if "-" in range_arg:
            try:
                start, end = range_arg.split("-", 1)
                start_idx = int(start)
                end_idx = int(end)
                if start_idx < 0:
                    start_idx = 0
                if end_idx < start_idx:
                    await message.answer("❌ Invalid range: end must be >= start")
                    return
                entry_indices = list(range(start_idx, end_idx + 1))
            except ValueError:
                await message.answer("❌ Invalid range format. Use: N-M (e.g., 0-2)")
                return
        else:
            try:
                idx = int(range_arg)
                if idx < 0:
                    idx = 0
                entry_indices = [idx]
            except ValueError:
                await message.answer("❌ Invalid entry number. Use: /test [N or N-M] &lt;url&gt;")
                return

    if not url:
        await message.answer("❌ Missing URL. Use: /test [N or N-M] &lt;url&gt;")
        return

    if not re.match(r"https?://", url):
        await message.answer("❌ Invalid URL. Must start with http:// or https://")
        return

    status_msg = await message.answer(f"⏳ Fetching feed: {url}")

    try:
        fetcher = FeedFetcher()
        entries, _, _, feed_title = await fetcher.fetch(url)

        if not entries:
            await status_msg.edit_text("❌ No entries found in feed or feed hasn't changed.")
            return

        max_idx = max(entry_indices)
        if max_idx >= len(entries):
            await status_msg.edit_text(f"❌ Entry #{max_idx} not found. Feed has only {len(entries)} entries.\nValid range: 0-{len(entries) - 1}")
            return

        feed_config = config.get_feed_config(url)

        if not feed_config:
            from core.models import FeedConfig

            feed_config = FeedConfig(
                url=url,
                name=None,
                check_interval=config.global_config.check_interval,
                enable_preview=config.global_config.enable_preview,
                processing=config.global_config.processing,
            )

        if len(entry_indices) > 1:
            await status_msg.edit_text(f"⏳ Processing {len(entry_indices)} entries...\nEntries: {', '.join(str(i) for i in entry_indices)}")

        sent_count = 0
        filtered_count = 0

        for idx in entry_indices:
            entry = entries[idx]
            processed_entry = await monitor.process_entry(entry=entry, feed_url=url, feed_config=feed_config)
            if processed_entry.filtered:
                logger.info(f"Entry #{idx} filtered out: {entry.title[:50]}")
                filtered_count += 1
                continue

            if not processed_entry.formatted_message:
                logger.warning(f"Entry #{idx} has no formatted_message, skipping")
                continue

            try:
                await monitor.send_entry_with_media(
                    chat_id=message.chat.id,
                    message=processed_entry.formatted_message,
                    entry=processed_entry,
                    enable_preview=feed_config.enable_preview,
                )
                sent_count += 1

                if len(entry_indices) > 1 and idx != entry_indices[-1]:
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error sending entry #{idx}: {e}", exc_info=True)

        await status_msg.delete()

        proc_names = []
        if feed_config.processing:
            for p in feed_config.processing:
                if isinstance(p, str):
                    proc_names.append(p)
                elif isinstance(p, dict):
                    proc_names.append(p.get("name", "unknown"))

        info = (
            f"Feed: {url}\n"
            f"Feed title: {feed_config.name or feed_title or 'Unknown'}\n"
            f"Total entries: {len(entries)}\n"
            f"Tested: {len(entry_indices)} ({', '.join(str(i) for i in entry_indices)})\n"
            f"Sent: {sent_count}\n"
            f"Filtered: {filtered_count}\n\n"
            f"Processing: {', '.join(proc_names) if proc_names else 'none'}\n"
            f"Preview: {'enabled' if feed_config.enable_preview else 'disabled'}"
        )
        await message.answer(info, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Error testing feed: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Error fetching feed:\n<code>{str(e)}</code>")
