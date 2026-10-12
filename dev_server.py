"""
Local dev server that mimics Vercel's routing for this project.

  python dev_server.py            # port 3000
  python dev_server.py --port 4000

Routes:
  - /api/<name>  ->  api/<name>.py handler class
  - /renewals    ->  renewals.html  (via vercel.json rewrites)
  - /            ->  dashboard_live.html  (via vercel.json rewrites)
  - anything else -> static file in project root

For LOCAL DEVELOPMENT ONLY. Not deployed.
"""
import argparse
import importlib.util
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env(path=".env.local"):
    """Load env from .env.local into os.environ."""
    p = os.path.join(PROJECT_ROOT, path)
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def read_vercel_rewrites():
    """Load rewrites from vercel.json so local paths match prod."""
    vj = os.path.join(PROJECT_ROOT, "vercel.json")
    if not os.path.exists(vj):
        return {}
    cfg = json.load(open(vj))
    rewrites = {}
    for r in cfg.get("rewrites", []):
        rewrites[r["source"]] = r["destination"]
    return rewrites


def load_api_handler(name):
    """Import api/<name>.py and return its `handler` class."""
    api_path = os.path.join(PROJECT_ROOT, "api", f"{name}.py")
    if not os.path.exists(api_path):
        return None
    spec = importlib.util.spec_from_file_location(f"api_{name}", api_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "handler", None)


class DevHandler(BaseHTTPRequestHandler):
    rewrites = {}

    def log_message(self, fmt, *args):
        sys.stdout.write(f"  {self.command:<6} {self.path:<40} -> {fmt % args}\n")

    def _send_file(self, path):
        """Serve a static file from the project root."""
        full = os.path.join(PROJECT_ROOT, path.lstrip("/"))
        if not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return
        ext = os.path.splitext(full)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch_api(self, name):
        """Dispatch /api/<name> to the matching Vercel-style handler class."""
        HandlerCls = load_api_handler(name)
        if HandlerCls is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"API /api/{name} not found"}).encode())
            return
        method_name = f"do_{self.command}"
        if not hasattr(HandlerCls, method_name):
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Method {self.command} not allowed"}).encode())
            return
        # Rebind every method from the API handler onto `self` so helper calls
        # like `self._send_json` resolve correctly against the actual handler
        # (BaseHTTPRequestHandler can't be re-instantiated with an open socket).
        for attr in dir(HandlerCls):
            if attr.startswith("__"):
                continue
            val = getattr(HandlerCls, attr, None)
            if callable(val) and attr not in dir(BaseHTTPRequestHandler):
                setattr(self, attr, val.__get__(self))
        try:
            getattr(self, method_name)()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Handler crashed: {e}"}).encode())
            except Exception:
                pass

    def _handle(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Route /api/* to the Python handlers.
        if path.startswith("/api/"):
            api_name = path[len("/api/"):].split("/")[0]
            self._dispatch_api(api_name)
            return

        # Apply vercel.json rewrites (exact-match only, which is all we use).
        if path in self.rewrites:
            path = self.rewrites[path]

        # Default root.
        if path == "/":
            path = "/dashboard_live.html"

        self._send_file(path)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_OPTIONS(self):
        # Preflight for API calls.
        if self.path.startswith("/api/"):
            self._dispatch_api(self.path[len("/api/"):].split("/")[0].split("?")[0])
            return
        self.send_response(204)
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3000)
    args = ap.parse_args()

    load_env()
    DevHandler.rewrites = read_vercel_rewrites()

    print(f"Dev server on http://localhost:{args.port}")
    print(f"  Rewrites: {DevHandler.rewrites}")
    print(f"  Env: SHEETS_WEBHOOK_URL={'set' if os.environ.get('SHEETS_WEBHOOK_URL') else 'MISSING'}, "
          f"SHARED_SECRET={'set' if os.environ.get('SHARED_SECRET') else 'MISSING'}")
    print(f"  Open: http://localhost:{args.port}/renewals")
    print()

    server = HTTPServer(("127.0.0.1", args.port), DevHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
