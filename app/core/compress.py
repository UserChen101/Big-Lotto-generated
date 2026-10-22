"""体积优化：从「结构层清理」到「图片重采样重编码」的分级压缩。

PDF 体积的大头通常只有两个：内嵌图片和重复对象。
所以策略是 —— 轻度只清结构，中重度才动图片，且尽量保留文字层不失真。
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from .doc import DocumentSession

LEVELS = {
    "light": {"max_px": 0, "quality": 0, "grayscale": False, "dpi": 0},
    "medium": {"max_px": 1600, "quality": 78, "grayscale": False, "dpi": 0},
    "strong": {"max_px": 1000, "quality": 58, "grayscale": False, "dpi": 0},
    "extreme": {"max_px": 760, "quality": 42, "grayscale": True, "dpi": 0},
}


def _recompress_images(doc: fitz.Document, cfg: dict) -> int:
    """把超过尺寸阈值的位图重采样为 JPEG，返回替换数量。"""
    try:
        from PIL import Image
    except ImportError:
        return 0

    max_px = cfg["max_px"]
    quality = cfg["quality"]
    gray = cfg["grayscale"]
    done: set[int] = set()
    replaced = 0

    for page in doc:
        try:
            images = page.get_images(full=True)
        except Exception:
            continue
        for info in images:
            xref = info[0]
            if xref in done:
                continue
            done.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                w, h = pix.width, pix.height
                if max(w, h) <= max_px and not gray:
                    pix = None
                    continue
                if pix.n - pix.alpha >= 4:  # CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                mode = "RGB"
                raw = pix.samples
                img = Image.frombytes(mode, (pix.width, pix.height), raw)

                if max(img.size) > max_px:
                    ratio = max_px / max(img.size)
                    img = img.resize(
                        (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                        Image.LANCZOS,
                    )
                if gray:
                    img = img.convert("L")

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
                data = buf.getvalue()
                # 只有变小了才替换，否则留着原图
                if len(data) < max(2048, w * h * 0.4):
                    page.replace_image(xref, stream=data)
                    replaced += 1
            except Exception:
                continue
            finally:
                try:
                    pix = None
                except Exception:
                    pass
    return replaced


def compress(session: DocumentSession, level: str = "medium", out_path: str | None = None) -> dict:
    """压缩并返回体积对比。out_path 为空时覆盖回原文件。"""
    doc = session.doc
    cfg = LEVELS.get(level, LEVELS["medium"])
    std_size = Path(session.path).stat().st_size if session.path else 0

    replaced = 0
    if cfg["max_px"]:
        doc = _migrate(session, doc)
        replaced = _recompress_images(doc, cfg)

    target = out_path or (str(session.path) if session.path else None)
    if not target:
        raise RuntimeError("请指定输出路径")

    tmp = Path(tempfile.mkdtemp()) / "compressed.pdf"
    doc.save(
        str(tmp),
        garbage=4,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        clean=True,
    )
    new_size = tmp.stat().st_size

    # 压缩结果反而更大时，保留原文件不动
    if new_size >= std_size and std_size > 0 and not out_path:
        tmp.unlink(missing_ok=True)
        return {
            "path": target,
            "before": std_size,
            "after": std_size,
            "saved": 0,
            "ratio": 0.0,
            "images": replaced,
            "note": "当前文件已足够精简，未做改动",
        }

    Path(target).parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(target)
    if not out_path:
        session.open(target)
    return {
        "path": target,
        "before": std_size,
        "after": new_size,
        "saved": max(0, std_size - new_size),
        "ratio": round((1 - new_size / std_size) * 100, 1) if std_size else 0.0,
        "images": replaced,
    }


def _migrate(session: DocumentSession, doc: fitz.Document) -> fitz.Document:
    """有未落盘改动时先固化，保证图片操作作用于真实对象。"""
    if not session.dirty:
        return doc
    tmp = Path(tempfile.mkdtemp()) / "sync.pdf"
    doc.save(str(tmp), garbage=0, deflate=True)
    doc.close()
    fresh = fitz.open(tmp)
    session.doc = fresh
    return fresh


def encrypt(session: DocumentSession, out_path: str, user_pw: str, owner_pw: str = "", allow_print: bool = True,
            allow_copy: bool = True) -> str:
    doc = session.doc
    perms = fitz.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perms |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
    if allow_copy:
        perms |= fitz.PDF_PERM_COPY
    if session.annotations:
        session._flush_annotations(doc)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(
        out_path,
        garbage=4,
        deflate=True,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw=user_pw,
        owner_pw=owner_pw or user_pw,
        permissions=perms,
    )
    return out_path


def decrypt(session: DocumentSession, out_path: str) -> str:
    """去掉密码保护（需要已用正确密码打开文档）。"""
    doc = session.doc
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path, garbage=4, deflate=True)
    return out_path
