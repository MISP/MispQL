# mispql

mispql is a command-line client for [MISP](https://www.misp-project.org/) that
lets you query your threat intelligence in **MispQL**, an SQL-like language.
Ask MISP a question the way you would ask a database:

```sql
SELECT id, event_id, type, value, Tag FROM attributes
WHERE type IN ('domain', 'ip-dst') AND timestamp >= '7d' AND tags != 'tlp:red'
LIMIT 5 FORMAT table;
```

```
+--------+----------+--------+--------------------------+------------------------------------------------+
| id     | event_id | type   | value                    | Tag                                            |
+--------+----------+--------+--------------------------+------------------------------------------------+
| 913204 | 4711     | domain | update-check.example.net | tlp:amber,kill-chain:Command and Control       |
| 913205 | 4711     | ip-dst | 198.51.100.23            | tlp:amber                                      |
| 913388 | 4718     | domain | cdn-sync.example.org     |                                                |
| 913390 | 4718     | ip-dst | 203.0.113.7              | tlp:green                                      |
| 913412 | 4720     | domain | login-portal.example.com | tlp:clear,phishing:techniques="spear-phishing" |
+--------+----------+--------+--------------------------+------------------------------------------------+
5 rows
```

mispql checks the statement, compiles it into one request to one of MISP's
restSearch endpoints and sends it with your API key. MISP evaluates the
conditions as the user the key belongs to, so a query sees exactly what that
user may see, and never more. mispql only searches: nothing is ever written to
MISP.

Use it interactively, at a prompt with completion and history, or from shell
scripts, with statements on the command line, in a file or on a pipe. Answers
come back the way an SQL client prints them (a text table), as CSV or JSON
rows, or in any of the export formats MISP itself offers for the table queried:
STIX 2, CSV, text, XLSX, Suricata rules and so on.

mispql needs a **MISP 3.0** server.

## Why MispQL

### Conditions beyond restSearch's parameters

MISP's classic restSearch filters are a set of parameters, each taking a value
or a list of values, which must all match; only a few time fields take a range.
A MispQL `WHERE` is a full boolean expression:

- `AND`, `OR`, `NOT` and parentheses, nested, across any fields of the table:
  `(type = 'btc' OR object_name = 'bank-account')` is one search;
- comparisons, `<`, `>`, `<=` and `>=`, on ids, numbers, timestamps, dates and
  datetimes: `id > 50000`, `opinion < 50`, `date >= '2026-01-01'`;
- `=`, `!=` (or `<>`), `IN`, `NOT IN`, and `LIKE` / `NOT LIKE` with `%`
  wildcards;
- relative times: `timestamp >= '7d'`, `publish_timestamp >= '12h'`.

The conditions are evaluated by MISP, not filtered on your machine: mispql
compiles them into the `query` that MISP 3.0 accepts on restSearch, so only the
matching rows are sent, and MISP's access rules and limits apply as on any API
call. This statement:

```sql
SELECT * FROM attributes
WHERE category = 'Network activity' AND (type = 'domain' OR type IN ('ip-src', 'ip-dst'))
  AND id > 50000
LIMIT 100;
```

is this one request (shortened from what `mispql --dry-run` prints):

```
POST /attributes/restSearch
{
  "returnFormat": "json",
  "limit": 100,
  "page": 1,
  "query": {
    "AND": [
      { "category": "Network activity" },
      { "OR": [
          { "type": "domain" },
          { "type": { "IN": ["ip-src", "ip-dst"] } }
      ] },
      { "id": { ">": 50000 } }
    ]
  }
}
```

### MISP's internals, abstracted

You query records, not endpoints and their parameters:

- **Eleven tables**, one per kind of record: `attributes`, `events`,
  `objects`, `galaxy_clusters`, `event_reports`, `notes`, `opinions`,
  `relationships`, `collections`, `tags` and `organisations`. mispql knows
  which endpoint serves each one (`notes` is `/analyst_data/restSearch/Note`).
- **Related records by name.** On `attributes`, `eventinfo`, `org`,
  `published` and `event_timestamp` are conditions on the attribute's event. On
  `events`, `type` and `value` select the events holding a matching attribute.
  `tags` matches a tag on the record or on its event. Galaxy clusters are
  searched by `galaxy_name`, or by their meta values as `meta.synonyms`.
- **Columns** reach into nested data with dots (`Event.info`,
  `Event.Orgc.name`), and `Tag` prints as one cell of tag names.
- **SQL paging and sorting.** `LIMIT` and `OFFSET` become MISP's `limit` and
  `page`. `ORDER BY` names are checked before anything is sent, where MISP
  would quietly ignore a sort field it does not know.
- **`COUNT(*)`** is counted by the server, and no rows are fetched.
- **Discovery.** `SHOW TABLES` lists the tables you may query; `DESCRIBE
  events` asks the server for a table's fields with each one's type and
  operators, the names `ORDER BY` takes and the limits in effect.

### Mistakes are shown where they are

The whole statement, or the whole script, is checked before anything is sent,
and an error points at the text it is about. What only the server can check
(field names, the operators each field takes, types) comes back from MISP as an
error per condition, placed on the statement the same way:

```
$ mispql -e "SELECT * FROM attributes WHERE type = domain LIMIT 10"
ERROR 1:39: expected a value ('string', number, TRUE or FALSE), found domain (strings need quotes)
SELECT * FROM attributes WHERE type = domain LIMIT 10
                                      ^^^^^^
```

## Requirements

- A MISP 3.0 server.
- A role with the **Complex query** permission, or a site administrator. It is
  off for every role until an administrator turns it on. It is needed for every
  statement with a `WHERE`, for `SHOW TABLES` and `DESCRIBE`, and for any
  search of the tables other than attributes, events, objects and galaxy
  clusters; without it MISP answers 403.
- Python 3.11 or later.

## Install

```
python3 -m venv .venv
.venv/bin/pip install .
```

This puts `mispql` in `.venv/bin`.

## Configuration

mispql reads the first config file it finds: the one given with `--config`,
else `$MISPQL_CONFIG`, else `config.toml` next to the package, else
`~/.config/mispql/config.toml`. Each profile is a TOML table; `[default]` is
used unless `--profile` names another.

```toml
[default]
url = "https://misp.example.org"
key = "..."            # chmod 600 the file
verify_ssl = true      # or the path of a CA bundle
timeout = 300          # seconds to wait for an answer
```

`MISPQL_URL` and `MISPQL_KEY` override the file, and `--key-file PATH` reads
the key from a file of its own. The key is never a command-line argument, where
other users of the machine could see it. Without a key, mispql asks for one
when it runs at a terminal. A config file holding a key that others can read
gets a warning.

## Several MISPs and accounts: databases

Each profile of the config file is a **database**: one MISP URL and one API
key. Add a profile for each MISP you query, and for each account on the same
MISP whose view you want, such as your own and a read-only one, to check what
each of them sees:

```toml
[default]              # your account
url = "https://misp.example.org"
key = "..."

[readonly]             # another account on the same MISP
url = "https://misp.example.org"
key = "..."

[test]                 # another MISP
url = "https://misp-test.example.org"
key = "..."
verify_ssl = "/etc/ssl/certs/misp-test-ca.pem"
```

`SHOW DATABASES` lists them and asks each MISP whose key it is; `*` marks the
database in use, and a MISP that does not answer, or turns the key down, says
so in its row:

```
mispql [default]> SHOW DATABASES;
+---+----------+-------------------------------+---------------------+-----------+-------+
|   | database | url                           | email               | role      | org   |
+---+----------+-------------------------------+---------------------+-----------+-------+
| * | default  | https://misp.example.org      | analyst@example.org | Org Admin | CIRCL |
|   | readonly | https://misp.example.org      | reader@example.org  | Read Only | CIRCL |
|   | test     | https://misp-test.example.org | (key rejected)      |           |       |
+---+----------+-------------------------------+---------------------+-----------+-------+
3 databases
```

`USE readonly` switches to another database once its MISP accepts the key, and
`WHOAMI` shows the current one. At the prompt, `USE` says who you are now and
the prompt shows the database in use (`mispql [readonly]>`); in a script, each
statement runs on the database chosen last. `--profile` picks the database a
session starts with.

Keys never cross databases: `MISPQL_URL`, `MISPQL_KEY` and `--key-file` apply
to the database the session starts with only, and every other profile is used
as written. A profile without a key asks for one at a terminal. Names with
capitals, dots or spaces go in backticks: ``USE `Prod.EU`;``.

## Running mispql

```
mispql                                # the interactive prompt, at a terminal
mispql -e "SELECT ...; SELECT ...;"   # statements on the command line
mispql script.mql                     # statements from a file
mispql < script.mql                   # or from stdin
```

### Interactively

```
$ mispql
mispql 1.0.0, default: analyst@example.org, Org Admin, CIRCL at https://misp.example.org. End statements with ;  help for help, quit to leave.
mispql [default]> SELECT COUNT(*) FROM events WHERE date >= '7d';
128
mispql [default]> USE readonly;
readonly: reader@example.org, Read Only, CIRCL
mispql [readonly]> SELECT COUNT(*) FROM events WHERE date >= '7d';
97
```

Enter runs the input once it ends with `;`, so a statement can span lines;
Alt-Enter runs it as it is. Tab completes keywords, table names and, after
`FROM`, the fields of that table as the server lists them for your account.
`help` prints a syntax summary, Ctrl-C clears the input or stops a running
query, `quit` or Ctrl-D leaves. History is kept in
`~/.local/state/mispql/history` (or under `$XDG_STATE_HOME`), readable by you
alone.

The prompt needs a terminal on both stdin and stdout; with output redirected
(`mispql > out.txt`), what you type is read as a script until Ctrl-D.

### In shell scripts

Results go to stdout, errors and warnings to stderr, so the output can be piped
or redirected as it is. Every statement of a script is checked before the first
one is sent, the script stops at the first statement that fails, and the exit
code tells a calling script what happened.

```sh
# yesterday's IDS-flagged IPs, one per line, in MISP's text format
mispql -e "SELECT * FROM attributes
           WHERE type IN ('ip-src', 'ip-dst') AND to_ids = TRUE AND timestamp >= '1d'
           LIMIT 1000 FORMAT text" > blocklist.txt

# a column list prints JSON objects, ready for jq
mispql -e "SELECT id, value FROM attributes
           WHERE eventinfo LIKE '%phishing%' AND type = 'url' LIMIT 100" | jq -r '.[].value'

# a count for a monitoring check, as the read-only account
n=$(mispql --profile readonly -e "SELECT COUNT(*) FROM events WHERE date >= '1d'") || exit 1

# a spreadsheet of last month's events, built by MISP
mispql -e "SELECT * FROM events WHERE date >= '30d' LIMIT 100 FORMAT xlsx" > events.xlsx
```

A file of statements can switch databases as it goes:

```sql
-- weekly.mql: the same count on two MISPs
USE default;
SELECT COUNT(*) FROM events WHERE date >= '7d' AND tags = 'tlp:clear';
USE test;
SELECT COUNT(*) FROM events WHERE date >= '7d' AND tags = 'tlp:clear';
```

`--dry-run` prints the requests the statements compile to and sends nothing. It
needs no server and no key, so a statement can be turned into an API call for a
script, a playbook or another tool. `-v` prints each request to stderr as it is
sent.

| Option | |
|---|---|
| `-e TEXT` | run these statements |
| `--config PATH` | the config file (else `$MISPQL_CONFIG`, `config.toml` next to the package, `~/.config/mispql/config.toml`) |
| `--profile NAME` | the profile (database) to start with; default `[default]` |
| `--key-file PATH` | read the API key from a file; the key is never an argument |
| `-v`, `--verbose` | print each request to stderr as it is sent |
| `--dry-run` | print the requests the statements compile to, send nothing |
| `--version` | print the version |

| Exit code | |
|---|---|
| 0 | every statement ran |
| 1 | a statement failed |
| 2 | a usage, input or configuration error |
| 130 | interrupted |
| 141 | stdout was closed early, as by `\| head` |

## MispQL at a glance

```
SELECT * | COUNT(*) | column, ... FROM table
  [WHERE condition]
  [ORDER BY field [ASC | DESC], ...]
  [LIMIT n [OFFSET n]]
  [FORMAT word];
DESCRIBE table;
SHOW TABLES;
SHOW DATABASES;
USE database;
WHOAMI;
```

| Condition | Meaning |
|---|---|
| `field = value`, `field != value` | equals, does not equal (`<>` is the same as `!=`) |
| `<`, `>`, `<=`, `>=` | numbers, timestamps, dates and datetimes only |
| `field IN (v, ...)`, `NOT IN` | one of the values, none of them |
| `field LIKE 'pattern'`, `NOT LIKE` | text fields only; `%` at the start, the end or both |
| `AND`, `OR`, `NOT`, `( )` | combine conditions; `NOT` binds tighter than `AND`, `AND` tighter than `OR` |

- Keywords can be written in any case; table and field names are
  case-insensitive. A name in backticks is taken as written.
- Strings take single or double quotes, numbers are whole numbers, booleans are
  `TRUE` and `FALSE`. Types are strict: `id = '50000'` is an error.
- Timestamps take a Unix time or a relative time (`'7d'`, `'12h'`, `'30m'`),
  dates take `'YYYY-MM-DD'` or a number of days (`'30d'`).
- `!=`, `NOT IN`, `NOT LIKE` and `NOT` also match rows where the field is
  empty.
- A search needs a `WHERE` or a `LIMIT`. `--` starts a comment.
- The server limits the size of a condition: by default 5 levels of nesting,
  50 conditions and operators, 100 values in one `IN`. `DESCRIBE` shows the
  limits in effect.

## Output formats

| `FORMAT` | Output |
|---|---|
| none | `SELECT *`: a JSON array of the rows, exactly as MISP sends them; a column list: a JSON array of objects with those columns |
| `table` | a text table, as an SQL client prints it; without a column list, the table's default columns |
| `flat` | CSV with a header row |
| any other word | one of MISP's own export formats, passed as `returnFormat` and printed byte for byte: `csv`, `text`, `xlsx`, `stix2`, `suricata`, … whichever the table's endpoint supports |

For a column list, `table` and `flat`, rows are flattened: nested objects
become dotted names (`Event.Orgc.name`), `Tag` becomes one cell of names, and
other lists (an event's attributes, an object's attributes, …) are left out.
They stay in the JSON of `SELECT *` and in MISP's formats. A column list takes
no MISP format, and `SELECT COUNT(*)` prints one number.

`flat` writes values as they are, so a value starting with `=` can act as a
formula when the file is opened in a spreadsheet; `FORMAT xlsx` is the safer
choice there.

## More examples

Last week's `tlp:clear` events, newest first, as CSV:

```sql
SELECT id, date, info, Orgc.name, Tag FROM events
WHERE tags = 'tlp:clear' AND date >= '7d' ORDER BY event_id DESC LIMIT 3 FORMAT flat;
```

```
id,date,info,Orgc.name,Tag
4720,2026-11-14,Credential phishing against webmail users,CIRCL,tlp:clear
4718,2026-11-13,Loader infrastructure seen in honeypot logs,ORG-B,tlp:clear
4711,2026-11-12,Command and control domains of a banking trojan,CIRCL,"tlp:clear,misp-galaxy:banker=""Zeus"""
```

More statements, each one request:

```sql
-- the events holding an indicator
SELECT id, info, Orgc.name, attribute_count FROM events
WHERE value = '198.51.100.23' AND published = TRUE
ORDER BY event_id DESC LIMIT 20 FORMAT table;

-- events of the last 30 days with a Bitcoin address or a bank account, not TLP:RED
SELECT * FROM events
WHERE date >= '30d' AND (type = 'btc' OR object_name = 'bank-account') AND tags != 'tlp:red'
ORDER BY event_id DESC LIMIT 20;

-- an event's attributes that are neither IDS-flagged nor marked as false positives
SELECT * FROM attributes
WHERE event_id = 4711 AND NOT (to_ids = TRUE OR comment LIKE '%false positive%')
LIMIT 100;

-- how many domain-ip objects arrived in events changed this week
SELECT COUNT(*) FROM objects WHERE object_name = 'domain-ip' AND event_timestamp >= '7d';

-- threat actors with a synonym containing "bear", from the galaxies MISP ships with
SELECT id, value, Galaxy.name FROM galaxy_clusters
WHERE galaxy_name = 'Threat Actor' AND org = 'MISP' AND meta.synonyms LIKE '%bear%'
ORDER BY value LIMIT 50 FORMAT table;

-- analysts' opinions below 50 since the start of the month
SELECT * FROM opinions WHERE opinion < 50 AND created >= '2026-11-01'
ORDER BY opinion LIMIT 20 FORMAT table;

-- external organisations of the financial sector
SELECT id, name, nationality, sector FROM organisations
WHERE local = FALSE AND sector IN ('Financial', 'Banking')
ORDER BY name LIMIT 100 FORMAT flat;

-- last week's events as a STIX 2 bundle
SELECT * FROM events WHERE date >= '7d' LIMIT 50 FORMAT stix2;
```

The guide, `site/guide.html`, covers every table with its fields, columns and
sort fields, the output formats and the limits in detail. The full field
reference is `docs/restsearch-complex-query.md` in the MISP repository.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

`site/` holds the product page and the guide: static files, no build step.
`tests/test_site.py` checks that every example shows what `--dry-run` prints
for it; after a change to what a statement compiles to,
`MISPQL_UPDATE_SITE=1 .venv/bin/pytest tests/test_site.py` rewrites the shown
requests (review the diff).

## License

MIT, see [LICENSE](LICENSE).
