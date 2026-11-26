"""Bounded JSON jobs against a selected page or an isolated research tab."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

from cdp_client import CDPClient, CDPProtocolError, validate_endpoint
from cdp_network import NetworkJobError, observe_network, prepare_query, query_expression
from workspace import atomic_write, page_identity, safe_url, sanitize_text
from zcode_handoff import HandoffError, workspace_lock


SCRIPT = Path(__file__).with_suffix('.js')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ACTIONS = {'read', 'click', 'fill', 'select', 'check'}
_OPERATIONS = {'ensure-page', 'inspect', 'discover-controls', 'read-page', 'act', 'observe-network', 'query-json'}
_RESEARCH_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,47}\Z')
_LIVE_PAGE = '(() => ({url: location.href, title: document.title, ready: document.readyState}))()'
_URL_SECRET = re.compile(r'(?:token|password|passwd|cookie|authorization|secret|credential|assertion|session|oauth|api[-_]?key|signature|(?:^|[?&#])code)[^&#?=]*=', re.I)


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


def _eval(client, expression, timeout=15):
    response = client.call('Runtime.evaluate', {'expression': expression,
                           'returnByValue': True, 'awaitPromise': True}, timeout=timeout)
    if not isinstance(response, dict) or response.get('exceptionDetails'):
        raise TaskBlocked('page-script-failed', '页面检查未返回有效结果，未自动重试。')
    value = (response.get('result') or {}).get('value')
    if not isinstance(value, dict):
        raise TaskBlocked('invalid-page-result', '页面未返回结构化检查结果。')
    return value


def _fingerprint(url):
    return hashlib.sha256(url.encode('utf-8')).hexdigest()


def _page_target(client, binding_path, binding, target_id, expected_hash):
    pages = client.list_pages()
    matches = [page for page in pages if page.get('id') == target_id]
    if matches or not binding_path.exists():
        return matches, expected_hash, False
    saved = _read_object(binding_path, 16384)
    if (saved.get('binding') != binding or not isinstance(saved.get('targetId'), str)
            or not isinstance(saved.get('urlSha256'), str) or not _HASH.fullmatch(saved['urlSha256'])):
        raise TaskBlocked('invalid-page-binding', '恢复页签与当前学习包的绑定不一致。')
    matches = [page for page in pages if page.get('id') == saved['targetId']]
    return matches, saved['urlSha256'], bool(matches)


def _known_web_page(url):
    if not isinstance(url, str) or len(url) > 4000 or re.search(r'[\x00-\x20\x7f\\]', url):
        raise TaskBlocked('unsafe-reopen-url', '恢复地址必须是已知、无凭证的 HTTP(S) 页面网址。')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in {'http', 'https'} and parsed.username is None and parsed.password is None
                and not _URL_SECRET.search(unquote('?' + parsed.query + '#' + parsed.fragment))):
            return page_identity(url)
    except ValueError:
        pass
    raise TaskBlocked('unsafe-reopen-url', '网址必须是已知、无凭证的 HTTP(S) 页面网址。')


def _reopen_address(job, expected_page, initial_hash):
    url = job.get('reopenUrl', expected_page['url'])
    if _known_web_page(url) != expected_page:
        raise TaskBlocked('unsafe-reopen-url', '恢复地址超出原页面范围，或包含凭证相关参数。')
    if _fingerprint(url) != initial_hash and ('reopenUrl' not in job or url != expected_page['url']):
        raise TaskBlocked('reopen-url-needed', '学习包未保存完整路由。请用已验证的入口地址或原无凭证网址恢复。')
    return url


def _research_scope(job, binding_path, original):
    """A research ID owns one route and one target, independent of the capture tab."""
    operation = job['operation']
    address = job.get('researchUrl')
    if operation == 'ensure-page':
        if address is None:
            raise TaskBlocked('research-url-needed', '准备研究页签时请给出已知的 researchUrl。')
        page = _known_web_page(address)
        url_hash = _fingerprint(address)
        binding = {'source': original, 'researchId': job['researchId'],
                   'researchPage': page, 'researchUrlSha256': url_hash}
    else:
        if 'researchUrl' in job:
            raise TaskBlocked('unexpected-research-url', '研究地址仅用于 ensure-page；后续操作使用 researchId。')
        if not binding_path.exists():
            raise TaskBlocked('research-not-ready', '请先为这个 researchId 准备研究页签。')
        binding = _read_object(binding_path, 16384).get('binding')
        if (not isinstance(binding, dict) or binding.get('source') != original
                or binding.get('researchId') != job['researchId']):
            raise TaskBlocked('invalid-page-binding', '研究页签与当前学习包的绑定不一致。')
        page, url_hash = binding.get('researchPage'), binding.get('researchUrlSha256')
    if (not isinstance(page, dict) or page.get('origin') != original['page']['origin']
            or not isinstance(url_hash, str) or not _HASH.fullmatch(url_hash)):
        raise TaskBlocked('research-origin-mismatch', '研究页签只接受本学习包同源的已知网址。')
    if binding_path.exists() and _read_object(binding_path, 16384).get('binding') != binding:
        raise TaskBlocked('research-scope-changed', '该研究编号已绑定其他网址。为新网址使用新的 researchId。')
    return page, url_hash, binding, address


def _ensure_page(client, job, binding_path, binding, target_id, expected_page, initial_hash, open_url=None):
    try:
        with workspace_lock(binding_path.with_suffix('.lock')):
            matches, expected_hash, previously_reopened = _page_target(
                client, binding_path, binding, target_id, job.get('expectedUrlHash', initial_hash))
            if len(matches) > 1:
                raise TaskBlocked('target-not-found', '原页签身份不唯一，无法恢复。')
            created = not matches
            if created:
                url = open_url if open_url is not None else _reopen_address(job, expected_page, initial_hash)
                expected_hash = _fingerprint(url)
                target = client.create_blank_page()
                atomic_write(binding_path, json.dumps({
                    'binding': binding, 'targetId': target['id'], 'urlSha256': expected_hash,
                }) + '\n')
            else:
                target = matches[0]
                if previously_reopened:
                    expected_hash = job.get('expectedUrlHash', expected_hash)
            client.connect(target)
            if created:
                navigation = client.call('Page.navigate', {'url': url}, timeout=10)
                if navigation.get('errorText') or navigation.get('isDownload'):
                    raise TaskBlocked('page-navigation-failed', '已打开恢复页签，但该网址未能加载为网页。')
            deadline = time.monotonic() + 10
            while True:
                try:
                    live = _eval(client, _LIVE_PAGE, timeout=2)
                except CDPProtocolError:
                    # During navigation Chrome can replace the execution context.
                    live = {}
                url = live.get('url', '')
                if url and url != 'about:blank' and live.get('ready') in {'interactive', 'complete'}:
                    if _fingerprint(url) != expected_hash:
                        raise TaskBlocked('page-changed', '页签进入了登录页或其他路由，请核对恢复入口和登录状态。')
                    return live, target['id'], created
                if time.monotonic() >= deadline:
                    raise TaskBlocked('page-not-ready', '页签尚未就绪，请检查该页签及站点响应。')
                time.sleep(0.15)
    except HandoffError:
        raise TaskBlocked('page-recovery-busy', '另一个请求正在恢复此学习页签，稍后再检查。') from None


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
    success. Read-only jobs may recover a closed learning tab, but actions may
    only use a tab that is already present.
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
        binding_path = _inside(learning / '.page-recovery.json', learning)
        lock_path = _inside(binding_path.with_suffix('.lock'), learning)
        reserved_files = {binding_path, lock_path}
        research_directory = _inside(learning / '.research', learning)
        _inside(source, learning)
        destination = _inside(output_path, learning)
        if (destination in {source, bundle_path, binding_path, lock_path}
                or destination.is_relative_to(research_directory) or destination.suffix.lower() != '.json'):
            destination = None
            raise TaskBlocked('invalid-output-path', '结果需要独立 JSON 文件，不能覆盖任务或来源。')
        if source in {binding_path, lock_path} or source.is_relative_to(research_directory):
            raise TaskBlocked('invalid-input', '页签恢复记录不能用作任务。')
        if type(job.get('schemaVersion')) is not int or job['schemaVersion'] != 1 or operation not in _OPERATIONS:
            raise TaskBlocked('unsupported-job', '只支持 schemaVersion=1 及运行协议中列出的任务。')
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
        initial_hash = connection.get('initialUrlSha256')
        expected_hash = job.get('expectedUrlHash', initial_hash)
        if not isinstance(target_id, str) or not target_id.strip():
            raise TaskBlocked('missing-target', '缺少当前选中的页签 ID，请回采集器重新选页。')
        if any(not isinstance(digest, str) or not _HASH.fullmatch(digest)
               for digest in (initial_hash, expected_hash)):
            raise TaskBlocked('missing-url-fingerprint', '缺少有效的当前路由指纹，不能猜测页面。')
        binding = {'endpoint': endpoint, 'sourceTargetId': target_id,
                   'page': expected_page, 'initialUrlSha256': initial_hash}
        research_id = job.get('researchId')
        research_url = None
        if 'researchId' in job:
            if not isinstance(research_id, str) or not _RESEARCH_ID.fullmatch(research_id):
                raise TaskBlocked('invalid-research-id', 'researchId 需要 1 至 48 个小写字母、数字、下划线或连字符，且以字母或数字开头。')
            result['researchId'] = research_id
            binding_path = _inside(research_directory / ('tab-' + research_id + '.json'), research_directory)
            _inside(binding_path.with_suffix('.lock'), research_directory)
            research_page, research_hash, binding, research_url = _research_scope(job, binding_path, binding)
            if operation in {'inspect', 'act'} and research_hash != initial_hash:
                raise TaskBlocked('research-page-needs-capture', '此研究页使用不同路由，请先查看候选控件，再为该页面单独采集后执行。')
            expected_page, initial_hash = research_page, research_hash
            expected_hash = job.get('expectedUrlHash', initial_hash)
            target_id = None
        elif operation == 'discover-controls' or 'researchUrl' in job:
            raise TaskBlocked('research-id-needed', '查看新控件或指定研究地址需要独立的 researchId。')
        ready_path = None
        if operation in {'observe-network', 'query-json'} and not expected_page['origin'].startswith(('http://', 'https://')):
            raise TaskBlocked('network-page-required', '接口观察和查询需要选中 HTTP(S) 页面。')
        if operation == 'observe-network':
            duration_ms = job.get('durationMs', 8000)
            if type(duration_ms) is not int or not 250 <= duration_ms <= 20000:
                raise TaskBlocked('invalid-observation-window', '网络观察时长应为 250 至 20000 毫秒。')
            if 'readyPath' in job:
                ready_value = job['readyPath']
                if not isinstance(ready_value, str) or not Path(ready_value).is_absolute():
                    raise TaskBlocked('invalid-ready-path', '监听就绪文件需要独立绝对路径。')
                ready_path = _inside(ready_value, learning)
                if (ready_path in {destination, source} | reserved_files or ready_path.is_relative_to(research_directory)
                        or ready_path.suffix.lower() != '.json' or ready_path.exists()):
                    raise TaskBlocked('invalid-ready-path', '监听就绪文件需要不存在的独立 JSON 文件。')
        if operation == 'query-json':
            query = prepare_query(job)
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
        if operation == 'ensure-page':
            live, target_id, reopened = _ensure_page(client, job, binding_path, binding,
                                                     target_id, expected_page, initial_hash, research_url)
            expected_hash = _fingerprint(live['url'])
        else:
            matches, bound_hash, _ = _page_target(client, binding_path, binding, target_id, initial_hash)
            if (not matches and research_id is None and operation in {'inspect', 'read-page', 'query-json'}
                    and action_policy == 'infer-from-purpose' and expected_hash == initial_hash
                    and expected_page['origin'].startswith(('http://', 'https://'))):
                live, recovered_id, reopened = _ensure_page(client, job, binding_path, binding,
                                                            target_id, expected_page, initial_hash)
                expected_hash = _fingerprint(live['url'])
                result.update(targetId=recovered_id, reopened=reopened)
            elif len(matches) != 1:
                raise TaskBlocked('target-not-found', '目标页签不存在或身份不唯一。页签已关闭时先用 ensure-page。')
            else:
                expected_hash = job.get('expectedUrlHash', bound_hash)
                client.connect(matches[0])
                live = _eval(client, _LIVE_PAGE)
                if research_id is not None:
                    result['targetId'] = matches[0]['id']
        live_url = live.get('url')
        if not isinstance(live_url, str):
            raise TaskBlocked('missing-page-url', '页面没有返回当前地址。')
        actual_page = page_identity(live_url)
        actual_hash = _fingerprint(live_url)
        result['page'] = {**actual_page, 'title': _clean(live.get('title', '')), 'urlSha256': actual_hash}
        if actual_page != expected_page or actual_hash != expected_hash:
            raise TaskBlocked('page-changed', '当前地址或页面路由已经变化；未操作控件，请重新核对页面。')
        if operation == 'ensure-page':
            observed = {'status': 'completed', 'targetId': target_id, 'reopened': reopened}
        elif operation == 'observe-network':
            def mark_ready():
                if ready_path is not None:
                    ready_path.parent.mkdir(parents=True, exist_ok=True)
                    with ready_path.open('x', encoding='utf-8') as stream:
                        json.dump({'status': 'listening', 'windowMs': duration_ms}, stream)
            observed = observe_network(client, live_url, duration_ms, mark_ready)
            after_observation = _eval(client, '(() => ({url: location.href}))()')
            if after_observation.get('url') != live_url:
                raise TaskBlocked('page-changed', '监听期间起始页已变化，本次不登记接口证据。')
        elif operation == 'query-json':
            observed = _eval(client, query_expression(query, live_url))
            result['businessSuccessVerified'] = False
        else:
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
        if operation == 'query-json' and observed['status'] == 'completed':
            projected = observed.get('evidence', {}).get('fields', {})
            if not isinstance(projected, dict) or projected.keys() != query['fields'].keys():
                raise TaskBlocked('invalid-query-result', '接口投影与请求字段不一致。')
            result['evidence']['fields'] = {key: _clean(value) for key, value in projected.items()}
        result['operation'] = operation
        result['schemaVersion'] = 1
        result['page'] = {**actual_page, 'title': _clean(live.get('title', '')), 'urlSha256': actual_hash}
        if operation == 'act':
            result['businessSuccessVerified'] = False
    except TaskBlocked as error:
        result.update(status='unknown' if result['actionAttempted'] else 'blocked', error={'code': error.code, 'message': error.message})
    except NetworkJobError as error:
        result.update(status='blocked', error={'code': error.code, 'message': str(error)})
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
