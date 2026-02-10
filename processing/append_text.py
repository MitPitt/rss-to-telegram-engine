import logging
from typing import Any, Dict

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)

DELIMITERS = set(" \t\n\r")


class AppendTextProcessor(Processor):
    name = "append_text"

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        base_text = config.get("text", "")
        base_position = config.get("position", "suffix")

        extra_flags = config.get("extra_flags", {})
        extra_append = extra_flags.get("append_text")

        if extra_append is not None:
            if isinstance(extra_append, str):
                text = extra_append
                position = base_position
            elif isinstance(extra_append, dict):
                text = extra_append.get("text", base_text)
                position = extra_append.get("position", base_position)
            else:
                text = base_text
                position = base_position
        else:
            text = base_text
            position = base_position

        if not text:
            return entry

        if not entry.formatted_message:
            logger.debug("AppendTextProcessor: no formatted_message yet, skipping")
            return entry

        msg = entry.formatted_message

        if position == "prefix":
            # Check if text ends with delimiter
            needs_space = text[-1] not in DELIMITERS
            if needs_space:
                entry.formatted_message = text + " " + msg
            else:
                entry.formatted_message = text + msg
        else:
            # Check if  text starts with delimiter
            needs_space = text[0] not in DELIMITERS
            if needs_space:
                entry.formatted_message = msg + " " + text
            else:
                entry.formatted_message = msg + text

        logger.debug(f"Appended text ({position}): '{text}'")
        return entry
