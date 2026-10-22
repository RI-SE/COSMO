"""Effective vehicle height per object from how its corrected size changes along the track (opt-in).

The oblique correction removes the roof stretch delta = (P - C) h / (H_c - h). With a wrong height h the
corrected length and width still depend on where the vehicle is relative to the camera; a car
does not change size, so the h that makes them most constant over the track is the object's effective
silhouette height. It needs the vehicle to move through a range of distances: other objects keep the class
default. Untested idea (SAVANT uncertainty.md D4d), hence opt-in.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np

HEIGHT_GRID = np.round(np.arange(0.5, 4.5001, 0.05), 2)
MIN_FRAMES = 20
MAX_FRAMES = 200  # evenly subsampled per track
MIN_DISTANCE_RANGE_M = 20.0  # spread of the horizontal distance to the camera along the track
MIN_SIGMA_M = 0.1

Observation = Tuple[Sequence[float], float]  # (rbbox [cx, cy, w, h, yaw_img], world heading)


def size_curves(corrector, rbbox: Sequence[float], heading: float) -> Tuple[np.ndarray, np.ndarray, float]:
    """Corrected (length, width) for every height in HEIGHT_GRID, and the horizontal distance to the camera."""
    cx, cy, w_px, h_px, yaw_img = rbbox
    centre, length, width, u, v = corrector.measured_footprint(cx, cy, w_px, h_px, yaw_img, heading)
    delta = corrector.roof_offset(centre, HEIGHT_GRID)  # (G, 2)
    distance = float(np.linalg.norm(centre - np.asarray(corrector._cam_pos[:2])))
    return length - np.abs(delta @ u), width - np.abs(delta @ v), distance


def _best_height(lengths: np.ndarray, widths: np.ndarray) -> int:
    """Grid index minimising the relative size scatter (CV² of length plus width) over the frames."""
    cv = lengths.var(axis=0) / lengths.mean(axis=0) ** 2 + widths.var(axis=0) / widths.mean(axis=0) ** 2
    return int(np.argmin(cv))


def estimate_height(corrector, observations: List[Observation]) -> Tuple[float, float] | None:
    """(h_eff, sigma) of one object, or None if the track cannot determine it.

    Sigma is half the difference between the estimates from the first and second half of the track (by
    time), at least MIN_SIGMA_M: a rough consistency measure, not a formal standard error.
    """
    if len(observations) < MIN_FRAMES:
        return None
    if len(observations) > MAX_FRAMES:
        observations = [observations[i] for i in np.linspace(0, len(observations) - 1, MAX_FRAMES).astype(int)]
    curves = [size_curves(corrector, rbbox, heading) for rbbox, heading in observations]
    distances = np.array([d for _, _, d in curves])
    if distances.max() - distances.min() < MIN_DISTANCE_RANGE_M:
        return None
    lengths = np.array([lc for lc, _, _ in curves])
    widths = np.array([wc for _, wc, _ in curves])
    best = _best_height(lengths, widths)
    if best in (0, len(HEIGHT_GRID) - 1):  # at the edge of the plausible range: not determined
        return None
    half = len(curves) // 2
    halves = [HEIGHT_GRID[_best_height(lengths[part], widths[part])] for part in (slice(None, half), slice(half, None))]
    sigma = max(abs(halves[0] - halves[1]) / 2, MIN_SIGMA_M)
    return float(HEIGHT_GRID[best]), float(sigma)


def estimate_heights(corrector, observations: Dict[str, List[Observation]]) -> Dict[str, Tuple[float, float]]:
    """{object id: (h_eff, sigma)} for the objects whose track determines it."""
    out = {}
    for oid, obs in observations.items():
        result = estimate_height(corrector, obs)
        if result is not None:
            out[oid] = result
    return out
