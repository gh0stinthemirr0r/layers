#!/usr/bin/env python3
import argparse
import csv
import io
import logging
import os
import platform
import socket
import subprocess
import threading
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import List, Optional, Tuple

import requests  # For HTTP requests if needed
from fpdf import FPDF  # For PDF generation
from colorama import Fore, Style, init as colorama_init

# Initialize Colorama for vivid colored terminal output.
colorama_init(autoreset=True)

# Global stream to capture log output for the full report.
log_capture_stream = StringIO()


# =================== Data Structures ===================

@dataclass
class TestResult:
    layer: int
    status: str  # "Passed" or "Failed" (with color codes)
    message: str  # Additional details

    def to_dict(self):
        return {"layer": self.layer, "status": self.status, "message": self.message}


# =================== Layer Runner Base Class ===================

class LayerRunner:
    def run_tests(self, logger: logging.Logger) -> TestResult:
        raise NotImplementedError("Each LayerRunner must implement run_tests()")


# ---------- Layer1Runner (Physical Layer) ----------
class Layer1Runner(LayerRunner):
    def __init__(self, attempt_count: int = 3):
        self.attempt_count = attempt_count if attempt_count > 0 else 3

    def run_tests(self, logger: logging.Logger) -> TestResult:
        logger.info("Starting Layer 1 (Physical Layer) tests with %d attempt(s)", self.attempt_count)
        results = []
        threads = []
        results_lock = threading.Lock()

        def worker(iteration: int):
            ok = check_physical_connection(iteration)
            with results_lock:
                results.append(ok)

        for i in range(self.attempt_count):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        if not all(results):
            err_msg = "At least one concurrency check failed for physical link"
            logger.error("Layer 1 test failed: %s", err_msg)
            return TestResult(layer=1, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=err_msg)

        strength = check_signal_strength()
        if strength < 50:
            err_msg = f"Signal strength too low: {strength}%"
            logger.error("Layer 1 test failed: %s", err_msg)
            return TestResult(layer=1, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=err_msg)

        msg = f"Physical layer test passed. AttemptCount={self.attempt_count}, Min signal={strength}%"
        logger.info("Layer 1 tests completed successfully: %s", msg)
        return TestResult(layer=1, status=f"{Fore.GREEN}Passed{Style.RESET_ALL}", message=msg)

    @staticmethod
    def check_physical_connection(iteration: int) -> bool:
        time.sleep(0.05)  # Simulate 50 ms delay
        return True

    @staticmethod
    def check_signal_strength() -> int:
        return 85


# ---------- Layer3Runner (Network Layer) ----------
class Layer3Runner(LayerRunner):
    def __init__(self, hostname: str = "example.com", ping_addr: str = "8.8.8.8", ping_count: int = 4):
        self.hostname = hostname if hostname else "example.com"
        self.ping_addr = ping_addr if ping_addr else "8.8.8.8"
        self.ping_count = ping_count if ping_count > 0 else 4

    def run_tests(self, logger: logging.Logger) -> TestResult:
        logger.info("Starting Layer 3 (Network) tests for hostname '%s' and ping IP '%s'",
                    self.hostname, self.ping_addr)

        # 1) DNS Lookup
        try:
            ip_addrs = socket.gethostbyname_ex(self.hostname)[2]
        except Exception as e:
            msg = f"Failed to resolve hostname '{self.hostname}': {e}"
            logger.error(msg)
            return TestResult(layer=3, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=msg)

        # 2) Ping test.
        ping_output, err = run_ping(self.ping_addr, self.ping_count)
        if err:
            msg = f"Ping to {self.ping_addr} failed: {err}"
            logger.error(msg)
            return TestResult(layer=3, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=msg)

        sb = StringIO()
        sb.write(f"Hostname '{self.hostname}' resolved to IP(s):\n")
        for ip in ip_addrs:
            sb.write(f"  {ip}\n")
        sb.write(f"Ping to {self.ping_addr} successful. Output:\n{ping_output}")

        logger.info("Layer 3 test successful. Details:\n%s", sb.getvalue())
        return TestResult(layer=3, status=f"{Fore.GREEN}Passed{Style.RESET_ALL}", message=sb.getvalue())


