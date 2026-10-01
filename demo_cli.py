"""데모 모드용 가짜 CLI (MOBIW_DEMO=1) — 상태 있는 시뮬레이션.

게임도 CLI 도 없이 UI 와 큐 러너를 돌려 보기 위한 것. 응답 모양·에러 이름은 fixtures/capabilities.json 의 카탈로그 원문을 그대로 따른다.
필드명을 여기서 틀리면 실제 CLI 를 붙였을 때 조용히 빈 화면이 되므로, 모양을 바꿀 일이 있으면 docs/CLI.md 를 먼저 고친다.

상태: 가방(bag)·창고·무게·채집 도구 내구도·가공 대기열(works)·정령의 날개·진행 중 행동.
- execute_gathering: 회당 최대 100개, 도구 내구도 -1, 무게 +1/개. 내구도 0 이면 tool_broken, 무게가 차면 overweight (카탈로그 이름).
- execute_crafting: 재료 차감·산출 추가, 모자라면 not_enough_ingredient. craftCount 는 시설 상한(10)을 넘으면 invalid_count(maxCount).
- execute_altering: 대기열에 등록(ALTER_SEC 뒤 완료), complete_altering_work: 같은 시설의 완료분 전부 수령.
- 실행 명령은 회당 정령의 날개 5개 소모, 부족하면 not_enough_currency (카탈로그).
MOBIW_DEMO_FAST=1 이면 지연 0, 가공 완료 2초 (테스트용). set_state()/reset() 로 테스트가 상황을 만든다.
"""
from __future__ import annotations

import os
import threading
import time

FAST = os.environ.get("MOBIW_DEMO_FAST") == "1"
GATHER_DELAY = 0.0 if FAST else 1.5
ALTER_SEC = 2.0 if FAST else 30.0
MAX_PER_PASS = 100
CRAFT_MAX_COUNT = 10        # 시설별 craftCount 상한 (데모 값)
WINGS_PER_CALL = 5
ITEM_WEIGHT = 1.0

_LOCK = threading.RLock()

# 레시피: 이름 → (1회 산출, {재료: 필요량}). 가공은 시설도.
_CRAFT = {
    "장작": (3, {"통나무": 2}),
    "모닥불 키트": (1, {"장작": 2, "통나무": 1}),
    "고급 붕대": (2, {"고급 무명실": 4}),
    "철제 단검": (1, {"정제된 철괴": 3, "튼튼한 가죽": 2}),
    "여행자의 스튜": (2, {"신선한 고기": 2, "감자": 3}),
}
_ALTER = {
    "고급 무명실": (2, {"아마 섬유": 2}, "물레"),
    "튼튼한 가죽": (1, {"생가죽": 2}, "무두질 작업대"),
    "정제된 철괴": (1, {"철광석": 5}, "용광로"),
    "굵은 실": (3, {"아마 섬유": 6}, "물레"),
}
# 「달걀」은 **방해 경고를 게임 없이 볼 수 있게** 넣어 뒀다 (workqueue.GATHER_HAZARDS — 수탉).
_GATHERABLE = ["통나무", "철광석", "아마", "잉어", "산딸기", "생가죽", "달걀"]
_FISHING_ONLY = {"잉어"}

_INIT_BAG = {"통나무": 47, "철광석": 2, "아마 섬유": 6, "감자": 1, "고급 무명실": 1, "여행자의 스튜": 3, "고급 붕대": 12}
_INIT_STORAGE = {"튼튼한 가죽": 2}
_CATEGORY = {"여행자의 스튜": ("Food", "음식"), "고급 붕대": ("Consumable", "소모품")}
# 모비폴리오(연주) 쪽 데모 자료 — 모양은 fixtures/capabilities.json 카탈로그 원문 그대로:
#   get_instruments → [{Name, Durability, IsEquipped}] · get_music_scores → [{Location, DisplayTitle, IsCopyingAllowed, IsLocked}]
# 예전에는 이 명령들이 없어 데모에서 폴리오 동기화·재생이 전부 unknown_command 였다.
_INIT_INSTRUMENTS = [("류트", 100, True), ("바이올린", 87, False), ("플루트", 64, False)]
_INIT_SCORES = [("새벽의 노래", "inventory"), ("모닥불 곁에서", "inventory"), ("티르 코네일의 봄", "account_storage"),
                ("별바다 왈츠", "character_storage")]
