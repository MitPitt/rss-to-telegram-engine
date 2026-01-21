"""
Telegram HTML Processing - Should work like html conversion from Rongronggg9/RSS-to-Telegram-Bot

The html_to_telegram processor uses this module
"""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial
from html import unescape
from itertools import chain, count, groupby
from typing import Final, Iterable, Optional, Tuple, Union
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PageElement, Tag

logger = logging.getLogger(__name__)

# ============================================================================
# PARSING UTILITIES
# ============================================================================

# Special spaces that Telegram handles specially
SPACES: Final[str] = " \xa0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u202f\u205f\u3000"

# Characters that are invalid in Telegram messages
INVALID_CHARACTERS: Final[str] = (
    "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f\u2028\u2029"
)


def _escape_special_char_in_re_set(text: str) -> str:
    """Escape special characters for use in regex character sets"""
    return re.sub(r"([\\\-\[\]])", r"\\\1", text)


def _merge_chars_into_ranged_set(sorted_chars: str) -> str:
    """Merge consecutive characters into ranges for regex sets"""
    monotonic = count()
    groups = ("".join(g) for _, g in groupby(sorted_chars, key=lambda char: ord(char) - next(monotonic)))
    ranged_set = "".join(
        f"{_escape_special_char_in_re_set(g[0])}-{_escape_special_char_in_re_set(g[-1])}" if len(g) > 2 else _escape_special_char_in_re_set(g)
        for g in groups
    )
    return ranged_set


# Compiled regex patterns
replaceInvalidCharacter = partial(re.compile(rf"[{_merge_chars_into_ranged_set(INVALID_CHARACTERS)}]").sub, " ")
replaceSpecialSpace = partial(re.compile(rf"[{_merge_chars_into_ranged_set(SPACES[1:])}]").sub, " ")
stripLineEnd = partial(re.compile(rf"[{_merge_chars_into_ranged_set(SPACES)}]+\n").sub, "\n")
stripNewline = partial(re.compile(r"\n{3,}").sub, "\n\n")
stripAnySpace = partial(re.compile(r"\s+").sub, " ")
isAbsoluteHttpLink = re.compile(r"^https?://").match
isSmallIcon = re.compile(r"(width|height): ?(([012]?\d|30)(\.\d)?px|([01](\.\d)?|2)r?em)").search
srcsetParser = re.compile(r"(?:^|,\s*)(?P<url>\S+)(?:\s+(?P<number>\d+(\.\d+)?)(?P<unit>[wx]))?\s*(?=,|$)").finditer


def resolve_relative_link(base: Optional[str], url: Optional[str]) -> str:
    """Resolve relative URLs to absolute URLs"""
    if not base or not url or isAbsoluteHttpLink(url) or not isAbsoluteHttpLink(base):
        return url or ""
    return urljoin(base, url)


def is_emoticon(tag) -> bool:
    """Detect if an image tag is an emoticon/emoji"""
    if not hasattr(tag, "name") or tag.name != "img":
        return False

    src = tag.get("src", "")
    alt = tag.get("alt", "")
    _class = tag.get("class", "")
    style = tag.get("style", "")
    width_attr = tag.get("width", "")
    height_attr = tag.get("height", "")

    try:
        width = int(width_attr) if width_attr and str(width_attr).isdigit() else 10000
        height = int(height_attr) if height_attr and str(height_attr).isdigit() else 10000
    except (ValueError, TypeError):
        width = height = 10000

    return (
        width <= 30
        or height <= 30
        or isSmallIcon(style)
        or "emoji" in str(_class)
        or "emoticon" in str(_class)
        or (alt.startswith(":") and alt.endswith(":"))
        or src.startswith("data:")
    )


def emojify(text: str) -> str:
    """Convert emoji shortcodes to actual emoji"""
    if not text:
        return text
    try:
        from emoji import emojize

        return emojize(text, language="alias", variant="emoji_type")
    except ImportError:
        return text


