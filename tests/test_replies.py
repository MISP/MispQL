import json
import subprocess
import sys

import pytest

import mispql
from mispql import (SUPPORT_CHECK, ClientError, Exchange, MispQLError, NoComplexQueries, QueryErrors, Request,
                    check_support, sends_query)

SUPPORTED = {'tables': {'attributes': {'endpoint': '/attributes/restSearch',
                                       'fields': {'id': {'type': 'int', 'operators': ['='], 'level': ['attribute']}},
                                       'sortable': ['id']}},
             'limits': {'max_depth': 5}}
# As MISP answers SUPPORT_CHECK: without complex queries, and to a key that may not use them.
NOT_FOUND = b'{"name": "Not Found", "message": "Not Found", "url": "/attributes/queryDescribe"}'
NO_PERMISSION = (b'{"name": "You do not have permission to use this functionality.", '
                 b'"message": "You do not have permission to use this functionality.", '
                 b'"url": "/attributes/queryDescribe"}')
JSON = {'Content-Type': 'application/json'}


def plan_of(text, **options):
    [statement] = mispql.compile(text, **options)
    return statement.plan


def converse(exchange, *replies):
    """Hands `replies` (status, headers, body) to the exchange; returns the requests it asked for."""
    asked = []
    for status, headers, body in replies:
        asked.append(exchange.request)
        exchange.receive(status, headers, body)
    return asked


def ok(body, headers=None):
    return 200, headers or JSON, body


def as_json(data):
    return json.dumps(data).encode()


def test_compile_keeps_each_statement_where_it_is():
    text = 'SELECT * FROM events LIMIT 1;\n  DESCRIBE tags'
    first, second = mispql.compile(text)
    assert (text[first.start:first.end], text[second.start:second.end]) == ('SELECT * FROM events LIMIT 1',
                                                                            'DESCRIBE tags')
    assert isinstance(first.plan, mispql.SearchPlan) and isinstance(second.plan, mispql.DescribePlan)
    assert first.node.limit.value == 1
    assert mispql.compile('-- nothing') == []
    with pytest.raises(MispQLError) as exc:
        mispql.compile('SELECT * FROM events LIMIT 1; SELECT * FROM nowhere LIMIT 1')
    assert exc.value.start == 44


def test_flatten_fetches_events_without_their_records():
    """flatten: the caller flattens the rows, which drops an event's attributes, objects and reports."""
    assert 'metadata' not in plan_of('SELECT * FROM events LIMIT 5').body
    assert plan_of('SELECT * FROM events LIMIT 5', flatten=True).body['metadata'] == 1
    assert plan_of('SELECT * FROM events LIMIT 5 FORMAT table').body['metadata'] == 1
    # MISP's own formats are passed on as MISP builds them; other tables have no metadata switch.
    assert 'metadata' not in plan_of('SELECT * FROM events LIMIT 5 FORMAT stix2', flatten=True).body
    assert 'metadata' not in plan_of('SELECT * FROM attributes LIMIT 5', flatten=True).body


def test_sends_query():
    assert sends_query(plan_of('SELECT * FROM events WHERE id = 1'))
    assert sends_query(plan_of('SELECT COUNT(*) FROM tags WHERE id > 0'))
    assert not sends_query(plan_of('SELECT * FROM events ORDER BY id LIMIT 1'))
    assert not sends_query(plan_of('DESCRIBE events'))
    assert not sends_query(plan_of('SHOW TABLES'))


def test_a_where_is_sent_only_once_misp_lists_its_tables():
    plan = plan_of("SELECT * FROM attributes WHERE type = 'md5' LIMIT 2")
    exchange = Exchange(plan)
    assert exchange.request == SUPPORT_CHECK == Request('GET', '/attributes/queryDescribe')
    asked = converse(exchange, ok(as_json(SUPPORTED)), ok(as_json({'response': {'Attribute': [{'id': '1'}]}})))
    assert asked == [SUPPORT_CHECK, Request('POST', '/attributes/restSearch', plan.body)]
    assert (exchange.done, exchange.result, exchange.supported, exchange.request) == (True, [{'id': '1'}], True, None)


