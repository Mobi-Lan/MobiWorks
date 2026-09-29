"""관찰 누적 DB — CLI 가 sync 때마다 준 것을 합집합으로 모아 data/recipes.json 에 남긴다.

왜 필요한가: get_craftable_items / get_alterable_items 의 MissingIngredients 는 '그 순간 부족한 재료'만 준다.
하지만 Required 는 레시피의 고정값이라 한 번 본 재료는 영원히 유효하다 → 볼 때마다 합치면 DB 가 자란다.
그래서 여기서는 어떤 것도 지우지 않는다 (레시피가 목록에서 사라져도 남긴다). 완전성은 알 수 없으므로
recipes[*].complete 는 항상 False 로 두고, 나중에 외부 데이터로 확정할 자리로만 쓴다.

동명 레시피 (schema 2): 실측에서 같은 이름이 한 응답에 두 줄 이상 오는 경우가 있었다 (제작 1834행 중 79종 159행).
같은 아이템을 다른 재료로 만드는 '경로'다. 키는 "{kind}:{name}#{n}" 이고 n 은 경로 번호(1부터).
경로 매칭은 응답 순서가 아니라 재료 집합으로 한다 — 다음 sync 에서 순서가 바뀌어도 같은 경로에 붙는다.

파일 모양은 /api/recipes 계약과 같다 (+ "_schema"). ingredients 는 recipes·items·gatherables 에서 매번 다시
계산하는 파생 섹션이라 같은 입력을 두 번 넣어도 결과가 같다(멱등).

출처 (schema 3): 재료는 「못 만드는」 레시피에서만 보이므로 한 사람의 관찰로는 끝까지 못 채운다. 조회는 재화를 쓰지 않으니
(실측) 여러 사용자의 관찰을 합친다 — 앱에 묶인 시드(seed) · 정적 파일로 받는 커뮤니티 DB(community) · 내 관찰(self).
entry.sources 에 출처별 관찰 횟수, srcIng 에 출처별로 본 재료·수량, confirmed 는 서로 다른 출처 둘 이상이 모든 재료의
수량에 동의했을 때만 True. 밖으로 내보내는 기여(contribution)는 화이트리스트 필드만 담는다 — 보유 개수·캐릭터·재화 절대 금지.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time

import store
import work

FILE = "recipes.json"
SCHEMA = 3
SECTIONS = ("recipes", "ingredients", "items", "gatherables", "facilities", "currencies")
MAX_CONFLICTS = 200
SOURCES = ("self", "seed", "community")
CONTRIB_FORMAT = "mobiworks-recipes/1"        # 기여 파일 포맷. 모양이 바뀌면 /2 로 올린다
# 기여 페이로드에 들어갈 수 있는 필드 전부 — 여기 없는 건 절대 안 나간다 (테스트가 강제한다)
CONTRIB_RECIPE_KEYS = ("kind", "name", "per", "ingredients", "signature", "variant", "variants")
CONTRIB_TOP_KEYS = ("format", "app", "day", "catalog", "recipes", "gatherables", "facilities")
# 이관 재구성에 쓰는 캐시 종류 (server.READ_COMMANDS 의 kind 와 같다)
CACHE_KINDS = ("craft", "alter", "items", "gather", "works", "currencies")
_lock = threading.RLock()


def key(kind: str, name: str, n: int = 1) -> str:
    """레시피 키. 같은 이름이 제작·가공 양쪽에 있을 수 있어 kind 를, 같은 이름의 다른 경로가 있어 #n 을 붙인다."""
    return f"{kind}:{name}#{n}"


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _n(v, default: int = 0) -> int:
    """숫자 강제. NaN·Infinity 는 값이 아니라 default (work._n 과 같은 규칙 — 섞이면 한 섹션이 통째로 버려진다)."""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v) if math.isfinite(v) else default
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return default
        return int(f) if math.isfinite(f) else default
    return default


def _rows(v) -> list:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return _rows(v.get("items"))
    return []


def empty() -> dict:
    return {"_schema": SCHEMA, "recipes": {}, "ingredients": {}, "items": {}, "gatherables": {},
            "facilities": {}, "currencies": {}, "conflicts": [], "stats": {}, "seeds_applied": []}


def normalize(d) -> dict:
    """파일 내용을 DB 모양으로 (순수, I/O 없음). schema 2 entry(출처 없음)는 전부 '내 관찰'로 친다.
    schema 1 이하(경로 없이 합쳐진 판)는 여기서 살릴 수 없으므로 load() 가 캐시로 재구성한다."""
    out = empty()
    if not isinstance(d, dict):
        return out
    for k in SECTIONS:
        if isinstance(d.get(k), dict):
            out[k] = d[k]
    if isinstance(d.get("conflicts"), list):
        out["conflicts"] = [c for c in d["conflicts"] if isinstance(c, dict)]
    if isinstance(d.get("stats"), dict):
        out["stats"] = d["stats"]
    if isinstance(d.get("seeds_applied"), list):
        out["seeds_applied"] = [h for h in d["seeds_applied"] if isinstance(h, str)]
    for e in list(out["recipes"].values()):
        if not isinstance(e, dict):
            continue
        ing = e.get("ingredients") if isinstance(e.get("ingredients"), dict) else {}
        if not isinstance(e.get("sources"), dict):
            e["sources"] = {"self": max(1, _n(e.get("seen")))}
        if not isinstance(e.get("srcIng"), dict):
            e["srcIng"] = {"self": {a: _n(v) for a, v in ing.items()}} if ing else {}
        e["confirmed"] = _confirmed(e)
    return out


