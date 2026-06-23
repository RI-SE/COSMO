# Contributing to COSMO

## Environment setup

### With uv (recommended)

```bash
uv sync --all-groups
```

The lockfile (`uv.lock`) ensures a reproducible environment. Use `--group <name>` instead of `--all-groups` to install only what you need — see the groups in `pyproject.toml`.

### With conda + uv

If you prefer conda to manage the Python interpreter:

```bash
conda env create -f environment.yml
conda activate cosmo
uv sync --all-groups
```

`environment.yml` only provides Python and uv. All project dependencies are still managed by uv via `pyproject.toml`.

---

## Development workflow

Install pre-commit hooks (one-time):

```bash
pre-commit install
```

Run the linter:

```bash
ruff check src/ tests/
```

Run tests:

```bash
pytest                        # all tests
pytest -m smoke               # fast CLI smoke tests only
pytest -m integration         # integration tests (requires betterosi/MCAP)
```

---

## Dependency groups

| Group | Purpose |
|---|---|
| `dev` | pytest, ruff, pre-commit |
| `gui` | PyQt5 (GUI and trajectory-explorer) |
| `mcap` | betterosi, betterproto2 (MCAP/OSI output) |
| `plot` | omega-prime, altair (plotting) |
| `correction` | scipy (drone bbox correction) |
| `ontology` | rdflib (ontology tooling) |
| `docs` | mkdocs-material (build the docs) |

Composite groups (`cosmo-cli`, `cosmo-gui`, `trajectory-explorer`) bundle the above — see `pyproject.toml` for details.
