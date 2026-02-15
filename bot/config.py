import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Set

from pydantic import Field
from pydantic_settings import BaseSettings

from core.models import ChannelConfig, Config, FeedConfig, GlobalConfig

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    bot_token: str = Field(..., validation_alias="BOT_TOKEN")
    admin_ids: str = Field(..., validation_alias="ADMIN_IDS")
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    config_path: str = Field("config/config.json", validation_alias="CONFIG_PATH")
    state_path: str = Field("config/state.json", validation_alias="STATE_PATH")
    telegram_api_server_url: str = Field("", validation_alias="TELEGRAM_API_SERVER_URL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def admin_id_list(self) -> List[int]:
        return [int(id.strip()) for id in self.admin_ids.split(",") if id.strip()]


class ConfigLoader:
    def __init__(self, config_path: str = "config/config.json"):
        self.config_path = Path(config_path)

    def _load_json_file(self, file_path: Path) -> Dict[str, Any]:
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        try:
            return json.loads(file_path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file {file_path}: {e}")

    def _resolve_includes(self, data: Dict[str, Any], base_path: Path, loaded_files: Set[str]) -> Dict[str, Any]:
        includes = data.get("includes", [])
        if not includes:
            return data

        merged_global: Dict[str, Any] = {}
        all_channels: List[Dict[str, Any]] = []

        for include_path in includes:
            include_file = base_path / include_path
            resolved_path = str(include_file.resolve())

            if resolved_path in loaded_files:
                logger.warning(f"Circular include detected, skipping: {include_path}")
                continue

            if not include_file.exists():
                logger.warning(f"Include file not found, skipping: {include_path}")
                continue

            logger.debug(f"Loading include: {include_path}")
            loaded_files.add(resolved_path)

            try:
                included_data = self._load_json_file(include_file)
                included_data = self._resolve_includes(included_data, include_file.parent, loaded_files)

                if "global" in included_data:
                    merged_global = self._merge_dicts(merged_global, included_data["global"])

                for channel_data in included_data.get("channels", []):
                    if channel_data.get("id") is not None:
                        all_channels.append(channel_data.copy())

            except Exception as e:
                logger.error(f"Error loading include {include_path}: {e}")
                continue

        if "global" in data:
            merged_global = self._merge_dicts(merged_global, data["global"])

        for channel_data in data.get("channels", []):
            if channel_data.get("id") is not None:
                all_channels.append(channel_data.copy())

        result = {}
        if merged_global:
            result["global"] = merged_global
        if all_channels:
            result["channels"] = all_channels

        return result

    def _merge_dicts(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_dicts(result[key], value)
            else:
                result[key] = value
        return result

    def load(self) -> Config:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}\nPlease create a config file. See README.md for examples.")

        data = self._load_json_file(self.config_path)
        loaded_files: Set[str] = {str(self.config_path.resolve())}
        data = self._resolve_includes(data, self.config_path.parent, loaded_files)

        logger.debug(f"Loaded {len(loaded_files)} config file(s)")

        global_data = data.get("global", {})
        global_config = GlobalConfig(
            check_interval=global_data.get("check_interval", 300),
            enable_preview=global_data.get("enable_preview", True),
            processing=global_data.get("processing", {}),
            send_delay=global_data.get("send_delay", 1),
            domain_delay=global_data.get("domain_delay", 2.0),
        )

        channels = []
        for channel_data in data.get("channels", []):
            if "id" not in channel_data:
                logger.warning(f"Skipping channel without ID: {channel_data.get('name', 'Unknown')}")
                continue

            if "name" not in channel_data:
                logger.warning(f"Skipping channel without name: ID {channel_data['id']}")
                continue

            feeds = {}
            feeds_data = channel_data.get("feeds", {})

            for url, feed_data in feeds_data.items():
                feeds[url] = FeedConfig(
                    url=url,
                    name=feed_data.get("name"),
                    link=feed_data.get("link"),
                    note=feed_data.get("note"),
                    check_interval=feed_data.get("check_interval"),
                    enable_preview=feed_data.get("enable_preview"),
                    processing=feed_data.get("processing", {}),
                    extra_flags=feed_data.get("extra_flags", {}),
                )

            channels.append(
                ChannelConfig(
                    id=channel_data["id"],
                    name=channel_data["name"],
                    feeds=feeds,
                    enable_preview=channel_data.get("enable_preview"),
                    check_interval=channel_data.get("check_interval"),
                    processing=channel_data.get("processing", {}),
                )
            )

        config = Config(global_config=global_config, channels=channels)
        logger.info(f"Loaded config with {len(channels)} channels and {sum(len(c.feeds) for c in channels)} feeds")
        return config

    def save(self, config: Config):
        data = {
            "global": {
                "check_interval": config.global_config.check_interval,
                "enable_preview": config.global_config.enable_preview,
                "processing": config.global_config.processing,
                "send_delay": config.global_config.send_delay,
            },
            "channels": [
                {
                    "id": channel.id,
                    "name": channel.name,
                    "enable_preview": channel.enable_preview,
                    "check_interval": channel.check_interval,
                    "processing": channel.processing,
                    "feeds": {
                        url: {
                            "name": feed.name,
                            "link": feed.link,
                            "note": feed.note,
                            "check_interval": feed.check_interval,
                            "enable_preview": feed.enable_preview,
                            "processing": feed.processing,
                            "extra_flags": feed.extra_flags,
                        }
                        for url, feed in channel.feeds.items()
                    },
                }
                for channel in config.channels
            ],
        }

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(data, indent=2))
        logger.info(f"Saved config to {self.config_path}")