# ============================================================================
# HTML NODE TREE STRUCTURE
# ============================================================================

TypeTextContent = Union["Text", str, list["Text"]]


class Text:
    """Base class for HTML text nodes"""

    tag: Optional[str] = None
    attr: Optional[str] = None

    def __init__(self, content: TypeTextContent, param: Optional[str] = None, *_args, **_kwargs):
        if content is None:
            content = ""
        self.param = param
        if type(content) is type(self) or type(content) is Text:
            self.content = content.content
        elif type(content) is str:
            # Escape special HTML characters in text content
            self.content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        else:
            self.content = content

    def is_nested(self) -> bool:
        return type(self.content) is not str

    def is_listed(self) -> bool:
        return type(self.content) is list

    def strip(self, deeper: bool = False, strip_l: Optional[bool] = True, strip_r: Optional[bool] = True):
        """Strip whitespace and line breaks from content"""
        if not self.is_nested():
            if strip_l:
                self.content = self.content.lstrip()
            if strip_r:
                self.content = self.content.rstrip()
            return
        if not self.is_listed():
            if deeper:
                self.content.strip()
            return
        # listed
        while strip_l and self.content and type(self.content[0]) is Br:
            self.content.pop(0)
        while strip_r and self.content and type(self.content[-1]) is Br:
            self.content.pop()
        if deeper:
            for text in self.content:
                text.strip(deeper=deeper, strip_l=strip_l, strip_r=strip_r)

    def lstrip(self, deeper: bool = False):
        self.strip(deeper=deeper, strip_r=False)

    def rstrip(self, deeper: bool = False):
        self.strip(deeper=deeper, strip_l=False)

    def is_empty(self, allow_whitespace: bool = False) -> bool:
        if self.is_listed():
            return all(subText.is_empty(allow_whitespace=allow_whitespace) for subText in self.content)
        elif self.is_nested():
            return self.content.is_empty(allow_whitespace=allow_whitespace)
        else:
            return not (self.content if allow_whitespace else self.content and self.content.strip())

    def get_html(self, plain: bool = False) -> str:
        """Generate HTML string from the node tree"""
        if self.is_listed():
            result = "".join(subText.get_html(plain=plain) for subText in self.content)
        elif self.is_nested():
            result = self.content.get_html(plain=plain)
        else:
            result = self.content

        if not plain:
            if self.attr and self.param:
                return f'<{self.tag} {self.attr}="{self.param}">{result}</{self.tag}>'
            if self.tag:
                return f"<{self.tag}>{result}</{self.tag}>"
        return result

    def __len__(self):
        if type(self.content) == list:
            return sum(len(subText) for subText in self.content)
        return len(self.content)

    def __bool__(self):
        return bool(self.content)

    def __str__(self):
        return self.get_html()


class HtmlTree(Text):
    """Root HTML tree node"""

    pass


class Link(Text):
    """<a href="...">text</a>"""

    tag = "a"
    attr = "href"

    def __init__(self, content: TypeTextContent, param: str, *_args, **_kwargs):
        super().__init__(content, param)


class Bold(Text):
    """<b>text</b>"""

    tag = "b"

    def __init__(self, content: TypeTextContent, *_args, **_kwargs):
        super().__init__(content)


class Italic(Text):
    """<i>text</i>"""

    tag = "i"

    def __init__(self, content: TypeTextContent, *_args, **_kwargs):
        super().__init__(content)


class Underline(Text):
    """<u>text</u>"""

    tag = "u"

    def __init__(self, content: TypeTextContent, *_args, **_kwargs):
        super().__init__(content)


class Strike(Text):
    """<s>text</s>"""

    tag = "s"

    def __init__(self, content: TypeTextContent, *_args, **_kwargs):
        super().__init__(content)


