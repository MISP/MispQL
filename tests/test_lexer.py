import pytest

from mispql.errors import MispQLError, position
from mispql.lexer import tokenize


def kinds_values(text):
    return [(t.kind, t.value) for t in tokenize(text)][:-1]


def test_keywords_are_case_insensitive():
    assert kinds_values('select Select SELECT') == [('KEYWORD', 'SELECT')] * 3


def test_bare_names_are_lowercased_quoted_names_kept():
    assert kinds_values('Type `Type` `a``b` `select`') == [
        ('NAME', 'type'), ('NAME', 'Type'), ('NAME', 'a`b'), ('NAME', 'select'),
    ]


@pytest.mark.parametrize('text, value', [
    ("'abc'", 'abc'),
    ('"abc"', 'abc'),
    ("'it''s'", "it's"),
    ('"say ""hi"""', 'say "hi"'),
    ('"it\'s"', "it's"),
    (r"'C:\Windows\temp\n'", r'C:\Windows\temp\n'),
    ("''", ''),
    ("'two\nlines'", 'two\nlines'),
])
def test_strings(text, value):
    assert kinds_values(text) == [('STRING', value)]


def test_numbers_and_symbols():
    assert kinds_values('id>=-5 <> != <= = ( ) , . * ; <') == [
        ('NAME', 'id'), ('SYMBOL', '>='), ('SYMBOL', '-'), ('INT', 5), ('SYMBOL', '<>'),
        ('SYMBOL', '!='), ('SYMBOL', '<='), ('SYMBOL', '='), ('SYMBOL', '('), ('SYMBOL', ')'),
        ('SYMBOL', ','), ('SYMBOL', '.'), ('SYMBOL', '*'), ('SYMBOL', ';'), ('SYMBOL', '<'),
    ]


def test_comments_are_skipped():
    assert kinds_values("a -- b 'c\n-- whole line\nd--e") == [
        ('NAME', 'a'), ('NAME', 'd'),
    ]


def test_spans_and_eof():
    tokens = tokenize("SELECT 'x' -- tail")
    assert [(t.start, t.end) for t in tokens] == [(0, 6), (7, 10), (10, 10)]
    assert tokens[-1].kind == 'EOF'
    assert tokenize('')[0].kind == 'EOF'


@pytest.mark.parametrize('text, line, column, message, at_end', [
    ("a = 'open", 1, 5, 'string not closed', True),
    ('a = "open', 1, 5, 'string not closed', True),
    ('`open', 1, 1, 'name not closed', True),
    ('``', 1, 1, 'empty name', False),
    ('a ! b', 1, 3, "unexpected character '!': use != or NOT", False),
    ('a = {', 1, 5, "unexpected character '{'", False),
    ('ts >= 7d', 1, 7, "7d must be quoted: '7d'", False),
    ('x = 1.5', 1, 5, 'only whole numbers are supported', False),
    ("a = 1\n  b # c", 2, 5, "unexpected character '#'", False),
])
def test_errors(text, line, column, message, at_end):
    with pytest.raises(MispQLError) as exc:
        tokenize(text)
    assert exc.value.message == message
    assert position(text, exc.value.start) == (line, column)
    assert exc.value.at_end is at_end
