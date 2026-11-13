# The AutoBubbler
**The AutoBubbler** is a desktop application to prepare Scantron multiple choice answer keys suitable for scanning by SFU Document Solutions. It automates the creation of a pixel-perfect "Answer Key" PDF by reading in a CSV file, with the first column indicating question number and the second column indicating the correct letter answer.

## Features
* **Drag & Drop Interface:** Simply drag one or more CSV key files onto the window to generate your PDF key.
* **Auto-Detection:** Automatically detects and bubbles the "Special Code" based upon the filename (e.g., `v1234`).
* **Batch Processing:** Can handle multiple CSV files at once.
* **Visual Feedback:** Dark mode GUI with real-time status logging.
* **AutoScorer (new in v1.6):** Grade a *single* student's Scantron (e.g., a makeup exam) from a scan or phone photo, and get back an annotated PDF showing the correct answers and the score.

## Installation
**The AutoBubbler** is availalable for Windows and Mac. In both cases, no installer is required; however, there are a few small issues with running it on Mac that you should be aware of.

### For Windows:
1. Download `AutoBubbler-Windows.zip` from the [Releases](https://github.com/mattsigal/AutoBubbler/releases) page.
2. Unzip and place `AutoBubbler.exe` in a folder (it does not need any other files to run).
3. Double click `AutoBubbler.exe` to run the app.
4. Profit!

### For MacOS:
1. Download `AutoBubbler-Mac-Silicon.zip` if you are on a newer Mac or `AutoBubbler-Mac-Intel.zip` if you are on a pre-2020 Mac that has an Intel processor from the [Releases](https://github.com/mattsigal/AutoBubbler/releases) page.
2. Unarchive the zip file if needed (depending on your browser, it may automatically extract itself and you will just see `AutoBubbler` in your Downloads directory with an icon)
3. Move the app to your Applications folder and try to open it. It will produce an error message about being "unsigned".

#### If you are on a Silicon-based Mac:
4. Open System Preferences and go to "Privacy & Security"
5. Near the bottom of the System Preferences page, under "Security", ensure "Allow applications from" is set to "App Store & Known Developers" and then look below that for `AutoBubbler` listed under "Recently Denied Applications". Click the `Open Anyways` button, and `Open Anyways` again in the pop-up.
6. Double click the app to launch!

#### If you are on an older Intel-based Mac:
4. Open System Preferences and go to "Security & Privacy"
5. Click on the first "General" tab and near the bottom, ensure "Allow applications from" is set to "App Store & Known Developers" and then look below that for `AutoBubbler` listed under "Recently Denied Applications". Click the `Open Anyways` button, and `Open Anyways` again in the pop-up.
6. Double click the app to launch!

## Usage
1. **Prepare your CSV File(s):**
   * Data: Your CSV file should have two columns. The first should indicate the question number and the second should indicate the question's answer (in either upper or lowercase). The first row should also contain the variable names but these are not parsed. Columns beyond the second are not considered by the script. See the example files provided on the Releases page for a template.
   * Filename Format: Should be COURSE ID followed by `-vSPECIALCODE`. The special code must include `v` followed by 4 digits (e.g., `PSYC100-v1000-Key.csv`). If a special code is missing, it will default to 0000. The answer key PDFs will use the same filenames as the input.
2. **Run AutoBubbler:** Open the application.
3. **Drag & Drop:** Drag your CSV file(s) onto the black window.
4. **Done:** The PDF answer key(s) will appear in the same folder as your CSV(s)!

## Printing Instructions (Important!)
In order for the keys to be scanned by Document Solutions, you **must** print them with the following specifications:
* **DPI:** 300 or 600 DPI.
* **Color:** Black and White.
* **Sizing:** 100% Scale (NOT "Fit to Page", which many printers typically default to).
* **Duplex:** Two-sided.

These print settings have been confirmed to work by SFU Document Solutions.

## AutoScorer (One-Off Grading)
The **AutoScorer** tab grades ONE completed SFU GPAS200 Scantron against an answer key. It is meant for one-offs such as a makeup or deferred exam. **Whole classes must still be graded by Document Solutions.**

1. **Answer Key:** Drop the original key CSV (same format as above) *or* the PDF key that AutoBubbler made.
2. **Student Scantron:** Drop ONE student's sheet:
   * A scanned PDF (recommended; any colour scanner/copier is fine), or
   * A phone photo (JPG/PNG). Photograph the whole sheet straight-on, on a darker surface, with even lighting. iPhone HEIC photos must be exported as JPG.
   * **Exams with more than 40 questions** need the **back** of the sheet as well: a 2-page PDF (front + back, in either order) or two photos.
3. Click **Grade Scantron**. A `<scan name>-GRADED.pdf` is saved beside the scan (or on your Desktop if that folder is read-only) and opened automatically.

On the graded PDF, a **green ring** marks every correct answer, a **red X** marks each incorrect response, and `BL` / `MX` flag blank or multiple-marked questions. The score is printed at the top. The log lists every incorrect question, and anything faint or ambiguous is flagged for a visual check. If the key filename (or key PDF) has a special code, the AutoScorer also warns when the student bubbled a different code.

> AutoScorer results are unofficial. Always spot-check the graded PDF before recording a grade.
