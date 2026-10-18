"""Builds the Legion marks: logos, icons, covers and colour tokens, in the Vapulab identity.

Usage (from the repository root):  python brand/build.py
Needs fonttools, brotli and uharfbuzz (pip) and Chrome or Edge for the PNGs (CHROME_PATH overrides the path).
Everything in brand/ is output of this script, except README.md, fonts/ and the script itself.

The system is Vapulab's Goetia layer: a seal drawn in line inside a ring, one colour per daemon, the name in Martian
Mono and the office below it in capitals. What runs inside is line; Legion itself is the summoning circle that holds
the three seats, one per daemon.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "fonts"
TMP = Path(tempfile.mkdtemp(prefix="legion-brand-"))

# The Vapulab neutrals.
INK = "#0A0F1A"      # dark surface
CARBON = "#090D14"   # text on light
BONE = "#FAFAF9"     # light surface
FOG = "#EDF0F4"      # text on dark
SLATE = "#94A3B8"    # secondary text on dark
SLATE_D = "#64748B"  # secondary text on light
LINE = "#1E293B"     # borders on dark

# dark: the colour on INK. light: the colour on BONE (deeper, for contrast). Both pass WCAG AA (4.5:1).
AGENTS = [
    dict(slug="marbas", name="marbas", dark="#2DD4BF", light="#0F766E", role="Healing",
         office="He cures diseases and teaches the mechanical arts.",
         line="Turns a bug thread into a reviewed PR, then proves the fix in production.", status="Bug thread to PR"),
    dict(slug="valac", name="valac", dark="#FDE047", light="#A16207", role="Discovery",
         office="He reveals hidden treasures and where the serpents lie.",
         line="Patrols the production logs and opens one issue for each new defect.", status="Log patrol"),
    dict(slug="orobas", name="orobas", dark="#F472B6", light="#BE185D", role="Verdict",
         office="He answers truly of things past, present and to come.",
         line="Reviews every PR through a ladder of gates and lanes, and never deceives.", status="PR review"),
]
LEGION = dict(slug="legion", name="legion", dark="#EDF0F4", light="#090D14", role="Autonomous engineering agents",
              line="Three daemons of the Goetia that fix, patrol and review your code.", status="Apache-2.0")

SIGILS = {
    # Healing: the fracture stitched shut, and the node where the cure took hold.
    "marbas": ('<path d="M14,44L25,33L33,41L42,32"/><path d="M16,35L23,42"/><path d="M33,30L40,37"/>',
               '<rect x="41" y="18" width="10" height="10"/>'),
    # Discovery: the serpent in the grass, and the ring that finds its head.
    "valac": ('<path d="M13,42C18,32 24,31 28,37S37,44 40.5,29.5"/><circle cx="45" cy="24" r="6.5"/>',
              '<circle cx="45" cy="24" r="2.5"/>'),
    # Verdict: the brackets of a review, and the check that passes only when it is true.
    "orobas": ('<path d="M22,17H15V47H22"/><path d="M42,17H49V47H42"/><path d="M23,32L29,38L41,26"/>',
               '<rect x="28.5" y="44" width="7" height="7"/>'),
}


def seal(sigil, c):
    """The Goetia seal: a sigil in line inside a ring."""
    return (f'<circle cx="32" cy="32" r="29" fill="none" stroke="{c}" stroke-width="2"/>'
            f'<g fill="none" stroke="{c}" stroke-width="3.5" stroke-linecap="square" '
            f'stroke-linejoin="miter">{sigil[0]}</g><g fill="{c}">{sigil[1]}</g>')


# The three seats of the circle, at the corners of a triangle: Marbas, Valac and Orobas, clockwise from the top.
SEATS = ((32, 15), (46.7, 40.5), (17.3, 40.5))


def legion_body(theme):
    """Legion: the summoning circle, its triangle, and a seat in each daemon's colour."""
    fg = FOG if theme == "dark" else CARBON
    tri = "M32,15L46.7,40.5L17.3,40.5Z"
    seats = "".join(f'<rect x="{x - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" fill="{a[theme]}"/>'
                    for (x, y), a in zip(SEATS, AGENTS))
    return (f'<circle cx="32" cy="32" r="29" fill="none" stroke="{fg}" stroke-width="2"/>'
            f'<path d="{tri}" fill="none" stroke="{fg}" stroke-width="3.5" stroke-linejoin="miter"/>{seats}')


def mark_body(e, theme):
    return legion_body(theme) if e["slug"] == "legion" else seal(SIGILS[e["slug"]], e[theme])


def mark_svg(e, theme):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{mark_body(e, theme)}</svg>\n'


def favicon_svg(e):
    """One mark that follows the system theme."""
    light, dark = mark_body(e, "light"), mark_body(e, "dark")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<style>.d{display:none}@media (prefers-color-scheme:dark){.l{display:none}.d{display:inline}}</style>'
            f'<g class="l">{light}</g><g class="d">{dark}</g></svg>\n')


