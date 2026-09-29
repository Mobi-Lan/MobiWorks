"""생활 작업 도메인 로직 — CLI 응답을 화면이 쓰기 좋은 모양으로 바꾼다.

CLI 응답을 그대로 믿지 않는다: 필드가 없거나 타입이 다르거나 아예 없는 경우가 실제로 있으므로
(docs/CLI.md §4) 전부 방어적으로 읽는다. 여기서 예외가 나면 화면 전체가 비므로 절대 던지지 않는다.

제작(craft)과 가공(alter)은 CLI 응답의 필드명이 다르다 — Craftable/ProducedPerCraft vs Alterable/ProducedPerWork.
그 차이는 여기서 흡수하고, 화면은 같은 모양(kind 만 다름)으로 다룬다.
"""
from __future__ import annotations

import math as _math

REASON_KO = {
    "insufficient_living_skill_level": "생활 스킬 레벨 부족",
    "insufficient_facility_level": "시설 레벨 부족",
    "insufficient_decor_score": "장식 점수 부족",
    "not_enough_ingredient": "재료 부족",
    "ingredient_locked": "재료가 잠김",
    "insufficient_transfer_cost": "이동 비용 부족",
    "": "",
}


def _s(v, default: str = "") -> str:
    """빈 문자열·공백뿐인 문자열도 값이 없는 것으로 본다 — 필드가 아예 없는 경우와 같게 default 로."""
    if isinstance(v, str):
        return v.strip() or default
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return default


def _n(v, default: int = 0) -> int:
    """숫자 강제. NaN·Infinity 는 값이 아니라 default 다 — json 이 표준 밖 `NaN`/`Infinity` 리터럴을
    그대로 파싱하므로(실측) int() 에 넘기면 ValueError/OverflowError 가 나고, 이 모듈은 던지면 안 된다."""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v) if _math.isfinite(v) else default
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return default
        return int(f) if _math.isfinite(f) else default
    return default


def _rows(v) -> list:
    """{items:[...]} 도 [...] 도 받는다 — 명령마다 감싸는 방식이 다르다."""
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return _rows(v.get("items"))
    return []


def _missing(v) -> list:
    """부족 재료. 같은 재료가 여러 줄로 와도 한 줄로 합친다 — 안 합치면 plan() 이 줄마다 재고를 따로
    빼 보므로 실제로는 모자란데 「충분」으로 보인다. 음수는 0 으로 본다(화면에 「-3개 필요」가 뜨지 않게)."""
    out: list = []
    index: dict = {}
    for m in (v if isinstance(v, list) else []):
        if not isinstance(m, dict):
            continue
        name = _s(m.get("DisplayName"))
        if not name:
            continue
        req, own = max(0, _n(m.get("Required"))), max(0, _n(m.get("Owned")))
        cur = index.get(name)
        if cur is None:
            cur = index[name] = {"name": name, "required": 0, "owned": own, "short": 0}
            out.append(cur)
        cur["required"] += req
        cur["owned"] = max(cur["owned"], own)   # 같은 재료의 보유량은 같은 값이라 더하면 두 배가 된다
        cur["short"] = max(0, cur["required"] - cur["owned"])
    return out


