"""Queue and package diagnostics use fakes; no Tk window or desktop is opened."""
import json
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from workspace import Workspace


class FakeValue:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class FakeRoot:
    def __init__(self, failure=None):
        self.failure = failure
        self.clipboard = None
        self.scheduled = []

    def clipboard_clear(self):
        if self.failure == "clear":
            raise app.tk.TclError("clipboard is busy")
        self.clipboard = ""

    def clipboard_append(self, value):
        if self.failure == "append":
            raise app.tk.TclError("clipboard is busy")
        self.clipboard += value

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))


class AppDiagnosticsTests(unittest.TestCase):
    def fake_app(self, failure=None):
        picker = app.PickerApp.__new__(app.PickerApp)
        picker.root = FakeRoot(failure)
        picker.closed = False
        picker.events = queue.Queue()
        picker.status = FakeValue()
        picker.saved_status = FakeValue()
        picker._save_lock = threading.RLock()
        picker._pending_saves = {}
        picker._save_barriers = {}
        picker.connection_generation = 1
        picker.capture_enabled = True
        picker.client = None
        picker.current_target = None
        picker.pages = []
        picker.page_combo = SimpleNamespace(current=lambda: -1)
        picker.records = {}
        picker.exporting = picker.sending = True
        picker.logs = []
        picker._log = picker.logs.append
        picker._controls = lambda: None
        picker.events.put(("page-bundle", {
            "shortTask": "Read the exported page task.",
            "captureCount": 2, "bundleId": "page-fixture",
        }))
        picker.events.put(("page-export-finished", None))
        return picker

    def test_clipboard_failure_keeps_event_pump_and_finishes_export(self):
        for failure in ("clear", "append"):
            with self.subTest(failure=failure):
                picker = self.fake_app(failure)
                picker._drain()
                self.assertIn("复制失败", picker.status.value)
                self.assertNotIn("交接说明已复制", picker.status.value)
                self.assertFalse(picker.exporting)
                self.assertFalse(picker.sending)
                self.assertTrue(picker.events.empty())
                self.assertEqual(picker.root.scheduled, [(100, picker._drain)])
                self.assertTrue(any("剪贴板复制失败" in line for line in picker.logs))

    def test_successful_copy_still_reports_copy_and_finishes_export(self):
        picker = self.fake_app()
        picker._drain()
        self.assertEqual(picker.root.clipboard, "Read the exported page task.")
        self.assertIn("交接说明已复制", picker.status.value)
        self.assertFalse(picker.exporting)
        self.assertEqual(len(picker.root.scheduled), 1)

    def test_package_check_reports_cli_backend_without_loading_runner_or_ui(self):
        with tempfile.TemporaryDirectory(prefix="picker-package-diagnostic-") as temporary:
            root = Path(temporary)
            output = root / "package.json"
            with patch.object(sys, "argv", ["app.py", "--package-check", str(output)]), \
                 patch.dict(sys.modules, {"websocket": SimpleNamespace(__version__="fixture")}), \
                 patch.object(app.tk, "Tk", side_effect=AssertionError("No UI permitted")):
                for name in ("zcode_runner", "zcode_handoff", "desktop_handoff"):
                    sys.modules.pop(name, None)
                app.main()
                self.assertNotIn("zcode_runner", sys.modules)
                self.assertNotIn("zcode_handoff", sys.modules)
                self.assertNotIn("desktop_handoff", sys.modules)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["handoffBackend"], "app-server-with-desktop-index")
            self.assertFalse(report["zcodeRunnerImported"])
            self.assertNotIn("desktopBridgePath", report)
            self.assertNotIn("desktopBridgeExists", report)
            self.assertFalse(report["openedWindow"])
            self.assertFalse(report["connectedBrowser"])

    def test_handoff_requires_registered_index_and_session(self):
        cases = [
            ({"status": "registered", "indexRegistered": True, "sessionId": "session-fixture"}, True),
            ({"status": "registered", "indexRegistered": False, "sessionId": "session-fixture"}, False),
            ({"status": "registered", "indexRegistered": True, "sessionId": " "}, False),
            ({"status": "confirmed", "uiVisible": True}, False),
            ({"status": "failed", "message": "fixture failure"}, False),
            ({"status": "unknown", "message": "fixture uncertainty"}, False),
        ]
        for result, expected_success in cases:
            with self.subTest(result=result):
                picker = self.fake_app()
                picker.events = queue.Queue()
                picker.events.put(("handoff-result", result))
                picker.events.put(("page-export-finished", None))
                picker._drain()
                self.assertEqual("已交给 ZCode" in picker.status.value, expected_success)
                self.assertNotIn("已进入 ZCode 对话", picker.status.value)
                self.assertFalse(picker.sending)
                self.assertEqual(len(picker.root.scheduled), 1)

    def test_send_uses_lazy_cli_adapter_with_selected_workspace_and_program(self):
        picker = self.fake_app()
        picker.events = queue.Queue()
        picker.exporting = picker.sending = False
        picker.client = None
        picker.page_combo = SimpleNamespace(current=lambda: 0)
        picker.pages = [{"url": "https://example.test/orders?filter=a", "id": "fixture-page"}]
        picker.zcode_path = SimpleNamespace(get=lambda: r"C:\Installed\ZCode\ZCode.exe")
        bundle = {"bundleId": "page-fixture", "captureCount": 2, "shortTask": "Read task."}
        workspace = SimpleNamespace(export_page_bundle=lambda page: bundle)
        picker._workspace = lambda: workspace
        picker._background = lambda action: action()
        calls = []

        class FakeHandoff:
            def __init__(self, selected_workspace, commandPath=None):
                calls.append((selected_workspace, commandPath))

            def send(self, selected_bundle, on_event=None):
                calls.append(selected_bundle)
                on_event({"type": "progress", "message": "fixture progress"})
                return {"status": "registered", "indexRegistered": True, "sessionId": "session-fixture"}

        with patch.dict(sys.modules, {"zcode_handoff": SimpleNamespace(ZCodeHandoff=FakeHandoff)}):
            picker.send_page_to_zcode()
        self.assertEqual(calls, [(workspace, r"C:\Installed\ZCode\ZCode.exe"), bundle])
        picker._drain()
        self.assertIn("已交给 ZCode", picker.status.value)
        self.assertFalse(picker.sending)

    @staticmethod
    def binding(payload):
        return {"method": "Runtime.bindingCalled", "params": {
            "name": app.BINDING, "payload": json.dumps(payload), "executionContextId": 1,
        }}

    def test_pending_binding_blocks_send_and_copy_before_tk_queue_is_drained(self):
        picker = self.fake_app()
        picker.events = queue.Queue()
        picker.exporting = picker.sending = False
        picker._event(self.binding({"captureId": "cap-pending"}), picker.connection_generation)
        self.assertEqual(picker._pending_save_count(), 1)
        self.assertEqual(picker.events.qsize(), 1)
        for send in (False, True):
            with self.subTest(send=send), patch.object(picker, "_background", create=True) as background:
                picker._begin_page_task(send=send)
                background.assert_not_called()
                self.assertEqual(picker.status.value, app.SAVE_WAIT_MESSAGE)
                self.assertFalse(picker.exporting)
                self.assertFalse(picker.sending)

    def test_pending_controls_disable_export_and_preserve_annotation_workspace(self):
        picker = self.fake_app()
        picker.exporting = picker.sending = False
        picker.closing = picker.connecting = False
        picker.client = SimpleNamespace(connected=True)
        picker._queue_fit = lambda: None

        class Widget:
            def __init__(self):self.options = {}
            def configure(self, **options):self.options.update(options)
            def winfo_exists(self):return True

        for name in ("connect_button", "disconnect_button", "page_combo", "send_button", "export_button"):
            setattr(picker, name, Widget())
        folder = Widget()
        picker._settings_workspace_controls = (folder,)
        picker._event(self.binding({"captureId": "cap-pending"}))
        app.PickerApp._controls(picker)
        for widget in (picker.send_button, picker.export_button, picker.disconnect_button, folder):
            self.assertEqual(widget.options["state"], "disabled")
        token = next(iter(picker._pending_saves))
        picker._finish_pending_save(token)
        app.PickerApp._controls(picker)
        self.assertEqual(picker.send_button.options["text"], "发给 ZCode")
        self.assertEqual(picker.send_button.options["state"], "normal")

    def test_save_worker_releases_pending_after_success_or_error(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                picker = self.fake_app()
                picker.events = queue.Queue()
                picker.client = SimpleNamespace(connected=True)
                workers = []
                picker._background = workers.append
                picker._event(self.binding({"captureId": "cap-pending"}))
                _, (_, event) = picker.events.get_nowait()
                picker._handle_cdp(event)
                self.assertEqual(picker._pending_save_count(), 1)
                self.assertEqual(len(workers), 1)
                observations = []

                def save(payload, context):
                    observations.append(picker._pending_save_count())
                    if failure:raise ValueError("fixture save failed")

                picker._save_binding = save
                if failure:
                    with self.assertRaisesRegex(ValueError, "fixture save failed"):workers[0]()
                else:workers[0]()
                self.assertEqual(observations, [1])
                self.assertEqual(picker._pending_save_count(), 0)
                self.assertEqual(picker.events.get_nowait(), ("save-finished", None))

    def test_stale_connection_event_releases_its_pending_save(self):
        picker = self.fake_app()
        picker.events = queue.Queue()
        picker._event(self.binding({"captureId": "cap-old"}))
        picker.connection_generation += 1
        with patch.object(picker, "_save_binding", create=True) as save:
            picker._drain()
        save.assert_not_called()
        self.assertEqual(picker._pending_save_count(), 0)

    def test_barrier_blocks_late_binding_then_exports_all_committed_annotations(self):
        with tempfile.TemporaryDirectory(prefix="picker-pending-snapshot-") as temporary:
            workspace = Workspace(Path(temporary))
            page_url = "https://example.test/orders?filter=a"

            def card(capture_id):
                return {"captureId": capture_id, "page": {"url": page_url},
                        "control": {"tagName": "button"},
                        "annotation": {"description": "查看订单详情", "allowedActions": ["read"]}}

            original = workspace.save_capture(card("cap-original"))
            picker = self.fake_app()
            picker.events = queue.Queue()
            picker.records = {original["captureId"]: original}
            picker.exporting = picker.sending = False
            picker.page_combo = SimpleNamespace(current=lambda: 0)
            picker.pages = [{"id": "page-fixture", "url": page_url}]
            picker.current_target = dict(picker.pages[0])
            picker.zcode_path = SimpleNamespace(get=lambda: "fixture ZCode.exe")
            picker._workspace = lambda: workspace

            def inline(action):
                try:action()
                except Exception as error:picker.events.put(("error", str(error)))

            picker._background = inline

            def save(payload, context):
                record = workspace.save_capture(json.loads(payload))
                picker.events.put(("saved", record))

            picker._save_binding = save

            class Client:
                connected = True
                late_binding = True

                def call(self, method, params):
                    self.assert_barrier(method, params)
                    nonce = json.loads(re.search(r'_pickerHostBarrier:("[0-9a-f]+")', params["expression"]).group(1))
                    # Simulate a capture already ahead of the marker in the
                    # transport's callback queue, but not admitted when clicked.
                    if self.late_binding:
                        self.late_binding = False
                        picker._event(AppDiagnosticsTests.binding(card("cap-latest")))
                    picker._event(AppDiagnosticsTests.binding({"_pickerHostBarrier": nonce}))
                    return {"result": {"value": page_url}}

                @staticmethod
                def assert_barrier(method, params):
                    if method != "Runtime.evaluate" or "_pickerHostBarrier" not in params["expression"]:
                        raise AssertionError("Expected a binding-queue barrier")

            picker.client = Client()
            self.assertEqual(picker._pending_save_count(), 0)
            picker.export_page_task()
            self.assertEqual(list((workspace.root / "page-bundles").glob("*/page-bundle.json")), [])
            self.assertEqual(picker._pending_save_count(), 1)
            self.assertEqual(picker._save_barriers, {})
            picker._drain()
            self.assertEqual(picker._pending_save_count(), 0)
            self.assertEqual(len(workspace.list_captures()), 2)
            self.assertEqual(picker.saved_status.value, "本页已保存 2 项 · 仅含已落盘标注")
            self.assertFalse(picker.exporting)
            picker.export_page_task()
            picker._drain()
            bundles = list((workspace.root / "page-bundles").glob("*/page-bundle.json"))
            self.assertEqual(len(bundles), 1)
            payload = json.loads(bundles[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["captureCount"], 2)
            self.assertEqual({item["captureId"] for item in payload["captures"]}, {"cap-original", "cap-latest"})
            self.assertIn("交接说明已复制", picker.status.value)
            self.assertIsNotNone(picker.root.clipboard)

    def test_saved_count_uses_current_page_identity(self):
        picker = self.fake_app()
        picker.client = SimpleNamespace(connected=True)
        picker.current_target = {"url": "https://EXAMPLE.test:443/orders?view=1#item"}
        picker.records = {str(index): {"page": {"url": url}} for index, url in enumerate((
            "https://example.test/orders", "https://example.test/orders?view=2",
            "https://example.test/other", "https://other.test/orders",
        ))}
        picker._update_saved_status()
        self.assertEqual(picker.saved_status.value, "本页已保存 2 项 · 仅含已落盘标注")


if __name__ == "__main__":
    unittest.main()
