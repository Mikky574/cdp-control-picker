"""Queue and package diagnostics use fakes; no Tk window or desktop is opened."""
from contextlib import redirect_stderr
import hashlib
import io
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
from picker_settings import PickerSettings


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
    def model_picker(self):
        picker,workspace=self.prepared_sender()
        picker._settings_store=PickerSettings(workspace.root/'fixture-settings.json')
        picker.chrome_path=FakeValue('')
        picker._model_selection=None;picker._models=[];picker._model_catalog_scope=None
        picker.model_provider_choice=FakeValue('跟随后端当前模型')
        picker.model_choice=FakeValue('');picker.model_catalog_status=FakeValue('')
        picker.desktop_bridge_path=FakeValue('');picker.desktop_status=FakeValue('')
        picker.workspace_path=SimpleNamespace(get=lambda:picker.workspace_value,set=lambda value:setattr(picker,'workspace_value',value))
        picker.settings_window=SimpleNamespace(winfo_exists=lambda:False)
        return picker,workspace

    @staticmethod
    def model_rows():
        return [{'providerId':'provider-a','modelId':'model-a','label':'Model A','providerLabel':'Provider A','available':True},
                {'providerId':'provider-b','modelId':'model-b','label':'Disabled B','providerLabel':'Provider B','available':False}]

    def test_model_catalog_is_asynchronous_scope_bound_and_selection_persists_ids_only(self):
        picker,workspace=self.model_picker()
        workers=[];picker._background=workers.append
        picker.refresh_model_catalog();picker.refresh_model_catalog()
        self.assertEqual(len(workers),1)
        old_scope=picker._model_scope()
        picker.workspace_value=old_scope[2]+'-changed'
        bridge=lambda *args,**kwargs:SimpleNamespace(list_models=lambda:self.model_rows())
        with patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=bridge)}):workers[0]()
        picker._drain()
        self.assertIsNone(picker._model_catalog_scope)
        self.assertEqual(picker._models,[])
        self.assertIn('已改变',picker.model_catalog_status.get())
        picker.workspace_value=old_scope[2];workers.clear()
        picker.refresh_model_catalog()
        with patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=bridge)}):workers[0]()
        self.assertTrue(picker._model_catalog_active,'Worker must only enqueue its result')
        picker._drain()
        self.assertFalse(picker._model_catalog_active)
        self.assertEqual(picker._model_catalog_scope,picker._model_scope())
        self.assertNotIn('provider-b',picker._provider_choices.values())
        provider=next(label for label,value in picker._provider_choices.items() if value=='provider-a')
        picker.model_provider_choice.set(provider);picker._choose_model_provider()
        self.assertEqual(picker._model_provider_pending,'provider-a')
        selected=next(iter(picker._model_choices));picker.model_choice.set(selected);picker._choose_session_model()
        self.assertEqual(picker._model_selection,{'providerId':'provider-a','modelId':'model-a'})
        saved=PickerSettings(picker._settings_store.path).load()
        self.assertEqual(saved['modelSelection'],picker._model_selection)
        self.assertNotIn('label',str(saved))

    def test_catalog_selected_model_is_used_by_handoff_and_content_identity(self):
        picker,workspace=self.model_picker()
        prior=picker._page_revision()
        picker._models=self.model_rows();picker._model_catalog_scope=picker._model_scope()
        picker._model_selection={'providerId':'provider-a','modelId':'model-a'}
        self.assertNotEqual(picker._page_revision(),prior)
        calls=[]
        def inspect(selected_workspace,bundle,*,modelSelection):
            calls.append(('inspect',modelSelection));return {'canSubmit':True}
        class FakeHandoff:
            def __init__(self,selected_workspace,commandPath=None,*,modelSelection=None):
                calls.append(('constructor',modelSelection))
            def send(self,bundle,on_event=None):
                calls.append(('send',bundle['captureCount']))
                return {'status':'registered','indexRegistered':True,'sessionId':'sess_explicit_model'}
        with patch.dict(sys.modules,{'zcode_handoff':SimpleNamespace(inspect_handoff=inspect,ZCodeHandoff=FakeHandoff)}):
            picker.send_page_to_zcode()
        picker._drain()
        self.assertEqual(calls,[('inspect',picker._model_selection),('constructor',picker._model_selection),('send',2)])
        self.assertEqual(picker._send_state().view()['phase'],'registered')

    def test_explicit_desktop_instance_is_part_of_ui_guard_and_handoff_arguments(self):
        picker,workspace=self.model_picker()
        original=picker._page_revision()
        discovery=workspace.root/'selected-desktop.json'
        picker.desktop_bridge_path.set(str(discovery))
        self.assertNotEqual(picker._page_revision(),original)
        calls=[]
        def inspect(selected_workspace,bundle,*,discovery_path):
            calls.append(discovery_path);return {'canSubmit':False,'duplicate':True,
                'previous':{'status':'unknown','transport':'desktop-native-bridge','sessionId':'sess_existing'}}
        with patch.dict(sys.modules,{'zcode_handoff':SimpleNamespace(inspect_handoff=inspect,
                ZCodeHandoff=lambda *_args,**_kwargs:self.fail('A duplicate must not construct another sender'))}):
            picker.send_page_to_zcode()
        picker._drain()
        self.assertEqual(calls,[str(discovery)])

    def test_saved_model_is_refreshed_before_first_send_and_unknown_model_is_not_submitted(self):
        picker,_=self.model_picker()
        picker._model_selection={'providerId':'saved-provider','modelId':'saved-model'}
        workers=[];picker._background=workers.append
        picker.send_page_to_zcode()
        self.assertEqual(len(workers),1)
        self.assertTrue(picker._model_catalog_active)
        self.assertFalse(picker.sending)
        picker._model_catalog_active=False;picker._model_catalog_scope=picker._model_scope();picker._models=self.model_rows()
        workers.clear();picker.send_page_to_zcode()
        self.assertEqual(workers,[])
        self.assertIn('不在当前可用目录',picker.status.get())

    def test_desktop_connection_is_authorized_in_worker_and_workspace_switch_is_explicit(self):
        picker,workspace=self.model_picker()
        native=workspace.root/'native-learning';native.mkdir()
        original_workspace=picker.workspace_value
        captures={path.name:path.read_bytes() for path in (workspace.root/'captures').rglob('capture.json')}
        workers=[];calls=[];picker._background=workers.append
        class Bridge:
            def __init__(self,selected,discovery_path=None):calls.append(('input',selected,discovery_path))
            def authorize_connection(self):calls.append(('authorize',));return {'granted':['sessions.fullAccess']}
            def health(self):return {'workspacePath':str(native),'applicationVersion':'3.18.2','token':'PRIVATE_TOKEN',
                'nativeSessions':True,'authorized':True,'transport':'zcode-extension-host'}
        picker.connect_zcode_desktop();picker.connect_zcode_desktop()
        self.assertEqual(len(workers),1)
        self.assertEqual(calls,[])
        with patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=Bridge)}):workers[0]()
        self.assertEqual(picker.workspace_value,original_workspace)
        picker._drain()
        self.assertEqual(picker.workspace_value,original_workspace,'Connecting must not move existing captures')
        self.assertEqual(calls,[('input',original_workspace,None),('authorize',)])
        self.assertNotIn('PRIVATE_',str(picker._desktop_health))
        self.assertIn('已连接',picker.desktop_status.get())
        picker.use_desktop_learning_workspace()
        self.assertEqual(picker.workspace_value,str(native))
        self.assertEqual(PickerSettings(picker._settings_store.path).load()['workspaceDir'],str(native))
        self.assertEqual({path.name:path.read_bytes() for path in (workspace.root/'captures').rglob('capture.json')},captures)

    def test_desktop_connect_rejects_missing_full_access_and_negative_capability_readback(self):
        healthy={'nativeSessions':True,'authorized':True,'transport':'zcode-extension-host'}
        cases=[('missing-grants',None,healthy),('empty-grants',{'granted':[]},healthy),
               ('wrong-grants-shape',{'granted':'sessions.fullAccess'},healthy),
               ('read-write-only',{'granted':['sessions.read','sessions.write']},healthy),
               ('no-native',{'granted':['sessions.fullAccess']},{**healthy,'nativeSessions':False}),
               ('no-auth',{'granted':['sessions.fullAccess']},{**healthy,'authorized':False}),
               ('numeric-not-true',{'granted':['sessions.fullAccess']},{**healthy,'authorized':1})]
        for name,grant,health in cases:
            picker,_=self.model_picker()
            bridge=SimpleNamespace(authorize_connection=lambda:grant,health=lambda:health)
            with self.subTest(name=name),patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=lambda *args,**kwargs:bridge)}):
                picker.connect_zcode_desktop();picker._drain()
                self.assertEqual(picker._desktop_health,{})
                self.assertNotIn('已连接 ZCode 桌面，',picker.desktop_status.get())

    def test_install_connector_is_explicit_async_and_never_claims_desktop_connected(self):
        picker,_=self.model_picker()
        workers=[];calls=[];picker._background=workers.append
        selected=picker.zcode_path.get()
        self.assertFalse(getattr(picker,'_bridge_install_active',False))
        picker.install_desktop_connector();picker.install_desktop_connector()
        self.assertEqual(len(workers),1)
        self.assertEqual(calls,[])
        def install(**kwargs):
            calls.append((kwargs['commandPath'],kwargs['cwd']))
            kwargs['on_event']({'phase':'install','message':'正在安装。'})
            return {'installed':True,'activationNeeded':True,'desktopConnected':False,'permissionsGranted':False,
                'appRestarted':False,'message':'已安装；首次请在 ZCode 重新加载扩展，再连接并授权。'}
        with patch.dict(sys.modules,{'bridge_setup':SimpleNamespace(install_connector=install)}):workers[0]()
        self.assertTrue(picker._bridge_install_active,'Worker must only enqueue results')
        picker._drain()
        self.assertFalse(picker._bridge_install_active)
        self.assertEqual(calls,[(selected,picker.workspace_value)])
        self.assertEqual(picker._desktop_health,{})
        self.assertIn('重新加载扩展',picker.desktop_status.get())
        self.assertNotIn('已连接',picker.desktop_status.get())
        self.assertFalse(picker.sending)

    def test_install_connector_failure_displays_phase_and_exit_without_retry_or_model(self):
        picker,_=self.model_picker()
        def fail(**kwargs):raise ValueError('插件管理未完成：enable（退出码 7）。')
        with patch.dict(sys.modules,{'bridge_setup':SimpleNamespace(install_connector=fail)}):picker.install_desktop_connector()
        picker._drain()
        self.assertFalse(picker._bridge_install_active)
        self.assertIn('enable',picker.desktop_status.get());self.assertIn('7',picker.desktop_status.get())
        self.assertEqual(picker._desktop_health,{})

    def test_native_progress_opens_real_receipt_before_completion_and_final_needs_native_terminal(self):
        picker,_=self.prepared_sender()
        picker.send_feedback.begin(picker._page_revision());picker.sending=picker.exporting=True
        event={'phase':'native-processing','transport':'desktop-native-bridge','nativeAccepted':True,
               'uiOpened':True,'sessionId':'sess_native_visible','requestId':'native-fixture',
               'conversationTitle':'网页能力学习','message':'原对话已经打开。'}
        picker.events.put(('handoff-progress',event));picker._drain()
        self.assertTrue(picker.send_feedback.active)
        self.assertEqual(picker.send_feedback.view()['phase'],'native-processing')
        self.assertIn('已在 ZCode 打开',picker.status.get())
        with patch.object(app.messagebox,'showinfo') as receipt:picker.open_send_receipt()
        self.assertIn('sess_native_visible',receipt.call_args.args[1])
        picker.events.put(('handoff-result',{**event,'status':'completed','nativeCompleted':True,'responseSummary':'已完成。'}))
        picker.events.put(('page-export-finished',None));picker._drain()
        self.assertEqual(picker.send_feedback.view()['phase'],'native-completed')
        self.assertFalse(picker.sending)
        self.assertIn('本轮已完成',picker.status.get())

    def test_view_zcode_opens_only_the_current_real_sid_once_without_mutating_send_receipt(self):
        picker,workspace=self.prepared_sender()
        picker.send_feedback.begin(picker._page_revision())
        picker.send_feedback.progress('native-processing','已打开',details={
            'transport':'desktop-native-bridge','nativeAccepted':True,'uiOpened':True,'sessionId':'sess_exact_navigation'})
        before=dict(picker.send_feedback.view()['result'])
        workers=[];calls=[];picker._background=workers.append
        class Bridge:
            def __init__(self,selected,discovery_path=None):calls.append(('input',selected,discovery_path))
            def open_session(self,session_id):calls.append(('open',session_id));return {'status':'opened'}
        picker.view_zcode_session();picker.view_zcode_session()
        self.assertEqual(len(workers),1)
        with patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=Bridge)}):workers[0]()
        self.assertEqual(picker.send_feedback.view()['result'],before)
        picker._drain()
        self.assertEqual(calls,[('input',picker.workspace_value,None),('open','sess_exact_navigation')])
        self.assertIn('原对话已在 ZCode 打开',picker.status.get())
        self.assertEqual(picker.send_feedback.view()['result'],before)
        self.assertTrue(picker.send_feedback.active)

    def test_view_zcode_rejects_missing_native_sid_and_reports_navigation_failure_without_resend(self):
        picker,_=self.prepared_sender()
        workers=[];picker._background=workers.append
        picker.view_zcode_session();self.assertEqual(workers,[])
        picker.send_feedback.begin(picker._page_revision())
        picker.send_feedback.progress('native-processing','已接收',details={
            'transport':'desktop-native-bridge','nativeAccepted':True,'uiOpened':False,'sessionId':'sess_not_opened'})
        picker.view_zcode_session()
        with patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=lambda *args,**kwargs:
                SimpleNamespace(open_session=lambda sid:{'status':'unavailable'}))}):workers[0]()
        picker._drain()
        self.assertIn('尚未确认打开',picker.status.get());self.assertIn('不要重新发送',picker.status.get())
        self.assertEqual(picker.send_feedback.view()['phase'],'native-processing')

    def test_close_keeps_install_and_legacy_guards_but_detaches_persisted_native_owner_without_cancel(self):
        picker,_=self.prepared_sender();workers=[];calls=[]
        picker._background=workers.append;picker._send_timer_job=None
        picker.root.destroy=lambda:calls.append('destroy')
        picker._disconnect_now=lambda:calls.append('local-disconnect')
        picker._bridge_install_active=True;picker.on_close()
        self.assertEqual(workers,[]);self.assertFalse(picker.closing)
        self.assertIn('安装',picker.status.get())
        picker._bridge_install_active=False;picker.sending=True
        picker.send_feedback.begin(picker._page_revision());picker.send_feedback.progress('running','legacy SDK')
        picker.on_close();self.assertEqual(workers,[])
        self.assertIn('尚未确认接纳',picker.status.get())
        picker.send_feedback.progress('native-processing','原生接纳',details={
            'transport':'desktop-native-bridge','nativeAccepted':True,'sessionId':'sess_detach_native','uiOpened':True})
        with patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=lambda *_a,**_k:self.fail('Close must not call native cancel/create/send'))}):
            picker.on_close();self.assertTrue(picker.closing);self.assertEqual(len(workers),1)
            workers[0]();picker._drain()
        self.assertEqual(calls,['local-disconnect','destroy'])
        self.assertTrue(picker.closed)

    def test_default_cli_is_auto_and_picker_accepts_executable_or_command_wrappers(self):
        self.assertEqual(app.default_zcode_path(), '')
        picker=SimpleNamespace(root=object(),settings_window=None,zcode_path=FakeValue(''),_log=lambda *_:None)
        for extension in ('exe','cmd','bat','cjs','mjs','js'):
            selected='C:/Installed/zcode-cli.'+extension
            with self.subTest(extension=extension),patch.object(app.filedialog,'askopenfilename',return_value=selected) as dialog:
                app.PickerApp.choose_zcode(picker)
            self.assertEqual(picker.zcode_path.get(),selected)
            self.assertIn('*.'+extension,str(dialog.call_args.kwargs['filetypes']))

    def test_backend_probe_runs_off_ui_and_displays_only_snapshot_and_public_diagnostics(self):
        picker,_=self.prepared_sender()
        picker.backend_probe_status=FakeValue('not checked')
        picker.settings_window=SimpleNamespace(winfo_exists=lambda:False)
        picker._settings_zcode_controls=(Widget(),Widget(),Widget())
        workers=[]
        picker._background=workers.append
        selected=picker.zcode_path.get()
        workspace=picker.workspace_value
        picker.probe_zcode_backend()
        self.assertTrue(picker._backend_probe_active)
        self.assertTrue(all(widget.options['state']=='disabled' for widget in picker._settings_zcode_controls))
        picker.probe_zcode_backend()
        self.assertEqual(len(workers),1)
        pending_message=picker.backend_probe_status.get()
        picker._show_backend_probe_result({'requestId':'obsolete','result':{'ok':False}})
        self.assertTrue(picker._backend_probe_active)
        picker.zcode_path.set('changed-cli')
        picker.workspace_value=workspace+'-changed'
        report={'ok':True,'phase':'ready','command':['node.exe','C:/CLI/zcode.cjs'],
                'runtimeType':'node-cli','cliVersion':'0.17.0','transport':'app-server-stdio',
                'message':'连接通过；未调用模型。','modelCalled':False,'indexWritten':False,'sessionCreated':False,
                'stderr':'PRIVATE_STDERR','sessions':[{'body':'PRIVATE_SESSION'}]}
        with patch.dict(sys.modules,{'zcode_diagnostics':SimpleNamespace(
                probe_backend=lambda **kwargs:(self.assertEqual(kwargs,{'commandPath':selected,'cwd':workspace,'timeout_seconds':12}) or report))}):
            workers[0]()
        self.assertEqual(picker.backend_probe_status.get(),pending_message,'Worker must not touch Tk variables')
        picker._drain()
        shown=picker.backend_probe_status.get()
        for expected in ('检测通过',selected,workspace,'node-cli','zcode.cjs','0.17.0','app-server-stdio'):
            self.assertIn(expected,shown)
        self.assertNotIn('changed-cli',shown)
        self.assertNotIn('PRIVATE_',shown)
        self.assertFalse(picker._backend_probe_active)
        self.assertFalse(picker.sending)
        self.assertFalse(picker.exporting)
        self.assertTrue(all(widget.options['state']=='normal' for widget in picker._settings_zcode_controls))

    def test_backend_probe_failure_and_closed_settings_keep_the_event_pump_alive(self):
        picker,_=self.prepared_sender()
        picker.backend_probe_status=FakeValue()
        def destroyed():raise app.tk.TclError('window destroyed')
        picker.settings_window=SimpleNamespace(winfo_exists=destroyed)
        with patch.dict(sys.modules,{'zcode_diagnostics':SimpleNamespace(
                probe_backend=lambda **kwargs:{'ok':False,'phase':'version','message':'无有效CLI版本。',
                                               'stderr':'PRIVATE_STDERR','sessions':['PRIVATE_SESSION']})}):
            picker.probe_zcode_backend()
        picker._drain()
        self.assertFalse(picker._backend_probe_active)
        self.assertIn('version',picker.backend_probe_status.get())
        self.assertIn('无有效CLI版本',picker.backend_probe_status.get())
        self.assertNotIn('PRIVATE_',picker.backend_probe_status.get())
        self.assertEqual(picker.root.scheduled[-1],(100,picker._drain))

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
        picker.endpoint = FakeValue('http://127.0.0.1:19333')
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
            self.assertEqual(report['version'], '0.3.1')
            self.assertEqual(report["handoffBackend"], "zcode-native-extension-host")
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
            self.assertEqual(report['defaultHandoffIntent'], 'learn-and-verify')
            self.assertEqual(report['actionPolicy'], 'infer-from-purpose')
            self.assertFalse(report['manualActionSelection'])
            self.assertEqual(report['backendSelection'],'path-cli-first')
            self.assertTrue(report['backendDiagnostic'])
            self.assertTrue(report['nativeBridgeBundled'])
            self.assertTrue(report['desktopBridgeApi'])
            self.assertEqual(report['modelCatalogSource'],'zcode-desktop-composer')
            self.assertTrue(report['persistentSettings'])
            self.assertEqual(report['requestedPermissionMode'],'yolo')
            self.assertEqual(report['permissionScope'],'native-session-request')
            self.assertFalse(report['nativePermissionVerified'])
            self.assertNotIn('permissionMode',report)
            self.assertTrue(report['headlessCdpTask'])
            self.assertTrue(report['cdpTaskScriptExists'])
            self.assertTrue(report['learningSkillExists'])

    def test_backend_check_is_headless_writes_json_and_never_creates_the_workspace(self):
        with tempfile.TemporaryDirectory(prefix='picker-backend-check-') as temporary:
            directory=Path(temporary)
            workspace=directory/'not-created'
            output=directory/'result.json'
            for ok,expected_exit in ((True,0),(False,2)):
                calls=[]
                def probe(**kwargs):
                    calls.append(kwargs)
                    return {'ok':ok,'phase':'ready' if ok else 'resolve','command':['fixture-cli'],
                            'modelCalled':False,'indexWritten':False,'sessionCreated':False}
                with self.subTest(ok=ok),patch.object(sys,'argv',['app.py','--backend-check',str(output),
                        '--workspace',str(workspace),'--zcode-cli','fixture-cli']),patch.dict(sys.modules,{
                        'zcode_diagnostics':SimpleNamespace(probe_backend=probe)}),patch.object(
                        app.tk,'Tk',side_effect=AssertionError('Backend check must not open UI')):
                    with self.assertRaises(SystemExit) as raised:app.main()
                self.assertEqual(raised.exception.code,expected_exit)
                self.assertEqual(calls,[{'commandPath':'fixture-cli','cwd':workspace}])
                report=json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(report['pickerVersion'],'0.3.1')
                self.assertIs(report['ok'],ok)
                self.assertFalse(workspace.exists())

    def test_backend_check_rejects_other_modes_and_missing_report_parent_before_probe(self):
        with tempfile.TemporaryDirectory(prefix='picker-backend-args-') as temporary:
            report=Path(temporary)/'result.json'
            invalid=[['--backend-check',str(report),mode,'fixture.json']
                     for mode in ('--cdp-task','--package-check','--smoke-test','--desktop-check')]
            invalid.append(['--backend-check',str(Path(temporary)/'missing'/'result.json')])
            for arguments in invalid:
                with self.subTest(arguments=arguments),patch.object(sys,'argv',['app.py',*arguments]),\
                     patch.dict(sys.modules,{'zcode_diagnostics':SimpleNamespace(
                         probe_backend=lambda **kwargs:self.fail('Invalid modes must not probe'))}),\
                     patch.object(app.tk,'Tk',side_effect=AssertionError('Invalid modes must not open UI')),\
                     redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:app.main()
                self.assertEqual(raised.exception.code,2)
            self.assertFalse(report.exists())
            self.assertFalse((Path(temporary)/'missing').exists())

    def test_desktop_check_only_reads_native_health_and_catalog_without_ui_or_authorization(self):
        with tempfile.TemporaryDirectory(prefix='picker-desktop-check-') as temporary:
            output=Path(temporary)/'native.json';workspace=Path(temporary)/'not-created'
            discovery=Path(temporary)/'explicit-bridge.json'
            calls=[]
            class Bridge:
                def __init__(self,selected,discovery_path=None):calls.append(('input',selected,discovery_path))
                def health(self):calls.append(('health',));return {'workspacePath':'C:/Native/Learning','applicationVersion':'3.18.2','token':'PRIVATE_TOKEN',
                    'nativeSessions':True,'authorized':False,'transport':'zcode-extension-host'}
                def list_models(self):calls.append(('models',));return [{'id':'native/model','providerId':'p','modelId':'m'}]
                def authorize_connection(self):raise AssertionError('Diagnostic must not request permissions')
            with patch.object(sys,'argv',['app.py','--desktop-check',str(output),'--desktop-bridge',str(discovery),
                    '--workspace',str(workspace)]),patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=Bridge)}),\
                 patch.object(app.tk,'Tk',side_effect=AssertionError('No UI allowed')):
                with self.assertRaises(SystemExit) as raised:app.main()
            self.assertEqual(raised.exception.code,0)
            report=json.loads(output.read_text(encoding='utf-8'))
            self.assertTrue(report['ok']);self.assertEqual(report['modelCount'],1)
            self.assertFalse(report['permissionRequested']);self.assertFalse(report['sessionCreated'])
            self.assertFalse(report['modelCalled']);self.assertNotIn('PRIVATE_',str(report))
            self.assertFalse(report['authorized']);self.assertTrue(report['nativeSessions'])
            self.assertIn('no-grant-request',report['permissionScope'])
            self.assertFalse(workspace.exists())
            self.assertEqual(calls,[('input',workspace,discovery),('health',),('models',)])

    def test_desktop_check_rejects_non_native_health_without_granting_or_reading_models(self):
        with tempfile.TemporaryDirectory(prefix='picker-desktop-negative-') as temporary:
            output=Path(temporary)/'native.json'
            bridge=SimpleNamespace(health=lambda:{'nativeSessions':False,'authorized':False,'transport':'zcode-extension-host'},
                list_models=lambda:self.fail('An incompatible host must not look ready'),
                authorize_connection=lambda:self.fail('Read-only diagnostic must never request grants'))
            with patch.object(sys,'argv',['app.py','--desktop-check',str(output),'--desktop-bridge','explicit.json',
                    '--workspace',temporary]),patch.dict(sys.modules,{'desktop_bridge':SimpleNamespace(DesktopBridge=lambda *a,**k:bridge)}),\
                 patch.object(app.tk,'Tk',side_effect=AssertionError('No UI permitted')):
                with self.assertRaises(SystemExit) as raised:app.main()
            self.assertEqual(raised.exception.code,2)
            result=json.loads(output.read_text(encoding='utf-8'))
            self.assertFalse(result['ok']);self.assertEqual(result['phase'],'incompatible')
            self.assertFalse(result['nativeSessions']);self.assertFalse(result['authorized'])

    def test_gui_cli_option_only_sets_the_initial_selection(self):
        root=SimpleNamespace(mainloop=lambda:None)
        controller=SimpleNamespace(zcode_path=FakeValue(''))
        native=SimpleNamespace(windll=SimpleNamespace(shcore=SimpleNamespace(SetProcessDpiAwareness=lambda *_:None),
            shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=lambda *_:None)))
        with patch.object(sys,'argv',['app.py','--zcode-cli','fixture-cli']),\
             patch.object(app.tk,'Tk',return_value=root),patch.object(app,'PickerApp',return_value=controller),\
             patch.dict(sys.modules,{'ctypes':native,'zcode_diagnostics':SimpleNamespace(
                 probe_backend=lambda **kwargs:self.fail('Opening GUI must not auto-probe'))}):
            app.main()
        self.assertEqual(controller.zcode_path.get(),'fixture-cli')

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

    def test_scoped_cdp_task_runs_before_tk_and_returns_a_protocol_exit_code(self):
        with tempfile.TemporaryDirectory(prefix='picker-headless-task-') as temporary:
            request = Path(temporary) / 'task.json'
            output = Path(temporary) / 'result.json'
            for status, expected_exit in (('completed', 0), ('blocked', 2), ('failed', 2)):
                calls = []

                def run_task(selected_request, selected_output):
                    calls.append((selected_request, selected_output))
                    selected_output.write_text(json.dumps({'status': status}), encoding='utf-8')
                    return {'status': status}

                with self.subTest(status=status), \
                     patch.object(sys, 'argv', ['app.py', '--cdp-task', str(request), '--output', str(output)]), \
                     patch.dict(sys.modules, {'cdp_task': SimpleNamespace(run_task=run_task)}), \
                     patch.object(app.tk, 'Tk', side_effect=AssertionError('Headless CDP task must not open UI')):
                    with self.assertRaises(SystemExit) as raised:
                        app.main()
                    self.assertEqual(raised.exception.code, expected_exit)
                    self.assertEqual(calls, [(request, output)])
                    self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['status'], status)

    def test_scoped_cdp_task_requires_output_and_orphan_output_cannot_open_ui(self):
        for arguments in (['--cdp-task', 'fixture-task.json'], ['--output', 'fixture-output.json']):
            with self.subTest(arguments=arguments), \
                 patch.object(sys, 'argv', ['app.py', *arguments]), \
                 patch.dict(sys.modules, {'cdp_task': SimpleNamespace(
                     run_task=lambda *_: self.fail('Invalid arguments must not execute tasks'))}), \
                 patch.object(app.tk, 'Tk', side_effect=AssertionError('Invalid CDP arguments must not open UI')), \
                 redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    app.main()
                self.assertEqual(raised.exception.code, 2)

    def test_runtime_command_reuses_the_frozen_executable_or_source_entrypoint(self):
        with patch.object(app.sys, 'frozen', True, create=True):
            self.assertEqual(app.cdp_runtime_command(), [str(Path(sys.executable).resolve())])
        with patch.object(app.sys, 'frozen', False, create=True):
            self.assertEqual(app.cdp_runtime_command(), [str(Path(sys.executable).resolve()),
                                                        str(Path(app.__file__).resolve())])

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
        payload = json.loads(Path(bundle['bundlePath']).read_text(encoding='utf-8'))
        self.assertEqual(payload['taskIntent']['mode'], 'learn-and-verify')
        self.assertEqual(payload['connection']['endpoint'], picker.endpoint.get())
        self.assertEqual(payload['connection']['targetId'], 'fixture-page')
        self.assertEqual(payload['runtime']['command'], app.cdp_runtime_command())
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

    def test_cdp_endpoint_or_current_target_changes_unlock_the_learning_scope_guard(self):
        picker, _ = self.prepared_sender()
        original = picker._page_revision()
        picker._send_state().begin(original)
        picker._send_state().complete({'status': 'registered', 'indexRegistered': True,
                                       'sessionId': 'sess_learning_scope_fixture'})
        endpoint = picker.endpoint.get()
        picker.endpoint.set('http://127.0.0.1:19334')
        changed_endpoint = picker._page_revision()
        self.assertNotEqual(changed_endpoint, original)
        self.assertTrue(picker._send_state().can_send(changed_endpoint))
        picker.endpoint.set(endpoint)
        self.assertEqual(picker._page_revision(), original)
        picker.pages[0]['id'] = 'another-current-target'
        changed_target = picker._page_revision()
        self.assertNotEqual(changed_target, original)
        self.assertTrue(picker._send_state().can_send(changed_target))

    def test_inferred_action_policy_cannot_reuse_a_manual_action_learning_receipt(self):
        picker, _ = self.prepared_sender()
        with patch.object(app.json, 'dumps', wraps=json.dumps) as serialize:
            current = picker._page_revision()
        current_identity = serialize.call_args.args[0]
        self.assertEqual(current_identity['taskIntent']['actionPolicy'], 'infer-from-purpose')
        legacy_identity = json.loads(json.dumps(current_identity))
        legacy_identity['taskIntent'].pop('actionPolicy')
        legacy = hashlib.sha256(json.dumps(legacy_identity, sort_keys=True,
                                          ensure_ascii=False).encode('utf-8')).hexdigest()
        picker._send_state().begin(legacy)
        picker._send_state().complete({'status': 'registered', 'indexRegistered': True,
                                       'sessionId': 'sess_manual_action_fixture'})
        self.assertNotEqual(current, legacy)
        self.assertFalse(picker._send_state().can_send(legacy))
        self.assertTrue(picker._send_state().can_send(current))
        picker._send_state().select(current)
        picker._controls()
        self.assertEqual(picker.send_button.options['state'], 'normal')

    def test_learning_guard_tracks_query_and_hash_on_the_explicit_target_only(self):
        picker,_=self.prepared_sender()
        original=picker._page_revision()
        picker._send_state().begin(original)
        picker._send_state().complete({'status':'registered','indexRegistered':True,
                                       'sessionId':'sess_original_url_fixture'})
        revisions={original}
        for suffix in ('?order=A#first','?order=B#first','?order=B#second'):
            picker.pages[0]['url']='https://example.test/orders'+suffix
            changed=picker._page_revision()
            self.assertNotIn(changed,revisions)
            self.assertTrue(picker._send_state().can_send(changed))
            revisions.add(changed)
        picker.pages.append({'id':'unselected','url':'https://example.test/orders?unselected=1'})
        self.assertEqual(picker._page_revision(),changed)
        picker.page_combo.current(-1)
        self.assertIsNone(picker._page_revision(),'An unselected list must not use its first target')
        picker.client=SimpleNamespace(connected=True)
        picker.current_target={'id':'connected','url':'https://example.test/orders?active=1'}
        connected=picker._page_revision()
        self.assertIsNotNone(connected)
        picker.pages[0]['url']='https://example.test/orders?inactive=2'
        self.assertEqual(picker._page_revision(),connected)

    def test_copy_exports_learning_instructions_without_constructing_a_model_backend(self):
        picker, workspace = self.prepared_sender()
        with patch.dict(sys.modules, {'zcode_handoff': SimpleNamespace(
                inspect_handoff=lambda *_: self.fail('Copy must not inspect model submissions'),
                ZCodeHandoff=lambda *_args, **_kwargs: self.fail('Copy must not construct model backend'))}):
            picker.export_page_task()
        picker._drain()
        self.assertIn('学习并实际验证', picker.root.clipboard)
        bundles = list((workspace.root / 'page-bundles').glob('*/page-bundle.json'))
        self.assertEqual(len(bundles), 1)
        payload = json.loads(bundles[0].read_text(encoding='utf-8'))
        self.assertEqual(payload['taskIntent']['mode'], 'learn-and-verify')
        self.assertEqual(payload['connection']['endpoint'], picker.endpoint.get())
        self.assertEqual(payload['connection']['targetId'], picker.pages[0]['id'])

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
        self.assertEqual(picker.send_button.options["text"], "交给 ZCode 学习")
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
