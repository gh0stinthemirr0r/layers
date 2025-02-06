![Layers Header](https://github.com/gh0stinthemirr0r/layers/blob/main/screenshots/header.png))
# LAYERS: OSI Layers CLI Testing Suite

A comprehensive Python project that simulates testing across the OSI model layers (1–7) via a command‑line interface. This suite performs various tests (physical, data link, network, transport, session, presentation, and application) concurrently and produces vivid, colorized output along with detailed reports in CSV, PDF, and JSON formats.

## Features

- **Multi-Layer Testing:**  
  Implements simulated tests for all seven OSI layers:
  - **Layer 1 (Physical):** Simulated physical connection checks and signal strength.
  - **Layer 2 (Data Link):** Checks network interfaces, MAC addresses, and interface status.
  - **Layer 3 (Network):** Performs DNS resolution and ping tests.
  - **Layer 4 (Transport):** Checks TCP and UDP connectivity (with independent evaluations).
  - **Layer 5 (Session):** Establishes TCP sessions by sending minimal HTTP GET requests.
  - **Layer 6 (Presentation):** Tests JSON encoding/decoding on provided datasets.
  - **Layer 7 (Application):** Issues HTTP GET requests to validate application-level protocols.

- **Vivid Colorized Output:**  
  Uses [Colorama](https://github.com/tartley/colorama) to display test statuses in vivid green (Passed) or red (Failed) on the CLI.

- **Advanced Reporting:**  
  Generates neatly formatted reports in:
  - **CSV**
  - **PDF** (with exotic table formatting including cell outlines and wrapped text)
  - **JSON** (optional)
  - **Full Text Report** (combining the test result summary with captured logs in neatly aligned columns)

- **Concurrent Testing:**  
  Uses Python's `threading` module to perform tests concurrently across layers, speeding up the overall scan.

- **Log Capture:**  
  Captures all log output (from both console and file) for inclusion in the full text report.

## Installation and Use

1. **Clone the Repository, execute each file individually or simply run the comprehensive scan. Thats it!**

