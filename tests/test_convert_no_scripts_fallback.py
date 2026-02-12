# tests/test_convert_no_scripts_fallback.py
from __future__ import annotations

from cosmo.app.convert_app import (
    _get_converter_or_raise,  # internal helper [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
)


def test_converter_import_has_clear_error_message():
    try:
        fn = _get_converter_or_raise()
        assert callable(fn)
    except RuntimeError as e:
        # If it fails, ensure the error message guides the developer correctly. [2](https://risecloud-my.sharepoint.com/personal/anders_thorsen_ri_se/Documents/Microsoft%20Copilot%20Chat%20Files/convert_app.py)
        msg = str(e)
        assert "COSMO converter implementation not found" in msg
        assert "cosmo.converters.openlabel_to_omega" in msg
