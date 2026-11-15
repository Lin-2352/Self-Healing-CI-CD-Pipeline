"""Tests for the page checks in check_layout.py, using synthetic page images (no LaTeX needed).

    python docs/report-kiit/tests/test_check_layout.py
"""
import os, sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import check_layout as C   # noqa: E402

W, H = 827, 1169            # A4 at 100 dpi
MM = 100 / 25.4
fails = 0


def check(label, cond):
    global fails
    fails += not cond
    print("  %-64s %s" % (label, "PASS" if cond else "FAIL"))


def page(text_boxes=(), border=True):
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    if border:
        o = int(7 * MM)
        d.rectangle([o, o, W - o, H - o], outline=0, width=int(1.5 * 100 / 72) + 1)
    for box in text_boxes:
        d.rectangle(box, fill=0)
    return im


print("border collision")
ok = page([(120, 120, 700, 140), (120, 900, 700, 920)])
check("text well inside the border is fine", C.border_collisions(ok) == [])
bad = page([(120, 120, 700, 140), (int(7 * MM) + 4, 400, 300, 420)])
check("text 4 px from the border line is reported", len(C.border_collisions(bad)) >= 1)
over = page([(int(7 * MM) - 6, 400, 300, 420)])
check("text crossing the border is reported", len(C.border_collisions(over)) >= 1)
check("a missing border is reported", C.has_border(page(border=False)) is False)
check("a present border is detected", C.has_border(ok) is True)

print("empty gap")
full = page([(120, y, 700, y + 14) for y in range(120, 1000, 30)])
check("a filled page has no big gap", C.largest_gap_fraction(full) < 0.2)
sparse = page([(120, 120, 700, 140), (120, 1000, 700, 1020)])
check("a page with a big hole is reported", C.largest_gap_fraction(sparse) > 0.6)

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