DEMO_SCORE_SEC = 180        # 데모 곡 길이 (get_activity.Performance 의 TotalDurationSeconds)

S: dict = {}


def reset(fast: bool | None = None) -> None:
    """초기 상태로. fast 는 지연 없음(테스트)."""
    global GATHER_DELAY, ALTER_SEC
    if fast is not None:
        GATHER_DELAY = 0.0 if fast else 1.5
        ALTER_SEC = 2.0 if fast else 30.0
    with _LOCK:
        S.clear()
        S.update({
            "bag": dict(_INIT_BAG), "storage": dict(_INIT_STORAGE),
            "weight": 300.0, "max_weight": 1000.0,
            "tool": {n: 5 for n in _GATHERABLE}, "tool_missing": {"잉어"},   # 잉어는 낚싯대 없음 → ToolOk false
            "works": [], "wings": 41, "gold": 1_284_300,
            "in_progress": None, "pipe": "connected", "blocked_kind": None,
            # get_activity 가 보고하는 캐릭터 상태 — 테스트가 set_state(dead=True) 등으로 만든다
            "dead": False, "reviving": False, "combat": False, "dialog": False, "dungeon": "NotInDungeon", "hp": 1234,
            # 연주 중인가 (모비폴리오 쪽이 틀어 둔 곡). None 이거나
            # {"title":…, "instrument":…, "loop":bool, "ends_at":epoch|None}.
            # **여기가 없으면 중재 규칙을 검사할 수가 없다** — 실제 연주를 걸어 볼 수는 없으니까.
            "perf": None,
            # 폴리오: 가진 악기(이름 → [내구도, 장착]) · 악보 [(제목, 위치)]
            "instruments": {n: [d, eq] for n, d, eq in _INIT_INSTRUMENTS},
            "scores": list(_INIT_SCORES),
            # 재화 셋째 칸 — 화면은 골드 · 정령의 날개 · **데카** 3칸이다. 데모에 데카가 없어 두 칸만 보였다
            "deca": 62_029,
        })
        if not (FAST if fast is None else fast):
            # UI 데모 — 채집 1회를 9초 동안 30칸으로 돌려 가방이 도중에 오르게 한다. 러너는 3초마다 가방을 읽으므로
            # 한 회가 그보다 길어야 목표 도달 정지를 화면에서 볼 수 있다 (실물의 한 회는 몇 분이다)
            S.update(gather_ticks=30, gather_tick_sec=0.3)
        if not FAST and ALTER_SEC >= 30:   # UI 데모용 초기 대기열 (타이머 화면 확인)
            now = time.time()
            S["works"] = [{"name": "고급 무명실", "facility": "물레", "done_at": now + 25},
                          {"name": "튼튼한 가죽", "facility": "무두질 작업대", "done_at": now + 70},
                          {"name": "굵은 실", "facility": "물레", "done_at": now + 150},
                          {"name": "정제된 철괴", "facility": "용광로", "done_at": now + 900}]


def set_state(**kw) -> None:
    """테스트가 상황을 만든다: bag=, weight=, max_weight=, tool={이름:내구도}, wings=, pipe=, blocked_kind=, storage=,
    blocked_once=kind (다음 실행 명령 한 번만 blocked), collect_stop_once=True (다음 수령 한 번을 stopped_by_user·collected 0 으로 — 완료분은 남는다),
    autoplay=, autoplay_target= (get_activity.AutoPlayTarget),
    perf={"title":…, "loop":bool, "ends_at":epoch} (연주 중),
    fail_next={명령: [오류, …]} (그 명령의 다음 호출들을 차례로 그 오류로 — "disconnected" 는 exit 5, 그 밖은 body.error.
      "loading" 은 게임이 재접속·캐릭터 선택 중인 답, "ok" 는 그 호출만 그대로 통과), gather_yield=N (채집 1회에 실제로 가방에 드는 수 — 100 보다 적게),
    gather_reply=N (채집 회신의 gained 를 이 값으로 — 가방과 회신이 어긋나는 경우),
    gather_ticks=N (채집 1회를 N 칸으로 나눠 **도는 동안 가방이 오르게** — 칸마다 gather_tick_sec 초, 기본 0.02),
    gather_modes=["Hide","Stop",…] (칸마다 돌아가며 get_activity.Mode.MainButtonState — Hide 인 동안 stop_action 은 invalid_state),
    gather_stop_lag=N (stop_action 이 받아들여진 뒤 회신이 돌아오기까지의 칸 수, 기본 1).
    칸으로 도는 채집은 다른 실행 명령(제작·가공·수령·채집·연주)이 들어오면 error=canceled 로 끝난다 — 카탈로그의
    「canceled = 다른 명령이 이 행동을 대신했다」를 흉내낸 것이다 (**실물 미검증**)."""
    with _LOCK:
        for k, v in kw.items():
            if k in ("bag", "storage", "tool") and isinstance(v, dict):
                S[k].update(v)
            else:
                S[k] = v


