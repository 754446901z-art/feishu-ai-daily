"""Discover review candidates from public first-party sources, without a language model.

Extraction preserves source facts. It deliberately does not invent summaries,
prompts, dates, operating steps or automatic semantic verification.
"""

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from source_utils import (ArticleParser, canonical_url, fetch_article, fetch_text,
                          method_id, parse_publication_date, write_json)

SOURCES = (
    ("OpenAI Cookbook", "https://developers.openai.com/cookbook", "index"),
    ("Anthropic Engineering", "https://www.anthropic.com/engineering", "index"),
    ("Google Developers", "https://developers.googleblog.com/feeds/posts/default/", "feed"),
)
TOPICS = {
    "agents": ("agent", "multi-agent", "tool call", "function call", "orchestrat"),
    "context": ("context engineering", "context", "retrieval", "rag", "knowledge", "memory"),
    "prompting": ("prompt", "reasoning", "elicitation", "question", "reflection"),
    "workflows": ("workflow", "automat", "harness", "evaluation", "evals", "improvement", "practical"),
}
PRACTICE_WORDS = ("build", "how to", "guide", "pattern", "cookbook", "workflow", "engineering", "optim", "practice", "evaluat", "implement", "improv", "tool", "context")
NEWS_WORDS = ("announcing", "introducing", "now available", "launch", "anniversary", "conference", "join us", "award")


def relevance(title, text=""):
    sample = (title + " " + text[:5000]).lower()
    topics = [topic for topic, words in TOPICS.items() if any(word in sample for word in words)]
    practical = sum(word in sample for word in PRACTICE_WORDS)
    news = sum(word in title.lower() for word in NEWS_WORDS)
    return 3 * len(topics) + min(practical, 6) - news * 5, topics


class _IndexParser(ArticleParser):
    def __init__(self):
        super().__init__()
        self.recipe_dates = {}
        self.recipe_href = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self.recipe_href = attributes.get("href") if "recipe-row" in attributes.get("class", "").split() else None
        if (tag == "time" and self.recipe_href
                and "recipe-date" in attributes.get("class", "").split()
                and attributes.get("datetime")):
            self.recipe_dates[self.recipe_href] = attributes["datetime"]
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "a":
            self.recipe_href = None
        super().handle_endtag(tag)


def parse_index(content, base_url, today=None):
    parser = _IndexParser()
    parser.feed(content)
    today = today or datetime.now(timezone.utc).date()
    cookbook_index = base_url.rstrip("/") == "https://developers.openai.com/cookbook"
    dates = {}
    if cookbook_index:
        for href, raw_date in parser.recipe_dates.items():
            try:
                dates[canonical_url(urljoin(base_url, href))] = raw_date
            except ValueError:
                continue
    urls = []
    for href in parser.links:
        try:
            url = canonical_url(urljoin(base_url, href))
        except ValueError:
            continue
        if url not in urls:
            urls.append(url)
    results = []
    for url in urls:
        raw_date = dates.get(url)
        published = parse_publication_date(raw_date, today)
        result = {"url": url, "published_at": published,
                  "date_evidence": {"kind": "index:published", "value": raw_date,
                                    "url": "https://developers.openai.com/cookbook"} if published else None}
        if raw_date and not published:
            result["invalid_publication"] = True
        results.append(result)
    return results


def parse_feed(content, base_url, today=None):
    root = ET.fromstring(content)
    today = today or datetime.now(timezone.utc).date()
    entries = root.findall("./channel/item") or root.findall("{http://www.w3.org/2005/Atom}entry")
    results, seen = [], set()
    for entry in entries:
        link = entry.find("link")
        if link is None:
            link = entry.find("{http://www.w3.org/2005/Atom}link")
        raw_url = (link.text or link.get("href", "")) if link is not None else ""
        try:
            url = canonical_url(urljoin(base_url, raw_url))
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        # RSS pubDate is an actual publication field; lastBuildDate/updated are not.
        raw_date = entry.findtext("pubDate") or entry.findtext("{http://www.w3.org/2005/Atom}published")
        published = parse_publication_date(raw_date, today)
        if raw_date and not published:
            try:
                from email.utils import parsedate_to_datetime
                iso_date = parsedate_to_datetime(raw_date).date().isoformat()
                published = parse_publication_date(iso_date, today)
                raw_date = iso_date if published else None
            except (ValueError, TypeError, OverflowError):
                pass
        results.append({"url": url, "published_at": published,
                        "date_evidence": {"kind": "feed:published", "value": raw_date} if published else None})
    return results


def _snippets(text):
    # Exact contiguous extracts, not generated paraphrases. Avoid committing full pages.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    ranked = sorted(enumerate(sentences), key=lambda pair: (-relevance("", pair[1])[0], pair[0]))
    selected = sorted(ranked[:3])
    return [sentence[:240] for _, sentence in selected if sentence.strip()]


def candidate_from_article(article, source_name, now, listing=None):
    score, topics = relevance(article["title"], article["text"])
    published = article.get("published_at")
    provenance = article.get("date_evidence")
    if not published and listing and listing.get("published_at"):
        published, provenance = listing["published_at"], listing["date_evidence"]
    return {
        "id": method_id(article["url"]), "method_key": None, "status": "pending_review",
        "name": article["title"], "principle": None, "scenarios": [], "steps": [],
        "example_prompt": None,
        "source": {"name": source_name, "url": article["url"], "title": article["title"],
                   "published_at": published, "date_evidence": provenance},
        "discovered_at": now, "last_checked_at": now,
        "evidence": {"text_sha256": article["text_sha256"], "snippets": _snippets(article["text"])},
        "review": None, "topics": topics, "score": score,
    }


