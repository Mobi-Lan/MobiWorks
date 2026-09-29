"""경량판·미니판의 합주 시작 연출 — 표준 라이브러리만 (tkinter + ctypes).

정식판은 투명 BrowserWindow 하나로 게임 화면을 덮지만, 경량판에는 일렉트론이 없다.
그래서 게임 창 위아래에 검은 띠 창을 하나씩 띄워 같은 연출을 만든다(가운데는 창 자체가 없으니 투명한 셈).
두 창 모두 항상 위·클릭 관통·작업 표시줄 숨김이라 연주를 방해하지 않는다.

시간표 — 전체 6.00초, 앞 1초 페이드인, 뒤 1초 페이드아웃.
Tcl 은 인터프리터를 만든 스레드에서만 만져야 해서, 여기 있는 것은 전부 overlay_tk 의 화면 스레드에서만 돈다.
"""
from __future__ import annotations

import ctypes
import math
import time

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_GA_ROOT = 2                        # Tk 의 winfo_id() 는 자식 창 — 진짜 최상위 창을 얻는다
_HWND_TOP, _HWND_TOPMOST, _HWND_NOTOPMOST = 0, -1, -2
_GW_HWNDNEXT, _GW_HWNDPREV = 2, 3
_WS_EX_TOPMOST = 0x8
_SWP_KEEP = 0x0001 | 0x0002 | 0x0010   # NOSIZE | NOMOVE | NOACTIVATE
_MIN_W = 50                            # 이보다 좁은 창은 「진짜 창」으로 세지 않는다


