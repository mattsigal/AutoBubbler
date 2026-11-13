import sys
import os
import csv
import re
import fitz  # PyMuPDF
import subprocess # Added for opening folders on Mac
from cryptography.fernet import Fernet # For decryption
from PySide6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, 
                               QWidget, QTextEdit, QProgressBar, QMessageBox, 
                               QPushButton, QHBoxLayout, QDialog, QFormLayout, 
                               QLineEdit, QSpinBox, QDialogButtonBox, QFileDialog,
                               QTabWidget)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QDropEvent, QDragEnterEvent, QIcon

# ================= CONFIG & GEOMETRY =================
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)

def get_desktop_path():
    """ Cross-platform way to get the Desktop path """
    return os.path.join(os.path.expanduser("~"), "Desktop")

APP_VERSION = "v1.6"
BLANK_PDF_NAME = "DocSolScantron.enc" # Now using encrypted file
ICON_NAME = "AutoBubbler.ico" 
EMBEDDED_KEY = b"K19UjqIKt63Ff2SjTHfU6wgj_5sRL-oejBjT1tmBZ50="

# GEOMETRY: SPECIAL CODE (Manual Grid)
SC_START_X = 511.5
SC_START_Y = 351.5
SC_STEP_X = 15.0
SC_STEP_Y = 17.0
BUBBLE_NUDGE_X = -1.0 

# GEOMETRY: SECTION
SECTION_START_X = 435.0
SECTION_LETTER_MAP = {'D': 0, 'E': 1, 'C': 2, 'F': 3, 'S': 4, 'T': 5}

# GEOMETRY: SPECIAL CODE TEXT
SC_TEXT_OFFSET_X = -7
SC_TEXT_OFFSET_Y = -28

# GEOMETRY: QUESTIONS (Visual Grid)
Q_SYMBOLS = {33: "A", 35: "B", 36: "C", 37: "D", 38: "E"}
PAGE1_MIN_Y = 550  

