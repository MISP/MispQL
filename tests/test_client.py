import json
import socket
from pathlib import Path

import pytest

from mispql.client import Client, ClientError, QueryErrors
from mispql.compiler import compile_statement
from mispql.config import Settings
from mispql.parser import parse

SERVER_ERRORS = Path(__file__).parent / 'server_errors'


def client(url, **settings):
    return Client(Settings(url=url, key='the-key', **settings))


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
        'the-key', 'application/json', 'application/json', 'mispql/0.1.0')


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


def test_show_tables_leaves_out_a_table_that_refuses(stub):
    stub.reply(DISCOVERY)
    stub.reply(FORBIDDEN, status=403)
    stub.reply(DISCOVERY)
    stub.reply({'message': 'Not found'}, status=404)
    assert client(stub.url).run(plan_of('SHOW TABLES')) == ['attributes', 'objects']
    assert [(r['method'], r['path']) for r in stub.requests] == [
        ('GET', f'/{table}/queryDescribe') for table in ('attributes', 'events', 'objects', 'galaxy_clusters')]


def test_show_tables_fails_with_the_first_error_when_none_answers(stub):
    stub.reply(FORBIDDEN, status=403)
    stub.reply({'message': 'boom'}, status=500)
    stub.reply({'message': 'Not found'}, status=404)
    stub.reply({'message': 'Not found'}, status=404)
    with pytest.raises(ClientError) as exc:
        client(stub.url).run(plan_of('SHOW TABLES'))
    assert str(exc.value) == f'HTTP 403: {FORBIDDEN["message"]}'
    assert len(stub.requests) == 4


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
