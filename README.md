This is an RSS-to-Telegram bot. Made mostly for personal use.

## Table of Contents

- [Features](#features)
- [Run](#run)
- [Config](#config)
- [Processors](#processors)
  - [media_extract](#media_extract)
  - [html_to_telegram](#html_to_telegram)
  - [jinja_formatter](#jinja_formatter)
  - [content_filter](#content_filter)
  - [ytdlp_downloader](#ytdlp_downloader)
- [Feeds I tested](#feeds-i-tested-and-use)
- [Alternatives](#alternatives)

# Features

- Multi-channel support with hierarchical and per-feed configuration
- Media extraction and downloading, with yt-dlp integration
- Customizable message formatting 
- Content filtering 
- Rate limiting for RSS source requests

# Usage
1. Create bot in [@BotFather](https://t.me/BotFather)
2. Create and fill out .env 
3. Create and fill out config.json
4. Run
    - Using uv: `uv run python -m bot.main`
    - Using docker compose: `docker compose up -d --build`

# Config

All configuration files, state, templates, cookies, and proxy files should be placed in the `config/` directory.

Example .env:
```
# Bot token from @BotFather
BOT_TOKEN=your_bot_token_here

# Comma-separated list of user IDs or channel IDs
ADMIN_IDS=123456789

# Logging level: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=INFO

# Path to configuration file
CONFIG_PATH=config/config.json

# Path to state file
STATE_PATH=config/state.json
```

Example config.json:
```json
{
    "_comment": "You can split configs into multiple files using 'includes'.",
    "includes": [],
    "global": {
        "check_interval": 300,
        "enable_preview": true,
        "processing": {
            "media_extract": {},
            "html_to_telegram": {},
            "jinja_formatter": {}
        }
    },
    "channels": [
        {
            "id": -1001234567890,
            "name": "tech news feed",
            "feeds": {
                "https://hnrss.org/newest": {
                    "note": "This feed will use title from RSS since name is null",
                    "processing": {
                        "media_extract": {},
                        "html_to_telegram": {},
                        "jinja_formatter": {
                            "show_author": true,
                            "show_title": true,
                            "show_content": true
                        }
                    }
                },
                "https://github.blog/feed/": {
                    "name": "GitHub Blog",
                    "note": "Custom name overrides RSS title.",
                    "check_interval": 600,
                    "processing": {
                        "media_extract": {
                            "download_media": true,
                            "max_media_size": 20971520,
                            "download_timeout": 30
                        },
                        "html_to_telegram": {},
                        "jinja_formatter": {
                            "show_author": false
                        }
                    }
                }
            }
        }
    ]
}
```

# Processors

Processors transform RSS entries before sending to Telegram. They run in the order specified in `processing` config. Each processor can be configured per-feed, per-channel, or globally.

Any processor pipeline should end with `html_to_telegram` and `jinja_formatter` processors to guarantee a successful telegram message.

## media_extract

Extracts media (images, videos, audio) from entry content and enclosures.

Options:
- `download_media` (bool, default: true) - Download media to buffers when True. Pass as urls when False (sometimes less robust)
- `max_media_size` (int, default: 20MB) - Maximum file size in bytes
- `download_timeout` (int, default: 30) - Download timeout in seconds
- `skip_if_has_media` (bool, default: false) - Skip if entry already has media (useful when run after yt-dlp processor)
- `remove_media_tags` (bool, default: true) - Remove media tags from content

```json
"media_extract": {
    "download_media": true,
    "max_media_size": 10485760
}
```

## html_to_telegram

Converts HTML content to Telegram-compatible HTML. Should run after `media_extract` but before `jinja_formatter`.

```json
"html_to_telegram": {}
```

## jinja_formatter

Formats the final message using Jinja2 templates. Should be the last processor.

All config options are passed to the template as context variables, so templates can define their own options.

Processor options:
- `template` (string, default: "default") - Template name (without .j2)
- `feed_name` - Custom feed name (overrides RSS title)
- `content_use_blockquote` (bool) - Wrap content in expandable blockquote
- `blockquote_length_threshold` (int, default: 750) - Min length to use blockquote
- `try_replace_content_with_title` (bool) - Fallback strategy when message too long

Default template (`config/jinja_templates/default.j2`) options:
- `show_title` (bool, default: true) - Show entry title
- `show_content` (bool, default: true) - Show entry content
- `show_author` (bool, default: false) - Show author name
- `title_bold` (bool, default: true) - Wrap title in `<b>` tags
- `title_underline` (bool, default: true) - Wrap title in `<u>` tags
- `title_as_link` (bool, default: true) - Make title a clickable link
- `plain_link` (bool, default: false) - Show raw URL instead of feed name link

```json
"jinja_formatter": {
    "show_author": true,
    "show_title": true,
    "show_content": false,
    "title_bold": true,
    "title_as_link": true
}
```

## content_filter

Filters posts based on regex pattern matching.

Options:
- `patterns` (list) - Regex patterns to match
- `match_title` (bool, default: true) - Check title
- `match_content` (bool, default: true) - Check content
- `match_mode` ("any" | "all", default: "any") - How patterns combine
- `invert` (bool, default: false) - If true, filter OUT non-matching posts
- `flags` (string) - Regex flags (e.g., "IGNORECASE,MULTILINE")

Filter out ads/sponsored posts:
```json
"content_filter": {
    "patterns": ["Advertisement", "Sponsored"],
    "match_mode": "any"
}
```

Keep only posts about Python:
```json
"content_filter": {
    "patterns": ["[Pp]ython"],
    "invert": true
}
```

## ytdlp_downloader

Downloads videos using yt-dlp. Requires yt-dlp to be installed.

Options:
- `url_patterns` (list) - Regex patterns to match video URLs (default: YouTube, Reddit video)
- `search_in` (string, default: "link") - Where to search for URLs:
  - `"link"` - Only search in entry.link
  - `"title"` - Only search in entry.title
  - `"content"` - Search in entry.content (all matches)
  - `"content_first"` - Only first match in entry.content
  - `"all"` - Search everywhere
- `cookies_file` (string) - Path to cookies.txt file (e.g., `config/cookies.txt`)
- `proxy_file` (string) - Path to file containing proxy URL (e.g., `config/proxy.txt` with `socks5://host:port`)
- `max_filesize` (int, default: 50) - Max file size in MB
- `max_duration` (int, default: 900) - Max video duration in seconds
- `download_timeout` (int, default: 300) - Download timeout in seconds
- `quality` (string, default: "best[height<=720]") - yt-dlp format selection
- `extract_audio` (bool, default: false) - Extract audio only (mp3 with thumbnail)

```json
"ytdlp_downloader": {
    "url_patterns": ["https?://(?:www\\.)?youtube\\.com/watch"],
    "search_in": "link",
    "max_filesize": 50,
    "quality": "best[height<=720]"
}
```

# Feeds I tested and use:
- [RSS-Bridge/rss-bridge](https://github.com/RSS-Bridge/rss-bridge) — Create feeds from Youtube, Vk, Telegram channels and more.
- [trashhalo/reddit-rss](https://github.com/trashhalo/reddit-rss) — Doesn't require a reddit account. Reddit also has native rss feeds but they lack media. 

# Alternatives
- [Rongronggg9/RSS-to-Telegram-Bot](https://github.com/Rongronggg9/RSS-to-Telegram-Bot) — Better multi-user support