def _looks_pathkeyed(d: dict) -> bool:
    """내용으로 판단한 schema ≥ 2 (경로 키 kind:name#n + entry 의 variant). _schema 필드가 망가져도 데이터는 살린다 —
    필드 하나 때문에 캐시로 재구성하면 캐시가 없을 때 관찰 전체가 조용히 사라진다(실측)."""
    recs = d.get("recipes")
    if not isinstance(recs, dict) or not recs:
        return False
    for k, e in recs.items():
        if isinstance(k, str) and "#" in k and isinstance(e, dict) and "variant" in e:
            return True
    return False


def load() -> dict:
    """없거나 모양이 틀리면 빈 DB. 옛 스키마(경로 없이 kind:name 으로 합쳐진 판)면 버리고 캐시로 재구성한다.
    schema 2(경로는 있고 출처가 없는 판)는 데이터를 그대로 두고 출처 필드만 채워 3 으로 올린다 — 재구성하면 안 된다(관찰 유실).
    _schema 가 망가졌어도 내용이 경로 키면 살린다 (_looks_pathkeyed).
    **이 앱보다 높은 스키마면 읽기만 하고 절대 쓰지 않는다** — normalize 가 모르는 섹션을 버리는데 그대로 저장하면
    새 버전이 쌓은 데이터를 옛 앱이 깎아 내린다(강등). _schemaAhead 를 달아 save 가 거부하게 한다."""
    d = store.load(FILE, None)
    if not isinstance(d, dict) or (_n(d.get("_schema")) < 2 and not _looks_pathkeyed(d)):   # 없음·손상·옛 스키마 → 캐시로 재구성 (캐시도 없으면 빈 DB)
        return _rebuild_from_cache()
    if _n(d.get("_schema")) > SCHEMA:
        out = normalize(d)
        out["_schema"] = _n(d.get("_schema"))   # 원래 값을 지운 채로 두면 다음 기동에 강등된 것으로 보인다
        out["_schemaAhead"] = True
        print(f"[recipedb] schema {out['_schema']} > {SCHEMA} — 읽기 전용 (이 앱보다 새 버전이 만든 데이터)", flush=True)
        return out
    out = normalize(d)
    if _n(d.get("_schema")) < SCHEMA:
        try:
            save(out)
            print(f"[recipedb] migrated schema {_n(d.get('_schema'))} -> {SCHEMA} (sources added, data kept)", flush=True)
        except Exception as e:
            print(f"[recipedb] migration save failed: {type(e).__name__}: {e}", flush=True)
    return out


def _rebuild_from_cache() -> dict:
    """캐시(cache_*.json)는 CLI 응답 원본이라 손실 없이 DB 를 다시 만들 수 있다 — CLI 를 다시 부를 필요가 없다.
    캐시가 하나도 없으면 빈 DB (파일도 쓰지 않는다)."""
    bodies = {}
    at = 0.0
    for k in CACHE_KINDS:
        c = store.get_cache(k)
        if c.get("data") is not None:
            bodies[k] = c["data"]
            at = max(at, float(c.get("fetched_at") or 0))
    db = empty()
    if not bodies:
        return db
    merge(db, bodies, at or None)
    try:
        save(db)
        print(f"[recipedb] rebuilt from cache: {db['stats']}", flush=True)
    except Exception as e:
        print(f"[recipedb] rebuild save failed: {type(e).__name__}: {e}", flush=True)
    return db


class SchemaAhead(Exception):
    """저장 파일이 이 앱보다 새 스키마라 쓰지 않았다 (강등 방지)."""


def save(db: dict) -> None:
    if db.get("_schemaAhead"):   # 새 버전이 만든 데이터 — 옛 앱이 덮으면 모르는 섹션이 사라진다
        raise SchemaAhead(f"schema {_n(db.get('_schema'))} > {SCHEMA} — 저장하지 않습니다 (앱을 업데이트하세요)")
    db["_schema"] = SCHEMA
    store.save(FILE, db)


