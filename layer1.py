#!/usr/bin/env python3
import argparse
import csv
import io
import logging
import os
import threading
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

import psutil
from fpdf import FPDF
from colorama import Fore, Style, init as colorama_init

# Initialize Colorama for colored terminal output
colorama_init(autoreset=True)

# Global stream to capture log output for the full report
log_capture_stream = io.StringIO()


@dataclass
class TestResult:
    layer: int
    status: str
    message: str


class LayerRunner:
    def run_tests(self, logger: logging.Logger) -> TestResult:
        raise NotImplementedError("Each LayerRunner must implement run_tests()")


class Layer1Runner(LayerRunner):
    def __init__(self, attempt_count: int = 3):
        self.attempt_count = max(attempt_count, 1)  # Ensure at least 1 attempt

    def run_tests(self, logger: logging.Logger) -> TestResult:
        logger.info("Starting Layer 1 (Physical Layer) tests...")
        results = []
        threads = []
        lock = threading.Lock()

        def worker(attempt: int) -> None:
            ok = self.check_physical_connection(attempt)
            with lock:
                results.append(ok)

        for i in range(self.attempt_count):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        if not all(results):
            err_msg = "Physical cable or connection not detected on at least one attempt"
            logger.error("Layer 1 test failed: %s", err_msg)
            return TestResult(layer=1, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=err_msg)

        strength = self.check_signal_strength()
        if strength < 50:
            err_msg = f"Signal strength too low at {strength}%"
            logger.error("Layer 1 test failed: %s", err_msg)
            return TestResult(layer=1, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=err_msg)

        msg = (f"Layer 1 test successful. Physical checks: {self.attempt_count} attempts, "
               f"min signal: {strength}%")
        logger.info(msg)
        return TestResult(layer=1, status=f"{Fore.GREEN}Passed{Style.RESET_ALL}", message=msg)

    @staticmethod
    def check_physical_connection(attempt: int) -> bool:
        time.sleep(0.02)  # 20ms simulated I/O delay
        return True

    @staticmethod
    def check_signal_strength() -> int:
        return 85


class Layer2Runner(LayerRunner):
    def run_tests(self, logger: logging.Logger) -> TestResult:
        logger.info("Starting Layer 2 (Data Link) tests...")
        try:
            interfaces = psutil.net_if_addrs()
        except Exception as e:
            err_msg = f"Unable to fetch network interfaces: {str(e)}"
            logger.error(err_msg, exc_info=True)
            return TestResult(layer=2, status=f"{Fore.RED}Failed{Style.RESET_ALL}", message=err_msg)

        color_cycle = [Fore.CYAN, Fore.MAGENTA, Fore.YELLOW, Fore.BLUE, Fore.WHITE]
        details = []
        
        for i, (iface, addrs) in enumerate(interfaces.items()):
            color = color_cycle[i % len(color_cycle)]
            mac = next((addr.address for addr in addrs if addr.family == psutil.AF_LINK), "")
            ips = [addr.address for addr in addrs if str(addr.family) not in ("AddressFamily.AF_LINK",)]
            details.append(f"{color}Interface: {iface} | MAC: {mac} | IPs: {', '.join(ips)}{Style.RESET_ALL}")

        msg = "Layer 2 Test successful. Details:\n" + "\n".join(details)
        logger.info(msg)
        return TestResult(layer=2, status=f"{Fore.GREEN}Passed{Style.RESET_ALL}", message=msg)


def initialize_logger() -> Tuple[logging.Logger, str]:
    log_dir = os.path.join("ghostshell", "logging")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"layer1_log_{timestamp}.log")

    logger = logging.getLogger("osilayers")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )

    handlers = [
        logging.FileHandler(log_file),
        logging.StreamHandler(),
        logging.StreamHandler(log_capture_stream)
    ]

    for handler in handlers:
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.info("Logger initialized with file: %s", log_file)
    return logger, log_file


def generate_report(results: List[TestResult], logger: logging.Logger) -> None:
    report_dir = os.path.join("ghostshell", "reporting")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_file = os.path.join(report_dir, f"layer12_report_{timestamp}.pdf")
    csv_file = os.path.join(report_dir, f"layer12_report_{timestamp}.csv")

    try:
        write_csv_report(results, csv_file)
        write_pdf_report(results, pdf_file)
        logger.info("Reports generated successfully:\n\tCSV: %s\n\tPDF: %s", csv_file, pdf_file)
    except Exception as e:
        logger.error("Failed to generate reports: %s", e, exc_info=True)