class Blockquote(Text):
    """<blockquote>text</blockquote>"""

    tag = "blockquote"

    def __init__(self, content: TypeTextContent, *_args, **_kwargs):
        super().__init__(content)


class Code(Text):
    """<code>text</code> or <code class="language-...">text</code>"""

    tag = "code"
    attr = "class"


class Pre(Text):
    """<pre>text</pre>"""

    tag = "pre"

    def __init__(self, content: TypeTextContent, *_args, **_kwargs):
        super().__init__(content)


class Br(Text):
    """Line break"""

    def __init__(self, count: int = 1, copy: bool = False, *_args, **_kwargs):
        if copy:
            super().__init__(self.content)
            return
        if not isinstance(count, int):
            count = 1
        super().__init__("\n" * count)

    def get_html(self, plain: bool = False):
        return "" if plain else super().get_html()


class Hr(Text):
    """Horizontal rule"""

    def __init__(self, *_args, **_kwargs):
        super().__init__("\n———\n")

    def get_html(self, plain: bool = False):
        return "" if plain else super().get_html()


class ListItem(Text):
    """List item"""

    def __init__(self, content, *_args, copy: bool = False, **_kwargs):
        super().__init__(content)
        if copy:
            return
        nested_lists = self._find_nested_lists()
        if nested_lists:
            for nested_list in nested_lists:
                nested_list.rstrip()
                nested_list_items = self._find_list_items(nested_list)
                if nested_list_items:
                    for nested_list_item in nested_list_items:
                        nested_list_item.content = [Text("    "), Text(nested_list_item.content)]
                    nested_list_items[-1].rstrip(deeper=True)

    def _find_nested_lists(self) -> list:
        result = []
        if isinstance(self.content, list):
            for item in self.content:
                if isinstance(item, (OrderedList, UnorderedList)):
                    result.append(item)
                elif hasattr(item, "_find_nested_lists"):
                    result.extend(item._find_nested_lists())
        elif isinstance(self.content, (OrderedList, UnorderedList)):
            result.append(self.content)
        elif hasattr(self.content, "_find_nested_lists"):
            result.extend(self.content._find_nested_lists())
        return result

    def _find_list_items(self, parent) -> list:
        if not isinstance(parent.content, list):
            return []
        return [item for item in parent.content if isinstance(item, ListItem)]


class OrderedList(Text):
    """Ordered list (numbered)"""

    def __init__(self, content, *_args, copy: bool = False, **_kwargs):
        super().__init__(content)
        if copy:
            return
        list_items = self._get_direct_list_items()
        if list_items:
            for index, list_item in enumerate(list_items, start=1):
                list_item.content = [Bold(f"{index}. "), Text(list_item.content), Br()]

    def _get_direct_list_items(self) -> list:
        if not isinstance(self.content, list):
            return []
        return [item for item in self.content if isinstance(item, ListItem)]


class UnorderedList(Text):
    """Unordered list (bulleted)"""

    def __init__(self, content, *_args, copy: bool = False, **_kwargs):
        super().__init__(content)
        if copy:
            return
        list_items = self._get_direct_list_items()
        if list_items:
            for list_item in list_items:
                list_item.content = [Bold("• "), Text(list_item.content), Br()]

    def _get_direct_list_items(self) -> list:
        if not isinstance(self.content, list):
            return []
        return [item for item in self.content if isinstance(item, ListItem)]


# ============================================================================
# HTML PARSER
# ============================================================================


def effective_link(content: TypeTextContent, href: str, base: str = None) -> Union[TypeTextContent, Link, Text]:
    """Create a link node or fallback representation"""
    if href.startswith("javascript"):
        return content
    href = resolve_relative_link(base, href)
    if not isAbsoluteHttpLink(href):
        return Text([Text(f"{content} ("), Code(href), Text(")")])
    return Link(content, href)


