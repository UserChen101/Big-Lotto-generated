"""水印内核几何自测：角度朝向、居中精度、旋转页补偿、平铺覆盖。

跑法：python scripts/test_watermark.py
产物：build/wm_angles.png / build/wm_tile.png / build/wm_rotated.png
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402

from app.core import edit as cedit  # noqa: E402
from app.core import watermark as wm  # noqa: E402

OUT = ROOT / "build"
OUT.mkdir(parents=True, exist_ok=True)
FONT = cedit._pick("msyh")


def make_page(doc: fitz.Document, probe: bool = True) -> fitz.Page:
    page = doc.new_page(width=595, height=842)
    if probe:
        # 画点格子，方便肉眼判断朝向和居中
        page.draw_rect(fitz.Rect(28, 28, 567, 814), color=(0.82, 0.85, 0.9), width=1)
        page.draw_line(fitz.Point(297.5, 28), fitz.Point(297.5, 814), color=(0.9, 0.92, 0.96))
        page.draw_line(fitz.Point(28, 421), fitz.Point(567, 421), color=(0.9, 0.92, 0.96))
        page.insert_text(fitz.Point(40, 60), "PageSmith — watermark geometry probe", fontsize=11)
        page.insert_text(fitz.Point(40, 800), "bottom-left marker", fontsize=9, color=(0.6, 0.6, 0.6))
    return page


def contact_sheet(pages: list[fitz.Page], cols: int, path: Path, zoom: float = 0.5) -> None:
    tiles = []
    for page in pages:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        tiles.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    if not tiles:
        return
    w, h = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols + 1) * 8, rows * h + (rows + 1) * 8), (238, 240, 245))
    for i, img in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet.paste(img, (8 + c * (w + 8), 8 + r * (h + 8)))
    sheet.save(path)
    print(f"→ {path}  {sheet.size[0]}x{sheet.size[1]}")


def ink_center(page: fitz.Page) -> tuple[float, float]:
    """渲染整页并返回深色墨迹的中心（页面比例 0..1，原点左上）。"""
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    px = img.load()
    xs, ys = [], []
    for y in range(img.height):
        for x in range(img.width):
            if px[x, y] < 120:
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0.0, 0.0)
    return ((min(xs) + max(xs)) / 2 / img.width, (min(ys) + max(ys)) / 2 / img.height)


def main() -> int:
    problems: list[str] = []

    # ── 1. 单个居中：空白页（墨迹只有水印本身），量中心偏差
    doc = fitz.open()
    for a in (0, 45, 90, 135, 180, 270, 315):
        page = make_page(doc, probe=False)
        wm._draw_page(page, wm.Options(text="内部资料 INTERNAL", size=44, angle=a, opacity=1.0, color=(0.1, 0.1, 0.1)), FONT)
        cx, cy = ink_center(page)
        dx, dy = cx - 0.5, cy - 0.5
        print(f"angle {a:>3}: 墨迹中心=({cx:.3f},{cy:.3f})  偏差=({dx:+.3f},{dy:+.3f})")
        if abs(dx) > 0.012 or abs(dy) > 0.012:
            problems.append(f"角度 {a}° 未居中：偏差 ({dx:+.3f}, {dy:+.3f})")
    contact_sheet(list(doc), 4, OUT / "wm_angles.png")

    # ── 2. 多行 + 平铺（视觉检查：格子铺满、无漏铺、方向 45°）
    doc2 = fitz.open()
    p = make_page(doc2)
    wm._draw_page(
        p,
        wm.Options(text="机密\nCONFIDENTIAL", size=26, angle=45, opacity=0.5, tile=True),
        FONT,
    )
    contact_sheet([p], 1, OUT / "wm_tile.png", zoom=0.7)

    # ── 3. /Rotate 90 页面：0° 在显示页上必须水平、字头朝上
    doc3 = fitz.open()
    for a in (0, 45, 90):
        page = make_page(doc3, probe=False)
        page.set_rotation(90)
        wm._draw_page(page, wm.Options(text="AB 水印 123", size=40, angle=a, opacity=1.0, color=(0.1, 0.1, 0.1)), FONT)
        cx, cy = ink_center(page)
        if abs(cx - 0.5) > 0.02 or abs(cy - 0.5) > 0.02:
            problems.append(f"旋转页角度 {a}° 未居中：({cx:.3f},{cy:.3f})")
    contact_sheet(list(doc3), 3, OUT / "wm_rotated.png", zoom=0.5)

    print()
    if problems:
        for msg in problems:
            print(f"✗ {msg}")
        return 1
    print("✓ 水印几何全部通过（角度图 / 平铺图 / 旋转页图请目视复核 build/*.png）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