# ── 경로 매칭 ──
def _path_index(db: dict, kind: str) -> dict:
    """{이름: [(key, entry)…]} 를 한 번에 만든다 (경로 번호순).
    이름마다 recipes 전체를 훑으면 N개 행 병합이 O(N²) 가 된다 — 8,000개에 2.2초, 100,000개면 수 분간 잠금을 쥔 채 멈춘다(실측).
    커뮤니티 DB 는 사용자가 지정한 주소에서 받으므로 크기를 앱이 정할 수 없다."""
    idx: dict[str, list] = {}
    for k, e in db["recipes"].items():
        if isinstance(e, dict) and e.get("kind") == kind:
            idx.setdefault(e.get("name"), []).append((k, e))
    for lst in idx.values():
        lst.sort(key=lambda kv: _n(kv[1].get("variant"), 1))
    return idx


def _compat(row: dict, path: dict) -> int:
    """행의 재료 집합이 경로와 같은 레시피로 볼 수 있는가.
    0 = 다른 경로 / 1 = 한쪽이 비어 판단 불가(약하게 일치) / 2 = 같은 집합인데 수량만 다름(충돌로 기록) / 3 = 부분집합이고 수량 일치.
    부분집합을 요구하는 이유: 두 경로가 재료 하나를 공유하는 일이 흔하다(예: 특수강괴). 겹침만 보면 서로 붙어 버린다."""
    if not row or not path:
        return 1
    a, b = set(row), set(path)
    if not (a <= b or b <= a):
        return 0
    if all(row[n] == _n(path[n]) for n in a & b):
        return 3
    return 2 if a == b else 0


def _place(db: dict, kind: str, rows: list, now: float, mutate: bool, source: str = "self") -> list:
    """work.recipes() 행들을 경로에 배정한다. 돌려주는 것은 행과 같은 순서의 키 목록.
    mutate=True 면 DB 를 갱신(새 경로 생성·재료 합집합), False 면 읽기만(화면 스냅샷용).

    규칙:
    - 응답에 그 이름이 한 줄뿐이면: 기존 경로가 하나 이하일 때는 그 경로에 합치고(동명이 아닌 보통 레시피 — 부분 관찰을 합집합으로),
      경로가 여럿이면 재료 집합으로 매칭. 재료가 비어 온 한 줄(Reason 이 스킬·이동비용)은 첫 경로에 얹고 재료는 안 바꾼다.
    - 응답에 동명 행이 여럿이면 전부 서로 다른 경로다. 재료 있는 행을 먼저 재료 집합으로 매칭하고(한 번 배정된 경로는
      그 응답 안에서 다시 쓰지 않는다), 재료 빈 행은 남은 경로(재료 없는 경로 우선)로, 없으면 새 경로로 보낸다 —
      같은 응답에서 id 가 겹치면 화면이 선택·펼침 상태를 잃는다 (실측: 정령의 날개 부족으로 재료가 비어 온 동명 11쌍).
      재료 없는 경로(signature 없음)는 어떤 재료 집합과도 호환이라, 나중에 재료가 보이면 그 경로에 붙는다."""
    by_name: dict[str, list] = {}
    for i, r in enumerate(rows):
        by_name.setdefault(r["name"], []).append(i)
    keys: list = [None] * len(rows)
    index = _path_index(db, kind)   # 이름 → 경로 목록. 이름마다 전체를 훑지 않는다 (O(N²) 방지)
    for name, idxs in by_name.items():
        paths = list(index.get(name, ()))
        multi = len(idxs) > 1
        single = not multi and len(paths) <= 1
        claimed: set = set()
        ings_of = {i: {m["name"]: m["required"] for m in rows[i]["missing"] if m["required"] > 0} for i in idxs}
        for i in sorted(idxs, key=lambda i: not ings_of[i]):   # 재료 있는 행 먼저 — 빈 행이 남는 경로를 가져가게
            row, ings = rows[i], ings_of[i]
            k = None
            if not multi and (single or not ings):
                k = paths[0][0] if paths else None
                if k is None and mutate:
                    k = _new_path(db, kind, name, 1, row, now); paths.append((k, db["recipes"][k]))
            else:
                best = None
                for k2, e in paths:
                    if k2 in claimed:
                        continue
                    sig = e.get("ingredients") if isinstance(e.get("ingredients"), dict) else {}
                    sc = _compat(ings, sig) if ings else (2 if not sig else 1)   # 빈 행: 재료 없는 경로를 먼저 채운다
                    if sc and (best is None or sc > best[0]):
                        best = (sc, k2)
                if best:
                    k = best[1]; claimed.add(k)
                elif mutate:
                    n = max((_n(e.get("variant"), 1) for _, e in paths), default=0) + 1
                    k = _new_path(db, kind, name, n, row, now); paths.append((k, db["recipes"][k])); claimed.add(k)
            if k is None:   # 읽기 전용인데 DB 에 없다 (아직 observe 전) — 비어 있는 번호로 임시 배정 (응답 안 중복 없이)
                used = {k2 for k2, _ in paths} | claimed
                n = 1
                while key(kind, name, n) in used:
                    n += 1
                k = key(kind, name, n); claimed.add(k)
            elif mutate:
                _apply(db, k, row, ings, now, source)
            keys[i] = k
        if mutate:
            for k2, e in paths:   # 이 이름의 경로 수를 전부에 적어 둔다
                e["variants"] = len(paths)
    return keys