# ---------------------------------------------------------------- text as curves


def ttf_of(woff2):
    out = TMP / (woff2.stem + ".ttf")
    if not out.exists():
        font = TTFont(woff2)
        font.flavor = None
        font.save(out)
    return out


class Face:
    def __init__(self, woff2, **axes):
        self.face = hb.Face(hb.Blob.from_file_path(str(ttf_of(woff2))))
        self.font = hb.Font(self.face)
        self.font.set_variations(axes)
        self.upem = self.face.upem

    def path(self, text, size, x, y, tracking=0.0):
        """(d, width). tracking in em."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {"kern": True, "liga": True})
        s = size / self.upem
        pen = SVGPathPen(None, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        cx = 0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            t = TransformPen(pen, (s, 0, 0, -s, x + (cx + pos.x_offset) * s, y - pos.y_offset * s))
            self.font.draw_glyph_with_pen(info.codepoint, t)
            cx += pos.x_advance + tracking * self.upem
        return pen.getCommands(), (cx - tracking * self.upem) * s


GROTESK = FONTS / "SpaceGrotesk.woff2"
MONO = FONTS / "MartianMono.woff2"


def lockup_svg(e, theme):
    """The mark and the name in curves, so no installed font is needed."""
    fg = FOG if theme == "dark" else CARBON
    parts = []
    if e["slug"] == "legion":
        d, w1 = Face(GROTESK, wght=600).path("legion", 44, 80, 45, tracking=-0.025)
        parts.append(f'<path d="{d}" fill="{fg}"/>')
        width = 80 + w1 + 4
    else:
        d, w1 = Face(MONO, wght=500, wdth=100).path(e["name"], 30, 84, 38, tracking=-0.02)
        d2, w2 = Face(MONO, wght=500, wdth=100).path(e["role"].upper(), 9.5, 85, 55, tracking=0.28)
        parts += [f'<path d="{d}" fill="{fg}"/>', f'<path d="{d2}" fill="{e[theme]}"/>']
        width = 84 + max(w1, w2) + 6
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} 64">'
            f'<g>{mark_body(e, theme)}</g>{"".join(parts)}</svg>\n')


# ---------------------------------------------------------------- PNGs


def chrome():
    for p in [os.environ.get("CHROME_PATH"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              shutil.which("google-chrome"), shutil.which("chromium")]:
        if p and Path(p).exists():
            return p
    raise SystemExit("Chrome not found: set CHROME_PATH")


def shoot(html_path, png_path, w, h):
    subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1", f"--window-size={w},{h}",
                    f"--screenshot={png_path}", html_path.as_uri()],
                   check=True, capture_output=True)


FONT_FACES = f"""
@font-face{{font-family:"Space Grotesk";src:url("{GROTESK.as_uri()}") format("woff2");font-weight:300 700}}
@font-face{{font-family:"Martian Mono";src:url("{MONO.as_uri()}") format("woff2");font-weight:100 800;font-stretch:75% 112.5%}}
"""

BASE_CSS = f"""{FONT_FACES}
*{{box-sizing:border-box}}
html,body{{margin:0;overflow:hidden;background:{INK};color:{FOG};font-family:"Space Grotesk",system-ui,sans-serif}}
.grid{{position:absolute;inset:0;background-image:linear-gradient({LINE}88 1px,transparent 1px),
  linear-gradient(90deg,{LINE}88 1px,transparent 1px);background-size:48px 48px;background-position:-1px -1px}}
.eyebrow{{font-family:"Martian Mono",monospace;font-size:15px;letter-spacing:.22em;text-transform:uppercase;color:{SLATE}}}
"""


def icon_html(e):
    return f"""<!doctype html><html><head><style>
html,body{{margin:0;width:512px;height:512px;background:{INK}}}
svg{{position:absolute;inset:18%;width:64%;height:64%}}
</style></head><body><svg viewBox="0 0 64 64">{mark_body(e, "dark")}</svg></body></html>"""


def cover_html(e):
    c = e["dark"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
html,body{{width:1200px;height:630px}}
.glow{{position:absolute;right:-160px;top:-120px;width:820px;height:820px;
  background:radial-gradient(closest-side,{c}2e,transparent 72%)}}
.mark{{position:absolute;right:72px;top:50%;transform:translateY(-50%);width:430px}}
.mark svg{{width:100%;height:auto;display:block}}
.copy{{position:absolute;left:72px;top:72px;bottom:72px;width:600px;display:flex;flex-direction:column}}
.eyebrow b{{color:{c};font-weight:500}}
h1{{margin:28px 0 0;line-height:.95;font-family:"Martian Mono",monospace;font-weight:500;font-stretch:100%;
  font-size:104px;letter-spacing:-.04em}}
.role{{margin-top:22px;font-size:30px;font-weight:500;color:{c}}}
.office{{margin-top:10px;font-size:20px;font-style:italic;color:{SLATE}}}
.line{{margin-top:14px;font-size:24px;line-height:1.35;color:{FOG};max-width:580px}}
.foot{{margin-top:auto;display:flex;align-items:center;gap:18px;font-family:"Martian Mono",monospace;
  font-size:14px;letter-spacing:.12em;text-transform:uppercase;color:{SLATE}}}
.chip{{border:1px solid {c}88;color:{c};padding:8px 14px;border-radius:2px}}
</style></head><body><div class="grid"></div><div class="glow"></div>
<div class="mark"><svg viewBox="0 0 64 64">{mark_body(e, "dark")}</svg></div>
<div class="copy"><div class="eyebrow"><b>■</b>&nbsp; Goetia · Legion</div><h1>{e["name"]}</h1>
<div class="role">{e["role"]}</div><div class="office">{e["office"]}</div><div class="line">{e["line"]}</div>
<div class="foot"><span class="chip">{e["status"]}</span><span>A Vapulab project</span></div></div>
</body></html>"""


