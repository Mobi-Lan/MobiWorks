"""경량판·미니판용 게임 위 오버레이 — 표준 라이브러리만 (tkinter + ctypes).

정식판은 일렉트론의 투명 BrowserWindow 를 쓰지만, 경량판에는 일렉트론이 없다(Edge 앱 창은 투명·항상 위·클릭 관통이 안 된다).
그래서 백엔드 프로세스 안에서 tkinter 창을 직접 그리고, Win32 확장 스타일로 항상 위·클릭 관통을 건다.

표시는 연주 관련만: 지금 연주 중인 곡·악기·진행. (주변 연주 표시는 걷어냈다.)
단축키(창 포커스와 무관, GetAsyncKeyState 폴링): Shift+F1 보이기/숨기기 · Shift+F10 클릭 관통
· Shift+F2 위치 잠금 · Shift+F12 앞으로.
창 위치는 <데이터폴더>\\overlay.json 에 기억한다 (정식판과 같은 파일, 같은 키).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import math
import os
import queue
import random
import sys
import threading
import time

import library                       # 악기 이름에서 종류를 뽑을 때 쓴다 (inst_kind)
import inst_picker                   # 악기 고르기의 종류 탭·검색 규칙 (창 없이 테스트한다)
import runmode                       # 배포판(exe·임베디드 파이썬)은 개발용 변수를 무시한다

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080      # 작업 표시줄·Alt+Tab 에 안 뜨게
_WS_EX_NOACTIVATE = 0x08000000      # 눌러도 게임에서 포커스를 뺏지 않게
# 단축키 — Shift 와 함께 눌러 게임 조작과 겹치지 않게 한다.
_VK_SHIFT = 0x10
_KEYS = {"show": 0x70, "lock": 0x71, "through": 0x79, "front": 0x7B}   # F1 F2 F10 F12
_GA_ROOT = 2                        # Tk 의 winfo_id() 는 자식 창 — 진짜 최상위 창을 얻는다

# 「MobiScore Overlay」 디자인 값. 반투명은 창 전체 불투명도로 내고, 선·글자는 바탕에 얹은 불투명색으로 쓴다.
BAND = "#090c11"                      # rgba(9,12,17,.82)
GOLD = "#e2b866"
HOVER_WASH, HOVER_ALPHA = "#000000", 54   # 롤오버 — 흰색으로 밝히지 않고 바탕보다 조금 어둡게
GOLD_HI = "#f0d08a"
GOLD_DIM = "#8a6f3a"
TITLE_FG = "#f4e7c8"
ARTIST_FG = "#9aa3b2"
TIME_FG = "#c9d0da"
HANDLE = "#4b5665"
CARET = "#6f7a8a"
UNKNOWN = "#f0a35a"
UNKNOWN_TITLE = "#f6c48a"
CHIP_LINE = "#343b46"
FOOT = "#6f7a8a"
# 밴드 자동 배치 — 모비웍스와 같은 값 (디자인 캔버스 1280x780 기준, 클라이언트 크기에 비례 환산)
DESIGN_W, DESIGN_H = 1280, 780
DESIGN_TOP = 14                 # 밴드 윗변
DESIGN_SAFE = (240, 1002)       # 채팅을 펼쳐도 안 겹치는 상단 가로 구역
# 밴드는 게임 화면 오른쪽 아래에 가장자리 간격을 두고 붙인다.
# 예전에는 게임의 둥근 정지 버튼 위에 우리 원을 정확히 겹쳐 「알약이 버튼 뒤로 물린」 모양을 냈는데,
# 지금은 그 원이 **우리 정지 단추**다 — 겹쳐 두면 게임 버튼이 클릭을 못 받는다.
DESIGN_VIEW_W = 1098            # 디자인의 게임 화면 폭 — 밴드 크기는 이 비율로 환산한다
DESIGN_EDGE = 16                # 게임 화면 가장자리에서 띄우는 간격 (아래·오른쪽 같게)
# 게임 버튼 뒤로는 못 들어가므로, 같은 버튼을 우리가 그려 그 위에 정확히 겹친다 —
# 그래야 알약이 버튼 뒤로 물려 들어간 모양이 나온다.
DESIGN_BTN = 84                 # 원 지름
DESIGN_SQ = 30                  # 원 안의 라운드 사각
DESIGN_OVERLAP = 96             # 원이 카드 오른쪽 끝에서 안으로 들어오는 깊이

# 알약 위로 펼쳐지는 상세창 (목록 + 지금 곡)
DESIGN_PANEL_W = 534            # 디자인 가로
# 카드 윗변의 「▴ 재생목록 / ▴ 주변 연주」 띠 — 높이 26px (예전 20 에서 커졌다).
DESIGN_STRIP = 26
# 띠 아래 세 줄 (padding + 내용): 제목줄 11+19+10 · 진행줄 24+13 · 다음곡줄 9+18+11
DESIGN_CARD_ROWS = (40, 37, 38)
DESIGN_ROW = 38                 # 목록 한 줄
DESIGN_RADIUS = 14              # 상세창(펼침 패널) 곡률 14px
DESIGN_CARD_RADIUS = 20         # 접힌 카드 곡률 20px (패널 14 와 다르다)
DESIGN_GAP = 5                  # 상세창과 밴드 사이의 틈
DESIGN_CHIPS = 38               # 필터 알약 줄
DESIGN_TOPBAR = 38              # 위치 고정·스마트 관통·초기화·악기·장착
DESIGN_TABS = 32                # 재생목록 / 주변 연주 탭 띠
DESIGN_FOOT = 38                # 손잡이·접기·제목·시간·창 열기
CLOSE_H = 26                    # 맨 위에 떠 있는 「전체닫기」 알약 높이
DESIGN_SIDE = 212               # 오른쪽 기둥
PANEL_ROWS = 6                  # 한 번에 보이는 줄 수
_DRAG_MIN = 4                   # 이만큼 움직여야 「끌었다」로 본다 (누를 때의 손 떨림 무시)
P_BG = "#0a0f17"                # rgba(11,16,24,.92)
P_LINE = "#23303f"
P_DIV = "#16202c"
P_ROWLINE = "#131c27"
P_INK = "#dce3ea"
P_SUB = "#8b97a6"
P_SIDE_BG = "#141a1f"           # rgba(226,184,102,.05) 를 판 바탕에 얹은 값
P_CHIP_FG = "#c2cad4"
P_SEL_BG = "#161f2b"
P_TRACK = "#1e2a38"              # 진행 막대 바탕 (--track)
PCARD_BG = "#0d1420"            # rgba(13,20,32,.88)
PCARD_LINE = "#23303f"
PSTRIP_BG = "#131c27"
PSTRIP_LINE = "#23303f"
PSTRIP_ON = "#282c2d"           # 밴드 윗변에서 지금 고른 탭 = 띠 바탕 + 금색 10% (rgba(226,184,102,.1))
PGROOVE_BG = "#23303f"
ON_GOLD = "#1a1608"             # 골드 원 안의 사각

# 「MobiScore Player」 1a 패널 — 밴드와 별개 창
PCARD = "#0c1412"                     # rgba(12,20,18,.86)
PLINE = "#2f5d49"                     # rgba(95,207,158,.4) on card
MINT = "#5fcf9e"
MINT_SOFT = "#9ec9b8"
PTIME = "#c9d6d0"
PGROOVE = "#33403c"
# 상태별 테두리색 (디자인의 rgba 를 밴드 바탕에 얹은 값)
B_PLAY = "#5f5133"
B_START = "#c19e59"
B_IDLE = "#2b2d31"
B_UNKNOWN = "#7c5735"
# 경고 알약 (탈것 탑승 중) — 미니 창의 --pink(#ff6f9c) 와 같은 색, 바탕은 그 색을 카드 바탕에 옅게 얹은 값
WARN_PINK = "#ff6f9c"
WARN_BG = "#2a1826"
FONT = "Malgun Gothic"
MONO = "Consolas"


def card_height() -> int:
    """접힌 카드의 디자인 높이 (배율 전) — 띠 + 세 줄: 26 + 40 + 37 + 38 = 141.
    한 곳에서만 더한다 — 그려지는 카드와 검사가 같은 수를 봐야 한다."""
    return DESIGN_STRIP + sum(DESIGN_CARD_ROWS)


def _mix(a: str, b: str, t: float) -> str:
    """색 a 에서 b 로 t 만큼 — 깜박임처럼 밝기만 바꿀 때 쓴다."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    ca = (int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16))
    cb = (int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16))
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ca, cb))


def _fmt(sec) -> str:
    try:
        s = max(0, int(sec or 0))
    except (TypeError, ValueError):
        return "0:00"
    return f"{s // 60}:{s % 60:02d}"


def _norm(s: str) -> str:
    out = []
    for ch in str(s or ""):
        if ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


_FG = None


