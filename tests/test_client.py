import json
import socket
from pathlib import Path

import pytest

from mispql import __version__
from mispql.client import Client, ClientError, QueryErrors
from mispql.replies import NoComplexQueries
from mispql.compiler import compile_statement
from mispql.config import Settings
from mispql.parser import parse

SERVER_ERRORS = Path(__file__).parent / 'server_errors'


def client(url, checked=True, **settings):
    """checked: MISP has already shown this client that it runs complex queries."""
    c = Client(Settings(url=url, key='the-key', **settings))
    c.supports_queries = checked
    return c


def plan_of(text):
    return compile_statement(parse(text)[0])


def test_search(stub):
    plan = plan_of("SELECT * FROM attributes WHERE type = 'md5' LIMIT 10")
    stub.reply({'response': {'Attribute': [{'id': '1'}, {'id': '2'}]}})
    assert client(stub.url + '/').run(plan) == [{'id': '1'}, {'id': '2'}]
    [request] = stub.requests
    assert request['path'] == '/attributes/restSearch'
    assert request['body'] == plan.body
    headers = request['headers']
    assert (headers['Authorization'], headers['Accept'], headers['Content-Type'], headers['User-Agent']) == (
        'the-key', 'application/json', 'application/json', f'mispql/{__version__}')


def test_rows_are_unwrapped(stub):
    stub.reply({'response': [{'Event': {'id': '7'}}]})
    stub.reply({'response': [{'Object': {'id': '9'}}]})
    stub.reply({'response': {'Attribute': []}})
    stub.reply({'response': []})
    stub.reply({'response': [{'GalaxyCluster': {'id': '2', 'Galaxy': {'name': 'Threat Actor'}}}]})
    c = client(stub.url)
    assert c.run(plan_of('SELECT * FROM events LIMIT 1')) == [{'id': '7'}]
    assert c.run(plan_of('SELECT * FROM objects LIMIT 1')) == [{'id': '9'}]
    assert c.run(plan_of('SELECT * FROM attributes LIMIT 1')) == []
    assert c.run(plan_of('SELECT * FROM events LIMIT 1')) == []
    assert c.run(plan_of('SELECT * FROM galaxy_clusters LIMIT 1')) == [
        {'id': '2', 'Galaxy': {'name': 'Threat Actor'}}]
    assert stub.requests[-1]['path'] == '/galaxy_clusters/restSearch'


TABLE_ROWS = [
    ('event_reports', '/event_reports/restSearch',
     [{'EventReport': {'id': '4', 'Event': {'id': '1', 'info': 'Phishing'}, 'Tag': []}}],
     [{'id': '4', 'Event': {'id': '1', 'info': 'Phishing'}, 'Tag': []}]),
    ('notes', '/analyst_data/restSearch/Note', [{'Note': {'id': '5', 'note': 'n'}}], [{'id': '5', 'note': 'n'}]),
    ('opinions', '/analyst_data/restSearch/Opinion', [{'Opinion': {'id': '6'}}], [{'id': '6'}]),
    ('relationships', '/analyst_data/restSearch/Relationship', [{'Relationship': {'id': '7', 'related_object': []}}],
     [{'id': '7', 'related_object': []}]),
    ('collections', '/collections/restSearch',
     [{'Collection': {'id': '8', 'element_count': 1}, 'Orgc': {'id': '1', 'name': 'CIRCL'},
       'SharingGroup': {'id': None}, 'CollectionElement': [{'id': '9'}]}],
     [{'id': '8', 'element_count': 1, 'Orgc': {'id': '1', 'name': 'CIRCL'}, 'SharingGroup': {'id': None},
       'CollectionElement': [{'id': '9'}]}]),
    ('tags', '/tags/restSearch',
     [{'Tag': {'id': '1', 'Taxonomy': {'namespace': 'tlp'}, 'count': 3}}, {'Tag': {'id': '2'}}],
     [{'id': '1', 'Taxonomy': {'namespace': 'tlp'}, 'count': 3}, {'id': '2'}]),
    ('organisations', '/organisations/restSearch', [{'Organisation': {'id': '1', 'user_count': '2'}}],
     [{'id': '1', 'user_count': '2'}]),
]


@pytest.mark.parametrize('table, path, reply, rows', TABLE_ROWS)
def test_rows_of_each_table(stub, table, path, reply, rows):
    stub.reply({'response': reply})
    assert client(stub.url).run(plan_of(f'SELECT * FROM {table} LIMIT 2')) == rows
    [request] = stub.requests
    assert request['path'] == path


