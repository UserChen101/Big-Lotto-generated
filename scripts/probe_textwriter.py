"""实验 2：确定旋转页的角度补偿方向 + 验证锚点共轭反解后的居中。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402


def rot(angle: float, k: int = +1) -> fitz.Matrix:
    r = math.radians(angle)
    c, s = math.cos(r), math.sin(r)
    return fitz.Matrix(c, k * s, -k * s, c, 0, 0)


def draw(page, angle: float, comp: str, text="TEST 排版", size=40):
    """morph 矩阵用 +θ（y-up 空间），锚点反解用 -θ（页面坐标）。"""
    f = fitz.Font(fontfile=r"C:\Windows\Fonts\msyh.ttc")
    lines = text.split("\n")
    lh = size * 1.32
    asc, desc = size * 0.86, size * 0.22
    width = max(f.text_length(l, fontsize=size) for l in lines)
    height = asc + desc + lh * (len(lines) - 1)

    rect = fitz.Rect(page.rect * page.derotation_matrix).normalize()
    cx, cy = rect.x0 + rect.width / 2, rect.y0 + rect.height / 2

    off = {"plus": page.rotation, "minus": -page.rotation, "none": 0}[comp]
    theta = angle + off
    mat = rot(theta)
    conj = rot(-theta)

    yc = (-asc + (lh * (len(lines) - 1) + desc)) / 2
    local = fitz.Point(width / 2, yc) * conj
    pivot = fitz.Point(cx - local.x, cy - local.y)

    tw = fitz.TextWriter(page.rect)
    for i, line in enumerate(lines):
        tw.append(fitz.Point(pivot.x, pivot.y + i * lh), line, font=f, fontsize=size)
    tw.write_text(page, morph=(pivot, mat))


def ink_stats(page) -> dict:
    pix = page.get_pixmap(matrix=fitz.Matrix(0.55, 0.55), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    px = img.load()
    xs, ys = [], []
    for y in range(img.height):
        for x in range(img.width):
            if px[x, y] < 120:
                xs.append(x)
                ys.append(y)
    if not xs:
        return {"w": 0, "h": 0, "dir": 0, "cx": -1, "cy": -1}
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    lo, ro = [], []
    for x, y in zip(xs, ys):
        if x < x0 + (x1 - x0) / 3:
            lo.append(y)
        elif x > x1 - (x1 - x0) / 3:
            ro.append(y)
    lm = sum(lo) / len(lo) if lo else 0
    rm = sum(ro) / len(ro) if ro else 0
    return {
        "w": x1 - x0, "h": y1 - y0,
        "dir": round(lm - rm, 1),
        "cx": round((x0 + x1) / 2 / img.width, 3),
        "cy": round((y0 + y1) / 2 / img.height, 3),
    }


def main() -> None:
    print("=== 普通页：居中 + 方向 ===")
    for a in (0, 45, 90, 180, 270):
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        draw(page, a, "none")
        st = ink_stats(page)
        orient = "逆时针↑" if st["dir"] > 3 else ("顺时针↓" if st["dir"] < -3 else ("横" if st["w"] > st["h"] else "竖"))
        off = (st["cx"] - 0.5, st["cy"] - 0.5)
        print(f"{a:>3}°: bbox {st['w']}x{st['h']} dir={st['dir']:+.1f} {orient}  中心偏差=({off[0]:+.3f},{off[1]:+.3f})")

    print()
    print("=== /Rotate 90 页：补偿方向 ===")
    for comp in ("plus", "minus"):
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.set_rotation(90)
        draw(page, 0, comp)
        st0 = ink_stats(page)

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.set_rotation(90)
        draw(page, 45, comp)
        st45 = ink_stats(page)
        orient = "逆时针↑" if st45["dir"] > 3 else ("顺时针↓" if st45["dir"] < -3 else "?")
        print(
            f"comp={comp:5s}: 0° bbox {st0['w']}x{st0['h']} 中心=({st0['cx']},{st0['cy']})"
            f" | 45° dir={st45['dir']:+.1f} {orient} 中心=({st45['cx']},{st45['cy']})"
        )


if __name__ == "__main__":
    main()
