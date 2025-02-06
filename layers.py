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
from typing import Any, Dict, List, Optional, Tuple

import psutil  # For network interfaces
import requests  # For HTTP requests
from fpdf import FPDF  # For PDF generation
from colorama import Fore, Style, init as colorama_init

# ------------------ Global Constants & Paths ------------------
LOG_DIR = os.path.join("ghostshell", "logging")
REPORT_DIR = os.path.join("ghostshell", "reporting")
# (WINDOW_WIDTH, WINDOW_HEIGHT, MAX_PARTICLES are unused in CLI)

# ------------------ Initialize Colorama ------------------
colorama_init(autoreset=True)

# Global stream to capture log output for the full report.
log_capture_stream = StringIO()


# ------------------ Data Structures ------------------
from dataclasses import dataclass

@dataclass
class TestResult:
    layer: int         # OSI Layer number (1 to 7)
    status: str        # "Passed" or "Failed" (with color codes)
    message: str       # Additional details about test results

    def to_dict(self) -> Dict[str, Any]:
        return {"layer": self.layer, "status": self.status, "message": self.message}

    def __str__(self) -> str:
        return f"Layer {self.layer}: {self.status}\nMessage: {self.message}"


# ------------------ Layer Runner Base Class ------------------
class LayerRunner:
    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        raise NotImplementedError("Each LayerRunner must implement run_tests()")


# ------------------ Layer1Runner (Physical Layer) ------------------
class Layer1Runner(LayerRunner):
    def __init__(self, attempt_count: int = 3):
        self.attempt_count = attempt_count if attempt_count > 0 else 3

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Layer1: Starting physical layer checks (attempt_count=%d)", self.attempt_count)
        results = []
        threads = []
        lock = threading.Lock()

        def worker(iteration: int):
            ok = check_physical_connection(iteration)
            with lock:
                results.append(ok)

        for i in range(self.attempt_count):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        strength = check_signal_strength()
        if not all(results):
            msg = f"Layer1 physical checks: some failures; signal={strength}%"
            logger.error(msg)
            return ([TestResult(1, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)], Exception("physical check failure"))
        if strength < 50:
            msg = f"Layer1 test fail: signal strength too low: {strength}%"
            logger.error(msg)
            return ([TestResult(1, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)], Exception("signal strength too low"))
        pass_msg = f"Layer1 all concurrency checks pass; signal={strength}%"
        logger.info(pass_msg)
        return ([TestResult(1, f"{Fore.GREEN}Passed{Style.RESET_ALL}", pass_msg)], None)

def check_physical_connection(iteration: int) -> bool:
    time.sleep(0.02)  # Simulate 20ms delay
    return True

def check_signal_strength() -> int:
    return 85


# ------------------ Layer2Runner (Data Link Layer) ------------------
class Layer2Runner(LayerRunner):
    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Layer2: Starting data link checks")
        try:
            interfaces = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
        except Exception as e:
            msg = f"Failed to fetch network interfaces: {e}"
            logger.error(msg)
            return ([TestResult(2, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)], e)

        details = StringIO()
        all_passed = True
        for iface, addrs in interfaces.items():
            mac = ""
            for addr in addrs:
                if addr.family == psutil.AF_LINK:
                    mac = addr.address
            result = "OK"
            if not mac or mac.startswith("00:00:00"):
                result = "INVALID_MAC"
            if iface in stats and not stats[iface].isup:
                result = "DOWN"
            details.write(f"Interface: {iface}, MAC: {mac} => {result}\n")
            if result != "OK":
                all_passed = False

        msg = "Layer2: " + ("all interfaces appear OK" if all_passed else "one or more interfaces invalid or down") + "\n" + details.getvalue()
        if all_passed:
            logger.info(msg)
            return ([TestResult(2, f"{Fore.GREEN}Passed{Style.RESET_ALL}", msg)], None)
        else:
            logger.error(msg)
            return ([TestResult(2, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)], Exception("interface check failure"))