def test_keys_beside_the_row_follow_its_own_and_never_replace_them(stub):
    stub.reply({'response': [{'Orgc': {'name': 'beside'}, 'Collection': {'id': '8', 'Orgc': {'name': 'own'}},
                              'SharingGroup': {'id': None}}]})
    [row] = client(stub.url).run(plan_of('SELECT * FROM collections LIMIT 1'))
    assert list(row.items()) == [('id', '8'), ('Orgc', {'name': 'own'}), ('SharingGroup', {'id': None})]


@pytest.mark.parametrize('table, path', [(table, path) for table, path, _, _ in TABLE_ROWS]
                         + [('objects', '/objects/restSearch')])
def test_count_of_each_table(stub, table, path):
    stub.reply(b'12')
    assert client(stub.url).run(plan_of(f'SELECT COUNT(*) FROM {table} WHERE id > 0')) == 12
    [request] = stub.requests
    assert (request['path'], request['body']) == (path, {'returnFormat': 'count', 'query': {'id': {'>': 0}}})


def test_unaligned_offset_reads_two_pages(stub):
    plan = plan_of('SELECT * FROM attributes LIMIT 3 OFFSET 4')
    stub.reply({'response': {'Attribute': [{'id': i} for i in (3, 4, 5)]}})
    stub.reply({'response': {'Attribute': [{'id': i} for i in (6, 7, 8)]}})
    assert client(stub.url).run(plan) == [{'id': 4}, {'id': 5}, {'id': 6}]
    assert [r['body']['page'] for r in stub.requests] == [2, 3]
    assert {r['body']['limit'] for r in stub.requests} == {3}


def test_unaligned_offset_stops_after_a_short_page(stub):
    stub.reply({'response': {'Attribute': [{'id': 3}, {'id': 4}]}})
    assert client(stub.url).run(plan_of('SELECT * FROM attributes LIMIT 3 OFFSET 4')) == [{'id': 4}]
    assert len(stub.requests) == 1


def test_trace_sees_each_request_sent(stub):
    traced = []
    c = Client(Settings(url=stub.url, key='k'), trace=lambda *request: traced.append(request))
    plan = plan_of('SELECT * FROM attributes LIMIT 3 OFFSET 4')
    stub.reply({'response': {'Attribute': [{'id': i} for i in (3, 4, 5)]}})
    stub.reply({'response': {'Attribute': []}})
    stub.reply({'response': {'Attribute': [{'id': 3}]}})
    c.run(plan)
    c.run(plan)
    url = f'{stub.url}/attributes/restSearch'
    assert traced == [('POST', url, plan.body), ('POST', url, {**plan.body, 'page': 3}),
                      ('POST', url, plan.body)]
    assert [r['body'] for r in stub.requests] == [body for _, _, body in traced]


def test_verbatim_format_returns_the_response(stub):
    stub.reply(b'uuid,value\nabc,1.2.3.4\n', headers={'Content-Type': 'text/csv'})
    response = client(stub.url).run(plan_of('SELECT * FROM attributes LIMIT 5 FORMAT csv'))
    assert b''.join(response.iter_content(4)) == b'uuid,value\nabc,1.2.3.4\n'
    assert stub.requests[0]['body']['returnFormat'] == 'csv'


def test_verbatim_offset_does_not_stream_the_wrong_page(stub):
    stub.reply(b'uuid,value\n', headers={'Content-Type': 'text/csv', 'X-Result-Page-Limit': '2'})
    with pytest.raises(ClientError, match=r'MISP applied LIMIT 2 instead of 3'):
        client(stub.url).run(plan_of('SELECT * FROM attributes LIMIT 3 OFFSET 3 FORMAT csv'))


def test_counts(stub):
    stub.reply({'response': [{'Event': {'id': '1'}}]}, headers={'X-Result-Count': '6375'})
    stub.reply(b'73890')
    stub.reply(b'2028', headers={'X-Result-Count': '2028'})
    c = client(stub.url)
    assert c.run(plan_of('SELECT COUNT(*) FROM events WHERE id > 0')) == 6375
    assert c.run(plan_of("SELECT COUNT(*) FROM attributes WHERE type = 'md5'")) == 73890
    assert c.run(plan_of("SELECT COUNT(*) FROM galaxy_clusters WHERE galaxy_name = 'Threat Actor'")) == 2028
    stub.reply({'response': []})
    with pytest.raises(ClientError, match='no count in the answer'):
        c.run(plan_of('SELECT COUNT(*) FROM events WHERE id > 0'))


