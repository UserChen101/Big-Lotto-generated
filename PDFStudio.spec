# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

为什么用 onedir 而不是 onefile
------------------------------
本应用带着 onnxruntime / opencv / pymupdf 这些重型依赖，产物 150MB+。
onefile 每次启动都要把整包解压到临时目录，冷启动会多等好几秒；
PDF 阅读器是高频打开的软件，这个代价不值得。onedir 一次解压即可，
要分发时直接压成 zip，或用 Inno Setup 做安装包。

构建
----
    python -m PyInstaller PDFStudio.spec --noconfirm
产物：dist/PDFStudio/PDFStudio.exe
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve()

datas = [
    # 界面资源必须整目录带上：index.html + css + js + pdf.js（含 cmaps/字体）
    (str(ROOT / "app" / "web"), "app/web"),
]
binaries = []
hiddenimports = [
    # pywebview 的平台后端是按需 import 的，静态分析看不到
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    # pywebview 通过 pythonnet 调 WebView2
    "clr_loader",
    "pythonnet",
    # docx2pdf 走 COM，只有运行时才 import win32com
    "win32com",
    "win32com.client",
    "pythoncom",
]

# 这几个包的资源（模型文件、模板、字体）散落在包目录里，静态分析抓不到
for pkg in ("rapidocr_onnxruntime", "pdf2docx", "pymupdf"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# collect_all 会把整包都拖进来，其中相当一部分本应用根本不用：
#   pymupdf/layout      50MB  ONNX 版面分析模型（我们只做读写，不做版面重建）
#   pymupdf/mupdf-devel  7MB  C 头文件
#   opencv_videoio_ffmpeg 30MB 视频解码（pdf2docx 只吃静态图）
#   pymupdf4llm / pymupdf_layout  Markdown 转换，未使用
_DROP = (
    "pymupdf/layout", "pymupdf\\layout",
    "mupdf-devel",
    "pymupdf4llm",
    "pymupdf_layout",
    "opencv_videoio_ffmpeg",
)


def _keep(entry) -> bool:
    name = str(entry[0]).replace("\\", "/").lower()
    return not any(k.replace("\\", "/").lower() in name for k in _DROP)


datas = [d for d in datas if _keep(d)]
binaries = [b for b in binaries if _keep(b)]

# 关键：hiddenimports 也得过滤。pymupdf/__init__.py 里是
#     try: import pymupdf.layout
#     except ImportError: pass
# 只删资源不删模块，就会 import 成功但找不到模型，抛的是 FileNotFoundError
# 而不是 ImportError —— 兜不住，直接崩在 import pymupdf 上。
_DROP_MODULES = ("pymupdf.layout", "pymupdf_layout", "pymupdf4llm")
hiddenimports = [
    h for h in hiddenimports if not any(h == m or h.startswith(m + ".") for m in _DROP_MODULES)
]

excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "notebook",
    "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "setuptools", "pip",
    # pymupdf 2.0 自带版面分析（50MB ONNX 模型），本应用不做版面重建，
    # 连模块一起排掉，见上面 _DROP_MODULES 的说明
    "pymupdf.layout", "pymupdf_layout", "pymupdf4llm",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDFStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 桌面软件，不要黑框
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDFStudio",
)
