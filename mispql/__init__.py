"""MispQL, an SQL-like language for the MISP REST API.

As a library, mispql compiles statements and reads MISP's replies, and sends nothing
itself: the caller sends each request with its own session, keys and timeouts.

    import mispql

    [statement] = mispql.compile("SELECT COUNT(*) FROM events WHERE tags = 'tlp:red'")
    exchange = mispql.Exchange(statement.plan)
    while not exchange.done:
        request = exchange.request
        response = session.request(request.method, url + request.path, json=request.body)
        exchange.receive(response.status_code, response.headers, response.content)
    exchange.result

MispQLError places a mistake on the text by character offsets; QueryErrors.located()
does the same with MISP's own errors. A plan with a WHERE is sent only once MISP has shown
that it runs complex queries: one that does not would ignore the WHERE.
"""
__version__ = '1.0.0'

from mispql.compiler import (CountPlan, DescribePlan, Plan, SearchPlan, ShowDatabasesPlan,  # noqa: E402
                             ShowTablesPlan, Statement, UsePlan, WhoamiPlan, compile)
from mispql.errors import MispQLError  # noqa: E402
from mispql.replies import (SUPPORT_CHECK, ClientError, Exchange, NoComplexQueries, QueryErrors,  # noqa: E402
                            Request, check_support, flatten, sends_query)
from mispql.tables import TABLES, Table  # noqa: E402

__all__ = [
    'SUPPORT_CHECK', 'TABLES', 'ClientError', 'CountPlan', 'DescribePlan', 'Exchange', 'MispQLError',
    'NoComplexQueries', 'Plan', 'QueryErrors', 'Request', 'SearchPlan', 'ShowDatabasesPlan', 'ShowTablesPlan',
    'Statement', 'Table', 'UsePlan', 'WhoamiPlan', 'check_support', 'compile', 'flatten', 'sends_query',
]
