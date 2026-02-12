
pytest -q -m smoke
pytest -q -m "not smoke"

##Only integration tests:
pytest -q -m integration

##Run everything except integration:
pytest -q -m "not integration"

### test_integration_mcap_gt_count.py

pytest -q -m integration -k mcap_gt_count
or
pytest -q tests/test_integration_mcap_gt_count.py