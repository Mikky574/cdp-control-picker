"""Background handoff tests use fake CLI and index; no model or user DB."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import Workspace, page_identity
from zcode_handoff import (HandoffError, ZCodeHandoff, inspect_handoff as native_inspect_handoff, read_handoff,
                           workspace_lock, _response_summary, _visible_controls_summary)
from desktop_bridge import DesktopBridgeError


def inspect_handoff(workspace, bundle, **kwargs):
    """The original test group explicitly injects the historical CLI backend."""
    return native_inspect_handoff(workspace, bundle, transport='app-server-stdio', **kwargs)


class FakeNativeBridge:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.authorized = True
        self.opened = True
        self.outcomes = ['running', 'completed']
        self.problem = None
        self.prompt = None
        self.on_wait = None

    def health(self):
        self.calls.append(('health', None))
        return {'nativeSessions': True, 'authorized': self.authorized,
                'transport': 'zcode-extension-host', 'workspacePath': str(self.root/'native-project')}

    def model_reference(self, selection):
        self.calls.append(('model', selection))
        return None if selection is None else selection['providerId']+'/'+selection['modelId']

    def create_session(self, prompt, **params):
        self.calls.append(('create', params))
        self.prompt = prompt
        session = {'kind':'app','sourceServerId':'local','workspacePath':str(self.root/'native-project'),
                   'sessionId':'sess_native_fixture'}
        self.created = {'requestId':params['request_id'],'creation':'created','firstInput':'accepted',
                        'delivery':'start_now','session':session,
                        'operation':{'session':session,'sessionId':session['sessionId'],'commandId':'command-fixture'}}
        if self.problem == 'optional-source':
            session.pop('sourceServerId')
        if self.problem == 'rejected':
            return {'requestId':params['request_id'],'creation':'rejected','firstInput':'rejected','delivery':'none'}
        if self.problem == 'unknown':
            return {**self.created,'creation':'unknown','firstInput':'unknown','operation':None}
        if self.problem == 'wrong-id':
            return {**self.created,'requestId':'another-request'}
        if self.problem in {'not-granted','not-declared','incompatible'}:
            raise DesktopBridgeError('fixture precondition',code=self.problem,definite_rejection=True)
        if self.problem in {'lost-ack','lost-ack-pending'}:
            raise DesktopBridgeError('fixture timeout',code='transport_unknown')
        return self.created

    def receipt(self, request_id):
        self.calls.append(('receipt', request_id))
        return ({'status':'accepted','result':self.created} if self.problem=='lost-ack' else {'status':'pending'})

    def open_session(self, session_id):
        self.calls.append(('open', session_id))
        return {'status':'opened' if self.opened else 'unavailable'}

    def wait_operation(self, operation, **kwargs):
        self.calls.append(('wait', operation))
        if self.on_wait:
            self.on_wait()
        outcome = self.outcomes.pop(0) if len(self.outcomes)>1 else self.outcomes[0]
        row = {'operation':operation,'epoch':'fixture-epoch','revision':2,'status':outcome}
        if self.problem == 'wrong-operation':
            row['operation']={**operation,'commandId':'another-command'}
        elif self.problem == 'legacy-flat-wait':
            row = {**operation,'epoch':'fixture-epoch','revision':2,'status':outcome}
        return {'status':'completed' if outcome in {'completed','failed','interrupted'} else 'timed_out', 'operations':[row]}

    def read_session(self, session_id):
        self.calls.append(('read', session_id))
        return {'view':'messages','session':self.created['session'], 'messages':[
            {'id':'user-1','role':'user','text':self.prompt},
            {'id':'assistant-1','role':'assistant','text':'已收到本页控件，等待补充。'},
            {'id':'user-2','role':'user','text':'A later unrelated user turn'},
            {'id':'assistant-2','role':'assistant','text':'UNRELATED_REPLY_MUST_NOT_APPEAR'}]}


class NativeDesktopHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='picker-native-handoff-')
        self.root=Path(self.temp.name)
        self.workspace=Workspace(self.root)
        self.workspace.save_capture({'page':{'url':'https://example.test/orders'},
            'control':{'tagName':'button','text':'查询'},'annotation':{'description':'查询订单'}})
        self.bundle=self.workspace.export_page_bundle('https://example.test/orders')
        self.bridge=FakeNativeBridge(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def handoff(self, **kwargs):
        return ZCodeHandoff(self.root, bridge_factory=lambda *args,**values:self.bridge,
                            timeout_seconds=1,**kwargs)

    def test_host_accepts_and_opens_before_wait_without_cli_or_private_index(self):
        events=[]
        before=Path(self.bundle['bundlePath']).read_bytes()
        def during_wait():
            early=[event for event in events if event['phase']=='native-processing']
            self.assertGreaterEqual(len(early),2)
            self.assertFalse(early[0]['uiOpened'])
            self.assertTrue(early[1]['uiOpened'])
            self.assertEqual(early[1]['sessionId'],'sess_native_fixture')
            state=read_handoff(self.root,early[1]['requestId'])
            self.assertEqual(state['status'],'native-processing')
            self.assertTrue(state['nativeAccepted'])
        self.bridge.on_wait=during_wait
        with patch('zcode_handoff.resolve_command',side_effect=AssertionError('native must not discover CLI')):
            result=self.handoff().send(self.bundle,events.append)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['nativeCompleted'])
        self.assertTrue(result['uiOpened'])
        self.assertNotIn('indexRegistered',result)
        self.assertIn(str(Path(self.bundle['contextPath'])),self.bridge.prompt)
        self.assertNotEqual(result['workspace'],result['nativeWorkspace'])
        self.assertNotIn('UNRELATED_REPLY',result['responseSummary'])
        self.assertEqual(Path(self.bundle['bundlePath']).read_bytes(),before)
        methods=[call[0] for call in self.bridge.calls]
        self.assertLess(methods.index('create'),methods.index('open'))
        self.assertLess(methods.index('open'),methods.index('wait'))
        self.assertEqual(methods.count('create'),1)

    def test_native_identity_does_not_reuse_legacy_cli_receipt_and_exact_repeat_is_cached(self):
        legacy_fingerprint=inspect_handoff(self.root,self.bundle)['fingerprint']
        result=self.handoff().send(self.bundle)
        self.assertNotEqual(result['fingerprint'],legacy_fingerprint)
        view=native_inspect_handoff(self.root,self.bundle)
        self.assertFalse(view['canSubmit'])
        self.assertEqual(view['previous']['sessionId'],result['sessionId'])
        before=len(self.bridge.calls)
        repeated=self.handoff().send(self.workspace.export_page_bundle('https://example.test/orders'))
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(len(self.bridge.calls),before)

    def test_model_project_and_selected_connection_enter_native_content_identity(self):
        base=native_inspect_handoff(self.root,self.bundle)['fingerprint']
        selected={'providerId':'demo','modelId':'model/with/slash'}
        model=native_inspect_handoff(self.root,self.bundle,modelSelection=selected)['fingerprint']
        connection=native_inspect_handoff(self.root,self.bundle,discovery_path=self.root/'other.json')['fingerprint']
        project=native_inspect_handoff(self.root,self.bundle,project_ref={'kind':'app','sourceServerId':'local','projectId':'project-2'})['fingerprint']
        self.assertEqual(len({base,model,connection,project}),4)
        result=self.handoff(modelSelection=selected).send(self.bundle)
        self.assertEqual(result['fingerprint'],model)
        created=next(value for method,value in self.bridge.calls if method=='create')
        self.assertEqual(created['model'],'demo/model/with/slash')
        self.assertNotIn('demo/model',self.bridge.prompt)
        self.assertEqual(read_handoff(self.root,result['requestId'])['modelSelection'],selected)

    def test_missing_native_authorization_fails_before_mutation_and_can_retry(self):
        self.bridge.authorized=False
        result=self.handoff().send(self.bundle)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['submissionAttempted'])
        self.assertFalse(any(method=='create' for method,_ in self.bridge.calls))
        self.bridge.authorized=True
        self.assertEqual(self.handoff().send(self.bundle)['status'],'completed')

    def test_unknown_partial_receipt_keeps_true_sid_and_never_resubmits(self):
        self.bridge.problem='unknown'
        result=self.handoff().send(self.bundle)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(result['sessionId'],'sess_native_fixture')
        self.assertFalse(result['nativeAccepted'])
        self.assertTrue(self.handoff().send(self.bundle)['duplicate'])
        self.assertEqual(sum(method=='create' for method,_ in self.bridge.calls),1)

    def test_lost_create_ack_uses_readonly_durable_receipt_without_second_create(self):
        self.bridge.problem='lost-ack'
        result=self.handoff().send(self.bundle)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(sum(method=='create' for method,_ in self.bridge.calls),1)
        self.assertEqual(sum(method=='receipt' for method,_ in self.bridge.calls),1)

    def test_pending_receipt_or_wrong_operation_remains_unknown_not_model_success(self):
        for problem in ('lost-ack-pending','wrong-operation','wrong-id'):
            with self.subTest(problem=problem):
                self.bridge.problem=problem
                result=self.handoff(discovery_path=self.root/(problem+'.json')).send(self.bundle)
                self.assertEqual(result['status'],'unknown')
                self.assertFalse(result['nativeCompleted'])
                self.assertTrue(self.handoff(discovery_path=self.root/(problem+'.json')).send(self.bundle)['duplicate'])

    def test_open_unavailable_cannot_be_reported_as_successful_visible_handoff(self):
        self.bridge.opened=False
        result=self.handoff().send(self.bundle)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['nativeCompleted'])
        self.assertFalse(result['uiOpened'])
        self.assertTrue(self.handoff().send(self.bundle)['duplicate'])

    def test_wait_outer_completed_does_not_mask_failed_inner_operation(self):
        self.bridge.outcomes=['failed']
        result=self.handoff().send(self.bundle)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['nativeCompleted'])
        self.assertEqual(result['nativeOutcome'],'failed')
        self.assertTrue(result['nativeAccepted'])

    def test_old_incorrect_flat_wait_shape_is_never_native_completion(self):
        self.bridge.problem='legacy-flat-wait'
        result=self.handoff().send(self.bundle)
        self.assertEqual(result['status'],'unknown')
        self.assertFalse(result['nativeCompleted'])
        self.assertTrue(self.handoff().send(self.bundle)['duplicate'])
        self.assertEqual(sum(method=='create' for method,_ in self.bridge.calls),1)

    def test_official_optional_local_source_id_is_not_an_extra_compatibility_gate(self):
        self.bridge.problem='optional-source'
        result=self.handoff(project_ref={'kind':'app','projectId':'fixture-project'}).send(self.bundle)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['nativeCompleted'])

    def test_explicit_native_precondition_rejection_is_retryable_without_replaying_accepted_input(self):
        self.bridge.problem='rejected'
        first=self.handoff().send(self.bundle)
        self.assertEqual(first['status'],'failed')
        self.assertFalse(first['submissionAttempted'])
        self.bridge.problem=None
        second=self.handoff().send(self.bundle)
        self.assertEqual(second['status'],'completed')
        self.assertNotEqual(first['requestId'],second['requestId'])

    def test_gateway_write_preconditions_remain_retryable_even_if_read_health_was_authorized(self):
        for problem in ('not-granted','not-declared','incompatible'):
            with self.subTest(problem=problem):
                self.bridge.problem=problem
                result=self.handoff(discovery_path=self.root/(problem+'.json')).send(self.bundle)
                self.assertEqual(result['status'],'failed')
                self.assertFalse(result['submissionAttempted'])
                self.assertFalse(result['nativeAccepted'])
                self.bridge.problem=None
                retried=self.handoff(discovery_path=self.root/(problem+'.json')).send(self.bundle)
                self.assertEqual(retried['status'],'completed')

    def test_first_visible_message_contains_scrubbed_page_and_annotated_controls(self):
        self.workspace.save_capture({'page':{'url':'https://example.test/orders','title':'订单查询页面'},
            'control':{'tagName':'input'},'annotation':{'name':'订单号输入框','description':'输入订单编号后查询'}})
        bundle=self.workspace.export_page_bundle('https://example.test/orders')
        path=Path(bundle['bundlePath']); payload=json.loads(path.read_text(encoding='utf-8'))
        payload['page']['url']='https://user:pass@example.test/orders?token=PRIVATE_QUERY#secret'
        payload['captures'][0]['capture']['annotation'].update(name='查询按钮',description='查看订单结果 api_key=PRIVATE_LITERAL')
        path.write_text(json.dumps(payload),encoding='utf-8')
        result=self.handoff().send(bundle)
        prompt=self.bridge.prompt
        self.assertEqual(result['status'],'completed')
        self.assertIn('页面：订单查询页面',prompt)
        self.assertIn('地址：https://example.test/orders',prompt)
        self.assertIn('查询按钮：查看订单结果',prompt)
        self.assertIn('订单号输入框：输入订单编号后查询',prompt)
        self.assertIn('只是控件罗列，用户未声明操作先后',prompt)
        self.assertNotIn('PRIVATE_QUERY',prompt)
        self.assertNotIn('PRIVATE_LITERAL',prompt)
        self.assertNotIn('user:pass',prompt)
        self.assertIn('等待我补充',prompt)
        self.assertLessEqual(len(prompt),3500)

    def test_declared_sequence_is_visible_and_uses_exact_capture_order(self):
        payload={'page':{'origin':'https://example.test','path':'/orders'},'captures':[
            {'captureId':'one','capture':{'annotation':{'name':'输入条件','description':'输入订单号'}}},
            {'captureId':'two','capture':{'annotation':{'name':'查询','description':'查看结果'}}}],
            'sequence':{'captureIds':['two','one'],'ordered':True}}
        summary=_visible_controls_summary(payload)
        self.assertIn('用户声明期望按下列顺序操作',summary)
        self.assertIn('尚未执行验证',summary)
        self.assertLess(summary.index('1. 查询'),summary.index('2. 输入条件'))
        payload['sequence']['ordered']=False
        self.assertIn('只是控件罗列',_visible_controls_summary(payload))
        self.assertNotIn('用户声明期望按下列顺序操作',_visible_controls_summary(payload))

    def test_visible_summary_caps_twelve_controls_each_purpose_and_entire_message(self):
        payload={'page':{'title':'长页面标题'*80,'origin':'https://example.test','path':'/orders'},
                 'captures':[{'captureId':str(i),'capture':{'annotation':{
                    'name':f'控件名称{i}'+('很长名称'*30),'description':('用途摘要'*80)+'OMITTED_TAIL'}}} for i in range(20)]}
        summary=_visible_controls_summary(payload)
        rows=[line for line in summary.splitlines() if line[:1].isdigit()]
        self.assertLessEqual(len(summary),3500)
        self.assertEqual(len(rows),12)
        self.assertTrue(all(len(row.split('：',1)[1])<=180 for row in rows))
        self.assertNotIn('OMITTED_TAIL',summary)
        self.assertNotIn('13. ',summary)
        self.assertIn('其余控件',summary)


class FakeIndex:
    def __init__(self):
        self.preflights = 0
        self.calls = []
        self.failure = False

    def preflight(self):
        self.preflights += 1
        if self.failure == 'preflight':
            raise ValueError('unsupported schema')
        return {'compatible': True}

    def register(self, session_id, **kwargs):
        self.calls.append((session_id, kwargs))
        if self.failure:
            raise ValueError('fixture database locked')
        return {'sessionId': session_id, 'taskId': session_id, 'projectId': 'fixture-project',
                'indexRegistered': True, 'mode': kwargs['mode']}


class FakeServer:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.bindings = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def bind(self, session_id=None):
        self.bindings.append(session_id)
        return {'session': {'sessionId': session_id or 'sess-fixture'},
                'settings': {'permission': {'mode': 'yolo'}}}

    def prompt(self, session_id, content, **kwargs):
        self.calls.append({'sessionId': session_id, 'content': content, **kwargs})
        if self.result is None:
            raise TimeoutError('fixture native turn timed out')
        if self.result.get('status') != 'completed':
            raise ValueError('fixture native failure or unconfirmed result')
        messages = self.result.get('messages', [
            {'info': {'role': 'user', 'sessionId': session_id},
             'parts': [{'type': 'text', 'text': content, 'sessionId': session_id}]},
            {'info': {'role': 'assistant', 'sessionId': session_id},
             'parts': [{'type': 'text', 'text': self.result.get('response', ''), 'sessionId': session_id}]}])
        return {'session': {'sessionId': self.result.get('sessionId')}, 'messages': messages,
                'settings': {'permission': {'mode': 'yolo'}}}


class BackgroundHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='picker-cli-handoff-')
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root)
        for label in ('输入条件', '查询订单'):
            self.workspace.save_capture({'page': {'url': 'https://example.test/orders'},
                'control': {'tagName': 'button', 'text': label},
                'annotation': {'description': label, 'allowedActions': ['read']}})
        self.bundle = self.workspace.export_page_bundle('https://example.test/orders')
        self.index = FakeIndex()
        self.server = FakeServer({'status': 'completed', 'sessionId': 'sess-fixture', 'response': '已收到本页两个控件的上下文，等待补充。'})

    def tearDown(self):
        self.temporary.cleanup()

    def handoff(self):
        return ZCodeHandoff(self.root, commandPath=[sys.executable], index_adapter=self.index,
                            server_factory=lambda **kwargs: self.server, timeout_seconds=1)

    def learning_bundle(self, bundle=None):
        bundle = self.bundle if bundle is None else bundle
        path = Path(bundle['bundlePath'])
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['taskIntent'] = {'mode': 'learn-and-verify', 'contractVersion': 1}
        payload['connection'] = {'endpoint': 'http://127.0.0.1:9222', 'targetId': 'fixture-target',
                                 'page': page_identity('https://example.test/orders'),
                                 'initialUrlSha256': hashlib.sha256(b'https://example.test/orders').hexdigest()}
        payload['runtime'] = {'command': [sys.executable, 'fixture-runtime.py']}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        Path(bundle['contextPath']).write_text('Read the scoped fixture learning skill and validate.', encoding='utf-8')
        return bundle

    def test_background_result_registers_actual_session_and_keeps_original_bundle(self):
        before = Path(self.bundle['bundlePath']).read_bytes()
        events = []
        result = self.handoff().send(self.bundle, events.append)
        self.assertEqual(result['status'], 'registered')
        self.assertTrue(result['indexRegistered'])
        self.assertNotIn('uiVisible', result)
        self.assertEqual(self.index.calls[0][0], 'sess-fixture')
        self.assertEqual(self.index.calls[0][1]['task_path'], self.bundle['taskPath'])
        self.assertEqual(self.index.calls[0][1]['mode'], 'yolo')
        self.assertEqual(result['mode'], 'yolo')
        self.assertIn('2 个已标注控件', self.server.calls[0]['content'])
        self.assertEqual(Path(self.bundle['bundlePath']).read_bytes(), before)
        self.assertEqual([event['phase'] for event in events], ['running', 'registering'])

    def test_schema_failure_happens_before_model_start(self):
        self.index.failure = 'preflight'
        with self.assertRaisesRegex(ValueError, 'unsupported schema'):
            self.handoff().send(self.bundle)
        self.assertEqual(self.server.calls, [])

    def test_yolo_label_with_active_planning_flag_does_not_submit_prompt(self):
        self.server.bind = lambda: {'session': {'sessionId': 'sess-fixture'},
            'settings': {'permission': {'mode': 'yolo'}, 'mode': {'current': 'plan'}}}
        result = self.handoff().send(self.bundle)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['submissionAttempted'])
        self.assertEqual(result['sessionId'], 'sess-fixture')
        self.assertEqual(self.server.calls, [])

    def test_bind_failure_before_send_allows_explicit_retry_without_duplicate_model_call(self):
        original = self.server.bind
        self.server.bind = lambda: (_ for _ in ()).throw(ValueError('fixture bind failure'))
        first = self.handoff().send(self.bundle)
        self.assertEqual(first['status'], 'failed')
        self.assertFalse(first['submissionAttempted'])
        self.server.bind = original
        second = self.handoff().send(self.bundle)
        self.assertEqual(second['status'], 'registered')
        self.assertTrue(self.handoff().send(self.bundle)['duplicate'])
        self.assertEqual(len(self.server.calls), 1)

    def test_mismatched_index_receipt_cannot_report_registration(self):
        self.index.register = lambda *args, **kwargs: {'indexRegistered': True,
            'sessionId': 'another-session', 'taskId': 'another-session'}
        result = self.handoff().send(self.bundle)
        self.assertEqual(result['status'], 'index_failed')
        self.assertFalse(result['indexRegistered'])
        self.assertEqual(result['sessionId'], 'sess-fixture')

    def test_same_content_new_export_never_submits_second_model_task(self):
        first = self.handoff().send(self.bundle)
        second = self.handoff().send(self.workspace.export_page_bundle('https://example.test/orders'))
        self.assertEqual(first['sessionId'], second['sessionId'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(len(self.server.calls), 1)

    def test_learning_intent_upgrades_legacy_receipt_without_visible_request_marker(self):
        legacy = self.handoff().send(self.bundle)
        legacy_prompt = self.server.calls[-1]['content']
        self.assertNotIn('【CDP:', legacy_prompt)
        self.assertNotIn(str(self.root), legacy_prompt)
        self.assertIn('工作区内', legacy_prompt)
        self.learning_bundle()
        view = inspect_handoff(self.workspace, self.bundle)
        self.assertTrue(view['canSubmit'])
        self.assertFalse(view['duplicate'])
        self.assertEqual(view['handoffIntent'], 'learn-and-verify')
        learned = self.handoff().send(self.bundle)
        self.assertEqual(learned['status'], 'registered')
        self.assertEqual(learned['handoffIntent'], 'learn-and-verify')
        self.assertNotEqual(legacy['fingerprint'], learned['fingerprint'])
        self.assertEqual(len(self.server.calls), 2)
        prompt = self.server.calls[-1]['content']
        self.assertIn('请学习并验证本页 2 个已标注控件', prompt)
        self.assertIn('其中指定的技能', prompt)
        self.assertIn('连接已有 Chrome', prompt)
        self.assertIn('真实验证结果和可复用能力', prompt)
        self.assertNotIn('等待我补充', prompt)
        self.assertNotIn('【CDP:', prompt)
        self.assertNotIn(learned['requestId'], prompt)
        self.assertNotIn(str(self.root), prompt)
        self.assertIn(Path(self.bundle['contextPath']).relative_to(self.root).as_posix(), prompt)
        self.assertEqual(self.server.calls[0]['input_id'], legacy['requestId'])
        self.assertEqual(self.server.calls[1]['input_id'], learned['requestId'])
        self.assertNotEqual(legacy['requestId'], learned['requestId'])
        receipt = read_handoff(self.root, learned['requestId'])
        self.assertEqual(receipt['handoffIntent'], 'learn-and-verify')
        request = json.loads((Path(learned['journalPath']) / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['contextPath'], self.bundle['contextPath'])

    def test_inferred_policy_has_its_own_handoff_identity_and_saved_receipt(self):
        self.learning_bundle()
        legacy = self.handoff().send(self.bundle)
        path = Path(self.bundle['bundlePath'])
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['taskIntent']['actionPolicy'] = 'infer-from-purpose'
        path.write_text(json.dumps(payload), encoding='utf-8')
        view = inspect_handoff(self.root, self.bundle)
        self.assertTrue(view['canSubmit'])
        self.assertEqual(view['actionPolicy'], 'infer-from-purpose')
        self.assertNotEqual(view['fingerprint'], legacy['fingerprint'])
        current = self.handoff().send(self.bundle)
        self.assertEqual(current['actionPolicy'], 'infer-from-purpose')
        self.assertNotEqual(current['requestId'], legacy['requestId'])
        self.assertEqual(len(self.server.calls), 2)
        self.assertEqual(read_handoff(self.root, current['requestId'])['actionPolicy'], 'infer-from-purpose')
        self.assertTrue(self.handoff().send(self.bundle)['duplicate'])
        self.assertEqual(len(self.server.calls), 2)

    def test_unknown_action_policy_stops_before_index_or_model(self):
        self.learning_bundle()
        path = Path(self.bundle['bundlePath'])
        payload = json.loads(path.read_text(encoding='utf-8'))
        for policy in (None, True, [], 'allow-everything'):
            with self.subTest(policy=policy):
                payload['taskIntent']['actionPolicy'] = policy
                path.write_text(json.dumps(payload), encoding='utf-8')
                with self.assertRaises(HandoffError):
                    self.handoff().send(self.bundle)
        self.assertEqual(self.index.preflights, 0)
        self.assertEqual(self.server.calls, [])

    def test_only_electron_bundled_cli_identifies_a_desktop_index_executable(self):
        calls = []
        fake_module = SimpleNamespace(DesktopTaskIndex=lambda workspace, desktop_path=None:
                                      calls.append(desktop_path) or self.index)
        native = self.root / 'standalone' / 'ZCode.exe'
        native.parent.mkdir()
        native.write_bytes(b'fixture native CLI')
        desktop = self.root / 'desktop' / 'ZCode.exe'
        bundle = desktop.parent / 'resources' / 'glm' / 'zcode.cjs'
        bundle.parent.mkdir(parents=True)
        desktop.write_bytes(b'fixture Electron desktop')
        bundle.write_text('// fixture bundled CLI', encoding='utf-8')
        with patch.dict(sys.modules, {'desktop_index': fake_module}):
            for command, expected in (([str(native)], None),
                                      ([str(desktop), str(bundle)], str(desktop.resolve()))):
                with self.subTest(command=command), patch('zcode_handoff.resolve_command', return_value=command):
                    ZCodeHandoff(self.root, commandPath='fixture-selected-entry', server_factory=lambda **kwargs: self.server)
                    self.assertEqual(calls[-1], expected)
        self.assertEqual(self.server.calls, [])

    def test_index_adapter_failure_is_distinct_from_cli_connection_and_precedes_model(self):
        def unavailable(*args, **kwargs):
            raise ValueError('fixture desktop index version unavailable')
        with patch.dict(sys.modules, {'desktop_index': SimpleNamespace(DesktopTaskIndex=unavailable)}), \
             patch('zcode_handoff.resolve_command', return_value=[sys.executable]):
            with self.assertRaisesRegex(HandoffError, 'CLI入口已解析，但桌面对话索引适配不可用；尚未发送模型任务'):
                ZCodeHandoff(self.root, commandPath='fixture-independent-cli', server_factory=lambda **kwargs: self.server)
        self.assertEqual(self.server.bindings, [])
        self.assertEqual(self.server.calls, [])

    def test_learning_scope_changes_fingerprint_but_runtime_location_does_not(self):
        self.learning_bundle()
        path = Path(self.bundle['bundlePath'])
        original = json.loads(path.read_text(encoding='utf-8'))

        def fingerprint(payload):
            path.write_text(json.dumps(payload), encoding='utf-8')
            return self.handoff()._bundle(self.bundle)[-1]

        baseline = fingerprint(original)
        for field, value in (
                ('endpoint', 'http://127.0.0.1:9333'), ('targetId', 'another-selected-target'),
                ('page', page_identity('https://example.test/another-page')),
                ('initialUrlSha256', hashlib.sha256(b'changed-query-scope').hexdigest())):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(original))
                changed['connection'][field] = value
                self.assertNotEqual(fingerprint(changed), baseline)
        changed = json.loads(json.dumps(original))
        changed['runtime']['command'] = [r'D:\Different Local Install\ZCode.exe', 'another-runtime.py']
        changed['connection']['diagnosticTimestamp'] = 'does-not-change-execution-scope'
        changed['createdAt'] = '2099-01-01T00:00:00Z'
        self.assertEqual(fingerprint(changed), baseline)
        explicit_receipt = json.loads(json.dumps(original))
        explicit_receipt['taskIntent']['mode'] = 'context-only'
        self.assertNotEqual(fingerprint(explicit_receipt), baseline)

    def test_identical_learning_export_keeps_one_submission(self):
        first = self.handoff().send(self.learning_bundle())
        newer = self.learning_bundle(self.workspace.export_page_bundle('https://example.test/orders'))
        second = self.handoff().send(newer)
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['requestId'], second['requestId'])
        self.assertEqual(len(self.server.calls), 1)

    def test_learning_requires_current_contract_connection_and_context_before_model(self):
        self.learning_bundle()
        path = Path(self.bundle['bundlePath'])
        original = json.loads(path.read_text(encoding='utf-8'))
        invalid = []
        for field, value in (('contractVersion', 2), ('contractVersion', True), ('mode', 'execute-anything')):
            changed = json.loads(json.dumps(original))
            changed['taskIntent'][field] = value
            invalid.append(changed)
        for field, value in (('endpoint', ''), ('endpoint', 'https://remote.example.test:9222'),
                             ('targetId', ''), ('page', {}), ('initialUrlSha256', 'not-a-sha256')):
            changed = json.loads(json.dumps(original))
            changed['connection'][field] = value
            invalid.append(changed)
        changed = json.loads(json.dumps(original))
        changed.pop('connection')
        invalid.append(changed)
        for number, payload in enumerate(invalid):
            with self.subTest(case=number):
                path.write_text(json.dumps(payload), encoding='utf-8')
                with self.assertRaises(HandoffError):
                    self.handoff().send(self.bundle)
        path.write_text(json.dumps(original), encoding='utf-8')
        with self.assertRaisesRegex(HandoffError, '缺少上下文'):
            self.handoff().send({**self.bundle, 'contextPath': None})
        self.assertEqual(self.index.preflights, 0)
        self.assertEqual(self.server.bindings, [])
        self.assertEqual(self.server.calls, [])

    def test_legacy_without_context_keeps_receipt_fallback_and_protocol_input_id(self):
        result = self.handoff().send({**self.bundle, 'contextPath': None})
        self.assertEqual(result['status'], 'registered')
        self.assertEqual(result['handoffIntent'], 'context-only')
        self.assertEqual(self.server.calls[0]['input_id'], result['requestId'])
        self.assertNotIn('【CDP:', self.server.calls[0]['content'])
        self.assertIn('等待我补充', self.server.calls[0]['content'])
        self.assertTrue(Path(result['contextPath']).is_file())

    def test_read_only_inspection_needs_no_cli_index_or_file_creation(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        with patch('zcode_handoff.resolve_command', side_effect=AssertionError('No CLI discovery')):
            view = inspect_handoff(self.workspace, self.bundle)
            self.assertIsNone(read_handoff(self.root, 'handoff-' + '0' * 20))
        self.assertTrue(view['canSubmit'])
        self.assertFalse(view['duplicate'])
        self.assertFalse(view['canRegister'])
        self.assertIsNone(view['previous'])
        self.assertEqual(view['captureCount'], 2)
        self.assertFalse((self.root / '.handoffs').exists())
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                  for path in self.root.rglob('*') if path.is_file()})
        self.assertEqual(self.index.preflights, 0)
        self.assertEqual(self.index.calls, [])
        self.assertEqual(self.server.calls, [])

    def test_registered_duplicate_returns_original_summary_without_model_or_index_write(self):
        first = self.handoff().send(self.bundle)
        state_path = Path(first['journalPath']) / 'state.json'
        before = state_path.read_bytes()
        events = []
        second = self.handoff().send(self.bundle, events.append)
        self.assertTrue(second['duplicate'])
        self.assertIn('未重复提交', second['message'])
        self.assertEqual(second['responseSummary'], first['responseSummary'])
        self.assertEqual(second['requestId'], first['requestId'])
        self.assertEqual(self.server.bindings, [None])
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(len(self.index.calls), 1)
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual([event['phase'] for event in events], ['already_sent'])
        with patch('zcode_handoff.resolve_command', side_effect=AssertionError('No CLI discovery')):
            view = inspect_handoff(self.root, self.workspace.export_page_bundle('https://example.test/orders'))
            receipt = read_handoff(self.root, first['requestId'])
        self.assertFalse(view['canSubmit'])
        self.assertTrue(view['duplicate'])
        self.assertEqual(view['previous']['sessionId'], 'sess-fixture')
        self.assertEqual(receipt['responseSummary'], first['responseSummary'])
        self.assertNotIn('indexReceipt', receipt)
        self.assertNotIn('binding', receipt)

    def test_inspection_keeps_unknown_blocked_and_index_retry_distinct(self):
        self.server.result = None
        failed = self.handoff().send(self.bundle)
        view = inspect_handoff(self.root, self.bundle)
        self.assertEqual(view['previous']['status'], 'unknown')
        self.assertFalse(view['canSubmit'])
        self.assertFalse(view['canRegister'])
        self.assertTrue(view['duplicate'])
        self.assertEqual(view['previous']['requestId'], failed['requestId'])
        self.assertEqual(len(self.server.calls), 1)
        # A different content identity gets an independent, fake-only task.
        payload_path = Path(self.bundle['bundlePath'])
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
        payload['captures'][0]['capture']['annotation']['description'] += ' changed'
        payload_path.write_text(json.dumps(payload), encoding='utf-8')
        self.server.result = {'status': 'completed', 'sessionId': 'sess-fixture', 'response': '已收到'}
        self.index.failure = True
        first = self.handoff().send(self.bundle)
        view = inspect_handoff(self.root, self.bundle)
        self.assertEqual(view['previous']['status'], 'index_failed')
        self.assertTrue(view['canRegister'])
        self.assertFalse(view['canSubmit'])
        self.assertEqual(view['previous']['sessionId'], first['sessionId'])
        self.assertEqual(len(self.server.calls), 2)

    def test_inspection_reports_latest_pre_submit_failure_by_time_not_random_id(self):
        fingerprint = inspect_handoff(self.root, self.bundle)['fingerprint']
        for suffix, created in (('f' * 20, '2026-01-01T00:00:00+00:00'),
                                ('a' * 20, '2026-01-02T00:00:00+00:00')):
            directory = self.root / '.handoffs' / ('handoff-' + suffix)
            directory.mkdir(parents=True)
            state = {'requestId': directory.name, 'fingerprint': fingerprint,
                     'status': 'failed', 'submissionAttempted': False, 'createdAt': created,
                     'error': 'not admitted', 'privatePrompt': 'PRIVATE_FIXTURE_MUST_NOT_RETURN'}
            (directory / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        view = inspect_handoff(self.root, self.bundle)
        self.assertTrue(view['canSubmit'])
        self.assertFalse(view['duplicate'])
        self.assertEqual(view['previous']['requestId'], 'handoff-' + 'a' * 20)
        self.assertNotIn('PRIVATE_FIXTURE_MUST_NOT_RETURN', json.dumps(view))
        with self.assertRaises(HandoffError):
            read_handoff(self.root, '../outside')

    def test_inspection_prioritizes_real_blocking_receipt_over_newer_pre_submit_failure(self):
        fingerprint = inspect_handoff(self.root, self.bundle)['fingerprint']
        old_id, new_id = 'handoff-' + 'f' * 20, 'handoff-' + 'a' * 20
        old_directory = self.root / '.handoffs' / old_id
        new_directory = self.root / '.handoffs' / new_id
        old_directory.mkdir(parents=True)
        new_directory.mkdir()
        (new_directory / 'state.json').write_text(json.dumps({
            'requestId': new_id, 'fingerprint': fingerprint, 'status': 'failed',
            'submissionAttempted': False, 'createdAt': '2026-01-02T00:00:00+00:00'}), encoding='utf-8')
        for status in ('registered', 'unknown'):
            with self.subTest(blocking_status=status):
                (old_directory / 'state.json').write_text(json.dumps({
                    'requestId': old_id, 'fingerprint': fingerprint, 'status': status,
                    'submissionAttempted': True, 'sessionId': 'sess-original',
                    'indexRegistered': status == 'registered', 'responseSummary': '之前的原会话回执',
                    'createdAt': '2026-01-01T00:00:00+00:00'}), encoding='utf-8')
                view = inspect_handoff(self.root, self.bundle)
                self.assertFalse(view['canSubmit'])
                self.assertTrue(view['duplicate'])
                self.assertEqual(view['previous']['requestId'], old_id)
                self.assertEqual(view['previous']['sessionId'], 'sess-original')
                self.assertEqual(view['previous']['status'], status)
                self.assertEqual(view['previous']['responseSummary'], '之前的原会话回执')
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.index.calls, [])
        self.assertEqual(self.index.preflights, 0)

    def test_summary_only_uses_native_public_assistant_text_and_is_persisted_bounded(self):
        public = '已收到本页两个控件的位置与用途，等待后续补充。' * 40
        self.server.result['messages'] = [
            {'info': {'role': 'user', 'sessionId': 'sess-fixture'}, 'parts': [{'type': 'text', 'text': 'fixture context'}]},
            {'info': {'role': 'assistant', 'sessionId': 'sess-fixture'}, 'parts': [
                {'type': 'reasoning', 'text': 'PRIVATE_REASONING'},
                {'type': 'tool', 'state': {'output': 'PRIVATE_TOOL_OUTPUT'}},
                {'type': 'text', 'text': 'PRIVATE_SYNTHETIC', 'synthetic': True},
                {'type': 'text', 'text': 'PRIVATE_IGNORED', 'ignored': True},
                {'type': 'text', 'text': 'PRIVATE_HIDDEN', 'metadata': {'zcode': {'visibility': 'model-only'}}},
                {'type': 'text', 'text': public, 'sessionId': 'sess-fixture'}]}]
        state = self.handoff().send(self.bundle)
        summary = state['responseSummary']
        self.assertEqual(summary, public[:500])
        self.assertLessEqual(len(summary), 500)
        self.assertNotIn('PRIVATE_', summary)
        directory = Path(state['journalPath'])
        for name in ('state.json', 'result.json'):
            saved = json.loads((directory / name).read_text(encoding='utf-8'))
            self.assertEqual(saved['responseSummary'], summary)
            self.assertNotIn('PRIVATE_', json.dumps(saved))

    def test_summary_does_not_treat_flat_cli_response_or_historical_text_as_native_reply(self):
        self.assertEqual(_response_summary({'response': 'PRIVATE_CLI', 'messages': [
            {'role': 'assistant', 'content': 'PRIVATE_UNTYPED'}]}, 'sess-fixture'), '')
        self.assertEqual(_response_summary({'messages': [
            {'info': {'role': 'assistant', 'sessionId': 'sess-fixture'},
             'parts': [{'type': 'text', 'text': 'OLD_REPLY'}]},
            {'info': {'role': 'user', 'sessionId': 'sess-fixture'},
             'parts': [{'type': 'text', 'text': 'NEW_REQUEST'}]}]}, 'sess-fixture'), '')
        self.assertEqual(_response_summary({'messages': [
            {'info': {'role': 'assistant', 'sessionId': 'sess-other'},
             'parts': [{'type': 'text', 'text': 'WRONG_SESSION'}]}]}, 'sess-fixture'), '')

    def test_legacy_fingerprint_is_unchanged_without_sequence_field(self):
        payload = json.loads(Path(self.bundle['bundlePath']).read_text(encoding='utf-8'))
        payload.pop('sequence', None)
        Path(self.bundle['bundlePath']).write_text(json.dumps(payload), encoding='utf-8')
        legacy_identity = {'page': payload.get('page'), 'captures': [
            {'captureId': item.get('captureId'), 'sha256': (item.get('source') or {}).get('sha256'),
             'capture': item.get('capture')} for item in payload['captures']]}
        expected = hashlib.sha256(json.dumps(legacy_identity, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        self.assertEqual(self.handoff()._bundle(self.bundle)[-1], expected)

    def test_control_edits_removal_order_and_explicit_sequence_change_fingerprint(self):
        path = Path(self.bundle['bundlePath'])
        original = json.loads(path.read_text(encoding='utf-8'))

        def fingerprint(payload):
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            return self.handoff()._bundle(self.bundle)[-1]

        baseline = fingerprint(original)
        variants = {}
        for field, value in (('name', '更正后的控件名'), ('description', '更正后的功能说明')):
            changed = json.loads(json.dumps(original))
            changed['captures'][0]['capture']['annotation'][field] = value
            variants[field] = changed
        removed = json.loads(json.dumps(original))
        removed['captures'].pop()
        removed['captureCount'] = len(removed['captures'])
        variants['delete'] = removed
        reordered = json.loads(json.dumps(original))
        reordered['captures'].reverse()
        variants['reorder'] = reordered
        for label, payload in variants.items():
            with self.subTest(change=label):
                self.assertNotEqual(fingerprint(payload), baseline)

        unordered = json.loads(json.dumps(original))
        unordered['sequence'] = {'ordered': False,
                                 'captureIds': [item['captureId'] for item in original['captures']]}
        ordered = json.loads(json.dumps(unordered))
        ordered['sequence']['ordered'] = True
        self.assertNotEqual(fingerprint(unordered), baseline)
        self.assertNotEqual(fingerprint(unordered), fingerprint(ordered))
        self.assertEqual(fingerprint(ordered), fingerprint(json.loads(json.dumps(ordered))))

    def test_sequence_only_change_submits_once_and_identical_export_is_duplicate(self):
        path = Path(self.bundle['bundlePath'])
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['sequence'] = {'ordered': False}
        path.write_text(json.dumps(payload), encoding='utf-8')
        first = self.handoff().send(self.bundle)
        self.assertEqual(first['status'], 'registered')
        payload['sequence']['ordered'] = True
        path.write_text(json.dumps(payload), encoding='utf-8')
        second = self.handoff().send(self.bundle)
        self.assertEqual(second['status'], 'registered')
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        duplicate = self.handoff().send(self.bundle)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.server.calls), 2)

    def test_index_failure_retries_only_registration_and_preserves_session(self):
        self.index.failure = True
        first = self.handoff().send(self.bundle)
        self.assertEqual(first['status'], 'index_failed')
        self.assertEqual(first['sessionId'], 'sess-fixture')
        self.index.failure = False
        second = self.handoff().send(self.bundle)
        self.assertEqual(second['status'], 'registered')
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(len(self.index.calls), 2)

    def test_incomplete_nonzero_or_missing_session_blocks_retry(self):
        for case_number, result in enumerate(({'status': 'unknown'}, {'status': 'failed', 'exitCode': 3}, {'status': 'completed'})):
            with self.subTest(result=result):
                self.server.result = result
                calls_before = len(self.server.calls)
                state = self.handoff().send(self.bundle)
                self.assertEqual(state['status'], 'unknown')
                self.assertFalse(state['indexRegistered'])
                self.assertTrue(self.handoff().send(self.bundle)['duplicate'])
                self.assertEqual(len(self.server.calls), calls_before + 1)
                self.assertEqual(len(self.index.calls), 0)
                # Each case has its own journal without rewriting real state.
                self.bundle = self.workspace.export_page_bundle('https://example.test/orders')
                payload = json.loads(Path(self.bundle['bundlePath']).read_text(encoding='utf-8'))
                payload['captures'][0]['capture']['annotation']['description'] += f' changed-{case_number}'
                Path(self.bundle['bundlePath']).write_text(json.dumps(payload), encoding='utf-8')

    def test_timeout_stops_original_run_and_does_not_register(self):
        self.server.result = None
        state = self.handoff().send(self.bundle)
        self.assertEqual(state['status'], 'unknown')
        self.assertTrue(self.server.closed)
        self.assertEqual(self.index.calls, [])

    def test_paths_cannot_escape_workspace(self):
        with self.assertRaises(HandoffError):
            self.handoff().send({**self.bundle, 'taskPath': str(self.root.parent / 'outside.md')})
        self.assertEqual(self.server.calls, [])

    def test_previous_picker_session_switches_to_selected_mode_without_model_resubmission(self):
        first = self.handoff().send(self.bundle)
        path = Path(first['journalPath']) / 'state.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved['mode'] = 'edit'
        path.write_text(json.dumps(saved), encoding='utf-8')
        second = self.handoff().send(self.bundle)
        self.assertEqual(second['mode'], 'yolo')
        self.assertEqual(second['sessionId'], first['sessionId'])
        self.assertEqual(self.server.bindings, [None, first['sessionId']])
        self.assertEqual(len(self.server.calls), 1)

    def test_os_lock_rejects_separate_process(self):
        module = Path(__file__).resolve().parents[1]
        lock = self.root / '.handoffs' / '.send.lock'
        child = ('import sys;sys.path.insert(0,sys.argv[1]);'
                 'from zcode_handoff import workspace_lock;'
                 'with workspace_lock(__import__("pathlib").Path(sys.argv[2])): pass')
        # A multiline suite is required after the imports.
        child = child.replace(';with ', '\nwith ')
        with workspace_lock(lock):
            completed = subprocess.run([sys.executable, '-c', child, str(module), str(lock)],
                                       capture_output=True, text=True, timeout=5)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('HandoffError', completed.stderr)


if __name__ == '__main__':
    unittest.main()
