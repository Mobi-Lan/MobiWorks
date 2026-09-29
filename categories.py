"""이름 규칙으로 추정하는 분류 — 채집 스킬, 레시피 분류.

CLI 는 채집 항목의 스킬도, 레시피의 분류도 주지 않는다(카탈로그에 그런 필드가 없다). 그래서 이름 패턴으로 **추정**한다.
틀릴 수 있으므로 API 응답에는 inferred:true 를 붙이고, 사용자가 `data/categories.json` 으로 덮어쓸 수 있다:
    {"gather": {"이름": "스킬"}, "recipe": {"이름": "분류"}}   — 규칙보다 우선.
규칙 표는 (정규식, 라벨) 순서 목록이고 위에서부터 첫 일치. 레시피는 등급 접미(SS/ZZ/S/Z)를 떼고 본다.
tools/recipebook.py 도 이 표를 import 해서 쓴다 (중복 제거).
"""
from __future__ import annotations

import json
import os
import re

# ── 채집 스킬 (이름 → 스킬) ──
GATHER_UNKNOWN = "미분류"
GATHER_RULES: list[tuple[str, str]] = [
    # 실데이터 177종으로 검증. 마감재·데코 부품·도구 상자·부러진/망가진 물건 등 "채집 가능"으로 잡히지만
    # 특정 생활 스킬로 묶기 어려운 것은 「잡동사니」— 추정이며, 실제 스킬은 게임에서 확인해 categories.json 으로 덮어쓸 수 있다
    (r"양털", "양털깎기"),
    (r"마감재|제작 부품|도구 상자|부러진|망가진|훼손된|축축한|빛바랜|도금이 벗겨진|오래된|스케치|조각$|편지|계약서|기도문|빈 병|응축된|얼룩덜룩한|윤이 나는", "잡동사니"),
    (r"통나무|나뭇가지|목재|수액|진액|송진|나무껍질|잎$|단풍", "벌목"),
    (r"광석|돌멩이|점토|괴$|주괴|결정|원석|모래|흑요석|석탄|보석|수정|금속|철$|은$|금$|규사|황$|얼음|돌$|부스러기|화석|구슬", "채광"),
    (r"나비|풍뎅이|하루살이|벌레|딱정벌레|잠자리|매미|반딧불|곤충|사마귀|귀뚜라미|나방|초파리|파리$|모기", "곤충 채집"),
    (r"잉어|송어|물고기|연어|참치|메기|붕어|장어|고등어|새우|게$|조개|오징어|문어|가재|피라미|열목어|농어|자루퍼|아귀|고기$|(?<!달걀|우유)어$", "낚시"),
    (r"밀$|밀\b|감자|달걀|우유|옥수수|당근|양파|호박|고구마|보리|쌀$|벼$|토마토|양배추|배추|무$|마늘|고추|콩$|사과|(?<!산)딸기|포도|과일|곡물|볏짚|짚|파스닙|헤이즐넛|넛$|밤$|꿀", "농사"),
    (r"허브|꽃|풀|버섯|클로버|줄기|열매|씨앗|잔디|이끼|덩굴|나물|약초|뿌리|잎사귀|거미줄|깃털|솜|목화|아마|면화|비단|누에|고치|가지$|산딸기|블루베리|라벤더|민트|장미|튤립|백합|해바라기|수국|안개꽃|물이 든 병|초$|베리", "채집"),
]

