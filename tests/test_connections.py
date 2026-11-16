import io
import sys

import pytest

from mispql.cli import main

ROWS = {'response': {'Attribute': [{'id': '1'}]}}
pytestmark = pytest.mark.usefixtures('no_config')


def me(email, role='User', org='ORG'):
    return {'User': {'email': email}, 'Role': {'name': role}, 'Organisation': {'name': org}}


class Script:
    buffer = io.BytesIO()

    def isatty(self):
        return False


@pytest.fixture
def run(monkeypatch, capsys):
    def run(*argv):
        monkeypatch.setattr(sys, 'stdin', Script())
        code = main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err
    return run


@pytest.fixture
def config(tmp_path, stub, other_stub, monkeypatch):
    """Profiles a and b on two MISPs; c without a key; d where nothing listens."""
    path = tmp_path / 'config.toml'
    path.write_text(f'[a]\nurl = "{stub.url}"\nkey = "key-a"\n'
                    f'[b]\nurl = "{other_stub.url}"\nkey = "key-b"\n'
                    f'[c]\nurl = "{other_stub.url}"\n'
                    '[d]\nurl = "http://127.0.0.1:9"\nkey = "key-d"\n')
    path.chmod(0o600)
    monkeypatch.setenv('MISPQL_CONFIG', str(path))
    return path


def keys(stub):
    return {request['headers']['Authorization'] for request in stub.requests}


def test_each_key_reaches_only_its_own_misp(config, stub, other_stub, run, monkeypatch):
    monkeypatch.setenv('MISPQL_KEY', 'env-key')  # for the starting connection, a, alone
    stub.reply(me('a@example.org'))
    other_stub.reply(me('b@example.org'))
    other_stub.reply(ROWS)
    stub.reply(me('a@example.org'))
    stub.reply(ROWS)
    code, out, err = run('--profile', 'a', '-e',
                         'WHOAMI; USE b; SELECT * FROM attributes LIMIT 1; USE a; SELECT * FROM attributes LIMIT 1')
    assert (code, err) == (0, '')
    assert keys(stub) == {'env-key'} and keys(other_stub) == {'key-b'}
    assert 'a@example.org' in out and 'b@example.org' not in out  # USE says nothing in a script
    assert [r['path'] for r in other_stub.requests] == ['/users/view/me', '/attributes/restSearch']


def test_show_databases(config, stub, other_stub, run):
    stub.reply(me('a@example.org', 'Org Admin', 'CIRCL'))
    other_stub.reply({'name': 'Authentication failed.'}, status=403)
    code, out, err = run('--profile', 'a', '-e', 'SHOW DATABASES')
    assert (code, err) == (0, '')
    lines = out.splitlines()
    assert [cell.strip() for cell in lines[3].split('|')[1:7]] == ['*', 'a', stub.url, 'a@example.org',
                                                                  'Org Admin', 'CIRCL']
    assert '| b ' in lines[4] and '(key rejected)' in lines[4]
    assert '| c ' in lines[5] and '(no key in the config)' in lines[5]
    assert '| d ' in lines[6] and '(cannot reach http://127.0.0.1:9/users/view/me)' in lines[6]
    assert lines[-1] == '4 databases'
    assert keys(other_stub) == {'key-b'}


def test_use_checks_the_key_and_stays_put_when_it_is_rejected(config, stub, other_stub, run):
    other_stub.reply({'name': 'Authentication failed.'}, status=403)
    stub.reply(me('a@example.org'))
    code, out, err = run('--profile', 'a', '-e', 'USE b; WHOAMI')
    assert (code, out) == (1, '')
    assert err == 'ERROR 1:1: HTTP 403: Authentication failed.\n'
    code, out, err = run('--profile', 'a', '-e', 'USE c')
    assert (code, err) == (2, 'ERROR 1:1: c: no API key in the config\n')


def test_unknown_databases_stop_the_script_before_anything_is_sent(config, stub, run, monkeypatch):
    code, out, err = run('--profile', 'a', '-e', 'WHOAMI; USE nope')
    assert (code, out) == (1, '')
    assert err.startswith('ERROR 1:13: unknown database nope: the config has a, b, c, d\n')
    assert stub.requests == []
    monkeypatch.delenv('MISPQL_CONFIG')
    code, out, err = run('-e', 'USE a')
    assert err.startswith('ERROR 1:5: unknown database a: there is no config file\n')


def test_without_a_config_the_one_database_is_default(stub, run, monkeypatch):
    monkeypatch.setenv('MISPQL_URL', stub.url)
    monkeypatch.setenv('MISPQL_KEY', 'env-key')
    stub.reply(me('me@example.org'))
    code, out, err = run('-e', 'SHOW DATABASES')
    assert (code, err) == (0, '')
    assert '| * | default  |' in out and out.endswith('1 database\n')


def test_dry_run(config, run):
    assert run('--dry-run', '-e', 'SHOW DATABASES; USE b; WHOAMI') == (0, (
        'GET /users/view/me (a)\nGET /users/view/me (b)\nGET /users/view/me (c)\nGET /users/view/me (d)\n'
        'GET /users/view/me (b)\nGET /users/view/me\n'), '')
    code, out, err = run('--dry-run', '-e', 'USE e')
    assert (code, out) == (1, '') and 'unknown database e' in err
