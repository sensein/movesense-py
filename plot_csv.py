import sys
import pandas as pd
import matplotlib.pyplot as plt
import os

# --- Get CSV filename from terminal ---
csv_file = sys.argv[1]

# --- Verify file exists ---
if not os.path.exists(csv_file):
    print(f"Error: File '{csv_file}' not found.")
    sys.exit(1)

# --- Load CSV data ---
data = pd.read_csv(csv_file)

# --- Convert timestamp if needed ---
if 'Timestamp_ms' in data.columns:
    data['Timestamp_ms'] = data['Timestamp_ms'] / 1000  # convert to seconds

# --- Plotting ---
plt.figure(figsize=(12, 6))
plt.plot(data['Timestamp_ms'], data['Value'], label='ECG Signal', color='blue')

plt.ylim(-2, 2)
plt.title('ECG Signal from CSV')
plt.xlabel('Time (s)')
plt.ylabel('Voltage (mV)')
plt.grid(True)
plt.legend()
plt.show()