# -*- coding: utf-8 -*-
"""펼친 판 둘을 **그림 한 장씩으로 그린다** — 밴드·토스트와 같은 방식.

위젯으로 쌓으면 둥근 모서리가 계단으로 남고 테두리가 곡선을 못 따라간다 (밴드에서 겪은 그대로).
`paint32` 로 그리면 GDI+ 가 안티에일리어싱해 주고, 배경만 반투명하게 둘 수 있다.

돌려주는 것은 `(폭, 높이, 적중 사각형)`. 적중 사각형은 `[(열쇠, x, y, w, h)]` 이고 판 왼쪽 위
기준이다 — 위젯이 없으므로 누르기·커서·관통 예외를 전부 이 목록으로 판정한다.
"""

import bandpaint

UI = bandpaint.UI_FAMILY
MONO = bandpaint.MONO_FAMILY
# 자물쇠·톱니는 **이모지가 아니라 아이콘 글꼴**로 그린다 — 맑은 고딕에 `🔒`·`⚙` 글리프가 없어
# 네모로 나온다 (실측). Windows 10 부터 있는 글꼴이고, 없으면 paint32 가 알아서 떨어진다.
ICON_FAMILY = "Segoe MDL2 Assets"
ICON_LOCK, ICON_UNLOCK, ICON_GEAR = "", "", ""


# 머리줄 위 여백. 0 으로 두면 글자가 테두리에 붙는다 — 아래 가로선까지가 머리줄이다.
HEAD_TOP = 6.0

# 시설 알약 치수 — 아이콘 14px · 이름 23px 고정폭 · 상태 점 5px · 시간 43px
# 우측정렬(000:00 기준) · 칸 막대 3×14px 최대 7개. 막대 치수는 `bandpaint.SLOT_*`. 디자인 px.
FAC_ICON = 14.0
FAC_NAME_W = 23.0
FAC_DOT = 5.0
FAC_TIME_W = 43.0
FAC_SLOT_MAX = 7
FAC_BADGE_D = 15.0       # 우상단 주황 배지 지름

# 컨트롤 줄 첫 알약 `⏎` — 폭 30 · 높이 26 · 곡률 8 (다른 알약은 13 = 완전한 알약).
FRONT_W = 30.0
FRONT_R = 8.0
FRONT_GLYPH = "⏎"   # ⏎ — 맑은 고딕에 있다 (ink 11×10, 빠진 글리프 네모 7×10 과 다르다)

# 「전체 수령」이 0건일 때의 흐린 색 — 완료 0건이면 흐리게(비활성) 그린다.
# 팔레트(`overlay.COLORS`)에 없으면 이 값으로 떨어진다: 초록 버튼을 판 배경 위에 .4 로 얹은 값.
COLLECT_DIM_BG = "#1b3a28"
COLLECT_DIM_FG = "#5a8a68"

# 「스마트 관통 중」 활성 색 — 골드: rgba(226,184,102,.12) 배경 · .45 테두리.
# 판 배경(#090c11) 위에 얹어 환산한 값. 팔레트에 `goldbg`·`goldline` 이 없으면 이 값.
GOLD_BG = "#232119"
GOLD_LINE = "#6b5937"


def _f(key, text, sc):
    return bandpaint.font_of(key, text, sc)


