import { request } from 'node:http';

const PREFIX = '/codex';
const HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

function endError(res, status, code) {
  if (res.headersSent) {
    res.destroy();
    return;
  }
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    Connection: 'close',
  });
  res.end(JSON.stringify({ error: { code, message: code.replaceAll('_', ' ') } }));
}

function cleanHeaders(headers) {
  const blocked = new Set(HOP_HEADERS);
  for (const name of (headers.connection ?? '').split(',')) {
    blocked.add(name.trim().toLowerCase());
  }
  return Object.fromEntries(Object.entries(headers).filter(([name]) => !blocked.has(name)));
}

/** Optional loopback-only mount; the upstream authenticates each client's own bearer token. */
export function createCodexProxy(upstream) {
  if (!upstream) return null;
  const target = new URL(upstream);
  if (
    target.protocol !== 'http:' ||
    !['127.0.0.1', '[::1]', 'localhost'].includes(target.hostname) ||
    target.pathname !== '/' ||
    target.search ||
    target.hash ||
    target.username ||
    target.password
  ) {
    throw new Error('CODEX_UPSTREAM must be a plain loopback HTTP origin');
  }

  return function proxy(req, res) {
    const raw = req.url ?? '/';
    const path = raw.split('?')[0];
    if (path !== PREFIX && !path.startsWith(`${PREFIX}/`)) return false;
    let failed = false;
    let size = 0;
    const headers = cleanHeaders(req.headers);
    // Gateway uses a configured root_path, never an untrusted forwarded prefix.
    delete headers['x-forwarded-prefix'];
    delete headers['x-forwarded-for'];
    delete headers['x-forwarded-proto'];

    const upstreamReq = request({
      hostname: target.hostname.replace(/^\[|\]$/g, ''),
      port: target.port || 80,
      method: req.method,
      path: raw.slice(PREFIX.length) || '/',
      headers,
    });
    function fail(status, code) {
      if (failed || res.writableEnded || res.destroyed) return;
      failed = true;
      req.unpipe(upstreamReq);
      upstreamReq.destroy();
      endError(res, status, code);
    }
    upstreamReq.setTimeout(15_000, () => fail(504, 'codex_upstream_timeout'));
    upstreamReq.on('error', () => fail(502, 'codex_upstream_unavailable'));
    upstreamReq.on('response', (upstreamRes) => {
      if (failed) {
        upstreamRes.destroy();
        return;
      }
      res.writeHead(upstreamRes.statusCode, cleanHeaders(upstreamRes.headers));
      upstreamRes.on('error', () => fail(502, 'codex_upstream_disconnected'));
      upstreamRes.pipe(res);
    });
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > 512 * 1024) fail(413, 'body_too_large');
    });
    req.on('aborted', () => upstreamReq.destroy());
    req.on('error', () => upstreamReq.destroy());
    res.on('close', () => {
      if (!res.writableEnded) upstreamReq.destroy();
    });
    req.pipe(upstreamReq);
    return true;
  };
}
