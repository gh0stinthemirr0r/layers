#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import os
import platform
import socket
import subprocess
import threading
import textwrap
import time
from datetime import datetime
from io import StringIO
from typing import Dict, List, Optional, Tuple

import requests  # For HTTP requests
from fpdf import FPDF  # For PDF generation
from colorama import Fore, Style, init as colorama_init

# ------------------ Constants & Paths ------------------
LOG_DIR = os.path.join("ghostshell", "logging")
REPORT_DIR = os.path.join("ghostshell", "reporting")

# ------------------ Initialize Colorama ------------------
colorama_init(autoreset=True)

# Global stream to capture log output for the full report.
log_capture_stream = StringIO()


# ------------------ Data Structures ------------------
class TestResult:
    def __init__(self, layer: int, status: str, message: str):
        self.layer = layer        # OSI Layer (7 for Application)
        self.status = status      # "Passed" or "Failed" (with color codes)
        self.message = message    # Additional details

    def to_dict(self):
        return {"layer": self.layer, "status": self.status, "message": self.message}

    def __str__(self):
        return f"Layer {self.layer}: {self.status}\nMessage: {self.message}"


# ------------------ Layer Runner Base Class ------------------
class LayerRunner:
    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        raise NotImplementedError("Each LayerRunner must implement run_tests()")


# ------------------ Layer7Runner (Application Layer) ------------------
class Layer7Runner(LayerRunner):
    def __init__(self, endpoints: List[str] = None, timeout: float = 5.0):
        # Provide default endpoints if none are provided.
        if endpoints is None or len(endpoints) == 0:
            endpoints = [
                "https://jsonplaceholder.typicode.com/posts/1",
                "https://jsonplaceholder.typicode.com/posts/2"
            ]
        self.endpoints = endpoints
        self.timeout = timeout

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Starting Layer 7 (Application) tests",
                    extra={"endpoints": self.endpoints, "timeout": self.timeout})
        if not self.endpoints:
            err = Exception("no endpoints provided for Layer7Runner")
            logger.error("Aborting Layer7 tests", exc_info=err)
            return [], err

        results: List[TestResult] = []
        threads = []
        lock = threading.Lock()

        def worker(ep: str):
            res = self.check_endpoint(ep, logger)
            with lock:
                results.append(res)

        for endpoint in self.endpoints:
            t = threading.Thread(target=worker, args=(endpoint,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Count failures (strip ANSI codes for checking).
        fail_count = sum(1 for r in results if strip_ansi(r.status).lower() == "failed")
        if fail_count == len(results):
            err = Exception("all application layer endpoints failed")
            logger.error(err, exc_info=err)
            return results, err

        logger.info("Layer 7 tests complete", extra={"total_endpoints": len(results), "failures": fail_count})
        return results, None

    def check_endpoint(self, endpoint: str, logger: logging.Logger) -> TestResult:
        layer = 7
        try:
            response = requests.get(endpoint, timeout=self.timeout)
        except Exception as e:
            msg = f"HTTP GET failed for {endpoint}: {e}"
            logger.error(msg)
            return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
        if response.status_code < 200 or response.status_code >= 300:
            msg = f"HTTP GET {endpoint} returned status {response.status_code}"
            logger.error(msg)
            return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
        msg = f"HTTP GET {endpoint} returned {response.status_code} OK"
        logger.info("Layer 7 check success", extra={"endpoint": endpoint, "status_code": response.status_code})
        return TestResult(layer, f"{Fore.GREEN}Passed{Style.RESET_ALL}", msg)


# ------------------ Logging Setup ------------------
def initialize_logger() -> Tuple[logging.Logger, str]:
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"layer7_log_{timestamp}.log")

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

    # Capture handler to store log output in our in-memory stream.
    capture_handler = logging.StreamHandler(log_capture_stream)
    capture_handler.setLevel(logging.INFO)
    capture_handler.setFormatter(formatter)
    logger.addHandler(capture_handler)

    logger.info("Logger initialized with file: %s", log_file)
    return logger, log_file


# ------------------ Reporting ------------------
def generate_report(results: List[TestResult], logger: logging.Logger):
    os.makedirs(REPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REPORT_DIR, f"layer7_report_{timestamp}.csv")
    pdf_path = os.path.join(REPORT_DIR, f"layer7_report_{timestamp}.pdf")
    try:
        write_csv_report(results, csv_path)
        write_pdf_report(results, pdf_path)
        logger.info("Reports generated successfully:\n\tCSV: %s\n\tPDF: %s", csv_path, pdf_path)
    except Exception as e:
        logger.error("Report generation failed: %s", e)


def write_csv_report(results: List[TestResult], path: str):
    with open(path, mode="w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Layer", "Status", "Message"])
        for r in results:
            writer.writerow([r.layer, r.status, r.message])


def write_pdf_report(results: List[TestResult], path: str) -> None:
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 15, "OSI Layer Test Report", 0, 1, 'C')
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
        clean_status = strip_ansi(r.status)
        # Use multi_cell with split_only to get wrapped lines.
        message_lines = pdf.multi_cell(col_widths[2], 10, r.message, split_only=True)
        row_height = max(10, 10 * len(message_lines))
        
        x_start = pdf.get_x()
        y_start = pdf.get_y()
        
        # Draw cell outlines.
        pdf.rect(x_start, y_start, col_widths[0], row_height)
        pdf.rect(x_start + col_widths[0], y_start, col_widths[1], row_height)
        pdf.rect(x_start + sum(col_widths[:2]), y_start, col_widths[2], row_height)
        
        # Fill cell content.
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
    formatting both test results and log data into neat columns with extra newlines.
    """
    os.makedirs(REPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORT_DIR, f"full_output_{timestamp}.txt")

    # Create header.
    header = (
        "=" * 70 + "\n" +
        " " * 20 + "Layers - OSI Testing Report: Layer 7\n" +
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
        # Assuming log format: "YYYY-MM-DDTHH:MM:SS - LEVEL - message"
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
    lines.append("Layers - OSI Testing Report: Layer 7")
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


def print_results(results: List[TestResult]):
    print("=" * 50)
    print("Layers - OSI Testing Report: Layer 7")
    print("=" * 50)
    for r in results:
        print(f"Layer {r.layer}: {r.status}")
        print("Message:")
        print(r.message)
        print("-" * 50)


# ------------------ MAIN ------------------
def main():
    parser = argparse.ArgumentParser(description="OSI Layer 7 (Application) Test Command-Line Application")
    args = parser.parse_args()

    logger, log_file = initialize_logger()

    # Instantiate the Layer7Runner (using default endpoints and timeout)
    runner = Layer7Runner()
    # Optionally, you can customize:
    # runner.endpoints = ["https://jsonplaceholder.typicode.com/posts/1", "https://httpbin.org/get"]
    # runner.timeout = 3.0

    results, err = runner.run_tests(logger)
    if err:
        logger.warning("Some or all application checks failed", exc_info=err)

    # Generate CSV and PDF reports.
    generate_report(results, logger)

    # Print results to the command line.
    print_results(results)

    logger.info("All tests complete. Exiting.")


if __name__ == "__main__":
    main()
