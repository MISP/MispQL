"""Compile parsed MispQL statements into MISP API requests."""
import re
from dataclasses import dataclass, field

from mispql import nodes
from mispql.errors import MispQLError
from mispql.tables import TABLES, names

# Rendered by the tool from the JSON reply. Any other FORMAT word is MISP's returnFormat.
RENDERINGS = ('table', 'flat')


@dataclass(kw_only=True)
class Plan:
    # Path in the request body (query.AND[1].OR[0], order[0]) -> offsets in the statement text.
    spans: dict = field(default_factory=dict, compare=False, repr=False)

    def locate(self, path):
        """Offsets of the text an error path points at, through its longest known prefix."""
        while path:
            if path in self.spans:
                return self.spans[path]
            path = _parent(path)
        return None


@dataclass(kw_only=True)
class SearchPlan(Plan):
    table: str
    endpoint: str
    body: dict
    output: str  # default, table, flat, or verbatim: printed as MISP sends it
    columns: list[str] | None = None
    # When non-zero, fetch `page` and the next one, drop `skip` rows and keep `limit`.
    skip: int = 0


@dataclass(kw_only=True)
class CountPlan(Plan):
    table: str
    endpoint: str
    body: dict
    source: str  # header: X-Result-Count; body: the reply is the number


@dataclass(kw_only=True)
class DescribePlan(Plan):
    table: str
    endpoint: str


@dataclass(kw_only=True)
class ShowTablesPlan(Plan):
    endpoints: dict


def compile_statement(statement):
    if isinstance(statement, nodes.ShowTables):
        return ShowTablesPlan(endpoints={name: table.discovery for name, table in TABLES.items()})
    if isinstance(statement, nodes.Describe):
        table = _table(statement.table)
        return DescribePlan(table=table.name, endpoint=table.discovery)
    return _Select(statement).plan()


def _table(name):
    if name.value not in TABLES:
        raise MispQLError(f'unknown table {name.value}: use {names()}', name.start, name.end)
    return TABLES[name.value]


def _parent(path):
    match = re.search(r'(\.[^.\[]+|\[\d+\])$', path)
    return path[:match.start()] if match else ''


class _Select:
    def __init__(self, stmt):
        self.stmt = stmt
        self.table = _table(stmt.table)
        self.spans = {}

    def plan(self):
        stmt = self.stmt
        if isinstance(stmt.columns, nodes.Count):
            return self.count()
        if stmt.where is None and stmt.limit is None:
            raise MispQLError('add a WHERE or a LIMIT: MISP takes no search without either',
                              stmt.start, stmt.end)
        word = stmt.format.value if stmt.format else None
        output = 'default' if word is None else word if word in RENDERINGS else 'verbatim'
        verbatim = word if output == 'verbatim' else None
        columns = None
        if isinstance(stmt.columns, list):
            columns = [self.field(column) for column in stmt.columns]
        query = None if stmt.where is None else self.node(stmt.where, 'query')
        order = self.order()
        limit, page, skip = self.paging(verbatim)
        if columns and verbatim:
            raise MispQLError(
                f'a column list needs FORMAT table, FORMAT flat or no FORMAT: '
                f'FORMAT {verbatim} is printed as MISP sends it', stmt.format.start, stmt.format.end)
        body = {'returnFormat': verbatim or 'json'}
        if limit:
            body.update(limit=limit, page=page)
        if order:
            body['order'] = order
        if query is not None:
            body['query'] = query
        return SearchPlan(table=self.table.name, endpoint=self.table.search, body=body,
                          output=output, columns=columns, skip=skip, spans=self.spans)

    def count(self):
        stmt = self.stmt
        if stmt.where is None:
            raise MispQLError('COUNT(*) needs a WHERE', stmt.columns.start, stmt.columns.end)
        query = self.node(stmt.where, 'query')
        clauses = (('ORDER BY', stmt.order[0] if stmt.order else None), ('LIMIT', stmt.limit),
                   ('OFFSET', stmt.offset), ('FORMAT', stmt.format))
        for clause, node in clauses:
            if node is not None:
                raise MispQLError(f'COUNT(*) takes no {clause}', node.start, node.end)
        if self.table.count == 'header':
            # metadata: no event, however large, is fetched whole just to read the header.
            body = {'returnFormat': 'json', 'limit': 1, 'page': 1, 'metadata': 1}
        else:
            body = {'returnFormat': 'count'}
        body['query'] = query
        return CountPlan(table=self.table.name, endpoint=self.table.search, body=body,
                         source=self.table.count, spans=self.spans)

    def node(self, node, path):
        self.spans[path] = (node.start, node.end)
        if isinstance(node, (nodes.And, nodes.Or)):
            key = 'AND' if isinstance(node, nodes.And) else 'OR'
            return {key: [self.node(item, f'{path}.{key}[{i}]') for i, item in enumerate(node.items)]}
        if isinstance(node, nodes.Not):
            return {'NOT': self.node(node.item, f'{path}.NOT')}
        name = self.field(node.field)
        if isinstance(node.value, list):
            value = [literal.value for literal in node.value]
        else:
            value = node.value.value
        self.spans[f'{path}.<unknown>'] = (node.field.start, node.field.end)
        return {name: value if node.op == '=' else {node.op: value}}

    def field(self, field):
        if field.table is None or field.table == self.table.name:
            return field.name
        if field.table in TABLES:
            raise MispQLError(f'{field.table} is not the table queried here ({self.table.name})',
                              field.start, field.end)
        # Not a table, so part of the name: meta.synonyms, or Event.Orgc.name as a column.
        return f'{field.table}.{field.name}'

    def order(self):
        table = self.table
        rules = []
        for i, item in enumerate(self.stmt.order):
            if not table.sortable:
                raise MispQLError(f'{table.name} cannot be sorted', item.start, item.end)
            name = self.field(item.field)
            if name not in table.sortable:
                raise MispQLError(
                    f'cannot sort {table.name} by {name}; use {", ".join(table.sortable)}',
                    item.field.start, item.field.end)
            self.spans[f'order[{i}]'] = (item.start, item.end)
            rules.append(f'{table.sortable[name]} {"desc" if item.descending else "asc"}')
        return rules

    def paging(self, verbatim):
        """limit, page and the rows to skip in that page."""
        limit, offset = self.stmt.limit, self.stmt.offset
        if limit is None:
            if offset is not None:
                raise MispQLError('OFFSET needs a LIMIT', offset.start, offset.end)
            return None, None, 0
        if limit.value == 0:
            raise MispQLError('LIMIT must be at least 1', limit.start, limit.end)
        first = offset.value if offset else 0
        skip = first % limit.value
        if skip and verbatim:
            raise MispQLError(
                f'OFFSET must be a multiple of LIMIT with FORMAT {verbatim}, '
                f'which is printed as MISP sends it', offset.start, offset.end)
        return limit.value, first // limit.value + 1, skip
