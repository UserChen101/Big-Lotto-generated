"""生成应用图标（assets/icon.ico + assets/icon.png + app/web/favicon.ico）。

设计：蓝色圆角方块 + 白色纸张（右上折角）+ 红色 PDF 色带。
用 4 倍超采样绘制再缩小，保证 16px 下边缘依然干净。

    python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets"
WEB_DIR = ROOT / "app" / "web"

S = 4                 # 超采样倍数
BASE = 1024
N = BASE * S

BLUE_TOP = (59, 130, 246)
BLUE_BOTTOM = (29, 64, 175)
PAPER = (255, 255, 255)
PAPER_EDGE = (219, 226, 236)
FOLD = (203, 213, 225)
LINE = (203, 213, 225)
BAND = (220, 38, 38)

FONT_BOLD = Path(r"C:\Windows\Fonts\segoeuib.ttf")
FONT_FALLBACK = Path(r"C:\Windows\Fonts\arialbd.ttf")


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad.resize((size, size), Image.BILINEAR)


def build() -> Image.Image:
    # 圆角方块底
    bg = vertical_gradient(N, BLUE_TOP, BLUE_BOTTOM).convert("RGBA")
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, N - 1, N - 1], radius=int(N * 0.225), fill=255)
    canvas = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    canvas.paste(bg, (0, 0), mask)

    d = ImageDraw.Draw(canvas)

    # 纸张：右上角留出折角
    pw, ph = int(N * 0.46), int(N * 0.58)
    x0 = (N - pw) // 2
    y0 = int(N * 0.20)
    r = int(N * 0.035)
    fold = int(pw * 0.26)

    paper = [
        (x0, y0),
        (x0 + pw - fold, y0),
        (x0 + pw, y0 + fold),
        (x0 + pw, y0 + ph),
        (x0, y0 + ph),
    ]
    d.polygon(paper, fill=PAPER)
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=r, fill=PAPER)

    # 折角三角形（浅灰），并补一条折线
    d.polygon(
        [(x0 + pw - fold, y0), (x0 + pw - fold, y0 + fold), (x0 + pw, y0 + fold)],
        fill=FOLD,
    )
    d.line([(x0 + pw - fold, y0), (x0 + pw - fold, y0 + fold), (x0 + pw, y0 + fold)],
           fill=PAPER_EDGE, width=max(1, S * 2))

    # 正文短线
    lx0 = x0 + int(pw * 0.16)
    lx1 = x0 + pw - int(pw * 0.16)
    for i in range(3):
        ly = y0 + int(ph * 0.30) + i * int(ph * 0.105)
        w = lx1 if i < 2 else x0 + int(pw * 0.62)
        d.rounded_rectangle([lx0, ly, w, ly + int(N * 0.017)], radius=int(N * 0.009), fill=LINE)

    # 底部红色 PDF 色带
    by0 = y0 + int(ph * 0.66)
    by1 = y0 + ph - int(ph * 0.075)
    bx0 = x0 + int(pw * 0.10)
    bx1 = x0 + pw - int(pw * 0.10)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=int((by1 - by0) * 0.22), fill=BAND)

    font_path = FONT_BOLD if FONT_BOLD.exists() else FONT_FALLBACK
    fs = int((by1 - by0) * 0.68)
    font = ImageFont.truetype(str(font_path), fs)
    text = "PDF"
    tb = d.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text(
        ((bx0 + bx1 - tw) / 2 - tb[0], (by0 + by1 - th) / 2 - tb[1]),
        text, font=font, fill=(255, 255, 255),
    )

    return canvas.resize((BASE, BASE), Image.LANCZOS)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = build()
    icon.save(OUT_DIR / "icon.png")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    icon.save(OUT_DIR / "icon.ico", sizes=sizes)
    # 网页 favicon（浏览器标签页用，避免 /favicon.ico 404）
    icon.resize((64, 64), Image.LANCZOS).save(WEB_DIR / "favicon.ico", sizes=[(64, 64), (32, 32), (16, 16)])
    print(f"生成完成: {OUT_DIR / 'icon.ico'} / icon.png / {WEB_DIR / 'favicon.ico'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
