# -*- coding: utf-8 -*-
"""채집 퀘스트가 **살아 있는가** — 퀘스트 추적창을 읽어 판정한다.

## 왜 필요한가

게임 UI(거래소·인벤토리·팝업)가 열리면 커넥터 명령이 멈춘다. 그러면 돌고 있던 채집이
끊기는데, **CLI 는 「채집 중이다」를 알려 주는 명령이 없다.** 28개를 다 봐도 없다.
그래서 「아직 캐고 있나」를 알 수 있는 길은 **퀘스트 추적창**뿐이다.

## 판정은 셋이다 — 둘이 아니다

    살아 있다 · 끊겼다 · **모른다**

**「모른다」를 빼면 반드시 틀린다.** 카탈로그가 `get_quests` 에 대해 직접 적어 둔 말:

  · "Returns only **currently visible and executable** Quest Tracker items."
  · "Returns only items in the **active Quest Tracker tab**."

즉 **목록이 비었다는 것이 「퀘스트가 없다」를 뜻하지 않는다.** 다른 탭을 보고 있어도,
화면이 가려져 있어도 빈다. UI 가 막히면 퀘스트 줄이 사라졌다가 UI 를 닫으면 돌아오는 것으로 보인다(미검증).

이 저장소가 이미 적어 둔 규칙 그대로다 — **빈 결과는 두 가지 뜻**(없어서 빈 것 /
못 봐서 빈 것). 구분하지 않으면 **멀쩡히 캐는 중에 또 캔다.** 그건 날개를 태운다.

## 실물로 확인했다 (읽기 3회, 날개 0)

찻잎 채집을 걸어 둔 채로 `get_quests` 를 불러 봤다. 알게 된 것:

1. **첫 호출이 `ok=True` 인데 빈 목록이었고, 1초 뒤 같은 호출이 6줄을 줬다.**
   같은 상황에서 두 답이 나온 것이다 — **빈 목록이 답이 아니라는 것을 실측으로 봤다.**
2. 채집 퀘스트는 `Source: "shortcut"`, `QuestTitle: "채집물 탐색"` 으로 온다.
   **제목에는 물건 이름이 없다.** 이름은 목표 설명에 있다:
   `"<color=orange>찻잎</color> 채집 8/30"` — **색 태그가 섞여 있다.**
3. **`Count`/`Goal` 은 채집 진행도가 아니다.** 그 줄은 `Count=0 Goal=1` 인데 설명글은
   `8/30` 이라고 말한다. **진행도는 글자 안에 있다** — 1분 뒤 다시 보니 `12/30` 이었다.
4. `shortcut` 에는 채집과 무관한 줄도 있다(「연인에게 이동」). **출처만으로는 내 것을
   가릴 수 없다.**
5. **채집이 멈추면 그 줄은 사라진다** (실제 화면에서 확인). 이게 이 기능이 서는 기둥이다 —
   「표지는 보이는데 내 줄이 없다」가 **끊겼다**를 뜻한다. 줄이 남아 있는 채로 숫자만
   멈추는 모양이었다면 판정을 진행도 비교로 바꿔야 했다.

## 틀리면 어느 쪽으로 넘어지는가

표지 출처(`MARKER_SOURCES`)에서 **`shortcut` 은 일부러 뺐다.** 우리 채집 퀘스트가 바로
그 출처에 살기 때문이다 — 표지로 쓰면 **「내 퀘스트가 있어야 눈이 떠 있다」**가 되어
「끊겼다」를 영영 알아볼 수 없다. 순환이다.

남은 규칙도 추측이 섞여 있으므로, 틀리면 **「모른다」가 되도록** 짰다. 표지를 못 찾으면
눈이 감긴 것으로 보고 **아무것도 하지 않는다.** 반대로 짜면(못 찾으면 끊긴 것으로 보면)
추측이 틀린 순간 **멀쩡한 채집마다 날개를 5개씩 더 쓴다.**
"""
from __future__ import annotations

import re

# 추적창이 **살아 있다는 표지**로 삼는 출처. 「늘 떠 있는 것」이 하나라도 보이면
# 눈이 떠 있는 것으로 본다. 실측에서 본 출처 그대로이며, **`shortcut` 은 뺐다**
# (채집 퀘스트가 그 출처라 표지로 쓰면 순환이다 — 위 설명 참조).
MARKER_SOURCES = ("main", "pinned_sub", "auto_register_sub", "auto_register_candidate_sub")

# 게임 글자에 섞여 오는 색 태그 — `<color=orange>찻잎</color> 채집 8/30`
_TAG = re.compile(r"<[^>]*>")
# 설명글 끝의 진행도 — **여기가 진짜 숫자다** (`Count`/`Goal` 이 아니다)
_PROG = re.compile(r"(\d+)\s*/\s*(\d+)\s*$")

ALIVE, GONE, BLIND = "alive", "gone", "blind"


def _s(v) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def plain(v) -> str:
    """게임 글자에서 **색 태그를 벗긴다** — `<color=orange>찻잎</color> 채집 8/30` → `찻잎 채집 8/30`.

    이름 맞추기와 사람에게 보여 줄 글자 둘 다 이걸 쓴다. 안 벗기면 로그에 태그가 그대로
    찍히고, 무엇보다 **이름 한가운데에 태그가 끼면 조각 맞추기가 빗나간다.**"""
    return _TAG.sub("", _s(v)).strip()