def _new_path(db: dict, kind: str, name: str, n: int, row: dict, now: float) -> str:
    k = key(kind, name, n)
    db["recipes"][k] = {"kind": kind, "name": name, "per": row["per"], "variant": n, "variants": n,
                        "ingredients": {}, "signature": [], "firstSeen": now, "lastSeen": now, "seen": 0,
                        "lastReason": "", "lastEmptyReason": "", "lastReady": False, "complete": False,
                        "sources": {}, "srcIng": {}, "confirmed": False}
    return k


def _confirmed(cur: dict) -> bool:
    """서로 다른 출처 둘 이상이 이 경로의 모든 재료 수량에 동의했는가. 재료가 없으면 False.
    한 사람의 관찰(self)만으로는 절대 True 가 되지 않는다 — 그게 이 필드의 뜻이다."""
    ing = cur.get("ingredients") if isinstance(cur.get("ingredients"), dict) else {}
    src = cur.get("srcIng") if isinstance(cur.get("srcIng"), dict) else {}
    if not ing:
        return False
    for name, req in ing.items():
        agree = sum(1 for s, seen in src.items() if isinstance(seen, dict) and _n(seen.get(name), -1) == _n(req))
        if agree < 2:
            return False
    return True


def _self_changed(cur: dict, row: dict, ings: dict) -> bool:
    """이번 내 관찰이 지난 내 관찰과 다른가 — 사유·가능 여부·재료(수량 포함)·회당 산출 중 하나라도."""
    if not _n(cur.get("seen")):
        return True                     # 첫 관찰
    last_ing = (cur.get("srcIng") or {}).get("self")
    if ings and last_ing != ings:
        return True
    if bool(row["ready"]) != bool(cur.get("lastReady")) or _n(row["per"]) != _n(cur.get("per")):
        return True
    if ings or not row["reason"]:            # 진짜 상태 관찰 — lastReason 과 견준다
        return row["reason"] != cur.get("lastReason")
    return row["reason"] != cur.get("lastEmptyReason")   # 사유만 있고 재료가 빈 관찰 — lastEmptyReason 과 견준다


def _apply(db: dict, k: str, row: dict, ings: dict, now: float, source: str = "self") -> None:
    """행을 경로에 반영: 재료는 합집합으로(수량이 다르면 큰 값 + 충돌 기록), 출처별 관찰 횟수·재료를 기록한다.
    lastSeen/seen/lastReason/lastReady 는 「내 게임 상태」라 self 관찰만 갱신한다 — 남의 관찰이 내 상태를 덮으면 안 된다."""
    cur = db["recipes"][k]
    if not isinstance(cur.get("ingredients"), dict):
        cur["ingredients"] = {}
    if not isinstance(cur.get("sources"), dict):
        cur["sources"] = {}
    if not isinstance(cur.get("srcIng"), dict):
        cur["srcIng"] = {}
    # **내 관찰(self)은 상태가 바뀔 때만 센다.** 화면이 30초마다 자동 갱신하므로
    # 같은 상태를 다시 읽은 것까지 세면 「관찰 n회」가 켜 둔 시간에 비례해 자랄 뿐 뜻이 없다. 남의 기여(source≠self)는
    # 한 건이 한 관찰이라 그대로 센다. lastSeen 은 늘 갱신한다 — 「언제 마지막으로 봤나」는 다른 질문이다.
    changed = source != "self" or _self_changed(cur, row, ings)
    if changed:
        cur["sources"][source] = _n(cur["sources"].get(source)) + 1
    if ings:
        si = cur["srcIng"].setdefault(source, {})
        if not isinstance(si, dict):
            si = cur["srcIng"][source] = {}
        for name, req in ings.items():
            si[name] = req   # 그 출처가 마지막으로 본 값
    if source == "self":
        cur["per"] = row["per"]
        cur["lastSeen"] = now
        if changed:
            cur["seen"] = _n(cur.get("seen")) + 1
        if ings or not row["reason"]:   # 재료가 있거나 '가능'(사유 없음) 관찰 → 진짜 상태
            cur["lastReason"] = row["reason"]
        else:   # 사유만 있고 재료가 비어 온 관찰(스킬·이동비용) — 재료 있는 관찰의 사유를 덮지 않고 따로 남긴다
            cur["lastEmptyReason"] = row["reason"]
        cur["lastReady"] = bool(row["ready"])
    elif not _n(cur.get("per")):
        cur["per"] = row["per"]
    cur["complete"] = False
    cur.setdefault("firstSeen", now)
    cur.setdefault("variant", 1)
    for name, req in ings.items():
        old = cur["ingredients"].get(name)
        if old is None:
            cur["ingredients"][name] = req
        elif _n(old) != req:
            _conflict(db, k, name, _n(old), req, now)
            cur["ingredients"][name] = max(_n(old), req)   # 다르면 큰 값
    cur["signature"] = sorted(cur["ingredients"])
    cur["confirmed"] = _confirmed(cur)


