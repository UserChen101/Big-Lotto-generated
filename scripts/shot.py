"""抓取应用窗口截图（开发期验界面用，不参与打包）。

用法：
    python scripts/shot.py build/shot.png [窗口标题关键字]

只依赖 Pillow 的 ImageGrab，不需要 pywin32 截图依赖。
注意 EnumWindows 系列函数必须声明 argtypes，否则 64 位下句柄被截断，
一个窗口都枚举不到。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

user32 = ctypes.WinDLL("user32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL


def find_window(keyword: str) -> tuple[int, str] | None:
    found: list[tuple[int, str]] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if keyword.lower() in title.lower():
            rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w > 200 and h > 200:
                    found.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found[0] if found else None


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/shot.png")
    keyword = sys.argv[2] if len(sys.argv) > 2 else "PDF Studio"
    hit = find_window(keyword)
    if not hit:
        print(f"[shot] 未找到标题含 {keyword!r} 的可见窗口")
        return 1
    hwnd, title = hit
    user32.SetForegroundWindow(hwnd)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    box = (rect.left, rect.top, rect.right, rect.bottom)
    out.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=box, all_screens=True).save(out)
    print(f"[shot] {title!r} {box} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
