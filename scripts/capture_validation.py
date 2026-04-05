#!/usr/bin/env python3
"""Capture validation dataset from Movesense device.

Configures all channels, logs + subscribes via rotation (3 channels per batch),
saves raw packets and parsed data for protocol validation.

Usage:
    python scripts/capture_validation.py [--duration 5] [--batch-size 3]
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from movensense.cli import _load_env_serial
from movensense.protocol import parse_subscription_packet
from movensense.sensor import SensorCommand

# All subscribable channels with default rates
ALL_CHANNELS = [
    "/Meas/Ecg/200/mV",
    "/Meas/Acc/52",
    "/Meas/Gyro/52",
    "/Meas/Magn/13",
    "/Meas/IMU6/52",
    "/Meas/IMU9/52",
    "/Meas/Temp",
    "/Meas/HR",
]

OUT_DIR = Path.home() / "dbp" / "data" / "movesense" / "validation_capture"


async def capture(duration_per_batch: float, batch_size: int):
    serial = _load_env_serial()
    if not serial:
        print("Error: set MSN in .env")
        return

    print(f"Device: {serial}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_data = {}

    async with SensorCommand(serial) as sensor:
        # Configure and start logging
        config_data = bytearray()
        for p in ALL_CHANNELS + ["/Time/Detailed"]:
            config_data.extend(p.encode() + b"\0")
        await sensor.configure_device(config_data)
        await sensor.start_logging()
        print(f"Logging started with {len(ALL_CHANNELS)} channels")

        # Rotate through channel batches
        batches = [ALL_CHANNELS[i:i + batch_size] for i in range(0, len(ALL_CHANNELS), batch_size)]
        for batch_idx, batch in enumerate(batches):
            print(f"\n--- Batch {batch_idx + 1}/{len(batches)}: {batch} ---")

            refs = {}
            for i, ch in enumerate(batch):
                ref = 30 + batch_idx * 10 + i
                try:
                    r = await sensor.subscribe_to_resource(ch, reference=ref)
                    if r.get("success"):
                        refs[ref] = ch
                        all_data.setdefault(ch, [])
                        print(f"  ✓ {ch}")
                    else:
                        print(f"  ✗ {ch} (status {r.get('status_code')})")
                except Exception as e:
                    print(f"  ✗ {ch} ({e})")

            end = time.time() + duration_per_batch
            count = 0
            while time.time() < end:
                try:
                    resp = await asyncio.wait_for(sensor.data_queue.get(), timeout=0.5)
                    ref = resp.get("reference")
                    ch = refs.get(ref)
                    if ch:
                        payload = resp.get("data_payload", b"")
                        parsed = parse_subscription_packet(payload, ch)
                        all_data[ch].append({
                            "timestamp_ms": parsed.timestamp_ms,
                            "values": parsed.values,
                            "unit": parsed.unit,
                            "n_values": len(parsed.values),
                            "raw_hex": payload.hex(),
                        })
                        count += 1
                except asyncio.TimeoutError:
                    continue
            print(f"  Captured {count} packets")

            for ref in refs:
                try:
                    while not sensor.data_queue.empty():
                        await sensor.data_queue.get()
                    await sensor.unsubscribe_from_resource(ref)
                except Exception:
                    pass
            await asyncio.sleep(0.5)

        # Stop logging
        print("\nStopping logging...")
        await sensor.stop_logging()

    # Save
    result = {
        "serial": serial,
        "active_channels": list(all_data.keys()),
        "all_attempted": ALL_CHANNELS,
        "duration_per_batch_s": duration_per_batch,
        "batch_size": batch_size,
        "capture_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for ch, pkts in all_data.items():
        total = sum(p["n_values"] for p in pkts)
        result[ch] = {
            "packets": len(pkts),
            "total_samples": total,
            "first_3": pkts[:3],
        }
        print(f"  {ch}: {len(pkts)} pkts, {total} samples")
        if pkts and pkts[0]["values"]:
            v = pkts[0]["values"]
            if isinstance(v[0], list):
                print(f"    first: {v[0]}")
            else:
                print(f"    first 3: {v[:3]}")

    out_file = OUT_DIR / f"capture_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nSaved to {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Capture validation dataset from Movesense device")
    parser.add_argument("-d", "--duration", type=float, default=5, help="Seconds per batch (default: 5)")
    parser.add_argument("-b", "--batch-size", type=int, default=3, help="Channels per batch (default: 3)")
    args = parser.parse_args()
    asyncio.run(capture(args.duration, args.batch_size))


if __name__ == "__main__":
    main()