@pytest.mark.parametrize('status, body, message', [
    (404, NOT_FOUND, 'this MISP does not support complex queries: it would ignore the WHERE and answer as if '
                     'there were none (/attributes/queryDescribe: HTTP 404: Not Found)'),
    (403, NO_PERMISSION, 'this key may not use complex queries, or this MISP does not support them '
                         '(/attributes/queryDescribe: HTTP 403: You do not have permission to use this '
                         'functionality.)'),
    (500, b'<pre class="cake-error">Warning</pre>',
     'cannot tell whether this MISP supports complex queries (/attributes/queryDescribe: HTTP 500)'),
    (200, b'{"tables": {}}',
     'cannot tell whether this MISP supports complex queries (/attributes/queryDescribe: unexpected answer)'),
    (200, b'<html>login</html>',
     'cannot tell whether this MISP supports complex queries (/attributes/queryDescribe: unexpected answer)'),
])
def test_no_where_reaches_a_misp_that_does_not_list_its_tables(status, body, message):
    exchange = Exchange(plan_of('SELECT COUNT(*) FROM events WHERE id = -1'))
    with pytest.raises(NoComplexQueries) as exc:
        exchange.receive(status, JSON, body)
    assert (str(exc.value), exc.value.status) == (message, status)
    assert isinstance(exc.value, ClientError)
    assert (exchange.request, exchange.done, exchange.supported) == (None, False, False)
    with pytest.raises(RuntimeError):
        exchange.receive(200, JSON, b'4291')
    with pytest.raises(NoComplexQueries):
        check_support(status, body)
    check_support(200, as_json(SUPPORTED))


def test_the_check_can_be_left_to_the_caller():
    plan = plan_of('SELECT COUNT(*) FROM attributes WHERE id > 0')
    exchange = Exchange(plan, check_support=False)
    assert converse(exchange, ok(b'12')) == [Request('POST', '/attributes/restSearch', plan.body)]
    assert (exchange.result, exchange.supported) == (12, False)


def test_no_check_without_a_where():
    assert Exchange(plan_of('SELECT * FROM events ORDER BY id LIMIT 1')).request.method == 'POST'
    assert Exchange(plan_of('DESCRIBE events')).request == Request('GET', '/events/queryDescribe')


def test_counts():
    count = Exchange(plan_of('SELECT COUNT(*) FROM events WHERE id > 0'), check_support=False)
    converse(count, ok(b'{"response": []}', {'x-result-count': '4291'}))
    assert count.result == 4291
    count = Exchange(plan_of('SELECT COUNT(*) FROM tags WHERE id > 0'), check_support=False)
    converse(count, ok('73\n'))
    assert count.result == 73
    count = Exchange(plan_of('SELECT COUNT(*) FROM events WHERE id > 0'), check_support=False)
    with pytest.raises(ClientError, match='/events/restSearch: no count in the answer'):
        converse(count, ok(b'{"response": []}'))


def test_unaligned_offset_asks_for_the_next_page_when_the_first_is_full():
    plan = plan_of('SELECT * FROM collections LIMIT 3 OFFSET 4')
    page = [{'Collection': {'id': i}, 'Orgc': {'name': 'CIRCL'}} for i in (3, 4, 5)]
    exchange = Exchange(plan)
    asked = converse(exchange, ok(as_json({'response': page})), ok(as_json({'response': page[:1]})))
    assert [request.body['page'] for request in asked] == [2, 3]
    assert exchange.result == [{'id': 4, 'Orgc': {'name': 'CIRCL'}}, {'id': 5, 'Orgc': {'name': 'CIRCL'}},
                               {'id': 3, 'Orgc': {'name': 'CIRCL'}}]
    short = Exchange(plan)
    converse(short, ok(as_json({'response': page[:2]})))
    assert (short.done, short.result) == (True, [{'id': 4, 'Orgc': {'name': 'CIRCL'}}])


