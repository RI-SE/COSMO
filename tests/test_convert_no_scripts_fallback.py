# tests/test_convert_no_scripts_fallback.py
from __future__ import annotations

from cosmo.app.convert_app import (
    _get_converter_or_raise,  # internal helper
)


def test_converter_import_has_clear_error_message():
    try:
        fn = _get_converter_or_raise()
        assert callable(fn)
    except RuntimeError as e:
        # If it fails, ensure the error message guides the developer correctly.
        msg = str(e)
        assert "COSMO converter implementation not found" in msg
        assert "cosmo.converters.openlabel_to_omega" in msg