def legion_cover_html():
    seats = "".join(
        f'<div class="seat"><svg viewBox="0 0 64 64">{mark_body(a, "dark")}</svg>'
        f'<div class="n">{a["name"]}</div><div class="r" style="color:{a["dark"]}">{a["role"]}</div></div>'
        for a in AGENTS)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}
html,body{{width:1280px;height:640px}}
.wrap{{position:absolute;inset:72px 80px;display:grid;grid-template-columns:1fr 560px;gap:56px;align-items:center}}
.hero svg{{width:120px;display:block}}
h1{{font-size:112px;font-weight:600;letter-spacing:-.03em;margin:28px 0 0;line-height:1}}
.line{{margin-top:22px;font-size:26px;line-height:1.35;color:{FOG};max-width:520px}}
.hero .eyebrow{{margin-top:26px}}
.seats{{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;border-left:1px solid {LINE};padding-left:48px}}
.seat svg{{width:120px;display:block}}
.n{{margin-top:18px;font-family:"Martian Mono",monospace;font-weight:500;font-size:26px;letter-spacing:-.02em}}
.r{{margin-top:6px;font-family:"Martian Mono",monospace;font-size:12px;letter-spacing:.16em;text-transform:uppercase}}
</style></head><body><div class="grid"></div><div class="wrap">
<div class="hero"><svg viewBox="0 0 64 64">{legion_body("dark")}</svg><h1>legion</h1>
<div class="line">{LEGION["line"]}</div><div class="eyebrow">Apache-2.0 · A Vapulab project</div></div>
<div class="seats">{seats}</div></div></body></html>"""


# ---------------------------------------------------------------- contrast and tokens


def lum(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda v: v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main():
    report = []
    for e in AGENTS + [LEGION]:
        s = e["slug"]
        for theme in ("dark", "light"):
            write(ROOT / "logos" / s / f"mark-{theme}.svg", mark_svg(e, theme))
            write(ROOT / "logos" / s / f"lockup-{theme}.svg", lockup_svg(e, theme))
        write(ROOT / "icons" / f"{s}.svg", favicon_svg(e))
        page = TMP / f"icon-{s}.html"
        write(page, icon_html(e))
        (ROOT / "icons").mkdir(exist_ok=True)
        shoot(page, ROOT / "icons" / f"{s}-512.png", 512, 512)
        if s != "legion":
            page = TMP / f"cover-{s}.html"
            write(page, cover_html(e))
            (ROOT / "covers").mkdir(exist_ok=True)
            shoot(page, ROOT / "covers" / f"{s}.png", 1200, 630)
            report.append((s, contrast(e["dark"], INK), contrast(e["light"], BONE)))
    page = TMP / "cover-legion.html"
    write(page, legion_cover_html())
    shoot(page, ROOT / "covers" / "legion.png", 1280, 640)

    write(ROOT / "tokens.json", json.dumps({
        "neutral": {"ink": INK, "carbon": CARBON, "bone": BONE, "fog": FOG, "slate": SLATE, "slateDeep": SLATE_D,
                    "line": LINE},
        "agents": {a["slug"]: {"dark": a["dark"], "light": a["light"], "role": a["role"]} for a in AGENTS},
        "fonts": {"display": "Space Grotesk", "mono": "Martian Mono", "body": "system-ui"},
    }, indent=2) + "\n")

    print("contrast      on INK      on BONE")
    bad = False
    for s, d, l in report:
        flag = "" if d >= 4.5 and l >= 4.5 else "  < 4.5"
        bad |= bool(flag)
        print(f"{s:<10} {d:>8.2f}:1 {l:>9.2f}:1{flag}")
    shutil.rmtree(TMP, ignore_errors=True)
    if bad:
        raise SystemExit("an agent colour is below AA (4.5:1)")


if __name__ == "__main__":
    main()
