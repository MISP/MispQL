import json
import os
from dataclasses import fields
from pathlib import Path

import pytest

from mispql.compiler import compile_statement
from mispql.errors import MispQLError
from mispql.parser import parse

HERE = Path(__file__).parent
GOLDEN = HERE / 'golden'
SERVER_ERRORS = HERE / 'server_errors'


def compile_one(text):
    statements = parse(text)
    assert len(statements) == 1
    return compile_statement(statements[0])


def summary(plan, text):
    data = {'plan': type(plan).__name__}
    data.update({f.name: getattr(plan, f.name) for f in fields(plan) if f.name != 'spans'})
    data['spans'] = {path: text[start:end] for path, (start, end) in plan.spans.items()}
    return data


@pytest.mark.parametrize('case', sorted(path.stem for path in GOLDEN.glob('*.mql')))
def test_golden(case):
    """MISPQL_UPDATE_GOLDEN=1 rewrites the .json files; review the diff before committing."""
    text = (GOLDEN / f'{case}.mql').read_text()
    got = summary(compile_one(text), text)
    expected = GOLDEN / f'{case}.json'
    if os.environ.get('MISPQL_UPDATE_GOLDEN') == '1':
        expected.write_text(json.dumps(got, indent=2) + '\n')
    assert got == json.loads(expected.read_text())


@pytest.mark.parametrize('text, fragment, message', [
    ('SELECT * FROM tags LIMIT 1', 'tags',
     'unknown table tags: use attributes, events, objects or galaxy_clusters'),
    ('DESCRIBE `Attributes`', '`Attributes`',
     'unknown table Attributes: use attributes, events, objects or galaxy_clusters'),
    ('SELECT * FROM galaxy_clusters ORDER BY description LIMIT 1', 'description',
     'cannot sort galaxy_clusters by description; use id, version, type, value, distribution, orgc_id, '
     'org_id, tag_name, galaxy_id'),
    ("SELECT * FROM galaxy_clusters WHERE Galaxy.name = 'x'", 'Galaxy.name',
     'galaxy is not the table queried here (galaxy_clusters)'),
    ('SELECT * FROM attributes WHERE events.id = 1', 'events.id',
     'events is not the table queried here (attributes)'),
    ("SELECT * FROM events WHERE id = 1 AND Event.info = 'x'", 'Event.info',
     'event is not the table queried here (events)'),
    ('SELECT events.info FROM attributes LIMIT 1', 'events.info',
     'events is not the table queried here (attributes)'),
    ('SELECT * FROM attributes WHERE events.id = 1 ORDER BY bogus', 'events.id',
     'events is not the table queried here (attributes)'),
    ('SELECT * FROM events', 'SELECT * FROM events',
     'add a WHERE or a LIMIT: MISP takes no search without either'),
    ('SELECT id FROM attributes ORDER BY id;', 'SELECT id FROM attributes ORDER BY id',
     'add a WHERE or a LIMIT: MISP takes no search without either'),
    ('SELECT * FROM events WHERE id = 1 OFFSET 10', '10', 'OFFSET needs a LIMIT'),
    ('SELECT * FROM events LIMIT 0', '0', 'LIMIT must be at least 1'),
    ('SELECT * FROM events ORDER BY info LIMIT 1', 'info',
     'cannot sort events by info; use id, event_id, eventinfo, analysis, threat_level_id, '
     'distribution, timestamp, event_timestamp, publish_timestamp'),
    ('SELECT * FROM attributes ORDER BY Event.publish_timestamp LIMIT 1', 'Event.publish_timestamp',
     'event is not the table queried here (attributes)'),
    ('SELECT * FROM attributes ORDER BY attributes.id, events.id LIMIT 1', 'events.id',
     'events is not the table queried here (attributes)'),
    ('SELECT * FROM objects ORDER BY id DESC LIMIT 1', 'id DESC', 'objects cannot be sorted'),
    ('SELECT COUNT(*) FROM objects WHERE id = 1', 'objects', 'COUNT(*) is not available on objects'),
    ('SELECT COUNT(*) FROM events', 'COUNT(*)', 'COUNT(*) needs a WHERE'),
    ('SELECT COUNT(*) FROM events LIMIT 5', 'COUNT(*)', 'COUNT(*) needs a WHERE'),
    ('SELECT COUNT(*) FROM events WHERE id > 1 ORDER BY id DESC', 'id DESC', 'COUNT(*) takes no ORDER BY'),
    ('SELECT COUNT(*) FROM events WHERE id > 1 LIMIT 5', '5', 'COUNT(*) takes no LIMIT'),
    ('SELECT COUNT(*) FROM events WHERE id > 1 OFFSET 5', '5', 'COUNT(*) takes no OFFSET'),
    ('SELECT COUNT(*) FROM attributes WHERE id > 1 FORMAT json', 'json', 'COUNT(*) takes no FORMAT'),
    ('SELECT COUNT(*) FROM attributes WHERE events.id > 1 FORMAT json', 'events.id',
     'events is not the table queried here (attributes)'),
    ('SELECT id, value FROM attributes LIMIT 5 FORMAT csv', 'csv',
     'a column list needs FORMAT table, FORMAT flat or no FORMAT: FORMAT csv is printed as MISP sends it'),
    ('SELECT * FROM attributes LIMIT 10 OFFSET 15 FORMAT csv', '15',
     'OFFSET must be a multiple of LIMIT with FORMAT csv, which is printed as MISP sends it'),
])
def test_errors(text, fragment, message):
    with pytest.raises(MispQLError) as exc:
        compile_one(text)
    assert exc.value.message == message
    assert text[exc.value.start:exc.value.end] == fragment


