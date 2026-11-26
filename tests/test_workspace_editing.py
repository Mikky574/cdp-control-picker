"""Annotation overlays and page order use isolated files; no GUI or browser."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import Workspace, WorkspaceError


PAGE = "https://example.test/orders"


class WorkspaceEditingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="picker-edits-")
        self.root = Path(self.temporary.name) / "workspace"
        self.skill = self.root / ".capture-kit" / "cdp-page-contract" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("Fixture page rules.", encoding="utf-8")
        self.workspace = self.fresh_workspace()

    def tearDown(self):
        self.temporary.cleanup()

    def fresh_workspace(self):
        return Workspace(self.root, skill_path=self.skill)

    def capture(self, capture_id, *, second=1, page=PAGE):
        return self.workspace.save_capture({
            "createdAt": f"2026-11-26T00:00:{second:02d}+00:00",
            "page": {"url": page, "frameChain": [], "title": "Orders"},
            "control": {"tagName": "button", "name": "Original DOM evidence", "role": {"value": "button", "source": "cdp-accessibility"},
                        "selectors": [{"strategy": "css", "value": "#" + capture_id, "matchCount": 1}]},
            "annotation": {"name": capture_id, "description": "Original purpose", "allowedActions": ["read"]},
        }, capture_id=capture_id)

    @staticmethod
    def ids(records):
        return [record["captureId"] for record in records]

    def bundle(self, **kwargs):
        exported = self.workspace.export_page_bundle(PAGE, **kwargs)
        return exported, json.loads(Path(exported["bundlePath"]).read_text(encoding="utf-8"))

    def test_default_list_uses_capture_time_and_canonical_page_without_claiming_workflow(self):
        self.capture("cap-a", second=3)
        self.capture("cap-z", second=1, page=PAGE + "?view=2#row")
        self.capture("cap-m", second=2)
        self.capture("cap-other-path", page="https://example.test/users")
        self.capture("cap-other-host", page="https://other.test/orders")
        expected = ["cap-z", "cap-m", "cap-a"]
        self.assertEqual(self.ids(self.workspace.list_page_captures(PAGE)), expected)
        self.assertEqual(self.workspace.page_sequence(PAGE), {"captureIds": expected, "ordered": False})
        _, bundle = self.bundle()
        self.assertEqual(bundle["sequence"]["captureIds"], expected)
        self.assertEqual(bundle["sequence"]["source"], "capture-order")
        self.assertFalse(bundle["sequence"]["ordered"])
        self.assertFalse(bundle["sequence"]["executionValidated"])

    def test_edit_overlay_preserves_raw_evidence_and_old_snapshot_but_changes_new_context(self):
        original = self.capture("cap-a")
        raw = {key: Path(original[key]).read_bytes() for key in ("capturePath", "descriptionPath", "taskPath")}
        old, old_payload = self.bundle()
        old_bytes = Path(old["bundlePath"]).read_bytes()
        updated = self.workspace.update_capture("cap-a", {
            "name": "查询订单", "description": "按编号查询本页订单", "preconditions": ["已登录", "订单号已填写"],
            "expectedResult": "仅显示匹配订单", "postconditions": "列表可见", "notes": "等待用户指定下一步", "allowedActions": ["read", "click"],
        })
        for key in ("page", "control", "createdAt", "savedAt"):
            self.assertEqual(updated[key], original[key])
        for key, content in raw.items():
            self.assertEqual(Path(original[key]).read_bytes(), content)
        self.assertEqual(self.fresh_workspace().load_capture("cap-a")["annotation"], updated["annotation"])
        self.assertEqual(self.workspace.list_captures()[0]["annotation"]["name"], "查询订单")
        _, new_payload = self.bundle()
        item = new_payload["captures"][0]
        self.assertEqual(item["capture"]["annotation"], updated["annotation"])
        self.assertTrue(item["source"]["annotationEdited"])
        self.assertEqual(item["source"]["sha256"], hashlib.sha256(raw["capturePath"]).hexdigest())
        self.assertEqual(old_payload["captures"][0]["capture"]["annotation"]["description"], "Original purpose")
        self.assertNotEqual(old_payload["captures"], new_payload["captures"])
        self.assertEqual(Path(old["bundlePath"]).read_bytes(), old_bytes)

    def test_edits_only_accept_annotation_fields_and_scrub_new_sensitive_literals(self):
        self.capture("cap-a")
        for invalid in ({}, {"page": {}}, {"control": {}}, {"description": " "}, {"name": 12},
                        {"preconditions": [1]}, {"expectedResult": {}}, {"allowedActions": ["execute"]}):
            with self.subTest(invalid=invalid), self.assertRaises(WorkspaceError):
                self.workspace.update_capture("cap-a", invalid)
        result = self.workspace.update_capture("cap-a", {"description": "Purpose password=FAKE_PASSWORD token=FAKE_TOKEN", "notes": r"C:\Users\Alice\private.txt"})
        state = self.workspace.state_path.read_text(encoding="utf-8")
        self.assertNotIn("FAKE_PASSWORD", state)
        self.assertNotIn("FAKE_TOKEN", state)
        self.assertNotIn("Alice", state)
        self.assertIn("[REDACTED]", result["annotation"]["description"])

    def test_soft_delete_excludes_current_exports_and_restore_keeps_original_evidence(self):
        first = self.capture("cap-a", second=1)
        self.capture("cap-b", second=2)
        old, _ = self.bundle()
        old_bytes = Path(old["bundlePath"]).read_bytes()
        evidence = Path(first["capturePath"]).read_bytes()
        self.workspace.update_capture("cap-a", {"description": "Edited before recycle"})
        deleted = self.workspace.delete_capture("cap-a")
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.ids(self.fresh_workspace().list_page_captures(PAGE)), ["cap-b"])
        self.assertEqual(self.ids(self.workspace.list_captures()), ["cap-b"])
        with self.assertRaises(WorkspaceError):self.workspace.load_capture("cap-a")
        with self.assertRaises(WorkspaceError):self.workspace.export_page_bundle(PAGE, ["cap-a"])
        self.assertEqual(self.workspace.delete_capture("cap-a"), deleted)
        _, current = self.bundle()
        self.assertEqual(current["sequence"]["captureIds"], ["cap-b"])
        self.assertEqual(Path(first["capturePath"]).read_bytes(), evidence)
        self.assertEqual(Path(old["bundlePath"]).read_bytes(), old_bytes)
        restored = self.workspace.restore_capture("cap-a")
        self.assertEqual(restored["annotation"]["description"], "Edited before recycle")
        self.assertEqual(self.ids(self.workspace.list_page_captures(PAGE)), ["cap-a", "cap-b"])

    def test_user_sequence_persists_new_capture_appends_and_explicit_subset_keeps_input_order(self):
        for number, capture_id in enumerate(("cap-a", "cap-b", "cap-c"), 1):self.capture(capture_id, second=number)
        order = ["cap-c", "cap-a", "cap-b"]
        self.workspace.set_page_sequence(PAGE, order, ordered=True)
        self.assertEqual(self.fresh_workspace().page_sequence(PAGE), {"captureIds": order, "ordered": True})
        self.capture("cap-d", second=4)
        expected = order + ["cap-d"]
        self.assertEqual(self.ids(self.workspace.list_page_captures(PAGE)), expected)
        exported, payload = self.bundle()
        self.assertEqual(payload["sequence"]["captureIds"], expected)
        self.assertTrue(payload["sequence"]["ordered"])
        self.assertEqual(payload["sequence"]["source"], "user-declared")
        self.assertFalse(payload["sequence"]["executionValidated"])
        self.assertTrue(payload["recording"]["orderIsWorkflow"])
        self.assertFalse(payload["recording"]["workflowExecutionValidated"])
        self.assertIn("用户明确安排的预期操作顺序", Path(exported["contextPath"]).read_text(encoding="utf-8"))
        _, subset = self.bundle(capture_ids=["cap-b", "cap-c"])
        self.assertEqual([item["captureId"] for item in subset["captures"]], ["cap-b", "cap-c"])
        self.assertEqual(subset["sequence"]["source"], "explicit-selection")
        self.workspace.set_page_sequence(PAGE, expected, ordered=False)
        _, unchecked = self.bundle()
        self.assertFalse(unchecked["sequence"]["ordered"])
        self.assertIn("用户未声明按序操作", unchecked["sequence"]["note"])

    def test_sequence_requires_every_live_page_id_without_duplicates_or_foreign_items(self):
        self.capture("cap-a")
        self.capture("cap-b", second=2)
        self.capture("cap-foreign", page="https://example.test/other")
        self.workspace.set_page_sequence(PAGE, ["cap-b", "cap-a"], ordered=True)
        before = self.workspace.state_path.read_bytes()
        for invalid in (["cap-a"], ["cap-a", "cap-a"], ["cap-a", "cap-foreign"], ["cap-a", "../escape"], "cap-a"):
            with self.subTest(invalid=invalid), self.assertRaises(WorkspaceError):
                self.workspace.set_page_sequence(PAGE, invalid)
        with self.assertRaises(WorkspaceError):self.workspace.set_page_sequence(PAGE, ["cap-a", "cap-b"], ordered=1)
        self.assertEqual(self.workspace.state_path.read_bytes(), before)

    def test_multiple_workspace_instances_serialize_overlay_and_sequence_updates(self):
        ids = ["cap-" + str(number) for number in range(6)]
        for number, capture_id in enumerate(ids, 1):self.capture(capture_id, second=number)
        instances = [self.fresh_workspace() for _ in range(7)]
        self.assertTrue(all(instance._lock is self.workspace._lock for instance in instances))
        start = threading.Barrier(7)

        def edit(number):
            start.wait(timeout=5)
            if number == 6:return instances[number].set_page_sequence(PAGE, list(reversed(ids)), ordered=True)
            return instances[number].update_capture(ids[number], {"name": "Edited " + str(number)})

        with ThreadPoolExecutor(max_workers=7) as workers:list(workers.map(edit, range(7)))
        fresh = self.fresh_workspace()
        self.assertEqual(fresh.page_sequence(PAGE), {"captureIds": list(reversed(ids)), "ordered": True})
        self.assertEqual({row["annotation"]["name"] for row in fresh.list_page_captures(PAGE)}, {"Edited " + str(number) for number in range(6)})

    def test_atomic_write_failure_keeps_prior_state_and_invalid_json_fails_closed(self):
        self.capture("cap-a")
        self.capture("cap-b", second=2)
        self.workspace.set_page_sequence(PAGE, ["cap-b", "cap-a"], ordered=True)
        before = self.workspace.state_path.read_bytes()
        with patch("workspace.os.replace", side_effect=OSError("fixture atomic failure")), self.assertRaises(OSError):
            self.workspace.update_capture("cap-a", {"name": "Must not persist"})
        self.assertEqual(self.workspace.state_path.read_bytes(), before)
        self.assertEqual(self.workspace.load_capture("cap-a")["annotation"]["name"], "cap-a")
        self.assertEqual(list(self.workspace.state_path.parent.glob(".page-state.json.*.tmp")), [])
        self.workspace.delete_capture("cap-a")
        self.workspace.state_path.write_text("{invalid JSON", encoding="utf-8")
        for operation in (lambda: self.workspace.list_captures(), lambda: self.workspace.list_page_captures(PAGE),
                          lambda: self.workspace.page_sequence(PAGE), lambda: self.workspace.export_page_bundle(PAGE),
                          lambda: self.workspace.load_capture("cap-a")):
            with self.assertRaises(WorkspaceError):operation()

    def test_capture_file_symlink_cannot_read_or_edit_outside_workspace(self):
        record = self.capture("cap-a")
        path = Path(record["capturePath"])
        outside = self.root.parent / "outside-capture.json"
        original = path.read_bytes()
        outside.write_bytes(original)
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("Creating isolated symlinks is unavailable on this Windows installation")
        with self.assertRaises(WorkspaceError):self.workspace.load_capture("cap-a")
        self.assertEqual(self.workspace.list_captures(), [])
        self.assertEqual(self.workspace.list_page_captures(PAGE), [])
        with self.assertRaises(WorkspaceError):self.workspace.update_capture("cap-a", {"description": "Must not edit an external source"})
        with self.assertRaises(WorkspaceError):self.workspace.delete_capture("cap-a")
        with self.assertRaises(WorkspaceError):self.workspace.export_page_bundle(PAGE, ["cap-a"])
        self.assertEqual(outside.read_bytes(), original)
        self.assertFalse(self.workspace.state_path.exists())

    def test_loading_checks_final_capture_file_boundary_even_without_symlink_privileges(self):
        record = self.capture("cap-a")
        path = Path(record["capturePath"])
        original_inside = self.workspace._inside
        checked = []

        def final_file_guard(candidate):
            if Path(candidate) == path:
                checked.append(candidate)
                raise WorkspaceError("fixture final file resolves outside workspace")
            return original_inside(candidate)

        with patch.object(self.workspace, "_inside", side_effect=final_file_guard), self.assertRaises(WorkspaceError):
            self.workspace.load_capture("cap-a")
        self.assertEqual(checked, [path])


if __name__ == "__main__":
    unittest.main()
