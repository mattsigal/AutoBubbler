import sys
import os
import csv
import re
import fitz  # PyMuPDF
import fitz  # PyMuPDF
import subprocess # Added for opening folders on Mac
from cryptography.fernet import Fernet # For decryption
from PySide6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, 
                               QWidget, QTextEdit, QProgressBar, QMessageBox, 
                               QPushButton, QHBoxLayout)
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

    return os.path.join(os.path.expanduser("~"), "Desktop")

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
                
                # Draw Text (always draw text even if mapping failed? or only if valid? Let's draw valid chars)
                # The text is drawn at the top box (which is above row 0? Or just using the standard offset logic?)
                # For Special Code, the text logic was independent of the row_idx of the bubble.
                # However, in Special Code loop:
                # box_y = SC_START_Y + SC_TEXT_OFFSET_Y -> this meant the text was printed relative to Row 0 START Y.
                # So it prints nicely in the box above.
                
                bubble_x_text = base_x + BUBBLE_NUDGE_X
                box_y = SC_START_Y + SC_TEXT_OFFSET_Y
                # Nudge text slightly right for Section boxes (approx +1.5)
                text_pt = fitz.Point(bubble_x_text + SC_TEXT_OFFSET_X + 1.5, box_y)
                page.insert_text(text_pt, char, fontsize=14, color=(0,0,0))

        # 4. PRINT REFERENCE FILENAME
            
        # 3. PRINT REFERENCE FILENAME
        # Prints the source filename (e.g. "PSYC100-v1000") at the top center
        # to help instructors identify which key is which.
        
        # Calculate text width to center it (approximate)
        # Page width is usually 612 for Letter. 
        # We can just center it by using insert_text with align (if supported) or simple math.
        # But for basics, let's put it at (306, 50) and align center if possible 
        # or just visually guess.
        
        # A better way with PyMuPDF for centering:
        rect = page.rect
        mid_x = rect.width / 2
        top_y = 60 # Slightly below top margin
        
        
        # We need the filename here. Since we are in a static method, we need to pass it in.
        # However, we are currently inside fill_pdf which receives 'answers' and 'special_code'.
        # We will update the signature to accept 'filename_text' or similar.
        
        # Using insert_text with 'morph' to center is complex, simpler to use basic font width estimation
        # or just hardcode a reasonable center if font is monospaced or standard.
        # But actually fitz.TextWriter is good for this, but let's stick to simple insert_text.
        # We will assume "center" is roughly x=306 (Letter width 612).
        # We'll use a standard font like Helvetica-Bold.
        
        text_len = fitz.get_text_length(ref_text, fontname="helv", fontsize=12)
        start_x = mid_x - (text_len / 2)
        
        # Draw box padding
        padding_x = 10
        padding_y = 5
        
        rect_x0 = start_x - padding_x
        rect_y0 = top_y - 12 - padding_y # 12 is approx cap height/ascent for size 12
        rect_x1 = start_x + text_len + padding_x
        rect_y1 = top_y + padding_y # descent is small
        
        # Draw Rectangle (stroked, not filled)
        page.draw_rect(fitz.Rect(rect_x0, rect_y0, rect_x1, rect_y1), color=(0,0,0), width=1)
        
        page.insert_text(fitz.Point(start_x, top_y), ref_text, fontname="helv", fontsize=12, color=(0,0,0))


# ================= WORKER THREAD =================
class Worker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, file_urls):
        super().__init__()
        self.file_urls = file_urls
        self.blank_pdf_path = resource_path(BLANK_PDF_NAME)

    def run(self):
        if not os.path.exists(self.blank_pdf_path):
            self.log_signal.emit(f"ERROR: Could not find blank PDF at {self.blank_pdf_path}")
            self.finished_signal.emit()
            return
        
        try:
            # IN-MEMORY DECRYPTION
            # 1. Read Encrypted File
            with open(self.blank_pdf_path, "rb") as f:
                enc_data = f.read()
            
            # 2. Decrypt
            fernet = Fernet(EMBEDDED_KEY)
            pdf_bytes = fernet.decrypt(enc_data)
            
            # 3. Load from bytes (never save to disk)
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
                
                # Re-open blank doc from memory for each file
                # Since 'pdf_bytes' from above is available in scope (or we should make it self)
                # Correction: pdf_bytes variable is local to the try/except block above.
                # We should move decryption to __init__ or start of run and store it in self.pdf_bytes
                
                # Let's fix the logic flow slightly by moving decryption up.
                # Actually, simpler to just re-decrypt or better yet, store decoded bytes.
                
                doc = fitz.open("pdf", pdf_bytes)
                
                # filename without extension for the reference text
                ref_text = os.path.splitext(filename)[0]
                
                BubblerLogic.fill_pdf(doc, grid_map, answers, special_code, ref_text, section_code)
                
                output_name = os.path.splitext(filename)[0] + ".pdf"
                source_dir = os.path.dirname(file_path)
                
                # --- MAC FIX: Check if Source Directory is Writable ---
                # If running from a translocated/quarantined path, source_dir might be read-only.
                # If so, fallback to Desktop.
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

