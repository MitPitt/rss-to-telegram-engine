import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_PROCESSED_ENTRIES_PER_FEED = 100


@dataclass
class FeedState:
    last_check: Optional[datetime] = None
    processed_entries: List[str] = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    error_count: int = 0
    feed_title: Optional[str] = None
    feed_link: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        entries_list = self.processed_entries
        if len(entries_list) > MAX_PROCESSED_ENTRIES_PER_FEED:
            entries_list = entries_list[-MAX_PROCESSED_ENTRIES_PER_FEED:]

        return {
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "processed_entries": entries_list,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "error_count": self.error_count,
            "feed_title": self.feed_title,
            "feed_link": self.feed_link,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeedState":
        return cls(
            last_check=datetime.fromisoformat(data["last_check"]) if data.get("last_check") else None,
            processed_entries=list(data.get("processed_entries", [])),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            error_count=data.get("error_count", 0),
            feed_title=data.get("feed_title"),
            feed_link=data.get("feed_link"),
        )


class StateManager:
    def __init__(self, state_path: str = "config/state.json"):
        self.state_path = Path(state_path)
        self.states: Dict[str, FeedState] = {}
        self._lock = asyncio.Lock()

    async def load(self):
        async with self._lock:
            if self.state_path.exists():
                try:
                    data = json.loads(self.state_path.read_text())
                    self.states = {url: FeedState.from_dict(state_data) for url, state_data in data.get("feeds", {}).items()}
                    logger.info(f"Loaded state for {len(self.states)} feeds")
                except Exception as e:
                    logger.error(f"Error loading state: {e}")
                    self.states = {}
            else:
                logger.info("No existing state file, creating new")
                self.states = {}

    async def save(self):
        async with self._lock:
            try:
                data = {"feeds": {url: state.to_dict() for url, state in self.states.items()}}
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self.state_path.with_suffix(".tmp")
                temp_path.write_text(json.dumps(data, indent=2))
                temp_path.rename(self.state_path)

            except Exception as e:
                logger.error(f"Error saving state: {e}")

    def get_state(self, feed_url: str) -> FeedState:
        if feed_url not in self.states:
            self.states[feed_url] = FeedState()
        return self.states[feed_url]

    async def is_processed(self, feed_url: str, entry_guid: str) -> bool:
        state = self.get_state(feed_url)
        return entry_guid in state.processed_entries

    async def mark_processed(self, feed_url: str, entry_guid: str):
        state = self.get_state(feed_url)
        if entry_guid not in state.processed_entries:
            state.processed_entries.append(entry_guid)
            if len(state.processed_entries) > MAX_PROCESSED_ENTRIES_PER_FEED:
                state.processed_entries = state.processed_entries[-MAX_PROCESSED_ENTRIES_PER_FEED:]
            await self.save()

    async def update_metadata(
        self, feed_url: str, etag: Optional[str], last_modified: Optional[str], feed_title: Optional[str] = None, feed_link: Optional[str] = None
    ):
        state = self.get_state(feed_url)
        state.etag = etag
        state.last_modified = last_modified
        state.last_check = datetime.now()
        state.error_count = 0  # Reset error count on successful fetch
        if feed_title:
            state.feed_title = feed_title  # Cache feed title
        if feed_link:
            state.feed_link = feed_link  # Cache feed canonical link
        await self.save()

    async def increment_error(self, feed_url: str):
        state = self.get_state(feed_url)
        state.error_count += 1
        state.last_check = datetime.now()
        await self.save()
        logger.warning(f"Feed {feed_url} error count: {state.error_count}")
