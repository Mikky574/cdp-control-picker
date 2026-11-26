"""Background handoff tests use fake CLI and index; no model or user DB."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import Workspace
from zcode_handoff import HandoffError, ZCodeHandoff, workspace_lock


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
        return {'session': {'sessionId': self.result.get('sessionId')}, 'messages': [],
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
        self.server = FakeServer({'status': 'completed', 'sessionId': 'sess-fixture', 'response': 'draft ready'})

    def tearDown(self):
        self.temporary.cleanup()

    def handoff(self):
        return ZCodeHandoff(self.root, commandPath=[sys.executable], index_adapter=self.index,
                            server_factory=lambda **kwargs: self.server, timeout_seconds=1)

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
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['sessionId'], 'sess-fixture')
        self.assertEqual(self.server.calls, [])

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
