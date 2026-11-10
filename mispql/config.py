"""Find the MISP server and the API key to use."""
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_CONFIG = Path(__file__).resolve().parent.parent / 'config.toml'
SETTINGS = ('url', 'key', 'verify_ssl', 'timeout')


class ConfigError(Exception):
    pass


@dataclass(kw_only=True)
class Settings:
    url: str
    key: str | None = field(default=None, repr=False)
    verify_ssl: bool | str = True  # or a CA bundle path
    timeout: float = 300  # seconds to wait for an answer
    source: Path | None = None
    warnings: list[str] = field(default_factory=list)


def load(config=None, profile=None, key_file=None, env=os.environ):
    """Settings from the first config file found, then MISPQL_URL / MISPQL_KEY, then key_file."""
    path = find_config(config, env)
    settings = Settings(url='', source=path)
    if path is not None:
        values = _profile(path, profile)
        for name, value in values.items():
            setattr(settings, name, value)
        if 'key' in values and path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
            settings.warnings.append(f'{path} holds an API key and is readable by others (chmod 600 it)')
    elif profile is not None:
        raise ConfigError(f'no config file found for profile {profile}')
    settings.url = env.get('MISPQL_URL') or settings.url
    settings.key = env.get('MISPQL_KEY') or settings.key
    if key_file is not None:
        settings.key = _read_key(Path(key_file))
    if not settings.url:
        raise ConfigError('no MISP URL: set url in a config file, or MISPQL_URL')
    if isinstance(settings.verify_ssl, str):
        settings.verify_ssl = str(Path(settings.verify_ssl).expanduser())
    return settings


def find_config(config=None, env=os.environ):
    if config is not None:
        return _existing(Path(config), '--config')
    if env.get('MISPQL_CONFIG'):
        return _existing(Path(env['MISPQL_CONFIG']), 'MISPQL_CONFIG')
    for path in (REPO_CONFIG, Path.home() / '.config' / 'mispql' / 'config.toml'):
        if path.is_file():
            return path
    return None


def _existing(path, given_by):
    if not path.is_file():
        raise ConfigError(f'{given_by}: no such file: {path}')
    return path


def _profile(path, name):
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f'{path}: {e}') from None
    profiles = {key: value for key, value in data.items() if isinstance(value, dict)}
    if name is None:
        if 'default' in profiles:
            name = 'default'
        elif len(profiles) == 1:
            name = next(iter(profiles))
        elif not profiles:
            raise ConfigError(f'{path}: no profile; add a [default] table')
        else:
            raise ConfigError(f'{path}: no [default] profile; pick one of {", ".join(profiles)} with --profile')
    if name not in profiles:
        raise ConfigError(f'{path}: no profile [{name}]')
    values = profiles[name]
    where = f'{path} [{name}]'
    unknown = sorted(set(values) - set(SETTINGS))
    if unknown:
        raise ConfigError(f'{where}: unknown setting {", ".join(unknown)} (known: {", ".join(SETTINGS)})')
    for setting in ('url', 'key'):
        if setting in values and not (isinstance(values[setting], str) and values[setting]):
            raise ConfigError(f'{where}: {setting} must be a non-empty string')
    if 'verify_ssl' in values and not isinstance(values['verify_ssl'], (bool, str)):
        raise ConfigError(f'{where}: verify_ssl must be true, false or a CA bundle path')
    timeout = values.get('timeout', 1)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError(f'{where}: timeout must be a positive number of seconds')
    return values


def _read_key(path):
    try:
        key = path.read_text().strip()
    except OSError as e:
        raise ConfigError(f'--key-file: {e}') from None
    if not key:
        raise ConfigError(f'--key-file: {path} is empty')
    return key