reset()


def _perf_row() -> dict:
    """`get_activity` 의 Performance 칸 — 카탈로그가 적어 둔 이름 그대로.

    남은 시간은 **여기서 계산해 준다.** 실제 게임이 `RemainingSeconds` 를 주기 때문이고,
    우리 쪽 「연주 계속」이 그 값에 기대기 때문이다. 반복(IsLoop)이면 끝나지 않으므로
    남은 시간을 주지 않는다 — 기다리면 안 되는 경우를 흉내내는 자리다."""
    p = S.get("perf")
    if not p:
        return {"IsPlaying": False, "InstrumentName": "", "MusicTitle": "", "IsLoop": False}
    row = {"IsPlaying": True, "InstrumentName": p.get("instrument") or "류트",
           "MusicTitle": p.get("title") or "무제", "IsLoop": bool(p.get("loop")), "ChannelCount": 1}
    if not p.get("loop") and p.get("ends_at"):
        row["RemainingSeconds"] = max(0, int(round(p["ends_at"] - time.time())))
        row["TotalDurationSeconds"] = int(p.get("total") or row["RemainingSeconds"])
    return row


def _tool_ok(name: str) -> bool:
    return name not in S["tool_missing"] and S["tool"].get(name, 0) > 0


def _works_rows() -> list:
    now = time.time()
    out = []
    for w in S["works"]:
        left = max(0, int(round(w["done_at"] - now)))
        out.append({"DisplayName": w["name"], "FacilityName": w["facility"],
                    "State": "Completed" if left == 0 else "InProgress", "IsCompleted": left == 0, "RemainingSeconds": left})
    return out


def _missing(ings: dict) -> list:
    return [{"DisplayName": n, "Required": req, "Owned": S["bag"].get(n, 0)} for n, req in ings.items() if S["bag"].get(n, 0) < req]


def _pay() -> dict | None:
    if S["wings"] < WINGS_PER_CALL:
        return {"error": "not_enough_currency", "message": f"정령의 날개가 부족합니다 (필요 {WINGS_PER_CALL}, 보유 {S['wings']})"}
    S["wings"] -= WINGS_PER_CALL
    return None


def _cost() -> str:
    return f"정령의 날개 {WINGS_PER_CALL}개를 사용했습니다. 남은 수량 {S['wings']}개"


def _blocked() -> dict | None:
    # blocked_once: 다음 실행 명령 **한 번만** 거절한다 (부활 직후 사망 잔상 — 실측). 값은 kind 이름.
    once = S.pop("blocked_once", None)
    if once:
        return {"error": "blocked", "kind": once, "message": f"{once} (한 번만)"}
    if S.get("blocked_kind"):
        return {"error": "blocked", "kind": S["blocked_kind"], "message": f"{S['blocked_kind']} 를 닫아야 합니다"}
    return None


def _forced_fail(command: str):
    """set_state(fail_next={명령: [오류…]}) 로 걸어 둔 실패를 하나 꺼낸다 → (code, body) | None."""
    q = (S.get("fail_next") or {}).get(command)
    if not q:
        return None
    err = q.pop(0)
    if err in (None, "ok"):   # 이 호출은 그대로 통과 — 몇 번째 호출이 실패할지 고를 때
        return None
    if err == "disconnected":
        return 5, {"error": "disconnected", "message": "game_off"}
    if err == "loading":
        return 0, {"error": "loading", "message": "The game is loading (reconnecting or character select).", "loading": True}
    return 0, {"error": err, "message": f"demo {err}"}