def test_unaligned_offset_is_fine_with_the_tools_renderings():
    for rendering in ('', 'FORMAT table', 'FORMAT flat'):
        plan = compile_one(f'SELECT * FROM attributes LIMIT 10 OFFSET 15 {rendering}')
        assert (plan.body['page'], plan.skip) == (2, 5)


# The text each error path of a captured server reply points at.
LOCATED = {
    'attribute_leaves': {
        'query.AND[0].<unknown>': 'nosuch',
        'query.AND[1].id': "id = 'x'",
        'query.AND[2].type': "type IN ('a', 5)",
        'query.AND[3].value': "value NOT LIKE 'a%b'",
        'query.AND[4].id': "id LIKE '1%'",
    },
    'event_negation': {'query.AND[1].OR[1].type': "type != 'btc'"},
    'event_negated_tags': {'query.OR[1].tags': "tags NOT LIKE 'tlp:%'"},
    'object_not_over_attribute': {'query.OR[0].NOT.value': "value = 'x'"},
    'galaxy_cluster_leaves': {
        'query.AND[0].org': "org LIKE 'MISP%'",
        'query.AND[1].<unknown>': 'nosuch',
        'query.AND[2].default': "default = 'yes'",
    },
    'too_deep': {
        'query.AND[0].OR[0].AND[0].OR[0].AND[0]': 'id = 1',
        'query.AND[0].OR[0].AND[0].OR[0].AND[1]': 'id = 2',
    },
}


@pytest.mark.parametrize('case', sorted(LOCATED))
def test_server_error_paths(case):
    """Replies captured by capture_server_errors.py; rerun it when a request body changes."""
    capture = json.loads((SERVER_ERRORS / f'{case}.json').read_text())
    text = capture['statement']
    plan = compile_one(text)
    assert plan.body == capture['body']
    assert capture['status'] == 400
    located = {}
    for error in capture['reply']['errors']:
        start, end = plan.locate(error['path'])
        located[error['path']] = text[start:end]
    assert located == LOCATED[case]


def test_locate():
    text = "SELECT * FROM attributes WHERE id > 1 AND (type = 'a' OR id = 2) ORDER BY id DESC LIMIT 1"
    plan = compile_one(text)

    def at(path):
        span = plan.locate(path)
        return span and text[slice(*span)]

    assert at('query') == "id > 1 AND (type = 'a' OR id = 2)"
    assert at('query.AND[1].OR[0]') == "type = 'a'"
    assert at('query.AND[1].OR[0].<unknown>') == 'type'
    assert at('query.AND[1].OR[0].type.IN[3]') == "type = 'a'"
    assert at('query.AND[1].OR[7]') == "type = 'a' OR id = 2"
    assert at('query.AND[11]') == "id > 1 AND (type = 'a' OR id = 2)"
    assert at('order[0]') == 'id DESC'
    assert at('limit') is None
    assert at('') is None
