This is an RSS-to-Telegram bot. Made mostly for personal use like an RSS-reader.

See [Feed Examples](#feed-examples) to see screenshot examples.

## Table of Contents

- [Features](#features)
- [Run](#run)
- [Config](#config)
- [Commands](#commands)
- [Processors](#processors)
  - [media_extract](#media_extract)
  - [html_to_telegram](#html_to_telegram)
  - [jinja_formatter](#jinja_formatter)
  - [content_filter](#content_filter)
  - [ytdlp_downloader](#ytdlp_downloader)
- [Suggestions](#suggestions)
- [Feed Examples](#feed-examples)
  - [Reddit](#reddit-with-media)
  - [Youtube](#youtube)
  - [Youtube to mp3](#music-audio-from-youtube)
  - [Telegram](#telegram)
  - [Vk](#vkontakte)
- [Alternatives](#alternatives)

# Features

- Multi-channel support with hierarchical and per-feed configuration
- Media extraction and downloading, with yt-dlp integration
- Customizable message formatting 
- Content filtering 
- Rate limiting for RSS source requests

# Usage
1. Create bot in [@BotFather](https://t.me/BotFather)
2. Create and fill out .env (in root directory)
3. Create and fill out config.json (in config directory)
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

# Path to main configuration file
CONFIG_PATH=config/config.json

# Path to state file
STATE_PATH=config/state.json

# Local Telegram Api (Optional, uncomment container in docker-compose.yml)
# Allows for larger uploads up to 2GB in size
# TELEGRAM_API_SERVER_URL="http://telegram-api-server:8081"
# TELEGRAM_API_ID="123..."
# TELEGRAM_API_HASH="12345..."
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

# Commands
Only admins specified in .env can use commands.

- `/help` or `/status` - pull up Command list and Bot status
- `/list` - list out all live feeds
- `/test` - test feed url
- `/reload` - reload config after you edited the json files.

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

# Suggestions:
- [RSS-Bridge/rss-bridge](https://github.com/RSS-Bridge/rss-bridge) — Create feeds from Youtube, Vk, Telegram channels and more.
- [trashhalo/reddit-rss](https://github.com/trashhalo/reddit-rss) — Media-rich reddit RSS. Doesn't require a reddit account. 

# Feed examples

Once you configured a feed you can use `/test` command to test it with the whole processing pipeline (`/test` command will try finding the url somewhere in config).

## Reddit with media

You must self-host [trashhalo/reddit-rss](https://github.com/trashhalo/reddit-rss) to get videos and high resolution pirctures in RSS feed.

```
{
    "id": -100123,
    "name": "vidya reddit",
    "enable_preview": false,
    "processing": {
        "ytdlp_downloader": {
            "search_in": "link",
            "max_duration": 200
        },
        "media_extract": {
            "skip_if_has_media": true
        },
        "html_to_telegram": {},
        "jinja_formatter": {
            "plain_link": false,
            "content_use_blockquote": true,
            "blockquote_only_if_exceeds": true
        }
    },
    "feeds": {
        "https://reddit-rss.example.com/r/shittydarksouls/top.json?sort=top&t=week": {
            "name": "reddit.com/r/shittydarksouls"
        }
    }
}
```
<img src="docs/reddit_example.png" width="45%">

## Youtube

I suggest self-hosting [RSS-Bridge/rss-bridge](https://github.com/RSS-Bridge/rss-bridge) but you can use the public instance.

Will download short videos, and just link the large ones. `show_content": false` to hide video description.

```
{
    "id": -100123,
    "name": "vidya youtube",
    "processing": {
        "ytdlp_downloader": {
            "search_in": "link",
            "max_duration": 200
        },
        "html_to_telegram": {},
        "jinja_formatter": {
            "show_content": false
        }
    },
    "feeds": {
        "https://rss-bridge.org/?action=display&bridge=YouTubeFeedExpanderBridge&channel=UC2oWuUSd3t3t5O3Vxp4lgAA&format=Atom": {},
        "https://rss-bridge.org/?action=display&bridge=YouTubeFeedExpanderBridge&channel=UCDPG5a6rinyhES4wTLrGfig&format=Atom": {}
    }
}
```

<p float="left">
    <img src="docs/youtube_example_2.png" width="45%">
    <img src="docs/youtube_example_1.png" width="45%">
</p>

## Music audio from YouTube

`"show_content": false` to hide video description. `cookies_file` and `proxy_file` are optional.
```
{
    "id": -100123,
    "name": "Ringtone bangers",
    "processing": {
        "ytdlp_downloader": {
            "cookies_file": "config/cookies.txt",
            "proxy_file": "config/socks5.txt",
            "extract_audio": true,
            "search_in": "link",
            "max_duration": 2160
        },
        "media_extract": {
            "download_media": true,
            "max_media_size": 20971520,
            "download_timeout": 30
        },
        "html_to_telegram": {},
        "jinja_formatter": {
            "show_content": false
        }
    },
    "feeds": {
        "https://rss-bridge.org/bridge01/?action=display&bridge=YouTubeFeedExpanderBridge&channel=UCjEk4ipFLqcdZ_m7uqItTIw&format=Atom": {}
    }
}
```
<img src="docs/youtube_music_example.png" width="65%">

## Telegram 

Telegram natively embeds all media nicely, skip everything other than link.

```
{
    "id": 123,
    "name": "animals telegram",
    "processing": {
        "html_to_telegram": {},
        "jinja_formatter": {
            "show_title": false,
            "show_content": false
        }
    },
    "feeds": {
        "https://rss-bridge.org/?action=display&bridge=TelegramBridge&username=@birblife&format=Atom": {}
    }
}
```
<img src="docs/telegram_example.png" width="50%">

## Vkontakte

You must self-host [RSS-Bridge/rss-bridge](https://github.com/RSS-Bridge/rss-bridge) and use Vk api by using your Vk.com account and creating a Vk app. See [rss-bridge docs](https://rss-bridge.github.io/rss-bridge/Bridge_Specific/Vk2.html).

```
{
    "id": -100123,
    "name": "hema news vk",
    "processing": {
        "media_extract": {},
        "html_to_telegram": {},
        "jinja_formatter": {
            "show_title": false,
            "try_replace_content_with_title": true,
            "content_use_blockquote": true,
            "blockquote_only_if_exceeds": true
        }
    },
    "feeds": {
        "https://rss.example.com/?action=display&bridge=Vk2Bridge&u=wildarmory&format=Atom": {},
        "https://rss.example.com/?action=display&bridge=Vk2Bridge&u=ur_for_hema&format=Atom": {}
    }
}
```

Using `"content_use_blockquote": true` will hide long text in exandable block.
Text exceeding telegram limit of 4096 will be excluded entirely.

<p float="left">
    <img src="docs/vk_example.png" width="45%">
    <img src="docs/vk_example_2.png" width="45%">
</p>

# Alternatives
- [Rongronggg9/RSS-to-Telegram-Bot](https://github.com/Rongronggg9/RSS-to-Telegram-Bot) — Better multi-user support