def _fg_api():
    """앞창(포그라운드) 다루는 user32 · kernel32 — **우리 것만의** WinDLL 에 인자 타입을 박는다.
    공용 ctypes.windll.user32 에 argtypes 를 박으면 다른 모듈과 부딪힌다 (fd43aa0 에서 당했다)."""
    global _FG
    if _FG is None:
        import ctypes.wintypes as wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, res in (
            ("GetForegroundWindow", [], wt.HWND),
            ("SetForegroundWindow", [wt.HWND], wt.BOOL),
            ("BringWindowToTop", [wt.HWND], wt.BOOL),
            ("GetWindowThreadProcessId", [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            ("AttachThreadInput", [wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
        ):
            f = getattr(u, name)
            f.argtypes, f.restype = args, res
        k.GetCurrentThreadId.argtypes, k.GetCurrentThreadId.restype = [], wt.DWORD
        _FG = (u, k)
    return _FG


def _strip_prefix(s: str) -> str:
    t = str(s or "").strip()
    for p in ("악보:", "악보 :", "악보："):
        if t.startswith(p):
            return t[len(p):].strip()
    return t


class Overlay:
    """백엔드가 만드는 오버레이 한 개. start() 로 스레드 두 개(조회·화면)를 띄운다."""

    def __init__(self, base: str, activity, ensemble, settings, log=print, game_rect=None, meta=None, ops=None,
                 app_gone=None):
        self.base, self._activity, self._ensemble, self._settings, self._log = base, activity, ensemble, settings, log
        self._ops = ops or {}                # 상세창이 쓰는 기능 (목록·재생·정지·악기·창 열기)
        self._app_gone = app_gone            # 앱 창이 닫혔는가 (밴드를 먼저 내리는 데만 쓴다)
        self._gone = False                   # 그 결과 (종료 판정과는 별개)
        self._popen = False                  # 상세창 펼침
        self._pview = ("lib", None)          # 보고 있는 목록 (전체 / 재생목록)
        self._pscroll = 0                    # 목록 스크롤 (줄 단위)
        self._ptab = "queue"                 # 상세창 탭 — queue(재생목록) · near(주변 연주)
        self._inst_seen = ""                 # 게임이 알려 준 「지금 든 악기」 (미니 창과 한 몸)
        self._near = {"groups": [], "playing": 0, "quiet": 0, "realm": "", "rev": -1, "ok": False}
        self._nat = 0.0                      # 주변을 마지막으로 읽은 때
        self._psel = -1                      # 마지막으로 튼 줄 (이전/다음의 기준)
        self._hits: list = []                # 클릭 판정 사각형 [(x, y, w, h, 함수, 관통중에도살림)]
        # 개발용: 그린 그림을 받을 때 팝업까지 보려고 열어 둔다 (MABI_OV_DUMP 와 같은 결)
        self._menu = 0 if (os.environ.get("MABI_OV_MENU") == "1"
                           and not runmode.release()) else None   # 악기 고르기 (None=닫힘, 수=첫 줄)
        # 악기 고르기 — 종류 탭 · 검색. 거르는 규칙은 inst_picker 에 있다.
        self._pk_fam = inst_picker.ALL       # 고른 종류 탭 (이번 실행 동안만 기억)
        self._pk_q = ""                      # 검색어 (열 때마다 비운다)
        self._pk_hi = 0                      # ↑/↓ 로 고른 줄 (거른 목록 안의 번호)
        self._pk_chipx = 0                   # 종류 탭 줄을 옆으로 굴린 만큼
        self._pk_rows = 6                    # 한 화면에 보이는 줄 수 (그릴 때 다시 정한다)
        self._pk_fpx = 12                    # 입력칸 글자 크기 (px, 그릴 때 배율을 곱해 정한다)
        self._pk_list: list = []             # 지금 거른 목록 (Enter 가 집는다)
        self._pk_rect = None                 # 고르기 판 자리 (연주 패널 창 기준) — 이 안은 전부 「우리 UI」
        self._pk_box = None                  # 검색칸 글자 자리 (창 기준) — 여기에 진짜 입력칸을 얹는다
        self._pk_win = None                  # 입력칸 창 (tk.Toplevel + tk.Entry, 처음 열 때 만든다)
        self._pk_shown = False
        self._pk_geo = ""
        self._lib = {"scores": [], "playlists": [], "instruments": []}
        self._lib_at = 0.0
        self._dcache = None                  # 상세창을 그려 둔 그림 (프레임마다 다시 그리지 않는다)
        self._dsig = None                    # 그때의 내용 표식
        self._dhits: list = []               # 그때 잡아 둔 단추 자리
        self._dat = 0.0
        self._fsig = None                    # 한 프레임 전체의 표식 — 같으면 아예 안 그린다
        self._fat = 0.0
        self._inst = ""                      # 상세창에서 고른 악기
        # 악기 고정 — 서버 설정 `inst_pin` 의 거울. 비어 있지 않으면 모든 곡이 이 악기로 시작한다.
        # 값은 서버에만 있다: 여기서는 `queue_state()["instPin"]` 을 읽어 그리고, 누르면 `set_cfg` 로 적는다.
        self._inst_pin = ""
        self._repeat = "off"                 # off · one · all (상세창·밴드의 반복 단추)
        self._hint = ("", 0.0)               # 단추를 누르면 잠깐 뜨는 안내 (눌러도 뭐가 바뀌었는지 안 보였다)
        self._dragging = ""                  # 지금 끌고 있는 창 ("band" · "player") — 끄는 동안은 자리를 다시 계산하지 않는다
        self._pwin = (0, 0, 0, 0)            # 마지막으로 그린 연주 패널 창 (x, y, w, h)
        self._shuffle = False
        # 대기열은 **서버가 하나만** 들고 있다 (미니 창과 같은 것). 여기서는 그려 주고 명령만 보낸다.
        # 서버 대기열의 거울. 이름이 _qst 인 이유: self._q 는 **창 조작 명령 큐**다 (이름이 겹치면
        # 그리기 스레드가 Queue.get("pos") 에 영원히 걸린다 — 실제로 한 번 당했다).
        # gen 이 None 이면 「아직 한 번도 안 읽었다」 — 첫 읽기에는 보기를 바꾸지 않는다
        self._qst = {"items": [], "pos": -1, "src": "", "name": "", "next": None, "gen": None}
        self._qat = 0.0                      # 마지막으로 대기열을 읽은 시각
        self._panel_off = 0                  # 지금 그린 상세창 높이 (창을 그만큼 위로 올려 놓는다)
        self._pill_h = 0                     # 알약 부분 높이 (끌 수 있는 구역)
        self._drag_from = 0                  # 이 y 아래에서만 창을 끌 수 있다
        self._panel_dx = 0                   # 판이 알약보다 넓은 만큼 창이 왼쪽으로 나간 폭
        self._game_rect = game_rect          # 게임 창 위치 (합주 시작 연출을 게임 화면에 맞추려고)
        self._meta = meta                    # 곡 제목 → 곡명·아티스트 (악보함과 대조)
        self.opening = None                  # 화면 스레드에서 만든다
        self.file = os.path.join(base, "overlay.json")
        self._lock = threading.Lock()
        # 주변 연주 표시는 걷어냈다. 합주 인식은 미니 창에 남아 있다.
        self._data = {"playing": False, "title": "", "inst": "", "el": 0, "tot": 0, "loop": False, "ens": 0,
                      "song": "", "artist": "", "known": True, "play_at": 0.0, "event_at": 0.0,
                      "mounted": False, "wait_mount": False}
        self._cfg = {"ov_scale": 100, "ov_alpha": 85, "opening": True,
                     "repeat": "off", "shuffle": False, "advance_margin": 2, "gap_sec": 2}
        self._seen: dict[str, float] = {}
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue()   # 다른 스레드가 보내는 창 조작 (Tk 는 자기 스레드에서만 만져야 한다)
        self._visible = True
        self._through = False
        self._pass_ui = False   # 관통 중이지만 지금 커서가 우리 단추 위라 잠깐 클릭을 받는 상태
        self._hover = None      # 커서가 얹힌 단추 자리 (롤오버)
        self._chipx = 0         # 알약 줄을 좌우로 굴린 만큼 (재생목록이 많으면 화면 밖으로 나간다)
        self._chiprow = (0, 0, 0, 0)   # 그 줄의 자리 — 여기서 끌면 창이 아니라 줄이 움직인다
        self._locked = False
        self._player = True     # 플레이어 패널 표시 (연주 중에만 뜬다)
        self._auto = True       # 게임 클라이언트 영역 기준 자동 배치
        self._client = None     # 마지막으로 본 게임 클라이언트 사각형
        self._off = {}          # 수동 배치일 때 클라이언트 왼쪽 위를 기준으로 한 자리
        self._xy = (60, 60)
        self._pxy = (400, 300)
        self.root = None
        self._hwnd = 0
        self._game = 0
        self._game_at = 0.0
        self._dpi = 1.0
        self._restack = True     # 창이 막 보였으면 확인 없이 한 번 끼운다
        self._op_at = 0.0        # 마지막으로 연출을 띄운 시각 (화면 쪽과 감지 쪽이 겹쳐 두 번 뜨지 않게)
        self._op_seen = ""       # 연출을 띄워 준 곡 (같은 곡이 반복되어도 다시 띄우지 않는다)
        self._polled = False     # 첫 조회에서는 연출을 띄우지 않는다 (이미 연주 중이던 곡을 새로 시작한 걸로 보면 안 된다)

    # ── 저장 ──
    def _load(self) -> dict:
        try:
            with open(self.file, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, **kw) -> None:
        try:
            d = self._load(); d.update(kw)
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except OSError:
            pass

    def reload_cfg(self) -> None:
        """설정을 **지금 바로** 다시 읽는다.

        평소에는 10초마다 읽는데, 크기·투명도를 바꾸고 화면을 보면 그 10초가 「안 먹는다」로 보인다
        설정을 저장한 쪽에서 이걸 불러 그 자리에서 반영한다.
        """
        try:
            s = self._settings() or {}
        except Exception:
            return
        with self._lock:
            self._cfg = {k: s.get(k, v) for k, v in self._cfg.items()}
        r = str(s.get("repeat") or "off")
        if r in ("off", "one", "all"):
            self._repeat = r
        self._shuffle = bool(s.get("shuffle"))
        self._fsig = None            # 다음 그리기에서 반드시 다시 그리게 한다

    # ── 조회 스레드 (CLI 호출은 여기서만; 화면 스레드를 막지 않게) ──
    def _poll(self) -> None:
        last_cfg = 0.0
        while not self._stop.wait(2.0):
            now = time.time()
            if now - last_cfg > 10:
                last_cfg = now
                try:
                    s = self._settings() or {}
                    with self._lock:
                        self._cfg = {k: s.get(k, v) for k, v in self._cfg.items()}
                    # 반복·셔플은 미니 창과 **같은 값**이다 — 저쪽에서 바꿨으면 이쪽도 따라간다
                    r = str(s.get("repeat") or "off")
                    if r in ("off", "one", "all") and r != self._repeat:
                        self._repeat = r
                    if bool(s.get("shuffle")) != self._shuffle:
                        self._shuffle = bool(s.get("shuffle"))     # 차례를 다시 만드는 일은 서버가 한다
                except Exception:
                    pass
            if not self._visible:
                continue
            try:
                # 연주 상태는 **서버가 1초마다 한 번만** 조회한다 (예전엔 미니 창과 따로 두드렸다).
                n = (self._ops.get("now") or (lambda: {}))() or {}
                with self._lock:
                    tot = n.get("tot") or 0
                    el, loop = n.get("el") or 0, bool(n.get("loop"))
                    t = _strip_prefix(n.get("title"))
                    m = self._meta(t) if (self._meta and t) else {}
                    fresh = bool(n.get("playing")) and t != self._data.get("title")
                    if fresh:
                        self._data["play_at"] = self._data["event_at"] = now      # 연주 시작 순간 1.5초 100%
                    self._data.update(playing=bool(n.get("playing")), title=t, song=m.get("song") or t,
                                      artist=m.get("artist") or "", known=bool(m.get("known", True)),
                                      inst=str(n.get("inst") or ""), el=el, tot=tot, loop=loop,
                                      # 탈것 탑승 중 · 내리면 이어서 재생 (engine.now_state)
                                      mounted=bool(n.get("mounted")), wait_mount=bool(n.get("wait_mount")))
                    song = m.get("song") or t
                # 새 연주의 카드는 **서버 감시**(`engine._game_card_step`)가 띄운다 — 합주단이 잡힌 뒤에 사람들을 실어.
                # 예전에는 여기서 보자마자 불러 「나」 혼자였고, 오버레이를 꺼 두면 아예 안 떴다.
                self._polled = True
            except Exception:
                pass

    def _drain(self) -> None:
        while True:
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as e:
                self._log(f"[overlay] command failed: {e}")

    def _apply_style_for(self, hwnd) -> None:
        if not hwnd:
            return
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(hwnd, _GWL_EXSTYLE) | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
        thru = self._through and not (self._pass_ui and hwnd == getattr(self, "_phwnd", 0))
        ex = (ex | _WS_EX_TRANSPARENT) if thru else (ex & ~_WS_EX_TRANSPARENT)
        u.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex)

    def _apply_style(self) -> None:
        self._apply_style_for(self._hwnd)
        self._apply_style_for(getattr(self, "_phwnd", 0))

    # 아래 둘은 어느 스레드에서 불러도 된다: 상태는 즉시 바꾸고(조회가 바로 맞게), 창 조작만 큐로 넘긴다
    def _show(self, on: bool) -> None:
        self._visible = bool(on)
        self._save(visible=self._visible)
        self._log(f"[overlay] {'shown' if on else 'hidden'}")
        self._q.put(self._apply_show)

    def _want_band(self) -> bool:
        """작은 띠(「⋮⋮ 대기 — 0:00」)는 **띄우지 않는다.**

        게임 위쪽에 떠서 모비웍스 밴드와 겹쳤고, 폴리오 카드(플레이어)가 같은 것을 다 보여 준다.
        그리는 코드(`_draw`)는
        남겨 두되 이 문이 늘 닫혀 있어 창이 내려간 채다. 오버레이 켜기·끄기·Shift+F1 은 카드에만 걸린다."""
        return False

    def _apply_show(self) -> None:
        if self._want_band():
            self.root.deiconify()
            self._restack = True
        else:
            self.root.withdraw()
            if getattr(self, "pw", None) is not None:
                self._pshown = False
                self.pw.withdraw()
            self._pk_sync()                       # 판이 내려가면 입력칸도 같이 내린다

    def _set_through(self, on: bool) -> None:
        self._through = bool(on)
        self._pass_ui = False
        self._save(clickThrough=self._through)
        self._log(f"[overlay] click-through {'on' if on else 'off'}")

        def _restyle():
            self._apply_style()
            self._blit_band()       # 스타일을 바꾸면 레이어 표면이 무효가 된다 — 다시 얹지 않으면 절반이 사라진다
            if getattr(self, "_pshown", False):
                try:                 # 상세창을 펼쳤으면 그만큼 위·왼쪽으로 (그리는 자리와 같아야 한다)
                    self.psurf.blit(self._phwnd, self._pxy[0] - self._panel_dx, self._pxy[1] - self._panel_off)
                except Exception:
                    pass
        self._q.put(_restyle)

    OPEN_GAP = 7.0        # 연출 길이(6초) + 여유. 이 안에 또 부르면 무시한다

    def play_opening(self, data: dict) -> dict:
        """합주 시작 연출을 한 번 띄운다. 어느 스레드에서 불러도 된다 (창 조작은 화면 스레드가 한다).

        **부르는 곳이 셋이다** — 인사 타임라인(서버) · 미니 창의 재생 직후 · 합주로 갓 인식된 순간.
        그래서 연속으로 두세 번 뜨는 일이 있었다. 여기서 한 번만 나가게 막는다.
        """
        if self.opening is None:
            return {"ok": False, "error": "not_ready"}
        now = time.time()
        if now - self._op_at < self.OPEN_GAP:
            self._log(f"[opening] 방금 띄웠습니다 — 건너뜁니다 ({now - self._op_at:.1f}초 전)")
            return {"ok": True, "skipped": True}
        self._op_at = now
        self._q.put(lambda: self.opening.play(dict(data or {})))
        return {"ok": True}

    def set_player(self, on: bool) -> None:
        """연주 패널(둥근 알약) 표시 여부. 밴드와 따로 켜고 끈다."""
        self._player = bool(on)
        self._save(player=self._player)
        self._log(f"[overlay] player panel {'on' if on else 'off'}")

    def move_default(self) -> None:
        """위치 초기화 — 저장된 좌표를 지우고 창을 기본 자리로."""
        d = self._load()
        for k in ("x", "y", "px", "py"):
            d.pop(k, None)
        try:
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except OSError:
            pass
        if self._locked:            # 잠겨 있으면 자리를 건드리지 않는다
            self._log("[overlay] position locked — reset ignored")
            return
        self._off = {}
        self._auto = True
        self._save(off={}, auto=True)
        self._log("[overlay] placement reset to auto")

    # ── 그리기 (「모비스코어 플레이어」) ──
    # ── 자리 잡기 ──
    def _client_rect(self):
        """게임 클라이언트 영역 (GetClientRect + ClientToScreen — 테두리가 섞이지 않게).
        핸들은 기억해 두고, 사라졌을 때만 3초 간격으로 다시 찾는다 (EnumWindows 는 비싸다)."""
        try:
            import opening_tk
            u = opening_tk._u32()
            if not self._game or not u.IsWindow(self._game):
                now = time.time()
                just_lost = bool(self._game)          # 방금 사라졌으면 3초를 안 기다린다
                self._game = 0
                if not just_lost and now - getattr(self, "_find_at", 0) < 3.0:
                    return None
                self._find_at = now
                self._game = opening_tk.find_game()
                if self._game != getattr(self, "_game_said", None):   # 찾았다/못 찾았다를 바뀔 때 한 번만 적는다
                    self._game_said = self._game
                    self._log(f"[overlay] 게임 창 {'찾음 hwnd=' + str(self._game) if self._game else '없음 — 마지막 자리(또는 기본 자리)에 그린다'}")
                if not self._game:
                    return None
            return opening_tk.client_rect(self._game)      # 최소화면 None
        except Exception:
            return None

    def _btn_d(self, ch: int) -> int:
        """밴드의 정지 단추 지름. 이 원은 **우리 단추**다 — 게임의 둥근 버튼을
        덮는 물건이 아니라 눌러서 멈추는 단추다 (덮으면 게임 버튼이 클릭을 못 받는다)."""
        return max(40, int(DESIGN_BTN * self._sc))

    def _auto_xy(self, which, w, h, rect, cyw=None):
        """자동 배치 자리. 밴드는 클라이언트 위쪽 가운데(안전구역 안), 연주 패널은 게임 둥근 버튼 위."""
        cx, cy, cw, ch = rect
        if which == "band":
            # 폭 가운데 정렬이 먼저, 그다음 상단 안전구역 안으로 당긴다 (모비웍스와 같은 순서)
            x = cx + (cw - w) // 2
            left = cx + round(DESIGN_SAFE[0] * cw / DESIGN_W)
            right = cx + round(DESIGN_SAFE[1] * cw / DESIGN_W)
            if w <= right - left:
                x = min(max(x, left), right - w)
            return x, cy + max(0, round(DESIGN_TOP * ch / DESIGN_H))
        # 연주 패널: 게임 화면 **아래 가운데** — 폭 가운데 정렬, 아래 여백은 DESIGN_EDGE
        # (그 전에는 오른쪽 아래 16/16 이었다). 끌어 둔 자리가 있으면 그것이 먼저 (_player_manual).
        m = max(8, int(DESIGN_EDGE * self._sc))
        return cx + (cw - w) // 2, cy + ch - m - h

    def _manual_xy(self, which, w, h):
        """끌어 둔 자리 — 게임 클라이언트 좌상단 기준 상대값으로 기억한다 (게임 창이 움직여도 따라간다).
        연주 패널은 창 왼쪽 위가 아니라 **원 중심**을 기억한다 — 카드 모양이 바뀌어도 원이
        게임의 둥근 버튼에 그대로 얹혀 있어야 한다 (모양을 바꿨더니 통째로 밀려 삐져나왔다)."""
        off = self._off.get(which)
        rect = self._client
        ax, ay = self._anchor(which)
        if off and rect:
            return rect[0] + int(off[0]) - ax, rect[1] + int(off[1]) - ay
        if off:
            return int(off[0]) - ax, int(off[1]) - ay
        if rect:
            # 자동 배치로 떨어질 때도 기준점(원 중심)을 넘겨야 한다 — 안 넘기면 창 높이의 절반으로
            # 잡아서 원이 게임 버튼보다 몇 px 아래로 간다 (실측 8px, 초록 초승달이 비쳤다)
            return self._auto_xy(which, w, h, rect, cyw=(ay if which == "player" else None))
        return self._xy if which == "band" else self._pxy

    def _anchor(self, which):
        """기억할 기준점 — 둘 다 창 왼쪽 위다 (원이 우리 단추가 되면서 원 중심 기준은 필요 없어졌다)."""
        return (0, 0)

    def _player_manual(self, w, h):
        """손으로 옮긴 연주 패널 자리를 되돌린다 — 기억해 둔 **오른쪽·아래 여백**에서 계산한다.
        그래서 게임 창을 키우거나 줄여도 오른쪽 아래 구석에서 같은 간격을 지킨다
        (왼쪽 위 기준으로 기억하던 때는 창을 키우면 구석에서 멀어졌다)."""
        off = self._off.get("player")
        if off and self._client:
            gx, gy, gw2, gh2 = self._client
            return gx + gw2 - int(off[0]) - w, gy + gh2 - int(off[1]) - h
        if off:
            return int(off[0]), int(off[1])
        return self._auto_xy("player", w, h, self._client) if self._client else self._pxy

    def _remember_player(self) -> None:
        """연주 패널의 자리를 **오른쪽·아래 여백**으로 기억한다.
        왼쪽 위 기준으로 기억하면 게임 창을 키울 때 오른쪽 아래에서 멀어진다
        (위 오버레이는 크기를 따라가는데 아래는 안 따라간다)."""
        bx0, by0, w, h = self._pwin
        rect = self._client
        if rect:
            gx, gy, gw2, gh2 = rect
            self._off["player"] = [int(gx + gw2 - (bx0 + w)), int(gy + gh2 - (by0 + h))]
        else:
            self._off["player"] = [int(bx0), int(by0)]
        self._auto = False
        self._save(off=self._off, auto=False)

    def _remember(self, which, xy) -> None:
        """끌어서 옮긴 자리를 게임 클라이언트 왼쪽 위 기준 상대값으로 기억한다. 끌면 자동 배치는 꺼진다."""
        rect = self._client
        ax, ay = self._anchor(which)
        xy = (xy[0] + ax, xy[1] + ay)
        self._off[which] = [xy[0] - rect[0], xy[1] - rect[1]] if rect else [int(xy[0]), int(xy[1])]
        self._auto = False
        self._save(off=self._off, auto=False)

    def set_auto(self, on: bool) -> None:
        self._auto = bool(on)
        self._save(auto=self._auto)
        self._log(f"[overlay] auto placement {'on' if on else 'off'}")

    def _state(self, d, cfg) -> str:
        """밴드 상태 — 연주 중 · 연주 시작(1.5초) · 미확인 곡 · 대기."""
        now = time.time()
        if d["playing"]:
            if not d.get("known", True):
                return "unknown"
            if now - (d.get("play_at") or 0) < 1.5:
                return "start"
            return "play"
        return "idle"

    def _build(self) -> None:
        import tkinter as tk
        import paint32
        saved = self._load()
        self._paint = paint32
        self._through = bool(saved.get("clickThrough"))
        self._locked = bool(saved.get("locked"))
        self._auto = bool(saved.get("auto", True))
        self._off = dict(saved.get("off") or {})
        if int(saved.get("layout") or 0) < 2:      # 알약 모양이 바뀌어 옛 좌표는 못 쓴다 — 한 번만 버린다
            self._off.pop("player", None)
            self._save(off=self._off, layout=2)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("모비폴리오 오버레이")
        self.root.overrideredirect(True)
        self._sc = max(0.6, min(2.5, (saved.get("scale") or 100) / 100))
        self.root.geometry(f"420x40+{int(saved.get('x', 60))}+{int(saved.get('y', 60))}")
        # 캔버스를 쓰지 않는다 — 창 내용은 GDI+ 그림을 UpdateLayeredWindow 로 얹어 만든다
        self.surf = paint32.Surface(420, 40)
        self._xy = (int(saved.get("x", 60)), int(saved.get("y", 60)))

        drag = {"x": 0, "y": 0, "moved": False, "x0": 0, "y0": 0}

        def press(e):
            drag.update(x=e.x_root - self._xy[0], y=e.y_root - self._xy[1], moved=False,
                        x0=e.x_root, y0=e.y_root)
            self._dragging = ""

        def move(e):
            if self._through or self._locked:
                return
            if not drag["moved"] and max(abs(e.x_root - drag["x0"]), abs(e.y_root - drag["y0"])) < _DRAG_MIN:
                return
            drag["moved"] = True
            self._dragging = "band"
            self._auto = False
            self._xy = (e.x_root - drag["x"], e.y_root - drag["y"])
            self._blit_band()

        def release(_e):
            self._dragging = ""
            if drag["moved"]:
                self._remember("band", self._xy)
            # 밴드는 끌어서 옮기기만 한다 (주변 연주 펼침은 걷어냈다)
        self.root.bind("<Button-1>", press)
        self.root.bind("<B1-Motion>", move)
        self.root.bind("<ButtonRelease-1>", release)
        self.root.config(cursor="fleur")             # 밴드는 통째로 끌 수 있는 자리다

        self.root.update_idletasks()
        u = ctypes.windll.user32
        wid = self.root.winfo_id()
        self._hwnd = u.GetAncestor(wid, _GA_ROOT) or u.GetParent(wid) or wid
        self._dpi = paint32.dpi_of(self._hwnd)       # 화면 배율 — 크기를 여기에 곱해 실제 픽셀로 그린다

        self._sc = self._sc * self._dpi
        self._apply_style()
        self._visible = bool(saved.get("visible", True))   # 첫 실행부터 띄운다. Shift+F1 로 끈다
        self._apply_show()
        self._build_player(tk, saved)
        try:
            import opening_wv
            self._log("[opening] 만들기 시작")       # 배포판에서 「준비됨」이 3분 뒤에 온 일 — 어디서 기다리는지 가른다
            self.opening = opening_wv.make_opening(self.root, self._game_rect, self._log)   # 오버레이와 별개로 동작한다 (WebView2, 안 되면 tk)
        except Exception as e:
            self._log(f"[opening] not available: {e}")

    def _build_player(self, tk, saved) -> None:
        """연주 중에만 뜨는 둥근 패널. 게임의 둥근 정지 버튼을 우리가 그려 그 위에 겹친다."""
        self._player = bool(saved.get("player", True))
        self._popen = bool(saved.get("popen"))
        # 반복·셔플은 **서버 설정 하나**가 원본이다 — overlay.json 에 따로 적어 두던 사본은 걷어냈다.
        # (사본을 먼저 읽으면 미니에서 끈 셔플이 오버레이에는 켜진 채로 떴다.)
        self._load_play_cfg()
        self.pw = tk.Toplevel(self.root)
        self.pw.withdraw()
        self.pw.overrideredirect(True)
        self.pw.geometry("420x90+400+300")
        self.psurf = self._paint.Surface(420, 90)
        self._pxy = (400, 300)
        drag = {"x": 0, "y": 0, "moved": False, "x0": 0, "y0": 0}

        def in_chips(e):
            """알약 줄 위인가 — 거기서 끌면 창이 아니라 줄이 좌우로 굴러간다."""
            if not self._popen:
                return False
            rx, ry, rw, rh = self._chiprow
            return rx <= e.x < rx + rw and ry <= e.y < ry + rh

        def press(e):
            drag.update(x=e.x_root - self._pxy[0], y=e.y_root - self._pxy[1], moved=False,
                        x0=e.x_root, y0=e.y_root, chips=in_chips(e),
                        cx0=(self._pk_chipx if self._menu is not None else self._chipx))
            self._dragging = ""

        def move(e):
            if self._through or self._locked:
                return
            if drag.get("chips"):           # 알약 줄 굴리기 (재생목록이 많으면 화면 밖으로 나간다)
                if not drag["moved"] and abs(e.x_root - drag["x0"]) < _DRAG_MIN:
                    return
                drag["moved"] = True
                self._dragging = "chips"
                if self._menu is not None:  # 악기 고르기가 열려 있으면 그 종류 탭 줄을 굴린다
                    self._pk_chipx = drag["cx0"] + (e.x_root - drag["x0"])
                else:
                    self._chipx = drag["cx0"] + (e.x_root - drag["x0"])
                now = time.time()          # 50ms 에 한 번만 다시 그린다 (마우스는 초당 100번 온다)
                if now - drag.get("t", 0.0) > 0.05:
                    drag["t"] = now
                    self._redraw()
                return
            if self._popen and e.y < self._drag_from:
                return                      # 펼친 판 안에서는 끌지 않는다 (단추를 누르려던 것)
            # 누를 때 손이 1px 흔들리는 것을 「끌었다」로 보면 단추가 하나도 안 먹는다.
            # 문턱을 넘은 뒤부터 끌기로 본다.
            if not drag["moved"] and max(abs(e.x_root - drag["x0"]), abs(e.y_root - drag["y0"])) < _DRAG_MIN:
                return
            drag["moved"] = True
            self._dragging = "player"
            self._auto = False                   # 끌기 시작하면 곧바로 수동 배치로 (자동이면 계속 되돌아간다)
            self._pxy = (e.x_root - drag["x"], e.y_root - drag["y"])
            bx0, by0 = self._pxy[0] - self._panel_dx, self._pxy[1] - self._panel_off
            self._pwin = (bx0, by0, self._pwin[2], self._pwin[3])   # 놓을 때 이 자리를 기억한다
            self.psurf.blit(self._phwnd, bx0, by0)
            self._pk_sync()                 # 검색 입력칸도 판을 따라 옮긴다

        def release(e):
            was = self._dragging
            self._dragging = ""
            if drag["moved"]:
                if was != "chips":          # 줄을 굴린 것은 창을 옮긴 게 아니다
                    self._remember_player()
            else:
                self._click(e.x, e.y)
        self.pw.bind("<Button-1>", press)
        self.pw.bind("<B1-Motion>", move)
        self.pw.bind("<ButtonRelease-1>", release)
        self.pw.bind("<MouseWheel>", lambda e: self._wheel(e.delta))
        self.pw.config(cursor="")                    # 단추 위에서는 _hover_tick 이 손 모양으로 바꾼다
        self.pw.update_idletasks()
        u = ctypes.windll.user32
        wid = self.pw.winfo_id()
        self._phwnd = u.GetAncestor(wid, _GA_ROOT) or u.GetParent(wid) or wid
        self._apply_style_for(self._phwnd)

    # ── 공통 ──
    def _fit(self, g, text, family, size, bold, maxw) -> str:
        """칸 폭을 넘으면 말줄임. 글자 수로 자르면 한글·영문이 섞일 때 들쭉날쭉하다."""
        if not text or g.measure(text, family, size, bold) <= maxw:
            return text or ""
        t = text
        while t and g.measure(t + "…", family, size, bold) > maxw:
            t = t[:-1]
        return (t + "…") if t else ""

    def _line_w(self) -> float:
        """테두리 굵기 — 디자인의 1px 에 화면 배율을 곱해 반올림 (모비웍스와 같은 식).
        125% 면 1, 175% 면 2. 배율을 안 곱하면 175% 에서 테두리가 실오라기가 된다."""
        return max(1.0, float(round(self._sc)))

    def _card(self, g, x, y, w, h, r, fill, outline, alpha) -> None:
        """카드 한 장. 테두리를 쓸 때는 (t/2, t/2, w-t, h-t) 로 넘긴다 — 바탕·테두리가 같은 경로에 앉는다."""
        t = self._line_w()
        g.round_rect(x + t / 2, y + t / 2, w - t, h - t, r, fill=fill, outline=outline, width=t, alpha=alpha)

    def _handle(self, g, x, cy, color) -> int:
        """⋮⋮ 손잡이 — 글리프를 쓰지 않고 점 여섯 개를 직접 찍는다 (글꼴마다 간격이 제멋대로다).
        지름 1.6 · 두 열 간격 3.0 · 세로 점 간격 3.6 (모비웍스 확정값)."""
        sc = self._sc
        d = max(1, int(round(1.6 * sc)))
        colgap = 3.0 * sc
        rowgap = 3.6 * sc
        for c in range(2):
            for rr in range(3):
                g.ellipse(x + int(round(c * colgap)), int(round(cy - rowgap + rr * rowgap - d / 2)), d, d, color)
        return int(round(colgap)) + d

    def _bg_alpha(self) -> int:
        """**바탕에만** 거는 불투명도. 글자는 언제나 불투명하다 —
        창 전체에 걸면 게임 글자가 밴드를 뚫고 올라와 작은 한글 획이 뭉개진다.
        평소는 설정값, 연주 시작·주변 감지 순간만 1.5초 동안 꽉 채운다."""
        with self._lock:
            rest = self._cfg.get("ov_alpha") or 85
            ev = self._data.get("event_at") or 0
        return 255 if (time.time() - ev < 1.5) else max(60, min(255, int(rest * 255 / 100)))

    def _dump(self, g, name) -> None:
        """개발용: 그린 그림을 파일로 남긴다 (화면 캡처는 반투명 창을 제대로 못 담는다)."""
        d = os.environ.get("MABI_OV_DUMP")
        if d:
            try:
                g.save(os.path.join(d, name + ".png"))
            except Exception:
                pass

    def _lwa_check(self, g, hwnd, what) -> None:
        """첫 얹기 뒤 한 번만 — 이 창에 -alpha 가 걸려 있으면 그림이 조용히 사라진다."""
        if getattr(self, "_lwa_done", None) is None:
            self._lwa_done = set()
        if what in self._lwa_done:
            return
        self._lwa_done.add(what)
        try:
            if g.lwa_locked(hwnd):
                self._log(f"[overlay] {what}: 이 창에 -alpha 가 걸려 있어 그림이 안 그려집니다 (SetLayeredWindowAttributes 고정)")
        except Exception:
            pass

    def _sync_size(self, win, key, w, h) -> None:
        """Tk 창 크기를 그림 크기에 맞춘다. 창 자체는 UpdateLayeredWindow 가 옮기고 키우지만,
        Tk 안쪽 창이 처음 크기 그대로면 그 밖을 누른 클릭이 Tk 이벤트로 오지 않는다 (실측)."""
        if getattr(self, key, None) == (w, h):
            return
        setattr(self, key, (w, h))
        try:
            win.geometry(f"{int(w)}x{int(h)}")
        except Exception:
            pass

    def _blit_band(self) -> None:
        try:
            self.surf.blit(self._hwnd, self._xy[0], self._xy[1])
        except Exception:
            pass

    # ── 상세창 ──
    def _lib_get(self) -> dict:
        """악보·재생목록·악기. 2초 안에 다시 부르면 그대로 쓴다 (한 번 그릴 때마다 만들지 않게)."""
        now = time.time()
        if now - self._lib_at > 2.0 and self._ops.get("library"):
            self._lib_at = now
            try:
                x = self._ops["library"]() or {}
                self._lib = {"scores": x.get("scores") or [], "playlists": x.get("playlists") or [],
                             "instruments": x.get("instruments") or []}
            except Exception as e:
                self._log(f"[overlay] 목록을 못 읽었습니다: {e}")
        return self._lib

    def _view_name(self) -> str:
        """지금 보고 있는 목록의 이름 (대기열 출처로 적어 둔다)."""
        kind, pid = self._pview
        if kind == "pl":
            pl = next((x for x in self._lib_get()["playlists"] if x.get("id") == pid), None)
            return str((pl or {}).get("name") or "재생목록")
        return {"lib": "전체", "recent": "최근", "solo": "솔로", "ens": "합주", "queue": "현재 재생목록"}.get(kind, "전체")

    def _q_get(self) -> dict:
        """서버 대기열. 2초 안에 다시 부르면 그대로 쓴다 (한 프레임마다 묻지 않게)."""
        now = time.time()
        if now - self._qat > 2.0 and self._ops.get("queue_state"):
            self._qat = now
            try:
                q = self._ops["queue_state"]() or {}
                if q.get("ok"):
                    gen = int(q.get("gen") or 0)
                    first = self._qst.get("gen") is None
                    if gen != self._qst.get("gen") and q.get("total") and not first:
                        self._pview = ("queue", None)    # 재생을 시작하면 현재 재생목록을 보여 준다
                        self._pscroll = 0
                    self._mirror_play_cfg(q)
                    self._qst = {"items": q.get("items") or [], "pos": int(q.get("pos") or -1),
                                 "src": q.get("src") or "", "name": q.get("name") or "",
                                 "next": q.get("next"), "gen": gen, "rev": int(q.get("rev") or 0)}
            except Exception as e:
                self._log(f"[overlay] 대기열을 못 읽었습니다: {e}")
        return self._qst

    def _load_play_cfg(self) -> None:
        """반복·셔플·악기 고정을 서버 설정에서 읽는다 (창을 만들 때 — 첫 `_q_get` 전에도 맞는 값으로)."""
        try:
            s0 = self._settings() or {}
        except Exception:
            s0 = {}
        self._mirror_play_cfg({"repeat": s0.get("repeat"), "shuffle": s0.get("shuffle"),
                               "instPin": s0.get("inst_pin")})

    def _mirror_play_cfg(self, q: dict) -> None:
        """서버가 들고 있는 반복·셔플·악기 고정을 그대로 비춘다 — 미니 창에서 바꾼 것도 2초 안에 따라온다."""
        r = str(q.get("repeat") or "off")
        if r in ("off", "one", "all"):
            self._repeat = r
        if "shuffle" in q:
            self._shuffle = bool(q.get("shuffle"))
        if "instPin" in q:
            self._inst_pin = str(q.get("instPin") or "")

    def _near_get(self) -> dict:
        """주변 연주. 상세창의 주변 탭을 보고 있을 때만 1.5초마다 읽는다
        (서버는 「보고 있다」는 신호를 받은 동안에만 게임에 물어본다)."""
        now = time.time()
        playing = bool(self._data.get("playing"))
        want = (self._popen and self._ptab == "near") or playing   # 연주 중에는 서버가 이미 주변을 본다
        if want and now - self._nat > 1.5 and self._ops.get("near"):
            self._nat = now
            try:
                n = self._ops["near"]() or {}
                self._near = {"groups": n.get("groups") or [], "playing": int(n.get("playing") or 0),
                              "quiet": int(n.get("quiet") or 0), "realm": n.get("realm") or "",
                              "rev": int(n.get("rev") or 0), "ok": bool(n.get("ok")),
                              "message": n.get("message") or ""}
            except Exception as e:
                self._log(f"[overlay] 주변을 못 읽었습니다: {e}")
        return self._near

    def _set_tab(self, key: str) -> None:
        """상세창 탭 바꾸기 — 미니 창의 펼친 플레이어와 같은 갈래다."""
        if self._menu is not None:         # 탭을 누르면 악기 고르기는 닫는다 (고르기는 재생목록 탭 위의 판이다)
            self._close_menu()
        if self._ptab != key:
            self._ptab = key
            self._nat = 0.0            # 탭을 열면 바로 한 번 읽는다
            self._dsig = None

    def _next_row(self):
        """밴드·상세창에 보여 줄 「다음 곡」 — 서버 대기열 기준이라 미니 창과 같다."""
        return self._q_get().get("next")

    def _rows(self) -> list:
        """지금 보기(전체 또는 재생목록)의 줄."""
        lib = self._lib_get()
        kind, pid = self._pview
        if kind == "queue":                      # 현재 재생목록 (서버 대기열 그대로)
            return list(self._q_get().get("items") or [])
        if kind == "recent":
            return sorted([it for it in lib["scores"] if it.get("lastPlayed")],
                          key=lambda it: -(it.get("lastPlayed") or 0))
        if kind == "solo":
            return [it for it in lib["scores"] if (it.get("ens") or 0) == 1]
        if kind == "ens":
            return [it for it in lib["scores"] if (it.get("ens") or 0) >= 2]
        if kind != "pl":
            return lib["scores"]
        pl = next((p for p in lib["playlists"] if p.get("id") == pid), None)
        if not pl:
            return lib["scores"]
        by = {s.get("key"): s for s in lib["scores"]}
        out = []
        for it in pl.get("items") or []:
            s = by.get(it.get("key"))
            if s:
                r = dict(s)
                r["plInst"] = it.get("inst") or ""
            else:
                t = it.get("title") or ""
                r = {"title": t, "song": t, "artist": "", "key": it.get("key") or "",
                     "missing": True, "plInst": it.get("inst") or ""}
            out.append(r)
        return out

    def _bg(self, fn, *a) -> None:
        """CLI 를 타는 일은 화면 스레드에서 하지 않는다 (몇 초씩 걸린다 — 그동안 창이 멈춘다)."""
        def run():
            try:
                fn(*a)
            except Exception as e:
                self._log(f"[overlay] {getattr(fn, '__name__', '동작')} 실패: {e}")
        threading.Thread(target=run, daemon=True).start()

    def _key_of(self, it) -> str:
        return str((it or {}).get("key") or (it or {}).get("title") or "")

    def _play_row(self, i: int) -> None:
        """지금 보고 있는 목록에서 한 줄을 튼다 — **그 목록 전체가 대기열**이 된다.

        서버가 대기열을 하나만 들고 있으므로, 미니 창의 「다음 곡」도 같은 것이 된다.
        """
        if self._pview[0] == "queue":                    # 현재 재생목록에서 고르면 그 자리로 옮긴다
            fn = self._ops.get("queue_at")
            if fn:
                self._bg(fn, i)
            return
        rows = self._rows()
        if not (0 <= i < len(rows)) or not self._ops.get("queue_set"):
            return
        keys = [self._key_of(r) for r in rows if self._key_of(r)]
        insts = {self._key_of(r): (r.get("plInst") or self._inst or "") for r in rows if self._key_of(r)}
        kind, pid = self._pview
        src = f"pl:{pid}" if kind == "pl" else kind
        self._psel = i
        self._log(f"[overlay] 재생 — {rows[i].get('title')} ({self._view_name()} {len(keys)}곡)")
        self._bg(self._ops["queue_set"], keys, self._key_of(rows[i]), src, self._view_name(), insts)

    def _step(self, dir_: int) -> None:
        """이전·다음 — 서버 대기열을 움직인다 (미니 창과 같은 하나)."""
        fn = self._ops.get("queue_step")
        if not fn:
            return

        def go():
            r = fn(dir_) or {}
            if r.get("error") == "queue_end":
                self._say_hint("대기열 끝입니다 — 전체 반복을 켜면 처음으로 돌아갑니다")
        self._bg(go)

    def _put_cfg(self, key, val) -> None:
        """미니 창과 함께 쓰는 값(반복·셔플·악기 고정)을 **서버 설정**에 적는다.

        CLI 를 타지 않는 일(설정 저장 + 차례 다시 만들기)이라 그 자리에서 부른다 — 뒤 스레드로 미루면
        바로 다음 `_q_get` 이 옛 값을 읽어 단추가 한 번 되돌아가 보인다."""
        fn = self._ops.get("set_cfg")
        if fn:
            try:
                fn(key, val)
            except Exception as e:
                self._log(f"[overlay] 설정 저장 실패 ({key}): {e}")
        with self._lock:
            if key in self._cfg:
                self._cfg[key] = val

    def _say_hint(self, text: str) -> None:
        """눌렀을 때 뭐가 바뀌었는지 2초간 보여 준다 — 안 보이면 「작동 안 함」으로 보인다."""
        self._hint = (text, time.time() + 2.0)

    def _cycle_repeat(self) -> None:
        order = ("off", "one", "all")
        self._repeat = order[(order.index(self._repeat) + 1) % 3]
        self._put_cfg("repeat", self._repeat)
        self._say_hint({"off": "반복 끔 — 목록 끝나면 정지", "one": "한 곡 반복 — 지금 곡만 계속",
                        "all": "전체 반복 — 목록 끝나면 처음으로"}[self._repeat])

    def _toggle_shuffle(self) -> None:
        """셔플을 누른 **그 순간** 지금 목록을 한 번 섞고, 그 차례를 그대로 들고 간다."""
        self._shuffle = not self._shuffle
        self._put_cfg("shuffle", self._shuffle)          # 서버가 그 자리에서 대기열 차례를 다시 만든다
        self._qat = 0.0                                  # 대기열을 곧바로 다시 읽는다 (서버가 차례를 새로 만든다)
        self._say_hint("셔플 켬 — 지금 목록을 섞었습니다" if self._shuffle else "셔플 끔 — 목록 순서대로")

    def _toggle_opening(self) -> None:
        """연주 시작 연출 켜기·끄기. 설정에 저장해 미니 창과 같은 값을 본다."""
        on = self._cfg.get("opening") is not False
        self._cfg["opening"] = not on
        fn = self._ops.get("set_opening")
        if fn:
            self._bg(fn, not on)
        self._say_hint("연주 시작 연출 켬 — 곡이 시작될 때 나옵니다" if not on else "연주 시작 연출 끔")

    def _toggle_play(self) -> None:
        with self._lock:
            playing = bool(self._data.get("playing"))
        if playing and self._ops.get("stop"):
            self._bg(self._ops["stop"])          # 서버가 「사람이 멈춤」으로 적어 다음 곡을 잇지 않는다
        else:
            q = self._q_get()
            if (q.get("items") or []) and q.get("pos", -1) >= 0 and self._ops.get("queue_at"):
                self._bg(self._ops["queue_at"], int(q["pos"]))     # 대기열의 지금 자리를 다시 튼다
            else:
                self._play_row(self._psel if self._psel >= 0 else 0)

    def _equip(self) -> None:
        if self._inst_pin:                  # 고정 중에는 고정한 악기가 곧 「지금 악기」다
            self._inst = self._inst_pin
        self._inst_seen = self._inst        # 내가 바꾼 것 — 다음 조회에서 되돌아오지 않게
        if self._inst and self._ops.get("instrument"):
            self._bg(self._ops["instrument"], self._inst)
            self._say_hint(f"악기를 바꿔 듭니다 — {self._inst}")

    def _shown_inst(self) -> str:
        """악기 칸·고르기 판에 「지금 악기」로 보일 이름 — 고정 중이면 고정한 악기."""
        return self._inst_pin or self._inst

    def _toggle_pin(self) -> None:
        """악기 고정 — 눌러 두면 설정된 악기와 상관없이 고정한 악기로 튼다.
        켜면 지금 칸의 악기를 서버 설정 `inst_pin` 에 적고, 끄면 비운다.
        악기를 바꿔 드는 명령은 여기서 부르지 않는다 — 다음 곡을 틀 때 `play()` 가 한 번 바꿔 든다."""
        if self._inst_pin:
            self._inst_pin = ""
            self._put_cfg("inst_pin", "")
            self._qat = 0.0
            self._say_hint("악기 고정 해제 — 곡마다 정한 악기·기본 악기로")
            return
        name = self._inst or self._inst_seen
        if not name:
            self._say_hint("고정할 악기를 먼저 고르세요")
            return
        self._inst_pin = name
        self._put_cfg("inst_pin", name)
        self._qat = 0.0
        self._say_hint(f"악기 고정 — 모든 곡을 {name} 로")

    def _play_view_all(self) -> None:
        """지금 보고 있는 목록 전부를 대기열로 만들고 첫 곡부터 튼다 (미니 창과 같은 동작)."""
        rows = self._rows()
        if not rows or not self._ops.get("queue_set"):
            self._say_hint("재생할 곡이 없습니다")
            return
        keys = [self._key_of(r) for r in rows if self._key_of(r)]
        if not keys:
            self._say_hint("재생할 곡이 없습니다")
            return
        insts = {self._key_of(r): (r.get("plInst") or self._inst or "") for r in rows if self._key_of(r)}
        kind, pid = self._pview
        src = f"pl:{pid}" if kind == "pl" else kind          # _play_row 과 같은 규칙
        name = self._view_name()
        self._psel = 0
        self._say_hint(f"{name} 전체 재생 — {len(keys)}곡")
        self._bg(self._ops["queue_set"], keys, keys[0], src, name, insts)

    def _set_view(self, kind, pid) -> None:
        self._pview = (kind, pid)
        self._pscroll = 0

    def _ctl_icon(self, g, kind: str, cx: float, cy: float, size: float, color: str) -> None:
        """재생 조작 아이콘 — **미니 창과 같은 모양**으로 그린다.

        미니는 24x24 SVG 를 쓰므로 여기서도 그 좌표를 그대로 옮겨 그린다.
        (글꼴 기호 ↻ ⇄ 는 글꼴마다 모양이 달라 두 화면이 달라 보였다.)
        """
        f = size / 24.0
        w = max(1.0, round(size / 12.0))          # 미니의 stroke-width 1.6 과 비슷한 굵기

        def X(v):
            return cx + (v - 12.0) * f

        def Y(v):
            return cy + (v - 12.0) * f

        def seg(x0, y0, x1, y1):
            g.line(X(x0), Y(y0), X(x1), Y(y1), color, width=w)

        if kind == "shuffle":                      # M16 4h4v4 · M4 20l16-16 · M16 20h4v-4 · M4 4l5 5
            seg(4, 20, 20, 4)
            seg(16, 4, 20, 4); seg(20, 4, 20, 8)
            seg(16, 20, 20, 20); seg(20, 20, 20, 16)
            seg(4, 4, 9, 9)
            return
        if kind == "pin":                          # 악기 고정 — 미니의 ic-pin: M8 3h8 · M9 3v7l-3 5h12l-3-5V3 · M12 15v7
            seg(8, 3, 16, 3)
            seg(9, 3, 9, 10); seg(9, 10, 6, 15)
            seg(15, 3, 15, 10); seg(15, 10, 18, 15)
            seg(6, 15, 18, 15)
            seg(12, 15, 12, 22)
            return
        if kind == "spark":                        # 연주 시작 연출 — 모양은 그대로 두고 조금 키웠다
            g.icon(cx, cy, "✦", FONT, max(8, int(round(size * 1.05))), color)
            return
        # 반복 — 미니의 ic-rep: 위아래 막대 + 둥근 모서리 + 화살촉
        seg(8, 5, 15, 5)
        g.arc(X(4), Y(5), 8 * f, 8 * f, 180, 90, color, width=w)     # 왼쪽 위 모서리
        seg(16, 2, 19, 5); seg(19, 5, 16, 8)
        seg(16, 19, 5, 19)
        g.arc(X(12), Y(11), 8 * f, 8 * f, 0, 90, color, width=w)     # 오른쪽 아래 모서리
        seg(8, 22, 5, 19); seg(5, 19, 8, 16)
        if kind == "repeat-one":                   # 가운데 1
            seg(11.55, 10.7, 12.45, 10); seg(12.45, 10, 12.45, 14)
        elif kind == "repeat-off":                 # 빗금
            seg(4, 3, 20, 21)

    def _ctl_kind(self, what: str) -> str:
        """단추가 지금 어떤 모양이어야 하는가."""
        if what == "repeat":
            return {"one": "repeat-one", "off": "repeat-off"}.get(self._repeat, "repeat")
        return what

    def _round_btn(self, g, x, y, dd, on) -> None:
        """작은 둥근 단추 — 켜져 있으면 금색 바탕."""
        if on:
            g.round_rect(x, y, dd, dd, dd // 2, fill="#3a3421", outline=GOLD_DIM, width=1)
        else:
            g.round_rect(x, y, dd, dd, dd // 2, outline=P_LINE, width=1)

    def _ctext(self, g, cx, cy, txt, family, size, color, bold=False) -> None:
        """가로 가운데 정렬 글자. paint32.text 의 anchor 에는 가운데가 없어서 재서 놓는다."""
        g.text(cx - g.measure(txt, family, size, bold) / 2.0, cy, txt, family, size, color, bold)

    def _hit(self, x, y, w, h, fn, keep=False) -> None:
        """단추 자리 하나. keep=True 면 **스마트 관통 중에도** 살아 있는다 (관통을 되돌릴 길).
        나머지는 관통 중에 눌리지 않는다 — 안 그러면 관통을 켠 의미가 없다."""
        self._hits.append((int(x), int(y), int(w), int(h), fn, bool(keep)))

    def _hit_at(self, x, y):
        """창 안 (x, y) 에 걸린 단추 → (자리, 함수). 뒤에 담은 것이 위에 그린 것이므로 뒤에서부터 본다.

        스마트 관통 중에는 keep 이 붙은 것만 본다 — 그래야 나머지 자리는 게임으로 지나간다.
        """
        for hx, hy, hw, hh, fn, keep in reversed(self._hits):
            if self._through and not keep:
                continue
            if hx <= x < hx + hw and hy <= y < hy + hh:
                return (hx, hy, hw, hh), fn
        return None, None

    def _hit_fn(self, x, y):
        return self._hit_at(x, y)[1]

    def _cursor_hit(self):
        """커서가 얹힌 단추 자리 (창 기준). 손 모양 커서와 롤오버 색에 쓴다."""
        if not getattr(self, "_pshown", False) or not self._hits:
            return None
        pt = ctypes.wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return None
        return self._hit_at(pt.x - self._pwin[0], pt.y - self._pwin[1])[0]

    def _hover_tick(self) -> None:
        """커서가 단추에 얹히면 **손 모양**으로 바꾸고 그 자리를 밝힌다.

        40ms 마다 본다 — 누를 수 있는 곳인지 눈으로 알 수 있어야 한다.
        """
        if self._dragging:            # 끄는 동안에는 롤오버로 또 그리지 않는다 (깜빡임)
            return
        r = self._cursor_hit()
        if r == self._hover:
            return
        self._hover = r
        try:
            self.pw.config(cursor="hand2" if r else "")
        except Exception:
            pass
        self._redraw()

    def _click(self, x, y) -> bool:
        """상세창 클릭."""
        fn = self._hit_fn(x, y)
        if fn is not None:
            try:
                self._log(f"[overlay] 상세창 클릭 ({x},{y})")
                fn()
            except Exception as e:
                self._log(f"[overlay] 클릭 처리 실패: {e}")
            self._redraw()
            return True
        if self._menu is not None:
            if self._in_picker(x, y):       # 고르기 판 안의 빈틈(칩 사이·줄 사이)은 닫지 않는다
                return True
            self._close_menu()              # 판 밖 빈 곳을 누르면 고르기만 닫는다
            self._redraw()
            return True
        return False

    def _in_picker(self, x, y) -> bool:
        """창 안 (x, y) 가 악기 고르기 판 위인가 (열려 있을 때만)."""
        r = self._pk_rect if self._menu is not None else None
        return bool(r) and r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]

    def _wheel(self, delta: int) -> None:
        """휠. 악기 고르기가 열려 있으면 **그 목록만** 굴린다 — 뒤의 재생목록도, 게임도 아니다
        (휠이 뒤 게임까지 같이 굴렀다). 게임으로 새지 않게 하는 쪽은 _on_ui 가 맡는다."""
        if self._menu is not None:
            self._menu = inst_picker.scroll(int(self._menu or 0), len(self._pk_list), self._pk_rows, delta)
            self._redraw()
            return
        if not self._popen:
            return
        n = len(self._rows())
        self._pscroll = max(0, min(max(0, n - PANEL_ROWS), self._pscroll - (1 if delta > 0 else -1) * 3))
        self._redraw()

    def _panel_h(self, px) -> int:
        return (px(DESIGN_TOPBAR) + px(DESIGN_TABS) + px(DESIGN_CHIPS)
                + PANEL_ROWS * px(DESIGN_ROW) + px(DESIGN_FOOT))

    def _chip(self, g, x, cy, label, on, px, fn=None, gold=True, icon="", count="", keep=False, span=None) -> int:
        """알약 하나 (아이콘 + 글자 + 작은 숫자). 돌려주는 값은 다음 알약이 시작할 x."""
        s = px(11)
        iw = (g.measure(icon, FONT, s) + px(5)) if icon else 0
        cw = (g.measure(count, MONO, px(10)) + px(5)) if count else 0
        w = iw + g.measure(label, FONT, s, on) + cw + px(20)
        h = px(24)
        y = cy - h // 2
        fg = "#ffffff" if on else P_CHIP_FG          # 고른 알약 글자는 흰색
        if on:
            self._card(g, x, y, w, h, h // 2, "#3a3421" if gold else P_SEL_BG, GOLD if gold else P_LINE, 255)
        else:
            self._card(g, x, y, w, h, h // 2, None, P_LINE, 255)
        tx = x + px(10)
        if icon:
            g.icon(tx + g.measure(icon, FONT, s) / 2.0, cy, icon, FONT, s, fg)
            tx += g.measure(icon, FONT, s) + px(5)
        tx += g.text(tx, cy, label, FONT, s, fg, bold=on)
        if count:
            g.text(tx + px(5), cy, count, MONO, px(10), ("#ffffff" if on else P_SUB))
        # span 은 이 알약이 놓인 줄의 (왼쪽, 오른쪽). 줄 밖으로 나간 알약은 누르지 못하게 한다
        # (안 그러면 안 보이는 알약이 아래 목록 위에서 눌린다).
        if fn and (span is None or (x >= span[0] and x + w <= span[1])):
            self._hit(x, y, w, h, fn, keep=keep)
        return x + w + px(6)

    def _chip_scroll(self, d: int) -> None:
        """알약 줄을 한 화면씩 옆으로 민다 (가두는 것은 그릴 때 한다)."""
        self._chipx = int(self._chipx) + int(d)

    def _band_near_n(self) -> int:
        return len(self._near.get("groups") or ())

    def _detail_sig(self, d) -> tuple:
        """상세창이 읽는 값들. 이게 그대로면 지난 프레임 그림을 다시 써도 된다."""
        lib = self._lib
        return (round(self._sc, 3), self._locked, self._through, self._inst, self._inst_pin, self._menu,
                self._pk_fam, self._pk_q, self._pk_hi, int(self._pk_chipx),
                self._ptab, self._near.get("rev"), len(self._near.get("groups") or ()),
                self._pview, self._pscroll, self._psel, self._repeat, self._shuffle,
                self._qst.get("pos"), len(self._qst.get("items") or ()), self._qst.get("gen"),
                self._qst.get("rev"),
                self._key_of(self._next_row()), int(self._chipx),
                self._cfg.get("opening") is not False, self._bg_alpha(),
                len(lib.get("scores") or ()), len(lib.get("playlists") or ()), len(lib.get("instruments") or ()),
                d.get("title"), d.get("song"), d.get("artist"), int(d.get("tot") or 0), int(d.get("el") or 0),
                int(d.get("ens") or 0), bool(d.get("playing")))

    def _detail_cached(self, g, d, px, w, top) -> None:
        """상세창은 한 번 그려 두고 프레임마다 **옮겨 붙인다**.

        상세창 한 장이 한 프레임의 65% 였다(실측 프로파일). 내용이 바뀔 때만 다시 그리고,
        평소에는 32비트 DIB 끼리 BitBlt 로 옮긴다(0.02ms). 놓친 변화가 있어도 2초마다
        한 번은 무조건 다시 그려 스스로 따라잡는다 — 조용히 옛 화면이 남는 일이 없게.
        """
        pw = min(w, px(DESIGN_PANEL_W))
        ph = self._panel_h(px)
        x0 = w - pw
        sig = (self._detail_sig(d), w, top, pw, ph, g.w, g.h)
        now = time.time()
        cache = self._dcache
        if cache is None:
            cache = self._dcache = self._paint.Surface(g.w, g.h)
            self._dsig = None
        if sig != self._dsig or now - self._dat > 2.0:
            cache.resize(g.w, g.h)
            cache.clear()
            keep, self._hits = self._hits, []
            try:
                self._draw_detail(cache, d, px, w, top)
            finally:
                self._dhits, self._hits = self._hits, keep
            self._dsig, self._dat = sig, now
        self._hits.extend(self._dhits)
        g.copy_from(cache, x0, top, pw, ph)

    def _draw_near(self, g, px, x0, pw, y0, h, near) -> None:
        """주변 연주 — **StartAt 이 같은 사람끼리 한 팀**으로 묶어 보여 준다.

        게임이 닉네임을 주지 않아 사람은 **칭호**로 적는다 (RealmName 은 영지다).
        미니 창의 주변 탭과 같은 값을 본다 — 판정도 조회도 서버가 한 번만 한다.
        """
        s10, s12, s13 = px(10), px(12), px(13)
        pad = px(12)
        hy = y0 + px(14)
        if near.get("ok"):
            left = str(near.get("realm") or "")
            tx = x0 + pad
            if left:
                g.text(tx, hy, left, FONT, s10, P_SUB)
                tx += g.measure(left, FONT, s10) + px(8)
            g.text(tx, hy, f"연주 중 {near.get('playing') or 0}", FONT, s10, P_SUB)
            g.text(x0 + pw - pad, hy, f"연주 안 함 {near.get('quiet') or 0}", FONT, s10, P_SUB, anchor="e")
        else:
            g.text(x0 + pad, hy, "주변을 읽지 못했습니다 — 게임이 켜져 있는지 보세요", FONT, s10, P_SUB)
        groups = near.get("groups") or []
        gy = y0 + px(26)
        if not groups:
            g.text(x0 + pad, gy + px(16), "주변에 연주 중인 사람이 없습니다", FONT, s12, P_SUB)
            return
        gh = px(80)                      # 제목 · 칩 줄 · 진행 막대 · 구성원 칩이 겹치지 않는 높이
        room = max(1, int((h - px(30)) // gh))
        for grp in groups[:room]:
            mine = bool(grp.get("me"))
            song = self._fit(g, str(grp.get("song") or grp.get("title") or "미확인 곡"),
                             FONT, s13, True, pw - pad * 2 - px(74))
            g.text(x0 + pad, gy + px(11), song, FONT, s13, (GOLD if mine else P_INK), bold=True)
            dist = grp.get("distance")
            dtxt = "내 연주" if mine else ("" if dist is None else ("바로 옆" if dist <= 0 else f"{int(round(dist))}m"))
            if dtxt:
                g.text(x0 + pw - pad, gy + px(11), dtxt, FONT, s10, P_SUB, anchor="e")
            cx = x0 + pad
            cy2 = gy + px(28)
            tags = [(f"{grp.get('size') or 1}인", "#9dbcf2", "#1b2433")]
            if grp.get("loop"):
                tags.append(("반복", P_CHIP_FG, None))
            if grp.get("copy"):
                tags.append(("복사 허용", GOLD, None))
            for label, col, bg in tags:
                cw = g.measure(label, FONT, s10, True) + px(14)
                self._card(g, cx, cy2 - px(9), cw, px(18), px(9), bg, (col if bg else P_LINE), 255)
                self._ctext(g, cx + cw / 2, cy2, label, FONT, s10, col, True)
                cx += cw + px(5)
            tot, el = float(grp.get("total") or 0), float(grp.get("elapsed") or 0)
            g.text(x0 + pw - pad, cy2, (f"{_fmt(el)} / {_fmt(tot)}" if tot else _fmt(el)),
                   MONO, s10, P_SUB, anchor="e")
            by = gy + px(40)
            bw = pw - pad * 2
            g.rect(x0 + pad, by, bw, px(3), P_TRACK)
            pct = max(0.0, min(1.0, float(grp.get("pct") or 0) / 100.0))
            if pct > 0:
                g.rect(x0 + pad, by, int(bw * pct), px(3), GOLD)
            mx = x0 + pad
            for m in (grp.get("members") or []):
                t = self._fit(g, str(m.get("title") or "칭호 없음"), FONT, s10, False, px(110))
                ch = ""          # 「N성부」 꼬리는 뺐다 — 의미 없다
                mw = g.measure(t, FONT, s10) + px(14)
                if mx + mw > x0 + pw - pad:
                    break
                self._card(g, mx, by + px(12), mw, px(18), px(9), "#111a26", P_LINE, 255)
                g.text(mx + px(7), by + px(21), t, FONT, s10, P_CHIP_FG)
                if ch:
                    g.text(mx + px(7) + g.measure(t, FONT, s10), by + px(21), ch, MONO, s10, P_SUB)
                mx += mw + px(5)
            gy += gh
            g.line(x0 + px(1), gy - px(9), x0 + pw - px(2), gy - px(9), P_ROWLINE)
        if len(groups) > room:
            g.text(x0 + pad, gy + px(9), f"… 그리고 {len(groups) - room}팀 더", FONT, s10, P_SUB)

    def _draw_detail(self, g, d, px, w, top) -> None:
        """패널 본체 — 위 도구줄 · 왼쪽 목록 · 오른쪽 기둥 · 아래 손잡이줄."""
        pw = min(w, px(DESIGN_PANEL_W))
        x0 = w - pw                                   # 알약·버튼 쪽(오른쪽)에 붙인다
        ph = self._panel_h(px)
        tb, ch, rh, ft = px(DESIGN_TOPBAR), px(DESIGN_CHIPS), px(DESIGN_ROW), px(DESIGN_FOOT)
        side = px(DESIGN_SIDE)
        self._card(g, x0, top, pw, ph, px(14), P_BG, P_LINE, max(self._bg_alpha(), 235))
        s11, s12, s10 = px(11), px(12), px(10)

        # 1) 위 도구줄
        cy = top + tb // 2
        x = x0 + px(10)
        x = self._chip(g, x, cy, "위치 고정", self._locked, px, lambda: self._toggle_lock(), icon="⌾")
        x = self._chip(g, x, cy, "스마트 관통", self._through, px, lambda: self._set_through(not self._through),
                       icon="↗", keep=True)      # 관통 중에도 이것만은 눌린다 (되돌릴 길)
        x = self._chip(g, x, cy, "초기화", False, px, lambda: self.move_default(), icon="↺")
        # 지금 보고 있는 목록을 통째로 대기열로 — 미니 창의 「▶ 전체 재생」과 같은 일
        x = self._chip(g, x, cy, "전체 재생", False, px, lambda: self._play_view_all(), icon="▶")
        # 이 ✕ 는 **상세창만 접는다**. 오버레이를 통째로 닫는 것은
        # 플레이어 왼쪽의 세로 띠가 맡는다 — 둘을 헷갈리지 않게 갈라 두었다.
        xw = px(26)
        xx = x0 + pw - px(10) - xw
        self._card(g, xx, cy - xw // 2, xw, xw, xw // 2, None, P_LINE, 255)
        self._ctext(g, xx + xw / 2.0, cy, "✕", FONT, s11, P_CHIP_FG)
        self._hit(xx, cy - xw // 2, xw, xw, lambda: self._set_open(False), keep=True)

        # 악기·장착은 **오른쪽 기둥 아래**(「창 열기」 바로 위)로 내렸다.
        # 윗줄 오른쪽 끝에 두었더니 왼쪽 단추들이 길어질 때 「전체 재생」을 덮었다.
        insts = [str(i.get("name") or "") for i in self._lib_get()["instruments"] if isinstance(i, dict)]
        # 악기는 **미니 창과 한 몸**이다. 게임이 알려 주는 「지금 든 악기」를 따라간다 —
        # 사람이 이 창에서 따로 고른 뒤에는 그 선택을 지킨다(_inst_pick).
        now_inst = str(self._data.get("inst") or "")
        if now_inst and now_inst != self._inst_seen:
            self._inst_seen = now_inst
            self._inst = now_inst                       # 밖에서 바뀌었다 — 따라간다
        if not self._inst:
            self._inst = now_inst or next((str(i.get("name") or "") for i in self._lib_get()["instruments"]
                                           if isinstance(i, dict) and i.get("equipped")), "")
        # 지금 곡 — 아래 손잡이줄과 오른쪽 기둥이 같이 쓴다 (탭과 상관없이 늘 필요하다)
        with self._lock:
            song = self._data.get("song") or self._data.get("title") or "대기"
            tot, el = self._data.get("tot") or 0, self._data.get("el") or 0

        # 탭 띠 — 재생목록 / 주변 연주
        ty = top + tb
        th = px(DESIGN_TABS)
        g.line(x0 + px(1), ty, x0 + pw - px(2), ty, P_DIV)
        near = self._near_get()
        n_near = len(near.get("groups") or ())
        n_q = len(self._q_get().get("items") or ())
        half = pw // 2
        tcy = ty + th // 2
        for i, (key, label, badge) in enumerate((("queue", "재생목록", str(n_q) if n_q else ""),
                                                 ("near", "주변 연주", str(n_near) if n_near else ""))):
            on = self._ptab == key
            bx = x0 + i * half
            tw = g.measure(label, FONT, s12, on)
            bw = (g.measure(badge, MONO, s10) + px(10)) if badge else 0
            sx_t = bx + (half - tw - bw) // 2
            g.text(sx_t, tcy, label, FONT, s12, (GOLD if on else P_SUB), bold=on)
            if badge:
                if key == "near":            # 주변 인원은 금색 배지
                    g.round_rect(sx_t + tw + px(5), tcy - px(8), bw - px(2), px(16), px(8), fill=GOLD)
                    self._ctext(g, sx_t + tw + px(5) + (bw - px(2)) / 2, tcy, badge, MONO, s10, "#1a1608", True)
                else:
                    g.text(sx_t + tw + px(5), tcy, badge, MONO, s10, P_SUB)
            if on:
                g.rect(bx, ty + th - px(2), half, px(2), GOLD)
            self._hit(bx, ty, half, th, (lambda k: (lambda: self._set_tab(k)))(key))

        if self._ptab == "near":
            self._draw_near(g, px, x0, pw, ty + th, ph - (ty + th - top) - ft, near)
        elif self._menu is not None:
            # 악기 고르기 — 목록·오른쪽 기둥 자리를 **통째로 덮는 판**으로 연다.
            # 밑의 목록은 아예 그리지 않는다: 그려 두면 판의 빈틈을 누를 때 뒤의 곡이 틀어진다.
            self._draw_picker(g, px, x0, pw, ty + th, (top + ph - ft) - (ty + th), insts)
        else:
            # 2) 가운데 — 왼쪽 목록 (탭 띠 아래에서 시작한다)
            my = ty + th
            lw = pw - side
            rows = self._rows()
            ccy = my + ch // 2
            lib = self._lib_get()
            n_recent = sum(1 for it in lib["scores"] if it.get("lastPlayed"))
            n_solo = sum(1 for it in lib["scores"] if (it.get("ens") or 0) == 1)
            n_ens = sum(1 for it in lib["scores"] if (it.get("ens") or 0) >= 2)
            # 알약 줄 — 재생목록까지 **전부** 그린다. 넘치면 좌우로 끌어서 굴린다 (미니와 같게).
            # 줄 밖은 잘라 내고, 잘린 알약은 눌리지 않게 한다.
            self._chiprow = (x0, my, lw, ch)
            span = (x0, x0 + lw)
            g.clip(x0, my, lw, ch)
            cx = x0 + px(10) + int(self._chipx)
            q = self._q_get()
            if q.get("items"):                       # 지금 틀고 있는 차례 = 현재 재생목록
                cx = self._chip(g, cx, ccy, "현재 재생목록", self._pview[0] == "queue", px,
                                lambda: self._set_view("queue", None), count=str(len(q["items"])), span=span)
            cx = self._chip(g, cx, ccy, "전체", self._pview[0] == "lib", px,
                            lambda: self._set_view("lib", None), count=str(len(lib["scores"])), span=span)
            cx = self._chip(g, cx, ccy, "최근", self._pview[0] == "recent", px,
                            lambda: self._set_view("recent", None), count=str(n_recent), span=span)
            cx = self._chip(g, cx, ccy, "솔로", self._pview[0] == "solo", px,
                            lambda: self._set_view("solo", None), count=str(n_solo), span=span)
            cx = self._chip(g, cx, ccy, "합주", self._pview[0] == "ens", px,
                            lambda: self._set_view("ens", None), count=str(n_ens), span=span)
            for pl in lib["playlists"]:
                pid = pl.get("id")
                cx = self._chip(g, cx, ccy, self._fit(g, str(pl.get("name") or ""), FONT, px(11), False, px(90)),
                                self._pview == ("pl", pid), px, (lambda q: (lambda: self._set_view("pl", q)))(pid),
                                count=str(len(pl.get("items") or ())), span=span)
            self._chipw = cx - int(self._chipx) - x0 + px(4)     # 줄 전체 길이 (굴릴 수 있는 폭을 잰다)
            over = max(0, self._chipw - lw)
            self._chipx = min(0, max(-over, int(self._chipx)))   # 양 끝을 넘겨 굴리지 않는다
            if over:              # 한 화면씩 옮기는 **단추**. 끌어서도 되지만 단추가 확실하다
                step = max(px(60), int(lw * 0.6))
                for side_x, glyph, show, delta in ((x0 + lw - px(16), "›", self._chipx > -over, -step),
                                                   (x0 + px(1), "‹", self._chipx < 0, step)):
                    if not show:
                        continue
                    g.round_rect(side_x, ccy - px(12), px(15), px(24), px(6), fill=P_BG, alpha=240,
                                 outline=GOLD_DIM, width=1)
                    g.icon(side_x + px(7), ccy, glyph, FONT, px(13), GOLD)
                    self._hit(side_x, ccy - px(12), px(15), px(24),
                              (lambda d: (lambda: self._chip_scroll(d)))(delta))
            g.clip_off()
            ly = my + ch
            g.line(x0 + px(1), ly, x0 + lw, ly, P_ROWLINE)
            with self._lock:
                now_title = self._data.get("title") or ""
            top_i = max(0, min(self._pscroll, max(0, len(rows) - PANEL_ROWS)))
            self._pscroll = top_i
            for n in range(PANEL_ROWS):
                i = top_i + n
                y = ly + n * rh
                if i >= len(rows):
                    break
                it = rows[i]
                playing = (it.get("title") or "") == now_title and now_title
                if playing:
                    g.rect(x0 + px(1), y, lw - px(1), rh, "#141b16")
                rcy = y + rh // 2
                n_ens_row = int(it.get("ens") or 0)
                cf = (GOLD if n_ens_row <= 1 else ("#7fe0bd" if n_ens_row <= 3 else "#9dbcf2")) if n_ens_row else P_SUB
                g.round_rect(x0 + px(10), rcy - px(11), px(22), px(22), px(6),
                             fill="#182231", outline=(P_LINE if not n_ens_row else cf), width=1)
                if n_ens_row:
                    self._ctext(g, x0 + px(21), rcy, str(n_ens_row), FONT, px(11), cf, True)
                else:
                    g.icon(x0 + px(21), rcy, "♪", FONT, px(11), GOLD if playing else P_SUB)
                tx = x0 + px(40)
                avail = lw - px(40) - px(52)
                sub = str(it.get("artist") or "")
                g.text(tx, rcy - (px(7) if sub else 0),
                       self._fit(g, str(it.get("song") or it.get("title") or ""), FONT, s12, True, avail),
                       FONT, s12, TITLE_FG if playing else P_INK, bold=True)
                if sub:
                    g.text(tx, rcy + px(8), self._fit(g, sub, FONT, s10, False, avail), FONT, s10, P_SUB)
                dur = it.get("duration")
                g.text(x0 + lw - px(10), rcy, _fmt(dur) if dur else "--:--", MONO, s10, P_SUB, anchor="e")
                g.line(x0 + px(1), y + rh, x0 + lw, y + rh, P_ROWLINE)
                self._hit(x0, y, lw, rh, (lambda k: (lambda: self._play_row(k)))(i))
            if not rows:
                g.text(x0 + px(14), ly + rh, "악보가 없습니다 — 미니 창에서 「갱신」을 누르세요", FONT, s11, P_SUB)
            # 스크롤 막대
            if len(rows) > PANEL_ROWS:
                track_h = PANEL_ROWS * rh
                bar = max(px(18), int(track_h * PANEL_ROWS / len(rows)))
                by = ly + int((track_h - bar) * top_i / max(1, len(rows) - PANEL_ROWS))
                g.round_rect(x0 + lw - px(4), by, px(3), bar, px(2), fill="#2a3644")

            # 3) 가운데 — 오른쪽 기둥
            sx = x0 + lw
            g.rect(sx, my, side, ch + PANEL_ROWS * rh, P_SIDE_BG)
            g.line(sx, my, sx, my + ch + PANEL_ROWS * rh, P_DIV)
            with self._lock:
                song = self._data.get("song") or self._data.get("title") or "대기"
                artist = self._data.get("artist") or ""
                tot, el = self._data.get("tot") or 0, self._data.get("el") or 0
                ens = self._data.get("ens") or 0
                playing = bool(self._data.get("playing"))
            sy = my + px(12)
            g.text(sx + px(12), sy, self._fit(g, song, FONT, px(13), True, side - px(24)), FONT, px(13), "#ffffff", bold=True)
            sub = " · ".join(v for v in (artist, f"{ens}인 합주" if ens else "") if v)
            if sub:
                g.text(sx + px(12), sy + px(16), self._fit(g, sub, FONT, s10, False, side - px(24)), FONT, s10, P_SUB)
            gy = sy + px(34)
            g.rect(sx + px(12), gy, side - px(24), px(3), P_TRACK)
            if tot:
                g.rect(sx + px(12), gy, int((side - px(24)) * min(1.0, el / tot)), px(3), GOLD)
            g.text(sx + side - px(12), gy + px(12), f"{_fmt(el)} / {_fmt(tot)}", MONO, s10, ARTIST_FG, anchor="e")
            # 이전 · 재생/정지 · 다음 (왼쪽) — 반복 · 셔플 · 연출 (오른쪽).
            by2 = gy + px(34)
            bx = sx + px(12)
            for glyph, fn, big in (("◀◀", lambda: self._step(-1), False),
                                   ("■" if playing else "▶", self._toggle_play, True),
                                   ("▶▶", lambda: self._step(1), False)):
                dd = px(30) if big else px(24)
                if big:
                    g.ellipse(bx, by2 - dd // 2, dd, dd, GOLD)
                else:
                    g.round_rect(bx, by2 - dd // 2, dd, dd, dd // 2, outline=P_LINE, width=1)
                g.icon(bx + dd / 2, by2, glyph, FONT, px(11) if big else px(9),
                       ON_GOLD if big else P_CHIP_FG)
                self._hit(bx, by2 - dd // 2, dd, dd, fn)
                bx += dd + px(7)
            rx = sx + side - px(12)
            # 오른쪽 끝에서 왼쪽으로 그리므로 순서(반복·셔플·연출)의 역순으로 돈다
            for what, on, fn in (("spark", self._cfg.get("opening") is not False, self._toggle_opening),
                                 ("shuffle", self._shuffle, self._toggle_shuffle),
                                 ("repeat", self._repeat != "off", self._cycle_repeat)):
                dd = px(24)
                rx -= dd
                self._round_btn(g, rx, by2 - dd // 2, dd, on)
                self._ctl_icon(g, self._ctl_kind(what), rx + dd / 2.0, by2, px(15) if what == "spark" else px(13),
                               GOLD if on else P_CHIP_FG)
                self._hit(rx, by2 - dd // 2, dd, dd, fn)
                rx -= px(5)
            # 다음 곡 (악기 줄보다 먼저 — 악기는 아래 「창 열기」 바로 위로 내렸다)
            ny = by2 + px(24)
            g.line(sx + px(12), ny, sx + side - px(12), ny, P_DIV)
            g.text(sx + px(12), ny + px(12), "다음 곡", FONT, px(9), P_SUB)
            nxt = self._next_row()
            if nxt:
                nn = int(nxt.get("ens") or 0)
                g.round_rect(sx + px(12), ny + px(20), px(18), px(18), px(5),
                             fill="#1b2433", outline="#3b4a63", width=1)
                if nn:
                    self._ctext(g, sx + px(21), ny + px(29), str(nn), FONT, px(9), "#9dbcf2", True)
                else:
                    g.icon(sx + px(21), ny + px(29), "♪", FONT, px(9), "#9dbcf2")
                nd = nxt.get("duration")
                g.text(sx + side - px(12), ny + px(29), _fmt(nd) if nd else "--:--", MONO, s10, P_SUB, anchor="e")
                g.text(sx + px(36), ny + px(29),
                       self._fit(g, str(nxt.get("song") or nxt.get("title") or ""), FONT, s11, False, side - px(92)),
                       FONT, s11, P_INK)
            else:
                g.text(sx + px(12), ny + px(29), "—", FONT, s11, P_SUB)

            # 악기 고르기 + 장착 — 「다음 곡」 아래, 「창 열기」 바로 위에 **한 줄로**.
            iy = top + ph - ft - px(38)
            eq_w = g.measure("장착", FONT, s11, True) + px(20)
            pn = px(28)                                   # 악기 고정 단추 — 「장착」 오른쪽
            pnx = sx + side - px(12) - pn
            self._pin_btn(g, pnx, iy, pn, px)
            ex = pnx - px(5) - eq_w
            self._card(g, ex, iy, eq_w, px(28), px(8), "#3a3421", GOLD, 255)
            self._ctext(g, ex + eq_w / 2, iy + px(14), "장착", FONT, s11, GOLD, True)
            self._hit(ex, iy, eq_w, px(28), self._equip)
            ix = sx + px(12)
            iw = ex - px(7) - ix
            self._card(g, ix, iy, iw, px(28), px(8), "#111a26", P_LINE, 255)
            g.text(ix + px(8), iy + px(14), "악기", FONT, s10, P_SUB)
            g.text(ix + px(8) + g.measure("악기", FONT, s10) + px(7), iy + px(14),
                   self._fit(g, self._shown_inst() or "악기 그대로", FONT, s11, False, iw - px(52)),
                   FONT, s11, GOLD if self._inst_pin else P_INK)
            g.icon(ix + iw - px(8), iy + px(14), "▾", FONT, s10, P_SUB, anchor="e")
            self._hit(ix, iy, iw, px(28), lambda: self._toggle_menu())

        # 4) 아래 손잡이줄
        fy = top + ph - ft
        g.line(x0 + px(1), fy, x0 + pw - px(2), fy, P_DIV)
        fcy = fy + ft // 2
        g.icon(x0 + px(14), fcy, "⋮⋮", FONT, s11, HANDLE)
        cxx = x0 + px(30)
        g.round_rect(cxx, fcy - px(13), px(26), px(26), px(13), outline=P_LINE, width=1)
        g.icon(cxx + px(13), fcy, "▾", FONT, s11, GOLD)
        self._hit(cxx, fcy - px(13), px(26), px(26), lambda: self._set_open(False))
        w_ico = g.measure("⧉", FONT, s11)
        ow = w_ico + g.measure("창 열기", FONT, s11) + px(31)
        ox = x0 + pw - px(10) - ow
        tx2 = cxx + px(34)
        clock2 = f"{_fmt(el)} / {_fmt(tot)}"
        w_clock2 = g.measure(clock2, MONO, s11)
        avail2 = max(px(40), ox - px(10) - tx2 - w_clock2 - px(8))
        tw = g.text(tx2, fcy, self._fit(g, song, FONT, s12, True, avail2), FONT, s12, TITLE_FG, bold=True)
        g.text(tx2 + tw + px(8), fcy, clock2, MONO, s11, ARTIST_FG)
        self._card(g, ox, fcy - px(12), ow, px(24), px(12), None, P_LINE, 255)
        g.icon(ox + px(10) + w_ico / 2.0, fcy, "⧉", FONT, s11, P_CHIP_FG)
        g.text(ox + px(10) + w_ico + px(5), fcy, "창 열기", FONT, s11, P_CHIP_FG)
        self._hit(ox, fcy - px(12), ow, px(24), lambda: self._open_app())

    # ── 악기 고르기 (select 대신 종류 탭 + 검색) ──
    PK_ROW = 26                     # 목록 한 줄 (디자인 값이 없어 예전 팝업 줄 높이를 그대로 쓴다)
    PK_SEARCH = 38                  # 검색 줄
    PK_FOOT = 22                    # 맨 아래 「n / 전체」 줄
    PK_HINT = "악기 이름 검색 — 띄어 쓰면 모두 들어간 것만"

    def _draw_picker(self, g, px, x0, pw, y0, h, insts) -> None:
        """악기 고르기 판 — 상세창의 목록·기둥 자리를 통째로 덮는다 (판보다 넓어지지 않는다).

        1줄: 종류 탭 (「전체」 + 이름 앞 [종류] 마다, 개수) — 넘치면 옆으로 굴린다 · 오른쪽 끝 ✕
        2줄: 검색칸 · 「장착」. 검색칸에는 진짜 입력칸(tk.Entry)을 따로 띄워 얹는다 (_pk_sync) —
             이 판은 그림이라 글자를 받을 수 없고, 한글 조합(IME)은 진짜 입력칸이어야 된다.
        3줄~: 거른 목록 (휠 · 막대). 지금 든 악기는 금색, ↑/↓ 로 고른 줄은 바탕색.
        맨 아래: 「거른 수 / 전체 · 휠로 넘기기」 · 조작 안내.
        판 안의 단추는 전부 keep — 스마트 관통 중에 열려 있어도 눌린다 (연 것은 사람이다).
        """
        s10, s11, s12 = px(10), px(11), px(12)
        pad = px(10)
        self._pk_rect = (x0, y0, pw, h)

        # 1) 종류 탭 줄
        tabs = inst_picker.tabs(insts)
        fam = self._pk_fam = inst_picker.valid_tab(insts, self._pk_fam)
        ch = px(DESIGN_CHIPS)
        ccy = y0 + ch // 2
        xw = px(24)
        xx = x0 + pw - pad - xw
        self._card(g, xx, ccy - xw // 2, xw, xw, xw // 2, None, P_LINE, 255)
        self._ctext(g, xx + xw / 2.0, ccy, "✕", FONT, s11, P_CHIP_FG)
        self._hit(xx, ccy - xw // 2, xw, xw, self._close_menu, keep=True)
        lw = xx - px(6) - x0                          # 탭이 쓸 수 있는 폭 (✕ 앞까지)
        self._chiprow = (x0, y0, lw, ch)              # 여기서 끌면 탭 줄이 옆으로 구른다
        span = (x0, x0 + lw)
        g.clip(x0, y0, lw, ch)
        cx = x0 + pad + int(self._pk_chipx)
        for name, n in tabs:
            cx = self._chip(g, cx, ccy, name, name == fam, px, (lambda f: (lambda: self._pk_set_fam(f)))(name),
                            count=str(n), keep=True, span=span)
        full = cx - int(self._pk_chipx) - x0 + px(4)
        over = max(0, full - lw)
        self._pk_chipx = min(0, max(-over, int(self._pk_chipx)))
        if over:                                      # 상세창 알약 줄과 같은 ‹ › 단추
            step = max(px(60), int(lw * 0.6))
            for side_x, glyph, show, delta in ((x0 + lw - px(16), "›", self._pk_chipx > -over, -step),
                                               (x0 + px(1), "‹", self._pk_chipx < 0, step)):
                if not show:
                    continue
                g.round_rect(side_x, ccy - px(12), px(15), px(24), px(6), fill=P_BG, alpha=240,
                             outline=GOLD_DIM, width=1)
                g.icon(side_x + px(7), ccy, glyph, FONT, px(13), GOLD)
                self._hit(side_x, ccy - px(12), px(15), px(24),
                          (lambda d: (lambda: self._pk_chip_scroll(d)))(delta), keep=True)
        g.clip_off()
        g.line(x0 + px(1), y0 + ch, x0 + pw - px(2), y0 + ch, P_ROWLINE)

        # 2) 검색칸 · 장착
        sy = y0 + ch
        sh = px(self.PK_SEARCH)
        bh = px(28)
        by = sy + (sh - bh) // 2
        eq_w = g.measure("장착", FONT, s11, True) + px(20)
        pnx = x0 + pw - pad - bh                      # 악기 고정 단추 — 「장착」 바로 오른쪽
        self._pin_btn(g, pnx, by, bh, px, keep=True)
        ex = pnx - px(5) - eq_w
        self._card(g, ex, by, eq_w, bh, px(8), "#3a3421", GOLD, 255)
        self._ctext(g, ex + eq_w / 2, by + bh / 2.0, "장착", FONT, s11, GOLD, True)
        self._hit(ex, by, eq_w, bh, self._equip, keep=True)
        bx = x0 + pad
        bw = ex - px(7) - bx
        self._card(g, bx, by, bw, bh, px(8), "#111a26", GOLD_DIM, 255)
        r = px(4)                                     # 돋보기 — 글리프는 글꼴마다 달라 직접 그린다
        mcx, mcy = bx + px(13), by + bh / 2.0 - px(1)
        g.arc(mcx - r, mcy - r, 2 * r, 2 * r, 0, 360, P_SUB, width=max(1.0, px(1.4)))
        g.line(mcx + r * 0.7, mcy + r * 0.7, mcx + r * 1.7, mcy + r * 1.7, P_SUB, width=max(1.0, px(1.4)))
        tx = bx + px(26)
        # 글자 자리 — 진짜 입력칸이 여기에 얹힌다. 입력칸이 못 뜨더라도 친 글자는 보이게 밑에도 그려 둔다.
        self._pk_box = (tx, by + px(4), max(px(40), bx + bw - px(8) - tx), bh - px(8))
        self._pk_fpx = s12
        if self._pk_q:
            g.text(tx, by + bh / 2.0, self._fit(g, self._pk_q, FONT, s12, False, bw - px(34)), FONT, s12, P_INK)
        else:
            g.text(tx, by + bh / 2.0, self._fit(g, self.PK_HINT, FONT, s11, False, bw - px(34)), FONT, s11, P_SUB)
        g.line(x0 + px(1), sy + sh, x0 + pw - px(2), sy + sh, P_ROWLINE)

        # 3) 목록
        ly = sy + sh
        fh = px(self.PK_FOOT)
        rh = px(self.PK_ROW)
        rows = max(1, (y0 + h - fh - ly) // rh)
        lst = inst_picker.filter_insts(insts, fam, self._pk_q)
        n = len(lst)
        self._pk_list, self._pk_rows = lst, rows
        self._pk_hi = inst_picker.move(self._pk_hi, n, 0)
        off = max(0, min(int(self._menu or 0), max(0, n - rows)))
        self._menu = off
        rw = pw - px(14)                              # 오른쪽 막대 자리를 남긴다
        for k, name in enumerate(lst[off:off + rows]):
            i = off + k
            yy = ly + k * rh
            on = name == self._shown_inst()
            if i == self._pk_hi:
                g.rect(x0 + px(4), yy, rw - px(4), rh, P_SEL_BG)
            kind, bare = inst_picker.split(name)
            tx = x0 + px(14)
            if kind:                                  # 「[종류] 이름」 — 뒤가 잘려도 종류는 남는다
                tag = f"[{kind}]"
                g.text(tx, yy + rh / 2.0, tag, FONT, s11, GOLD if on else GOLD_DIM)
                tx += g.measure(tag, FONT, s11) + px(6)
            g.text(tx, yy + rh / 2.0, self._fit(g, bare, FONT, s11, on, x0 + rw - px(8) - tx), FONT, s11,
                   GOLD if on else P_INK, bold=on)
            self._hit(x0 + px(4), yy, rw - px(4), rh, (lambda nm: (lambda: self._pick_inst(nm)))(name), keep=True)
        if not n:
            msg = ("검색에 맞는 악기가 없습니다" if (self._pk_q.strip() or fam != inst_picker.ALL)
                   else "악기 목록이 없습니다 — 미니 창에서 「갱신」을 누르세요")
            g.text(x0 + px(14), ly + rh / 2.0 + px(4), msg, FONT, s11, P_SUB)
        if n > rows:                                  # 막대 — 누른 자리로 뛴다 (휠로도 굴린다)
            track = rows * rh
            tx2 = x0 + pw - px(9)
            bar = max(px(16), int(track * rows / n))
            bpos = ly + int((track - bar) * off / max(1, n - rows))
            g.round_rect(tx2, ly, px(3), track, px(2), fill=P_ROWLINE)
            g.round_rect(tx2, bpos, px(3), bar, px(2), fill="#2a3644")
            for k in range(rows):
                frac = k / max(1, rows - 1)
                self._hit(tx2 - px(4), ly + k * rh, px(11), rh,
                          (lambda f: (lambda: self._pk_jump(f)))(frac), keep=True)

        # 맨 아래 줄
        fy = y0 + h - fh
        g.line(x0 + px(1), fy, x0 + pw - px(2), fy, P_ROWLINE)
        total = sum(c for _, c in inst_picker.families(insts))
        g.text(x0 + px(14), fy + fh / 2.0, f"{n} / {total}" + (" · 휠로 넘기기" if n > rows else ""),
               FONT, s10, P_SUB)
        g.text(x0 + pw - px(12), fy + fh / 2.0, "↑↓ 고르기 · Enter 장착 · Esc 닫기", FONT, s10, P_SUB, anchor="e")

    def _pin_btn(self, g, x, y, d, px, keep=False) -> None:
        """악기 고정 단추 (정사각 d). 켜져 있으면 금색 바탕 + 금색 핀 — 셔플·반복 단추와 같은 말투."""
        on = bool(self._inst_pin)
        if on:
            self._card(g, x, y, d, d, px(8), "#3a3421", GOLD, 255)
        else:
            self._card(g, x, y, d, d, px(8), "#111a26", P_LINE, 255)
        self._ctl_icon(g, "pin", x + d / 2.0, y + d / 2.0, px(14), GOLD if on else P_CHIP_FG)
        self._hit(x, y, d, d, self._toggle_pin, keep=keep)

    def _pk_names(self) -> list:
        return [str(i.get("name") or "") for i in self._lib_get()["instruments"] if isinstance(i, dict)]

    def _pk_set_fam(self, fam: str) -> None:
        """종류 탭 — 그 종류만 보인다. 맨 위부터 다시."""
        self._pk_fam = fam
        self._pk_hi = 0
        if self._menu is not None:
            self._menu = 0

    def _pk_chip_scroll(self, d: int) -> None:
        self._pk_chipx = int(self._pk_chipx) + int(d)

    def _pk_jump(self, frac: float) -> None:
        """막대를 누른 자리로 — 0 이면 맨 위, 1 이면 맨 아래."""
        top = max(0, len(self._pk_list) - self._pk_rows)
        self._menu = int(round(max(0.0, min(1.0, frac)) * top))

    def _pk_query(self, q: str) -> None:
        """검색어가 바뀌었다 — 거른 목록의 맨 위부터 다시 본다."""
        q = str(q or "")
        if q == self._pk_q:
            return
        self._pk_q = q
        self._pk_hi = 0
        if self._menu is not None:
            self._menu = 0

    def _pk_key(self, what: str) -> None:
        """입력칸의 키 — ↑/↓ 줄 고르기 · PgUp/PgDn · Enter 장착 · Esc 닫기."""
        n = len(self._pk_list)
        if what == "esc":
            self._close_menu()
        elif what == "enter":
            if n and self._menu is not None:
                self._pick_inst(self._pk_list[inst_picker.move(self._pk_hi, n, 0)])
        elif what in ("up", "down", "pgup", "pgdn") and self._menu is not None:
            d = {"up": -1, "down": 1, "pgup": -self._pk_rows, "pgdn": self._pk_rows}[what]
            self._pk_hi = inst_picker.move(self._pk_hi, n, d)
            self._menu = inst_picker.keep_visible(int(self._menu or 0), self._pk_hi, n, self._pk_rows)
        self._redraw()

    def _pick_inst(self, name) -> None:
        """고르면 그 자리에서 바꿔 든다. 고르기만 하고 「장착」을 또 눌러야 하면 안 바뀐 것처럼 보인다."""
        self._inst = name
        if self._inst_pin and name != self._inst_pin:   # 고정 중에 다른 악기를 고르면 고정도 그 악기로 옮긴다
            self._inst_pin = name
            self._put_cfg("inst_pin", name)
            self._qat = 0.0
        self._close_menu()
        self._equip()

    def _toggle_menu(self) -> None:
        """「악기」 단추 — 고르기 판을 연다 (열려 있으면 닫는다). 열 때마다 검색어는 비우고,
        지금 든 악기가 보이는 자리에서 시작한다. 종류 탭은 이번 실행 동안 기억한다."""
        if self._menu is not None:
            self._close_menu()
            return
        var = getattr(self, "_pk_var", None)
        if var is not None:
            try:
                var.set("")                  # 먼저 비운다 — 입력칸의 알림이 아래 상태를 덮지 않게
            except Exception:
                pass
        self._pk_q = ""
        names = self._pk_names()
        self._pk_fam = inst_picker.valid_tab(names, self._pk_fam)
        lst = inst_picker.filter_insts(names, self._pk_fam, "")
        cur = self._shown_inst()                         # 고정 중이면 고정한 악기에서 시작한다
        if cur and cur not in lst:                       # 지금 든 악기가 이 탭에 없으면 「전체」에서 보여 준다
            self._pk_fam = inst_picker.ALL
            lst = inst_picker.filter_insts(names, self._pk_fam, "")
        self._pk_hi = lst.index(cur) if cur in lst else 0
        self._pk_list = lst
        self._menu = inst_picker.keep_visible(0, self._pk_hi, len(lst), self._pk_rows)

    def _close_menu(self) -> None:
        self._menu = None
        self._pk_sync()

    # 검색 입력칸 창 — 판은 GDI+ 그림(레이어 창)이라 글자를 못 받는다. 진짜 tk.Entry 를 담은 테두리 없는
    # 작은 창을 검색칸 자리에 정확히 얹고, 고르기가 열려 있는 동안만 띄운다. 이 창만은 NOACTIVATE 가 아니다 —
    # 키보드(한글 조합 포함)를 받으려면 앞창(포그라운드)이 되어야 한다. 닫으면 게임에 앞창을 돌려준다.
    def _pk_ensure(self):
        if self._pk_win is not None:
            return self._pk_win or None
        try:
            import tkinter as tk
            win = tk.Toplevel(self.root)
            win.withdraw()
            win.overrideredirect(True)
            win.attributes("-topmost", True)          # 연주 패널(레이어 창) 위에 떠야 보인다 — 열린 동안만
            win.configure(bg="#111a26")
            var = tk.StringVar(master=win)
            font = (FONT, -max(8, int(getattr(self, "_pk_fpx", 12))))
            e = tk.Entry(win, textvariable=var, relief="flat", bd=0, highlightthickness=0,
                         bg="#111a26", fg=P_INK, insertbackground=GOLD, insertwidth=2,
                         selectbackground="#3a3421", selectforeground="#ffffff", font=font)
            e.pack(fill="both", expand=True)
            hint = tk.Label(win, text=self.PK_HINT, bg="#111a26", fg=P_SUB, font=font, anchor="w", bd=0, padx=0)
            hint.place(x=4, y=0, relwidth=1, relheight=1)
            hint.bind("<Button-1>", lambda _e: e.focus_set())

            def typed(*_a):
                q = var.get()
                if q:
                    hint.place_forget()
                else:
                    hint.place(x=4, y=0, relwidth=1, relheight=1)
                if q != self._pk_q:
                    self._pk_query(q)
                    self._redraw()
            var.trace_add("write", typed)
            e.bind("<KeyPress>", lambda ev: (hint.place_forget() if ev.keysym not in (
                "Up", "Down", "Prior", "Next", "Escape", "Return", "KP_Enter") else None))
            for seq, what in (("<Escape>", "esc"), ("<Return>", "enter"), ("<KP_Enter>", "enter"),
                              ("<Up>", "up"), ("<Down>", "down"), ("<Prior>", "pgup"), ("<Next>", "pgdn")):
                e.bind(seq, (lambda w: (lambda _e: (self._pk_key(w), "break")[1]))(what))
            # 입력칸 위의 휠도 목록을 굴린다 (Tk 는 휠을 포커스 창으로 보내기도 한다 — 그때도 같은 곳으로)
            win.bind("<MouseWheel>", lambda ev: self._wheel(ev.delta))
            win.update_idletasks()
            u = ctypes.windll.user32
            wid = win.winfo_id()
            self._pk_hwnd = int(u.GetAncestor(wid, _GA_ROOT) or u.GetParent(wid) or wid)
            self._pk_win, self._pk_var, self._pk_entry, self._pk_hintw = win, var, e, hint
        except Exception as ex:
            self._log(f"[overlay] 악기 검색칸을 못 만들었습니다: {ex}")
            self._pk_win = False                       # 다시 시도하지 않는다 (판의 탭·목록은 그대로 쓴다)
            return None
        return win

    def _pk_sync(self) -> None:
        """검색 입력칸을 판의 검색칸 자리에 맞춘다 — 열려 있으면 그 자리에 띄우고, 아니면 내린다.

        화면 스레드에서만 부른다. 판을 얹은 **직후**(_draw_player 의 blit 뒤 · 끌기 중)에 불러야
        `_pwin` 이 새 자리다.
        """
        want = (self._menu is not None and self._popen and self._visible and not self._gone
                and self._ptab == "queue" and getattr(self, "_pshown", False) and self._pk_box is not None)
        if not want:
            if self._pk_shown:
                self._pk_hide()
            return
        win = self._pk_ensure()
        if win is None:
            return
        bx, by, bw, bh = self._pk_box
        geo = f"{int(bw)}x{int(bh)}+{int(self._pwin[0] + bx)}+{int(self._pwin[1] + by)}"
        try:
            if geo != self._pk_geo:
                self._pk_geo = geo
                win.geometry(geo)
                font = (FONT, -max(8, int(getattr(self, "_pk_fpx", 12))))
                self._pk_entry.configure(font=font)
                self._pk_hintw.configure(font=font)
            if not self._pk_shown:
                self._pk_shown = True
                win.deiconify()
                win.lift()
                self._pk_focus()
        except Exception as ex:
            self._log(f"[overlay] 악기 검색칸 자리 잡기 실패: {ex}")

    def _pk_focus(self) -> None:
        """입력칸을 앞창으로 — 게임이 앞창이면 SetForegroundWindow 가 막힐 수 있어 입력 스레드를 잠깐 잇는다.
        앞창을 가져오면 게임이 휠·키를 (raw input 으로) 읽지 않게 되는 덤도 있다."""
        h = getattr(self, "_pk_hwnd", 0)
        try:
            u, k = _fg_api()
            fg = u.GetForegroundWindow() or 0
            if h and fg != h:
                me = k.GetCurrentThreadId()
                other = u.GetWindowThreadProcessId(fg, None) if fg else 0
                att = bool(other and other != me and u.AttachThreadInput(other, me, True))
                try:
                    u.BringWindowToTop(h)
                    u.SetForegroundWindow(h)
                finally:
                    if att:
                        u.AttachThreadInput(other, me, False)
        except Exception as ex:
            self._log(f"[overlay] 악기 검색칸 포커스 실패: {ex}")
        try:
            self._pk_entry.focus_force()
            self._pk_entry.icursor("end")
        except Exception:
            pass

    def _pk_hide(self) -> None:
        """입력칸을 내리고, 앞창이 우리 입력칸이었으면 **게임에 돌려준다** (안 돌려주면 키가 안 먹는다)."""
        self._pk_shown = False
        self._pk_geo = ""
        mine = False
        try:
            u, _k = _fg_api()
            mine = bool(getattr(self, "_pk_hwnd", 0)) and (u.GetForegroundWindow() or 0) == self._pk_hwnd
        except Exception:
            u = None
        try:
            self._pk_win.withdraw()
        except Exception:
            pass
        if mine and u is not None:
            try:
                import opening_tk
                game = self._game if (self._game and opening_tk._u32().IsWindow(self._game)) else opening_tk.find_game()
                if game:
                    u.SetForegroundWindow(game)
            except Exception as ex:
                self._log(f"[overlay] 게임에 포커스를 못 돌려줬습니다: {ex}")

    def _pk_guard(self) -> None:
        """입력칸이 떠 있는데 사람이 다른 프로그램으로 갔으면 고르기를 닫는다 (40ms 루프).
        입력칸은 항상 위(-topmost)라, 안 닫으면 다른 프로그램 위에 떠 있게 된다."""
        if not self._pk_shown:
            return
        try:
            u, _k = _fg_api()
            fg = u.GetForegroundWindow() or 0
            if not fg or fg == self._game:
                return
            pid = ctypes.wintypes.DWORD(0)
            u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if pid.value == os.getpid():
                return
        except Exception:
            return
        self._close_menu()
        self._fsig = None

    def _toggle_lock(self) -> None:
        self._locked = not self._locked
        self._save(locked=self._locked)
        self._log(f"[overlay] 위치 {'고정' if self._locked else '고정 해제'}")
        self._say_hint("위치 고정 — 끌어도 안 움직입니다" if self._locked else "위치 고정 해제")

    def _band_tab(self, key: str) -> None:
        """밴드 윗변의 탭 — 접혀 있으면 그 탭으로 펼치고, 같은 탭을 다시 누르면 접는다."""
        if self._popen and self._ptab == key:
            self._set_open(False)
            return
        self._set_tab(key)
        if not self._popen:
            self._set_open(True)

    def _set_open(self, on: bool) -> None:
        self._popen = bool(on)
        self._menu = None
        self._save(popen=self._popen)
        self._band_on = None      # 띠를 띄울지 다시 판단하게 한다 (펼치면 내리고, 접으면 되살린다)
        self._fsig = None

    def _open_app(self) -> None:
        fn = self._ops.get("open_app")
        if fn:
            self._bg(fn)

    def _draw_player(self, d) -> None:
        """「폴리오 밴드 (접힘)」 — 네 줄짜리 카드 위에 골드 원이 얹힌다.

        띠(26) / 제목·합주·이퀄라이저(40) / 진행바·시간·반복·셔플·연출(37) / 다음 곡(38) = 141.
        카드가 원보다 크고, 원은 카드 오른쪽 끝에서 96 만큼 안으로 들어와 앉는다(margin-right:-96).
        크기는 전부 디자인 값 × (게임 버튼 지름 / 84).
        """
        show = self._player and self._visible
        if not show:
            if getattr(self, "_pshown", False):
                self._pshown = False
                self.pw.withdraw()
            self._pk_sync()
            return
        if not getattr(self, "_pshown", False):
            self._pshown = True
            self.pw.deiconify()
            self._apply_style_for(self._phwnd)
            self._restack = True

        def px(v):                                   # 화면 배율 (설정 배율 × DPI)
            return max(1, int(round(v * self._sc)))

        g = self.psurf
        playing = bool(d["playing"])
        # 밴드도 상세창과 같은 배율로 그린다 — 폭도 글자도 따로 놀지 않게
        k = self._sc

        def dk(v):
            return max(1, int(round(v * k)))

        # 띠 26 · 곡률 20 (패널의 14 와 다르다). 값은 위 상수에서만 온다.
        strip_h, r = dk(DESIGN_STRIP), px(DESIGN_CARD_RADIUS)
        t_sz, b_sz, m_sz, s_sz, n_sz = dk(19), dk(12), dk(12), dk(11), dk(11)
        pad_l = dk(20)
        pad_r1, pad_r2, pad_r3 = dk(118), dk(108), dk(108)   # 줄마다 다른 오른쪽 여백
        gw, gh = dk(130), dk(4)
        bd = dk(24)                                   # 작은 둥근 단추
        eq_w = 4 * dk(3) + 3 * dk(2.5)
        row1_h, row2_h, row3_h = (dk(v) for v in DESIGN_CARD_ROWS)
        card_h = strip_h + row1_h + row2_h + row3_h   # 141 (= card_height())

        title = d.get("song") or d["title"] or "대기 중"
        badge = (f"{d['ens']}인 합주" if d.get("ens") else "") if playing else "목록에서 곡을 고르세요"
        # 탈것 탑승 중 — 게임이 연주를 받지 않는다 (CLI 로는 못 내린다). 재생을 미뤄 뒀으면 「내리면 재생」.
        # 쉬는 중의 안내(「목록에서 곡을 고르세요」) 자리를 이 알약이 쓴다 — 줄 폭이 늘지 않게.
        mount = "내리면 재생" if d.get("wait_mount") else ("탈것 탑승 중" if d.get("mounted") else "")
        if mount and not playing:
            badge = ""
        tot, el = (d["tot"] or 0, d["el"] or 0) if playing else (0, 0)
        pct = min(1.0, el / tot) if tot else 0.0
        clock = f"{_fmt(el)} / {_fmt(tot)}"
        rows = self._rows() if self._ops.get("library") else []
        nxt = self._next_row()
        n_title = str((nxt or {}).get("song") or (nxt or {}).get("title") or "") if nxt else ""
        n_dur = _fmt(nxt.get("duration")) if (nxt and nxt.get("duration")) else "--:--"

        title = self._fit(g, title, FONT, t_sz, True, dk(230))     # max-width 230
        w_title = g.measure(title, FONT, t_sz, True)
        w_badge = g.measure(badge, FONT, b_sz) if badge else 0
        mw_sz, mw_h = dk(11), dk(18)                  # 경고 알약 — 글자 11 · 높이 18 (제목 줄 40 안에 앉는다)
        w_mount = (g.measure(mount, FONT, mw_sz, True) + dk(16)) if mount else 0
        w_clock = g.measure(clock, MONO, m_sz)

        # 연주 중이 아니어도 밴드는 그대로 떠 있는다. 폭은 상세창과 같게 맞춘다.
        d_btn = dk(DESIGN_BTN)
        over = dk(DESIGN_OVERLAP)                     # margin-right:-96 — 값은 위 상수에서만 온다
        pill_w = px(DESIGN_PANEL_W)                   # 상세창과 같은 폭으로 고정
        gw = max(dk(60), pill_w - pad_l - pad_r2 - dk(9) - w_clock - dk(9) - 3 * bd - 2 * dk(9))
        avail1 = pill_w - pad_l - pad_r1 - (dk(11) + w_badge if badge else 0) - dk(11) - eq_w \
            - (dk(11) + w_mount if mount else 0)      # 탈것 알약도 제목 줄에서 뺀다
        if w_title > avail1:                          # 넘치면 제목을 줄인다 (폭은 고정이다)
            title = self._fit(g, title, FONT, t_sz, True, max(dk(60), avail1))
            w_title = g.measure(title, FONT, t_sz, True)

        self._hits = []
        panel_h = self._panel_h(px) if self._popen else 0
        gap = px(DESIGN_GAP) if self._popen else 0
        # 카드가 원보다 크다 (141 > 84) — 원은 카드 안에 들어온다
        extra = max(0, (d_btn - card_h) // 2)
        h0 = card_h + 2 * extra
        w_pill = pill_w                               # 원이 카드 안이라 카드 폭이 곧 전체 폭
        close_h = px(CLOSE_H)
        top_h = close_h + px(6)                      # 카드 윗변에서 6 띄운다
        self._panel_off, self._pill_h = top_h + panel_h + gap, h0
        self._panel_dx = max(0, (px(DESIGN_PANEL_W) if self._popen else 0) - w_pill)
        self._drag_from = (top_h + panel_h - px(DESIGN_FOOT)) if self._popen else 0
        # 「전체닫기」는 **맨 위에 떠 있는 알약**이다 — 상세창을 펴면 그 위로 간다.
        # 창은 오른쪽·아래 여백으로 자리를 잡으므로, 높이를 늘리면 그만큼 **위로** 자란다.
        w = max(w_pill, px(DESIGN_PANEL_W) if self._popen else 0)
        h = top_h + panel_h + gap + h0
        g.resize(w, h)
        g.clear()
        self._sync_size(self.pw, "_psize", w, h)

        cx = w - pill_w                               # 카드 왼쪽
        ct = top_h + panel_h + gap + extra            # 카드 윗변 (맨 위는 닫기 알약 자리)
        if self._popen:
            self._detail_cached(g, d, px, w, max(0, ct - gap - panel_h))
        # 오버레이 전체 닫기 — 맨 위 오른쪽에 떠 있는 알약 (게임 UI 와 겹칠 위험이 낮고 이름이 보인다)
        z_sz = dk(11)
        z_ico = g.measure("✕", FONT, z_sz)
        zw = z_ico + dk(6) + g.measure("전체닫기", FONT, z_sz) + dk(20)
        zx = w - zw
        self._card(g, zx, 0, zw, close_h, close_h // 2, PCARD_BG, PCARD_LINE, self._bg_alpha())
        g.icon(zx + dk(10) + z_ico / 2.0, close_h / 2.0, "✕", FONT, z_sz, P_CHIP_FG)
        g.text(zx + dk(10) + z_ico + dk(6), close_h / 2.0, "전체닫기", FONT, z_sz, P_CHIP_FG)
        self._hit(zx, 0, zw, close_h, lambda: self._show(False), keep=True)
        if h0:
            alpha = self._bg_alpha()
            line_t = self._line_w()
            g.round_rect(cx, ct, pill_w, card_h, r, fill=PCARD_BG, alpha=alpha)
            # 띠는 **높이 26 을 정확히** 차지한다. 둥근 윗모서리(곡률 20)를 따라야 하므로
            # 카드와 같은 둥근 사각형을 그리되 띠 높이에서 **잘라 낸다** — 예전처럼 곡률 높이의
            # 사각형을 덧칠하면 띠가 곡률(20)에서 끝나 6px 짧았다.
            g.clip(cx, ct, pill_w, strip_h)
            g.round_rect(cx, ct, pill_w, 2 * r, r, fill=PSTRIP_BG, alpha=alpha)
            g.clip_off()
            g.line(cx, ct + strip_h, cx + pill_w, ct + strip_h, PSTRIP_LINE)
            g.round_rect(cx + line_t / 2, ct + line_t / 2, pill_w - line_t, card_h - line_t, r,
                         outline=PCARD_LINE, width=line_t)
            # 윗변은 **두 칸짜리 탭 띠**다: 「▴ 재생목록」 · 「▴ 주변 연주 N」.
            # 접혀 있으면 누른 쪽 탭으로 펼치고, 펼쳐져 있으면 같은 쪽을 다시 눌러 접는다.
            # 접혀 있을 때 이 띠는 관통 칩까지 가는 유일한 길이라 keep 을 붙인다.
            near_n = len(self._near_get().get("groups") or ())
            half = pill_w / 2.0
            for i, (key, label) in enumerate((("queue", "재생목록"), ("near", "주변 연주"))):
                bx = cx + i * half
                on = self._ptab == key
                if on:
                    # 고른 탭의 옅은 골드(rgba(226,184,102,.1))는 띠 **전체 높이**다 — 곡률 아래
                    # 조각만 칠하던 것을 잘라 내기(clip)로 바꿨다 (위 띠와 같은 이유).
                    g.clip(bx + (0 if i else line_t), ct + line_t, half - line_t, strip_h - line_t)
                    g.round_rect(cx, ct, pill_w, 2 * r, r, fill=PSTRIP_ON, alpha=alpha)
                    g.clip_off()
                if i:
                    g.line(bx, ct + r, bx, ct + strip_h, PSTRIP_LINE)
                arrow = ("▾" if (self._popen and on) else "▴") + " "
                badge = str(near_n) if (key == "near" and near_n) else ""
                tw = g.measure(arrow + label, FONT, s_sz, on)
                bw = (g.measure(badge, MONO, s_sz) + dk(10)) if badge else 0
                tx = bx + (half - tw - bw) / 2.0
                tx += g.text(tx, ct + strip_h / 2.0, arrow + label, FONT, s_sz,
                             ("#e2b866" if on else P_SUB), bold=on)
                if badge:
                    g.round_rect(tx + dk(5), ct + strip_h / 2.0 - dk(8), bw - dk(3), dk(16), dk(8), fill=GOLD)
                    self._ctext(g, tx + dk(5) + (bw - dk(3)) / 2.0, ct + strip_h / 2.0,
                                badge, MONO, s_sz, "#1a1608", True)
                self._hit(bx, ct, half, strip_h,
                          (lambda k: (lambda: self._band_tab(k)))(key), keep=True)
            if True:
                # 1) 제목 · 합주 · 이퀄라이저
                y1 = ct + strip_h + dk(11) + dk(19) / 2.0
                x = cx + pad_l
                g.text(x, y1, title, FONT, t_sz, "#ffffff", bold=True)
                x += w_title + dk(11)
                if badge:
                    g.text(x, y1, badge, FONT, b_sz, P_SUB)
                    x += w_badge + dk(11)
                if mount:                        # 「탈것 탑승 중」 / 「내리면 재생」 — 분홍 경고 알약
                    self._card(g, x, y1 - mw_h / 2.0, w_mount, mw_h, mw_h // 2, WARN_BG, WARN_PINK, 255)
                    self._ctext(g, x + w_mount / 2.0, y1, mount, FONT, mw_sz, WARN_PINK, True)
                    x += w_mount + dk(11)
                for i in range(4):               # 대기 중에는 낮게 눕혀 둔다 (움직이지 않는다)
                    amp = (0.35 + 0.65 * abs(math.sin(time.time() * 2.2 + i * 0.8))) if playing else 0.18
                    bh = max(dk(2), int(dk(14) * amp))
                    g.round_rect(x + i * (dk(3) + dk(2.5)), y1 + dk(14) / 2.0 - bh, dk(3), bh, dk(1.5),
                                 fill=GOLD if playing else "#4b5665")
                # 2) 진행바 · 시간 · 반복 · 셔플 · 설정
                y2 = ct + strip_h + row1_h + (row2_h - dk(13)) / 2.0
                g.round_rect(cx + pad_l, y2 - gh / 2.0, gw, gh, gh // 2, fill=PGROOVE_BG)
                if pct > 0:
                    g.round_rect(cx + pad_l, y2 - gh / 2.0, max(gh, int(gw * pct)), gh, gh // 2, fill=GOLD)
                # 진행바 앞머리의 원은 일부러 그리지 않는다 — 끌면 구간 이동이 되는 줄 오해한다
                bx2 = cx + pad_l + gw + dk(9)
                g.text(bx2, y2, clock, MONO, m_sz, ARTIST_FG)
                bx2 += w_clock + dk(9)
                for what, on, fn in (("repeat", self._repeat != "off", self._cycle_repeat),
                                     ("shuffle", self._shuffle, self._toggle_shuffle),
                                     ("spark", self._cfg.get("opening") is not False, self._toggle_opening)):
                    self._round_btn(g, bx2, y2 - bd / 2.0, bd, on)
                    self._ctl_icon(g, self._ctl_kind(what), bx2 + bd / 2.0, y2,
                                   dk(16) if what == "spark" else dk(14), GOLD if on else P_CHIP_FG)
                    self._hit(bx2, y2 - bd / 2.0, bd, bd, fn)
                    bx2 += bd + dk(9)
                # 3) 다음 곡
                y3top = ct + strip_h + row1_h + row2_h
                g.line(cx + dk(1), y3top, cx + pill_w - dk(2), y3top, "#1c2531")
                y3 = y3top + dk(9) + dk(18) / 2.0
                x3 = cx + pad_l
                if self._hint[0] and time.time() < self._hint[1]:     # 방금 누른 것을 알려 준다
                    g.icon(x3 + dk(7), y3, "✓", FONT, dk(11), GOLD)
                    g.text(x3 + dk(20), y3, self._fit(g, self._hint[0], FONT, n_sz, True,
                                                      pill_w - pad_l - pad_r3 - dk(24)),
                           FONT, n_sz, GOLD_HI, bold=True)
                    nxt = None
                    x3 = cx + pill_w                                  # 아래 그리기를 건너뛴다
                else:
                    x3 += g.text(x3, y3, "다음", FONT, n_sz, P_SUB) + dk(8)
                if nxt:
                    nn = int(nxt.get("ens") or 0)
                    g.round_rect(x3, y3 - dk(9), dk(18), dk(18), dk(5), fill="#1b2433", outline="#3b4a63", width=1)
                    if nn:
                        self._ctext(g, x3 + dk(9), y3, str(nn), FONT, dk(9), "#9dbcf2", True)
                    else:
                        g.icon(x3 + dk(9), y3, "♪", FONT, dk(9), "#9dbcf2")
                    x3 += dk(18) + dk(8)
                    avail3 = cx + pill_w - pad_r3 - x3 - g.measure(n_dur, MONO, dk(10)) - dk(8)
                    x3 += g.text(x3, y3, self._fit(g, n_title, FONT, n_sz, True, max(dk(40), avail3)),
                                 FONT, n_sz, P_INK, bold=True) + dk(8)
                    g.text(x3, y3, n_dur, MONO, dk(10), P_SUB)
                    self._hit(cx + pad_l, y3top, pill_w - pad_r3 - pad_l, row3_h,
                              lambda: self._step(1))   # 누르면 그 곡으로 (「다음」과 같은 일)
                else:
                    g.text(x3, y3, "다음 곡 없음", FONT, n_sz, P_SUB)
                # 원 — 카드 오른쪽 끝에서 96 안쪽에 앉는다. 실제보다 조금 크게 그려 초록이 안 비치게.
                bcx = cx + pill_w - over + dk(DESIGN_BTN) / 2.0
                bcy = ct + strip_h + (card_h - strip_h) / 2.0   # 띠를 뺀 영역의 세로 가운데
                g.ellipse(int(bcx - d_btn / 2), int(bcy - d_btn / 2), d_btn, d_btn, GOLD)
                sq = dk(DESIGN_SQ)
                if playing:                                    # 연주 중이면 정지(네모), 아니면 재생(세모)
                    g.round_rect(bcx - sq / 2.0, bcy - sq / 2.0, sq, sq, dk(8), fill=ON_GOLD)
                else:
                    g.icon(bcx + sq * 0.08, bcy, "▶", FONT, dk(26), ON_GOLD)
                self._hit(bcx - d_btn / 2, bcy - d_btn / 2, d_btn, d_btn, self._toggle_play)
        if self._hover:                            # 롤오버 — 커서가 얹힌 자리를 바탕보다 조금 어둡게
            hx, hy, hw, hh = self._hover
            hr = hh // 2 if (hh <= px(30) or abs(hw - hh) <= 2) else px(8)
            g.round_rect(hx, hy, hw, hh, hr, fill=HOVER_WASH, alpha=HOVER_ALPHA)
        if self._dragging == "player":             # 끄는 동안은 손을 따라간다 (다시 계산하면 제자리로 튄다)
            x, y0 = self._pxy
            bx0, by0 = x - (w - w_pill), y0 - panel_h - gap
        elif self._auto and self._client:          # 창 전체(판 포함) 기준으로 가장자리 간격을 맞춘다
            bx0, by0 = self._auto_xy("player", w, h, self._client)
            x, y0 = bx0 + (w - w_pill), by0 + panel_h + gap
        else:                                      # 손으로 옮긴 자리 — 오른쪽·아래 여백으로 기억한다
            bx0, by0 = self._player_manual(w, h)
            x, y0 = bx0 + (w - w_pill), by0 + panel_h + gap
        self._pxy = (x, y0)
        # 가두는 것은 **자동 배치일 때만**. 손으로 옮긴 자리는 게임 밖이라도 그대로 둔다 —
        # 안 그러면 끌어다 놓는 순간 다시 안으로 튀어 「이동이 안 된다」가 된다.
        if self._client and self._auto and not self._dragging:
            gx, gy, gw2, gh2 = self._client
            if w <= gw2:
                bx0 = min(max(bx0, gx), gx + gw2 - w)
            if h <= gh2:
                by0 = min(max(by0, gy), gy + gh2 - h)
            self._pxy = (bx0 + (w - w_pill), by0 + panel_h + gap)
        self._pwin = (bx0, by0, w, h)
        g.blit(self._phwnd, bx0, by0)
        self._pk_sync()                            # 악기 검색 입력칸을 판의 새 자리에 맞춘다
        self._lwa_check(g, self._phwnd, "연주 패널")
        self._dump(g, "player")

    def _draw(self, d, cfg) -> None:
        sc = self._sc

        def px(v):
            return max(1, int(round(v * sc)))

        st = self._state(d, cfg)
        border = {"play": B_PLAY, "start": B_START, "idle": B_IDLE, "unknown": B_UNKNOWN}[st]
        status = {"play": ("연주 중", GOLD), "start": ("재생 시작", GOLD_HI),
                  "idle": ("대기", ARTIST_FG), "unknown": ("미확인 곡", UNKNOWN)}[st]
        title_fg = {"idle": ARTIST_FG, "unknown": UNKNOWN_TITLE}.get(st, TITLE_FG)

        if d["playing"]:
            title = d.get("song") or d["title"] or "연주 중"
            artist = d.get("artist") or ""
            clock = f"{_fmt(d['el'] or 0)} / {_fmt(d['tot'] or 0)}"
        elif st == "unknown":
            title, artist, clock = "미확인 곡", "—", "—"
        else:
            title, artist, clock = "—", "", "0:00"

        g = self.surf
        s12, s11 = px(12), px(11)
        h = px(30)
        cy = h // 2
        w_handle = max(2, int(round(1.6 * self._sc))) + int(round(3.0 * self._sc))
        w_status = g.measure(status[0], FONT, s12, True)
        w_title = min(px(180), g.measure(title, FONT, s12, True))
        w_artist = g.measure(artist, FONT, s12) if artist else 0
        w_clock = g.measure(clock, MONO, s12)
        w = (px(4) + px(7) + w_handle + px(7) + px(5) + px(5) + w_status + px(8) + w_title + px(7)
             + ((w_artist + px(8)) if artist else 0) + w_clock + px(8) + px(4))
        g.resize(w, h)
        g.clear()
        self._card(g, 0, 0, w, h, px(15), BAND, border, self._bg_alpha())

        x = px(4) + px(7)
        self._handle(g, x, cy, HANDLE)
        x += w_handle + px(7)
        dot = GOLD if st != "idle" else CARET
        if st in ("play", "start"):
            dot = _mix(BAND, dot, 0.25 + 0.75 * abs(math.sin(time.time() * math.pi / 1.4)))
        g.ellipse(x, cy - px(2), px(5), px(5), dot)
        x += px(5) + px(5)
        g.text(x, cy, status[0], FONT, s12, status[1], bold=True)
        x += w_status + px(8)
        g.text(x, cy, self._fit(g, title, FONT, s12, True, w_title), FONT, s12, title_fg, bold=True)
        x += w_title + px(7)
        if artist:
            g.text(x, cy, artist, FONT, s12, ARTIST_FG)
            x += w_artist + px(8)
        g.text(x, cy, clock, MONO, s12, TIME_FG)

        bx, by = (self._xy if self._dragging == "band" else
                  (self._auto_xy("band", w, h, self._client) if (self._auto and self._client)
                   else self._manual_xy("band", w, h)))
        self._xy = (bx, by)
        g.blit(self._hwnd, bx, by)
        self._lwa_check(g, self._hwnd, "밴드")
        self._dump(g, "band")

    def _redraw(self) -> None:
        if self._stop.is_set():
            for g in (getattr(self, "surf", None), getattr(self, "psurf", None), self._dcache):
                try:
                    g.close()
                except Exception:
                    pass
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        try:
            self._drain()
            with self._lock:
                d = dict(self._data)
                cfg = dict(self._cfg)
            gone = False
            if self._app_gone is not None:
                try:
                    gone = bool(self._app_gone())
                except Exception:
                    gone = False
            if gone != self._gone:
                # 프로세스는 종료 판정을 따로 하고(몇 초 더 산다), 밴드만 먼저 내린다.
                # 다시 붙으면 그대로 되살아난다 — 저장된 「보이기」 값은 건드리지 않는다.
                self._gone = gone
                self._log(f"[overlay] 앱 창이 {'닫혔습니다 — 밴드를 내립니다' if gone else '돌아왔습니다 — 밴드를 되살립니다'}")
                self._apply_show()
            if self._visible and not self._gone:
                # 화면 배율을 곱해 실제 픽셀로 그린다 (창이 늘어나지 않으므로 글자가 또렷하다)
                self._sc = max(0.6, min(2.5, (cfg.get("ov_scale") or 100) / 100)) * getattr(self, "_dpi", 1.0)
                # 바뀐 게 없으면 아예 그리지 않는다 — 가만히 있어도 CPU 한 코어를 먹고 있었다
                now2 = time.time()
                sig = self._frame_sig(d, cfg)
                if sig != self._fsig or now2 - self._fat > 1.0:
                    self._fsig, self._fat = sig, now2
                    want = self._want_band()
                    if want != getattr(self, "_band_on", None):
                        self._band_on = want
                        try:
                            self.root.deiconify() if want else self.root.withdraw()
                        except Exception:
                            pass
                        if want:
                            self._restack = True
                    if want:
                        self._draw(d, cfg)
                    self._draw_player(d)
            self._hotkeys()
            self._keep_above()
        except Exception as e:
            # 어디서 났는지가 없으면 못 고친다 — 같은 오류는 한 번만 자리를 적는다 (매 프레임 나므로)
            import traceback
            key = f"{type(e).__name__}: {e}"
            if key != getattr(self, "_redraw_err", None):
                self._redraw_err = key
                self._log(f"[overlay] redraw failed: {key}\n" + traceback.format_exc().rstrip())
            else:
                self._log(f"[overlay] redraw failed: {key}")

    def _tick(self) -> None:
        """120ms 그리기 루프. **다음 예약은 여기서만** 한다.

        예전에는 _redraw 끝에서 스스로 예약했는데, 단추를 누르거나 휠을 굴릴 때마다
        _redraw 를 직접 부르므로 그때마다 루프가 하나씩 늘었다. 몇 번 만지면 초당 수십 번
        전체를 다시 그리게 되어 점점 느려졌다."""
        self._redraw()
        if self._stop.is_set():
            return
        try:
            self.root.after(120, self._tick)
        except Exception:
            pass

    def _on_ui(self, pt=None) -> bool:
        """커서가 우리 UI 위에 있는가. 창 왼쪽 위 기준으로 옮겨서 본다. pt 는 화면 좌표 (x, y) — 없으면 커서.

        보통은 단추(클릭 판정 사각형) 위만 UI 다. 그런데 **악기 고르기가 열려 있는 동안은 연주 패널 창
        전체가 UI 다** — 판의 빈틈(칩 사이·줄 사이)과 막대는 단추가 아니어서, 스마트 관통 중에 거기서
        굴린 휠이 게임으로 새어 뒤 게임까지 같이 굴렀다.
        """
        if not getattr(self, "_pshown", False):
            return False
        if pt is None:
            p = ctypes.wintypes.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
                return False
            pt = (p.x, p.y)
        x, y = pt[0] - self._pwin[0], pt[1] - self._pwin[1]
        if self._menu is not None:
            if 0 <= x < self._pwin[2] and 0 <= y < self._pwin[3]:
                return True
        if not self._hits:
            return False
        return self._hit_at(x, y)[1] is not None

    def _smart_pass(self) -> None:
        """스마트 관통 — 관통 중에도 **우리 단추 위에서는** 클릭을 받는다.

        WS_EX_TRANSPARENT 는 창 전체에 걸리는 값이라 켜면 스마트 관통 칩까지 안 눌려
        끌 방법이 사라졌다. 그래서 40ms 마다 커서를 보고, 단추 위에 있는 동안만 그 값을 뺀다.
        단추가 아닌 곳(게임 UI 를 덮고 있는 여백)은 그대로 게임으로 지나간다 — 그래서 「스마트」다.
        """
        if not self._through:
            if self._pass_ui:
                self._pass_ui = False
                self._apply_style_for(getattr(self, "_phwnd", 0))
            return
        on = self._on_ui()
        if on == self._pass_ui:
            return
        self._pass_ui = on
        self._apply_style_for(getattr(self, "_phwnd", 0))
        try:        # 확장 스타일을 바꾸면 레이어 표면이 무효가 된다 — 다시 얹지 않으면 절반이 사라진다
            self.psurf.blit(self._phwnd, self._pwin[0], self._pwin[1])
        except Exception:
            pass

    def _frame_sig(self, d, cfg) -> tuple:
        """이 프레임에 그릴 내용. 지난 프레임과 같으면 그리기를 통째로 건너뛴다.

        빠뜨린 값이 있어도 1초에 한 번은 무조건 다시 그리므로 화면이 굳지는 않는다.
        이퀄라이저 막대는 연주 중에만 움직이니 그때만 시간을 넣는다."""
        now = time.time()
        playing = bool(d.get("playing"))
        return self._detail_sig(d) + (
            self._hover,                                         # 커서가 얹힌 단추 (롤오버 색)
            int(now * 8) if playing else 0,                      # 이퀄라이저 막대
            self._hint[0] if self._hint[1] > now else "",
            self._visible, self._gone, self._player, self._popen, self._auto, self._dragging,
            self._client, self._pxy, self._xy, d.get("artist"), bool(d.get("known", True)),
            bool(d.get("loop")), cfg.get("ov_scale"), cfg.get("ov_alpha"))

    def _follow(self) -> None:
        """게임 창을 따라간다. 보이면 40ms, 숨겨져 있으면 400ms — 끌 때 뒤처지지 않게."""
        try:
            if self._visible:
                try:            # 배율이 다른 모니터로 옮겨 가면 그리는 크기도 따라 바꾼다
                    import paint32
                    d = paint32.dpi_of(self._hwnd)
                    if d and abs(d - self._dpi) > 0.01:
                        self._dpi = d
                        self._psize = None          # 크기를 다시 잡게 한다
                except Exception:
                    pass
                r = self._client_rect()
                if r and r != self._client:
                    self._client = r
                elif r is None and self._game:
                    self._client = self._client        # 최소화 등 — 마지막 자리를 그대로 둔다
        except Exception:
            pass
        for fn in (self._smart_pass, self._hover_tick, self._pk_guard):
            try:
                fn()
            except Exception:
                pass
        try:
            self.root.after(40 if self._visible else 400, self._follow)
        except Exception:
            pass

    def _keep_above(self) -> None:
        """최상위로 두지 않고, 게임이 우리 위로 올라온 때만 앞으로 올린다 (게임 핸들은 2초 동안 재사용)."""
        if not self._visible or not self._hwnd:
            return
        try:
            import opening_tk
            now = time.time()
            if now - self._game_at < 0.3:          # 0.3초마다만 — 끼워 넣는 일 자체가 순서를 흔든다
                return
            self._game_at = now
            if not self._game or not opening_tk._u32().IsWindow(self._game):
                self._game = opening_tk.find_game()
            wins = [self._hwnd] + ([self._phwnd] if getattr(self, "_pshown", False) else [])
            force = self._restack
            self._restack = False
            opening_tk.keep_above_game(wins, self._game, force=force)
        except Exception:
            pass

    _prev: dict = {}

    def _hotkeys(self) -> None:
        """창 포커스와 무관하게 Shift + F키를 본다 (GetAsyncKeyState 폴링).
        Shift+F1 보이기/숨기기 · Shift+F10 클릭 관통 · Shift+F2 위치 잠금 · Shift+F12 앞으로."""
        u = ctypes.windll.user32
        shift = bool(u.GetAsyncKeyState(_VK_SHIFT) & 0x8000)
        for name, vk in _KEYS.items():
            down = shift and bool(u.GetAsyncKeyState(vk) & 0x8000)
            if down and not self._prev.get(name):
                if name == "show":
                    self._show(not self._visible)
                elif name == "through":
                    self._set_through(not self._through)
                elif name == "lock":
                    self._locked = not self._locked
                    self._save(locked=self._locked)
                    self._log(f"[overlay] position {'locked' if self._locked else 'unlocked'}")
                elif name == "front":
                    self._q.put(lambda: self._front())
            self._prev[name] = down

    def _front(self) -> None:
        try:
            import opening_tk
            u = opening_tk._u32()
            u.SetWindowPos(self._hwnd, -1, 0, 0, 0, 0, 0x0013)
            u.SetWindowPos(self._hwnd, -2, 0, 0, 0, 0, 0x0013)
        except Exception:
            pass

    def _run(self) -> None:
        try:
            import paint32
            paint32.set_dpi_aware()      # 창을 만들기 전에 — 안 켜면 화면 배율만큼 늘어나 글자가 뭉개진다
            self._build()
        except Exception as e:
            self._log(f"[overlay] start failed: {e}")
            return
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.after(300, self._tick)
        self.root.after(350, self._follow)
        try:
            self.root.mainloop()
        except Exception as e:
            self._log(f"[overlay] loop ended: {e}")

    def start(self) -> "Overlay":
        threading.Thread(target=self._run, daemon=True, name="overlay").start()
        return self

    def stop(self) -> None:
        self._stop.set()          # 실제 정리는 화면 스레드가 한다 (_redraw 의 종료 가지)


def start(base: str, activity, ensemble, settings, log=print, game_rect=None, meta=None, ops=None,
          app_gone=None) -> Overlay | None:
    """오버레이를 띄운다 (Windows 전용). 실패해도 예외를 밖으로 내지 않는다."""
    if os.name != "nt":
        return None
    try:
        return Overlay(base, activity, ensemble, settings, log, game_rect, meta, ops, app_gone).start()
    except Exception as e:
        log(f"[overlay] not started: {e}")
        return None
