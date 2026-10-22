"""Velocity and acceleration of converted object tracks."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np

Vector = Tuple[float, float, float]
ZERO: Vector = (0.0, 0.0, 0.0)


def raw_finite_differences(frames: Iterable[Tuple[float, List[dict]]]) -> None:
    """Set "vel"/"acc" of every sample from the previous sample of the same object (in place).

    frames: (t_sec, samples) in time order; samples carry "idx" and "X", "Y", "Z".
    Uses the real time step, so frame gaps in a track don't inflate velocity; the first sample of a
    track gets zero velocity, the second zero acceleration.
    """
    last_positions: Dict[int, Tuple[float, float, float, float]] = {}
    last_velocities: Dict[int, Vector] = {}
    for t_sec, samples in frames:
        for s in samples:
            idx, position = s["idx"], (s["X"], s["Y"], s["Z"])
            last_p = last_positions.get(idx)
            step = t_sec - last_p[3] if last_p is not None else 0.0
            if last_p is None or step <= 0:
                vel, acc = ZERO, ZERO
            else:
                vel = tuple((position[i] - last_p[i]) / step for i in range(3))
                last_v = last_velocities.get(idx)
                acc = ZERO if last_v is None else tuple((vel[i] - last_v[i]) / step for i in range(3))
            s["vel"], s["acc"] = vel, acc
            last_positions[idx] = (*position, t_sec)
            last_velocities[idx] = vel


# Constant-acceleration model: white jerk with this power spectral density (m²/s⁵); vehicles rarely exceed
# a jerk of 1-2 m/s³ (assumption, see SAVANT uncertainty.md "Assumptions to check later")
DEFAULT_JERK_PSD = 2.0
_INITIAL_VEL_SIGMA = 10.0  # m/s
_INITIAL_ACC_SIGMA = 5.0  # m/s²


def _transition(dt: float, q: float) -> Tuple[np.ndarray, np.ndarray]:
    """F and Q of state [x, y, vx, vy, ax, ay] over dt."""
    F = np.eye(6)
    F[0, 2] = F[1, 3] = F[2, 4] = F[3, 5] = dt
    F[0, 4] = F[1, 5] = dt * dt / 2
    block = q * np.array([[dt ** 5 / 20, dt ** 4 / 8, dt ** 3 / 6],
                          [dt ** 4 / 8, dt ** 3 / 3, dt ** 2 / 2],
                          [dt ** 3 / 6, dt ** 2 / 2, dt]])
    Q = np.zeros((6, 6))
    for i in range(3):
        for j in range(3):
            Q[2 * i, 2 * j] = Q[2 * i + 1, 2 * j + 1] = block[i, j]
    return F, Q


def rts_smooth(t: Sequence[float], z: np.ndarray, R: np.ndarray, jerk_psd: float = DEFAULT_JERK_PSD):
    """Kalman filter + Rauch-Tung-Striebel smoother of one track.

    t: (n,) times; z: (n, 2) positions; R: (n, 2, 2) measurement covariances (must be positive definite).
    Returns (states (n, 6) [x, y, vx, vy, ax, ay], covariances (n, 6, 6)).
    """
    n = len(t)
    Hm = np.zeros((2, 6))
    Hm[0, 0] = Hm[1, 1] = 1.0
    x = np.array([z[0, 0], z[0, 1], 0.0, 0.0, 0.0, 0.0])
    P = np.diag([0.0, 0.0, _INITIAL_VEL_SIGMA ** 2, _INITIAL_VEL_SIGMA ** 2,
                 _INITIAL_ACC_SIGMA ** 2, _INITIAL_ACC_SIGMA ** 2])
    P[:2, :2] = R[0]
    xf, Pf, xp, Pp, Fs = np.zeros((n, 6)), np.zeros((n, 6, 6)), np.zeros((n, 6)), np.zeros((n, 6, 6)), []
    for k in range(n):
        if k > 0:
            F, Q = _transition(t[k] - t[k - 1], jerk_psd)
            x, P = F @ x, F @ P @ F.T + Q
            Fs.append(F)
        xp[k], Pp[k] = x, P
        if k > 0:
            S = Hm @ P @ Hm.T + R[k]
            K = P @ Hm.T @ np.linalg.inv(S)
            x = x + K @ (z[k] - Hm @ x)
            P = (np.eye(6) - K @ Hm) @ P
        xf[k], Pf[k] = x, P
    xs, Ps = xf.copy(), Pf.copy()
    for k in range(n - 2, -1, -1):
        C = Pf[k] @ Fs[k].T @ np.linalg.inv(Pp[k + 1])
        xs[k] = xf[k] + C @ (xs[k + 1] - xp[k + 1])
        Ps[k] = Pf[k] + C @ (Ps[k + 1] - Pp[k + 1]) @ C.T
    return xs, Ps


def smooth_tracks(frames: Iterable[Tuple[float, List[dict]]], jerk_psd: float = DEFAULT_JERK_PSD,
                  replace: bool = False) -> None:
    """Smooth every track that has "cov_noise"; store "smoothed" = (state, cov) on each sample (in place).

    The measurement noise is the frame-independent part of the error only (see cosmo.converters.quality).

    With replace, x/y position, velocity and acceleration are replaced by the smoothed ones (z keeps its
    raw differences).
    """
    tracks: Dict[int, List[Tuple[float, dict]]] = {}
    for t_sec, samples in frames:
        for s in samples:
            if s.get("cov_noise") is not None:
                tracks.setdefault(s["idx"], []).append((t_sec, s))
    for track in tracks.values():
        t = [ts for ts, _ in track]
        if len(t) < 2 or any(b <= a for a, b in zip(t, t[1:])):
            continue
        z = np.array([[s["X"], s["Y"]] for _, s in track])
        R = np.array([s["cov_noise"] + np.eye(2) * 1e-6 for _, s in track])
        states, covs = rts_smooth(t, z, R, jerk_psd)
        for (_, s), state, cov in zip(track, states, covs):
            s["smoothed"] = (state, cov)
            if replace:
                s["X"], s["Y"] = float(state[0]), float(state[1])
                s["vel"] = (float(state[2]), float(state[3]), s["vel"][2])
                s["acc"] = (float(state[4]), float(state[5]), s["acc"][2])
