"""Send compiled statements to MISP and bring back its answer."""
import requests

from mispql import __version__
from mispql.compiler import CountPlan
from mispql.errors import MispQLError

CONNECT_TIMEOUT = 10
ROW_KEYS = {'attributes': 'Attribute', 'events': 'Event', 'objects': 'Object'}


class ClientError(Exception):
    """MISP could not be reached, refused the request or answered in an unexpected shape."""


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
    def __init__(self, settings, session=None):
        if not settings.key:
            raise ClientError('no API key: set key in a config file, MISPQL_KEY or --key-file')
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({
            'Authorization': settings.key,
            'Accept': 'application/json',
            'User-Agent': f'mispql/{__version__}',
        })
        self.session.verify = settings.verify_ssl

    def run(self, plan):
        """A count, the rows of a search, or for MISP's own formats the unread response."""
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
        key = ROW_KEYS[plan.table]
        try:
            found = response.json()['response']
            # {"response": {"Attribute": [...]}}, {"response": [{"Event": {...}}, ...]}
            if isinstance(found, dict):
                return found[key]
            return [item[key] for item in found]
        except (ValueError, KeyError, TypeError):
            raise ClientError(f'{plan.endpoint}: unexpected answer') from None

    def post(self, endpoint, body, stream=False):
        url = self.settings.url.rstrip('/') + endpoint
        try:
            response = self.session.post(url, json=body, stream=stream,
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


def _failure(response):
    try:
        reply = response.json()
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        return ClientError(f'HTTP {response.status_code}')
    if response.status_code == 400 and isinstance(reply.get('errors'), list) and reply['errors']:
        return QueryErrors(reply['errors'])
    message = reply.get('message') or reply.get('name')
    return ClientError(f'HTTP {response.status_code}: {message}' if message else f'HTTP {response.status_code}')