def respond(command: str, body=None):
    """(exit code, 파싱된 응답) 을 돌려준다. 실제 CLI 와 같은 규약 (exit 0 이어도 body.error 면 실패).
    채집은 지연 동안 잠금을 놓는다 — 실제 CLI 처럼 그 사이 stop_action 이 들어올 수 있게 (진행 중 플래그가 지워지면 result=stopped)."""
    with _LOCK:
        forced = _forced_fail(command)
        ticked = command == "execute_gathering" and bool(S.get("gather_ticks"))
    if forced is not None:
        return forced
    if ticked:
        return _gather_ticked(body)
    if command == "execute_gathering" and GATHER_DELAY:
        with _LOCK:
            pre = _gather_pre(body)
        if pre is not None:
            return pre
        time.sleep(GATHER_DELAY)
        with _LOCK:
            return _gather_post(body)
    with _LOCK:
        return _respond(command, body)


def _gather_pre(body):
    """채집 사전 검사 + 결제 + 진행 중 표시. 거부면 (0, 에러) 를, 진행이면 None 을 돌려준다."""
    b = body if isinstance(body, dict) else {}
    if S["pipe"] != "connected":
        return 5, {"error": "disconnected", "message": "game_off"}
    name = str(b.get("displayName") or "")
    if name not in _GATHERABLE:
        return 0, {"error": "not_found", "message": f"채집 가능 목록에 없습니다: {name}"}
    if name in S["tool_missing"]:
        return 0, {"error": "tool_missing", "message": "도구가 없습니다"}
    if S["tool"].get(name, 0) <= 0:
        return 0, {"error": "tool_broken", "message": "도구 내구도가 0 입니다"}
    if S["weight"] >= S["max_weight"]:
        return 0, {"error": "overweight", "message": "가방 무게가 찼습니다"}
    e = _blocked() or _pay()
    if e:
        return 0, e
    if name in _FISHING_ONLY:
        S["in_progress"] = "fishing"
        return 0, {"result": "started", "message": "자동 낚시를 시작했습니다", "cost": _cost()}
    S["in_progress"] = "gathering"
    return None


def _preempt() -> None:
    """칸으로 도는 채집을 다른 실행 명령이 갈아치운다 — 도는 고리가 세대(gen)가 바뀐 것을 보고 canceled 로 끝낸다."""
    if S.get("in_progress") == "gathering" and S.get("gather_mode") is not None:
        S["gather_gen"] = int(S.get("gather_gen") or 0) + 1
        S["in_progress"] = None
        S["gather_mode"] = None


def _gather_ticked(body):
    """채집 1회를 gather_ticks 칸으로 — 칸마다 가방이 오르고(실측: 6 → 16 → 64), MainButtonState 가 gather_modes 를 돈다.
    stop_action 이 받아들여지면 gather_stop_lag 칸 뒤 result=stopped 로, 다른 실행 명령이 오면 error=canceled 로 끝난다."""
    with _LOCK:
        _preempt()
        pre = _gather_pre(body)
        if pre is not None:
            return pre
        name = str((body if isinstance(body, dict) else {}).get("displayName") or "")
        S["gather_gen"] = gen = int(S.get("gather_gen") or 0) + 1
        ticks = max(1, int(S["gather_ticks"]))
        modes = list(S.get("gather_modes") or ["Stop"])
        lag = max(0, int(S.get("gather_stop_lag", 1)))
        room = int((S["max_weight"] - S["weight"]) / ITEM_WEIGHT)
        total = min(MAX_PER_PASS, room)
        if S.get("gather_yield") is not None:
            total = min(total, max(0, int(S["gather_yield"])))
        S["gather_mode"] = modes[0]
        tick = float(S.get("gather_tick_sec", 0.02))
    added, stop_at = 0, None
    for i in range(ticks):
        time.sleep(tick)
        with _LOCK:
            if S.get("gather_gen") != gen:     # 다른 명령이 갈아치웠다
                return 0, {"error": "canceled", "message": "Another command replaced this action.",
                           "gained": added, "target": MAX_PER_PASS, "cost": _cost()}
            if S["in_progress"] is None:       # stop_action 이 받아들여졌다 — 몇 칸 뒤 돌아온다
                stop_at = i if stop_at is None else stop_at
                if i - stop_at >= lag:
                    S["gather_mode"] = None
                    S["tool"][name] -= 1
                    return 0, {"result": "stopped", "gained": added, "target": MAX_PER_PASS,
                               "message": "Gathering stopped before reaching the goal.", "cost": _cost()}
                continue
            want = round(total * (i + 1) / ticks) - added
            if want > 0:
                S["bag"][name] = S["bag"].get(name, 0) + want
                S["weight"] += want * ITEM_WEIGHT
                added += want
            S["gather_mode"] = modes[(i + 1) % len(modes)]
    with _LOCK:
        if S.get("gather_gen") != gen:
            return 0, {"error": "canceled", "message": "Another command replaced this action.",
                       "gained": added, "target": MAX_PER_PASS, "cost": _cost()}
        stopped = S["in_progress"] is None
        S["in_progress"] = None
        S["gather_mode"] = None
        S["tool"][name] -= 1
        if stopped:
            return 0, {"result": "stopped", "gained": added, "target": MAX_PER_PASS,
                       "message": "Gathering stopped before reaching the goal.", "cost": _cost()}
        return 0, {"result": "completed", "gained": added, "target": MAX_PER_PASS, "cost": _cost()}


