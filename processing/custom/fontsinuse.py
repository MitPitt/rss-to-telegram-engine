import logging
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)


class FontsInUseProcessor(Processor):
    name = "fontsinuse"

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        if not entry.content:
            return entry

        soup = BeautifulSoup(entry.content, "html.parser")
        font_entries = self._extract_fonts(soup)
        seen_urls: Dict[str, Tuple[str, str]] = {}
        for name, href in font_entries:
            key = href.rstrip("/").lower()
            if key in seen_urls:
                prev_name, _ = seen_urls[key]
                if len(name) > len(prev_name):
                    seen_urls[key] = (name, href)
            else:
                seen_urls[key] = (name, href)

        if seen_urls:
            links = []
            for name, href in seen_urls.values():
                links.append(f'<a href="{href}">{name}</a>')
            entry.content = "Используемые шрифты: " + ", ".join(links)
            logger.debug(f"Extracted fonts for '{entry.title[:50]}': {list(seen_urls.values())}")
        else:
            logger.debug(f"No fonts found in '{entry.title[:50]}'")

        return entry

    def _extract_fonts(self, soup: BeautifulSoup) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "fontsinuse.com/typefaces" not in href:
                continue
            text = a_tag.get_text(strip=True)
            if text:
                results.append((text, href))
            else:
                name = self._name_from_url(href)
                if name:
                    results.append((name, href))
        return results

    @staticmethod
    def _name_from_url(url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        slug = path.rsplit("/", 1)[-1]
        slug = slug.replace("-", " ").title()
        return slug
