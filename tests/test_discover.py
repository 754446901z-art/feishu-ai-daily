import copy
import hashlib
import itertools
import unittest
from datetime import date
from unittest.mock import patch

from discover import (candidate_from_article, candidate_sort_key, discover,
                      merge_candidate, parse_feed, parse_index, relevance)
from source_utils import (ArticleParser, canonical_url, method_id,
                          parse_publication_date, validate_verified)

URL = "https://www.anthropic.com/engineering/context-patterns"
TEXT = "Build an agent workflow with context engineering. " * 12


def article(url=URL, published="2026-10-25"):
    return {"url": url, "title": "Build effective agent workflows", "text": TEXT,
            "published_at": published,
            "date_evidence": {"kind": "datePublished", "value": published} if published else None,
            "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest()}


class SourceTests(unittest.TestCase):
    def test_legacy_cookbook_identity_and_tracking(self):
        before = "https://cookbook.openai.com/examples/agents_sdk/sample?utm_source=x#intro"
        after = "https://developers.openai.com/cookbook/examples/agents_sdk/sample/"
        self.assertEqual(canonical_url(before), canonical_url(after))
        self.assertEqual(method_id(before), method_id(after))

    def test_allowlist_is_host_and_path_specific(self):
        for bad in ("https://www.anthropic.com.evil.test/engineering/x", "http://www.anthropic.com/engineering/x",
                    "https://www.anthropic.com/news/x", "https://user@www.anthropic.com/engineering/x",
                    "https://www.anthropic.com/engineering/../x", "https://developers.openai.com/api/docs/x"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                canonical_url(bad)

    def test_explicit_publication_date_and_article_text(self):
        html = '''<html><head><title>Title</title>
          <meta property="article:modified_time" content="2026-10-26">
          <script type="application/ld+json">{"@type":"Article","datePublished":"2026-10-21T00:00:00Z"}</script>
          </head><body><nav>Ignore navigation</nav><main><h1>Build agents</h1>
          <article><p>''' + TEXT + '''</p><script>evil instruction</script></article></main></body></html>'''
        parser = ArticleParser()
        parser.feed(html)
        parsed = parser.result(URL, date(2026, 10, 27))
        self.assertEqual(parsed["published_at"], "2026-10-21")
        self.assertEqual(parsed["date_evidence"]["kind"], "datePublished")
        self.assertNotIn("Ignore navigation", parsed["text"])
        self.assertNotIn("evil instruction", parsed["text"])

    def test_modified_dates_are_not_publication_dates(self):
        parser = ArticleParser()
        parser.feed('<meta property="article:modified_time" content="2026-10-26"><h1>Agents</h1><article>' + TEXT + '</article>')
        self.assertIsNone(parser.result(URL)["published_at"])
        self.assertIsNone(parse_publication_date("2039-01-01", date(2026, 10, 27)))
        self.assertIsNone(parse_publication_date("2026-02-30", date(2026, 10, 27)))

    def test_future_publication_is_rejected_not_reclassified_unknown(self):
        parser = ArticleParser()
        parser.feed('<meta property="article:published_time" content="2039-01-01"><h1>Agents</h1><article>' + TEXT + '</article>')
        with self.assertRaises(ValueError):
            parser.result(URL, date(2026, 10, 27))

    def test_google_localized_and_feed_link_identity(self):
        self.assertEqual(canonical_url("https://developers.googleblog.com/en/build-ai-agents/"),
                         canonical_url("https://developers.googleblog.com/build-ai-agents/"))

    def test_google_article_body_excludes_changing_related_posts(self):
        html = ('<h1>Agent workflows</h1><section class="blocks-container glue-page"><div><p>'
                + TEXT + '</p></div></section><section class="related-posts-container">'
                'Related Posts New unrelated announcement</section>')
        parser = ArticleParser()
        parser.feed(html)
        first = parser.result("https://developers.googleblog.com/build-ai-agents/")
        parser = ArticleParser()
        parser.feed(html.replace("New unrelated announcement", "A different announcement"))
        second = parser.result("https://developers.googleblog.com/build-ai-agents/")
        self.assertNotIn("Related Posts", first["text"])
        self.assertEqual(first["text_sha256"], second["text_sha256"])

    def test_feed_uses_published_not_updated_and_dedupes(self):
        feed = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <link href="https://developers.googleblog.com/en/build-agents/?tracking=yes"/>
          <published>2026-10-24T00:00:00Z</published><updated>2026-10-26T00:00:00Z</updated></entry>
          <entry><link href="https://developers.googleblog.com/en/build-agents/"/></entry>
          <entry><link href="https://evil.test/path"/><published>2026-10-24</published></entry></feed>'''
        rows = parse_feed(feed, "https://developers.googleblog.com/", date(2026, 10, 27))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["published_at"], "2026-10-24")
        self.assertEqual(rows[0]["date_evidence"]["kind"], "feed:published")

    def test_index_discards_unrelated_links(self):
        rows = parse_index('<a href="/research/context-patterns">x</a><a href="/news/model">n</a><a href="https://evil.test/engineering/x">e</a>', "https://www.anthropic.com/engineering")
        self.assertEqual([row["url"] for row in rows], ["https://www.anthropic.com/research/context-patterns"])

    def test_cookbook_recipe_row_dates_are_bound_to_the_matching_article(self):
        html = '''<a href="/cookbook/examples/old">Old feature</a>
          <a class="recipe-row" href="/cookbook/examples/new"><span>New method</span>
          <time class="recipe-date" datetime="2026-10-25">Oct 25</time></a>
          <a href="/cookbook/examples/unrelated"><time datetime="2026-10-26">Other date</time></a>'''
        rows = parse_index(html, "https://developers.openai.com/cookbook", date(2026, 10, 27))
        by_url = {row["url"]: row for row in rows}
        new = by_url["https://developers.openai.com/cookbook/examples/new"]
        self.assertEqual(new["published_at"], "2026-10-25")
        self.assertEqual(new["date_evidence"], {"kind": "index:published", "value": "2026-10-25", "url": "https://developers.openai.com/cookbook"})
        self.assertIsNone(by_url["https://developers.openai.com/cookbook/examples/unrelated"]["published_at"])


class DiscoveryTests(unittest.TestCase):
    def test_no_invented_method_fields_and_source_exact_snippets(self):
        source = article()
        row = candidate_from_article(source, "Test", "2026-10-27T00:00:00+00:00")
        self.assertEqual(row["status"], "pending_review")
        self.assertIsNone(row["principle"])
        self.assertEqual(row["steps"], [])
        self.assertIsNone(row["example_prompt"])
        self.assertIsNone(row["review"])
        self.assertTrue(all(quote in source["text"] for quote in row["evidence"]["snippets"]))

    def test_refresh_preserves_review_and_invalidates_changed_source(self):
        candidate = candidate_from_article(article(), "Test", "2026-10-27T00:00:00+00:00")
        old = copy.deepcopy(candidate)
        old.update(status="verified", principle="Reviewed principle", review={"reviewer": "Human"})
        same = merge_candidate(old, candidate)
        self.assertEqual(same["status"], "verified")
        candidate["evidence"]["text_sha256"] = "a" * 64
        changed = merge_candidate(old, candidate)
        self.assertEqual(changed["status"], "pending_review")
        self.assertIsNone(changed["review"])
        self.assertEqual(changed["principle"], "Reviewed principle")

    def test_recent_sort_prefers_seven_days_over_higher_score(self):
        new = candidate_from_article(article(), "Test", "now")
        older = candidate_from_article(article("https://www.anthropic.com/engineering/older", "2026-09-15"), "Test", "now")
        older["score"] = 100
        self.assertLess(candidate_sort_key(new, date(2026, 10, 27)), candidate_sort_key(older, date(2026, 10, 27)))

    def test_failures_isolated_and_repeated_discovery_deduplicates(self):
        sources = (("Test", "https://www.anthropic.com/engineering", "index"),
                   ("Failed", "https://developers.googleblog.com/feeds/posts/default/", "feed"))
        def index_fetcher(url):
            if "googleblog" in url:
                raise TimeoutError()
            return '<a href="' + URL + '">Build agent workflows</a><a href="' + URL + '?x=y">same</a>', url
        with patch("discover.SOURCES", sources):
            first = discover({"methods": []}, date(2026, 10, 27), lambda url: article(url), index_fetcher)
            second = discover(first, date(2026, 10, 27), lambda url: article(url), index_fetcher)
        self.assertEqual(len(second["methods"]), 1)
        self.assertEqual(second["discovery"]["errors"][0]["error"], "TimeoutError")
        self.assertEqual(second["methods"][0]["discovered_at"], first["methods"][0]["discovered_at"])

    def test_revalidate_verified_even_when_missing_from_index(self):
        old = candidate_from_article(article(), "Test", "2026-10-25T00:00:00+00:00")
        old["status"] = "verified"
        changed = article()
        changed["text_sha256"] = "a" * 64
        with patch("discover.SOURCES", ()):
            result = discover({"methods": [old]}, date(2026, 10, 27), lambda url: changed)
        self.assertEqual(result["methods"][0]["status"], "pending_review")

    def test_discovery_budget_preserves_library_and_does_not_start_requests(self):
        old = candidate_from_article(article(), "Test", "2026-10-25T00:00:00+00:00")
        calls = []
        ticks = itertools.chain([0], itertools.repeat(180))
        result = discover({"methods": [old]}, date(2026, 10, 27),
                          lambda url: calls.append(url), lambda url: calls.append(url),
                          clock=lambda: next(ticks))
        self.assertEqual(calls, [])
        self.assertEqual(result["methods"], [old])
        self.assertEqual(result["discovery"]["errors"][0]["error"], "BudgetExhausted")

    def test_verified_cookbook_index_date_is_revalidated_before_refresh(self):
        url = "https://developers.openai.com/cookbook/examples/context-patterns"
        raw_article = article(url, None)
        listing = {"published_at": "2026-10-25", "date_evidence": {"kind": "index:published", "value": "2026-10-25", "url": "https://developers.openai.com/cookbook"}}
        old = candidate_from_article(raw_article, "OpenAI Cookbook", "before", listing)
        old["status"] = "verified"
        old["review"] = {"reviewer": "Human"}
        sources = (("OpenAI Cookbook", "https://developers.openai.com/cookbook", "index"),)
        html = '<a class="recipe-row" href="/cookbook/examples/context-patterns"><time class="recipe-date" datetime="2026-10-25">date</time></a>'
        with patch("discover.SOURCES", sources):
            same = discover({"methods": [old]}, date(2026, 10, 27), lambda url: raw_article, lambda url: (html, url))
            changed = discover({"methods": [old]}, date(2026, 10, 27), lambda url: raw_article, lambda url: (html.replace("2026-10-25", "2026-10-24"), url))
            missing = discover({"methods": [old]}, date(2026, 10, 27), lambda url: raw_article, lambda url: ("<h1>No recipe row</h1>", url))
        self.assertEqual(same["methods"][0]["status"], "verified")
        self.assertEqual(changed["methods"][0]["status"], "pending_review")
        self.assertEqual(missing["methods"][0]["status"], "pending_review")
        self.assertIsNone(missing["methods"][0]["source"]["published_at"])

    def test_offline_verified_gate_binds_review_to_snapshot(self):
        row = candidate_from_article(article(), "Test", "2026-10-27T00:00:00+00:00")
        row.update(status="verified", method_key="context-patterns", principle="Use explicit context", scenarios=["Work"],
                   steps=["Collect context"], example_prompt="Review my context")
        row["review"] = {"reviewer": "Human", "reviewed_at": "2026-10-27T00:00:00+00:00",
                         "content_sha256": row["evidence"]["text_sha256"],
                         "field_evidence": {key: "Build an agent workflow with context engineering." for key in ("name", "principle", "scenarios", "steps", "example_prompt")}}
        self.assertTrue(validate_verified(row, date(2026, 10, 27)))
        row["review"]["content_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            validate_verified(row, date(2026, 10, 27))


if __name__ == "__main__":
    unittest.main()
