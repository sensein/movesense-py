import sys
import json
import os
import csv
from datetime import datetime

ECG_LSB_TO_MV = 0.000381469726563

def convert_json_to_csv(input_file, output_file):
    """
    Convert a JSON file containing sensor data to CSV format.
    Works for ECG, ACC, GYRO, TEMP, and similar Meas* streams.
    """
    with open(input_file, 'r') as f:
        print("Parsing JSON...")
        content = json.load(f)

    samples = content.get("Samples", [])
    print(f"Total sample entries: {len(samples)}")

    # Group samples by stream name
    sample_streams = {}
    for sample in samples:
        sample_type = list(sample.keys())[0]
        if sample_type == "TimeDetailed":
            continue
        sample_streams.setdefault(sample_type, []).append(sample[sample_type])

    print(f"Streams found: {list(sample_streams.keys())}")

    # Extract time reference (if available)
    time_detailed = next((s["TimeDetailed"] for s in samples if "TimeDetailed" in s), {})
    relative_time = time_detailed.get("relativeTime", 0)
    utc_time = time_detailed.get("utcTime", 0)
    try:
        relative_time = int(relative_time) / 1000
    except Exception:
        relative_time = 0
    try:
        utc_time_str = datetime.utcfromtimestamp(int(utc_time) / 1_000_000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except Exception:
        utc_time_str = "N/A"

    # Process each stream
    for stream_name, entries in sample_streams.items():
        print(f"\n{'='*60}\nProcessing stream: {stream_name}")
        all_data = []
        prev_dt = 5.0  # default sample step (ms)

        for chunk_idx, entry in enumerate(entries):
            # --- Universal timestamp handling ---
            timestamp = entry.get("Timestamp") or entry.get("timestamp")

            # If missing, try to find it inside nested dicts
            if timestamp is None:
                for v in entry.values():
                    if isinstance(v, dict) and "Timestamp" in v:
                        timestamp = v["Timestamp"]
                        entry = v
                        break

            if timestamp is None:
                print(f"Warning: chunk {chunk_idx} missing Timestamp, skipping")
                continue

            # --- Find the data field ---
            data_keys = [k for k in entry.keys() if k.lower() not in ["timestamp"]]
            if len(data_keys) == 0:
                print(f"Warning: no data key in chunk {chunk_idx}: {list(entry.keys())}")
                continue

            # Prefer known field names (Samples, Measurement, ArrayAcc, etc.)
            preferred_fields = ["Samples", "Measurement", "ArrayAcc", "ArrayGyro", "ArrayMag"]
            data_key = next((k for k in preferred_fields if k in entry), data_keys[0])
            data_array = entry[data_key]

            # --- Normalize data to a list of values ---
            if isinstance(data_array, (int, float)):
                values = [data_array]
            elif isinstance(data_array, dict):
                # Flatten nested vectors (x,y,z)
                if all(isinstance(v, (int, float)) for v in data_array.values()):
                    values = [[data_array.get("x", 0), data_array.get("y", 0), data_array.get("z", 0)]]
                else:
                    # Nested lists or other dicts
                    values = []
                    for v in data_array.values():
                        if isinstance(v, list):
                            values.extend(v)
            elif isinstance(data_array, list):
                # Might be a list of scalars or dicts
                if len(data_array) > 0 and isinstance(data_array[0], dict) and all(k in data_array[0] for k in ("x", "y", "z")):
                    values = [[i["x"], i["y"], i["z"]] for i in data_array]
                else:
                    values = data_array
            else:
                print(f"Warning: data not recognized in chunk {chunk_idx}, skipping")
                continue

            n = len(values)
            # --- Estimate dt between chunks ---
            if chunk_idx + 1 < len(entries):
                next_ts = entries[chunk_idx + 1].get("Timestamp") or 0
                if next_ts and next_ts > timestamp:
                    dt = (next_ts - timestamp) / n
                    prev_dt = dt
                else:
                    dt = prev_dt
            else:
                dt = prev_dt

            # --- Append samples with timestamps ---
            for i, val in enumerate(values):
                ts = int(timestamp + i * dt)
                all_data.append((ts, val))

        if not all_data:
            print(f"No valid samples found for {stream_name}, skipping.")
            continue

        all_data.sort(key=lambda x: x[0])

        base, _ = os.path.splitext(output_file)
        stream_output = f"{base}_{stream_name}.csv"

        # --- Write CSV ---
        print(f"Writing {len(all_data)} samples to {stream_output}")
        with open(stream_output, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)

            first_val = all_data[0][1]
            if isinstance(first_val, list) and len(first_val) == 3:
                header = ["Timestamp_ms", "X", "Y", "Z", "RelativeTime", relative_time, "UTC", utc_time_str]
            else:
                header = ["Timestamp_ms", "Value", "RelativeTime", relative_time, "UTC", utc_time_str]
            writer.writerow(header)

            for ts, val in all_data:
                if isinstance(val, list):
                    writer.writerow([ts] + [f"{v:.6f}" for v in val])
                else:
                    writer.writerow([ts, f"{val:.6f}"])

        print(f"Saved {len(all_data)} samples to {stream_output}")

def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <input_json_file> <output_csv_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    convert_json_to_csv(input_file, output_file)

if __name__ == "__main__":
    main()
