import http from 'node:http';
import { randomBytes, randomUUID, createHash, timingSafeEqual } from 'node:crypto';
import { mkdir, readFile, writeFile, rename, unlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const VERSION = '0.3.1';
const MUTATIONS = new Set(['create', 'send']);
const PRECONDITIONS = new Set(['not-granted', 'not-declared', 'incompatible', 'invalid-input', 'forbidden']);
let active;

function fault(code, message) { return Object.assign(new Error(message), { code }); }
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
async function atomicJson(file, value) {
  const temporary = file + '.' + randomUUID() + '.tmp';
  try {
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600 });
    await rename(temporary, file);
  } finally { await unlink(temporary).catch(() => {}); }
}
function safeError(error) {
  const code = typeof error?.code === 'string' && /^[\w.-]{1,100}$/.test(error.code) ? error.code : 'native-call-failed';
  const messages = {
    'not-granted': '请在 ZCode 中确认连接桥的会话权限。',
    'not-declared': '连接桥声明与宿主能力不匹配。',
    'incompatible': '宿主缺少本连接需要的会话接口。',
    'invalid-input': '原生接口拒绝了无效参数。',
    'forbidden': '当前连接不允许操作该会话或项目。',
    'result-unknown': '上次调用结果未知；请核对原生对话，不会创建替代任务。',
    'request-conflict': '同一请求号对应的内容已变化。',
    'invalid-method': '此连接桥不支持该方法。',
  };
  return { code, message: messages[code] || 'ZCode 原生调用未确认成功；未自动重新发送。' };
}

