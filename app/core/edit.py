"""PDF 文本编辑：读取可编辑文本块、删除/替换文本、按方向插入新文本。

实现要点
--------
* **删除**走"修订（redaction）"而非画白块：只有修订能真正把字形从内容流里
  去掉，否则复制的文字里还留着原文。修订时保留图像与矢量图形，避免误删背景。
* **插入**用 TextWriter，方向靠 transform 实现：
    - 正向 h：正常书写
    - 反向 r：绕锚点旋转 180°（相当于倒着印）
    - 竖向 v：逐字堆叠，字形保持直立（中文竖排习惯）
* 字体优先用系统字体并嵌入子集；系统字体缺失时退回内置 CJK 字体，
  宁可字形朴素，也不能出现方块乱码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf as fitz

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# (id, 显示名, 文件名)。按此顺序出现在界面下拉框里。
_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("msyh", "微软雅黑", "msyh.ttc"),
    ("simsun", "宋体", "simsun.ttc"),
    ("simhei", "黑体", "simhei.ttf"),
    ("simkai", "楷体", "simkai.ttf"),
    ("simfang", "仿宋", "simfang.ttf"),
    ("deng", "等线", "Deng.ttf"),
    ("arial", "Arial", "arial.ttf"),
    ("times", "Times New Roman", "times.ttf"),
    ("cour", "Courier New", "cour.ttf"),
)

BUILTIN_CJK = "china-ss"      # MuPDF 内置 Droid Sans Fallback，作为中文兜底
BUILTIN_LATIN = "helv"


@dataclass(frozen=True)
class FontChoice:
    fid: str
    label: str
    path: str | None = None          # 系统字体文件
    builtin: str | None = None       # 内置字体名

    @property
    def alias(self) -> str:
        """写进 PDF 的字体别名，同一字体重复使用时靠它对上号。"""
        return f"PS_{self.fid}"


_font_cache: dict[str, FontChoice] = {}
_available: list[FontChoice] | None = None
# 首次字体发现会逐个加载系统字体（fitz.Font 是 C 层操作且不持 GIL），
# 水印预览线程和 UI 请求线程同时进来会撞车，必须串行化
_font_lock = __import__("threading").Lock()


def _resolve() -> list[FontChoice]:
    global _available
    if _available is not None:
        return _available
    with _font_lock:
        if _available is not None:      # 双重检查：等锁期间可能已被别的线程填好
            return _available
        out: list[FontChoice] = []
        for fid, label, fname in _CANDIDATES:
            path = FONT_DIR / fname
            if not path.exists():
                continue
            try:
                fitz.Font(fontfile=str(path))       # 能加载才算可用（TTC 也支持）
            except Exception:
                continue
            out.append(FontChoice(fid, label, path=str(path)))
        # 一个可用的都没有时，至少还能用内置字体写字
        if not out:
            out.append(FontChoice("builtin-cjk", "内置黑体", builtin=BUILTIN_CJK))
        _available = out
        return out


def available_fonts() -> list[dict]:
    return [{"id": f.fid, "label": f.label} for f in _resolve()]


def _pick(fid: str | None) -> FontChoice:
    fonts = _resolve()
    if fid and fid in _font_cache:
        return _font_cache[fid]
    for f in fonts:
        if f.fid == fid:
            _font_cache[fid] = f
            return f
    # 用户没选 / 选了不存在的：中文优先，其次第一个
    fallback = next((f for f in fonts if f.fid in ("msyh", "simsun", "simhei")), fonts[0])
    _font_cache[fid or ""] = fallback
    return fallback


def _load_font(choice: FontChoice) -> fitz.Font:
    # fitz.Font 的加载在 MuPDF C 层完成且不持 GIL，多线程同时加载同一个
    # 字体文件会互相卡死（水印预览线程 × UI 请求线程的真实事故），加锁串行
    with _font_lock:
        if choice.path:
            return fitz.Font(fontfile=choice.path)
        return fitz.Font(fontname=choice.builtin or BUILTIN_CJK)


# ---------------------------------------------------------------- 颜色


def _int_to_rgb(value: int) -> list[float]:
    return [((value >> 16) & 0xFF) / 255, ((value >> 8) & 0xFF) / 255, (value & 0xFF) / 255]


def _to_rgb(color: Any, default: tuple[float, float, float] = (0, 0, 0)) -> tuple[float, float, float]:
    if color is None:
        return default
    if isinstance(color, str) and color.startswith("#"):
        h = color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        vals = [float(c) for c in color[:3]]
        return tuple(min(1.0, max(0.0, c if c <= 1 else c / 255)) for c in vals)  # type: ignore[return-value]
    return default


# ---------------------------------------------------------------- 坐标换算
#
# 前端 pdf.js 的用户空间：原点左下、y 向上、未旋转页面。
# PyMuPDF 的页面空间：原点左上、y 向下、应用 /Rotate 之后。
# 两者只差一个 y 翻转 + 旋转矩阵。所有跨前后端的坐标都在这层转换，
# 保证页面被旋转过（90/180/270）之后编辑位置依然正确。


def _unrot_size(page: fitz.Page) -> tuple[float, float]:
    """未旋转页面的 (宽, 高)。"""
    r = page.rect
    return (r.height, r.width) if page.rotation in (90, 270) else (r.width, r.height)


def pdfjs_point(page: fitz.Page, x: float, y: float) -> fitz.Point:
    """前端坐标点 → PyMuPDF 页面坐标点。"""
    _, h = _unrot_size(page)
    return fitz.Point(float(x), h - float(y)) * page.rotation_matrix


def pdfjs_rect(page: fitz.Page, rect: Any) -> list[float]:
    """前端矩形 [x0,y0,x1,y1] → PyMuPDF 矩形。"""
    p0 = pdfjs_point(page, rect[0], rect[1])
    p1 = pdfjs_point(page, rect[2], rect[3])
    return [min(p0.x, p1.x), min(p0.y, p1.y), max(p0.x, p1.x), max(p0.y, p1.y)]


def frontend_rect(page: fitz.Page, rect: Any) -> list[float]:
    """PyMuPDF 矩形 → 前端矩形。"""
    _, h = _unrot_size(page)
    p0 = fitz.Point(float(rect[0]), float(rect[1])) * page.derotation_matrix
    p1 = fitz.Point(float(rect[2]), float(rect[3])) * page.derotation_matrix
    return [min(p0.x, p1.x), h - max(p0.y, p1.y), max(p0.x, p1.x), h - min(p0.y, p1.y)]


# ---------------------------------------------------------------- 读取


def text_spans(page: fitz.Page) -> list[dict]:
    """返回页面上所有可编辑文本片段（坐标已转成前端 pdf.js 空间）。"""
    data = page.get_text("dict")
    out: list[dict] = []
    for bi, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for li, line in enumerate(block.get("lines", [])):
            for si, span in enumerate(line.get("spans", [])):
                txt = span.get("text") or ""
                if not txt.strip():
                    continue
                bbox = frontend_rect(page, span["bbox"])
                out.append(
                    {
                        "id": f"{bi}-{li}-{si}",
                        "text": txt,
                        "bbox": [round(v, 2) for v in bbox],
                        "size": round(float(span["size"]), 2),
                        "font": span.get("font", ""),
                        "color": _int_to_rgb(int(span.get("color", 0))),
                    }
                )
    return out


# ---------------------------------------------------------------- 删除


def delete_rect(page: fitz.Page, rect: list[float] | tuple[float, ...]) -> None:
    """用修订方式删掉矩形内的文字，保留背景图像/矢量。"""
    r = fitz.Rect(rect)
    if r.is_empty or r.width <= 0 or r.height <= 0:
        raise ValueError("删除区域无效")
    page.add_redact_annot(r, fill=None)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )


# ---------------------------------------------------------------- 写入


def _line_height(size: float) -> float:
    return size * 1.32


def _write(
    page: fitz.Page,
    text: str,
    point: tuple[float, float],
    *,
    size: float,
    font: FontChoice,
    color: tuple[float, float, float],
    direction: str = "h",
) -> None:
    """按方向把文字写到页面上。

    point 统一表示"文本块的左上角"（竖向是第一个字的左上角），
    内部再换算成各方向需要的基线锚点。
    """
    f = _load_font(font)
    px, py = float(point[0]), float(point[1])
    size = max(1.0, float(size))
    asc = size * 0.86          # 基线到字顶的近似距离
    desc = size * 0.22         # 基线到字底的近似距离
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    if direction == "v":
        # 中文竖排：字形直立，逐字向下堆叠
        tw = fitz.TextWriter(page.rect, color=color)
        n = 0
        for line in lines:
            for ch in line:
                if ch.strip() == "":
                    n += 1
                    continue
                tw.append(fitz.Point(px + size * 0.08, py + asc + n * size * 1.02), ch, font=f, fontsize=size)
                n += 1
        if n:
            tw.write_text(page)
        return

    lh = _line_height(size)

    if direction == "r":
        # 反向：绕锚点旋转 180°，锚点取文本块右上角，旋转后正好落回左上角起点
        width = max((f.text_length(line, fontsize=size) for line in lines), default=0.0)
        pivot = fitz.Point(px + width, py + desc)
        tw = fitz.TextWriter(page.rect, color=color)
        for i, line in enumerate(lines):
            tw.append(fitz.Point(pivot.x, pivot.y + i * lh), line, font=f, fontsize=size)
        tw.write_text(page, morph=(pivot, fitz.Matrix(-1, 0, 0, -1, 0, 0)))
        return

    # 正向
    tw = fitz.TextWriter(page.rect, color=color)
    for i, line in enumerate(lines):
        tw.append(fitz.Point(px, py + asc + i * lh), line, font=f, fontsize=size)
    tw.write_text(page)


def measure(font: FontChoice, text: str, size: float) -> tuple[float, float]:
    """估算一段文字的宽高（用于把左上角锚点换算成基线锚点）。"""
    f = _load_font(font)
    lines = text.split("\n") or [""]
    width = max((f.text_length(line, fontsize=size) for line in lines), default=0.0)
    return width, _line_height(size) * len(lines)


def patch_space_tounicode(page: fitz.Page, font: FontChoice) -> None:
    """把嵌入字体 ToUnicode 里的空格映射修正为 U+0020。

    背景：MuPDF 自动生成的 ToUnicode 不含空格字形（Arial 的 gid 3），
    提取文本时空格会变成不间断空格 U+00A0，导致复制/搜索对不上。
    空格→U+0020 对任何字体都成立，补上没有副作用。
    """
    try:
        doc = page.parent
        f = _load_font(font)
        gid = f.has_glyph(0x20)
        if not gid:
            return
        entry = "<%04X> <0020>" % gid
        for info in doc.get_page_fonts(page.number, full=True):
            fxref, _ext, _typ, basefont, _resname = info[:5]
            if basefont != f.name:
                continue
            tu = doc.xref_get_key(fxref, "ToUnicode")
            if not tu or tu[0] != "xref":
                continue
            tuxref = int(str(tu[1]).split()[0])
            stream = doc.xref_stream(tuxref).decode("latin1")
            if entry in stream:
                continue
            block = "1 beginbfchar\n%s\nendbfchar\n" % entry
            if "endcmap" not in stream:
                continue
            doc.update_stream(tuxref, stream.replace("endcmap", block + "endcmap").encode("latin1"))
    except Exception:
        pass  # 修补失败只影响搜索体验，不值得让编辑失败
