"""Send lifecycle checks use fake time and local receipts; no UI or model."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from send_feedback import SendFeedback


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class SendFeedbackTests(unittest.TestCase):
    FIRST = 'a' * 64
    CHANGED = 'b' * 64

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='picker-feedback-')
        self.addCleanup(self.temp.cleanup)
        self.receipts = Path(self.temp.name) / 'receipts.json'
        self.clock = Clock()
        self.model = SendFeedback(clock=self.clock, receipt_path=self.receipts)
        self.model.select(self.FIRST)

    @staticmethod
    def registered(**extra):
        return {'status': 'registered', 'indexRegistered': True,
                'sessionId': 'sess_feedback_fixture',
                'responseSummary': '已收到控件，等待后续说明', **extra}

    def test_active_progress_counts_time_and_rejects_a_second_begin(self):
        self.assertTrue(self.model.begin(self.FIRST))
        self.clock.advance(4)
        self.model.progress('running', '正在等待上下文确认')
        view = self.model.view()
        self.assertTrue(view['active'])
        self.assertEqual(view['elapsed'], 4)
        self.assertIn('上下文', view['message'])
        self.assertFalse(self.model.begin(self.FIRST))
        self.assertFalse(self.model.begin(self.CHANGED))
        self.assertEqual(self.model.revision, self.FIRST)
        self.assertEqual(self.model.view()['elapsed'], 4)

    def test_success_keeps_terminal_feedback_and_freezes_elapsed(self):
        self.model.begin(self.FIRST)
        self.clock.advance(7)
        self.model.complete(self.registered())
        completed = self.model.view()
        self.assertFalse(completed['active'])
        self.assertTrue(completed['blocked'])
        self.assertTrue(completed['title'])
        self.assertTrue(completed['message'])
        self.assertTrue(completed['summary'])
        self.assertTrue(completed['completedAt'])
        self.assertEqual(completed['elapsed'], 7)
        self.clock.advance(60)
        self.assertEqual(self.model.view()['elapsed'], 7)
        self.assertFalse(self.model.can_send(self.FIRST))
        self.assertFalse(self.model.begin(self.FIRST))
        self.assertEqual(self.model.view()['title'], completed['title'])
        self.assertEqual(self.model.view()['result'], completed['result'])

    def test_quick_duplicate_has_a_persistent_receipt_without_minimum_wait(self):
        self.model.begin(self.FIRST)
        self.model.complete(self.registered(duplicate=True))
        view = self.model.view()
        self.assertFalse(view['active'])
        self.assertTrue(view['blocked'])
        self.assertTrue(view['result']['duplicate'])
        self.assertTrue(view['summary'])
        self.assertEqual(view['elapsed'], 0)
        self.assertFalse(self.model.can_send(self.FIRST))
        self.clock.advance(30)
        self.assertEqual(self.model.view()['result'], view['result'])

    def test_registered_receipt_blocks_identical_content_after_reload(self):
        self.model.begin(self.FIRST)
        self.model.complete(self.registered())
        self.assertTrue(self.receipts.is_file())
        json.loads(self.receipts.read_text(encoding='utf-8'))
        reloaded = SendFeedback(clock=self.clock, receipt_path=self.receipts)
        reloaded.select(self.FIRST)
        self.assertFalse(reloaded.can_send(self.FIRST))
        self.assertFalse(reloaded.begin(self.FIRST))
        self.assertTrue(reloaded.view()['blocked'])
        self.assertEqual(reloaded.view()['result']['sessionId'],
                         'sess_feedback_fixture')

    def test_changed_content_unlocks_without_erasing_old_receipt(self):
        self.model.begin(self.FIRST)
        self.model.complete(self.registered())
        self.model.select(self.CHANGED)
        self.assertTrue(self.model.can_send(self.CHANGED))
        self.assertFalse(self.model.can_send(self.FIRST))
        self.assertTrue(self.model.begin(self.CHANGED))
        self.assertEqual(self.model.revision, self.CHANGED)

    def test_legacy_registered_receipt_does_not_invent_an_assistant_summary(self):
        self.model.begin(self.FIRST)
        result = self.registered()
        result.pop('responseSummary')
        self.model.complete(result)
        self.assertEqual(self.model.view()['summary'], '')
        reloaded = SendFeedback(clock=self.clock, receipt_path=self.receipts)
        reloaded.select(self.FIRST)
        self.assertEqual(reloaded.view()['summary'], '')
        self.assertFalse(reloaded.can_send(self.FIRST))

    def test_corrupt_or_incomplete_success_cache_cannot_report_done(self):
        self.model.begin(self.FIRST)
        self.model.complete(self.registered())
        valid = self.receipts.read_text(encoding='utf-8')
        cases = (
            ('missing index proof', lambda receipt: receipt['result'].pop('indexRegistered')),
            ('blank session', lambda receipt: receipt['result'].update(sessionId=' ')),
            ('wrong status', lambda receipt: receipt['result'].update(status='unknown')),
            ('negative elapsed', lambda receipt: receipt.update(elapsed=-1)),
            ('text elapsed', lambda receipt: receipt.update(elapsed='7')),
            ('invalid completion timestamp', lambda receipt: receipt.update(completedAt={})),
        )
        for label, corrupt in cases:
            with self.subTest(cache=label):
                payload = json.loads(valid)
                corrupt(payload['receipts'][self.FIRST])
                self.receipts.write_text(json.dumps(payload), encoding='utf-8')
                reloaded = SendFeedback(clock=self.clock, receipt_path=self.receipts)
                reloaded.select(self.FIRST)
                view = reloaded.view()
                self.assertNotIn(view['phase'], ('registered', 'duplicate'))
                self.assertFalse(view['result'])
                self.assertTrue(reloaded.can_send(self.FIRST))

    def test_unknown_result_is_not_an_invitation_to_resubmit(self):
        self.model.begin(self.FIRST)
        self.model.complete({'status': 'unknown', 'sessionId': 'sess_uncertain',
                             'message': '结果尚未确认，不重发'})
        self.assertFalse(self.model.can_send(self.FIRST))
        self.assertTrue(self.model.view()['blocked'])
        reloaded = SendFeedback(clock=self.clock, receipt_path=self.receipts)
        self.assertFalse(reloaded.can_send(self.FIRST))
        self.assertTrue(reloaded.can_send(self.CHANGED))

    def test_definite_failure_allows_explicit_retry(self):
        self.model.begin(self.FIRST)
        self.clock.advance(2)
        self.model.fail('启动前检查失败', uncertain=False)
        self.assertFalse(self.model.view()['active'])
        self.assertTrue(self.model.can_send(self.FIRST))
        self.assertIn('检查失败', self.model.view()['message'])
        self.assertTrue(self.model.begin(self.FIRST))
        self.assertEqual(self.model.view()['elapsed'], 0)

    def test_uncertain_failure_keeps_content_blocked(self):
        self.model.begin(self.FIRST)
        self.model.fail('任务可能已经提交，结果未知', uncertain=True)
        self.assertFalse(self.model.view()['active'])
        self.assertTrue(self.model.view()['blocked'])
        self.assertFalse(self.model.begin(self.FIRST))

    def test_index_failure_is_retryable_without_discarding_the_session(self):
        self.model.begin(self.FIRST)
        self.model.complete({'status': 'index_failed', 'indexRegistered': False,
                             'sessionId': 'sess_index_retry',
                             'message': '会话已保存，只补登记'})
        self.assertFalse(self.model.view()['active'])
        self.assertTrue(self.model.can_send(self.FIRST))
        self.assertEqual(self.model.view()['result']['sessionId'],
                         'sess_index_retry')
        self.assertTrue(self.model.begin(self.FIRST))


if __name__ == '__main__':
    unittest.main()
