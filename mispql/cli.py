"""The mispql command: run MispQL statements from -e, a file or stdin."""
import argparse
import json
import os
import sys
from pathlib import Path

from mispql import __version__, config
from mispql.client import WHOAMI, ClientError, QueryErrors
from mispql.compiler import (DescribePlan, SearchPlan, ShowDatabasesPlan, ShowTablesPlan, UsePlan, WhoamiPlan,
                             compile_statement)
from mispql.connections import Connections, describe
from mispql.errors import MispQLError, position
from mispql.output import write
from mispql.parser import parse

OK, FAILED, USAGE = 0, 1, 2
INTERRUPTED, BROKEN_PIPE = 130, 141


class UsageError(Exception):
    pass


def main(argv=None):
    args = _arguments().parse_args(argv)
    try:
        return _main(args)
    except KeyboardInterrupt:
        return INTERRUPTED
    except BrokenPipeError:
        # Python flushes stdout once more on the way out; send that nowhere.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return BROKEN_PIPE


def _arguments():
    parser = argparse.ArgumentParser(
        prog='mispql',
        description='Query MISP in MispQL, an SQL-like language, over its REST API.',
    )
    parser.add_argument('file', nargs='?', metavar='FILE',
                        help='a file of statements, - for stdin; without one, the interactive prompt '
                             'at a terminal, else stdin')
    parser.add_argument('-e', '--execute', metavar='TEXT', help='run these statements')
    parser.add_argument('--config', metavar='PATH', help='the config file to use')
    parser.add_argument('--profile', metavar='NAME', help='the profile (table) in the config file')
    parser.add_argument('--key-file', metavar='PATH', help='read the API key from this file')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='print each request to stderr as it is sent')
    parser.add_argument('--dry-run', action='store_true',
                        help='print the requests the statements compile to; send nothing')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return parser


def _main(args):
    if args.execute is None and args.file is None and sys.stdin.isatty() and sys.stdout.isatty():
        from mispql import repl
        return repl.main(args)
    try:
        text = _read_input(args)
    except UsageError as e:
        stderr(f'ERROR: {e}')
        return USAGE
    return run(text, connections(args), args.dry_run)


def run(text, connections, dry_run=False, interactive=False):
    """Check every statement in `text`, then run them in order; returns an exit code."""
    try:
        statements = parse(text)
        plans = [compile_statement(statement) for statement in statements]
        _check_databases(statements, plans, connections)
        if dry_run:
            for plan in plans:
                _dry_run(plan, connections)
            return OK
    except MispQLError as e:
        stderr(e.render(text))
        return FAILED
    except config.ConfigError as e:
        stderr(f'ERROR: {e}')
        return USAGE
    if not plans:
        return OK
    try:
        connections.client
    except (config.ConfigError, ClientError) as e:
        stderr(f'ERROR: {e}')
        return USAGE
    for statement, plan in zip(statements, plans):
        try:
            if isinstance(plan, UsePlan):
                identity = connections.use(plan.database)
                if interactive:
                    print(describe(plan.database, identity), flush=True)
                continue
            warnings = write(plan, connections.run(plan), sys.stdout.buffer)
        except QueryErrors as e:
            stderr('\n'.join(error.render(text) for error in e.located(plan, statement)))
            return FAILED
        except (ClientError, config.ConfigError) as e:
            line, column = position(text, statement.start)
            stderr(f'ERROR {line}:{column}: {e}')
            return FAILED if isinstance(e, ClientError) else USAGE
        sys.stdout.flush()
        for warning in warnings:
            stderr(f'WARNING: {warning}')
    return OK


def _read_input(args):
    if args.execute is not None:
        if args.file is not None:
            raise UsageError('give -e or a file, not both')
        try:
            args.execute.encode()
        except UnicodeEncodeError:
            raise UsageError('-e: not UTF-8') from None
        return args.execute
    if args.file is not None and args.file != '-':
        try:
            return _decode(Path(args.file).read_bytes(), args.file)
        except OSError as e:
            raise UsageError(f'{args.file}: {e.strerror}') from None
    return _decode(sys.stdin.buffer.read(), 'stdin')


def _decode(data, name):
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        raise UsageError(f'{name}: not UTF-8 (byte {e.start})') from None


def connections(args):
    trace = (lambda method, url, body: stderr(_request(method, url, body))) if args.verbose else None
    return Connections(args.config, args.profile, args.key_file, trace=trace,
                       warn=lambda warning: stderr(f'WARNING: {warning}'))


def _check_databases(statements, plans, connections):
    """A USE naming no profile stops the script before anything is sent."""
    uses = [(statement, plan) for statement, plan in zip(statements, plans) if isinstance(plan, UsePlan)]
    names = connections.names() if uses else []
    for statement, plan in uses:
        if plan.database not in names:
            have = f'the config has {", ".join(names)}' if names else 'there is no config file'
            raise MispQLError(f'unknown database {plan.database}: {have}',
                              statement.database.start, statement.database.end)


def _dry_run(plan, connections):
    if isinstance(plan, ShowTablesPlan):
        for endpoint in plan.endpoints.values():
            print(_request('GET', endpoint))
    elif isinstance(plan, ShowDatabasesPlan):
        for name in connections.names() or ['default']:
            print(_request('GET', WHOAMI, note=f' ({name})'))
    elif isinstance(plan, UsePlan):
        print(_request('GET', WHOAMI, note=f' ({plan.database})'))
    elif isinstance(plan, WhoamiPlan):
        print(_request('GET', WHOAMI))
    elif isinstance(plan, DescribePlan):
        print(_request('GET', plan.endpoint))
    else:
        print(_request('POST', plan.endpoint, plan.body))
    if isinstance(plan, SearchPlan) and plan.skip:
        body = {**plan.body, 'page': plan.body['page'] + 1}
        print(_request('POST', plan.endpoint, body, ' (sent only when the first page is full)'))
    sys.stdout.flush()


def _request(method, target, body=None, note=''):
    if body is None:
        return f'{method} {target}{note}'
    return f'{method} {target}{note}\n{json.dumps(body, indent=2, ensure_ascii=False)}'


def stderr(message):
    # Keep stdout and stderr in order on a terminal.
    sys.stdout.flush()
    print(message, file=sys.stderr, flush=True)
