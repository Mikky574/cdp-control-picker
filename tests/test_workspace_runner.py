"""Offline tests only: no installed ZCode or Chrome is started."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))
from workspace import Workspace, WorkspaceError
from zcode_runner import ZCodeRunner, RunnerError, resolve_command


def sample_card():
    return {
        "page": {"url": "https://user:pass@example.test/orders?token=not-for-disk#secret", "title": "订单", "targetId": "tab-1"},
        "control": {
            "tagName": "BUTTON", "text": "详情", "role": {"value": "button", "source": "dom-heuristic"},
            "value": "private-input-value", "outerHTML": '<input value="private-html-value">',
            "attributes": {"data-action": "view-order", "value": "private-attr-value", "data-token": "private-token"},
            "selectors": [{"strategy": "css", "value": '[data-action="view-order"]', "matchCount": 1}, {"strategy": "css", "value": 'input[value="private-selector-value"]'}],
        },
        "annotation": {"description": "打开指定订单的只读详情", "allowedActions": ["click", "read"]},
        "validation": {"status": "validated", "matchCount": 1},
        "ephemeral": {"elementToken": "not-for-disk"},
        "privateKey": "-----BEGIN PRIVATE KEY-----\nprivate-key-bytes\n-----END PRIVATE KEY-----",
    }


class OfflineTests(unittest.TestCase):
    def setUp(self):
        # Test work is isolated and never enters the repository deliverables.
        task_work = Path.cwd() / "work"
        task_work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="picker-test-", dir=task_work)
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_task(self):
        workspace = Workspace(self.base / "project space & name")
        record = workspace.save_capture(sample_card())
        return workspace, record

    def fake_cli(self, body):
        script = self.base / "fake cli.py"
        script.write_text("import sys,json,pathlib,time\n" + body, encoding="utf-8")
        return [sys.executable, str(script)]

    def test_capture_roundtrip_redacts_inputs_and_keeps_locator_meaning(self):
        workspace, record = self.make_task()
        text = Path(record["capturePath"]).read_text(encoding="utf-8")
        for forbidden in ("private-input-value", "private-html-value", "private-attr-value", "private-token", "private-selector-value", "private-key-bytes", "elementToken", "user:pass", "?token"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(record["page"]["url"], "https://example.test/orders")
        self.assertEqual(record["control"]["role"]["value"], "button")
        self.assertEqual(record["control"]["selectors"][0]["value"], '[data-action="view-order"]')
        self.assertEqual(record["validation"]["status"], "draft")
        self.assertEqual(workspace.list_captures()[0]["captureId"], record["captureId"])
        self.assertEqual(workspace.load_capture(record["captureId"])["annotation"]["description"], "打开指定订单的只读详情")
        task = Path(record["taskPath"]).read_text(encoding="utf-8")
        self.assertIn("不连接或操作网页", task)
        self.assertIn("cdp-page-contract", task)
        self.assertTrue(Path(record["generatedPath"]).is_dir())

    def test_description_requires_meaning_and_does_not_persist_obvious_secret(self):
        workspace = Workspace(self.base / "project")
        card = sample_card()
        card["annotation"] = {"description": ""}
        with self.assertRaises(WorkspaceError):
            workspace.save_capture(card)
        card["annotation"]["description"] = "查询接口，password=example-password"
        record = workspace.save_capture(card)
        self.assertNotIn("example-password", Path(record["descriptionPath"]).read_text(encoding="utf-8"))

    def test_capture_id_does_not_escape_workspace(self):
        workspace = Workspace(self.base / "project")
        with self.assertRaises(WorkspaceError):
            workspace.save_capture(sample_card(), capture_id="../outside")
        self.assertFalse((self.base / "outside").exists())

    def test_cmd_wrapper_resolves_node_and_script_without_running_shell(self):
        bundle_dir = self.base / "ZCode with spaces"
        bundle_dir.mkdir()
        node = bundle_dir / "node.exe"
        script = bundle_dir / "zcode.cjs"
        node.write_bytes(b"not-executable-fixture")
        script.write_text("// fixture", encoding="utf-8")
        wrapper = bundle_dir / "zcode.cmd"
        wrapper.write_text('@echo off\n"' + str(node) + '" "' + str(script) + '" %*\n', encoding="utf-8")
        self.assertEqual(resolve_command(wrapper), [str(node.resolve()), str(script.resolve())])
        wrapper.write_text('@echo off\necho unrecognized wrapper %*\n', encoding="utf-8")
        with self.assertRaises(RunnerError):
            resolve_command(wrapper)

    def test_fake_cli_receives_one_short_task_and_returns_json(self):
        workspace, record = self.make_task()
        received = self.base / "received.json"
        command = self.fake_cli("pathlib.Path(" + repr(str(received)) + ").write_text(json.dumps(sys.argv[1:],ensure_ascii=False),encoding='utf-8')\nprint(json.dumps({'sessionId':'sess_fake','response':'draft ready','projection':{'status':'idle'}}),flush=True)\n")
        outputs, done = [], threading.Event()
        runner = ZCodeRunner(command, workspace.root)
        run_id = runner.start(record["taskPath"], lambda rid, stream, text: outputs.append((rid, stream, text)), lambda rid, state: done.set())
        state = runner.wait(run_id, 10)
        self.assertIsNotNone(state)
        self.assertTrue(done.wait(2))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["sessionId"], "sess_fake")
        args = json.loads(received.read_text(encoding="utf-8"))
        self.assertEqual(args[args.index("--cwd") + 1], str(workspace.root))
        self.assertEqual(args[args.index("--mode") + 1], "edit")
        self.assertIn("--json", args)
        self.assertEqual(args[args.index("-p") + 1], "读取“" + record["taskPath"] + "”，按其中说明整理能力草稿。")
        self.assertTrue(outputs)
        self.assertEqual(json.loads(Path(state["runPath"]).read_text(encoding="utf-8"))["status"], "completed")

    def test_nonzero_and_unconfirmed_exit_are_distinct(self):
        workspace, record = self.make_task()
        failed = ZCodeRunner(self.fake_cli("print('api_key=example-sensitive-key',file=sys.stderr)\nsys.exit(7)\n"), workspace.root)
        state = failed.wait(failed.start(record["taskPath"]), 10)
        self.assertEqual((state["status"], state["exitCode"]), ("failed", 7))
        self.assertNotIn("example-sensitive-key", Path(state["stderrPath"]).read_text(encoding="utf-8"))
        unknown = ZCodeRunner(self.fake_cli("print('no final protocol JSON')\n"), workspace.root)
        state = unknown.wait(unknown.start(record["taskPath"]), 10)
        self.assertEqual(state["status"], "unknown")

    def test_timeout_does_not_repeat_cli(self):
        workspace, record = self.make_task()
        admitted = self.base / "admitted.txt"
        command = self.fake_cli("with pathlib.Path(" + repr(str(admitted)) + ").open('a') as f: f.write('once\\n')\ntime.sleep(30)\n")
        runner = ZCodeRunner(command, workspace.root, timeout_seconds=0.5)
        run_id = runner.start(record["taskPath"])
        with self.assertRaises(RunnerError):
            runner.start(record["taskPath"])
        state = runner.wait(run_id, 10)
        self.assertEqual(state["status"], "unknown")
        self.assertEqual(admitted.read_text().splitlines(), ["once"])

    def test_task_outside_workspace_is_rejected_before_process(self):
        workspace, _ = self.make_task()
        outside = self.base / "outside.md"
        outside.write_text("no", encoding="utf-8")
        runner = ZCodeRunner(self.fake_cli("raise SystemExit(99)\n"), workspace.root)
        with self.assertRaises(RunnerError):
            runner.start(outside)


if __name__ == "__main__":
    unittest.main()
