"""Send compiled statements to MISP and bring back its answer."""
import requests

from mispql import __version__
from mispql.compiler import DescribePlan, SearchPlan
from mispql.replies import (SUPPORT_CHECK, ClientError, Exchange, QueryErrors,  # noqa: F401
                            check_page_limit, check_support, failure, sends_query)

CONNECT_TIMEOUT = 10
WHOAMI = '/users/view/me'


class Client:
    def __init__(self, settings, session=None, trace=None, warn=None):
        if not settings.key:
            raise ClientError('no API key: set key in a config file, MISPQL_KEY or --key-file')
        self.settings = settings
        self.trace = trace  # called with method, URL and body before each request
        self.warn = warn or (lambda message: None)  # called with each warning about a result
        self.session = session or requests.Session()
        self.session.headers.update({
            'Authorization': settings.key,
            'Accept': 'application/json',
            'User-Agent': f'mispql/{__version__}',
        })
        self.session.verify = settings.verify_ssl
        self.supports_queries = False  # MISP answered SUPPORT_CHECK with its tables

    def run(self, plan):
        """A count, the rows of a search, for MISP's own formats the unread response; for
        DESCRIBE the table's fields and the limits, for SHOW TABLES the tables answering.
        A statement with a WHERE is sent only to a MISP that runs complex queries."""
        if sends_query(plan):
            self.check_support()
        if isinstance(plan, SearchPlan) and plan.output == 'verbatim':
            response = self.send('POST', plan.endpoint, plan.body, stream=True)
            try:
                warning = check_page_limit(plan, response.headers)
            except ClientError:
                response.close()
                raise
            if warning:
                self.warn(warning)
            return response
        exchange = Exchange(plan, check_support=False)
        while not exchange.done:
            request = exchange.request
            response = self.request(request.method, request.path, request.body)
            exchange.receive(response.status_code, response.headers, response.content)
        for warning in exchange.warnings:
            self.warn(warning)
        return exchange.result

    def check_support(self):
        """NoComplexQueries unless this MISP runs complex queries; asked once per client.

        A MISP without them drops a search's WHERE without a word and answers as if there
        were none."""
        if not self.supports_queries:
            response = self.request(SUPPORT_CHECK.method, SUPPORT_CHECK.path)
            check_support(response.status_code, response.content)
            self.supports_queries = True

    def describe(self, table, endpoint):
        return self.run(DescribePlan(table=table, endpoint=endpoint))

    def whoami(self):
        """The email, role and organisation of the key's user, as MISP reports them."""
        try:
            found = self.send('GET', WHOAMI).json()
            return {'email': found['User']['email'], 'role': found['Role']['name'],
                    'org': found['Organisation']['name']}
        except (ValueError, KeyError, TypeError):
            raise ClientError(f'{WHOAMI}: unexpected answer') from None

    def send(self, method, endpoint, body=None, stream=False):
        """MISP's response to a request it accepted; ClientError or QueryErrors otherwise."""
        response = self.request(method, endpoint, body, stream)
        if response.status_code != 200:
            raise failure(response.status_code, response.content)
        return response

    def request(self, method, endpoint, body=None, stream=False):
        """MISP's response, whatever its status; ClientError when there is none."""
        url = self.settings.url.rstrip('/') + endpoint
        if self.trace:
            self.trace(method, url, body)
        try:
            return self.session.request(method, url, json=body, stream=stream,
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


def chunks(response, size=65536):
    """The body of an unread response, piece by piece."""
    try:
        yield from response.iter_content(size)
    except requests.exceptions.RequestException as e:
        raise ClientError(f'{response.url}: the answer broke off: {e}') from None
