from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from cosmo.converters.openlabel_to_omega import convert_openlabel_to_omega
from tests.cli_helpers import run_cosmo
from tests.test_environment import SRT_TEXT


def _has_betterosi() -> bool:
    try:
        import betterosi  # noqa: F401
        return True
    except Exception:
        return False


def _write_inputs(tmp_path: Path, frames: list[int]) -> tuple[Path, Path, Path]:
    """Write a one-car OpenLABEL (moving 1 px per frame number), a calibration and an SRT."""
    openlabel = {"openlabel": {
        "objects": {"1": {"name": "obj1", "type": "Car"}},
        "frames": {str(f): {"objects": {"1": {"object_data": {
            "rbbox": [{"name": "shape", "val": [10 + f, 20, 40, 20, 0.0]}]}}}} for f in frames},
        "tags": {"0": {"type": "WeatherTag", "tag_data": {
            "text": [{"name": "precipitation", "val": "none"}, {"name": "cloud_cover", "val": "clear"}],
            "num": [{"name": "visibility_km", "val": 50.0}]}}},
    }}
    ol = tmp_path / "openlabel.json"
    ol.write_text(json.dumps(openlabel), encoding="utf-8")
    calib = tmp_path / "Calibration.json"
    calib.write_text(json.dumps({"fps": 30.0, "homography": [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1]]}),
                     encoding="utf-8")
    srt = tmp_path / "video.srt"
    srt.write_text(SRT_TEXT, encoding="utf-8")
    return ol, calib, srt


def _convert(tmp_path: Path, ol: Path, calib: Path, srt: Path | None, write_mcap: bool) -> Path:
    out_prefix = tmp_path / "out" / "run"
    convert_openlabel_to_omega(
        openlabel_path=str(ol), odr_path=None, out_prefix=str(out_prefix),
        calibration_path=str(calib), write_csv=True, write_mcap=write_mcap,
        srt_path=str(srt) if srt else None, log_fn=lambda _m: None,
    )
    return out_prefix


def _csv_rows(out_prefix: Path) -> list[dict]:
    with open(f"{out_prefix}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_srt_drives_frame_timing(tmp_path: Path):
    ol, calib, srt = _write_inputs(tmp_path, [0, 1, 2])
    rows = _csv_rows(_convert(tmp_path, ol, calib, srt, write_mcap=False))
    # SRT stamps 816/850/883 ms -> fitted period 33.5 ms, not the calibration's 1/30 s.
    assert int(rows[2]["total_nanos"]) == pytest.approx(67_000_000, abs=1_000_000)


def test_converter_returns_fps_used(tmp_path: Path):
    ol, calib, srt = _write_inputs(tmp_path, [0, 1, 2])
    kwargs = dict(openlabel_path=str(ol), odr_path=None, out_prefix=str(tmp_path / "run"),
                  calibration_path=str(calib), write_csv=False, write_mcap=False, log_fn=lambda _m: None)
    assert convert_openlabel_to_omega(**kwargs) == pytest.approx(30.0)
    assert convert_openlabel_to_omega(**kwargs, srt_path=str(srt)) == pytest.approx(1 / 0.0335, rel=0.01)


def test_velocity_uses_real_time_step_across_frame_gaps(tmp_path: Path):
    ol, calib, _ = _write_inputs(tmp_path, [0, 1, 5])
    rows = _csv_rows(_convert(tmp_path, ol, calib, None, write_mcap=False))
    # Homography scale 0.1 m/px, 1 px per frame at 30 fps -> 3 m/s also after the 4-frame gap.
    assert float(rows[1]["vel_x"]) == pytest.approx(3.0)
    assert float(rows[2]["vel_x"]) == pytest.approx(3.0)


@pytest.mark.skipif(not _has_betterosi(), reason="betterosi not installed")
def test_mcap_ground_truth_has_environmental_conditions(tmp_path: Path):
    import betterosi

    ol, calib, srt = _write_inputs(tmp_path, [0, 1, 2])
    out_prefix = _convert(tmp_path, ol, calib, srt, write_mcap=True)
    gts = list(betterosi.read(f"{out_prefix}.mcap", return_ground_truth=True))
    ec = gts[0].environmental_conditions
    assert ec.unix_timestamp == 1760614859  # 2025-10-16T11:40:59Z
    assert ec.time_of_day.seconds_since_midnight == 13 * 3600 + 40 * 60 + 59
    assert ec.sun.elevation > 0.3
    assert ec.ambient_illumination == betterosi.EnvironmentalConditionsAmbientIllumination.LEVEL8
    assert ec.precipitation == betterosi.EnvironmentalConditionsPrecipitation.NONE
    assert ec.fog == betterosi.EnvironmentalConditionsFog.EXCELLENT_VISIBILITY
    assert ec.clouds.fractional_cloud_cover == \
        betterosi.EnvironmentalConditionsCloudLayerFractionalCloudCover.ZERO_OKTAS


@pytest.mark.skipif(not _has_betterosi(), reason="betterosi not installed")
def test_mcap_without_sources_leaves_environment_unset(tmp_path: Path):
    import betterosi

    ol, calib, _ = _write_inputs(tmp_path, [0, 1])
    data = json.loads(ol.read_text(encoding="utf-8"))
    del data["openlabel"]["tags"]
    ol.write_text(json.dumps(data), encoding="utf-8")
    gts = list(betterosi.read(f"{_convert(tmp_path, ol, calib, None, write_mcap=True)}.mcap",
                         return_ground_truth=True))
    assert not gts[0].is_set("environmental_conditions")


def test_cli_records_srt_in_provenance(tmp_path: Path):
    ol, calib, srt = _write_inputs(tmp_path, [0, 1, 2])
    srt_prov = tmp_path / "DPR_video.json"
    prov_out = tmp_path / "DPR_out.json"
    # Any valid dataprov file works as upstream provenance for the SRT; create one via a first run.
    first = run_cosmo(["convert", str(ol), "--calibration", str(calib), "--no-mcap",
                       "--output-prefix", str(tmp_path / "first"), "--prov-out", str(srt_prov)], cwd=tmp_path)
    assert first.returncode == 0, first.stderr

    res = run_cosmo(["convert", str(ol), "--calibration", str(calib), "--no-mcap",
                     "--output-prefix", str(tmp_path / "out"), "--srt", str(srt),
                     "--srt-prov", str(srt_prov), "--prov-out", str(prov_out)], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    prov_text = prov_out.read_text(encoding="utf-8")
    assert str(srt) in prov_text
    assert '"dataprov:format": "srt"' in prov_text