def recipes(body, kind: str) -> list:
    """제작/가공 레시피를 한 모양으로. kind: 'craft' | 'alter'."""
    ok_key, per_key = ("Craftable", "ProducedPerCraft") if kind == "craft" else ("Alterable", "ProducedPerWork")
    out = []
    for r in _rows(body):
        name = _s(r.get("DisplayName"))
        if not name:
            continue
        miss = _missing(r.get("MissingIngredients"))
        reason = _s(r.get("Reason"))
        out.append({
            "kind": kind,
            "name": name,
            "ready": bool(r.get(ok_key)),
            "per": max(1, _n(r.get(per_key), 1)),
            "reason": reason,
            "reasonKo": REASON_KO.get(reason, reason),
            "missing": miss,
            # 재료만 모으면 되는지(=시설·레벨 문제가 아닌지). 화면에서 「모으면 됨」과 「막힘」을 가른다
            "blocked": bool(reason) and reason not in ("not_enough_ingredient", ""),
        })
    out.sort(key=lambda x: (not x["ready"], x["blocked"], x["name"]))
    # 동명 레시피(같은 아이템의 다른 제작 경로)에 응답 순서로 임시 번호를 단다. 관찰 DB 가 있으면 server 가
    # 재료 집합 매칭으로 확정 번호(id)를 덮어쓴다 — 여기 번호는 DB 가 없을 때의 폴백이다.
    seen: dict[str, int] = {}
    total: dict[str, int] = {}
    for r in out:
        total[r["name"]] = total.get(r["name"], 0) + 1
    for r in out:
        n = seen[r["name"]] = seen.get(r["name"], 0) + 1
        r["variant"] = n
        r["variants"] = total[r["name"]]
        r["id"] = f"{kind}:{r['name']}#{n}"
    return out


def gatherables(body) -> list:
    """채집 가능 목록. ToolOk=False 면 도구가 없어 실제로는 못 캔다."""
    out = [{"name": _s(g.get("DisplayName")), "toolOk": bool(g.get("ToolOk"))} for g in _rows(body)]
    out = [g for g in out if g["name"]]
    out.sort(key=lambda x: (not x["toolOk"], x["name"]))
    return out


def works(body) -> dict:
    """가공 대기열. 같은 시설(FacilityName)의 완료분은 한 번에 수령되므로 시설로 묶어 준다."""
    rows = []
    for w in _rows(body.get("works") if isinstance(body, dict) else body):
        name = _s(w.get("DisplayName"))
        if not name:
            continue
        state = _s(w.get("State"))
        done = bool(w.get("IsCompleted")) or state == "Completed"
        rows.append({"name": name, "facility": _s(w.get("FacilityName")), "state": state,
                     "done": done, "left": 0 if done else max(0, _n(w.get("RemainingSeconds")))})
    rows.sort(key=lambda x: (not x["done"], x["left"]))
    by_facility: dict[str, dict] = {}
    for r in rows:
        f = by_facility.setdefault(r["facility"], {"facility": r["facility"], "done": 0, "running": 0, "next": None})
        if r["done"]:
            f["done"] += 1
        else:
            f["running"] += 1
            if f["next"] is None or r["left"] < f["next"]:
                f["next"] = r["left"]
    reported = _n(body.get("completedCount")) if isinstance(body, dict) else 0
    return {"works": rows, "facilities": sorted(by_facility.values(), key=lambda x: x["facility"]),
            "completed": max(reported, sum(1 for r in rows if r["done"]))}


def stock(body) -> dict:
    """아이템 재고: 이름 → 가방(bag) / 창고(storage) / 합계(total) + 위치별 내역.

    CLI 의 Owned 숫자는 **가방(inventory)만** 센다 — 실측: 창고(account_storage)에만 있는 재료가 부족 재료로 잡힌
    3,478행이 전부 Owned=0. 그러나 '만들 수 있나' 판정은 **가방+창고 합산**(게임이 창고 재료를 원격 사용, 이송 비용만 듦)이므로
    plan()/apply_storage() 는 bag 과 storage 를 합쳐 short 를 낸다. bag 은 표시·이송량 계산에 쓴다."""
    total: dict[str, int] = {}
    bag: dict[str, int] = {}
    storage: dict[str, int] = {}
    where: dict[str, dict] = {}
    for it in _rows(body):
        name = _s(it.get("DisplayName"))
        if not name:
            continue
        cnt = _n(it.get("Count"))
        loc = _s(it.get("Location"), "inventory")
        total[name] = total.get(name, 0) + cnt
        (bag if loc == "inventory" else storage)[name] = (bag if loc == "inventory" else storage).get(name, 0) + cnt
        where.setdefault(name, {})[loc] = where.get(name, {}).get(loc, 0) + cnt
    return {"total": total, "bag": bag, "storage": storage, "where": where}


