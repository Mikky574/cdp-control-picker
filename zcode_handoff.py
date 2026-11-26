"""Hand annotated controls to one native ZCode conversation.

No desktop input, shell, credential changes, or implicit model retries.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from workspace import atomic_write, sanitize_text
from zcode_runner import resolve_command


class HandoffError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def workspace_lock(path):
    """An OS lock also covers separate picker processes; crashes release it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise HandoffError('该工作区正在交接，请等待当前后台任务结束。') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ZCodeHandoff:
    def __init__(self, workspace, commandPath=None, *, timeout_seconds=900,
                 server_factory=None, index_adapter=None):
        root = workspace if isinstance(workspace, (str, os.PathLike)) else workspace.root
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise HandoffError('请先选择有效的采集工作区。')
        self.command = resolve_command(commandPath)
        self.timeout = max(1, float(timeout_seconds))
        self.mode = 'yolo'  # Explicitly selected by the user for picker sessions.
        if server_factory is None:
            from zcode_appserver import ZCodeAppServer
            server_factory = ZCodeAppServer
        self.server_factory = server_factory
        if index_adapter is None:
            from desktop_index import DesktopTaskIndex
            desktop = self.command[0] if Path(self.command[0]).name.lower() == 'zcode.exe' else None
            index_adapter = DesktopTaskIndex(self.root, desktop_path=desktop)
        self.index = index_adapter
        self.journal = self._inside(self.root / '.handoffs')
        self.journal.mkdir(exist_ok=True)

    def _inside(self, value):
        path = Path(value).resolve()
        if not path.is_relative_to(self.root):
            raise HandoffError('交接文件超出当前采集工作区。')
        return path

    def _bundle(self, bundle):
        if not isinstance(bundle, dict):
            raise HandoffError('页面任务格式无效。')
        task = self._inside(bundle.get('taskPath', ''))
        path = self._inside(bundle.get('bundlePath', ''))
        if not task.is_file() or not path.is_file() or task.parent != path.parent:
            raise HandoffError('页面任务文件不存在或来源不一致。')
        payload = json.loads(path.read_text(encoding='utf-8'))
        captures = payload.get('captures')
        if (payload.get('kind') != 'annotated-controls-page-bundle' or not captures
                or not isinstance(captures, list) or payload.get('captureCount') != len(captures)
                or payload.get('bundleId') != bundle.get('bundleId')):
            raise HandoffError('请先导出有效的本页控件合集。')
        identity = {'page': payload.get('page'), 'captures': [
            {'captureId': item.get('captureId'), 'sha256': (item.get('source') or {}).get('sha256'),
             'capture': item.get('capture')} for item in captures]}
        # New bundles can explicitly attach order/workflow semantics. Keep the
        # legacy identity unchanged when that field is absent.
        if 'sequence' in payload:
            identity['sequence'] = payload['sequence']
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        context = self._inside(bundle['contextPath']) if bundle.get('contextPath') else None
        if context is not None and (not context.is_file() or context.parent != task.parent):
            raise HandoffError('控件上下文文件不存在或来源不一致。')
        return task, context, path, payload, fingerprint

    def _previous(self, fingerprint):
        for path in sorted(self.journal.glob('handoff-*/state.json'), reverse=True):
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if state.get('fingerprint') == fingerprint:
                if state.get('status') == 'failed' and state.get('submissionAttempted') is False:
                    continue  # Explicit retry is safe only before any prompt call.
                return path, state
        return None, None

    @staticmethod
    def _notify(callback, phase, message):
        if callback:
            try:
                callback({'phase': phase, 'message': message})
            except Exception:
                pass

    @staticmethod
    def _save(path, state):
        atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')

    def _check_mode(self, snapshot):
        settings = snapshot.get('settings') or {}
        actual = ((settings.get('permission') or {}).get('mode')
                  or (settings.get('mode') or {}).get('current')
                  or (snapshot.get('session') or {}).get('mode'))
        planning = ((settings.get('mode') or {}).get('current') == 'plan'
                    or any(item.get('planEnabled') is True for item in
                           (settings, settings.get('permission') or {}, snapshot.get('session') or {},
                            snapshot.get('runtime') or {})))
        if actual != self.mode or planning:
            raise HandoffError('后台未确认使用本工具选定的完全访问权限。')

    def _register(self, state_path, state, callback):
        self._notify(callback, 'registering', '后台任务完成，正在登记 ZCode 桌面对话索引。')
        try:
            receipt = self.index.register(state['sessionId'], title=state['conversationTitle'],
                                          task_path=state['taskPath'], mode=state.get('mode', self.mode),
                                          update_owned_mode=True)
            if (not isinstance(receipt, dict) or receipt.get('indexRegistered') is not True
                    or receipt.get('sessionId') != state['sessionId']
                    or receipt.get('taskId') != state['sessionId']
                    or receipt.get('mode') != state.get('mode', self.mode)):
                raise HandoffError('索引回执没有确认同一个真实会话。')
            state.update(status='registered', indexRegistered=True, indexReceipt=receipt,
                         message='已交给 ZCode，可在该对话继续补充。')
        except Exception as error:
            state.update(status='index_failed', indexRegistered=False,
                         message='后台会话已保存，桌面索引登记失败；再次发送仅重试登记，不会重新调用模型。',
                         error=sanitize_text(error, 1500))
        state['finishedAt'] = _now()
        self._save(state_path, state)
        return state

    def send(self, bundle, on_event=None):
        with workspace_lock(self.journal / '.send.lock'):
            task, context, bundle_path, payload, fingerprint = self._bundle(bundle)
            previous_path, previous = self._previous(fingerprint)
            if previous:
                if previous.get('status') in {'registered', 'index_failed'} and previous.get('sessionId'):
                    if previous.get('mode') != self.mode:
                        # Upgrade only this tool's known session after the user's
                        # explicit mode selection; never submit its prompt again.
                        with self.server_factory(commandPath=self.command, cwd=self.root,
                                                 timeout_seconds=self.timeout, mode=self.mode) as server:
                            restored = server.bind(previous['sessionId'])
                            if (restored.get('session') or {}).get('sessionId') != previous['sessionId']:
                                raise HandoffError('恢复的不是原交接会话，未重新发送任务。')
                            self._check_mode(restored)
                            previous['binding'] = getattr(server, 'last_binding', {})
                        previous['mode'] = self.mode
                        self._save(previous_path, previous)
                    return {**self._register(previous_path, previous, on_event), 'duplicate': True}
                return {**previous, 'status': 'unknown', 'duplicate': True,
                        'message': '原后台任务结果尚未确认，请先检查原会话与日志；不会重复调用模型。'}
            # Schema/version/path failures stop before a model request is submitted.
            self.index.preflight()
            request_id = 'handoff-' + uuid.uuid4().hex[:20]
            directory = self._inside(self.journal / request_id)
            directory.mkdir()
            state_path = directory / 'state.json'
            page = payload.get('page') or {}
            label = page.get('title') or page.get('path') or '本页'
            title = sanitize_text(f'CDP · {label} · {payload["captureCount"]} 个控件', 100)
            if context is None:
                context = directory / 'context.md'
                atomic_write(context, f'# 本页控件上下文\n\n完整读取 `{bundle_path}`，'
                             f'理解本页 {payload["captureCount"]} 个已标注控件的位置与用途。\n\n'
                             '本次只简短确认收到并等待用户后续要求；不要自动生成技能、脚本或计划，'
                             '不要操作网页、执行控件或读取旧的生成任务。\n')
            prompt = (f'接收本页全部 {payload["captureCount"]} 个已标注控件的上下文。'
                      f'读取“{context}”，简短确认收到后等待我补充。【CDP:{request_id}】')
            state = {'schemaVersion': 1, 'requestId': request_id, 'bundleId': payload['bundleId'],
                     'captureCount': payload['captureCount'], 'fingerprint': fingerprint,
                     'createdAt': _now(), 'status': 'prepared', 'indexRegistered': False,
                     'submissionAttempted': False,
                     'conversationTitle': title, 'workspace': str(self.root), 'taskPath': str(task),
                     'contextPath': str(context), 'handoffIntent': 'context-only',
                     'mode': self.mode, 'transport': 'app-server-stdio',
                     'journalPath': str(directory), 'message': '正在启动 ZCode 后台任务。'}
            self._save(state_path, state)
            self._save(directory / 'request.json', {'requestId': request_id, 'prompt': prompt,
                                                    'contextPath': str(context), 'taskPath': str(task),
                                                    'bundleId': payload['bundleId']})
            self._notify(on_event, 'running', '正在把本页标注交给 ZCode；无需保持前台。')
            try:
                with self.server_factory(commandPath=self.command, cwd=self.root,
                                         timeout_seconds=self.timeout, mode=self.mode) as server:
                    initial = server.bind()
                    session_id = (initial.get('session') or {}).get('sessionId')
                    if not isinstance(session_id, str) or not session_id.strip():
                        raise HandoffError('后台没有返回真实会话 ID。')
                    state.update(status='running', sessionId=session_id,
                                 binding=getattr(server, 'last_binding', {}),
                                 message='ZCode 会话已建立，正在读取控件资料。')
                    self._save(state_path, state)
                    self._check_mode(initial)
                    def progress(event):
                        kind = str(event.get('type') or event.get('phase') or 'running')
                        labels = {'request_admitted': 'ZCode 已接收，等待模型回复。',
                                  'turn_started': 'ZCode 正在读取本页标注（完全访问）。',
                                  'tool.started': 'ZCode 正在使用工具：' + sanitize_text(event.get('toolName', ''), 80),
                                  'tool.completed': '工具已完成，正在整理结果。',
                                  'tool.failed': '工具返回失败，等待 ZCode 确认本轮结果。',
                                  'model.request.started': 'ZCode 正在请求模型。',
                                  'public_text_delta': 'ZCode 正在生成公开回复。',
                                  'final_snapshot_read': 'ZCode 已报告完成，正在核对最终会话。',
                                  'completed': '后台任务已完成，正在准备桌面登记。'}
                        message = sanitize_text(event.get('message') or labels.get(kind) or 'ZCode 正在后台处理。', 350)
                        state.update(phase=kind, message=message)
                        self._save(state_path, state)
                        self._notify(on_event, kind, message)
                    state['submissionAttempted'] = True
                    self._save(state_path, state)
                    result = server.prompt(session_id, prompt, input_id=request_id, on_event=progress)
                    if (result.get('session') or {}).get('sessionId') != session_id:
                        raise HandoffError('后台结果与本次会话不一致。')
                    self._check_mode(result)
                    self._save(directory / 'result.json', {
                        'sessionId': session_id, 'mode': self.mode, 'completed': True,
                        'messageCount': len(result.get('messages') or [])})
                state.update(status='index_failed')
                self._save(state_path, state)
            except Exception as error:
                attempted = state['submissionAttempted']
                state.update(status='unknown' if attempted else 'failed', finishedAt=_now(),
                             error=sanitize_text(error, 1500),
                             message=('发送结果未确认，请检查原对话；不会自动重发。' if attempted
                                      else 'ZCode 连接未完成，尚未发送模型消息，可以重试。'))
                self._save(state_path, state)
                return state
            return self._register(state_path, state, on_event)
