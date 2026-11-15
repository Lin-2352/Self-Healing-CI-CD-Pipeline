"""Join screenshots and captions into the demo video (docs/media/demo.mp4). No audio.

    python tools/media/build_demo_video.py docs/media/demo_spec.json

The spec is a JSON list. Each entry is either a title card or a screenshot with a caption:

    {"title": "Self-healing CI/CD", "sub": "one failing push, one fix PR", "seconds": 4}
    {"image": "docs/media/shots/01_break_commit.png", "caption": "1. A commit removes a line", "seconds": 6}

An entry may also carry "crop": [x0, y0, x1, y1] to show only part of the screenshot.

Every frame is drawn at 1920x1080 with Pillow (screenshot scaled to fit, caption in a bar at the bottom),
written to a temporary folder, and ffmpeg joins them with the given durations. Paths in the spec are
relative to the repository root.
"""
import io, json, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W, H, BAR = 1920, 1080, 150
FONT = r"C:\Windows\Fonts\calibri.ttf"
FONT_B = r"C:\Windows\Fonts\calibrib.ttf"


def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def title_card(e):
    im = Image.new("RGB", (W, H), (18, 24, 38))
    d = ImageDraw.Draw(im)
    big, small = ImageFont.truetype(FONT_B, 84), ImageFont.truetype(FONT, 44)
    y = 380
    for ln in wrap(d, e["title"], big, W - 300):
        d.text(((W - d.textlength(ln, font=big)) / 2, y), ln, font=big, fill=(240, 244, 250)); y += 100
    if e.get("sub"):
        y += 20
        for ln in wrap(d, e["sub"], small, W - 400):
            d.text(((W - d.textlength(ln, font=small)) / 2, y), ln, font=small, fill=(160, 175, 200)); y += 58
    return im


def shot_card(e):
    im = Image.new("RGB", (W, H), (18, 24, 38))
    src = Image.open(os.path.join(ROOT, e["image"])).convert("RGB")
    if e.get("crop"):                                   # [x0, y0, x1, y1] in source pixels
        src = src.crop(tuple(e["crop"]))
    area_h = H - BAR - 40
    s = min((W - 80) / src.width, area_h / src.height)
    src = src.resize((max(1, int(src.width * s)), max(1, int(src.height * s))), Image.LANCZOS)
    im.paste(src, ((W - src.width) // 2, 20 + (area_h - src.height) // 2))
    d = ImageDraw.Draw(im)
    d.rectangle([0, H - BAR, W, H], fill=(10, 14, 24))
    font = ImageFont.truetype(FONT, 46)
    lines = wrap(d, e["caption"], font, W - 160)[:2]
    y = H - BAR + (BAR - 56 * len(lines)) // 2
    for ln in lines:
        d.text((80, y), ln, font=font, fill=(240, 244, 250)); y += 56
    return im


def main():
    spec_path = os.path.join(ROOT, sys.argv[1]) if not os.path.isabs(sys.argv[1]) else sys.argv[1]
    spec = json.load(io.open(spec_path, encoding="utf-8"))
    out = os.path.join(os.path.dirname(spec_path), "demo.mp4")
    tmp = tempfile.mkdtemp()
    listing = []
    for i, e in enumerate(spec):
        p = os.path.join(tmp, "f%03d.png" % i)
        (title_card(e) if "title" in e else shot_card(e)).save(p)
        listing.append("file '%s'\nduration %s" % (p.replace("\\", "/"), e.get("seconds", 5)))
    listing.append("file '%s'" % p.replace("\\", "/"))              # concat demuxer needs the last file repeated
    lst = os.path.join(tmp, "list.txt")
    io.open(lst, "w", encoding="utf-8").write("\n".join(listing))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
           "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    total = sum(float(e.get("seconds", 5)) for e in spec)
    print("wrote %s (%d frames, %.0f s)" % (os.path.relpath(out, ROOT), len(spec), total))


if __name__ == "__main__":
    main()