# ================= CORE LOGIC =================
class BubblerLogic:   
    @staticmethod
    def parse_csv(filepath):
        answers = {}
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip Header
                for row in reader:
                    if len(row) >= 2:
                        q_num = row[0].strip()
                        ans = row[1].strip().upper()
                        if q_num and ans:
                            answers[q_num] = ans
        except Exception as e:
            raise ValueError(f"CSV Error: {e}")
        return answers

    @staticmethod
    def extract_special_code(filename):
        match = re.search(r"v(\d+)", filename)
        if match:
            return match.group(1)
        return None 

    @staticmethod
    def extract_section_code(filename):
        search_name = filename.upper()
        # Look for pattern: Letter + 3 Digits (e.g. D100, E203)
        # We look for valid letters only: D, E, C, F, S, T
        # Use word boundaries \b to avoid matching things like "PSYC301" -> "C301"
        match = re.search(r"\b([DECFST])\s*(\d{3})\b", search_name)
        if match:
            return match.group(1) + match.group(2) # e.g. "D100"
        return None 

    @staticmethod
    def get_center(bbox):
        return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]

    @staticmethod
    def cluster_by_x(items, gap=15):
        items.sort(key=lambda i: i["center"][0])
        cols = []
        curr = [items[0]]
        for x in items[1:]:
            if x["center"][0] - curr[-1]["center"][0] < gap:
                curr.append(x)
            else:
                cols.append(curr)
                curr = [x]
        cols.append(curr)
        return cols

    @staticmethod
    def cluster_by_y(items, gap=6):
        items.sort(key=lambda i: i["center"][1])
        rows = []
        curr = [items[0]]
        for x in items[1:]:
            if x["center"][1] - curr[-1]["center"][1] < gap:
                curr.append(x)
            else:
                rows.append(curr)
                curr = [x]
        rows.append(curr)
        return rows

    @classmethod
    def map_questions(cls, doc):
        mapping = {}
        for page_num, page in enumerate(doc):
            raw = page.get_text("rawdict")
            page_bubbles = []
            
            for block in raw["blocks"]:
                if "lines" not in block: continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        for char in span["chars"]:
                            c_val = ord(char["c"])
                            center = cls.get_center(char["bbox"])
                            if c_val in Q_SYMBOLS:
                                if page_num == 0 and center[1] < PAGE1_MIN_Y:
                                    continue
                                page_bubbles.append({
                                    "choice": Q_SYMBOLS[c_val],
                                    "center": center
                                })
            
            if page_bubbles:
                cols = cls.cluster_by_x(page_bubbles, gap=50)
                cols.sort(key=lambda c: c[0]["center"][0])
                
                if page_num == 0: col_starts = [1, 11, 21, 31]
                else: col_starts = [41, 81, 121, 161]
                
                for i, col_items in enumerate(cols):
                    if i >= len(col_starts): break
                    start_q = col_starts[i]
                    
                    rows = cls.cluster_by_y(col_items)
                    rows.sort(key=lambda r: r[0]["center"][1])
                    
                    for r_idx, row in enumerate(rows):
                        q_num = str(start_q + r_idx)
                        if q_num not in mapping: 
                            mapping[q_num] = {"page": page_num}
                        
                        for bubble in row:
                            mapping[q_num][bubble["choice"]] = bubble["center"]
        return mapping

    @staticmethod
    def fill_pdf(doc, q_map, answers, special_code, ref_text, section_code=None):
        # 1. FILL QUESTIONS
        for q, choice in answers.items():
            if q in q_map and choice in q_map[q]:
                pt = q_map[q][choice]
                page = doc[q_map[q]["page"]]
                x = pt[0] + BUBBLE_NUDGE_X
                y = pt[1]
                page.draw_circle(fitz.Point(x, y), radius=6, color=(0,0,0), fill=(0,0,0))

        # 2. FILL SPECIAL CODE
        page = doc[0]
        for i, char in enumerate(special_code):
            if not char.isdigit(): continue
            digit = int(char)
            
            base_x = SC_START_X + (i * SC_STEP_X)
            base_y = SC_START_Y + (digit * SC_STEP_Y)
            bubble_x = base_x + BUBBLE_NUDGE_X
            bubble_y = base_y
            
            page.draw_circle(fitz.Point(bubble_x, bubble_y), radius=4.5, color=(0,0,0), fill=(0,0,0))
            
            box_y = SC_START_Y + SC_TEXT_OFFSET_Y
            text_pt = fitz.Point(bubble_x + SC_TEXT_OFFSET_X, box_y)
            page.insert_text(text_pt, char, fontsize=14, color=(0,0,0))
            
        # 3. FILL SECTION CODE (if present)
        if section_code:
            # section_code is e.g. "D100"
            # Char 0 is Letter, chars 1-3 are digits
            
            for i, char in enumerate(section_code):
                base_x = SECTION_START_X + (i * SC_STEP_X)
                row_idx = -1
                
                if i == 0: # Letter Column
                    if char in SECTION_LETTER_MAP:
                        row_idx = SECTION_LETTER_MAP[char]
                else: # Digit Columns
                    if char.isdigit():
                        row_idx = int(char)
                
                if row_idx != -1:
                     base_y = SC_START_Y + (row_idx * SC_STEP_Y)
                     bubble_x = base_x + BUBBLE_NUDGE_X
                     bubble_y = base_y
                     
                     # Fill Bubble
                     page.draw_circle(fitz.Point(bubble_x, bubble_y), radius=4.5, color=(0,0,0), fill=(0,0,0))
                              
                bubble_x_text = base_x + BUBBLE_NUDGE_X
                box_y = SC_START_Y + SC_TEXT_OFFSET_Y
                text_pt = fitz.Point(bubble_x_text + SC_TEXT_OFFSET_X + 1.5, box_y)
                page.insert_text(text_pt, char, fontsize=14, color=(0,0,0))

        # 3. PRINT REFERENCE FILENAME
        # Prints the source filename (e.g. "PSYC100-v1000") at the top center
        # to help instructors identify which key is which.
        rect = page.rect
        mid_x = rect.width / 2
        top_y = 60 # Slightly below top margin
        
        text_len = fitz.get_text_length(ref_text, fontname="helv", fontsize=12)
        start_x = mid_x - (text_len / 2)
        
        padding_x = 10
        padding_y = 5
        
        rect_x0 = start_x - padding_x
        rect_y0 = top_y - 12 - padding_y # 12 is approx cap height/ascent for size 12
        rect_x1 = start_x + text_len + padding_x
        rect_y1 = top_y + padding_y
        
        page.draw_rect(fitz.Rect(rect_x0, rect_y0, rect_x1, rect_y1), color=(0,0,0), width=1)
        page.insert_text(fitz.Point(start_x, top_y), ref_text, fontname="helv", fontsize=12, color=(0,0,0))


