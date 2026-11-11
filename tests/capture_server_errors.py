"""Record a MISP 3.0 server's 400 replies to invalid statements, for test_compiler.py.

Usage: MISPQL_URL=https://misp.example MISPQL_KEY=... python tests/capture_server_errors.py
The user needs the Complex query permission or to be a site admin.
"""
import json
import os
from pathlib import Path

import requests

from mispql.compiler import compile_statement
from mispql.parser import parse

STATEMENTS = {
    'attribute_leaves': (
        "SELECT * FROM attributes WHERE nosuch = 1 AND id = 'x' AND type IN ('a', 5)"
        " AND value NOT LIKE 'a%b' AND id LIKE '1%' LIMIT 1"
    ),
    'event_negation': "SELECT * FROM events WHERE event_id > 0 AND (eventinfo = 'x' OR type != 'btc') LIMIT 1",
    'event_negated_tags': "SELECT * FROM events WHERE id = 1 OR tags NOT LIKE 'tlp:%' LIMIT 1",
    'object_not_over_attribute': "SELECT * FROM objects WHERE NOT value = 'x' OR object_name = 'url' LIMIT 1",
    'too_deep': (
        "SELECT * FROM events WHERE ((((id = 1 AND id = 2) OR id = 3) AND id = 4) OR id = 5)"
        " AND id = 6 LIMIT 1"
    ),
}


def main():
    url = os.environ['MISPQL_URL'].rstrip('/')
    headers = {'Authorization': os.environ['MISPQL_KEY'], 'Accept': 'application/json'}
    out = Path(__file__).parent / 'server_errors'
    out.mkdir(exist_ok=True)
    for case, text in STATEMENTS.items():
        plan = compile_statement(parse(text)[0])
        reply = requests.post(url + plan.endpoint, json=plan.body, headers=headers, timeout=60)
        capture = {'statement': text, 'body': plan.body, 'status': reply.status_code, 'reply': reply.json()}
        (out / f'{case}.json').write_text(json.dumps(capture, indent=2) + '\n')
        print(case, reply.status_code)


if __name__ == '__main__':
    main()
