import logging
from typing import Any, Dict

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)


class HtmlToTelegramProcessor(Processor):
    name = "html_to_telegram"

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        if not entry.content:
            logger.debug(f"No content to process for entry: {entry.title[:50]}")
            return entry

        try:
            from utils.telegram_html import html_to_telegram

            feed_link = config.get("feed_link")

            logger.debug(f"Converting HTML to Telegram format: {entry.title[:50]}")

            # Convert to Telegram-safe HTML (media already extracted by media_extract)
            telegram_html = await html_to_telegram(
                entry.content,
                feed_link=feed_link,
                extract_media=False,  # Media already extracted
            )

            # Update content with cleaned HTML
            entry.content = telegram_html

            logger.debug(f"HTML converted successfully ({len(telegram_html)} chars)")

        except Exception as e:
            logger.error(f"Error converting HTML to Telegram format: {e}", exc_info=True)
            # Keep original content if conversion fails

        return entry
