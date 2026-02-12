# tests/mcap_helpers.py
"""MCAP test helpers for COSMO.

These utilities are designed for integration tests that validate MCAP output.
They work in two modes:

1) If the optional `mcap` Python package is installed, we parse the file and
   extract channel topics via `mcap.reader.make_reader`.
2) If `mcap` is not installed, we fall back to lightweight checks such as
   file existence and non-empty size.

This keeps the core test suite independent of optional dependencies while
still enabling deeper validation when available.

Typical usage:

    from tests.mcap_helpers import (
        has_mcap_reader,
        mcap_topics,
        assert_mcap_nonempty,
        assert_mcap_has_topics,
    )

    assert_mcap_nonempty(mcap_path)
    if has_mcap_reader():
        assert_mcap_has_topics(mcap_path, {"ground_truth", "ground_truth_map"})
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set


@dataclass(frozen=True)
class McapInspection:
    """Result of best-effort MCAP inspection."""

    path: str
    size_bytes: int
    topics: Set[str]
    used_reader: bool
    error: Optional[str] = None


def has_mcap_reader() -> bool:
    """Return True if the `mcap` reader is importable."""
    try:
        from mcap.reader import make_reader  # noqa: F401
        return True
    except Exception:
        return False


def assert_mcap_nonempty(mcap_path: str | Path) -> Path:
    """Assert the MCAP file exists and is non-empty; return Path."""
    p = Path(mcap_path)
    assert p.is_file(), f"MCAP file not found: {p}"
    size = p.stat().st_size
    assert size > 0, f"MCAP file is empty: {p}"
    return p


def mcap_topics(mcap_path: str | Path) -> Set[str]:
    """Return the set of topics in an MCAP file (requires `mcap`).

    Raises ImportError if `mcap` is not installed.
    """
    if not has_mcap_reader():
        raise ImportError("mcap package is not installed; cannot inspect topics")

    from mcap.reader import make_reader

    p = assert_mcap_nonempty(mcap_path)
    topics: Set[str] = set()

    with p.open("rb") as f:
        reader = make_reader(f)
        # iter_messages yields (schema, channel, message)
        for _schema, channel, _msg in reader.iter_messages():
            topics.add(channel.topic)

    return topics


def inspect_mcap(mcap_path: str | Path) -> McapInspection:
    """Best-effort inspection: always checks size; parses topics if possible."""
    p = Path(mcap_path)
    if not p.is_file():
        return McapInspection(path=str(p), size_bytes=0, topics=set(), used_reader=False, error="file not found")

    size = p.stat().st_size
    if size <= 0:
        return McapInspection(path=str(p), size_bytes=size, topics=set(), used_reader=False, error="empty file")

    if not has_mcap_reader():
        return McapInspection(path=str(p), size_bytes=size, topics=set(), used_reader=False)

    try:
        topics = mcap_topics(p)
        return McapInspection(path=str(p), size_bytes=size, topics=topics, used_reader=True)
    except Exception as e:
        return McapInspection(path=str(p), size_bytes=size, topics=set(), used_reader=True, error=f"{type(e).__name__}: {e}")


def assert_mcap_has_topics(mcap_path: str | Path, expected: Iterable[str]) -> None:
    """Assert an MCAP file contains all expected topics.

    If the `mcap` package is not available, raises ImportError.
    """
    expected_set = set(expected)
    topics = mcap_topics(mcap_path)
    missing = sorted(expected_set - topics)
    assert not missing, f"Missing MCAP topics: {missing}. Present: {sorted(topics)}"


def assert_mcap_has_any_topic(mcap_path: str | Path, candidates: Iterable[str]) -> str:
    """Assert an MCAP file contains at least one topic from candidates.

    Returns the first matching topic. Requires `mcap`.
    """
    cand = list(candidates)
    topics = mcap_topics(mcap_path)
    for t in cand:
        if t in topics:
            return t
    raise AssertionError(f"None of the candidate topics found: {cand}. Present: {sorted(topics)}")