def _u32():
    """인자·반환 타입을 박아 둔 user32. 안 박으면 64비트에서 창 핸들이 32비트로 잘려 조용히 실패한다."""
    global _U
    try:
        return _U
    except NameError:
        pass
    import ctypes.wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    for name, args, res in (
        ("GetAncestor", [wt.HWND, wt.UINT], wt.HWND),
        ("GetParent", [wt.HWND], wt.HWND),
        ("GetWindow", [wt.HWND, wt.UINT], wt.HWND),
        ("GetClassNameW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("IsWindowVisible", [wt.HWND], wt.BOOL),
        ("IsIconic", [wt.HWND], wt.BOOL),
        ("IsWindow", [wt.HWND], wt.BOOL),
        ("GetWindowLongW", [wt.HWND, ctypes.c_int], ctypes.c_long),
        ("SetWindowLongW", [wt.HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long),
        ("GetWindowRect", [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
        ("GetClientRect", [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
        ("ClientToScreen", [wt.HWND, ctypes.POINTER(wt.POINT)], wt.BOOL),
        ("SetWindowPos", [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT], wt.BOOL),
        ("EnumWindows", [ctypes.c_void_p, wt.LPARAM], wt.BOOL),
    ):
        f = getattr(u, name)
        f.argtypes, f.restype = args, res
    _U = u
    return u


_ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def hwnd_of(win) -> int:
    """Tk 창의 진짜 최상위 핸들 (winfo_id() 는 자식 창이다)."""
    try:
        u = _u32()
        wid = win.winfo_id()
        return int(u.GetAncestor(wid, _GA_ROOT) or u.GetParent(wid) or wid)
    except Exception:
        return 0


def find_game() -> int:
    """마비노기 모바일 창 핸들. 없으면 0. (비싸다 — 핸들은 기억해 두고 가끔만 부른다)"""
    u = _u32()
    box = []

    @_ENUM
    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, c, 64)
        if c.value == "UnityWndClass":
            box.append(int(h))
            return False
        return True

    u.EnumWindows(cb, 0)
    return box[0] if box else 0


def client_rect(hwnd):
    """창의 클라이언트 영역을 화면 좌표로. 최소화·비정상이면 None.
    (GetWindowRect 는 테두리가 섞여 들어와 쓰지 않는다 — 이 기계에서 8px·30px 차이가 난다)"""
    import ctypes.wintypes as wt
    u = _u32()
    if not hwnd or not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
        return None
    r, pt = wt.RECT(), wt.POINT(0, 0)
    if not u.GetClientRect(hwnd, ctypes.byref(r)) or not u.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    if r.right < 200 or r.bottom < 200:        # 스플래시·숨은 창 거르기
        return None
    return int(pt.x), int(pt.y), int(r.right), int(r.bottom)


def _insert_target(game: int, ours: set) -> int:
    """게임 바로 위에 있는 「진짜 보이는 창」. 그 아래에 우리를 끼우면 게임보다만 위가 된다.
    보이지 않는 창(Default IME·MSCTFIME UI·트레이)을 기준으로 삼으면 SetWindowPos 가 조용히 실패한다."""
    import ctypes.wintypes as wt
    u = _u32()
    h = game
    for _ in range(64):
        h = u.GetWindow(h, _GW_HWNDPREV)
        if not h:
            return _HWND_TOP
        if int(h) in ours or not u.IsWindowVisible(h):
            continue
        if u.GetWindowLongW(h, _GWL_EXSTYLE) & _WS_EX_TOPMOST:
            return _HWND_TOP          # 최상위 띠에 닿았다 — 그 아래에 끼우면 우리도 최상위가 된다
        r = wt.RECT()
        if not u.GetWindowRect(h, ctypes.byref(r)) or (r.right - r.left) < _MIN_W:
            continue
        return int(h)
    return _HWND_TOP


def _is_above(win: int, game: int) -> bool:
    """win 이 게임보다 위인가. 우리 창에서 **아래로** 걸으며 게임을 찾는다 (보이는 창만 센다).

    끝까지 걸었는데 못 만나면 **아래에 있다(False)**. 아래로 걸어 못 만났다는 것은 게임이 위에
    있다는 뜻이기 때문이다. 예전에는 여기서 True 를 돌려줘서, 게임 뒤로 한 번 들어가면
    「고칠 게 없다」로 보고 스스로 못 돌아왔다 (오버레이가 창 뒤로 숨었다).
    **「못 물어봤다」와 「끝까지 봤는데 없다」는 다르다** — 앞엣것만 True 다 (모비웍스 규칙).

    틀릴 때는 안전한 쪽으로 틀린다: 사이에 보이는 창이 12개 넘게 끼면 위에 있어도 「아래」로 보고
    한 번 더 끼워 넣는다. 헛일이지만 해롭지 않다. 전체 z순서를 훑는 방법은 쓰지 않는다 —
    비용도 비용이고, 훑는 사이에 순서가 바뀌어 「정확해 보이는 낡은 답」이 나온다."""
    u = _u32()
    try:
        h, steps = win, 0
        while steps < 12:
            h = u.GetWindow(h, _GW_HWNDNEXT)
            if not h:
                return False
            if int(h) == game:
                return True
            if u.IsWindowVisible(h):
                steps += 1
        return False                             # 끝까지 봤는데 없다 = 게임이 위에 있다
    except Exception:
        return True                              # 못 물어보면 건드리지 않는다


def clear_topmost(h: int) -> None:
    """최상위 깃발 떼기. 정규 경로로 부르고, 그래도 남아 있으면 비트를 직접 지운다.
    한 번 최상위가 되면 「게임보다 위인가」는 늘 참이라, 깃발 검사만이 유일한 탈출구다."""
    u = _u32()
    u.SetWindowPos(h, _HWND_NOTOPMOST, 0, 0, 0, 0, _SWP_KEEP)
    ex = u.GetWindowLongW(h, _GWL_EXSTYLE)
    if ex & _WS_EX_TOPMOST:
        u.SetWindowLongW(h, _GWL_EXSTYLE, ex & ~_WS_EX_TOPMOST)


def keep_above_game(hwnds, game: int, force: bool = False) -> bool:
    """「모든 창의 하위에 올 수는 있지만 마비노기보다는 위」.

    게임 바로 위 창을 찾아 그 아래에 우리 창을 끼운다. SetWindowPos(X, Y) 는 X 를 Y 「아래」에 놓는다.
    같은 기준에 여러 개를 넣으면 나중에 넣은 것이 더 위로 쌓이므로 [밴드, 연주 패널] 순으로 넣는다.
    최상위(TOPMOST)로 올렸다 내리는 방법은 쓰지 않는다 — 그러면 디스코드·브라우저까지 덮는다."""
    hwnds = [h for h in hwnds if h]
    if not game or not hwnds:
        return False
    u = _u32()
    # 최상위 깃발부터 본다 — 한 번 붙으면 「게임보다 위인가」는 늘 참이라 스스로 못 빠져나온다
    stuck = [h for h in hwnds if u.GetWindowLongW(h, _GWL_EXSTYLE) & _WS_EX_TOPMOST]
    for h in stuck:
        clear_topmost(h)                # 깃발만 떨어지고 자리는 남는다
    if not force and not stuck and all(_is_above(h, game) for h in hwnds):
        return False
    ours = set(hwnds)
    target = _insert_target(game, ours)
    for h in hwnds:
        if target == _HWND_TOP:
            # 게임 위에 보통 창이 하나도 없는 때다. HWND_TOP 은 전경 프로세스가 아니면 먹지 않으므로
            # 최상위로 올렸다 곧바로 내린다 — 이때는 「보통 창들 중 맨 앞」이 곧 「게임 바로 위」다.
            u.SetWindowPos(h, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_KEEP)
            u.SetWindowPos(h, _HWND_NOTOPMOST, 0, 0, 0, 0, _SWP_KEEP)
            if u.GetWindowLongW(h, _GWL_EXSTYLE) & _WS_EX_TOPMOST:   # 내리는 호출이 실패하면 최상위로 남는다
                clear_topmost(h)
        else:
            u.SetWindowPos(h, target, 0, 0, 0, 0, _SWP_KEEP)
    # SetWindowPos 의 반환값은 「호출이 유효했나」지 「자리가 바뀌었나」가 아니다 — 보지 않는다.
    # 대신 0.3초 뒤 다음 주기가 _is_above 로 결과를 다시 재고, 아니면 또 끼운다 (모비웍스 구조).
    return True


BAR = "#06070b"          # 띠 바탕
ACC = "#c8a6f0"
ACC_DIM = "#a893c9"
ACC_FAR = "#8b7ba8"
ROSTER_T = "#7d6f96"     # 칭호
ROSTER_N = "#ded6ee"     # 닉네임
SONG_FG = "#ffffff"
FONT = "Malgun Gothic"
MAX_PLAYERS = 6

# 시간표(초)
T_IN, T_CLOSE, T_OPEN, T_FADE, T_TOTAL = 1.00, 0.40, 0.30, 1.00, 6.00
T_TITLE, T_NAME, T_STEP = 0.32, 0.30, 0.06
FPS_MS = 25


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def _ease(p):
    """cubic-bezier(.2,.9,.3,1) 근사 — 빠르게 나왔다가 부드럽게 멎는다."""
    return 1 - (1 - p) ** 3


def _mix(a: str, b: str, t: float) -> str:
    """색 a 에서 b 로 t 만큼. 글자를 띠 바탕색에서 제 색으로 끌어올려 페이드인처럼 보이게 쓴다."""
    t = _clamp(t)
    ca = (int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16))
    cb = (int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16))
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ca, cb))


class Opening:
    """띠 두 개로 만드는 합주 시작 연출. play() 와 _tick() 은 화면 스레드에서만 부른다."""

    def __init__(self, root, game_rect=None, log=print):
        self.root, self._game_rect, self._log = root, game_rect, log
        self.tops = []          # [(Toplevel, Canvas), …]
        self._items = {}
        self._area = (0, 0, 0, 0)
        self._barh = 0
        self._t0 = 0.0
        self._t_open = 0.0
        self._running = False

    def busy(self) -> bool:
        return self._running

    # ── 자리 잡기 ──
    def _area_now(self):
        """게임 창 안쪽 영역. 게임이 없으면 화면 전체로 떨어진다.
        좌표는 이 프로세스가 보는 그대로라(tkinter 와 같은 공간) 따로 환산하지 않는다."""
        try:
            r = self._game_rect() if self._game_rect else None
            if r and r.get("found"):
                return int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"])
            # 왜 화면 전체로 떨어졌는지 남긴다 — 배포판 로그에 「3440x1440+0+0」만 있어 원인을 못 봤다
            self._log(f"[opening] 게임 창을 못 찾아 화면 전체에 띄웁니다 ({(r or {}).get('reason') or 'found=False'})")
        except Exception as e:
            self._log(f"[opening] game rect failed: {e}")
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _style(self, win) -> None:
        try:
            u = ctypes.windll.user32
            h = hwnd_of(win)
            ex = (u.GetWindowLongW(h, _GWL_EXSTYLE) | _WS_EX_LAYERED | _WS_EX_TRANSPARENT
                  | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
            u.SetWindowLongW(h, _GWL_EXSTYLE, ex)
        except Exception as e:
            self._log(f"[opening] style failed: {e}")

    def _destroy(self) -> None:
        for win, _c in self.tops:
            try:
                win.destroy()
            except Exception:
                pass
        self.tops = []
        self._items = {}
        self._running = False

    # ── 재생 ──
    def play(self, data: dict) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self._destroy()
        given = data.get("players") or []
        players = [(str(t or ""), str(n or "")) for t, n in given if str(n or "").strip()][:MAX_PLAYERS]
        rest = max(0, len(given) - len(players))
        n = len(players)
        song = str(data.get("song") or "")
        badge = str(data.get("badge") or (f"{n}인 합주" if n > 1 else "솔로"))
        kicker = str(data.get("kicker") or ("ENSEMBLE" if n > 2 else "DUET" if n == 2 else "SOLO"))

        gx, gy, gw, gh = self._area_now()
        self._area = (gx, gy, gw, gh)
        self._game = find_game()
        barh = max(30, int(gh * (0.16 if n > 1 else 0.13)))
        pad = max(12, int(gw * 0.031))

        def px(frac):       # 화면 폭에 비례한 길이 — 정식판의 컨테이너 단위(cqw)와 같은 방식
            return int(gw * frac)

        def fs(frac):       # 글자 크기만 최소값을 둔다 (창이 아주 작아도 읽히게)
            return max(9, int(gw * frac))

        f_song = tkfont.Font(family=FONT, size=-fs(0.034 if n > 1 else 0.037), weight="bold")
        f_badge = tkfont.Font(family=FONT, size=-fs(0.0102))
        f_kick = tkfont.Font(family=FONT, size=-fs(0.0072), weight="bold")
        f_title = tkfont.Font(family=FONT, size=-fs(0.0064))
        f_name = tkfont.Font(family=FONT, size=-fs(0.0102), weight="bold")

        def band(y):
            w = tk.Toplevel(self.root)
            w.withdraw()
            w.overrideredirect(True)
            w.configure(bg=BAR)
            w.attributes("-alpha", 0.0)          # 최상위로 만들지 않는다 — 게임보다만 위 (_tick 이 지킨다)
            w.geometry(f"{gw}x1+{gx}+{y}")
            c = tk.Canvas(w, width=gw, height=barh, bg=BAR, highlightthickness=0, bd=0)
            c.place(x=0, y=0)
            w.deiconify()
            w.update_idletasks()
            self._style(w)
            self.tops.append((w, c))
            return c

        ct = band(gy)                    # 위 띠 — 아래로 자란다
        cb = band(gy + gh - 1)           # 아래 띠 — 위로 자란다

        it = self._items = {}
        mid = barh // 2

        # ── 위 띠: 왼쪽 머리말 + 막대, 오른쪽 사람들 (칭호 윗줄 · 영지 아랫줄) ──
        # **게임은 닉네임을 주지 않는다.** 아랫줄에 오는 값은 RealmName = 영지(서버) 이름이다
        # — 예전에는 이것을 「연주자 이름」처럼 보여 줬다. 이제 윗줄 칭호가 주인공이고
        # 아랫줄은 영지로 흐리게 적는다.
        it["kick"] = ct.create_text(pad, mid - px(0.004), text=" ".join(kicker), anchor="sw", fill=BAR, font=f_kick)
        it["eq"] = []
        it["eq_top"] = mid + px(0.004)
        it["eq_h"] = px(0.010)
        bw, bgap = max(2, px(0.0027)), max(2, px(0.0023))
        for i, hfrac in enumerate((0.55, 0.80, 0.40, 1.0, 0.62, 0.88)):
            x0 = pad + i * (bw + bgap)
            it["eq"].append((ct.create_rectangle(x0, it["eq_top"], x0 + bw, it["eq_top"] + 2, fill=BAR, width=0), hfrac))

        it["who"] = []
        it["more"] = None
        gap = px(0.0164)
        right = gw - pad
        if rest:
            it["more"] = ct.create_text(right, mid, text=f"외 {rest}", anchor="e", fill=BAR, font=f_title)
            right -= f_title.measure(f"외 {rest}") + gap
        for title, nick in reversed(players):      # 오른쪽부터 왼쪽으로 채운다 (등장도 오른쪽부터)
            colw = max(f_title.measure(title) if title else 0, f_name.measure(nick))
            y_t = mid - px(0.0022)
            a = ct.create_text(right, y_t, text=title, anchor="se", fill=BAR, font=f_title) if title else None
            b = ct.create_text(right, y_t + px(0.0016), text=nick, anchor="ne", fill=BAR, font=f_name)
            it["who"].append((a, b))
            right -= colw + gap

        # ── 아래 띠: 곡명 · 구분선 · 배지 ──
        it["song_x"] = pad
        it["song_dx"] = px(0.016)
        it["song"] = cb.create_text(pad - it["song_dx"], mid, text=song, anchor="w", fill=BAR, font=f_song)
        sx = pad + f_song.measure(song) + px(0.0145)
        it["vr"] = cb.create_line(sx, mid - px(0.012), sx, mid + px(0.012), fill=BAR, width=1)
        it["badge"] = cb.create_text(sx + px(0.0145), mid, text=badge, anchor="w", fill=BAR, font=f_badge)

        # ── 안쪽 가장자리의 가는 선 (가운데만 밝게) ──
        it["edge"] = []
        segs = 40
        for i in range(segs):
            p = i / (segs - 1)
            k = _clamp(min(p, 1 - p) / 0.2)
            x0, x1 = gw * i / segs, gw * (i + 1) / segs
            it["edge"].append((ct.create_line(x0, barh - 1, x1, barh - 1, fill=BAR, width=2),
                               cb.create_line(x0, 1, x1, 1, fill=BAR, width=2), k))

        self._barh = barh
        roster_end = T_IN + 0.3 + max(0, n - 1) * T_STEP + T_NAME
        self._t_open = roster_end + max(0.6, T_TOTAL - T_FADE - roster_end)
        self._t0 = time.monotonic()
        self._running = True
        self._log(f"[opening] {song!r} {gw}x{gh}+{gx}+{gy} 사람 {n}명 (칭호·영지)")
        self._tick()

    def _tick(self) -> None:
        if not self._running or len(self.tops) < 2:
            return
        t = time.monotonic() - self._t0
        gx, gy, gw, gh = self._area
        (wt, ct), (wb, cb) = self.tops[0], self.tops[1]
        barh, it = self._barh, self._items
        try:
            closing = t >= self._t_open
            # 전체 밝기: 앞 1초 페이드인, 끝 1초 페이드아웃
            alpha = 1 - _clamp((t - self._t_open) / T_FADE) if closing else _clamp(t / T_IN)
            # 띠 높이: 열림 0.40초, 닫힘 0.30초
            grow = 1 - _ease(_clamp((t - self._t_open) / T_OPEN)) if closing else _ease(_clamp(t / T_CLOSE))
            hh = max(1, int(barh * grow))
            wt.attributes("-alpha", alpha)
            wb.attributes("-alpha", alpha)
            wt.geometry(f"{gw}x{hh}+{gx}+{gy}")
            wb.geometry(f"{gw}x{hh}+{gx}+{gy + gh - hh}")
            cb.place(x=0, y=hh - barh)       # 아래 띠는 내용을 아래에 붙인 채로 잘린다


            # 닫히는 동안에는 글자도 같이 사그라든다
            fade = 1 - _clamp((t - self._t_open) / (T_OPEN * 0.8)) if closing else 1.0

            def show(cv, item, start, dur, color):
                if item is None:
                    return
                p = 1.0 if closing else _clamp((t - start) / dur)
                cv.itemconfig(item, fill=_mix(BAR, color, _ease(p) * fade))

            k = _ease(_clamp((t - T_IN) / T_TITLE))
            show(cb, it["song"], T_IN, T_TITLE, SONG_FG)
            cb.coords(it["song"], it["song_x"] - it["song_dx"] * (1 - k), barh // 2)
            show(cb, it["vr"], T_IN + 0.16, 0.24, ACC_DIM)
            show(cb, it["badge"], T_IN + 0.20, 0.26, ACC_DIM)

            t_roster = T_IN + 0.3
            show(ct, it["kick"], t_roster, 0.28, ACC_FAR)
            ke = (1.0 if closing else _ease(_clamp((t - t_roster) / 0.28))) * fade
            for i, (rect, hfrac) in enumerate(it["eq"]):
                amp = 0.35 + 0.65 * abs(math.sin(t * 2.2 + i * 0.8))
                hpx = max(2, int(it["eq_h"] * hfrac * amp))
                x0, _y0, x1, _y1 = ct.coords(rect)
                ct.coords(rect, x0, it["eq_top"] + it["eq_h"] - hpx, x1, it["eq_top"] + it["eq_h"])
                ct.itemconfig(rect, fill=_mix(BAR, ACC, ke))
            for i, (a, b) in enumerate(it["who"]):
                show(ct, a, t_roster + i * T_STEP, T_NAME, ROSTER_T)
                show(ct, b, t_roster + i * T_STEP, T_NAME, ROSTER_N)
            show(ct, it["more"], t_roster, T_NAME, ACC_FAR)

            if int(t * 5) != int((t - FPS_MS / 1000) * 5):        # 0.2초마다만 확인 (매 프레임은 비싸다)
                keep_above_game([hwnd_of(wt), hwnd_of(wb)], self._game)
            edge = 0.5 * (fade if closing else _ease(_clamp(t / T_CLOSE)))
            for top_line, bot_line, k2 in it["edge"]:
                col = _mix(BAR, ACC, edge * k2)
                ct.itemconfig(top_line, fill=col)
                cb.itemconfig(bot_line, fill=col)
        except Exception as e:
            self._log(f"[opening] tick failed: {e}")
            self._destroy()
            return
        if t >= T_TOTAL:
            self._destroy()
            return
        try:
            self.root.after(FPS_MS, self._tick)
        except Exception:
            self._destroy()