def _gather_post(body):
    b = body if isinstance(body, dict) else {}
    name = str(b.get("displayName") or "")
    room = int((S["max_weight"] - S["weight"]) / ITEM_WEIGHT)
    gained = min(MAX_PER_PASS, room)
    if S.get("gather_yield") is not None:   # 소모품·채집지 사정으로 100개보다 적게 드는 회
        gained = min(gained, max(0, int(S["gather_yield"])))
    S["bag"][name] = S["bag"].get(name, 0) + gained
    S["weight"] += gained * ITEM_WEIGHT
    S["tool"][name] -= 1
    stopped = S["in_progress"] is None    # stop_action 이 그 사이 들어왔으면
    S["in_progress"] = None
    reply = gained if S.get("gather_reply") is None else int(S["gather_reply"])   # 회신이 가방과 어긋나는 경우
    if gained < MAX_PER_PASS and gained == room:
        return 0, {"error": "overweight", "message": "채집 도중 가방 무게가 찼습니다", "gained": reply, "target": MAX_PER_PASS, "cost": _cost()}
    gained = reply
    if stopped:
        return 0, {"result": "stopped", "gained": gained, "target": MAX_PER_PASS, "message": "정지됨", "cost": _cost()}
    return 0, {"result": "completed", "gained": gained, "target": MAX_PER_PASS, "cost": _cost()}


