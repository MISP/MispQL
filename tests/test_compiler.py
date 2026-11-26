import json
import os
from dataclasses import fields
from pathlib import Path

import pytest

from mispql.compiler import compile_statement
from mispql.errors import MispQLError
from mispql.parser import parse
from mispql.tables import TABLES

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


ALL_TABLES = ('attributes, events, objects, galaxy_clusters, event_reports, notes, opinions, relationships, '
              'collections, tags or organisations')


@pytest.mark.parametrize('text, fragment, message', [
    ('SELECT * FROM sightings LIMIT 1', 'sightings', f'unknown table sightings: use {ALL_TABLES}'),
    ('DESCRIBE `Attributes`', '`Attributes`', f'unknown table Attributes: use {ALL_TABLES}'),
    ('SELECT * FROM tags ORDER BY colour LIMIT 1', 'colour',
     'cannot sort tags by colour; use id, name, numerical_value'),
    ('SELECT * FROM notes ORDER BY note LIMIT 1', 'note',
     'cannot sort notes by note; use id, created, modified, distribution'),
    ("SELECT * FROM opinions WHERE notes.id = 1", 'notes.id', 'notes is not the table queried here (opinions)'),
    ('SELECT tags.name FROM event_reports LIMIT 1', 'tags.name',
     'tags is not the table queried here (event_reports)'),
    ('SELECT * FROM galaxy_clusters ORDER BY description LIMIT 1', 'description',
     'cannot sort galaxy_clusters by description; use id, version, type, value, distribution, orgc_id, '
     'org_id, tag_name, galaxy_id'),
    ("SELECT * FROM galaxy_clusters WHERE events.id = 1", 'events.id',
     'events is not the table queried here (galaxy_clusters)'),
    ('SELECT * FROM attributes WHERE events.id = 1', 'events.id',
     'events is not the table queried here (attributes)'),
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
    ('SELECT * FROM attributes ORDER BY attributes.id, events.id LIMIT 1', 'events.id',
     'events is not the table queried here (attributes)'),
    ('SELECT * FROM objects ORDER BY id DESC LIMIT 1', 'id DESC', 'objects cannot be sorted'),
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


def test_query_names_in_column_lists_become_row_keys():
    for table in TABLES.values():
        for name, key in table.aliases.items():
            plan = compile_one(f'SELECT id, {table.name}.{name} FROM {table.name} LIMIT 1')
            assert plan.columns == ['id', key], (table.name, name)
    plan = compile_one("SELECT eventinfo, meta.org FROM events WHERE eventinfo = 'x' LIMIT 1")
    assert (plan.columns, plan.body['query']) == (['info', 'meta.org'], {'eventinfo': 'x'})


@pytest.mark.parametrize('table, names, wire_name', [
    ('events', ('info', 'eventinfo', 'events.info'), 'info'),
    ('attributes', ('Event.publish_timestamp', '`Event.publish_timestamp`', 'publish_timestamp'),
     'Event.publish_timestamp'),
])
def test_order_accepts_misp_discovery_names_and_existing_aliases(table, names, wire_name):
    for name in names:
        plan = compile_one(f'SELECT * FROM {table} ORDER BY {name} DESC LIMIT 1')
        assert plan.body['order'] == [f'{wire_name} desc']


@pytest.mark.parametrize('text, metadata', [
    ('SELECT id, info FROM events LIMIT 5', True),
    ('SELECT id, Attribute.value FROM events LIMIT 5 FORMAT flat', True),
    ('SELECT * FROM events LIMIT 5 FORMAT table', True),
    ('SELECT * FROM events LIMIT 5 FORMAT flat', True),
    ('SELECT * FROM events LIMIT 5', False),
    ('SELECT * FROM events LIMIT 5 FORMAT json', False),
    ('SELECT * FROM events LIMIT 5 FORMAT csv', False),
    ('SELECT id, value FROM attributes LIMIT 5', False),
    ('SELECT * FROM objects LIMIT 5 FORMAT table', False),
])
def test_metadata_when_the_tool_flattens_event_rows(text, metadata):
    assert ('metadata' in compile_one(text).body) == metadata


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
    'galaxy_cluster_meta': {
        'query.OR[0].meta.<key>': "meta.synonyms > 'x'",
        'query.OR[1].meta.<key>': 'meta.`Additional Content Note` = 1',
    },
    'galaxy_cluster_leaves': {
        'query.AND[0].org': "org LIKE 'MISP%'",
        'query.AND[1].<unknown>': 'nosuch',
        'query.AND[2].default': "default = 'yes'",
    },
    'event_report_leaves': {
        'query.AND[0].<unknown>': 'nosuch',
        'query.AND[1].name': "name LIKE 'a%b'",
        'query.AND[2].OR[0].eventinfo': 'eventinfo = 5',
        'query.AND[2].OR[1].date': "date > 'soon'",
    },
    'note_leaves': {
        'query.OR[0].created': "created > 'tomorrow'",
        'query.OR[1].note': "note IN ('a', 1)",
        'query.OR[2].locked': "locked = 'yes'",
    },
    'opinion_leaves': {
        'query.AND[0].opinion': "opinion > 'high'",
        'query.AND[1].NOT.comment': "comment LIKE '%a%b%'",
    },
    'relationship_leaves': {
        'query.AND[0].<unknown>': 'related_object',
        'query.AND[1].relationship_type': "relationship_type != ''",
    },
    'collection_leaves': {
        'query.OR[0].element_type': 'element_type = 1',
        'query.OR[1].NOT.AND[0].element_uuid': "element_uuid LIKE 'a%b'",
        'query.OR[1].NOT.AND[1].<unknown>': 'element_count',
    },
    'tag_leaves': {
        'query.AND[0].colour': "colour LIKE '#ff%'",
        'query.AND[1].<unknown>': '`count`',
        'query.AND[2].numerical_value': "numerical_value = 'x'",
    },
    'organisation_leaves': {
        'query.OR[0].<unknown>': 'user_count',
        'query.OR[1].uuid': "uuid LIKE 'a%'",
        'query.OR[2].date_created': "date_created > 'soon'",
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
