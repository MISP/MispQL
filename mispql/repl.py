"""The interactive prompt: statements typed at a terminal, run one submission at a time."""
import os
import re
import textwrap
import threading
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory, InMemoryHistory

from mispql import __version__, cli, config, snake
from mispql.client import Client, ClientError
from mispql.connections import describe
from mispql.errors import MispQLError
from mispql.lexer import KEYWORDS, tokenize
from mispql.tables import TABLES

COMMANDS = ('quit', 'exit', 'help', 'snake2')
HELP = f"""\
SELECT * | COUNT(*) | field, ... FROM table
  [WHERE condition] [ORDER BY field [ASC|DESC], ...] [LIMIT n [OFFSET n]] [FORMAT word];
DESCRIBE table;  SHOW TABLES;
SHOW DATABASES;  USE database;  WHOAMI;   a database is a profile of the config file
{textwrap.fill('tables: ' + ', '.join(TABLES), 100, subsequent_indent='  ')}
condition: field = != < > <= >= value | field [NOT] IN (value, ...) | field [NOT] LIKE 'a%',
  combined with AND, OR, NOT and parentheses; values are 'text', 123, TRUE or FALSE
FORMAT table and flat are rendered here; any other word is MISP's format, printed as sent.
A search needs a WHERE or a LIMIT. DESCRIBE shows a table's fields and the names ORDER BY takes.
End a statement with ; (Alt-Enter runs it without). Ctrl-C clears, Ctrl-D or quit leaves."""


def main(args):
    connections = cli.connections(args)
    client = None
    if not args.dry_run:
        try:
            client = connections.client
        except (config.ConfigError, ClientError) as e:
            cli.stderr(f'ERROR: {e}')
            return cli.USAGE
    # Completion fetches fields in its own thread: its own session, and no --verbose trace.
    completions = Completions(client and Client(client.settings))
    session = PromptSession(
        message=lambda: prompt(connections),
        multiline=Condition(lambda: not complete(session.default_buffer.text)),
        prompt_continuation=lambda width, line_number, wrap_count: '-> '.rjust(width),
        history=_history(),
        completer=ThreadedCompleter(completions),
        complete_while_typing=False,
    )
    print(f'mispql {__version__}, {_where(connections, client)}. End statements with ;  help for help, '
          'quit to leave.')
    while True:
        try:
            text = session.prompt()
        except KeyboardInterrupt:
            continue
        except EOFError:
            return cli.OK
        command = text.strip().rstrip(';').strip().lower()
        if command in ('quit', 'exit'):
            return cli.OK
        if command == 'help':
            print(HELP)
            continue
        if command == 'snake2':
            snake.play(_state() / 'snake2')
            continue
        shown = connections.current
        try:
            cli.run(text, connections, args.dry_run, interactive=True)
        except KeyboardInterrupt:
            cli.stderr('interrupted')
        if connections.current != shown:
            completions.reset(Client(connections.client.settings))


def prompt(connections):
    return f'mispql [{connections.current}]> ' if connections.current else 'mispql> '


def _where(connections, client):
    """Who the session starts as; the URL alone, and a warning, when MISP does not say."""
    if client is None:
        return 'dry run: nothing is sent'
    name, url = connections.current, client.settings.url
    found = connections.identify(name)
    if 'error' in found:
        cli.stderr(f'WARNING: cannot tell who {name} is: {found["error"]}')
        return f'{name} at {url}'
    return f'{describe(name, found)} at {url}'


def complete(text):
    """Whether Enter runs `text` rather than starting a new line: it ends in ;, is a command,
    or cannot be lexed whatever follows (so its error shows)."""
    stripped = text.strip()
    if not stripped or stripped.rstrip(';').strip().lower() in COMMANDS:
        return True
    try:
        tokens = tokenize(text)
    except MispQLError as e:
        return not e.at_end
    last = tokens[-2] if len(tokens) > 1 else None  # tokens[-1] is EOF
    return last is not None and last.kind == 'SYMBOL' and last.value == ';'


class Completions(Completer):
    """Keywords, table names and the fields of the table the statement names after FROM."""

    def __init__(self, client):
        self.client = client
        self.fields = {}
        self.lock = threading.Lock()

    def reset(self, client):
        """Another connection: its account may see other fields."""
        with self.lock:
            self.client, self.fields = client, {}

    def get_completions(self, document, complete_event):
        if _in_string(document.text_before_cursor):
            return
        word = re.search(r'\w*$', document.text_before_cursor).group()
        for candidate in [*sorted(KEYWORDS), *TABLES, *self.table_fields(_statement_table(document))]:
            if candidate.lower().startswith(word.lower()):
                yield Completion(candidate, start_position=-len(word))

    def table_fields(self, table):
        if self.client is None or table not in TABLES:
            return []
        with self.lock:
            if table not in self.fields:
                try:
                    found = self.client.describe(table, TABLES[table].discovery)
                    # meta.<key> stands for any key: offer meta.
                    self.fields[table] = [name.removesuffix('<key>') for name in found['fields']]
                except ClientError:
                    self.fields[table] = []
            return self.fields[table]


def _in_string(text):
    """Whether `text` ends inside an open '...' or "..." string (a `name` still completes)."""
    try:
        tokenize(text)
    except MispQLError as e:
        return e.at_end and text[e.start] in '\'"'
    return False


def _statement_table(document):
    text, cursor = document.text, document.cursor_position
    start = text.rfind(';', 0, cursor) + 1
    end = text.find(';', cursor)
    match = re.search(r'\bfrom\s+`?(\w+)', text[start:] if end == -1 else text[start:end], re.IGNORECASE)
    return match.group(1).lower() if match else None


def _state():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'mispql'


def _history():
    path = _state() / 'history'
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Statements hold indicator values: the file is the user's alone.
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    except OSError as e:
        cli.stderr(f'WARNING: no history file: {e}')
        return InMemoryHistory()
    return FileHistory(str(path))
