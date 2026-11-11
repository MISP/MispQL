import io
import json

import pytest
import requests

from mispql.client import ClientError
from mispql.compiler import compile_statement
from mispql.output import flatten, write
from mispql.parser import parse

ATTRIBUTES = [
    {
        'id': '12', 'event_id': '3', 'category': 'Network activity', 'type': 'ip-dst', 'value': '1.2.3.4',
        'to_ids': True, 'timestamp': '1700000000', 'object_relation': None,
        'Event': {'id': '3', 'info': 'Phishing, round 2', 'Orgc': {'id': '1', 'name': 'CIRCL'}},
        'Object': {'id': '7'},
        'Tag': [{'id': '1', 'name': 'tlp:white'}, {'id': '2', 'name': 'osint:source-type="blog-post"'}],
    },
    {
        'id': '13', 'event_id': '3', 'category': 'Other', 'type': 'text', 'value': 'line one\nline "two"',
        'to_ids': False, 'timestamp': '1700000001', 'object_relation': None,
        'Event': {'id': '3', 'info': 'Phishing, round 2', 'Orgc': {'id': '1', 'name': 'CIRCL'}},
    },
]
EVENTS = [{
    'id': '3', 'date': '2026-11-01', 'info': 'Phishing, round 2', 'threat_level_id': '1', 'analysis': '2',
    'published': False, 'attribute_count': '2',
    'Orgc': {'id': '1', 'name': 'CIRCL'},
    'Attribute': [{'id': '12', 'value': '1.2.3.4'}, {'id': '13', 'value': 'x'}],
    'Galaxy': [], 'RelatedEvent': [],
    'Tag': [{'id': '1', 'name': 'tlp:white'}],
}]


def render(statement, result):
    plan = compile_statement(parse(statement)[0])
    out = io.BytesIO()
    warnings = write(plan, result, out)
    return out.getvalue().decode(), warnings


def test_flatten():
    assert flatten(ATTRIBUTES[0]) == {
        'id': '12', 'event_id': '3', 'category': 'Network activity', 'type': 'ip-dst', 'value': '1.2.3.4',
        'to_ids': True, 'timestamp': '1700000000', 'object_relation': None,
        'Event.id': '3', 'Event.info': 'Phishing, round 2', 'Event.Orgc.id': '1', 'Event.Orgc.name': 'CIRCL',
        'Object.id': '7', 'Tag': ['tlp:white', 'osint:source-type="blog-post"'],
    }
    flat = flatten(EVENTS[0])
    assert 'Attribute' not in flat and 'Galaxy' not in flat and 'RelatedEvent' not in flat
    assert (flat['Orgc.name'], flat['Tag']) == ('CIRCL', ['tlp:white'])
    assert flatten({'Tag': [], 'list': ['a', 1]}) == {'Tag': [], 'list': ['a', 1]}


def test_default_is_the_rows_as_json():
    text, warnings = render('SELECT * FROM events LIMIT 1', EVENTS)
    assert json.loads(text) == EVENTS
    assert text.endswith('\n') and warnings == []


def test_default_with_columns():
    text, warnings = render('SELECT id, event.info, Event.Orgc.name, tag, nosuch FROM attributes LIMIT 2', ATTRIBUTES)
    assert json.loads(text) == [
        {'id': '12', 'Event.info': 'Phishing, round 2', 'Event.Orgc.name': 'CIRCL',
         'Tag': ['tlp:white', 'osint:source-type="blog-post"'], 'nosuch': None},
        {'id': '13', 'Event.info': 'Phishing, round 2', 'Event.Orgc.name': 'CIRCL', 'Tag': None, 'nosuch': None},
    ]
    assert warnings == ['no row has a column nosuch']


def test_flat_all_columns():
    text, warnings = render('SELECT * FROM attributes LIMIT 2 FORMAT flat', ATTRIBUTES)
    assert text == (
        'id,event_id,category,type,value,to_ids,timestamp,object_relation,Event.id,Event.info,'
        'Event.Orgc.id,Event.Orgc.name,Object.id,Tag\n'
        '12,3,Network activity,ip-dst,1.2.3.4,true,1700000000,,3,"Phishing, round 2",1,CIRCL,7,'
        '"tlp:white,osint:source-type=""blog-post"""\n'
        '13,3,Other,text,"line one\nline ""two""",false,1700000001,,3,"Phishing, round 2",1,CIRCL,,\n'
    )
    assert warnings == []


def test_flat_columns():
    text, warnings = render('SELECT value, `EVENT`.`INFO`, typo FROM attributes LIMIT 2 FORMAT flat', ATTRIBUTES)
    assert text == (
        'value,Event.info,typo\n'
        '1.2.3.4,"Phishing, round 2",\n'
        '"line one\nline ""two""","Phishing, round 2",\n'
    )
    assert warnings == ['no row has a column typo']


