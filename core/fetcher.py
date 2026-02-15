import asyncio
import contextlib
import logging
from datetime import datetime
from typing import List, Optional, Tuple

import aiohttp
import feedparser

from core.models import Entry

logger = logging.getLogger(__name__)


class FeedFetcher:
    def __init__(self, user_agent: str = "RSS-to-Telegram-Bot/2.0"):
        self.user_agent = user_agent
        self.timeout = aiohttp.ClientTimeout(total=60)

    async def fetch(
        self, url: str, etag: Optional[str] = None, last_modified: Optional[str] = None
    ) -> Tuple[List[Entry], Optional[str], Optional[str], Optional[str], Optional[str]]:
        headers = {"User-Agent": self.user_agent}

        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session, session.get(url, headers=headers) as response:
                if response.status == 304:
                    logger.debug(f"Feed not modified: {url}")
                    return [], etag, last_modified, None, None

                response.raise_for_status()

                content = await response.text()
                new_etag = response.headers.get("ETag")
                new_last_modified = response.headers.get("Last-Modified")

        except aiohttp.ClientResponseError as e:
            logger.error(f"HTTP {e.status} Error: {e.message} | URL: {url}")
            raise
        except aiohttp.InvalidURL as e:
            logger.error(f"Invalid URL: {e} | URL: {url}")
            raise
        except asyncio.TimeoutError:
            logger.error(f"Timeout Error: Server took too long to respond | URL: {url}")
            raise
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Connection Error: {type(e).__name__} ({e}) | URL: {url}")
            raise
        except aiohttp.ClientError as e:
            logger.error(f"Aiohttp Client Error: {type(e).__name__}: {e} | URL: {url}")
            raise
        except Exception as e:
            logger.error(f"Unexpected {type(e).__name__}: {str(e) or repr(e)} | URL: {url}")
            raise

        # Parse feed
        try:
            feed = feedparser.parse(content)

            if feed.bozo:
                logger.warning(f"Feed parse warning for {url}: {feed.bozo_exception}")

            feed_title = feed.feed.get("title", "RSS Feed") if hasattr(feed, "feed") else "RSS Feed"
            feed_link = feed.feed.get("link") if hasattr(feed, "feed") else None

            entries = self._parse_entries(feed, feed_title)
            logger.info(f"Fetched {len(entries)} entries from {url} (feed: {feed_title}, link: {feed_link})")

            return entries, new_etag, new_last_modified, feed_title, feed_link

        except Exception as e:
            logger.error(f"Error parsing feed {url}: {e}")
            raise

    def _parse_entries(self, feed, feed_title: str) -> List[Entry]:
        entries = []

        for entry in feed.entries:
            try:
                published = datetime.now()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    with contextlib.suppress(TypeError, ValueError):
                        published = datetime(*entry.published_parsed[:6])
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    with contextlib.suppress(TypeError, ValueError):
                        published = datetime(*entry.updated_parsed[:6])

                content = ""
                if hasattr(entry, "content") and entry.content:
                    content = entry.content[0].value
                elif hasattr(entry, "summary"):
                    content = entry.summary
                elif hasattr(entry, "description"):
                    content = entry.description

                enclosures = []
                if hasattr(entry, "enclosures"):
                    enclosures = [enc.href for enc in entry.enclosures if hasattr(enc, "href")]

                entries.append(
                    Entry(
                        title=entry.get("title", "No title"),
                        link=entry.get("link", ""),
                        content=content,
                        guid=entry.get("id", entry.get("link", "")),
                        published=published,
                        author=entry.get("author"),
                        feed_title=feed_title,
                        enclosures=enclosures,
                        images=[],  # Populated by media_extract processor
                        videos=[],  # Populated by media_extract processor
                        audios=[],  # Populated by media_extract processor
                        formatted_message=None,  # Populated by jinja_formatter processor
                    )
                )

            except Exception as e:
                logger.warning(f"Error parsing entry in {feed_title}: {e}")
                continue

        return entries
