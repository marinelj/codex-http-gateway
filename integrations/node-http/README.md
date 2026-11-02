# Share an existing Node HTTP port

Copy `codex-proxy.mjs` into an existing Node HTTP server (Node 20.6+), then mount
the gateway before the server's normal route dispatch:

```js
import { createServer } from 'node:http';
import { createCodexProxy } from './codex-proxy.mjs';

const codex = createCodexProxy(process.env.CODEX_UPSTREAM);
createServer((req, res) => {
  if (codex?.(req, res)) return;
  existingHandler(req, res);
}).listen(8080);
```

Set `CODEX_UPSTREAM=http://127.0.0.1:18080` on the provider and
`GATEWAY_HOST=127.0.0.1 GATEWAY_PORT=18080 GATEWAY_ROOT_PATH=/codex` on the gateway.
Requests to `/codex/*` are forwarded with the prefix stripped. Without the upstream
setting, the mount is disabled. Other paths remain the provider's responsibility.

The adapter accepts loopback HTTP origins only, limits request bodies to 512 KiB,
sets a 15-second upstream inactivity timeout, removes hop-by-hop headers, and streams
responses with backpressure. It forwards the caller's authentication as-is. Configure
browser CORS and allowed headers (including `Idempotency-Key`) in the existing provider.

```sh
node --test integrations/node-http/proxy.test.mjs
```
