"""Multi-stream physiological analysis pipeline.

Orchestrates analysis across multiple synchronized sensor channels,
producing a unified set of events and derived features.
"""

import logging
from typing import Optional

import numpy as np

from .ecg import compute_bsqi, compute_hrv, compute_rr_intervals, detect_r_peaks
from .events import Event, EventStore
from .motion import classify_activity, detect_motion_artifacts, detect_posture_changes
from .quality import ecg_signal_quality

log = logging.getLogger(__name__)


def analyze_session(
    streams: dict[str, np.ndarray],
    rates: dict[str, float],
    detectors: Optional[list[str]] = None,
) -> list[Event]:
    """Run all applicable detectors on a multi-channel session.

    Parameters
    ----------
    streams : dict mapping channel name → numpy array
        e.g., {"MeasECGmV": ecg_1d, "MeasAcc": acc_nx3, "MeasGyro": gyro_nx3}
    rates : dict mapping channel name → sampling rate in Hz
    detectors : list of detector names to run (None = run all applicable)
        Options: "r_peak", "hrv", "sqi", "bsqi", "activity", "posture",
                 "motion_artifact", "all"

    Returns
    -------
    List of Event objects detected across all channels
    """
    if detectors is None or "all" in (detectors or []):
        detectors = ["r_peak", "hrv", "sqi", "bsqi", "activity", "posture", "motion_artifact"]

    events = []

    # Find ECG channel
    ecg_key = _find_channel(streams, ["ecg", "ECG", "MeasECGmV", "MeasEcgmV", "MeasEcg"])
    # Find ACC channel
    acc_key = _find_channel(streams, ["acc", "ACC", "MeasAcc"])
    # Find GYRO channel
    gyro_key = _find_channel(streams, ["gyro", "GYRO", "MeasGyro"])

    # --- ECG-based detectors ---
    r_peaks = None
    if ecg_key:
        ecg = streams[ecg_key]
        fs_ecg = rates[ecg_key]

        if "r_peak" in detectors:
            r_peaks = detect_r_peaks(ecg, fs_ecg, method="pan_tompkins")
            for peak in r_peaks:
                events.append(Event(
                    timestamp_s=round(peak / fs_ecg, 6),
                    event_type="r_peak",
                    confidence=0.9,
                    source_channels=[ecg_key],
                    description="R-peak",
                ))
            log.info(f"Detected {len(r_peaks)} R-peaks")

        if "hrv" in detectors and r_peaks is not None and len(r_peaks) > 2:
            rr = compute_rr_intervals(r_peaks, fs_ecg)
            hrv = compute_hrv(rr)
            events.append(Event(
                timestamp_s=0,
                duration_s=round(len(ecg) / fs_ecg, 3),
                event_type="hrv_summary",
                confidence=1.0,
                source_channels=[ecg_key],
                description=f"HR={hrv['mean_hr']}bpm SDNN={hrv['sdnn']}ms RMSSD={hrv['rmssd']}ms",
            ))

        if "sqi" in detectors:
            sqi_results = ecg_signal_quality(ecg, fs_ecg)
            for sq in sqi_results:
                if sq["level"] == "low":
                    events.append(Event(
                        timestamp_s=round(sq["sample_idx"] / fs_ecg, 6),
                        duration_s=5.0,
                        event_type="low_quality",
                        confidence=sq["sqi"],
                        source_channels=[ecg_key],
                        description=f"Low ECG quality (SQI={sq['sqi']})",
                    ))

        if "bsqi" in detectors:
            bsqi = compute_bsqi(ecg, fs_ecg)
            events.append(Event(
                timestamp_s=0,
                duration_s=round(len(ecg) / fs_ecg, 3),
                event_type="bsqi_summary",
                confidence=bsqi,
                source_channels=[ecg_key],
                description=f"Beat SQI={bsqi}",
            ))

    # --- ACC-based detectors ---
    if acc_key:
        acc = streams[acc_key]
        fs_acc = rates[acc_key]

        if "activity" in detectors:
            labels = classify_activity(acc, fs_acc)
            window_s = 2.0  # default window
            for i, label in enumerate(labels):
                events.append(Event(
                    timestamp_s=round(i * window_s, 3),
                    duration_s=window_s,
                    event_type=label,
                    confidence=0.8,
                    source_channels=[acc_key],
                    description=f"Activity: {label}",
                ))

        if "posture" in detectors and acc.ndim == 2:
            changes = detect_posture_changes(acc, fs_acc)
            for ch in changes:
                events.append(Event(
                    timestamp_s=round(ch["sample_idx"] / fs_acc, 6),
                    event_type="posture_change",
                    confidence=min(1.0, ch["angle_change"] / 90),
                    source_channels=[acc_key],
                    description=f"Posture change ({ch['angle_change']}°)",
                ))

    # --- Multi-channel detectors ---
    if ecg_key and acc_key and "motion_artifact" in detectors:
        artifacts = detect_motion_artifacts(
            streams[ecg_key], streams[acc_key],
            rates[ecg_key], rates[acc_key],
        )
        for art in artifacts:
            events.append(Event(
                timestamp_s=round(art["sample_idx"] / rates[ecg_key], 6),
                duration_s=1.0,
                event_type="motion_artifact",
                confidence=abs(art["correlation"]),
                source_channels=[ecg_key, acc_key],
                description=f"Motion artifact (corr={art['correlation']}, energy={art['acc_energy']})",
            ))

    log.info(f"Total events detected: {len(events)}")
    return events


def _find_channel(streams: dict, patterns: list[str]) -> Optional[str]:
    """Find a channel key matching any of the given patterns (case-insensitive)."""
    for key in streams:
        for pattern in patterns:
            if pattern.lower() in key.lower():
                return key
    return None
