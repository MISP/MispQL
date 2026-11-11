import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mispql import config


@pytest.fixture
def no_config(monkeypatch, tmp_path):
    """No config file, environment or history reaches the code under test."""
    monkeypatch.setattr(config, 'REPO_CONFIG', tmp_path / 'absent.toml')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for name in ('MISPQL_CONFIG', 'MISPQL_URL', 'MISPQL_KEY'):
        monkeypatch.delenv(name, raising=False)


class Stub:
    """A MISP stand-in: answers each request with the next queued reply and records it."""

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
            self.answer(json.loads(body))

        def do_GET(self):
            self.answer(None)

        def answer(self, body):
            stub.requests.append({'method': self.command, 'path': self.path, 'headers': dict(self.headers),
                                  'body': body})
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

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # A client that gave up (timeout, closed pipe) is expected; the traceback would
            # otherwise land, from a daemon thread, in some later test's captured stderr.
            if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
                super().handle_error(request, client_address)

    server = Server(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    stub.url = f'http://127.0.0.1:{server.server_port}'
    yield stub
    server.shutdown()
    server.server_close()