def _conflict(db: dict, recipe: str, ing: str, old: int, new: int, now: float) -> None:
    """같은 경로 안에서 재료 필요량이 전에 본 것과 다르다 — 어느 쪽이 맞는지 모르므로 기록만 남긴다. 같은 쌍은 한 번만."""
    seen = sorted({old, new})
    for c in db["conflicts"]:
        if c.get("recipe") == recipe and c.get("ingredient") == ing and sorted(set(c.get("seen") or [])) == seen:
            c["at"] = now
            return
    db["conflicts"].append({"recipe": recipe, "ingredient": ing, "seen": seen, "at": now})
    if len(db["conflicts"]) > MAX_CONFLICTS:
        del db["conflicts"][:-MAX_CONFLICTS]


# ── 병합 ──
def _merge_recipes(db: dict, body, kind: str, now: float) -> None:
    _place(db, kind, work.recipes(body, kind), now, mutate=True)   # 중복 재료 합산·음수 클램프는 work 와 같은 규칙


def _merge_items(db: dict, body, now: float) -> None:
    # 개수는 위치별로 모아 최신값으로 덮어쓴다 — 한 sync 안에서 같은 이름이 위치별로 여러 줄 온다
    seen: dict[str, dict] = {}
    for it in _rows(body):
        name = _s(it.get("DisplayName"))
        if not name:
            continue
        e = seen.get(name)
        if e is None:
            e = seen[name] = {"category": _s(it.get("Category")), "categoryKo": _s(it.get("CategoryDisplayName")),
                              "where": {}, "locked": False}
        loc = _s(it.get("Location")) or "inventory"
        e["where"][loc] = e["where"].get(loc, 0) + _n(it.get("Count"))
        e["locked"] = e["locked"] or bool(it.get("IsLocked"))
        if not e["category"]:
            e["category"] = _s(it.get("Category"))
        if not e["categoryKo"]:
            e["categoryKo"] = _s(it.get("CategoryDisplayName"))
    for name, e in seen.items():
        cur = db["items"].get(name)
        if not isinstance(cur, dict):
            cur = db["items"][name] = {"firstSeen": now}
        cur.update({"category": e["category"] or cur.get("category", ""),
                    "categoryKo": e["categoryKo"] or cur.get("categoryKo", ""),
                    "count": sum(e["where"].values()), "where": e["where"], "locked": e["locked"], "lastSeen": now})
        cur.setdefault("firstSeen", now)


def _merge_gatherables(db: dict, body, now: float) -> None:
    for g in work.gatherables(body):
        cur = db["gatherables"].get(g["name"])
        if not isinstance(cur, dict):
            cur = db["gatherables"][g["name"]] = {"firstSeen": now}
        cur.update({"toolOk": g["toolOk"], "lastSeen": now})
        cur.setdefault("firstSeen", now)


def _merge_facilities(db: dict, body, now: float) -> None:
    for w in work.works(body if body is not None else {})["works"]:
        if not w["facility"]:
            continue
        cur = db["facilities"].get(w["facility"])
        if not isinstance(cur, dict) or not isinstance(cur.get("works"), list):
            cur = db["facilities"][w["facility"]] = {"works": [], "lastSeen": now}
        if w["name"] not in cur["works"]:
            cur["works"].append(w["name"]); cur["works"].sort()
        cur["lastSeen"] = now


def _merge_currencies(db: dict, body, now: float) -> None:
    for c in _rows(body):
        name = _s(c.get("DisplayName"))
        if not name:
            continue
        db["currencies"][name] = {"amount": _n(c.get("Amount")), "lastSeen": now}


def _rebuild_ingredients(db: dict) -> None:
    """파생 섹션: 재료 → 어떤 레시피가 쓰는지(역방향) + 공급 경로(재고 카테고리·채집·다른 레시피 산출).
    stock 은 가방(inventory)만이다 — 게임이 창고 물건을 보유로 세지 않는 것을 실측했다(창고에만 있는 재료 3,478행 전부 Owned=0).
    창고 수량은 storage 로 따로 준다 (옮기면 쓸 수 있다는 안내용)."""
    made_by: dict[str, list] = {}
    for k, r in db["recipes"].items():
        made_by.setdefault(r.get("name", ""), []).append(k)
    ing: dict[str, dict] = {}
    for k in sorted(db["recipes"]):
        for name, need in (db["recipes"][k].get("ingredients") or {}).items():
            e = ing.setdefault(name, {"usedBy": [], "need": {}, "category": None, "gatherable": False,
                                      "madeBy": [], "stock": None, "storage": None})
            e["usedBy"].append(k)
            e["need"][k] = _n(need)
    for name, e in ing.items():
        it = db["items"].get(name)
        if isinstance(it, dict):
            e["category"] = it.get("categoryKo") or it.get("category") or None
            where = it.get("where") if isinstance(it.get("where"), dict) else {}
            e["stock"] = _n(where.get("inventory"))
            e["storage"] = sum(_n(v) for loc, v in where.items() if loc != "inventory")
        e["gatherable"] = name in db["gatherables"]
        e["madeBy"] = sorted(made_by.get(name, []))
    db["ingredients"] = ing


