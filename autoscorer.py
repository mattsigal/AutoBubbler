"""
AutoScorer engine for The AutoBubbler.

Grades a single scanned (or photographed) SFU GPAS200 Scantron against an
answer key and produces an annotated PDF. Intended for one-off use only
(e.g. makeup exams) -- full classes must still go through Document Solutions.

This module has no Qt dependency. It is imported lazily by AutoBubbler.py so
that OpenCV is only loaded when the AutoScorer is actually used.
"""
import os
import re
import csv

import numpy as np
import cv2
import fitz  # PyMuPDF

# ================= CONSTANTS =================
DPI = 150                 # working resolution for template + scans
S = DPI / 72.0            # PDF points -> working pixels
OUT_IMG_DPI = 200         # resolution of rectified photo pages in output PDF
MAX_PHOTO_SIDE = 2400     # downscale huge phone photos before processing
CHOICES = "ABCDE"
FRONT_LAST_Q = 40         # questions 1-40 live on the front page

BUBBLE_R = 4.5            # sampling radius (pt) for answer bubbles
SC_BUBBLE_R = 3.5         # sampling radius (pt) for special code bubbles
MIN_ALIGN_CC = 0.30       # below this an alignment is considered failed

GREEN = (0.0, 0.6, 0.0)
RED = (0.85, 0.0, 0.0)
ORANGE = (1.0, 0.5, 0.0)
WHITE = (1, 1, 1)

PDF_EXT = (".pdf",)
IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


class ScoringError(Exception):
    """User-facing error (message is shown in the log as-is)."""


# ================= IMAGE HELPERS =================
def _pix_to_rgb(pix):
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
    return np.ascontiguousarray(img[:, :, :3])


def _flatten(gray):
    """Divide out uneven illumination (phone photos, scanner shading).
    Returns darkness in [0, 1] (0 = paper white, 1 = black)."""
    g = gray.astype(np.float32)
    k = max(31, int(max(g.shape) / 40) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.morphologyEx(g, cv2.MORPH_CLOSE, kernel)
    bg = cv2.GaussianBlur(bg, (k, k), 0)
    norm = np.clip(g / np.maximum(bg, 1.0), 0.0, 1.0)
    return 1.0 - norm


def align_channel(rgb):
    """Min channel: coloured (cyan) and grey form printing both look dark,
    so a real form and the grey template look alike."""
    return _flatten(rgb.min(axis=2))


def ink_channel(rgb):
    """Max channel: graphite/pen is dark in every channel, coloured form
    printing is not -- this keeps marks and suppresses cyan ink."""
    return _flatten(rgb.max(axis=2))


def _order_quad(pts):
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def find_paper_quad(rgb):
    """Locate the sheet of paper in a photo. Returns 4 corners (TL,TR,BR,BL)
    in image pixels, or None."""
    h, w = rgb.shape[:2]
    f = 800.0 / max(h, w)
    small = cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (7, 7), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    area = cv2.contourArea(c)
    img_area = small.shape[0] * small.shape[1]
    if area < 0.20 * img_area or area > 0.97 * img_area:
        return None
    hull = cv2.convexHull(c)
    approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    quad = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
    return _order_quad(quad) / f


# ================= TEMPLATE =================
class Template:
    """The blank GPAS200 form (decrypted vector PDF) plus bubble geometry."""

    def __init__(self, pdf_bytes, qmap, sc_geom):
        doc = fitz.open("pdf", pdf_bytes)
        self.qmap = qmap
        self.sc_geom = sc_geom
        self.rgb = [_pix_to_rgb(p.get_pixmap(dpi=DPI)) for p in doc]
        self.align = [align_channel(im) for im in self.rgb]
        self.ink = [ink_channel(im) for im in self.rgb]
        self.size = [(im.shape[1], im.shape[0]) for im in self.rgb]
        self.page_rect = [p.rect for p in doc]
        self.pyr = [_Pyramid(a, PYR) for a in self.align]
        doc.close()


# ================= ALIGNMENT =================
def _scale_h(H, f):
    D = np.diag([f, f, 1.0])
    return D @ H @ np.linalg.inv(D)


def _ecc(t_img, s_img, H, iters, eps=1e-5):
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, eps)
    try:
        cc, Hn = cv2.findTransformECC(t_img, s_img, H.astype(np.float32),
                                      cv2.MOTION_HOMOGRAPHY, crit, None, 1)
        return float(cc), Hn.astype(np.float64)
    except cv2.error:
        return -1.0, H


