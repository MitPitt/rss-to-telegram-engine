import asyncio
import json
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from core.models import Entry
from processing.base import Processor

logger = logging.getLogger(__name__)


class YtDlpDownloaderProcessor(Processor):
    name = "ytdlp_downloader"

    DEFAULT_URL_PATTERNS = [
        r"https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+",
        r"https?://(?:www\.)?youtube\.com/shorts/[\w-]+",
        r"https?://(?:www\.)?youtu\.be/[\w-]+",
        r"https?://v\.redd\.it/[\w]+",
    ]

    DEFAULT_MAX_SIZE_MB = 50

    DEFAULT_MAX_DURATION = 900

    def __init__(self):
        self._proxy_cache: Dict[str, str] = {}

    def _load_proxy_url(self, proxy_file: str) -> Optional[str]:
        if proxy_file in self._proxy_cache:
            return self._proxy_cache[proxy_file]
        try:
            if not os.path.exists(proxy_file):
                logger.warning(f"Proxy file not found: {proxy_file}")
                return None
            with open(proxy_file, "r") as f:
                proxy_url = f.read().strip()
            if not proxy_url:
                logger.warning(f"Proxy file is empty: {proxy_file}")
                return None
            if not proxy_url.startswith(("socks5://", "socks4://", "http://", "https://")):
                logger.warning(f"Invalid proxy URL format in {proxy_file}: {proxy_url[:20]}...")
                return None
            self._proxy_cache[proxy_file] = proxy_url
            logger.debug(f"Loaded proxy from {proxy_file}")
            return proxy_url
        except Exception as e:
            logger.error(f"Failed to read proxy file {proxy_file}: {e}")
            return None

    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        # Get configuration
        url_patterns = config.get("url_patterns", self.DEFAULT_URL_PATTERNS)
        search_in = config.get("search_in", "link")
        cookies_file = config.get("cookies_file")
        proxy_file = config.get("proxy_file")
        max_filesize_mb = config.get("max_filesize", self.DEFAULT_MAX_SIZE_MB)
        max_duration = config.get("max_duration", self.DEFAULT_MAX_DURATION)
        timeout = config.get("download_timeout", 300)
        quality = config.get("quality", "best[height<=720]/bv+ba/bv")
        extract_audio = config.get("extract_audio", False)

        # Load proxy URL from file if provided
        proxy_url = None
        if proxy_file:
            proxy_url = self._load_proxy_url(proxy_file)

        # Compile regex patterns
        compiled_patterns = [re.compile(pattern) for pattern in url_patterns]

        # Find matching URLs in entry
        urls_to_download = self._find_matching_urls(entry, compiled_patterns, search_in)

        if not urls_to_download:
            logger.debug(f"No matching URLs found in entry: {entry.title}")
            return entry

        logger.info(f"Found {len(urls_to_download)} video URLs to download in '{entry.title}'")

        # Download videos
        for url in urls_to_download:
            try:
                result = await self._download_video(
                    url=url,
                    cookies_file=cookies_file,
                    proxy_url=proxy_url,
                    max_filesize_mb=max_filesize_mb,
                    max_duration=max_duration,
                    timeout=timeout,
                    quality=quality,
                    extract_audio=extract_audio,
                )

                if result:
                    if extract_audio:
                        # Audio returns (data, filename, thumbnail_data)
                        data, filename, thumbnail_data = result
                        entry.audio_buffers.append((data, url, filename, thumbnail_data))
                        logger.info(f"Downloaded audio from {url}: {filename}")
                    else:
                        # Video returns (data, filename)
                        data, filename = result
                        entry.video_buffers.append((data, url, filename))
                        logger.info(f"Downloaded video from {url}: {filename}")

            except Exception as e:
                logger.error(f"Failed to download video from {url}: {e}", exc_info=True)

        return entry

    def _find_matching_urls(self, entry: Entry, patterns: List[re.Pattern], search_in: str = "link") -> List[str]:
        urls = set()

        if search_in in ("link", "all"):
            for pattern in patterns:
                if pattern.match(entry.link):
                    urls.add(entry.link)
                    break

        if search_in in ("title", "all") and entry.title:
            for pattern in patterns:
                matches = pattern.findall(entry.title)
                urls.update(matches)

        if search_in in ("content", "content_first", "all") and entry.content:
            for pattern in patterns:
                matches = pattern.findall(entry.content)
                if search_in == "content_first" and matches:
                    urls.add(matches[0])
                    break
                else:
                    urls.update(matches)

        return list(urls)

    async def _download_video(
        self,
        url: str,
        cookies_file: Optional[str],
        proxy_url: Optional[str],
        max_filesize_mb: int,
        max_duration: int,
        timeout: int,
        quality: str,
        extract_audio: bool,
    ) -> Optional[Tuple[bytes, str, Optional[bytes]]]:
        # Create temporary directory for download in a dedicated subdirectory
        # This ensures easy cleanup even if something goes wrong
        base_tmpdir = Path(tempfile.gettempdir()) / "telegram-rss-ytdlp"
        base_tmpdir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=base_tmpdir, prefix="download_") as tmpdir:
            tmppath = Path(tmpdir)
            # Use title for filename (more descriptive than ID)
            output_template = str(tmppath / "%(title)s.%(ext)s")

            # First, get video info to check duration and size (with retries)
            info_cmd = self._build_ytdlp_command(
                url=url,
                output_template=None,
                cookies_file=cookies_file,
                proxy_url=proxy_url,
                quality=quality,
                extract_audio=extract_audio,
                info_only=True,
            )

            # Retry info fetch up to 3 times on timeout or parse errors
            max_retries = 3
            video_info = None

            for attempt in range(1, max_retries + 1):
                try:
                    logger.debug(f"Getting video info for {url} (attempt {attempt}/{max_retries})")
                    info = await self._run_command(info_cmd, timeout=60, capture_output=True)

                    if not info:
                        if attempt < max_retries:
                            logger.warning(f"Attempt {attempt}/{max_retries} - No info returned. Retrying...")
                            await asyncio.sleep(60)
                            continue
                        else:
                            logger.warning(f"Failed to get video info for {url} after {max_retries} attempts")
                            return None

                    video_info = json.loads(info)
                    break

                except (asyncio.TimeoutError, json.JSONDecodeError) as e:
                    if attempt < max_retries:
                        logger.warning(f"Attempt {attempt}/{max_retries} failed: {e}. Retrying...")
                        await asyncio.sleep(60)
                    else:
                        logger.warning(f"Failed to get video info for {url} after {max_retries} attempts: {e}")
                        return None

            if not video_info:
                logger.warning(f"Failed to get video info for {url}")
                return None

            try:
                duration = video_info.get("duration")
                if duration and duration > max_duration:
                    logger.warning(f"Video too long ({duration}s > {max_duration}s): {url}")
                    return None

                filesize = video_info.get("filesize") or video_info.get("filesize_approx")
                if filesize:
                    filesize_mb = filesize / (1024 * 1024)
                    if filesize_mb > max_filesize_mb:
                        logger.warning(f"Video too large ({filesize_mb:.1f}MB > {max_filesize_mb}MB): {url}")
                        return None

                download_cmd = self._build_ytdlp_command(
                    url=url,
                    output_template=output_template,
                    cookies_file=cookies_file,
                    proxy_url=proxy_url,
                    quality=quality,
                    extract_audio=extract_audio,
                    max_filesize_mb=max_filesize_mb,
                )

                logger.debug(f"Downloading video from {url}")
                await self._run_command(download_cmd, timeout=timeout)

                if extract_audio:
                    files = list(tmppath.glob("*.mp3")) + list(tmppath.glob("*.m4a")) + list(tmppath.glob("*.opus"))
                else:
                    files = list(tmppath.glob("*.mp4")) + list(tmppath.glob("*.webm")) + list(tmppath.glob("*.mkv"))

                if not files:
                    logger.warning(f"No file downloaded for {url}")
                    return None
                downloaded_file = files[0]

                file_size = downloaded_file.stat().st_size
                if file_size > max_filesize_mb * 1024 * 1024:
                    logger.warning(f"Downloaded file too large ({file_size / 1024 / 1024:.1f}MB): {url}")
                    return None

                with open(downloaded_file, "rb") as f:
                    data = f.read()

                filename = downloaded_file.name

                # For audio process thumbnail
                thumbnail_data = None
                if extract_audio:
                    thumbnail_data = await self._process_thumbnail(tmppath)

                logger.debug(f"Successfully downloaded {len(data)} bytes: {filename}")

                if extract_audio:
                    return (data, filename, thumbnail_data)
                else:
                    return (data, filename)

            except asyncio.TimeoutError:
                logger.warning(f"Timeout downloading {url}")
                return None
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse video info for {url}: {e}")
                return None
            except Exception as e:
                logger.error(f"Error downloading {url}: {e}", exc_info=True)
                return None

    async def _process_thumbnail(self, tmppath: Path) -> Optional[bytes]:
        try:
            thumbnail_files = list(tmppath.glob("*.jpg")) + list(tmppath.glob("*.webp"))
            if not thumbnail_files:
                logger.debug("No thumbnail found for audio")
                return None

            thumbnail_path = thumbnail_files[0]
            logger.debug(f"Processing thumbnail: {thumbnail_path}")

            def process_image():
                img = Image.open(thumbnail_path)
                img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                processed_img = Image.new("RGB", (320, 320), (0, 0, 0))
                x_offset = (320 - img.width) // 2
                y_offset = (320 - img.height) // 2
                processed_img.paste(img, (x_offset, y_offset))

                output = BytesIO()
                processed_img.save(output, format="JPEG", quality=85)
                return output.getvalue()

            thumbnail_data = await asyncio.to_thread(process_image)
            logger.debug(f"Processed thumbnail: {len(thumbnail_data)} bytes")
            return thumbnail_data

        except Exception as e:
            logger.warning(f"Failed to process thumbnail: {e}")
            return None

    def _build_ytdlp_command(
        self,
        url: str,
        output_template: Optional[str],
        cookies_file: Optional[str],
        proxy_url: Optional[str],
        quality: str,
        extract_audio: bool,
        max_filesize_mb: Optional[int] = None,
        info_only: bool = False,
    ) -> List[str]:
        cmd = ["yt-dlp"]

        if proxy_url:
            cmd.extend(["--proxy", proxy_url])
        if cookies_file and os.path.exists(cookies_file):
            cmd.extend(["--cookies", cookies_file])

        # Format selection
        if extract_audio:
            cmd.extend(
                [
                    "-f",
                    "ba[acodec^=mp3]/ba/b",
                    "-x",  # Extract audio
                    "--audio-format",
                    "mp3",
                    "--audio-quality",
                    "192",  # 192 kbps
                    "--write-thumbnail",  # Download thumbnail
                    "--convert-thumbnails",
                    "jpg",  # Convert to JPEG
                ]
            )
        else:
            cmd.extend(["-f", quality])

        # Max filesize
        if max_filesize_mb and not info_only:
            cmd.extend(["--max-filesize", f"{max_filesize_mb}M"])

        if info_only:
            cmd.extend(["--dump-json", "--no-download"])
        else:
            if output_template:
                cmd.extend(["-o", output_template])

            cmd.extend(
                [
                    "--no-playlist",  # Don't download playlists
                    "--no-warnings",
                    "--no-check-certificate",  # Useful for some sites
                ]
            )

        cmd.append(url)

        return cmd

    async def _run_command(self, cmd: List[str], timeout: int, capture_output: bool = False) -> Optional[str]:
        try:
            if capture_output:
                process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

                if process.returncode != 0:
                    logger.warning(f"Command failed with code {process.returncode}: {' '.join(cmd)}")
                    if stderr:
                        logger.debug(f"stderr: {stderr.decode('utf-8', errors='ignore')[:500]}")
                    return None

                return stdout.decode("utf-8")
            else:
                process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

                if process.returncode != 0:
                    logger.warning(f"Command failed with code {process.returncode}: {' '.join(cmd)}")
                    if stderr:
                        logger.debug(f"stderr: {stderr.decode('utf-8', errors='ignore')[:500]}")
                    return None

                return None

        except asyncio.TimeoutError:
            logger.error(f"Command timed out after {timeout}s: {' '.join(cmd)}")
            raise
        except Exception as e:
            logger.error(f"Error running command: {e}")
            raise