def pill(surf, x, y, text, fg, bg, border, sc, h=26.0, pad=11.0, radius=13.0, bold=True,
         key=None, icon="", width=None) -> tuple:
    """컨트롤 줄의 알약 하나. 돌려주는 것: (폭, 적중 사각형 또는 None).
    `icon` 은 아이콘 글꼴로 앞에 붙는 글리프 (자물쇠·톱니).
    `width` 를 주면 글자 폭과 상관없이 그 폭(디자인 px)으로 굳힌다 — `⏎`(30×26)."""
    fam, size, bd = _f("pill", text, sc)
    isz = max(1, int(round(13 * sc)))
    # **자리는 advance, 가운데는 잉크.** 둘을 섞으면 안 된다:
    #  · 자리를 잉크 폭으로 잡으면 알약이 디자인보다 좁아진다 (실측 −7~−12px). 디자인은 CSS
    #    기준이고 CSS 는 글자를 advance 로 눕힌다.
    #  · 가운데를 metric 으로 잡으면 아이콘이 뜬다 — `Segoe MDL2 Assets` 에는 `text_h` 가
    #    재는 「가Ag」가 없어 대체 글꼴 값이 나온다. 톱니가 1.2px 위로 뜬다.
    # 아이콘 뒤 여백은 **글자가 있을 때만** 준다 (없으면 그만큼 왼쪽으로 쏠린다).
    iadv = float(surf.measure(icon, ICON_FAMILY, isz, False)) if icon else 0.0
    iw = (iadv + (5 * sc if text else 0)) if icon else 0.0
    tw = surf.measure(text, fam, size, bd) if text else 0.0
    w = width * sc if width else max(h * sc, iw + tw + pad * 2 * sc)
    hh = h * sc
    if bg:
        surf.round_rect(x, y, w, hh, radius * sc, fill=bg)
    if border:
        surf.round_rect(x, y, w, hh, radius * sc, outline=border, width=max(1.0, sc))
    cx = x + w / 2.0 - (iw + tw) / 2.0
    if icon:
        surf.icon(cx + iadv / 2.0, y + hh / 2.0, icon, ICON_FAMILY, isz, fg)
        cx += iw
    if text:
        surf.text(cx, y + hh / 2.0, text, fam, size, fg, bd)
    return w, ((key, x, y, w, hh) if key else None)


def fac_chip(surf, f, x, y, sc, C, svg_polylines, slot_style) -> tuple:
    """시설 알약 하나 (가공 펼침 판의 3열 격자). 돌려주는 것: (폭, 높이)."""
    pad = 7 * sc
    # 아이콘 14 · 이름 23 고정폭 · 상태 점 5 · 시간 43 우측정렬(000:00 기준).
    icon, name_w, dot, gap = FAC_ICON * sc, FAC_NAME_W * sc, FAC_DOT * sc, 5 * sc
    sw, sgap, sh = bandpaint.SLOT_W * sc, bandpaint.SLOT_GAP * sc, bandpaint.SLOT_H * sc
    slots_w = FAC_SLOT_MAX * sw + (FAC_SLOT_MAX - 1) * sgap      # 막대 최대 7개
    time_w = FAC_TIME_W * sc
    w = pad * 2 + icon + gap + name_w + gap + dot + 6 * sc + time_w + sc + slots_w
    h = 20 * sc
    surf.round_rect(x, y, w, h, 7 * sc, fill=f["bg"])
    surf.round_rect(x, y, w, h, 7 * sc, outline=f["border"], width=max(1.0, sc))
    mid = y + h / 2.0

    cx = x + pad
    for seg in svg_polylines(f["icon"], icon):
        for i in range(len(seg) - 1):
            surf.line(cx + seg[i][0], mid - icon / 2.0 + seg[i][1],
                      cx + seg[i + 1][0], mid - icon / 2.0 + seg[i + 1][1],
                      f["iconColor"], max(1.0, sc))
    cx += icon + gap
    fam, size, bd = _f("badge", f["key"], sc)
    surf.text(cx, mid, f["key"], fam, size, f["nameColor"], bd)
    cx += name_w + gap
    surf.ellipse(cx, mid - dot / 2.0, dot, dot, f["dotColor"])

    sx = x + w - pad - slots_w
    fam, size, bd = _f("pmonob", f["time"], sc)
    surf.text(sx - 2 * sc, mid, f["time"], fam, size, f["timeColor"], bd, anchor="e")
    for sl in f["slots"]:
        track, fill = slot_style.get(sl["kind"], slot_style["none"])
        surf.rect(sx, mid - sh / 2.0, sw, sh, track)
        if fill:
            fh = sh if sl["kind"] == "done" else 2 * sc
            surf.rect(sx, mid + sh / 2.0 - fh, sw, fh, fill)
        sx += sw + sgap
    if f["badge"]:
        r = FAC_BADGE_D / 2.0 * sc      # 우상단 주황 배지 지름 15
        bx = x + w - 4 * sc
        surf.ellipse(bx - r, y - r + 3 * sc, r * 2, r * 2, C["warn"])
        fam, size, bd = _f("badge", f["badge"], sc)
        surf.text(bx - surf.measure(f["badge"], fam, size, bd) / 2.0, y + 3 * sc,
                  f["badge"], fam, size, C["facbadgefg"], bd)
    return w, h


