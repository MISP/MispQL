"""The connections a session switches between: one per profile of the config file."""
import dataclasses
import getpass
import sys
from concurrent.futures import ThreadPoolExecutor

from mispql import config
from mispql.client import Client, ClientError
from mispql.compiler import ShowDatabasesPlan, WhoamiPlan

IDENTITY_TIMEOUT = 10  # seconds SHOW DATABASES waits for an answer from each instance


class Connections:
    """The connection statements run on, opened on first use, and the others to switch to.

    The starting connection carries the overrides (MISPQL_URL, MISPQL_KEY, --key-file); every
    other profile is used as written, so no key reaches another profile's URL."""

    def __init__(self, config_path=None, profile=None, key_file=None, trace=None, warn=None):
        self.config_path, self.profile, self.key_file = config_path, profile, key_file
        self.trace = trace  # given to every client: --verbose
        self.warn = warn or (lambda message: None)
        self.opened = {}  # name -> Settings of each connection used so far, typed keys included
        self.warned = set()
        self.current = None
        self._client = None

    @property
    def client(self):
        """The current connection's client; the starting connection's on first use."""
        if self._client is None:
            start = config.load(self.config_path, self.profile, self.key_file)
            self._client = self._open(start, starting=True)
            self.opened[start.name], self.current = start, start.name
        return self._client

    def names(self):
        """The profile names of the config file; none without one."""
        path = config.find_config(self.config_path)
        return config.profiles(path) if path else []

    def use(self, name):
        """Switch to profile `name` once MISP accepts its key; returns who that is."""
        settings = self._settings(name)
        client = self._open(settings)
        identity = client.whoami()
        # Kept only now, so a mistyped key is asked for again on the next USE.
        self.opened[name], self._client, self.current = settings, client, name
        return identity

    def run(self, plan):
        if isinstance(plan, ShowDatabasesPlan):
            return self.databases()
        if isinstance(plan, WhoamiPlan):
            return self.whoami()
        return self.client.run(plan)

    def whoami(self):
        identity = self.client.whoami()
        return {'current': True, 'database': self.current, 'url': self.opened[self.current].url, **identity}

    def databases(self):
        self.client
        with ThreadPoolExecutor(8) as pool:
            return list(pool.map(self.identify, self.names() or [self.current]))

    def identify(self, name):
        """A row for profile `name`: its URL and identity, or, under `error`, why there is none."""
        row = {'current': name == self.current, 'database': name, 'url': ''}
        try:
            settings = self._settings(name)
            row['url'] = settings.url
            if not settings.key:
                raise config.ConfigError('no key in the config')
            quick = dataclasses.replace(settings, timeout=min(settings.timeout, IDENTITY_TIMEOUT))
            row.update(Client(quick, trace=self.trace).whoami())
        except ClientError as e:
            row['error'] = 'key rejected' if e.status in (401, 403) else str(e)
        except config.ConfigError as e:
            row['error'] = str(e)
        return row

    def _settings(self, name):
        self.client  # the starting connection is known by name before any other
        return self.opened.get(name) or config.load_profile(config.find_config(self.config_path), name)

    def _open(self, settings, starting=False):
        for warning in settings.warnings:
            if warning not in self.warned:
                self.warned.add(warning)
                self.warn(warning)
        if not settings.key and sys.stdin.isatty():
            question = 'MISP API key: ' if starting else f'MISP API key for {settings.name}: '
            try:
                settings.key = getpass.getpass(question).strip() or None
            except EOFError:
                pass
        if not settings.key and not starting:
            raise config.ConfigError(f'{settings.name}: no API key in the config')
        return Client(settings, trace=self.trace)


def describe(name, identity):
    return f'{name}: {identity["email"]}, {identity["role"]}, {identity["org"]}'
