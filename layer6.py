#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import os
import threading
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Dict, List, Tuple

from fpdf import FPDF
from colorama import Fore, Style, init as colorama_init

# ------------------ Constants & Paths ------------------
LOGGING_DIR = os.path.join("ghostshell", "logging")
REPORTING_DIR = os.path.join("ghostshell", "reporting")

# ------------------ Initialize Colorama ------------------
colorama_init(autoreset=True)

# Global stream to capture log output for the full report.
log_capture_stream = StringIO()


# ------------------ Data Structures ------------------
@dataclass
class TestResult:
    layer: int         # OSI Layer number (6 for Presentation)
    status: str        # "Passed" or "Failed" (with color codes)
    message: str       # Additional details about test results

    def to_dict(self):
        return {"layer": self.layer, "status": self.status, "message": self.message}


# ------------------ Layer Runner Base Class ------------------
class LayerRunner:
    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Exception]:
        raise NotImplementedError("Each LayerRunner must implement run_tests()")


# ------------------ Layer6Runner (Presentation Layer) ------------------
class Layer6Runner(LayerRunner):
    def __init__(self, data_sets: List[Dict[str, str]] = None, fmt: str = "json"):
        # Provide default sample data sets if none are provided.
        if data_sets is None or len(data_sets) == 0:
            data_sets = [
                {"message": "Hello OSI L6 - Test 1", "status": "ok"},
                {"message": "Hello OSI L6 - Test 2", "status": "ok2"}
            ]
        self.data_sets = data_sets
        self.format = fmt.lower()

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Exception]:
        logger.info("Starting Layer 6 (Presentation) tests",
                    extra={"dataset_count": len(self.data_sets), "format": self.format})
        results: List[TestResult] = []
        threads = []
        lock = threading.Lock()

        def worker(idx: int, data: Dict[str, str]):
            res = self.check_encoding_decoding(idx, data, logger)
            with lock:
                results.append(res)

        for i, ds in enumerate(self.data_sets):
            t = threading.Thread(target=worker, args=(i, ds))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Count failures (strip ANSI codes for checking)
        fail_count = sum(1 for r in results if strip_ansi(r.status).lower() == "failed")
        if fail_count == len(results):
            err = Exception("all concurrency presentation checks failed")
            logger.error(err, exc_info=err)
            return results, err

        logger.info("Layer 6 concurrency checks complete",
                    extra={"total": len(results), "failures": fail_count})
        return results, None

    def check_encoding_decoding(self, idx: int, data: Dict[str, str], logger: logging.Logger) -> TestResult:
        layer = 6
        if self.format == "json":
            try:
                encoded = json.dumps(data)
            except Exception as e:
                msg = f"Dataset {idx}: failed to encode to JSON: {e}"
                logger.error(msg)
                return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
            logger.debug("Dataset encoded", extra={"dataset_index": idx, "encoded": encoded})
            try:
                decoded = json.loads(encoded)
            except Exception as e:
                msg = f"Dataset {idx}: failed to decode JSON: {e}"
                logger.error(msg)
                return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
        else:
            msg = f"Format '{self.format}' not supported"
            logger.error(msg)
            return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)

        if not compare_maps(data, decoded):
            msg = f"Dataset {idx}: mismatch after encode/decode. original={data} decoded={decoded}"
            logger.error(msg)
            return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)

        msg = f"Dataset {idx}: successfully encoded & decoded. original={data}"
        logger.info("Layer 6 encode/decode success", extra={"dataset_index": idx})
        return TestResult(layer, f"{Fore.GREEN}Passed{Style.RESET_ALL}", msg)


def compare_maps(a: Dict[str, str], b: Dict[str, str]) -> bool:
    if len(a) != len(b):
        return False
    for k, v in a.items():
        if b.get(k) != v:
            return False
    return True


# ------------------ Logging Setup ------------------
def initialize_logger() -> Tuple[logging.Logger, str]:
    os.makedirs(LOGGING_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOGGING_DIR, f"layer6_log_{timestamp}.log")

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


# ------------------ Reporting ------------------
def generate_report(results: List[TestResult], logger: logging.Logger):
    os.makedirs(REPORTING_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REPORTING_DIR, f"layer6_report_{timestamp}.csv")
    pdf_path = os.path.join(REPORTING_DIR, f"layer6_report_{timestamp}.pdf")
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
    pdf.cell(0, 15, "Layers - OSI Testing Report: Layer 6 ", 0, 1, 'C')
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
        # Use multi_cell with split_only to get the wrapped lines.
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
    os.makedirs(REPORTING_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORTING_DIR, f"layer6_raw_output_{timestamp}.txt")

    # Create header.
    header = (
        "=" * 70 + "\n" +
        " " * 20 + "Layers - OSI Testing Report: Layer 6\n" +
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
    lines.append("Layers - OSI Testing Report: Layer 6")
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
    print("Layers - OSI Testing Report: Layer 6")
    print("=" * 50)
    for r in results:
        print(f"Layer {r.layer}: {r.status}")
        print("Message:")
        print(r.message)
        print("-" * 50)


# ------------------ MAIN ------------------
def main():
    parser = argparse.ArgumentParser(description="OSI Layer 6 (Presentation) Test Command-Line Application")
    args = parser.parse_args()

    logger, log_file = initialize_logger()

    # Instantiate the Layer6Runner (with default data sets and format "json")
    runner = Layer6Runner()
    # Optionally, you can customize the runner:
    # runner.data_sets.append({"message": "Another test", "status": "example"})
    # runner.format = "json"

    results, err = runner.run_tests(logger)
    if err:
        logger.warning("Some or all presentation checks failed", exc_info=err)

    # Generate CSV and PDF reports.
    generate_report(results, logger)

    # Print results to the command line.
    print_results(results)

    logger.info("Presentation layer tests complete. Exiting.")

if __name__ == "__main__":
    main()