# ================= MAIN GUI =================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("The AutoBubbler")
        self.resize(600, 500)
        self.setAcceptDrops(True)
        
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
        """)

        # Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header
        self.lbl_title = QLabel("The AutoBubbler")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.lbl_title.setStyleSheet("color: #CC0633;") 
        layout.addWidget(self.lbl_title)

        # Instructions Drop Box
        self.instruction_style_idle = """
            QLabel {
                background-color: #2d2d2d;
                color: #cccccc;
                border: 2px dashed #444444;
                border-radius: 10px;
                padding: 20px;
            }
        """
        self.instruction_style_active = """
            QLabel {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 2px solid #CC0633;
                border-radius: 10px;
                padding: 20px;
            }
        """
        
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

        self.lbl_instructions = QLabel(self.default_html)
        self.lbl_instructions.setFont(QFont("Segoe UI", 11))
        self.lbl_instructions.setStyleSheet(self.instruction_style_idle)
        layout.addWidget(self.lbl_instructions)

        # Log Window
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Consolas", 10))
        layout.addWidget(self.txt_log)

        # Disclaimer
        self.lbl_disclaimer = QLabel("IMPORTANT: Do NOT duplicate or distribute generated PDFs for student use.\nThis app is only intended for generating answer keys. Students must submit authentic Scantron forms to Document Solutions for grading purposes.")
        self.lbl_disclaimer.setAlignment(Qt.AlignCenter)
        self.lbl_disclaimer.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold; margin-top: 5px;")
        layout.addWidget(self.lbl_disclaimer)

        # Footer Layout
        footer_layout = QHBoxLayout()
        
        # Sample Button
        self.btn_sample = QPushButton("Generate Sample CSV")
        self.btn_sample.setFixedSize(140, 25)
        self.btn_sample.setCursor(Qt.PointingHandCursor)
        self.btn_sample.setStyleSheet("""
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
        """)
        self.btn_sample.clicked.connect(self.generate_sample_csv)
        footer_layout.addWidget(self.btn_sample)

        # Spacer
        footer_layout.addStretch()

        # Version Label
        self.lbl_footer = QLabel("SFU Document Solutions Helper, v1.3")
        self.lbl_footer.setStyleSheet("font-size: 10px; color: #666666;")
        footer_layout.addWidget(self.lbl_footer)

        layout.addLayout(footer_layout)

    def generate_sample_csv(self):
        # FIX: Always save to Desktop to avoid Read-only errors on Mac
        desktop = get_desktop_path()
        filename = os.path.join(desktop, "PSYCXXX-v0000-key.csv")
        content = "Question,Answer\n1,A"
        
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            self.log_msg(f"Generated sample file on Desktop: {filename}")
            
            # Cross-platform way to open the folder
            if sys.platform == "win32":
                os.startfile(desktop)
            elif sys.platform == "darwin": # macOS
                subprocess.call(["open", desktop])
                
        except Exception as e:
            self.log_msg(f"Error generating sample: {e}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
            self.lbl_instructions.setStyleSheet(self.instruction_style_active)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.lbl_instructions.setStyleSheet(self.instruction_style_idle)

    def dropEvent(self, event: QDropEvent):
        self.lbl_instructions.setStyleSheet(self.instruction_style_idle)
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        self.start_processing(files)

    def start_processing(self, files):
        self.txt_log.clear()
        self.lbl_instructions.setText("<h3 align='center'>Processing...</h3>")
        
        self.worker = Worker(files)
        self.worker.log_signal.connect(self.log_msg)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def log_msg(self, msg):
        self.txt_log.append(msg)
        sb = self.txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_finished(self):
        self.log_msg("\n--- All Tasks Completed ---")
        self.lbl_instructions.setText(self.default_html)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
