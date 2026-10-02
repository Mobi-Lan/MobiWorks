# -*- coding: utf-8 -*-
"""화면 상태 — **판단은 여기서 하고, 그리는 일만 화면이 한다.**

## 왜 옮기는가

모바일로 옮기기 위해서다. 폰은 「세 번째 화면」일 뿐이어야 한다 — PC 창·오버레이·폰이
각자 판단하면 **화면마다 답이 갈린다**. 모비폴리오가 실제로 그 부류를 겪었다:
오버레이에서 「전체 반복」을 켜도 미니 창은 모르고 목록 끝에서 멈췄다. 대기열을 서버가
**하나만** 들게 바꾸니 그 부류가 통째로 사라졌다.

**HTML 은 서버가 만들지 않는다.** 조각 HTML 을 매 틱 보내면
터널 너머로 JSON 보다 훨씬 무겁고 PC 판까지 느려진다. 여기서 만드는 것은 **글자와 숫자**,
즉 「무엇을 적을지」다. 「어떻게 적을지」는 화면이 한다.

## 여기 있는 것은 전부 화면에서 옮겨 온 것이다

`ui/js/board.js` 의 `progOf`·`subOf`·`txOf`·`itemCalls`·`itemWingCalls`·`nameOf`·`lastStart`,
`ui/js/works.js` 의 시설별 묶기. **글자를 한 자도 바꾸지 않고 옮겼다** — 바꾸면 옮긴 것이
맞는지 확인할 길이 없어진다. 고치는 것은 옮긴 뒤에 따로 한다.

## sig

diff 를 만들지 말고 **서명을 비교**한다. 화면은 `sig` 가 같으면
다시 그리지 않는다. diff 보다 훨씬 싸고 안 틀린다.
"""
from __future__ import annotations

import time

import workqueue as wq

# ── 화면에 적는 말 (ui/js/board.js 의 표를 그대로 옮긴 것) ──
QTYPE_KO = {"craft": "제작", "alter": "가공", "gather": "채집", "collect": "수령", "group": "그룹",
            "play": "연주", "notify": "알림"}
QTYPE_CLS = {"gather": "gather", "craft": "run", "alter": "warn", "collect": "collect", "group": "gold",
             "play": "gold", "notify": "notify"}   # 연주 = 폴리오 금색 ♪ · 알림 = 파랑
FREE_TYPES = ("play", "notify")   # 호출 0 · 날개 0 (workqueue.FREE_CARD_TYPES)
PLAY_MODE_KO = {"song": "이 곡만", "list": "재생목록 전체 (옛 카드)", "resume": "지금 대기열 이어서"}   # 재생목록은 이제 그룹
QST_KO = {"pending": "대기", "running": "실행 중", "waiting": "가공 대기",
          "done": "완료", "stopped": "정지", "error": "오류"}
COLS = (("wait", "대기"), ("run", "작업 중"), ("done", "완료"), ("fail", "실패"))

# 오류코드별 안내 한 줄. 코드는 원문 그대로 보여 주고, 여기 없는 코드는 안내 없이 코드·메시지만.
ERR_HINT = {
    "tool_broken": "도구를 고치거나 새로 장착한 뒤 재시도",
    "tool_not_ok": "이 채집에 맞는 도구가 없습니다",
    "tool_missing": "채집 도구가 없습니다",
    "overweight": "가방을 비우거나 창고에 넣은 뒤 재시도",
    "overweight_soon": "가방이 거의 찼습니다",
    "not_enough_ingredient": "부족한 재료를 앞에 담거나 가방으로 옮기세요",
    "insufficient_transfer_cost": "재료를 직접 가방으로 옮기면 됩니다",
    "insufficient_living_skill_level": "지금은 만들 수 없는 레시피입니다",
    "insufficient_facility_level": "지금은 만들 수 없는 레시피입니다",
    "insufficient_decor_score": "지금은 만들 수 없는 레시피입니다",
    "invalid_count": "한 번에 만들 수 있는 횟수를 넘었습니다 — 나눠서 담으세요",
    "requires_user_interaction": "게임 안에서 직접 등록해야 하는 가공입니다",
    "not_completed_yet": "아직 완료된 가공이 없습니다",
    "no_completed_work": "아직 완료된 가공이 없습니다",
    "no_completed_work_at_facility": "이 시설에 완료된 가공이 없습니다",
    "blocked": "게임 화면의 창을 닫은 뒤 「시작」",
    "disconnected": "게임과 연결이 끊겼습니다 — 게임을 확인한 뒤 「▶ 시작」",
    "game_off": "게임이 꺼져 있습니다",
    "timeout": "응답이 없었습니다 — 게임 상태를 확인하세요",
    "loading": "게임이 로딩 중이었습니다 — 게임에 들어간 뒤 「▶ 시작」",
    "stopped_by_user": "게임에서 정지했습니다 — 이어하려면 「▶ 시작」",
    "max_passes": "반복 상한에 닿았습니다. 설정에서 올리거나 개수를 나누세요",
    "not_enough_currency": "정령의 날개가 부족합니다",
    "required_consumable_missing": "채집에 필요한 소모품이 없습니다",
    "no_route": "채집지까지 갈 수 없습니다",
    "not_in_field": "필드가 아닙니다",
    "facility_not_found": "시설을 찾지 못했습니다",
    "cost_payment_failed": "비용 지불에 실패했습니다",
    "canceled": "다른 명령이 끼어들어 취소됐습니다",
    "not_gatherable": "채집 목록에 없는 항목입니다",
    "not_in_cache": "레시피가 캐시에 없습니다 — 「갱신」 뒤 다시",
    "cli_disconnected": "CLI 연결이 없습니다",
    "cli_not_found": "CLI 를 찾지 못했습니다",
    "cli_disabled": "CLI 실행이 차단된 실행입니다",
}