class _Pyramid:
    def __init__(self, img, levels):
        self.lv = {}
        for f, k in levels:
            im = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f != 1 else img
            self.lv[f] = cv2.GaussianBlur(im, (k, k), 0)


PYR = ((0.125, 5), (0.25, 5), (0.5, 3), (1.0, 3))
GOOD_COARSE_CC = 0.80


def align_coarse(sp, tp, quad_candidates, tmpl_size):
    """Coarse homography search (template px -> scan px). Tries each
    candidate page outline in 4 orientations at 1/8 scale, then polishes
    the best at 1/4 scale. Returns (cc, H) in full-resolution pixels."""
    tw, th = tmpl_size
    t_corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
    best = (-2.0, None)
    for quad in quad_candidates:
        for rot in range(4):
            H0 = cv2.getPerspectiveTransform(t_corners, np.roll(quad, -rot, axis=0)).astype(np.float64)
            cc, H = _ecc(tp.lv[0.125], sp.lv[0.125], _scale_h(H0, 0.125), 100, 1e-4)
            if cc > best[0]:
                best = (cc, _scale_h(H, 8.0))
            if cc > GOOD_COARSE_CC:
                break  # upright full-page scans hit this on the first try
        if best[0] > GOOD_COARSE_CC:
            break
    cc, H = best
    if H is None:
        return -1.0, None
    c2, H2 = _ecc(tp.lv[0.25], sp.lv[0.25], _scale_h(H, 0.25), 100, 1e-5)
    if c2 >= 0:
        cc, H = c2, _scale_h(H2, 4.0)
    return cc, H


def align_refine(sp, tp, cc, H):
    # Half resolution (75 dpi) is enough: the per-question shift search in
    # read_answers absorbs the remaining 1-2 px, and full-res ECC is slow.
    for f, iters, eps in ((0.5, 40, 1e-4),):
        c2, H2 = _ecc(tp.lv[f], sp.lv[f], _scale_h(H, f), iters, eps)
        if c2 < 0:
            break
        cc, H = c2, _scale_h(H2, 1.0 / f)
    return cc, H


# ================= INPUT LOADING =================
class ScanPage:
    def __init__(self, rgb, kind, src, pno=0, scale=1.0):
        self.rgb = rgb            # upright visual image
        self.kind = kind          # 'pdf' or 'image'
        self.src = src            # file path
        self.pno = pno            # page index in PDF
        self.scale = scale        # working px per original px (images)
        self.align = align_channel(rgb)
        self.ink = ink_channel(rgb)
        self.H = None             # template px -> working px
        self.cc = None
        self.tpage = None         # matched template page (0 front, 1 back)

    def quad_candidates(self):
        h, w = self.rgb.shape[:2]
        cands = [np.float32([[0, 0], [w, 0], [w, h], [0, h]])]
        q = find_paper_quad(self.rgb)
        if q is not None:
            cands.append(q)
        return cands


def _imread(path):
    data = np.fromfile(path, np.uint8)  # unicode-safe on Windows
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ScoringError(f"Could not read image: {os.path.basename(path)} "
                           "(use JPG or PNG; iPhone HEIC photos must be exported as JPG).")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def load_pages(paths):
    pages = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in PDF_EXT:
            doc = fitz.open(path)
            for i, p in enumerate(doc):
                pages.append(ScanPage(_pix_to_rgb(p.get_pixmap(dpi=DPI)), "pdf", path, i))
            doc.close()
        elif ext in IMG_EXT:
            rgb = _imread(path)
            f = min(1.0, MAX_PHOTO_SIDE / max(rgb.shape[:2]))
            if f < 1.0:
                rgb = cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            pages.append(ScanPage(rgb, "image", path, 0, f))
        else:
            raise ScoringError(f"Unsupported file type: {os.path.basename(path)}")
    return pages


