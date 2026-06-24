# tests/test_convert_cli_validation.py
from __future__ import annotations

import json
from pathlib import Path

from tests.cli_helpers import run_cosmo


def test_convert_rejects_positional_and_flag_input(tmp_path: Path):
    ol = tmp_path/"openlabel.json"
    ol.write_text(json.dumps({"openlabel": {"objects": {}, "frames": {}}}), encoding="utf-8")

    res = run_cosmo(
        ["convert", str(ol), "--input", str(ol), "--no-mcap", "--json"],
        cwd=tmp_path,
    )
    assert res.returncode != 0
    assert "Provide either a positional input OR --input/--openlabel" in (res.stderr + res.stdout)  # emitted by ap.error