# ================= SHARED HELPERS =================
def load_blank_pdf_bytes():
    """Decrypt the embedded blank Scantron template in memory."""
    path = resource_path(BLANK_PDF_NAME)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find blank PDF at {path}")
    with open(path, "rb") as f:
        enc_data = f.read()
    return Fernet(EMBEDDED_KEY).decrypt(enc_data)


def open_file_externally(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
    except Exception:
        pass


# ================= WORKER THREADS =================
class Worker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, file_urls):
        super().__init__()
        self.file_urls = file_urls

    def run(self):
        try:
            pdf_bytes = load_blank_pdf_bytes()
            base_doc = fitz.open("pdf", pdf_bytes)
            grid_map = BubblerLogic.map_questions(base_doc)
            base_doc.close()
        except Exception as e:
            self.log_signal.emit(f"CRITICAL ERROR mapping/decrypting PDF: {e}")
            self.finished_signal.emit()
            return

        for file_path in self.file_urls:
            filename = os.path.basename(file_path)
            if not filename.lower().endswith('.csv'):
                self.log_signal.emit(f"Skipping non-CSV: {filename}")
                continue

            self.log_signal.emit(f"Processing: {filename}...")

            try:
                special_code = BubblerLogic.extract_special_code(filename)
                section_code = BubblerLogic.extract_section_code(filename)
                answers = BubblerLogic.parse_csv(file_path)

                if special_code is None:
                    special_code = "0000"
                    self.log_signal.emit(f"  > Warning: No 'vXXXX' in filename. Using code 0000.")

                if section_code:
                    self.log_signal.emit(f"  > Found Section Code: {section_code}")

                doc = fitz.open("pdf", pdf_bytes)
                ref_text = os.path.splitext(filename)[0]
                BubblerLogic.fill_pdf(doc, grid_map, answers, special_code, ref_text, section_code)

                output_name = os.path.splitext(filename)[0] + ".pdf"
                source_dir = os.path.dirname(file_path)

                if os.access(source_dir, os.W_OK):
                    output_path = os.path.join(source_dir, output_name)
                else:
                    output_path = os.path.join(get_desktop_path(), output_name)
                    self.log_signal.emit(f"  > Note: Source folder is read-only. Saving to Desktop instead.")

                doc.save(output_path)
                doc.close()
                self.log_signal.emit(f"  > Success! Saved to: {output_path}")

            except Exception as e:
                self.log_signal.emit(f"  > Error: {e}")

        self.finished_signal.emit()


_SCORER_TEMPLATE = None  # built once per session (rendering + pyramids)


class ScorerWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(str)  # output path, or "" on failure

    def __init__(self, key_path, scan_paths):
        super().__init__()
        self.key_path = key_path
        self.scan_paths = scan_paths

    def run(self):
        global _SCORER_TEMPLATE
        out_path = ""
        try:
            import autoscorer  # lazy: pulls in OpenCV only when needed
            if _SCORER_TEMPLATE is None:
                self.log_signal.emit("Preparing Scantron template...")
                pdf_bytes = load_blank_pdf_bytes()
                base_doc = fitz.open("pdf", pdf_bytes)
                qmap = BubblerLogic.map_questions(base_doc)
                base_doc.close()
                sc_geom = dict(start_x=SC_START_X, start_y=SC_START_Y, step_x=SC_STEP_X,
                               step_y=SC_STEP_Y, nudge_x=BUBBLE_NUDGE_X)
                _SCORER_TEMPLATE = autoscorer.Template(pdf_bytes, qmap, sc_geom)
            out_path, _ = autoscorer.score(self.key_path, self.scan_paths, _SCORER_TEMPLATE,
                                           get_desktop_path(), APP_VERSION,
                                           log=self.log_signal.emit)
            self.log_signal.emit(f"  > Graded PDF saved to: {out_path}")
        except Exception as e:
            name = type(e).__name__
            msg = str(e) if name == "ScoringError" else f"{name}: {e}"
            self.log_signal.emit(f"  > ERROR: {msg}")
            out_path = ""
        self.finished_signal.emit(out_path)


# ================= DIALOGS =================
class TemplateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate Template CSV")
        self.resize(400, 180)

        layout = QFormLayout(self)

        self.course_input = QLineEdit()
        self.course_input.setPlaceholderText("e.g. PSYC100")
        layout.addRow("What is your course code?", self.course_input)

        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("e.g. 1000 (Optional)")
        layout.addRow("Is there a special code?", self.code_input)

        self.num_q_input = QSpinBox()
        self.num_q_input.setRange(1, 200)
        self.num_q_input.setValue(50)
        layout.addRow("How many questions are on the exam?", self.num_q_input)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def get_data(self):
        course = self.course_input.text().strip().upper() or "COURSE"
        code = self.code_input.text().strip()
        if not code:
            code = "0000"
        return course, code, self.num_q_input.value()


# ================= WIDGETS =================
DROP_STYLE_IDLE = """
    QLabel {
        background-color: #2d2d2d;
        color: #cccccc;
        border: 2px dashed #444444;
        border-radius: 10px;
        padding: 20px;
    }
"""
DROP_STYLE_ACTIVE = """
    QLabel {
        background-color: #2d2d2d;
        color: #ffffff;
        border: 2px solid #CC0633;
        border-radius: 10px;
        padding: 20px;
    }
"""
DROP_STYLE_LOADED = """
    QLabel {
        background-color: #263026;
        color: #e0e0e0;
        border: 2px solid #3c7a3c;
        border-radius: 10px;
        padding: 20px;
    }
"""
SMALL_BUTTON_STYLE = """
    QPushButton {
        background-color: #333333;
        color: #aaaaaa;
        border: 1px solid #555555;
        border-radius: 4px;
        font-size: 11px;
    }
    QPushButton:hover {
        background-color: #444444;
        color: #ffffff;
        border: 1px solid #777777;
    }
    QPushButton:disabled {
        color: #555555;
        border: 1px solid #3a3a3a;
    }
"""


class DropZone(QLabel):
    """A label that accepts dropped files (and optionally click-to-browse)."""
    files_dropped = Signal(list)

    def __init__(self, html, browse_filter=None, browse_multi=False, parent=None):
        super().__init__(html, parent)
        self.setAcceptDrops(True)
        self.setWordWrap(True)
        self.browse_filter = browse_filter
        self.browse_multi = browse_multi
        self.loaded = False
        self.setFont(QFont("Segoe UI", 11))
        self.setStyleSheet(DROP_STYLE_IDLE)
        if browse_filter:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Drop file(s) here, or click to browse")

    def set_loaded(self, loaded):
        self.loaded = loaded
        self.setStyleSheet(DROP_STYLE_LOADED if loaded else DROP_STYLE_IDLE)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(DROP_STYLE_ACTIVE)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.set_loaded(self.loaded)

    def dropEvent(self, event: QDropEvent):
        self.set_loaded(self.loaded)
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if files:
            self.files_dropped.emit(files)

    def mousePressEvent(self, event):
        if not self.browse_filter or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        if self.browse_multi:
            files, _ = QFileDialog.getOpenFileNames(self, "Select file(s)", get_desktop_path(), self.browse_filter)
        else:
            f, _ = QFileDialog.getOpenFileName(self, "Select file", get_desktop_path(), self.browse_filter)
            files = [f] if f else []
        if files:
            self.files_dropped.emit(files)


