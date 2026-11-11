# mispql

A command-line client that queries MISP in MispQL, an SQL-like language, over
MISP's REST API. It runs interactively or in batch:

```
mispql -e "SELECT * FROM attributes WHERE type = 'ip-dst' AND id > 50000 LIMIT 10;"
```

Every query goes through the API as the user whose key it uses, so it sees
exactly what that user may see. Read-only.

**Status**: early development; batch mode works, the interactive prompt does
not yet. Needs a MISP 3.0 server (the `query` key of restSearch).

`SHOW TABLES` lists the tables you may query, `DESCRIBE events` their fields,
operators and the names `ORDER BY` takes. Both, and every `WHERE`, need the
Complex query permission (or a site admin).

## Usage

```
mispql -e "SELECT ...; SELECT ...;"   # statements on the command line
mispql script.mql                     # statements from a file
mispql < script.mql                   # or from stdin
```

Every statement is checked before the first one is sent; the script stops at
the first error. Results go to stdout, errors and warnings to stderr.

| Option | |
|---|---|
| `--config PATH` | the config file (else `$MISPQL_CONFIG`, `config.toml` next to the package, `~/.config/mispql/config.toml`) |
| `--profile NAME` | a profile (TOML table) of the config file; default `[default]` |
| `--key-file PATH` | read the API key from a file; the key is never an argument |
| `-v`, `--verbose` | print each request to stderr as it is sent |
| `--dry-run` | print the requests the statements compile to, send nothing |

`MISPQL_URL` and `MISPQL_KEY` override the config file. Without a key, `mispql`
asks for one when stdin is a terminal.

```toml
[default]
url = "https://misp.example.org"
key = "..."            # chmod 600 the file
verify_ssl = true      # or the path of a CA bundle
timeout = 300          # seconds to wait for an answer
```

Exit codes: 0 every statement ran, 1 a statement failed, 2 a usage, input or
config error, 130 interrupted, 141 stdout closed (`| head`).

## Install

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Python 3.11 or later.

## License

MIT, see [LICENSE](LICENSE).
