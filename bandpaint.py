# -*- coding: utf-8 -*-
"""밴드 한 줄을 **그림 한 장으로 그린다**.

왜 위젯도 Tk 캔버스도 아닌가 — 둘 다 **안티에일리어싱이 없다.** `SetWindowRgn` 으로 창을
둥글게 깎으면 모서리가 계단으로 잘리고, Tk 캔버스의 `create_polygon` 도 가장자리를 안 부드럽게
한다.

그리고 밴드 배경은 `background: rgba(9,12,17,.82)` 다 — **배경만 반투명하고 글자는 불투명**.
창 전체에 `-alpha` 를 걸면 글자까지 같이 비쳐 게임 글자가 밴드를 뚫고 올라온다.

둘 다 **픽셀마다 알파를 따로 주면** 풀린다 = `UpdateLayeredWindow`. `paint32.Surface` 가 그것을
한다 (GDI+, 표준 라이브러리만). 배경은 `alpha=209`(=0.82×255), 글자·칩은 255 로 그린다.

**주의**: 그 창에 `-alpha`(=`SetLayeredWindowAttributes`)를 한 번이라도 걸면 창이 그 방식에
고정되어 `UpdateLayeredWindow` 가 **조용히 무시된다**. 확진: `GetLayeredWindowAttributes` 가
참이면 그 창은 못 쓴다.

`band_cells` 가 만든 칸 목록을 그대로 받아 순서대로 가로로 눕힌다 — 예전 `pack(side="left")`
와 같은 규칙이고 여백(`pad`)도 그 값 그대로다. 돌려주는 것은 폭과 **적중 사각형**
(관통 예외로 둘 자리: `⋮⋮` · `▾` · 「일괄 수령」).
"""

BAND_H = 30          # 디자인 px (실제 픽셀은 배율을 곱한다)
R_BAND = 15
BG_ALPHA = 209       # rgba(9,12,17,.82) 의 알파 (0.82×255)
BORDER_W = 1

# GDI+ 는 글꼴 **가족 이름**을 직접 받는다 (tkinter 처럼 목록을 뒤질 필요가 없다).
# **영문 이름이 안전하다** — 한글 이름(「맑은 고딕」)은 시스템 언어를 탄다.
UI_FAMILY = "Malgun Gothic"
MONO_FAMILY = "Consolas"        # 한글 글리프가 없다 — 숫자·시간에만 쓴다

# 디자인 px 값. (크기, 굵게, 고정폭인가)
FONTS = {
    "ui": (12, False, False), "bold": (12, True, False), "badge": (10, True, False),
    "xs": (11, False, False), "boldxs": (11, True, False), "pill": (10, True, False),
    "mono": (12, False, True), "monob": (12, True, True), "cols": (10, False, True),
    "p": (11, False, False), "pbold": (11, True, False), "plabel": (10, False, False),
    "pmono": (11, False, True), "pmonob": (11, True, True),
}


# **구역을 통째로 누르면 판이 열린다** (그래서 밴드 끝에 따로 `▾` 를 두지 않는다).
# 큐 칸들 = 대기 목록 판, 가공 칸들 = 가공기 현황 판.
ZONE_KEYS = {"qdot": "panel", "queue": "panel", "qempty": "panel", "type": "panel",
             "name": "panel", "prog": "panel",
             # 연주 대기 「연주 끝나면 시작」 (overlay.band_cells · 큐 hold) — 누르면 대기 목록 판
             "holddot": "panel", "hold": "panel",
             "alterlbl": "apanel", "altertime": "apanel", "alterdone": "apanel",
             "alterdot": "apanel", "alteridle": "apanel",
             # 일간 리포트 한 줄 (큐가 멈췄을 때만 있다) = 펼침 판의 7일 표
             "daydot": "day", "dayline": "day", "dayfail": "day",
             # 「창에서 담기」(큐가 비었을 때만) — 누르면 앱 창을 앞으로 올리고 담기 서랍을 연다
             "hint": "hint"}


def needs_ui_font(text) -> bool:
    """고정폭 칸에 한글이 섞였나. Consolas 에는 한글이 없어 네모로 나온다."""
    return any(ord(ch) >= 0x2000 for ch in str(text or ""))


def font_of(key: str, text, sc: float):
    """(가족, 크기, 굵게). 고정폭 칸인데 한글이면 한글 글꼴로 바꾼다."""
    size, bold, mono = FONTS.get(key, FONTS["ui"])
    fam = MONO_FAMILY if (mono and not needs_ui_font(text)) else UI_FAMILY
    return fam, max(1, int(round(size * sc))), bold


def _key_of(c) -> str:
    kind = c.get("kind")
    if kind in ("badge",):
        return "badge"
    if kind == "button":
        return "pill"
    if kind in ("handle", "expand", "expand2"):
        return "xs"
    return c.get("font") or "ui"


