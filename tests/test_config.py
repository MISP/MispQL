import re

import pytest

from mispql import config
from mispql.config import ConfigError, load


@pytest.fixture(autouse=True)
def no_real_config(tmp_path, monkeypatch):
    """Keep the developer's own config files out of every test."""
    monkeypatch.setattr(config, 'REPO_CONFIG', tmp_path / 'repo' / 'config.toml')
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def test_default_profile(tmp_path):
    path = write(tmp_path / 'c.toml', '[other]\nurl = "https://other"\n'
                                      '[default]\nurl = "https://misp"\nkey = "k1"\n')
    settings = load(config=path, env={})
    assert (settings.url, settings.key, settings.verify_ssl, settings.timeout) == ('https://misp', 'k1', True, 300)
    assert settings.source == path
    assert settings.warnings == []


def test_only_profile_and_named_profile(tmp_path):
    path = write(tmp_path / 'c.toml', '[prod]\nurl = "https://prod"\nverify_ssl = false\ntimeout = 30\n')
    settings = load(config=path, env={})
    assert (settings.url, settings.key, settings.verify_ssl, settings.timeout) == ('https://prod', None, False, 30)
    path = write(tmp_path / 'c.toml', '[a]\nurl = "https://a"\n[b]\nurl = "https://b"\n')
    assert load(config=path, profile='b', env={}).url == 'https://b'


@pytest.mark.parametrize('text, profile, message', [
    ('[a]\nurl = "x"\n[b]\nurl = "y"\n', None, 'no [default] profile; pick one of a, b with --profile'),
    ('[a]\nurl = "x"\n', 'b', 'no profile [b]'),
    ('url = "x"\n', None, 'no profile; add a [default] table'),
    ('[default]\nurl = "x"\napikey = "k"\n', None,
     'unknown setting apikey (known: url, key, verify_ssl, timeout)'),
    ('[default]\nurl = ""\n', None, 'url must be a non-empty string'),
    ('[default]\nurl = "x"\nkey = 5\n', None, 'key must be a non-empty string'),
    ('[default]\nurl = "x"\nverify_ssl = 1\n', None, 'verify_ssl must be true, false or a CA bundle path'),
    ('[default]\nurl = "x"\ntimeout = 0\n', None, 'timeout must be a positive number of seconds'),
    ('[default]\nurl = "x"\ntimeout = true\n', None, 'timeout must be a positive number of seconds'),
    ('[default\n', None, 'Expected'),
])
def test_bad_files(tmp_path, text, profile, message):
    path = write(tmp_path / 'c.toml', text)
    with pytest.raises(ConfigError, match=re.escape(message)):
        load(config=path, profile=profile, env={})


def test_lookup_order(tmp_path):
    home = write(tmp_path / 'home' / '.config' / 'mispql' / 'config.toml', '[default]\nurl = "https://home"\n')
    assert load(env={}).source == home
    write(config.REPO_CONFIG, '[default]\nurl = "https://repo"\n')
    assert load(env={}).url == 'https://repo'
    from_env = write(tmp_path / 'env.toml', '[default]\nurl = "https://env"\n')
    assert load(env={'MISPQL_CONFIG': str(from_env)}).url == 'https://env'
    given = write(tmp_path / 'given.toml', '[default]\nurl = "https://given"\n')
    assert load(config=given, env={'MISPQL_CONFIG': str(from_env)}).url == 'https://given'


def test_named_files_must_exist(tmp_path):
    with pytest.raises(ConfigError, match='--config: no such file'):
        load(config=tmp_path / 'missing.toml', env={})
    with pytest.raises(ConfigError, match='MISPQL_CONFIG: no such file'):
        load(env={'MISPQL_CONFIG': str(tmp_path / 'missing.toml')})


def test_environment_and_key_file(tmp_path):
    settings = load(env={'MISPQL_URL': 'https://env', 'MISPQL_KEY': 'k-env'})
    assert (settings.url, settings.key, settings.source) == ('https://env', 'k-env', None)
    path = write(tmp_path / 'c.toml', '[default]\nurl = "https://file"\nkey = "k-file"\n')
    settings = load(config=path, env={'MISPQL_KEY': 'k-env'})
    assert (settings.url, settings.key) == ('https://file', 'k-env')
    settings = load(config=path, env={'MISPQL_URL': 'https://env'})
    assert (settings.url, settings.key) == ('https://env', 'k-file')
    key_file = write(tmp_path / 'key', '  k-from-file\n')
    assert load(config=path, key_file=key_file, env={'MISPQL_KEY': 'k-env'}).key == 'k-from-file'


def test_missing_values(tmp_path):
    with pytest.raises(ConfigError, match='no MISP URL'):
        load(env={'MISPQL_KEY': 'k'})
    with pytest.raises(ConfigError, match='no config file found for profile prod'):
        load(profile='prod', env={'MISPQL_URL': 'https://env'})
    with pytest.raises(ConfigError, match='is empty'):
        load(key_file=write(tmp_path / 'key', '\n'), env={'MISPQL_URL': 'https://env'})
    with pytest.raises(ConfigError, match='--key-file'):
        load(key_file=tmp_path / 'missing', env={'MISPQL_URL': 'https://env'})


def test_readable_key_warns(tmp_path):
    with_key = '[default]\nurl = "https://misp"\nkey = "k"\n'
    assert load(config=write(tmp_path / 'a.toml', with_key, 0o600), env={}).warnings == []
    path = write(tmp_path / 'b.toml', with_key, 0o644)
    assert load(config=path, env={}).warnings == [
        f'{path} holds an API key and is readable by others (chmod 600 it)'
    ]
    assert load(config=write(tmp_path / 'c.toml', '[default]\nurl = "x"\n', 0o644), env={}).warnings == []


def test_ca_bundle_path(tmp_path):
    path = write(tmp_path / 'c.toml', '[default]\nurl = "x"\nverify_ssl = "~/ca.pem"\n')
    assert load(config=path, env={}).verify_ssl == str(tmp_path / 'home' / 'ca.pem')


def test_profile_names(tmp_path):
    path = write(tmp_path / 'c.toml', '[b]\nurl = "https://b"\n[a]\nurl = "https://a"\n')
    assert config.profiles(path) == ['b', 'a']
    assert load(config=path, profile='a', env={}).name == 'a'
    assert load(env={'MISPQL_URL': 'https://env'}).name == 'default'


def test_a_profile_as_written_takes_no_overrides(tmp_path, monkeypatch):
    path = write(tmp_path / 'c.toml', '[a]\nurl = "https://a"\nkey = "ka"\n[b]\nurl = "https://b"\n[c]\nkey = "kc"\n')
    monkeypatch.setenv('MISPQL_URL', 'https://env')
    monkeypatch.setenv('MISPQL_KEY', 'k-env')
    settings = config.load_profile(path, 'b')
    assert (settings.name, settings.url, settings.key) == ('b', 'https://b', None)
    with pytest.raises(ConfigError, match=r'no profile \[d\]'):
        config.load_profile(path, 'd')
    with pytest.raises(ConfigError, match=r'\[c\]: no url'):
        config.load_profile(path, 'c')