class HtmlParser:
    """Parse HTML content into Telegram-compatible format"""

    def __init__(self, html: str, feed_link: Optional[str] = None):
        self.html = html
        self.soup: Optional[BeautifulSoup] = None
        self.html_tree = HtmlTree("")
        self.feed_link = feed_link
        self.parsed = False
        self._parse_item_count = 0
        self.images: list[str] = []
        self.videos: list[str] = []
        self.audio: list[str] = []

    async def parse(self):
        """Parse the HTML content"""
        self.html = re.sub(r"<!--.*?-->", "", self.html, flags=re.DOTALL)
        self.soup = BeautifulSoup(self.html, "lxml")

        for tag in self.soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        self.html_tree = HtmlTree(await self._parse_item(self.soup))
        self.parsed = True

    def get_parsed_html(self) -> str:
        """Get the final parsed HTML"""
        if not self.parsed:
            raise RuntimeError("You must parse the HTML first")
        html = self.html_tree.get_html().strip()
        html = stripLineEnd(html)
        html = stripNewline(html)
        return html

    async def _parse_item(
        self, soup: Union[PageElement, BeautifulSoup, Tag, NavigableString, Iterable[PageElement]], in_list: bool = False
    ) -> Optional[Text]:
        """Recursively parse HTML elements"""
        self._parse_item_count += 1
        if self._parse_item_count % 64 == 0:
            await asyncio.sleep(0)

        result = []

        if hasattr(soup, "__iter__") and not isinstance(soup, (str, Tag, NavigableString)):
            prev_tag_name = None
            for child in soup:
                item = await self._parse_item(child, in_list)
                if item:
                    tag_name = child.name if isinstance(child, Tag) else None
                    if tag_name == "div" or prev_tag_name == "div":
                        html_str = item.get_html()
                        if not ((result and result[-1].get_html().endswith("\n")) or html_str.startswith("\n")):
                            result.append(Br())
                    result.append(item)
                    prev_tag_name = tag_name
            if not result:
                return None
            return result[0] if len(result) == 1 else Text(result)

        if isinstance(soup, NavigableString):
            if type(soup) is NavigableString:
                text = str(soup)
                return Text(emojify(text)) if text else None
            return None

        if not isinstance(soup, Tag):
            return None

        tag = soup.name
        if tag is None or tag == "script":
            return None

        # TABLE
        if tag == "table":
            rows = soup.find_all("tr")
            if not rows:
                return None
            rows_content = []
            for i, row in enumerate(rows):
                columns = row.find_all(["td", "th"])
                if len(rows) > 1 and len(columns) > 1:
                    logger.debug("Dropping complex table")
                    return None
                for j, column in enumerate(columns):
                    row_content = await self._parse_item(column)
                    if row_content:
                        rows_content.append(row_content)
                        if i < len(rows) - 1 or j < len(columns) - 1:
                            rows_content.append(Br(2))
            return Text(rows_content) if rows_content else None

        # PARAGRAPH
        if tag in ("p", "section"):
            parent = soup.parent.name if soup.parent else None
            text = await self._parse_item(soup.children, in_list)
            if text:
                if parent == "li":
                    return text
                text_l = [text]
                ps, ns = soup.previous_sibling, soup.next_sibling
                if not (isinstance(ps, Tag) and ps.name == "blockquote"):
                    text_l.insert(0, Br())
                if not (isinstance(ns, Tag) and ns.name == "blockquote"):
                    text_l.append(Br())
                return Text(text_l) if len(text_l) > 1 else text
            return None

        # BLOCKQUOTE
        if tag == "blockquote":
            quote = await self._parse_item(soup.children, in_list)
            if not quote:
                return None
            quote.strip()
            if quote.is_empty():
                return None
            return Blockquote(quote)

        # INLINE QUOTE
        if tag == "q":
            quote = await self._parse_item(soup.children, in_list)
            if not quote:
                return None
            quote.strip()
            if quote.is_empty():
                return None
            cite = soup.get("cite")
            if cite:
                quote = effective_link(quote, cite, self.feed_link)
            return Text([Text('"'), quote, Text('"')])

        # CODE/PRE
        if tag == "pre":
            return Pre(await self._parse_item(soup.children, in_list))

        if tag == "code":
            class_ = soup.get("class")
            lang_class = None
            if isinstance(class_, list):
                for cls in class_:
                    if cls.startswith("language-"):
                        lang_class = cls
                        break
            elif class_ and isinstance(class_, str):
                lang_class = class_ if class_.startswith("language-") else f"language-{class_}"
            return Code(await self._parse_item(soup.children, in_list), param=lang_class)

        # BR
        if tag == "br":
            return Br()

        # LINKS
        if tag == "a":
            text = await self._parse_item(soup.children, in_list)
            if not text or text.is_empty():
                return None
            href = soup.get("href")
            if not href:
                return None
            return effective_link(text, href, self.feed_link)

        # IMAGES
        if tag == "img":
            src = soup.get("src")
            srcset = soup.get("srcset")
            if not (src or srcset):
                return None
            if is_emoticon(soup):
                alt = soup.get("alt")
                return Text(emojify(alt)) if alt else None

            image_urls = []
            if srcset:
                srcset_matches = []
                for match in srcsetParser(srcset):
                    srcset_matches.append(
                        {
                            "url": match["url"],
                            "number": float(match["number"]) if match["number"] else 1,
                            "unit": match["unit"] if match["unit"] else "x",
                        }
                    )
                if src:
                    srcset_matches.append({"url": src, "number": 1, "unit": "x"})
                srcset_matches_w = [m for m in srcset_matches if m["unit"] == "w"]
                srcset_matches_x = [m for m in srcset_matches if m["unit"] == "x"]
                srcset_matches_w.sort(key=lambda m: m["number"], reverse=True)
                srcset_matches_x.sort(key=lambda m: m["number"], reverse=True)
                while srcset_matches_w or srcset_matches_x:
                    if srcset_matches_w:
                        image_urls.append(srcset_matches_w.pop(0)["url"])
                    if srcset_matches_x:
                        image_urls.append(srcset_matches_x.pop(0)["url"])
            elif src:
                image_urls.append(src)

            for url in image_urls:
                if not isinstance(url, str):
                    continue
                url = resolve_relative_link(self.feed_link, url)
                if url:
                    self.images.append(url)
                    break
            return None

        # VIDEO
        if tag == "video":
            multi_src = self._get_multi_src(soup)
            if multi_src:
                self.videos.extend(multi_src)
            return None

        # AUDIO
        if tag == "audio":
            multi_src = self._get_multi_src(soup)
            if multi_src:
                self.audio.extend(multi_src)
            return None

        # FORMATTING
        if tag in ("b", "strong"):
            text = await self._parse_item(soup.children, in_list)
            return Bold(text) if text else None

        if tag in ("i", "em"):
            text = await self._parse_item(soup.children, in_list)
            return Italic(text) if text else None

        if tag in ("u", "ins"):
            text = await self._parse_item(soup.children, in_list)
            return Underline(text) if text else None

        if tag in ("s", "strike", "del"):
            text = await self._parse_item(soup.children, in_list)
            return Strike(text) if text else None

        # HEADERS
        if tag == "h1":
            text = await self._parse_item(soup.children, in_list)
            return Text([Br(2), Bold(Underline(text)), Br()]) if text else None

        if tag == "h2":
            text = await self._parse_item(soup.children, in_list)
            return Text([Br(2), Bold(text), Br()]) if text else None

        if tag == "hr":
            return Hr()

        if tag.startswith("h") and len(tag) == 2:
            text = await self._parse_item(soup.children, in_list)
            return Text([Br(2), Underline(text), Br()]) if text else None

        # IFRAME
        if tag == "iframe":
            src = soup.get("src")
            if not src:
                return None
            src = resolve_relative_link(self.feed_link, src)
            hostname = urlparse(src).hostname or "embedded content"
            return Text([Br(2), effective_link(f"iframe ({hostname})", src), Br(2)])

        # LISTS
        if tag == "ol":
            texts = []
            list_items = soup.find_all("li", recursive=False)
            if not list_items:
                return None
            for list_item in list_items:
                text = await self._parse_item(list_item, in_list=True)
                if text:
                    texts.append(text)
            if not texts:
                return None
            return OrderedList([Br(), *texts, Br()])

        if tag in ("ul", "menu", "dir"):
            texts = []
            list_items = soup.find_all("li", recursive=False)
            if not list_items:
                return None
            for list_item in list_items:
                text = await self._parse_item(list_item, in_list=True)
                if text:
                    texts.append(text)
            if not texts:
                return None
            return UnorderedList([Br(), *texts, Br()])

        if tag == "li":
            text = await self._parse_item(soup.children, in_list)
            if not text:
                return None
            text.strip(deeper=True)
            if not text.get_html().strip():
                return None
            return ListItem(text) if in_list else UnorderedList([Br(), ListItem(text), Br()])

        # DEFAULT
        text = await self._parse_item(soup.children, in_list)
        return text or None

    def _get_multi_src(self, soup: Tag) -> list[str]:
        """Extract multiple source URLs from video/audio tags"""
        src = soup.get("src")
        _multi_src = [t.get("src") for t in soup.find_all(name="source") if t.get("src")]
        if src:
            _multi_src.append(src)
        multi_src = []
        for _src in _multi_src:
            if not isinstance(_src, str):
                continue
            _src = resolve_relative_link(self.feed_link, _src)
            if _src:
                multi_src.append(_src)
        return multi_src


