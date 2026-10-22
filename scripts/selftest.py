"""核心引擎自检：逐项验证 PyMuPDF 的能力与 API 签名是否与代码假设一致。

直接运行： python scripts/selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf as fitz  # noqa: E402

PASS, FAIL, WARN = "[OK]", "[FAIL]", "[WARN]"
results: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    try:
        detail = fn()
        results.append((name, True, str(detail or "")))
        print(f"{PASS} {name}  {detail or ''}")
    except Exception as exc:
        results.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"{FAIL} {name}  {type(exc).__name__}: {exc}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="selftest_"))
    print(f"PyMuPDF {fitz.__doc__.splitlines()[0] if fitz.__doc__ else '?'}")
    print(f"临时目录 {tmp}\n")

    doc_path = tmp / "base.pdf"

    def make_doc():
        doc = fitz.open()
        for i in range(5):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"Page {i + 1} hello world", fontsize=18)
            page.insert_text((72, 140), "中文测试内容 表格示例", fontsize=14, fontname="china-s")
            page.draw_rect(fitz.Rect(72, 200, 400, 320), color=(0, 0, 0), width=1)
            page.insert_text((80, 230), f"row A{i}  col B{i}", fontsize=12)
        doc.save(doc_path, garbage=3, deflate=True)
        doc.close()
        return f"{doc_path.name} 5页"

    check("创建文档", make_doc)

    def open_doc():
        d = fitz.open(doc_path)
        n = d.page_count
        d.close()
        return f"{n} 页"

    check("打开文档", open_doc)

    def tobytes_api():
        d = fitz.open(doc_path)
        data = d.tobytes(deflate=True)
        d.close()
        return f"{len(data)} bytes"

    check("doc.tobytes()", tobytes_api)

    def rotate_set():
        d = fitz.open(doc_path)
        p = d[0]
        before = p.rotation
        p.set_rotation((before + 90) % 360)
        after = d[0].rotation
        d.close()
        assert after == (before + 90) % 360, f"rotation {before}->{after}"
        return f"{before} -> {after}"

    check("page.set_rotation()", rotate_set)

    def delete_move():
        d = fitz.open(doc_path)
        d.delete_page(1)
        n1 = d.page_count
        d.move_page(0, 2)
        n2 = d.page_count
        d.close()
        assert n1 == 4 and n2 == 4
        return f"删除后{n1}页，移动后{n2}页"

    check("delete_page / move_page", delete_move)

    def insert_pdf_api():
        d = fitz.open(doc_path)
        src = fitz.open(doc_path)
        d.insert_pdf(src, start_at=0)
        n = d.page_count
        src.close()
        d.close()
        return f"插入后 {n} 页"

    check("insert_pdf(start_at=)", insert_pdf_api)

    def pixmap_api():
        d = fitz.open(doc_path)
        pix = d[0].get_pixmap(dpi=96)
        size = (pix.width, pix.height)
        jpg = pix.tobytes("jpeg", jpg_quality=70)
        d.close()
        return f"{size[0]}x{size[1]}, jpeg {len(jpg)}B"

    check("get_pixmap + tobytes('jpeg')", pixmap_api)

    def highlight_quads():
        d = fitz.open(doc_path)
        page = d[0]
        rect = fitz.Rect(70, 86, 300, 108)
        an = page.add_highlight_annot(quads=rect.quad)
        an.set_colors(stroke=(1, 0.85, 0.2))
        an.set_opacity(0.4)
        an.set_info(content="测试批注")
        an.update()
        cnt = len(list(page.annots() or []))
        d.close()
        assert cnt >= 1, f"annots={cnt}"
        return f"{cnt} 条注释"

    check("add_highlight_annot(quads=)", highlight_quads)

    def underline_rect():
        d = fitz.open(doc_path)
        page = d[0]
        an = page.add_underline_annot(fitz.Rect(70, 86, 300, 108))
        an.set_colors(stroke=(0.2, 0.4, 1))
        an.update()
        d.close()
        return "ok"

    check("add_underline_annot(rect)", underline_rect)

    def text_annot():
        d = fitz.open(doc_path)
        an = d[0].add_text_annot(fitz.Point(300, 300), "这是一个便签批注")
        an.update()
        d.close()
        return "ok"

    check("add_text_annot(point, text)", text_annot)

    def freetext():
        d = fitz.open(doc_path)
        an = d[0].add_freetext_annot(fitz.Rect(80, 400, 320, 450), "自由文本批注", fontsize=11)
        an.update()
        d.close()
        return "ok"

    check("add_freetext_annot", freetext)

    def invisible_text():
        d = fitz.open(doc_path)
        page = d[0]
        rc = page.insert_textbox(
            fitz.Rect(72, 500, 400, 530),
            "不可见文字层 OCR",
            fontsize=12,
            fontname="china-s",
            render_mode=3,
        )
        d.close()
        assert rc >= 0, f"insert_textbox 返回 {rc}"
        return f"rc={rc}"

    check("insert_textbox(render_mode=3, china-s)", invisible_text)

    def find_tables_api():
        d = fitz.open()
        page = d.new_page(width=595, height=842)
        # 画一个真表格
        rows, cols = 3, 3
        x0, y0, w, h = 72, 100, 130, 30
        for r in range(rows + 1):
            page.draw_line(fitz.Point(x0, y0 + r * h), fitz.Point(x0 + cols * w, y0 + r * h))
        for c in range(cols + 1):
            page.draw_line(fitz.Point(x0 + c * w, y0), fitz.Point(x0 + c * w, y0 + rows * h))
        for r in range(rows):
            for c in range(cols):
                page.insert_text((x0 + c * w + 6, y0 + r * h + 20), f"R{r}C{c}", fontsize=11)
        path = tmp / "table.pdf"
        d.save(path)
        d.close()

        d2 = fitz.open(path)
        found = d2[0].find_tables()
        n = len(found.tables)
        data = found.tables[0].extract() if n else []
        d2.close()
        assert n >= 1, "未识别到表格"
        return f"{n} 个表格，首行 {data[0] if data else []}"

    check("page.find_tables()", find_tables_api)

    def encrypt_save():
        d = fitz.open(doc_path)
        out = tmp / "enc.pdf"
        d.save(
            out,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw="1234",
            owner_pw="1234",
            permissions=fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY,
        )
        d.close()
        d2 = fitz.open(out)
        needs = d2.needs_pass
        ok = d2.authenticate("1234")
        d2.close()
        assert needs and ok
        return "AES-256 加密 / 认证通过"

    check("save(encryption=AES_256)", encrypt_save)

    def consts():
        names = ["PDF_ENCRYPT_AES_256", "PDF_PERM_PRINT", "PDF_PERM_COPY",
                 "PDF_PERM_ACCESSIBILITY", "PDF_PERM_PRINT_HQ"]
        missing = [n for n in names if not hasattr(fitz, n)]
        assert not missing, f"缺失常量 {missing}"
        return "全部存在"

    check("权限常量", consts)

    def metadata_api():
        d = fitz.open(doc_path)
        md = d.metadata
        d.set_metadata({**(md or {}), "title": "测试标题"})
        t = d.metadata.get("title")
        d.close()
        assert t == "测试标题"
        return t

    check("set_metadata", metadata_api)

    def outline_api():
        d = fitz.open(doc_path)
        toc = [[1, "第一章", 1], [2, "第一节", 2], [1, "第二章", 4]]
        d.set_toc(toc)
        got = d.get_toc(simple=True)
        d.close()
        assert len(got) == 3, f"toc={got}"
        return f"{len(got)} 条"

    check("set_toc / get_toc", outline_api)

    def page_replace_image():
        d = fitz.open(doc_path)
        has = hasattr(d[0], "replace_image")
        d.close()
        assert has, "Page 没有 replace_image 方法"
        return "可用"

    check("page.replace_image", page_replace_image)

    def insert_text_morph():
        d = fitz.open(doc_path)
        page = d[0]
        pivot = fitz.Point(297, 421)
        mat = fitz.Matrix(1, 1).prerotate(-45)
        page.insert_text(pivot, "水印 WATERMARK", fontsize=40, fontname="china-s",
                         color=(0.5, 0.5, 0.5), fill_opacity=0.12, morph=(pivot, mat))
        d.close()
        return "旋转水印 ok"

    check("insert_text(morph=) 水印", insert_text_morph)

    # ── 可选依赖探测
    print()

    def probe(name, mod, attr=None):
        def _fn():
            import importlib

            m = importlib.import_module(mod)
            if attr:
                importlib.import_module(f"{mod}.{attr}")
            return "已安装"
        check(f"可选依赖 {name}", _fn)

    probe("pdf2docx", "pdf2docx", "converter")
    probe("openpyxl", "openpyxl", "workbook")
    probe("rapidocr-onnxruntime", "rapidocr_onnxruntime")
    probe("Pillow", "PIL", "Image")

    ok = sum(1 for _, s, _ in results if s)
    print(f"\n{'=' * 52}\n通过 {ok}/{len(results)}")
    for name, success, detail in results:
        if not success:
            print(f"  {FAIL} {name}: {detail}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
