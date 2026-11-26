"""Observe request metadata and run a bounded same-origin JSON query."""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import parse_qsl, unquote, urlsplit


_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,63}\Z')
_PATH_SEGMENT = re.compile(r'[A-Za-z0-9_.~-]{1,80}\Z')
_SECRET = re.compile(
    r'passw|passwd|secret|credential|cookie|bearer|token|authorization|apikey|csrf|xsrf|'
    r'privatekey|sessionid|sessionkey|oauth|signature|otp|^auth$|^sid$|^codeverifier$',
    re.I,
)
_OPAQUE = re.compile(r'^\d+$|[0-9a-f]{8}-[0-9a-f-]{20,}|^[a-z0-9_-]{20,}$|^eyJ', re.I)


class NetworkJobError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _public_name(name):
    return (isinstance(name, str) and bool(_NAME.fullmatch(name))
            and not _SECRET.search(re.sub(r'[-_]', '', name))
            and name not in {'__proto__', 'prototype', 'constructor'})


def prepare_query(job):
    if job.get('purposeScope') != 'read-only':
        raise NetworkJobError('query-not-read-only', '同源查询需要已确认的只读 GET 接口。')
    request = job.get('request')
    if not isinstance(request, dict) or set(request) != {'path', 'query', 'fields'}:
        raise NetworkJobError('invalid-query', '查询需要 path、query 和 fields，使用浏览器会话。')
    path = request['path']
    if (not isinstance(path, str) or not path.startswith('/') or len(path) > 400
            or any(not _PATH_SEGMENT.fullmatch(s) or s in {'.', '..'} or s.startswith('eyJ')
                   for s in path[1:].rstrip('/').split('/'))):
        raise NetworkJobError('invalid-query-path', '查询只接受同源的接口路径，例如 /api/orders。')
    query = request['query']
    if (not isinstance(query, dict) or len(query) > 15
            or any(not _public_name(k) or not isinstance(v, str) or len(v) > 300
                   or any(ord(c) < 32 for c in v) or re.search(r'\bBearer\s|eyJ[A-Za-z0-9_-]{8,}\.', v, re.I)
                   for k, v in query.items())):
        raise NetworkJobError('invalid-query-parameters', '查询参数应为业务名称与业务值，请使用当前浏览器会话。')
    fields = request['fields']
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 20:
        raise NetworkJobError('invalid-query-fields', '请明确列出结果需要的 1 至 20 个字段。')
    projections = {}
    for alias, value in fields.items():
        parts = value.split('.') if isinstance(value, str) else []
        if (not _public_name(alias) or not 1 <= len(parts) <= 7 or parts.count('*') > 1
                or any(not (_public_name(piece) or piece == '*' or (piece.isascii() and piece.isdigit() and len(piece) <= 2))
                       for piece in parts)):
            raise NetworkJobError('invalid-query-fields', '返回字段应是业务数据路径，例如 items.*.status。')
        projections[alias] = parts
    return {'path': path, 'query': query, 'fields': projections}


def query_expression(request, live_url):
    return '(' + _QUERY + ')(' + json.dumps({**request, 'expectedLiveUrl': live_url}, ensure_ascii=True) + ')'


def _origin(url):
    parts = urlsplit(url)
    if (parts.scheme not in {'http', 'https'} or not parts.hostname
            or parts.username is not None or parts.password is not None):
        raise ValueError('origin needs HTTP(S)')
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)


def _path_template(path):
    pieces = []
    for piece in path.split('/')[:12]:
        decoded = unquote(piece)
        public = not decoded or (_PATH_SEGMENT.fullmatch(decoded) and not _OPAQUE.search(decoded)
                                 and not _SECRET.search(re.sub(r'[-_]', '', decoded)))
        pieces.append(decoded if public else '{id}')
    return '/'.join(pieces) or '/'