# ============================================================================
# CONVERTER FUNCTIONS
# ============================================================================


async def parse_html(html: str, feed_link: Optional[str] = None) -> Tuple[str, list[str], list[str], list[str]]:
    """Parse HTML and extract content and media"""
    parser = HtmlParser(html=html, feed_link=feed_link)
    await parser.parse()
    return (parser.get_parsed_html(), parser.images, parser.videos, parser.audio)


async def html_to_telegram(html: str, feed_link: Optional[str] = None, extract_media: bool = False) -> Union[str, Tuple[str, list, list, list]]:
    """Convert HTML to Telegram-compatible HTML"""
    if not html:
        if extract_media:
            return "", [], [], []
        return ""

    try:
        parsed_html, images, videos, audio = await parse_html(html, feed_link)
        if extract_media:
            return parsed_html, images, videos, audio
        return parsed_html
    except Exception as e:
        logger.error(f"Error in HTML parsing: {e}", exc_info=True)
        result = _fallback_clean(html)
        if extract_media:
            return result, [], [], []
        return result


def _fallback_clean(html: str) -> str:
    """Fallback HTML cleaning using regex only"""
    if not html:
        return ""
    try:
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<(?:img|video|audio|iframe|table)[^>]*>.*?</(?:video|audio|iframe|table)>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<(?:br|/p|/div|/h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"<strong>", "<b>", html, flags=re.IGNORECASE)
        html = re.sub(r"</strong>", "</b>", html, flags=re.IGNORECASE)
        html = re.sub(r"<em>", "<i>", html, flags=re.IGNORECASE)
        html = re.sub(r"</em>", "</i>", html, flags=re.IGNORECASE)
        html = re.sub(r"<(?!/?(?:b|i|u|s|a|code|pre|blockquote)\b)[^>]+>", "", html, flags=re.IGNORECASE)
        html = unescape(html)
        html = replaceInvalidCharacter(html)
        html = re.sub(r"\n{3,}", "\n\n", html)
        return html.strip()
    except Exception as e:
        logger.error(f"Fallback cleaning error: {e}", exc_info=True)
        return re.sub(r"<[^>]+>", "", unescape(html)).strip()
