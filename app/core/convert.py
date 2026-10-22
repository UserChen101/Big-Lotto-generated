"""格式转换：PDF ↔ Word / Excel / 图片 / 文本 / HTML。

每条转换路径都做了能力探测 —— 缺少可选依赖时返回清晰的中文提示，
而不是抛出一串 ImportError 堆栈。
"""

from __future__ import annotations

import csv
import html as html_lib
import os
import tempfile
from pathlib import Path
from typing import Any

import pymupdf as fitz  # PyMuPDF

from .doc import DocumentSession, _parse_pages

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class ConversionError(RuntimeError):
    pass


def _tmp_pdf(session: DocumentSession) -> str:
    """把当前会话的文档状态落到一个临时 PDF，供第三方库读取。"""
    if not session.dirty and session.path:
        return str(session.path)
    fh = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    fh.write(session.to_bytes(optimize=True))
    fh.close()
    return fh.name


# ------------------------------------------------------------------ PDF → Word


def pdf_to_docx(session: DocumentSession, out_path: str, pages: Any = None) -> str:
    try:
        from pdf2docx import Converter
    except ImportError as exc:  # pragma: no cover
        raise ConversionError(
            "缺少 pdf2docx 组件，无法转换为 Word。请先执行：pip install pdf2docx"
        ) from exc

    src = _tmp_pdf(session)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    targets = _parse_pages(pages, session.doc.page_count) if pages else None

    cv = Converter(src)
    try:
        if targets:
            for i in targets:
                cv.convert(out_path, start=i, end=i + 1, multi_processing=False)
                # 多页时把每页结果追加到一个文档里
            # pdf2docx 不支持逐页追加，退化为按首尾页整体转换
            if len(targets) > 1:
                cv.convert(out_path, start=targets[0], end=targets[-1] + 1)
        else:
            cv.convert(out_path)
    finally:
        cv.close()
    return out_path


# ------------------------------------------------------------------ PDF → 图片


def pdf_to_images(
    session: DocumentSession,
    out_dir: str,
    fmt: str = "png",
    dpi: int = 150,
    pages: Any = None,
    quality: int = 88,
) -> list[str]:
    doc = session.doc
    targets = _parse_pages(pages, doc.page_count) if pages else list(range(doc.page_count))
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpg":
        fmt = "jpeg"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = Path(session.path).stem if session.path else "page"
    out: list[str] = []
    for i in targets:
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        ext = "jpg" if fmt == "jpeg" else fmt
        target = Path(out_dir) / f"{stem}_{i + 1:03d}.{ext}"
        if fmt == "jpeg":
            pix.save(str(target), jpg_quality=int(quality))
        else:
            pix.save(str(target))
        out.append(str(target))
    return out


# ------------------------------------------------------------------ PDF → 文本 / HTML


def pdf_to_text(session: DocumentSession, out_path: str, pages: Any = None) -> str:
    doc = session.doc
    targets = _parse_pages(pages, doc.page_count) if pages else list(range(doc.page_count))
    chunks = []
    for i in targets:
        chunks.append(doc[i].get_text("text"))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n\n".join(chunks), encoding="utf-8")
    return out_path


def pdf_to_html(session: DocumentSession, out_path: str, pages: Any = None) -> str:
    doc = session.doc
    targets = _parse_pages(pages, doc.page_count) if pages else list(range(doc.page_count))
    title = html_lib.escape(Path(session.path).stem if session.path else "document")
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        f"<title>{title}</title>",
        "<style>body{max-width:820px;margin:40px auto;padding:0 24px;"
        "font-family:'Source Han Sans SC','Microsoft YaHei',system-ui,sans-serif;"
        "font-size:15px;line-height:1.85;color:#1f2328}"
        ".pg{border-bottom:1px dashed #d8dee4;margin:32px 0;padding-bottom:8px}"
        ".pg::before{content:attr(data-p);display:block;color:#8c959f;font-size:11px;"
        "letter-spacing:.08em;text-transform:uppercase;margin-bottom:12px}"
        "p{margin:0 0 .7em;white-space:pre-wrap}</style></head><body>",
    ]
    for i in targets:
        page = doc[i]
        parts.append(f'<section class="pg" data-p="Page {i + 1}">')
        blocks = page.get_text("blocks")
        for b in blocks:
            text = b[4].strip()
            if text:
                parts.append(f"<p>{html_lib.escape(text)}</p>")
        parts.append("</section>")
    parts.append("</body></html>")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(parts), encoding="utf-8")
    return out_path


# ------------------------------------------------------------------ PDF → Excel


def _clean_table(rows: list[list[str]]) -> list[list[str]]:
    """PyMuPDF 的表格识别常把单元格间距当成独立空列，这里剔掉全空的列。"""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    keep = [c for c in range(width) if any(padded[r][c].strip() for r in range(len(padded)))]
    if not keep:
        return []
    return [[padded[r][c] for c in keep] for r in range(len(padded))]


