# -*- coding: utf-8 -*-
"""큐 프리셋과 **공유 코드**.

두 가지를 한다:
  1. 지금 보드에 쌓인 것을 이름 붙여 저장했다가 다시 불러오기 (`data/presets.json`)
  2. 그 프리셋을 **글자 한 덩어리**로 바꿔 남에게 주고, 받아서 다시 푸는 것

## 공유 코드를 어떻게 만드는가

암호화가 아니라 **압축**이면 충분하다 — 외부 저장소(KV 등) 없이 자체 연산으로 만든다.
그래서 서버에 아무것도 저장하지 않는다 — 코드 안에 내용이 **전부** 들어 있다.

    MW1.<base64url(zlib(json))>.<crc32 8자리>

**구분자는 마침표다.** 처음에 `-` 로 썼다가 검사에서 바로 깨졌다 — base64url 의 글자에
`-` 와 `_` 가 들어 있어서 본문을 쪼개면 조각이 세 개가 아니다. 마침표는 그 알파벳에 없다.

`zlib` 과 `base64` 는 파이썬 표준 라이브러리다 (이 앱은 외부 패키지를 쓰지 않는다).
**암호가 아니다.** 열쇠가 전부 같으니 누구나 풀 수 있고, 그래서 비밀을 담으면 안 된다 —
담는 것은 「무엇을 몇 개 만들지」뿐이다.

## 받는 코드는 남이 만든 것이다 — 믿지 않는다

푸는 쪽에서 지키는 것 (아래 `decode`):
  · 코드 길이 상한 — 아주 긴 글자를 받아 메모리를 쓰지 않는다
  · **압축을 풀 때 상한**을 둔다 — 400바이트가 수백 MB 로 부푸는 자료(zip bomb)를 막는다
  · crc 로 잘린 코드·오타를 거른다. 사람이 「안 된다」가 아니라 「코드가 깨졌다」를 본다
  · 풀린 뒤에도 **모양을 하나하나 확인**한다 — 개수 상한, 이름 길이, 아는 종류만, 그룹 한 겹만
  · 불러오는 것은 결국 `Queue.add` 를 지난다. 재료·레시피 확인은 거기서 한 번 더 한다
"""
import base64
import binascii
import json
import zlib

VERSION = 1
PREFIX = "MW1"

MAX_CODE = 40_000        # 코드 글자 수 상한 (프리셋 200장이면 넉넉하다)
MAX_JSON = 512_000       # **압축을 푼 뒤** 허용하는 최대 바이트 — zip bomb 방어선
MAX_ITEMS = 200          # 루트 + 그룹 안을 합친 카드 수
MAX_NAME = 60
MAX_QTY = 1_000_000

# 종류 이름을 한 글자로 — 코드를 짧게. **양쪽이 같은 표를 본다**
T_OUT = {"gather": "g", "craft": "c", "alter": "a", "collect": "k", "group": "G",
         "play": "p", "notify": "b"}   # 연주·알림 카드 — p: s(곡)·l(목록)·d(모드) · b: x(글)·o(소리)
T_IN = {v: k for k, v in T_OUT.items()}


