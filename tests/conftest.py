import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class Stub:
    """A MISP stand-in: answers each POST with the next queued reply and records the request."""

    def __init__(self):
        self.replies = []
        self.requests = []

    def reply(self, payload, status=200, headers=None, delay=0):
        self.replies.append((status, headers or {}, payload, delay))


@pytest.fixture
def stub():
    stub = Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            stub.requests.append({'path': self.path, 'headers': dict(self.headers), 'body': json.loads(body)})
            status, headers, payload, delay = stub.replies.pop(0)
            time.sleep(delay)
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    stub.url = f'http://127.0.0.1:{server.server_port}'
    yield stub
    server.shutdown()
    server.server_close()
