# mispql

A command-line client that queries MISP in MispQL, an SQL-like language, over
MISP's REST API. It runs interactively or in batch:

```
mispql -e "SELECT * FROM attributes WHERE type = 'ip-dst' AND id > 50000 LIMIT 10;"
```

Every query goes through the API as the user whose key it uses, so it sees
exactly what that user may see. Read-only.

**Status**: early development; only `mispql --version` works so far. Needs a
MISP 3.0 server (the `query` key of restSearch).

## Install

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Python 3.11 or later.

## License

MIT, see [LICENSE](LICENSE).