def _stats(db: dict, now: float) -> None:
    recs = [e for e in db["recipes"].values() if isinstance(e, dict)]
    by = {s: sum(1 for e in recs if _n((e.get("sources") or {}).get(s))) for s in SOURCES}
    db["stats"] = {"recipes": len(db["recipes"]), "ingredients": len(db["ingredients"]), "items": len(db["items"]),
                   "gatherables": len(db["gatherables"]), "facilities": len(db["facilities"]),
                   "currencies": len(db["currencies"]), "conflicts": len(db["conflicts"]), "updatedAt": now,
                   "bySource": by, "confirmed": sum(1 for e in recs if e.get("confirmed"))}


def merge(db: dict, bodies: dict, now: float | None = None) -> dict:
    """순수 병합 (파일 I/O 없음). bodies 키: craft / alter / works / items / gather / currencies (없는 건 건너뜀).
    어떤 입력에도 던지지 않는다 — 깨진 섹션은 그 섹션만 건너뛴다."""
    now = time.time() if now is None else now
    if not isinstance(bodies, dict):
        bodies = {}
    steps = (("craft", lambda b: _merge_recipes(db, b, "craft", now)),
             ("alter", lambda b: _merge_recipes(db, b, "alter", now)),
             ("items", lambda b: _merge_items(db, b, now)),
             ("gather", lambda b: _merge_gatherables(db, b, now)),
             ("works", lambda b: _merge_facilities(db, b, now)),
             ("currencies", lambda b: _merge_currencies(db, b, now)))
    for k, fn in steps:
        if k not in bodies or bodies[k] is None:
            continue
        try:
            fn(bodies[k])
        except Exception as e:   # 한 섹션이 깨져도 나머지는 살린다
            print(f"[recipedb] {k} merge failed: {type(e).__name__}: {e}", flush=True)
    _rebuild_ingredients(db)
    _stats(db, now)
    return db


def observe(bodies: dict, now: float | None = None) -> dict:
    """sync 가 부른다: 읽고 → 합치고 → 저장. 저장 실패는 sync 를 막지 않는다."""
    with _lock:
        db = load()
        merge(db, bodies, now)
        try:
            save(db)
        except Exception as e:
            print(f"[recipedb] save failed: {type(e).__name__}: {e}", flush=True)
        return db


def assign(db: dict, kind: str, rows: list) -> list:
    """화면 스냅샷용: work.recipes() 행마다 id·variant·variants·known 을 채운다 (DB 는 바꾸지 않는다).
    observe 와 같은 매칭이라 sync 직후의 스냅샷은 DB 키와 정확히 맞는다."""
    try:
        keys = _place(db, kind, rows, 0.0, mutate=False)
    except Exception as e:   # 어떤 DB 모양에도 화면은 살린다
        print(f"[recipedb] assign failed: {type(e).__name__}: {e}", flush=True)
        keys = [key(kind, r["name"], 1) for r in rows]
    for r, k in zip(rows, keys):
        e = db.get("recipes", {}).get(k)
        e = e if isinstance(e, dict) else {}
        r["id"] = k
        r["variant"] = _n(e.get("variant"), _n(k.rsplit("#", 1)[-1], 1))
        r["variants"] = _n(e.get("variants"), 1)
        r["known"] = known_by_id(db, k)
        r["knownCount"] = len(r["known"])
    return rows


def known_by_id(db: dict, k: str) -> dict:
    """확인된 재료 {이름: 필요량}. 화면의 제작/가공 행 펼침용."""
    r = db.get("recipes", {}).get(k)
    ing = r.get("ingredients") if isinstance(r, dict) else None
    return {a: _n(v) for a, v in ing.items()} if isinstance(ing, dict) else {}


def known(db: dict, kind: str, name: str, n: int = 1) -> dict:
    return known_by_id(db, key(kind, name, n))


def ingredient_source(db: dict, name: str) -> tuple:
    """재료를 어떻게 구하나: ("gather", None) 채집 가능 / ("craft"|"alter", 레시피 키) 다른 레시피의 산출 / (None, None) 모름.
    화면이 「큐에 담기」 버튼 종류를 정하는 데 쓴다. 채집이 되면 채집을 우선한다 (비용·시간이 덜 든다)."""
    e = db.get("ingredients", {}).get(name)
    if not isinstance(e, dict):
        return (None, None)
    if e.get("gatherable"):
        return ("gather", None)
    made = e.get("madeBy") or []
    if made:
        k = str(made[0])
        return (k.split(":", 1)[0] if ":" in k else None, k)
    return (None, None)


