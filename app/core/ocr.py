"""OCR：把扫描件识别成可搜索、可复制的文字层。

实现要点
--------
* 用 RapidOCR（PaddleOCR 的 ONNX 轻量版），不依赖 paddle 框架，打包体积可控。
* 识别结果以「不可见文字」写入 PDF 原位置（render_mode=3），
  视觉上完全不影响原文件，但复制/搜索/朗读都能用。
* 引擎单例懒加载 —— 首次调用约 1~2 秒，之后常驻内存。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pymupdf as fitz  # PyMuPDF

from .doc import DocumentSession, _parse_pages

_engine = None
_engine_lock = threading.Lock()


class OcrUnavailable(RuntimeError):
    pass


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OcrUnavailable(
                "缺少 OCR 组件。请执行：pip install rapidocr-onnxruntime onnxruntime"
            ) from exc
        _engine = RapidOCR()
        return _engine


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _pick_font(text: str) -> str:
    """含中文就用内置 CJK 字体，否则用 Helvetica。"""
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return "china-s"
    return "helv"


def ocr_pages(
    session: DocumentSession,
    pages: Any = None,
    dpi: int = 300,
    min_confidence: float = 0.55,
    write_layer: bool = True,
) -> dict:
    """对指定页做 OCR。write_layer=True 时把识别结果写入不可见文字层。"""
    engine = _get_engine()
    doc = session.doc
    targets = _parse_pages(pages, doc.page_count) if pages else list(range(doc.page_count))
    scale = 72.0 / dpi

    summary = {"pages": [], "blocks": 0, "written": 0}
    for page_index in targets:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img_bytes = pix.tobytes("png")

        result, _elapse = engine(img_bytes)
        blocks: list[dict] = []
        for item in result or []:
            try:
                box, text, score = item[0], item[1], float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            if score < min_confidence or not str(text).strip():
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            rect = fitz.Rect(min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale)
            blocks.append({"text": str(text), "rect": [rect.x0, rect.y0, rect.x1, rect.y1], "score": score})

        if write_layer:
            # 已存在文字层说明不是纯扫描件，跳过以免重复叠加
            existing = page.get_text("text").strip()
            if len(existing) > 20:
                summary["pages"].append(
                    {"page": page_index, "blocks": len(blocks), "skipped": "该页已有文字层，已跳过写入"}
                )
                summary["blocks"] += len(blocks)
                continue
            for b in blocks:
                rect = fitz.Rect(b["rect"])
                if rect.is_empty or rect.width < 1 or rect.height < 1:
                    continue
                text = b["text"]
                font = _pick_font(text)
                size = max(4.0, min(rect.height * 0.82, 42))
                # 先按框内排，放不下就缩小字号重试
                for attempt in range(3):
                    rc = page.insert_textbox(
                        rect,
                        text,
                        fontsize=size,
                        fontname=font,
                        render_mode=3,
                        color=(0, 0, 0),
                    )
                    if rc >= 0:
                        summary["written"] += 1
                        break
                    size *= 0.72
                    if size < 3.5:
                        break
            session.dirty = True

        summary["pages"].append({"page": page_index, "blocks": len(blocks), "text": blocks})
        summary["blocks"] += len(blocks)

    if write_layer and summary["written"]:
        session._bump()
    return summary


def ocr_text_only(session: DocumentSession, pages: Any = None, dpi: int = 250) -> str:
    """只识别文字，不写回文档 —— 用于快速提取扫描件内容。"""
    engine = _get_engine()
    doc = session.doc
    targets = _parse_pages(pages, doc.page_count) if pages else list(range(doc.page_count))
    chunks = []
    for i in targets:
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        result, _ = engine(pix.tobytes("png"))
        texts = [str(item[1]) for item in (result or []) if len(item) >= 3 and float(item[2]) >= 0.5]
        chunks.append(f"--- 第 {i + 1} 页 ---\n" + "\n".join(texts))
    return "\n\n".join(chunks)
