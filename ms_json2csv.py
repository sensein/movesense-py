import sys
import json
import os
import csv
from datetime import datetime

ECG_LSB_TO_MV = 0.000381469726563

def convert_json_to_csv(input_file, output_file):
    """
    Convert a JSON file containing sensor data to CSV format.
    Works for ECG, ACC, GYRO, TEMP, MeasIMU6, MeasIMU9, and similar Meas* streams.
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
        
        # For IMU6/IMU9, we need to split into separate CSV files per sensor type
        if stream_name in ["MeasIMU6", "MeasIMU9"]:
            process_imu_stream(stream_name, entries, output_file, relative_time, utc_time_str)
        elif stream_name == "MeasHR":
            process_hr_stream(stream_name, entries, output_file, relative_time, utc_time_str)
        else:
            process_regular_stream(stream_name, entries, output_file, relative_time, utc_time_str)

def get_missing_value(stream_name):
    """Return appropriate missing value based on stream type."""
    if "ECG" in stream_name.upper():
        return -1.5  # -1.5mV for ECG
    else:
        return 0.0  # Default

def detect_missing_chunks(entries, stream_name, tolerance=1.5):
    """
    Detect missing chunks by analyzing timestamp gaps between consecutive chunks.
    Uses the first two chunks to establish expected chunk interval.
    
    Returns:
        tuple: (missing_chunks list, expected_chunk_dt)
    """
    if len(entries) < 2:
        return [], 0

    timestamps = []
    for entry in entries[:10]:
        ts = entry.get("Timestamp") or entry.get("timestamp")
        if ts is not None:
            timestamps.append(ts)
        if len(timestamps) >= 2:
            break

    if len(timestamps) < 2:
        print(f"  Warning: Cannot determine chunk interval for {stream_name}")
        return [], 0
    
    expected_chunk_dt = timestamps[1] - timestamps[0]
    print(f"  Expected chunk interval for {stream_name}: {expected_chunk_dt} ms")

    missing_chunks = []
    
    # Scan through all chunks to find gaps 
    for i in range(len(entries) - 1):
        current_ts = entries[i].get("Timestamp") or entries[i].get("timestamp")
        next_ts = entries[i + 1].get("Timestamp") or entries[i + 1].get("timestamp")

        if current_ts is None or next_ts is None:
            continue

        gap = next_ts - current_ts

        # If gap is significantly larger than expected, we have missing chunks
        if gap > expected_chunk_dt * tolerance:
            num_missing = int(round((gap - expected_chunk_dt) / expected_chunk_dt))
            print(f"  Missing chunk(s) detected between index {i} and {i+1}")
            print(f"    Current ts: {current_ts}, Next ts: {next_ts}, Gap: {gap:.1f}ms")
            print(f"    Expected interval: {expected_chunk_dt:.1f}ms, Missing chunks: {num_missing}")

            # Store info about where to insert and how many
            for j in range(1, num_missing + 1):
                expected_ts = int(current_ts + j * expected_chunk_dt)
                missing_chunks.append((i, expected_ts))
    
    return missing_chunks


def process_hr_stream(stream_name, entries, output_file, relative_time, utc_time_str):
    """Process heart rate (MeasHR) streams with RR intervals."""
    
    all_data = []
    cumulative_time = 0  # Track cumulative time based on RR intervals
    
    for idx, entry in enumerate(entries):
        average = entry.get("average", 0)
        rr_data = entry.get("rrData", [])
        
        # Each RR interval is in milliseconds
        for rr_ms in rr_data:
            all_data.append({
                "timestamp_ms": cumulative_time,
                "average_hr": average,
                "rr_interval_ms": rr_ms
            })
            cumulative_time += rr_ms
    
    if not all_data:
        print(f"No valid samples found for {stream_name}, skipping.")
        return
    
    base, _ = os.path.splitext(output_file)
    stream_output = f"{base}_{stream_name}.csv"
    
    print(f"Writing {len(all_data)} samples to {stream_output}")
    with open(stream_output, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        
        header = ["Timestamp_ms", "Average_HR", "RR_Interval_ms", "RelativeTime", relative_time, "UTC", utc_time_str]
        writer.writerow(header)
        
        for sample in all_data:
            writer.writerow([
                sample["timestamp_ms"],
                f"{sample['average_hr']:.2f}",
                sample["rr_interval_ms"]
            ])
    
    print(f"Saved {len(all_data)} samples to {stream_output}")

def process_imu_stream(stream_name, entries, output_file, relative_time, utc_time_str):
    """Process IMU6/IMU9 streams which contain multiple sensor arrays."""

    if len(entries) < 2:
        print(f"Warning: Not enough entries in {stream_name} to estimate sampling interval.")
        return

    # --- Determine dt from the first two timestamps ---
    first_ts = entries[0].get("Timestamp") or entries[0].get("timestamp")
    second_ts = None
    for entry in entries[1:]:
        ts = entry.get("Timestamp") or entry.get("timestamp")
        if ts is not None:
            second_ts = ts
            break

    if first_ts is None or second_ts is None or second_ts <= first_ts:
        print(f"Warning: Cannot determine sample interval for {stream_name}")
        return

    expected_chunk_dt = second_ts - first_ts
    print(f"  Estimated chunk-to-chunk interval for {stream_name}: {expected_chunk_dt:.3f} ms")

    # Try to estimate per-sample dt assuming similar sample counts in chunks
    first_chunk = entries[0]
    sample_counts = []
    for v in first_chunk.values():
        if isinstance(v, list):
            sample_counts.append(len(v))
    samples_per_chunk = max(sample_counts) if sample_counts else 1

    dt = expected_chunk_dt / samples_per_chunk
    freq_hz = 1000.0 / dt
    print(f"  Estimated sample interval: {dt:.4f} ms  →  {freq_hz:.2f} Hz")
    
    # Collect data by sensor type (Acc, Gyro, Magn)
    sensor_data = {}
    
    for chunk_idx, entry in enumerate(entries):
        timestamp = entry.get("Timestamp") or entry.get("timestamp")
        
        if timestamp is None:
            print(f"Warning: chunk {chunk_idx} missing Timestamp, skipping")
            continue
        
        # Find all array fields (ArrayAcc, ArrayGyro, ArrayMagn)
        for key, value in entry.items():
            if key.lower() == "timestamp":
                continue
            
            # Extract sensor type from key (e.g., ArrayAcc -> Acc)
            if key.startswith("Array"):
                sensor_type = key[5:]  # Remove "Array" prefix
            else:
                sensor_type = key
            
            if not isinstance(value, list):
                continue
            
            if sensor_type not in sensor_data:
                sensor_data[sensor_type] = []
            
            # Add timestamped samples
            for i, sample in enumerate(value):
                ts = int(timestamp + i * dt)
                if isinstance(sample, dict) and all(k in sample for k in ("x", "y", "z")):
                    sensor_data[sensor_type].append((ts, [sample["x"], sample["y"], sample["z"]]))
                else:
                    sensor_data[sensor_type].append((ts, sample))
    
    # Write separate CSV for each sensor type
    base, _ = os.path.splitext(output_file)
    for sensor_type, data in sensor_data.items():
        if not data:
            continue
        
        data.sort(key=lambda x: x[0])
        stream_output = f"{base}_{stream_name}_{sensor_type}.csv"
        
        print(f"Writing {len(data)} samples to {stream_output}")
        with open(stream_output, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            
            first_val = data[0][1]
            if isinstance(first_val, list) and len(first_val) == 3:
                header = ["Timestamp_ms", "X", "Y", "Z", "RelativeTime", relative_time, "UTC", utc_time_str]
            else:
                header = ["Timestamp_ms", "Value", "RelativeTime", relative_time, "UTC", utc_time_str]
            writer.writerow(header)
            
            for ts, val in data:
                if isinstance(val, list):
                    writer.writerow([ts] + [f"{v:.6f}" for v in val])
                else:
                    writer.writerow([ts, f"{val:.6f}"])
        
        print(f"Saved {len(data)} samples to {stream_output}")

def process_regular_stream(stream_name, entries, output_file, relative_time, utc_time_str):
    """Process regular (non-IMU) data streams, detect sample-level gaps, and fill missing areas with -1.5mV."""

    if not entries:
        print(f"No entries for {stream_name}, skipping.")
        return

    all_data = []
    prev_dt = None  # Sample interval (ms)
    print(f"\nProcessing stream: {stream_name}")

    # Flatten all chunks into (timestamp, value) pairs ---
    for chunk_idx, entry in enumerate(entries):
        # Extract timestamp
        timestamp = entry.get("Timestamp") or entry.get("timestamp")
        if timestamp is None:
            for v in entry.values():
                if isinstance(v, dict) and "Timestamp" in v:
                    timestamp = v["Timestamp"]
                    entry = v
                    break

        if timestamp is None:
            print(f"Chunk {chunk_idx} missing timestamp, skipping.")
            continue

        # --- Identify data field ---
        data_keys = [k for k in entry.keys() if k.lower() != "timestamp"]
        if not data_keys:
            print(f"No data field in chunk {chunk_idx}.")
            continue

        preferred_fields = ["Samples", "Measurement", "ArrayAcc", "ArrayGyro", "ArrayMag"]
        data_key = next((k for k in preferred_fields if k in entry), data_keys[0])
        data_array = entry[data_key]

        # --- Normalize into list of numeric or [x,y,z] samples ---
        if isinstance(data_array, (int, float)):
            values = [data_array]
        elif isinstance(data_array, dict):
            if all(isinstance(v, (int, float)) for v in data_array.values()):
                values = [[data_array.get("x", 0), data_array.get("y", 0), data_array.get("z", 0)]]
            else:
                values = []
                for v in data_array.values():
                    if isinstance(v, list):
                        values.extend(v)
        elif isinstance(data_array, list):
            if len(data_array) > 0 and isinstance(data_array[0], dict) and all(k in data_array[0] for k in ("x", "y", "z")):
                values = [[i["x"], i["y"], i["z"]] for i in data_array]
            else:
                values = data_array
        else:
            print(f" Unsupported data format in chunk {chunk_idx}, skipping.")
            continue

        n = len(values)
        if n == 0:
            continue

        # --- Estimate dt (per-sample interval) if not yet known ---
        if prev_dt is None and chunk_idx + 1 < len(entries):
            next_ts = entries[chunk_idx + 1].get("Timestamp") or entries[chunk_idx + 1].get("timestamp")
            if next_ts and next_ts > timestamp:
                prev_dt = (next_ts - timestamp) / n
                print(f" Estimated sample interval: {prev_dt:.3f} ms ({1000/prev_dt:.2f} Hz)")
            else:
                prev_dt = 5.0  # fallback
        elif prev_dt is None:
            prev_dt = 5.0

        # --- Append all samples with estimated timestamps ---
        for i, val in enumerate(values):
            ts = int(timestamp + i * prev_dt)
            all_data.append((ts, val))

    if not all_data:
        print(f"No valid samples found for {stream_name}, skipping.")
        return

    # --- Sort samples by timestamp ---
    all_data.sort(key=lambda x: x[0])

    # --- Fill missing gaps ---
    filled_data = []
    is_ecg = "ECG" in stream_name.upper()

    filled_data.append(all_data[0])
    prev_ts = all_data[0][0]

    if is_ecg:
        missing_value = get_missing_value(stream_name)
        for ts, val in all_data[1:]:
            gap = ts - prev_ts
            if gap > prev_dt * 1.5:
                num_missing = int((gap // prev_dt) - 1)
                print(f" ECG gap detected: {gap:.1f}ms → inserting {num_missing} missing samples (from {prev_ts} to {ts})")
                for i in range(num_missing):
                    prev_ts += prev_dt
                    filled_data.append((int(prev_ts), missing_value))
            filled_data.append((ts, val))
            prev_ts = ts
    else:
        # For non-ECG, just keep samples as-is (no gap filling)
        filled_data.extend(all_data[1:])
        print(" Non-ECG stream: skipping gap filling.")

    print(f"  Total samples (after filling): {len(filled_data)}")

    # --- Write output CSV ---
    base, _ = os.path.splitext(output_file)
    stream_output = f"{base}_{stream_name}.csv"

    with open(stream_output, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)

        first_val = filled_data[0][1]
        if isinstance(first_val, list) and len(first_val) == 3:
            header = ["Timestamp_ms", "X", "Y", "Z", "RelativeTime", relative_time, "UTC", utc_time_str]
        else:
            header = ["Timestamp_ms", "Value", "RelativeTime", relative_time, "UTC", utc_time_str]
        writer.writerow(header)

        for ts, val in filled_data:
            if isinstance(val, list):
                writer.writerow([ts] + [f"{v:.6f}" for v in val])
            else:
                writer.writerow([ts, f"{val:.6f}"])

    print(f"Saved {len(filled_data)} samples to {stream_output}\n")

def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <input_json_file> <output_csv_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    convert_json_to_csv(input_file, output_file)

if __name__ == "__main__":
    main()