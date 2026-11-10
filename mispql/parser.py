from mispql import nodes
from mispql.errors import MispQLError
from mispql.lexer import tokenize

COMPARISONS = {'=': '=', '!=': '!=', '<>': '!=', '<': '<', '>': '>', '<=': '<=', '>=': '>='}


def parse(text):
    """Parse a script into its statements; raises MispQLError at the first error."""
    return _Parser(text).script()


def _one_of(items):
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


class _Parser:
    def __init__(self, text):
        self.text = text
        self.tokens = tokenize(text)
        self.pos = 0

    @property
    def tok(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.tok
        if tok.kind != 'EOF':
            self.pos += 1
        return tok

    def at(self, kind, value=None):
        return self.tok.kind == kind and (value is None or self.tok.value == value)

    def accept(self, kind, value=None):
        return self.advance() if self.at(kind, value) else None

    def keyword(self, word):
        return self.accept('KEYWORD', word)

    def symbol(self, symbol):
        return self.accept('SYMBOL', symbol)

    def expect(self, kind, value, expected):
        tok = self.accept(kind, value)
        if tok is None:
            self.fail(expected)
        return tok

    def fail(self, expected, hint=''):
        tok = self.tok
        raise MispQLError(
            f'expected {expected}, found {tok.describe()}{hint}',
            tok.start, tok.end, at_end=tok.kind == 'EOF',
        )

    def last_end(self):
        return self.tokens[self.pos - 1].end

    def script(self):
        statements = []
        while True:
            while self.symbol(';'):
                pass
            if self.at('EOF'):
                return statements
            statements.append(self.statement())
            if not (self.at('SYMBOL', ';') or self.at('EOF')):
                self.fail("';'")

    def statement(self):
        start = self.tok.start
        if self.keyword('SELECT'):
            return self.select(start)
        if self.keyword('DESCRIBE') or self.keyword('DESC'):
            table = self.name('a table name')
            return nodes.Describe(table=table, start=start, end=table.end)
        if self.keyword('SHOW'):
            end = self.expect('KEYWORD', 'TABLES', 'TABLES').end
            return nodes.ShowTables(start=start, end=end)
        self.fail('SELECT, DESCRIBE or SHOW TABLES')

    def select(self, start):
        columns = self.columns()
        self.expect('KEYWORD', 'FROM', 'FROM')
        stmt = nodes.Select(columns=columns, table=self.name('a table name'), start=start)
        following = ['WHERE', 'ORDER BY', 'LIMIT', 'OFFSET', 'FORMAT']
        if self.keyword('WHERE'):
            stmt.where = self.expr()
            following = ['AND', 'OR', 'ORDER BY', 'LIMIT', 'OFFSET', 'FORMAT']
        if self.keyword('ORDER'):
            self.expect('KEYWORD', 'BY', 'BY')
            stmt.order.append(self.order_item())
            while self.symbol(','):
                stmt.order.append(self.order_item())
            following = ["','", 'LIMIT', 'OFFSET', 'FORMAT']
        if self.keyword('LIMIT'):
            stmt.limit = self.whole_number('LIMIT')
            following = ['OFFSET', 'FORMAT']
        if self.keyword('OFFSET'):
            stmt.offset = self.whole_number('OFFSET')
            following = ['FORMAT']
        if self.keyword('FORMAT'):
            stmt.format = self.format_name()
            following = []
        stmt.end = self.last_end()
        if not (self.at('SYMBOL', ';') or self.at('EOF')):
            self.fail(_one_of(following + ["';'"]))
        return stmt

    def columns(self):
        tok = self.tok
        if self.symbol('*'):
            return nodes.Star(start=tok.start, end=tok.end)
        if self.keyword('COUNT'):
            self.expect('SYMBOL', '(', "'('")
            self.expect('SYMBOL', '*', "'*' (COUNT takes only *)")
            end = self.expect('SYMBOL', ')', "')'").end
            return nodes.Count(start=tok.start, end=end)
        if self.at('NAME'):
            fields = [self.field()]
            while self.symbol(','):
                fields.append(self.field())
            return fields
        self.fail('*, COUNT(*) or field names')

    def name(self, expected):
        tok = self.tok
        if tok.kind != 'NAME':
            hint = ' (quote it with backticks to use it as a name)' if tok.kind == 'KEYWORD' else ''
            self.fail(expected, hint)
        self.advance()
        return nodes.Name(value=tok.value, start=tok.start, end=tok.end)

    def field(self):
        first = self.name('a field name')
        if not self.symbol('.'):
            return nodes.Field(name=first.value, start=first.start, end=first.end)
        second = self.name('a field name')
        return nodes.Field(table=first.value, name=second.value, start=first.start, end=second.end)

    def order_item(self):
        field = self.field()
        item = nodes.OrderItem(field=field, start=field.start, end=field.end)
        direction = self.keyword('DESC') or self.keyword('ASC')
        if direction:
            item.descending = direction.value == 'DESC'
            item.end = direction.end
        return item

    def whole_number(self, clause):
        tok = self.tok
        if tok.kind != 'INT':
            self.fail(f'a whole number after {clause}')
        self.advance()
        return nodes.Literal(value=tok.value, start=tok.start, end=tok.end)

    def format_name(self):
        first = self.tok
        if first.kind == 'STRING' or (first.kind == 'NAME' and first.quoted):
            self.advance()
            return nodes.Name(value=first.value, start=first.start, end=first.end)
        if first.kind not in ('NAME', 'KEYWORD'):
            self.fail('a format name')
        self.advance()
        end = first.end
        # Format names may hold hyphens (stix-json): glue the pieces written without spaces.
        while self.at('SYMBOL', '-') and self.tok.start == end:
            piece = self.tokens[self.pos + 1]
            if piece.kind not in ('NAME', 'KEYWORD', 'INT') or piece.quoted or piece.start != self.tok.end:
                break
            self.advance()
            end = self.advance().end
        return nodes.Name(value=self.text[first.start:end].lower(), start=first.start, end=end)

    def expr(self):
        return self.chain(nodes.Or, 'OR', self.and_expr)

    def and_expr(self):
        return self.chain(nodes.And, 'AND', self.not_expr)

    def chain(self, cls, word, operand):
        start = self.tok.start
        items = [operand()]
        while self.keyword(word):
            items.append(operand())
        if len(items) == 1:
            return items[0]
        flat = []
        for item in items:
            flat.extend(item.items if isinstance(item, cls) else [item])
        return cls(items=flat, start=start, end=self.last_end())

    def not_expr(self):
        start = self.tok.start
        if self.keyword('NOT'):
            item = self.not_expr()
            return nodes.Not(item=item, start=start, end=self.last_end())
        if self.symbol('('):
            inner = self.expr()
            self.expect('SYMBOL', ')', "')'")
            return inner
        return self.predicate()

    def predicate(self):
        field = self.field()
        tok = self.tok
        if tok.kind == 'SYMBOL' and tok.value in COMPARISONS:
            self.advance()
            return self.comparison(field, COMPARISONS[tok.value], self.literal())
        negated = self.keyword('NOT') is not None
        if self.keyword('IN'):
            return self.comparison(field, 'NOT IN' if negated else 'IN', self.literal_list())
        if self.keyword('LIKE'):
            return self.comparison(field, 'NOT LIKE' if negated else 'LIKE', self.pattern())
        if negated:
            self.fail('IN or LIKE after NOT')
        self.fail(f'an operator after {field.name} (=, !=, <, >, <=, >=, IN, NOT IN, LIKE, NOT LIKE)')

    def comparison(self, field, op, value):
        return nodes.Comparison(field=field, op=op, value=value, start=field.start, end=self.last_end())

    def literal(self):
        tok = self.tok
        if tok.kind in ('STRING', 'INT'):
            self.advance()
            return nodes.Literal(value=tok.value, start=tok.start, end=tok.end)
        if tok.kind == 'KEYWORD' and tok.value in ('TRUE', 'FALSE'):
            self.advance()
            return nodes.Literal(value=tok.value == 'TRUE', start=tok.start, end=tok.end)
        if tok.kind == 'SYMBOL' and tok.value == '-' and self.tokens[self.pos + 1].kind == 'INT':
            self.advance()
            number = self.advance()
            return nodes.Literal(value=-number.value, start=tok.start, end=number.end)
        hint = ' (strings need quotes)' if tok.kind == 'NAME' and not tok.quoted else ''
        self.fail("a value ('string', number, TRUE or FALSE)", hint)

    def literal_list(self):
        self.expect('SYMBOL', '(', "'(' after IN")
        if self.at('SYMBOL', ')'):
            self.fail('at least one value in IN (...)')
        values = [self.literal()]
        while self.symbol(','):
            values.append(self.literal())
        self.expect('SYMBOL', ')', "',' or ')'")
        return values

    def pattern(self):
        tok = self.tok
        if tok.kind != 'STRING':
            self.fail("a 'quoted pattern' after LIKE")
        self.advance()
        return nodes.Literal(value=tok.value, start=tok.start, end=tok.end)
