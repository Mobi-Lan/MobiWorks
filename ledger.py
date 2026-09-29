# -*- coding: utf-8 -*-
"""날개·산출 기록 — 실행 명령 한 번을 한 줄로 남긴다 (`data/ledger.jsonl`).

**왜 따로 두는가.** 큐의 회신 기록(events)은 최근 몇백 줄만 들고 있다가 큐를 비우면 같이 사라지고,
카드 로그는 회차마다 지워진다. 「오늘 날개를 얼마나 썼나」는 그걸로는 답할 수 없다.

**한 줄 = 실행 명령 한 번.** 쓰는 곳은 `Queue._exec` 한 군데뿐이다 — CLI 실행 명령은 전부 그리로
지나간다. 부르는 쪽마다 적게 하면 한 군데를 빠뜨려도 조용히 덜 세어진다.

기록이 큐를 망가뜨리면 안 된다: 쓰기가 실패해도 삼킨다 (장부 때문에 작업이 멈추지는 않는다).

날개 수는 **카탈로그 기준의 예상**이다 (실행 명령 1회 = 5개). 게임이 실제로 얼마를 뺐는지
회신은 문장(cost)으로만 알려주므로, 거절된 호출까지 실제로 빠졌는지는 알 수 없다.
그래서 성공/실패를 따로 세고 화면에도 나눠 적는다 — 「예상」이라고 쓴다.
"""
import io
import json
import os
import threading
import time

FILE = "ledger.jsonl"
KEEP_DAYS = 90          # 이보다 오래된 줄은 프로세스당 한 번 걸러낸다
READ_MAX = 200_000      # 읽을 때 들여다볼 최대 줄 수 (파일이 어떤 이유로 커져도 메모리를 지키게)
T_MAX = 4_102_444_800   # 2100-01-01 — 이보다 뒤의 시각은 기록이 아니라 깨진 값이다 (localtime 이 OSError 를 낸다)