def test_query_errors_point_at_the_statement(stub):
    capture = json.loads((SERVER_ERRORS / 'attribute_leaves.json').read_text())
    text = capture['statement']
    reply = capture['reply']
    reply['errors'].append({'path': 'limit', 'code': 'bad_type', 'message': 'somewhere else'})
    stub.reply(reply, status=400)
    statement = parse(text)[0]
    plan = compile_statement(statement)
    with pytest.raises(QueryErrors) as exc:
        client(stub.url).run(plan)
    located = [(e.message, text[e.start:e.end]) for e in exc.value.located(plan, statement)]
    assert located == [
        ('unknown field', 'nosuch'),
        ('this field expects an integer', "id = 'x'"),
        ('this field expects a non-empty string', "type IN ('a', 5)"),
        ('LIKE takes a leading and/or trailing %, nothing else', "value NOT LIKE 'a%b'"),
        ('operator not allowed for this field', "id LIKE '1%'"),
        ('somewhere else', text),
    ]


@pytest.mark.parametrize('status, payload, message', [
    (403, {'name': 'Forbidden', 'message': 'You do not have the permission to use complex queries.'},
     'HTTP 403: You do not have the permission to use complex queries.'),
    (404, {'name': 'Invalid output format.', 'message': 'Invalid output format.'},
     'HTTP 404: Invalid output format.'),
    (400, {'name': 'Bad request', 'message': 'The query parameter is only accepted as a JSON object in a POST body.'},
     'HTTP 400: The query parameter is only accepted as a JSON object in a POST body.'),
    (500, b'<pre class="cake-error">Warning</pre>', 'HTTP 500'),
    (502, [], 'HTTP 502'),
])
def test_http_errors(stub, status, payload, message):
    stub.reply(payload, status=status)
    with pytest.raises(ClientError) as exc:
        client(stub.url).run(plan_of('SELECT * FROM events LIMIT 1'))
    assert (str(exc.value), exc.value.status) == (message, status)


DISCOVERY = {
    'tables': {'events': {
        'endpoint': '/events/restSearch',
        'fields': {'id': {'type': 'int', 'operators': ['='], 'level': ['event']}},
        'sortable': ['id', 'info'],
    }},
    'limits': {'max_depth': 5},
}
FORBIDDEN = {'name': 'Forbidden', 'message': 'You do not have the permission to use complex queries.'}


def test_describe(stub):
    stub.reply(DISCOVERY)
    assert client(stub.url).run(plan_of('DESCRIBE events')) == {
        'fields': DISCOVERY['tables']['events']['fields'], 'limits': {'max_depth': 5}}
    [request] = stub.requests
    assert (request['method'], request['path'], request['body']) == ('GET', '/events/queryDescribe', None)
    stub.reply({'tables': {'attributes': DISCOVERY['tables']['events']}, 'limits': {}})
    with pytest.raises(ClientError, match='/events/queryDescribe: unexpected answer'):
        client(stub.url).run(plan_of('DESCRIBE events'))


SHOW_TABLES_PATHS = [
    *(f'/{table}/queryDescribe' for table in ('attributes', 'events', 'objects', 'galaxy_clusters',
                                              'event_reports')),
    *(f'/analyst_data/queryDescribe/{kind}' for kind in ('Note', 'Opinion', 'Relationship')),
    *(f'/{table}/queryDescribe' for table in ('collections', 'tags', 'organisations')),
]


def test_show_tables_leaves_out_a_table_that_refuses(stub):
    stub.reply(DISCOVERY)
    stub.reply(FORBIDDEN, status=403)
    stub.reply(DISCOVERY)
    for _ in range(4):
        stub.reply({'message': 'Not found'}, status=404)
    stub.reply(DISCOVERY)
    stub.reply(DISCOVERY)
    stub.reply(DISCOVERY)
    stub.reply({'message': 'You do not have permission to use this functionality.'}, status=403)
    assert client(stub.url).run(plan_of('SHOW TABLES')) == [
        'attributes', 'objects', 'relationships', 'collections', 'tags']
    assert [(r['method'], r['path']) for r in stub.requests] == [('GET', path) for path in SHOW_TABLES_PATHS]


def test_show_tables_fails_with_the_first_error_when_none_answers(stub):
    stub.reply(FORBIDDEN, status=403)
    stub.reply({'message': 'boom'}, status=500)
    for _ in range(9):
        stub.reply({'message': 'Not found'}, status=404)
    with pytest.raises(ClientError) as exc:
        client(stub.url).run(plan_of('SHOW TABLES'))
    assert str(exc.value) == f'HTTP 403: {FORBIDDEN["message"]}'
    assert len(stub.requests) == 11


