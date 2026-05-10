"""
Simple HTTP server that serves frontend static files AND proxies /api requests to backend.
Run on port 80 for unified access.
"""
import http.server
import urllib.request
import os
import json

DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
BACKEND_URL = "http://127.0.0.1:8000"


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST_DIR, **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy_request("GET")
        else:
            # SPA fallback: serve index.html for non-file paths
            file_path = os.path.join(DIST_DIR, self.path.lstrip("/"))
            if not os.path.exists(file_path) or os.path.isdir(file_path):
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy_request("POST")
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy_request("PUT")
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy_request("DELETE")
        else:
            self.send_response(404)
            self.end_headers()

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            self._proxy_request("PATCH")
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self._proxy_request("OPTIONS")

    def _proxy_request(self, method):
        try:
            url = BACKEND_URL + self.path
            if self.path.startswith("/api/v1/ws/"):
                # WebSocket requests return 400 on regular HTTP
                self.send_response(400)
                self.end_headers()
                return

            data = None
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                data = self.rfile.read(content_length)

            req = urllib.request.Request(
                url,
                data=data,
                method=method,
            )

            # Forward relevant headers
            for header in ["Authorization", "Content-Type", "Accept"]:
                value = self.headers.get(header)
                if value:
                    req.add_header(header, value)

            # Add X-Forwarded headers
            req.add_header("X-Forwarded-For", self.client_address[0])
            req.add_header("X-Forwarded-Proto", "http")

            with urllib.request.urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(resp.read())

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            try:
                self.wfile.write(e.read())
            except Exception:
                pass
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            error_body = json.dumps({"code": 502, "message": f"Backend unavailable: {str(e)}"}).encode()
            self.wfile.write(error_body)

    def log_message(self, format, *args):
        # Suppress noise — only log API calls
        if "/api/" in str(args[0]):
            super().log_message(format, *args)


if __name__ == "__main__":
    PORT = 80
    server = http.server.HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"Unified server running on http://0.0.0.0:{PORT}")
    print(f"  Frontend: {DIST_DIR}")
    print(f"  Backend proxy: {BACKEND_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
        server.shutdown()