# `⋮⋮` 는 **글리프로 그리지 않는다.** 맑은 고딕의 `⋮` 는 자간이 넓어 두 줄이 멀찍이 떨어져
# 보이고 세로 가운데도 안 맞는다.
# 점 여섯 개를 직접 찍으면 간격·정렬을 우리가 정한다. 값은 디자인 px.
# 칸 막대 — 3×14px, 사이 2. 색은 overlay.SLOT_STYLE.
SLOT_W, SLOT_H, SLOT_GAP = 3.0, 14.0, 2.0

HANDLE_DOT = 1.6     # 점 지름
HANDLE_COL = 3.0     # 두 줄 사이
HANDLE_ROW = 3.6     # 점 사이 (세로)


def handle_w(sc: float) -> float:
    return HANDLE_DOT * sc + HANDLE_COL * sc


def cell_width(surf, c, sc: float) -> float:
    """칸 하나가 차지하는 폭 (여백 제외)."""
    kind = c.get("kind")
    text = c.get("text") or ""
    if kind == "handle":
        return handle_w(sc)
    if kind == "sep":
        return max(1.0, round(BORDER_W * sc))
    if kind == "dot":
        return max(1.0, round(int(c.get("size") or 6) * sc))
    fam, size, bold = font_of(_key_of(c), text, sc)
    w = surf.measure(text, fam, size, bold)
    if kind == "badge":
        return w + 12.0 * sc
    if kind == "button":
        return w + 18.0 * sc
    return float(w)


def band_size(surf, cells, sc: float) -> tuple:
    """(폭, 높이) — 실제 픽셀."""
    pad = max(1.0, round(BORDER_W * sc))
    x = pad
    for c in cells:
        pl, pr = c.get("pad") or (0, 0)
        x += pl * sc + cell_width(surf, c, sc) + pr * sc
    return int(round(x + pad)), int(round(BAND_H * sc))


def paint(surf, cells, bg: str, border: str, sc: float, dashed: bool = False) -> list:
    """밴드를 그리고 **적중 사각형 목록**을 돌려준다: [(key, x, y, w, h)].

    배경만 반투명(`BG_ALPHA`)이고 나머지는 전부 불투명이다 — 그게 이 파일이 있는 이유다.
    모서리는 GDI+ 가 **안티에일리어싱**으로 그리므로 계단이 생기지 않는다."""
    w, h = surf.w, surf.h
    r = R_BAND * sc
    t = max(1.0, round(BORDER_W * sc))
    surf.clear()
    surf.round_rect(t / 2, t / 2, w - t, h - t, r, fill=bg, alpha=BG_ALPHA)
    if dashed:
        _dash_border(surf, t / 2, t / 2, w - t, h - t, r, border, t, sc)
    else:
        surf.round_rect(t / 2, t / 2, w - t, h - t, r, outline=border, width=t)

    mid = h / 2.0
    hits, x = [], max(1.0, round(BORDER_W * sc))
    zone = {}
    for c in cells:
        pl, pr = ((c.get("pad") or (0, 0))[0] * sc, (c.get("pad") or (0, 0))[1] * sc)
        x += pl
        cw = cell_width(surf, c, sc)
        kind = c.get("kind")
        text = c.get("text") or ""
        fg = c.get("fg") or "#e9ecf1"
        if kind == "sep":
            surf.rect(x, mid - 7 * sc, cw, 14 * sc, fg)
        elif kind == "dot":
            surf.ellipse(x, mid - cw / 2.0, cw, cw, fg)
        elif kind in ("badge", "button"):
            btn = kind == "button"
            fam, size, bold = font_of("pill" if btn else "badge", text, sc)
            bh = surf.text_h(fam, size, bold) + (8 if btn else 2) * sc
            rr = (11 if btn else 4) * sc
            if c.get("bg"):
                surf.round_rect(x, mid - bh / 2.0, cw, bh, rr, fill=c["bg"])
            if c.get("outline"):
                surf.round_rect(x, mid - bh / 2.0, cw, bh, rr, outline=c["outline"], width=t)
            surf.text(x + cw / 2.0 - surf.measure(text, fam, size, bold) / 2.0, mid, text, fam, size, fg, bold)
            if c.get("key") == "collect":
                hits.append((c["key"], x, mid - bh / 2.0, cw, bh))
        elif kind == "handle":
            d = HANDLE_DOT * sc
            for cx in (x, x + HANDLE_COL * sc):
                for k in (-1, 0, 1):
                    surf.ellipse(cx, mid + k * HANDLE_ROW * sc - d / 2.0, d, d, fg)
            hits.append((c["key"], x - 4 * sc, 0, cw + 8 * sc, h))
        elif kind == "expand":
            # `▾`↔`▴` 는 글리프마다 metric 과 잉크의 관계가 달라 **토글할 때 0.5px 튄다**
            # (실측). 칸 폭은 advance 그대로 두고 **잉크 가운데만** 칸 가운데에 맞춘다 —
            # 그래야 밴드 폭도 안 흔들린다. (`paint32.Surface.icon` 주석)
            fam, size, bold = font_of(_key_of(c), text, sc)
            surf.icon(x + cw / 2.0, mid, text, fam, size, fg, bold)
            hits.append((c["key"], x - 3 * sc, 0, cw + 6 * sc, h))
        else:
            fam, size, bold = font_of(_key_of(c), text, sc)
            surf.text(x, mid, text, fam, size, fg, bold)
        z = ZONE_KEYS.get(c.get("key"))
        if z:       # 같은 구역의 칸들을 하나의 사각형으로 합친다 (여백까지 포함)
            x0, x1 = zone.get(z, (x - pl, x + cw + pr))
            zone[z] = (min(x0, x - pl), max(x1, x + cw + pr))
        x += cw + pr
    for z, (x0, x1) in zone.items():
        hits.append((z, x0, 0, x1 - x0, h))
    return hits


