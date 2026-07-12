"""Live web version of the CoC dashboard (for Render or any WSGI host).

Optimal API usage:
- The CoC API caches its own data for ~1-2 minutes, so we fetch at most once
  every CACHE_SECONDS (default 60) no matter how many visitors load the page.
- The page auto-reloads every CACHE_SECONDS + 15 so visitors always see the
  freshest data the API can give, without ever hammering it.

Config via environment variables:
  COC_API_KEY    the API key (required in production - never commit it)
  COC_API_BASE   optional, e.g. https://cocproxy.royaleapi.dev/v1 for the
                 RoyaleAPI proxy (whitelist IP 45.79.218.79 in your key)
  CACHE_SECONDS  optional, default 60

Local test:  python app.py  ->  http://localhost:8000
Render:      gunicorn app:app
"""
import os
import time
import threading

import dashboard as d

CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
RELOAD_SECONDS = CACHE_SECONDS + 15

_lock = threading.Lock()
_cache = {"at": 0.0, "html": None}


def _get_key():
    k = os.environ.get("COC_API_KEY")
    if k:
        return k.strip()
    if d.KEY_FILE.exists():                      # local fallback
        return d.KEY_FILE.read_text().strip()
    return None


def _render():
    key = _get_key()
    if not key:
        return d.build_error_page("No API key configured. Set the COC_API_KEY "
                                  "environment variable.")
    data = d.fetch_all(key)
    if data["c_err"]:
        return d.build_error_page(
            f"Clan fetch failed: {data['c_err']}. If this says accessDenied, "
            "this server's IP is not whitelisted on the key - see README-DEPLOY.md.")
    return d.build_page(data, live_seconds=RELOAD_SECONDS)


def _get_page():
    now = time.time()
    with _lock:
        if _cache["html"] is None or now - _cache["at"] >= CACHE_SECONDS:
            _cache["html"] = _render()
            _cache["at"] = now
        return _cache["html"]


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path == "/healthz":
        body = b"ok"
        start_response("200 OK", [("Content-Type", "text/plain"),
                                  ("Content-Length", str(len(body)))])
        return [body]
    if path == "/sw.js":
        # Minimal service worker: makes the site installable; no caching so
        # war data always comes fresh from the server.
        body = (b"self.addEventListener('install',e=>self.skipWaiting());"
                b"self.addEventListener('activate',e=>self.clients.claim());"
                b"self.addEventListener('fetch',()=>{});")
        start_response("200 OK", [("Content-Type", "text/javascript"),
                                  ("Content-Length", str(len(body))),
                                  ("Cache-Control", "public, max-age=86400")])
        return [body]
    if path == "/manifest.json":
        _get_page()                     # ensure the manifest is built
        body = d.LAST_MANIFEST.encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/manifest+json"),
                                  ("Content-Length", str(len(body))),
                                  ("Cache-Control", "public, max-age=3600")])
        return [body]
    if path != "/":
        body = b"not found"
        start_response("404 Not Found", [("Content-Type", "text/plain"),
                                         ("Content-Length", str(len(body)))])
        return [body]
    body = _get_page().encode("utf-8")
    start_response("200 OK", [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", f"public, max-age={CACHE_SECONDS}"),
    ])
    return [body]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", "8000"))
    print(f"Serving on http://localhost:{port}  (Ctrl+C to stop)")
    make_server("", port, app).serve_forever()
