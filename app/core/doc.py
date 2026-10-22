"""PDF 文档会话：封装 PyMuPDF 的全部读写操作。

设计原则
--------
1. 前端只负责显示，所有"会改变文件"的动作都在 Python 侧完成，避免状态分裂。
2. 页面级操作（旋转/删除/重排）立即落盘到临时文件，前端整篇重载。
3. 注释（高亮/批注）先记在内存队列里，前端即时叠加显示，保存时一次性写入，
   这样既保证了流畅度，也保证了最终产物的正确性。
4. 每次修改都递增 version，前端凭 version 判断是否需要重新拉取字节。
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf as fitz  # PyMuPDF

from . import edit as ascedit  # 文本编辑内核
from . import watermark as wmk  # 水印内核

# 撤销栈上限：每份快照是整本 PDF 的字节副本，防止内存失控
MAX_UNDO_SNAPSHOTS = 10


# ---------------------------------------------------------------- 工具函数


def _safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if ch in bad else ch for ch in name).strip() or "document"


def _parse_pages(spec: Any, total: int) -> list[int]:
    """把 "1-3,5,8-" 这类页码范围解析成 0 基索引列表。"""
    if spec is None:
        return []
    if isinstance(spec, (list, tuple)):
        return [int(p) for p in spec if 0 <= int(p) < total]
    out: list[int] = []
    for chunk in str(spec).replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else total
            out.extend(range(start - 1, min(end, total)))
        else:
            out.append(int(chunk) - 1)
    seen, uniq = set(), []
    for i in out:
        if 0 <= i < total and i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


@dataclass
class Annotation:
    """尚未写入文件的一条注释。"""

    kind: str  # highlight | underline | strikeout | squiggly | note | freetext | ink
    page: int  # 0 基页码
    rects: list[list[float]] = field(default_factory=list)  # PDF 坐标 x0,y0,x1,y1
    color: tuple[float, float, float] = (1, 0.85, 0.2)
    content: str = ""
    opacity: float = 0.45
    aid: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def to_dict(self) -> dict:
        return {
            "id": self.aid,
            "kind": self.kind,
            "page": self.page,
            "rects": self.rects,
            "color": list(self.color),
            "content": self.content,
            "opacity": self.opacity,
        }


class DocumentSession:
    """一个已打开文档的全部状态。"""

    def __init__(self) -> None:
        self.doc: fitz.Document | None = None
        self.path: Path | None = None
        self.token: str = ""
        self.version: int = 0
        self.dirty: bool = False
        self.annotations: list[Annotation] = []
        self.password: str | None = None
        self._tmpdir = Path(tempfile.mkdtemp(prefix="pagesmith_"))
        self._restore_page: int = 0
        self._undo: list[bytes] = []       # 文本编辑撤销栈
        self._undo_labels: list[str] = []

    # ------------------------------------------------------------ 生命周期

    def open(self, path: str | os.PathLike, password: str | None = None) -> dict:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"文件不存在：{p}")
        doc = fitz.open(p)
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                doc.close()
                raise PermissionError("PDF 已加密，需要正确密码")
        self.close()
        self.doc = doc
        self.path = p
        self.password = password
        self.dirty = False
        self.annotations.clear()
        self._bump()
        return self.meta()

    def close(self) -> None:
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self.path = None
        self.token = ""
        self.annotations.clear()
        self.dirty = False

    def _bump(self) -> None:
        """生成新的取流 token —— 前端看到 token 变了就会重新 fetch 字节。"""
        self.version += 1
        self.token = f"{self.version}-{uuid.uuid4().hex[:8]}"

    def _require(self) -> fitz.Document:
        if self.doc is None:
            raise RuntimeError("尚未打开任何文档")
        return self.doc

    # ------------------------------------------------------------ 基础信息

    def meta(self) -> dict:
        doc = self._require()
        md = doc.metadata or {}
        return {
            "token": self.token,
            "version": self.version,
            "name": self.path.name if self.path else "未命名",
            "path": str(self.path) if self.path else "",
            "pages": doc.page_count,
            "size": self.path.stat().st_size if self.path else 0,
            "encrypted": bool(self.password),
            "title": md.get("title") or "",
            "author": md.get("author") or "",
            "subject": md.get("subject") or "",
            "creator": md.get("creator") or "",
            "producer": md.get("producer") or "",
            "dirty": self.dirty,
            "annotations": [a.to_dict() for a in self.annotations],
            "restore_page": self._restore_page,
            "undo": len(self._undo),
        }

    def page_sizes(self) -> list[list[float]]:
        doc = self._require()
        out = []
        for page in doc:
            r = page.rect
            out.append([round(r.width, 2), round(r.height, 2)])
        return out

    def outline(self) -> list[dict]:
        doc = self._require()
        toc = doc.get_toc(simple=True) or []
        root: list[dict] = []
        stack: list[tuple[int, list[dict]]] = [(0, root)]
        for level, title, page in toc:
            node = {"title": title, "page": max(0, page - 1), "children": []}
            while stack and stack[-1][0] >= level:
                stack.pop()
            if not stack:
                stack = [(0, root)]
            stack[-1][1].append(node)
            stack.append((level, node["children"]))
        return root

    # ------------------------------------------------------------ 字节流

    def to_bytes(self, optimize: bool = False) -> bytes:
        """序列化当前文档，供前端 pdf.js 加载。"""
        doc = self._require()
        if not self.dirty and not optimize and self.path:
            # 未修改过就直接读原文件，省掉一次重写
            return self.path.read_bytes()
        kw: dict[str, Any] = {"deflate": True}
        if optimize:
            kw = {"garbage": 4, "deflate": True, "clean": True}
        return doc.tobytes(**kw)

    def _persist(self) -> None:
        """把内存改动写回临时文件，保持 doc 与磁盘一致（不需用户确认的场景）。"""
        doc = self._require()
        tmp = self._tmpdir / f"v{self.version}.pdf"
        doc.save(tmp, garbage=0, deflate=True, incremental=False)
        # 重开，避免 PyMuPDF 对已保存的 doc 继续修改出错
        reopen = fitz.open(tmp)
        doc.close()
        self.doc = reopen
        self.dirty = True

    # ------------------------------------------------------------ 页面操作

    def rotate(self, pages: Any, angle: int) -> dict:
        doc = self._require()
        targets = _parse_pages(pages, doc.page_count)
        for i in targets:
            page = doc[i]
            page.set_rotation((page.rotation + angle) % 360)
        self.dirty = True
        self._bump()
        return self.meta()

    def set_rotation(self, page_index: int, angle: int) -> dict:
        doc = self._require()
        doc[page_index].set_rotation(angle % 360)
        self.dirty = True
        self._bump()
        return self.meta()

    def delete_pages(self, pages: Any) -> dict:
        doc = self._require()
        targets = sorted(_parse_pages(pages, doc.page_count), reverse=True)
        if len(targets) >= doc.page_count:
            raise ValueError("不能删除全部页面")
        for i in targets:
            doc.delete_page(i)
        self.dirty = True
        self._bump()
        return self.meta()

    def move_page(self, src: int, dst: int) -> dict:
        doc = self._require()
        src = max(0, min(src, doc.page_count - 1))
        dst = max(0, min(dst, doc.page_count - 1))
        if src != dst:
            doc.move_page(src, dst)
            self.dirty = True
            self._bump()
        return self.meta()

    def reverse(self) -> dict:
        doc = self._require()
        n = doc.page_count
        # PyMuPDF 没有内置反转，用新建文档倒序插入
        new = fitz.open()
        for i in range(n - 1, -1, -1):
            new.insert_pdf(doc, from_page=i, to_page=i)
        doc.close()
        self.doc = new
        self.dirty = True
        self._bump()
        return self.meta()

    def insert_pdf(self, path: str, at: int | None = None) -> dict:
        doc = self._require()
        src = fitz.open(path)
        pos = doc.page_count if at is None else max(0, min(at, doc.page_count))
        doc.insert_pdf(src, start_at=pos)
        src.close()
        self.dirty = True
        self._bump()
        return self.meta()

    def extract_pages(self, pages: Any, out_path: str) -> str:
        """把指定页抽成一个新 PDF。"""
        doc = self._require()
        targets = _parse_pages(pages, doc.page_count)
        if not targets:
            raise ValueError("没有选中任何页面")
        new = fitz.open()
        for i in targets:
            new.insert_pdf(doc, from_page=i, to_page=i)
        self._ensure_ext(out_path)
        new.save(out_path, garbage=3, deflate=True)
        new.close()
        return out_path

    def split_every(self, every: int, out_dir: str, name_tpl: str = "{name}_part{n}.pdf") -> list[str]:
        """按每 N 页一个文件拆分。"""
        doc = self._require()
        every = max(1, int(every))
        stem = _safe_name(self.path.stem if self.path else "document")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        results: list[str] = []
        part = 1
        for start in range(0, doc.page_count, every):
            new = fitz.open()
            new.insert_pdf(doc, from_page=start, to_page=min(start + every, doc.page_count) - 1)
            out = Path(out_dir) / name_tpl.format(name=stem, n=part, start=start + 1)
            new.save(out, garbage=3, deflate=True)
            new.close()
            results.append(str(out))
            part += 1
        return results

    def blank_page(self, at: int | None = None, width: float = 595, height: float = 842) -> dict:
        doc = self._require()
        pos = doc.page_count if at is None else max(0, min(at, doc.page_count))
        doc.new_page(pno=pos, width=width, height=height)
        self.dirty = True
        self._bump()
        return self.meta()

    # ------------------------------------------------------------ 注释

    def add_annotation(self, payload: dict) -> dict:
        doc = self._require()
        page_index = int(payload.get("page", 0))
        if not 0 <= page_index < doc.page_count:
            raise ValueError("页码越界")
        rects = payload.get("rects") or []
        if payload.get("kind") != "note" and not rects:
            raise ValueError("缺少选区坐标")
        color = payload.get("color") or [1, 0.85, 0.2]
        annot = Annotation(
            kind=payload.get("kind", "highlight"),
            page=page_index,
            rects=[[float(v) for v in r] for r in rects],
            color=tuple(float(c) for c in color[:3]),
            content=payload.get("content", "") or "",
            opacity=float(payload.get("opacity", 0.45)),
        )
        self.annotations.append(annot)
        self.dirty = True
        return annot.to_dict()

    def remove_annotation(self, aid: str) -> bool:
        before = len(self.annotations)
        self.annotations = [a for a in self.annotations if a.aid != aid]
        changed = len(self.annotations) != before
        if changed:
            self.dirty = True
        return changed

    def update_annotation(self, aid: str, content: str) -> bool:
        for a in self.annotations:
            if a.aid == aid:
                a.content = content
                self.dirty = True
                return True
        return False

    def clear_annotations(self) -> None:
        self.annotations.clear()
        self.dirty = True

    def _flush_annotations(self, doc: fitz.Document) -> None:
        """把内存中的注释真正写入 PyMuPDF 文档。

        注释坐标全程用前端 pdf.js 空间（y 向上）保存，只有写进文件这一刻
        才换成 PyMuPDF 空间（y 向下），旋转过的页面也不会错位。
        """
        from . import edit as _cedit

        kind_map = {
            "highlight": ("add_highlight_annot", {}),
            "underline": ("add_underline_annot", {}),
            "strikeout": ("add_strikeout_annot", {}),
            "squiggly": ("add_squiggly_annot", {}),
        }
        for a in self.annotations:
            if not 0 <= a.page < doc.page_count:
                continue
            page = doc[a.page]
            if a.kind in kind_map:
                method, _ = kind_map[a.kind]
                for r in a.rects:
                    fitz_r = fitz.Rect(_cedit.pdfjs_rect(page, r))
                    quad = fitz_r.quad
                    an = getattr(page, method)(quads=quad) if a.kind == "highlight" else getattr(page, method)(fitz_r)
                    an.set_colors(stroke=a.color)
                    an.set_opacity(a.opacity)
                    an.set_info(content=a.content or "")
                    an.update()
            elif a.kind == "note" and a.rects:
                r = a.rects[0]
                pt = _cedit.pdfjs_point(page, r[0], r[1])
                an = page.add_text_annot(pt, a.content or "批注")
                an.set_colors(stroke=a.color)
                an.update()
            elif a.kind == "freetext" and a.rects:
                fitz_r = fitz.Rect(_cedit.pdfjs_rect(page, a.rects[0]))
                an = page.add_freetext_annot(fitz_r, a.content, fontsize=11, text_color=(0, 0, 0))
                an.set_border(width=0.5)
                an.set_colors(stroke=a.color)
                an.update()
            elif a.kind == "ink" and a.rects:
                fitz_r = fitz.Rect(_cedit.pdfjs_rect(page, a.rects[0]))
                an = page.add_ink_annot([fitz_r.quad])
                an.set_colors(stroke=a.color)
                an.update()

    # ------------------------------------------------------------ 文本编辑

    def _snapshot(self, label: str = "") -> None:
        """编辑前留存整本快照，用于撤销。"""
        try:
            self._undo.append(self.doc.tobytes(deflate=True))
            self._undo_labels.append(label)
            if len(self._undo) > MAX_UNDO_SNAPSHOTS:
                self._undo.pop(0)
                self._undo_labels.pop(0)
        except Exception:
            pass  # 快照失败不阻断编辑本身

    def undo_edit(self) -> dict:
        if not self._undo:
            raise ValueError("没有可撤销的编辑")
        data = self._undo.pop()
        label = self._undo_labels.pop()
        self.doc = fitz.open("pdf", data)
        self.dirty = True
        self._bump()
        meta = self.meta()
        meta["undo_label"] = label
        return meta

    def can_undo(self) -> bool:
        return bool(self._undo)

    def text_spans(self, page_index: int) -> list[dict]:
        """页面上的可编辑文本片段（坐标已转成前端 pdf.js 空间）。"""
        doc = self._require()
        if not 0 <= page_index < doc.page_count:
            raise ValueError("页码越界")
        return ascedit.text_spans(doc[page_index])

    def apply_edit(self, payload: dict) -> dict:
        """执行一次文本编辑（replace / delete / insert），返回新 meta。"""
        doc = self._require()
        pno = int(payload.get("page", -1))
        if not 0 <= pno < doc.page_count:
            raise ValueError("页码越界")
        page = doc[pno]
        kind = payload.get("kind", "")
        label = {"replace": "替换文字", "delete": "删除文字", "insert": "新增文字"}.get(kind, "编辑")

        # 前端（pdf.js 空间，y 向上）→ PyMuPDF（y 向下）
        def frect(r):
            return ascedit.pdfjs_rect(page, r)

        def fpoint(x, y):
            return ascedit.pdfjs_point(page, x, y)

        self._snapshot(label)
        try:
            if kind == "delete":
                rects = payload.get("rects") or ([payload["rect"]] if payload.get("rect") else [])
                if not rects:
                    raise ValueError("缺少删除区域")
                for r in rects:
                    ascedit.delete_rect(page, frect(r))
            elif kind == "replace":
                rect = payload.get("rect")
                text = str(payload.get("text") or "")
                if not rect:
                    raise ValueError("缺少原文本区域")
                target = frect(rect)
                if text.strip():
                    # 原文先删，再按工具条参数在原位置重写
                    ascedit.delete_rect(page, target)
                    size = float(payload.get("size") or (target[3] - target[1]) or 12)
                    font = ascedit._pick(payload.get("font"))
                    ascedit._write(
                        page, text, (target[0], target[1]),
                        size=size,
                        font=font,
                        color=ascedit._to_rgb(payload.get("color")),
                        direction=payload.get("direction", "h"),
                    )
                    ascedit.patch_space_tounicode(page, font)
                else:
                    ascedit.delete_rect(page, target)
            elif kind == "insert":
                text = str(payload.get("text") or "")
                if not text.strip():
                    raise ValueError("文字内容为空")
                x, y = fpoint(float(payload.get("x", 0)), float(payload.get("y", 0)))
                font = ascedit._pick(payload.get("font"))
                ascedit._write(
                    page, text, (x, y),
                    size=float(payload.get("size", 14)),
                    font=font,
                    color=ascedit._to_rgb(payload.get("color"), (0.07, 0.07, 0.07)),
                    direction=payload.get("direction", "h"),
                )
                ascedit.patch_space_tounicode(page, font)
            else:
                raise ValueError(f"未知的编辑类型：{kind}")
        except Exception:
            # 编辑失败时回滚快照，保持文档与界面一致
            if self._undo:
                data = self._undo.pop()
                self._undo_labels.pop()
                try:
                    self.doc = fitz.open("pdf", data)
                except Exception:
                    pass
            raise

        self.dirty = True
        self._bump()
        return self.meta()

    # ------------------------------------------------------------ 水印

    def watermark(self, payload: dict) -> dict:
        """给文档加水印（进撤销栈，Ctrl+Z 可整体撤回）。"""
        doc = self._require()
        opts = wmk.Options.parse(payload)

        spec = payload.get("pages")
        explicit = spec is not None and str(spec).strip() != ""
        targets = _parse_pages(spec, doc.page_count) if explicit else None
        if explicit and not targets:
            raise ValueError("页码范围没有匹配到任何页面")

        self._snapshot("添加水印")
        try:
            wmk.apply(doc, opts, targets)
        except Exception:
            if self._undo:
                data = self._undo.pop()
                self._undo_labels.pop()
                try:
                    self.doc = fitz.open("pdf", data)
                except Exception:
                    pass
            raise

        self.dirty = True
        self._bump()
        return self.meta()

    def watermark_preview(self, payload: dict) -> dict:
        """渲染"加水印之后"的当前页预览图（不改动文档）。"""
        doc = self._require()
        opts = wmk.Options.parse(payload)
        page = int(payload.get("page", self._restore_page) or 0)
        width = max(160, min(720, int(payload.get("width", 460) or 460)))
        png = wmk.preview_png(doc, page, opts, max_width=width)
        import base64

        return {"png": "data:image/png;base64," + base64.b64encode(png).decode("ascii"), "page": page}

    # ------------------------------------------------------------ 保存

    def save(self, out_path: str, password: str | None = None, owner_password: str | None = None) -> str:
        doc = self._require()
        self._ensure_ext(out_path)
        before = len(self.annotations)
        if before:
            self._flush_annotations(doc)
        kw: dict[str, Any] = {"garbage": 4, "deflate": True, "clean": True}
        if password:
            kw.update(
                encryption=fitz.PDF_ENCRYPT_AES_256,
                user_pw=password,
                owner_pw=owner_password or password,
                permissions=fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ACCESSIBILITY,
            )
        doc.save(out_path, **kw)
        self.annotations.clear()
        self.dirty = False
        return out_path

    def save_inplace(self) -> dict:
        if not self.path:
            raise RuntimeError("文档没有原始路径，请另存为")
        self.save(str(self.path))
        return self.open(str(self.path))

    @staticmethod
    def _ensure_ext(out_path: str) -> None:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 文本 / 元数据

    def page_text(self, page_index: int) -> str:
        doc = self._require()
        return doc[page_index].get_text("text")

    def all_text(self) -> str:
        doc = self._require()
        return "\n\n".join(f"--- 第 {i + 1} 页 ---\n{p.get_text('text')}" for i, p in enumerate(doc))

    def set_metadata(self, data: dict) -> dict:
        doc = self._require()
        md = doc.metadata or {}
        for key in ("title", "author", "subject", "keywords"):
            if key in data:
                md[key] = data[key]
        doc.set_metadata(md)
        self.dirty = True
        return self.meta()
