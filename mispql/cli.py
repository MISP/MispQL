"""The mispql command: run MispQL statements from -e, a file or stdin."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from mispql import __version__, config
from mispql.client import Client, ClientError, QueryErrors
from mispql.compiler import DescribePlan, SearchPlan, ShowTablesPlan, compile_statement
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
                        help='a file of statements; - or no FILE reads stdin when it is not a terminal')
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
    if args.execute is None and args.file is None and sys.stdin.isatty():
        from mispql import repl
        return repl.main(args)
    try:
        text = _read_input(args)
    except UsageError as e:
        stderr(f'ERROR: {e}')
        return USAGE
    return run(text, lambda: connect(args), args.dry_run)


def run(text, get_client, dry_run=False):
    """Check every statement in `text`, then run them in order; returns an exit code."""
    try:
        statements = parse(text)
        plans = [compile_statement(statement) for statement in statements]
    except MispQLError as e:
        stderr(e.render(text))
        return FAILED
    if dry_run:
        for plan in plans:
            _dry_run(plan)
        return OK
    if not plans:
        return OK
    try:
        client = get_client()
    except (config.ConfigError, ClientError) as e:
        stderr(f'ERROR: {e}')
        return USAGE
    for statement, plan in zip(statements, plans):
        try:
            warnings = write(plan, client.run(plan), sys.stdout.buffer)
        except QueryErrors as e:
            stderr('\n'.join(error.render(text) for error in e.located(plan, statement)))
            return FAILED
        except ClientError as e:
            line, column = position(text, statement.start)
            stderr(f'ERROR {line}:{column}: {e}')
            return FAILED
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


def connect(args):
    settings = config.load(args.config, args.profile, args.key_file)
    for warning in settings.warnings:
        stderr(f'WARNING: {warning}')
    if not settings.key and sys.stdin.isatty():
        try:
            settings.key = getpass.getpass('MISP API key: ').strip() or None
        except EOFError:
            pass
    trace = (lambda method, url, body: stderr(_request(method, url, body))) if args.verbose else None
    return Client(settings, trace=trace)


def _dry_run(plan):
    if isinstance(plan, ShowTablesPlan):
        for endpoint in plan.endpoints.values():
            print(_request('GET', endpoint))
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