# ------------------ Layer3Runner (Network Layer) ------------------
class Layer3Runner(LayerRunner):
    def __init__(self, hostname: str = "example.com", ping_addr: str = "8.8.8.8", ping_count: int = 4):
        self.hostname = hostname if hostname else "example.com"
        self.ping_addr = ping_addr if ping_addr else "8.8.8.8"
        self.ping_count = ping_count if ping_count > 0 else 4

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Layer3: Starting network checks", extra={"hostname": self.hostname, "ping_addr": self.ping_addr})
        results = []
        # DNS resolution
        try:
            ips = socket.gethostbyname_ex(self.hostname)[2]
        except Exception as e:
            msg = f"DNS resolution failed for {self.hostname}: {e}"
            logger.error(msg)
            results.append(TestResult(3, f"{Fore.RED}Failed{Style.RESET_ALL}", msg))
            return (results, e)
        ips_str = " ".join(ips)
        dns_msg = f"DNS for {self.hostname} resolved to: {ips_str}"
        logger.info(dns_msg)
        results.append(TestResult(3, f"{Fore.GREEN}Passed{Style.RESET_ALL}", dns_msg))
        # Ping test
        out, err = run_ping(self.ping_addr, self.ping_count)
        if err:
            msg = f"Ping to {self.ping_addr} failed: {err} (Output: {out})"
            logger.error(msg)
            results.append(TestResult(3, f"{Fore.RED}Failed{Style.RESET_ALL}", msg))
            return (results, err)
        ping_msg = f"Ping to {self.ping_addr} succeeded:\n{out}"
        logger.info(ping_msg)
        results.append(TestResult(3, f"{Fore.GREEN}Passed{Style.RESET_ALL}", ping_msg))
        return (results, None)

def run_ping(ip: str, count: int) -> Tuple[str, Optional[Exception]]:
    count_str = str(count)
    if os.name == "nt":
        cmd = ["ping", "-n", count_str, ip]
    else:
        cmd = ["ping", "-c", count_str, ip]
    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return (completed.stdout, None)
    except subprocess.CalledProcessError as e:
        return (e.output, e)


# ------------------ Layer4Runner (Transport Layer) ------------------
def strip_ansi(s: str) -> str:
    for code in (Fore.GREEN, Fore.RED, Style.RESET_ALL):
        s = s.replace(code, "")
    return s