def test_show_tables_stops_on_a_transport_error():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    traced = []
    c = Client(Settings(url=f'http://127.0.0.1:{port}', key='k'), trace=lambda *request: traced.append(request))
    with pytest.raises(ClientError) as exc:
        c.run(plan_of('SHOW TABLES'))
    assert (str(exc.value), exc.value.status) == (f'cannot reach http://127.0.0.1:{port}/attributes/queryDescribe', None)
    assert traced == [('GET', f'http://127.0.0.1:{port}/attributes/queryDescribe', None)]


def test_unexpected_answer(stub):
    stub.reply(b'not json')
    with pytest.raises(ClientError, match='/events/restSearch: unexpected answer'):
        client(stub.url).run(plan_of('SELECT * FROM events LIMIT 1'))


def test_timeout(stub):
    stub.reply({'response': []}, delay=0.5)
    with pytest.raises(ClientError) as exc:
        client(stub.url, timeout=0.1).run(plan_of('SELECT * FROM events LIMIT 1'))
    assert str(exc.value) == f'no answer from {stub.url}/events/restSearch within 0.1 s'


def test_connection_refused():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    with pytest.raises(ClientError) as exc:
        client(f'http://127.0.0.1:{port}').run(plan_of('SELECT * FROM events LIMIT 1'))
    assert str(exc.value) == f'cannot reach http://127.0.0.1:{port}/events/restSearch'


def test_settings_reach_the_session():
    c = Client(Settings(url='https://misp', key='k', verify_ssl='/etc/ca.pem'))
    assert c.session.verify == '/etc/ca.pem'
    with pytest.raises(ClientError, match='no API key'):
        Client(Settings(url='https://misp'))


NOT_FOUND = {'name': 'Not Found', 'message': 'Not Found', 'url': '/attributes/queryDescribe'}
SUPPORT_CHECK = ('GET', '/attributes/queryDescribe')


def test_a_where_waits_for_misp_to_list_its_tables_once_per_client(stub):
    stub.reply(DISCOVERY)
    stub.reply(b'4')
    stub.reply(b'uuid,value\n', headers={'Content-Type': 'text/csv'})
    stub.reply({'response': []})
    c = client(stub.url, checked=False)
    assert c.run(plan_of('SELECT COUNT(*) FROM attributes WHERE id > 0')) == 4
    response = c.run(plan_of("SELECT * FROM attributes WHERE type = 'md5' LIMIT 5 FORMAT csv"))
    assert b''.join(response.iter_content()) == b'uuid,value\n'
    assert c.run(plan_of('SELECT * FROM events WHERE id = 1')) == []
    assert [(r['method'], r['path']) for r in stub.requests] == [
        SUPPORT_CHECK, ('POST', '/attributes/restSearch'), ('POST', '/attributes/restSearch'),
        ('POST', '/events/restSearch')]


@pytest.mark.parametrize('text', ['SELECT COUNT(*) FROM events WHERE id = -1',
                                  'SELECT * FROM attributes WHERE id = -1 LIMIT 5 FORMAT csv'])
def test_a_misp_without_complex_queries_gets_no_where(stub, text):
    """Such a MISP drops the WHERE without a word: the count would be every event's."""
    stub.reply(NOT_FOUND, status=404)
    stub.reply(NOT_FOUND, status=404)
    stub.reply(DISCOVERY)
    stub.reply(b'0', headers={'X-Result-Count': '0'})
    c = client(stub.url, checked=False)
    with pytest.raises(NoComplexQueries) as exc:
        c.run(plan_of(text))
    assert exc.value.status == 404
    assert str(exc.value).startswith('this MISP does not support complex queries')
    with pytest.raises(NoComplexQueries):
        c.run(plan_of(text))
    # A refusal is not remembered: the same client asks again, and goes on once MISP lists its tables.
    c.run(plan_of(text))
    assert [(r['method'], r['path']) for r in stub.requests] == [SUPPORT_CHECK] * 3 + [
        ('POST', plan_of(text).endpoint)]


def test_no_check_without_a_where(stub):
    stub.reply({'response': []})
    stub.reply(DISCOVERY)
    c = client(stub.url, checked=False)
    c.run(plan_of('SELECT * FROM events ORDER BY id LIMIT 1'))
    c.run(plan_of('DESCRIBE events'))
    assert [(r['method'], r['path']) for r in stub.requests] == [('POST', '/events/restSearch'),
                                                                 ('GET', '/events/queryDescribe')]
