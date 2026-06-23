# Testing

Run smoke tests / everything but smoke:

```bash
pytest -q -m smoke
pytest -q -m "not smoke"
```

Only integration tests:

```bash
pytest -q -m integration
```

Run everything except integration:

```bash
pytest -q -m "not integration"
```

A single integration test (e.g. `test_integration_mcap_gt_count.py`):

```bash
pytest -q -m integration -k mcap_gt_count
# or
pytest -q tests/test_integration_mcap_gt_count.py
```
