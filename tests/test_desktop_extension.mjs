import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { activate, deactivate } from '../zcode-extension/cdp-picker-bridge/extension.js';

test('native bridge authenticates, forwards Host methods and never repeats admission', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'picker-extension-'));
  const calls = [];
  const session = { kind: 'app', sourceServerId: 'local', workspacePath: root, sessionId: 'sess_fixture' };
  // Official v3.18.2 operationRefSchema: epoch/revision belong to the wait
  // snapshot, never to the create receipt's operation or wait input target.
  const operation = { session, sessionId: session.sessionId, commandId: 'fixture-command' };
  let allowed = false;
  let writeAllowed = false;
  let failure = null;
  const exactKeys = (value, required, optional = []) => {
    assert.ok(value && typeof value === 'object' && !Array.isArray(value));
    for (const key of required) assert.ok(Object.hasOwn(value, key), `missing ${key}`);
    for (const key of Object.keys(value)) assert.ok([...required, ...optional].includes(key), `extra ${key}`);
  };
  const ctx = {
    extension: { id: 'cdp-picker-bridge@cdp-picker-local', path: root, dataPath: root },
    models: { list: async () => [{ id: 'provider/org/model', providerId: 'provider', modelId: 'org/model', label: 'org/model', providerName: 'Fixture' }] },
    grants: { request: async () => { allowed = writeAllowed = true; return { granted: ['sessions.fullAccess'] }; } },
    sessions: {
      listProjects: async () => { if (!allowed) throw Object.assign(Error('private details'), { code: 'not-granted' });return { projects: [] }; },
      createSession: async input => {
        exactKeys(input, ['prompt', 'requestId'], ['project', 'model', 'mode']);
        assert.equal(typeof input.prompt, 'string');
        assert.equal(typeof input.requestId, 'string');
        if (input.model !== undefined) assert.equal(typeof input.model, 'string');
        if (input.mode !== undefined) assert.ok(['build', 'guarded', 'yolo'].includes(input.mode));
        if (!writeAllowed) throw Object.assign(Error('private write permission details'), { code: 'not-granted' });
        calls.push(['create', input]);
        if (failure) throw Object.assign(Error('PRIVATE_PROVIDER_FIXTURE'), { code: failure });
        await new Promise(resolve => setTimeout(resolve, 20));
        return { requestId: input.requestId, creation: 'created', firstInput: 'accepted', delivery: 'start_now', session, operation };
      },
      sendToSession: async input => { calls.push(['send', input]);return { requestId: input.requestId, admission: 'accepted', delivery: 'start_now', session, operation }; },
      openSession: async input => { exactKeys(input, ['sessionId']);calls.push(['open', input]);return { status: 'opened' }; },
      readSession: async input => {
        exactKeys(input, ['sessionId', 'view'], ['cursor', 'limit']);
        assert.ok(['summary', 'messages'].includes(input.view));
        if (input.limit !== undefined) assert.ok(Number.isInteger(input.limit) && input.limit >= 1 && input.limit <= 200);
        return { view: 'messages', session, messages: [{ id: 'user-1', role: 'user', text: 'Fixture prompt' }] };
      },
      waitSessions: async input => {
        exactKeys(input, ['operations', 'mode'], ['timeoutMs']);
        assert.ok(['any', 'all'].includes(input.mode));
        assert.ok(Number.isInteger(input.timeoutMs) && input.timeoutMs >= 0 && input.timeoutMs <= 300000);
        for (const target of input.operations) {
          exactKeys(target, ['commandId'], ['session', 'sessionId']);
          assert.ok(target.session || target.sessionId);
          if (target.session && target.sessionId) assert.equal(target.session.sessionId, target.sessionId);
        }
        return { status: 'completed', operations: [{ operation, epoch: 'fixture-epoch', revision: 1, status: 'completed' }] };
      },
    },
    commands: { register() {} }, slots: { update() {} }, log: { info() {} },
  };
  try {
    const activated = await activate(ctx, { discoveryDirectory: path.join(root, 'discovery') });
    const discovery = JSON.parse(await readFile(activated.discoveryPath, 'utf8'));
    assert.match(discovery.endpoint, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(discovery.token.length, 64);
    const rpc = async (method, params = {}, extra = {}, auth = discovery.token, headers = {}) => {
      const response = await fetch(discovery.endpoint + '/rpc', { method: 'POST',
        headers: { authorization: 'Bearer ' + auth, 'content-type': 'application/json', ...headers },
        body: JSON.stringify({ method, params, ...extra }) });
      return { status: response.status, body: await response.json() };
    };
    const health = (await rpc('health')).body;
    assert.equal(health.result.nativeSessions, true);
    assert.equal(health.result.authorized, false);
    assert.equal(JSON.stringify(health).includes(discovery.token), false);
    assert.equal((await rpc('models')).body.result[0].modelId, 'org/model');
    assert.equal((await rpc('authorize')).body.result.granted[0], 'sessions.fullAccess');
    assert.equal((await rpc('health')).body.result.authorized, true);
    assert.equal((await rpc('health', {}, {}, 'wrong')).status, 403);
    assert.equal((await rpc('health', {}, {}, discovery.token, { origin: 'https://example.test' })).status, 403);
    const input = { requestId: 'fixture-1', prompt: 'Read the explicitly selected fixture context.', model: 'provider/org/model', mode: 'yolo' };
    assert.equal((await rpc('create', input, { requestId: 'wrong-outer-id' })).body.error.code, 'invalid-input');
    assert.equal(calls.filter(call => call[0] === 'create').length, 0);
    const [first, duplicate] = await Promise.all([rpc('create', input), rpc('create', input)]);
    assert.equal(first.body.result.requestId, input.requestId);
    assert.deepEqual(first.body.result.session, session);
    assert.deepEqual(duplicate.body.result.session, session);
    assert.equal(calls.filter(call => call[0] === 'create').length, 1);
    assert.equal((await rpc('create', input)).body.ok, true);
    assert.equal(calls.filter(call => call[0] === 'create').length, 1);
    assert.equal((await rpc('create', { ...input, prompt: 'changed' })).body.error.code, 'request-conflict');
    assert.equal((await rpc('receipt', { requestId: 'fixture-1' })).body.result.result.session.sessionId, session.sessionId);
    assert.equal((await rpc('open', { sessionId: session.sessionId })).body.result.status, 'opened');
    const read = (await rpc('read', { sessionId: session.sessionId, view: 'messages', limit: 100 })).body.result;
    assert.equal(read.view, 'messages');
    assert.equal(read.messages[0].role, 'user');
    const waited = (await rpc('wait', { operations: [operation], mode: 'all', timeoutMs: 1000 })).body.result;
    assert.equal(waited.operations[0].status, 'completed');
    assert.deepEqual(waited.operations[0].operation, operation);
    assert.equal(Object.hasOwn(waited.operations[0], 'sessionId'), false);
    assert.equal((await rpc('wait', { operations: [{ ...operation, epoch: 'wrong-extra' }], mode: 'all', timeoutMs: 1000 })).body.ok, false);
    assert.equal((await rpc('delete-anything')).body.error.code, 'invalid-method');
    // A successful read-health probe does not grant write/full-access.
    writeAllowed = false;
    const rejectedInput = { ...input, requestId: 'fixture-rejected' };
    assert.equal((await rpc('health')).body.result.authorized, true);
    assert.equal((await rpc('create', rejectedInput)).body.error.code, 'not-granted');
    assert.equal((await rpc('receipt', { requestId: rejectedInput.requestId })).body.result.status, 'rejected');
    await rpc('authorize');
    assert.equal((await rpc('create', rejectedInput)).body.error.code, 'not-granted');
    assert.equal(calls.filter(call => call[0] === 'create').length, 1);
    assert.equal((await rpc('create', { ...input, requestId: 'fixture-after-authorization' })).body.ok, true);
    const beforeUnknown = calls.filter(call => call[0] === 'create').length;
    failure = 'unavailable';
    const uncertainInput = { ...input, requestId: 'fixture-uncertain' };
    const uncertain = (await rpc('create', uncertainInput)).body;
    assert.equal(uncertain.ok, false);
    assert.equal(JSON.stringify(uncertain).includes('PRIVATE_PROVIDER'), false);
    assert.equal((await rpc('receipt', { requestId: uncertainInput.requestId })).body.result.status, 'unknown');
    assert.equal((await rpc('create', uncertainInput)).body.error.code, 'result-unknown');
    assert.equal(calls.filter(call => call[0] === 'create').length, beforeUnknown + 1);
    failure = null;
    await deactivate();
    assert.deepEqual(await readdir(path.join(root, 'discovery')), []);
    const again = await activate(ctx, { discoveryDirectory: path.join(root, 'discovery') });
    const next = JSON.parse(await readFile(again.discoveryPath, 'utf8'));
    const response = await fetch(next.endpoint + '/rpc', { method: 'POST', headers: { authorization: 'Bearer ' + next.token, 'content-type': 'application/json' }, body: JSON.stringify({ method: 'create', params: input }) });
    assert.equal((await response.json()).result.session.sessionId, session.sessionId);
    assert.equal(calls.filter(call => call[0] === 'create').length, beforeUnknown + 1);
  } finally {
    await deactivate();
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('picker-extension-'));
    await rm(root, { recursive: true, force: true });
  }
});
