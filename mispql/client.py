"""Send compiled statements to MISP and bring back its answer."""
import requests

from mispql import __version__
from mispql.compiler import CountPlan, DescribePlan, ShowTablesPlan
from mispql.errors import MispQLError
from mispql.tables import TABLES

CONNECT_TIMEOUT = 10


class ClientError(Exception):
    """MISP could not be reached, refused the request or answered in an unexpected shape."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # the HTTP status MISP answered with; None if it did not answer


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


class Client:
    def __init__(self, settings, session=None, trace=None):
        if not settings.key:
            raise ClientError('no API key: set key in a config file, MISPQL_KEY or --key-file')
        self.settings = settings
        self.trace = trace  # called with method, URL and body before each request
        self.session = session or requests.Session()
        self.session.headers.update({
            'Authorization': settings.key,
            'Accept': 'application/json',
            'User-Agent': f'mispql/{__version__}',
        })
        self.session.verify = settings.verify_ssl

    def run(self, plan):
        """A count, the rows of a search, for MISP's own formats the unread response; for
        DESCRIBE the table's fields and the limits, for SHOW TABLES the tables answering."""
        if isinstance(plan, DescribePlan):
            return self.describe(plan.table, plan.endpoint)
        if isinstance(plan, ShowTablesPlan):
            return self.tables(plan.endpoints)
        if isinstance(plan, CountPlan):
            response = self.post(plan.endpoint, plan.body)
            text = response.headers.get('X-Result-Count') if plan.source == 'header' else response.text
            try:
                return int(text.strip())
            except (AttributeError, ValueError):
                raise ClientError(f'{plan.endpoint}: no count in the answer') from None
        if plan.output == 'verbatim':
            return self.post(plan.endpoint, plan.body, stream=True)
        rows = self.rows(plan, plan.body)
        if plan.skip:
            limit = plan.body['limit']
            if len(rows) == limit:
                rows += self.rows(plan, {**plan.body, 'page': plan.body['page'] + 1})
            rows = rows[plan.skip:plan.skip + limit]
        return rows

    def rows(self, plan, body):
        response = self.post(plan.endpoint, body)
        key = TABLES[plan.table].row_key
        try:
            found = response.json()['response']
            # {"response": {"Attribute": [...]}}, {"response": [{"Event": {...}}, ...]}
            if isinstance(found, dict):
                return found[key]
            return [item[key] for item in found]
        except (ValueError, KeyError, TypeError):
            raise ClientError(f'{plan.endpoint}: unexpected answer') from None

    def describe(self, table, endpoint):
        try:
            found = self.send('GET', endpoint).json()
            entry = found['tables'][table]
            return {'fields': dict(entry['fields']), 'limits': dict(found['limits'])}
        except (ValueError, KeyError, TypeError):
            raise ClientError(f'{endpoint}: unexpected answer') from None

    def tables(self, endpoints):
        """The tables whose discovery endpoint answers; MISP's first error if none does."""
        found, first_error = [], None
        for table, endpoint in endpoints.items():
            try:
                self.send('GET', endpoint)
            except ClientError as e:
                if e.status is None:
                    raise
                first_error = first_error or e
                continue
            found.append(table)
        if not found and first_error:
            raise first_error
        return found

    def post(self, endpoint, body, stream=False):
        return self.send('POST', endpoint, body, stream)

    def send(self, method, endpoint, body=None, stream=False):
        url = self.settings.url.rstrip('/') + endpoint
        if self.trace:
            self.trace(method, url, body)
        try:
            response = self.session.request(method, url, json=body, stream=stream,
                                            timeout=(CONNECT_TIMEOUT, self.settings.timeout))
        except requests.exceptions.SSLError as e:
            raise ClientError(f'TLS failed for {url}: {e}') from None
        except requests.exceptions.ConnectTimeout:
            raise ClientError(f'cannot reach {url}: no connection within {CONNECT_TIMEOUT} s') from None
        except requests.exceptions.Timeout:
            raise ClientError(f'no answer from {url} within {self.settings.timeout} s') from None
        except requests.exceptions.ConnectionError:
            raise ClientError(f'cannot reach {url}') from None
        except requests.exceptions.RequestException as e:
            raise ClientError(f'{url}: {e}') from None
        if response.status_code != 200:
            raise _failure(response)
        return response


def chunks(response, size=65536):
    """The body of an unread response, piece by piece."""
    try:
        yield from response.iter_content(size)
    except requests.exceptions.RequestException as e:
        raise ClientError(f'{response.url}: the answer broke off: {e}') from None


def _failure(response):
    try:
        reply = response.json()
    except ValueError:
        reply = None
    status = response.status_code
    if not isinstance(reply, dict):
        return ClientError(f'HTTP {status}', status)
    if status == 400 and isinstance(reply.get('errors'), list) and reply['errors']:
        return QueryErrors(reply['errors'])
    message = reply.get('message') or reply.get('name')
    return ClientError(f'HTTP {status}: {message}' if message else f'HTTP {status}', status)
