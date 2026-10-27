"""Small, dependency-free helpers for primary-source evidence.

No page is an instruction and no fetched text can approve itself for delivery.
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "Mozilla/5.0 (compatible; FeishuAIDaily/1.0; public-source-reader)"
MAX_BYTES = 2_500_000
ALLOWED_HOSTS = {
    "cookbook.openai.com", "developers.openai.com", "www.anthropic.com",
    "anthropic.com", "developers.googleblog.com",
}
FIELD_NAMES = ("name", "principle", "scenarios", "steps", "example_prompt")


def canonical_url(url):
    """Accept only actual article paths on the configured first-party sites."""
    parts = urlsplit(unescape(str(url).strip()))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in ALLOWED_HOSTS:
        raise ValueError("Source must be an allowlisted HTTPS primary source")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Unexpected source URL credentials or port")
    path = unquote(parts.path).rstrip("/")
    if any(piece in (".", "..") for piece in path.split("/")) or "\\" in path:
        raise ValueError("Invalid source path")
    if host == "cookbook.openai.com":
        host, path = "developers.openai.com", "/cookbook" + path
    if host == "anthropic.com":
        host = "www.anthropic.com"
    if host == "developers.googleblog.com" and path.startswith("/en/"):
        path = path[3:]
    valid = (
        host == "developers.openai.com"
        and re.match(r"^/cookbook/(examples|articles)/[^\s?#]+$", path)
        or host == "www.anthropic.com"
        and re.match(r"^/(engineering|research)/[^/\s?#]+$", path)
        or host == "developers.googleblog.com"
        and re.match(r"^/[^/\s?#]+-[^/\s?#]+$", path)
    )
    if not valid:
        raise ValueError("URL is not a supported technical article")
    return urlunsplit(("https", host, path, "", ""))


def method_id(url):
    return "source_" + hashlib.sha256(canonical_url(url).encode()).hexdigest()[:20]


def write_json(path, value):
    """Replace one JSON file atomically without exposing truncated progress."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _network_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError("Fetch/redirect outside primary-source allowlist")
    return url


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _network_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(url):
    _network_url(url)
    opener = build_opener(_SafeRedirect())
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml"})
    with opener.open(request, timeout=20) as response:
        _network_url(response.url)
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Source response exceeds size limit")
        return raw.decode(response.headers.get_content_charset() or "utf-8"), response.url


def normalized_text(text):
    return re.sub(r"\s+", " ", unescape(text)).strip()


