"""오버레이 악기 고르기 — 종류 탭 · 검색의 **모델** (창·tk 없이 계산만).

종류 탭과 검색어 입력으로 악기를 거른다.
그리는 쪽(overlay_tk)은 여기서 돌려준 목록을 그대로 그린다 — 거르는 규칙은 이 파일 한 곳에만 있다
(창을 띄우지 않고 단위 테스트로 확인할 수 있게 나눴다).

악기 이름은 게임이 준 원래 이름(「3화음 고결한 서약의 피아노」)이다. 종류는 `library.inst_kind` 로 뽑는다.
이미 「[피아노] …」 처럼 접두가 붙은 이름이 들어와도 그 접두를 종류로 읽고, 검색에서는 접두를 뺀다.
"""
from __future__ import annotations

import re

ALL = "전체"            # 첫 탭 — 거르지 않는다
OTHER = "그 외"         # 종류를 모르는 이름 (「기타」는 진짜 악기 이름이라 쓰지 않는다)

_PREFIX = re.compile(r"^\s*\[([^\]]+)\]\s*")


def _kind_of(name: str) -> str:
    try:
        import library                      # folio/library.py — 종류는 한 곳에서만 뽑는다
        return library.inst_kind(name)
    except Exception:
        return ""


def name_of(it) -> str:
    """목록 한 칸 → 이름. 문자열이든 `{"name": …}` 이든 받는다."""
    if isinstance(it, dict):
        return str(it.get("name") or "")
    return str(it or "")


def split(name: str) -> tuple[str, str]:
    """이름 → (종류, 접두 뺀 이름). 「[피아노] 고결한 서약」 → ("피아노", "고결한 서약")."""
    n = str(name or "")
    m = _PREFIX.match(n)
    if m:
        return m.group(1).strip(), n[m.end():]
    return _kind_of(n), n


def family(name: str) -> str:
    return split(name)[0] or OTHER


def families(insts) -> list[tuple[str, int]]:
    """종류와 개수 — **처음 나온 차례대로** (가나다로 다시 늘어놓지 않는다: 게임 목록 순서가 곧 사람 눈의 순서다)."""
    order: list[str] = []
    count: dict[str, int] = {}
    for it in insts or ():
        n = name_of(it)
        if not n.strip():
            continue
        f = family(n)
        if f not in count:
            order.append(f)
            count[f] = 0
        count[f] += 1
    return [(f, count[f]) for f in order]


def tabs(insts) -> list[tuple[str, int]]:
    """화면의 탭 줄 — 「전체」 + 종류들."""
    fams = families(insts)
    return [(ALL, sum(c for _, c in fams))] + fams


def _squash(s: str) -> str:
    return "".join(str(s or "").lower().split())


def matches(name: str, query: str) -> bool:
    """띄어쓴 낱말이 **모두** 들어 있으면 맞다 (대소문자 무시, 접두 제외).

    낱말은 띄어쓰기를 빼고도 한 번 더 본다 — 「고결한서약」 으로 쳐도 「고결한 서약」 이 나오게.
    """
    words = str(query or "").lower().split()
    if not words:
        return True
    bare = split(name)[1].lower()
    flat = _squash(bare)
    return all(w in bare or w in flat for w in words)


def filter_insts(insts, fam: str, query: str) -> list[str]:
    """탭(종류) + 검색으로 거른 이름 목록. 차례는 원래 목록 그대로."""
    out = []
    for it in insts or ():
        n = name_of(it)
        if not n.strip():
            continue
        if fam and fam != ALL and family(n) != fam:
            continue
        if matches(n, query):
            out.append(n)
    return out


def valid_tab(insts, fam: str) -> str:
    """고른 탭이 지금 목록에 없으면(악기를 버렸다 등) 「전체」로 돌아간다."""
    return fam if fam in {f for f, _ in tabs(insts)} else ALL


def move(hi: int, n: int, d: int) -> int:
    """↑/↓ — 목록 안에서만 움직인다 (양 끝에서 멈춘다)."""
    if n <= 0:
        return 0
    return max(0, min(n - 1, int(hi) + int(d)))


def keep_visible(off: int, hi: int, n: int, rows: int) -> int:
    """고른 줄이 화면 밖이면 보이는 자리까지 굴린다. 돌려주는 것은 새 첫 줄."""
    rows = max(1, int(rows))
    top = max(0, n - rows)
    off = max(0, min(int(off), top))
    if hi < off:
        off = hi
    elif hi >= off + rows:
        off = hi - rows + 1
    return max(0, min(off, top))


def scroll(off: int, n: int, rows: int, delta: int, step: int = 3) -> int:
    """휠 한 칸 — 위로 굴리면(delta > 0) 앞쪽으로 step 줄."""
    top = max(0, n - max(1, int(rows)))
    return max(0, min(top, int(off) - (1 if delta > 0 else -1) * step))
