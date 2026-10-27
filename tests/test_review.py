from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

import review
import send
from source_utils import method_id


URL = "https://www.anthropic.com/engineering/example"
QUOTE = "Use an explicit workflow and verify the result."
HASH = hashlib.sha256(QUOTE.encode()).hexdigest()


def fixture():
    published = datetime.now(timezone.utc).date().isoformat()
    source = {"url": URL, "title": "Example workflow", "name": "Anthropic Engineering",
              "published_at": published, "date_evidence": {"kind": "datePublished", "value": published}}
    draft = {"id": method_id(URL), "status": "pending_review", "method_key": "workflow_example",
             "name": "示例方法", "principle": "显式工作流与核验", "scenarios": ["工作实践"],
             "steps": ["定义工作流", "检查输出"], "example_prompt": "定义步骤并检查输出",
             "source": source, "evidence": {"text_sha256": HASH, "snippets": [QUOTE]},
             "review": {"reviewer": "test-only reviewer", "reviewed_at": "", "content_sha256": HASH,
                        "field_evidence": {k: QUOTE for k in ("name", "principle", "scenarios", "steps", "example_prompt")}}}
    article = dict(source, text=QUOTE, text_sha256=HASH)
    return draft, article


class ReviewTests(unittest.TestCase):
    def test_exact_evidence_promotes_and_sender_accepts_shape(self):
        draft, article = fixture()
        verified = review.approve(draft, {"methods": [draft]}, lambda url: article)
        self.assertEqual(verified["status"], "verified")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            send.write_json(path, {"schema_version": 1, "methods": [verified]})
            self.assertEqual(len(send.load_verified(path)), 1)
        chosen = send.choose_methods(send.migrate_state({"next_index": 3}), [verified], article["published_at"])
        self.assertEqual(chosen[0]["id"], draft["id"])

    def test_nonexistent_quote_never_approves(self):
        draft, article = fixture()
        draft["review"]["field_evidence"]["steps"] = "An invented operation that the article never describes."
        with self.assertRaises(ValueError):
            review.approve(draft, {"methods": [draft]}, lambda url: article)

    def test_changed_snapshot_requires_review_again(self):
        draft, article = fixture()
        article["text_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            review.approve(draft, {"methods": [draft]}, lambda url: article)

    def test_unknown_publication_stays_candidate(self):
        draft, article = fixture()
        article["published_at"] = None
        with self.assertRaises(ValueError):
            review.approve(draft, {"methods": [draft]}, lambda url: article)

    def test_approval_does_not_mutate_original_candidate(self):
        draft, article = fixture()
        original = deepcopy(draft)
        review.approve(draft, {"methods": [draft]}, lambda url: article)
        self.assertEqual(draft, original)

    def test_successful_refetch_clears_previous_source_error(self):
        draft, article = fixture()
        draft["source_error"] = "TimeoutError"
        verified = review.approve(draft, {"methods": [draft]}, lambda url: article)
        self.assertNotIn("source_error", verified)

    def test_cookbook_index_publication_is_rechecked(self):
        draft, article = fixture()
        url = "https://developers.openai.com/cookbook/examples/reviewed_workflow"
        index_url = "https://developers.openai.com/cookbook"
        draft["id"] = method_id(url)
        draft["source"]["url"] = url
        draft["source"]["date_evidence"] = {"kind": "index:published", "value": article["published_at"], "url": index_url}
        article.update(url=url, published_at=None, date_evidence=None)
        html = (f'<a class="recipe-row" href="{url}"><time class="recipe-date" datetime="'
                + draft["source"]["published_at"] + '">Published</time></a>')
        verified = review.approve(draft, {"methods": [draft]}, lambda url: article,
                                  lambda url: (html, index_url))
        self.assertEqual(verified["source"]["date_evidence"]["kind"], "index:published")
        with self.assertRaises(ValueError):
            review.approve(draft, {"methods": [draft]}, lambda url: article,
                           lambda url: ("<p>No such item</p>", index_url))
