"""게임 오버레이 — 게임 창 위에 뜨는 30px 가로 밴드 (tkinter).

지키는 규칙:
1. 「항상 맨 위」가 아니라 **게임 창 바로 위 한 칸**이다. `_sync_zorder` 참조.
2. hwnd 는 `GetAncestor(winfo_id(), GA_ROOT)` — `winfo_id()` 는 자식 창이라 스타일이 안 먹는다.
3. 클릭 통과 예외 = 「커서가 버튼 사각형 안이면 WS_EX_TRANSPARENT 를 끈다」. 폴링 40ms, **바뀔 때만** SetWindowLongW.
4. 전역 단축키는 두지 않는다. 대신 설정 탭에 「위치 초기화」를 둔다.
5. Tk 는 스레드 안전하지 않다 — 서버가 메인 스레드를 쓰므로 Tk 는 전용 스레드에서 만들고 그 안에서만 만진다.
6. 반드시 닫히는 길을 둔다 — `stop()` 이 destroy 하고, 같은 프로세스라 서버가 끝나면 함께 사라진다.

CLI 는 이 파일에서 **한 번도 부르지 않는다**. 데이터는 전부 캐시/메모리(`QUEUE.state()`·`cache_works.json`)다.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import ledger     # 일간 리포트 한 줄 — 밴드와 보드가 같은 글을 쓴다 (`ledger.day_line`)
import workqueue  # 연주 대기 한 마디 (`workqueue.hold_short`) — 밴드와 보드가 같은 갈래 이름을 쓴다

# ── 디자인 색 (밴드는 tkinter 라 ui/css/tokens.css 를 못 쓴다 — 여기 한 곳에 모은다) ──
# 값은 디자인 값 그대로. 알파가 섞인 값(rgba)은 밴드 배경 위로 합성한 불투명 색으로 적는다.
COLORS = {
    "bg": "#090c11",            # rgba(9,12,17,.82) — 창 전체 불투명도로 투명도를 낸다
    "fg": "#e9ecf1",
    "fg2": "#c9d0da",
    "strong": "#f4e7c8",        # 현재 항목 이름
    "strongbad": "#fbe3ea",     # 오류·정지 상태의 항목 이름
    "label": "#8f98a6",         # 펼친 판의 「대기 3」·「가공 2/3」 머리말
    "sub": "#9aa3b2",
    "dim": "#4b5665",           # ⋮⋮ · 구분점
    "faint": "#6f7a8a",         # ▾
    "sep": "#2b333e",           # rgba(255,255,255,.14) 세로 구분선
    "line": "#3f454d",          # 평소 테두리 rgba(255,255,255,.16) — 게임 위에서 보이도록 한 단계 올렸다
    "run": "#7aa2e8",           # 실행 중
    "ok": "#4ade80",            # 완료
    "okline": "#379f5f",        # 완료 테두리 rgba(74,222,128,.7)
    "oktext": "#7ee6a0",
    "bad": "#e88aa6",           # 오류
    "badline": "#b06b81",       # 오류 테두리 rgba(232,138,166,.75)
    "badtext": "#f5b8c8",
    "warn": "#f0a35a",          # 가공 타이머 · 수령 대기
    "warnline": "#94673d",      # 주황 테두리 rgba(240,163,90,.6)
    "warnline2": "#7a552f",     # 가공 전용 판 테두리 rgba(240,163,90,.45)
    "panelline": "#33383f",     # 펼친 판 테두리 rgba(255,255,255,.18)
    "dim2": "#3b434e",          # 펼친 판의 흐린 줄 (opacity:.6/.5 — tkinter 는 위젯 투명도가 없어 색으로 낸다)
    "gold": "#e2b866",          # 회차
    "collectbg": "#3fb56b",     # 「일괄 수령」 버튼
    "collectfg": "#0a2a12",
    "toastbg": "#1a1216",       # 오류 토스트 rgba(26,18,22,.92)
    # ── 가공기 현황 판 ──
    "facwood": "#d6a97a",       # 시설 아이콘: 목재
    "facleather": "#c8a6f0",    # 시설 아이콘: 가죽
    "facbg": "#0d1016",         # 알약 배경 rgba(255,255,255,.03)
    "facbgok": "#101b14",       # 수령할 것이 있는 알약 rgba(74,222,128,.06)
    "facbadgefg": "#231204",    # 완료 개수 배지 글자
    # 칸 트랙. **검게 깔면 배경과 구분이 안 된다** — 디자인의 반투명을 게임 화면
    # 위에서 실제로 보이는 밝기(#2d3735 언저리)로 올려 잡는다.
    "slotdone": "#2e4a36",      # 칸 트랙: 완료
    "slotrun": "#2d3735",       # 칸 트랙: 진행
    "slotfree": "#252c31",      # 칸 트랙: 빈 칸
    "slotnone": "#1a1f24",      # 칸 트랙: 없는 칸 (있는 칸과 구분은 되게)
    "slotlock": "#3a262c",      # 칸 트랙: 잠김
    "okline2": "#3e6a5c",       # 「연결됨」 테두리 rgba(95,199,164,.45)
    "throughbg": "#14261b",     # 「스마트 관통 중」 배경 rgba(63,181,107,.14)
    "throughline": "#245c3b",   # 「스마트 관통 중」 테두리 rgba(63,181,107,.5)
    "toastline": "#8d5a6b",     # 토스트 테두리 rgba(232,138,166,.5) — 밴드 테두리(.75)보다 옅다
    "toastline2": "#2d6b45",    # 완료 토스트 테두리 rgba(74,222,128,.5)
    "toastbody": "#d7dce3",     # 토스트 둘째 줄
    # ── 「전부 비어 있을 때」 ──
    "sepfaint": "#22282f",      # 비어 있을 때의 구분선 rgba(255,255,255,.1) — 평소(.14)보다 옅다
    "emptydot": "#4b5665",      # 「대기」 앞 점
    "emptyhint": "#8f98a6",     # 「창에서 담기」 — 다른 글자보다 밝다 (누르라는 뜻)
    "dimborder": "#181c22",     # 흐린 시설 알약 테두리 rgba(255,255,255,.08)
    "dimname": "#6f7a8a",       # 흐린 시설 이름
    "dimtime": "#4b5665",       # 흐린 시설 시간
    "dimdot": "#3a4756",        # 흐린 시설 점
    # ── 「일괄 수령」이 눌리지 않을 때 ──
    "alterdot0": "#2b3542",     # 완료 0 일 때의 점
    "collectoffline": "#1f242b",   # 비활성 버튼 테두리 rgba(255,255,255,.1)
}
# 종류 배지 (채집 = 초록 계열, 제작 = 파랑 계열, 가공 = 주황 계열)
BADGE = {
    "gather": ("채집", "#1b3a33", "#7fe0bd"),
    "craft": ("제작", "#22304a", "#9dbcf0"),
    "alter": ("가공", "#3a2c1c", "#f6c48a"),
    "collect": ("수령", "#2c2340", "#c9aef5"),
    "group": ("그룹", "#332a16", "#e2b866"),
    "play": ("연주", "#332a16", "#e2b866"),      # 폴리오의 금색 ♪
    "notify": ("알림", "#1b2a44", "#9dc3ff"),    # 종 — 파랑
}
try:
    import paint32
    import bandpaint
    import panelpaint
except Exception:            # gdiplus 가 없는 기계 — 밴드는 못 그리지만 앱은 살아야 한다
    paint32 = bandpaint = panelpaint = None

BAND_H = 30          # 디자인: 높이 30px 한 줄
BAND_BORDER = 1      # 테두리 두께(디자인 px). 배율로 키운다 — 125% 에서 1px 은 반 픽셀이 되어 뭉갠다
PANEL_W = 420        # 「▾ 펼침」 판의 **고정 폭**. 내용에 맞추면 머리줄(밴드 반복)이 잘린다
POLL_MS = 40         # 커서 폴링 25Hz
IDLE_MS = 400        # 꺼져 있을 때의 폴링 주기 (창은 살아 있지만 볼 것이 없다)
FLASH_SEC = 1.5      # 완료·오류·회차 전환 때 100% 로 올라가 있는 시간
# 실패 토스트는 **스스로 사라지지 않는다** — `×` 로 닫는다.
#
# 실패는 **사람이 보고 지워야 하는 것**이다 —
# 자리를 비우러 혼자 사라지면, 자리를 비운 뒤에는 아무도 그 실패를 모른다.
TOAST_W = 236        # 토스트는 **폭 고정** (글 길이에 따라 들쭉날쭉하면 안 된다)
# 밴드 아래에 붙는 것(펼침 판·가공기 판·토스트)의 틈. 토스트와 가공 펼침이 둘 다
# **top 52** = 밴드 윗변 14 + 높이 30 + 8 이다 — 셋이 같은 틈을 써야 서로 어긋나지 않는다.
PANEL_GAP = 8
# 항목명은 **92px 에서 말줄임**. 글자 수(`ellipsis`)로 먼저
# 거칠게 자르고, 그린 폭으로 다시 잰다 — 한글·영문이 섞이면 글자 수와 폭이 따로 논다.
NAME_MAX_PX = 92
# 모서리 둥글기 (밴드 15 · 펼친 판 14 · 토스트 10). SetWindowRgn 으로 깎는다
R_BAND, R_PANEL, R_TOAST = 15, 14, 10
# 상태별 강조색 — **큐 진행 숫자와 그 앞 점**이 상태를 따라간다
ACCENT = {"run": "run", "idle": "sub", "done": "ok", "error": "bad", "ready": "run", "stopped": "bad"}

# ── 게임 창에 맞추기 ──
# 창 제목이 아니라 **프로세스 이름**으로 찾는다 — 제목은 지역화·업데이트로 바뀐다.
GAME_PROCESS = ("MabinogiMobile",)
RESCAN_SEC = 3.0           # 게임 창을 못 찾았을 때 다시 훑는 간격 (EnumWindows 를 1초마다 돌릴 이유는 없다)
DESIGN_W, DESIGN_H = 1280, 780    # 디자인 캔버스
DESIGN_TOP = 14                   # 밴드 윗변
DESIGN_SAFE = (240, 1002)         # 채팅을 펼쳐도 안 겹치는 상단 가로 구역
DEFAULT_POS = (250, 14)           # 「위치 초기화」가 되돌릴 자리 (store 의 overlay_x/y 기본값과 같다)

# ── Win32 상수 ──
GWL_EXSTYLE, GWL_STYLE = -20, -16
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x80000, 0x20, 0x80, 0x8
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
LWA_ALPHA, GA_ROOT = 2, 2
MONITOR_DEFAULTTONEAREST = 2
# Z 순서: **항상 맨 위가 아니라 「게임 창 바로 위 한 칸」**이다.
# 다른 창이 게임을 덮으면 밴드도 같이 가려지는 것이 맞는 동작이다.
HWND_NOTOPMOST, HWND_TOP, HWND_TOPMOST = -2, 0, -1
RGN_DIFF = 4         # CombineRgn: 첫 영역에서 둘째를 빼낸다 (점선 테두리의 홈)
GW_HWNDNEXT, GW_HWNDPREV = 2, 3   # Z 순서에서 아래쪽 / 위쪽 창
Z_WALK = 12          # 밴드 아래로 몇 걸음까지 게임을 찾아볼까 (보이지 않는 창은 세지 않는다)
Z_MIN_W = 50         # 이보다 좁은 창은 실제 창이 아니다 (IME·트레이 같은 숨은 창)
Z_CHECK_SEC = 0.3    # Z 순서 확인 주기. 끼워 넣는 일은 순서를 흔드므로 40ms 마다 부르면 안 된다
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
SWP_STACK = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE   # NOACTIVATE 가 없으면 밴드가 포커스를 뺏는다
# 배경 흐림 (디자인의 `backdrop-filter: blur(6px)`). **문서화된 길을 먼저 쓴다.**
DWMWA_SYSTEMBACKDROP_TYPE = 38      # Win11 22621+ — 문서화된 API
DWMSBT_NONE, DWMSBT_TRANSIENTWINDOW = 1, 3   # 없음 / 아크릴
# **흐림을 걸기 전에 반드시 이걸 먼저 건다.** DWM 재질은 시스템 테마를 따르므로, 밝은 테마 기계에서는
# 흰 아크릴이 깔려 밴드 테두리에 흰 배경이 붙어 보인다.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
WCA_ACCENT_POLICY = 19              # 아래는 비공개 API — 위가 안 될 때만 본다
ACCENT_DISABLED, ACCENT_ENABLE_ACRYLICBLURBEHIND = 0, 4
# 게임이 전체화면 전용이면 어떤 최상위 창도 위에 못 뜬다. 문서화된 판정 API:
QUNS_RUNNING_D3D_FULL_SCREEN = 3
# 전역 단축키는 **두지 않는다**. RegisterHotKey 도 전용 스레드도 두지 않는다.
# 덧붙여: 두 번째 인스턴스에서는 애초에 등록되지도 않았다 — 먼저 뜬 앱이 쥐고 있으면 err=1409
# (ERROR_HOTKEY_ALREADY_REGISTERED) 로 3/3 실패한다(실측). 대신 설정 탭에 「위치 초기화」를 두었다.

# ── 글꼴 ──
# **tkinter 는 대체 글꼴 목록을 못 받는다.** 웹(`tokens.css`)처럼 "Segoe UI","맑은 고딕",… 로 줄 세워 주면
# 브라우저가 알아서 넘어가지만, Tk 는 한 벌만 받고 없는 글자는 네모로 깨진다.
# Segoe UI·Consolas 에는 **한글 글리프가 없다** — 그래서 칸마다 글꼴을 직접 골라야 한다.
UI_FAMILIES = ("맑은 고딕", "Malgun Gothic", "굴림", "Gulim", "돋움", "Dotum")
MONO_FAMILIES = ("Consolas", "D2Coding", "Courier New")
# 이 값을 넘는 글자(한글·▾·⋮⋮·—)는 Consolas 에 없다 — 그 칸은 한글 글꼴로 그린다.
NON_ASCII_FROM = 0x2000

# 모든 위젯에 붙인다. tkinter 기본 테두리·포커스 테는 **시스템 기본색(밝은 회색/흰색)**이라
# 어두운 밴드에 흰 테가 붙어 보인다. 0 으로 박아 둔다.
FLAT = {"bd": 0, "highlightthickness": 0, "relief": "flat"}


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수 — 창을 만들지 않는다 (tests/test_overlay.py 가 이것만 시험한다)
# ══════════════════════════════════════════════════════════════════════════
def needs_ui_font(text) -> bool:
    """이 글자를 고정폭(Consolas)으로 그려도 되나. 한글·`▾`·`⋮⋮`·`—` 가 섞이면 **안 된다** — 깨져 나온다.
    칸 이름으로 가르지 않고 **실제 글자**로 가른다: 분류를 잘못해도 깨지지 않게."""
    return any(ord(ch) >= NON_ASCII_FROM for ch in str(text or ""))


def pick_family(want, available) -> str:
    """줄 세운 후보 중 이 기계에 **실제로 있는** 첫 글꼴. 하나도 없으면 빈 문자열(Tk 기본값을 쓴다)."""
    have = {str(a).strip().lower() for a in (available or [])}
    for f in want:
        if f.lower() in have:
            return f
    return ""


def fmt_mmss(sec) -> str:
    """02:10. 음수·None 은 00:00."""
    try:
        s = int(sec)
    except (TypeError, ValueError):
        s = 0
    s = max(0, s)
    return f"{s // 60:02d}:{s % 60:02d}"


def fmt_fac_time(sec) -> str:
    """가공기 현황 판의 시간 (예: `7:35` · `59:50` · `259:15`).

    밴드의 `fmt_mmss` 와 달리 **분에 0 을 채우지 않고 60분을 넘겨도 분으로 센다** —
    「000:00 폭으로 우측 정렬」하는 표기다."""
    try:
        v = int(sec)
    except (TypeError, ValueError):
        v = 0
    v = max(0, v)
    return f"{v // 60}:{v % 60:02d}"


def ellipsis(text: str, n: int) -> str:
    """이름이 길면 밴드가 늘어난다 — n 글자에서 자른다 (한글은 폭이 두 배라 글자 수로 센다)."""
    t = str(text or "")
    return t if len(t) <= n else t[: max(1, n - 1)] + "…"


def shrink_text(text: str, measure, max_px: float) -> str:
    """그린 폭이 `max_px` 를 넘으면 뒤에서부터 한 글자씩 덜어 내고 `…` 를 붙인다.

    `measure(text) -> px`. 적어도 한 글자는 남긴다 — 이름이 통째로 사라지면 무엇이 도는지 모른다."""
    t = str(text or "")
    if not t or measure(t) <= max_px:
        return t
    core = t[:-1] if t.endswith("…") else t
    while len(core) > 1 and measure(core + "…") > max_px:
        core = core[:-1]
    return core + "…"


def fit_name(cells: list, measure, max_px: float) -> list:
    """항목명은 최대 92px 에서 말줄임 — 이름 칸만 폭으로 다시 자른다. **다른 칸은 손대지 않는다.**"""
    for c in cells:
        if c.get("key") == "name":
            c["text"] = shrink_text(c.get("text") or "", measure, max_px)
    return cells


def fit_band(cells: list, width_of, max_w: float) -> list:
    """밴드는 항상 한 줄 — 넘치면 항목명부터 줄이고 배지·숫자는 유지한다.

    `width_of(cells) -> px` 로 잰 밴드 폭이 `max_w` 를 넘으면 **이름 칸의 글자를 하나씩** 덜어 낸다.
    이름이 한 글자 + `…` 까지 줄었는데도 넘치면 거기서 멈춘다 — 배지·숫자·버튼은 절대 빼지 않는다
    (빼면 밴드 폭이 들썩이고 무엇이 어디 있는지 잊는다). 칸을 없애는 일은 설정만 한다."""
    if max_w is None:
        return cells
    # 항목명 다음은 **일간 리포트 한 줄**이다 — 숫자가 아니라 덧붙인 글이므로 줄여도 된다.
    # 둘은 한 밴드에 같이 나오지 않는다 (리포트는 큐가 멈췄을 때만, 이름은 돌 때만).
    for key in ("name", "dayline"):
        cell = next((c for c in cells if c.get("key") == key), None)
        if cell is None:
            continue
        while width_of(cells) > max_w:
            t = str(cell.get("text") or "")
            core = t[:-1] if t.endswith("…") else t
            if len(core) <= 1:
                break
            cell["text"] = core[:-1] + "…"
    return cells


def mix_color(a: str, b: str, t: float) -> str:
    """`#rrggbb` a 에서 b 로 t 만큼 (0=a · 1=b). 디자인의 `opacity` 를 불투명 색으로 낼 때 쓴다 —
    밴드는 픽셀별 알파로 그리므로 글리프 하나의 불투명도를 따로 줄 길이 없다."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else float(t)
    ca = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    cb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ca, cb))


# 「실행 n/m(점 깜박 1.2s)」 = `@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}`.
BLINK_LOW = 0.25


def blink_cells(cells: list, on: bool, bg: str = "") -> list:
    """큐 진행 앞 점(`qdot`)의 깜박임 **꺼진 반주기** — 점을 배경 쪽으로 눌러 opacity .25 처럼 보이게.

    `on` 이면 손대지 않는다. **점만** 손댄다 — 숫자·배지는 깜박이지 않는다.
    `_sync_blink` 는 0.6초마다 토글만 한다 — **그림이 이 값을 읽어야** 점이 깜박인다."""
    if on:
        return cells
    base = bg or COLORS["bg"]
    for c in cells:
        if c.get("key") == "qdot" and c.get("fg"):
            c["fg"] = mix_color(base, c["fg"], BLINK_LOW)
    return cells


def toast_close_rect(w: float, h: float, sc: float) -> tuple:
    """토스트의 `×` 가 눌리는 자리 (토스트 왼쪽 위 기준). **이 자리만** 닫는다.

    `grid-template-columns:3px 1fr auto` 의 마지막 칸이다. 글리프 하나는 너무 작아 오른쪽 끝
    24px 를 세로 전체로 잡는다 (손가락이 헛디디지 않게)."""
    bw = 24 * sc
    return ("close", max(0.0, w - bw), 0.0, min(float(w), bw), float(h))


def split_card(card) -> tuple:
    """`currentCard` → (종류 라벨, 이름, 나머지 진행 문구).

    `text` 는 `workqueue.card_text` 가 만든 「채집 통나무 60/100」 한 줄이다. 밴드는 배지·이름·진행을
    따로 그리므로 앞의 「종류 이름 」을 떼어 낸다. 떼어 내지 못하면 text 를 그대로 진행 자리에 쓴다
    (지어내지 않는다)."""
    if not isinstance(card, dict):
        return "", "", ""
    label = BADGE.get(str(card.get("type") or ""), ("", "", ""))[0]
    name = str(card.get("name") or "")
    text = str(card.get("text") or "")
    head = f"{label} {name} "
    rest = text[len(head):] if label and name and text.startswith(head) else text
    return label, name, rest.strip()


def learn_facilities(rows, seen) -> dict:
    """**본 적 있는 시설과 그 최대 칸 수를 기억한다.**

    「전부 비어 있을 때」는 **작업이 하나도 없어도 시설 알약을 전부 흐리게** 보여 준다.
    그런데 `get_altering_works` 는 **지금 등록된 작업만** 주므로, 비어 있으면 시설 이름조차 모른다.
    그래서 볼 때마다 「그 시설에서 한 번에 본 작업 수」의 최댓값을 적어 둔다. 처음에는 작게 나오다가
    가공기를 꽉 채워 본 순간 진짜 칸 수가 된다 — **지어내지 않고 관찰로 배운다.**"""
    out = dict(seen or {})
    now: dict = {}
    for w in rows or []:
        if isinstance(w, dict):
            k = facility_key(w.get("FacilityName") or w.get("facility"))
            if k:
                now[k] = now.get(k, 0) + 1
    for k, n in now.items():
        if n > int(out.get(k) or 0):
            out[k] = n
    return out


