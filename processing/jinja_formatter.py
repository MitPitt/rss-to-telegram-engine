import html
import logging
import re
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)

# Telegram limits
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024

# Regex to detect blockquote tags in content
BLOCKQUOTE_PATTERN = re.compile(r"<blockquote[^>]*>.*?</blockquote>", re.DOTALL | re.IGNORECASE)


class JinjaFormatterProcessor(Processor):
    name = "jinja_formatter"

    def __init__(self, template_dir: str = "config/jinja_templates"):
        self.template_dir = Path(template_dir)
        self.template_dir.mkdir(parents=True, exist_ok=True)

        # Disable autoescape since content is already Telegram-safe HTML
        self.env = Environment(loader=FileSystemLoader(self.template_dir), autoescape=False, trim_blocks=True, lstrip_blocks=True)
        self.env.filters["escape_html"] = self._escape_html
        self.env.filters["has_blockquote"] = self._has_blockquote
        self.env.filters["unescape_html"] = self._unescape_html

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        template_name = config.get("template", "default")

        try:
            template = self.env.get_template(f"{template_name}.j2")
        except TemplateNotFound:
            logger.warning(f"Template not found: {template_name}.j2, using default")
            try:
                template = self.env.get_template("default.j2")
            except TemplateNotFound:
                logger.error("Default template not found, using minimal fallback")
                entry.formatted_message = self._minimal_fallback(entry)
                return entry

        context = self._build_context(entry, config)

        try:
            rendered = template.render(**context)
            entry.formatted_message = rendered.strip()

            has_media = bool(entry.images or entry.videos)
            max_length = MAX_CAPTION_LENGTH if has_media else MAX_MESSAGE_LENGTH

            if len(entry.formatted_message) > max_length:
                logger.warning(f"Formatted message too long ({len(entry.formatted_message)} chars, limit {max_length}), trying progressive fallback")
                entry.formatted_message = self._try_progressive_fallback(entry, config, max_length)

            logger.debug(f"Formatted message ({len(entry.formatted_message)} chars): {entry.title[:50]}")

        except Exception as e:
            logger.error(f"Error rendering template {template_name}: {e}", exc_info=True)
            entry.formatted_message = self._minimal_fallback(entry)

        return entry

    def _build_context(self, entry: Entry, config: Dict[str, Any]) -> Dict[str, Any]:
        content_has_blockquote = self._has_blockquote(entry.content) if entry.content else False

        content_length = len(entry.content) if entry.content else 0
        blockquote_threshold = config.get("blockquote_length_threshold", 750)
        should_use_blockquote = (
            content_length > 0
            and not content_has_blockquote
            and config.get("content_use_blockquote", False)
            and (not config.get("blockquote_only_if_exceeds", False) or content_length > blockquote_threshold)
        )

        context = {
            "title": entry.title,
            "content": entry.content,  # Already Telegram-safe HTML
            "link": entry.link,
            "author": entry.author,
            "published": entry.published,
            "feed_title": entry.feed_title,
            "feed_name": config.get("feed_name") or entry.feed_title or "Feed",
            "channel_name": config.get("channel_name"),
            "guid": entry.guid,
            "has_media": bool(entry.images or entry.videos),
            "images_count": len(entry.images),
            "videos_count": len(entry.videos),
            "audios_count": len(entry.audios),
            # Content blockquote settings
            "content_use_blockquote": should_use_blockquote,
            "content_has_blockquote": content_has_blockquote,
            "content_length": content_length,
        }

        # Add any extra config keys as template variables
        for key, value in config.items():
            if key not in context and key not in ("template", "feed_link"):
                context[key] = value

        return context

    def _try_progressive_fallback(self, entry: Entry, config: Dict[str, Any], max_length: int) -> str:
        template_name = config.get("template", "default")
        try:
            template = self.env.get_template(f"{template_name}.j2")
        except TemplateNotFound:
            template = self.env.get_template("default.j2")

        original_show_content = config.get("show_content", True)
        original_show_title = config.get("show_title", True)
        try_replace_content_with_title = config.get("try_replace_content_with_title", False)

        # Special case: If show_content=true and show_title=false, try swapping first
        if try_replace_content_with_title and original_show_content and not original_show_title:
            try:
                context = self._build_context(entry, config)
                context["show_content"] = False
                context["show_title"] = True
                context["content_use_blockquote"] = False  # Disable blockquote for fallback

                rendered = template.render(**context).strip()
                if len(rendered) <= max_length:
                    logger.info(f"Progressive fallback: swapped content with title ({len(rendered)} chars)")
                    return rendered
            except Exception as e:
                logger.warning(f"Error in progressive fallback (swap content/title): {e}")

        # Try 1: Disable blockquote if enabled
        if config.get("content_use_blockquote", False):
            try:
                context = self._build_context(entry, config)
                context["content_use_blockquote"] = False

                rendered = template.render(**context).strip()
                if len(rendered) <= max_length:
                    logger.info(f"Progressive fallback: disabled blockquote ({len(rendered)} chars)")
                    return rendered
            except Exception as e:
                logger.warning(f"Error in progressive fallback (disable blockquote): {e}")

        # Try 2: Set show_content=false
        try:
            context = self._build_context(entry, config)
            context["show_content"] = False
            context["content_use_blockquote"] = False

            rendered = template.render(**context).strip()
            if len(rendered) <= max_length:
                logger.info(f"Progressive fallback: disabled content ({len(rendered)} chars)")
                return rendered
        except Exception as e:
            logger.warning(f"Error in progressive fallback (show_content=false): {e}")

        # Try 3: Set show_content=false and show_title=false
        try:
            context = self._build_context(entry, config)
            context["show_content"] = False
            context["show_title"] = False
            context["content_use_blockquote"] = False

            rendered = template.render(**context).strip()
            if len(rendered) <= max_length:
                logger.info(f"Progressive fallback: disabled content and title ({len(rendered)} chars)")
                return rendered
        except Exception as e:
            logger.warning(f"Error in progressive fallback (show_content=false, show_title=false): {e}")

        # Try 4: Minimal fallback
        logger.warning("All progressive fallbacks failed, using minimal fallback")
        return entry.link

    def _has_blockquote(self, text: str) -> bool:
        if not text:
            return False
        return bool(BLOCKQUOTE_PATTERN.search(text))

    def _unescape_html(self, text: str) -> str:
        if not text:
            return ""
        return html.unescape(text)

    def _escape_html(self, text: str) -> str:
        if not text:
            return ""

        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        text = text.replace("'", "&#39;")

        return text

    def _minimal_fallback(self, entry: Entry) -> str:
        return entry.link
