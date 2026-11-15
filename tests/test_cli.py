import io
import json
import os
import subprocess
import sys

import pytest

from mispql import __version__
from mispql.cli import main
from mispql.client import Client

ROWS = {'response': {'Attribute': [{'id': '1', 'type': 'md5'}]}}
pytestmark = pytest.mark.usefixtures('no_config')


class FakeStdin:
    def __init__(self, data, tty):
        self.buffer = io.BytesIO(data)
        self.tty = tty

    def isatty(self):
        return self.tty


@pytest.fixture
def misp(stub, monkeypatch):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setenv('MISPQL_KEY', 'the-key')
    return stub


@pytest.fixture
def run(monkeypatch, capsys):
    def run(*argv, stdin=b'', tty=False):
        monkeypatch.setattr(sys, 'stdin', FakeStdin(stdin, tty))
        code = main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err
    return run


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(['--version'])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f'mispql {__version__}'


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, '-m', 'mispql', '--version'], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f'mispql {__version__}'


def test_statements_run_in_order(misp, run):
    misp.reply(ROWS)
    misp.reply(b'42')
    code, out, err = run('-e', "SELECT * FROM attributes LIMIT 1; SELECT COUNT(*) FROM attributes WHERE id > 0")
    assert (code, err) == (0, '')
    assert out == json.dumps(ROWS['response']['Attribute'], indent=2) + '\n42\n'
    assert [r['body'].get('returnFormat') for r in misp.requests] == ['json', 'count']


@pytest.mark.parametrize('how', ['file', 'dash', 'pipe', 'terminal'])
def test_input_from_a_file_or_stdin(misp, run, tmp_path, how):
    script = b'\xef\xbb\xbfSELECT * FROM attributes LIMIT 1;\n'
    path = tmp_path / 'script.mql'
    path.write_bytes(script)
    argv = {'file': [str(path)], 'dash': ['-'], 'pipe': [], 'terminal': []}[how]
    misp.reply(ROWS)
    # 'terminal': stdin at a terminal, stdout not (mispql > out): no prompt, stdin is the script.
    code, out, err = run(*argv, stdin=b'' if how == 'file' else script, tty=how == 'terminal')
    assert (code, err) == (0, '')
    assert json.loads(out) == ROWS['response']['Attribute']


@pytest.mark.parametrize('argv, stdin, tty, message', [
    (['-e', 'SELECT 1', 'x.mql'], b'', False, 'ERROR: give -e or a file, not both'),
    (['/nonexistent.mql'], b'', False, 'ERROR: /nonexistent.mql: No such file or directory'),
    ([], b'SELECT \xff', False, 'ERROR: stdin: not UTF-8 (byte 7)'),
])
def test_usage_errors(run, argv, stdin, tty, message):
    assert run(*argv, stdin=stdin, tty=tty) == (2, '', message + '\n')


def test_empty_input_needs_no_config(run):
    assert run(stdin=b'-- nothing here\n;') == (0, '', '')


def test_syntax_error_anywhere_sends_nothing(misp, run):
    code, out, err = run('-e', 'SELECT * FROM events LIMIT 1;\nSELECT * FROM;')
    assert (code, out) == (1, '')
    assert err == "ERROR 2:14: expected a table name, found ';'\nSELECT * FROM;\n             ^\n"
    assert misp.requests == []


def test_compile_error_needs_no_config(run):
    code, _, err = run('-e', 'SELECT * FROM events')
    assert code == 1
    assert err.startswith('ERROR 1:1: add a WHERE or a LIMIT')