def _int(v) -> int:
    """줄 안의 수 하나. 문자열·리스트·NaN 이 와도 0 — 한 줄이 `int()` 에서 죽으면 기록 전체를 잃는다."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == v and abs(v) != float("inf") else 0
    return 0

_LOCK = threading.RLock()
_pruned = False

# **누가 시켰나.** 밖(폰)에서 시작한 실행은 그 기기 이름을 같이 남긴다.
# 재화가 줄었는데 왜 줄었는지 못 밝히면 곤란한 것은 검사 때가 아니라 **실제로 쓸 때**다
# 큐는 한 번에 하나만 돌므로 값 하나로 충분하다.
_actor = ""


def set_actor(who: str = "") -> None:
    global _actor
    _actor = str(who or "")[:40]


def actor() -> str:
    return _actor


def _path() -> str:
    import store
    return os.path.join(store.DATA_DIR, FILE)


def add(cmd: str, name: str, wings: int, out: int, ok: bool, err: str = "") -> None:
    """한 줄 남긴다. **실패해도 조용히 넘어간다** — 장부가 작업을 멈추면 안 된다."""
    row = {"t": int(time.time()), "cmd": cmd, "name": name or "",
           "wings": int(wings), "out": int(out), "ok": bool(ok)}
    if err:
        row["err"] = err
    if _actor:
        row["by"] = _actor        # 밖에서 시킨 것 — 기기 이름
    try:
        with _LOCK:
            _prune_once()
            import store
            os.makedirs(store.DATA_DIR, exist_ok=True)
            with io.open(_path(), "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _rows(cutoff: float = 0.0) -> list:
    """파일을 읽어 줄을 돌려준다. **깨진 줄은 건너뛴다** — 한 줄 때문에 기록 전체를 잃지 않는다."""
    p = _path()
    if not os.path.exists(p):
        return []
    out = []
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= READ_MAX:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                # 시각은 **달력에 놓을 수 있는 수**여야 한다 — 불리언·NaN·9e18 같은 값은 `localtime` 이 OSError 로
                # 죽여 기록 화면 전체가 섰다 (손으로 고친 파일). 그런 줄은 깨진 줄과 같이 건너뛴다.
                t = r.get("t") if isinstance(r, dict) else None
                if (isinstance(t, (int, float)) and not isinstance(t, bool) and t == t
                        and cutoff <= t <= T_MAX):
                    out.append(r)
    except OSError:
        return []
    return out


def _prune_once() -> None:
    """프로세스당 한 번, KEEP_DAYS 보다 오래된 줄을 걸러 다시 쓴다. 호출 쪽이 이미 잠금을 쥐고 있다."""
    global _pruned
    if _pruned:
        return
    _pruned = True
    p = _path()
    if not os.path.exists(p):
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    allrows = _rows(0)                  # 한 번만 읽는다
    keep = [r for r in allrows if r["t"] >= cutoff]
    try:
        if len(keep) == len(allrows):
            return                      # 걸러낼 것이 없다 — 건드리지 않는다
        tmp = f"{p}.{os.getpid()}.tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, p)
    except OSError:
        pass


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# 날개도 CLI 도 안 쓰는 카드의 줄 (`Queue._run_free` — 연주 부탁·알림). 호출 수·실패 수에 섞지 않는다 —
# 섞으면 「오늘 날개 0 · 실패 1」 처럼 실행 통계가 거짓말을 한다. 따로 `free` 에 센다.
FREE_CMDS = ("play", "notify")


def _blank() -> dict:
    return {"wings": 0, "calls": 0, "fail": 0, "failWings": 0, "out": {}, "collected": 0,
            "remote": 0, "remoteWings": 0, "by": {}, "free": 0}


def _put(acc: dict, r: dict) -> None:
    if r.get("cmd") in FREE_CMDS:      # 연주·알림 — 날개 0·CLI 0. 중립 칸에만 센다
        acc["free"] = acc.get("free", 0) + 1
        return
    ok = bool(r.get("ok"))
    w = _int(r.get("wings"))
    acc["calls"] += 1
    acc["wings"] += w
    if not ok:
        acc["fail"] += 1
        acc["failWings"] += w
    who = r.get("by")
    if isinstance(who, str) and who:   # 밖에서 시킨 실행은 따로 센다 (「내가 안 켰는데 왜 줄었지」). 기기 이름은 글자다
        who = who[:40]
        acc["remote"] += 1
        acc["remoteWings"] += w
        acc["by"][who] = acc["by"].get(who, 0) + w
    n = _int(r.get("out"))
    if n <= 0:
        return
    if r.get("cmd") == "complete_altering_work":
        acc["collected"] += n           # 수령은 **건수**다 — 개수와 섞어 더하지 않는다
    else:
        nm = r.get("name") if isinstance(r.get("name"), str) and r.get("name") else "(이름 없음)"
        acc["out"][nm[:200]] = acc["out"].get(nm[:200], 0) + n


def stats(days: int = 14, now: float | None = None) -> dict:
    """하루씩 끊은 기록 + 오늘·7일 합계. 날짜는 **그 컴퓨터의 달력 날짜**로 끊는다.

    `days` 는 돌려줄 날짜 칸 수. 0 이하이거나 너무 크면 1~90 으로 자른다.
    """
    days = max(1, min(90, int(14 if days is None else days)))   # 0 은 「기본값」이 아니라 0 이다
    now = time.time() if now is None else now
    # 오늘 00:00 부터 days-1 일 전 00:00 까지
    t = time.localtime(now)
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    cutoff = midnight - (days - 1) * 86400
    rows = _rows(cutoff)

    by: dict = {}
    for r in rows:
        by.setdefault(_day(r["t"]), _blank())
        _put(by[_day(r["t"])], r)

    out_days = []
    for i in range(days - 1, -1, -1):
        d = _day(midnight - i * 86400)
        a = by.get(d) or _blank()
        out_days.append({"d": d, **a, "out": _top(a["out"]), "kinds": len(a["out"])})

    today = _blank()
    week = _blank()
    for r in rows:
        if r["t"] >= midnight:
            _put(today, r)
        if r["t"] >= midnight - 6 * 86400:
            _put(week, r)
    return {"ok": True, "days": out_days, "rows": len(rows),
            "today": {**today, "out": _top(today["out"]), "kinds": len(today["out"])},
            "week": {**week, "out": _top(week["out"]), "kinds": len(week["out"])},
            "wingsPerCall": 5, "keepDays": KEEP_DAYS}


def _top(d: dict, n: int = 8) -> list:
    """산출은 **많은 것부터 몇 가지만** — 화면이 읽히게. 나머지는 「그 밖 N종」으로 접는다."""
    items = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    head = [{"name": k, "n": v} for k, v in items[:n]]
    if len(items) > n:
        head.append({"name": f"그 밖 {len(items) - n}종", "n": sum(v for _, v in items[n:]), "rest": True})
    return head


# ── 일간 리포트 한 줄 ──────────────────────
# 「오늘 날개 35 · 통나무 300 · 장작 40 · 외 2」. 오버레이 밴드와 보드 「날개·산출」이 **같은 글**을 쓴다 —
# 두 곳이 따로 조립하면 한쪽만 고쳐져 숫자가 어긋난다. 그래서 조립은 여기 한 군데다.
LINE_TOP = 3            # 산출은 많은 것부터 세 가지만 (7일 표·보드)
BAND_TOP = 1            # 밴드의 오늘 한 줄은 가장 많은 것 하나 + 「외 n」 — 30px 밴드가 넘친다
FAIL_TAG = "실패"        # 날개를 쓴 실패 꼬리 「실패 N」 (밴드는 붉은 칸으로 뗀다)
LINE_NAME = 8           # 이름은 여덟 글자까지 (넘으면 일곱 글자 + 「…」)
LINE_EMPTY = "오늘 아직 없음"


def _cut(name, n: int = LINE_NAME) -> str:
    t = " ".join(str(name or "").split())
    return t if len(t) <= n else t[:max(1, n - 1)] + "…"


def _num(n) -> str:
    return f"{_int(n):,}"


def _kinds(day: dict, shown: list) -> int:
    """산출 가짓수. `kinds` 가 있으면 그 값, 없으면(옛 응답) 목록과 「그 밖 N종」에서 센다."""
    k = day.get("kinds")
    if isinstance(k, int) and not isinstance(k, bool) and k >= 0:
        return k
    n = len(shown)
    for o in day.get("out") or []:
        if isinstance(o, dict) and o.get("rest"):
            digits = "".join(ch for ch in str(o.get("name") or "") if ch.isdigit())
            n += int(digits) if digits else 0
    return n


def out_brief(day, top: int = LINE_TOP) -> str:
    """하루치 산출 → 「통나무 300 · 장작 40 · 외 2」. 산출이 없으면 수령 건수, 그것도 없으면 빈 글."""
    d = day if isinstance(day, dict) else {}
    shown = [o for o in (d.get("out") or [])
             if isinstance(o, dict) and not o.get("rest") and _int(o.get("n")) > 0]
    head = shown[:top]
    parts = [f"{_cut(o.get('name'))} {_num(o.get('n'))}" for o in head]
    more = _kinds(d, shown) - len(head)
    if more > 0:
        parts.append(f"외 {more}")
    if not parts and _int(d.get("collected")) > 0:
        parts.append(f"수령 {_num(d.get('collected'))}건")
    return " · ".join(parts)


def day_line(stats) -> str:
    """`stats()` 결과(또는 그 `today` 한 칸) → 오늘 한 줄.

    · 오늘 실행이 한 번도 없으면 「오늘 아직 없음」
    · 있으면 「오늘 날개 N」 + 산출 많은 것 셋 + 「외 n」(나머지 가짓수)
    날개는 카탈로그 기준 **예상**이다(머리말) — 줄이 짧아야 해서 「약」은 붙이지 않는다."""
    st = stats if isinstance(stats, dict) else {}
    t = st.get("today") if isinstance(st.get("today"), dict) else st
    if not (_int(t.get("calls")) or _int(t.get("wings")) or t.get("out") or _int(t.get("collected"))):
        return LINE_EMPTY
    brief = out_brief(t)
    fw = _int(t.get("failWings"))
    # 날개는 썼는데 산출이 없던 몫 — 「헛 N」
    brief = out_brief(t, top=BAND_TOP)
    # 날개를 쓴 실패는 맨 끝에 「실패 N」 — 밴드(overlay.day_cells)가 이 꼬리를 떼어 붉은 칸으로 그린다
    return f"오늘 날개 {_num(t.get('wings'))}" + (f" · {brief}" if brief else "") + (f" · {FAIL_TAG} {_num(fw)}" if fw else "")


def week_rows(stats, n: int = 7) -> list:
    """오버레이 펼침 판의 7일 표 — 최근 날부터. [{d: "09-25", wings, failWings, out: "통나무 300 · 외 4"}]
    산출은 많은 것부터 셋 + 「외 n」(LINE_TOP). 하나만 남기는 것은 밴드의 오늘 한 줄(BAND_TOP)이다."""
    st = stats if isinstance(stats, dict) else {}
    days = [x for x in (st.get("days") or []) if isinstance(x, dict)][-max(1, int(n)):]
    return [{"d": str(x.get("d") or "")[5:], "wings": _int(x.get("wings")), "failWings": _int(x.get("failWings")),
             "out": out_brief(x) or "—"}
            for x in reversed(days)]
