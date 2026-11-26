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
from send_feedback import SendFeedback
from workspace import Workspace


class FakeValue:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


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


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)

    def winfo_exists(self):
        return True


class Combo(Widget):
    def __init__(self, index=0):
        super().__init__()
        self.index = index

    def current(self, value=None):
        if value is not None:
            self.index = value
        return self.index


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
        picker._page_records = []
        picker.sequence_ordered = FakeValue(False)
        picker.send_feedback = SendFeedback()
        picker._feedback_workspace = None
        picker.send_feedback.select('a' * 64)
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

    def prepared_sender(self):
        temporary = tempfile.TemporaryDirectory(prefix="picker-send-controller-")
        self.addCleanup(temporary.cleanup)
        workspace = Workspace(Path(temporary.name))
        page = "https://example.test/orders"
        for capture_id in ("cap-first", "cap-next"):
            workspace.save_capture({"captureId": capture_id, "page": {"url": page},
                                    "control": {"tagName": "button"},
                                    "annotation": {"name": capture_id, "description": "查看信息"}})
        picker = self.fake_app()
        picker.events = queue.Queue()
        picker.exporting = picker.sending = picker.closing = picker.connecting = False
        picker.capture_enabled = False
        picker.editor_window = None
        picker.page_combo = Combo()
        picker.pages = [{"url": page, "id": "fixture-page", "title": "虚构订单页"}]
        picker.page_choice = FakeValue()
        picker.capture_status = FakeValue()
        picker.endpoint = FakeValue("http://127.0.0.1:19222")
        picker._refresh_selected_id = "fixture-page"
        picker._refresh_pages_backend = lambda: None
        picker.zcode_path = FakeValue(r"C:\Installed\ZCode\ZCode.exe")
        picker.workspace_value = str(workspace.root)
        picker._feedback_workspace = str(workspace.root)
        picker.send_feedback = SendFeedback(receipt_path=workspace.root / '.handoffs' / 'ui-receipts.json')
        picker._workspace = lambda: workspace
        picker._page_records = workspace.list_page_captures(page)
        picker.records = {record['captureId']: record for record in picker._page_records}
        picker.send_feedback.select(picker._page_revision())
        picker._queue_fit = lambda *args: None
        picker._background = lambda action: action()
        for name in ('connect_button', 'disconnect_button', 'send_button', 'export_button',
                     'launch_button', 'refresh_button', 'endpoint_entry'):
            setattr(picker, name, Widget())
        picker._controls = lambda: app.PickerApp._controls(picker)
        return picker, workspace

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
            self.assertFalse(report["automaticModelCalls"])
            for key in ("originalToolbarLayout", "persistentSendFeedback",
                        "contentSendGuard", "chromeWindowActivation"):
                self.assertIs(report[key], True)
            self.assertEqual(report['controlPresentation'], 'rows')
            self.assertEqual(report['minimumCollapsedWindowSize'], [280, 210])

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
        temporary = tempfile.TemporaryDirectory(prefix="picker-fake-handoff-")
        self.addCleanup(temporary.cleanup)
        workspace = Workspace(Path(temporary.name))
        for capture_id, name in (("cap-input", "编号"), ("cap-search", "查询")):
            workspace.save_capture({"captureId": capture_id,
                                    "page": {"url": "https://example.test/orders"},
                                    "control": {"tagName": "button"},
                                    "annotation": {"name": name, "description": "功能：" + name}})
        picker._page_records = workspace.list_page_captures("https://example.test/orders")
        picker.workspace_value = str(workspace.root)
        picker._feedback_workspace = str(workspace.root)
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

        inspector = lambda selected_workspace, bundle: {"canSubmit": True}
        with patch.dict(sys.modules, {"zcode_handoff": SimpleNamespace(
                ZCodeHandoff=FakeHandoff, inspect_handoff=inspector)}):
            picker.send_page_to_zcode()
        self.assertEqual(calls[0], (workspace, r"C:\Installed\ZCode\ZCode.exe"))
        bundle = calls[1]
        self.assertEqual(bundle["captureCount"], 2)
        self.assertTrue(Path(bundle["bundlePath"]).is_file())
        self.assertEqual(picker.events.queue[0][0], "handoff-bundle")
        picker._drain()
        self.assertIn("已交给 ZCode", picker.status.value)
        self.assertFalse(picker.sending)
        self.assertFalse(picker._send_state().can_send(picker._page_revision()))

    def test_same_tick_duplicate_has_persistent_feedback_without_creating_an_adapter(self):
        picker, _ = self.prepared_sender()
        previous = {'status': 'registered', 'indexRegistered': True,
                    'sessionId': 'sess_duplicate_fixture', 'mode': 'yolo',
                    'responseSummary': '已收到控件上下文。'}
        inspect = lambda *_: {'duplicate': True, 'canSubmit': False,
                             'canRegister': False, 'previous': previous}
        with patch.dict(sys.modules, {'zcode_handoff': SimpleNamespace(
                inspect_handoff=inspect,
                ZCodeHandoff=lambda *_args, **_kwargs: self.fail('Duplicate must not construct adapter'))}):
            picker.send_page_to_zcode()
        picker._drain()
        view = picker._send_state().view()
        self.assertEqual(view['phase'], 'duplicate')
        self.assertTrue(view['blocked'])
        self.assertFalse(view['active'])
        self.assertIn('没有重复提交', view['message'])
        self.assertEqual(picker.send_button.options['state'], 'disabled')
        with patch.object(picker, '_background') as worker:
            picker.send_page_to_zcode()
        worker.assert_not_called()
        self.assertEqual(picker._send_state().view()['phase'], 'duplicate')

    def test_legacy_duplicate_mode_still_uses_the_permission_upgrade_backend(self):
        for mode in ('edit', None):
            with self.subTest(mode=mode):
                picker, _ = self.prepared_sender()
                previous = {'status': 'registered', 'indexRegistered': True,
                            'sessionId': 'sess_upgrade_fixture', 'mode': mode}
                calls = []

                class MetadataOnlyAdapter:
                    def __init__(self, *_args, **_kwargs):
                        calls.append('adapter')

                    def send(self, _bundle, on_event=None):
                        calls.append('send')
                        return {**previous, 'mode': 'yolo', 'duplicate': True}

                with patch.dict(sys.modules, {'zcode_handoff': SimpleNamespace(
                        inspect_handoff=lambda *_: {'duplicate': True, 'canSubmit': False,
                                                   'canRegister': False, 'previous': previous},
                        ZCodeHandoff=MetadataOnlyAdapter)}):
                    picker.send_page_to_zcode()
                picker._drain()
                self.assertEqual(calls, ['adapter', 'send'])
                self.assertEqual(picker._send_state().view()['phase'], 'duplicate')

    def test_browser_refresh_and_launch_notice_do_not_replace_send_feedback(self):
        picker, _ = self.prepared_sender()
        picker._send_state().begin(picker._page_revision())
        picker._send_state().complete({'status': 'registered', 'indexRegistered': True,
                                       'sessionId': 'sess_browser_fixture'})
        before = picker._send_state().view()
        notice = '已找到Chrome，但系统未确认前台激活，请从任务栏打开。'
        picker.events.put(('browser-started', {'status': 'reused', 'message': notice,
                                              'visible': True, 'foreground': False}))
        picker.events.put(('pages', list(picker.pages)))
        picker._drain()
        self.assertEqual(picker.status.value, notice)
        after = picker._send_state().view()
        for key in ('phase', 'title', 'message', 'completedAt', 'result'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(picker.send_button.options['state'], 'disabled')

    def test_error_before_export_finished_keeps_the_actual_failure_classification(self):
        for uncertain, expected in ((False, 'failed'), (True, 'unknown')):
            with self.subTest(uncertain=uncertain):
                picker, _ = self.prepared_sender()
                revision = picker._page_revision()
                picker._send_state().begin(revision)
                picker.exporting = picker.sending = True
                picker.events.put(('handoff-error', {'message': 'fixture发送异常',
                                                     'uncertain': uncertain}))
                picker.events.put(('page-export-finished', None))
                picker._drain()
                self.assertEqual(picker._send_state().view()['phase'], expected)
                self.assertEqual(picker._send_state().can_send(revision), not uncertain)
                self.assertIn('fixture发送异常', picker._send_state().view()['message'])
                self.assertFalse(picker.sending)

    def test_export_finished_without_a_result_becomes_unknown_and_blocks_repeat(self):
        picker, _ = self.prepared_sender()
        revision = picker._page_revision()
        picker._send_state().begin(revision)
        picker.exporting = picker.sending = True
        picker.events.put(('page-export-finished', None))
        picker._drain()
        self.assertEqual(picker._send_state().view()['phase'], 'unknown')
        self.assertFalse(picker._send_state().can_send(revision))
        self.assertFalse(picker.sending)

    def test_second_send_is_blocked_while_the_first_worker_is_still_pending(self):
        picker, _ = self.prepared_sender()
        workers = []
        picker._background = workers.append
        picker.send_page_to_zcode()
        picker.send_page_to_zcode()
        self.assertEqual(len(workers), 1)
        self.assertTrue(picker._send_state().view()['active'])
        self.assertEqual(picker.send_button.options['state'], 'disabled')
        self.assertEqual(picker.page_combo.options['state'], 'disabled')

    def test_late_capture_changes_the_bundle_and_stops_before_any_backend_submission(self):
        picker, workspace = self.prepared_sender()
        revision = picker._page_revision()
        workers = []
        picker._background = workers.append
        picker.send_page_to_zcode()
        workspace.save_capture({'captureId': 'cap-late', 'page': {'url': picker.pages[0]['url']},
                                'control': {'tagName': 'input'},
                                'annotation': {'name': '迟到的输入框', 'description': '新保存的控件'}})
        inspector = unittest.mock.Mock(side_effect=AssertionError('Must reject stale UI snapshot first'))
        adapter = unittest.mock.Mock(side_effect=AssertionError('Must not construct backend'))
        with patch.dict(sys.modules, {'zcode_handoff': SimpleNamespace(
                inspect_handoff=inspector, ZCodeHandoff=adapter)}):
            workers[0]()
        picker._drain()
        inspector.assert_not_called()
        adapter.assert_not_called()
        self.assertEqual(picker._send_state().view()['phase'], 'failed')
        self.assertIn('刚有变化', picker._send_state().view()['message'])
        self.assertTrue(picker._send_state().can_send(revision))

    def test_annotation_or_order_changes_unlock_the_page_content_guard(self):
        picker, workspace = self.prepared_sender()
        original = picker._page_revision()
        picker._send_state().begin(original)
        picker._send_state().complete({'status': 'registered', 'indexRegistered': True,
                                       'sessionId': 'sess_content_fixture'})
        picker._controls()
        self.assertEqual(picker.send_button.options['state'], 'disabled')
        workspace.update_capture('cap-first', {'name': '更新名称', 'description': '更新功能说明'})
        picker._page_records = workspace.list_page_captures(picker.pages[0]['url'])
        changed = picker._page_revision()
        self.assertNotEqual(changed, original)
        picker._send_state().select(changed)
        picker._controls()
        self.assertEqual(picker.send_button.options['state'], 'normal')
        picker.sequence_ordered.set(True)
        self.assertNotEqual(picker._page_revision(), changed)



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