def test_describe_and_show_tables(misp, run):
    found = {'tables': {'objects': {'fields': {'id': {'type': 'int', 'operators': ['='], 'level': ['object']}}}},
             'limits': {'max_depth': 5}}
    misp.reply(found)
    misp.reply(found)
    misp.reply({'message': 'You do not have the permission to use complex queries.'}, status=403)
    misp.reply(found)
    misp.reply({'message': 'Not found'}, status=404)
    misp.reply({'message': 'Not found'}, status=404)
    misp.reply(found)
    for _ in range(4):
        misp.reply({'message': 'Not found'}, status=404)
    misp.reply({'message': 'You do not have permission to use this functionality.'}, status=403)
    code, out, err = run('-v', '-e', 'DESCRIBE objects; SHOW TABLES')
    assert code == 0
    assert out.endswith('1 field\nORDER BY: none\nlimits: max_depth 5\n'
                        '+------------+\n| table      |\n+------------+\n| attributes |\n| objects    |\n'
                        '| notes      |\n+------------+\n3 tables\n')
    paths = [f'/{table}/queryDescribe' for table in ('objects', 'attributes', 'events', 'objects',
                                                     'galaxy_clusters', 'event_reports')]
    paths += [f'/analyst_data/queryDescribe/{kind}' for kind in ('Note', 'Opinion', 'Relationship')]
    paths += [f'/{table}/queryDescribe' for table in ('collections', 'tags', 'organisations')]
    assert err == ''.join(f'GET {misp.url}{path}\n' for path in paths)


def test_describe_fails_like_any_statement(misp, run):
    misp.reply({'message': 'You do not have the permission to use complex queries.'}, status=403)
    assert run('-e', 'DESCRIBE events') == (
        1, '', 'ERROR 1:1: HTTP 403: You do not have the permission to use complex queries.\n')


def test_query_errors_stop_the_script(misp, run):
    text = ('SELECT * FROM attributes LIMIT 1;\nSELECT * FROM attributes\n  WHERE nosuch = 1 LIMIT 1;\n'
            'SELECT * FROM attributes LIMIT 1')
    misp.reply(ROWS)
    misp.reply({'errors': [{'path': 'query.<unknown>', 'message': 'unknown field'},
                           {'path': 'limit', 'message': 'too big'}]}, status=400)
    code, out, err = run('-e', text)
    assert code == 1
    assert json.loads(out) == ROWS['response']['Attribute']
    assert err == (
        'ERROR 3:9: unknown field\n  WHERE nosuch = 1 LIMIT 1;\n        ^^^^^^\n'
        'ERROR 2:1: too big\nSELECT * FROM attributes\n^^^^^^^^^^^^^^^^^^^^^^^^\n'
    )
    assert len(misp.requests) == 2


def test_client_error_names_the_statement(misp, run):
    misp.reply(ROWS)
    misp.reply({'message': 'Authentication failed.'}, status=403)
    code, out, err = run('-e', 'SELECT * FROM attributes LIMIT 1;\n  SELECT * FROM events LIMIT 1')
    assert code == 1
    assert json.loads(out) == ROWS['response']['Attribute']
    assert err == 'ERROR 2:3: HTTP 403: Authentication failed.\n'


def test_config_errors(stub, run, monkeypatch, tmp_path):
    assert run('-e', 'SELECT * FROM events LIMIT 1') == (
        2, '', 'ERROR: no MISP URL: set url in a config file, or MISPQL_URL\n')
    path = tmp_path / 'config.toml'
    path.write_text(f'[default]\nurl = "{stub.url}"\nkey = "k"\n[other]\nurl = "{stub.url}"\n')
    path.chmod(0o644)
    assert run('--config', str(path), '--profile', 'other', '-e', 'SELECT * FROM events LIMIT 1') == (
        2, '', 'ERROR: no API key: set key in a config file, MISPQL_KEY or --key-file\n')
    stub.reply({'response': []})
    assert run('--config', str(path), '-e', 'SELECT * FROM events LIMIT 1') == (
        0, '[]\n', f'WARNING: {path} holds an API key and is readable by others (chmod 600 it)\n')


@pytest.mark.parametrize('typed, code', [(' prompted \n', 0), ('', 2)])
def test_key_prompt_on_a_terminal(stub, run, monkeypatch, tmp_path, typed, code):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setattr('getpass.getpass', lambda prompt: typed)
    script = tmp_path / 'script.mql'
    script.write_text('SELECT * FROM events LIMIT 1')
    stub.reply({'response': []})
    assert run(str(script), tty=True)[0] == code
    if code == 0:
        assert stub.requests[0]['headers']['Authorization'] == 'prompted'


