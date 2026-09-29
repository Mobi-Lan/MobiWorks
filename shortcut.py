# -*- coding: utf-8 -*-
r"""임베디드 판의 바로가기 (1.0.1) — 시작 메뉴 「모비웍스」 · 바탕화면 바로가기.

`.cmd` 실행기에는 아이콘을 붙일 수 없다. 그래서 앱이
켜질 때마다 **시작 메뉴 바로가기(.lnk)** 를 만들거나 고쳐 둔다 — 아이콘은 `app\ui\icon.ico`. 대상은 PSF 가
서명한 `python\pythonw.exe`, 인자는 `"<app>\server.py"`, 시작 폴더는 `app\`. `.lnk` 에는 환경변수를 실을 수
없으므로 `runmode.layout_embed` 가 zip 의 짜임으로 임베디드 판임을 안다.

폴더를 옮기면 다음 실행(옮긴 곳의 `MobiWorks.cmd`)이 바로가기를 새 경로로 다시 쓴다. 같은 내용이면 다시
쓰지 않는다 — 마지막으로 쓴 내용을 자료 폴더의 `shortcut.json` 에 적어 두고 비교한다.

**PowerShell 을 띄우지 않는다** — `WScript.Shell` 을 부르는 한 줄짜리 PowerShell 이 가장 짧지만, 앱이 숨은
PowerShell 을 띄우는 것은 백신 휴리스틱이 싫어하는 모양이다 (이 판을 만든 까닭이 Defender 오탐이다).
그래서 `IShellLinkW`·`IPersistFile` 을 ctypes 로 직접 부른다 (vtable 순서는 ShObjIdl.h · ObjIdl.h).

검사에서는 COM 을 부르지 않는다 — `spec()` 은 순수 함수이고, 쓰는 자리(`write_lnk`)는 가짜로 바꾼다.
`MOBIW_SHORTCUT_DIR` 는 **개발·검사 전용**이다 (배포판은 MOBIW_DEV=1 이 함께 있을 때만 — 다른 개발용 변수와
같은 규칙). 그 폴더 아래 `Start Menu`·`Desktop` 으로 바꿔 써서 실제 시작 메뉴·바탕화면을 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
import sys

import runmode

NAME = "모비웍스"
LNK = NAME + ".lnk"
DESCRIPTION = "모비웍스 - 마비노기 모바일 팬 프로젝트"   # 설명 칸은 기호가 깨질 수 있어 ASCII 기호만
DIR_ENV = "MOBIW_SHORTCUT_DIR"

# SHGetKnownFolderPath 의 폴더 — 바탕화면은 OneDrive 로 옮겨져 있을 수 있어 %USERPROFILE%\Desktop 을 가정하지 않는다
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"   # %APPDATA%\Microsoft\Windows\Start Menu\Programs
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"

CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"


def spec(executable: str | None = None, app_dir: str | None = None) -> dict:
    r"""바로가기 내용 — 순수 함수 (파일을 만들지 않는다).

    콘솔 창이 뜨지 않게 대상은 언제나 `pythonw.exe` 다 (`python.exe` 로 떠 있어도 옆의 `pythonw.exe` 로 바꾼다).
    인자는 따옴표로 감싼다 — 경로에 공백·한글이 있어도 된다."""
    exe = os.path.abspath(executable or sys.executable)
    app = os.path.abspath(app_dir or runmode.HERE)
    target = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return {
        "target": target,
        "args": '"%s"' % os.path.join(app, "server.py"),
        "workdir": app,
        "icon": os.path.join(app, "ui", "icon.ico"),
        "description": DESCRIPTION,
    }


def _dev_dir() -> str:
    d = os.environ.get(DIR_ENV) or ""
    if d and (not runmode.release() or os.environ.get("MOBIW_DEV") == "1"):
        return d
    return ""


def _known_folder(fid: str) -> str:
    import ctypes
    from ctypes import wintypes
    guid = _guid(fid)
    out = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath 0x{hr & 0xFFFFFFFF:08X}")
    try:
        return out.value or ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def start_menu_dir() -> str:
    d = _dev_dir()
    return os.path.join(d, "Start Menu") if d else _known_folder(FOLDERID_PROGRAMS)


def desktop_dir() -> str:
    d = _dev_dir()
    return os.path.join(d, "Desktop") if d else _known_folder(FOLDERID_DESKTOP)


def _guid(s: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    h = s.strip("{}").replace("-", "")
    g = GUID()
    g.d1, g.d2, g.d3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
    for i in range(8):
        g.d4[i] = int(h[16 + 2 * i:18 + 2 * i], 16)
    return g


def write_lnk(path: str, sp: dict) -> None:
    """`IShellLinkW` 로 .lnk 를 쓴다 (ctypes · comtypes 없이). 실패하면 OSError."""
    import ctypes
    from ctypes import wintypes
    HRESULT = ctypes.c_long
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    hr = ole32.CoInitializeEx(None, 0x2)             # COINIT_APARTMENTTHREADED
    inited = hr in (0, 1)                             # S_OK · S_FALSE (RPC_E_CHANGED_MODE 면 이미 다른 방식으로 초기화됨 — 그대로 쓴다)

    def call(obj, index, *args, argtypes=()):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
        r = fn(obj, *args)
        if r < 0:
            raise OSError(f"COM 0x{r & 0xFFFFFFFF:08X} (vtable {index})")
        return r

    link = ctypes.c_void_p()
    pf = ctypes.c_void_p()
    try:
        clsid, iid_link, iid_pf = _guid(CLSID_SHELLLINK), _guid(IID_ISHELLLINKW), _guid(IID_IPERSISTFILE)
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1, ctypes.byref(iid_link), ctypes.byref(link))   # CLSCTX_INPROC_SERVER
        if hr < 0:
            raise OSError(f"CoCreateInstance(ShellLink) 0x{hr & 0xFFFFFFFF:08X}")
        w = wintypes.LPCWSTR
        call(link, 20, sp["target"], argtypes=(w,))                       # SetPath
        call(link, 11, sp["args"], argtypes=(w,))                         # SetArguments
        call(link, 9, sp["workdir"], argtypes=(w,))                       # SetWorkingDirectory
        call(link, 17, sp["icon"], 0, argtypes=(w, ctypes.c_int))         # SetIconLocation
        call(link, 7, sp.get("description", ""), argtypes=(w,))           # SetDescription
        call(link, 0, ctypes.byref(iid_pf), ctypes.byref(pf), argtypes=(ctypes.c_void_p, ctypes.c_void_p))   # QueryInterface
        os.makedirs(os.path.dirname(path), exist_ok=True)
        call(pf, 6, path, 1, argtypes=(w, wintypes.BOOL))                 # IPersistFile::Save
    finally:
        for obj in (pf, link):
            if obj.value:
                try:
                    call(obj, 2)                                          # Release
                except OSError:
                    pass
        if inited:
            ole32.CoUninitialize()


def _state_path(base: str) -> str:
    return os.path.join(base, "shortcut.json")


def ensure_start_menu(base: str, writer=None) -> dict:
    """시작 메뉴 「모비웍스」를 만들거나 고친다 (멱등 · 묻지 않는다).

    같은 내용으로 이미 써 두었고 파일도 있으면 아무것도 하지 않는다. 폴더를 옮겼거나 누가 지웠으면 다시 쓴다.
    `base` 는 자료 폴더 — 마지막으로 쓴 내용을 거기 적는다."""
    writer = writer or write_lnk
    sp = spec()
    path = os.path.join(start_menu_dir(), LNK)
    want = dict(sp, path=path)
    try:
        with open(_state_path(base), encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = None
    if last == want and os.path.isfile(path):
        return {"ok": True, "changed": False, "path": path}
    writer(path, sp)
    try:
        os.makedirs(base, exist_ok=True)
        with open(_state_path(base), "w", encoding="utf-8") as f:
            json.dump(want, f, ensure_ascii=False)
    except OSError:
        pass                        # 적어 두지 못하면 다음에 한 번 더 쓸 뿐이다
    return {"ok": True, "changed": True, "path": path}


def make_desktop(writer=None) -> dict:
    """바탕화면에 같은 바로가기를 만든다 (설정 → 일반의 단추). 있으면 새 경로로 덮는다."""
    writer = writer or write_lnk
    path = os.path.join(desktop_dir(), LNK)
    writer(path, spec())
    return {"ok": True, "path": path}
