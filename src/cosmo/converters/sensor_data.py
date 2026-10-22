"""Optional OSI SensorData topic: the converted objects as detections with their uncertainty.

GroundTruth has no uncertainty fields; SensorData.DetectedMovingObject has existence probability,
measurement state and `base_rmse`. Positions are absolute, so their RMSE includes the datum term.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

from cosmo.converters.quality import MEASURED_ORIGINS, SYNTHESIZED_ORIGINS

TOPIC = "sensor_data"


def _measurement_state(betterosi, origin: Optional[str]):
    states = betterosi.DetectedItemHeaderMeasurementState
    if origin in MEASURED_ORIGINS:
        return states.MEASURED
    if origin in SYNTHESIZED_ORIGINS:
        return states.PREDICTED
    return states.UNKNOWN


def _rmse(betterosi, s: Dict[str, Any], datum: Optional[np.ndarray]):
    """Per-field RMS error (1σ, assuming no bias); velocity/acceleration only when smoothed."""
    cov = s["cov_rel"] + (datum if datum is not None else 0.0)
    rmse = dict(
        position=betterosi.Vector3D(x=math.sqrt(cov[0, 0]), y=math.sqrt(cov[1, 1]), z=0.0),
        dimension=betterosi.Dimension3D(length=s["sigma_size"][0], width=s["sigma_size"][1], height=0.0),
        orientation=betterosi.Orientation3D(roll=0.0, pitch=0.0, yaw=s["sigma_heading"]),
    )
    if s.get("smoothed_output"):
        P = s["smoothed"][1]
        rmse["velocity"] = betterosi.Vector3D(x=math.sqrt(P[2, 2]), y=math.sqrt(P[3, 3]), z=0.0)
        rmse["acceleration"] = betterosi.Vector3D(x=math.sqrt(P[4, 4]), y=math.sqrt(P[5, 5]), z=0.0)
    return betterosi.BaseMoving(**rmse)


def detected_object(betterosi, s: Dict[str, Any], base, mo_type, datum: Optional[np.ndarray]):
    """One DetectedMovingObject; existence_probability only when the box has p_exist (unset = unknown)."""
    identifier = betterosi.Identifier(value=int(s["idx"]))
    header = betterosi.DetectedItemHeader(tracking_id=identifier, ground_truth_id=[identifier],
                                          measurement_state=_measurement_state(betterosi, s.get("origin")))
    if s.get("p_exist") is not None:
        header.existence_probability = float(s["p_exist"])
    candidate = betterosi.DetectedMovingObjectCandidateMovingObject(probability=1.0, type=mo_type)
    if s.get("vehicle_classification") is not None:
        candidate.vehicle_classification = s["vehicle_classification"]
    obj = betterosi.DetectedMovingObject(header=header, base=base, candidate=[candidate])
    if s.get("cov_rel") is not None:
        obj.base_rmse = _rmse(betterosi, s, datum)
    return obj


def sensor_data(betterosi, frame: Dict[str, Any], timestamp, objects):
    return betterosi.SensorData(
        version=betterosi.InterfaceVersion(version_major=3, version_minor=7, version_patch=0),
        timestamp=timestamp, moving_object=objects,
    )
