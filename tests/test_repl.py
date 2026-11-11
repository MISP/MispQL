import io
import json
import stat
import subprocess
import sys

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mispql import repl
from mispql.cli import main
from mispql.client import Client, ClientError

ROWS = {'response': {'Attribute': [{'id': '1'}]}}
pytestmark = pytest.mark.usefixtures('no_config')


class Terminal:
    buffer = io.BytesIO()

    def isatty(self):
        return True


@pytest.fixture
def misp(stub, monkeypatch):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setenv('MISPQL_KEY', 'the-key')
    return stub


@pytest.fixture
def session(monkeypatch, capsys):
    """Type `keys` into mispql at a terminal; field completion is left out (it would use up replies)."""
    monkeypatch.setattr(sys, 'stdin', Terminal())
    monkeypatch.setattr(repl.Completions, 'table_fields', lambda self, table: [])

    def session(keys, *argv):
        monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
        with create_pipe_input() as keyboard:
            keyboard.send_text(keys)
            keyboard.close()  # EOF after the keys: a session that does not end on them cannot hang
            with create_app_session(input=keyboard, output=DummyOutput()):
                code = main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err
    return session


@pytest.mark.parametrize('text, runs', [
    ('', True), ('  \n', True), ('quit', True), ('EXIT;', True), (' help ', True),
    ('SELECT * FROM attributes LIMIT 1;', True),
    ('SELECT * FROM attributes LIMIT 1;  -- a note', True),
    ('SELECT * FROM attributes\nLIMIT 1', False),
    ("SELECT * FROM attributes WHERE value = 'a;", False),
    ("SELECT * FROM attributes WHERE value = 'a;'", False),
    ('SELECT * FROM attributes WHERE value = @;', True),
    ('-- only a comment', False),
])
def test_when_enter_runs(text, runs):
    assert repl.complete(text) is runs


def test_statements_run_until_quit(misp, session):
    misp.reply(ROWS)
    misp.reply(ROWS)
    code, out, err = session('SELECT * FROM attributes\rLIMIT 1;\rSELECT id FROM attributes LIMIT 2\x1b\r'
                             'quit\rSELECT * FROM;\r')
    assert (code, err) == (0, '')
    assert out.startswith(f'mispql 0.1.0, {misp.url}. End statements with ;')
    assert out.count(json.dumps(ROWS['response']['Attribute'], indent=2)) == 2
    assert [r['body']['limit'] for r in misp.requests] == [1, 2]


def test_errors_leave_the_session_open(misp, session):
    misp.reply(ROWS)
    code, out, err = session('SELECT * FROM;\rSELECT * FROM attributes LIMIT 1;\r\x04')
    assert code == 0
    assert err == "ERROR 1:14: expected a table name, found ';'\nSELECT * FROM;\n             ^\n"
    assert len(misp.requests) == 1


def test_help_and_exit(misp, session):
    code, out, err = session('help\rEXIT;\rSELECT * FROM;\r')
    assert (code, err) == (0, '')
    assert repl.HELP in out


def test_ctrl_c_clears_the_statement(misp, session):
    misp.reply(ROWS)
    session('SELECT * FROM attributes\rWHERE id = 1\x03SELECT * FROM attributes LIMIT 3;\rquit\r')
    assert [r['body'] for r in misp.requests] == [{'returnFormat': 'json', 'ignore': 1, 'limit': 3, 'page': 1}]


def test_ctrl_c_abandons_a_running_query(misp, session, monkeypatch):
    run = Client.run
    calls = []

    def interrupted_once(self, plan):
        calls.append(plan)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return run(self, plan)
    monkeypatch.setattr(Client, 'run', interrupted_once)
    misp.reply(ROWS)
    code, out, err = session('SELECT * FROM attributes LIMIT 1;\rSELECT * FROM attributes LIMIT 1;\rquit\r')
    assert (code, err) == (0, 'interrupted\n')
    assert len(calls) == 2 and len(misp.requests) == 1


def test_history_is_private(misp, session, tmp_path):
    misp.reply(ROWS)
    session('SELECT * FROM attributes\rLIMIT 1;\rquit\r')
    path = tmp_path / 'state' / 'mispql' / 'history'
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    entries = [line for line in path.read_text().splitlines() if line.startswith('+')]
    assert entries == ['+SELECT * FROM attributes', '+LIMIT 1;', '+quit']


