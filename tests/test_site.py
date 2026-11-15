"""Every example on the site shows what the tool prints for it.

MISPQL_UPDATE_SITE=1 rewrites the blocks after each statement; review the diff before committing.
"""
import contextlib
import html
import io
import json
import os
import re
from pathlib import Path

import pytest

from mispql import cli
from mispql.client import QueryErrors
from mispql.compiler import compile_statement
from mispql.output import write
from mispql.parser import parse
from mispql.tables import TABLES

HERE = Path(__file__).parent
SITE = HERE.parent / 'site'
PAGES = ('index.html', 'guide.html')

EXAMPLE = re.compile(
    r'<div class="example"(?: data-(sample|capture|error)="([\w-]+)")?>\s*'
    r'<pre class="mql"><code>(.*?)</code></pre>(.*?)</div>', re.S)
BLOCK = re.compile(r'<pre class="(output|request|error)"><code>(.*?)</code></pre>', re.S)
KINDS = ('output', 'request', 'error')

# Rows for the rendered samples, shaped as the client returns them.
SAMPLES = {
    'attributes-table': [
        {'id': '913204', 'event_id': '4711', 'category': 'Network activity', 'type': 'domain',
         'value': 'update-check.example.net', 'to_ids': True, 'timestamp': '1794652210',
         'Tag': [{'name': 'tlp:amber'}, {'name': 'kill-chain:Command and Control'}]},
        {'id': '913205', 'event_id': '4711', 'category': 'Network activity', 'type': 'ip-dst',
         'value': '198.51.100.23', 'to_ids': True, 'timestamp': '1794652210',
         'Tag': [{'name': 'tlp:amber'}]},
        {'id': '913388', 'event_id': '4718', 'category': 'Network activity', 'type': 'domain',
         'value': 'cdn-sync.example.org', 'to_ids': False, 'timestamp': '1794689931', 'Tag': []},
        {'id': '913390', 'event_id': '4718', 'category': 'Network activity', 'type': 'ip-dst',
         'value': '203.0.113.7', 'to_ids': True, 'timestamp': '1794689931',
         'Tag': [{'name': 'tlp:green'}]},
        {'id': '913412', 'event_id': '4720', 'category': 'Network activity', 'type': 'domain',
         'value': 'login-portal.example.com', 'to_ids': True, 'timestamp': '1794702045',
         'Tag': [{'name': 'tlp:clear'}, {'name': 'phishing:techniques="spear-phishing"'}]},
    ],
    'events-table': [
        {'id': '4720', 'date': '2026-11-14', 'info': 'Credential phishing against webmail users',
         'Orgc': {'id': '1', 'name': 'CIRCL'}, 'threat_level_id': '2', 'analysis': '1',
         'published': True, 'attribute_count': '37', 'Tag': [{'name': 'tlp:clear'}]},
        {'id': '4718', 'date': '2026-11-13', 'info': 'Loader infrastructure seen in honeypot logs',
         'Orgc': {'id': '7', 'name': 'ORG-B'}, 'threat_level_id': '3', 'analysis': '2',
         'published': True, 'attribute_count': '1204', 'Tag': [{'name': 'tlp:clear'}]},
        {'id': '4711', 'date': '2026-11-12', 'info': 'Command and control domains of a banking trojan',
         'Orgc': {'id': '1', 'name': 'CIRCL'}, 'threat_level_id': '1', 'analysis': '2',
         'published': True, 'attribute_count': '58',
         'Tag': [{'name': 'tlp:clear'}, {'name': 'misp-galaxy:banker="Zeus"'}]},
    ],
}
SAMPLES['events-flat'] = SAMPLES['events-table']
SAMPLES['events-columns'] = SAMPLES['events-table'][:2]


def examples(page):
    return list(EXAMPLE.finditer((SITE / page).read_text()))


def dry_run(text):
    """What mispql --dry-run -e TEXT prints: requests on stdout, errors on stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        cli.run(text, get_client=None, dry_run=True)
    return {'request': out.getvalue(), 'error': err.getvalue()}


def expected(kind, name, text):
    blocks = dry_run(text)
    if kind == 'sample':
        plan = compile_statement(parse(text)[0])
        out = io.BytesIO()
        assert write(plan, SAMPLES[name], out) == [], name
        blocks['output'] = out.getvalue().decode()
    elif kind == 'capture':
        # A reply MISP sent, as tests/capture_server_errors.py recorded it.
        capture = json.loads((HERE / 'server_errors' / f'{name}.json').read_text())
        assert capture['statement'] == text, name
        statement = parse(text)[0]
        located = QueryErrors(capture['reply']['errors']).located(compile_statement(statement), statement)
        blocks['error'] = '\n'.join(error.render(text) for error in located) + '\n'
    assert bool(blocks['error']) == (kind in ('capture', 'error')), text
    return {kind: value.rstrip('\n') for kind, value in blocks.items() if value}


def shown(blocks):
    return {kind: html.unescape(text) for kind, text in BLOCK.findall(blocks)}


def render(blocks):
    return ''.join(f'\n<pre class="{kind}"><code>{html.escape(blocks[kind], quote=False)}</code></pre>'
                   for kind in KINDS if kind in blocks) + '\n'


@pytest.mark.parametrize('page', PAGES)
def test_examples_show_what_the_tool_prints(page):
    path = SITE / page
    source = path.read_text()
    found = examples(page)
    assert len(found) == source.count('<pre class="mql">'), 'an example the pattern does not match'
    if os.environ.get('MISPQL_UPDATE_SITE') == '1':
        parts, last = [], 0
        for m in found:
            parts += [source[last:m.start(4)], render(expected(m[1], m[2], html.unescape(m[3])))]
            last = m.end(4)
        path.write_text(''.join(parts) + source[last:])
        found = examples(page)
    for match in found:
        kind, name, text, blocks = match.groups()
        assert shown(blocks) == expected(kind, name, html.unescape(text)), html.unescape(text)


def test_guide_table_facts_match_the_registry():
    guide = (SITE / 'guide.html').read_text()
    facts = {}
    for table, body in re.findall(r'<dl class="facts" data-table="(\w+)">(.*?)</dl>', guide, re.S):
        facts[table] = {fact: re.findall(r'<code>(.*?)</code>', text)
                        for fact, text in re.findall(r'<dd data-fact="(\w+)">(.*?)</dd>', body, re.S)}
    assert list(facts) == list(TABLES)
    for name, table in TABLES.items():
        got = facts[name]
        assert got['endpoint'] == [f'POST {table.search}'], name
        assert got['sortable'] == list(table.sortable), name
        assert got['columns'] == list(table.columns), name
        pairs = got.get('aliases', [])
        assert dict(zip(pairs[::2], pairs[1::2])) == table.aliases, name


@pytest.mark.parametrize('page', PAGES)
def test_links_resolve(page):
    source = (SITE / page).read_text()
    ids = set(re.findall(r'\bid="([^"]+)"', source))
    for target in re.findall(r'(?:href|src|srcset)="([^"]+)"', source):
        if target.startswith('https://'):
            continue
        name, _, anchor = target.partition('#')
        if name:
            assert (SITE / name).is_file(), target
        if anchor:
            other = ids if not name else set(re.findall(r'\bid="([^"]+)"', (SITE / name).read_text()))
            assert anchor in other, target