def test_no_prompt_for_a_piped_script(stub, run, monkeypatch):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setattr('getpass.getpass', lambda prompt: pytest.fail('prompted'))
    code, _, err = run(stdin=b'SELECT * FROM events LIMIT 1')
    assert (code, err) == (2, 'ERROR: no API key: set key in a config file, MISPQL_KEY or --key-file\n')


def test_verbose_prints_each_request_sent(misp, run):
    misp.reply({'response': {'Attribute': [{'id': i} for i in (3, 4, 5)]}})
    misp.reply({'response': {'Attribute': []}})
    code, out, err = run('-v', '-e', 'SELECT * FROM attributes LIMIT 3 OFFSET 4')
    assert code == 0
    first, second = (r['body'] for r in misp.requests)
    url = f'{misp.url}/attributes/restSearch'
    assert err == f'POST {url}\n{json.dumps(first, indent=2)}\nPOST {url}\n{json.dumps(second, indent=2)}\n'
    assert 'the-key' not in err


def test_dry_run_sends_nothing(run):
    code, out, err = run('--dry-run', '-e', 'SELECT * FROM attributes LIMIT 3 OFFSET 4;'
                                            "SELECT COUNT(*) FROM events WHERE info LIKE 'x%';"
                                            'DESCRIBE events; SHOW TABLES')
    assert (code, err) == (0, '')
    body = {'returnFormat': 'json', 'limit': 3, 'page': 2}
    count = {'returnFormat': 'json', 'limit': 1, 'page': 1, 'metadata': 1,
             'query': {'info': {'LIKE': 'x%'}}}
    assert out == (
        f'POST /attributes/restSearch\n{json.dumps(body, indent=2)}\n'
        f'POST /attributes/restSearch (sent only when the first page is full)\n'
        f'{json.dumps({**body, "page": 3}, indent=2)}\n'
        f'POST /events/restSearch\n{json.dumps(count, indent=2)}\n'
        'GET /events/queryDescribe\n'
        'GET /attributes/queryDescribe\nGET /events/queryDescribe\nGET /objects/queryDescribe\n'
        'GET /galaxy_clusters/queryDescribe\nGET /event_reports/queryDescribe\n'
        'GET /analyst_data/queryDescribe/Note\nGET /analyst_data/queryDescribe/Opinion\n'
        'GET /analyst_data/queryDescribe/Relationship\nGET /collections/queryDescribe\n'
        'GET /tags/queryDescribe\nGET /organisations/queryDescribe\n'
    )


def test_output_warnings_go_to_stderr(misp, run):
    misp.reply(ROWS)
    code, out, err = run('-e', 'SELECT id, nosuch FROM attributes LIMIT 1 FORMAT flat')
    assert (code, out, err) == (0, 'id,nosuch\n1,\n', 'WARNING: no row has a column nosuch\n')


def test_interrupt(misp, run, monkeypatch):
    def interrupted(self, plan):
        raise KeyboardInterrupt
    monkeypatch.setattr(Client, 'run', interrupted)
    assert run('-e', 'SELECT * FROM events LIMIT 1') == (130, '', '')


@pytest.mark.parametrize('reply, argv', [
    (b'x' * (1 << 20), ['-e', 'SELECT * FROM attributes LIMIT 5 FORMAT csv']),
    ({'response': {'Attribute': [{'id': str(i), 'value': 'x' * 50} for i in range(20000)]}},
     ['-e', 'SELECT * FROM attributes LIMIT 20000']),
    # Output left in sys.stdout's own buffer: only the dup2 keeps the exit flush quiet.
    (None, ['--dry-run', '-e', 'SELECT * FROM attributes LIMIT 1;' * 3000]),
])
def test_broken_pipe_ends_quietly(stub, tmp_path, reply, argv):
    if reply is not None:
        stub.reply(reply)
    env = {**os.environ, 'MISPQL_KEY': 'k', 'HOME': str(tmp_path)}
    env.pop('MISPQL_CONFIG', None)
    config_file = tmp_path / 'config.toml'
    config_file.write_text(f'[default]\nurl = "{stub.url}"\n')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'mispql', '--config', str(config_file), *argv],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    proc.stdout.close()
    assert proc.wait(timeout=30) == 141
    assert proc.stderr.read() == b''
    proc.stderr.close()