def controls(surf, x, y, sc, C, locked: bool, through: bool) -> tuple:
    """컨트롤 줄 (펼친 판 맨 아래줄). 돌려주는 것: (폭, 높이, 적중 사각형들)."""
    hits, gap = [], 6 * sc
    cx = x
    # 첫 알약 `⏎` = 앱 창 앞으로 (`overlay.PANEL_ACTS["front"]`). 30×26 · 곡률 8.
    w, hit = pill(surf, cx, y, FRONT_GLYPH, C["fg2"], "", C["sep"], sc, pad=0,
                  radius=FRONT_R, key="front", width=FRONT_W)
    hits.append(hit)
    cx += w + gap
    # 활성은 골드 — 글자·바탕·테두리 셋 다 골드 (초록 `throughbg`·`oktext` 가 아니다).
    gold_fg = C.get("gold", "#e2b866")
    gold_bg = C.get("goldbg", GOLD_BG)
    gold_line = C.get("goldline", GOLD_LINE)
    items = [
        ("lock", f"위치 {'고정됨' if locked else '풀림'}", C["fg2"], "", C["sep"],
         ICON_LOCK if locked else ICON_UNLOCK),
        ("reset", "↻ 위치 초기화", C["fg2"], "", C["sep"], ""),
        ("through", f"↗ 스마트 관통 {'중' if through else '꺼짐'}",
         gold_fg if through else C["fg2"], gold_bg if through else "",
         gold_line if through else C["sep"], ""),
    ]
    for key, text, fg, bg, bd, ic in items:
        w, hit = pill(surf, cx, y, text, fg, bg, bd, sc, key=key, icon=ic)
        hits.append(hit)
        cx += w + gap
    w, hit = pill(surf, cx, y, "", C["fg2"], "", C["sep"], sc, pad=0, key="gear", icon=ICON_GEAR)
    hits.append(hit)
    return cx + w - x, 26 * sc, hits