class BadCode(Exception):
    """코드를 풀지 못했다. `reason` 은 사람에게 그대로 보여 줄 짧은 이유."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _s(v, cap: int = MAX_NAME) -> str:
    return str(v)[:cap] if isinstance(v, str) else ""


def _n(v, default: int = 0) -> int:
    """숫자 강제. **NaN·Infinity 는 default** — `json.loads` 가 `1e999`·`NaN` 을 받아들이므로 남이 준 코드에
    그게 있으면 `int()` 가 OverflowError 로 죽어 500 이 났다. 불리언도 수량이 아니다."""
    if isinstance(v, bool):
        return default
    try:
        f = float(v) if isinstance(v, (int, float, str)) else None
    except (ValueError, OverflowError):   # OverflowError: 수천 자리 정수 (옛 파이썬은 json 이 그대로 읽는다)
        return default
    if f is None or f != f or f in (float("inf"), float("-inf")):
        return default
    return int(f)


# ── 보드 → 프리셋 ────────────────────────────────────────────────────────
def card_spec(it: dict):
    """카드 한 장을 프리셋용 최소 모양으로. **진행 상태·로그·id 는 담지 않는다** —
    프리셋은 「무엇을 할지」이지 「어디까지 했는지」가 아니다."""
    t = it.get("type")
    if t not in ("gather", "craft", "alter", "collect", "play", "notify"):
        return None
    d = {"t": T_OUT[t], "n": _s(it.get("name"))}
    if not d["n"]:
        return None
    if t == "play":
        d["d"] = _s(it.get("mode"), 8) or "resume"
        if it.get("song"):
            d["s"] = _s(it["song"], 200)
        if it.get("list"):
            d["l"] = _s(it["list"], 80)
        if d["d"] != "resume" and _n(it.get("count"), 1) > 1:   # 회차 (c) — 1 이면 적지 않는다 (기본)
            d["c"] = max(1, min(20, _n(it.get("count"), 1)))
        return d
    if t == "notify":
        d["x"] = _s(it.get("text"), 80)
        d["o"] = it.get("sound") is not False
        return d
    d["q"] = _n(it.get("target")) if t == "gather" else _n(it.get("count"), 1)
    if t in ("craft", "alter") and it.get("recipeId"):
        d["r"] = _s(it["recipeId"], 200)
    if t == "alter" and it.get("collect"):
        d["m"] = _s(it["collect"], 20)
    if t == "collect":
        d["f"] = _s(it.get("facility"))
    return d


def group_spec(g: dict) -> dict:
    kids = [c for c in (card_spec(x) for x in (g.get("items") or [])) if c]
    return {"t": "G", "n": _s(g.get("name")), "r": _n(g.get("repeat"), 1),
            "e": _s(g.get("onError"), 12) or "continue",
            "f": bool(g.get("retryFailed", True)), "w": bool(g.get("waitAlter")),
            "i": kids}


def capture(items: list, name: str) -> dict:
    """보드의 루트 목록을 프리셋으로. 그룹은 한 겹만이다 (보드가 애초에 그렇다)."""
    out = []
    for it in (items or []):
        if it.get("type") == "group":
            out.append(group_spec(it))
        else:
            c = card_spec(it)
            if c:
                out.append(c)
    return {"v": VERSION, "n": _s(name) or "이름 없는 프리셋", "i": out}


def count_cards(p: dict) -> int:
    n = 0
    for x in (p.get("i") or []):
        n += len(x.get("i") or []) if x.get("t") == "G" else 1
    return n


# ── 코드로 바꾸기 / 되돌리기 ──────────────────────────────────────────────
def encode(preset: dict) -> str:
    raw = json.dumps(preset, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    return "{0}.{1}.{2:08x}".format(PREFIX, body, zlib.crc32(raw) & 0xffffffff)


def decode(code: str) -> dict:
    """공유 코드를 프리셋으로. 못 풀면 `BadCode` — **이유를 사람 말로** 담는다.

    순서가 중요하다: 길이 → 모양 → 압축 해제(상한) → crc → 내용 검사.
    crc 를 압축 해제보다 **뒤**에 두는 이유는 crc 가 원본(json) 바이트에 대한 값이라서다.
    대신 압축 해제에 상한을 둬서, crc 를 보기 전에 메모리가 터지는 일은 없게 한다.
    """
    code = (code or "").strip()
    if not code:
        raise BadCode("코드가 비어 있습니다.")
    if len(code) > MAX_CODE:
        raise BadCode("코드가 너무 깁니다 ({0:,}자 · 상한 {1:,}자).".format(len(code), MAX_CODE))
    parts = code.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise BadCode("모비웍스 공유 코드가 아닙니다 (「{0}.」 로 시작해야 합니다).".format(PREFIX))
    body, crc_txt = parts[1], parts[2]
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        raise BadCode("코드가 깨졌습니다 — 복사할 때 일부가 빠졌는지 확인하세요.")
    try:
        d = zlib.decompressobj()
        raw = d.decompress(blob, MAX_JSON)
        if d.unconsumed_tail:            # 상한에 걸려 더 남았다 = 너무 큰 자료
            raise BadCode("코드 안의 내용이 너무 큽니다 (상한 {0:,}KB).".format(MAX_JSON // 1000))
        raw += d.flush()
    except zlib.error:
        raise BadCode("코드가 깨졌습니다 — 복사할 때 일부가 빠졌는지 확인하세요.")
    if len(raw) > MAX_JSON:
        raise BadCode("코드 안의 내용이 너무 큽니다 (상한 {0:,}KB).".format(MAX_JSON // 1000))
    if "{0:08x}".format(zlib.crc32(raw) & 0xffffffff) != crc_txt.lower():
        raise BadCode("코드가 깨졌습니다 — 끝까지 다 복사했는지 확인하세요.")
    try:
        p = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):   # RecursionError: `[[[[…` 수만 겹 — 남이 준 코드다
        raise BadCode("코드가 깨졌습니다 (내용을 읽지 못했습니다).")
    return validate(p)


def validate(p) -> dict:
    """푼 내용의 모양을 확인하고 **우리가 아는 것만 남긴 사본**을 돌려준다.
    남의 자료를 그대로 쓰지 않는다 — 모르는 항목은 조용히 버린다."""
    if not isinstance(p, dict):
        raise BadCode("코드 안의 내용이 프리셋이 아닙니다.")
    if _n(p.get("v")) != VERSION:
        raise BadCode("더 새로운 판에서 만든 코드입니다 (v{0}). 앱을 업데이트하세요.".format(_n(p.get("v"))))
    rows = p.get("i")
    if not isinstance(rows, list):
        raise BadCode("코드 안에 담긴 것이 없습니다.")
    out, total = [], 0
    for x in rows:
        if not isinstance(x, dict):
            continue
        if x.get("t") == "G":
            kids = []
            for k in (x.get("i") if isinstance(x.get("i"), list) else []):
                c = _card_in(k)
                if c and total < MAX_ITEMS:
                    kids.append(c)
                    total += 1
            out.append({"t": "G", "n": _s(x.get("n")) or "그룹",
                        "r": max(1, min(999, _n(x.get("r"), 1))),
                        "e": "stop" if x.get("e") == "stop" else "continue",
                        "f": bool(x.get("f", True)), "w": bool(x.get("w")), "i": kids})
        else:
            c = _card_in(x)
            if c and total < MAX_ITEMS:
                out.append(c)
                total += 1
    if not total:
        raise BadCode("코드 안에 담을 수 있는 작업이 없습니다.")
    return {"v": VERSION, "n": _s(p.get("n")) or "받은 프리셋", "i": out}


def _card_in(x):
    if not isinstance(x, dict):
        return None
    t = T_IN.get(x.get("t"))
    if t is None or t == "group":
        return None
    name = _s(x.get("n"))
    if not name:
        return None
    d = {"t": T_OUT[t], "n": name, "q": max(0, min(MAX_QTY, _n(x.get("q"), 1)))}
    if t in ("play", "notify"):   # 연주·알림 — 수량이 없다. 곡·목록·모드 / 글·소리만
        d.pop("q", None)
        for k, lim in (("d", 8), ("s", 200), ("l", 80), ("x", 80)):
            if isinstance(x.get(k), str):
                d[k] = _s(x[k], lim)
        if "o" in x:
            d["o"] = bool(x.get("o"))
        if t == "play" and _n(x.get("c"), 1) > 1:
            d["c"] = max(1, min(20, _n(x.get("c"), 1)))   # 연주 회차
        return d
    if isinstance(x.get("r"), str):
        d["r"] = _s(x["r"], 200)
    if isinstance(x.get("m"), str):
        d["m"] = _s(x["m"], 20)
    if isinstance(x.get("f"), str):
        d["f"] = _s(x["f"])
    return d


# ── 항목 한 장을 Queue.add 가 받는 모양으로 ───────────────────────────────
def to_item(c: dict) -> dict:
    t = T_IN.get(c.get("t"), "")
    it = {"type": t, "name": c.get("n", "")}
    if t == "play":
        it.update(mode=c.get("d") or "resume", song=c.get("s"), list=c.get("l"), title=c.get("n", ""),
                  count=max(1, min(20, _n(c.get("c"), 1))))
        return it
    if t == "notify":
        it.update(text=c.get("x") or c.get("n", ""), sound=c.get("o", True) is not False)
        return it
    if t == "gather":
        it["target"] = _n(c.get("q"))
    else:
        it["count"] = max(1, _n(c.get("q"), 1))
    if c.get("r"):
        it["recipeId"] = c["r"]
    if t == "alter" and c.get("m"):
        it["collect"] = c["m"]
    if t == "collect":
        it["facility"] = c.get("f", "")
    return it


# ── 저장소 ────────────────────────────────────────────────────────────────
FILE = "presets.json"
MAX_SAVED = 50


def load_all() -> list:
    import store
    d = store.load(FILE, [])
    return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []


def save_all(rows: list) -> None:
    import store
    store.save(FILE, rows[:MAX_SAVED])