def merge_candidate(existing, candidate):
    if not existing:
        return candidate
    result = dict(existing)
    changed = (existing.get("evidence", {}).get("text_sha256") != candidate["evidence"]["text_sha256"]
               or existing.get("source", {}).get("published_at") != candidate["source"]["published_at"]
               or existing.get("source", {}).get("date_evidence") != candidate["source"]["date_evidence"])
    result.update({key: candidate[key] for key in ("source", "evidence", "last_checked_at", "topics", "score")})
    result.pop("source_error", None)
    if existing.get("status") == "verified" and changed:
        result["status"] = "pending_review"
        result["review"] = None
        result["review_reason"] = "source_content_or_publication_metadata_changed"
    return result


def candidate_sort_key(method, today):
    published = method.get("source", {}).get("published_at")
    age = (today - date.fromisoformat(published)).days if published else 99999
    return (0 if 0 <= age <= 7 else 1 if 0 <= age <= 90 else 2,
            -method.get("score", 0), age, method["id"])


def discover(library, today=None, fetcher=fetch_article, index_fetcher=fetch_text, per_source=10,
             time_budget=180, clock=time.monotonic):
    today = today or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    methods = {method["id"]: method for method in library.get("methods", [])}
    checked, errors = set(), []
    counts = {}
    deadline = clock() + time_budget
    budget_exhausted = False

    def within_budget():
        nonlocal budget_exhausted
        if clock() < deadline:
            return True
        if not budget_exhausted:
            errors.append({"source": "all", "url": None, "error": "BudgetExhausted"})
            budget_exhausted = True
        return False

    def update(url, name, listing=None):
        if not within_budget():
            return
        identifier = method_id(url)
        if identifier in checked:
            return
        checked.add(identifier)
        try:
            if listing and listing.get("invalid_publication"):
                raise ValueError("Invalid or future index publication date")
            article = fetcher(url)
            candidate = candidate_from_article(article, name, now, listing)
            if identifier not in methods:
                published = candidate["source"]["published_at"]
                age = (today - date.fromisoformat(published)).days if published else None
                if candidate["score"] < 4 or not candidate["topics"] or age is not None and not 0 <= age <= 90:
                    return
            methods[identifier] = merge_candidate(methods.get(identifier), candidate)
        except Exception as error:  # An unreachable source must not stop the other sources or fallback push.
            errors.append({"source": name, "url": url, "error": type(error).__name__})
            if identifier in methods:
                methods[identifier] = dict(methods[identifier], source_error=type(error).__name__)

    # Fetch indexes first: Cookbook publishes original dates in recipe rows rather
    # than article metadata. The date must be revalidated before refreshing approval.
    source_listings, listing_by_url = {}, {}
    for name, index_url, kind in SOURCES:
        if not within_budget():
            source_listings[name] = []
            continue
        try:
            content, final_url = index_fetcher(index_url)
            listings = parse_feed(content, final_url, today) if kind == "feed" else parse_index(content, final_url, today)
            listings.sort(key=lambda item: item.get("published_at") or "0000-00-00", reverse=True)
            source_listings[name] = listings
            listing_by_url.update({listing["url"]: listing for listing in listings})
            if not listings:
                errors.append({"source": name, "url": index_url, "error": "NoArticleLinks"})
        except Exception as error:
            source_listings[name] = []
            errors.append({"source": name, "url": index_url, "error": type(error).__name__})

    # Daily evidence revalidation is mandatory even if the article leaves an index.
    for method in list(methods.values()):
        if not within_budget():
            break
        if method.get("status") == "verified":
            try:
                url = method["source"]["url"]
                update(url, method["source"]["name"], listing_by_url.get(canonical_url(url)))
            except (KeyError, ValueError):
                method["status"] = "pending_review"
                method["review"] = None

    for name, index_url, kind in SOURCES:
        if not within_budget():
            counts[name] = 0
            continue
        before = len(checked)
        try:
            listings = source_listings.get(name, [])
            for listing in listings[:per_source]:
                if not within_budget():
                    break
                update(listing["url"], name, listing)
            counts[name] = len(checked) - before
        except Exception as error:
            errors.append({"source": name, "url": index_url, "error": type(error).__name__})
            counts[name] = 0
    result = dict(library)
    result.update(schema_version=1, methods=sorted(methods.values(), key=lambda method: candidate_sort_key(method, today)))
    result["discovery"] = {"checked_at": now, "source_checks": counts, "errors": errors,
                           "note": "Extracted candidates require human review; collection is not factual/semantic approval."}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("new_methods.json"))
    parser.add_argument("--per-source", type=int, default=10)
    parser.add_argument("--time-budget", type=int, default=180,
                        help="Wall-clock budget checked before each bounded (20-second timeout) request")
    args = parser.parse_args()
    if not 1 <= args.per_source <= 20:
        parser.error("--per-source must be between 1 and 20")
    if not 10 <= args.time_budget <= 600:
        parser.error("--time-budget must be between 10 and 600 seconds")
    try:
        library = json.loads(args.library.read_text("utf-8")) if args.library.exists() else {"schema_version": 1, "methods": []}
        if library.get("schema_version") != 1 or not isinstance(library.get("methods"), list):
            raise ValueError("Unsupported method library schema")
        result = discover(library, per_source=args.per_source, time_budget=args.time_budget)
        write_json(args.library, result)
    except (OSError, ValueError, TypeError) as error:
        print("Discovery did not change the library:", type(error).__name__, file=sys.stderr)
        return 1
    pending = sum(method.get("status") == "pending_review" for method in result["methods"])
    print(f"Discovery complete: {len(result['methods'])} stored, {pending} awaiting review, {len(result['discovery']['errors'])} source errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
