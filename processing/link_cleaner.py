import logging
from typing import Any, Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)


class LinkCleanerProcessor(Processor):
    name = "link_cleaner"

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        if not entry.link:
            return entry

        strip_query = config.get("strip_query", True)
        strip_fragment = config.get("strip_fragment", False)
        keep_params = config.get("keep_params", [])

        parts = urlsplit(entry.link)
        query = parts.query
        fragment = parts.fragment

        if strip_query and query:
            if keep_params:
                kept = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key in keep_params]
                query = urlencode(kept)
            else:
                query = ""

        if strip_fragment:
            fragment = ""

        new_link = urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))
        if new_link != entry.link:
            logger.debug(f"Cleaned link: {entry.link} -> {new_link}")
            entry.link = new_link

        return entry
