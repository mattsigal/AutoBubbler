# The AutoBubbler
**The AutoBubbler** is a desktop application for SFU Document Solutions. It automates the creation of "Answer Key" PDFs by reading a CSV answer key and generating a pixel-perfect filled Scantron PDF.

## Features
* **Drag & Drop Interface:** Simply drag your CSV key files onto the window.
* **Auto-Detection:** Automatically detects the "Special Code" from the filename (e.g., `v1234`).
* **Batch Processing:** Handle multiple CSV files at once.
* **Visual Feedback:** Dark mode GUI with real-time status logging.

## Installation
No installation is required if you have the executable!

1. Download `AutoBubbler.exe` from the **Releases** section.
2. Place it in a folder (it does not need any other files to run).
3. Run the app.

## Usage
1. **Prepare your CSV:**
   * Columns: Question, Answer
   * Filename Format: Must include `v` followed by 4 digits (e.g., `PSYC100-v1000-Key.csv`).
2. **Run AutoBubbler:** Open the application.
3. **Drag & Drop:** Drag your CSV file(s) onto the black window.
4. **Done:** The PDF answer key will appear in the same folder as your CSV!

## Printing Instructions (Important)
* **DPI:** 300 or 600 DPI.
* **Color:** Black and White.
* **Sizing:** 100% Scale (Do not "Fit to Page").
* **Duplex:** Two-sided.

## For Developers

To run from source:
1. Install Python 3.10+.
2. Install requirements: `pip install PySide6 pymupdf`.
3. Run `python AutoBubbler.py`.