class ArticleParser(HTMLParser):
    """Read visible article text and explicit publication metadata only."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.visible = []
        self.main = []
        self.article = []
        self.content = []
        self._content_depth = 0
        self.titles = []
        self.h1 = []
        self.meta = {}
        self.jsonld = []
        self._script = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            key = attributes.get("property", attributes.get("name", "")).lower()
            self.meta[key] = attributes.get("content", "")
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "script":
            self._script = [] if attributes.get("type") == "application/ld+json" else None
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append(tag)
            # Google Developers uses this article-body section instead of semantic
            # article/main tags. Related posts and changing topic links live outside.
            if tag == "section" and "blocks-container" in attributes.get("class", "").split():
                self._content_depth = len(self.stack)

    def handle_endtag(self, tag):
        if tag == "script":
            if self._script is not None:
                self.jsonld.append("".join(self._script))
            self._script = None
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]
            if len(self.stack) < self._content_depth:
                self._content_depth = 0

    def handle_data(self, data):
        if "script" in self.stack:
            if self._script is not None:
                self._script.append(data)
            return
        if "title" in self.stack:
            self.titles.append(data)
        if "h1" in self.stack:
            self.h1.append(data)
        if not any(tag in self.stack for tag in ("script", "style", "noscript", "nav", "footer", "header", "head", "svg", "button")):
            self.visible.append(data)
            if self._content_depth:
                self.content.append(data)
            if "main" in self.stack:
                self.main.append(data)
            if "article" in self.stack:
                self.article.append(data)

    def result(self, url, today=None):
        today = today or datetime.now(timezone.utc).date()
        published, evidence = None, None
        raw_date = self.meta.get("article:published_time")
        if raw_date:
            published = parse_publication_date(raw_date, today)
            if published:
                evidence = {"kind": "article:published_time", "value": raw_date}
            else:
                raise ValueError("Invalid or future explicit publication date")
        for raw_json in self.jsonld:
            try:
                values = list(_walk_dicts(json.loads(raw_json)))
            except (ValueError, TypeError):
                continue
            for item in values:
                # A breadcrumb, author profile or unrelated embedded item cannot date this article.
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if not set(types).intersection({"Article", "BlogPosting", "TechArticle", "NewsArticle", "WebPage"}):
                    continue
                raw_date = item.get("datePublished")
                candidate = parse_publication_date(raw_date, today)
                if raw_date and not candidate:
                    raise ValueError("Invalid or future explicit publication date")
                if candidate and not published:
                    published = candidate
                    evidence = {"kind": "datePublished", "value": raw_date}
        title = normalized_text(" ".join(self.h1) or self.meta.get("og:title") or " ".join(self.titles))
        text = normalized_text(" ".join(self.content or self.article or self.main or self.visible))
        if not title or len(text) < 80:
            raise ValueError("No substantive source article/title found")
        return {"url": canonical_url(url), "title": title, "text": text,
                "published_at": published, "date_evidence": evidence,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def parse_publication_date(value, today=None):
    """Never substitute dateModified, download time, HTTP Last-Modified or a URL year."""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T)", value):
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    today = today or datetime.now(timezone.utc).date()
    return parsed.isoformat() if date(2000, 1, 1) <= parsed <= today else None


def fetch_article(url):
    canonical_url(url)
    content, final_url = fetch_text(url)
    parser = ArticleParser()
    parser.feed(content)
    result = parser.result(final_url)
    if canonical_url(final_url) != canonical_url(url):
        raise ValueError("Article redirected to a different identity; requires rediscovery")
    return result


def validate_verified(method, today=None):
    """Offline admission gate. Semantic faithfulness still needs the named human review."""
    today = today or datetime.now(timezone.utc).date()
    if isinstance(today, datetime):
        today = today.date()
    if method.get("status") != "verified":
        raise ValueError("Method is not reviewed")
    source = method.get("source") or {}
    if method.get("id") != method_id(source.get("url", "")):
        raise ValueError("Source identity mismatch")
    if not isinstance(method.get("method_key"), str) or not method["method_key"].strip():
        raise ValueError("Stable method_key is required for concept deduplication")
    for key in ("name", "principle", "example_prompt"):
        if not isinstance(method.get(key), str) or not method[key].strip():
            raise ValueError("Missing reviewed field: " + key)
        if len(method[key]) > 1500:
            raise ValueError("Reviewed field too long: " + key)
    for key in ("scenarios", "steps"):
        value = method.get(key)
        if not isinstance(value, list) or not value or len(value) > 8:
            raise ValueError("Missing reviewed list: " + key)
        if any(not isinstance(item, str) or not item.strip() or len(item) > 600 for item in value):
            raise ValueError("Invalid reviewed list item: " + key)
    if not source.get("name") or not source.get("title"):
        raise ValueError("Source attribution required")
    published = source.get("published_at")
    date_evidence = source.get("date_evidence")
    if published is not None:
        if parse_publication_date(published, today) != published or not isinstance(date_evidence, dict):
            raise ValueError("Invalid publication provenance")
        if date_evidence.get("kind") not in ("datePublished", "article:published_time", "feed:published", "index:published"):
            raise ValueError("Unsupported publication evidence")
        if date_evidence.get("kind") == "index:published" and (
                date_evidence.get("url") != "https://developers.openai.com/cookbook"
                or not canonical_url(source["url"]).startswith("https://developers.openai.com/cookbook/")):
            raise ValueError("Unsupported publication index")
        if parse_publication_date(date_evidence.get("value"), today) != published:
            raise ValueError("Publication date/evidence mismatch")
    elif date_evidence is not None:
        raise ValueError("Publication evidence without a publication date")
    evidence = method.get("evidence") or {}
    review = method.get("review") or {}
    digest = evidence.get("text_sha256", "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or review.get("content_sha256") != digest:
        raise ValueError("Approval is not bound to this source snapshot")
    if not review.get("reviewer") or not review.get("reviewed_at"):
        raise ValueError("Named review and review time required")
    try:
        timestamp = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        if timestamp.date() > today:
            raise ValueError("Future review time")
    except (TypeError, AttributeError, ValueError) as error:
        raise ValueError("Invalid review timestamp") from error
    quotes = review.get("field_evidence")
    if not isinstance(quotes, dict):
        raise ValueError("Field-by-field exact source evidence required")
    for field in FIELD_NAMES:
        quote = quotes.get(field)
        if not isinstance(quote, str) or not 10 <= len(quote.strip()) <= 500:
            raise ValueError("Missing/oversized exact evidence for " + field)
    if method.get("duplicate_of"):
        raise ValueError("Duplicate method cannot be sent")
    return True
