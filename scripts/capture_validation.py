#!/usr/bin/env python3
"""Capture raw validation dataset from Movesense device.

Step 1: Configure all channels, start logging, subscribe via rotation,
        save raw binary packets to disk.
Step 2: Stop logging, fetch the SBEM log.
Step 3 (separate script): Parse both and compare.

Usage:
    python scripts/capture_validation.py [--duration 5] [--batch-size 3]
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from movensense.cli import _load_env_serial
from movensense.sensor import SensorCommand

ALL_CHANNELS = [
    "/Meas/Ecg/200/mV", "/Meas/Acc/52", "/Meas/Gyro/52", "/Meas/Magn/13",
    "/Meas/IMU6/52", "/Meas/IMU9/52", "/Meas/Temp", "/Meas/HR",
]

OUT_DIR = Path.home() / "dbp" / "data" / "movesense" / "validation_capture"


async def capture(duration_per_batch: float, batch_size: int):
    serial = _load_env_serial()
    if not serial:
        print("Error: set MSN in .env")
        return

    ts_label = time.strftime("%Y%m%d_%H%M%S")
    capture_dir = OUT_DIR / ts_label
    capture_dir.mkdir(parents=True, exist_ok=True)

    print(f"Device: {serial}")
    print(f"Output: {capture_dir}")

    raw_packets: dict[str, list[bytes]] = {}
    metadata = {
        "serial": serial,
        "all_channels": ALL_CHANNELS,
        "duration_per_batch_s": duration_per_batch,
        "batch_size": batch_size,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    async with SensorCommand(serial) as sensor:
        # Configure + start logging
        config_data = bytearray()
        for p in ALL_CHANNELS + ["/Time/Detailed"]:
            config_data.extend(p.encode() + b"\0")
        await sensor.configure_device(config_data)
        await sensor.start_logging()
        print(f"Logging started ({len(ALL_CHANNELS)} channels)")

        # Rotate through batches — collect raw binary only
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
                        raw_packets.setdefault(ch, [])
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
                        raw_packets[ch].append(resp.get("data_payload", b""))
                        count += 1
                except asyncio.TimeoutError:
                    continue
            print(f"  Captured {count} raw packets")

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

    # Save raw binary packets per channel
    metadata["captured_channels"] = {}
    for ch, pkts in raw_packets.items():
        safe_name = ch.replace("/", "_").strip("_")
        bin_file = capture_dir / f"{safe_name}.bin"
        with open(bin_file, "wb") as f:
            for pkt in pkts:
                # Write: uint32 packet_length + raw bytes
                f.write(len(pkt).to_bytes(4, "little"))
                f.write(pkt)
        metadata["captured_channels"][ch] = {
            "packet_count": len(pkts),
            "bin_file": str(bin_file.relative_to(capture_dir)),
            "total_bytes": sum(len(p) for p in pkts),
        }
        print(f"  {ch}: {len(pkts)} packets → {bin_file.name}")

    metadata["end_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta_file = capture_dir / "metadata.json"
    with open(meta_file, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"\nMetadata: {meta_file}")

    # Now fetch the log
    print("\nFetching logged data for comparison...")
    from movensense.cli import _fetch
    fetch_dir = capture_dir / "fetched"
    fetch_dir.mkdir(exist_ok=True)
    try:
        result = await _fetch(serial, fetch_dir, edf=False)
        if result.get("success"):
            print(f"  Fetched {len(result.get('files', []))} logs → {fetch_dir}")
        else:
            print(f"  Fetch failed: {result.get('error')}")
    except Exception as e:
        print(f"  Fetch error: {e}")

    print(f"\nDone. Run parse_validation.py to compare subscription vs logged data.")


def main():
    parser = argparse.ArgumentParser(description="Capture raw validation dataset")
    parser.add_argument("-d", "--duration", type=float, default=5, help="Seconds per batch")
    parser.add_argument("-b", "--batch-size", type=int, default=3, help="Channels per batch")
    args = parser.parse_args()
    asyncio.run(capture(args.duration, args.batch_size))


if __name__ == "__main__":
    main()
