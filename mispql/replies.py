"""What MISP is asked for a compiled plan, and what its replies mean.

Nothing here sends anything: the caller sends each request its own way (its own session,
keys, timeouts and concurrency) and hands MISP's reply back to an Exchange.
"""
import json
from dataclasses import dataclass

from mispql.compiler import CountPlan, DescribePlan, SearchPlan, ShowTablesPlan
from mispql.errors import MispQLError
from mispql.tables import TABLES


@dataclass(frozen=True)
class Request:
    """A request to MISP: `path` goes after the MISP URL, `body` is sent as JSON."""
    method: str  # GET or POST
    path: str
    body: dict | None = None


# A MISP that runs complex queries answers this with the attributes table's fields. One
# that does not drops a search's `query` without a word and answers as if there were no
# WHERE: every row, every event counted.
SUPPORT_CHECK = Request('GET', TABLES['attributes'].discovery)


class ClientError(Exception):
    """MISP could not be reached, refused the request or answered in an unexpected shape."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # the HTTP status MISP answered with; None if it did not answer


class NoComplexQueries(ClientError):
    """MISP did not show that it runs complex queries, so nothing with a WHERE is sent to it.

    `status` is SUPPORT_CHECK's: 404, MISP does not support them; 403, the key may not use
    them, or a MISP without them refusing an action it does not know; anything else, MISP
    could not tell."""


class QueryErrors(Exception):
    """A 400 listing what is wrong with the query, as paths into the request body."""

    def __init__(self, errors):
        super().__init__('; '.join(f'{e.get("path")}: {e.get("message")}' for e in errors))
        self.errors = errors

    def located(self, plan, statement):
        """The errors as MispQLErrors on the statement text; the whole statement where a path is unknown."""
        located = []
        for error in self.errors:
            start, end = plan.locate(str(error.get('path', ''))) or (statement.start, statement.end)
            located.append(MispQLError(str(error.get('message', 'invalid query')), start, end))
        return located


def sends_query(plan):
    """Whether `plan` sends MISP a WHERE, which only a MISP running complex queries applies."""
    return isinstance(plan, (SearchPlan, CountPlan)) and 'query' in plan.body


def check_support(status, body):
    """Raises NoComplexQueries unless MISP's reply to SUPPORT_CHECK lists its tables."""
    where = f'{SUPPORT_CHECK.path}: '
    if status == 200:
        try:
            tables = json.loads(body)['tables']
        except (ValueError, KeyError, TypeError):
            tables = None
        if isinstance(tables, dict) and tables:
            return
        raise NoComplexQueries(
            f'cannot tell whether this MISP supports complex queries ({where}unexpected answer)', status)
    refusal = f'{where}{failure(status, body)}'
    if status == 404:
        raise NoComplexQueries(
            'this MISP does not support complex queries: it would ignore the WHERE and answer as if '
            f'there were none ({refusal})', status)
    if status == 403:
        raise NoComplexQueries(
            f'this key may not use complex queries, or this MISP does not support them ({refusal})', status)
    raise NoComplexQueries(f'cannot tell whether this MISP supports complex queries ({refusal})', status)


def failure(status, body):
    """The error for a request MISP refused: QueryErrors for a 400 listing what is wrong with
    the query, ClientError for anything else."""
    try:
        reply = json.loads(body)
    except (ValueError, TypeError):
        reply = None
    if not isinstance(reply, dict):
        return ClientError(f'HTTP {status}', status)
    errors = reply.get('errors')
    if status == 400 and isinstance(errors, list) and errors and all(isinstance(e, dict) for e in errors):
        return QueryErrors(errors)
    message = reply.get('message') or reply.get('name')
    return ClientError(f'HTTP {status}: {message}' if message else f'HTTP {status}', status)


class Exchange:
    """The requests a plan needs, one at a time, and its result read from MISP's replies:

        exchange = Exchange(plan)
        while not exchange.done:
            request = exchange.request
            ...  # send it, then:
            exchange.receive(status, headers, body)
        exchange.result

    The result is a count; a list of rows; for MISP's own formats (FORMAT csv, stix2, ...)
    the body as received; for DESCRIBE the table's fields and the limits; for SHOW TABLES
    the tables answering. `receive` raises QueryErrors for a 400 listing what is wrong with
    the query, and ClientError when MISP refused a request or answered in an unexpected
    shape. `warnings` says what the result may lack: MISP capped a search's page size for
    the key's role, or left out rows it had found.

    A plan with a WHERE first asks SUPPORT_CHECK, and goes on only when MISP lists its
    tables (NoComplexQueries otherwise): a MISP without complex queries would ignore the
    WHERE and answer as if there were none. `check_support=False` leaves the check out, for
    a caller that has just made it itself.
    """

    def __init__(self, plan, check_support=True):
        if not isinstance(plan, (SearchPlan, CountPlan, DescribePlan, ShowTablesPlan)):
            raise TypeError(f'{type(plan).__name__}: answered from the connections, not by MISP')
        self.plan = plan
        self.request = None  # the request to send next; None once the result is known
        self.done = False
        self.result = None
        self.warnings = []  # about the result: a page capped for the key's role, rows left out
        self.supported = False  # MISP answered SUPPORT_CHECK with its tables
        self._steps = self._exchange(plan, check_support)
        self.request = next(self._steps)

    def receive(self, status, headers, body):
        """MISP's reply to `request`: its HTTP status, its headers and its body, bytes or text."""
        if self.request is None:
            raise RuntimeError('no request is waiting for a reply')
        self.request = None
        try:
            self.request = self._steps.send(_Reply(status, headers or {}, body))
        except StopIteration as finished:
            self.done, self.result = True, finished.value

    def _exchange(self, plan, check):
        if check and sends_query(plan):
            reply = yield SUPPORT_CHECK
            check_support(reply.status, reply.body)
            self.supported = True
        if isinstance(plan, DescribePlan):
            return _describe(plan, (yield Request('GET', plan.endpoint)))
        if isinstance(plan, ShowTablesPlan):
            return (yield from _show_tables(plan))
        reply = yield Request('POST', plan.endpoint, plan.body)
        if isinstance(plan, CountPlan):
            return _count(plan, reply)
        _accepted(reply)
        if warning := check_page_limit(plan, reply.headers):
            self.warnings.append(warning)
        pages = [reply.headers]
        if plan.output == 'verbatim':
            result = reply.body
        else:
            result = _rows(plan, reply)
            if plan.skip:
                if len(result) == plan.body['limit']:
                    body = {**plan.body, 'page': plan.body['page'] + 1}
                    reply = yield Request('POST', plan.endpoint, body)
                    _accepted(reply)
                    check_page_limit(plan, reply.headers)
                    pages.append(reply.headers)
                    result += _rows(plan, reply)
                result = result[plan.skip:plan.skip + plan.body['limit']]
        if warning := dropped_warning(plan, *pages):
            self.warnings.append(warning)
        return result


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: dict
    body: bytes | str | None


