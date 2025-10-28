import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import subprocess
import threading
import os
import sys

if sys.platform == 'win32':
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

class DataloggerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Movesense Flash Datalogger Tool")
        self.root.geometry("800x600")
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        
        # Create main container
        main_frame = ttk.Frame(root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        main_frame.columnconfigure(0, weight=1)
        
        # Serial Numbers Section
        serial_frame = ttk.LabelFrame(main_frame, text="Device Serial Number", padding="10")
        serial_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        serial_frame.columnconfigure(1, weight=1)
        
        ttk.Label(serial_frame, text="1.    Serial Number:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.serial_entry = ttk.Entry(serial_frame, width=50)
        self.serial_entry.grid(row=0, column=1, sticky=(tk.W, tk.E))
        # self.serial_entry.insert(0, "last five digits from serial number")
        ttk.Label(serial_frame, text="(e.g., 254230002030)", 
                 font=("", 8), foreground="gray").grid(row=1, column=1, sticky=tk.W)
        
        # Verbose checkbox
        # self.verbose_var = tk.BooleanVar()
        # ttk.Checkbutton(serial_frame, text="Verbose logging", 
        #                variable=self.verbose_var).grid(row=0, column=2, padx=(10, 0))
        
        # Commands Section
        cmd_frame = ttk.LabelFrame(main_frame, text="Commands", padding="10")
        cmd_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        # Configure columns once at the top
        cmd_frame.columnconfigure(0, minsize=25)   # narrow column for numbers
        cmd_frame.columnconfigure(1, weight=0)     # buttons
        cmd_frame.columnconfigure(2, weight=1)     # text fields, labels, frames stretch
        
        # Row 0: Connect / Status
        ttk.Label(cmd_frame, text="2.").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Connect",
                command=self.check_status, width=20).grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(cmd_frame, text="Check device connection and info").grid(row=0, column=2, sticky=tk.W, padx=5)
        
        # Row 1: Config
        # ttk.Button(cmd_frame, text="Configure Logging", 
        #           command=self.configure_logging, width=20).grid(row=0, column=2, padx=5, pady=5)
        # self.config_entry = ttk.Entry(cmd_frame, width=30)
        # self.config_entry.grid(row=0, column=3, sticky=(tk.W, tk.E), padx=5)
        # self.config_entry.insert(0, "/Meas/ECG/200/mV")
        # self.config_entry.state(["disabled"])
        # ttk.Label(cmd_frame, text="Resource paths (space-separated)", 
        #          font=("", 8), foreground="gray").grid(row=2, column=1, sticky=tk.W, padx=5)
        
        # Row 3: Start/Stop
        # button_frame = ttk.Frame(cmd_frame)
        # button_frame.grid(row=3, column=0, columnspan=2, pady=30)
        # ttk.Button(button_frame, text="Start Logging", 
        #           command=self.start_logging, width=20).pack(side=tk.LEFT, padx=5)
        # ttk.Button(button_frame, text="Stop Logging", 
        #           command=self.stop_logging, width=20).pack(side=tk.LEFT, padx=5)
        # Row 3: Start Logging
        ttk.Label(cmd_frame, text="3.").grid(row=3, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Start Logging",
                command=self.start_logging, width=20).grid(row=3, column=1, padx=5, pady=5)
        ttk.Label(cmd_frame, text="Begin data logging").grid(row=3, column=2, sticky=tk.W, padx=5)

        # Row 3: Stop Logging
        ttk.Label(cmd_frame, text="4.").grid(row=4, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Stop Logging",
                command=self.stop_logging, width=20).grid(row=4, column=1, padx=5, pady=5)
        ttk.Label(cmd_frame, text="Stop the logging process").grid(row=4, column=2, sticky=tk.W, padx=5)
        
        # Row 4: Fetch
        ttk.Label(cmd_frame, text="5.").grid(row=5, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Button(cmd_frame, text="Load Data",
                command=self.fetch_data, width=20).grid(row=5, column=1, padx=5, pady=5)

        fetch_frame = ttk.Frame(cmd_frame)
        fetch_frame.grid(row=5, column=2, sticky=(tk.W, tk.E), padx=5)
        ttk.Label(fetch_frame, text="Path:").pack(side=tk.LEFT, padx=(0, 5))
        self.output_entry = ttk.Entry(fetch_frame)
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.output_entry.insert(0, "./data")
        ttk.Button(fetch_frame, text="Browse...",
                command=self.browse_output).pack(side=tk.LEFT, padx=(5, 0))
        
        # Row 5: Erase Memory
        # ttk.Button(cmd_frame, text="Erase Memory", 
        #           command=self.erase_memory, width=20).grid(row=5, column=0, padx=5, pady=5)
        # self.force_var = tk.BooleanVar()
        # ttk.Checkbutton(cmd_frame, text="Force (skip confirmation)", 
        #                variable=self.force_var).grid(row=5, column=1, sticky=tk.W, padx=5)
        
        # Output Section
        output_frame = ttk.LabelFrame(main_frame, text="Output", padding="10")
        output_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        
        self.output_text = scrolledtext.ScrolledText(output_frame, height=10, width=80, 
                                                     wrap=tk.WORD, state='disabled')
        self.output_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Clear output button
        ttk.Button(output_frame, text="Clear Output", 
                  command=self.clear_output).grid(row=1, column=0, pady=(5, 0))
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, 
                              relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=3, column=0, sticky=(tk.W, tk.E))

        # Erase Memory
        erase_frame = ttk.Frame(main_frame, padding="5")
        erase_frame.grid(row=4, column=0, sticky=(tk.W, tk.E))  # frame spans full width
        erase_frame.columnconfigure(0, weight=1)  # push content to the right

        ttk.Button(erase_frame, text="Erase Memory",
                command=self.erase_memory, width=20).grid(row=0, column=1, padx=5, sticky=tk.E)
        # self.force_var = tk.BooleanVar()
        # ttk.Checkbutton(erase_frame, text="Force (skip confirmation)",
        #                 variable=self.force_var).grid(row=0, column=2, padx=5, sticky=tk.E)
        
        # Configure row weights for resizing
        main_frame.rowconfigure(2, weight=1)
    
    def log_output(self, message):
        """Add message to output text widget"""
        self.output_text.configure(state='normal')
        self.output_text.insert(tk.END, message + "\n")
        self.output_text.see(tk.END)
        self.output_text.configure(state='disabled')
    
    def clear_output(self):
        """Clear the output text widget"""
        self.output_text.configure(state='normal')
        self.output_text.delete(1.0, tk.END)
        self.output_text.configure(state='disabled')
    
    def get_serial_numbers(self):
        """Get serial numbers from entry field"""
        serials = self.serial_entry.get().strip().split()
        if not serials:
            messagebox.showwarning("Warning", "Please enter at least one serial number")
            return None
        return serials
    
    def build_command(self, command, extra_args=None):
        """Build command list for subprocess"""

        # Determine if we're running as a bundled executable
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            application_path = sys._MEIPASS  # PyInstaller extraction path
            python_exe = "pythonw.exe"
        else:
            # Running as normal Python script
            application_path = os.path.dirname(os.path.abspath(__file__))
            python_exe = "python"
        
        # Build path to datalogger_tool.py
        datalogger_script = os.path.join(application_path, "datalogger_tool.py")
        
        cmd = [python_exe, datalogger_script]
        
        # if self.verbose_var.get():
        #     cmd.append("-V")
        
        cmd.append(command)
        
        serials = self.get_serial_numbers()
        if serials is None:
            return None
        
        cmd.extend(["-s"] + serials)
        
        if extra_args:
            cmd.extend(extra_args)
        
        return cmd
    
    def run_command(self, cmd):
        """Run command in subprocess and display output"""
        if cmd is None:
            return
        
        self.log_output(f"\n{'='*60}")
        self.log_output(f"Running: {' '.join(cmd)}")
        self.log_output(f"{'='*60}\n")
        self.status_var.set("Running command...")
        
        def execute():
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags = CREATE_NO_WINDOW
                )
                
                for line in process.stdout:
                    self.root.after(0, self.log_output, line.rstrip())
                
                process.wait()
                
                if process.returncode == 0:
                    self.root.after(0, self.status_var.set, "Command completed")
                    self.root.after(0, self.log_output, "\n Command completed\n")
                else:
                    self.root.after(0, self.status_var.set, f"Command failed (code {process.returncode})")
                    self.root.after(0, self.log_output, f"\n Command failed with code {process.returncode}\n")
            
            except Exception as e:
                self.root.after(0, self.log_output, f"\nError: {str(e)}\n")
                self.root.after(0, self.status_var.set, "Error occurred")
        
        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
    
    def check_status(self):
        """Check device status"""
        cmd = self.build_command("status")
        self.run_command(cmd)
    
    def configure_logging(self):
        """Configure logging paths"""
        paths = self.config_entry.get().strip().split()
        if not paths:
            messagebox.showwarning("Warning", "Please enter at least one resource path")
            return
        
        extra_args = []
        for path in paths:
            extra_args.extend(["-p", path])
        
        cmd = self.build_command("config", extra_args)
        self.run_command(cmd)
    
    def start_logging(self):
        """Start logging"""
        cmd = self.build_command("start")
        self.run_command(cmd)
    
    def stop_logging(self):
        """Stop logging"""
        cmd = self.build_command("stop")
        self.run_command(cmd)
    
    def fetch_data(self):
        """Fetch data from devices"""
        output_dir = self.output_entry.get().strip()
        if not output_dir:
            messagebox.showwarning("Warning", "Please specify an output directory")
            return
        
        extra_args = ["-o", output_dir]
        cmd = self.build_command("fetch", extra_args)
        
        if cmd is None:
            return
        
        self.log_output(f"\n{'='*60}")
        self.log_output(f"Running: {' '.join(cmd)}")
        self.log_output(f"{'='*60}\n")
        self.status_var.set("Fetching data...")
        
        def execute():
            try:
                # Step 1: Fetch data
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=CREATE_NO_WINDOW
                )
                
                for line in process.stdout:
                    self.root.after(0, self.log_output, line.rstrip())
                
                process.wait()
                
                if process.returncode == 0:
                    self.root.after(0, self.log_output, "\n Fetch completed.\n")
                
                else:
                    self.root.after(0, self.status_var.set, f"Fetch failed (code {process.returncode})")
                    self.root.after(0, self.log_output, f"\n Fetch failed with code {process.returncode}\n")
            
                # Step 2: Convert SBEM to JSON
                self.root.after(0, self.log_output, "\n--- Converting SBEM to JSON ---\n")
                self.root.after(0, self.status_var.set, "Converting SBEM to JSON...")

                # Create sbem-files folder if it doesn't exist
                sbem_folder = os.path.join(output_dir, "sbem-files")
                if not os.path.exists(sbem_folder):
                    os.makedirs(sbem_folder)
                    self.root.after(0, self.log_output, f"Created folder: {sbem_folder}\n")
                
                # Find all .sbem files in output directory
                sbem_files = []
                for root_dir, dirs, files in os.walk(output_dir):
                    if 'sbem-files' in root_dir:
                        continue
                    for file in files:
                        if file.endswith('.sbem'):
                            sbem_files.append(os.path.join(root_dir, file))

                            # sbem_path = os.path.join(root_dir, file)
                            # # Check if this file already exists in sbem-files folder
                            # if os.path.exists(os.path.join(sbem_folder, file)):
                            #     self.root.after(0, self.log_output, 
                            #         f"Skipping {file} - already processed (found in sbem-files)\n")
                            #     # Remove the duplicate from the main folder
                            #     try:
                            #         os.remove(sbem_path)
                            #         self.root.after(0, self.log_output, f"Removed duplicate: {sbem_path}\n")
                            #     except Exception as e:
                            #         self.root.after(0, self.log_output, f"Warning: Could not remove duplicate: {str(e)}\n")
                            # else:
                            #     sbem_files.append(sbem_path)
                
                if not sbem_files:
                    self.root.after(0, self.log_output, "No SBEM files found to convert\n")
                else:
                    for sbem_file in sbem_files:
                        self.root.after(0, self.log_output, f"Converting: {sbem_file}\n")

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
                            text=True,
                            creationflags=CREATE_NO_WINDOW
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
                            
                            # Move SBEM file to sbem-files folder AFTER successful conversion
                            new_sbem_path = os.path.join(sbem_folder, sbem_filename)
                            try:
                                os.rename(sbem_file, new_sbem_path)
                                self.root.after(0, self.log_output, f"Moved SBEM to: {new_sbem_path}\n")
                            except Exception as e:
                                self.root.after(0, self.log_output, f"Warning: Could not move SBEM file: {str(e)}\n")
                
                # Step 3: Convert JSON to CSV
                self.root.after(0, self.log_output, "\n--- Converting JSON to CSV ---\n")
                self.root.after(0, self.status_var.set, "Converting JSON to CSV...")
                
                # Find all .json files in output directory
                json_files = []
                for root_dir, dirs, files in os.walk(output_dir):
                    for file in files:
                        if file.endswith('.json'):
                            json_path = os.path.join(root_dir, file)
                            # Check if corresponding CSV already exists
                            csv_path = os.path.splitext(json_path)[0] + '.csv'
                            if os.path.exists(csv_path):
                                self.root.after(0, self.log_output, 
                                    f"Skipping {file} - CSV already exists\n")
                            else:
                                json_files.append(json_path)
                
                if not json_files:
                    self.root.after(0, self.log_output, "No JSON files found to convert\n")
                else:
                    for json_file in json_files:
                        self.root.after(0, self.log_output, f"Converting: {json_file}\n")
                        
                        # Create output CSV filename (same name, different extension)
                        csv_file = os.path.splitext(json_file)[0] + '.csv'

                        ms_json2csv_script = os.path.join(application_path, "ms_json2csv.py")
                        # Call your Python script with input and output files
                        csv_cmd = ["python", ms_json2csv_script, json_file, csv_file]
                        
                        csv_process = subprocess.Popen(
                            csv_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            creationflags=CREATE_NO_WINDOW
                        )
                        
                        csv_output, _ = csv_process.communicate()
                        if csv_output:
                            self.root.after(0, self.log_output, csv_output)
                        
                        if csv_process.returncode != 0:
                            self.root.after(0, self.log_output, 
                                f"Warning: CSV conversion failed for {json_file}\n")
                        else:
                            self.root.after(0, self.log_output, 
                                f"Created: {csv_file}\n")
                            
                # Step 4: Convert CSV to EDF
                self.root.after(0, self.log_output, "\n--- Converting CSV to EDF ---\n")
                self.root.after(0, self.status_var.set, "Converting CSV to EDF...")

                # Find all .csv files in output directory
                csv_files = []
                for root_dir, dirs, files in os.walk(output_dir):
                    for file in files:
                        if file.endswith('.csv'):
                            csv_path = os.path.join(root_dir, file)
                            # Check if corresponding EDF already exists
                            edf_path = os.path.splitext(csv_path)[0] + '.edf'
                            if os.path.exists(edf_path):
                                self.root.after(0, self.log_output, 
                                    f"Skipping {file} - EDF already exists\n")
                            else:
                                csv_files.append(csv_path)

                if not csv_files:
                    self.root.after(0, self.log_output, "No CSV files found to convert\n")
                else:
                    for csv_file in csv_files:
                        self.root.after(0, self.log_output, f"Converting: {csv_file}\n")
                        
                        # Create output EDF filename (same name, different extension)
                        edf_file = os.path.splitext(csv_file)[0] + '.edf'

                        csv2edf_script = os.path.join(application_path, "csv2edf.py")
                        # Call csv2edf.py with just the input file (auto-detect frequency, auto-scale)
                        edf_cmd = ["python", csv2edf_script, csv_file]
                        
                        edf_process = subprocess.Popen(
                            edf_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            creationflags=CREATE_NO_WINDOW
                        )
                        
                        edf_output, _ = edf_process.communicate()
                        if edf_output:
                            self.root.after(0, self.log_output, edf_output)
                        
                        if edf_process.returncode != 0:
                            self.root.after(0, self.log_output, 
                                f"Warning: EDF conversion failed for {csv_file}\n")
                        else:
                            self.root.after(0, self.log_output, 
                                f"Created: {edf_file}\n")

                # All done
                self.root.after(0, self.status_var.set, "All conversions completed.")
                self.root.after(0, self.log_output, "\n All conversions completed.\n")
                
                # All done
                self.root.after(0, self.status_var.set, "All conversions completed.")
                self.root.after(0, self.log_output, "\n All conversions completed.\n")
            
            except Exception as e:
                self.root.after(0, self.log_output, f"\nError: {str(e)}\n")
                self.root.after(0, self.status_var.set, "Error occurred")
        
        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
    
    def erase_memory(self):
        """Erase device memory"""
        result = messagebox.askyesno(
            "Confirm Erase",
            "Are you sure you want to erase all logged data?\nThis action cannot be undone!"
        )
        if not result:
            self.log_output("\nMemory erase cancelled by user\n")
            return

        # Build and run erase command (no --force anymore)
        cmd = self.build_command("erasemem")
        self.run_command(cmd)
    
    def browse_output(self):
        """Browse for output directory"""
        directory = filedialog.askdirectory(title="Select Output Directory")
        if directory:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, directory)

def main():
    root = tk.Tk()
    app = DataloggerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()