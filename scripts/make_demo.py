"""生成一个用于演示/自测的中文 PDF（含目录、表格、图文混排）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf as fitz

OUT = Path(__file__).resolve().parent.parent / "sample" / "产品需求说明书.pdf"


def build() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    W, H = 595, 842
    accent = (0.14, 0.39, 0.92)

    # ── 封面
    page = doc.new_page(width=W, height=H)
    page.draw_rect(fitz.Rect(0, 0, W, 210), color=None, fill=(0.09, 0.13, 0.19))
    page.insert_text((56, 92), "PRODUCT SPEC", fontsize=11, fontname="helv",
                     color=(0.55, 0.65, 0.8))
    page.insert_text((56, 138), "产品需求说明书", fontsize=32, fontname="china-s",
                     color=(1, 1, 1))
    page.insert_text((56, 176), "PDF Studio 桌面端 · v1.0", fontsize=13, fontname="china-s",
                     color=(0.7, 0.78, 0.9))
    page.insert_text((56, 260), "文档编号", fontsize=10, fontname="china-s", color=(0.45, 0.5, 0.58))
    page.insert_text((150, 260), "SPEC-2026-1021", fontsize=11, fontname="helv")
    page.insert_text((56, 285), "编写人", fontsize=10, fontname="china-s", color=(0.45, 0.5, 0.58))
    page.insert_text((150, 285), "松子蟹", fontsize=11, fontname="china-s")
    page.insert_text((56, 310), "版本", fontsize=10, fontname="china-s", color=(0.45, 0.5, 0.58))
    page.insert_text((150, 310), "1.0 / 2026-10-21", fontsize=11, fontname="helv")

    paras = [
        "本文档描述 PDF Studio 桌面端的功能范围与技术实现方案。",
        "目标是提供一套可替代 Adobe Acrobat 日常使用的本地工具，",
        "覆盖阅读、批注、页面编排、格式转换与 OCR 识别五大场景。",
        "所有处理均在本机完成，不上传任何文件。",
    ]
    y = 380
    for line in paras:
        page.insert_text((56, y), line, fontsize=12.5, fontname="china-s", color=(0.2, 0.24, 0.3))
        y += 28

    page.draw_line(fitz.Point(56, 520), fitz.Point(W - 56, 520), color=(0.85, 0.87, 0.9))
    page.insert_text((56, 552), "核心能力", fontsize=13, fontname="china-s", color=accent)
    feats = ["① 阅读器：连续滚动、虚拟化渲染、文字选择、全文搜索",
             "② 批注：高亮 / 下划线 / 删除线 / 便签，可导出清单",
             "③ 页面：旋转、删除、拖拽重排、拆分、合并、插入",
             "④ 转换：Word / Excel / 图片 / HTML / 纯文本",
             "⑤ 安全：AES-256 加密、权限控制、体积压缩、水印"]
    y = 588
    for f in feats:
        page.insert_text((62, y), f, fontsize=11.5, fontname="china-s", color=(0.25, 0.3, 0.36))
        y += 26
    page.insert_text((56, H - 60), "第 1 页 / 共 5 页", fontsize=9, fontname="china-s",
                     color=(0.6, 0.64, 0.7))

    # ── 正文页
    chapters = [
        ("一、技术架构", [
            "界面层使用 WebView2 承载 HTML/CSS/JS，渲染交给 pdf.js。",
            "这是 Firefox 内置的 PDF 引擎，文字选取、目录、链接支持成熟。",
            "文档处理层是 PyMuPDF，底层为 MuPDF，具备完整的读写与注释能力。",
            "两层之间通过本地 HTTP 与 js_api 双通道通信，大文件走字节流。",
        ]),
        ("二、性能策略", [
            "页面采用虚拟化渲染：只画视口附近两屏内的页面。",
            "内存中最多保留 10 张 canvas，超出后按最久未使用淘汰。",
            "缩略图由后端统一渲染并缓存，滚动时不重复解码。",
            "大文件支持 HTTP Range 请求，首屏无需等待整份下载。",
        ]),
        ("三、转换能力", [
            "PDF 转 Word 使用 pdf2docx，保留段落、字体与图片位置。",
            "PDF 转 Excel 基于 MuPDF 的表格线框识别，再写入 xlsx。",
            "图片导出支持自定义 DPI 与 JPEG 质量，便于网页发布。",
            "图片合成 PDF 时按 A4 等比缩放，也可选择原尺寸页面。",
        ]),
    ]

    for idx, (title, lines) in enumerate(chapters, start=2):
        page = doc.new_page(width=W, height=H)
        page.insert_text((56, 78), title, fontsize=21, fontname="china-s", color=(0.09, 0.13, 0.19))
        page.draw_line(fitz.Point(56, 96), fitz.Point(150, 96), color=accent, width=2.2)
        y = 140
        for line in lines:
            page.insert_text((56, y), line, fontsize=12.5, fontname="china-s",
                             color=(0.22, 0.26, 0.32))
            y += 30

        # 表格
        rows = [
            ["项目", "规格", "备注"],
            ["渲染引擎", "pdf.js 4.10", "Mozilla 开源"],
            ["文档内核", "MuPDF 1.28", "AGPL / 商业双许可"],
            ["OCR", "RapidOCR ONNX", "中英混排"],
            ["打包体积", "约 120 MB", "单文件 exe"],
        ]
        x0, y0, h = 56, y + 40, 30
        widths = [130, 170, 180]
        total_w = sum(widths)
        for r in range(len(rows) + 1):
            page.draw_line(fitz.Point(x0, y0 + r * h), fitz.Point(x0 + total_w, y0 + r * h),
                           color=(0.8, 0.83, 0.88), width=0.8)
        cx = x0
        for w in widths + [0]:
            page.draw_line(fitz.Point(cx, y0), fitz.Point(cx, y0 + len(rows) * h),
                           color=(0.8, 0.83, 0.88), width=0.8)
            cx += w
        if True:
            page.draw_rect(fitz.Rect(x0, y0, x0 + total_w, y0 + h), color=None,
                           fill=(0.93, 0.95, 0.99))
        for r, row in enumerate(rows):
            cx = x0
            for c, cell in enumerate(row):
                page.insert_text((cx + 10, y0 + r * h + 20), cell, fontsize=10.5,
                                 fontname="china-s",
                                 color=(0.09, 0.13, 0.19) if r == 0 else (0.28, 0.33, 0.4))
                cx += widths[c]
        page.insert_text((56, H - 60), f"第 {idx} 页 / 共 5 页", fontsize=9,
                         fontname="china-s", color=(0.6, 0.64, 0.7))

    # ── 最后一页：清单
    page = doc.new_page(width=W, height=H)
    page.insert_text((56, 78), "四、验收清单", fontsize=21, fontname="china-s",
                     color=(0.09, 0.13, 0.19))
    page.draw_line(fitz.Point(56, 96), fitz.Point(150, 96), color=accent, width=2.2)
    items = [
        "打开 100 页文档，滚动流畅无卡顿",
        "选中文字可高亮，批注保存后仍存在",
        "旋转与删除页面后，页码与缩略图同步更新",
        "导出 Word 后，段落与表格结构基本保留",
        "扫描件 OCR 后，全文搜索能命中识别文字",
        "加密后的文件在系统阅读器中需要输入密码",
    ]
    y = 140
    for i, it in enumerate(items, 1):
        page.draw_rect(fitz.Rect(56, y - 11, 68, y + 1), color=(0.6, 0.65, 0.72), width=1)
        page.insert_text((82, y), it, fontsize=12, fontname="china-s", color=(0.22, 0.26, 0.32))
        y += 34

    page.insert_text((56, 430), "备注", fontsize=12, fontname="china-s", color=accent)
    note = ("本工具定位是「够用的本地替代方案」，并不追求在印刷级色彩管理、"
            "PDF/X 认证、CA 数字签名等领域对齐 Acrobat。")
    page.insert_textbox(fitz.Rect(56, 448, W - 56, 520), note, fontsize=11.5,
                        fontname="china-s", color=(0.35, 0.4, 0.47), lineheight=1.7)
    page.insert_text((56, H - 60), "第 5 页 / 共 5 页", fontsize=9, fontname="china-s",
                     color=(0.6, 0.64, 0.7))

    # 书签
    doc.set_toc([
        [1, "封面", 1],
        [1, "一、技术架构", 2],
        [1, "二、性能策略", 3],
        [1, "三、转换能力", 4],
        [1, "四、验收清单", 5],
    ])
    doc.set_metadata({
        "title": "产品需求说明书 — PDF Studio",
        "author": "松子蟹",
        "subject": "桌面端 PDF 工具产品规格",
        "creator": "PDF Studio Demo Builder",
    })
    doc.save(OUT, garbage=3, deflate=True)
    n, size = doc.page_count, OUT.stat().st_size
    doc.close()
    print(f"已生成 {OUT}  ({n} 页, {size} B)")
    return OUT


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