def match_pages(pages, tmpl, need_back, log):
    """Align each page against the needed template pages and assign
    front/back by best correlation (so page order doesn't matter)."""
    tpages = [0, 1] if need_back else [0]
    results = {}
    for i, pg in enumerate(pages):
        cands = pg.quad_candidates()
        sp = _Pyramid(pg.align, PYR)
        pg._pyr = sp
        for t in tpages:
            results[(i, t)] = align_coarse(sp, tmpl.pyr[t], cands, tmpl.size[t])

    assigned = {}
    for t in sorted(tpages, key=lambda t: -max(results[(i, t)][0] for i in range(len(pages)))):
        free = [i for i in range(len(pages)) if i not in assigned.values()]
        if not free:
            break
        i = max(free, key=lambda i: results[(i, t)][0])
        cc, H = results[(i, t)]
        name = "front" if t == 0 else "back"
        if H is not None and cc >= MIN_ALIGN_CC:
            cc, H = align_refine(pages[i]._pyr, tmpl.pyr[t], cc, H)
        if cc < MIN_ALIGN_CC or H is None:
            raise ScoringError(
                f"Could not line up the {name} page of the Scantron (match {cc:.2f}). "
                "Try a flatbed scan, or a straight-on photo of the whole sheet on a dark background.")
        pg = pages[i]
        pg.H, pg.cc, pg.tpage = H, cc, t
        assigned[t] = i
        log(f"  > {name.capitalize()} page: {os.path.basename(pg.src)}"
            f"{' p' + str(pg.pno + 1) if pg.kind == 'pdf' else ''} (alignment {cc:.2f})")
    if need_back and 1 not in assigned:
        raise ScoringError("This key has more than 40 questions, so the BACK of the Scantron is "
                           "also required. Provide a 2-page PDF (front + back) or two photos.")
    return {t: pages[i] for t, i in assigned.items()}


# ================= READING =================
def _warp_ink(pg, tmpl):
    tw, th = tmpl.size[pg.tpage]
    return cv2.warpPerspective(pg.ink, pg.H, (tw, th),
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=0)


def _disk_mean_map(ink_s, ink_t, r_pt):
    """Mean positive residual (scan ink minus template ink) within a disk of
    radius r_pt centred on every pixel -- one filter pass per page."""
    resid = np.clip(ink_s - ink_t, 0, None).astype(np.float32)
    r = r_pt * S
    k = int(np.ceil(r))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    disk = ((xx ** 2 + yy ** 2) <= r * r).astype(np.float32)
    disk /= disk.sum()
    return cv2.filter2D(resid, -1, disk, borderType=cv2.BORDER_CONSTANT)


def _lookup(dmap, pt, dx=0, dy=0):
    x = int(round(pt[0] * S)) + dx
    y = int(round(pt[1] * S)) + dy
    h, w = dmap.shape
    if 0 <= x < w and 0 <= y < h:
        return float(dmap[y, x])
    return 0.0


