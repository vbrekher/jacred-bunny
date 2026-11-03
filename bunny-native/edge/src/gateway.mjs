// Preserve original HTTP contracts by delegating to the SQL-backed .NET runtime.
// No server credential is injected. The original authentication middleware is authoritative.
const REQUEST_LIMIT = 2 * 1024 * 1024;
const RESPONSE_LIMIT = 8 * 1024 * 1024;
const METHODS = new Set(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']);
const REQUEST_HEADERS = ['accept', 'accept-language', 'content-type', 'authorization', 'x-api-key', 'x-dev-key', 'origin', 'access-control-request-method', 'access-control-request-headers', 'range'];
const RESPONSE_HEADERS = ['content-type', 'content-disposition', 'allow', 'www-authenticate', 'access-control-allow-origin', 'access-control-allow-methods', 'access-control-allow-headers', 'access-control-allow-credentials', 'access-control-expose-headers', 'content-range', 'accept-ranges'];
const PRIVATE = { 'Cache-Control': 'private, no-store, max-age=0', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff', 'Vary': 'Origin', 'X-JacRed-Gateway': 'original-dotnet' };
function unavailable(status, code) {
  return new Response(JSON.stringify({ error: code, productionReady: false }), { status, headers: { ...PRIVATE, 'Content-Type': 'application/json; charset=utf-8' } });
}
function workerOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || !/^mc-[a-z0-9]+\.bunny\.run$/.test(url.hostname) || url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash) return null;
    return url.origin;
  } catch { return null; }
}
async function boundedBody(stream, limit, signal) {
  if (!stream) return null;
  const reader = stream.getReader();
  const chunks = []; let size = 0;
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new RangeError('body limit'); }
      chunks.push(value);
    }
  } finally { signal.removeEventListener('abort', abort); reader.releaseLock(); }
  const result = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}
export function createGateway({ env = {}, fetcher = fetch, timeoutMs = 15000 } = {}) {
  const origin = workerOrigin(env.JACRED_WORKER_ORIGIN);
  return async function gateway(request) {
    if (env.JACRED_EDGE_GATEWAY_ENABLED !== 'true' || !origin) return unavailable(503, 'GATEWAY_NOT_CONFIGURED');
    if (!METHODS.has(request.method)) return new Response(null, { status: 405, headers: { ...PRIVATE, Allow: [...METHODS].join(', ') } });
    const incoming = new URL(request.url);
    if (incoming.href.length > 16384) return unavailable(414, 'REQUEST_TOO_LARGE');
    // Concatenate only the parsed path/query onto an independently verified fixed origin.
    // A leading // path must never become another authority or a redirect destination.
    const target = new URL(origin);
    target.pathname = incoming.pathname; target.search = incoming.search;
    const headers = new Headers();
    for (const name of REQUEST_HEADERS) if (request.headers.has(name)) headers.set(name, request.headers.get(name));
    headers.set('Cache-Control', 'no-store');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const abort = () => controller.abort();
    request.signal.addEventListener('abort', abort, { once: true });
    if (request.signal.aborted) controller.abort();
    let readingRequest = true;
    try {
      const body = await boundedBody(request.body, REQUEST_LIMIT, controller.signal);
      if (controller.signal.aborted) return unavailable(504, 'UPSTREAM_TIMEOUT');
      readingRequest = false;
      const response = await fetcher(target.href, { method: request.method, headers, body, redirect: 'manual', signal: controller.signal });
      if (response.status >= 300 && response.status < 400) {
        await response.body?.cancel();
        return unavailable(502, 'UPSTREAM_REDIRECT_REFUSED');
      }
      const resultHeaders = new Headers(PRIVATE);
      for (const name of RESPONSE_HEADERS) if (response.headers.has(name)) resultHeaders.set(name, response.headers.get(name));
      const result = await boundedBody(response.body, RESPONSE_LIMIT, controller.signal);
      if (controller.signal.aborted) return unavailable(504, 'UPSTREAM_TIMEOUT');
      return new Response(request.method === 'HEAD' || [204, 205, 304].includes(response.status) ? null : result, { status: response.status, headers: resultHeaders });
    } catch (error) {
      if (controller.signal.aborted) return unavailable(504, 'UPSTREAM_TIMEOUT');
      if (error instanceof RangeError) return unavailable(readingRequest ? 413 : 502, readingRequest ? 'REQUEST_TOO_LARGE' : 'UPSTREAM_BODY_TOO_LARGE');
      // Never return exception messages, request URLs, headers, tokens, or origin configuration.
      return unavailable(502, 'UPSTREAM_UNAVAILABLE');
    } finally {
      clearTimeout(timer);
      request.signal.removeEventListener('abort', abort);
    }
  };
}
