"""Bounded JSON jobs against one already selected, logged-in Chrome page."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from cdp_client import CDPClient, validate_endpoint
from workspace import atomic_write, page_identity, safe_url, sanitize_text


SCRIPT = Path(__file__).with_suffix('.js')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ACTIONS = {'read', 'click', 'fill', 'select', 'check'}


class TaskBlocked(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _read_object(path, limit):
    try:
        data = path.read_bytes()
        if len(data) > limit:
            raise TaskBlocked('input-too-large', '任务或页面包超过允许大小。')
        value = json.loads(data.decode('utf-8-sig'))
    except (OSError, UnicodeError, ValueError) as error:
        if isinstance(error, TaskBlocked):
            raise
        raise TaskBlocked('invalid-input', '任务或页面包不是可读取的 JSON 对象。') from None
    if not isinstance(value, dict):
        raise TaskBlocked('invalid-input', '任务或页面包需要 JSON 对象。')
    return value


def _inside(path, root):
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise TaskBlocked('outside-learning-directory', '任务和结果必须位于本页面包的 learning 目录内。')
    return resolved


def _clean(value):
    if isinstance(value, str):
        text = sanitize_text(value, 6000)
        return re.sub(r'https?://[^\s"\'<>]+', lambda match: safe_url(match.group()), text)
    if isinstance(value, list):
        return [_clean(item) for item in value[:256]]
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()
                if key not in {'value', 'inputValue', 'currentValue', 'cookie', 'token', 'html', 'outerHTML'}}
    return value


def _eval(client, expression):
    response = client.call('Runtime.evaluate', {'expression': expression,
                           'returnByValue': True, 'awaitPromise': True}, timeout=15)
    if not isinstance(response, dict) or response.get('exceptionDetails'):
        raise TaskBlocked('page-script-failed', '页面检查未返回有效结果，未自动重试。')
    value = (response.get('result') or {}).get('value')
    if not isinstance(value, dict):
        raise TaskBlocked('invalid-page-result', '页面未返回结构化检查结果。')
    return value


def _claim_action(source, job, learning, action_policy=None):
    directory = _inside(learning / '.executions', learning)
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.path.normcase(str(source)).encode('utf-8')).hexdigest()
    # Preserve old claim hashes for legacy bundles. A new policy cannot reuse
    # an old action receipt at the same job path or bypass an uncertain action.
    identity = job if action_policy is None else {'job': job, 'actionPolicy': action_policy}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()
    claim = directory / (key + '.claim')
    state_path = directory / (key + '.json')
    try:
        descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            state = _read_object(state_path, 8 * 1024 * 1024)
        except TaskBlocked:
            state = {}
        if state.get('jobSha256') and state['jobSha256'] != digest:
            raise TaskBlocked('job-was-modified', '同一步骤文件的内容已经改变；未再次执行，请为明确的新步骤使用独立任务。')
        previous = state.get('result')
        if state.get('phase') in {'completed', 'blocked', 'failed-before-action'} and isinstance(previous, dict):
            return None, {**previous, 'duplicate': True, 'executedNow': False}
        return None, {'schemaVersion': 1, 'status': 'unknown', 'operation': 'act',
                      'actionAttempted': True, 'duplicate': True,
                      'executedNow': False, 'error': {'code': 'previous-action-unconfirmed',
                      'message': '原步骤仍在执行或结果不明；请先核对现有页面与执行记录，不会自动重试。'}}
    else:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps({'jobSha256': digest}))
        state = {'schemaVersion': 1, 'jobSha256': digest,
                 'phase': 'prepared', 'actionAttempted': False}
        atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        return (state_path, state), None


def _save_execution(execution, phase, attempted, result=None):
    path, state = execution
    state.update(phase=phase, actionAttempted=attempted)
    if result is not None:
        state['result'] = result
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')


def run_task(input_path, output_path, client_factory=CDPClient):
    """Run exactly one job and atomically write a bounded, credential-free result.

    Invalid destinations are never written. An interrupted act remains attempted
    and must not be retried automatically; successful dispatch is not business
    success. This function never launches/closes a browser or creates a tab.
    """
    result = {'schemaVersion': 1, 'status': 'blocked', 'operation': None,
              'actionAttempted': False}
    destination = None
    client = None
    execution = None
    try:
        source = Path(input_path).expanduser().resolve()
        job = _read_object(source, 1024 * 1024)
        operation = job.get('operation')
        result['operation'] = operation if isinstance(operation, str) else None
        bundle_value = job.get('bundlePath')
        if not isinstance(bundle_value, str) or not Path(bundle_value).is_absolute():
            raise TaskBlocked('invalid-bundle-path', 'bundlePath 必须是当前页面包的绝对路径。')
        bundle_path = Path(bundle_value).expanduser().resolve()
        learning = (bundle_path.parent / 'learning').resolve()
        if not learning.is_relative_to(bundle_path.parent):
            raise TaskBlocked('outside-learning-directory', 'learning 目录不能链接到本页面包之外。')
        _inside(source, learning)
        destination = _inside(output_path, learning)
        if destination in {source, bundle_path} or destination.suffix.lower() != '.json':
            destination = None
            raise TaskBlocked('invalid-output-path', '结果需要独立 JSON 文件，不能覆盖任务或来源。')
        if type(job.get('schemaVersion')) is not int or job['schemaVersion'] != 1 or operation not in {'inspect', 'read-page', 'act'}:
            raise TaskBlocked('unsupported-job', '只支持 schemaVersion=1 的 inspect、read-page 或 act 任务。')
        bundle = _read_object(bundle_path, 8 * 1024 * 1024)
        intent = bundle.get('taskIntent') or {}
        if (bundle.get('kind') != 'annotated-controls-page-bundle' or not isinstance(intent, dict)
                or intent.get('mode') != 'learn-and-verify' or type(intent.get('contractVersion')) is not int
                or intent['contractVersion'] != 1):
            raise TaskBlocked('unsupported-intent', '此页面包未启用本版学习与验证任务。')
        action_policy = intent.get('actionPolicy')
        if 'actionPolicy' in intent and action_policy != 'infer-from-purpose':
            raise TaskBlocked('unsupported-action-policy', '页面动作策略无效，未降级为旧策略或执行动作。')
        if action_policy is not None:
            result['actionPolicy'] = action_policy
        captures = bundle.get('captures')
        if (not isinstance(captures, list) or not captures or len(captures) > 256
                or any(not isinstance(item, dict) or not isinstance(item.get('captureId'), str)
                       or not isinstance(item.get('capture'), dict) for item in captures)
                or len({item['captureId'] for item in captures}) != len(captures)):
            raise TaskBlocked('invalid-captures', '页面包控件资料无效或重复。')
        connection = bundle.get('connection')
        if not isinstance(connection, dict):
            raise TaskBlocked('missing-connection', '页面包缺少实际 CDP 连接信息。')
        try:
            endpoint = validate_endpoint(connection.get('endpoint'))
            expected_page = page_identity(connection.get('page'))
        except (ValueError, TypeError):
            raise TaskBlocked('invalid-connection', 'CDP 连接必须是有效的本地端点及明确页面身份。') from None
        if page_identity(bundle.get('page')) != expected_page:
            raise TaskBlocked('page-scope-mismatch', '连接页面身份与标注页面不一致。')
        target_id = connection.get('targetId')
        expected_hash = job.get('expectedUrlHash', connection.get('initialUrlSha256'))
        if not isinstance(target_id, str) or not target_id.strip():
            raise TaskBlocked('missing-target', '缺少当前选中的页签 ID，请回采集器重新选页。')
        if not isinstance(expected_hash, str) or not _HASH.fullmatch(expected_hash):
            raise TaskBlocked('missing-url-fingerprint', '缺少有效的当前路由指纹，不能猜测页面。')
        selected_capture = None
        if operation == 'act':
            if job.get('purposeScope') not in {'read-only', 'reversible'}:
                raise TaskBlocked('purpose-not-authorized', '本入口不执行业务写入；请先明确只读或可逆操作范围。')
            action = job.get('action')
            selected_capture = next((item for item in captures if item['captureId'] == job.get('captureId')), None)
            if selected_capture is None or action not in _ACTIONS:
                raise TaskBlocked('invalid-action', '动作需要有效的已标注控件编号和支持的动作。')
            annotation = selected_capture['capture'].get('annotation')
            allowed = annotation.get('allowedActions') if isinstance(annotation, dict) else None
            if action_policy is None and (not isinstance(allowed, list) or action not in allowed):
                raise TaskBlocked('action-not-allowed', '该动作未包含在本控件标注允许的动作中。')
            value = job.get('value')
            if action in {'fill', 'select'} and (not isinstance(value, str) or len(value) > 4000):
                raise TaskBlocked('missing-action-value', '填写或选择需要明确的字符串参数，不能猜测。')
            if action == 'check' and not isinstance(value, bool):
                raise TaskBlocked('missing-action-value', '勾选需要明确 true 或 false，不能盲目切换。')
            execution, duplicate = _claim_action(source, job, learning, action_policy)
            if duplicate is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(destination, json.dumps(duplicate, ensure_ascii=False, indent=2) + '\n')
                return duplicate
        client = client_factory(endpoint)
        pages = client.list_pages()
        matches = [page for page in pages if page.get('id') == target_id]
        if len(matches) != 1:
            raise TaskBlocked('target-not-found', '选中的页签不存在或身份不唯一，请回采集器重新选页。')
        client.connect(matches[0])
        live = _eval(client, '(() => ({url: location.href, title: document.title}))()')
        live_url = live.get('url')
        if not isinstance(live_url, str):
            raise TaskBlocked('missing-page-url', '页面没有返回当前地址。')
        actual_page = page_identity(live_url)
        actual_hash = hashlib.sha256(live_url.encode('utf-8')).hexdigest()
        result['page'] = {**actual_page, 'title': _clean(live.get('title', '')), 'urlSha256': actual_hash}
        if actual_page != expected_page or actual_hash != expected_hash:
            raise TaskBlocked('page-changed', '当前地址或页面路由已经变化；未操作控件，请重新核对页面。')
        parameters = {'operation': operation, 'expectedLiveUrl': live_url,
                      'captures': captures, 'captureId': job.get('captureId'),
                      'action': job.get('action'), 'value': job.get('value')}
        if action_policy is not None:
            parameters['actionPolicy'] = action_policy
        script = SCRIPT.read_text(encoding='utf-8')
        expression = '(' + script + ')(' + json.dumps(parameters, ensure_ascii=True) + ')'
        # Once an action call is submitted, an exception is an uncertain result.
        if execution:
            _save_execution(execution, 'executing', True)
        result['actionAttempted'] = operation == 'act'
        observed = _eval(client, expression)
        if observed.get('status') not in {'completed', 'blocked', 'failed'}:
            raise TaskBlocked('invalid-page-result', '页面操作没有返回可确认的结果。')
        after = observed.pop('_pageLocation', None)
        if isinstance(after, dict) and isinstance(after.get('url'), str):
            actual_page = page_identity(after['url'])
            actual_hash = hashlib.sha256(after['url'].encode('utf-8')).hexdigest()
            live['title'] = after.get('title', live.get('title', ''))
        result.update(_clean(observed))
        result['operation'] = operation
        result['schemaVersion'] = 1
        result['page'] = {**actual_page, 'title': _clean(live.get('title', '')), 'urlSha256': actual_hash}
        if operation == 'act':
            result['businessSuccessVerified'] = False
    except TaskBlocked as error:
        result.update(status='unknown' if result['actionAttempted'] else 'blocked', error={'code': error.code, 'message': error.message})
    except Exception as error:
        result.update(status='unknown' if result['actionAttempted'] else 'failed', error={'code': 'runtime-failure',
                      'message': 'CDP 连接或执行中断，未自动重试。', 'type': type(error).__name__})
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    if execution:
        if result.get('actionAttempted') and result.get('status') == 'failed':
            result['status'] = 'unknown'
        phase = (result['status'] if result['status'] in {'completed', 'blocked'}
                 else 'failed-before-action' if not result.get('actionAttempted') else 'unknown')
        try:
            _save_execution(execution, phase, result.get('actionAttempted', False), result)
        except OSError:
            if result.get('actionAttempted'):
                result.update(status='unknown', error={'code': 'execution-journal-write-failed',
                              'message': '动作回执无法持久保存；不会自动重试，请核对当前页面。'})
    if destination is not None:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(destination, json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        except OSError:
            result.update(status='failed', error={'code': 'result-write-failed',
                          'message': '结果文件无法保存；已尝试的动作不会自动重试。'})
    return result
