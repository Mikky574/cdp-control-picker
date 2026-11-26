"""Background handoff tests use fake CLI and index; no model or user DB."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import Workspace
from zcode_handoff import (HandoffError, ZCodeHandoff, inspect_handoff, read_handoff,
                           workspace_lock, _response_summary)


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
