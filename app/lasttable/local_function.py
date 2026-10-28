"""Local-only invocation adapter, using the same notification code as the OCI Function."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .notification import notify


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192 or self.path != '/invoke':
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(size))
            answer = notify(payload)
            status = 200
        except Exception as error:
            answer, status = {'error_type': type(error).__name__}, 500
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(answer).encode())


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
