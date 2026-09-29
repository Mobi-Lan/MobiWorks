"""악보 라이브러리: 제목 정규화 → 아티스트 분류 → 색인·검색.

사용자가 직접 만드는 악보라 제목이 제각각이다. 파이프라인:
  1) clean_title: [ ] ( ) 【 】 「 」 장식, 잡음어(악보·ver·cover·완성본…), 기호·이모지 제거
  2) split: ' - ', '–', '/', '|', '_', 'by', ':' 등으로 아티스트/곡 분리 (숫자만인 쪽은 곡)
  3) classify: 수동 지정 > 별칭·아티스트 사전(이름이 제목 안에 통째로 있으면) > 구분자 분리 > 기타
     구분자로 뽑힌 아티스트는 사전에 합류해, 구분자 없는 '아이유 밤편지' 같은 제목도 2차로 잡는다.
재생은 원본 DisplayTitle 로 보내야 하므로 `title` 은 원본 그대로 둔다.
"""
from __future__ import annotations

import re
import unicodedata

TITLE_KEYS = ("DisplayTitle", "Title", "DisplayName", "Name")
NAME_KEYS = ("Name", "DisplayName", "Title")
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_CHO_MERGE = {"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
INDEX_ORDER = list("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ") + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]
_SEP = re.compile(r"[\s\-_·•.,()\[\]{}~!?/\\|:;\"'`+*&%$#@^=<>【】「」『』〈〉《》]+")
# 아티스트/곡 구분자 (우선순위 순). 공백 하이픈 계열 먼저, 그다음 붙은 것들.
_SPLITTERS = [" - ", " – ", " — ", " / ", " | ", " : ", " by ", " BY ", " -", "- ", "-", "–", "—", "_", "/", "|"]
# 기본 잡음어: 제목에서 떼어내도 곡 식별에 영향 없는 것들. data/artists.json 의 noise 로 덧붙일 수 있다.
DEFAULT_NOISE = [
    "악보", "ver", "ver.", "version", "버전", "cover", "커버", "remix", "리믹스", "편곡", "피아노", "piano", "ost",
    "full", "풀", "완성", "완성본", "미완", "미완성", "test", "테스트", "연습", "수정", "수정본", "final", "최종",
    "mr", "inst", "inst.", "원곡", "orig", "original", "short", "숏", "long", "롱", "easy", "쉬움", "hard",
    "v1", "v2", "v3", "v4", "v5", "1절", "2절", "무료", "공유", "배포", "자작", "자동", "auto", "복사본", "copy",
]
_NUMERIC = re.compile(r"^[\d\s.]+$")
_WORD_SPLIT = re.compile(r"[\s\-_·•.,()\[\]{}~!?/\\|:;\"'`+*&%$#@^=<>【】「」『』〈〉《》]+")
# 실측(193곡)에서 본 패턴: 전부 '악보: ' 접두, 개인 분류 태그(A_ C_ J_ K_ P_ #_ !_ @ 1. 5.), 파트 번호(-1 -2 -test)
_PREFIXES = [r"^악보\s*[:：]\s*", r"^[!@#$%*]+\s*", r"^[A-Za-z]_(?=\S)", r"^[!@#]_", r"^\d{1,2}\.\s*(?=\D)"]
# 카테고리어: 아티스트가 아니라 분류 태그. 제목에서 떼어 tags 로 보관한다.
CATEGORY_WORDS = ["동요", "영화", "뮤지컬", "애니", "애니메이션", "게임", "클래식", "jpop", "kpop", "j-pop", "k-pop", "ost",
                  "재즈버전", "재즈", "jazz", "피아노", "piano", "임시", "연습", "테스트", "test"]
_VARIANT_TAIL = re.compile(r"[\s\-_]*(?:-|_|\s)(\d{1,2}|\d+합|test|TEST|테스트|재즈|jazz|rock\.?ver|Rock\.ver)\s*$")
_ATTACHED_TAG = re.compile(r"(?:(?<=[가-힣])|(?<![A-Za-z]))(OST)\s*$")   # '라퓨타OST' 처럼 붙어 있는 꼬리 ('Ghost' 는 아님)