def _respond(command: str, body):
    if command == "status":
        return 0, {"pipe": S["pipe"], "reason": None if S["pipe"] == "connected" else "game_off"}
    if S["pipe"] != "connected":
        return 5, {"error": "disconnected", "message": "game_off"}
    b = body if isinstance(body, dict) else {}

    if command == "get_activity":   # 모양은 **실제 게임 응답**(fixtures/get_activity.real.json) — 평면. 카탈로그 예시의 combatState/dialogue 래퍼는 실측에 없다
        return 0, {"IsAutoPlaying": bool(S.get("autoplay")), "CanStartAutoPlay": True,
                   "AutoPlayTarget": S.get("autoplay_target") or "none",
                   "IsAutoTraveling": False, "AutoTravelRemainingPositionCount": 0,
                   "IsDead": bool(S.get("dead")), "IsReviving": bool(S.get("reviving")), "IsInCombat": bool(S.get("combat")),
                   "IsDialoguePlaying": bool(S.get("dialog")), "IsDialogueNextAvailable": False, "IsWaitingForSelection": bool(S.get("dialog")),
                   "Dungeon": {"State": S.get("dungeon") or "NotInDungeon", "IsBossBattleInProgress": False},
                   "Battlefield": {"IsInBattleField": bool(S.get("battlefield"))}, "IsAbyssResultSequencePlaying": False,
                   "Tutorial": {"IsPlaying": False}, "Scenario": {"IsInScenario": bool(S.get("scenario")), "IsSequencePlaying": False},
                   "Performance": _perf_row(),
                   "Interaction": {"HasTarget": False, "AvailableInteractionType": "None", "TargetKind": "None"},
                   "Mode": {"MainButtonState": ((S.get("gather_mode") or "Stop")
                                                if S.get("in_progress") == "gathering" else
                                                "Stop" if S.get("in_progress") else "Hide"),
                            "MountPartState": "Mounted" if S.get("mounted") else "None",   # set_state(mounted=True) — 탈것 탑승 중 흉내
                            "SitState": "None", "IsPlayingMiniGame": False, "IsHousingEditMode": False}}
    if command == "get_my_info":
        # 카탈로그: 모든 항목이 {DisplayName, Value} 객체이고 Vitals 안에 현재 체력이 있다.
        # **죽으면 체력 0**, 그 밖에는 남아 있다 — 사망/부활 깃발이 실제와 다를 때 이 값으로 가린다.
        hp = 0 if S.get("dead") else int(S.get("hp", 1234))
        return 0, {"Title": {"DisplayName": "칭호", "Value": "별바다 여행자"},
                   "Level": {"DisplayName": "레벨", "Value": 100},
                   "Vitals": {"HealthCurrent": {"DisplayName": "체력", "Value": hp},
                              "HealthMax": {"DisplayName": "최대 체력", "Value": 1500},
                              "SatietyValue": {"DisplayName": "포만도", "Value": 80}}}
    if command == "get_currencies":
        return 0, [{"DisplayName": "골드", "Amount": S["gold"]}, {"DisplayName": "정령의 날개", "Amount": S["wings"]},
                   {"DisplayName": "데카", "Amount": S["deca"]}, {"DisplayName": "두카트", "Amount": 980}]
    if command == "get_inventory":
        return 0, {"CurrentInventoryWeight": int(S["weight"]), "CurrentInventoryWeightAsDecimal": round(S["weight"], 1),
                   "MaxInventoryWeight": int(S["max_weight"]), "MaxInventoryWeightAsDecimal": S["max_weight"]}
    if command == "get_items":
        # 몸 {"name": X} — 실물은 비슷한 이름도 같이 준다(「통나무」 → 「부드러운 통나무」 포함, 실측). 부분 일치로 흉내낸다
        flt = str(b.get("name") or "") if isinstance(body, dict) else ""
        out = []
        for n, c in S["bag"].items():
            if c > 0:
                cat, catko = _CATEGORY.get(n, ("Ingredient", "재료"))
                out.append({"DisplayName": n, "Category": cat, "CategoryDisplayName": catko, "Count": c, "Location": "inventory", "IsLocked": False})
        for n, c in S["storage"].items():
            if c > 0:
                out.append({"DisplayName": n, "Category": "Ingredient", "CategoryDisplayName": "재료", "Count": c, "Location": "account_storage", "IsLocked": False})
        return 0, [x for x in out if not flt or flt in x["DisplayName"]]
    if command == "get_craftable_items":
        items = []
        for name, (per, ings) in _CRAFT.items():
            miss = _missing(ings)
            items.append({"DisplayName": name, "Craftable": not miss, "ProducedPerCraft": per,
                          "Reason": "" if not miss else "not_enough_ingredient", "MissingIngredients": miss})
        return 0, {"craftingUnlocked": True, "items": items}
    if command == "get_alterable_items":
        items = []
        for name, (per, ings, _f) in _ALTER.items():
            miss = _missing(ings)
            items.append({"DisplayName": name, "Alterable": not miss, "ProducedPerWork": per,
                          "Reason": "" if not miss else "not_enough_ingredient", "MissingIngredients": miss})
        return 0, {"items": items}
    if command == "get_altering_works":
        w = _works_rows()
        return 0, {"completedCount": sum(1 for x in w if x["IsCompleted"]), "works": w}
    if command == "get_gatherable_items":
        flt = body if isinstance(body, str) else ""
        return 0, {"items": [{"DisplayName": n, "ToolOk": _tool_ok(n)} for n in _GATHERABLE if not flt or flt.lower() in n.lower()]}
    if command == "capabilities":
        return 0, {"commands": []}
    # ── 모비폴리오(연주) — 조회는 빈 몸 = 전체, 글자가 오면 대소문자 무시 부분 일치 (카탈로그 Note) ──
    if command == "get_instruments":
        flt = (body if isinstance(body, str) else "").lower()
        return 0, [{"Name": n, "Durability": d, "IsEquipped": bool(eq)}
                   for n, (d, eq) in S["instruments"].items() if not flt or flt in n.lower()]
    if command == "get_music_scores":
        flt = (body if isinstance(body, str) else "").lower()
        return 0, [{"Location": loc, "DisplayTitle": t, "IsCopyingAllowed": True, "IsLocked": False}
                   for t, loc in S["scores"] if not flt or flt in t.lower()]
    if command == "change_instrument":
        name = str(b.get("name") or "").strip()
        if not name:
            return 0, {"status": "invalid_body", "error": "invalid_body", "message": "name 이 없습니다"}
        if name not in S["instruments"]:
            return 0, {"status": "rejected", "error": "not_found", "message": f"가진 악기가 아닙니다: {name}"}
        if S.get("perf"):
            return 0, {"status": "rejected", "error": "is_playing_instrument", "message": "연주 중에는 악기를 바꿀 수 없습니다"}
        if S.get("dead"):
            return 0, {"status": "rejected", "error": "not_available_on_dead", "message": "쓰러진 상태입니다"}
        for n in S["instruments"]:
            S["instruments"][n][1] = (n == name)
        return 0, {"status": "accepted"}
    if command == "play_music_score":
        title = str(b.get("title") or "").strip()
        if not title:
            return 0, {"status": "invalid_body", "error": "invalid_body", "message": "title 이 없습니다"}
        if title not in [t for t, _ in S["scores"]]:
            return 0, {"status": "rejected", "error": "not_found", "message": f"가진 악보가 아닙니다: {title}"}
        inst = next((n for n, (_d, eq) in S["instruments"].items() if eq), "")
        if not inst:
            return 0, {"status": "rejected", "error": "no_instrument", "message": "장착한 악기가 없습니다"}
        if S.get("dead"):
            return 0, {"status": "rejected", "error": "not_available_on_dead", "message": "쓰러진 상태입니다"}
        if S.get("combat"):
            return 0, {"status": "rejected", "error": "not_available_on_combat", "message": "전투 중입니다"}
        # 실제 게임처럼 곧바로 돌아오고, 연주는 get_activity.Performance 로 보인다 (stop_action 이 같은 슬롯을 멈춘다)
        _preempt()      # 칸으로 도는 채집은 연주가 갈아치운다 (미검증 — 흉내)
        S["perf"] = {"title": title, "instrument": inst, "loop": False,
                     "ends_at": time.time() + DEMO_SCORE_SEC, "total": DEMO_SCORE_SEC}
        return 0, {"status": "accepted"}
    if command == "stop_action":
        # 카탈로그: **현재의 멈출 수 있는 행동 하나**를 멈춘다 (연주·앉기·자동사냥·운반·채집).
        # 그래서 연주와 채집이 같은 슬롯이고, 여기서도 한 슬롯으로 흉내낸다.
        #
        # **다만 이건 추론이다.** 「한 슬롯」은 위 목록 한 줄에서 끌어낸 것이지 확인한 게
        # 아니다. 가짜가 추론을 사실처럼 굳히는 자리라 적어 둔다 — 실물이 다르게 굴면
        # 여기가 먼저 틀리고, 그러면 검사는 통과하는데 게임에서만 깨진다.
        #
        # **「우리 코드가 그렇게 대비하고 있다」는 「게임이 그렇게 준다」의 증거가 아니다.**
        if S.get("perf"):
            S["perf"] = None
            return 0, {"message": "연주를 정지했습니다"}
        if S["in_progress"] == "gathering" and S.get("gather_mode") is not None:
            # 칸으로 도는 채집 — 실측: MainButtonState 가 Hide(채집지 사이 이동 중)면 거절, Stop 이면 받아들인다
            if S["gather_mode"] == "Hide":
                return 0, {"error": "invalid_state", "message": "No stoppable action is in progress right now."}
            S["in_progress"] = None
            return 0, {"message": "Stop confirmed; no stoppable action remains."}
        if S["in_progress"]:
            S["in_progress"] = None
            return 0, {"message": "정지했습니다"}
        return 0, {"error": "invalid_state", "message": "정지할 행동이 없습니다"}

    # ── 실행 명령 ──
    if command == "execute_gathering":   # 지연 없는 경로 (FAST) — 지연이 있으면 respond() 가 잠금을 놓고 처리한다
        pre = _gather_pre(body)
        return pre if pre is not None else _gather_post(body)

    if command in ("execute_crafting", "execute_altering", "complete_altering_work"):
        _preempt()      # 칸으로 도는 채집은 다른 실행 명령이 갈아치운다 (미검증 — 흉내)

    if command == "execute_crafting":
        name = str(b.get("displayName") or "")
        if name not in _CRAFT:
            return 0, {"error": "not_found", "message": f"제작 목록에 없습니다: {name}"}
        per, ings = _CRAFT[name]
        try:
            count = int(b.get("craftCount", 1))
        except (TypeError, ValueError):
            count = 0
        if count < 1 or count > CRAFT_MAX_COUNT:
            return 0, {"error": "invalid_count", "message": f"craftCount 는 1~{CRAFT_MAX_COUNT}", "maxCount": CRAFT_MAX_COUNT}
        short = [n for n, req in ings.items() if S["bag"].get(n, 0) < req * count]
        if short:
            return 0, {"error": "not_enough_ingredient", "message": "재료 부족: " + ", ".join(short)}
        e = _blocked() or _pay()
        if e:
            return 0, e
        S["in_progress"] = "crafting"
        if GATHER_DELAY:
            time.sleep(GATHER_DELAY)
        for n, req in ings.items():
            S["bag"][n] -= req * count
        S["bag"][name] = S["bag"].get(name, 0) + per * count
        S["in_progress"] = None
        return 0, {"result": "completed", "craftCount": count, "rewards": [{"DisplayName": name, "Count": per * count}],
                   "criticalRewards": [], "cost": _cost()}

    if command == "execute_altering":
        name = str(b.get("displayName") or "")
        if name not in _ALTER:
            return 0, {"error": "not_found", "message": f"가공 목록에 없습니다: {name}"}
        per, ings, facility = _ALTER[name]
        short = [n for n, req in ings.items() if S["bag"].get(n, 0) < req]
        if short:
            return 0, {"error": "not_enough_ingredient", "message": "재료 부족: " + ", ".join(short)}
        e = _blocked() or _pay()
        if e:
            return 0, e
        for n, req in ings.items():
            S["bag"][n] -= req
        S["works"].append({"name": name, "facility": facility, "done_at": time.time() + ALTER_SEC})
        return 0, {"result": "started", "message": "가공을 등록했습니다", "cost": _cost()}

    if command == "complete_altering_work":
        name = str(b.get("displayName") or "")
        if name not in _ALTER:
            return 0, {"error": "not_found", "message": f"가공 목록에 없습니다: {name}"}
        if not S["works"]:
            return 0, {"error": "no_altering", "message": "가공 대기열이 비어 있습니다"}
        facility = _ALTER[name][2]
        mine = [w for w in S["works"] if w["name"] == name]
        if mine and all(w["done_at"] > time.time() for w in mine):
            return 0, {"error": "not_completed_yet", "message": "아직 진행 중입니다"}
        e = _blocked()
        if e:
            return 0, e
        if S.pop("collect_stop_once", None):   # 직접 정지·이동 실패 흉내 — 게임은 ok 에 stopped_by_user 로 답하고 완료분은 남는다
            return 0, {"result": "stopped_by_user", "collected": 0, "rewards": [],
                       "message": "The action stopped before it started, during travel or at the start."}
        done = [w for w in S["works"] if w["facility"] == facility and w["done_at"] <= time.time()]
        if not done:
            return 0, {"error": "no_completed_work_at_facility", "message": "그 시설에 완료된 작업이 없습니다"}
        rewards = {}
        for w in done:
            per = _ALTER[w["name"]][0]
            S["bag"][w["name"]] = S["bag"].get(w["name"], 0) + per
            rewards[w["name"]] = rewards.get(w["name"], 0) + per
            S["works"].remove(w)
        return 0, {"collected": len(done), "rewards": [{"DisplayName": n, "Count": c} for n, c in rewards.items()],
                   "criticalRewards": [], "message": f"{len(done)}건 수령"}

    return 4, {"error": "unknown_command", "message": f"데모가 모르는 명령입니다: {command}"}
