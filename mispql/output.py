"""Print MISP's answer the way the statement asked for it."""
import csv
import io
import json

from mispql.client import chunks
from mispql.compiler import CountPlan, DescribePlan, ShowTablesPlan
from mispql.tables import TABLES

CELL_WIDTH = 60


def write(plan, result, out):
    """Write `result` of `plan` to the binary stream `out`; returns warnings for stderr."""
    if isinstance(plan, CountPlan):
        out.write(f'{result}\n'.encode())
        return []
    if isinstance(plan, DescribePlan):
        renderer = _describe
    elif isinstance(plan, ShowTablesPlan):
        renderer = _show_tables
    elif plan.output == 'verbatim':
        for chunk in chunks(result):
            out.write(chunk)
        return []
    else:
        renderer = _RENDERERS[plan.output]
    text = io.TextIOWrapper(out, encoding='utf-8', newline='', write_through=True)
    try:
        return renderer(plan, result, text)
    finally:
        text.detach()


def flatten(row, prefix=''):
    """Nested objects as dotted keys, Tag as a list of names; other lists are left out."""
    flat = {}
    for key, value in row.items():
        name = prefix + key
        if isinstance(value, dict):
            flat.update(flatten(value, f'{name}.'))
        elif isinstance(value, list):
            if key == 'Tag':
                flat[name] = [tag.get('name') for tag in value if isinstance(tag, dict)]
            elif value and not any(isinstance(item, (dict, list)) for item in value):
                flat[name] = value
            # Attribute, Object, Galaxy, ...: an event can hold a million of them.
        else:
            flat[name] = value
    return flat


def _columns(flat_rows, wanted):
    """The wanted columns as the rows spell them, matched without regard to case, and warnings."""
    spelling = {}
    for row in flat_rows:
        for key in row:
            spelling.setdefault(key.lower(), key)
    columns = [spelling.get(name.lower(), name) for name in wanted]
    warnings = []
    if flat_rows:
        warnings = [f'no row has a column {name}' for name in wanted if name.lower() not in spelling]
    return columns, warnings


def _cells(flat_row, columns):
    by_name = {key.lower(): value for key, value in flat_row.items()}
    return [by_name.get(column.lower()) for column in columns]


def _all_columns(flat_rows):
    seen = {}
    for row in flat_rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def _text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return ','.join(_text(item) for item in value)
    return str(value)


def _default(plan, rows, out):
    if plan.columns is None:
        json.dump(rows, out, indent=2, ensure_ascii=False)
        out.write('\n')
        return []
    flat_rows = [flatten(row) for row in rows]
    columns, warnings = _columns(flat_rows, plan.columns)
    projected = [dict(zip(columns, _cells(row, columns))) for row in flat_rows]
    json.dump(projected, out, indent=2, ensure_ascii=False)
    out.write('\n')
    return warnings


def _flat(plan, rows, out):
    flat_rows = [flatten(row) for row in rows]
    if plan.columns is None:
        columns, warnings = _all_columns(flat_rows), []
    else:
        columns, warnings = _columns(flat_rows, plan.columns)
    if not columns:
        return warnings
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(columns)
    for row in flat_rows:
        writer.writerow([_text(cell) for cell in _cells(row, columns)])
    return warnings


def _table(plan, rows, out):
    flat_rows = [flatten(row) for row in rows]
    columns, warnings = _columns(flat_rows, plan.columns or TABLES[plan.table].columns)
    if plan.columns is None:
        warnings = []
    _box(columns, [[_text(cell) for cell in _cells(row, columns)] for row in flat_rows], out)
    return warnings


def _describe(plan, found, out):
    fields = found['fields']
    _box(['field', 'type', 'operators', 'level'],
         [[name, _text(info.get('type')), ' '.join(info.get('operators', [])), _text(info.get('level'))]
          for name, info in fields.items()], out, 'field')
    out.write(f'ORDER BY: {", ".join(TABLES[plan.table].sortable) or "none"}\n')
    out.write(f'limits: {", ".join(f"{name} {value}" for name, value in found["limits"].items())}\n')
    return []


def _show_tables(plan, tables, out):
    _box(['table'], [[table] for table in tables], out, 'table')
    return []


def _box(columns, rows, out, noun='row'):
    lines = [[_cut(cell) for cell in row] for row in rows]
    widths = [max([len(column)] + [len(line[i]) for line in lines]) for i, column in enumerate(columns)]
    rule = '+' + '+'.join('-' * (width + 2) for width in widths) + '+\n'
    out.write(rule)
    out.write(_table_line(columns, widths))
    out.write(rule)
    for line in lines:
        out.write(_table_line(line, widths))
    if lines:
        out.write(rule)
    out.write(f'{len(lines)} {noun}{"" if len(lines) == 1 else "s"}\n')


def _cut(text):
    text = text.replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t')
    return text if len(text) <= CELL_WIDTH else text[:CELL_WIDTH - 1] + '…'


def _table_line(cells, widths):
    return '| ' + ' | '.join(cell.ljust(width) for cell, width in zip(cells, widths)) + ' |\n'


_RENDERERS = {'default': _default, 'flat': _flat, 'table': _table}
