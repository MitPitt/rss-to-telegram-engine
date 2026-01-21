from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Entry:
    title: str
    link: str
    content: str
    guid: str
    published: datetime
    author: Optional[str] = None
    feed_title: Optional[str] = None
    enclosures: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    videos: List[str] = field(default_factory=list)
    audios: List[str] = field(default_factory=list)
    image_buffers: List[Tuple[bytes, str, Optional[str]]] = field(default_factory=list)
    video_buffers: List[Tuple[bytes, str, Optional[str]]] = field(default_factory=list)
    audio_buffers: List[Tuple[bytes, str, str, Optional[bytes]]] = field(default_factory=list)
    filtered: bool = False
    formatted_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "content": self.content,
            "guid": self.guid,
            "published": self.published.isoformat(),
            "author": self.author,
            "enclosures": self.enclosures,
            "images": self.images,
            "videos": self.videos,
            "audios": self.audios,
        }

    def has_media(self) -> bool:
        return bool(self.images or self.videos or self.audios)


@dataclass
class FeedConfig:
    url: str
    name: Optional[str] = None
    note: Optional[str] = None
    check_interval: Optional[int] = None
    enable_preview: Optional[bool] = None
    processing: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def merge_with_defaults(self, defaults: "FeedConfig") -> "FeedConfig":
        return FeedConfig(
            url=self.url,
            name=self.name or defaults.name,
            note=self.note or defaults.note,
            check_interval=self.check_interval if self.check_interval is not None else defaults.check_interval,
            enable_preview=self.enable_preview if self.enable_preview is not None else defaults.enable_preview,
            processing=self.processing if self.processing else defaults.processing,
        )


@dataclass
class ChannelConfig:
    id: int
    name: str
    feeds: Dict[str, FeedConfig]
    enable_preview: Optional[bool] = None
    check_interval: Optional[int] = None
    processing: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class GlobalConfig:
    check_interval: int = 300
    enable_preview: bool = True
    processing: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    send_delay: int = 1
    domain_delay: float = 2.0


@dataclass
class Config:
    global_config: GlobalConfig
    channels: List[ChannelConfig]

    def get_feed_config(self, feed_url: str) -> Optional[FeedConfig]:
        for channel in self.channels:
            if feed_url in channel.feeds:
                feed = channel.feeds[feed_url]
                channel_defaults = FeedConfig(
                    url="",
                    check_interval=channel.check_interval if channel.check_interval is not None else self.global_config.check_interval,
                    enable_preview=channel.enable_preview if channel.enable_preview is not None else self.global_config.enable_preview,
                    processing=channel.processing if channel.processing else self.global_config.processing,
                )
                return feed.merge_with_defaults(channel_defaults)
        return None

    def get_channel_for_feed(self, feed_url: str) -> Optional[ChannelConfig]:
        for channel in self.channels:
            if feed_url in channel.feeds:
                return channel
        return None

    def all_feeds(self) -> List[tuple[int, FeedConfig]]:
        result = []
        for channel in self.channels:
            for feed_url, feed in channel.feeds.items():
                result.append((channel.id, feed))
        return result