def apply_storage(recipes_rows: list, storage: dict | None) -> list:
    """레시피 행의 missing 에 창고 수량과 **합산 기준 부족**을 덧붙인다 (제자리 수정 후 같은 목록 반환).
    CLI 의 Owned(가방)는 `owned` 그대로 두고, `bag` 으로도 한 번 더 적는다. `short` 는 가방+창고 합산 기준으로 바꾼다."""
    storage = storage if isinstance(storage, dict) else {}
    for r in recipes_rows if isinstance(recipes_rows, list) else []:
        for m in r.get("missing") or []:
            sto = _n(storage.get(m["name"]))
            m["bag"] = m["owned"]
            m["storage"] = sto
            m["short"] = max(0, m["required"] - m["owned"] - sto)
    return recipes_rows


def plan(recipe: dict, count: int, have: dict, storage: dict | None = None) -> dict:
    """이 레시피로 count 회 작업하려면 무엇이 얼마나 부족한가.

    CLI 의 MissingIngredients 는 '1회 기준 부족분'만 준다(필요·보유). count 회로 늘리면 필요량도 배로 는다.
    **보유 판정은 가방 + 계정 창고 합산**이다 — 게임은 창고 재료를 원격으로 가져와 제작한다(이송 비용만 든다).
    실측 규칙(docs/CLI.md §3.5): 「가능」= 가방+창고에 전부 있음, not_enough_ingredient = 합쳐도 부족, Owned 숫자만 가방 기준.
    have 는 가방 재고(없으면 CLI 가 준 Owned), storage 는 창고 재고. 행마다 bag / storage / owned(합) / short(합산 기준) /
    fromStorage(창고에서 이송해야 할 양 = min(storage, required − bag)) 를 준다."""
    count = max(1, _n(count, 1))
    storage = storage if isinstance(storage, dict) else {}
    need = []
    for m in recipe.get("missing") or []:
        req = m["required"] * count
        bag = _n(have.get(m["name"], m["owned"]))
        sto = _n(storage.get(m["name"]))
        owned = bag + sto
        need.append({"name": m["name"], "required": req, "bag": bag, "storage": sto, "owned": owned,
                     "short": max(0, req - owned), "fromStorage": max(0, min(sto, req - bag))})
    return {"id": recipe.get("id", ""), "name": recipe.get("name", ""), "kind": recipe.get("kind", ""), "count": count,
            "produced": recipe.get("per", 1) * count, "need": need,
            "enough": all(x["short"] == 0 for x in need)}


def requirements(row: dict, count: int, bag: dict, storage: dict | None = None) -> list:
    """count 회에 필요한 재료 전부 = 확인된 재료(known, 사전 DB) ∪ 지금 부족(missing, CLI). 1회 필요량은 둘 중 큰 값.
    행마다 required / bag / storage / owned(합) / short(합산 기준) / fromStorage. 재료 정보가 전혀 없으면 빈 목록."""
    count = max(1, _n(count, 1))
    storage = storage if isinstance(storage, dict) else {}
    bag = bag if isinstance(bag, dict) else {}
    per: dict[str, int] = {}
    cli_owned: dict[str, int] = {}
    for m in row.get("missing") or []:
        if isinstance(m, dict) and m.get("name"):
            per[m["name"]] = max(per.get(m["name"], 0), _n(m.get("required")))
            cli_owned[m["name"]] = _n(m.get("owned"))
    known = row.get("known") if isinstance(row.get("known"), dict) else {}
    for name, req in known.items():
        if isinstance(name, str) and name:
            per[name] = max(per.get(name, 0), _n(req))
    out = []
    for name, req1 in per.items():
        req = req1 * count
        b = _n(bag.get(name, cli_owned.get(name, 0)))
        s = _n(storage.get(name))
        out.append({"name": name, "required": req, "requiredPer": req1, "bag": b, "storage": s, "owned": b + s,
                    "short": max(0, req - b - s), "fromStorage": max(0, min(s, req - b))})
    return out


