import sys
import json
import os
import csv
from datetime import datetime

ECG_LSB_TO_MV = 0.000381469726563

def convert_json_to_csv(input_file, output_file):
    """
    Convert a JSON file containing sensor data to CSV format.
    
    Args:
        input_file (str): Path to the input JSON file
        output_file (str): Path to the output CSV file
        
    Returns:
        bool: True if conversion was successful, False otherwise
    """

    with open(input_file, 'r') as f:
        print("Parsing JSON...")
        content = json.load(f)
        
        # Find sensor streams
        samples = content["Samples"]
        print(f"Total sample entries: {len(samples)}")

        # Group samples by type
        sample_streams = {}        
        for sample in samples:
            sample_type = list(sample.keys())[0]
            if sample_type not in sample_streams:
                sample_streams[sample_type] = []
            sample_streams[sample_type].append(sample[sample_type])

        print(f"Streams found: {list(sample_streams.keys())}")

        # Process each stream and write to individual CSV
        for stream_name, entries in sample_streams.items():
            print(f"\n{'='*60}")
            print(f"Processing stream: {stream_name}")
            print(f"Number of chunks: {len(entries)}")

            time_detailed = {}
            for sample in samples:
                if "TimeDetailed" in sample:
                    time_detailed = sample["TimeDetailed"]
                    break

            # Handle relative time with error checking
            relative_time = time_detailed.get("relativeTime", "")
            try:
                relative_time = int(relative_time) / 1000 if relative_time else 0
            except (ValueError, TypeError):
                relative_time = 0
                print(f"Warning: Could not parse relativeTime, using 0")

            # Handle UTC time with error checking
            utc_time = time_detailed.get("utcTime", "")
            try:
                utc_time_int = int(utc_time) if utc_time else 0
                if utc_time_int > 0:
                    utc_time = datetime.utcfromtimestamp(utc_time_int / 1000000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                else:
                    utc_time = "N/A"
                    print(f"Warning: No UTC time found, using N/A")
            except (ValueError, TypeError, OSError):
                utc_time = "N/A"
                print(f"Warning: Could not parse utcTime, using N/A")
    
            # Skip non-data entries (like TimeDetailed)
            if not any('Samples' in entry or 'samples' in entry for entry in entries if isinstance(entry, dict)):
                print(f"Skipping {stream_name} - no sample data found")
                continue

            # Collect all samples and timestamps in order
            all_data = []  # List of (timestamp, value) tuples
            prev_dt = 5.0  # Default time step in ms

            for chunk_idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                    
                timestamp = entry.get("Timestamp")
                
                if timestamp is None:
                    print(f"Warning: chunk {chunk_idx} missing Timestamp, skipping")
                    continue

                # Determine the key containing the actual data
                data_keys = [k for k in entry.keys() if k not in ["Timestamp", "timestamp"]]
                if len(data_keys) != 1:
                    print(f"Warning: unexpected data structure in chunk {chunk_idx}: {list(entry.keys())}")
                    continue
                
                data_key = data_keys[0]
                data_array = entry[data_key]

                # If data_array is a nested dict, extract the samples
                if isinstance(data_array, dict):
                    nested_key = list(data_array.keys())[0]
                    print(f"Detected nested structure, using key: {nested_key}")
                    data_array = data_array[nested_key]

                # Check if it's a list
                if not isinstance(data_array, list):
                    print(f"Warning: data is not a list in chunk {chunk_idx}, skipping")
                    continue

                n = len(data_array)
                
                # Calculate time increment between samples
                if chunk_idx + 1 < len(entries):
                    # Use next chunk's timestamp to calculate dt
                    next_entry = entries[chunk_idx + 1]
                    if isinstance(next_entry, dict):
                        next_timestamp = next_entry.get("Timestamp")
                        if next_timestamp and next_timestamp > timestamp:
                            dt = (next_timestamp - timestamp) / n
                            prev_dt = dt  # Store for potential use in last chunk
                        else:
                            dt = prev_dt  # Use previous dt if next timestamp is invalid
                    else:
                        dt = prev_dt
                else:
                    # For last chunk, use the dt from previous chunks
                    dt = prev_dt

                # print(f"  Chunk {chunk_idx}: timestamp={timestamp}, samples={n}, dt={dt:.4f} ms")
                
                # Add each sample with interpolated timestamp
                for i, value in enumerate(data_array):
                    # Convert ECG values if needed
                    if "ECG" in stream_name.upper() and stream_name != "MeasECGmV":
                        value = value * ECG_LSB_TO_MV
                    
                    sample_timestamp = int(timestamp + i * dt)
                    all_data.append((sample_timestamp, value))

                    #print(f"  sample {i:02d}: {sample_timestamp} ms -> {value:.6f} mV")

            print(f"\nTotal samples collected: {len(all_data)}")
            
            if len(all_data) == 0:
                print(f"No valid samples found for {stream_name}, skipping")
                continue

            # Sort by timestamp 
            all_data.sort(key=lambda x: x[0])
            
            # Write to CSV
            print(f"\n Writing to: {output_file}")
            
            with open(output_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["Timestamp_ms", "Value", "Relative Time:", relative_time, "UTC Time:", utc_time])
                print(f"Relative time", relative_time)
                print(f"UTC time", utc_time)
                for ts, val in all_data:
                    writer.writerow([f"{ts}", f"{val:.3f}"])

            print(f"Successfully saved {len(all_data)} samples")
            print(f"\n CSV file saved successfully to: {os.path.abspath(output_file)}")

def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <input_json_file> <output_csv_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    convert_json_to_csv(input_file, output_file)

if __name__ == "__main__":
    main()