def paint_apanel(surf, data, sc, C, svg_polylines, slot_style, locked, through) -> tuple:
    """가공 펼침 판 — 머리 · 시설 격자 3열 · 전체 수령 · 컨트롤 줄."""
    pad = 8 * sc
    facs = data["facs"]
    # 먼저 크기를 잰다 (알약 하나를 시험 삼아 그려 폭을 얻는다)
    surf.resize(1200, 400)
    surf.clear()
    fw, fh = (fac_chip(surf, facs[0], -5000, -5000, sc, C, svg_polylines, slot_style) if facs else (120 * sc, 20 * sc))
    cols = min(3, max(1, len(facs))) if facs else 1
    rows = (len(facs) + 2) // 3 if facs else 1
    grid_w = cols * fw + (cols - 1) * 4 * sc
    cw, ch, _ = controls(surf, -5000, -5000, sc, C, locked, through)
    top = HEAD_TOP * sc
    head_h = 28 * sc
    bar_h = 22 * sc + 3 * sc        # 「전체 수령」은 0건이어도 자리를 지킨다 (흐리게)
    w = int(round(max(grid_w, cw) + pad * 2))
    h = int(round(top + head_h + 1 + pad + rows * fh + (rows - 1) * 3 * sc + bar_h
                  + pad + 1 + pad + 26 * sc + pad))
    surf.resize(w, h)
    surf.clear()
    r = 14 * sc
    surf.round_rect(0.5, 0.5, w - 1, h - 1, r, fill=C["bg"], alpha=217)
    surf.round_rect(0.5, 0.5, w - 1, h - 1, r, outline=data.get("frame") or C["warnline2"],
                    width=max(1.0, sc))
    hits = []

    # 머리줄
    fam, size, bd = _f("boldxs", "가공기 현황", sc)
    y = top + head_h / 2.0
    tw = surf.text(10 * sc, y, "가공기 현황", fam, size, data.get("titleColor") or C["gold"], True)
    cx = 10 * sc + tw + 7 * sc
    if data["collect"]:
        pw, hit = pill(surf, cx, y - 9 * sc, f"수령 {data['collect']}건", C["collectfg"],
                       C["collectbg"], "", sc, h=18, pad=8, radius=9, key="collect")
        hits.append(hit)
    elif data.get("note"):
        fam2, s2, b2 = _f("xs", data["note"], sc)
        surf.text(cx, y, data["note"], fam2, s2, C["faint"])
    fam2, s2, b2 = _f("xs", "▴", sc)
    surf.text(w - 10 * sc, y, "▴", fam2, s2, C["faint"], anchor="e")
    hits.append(("aexpand", w - 24 * sc, 0, 24 * sc, top + head_h))
    surf.rect(0, top + head_h, w, 1, C["sepfaint"])

    # 시설 격자
    gy = top + head_h + 1 + pad
    for i, f in enumerate(facs):
        fx = pad + (i % 3) * (fw + 4 * sc)
        fy = gy + (i // 3) * (fh + 3 * sc)
        fac_chip(surf, f, fx, fy, sc, C, svg_polylines, slot_style)
    gy += rows * fh + (rows - 1) * 3 * sc
    if not facs:
        fam2, s2, b2 = _f("xs", "등록된 가공이 없습니다", sc)
        surf.text(pad, gy - fh / 2.0, "등록된 가공이 없습니다", fam2, s2, C["faint"])

    # 전체 수령 — 그리드 전폭, 완료 0건이면 흐리게(비활성).
    # 0건이어도 자리는 남기되 흐리게 그리고 **적중 사각형을 내놓지 않는다** —
    # 눌러도 아무 일도 없어야 하고(수령 요청이 나가면 안 된다) 손 모양 커서도 뜨면 안 된다.
    n = int(data["collect"] or 0)
    by = gy + 3 * sc
    bh = 22 * sc
    bg = C["collectbg"] if n else C.get("collectbgdim", COLLECT_DIM_BG)
    fg = C["collectfg"] if n else C.get("collectfgdim", COLLECT_DIM_FG)
    surf.round_rect(pad, by, w - pad * 2, bh, 6 * sc, fill=bg)
    txt = f"전체 수령 {n}"
    fam2, s2, b2 = _f("pill", txt, sc)
    surf.text(w / 2.0 - surf.measure(txt, fam2, s2, b2) / 2.0, by + bh / 2.0,
              txt, fam2, s2, fg, b2)
    if n:
        hits.append(("collect", pad, by, w - pad * 2, bh))
    gy = by + bh

    # 컨트롤 줄
    ly = gy + pad
    surf.rect(0, ly, w, 1, C["sepfaint"])
    ly += pad
    _cw, _ch, chits = controls(surf, pad, ly, sc, C, locked, through)
    hits += chits
    return w, h, [x for x in hits if x]


def fit_text(surf, text, fam, size, bold, max_px) -> str:
    """폭에 맞게 끝을 「…」 로 줄인다 (7일 표의 산출 칸)."""
    t = str(text or "")
    if surf.measure(t, fam, size, bold) <= max_px:
        return t
    while len(t) > 1 and surf.measure(t + "…", fam, size, bold) > max_px:
        t = t[:-1]
    return t.rstrip(" ·") + "…"


def paint_panel(surf, data, sc, C, band_paint_cells, locked, through) -> tuple:
    """펼침 판 (대기 목록과 가공) — 머리(밴드 반복) · 대기 · 가공 · 컨트롤 줄."""
    w = int(round(420 * sc))
    pad = 12 * sc
    top = HEAD_TOP * sc
    head_h = 30 * sc
    line = 18 * sc
    days = data.get("days") or []      # 밴드의 오늘 한 줄을 눌러 열었을 때만
    rows = len(data["waits"]) + (1 if not data["waits"] else 0) + len(data["works"]) + len(days)
    labels = 1 + (1 if data["works"] else 0) + (1 if days else 0)
    h = int(round(top + head_h + 1 + 8 * sc + labels * 17 * sc + rows * line
                  + pad + 1 + pad + 26 * sc + pad))
    surf.resize(w, h)
    surf.clear()
    r = 14 * sc
    surf.round_rect(0.5, 0.5, w - 1, h - 1, r, fill=C["bg"], alpha=217)
    surf.round_rect(0.5, 0.5, w - 1, h - 1, r, outline=C["panelline"], width=max(1.0, sc))
    hits = []

    # 머리줄 — 밴드 칸을 그대로 다시
    band_paint_cells(surf, data.get("head") or [], 10 * sc, top + head_h / 2.0, sc)
    fam, size, bd = _f("xs", "▴", sc)
    surf.text(w - 10 * sc, top + head_h / 2.0, "▴", fam, size, C["faint"], anchor="e")
    hits.append(("expand", w - 24 * sc, 0, 24 * sc, top + head_h))
    surf.rect(0, top + head_h, w, 1, C["sepfaint"])

    y = top + head_h + 1 + 8 * sc

    def label(text):
        nonlocal y
        fam2, s2, b2 = _f("plabel", text, sc)
        surf.text(pad, y + 8 * sc, text, fam2, s2, C["label"])
        y += 17 * sc

    wait_top = y
    label(f"대기 {data['waitTotal']}")
    if not data["waits"]:
        fam2, s2, b2 = _f("p", "대기 중인 항목이 없습니다", sc)
        surf.text(pad, y + line / 2.0, "대기 중인 항목이 없습니다", fam2, s2, C["dim"])
        y += line
    for rw in data["waits"]:
        dim = rw["dim"]
        fam2, s2, b2 = _f("pmono", str(rw["n"]), sc)
        surf.text(pad, y + line / 2.0, str(rw["n"]), fam2, s2, C["dim2"] if dim else C["faint"])
        fam3, s3, b3 = _f("pmono", rw["name"], sc)
        surf.text(pad + 16 * sc, y + line / 2.0, rw["name"], fam3, s3, C["dim2"] if dim else C["fg2"])
        fam4, s4, b4 = _f("pmono", rw["detail"], sc)
        surf.text(w - pad, y + line / 2.0, rw["detail"], fam4, s4,
                  C["dim2"] if dim else C["sub"], anchor="e")
        y += line
    # 대기 구역을 누르면 앱 창의 큐 탭이 열린다
    hits.append(("queue", 0, wait_top, w, y - wait_top))
    if data["works"]:
        label(f"가공 {data['alterDone']}/{data['alterTotal']}")
        for wk in data["works"]:
            name = f"{wk['name']} ×{wk['n']}" if wk["n"] > 1 else wk["name"]
            fam2, s2, b2 = _f("pmono", name, sc)
            surf.text(pad, y + line / 2.0, name, fam2, s2, C["fg2"])
            fam3, s3, b3 = _f("pmonob", wk["text"], sc)
            surf.text(w - pad, y + line / 2.0, wk["text"], fam3, s3, wk["fg"], True, anchor="e")
            y += line
    if days:
        # 7일 표: 날짜 | 날개 | 산출 셋 (「외 n」). 최근 날이 위. 표를 누르면 접힌다.
        day_top = y
        label("최근 7일 · 날개 · 산출")
        # 날짜 | 날개(오른쪽 맞춤) | 실패 N(붉은색, 있는 날이 하나라도 있으면 칸을 연다) | 산출
        has_fail = any(int(d.get("failWings") or 0) for d in days)
        wx = pad + 82 * sc                                # 날개 오른쪽 끝
        fx = wx + 8 * sc                                  # 실패 칸 시작
        ox = (fx + 52 * sc) if has_fail else (wx + 10 * sc)   # 산출 시작
        dx = pad
        for i, d in enumerate(days):
            fg = C["fg2"] if i == 0 else C["sub"]
            fam2, s2, b2 = _f("pmono", d["d"], sc)
            surf.text(dx, y + line / 2.0, d["d"], fam2, s2, C["faint"])
            fw = int(d.get("failWings") or 0)
            wt = f"{d['wings']:,}" if d["wings"] else "—"
            fam3, s3, b3 = _f("pmonob", wt, sc)
            surf.text(wx, y + line / 2.0, wt, fam3, s3, C["gold"] if d["wings"] else C["dim"], True, anchor="e")
            if fw:
                fam5, s5, b5 = _f("pmono", f"실패 {fw:,}", sc)
                surf.text(fx, y + line / 2.0, f"실패 {fw:,}", fam5, s5, C["bad"])
            fam4, s4, b4 = _f("p", d["out"], sc)
            surf.text(ox, y + line / 2.0, fit_text(surf, d["out"], fam4, s4, b4, w - pad - ox),
                      fam4, s4, fg if d["out"] != "—" else C["dim"])
            y += line
        hits.append(("day", 0, day_top, w, y - day_top))

    ly = y + pad
    surf.rect(0, ly, w, 1, C["sepfaint"])
    ly += pad
    _cw, _ch, chits = controls(surf, pad, ly, sc, C, locked, through)
    hits += chits
    return w, h, [x for x in hits if x]