def paint_cells(surf, cells, x0: float, mid: float, sc: float) -> float:
    """칸 목록을 한 줄로 그린다 (펼친 판의 머리줄이 밴드를 그대로 다시 쓴다).
    돌려주는 것: 그린 뒤의 x."""
    x = x0
    for c in cells:
        pl, pr = ((c.get("pad") or (0, 0))[0] * sc, (c.get("pad") or (0, 0))[1] * sc)
        x += pl
        cw = cell_width(surf, c, sc)
        kind = c.get("kind")
        text = c.get("text") or ""
        fg = c.get("fg") or "#e9ecf1"
        if kind == "sep":
            surf.rect(x, mid - 7 * sc, cw, 14 * sc, fg)
        elif kind == "dot":
            surf.ellipse(x, mid - cw / 2.0, cw, cw, fg)
        elif kind in ("badge", "button"):
            btn = kind == "button"
            fam, size, bold = font_of("pill" if btn else "badge", text, sc)
            bh = surf.text_h(fam, size, bold) + (8 if btn else 2) * sc
            if c.get("bg"):
                surf.round_rect(x, mid - bh / 2.0, cw, bh, (11 if btn else 4) * sc, fill=c["bg"])
            surf.text(x + cw / 2.0 - surf.measure(text, fam, size, bold) / 2.0, mid,
                      text, fam, size, fg, bold)
        elif kind == "handle":
            d = HANDLE_DOT * sc
            for cx in (x, x + HANDLE_COL * sc):
                for k in (-1, 0, 1):
                    surf.ellipse(cx, mid + k * HANDLE_ROW * sc - d / 2.0, d, d, fg)
        elif kind == "expand":      # 위 `paint` 와 같은 이유 — 토글해도 안 튀게
            fam, size, bold = font_of(_key_of(c), text, sc)
            surf.icon(x + cw / 2.0, mid, text, fam, size, fg, bold)
        else:
            fam, size, bold = font_of(_key_of(c), text, sc)
            surf.text(x, mid, text, fam, size, fg, bold)
        x += cw + pr
    return x


def _dash_border(surf, x, y, w, h, r, color, t, sc) -> None:
    """점선 둥근 테두리 (칸이 전부 비어 있을 때).

    `paint32` 에 점선이 없어 **짧은 선분으로 끊어 그린다.** 곧은 변만 끊고 모서리는 이어 둔다 —
    30px 짜리 밴드에서 모서리까지 끊으면 점이 흩어져 테두리로 안 보인다."""
    import math
    d = max(2.0, 3.0 * sc)
    for x0, y0, x1, y1 in ((x + r, y, x + w - r, y), (x + r, y + h, x + w - r, y + h),
                           (x, y + r, x, y + h - r), (x + w, y + r, x + w, y + h - r)):
        span = math.hypot(x1 - x0, y1 - y0)
        if span <= 0:
            continue
        n = max(1, int(span // (d * 2)))
        for i in range(n):
            t0, t1 = (i * 2 * d) / span, min(1.0, (i * 2 * d + d) / span)
            surf.line(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0,
                      x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1, color, t)
    for cx, cy, a0 in ((x + w - r, y + r, -90.0), (x + w - r, y + h - r, 0.0),
                       (x + r, y + h - r, 90.0), (x + r, y + r, 180.0)):
        pts = [(cx + r * math.cos(math.radians(a0 + 90.0 * k / 8.0)),
                cy + r * math.sin(math.radians(a0 + 90.0 * k / 8.0))) for k in range(9)]
        for i in range(8):
            surf.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color, t)
