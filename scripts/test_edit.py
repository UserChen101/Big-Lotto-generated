"""文本编辑内核的可视化验证：生成一张 PNG，肉眼确认方向/字体/删除效果。

    python scripts/test_edit.py
产物：build/edit_probe.png
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402

from app.core import edit  # noqa: E402


def main() -> int:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    # 参考框：每个文本块允许 300x60
    boxes = [
        ("正向 微软雅黑 20pt", (60, 60), "h", "msyh", 20),
        ("反向 ABC 汉字 20pt", (60, 140), "r", "simhei", 20),
        ("竖排 黑体 22pt", (60, 230), "v", "simhei", 22),
        ("竖排 ABC 宋体 16pt", (110, 230), "v", "simsun", 16),
        ("楷体 正向 14pt ABCdef", (60, 520), "h", "simkai", 14),
        ("多行 正向 12pt\n第二行 second", (60, 570), "h", "simfang", 12),
        ("反向多行 14pt\n第二行", (330, 640), "r", "msyh", 14),
    ]

    for label, pos, direction, fid, size in boxes:
        edit._write(page, label, pos, size=size, font=edit._pick(fid), color=(0, 0, 0), direction=direction)
        # 画一个淡框标出锚点（左上角），便于判断是否对齐
        page.draw_rect(fitz.Rect(pos[0], pos[1] - 6, pos[0] + 6, pos[1] + 6), color=(1, 0, 0), width=0.6)

    # 删除测试：在页面底部写一段再删掉中间几个字
    edit._write(page, "删除测试 ABCDEFG 保留", (60, 740), size=18, font=edit._pick("simhei"), color=(0, 0, 0))
    page.draw_rect(fitz.Rect(60, 730, 320, 760), color=(0, 0.6, 0), width=0.4)
    # 只删 "ABCDEFG" 所在的一小段
    edit.delete_rect(page, [130, 733, 215, 758])

    pix = page.get_pixmap(dpi=150)
    out = ROOT / "build" / "edit_probe.png"
    out.parent.mkdir(exist_ok=True)
    pix.save(out)
    print("已生成", out)
    print("剩余文本：", repr(page.get_text("text")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
