from __future__ import annotations

import pytest

from cosmo.converters.ontology_mapper import OntologyMapper


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Car", (2, "VEHICLE", "CAR")),
        ("StandupScooter", (2, "VEHICLE", "STANDUP_SCOOTER")),
        ("WheelChairUser", (2, "VEHICLE", "WHEELCHAIR")),
        ("Pedestrian", (3, "PEDESTRIAN", "OTHER")),
        ("Unicorn", (1, "OTHER", "OTHER")),
    ],
)
def test_builtin_classify(label, expected):
    assert OntologyMapper().classify(label) == expected


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Ambulance", "AMBULANCE"), ("Fire", "FIRE"), ("Police", "POLICE"),
        ("Bus", "PUBLIC_TRANSPORT"), ("Tram", "PUBLIC_TRANSPORT"), ("Train", "PUBLIC_TRANSPORT"),
        ("AgriculturalVehicle", "OTHER"), ("EmergencyVehicle", "OTHER"),
        ("ConstructionVehicle", None), ("Car", None),
    ],
)
def test_builtin_role(label, expected):
    assert OntologyMapper().role(label) == expected