def test_flat_without_rows():
    assert render('SELECT * FROM attributes LIMIT 1 FORMAT flat', []) == ('', [])
    assert render('SELECT id, value FROM attributes LIMIT 1 FORMAT flat', []) == ('id,value\n', [])


def test_table_default_columns():
    text, warnings = render('SELECT * FROM events LIMIT 1 FORMAT table', EVENTS)
    assert text == (
        '+----+------------+-------------------+-----------+-----------------+----------+-----------+-----------------+-----------+\n'
        '| id | date       | info              | Orgc.name | threat_level_id | analysis | published | attribute_count | Tag       |\n'
        '+----+------------+-------------------+-----------+-----------------+----------+-----------+-----------------+-----------+\n'
        '| 3  | 2026-11-01 | Phishing, round 2 | CIRCL     | 1               | 2        | false     | 2               | tlp:white |\n'
        '+----+------------+-------------------+-----------+-----------------+----------+-----------+-----------------+-----------+\n'
        '1 row\n'
    )
    assert warnings == []


def test_table_columns_and_long_cells():
    rows = [{'id': '1', 'value': 'a' * 70}, {'id': '2', 'value': 'tab\there\r\nnext'}]
    text, warnings = render('SELECT id, value FROM attributes LIMIT 2 FORMAT table', rows)
    lines = text.splitlines()
    assert lines[3] == f'| 1  | {"a" * 59}… |'
    assert lines[4] == f'| 2  | {"tab\\there\\r\\nnext".ljust(60)} |'
    assert lines[-1] == '2 rows'


def test_table_without_rows():
    text, warnings = render('SELECT id FROM objects LIMIT 1 FORMAT table', [])
    assert text == '+----+\n| id |\n+----+\n0 rows\n'
    assert warnings == []


def test_table_default_columns_do_not_warn():
    text, warnings = render('SELECT * FROM attributes LIMIT 1 FORMAT table', [ATTRIBUTES[1]])
    assert warnings == []
    header, row = text.splitlines()[1], text.splitlines()[3]
    assert header.split('|')[-2].strip() == 'Tag' and row.split('|')[-2].strip() == ''


class FakeResponse:
    url = 'http://misp/attributes/restSearch'

    def __init__(self, parts, fail=False):
        self.parts = parts
        self.fail = fail

    def iter_content(self, size):
        yield from self.parts
        if self.fail:
            raise requests.exceptions.ChunkedEncodingError('connection reset')


def test_verbatim_bytes_pass_through():
    payload = [b'PK\x03\x04', b'\x00\xffbinary']
    plan = compile_statement(parse('SELECT * FROM attributes LIMIT 1 FORMAT xlsx')[0])
    out = io.BytesIO()
    assert write(plan, FakeResponse(payload), out) == []
    assert out.getvalue() == b''.join(payload)


def test_verbatim_broken_answer():
    plan = compile_statement(parse('SELECT * FROM attributes LIMIT 1 FORMAT csv')[0])
    with pytest.raises(ClientError, match='the answer broke off'):
        write(plan, FakeResponse([b'a,b\n'], fail=True), io.BytesIO())


def test_count():
    assert render("SELECT COUNT(*) FROM attributes WHERE type = 'md5'", 73890) == ('73890\n', [])


def test_describe():
    found = {
        'fields': {
            'id': {'type': 'int', 'operators': ['=', '!=', '>', 'IN'], 'level': ['event']},
            'tags': {'type': 'string', 'operators': ['=', 'LIKE'], 'level': ['attribute', 'event']},
        },
        'limits': {'max_depth': 5, 'max_list': 100},
    }
    text, warnings = render('DESCRIBE events', found)
    assert warnings == []
    assert text == (
        '+-------+--------+-----------+-----------------+\n'
        '| field | type   | operators | level           |\n'
        '+-------+--------+-----------+-----------------+\n'
        '| id    | int    | = != > IN | event           |\n'
        '| tags  | string | = LIKE    | attribute,event |\n'
        '+-------+--------+-----------+-----------------+\n'
        '2 fields\n'
        'ORDER BY: id, event_id, eventinfo, analysis, threat_level_id, distribution, timestamp, '
        'event_timestamp, publish_timestamp\n'
        'limits: max_depth 5, max_list 100\n'
    )
    assert render('DESCRIBE objects', found)[0].splitlines()[-2] == 'ORDER BY: none'


def test_show_tables():
    assert render('SHOW TABLES', ['attributes', 'events'])[0] == (
        '+------------+\n| table      |\n+------------+\n| attributes |\n| events     |\n+------------+\n2 rows\n')
    assert render('SHOW TABLES', [])[0] == '+-------+\n| table |\n+-------+\n0 rows\n'
