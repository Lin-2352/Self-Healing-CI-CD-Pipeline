"""Check the rendered pages of the report for layout problems.

    python docs/report-kiit/check_layout.py [path/to/report.pdf] [--dpi 100] [--max-gap 0.30] [--skip-front N]

Renders every page with pdftoppm (shipped with MiKTeX) and reports, per page:
  border   the 1.5 pt page border is missing, or anything dark sits within 3 mm inside it or outside it
  gap      a hole in the middle of a page: the longest run of empty rows between the first and last text
           row, as a fraction of the page's text area, is above --max-gap (the empty end of a chapter's last
           page is not a hole)
  margin   dark pixels beyond the left/right text margins (text running into the margin)

Exit code 1 if any page is reported. The pure functions are tested in tests/test_check_layout.py.
"""
import os, shutil, subprocess, sys, tempfile
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BORDER_MM = 7.0          # border rectangle sits 0.7 cm inside the paper edge
LEFT_RIGHT_MM = 19.0     # text margins of the geometry in main.tex
DARK = 140


def _px(mm, dpi):
    return mm * dpi / 25.4


def _dark(im):
    return im.convert("L").point(lambda v: 255 if v < DARK else 0)


def has_border(im, dpi=100):
    """True if a long dark line runs along the left and right border positions."""
    b = _dark(im)
    w, h = b.size
    o = int(round(_px(BORDER_MM, dpi)))
    for x in range(o - 3, o + 5):
        col = [b.getpixel((x, y)) for y in range(int(h * 0.2), int(h * 0.8), 7)]
        if sum(1 for v in col if v) > 0.9 * len(col):
            break
    else:
        return False
    for x in range(w - o - 5, w - o + 3):
        col = [b.getpixel((x, y)) for y in range(int(h * 0.2), int(h * 0.8), 7)]
        if sum(1 for v in col if v) > 0.9 * len(col):
            return True
    return False


def border_collisions(im, dpi=100):
    """Rectangles (as strings) where dark pixels lie within 3 mm inside the border line or outside it."""
    b = _dark(im)
    w, h = b.size
    o = _px(BORDER_MM, dpi)
    lw = max(2, int(round(1.5 * dpi / 72.0)) + 1)          # line width in pixels, with a little slack
    inner = int(o + lw + 1)
    zone = int(o + lw + _px(3, dpi))
    found = []
    px = b.load()

    def count(x0, x1, y0, y1):
        n = 0
        for y in range(max(0, y0), min(h, y1)):
            for x in range(max(0, x0), min(w, x1)):
                if px[x, y]:
                    n += 1
        return n

    ob, oe = int(o), int(o)
    # inside strips: left, right, top, bottom (ignoring the corners where the border line itself turns)
    strips = {
        "left": (inner, zone, oe + lw + 2, h - oe - lw - 2),
        "right": (w - zone, w - inner, oe + lw + 2, h - oe - lw - 2),
        "top": (oe + lw + 2, w - oe - lw - 2, inner, zone),
        "bottom": (oe + lw + 2, w - oe - lw - 2, h - zone, h - inner),
    }
    for name, (x0, x1, y0, y1) in strips.items():
        if count(x0, x1, y0, y1) > 2:
            found.append("text within 3 mm inside the %s border" % name)
    # outside the border
    outside = {
        "left": (0, int(o - lw - 1), 0, h), "right": (int(w - o + lw + 1), w, 0, h),
        "top": (0, w, 0, int(o - lw - 1)), "bottom": (0, w, int(h - o + lw + 1), h),
    }
    for name, (x0, x1, y0, y1) in outside.items():
        if count(x0, x1, y0, y1) > 2:
            found.append("marks outside the %s border" % name)
    return found


def largest_gap_fraction(im, dpi=100):
    """Longest run of empty rows between the first and last text row, relative to the text area height."""
    b = _dark(im)
    w, h = b.size
    o = int(_px(BORDER_MM + 6, dpi))
    left, right = int(_px(LEFT_RIGHT_MM - 3, dpi)), int(w - _px(LEFT_RIGHT_MM - 3, dpi))
    top, bottom = int(_px(24, dpi)), int(h - _px(27, dpi))
    px = b.load()
    rows = []
    for y in range(top, bottom):
        rows.append(any(px[x, y] for x in range(left, right, 2)))
    if not any(rows):
        return 0.0
    first = rows.index(True)
    last = len(rows) - 1 - rows[::-1].index(True)
    best = run = 0
    for r in rows[first:last + 1]:
        run = 0 if r else run + 1
        best = max(best, run)
    return best / float(len(rows))


def margin_overflow(im, dpi=100):
    """Dark pixels between the border and the text margin, beyond the page number / header band."""
    b = _dark(im)
    w, h = b.size
    px = b.load()
    o = _px(BORDER_MM, dpi)
    lw = max(2, int(round(1.5 * dpi / 72.0)) + 1)
    x0, x1 = int(o + lw + 1), int(_px(LEFT_RIGHT_MM - 4, dpi))
    y0, y1 = int(_px(25, dpi)), int(h - _px(20, dpi))
    n = sum(1 for y in range(y0, y1) for x in range(x0, x1) if px[x, y])
    n += sum(1 for y in range(y0, y1) for x in range(int(w - _px(LEFT_RIGHT_MM - 4, dpi)), int(w - o - lw - 1)) if px[x, y])
    return n


def render(pdf, dpi):
    d = tempfile.mkdtemp()
    exe = shutil.which("pdftoppm") or r"D:\Program Files (x86)\MikiTek\miktex\bin\x64\pdftoppm.exe"
    subprocess.run([exe, "-r", str(dpi), "-png", pdf, os.path.join(d, "p")], check=True)
    return d, sorted(f for f in os.listdir(d) if f.endswith(".png"))


def main(argv):
    pdf = next((a for a in argv if a.endswith(".pdf")), os.path.join(HERE, "output", "main.pdf"))
    dpi = int(argv[argv.index("--dpi") + 1]) if "--dpi" in argv else 100
    max_gap = float(argv[argv.index("--max-gap") + 1]) if "--max-gap" in argv else 0.30
    skip = int(argv[argv.index("--skip-front") + 1]) if "--skip-front" in argv else 0   # front matter pages: no gap check
    d, files = render(pdf, dpi)
    bad = []
    try:
        for i, f in enumerate(files, 1):
            im = Image.open(os.path.join(d, f))
            msgs = []
            if not has_border(im, dpi):
                msgs.append("border missing")
            msgs += border_collisions(im, dpi)
            g = largest_gap_fraction(im, dpi)
            if g > max_gap and i > skip:
                msgs.append("hole of %.0f%% of the text area in the middle of the page" % (100 * g))
            if margin_overflow(im, dpi) > 6:
                msgs.append("marks between the border and the left/right margin")
            if msgs:
                bad.append((i, msgs))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    for i, msgs in bad:
        print("page %d: %s" % (i, "; ".join(msgs)))
    print("%d pages checked, %d with problems" % (len(files), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
