"""Persistent user-facing handoff receipts, separate from browser status.

This is UI state only. The backend's content identity and workspace lock remain
the authority for preventing a second model submission.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import time

from workspace import atomic_write, sanitize_text


class SendFeedback:
    def __init__(self, clock=time.monotonic, receipt_path=None):
        self.clock = clock
        self.receipt_path = Path(receipt_path) if receipt_path else None
        self.receipts = {}
        self.active = False
        self.revision = self.current_revision = None
        self.started = None
        self.phase = 'idle'
        self.message = '发送后，进度和结果会一直显示在这里。'
        self.result = {}
        self.completed_at = None
        self.elapsed_finished = 0
        self._load()

    def _load(self):
        if not self.receipt_path or not self.receipt_path.is_file():
            return
        try:
            if self.receipt_path.stat().st_size > 262144:
                return
            payload = json.loads(self.receipt_path.read_text(encoding='utf-8'))
            if payload.get('schemaVersion') != 1 or not isinstance(payload.get('receipts'), dict):
                return
            for revision, receipt in payload['receipts'].items():
                if (isinstance(revision, str) and re.fullmatch(r'[a-f0-9]{64}', revision)
                        and isinstance(receipt, dict) and isinstance(receipt.get('result'), dict)
                        and isinstance(receipt.get('message'), str)
                        and isinstance(receipt.get('elapsed',0),int) and receipt.get('elapsed',0)>=0
                        and (receipt.get('completedAt') is None or isinstance(receipt.get('completedAt'),str))
                        and receipt.get('phase') in {'registered', 'duplicate', 'unknown', 'index_failed'}):
                    result=receipt['result']
                    if receipt['phase'] in {'registered','duplicate'} and not self._registered(result):
                        continue
                    self.receipts[revision] = receipt
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # The backend still checks its original journal before send.

    def _persist(self):
        if not self.receipt_path:
            return
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        recent = dict(list(self.receipts.items())[-80:])
        atomic_write(self.receipt_path, json.dumps({'schemaVersion': 1, 'receipts': recent},
                                                 ensure_ascii=False, indent=2) + '\n')

    def select(self, revision):
        self.current_revision = revision
        if self.active:
            return
        receipt = self.receipts.get(revision)
        if receipt:
            self.revision = revision
            self.phase = receipt['phase']
            self.message = receipt['message']
            self.result = receipt['result']
            self.completed_at = receipt.get('completedAt')
            self.elapsed_finished = max(0,int(receipt.get('elapsed') or 0))
            self.started = None
        elif revision != self.revision:
            self.phase, self.message, self.result = 'idle', '本页标注尚未发送。', {}
            self.completed_at = self.started = None
            self.elapsed_finished = 0

    def can_send(self, revision):
        if self.active or revision is None:
            return False
        receipt = self.receipts.get(revision)
        return not receipt or receipt['phase'] not in {'registered', 'duplicate', 'unknown'}

    def begin(self, revision):
        if self.active:
            return False
        self.select(revision)
        if not self.can_send(revision):
            return False
        self.revision = revision
        self.active, self.phase = True, 'preparing'
        self.message = '正在合并控件资料，请稍候。'
        self.started, self.completed_at = self.clock(), None
        self.result = {}
        self.elapsed_finished = 0
        return True

    def progress(self, phase, message):
        if self.active:
            self.phase = str(phase or 'running')
            self.message = sanitize_text(message or 'ZCode 正在处理，请等待。', 240)

    @staticmethod
    def _registered(result):
        return (result.get('status')=='registered' and result.get('indexRegistered') is True
                and isinstance(result.get('sessionId'),str) and bool(result['sessionId'].strip()))

    def complete(self, result):
        result = result if isinstance(result, dict) else {}
        registered = self._registered(result)
        self.elapsed_finished = max(0,int(self.clock()-self.started)) if self.started is not None else 0
        self.active = False
        self.completed_at = datetime.now().astimezone().isoformat(timespec='seconds')
        allowed = ('status', 'indexRegistered', 'sessionId', 'requestId', 'journalPath',
                   'conversationTitle', 'captureCount', 'duplicate', 'responseSummary', 'submissionAttempted')
        self.result = {key: result[key] for key in allowed if key in result}
        if registered:
            duplicate = result.get('duplicate') is True
            self.phase = 'duplicate' if duplicate else 'registered'
            self.message = ('这份标注已经发送过，本次没有重复提交。请到原 ZCode 对话继续。' if duplicate else
                            'ZCode 已接收并回复，对话已登记。你可以到 ZCode 继续沟通。')
        elif result.get('status') == 'index_failed':
            self.phase = 'index_failed'
            self.message = 'ZCode 已处理，桌面对话登记待完成；再次点击只补登记，不重复发给模型。'
        elif result.get('status') == 'failed' and result.get('submissionAttempted') is not True:
            self.phase = 'failed'
            self.message = sanitize_text(result.get('message') or '尚未提交成功，可以重试。', 240)
        else:
            self.phase = 'unknown'
            self.message = sanitize_text(result.get('message') or '发送结果尚未确认，请先检查原对话；这份内容不会再次提交。', 240)
        if self.revision is not None and self.phase != 'failed':
            self.receipts.pop(self.revision, None)
            self.receipts[self.revision] = {'phase': self.phase, 'message': self.message,
                                            'completedAt': self.completed_at, 'elapsed':self.elapsed_finished, 'result': self.result}
            try:
                self._persist()
            except OSError:
                self.message += ' 本地显示回执未能保存；后台仍会检查重复内容。'

    def fail(self, message, uncertain=False):
        self.complete({'status': 'unknown' if uncertain else 'failed',
                       'submissionAttempted': bool(uncertain), 'message': sanitize_text(message, 240)})

    def view(self):
        elapsed = max(0, int(self.clock() - self.started)) if self.active and self.started is not None else self.elapsed_finished
        titles = {'idle': '等待发送', 'preparing': '准备发送', 'registered': '发送完成',
                  'duplicate': '已经发送过', 'failed': '发送未完成', 'unknown': '结果待确认',
                  'index_failed': '等待补登记'}
        phase = self.phase
        title = titles.get(phase, 'ZCode 处理中' if self.active else '发送状态')
        blocked = not self.can_send(self.current_revision)
        current_receipt = self.receipts.get(self.current_revision, {})
        current_phase = current_receipt.get('phase')
        button = ('正在发送…' if self.active else '本页已发送' if current_phase in {'registered', 'duplicate'} else
                  '结果待确认' if current_phase == 'unknown' else '补登记对话' if current_phase == 'index_failed' else '发给 ZCode')
        return {'phase': phase, 'title': title, 'message': self.message, 'elapsed': elapsed,
                'active': self.active, 'blocked': blocked, 'buttonText': button,
                'summary': sanitize_text(self.result.get('responseSummary') or '', 500),
                'completedAt': self.completed_at, 'result': self.result}
