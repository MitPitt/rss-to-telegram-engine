import logging
import re
from typing import Any, Dict, List

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)


class ContentFilterProcessor(Processor):
    name = "content_filter"

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        patterns = config.get("patterns", [])

        if not patterns:
            logger.warning("ContentFilterProcessor called without patterns")
            return entry

        match_title = config.get("match_title", True)
        match_content = config.get("match_content", True)
        match_mode = config.get("match_mode", "any")  # 'any' or 'all'
        invert = config.get("invert", False)
        flags_str = config.get("flags", "")

        flags = self._parse_flags(flags_str)

        search_texts = []
        if match_title and entry.title:
            search_texts.append(entry.title)
        if match_content and entry.content:
            search_texts.append(entry.content)

        if not search_texts:
            logger.debug("No text to search in entry")
            return entry

        combined_text = "\n".join(search_texts)

        matches = []
        for pattern in patterns:
            if isinstance(pattern, str):
                try:
                    match = bool(re.search(pattern, combined_text, flags=flags))
                    matches.append(match)
                except re.error as e:
                    logger.error(f"Invalid regex pattern '{pattern}': {e}")
                    continue

        if match_mode == "all":
            pattern_matched = all(matches) if matches else False
        else:  # 'any'
            pattern_matched = any(matches) if matches else False

        should_filter = pattern_matched if not invert else not pattern_matched

        if should_filter:
            entry.filtered = True
            logger.info(f"Entry filtered: '{entry.title[:50]}' (matched={pattern_matched}, invert={invert}, mode={match_mode})")
        else:
            logger.debug(f"Entry passed filter: '{entry.title[:50]}'")

        return entry

    def _parse_flags(self, flags_str: str) -> int:
        if not flags_str:
            return 0

        flag_map = {
            "IGNORECASE": re.IGNORECASE,
            "I": re.IGNORECASE,
            "MULTILINE": re.MULTILINE,
            "M": re.MULTILINE,
            "DOTALL": re.DOTALL,
            "S": re.DOTALL,
            "UNICODE": re.UNICODE,
            "U": re.UNICODE,
        }

        flags = 0
        for flag_name in flags_str.upper().replace(" ", "").split(","):
            if flag_name in flag_map:
                flags |= flag_map[flag_name]

        return flags