def progress(row: dict):
    """그 줄이 말하는 **진행도** → `(한 것, 할 것)` | None.

    **`Count`/`Goal` 이 아니다.** 실측에서 채집 줄은 `Count=0 Goal=1` 인데 설명글은
    `8/30` 이라고 말했고, 1분 뒤 `12/30` 으로 올라갔다. 즉 **움직이는 숫자는 글자 안에
    있다.** (이 숫자는 **게임이 낸 의뢰의 목표**이지 우리 큐가 받은 목표가 아니다 —
    같은 값으로 착각하면 안 된다. 여기서는 「움직이고 있다」를 보는 데만 쓴다.)"""
    for o in (row.get("Objectives") or []):
        if not isinstance(o, dict):
            continue
        m = _PROG.search(plain(o.get("Description")))
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def entries(body) -> list:
    """회신에서 퀘스트 줄들을 꺼낸다.

    카탈로그는 `array of {...}` 라고 적었는데, 이 CLI 는 같은 자리를 어떤 명령에서는
    `{items: [...]}` 로 감싸 준다(`get_gatherable_items` 등). **어느 쪽이 올지 모르므로
    둘 다 받는다** — 모양을 하나로 못 박아 두면 다른 쪽이 왔을 때 조용히 빈 목록이 되고,
    빈 목록은 여기서 「모른다」라서 **기능이 영영 아무 일도 안 한 채 통과한다.**"""
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("quests") or body.get("items") or body.get("Quests") or []
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def eyes_open(rows: list) -> bool:
    """추적창을 **볼 수 있는 상태인가** (표지가 하나라도 보이는가)."""
    return any(_s(r.get("Source")) in MARKER_SOURCES for r in rows)


def texts(row: dict) -> list:
    """한 줄에서 이름이 나올 만한 글자 전부 — 제목과 목표 설명 (**태그를 벗긴 뒤**).

    실측에서 채집 줄의 제목은 「채집물 탐색」이라 **물건 이름이 없었다.** 이름은
    목표 설명에만 있다. 그래서 둘 다 본다."""
    out = [plain(row.get("QuestTitle"))]
    objs = row.get("Objectives")
    if isinstance(objs, list):
        out += [plain(o.get("Description")) for o in objs if isinstance(o, dict)]
    return [t for t in out if t]


def mine(rows: list, name: str) -> dict | None:
    """지금 캐는 물건(`name`)에 해당하는 줄을 찾는다 → 그 줄 | None.

    **이름 조각으로 찾는다.** 퀘스트 제목이 물건 이름을 그대로 쓰는지, 「○○ 채집」처럼
    쓰는지 모르기 때문이다. 조각 맞추기는 헐거운 판정이지만, 여기서 헐거운 쪽은
    **안전한 쪽**이다 — 남의 퀘스트를 내 것으로 잘못 보면 「살아 있다」가 되어
    **아무것도 안 하게** 된다."""
    n = _s(name).strip()
    if not n:
        return None
    for r in rows:
        if any(n in t for t in texts(r)):
            return r
    return None


def done(row: dict) -> bool:
    """이 줄이 **다 끝났는가.** 끝난 것을 「살아 있다」로 보면 다 캔 뒤에 또 캔다.

    **글자 속 진행도를 먼저 본다** — 실측에서 `Count`/`Goal` 은 채집 진행을 안 담고 있었다."""
    pr = progress(row)
    if pr is not None:
        return pr[0] >= pr[1] > 0
    objs = row.get("Objectives")
    if not isinstance(objs, list) or not objs:
        return False
    ok = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        if o.get("IsCompleted") is True:
            ok.append(True)
            continue
        c, g = o.get("Count"), o.get("Goal")
        ok.append(isinstance(c, (int, float)) and isinstance(g, (int, float)) and g > 0 and c >= g)
    return bool(ok) and all(ok)


def classify(body, name: str) -> dict:
    """회신 하나 → `{state, why, row}`. **CLI 를 부르지 않는다** (이미 받은 값만 본다).

    state: `alive` 아직 캐는 중 · `gone` 끊겼다 · `blind` 못 봤다(아무것도 하지 않는다)
    """
    rows = entries(body)
    if not rows:
        return {"state": BLIND, "row": None,
                "why": "퀘스트 추적창이 비어 보입니다 — 게임 UI 가 열려 있거나 다른 탭일 수 있습니다"}
    if not eyes_open(rows):
        return {"state": BLIND, "row": None,
                "why": "늘 떠 있어야 할 퀘스트가 안 보입니다 — 추적창을 제대로 못 읽고 있습니다"}
    row = mine(rows, name)
    if row is None:
        return {"state": GONE, "row": None, "why": f"「{name}」 채집 퀘스트가 추적창에 없습니다"}
    if done(row):
        return {"state": GONE, "row": row, "why": f"「{name}」 채집 퀘스트가 완료되었습니다"}
    pr = progress(row)
    at = f" ({pr[0]}/{pr[1]})" if pr else ""
    return {"state": ALIVE, "row": row, "why": f"「{name}」 채집이 이어지고 있습니다{at}"}