def source_index(db: dict) -> dict:
    """아이템 이름 → ["gather"|"craft"|"alter", 레시피키|None] — 그것을 **얻는 방법**.

    규칙은 ingredient_source 와 같다 (채집이 되면 채집을 우선, 아니면 그것을 산출하는 레시피).
    다른 점은 보는 범위다: ingredient_source 는 db["ingredients"] — 즉 **어떤 레시피의 재료로 한 번이라도
    관찰된 이름**만 답한다. 재고 화면은 재료가 아닌 완제품에도 「담기」를 붙여야 하므로 여기서는
    gatherables·recipes 전체를 본다. 기존 함수는 그대로 둔다 (부르는 쪽이 그 좁은 범위에 기대고 있다).

    모르는 이름은 **넣지 않는다**. 화면은 빠진 이름을 「—」(비활성)로 그린다 — 0 이나 「?」로 채우지 않는다.
    레시피 키는 정렬해 첫 경로를 고른다 (같은 DB 면 항상 같은 답이 나오게).
    """
    out: dict[str, list] = {}
    for name in db.get("gatherables") or {}:
        if name:
            out[str(name)] = ["gather", None]
    for k in sorted(db.get("recipes") or {}):
        e = db["recipes"][k]
        if not isinstance(e, dict):
            continue
        name = _s(e.get("name"))
        kind = k.split(":", 1)[0] if ":" in k else ""
        if not name or name in out or kind not in ("craft", "alter"):
            continue
        out[name] = [kind, k]
    return out


def public(db: dict) -> dict:
    """/api/recipes 응답 (내부 필드 제외). srcIng 은 출처별 원본 기록이라 크기만 키우므로 뺀다 — sources·confirmed 로 충분하다."""
    out = {k: db.get(k, {} if k != "conflicts" else []) for k in (*SECTIONS, "conflicts", "stats")}
    out["recipes"] = {k: {a: v for a, v in e.items() if a != "srcIng"} if isinstance(e, dict) else e
                      for k, e in out["recipes"].items()}
    if db.get("_schemaAhead"):   # 화면이 「읽기 전용」 줄을 띄운다
        out["schemaAhead"] = True
        out["schema"] = _n(db.get("_schema"))
    return out


# ── 기여·시드·커뮤니티 ──
def contribution(db: dict, app: str = "", catalog: str = "", now: float | None = None) -> dict:
    """내 관찰을 남에게 줄 수 있는 익명 페이로드로. 화이트리스트(CONTRIB_*_KEYS) 필드만 담는다.
    보유 개수·재고·창고·재화·캐릭터·사유(lastReason 은 내 보유 상태를 드러낸다)·시각(초 단위)·경로·토큰 — 전부 안 나간다.
    시각은 날짜로만 뭉갠다. 재료 수량(Required)은 게임 고정값이라 개인 정보가 아니다."""
    now = time.time() if now is None else now
    recipes = {}
    for k, e in db.get("recipes", {}).items():
        if not isinstance(e, dict) or not _s(e.get("name")):
            continue
        ing = e.get("ingredients") if isinstance(e.get("ingredients"), dict) else {}
        ing = {a: _n(v) for a, v in ing.items() if _s(a) and _n(v) > 0}
        recipes[k] = {"kind": _s(e.get("kind")), "name": _s(e.get("name")), "per": max(1, _n(e.get("per"), 1)),
                      "ingredients": ing, "signature": sorted(ing),
                      "variant": max(1, _n(e.get("variant"), 1)), "variants": max(1, _n(e.get("variants"), 1))}
    gath = {n: {"toolOk": bool(g.get("toolOk"))} for n, g in db.get("gatherables", {}).items()
            if isinstance(g, dict) and _s(n)}
    fac = {n: {"works": sorted(str(w) for w in (f.get("works") or []) if _s(w))}
           for n, f in db.get("facilities", {}).items() if isinstance(f, dict) and _s(n)}
    return {"format": CONTRIB_FORMAT, "app": str(app or ""), "day": time.strftime("%Y-%m-%d", time.localtime(now)),
            "catalog": str(catalog or ""), "recipes": recipes, "gatherables": gath, "facilities": fac}


CONTRIB_FORMATS = {CONTRIB_FORMAT}   # 읽을 수 있는 포맷. 새 판을 낼 때 옛 것도 읽으려면 여기에 남긴다


def is_contribution(payload) -> bool:
    """**정확히 아는 포맷만** 받는다. 앞자리(mobiworks-recipes/)만 보면 의미가 다른 /2·/99 를 /1 인 양 읽어
    엉뚱한 값이 관찰 DB 에 섞인다."""
    return (isinstance(payload, dict) and str(payload.get("format") or "") in CONTRIB_FORMATS
            and isinstance(payload.get("recipes"), dict))


def contribution_reject(payload) -> str:
    """is_contribution 이 거부한 이유 — 로그·응답에 쓴다."""
    if not isinstance(payload, dict):
        return "not_object"
    fmt = str(payload.get("format") or "")
    if not fmt:
        return "no_format"
    if fmt not in CONTRIB_FORMATS:
        return "format_unsupported"
    return "no_recipes"