export async function activate(ctx, options = {}) {
  if (active) throw fault('already-active', 'Bridge already active');
  const instanceId = randomUUID();
  const token = randomBytes(32).toString('hex');
  const directory = options.discoveryDirectory || path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'CDP-Control-Picker', 'desktop-bridges');
  const workspacePath = path.join(ctx.extension.dataPath, 'workspace');
  const receipts = path.join(ctx.extension.dataPath, 'picker-receipts');
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await mkdir(workspacePath, { recursive: true, mode: 0o700 });
  await mkdir(receipts, { recursive: true, mode: 0o700 });
  const file = path.join(directory, `${process.pid}-${instanceId}.json`);
  const metadata = { schemaVersion: 1, instanceId, pid: process.pid, pluginId: ctx.extension.id,
    pluginVersion: VERSION, workspacePath, startedAt: new Date().toISOString() };
  const inFlight = new Map();
  const receiptPath = id => path.join(receipts, createHash('sha256').update(id).digest('hex') + '.json');
  async function loadReceipt(id) {
    try { return JSON.parse(await readFile(receiptPath(id), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw fault('result-unknown', 'Receipt unavailable'); }
  }
  async function native(method, params) {
    switch (method) {
      case 'health': {
        const nativeSessions = ['createSession', 'openSession', 'waitSessions', 'readSession'].every(key => typeof ctx.sessions?.[key] === 'function');
        let authorized = false, authorizationReason;
        if (nativeSessions) {
          try { await ctx.sessions.listProjects({}); authorized = true; }
          catch (error) { authorizationReason = safeError(error).code; }
        }
        return { ...metadata, transport: 'zcode-extension-host', nativeSessions, authorized,
          ...(authorizationReason ? { authorizationReason } : {}),
          api: ['models', 'projects', 'authorize', 'create', 'send', 'open', 'read', 'wait', 'receipt'] };
      }
      case 'models': return ctx.models.list();
      case 'projects': return ctx.sessions.listProjects(params);
      case 'authorize': return ctx.grants.request();
      case 'create': return ctx.sessions.createSession(params);
      case 'send': return ctx.sessions.sendToSession(params);
      case 'open': return ctx.sessions.openSession(params);
      case 'read': return ctx.sessions.readSession(params);
      case 'wait': return ctx.sessions.waitSessions(params);
      case 'receipt': {
        if (typeof params.requestId !== 'string') throw fault('invalid-input', 'Missing request identity');
        const saved = await loadReceipt(params.requestId);
        if (!saved) return { status: 'not-found' };
        return saved.phase === 'completed' ? { status: 'accepted', result: saved.result } : { status: saved.phase, error: saved.error };
      }
      default: throw fault('invalid-method', 'Unsupported method');
    }
  }
  async function dispatch(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body) || typeof body.method !== 'string') throw fault('invalid-input', 'Invalid request');
    const params = body.params ?? {};
    if (!params || typeof params !== 'object' || Array.isArray(params)) throw fault('invalid-input', 'Invalid parameters');
    if (!MUTATIONS.has(body.method)) return native(body.method, params);
    const id = body.requestId || params.requestId;
    if (typeof id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(id) || params.requestId !== id) throw fault('invalid-input', 'Request identity required');
    const hash = createHash('sha256').update(JSON.stringify(canonical({ method: body.method, params }))).digest('hex');
    if (inFlight.has(id)) {
      const current = inFlight.get(id);
      if (current.hash !== hash) throw fault('request-conflict', 'Changed request');
      return current.promise;
    }
    const promise = (async () => {
      const previous = await loadReceipt(id);
      if (previous) {
        if (previous.hash !== hash) throw fault('request-conflict', 'Changed request');
        if (previous.phase === 'completed') return previous.result;
        if (previous.phase === 'rejected') throw fault(previous.error.code, 'Native precondition rejected');
        throw fault('result-unknown', 'Previous result unknown');
      }
      await atomicJson(receiptPath(id), { schemaVersion: 1, hash, phase: 'pending' });
      try {
        const result = await native(body.method, params);
        await atomicJson(receiptPath(id), { schemaVersion: 1, hash, phase: 'completed', result });
        return result;
      } catch (error) {
        const publicError = safeError(error);
        await atomicJson(receiptPath(id), { schemaVersion: 1, hash,
          phase: PRECONDITIONS.has(publicError.code) ? 'rejected' : 'unknown', error: publicError }).catch(() => {});
        throw error;
      }
    })();
    inFlight.set(id, { hash, promise });
    try { return await promise; } finally { inFlight.delete(id); }
  }
  const server = http.createServer(async (request, response) => {
    const send = (status, value) => { response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }); response.end(JSON.stringify(value)); };
    const auth = request.headers.authorization || '';
    const expected = Buffer.from('Bearer ' + token);
    const received = Buffer.from(auth);
    if (request.headers.origin || !['127.0.0.1', '::ffff:127.0.0.1'].includes(request.socket.remoteAddress)
        || expected.length !== received.length || !timingSafeEqual(expected, received)) {
      request.resume();send(403, { ok: false, error: { code: 'unauthorized', message: 'Local authenticated client required' } });return;
    }
    if (request.method !== 'POST' || request.url !== '/rpc') { request.resume();send(404, { ok: false, error: { code: 'not-found', message: 'Unknown endpoint' } });return; }
    let size = 0;
    const chunks = [];
    try {
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 256 * 1024) throw fault('invalid-input', 'Request too large');
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const result = await dispatch(body);
      send(200, { ok: true, result });
    } catch (error) {
      if (!response.headersSent && !response.destroyed) send(200, { ok: false, error: safeError(error) });
    }
  });
  server.requestTimeout = 330000;
  server.headersTimeout = 10000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const discovery = { ...metadata, endpoint: `http://127.0.0.1:${server.address().port}`, token };
  try { await atomicJson(file, discovery); }
  catch (error) { await new Promise(resolve => server.close(resolve));throw error; }
  active = { server, file, instanceId };
  ctx.commands?.register('authorize', () => ctx.grants.request());
  ctx.slots?.update('bridge', { text: '采集器', tooltip: '点击确认一次会话连接权限；消息由 ZCode 原生会话接收。' });
  ctx.log?.info('CDP picker native bridge is ready.');
  // The return value is useful for isolated contract tests; the Host ignores it.
  return { discoveryPath: file, ...metadata };
}

export async function deactivate() {
  const current = active;
  active = undefined;
  if (!current) return;
  await new Promise(resolve => current.server.close(resolve));
  try {
    const saved = JSON.parse(await readFile(current.file, 'utf8'));
    if (saved.instanceId === current.instanceId) await unlink(current.file);
  } catch {}
}