def learn_durations(rows, dur) -> dict:
    """**가공 한 회가 몇 초인지 관찰로 배운다.**

    칸 막대는 「아래 → 위로 차오름」이다. 차오르려면 `(총 시간 − 남은 시간) / 총 시간` 이
    필요한데 **CLI 는 남은 시간만 준다** (`get_altering_works` 의 `RemainingSeconds`).

    그런데 남은 시간은 시작 직후가 가장 크다. 그래서 **작업 이름마다 본 것 중 가장 큰 남은 시간**을
    적어 두면 그게 곧 한 회다. 처음 본 회차에는 막대가 0 에서 시작해 제자리걸음처럼 보이지만,
    한 회를 끝까지 지켜본 뒤부터는 진짜 비율이 된다 — 지어낸 눈금을 그리지 않는다."""
    out = dict(dur or {})
    for w in rows or []:
        if not isinstance(w, dict) or w.get("done"):
            continue
        name = str(w.get("name") or "").strip()
        try:
            left = int(w.get("left") or 0)
        except (TypeError, ValueError):
            continue
        if name and left > int(out.get(name) or 0):
            out[name] = left
    return out


def alter_view(rows, fetched_at: float, now: float, seen=None, dur=None) -> dict:
    """가공 대기열을 밴드가 쓸 모양으로. 남은 시간은 캐시 시각 이후 흐른 만큼 깎는다
    (`ui/js/works.js` 와 같은 규칙: 남은 시간이 0 이어도 아직 시작 안 한 작업(NotStarted)은 완료가 아니다)."""
    el = max(0, int(now - fetched_at)) if fetched_at else 0
    by_fac: dict = {}
    works: list = []
    total_done = 0
    nxt = None
    for w in rows or []:
        if not isinstance(w, dict):
            continue
        name = str(w.get("name") or "")
        fac = str(w.get("facility") or "")
        try:
            left0 = int(w.get("left") or 0)
        except (TypeError, ValueError):
            left0 = 0
        left = 0 if w.get("done") else max(0, left0 - el)
        done = bool(w.get("done")) or (w.get("state") != "NotStarted" and left == 0)
        wait = (not done) and w.get("state") == "NotStarted"
        works.append({"name": name, "facility": fac, "done": done, "wait": wait, "left": left,
                      "total": int((dur or {}).get(name) or 0)})
        f = by_fac.setdefault(fac, {"facility": fac, "done": 0, "running": 0, "next": None, "name": ""})
        if done:
            f["done"] += 1
            total_done += 1
            if not f["name"]:
                f["name"] = name
        else:
            f["running"] += 1
            if wait:
                continue      # 아직 시작 안 한 작업은 셈에만 넣는다 — 0초 타이머로 보이면 안 된다
            if f["next"] is None or left < f["next"]:
                f["next"] = left
            if nxt is None or left < nxt:
                nxt = left
    facs = sorted(by_fac.values(), key=lambda x: x["facility"])
    targets = [{"facility": f["facility"], "name": f["name"], "n": f["done"]} for f in facs if f["done"]]
    targets.sort(key=lambda x: -x["n"])
    # 펼친 판이 쓰는 줄: 같은 시설·같은 이름·같은 분류는 한 칸으로 묶어 「이름 ×n」 (ui/js/works.js 와 같은 규칙).
    # 남은 시간은 그 묶음에서 가장 빨리 끝나는 것을 보여 준다 — 실데이터에 같은 가공이 여러 건씩 쌓인다.
    rows: list = []
    idx: dict = {}
    for w in works:
        key = (w["facility"], w["name"], w["done"], w["wait"])
        if key in idx:
            r = rows[idx[key]]
            r["n"] += 1
            if w["left"] is not None and (r["left"] is None or w["left"] < r["left"]):
                r["left"] = w["left"]
        else:
            idx[key] = len(rows)
            rows.append({"name": w["name"], "facility": w["facility"], "done": w["done"],
                         "left": None if w["wait"] else w["left"], "n": 1,
                         "total": w.get("total") or 0})
    rows.sort(key=lambda r: (not r["done"], r["left"] is None, r["left"] or 0))
    return {"facilities": facs, "done": total_done, "next": nxt, "targets": targets, "works": rows,
            "runDone": total_done, "runTotal": len(works), "seen": dict(seen or {})}


def band_status(state, alter, flash_kind: str) -> str:
    """상태 5종 + 대기(idle).

    run(실행 중) / done(완료 순간) / error(오류) / ready(가공 완료·수령 대기) / stopped(체인 정지) / idle.
    flash_kind 는 방금 일어난 사건("done"·"error"·"loop"·"") — 호출자가 1.5초 동안만 넘긴다."""
    st = state if isinstance(state, dict) else {}
    al = alter if isinstance(alter, dict) else {}
    running = bool(st.get("running"))
    if flash_kind == "error":
        return "error"
    if flash_kind in ("done", "loop"):
        return "done"
    if st.get("stopReason") and not running:
        return "stopped"
    if st.get("lastError") and not running:
        return "error"
    # 「가공만 돌릴 때」와 「전부 비어 있을 때」. 가르는 것은 **큐가 도느냐**지 큐에 카드가 몇 장이냐가 아니다 —
    # 상태 이름이 「가공만 **돌릴** 때」다. 카드를 담아 두고 시작을 안 눌렀으면 그것도 이 상태다.
    # 「수령 대기(ready)」보다 **먼저** 본다 (둘 다 맞으면 이쪽이다).
    if not running:
        has_alter = al.get("next") is not None or al.get("done") or (al.get("runTotal") or 0)
        return "alteronly" if has_alter else "empty"
    if al.get("done"):
        return "ready"      # 큐는 도는데 가공이 끝나 수령을 기다린다 (주황 강조)
    return "run"


BORDER = {"run": COLORS["line"], "idle": COLORS["line"], "done": COLORS["okline"],
          "error": COLORS["badline"], "ready": COLORS["warnline"], "stopped": COLORS["badline"],
          # 「전부 비어 있을 때」는 **점선**, 「가공만 돌릴 때」는 주황 rgba(240,163,90,.45)
          "empty": COLORS["sep"], "alteronly": COLORS["warnline2"]}
DASHED = ("empty",)      # 테두리를 점선으로 그리는 상태


def enable_dpi_awareness() -> str:
    """**`tk.Tk()` 를 만들기 전에** 프로세스를 모니터별 DPI 인식으로 바꾼다.

    창을 하나라도 만든 뒤에는 안 먹는다. 켜지 않으면 윈도우가 우리 창을 배율만큼 **늘려서**
    보여 주므로 글자가 뭉개지고(예: 125%), `GetDpiForWindow` 도 늘 96 만 돌려줘
    **배율을 잴 방법조차 없어진다.**"""
    try:
        u = ctypes.windll.user32
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):    # PER_MONITOR_AWARE_V2
            return "모니터별(v2)"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:     # PROCESS_PER_MONITOR_DPI_AWARE
            return "모니터별"
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "시스템"
    except Exception:
        pass
    return "못 켬 (창이 늘어나 글자가 뭉개집니다)"


def band_alpha(status: str, flashing: bool, settings) -> float:
    """평소 불투명도(설정), 사건 순간 100%. 「사건 때만 밝게」를 끄면 늘 평소 값.
    가공 완료·수령 대기는 85% 로 살짝 밝다."""
    s = settings if isinstance(settings, dict) else {}
    try:
        base = float(s.get("overlay_opacity", 55)) / 100.0
    except (TypeError, ValueError):
        base = 0.55
    base = min(1.0, max(0.2, base))
    if s.get("overlay_flash", True) and flashing:
        return 1.0
    if status == "stopped":
        return 1.0
    if status == "ready":
        return min(1.0, max(base, 0.85))
    if status == "alteronly":       # 85%
        return min(1.0, max(base, 0.85))
    if status == "empty":           # 45% — 볼 것이 없으면 가장 흐리다
        return min(base, 0.45)
    return base


# 게임 창의 상태. 최소화는 **없음과 같이** 다룬다 (덮을 화면이 없다) — 다만 사유는 따로 적는다.
GAME_OK, GAME_NONE, GAME_MIN = "ok", "none", "min"

# 안 보이는 이유. **설정 탭이 이 값을 그대로 보여 준다** — 「켰는데 아무것도 안 나타난다」가 되지 않게.
# 못 만드는 기능의 변명이 아니라 **지금 왜 안 보이는지**를 알려 주는 상태 표시다 (성격이 다르다).
HIDE_REASON = {
    "": "보이는 중",
    "off": "꺼짐",
    "no_game": "게임을 찾지 못해 숨김",
    "minimized": "게임이 최소화되어 숨김",
    "idle": "큐가 멈춰 있어 숨김 (설정)",
    "closing": "앱 창을 닫는 중이라 숨김",
    "test": "테스트 표시 중",
}


def visibility(state, settings, game=GAME_OK, app_gone: bool = False) -> tuple:
    """(지금 보여야 하는가, 안 보이면 왜) — `HIDE_REASON` 의 열쇠말을 돌려준다.

    - 스위치가 꺼져 있으면 안 보인다.
    - **게임이 없거나 최소화돼 있으면 안 보인다**. 다시 뜨면 나타난다.
    - 「큐가 멈추면 밴드 숨김」이 켜져 있으면 대기 상태에서 숨긴다. 단 멈춰 있어도 알릴 것
      (오류·정지)이 있으면 보인다 — 숨겨 놓고 「왜 아무 말이 없었나」가 되면 안 된다.

    game 은 GAME_OK/GAME_NONE/GAME_MIN. 옛 호출을 위해 True/False 도 받는다."""
    s = settings if isinstance(settings, dict) else {}
    st = state if isinstance(state, dict) else {}
    g = GAME_OK if game is True else GAME_NONE if game is False else str(game or GAME_NONE)
    if not s.get("overlay_enabled", False):
        return False, "off"
    # 앱 창이 닫히는 중이면 **게임 상태와 무관하게** 내린다. 프로세스는 창이 정말 갔는지
    # 확인하느라 10초를 더 사는데(server._watchdog_step), 그동안 밴드가 게임 위에 유령으로
    # 남는다. 닫는 게 아니라 새로고침이었으면 곧 다시 붙어 되살아난다.
    if app_gone:
        return False, "closing"
    if g == GAME_MIN:
        return False, "minimized"
    if g != GAME_OK:
        return False, "no_game"
    if s.get("overlay_hide_idle", False) and not (st.get("running") or st.get("lastError") or st.get("stopReason")):
        return False, "idle"
    return True, ""


def should_show(state, settings, game=GAME_OK) -> bool:
    return visibility(state, settings, game)[0]


def fullscreen_kind(style: int, client, monitor) -> str:
    """게임 창이 **화면을 통째로 덮는가**, 덮는다면 어떤 식인가.

    `"borderless"` — 테두리 없는 창. 우리 밴드가 **보인다**.
    `""` — 창 모드. 보인다.
    (**전용(exclusive) 전체화면**은 창 모양만으로는 못 가른다 — 모양이 borderless 와 같다.
    그건 `SHQueryUserNotificationState()` 가 `QUNS_RUNNING_D3D_FULL_SCREEN` 을 주는지로 따로 본다.)"""
    if not client or not monitor:
        return ""
    cx, cy, cw, ch = (int(v) for v in client)
    mx, my, mw, mh = (int(v) for v in monitor)
    covers = (cx <= mx and cy <= my and cw >= mw and ch >= mh)
    framed = bool(int(style) & (WS_CAPTION | WS_THICKFRAME))
    return "borderless" if (covers and not framed) else ""


def _sep(fg: str = ""):
    """세로 구분선: `width:1px height:14px background:rgba(255,255,255,.14)`.
    **세 개뿐이다** — 진행 뒤 · 회차 뒤 · 「일괄 수령」 뒤. 칸마다 넣으면 파이프로 도배된다."""
    return {"key": "sep", "kind": "sep", "pad": (5, 0), "fg": fg or COLORS["sep"]}


DAY_EVERY = 30.0     # 일간 리포트 한 줄을 다시 읽는 간격(초) — 장부 파일을 읽는 일이라 1초마다 돌리지 않는다
DAY_IDLE = ("empty", "alteronly")    # 오늘 한 줄을 보이는 상태 = **큐가 멈춰 있을 때만**


def day_cells(day, status: str, settings=None) -> list:
    """일간 리포트 한 줄의 칸 — 「· 오늘 날개 35 · 통나무 300 · 장작 40 · 외 2」.

    밴드는 칸 순서가 정해진 **한 줄**이고 빈 줄이 없다. 그래서 새 줄을 만들지 않고
    **큐가 멈춰 있을 때만**(`DAY_IDLE`) 대기 상태 글 「큐 비어 있음」 뒤에 붙인다. 큐가 돌면 그 자리는
    이름·진행·회차 칸의 것이므로 빠진다. 누르면 펼침 판에 7일 표가 열린다 (`bandpaint.ZONE_KEYS` "day")."""
    s = settings if isinstance(settings, dict) else {}
    if status not in DAY_IDLE or not isinstance(day, str) or not day.strip():
        return []
    if not s.get("overlay_show_queue", True):     # 대기 글과 한 묶음이다 — 큐 칸을 끄면 같이 빠진다
        return []
    C = COLORS
    text = day.strip()
    cells = [{"key": "daydot", "kind": "text", "text": "·", "fg": C["dim"], "font": "ui", "pad": (4, 8)}]
    # 꼬리 「· 실패 N」 은 붉은 칸으로 뗀다
    tag = f" · {ledger.FAIL_TAG} "
    if tag in text:
        head, _, n = text.rpartition(tag)
        cells.append({"key": "dayline", "kind": "text", "text": head, "fg": C["sub"], "font": "ui", "pad": (0, 10)})
        cells.append({"key": "dayfail", "kind": "text", "text": f"{ledger.FAIL_TAG} {n}", "fg": C["bad"], "font": "ui", "pad": (0, 14)})
    else:
        cells.append({"key": "dayline", "kind": "text", "text": text, "fg": C["sub"], "font": "ui", "pad": (0, 14)})   # 양옆을 조금 더 띄운다
    return cells


def band_cells(state, alter, settings, status: str, now: float | None = None, wings=None,
               day=None) -> list:
    """밴드 한 줄을 이루는 칸들. **설정에서 끈 항목은 아예 빠진다** — 그만큼 밴드가 짧아진다.

    여백(`pad`)·글꼴·색은 **디자인의 `style` 을 그대로 옮긴 값**이다.
    돌려주는 것: [{key, kind, text, fg, bg, font, pad}]. kind 는
    handle(⋮⋮) · dot(원) · text · badge(둥근 칩) · sep(1px 선) · expand/expand2(▾) · button(알약)."""
    st = state if isinstance(state, dict) else {}
    al = alter if isinstance(alter, dict) else {}
    s = settings if isinstance(settings, dict) else {}
    C = COLORS
    accent = C[ACCENT.get(status, "run")]      # 큐 진행 숫자와 그 앞 점이 상태를 따라간다
    bad_state = status in ("error", "stopped")
    fail = failed_item(st)
    # 이름 칸을 빨갛게 하는 것은 **그 이름이 실패한 항목일 때만**. 다른 카드가 도는 중에 앞 항목이 실패했으면
    # 이름 칸은 도는 카드의 것이다 — 빨갛게 하면 「거미줄이 실패했다」로 읽힌다.
    name_bad = bad_state and not (fail and fail["other"])

    # ── 묶음 A: ⋮⋮ · 큐 진행 · 종류 배지 + 이름 + 진행 ──
    empty = status in ("empty", "alteronly")      # 큐가 비었다 — 「대기 · 큐 비어 있음」
    ga = []
    if s.get("overlay_show_queue", True):
        prog = st.get("progress") if isinstance(st.get("progress"), dict) else {}
        if empty:
            # 점 #4b5665 · 「대기」 #6f7a8a 굵게 · 「큐 비어 있음」 #6f7a8a.
            # 담아 둔 카드가 있으면 그 수를 적는다 — 카드가 있는데 「비어 있음」이라고 쓰면 거짓말이다.
            n = int(prog.get("total") or 0) - int(prog.get("done") or 0)
            ga.append({"key": "qdot", "kind": "dot", "fg": C["emptydot"], "size": 5, "pad": (0, 5)})
            ga.append({"key": "queue", "kind": "text", "text": "대기", "fg": C["faint"],
                       "font": "bold", "pad": (0, 9)})
            ga.append({"key": "qempty", "kind": "text", "fg": C["faint"], "font": "ui", "pad": (0, 9),
                       "text": f"{n}건 담김" if n > 0 else "큐 비어 있음"})
            ga += day_cells(day, status, s)      # 일간 리포트 한 줄 — 멈춰 있을 때만
        else:
            txt = "정지" if status == "stopped" else f"{int(prog.get('done') or 0)}/{int(prog.get('total') or 0)}"
            ga.append({"key": "qdot", "kind": "dot", "fg": accent, "size": 5, "pad": (0, 5)})
            ga.append({"key": "queue", "kind": "text", "text": txt, "fg": accent, "font": "bold", "pad": (0, 9)})
    if s.get("overlay_show_name", True) and not empty:
        label, name, rest = split_card(st.get("currentCard"))
        if label or name:
            _, bbg, bfg = BADGE.get(str((st.get("currentCard") or {}).get("type") or ""), ("", C["sep"], C["sub"]))
            ga.append({"key": "type", "kind": "badge", "text": label, "fg": bfg, "bg": bbg,
                       "font": "badge", "pad": (0, 0)})
            ga.append({"key": "name", "kind": "text", "text": ellipsis(name, 8),
                       "fg": C["strongbad"] if name_bad else C["strong"], "font": "bold", "pad": (6, 6)})
            if rest:
                ga.append({"key": "prog", "kind": "text", "text": rest, "fg": C["fg2"], "font": "mono",
                           "pad": (0, 9)})

    # ── 묶음 B: 대기·완료·실패 · 회차 (+ 경과·날개) ──
    gb = []
    if s.get("overlay_show_columns", True) and not empty:
        col = st.get("columns") if isinstance(st.get("columns"), dict) else {}
        for i, (k, fg) in enumerate((("wait", C["fg2"]), ("done", C["oktext"]), ("fail", C["badtext"]))):
            if i:
                gb.append({"key": f"cdot{i}", "kind": "text", "text": "·", "fg": C["dim"], "font": "cols",
                           "pad": (3, 3)})
            gb.append({"key": f"col_{k}", "kind": "text", "text": str(int(col.get(k) or 0)), "fg": fg,
                       "font": "cols", "pad": (9 if i == 0 else 0, 9 if k == "fail" else 0)})
    if s.get("overlay_show_round", True):
        r = st.get("currentGroupRound")
        if isinstance(r, dict) and int(r.get("repeat") or 0) > 0:
            gb.append({"key": "round", "kind": "text", "font": "mono", "fg": C["gold"], "pad": (0, 9),
                       "text": f"{int(r.get('loop') or 0)}/{int(r.get('repeat') or 1)}회차"})
    if s.get("overlay_show_elapsed", False) and st.get("running"):
        t0 = run_started_at(st.get("events"))
        if t0:
            gb.append({"key": "elapsed", "kind": "text", "fg": C["fg2"], "font": "mono", "pad": (0, 9),
                       "text": fmt_mmss((now if now is not None else time.time()) - t0)})
    if s.get("overlay_show_wings", False) and wings is not None:
        gb.append({"key": "wings", "kind": "text", "text": f"날개 {int(wings)}", "fg": C["gold"],
                   "font": "mono", "pad": (0, 9)})

    # ── 묶음 C: 가공 · 펼침 ▾ · 「일괄 수령」 ──
    gc = []
    if s.get("overlay_show_alter", True) and (al.get("next") is not None or al.get("done")):
        gc.append({"key": "alterlbl", "kind": "text", "text": "가공", "fg": C["sub"], "font": "ui", "pad": (9, 7)})
        if al.get("next") is None:
            gc.append({"key": "alterdone", "kind": "text", "text": f"완료 {int(al['done'])}", "fg": C["warn"],
                       "font": "monob", "pad": (0, 6)})
        else:
            gc.append({"key": "altertime", "kind": "text", "text": fmt_mmss(al["next"]), "fg": C["warn"],
                       "font": "monob", "pad": (0, 6)})
            done = int(al.get("done") or 0)
            # 「가공만 돌릴 때」는 완료 수를 **0 이라도 보여 준다** (점이 가라앉을 뿐).
            # 큐가 도는 중에는 완료가 있을 때만 붙는다.
            if done or status == "alteronly":
                gc.append({"key": "alterdot", "kind": "dot", "size": 6, "pad": (0, 4),
                           "fg": C["ok"] if done else C["alterdot0"]})
                gc.append({"key": "alterdone", "kind": "text", "text": str(done), "font": "boldxs",
                           "pad": (0, 6), "fg": C["oktext"] if done else C["dim"]})
    if status == "empty":
        # 「전부 비어 있을 때」: `가공 0/12` 와 `창에서 담기` 만 남고, 그 사이에도 구분선이 있다.
        # 이 둘은 **펼칠 것이 없으므로** 가공 묶음(gc)이 아니라 따로 둔다 — 그래야 `▾` 가 하나만 붙는다.
        gb.append({"key": "alteridle", "kind": "text", "font": "ui", "fg": C["faint"], "pad": (9, 9),
                   "text": f"가공 {int(al.get('runDone') or 0)}/{alter_slots_total(al)}"})
        gb.append(_sep(C["sepfaint"]))
        # 누를 수 있다 — 앱 창을 앞으로 올리고 큐 탭의 담기 서랍을 연다 (`_on_hint`)
        gb.append({"key": "hint", "kind": "text", "text": "창에서 담기", "fg": C["emptyhint"],
                   "font": "ui", "pad": (9, 9)})
    # 가공 뒤의 `▾` 는 **가공 구간이 있을 때만** 붙는다.
    # 「큐만 돌 때」·「전부 비어 있을 때」는 `▾` 가 맨 끝의 하나뿐이다 — 두 개 그리면 안 된다.
    # (「체인 정지」에는 ▾ 도 「일괄 수령」도 없다.)
    if status != "stopped" and gc:
        gc.append({"key": "expand", "kind": "expand", "text": "▾", "fg": C["faint"], "font": "xs", "pad": (0, 6)})
        if s.get("overlay_show_collect", True) and gc:
            # **완료된 가공이 하나도 없으면 초록을 잃고 외곽선만 남는다 — 누를 수 없다.**
            # 버튼을 아예 빼지 않는 것이 핵심이다 (빼면 밴드 폭이 들썩이고 어디를 눌러야 하는지 잊는다).
            on = bool(al.get("targets"))
            gc.append({"key": "collect" if on else "collectoff", "kind": "button", "text": "일괄 수령",
                       "font": "boldxs", "pad": (0, 4),
                       "fg": C["collectfg"] if on else C["dim"],
                       "bg": C["collectbg"] if on else "",
                       "outline": "" if on else C["collectoffline"]})

    # ── 묶음 D: 오류 한 줄 ──
    gd = []
    # 연주 대기 — 연주 때문에 러너가 기다리는 중 (GET /api/queue.hold — 우선이 무엇이든: music 은 _hold_for_music, song 은
    # 곡 경계 부탁(_yield_after_song), work 는 합주·인사말이 걷히기를 기다리는 동안). 밴드는 30px 라 한 마디만:
    # 「연주 끝나면 시작」·「이 곡 끝나면 시작」·「합주·인사말 끝나면 시작」(workqueue.hold_short — tests/test_perf_hold.py 가 맞춘다).
    # 주황 = 「기다림」의 색(가공 타이머·수령 대기와 같은 계열). 오류가 아니므로 빨강이 아니다.
    hold = st.get("hold") if isinstance(st.get("hold"), dict) else None
    if hold and st.get("running"):
        gd.append({"key": "holddot", "kind": "dot", "fg": C["warn"], "size": 6, "pad": (8, 5)})
        gd.append({"key": "hold", "kind": "text", "text": workqueue.hold_short(hold.get("kind")), "fg": C["warn"],
                   "font": "boldxs", "pad": (0, 8)})
    if s.get("overlay_show_error", True):
        err = st.get("lastError") if isinstance(st.get("lastError"), dict) else None
        short = str((err or {}).get("short") or "").strip()
        room = 16
        if short:
            # 실패한 항목이 이름 칸의 카드가 **아니면** 그 이름을 같이 적는다 — 「실패 · 동 광석 · stopped」.
            # 이름을 빼면 옆의 이름 칸(지금 도는 거미줄)이 실패한 것처럼 읽힌다.
            nm = str((fail or {}).get("name") or "")
            if nm and not (fail or {}).get("current"):   # 멈춰 있을 때(이름 칸이 비었거나 다른 카드)도 이름을 붙인다
                short = f"실패 · {ellipsis(nm, 6)} · {short}"
                room = 24
            else:
                short = f"실패 · {short}"
        if st.get("stopReason") and not st.get("running"):
            why = str(st.get("stopReason") or "")
            # "performance" = 「완전한 연주 우선」이라 연주가 시작돼 멈춤 (workqueue.PERF_STOP_REASON · ERROR_SHORT 「연주 중 대기」)
            # "fatal:wing_cap"·"fatal:wing_waste" = 날개 차단기 강제 정지 (workqueue.ERROR_SHORT 「날개 상한」·「날개 헛소모」)
            short = ("정지 · 게임 창을 닫으세요" if why.startswith("fatal:blocked")
                     else "연주 중 대기" if why == "performance"
                     else "날개 상한" if why == "fatal:wing_cap"
                     else "날개 헛소모" if why == "fatal:wing_waste" else (short or "정지"))
        if short:
            gd.append({"key": "errdot", "kind": "dot", "fg": C["bad"], "size": 6, "pad": (8, 5)})
            gd.append({"key": "error", "kind": "text", "text": ellipsis(short, room), "fg": C["bad"],
                       "font": "boldxs", "pad": (0, 8)})

    # `⋮⋮` 는 묶음에 들지 않는다 — 그 뒤에는 구분선이 없다
    out = [{"key": "handle", "kind": "handle", "text": "⋮⋮", "fg": C["dim"], "font": "xs", "pad": (7, 7)}]
    first = True
    for blk in (ga, gb, gc, gd):
        if not blk:
            continue
        if not first:
            out.append(_sep(C["sepfaint"] if empty else ""))
        first = False
        out += blk
    # 맨 끝에 `▾` 를 두지 않는다. 대신 **대기 구역을 누르면** 그 판이 열린다
    # (`bandpaint.ZONE`). 여는 동작(`_on_expand`)은 그대로 살아 있다.
    # 마지막 칸의 오른쪽 여백은 **디자인 값 그대로** 둔다 (「일괄 수령」 여백 4px) — 여백을
    # 더 주면 버튼과 끝 사이가 벌어져 보인다.
    return out