def _n(v, default: int = 0) -> int:
    """`wq._n` 과 같은 규칙 — NaN·Infinity 는 default (int() 의 OverflowError 로 화면 한 벌이 통째로 서지 않게)."""
    return wq._n(v, default)


def _fmt(n) -> str:
    """1,234 — 화면의 `fmtN` 과 같은 규칙 (숫자만 쉼표, 그 밖은 그대로)."""
    return "{:,}".format(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else str(n or "")


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t)) if t else ""


def _mmss(sec) -> str:
    """00:00 — 화면의 `fmtT` 와 같은 규칙 (음수는 0, 초 단위 내림)."""
    s = max(0, int(sec or 0))
    return "{:02d}:{:02d}".format(s // 60, s % 60)


def col_of(c: dict) -> str:
    """서버가 실어 준 column 을 믿는다. 없을 때의 되돌림도 **같은 함수**(wq.column_of)다 —
    그룹 규칙(자식 실패는 그룹을 실패로 만들지 않는다)을 여기서 다시 적으면 두 판정이 갈린다."""
    k = c.get("column")
    return k if k in dict(COLS) else wq.column_of(c)


# 좁은 폭: 작업 중은 위에 고정, 나머지 셋이 탭. ui/js/board.js 의 TAB_COLS 와 짝.
TAB_COLS = tuple((k, ko) for k, ko in COLS if k != "run")


def narrow_tabs(counts: dict, chosen: str = "wait") -> list:
    """3등분 탭 한 줄. **실패가 생기면 실패 탭이 빨강**(`bad`) — 숫자만 바뀌면 눈에 안 띈다."""
    return [{"k": k, "n": ko, "c": _n(counts.get(k)), "on": k == chosen,
             "bad": k == "fail" and _n(counts.get(k)) > 0} for k, ko in TAB_COLS]


def chain_stop(state: dict) -> dict | None:
    """체인 정지(치명) 한 벌 → `{"error", "message"[, "kind"]}` | None. **ui/js/board.js 의 `chainStop` 과 짝.**

    서버는 `stopReason` 을 **글자**로 준다 — `"user"` · `"onError"` · `"fatal:<code>"`.
    화면과 이 파일은 dict 일 때만 배너로 봤다 → 실제 데이터에서는 **배너·실패 탭 자동 선택이 한 번도
    안 떴고, 실행 줄에 「fatal:blocked」 원문이 찍혔다** (검사 자료만 dict 였다).
    코드는 글자에서, 메시지(blocked 의 kind 포함)는 같은 코드의 `lastError` 에서 가져온다.
    dict 로 오면 그대로 받는다 (옛 자료·앞으로 서버가 dict 를 주게 되더라도)."""
    sr = state.get("stopReason")
    if isinstance(sr, dict):
        return sr if sr.get("error") else None
    if not isinstance(sr, str) or not sr.startswith("fatal:"):
        return None
    code = sr[len("fatal:"):]
    le = state.get("lastError") if isinstance(state.get("lastError"), dict) else {}
    same = (le.get("error") or le.get("code")) == code
    out = {"error": code, "message": (le.get("message") or "") if same else ""}
    if same and le.get("kind"):
        out["kind"] = le["kind"]
    return out


def chain_banner(state: dict) -> dict | None:
    """체인 정지 배너 — 작업 중 슬롯 **위**. 치명 정지(`chain_stop`)일 때만.
    사용자 정지·「오류 시 정지」는 배너가 아니다 — 실행 줄의 상태 글자로 충분하다."""
    sr = chain_stop(state)
    if sr is None:
        return None
    code = sr.get("error") or ""
    kind = kind_ko(blocked_kind(sr)) if code == "blocked" else ""
    return {"code": code + (f" ({kind})" if kind else ""), "title": "체인 정지",
            "msg": err_hint(sr, "▶ 시작") or sr.get("message") or ""}


def blocked_kind(o: dict) -> str:
    if o.get("kind"):
        return str(o["kind"])
    msg = str(o.get("message") or "")
    i = msg.find("kind=")
    if i < 0:
        return ""
    tail = msg[i + 5:]
    out = []
    for ch in tail:
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        else:
            break
    return "".join(out)


# blocked 의 kind(게임이 준 영어 낱말) → 한글. **ui/js/board.js 의 BLOCK_KIND_KO 와 짝.**
# 화면에 영어 원문(「dialog」)이 그대로 찍혔다. 모르는 kind 는 원문 그대로 둔다 — 지어내지 않는다.
BLOCK_KIND_KO = {"dialog": "대화", "popup": "팝업 창", "combat": "전투", "dungeon": "던전", "battlefield": "전장",
           "scenario": "시나리오", "tutorial": "튜토리얼", "reviving": "부활 대기", "minigame": "미니게임",
           "cutscene": "컷신", "housing": "하우징 편집", "fishing": "낚시", "loading": "로딩", "dead": "사망"}


def kind_ko(kind: str) -> str:
    return BLOCK_KIND_KO.get(kind, kind) if kind else ""


def blocked_hint(kind: str, act: str = "다시 시작") -> str:
    if kind == "dead":
        return ("게임이 캐릭터를 사망/부활 대기 상태로 보고했습니다. 부활 선택 창이 있으면 닫고, "
                f"캐릭터가 멀쩡한데도 반복되면 캐릭터를 한 번 움직이거나 채널 이동 뒤 「{act}」. "
                "그래도 같으면 게임 AI 커넥터 상태 오류 — 게임 재접속.")
    if kind:
        return f"게임 화면의 「{kind_ko(kind)}」 — 닫거나 끝낸 뒤 「{act}」"
    return f"게임 화면에 열린 창을 닫은 뒤 「{act}」"


def err_hint(o: dict, act: str = "대기로") -> str:
    """오류 코드 → 안내. **게임 메시지가 이미 같은 말을 하고 있으면 붙이지 않는다.**"""
    code = o.get("error") or ""
    h = blocked_hint(blocked_kind(o), act) if code == "blocked" else ERR_HINT.get(code, "")
    if h and h in str(o.get("message") or ""):
        return ""
    return h


def last_start(events: list, iid: str) -> float:
    """그 항목의 마지막 「시작」 회신 시각 (경과 시간·등록 시각 표시용)."""
    for e in reversed(events or []):
        if e.get("id") == iid and e.get("kind") == "start":
            return float(e.get("t") or 0)
    return 0.0


def facility_of(snap: dict, name: str) -> str:
    for w in (snap.get("works") or []):
        if isinstance(w, dict) and w.get("name") == name:
            return w.get("facility") or ""
    return ""


def name_of(it: dict, snap: dict) -> str:
    if it.get("type") == "collect":
        fac = it.get("facility") or facility_of(snap, it.get("name") or "") or "(시설 미상)"
        return f"{fac} 수령"
    if it.get("type") == "notify":
        return it.get("text") or it.get("name") or "알림"
    return it.get("name") or "(이름 없음)"


def qty_txt(it: dict) -> str:
    t = it.get("type")
    if t == "gather":
        return f"{_fmt(_n(it.get('target')))}개"
    if t == "collect":
        return f"{_fmt(_n(it.get('count')))}건"
    if t == "play":
        return {"song": "1곡", "list": "목록", "resume": "이어서"}.get(it.get("mode") or "", "이어서")
    if t == "notify":
        return "1회"
    return f"{_fmt(_n(it.get('count'), 1))}회"


def play_prog(it: dict, p: dict, st) -> str:
    """연주 카드의 진행 한 줄 — **카드는 연주가 끝날 때까지 돈다**. board.js playProg 와 **같은 글**."""
    pl = p.get("play") if isinstance(p.get("play"), dict) else None
    if st == "pending" and not p.get("done"):
        return "연주 시작 예정 (호출 없음 · 날개 0)"
    if st == "running":
        if not pl:
            return "연주 부탁 중…"
        el = _mmss(time.time() - float(pl.get("started") or 0)) if pl.get("started") else "00:00"
        title = pl.get("title") or it.get("name") or ""
        if pl.get("endless"):
            return f"연주 중 · {title} · 끝을 알 수 없음 — 반복 재생 · {el}"
        n = _n(pl.get("passes"))
        return f"연주 중 · {title}" + (f" · {_fmt(_n(pl.get('pass'), 1))}/{_fmt(n)}회" if n else "") + f" · {el}"
    if st == "done":
        return p.get("note") or (f"연주 끝 · {_fmt(_n(pl.get('passes')))}회" if pl and _n(pl.get("passes")) else "연주 끝")
    if st == "stopped":
        return "연주 멈춤 (■ 정지)"
    if st == "error":
        return "연주 실패"
    return "연주 시작을 부탁함"


def prog_of(it: dict, rep: int = 1) -> dict:
    """진행 글자와 막대 비율. **`pct` 가 None 이면 막대를 그리지 않는다** (아직 시작 전)."""
    p = it.get("progress") or {}
    st = it.get("status")
    acc = f" · 누적 {_fmt(_n(p.get('totalDone')))}" if (p.get("totalDone") is not None and rep > 1) else ""
    t = it.get("type")
    if t in FREE_TYPES:   # 연주·알림 — 막대 없음, 한 줄 (board.js progOf 와 같은 글)
        fresh = st == "pending" and not p.get("done")
        if t == "play":
            txt = play_prog(it, p, st)
        else:
            txt = "알림 예정 (호출 없음 · 날개 0)" if fresh else "알림 보냄"
        return {"txt": txt, "pct": None}
    if t == "collect":
        c = _n(it.get("count"))
        fresh = st == "pending" and not p.get("done")
        return {"txt": f"수령 예정 {_fmt(c)}건 (1회 호출)" if fresh
                else f"수령 {_fmt(_n(p.get('done')))} / {_fmt(c)}건{acc}",
                "pct": None if fresh else min(100, round(_n(p.get("done")) / max(1, c) * 100))}
    if t == "gather":
        # 채집 = 개수(target). 진행은 「지나간 회/예상 회 · +가방으로 센 개수/목표 개수」 (N3 — 개수로 받고 가방으로 센다).
        # `ui/js/board.js` 의 `progOf` 와 **한 글자도 다르면 안 된다** (`tests/test_viewmodel.py` 대조).
        target = _n(it.get("target"))
        n = _n(p.get("passesPlanned")) or wq.gather_plan(target)["passesPlanned"]
        got = _n(p.get("done"))
        ps = _n(p.get("passes"))
        bag = f" · 가방 {_fmt(_n(p.get('have')))}" if p.get("have") is not None else ""
        fresh = st == "pending" and not ps
        live = _n(p.get("live")) if (st == "running" and p.get("live") is not None) else None
        now = got + live if live is not None else got
        if fresh:
            h = _n(p.get("have"))
            txt = f"{_fmt(n)}회 · 날개 {_fmt(n * 5)}" + (f" · 가방 {_fmt(h)} → 목표 {_fmt(h + target)}"
                                                       if p.get("have") is not None else "")
        elif st == "running" and live is None:
            # 넘칠 수 없는 회(남은 100개 이상)는 가방을 도중에 읽지 않는다 — 그 사이 개수는 올라갈 수 없다. 몇 회째인지만 적는다
            txt = f"{_fmt(ps)}/{_fmt(n)}회 · +{_fmt(got)}/{_fmt(target)}개 · {_fmt(ps + 1)}회째 도는 중{bag}{acc}"
        else:
            txt = f"{_fmt(ps)}/{_fmt(n)}회 · +{_fmt(now)}/{_fmt(target)}개{bag}{acc}"
        # 반올림은 JS Math.round 와 같게 (.5 는 올림 — 파이썬 round 는 짝수 쪽으로 간다)
        pct = None if fresh else (100 if st == "done" else
                                  (min(100, int(now * 100 / target + 0.5)) if target else 0))
        return {"txt": txt, "pct": pct}
    if t == "alter":
        c = _n(it.get("count"), 1)
        fresh = st == "pending" and not p.get("done")
        if not fresh and (p.get("cap") is not None or _n(p.get("got"))):
            # 칸·수령을 센 카드 (N6) — 「등록 14/63 · 수령 7 · 칸 5/7」. 막대는 걸기만이면 등록, 그 밖은 수령으로 찬다
            slot = f" · 칸 {_fmt(_n(p.get('slot')))}/{_fmt(_n(p.get('cap')))}" if p.get("cap") is not None else ""
            base = _n(p.get("done")) if it.get("collect") == "none" else _n(p.get("got"))
            return {"txt": f"등록 {_fmt(_n(p.get('done')))}/{_fmt(c)} · 수령 {_fmt(_n(p.get('got')))}{slot}{acc}",
                    "pct": min(100, int(base * 100 / max(1, c) + 0.5))}   # 반올림은 JS Math.round 와 같게
        if st == "waiting":
            n = _n(p.get("passes"))
            return {"txt": f"등록됨 · 완료 감지 → 수령{f' · {n}건' if n else ''}{acc}", "pct": None}
        return {"txt": f"{_fmt(c)}건 등록 예정" if fresh
                else f"{_fmt(_n(p.get('done')))} / {_fmt(c)}건{acc}",
                "pct": None if fresh else min(100, round(_n(p.get("done")) / max(1, c) * 100))}
    c = _n(it.get("count"), 1)
    per = _n(p.get("per"), 1)
    mul = f" (×{per})" if per > 1 else ""
    fresh = st == "pending" and not p.get("done")
    planned = _n(p.get("passesPlanned"), 1) if t == "craft" else 1
    if t == "craft" and planned > 1:
        # 시설 상한으로 나눠 부르는 제작 (N7) — 「3/15번 · 30/150회」
        return {"txt": f"{_fmt(c)}회 예정{mul} · {_fmt(planned)}번 나눠 호출" if fresh
                else f"{_fmt(_n(p.get('calls')))}/{_fmt(planned)}번 · {_fmt(_n(p.get('done')))}/{_fmt(c)}회{mul}{acc}",
                "pct": None if fresh else min(100, round(_n(p.get("done")) / max(1, c) * 100))}
    return {"txt": f"{_fmt(c)}회 예정{mul}" if fresh
            else f"{_fmt(_n(p.get('done')))} / {_fmt(c)}회{mul}{acc}",
            "pct": None if fresh else min(100, round(_n(p.get("done")) / max(1, c) * 100))}


def sub_of(it: dict, snap: dict, events: list) -> str:
    p = it.get("progress") or {}
    t = it.get("type")
    if t == "play":   # 「이 곡만 · 2회」 — song 은 회차를 적는다 (resume 은 폴리오 제 설정대로라 없다)
        mode = it.get("mode") or ""
        return PLAY_MODE_KO.get(mode, "") + (f" · {_n(it.get('count'), 1)}회" if mode == "song" else "")
    if t == "notify":
        return "소리 끔" if it.get("sound") is False else "소리 켬"
    if t == "collect":
        return "시설당 1회 호출로 전부 수령"
    if t == "gather":
        extra = (f" · 직전 누적 {_fmt(_n(p.get('totalDone')))}"
                 if it.get("status") == "pending" and p.get("totalDone") else "")
        return f"회당 최대 100{extra}"
    if t == "alter":
        t0 = last_start(events, it.get("id") or "")
        parts = [facility_of(snap, it.get("name") or ""),
                 f"등록 {_hhmm(t0)}" if t0 else "",
                 "완료까지 기다림" if it.get("collect") == "wait" else ""]
        return " · ".join(x for x in parts if x)
    per = _n(p.get("per"), 1)
    return f"1회 {_fmt(per)}개" if per > 1 else ""


def item_calls(it: dict) -> int:
    """호출 예상 회수 — 서버 `preview`(`_preview_card`) 와 **같은 규칙**.

    가공은 남은 등록 + 수령 왕복(7건마다 한 번 — `wq.alter_calls`, N6). 7건 이하면 등록 count + 수령 1(걸기만 count),
    수령만 남은 waiting 은 1. 제작은 시설 상한으로 나눈 남은 호출(N7). 전에는 count 만 세어 카드의 「예상 N회」가 확인창의 수와 어긋났다."""
    p = it.get("progress") or {}
    t = it.get("type")
    if t in FREE_TYPES:
        return 0
    if t == "gather":
        return _n(p.get("passesPlanned")) or wq.gather_plan(_n(it.get("target")))["passesPlanned"]
    if t == "collect":
        return 1
    if t == "craft":   # 시설 상한으로 나눈 남은 호출 (N7) — 서버가 progress.passesPlanned 에 적어 둔다
        return max(1, _n(p.get("passesPlanned"), 1) - _n(p.get("calls")))
    return wq.alter_calls(it)[0]   # 남은 등록 + 수령 왕복 (N6)


def item_wing_calls(it: dict) -> int:
    """**정령의 날개를 쓰는 호출만** 센다. 수령(complete_altering_work)은 쓰지 않는다 —
    수령까지 세면 화면의 예상 소모가 실제와 어긋난다."""
    if it.get("wingCalls") is not None:
        return _n(it.get("wingCalls"))
    if it.get("type") == "collect" or it.get("type") in FREE_TYPES:
        return 0
    if it.get("type") == "alter":
        return wq.alter_calls(it)[1]   # 남은 등록만 — 수령은 날개 0
    return item_calls(it)


def group_calls(g: dict, kids: list, running: bool) -> tuple:
    """그룹의 (호출, 날개 호출) — `wq.Queue.preview` 와 같은 셈: **아직 할 자식(pending·waiting)** 의
    회차당 합 × **남은 회차**(`loop_left` — 멈췄던 회차부터 잇는다). 돌지 않는 그룹(끝남·치명)은 걸린
    가공의 수령만 한 번. 전에는 끝난 자식까지 × repeat 로 곱해 부풀렸다."""
    todo = [c for c in kids if c["status"] in ("pending", "waiting")]
    left = wq.Queue.loop_left(g, running)
    if left == 0:
        todo = [c for c in todo if c["status"] == "waiting"]
        left = 1 if todo else 0
    return sum(c["calls"] for c in todo) * left, sum(c["wingCalls"] for c in todo) * left


def work_left(snap: dict, name: str, aged: int = 0):
    """그 이름의 가공 중 **가장 빨리 끝나는 것**의 남은 초. 없으면 None, 다 끝났으면 0."""
    rows = [w for w in (snap.get("works") or []) if isinstance(w, dict) and w.get("name") == name]
    if not rows:
        return None
    live = [w for w in rows if not w.get("done")]
    if not live:
        return 0
    return max(0, min(_n(w.get("left")) for w in live) - aged)


def editable(it: dict) -> bool:
    """수량을 고칠 수 있는 카드 — 연주는 song 의 회차(1~20)만 (resume 은 없다). board.js editable 과 같은 규칙."""
    if (it.get("status") or "pending") not in ("pending", "stopped", "error"):
        return False
    if it.get("type") == "play":
        return (it.get("mode") or "") == "song"
    return it.get("type") not in ("collect", "group", "notify")


def card_view(it: dict, ctx: dict, rep: int = 1) -> dict:
    """카드 한 장이 화면에 적을 것 전부. **여기 없는 값을 화면이 지어내지 않는다.**"""
    snap, events = ctx["snap"], ctx["events"]
    t = it.get("type")
    st = it.get("status") or "pending"
    pr = prog_of(it, rep)
    sub = sub_of(it, snap, events)
    g = t == "gather"
    v = {
        "id": it.get("id", ""), "type": t, "status": st, "col": col_of(it),
        "kind": QTYPE_KO.get(t, t or ""), "kindCls": QTYPE_CLS.get(t, "warn"),
        "stateKo": QST_KO.get(st, st),
        "name": name_of(it, snap), "qty": qty_txt(it),
        "calls": item_calls(it), "wingCalls": item_wing_calls(it),
        "prog": pr, "sub": sub,
        "plain": pr["txt"] + (" · " + sub if sub else ""),
        "editable": editable(it),
        "unit": "개" if g else ("건" if t == "collect" else "회"),
        "step": 100 if g else 1,          # 채집은 100 단위로 움직이고 직접 입력은 1~99,999
        "value": _n(it.get("target")) if g else _n(it.get("count"), 1),
    }
    if st == "running":
        v["stage"] = f"{QTYPE_KO.get(t, '')} 중"
        v["t0"] = last_start(events, it.get("id") or "")
    if st == "waiting":
        # **시각은 글자로 굳히지 않는다.** 남은 초만 준다 — 폰의 시간대가 PC 와 다를 수 있고,
        # 「01:59 완료」는 보는 사람의 시계로 적어야 맞다. 세는 것은 서버, 적는 것은 화면.
        left = work_left(snap, it.get("name") or "", ctx.get("aged", 0))
        v["wait"] = {"state": "unknown" if left is None else ("done" if left == 0 else "left"),
                     "left": left}
    if st == "error":
        v["err"] = {"code": it.get("error") or "error", "msg": it.get("message") or "",
                    "hint": err_hint(it), "appCheck": (it.get("error") in wq.APP_ERRORS),
                    "stuck": bool(it.get("stuck")), "streak": _n(it.get("streak"))}
    return v


def group_view(g: dict, ctx: dict) -> dict:
    rep = max(1, _n(g.get("repeat"), 1))
    kids = [card_view(c, ctx, rep) for c in (g.get("items") or []) if c]
    inner = {k: [c for c in kids if c["col"] == k] for k, _ in COLS}
    return {
        "id": g.get("id", ""), "type": "group", "name": g.get("name") or "그룹",
        "col": col_of(g), "status": g.get("status") or "pending",
        "repeat": rep, "loop": _n(g.get("loop")),
        "onError": g.get("onError") or "continue",
        "retryFailed": g.get("retryFailed", True) is not False,
        "waitAlter": g.get("waitAlter") is True,
        "summary": wq.group_summary(g),
        "items": kids,
        "inner": {k: len(v) for k, v in inner.items()},
        # 완료 열 그룹 카드의 「실패 n」 — 서버가 실은 값을 믿고, 없으면 같은 함수로 센다
        "failBadge": _n(g["failBadge"]) if "failBadge" in g else wq.group_fail_badge(g),
        "loopLeft": wq.Queue.loop_left(g, bool(ctx.get("running"))),
        "calls": group_calls(g, kids, bool(ctx.get("running")))[0],
        "wingCalls": group_calls(g, kids, bool(ctx.get("running")))[1],
        "err": ({"code": g.get("error"), "msg": g.get("message") or "",
                 "hint": err_hint(g)} if g.get("error") else None),
    }


def queue_view(state: dict, snap: dict, wings_estimate=None, now: float | None = None) -> dict:
    """큐 화면 한 벌. 화면은 이 값을 **그대로 뿌리기만** 한다.

    `sig` 가 같으면 다시 그릴 것이 없다 (서명 비교).
    """
    now = time.time() if now is None else now
    events = state.get("events") or []
    # 가공 대기열을 **언제 읽었나**. 캐시를 읽은 뒤 흐른 만큼을 남은 시간에서 빼야
    # 「02:10 남음」이 멈춰 있지 않다. fetchedAt 은 캐시 종류별 dict 다 (server.snapshot).
    fa = (snap or {}).get("fetchedAt")
    fetched = _n((fa or {}).get("works")) if isinstance(fa, dict) else 0
    ctx = {"snap": snap or {}, "events": events, "running": bool(state.get("running")),
           "aged": max(0, int(now - fetched)) if fetched else 0}

    rows, counts = [], {k: 0 for k, _ in COLS}
    for it in (state.get("items") or []):
        if not it:
            continue
        v = group_view(it, ctx) if it.get("type") == "group" else card_view(it, ctx)
        rows.append(v)
        counts[v["col"]] = counts.get(v["col"], 0) + 1

    running = bool(state.get("running"))
    cur = state.get("current")
    cg = state.get("currentGroup")
    cg_row = next((r for r in rows if r["id"] == cg), None) if cg else None
    cur_name = ""
    for r in rows:
        if r["id"] == cur:
            cur_name = r["name"]
        for k in (r.get("items") or []):
            if k["id"] == cur:
                cur_name = k["name"]

    if running:
        where = (f"「{cg_row['name']}」 회차 {_fmt(cg_row['loop'] or 1)} / {_fmt(cg_row['repeat'])}"
                 if cg_row else cur_name)
        state_txt, state_cls = ("실행 중" + (f" · {where}" if where else ""), "run")
    else:
        sr = state.get("stopReason")
        cs = chain_stop(state)
        if cs is not None:
            state_txt, state_cls = (f"체인 정지 · {cs.get('error') or ''}", "bad")
        elif sr:
            state_txt, state_cls = ({"user": "사용자가 정지했습니다",
                                     "onError": "오류 시 정지로 멈췄습니다",
                                     "performance": "연주가 시작돼 멈췄습니다 — 연주가 끝난 뒤 ▶ 시작으로 이어집니다",
                                     }.get(sr, str(sr)), "stop")
        elif not rows:
            state_txt, state_cls = ("비어 있음", "idle")
        else:
            state_txt, state_cls = (f"대기 {_fmt(counts['wait'])}장", "idle")

    return {
        "ok": True,
        "sig": "{0}:{1}:{2}:{3}:{4}".format(
            len(rows), int(running), cur or "-", cg or "-",
            ",".join(str(counts[k]) for k, _ in COLS)),
        "segs": [{"k": k, "n": ko, "c": counts[k], "on": False} for k, ko in COLS],
        # 좁은 폭 — 탭 셋 · 체인 정지 배너 · 배너가 뜨면 실패 탭이 자동 선택
        "tabs": narrow_tabs(counts, "fail" if chain_stop(state) is not None else "wait"),
        "banner": chain_banner(state),
        "items": rows,
        "runbar": {"running": running, "state": state_txt, "cls": state_cls,
                   "wings": wings_estimate, "onError": state.get("onError") or "continue",
                   "events": len(events), "t0": run_start(events)},
        "lastError": state.get("lastError"),
        "stopReason": state.get("stopReason"),
        "events": [{"t": e.get("t"), "id": e.get("id"), "kind": e.get("kind"),
                    "ko": {"done": "완료", "error": "오류", "start": "시작",
                           "collect": "수령", "loop": "회차", "stop": "정지", "notice": "알림"}.get(e.get("kind"), e.get("kind")),
                    "msg": e.get("msg") or ""} for e in events[-100:]],
        # 알림 — 알림 카드·큐 종료. 폰이 id·t 로 「본 것」을 가려 한 번씩 띄운다
        "notices": [n for n in (state.get("notices") or []) if isinstance(n, dict)],
    }


def run_start(events: list) -> float:
    """이번 실행이 시작된 시각 — 마지막 정지 이후로 거슬러 올라가 가장 이른 회차·시작 회신."""
    t = 0.0
    for e in reversed(events or []):
        if e.get("kind") == "stop":
            break
        if e.get("kind") in ("loop", "start"):
            t = float(e.get("t") or t)
    return t


# ── 가공 화면 (ui/js/works.js 에서 옮겨 온 것) ──────────────────────────────
BAND_KO = {"run": "진행", "wait": "대기", "done": "완료"}
WVIEWS = (("all", "전체", ("run", "wait", "done")),
          ("run", "진행", ("run", "wait")),
          ("done", "완료", ("done",)))


def works_view(snap: dict, now: float | None = None) -> dict:
    """가공 탭 한 벌 — 시설별 묶기·완료 판정·수령 요약.

    **남은 시간이 0 이면 완료로 본다. 단 아직 시작 안 한 작업(NotStarted)은 예외다.**
    게임이 NotStarted 의 RemainingSeconds 를 0 으로 주기도 해서, 그대로 두면 완료로 세고
    수령까지 권하게 된다 (화면에 있던 규칙 그대로).

    **한 건이 한 칸이다.** 같은 이름을 묶어 「×n」으로 적지 않는다 — 그 n 이 건수인지
    산출 개수인지 읽는 사람이 구별할 수 없다.
    """
    now = time.time() if now is None else now
    snap = snap or {}
    fa = snap.get("fetchedAt")
    fetched = _n((fa or {}).get("works")) if isinstance(fa, dict) else 0
    aged = max(0, int(now - fetched)) if fetched else 0

    rows = [w for w in (snap.get("works") or []) if isinstance(w, dict)]
    if not rows:
        return {"ok": True, "sig": "empty", "empty": True, "facilities": [],
                "counts": {"run": 0, "wait": 0, "done": 0}, "collectable": [],
                "banner": None, "views": [{"k": k, "n": ko} for k, ko, _ in WVIEWS]}

    per_of = {}
    for r in (snap.get("alter") or []):
        if isinstance(r, dict) and r.get("name") and r["name"] not in per_of:
            per_of[r["name"]] = max(1, _n(r.get("per"), 1))

    by_fac: dict = {}
    counts = {"run": 0, "wait": 0, "done": 0}
    for w in rows:
        left = 0 if w.get("done") else max(0, _n(w.get("left")) - aged)
        d = bool(w.get("done")) or (w.get("state") != "NotStarted" and left == 0)
        band = "done" if d else ("wait" if w.get("state") == "NotStarted" else "run")
        counts[band] += 1
        by_fac.setdefault(w.get("facility") or "", []).append(
            {"name": w.get("name") or "", "left": left, "done": d, "band": band,
             # 칸 글자는 화면(ui/js/works.js tileHTML)과 **같은 말**이어야 한다 — 원격 화면이
             # 다른 말을 하면 같은 칸이 두 이름을 갖는다. 완료 「수령 대기」 · 대기 「대기 중」 · 진행은 남은 시간 mm:ss
             "st": "수령 대기" if d else ("대기 중" if band == "wait" else _mmss(left))})

    order = {"done": 0, "run": 1, "wait": 2}
    facilities = []
    for f in sorted(by_fac):
        ws = by_fac[f]
        dn = sum(1 for w in ws if w["done"])
        # 완료 → 진행(빨리 끝나는 것부터) → 대기. 수령할 것이 눈에 먼저 들어와야 한다
        ws = sorted(ws, key=lambda w: (order[w["band"]], w["left"], w["name"]))
        facilities.append({"name": f, "label": f or "(시설 미상)", "done": dn,
                           "running": len(ws) - dn, "works": ws,
                           "collectName": next((w["name"] for w in ws if w["done"]), "")})

    collectable = sorted(
        ({"facility": x["name"], "name": x["collectName"], "n": x["done"]}
         for x in facilities if x["done"]), key=lambda x: -x["n"])

    banner = None
    if counts["done"]:
        n: dict = {}
        for x in facilities:
            for w in x["works"]:
                if w["done"]:
                    n[w["name"]] = n.get(w["name"], 0) + 1
        gain = sorted(({"name": k, "c": c, "per": per_of.get(k)} for k, c in n.items()),
                      key=lambda g: -((g["c"] * g["per"]) if g["per"] else g["c"]))
        banner = {"done": counts["done"], "calls": len(collectable), "wings": 0,
                  "gain": gain[:3], "more": max(0, len(gain) - 3)}

    return {"ok": True,
            "sig": "{0}:{1}:{2}:{3}".format(counts["done"], counts["run"], counts["wait"], len(facilities)),
            "empty": False, "facilities": facilities, "counts": counts,
            "facN": len(facilities), "collectable": collectable, "banner": banner,
            "views": [{"k": k, "n": ko, "c": sum(counts[x] for x in keys)} for k, ko, keys in WVIEWS]}