def make_log_box():
    box = QTextEdit()
    box.setReadOnly(True)
    box.setFont(QFont("Consolas", 10))
    return box


def append_log(box, msg):
    box.append(msg)
    sb = box.verticalScrollBar()
    sb.setValue(sb.maximum())


# ================= MAIN GUI =================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("The AutoBubbler")
        self.resize(980, 660)

        icon_path = resource_path(ICON_NAME)
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Dark Mode Styling
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QLabel {
                color: #e0e0e0;
            }
            QTextEdit {
                background-color: #252526;
                color: #d4d4d4;
                border: 1px solid #3e3e42;
                border-radius: 4px;
            }
            QTabWidget::pane {
                border: none;
            }
            QTabBar::tab {
                background: #2d2d2d;
                color: #aaaaaa;
                padding: 7px 22px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: 12px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background: #CC0633;
                color: #ffffff;
            }
            QTabBar::tab:hover:!selected {
                background: #3a3a3a;
                color: #ffffff;
            }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 15, 20, 15)

        # Header
        self.lbl_title = QLabel("The AutoBubbler")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.lbl_title.setStyleSheet("color: #CC0633;")
        layout.addWidget(self.lbl_title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_bubbler_tab(), "AutoBubbler")
        self.tabs.addTab(self._build_scorer_tab(), "AutoScorer")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # Footer
        footer_layout = QHBoxLayout()
        self.btn_sample = QPushButton("Generate Template CSV")
        self.btn_sample.setFixedSize(140, 25)
        self.btn_sample.setCursor(Qt.PointingHandCursor)
        self.btn_sample.setStyleSheet(SMALL_BUTTON_STYLE)
        self.btn_sample.clicked.connect(self.generate_sample_csv)
        footer_layout.addWidget(self.btn_sample)
        footer_layout.addStretch()
        self.lbl_footer = QLabel(f"SFU Document Solutions Helper, {APP_VERSION}")
        self.lbl_footer.setStyleSheet("font-size: 10px; color: #666666;")
        footer_layout.addWidget(self.lbl_footer)
        layout.addLayout(footer_layout)

    # ---------- Tab 1: AutoBubbler ----------
    def _build_bubbler_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        self.default_html = (
            "<h3 align='center'>DRAG AND DROP YOUR ANSWER KEY CSV FILE(S) HERE</h3>"
            "<p style='line-height: 120%'>"
            "• CSV format: Question Number (Column A), Answer (Column B)<br>"
            "• Filename should include special code as 'v1234' (e.g., PSYC100-v1000.csv)<br>"
            "• Filename can also include section number (e.g., -D106)<br>"
            "• PDF key will be saved to the same directory as the CSV file (or your Desktop if read-only)<br>"
            "• PDF key <span style='color: #ff3333; font-weight: bold;'>MUST</span> be printed in black and white, two-sided, at 300 or 600 DPI, and 100% scale"
            "</p>"
        )
        self.lbl_instructions = DropZone(self.default_html)
        self.lbl_instructions.files_dropped.connect(self.start_processing)
        layout.addWidget(self.lbl_instructions)

        self.txt_log = make_log_box()
        layout.addWidget(self.txt_log)

        lbl_disclaimer = QLabel("IMPORTANT: Do NOT duplicate or distribute generated PDFs for student use.\n"
                                "This app is only intended for generating answer keys.\n"
                                "Students MUST submit authentic Scantron forms to Document Solutions for grading purposes.")
        lbl_disclaimer.setAlignment(Qt.AlignCenter)
        lbl_disclaimer.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold; margin-top: 5px;")
        layout.addWidget(lbl_disclaimer)
        return page

    # ---------- Tab 2: AutoScorer ----------
    def _build_scorer_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        self.score_key = None
        self.score_scans = []

        zones = QHBoxLayout()
        zones.setSpacing(12)
        self.key_zone = DropZone("", "Answer keys (*.csv *.pdf)")
        self.key_zone.files_dropped.connect(self.on_key_dropped)
        self.scan_zone = DropZone("", "Scans/photos (*.pdf *.jpg *.jpeg *.png *.tif *.tiff)", browse_multi=True)
        self.scan_zone.files_dropped.connect(self.on_scan_dropped)
        for z, stretch in ((self.key_zone, 2), (self.scan_zone, 3)):
            z.setMinimumHeight(170)
            z.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            zones.addWidget(z, stretch)
        layout.addLayout(zones)

        btn_row = QHBoxLayout()
        self.btn_grade = QPushButton("Grade Scantron")
        self.btn_grade.setFixedHeight(32)
        self.btn_grade.setCursor(Qt.PointingHandCursor)
        self.btn_grade.setStyleSheet("""
            QPushButton { background-color: #CC0633; color: white; border: none;
                          border-radius: 4px; font-size: 13px; font-weight: bold; padding: 0 24px; }
            QPushButton:hover { background-color: #e0103f; }
            QPushButton:disabled { background-color: #4a2a31; color: #888888; }
        """)
        self.btn_grade.clicked.connect(self.start_scoring)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setFixedSize(80, 32)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setStyleSheet(SMALL_BUTTON_STYLE)
        self.btn_clear.clicked.connect(self.clear_scoring)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_grade)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.score_log = make_log_box()
        layout.addWidget(self.score_log)

        lbl_disclaimer = QLabel("AutoScorer is for ONE-OFF grading only (e.g., a single makeup exam).\n"
                                "Whole-class grading MUST go through Document Solutions. "
                                "Always spot-check the graded PDF.")
        lbl_disclaimer.setAlignment(Qt.AlignCenter)
        lbl_disclaimer.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold; margin-top: 5px;")
        layout.addWidget(lbl_disclaimer)

        self._refresh_scorer_ui()
        return page

    def _refresh_scorer_ui(self):
        if self.score_key:
            key_html = ("<h3>1. ANSWER KEY &#10003;</h3>"
                        f"<p><b>{os.path.basename(self.score_key)}</b></p>"
                        "<p style='color:#999999'>Drop another file to replace.</p>")
        else:
            key_html = ("<h3>1. ANSWER KEY</h3>"
                        "<p style='line-height: 120%'>Drop the key here (or click):<br>"
                        "• The original CSV, <i>or</i><br>"
                        "• The PDF key made by AutoBubbler</p>")
        if self.score_scans:
            names = "<br>".join(os.path.basename(p) for p in self.score_scans)
            scan_html = ("<h3>2. STUDENT SCANTRON &#10003;</h3>"
                         f"<p><b>{names}</b></p>"
                         "<p style='color:#999999'>Drop another file to replace.</p>")
        else:
            scan_html = ("<h3>2. STUDENT SCANTRON</h3>"
                         "<p style='line-height: 120%'>Drop ONE student's sheet here (or click):<br>"
                         "• A scanned PDF (preferred), or a phone photo (JPG/PNG)<br>"
                         "• More than 40 questions? Include both pages (2-page PDF or two photos)</p>")
        self.key_zone.setText(key_html)
        self.key_zone.set_loaded(bool(self.score_key))
        self.scan_zone.setText(scan_html)
        self.scan_zone.set_loaded(bool(self.score_scans))
        busy = getattr(self, "score_worker", None) is not None and self.score_worker.isRunning()
        self.btn_grade.setEnabled(bool(self.score_key and self.score_scans) and not busy)

    def on_key_dropped(self, files):
        # Allow dropping key + scan together onto the key zone
        keys = [f for f in files if f.lower().endswith((".csv", ".pdf"))]
        if len(files) > 1:
            csvs = [f for f in files if f.lower().endswith(".csv")]
            if csvs:
                self.score_key = csvs[0]
                rest = [f for f in files if f != csvs[0]]
                if rest:
                    self.on_scan_dropped(rest)
                    return
            else:
                append_log(self.score_log, "Please drop only ONE answer key.")
                return
        elif keys:
            self.score_key = keys[0]
        else:
            append_log(self.score_log, "The answer key must be a .csv or AutoBubbler .pdf file.")
            return
        self._refresh_scorer_ui()

    def on_scan_dropped(self, files):
        csvs = [f for f in files if f.lower().endswith(".csv")]
        if csvs:  # a CSV can only be a key
            self.score_key = csvs[0]
            files = [f for f in files if f not in csvs]
        pdfs = [f for f in files if f.lower().endswith(".pdf")]
        imgs = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"))]
        if files:
            if pdfs and imgs:
                append_log(self.score_log, "Please drop either one PDF or photo(s), not both.")
            elif len(pdfs) > 1:
                append_log(self.score_log, "Please drop only ONE student's scan (a single PDF; "
                                           "use a 2-page PDF for front + back).")
            elif len(imgs) > 2:
                append_log(self.score_log, "Please drop at most TWO photos (front + back of one sheet).")
            elif pdfs or imgs:
                self.score_scans = pdfs or imgs
            else:
                append_log(self.score_log, "Unsupported scan file type. Use PDF, JPG or PNG.")
        self._refresh_scorer_ui()

    def clear_scoring(self):
        self.score_key = None
        self.score_scans = []
        self.score_log.clear()
        self._refresh_scorer_ui()

    def start_scoring(self):
        if not (self.score_key and self.score_scans):
            return
        self.score_log.clear()
        self.score_worker = ScorerWorker(self.score_key, list(self.score_scans))
        self.score_worker.log_signal.connect(lambda m: append_log(self.score_log, m))
        self.score_worker.finished_signal.connect(self.on_scoring_finished)
        self.btn_grade.setEnabled(False)
        self.btn_grade.setText("Grading...")
        self.score_worker.start()

    def on_scoring_finished(self, out_path):
        self.btn_grade.setText("Grade Scantron")
        self._refresh_scorer_ui()
        if out_path:
            open_file_externally(out_path)

    def _on_tab_changed(self, idx):
        self.btn_sample.setVisible(idx == 0)

    # ---------- AutoBubbler actions ----------
    def generate_sample_csv(self):
        dialog = TemplateDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        course, code, num_q = dialog.get_data()

        # Format: COURSE-vCODE-Key.csv (avoid double v)
        code_str = code if code.lower().startswith('v') else f"v{code}"
        default_filename = f"{course}-{code_str}-Key.csv"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose where to save the template CSV",
            os.path.join(get_desktop_path(), default_filename),
            "CSV Files (*.csv)"
        )
        if not file_path:
            return

        lines = ["Question,Answer"] + [f"{i}," for i in range(1, num_q + 1)]
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.log_msg(f"Generated template file: {file_path}")
            open_file_externally(file_path)
        except Exception as e:
            self.log_msg(f"Error generating template: {e}")

    def start_processing(self, files):
        self.txt_log.clear()
        self.lbl_instructions.setText("<h3 align='center'>Processing...</h3>")

        self.worker = Worker(files)
        self.worker.log_signal.connect(self.log_msg)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def log_msg(self, msg):
        append_log(self.txt_log, msg)

    def on_finished(self):
        self.log_msg("\n--- All Tasks Completed ---")
        self.lbl_instructions.setText(self.default_html)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