def _accepted(reply):
    if reply.status != 200:
        raise failure(reply.status, reply.body)


def _rows(plan, reply):
    key = TABLES[plan.table].row_key
    try:
        found = json.loads(reply.body)['response']
        # {"response": {"Attribute": [...]}}, {"response": [{"Event": {...}}, ...]}
        if isinstance(found, dict):
            rows = found[key]
        else:
            # Keys beside the row's own ({"Collection": {...}, "Orgc": {...}}) join the row.
            rows = [{**item[key], **{k: v for k, v in item.items() if k != key and k not in item[key]}}
                    for item in found]
    except (ValueError, KeyError, TypeError):
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ClientError(f'{plan.endpoint}: unexpected answer', reply.status)
    return rows


def _count(plan, reply):
    _accepted(reply)
    found = _header(reply.headers, 'X-Result-Count') if plan.source == 'header' else reply.body
    try:
        return int(_text(found).strip())
    except (AttributeError, ValueError, TypeError):
        raise ClientError(f'{plan.endpoint}: no count in the answer', reply.status) from None


def _describe(plan, reply):
    _accepted(reply)
    try:
        found = json.loads(reply.body)
        entry = found['tables'][plan.table]
        return {'fields': dict(entry['fields']), 'limits': dict(found['limits'])}
    except (ValueError, KeyError, TypeError):
        raise ClientError(f'{plan.endpoint}: unexpected answer', reply.status) from None


def _show_tables(plan):
    """The tables whose discovery endpoint answers; MISP's first refusal if none does."""
    found, first_error = [], None
    for table, endpoint in plan.endpoints.items():
        reply = yield Request('GET', endpoint)
        if reply.status == 200:
            found.append(table)
        else:
            first_error = first_error or failure(reply.status, reply.body)
    if not found and first_error:
        raise first_error
    return found


def _text(body):
    return body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body


def _header(headers, name):
    name = name.lower()
    return next((value for key, value in headers.items() if key.lower() == name), None)


def _int_header(headers, name):
    try:
        return int(_header(headers, name))
    except (TypeError, ValueError):
        return None


def dropped_warning(plan, *headers):
    """A warning when MISP read rows for the pages with these headers and then left them out,
    None when it did not."""
    if not TABLES[plan.table].filtered_after_read:
        return None
    count = sum(max(_int_header(page, 'X-Skipped-Elements-Count') or 0, 0) for page in headers)
    if not count:
        return None
    rows = f'{count} matching row' if count == 1 else f'{count} matching rows'
    pages = '; a page can hold fewer rows than LIMIT' if plan.body.get('limit') else ''
    return f'MISP left out {rows} (warninglists, decaying or proposal blocking){pages}'


def check_page_limit(plan, headers):
    """A warning when MISP capped the page size of a search without OFFSET, None when it did not;
    ClientError when the cap moves the page an OFFSET asks for."""
    # Older MISP versions do not report this header.
    applied = _header(headers, 'X-Result-Page-Limit')
    if applied is None:
        return None
    try:
        applied = int(applied)
    except (TypeError, ValueError):
        raise ClientError(f'{plan.endpoint}: invalid X-Result-Page-Limit') from None
    if applied <= 0:
        raise ClientError(f'{plan.endpoint}: invalid X-Result-Page-Limit')
    requested = plan.body.get('limit')
    if applied == requested:
        return None
    if plan.skip or plan.body.get('page', 1) > 1:
        raise ClientError(f"{plan.endpoint}: this key's role allows at most {applied} rows per request "
                          f'(LIMIT {requested} asked); use LIMIT {applied} or smaller with OFFSET')
    asked = f'LIMIT {requested}' if requested else 'the search'
    return f"MISP capped {asked} at {applied} rows for this key's role"


def flatten(row, prefix=''):
    """Nested objects as dotted keys, Tag as a list of names; other lists are left out."""
    flat = {}
    for key, value in row.items():
        name = prefix + key
        if isinstance(value, dict):
            flat.update(flatten(value, f'{name}.'))
        elif isinstance(value, list):
            if key == 'Tag':
                flat[name] = [tag.get('name') for tag in value if isinstance(tag, dict)]
            elif value and not any(isinstance(item, (dict, list)) for item in value):
                flat[name] = value
            # Attribute, Object, Galaxy, ...: an event can hold a million of them.
        else:
            flat[name] = value
    return flat
