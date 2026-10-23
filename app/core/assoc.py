"""Windows 文件关联：把页匠设为 .pdf 的默认打开工具。

设计约束
--------
* 全部写 HKCU，不需要管理员权限。
* Windows 8+ 对 FileExts\\.pdf\\UserChoice 有哈希保护，第三方无法直接写入。
  正确姿势是：
  1) 注册 ProgId + Capabilities + RegisteredApplications（让自己出现在
     系统的「默认应用」列表里）；
  2) 尝试通过 COM 接口 IApplicationAssociationRegistration::SetAppAsDefault
     直接设置（注册过 Capabilities 的应用对自己名下的关联通常可以成功）；
  3) 若被系统拒绝（不同 Windows 版本策略不同），回退到打开系统设置页，
     由用户点一下「设为默认」完成最后一步。
* UserChoice 是否真的切过来，以读回注册表为准，不信 COM 返回值。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

PROG_ID = "PageSmith.pdf"           # ProgId：不能有点分混淆，惯例 <app>.<ext>
APP_REG_NAME = "PageSmith"          # RegisteredApplications 里的键名（保持 ASCII，
                                    # 也是 ms-settings 深链 ?registeredApp= 的参数）
DISPLAY_NAME = "页匠 PageSmith"      # 系统设置里展示的应用名
FILE_DESC = "页匠 PageSmith PDF 文档"

_USERCHOICE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice"
_CAPABILITIES = r"Software\PageSmith\Capabilities"


# ---------------------------------------------------------------- 命令构造

def open_command() -> str:
    """双击 PDF 时实际执行的命令行。源码运行时也支持（pythonw 免黑窗）。"""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" "%1"'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    if pyw.exists():
        py = pyw
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{py}" "{main_py}" "%1"'


def icon_ref() -> str | None:
    """DefaultIcon；源码模式没有打包图标就返回 None（不写这个键）。"""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}",0'
    return None


# ---------------------------------------------------------------- 注册表

def register() -> None:
    """把页匠注册进系统的「默认应用」候选列表（HKCU，免管理员）。"""
    import winreg

    # ProgId：定义“用页匠打开 .pdf”这件事本身
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}") as k:
        _set(k, "", FILE_DESC)
        _set(k, "FriendlyTypeName", FILE_DESC)
    icon = icon_ref()
    if icon:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\DefaultIcon") as k:
            _set(k, "", icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command") as k:
        _set(k, "", open_command())

    # Capabilities：让页匠出现在 设置→默认应用 的应用列表里
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CAPABILITIES) as k:
        _set(k, "ApplicationName", DISPLAY_NAME)
        _set(k, "ApplicationDescription",
             "轻量级 PDF 全能工具箱：阅读批注、文字编辑、格式转换、OCR、水印、压缩加密。")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{_CAPABILITIES}\FileAssociations") as k:
        _set(k, ".pdf", PROG_ID)

    # RegisteredApplications：把 Capabilities 挂到系统注册表
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications") as k:
        _set(k, APP_REG_NAME, _CAPABILITIES)

    _notify_shell()


def unregister() -> None:
    """移除注册（UserChoice 由系统管理无法直接清，ProgId 消失后系统会自动回退）。"""
    import winreg

    for sub in (rf"Software\Classes\{PROG_ID}", _CAPABILITIES):
        _delete_tree(sub)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_REG_NAME)
    except FileNotFoundError:
        pass
    _notify_shell()


def is_registered() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command"):
            return True
    except FileNotFoundError:
        return False


def is_default() -> bool:
    """读 UserChoice，以系统实际状态为准。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USERCHOICE_KEY) as k:
            val, _ = winreg.QueryValueEx(k, "ProgId")
            return val == PROG_ID
    except (FileNotFoundError, OSError):
        return False


def _set(key, name: str, value: str) -> None:
    import winreg
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(subkey: str) -> None:
    import winreg

    def _del_recursive(root, path: str) -> None:
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        child = winreg.EnumKey(k, 0)
                    except OSError:
                        break
                    _del_recursive(root, rf"{path}\{child}")
                winreg.DeleteKey(k, "")
        except FileNotFoundError:
            pass

    _del_recursive(winreg.HKEY_CURRENT_USER, subkey)


def _notify_shell() -> None:
    """SHChangeNotify(SHCNE_ASSOCCHANGED)：让资源管理器立即感知关联变化。"""
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED, SHCNF_IDLIST
    except Exception:
        pass


# ---------------------------------------------------------------- 设置默认

def try_set_default() -> bool:
    """尝试通过 COM 直接设为默认；成功与否以回读 UserChoice 为准。

    实测（Windows 11）：SetAppAsDefault 可能返回 S_OK 但 UserChoice
    纹丝不动（微软对桌面应用的静默 no-op，Chrome/Firefox 同样受限），
    且不同版本对 ASSOCIATIONTYPE 的接受度不一。因此逐一尝试、
    每次都回读注册表验证；全部无效则返回 False，由前端引导用户
    在系统设置页完成最后一步。
    """
    if not is_registered():
        register()
    for at_type in (3, 0):
        try:
            _com_set_app_as_default(APP_REG_NAME, ".pdf", at_type)
        except Exception:
            continue
        if is_default():
            return True
    return False


def settings_url() -> str:
    """系统默认应用设置页；Win11 支持深链直达本应用，Win10 会打开总页。"""
    return f"ms-settings:defaultapps?registeredApp={APP_REG_NAME}"


# ---------------------------------------------------------------- COM

_CLSID_APPASSOC = "{591209c7-767b-42b2-9fba-44ee4615f2c7}"
_IID_APPASSOC = "{4e530b0a-e611-4c77-a3ac-9031d022281b}"
# 文档值 AT_FILEEXTENSION=3；实测部分 Win11 构建 AT=0 才接受扩展名参数。
# 两者都试，以 UserChoice 回读为准（见 try_set_default）。
_AT_FILEEXTENSION_CANDIDATES = (3, 0)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _com_set_app_as_default(app_name: str, extension: str, at_type: int) -> None:
    """IApplicationAssociationRegistration::SetAppAsDefault（vtable 槽位 6）。"""
    ole32 = ctypes.WinDLL("ole32")
    hr_init = ole32.CoInitialize(None)
    try:
        clsid = _GUID()
        iid = _GUID()
        if ole32.CLSIDFromString(ctypes.c_wchar_p(_CLSID_APPASSOC), ctypes.byref(clsid)) != 0:
            raise OSError("CLSIDFromString failed")
        if ole32.IIDFromString(ctypes.c_wchar_p(_IID_APPASSOC), ctypes.byref(iid)) != 0:
            raise OSError("IIDFromString failed")

        ppv = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1,  # CLSCTX_INPROC_SERVER
            ctypes.byref(iid), ctypes.byref(ppv),
        )
        if hr != 0 or not ppv.value:
            raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")

        vtbl = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p)).contents
        funcs = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p * 9)).contents
        proto = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int
        )
        set_default = proto(funcs[6])
        hr = set_default(ppv.value, app_name, extension, at_type)
        if hr != 0:
            raise OSError(f"SetAppAsDefault failed: 0x{hr & 0xFFFFFFFF:08X}")
    finally:
        if hr_init == 0:  # 只有本函数成功 Initialize 才需要配对 Uninitialize
            ole32.CoUninitialize()