def _best_shift_darkness(dmap, pts, search_px=3):
    """Sample a row of bubbles allowing a small common shift, to absorb
    residual misalignment (lens distortion, paper curl). The chosen shift is
    the one where the *unfilled* bubbles show the least residual, i.e. where
    the printed rings line up best with the template."""
    best = None
    for dy in range(-search_px, search_px + 1):
        for dx in range(-search_px, search_px + 1):
            vals = [_lookup(dmap, p, dx, dy) for p in pts]
            score = sorted(vals)[len(vals) // 2]
            if best is None or score < best[0] - 1e-6:
                best = (score, vals)
    return best[1]


def _classify(scores, empty, filled):
    """Normalise each bubble to 0 (typical empty) .. 1 (typical filled
    on THIS sheet), so light pencil and dark pen are treated alike."""
    span = max(filled - empty, 0.10)
    rel = {c: (s - empty) / span for c, s in scores.items()}
    ranked = sorted(rel.items(), key=lambda kv: -kv[1])
    c1, r1 = ranked[0]
    marked = [c for c, r in ranked if r >= 0.5 and r >= 0.6 * r1]
    if r1 < 0.5:
        status = "blank"
    elif len(marked) > 1:
        status = "multi"
    else:
        status = "mark"
    # anything sitting near the decision boundary deserves a human look
    low_conf = any(0.35 <= r < 0.60 for _, r in ranked[:2])
    return status, c1, marked, low_conf


def _sheet_levels(raw):
    allv = np.array([v for d in raw.values() for v in d.values()])
    empty = float(np.median(allv))
    tops = np.array([max(d.values()) for d in raw.values()])
    strong = tops[tops >= empty + 0.12]
    filled = float(np.median(strong)) if len(strong) else empty + 0.25
    return empty, max(filled, empty + 0.12)


def read_answers(pages_by_t, tmpl, questions):
    """Return {q: dict(read, status, marked, scores, low_conf)}."""
    out = {}
    for t, pg in pages_by_t.items():
        ink_s = _warp_ink(pg, tmpl)
        dmap = _disk_mean_map(ink_s, tmpl.ink[t], BUBBLE_R)
        # read EVERY question on the page so the empty/filled levels are
        # estimated from plenty of bubbles, then keep only those requested
        qs = [int(q) for q, e in tmpl.qmap.items() if e.get("page") == t]
        wanted = set(questions)
        raw = {}
        for q in qs:
            e = tmpl.qmap[str(q)]
            cs = [c for c in CHOICES if c in e]
            vals = _best_shift_darkness(dmap, [e[c] for c in cs])
            raw[q] = dict(zip(cs, vals))
        if not raw:
            continue
        empty, filled = _sheet_levels(raw)
        for q, sc in raw.items():
            if q not in wanted:
                continue
            status, top, marked, low = _classify(sc, empty, filled)
            out[q] = dict(read=top if status == "mark" else None, status=status,
                          marked=marked, scores=sc, low_conf=low, thr=(empty + filled) / 2)
    return out


def read_special_code(front_pg, tmpl):
    g = tmpl.sc_geom
    ink_s = _warp_ink(front_pg, tmpl)
    dmap = _disk_mean_map(ink_s, tmpl.ink[0], SC_BUBBLE_R)
    cols = []
    for i in range(4):
        x = g["start_x"] + i * g["step_x"] + g["nudge_x"]
        pts = [(x, g["start_y"] + d * g["step_y"]) for d in range(10)]
        vals = _best_shift_darkness(dmap, pts, search_px=2)
        order = np.argsort(vals)[::-1]
        med = float(np.median(vals))
        top, second = vals[order[0]], vals[order[1]]
        if top >= max(0.18, med + 0.15) and second < 0.6 * top:
            cols.append(str(order[0]))
        else:
            cols.append("_")
    code = "".join(cols)
    return None if code == "____" else code


# ================= KEY =================
def parse_key_csv(path):
    key = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                try:
                    q = int(float(row[0].strip()))
                except ValueError:
                    continue
                ans = "".join(c for c in row[1].strip().upper() if c in CHOICES)
                if ans:
                    key[q] = ans
    if not key:
        raise ScoringError("No answers found in the key CSV (Column A = question, Column B = answer).")
    return key


def load_key(path, tmpl, log):
    """Key from CSV, or from an AutoBubbler key PDF / scanned key sheet."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return parse_key_csv(path), None
    pages = load_pages([path])
    pages_by_t = match_pages(pages, tmpl, need_back=len(pages) >= 2, log=lambda m: None)
    res = read_answers(pages_by_t, tmpl, range(1, 201))
    key = {q: "".join(r["marked"]) for q, r in res.items() if r["status"] in ("mark", "multi")}
    if not key:
        raise ScoringError("No filled bubbles found on the key PDF.")
    sc = read_special_code(pages_by_t[0], tmpl) if 0 in pages_by_t else None
    return key, sc


# ================= OUTPUT =================
class _Painter:
    """Draws in template coordinates onto an output page."""

    def __init__(self, page, to_out, rot):
        self.page, self.to_out, self.rot = page, to_out, rot

    def _vis(self, p):  # output point -> visual space (for offsets)
        return p * self.page.rotation_matrix

    def _unvis(self, p):
        return p * self.page.derotation_matrix

    def ring(self, pt, color, r=8.0, width=2.0):
        self.page.draw_circle(self.to_out(pt), r, color=color, width=width)

    def cross(self, pt, color, d=6.0, width=2.0):
        v = self._vis(self.to_out(pt))
        self.page.draw_line(self._unvis(v + (-d, -d)), self._unvis(v + (d, d)), color=color, width=width)
        self.page.draw_line(self._unvis(v + (-d, d)), self._unvis(v + (d, -d)), color=color, width=width)

    def text(self, pt, txt, size, color, bold=False):
        self.page.insert_text(self.to_out(pt), txt, fontsize=size, color=color,
                              fontname="hebo" if bold else "helv", rotate=self.rot)

    def box(self, rect_pts, color, fill=WHITE, width=1.5):
        (x0, y0), (x1, y1) = rect_pts
        q = fitz.Quad(self.to_out((x0, y0)), self.to_out((x1, y0)),
                      self.to_out((x0, y1)), self.to_out((x1, y1)))
        self.page.draw_quad(q, color=color, fill=fill, width=width)


def _make_mapper(pg, page, out_kind):
    if out_kind == "pdf":
        H = pg.H
        k = 72.0 / DPI
        derot = page.derotation_matrix

        def to_out(pt):
            v = H @ np.array([pt[0] * S, pt[1] * S, 1.0])
            return fitz.Point(v[0] / v[2] * k, v[1] / v[2] * k) * derot
    else:
        def to_out(pt):
            return fitz.Point(pt[0], pt[1])
    return to_out


def _rectified_page(out_doc, pg, tmpl):
    """For photos: warp the original photo onto a clean letter page."""
    rect = tmpl.page_rect[pg.tpage]
    ow, oh = int(rect.width / 72 * OUT_IMG_DPI), int(rect.height / 72 * OUT_IMG_DPI)
    full = _imread(pg.src)
    # H maps template px (DPI) -> working px; compose to output px -> original px
    A = np.diag([DPI / OUT_IMG_DPI, DPI / OUT_IMG_DPI, 1.0])
    B = np.diag([1.0 / pg.scale, 1.0 / pg.scale, 1.0])
    M = B @ pg.H @ A
    warped = cv2.warpPerspective(full, M, (ow, oh), flags=cv2.INTER_AREA | cv2.WARP_INVERSE_MAP,
                                 borderValue=(255, 255, 255))
    ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(warped, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, 88])
    page = out_doc.new_page(width=rect.width, height=rect.height)
    page.insert_image(rect, stream=jpg.tobytes())


def annotate(pages_by_t, all_pages, tmpl, key, res, summary, out_path, version):
    first = all_pages[0]
    if first.kind == "pdf":
        out_doc = fitz.open(first.src)
        page_for = {t: out_doc[pg.pno] for t, pg in pages_by_t.items()}
        kinds = {t: "pdf" for t in pages_by_t}
    else:
        out_doc = fitz.open()
        page_for, kinds = {}, {}
        order = sorted(pages_by_t)
        for t in order:
            _rectified_page(out_doc, pages_by_t[t], tmpl)
        for idx, t in enumerate(order):  # re-fetch: new_page() can orphan old refs
            page_for[t] = out_doc[idx]
            kinds[t] = "image"

    for t, pg in pages_by_t.items():
        page = page_for[t]
        p = _Painter(page, _make_mapper(pg, page, kinds[t]),
                     page.rotation if kinds[t] == "pdf" else 0)
        for q in sorted(key):
            e = tmpl.qmap.get(str(q))
            if not e or e["page"] != t:
                continue
            r = res.get(q)
            for k in key[q]:
                p.ring(e[k], GREEN)
            if r["status"] == "mark" and r["read"] in key[q]:
                continue
            m_pt = (e["A"][0] - 44, e["A"][1] + 3.5)
            if r["status"] == "mark":
                p.cross(e[r["read"]], RED)
                p.text(m_pt, "X", 11, RED, bold=True)
            elif r["status"] == "multi":
                for c in r["marked"]:
                    p.cross(e[c], ORANGE)
                p.text(m_pt, "MX", 8, ORANGE, bold=True)
            else:
                p.text(m_pt, "BL", 8, ORANGE, bold=True)

        if t == 0:
            p.box(((301, 18), (421, 54)), RED)
            p.text((307, 33), "AutoScorer (unofficial)", 7.5, RED)
            p.text((307, 49), f"{summary['correct']}/{summary['total']}  ({summary['pct']:.1f}%)", 13, RED, bold=True)
            legend_y = 729
        else:
            p.text((60, 30), f"AutoScorer (unofficial) - back page - Total: {summary['correct']}/"
                             f"{summary['total']} ({summary['pct']:.1f}%)", 9, RED, bold=True)
            legend_y = 733
        p.text((60, legend_y), "Green ring = correct answer   Red X = incorrect response   "
                               "BL = blank   MX = multiple marks", 7, RED)
        p.text((60, legend_y + 9), f"UNOFFICIAL - AutoBubbler {version} AutoScorer. For one-off use only; "
                                   "not a substitute for Document Solutions grading.", 7, RED)

    out_doc.save(out_path, garbage=3, deflate=True)
    out_doc.close()


# ================= ENTRY POINT =================
def score(key_path, scan_paths, tmpl, out_dir_fallback, version, log=print):
    """Grade one Scantron. Returns (out_path, summary dict)."""
    log(f"Key:  {os.path.basename(key_path)}")
    key, key_sc = load_key(key_path, tmpl, log)
    from_name = None
    m = re.search(r"v(\d{4})", os.path.basename(key_path))
    if m:
        from_name = m.group(1)
    key_code = from_name or key_sc
    n_q, max_q = len(key), max(key)
    need_back = max_q > FRONT_LAST_Q
    log(f"  > {n_q} questions (highest #{max_q}) - "
        f"{'front AND back required' if need_back else 'front page only'}")

    log("Scan: " + ", ".join(os.path.basename(p) for p in scan_paths))
    pages = load_pages(scan_paths)
    if len(pages) > 2:
        raise ScoringError(f"The scan has {len(pages)} pages. AutoScorer grades ONE Scantron at a time "
                           "(1 page, or 2 pages for front + back).")
    if need_back and len(pages) < 2:
        raise ScoringError("This key has more than 40 questions, so the BACK of the Scantron is also "
                           "required. Provide a 2-page PDF (front + back) or two photos.")
    if len({p.kind for p in pages}) > 1:
        raise ScoringError("Please provide either one PDF or image files, not a mix.")

    pages_by_t = match_pages(pages, tmpl, need_back, log)
    res = read_answers(pages_by_t, tmpl, sorted(key))

    sheet_sc = read_special_code(pages_by_t[0], tmpl)
    if key_code and sheet_sc and sheet_sc != key_code:
        log(f"  > WARNING: Special code on sheet is {sheet_sc} but the key is v{key_code}. "
            "Is this the right exam version?")
    elif sheet_sc:
        log(f"  > Special code on sheet: {sheet_sc}")

    correct = 0
    issues = []
    for q in sorted(key):
        r = res[q]
        ok = r["status"] == "mark" and r["read"] in key[q]
        correct += ok
        if not ok:
            got = {"mark": r["read"], "multi": "/".join(r["marked"]) + " (multiple)",
                   "blank": "blank"}[r["status"]]
            issues.append(f"    Q{q:<3} key {key[q]:<3} student {got}")
        if r["low_conf"]:
            issues.append(f"    Q{q:<3} ** faint/ambiguous mark - please check visually")
    pct = 100.0 * correct / n_q
    summary = dict(correct=correct, total=n_q, pct=pct, details=res, key=key)

    if issues:
        log("  > Incorrect / flagged:")
        for line in issues:
            log(line)
    log(f"  > SCORE: {correct}/{n_q} ({pct:.1f}%)")

    front_src = pages_by_t[0].src
    base = os.path.splitext(os.path.basename(front_src))[0]
    out_name = f"{base}-GRADED.pdf"
    src_dir = os.path.dirname(front_src)
    out_dir = src_dir if os.access(src_dir, os.W_OK) else out_dir_fallback
    out_path = os.path.join(out_dir, out_name)
    annotate(pages_by_t, pages, tmpl, key, res, summary, out_path, version)
    return out_path, summary
