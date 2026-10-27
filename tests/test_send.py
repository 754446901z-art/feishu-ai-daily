import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import send


TODAY = "2026-10-28"


def state(index=3):
    return send.migrate_state({"next_index": index, "last_sent": "2026-10-27"})


def method(identifier="source_one", key="concept_one", name="已审核方法", published=TODAY):
    return {"id": identifier, "method_key": key, "name": name, "principle": "原文支持的原理",
            "scenarios": ["工作研究"], "steps": ["打开资料", "检查结果"],
            "example_prompt": "请基于资料说明依据", "source": {"name": "Official",
            "url": "https://www.anthropic.com/engineering/example", "published_at": published}}


class SelectionTests(unittest.TestCase):
    def test_original_42_methods_preserved(self):
        self.assertEqual(len(send.METHODS), 42)
        self.assertEqual(len({send.builtin_id(i) for i in range(42)}), 42)

    def test_migration_keeps_date_and_first_three_ids(self):
        migrated = state()
        self.assertEqual(migrated["last_sent"], "2026-10-27")
        self.assertEqual(set(migrated["sent_ids"]), {send.builtin_id(i) for i in range(3)})
        self.assertIsNone(send.build_pending(migrated, [], "2026-10-27"))

    def test_legacy_later_round_does_not_repeat_concepts(self):
        migrated = state(45)
        self.assertEqual(len(migrated["sent_ids"]), 42)
        self.assertEqual(send.choose_methods(migrated, [], TODAY), [])

    def test_rejects_corrupt_or_future_state(self):
        for raw in ({"next_index": "3"}, {"next_index": -1}, {"schema_version": 3}, []):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                send.migrate_state(raw)

    def test_verified_first_then_unseen_builtins(self):
        chosen = send.choose_methods(state(), [method()], TODAY)
        self.assertEqual([x["kind"] for x in chosen], ["verified", "builtin", "builtin"])
        self.assertEqual(chosen[1]["id"], send.builtin_id(3))
        self.assertIn("非最新发布", chosen[1]["card"])

    def test_semantic_key_and_normalized_name_dedupe(self):
        variants = [method(key=send.builtin_id(0)),
                    method("source_two", "concept_two", send.METHODS[1][0]),
                    method("source_three", "concept_three"),
                    method("source_four", "concept_three", "同一种方法的另一标题")]
        chosen = send.choose_methods(state(), variants, TODAY)
        self.assertEqual([x["kind"] for x in chosen], ["verified", "builtin", "builtin"])
        self.assertEqual(chosen[0]["id"], "source_three")

    def test_fewer_than_three_and_exhaustion_never_recycle(self):
        pending = send.build_pending(state(41), [], TODAY)
        self.assertEqual(len(pending["items"]), 1)
        self.assertIn("不足3条", pending["message"])
        self.assertIsNone(send.build_pending(state(42), [], TODAY))

    def test_current_pending_is_immutable_on_rerun(self):
        current = state()
        current["pending"] = send.build_pending(current, [], TODAY)
        self.assertEqual(send.build_pending(current, [method()], TODAY), current["pending"])

    def test_missing_or_corrupt_library_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            self.assertEqual(send.load_verified(path), [])
            path.write_text("{bad", encoding="utf-8")
            self.assertEqual(send.load_verified(path), [])

    def test_stale_unknown_future_and_pending_not_sent(self):
        now = datetime(2026, 10, 28, 2, tzinfo=timezone.utc)
        rows = []
        for published, status, checked in [(None, "verified", now.isoformat()),
                                            ("2026-10-29", "verified", now.isoformat()),
                                            (TODAY, "verified", "2026-10-20T00:00:00Z"),
                                            (TODAY, "pending_review", now.isoformat())]:
            row = method(published=published)
            row.update(status=status, last_checked_at=checked)
            rows.append(row)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            path.write_text(json.dumps({"schema_version": 1, "methods": rows}), encoding="utf-8")
            with patch("send.validate_verified"):
                self.assertEqual(send.load_verified(path, now), [])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.current = state()
        self.current["pending"] = send.build_pending(self.current, [], TODAY)

    def tearDown(self):
        self.temp.cleanup()

    def test_receipt_updates_ids_and_same_day_skips(self):
        def sender(message, key, checkpoint):
            checkpoint()
            self.assertEqual(json.loads(self.path.read_text("utf-8"))["pending"]["status"], "attempted")
            return "om_receipt"
        send.deliver(self.current, TODAY, self.path, sender)
        saved = send.load_state(self.path)
        self.assertEqual(len(saved["sent_ids"]), 6)
        self.assertEqual(saved["deliveries"][0]["message_id"], "om_receipt")
        self.assertIsNone(send.build_pending(saved, [], TODAY))
        later = send.build_pending(saved, [], "2026-10-29")
        self.assertTrue(set(x["id"] for x in later["items"]).isdisjoint(saved["sent_ids"]))

    def test_timeout_after_attempt_blocks_even_on_later_day(self):
        def sender(message, key, checkpoint):
            checkpoint()
            raise TimeoutError("ambiguous")
        with self.assertRaises(TimeoutError):
            send.deliver(self.current, TODAY, self.path, sender)
        saved = send.load_state(self.path)
        self.assertEqual(saved["pending"]["status"], "unknown")
        with self.assertRaises(RuntimeError):
            send.build_pending(saved, [], "2026-10-29")
        with self.assertRaises(RuntimeError):
            send.deliver(saved, TODAY, self.path, sender)

    def test_auth_failure_remains_safe_prepared(self):
        def sender(*args):
            raise RuntimeError("auth failed before message API")
        with self.assertRaises(RuntimeError):
            send.deliver(self.current, TODAY, self.path, sender)
        self.assertEqual(self.current["pending"]["status"], "prepared")

    def test_durable_checkpoint_failure_prevents_message(self):
        requests = []
        def sender(message, key, checkpoint):
            checkpoint()
            requests.append(message)
            return "receipt"
        with patch("send.subprocess.run", side_effect=RuntimeError("push failed")):
            with self.assertRaises(RuntimeError):
                send.deliver(self.current, TODAY, self.path, sender, durable=True)
        self.assertEqual(requests, [])
        self.assertEqual(send.load_state(self.path)["pending"]["status"], "unknown")

    def test_expired_prepared_batch_not_delivered(self):
        with self.assertRaises(RuntimeError):
            send.deliver(self.current, "2026-10-29", self.path, lambda *a: self.fail("sent"))

    def test_existing_feishu_protocol_and_stable_uuid(self):
        with patch.dict("os.environ", {"FEISHU_APP_ID": "test-id", "FEISHU_APP_SECRET": "test-secret",
                                       "FEISHU_OPEN_ID": "ou_test"}), patch("send.request_json") as request:
            request.side_effect = [{"code": 0, "tenant_access_token": "test-token"},
                                   {"code": 0, "data": {"message_id": "om_test"}}]
            checkpoints = []
            self.assertEqual(send.send_to_feishu("text", "daily-key", lambda: checkpoints.append(True)), "om_test")
            message_call = request.call_args_list[1]
            self.assertIn("receive_id_type=open_id", message_call.args[0])
            self.assertEqual(message_call.args[1]["uuid"], str(send.uuid.uuid5(send.uuid.NAMESPACE_URL, "daily-key")))
            self.assertEqual(checkpoints, [True])


if __name__ == "__main__":
    unittest.main()
