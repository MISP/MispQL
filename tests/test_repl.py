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
ME = {'User': {'email': 'me@example.org'}, 'Role': {'name': 'User'}, 'Organisation': {'name': 'ORG'}}
pytestmark = pytest.mark.usefixtures('no_config')


class Terminal:
    buffer = io.BytesIO()

    def isatty(self):
        return True


@pytest.fixture
def misp(stub, monkeypatch):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setenv('MISPQL_KEY', 'the-key')
    stub.reply(ME)  # the banner's
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
    ('', True), ('  \n', True), ('quit', True), ('EXIT;', True), (' help ', True), ('Space  Impact', True),
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
    assert out.startswith(f'mispql 0.1.0, default: me@example.org, User, ORG at {misp.url}. End statements with ;')
    assert out.count(json.dumps(ROWS['response']['Attribute'], indent=2)) == 2
    assert [r['body']['limit'] for r in misp.requests[1:]] == [1, 2]


def test_errors_leave_the_session_open(misp, session):
    misp.reply(ROWS)
    code, out, err = session('SELECT * FROM;\rSELECT * FROM attributes LIMIT 1;\r\x04')
    assert code == 0
    assert err == "ERROR 1:14: expected a table name, found ';'\nSELECT * FROM;\n             ^\n"
    assert len(misp.requests) == 2


def test_help_and_exit(misp, session):
    code, out, err = session('help\rEXIT;\rSELECT * FROM;\r')
    assert (code, err) == (0, '')
    assert repl.HELP in out


def test_snake2(misp, session):
    code, out, err = session('SNAKE2;\r\x1b[Aq' 'quit\r')
    assert (code, err) == (0, '')


def test_space_impact(misp, session):
    code, out, err = session('SPACE  IMPACT;\r\x1b[A q' 'quit\r')
    assert (code, err) == (0, '')


def test_ctrl_c_clears_the_statement(misp, session):
    misp.reply(ROWS)
    session('SELECT * FROM attributes\rWHERE id = 1\x03SELECT * FROM attributes LIMIT 3;\rquit\r')
    assert [r['body'] for r in misp.requests[1:]] == [{'returnFormat': 'json', 'limit': 3, 'page': 1}]


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
    assert len(calls) == 2 and len(misp.requests) == 2


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
                           'objects': {'id': {}, 'object_name': {}},
                           'galaxy_clusters': {'id': {}, 'galaxy_name': {}, 'galaxy_namespace': {}, 'meta.<key>': {}},
                           'notes': {'id': {}, 'note': {}, 'note_type_name': {}, 'object_uuid': {}}}[table],
                'limits': {}}


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
    assert offered(completions, 'SELECT * FROM events WHERE ev') == [
        'events', 'event_reports', 'eventinfo', 'event_timestamp']
    assert offered(completions, 'SELECT * FROM events WHERE o') == [
        'OFFSET', 'OR', 'ORDER', 'objects', 'opinions', 'organisations']
    assert offered(completions, 'SELECT ob FROM objects', cursor=9) == ['objects', 'object_name']
    assert offered(completions, 'SELECT * FROM objects LIMIT 1; SELECT * FROM events WHERE eventi') == ['eventinfo']
    assert offered(completions, 'SELECT * FROM galaxy_clusters WHERE galaxy_n') == ['galaxy_name', 'galaxy_namespace']
    assert offered(completions, 'SELECT * FROM galaxy_clusters WHERE me') == ['meta.']
    assert offered(completions, 'SELECT * FROM notes WHERE no') == ['NOT', 'notes', 'note', 'note_type_name']
    assert discovery.calls == ['/events/queryDescribe', '/objects/queryDescribe', '/galaxy_clusters/queryDescribe',
                               '/analyst_data/queryDescribe/Note']


def test_completion_without_fields():
    discovery = Discovery(fail=True)
    completions = repl.Completions(discovery)
    assert offered(completions, 'SELECT * FROM events WHERE eve') == ['events', 'event_reports']
    assert offered(completions, 'SELECT * FROM events WHERE ev') == ['events', 'event_reports']
    assert offered(completions, 'SELECT * FROM nosuch WHERE eve') == ['events', 'event_reports']
    assert discovery.calls == ['/events/queryDescribe']
    assert offered(repl.Completions(None), 'SELECT * FROM events WHERE ev') == ['events', 'event_reports']


def test_completion_on_an_empty_word():
    assert offered(repl.Completions(None), 'SELECT * FROM ')[-11:] == [
        'attributes', 'events', 'objects', 'galaxy_clusters', 'event_reports', 'notes', 'opinions',
        'relationships', 'collections', 'tags', 'organisations']
    assert offered(repl.Completions(None), 'SELECT * FROM gal') == ['galaxy_clusters']


def test_no_completion_inside_a_string():
    completions = repl.Completions(Discovery())
    fields = ['events', 'event_reports', 'eventinfo', 'event_timestamp']
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


@pytest.fixture
def two(misp, other_stub, tmp_path, monkeypatch):
    """[default] is misp, with the environment's key; [other] is a second MISP."""
    path = tmp_path / 'config.toml'
    path.write_text(f'[default]\nurl = "{misp.url}"\n[other]\nurl = "{other_stub.url}"\nkey = "key-o"\n')
    path.chmod(0o600)
    monkeypatch.setenv('MISPQL_CONFIG', str(path))
    return other_stub


def test_use_says_who_and_resets_completion(two, session, monkeypatch):
    resets = []
    monkeypatch.setattr(repl.Completions, 'reset', lambda self, client: resets.append(client.settings.url))
    two.reply({'User': {'email': 'o@example.org'}, 'Role': {'name': 'Admin'}, 'Organisation': {'name': 'O'}})
    code, out, err = session('USE other;\rUSE nope;\rquit\r')
    assert (code, resets) == (0, [two.url])
    assert '\nother: o@example.org, Admin, O\n' in out
    assert 'unknown database nope: the config has default, other' in err
    assert {r['headers']['Authorization'] for r in two.requests} == {'key-o'}


def test_prompt_names_the_database():
    class Connections:
        current = None
    assert repl.prompt(Connections()) == 'mispql> '
    Connections.current = 'prod-readonly'
    assert repl.prompt(Connections()) == 'mispql [prod-readonly]> '


def test_banner_warns_when_misp_does_not_say_who(stub, session, monkeypatch):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setenv('MISPQL_KEY', 'the-key')
    stub.reply({'name': 'Authentication failed.'}, status=403)
    code, out, err = session('quit\r')
    assert out.startswith(f'mispql 0.1.0, default at {stub.url}. End statements with ;')
    assert (code, err) == (0, 'WARNING: cannot tell who default is: key rejected\n')