# ── 레시피 분류 (이름 → 분류) ──
GRADE_RE = re.compile(r"(SS|ZZ|S|Z)$")
PET_RE = r"^(토끼|라쿤|포메라니안|아기 여우|아기 곰|리트리버|아기 늑대|올빼미|시추|레서판다|아기 돼지|노르웨이 숲 고양이|잉글리시 쉽독|고양이|강아지|햄스터|게코 도마뱀)의 "
CRAFT_RULES: list[tuple[str, str]] = [
    (r"데코 상자|데코 트로피", "하우징"),
    (r"갑옷|전투복|투구$|상의$|하의$|장갑$|신발$", "방어구"),
    (PET_RE, "펫 장식"),
    (r"소드|대거|핼버드|너클|보우$|류트|하프|완드|케인|오브$|코일|스태프|단검|폴액스|도끼|접부채|클리버|글레이브|스틸레토|차크람|블레이드|페스카즈|방패", "무기"),
    (r"링$|네크리스|펜던트|목걸이|귀걸이|화관|머플러|리본|망토|티아라|핀$", "장신구"),
    (r"괭이|낫$|호미|가위|채집망|낚싯대|캠프파이어 키트", "도구"),
    (r"물약|붕대|비약|유탄|염색약|기폭제|간식", "소모품"),
    (r"홍차|달걀|대만찬|수프|스튜|구이|주스|볶음|샐러드|차$|밀크|콘치즈|퐁뒤|수플레|찜$|스테이크|올리오|케이크|빵$|밥$|두부|치즈|밀가루|오트밀|오일$|마요네즈|생크림|두유|찻잎|요리", "음식"),
    (r"목재|실$|천$|옷감|가죽$|괴$|못$|부품|도안|실크|밧줄|섬유", "재료"),
]
CRAFT_OTHER = "기타"
ALTER_RULES: list[tuple[str, str]] = [
    (r"괴$|괴\(", "금속"),
    (r"목재|막대", "목재"),
    (r"옷감|실크|가죽|면$|섬유|밧줄|실$", "직물·가죽"),
    (r"버섯|가루|진액|포자|풀 가루|꽃 가루|초 가루", "버섯·약재"),
    (r"결정|파편|기폭제", "결정"),
    (r"마요네즈|밀가루|치즈|생크림|콩$|두부|두유|고기|쌀$|밥$|찻잎|오일|오트밀|타르|아교", "식재료"),
]
ALTER_OTHER = "가공 기타"
CAT_ORDER = ["무기", "방어구", "장신구", "펫 장식", "도구", "소모품", "음식", "하우징", "재료", CRAFT_OTHER,
             "금속", "목재", "직물·가죽", "버섯·약재", "결정", "식재료", ALTER_OTHER]

_compiled: dict = {}


def _rules(table: list) -> list:
    k = id(table)
    if k not in _compiled:
        _compiled[k] = [(re.compile(p), label) for p, label in table]
    return _compiled[k]


def _first(table: list, name: str, other: str) -> str:
    for rx, label in _rules(table):
        if rx.search(name):
            return label
    return other


# ── 사용자 덮어쓰기 (data/categories.json) ──
def load_overrides(data_dir: str | None = None) -> dict:
    """{"gather": {...}, "recipe": {...}}. 없거나 깨지면 빈 표. 파일은 사용자가 손으로 만든다."""
    if data_dir is None:
        try:
            import store
            data_dir = store.DATA_DIR
        except Exception:
            return {"gather": {}, "recipe": {}}
    p = os.path.join(data_dir, "categories.json")
    try:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception:
        return {"gather": {}, "recipe": {}}
    g = d.get("gather") if isinstance(d, dict) else None
    r = d.get("recipe") if isinstance(d, dict) else None
    return {"gather": {str(k): str(v) for k, v in g.items()} if isinstance(g, dict) else {},
            "recipe": {str(k): str(v) for k, v in r.items()} if isinstance(r, dict) else {}}


def gather_skill(name: str, overrides: dict | None = None) -> str:
    """채집 항목 이름 → 스킬 (추정). 덮어쓰기 > 규칙 > 미분류."""
    name = name or ""
    ov = (overrides or {}).get("gather", {})
    if name in ov:
        return ov[name]
    return _first(GATHER_RULES, name, GATHER_UNKNOWN)


def recipe_category(name: str, kind: str, overrides: dict | None = None) -> str:
    """레시피 이름 → 분류 (추정). 등급 접미를 떼고 본다. 덮어쓰기 > 규칙 > 기타."""
    name = name or ""
    ov = (overrides or {}).get("recipe", {})
    if name in ov:
        return ov[name]
    base = GRADE_RE.sub("", name)
    if kind == "alter":
        return _first(ALTER_RULES, base, ALTER_OTHER)
    return _first(CRAFT_RULES, base, CRAFT_OTHER)


def classify(name: str, kind: str) -> str:
    """tools/recipebook.py 호환 이름."""
    return recipe_category(name, kind)


def counts(labels: list) -> list:
    """[{label, count}] — 개수 내림차순, 같으면 이름순. 필터 칩용."""
    c: dict[str, int] = {}
    for x in labels:
        c[x] = c.get(x, 0) + 1
    return [{"label": k, "count": v} for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]