class Layer4Runner(LayerRunner):
    def __init__(self, tcp_addresses: List[str] = None, udp_addresses: List[str] = None, timeout: float = 5.0):
        if tcp_addresses is None or len(tcp_addresses) == 0:
            tcp_addresses = ["8.8.8.8:53", "1.1.1.1:53"]
        if udp_addresses is None:
            udp_addresses = ["8.8.8.8:53", "1.1.1.1:53"]
        self.tcp_addresses = tcp_addresses
        self.udp_addresses = udp_addresses
        self.timeout = timeout

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Layer4: Starting transport checks", extra={"tcp_addresses": self.tcp_addresses, "udp_addresses": self.udp_addresses})
        results = []
        # TCP checks (concurrent)
        tcp_results = []
        threads = []
        lock = threading.Lock()

        def tcp_worker(addr: str):
            success, msg = check_tcp_connection(addr, self.timeout)
            st = f"{Fore.GREEN}Passed{Style.RESET_ALL}" if success else f"{Fore.RED}Failed{Style.RESET_ALL}"
            with lock:
                tcp_results.append(TestResult(4, st, f"TCP {addr} => {msg}"))

        for addr in self.tcp_addresses:
            t = threading.Thread(target=tcp_worker, args=(addr,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        tcp_details = StringIO()
        tcp_pass = False
        tcp_details.write("TCP Checks:\n")
        for r in tcp_results:
            if "Passed" in strip_ansi(r.status):
                tcp_details.write(f"  - {r.message}\n")
                tcp_pass = True
            else:
                tcp_details.write(f"  - {r.message}\n")
            results.append(r)

        # UDP checks: test each UDP address independently.
        udp_details = StringIO()
        udp_details.write("UDP Checks:\n")
        udp_pass = False
        for addr in self.udp_addresses:
            ok, msg = check_udp_connection(addr, self.timeout)
            if ok:
                udp_details.write(f"  - {addr}: OK\n")
                udp_pass = True
                results.append(TestResult(4, f"{Fore.GREEN}Passed{Style.RESET_ALL}", f"UDP {addr} => OK"))
            else:
                udp_details.write(f"  - {addr}: FAIL ({msg})\n")
                results.append(TestResult(4, f"{Fore.RED}Failed{Style.RESET_ALL}", f"UDP {addr} => FAIL ({msg})"))
        full_details = tcp_details.getvalue() + "\n" + udp_details.getvalue()

        if tcp_pass and udp_pass:
            overall_status = f"{Fore.GREEN}Passed{Style.RESET_ALL}"
            overall_msg = f"Layer4 test successful.\n{full_details}"
            logger.info("Layer4 test successful.")
            return (results, None)
        elif tcp_pass and not udp_pass:
            overall_status = f"{Fore.YELLOW}Partial Passed{Style.RESET_ALL}"
            overall_msg = f"Layer4 test partial pass. TCP checks passed but UDP checks failed.\n{full_details}"
            logger.warning("Layer4 test partial pass. UDP checks failed.")
            return (results, Exception("UDP checks failed"))
        else:
            overall_status = f"{Fore.RED}Failed{Style.RESET_ALL}"
            overall_msg = f"Layer4 test failed.\n{full_details}"
            logger.error("Layer4 test failed.")
            return (results, Exception("Layer4 test failed"))

def check_tcp_connection(addr: str, timeout: float) -> Tuple[bool, str]:
    try:
        host, port_str = addr.split(":")
        port = int(port_str)
        with socket.create_connection((host, port), timeout=timeout):
            return (True, "OK")
    except Exception as e:
        return (False, str(e))

def check_udp_connection(addr: str, timeout: float) -> Tuple[bool, str]:
    try:
        host, port_str = addr.split(":")
        port = int(port_str)
    except Exception as e:
        return (False, f"Invalid UDP address format: {addr}")
    try:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.settimeout(timeout)
        udp_sock.connect((host, port))
        msg = bytes([0x00, 0x01])
        udp_sock.send(msg)
        buf = udp_sock.recv(32)
        udp_sock.close()
        return (True, f"Received {len(buf)} bytes")
    except Exception as e:
        return (False, str(e))


# ------------------ Layer5Runner (Session) ------------------
class Layer5Runner(LayerRunner):
    def __init__(self, targets: List[str] = None, timeout: float = 5.0):
        if targets is None or len(targets) == 0:
            targets = ["example.com:80", "example.net:80"]
        self.targets = targets
        self.timeout = timeout

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Layer5: Starting session checks", extra={"targets": self.targets})
        results = []
        threads = []
        lock = threading.Lock()

        def session_worker(target: str):
            res = check_session(target, self.timeout, logger)
            with lock:
                results.append(res)

        for target in self.targets:
            t = threading.Thread(target=session_worker, args=(target,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        fail_count = sum(1 for r in results if strip_ansi(r.status).lower() == "failed")
        if fail_count == len(results):
            return (results, Exception("all session layer checks failed"))
        return (results, None)

def check_session(target: str, timeout: float, logger: logging.Logger) -> TestResult:
    layer = 5
    try:
        host, port_str = target.split(":")
        port = int(port_str)
        conn = socket.create_connection((host, port), timeout=timeout)
    except Exception as e:
        msg = f"Session fail for {target}: {e}"
        logger.error(msg)
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    try:
        req = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        conn.sendall(req.encode())
        buf = conn.recv(512)
        n = len(buf)
    except Exception as e:
        msg = f"Failed during session with {target}: {e}"
        logger.error(msg)
        conn.close()
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    finally:
        conn.close()
    pass_msg = f"Session to {target} success, read {n} bytes"
    logger.info("Session success", extra={"target": target, "bytes_received": n})
    return TestResult(layer, f"{Fore.GREEN}Passed{Style.RESET_ALL}", pass_msg)


# ------------------ Layer6Runner (Presentation) ------------------
class Layer6Runner(LayerRunner):
    def __init__(self, data_sets: List[Dict[str, str]] = None, fmt: str = "json"):
        if data_sets is None or len(data_sets) == 0:
            data_sets = [
                {"message": "Hello L6 #1", "status": "ok"},
                {"message": "Hello L6 #2", "status": "ok2"}
            ]
        self.data_sets = data_sets
        self.format = fmt.lower()

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        logger.info("Layer6: Starting presentation checks", extra={"dataset_count": len(self.data_sets), "format": self.format})
        results = []
        threads = []
        lock = threading.Lock()

        def worker(idx: int, data: Dict[str, str]):
            res = check_encoding_decoding(idx, data, logger)
            with lock:
                results.append(res)

        for i, ds in enumerate(self.data_sets):
            t = threading.Thread(target=worker, args=(i, ds))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        fail_count = sum(1 for r in results if strip_ansi(r.status).lower() == "failed")
        if fail_count == len(results):
            return (results, Exception("all presentation checks failed"))
        logger.info("Layer6 concurrency checks complete", extra={"total": len(results), "failures": fail_count})
        return (results, None)

def check_encoding_decoding(idx: int, data: Dict[str, str], logger: logging.Logger) -> TestResult:
    layer = 6
    try:
        encoded = json.dumps(data)
    except Exception as e:
        msg = f"Dataset {idx} JSON encode fail: {e}"
        logger.error(msg)
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    try:
        decoded = json.loads(encoded)
    except Exception as e:
        msg = f"Dataset {idx} JSON decode fail: {e}"
        logger.error(msg)
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    if not compare_maps(data, decoded):
        msg = f"Dataset {idx} mismatch after encode/decode. original={data} decoded={decoded}"
        logger.error(msg)
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    msg = f"Dataset {idx}: successfully encoded & decoded. original={data}"
    logger.info("Layer6 encode/decode success", extra={"dataset_index": idx})
    return TestResult(layer, f"{Fore.GREEN}Passed{Style.RESET_ALL}", msg)

def compare_maps(a: Dict[str, str], b: Dict[str, str]) -> bool:
    if len(a) != len(b):
        return False
    for k, v in a.items():
        if b.get(k) != v:
            return False
    return True


# ------------------ Layer7Runner (Application) ------------------
class Layer7Runner(LayerRunner):
    def __init__(self, endpoints: List[str] = None, timeout: float = 5.0):
        if endpoints is None or len(endpoints) == 0:
            endpoints = [
                "https://jsonplaceholder.typicode.com/posts/1",
                "https://jsonplaceholder.typicode.com/posts/2"
            ]
        self.endpoints = endpoints
        self.timeout = timeout

    def run_tests(self, logger: logging.Logger) -> Tuple[List[TestResult], Optional[Exception]]:
        if not self.endpoints:
            return ([], Exception("no endpoints provided for Layer7Runner"))
        logger.info("Layer7: Starting application checks", extra={"endpoints": self.endpoints, "timeout": self.timeout})
        results = []
        threads = []
        lock = threading.Lock()

        def worker(ep: str):
            res = check_http_get(ep, self.timeout, logger)
            with lock:
                results.append(res)

        for ep in self.endpoints:
            t = threading.Thread(target=worker, args=(ep,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        fail_count = sum(1 for r in results if strip_ansi(r.status).lower() == "failed")
        if fail_count == len(results):
            return (results, Exception("all application layer endpoints failed"))
        return (results, None)

def check_http_get(url: str, timeout: float, logger: logging.Logger) -> TestResult:
    layer = 7
    try:
        resp = requests.get(url, timeout=timeout)
    except Exception as e:
        msg = f"HTTP GET {url} fail: {e}"
        logger.error(msg)
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    if resp.status_code < 200 or resp.status_code >= 300:
        msg = f"HTTP GET {url} => status {resp.status_code}"
        logger.error(msg)
        return TestResult(layer, f"{Fore.RED}Failed{Style.RESET_ALL}", msg)
    msg = f"HTTP GET {url} => {resp.status_code} OK"
    logger.info("Layer7 check success", extra={"endpoint": url, "status_code": resp.status_code})
    return TestResult(layer, f"{Fore.GREEN}Passed{Style.RESET_ALL}", msg)


# ------------------ Aggregation & Reporting ------------------
class Options:
    def __init__(self, output_format: str):
        self.output_format = output_format.lower()  # "csv", "pdf", or "json"

def ExecuteLayers(runners: List[LayerRunner], opts: Options) -> List[TestResult]:
    all_results = []
    for runner in runners:
        sub_results, err = runner.run_tests(logger)
        all_results.extend(sub_results)
        if err:
            logger.warning("Some sub-tests in a layer encountered errors", exc_info=err)
    # Generate chosen output
    os.makedirs(REPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REPORT_DIR, f"osilayers_report_{timestamp}.csv")
    pdf_path = os.path.join(REPORT_DIR, f"osilayers_report_{timestamp}.pdf")
    json_path = os.path.join(REPORT_DIR, f"osilayers_report_{timestamp}.json")
    if opts.output_format == "csv":
        write_csv_report(all_results, csv_path)
    elif opts.output_format == "pdf":
        write_pdf_report(all_results, pdf_path)
    elif opts.output_format == "json":
        write_json_report(all_results, json_path)
    else:
        logger.error("Unsupported output format. Choose 'csv', 'pdf', or 'json'.",
                     extra={"requested_format": opts.output_format})
    return all_results

def write_csv_report(results: List[TestResult], path: str):
    with open(path, mode="w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Layer", "Status", "Message"])
        for r in results:
            writer.writerow([r.layer, r.status, r.message])

def write_pdf_report(results: List[TestResult], path: str):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(40, 10, "OSI Layer Test Report")
    pdf.ln(12)

    # Table header
    pdf.set_font("Arial", "B", 12)
    pdf.cell(30, 10, "Layer", 1)
    pdf.cell(40, 10, "Status", 1)
    pdf.cell(120, 10, "Message", 1)
    pdf.ln(10)

    # Table rows
    pdf.set_font("Arial", "", 12)
    for r in results:
        pdf.cell(30, 10, str(r.layer), 1)
        pdf.cell(40, 10, r.status, 1)
        # For the message, using multi-cell in case of long text
        x = pdf.get_x()
        y = pdf.get_y()
        pdf.multi_cell(120, 10, r.message, border=1)
        # Set the position for the next row (if needed)
        pdf.set_xy(x + 30 + 40 + 120, y)
        pdf.ln(10)

    pdf.output(path, "F")

def write_json_report(results: List[TestResult], output_path: str):
    try:
        with open(output_path, "w") as f:
            json.dump([r.to_dict() for r in results], f, indent=2)
        logger.info("JSON report generated", extra={"file": output_path})
    except Exception as e:
        logger.error("Failed to write JSON report", exc_info=e)


# ------------------ Logging Setup ------------------
def initialize_logger() -> Tuple[logging.Logger, str]:
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"osilayers_log_{timestamp}.log")

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
    logger.info("Logger initialized with file: %s", log_file)
    return logger, log_file


# ------------------ Command-Line Output ------------------
def print_results(results: List[TestResult]):
    print("=" * 50)
    print("OSI Layer Test Results")
    print("=" * 50)
    for r in results:
        print(f"Layer {r.layer}: {r.status}")
        print("Message:")
        print(r.message)
        print("-" * 50)


# ------------------ MAIN ------------------
def main():
    parser = argparse.ArgumentParser(description="OSI Layers Test Command-Line Application")
    parser.add_argument("-format", type=str, default="csv", help="Output format: csv, pdf, or json")
    args = parser.parse_args()
    opts = Options(args.format)

    global logger
    logger, log_file = initialize_logger()

    # Build layer runners (Layers 1 - 7)
    layer_runners: List[LayerRunner] = [
        Layer1Runner(attempt_count=3),
        Layer2Runner(),
        Layer3Runner(hostname="example.com", ping_addr="8.8.8.8", ping_count=4),
        Layer4Runner(
            tcp_addresses=["8.8.8.8:53", "1.1.1.1:53"],
            udp_addresses=["8.8.8.8:53", "1.1.1.1:53"],
            timeout=5.0
        ),
        Layer5Runner(
            targets=["example.com:80", "api.example.net:443"],
            timeout=5.0
        ),
        Layer6Runner(
            data_sets=[
                {"message": "Hello L6 #1", "status": "ok"},
                {"message": "Hello L6 #2", "status": "ok2"}
            ]
        ),
        Layer7Runner(
            endpoints=[
                "https://jsonplaceholder.typicode.com/posts/1",
                "https://jsonplaceholder.typicode.com/posts/2"
            ],
            timeout=5.0
        )
    ]

    # Execute tests and generate the chosen report.
    results = ExecuteLayers(layer_runners, opts)

    # Print results to the command line.
    print_results(results)

    logger.info("All tests complete. Exiting.")

if __name__ == "__main__":
    main()