def test_dry_run_needs_no_config(session):
    code, out, err = session('DESCRIBE events;\r\x04', '--dry-run')
    assert (code, err) == (0, '')
    assert out.startswith('mispql 0.1.0, dry run: nothing is sent.')
    assert 'GET /events/queryDescribe\n' in out


def test_completion_has_its_own_untraced_client(misp, session, monkeypatch):
    clients = []
    init = repl.Completions.__init__
    monkeypatch.setattr(repl.Completions, '__init__', lambda self, client: clients.append(client) or init(self, client))
    session('quit\r', '-v')
    [client] = clients
    assert client.trace is None
    assert client.settings.url == misp.url


def test_config_errors_stop_at_startup(session):
    assert session('\x04') == (2, '', 'ERROR: no MISP URL: set url in a config file, or MISPQL_URL\n')


class Discovery:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def describe(self, table, endpoint):
        self.calls.append(endpoint)
        if self.fail:
            raise ClientError('HTTP 403: no', 403)
        return {'fields': {'events': {'id': {}, 'eventinfo': {}, 'event_timestamp': {}},
                           'objects': {'id': {}, 'object_name': {}}}[table], 'limits': {}}


def offered(completions, text, cursor=None):
    document = Document(text, len(text) if cursor is None else cursor)
    event = CompleteEvent(completion_requested=True)
    return [c.text for c in completions.get_completions(document, event)]


def test_completion_on_tab_only(misp, session, monkeypatch):
    options = {}
    init = repl.PromptSession.__init__
    monkeypatch.setattr(repl.PromptSession, '__init__', lambda self, **kw: options.update(kw) or init(self, **kw))
    session('quit\r')
    assert options['complete_while_typing'] is False


def test_completion_offers_the_statement_tables_fields():
    discovery = Discovery()
    completions = repl.Completions(discovery)
    assert offered(completions, 'SELECT * FROM events WHERE ev') == ['events', 'eventinfo', 'event_timestamp']
    assert offered(completions, 'SELECT * FROM events WHERE o') == ['OFFSET', 'OR', 'ORDER', 'objects']
    assert offered(completions, 'SELECT ob FROM objects', cursor=9) == ['objects', 'object_name']
    assert offered(completions, 'SELECT * FROM objects LIMIT 1; SELECT * FROM events WHERE eventi') == ['eventinfo']
    assert discovery.calls == ['/events/queryDescribe', '/objects/queryDescribe']


def test_completion_without_fields():
    discovery = Discovery(fail=True)
    completions = repl.Completions(discovery)
    assert offered(completions, 'SELECT * FROM events WHERE eve') == ['events']
    assert offered(completions, 'SELECT * FROM events WHERE ev') == ['events']
    assert offered(completions, 'SELECT * FROM nosuch WHERE eve') == ['events']
    assert discovery.calls == ['/events/queryDescribe']
    assert offered(repl.Completions(None), 'SELECT * FROM events WHERE ev') == ['events']


def test_completion_on_an_empty_word():
    assert offered(repl.Completions(None), 'SELECT * FROM ')[-3:] == ['attributes', 'events', 'objects']


def test_no_completion_inside_a_string():
    completions = repl.Completions(Discovery())
    fields = ['events', 'eventinfo', 'event_timestamp']
    assert offered(completions, "SELECT * FROM events WHERE eventinfo LIKE 'ev") == []
    assert offered(completions, 'SELECT * FROM events WHERE eventinfo = "it\'s ev') == []
    assert offered(completions, "SELECT * FROM events WHERE eventinfo = 'it''s ev") == []
    assert offered(completions, "SELECT * FROM events WHERE eventinfo = 'evil' LIMIT 1", cursor=42) == []
    assert offered(completions, "SELECT * FROM events WHERE eventinfo = 'evil' AND ev") == fields
    assert offered(completions, 'SELECT * FROM events WHERE `ev') == fields


def test_batch_never_loads_prompt_toolkit():
    code = ("import sys; from mispql.cli import main; main(['--dry-run', '-e', 'SHOW TABLES']); "
            "print('prompt_toolkit' in sys.modules)")
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.stdout.splitlines()[-1] == 'False'