def observe_network(client, live_url, duration_ms, mark_ready):
    origin = _origin(live_url)
    frame_id = client.call('Page.getFrameTree')['frameTree']['frame']['id']
    requests = {}
    lock = threading.Lock()
    disconnected = threading.Event()

    def on_event(event):
        method = event.get('method')
        params = event.get('params', {})
        if method == 'CDP.disconnected':
            disconnected.set()
            return
        request_id = params.get('requestId')
        if not isinstance(request_id, str):
            return
        with lock:
            if method == 'Network.requestWillBeSent':
                requests.pop(request_id, None)
                if params.get('frameId') != frame_id or params.get('type') not in {'XHR', 'Fetch'} or len(requests) >= 100:
                    return
                request = params.get('request', {})
                url = request.get('url', '')
                verb = request.get('method', '')
                try:
                    if len(url) > 4096 or _origin(url) != origin or verb not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'}:
                        return
                    parts = urlsplit(url)
                    keys = sorted({k for k, _ in parse_qsl(parts.query, max_num_fields=60) if _public_name(k)})
                except (TypeError, ValueError):
                    return
                requests[request_id] = {'method': verb, 'pathTemplate': _path_template(parts.path),
                                        'queryKeys': keys, 'type': params['type']}
            elif request_id in requests and method == 'Network.responseReceived':
                response = params.get('response', {})
                try:
                    if _origin(response.get('url', '')) != origin:
                        requests.pop(request_id, None)
                        return
                except (TypeError, ValueError):
                    return
                status = response.get('status')
                mime = response.get('mimeType')
                if isinstance(status, (int, float)):
                    requests[request_id]['httpStatus'] = status
                if isinstance(mime, str) and re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+', mime):
                    requests[request_id]['mimeType'] = mime[:80]
            elif request_id in requests and method in {'Network.loadingFinished', 'Network.loadingFailed'}:
                requests[request_id]['finished'] = method == 'Network.loadingFinished'

    previous = client.on_event
    client.on_event = on_event
    enabled = False
    try:
        client.call('Network.enable', {'maxPostDataSize': 0})
        enabled = True
        mark_ready()
        if disconnected.wait(duration_ms / 1000):
            raise NetworkJobError('network-disconnected', '监听期间页签断开，请先核对页签。')
    finally:
        client.on_event = previous
        if enabled and not disconnected.is_set():
            client.call('Network.disable')
    with lock:
        return {'status': 'completed', 'evidence': {'requests': list(requests.values()),
                                                    'windowMs': duration_ms, 'sameOriginOnly': True}}


_QUERY = r"""async task => {
  const fail = (code, message, httpStatus) => ({status: 'blocked', error: {code, message},
                                                ...(httpStatus === undefined ? {} : {evidence: {httpStatus}})});
  if (location.href !== task.expectedLiveUrl) return fail('page-changed', '查询起始页已变化。');
  const url = new URL(task.path, location.origin);
  if (url.origin !== location.origin || url.pathname !== task.path) {
    return fail('origin-mismatch', '查询地址与当前业务页不同源。');
  }
  for (const [name, value] of Object.entries(task.query)) url.searchParams.append(name, value);
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 8000);
  try {
    const response = await fetch(url, {
      method: 'GET', mode: 'same-origin', credentials: 'same-origin', redirect: 'manual',
      cache: 'no-store', headers: {'Accept': 'application/json'}, signal: abort.signal
    });
    if (response.type === 'opaqueredirect') return fail('query-redirect', '接口发生跳转，请检查登录或路由。');
    if (!response.ok) return fail('query-http-error', '接口返回错误，请核对会话和接口契约。', response.status);
    if (!/^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')) {
      return fail('query-not-json', '接口未返回 JSON，请核对会话和接口契约。', response.status);
    }
    if (!response.body) return fail('query-empty', '接口未返回结果。', response.status);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let size = 0;
    let text = '';
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 262144) {
        void reader.cancel();
        return fail('query-result-too-large', '接口结果超过读取上限，请缩小查询范围。', response.status);
      }
      text += decoder.decode(value, {stream: true});
    }
    const payload = JSON.parse(text + decoder.decode());
    const missing = Symbol('missing');
    const select = (value, path, depth = 0) => {
      if (depth === path.length) {
        if (typeof value === 'string') {
          return /\b(?:bearer\s+|eyJ[a-z0-9_-]{8,}\.|sk-[a-z0-9_-]{12,})/i.test(value) ? missing : value.slice(0, 600);
        }
        return value === null || typeof value === 'number' || typeof value === 'boolean' ? value : missing;
      }
      const key = path[depth];
      if (key === '*' && Array.isArray(value)) {
        if (value.length > 50) return missing;
        const selected = value.map(item => select(item, path, depth + 1));
        return selected.includes(missing) ? missing : selected;
      }
      return value && typeof value === 'object' && Object.hasOwn(value, key)
        ? select(value[key], path, depth + 1) : missing;
    };
    const selected = Object.entries(task.fields).map(([name, path]) => [name, select(payload, path)]);
    if (selected.some(([, value]) => value === missing)) {
      return fail('query-result-shape', '接口结果缺少所需字段或字段类型已变化。', response.status);
    }
    if (location.href !== task.expectedLiveUrl) return fail('page-changed', '查询期间已切换业务页面。');
    return {status: 'completed', evidence: {httpStatus: response.status, fields: Object.fromEntries(selected)}};
  } catch (_) {
    return fail('query-unavailable', '同源查询未完成，请先核对会话和接口。');
  } finally {
    clearTimeout(timer);
  }
}"""