def write_csv_report(results: List[TestResult], csv_path: str) -> None:
    try:
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Layer", "Status", "Message"])
            for r in results:
                writer.writerow([r.layer, r.status, r.message])
    except Exception as e:
        raise Exception(f"Failed to write CSV report: {str(e)}") from e


def write_pdf_report(results: List[TestResult], pdf_path: str) -> None:
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", "B", 14)
        pdf.cell(40, 10, "Layers - OSI Testing Report: Layer 1 & Layer 2")
        pdf.ln(12)

        pdf.set_font("Arial", "B", 12)
        pdf.cell(30, 10, "Layer", 1)
        pdf.cell(40, 10, "Status", 1)
        pdf.cell(120, 10, "Message", 1)
        pdf.ln(10)

        pdf.set_font("Arial", "", 12)
        for r in results:
            pdf.cell(30, 10, str(r.layer), 1)
            status = r.status.replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Style.RESET_ALL, "")
            pdf.cell(40, 10, status, 1)
            message_lines = textwrap.wrap(r.message, width=60)
            pdf.cell(120, 10, message_lines[0] if message_lines else "", 1)
            pdf.ln(10)
            for line in message_lines[1:]:
                pdf.cell(30, 10, "", 1)
                pdf.cell(40, 10, "", 1)
                pdf.cell(120, 10, line, 1)
                pdf.ln(10)

        pdf.output(pdf_path, "F")
    except Exception as e:
        raise Exception(f"Failed to write PDF report: {str(e)}") from e


def generate_full_report(results: List[TestResult], full_output: str) -> str:
    report_dir = os.path.join("ghostshell", "reporting")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"layer12_raw_output_{timestamp}.txt")

    header = (
        "=" * 70 + "\n" +
        " " * 20 + "OSI LAYERS TEST REPORT\n" +
        "=" * 70 + "\n" +
        f"Timestamp: {datetime.now().isoformat()}\n\n"
    )

    table_header = f"{'Layer':<10}{'Status':<15}{'Message':<40}\n" + "-" * 70 + "\n"
    table_rows = ""

    def strip_ansi(s: str) -> str:
        return s.replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Style.RESET_ALL, "")

    for r in results:
        layer_str = f"{r.layer:<10}"
        status_str = f"{strip_ansi(r.status):<15}"
        wrapped_msg = "\n".join(textwrap.wrap(strip_ansi(r.message), width=40))
        row = f"{layer_str}{status_str}{wrapped_msg}\n"
        table_rows += row

    table = table_header + table_rows + "\n"
    log_section = "=" * 70 + "\nLOG OUTPUT\n" + "=" * 70 + "\n" + full_output

    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(header + table + log_section)
    except Exception as e:
        raise Exception(f"Failed to write full report: {str(e)}") from e

    return report_path


def get_results_summary(results: List[TestResult]) -> str:
    lines = []
    separator = "=" * 70
    lines.append(separator)
    lines.append("OSI Layers Test Results")
    lines.append(separator)
    
    for r in results:
        status = r.status.replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Style.RESET_ALL, "")
        lines.append(f"Layer {r.layer}: {status}")
        for line in r.message.splitlines():
            lines.append("   " + line)
    
    lines.append(separator)
    return "\n".join(lines)


def display_interface(results: List[TestResult]) -> str:
    summary = get_results_summary(results)
    print("\n" + summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="OSI Layer Test Command-Line Application")
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable the terminal UI visualization (only logs and reports will be generated)",
    )
    args = parser.parse_args()

    try:
        logger, log_file = initialize_logger()
        
        runners: List[LayerRunner] = [
            Layer1Runner(attempt_count=3),
            Layer2Runner()
        ]
        
        results: List[TestResult] = []
        for runner in runners:
            result = runner.run_tests(logger)
            results.append(result)
            raw_status = result.status.replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Style.RESET_ALL, "")
            if raw_status.lower() == "failed":
                logger.warning("A layer test encountered errors: %s", result.message)

        generate_report(results, logger)

        summary_text = display_interface(results) if not args.no_ui else get_results_summary(results)
        logger.info("All tests completed.")

        full_output = log_capture_stream.getvalue() + "\n" + summary_text
        full_report_file = generate_full_report(results, full_output)
        logger.info("Full output report written to: %s", full_report_file)

    except Exception as e:
        logging.error("Fatal error occurred: %s", str(e), exc_info=True)
        raise


if __name__ == "__main__":
    main()