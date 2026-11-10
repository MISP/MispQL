import pytest

from mispql.errors import MispQLError, position
from mispql.nodes import (
    And, Comparison, Count, Describe, Field, Literal, Name, Not, Or, OrderItem, Select,
    ShowTables, Star,
)
from mispql.parser import parse


def one(text):
    statements = parse(text)
    assert len(statements) == 1
    return statements[0]


def where(condition):
    return one(f'SELECT * FROM attributes WHERE {condition}').where


def cmp(name, op, value):
    if isinstance(value, list):
        value = [Literal(value=v) for v in value]
    else:
        value = Literal(value=value)
    return Comparison(field=Field(name=name), op=op, value=value)


def test_show_tables_and_describe():
    assert parse('SHOW TABLES; describe events; DESC `Objects`') == [
        ShowTables(), Describe(table=Name(value='events')), Describe(table=Name(value='Objects')),
    ]


def test_select_columns():
    assert one('SELECT * FROM attributes').columns == Star()
    assert one('select count(*) from Events').columns == Count()
    assert one('SELECT id, attributes.Type FROM attributes').columns == [
        Field(name='id'), Field(table='attributes', name='type'),
    ]
    assert one('SELECT * FROM `attributes`').table == Name(value='attributes')


@pytest.mark.parametrize('text, op, value', [
    ("type = 'domain'", '=', 'domain'),
    ('type = "domain"', '=', 'domain'),
    ("type != 'domain'", '!=', 'domain'),
    ("type <> 'domain'", '!=', 'domain'),
    ('id > 5', '>', 5),
    ('id < -5', '<', -5),
    ('id >= - 5', '>=', -5),
    ("timestamp <= '7d'", '<=', '7d'),
    ('to_ids = TRUE', '=', True),
    ('to_ids = false', '=', False),
    ("type IN ('a', \"b\")", 'IN', ['a', 'b']),
    ('id NOT IN (1)', 'NOT IN', [1]),
    ("value LIKE '%.example.com'", 'LIKE', '%.example.com'),
    ("tags NOT LIKE 'tlp:%'", 'NOT LIKE', 'tlp:%'),
])
def test_comparisons(text, op, value):
    assert where(text) == cmp(text.split()[0].lower(), op, value)


def test_booleans_stay_booleans():
    assert type(where('to_ids = true').value.value) is bool
    assert type(where('id = 1').value.value) is int


def test_qualified_field():
    assert where("attributes.type = 'x'").field == Field(table='attributes', name='type')
    assert one('SELECT Event.Orgc.name, `Event`.`Orgc` FROM attributes').columns == [
        Field(table='event', name='orgc.name'), Field(table='Event', name='Orgc'),
    ]


def test_precedence():
    a, b, c = cmp('a', '=', 1), cmp('b', '=', 2), cmp('c', '=', 3)
    assert where('a = 1 OR b = 2 AND c = 3') == Or(items=[a, And(items=[b, c])])
    assert where('NOT a = 1 AND b = 2') == And(items=[Not(item=a), b])
    assert where('NOT (a = 1 OR b = 2)') == Not(item=Or(items=[a, b]))
    assert where('NOT NOT a = 1') == Not(item=Not(item=a))
    assert where('NOT a IN (1)') == Not(item=cmp('a', 'IN', [1]))


def test_chains_are_flattened():
    a, b, c, d = (cmp(x, '=', 1) for x in 'abcd')
    assert where('a = 1 AND b = 1 AND c = 1') == And(items=[a, b, c])
    assert where('(a = 1 AND b = 1) AND c = 1') == And(items=[a, b, c])
    assert where('a = 1 AND (b = 1 AND c = 1)') == And(items=[a, b, c])
    assert where('(a = 1 OR b = 1) OR (c = 1 OR d = 1)') == Or(items=[a, b, c, d])
    assert where('(a = 1 OR b = 1) AND c = 1') == And(items=[Or(items=[a, b]), c])
    assert where('((a = 1))') == a


def test_order_limit_offset_format():
    stmt = one('SELECT * FROM events ORDER BY eventinfo DESC, id asc, date LIMIT 20 OFFSET 40 FORMAT csv')
    assert stmt.order == [
        OrderItem(field=Field(name='eventinfo'), descending=True),
        OrderItem(field=Field(name='id')),
        OrderItem(field=Field(name='date')),
    ]
    assert (stmt.limit, stmt.offset, stmt.format) == (Literal(value=20), Literal(value=40), Name(value='csv'))


@pytest.mark.parametrize('text, value', [
    ('FORMAT stix-json', 'stix-json'),
    ('FORMAT attack-sightings', 'attack-sightings'),
    ('FORMAT STIX2', 'stix2'),
    ('FORMAT count', 'count'),
    ("FORMAT 'yara-json'", 'yara-json'),
    ('FORMAT `Json`', 'Json'),
])
def test_format_names(text, value):
    assert one(f'SELECT * FROM attributes LIMIT 1 {text}').format == Name(value=value)


def test_full_example():
    stmt = one(
        "SELECT * FROM events WHERE date >= '30d' AND (type = 'btc' OR object_name = 'bank-account')\n"
        "  AND tags NOT IN ('tlp:red') ORDER BY eventinfo DESC LIMIT 20 OFFSET 40;"
    )
    assert stmt == Select(
        columns=Star(),
        table=Name(value='events'),
        where=And(items=[
            cmp('date', '>=', '30d'),
            Or(items=[cmp('type', '=', 'btc'), cmp('object_name', '=', 'bank-account')]),
            cmp('tags', 'NOT IN', ['tlp:red']),
        ]),
        order=[OrderItem(field=Field(name='eventinfo'), descending=True)],
        limit=Literal(value=20),
        offset=Literal(value=40),
    )