def run_ping(addr: str, count: int) -> Tuple[str, Optional[Exception]]:
    system = platform.system().lower()
    count_arg = str(count)
    if system == "windows":
        cmd = ["ping", "-n", count_arg, addr]
    else:
        cmd = ["ping", "-c", count_arg, addr]
    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return completed.stdout, None
    except subprocess.CalledProcessError as e:
        return e.output, e


# =================== Logging Setup ===================

def initialize_logger() -> Tuple[logging.Logger, str]:
    log_dir = os.path.join("ghostshell", "logging")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"layer3_log_{timestamp}.log")

    logger = logging.getLogger("osilayers")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(fmt="%(asctime)s - %(levelname)s - %(message)s",
                                  datefmt="%Y-%m-%dT%H:%M:%S")

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    capture_handler = logging.StreamHandler(log_capture_stream)
    capture_handler.setLevel(logging.INFO)
    capture_handler.setFormatter(formatter)
    logger.addHandler(capture_handler)

    logger.info("Logger initialized with file: %s", log_file)
    return logger, log_file


# =================== Reporting ===================

def generate_report(results: List[TestResult], logger: logging.Logger):
    report_dir = os.path.join("ghostshell", "reporting")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_file = os.path.join(report_dir, f"layer3_report_{timestamp}.pdf")
    csv_file = os.path.join(report_dir, f"layer3_report_{timestamp}.csv")
    try:
        write_csv_report(results, csv_file)
        write_pdf_report(results, pdf_file)
        logger.info("Reports generated successfully:\n\tCSV: %s\n\tPDF: %s", csv_file, pdf_file)
    except Exception as e:
        logger.error("Report generation failed: %s", e)


def write_csv_report(results: List[TestResult], path: str):
    with open(path, mode="w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Layer", "Status", "Message"])
        for r in results:
            writer.writerow([r.layer, r.status, r.message])


# --- Updated write_pdf_report using exotic formatting ---
def write_pdf_report(results: List[TestResult], path: str) -> None:
    pdf = FPDF()
    pdf.add_page()

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 15, "Layers - OSI Testing Report: Layer 3", 0, 1, 'C')
    pdf.ln(5)

    # Column configurations: [Layer, Status, Message]
    col_widths = [25, 35, 130]
    
    # Headers
    pdf.set_font("Arial", "B", 12)
    pdf.cell(col_widths[0], 10, "Layer", 1, 0, 'C')
    pdf.cell(col_widths[1], 10, "Status", 1, 0, 'C')
    pdf.cell(col_widths[2], 10, "Message", 1, 1, 'C')

    # Content
    pdf.set_font("Arial", "", 10)
    for r in results:
        # Remove ANSI escape sequences for PDF.
        clean_status = strip_ansi(r.status)
        message_lines = pdf.multi_cell(col_widths[2], 10, r.message, split_only=True)
        row_height = max(10, 10 * len(message_lines))
        
        x_start = pdf.get_x()
        y_start = pdf.get_y()
        
        # Draw cells using rectangle outlines.
        pdf.rect(x_start, y_start, col_widths[0], row_height)
        pdf.rect(x_start + col_widths[0], y_start, col_widths[1], row_height)
        pdf.rect(x_start + sum(col_widths[:2]), y_start, col_widths[2], row_height)
        
        # Fill content.
        pdf.set_xy(x_start, y_start)
        pdf.cell(col_widths[0], row_height, str(r.layer), 0, 0, 'C')
        pdf.set_xy(x_start + col_widths[0], y_start)
        pdf.cell(col_widths[1], row_height, clean_status, 0, 0, 'C')
        pdf.set_xy(x_start + sum(col_widths[:2]), y_start)
        pdf.multi_cell(col_widths[2], 10, r.message)
        
        pdf.set_xy(x_start, y_start + row_height)
        pdf.ln(0)
    pdf.output(path, "F")


def strip_ansi(s: str) -> str:
    return s.replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Style.RESET_ALL, "")