def max_by_stock(row: dict, bag: dict, storage: dict | None = None) -> tuple:
    """지금 보유(가방+창고)로 몇 회까지 만들 수 있나 = min(floor(owned / 1회 필요량)).
    (값, partial) — 재료 정보가 전혀 없으면 (None, True). 확인된 재료가 있어도 미확인 재료가 더 있을 수 있다(사전은 완전하지 않다)."""
    reqs = [r for r in requirements(row, 1, bag, storage) if r["requiredPer"] > 0]
    if not reqs:
        return None, True
    return min(r["owned"] // r["requiredPer"] for r in reqs), False


# ── 검색·필터 (MobiFolio library.py 방식) ──
import re as _re
import unicodedata as _ud

_SEP = _re.compile(r"[\s\-_·.,'\"()\[\]{}:;!?/+&*~^%$#@`|\\<>=]+")
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_CHO_MERGE = {"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}   # 된소리는 예사소리 칸에


def norm(*parts) -> str:
    """검색용 정규화: NFKC → 소문자 → 공백·구분 기호 제거. 여러 문자열(이름 + 재료명들)을 이어붙인다."""
    s = " ".join(p for p in parts if isinstance(p, str))
    return _SEP.sub("", _ud.normalize("NFKC", s).lower())


def initial(name: str) -> str:
    """색인용 첫 글자: 한글은 초성(된소리 합침), 영문은 대문자, 그 외 '#'. 첫 글자가 따옴표·괄호·기호면 다음 글자를 본다."""
    s = _ud.normalize("NFKC", name or "")
    for ch in s:
        if _SEP.match(ch):
            continue
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            c = _CHO[(o - 0xAC00) // 588]
            return _CHO_MERGE.get(c, c)
        if 0x1100 <= o <= 0x1112:   # NFKC 가 호환 자모 ㄱ(U+3131) 을 초성 자모(U+1100~) 로 바꾼다
            c = _CHO[o - 0x1100]
            return _CHO_MERGE.get(c, c)
        if ch in _CHO:
            return _CHO_MERGE.get(ch, ch)
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        return "#"
    return "#"


def index_rows(rows: list, kind: str) -> list:
    """행마다 norm(이름 + 재료명)·initial 을 붙인다 (제자리). kind: craft|alter|gather."""
    for r in rows if isinstance(rows, list) else []:
        ings = [m.get("name", "") for m in (r.get("missing") or []) if isinstance(m, dict)]
        ings += list((r.get("known") or {}).keys()) if isinstance(r.get("known"), dict) else []
        r["norm"] = norm(r.get("name", ""), *ings)
        r["initial"] = initial(r.get("name", ""))
    return rows


def _count(labels) -> dict:
    out: dict[str, int] = {}
    for x in labels:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def tab_summary(rows: list, kind: str) -> dict:
    """탭 하나의 요약 (필터 전 전체 기준): count, byInitial, byStatus(ready/short/blocked), byCat 또는 bySkill."""
    rows = rows if isinstance(rows, list) else []
    out = {"count": len(rows), "byInitial": _count(r.get("initial") or initial(r.get("name", "")) for r in rows)}
    if kind == "gather":
        out["byStatus"] = {"ready": sum(1 for r in rows if r.get("toolOk")), "short": 0,
                           "blocked": sum(1 for r in rows if not r.get("toolOk"))}
        out["bySkill"] = _count(r.get("skill", "") for r in rows)
    else:
        out["byStatus"] = {"ready": sum(1 for r in rows if r.get("ready")),
                           "blocked": sum(1 for r in rows if not r.get("ready") and r.get("blocked")),
                           "short": sum(1 for r in rows if not r.get("ready") and not r.get("blocked"))}
        out["byCat"] = _count(r.get("cat", "") for r in rows)
    return out


def summary(craft: list, alter: list, w: dict) -> dict:
    return {"craftReady": sum(1 for x in craft if x["ready"]), "craftTotal": len(craft),
            "alterReady": sum(1 for x in alter if x["ready"]), "alterTotal": len(alter),
            "worksRunning": sum(1 for x in w["works"] if not x["done"]), "worksDone": w["completed"]}
