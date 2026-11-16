from dataclasses import dataclass

from mispql.errors import MispQLError

KEYWORDS = frozenset({
    'AND', 'ASC', 'BY', 'COUNT', 'DATABASES', 'DESC', 'DESCRIBE', 'FALSE', 'FORMAT', 'FROM',
    'IN', 'LIKE', 'LIMIT', 'NOT', 'OFFSET', 'OR', 'ORDER', 'SELECT', 'SHOW', 'TABLES', 'TRUE',
    'USE', 'WHERE', 'WHOAMI',
})
SYMBOLS = ('!=', '<>', '<=', '>=', '=', '<', '>', '(', ')', ',', '.', '*', ';', '-')
WORD_CHARS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')


@dataclass
class Token:
    kind: str  # KEYWORD, NAME, STRING, INT, SYMBOL or EOF
    value: object
    start: int
    end: int
    quoted: bool = False

    def describe(self):
        if self.kind == 'EOF':
            return 'end of input'
        if self.kind == 'STRING':
            return f"the string '{self.value}'"
        if self.kind == 'NAME' and self.quoted:
            return f'`{self.value}`'
        if self.kind == 'SYMBOL':
            return f"'{self.value}'"
        return str(self.value)


def tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith('--', i):
            end = text.find('\n', i)
            i = n if end == -1 else end
        elif c in ('"', "'"):
            value, end = _quoted(text, i, c, 'string')
            tokens.append(Token('STRING', value, i, end))
            i = end
        elif c == '`':
            value, end = _quoted(text, i, '`', 'name')
            if not value:
                raise MispQLError('empty name', i, end)
            tokens.append(Token('NAME', value, i, end, quoted=True))
            i = end
        elif c in WORD_CHARS:
            end = i
            while end < n and text[end] in WORD_CHARS:
                end += 1
            tokens.append(_word(text, i, end))
            i = end
        else:
            symbol = next((s for s in SYMBOLS if text.startswith(s, i)), None)
            if symbol is None:
                hint = ': use != or NOT' if c == '!' else ''
                raise MispQLError(f"unexpected character '{c}'{hint}", i)
            tokens.append(Token('SYMBOL', symbol, i, i + len(symbol)))
            i += len(symbol)
    eof = tokens[-1].end if tokens else 0
    tokens.append(Token('EOF', None, eof, eof))
    return tokens


def _quoted(text, start, quote, what):
    """Read a quoted string or name; a doubled quote stands for one."""
    parts = []
    i = start + 1
    while True:
        end = text.find(quote, i)
        if end == -1:
            raise MispQLError(f'{what} not closed', start, len(text), at_end=True)
        parts.append(text[i:end])
        if text.startswith(quote, end + 1):
            parts.append(quote)
            i = end + 2
        else:
            return ''.join(parts), end + 1


def _word(text, start, end):
    word = text[start:end]
    if word[0].isdigit():
        if not word.isdigit():
            raise MispQLError(f"{word} must be quoted: '{word}'", start, end)
        if text.startswith('.', end) and end + 1 < len(text) and text[end + 1].isdigit():
            raise MispQLError('only whole numbers are supported', start, end + 1)
        return Token('INT', int(word), start, end)
    if word.upper() in KEYWORDS:
        return Token('KEYWORD', word.upper(), start, end)
    return Token('NAME', word.lower(), start, end)