def _payload_rows(recs: dict) -> dict:
    """기여 페이로드의 recipes 를 kind 별 work.recipes() 행 모양으로. 화이트리스트 밖 필드(Owned 등)는 읽지도 않는다."""
    by_kind: dict[str, list] = {}
    for k, e in recs.items():
        if not isinstance(e, dict):
            continue
        kind, name = _s(e.get("kind")), _s(e.get("name"))
        if kind not in ("craft", "alter") or not name:
            continue
        ing = e.get("ingredients") if isinstance(e.get("ingredients"), dict) else {}
        miss = [{"name": _s(a), "required": _n(v), "owned": 0, "short": _n(v)} for a, v in ing.items() if _s(a) and _n(v) > 0]
        by_kind.setdefault(kind, []).append({
            "kind": kind, "name": name, "per": max(1, _n(e.get("per"), 1)), "ready": False,
            "reason": "not_enough_ingredient" if miss else "", "reasonKo": "", "missing": miss, "blocked": False,
            "_variant": max(1, _n(e.get("variant"), 1))})
    for rows in by_kind.values():   # 같은 이름은 경로 번호순으로 — 매칭이 재료 집합 기준이라 순서는 보조일 뿐
        rows.sort(key=lambda r: (r["name"], r["_variant"]))
    return by_kind


def merge_external(db: dict, payload, source: str, now: float | None = None) -> dict:
    """외부 DB(seed / community / 다른 사용자의 기여)를 합집합으로 병합. 내 관찰은 절대 덮이지 않는다.
    items·currencies 는 받지 않는다(개인 정보이자 남의 것). 돌려주는 것: 이번 병합으로 늘어난 수."""
    now = time.time() if now is None else now
    if source not in SOURCES:
        source = "community"
    before = (len(db["recipes"]), sum(len(e.get("ingredients") or {}) for e in db["recipes"].values() if isinstance(e, dict)))
    if not is_contribution(payload):
        return {"ok": False, "error": "bad_format", "recipes": 0, "ingredients": 0}
    try:
        for kind, rows in _payload_rows(payload["recipes"]).items():
            _place(db, kind, rows, now, mutate=True, source=source)
    except Exception as e:   # 어떤 페이로드에도 던지지 않는다
        print(f"[recipedb] merge_external({source}) recipes failed: {type(e).__name__}: {e}", flush=True)
    gath = payload.get("gatherables")
    if isinstance(gath, dict):
        for n, g in gath.items():
            n = _s(n)
            if n and n not in db["gatherables"] and isinstance(g, dict):   # 내가 본 적 없는 것만 — toolOk 는 내 도구 사정이 우선
                db["gatherables"][n] = {"toolOk": bool(g.get("toolOk")), "firstSeen": now, "lastSeen": now, "source": source}
    fac = payload.get("facilities")
    if isinstance(fac, dict):
        for n, f in fac.items():
            n = _s(n)
            if not n or not isinstance(f, dict):
                continue
            cur = db["facilities"].get(n)
            if not isinstance(cur, dict) or not isinstance(cur.get("works"), list):
                cur = db["facilities"][n] = {"works": [], "lastSeen": now}
            for w in f.get("works") or []:
                if _s(w) and w not in cur["works"]:
                    cur["works"].append(str(w))
            cur["works"].sort()
    _rebuild_ingredients(db)
    _stats(db, now)
    after = (len(db["recipes"]), sum(len(e.get("ingredients") or {}) for e in db["recipes"].values() if isinstance(e, dict)))
    return {"ok": True, "source": source, "recipes": after[0] - before[0], "ingredients": after[1] - before[1],
            "stats": db["stats"]}


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_seed_file(path: str) -> dict:
    """앱에 묶인 시드(data/recipes.seed.json)를 한 번만 병합한다. 같은 파일(해시)은 다시 넣지 않는다 — 기동마다 관찰 횟수가 늘지 않게.
    시드가 없거나 깨졌으면 아무것도 안 한다."""
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "no_seed"}
    try:
        h = file_hash(path)
        with open(path, encoding="utf-8-sig") as f:
            payload = json.load(f)
    except Exception as e:
        print(f"[recipedb] seed unreadable: {type(e).__name__}: {e}", flush=True)
        return {"ok": False, "error": "unreadable"}
    with _lock:
        db = load()
        if h in db.get("seeds_applied", []):
            return {"ok": True, "applied": False, "hash": h}
        r = merge_external(db, payload, "seed")
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error"), "hash": h}
        db.setdefault("seeds_applied", []).append(h)
        try:
            save(db)
        except Exception as e:
            print(f"[recipedb] seed save failed: {type(e).__name__}: {e}", flush=True)
        print(f"[recipedb] seed applied: +{r['recipes']} recipes, +{r['ingredients']} ingredients ({h[:12]})", flush=True)
        return {"ok": True, "applied": True, "hash": h, **{k: r[k] for k in ("recipes", "ingredients")}}
