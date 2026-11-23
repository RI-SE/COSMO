"""metadata.schema_version COSMO writes. Reading accepts any version: no parsing depends on it."""

from __future__ import annotations

from typing import Any, Dict

# ASAM OpenLABEL: documents that use parts outside the SAVANT subset (cuboids, coordinate systems)
OPENLABEL_SCHEMA_VERSION = "1.0.0"
# SAVANT OpenLABEL subset (const of SAVANT's schema/savant_openlabel_subset.schema.json): corrected pixel annotations
SAVANT_SCHEMA_VERSION = "1.2.0"


def stamp_output(root: Dict[str, Any], schema_version: str) -> None:
    """Set the schema version of a document COSMO writes and make object-level vec values lists.

    Older SAVANT files (schema 1.1) wrote some object vec values (marker description) as a scalar;
    OpenLABEL vec values are lists.
    """
    root.setdefault("metadata", {})["schema_version"] = schema_version
    for obj in (root.get("objects") or {}).values():
        for entry in (obj.get("object_data") or {}).get("vec") or []:
            if "val" in entry and not isinstance(entry["val"], list):
                entry["val"] = [entry["val"]]
