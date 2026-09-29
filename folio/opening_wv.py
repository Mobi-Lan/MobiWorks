"""합주 시작 연출 — WebView2 를 **투명 배경**으로 게임 위에 얹는다 (표준 라이브러리 + ctypes 만).

연출은 카드 애니메이션 HTML(`ui/folio/opening2.html`)이고,
미리 그림으로 굽지 않고 **WebView2 가 그 자리에서 그린다.** 게임이 카드 뒤로 그대로 비쳐야 한다.

왜 「컴포지션 호스팅」인가
    WebView2 를 보통 방식(HWND 호스팅)으로 붙이면 웹뷰가 자식 창을 하나 만들어 그린다. 자식 창은
    바탕을 투명하게 해도 **부모 창의 바탕(검정/흰색)이 비칠 뿐** 바탕화면·게임까지는 안 비친다.
    `CreateCoreWebView2CompositionController` 로 만들면 웹뷰는 창 대신 **DirectComposition
    비주얼**에 그리고, 그 비주얼을 `WS_EX_NOREDIRECTIONBITMAP` 창의 컴포지션 대상에 걸면 DWM 이
    픽셀 단위 알파 그대로 합성한다 — 카드 밖은 진짜로 뚫려 게임이 보인다.

구조
    · 자기 스레드 하나 (STA, Win32 메시지 펌프). 웹뷰의 완료 콜백은 전부 이 스레드의 메시지로 온다.
    · 앱이 켜질 때 미리 만들고(숨김) 페이지까지 읽어 둔다 — 첫 연출에 Edge 시동 지연이 없게 (웜 스타트).
    · `play(data)` 는 어느 스레드에서 불러도 된다 — 명령을 큐에 넣고 창 스레드를 깨울 뿐이다.
    · 창은 클릭 관통(`WS_EX_LAYERED|WS_EX_TRANSPARENT`)·활성화 안 함(`WS_EX_NOACTIVATE`)·
      작업 표시줄 없음(`WS_EX_TOOLWINDOW`). 연출하는 6.5초 동안만 보인다.
    · 게임보다만 위 — `opening_tk.keep_above_game` 을 그대로 쓴다 (최상위로 올리지 않는다).

실패하면 `opening_tk` (띠 두 개짜리 tk 연출)로 물러난다. 로그: `[opening] WebView2 … 폴백: tk`.
COM 은 comtypes·pywin32 없이 vtable 을 손으로 부른다. 순번·IID 는 `vendor/WebView2.h`(SDK
1.0.4191.47)와 Windows SDK `dcomp.h` 에서 옮겼다 — `tests/test_opening_wv.py` 가 헤더와 대조한다.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import uuid

OPENING_SEC = 6.0          # 연출 길이 — engine.OPENING_SEC · opening2.html 의 DUR(6000ms)와 같다
HIDE_PAD = 0.5             # 연출이 끝나고 창을 숨기기까지 여유. 퇴장이 T.out 내내 옅어지므로
                           # 마지막 프레임이 잘리지 않게 — 헤드리스 실측으로 페이지의 run() 이 6.09초에 끝났다
KEEP_EVERY = 0.2           # 연출 중 「게임보다 위」를 다시 재는 간격
NAV_RETRY_SEC = 3.0        # 서버가 아직 안 떴을 때 페이지 다시 읽기 간격
NAV_RETRY_MAX = 10
PAGE_PATH = "/folio/opening2.html"

# ── IID ─────────────────────────────────────────────────────────────────────
# WebView2 는 vendor/WebView2.h (MIDL_INTERFACE), DirectComposition 은 dcomp.h (DECLARE_INTERFACE_IID_).
IID = {
    "IUnknown": "00000000-0000-0000-c000-000000000046",
    "ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler": "4e8a3389-c9d8-4bd2-b6b5-124fee6cc14d",
    "ICoreWebView2CreateCoreWebView2CompositionControllerCompletedHandler": "02fab84b-1428-4fb7-ad45-1b2e64736184",
    "ICoreWebView2ExecuteScriptCompletedHandler": "49511172-cc67-4bca-9923-137112f4c4cc",
    "ICoreWebView2NavigationCompletedEventHandler": "d33a35bf-1c49-4f98-93ab-006e0533fe1c",
    "ICoreWebView2Environment3": "80a22ae3-be7c-4ce2-afe1-5a50056cdeeb",
    "ICoreWebView2CompositionController": "3df9b733-b9ae-4a15-86b4-eb9ee9826469",
    "ICoreWebView2Controller": "4d00c0d1-9434-4eb6-8078-8697a560334f",
    "ICoreWebView2Controller2": "c979903e-d4ca-4228-92eb-47ee3fa96eab",
    "ICoreWebView2": "76eceacb-0462-4d94-ac83-423a6793775e",
    "ICoreWebView2Settings": "e562e4f0-d7fa-43ac-8d71-c05150499f00",
    "ICoreWebView2NavigationCompletedEventArgs": "30d68b7d-20d9-4752-a9ca-ec8448fbb5c1",
    # dcomp.h (Windows SDK 10.0.26100). IDCompositionTarget 은 헤더 값을 쓴다.
    # (이 IID 는 QueryInterface 에 쓰지 않아 동작엔 영향이 없다.)
    "IDCompositionDevice": "c37ea93a-e7aa-450d-b16f-9746cb0407f3",
    "IDCompositionTarget": "eacdd04c-117e-4e17-88f4-d1b12b0e3d89",
    "IDCompositionVisual": "4d93059d-097b-4651-9a60-f0f25116e2f3",
}

# ── vtable 순번 (IUnknown 0·1·2 포함) ─────────────────────────────────────────
VT = {
    "IUnknown": {"QueryInterface": 0, "AddRef": 1, "Release": 2},
    "ICoreWebView2Environment3": {"CreateCoreWebView2CompositionController": 9},
    "ICoreWebView2CompositionController": {"put_RootVisualTarget": 4},
    "ICoreWebView2Controller": {"put_IsVisible": 4, "put_Bounds": 6, "Close": 24, "get_CoreWebView2": 25},
    "ICoreWebView2Controller2": {"put_DefaultBackgroundColor": 27},
    "ICoreWebView2": {"get_Settings": 3, "Navigate": 5, "NavigateToString": 6,
                      "add_NavigationCompleted": 15, "ExecuteScript": 29},
    "ICoreWebView2Settings": {"put_AreDefaultScriptDialogsEnabled": 8, "put_IsStatusBarEnabled": 10,
                              "put_AreDevToolsEnabled": 12, "put_AreDefaultContextMenusEnabled": 14,
                              "put_IsZoomControlEnabled": 18},
    "ICoreWebView2NavigationCompletedEventArgs": {"get_IsSuccess": 3, "get_WebErrorStatus": 4},
    "IDCompositionDevice": {"Commit": 3, "CreateTargetForHwnd": 6, "CreateVisual": 7},
    "IDCompositionTarget": {"SetRoot": 3},
}

# ── Win32 상수 ──────────────────────────────────────────────────────────────
WS_POPUP = 0x80000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
EX_STYLE = (WS_EX_NOREDIRECTIONBITMAP | WS_EX_LAYERED | WS_EX_TRANSPARENT
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
LWA_ALPHA = 0x2
WM_DESTROY, WM_NCHITTEST, WM_MOUSEACTIVATE, WM_QUIT, WM_APP = 0x0002, 0x0084, 0x0021, 0x0012, 0x8000
HTTRANSPARENT, MA_NOACTIVATE = -1, 3
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
COINIT_APARTMENTTHREADED = 0x2
OFFSCREEN = -32000
E_NOINTERFACE = -2147467262      # 0x80004002

HERE = os.path.dirname(os.path.abspath(__file__))


def loader_path() -> str:
    """WebView2Loader.dll 자리 — 묶인 exe 는 `_MEIPASS/vendor`, 소스는 저장소의 `vendor/`."""
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(HERE)):
        if base:
            p = os.path.join(base, "vendor", "WebView2Loader.dll")
            if os.path.isfile(p):
                return p
    return ""


def runtime_version(loader: str) -> str:
    """설치된 WebView2 런타임 버전. 없으면 빈 문자열 (로더가 레지스트리를 본다 — 창을 만들지 않는다)."""
    if os.name != "nt" or not loader:
        return ""
    try:
        dll = ctypes.WinDLL(loader)
        f = dll.GetAvailableCoreWebView2BrowserVersionString
        f.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
        f.restype = ctypes.c_long
        out = ctypes.c_void_p()
        if f(None, ctypes.byref(out)) < 0 or not out.value:
            return ""
        try:
            return ctypes.wstring_at(out.value)
        finally:
            ole = ctypes.WinDLL("ole32")
            ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole.CoTaskMemFree(out)
    except Exception:
        return ""


def page_url() -> str:
    """연출 페이지 주소. 모비웍스 서버(`server.py`, exe 에서는 `__main__`)의 포트로 부른다 —
    거기서 내야 net.js 에 실행 토큰이 심어져 악보함(/api/folio/scores)을 읽을 수 있다."""
    for name in ("server", "__main__"):
        m = sys.modules.get(name)
        port = getattr(m, "PORT", None)
        if isinstance(port, int) and port > 0 and hasattr(m, "folio_engine"):
            return f"http://127.0.0.1:{port}{PAGE_PATH}"
    return ""


def user_data_dir() -> str:
    """웹뷰 프로필 자리 — 앱 자료 폴더(%LOCALAPPDATA%\\MobiWorks) 아래."""
    try:
        import store
        return os.path.join(os.path.dirname(store.DATA_DIR), "webview2")
    except Exception:
        return os.path.join(os.environ.get("TEMP") or HERE, "MobiWorks-webview2")


# ── ctypes 로 부르는 COM ────────────────────────────────────────────────────
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def guid(name_or_str: str) -> GUID:
    s = IID.get(name_or_str, name_or_str)
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


class COLOR(ctypes.Structure):          # COREWEBVIEW2_COLOR — 값으로 넘긴다 (4바이트)
    _fields_ = [("A", ctypes.c_ubyte), ("R", ctypes.c_ubyte), ("G", ctypes.c_ubyte), ("B", ctypes.c_ubyte)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class Token(ctypes.Structure):          # EventRegistrationToken
    _fields_ = [("value", ctypes.c_int64)]


def vcall(obj, index: int, argtypes=(), *args, restype=ctypes.c_long):
    """COM 메서드 부르기: obj 의 vtable[index] 를 (this, *args) 로."""
    p = obj.value if isinstance(obj, ctypes.c_void_p) else obj
    if not p:
        raise OSError("null COM pointer")
    vt = ctypes.cast(ctypes.c_void_p(p), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vt[index])
    return fn(ctypes.c_void_p(p), *args)


def check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} 실패 hr=0x{hr & 0xFFFFFFFF:08X}")


def qi(obj, name: str) -> int:
    out = ctypes.c_void_p()
    g = guid(name)
    check(vcall(obj, 0, (ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(g), ctypes.byref(out)),
          f"QueryInterface({name})")
    return out.value or 0


def release(obj) -> None:
    try:
        if obj:
            vcall(obj, 2, restype=ctypes.c_ulong)
    except Exception:
        pass


_QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
# Invoke 모양 — (this, HRESULT, 결과) 또는 (this, sender, args)
INV_RESULT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p)
INV_SCRIPT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_wchar_p)
INV_EVENT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class Handler:
    """IUnknown + Invoke 하나짜리 COM 콜백 객체. 수명은 파이썬이 쥔다 (Release 로 지우지 않는다) —
    웹뷰가 들고 있는 동안 사라지면 안 되므로, 만든 쪽이 끝날 때까지 참조를 들고 있는다."""

    def __init__(self, iid_name: str, proto, fn, log=print):
        self._iid = bytes(guid(iid_name))
        self._unk = bytes(guid("IUnknown"))
        self._fn, self._log, self._name = fn, log, iid_name
        self._refs = 1
        self._cb = (_QI(self._query), _REF(self._addref), _REF(self._release), proto(self._invoke))
        self._vt = (ctypes.c_void_p * 4)(*[ctypes.cast(c, ctypes.c_void_p) for c in self._cb])
        self._obj = (ctypes.c_void_p * 1)(ctypes.addressof(self._vt))
        self.ptr = ctypes.addressof(self._obj)

    def _query(self, this, riid, ppv):
        try:
            g = bytes(riid.contents)
            if g in (self._iid, self._unk):
                ppv[0] = this
                self._refs += 1
                return 0
            ppv[0] = None
        except Exception:
            pass
        return E_NOINTERFACE

    def _addref(self, _this):
        self._refs += 1
        return self._refs

    def _release(self, _this):
        self._refs = max(0, self._refs - 1)
        return self._refs

    def _invoke(self, _this, a, b):
        try:
            self._fn(a, b)
        except Exception as e:           # 콜백 안에서 터지면 웹뷰 쪽으로 새지 않게
            self._log(f"[opening] WebView2 콜백 오류({self._name.split('ICoreWebView2')[-1]}): {type(e).__name__}: {e}")
        return 0


# ── user32 등: 우리 전용 인스턴스 (공용 windll 의 argtypes 를 남과 섞지 않는다 — engine._game_rect 참고) ──
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long), ("lPrivate", ctypes.c_ulong)]


def _libs():
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    o = ctypes.WinDLL("ole32")
    vp, ui, i = ctypes.c_void_p, ctypes.c_uint, ctypes.c_int
    for name, args, res in (
        ("RegisterClassExW", [ctypes.POINTER(WNDCLASSEXW)], ctypes.c_ushort),
        ("UnregisterClassW", [ctypes.c_wchar_p, vp], ctypes.c_int),
        ("CreateWindowExW", [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                             i, i, i, i, vp, vp, vp, vp], vp),
        ("DestroyWindow", [vp], ctypes.c_int),
        ("DefWindowProcW", [vp, ui, ctypes.c_size_t, ctypes.c_ssize_t], ctypes.c_ssize_t),
        ("ShowWindow", [vp, i], ctypes.c_int),
        ("SetWindowPos", [vp, vp, i, i, i, i, ui], ctypes.c_int),
        ("SetLayeredWindowAttributes", [vp, ctypes.c_ulong, ctypes.c_ubyte, ctypes.c_ulong], ctypes.c_int),
        ("PeekMessageW", [ctypes.POINTER(MSG), vp, ui, ui, ui], ctypes.c_int),
        ("TranslateMessage", [ctypes.POINTER(MSG)], ctypes.c_int),
        ("DispatchMessageW", [ctypes.POINTER(MSG)], ctypes.c_ssize_t),
        ("PostMessageW", [vp, ui, ctypes.c_size_t, ctypes.c_ssize_t], ctypes.c_int),
        ("MsgWaitForMultipleObjectsEx", [ctypes.c_ulong, vp, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong], ctypes.c_ulong),
        ("GetSystemMetrics", [i], i),
    ):
        f = getattr(u, name)
        f.argtypes, f.restype = args, res
    k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [ctypes.c_wchar_p], vp
    o.CoInitializeEx.argtypes, o.CoInitializeEx.restype = [vp, ctypes.c_ulong], ctypes.c_long
    o.CoUninitialize.argtypes, o.CoUninitialize.restype = [], None
    return u, k, o


class WebOpening:
    """WebView2 연출. `opening_tk.Opening` 과 같은 얼굴(play·busy·close)을 한다.

    준비가 안 됐거나(웜 스타트 중) 망가졌으면 play 는 tk 연출로 물러난다 (`fallback` 이 만든다).
    `offscreen=True` 는 스모크 검사용 — 창을 화면 밖에 두고 **절대 보이지 않는다.**"""

    def __init__(self, game_rect=None, log=print, url: str = "", data_dir: str = "", loader: str = "",
                 fallback=None, offscreen: bool = False, html: str = ""):
        self._game_rect, self._log = game_rect, log
        self._url, self._html = url, html
        self._data_dir, self._loader = data_dir, loader or loader_path()
        self._fallback_make, self._fallback = fallback, None
        self._offscreen = offscreen
        self._cmds: queue.Queue = queue.Queue()
        self._keep = []                   # 콜백 객체 — 웹뷰가 들고 있는 동안 살아 있어야 한다
        self.hwnd = 0
        self.env = self.comp = self.ctrl = self.ctrl2 = self.core = 0
        self.dev = self.target = self.visual = 0
        self.stage = "new"                # new → window → environment → controller → visual → page → ready | failed
        self.error = ""
        self._ready = threading.Event()   # 페이지까지 읽혔다
        self._settled = threading.Event()  # 준비됐거나 실패했다 (스모크가 기다린다)
        self._stop = False
        self._playing_until = 0.0
        self._keep_at = 0.0
        self._game = 0
        self._pending = None
        self._nav_tries = 0
        self._nav_retry_at = 0.0
        self._last_script = None
        self._thread = None

    # ── 밖에서 부르는 것 ──
    def start(self) -> "WebOpening":
        self._thread = threading.Thread(target=self._run, name="opening-webview2", daemon=True)
        self._thread.start()
        return self

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self.stage == "ready"

    def busy(self) -> bool:
        if self._fallback is not None and self._fallback.busy():
            return True
        return time.monotonic() < self._playing_until

    def play(self, data: dict) -> dict:
        """연출 한 번. 겹치면 앞 것이 끝난 뒤 이어서 (Overlay.play_opening 이 이미 7초 간격을 둔다)."""
        data = dict(data or {})
        if not self.ready or not self.hwnd:
            why = self.error or f"아직 준비 중({self.stage})"
            self._log(f"[opening] WebView2 {why} — 이번 연출은 폴백: tk")
            return self._play_fallback(data)
        self._cmds.put(("play", data))
        self._wake()
        return {"ok": True, "engine": "web"}

    def close(self) -> None:
        self._cmds.put(("close", None))
        self._wake()

    # ── 폴백 ──
    def _play_fallback(self, data: dict) -> dict:
        if self._fallback is None and self._fallback_make:
            try:
                self._fallback = self._fallback_make()
            except Exception as e:
                self._log(f"[opening] tk 연출도 못 만들었습니다: {e}")
                return {"ok": False, "error": "no_opening"}
        if self._fallback is None:
            return {"ok": False, "error": "not_ready"}
        self._fallback.play(data)
        return {"ok": True, "engine": "tk"}

    def _wake(self) -> None:
        try:
            if self.hwnd:
                self._u.PostMessageW(self.hwnd, WM_APP, 0, 0)
        except Exception:
            pass

    def _fail(self, msg: str) -> None:
        self.error = msg
        self.stage = "failed"
        self._log(f"[opening] WebView2 {msg} — 폴백: tk")
        self._settled.set()

    # ── 창 스레드 ──
    def _run(self) -> None:
        try:
            self._u, self._k, self._o = _libs()
        except Exception as e:
            return self._fail(f"Win32 를 못 불렀습니다({e})")
        hr = self._o.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        self._t0 = time.monotonic()
        self._step("창 스레드 시작")
        try:
            self._make_window()
            self._make_environment()
            self._loop()
        except Exception as e:
            self._fail(f"{type(e).__name__}: {e}")
        finally:
            self._teardown()
            if hr >= 0:
                self._o.CoUninitialize()
            self._settled.set()

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT            # 우리 창은 보기만 한다 — 클릭은 뒤(게임)로
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        return self._u.DefWindowProcW(hwnd, msg, wp, lp)

    def _make_window(self) -> None:
        u = self._u
        self._hinst = self._k.GetModuleHandleW(None)
        self._cls = f"MobiWorksOpeningWV{id(self):x}"
        self._proc = _WNDPROC(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = self._hinst
        wc.lpszClassName = self._cls
        if not u.RegisterClassExW(ctypes.byref(wc)):
            raise OSError(f"RegisterClassExW 실패 ({ctypes.get_last_error()})")
        # 처음 자리는 화면 밖 — 보일 때 게임 자리로 옮긴다. 만들 때 WS_VISIBLE 을 주지 않는다.
        h = u.CreateWindowExW(EX_STYLE, self._cls, "MobiWorks Opening", WS_POPUP,
                              OFFSCREEN, OFFSCREEN, 640, 360, None, None, self._hinst, None)
        if not h:
            raise OSError(f"CreateWindowExW 실패 ({ctypes.get_last_error()})")
        self.hwnd = int(h)
        # 레이어드 창은 알파를 한 번 정해 줘야 합성에 들어간다 (255 = 창 전체는 불투명, 픽셀 알파는 DComp 가).
        u.SetLayeredWindowAttributes(self.hwnd, 0, 255, LWA_ALPHA)
        self.stage = "window"

    def _make_environment(self) -> None:
        if not self._loader:
            raise OSError("WebView2Loader.dll 이 없습니다")
        # 만들어지는 순간의 흰 바탕을 막는다 (DefaultBackgroundColor 를 넣기 전 한 프레임)
        os.environ.setdefault("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "0")
        dll = ctypes.WinDLL(self._loader)
        create = dll.CreateCoreWebView2EnvironmentWithOptions
        create.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p]
        create.restype = ctypes.c_long
        h = Handler("ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler", INV_RESULT, self._on_env, self._log)
        self._keep.append(h)
        if self._data_dir:
            os.makedirs(self._data_dir, exist_ok=True)
        self._step("환경 요청 직전")      # create() 가 동기로 오래 막히면 이 줄과 다음 줄 사이가 벌어진다
        check(create(None, self._data_dir or None, None, h.ptr), "CreateCoreWebView2EnvironmentWithOptions")
        self.stage = "environment"
        self._step("환경 요청")

    def _on_env(self, hr, env) -> None:
        try:
            self._on_env_(hr, env)
        except Exception as e:           # 여기서 멎으면 영영 「준비 중」이다 — 실패로 못 박아 tk 로 보낸다
            self._fail(f"{type(e).__name__}: {e}")

    def _step(self, what: str) -> None:
        """부팅 단계마다 걸린 시간을 남긴다 — 배포판에서 「준비됨」이 3분 뒤에 온 적이 있다.
        어느 단계에서 기다렸는지 로그 없이는 알 수 없다."""
        self._log(f"[opening] WebView2 {what} (+{time.monotonic() - getattr(self, '_t0', time.monotonic()):.1f}s)")

    def _on_env_(self, hr, env) -> None:
        self._step(f"환경 완료 hr=0x{hr & 0xFFFFFFFF:08X}")
        check(hr, "환경 만들기")
        self.env = qi(env, "ICoreWebView2Environment3")   # QI 가 AddRef 한다 — 우리 몫
        h = Handler("ICoreWebView2CreateCoreWebView2CompositionControllerCompletedHandler", INV_RESULT,
                    self._on_controller, self._log)
        self._keep.append(h)
        check(vcall(self.env, VT["ICoreWebView2Environment3"]["CreateCoreWebView2CompositionController"],
                    (ctypes.c_void_p, ctypes.c_void_p), ctypes.c_void_p(self.hwnd), ctypes.c_void_p(h.ptr)),
              "CreateCoreWebView2CompositionController")
        self.stage = "controller"

    def _on_controller(self, hr, comp) -> None:
        try:
            self._step(f"컨트롤러 완료 hr=0x{hr & 0xFFFFFFFF:08X}")
            check(hr, "컴포지션 컨트롤러 만들기")
            self.comp = qi(comp, "ICoreWebView2CompositionController")
            self.ctrl = qi(comp, "ICoreWebView2Controller")
            self.ctrl2 = qi(comp, "ICoreWebView2Controller2")
            vc = VT["ICoreWebView2Controller"]
            # 바탕 완전 투명 — 페이지가 칠하지 않은 곳은 게임이 비친다
            check(vcall(self.ctrl2, VT["ICoreWebView2Controller2"]["put_DefaultBackgroundColor"], (COLOR,),
                        COLOR(0, 0, 0, 0)), "put_DefaultBackgroundColor")
            check(vcall(self.ctrl, vc["put_IsVisible"], (ctypes.c_int,), 0), "put_IsVisible")
            check(vcall(self.ctrl, vc["put_Bounds"], (RECT,), RECT(0, 0, 640, 360)), "put_Bounds")
            self._make_visual()
            core = ctypes.c_void_p()
            check(vcall(self.ctrl, vc["get_CoreWebView2"], (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(core)),
                  "get_CoreWebView2")
            self.core = core.value or 0
            self._tune_settings()
            h = Handler("ICoreWebView2NavigationCompletedEventHandler", INV_EVENT, self._on_nav, self._log)
            self._keep.append(h)
            tok = Token()
            check(vcall(self.core, VT["ICoreWebView2"]["add_NavigationCompleted"],
                        (ctypes.c_void_p, ctypes.POINTER(Token)), ctypes.c_void_p(h.ptr), ctypes.byref(tok)),
                  "add_NavigationCompleted")
            self.stage = "page"
            self._step(f"페이지 요청 {self._url or 'string'}")
            self._navigate()
        except Exception as e:
            self._fail(f"{type(e).__name__}: {e}")

    def _make_visual(self) -> None:
        """DirectComposition: 창 → 대상 → 비주얼(루트) ← 웹뷰가 여기에 제 비주얼을 붙인다."""
        dc = ctypes.WinDLL("dcomp")
        dev = ctypes.c_void_p()
        g = guid("IDCompositionDevice")
        hr = -1
        for fname in ("DCompositionCreateDevice2", "DCompositionCreateDevice"):   # 2 는 렌더링 장치 없이도 된다
            f = getattr(dc, fname, None)
            if f is None:
                continue
            f.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
            f.restype = ctypes.c_long
            hr = f(None, ctypes.byref(g), ctypes.byref(dev))
            if hr >= 0 and dev.value:
                break
        check(hr, "DCompositionCreateDevice")
        self.dev = dev.value
        vd = VT["IDCompositionDevice"]
        tgt, vis = ctypes.c_void_p(), ctypes.c_void_p()
        check(vcall(self.dev, vd["CreateTargetForHwnd"], (ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                    ctypes.c_void_p(self.hwnd), 1, ctypes.byref(tgt)), "CreateTargetForHwnd")
        self.target = tgt.value
        check(vcall(self.dev, vd["CreateVisual"], (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(vis)), "CreateVisual")
        self.visual = vis.value
        check(vcall(self.target, VT["IDCompositionTarget"]["SetRoot"], (ctypes.c_void_p,), ctypes.c_void_p(self.visual)),
              "SetRoot")
        check(vcall(self.comp, VT["ICoreWebView2CompositionController"]["put_RootVisualTarget"], (ctypes.c_void_p,),
                    ctypes.c_void_p(self.visual)), "put_RootVisualTarget")
        check(vcall(self.dev, vd["Commit"]), "Commit")
        self.stage = "visual"

    def _tune_settings(self) -> None:
        """우클릭 메뉴·개발자 도구·상태 표시줄·확대 끄기 (클릭은 어차피 안 오지만 단축키가 올 수 있다)."""
        try:
            st = ctypes.c_void_p()
            check(vcall(self.core, VT["ICoreWebView2"]["get_Settings"], (ctypes.POINTER(ctypes.c_void_p),),
                        ctypes.byref(st)), "get_Settings")
            vs = VT["ICoreWebView2Settings"]
            for k in ("put_AreDefaultContextMenusEnabled", "put_AreDevToolsEnabled", "put_IsStatusBarEnabled",
                      "put_IsZoomControlEnabled", "put_AreDefaultScriptDialogsEnabled"):
                vcall(st.value, vs[k], (ctypes.c_int,), 0)
            release(st.value)
        except Exception as e:
            self._log(f"[opening] WebView2 설정 일부를 못 바꿨습니다: {e}")

    def _navigate(self) -> None:
        self._nav_tries += 1
        vcw = VT["ICoreWebView2"]
        if self._html:
            check(vcall(self.core, vcw["NavigateToString"], (ctypes.c_wchar_p,), self._html), "NavigateToString")
        elif self._url:
            check(vcall(self.core, vcw["Navigate"], (ctypes.c_wchar_p,), self._url), "Navigate")
        else:
            check(vcall(self.core, vcw["Navigate"], (ctypes.c_wchar_p,), "about:blank"), "Navigate")

    def _on_nav(self, _sender, args) -> None:
        ok, status = ctypes.c_int(0), ctypes.c_int(0)
        va = VT["ICoreWebView2NavigationCompletedEventArgs"]
        vcall(args, va["get_IsSuccess"], (ctypes.POINTER(ctypes.c_int),), ctypes.byref(ok))
        vcall(args, va["get_WebErrorStatus"], (ctypes.POINTER(ctypes.c_int),), ctypes.byref(status))
        if ok.value:
            if not self._ready.is_set():
                self.stage = "ready"
                self._ready.set()
                self._settled.set()
                self._log(f"[opening] WebView2 준비됨 ({self._url or 'string'})")
            return
        if self._nav_tries < NAV_RETRY_MAX:
            self._nav_retry_at = time.monotonic() + NAV_RETRY_SEC   # 서버가 아직 안 떴을 수 있다
        else:
            self._fail(f"페이지를 못 읽었습니다(WebErrorStatus={status.value})")

    def execute(self, script: str, done=None) -> None:
        """창 스레드에서만. 결과(JSON 글자)는 done(hr, text) 로."""
        def fin(hr, text):
            self._last_script = (hr, text)
            if done:
                done(hr, text)
        h = Handler("ICoreWebView2ExecuteScriptCompletedHandler", INV_SCRIPT, fin, self._log)
        self._keep.append(h)
        if len(self._keep) > 64:            # 끝난 스크립트 콜백은 오래 쥘 필요가 없다
            self._keep = self._keep[:3] + self._keep[-32:]
        check(vcall(self.core, VT["ICoreWebView2"]["ExecuteScript"], (ctypes.c_wchar_p, ctypes.c_void_p),
                    script, ctypes.c_void_p(h.ptr)), "ExecuteScript")

    # ── 연출 ──
    def _area(self):
        try:
            r = self._game_rect() if self._game_rect else None
            if r and r.get("found"):
                return int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"])
            self._log(f"[opening] 게임 창을 못 찾아 화면 전체에 띄웁니다 ({(r or {}).get('reason') or 'found=False'})")
        except Exception as e:
            self._log(f"[opening] game rect failed: {e}")
        return 0, 0, int(self._u.GetSystemMetrics(0)), int(self._u.GetSystemMetrics(1))

    def _do_play(self, data: dict) -> None:
        if time.monotonic() < self._playing_until:
            self._pending = data              # 끝나면 이어서 (가장 최근 것 하나만)
            return
        x, y, w, h = self._area()
        if self._offscreen:
            x, y = OFFSCREEN, OFFSCREEN
        u, vc = self._u, VT["ICoreWebView2Controller"]
        u.SetWindowPos(self.hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        vcall(self.ctrl, vc["put_Bounds"], (RECT,), RECT(0, 0, w, h))
        vcall(self.ctrl, vc["put_IsVisible"], (ctypes.c_int,), 1)
        given = data.get("players") or []
        payload = {"song": str(data.get("song") or ""),
                   "players": [[str(t or ""), str(n or "")] for t, n in given if str(n or "").strip()],
                   "badge": str(data.get("badge") or ""), "kicker": str(data.get("kicker") or ""),
                   "inst": str(data.get("inst") or "")}   # 카드 제목 위 악기 줄 — 페이지에 그대로 넘긴다
        self.execute("playOpening(" + json.dumps(payload, ensure_ascii=True) + ")")
        if not self._offscreen:
            u.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self._keep_above(force=True)
        self._playing_until = time.monotonic() + OPENING_SEC + HIDE_PAD
        self._log(f"[opening] {payload['song']!r} {w}x{h}+{x}+{y} (WebView2) 사람 {len(payload['players'])}명")

    def _keep_above(self, force: bool = False) -> None:
        try:
            import opening_tk
            if not self._game or not opening_tk._u32().IsWindow(self._game):
                self._game = opening_tk.find_game()
            opening_tk.keep_above_game([self.hwnd], self._game, force=force)
        except Exception as e:
            self._log(f"[opening] 게임 위로 못 올렸습니다: {e}")

    def _hide(self) -> None:
        try:
            vcall(self.ctrl, VT["ICoreWebView2Controller"]["put_IsVisible"], (ctypes.c_int,), 0)
        except Exception:
            pass
        self._u.ShowWindow(self.hwnd, SW_HIDE)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._nav_retry_at and now >= self._nav_retry_at and not self._ready.is_set():
            self._nav_retry_at = 0.0
            try:
                self._navigate()
            except Exception as e:
                self._fail(f"{type(e).__name__}: {e}")
        if self._playing_until:
            if now >= self._playing_until:
                self._playing_until = 0.0
                self._hide()
                if self._pending is not None:
                    d, self._pending = self._pending, None
                    self._do_play(d)
            elif not self._offscreen and now - self._keep_at >= KEEP_EVERY:
                self._keep_at = now
                self._keep_above()

    def _loop(self) -> None:
        u, msg = self._u, MSG()
        while not self._stop:
            while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_QUIT:
                    self._stop = True
                    break
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            while True:
                try:
                    cmd, arg = self._cmds.get_nowait()
                except queue.Empty:
                    break
                if cmd == "close":
                    self._stop = True
                elif cmd == "play" and self.ready:
                    try:
                        self._do_play(arg)
                    except Exception as e:
                        self._log(f"[opening] WebView2 연출 실패: {type(e).__name__}: {e}")
                        self._hide()
                elif callable(cmd):
                    cmd()
            if self.stage == "failed":
                break
            self._tick()
            # 연출 중에는 자주(게임 위 지키기), 쉴 때는 드물게 깬다 — 명령은 PostMessage 로 바로 깨운다
            busy = self._playing_until or self._nav_retry_at or not self._ready.is_set()
            u.MsgWaitForMultipleObjectsEx(0, None, 30 if busy else 250, QS_ALLINPUT, MWMO_INPUTAVAILABLE)

    def _teardown(self) -> None:
        if self.ctrl:
            try:
                vcall(self.ctrl, VT["ICoreWebView2Controller"]["Close"])
            except Exception:
                pass
        for name in ("core", "ctrl2", "ctrl", "comp", "env", "visual", "target", "dev"):
            release(getattr(self, name))
            setattr(self, name, 0)
        if self.hwnd:
            try:
                self._u.DestroyWindow(self.hwnd)
                self._u.UnregisterClassW(self._cls, self._hinst)
            except Exception:
                pass
            self.hwnd = 0
        self._ready.clear()


_SHARED = None   # 프로세스에 웹 연출은 하나 — 엔진이 시작 때 만들고, 오버레이 창은 그것을 같이 쓴다


def make_opening(root, game_rect=None, log=print):
    """시작 연출 하나를 만든다 — 설정 `opening_engine` 이 "web"(기본)이고 WebView2 가 되면 웹 연출,
    아니면 예전 tk 연출(`opening_tk.Opening`). **화면(tk) 스레드에서 부른다** (tk 연출이 그 스레드에 산다).

    `root=None` 이면 웹 연출만 시도한다 (tk 연출은 오버레이 창이 있어야 한다) — 엔진이 앱 시작 때
    오버레이와 무관하게 부른다. 예전에는 연출이 오버레이 밴드 안에서만 만들어져서, 밴드를 켜 두지
    않으면 **연출이 아예 없었다** (배포판 실측: 「오버레이 켜기」를 누른 뒤에야 준비됨)."""
    global _SHARED
    import opening_tk

    if _SHARED is not None and getattr(_SHARED, "stage", "") != "failed":
        return _SHARED

    def tk_opening():
        if root is None:
            log("[opening] tk 연출은 오버레이 창이 있어야 합니다 — 지금은 만들지 않습니다")
            return None
        return opening_tk.Opening(root, game_rect, log)

    try:
        import store
        mode = str(store.get_settings().get("opening_engine") or "web")
    except Exception:
        mode = "web"
    if mode != "web":
        return tk_opening()
    why = ""
    loader = loader_path()
    url = page_url()
    if os.name != "nt":
        why = "은 윈도우에서만 됩니다"
    elif not loader:
        why = "로더(vendor/WebView2Loader.dll)가 없습니다"
    elif not runtime_version(loader):
        why = "런타임이 설치돼 있지 않습니다"
    elif not url:
        why = "페이지를 낼 서버를 못 찾았습니다"
    if why:
        log(f"[opening] WebView2 {why} — 폴백: tk")
        return tk_opening()
    try:
        _SHARED = WebOpening(game_rect, log, url=url, data_dir=user_data_dir(), loader=loader,
                             fallback=tk_opening).start()
        return _SHARED
    except Exception as e:
        log(f"[opening] WebView2 를 못 띄웠습니다({type(e).__name__}: {e}) — 폴백: tk")
        return tk_opening()


def smoke(timeout: float = 12.0) -> dict:
    """화면에 **아무것도 안 띄우고** 환경·컴포지션 컨트롤러·비주얼·페이지가 만들어지는지만 본다.
    창은 (-32000,-32000) 에 있고 ShowWindow 를 한 번도 부르지 않는다. 15초 안에 부순다.
    `MOBIW_WV_SMOKE=1` 일 때만 돈다."""
    if os.environ.get("MOBIW_WV_SMOKE") != "1":
        return {"ok": False, "error": "MOBIW_WV_SMOKE=1 이 아니라 돌리지 않습니다"}
    t0 = time.monotonic()
    loader = loader_path()
    ver = runtime_version(loader)
    lines = []
    here = os.path.dirname(HERE)
    try:
        with open(os.path.join(here, "ui", "folio", "opening2.html"), encoding="utf-8") as f:
            html = f.read()
    except OSError:
        html = "<html><body></body></html>"
    data = os.path.join(os.environ.get("TEMP") or here, "mobiw-wv-smoke")
    w = WebOpening(None, lines.append, data_dir=data, loader=loader, offscreen=True, html=html).start()
    w._settled.wait(timeout)
    probe = None
    if w.ready:
        box = threading.Event()
        res = {}

        def ask():
            w.execute("typeof playOpening + '|' + getComputedStyle(document.body).backgroundColor + '|' + "
                      "String(document.getElementById('panel')) + '|' + document.querySelectorAll('.card').length",
                      lambda hr, text: (res.update(hr=hr, text=text), box.set()))
        w._cmds.put((ask, None))
        w._wake()
        box.wait(max(0.5, timeout - (time.monotonic() - t0)))
        probe = res.get("text")
    out = {"ok": w.ready, "loader": loader, "runtime": ver, "stage": w.stage, "error": w.error,
           "window": bool(w.hwnd), "environment": bool(w.env), "controller": bool(w.comp and w.ctrl2),
           "visual": bool(w.dev and w.target and w.visual), "page_probe": probe,
           "seconds": round(time.monotonic() - t0, 2), "log": lines}
    w.close()
    if w._thread:
        w._thread.join(3.0)
    out["closed"] = not (w._thread and w._thread.is_alive())
    return out


if __name__ == "__main__":
    print(json.dumps(smoke(), ensure_ascii=False, indent=1))