def pdf_tables(session: DocumentSession, pages: Any = None) -> list[dict]:
    """抽取 PDF 中的表格（返回结构化数据，不写文件）。"""
    doc = session.doc
    targets = _parse_pages(pages, doc.page_count) if pages else list(range(doc.page_count))
    result: list[dict] = []
    for i in targets:
        try:
            found = doc[i].find_tables()
        except Exception:
            continue
        for t_idx, table in enumerate(getattr(found, "tables", []) or []):
            data = table.extract()
            if not data or len(data) < 2:
                continue
            rows = [[("" if c is None else str(c).strip()) for c in row] for row in data]
            rows = _clean_table(rows)
            if not rows or len(rows) < 2:
                continue
            result.append({"page": i, "index": t_idx, "rows": rows})
    return result


def pdf_to_excel(session: DocumentSession, out_path: str, pages: Any = None) -> dict:
    tables = pdf_tables(session, pages)
    if not tables:
        raise ConversionError("未在当前文档中识别到表格结构。可先尝试「转 Word」再整理，或对扫描件先做 OCR。")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError as exc:  # pragma: no cover
        raise ConversionError("缺少 openpyxl，无法生成 Excel。请执行：pip install openpyxl") from exc

    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="EAF2FB")
    thin = Side(style="thin", color="D0D7DE")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for n, tbl in enumerate(tables, start=1):
        ws = wb.create_sheet(f"P{tbl['page'] + 1}-表格{n}")
        for r_i, row in enumerate(tbl["rows"], start=1):
            for c_i, val in enumerate(row, start=1):
                cell = ws.cell(row=r_i, column=c_i, value=val)
                cell.border = border
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if r_i == 1:
                    cell.font = Font(bold=True)
                    cell.fill = head_fill
        for col in range(1, max(len(r) for r in tbl["rows"]) + 1):
            width = max(
                (len(str(tbl["rows"][r][col - 1])) for r in range(len(tbl["rows"])) if col - 1 < len(tbl["rows"][r])),
                default=8,
            )
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = min(46, max(9, width * 1.9))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"path": out_path, "tables": len(tables)}


def pdf_to_csv(session: DocumentSession, out_dir: str, pages: Any = None) -> list[str]:
    tables = pdf_tables(session, pages)
    if not tables:
        raise ConversionError("未识别到表格")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out = []
    for n, tbl in enumerate(tables, start=1):
        p = Path(out_dir) / f"page{tbl['page'] + 1}_table{n}.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(tbl["rows"])
        out.append(str(p))
    return out


# ------------------------------------------------------------------ → PDF


def images_to_pdf(paths: list[str], out_path: str, page_size: str = "a4", margin: float = 0) -> str:
    """图片合成 PDF。page_size='fit' 时每页尺寸等于图片尺寸。"""
    doc = fitz.open()
    A4 = fitz.paper_rect("a4")
    for p in paths:
        ext = Path(p).suffix.lower()
        if ext == ".pdf":
            src = fitz.open(p)
            doc.insert_pdf(src)
            src.close()
            continue
        if ext not in IMAGE_EXTS:
            continue
        if page_size == "fit":
            img = fitz.open(p)
            r = img[0].rect
            img.close()
            page = doc.new_page(width=r.width, height=r.height)
            page.insert_image(page.rect, filename=p, keep_proportion=True)
        else:
            page = doc.new_page(width=A4.width, height=A4.height)
            area = fitz.Rect(margin, margin, A4.width - margin, A4.height - margin)
            page.insert_image(area, filename=p, keep_proportion=True)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path


def text_to_pdf(text: str, out_path: str, font_size: float = 11, title: str = "") -> str:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    rect = fitz.Rect(56, 56, 539, 786)
    # insert_textbox 会自动换行，返回负值表示放不下，循环续页
    remaining = text
    while remaining:
        overflow = page.insert_textbox(
            rect, remaining, fontsize=font_size, fontname="china-s", lineheight=1.6
        )
        if overflow >= 0:
            break
        # 估算本页容纳的字符数，切分后续页
        approx = int(len(remaining) * (len(remaining) / (len(remaining) + abs(overflow) + 1)))
        approx = max(1, min(approx, len(remaining) - 1))
        remaining = remaining[approx:]
        page = doc.new_page(width=595, height=842)
    if title:
        doc.set_metadata({"title": title})
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path


def docx_to_pdf(src: str, out_path: str) -> str:
    """Word → PDF：优先走本机 Office/WPS COM，退化为提示。"""
    try:
        from docx2pdf import convert  # type: ignore
    except ImportError as exc:
        raise ConversionError(
            "Word 转 PDF 需要本机安装 Microsoft Word 或 WPS，并安装 docx2pdf 组件。\n"
            "指令：pip install docx2pdf"
        ) from exc
    try:
        convert(src, out_path)
    except Exception as exc:  # pragma: no cover
        raise ConversionError(f"调用本机 Word 失败：{exc}") from exc
    return out_path


def merge_files(paths: list[str], out_path: str) -> str:
    doc = fitz.open()
    for p in paths:
        src = fitz.open(p)
        doc.insert_pdf(src)
        src.close()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path