def pick(obj: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if isinstance(v, str) and v.strip():
            return v
    return ""


def norm(s: str) -> str:
    """검색·비교용: NFKC, 소문자, 구분자·기호 제거."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return _SEP.sub("", s)


def tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return [t for t in _WORD_SPLIT.split(s) if t]


def dup_key(s: str) -> str:
    """'백예린 - 0310' 과 '0310-백예린' 을 같게 보는 키."""
    return "|".join(sorted(tokens(s)))


def initial(s: str) -> str:
    for ch in (s or "").strip():
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            c = _CHO[(o - 0xAC00) // 588]
            return _CHO_MERGE.get(c, c)
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        if ch.isdigit():
            return "#"
        cat = unicodedata.category(ch)
        if ch.isspace() or cat[0] in "PSZMC":   # 따옴표·괄호·이모지·결합기호는 건너뛴다
            continue
        return "#"
    return "#"


# ── 1) 정리 ──
def _strip_symbols(s: str) -> str:
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith(("So", "Sk", "Cf")) or ch in "★☆♪♫♬♥❤✨":   # 이모지·장식 기호
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def clean_title(title: str, noise: list[str] | None = None) -> tuple[str, list[str], list[str], str]:
    """장식·잡음어를 떼고 (정리된 제목, 떼어낸 조각, 카테고리 태그, 변형 꼬리) 를 돌려준다. 전부 떼어져 비면 원본."""
    removed: list[str] = []
    tags: list[str] = []
    variant = ""
    t = unicodedata.normalize("NFKC", title or "").strip()
    t = _strip_symbols(t)
    # 접두 태그 ('악보: ', 'A_', '!_', '1.', '@' …)
    for pat in _PREFIXES:
        t2, n = re.subn(pat, "", t)
        if n:
            removed.append(t[: len(t) - len(t2)]); t = t2.strip()
    base = t   # 전부 떼어져 비면 여기로 돌아온다 ('악보: ' 접두는 뗀 상태)
    # 변형 꼬리 ('-1' '-2' '-test' '-재즈' '-6합' …) → variant. 같은 곡의 파트/버전이 한 곡으로 묶이게.
    # 숫자 꼬리는 '-'/'_' 로 붙은 경우('곡-1'), 또는 띄운 하이픈이라도 앞에 다른 구분자가 남는 경우('가수 - 곡 - 1')만.
    # 'Route 66' 'Taylor Swift - 22' 처럼 숫자가 곡 제목인 경우는 남긴다.
    m = _VARIANT_TAIL.search(t)
    if m and len(t) - len(m.group(0)) >= 2:
        head, tail = t[: m.start()], m.group(0)
        numeric = m.group(1).isdigit()
        attached = ("-" in tail or "_" in tail) and not re.search(r"\s[-_]\s", tail)
        if not numeric or attached or raw_left(head):
            variant = m.group(1); t = head.strip()
    # 붙은 꼬리 태그 ('천공의성 라퓨타OST', '인터스텔라OST')
    m = _ATTACHED_TAG.search(t)
    if m and len(t) - len(m.group(0)) >= 2:
        tags.append("OST"); t = t[: m.start()].strip(" -_·•.,:/")
    # 카테고리어 → tags. 맨 앞에서 _ - : / 로 이어지거나('동요_루돌프', '영화: 라라랜드'), 맨 끝 토큰일 때만
    # ('재즈 카페' '피아노 치는 소녀' 처럼 제목 한가운데 있는 낱말은 건드리지 않는다)
    for w in CATEGORY_WORDS:
        pat = re.compile(r"(?i)^" + re.escape(w) + r"\s*[_\-:/]+\s*|(?:^|(?<=[\s_\-:/(\[]))" + re.escape(w) + r"\s*$")
        if pat.search(t):
            t2 = pat.sub(" ", t).strip(" -_·•.,:/")
            if t2:
                tags.append(w.upper() if w.isascii() else w); t = t2
    # 괄호류 안의 내용은 대개 장식(버전·비고). 제목 전체가 괄호면 유지.
    def _br(m):
        inner = m.group(0)
        removed.append(inner)
        return " "
    for pat in (r"\[[^\]]*\]", r"\([^)]*\)", r"【[^】]*】", r"「[^」]*」", r"『[^』]*』", r"〈[^〉]*〉", r"《[^》]*》", r"\{[^}]*\}"):
        t2 = re.sub(pat, _br, t)
        if t2.strip():
            t = t2
        else:
            removed.pop() if removed else None
    words = set(w.lower() for w in (noise if noise is not None else DEFAULT_NOISE))
    # 잡음어는 꼬리 토큰만 뗀다 ('곡 cover', '곡 ver.2', '곡 - 완성본'). 'Mr. Brightside' 'Easy On Me' 처럼 제목 속 낱말은 유지.
    def _is_noise(w: str) -> bool:
        core = w.strip(" .-_()[]")
        return bool(core) and (core.lower() in words or bool(re.fullmatch(r"(?i)v\d+|ver\.?\d*|\d+절", core)))
    toks = t.split()
    while len(toks) > 1 and _is_noise(toks[-1]):
        removed.append(toks.pop())
    t = " ".join(toks).strip(" -_·•.,:;/|")
    if not t:
        # 전부 떼어져 비면: 잡음어 떼기 전 텍스트 → '악보: ' 접두만 뗀 원본 → 원본
        t = base.strip(" -_·•.,:;/|") or re.sub(_PREFIXES[0], "", (title or "").strip()).strip() or (title or "").strip()
    return t, removed, tags, variant


# ── 2) 분리 ──
def raw_left(cleaned: str) -> str:
    """구분자 왼쪽 조각 (빈도 세기용). 없으면 ''."""
    t = cleaned.strip()
    for sep in _SPLITTERS:
        if sep in t:
            a, b = t.split(sep, 1)
            if a.strip(" -_·•.,") and b.strip(" -_·•.,"):
                return a.strip(" -_·•.,")
    return ""


def split_artist(cleaned: str, numeric_artists: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    """(아티스트, 곡, 규칙). 못 나누면 ('', cleaned, '').
    numeric_artists: '0018' 처럼 숫자로만 된 이름이 왼쪽에 2곡 이상 반복되면 밴드 이름으로 인정한다."""
    t = cleaned.strip()
    for sep in _SPLITTERS:
        if sep in t:
            a, b = t.split(sep, 1)
            a, b = a.strip(" -_·•,"), b.strip(" -_·•.,")
            if not a or not b:
                continue
            if sep.strip().lower() == "by":   # '밤편지 by 아이유' → 오른쪽이 아티스트
                a, b = b, a
            if _NUMERIC.match(a) and norm(a) in numeric_artists:
                return a, b, "split"
            # 숫자만인 쪽은 곡 제목(0310 같은 것) → 다른 쪽이 아티스트
            if _NUMERIC.match(a) and not _NUMERIC.match(b):
                return b, a, "split"
            if _NUMERIC.match(b) and not _NUMERIC.match(a):
                return a, b, "split"
            if _NUMERIC.match(a) and _NUMERIC.match(b):
                return "", t, ""
            # 너무 긴 쪽은 아티스트가 아니다 (문장형 제목)
            if len(a) > 24 and len(b) <= 24:
                return b, a, "split"
            # '내나이50에다시배우는악기' 같은 개인 태그: 글자+숫자 섞인 긴 덩어리는 아티스트로 안 본다
            if len(a) >= 8 and re.search(r"\d", a) and re.search(r"[가-힣A-Za-z]", a) and not _NUMERIC.match(a):
                return "", b, "tag"
            return a, b, "split"
    return "", t, ""


# ── 3) 분류 ──
class ArtistIndex:
    """수동 아티스트·별칭·지정 + 자동 추출 아티스트를 합친 사전."""

    def __init__(self, state: dict):
        self.artists: dict[str, dict] = {a["id"]: a for a in state.get("artists", [])}
        self.assign: dict[str, str] = dict(state.get("assign", {}))          # 원본 제목 → artistId
        self.assign_norm: dict[str, str] = {norm(t): a for t, a in self.assign.items()}   # 공백·기호만 다른 제목도 같은 지정
        self.noise: list[str] = list(DEFAULT_NOISE) + list(state.get("noise", []))
        self.by_norm: dict[str, str] = {}                                     # norm(이름/별칭) → artistId
        for a in self.artists.values():
            for nm in [a["name"], *a.get("aliases", [])]:
                if nm and nm.strip():
                    self.by_norm[norm(nm)] = a["id"]
        self.auto: dict[str, str] = {}                                        # norm(자동 추출 이름) → 표시 이름

    def lookup(self, name: str) -> str | None:
        return self.by_norm.get(norm(name))

    def display(self, artist_id: str) -> str:
        return self.artists.get(artist_id, {}).get("name", artist_id)

    def find_in_title(self, cleaned: str) -> tuple[str | None, str, tuple[int, int]]:
        """제목의 '토큰 경계' 안에 사전의 이름이 통째로 들어 있으면 (아티스트, 규칙, (시작, 끝)).
        'IU' 가 'Aquarium' 속 iu 에 걸리지 않게 이름은 연속된 토큰 묶음과 통째로 같아야 한다. 긴 이름 우선."""
        spans = [(m.start(), m.end(), norm(m.group(0))) for m in re.finditer(r"[^\s\-_·•.,()\[\]{}~!?/\\|:;\"'`+*&%$#@^=<>【】「」『』〈〉《》]+", cleaned)]
        spans = [x for x in spans if x[2]]
        if not spans:
            return None, "", (0, 0)
        cands: dict[str, tuple[int, int]] = {}
        for i in range(len(spans)):
            key = ""
            for j in range(i, min(len(spans), i + 5)):
                key += spans[j][2]
                cands.setdefault(key, (spans[i][0], spans[j][1]))
        best: tuple[int, str, str, tuple[int, int]] | None = None
        for key, aid in self.by_norm.items():
            if key in cands and (best is None or len(key) > best[0]):
                best = (len(key), aid, "dict", cands[key])
        if best:
            return best[1], best[2], best[3]
        for key, disp in self.auto.items():
            if len(key) >= 2 and key in cands and (best is None or len(key) > best[0]):
                best = (len(key), disp, "auto", cands[key])
        if best:
            return best[1], best[2], best[3]
        return None, "", (0, 0)


def build(scores: list, artist_state: dict | None = None) -> list[dict]:
    """원본 배열 → UI 아이템. bucket = 'artist' | 'other'."""
    idx = ArtistIndex(artist_state or {})
    prelim: list[dict] = []
    exact: dict[str, int] = {}
    similar: dict[str, int] = {}
    # 사전 통과: 구분자 왼쪽에 숫자 이름이 2곡 이상 반복되면 밴드(0018)로 인정
    left_counts: dict[str, set[str]] = {}
    for raw in scores:
        t0 = pick(raw, TITLE_KEYS) if isinstance(raw, dict) else str(raw)
        c0 = clean_title(t0[:300], idx.noise)[0]
        a = raw_left(c0)
        if a and _NUMERIC.match(a):
            left_counts.setdefault(norm(a), set()).add(norm(c0))   # 같은 제목 두 벌은 한 곡
    numeric_artists = frozenset(k for k, c in left_counts.items() if len(c) >= 2)
    for i, raw in enumerate(scores):
        if not isinstance(raw, dict):
            raw = {"DisplayTitle": str(raw)}
        title = pick(raw, TITLE_KEYS)
        exact[title] = exact.get(title, 0) + 1
        cleaned, removed, tags, variant = clean_title(title[:300], idx.noise)   # 정규화 비용 상한 (원본 title 은 그대로 둔다)
        similar[dup_key(cleaned)] = similar.get(dup_key(cleaned), 0) + 1
        artist, song, rule = split_artist(cleaned, numeric_artists)
        # '곡 - 아티스트' 처럼 뒤집힌 제목: 오른쪽만 등록된 이름이면 바꿔 준다
        if rule == "split" and idx.lookup(song) and not idx.lookup(artist):
            artist, song = song, artist
        prelim.append({"i": i, "raw": raw, "title": title, "cleaned": cleaned, "removed": removed, "tags": tags, "variant": variant,
                       "artist_guess": artist, "song": song, "rule": rule})
    # 구분자로 뽑힌 아티스트를 자동 사전에 합류 (2회 이상이면 확신, 1회도 후보로)
    counts: dict[str, int] = {}
    disp: dict[str, str] = {}
    for p in prelim:
        if p["artist_guess"] and not idx.lookup(p["artist_guess"]):
            k = norm(p["artist_guess"])
            counts[k] = counts.get(k, 0) + 1
            disp.setdefault(k, p["artist_guess"])
    for k, c in counts.items():
        if len(k) >= 2:
            idx.auto[k] = disp[k]
    items: list[dict] = []
    seen_title: dict[str, int] = {}   # 동명 채번: 같은 제목이 여럿이면 목록 순서대로 1, 2, 3…
    for p in prelim:
        title = p["title"]
        seen_title[title] = seen_title.get(title, 0) + 1
        dup_no = seen_title[title] if exact.get(title, 1) > 1 else 0
        artist_id: str | None = None
        artist_name = ""
        rule = ""
        assigned = idx.assign.get(title) or idx.assign_norm.get(norm(title))
        if assigned and assigned in idx.artists:
            artist_id = assigned; artist_name = idx.display(artist_id); rule = "manual"
        elif p["artist_guess"] and idx.lookup(p["artist_guess"]):
            artist_id = idx.lookup(p["artist_guess"]); artist_name = idx.display(artist_id); rule = "dict"
        elif p["artist_guess"]:
            artist_name = idx.auto.get(norm(p["artist_guess"]), p["artist_guess"]); rule = "split"
        else:
            hit, r, span = idx.find_in_title(p["cleaned"])
            if hit:
                # 구분자 없이 이름이 들어 있던 경우: 매칭된 구간만 떼어 곡 제목으로. 이름이 제목 전부면(곡이 남지 않으면) 지정하지 않는다
                rest = (p["cleaned"][: span[0]] + " " + p["cleaned"][span[1]:]).strip(" -_·•.,:")
                if rest:
                    if r == "dict":
                        artist_id = hit; artist_name = idx.display(hit); rule = "dict"
                    else:
                        artist_name = hit; rule = "auto"
                    p["song"] = rest
        song = p["song"] or p["cleaned"]
        if rule == "manual" and artist_name and not p["artist_guess"] and song == p["cleaned"]:
            # 수동 지정인데 구분자가 없던 제목: 이름이 토큰 경계로 들어 있으면 떼어 준다
            _h, _r, span = idx.find_in_title(p["cleaned"])
            if _h == artist_id and span[1] > span[0]:
                rest = (p["cleaned"][: span[0]] + " " + p["cleaned"][span[1]:]).strip(" -_·•.,:")
                if rest:
                    song = rest
        akey = artist_id or (("auto:" + norm(artist_name)) if artist_name else "")
        items.append({
            "i": p["i"], "title": title, "key": item_key(title, dup_no), "dupNo": dup_no, "cleaned": p["cleaned"], "removed": p["removed"], "tags": p["tags"], "variant": p["variant"],
            "artist": artist_name, "artistKey": akey, "manual": rule == "manual",
            "song": song, "rule": rule or "none", "bucket": "artist" if artist_name else "other",
            "cliBroken": title != title.strip(),   # 제목 끝 공백: 게임 CLI 의 play_music_score 가 못 찾는다 (실측) — 게임에서 이름을 고쳐야 함
            "location": p["raw"].get("Location", ""), "locked": bool(p["raw"].get("IsLocked", False)),
            "initial": initial(song or title), "norm": norm(title) + " " + norm(p["cleaned"]),
            "dupExact": exact.get(title, 1), "dupSimilar": similar.get(dup_key(title), 1),
            "raw": p["raw"],
        })
    return items


def item_key(title: str, dup_no: int) -> str:
    """앱 내부 식별자. 제목이 유일하면 제목 그대로, 동명이면 '제목#n' (n 은 보관함 순서). 재생은 언제나 title 로 보낸다."""
    return f"{title}#{dup_no}" if dup_no else title


def key_title_guess(key: str) -> str:
    """보관함에 없는 key 의 제목 추정: '제목#숫자' 꼴일 때만 꼬리를 뗀다 (제목 자체에 '#' 이 있는 악보를 자르지 않게)."""
    m = re.fullmatch(r"(.*)#\d+", key)
    return m.group(1) if m else key


def key_map(items: list[dict]) -> dict[str, str]:
    """key → 원본 제목."""
    return {it["key"]: it["title"] for it in items}


def resolve_key(key: str, title: str, items: list[dict]) -> str:
    """저장된 key 가 지금 보관함에 없으면(예전 형식 '제목', 또는 '제목#3' 인데 2장만 남음) 같은 제목의 첫 악보로 잇는다. 없으면 그대로."""
    keys = {it["key"] for it in items}
    if key in keys:
        return key
    for it in items:
        if it["title"] == (title or key):
            return it["key"]
    return key


# 악기 종류 — 게임은 이름만 준다(Name 하나뿐). 실측 목록(보유 악기 23개 + 최근 재생 기록)을 보면
# **종류가 늘 마지막 낱말**이다:
#   류트 · 3화음 류트 · 2화음 만돌린 · 3화음 폼폼푸린의 스위트 푸딩 바이올린 · 3화음 토파즈 하버 통기타 …
# 실측에서 실제로 나온 종류(11): 류트·만돌린·플루트·바이올린·피아노·실로폰·하프·심벌즈·통기타·하모니카·샬루모.
# 나머지는 오버레이가 쓰던 목록(게임 악기 종류)을 그대로 둔 것 — 실측에 없다고 지우면 그 악기를
# 가진 사람 화면에서 접두가 빠진다. **모르는 이름은 접두 없이** 원래 이름 그대로 (「[?]」 를 붙이지 않는다).
# 「통기타」가 「기타」보다 먼저 걸리도록 긴 것부터 본다.
INST_KINDS = ("통기타", "우쿨렐레", "하모니카", "아코디언", "오카리나", "클라리넷", "백파이프",
              "바이올린", "비브라폰", "탬버린", "트럼펫", "색소폰", "만돌린", "실로폰", "샬루모",
              "마림바", "심벌즈", "플루트", "피아노", "오보에", "베이스", "휘슬", "첼로", "비올라",
              "드럼", "기타", "하프", "류트", "리라", "튜바")
_KINDS_LONGEST_FIRST = tuple(sorted(INST_KINDS, key=len, reverse=True))


def inst_kind(name: str) -> str:
    """악기 이름에서 **무슨 악기인지**만 뽑는다 (「3화음 문라이트 팜 하프」 → 「하프」).

    긴 이름이 목록에서 잘려 무슨 악기인지 안 보인다는 말을 듣고 만들었다 —
    앞에 [하프] 처럼 붙여 두면 뒤가 잘려도 종류는 남는다.

    이름 **끝**에서 가장 긴 일치를 먼저, 없으면 이름 **안**에서 가장 긴 일치를 본다.
    둘 다 없으면 빈 문자열 — 마지막 낱말을 지어서 종류라고 하지 않는다 (예전엔 그랬다).
    """
    n = str(name or "").strip()
    if not n:
        return ""
    for k in _KINDS_LONGEST_FIRST:
        if n.endswith(k):
            return k
    for k in _KINDS_LONGEST_FIRST:
        if k in n:
            return k
    return ""


def inst_label(name: str) -> str:
    """화면에 보이는 악기 이름 — 「[바이올린] 3화음 폼폼푸린의 스위트 푸딩 바이올린」.

    「3화음」·끝 공백 같은 것은 원래 이름에 남긴다. 종류를 모르면 접두 없이 원래 이름.
    화면(JS) 쪽 `MFInst.label()` 과 같은 모양이어야 한다 — 종류는 여기서만 뽑고(`type` 필드),
    화면은 그 값을 받아 붙이기만 한다.
    """
    name = str(name or "")
    if not name.strip():
        return ""
    k = inst_kind(name)
    return f"[{k}] {name}" if k else name


def build_instruments(instruments: list) -> list[dict]:
    out = []
    for i, raw in enumerate(instruments):
        if not isinstance(raw, dict):
            raw = {"Name": str(raw)}
        name = pick(raw, NAME_KEYS)
        out.append({"i": i, "name": name, "norm": norm(name), "equipped": bool(raw.get("IsEquipped", False)),
                    "type": inst_kind(name),   # 화면이 「[종류] 이름」 으로 보일 때 쓴다 — 종류는 여기 한 곳에서만 뽑는다
                    "cliBroken": name != name.strip(),   # 이름 끝 공백: 게임 CLI 의 change_instrument 가 못 찾는다 (실측)
                    "durability": raw.get("Durability"), "raw": raw})
    return out


def search(items: list[dict], q: str) -> list[dict]:
    qt = [norm(t) for t in tokens(q)]
    qt = [t for t in qt if t]
    if not qt:
        return items
    return [it for it in items if all(t in it["norm"] or t in norm(it.get("artist", "")) for t in qt)]


def artists_summary(items: list[dict]) -> list[dict]:
    """아티스트 색인: key, 이름, 곡 수, 수동/자동. 곡 수 내림차순."""
    agg: dict[str, dict] = {}
    for it in items:
        if it["bucket"] != "artist":
            continue
        k = it["artistKey"]
        a = agg.setdefault(k, {"key": k, "name": it["artist"], "count": 0, "manual": not k.startswith("auto:"), "initial": initial(it["artist"])})
        a["count"] += 1
    return sorted(agg.values(), key=lambda a: (-a["count"], a["name"]))


def summary(items: list[dict]) -> dict:
    by_initial: dict[str, int] = {}
    rules: dict[str, int] = {}
    for it in items:
        by_initial[it["initial"]] = by_initial.get(it["initial"], 0) + 1
        rules[it["rule"]] = rules.get(it["rule"], 0) + 1
    dups = sorted({it["title"] for it in items if it["dupExact"] > 1})
    other = sum(1 for it in items if it["bucket"] == "other")
    return {"count": len(items), "byInitial": by_initial, "rules": rules, "other": other,
            "artists": artists_summary(items), "duplicateTitles": dups}
