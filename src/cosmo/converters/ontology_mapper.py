"""Resolve OpenLABEL object type labels to omega-prime MovingObject enumerations.

Loads the TTL ontology URL(s) declared in the OpenLABEL file and walks the
rdfs:subClassOf hierarchy to determine the correct MovingObjectType and
MovingObjectSubtype.  Falls back to the built-in table when rdflib is absent
or the ontology URL is unreachable.

Requires rdflib for online resolution:
    uv pip install rdflib      (or: pip install rdflib)
"""
from __future__ import annotations

import urllib.request
from functools import lru_cache
from typing import Optional

try:
    import rdflib
    from rdflib.namespace import RDFS
    _RDFLIB = True
except ImportError:
    _RDFLIB = False


# ---------------------------------------------------------------------------
# Built-in label table (covers SAVANT ontology v1.3.1 / savant.ttl)
# Keys are normalised (lowercase, spaces/hyphens/underscores stripped).
# Values are (MovingObjectType name, MovingObjectSubtype name).
# ---------------------------------------------------------------------------
_BUILTIN: dict[str, tuple[str, str]] = {
    # Hierarchy roots
    "dynamicobject":        ("OTHER",      "OTHER"),
    "roaduser":             ("OTHER",      "OTHER"),
    # Vehicle branch — type level
    "vehicle":              ("VEHICLE",    "OTHER"),
    # Vehicle subtypes
    "car":                  ("VEHICLE",    "CAR"),
    "van":                  ("VEHICLE",    "DELIVERY_VAN"),
    "truck":                ("VEHICLE",    "HEAVY_TRUCK"),
    "trailer":              ("VEHICLE",    "TRAILER"),
    "vehicletrailer":       ("VEHICLE",    "TRAILER"),
    "motorbike":            ("VEHICLE",    "MOTORBIKE"),
    "bicycle":              ("VEHICLE",    "BICYCLE"),
    "bus":                  ("VEHICLE",    "BUS"),
    "tram":                 ("VEHICLE",    "TRAM"),
    "train":                ("VEHICLE",    "TRAIN"),
    "caravan":              ("VEHICLE",    "TRAILER"),
    "standupsscooter":      ("VEHICLE",    "STANDUP_SCOOTER"),
    "agriculturalvehicle":  ("VEHICLE",    "OTHER"),
    "constructionvehicle":  ("VEHICLE",    "OTHER"),
    "emergencyvehicle":     ("VEHICLE",    "OTHER"),
    "ambulance":            ("VEHICLE",    "OTHER"),
    "fire":                 ("VEHICLE",    "OTHER"),
    "police":               ("VEHICLE",    "OTHER"),
    "slowmovingvehicle":    ("VEHICLE",    "OTHER"),
    # Human branch
    "human":                ("PEDESTRIAN", "OTHER"),
    "pedestrian":           ("PEDESTRIAN", "OTHER"),
    "wheelchairuser":       ("PEDESTRIAN", "WHEELCHAIR"),
    # Animal
    "animal":               ("ANIMAL",     "OTHER"),
}

# omega-prime MovingObjectType name → OSI numeric code
_TYPE_CODE: dict[str, int] = {
    "UNKNOWN":    0,
    "OTHER":      1,
    "VEHICLE":    2,
    "PEDESTRIAN": 3,
    "ANIMAL":     4,
}


def _norm(label: str) -> str:
    """Normalise a class label for lookup: lowercase, strip whitespace/punctuation."""
    return label.strip().lower().replace(" ", "").replace("-", "").replace("_", "")


class OntologyMapper:
    """Resolves OpenLABEL type labels → (type_code, type_name, subtype_name).

    Loads the ontology TTL from the URL(s) in the OpenLABEL file and extends
    the built-in table with any additional classes found in the hierarchy.
    Falls back silently to the built-in table if rdflib is absent.
    """

    def __init__(self, ontology_urls: Optional[list[str]] = None):
        self._map: dict[str, tuple[str, str]] = dict(_BUILTIN)
        if ontology_urls and _RDFLIB:
            for url in ontology_urls:
                try:
                    self._extend_from_ttl(url)
                except Exception as exc:
                    print(f"[ontology_mapper] Could not load {url!r}: {exc}")

    def _extend_from_ttl(self, url: str) -> None:
        """Fetch TTL, walk subClassOf chains, extend self._map."""
        g = _cached_graph(url)

        # Build URI → (rdfs:label, parent URI) maps
        uri_label: dict[str, str] = {}
        uri_parent: dict[str, str] = {}
        for s, p, o in g:
            s_str, o_str = str(s), str(o)
            if p == RDFS.label:
                uri_label[s_str] = o_str
            elif p == RDFS.subClassOf:
                uri_parent[s_str] = o_str

        def _resolve(uri: str) -> tuple[str, str] | None:
            """Walk up ancestry and return the deepest known mapping."""
            cur, visited = uri, set()
            while cur and cur not in visited:
                visited.add(cur)
                lbl = uri_label.get(cur)
                if lbl:
                    entry = self._map.get(_norm(lbl))
                    if entry:
                        return entry
                cur = uri_parent.get(cur)
            return None

        for uri, lbl in uri_label.items():
            key = _norm(lbl)
            if key not in self._map:
                resolved = _resolve(uri_parent.get(uri, ""))
                if resolved:
                    self._map[key] = resolved

    def classify(self, label: str) -> tuple[int, str, str]:
        """Return (type_code, type_name, subtype_name) for an OpenLABEL type label."""
        type_name, subtype_name = self._map.get(_norm(label), ("OTHER", "OTHER"))
        return _TYPE_CODE.get(type_name, 1), type_name, subtype_name


@lru_cache(maxsize=16)
def _cached_graph(url: str) -> "rdflib.Graph":
    """Fetch and parse a TTL ontology; cached per URL for the process lifetime.

    Handles namespace IRIs ending in '#' by stripping the fragment and trying
    both the bare URL and a '.ttl' variant (standard ontology publishing pattern).
    """
    # Namespace IRIs like https://example.org/ontology/foo# are logical identifiers;
    # the physical file lives at the base URL, typically with a .ttl extension.
    base = url.rstrip("#")
    candidates = [base + ".ttl"] if not base.endswith(".ttl") else [base]
    candidates.append(base)  # also try the bare URL (content negotiation fallback)

    last_exc: Exception = RuntimeError(f"No candidates for {url!r}")
    for fetch_url in candidates:
        try:
            req = urllib.request.Request(
                fetch_url,
                headers={"Accept": "text/turtle, application/rdf+xml;q=0.9, */*;q=0.5"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "")
            fmt = "turtle" if ("turtle" in content_type or fetch_url.endswith(".ttl")) else "xml"
            g = rdflib.Graph()
            g.parse(data=data, format=fmt)
            if len(g) > 0:
                return g
        except Exception as exc:
            last_exc = exc
            continue
    raise last_exc