def test_describe_and_show_tables():
    describe = Exchange(plan_of('DESCRIBE attributes'))
    assert converse(describe, ok(as_json(SUPPORTED))) == [SUPPORT_CHECK]
    assert describe.result == {'fields': SUPPORTED['tables']['attributes']['fields'], 'limits': {'max_depth': 5}}
    tables = Exchange(plan_of('SHOW TABLES'))
    asked = converse(tables, ok(b'{}'), (403, JSON, NO_PERMISSION), *[(404, JSON, NOT_FOUND)] * 8, ok(b'{}'))
    assert [request.path for request in asked] == [table.discovery for table in mispql.TABLES.values()]
    assert tables.result == ['attributes', 'organisations']
    tables = Exchange(plan_of('SHOW TABLES'))
    with pytest.raises(ClientError) as exc:
        converse(tables, (403, JSON, NO_PERMISSION), *[(404, JSON, NOT_FOUND)] * 10)
    assert (str(exc.value), exc.value.status) == ('HTTP 403: You do not have permission to use this functionality.',
                                                  403)


def test_query_errors_point_at_the_statement():
    text = 'SELECT * FROM attributes\nWHERE nosuch = 1 LIMIT 1'
    [statement] = mispql.compile(text)
    exchange = Exchange(statement.plan, check_support=False)
    reply = {'name': 'Invalid query', 'message': '2 errors', 'errors': [
        {'path': 'query.<unknown>', 'code': 'unknown_field', 'message': 'unknown field'},
        {'path': 'limit', 'code': 'limit_exceeded', 'message': 'too many'}]}
    with pytest.raises(QueryErrors) as exc:
        converse(exchange, (400, JSON, as_json(reply)))
    assert [(e.message, text[e.start:e.end]) for e in exc.value.located(statement.plan, statement)] == [
        ('unknown field', 'nosuch'), ('too many', text)]


@pytest.mark.parametrize('body', [
    b'not json', b'{"response": {"Attribute": {"id": "1"}}}', b'{"response": {"Attribute": ["1"]}}',
    b'{"response": [{"Event": {"id": "1"}}]}', b'{"response": ["x"]}', None,
])
def test_rows_of_an_unexpected_shape(body):
    exchange = Exchange(plan_of('SELECT * FROM attributes LIMIT 1'))
    with pytest.raises(ClientError, match='/attributes/restSearch: unexpected answer'):
        converse(exchange, ok(body))


@pytest.mark.parametrize('status, body, error', [
    (400, b'{"errors": ["not an object"]}', 'HTTP 400'),
    (400, b'{"message": "The query parameter is only accepted as a JSON object in a POST body."}',
     'HTTP 400: The query parameter is only accepted as a JSON object in a POST body.'),
    (502, b'', 'HTTP 502'),
])
def test_refusals(status, body, error):
    exchange = Exchange(plan_of('SELECT * FROM events LIMIT 1'))
    with pytest.raises(ClientError) as exc:
        converse(exchange, (status, {}, body))
    assert (str(exc.value), exc.value.status) == (error, status)


def test_misp_formats_come_back_as_received():
    exchange = Exchange(plan_of('SELECT * FROM attributes LIMIT 5 FORMAT csv'))
    converse(exchange, ok(b'uuid,value\nabc,1.2.3.4\n', {'Content-Type': 'text/csv'}))
    assert exchange.result == b'uuid,value\nabc,1.2.3.4\n'


def test_statements_about_connections_are_not_for_misp():
    for text in ('SHOW DATABASES', 'USE other', 'WHOAMI'):
        with pytest.raises(TypeError):
            Exchange(plan_of(text))


def test_flatten():
    assert mispql.flatten({'id': '1', 'Event': {'Orgc': {'name': 'CIRCL'}}, 'Tag': [{'name': 'tlp:clear'}],
                           'Attribute': [{'id': '2'}]}) == {
        'id': '1', 'Event.Orgc.name': 'CIRCL', 'Tag': ['tlp:clear']}


def test_the_library_loads_neither_requests_nor_prompt_toolkit():
    code = ('import sys, mispql; mispql.compile("SELECT * FROM events WHERE id = 1"); '
            'print(sorted({"requests", "prompt_toolkit"} & set(sys.modules)))')
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert result.stdout == '[]\n'
