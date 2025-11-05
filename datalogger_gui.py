import logging
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from async_tkinter_loop import async_handler, async_mainloop
from csv2edf import csv_to_edf_plus
from ms_json2csv import convert_json_to_csv
from PIL import Image, ImageTk
import subprocess
import os
import sys
import io
import json
from contextlib import redirect_stdout
import datalogger_tool as tool  

class DataloggerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Movesense Flash Datalogger Tool")
        self.root.geometry("800x600")

        self.logging_configured = False
        
        # Load and display the logo
        try:
            logo_path = os.path.join(os.path.dirname(__file__), "Movesense logomark white.png")
            logo_image = Image.open(logo_path)
            # Resize the image
            logo_image = logo_image.resize((70, 45), Image.Resampling.LANCZOS)
            self.logo_photo = ImageTk.PhotoImage(logo_image)
            
            # Create and configure a label for the logo
            self.logo_label = ttk.Label(self.root, image=self.logo_photo)
            self.logo_label.grid(row=0, column=1, padx=(0, 30), pady=(10, 0), sticky=tk.NE) 
        except Exception as e:
            print(f"Could not load logo: {e}")
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=0)  
        self.root.rowconfigure(1, weight=1)
        
        # Create main container
        main_frame = ttk.Frame(root, padding="10")
        main_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))  
        main_frame.columnconfigure(0, weight=1)
        
        # Serial Numbers Section
        serial_frame = ttk.LabelFrame(main_frame, text="Device Serial Number", padding="10")
        serial_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        serial_frame.columnconfigure(1, weight=1)
        
        ttk.Label(serial_frame, text="1.    Serial Number:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.serial_entry = ttk.Entry(serial_frame, width=50)
        self.serial_entry.grid(row=0, column=1, sticky=(tk.W, tk.E))
        ttk.Label(serial_frame, text="(e.g., 254230002030)", 
                 font=("", 8), foreground="gray").grid(row=1, column=1, sticky=tk.W)
        
        # Verbose checkbox
        self.verbose_var = tk.BooleanVar()
        self.verbose_check = ttk.Checkbutton(
            serial_frame,
            text="Verbose logging",
            variable=self.verbose_var
        )
        self.verbose_check.grid(row=0, column=2, padx=(10, 0))
        self.verbose_check.grid_remove()
        
        # Commands Section
        cmd_frame = ttk.LabelFrame(main_frame, text="Commands", padding="10")
        cmd_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        # Configure columns
        cmd_frame.columnconfigure(0, minsize=25)   # narrow column for numbers
        cmd_frame.columnconfigure(1, weight=0)     # buttons
        cmd_frame.columnconfigure(2, weight=1)     # text fields, labels, frames stretch
        
        # Row 0: Connect / Status
        ttk.Label(cmd_frame, text="2.").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Connect", command=self.check_status, width=20).grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(cmd_frame, text="Check device connection and info").grid(row=0, column=2, sticky=tk.W, padx=5)
        
        # Row 1: Config
        self.config_button = ttk.Button(cmd_frame, text="Configure Logging", command=self.configure_logging, width=20)
        self.config_entry = ttk.Entry(cmd_frame, width=30)
        self.config_entry.grid(row=0, column=4, sticky=(tk.W, tk.E), padx=5)
        self.config_entry.insert(0, "/Meas/ECG/200/mV")
        self.config_button.grid_remove()
        self.config_entry.grid_remove()
        
        # Row 3: Start Logging
        ttk.Label(cmd_frame, text="3.").grid(row=3, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Start Logging", command=self.start_logging, width=20).grid(row=3, column=1, padx=5, pady=5)
        ttk.Label(cmd_frame, text="Begin data logging").grid(row=3, column=2, sticky=tk.W, padx=5)

        # Row 3: Stop Logging
        ttk.Label(cmd_frame, text="4.").grid(row=4, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Stop Logging", command=self.stop_logging, width=20).grid(row=4, column=1, padx=5, pady=5)
        ttk.Label(cmd_frame, text="Stop the logging process").grid(row=4, column=2, sticky=tk.W, padx=5)
        
        # Row 4: Fetch
        ttk.Label(cmd_frame, text="5.").grid(row=5, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Load Data", command=self.fetch_data, width=20).grid(row=5, column=1, padx=5, pady=5)

        fetch_frame = ttk.Frame(cmd_frame)
        fetch_frame.grid(row=5, column=2, columnspan=3, sticky=(tk.W, tk.E), padx=5)
        cmd_frame.columnconfigure(2, weight=1)  
        fetch_frame.columnconfigure(1, weight=1) 

        ttk.Label(fetch_frame, text="Path:").grid(row=0, column=0, padx=(0, 5))
        self.output_entry = ttk.Entry(fetch_frame, width=150) 
        self.output_entry.grid(row=0, column=1, sticky="ew")  
        ttk.Button(fetch_frame, text="Browse...", command=self.browse_output).grid(row=0, column=2, padx=(5, 0))
        
        # Output Section
        output_frame = ttk.LabelFrame(main_frame, text="Output", padding="10")
        output_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.output_text = scrolledtext.ScrolledText(output_frame, height=10, width=80, wrap=tk.WORD, state='disabled')
        self.output_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Clear output button
        ttk.Button(output_frame, text="Clear Output", command=self.clear_output).grid(row=1, column=0, pady=(5, 0))
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=3, column=0, sticky=(tk.W, tk.E))

        # Erase Memory
        erase_frame = ttk.Frame(main_frame, padding="5")
        erase_frame.grid(row=4, column=0, sticky=(tk.W, tk.E))  
        erase_frame.columnconfigure(0, weight=1)  

        # Erase button
        ttk.Button(erase_frame, text="Erase Memory", command=self.erase_memory, width=20).grid(row=0, column=1, padx=5, sticky=tk.E)

        # Force checkbox 
        self.force_var = tk.BooleanVar()
        self.force_check = ttk.Checkbutton(erase_frame, text="Force (skip confirmation)", variable=self.force_var)
        self.force_check.grid(row=0, column=2, padx=5, sticky=tk.E)
        self.force_check.grid_remove()
        
        # Configure row weights for resizing
        main_frame.rowconfigure(2, weight=1)
    
    def log_output(self, message, newline=True):
        """Add message to output text widget"""
        # Filter out specific error messages
        lines_to_filter = [
            "TimelineJsonFormatter::embedAttributes: invalid map<K, T> key"
        ]
        
        # Check if message should be filtered
        for filter_text in lines_to_filter:
            if filter_text in message:
                return 
            
        self.output_text.configure(state='normal')
        if newline:
            self.output_text.insert(tk.END, message + "\n")
        else:
            self.output_text.insert(tk.END, message)
        self.output_text.see(tk.END)
        self.output_text.configure(state='disabled')
    
    def clear_output(self):
        """Clear the output text widget"""
        self.output_text.configure(state='normal')
        self.output_text.delete(1.0, tk.END)
        self.output_text.configure(state='disabled')
    
    def get_serial_number(self):
        """Get serial number from entry field"""
        serial = self.serial_entry.get().strip()
        if not serial:
            messagebox.showwarning("Warning", "Please enter a serial number")
            return None
        return serial
    
    def logging_data(self):
        """Print dots to show logging is active"""
        if hasattr(self, 'logging_active') and self.logging_active:
            # Alternate between two different marks
            if not hasattr(self, 'logging_counter'):
                self.logging_counter = 0
            
            mark = "*" if self.logging_counter % 2 == 0 else "."
            self.log_output(mark, newline=False)
            self.logging_counter += 1
            
            self.root.after(2000, self.logging_data)

        if hasattr(self, 'fetching_active') and self.fetching_active:
            # Alternate between two different marks
            if not hasattr(self, 'logging_counter'):
                self.logging_counter = 0
            
            mark = "*" if self.logging_counter % 2 == 0 else "."
            self.log_output(mark, newline=False)
            self.logging_counter += 1
            
            self.root.after(2000, self.logging_data)

    def extract_utc_time_from_json(self, json_file):
        """Extract UTC time from JSON file"""
        try:
            with open(json_file, 'r') as f:
                content = json.load(f)
                time_detailed = {}
                samples = content.get("Samples", [])
                for sample in samples:
                    if "TimeDetailed" in sample:
                        time_detailed = sample["TimeDetailed"]
                        break
                utc_time = time_detailed.get("utcTime", "")
                if utc_time:
                    utc_time = int(utc_time)
                    from datetime import datetime
                    utc_time_str = datetime.utcfromtimestamp(utc_time / 1000000).strftime('%Y-%m-%d_%H%M%S')
                    return utc_time_str
        except Exception as e:
            self.root.after(0, self.log_output, f"Error extracting UTC time from JSON: {str(e)}\n")
        return None

    def rename_files_with_utc(self, base_file, utc_time):
        """Rename related files (JSON, CSV, EDF) to include UTC time"""
        try:
            dir_path = os.path.dirname(base_file)
            base_name = os.path.splitext(os.path.basename(base_file))[0]
            
            parts = base_name.split('_')
            if len(parts) >= 4:
                new_base = '_'.join(parts[:4])
                new_name = f"{new_base}_{utc_time}"
                
                # Check if already correctly named
                if base_name == new_name:
                    self.root.after(0, self.log_output, 
                        f"File already has correct UTC timestamp: {base_name}\n")
                    return base_file
                
                # Only handle JSON, CSV, and EDF (not SBEM)
                extensions = ['.json', '.csv', '.edf']
                
                for ext in extensions:
                    old_file = os.path.join(dir_path, f"{base_name}{ext}")
                    new_file = os.path.join(dir_path, f"{new_name}{ext}")
                    
                    if os.path.exists(old_file):
                        if old_file == new_file:
                            # Already correctly named
                            continue
                        
                        if os.path.exists(new_file):
                            # Target already exists - don't rename, just delete the duplicate
                            self.root.after(0, self.log_output, 
                                f"Target exists, removing duplicate: {os.path.basename(old_file)}\n")
                            os.remove(old_file)
                        else:
                            # Rename to correct name
                            os.rename(old_file, new_file)
                            self.root.after(0, self.log_output, 
                                f"Renamed: {os.path.basename(old_file)} -> {os.path.basename(new_file)}\n")
                
                return new_file
                    
        except Exception as e:
            self.root.after(0, self.log_output, f"Error renaming files: {str(e)}\n")
            return None
    
    @async_handler
    async def check_status(self):
        if self.verbose_var.get():
            logging.getLogger().setLevel(logging.DEBUG)
        """Check device status"""
        try:
            serial = self.serial_entry.get().strip()
            
            self.root.after(0, self.log_output, "Connecting sensor and loading status...")
            self.root.after(0, self.status_var.set, "Connecting sensor and loading status.")
                
            # Capture stdout to show in GUI
            output = io.StringIO()
            with redirect_stdout(output):
                status = await tool.fetch_status(serial=serial, args=None)
                print(f"Device {serial} status:")
                print(f"  Protocol version: {status.get('protocol_version', 'Unknown')}")
                print(f"  Serial number: {status.get('serial_number', 'Unknown')}")
                print(f"  Product name: {status.get('product_name', 'Unknown')}")
                print(f"  App name: {status.get('app_name', 'Unknown')}")
                print(f"  App version: {status.get('app_version', 'Unknown')}")
                print(f"  DataLogger state: {tool.DL_STATES[status.get('dlstate', 1)]}")
            
            # Update GUI with captured output
            self.root.after(0, self.log_output, output.getvalue())
            self.root.after(0, self.status_var.set, "Status check completed.")
                
        except Exception as e:
            self.root.after(0, self.log_output, f"\nError: {str(e)}\n")
            self.root.after(0, self.status_var.set, "Error occurred")
    
    
    @async_handler
    async  def configure_logging(self):
        """Configure logging paths"""
        if self.verbose_var.get():
            logging.getLogger().setLevel(logging.DEBUG)
            
        try:
            paths = self.config_entry.get().strip().split()

            self.root.after(0, self.log_output, "Configure logging started...")
            self.root.after(0, self.status_var.set, "Configure logging started.")

            if not paths:
                messagebox.showwarning("Warning", "Please enter at least one resource path")
                return
            serial = self.serial_entry.get().strip()
            
            # Capture stdout to show in GUI
            output = io.StringIO()
            with redirect_stdout(output):
                await tool.configure_device(serial, paths=paths)
                print(f"Logging configured for device {serial} with paths: {paths}")
            
            # Update GUI with captured output
            self.root.after(0, self.log_output, output.getvalue())
            self.root.after(0, self.status_var.set, "Logging configured")
            self.logging_configured = True
                
        except Exception as e:
            self.root.after(0, self.log_output, f"\nError: {str(e)}\n")
            self.root.after(0, self.status_var.set, "Error occurred")
        
    @async_handler
    async def start_logging(self):
        """Start logging"""
        if self.verbose_var.get():
            logging.getLogger().setLevel(logging.DEBUG)
        try:
            serial = self.serial_entry.get().strip()

            if not self.logging_configured:
                self.log_output("Configuring and starting logging...")
                self.root.after(0, self.status_var.set, "Configuring and starting logging.")
                
                # Get config paths
                paths = self.config_entry.get().strip().split()
                if not paths:
                    messagebox.showwarning("Warning", "Please enter at least one resource path")
                    return
                
                # Capture stdout to show in GUI
                output = io.StringIO()
                with redirect_stdout(output):
                    # Configure logging
                    await tool.configure_device(serial, paths=paths)
                    self.logging_configured = True
                    # Start logging immediately after configuration
                    await tool.start_logging(serial, args=None)
                
                # Update GUI with captured output
                self.root.after(0, self.log_output, output.getvalue())
                self.root.after(0, self.log_output, f"Logging started on device {serial}. Recording data...")
                self.root.after(0, self.status_var.set, "Logging started.")

                # Set logging active flag 
                self.logging_active = True
                self.root.after(2000, self.logging_data)

            else:
                self.root.after(0, self.log_output, f"Starting logging on device {serial}...")
                # Start logging if already configured
                output = io.StringIO()
                with redirect_stdout(output):
                    await tool.start_logging(serial, args=None)
                self.root.after(0, self.log_output, output.getvalue())
                self.root.after(0, self.log_output, f"Logging started on device {serial}. Recording data...")
                self.root.after(0, self.status_var.set, "Logging started.")

                # Set logging active flag 
                self.logging_active = True
                self.root.after(2000, self.logging_data)
                
        except Exception as e:
            self.root.after(0, self.log_output, f"Error: {str(e)}\n")
            self.root.after(0, self.status_var.set, "Error occurred")

    @async_handler
    async def stop_logging(self):
        """Stop logging"""
        if self.verbose_var.get():
            logging.getLogger().setLevel(logging.DEBUG)
        try:
            # Stop the dots
            self.logging_active = False
            # Capture stdout to show in GUI
            output = io.StringIO()
            serial = self.serial_entry.get().strip()
            self.root.after(0, self.log_output, f"\nStopping logging on device {serial}...")
            self.root.after(0, self.status_var.set, "Stopping logging.")
            with redirect_stdout(output):
                await tool.stop_logging(serial=serial, args=None)
            
            # Update GUI with captured output
            self.root.after(0, self.log_output, output.getvalue())
            self.root.after(0, self.log_output, f"Logging stopped on device {serial}")
            self.root.after(0, self.status_var.set, "Logging stopped")
                
        except Exception as e:
            self.logging_active = False
            self.root.after(0, self.log_output, f"Error: {str(e)}\n")
            self.root.after(0, self.status_var.set, "Error occurred")
        
    @async_handler
    async def fetch_data(self):
        """Fetch data from devices"""
        if self.verbose_var.get():
            logging.getLogger().setLevel(logging.DEBUG)

        # Get output directory and convert to absolute path
        output_dir = os.path.abspath(self.output_entry.get().strip())
        # Create directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        self.root.after(0, self.log_output, f"Using directory: {output_dir}\n")

        if not output_dir:
            messagebox.showwarning("Warning", "Please specify an output directory")
            return

        try:
            serial = self.serial_entry.get().strip()
            self.root.after(0, self.log_output, f"\nLoading data from device {serial}.")
            self.root.after(0, self.status_var.set, "Loading data from device")

            # Set logging active flag 
            self.fetching_active = True
            self.root.after(2000, self.logging_data)

            # Capture stdout to show in GUI
            output = io.StringIO()
            with redirect_stdout(output):
                # Step 1: Fetch data
                await tool.fetch_data(serial=serial, args=None, output_dir=output_dir)
                self.logging_configured = False
                
            # Update GUI with fetch output
            self.root.after(0, self.log_output, output.getvalue())
            self.root.after(0, self.log_output, "\n Logging completed.")
        
            # Step 2: Convert SBEM to JSON
            self.root.after(0, self.log_output, "\n--- Converting SBEM to JSON ---")
            self.root.after(0, self.status_var.set, "Converting SBEM to JSON...")

            # Create sbem-files folder if it doesn't exist
            sbem_folder = os.path.join(output_dir, "sbem-files")
            if not os.path.exists(sbem_folder):
                os.makedirs(sbem_folder)
                self.root.after(0, self.log_output, f"Created folder: {sbem_folder}")
            
            # Find all .sbem files in output directory
            sbem_files = []
            for root_dir, dirs, files in os.walk(output_dir):
                if 'sbem-files' in root_dir:
                    continue
                for file in files:
                    if file.endswith('.sbem'):
                        sbem_files.append(os.path.join(root_dir, file))
            
            if not sbem_files:
                self.root.after(0, self.log_output, "No SBEM files found to convert.")
            else:
                for sbem_file in sbem_files:
                    self.root.after(0, self.log_output, f"Converting: {sbem_file}")

                    # Get directory and filename
                    original_dir = os.path.dirname(sbem_file)
                    sbem_filename = os.path.basename(sbem_file)
                    
                    # Create output JSON filename in the original location
                    json_filename = os.path.splitext(sbem_filename)[0] + '.json'
                    json_file = os.path.join(original_dir, json_filename)

                    # For sbem2json.exe
                    if getattr(sys, 'frozen', False):
                        application_path = sys._MEIPASS
                    else:
                        application_path = os.path.dirname(os.path.abspath(__file__))

                    sbem2json_exe = os.path.join(application_path, "sbem2json.exe")
                    
                    # Call sbem2json.exe - convert from original location
                    converter_cmd = [sbem2json_exe, "--sbem2json", sbem_file, "--output", json_file]
                    
                    conv_process = subprocess.Popen(
                        converter_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True
                    )
                    
                    conv_output, _ = conv_process.communicate()
                    if conv_output:
                        self.root.after(0, self.log_output, conv_output)
                    
                    if conv_process.returncode != 0:
                        self.root.after(0, self.log_output, 
                            f"Warning: Conversion failed for {sbem_filename}\n")
                    else:
                        self.root.after(0, self.log_output, 
                            f"Created: {json_file}\n")
                        
                        # Extract UTC time and rename files after successful conversion
                        utc_time = self.extract_utc_time_from_json(json_file)
                        if utc_time:
                            self.root.after(0, self.log_output, f"UTC time from file: {utc_time}")
                            # Rename all related files with UTC time
                            self.rename_files_with_utc(json_file, utc_time)
                        
                        # Move SBEM file to sbem-files folder after successful conversion
                        new_sbem_path = os.path.join(sbem_folder, sbem_filename)
                        try:
                            if os.path.exists(new_sbem_path):
                                # Delete the sbem file since it archived
                                os.remove(sbem_file)
                                self.root.after(0, self.log_output, 
                                    f"Removed SBEM (already archived): {sbem_filename}")
                            else:
                                # Move to sbem-files folder
                                os.rename(sbem_file, new_sbem_path)
                                self.root.after(0, self.log_output, 
                                    f"Moved SBEM to: {new_sbem_path}")
                        except Exception as e:
                            self.root.after(0, self.log_output, f"Warning: Could not move SBEM file: {str(e)}")
            
            # Step 3: Convert JSON to CSV
            self.root.after(0, self.log_output, "\n--- Converting JSON to CSV ---")
            self.root.after(0, self.status_var.set, "Converting JSON to CSV...")
            
            # Find all .json files in output directory
            json_files = []
            for root_dir, dirs, files in os.walk(output_dir):
                # Skip virtual environment and site-packages directories
                if 'venv' in root_dir or '.venv' in root_dir or 'site-packages' in root_dir:
                    continue
                for file in files:
                    if file.endswith('.json'):
                        json_path = os.path.join(root_dir, file)
                        # Check if corresponding CSV already exists
                        csv_path = os.path.splitext(json_path)[0] + '.csv'
                        if os.path.exists(csv_path):
                            self.root.after(0, self.log_output, 
                                f"Skipping {file} - CSV already exists")
                        else:
                            json_files.append(json_path)
            
            if not json_files:
                self.root.after(0, self.log_output, "No JSON files found to convert")
            else:
                for json_file in json_files:
                    self.root.after(0, self.log_output, f"Converting: {json_file}")
                    
                    # Create output CSV filename
                    csv_file = os.path.splitext(json_file)[0] + '.csv'

                    try: 
                        convert_json_to_csv(input_file=json_file, 
                                    output_file=csv_file)

                        self.root.after(0, self.log_output, f"Created: {csv_file}")

                    except Exception as e:
                        self.root.after(0, self.log_output, 
                            f"Warning: CSV conversion failed for {json_file}: {str(e)}")
                        
            # Step 4: Convert CSV to EDF
            self.root.after(0, self.log_output, "\n--- Converting CSV to EDF ---")
            self.root.after(0, self.status_var.set, "Converting CSV to EDF...")

            # Find ECG-related CSV files in output directory
            csv_files = []
            csv_files_total = 0
            for root_dir, dirs, files in os.walk(output_dir):
                if 'venv' in root_dir or 'site-packages' in root_dir:
                    continue  # Skip virtual environment and site-packages directories
                for file in files:
                    if file.endswith('.csv') and ('log_' in file.lower() or 'ecg' in file.lower()):
                        csv_files_total += 1
                        csv_path = os.path.join(root_dir, file)
                        # Check if corresponding EDF already exists
                        edf_path = os.path.splitext(csv_path)[0] + '.edf'
                        if os.path.exists(edf_path):
                            self.root.after(0, self.log_output, 
                                f"Skipping {file} - EDF already exists")
                        else:
                            csv_files.append(csv_path)

            if not csv_files:
                if csv_files_total == 0:
                    self.root.after(0, self.log_output, "No CSV files found in directory")
                else:
                    self.root.after(0, self.log_output, 
                        f"All CSV files already converted. No new EDF files to create.")
            else:
                for csv_file in csv_files:
                    self.root.after(0, self.log_output, f"Converting: {csv_file}")
                    
                    # Create output EDF filename 
                    edf_file = os.path.splitext(csv_file)[0] + '.edf'

                    try: 
                        csv_to_edf_plus(csv_filename=csv_file, 
                                    edf_filename=edf_file, 
                                    sampling_freq=None, 
                                    unit='mV', 
                                    scale_factor=1)

                        self.root.after(0, self.log_output, f"Created: {edf_file}")

                    except Exception as e:
                        self.root.after(0, self.log_output, 
                            f"Warning: EDF conversion failed for {csv_file}: {str(e)}")

            # Stop the dots
            self.fetching_active = False

            # All done
            self.root.after(0, self.status_var.set, "All conversions completed.")
            self.root.after(0, self.log_output, "All conversions completed.")
            self.root.after(0, self.log_output, "\nDone!")

        
        except Exception as e:
            self.root.after(0, self.log_output, f"\nError: {str(e)}")
            self.root.after(0, self.status_var.set, "Error occurred")
    
    @async_handler
    async def erase_memory(self):
        """Erase device memory"""
        if self.verbose_var.get():
            logging.getLogger().setLevel(logging.DEBUG)
        if not self.force_var.get():
            result = messagebox.askyesno(
                "Confirm Erase",
                "Are you sure you want to erase all logged data?\nThis action cannot be undone!"
            )
            if not result:
                self.log_output("\nMemory erase cancelled by user\n")
                return
        try:
            # Get serial number first
            serial = self.serial_entry.get().strip()
            if not serial:
                self.root.after(0, lambda: messagebox.showerror("Error", "Please enter a serial number"))
                return
            
            # Update status
            self.root.after(0, self.status_var.set, "Connecting to device...")
            self.root.after(0, self.log_output, f"Attempting to connect to device {serial}...")
            self.root.after(0, self.log_output, f"Erasing memory on device {serial}...")
            self.root.after(0, self.status_var.set, "Erasing memory on device.")
            
            # Always use force=True as required by the device protocol
            output = io.StringIO()
            with redirect_stdout(output):
                await tool.erase_memory(serial=serial)
            
            # Update GUI with captured output
            self.root.after(0, self.log_output, output.getvalue())
            self.root.after(0, self.log_output, "Memory erased successfully")
            self.root.after(0, self.status_var.set, "Memory erased")
                
        except Exception as e:
            error_msg = str(e)
            self.root.after(0, self.log_output, f"\nError: {error_msg}")
            self.root.after(0, self.status_var.set, "Error occurred")
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to erase memory: {error_msg}"))

    
    def browse_output(self):
        """Browse for output directory"""
        directory = filedialog.askdirectory(title="Select Output Directory")
        if directory:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, directory)

root = tk.Tk()
app = DataloggerGUI(root)
async_mainloop(root)