def panel_head_cells(state, alter, settings, status: str, now=None) -> list:
    """펼친 판 머리줄의 칸들 — **밴드의 첫 묶음 그대로** (판 위에 밴드를 다시 보여 준다).

    `⋮⋮ · 큐 진행 · 종류 배지 · 이름 · 진행` 까지다. 구분선을 만나면 거기서 끊는다
    (열·회차·가공은 아래 목록이 더 자세히 보여 주므로 머리줄에 두 번 적지 않는다)."""
    out = []
    for c in band_cells(state, alter, settings, status, now):
        if c.get("kind") == "sep":
            break
        # `⋮⋮` 는 빼 둔다 — 판은 끌 수 없고, 밴드에 이미 있다
        if c.get("kind") in ("expand", "expand2", "handle"):
            continue
        out.append(c)
    return out


def day_table(stats, n: int = 7) -> list:
    """펼침 판의 7일 표 — [{d: "09-25", wings: 35, out: "통나무 300 · 장작 40 · 외 2"}] (최근 날부터).
    장부가 없으면 빈 목록 — 판은 표 없이 그려진다."""
    if not isinstance(stats, dict):
        return []
    return ledger.week_rows(stats, n)


def panel_rows(state, alter, settings) -> dict:
    """맨 끝 `▾` 를 펼쳤을 때 (대기 목록과 가공).

    `대기 N` 머리 + 「번호 | 이름 종류 | N회」 3줄, 그다음 `가공 D/T` 머리 +
    「작업명 ×n | 02:10 또는 완료」 줄들. 셋째 줄은 디자인에서 `opacity:.6` 으로 흐리다 —
    tkinter 는 위젯 투명도가 없으므로 흐린 색으로 낸다."""
    st = state if isinstance(state, dict) else {}
    al = alter if isinstance(alter, dict) else {}
    waits = []
    for it in st.get("items") or []:
        if not isinstance(it, dict) or it.get("column") != "wait":
            continue
        if it.get("type") == "group":
            sm = it.get("summary") if isinstance(it.get("summary"), dict) else {}
            name, detail = str(it.get("name") or ""), f"{int(sm.get('count') or 0)}항목"
        elif it.get("type") == "gather":
            name, detail = str(it.get("name") or ""), f"{int(it.get('target') or 0)}개"
        elif it.get("type") == "collect":
            name, detail = str(it.get("facility") or it.get("name") or ""), f"{int(it.get('count') or 0)}건"
        elif it.get("type") in ("play", "notify"):   # 연주·알림 — 회수가 없다. 종류 배지 글자를 오른칸에
            name, detail = str(it.get("name") or ""), BADGE[it["type"]][0]
        else:
            name, detail = str(it.get("name") or ""), f"{int(it.get('count') or 1)}회"
        # 줄은 **이름 그대로**다 (「판재 가공」·「판재 상자 제작」·「가죽 갑옷 상의」 — 전부 항목 이름).
        # 종류를 덧붙이면 「판재 가공 가공」처럼 겹친다.
        waits.append({"n": len(waits) + 1, "name": ellipsis(name, 18), "detail": detail,
                      "dim": len(waits) >= 2})
        if len(waits) >= 3:
            break
    works = []
    for w in al.get("works") or []:
        if w.get("done"):
            txt, fg = "완료", COLORS["oktext"]
        elif w.get("left") is None:
            txt, fg = "대기", COLORS["sub"]
        else:
            txt, fg = fmt_mmss(w["left"]), COLORS["warn"]
        works.append({"name": ellipsis(w.get("name") or "", 12), "n": int(w.get("n") or 1), "text": txt, "fg": fg})
    return {"waits": waits, "waitTotal": int((st.get("columns") or {}).get("wait") or 0),
            "works": works[:3], "alterDone": int(al.get("runDone") or 0), "alterTotal": int(al.get("runTotal") or 0)}


# ── 가공기 현황 판 ───────────────────────────
# 디자인의 SVG 를 **그대로** 옮긴다 (viewBox 0 0 256 256, stroke-width 9, 둥근 끝).
# tkinter 에는 SVG 가 없으므로 `svg_polylines` 가 선분으로 펴서 Canvas 에 긋는다.
FAC_ICONS = {
    "금속": ["M160 40l56 56-32 32-56-56z", "M128 72L40 160v56h56l88-88"],
    "목재": ["M128 24l64 96h-40l48 72H56l48-72H64z", "M128 192v40"],
    "가죽": ["M128 32c40 0 68 20 68 52 0 40-28 40-28 80 0 32-18 60-40 60s-40-28-40-60"
             "c0-40-28-40-28-80 0-32 28-52 68-52Z"],
    "옷감": ["M40 40h176v176H40z", "M40 96h176M40 152h176M96 40v176M152 40v176"],
    "약품": ["M96 32h64", "M104 32v64l-48 88a24 24 0 0021 36h102a24 24 0 0021-36l-48-88V32", "M70 176h116"],
    "직물": ["M80 112V80a48 48 0 0196 0v32", "M56 112h144v104H56z"],
}
# 시설 아이콘 색. 목록에 없는 시설은 기본 칩 색을 쓴다.
# 시설 아이콘 색 — 셋은 종류 배지와 같은 색이라 BADGE 에서 가져온다 (두 곳에 적지 않는다)
FAC_COLORS = {"금속": BADGE["craft"][2], "목재": COLORS["facwood"], "가죽": COLORS["facleather"],
              "옷감": BADGE["gather"][2], "약품": BADGE["alter"][2], "직물": BADGE["craft"][2]}
FAC_ICON_DEFAULT = BADGE["craft"][2]
FAC_SLOTS = 7        # 막대는 항상 7칸(없는 칸은 비활성)
FAC_ORDER = ("금속", "목재", "가죽", "옷감", "약품", "직물")   # 판에 늘어놓는 차례
# 칸 막대 색. 트랙은 반투명이라 판 배경(#090c11) 위에 섞은 값을 쓴다.
SLOT_STYLE = {
    "done": (COLORS["slotdone"], COLORS["ok"]),
    "run": (COLORS["slotrun"], COLORS["warn"]),
    "free": (COLORS["slotfree"], ""),    # 비어 있는 칸
    "none": (COLORS["slotnone"], ""),    # 없는 칸(비활성)
    "lock": (COLORS["slotlock"], ""),    # 잠김
}
SLOT_W, SLOT_H, SLOT_GAP, SLOT_R = 2.5, 12, 1.5, 1.25   # 디자인 치수


def alter_slots_total(alter) -> int:
    """밴드의 `가공 0/12` 에서 **12** — 가공기 칸이 모두 몇 개인가.

    **CLI 는 시설의 총 칸 수를 주지 않는다.** `get_altering_works` 는 지금 등록된 작업만 준다.
    그래서 **본 것 중 가장 많았던 수**를 시설마다 기억해 두고 더한다 (`store` 의 `alter_seen`).
    처음에는 작게 나오다가 가공기를 꽉 채워 본 순간 진짜 값이 된다 — 지어내지 않는다."""
    al = alter if isinstance(alter, dict) else {}
    seen = al.get("seen") if isinstance(al.get("seen"), dict) else {}
    now: dict = {}
    for w in al.get("works") or []:
        if isinstance(w, dict):
            now[facility_key(w.get("facility"))] = now.get(facility_key(w.get("facility")), 0) + 1
    keys = set(seen) | set(now)
    return sum(max(int(seen.get(k) or 0), int(now.get(k) or 0)) for k in keys)


def facility_key(name: str) -> str:
    """`약품 가공 시설` → `약품`. 시설 알약은 **두 글자 이름**을 쓴다 (폭 21px 고정).

    CLI 가 주는 `FacilityName` 은 display name 이라 뒤에 「가공 시설」이 붙는다. 앞 낱말만 쓴다.
    아는 이름이 아니면 앞 두 글자를 그대로 쓴다 (지어내지 않는다)."""
    t = str(name or "").strip()
    head = t.split()[0] if t else ""
    return head if head in FAC_ICONS else (head[:2] or t[:2])


