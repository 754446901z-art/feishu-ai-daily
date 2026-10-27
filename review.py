"""Human review gate: no generated claims are automatically approved."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from discover import parse_index
from source_utils import canonical_url, fetch_article, fetch_text, validate_verified, write_json

ROOT = Path(__file__).resolve().parent
LIBRARY = ROOT / "new_methods.json"


def approve(draft, library, fetcher=fetch_article, index_fetcher=fetch_text):
    candidate = next((m for m in library["methods"] if m["id"] == draft["id"]), None)
    if candidate is None:
        raise ValueError("找不到候选 ID")
    if candidate["source"]["url"] != draft["source"]["url"]:
        raise ValueError("不允许用其他来源替换候选")
    article = fetcher(draft["source"]["url"])
    provenance = draft["source"].get("date_evidence") or {}
    if not article["published_at"] and provenance.get("kind") == "index:published":
        # Cookbook sometimes publishes a date only in the official recipe list.
        # Re-read the exact list item instead of trusting a manually entered date.
        index_url = "https://developers.openai.com/cookbook"
        if provenance.get("url") != index_url:
            raise ValueError("目录日期必须来自官方 Cookbook 目录")
        content, final_url = index_fetcher(index_url)
        listing = next((row for row in parse_index(content, final_url)
                        if row["url"] == canonical_url(article["url"])), None)
        if not listing or listing["published_at"] != draft["source"]["published_at"] or listing["date_evidence"] != provenance:
            raise ValueError("无法在官方目录核实文章的原始发布日期")
        article = dict(article, published_at=listing["published_at"], date_evidence=listing["date_evidence"])
    if not article["published_at"]:
        raise ValueError("原文未能核实发布日期，继续留在待审核库")
    now = datetime.now(timezone.utc).isoformat()
    method = deepcopy(draft)
    if method["review"]["content_sha256"] != article["text_sha256"]:
        raise ValueError("原文已变化，请重新对照原文审核并更新 content_sha256")
    for quote in method["review"]["field_evidence"].values():
        if not isinstance(quote, str) or quote not in article["text"]:
            raise ValueError("字段证据不是当前原文中的逐字摘录")
    method["source"].update({k: article[k] for k in ("url", "title", "published_at", "date_evidence")})
    method["evidence"] = {"text_sha256": article["text_sha256"],
                          "snippets": candidate.get("evidence", {}).get("snippets", [])}
    method["last_checked_at"] = now
    method.pop("source_error", None)
    method["review"]["reviewed_at"] = now
    method["status"] = "verified"
    validate_verified(method)
    return method


def main():
    parser = argparse.ArgumentParser(description="人工对照原文后审核；程序只核对证据，不代替人的语义判断")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--template", metavar="CANDIDATE_ID")
    modes.add_argument("--approve", type=Path, metavar="DRAFT_JSON")
    parser.add_argument("--output", type=Path, default=ROOT / "review_draft.json")
    args = parser.parse_args()
    library = json.loads(LIBRARY.read_text("utf-8"))
    if args.template:
        method = deepcopy(next(m for m in library["methods"] if m["id"] == args.template))
        method["name"] = method.get("name") or method.get("title", "")
        method["method_key"] = method.get("method_key") or method["id"]
        method["review"] = {"reviewer": "", "reviewed_at": "",
                            "content_sha256": method["evidence"]["text_sha256"],
                            "field_evidence": {k: "" for k in ("name", "principle", "scenarios", "steps", "example_prompt")}}
        write_json(args.output, method)
        print("已生成审核草稿。填写各字段和逐字证据，核对是否与旧方法同义后再 --approve。")
    else:
        draft = json.loads(args.approve.read_text("utf-8"))
        method = approve(draft, library)
        library["methods"] = [method if m["id"] == method["id"] else m for m in library["methods"]]
        write_json(LIBRARY, library)
        print("已通过原文哈希和逐字证据校验；请审查并提交 new_methods.json。")


if __name__ == "__main__":
    main()
