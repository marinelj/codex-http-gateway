import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { test } from 'node:test';

import { createCodexProxy } from './codex-proxy.mjs';

async function start(handler) {
  const server = createServer(handler);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

async function close(server) {
  await new Promise((resolve) => server.close(resolve));
}

test('streams a submission and preserves body, auth, status and polling location', async () => {
  const upstream = await start(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    assert.equal(req.url, '/v1/runs?after=1');
    assert.equal(req.headers.authorization, 'Bearer caller-token');
    assert.equal(req.headers['idempotency-key'], 'one');
    assert.equal(body, '{"prompt":"hello"}');
    res.writeHead(202, { Location: '/codex/v1/runs/run_test', 'Content-Type': 'application/json' });
    res.end('{"status":"running"}');
  });
  const proxy = createCodexProxy(upstream.origin);
  const provider = await start((req, res) => {
    if (proxy(req, res)) return;
    res.end('existing API');
  });
  try {
    const result = await fetch(provider.origin + '/codex/v1/runs?after=1', {
      method: 'POST',
      body: '{"prompt":"hello"}',
      headers: { Authorization: 'Bearer caller-token', 'Idempotency-Key': 'one' },
    });
    assert.equal(result.status, 202);
    assert.equal(result.headers.get('location'), '/codex/v1/runs/run_test');
    assert.equal((await result.json()).status, 'running');
    assert.equal(await (await fetch(provider.origin + '/health')).text(), 'existing API');
  } finally {
    await close(provider.server);
    await close(upstream.server);
  }
});

test('returns a clear 502 if the local gateway stops', async () => {
  const upstream = await start((_req, res) => res.end());
  await close(upstream.server);
  const proxy = createCodexProxy(upstream.origin);
  const provider = await start(proxy);
  try {
    const result = await fetch(provider.origin + '/codex/healthz');
    assert.equal(result.status, 502);
    assert.equal((await result.json()).error.code, 'codex_upstream_unavailable');
  } finally {
    await close(provider.server);
  }
});

test('only an explicit loopback origin enables the mount', () => {
  assert.equal(createCodexProxy(), null);
  for (const origin of ['http://example.org', 'https://localhost', 'http://localhost/path']) {
    assert.throws(() => createCodexProxy(origin), /loopback/);
  }
});
