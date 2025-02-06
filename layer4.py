#!/usr/bin/env python3
import argparse
import csv
import io
import logging
import os
import socket
import subprocess
import threading
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import List, Tuple, Optional

import platform
from fpdf import FPDF
from colorama import Fore, Style, init as colorama_init

# Initialize Colorama for vivid colored terminal output.
colorama_init(autoreset=True)

# Global stream to capture log output for the full report.
log_capture_stream = StringIO()


# =================== Data Structures ===================

@dataclass
class TestResult:
    layer: int             # OSI Layer number (4 for Transport)
    status: str            # "Passed", "Partial Passed", or "Failed" (with color codes)
    message: str           # Additional details about test results

    def to_dict(self):
        return {"layer": self.layer, "status": self.status, "message": self.message}


# =================== Layer Runner Interface ===================

class LayerRunner:
    def run_tests(self, logger: logging.Logger) -> Tuple[TestResult, Optional[Exception]]:
        raise NotImplementedError("Each LayerRunner must implement run_tests()")


# =================== Layer4Runner (Transport Layer) ===================

class Layer4Runner(LayerRunner):
    def __init__(self,
                 tcp_addresses: List[str] = None,
                 udp_addresses: List[str] = None,
                 timeout: float = 5.0):
        # Provide default addresses if not given.
        if tcp_addresses is None:
            # Defaults: Google and Cloudflare DNS servers.
            tcp_addresses = ["8.8.8.8:53", "1.1.1.1:53"]
        if udp_addresses is None:
            udp_addresses = ["8.8.8.8:53", "1.1.1.1:53"]
        self.tcp_addresses = tcp_addresses
        self.udp_addresses = udp_addresses
        self.timeout = timeout

    def run_tests(self, logger: logging.Logger) -> Tuple[TestResult, Optional[Exception]]:
        logger.info("Starting Layer 4 (Transport) tests",
                    extra={"tcp_addresses": self.tcp_addresses, "udp_addresses": self.udp_addresses})
        
        # ----- TCP Checks -----
        tcp_results = []
        threads = []
        results_lock = threading.Lock()

        def tcp_worker(addr: str):
            success, err_msg = check_tcp_connection(addr, self.timeout)
            with results_lock:
                tcp_results.append((addr, success, err_msg))

        for addr in self.tcp_addresses:
            t = threading.Thread(target=tcp_worker, args=(addr,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        tcp_details = StringIO()
        tcp_pass = False
        tcp_details.write("TCP Checks:\n")
        for addr, success, err_msg in tcp_results:
            if success:
                logger.info("TCP connection successful", extra={"address": addr})
                tcp_details.write(f"  - {addr}: OK\n")
                tcp_pass = True
            else:
                logger.error("TCP connection failed", extra={"address": addr, "error": err_msg})
                tcp_details.write(f"  - {addr}: FAIL ({err_msg})\n")
        # If no TCP check passes, overall result is failed.
        if not tcp_pass:
            err = Exception("All TCP connections failed")
            msg = f"Layer 4 test fails. TCP Checks:\n{tcp_details.getvalue()}"
            logger.error(msg, exc_info=err)
            return (TestResult(layer=4, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=msg), err)

        # ----- UDP Checks -----
        udp_pass = False
        udp_details = StringIO()
        udp_details.write("UDP Checks:\n")
        for addr in self.udp_addresses:
            ok, err_msg = check_udp_connection(addr, self.timeout)
            if ok:
                logger.info("UDP connection successful", extra={"address": addr})
                udp_details.write(f"  - {addr}: OK\n")
                udp_pass = True
            else:
                logger.error("UDP connection failed", extra={"address": addr, "error": err_msg})
                udp_details.write(f"  - {addr}: FAIL ({err_msg})\n")
        # Build overall message
        full_details = tcp_details.getvalue() + "\n" + udp_details.getvalue()
        if tcp_pass and udp_pass:
            overall_status = f"{Fore.GREEN}Passed{Style.RESET_ALL}"
            overall_msg = f"Layer 4 test successful. \n{full_details}"
            logger.info("Layer 4 test successful.")
            return (TestResult(layer=4, status=overall_status, message=overall_msg), None)
        elif tcp_pass and not udp_pass:
            overall_status = f"{Fore.YELLOW}Partial Passed{Style.RESET_ALL}"
            overall_msg = f"Layer 4 test partial pass. TCP Checks passed, but all UDP checks failed.\n{full_details}"
            logger.warning("Layer 4 test partial pass. UDP checks failed.")
            return (TestResult(layer=4, status=overall_status, message=overall_msg), Exception("UDP checks failed"))
        else:
            overall_status = f"{Fore.RED}Failed{Style.RESET_ALL}"
            overall_msg = f"Layer 4 test failed. \n{full_details}"
            logger.error("Layer 4 test failed.")
            return (TestResult(layer=4, status=overall_status, message=overall_msg), Exception("Layer 4 test failed"))

# ------------------ TCP and UDP Helper Functions ------------------

def check_tcp_connection(addr: str, timeout: float) -> Tuple[bool, str]:
    """Attempt to establish a TCP connection to the given address within the timeout.
       Address should be in the form 'host:port'."""
    try:
        host, port_str = addr.split(":")
        port = int(port_str)
    except Exception as e:
        return False, f"Invalid address format: {addr}"
    
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "OK"
    except Exception as e:
        return False, str(e)


def check_udp_connection(addr: str, timeout: float) -> Tuple[bool, str]:
    """Attempt a UDP 'connection' by sending a small message and waiting for a response."""
    try:
        host, port_str = addr.split(":")
        port = int(port_str)
    except Exception as e:
        return False, f"Invalid UDP address format: {addr}"
    
    try:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.settimeout(timeout)
        udp_sock.connect((host, port))
        msg = bytes([0x00, 0x01, 0x02])
        udp_sock.send(msg)
        buf = udp_sock.recv(64)
        udp_sock.close()
        if buf:
            return True, f"Received {len(buf)} bytes"
        else:
            return False, "No data received"
    except Exception as e:
        return False, str(e)


# =================== Logging Setup ===================

def initialize_logger() -> Tuple[logging.Logger, str]:
    log_dir = os.path.join("ghostshell", "logging")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"layer4_log_{timestamp}.log")

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
    pdf_file = os.path.join(report_dir, f"layer4_report_{timestamp}.pdf")
    csv_file = os.path.join(report_dir, f"layer4_report_{timestamp}.csv")
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


def write_pdf_report(results: List[TestResult], path: str) -> None:
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 15, "Layers - OSI Testing Report: Layer 4", 0, 1, 'C')
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
    formatting both test results and log data into neat columns.
    """
    report_dir = os.path.join("ghostshell", "reporting")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"layer4_raw_output_{timestamp}.txt")

    # Create header.
    header = (
        "=" * 70 + "\n" +
        " " * 20 + "Layers - OSI Testing Report: Layer 4\n" +
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


# =================== Display / CLI Output ===================

def get_results_summary(results: List[TestResult]) -> str:
    lines = []
    separator = "=" * 70
    lines.append(separator)
    lines.append("Layers - OSI Testing Report: Layer 4")
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
    print("Layers - OSI Testing Report: Layer 4")
    print("=" * 50)
    for r in results:
        print(f"Layer {r.layer}: {r.status}")
        print(f"Message:\n{r.message}")
        print("-" * 50)


# =================== MAIN ===================

def main():
    parser = argparse.ArgumentParser(description="OSI Layer 4 (Transport) Test Command-Line Application")
    args = parser.parse_args()

    # Initialize logging.
    logger, log_file = initialize_logger()

    # Instantiate the Layer4Runner.
    runner = Layer4Runner()  # Uses default TCP/UDP addresses and timeout.

    # Run tests.
    result, err = runner.run_tests(logger)
    results: List[TestResult] = [result]

    # Generate CSV and PDF reports.
    generate_report(results, logger)

    # Print results to the command line.
    print_results(results)

    logger.info("All tests complete. Exiting.")


if __name__ == "__main__":
    main()
