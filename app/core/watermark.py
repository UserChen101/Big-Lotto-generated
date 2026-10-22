"""页面水印：文字、字号、任意角度（0–360°）、颜色、透明度、单个 / 平铺。

为什么值得单独一个模块
----------------------
水印听起来"随便写个字就行"，实际有三个坑：

1. **任意角度**。PyMuPDF 的 ``insert_text(rotate=)`` 只认 90 的整数倍，
   想画 37° 必须自己给仿射矩阵。矩阵一给，锚点就不能再按"文字起点"理解，
   而要反解成"旋转后包围盒正好居中"的那个点。
2. **旋转页面**。/Rotate 90 的页面，落笔坐标是"未旋转"的坐标系，而用户看到的
   是旋转后的页面。角度不补偿就会歪 90°，字号也会看着不对。
3. **平铺**。逐块 insert_text 每块都要嵌一次字体、写一次内容流；改成
   "先在未旋转坐标系里铺好格子、最后整体做一次旋转"，只写一次内容流。

水印写的是**真实文字**（不是图片），所以可选中、可搜索，文件体积几乎不变。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import pymupdf as fitz

from . import edit as cedit

# 行高与基线到字顶/字底的经验比例，与 edit.py 保持一致，视觉上才对得齐
LINE_FACTOR = 1.32
ASCENT = 0.86
DESCENT = 0.22

MAX_TEXT_CHARS = 400
MAX_TILES = 4000  # 防止极端参数（超大字号 + 极小间距）把页面写爆


@dataclass
class Options:
    """一次水印操作的全部参数（从界面 JSON 直接解析）。"""

    text: str
    size: float = 48.0
    angle: float = 45.0
    opacity: float = 0.18
    color: tuple[float, float, float] = (0.5, 0.5, 0.5)
    font: str | None = None
    tile: bool = False
    gap: float = 0.8

    @classmethod
    def parse(cls, data: dict) -> "Options":
        text = str(data.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            raise ValueError("水印文字不能为空")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]

        def num(key: str, default: float, lo: float, hi: float) -> float:
            try:
                v = float(data.get(key, default))
            except (TypeError, ValueError):
                v = default
            if math.isnan(v) or math.isinf(v):
                v = default
            return max(lo, min(hi, v))

        return cls(
            text=text,
            size=num("size", 48, 4, 400),
            angle=num("angle", 45, 0, 360) % 360,
            opacity=num("opacity", 0.18, 0.02, 1.0),
            color=cedit._to_rgb(data.get("color"), (0.5, 0.5, 0.5)),
            font=(data.get("font") or None),
            tile=bool(data.get("tile")),
            gap=num("gap", 0.8, 0.15, 4.0),
        )


# ---------------------------------------------------------------- 几何


def _rot(angle: float) -> fitz.Matrix:
    """逆时针旋转 angle 度的矩阵（TextWriter 内部是 y-up 坐标，视觉即逆时针）。"""
    r = math.radians(angle)
    c, s = math.cos(r), math.sin(r)
    return fitz.Matrix(c, s, -s, c, 0, 0)


def _metrics(font: fitz.Font, lines: list[str], size: float) -> tuple[float, float, float, float, float]:
    """返回 (宽, 高, 行高, 字顶高, 字底深)。"""
    lh = size * LINE_FACTOR
    asc = size * ASCENT
    desc = size * DESCENT
    width = max((font.text_length(line, fontsize=size) for line in lines), default=0.0)
    height = asc + desc + lh * (len(lines) - 1)
    return width, height, lh, asc, desc


def _unrot_rect(page: fitz.Page) -> fitz.Rect:
    """未旋转（落笔坐标系）的页面矩形。"""
    r = fitz.Rect(page.rect * page.derotation_matrix)
    return r.normalize()


def _tile_anchors(
    rect: fitz.Rect, mat: fitz.Matrix, step_x: float, step_y: float, block_w: float, block_h: float
) -> list[fitz.Point]:
    """在未旋转坐标系里铺格子，只保留可能落到页面上的那些。

    旋转矩形的支撑函数：把半宽半高分别投影到旋转后的两个坐标轴上，
    超出这个范围的格子不可能压到页面，直接丢掉（可省掉 2/3 的冗余对象）。
    """
    cx, cy = rect.x0 + rect.width / 2, rect.y0 + rect.height / 2
    hx, hy = rect.width / 2, rect.height / 2
    pu = hx * abs(mat.a) + hy * abs(mat.b) + block_w / 2 + step_x / 2
    pv = hx * abs(mat.c) + hy * abs(mat.d) + block_h / 2 + step_y / 2

    nx = int(pu // step_x) + 1
    ny = int(pv // step_y) + 1
    nx = min(nx, 60)
    ny = min(ny, 60)

    pts: list[fitz.Point] = []
    for i in range(-nx, nx + 1):
        if abs(i * step_x) > pu:
            continue
        for j in range(-ny, ny + 1):
            if abs(j * step_y) > pv:
                continue
            pts.append(fitz.Point(cx + i * step_x, cy + j * step_y))
            if len(pts) >= MAX_TILES:
                return pts
    return pts


def _draw_page(page: fitz.Page, opts: Options, font_choice: cedit.FontChoice) -> int:
    """把水印画到一页上，返回绘制的水印块数。"""
    font = cedit._load_font(font_choice)
    lines = opts.text.split("\n")
    width, height, lh, asc, desc = _metrics(font, lines, opts.size)

    rect = _unrot_rect(page)
    cx, cy = rect.x0 + rect.width / 2, rect.y0 + rect.height / 2

    # 角度按"用户看到的页面"给，补偿 /Rotate 之后才是未旋转坐标系里的角度
    theta = (opts.angle + page.rotation) % 360
    mat = _rot(theta)         # 传给 morph（TextWriter 内部 y-up 空间）
    conj = _rot(-theta)       # 同一旋转在页面坐标（y-down）里的等价形式

    if opts.tile:
        first = fitz.Point(cx, cy)
        step_x = max(width + opts.size * opts.gap, opts.size * 1.2)
        step_y = max(height + opts.size * opts.gap * 0.75, opts.size * 1.4)
        anchors = _tile_anchors(rect, conj, step_x, step_y, width, height)
    else:
        # 让旋转后的包围盒正好居中：morph 的锚点反解要用"页面坐标系里的
        # 等价旋转"（-θ），因为 TextWriter 在内部 y-up 空间里套矩阵，
        # 页面偏移会先被 y 翻转一次。
        local = fitz.Point(width / 2, (-asc + (lh * (len(lines) - 1) + desc)) / 2) * conj
        first = fitz.Point(cx - local.x, cy - local.y)
        anchors = [first]

    # TextWriter 要求 rect 与 page.rect 完全一致（内部只用它做兼容性校验），
    # 文字本身画到哪里不受这个 rect 限制，平铺超出页面的块会被页面边界自然裁掉。
    tw = fitz.TextWriter(page.rect, opacity=opts.opacity, color=opts.color)
    for anchor in anchors:
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            tw.append(fitz.Point(anchor.x, anchor.y + i * lh), line, font=font, fontsize=opts.size)

    if anchors:
        tw.write_text(page, morph=(first, mat))
    return len(anchors)


# ---------------------------------------------------------------- 对外


def apply(doc: fitz.Document, opts: Options, pages: Iterable[int] | None = None) -> dict:
    """给指定页（默认全部）加水印。"""
    targets = list(pages) if pages else list(range(doc.page_count))
    font_choice = cedit._pick(opts.font)
    done = 0
    blocks = 0
    for i in targets:
        if not 0 <= i < doc.page_count:
            continue
        blocks += _draw_page(doc[i], opts, font_choice)
        done += 1
    return {"pages": done, "blocks": blocks}


def preview_png(
    doc: fitz.Document, page_index: int, opts: Options, max_width: int = 460
) -> bytes:
    """渲染一页"加水印之后"的样子，返回 PNG 字节（不改动原文档）。

    做法是把这一页单独抽到临时文档里再加水印 —— 既保证了预览与最终结果
    走的是同一段代码，也不会把用户的文档弄脏。
    """
    if doc.page_count == 0:
        raise ValueError("文档没有页面")
    idx = max(0, min(int(page_index), doc.page_count - 1))
    tmp = fitz.open()
    try:
        tmp.insert_pdf(doc, from_page=idx, to_page=idx)
        page = tmp[0]
        _draw_page(page, opts, cedit._pick(opts.font))
        zoom = max_width / max(page.rect.width, 1.0)
        zoom = min(zoom, 3.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")
    finally:
        tmp.close()
