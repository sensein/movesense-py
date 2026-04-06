"""Orientation estimation from IMU data (ACC + GYRO fusion).

Implements Madgwick filter and complementary filter for robust
orientation tracking from multi-axis inertial sensors.
"""

import numpy as np
from typing import Optional


def madgwick_filter(
    acc: np.ndarray, gyro: np.ndarray, fs: float,
    beta: float = 0.1, initial_q: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Madgwick AHRS orientation filter for ACC + GYRO fusion.

    Parameters
    ----------
    acc : Nx3 accelerometer data (g)
    gyro : Nx3 gyroscope data (rad/s or deg/s — auto-detected)
    fs : sampling rate in Hz
    beta : filter gain (higher = more accelerometer trust, default 0.1)
    initial_q : initial quaternion [w, x, y, z], default [1, 0, 0, 0]

    Returns
    -------
    Nx4 array of quaternions [w, x, y, z] per sample
    """
    try:
        from ahrs.filters import Madgwick
        filt = Madgwick(gyr=gyro, acc=acc, frequency=fs, gain=beta)
        return filt.Q
    except ImportError:
        return _madgwick_pure(acc, gyro, fs, beta, initial_q)


def _madgwick_pure(
    acc: np.ndarray, gyro: np.ndarray, fs: float,
    beta: float, initial_q: Optional[np.ndarray],
) -> np.ndarray:
    """Pure numpy Madgwick filter implementation (fallback when ahrs not installed)."""
    n = len(acc)
    dt = 1.0 / fs
    q = np.array(initial_q if initial_q is not None else [1.0, 0, 0, 0], dtype=np.float64)
    quaternions = np.zeros((n, 4))

    # Auto-detect deg/s vs rad/s
    gyro = gyro.copy().astype(np.float64)
    if np.max(np.abs(gyro)) > 10:  # likely degrees/s
        gyro = np.radians(gyro)

    for i in range(n):
        a = acc[i].astype(np.float64)
        g = gyro[i].astype(np.float64)

        # Normalize accelerometer
        a_norm = np.linalg.norm(a)
        if a_norm > 0:
            a = a / a_norm

        # Quaternion rate from gyroscope
        qw, qx, qy, qz = q
        q_dot = 0.5 * np.array([
            -qx * g[0] - qy * g[1] - qz * g[2],
             qw * g[0] + qy * g[2] - qz * g[1],
             qw * g[1] - qx * g[2] + qz * g[0],
             qw * g[2] + qx * g[1] - qy * g[0],
        ])

        # Gradient descent corrective step
        f = np.array([
            2 * (qx * qz - qw * qy) - a[0],
            2 * (qw * qx + qy * qz) - a[1],
            2 * (0.5 - qx**2 - qy**2) - a[2],
        ])
        j = np.array([
            [-2*qy,  2*qz, -2*qw, 2*qx],
            [ 2*qx,  2*qw,  2*qz, 2*qy],
            [ 0,    -4*qx, -4*qy, 0    ],
        ])
        step = j.T @ f
        step_norm = np.linalg.norm(step)
        if step_norm > 0:
            step = step / step_norm

        q = q + (q_dot - beta * step) * dt
        q = q / np.linalg.norm(q)
        quaternions[i] = q

    return quaternions


def quaternion_to_euler(quaternions: np.ndarray) -> np.ndarray:
    """Convert Nx4 quaternions [w,x,y,z] to Nx3 Euler angles [roll, pitch, yaw] in degrees."""
    q = quaternions
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]

    roll = np.degrees(np.arctan2(2 * (w * x + y * z), 1 - 2 * (x**2 + y**2)))
    pitch = np.degrees(np.arcsin(np.clip(2 * (w * y - z * x), -1, 1)))
    yaw = np.degrees(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y**2 + z**2)))

    return np.column_stack([roll, pitch, yaw])


def estimate_posture_from_quaternions(quaternions: np.ndarray) -> np.ndarray:
    """Classify posture per sample from orientation quaternions.

    Returns array of labels: 'upright', 'supine', 'prone', 'left', 'right'
    """
    euler = quaternion_to_euler(quaternions)
    pitch = euler[:, 1]  # forward/back tilt
    roll = euler[:, 0]   # left/right tilt

    labels = []
    for p, r in zip(pitch, roll):
        if abs(p) < 30 and abs(r) < 30:
            labels.append("upright")
        elif p > 45:
            labels.append("supine")
        elif p < -45:
            labels.append("prone")
        elif r > 45:
            labels.append("right")
        elif r < -45:
            labels.append("left")
        else:
            labels.append("upright")

    return np.array(labels)