def svg_polylines(paths, size: float, view: float = 256.0, steps: int = 8) -> list:
    """SVG `d` 를 Canvas 용 선분 묶음으로. M/L/H/V/C/S/A/Z (대소문자) 만 다룬다 — 아이콘이 쓰는 전부다.

    곡선(C/S)과 호(A)는 **선분으로 휈다.** 12px 로 줄여 그리므로 8토막이면 눈으로는 곡선이다.
    크게 그릴 때는 `steps` 를 올린다 (앱 아이콘을 1024px 로 뽑을 때 쓴다 — `tools/make_icon.py`)."""
    import math
    out = []
    for d in paths or []:
        num = []
        i, n = 0, len(d)
        cur = start = (0.0, 0.0)
        cmd = ""
        pts = []
        prev_c2 = None

        def flush(pts=None):
            pass

        segs = []
        while i < n:
            ch = d[i]
            if ch.isalpha():
                cmd = ch
                num = []
                if cmd in "Zz":
                    if pts:
                        pts.append(start)
                    cur = start
                i += 1
                continue
            if ch in " ,":
                i += 1
                continue
            # **호(A/a)의 두 깃발은 한 글자다.** SVG 는 `0 0 21 36` 을 `0021 36` 으로 붙여 쓸 수 있고,
            # 약품·직물 아이콘이 실제로 그렇게 적혀 있다. 한 덩이로 읽으면 21 이 되어 호가 뭉개진다.
            if cmd in "Aa" and len(num) in (3, 4):
                num.append(1.0 if d[i] == "1" else 0.0)
                i += 1
            else:
                j = i
                if d[j] in "+-":
                    j += 1
                while j < n and (d[j].isdigit() or d[j] == "."):
                    j += 1
                if j == i:
                    i += 1
                    continue
                num.append(float(d[i:j]))
                i = j
            need = {"M": 2, "m": 2, "L": 2, "l": 2, "H": 1, "h": 1, "V": 1, "v": 1,
                    "C": 6, "c": 6, "S": 4, "s": 4, "A": 7, "a": 7}.get(cmd, 2)
            if len(num) < need:
                continue
            a = num[:need]
            del num[:need]
            rel = cmd.islower()
            if cmd in "Mm":
                cur = (cur[0] + a[0], cur[1] + a[1]) if rel else (a[0], a[1])
                if len(pts) > 1:
                    segs.append(pts)
                pts = [cur]
                start = cur
                cmd = "l" if rel else "L"      # SVG 규칙: M 뒤에 오는 좌표쌍은 L
                prev_c2 = None
            elif cmd in "LlHhVv":
                if cmd in "Ll":
                    cur = (cur[0] + a[0], cur[1] + a[1]) if rel else (a[0], a[1])
                elif cmd in "Hh":
                    cur = (cur[0] + a[0], cur[1]) if rel else (a[0], cur[1])
                else:
                    cur = (cur[0], cur[1] + a[0]) if rel else (cur[0], a[0])
                pts.append(cur)
                prev_c2 = None
            elif cmd in "CcSs":
                if cmd in "Cc":
                    c1 = (cur[0] + a[0], cur[1] + a[1]) if rel else (a[0], a[1])
                    c2 = (cur[0] + a[2], cur[1] + a[3]) if rel else (a[2], a[3])
                    end = (cur[0] + a[4], cur[1] + a[5]) if rel else (a[4], a[5])
                else:
                    c1 = (2 * cur[0] - prev_c2[0], 2 * cur[1] - prev_c2[1]) if prev_c2 else cur
                    c2 = (cur[0] + a[0], cur[1] + a[1]) if rel else (a[0], a[1])
                    end = (cur[0] + a[2], cur[1] + a[3]) if rel else (a[2], a[3])
                for k in range(1, steps + 1):
                    t = k / float(steps)
                    u = 1 - t
                    pts.append((u * u * u * cur[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * end[0],
                                u * u * u * cur[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * end[1]))
                prev_c2, cur = c2, end
            elif cmd in "Aa":
                # 호는 **현으로 잉지 않고** 중심을 구해 휈다 (약품 플라스크 바닥·직물 실패가 이것이다)
                rx, ry, rot = abs(a[0]), abs(a[1]), math.radians(a[2])
                large, sweep = a[3], a[4]
                end = (cur[0] + a[5], cur[1] + a[6]) if rel else (a[5], a[6])
                if rx and ry and end != cur:
                    cs, sn = math.cos(rot), math.sin(rot)
                    dx, dy = (cur[0] - end[0]) / 2.0, (cur[1] - end[1]) / 2.0
                    x1, y1 = cs * dx + sn * dy, -sn * dx + cs * dy
                    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
                    if lam > 1:
                        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
                    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
                    co = (math.sqrt(max(0.0, rx * rx * ry * ry - den) / den) if den else 0.0)
                    co *= -1 if large == sweep else 1
                    cxp, cyp = co * rx * y1 / ry, -co * ry * x1 / rx
                    cx = cs * cxp - sn * cyp + (cur[0] + end[0]) / 2.0
                    cy = sn * cxp + cs * cyp + (cur[1] + end[1]) / 2.0
                    th0 = math.atan2((y1 - cyp) / ry, (x1 - cxp) / rx)
                    th1 = math.atan2((-y1 - cyp) / ry, (-x1 - cxp) / rx)
                    dth = th1 - th0
                    if sweep and dth < 0:
                        dth += 2 * math.pi
                    if not sweep and dth > 0:
                        dth -= 2 * math.pi
                    for k in range(1, steps + 1):
                        th = th0 + dth * k / float(steps)
                        ex, ey = rx * math.cos(th), ry * math.sin(th)
                        pts.append((cs * ex - sn * ey + cx, sn * ex + cs * ey + cy))
                else:
                    pts.append(end)
                cur = end
                prev_c2 = None
        if len(pts) > 1:
            segs.append(pts)
        for seg in segs:
            out.append([(x * size / view, y * size / view) for x, y in seg])
    return out


def slot_pct(w) -> int:
    """진행 칸을 얼마나 채울까. **비율은 그리지 않는다** — 0 이면 「자리만 차 있음」 표시다.

    CLI 는 남은 시간만 주고 한 회가 몇 초인지 안 준다. 「본 것 중 가장 큰 남은 시간」으로
    추정해 봤더니 **한 회를 끝까지 못 본 작업은 거의 꽉 찬 것처럼 보였다** — 막 시작한 가공이
    100% 로 보이는 것이다.

    기준이 모호하면 그리지 않는 쪽이 맞다. 그래서 **진행 중인 칸은 맨 아래 2px 만** 주황으로
    칠해 「이 칸이 돌고 있다」만 알린다. 완료 칸만 초록으로 꽉 찬다 — 그건 확실히 아는 사실이다."""
    return 0


def facility_slots(works) -> list:
    """한 시설의 칸 막대 7개.

    **우리가 아는 것은 「등록된 작업」뿐이다.** 완료 칸은 초록 100%, 진행 칸은 주황이 차오른다.
    시설의 **총 칸 수와 잠김 여부는 CLI 가 주지 않으므로**(`get_altering_works` 는 등록된 작업만 준다)
    남은 자리는 「없는 칸(비활성)」으로 둔다 — 빈 칸인 척 하지 않는다."""
    slots = []
    for w in sorted(works or [], key=lambda x: (not x.get("done"), x.get("left") or 0)):
        # 줄은 같은 시설·같은 이름을 「이름 ×n」 한 줄로 묶는다 — 막대는 **작업 하나에 하나**라 n 만큼 그린다
        # (그러지 않으면 같은 작업 두 건이 한 줄로 묶여 막대가 하나만 보인다)
        for _ in range(max(1, int(w.get("n") or 1))):
            if w.get("done"):
                slots.append({"kind": "done", "pct": 100})
            elif w.get("wait"):
                slots.append({"kind": "free", "pct": 0})      # 등록만 되고 아직 시작 안 한 칸
            else:
                slots.append({"kind": "run", "pct": slot_pct(w)})
            if len(slots) >= FAC_SLOTS:
                break
        if len(slots) >= FAC_SLOTS:
            break
    while len(slots) < FAC_SLOTS:
        slots.append({"kind": "none", "pct": 0})
    return slots


def _dim_fac(key: str) -> dict:
    """작업이 없는 시설. 테두리·이름·시간·점이 전부 가라앉고 막대는 빈 트랙만."""
    return {"key": key, "icon": FAC_ICONS.get(key, ()), "iconColor": COLORS["dimname"],
            "time": "—", "timeColor": COLORS["dimtime"], "nameColor": COLORS["dimname"],
            "badge": "", "border": COLORS["dimborder"], "bg": COLORS["bg"],
            "dotColor": COLORS["dimdot"], "dim": True,
            "slots": [{"kind": "free", "pct": 0} for _ in range(FAC_SLOTS)]}


def facility_panel(alter, status: str = "") -> dict:
    """「가공기 현황」 판이 그릴 것. 시설마다 알약 하나.

    **작업이 없는 시설도 빠지지 않는다** — 「전부 비어 있을 때」도 시설을 전부 흐리게 보여 준다.
    비어 있는 시설 이름은 `learn_facilities` 가 적어 둔 기억에서 가져온다."""
    al = alter if isinstance(alter, dict) else {}
    by = {}
    for w in al.get("works") or []:
        if not isinstance(w, dict):
            continue
        by.setdefault(facility_key(w.get("facility")), []).append(w)
    seen = al.get("seen") if isinstance(al.get("seen"), dict) else {}
    facs = []
    # `FAC_ORDER` 차례대로. 모르는 시설은 뒤에 이름순으로 붙인다 (빠뜨리지 않는다)
    known = set(by) | set(seen)
    order = [k for k in FAC_ORDER if k in known] + sorted(k for k in known if k not in FAC_ORDER)
    for key in order:
        works = by.get(key)
        if not works:
            facs.append(_dim_fac(key))      # 본 적은 있지만 지금은 빈 시설
            continue
        done = sum(max(1, int(w.get("n") or 1)) for w in works if w.get("done"))   # 묶인 줄은 n 건
        lefts = [w.get("left") for w in works if not w.get("done") and w.get("left") is not None]
        nxt = min(lefts) if lefts else None
        facs.append({
            "key": key,
            "icon": FAC_ICONS.get(key, ()),
            "iconColor": FAC_COLORS.get(key, FAC_ICON_DEFAULT),
            "time": fmt_fac_time(nxt) if nxt is not None else ("완료" if done else "—"),
            "timeColor": COLORS["warn"] if nxt is not None else (COLORS["oktext"] if done else COLORS["faint"]),
            "nameColor": COLORS["strong"] if (nxt is not None or done) else COLORS["label"],
            "badge": str(done) if done else "",
            # 수령할 것이 있는 시설만 초록 테두리·배경·초록 점
            "border": COLORS["okline"] if done else COLORS["sep"],
            "bg": COLORS["facbgok"] if done else COLORS["facbg"],
            "dotColor": COLORS["ok"] if done else COLORS["warn"],
            "dim": False,
            "slots": facility_slots(works),
        })
    live = sum(1 for f in facs if not f["dim"])
    # 판의 두 모습. **밴드와 같은 상태를 쓴다** — 판만 보고 정하면 「가공만 돌릴 때」를 못 가른다
    # (그 상태는 시설이 돌고 있으므로 판만 봐서는 평소와 똑같이 보인다).
    mode = "empty" if (status == "empty" or not live) else ("alteronly" if status == "alteronly" else "normal")
    return {"facs": facs, "collect": int(al.get("done") or 0), "mode": mode,
            "dashed": mode == "empty",
            "titleColor": COLORS["emptyhint"] if mode == "empty" else COLORS["gold"],
            # 비었을 때 rgba(255,255,255,.14) 점선 · 가공만 돌 때 rgba(240,163,90,.45) 실선
            "frame": COLORS["sep"] if mode == "empty" else COLORS["warnline2"],   # 주황 테두리
            "note": "" if live else f"가공 {int(al.get('runDone') or 0)} / {alter_slots_total(al)}"}


def alter_panel_rows(alter) -> dict:
    """가공 그룹 뒤의 `▾` 를 펼쳤을 때 (가공 시설 칸).

    머리: `가공 D/T · 02:10 · [일괄 수령 N] · ▴`.
    칸: 「작업명 ×n · 시설」 | 「02:10」 / 「● 완료 · 수령 대기」 / 흐린 「빈 칸 · 시설」 | 「—」.
    **「빈 칸」은 작업이 하나도 없는 시설이다.** 우리는 대기열에 나타난 시설만 알 수 있으므로
    그런 시설이 보일 때만(= 작업이 전부 끝나 없어진 시설) 흐린 줄로 낸다."""
    al = alter if isinstance(alter, dict) else {}
    rows = []
    for w in al.get("works") or []:
        if not isinstance(w, dict):
            continue
        if w.get("done"):
            txt, fg, dot = "완료 · 수령 대기", COLORS["oktext"], True
        elif w.get("left") is None:
            txt, fg, dot = "대기", COLORS["sub"], False
        else:
            txt, fg, dot = fmt_mmss(w["left"]), COLORS["warn"], False
        rows.append({"name": ellipsis(w.get("name") or "", 10), "n": int(w.get("n") or 1),
                     "facility": ellipsis(w.get("facility") or "", 8), "text": txt, "fg": fg,
                     "dot": dot, "dim": False})
    for f in al.get("facilities") or []:      # 작업이 하나도 없는 시설 = 「빈 칸」
        if isinstance(f, dict) and not f.get("done") and not f.get("running"):
            rows.append({"name": "빈 칸", "n": 0, "facility": ellipsis(f.get("facility") or "", 8),
                         "text": "—", "fg": COLORS["faint"], "dot": False, "dim": True})
    return {"rows": rows[:3], "done": int(al.get("runDone") or 0), "total": int(al.get("runTotal") or 0),
            "next": al.get("next"), "collect": int(al.get("done") or 0)}


def game_band_pos(client, band_w: int, band_h: int = BAND_H) -> tuple:
    """게임 **클라이언트 영역**(x, y, w, h · 화면 좌표) 위에서 밴드가 있어야 할 자리.

    밴드는 「채팅을 펼쳐도 안 겹치는 상단 여백」에 둔다 — 1280 기준 x240~1002.
    가로는 클라이언트 가운데, 세로는 디자인의 윗변(14/780)을 클라이언트 높이에 맞춰 환산한다.
    창 테두리·제목표시줄을 빼야 하므로 반드시 GetClientRect + ClientToScreen 의 값을 넣는다
    (실측: 창 전체 (873,166) 1372x771 ↔ 클라이언트 (880,197) 1357x733 — 7px·31px 차이가 그것이다)."""
    cx, cy, cw, ch = (int(v) for v in client)
    band_w = max(1, int(band_w))
    x = cx + (cw - band_w) // 2
    left = cx + round(DESIGN_SAFE[0] * cw / DESIGN_W)
    right = cx + round(DESIGN_SAFE[1] * cw / DESIGN_W)
    if band_w <= right - left:      # 안전 구역보다 좁으면 그 안에 들어가게 당긴다
        x = min(max(x, left), right - band_w)
    return x, cy + max(0, round(DESIGN_TOP * ch / DESIGN_H))


def _find_item(state, iid):
    """state.items 에서 id 로 한 장 (그룹 자식까지). 없으면 None."""
    if not iid:
        return None
    for it in (state.get("items") if isinstance(state, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        if it.get("id") == iid:
            return it
        for k in it.get("items") or []:
            if isinstance(k, dict) and k.get("id") == iid:
                return k
    return None


def failed_item(state) -> dict | None:
    """`lastError` 가 가리키는 **실패한 그 항목** → {id, name, type, label, short, current} | None.

    이름은 `lastError.name`(workqueue 가 싣는다) → 없으면 `items` 에서 id 로 → 없으면 그 id 의 오류 회신 글
    (「이름: 코드 — …」)에서. **지금 도는 카드(currentCard)의 이름은 절대 빌리지 않는다** — 빌리면
    동 광석이 실패했는데 「거미줄 채집 실패」가 뜬다.
    `current` = 실패한 항목이 지금 도는 카드와 같은가 (이름 칸이 이미 그 항목을 말하고 있는가).
    `other` = 실패한 항목이 **확실히** 도는 카드와 다른가 (id 를 알고, 도는 중이고, 다르다). id 를 모르면 거짓 —
    모르는 것을 「다른 항목」이라고 단정하지 않는다."""
    st = state if isinstance(state, dict) else {}
    err = st.get("lastError") if isinstance(st.get("lastError"), dict) else None
    if not err:
        return None
    iid = err.get("id")
    it = _find_item(st, iid) or {}
    name = str(err.get("name") or it.get("name") or "")
    typ = str(err.get("type") or it.get("type") or "")
    if not name and iid:
        for e in reversed(list(st.get("events") or [])):
            if isinstance(e, dict) and e.get("id") == iid and e.get("kind") == "error" and e.get("msg"):
                name = str(e["msg"]).split(": ", 1)[0]
                break
    card = st.get("currentCard") if isinstance(st.get("currentCard"), dict) else {}
    short = str(err.get("short") or err.get("message") or err.get("code") or err.get("error") or "")
    return {"id": iid, "name": name, "type": typ, "label": BADGE.get(typ, ("", "", ""))[0], "short": short,
            "current": bool(iid) and bool(st.get("running")) and card.get("id") == iid,
            "other": bool(iid) and bool(st.get("running")) and card.get("id") != iid}


def toast_lines(kind: str, state) -> tuple:
    """토스트 두 줄 → (윗줄, 꼬리표, 아랫줄). 창 코드(`_show_toast`)와 갈라 둔 순수 함수 — 여기만 테스트한다.

    실패는 **실패한 항목**(`failed_item`)의 이름·종류로 쓴다 — 지금 도는 카드가 아니다.
    완료는 **방금 끝난 항목**(마지막 `done` 회신)으로 쓴다 — 끝나는 순간 러너는 이미 다음 카드로 넘어가 있다."""
    st = state if isinstance(state, dict) else {}
    card = st.get("currentCard") if isinstance(st.get("currentCard"), dict) else {}
    if kind == "error":
        f = failed_item(st) or {}
        t1 = f"{f.get('name') or ''} {f.get('label') or ''}".strip() or "큐"
        return t1, "실패", str(f.get("short") or "")
    if kind == "loop":
        r = st.get("currentGroupRound") if isinstance(st.get("currentGroupRound"), dict) else {}
        return "회차 전환", "", f"{int(r.get('loop') or 0)}/{int(r.get('repeat') or 1)}회차 시작"
    if kind == "notify":   # 알림 카드·큐 종료 — 마지막 알림의 글
        n = last_notice(st) or {}
        return "알림", "", str(n.get("text") or "")
    ev = None
    for e in reversed(list(st.get("events") or [])):
        if isinstance(e, dict) and e.get("kind") == "done":
            ev = e
            break
    if ev is not None and ev.get("id") != card.get("id"):
        it = _find_item(st, ev.get("id")) or {}
        msg = str(ev.get("msg") or "")
        name = str(it.get("name") or msg.split(": ", 1)[0])
        label = BADGE.get(str(it.get("type") or ""), ("", "", ""))[0]
        rest = msg.split(": ", 1)[1] if ": " in msg else ""
        return f"{name} {label}".strip() or "항목", "완료", rest
    label = BADGE.get(str(card.get("type") or ""), ("", "", ""))[0]
    return f"{card.get('name') or ''} {label}".strip() or "항목", "완료", str(card.get("text") or "")


def run_started_at(events) -> float:
    """이번 실행이 시작된 시각 (0 = 모름). 마지막 「정지」**·「연주 대기」** 이후로 거슬러 올라가 **가장 이른** start/loop.

    `ui/js/board.js` 의 `runStart()` 와 **같은 규칙**이다 — 두 곳이 다르면 밴드의 경과와 실행 줄의 경과가 어긋난다.
    연주 대기(`hold` 회신)도 경계다: 대기가 끝나면 서버가 `start` 를 한 번 더 적으므로 그 뒤부터 센다 — 기다린 시간은 작업 시간이
    아니다. `/api/queue` 가 이미 싣는 `events` 만 쓴다 (새 API 없음)."""
    t = 0.0
    for e in reversed(list(events or [])):
        if not isinstance(e, dict):
            continue
        k = e.get("kind")
        if k in ("stop", "hold"):
            break
        if k in ("start", "loop"):
            try:
                t = float(e.get("t") or t)
            except (TypeError, ValueError):
                pass
    return t


def offset_band_pos(client, off, band_w: int, band_h: int = BAND_H) -> tuple:
    """사용자가 끌어 둔 자리 — **게임 클라이언트 영역 기준 상대 위치(오프셋)** 로 따라간다.

    화면 절대 좌표를 저장하면 게임 창이 움직였을 때 따라갈 수 없다. `(밴드 좌상단 − 클라이언트 좌상단)`
    을 저장해 두고 매번 더한다. 게임 창 **크기**가 바뀌어 밴드가 클라이언트 밖으로 나가면 안쪽으로 당긴다."""
    cx, cy, cw, ch = (int(v) for v in client)
    ox, oy = (int(v) for v in off)
    return clamp_pos(cx + ox, cy + oy, band_w, band_h, cw, ch, cx, cy)


def band_offset(pos, client) -> tuple:
    """밴드를 끌어 놓은 화면 좌표 → 저장할 오프셋."""
    return int(pos[0]) - int(client[0]), int(pos[1]) - int(client[1])


def clamp_pos(x: int, y: int, w: int, h: int, sw: int, sh: int, ox: int = 0, oy: int = 0) -> tuple:
    """(sw×sh) 안으로 끌어온다. ox·oy 를 주면 그 지점을 왼쪽 위로 삼는다 (게임 클라이언트 영역용).
    저장된 위치가 화면 밖이면(모니터가 바뀌었다) 안 보이는 창이 되어 못 되돌린다 — 그걸 막는다."""
    x = int(x); y = int(y); w = max(1, int(w)); h = max(1, int(h))
    x = min(max(x, ox), max(ox, ox + int(sw) - w))
    y = min(max(y, oy), max(oy, oy + int(sh) - h))
    return x, y


def toast_kind(settings, kind: str) -> bool:
    """오류 토스트를 띄울지. 「켜짐」은 오류 + 회차 완료, 「오류만」은 오류만, 「끔」은 없음.
    `notify`(알림 카드·큐 종료)는 사람이 **일부러 담은** 알림이라 「끔」이 아니면 띄운다."""
    mode = str((settings or {}).get("overlay_toast") or "error")
    if mode == "off":
        return False
    if kind == "notify":
        return True
    if mode == "error":
        return kind == "error"
    return kind in ("error", "loop", "done")


def last_notice(state) -> dict | None:
    """`GET /api/queue` 의 `notices` 마지막 하나 (없으면 None)."""
    st = state if isinstance(state, dict) else {}
    ns = [n for n in (st.get("notices") or []) if isinstance(n, dict)]
    return ns[-1] if ns else None


def notice_key(state) -> str:
    """알림 하나를 가르는 열쇠 (id 는 프로세스마다 1부터라 시각을 같이 본다)."""
    n = last_notice(state)
    return f"{n.get('t')}|{n.get('id')}" if n else ""


# ══════════════════════════════════════════════════════════════════════════
# 창 (Tk 전용 스레드)
# ══════════════════════════════════════════════════════════════════════════
class _PT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(_ACCENTPOLICY)),
                ("SizeOfData", ctypes.c_size_t)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _abgr(hex_color: str, alpha: int) -> int:
    """#rrggbb → 0xAABBGGRR (비공개 accent API 가 쓰는 순서). 색은 COLORS 에서만 온다."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return ((int(alpha) & 0xFF) << 24) | (b << 16) | (g << 8) | r


class _U32:
    """user32 · gdi32 · kernel32 호출 묶음.

    **restype 를 반드시 못 박는다.** 기본값은 `c_int` 라 64비트에서 핸들(HWND·HRGN·HANDLE)이 잘려
    엉뚱한 값이 되고, 증상은 「조용히 아무 일도 안 일어남」이라 원인을 찾기 어렵다."""

    def __init__(self):
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        k = ctypes.windll.kernel32
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.restype = ctypes.c_long
        u.GetAncestor.restype = wintypes.HWND
        u.WindowFromPoint.restype = wintypes.HWND
        u.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        u.SetWindowRgn.restype = ctypes.c_int
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_PT)]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        g.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        g.CreateRoundRectRgn.restype = wintypes.HRGN
        # 점선 테두리용 — **핸들을 돌려주는 함수는 restype 을 반드시 준다** (64비트에서 잘린다)
        g.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        g.CreateRectRgn.restype = wintypes.HRGN
        g.SetRectRgn.argtypes = [wintypes.HRGN] + [ctypes.c_int] * 4
        g.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
        g.CombineRgn.restype = ctypes.c_int
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.MonitorFromWindow.restype = wintypes.HANDLE
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        self.u, self.g, self.k = u, g, k
        self.dwm = self.sh = None
        try:    # dwmapi·shell32 는 없을 수도 있다 (없으면 그 기능만 조용히 빠진다)
            self.dwm = ctypes.windll.dwmapi
            self.dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            self.dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        except Exception:
            self.dwm = None
        try:
            self.sh = ctypes.windll.shell32
            self.sh.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
            self.sh.SHQueryUserNotificationState.restype = ctypes.c_long
        except Exception:
            self.sh = None

    # ── 다크 모드 · 배경 흐림 ──
    def dark_mode(self, hwnd: int):
        """어두운 창이라고 DWM 에 알린다. **흰 테두리 방지용이고 합성과는 무관하다** — 흐림을 끄더라도 건다.
        돌려주는 것: HRESULT(0=성공) 또는 None(dwmapi 없음)."""
        if self.dwm is None:
            return None
        try:
            v = ctypes.c_int(1)
            return self.dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                                  ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            return None

    def blur(self, hwnd: int) -> str:
        """창 뒤를 흐리게 (설계의 `backdrop-filter`). 쓴 방법(`"dwm"`·`"accent"`) 또는 `""`.

        **「반투명 글래스」를 고른 때만 부른다.** 끌 때는 되돌리는 호출조차 하지 않는다 —
        `WS_EX_LAYERED` 알파와 함께 걸면 **창이 아예 합성되지 않는다**(실기 확인)."""
        if self.dwm is not None:
            try:
                v = ctypes.c_int(DWMSBT_TRANSIENTWINDOW)
                if self.dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
                                                  ctypes.byref(v), ctypes.sizeof(v)) == 0:
                    return "dwm"
            except Exception:
                pass
        fn = getattr(self.u, "SetWindowCompositionAttribute", None)   # 비공개 — 없는 빌드도 있다
        if fn is None:
            return ""
        try:
            pol = _ACCENTPOLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, _abgr(COLORS["bg"], 0x40), 0)
            data = _WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.pointer(pol), ctypes.sizeof(pol))
            return "accent" if fn(wintypes.HWND(hwnd), ctypes.byref(data)) else ""
        except Exception:
            return ""

    # ── 게임이 화면을 통째로 덮는가 ──
    def monitor_rect(self, hwnd: int):
        """그 창이 있는 모니터의 사각형 (x, y, w, h). 못 구하면 None."""
        try:
            mon = self.u.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
            if not mon:
                return None
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if not self.u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return None
            r = mi.rcMonitor
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            return None

    def dpi_scale(self, hwnd: int) -> float:
        """그 창이 놓인 모니터의 배율 (125% → 1.25). 못 읽으면 1.0.

        **인식을 켠 뒤에만 진짜 값이 나온다** — 비인식 프로세스에는 늘 96(=1.0)을 돌려준다."""
        try:
            self.u.GetDpiForWindow.argtypes = [wintypes.HWND]
            self.u.GetDpiForWindow.restype = ctypes.c_uint
            d = int(self.u.GetDpiForWindow(wintypes.HWND(hwnd)) or 0)
            if d >= 48:
                return d / 96.0
        except Exception:
            pass
        return 1.0

    def window_rect(self, hwnd: int):
        """그 창의 (x, y, w, h). 못 읽으면 None."""
        try:
            r = wintypes.RECT()
            if not self.u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
                return None
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            return None

    def style(self, hwnd: int) -> int:
        return self.u.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE)

    # ── Z 순서: 게임보다만 위 ──
    # **실제 게임 창에 직접 걸어 잰 값이다:**
    #  · `GetWindow(게임, GW_HWNDPREV)` 는 **보이지 않는 창**(`Default IME`·`MSCTFIME UI`·시스템 트레이)을
    #    준다. 그런 창을 기준으로 `SetWindowPos` 를 부르면 **False 를 돌려주고 아무 일도 안 일어난다.**
    #    → 거슬러 올라가며 **보이지 않는 창과 폭 50px 미만인 창을 건너뛰고** 첫 「보이는」 창을 기준으로 삼는다.
    #  · `HWND_TOP`(0) 은 True 를 돌려주면서 **순서를 안 바꾼다** (Tk 창의 성질).
    #  · `SetWindowPos(게임, 밴드)` 는 **False** — 남의 창은 못 옮긴다. 우리 창만 움직인다.
    #  · `HWND_TOPMOST` → `HWND_NOTOPMOST` 는 **보통 창 전체의 맨 앞**으로 올린다. 아무 때나 쓰면
    #    디스코드까지 덮으므로 **기준 창을 못 찾았을 때만** 쓴다 (`insert_below` 주석 참조).
    #    실측: 둘 다 True, `ex 0x800a0 → 0x800a8 → 0x800a0`, 끝난 자리는 게임 바로 위.
    def lwa_locked(self, hwnd: int) -> bool:
        """그 창이 `SetLayeredWindowAttributes` 방식에 고정돼 있나.

        고정돼 있으면 `UpdateLayeredWindow` 는 **True 를 돌려주면서 한 픽셀도 안 그린다.**
        Tk 의 투명도 속성(`-alpha`)이 곧 그 호출이다 — 한 번만 걸어도 창이 그쪽으로 굳는다."""
        try:
            self.u.GetLayeredWindowAttributes.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
            key, a, fl = wintypes.DWORD(), ctypes.c_ubyte(), wintypes.DWORD()
            return bool(self.u.GetLayeredWindowAttributes(
                wintypes.HWND(hwnd), ctypes.byref(key), ctypes.byref(a), ctypes.byref(fl)))
        except Exception:
            return False

    def is_topmost(self, hwnd: int) -> bool:
        try:
            return bool(self.ex(hwnd) & WS_EX_TOPMOST)
        except Exception:
            return False

    def _real_window(self, hwnd: int, ours) -> bool:
        """실제로 화면을 차지하는 남의 창인가 (숨은 IME·트레이 창과 우리 창을 거른다).

        **「항상 맨 위」 창은 기준으로 삼지 않는다.** `SetWindowPos` 는 최상위 창 아래에 끼우면
        끼운 창까지 **최상위로 만든다**(문서화된 동작). 게임이 맨 앞일 때는 게임 위에 보통 창이
        하나도 없어 작업표시줄(`Shell_TrayWnd`, 보이고 폭도 넓다)이 기준으로 잡혔고,
        그 순간 밴드가 최상위가 되어 **터미널·브라우저까지 덮었다** (실측: ex 0x800a0 → 0x800a8)."""
        if not hwnd or int(hwnd) in ours:
            return False
        try:
            if not self.u.IsWindowVisible(wintypes.HWND(hwnd)):
                return False
            if self.is_topmost(hwnd):
                return False
            r = wintypes.RECT()
            if not self.u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
                return False
            return (r.right - r.left) >= Z_MIN_W
        except Exception:
            return False

    def insert_target(self, game: int, ours=()) -> int:
        """밴드를 끼워 넣을 기준 창 — **게임 위의 첫 「보이는」 창.** 못 찾으면 `HWND_TOP`."""
        mine = {int(h) for h in ours if h}
        h = int(game)
        for _ in range(64):
            try:
                h = int(self.u.GetWindow(wintypes.HWND(h), GW_HWNDPREV) or 0)
            except Exception:
                return HWND_TOP
            if not h:
                return HWND_TOP
            if self._real_window(h, mine):
                return h
        return HWND_TOP

    def insert_below(self, target: int, hwnds) -> None:
        """`target` 바로 아래에 차례로 끼운다 (`SetWindowPos(X, Y)` 는 X 를 Y **아래**에 놓는다).
        같은 target 에 순서대로 넣으면 **나중에 넣은 것이 더 위** — [밴드, 판, 가공 판, 토스트] 순.
        **`SWP_NOACTIVATE` 를 반드시 준다** — 없으면 밴드가 포커스를 뺏는다.

        **`target` 이 `HWND_TOP` 이면 뜻이 다르다.** 그 값은 「게임 위에 보통 창이 하나도 없다」
        일 때만 나오는데, `HWND_TOP` 은 전경 창을 가진 프로세스가 아니면 **True 를 돌려주고
        아무 일도 안 한다** — 그래서 게임을 클릭하는 순간 밴드가 게임 밑에 깔려
        **아예 안 보인다** (실측: 밴드 z=7, 게임 z=4, 1.2초가 지나도 그대로).

        그때만 `HWND_TOPMOST` 로 올렸다가 곧바로 `HWND_NOTOPMOST` 로 내린다. 깃발은 떨어지고
        자리는 **보통 창들 중 맨 앞**에 남는다. 이 분기는 「게임 위에 보통 창이 없다」가 성립할 때만
        타므로 **그 자리가 곧 「게임 바로 위」다** — 밴드는 게임 클라이언트 영역 안에만 있으니
        원래 게임에 가려져 있던 것 말고는 아무것도 새로 덮지 않는다.
        (실측: True/True · `ex 0x800a0 → 0x800a8 → 0x800a0` · 끝난 자리 밴드 z=4, 게임 z=5)"""
        for h in hwnds:
            if not h:
                continue
            try:
                if target == HWND_TOP:
                    self.u.SetWindowPos(wintypes.HWND(h), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_STACK)
                    self.u.SetWindowPos(wintypes.HWND(h), wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, SWP_STACK)
                else:
                    self.u.SetWindowPos(wintypes.HWND(h), wintypes.HWND(target), 0, 0, 0, 0, SWP_STACK)
            except Exception:
                pass
        if target == HWND_TOP:
            # 두 번째 호출이 실패하면 최상위로 남는다 — 다음 틱의 깃발 검사가 300ms 안에 뗀다.
            for h in hwnds:
                if h and self.is_topmost(h):
                    self.clear_topmost(h)

    def is_above(self, hwnd: int, game: int, limit: int = Z_WALK) -> bool:
        """밴드가 게임보다 위에 있는가. 밴드에서 `GW_HWNDNEXT`(아래쪽)로 내려가며 게임을 만나면 위다.
        **보이지 않는 창은 걸음 수로 세지 않는다** (게임 둘레에 숨은 창이 여럿 끼어 있다)."""
        h, steps = int(hwnd), 0
        try:
            while steps < limit:
                h = int(self.u.GetWindow(wintypes.HWND(h), GW_HWNDNEXT) or 0)
                if not h:
                    return False
                if h == int(game):
                    return True
                if self.u.IsWindowVisible(wintypes.HWND(h)):
                    steps += 1
        except Exception:
            return True      # 못 물어보면 건드리지 않는다
        return False

    def clear_topmost(self, hwnd: int) -> None:
        """「항상 맨 위」를 뗀다 (게임보다만 위에 있어야 한다)."""
        try:
            self.u.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, SWP_STACK)
        except Exception:
            pass
        ex = self.ex(hwnd)
        if ex & WS_EX_TOPMOST:
            self.set_ex(hwnd, ex & ~WS_EX_TOPMOST)

    def d3d_fullscreen(self) -> bool:
        """**문서화된 API** — 전용(exclusive) 전체화면 D3D 앱이 돌고 있으면 참.
        그 상태에서는 어떤 최상위 창도 게임 위에 못 뜬다(밴드가 안 보인다). 시스템 전역 판정이라
        「지금 전체화면인 앱이 있다」까지만 말해 준다 — 그 앱이 게임인지까지는 창 모양으로 따로 본다."""
        if self.sh is None:
            return False
        try:
            st = ctypes.c_int(0)
            if self.sh.SHQueryUserNotificationState(ctypes.byref(st)) != 0:
                return False
            return st.value == QUNS_RUNNING_D3D_FULL_SCREEN
        except Exception:
            return False

    # ── 둥근 모서리 ──
    def round_corners(self, hwnd: int, w: int, h: int, r: int, dash: int = 0) -> bool:
        """`SetWindowRgn` 으로 창을 둥글게 깎는다 (디자인의 `border-radius`).

        `-transparentcolor` 를 쓰지 않는 이유: 그 색 픽셀이 **전부 클릭 통과**가 되어 「관통인데 버튼만 눌림」
        예외가 깨진다. 영역(region)은 그 반대다 — 영역 밖은 아예 창이 아니므로 별도 처리가 필요 없다.
        성공하면 **OS 가 region 을 소유한다** — DeleteObject 하지 않는다 (실패했을 때만 우리가 치운다)."""
        # **`CreateRoundRectRgn` 의 마지막 두 인자는 반지름이 아니라 「타원의 가로·세로」다.**
        # `r` 을 그대로 주면 실제 모서리는 `r/2` 로 깎인다 (15px 이 7.5px 로 나온다).
        # 지름으로 바꿔 준다.
        rgn = self.g.CreateRoundRectRgn(0, 0, int(w) + 1, int(h) + 1, int(r) * 2, int(r) * 2)
        if not rgn:
            return False
        if dash:
            # 점선 테두리 (「전부 비어 있을 때」): 곧은 변을 따라 **홈을 파낸다.**
            # 그 자리는 창이 아니게 되므로 테두리(창 배경)가 끊겨 점선으로 보인다.
            self._notch(rgn, int(w), int(h), int(r), int(dash))
        if not self.u.SetWindowRgn(wintypes.HWND(hwnd), rgn, True):
            self.g.DeleteObject(rgn)
            return False
        return True

    def _notch(self, rgn, w: int, h: int, r: int, t: int) -> None:
        """점선용 홈. 위·아래 변에 `t` 두께로 `d` 간격의 네모를 빼낸다 (곡선 부분은 남긴다)."""
        try:
            d = max(2, t * 3)
            cut = self.g.CreateRectRgn(0, 0, 1, 1)
            x = r
            while x + d < w - r:
                for y0 in (0, h - t):
                    self.g.SetRectRgn(cut, x, y0, x + d, y0 + t)
                    self.g.CombineRgn(rgn, rgn, cut, RGN_DIFF)
                x += d * 2
            self.g.DeleteObject(cut)
        except Exception:
            pass

    # ── 게임 창 ──
    def client_rect(self, hwnd: int, min_side: int = 200):
        """그 창의 클라이언트 영역을 화면 좌표로 (x, y, w, h). 너무 작으면(숨은 창·스플래시) None."""
        r = wintypes.RECT()
        if not self.u.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return None
        w, h = r.right - r.left, r.bottom - r.top
        if w < min_side or h < min_side:
            return None
        p = _PT(r.left, r.top)
        if not self.u.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(p)):
            return None
        return (p.x, p.y, w, h)

    def proc_stem(self, pid: int) -> str:
        """그 pid 의 실행 파일 이름(확장자·경로 뺀 소문자). 못 열면 빈 문자열.
        PowerShell 로 명령줄을 뒤지지 않는다 — 백신이 그 행동을 오탐한다 (server.py 의 같은 주석)."""
        if not pid:
            return ""
        h = self.k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            n = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(n.value)
            if not self.k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return ""
            return buf.value.rsplit("\\", 1)[-1].rsplit(".", 1)[0].lower()
        finally:
            self.k.CloseHandle(h)

    def find_process_window(self, names) -> tuple:
        """그 프로세스들의 **보이는 최상위 창** 중 클라이언트 영역이 가장 큰 것 → (hwnd, (x, y, w, h)).
        못 찾으면 (0, None). 창 제목에 의존하지 않는다 — 지역화·업데이트로 바뀐다."""
        want = {str(n).lower() for n in names}
        best = [0, None]

        def cb(hwnd, _l):
            if not self.u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self.proc_stem(pid.value) not in want:
                return True
            r = self.client_rect(hwnd)
            if r and (best[1] is None or r[2] * r[3] > best[1][2] * best[1][3]):
                best[0], best[1] = int(hwnd), r
            return True

        self.u.EnumWindows(_ENUMPROC(cb), 0)
        return best[0], best[1]

    def hwnd_of(self, widget) -> int:
        """winfo_id() 는 자식 창이다. 실제 최상위 창은 GetAncestor(GA_ROOT)."""
        child = widget.winfo_id()
        return int(self.u.GetAncestor(wintypes.HWND(child), GA_ROOT) or child)

    def ex(self, hwnd: int) -> int:
        return self.u.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)

    def set_ex(self, hwnd: int, value: int) -> None:
        self.u.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE, value)

    def set_alpha(self, hwnd: int, a: float) -> None:
        self.u.SetLayeredWindowAttributes(wintypes.HWND(hwnd), 0, int(max(0.0, min(1.0, a)) * 255), LWA_ALPHA)

    def cursor(self) -> tuple:
        p = _PT()
        self.u.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    # 가상 화면 = 모니터 전부를 합친 사각형. `winfo_screenwidth()` 는 **주 모니터만** 준다 —
    # 게임이 왼쪽(음수 x)·오른쪽 모니터에 있으면 밴드를 놓는 순간 주 모니터로 끌려왔다.
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

    def virtual_screen(self):
        """(x, y, w, h) — 모든 모니터를 합친 화면. 못 읽으면 None (호출자가 주 모니터로 떨어진다)."""
        try:
            gm = self.u.GetSystemMetrics
            x, y = int(gm(self.SM_XVIRTUALSCREEN)), int(gm(self.SM_YVIRTUALSCREEN))
            w, h = int(gm(self.SM_CXVIRTUALSCREEN)), int(gm(self.SM_CYVIRTUALSCREEN))
            return (x, y, w, h) if (w > 0 and h > 0) else None
        except Exception:
            return None


class Overlay:
    """밴드 창 하나 + 펼침 판 + 오류 토스트. 전부 한 Tk 스레드 안에 있다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._th: threading.Thread | None = None
        self._stopping = threading.Event()
        self._settings: dict = {}
        self._hooks: dict = {}
        self._error = ""                # 창을 못 만든 이유 (설정 탭이 보여 준다)
        self._why = "off"               # 지금 안 보이는 이유 (HIDE_REASON 의 열쇠말) — 설정 탭이 보여 준다
        self._game = GAME_NONE          # 게임 창 상태 (ok / none / min)
        self._blur = ""                 # 배경 흐림에 실제로 쓴 방법 ("dwm"·"accent"·"" = 못 했다)
        self._fs = ""                   # 게임이 화면을 덮는 방식 ("exclusive"·"borderless"·"")
        self._test_until = 0.0          # 「테스트 표시」가 끝나는 시각
        self._width = 0
        self._running = False           # Tk 스레드가 살아 있다
        self._alive = False             # 창을 만들어 두었다 (꺼도 true — 숨겼을 뿐이다)
        self._tk_made = False           # **이 프로세스에서 Tk 를 만든 적이 있다.** 두 번 만들면 크래시난다
        self.root = None
        # 이 창이 놓인 모니터의 배율 (125% → 1.25). **여기서 정해 둬야 한다** —
        # `_pick_fonts` 가 창을 만드는 도중에 `px()` 를 부르므로 `_build` 에서 늦게 잡으면 터진다.
        self._sc = 1.0
        # 「위치 초기화」 요청 — 서버 스레드가 깃발만 세우고 Tk 스레드(`_tick_ui`)가 집는다.
        # 다른 스레드에서 `root.after` 를 부르지 않는다 (Tk 는 만든 스레드에서만 만진다).
        self._reset_pending = False
        self._blit_said: set = set()    # UpdateLayeredWindow 가 실패한 창 — 한 번만 알린다
        self._ui_said: set = set()      # 40ms 루프에서 난 예외 — 종류마다 한 번만 알린다

    # ── 바깥에서 부르는 것 ────────────────────────────────────────────────
    def configure(self, **hooks) -> None:
        """get_state() · get_alter() · collect_all() · save_pos(x,y) · say(msg) 를 받는다."""
        self._hooks.update({k: v for k, v in hooks.items() if callable(v)})

    def status(self) -> dict:
        """설정 탭이 읽는 상태 (`GET /api/settings` 의 overlay 블록)."""
        with self._lock:
            return {"running": self._alive, "width": self._width, "error": self._error,
                    "visible": self._alive and self._why in ("", "test"), "reason": self._why,
                    "reasonText": HIDE_REASON.get(self._why, self._why),
                    "game": self._game, "blur": self._blur, "fullscreen": self._fs}

    def _set_why(self, why: str) -> None:
        with self._lock:
            self._why = why

    def reset_pos(self) -> None:
        """「위치 초기화」 — 저장된 좌표를 기본값으로 되돌리고 「게임 창에 맞춤」을 켠다.

        **위치가 고정돼 있으면 아무것도 하지 않는다** — 고정은 「지금 자리를 지킨다」는 뜻인데
        초기화가 그걸 덮어쓰면 밴드가 구석으로 튄다.

        **단축키를 없앤 짝이다**: 관통을 켠 채 밴드를 화면 밖으로 끌어 놓으면 되돌릴 길이 사라진다.
        설정만 바꾸고, 자리는 40ms 따라가기가 다음 틱에 잡는다."""
        if self._cfg("overlay_lock", False):
            self._say("위치가 고정돼 있어 초기화하지 않았습니다 — 먼저 고정을 푸세요")
            return
        self._call("save_offset", 0, 0)      # 끌어 둔 오프셋을 지운다
        self._set_setting("overlay_follow_game", True)   # 가운데 위 자동 배치로
        with self._lock:
            self._settings["overlay_off_x"] = self._settings["overlay_off_y"] = 0
            self._settings["overlay_x"], self._settings["overlay_y"] = DEFAULT_POS
        # 창 조작은 Tk 스레드 몫이다 — 여기(서버 스레드)서는 깃발만 세운다. 40ms 안에 `_tick_ui` 가 집는다.
        self._reset_pending = True
        self._say("위치를 기본값으로 되돌렸습니다")

    def _apply_reset_pos(self) -> None:
        """Tk 스레드에서만 부른다 (`_tick_ui`)."""
        try:
            self._pos = DEFAULT_POS
            self._blit()      # 자리는 blit 이 정한다 (geometry 는 레이어드 그림을 무효로 만든다)
            self._place_children()
            self._sync_follow()
        except Exception as e:
            self._say(f"위치 초기화를 적용하지 못했습니다 — {type(e).__name__}: {e}")

    def test_show(self, seconds: float = 8.0) -> bool:
        """설정 탭의 「테스트 표시」 — 잠깐 무조건 보이게 한다 (게임이 없어도·큐가 멈춰 있어도·Shift+F1 로 숨겼어도).
        설정을 바꾸지 않는다. 창이 안 떠 있으면 아무 일도 하지 않는다."""
        if not self._alive:
            return False
        self._test_until = time.time() + max(1.0, float(seconds))
        return True

    def apply(self, settings: dict) -> None:
        """설정이 바뀌었다.

        **끄기가 창을 부수지 않는다.** Tcl/Tk 인터프리터를 파괴한 뒤 다른 스레드에서 다시 만들면
        **프로세스가 네이티브 크래시로 사라진다** — 파이썬 예외가 아니라서 traceback 도 없다
        (오버레이를 껐다 켜면 앱이 통째로 죽는다). 그래서 **Tk 는 프로세스당 한 번만** 만들고,
        끄기는 `_refresh` 가 창을 `withdraw()` 하는 것으로 끝낸다. 켜기는 다시 `deiconify()` 다."""
        with self._lock:
            self._settings = dict(settings or {})
        if self._settings.get("overlay_enabled") and not self._running:
            self.start(self._settings)   # 처음 켤 때만 Tk 를 만든다

    def start(self, settings: dict) -> None:
        with self._lock:
            if self._running:
                return
            if self._tk_made:
                # 한 번 만들었다 죽은 Tk 를 다시 만들면 프로세스가 사라진다 — 그 길을 아예 막는다
                self._error = "오버레이를 다시 만들 수 없습니다 — 앱을 다시 실행해 주세요"
                self._say(self._error)
                return
            self._settings = dict(settings or {})
            self._stopping.clear()
            self._error = ""
            self._running = True
        self._th = threading.Thread(target=self._tk_main, name="overlay-tk", daemon=True)
        self._th.start()

    def stop(self) -> None:
        """반드시 닫히는 길. **앱이 끝날 때만** 부른다 (`/api/quit`·창 닫힘).
        설정에서 끄는 것은 여기로 오지 않는다 — `apply()` 주석 참조."""
        self._stopping.set()   # 40ms 뒤 Tk 스레드가 스스로 destroy 한다 (_tick_ui)
        th = self._th
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=1.0)   # 보통 40ms 안에 끝난다. 종료 경로를 여기서 오래 붙잡지 않는다
        with self._lock:
            self._running = self._alive = False
            self._width = 0
            self._why, self._game = "off", GAME_NONE

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _say(self, msg: str) -> None:
        fn = self._hooks.get("say")
        if fn:
            try:
                fn(f"[overlay] {msg}")
            except Exception:
                pass

    def _cfg(self, key, default=None):
        with self._lock:
            return self._settings.get(key, default)

    def _tk_main(self) -> None:
        try:
            import tkinter as tk
        except Exception as e:      # tkinter 가 없는 파이썬 (또는 빌드에서 빠졌다)
            with self._lock:
                self._error = f"오버레이 창 기능을 불러오지 못했습니다 ({type(e).__name__})"
                self._running = False
            self._say(self._error)
            return
        with self._lock:
            self._tk_made = True   # 두 번째 생성을 영구히 막는다 (start 가 이 값을 본다)
        self._say(f"화면 배율 인식: {enable_dpi_awareness()}")
        try:
            self._build(tk)
            with self._lock:
                self._alive = True
                self._error = ""
            self.root.mainloop()
        except Exception as e:
            import traceback
            traceback.print_exc()
            with self._lock:
                self._error = f"{type(e).__name__}: {e}"
            self._say(f"창을 유지하지 못했습니다 — {self._error}")
        finally:
            try:
                if self.root is not None:
                    self.root.destroy()
            except Exception:
                pass
            self.root = None
            with self._lock:
                self._running = self._alive = False
                self._width = 0
                self._why, self._game = "off", GAME_NONE   # 설정 탭이 읽는 값 — `False` 가 아니라 열쇠말

    # ---- 창 만들기 ----
    def _build(self, tk) -> None:
        self.tk = tk
        self.u = _U32()
        C = COLORS
        root = tk.Tk()
        self.root = root
        root.withdraw()                      # 자리를 잡기 전에는 보이지 않게
        root.title("모비웍스 오버레이")
        root.overrideredirect(True)          # 프레임 없음
        # **`-alpha` 를 절대 걸지 않는다.** 그건 `SetLayeredWindowAttributes` 이고, 한 번이라도 걸면
        # 창이 그 방식에 고정되어 `UpdateLayeredWindow` 가 **조용히 무시된다** (True 를 돌려주면서
        # 한 픽셀도 안 그린다). 확진: `GetLayeredWindowAttributes` 가 참이면 그 창은 못 쓴다 —
        # 우리가 여기 걸려 「이 기계에서는 ULW 가 안 된다」고 잘못 결론냈다.
        root.configure(bg=C["bg"])
        self._pick_fonts()      # 위젯을 만들기 전에 — 이 기계에 있는 글꼴로 표를 채운다
        # **밴드에는 자식 위젯이 없다.** 그림 한 장을 창에 통째로 얹는다 (bandpaint).
        # Tk 캔버스에는 안티에일리어싱이 없어 모서리가 계단으로 남는다.
        # GDI+ 로 그려 픽셀별 알파로 얹으면 모서리도 부드럽고 배경만 반투명이 된다.
        self.surf = paint32.Surface(120, 30) if paint32 is not None else None
        root.bind("<Button-1>", self._on_band_press)
        root.bind("<B1-Motion>", self._drag_move)
        root.bind("<ButtonRelease-1>", self._drag_end)
        # **레이어드 창의 그림은 창이 다시 노출되거나 크기가 바뀌면 무효가 된다.**
        # 다시 얹지 않으면 밴드가 반쯤 지워진 채로 남는다.
        root.bind("<Expose>", lambda _e: self._blit())
        root.bind("<Configure>", lambda _e: self._blit())

        # 펼침 판 둘 — 밴드 끝 `▾` = 대기 목록 + 가공, 가공 뒤 `▾` = 가공 시설 칸.
        # **판·토스트에도 `-alpha` 를 걸지 않는다.** 한 번이라도 걸면 창이
        # `SetLayeredWindowAttributes` 방식에 고정되어 `UpdateLayeredWindow` 가 무시된다
        # (판이 화면 왼쪽 위에 아무것도 없이 박혀 보인다).
        self.panel = tk.Toplevel(root)
        self.panel.withdraw()
        self.panel.overrideredirect(True)
        self.panel.configure(bg=C["panelline"])
        # **판도 그림 한 장이다** (밴드·토스트와 같은 방식 — 위젯으로는 모서리가 계단으로 남는다)
        self.panel_surf = paint32.Surface(10, 10) if paint32 is not None else None
        self.panel.bind("<Button-1>", lambda e: self._on_panel_press(e, "panel"))

        self.apanel = tk.Toplevel(root)
        self.apanel.withdraw()
        self.apanel.overrideredirect(True)
        self.apanel.configure(bg=C["warnline2"])   # 가공 판만 주황 테두리
        self.apanel_surf = paint32.Surface(10, 10) if paint32 is not None else None
        self.apanel.bind("<Button-1>", lambda e: self._on_panel_press(e, "apanel"))

        self.toast = tk.Toplevel(root)       # 오류 토스트
        self.toast.withdraw()
        self.toast.overrideredirect(True)
        self.toast.configure(bg=C["badline"])
        # **토스트도 그림 한 장이다** (밴드와 같은 방식). 위젯으로는 모서리가 계단으로 남는다.
        self.toast_surf = paint32.Surface(10, 10) if paint32 is not None else None
        self.toast_text = ("", "", "", False)     # (이름, 결과, 설명, 오류인가)
        self.toast.bind("<Button-1>", self._on_toast_click)

        root.update_idletasks()
        # hwnd 와 스타일은 창을 **처음 보일 때** 잡는다 (_show). 아직 한 번도 매핑되지 않은 창의
        # GetAncestor 는 믿을 게 못 되고, 여기서 미리 deiconify 하면 자리를 잡기 전 위치에 한 번 번쩍인다.
        self.hwnd = 0
        self.hwnd_panel = 0
        self.hwnd_apanel = 0
        self.hwnd_toast = 0

        # 상태
        self._cells: list = []          # 지금 그려져 있는 칸의 구성 [(key, kind)] — 바뀔 때만 위젯을 다시 짓는다
        self._hit: list = []            # 밴드의 클릭 통과 예외 위젯 (⋮⋮ · ▾ · 일괄 수령)
        self._ahit: list = []           # 가공기 판의 예외 위젯 (컨트롤 줄 · 수령 알약 · ▴)
        self._phit: list = []           # 밴드 펼침 판의 예외 위젯 (`▴`)
        self._fac_cv: dict = {}         # 시설 알약 캔버스 (값만 바뀌면 제자리에서 다시 그린다)
        self._through = None            # 지금 창에 걸린 관통 여부 (None = 아직 안 걸었다)
        self._alpha = -1.0
        self._border = ""
        self._look_dashed = False
        self._lwa_said: set = set()
        self._cursors: dict = {}
        self._expanded = str(self._cfg("overlay_expand", "off")) == "on"   # 밴드 끝 ▾ (대기 목록 + 가공)
        self._aexpanded = False                                            # 가공 뒤 ▾ (시설 칸)
        self._drag = None
        self._flash_until = 0.0
        self._flash_kind = ""
        self._prev = {"done": None, "err": None, "loop": None}
        self._prev_notice: str | None = None   # 마지막으로 본 알림 열쇠 (None = 아직 첫 상태 전 — 처음 본 것은 안 띄운다)
        self._toast_on = False        # 실패 토스트가 떠 있나 (× 를 눌러야 꺼진다)
        self._thit: list = []           # 토스트의 예외 사각형 — `×` 하나뿐 (× 로 닫는다)
        self._toast_pos = (0, 0)        # 토스트 창의 왼쪽 위 (적중 판정의 기준)
        self._auto_tried: set = set()
        self._auto_stamp = 0.0
        self._last_state = None
        self._last_alter = None
        self._data_at = 0.0
        self._blink = True
        self._blink_at = 0.0
        self._rgn: dict = {}            # 창별로 마지막에 깎은 (w, h, r) — 폭이 바뀔 때만 다시 만든다
        self._rgn_warned = False
        self._restack = True            # Z 순서를 다시 올려야 한다 (창이 막 보였을 때)
        self._z_at = 0.0                # 마지막으로 Z 순서를 확인한 시각
        self._game_hwnd = 0             # 찾아 둔 게임 창 (사라지면 다시 훑는다)
        self._game_scan = 0.0
        self._wings = None              # 예상 소모 (설정으로 켰을 때만 센다)
        self._wings_at = 0.0
        self._stats = None              # 장부 `stats(7)` — 오늘 한 줄과 7일 표 (30초에 한 번 읽는다)
        self._day_at = 0.0
        self._day_open = False          # 펼침 판에 7일 표를 붙였나 (밴드의 오늘 한 줄을 눌렀을 때)

        x, y = self._start_pos()
        self._pos = (x, y)      # 지금 있어야 할 자리. 크기를 바꿀 때 이 값을 같이 준다 (아래 주석 참조)
        # 창을 처음 보이기 전 한 번만 — 이후 크기·자리는 전부 `blit` 이 정한다
        root.geometry(f"{max(120, self._width or 200)}x{self.px(BAND_H)}+{x}+{y}")
        # 여기서는 아직 보이지 않는다 — 처음 그린 뒤 _refresh 가 「지금 보여야 하는가」를 보고 띄운다
        # (「큐가 멈추면 밴드 숨김」이 켜져 있으면 대기 상태에서는 창이 아예 나타나지 않는다).
        self._tick_data()     # 1초 데이터
        self._tick_ui()       # 40ms 커서·관통·깜박임
        self._say(f"준비됨 (위치 {x},{y}) — 표시 조건이 맞으면 밴드가 나타납니다")

    def _show(self, win, attr: str, through: bool) -> int:
        """창을 처음 보일 때 hwnd 를 잡고 스타일을 건다. Z 순서는 `_sync_zorder` 가 따로 본다."""
        first = not win.winfo_viewable()
        if first:
            win.deiconify()
            win.update_idletasks()
        h = getattr(self, attr)
        if not h:
            h = self.u.hwnd_of(win)
            ex = self.u.ex(h) | WS_EX_LAYERED | WS_EX_TOOLWINDOW   # 작업표시줄·Alt+Tab 에서 숨김
            if through:
                ex |= WS_EX_TRANSPARENT
            self.u.set_ex(h, ex)
            setattr(self, attr, h)
            self.u.clear_topmost(h)   # 「항상 맨 위」를 뗀다 — 게임보다만 위에 있어야 한다
            hr = self.u.dark_mode(h)   # 흰 테두리 방지. 흐림과 무관하므로 언제나 건다
            # **흐림은 「반투명 글래스」를 고른 때만** 건다. 끌 때는 되돌리는 호출조차 하지 않는다
            used = self.u.blur(h) if self._backdrop_mode() == "glass" else ""
            if attr == "hwnd":
                with self._lock:
                    self._blur = used
            dark = "안 걸림(dwmapi 없음)" if hr is None else ("걸림" if hr == 0 else f"실패 hr=0x{hr & 0xFFFFFFFF:x}")
            self._say(f"{attr} hwnd={h} exstyle=0x{self.u.ex(h):x} 흐림={used or '없음'} 다크모드={dark}")

        if first:
            self._restack = True      # 막 보인 창이 게임 아래로 들어가지 않게 다음 틱에 다시 끼운다
        return h

    def _screen_box(self) -> tuple:
        """(x, y, w, h) — 밴드를 가둘 화면. **모니터 전부**(가상 화면)다.
        `winfo_screenwidth()` 는 주 모니터만 알아서, 게임이 다른 모니터에 있으면 놓는 순간 튀었다."""
        vs = self.u.virtual_screen() if getattr(self, "u", None) is not None else None
        if vs:
            return vs
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _start_pos(self) -> tuple:
        ox, oy, sw, sh = self._screen_box()
        try:
            x = int(self._cfg("overlay_x", DEFAULT_POS[0])); y = int(self._cfg("overlay_y", DEFAULT_POS[1]))
        except (TypeError, ValueError):
            x, y = DEFAULT_POS
        return clamp_pos(x, y, max(120, self._width or 200), self.px(BAND_H), sw, sh, ox, oy)

    # ---- 둥근 모서리 ----
    def _round(self, hwnd: int, attr: str, w: int, h: int, r: int, dashed: bool = False) -> None:
        """폭이 바뀔 때마다 영역을 **다시 만들어야 한다** — 밴드 폭은 표시 항목에 따라 변한다.
        실패하면 그냥 네모로 둔다 (예외를 내보내지 않는다)."""
        if not hwnd or w <= 0 or h <= 0:
            return
        if self._rgn.get(attr) == (w, h, r, dashed):
            return
        self._rgn[attr] = (w, h, r, dashed)
        try:
            ok = self.u.round_corners(hwnd, w, h, r, self.px(BAND_BORDER) if dashed else 0)
        except Exception as e:
            ok = False
            if not self._rgn_warned:
                self._rgn_warned = True
                self._say(f"둥근 모서리를 만들지 못했습니다 ({type(e).__name__}: {e}) — 네모로 둡니다")
        if not ok and not self._rgn_warned:
            self._rgn_warned = True
            self._say("둥근 모서리를 만들지 못했습니다 (SetWindowRgn 실패) — 네모로 둡니다")

    # ---- 배경 흐림 ----
    def _backdrop_mode(self) -> str:
        """「배경 투명도 스타일」. **기본은 `solid`(불투명)** — 흐림은 창을 통째로 안 그리게 만든다."""
        return "glass" if str(self._cfg("overlay_backdrop", "solid") or "solid") == "glass" else "solid"

    def _apply_backdrop(self, settings) -> None:
        """설정이 바뀌었을 때만 건다 (창이 새로 생길 때의 첫 적용은 `_show` 가 한다).
        **`solid` 로 갈 때는 아무것도 부르지 않는다** — 되돌리는 호출조차 위험하다고 보고 빼 두었다.
        이미 걸린 흐림은 다음 실행에서 사라진다(창을 새로 만들 때 안 걸므로)."""
        want = "glass" if str(settings.get("overlay_backdrop") or "solid") == "glass" else "solid"
        if want == getattr(self, "_backdrop_at", None):
            return
        first = not hasattr(self, "_backdrop_at")
        self._backdrop_at = want
        if first or want != "glass":
            return
        used = ""
        for h in (self.hwnd, self.hwnd_panel, self.hwnd_apanel, self.hwnd_toast):
            if h:
                try:
                    used = self.u.blur(h) or used
                except Exception:
                    pass
        with self._lock:
            self._blur = used
        self._say(f"배경 흐림: {used or '걸지 못했습니다'}")

    # ---- 게임 창에 맞추기 ----
    def _game_client(self):
        """게임 클라이언트 영역 (화면 좌표) 또는 None. 찾은 창은 기억해 두고, 사라졌을 때만 다시 훑는다.
        상태(ok/none/min)를 `self._game` 에 남긴다 — 설정 탭이 「게임을 찾지 못해 숨김」·
        「게임이 최소화되어 숨김」을 그대로 보여 준다."""
        st, rect = self._find_game()
        if st != self._game:
            with self._lock:
                self._game = st
            self._say({GAME_OK: "게임 창을 찾았습니다",
                       GAME_MIN: "게임이 최소화됐습니다 — 밴드를 내립니다"}.get(st, "게임 창이 없습니다 — 밴드를 내립니다"))
        return rect

    def _find_game(self) -> tuple:
        """(상태, 클라이언트 사각형|None)."""
        h = self._game_hwnd
        if h:
            try:
                u = self.u.u
                hw = wintypes.HWND(h)
                if u.IsWindow(hw) and u.IsWindowVisible(hw):
                    # 최소화된 창은 IsWindowVisible 이 여전히 참이고 ClientToScreen 이 (-32000,-32000) 을 준다 —
                    # 그대로 쓰면 밴드가 화면 밖으로 갔다가 clamp 에 걸려 구석으로 튄다.
                    if u.IsIconic(hw):
                        return GAME_MIN, None      # 창은 그대로 두고(다시 안 훑는다) 밴드만 내린다
                    r = self.u.client_rect(h)
                    if r:
                        self._sync_fullscreen(h, r)
                        return GAME_OK, r
            except Exception:
                pass
            self._game_hwnd = 0
            self._game_scan = 0.0   # 창이 사라졌으면 바로 다시 훑는다 (3초를 기다리지 않는다)
        now = time.time()
        if now - self._game_scan < RESCAN_SEC:   # EnumWindows 를 1초마다 돌릴 이유는 없다
            return GAME_NONE, None
        self._game_scan = now
        try:
            hwnd, rect = self.u.find_process_window(GAME_PROCESS)
        except Exception as e:
            self._say(f"게임 창을 찾는 중 오류 — {type(e).__name__}: {e}")
            return GAME_NONE, None
        if hwnd and rect:
            self._game_hwnd = hwnd
            self._sync_fullscreen(hwnd, rect)
            return GAME_OK, rect
        return GAME_NONE, None

    def _sync_fullscreen(self, hwnd: int, rect) -> None:
        """게임이 **전용 전체화면**이면 어떤 최상위 창도 위에 못 뜬다 — 밴드가 그냥 안 보인다.
        그 사실을 설정 탭이 알려 준다 (밴드 위에 변명을 얹지 않는다). 확인은 1초에 한 번이면 충분하다."""
        now = time.time()
        if now - getattr(self, "_fs_at", 0.0) < 1.0:
            return
        self._fs_at = now
        try:
            kind = fullscreen_kind(self.u.style(hwnd), rect, self.u.monitor_rect(hwnd))
            if kind == "borderless" and self.u.d3d_fullscreen():
                kind = "exclusive"   # 창 모양은 같다 — 문서화된 API 로만 갈린다
        except Exception:
            kind = ""
        if kind != self._fs:
            with self._lock:
                self._fs = kind
            if kind == "exclusive":
                self._say("게임이 전체화면 전용입니다 — 밴드가 게임 위에 보이지 않습니다 (테두리 없는 창 모드로 바꾸세요)")

    def _sync_follow(self) -> None:
        """「게임 창에 맞춤」이 켜져 있으면 게임 클라이언트 영역을 따라간다.

        **40ms UI 폴링에서 돈다** — 1초 주기에 얹으면 창을 끌 때 눈에 띄게 뒤처진다. 확인 비용은
        `GetClientRect` + `ClientToScreen` 두 번이라 싸고, **값이 바뀌었을 때만** `geometry` 를 부른다.
        게임을 못 찾거나 최소화돼 있으면 **조용히 지금 자리에 그대로 둔다** (밴드를 숨기지 않는다)."""
        if self._drag:
            return
        rect = self._game_client()   # 숨어 있을 때도 맞춰 둔다 — 나타나는 순간 이미 제자리여야 한다
        if not rect:
            return                   # 게임이 없으면 기준이 없다 — 숨긴 채 그대로 둔다
        w = self._width or 200
        # **폭은 상태에 따라 변한다** (담긴 수·가공·오류 칸이 붙었다 떨어졌다 한다). 그때마다
        # 밴드가 좌우로 널뛰어 보인다. 규칙:
        #   · 위치 고정 **꺼짐** → 「위치 초기화」를 누른 것과 같게 **가운데로 다시** 잡는다
        #   · 위치 고정 **켜짐** → 초기화하지 않고 **좌측 좌표를 그대로 둔 채 오른쪽으로만** 늘어난다
        # **끌어 둔 자리를 덮어쓰지 않는다.** 폭이 바뀔 때마다 자동 배치를 강제로 켜면
        # 창을 옮기고 손을 떼는 순간 제자리로 돌아간다. 그래서 이렇게 갈린다:
        #   · 자동 배치(follow_game) 중 → 폭이 바뀌면 **가운데로 다시** 잡는다
        #   · 끌어 둔 자리 → **좌측 좌표를 그대로 두고 오른쪽으로만** 늘어난다
        # 끌면 자동 배치가 꺼지므로(_drag_end) 두 규칙이 서로 싸우지 않는다.
        if self._cfg("overlay_lock", False):
            # 좌측 고정 — 끌어 둔 자리(오프셋)를 그대로 쓴다. 폭이 늘어도 왼쪽은 안 움직인다
            x, y = offset_band_pos(rect, self._offset(), w, self.px(BAND_H))
        elif self._cfg("overlay_follow_game", True):
            x, y = game_band_pos(rect, w, self.px(BAND_H))          # 자동 배치 (가운데 위)
        else:
            x, y = offset_band_pos(rect, self._offset(), w, self.px(BAND_H))   # 끌어 둔 자리
        if (x, y) != self._pos:
            self._pos = (x, y)
            self._blit()      # 자리는 blit 이 정한다 (geometry 는 UpdateLayeredWindow 와 싸운다)
            self._place_children()

    def _offset(self) -> tuple:
        try:
            return int(self._cfg("overlay_off_x", 0)), int(self._cfg("overlay_off_y", 0))
        except (TypeError, ValueError):
            return 0, 0

    def _sync_zorder(self) -> None:
        """**게임보다만 위.** 게임 위의 첫 「보이는」 창 아래에 끼운다 (`_U32` 의 Z 순서 주석에 실측값).

        끼워 넣는 일은 순서를 흔드므로 **40ms 마다 부르면 안 된다.** `Z_CHECK_SEC`(300ms)마다
        **싸게 확인만** 하고 어긋났을 때만 다시 넣는다."""
        game = self._game_hwnd
        if not game or not self.hwnd:
            return
        now = time.time()
        if not self._restack and now - self._z_at < Z_CHECK_SEC:
            return
        self._z_at = now
        ours = (self.hwnd, self.hwnd_panel, self.hwnd_apanel, self.hwnd_toast)
        # 한 번 최상위가 되면 `is_above` 가 늘 참이라 **영영 안 고쳐진다.** 깃발부터 본다.
        stuck = [h for h in ours if h and self.u.is_topmost(h)]
        if stuck:
            for h in stuck:
                self.u.clear_topmost(h)
            self._say(f"「항상 맨 위」가 걸려 있어 뗐습니다 (창 {len(stuck)}개) — 게임보다만 위에 둡니다")
            self._restack = True
        elif not self._restack and self.u.is_above(self.hwnd, game):
            return
        self._restack = False
        # 밴드 → 펼침 판 → 가공 판 → 토스트 순 (나중에 넣은 것이 더 위). 보이지 않는 창은 건너뛴다
        stack = [self.hwnd]
        for win, h in ((self.panel, self.hwnd_panel), (self.apanel, self.hwnd_apanel), (self.toast, self.hwnd_toast)):
            try:
                if h and win.winfo_viewable():
                    stack.append(h)
            except Exception:
                pass
        self.u.insert_below(self.u.insert_target(game, ours), stack)

    def band_anchor(self) -> tuple:
        """상세창을 붙일 기준 — **밴드 창을 직접 재서** 왼쪽 아래 좌표를 돌려준다.

        `_pos` 는 「있어야 할 자리」이고 실제로 그려진 자리와 한 틱 어긋날 수 있다.
        그 값을 쓰면 상세창이 밴드와 왼쪽이 안 맞아 보인다."""
        # **끄는 중에는 `_pos` 를 쓴다** — 창이 한 틱 뒤처져 따라오므로 재면 판이 덜덜거린다.
        if not self._drag:
            try:
                if self.hwnd:
                    r = self.u.window_rect(self.hwnd)
                    if r:
                        return r[0], r[1] + r[3]
            except Exception:
                pass
        x, y = self._pos
        return x, y + self.px(BAND_H)

    def _place_children(self) -> None:
        """펼침 판·오류 토스트는 밴드에 붙어 있다 — 밴드가 움직이면 같이 움직인다."""
        x, y = self._pos
        try:
            gap = self.px(BAND_H + PANEL_GAP)      # 셋 다 top 52 (밴드 아래 8)
            if self.panel.winfo_viewable():
                self.panel.geometry(f"+{x}+{y + gap}")
            if self.apanel.winfo_viewable():
                self.apanel.geometry(f"+{x}+{y + gap}")
            if self.toast.winfo_viewable():
                self.toast.geometry(f"+{x}+{y + gap}")
        except Exception:
            pass

    # ---- 1초 주기: 데이터 ----
    def _tick_data(self) -> None:
        if self._stopping.is_set() or self.root is None:
            return
        try:
            self._refresh()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._say(f"갱신 중 오류 — {type(e).__name__}: {e}")
        try:
            self.root.after(1000, self._tick_data)
        except Exception:
            pass

    def _hide_all(self) -> None:
        for w in (self.root, self.panel, self.apanel, self.toast):
            try:
                if w.winfo_viewable():
                    w.withdraw()
            except Exception:
                pass

    def _refresh(self) -> None:
        now = time.time()
        # 꺼져 있으면 창을 **부수지 않고 숨기기만** 한다 (apply 주석: Tk 는 프로세스당 한 번).
        # 갈고리(get_state·get_alter)도 부르지 않는다 — 꺼 둔 동안 헛일을 하지 않게.
        if not self._settings.get("overlay_enabled"):
            self._set_why("off")
            self._hide_all()
            return
        state = self._call("get_state") or {}
        got = self._call("get_alter") or ([], 0.0, {}, {})
        rows, fetched = got[0], got[1]
        seen = got[2] if len(got) > 2 else {}     # 본 적 있는 시설 (빈 시설 알약용)
        dur = got[3] if len(got) > 3 else {}      # 배워 둔 회당 시간 (칸 막대가 차오르는 데 쓴다)
        alter = alter_view(rows, fetched, now, seen, dur)
        self._last_state, self._last_alter, self._data_at = state, alter, now
        self._detect_events(state, alter, now)
        self._detect_notice(state, now)
        if now >= self._flash_until:
            self._flash_kind = ""
        status = band_status(state, alter, self._flash_kind)
        settings = dict(self._settings)

        # 게임이 없거나 최소화되면 밴드를 내린다. 다시 뜨면 나타난다.
        # 왜 안 보이는지는 설정 탭이 그대로 보여 준다 — 「켰는데 아무것도 안 나타난다」가 되지 않게.
        self._game_client()
        vis, why = visibility(state, settings, self._game, bool(self._call("app_gone")))
        if now < self._test_until and settings.get("overlay_enabled"):
            vis, why = True, "test"     # 「테스트 표시」 — 잠깐 무조건 보인다 (설정은 바꾸지 않는다)
        # 「닫는 중」으로 내려가는 순간을 한 번 남긴다 — 이 판정이 실제로 언제 걸렸는지
        # 로그로 확인할 수 있어야 한다. 모비폴리오는 같은 판정이 **한 번도 안 걸리는데**
        # 로그가 없어 한참 못 찾았다 (허용치를 남의 값으로 베낀 탓).
        if why == "closing" and self._why != "closing":
            self._say("앱 창이 닫혔습니다 — 밴드를 내립니다 (프로세스는 창 확인 뒤에 끝납니다)")
        self._set_why(why)
        if not vis:
            if self.root.winfo_viewable():
                self.root.withdraw()
            self.panel.withdraw()   # 펼침 판·토스트도 같이 내려간다
            self.apanel.withdraw()
            self.toast.withdraw()
            return
        # 폭이 정해진 뒤에 보인다 — 먼저 띄우면 좁은 밴드가 한 번 번쩍이고 늘어난다
        self._draw(band_cells(state, alter, settings, status, now, self._sync_wings(settings, now),
                              self._sync_day(now)))
        self._sync_follow()   # 폭이 정해진 뒤에 자리를 잡는다 (가운데 정렬이 폭에 달려 있다).
                              # 이후로는 40ms 폴링(_tick_ui)이 계속 따라간다
        self._show(self.root, "hwnd", bool(settings.get("overlay_click_through", True)))
        # **둥근 모서리는 그림이 낸다** — `SetWindowRgn` 은 안티에일리어싱이 없어 계단으로 잘린다.
        # 배경 흐림도 걸지 않는다 — ULW 창에 걸면 픽셀이 하나도 안 그려진다.
        self._apply_look(status, now < self._flash_until, settings)
        self._blit()          # hwnd 는 방금 잡혔다 — 첫 그림을 여기서 얹는다
        self._sync_expand(state, alter, settings)
        self._sync_apanel(alter, settings)
        self._sync_toast(now)
        self._auto_collect(alter, settings, now)

    def _sync_wings(self, settings, now: float):
        """예상 소모(정령의 날개). 실행 줄의 「예상 소모」와 같은 값이고, 큐 미리보기를 세는 일이라
        1초마다 돌릴 만큼 싸지 않다 — 5초에 한 번만 센다. 설정으로 끄면 아예 세지 않는다."""
        if not settings.get("overlay_show_wings", False):
            self._wings = None
            return None
        if now - self._wings_at >= 5.0 or self._wings is None:
            self._wings_at = now
            v = self._call("get_wings")
            self._wings = int(v) if isinstance(v, (int, float)) else None
        return self._wings

    def _sync_day(self, now: float):
        """일간 리포트 한 줄 (`ledger.day_line`). 장부 파일을 읽는 일이라 30초에 한 번만 읽는다.
        갈고리 `get_stats() -> ledger.stats(7)` 가 없으면 None — 칸이 안 생긴다."""
        if now - getattr(self, "_day_at", 0.0) >= DAY_EVERY:
            self._day_at = now
            st = self._call("get_stats")
            self._stats = st if isinstance(st, dict) else None
        st = getattr(self, "_stats", None)
        return ledger.day_line(st) if st is not None else None

    def _call(self, name, *a):
        fn = self._hooks.get(name)
        if not fn:
            return None
        try:
            return fn(*a)
        except Exception as e:
            self._say(f"{name} 실패 — {type(e).__name__}: {e}")
            return None

    def _detect_events(self, state, alter, now) -> None:
        """완료·오류·회차 전환을 알아내 1.5초 강조와 토스트를 켠다."""
        prog = state.get("progress") if isinstance(state.get("progress"), dict) else {}
        done = int(prog.get("done") or 0)
        err = state.get("lastError") if isinstance(state.get("lastError"), dict) else None
        # lastError 는 {id, error, code, short, message} 다 (workqueue). 같은 항목이 같은 이유로 또 실패하면
        # 새 사건으로 보지 않는다 — 1.5초 강조와 토스트가 계속 되풀이되지 않게.
        errkey = f"{(err or {}).get('id')}|{(err or {}).get('code')}|{(err or {}).get('message')}" if err else None
        rnd = state.get("currentGroupRound") if isinstance(state.get("currentGroupRound"), dict) else None
        loop = int(rnd.get("loop") or 0) if rnd else None
        kind = ""
        if self._prev["err"] is not None and errkey and errkey != self._prev["err"]:
            kind = "error"
        elif self._prev["done"] is not None and done > self._prev["done"]:
            kind = "done"
        elif self._prev["loop"] is not None and loop is not None and loop > (self._prev["loop"] or 0):
            kind = "loop"
        self._prev = {"done": done, "err": errkey or "", "loop": loop}
        if not kind:
            return
        self._flash_kind = kind
        self._flash_until = now + FLASH_SEC
        if toast_kind(self._settings, kind):
            self._show_toast(kind, state, now)

    def _detect_notice(self, state, now) -> None:
        """알림 카드·큐 종료 — `notices` 의 마지막이 바뀌면 토스트. 밴드가 처음 본 것(켜기 전 것)은 안 띄운다."""
        key = notice_key(state)
        if self._prev_notice is None:
            self._prev_notice = key
            return
        if not key or key == self._prev_notice:
            return
        self._prev_notice = key
        if toast_kind(self._settings, "notify"):
            self._show_toast("notify", state, now)

    # ---- 밴드 그리기 ----
    # 크기는 **음수 = 픽셀**이다 (양수는 포인트라 DPI 에 따라 달라진다). 디자인이 px 기준이므로 그대로 맞춘다:
    # 본문 12px · ⋮⋮/▾/일괄수령/오류 11px · 종류 배지와 대기·완료·실패 10px.
    # 글꼴 이름은 창을 만들 때 이 기계에 있는 것으로 정한다 (_pick_fonts) — 없는 이름을 주면 한글이 깨진다.
    SPEC = {"ui": (12, "", "ui"), "bold": (12, "bold", "ui"), "badge": (10, "bold", "ui"),
            "xs": (11, "", "ui"), "boldxs": (11, "bold", "ui"),
            "mono": (12, "", "mono"), "monob": (12, "bold", "mono"), "cols": (10, "", "mono"),
            "p": (11, "", "ui"), "pbold": (11, "bold", "ui"), "plabel": (10, "", "ui"),
            # 알약 글자 — 디자인은 11px 이지만 맑은 고딕은 같은 px 에서 더 크게 잡혀
            # 초록 알약 안이 꽉 차 보인다. 한 단계 줄이고 여백을 늘린다.
            "pill": (10, "bold", "ui"),
            "pmono": (11, "", "mono"), "pmonob": (11, "bold", "mono")}

    def _pick_fonts(self) -> None:
        """이 기계에 실제로 있는 글꼴로 표를 만든다. 어느 것이 잡혔는지 로그에 남긴다 —
        다음에 또 한글이 깨지면 이 한 줄로 바로 안다."""
        try:
            from tkinter import font as tkfont
            self._tkfont = tkfont
            fams = tkfont.families(self.root)
        except Exception:
            self._tkfont = None
            fams = ()
        ui = pick_family(UI_FAMILIES, fams)
        mono = pick_family(MONO_FAMILIES, fams)
        self._fam = {"ui": ui, "mono": mono or ui}    # 고정폭이 없으면 한글 글꼴로 — 깨지느니 폭이 흔들리는 게 낫다
        self.FONTS = {}
        for key, (px, weight, kind) in self.SPEC.items():
            fam = self._fam[kind]
            n = -self.px(px)      # 음수 = 픽셀. **이 모니터의 실제 픽셀**로 키운다 (125% → 12px 이 15px)
            self.FONTS[key] = ((fam, n, weight) if weight else (fam, n)) if fam else \
                              (("TkDefaultFont", n, weight) if weight else ("TkDefaultFont", n))
        self._say(f"글꼴: 본문={ui or 'TkDefaultFont(대체 없음)'} 숫자={mono or ui or 'TkDefaultFont'}")

    def px(self, v) -> int:
        """디자인의 px 를 **이 모니터의 실제 픽셀**로. 125% 면 12px 이 15px 이 된다."""
        return max(1, int(round(float(v) * self._sc)))

    def _sync_dpi(self) -> bool:
        """밴드가 놓인 모니터의 배율이 바뀌었으면 글꼴을 다시 잡는다 (모니터 간 이동·배율 변경).
        돌려주는 것: 바뀌었는가."""
        if not self.hwnd:
            return False
        sc = self.u.dpi_scale(self.hwnd)
        if abs(sc - self._sc) < 0.01:
            return False
        self._sc = sc
        self._pick_fonts()
        self._cells = []        # 치수가 전부 바뀌었다 — 위젯을 다시 짓는다
        self._say(f"화면 배율 {round(sc * 100)}% 로 다시 그립니다")
        return True

    def _font(self, key: str, text=None):
        """그 칸의 글꼴. **고정폭 칸인데 한글이 섞였으면 한글 글꼴로 바꾼다** — Consolas 에는 한글이 없다."""
        spec = self.SPEC.get(key)
        if spec and spec[2] == "mono" and needs_ui_font(text):
            return self.FONTS[{"mono": "ui", "monob": "bold", "cols": "badge",
                               "pmono": "p", "pmonob": "pbold"}.get(key, "ui")]
        return self.FONTS.get(key, self.FONTS["ui"])

    # 캔버스로 그리는 칸 — 글자·색이 바뀌면 다시 그려야 하므로 **구성 열쇠에 값까지 넣는다**
    CANVAS_KINDS = ("badge", "button", "dot")

    def _draw(self, cells: list) -> None:
        """밴드를 **그림 한 장으로 그려 창에 얹는다** (bandpaint).

        위젯도 캔버스도 아닌 이유는 bandpaint 머리말에 있다 — 둘 다 안티에일리어싱이 없어
        모서리가 계단으로 남고, 창 전체 알파로는 글자까지 비친다."""
        if bandpaint is None or self.surf is None:
            return
        for c in cells:      # ▾/▴ 는 펼침 상태를 따른다
            if c.get("kind") == "expand":
                c["text"] = "▴" if self._aexpanded else "▾"
            elif c.get("kind") == "expand2":
                c["text"] = "▴" if self._expanded else "▾"
        # 항목명은 92px 에서 말줄임 · 밴드가 안전 구역(x240~1002 환산)을 넘치면
        # **항목명부터** 줄인다. 배지·숫자는 그대로다 (`fit_band`).
        sc = self._sc

        def _mw(t):
            fam, size, bold = bandpaint.font_of("bold", t, sc)
            return self.surf.measure(t, fam, size, bold)
        fit_name(cells, _mw, NAME_MAX_PX * sc)
        # 점 깜박 1.2s — `_sync_blink` 의 반주기를 **점 색으로** 낸다 (그림이 안 읽으면 안 깜박인다)
        running = bool((self._last_state or {}).get("running"))
        blink_cells(cells, self._blink or not running, COLORS["bg"])
        rect = self._game_client()
        if rect:
            cw = int(rect[2])
            safe = round((DESIGN_SAFE[1] - DESIGN_SAFE[0]) * cw / DESIGN_W)
            fit_band(cells, lambda cs: bandpaint.band_size(self.surf, cs, sc)[0], safe)
        sig = [(c.get("key"), c.get("kind"), c.get("text"), c.get("fg"), c.get("bg"), c.get("outline"))
               for c in cells] + [self._border, self._look_dashed, round(self._alpha, 3), self._sc]
        if sig == self._cells:
            self._blit()     # 자리가 바뀌었을 수 있다 (내용은 그대로)
            return
        self._cells = sig
        wd, ht = bandpaint.band_size(self.surf, cells, self._sc)
        wd = max(int(120 * self._sc), wd)
        self.surf.resize(wd, ht)
        self._hit = bandpaint.paint(self.surf, cells, COLORS["bg"],
                                    self._border or COLORS["line"], self._sc, self._look_dashed)
        with self._lock:
            self._width = wd
        self._blit()

    def _blit(self) -> None:
        """그린 그림을 창에 얹는다 — **크기도 자리도 이 호출이 정한다** (`geometry` 가 필요 없다)."""
        if self.surf is None or not self.hwnd:
            return
        x, y = self._pos
        self._blit_to("밴드", self.surf, self.hwnd, int(x), int(y), int(round(self._alpha * 255)))
        self._check_lwa("밴드", self.hwnd)

    def _blit_to(self, what: str, surf, hwnd: int, x: int, y: int, alpha: int = 255) -> bool:
        """그림을 창에 얹는다. **실패하면 한 번은 말한다** — `UpdateLayeredWindow` 는 예외 없이 False 만
        돌려주고, 그러면 창이 화면에서 조용히 사라진다. 흔적이 없으면 「왜 안 보이나」를 영영 못 찾는다."""
        try:
            ok = bool(surf.blit(hwnd, int(x), int(y), int(alpha)))
            why = "" if ok else f"UpdateLayeredWindow 실패 (GetLastError={ctypes.GetLastError()})"
        except Exception as e:
            ok, why = False, f"{type(e).__name__}: {e}"
        if not ok and what not in self._blit_said:
            self._blit_said.add(what)
            self._say(f"[경고] {what} 창에 그림을 얹지 못했습니다 — {why}")
        elif ok and what in self._blit_said:
            self._blit_said.discard(what)      # 되살아났으면 다음 실패를 다시 알린다
        return ok

    def _check_lwa(self, what: str, hwnd: int) -> None:
        """**그린 뒤에** 확진한다 — 창이 LWA 방식에 고정돼 있으면 그림이 한 픽셀도 안 나온다.

        그리기 전에 물어보면 True 가 나올 수 있어 헛경고가 된다 (실제로 한 번 울렸다).
        조용히 실패하는 함정이라 한 번은 크게 적어 둔다 — 같은 함정에 두 번 걸렸다."""
        if what in self._lwa_said or not hwnd:
            return
        if self.u.lwa_locked(hwnd):
            self._lwa_said.add(what)
            self._say(f"[경고] {what} 창이 LWA 방식에 고정돼 있습니다 — 그림이 안 나옵니다. "
                      f"그 창에 Tk 투명도 속성을 걸지 마세요 (lwa_locked 주석 참조).")

    # ── 둥근 칩·원형 점 ──
    # **tkinter 의 Label 에는 `border-radius` 가 없다.** 종류 배지(4px)와 「일괄 수령」(11px)은
    # 둥근 모서리가 모양의 핵심이라 포기하지 않고 **Canvas 에 직접 그린다**.
    def _apply_look(self, status: str, flashing: bool, settings) -> None:
        """불투명도·테두리를 정해 둔다. **적용은 `_draw` 가 그림을 그릴 때 한다** —
        `SetLayeredWindowAttributes` 를 쓰면 `UpdateLayeredWindow` 와 부딪혀 창이 안 그려진다."""
        a = band_alpha(status, flashing, settings)
        b = BORDER.get(status, COLORS["line"])
        dashed = status in DASHED
        if (b, dashed) != (self._border, self._look_dashed) or abs(a - self._alpha) > 0.004:
            self._border, self._look_dashed, self._alpha = b, dashed, a
            self._redraw_now()

    def _sync_expand(self, state, alter, settings) -> None:
        """밴드 끝 `▾` 의 판 — 대기 목록과 가공. **그림 한 장**이다."""
        mode = str(settings.get("overlay_expand") or "off")
        want = self._expanded or (mode == "error" and bool(state.get("lastError")))
        if not want or self.panel_surf is None:
            if self.panel.winfo_viewable():
                self.panel.withdraw()
            return
        data = panel_rows(state, alter, settings)
        data["head"] = panel_head_cells(state, alter, settings,
                                        band_status(state, alter, self._flash_kind))
        # 밴드의 오늘 한 줄을 눌러 연 판이면 **7일 표**를 덧붙인다
        data["days"] = day_table(getattr(self, "_stats", None)) if getattr(self, "_day_open", False) else []
        lock = bool(self._cfg("overlay_lock", False))
        thr = bool(self._cfg("overlay_click_through", True))
        sig = (repr(data), lock, thr, round(self._sc, 3))
        if sig != getattr(self, "_panel_sig", None):
            self._panel_sig = sig
            w, h, hits = panelpaint.paint_panel(self.panel_surf, data, self._sc, COLORS,
                                                bandpaint.paint_cells, lock, thr)
            self._phit, self._panel_wh = hits, (w, h)
        ax, ay = self.band_anchor()
        self._panel_pos = (int(ax), int(ay + self.px(PANEL_GAP)))     # 밴드 아래 top 52
        if not self.panel.winfo_viewable():
            # 보이기 **전에 크기와 자리를 같이** 준다. 자리만 주면 창이 기본 크기(200x200)에
            # 갇혀 그림이 잘린다.
            pw, ph = getattr(self, "_panel_wh", (200, 200))
            self.panel.geometry(f"{pw}x{ph}+{self._panel_pos[0]}+{self._panel_pos[1]}")
        self._show(self.panel, "hwnd_panel", thr)
        self._blit_to("panel", self.panel_surf, self.hwnd_panel, self._panel_pos[0], self._panel_pos[1], 255)
        self._check_lwa("panel", self.hwnd_panel)

    def _sync_apanel(self, alter, settings) -> None:
        """가공 뒤 `▾` 의 판 — 가공 시설 3열. **그림 한 장**이다."""
        if not self._aexpanded or self.apanel_surf is None:
            if self.apanel.winfo_viewable():
                self.apanel.withdraw()
            return
        data = facility_panel(alter, band_status(self._last_state, alter, self._flash_kind)
                              if self._last_state is not None else "")
        lock = bool(self._cfg("overlay_lock", False))
        thr = bool(self._cfg("overlay_click_through", True))
        sig = (repr(data), lock, thr, round(self._sc, 3))
        if sig != getattr(self, "_apanel_sig", None):
            self._apanel_sig = sig
            w, h, hits = panelpaint.paint_apanel(self.apanel_surf, data, self._sc, COLORS,
                                                 svg_polylines, SLOT_STYLE, lock, thr)
            self._ahit, self._apanel_wh = hits, (w, h)
        ax, ay = self.band_anchor()
        self._apanel_pos = (int(ax), int(ay + self.px(PANEL_GAP)))    # 밴드 아래 top 52
        if not self.apanel.winfo_viewable():
            # 보이기 **전에 크기와 자리를 같이** 준다. 자리만 주면 창이 기본 크기(200x200)에
            # 갇혀 그림이 잘린다.
            pw, ph = getattr(self, "_apanel_wh", (200, 200))
            self.apanel.geometry(f"{pw}x{ph}+{self._apanel_pos[0]}+{self._apanel_pos[1]}")
        self._show(self.apanel, "hwnd_apanel", thr)
        self._blit_to("apanel", self.apanel_surf, self.hwnd_apanel, self._apanel_pos[0], self._apanel_pos[1], 255)
        self._check_lwa("apanel", self.hwnd_apanel)

    def _show_toast(self, kind: str, state, now: float) -> None:
        # 글자는 `toast_lines` 가 만든다 — 실패는 **실패한 항목**, 완료는 **방금 끝난 항목**의 이름으로.
        # 지금 도는 카드(currentCard) 이름을 쓰면 엉뚱한 항목이 실패한 것처럼 보인다.
        t1, tag, t2 = toast_lines(kind, state)
        self.toast_text = (ellipsis(t1, 14), tag, ellipsis(t2, 26), kind == "error")
        self._toast_sig = None       # 다시 그리게 한다
        self._toast_on = True

    def _on_toast_close(self, _e=None):
        """`×` — **토스트를 닫는 유일한 길**이다 (토스트는 스스로 사라지지 않는다)."""
        self._toast_on = False
        try:
            self.toast.withdraw()
        except Exception:
            pass

    def _sync_toast(self, now: float) -> None:
        """오류 토스트 — 밴드와 같은 방식으로 **그림 한 장**을 얹는다 (폭 236 고정)."""
        if not self._toast_on or self.toast_surf is None:
            if self.toast.winfo_viewable():
                self.toast.withdraw()
            return
        name, kindtxt, desc, bad = self.toast_text
        sc = self._sc
        sig = (name, kindtxt, desc, bad, round(sc, 3))
        w = int(round(TOAST_W * sc))
        if sig != getattr(self, "_toast_sig", None):
            self._toast_sig = sig
            self._paint_toast(w, sc, name, kindtxt, desc, bad)
        ax, ay = self.band_anchor()
        self._toast_pos = (int(ax), int(ay + self.px(PANEL_GAP)))    # 밴드 아래 top 52
        if not self.toast.winfo_viewable():
            self.toast.geometry(f"+{self._toast_pos[0]}+{self._toast_pos[1]}")
        # 처음엔 관통으로 연다. **`×` 위에서만** `_sync_through` 가 관통을 끈다 — 토스트는 스스로
        # 사라지지 않으므로 관통으로만 두면 영영 닫을 수 없다.
        self._show(self.toast, "hwnd_toast", True)
        self._blit_to("toast", self.toast_surf, self.hwnd_toast, self._toast_pos[0], self._toast_pos[1], 255)
        self._check_lwa("toast", self.hwnd_toast)

    def _paint_toast(self, w: int, sc: float, name, kindtxt, desc, bad) -> None:
        """`3px 1fr auto` · padding 8 10 · 왼쪽에 색 막대 · 오른쪽에 `×`."""
        C, S = COLORS, self.toast_surf
        fam = bandpaint.UI_FAMILY
        s1, s2 = max(1, int(round(11 * sc))), max(1, int(round(11 * sc)))
        pad, gap = 8 * sc, 8 * sc
        lh = S.text_h(fam, s1, True)
        h = int(round(pad * 2 + lh * 2 + 2 * sc))
        S.resize(w, h)
        S.clear()
        r = R_TOAST * sc
        S.round_rect(0.5, 0.5, w - 1, h - 1, r, fill=C["toastbg"], alpha=235)
        S.round_rect(0.5, 0.5, w - 1, h - 1, r,
                     outline=C["toastline"] if bad else C["toastline2"], width=max(1.0, sc))
        S.round_rect(pad + 2 * sc, pad, 3 * sc, h - pad * 2, 2 * sc,
                     fill=C["bad"] if bad else C["ok"])
        x = pad + 2 * sc + 3 * sc + gap
        y1 = pad + lh / 2.0
        wn = S.text(x, y1, name, fam, s1, C["strong"], True)
        if kindtxt:
            S.text(x + wn + 6 * sc, y1, kindtxt, fam, s1,
                   C["badtext"] if bad else C["oktext"], True)
        S.text(x, y1 + lh + 2 * sc, desc, fam, s2, C["toastbody"])
        # `×` 도 기호라 metric 으로 잡으면 1px 내려앉는다 (실측 125%·175% 에서 +1.0px).
        # 자리는 advance 그대로 — `anchor="e"` 가 advance 상자의 **오른쪽 끝**을 여기 맞춘다.
        S.icon(w - pad - 2 * sc, y1, "×", fam, s2, C["faint"], anchor="e")
        self._thit = [toast_close_rect(w, h, sc)]     # 눌리는 곳은 `×` 자리 하나뿐

    def _on_toast_click(self, e=None):
        """`×` — **그 자리를 눌렀을 때만** 닫는다.

        다른 곳을 눌러도 닫히면 「×」가 있는 이유가 없고, 게임을 누르려다 실패를 지워 버린다."""
        if e is None:
            return
        if self._rect_at(self._thit, self._toast_pos, e.x_root, e.y_root) == "close":
            self._on_toast_close()

    def _auto_collect(self, alter, settings, now: float) -> None:
        if not settings.get("overlay_auto_collect"):
            return
        targets = alter.get("targets") or []
        if not targets:
            return
        if now - self._auto_stamp < 30.0:   # 같은 완료를 30초에 한 번보다 자주 담지 않는다
            return
        key = {t["facility"] for t in targets}
        if key and key <= self._auto_tried:
            return
        self._auto_tried |= key
        self._auto_stamp = now
        self._collect_bg("자동")

    # ---- 입력 ----
    def _collect_bg(self, why: str) -> None:
        """수령 담기는 캐시 전체를 훑는 일(snapshot)이라 Tk 스레드에서 부르면 밴드가 잠깐 굳는다."""
        def go():
            r = self._call("collect_all") or {}
            self._say(f"일괄 수령({why}): 담음 {r.get('added', 0)} · 중복 {r.get('dup', 0)}")
        threading.Thread(target=go, name="overlay-collect", daemon=True).start()

    def _on_collect(self, _e=None):
        # 「전체 수령」은 0건이면 흐리게 남고 **누르면 아무 일도 없다**. 그리는 쪽이
        # 0건이면 적중 사각형을 안 내놓지만, 여기서도 한 번 더 막는다 — 수령 요청이 나가면 안 된다.
        if not self.collect_ready(self._last_alter):
            return
        self._collect_bg("버튼")

    @staticmethod
    def collect_ready(alter) -> bool:
        """수령할 것이 있나 — 밴드 「일괄 수령」이 켜지는 조건(`targets`)과 판의 완료 수(`done`) 둘 중 하나."""
        al = alter if isinstance(alter, dict) else {}
        return bool(al.get("targets")) or int(al.get("done") or 0) > 0

    def _on_aexpand(self, _e=None):
        """가공 그룹 뒤의 `▾` — 가공 시설 칸만 펼친다.
        두 판이 겹치지 않게 다른 하나는 접는다."""
        self._aexpanded = not self._aexpanded
        if self._aexpanded:
            self._expanded = False
        self._redraw_now()

    def _on_expand(self, _e=None):
        """밴드 끝 `▾` — 대기 목록 + 가공."""
        self._expanded = not self._expanded
        self._day_open = False        # 대기 구역으로 연 판에는 7일 표가 없다 · 닫을 때도 같이 접는다
        if self._expanded:
            self._aexpanded = False
        self._redraw_now()

    def _on_day(self, _e=None):
        """밴드의 오늘 한 줄을 눌렀다 — 펼침 판에 **7일 표**를 붙여 연다. 한 번 더 누르면 접는다."""
        self._day_open = not (getattr(self, "_day_open", False) and self._expanded)
        self._expanded = self._day_open
        if self._expanded:
            self._aexpanded = False
        self._redraw_now()

    # ── 컨트롤 줄 손잡이 (펼친 판 맨 아래줄) ──
    FRONT_GAP = 1.5      # 이 시간 안에 다시 눌러도 창을 또 띄우지 않는다

    def _front(self, tab: str = "") -> None:
        """앱 창을 앞으로. **연타해도 하나만** 뜬다 — 누른 만큼 새 창이 뜨지 않게."""
        now = time.time()
        if now - getattr(self, "_front_at", 0.0) < self.FRONT_GAP:
            return
        self._front_at = now
        threading.Thread(target=lambda: self._call("bring_front", tab),
                         name="overlay-front", daemon=True).start()

    def _on_bring_front(self, _e=None):
        """앱 창을 앞으로. 밴드에서 큐를 고치러 갈 길이 이것뿐이다 (단축키를 없앴다)."""
        self._front()

    def _on_toggle_lock(self, _e=None):
        """`🔒 위치 고정됨` — 누르면 풀리고 다시 누르면 잠긴다 (설정 탭의 「위치 잠금」과 같은 값).

        **잠그는 순간 지금 자리를 오프셋으로 굳힌다** — 그게 「좌측 고정」의 기준이 된다."""
        lock = not bool(self._cfg("overlay_lock", False))
        if lock:
            rect = self._game_client()
            if rect:
                ox, oy = band_offset(self._pos, rect)
                self._call("save_offset", ox, oy)
                with self._lock:
                    self._settings["overlay_off_x"], self._settings["overlay_off_y"] = ox, oy
        self._set_setting("overlay_lock", lock)
        self._redraw_now()

    def _on_reset_pos(self, _e=None):
        """`↻ 위치 초기화` — 자동 배치로 되돌린다."""
        self.reset_pos()
        self._redraw_now()

    def _on_toggle_through(self, _e=None):
        """`↗ 스마트 관통 중` — 클릭 통과를 켜고 끈다."""
        self._set_setting("overlay_click_through", not bool(self._cfg("overlay_click_through", True)))
        self._redraw_now()

    def _on_open_queue(self, _e=None):
        """대기 목록을 누르면 앱 창의 **큐 탭**을 연다."""
        self._front("queue")

    def _on_hint(self, _e=None):
        """「창에서 담기」 — 글자 뜻대로: 앱 창을 앞으로 올려 큐 탭을 열고 담기 서랍까지 연다.
        서랍은 서버가 `/api/queue` 응답에 `goDrawer` 를 한 번 실어 페이지가 연다 (`open_drawer` 훅)."""
        self._call("open_drawer")
        self._front("queue")

    def _on_open_settings(self, _e=None):
        """`⚙` — 앱 창의 오버레이 설정 탭을 연다."""
        self._front("overlay")

    def _redraw_now(self) -> None:
        """▾/▴ 는 구성이 아니라 글리프만 바뀌므로 위젯을 다시 짓지 않는다 — 누르는 중인 위젯을 없애면 안 된다."""
        if self._last_state is None:
            return
        s = dict(self._settings)
        self._draw(band_cells(self._last_state, self._last_alter, s,
                              band_status(self._last_state, self._last_alter, self._flash_kind),
                              time.time(), self._wings, self._sync_day(time.time())))
        self._sync_expand(self._last_state, self._last_alter, s)
        self._sync_apanel(self._last_alter, s)

    CURSORS = {"handle": "fleur", "expand": "hand2", "expand2": "hand2", "collect": "hand2",
               "panel": "hand2", "apanel": "hand2", "day": "hand2", "hint": "hand2"}

    def _sync_cursor(self, cur) -> None:
        """밴드는 그림 한 장이라 위젯마다 커서를 줄 수 없다 — **자리를 보고 창 커서를 바꾼다.**
        누를 수 있는 곳(▾ · 일괄 수령)에서 손가락 커서가 떠야 한다."""
        want = self.CURSORS.get(self._hit_at(*cur), "")
        if want != getattr(self, "_cursor", None):
            self._cursor = want
            try:
                self.root.configure(cursor=want)
            except Exception:
                pass

    PANEL_ACTS = {"collect": "_on_collect", "aexpand": "_on_aexpand", "expand": "_on_expand",
                  "lock": "_on_toggle_lock", "reset": "_on_reset_pos",
                  "through": "_on_toggle_through", "gear": "_on_open_settings",
                  "queue": "_on_open_queue",
                  "day": "_on_day",       # 판의 7일 표를 누르면 접는다
                  # 컨트롤 줄의 첫 알약 `⏎` = 앱 창 앞으로 (`panelpaint.controls` 가 그린다).
                  "front": "_on_bring_front"}

    def _on_panel_press(self, e, which: str):
        """판을 눌렀다 — **위젯이 없으므로 자리로 무엇을 눌렀는지 가린다.**"""
        hits = self._ahit if which == "apanel" else self._phit
        org = getattr(self, "_%s_pos" % which, (0, 0))
        key = self._rect_at(hits, org, e.x_root, e.y_root)
        fn = getattr(self, self.PANEL_ACTS.get(key, ""), None)
        if fn:
            fn()

    @staticmethod
    def _rect_at(hits, org, px_, py_):
        ox, oy = org
        for item in hits or ():
            if not isinstance(item, tuple):
                continue
            key, rx, ry, rw, rh = item
            if ox + rx <= px_ <= ox + rx + rw and oy + ry <= py_ <= oy + ry + rh:
                return key
        return None

    def _panel_cursor(self, win, hits, org, cur) -> None:
        """판 위 커서 — 누를 수 있는 자리에서 손가락이 떠야 한다."""
        try:
            if not win.winfo_viewable():
                return
        except Exception:
            return
        want = "hand2" if self._rect_at(hits, org, cur[0], cur[1]) else ""
        if want != self._cursors.get(id(win)):
            self._cursors[id(win)] = want
            try:
                win.configure(cursor=want)
            except Exception:
                pass

    def _hit_at(self, px_, py_):
        """화면 좌표가 어느 적중 사각형 안인가 (없으면 None)."""
        bx, by = self._pos
        for item in self._hit:
            if not isinstance(item, tuple):
                continue
            key, rx, ry, rw, rh = item
            if bx + rx <= px_ <= bx + rx + rw and by + ry <= py_ <= by + ry + rh:
                return key
        return None

    def _on_band_press(self, e):
        """밴드를 눌렀다. **위젯이 없으므로 자리로 무엇을 눌렀는지 가린다.**"""
        key = self._hit_at(e.x_root, e.y_root)
        if key == "handle":
            self._drag_start(e)
        elif key == "collect":
            self._on_collect()
        elif key in ("expand", "apanel"):     # 가공 구역 = 가공기 현황 판
            self._on_aexpand()
        elif key in ("expand2", "panel"):     # 대기 구역 = 대기 목록 판
            self._on_expand()
        elif key == "day":                    # 오늘 한 줄 = 펼침 판의 7일 표
            self._on_day()
        elif key == "hint":                   # 「창에서 담기」 = 앱 창 앞으로 + 담기 서랍
            self._on_hint()

    def _drag_start(self, e):
        if self._cfg("overlay_lock", False):
            return
        # **`_pos` 를 기준으로 잡는다.** 창 자리는 `blit` 이 정하므로 Tk 가 아는 `winfo_x()` 는
        # 뒤처져 있을 수 있고, 그러면 누르는 순간 밴드가 튄다.
        self._drag = (e.x_root - self._pos[0], e.y_root - self._pos[1])

    def _drag_move(self, e):
        if not self._drag:
            return
        self._pos = (e.x_root - self._drag[0], e.y_root - self._drag[1])
        self._blit()             # 자리는 blit 이 정한다 (geometry 는 UpdateLayeredWindow 와 싸운다)
        self._place_children()   # 펼침 판·토스트가 끌려오는 밴드를 따라온다

    def _drag_end(self, _e):
        if not self._drag:
            return
        self._drag = None
        # **`_pos`·`_width` 를 쓴다.** 창 자리·크기는 이제 `blit` 이 정하므로 Tk 가 아는
        # `winfo_x/width` 는 뒤처져 있고, 그 값으로 `geometry` 를 부르면 창이 잘못된 크기로
        # 줄어 레이어드 그림이 **잘려 보인다** (손 떼면 밴드가 반쯤 사라진다).
        ox, oy, sw, sh = self._screen_box()      # 주 모니터가 아니라 **모니터 전부** 안으로
        x, y = clamp_pos(self._pos[0], self._pos[1], self._width or 200, self.px(BAND_H), sw, sh, ox, oy)
        self._pos = (x, y)
        self._blit()
        self._place_children()
        # **끌어도 따라다니기는 멈추지 않는다**. 화면 절대 좌표가 아니라
        # 게임 클라이언트 영역 기준 **오프셋**을 저장해 두고, 게임 창이 움직이면 그 간격을 유지한다.
        rect = self._game_client()
        if rect:
            ox, oy = band_offset((x, y), rect)
            self._call("save_offset", ox, oy)
            with self._lock:
                self._settings["overlay_off_x"], self._settings["overlay_off_y"] = ox, oy
            if self._cfg("overlay_follow_game", True):
                self._set_setting("overlay_follow_game", False)   # 「가운데 위 자동 배치」 → 끌어 둔 자리
            self._say(f"⋮⋮ 로 옮겼습니다 — 게임 기준 오프셋 ({ox},{oy}) 로 계속 따라갑니다")
        else:
            self._call("save_pos", x, y)

    # ---- 40ms: 클릭 통과 예외 · 깜박임 ----
    def _tick_ui(self) -> None:
        if self.root is None:
            return
        if self._stopping.is_set():
            # 반드시 닫히는 길: 종료는 **Tk 자기 스레드**에서 끝낸다.
            # 다른 스레드의 root.after 에 기대면 그 스레드가 붙잡혀 있을 때 창이 남는다.
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        if not self._settings.get("overlay_enabled"):
            self._reschedule_ui()
            return
        try:
            if self._reset_pending:     # 「위치 초기화」 — 서버 스레드가 세운 깃발을 **여기(Tk 스레드)서** 집행한다
                self._reset_pending = False
                self._apply_reset_pos()
            if self._sync_dpi():      # 다른 배율의 모니터로 옮겨 갔다 — 글꼴·치수를 다시 잡는다
                self._redraw_now()
            self._sync_through()
            self._sync_follow()   # 게임 창 따라가기 — 여기서 돈다 (1초 주기면 창을 끌 때 눈에 띄게 뒤처진다)
            self._sync_zorder()   # 게임 바로 위 한 칸 — 어긋났을 때만 다시 끼운다
            self._sync_blink()
        except Exception as e:
            # 40ms 마다 도는 루프라 매번 적으면 로그가 넘친다 — **같은 예외는 한 번만**. 조용히 삼키면
            # 따라가기·관통·Z 순서가 죽어도 흔적이 없다 (밴드가 「그냥 안 보임」이 된다).
            key = f"{type(e).__name__}: {e}"
            if key not in self._ui_said:
                self._ui_said.add(key)
                import traceback
                traceback.print_exc()
                self._say(f"40ms 루프 오류 (이후 같은 오류는 적지 않습니다) — {key}")
        self._reschedule_ui()

    def _reschedule_ui(self) -> None:
        """꺼져 있으면 커서·Z 순서를 25Hz 로 볼 이유가 없다 — 유휴 주기로 늦춘다.
        **루프 자체는 멈추지 않는다**: 다시 켤 때 이 루프가 창을 되살린다."""
        try:
            self.root.after(POLL_MS if self._settings.get("overlay_enabled") else IDLE_MS, self._tick_ui)
        except Exception:
            pass

    def _sync_through(self) -> None:
        """커서가 예외 위젯 사각형 안이면 관통을 끈다. **바뀔 때만** SetWindowLongW 를 부른다.

        **밴드와 가공기 판 둘 다** 본다. 판을 통째로 관통시켜 두면 컨트롤 줄을 못 눌러
        「스마트 관통」을 끌 방법이 없어진다."""
        if not self.hwnd:      # 아직 한 번도 보이지 않았다 (스타일을 걸 창이 없다)
            return
        on = bool(self._cfg("overlay_click_through", True))
        cur = self.u.cursor() if on else (0, 0)
        self._through = self._apply_through(self.hwnd, self.root, self._hit, on, cur, self._drag)
        cur2 = cur if on else self.u.cursor()
        self._sync_cursor(cur2)
        # 판에도 위젯이 없다 — 자리를 보고 그 창의 커서를 바꾼다
        for win, hits, org in ((self.panel, self._phit, getattr(self, "_panel_pos", (0, 0))),
                               (self.apanel, self._ahit, getattr(self, "_apanel_pos", (0, 0))),
                               (self.toast, self._thit, self._toast_pos)):
            self._panel_cursor(win, hits, org, cur2)
        # 밴드의 적중 자리는 **사각형 목록**이다 (캔버스에 그리므로 위젯이 없다)
        if self.hwnd_apanel:
            self._apply_through(self.hwnd_apanel, self.apanel, self._ahit, on, cur, False,
                                getattr(self, "_apanel_pos", (0, 0)))
        if self.hwnd_panel:
            self._apply_through(self.hwnd_panel, self.panel, self._phit, on, cur, False,
                                getattr(self, "_panel_pos", (0, 0)))
        # 토스트도 본다 — `×` 위에서만 관통이 풀린다. 토스트는 자동으로 사라지지 않으므로
        # 이 줄이 없으면 토스트는 **닫을 길이 없다** (관통 창은 어디를 눌러도 게임이 받는다).
        if self.hwnd_toast:
            self._apply_through(self.hwnd_toast, self.toast, self._thit, on, cur, False, self._toast_pos)

    def _apply_through(self, hwnd: int, win, hits, on: bool, cur, dragging, org=None) -> bool:
        """창 하나의 관통을 맞춘다. 돌려주는 것: 지금 관통인가."""
        want = on
        if dragging:
            want = False          # 끄는 중에 커서가 손잡이를 벗어나도 계속 받아야 한다
        elif want:
            try:
                viewable = win.winfo_viewable()
            except Exception:
                viewable = False
            if viewable and self._over(hits, cur, org):
                want = False
        ex = self.u.ex(hwnd)
        new = (ex | WS_EX_TRANSPARENT) if want else (ex & ~WS_EX_TRANSPARENT)
        if new != ex:
            self.u.set_ex(hwnd, new)
            # **스타일을 바꾸면 레이어드 창의 그림이 무효가 된다.** 다시 얹지 않으면 밴드가
            # 반쯤 지워진 채로 남는다 (끌기 시작 = 관통 해제 = 스타일 변경이다).
            if hwnd == self.hwnd:
                self._blit()
        return want

    def _over(self, hits, cur, org=None) -> bool:
        """커서가 적중 사각형 안인가 (`org` = 그 창의 왼쪽 위 좌표)."""
        return self._rect_at(hits, org or self._pos, cur[0], cur[1]) is not None

    def _sync_blink(self) -> None:
        now = time.time()
        if now - self._blink_at < 0.6:
            return
        self._blink_at = now
        self._blink = not self._blink
        # 큐 진행 앞의 원을 깜박인다 (`animation:blink 1.2s`).
        # 밴드는 캔버스 한 장이라 **다시 그리는 것이 곧 깜박임**이다 — 0.6초에 한 번이라 싸다.
        if self._last_state is not None and self._last_state.get("running"):
            self._redraw_now()

    # ---- 전역 단축키 (등록도 루프도 전용 스레드) ----
    def _set_setting(self, key: str, value) -> None:
        with self._lock:
            self._settings[key] = value
        self._call("save_setting", key, value)


OVERLAY = Overlay()