def generate_full_report(results: List[TestResult], full_output: str) -> str:
    """
    Write the entire captured CLI output (logs and summary) to a text file,
    formatting both test results and log data into neat columns.
    """
    report_dir = os.path.join("ghostshell", "reporting")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"layer3_raw_output_{timestamp}.txt")

    # Create header.
    header = (
        "=" * 70 + "\n" +
        " " * 20 + "Layers - OSI Testing Report: Layer 3\n" +
        "=" * 70 + "\n" +
        f"Timestamp: {datetime.now().isoformat()}\n\n"
    )

    # Build table for test results.
    col_width_layer = 8
    col_width_status = 12
    col_width_message = 50

    table_header = f"{'Layer':<{col_width_layer}}{'Status':<{col_width_status}}{'Message':<{col_width_message}}\n"
    table_header += "-" * (col_width_layer + col_width_status + col_width_message) + "\n"

    table_rows = ""
    for r in results:
        layer_str = f"{r.layer:<{col_width_layer}}"
        status_str = f"{strip_ansi(r.status):<{col_width_status}}"
        wrapped_msg = textwrap.wrap(r.message, width=col_width_message)
        if wrapped_msg:
            row = f"{layer_str}{status_str}{wrapped_msg[0]:<{col_width_message}}\n"
            for line in wrapped_msg[1:]:
                row += f"{'':<{col_width_layer + col_width_status}}{line:<{col_width_message}}\n"
        else:
            row = f"{layer_str}{status_str}{'':<{col_width_message}}\n"
        table_rows += row

    table = table_header + table_rows + "\n\n"  # Two newlines before logs.

    # Process captured logs into a table.
    logs_lines = log_capture_stream.getvalue().splitlines()
    log_table_header = f"{'Timestamp':<20}{'Level':<10}{'Message':<40}\n"
    log_table_header += "-" * 70 + "\n"
    log_table_rows = ""
    for line in logs_lines:
        parts = line.split(" - ", 2)
        if len(parts) == 3:
            timestamp_part, level_part, msg_part = parts
            timestamp_col = f"{timestamp_part:<20}"
            level_col = f"{level_part:<10}"
            wrapped_msg = textwrap.wrap(msg_part, width=40)
            if wrapped_msg:
                log_row = f"{timestamp_col}{level_col}{wrapped_msg[0]:<40}\n"
                for extra_line in wrapped_msg[1:]:
                    log_row += f"{'':<30}{extra_line:<40}\n"
            else:
                log_row = f"{timestamp_col}{level_col}{'':<40}\n"
            log_table_rows += log_row
        else:
            log_table_rows += line + "\n"
    log_section = "=" * 70 + "\nLOG OUTPUT\n" + "=" * 70 + "\n" + log_table_header + log_table_rows

    full_report = header + table + log_section

    with open(report_path, "w") as f:
        f.write(full_report)
    return report_path


# ------------------ Display / CLI Output ------------------
def get_results_summary(results: List[TestResult]) -> str:
    lines = []
    separator = "=" * 70
    lines.append(separator)
    lines.append("Layers - OSI Testing Report: Layer 3")
    lines.append(separator)
    for r in results:
        raw_status = r.status.replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Style.RESET_ALL, "")
        lines.append(f"Layer {r.layer}: {raw_status}")
        for line in r.message.splitlines():
            lines.append("   " + line)
    lines.append(separator)
    return "\n".join(lines)


def display_interface(results: List[TestResult]) -> str:
    summary = get_results_summary(results)
    print("\n" + summary)
    return summary


# ------------------ MAIN ------------------
def main():
    parser = argparse.ArgumentParser(description="OSI Layer Test Command-Line Application")
    args = parser.parse_args()

    # Initialize logging.
    logger, log_file = initialize_logger()

    # Create layer runners.
    layer3 = Layer3Runner(hostname="google.com", ping_addr="1.1.1.1", ping_count=4)

    # Run tests for Layer 3.
    results: List[TestResult] = []
    l3_res = layer3.run_tests(logger)
    results.append(l3_res)

    # Generate CSV and PDF reports.
    generate_report(results, logger)

    # Display results on the CLI.
    print_results(results)

    logger.info("All tests complete. Exiting.")

    # Combine captured logs and printed summary to produce the full output report.
    full_output = log_capture_stream.getvalue() + "\n" + get_results_summary(results)
    full_report_file = generate_full_report(results, full_output)
    logger.info("Full output report written to: %s", full_report_file)


def print_results(results: List[TestResult]):
    print("=" * 50)
    print("Layers - OSI Testing Report: Layer 3")
    print("=" * 50)
    for r in results:
        print(f"Layer {r.layer}: {r.status}")
        print(f"Message:\n{r.message}")
        print("-" * 50)


if __name__ == "__main__":
    main()
