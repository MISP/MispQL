"""Syntax tree of MispQL statements.

Every node carries the character offsets it spans in the statement text. They take no
part in equality, so trees can be compared by structure alone.
"""
from dataclasses import dataclass, field


def _span():
    return field(default=0, compare=False, repr=False)


@dataclass(kw_only=True)
class Node:
    start: int = _span()
    end: int = _span()


@dataclass(kw_only=True)
class Name(Node):
    value: str


@dataclass(kw_only=True)
class Field(Node):
    name: str
    table: str | None = None


@dataclass(kw_only=True)
class Literal(Node):
    value: str | int | bool


@dataclass(kw_only=True)
class Comparison(Node):
    field: Field
    op: str  # = != < > <= >= IN, NOT IN, LIKE, NOT LIKE
    value: Literal | list[Literal]


@dataclass(kw_only=True)
class And(Node):
    items: list


@dataclass(kw_only=True)
class Or(Node):
    items: list


@dataclass(kw_only=True)
class Not(Node):
    item: Node


@dataclass(kw_only=True)
class Star(Node):
    pass


@dataclass(kw_only=True)
class Count(Node):
    pass


@dataclass(kw_only=True)
class OrderItem(Node):
    field: Field
    descending: bool = False


@dataclass(kw_only=True)
class Select(Node):
    columns: Star | Count | list[Field]
    table: Name
    where: Node | None = None
    order: list[OrderItem] = field(default_factory=list)
    limit: Literal | None = None
    offset: Literal | None = None
    format: Name | None = None


@dataclass(kw_only=True)
class Describe(Node):
    table: Name


@dataclass(kw_only=True)
class ShowTables(Node):
    pass


@dataclass(kw_only=True)
class ShowDatabases(Node):
    pass


@dataclass(kw_only=True)
class Use(Node):
    database: Name


@dataclass(kw_only=True)
class Whoami(Node):
    pass
