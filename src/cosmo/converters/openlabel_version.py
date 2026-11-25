"""metadata.schema_version COSMO writes. Reading accepts any version: no parsing depends on it."""

from __future__ import annotations

from typing import Any, Dict

# SAVANT OpenLABEL subset (const of SAVANT's schema/savant_openlabel_subset.schema.json; a copy is in
# cosmo/schemas). Since 1.3.0 it covers COSMO's output too: cuboids, coordinate systems, object num/text.
SAVANT_SCHEMA_VERSION = "1.3.0"


def stamp_output(root: Dict[str, Any]) -> None:
    """Set the schema version of a document COSMO writes and make object-level vec values lists.

    Older SAVANT files (schema 1.1) wrote some object vec values (marker description) as a scalar;
    OpenLABEL vec values are lists.
    """
    root.setdefault("metadata", {})["schema_version"] = SAVANT_SCHEMA_VERSION
    for obj in (root.get("objects") or {}).values():
        for entry in (obj.get("object_data") or {}).get("vec") or []:
            if "val" in entry and not isinstance(entry["val"], list):
                entry["val"] = [entry["val"]]