def test_scripts():
    text = """
    -- leading comment
    SELECT * FROM attributes LIMIT 1;;
    SHOW TABLES ;
    DESCRIBE events  -- no final semicolon
    """
    assert [type(s) for s in parse(text)] == [Select, ShowTables, Describe]
    assert parse('') == []
    assert parse(' ; -- nothing\n;') == []


def test_spans():
    text = "SELECT * FROM attributes WHERE id > 5 AND NOT (type = 'x' OR tags LIKE 'a%') LIMIT 3;"
    stmt = one(text)

    def src(node):
        return text[node.start:node.end]

    assert src(stmt) == text[:-1]
    assert src(stmt.table) == 'attributes'
    assert src(stmt.where) == "id > 5 AND NOT (type = 'x' OR tags LIKE 'a%')"
    first, negation = stmt.where.items
    assert src(first) == 'id > 5'
    assert src(first.field) == 'id'
    assert src(first.value) == '5'
    assert src(negation) == "NOT (type = 'x' OR tags LIKE 'a%')"
    assert src(negation.item) == "type = 'x' OR tags LIKE 'a%'"
    assert src(negation.item.items[1]) == "tags LIKE 'a%'"
    assert src(stmt.limit) == '3'

    text = 'SELECT id FROM attributes WHERE id IN (1, -2) ORDER BY id DESC FORMAT stix-json'
    stmt = one(text)
    assert src(stmt.where) == 'id IN (1, -2)'
    assert src(stmt.where.value[1]) == '-2'
    assert src(stmt.order[0]) == 'id DESC'
    assert src(stmt.format) == 'stix-json'
    assert src(stmt) == text


@pytest.mark.parametrize('text, line, column, message, at_end', [
    ('SELECT * FROM events WHERE type = btc', 1, 35,
     "expected a value ('string', number, TRUE or FALSE), found btc (strings need quotes)", False),
    ('SELECT * FROM', 1, 14, 'expected a table name, found end of input', True),
    ('SELECT * FROM events WHERE', 1, 27, 'expected a field name, found end of input', True),
    ("SELECT * FROM events WHERE type 'x'", 1, 33,
     "expected an operator after type (=, !=, <, >, <=, >=, IN, NOT IN, LIKE, NOT LIKE), "
     "found the string 'x'", False),
    ('SELECT * FROM events WHERE type NOT = 1', 1, 37, "expected IN or LIKE after NOT, found '='", False),
    ('SELECT * FROM events WHERE type IN ()', 1, 37, "expected at least one value in IN (...), found ')'", False),
    ("SELECT * FROM events WHERE type IN ('a' 'b')", 1, 41, "expected ',' or ')', found the string 'b'", False),
    ('SELECT * FROM events WHERE type LIKE 5', 1, 38, "expected a 'quoted pattern' after LIKE, found 5", False),
    ('SELECT * FROM events WHERE (a = 1', 1, 34, "expected ')', found end of input", True),
    ('SELECT * FROM events WHERE a = 1 b = 2', 1, 34,
     "expected AND, OR, ORDER BY, LIMIT, OFFSET, FORMAT or ';', found b", False),
    ('SELECT COUNT(id) FROM events', 1, 14, "expected '*' (COUNT takes only *), found id", False),
    ('SELECT FROM events', 1, 8, 'expected *, COUNT(*) or field names, found FROM', False),
    ("SELECT * FROM events LIMIT 'x'", 1, 28, "expected a whole number after LIMIT, found the string 'x'", False),
    ('SELECT * FROM events LIMIT 10 WHERE a = 1', 1, 31, "expected OFFSET, FORMAT or ';', found WHERE", False),
    ('SELECT * FROM events ORDER id', 1, 28, 'expected BY, found id', False),
    ('SELECT * FROM select', 1, 15,
     'expected a table name, found SELECT (quote it with backticks to use it as a name)', False),
    ('DELETE FROM events', 1, 1, 'expected SELECT, DESCRIBE or SHOW TABLES, found delete', False),
    ('SHOW events', 1, 6, 'expected TABLES, found events', False),
    ('DESCRIBE events extra', 1, 17, "expected ';', found extra", False),
    ("SELECT * FROM events;\nSELECT *\n  FROM attributes\n  WHERE id = 'x' OR", 4, 20,
     'expected a field name, found end of input', True),
    ('SELECT * FROM events WHERE id >= 7d', 1, 34, "7d must be quoted: '7d'", False),
    ('SELECT * FROM events LIMIT 1 FORMAT stix -json', 1, 42, "expected ';', found '-'", False),
])
def test_errors(text, line, column, message, at_end):
    with pytest.raises(MispQLError) as exc:
        parse(text)
    assert exc.value.message == message
    assert position(text, exc.value.start) == (line, column)
    assert exc.value.at_end is at_end


def test_error_rendering():
    text = 'SELECT *\nFROM events WHERE type = btc;'
    with pytest.raises(MispQLError) as exc:
        parse(text)
    assert exc.value.render(text) == (
        "ERROR 2:26: expected a value ('string', number, TRUE or FALSE), found btc (strings need quotes)\n"
        'FROM events WHERE type = btc;\n'
        '                         ^^^'
    )


def test_error_rendering_at_end():
    text = 'SELECT * FROM'
    with pytest.raises(MispQLError) as exc:
        parse(text)
    assert exc.value.render(text).splitlines()[1:] == ['SELECT * FROM', '             ^']
