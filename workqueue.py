"""제작·채집 큐 엔진 (체인 실행).

사용자가 레시피·재료를 큐에 쌓고 「시작」을 누르면 러너 스레드 하나가 순서대로 CLI 실행 명령을 부른다.
항목 타입: gather(채집, 회당 최대 100개 반복) · craft(제작) · alter(가공 등록 → 완료 감지 → 수령) · collect(독립 수령 —
가공 대기열 카드의 시설별 「수령」 버튼, complete_altering_work 1회).
항목이 끝나거나 오류가 나면 **다음 항목으로 넘어간다** (onError="continue", 기본). 큐 전체를 repeat 회 반복할 수 있다.

안전 원칙 (docs/CLI.md §7):
- 러너는 POST /api/queue/start {confirm:true} 로만 돈다. 앱 재시작 후 자동 재개 없음 — 저장된 큐는 상태를 pending 으로 되돌린다.
- 오류는 그 항목만 error 로 두고 계속한다. 단 **다음 항목도 성공할 수 없는 오류**(연결 끊김·blocked)는 체인을 멈춘다 (FATAL_ERRORS).
  실행 명령(execute_*)은 날개가 들어 자동으로 다시 보내지 않는다. **읽기**(get_activity·get_items·도구·무게)는
  연결 계열 오류면 짧게 다시 읽고(READ_RETRY_WAITS), 게임이 로딩 중(loading)이면 LOADING_MAX 초까지 기다렸다 잇는다 (_read).
  blocked{kind} 는 사람이 게임 창을 닫아야 한다.
- 채집은 **개수(target)** 로 담는다. 회수는 ⌈target/100⌉ 로 따라 나온다 (1회 = execute_gathering 한 번 = 최대 100개 ·
  날개 5 — CLI 에 개수 인자가 없다). 진행·결과는 **가방 수**로 센다: 시작 전 가방(progress.bag0)과 지금 가방의 차이가
  progress.done(+N개)이고, 가방이 bag0 + target 에 닿으면 끝난다. 가방을 못 읽으면 그 회의 회신 gained 로 메운다.
  남은 개수가 100 보다 적은 회(넘칠 수 있는 회)는 도는 동안 3초마다 가방을 읽다가 목표에 닿으면 stop_action 으로
  끊는다 (_gather_goal_watch — 읽기는 도는 채집을 끊지 않는다, docs/CLI.md 실측). 매 회 전에 도구(ToolOk)·무게를 확인한다.
  1.0.9 의 회수 카드(runs)는 불러올 때 개수(runs×100)로 옮긴다.
- 가공은 등록 직전마다 그 시설의 칸을 대기열로 세어 빈 칸만큼만 걸고 곧바로 다음 항목으로 넘어간다(waiting · N6).
  항목 전환 시·회차 끝에 완료가 설정 n 개 모였으면 수령하고 빈 칸에 남은 건을 다시 건다. 체인이 다 끝났는데 waiting 이
  남아 있으면 남은 시간만큼(5초~) 자며 같은 규칙으로 끝까지 간다. 제작은 시설 상한으로 나눠 여러 번 부른다(N7).
- 실행 명령은 회당 정령의 날개 5개를 소모한다 (카탈로그 명시). 잔액 계산은 하지 않는다 — CLI 가 성공 응답의 cost 문장으로 알려준다.
- 실행 전 점검: 항목마다 CLI 실행 명령을 부르기 전에 get_activity(읽기)로 사망·부활·전투·대화 선택·던전을 본다.
  막히면 실행 명령을 부르지 않고 precheck_* 로 끝내며 체인을 멈춘다 (설정 queue_precheck 로 끌 수 있다).
  전장 안은 막지 않고 경고만 한다 — 실행해 보고 CLI 의 답으로 판단한다 (activity_summary 참조).
- 정지: 플래그 + 실행 중이면 stop_action 1회. CLI 는 "다른 명령이 오면 진행 중인 행동을 canceled 로 끝낸다"(카탈로그) 이므로
  stop_action 은 파이프 잠금을 **거치지 않고** 보낸다 — 잠금은 실행 명령이 최대 11분(EXEC_TIMEOUT) 쥐고 있다.

에러 이름은 fixtures/capabilities.json 의 execute_* / complete_altering_work OutputExample·Note 에서 옮겨 적은 것이다.
"""
from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
import uuid
from collections import deque

import ledger
import presets
import questwatch
import store
import work

MAX_PER_PASS = 100          # execute_gathering 이 한 번에 모으는 상한 (카탈로그: "One call gathers up to 100")
WINGS_PER_CALL = 5          # execute_gathering / execute_crafting / execute_altering 회당 소모 (카탈로그: "consumes 5 정령의 날개")
STREAK_STOP = 3             # 같은 오류가 이만큼 연달아 나면 그 카드를 회차에서 뺀다 (아래 주석)
# 정령의 날개를 쓰는 명령. **수령(complete_altering_work)은 여기 없다** — 호출이지만 소모가 없다
# (카탈로그에 "consumes 5" 가 없고 not_enough_currency·cost_payment_failed 오류도 없다).
WING_COMMANDS = ("execute_gathering", "execute_crafting", "execute_altering")

# ── 날개 소모 차단기 ──
# 실측: 날개 5 는 명령이 **수락되는 순간** 빠진다 — 나중에 stopped_by_user·blocked 로 끝나도 돌려받지 못한다.
# 그래서 러너가 무엇 때문이든 헛돌면 날개가 조용히 샌다. 두 가지 상한으로 끊는다 (넘으면 ■ 정지와 같은 길로 강제 정지):
#   총량   — WING_CAP_WINDOW_MIN 분 안에 설정 wing_cap_total(기본 150 = 30회) 을 **넘으면**
#   헛소모 — WING_WASTE_WINDOW_MIN 분 안에 날개는 나갔는데 산출이 없는 호출이 설정 wing_cap_waste(기본 4) 회에 **닿으면**
# 창 길이도 설정이다 (wing_cap_window_min · wing_cap_waste_min) — 아래 상수는 그 기본값. 기록은 메모리에만.
WING_CAP_WINDOW_MIN = 10
WING_WASTE_WINDOW_MIN = 5
DEFAULT_WING_CAP_TOTAL = 150
DEFAULT_WING_CAP_WASTE = 4
# 날개를 쓰지 않고 거절되는 답 (cost 줄이 없을 때만) — 실측: not_in_field·not_enough_ingredient·not_found·blocked kind=dead 는 0.1초·날개 0.
# not_enough_currency 는 낼 날개가 없다는 뜻이고, cli_* ·spawn_failed 는 게임까지 가지도 못했다.
# 그 밖의 실패는 **나갔다고 본다** (안전한 쪽 — 모르면 센다).
WING_FREE_ERRORS = {"not_in_field", "not_enough_ingredient", "not_found", "not_enough_currency",
                    "cli_not_found", "cli_disabled", "cli_disconnected", "spawn_failed"}
# ok 인데 산출 없이 끝난 결과 (게임 쪽 끊김)
WING_WASTE_RESULTS = {"stopped_by_user", "stopped", "canceled"}


def _now() -> float:
    """날개 차단기의 시계 — 검사가 창 만료를 흉내 내려고 바꿔 끼운다."""
    return time.time()


def wing_spend(command: str, r) -> tuple:
    """실행 명령 한 번의 날개 소모 → (날개 수, 헛소모인가). 날개를 쓰지 않는 명령·거절이면 (0, False).

    - cost 줄("정령의 날개 5 spent")이 있으면 무조건 나간 것이다.
    - 성공 답인데 result 가 stopped_by_user·stopped·canceled 면 헛소모 (채집이 조금이라도 캤으면 산출이 있으니 아님).
    - 실패 답이면 WING_FREE_ERRORS·사망 거절이 아닌 한 나갔다고 보고 헛소모로 센다 (부분 획득 gained>0 이면 아님)."""
    if command not in WING_COMMANDS:
        return 0, False
    b = getattr(r, "body", None)
    b = b if isinstance(b, dict) else {}
    cost = bool(str(b.get("cost") or "").strip())
    gained = _n(b.get("gained"))
    if getattr(r, "ok", False):
        return WINGS_PER_CALL, (b.get("result") in WING_WASTE_RESULTS and gained <= 0)
    if not cost and (_dead_blocked(r) or getattr(r, "error", None) in WING_FREE_ERRORS):
        return 0, False
    return WINGS_PER_CALL, gained <= 0


def wing_limits(settings: dict | None) -> tuple:
    """설정에서 (총량 상한, 헛소모 상한, 총량 창(분), 헛소모 창(분)).
    저장된 설정은 store 가 범위로 자르지만 검사·옛 파일이 날값을 줄 수 있어 여기서도 자른다."""
    s = settings or {}

    def pick(key, default):
        lo, hi = store._RANGES.get(key, (default, default))
        return int(min(max(_n(s.get(key), default), lo), hi))
    return (pick("wing_cap_total", DEFAULT_WING_CAP_TOTAL), pick("wing_cap_waste", DEFAULT_WING_CAP_WASTE),
            pick("wing_cap_window_min", WING_CAP_WINDOW_MIN), pick("wing_cap_waste_min", WING_WASTE_WINDOW_MIN))


def wing_cap_msg(cap: int, mins: int = WING_CAP_WINDOW_MIN) -> str:
    return f"날개 소모 이상 — {mins}분에 {cap} 넘게 소모, 강제 정지"


def wing_waste_msg(n: int, mins: int = WING_WASTE_WINDOW_MIN) -> str:
    return f"날개 헛소모 — {mins}분에 산출 없는 소모 {n}회, 강제 정지"

# ── 채집물마다 알려진 방해 요소 (실제로 겪은 것만 적는다 — 추측으로 늘리지 않는다) ──
# CLI 는 이걸 미리 알려 주지 않는다. 실패하고 나서야 안다. 그래서 **시작 전에** 알린다.
# 열쇠는 이름 일부다 (「계란」·「신선한 계란」 둘 다 잡으려고). 값은 사람에게 보일 한 줄.
# **게임이 쓰는 이름 그대로 적는다.** 처음에 「계란」으로 넣었더니 한 번도 안 걸렸다 —
# 실제 채집 목록의 이름은 「달걀」이다 (채집 목록 캐시 `cache_gather.json` 177개에서 확인:
# 「달걀」·「황금 달걀」). 사람이 부르는 말과 게임의 표기가 다를 수 있으니 **짐작하지 말고
# get_gatherable_items 의 DisplayName 을 보고 적는다.**
GATHER_HAZARDS = {
    "달걀": "수탉이 방해해 채집이 실패할 수 있습니다",
    "계란": "수탉이 방해해 채집이 실패할 수 있습니다",   # 사람이 이렇게 부르기도 한다
}


def gather_hazard(name) -> str:
    """이 채집물에 알려진 방해가 있나. 없으면 빈 문자열."""
    n = str(name or "")
    for key, note in GATHER_HAZARDS.items():
        if key in n:
            return note
    return ""
EXEC_TIMEOUT = 660.0        # 11분. **명세에 상한이 없다** — 우리가 고른 값이다 (실측 최대 293초)
READ_TIMEOUT = 60.0
FILE = "queue.json"
LOG_KEEP = 50
EVENTS_KEEP = 100
REPEAT_MAX = 20
WAIT_MIN = 5.0              # 가공 완료 대기 폴링 최소 간격
WAIT_MAX_TOTAL = 30 * 60.0  # 체인 끝 가공 대기 상한
GROUP_NAME_MAX = 24         # 그룹 이름 길이
CARD_TYPES = ("craft", "alter", "gather", "collect", "play", "notify")
# ── 연주·알림 카드 — **CLI 를 부르지 않고 날개도 0** 인 카드 둘 ──
#   play   : 차례가 오면 폴리오 엔진에 「틀어 달라」고만 하고 바로 done (큐는 음악을 기다리지 않는다 — 기다림은 「우선」이 맡는다)
#   notify : 차례가 오면 알림(PC 밴드 토스트 · 앱 창 토스트 · 폰 토스트·진동·알림)을 내고 바로 done
FREE_CARD_TYPES = ("play", "notify")
# 실행 명령을 보내는 카드 — 채집 목표 도달 뒤 「다음 카드에 넘기기」는 이 종류일 때만 한다 (그 명령이 도는 채집을 갈아치운다)
ACTION_CARD_TYPES = ("gather", "craft", "alter", "collect")


class _Failed:
    """호출 자체가 터졌을 때 CliResult 대신 돌려주는 실패 답."""

    def __init__(self, error: str, message: str = ""):
        self.ok, self.error, self.message, self.body = False, error, message, None
PLAY_MODES = ("song", "resume")   # 이 곡만 · 지금 대기열 이어서. 「재생목록」은 카드가 아니라 **그룹**으로 담긴다 (_add_playlist_group)
PLAY_MODE_LEGACY = "list"         # 예전의 「재생목록 전체」 카드 — 담을 때 그룹으로 펼치고, 저장본에 남은 것은 실행 시 list_gone
PLAY_NOTE_CUT = "연주가 중간에 멈춤"   # 카드 진행 줄 — 폴리오에서 ■ 를 눌렀거나 다른 재생이 자리를 가져갔다 (오류가 아니다)
PLAY_END_GRACE = 15.0             # 폴리오가 「부탁이 끝났다」고 한 뒤 busy 가 꺼지기를 기다리는 상한(초) — 남의 연주가 바로 이어져도 영영 안 선다
# 「연주」 카드의 회차 — 큐에 담긴 연주는 재생 상태를 보지 않으므로 회차를 따로 넣지 않았으면 한 번만 튼다.
# song·list 는 정확히 count 번 치고 **멈춘다** — 그 요청은 폴리오의 반복·셔플 설정을 안 본다 (engine.play_solo / queue_set(passes)).
# resume(지금 대기열 이어서)은 폴리오 제 설정대로라 회차가 없다 (화면에도 안 보인다).
PLAY_COUNT_MAX = 20                        # engine.SOLO_PASSES_MAX 와 같은 값
NOTIFY_TEXT_MAX = 80
NOTIFY_DEFAULT_TEXT = "큐가 여기까지 왔습니다"
NOTICES_KEEP = 10                          # GET /api/queue 의 notices — 최근 열 개 (화면은 id·t 로 「본 것」을 가른다)
PLAY_MODE_KO = {"song": "이 곡만", "list": "재생목록 전체", "resume": "지금 대기열 이어서"}
# 가공 수령 모드 — "none"(걸기만·새 기본) / "later"(등록 후 나중에 수령) / "wait"(완료까지 기다렸다 수령)
ALTER_MODES = ("none", "later", "wait")
ALTER_MODE_DEFAULT = "none"   # 새로 담는 항목의 기본값. 옛 저장본(필드 없음)은 alter_mode() 가 "later" 로 본다
# 열 매핑 — **서버만** 판정한다. UI 가 status 를 다시 해석하면 두 곳이 반드시 어긋난다.
COLUMN_OF = {"pending": "wait", "running": "run", "waiting": "run", "done": "done", "error": "fail", "stopped": "wait"}   # stopped 는 실패가 아니라 중단 — 낱장도 그룹과 같이 대기 열

# ── 카탈로그에서 옮겨 적은 에러 이름 (지어낸 것 없음) ──
GATHER_ERRORS = {"not_found", "no_route", "insufficient_living_skill_level", "overweight", "tool_missing", "tool_broken",
                 "required_consumable_missing", "not_in_field", "blocked", "timeout", "canceled",
                 "not_enough_currency", "cost_payment_failed"}
CRAFT_ERRORS = {"crafting_locked", "not_found", "not_available", "invalid_count", "insufficient_living_skill_level",
                "insufficient_facility_level", "insufficient_decor_score", "not_enough_ingredient", "ingredient_locked",
                "insufficient_transfer_cost", "blocked", "overweight", "not_in_field", "facility_not_found", "timeout",
                "canceled", "not_enough_currency", "cost_payment_failed"}
ALTER_ERRORS = {"not_found", "not_available", "requires_user_interaction", "insufficient_facility_level",
                "not_enough_ingredient", "ingredient_locked", "insufficient_transfer_cost", "blocked", "overweight",
                "not_in_field", "facility_not_found", "component_not_found", "timeout", "canceled",
                "not_enough_currency", "cost_payment_failed"}
COLLECT_ERRORS = {"no_altering", "no_completed_work", "not_found", "no_completed_work_at_facility", "not_completed_yet",
                  "blocked", "overweight", "not_in_field", "facility_not_found", "timeout", "canceled"}
# 폴링이 State=Completed 로 읽은 **직후**의 수령이 「수령분 없음」으로 거절될 때 (실제로 겪었다:
# 완료 감지 같은 초에 complete_altering_work → no_completed_work. 조회와 수령 쪽 캐시가 완료 순간에 어긋났거나 그 순간
# 게임에서 직접 수령). 완료분은 시설에 그대로다 — 카드를 영영 실패로 두면 등록에 쓴 날개를 못 찾는다. not_completed_yet 처럼
# waiting 으로 두고 COLLECT_RETRY_WAIT 초 가라앉힌 뒤 대기열을 **다시 읽어** 수령한다. 항목당 COLLECT_RETRY_MAX 번까지 —
# 세 번째도 같으면 기존 오류 처리. 거절은 이동 전에 돌아오고 수령은 날개를 쓰지 않아(WING_COMMANDS 밖) 재시도 비용은 0 이다.
COLLECT_RACE_ERRORS = {"no_completed_work", "no_completed_work_at_facility"}
COLLECT_RETRY_WAIT = 5.0     # 다시 읽기 전 가라앉히는 시간(초)
COLLECT_RETRY_MAX = 3        # 항목당 「수령분 없음」 허용 횟수 (progress.collectRetry) — 이만큼이면 오류로
TOOL_ERRORS = {"tool_missing", "tool_broken"}      # 도구 → 그 항목 즉시 종료
WEIGHT_ERRORS = {"overweight"}                     # 무게 → 그 항목 즉시 종료
# 앱이 스스로 내는 에러 (CLI 를 부르기 전에 막은 것)
PASS_EXEMPT = ("alter", "craft")   # 안전 상한(queue_max_passes)을 대지 않는 종류 — 호출 수가 수량으로 정해진다
APP_ERRORS = {"tool_not_ok", "overweight_soon", "max_passes", "not_gatherable", "not_in_cache", "cli_disconnected", "alter_full"}
# 다음 항목도 성공할 수 없는 오류 — onError 설정과 무관하게 체인을 멈춘다.
# 연결 계열(cli_transport 의 cli_not_found/cli_disabled/spawn_failed, exit 5 의 disconnected, status 의 game_off, timeout)과
# blocked(사람이 게임 창을 닫아야 함)
# 실행 전 상태 점검(get_activity)에서 막은 것 — 다음 항목도 같은 이유로 막히므로 FATAL.
# 필드명은 카탈로그 get_activity OutputExample 원문: combatState{IsDead, IsReviving, IsInCombat},
# dialogue{IsWaitingForSelection}, Dungeon{State: NotInDungeon|Entering|InProgress|Cleared}
# 연주 중에 큐가 무엇을 할지 (설정 queue_on_performance). 레일 「우선」 3단 · 설정 창 선택과 같은 값.
#   music = 완전한 연주 우선 — 연주 중이면 **연주 대기**(hold)로 들어간다: 작업 중 칸 맨 위에 임시 카드를 띄우고
#           연주가 끝나기를 기다렸다가 스스로 시작한다(_hold_for_music). 도는 중에 연주가 시작돼도 같다 — 다음 실행
#           명령을 보내지 않고 그 카드 앞에서 기다린다. 연주는 건드리지 않는다. (기본)
#   song  = 이 곡 끝나면 양보 — 지금 곡만 마치고 큐에 넘긴다 (연주 쪽에 부탁 — _yield_after_song). 기다리는 동안 같은 연주 대기 카드
#   work  = 작업 우선 — 지금 바로, 연주는 끊긴다 (합주·인사말 중이면 그때만 기다린다 — 그 동안만 같은 연주 대기 카드)
# **기본이 music 인 이유**: 사람이 고르지 않은 상태에서 남의 음악을 끊지 않는다.
# 실측: 연주 중 execute_altering 을 보내면 **거절 없이** 날개 5 를 빼고(t+1.2초) 연주를 끊은 뒤 이동한다 —
# 게임은 연주를 지켜 주지 않는다. 보호는 큐가 **보내기 전에만** 가능하다.
DEFAULT_ON_PERFORMANCE = "music"
PERF_MODES = ("music", "song", "work")
# 옛 기록용 — 예전에는 `music` 이 도는 중에 연주가 시작되면 체인을 이 사유로 세웠다. 저장된 카드·보드 로그가
# 이 값을 들고 있고 보드 실행 줄·오버레이 밴드가 읽으므로 이름·문구는 남긴다. 새로 내지는 않는다 (지금은 연주 대기).
PERF_STOP_REASON = "performance"
PERF_PAUSE_MSG = "연주가 시작돼 멈췄습니다 — 연주가 끝난 뒤 ▶ 시작으로 이어집니다"

# 「완전한 연주 우선」의 **연주 대기**(hold) — 왜 시작되지 않는지 사람이 알 수 있게 카드로 보여 준다.
# 연주 중에 ▶ 시작을 누르면 거절하는 대신 러너가 뜨고, 작업 중 칸 **맨 위**에 임시 카드를 띄운 채
# 연주가 끝나기를 기다렸다가 스스로 시작한다. 카드는 큐 항목이 아니다 — 저장하지 않고, 날개 0, 끝나면 사라진다.
# 어디까지 기다리는가는 **연주의 모양**이 정한다 (폴리오 엔진 `work_now` 가 답한다):
#   music = 우리 폴리오의 전체 재생(대기열)   → 목록이 **다** 끝날 때까지 (곡 사이 틈(gap_sec)에는 시작하지 않는다)
#   song  = 우리 폴리오의 한 곡 (/api/play)   → 이 곡이 끝나면
#   game  = 게임에서 직접 튼 연주(우리 것 아님) → 끝나면 (폴리오에 물을 데가 없을 때도 이 모양)
HOLD_KINDS = ("music", "song", "game")
# 카드가 뜨는 갈래 전부 (「이 곡 끝나면 양보」로 곡 경계를 기다리는 동안 화면이 「실행 중」으로만 보이던 것을
# 막는다). 우선이 무엇이든 **연주 때문에 기다리는 동안은 같은 카드**다:
#   music → 위 세 모양 · song(「이 곡」 우선) → 부탁하고 경계를 기다리는 동안 `song` (전체 재생이어도 넘김은 다음 곡 경계라 「이 곡」)
#   work(「작업」 우선) → 합주·인사말이라 못 끊는 동안만 `work` (바로 끊는 경우는 카드가 없다)
HOLD_WAIT_KINDS = HOLD_KINDS + ("work",)
HOLD_POLL = 2.0        # 폴리오가 재 둔 값(perf_now, 메모리)을 다시 읽는 간격(초) — CLI 를 부르지 않는다
HOLD_POLL_CLI = 5.0    # 폴리오에 물을 데가 없을 때 get_activity(읽기, 날개 0)를 다시 보내는 간격(초)
HOLD_SETTLE = 4.0      # 「연주가 끝났다」로 치기까지 조용해야 하는 시간(초) — 곡 사이 틈(gap_sec 기본 2초)보다 길게
HOLD_STALE = 30.0      # 폴리오가 재 둔 값이 이보다 오래됐으면 믿지 않고 게임에 직접 읽는다(초)
HOLD_SHORT = "연주 끝나면 시작"   # 오버레이 밴드 칸 (30px 라 짧게)
HOLD_SHORT_BY_KIND = {"song": "이 곡 끝나면 시작", "work": "합주·인사말 끝나면 시작"}   # 갈래별 한 마디 — 나머지는 HOLD_SHORT


def hold_short(kind) -> str:
    """오버레이 밴드의 한 마디 (overlay.band_cells 가 부른다)."""
    return HOLD_SHORT_BY_KIND.get(str(kind or ""), HOLD_SHORT)


def hold_text(kind: str, left=None, endless: bool = False) -> str:
    """연주 대기 카드의 한 줄. 모양(kind)마다 「언제 시작하는가」를 적는다."""
    if kind == "music":
        if endless:
            tail = "전체 반복 · 끝을 알 수 없음"
        elif left is None:
            tail = ""
        elif int(left) <= 0:
            tail = "마지막 곡"
        else:
            tail = f"{int(left)}곡 남음"
        return "연주 중 — 전체 재생이 끝나면 시작" + (f" ({tail})" if tail else "")
    more = " (반복 · 끝을 알 수 없음)" if endless else ""
    if kind == "song":
        return "연주 중 — 이 곡이 끝나면 시작" + more
    if kind == "work":
        return "연주 중 — 합주/인사말이 끝나면 시작"
    return "게임에서 연주 중 — 끝나면 시작" + more

# precheck_dead·precheck_combat·precheck_battlefield 는 **더 이상 내지 않는다** (실측 결과 경고·CLI 판단으로 바뀜).
# 옛 기록(저장된 카드·보드 로그)이 이 코드를 들고 있으므로 이름·문구는 남긴다.
PRECHECK_ERRORS = {"precheck_dead", "precheck_combat", "precheck_dialog", "precheck_dungeon", "precheck_battlefield",
                   "precheck_scenario", "precheck_performance", "precheck_mission"}
# not_in_field — 자동 이동을 쓸 수 없는 곳(집 안 등). 실측: 내 집 안에서 execute_altering → 0.1초 만에
# not_in_field 「Auto-travel cannot be used in this place. The user must leave this place before continuing.」, 날개 안 씀.
# 사람이 나오기 전까지 뒤 항목도 전부 같은 답이라 체인을 멈춘다 (전장은 반대로 스스로 이동하므로 경고만).
FATAL_ERRORS = {"cli_not_found", "cli_disabled", "spawn_failed", "disconnected", "game_off", "timeout", "blocked",
                "cli_disconnected", "internal", "not_in_field", "loading", "alter_full"} | PRECHECK_ERRORS

# ── 읽기 재시도 (N4) ──
# 읽기 호출(상태 점검·가방 수·도구·무게)이 연결 계열로 실패하면 곧바로 보드를 세우지 않고 짧게 다시 읽는다.
# 읽기는 날개가 들지 않는다. 실행 명령(execute_*)은 다시 보내지 않는다 — 날개 5 가 또 나간다.
READ_RETRY_ERRORS = {"disconnected", "timeout", "game_off", "cli_disconnected"}
READ_RETRY_WAITS = (3.0, 6.0)     # 재시도 전 기다림(초) — 길이가 곧 재시도 횟수
# 게임이 로딩 중(재접속·캐릭터 선택)이면 끝날 때까지 기다린다. 이 간격으로 다시 읽고, 상한을 넘으면 보드를 세운다.
LOADING_POLL = 5.0
LOADING_MAX = 180.0
# 연결 계열 오류의 멈춤 안내 — 코드는 그대로 두고 글만 바꾼다. {n} = 재시도 횟수
CONN_STOP_KO = {
    "disconnected": "게임과 연결이 잠깐 끊겼습니다",
    "cli_disconnected": "게임과 연결이 잠깐 끊겼습니다",
    "timeout": "게임이 제때 답하지 않았습니다",
    "game_off": "게임이 꺼져 있거나 연결되지 않았습니다",
}


def wait_scale() -> float:
    """재시도·로딩 대기의 배율. 데모 빠른 모드(MOBIW_DEMO_FAST=1 — 검사)에서는 기다리지 않는다."""
    return 0.0 if os.environ.get("MOBIW_DEMO_FAST") == "1" else 1.0


def is_loading(r) -> bool:
    """게임이 로딩 중(재접속·캐릭터 선택)이라는 답인가. 오류 코드 loading · blocked kind=loading · 본문의 loading:true."""
    if r is None or getattr(r, "ok", False):
        return False
    b = getattr(r, "body", None)
    b = b if isinstance(b, dict) else {}
    err = getattr(r, "error", None)
    if err == "loading":
        return True
    if err == "blocked" and b.get("kind") == "loading":
        return True
    return b.get("loading") is True


def conn_stop_msg(error: str, retries: int, action: bool = False, raw: str = "") -> str:
    """연결 계열 오류로 멈출 때의 안내. 읽기는 「재시도 n회 후 멈춤」, 실행 명령은 다시 보내지 않았다는 것을 적는다."""
    head = CONN_STOP_KO.get(error) or ERROR_KO.get(error, "") or error
    if action:
        tail = "실행 명령은 날개가 들어 자동으로 다시 보내지 않았습니다. 게임을 확인한 뒤 ▶ 시작"
    else:
        tail = f"재시도 {retries}회 후 멈춤. 게임을 확인한 뒤 ▶ 시작"
    return f"{head} — {tail}" + (f" · 원문: {raw}" if raw and raw != error else "")


# ── 실행 중 절전 막기 (N4) ──
# 큐가 도는 동안 윈도가 잠들면 게임과의 연결이 끊긴다. 러너 스레드에서 SetThreadExecutionState 로
# ES_CONTINUOUS|ES_SYSTEM_REQUIRED 를 걸고 끝날 때 ES_CONTINUOUS 로 푼다. 이 상태는 **건 스레드에 묶여**
# 스레드가 죽으면 윈도가 스스로 푼다. 윈도가 아니거나 검사·CLI 차단 실행이면 아무것도 하지 않는다.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _awake_enabled() -> bool:
    if sys.platform != "win32":
        return False
    if os.environ.get("MOBIW_NO_CLI") == "1" or os.environ.get("MOBIW_NO_KEEPAWAKE") == "1":
        return False
    return "pytest" not in sys.modules


def keep_awake(on: bool) -> bool:
    """절전 막기를 켜고 끈다 → 실제로 윈도에 걸었으면 True. 실패해도 큐는 계속한다."""
    if not _awake_enabled():
        return False
    try:
        import ctypes
        flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) if on else ES_CONTINUOUS
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))
    except Exception:
        return False

ERROR_KO = {
    "tool_not_ok": "채집 도구가 없거나 내구도가 0 입니다 (get_gatherable_items.ToolOk=false). 도구를 고치거나 바꾼 뒤 다시 시작하세요.",
    "tool_broken": "채집 도중 도구 내구도가 다 됐습니다. 도구를 고치거나 바꾼 뒤 다시 시작하세요.",
    "tool_missing": "채집 도구가 없습니다.",
    "overweight": "가방 무게가 찼습니다. 정리한 뒤 다시 시작하세요.",
    "overweight_soon": "가방 무게가 한계에 가깝습니다(설정 여유 이내). 정리한 뒤 다시 시작하세요.",
    "no_progress": "두 번 연속으로 한 개도 캐지 못했습니다. 필요한 소모품(빈 병 등)이 떨어졌는지 확인하세요.",
    # 게임 쪽에서 **이 한 장만** 멈춘 것이다 (카탈로그 result 값 그대로). 우리 「■ 정지」가 아니다.
    "stopped": "게임에서 이 작업이 중단됐습니다. 캐릭터가 움직였거나 게임에서 멈췄을 수 있습니다.",
    # 실측: 이동 중 몬스터에 붙잡히거나 사람이 직접 멈추면 ok=True · result=stopped_by_user (38초, 날개 −5).
    # 날개는 수락 즉시(t+1.2초) 빠지므로 「이미 소모됐다」가 맞다.
    "stopped_by_user": "게임에서 이동·작업이 끊겼습니다(전투·직접 정지) — 날개는 이미 소모됐습니다. ↻ 로 다시 시도하세요",
    "collect_interrupted": "수령이 끊겼습니다(직접 정지·이동 실패) — 완료된 가공이 시설에 그대로 남아 있습니다. ↻ 로 다시 수령하세요",
    "blocked": "게임에 닫아야 할 창이나 상태가 있습니다. 게임에서 정리한 뒤 다시 시작하세요.",
    "not_enough_currency": "정령의 날개가 부족합니다 (회당 5개).",
    "cost_payment_failed": "정령의 날개 결제에 실패했습니다.",
    "not_enough_ingredient": "재료가 부족합니다 (가방 기준).",
    "insufficient_transfer_cost": "재료 이송 비용이 부족합니다.",
    "invalid_count": "제작 횟수가 시설 상한을 넘습니다. 횟수를 줄여 다시 담으세요.",
    "requires_user_interaction": "이 가공은 게임에서 직접 시작해야 합니다 (CLI 로 등록 불가).",
    "not_completed_yet": "그 가공이 아직 진행 중입니다.",
    "no_completed_work_at_facility": "그 시설에 수령할 완료 작업이 없습니다.",
    "no_completed_work": "그 시설에 수령할 완료 작업이 없습니다 (캐시 기준). 갱신해 보세요.",
    "no_altering": "가공 대기열이 비어 있습니다.",
    "duplicate": "같은 시설의 수령 항목이 이미 큐에 있습니다.",
    "canceled": "다른 명령이 들어와 행동이 취소됐습니다.",
    "timeout": "게임이 제때 답하지 않아 행동이 중단됐습니다.",
    "max_passes": "안전 상한(설정 queue_max_passes)에 닿았습니다.",
    "not_gatherable": "지금 채집 가능 목록에 없는 항목입니다 (생활 레벨 부족이면 목록에 안 보입니다).",
    "not_in_cache": "그 레시피가 캐시에 없습니다. 갱신해 보세요.",
    "cli_disconnected": "게임에 연결되지 않았습니다.",
    "cli_disabled": "CLI 실행이 차단된 실행입니다 (MOBIW_NO_CLI=1).",
    "cli_not_found": "MabinogiMobile_CLI.exe 를 찾지 못했습니다.",
    "disconnected": "게임과 연결이 끊겼습니다 (파이프 끊김).",
    "game_off": "게임이 꺼져 있거나 연결되지 않았습니다.",
    "loading": f"게임이 로딩 중입니다(재접속·캐릭터 선택) — {int(LOADING_MAX // 60)}분을 기다려도 끝나지 않아 멈췄습니다. "
               "게임에 들어간 뒤 ▶ 시작",
    "precheck_dead": "캐릭터가 사망/부활 대기 상태로 보고됩니다 (get_activity). 게임에서 부활을 선택한 뒤 다시 시작하세요.",
    "precheck_combat": "캐릭터가 전투 중으로 보고됩니다 (get_activity). 전투가 끝난 뒤 다시 시작하세요.",
    "precheck_dialog": "대화 선택을 기다리는 상태로 보고됩니다 (get_activity). 게임에서 대화를 끝낸 뒤 다시 시작하세요.",
    "precheck_dungeon": "던전 안으로 보고됩니다 (get_activity). 던전을 나온 뒤 다시 시작하세요.",
    "precheck_battlefield": "전장 안으로 보고됩니다 (get_activity). 전장을 나온 뒤 다시 시작하세요.",
    "precheck_scenario": "시나리오·튜토리얼·연출이 진행 중으로 보고됩니다 (get_activity). 끝난 뒤 다시 시작하세요.",
    "precheck_mission": "지역 임무 진행 중(AutoPlayTarget=goddess_mission) — 나가면 임무 포기 확인창·결과 화면에 막힙니다 "
                        "(날개만 소모). 임무를 끝내고 전리품까지 받거나 포기한 뒤 ↻",
    "precheck_mounted": "탈것 탑승 중으로 보고됩니다 (get_activity Mode.MountPartState) — 탄 채로는 연주할 수 없습니다. "
                        "탈것에서 내린 뒤 다시 시작하세요 (폴리오 설정 「탈것은 제작으로 내리기」가 켜져 있으면 폴리오가 내리고 틉니다).",
    "precheck_performance": "연주 때문에 진행하지 못했습니다. 연주를 끝내거나, 우선을 "
                            "「이 곡 끝나면」 또는 「작업 우선」으로 바꾸세요.",
    "performance_playing": "연주 중입니다 — 「완전한 연주 우선」이라 연주가 끝날 때까지 기다렸다가 시작합니다 "
                           "(작업 중 칸에 연주 대기 카드가 뜹니다). 바로 시작하려면 우선을 「이 곡 끝나면」/「작업 우선」으로 바꾸세요.",
    # 같은 확인창 안내를 다른 우선에도 — board.js 는 「연주 대기 카드」 글자로 ♪ 줄을 고른다
    "performance_playing_song": "연주 중입니다 — 「이 곡 끝나면 양보」라 이 곡이 끝나면 시작합니다 "
                                "(기다리는 동안 작업 중 칸에 연주 대기 카드가 뜹니다).",
    "performance_playing_work": "연주 중입니다 — 「작업 우선」이라 연주를 끊고 바로 시작합니다 "
                                "(합주·인사말 중이면 그것이 끝날 때까지 작업 중 칸에 연주 대기 카드가 뜹니다).",
    "not_in_field": "자동 이동을 쓸 수 없는 곳(집 안 등)입니다 (CLI not_in_field). 그곳을 나온 뒤 ↻ 로 다시 시작하세요.",
    # 날개 차단기 — 기본 설정값의 문장이다. 실제 정지·거절 문장은 설정값으로 다시 만든다 (wing_cap_msg·wing_waste_msg)
    "wing_cap": "날개 소모 이상 — 10분에 150 넘게 소모, 강제 정지",
    "wing_waste": "날개 헛소모 — 5분에 산출 없는 소모 4회, 강제 정지",
    # 가공 칸이 찬 시설에 등록을 보냈다 — 게임에 「가공 추가 실패 — 가공 대기열이 가득 찼습니다」 창이 남고
    # CLI 로는 닫히지 않는다(실측). 그 창이 떠 있는 동안 실행 명령은 전부 blocked·unknown_modal 이다
    "alter_full": "게임에 뜬 『가공 대기열이 가득 찼습니다』 창을 닫아 주세요 — 닫은 뒤 다시 시작하면 이어서 갑니다",
}

# 오버레이 밴드(30px)에 찍는 아주 짧은 한글 — 「실패 · 도구 없음」 처럼. ERROR_KO 는 문장이라 밴드에 안 들어간다.
ERROR_SHORT = {
    "tool_not_ok": "도구 없음", "tool_missing": "도구 없음", "tool_broken": "도구 파손",
    "overweight": "무게 초과", "overweight_soon": "무게 임박", "no_progress": "수확 없음",
    "blocked": "게임 창 확인", "not_enough_currency": "날개 부족", "cost_payment_failed": "결제 실패",
    "not_enough_ingredient": "재료 부족", "insufficient_transfer_cost": "이송 비용 부족",
    "invalid_count": "횟수 초과", "requires_user_interaction": "직접 시작 필요",
    "not_completed_yet": "아직 진행 중", "no_completed_work_at_facility": "수령분 없음", "collect_interrupted": "수령 끊김",
    "no_completed_work": "수령분 없음", "no_altering": "대기열 비었음", "duplicate": "중복",
    "canceled": "취소됨", "timeout": "응답 없음", "max_passes": "안전 상한",
    "not_gatherable": "채집 불가", "not_in_cache": "캐시 없음", "cli_disconnected": "연결 끊김",
    "cli_disabled": "CLI 차단", "cli_not_found": "CLI 없음", "disconnected": "연결 끊김",
    "game_off": "게임 꺼짐", "spawn_failed": "실행 실패", "internal": "내부 오류",
    "precheck_dead": "사망/부활", "precheck_combat": "전투 중", "precheck_dialog": "대화 중",
    "precheck_dungeon": "던전 안", "precheck_battlefield": "전장 안", "precheck_scenario": "연출 중",
    "precheck_performance": "연주 중", "precheck_mission": "지역 임무 중", "precheck_mounted": "탈것 탑승 중",
    "performance_playing": "연주 중 대기", PERF_STOP_REASON: "연주 중 대기",
    "not_found": "대상 없음", "no_route": "경로 없음", "insufficient_living_skill_level": "생활 레벨 부족",
    "required_consumable_missing": "소모품 없음", "not_in_field": "이동 불가(집 안 등)", "crafting_locked": "제작 잠김",
    "not_available": "지금 불가", "insufficient_facility_level": "시설 레벨 부족",
    "insufficient_decor_score": "장식 점수 부족", "ingredient_locked": "재료 잠김",
    "facility_not_found": "시설 없음", "component_not_found": "부품 없음", "cli_error": "호출 실패",
    "wing_cap": "날개 상한", "wing_waste": "날개 헛소모", "loading": "게임 로딩 중",
    "alter_full": "가공 칸 가득 · 창 닫기",
}


def error_short(code) -> str:
    """오버레이용 짧은 한글. 모르는 코드는 코드를 그대로 돌려준다 (지어내지 않는다)."""
    c = _s(code) if isinstance(code, str) else ""
    return ERROR_SHORT.get(c, c)


def card_text(it: dict) -> str:
    """지금 무엇을 하고 있는지 한 줄 — 오버레이 밴드가 그대로 찍는다 (「채집 통나무 60/100」·「제작 가죽 1/3회」)."""
    if not isinstance(it, dict):
        return ""
    p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
    name = _s(it.get("name"))
    t = it.get("type")
    if t == "gather":
        return f"채집 {name} {_n(p.get('done'))}/{_n(it.get('target'))}"
    if t == "craft":
        calls = craft_calls_line(it)   # 나눠 부르는 제작(N7) — 「제작 요리 3/15번 · 30/150회」
        return f"제작 {name} " + (f"{calls} · " if calls else "") + f"{_n(p.get('done'))}/{_n(it.get('count'), 1)}회"
    if t == "alter":
        if alter_line(it):   # 칸·수령을 센 카드 (N6) — 「가공 가죽+ 등록 14/63 · 수령 7 · 칸 5/7」
            return f"가공 {name} {alter_line(it)}"
        return f"가공 {name} {_n(p.get('done'))}/{_n(it.get('count'), 1)}회"
    if t == "collect":
        return f"수령 {_s(it.get('facility')) or name}"
    if t == "play":
        pl = p.get("play") if isinstance(p.get("play"), dict) else None
        if it.get("status") == "running" and pl:      # 도는 중 — 폴리오가 답한 회차
            if pl.get("endless"):
                return f"연주 {name} 반복 재생"
            if pl.get("passes"):
                return f"연주 {name} {_n(pl.get('pass'), 1)}/{_n(pl.get('passes'), 1)}회"
            return f"연주 {name}"
        return f"연주 {name}" + (f" {_n(it.get('count'), 1)}회" if it.get("mode") == "song" else "")
    if t == "notify":
        return f"알림 {_s(it.get('text')) or name}"
    if t == "group":
        return f"그룹 {name} {_n(it.get('loop'))}/{_n(it.get('repeat'), 1)}회차"
    return name


def craft_calls_line(it: dict) -> str:
    """나눠 부르는 제작(N7)의 「3/15번」 — 한 번에 끝나는 카드면 빈 글."""
    p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
    planned = _n(p.get("passesPlanned"), 1)
    if planned <= 1:
        return ""
    return f"{_n(p.get('calls'))}/{planned}번"


def alter_line(it: dict) -> str:
    """칸·수령을 센 가공 카드(N6)의 「등록 14/63 · 수령 7 · 칸 5/7」. 아직 센 적이 없으면 빈 글.
    등록·수령은 앱이 센 값(카드에 저장), 칸은 마지막으로 읽은 게임 대기열의 그 시설 건수다."""
    p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
    if p.get("cap") is None and not _n(p.get("got")):
        return ""
    out = f"등록 {_n(p.get('done'))}/{_n(it.get('count'), 1)} · 수령 {_n(p.get('got'))}"
    if p.get("cap") is not None:
        out += f" · 칸 {_n(p.get('slot'))}/{_n(p.get('cap'))}"
    return out


def _line_of(it: dict) -> str:
    """그룹 요약 한 줄에 쓰는 자식 표현 (「약초 채집 100」·「비약 제작 3회」)."""
    name = _s(it.get("name"))
    t = it.get("type")
    if t == "gather":
        return f"{name} 채집 {_n(it.get('target'))}"
    if t == "craft":
        return f"{name} 제작 {_n(it.get('count'), 1)}회"
    if t == "alter":
        return f"{name} 가공 {_n(it.get('count'), 1)}회"
    if t == "collect":
        return f"{_s(it.get('facility')) or name} 수령"
    if t == "play":
        return f"{name} 연주" + (f" {_n(it.get('count'), 1)}회" if it.get("mode") == "song" else "")
    if t == "notify":
        return "알림"
    return name


def column_of(it: dict) -> str:
    """단일 카드는 COLUMN_OF 표 그대로. 그룹은 네 단계:

    1. 그룹이 running 이거나 자식이 running/waiting → run
    2. 그룹이 error → fail — **치명(FATAL_ERRORS)일 때만 그룹에 error 가 적힌다**
    3. 그룹이 done → done
    4. 그 밖(pending · stopped) → 남은 항목(pending 자식)이 있으면 wait, 없으면 done

    전에는 그룹 status 가 error/stopped 면 무조건 fail 이었다. 그래서 **자식 하나가 실패하면
    (「오류 시 정지」 포함) 그룹 카드 전체가 보드 실패 열로 갔다**.
    자식의 실패는 그 자식만 그룹 안 실패 열에 남고, 그룹이 보드 실패 열로 가는 것은
    blocked·연결 끊김·게임 꺼짐처럼 **다음 항목도 성공할 수 없는 오류**일 때뿐이다."""
    if it.get("type") == "group":
        kids = it.get("items") if isinstance(it.get("items"), list) else []
        if it.get("status") == "running" or any(k.get("status") in ("running", "waiting") for k in kids):
            return "run"
        if it.get("status") == "error":
            return "fail"
        if it.get("status") == "done":
            return "done"
        if it.get("status") == "stopped":
            return "wait" if group_has_left(it) else "done"
        return "wait"
    return COLUMN_OF.get(it.get("status"), "wait")


def group_has_left(g: dict) -> bool:
    """정지한 그룹에 **할 일이 남았나** — 아직 안 한 자식(pending) · 끊긴 자식(stopped) · 남은 회차.
    남았으면 대기 열에 서고 「▶ 시작」이 이어 간다(start). 없으면 완료다."""
    kids = g.get("items") if isinstance(g.get("items"), list) else []
    if any(k.get("status") in ("pending", "stopped") for k in kids):
        return True
    return 0 < _n(g.get("loop")) < min(REPEAT_MAX, max(1, _n(g.get("repeat"), 1)))


def group_fail_badge(g: dict) -> int:
    """완료 열 그룹 카드의 「실패 n」 배지 수.

    그룹이 마지막 회차의 **마지막 자식**에서 「오류 시 정지」로 멈추면 남은 항목이 없어 완료 열에 선다
    (`group_has_left` 가 거짓). 자리는 맞다 — 그룹 실패 열은 치명일 때만이다. 그런데 접힌 카드만 보면
    **실패한 자식이 안에 숨는다.** 그래서 완료 열 그룹에만 안쪽 실패 열의 수를 실어 준다.
    실패 열(치명) 그룹은 오류 상자가, 작업 중 열 그룹은 4등분 줄이 이미 말하므로 0 이다."""
    if not isinstance(g, dict) or g.get("type") != "group" or column_of(g) != "done":
        return 0
    kids = g.get("items") if isinstance(g.get("items"), list) else []
    return sum(1 for k in kids if isinstance(k, dict) and column_of(k) == "fail")


def group_summary(g: dict) -> dict:
    """그룹 요약. roundDone 은 **이번 회차에 done 으로 끝난 자식 수**다 (실패는 inner.fail 로 본다)."""
    kids = g.get("items") if isinstance(g.get("items"), list) else []
    inner = {"wait": 0, "run": 0, "done": 0, "fail": 0}
    for k in kids:
        inner[column_of(k)] += 1
    parts = [_line_of(k) for k in kids[:2]]
    line = " → ".join(parts) + (f" 외 {len(kids) - 2}" if len(kids) > 2 else "")
    return {"count": len(kids), "line": line,
            "roundDone": sum(1 for k in kids if k.get("status") == "done"), "roundTotal": len(kids),
            "totalDone": sum(_n((k.get("progress") or {}).get("totalDone")) for k in kids),
            "inner": inner}


def _n(v, default: int = 0) -> int:
    """숫자 강제. **NaN·Infinity 는 값이 아니라 default** — `json.loads` 는 둘 다 받아들이므로
    (요청 본문·손으로 고친 queue.json) `int()` 가 ValueError/OverflowError 로 죽으면 기동·API 가 통째로 선다."""
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) else default
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return default
        return int(f) if math.isfinite(f) else default
    return default


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def bag_count(name: str) -> int:
    """보유(캐시 기준) = 가방 + 창고 합산. 게임은 창고 재료를 원격으로 가져와 제작하므로(이송 비용만) 합산이 보유다."""
    return _n(work.stock(store.get_cache("items")["data"])["total"].get(name))


def _have(snap: dict, name: str) -> int:
    """스냅샷의 보유 = 가방(stock) + 창고(storage) 합산."""
    return _n(snap.get("stock", {}).get(name)) + _n(snap.get("storage", {}).get(name))


LIMITS_FILE = "recipe_limits.json"   # 시설 상한 학습: {recipeId: maxCount} — execute_crafting 의 invalid_count 응답(maxCount)에서


def limits_get(recipe_id: str) -> int | None:
    d = store.load(LIMITS_FILE, {})
    v = d.get(recipe_id) if isinstance(d, dict) else None
    return _n(v) if _n(v) > 0 else None


def limits_set(recipe_id: str, max_count: int) -> None:
    d = store.load(LIMITS_FILE, {})
    d = d if isinstance(d, dict) else {}
    d[recipe_id] = int(max_count)
    try:
        store.save(LIMITS_FILE, d)
    except Exception:
        pass


def craft_calls(count: int, max_count) -> int:
    """제작 count 회를 시설 상한 max_count 로 나눈 호출 수 (N7). 상한을 모르면 1 — 한 번에 보내 보고,
    invalid_count 의 maxCount 를 배우면 그 자리에서 나눠 잇는다 (거절은 시작 전이라 날개 0)."""
    c = max(0, _n(count))
    m = _n(max_count)
    if c <= 0:
        return 0
    return math.ceil(c / m) if m > 0 else 1


def craft_plan(it: dict, p: dict | None = None) -> int:
    """제작 카드의 예상 호출 수 = 이미 성공한 호출 + 남은 횟수를 시설 상한으로 나눈 수 (상한은 학습값)."""
    p = p if isinstance(p, dict) else (it.get("progress") if isinstance(it.get("progress"), dict) else {})
    count = max(1, _n(it.get("count"), 1))
    left = max(0, count - _n(p.get("done")))
    calls = max(0, _n(p.get("calls")))
    if left <= 0:
        return max(1, calls)
    return calls + craft_calls(left, limits_get(_s(it.get("recipeId"))) if _s(it.get("recipeId")) else None)


# ── 가공 시설 칸 (N6) ──
# 실측(2026-10-02): 가죽 가공 시설 Lv.6 = 7칸. 등록 한 번이 한 칸이고, 작업은 한 번에 하나씩 차례로 돈다.
# 칸이 찬 시설에 execute_altering 을 보내면 **날개 5 가 빠지고** blocked·kind=unknown_modal 로 끝나며 게임에
# 「가공 대기열이 가득 찼습니다」 창이 남는다 — CLI 로 닫히지 않는다. 그래서 칸 참을 오류로 알아내지 않고,
# 등록 직전마다 get_altering_works(읽기, 날개 0)로 그 시설(FacilityName)의 건수를 세어 칸 수에 닿았으면 부르지 않는다.
# 칸 수는 레벨마다 다를 수 있다 — 거절을 한 번 보면 그때의 건수를 그 시설의 칸 수로 기억한다. 기억이 없으면 7.
ALTER_SLOTS_DEFAULT = 7
ALTER_SLOTS_FILE = "alter_slots.json"   # {"facility": {가공 이름: 시설 이름}, "cap": {시설 이름: 칸 수}}
ALTER_COLLECT_AT_DEFAULT = 7            # 설정 alter_collect_at 의 기본 — 「가공 완료가 n개 이상 모이면 받으러 가기」


def _slots_file() -> dict:
    d = store.load(ALTER_SLOTS_FILE, {})
    d = d if isinstance(d, dict) else {}
    for k in ("facility", "cap"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def alter_facility_get(name: str) -> str:
    """가공 이름 → 시설 이름. 기억(alter_slots.json)이 먼저, 없으면 레시피 DB 가 대기열에서 본 시설 목록."""
    f = _s(_slots_file()["facility"].get(_s(name)))
    if f:
        return f
    try:
        import recipedb
        db = store.load(recipedb.FILE, {})
        fac = db.get("facilities") if isinstance(db, dict) else None
        for fname, row in (fac.items() if isinstance(fac, dict) else ()):
            if isinstance(row, dict) and _s(name) in (row.get("works") or []):
                return _s(fname)
    except Exception:
        pass
    return ""


def alter_facility_set(name: str, facility: str) -> None:
    name, facility = _s(name), _s(facility)
    if not name or not facility:
        return
    d = _slots_file()
    if d["facility"].get(name) == facility:
        return
    d["facility"][name] = facility
    try:
        store.save(ALTER_SLOTS_FILE, d)
    except Exception:
        pass


def alter_cap_get(facility: str) -> int:
    v = _n(_slots_file()["cap"].get(_s(facility)))
    return v if v > 0 else ALTER_SLOTS_DEFAULT


def alter_cap_set(facility: str, cap: int) -> None:
    facility, cap = _s(facility), _n(cap)
    if not facility or cap <= 0:
        return
    d = _slots_file()
    d["cap"][facility] = cap
    try:
        store.save(ALTER_SLOTS_FILE, d)
    except Exception:
        pass


def collect_at(settings: dict | None, cap: int = ALTER_SLOTS_DEFAULT) -> int:
    """설정 「가공 완료가 n개 이상 모이면 받으러 가기」 — 1 ~ 그 시설의 칸 수."""
    v = _n((settings or {}).get("alter_collect_at"), ALTER_COLLECT_AT_DEFAULT)
    return max(1, min(max(1, _n(cap, ALTER_SLOTS_DEFAULT)), v))


# ── 전장 안 (정지가 아니라 경고) ──
# 실측: 전장 안(IsInBattleField=true)에서 execute_altering 을 보내자 CLI 가 스스로 전장을 나와
# 금속 가공 시설까지 이동해 등록했다 — 한 번 부르는 데 약 50초(49.8초). 이동이 실행 명령 안에 들어 있으므로
# 실행 명령의 타임아웃(EXEC_TIMEOUT 660초)은 이보다 넉넉히 커야 한다 — **낮추지 마라.**
BATTLEFIELD_WARN = "전장 안(Battlefield.IsInBattleField=true) — 시설로 가는 이동이 안 될 수 있습니다. 실행해 보고 CLI 의 답으로 판단합니다"
# 전장 안에서 부른 가공·제작·수령이 실패하면 그 오류 문구 뒤에 붙인다 (원인을 짐작할 수 있게)
BATTLEFIELD_FAIL_HINT = "전장 안에서 실행 — 시설로 이동하지 못한 것으로 보입니다. 전장을 나온 뒤 ↻ 로 다시 시도하세요"

# ── 전투 중 (정지가 아니라 경고) ──
# 실측: IsInCombat=true 에서도 execute_altering 은 result=started 로 출발한다(날개 −5). 몬스터에 붙잡히면
# 이동이 끊겨 result=stopped_by_user 로 돌아온다 — 그래서 막지 않고, 끊기면 그 카드에 아래 힌트를 붙인다.
COMBAT_WARN = "전투 중(IsInCombat=true) — 출발은 되지만 몬스터에 붙잡히면 이동이 끊기고 날개는 소모됩니다"
COMBAT_FAIL_HINT = "전투 중 출발 — 이동이 끊긴 것으로 보입니다"

# ── 사망/부활 (우리가 막지 않는다 — CLI 가 스스로 거절한다) ──
# 실측: IsDead=true 에서 execute_altering → 0.1초 만에 blocked kind=dead, 날개 0. 비용이 없으므로 우리 점검으로
# 막을 이유가 없다. 다만 부활 직후에는 CLI 의 사망 깃발이 **잔상**으로 남는다 (get_activity 는 IsDead=false 인데
# CLI 는 blocked kind=dead) — 그래서 kind=dead 는 DEAD_RETRY_WAIT 초 뒤 **한 번만** 같은 명령을 다시 보낸다.
DEAD_RETRY_WAIT = 3.0
DEAD_WARN = "사망/부활로 보고됩니다 — 우리는 막지 않고 CLI 의 답으로 판단합니다 (거절은 비용 없음)"
# blocked 의 kind 별 안내 (카탈로그 원문 message 보다 먼저 보여 준다). 모르는 kind 는 원문 그대로.
BLOCKED_KIND_KO = {
    "dead": "사망/부활 상태로 보고됩니다 — 부활을 고른 뒤 ↻",
    # 실측: 지역 임무 포기 확인창·「사냥터 클리어!」 결과 화면. get_activity 에는 안 보인다 (대화 깃발 전부 false).
    "unknown_modal": "게임에 확인창·결과 화면이 떠 있습니다 — 닫은 뒤 ↻",
}


def _dead_blocked(r) -> bool:
    """CLI 가 blocked kind=dead 로 거절했나 (실행 명령 응답)."""
    b = getattr(r, "body", None)
    return (not getattr(r, "ok", True)) and getattr(r, "error", None) == "blocked" \
        and isinstance(b, dict) and b.get("kind") == "dead"


def max_suggested_value(max_by_stock, max_count) -> int | None:
    """재고 기준 최대와 시설 상한 중 작은 값. 둘 다 모르면 None."""
    vals = [v for v in (max_by_stock, max_count) if isinstance(v, int) and not isinstance(v, bool)]
    return min(vals) if vals else None


def max_suggested(r: dict, snap: dict) -> dict:
    """/api/plan·add(count:"max") 공용: {maxByStock, maxByStockPartial, maxCount, maxSuggested}. alter 는 호출당 1건이라 상한 없음."""
    mbs, partial = work.max_by_stock(r, snap.get("stock", {}), snap.get("storage", {}))
    mc = limits_get(r.get("id", "")) if r.get("kind") == "craft" else None
    return {"maxByStock": mbs, "maxByStockPartial": partial, "maxCount": mc, "maxSuggested": max_suggested_value(mbs, mc)}


# 수량 상한 — add·update 가 **같은 검증**을 쓴다. add 에만 없으면 사용자가 보낸 값이 그대로 저장돼
# 확인창 수치와 게임에 전달되는 craftCount 가 검증 없이 커진다.
TARGET_MAX = 99_999   # 채집 개수(target)
COUNT_MAX = 999       # 제작·가공 횟수


def check_count(count: int) -> dict | None:
    """제작·가공 횟수 검증. 문제가 있으면 오류 dict, 없으면 None."""
    if not 1 <= count <= COUNT_MAX:
        return {"ok": False, "error": "bad_request", "message": f"count 는 1~{COUNT_MAX} 사이여야 합니다."}
    return None


def check_gather(target: int, max_passes: int) -> tuple:
    """채집 개수 검증. (오류 dict | None, plan | None). plan = {passesPlanned: ⌈target/100⌉, need: target}."""
    if not 1 <= target <= TARGET_MAX:
        return {"ok": False, "error": "bad_request", "message": f"채집 개수는 1~{TARGET_MAX:,} 사이여야 합니다."}, None
    plan = gather_plan(target)
    if plan["passesPlanned"] > max_passes:
        return {"ok": False, "error": "max_passes",
                "message": f"예상 {plan['passesPlanned']}회가 안전 상한({max_passes}회)을 넘습니다. 개수를 나누세요."}, None
    return None, plan


def gather_runs(it: dict) -> int:
    """채집 카드의 예상 회수 = ⌈target/100⌉ (최소 1). 회수는 개수에서 따라 나오는 값이다 — 카드에 따로 적지 않는다."""
    t = _n(it.get("target")) if isinstance(it, dict) else 0
    return max(1, math.ceil(t / MAX_PER_PASS)) if t > 0 else 1


def target_from_request(d: dict):
    """요청(add item · update patch)에서 채집 개수를 읽는다 → int, 없으면 None.
    target(개수)이 먼저다. 1.0.9 화면은 회수를 runs(담기)·count(고치기)로 보냈다 — 그 값은 ×100 개로 읽는다
    (그 화면에서 1회 = 최대 100개였다). 범위를 넘는 값은 그대로 돌려 검증이 거절하게 한다."""
    if not isinstance(d, dict):
        return None
    if d.get("target") is not None:
        return _n(d.get("target"))
    for k in ("runs", "count"):
        if d.get(k) is not None:
            return _n(d.get(k)) * MAX_PER_PASS
    return None


def alter_mode(collect) -> str:
    """가공 항목의 수령 모드. 값이 없거나 모르는 값이면 **"later"** — 필드가 없던 시절의 저장본은 등록 뒤 수령이
    기본 동작이었고, 그걸 "none"(걸기만)으로 읽으면 이미 등록·결제된 가공을 수령하지 않고 버린다.
    새로 담는 항목의 기본값이 "none" 인 것은 add() 가 정한다."""
    return collect if collect in ALTER_MODES else "later"


def alter_trips(count: int, collect, got: int = 0, done: int = 0) -> int:
    """수령 왕복 예상 (날개 0). 칸이 ALTER_SLOTS_DEFAULT(7) 라고 보고 센다 — 7건마다 한 번.
    「걸기만」(none)은 끝에 받지 않는다 — 칸을 비워야 남은 등록을 걸 수 있을 때만 (7건을 넘는 몫)."""
    s = ALTER_SLOTS_DEFAULT
    count, got, done = max(0, _n(count)), max(0, _n(got)), max(0, _n(done))
    if alter_mode(collect) == "none":
        regs = max(0, count - done)
        return math.ceil(regs / s) if done > 0 else math.ceil(max(0, count - s) / s)
    return math.ceil(max(0, count - got) / s)


def alter_passes(count: int, collect) -> int:
    """가공 항목의 passesPlanned = 등록 count 회 (+ 수령 1회 — 「걸기만」이 아닐 때만). **안전 상한(queue_max_passes)에 대는 값**이다 —
    칸을 비우는 수령 왕복(alter_trips)까지 더하면 44건짜리 카드가 상한 50 에 걸린다(예전에는 45). 화면·확인창의 「예상 N회」는
    왕복까지 센 alter_calls 를 쓴다."""
    return count if alter_mode(collect) == "none" else count + 1


def alter_calls(it: dict) -> tuple:
    """가공 카드 한 장의 (호출, 날개 호출) — 남은 등록 + 남은 수령 왕복. 날개는 등록만 (수령은 0).
    화면(board.js itemCalls·viewmodel.item_calls)과 확인창(_preview_card)이 같은 규칙을 쓴다."""
    p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
    count = max(1, _n(it.get("count"), 1))
    done = min(count, max(0, _n(p.get("done"))))
    got = min(done, max(0, _n(p.get("got"))))
    regs = 0 if it.get("regStop") else count - done
    if it.get("status") == "waiting" and alter_mode(it.get("collect")) != "none" and regs == 0:
        return max(1, alter_trips(count, it.get("collect"), got, done)), 0   # 수령만 남았다 — 날개 없음
    return regs + alter_trips(count, it.get("collect"), got, done), regs


def gather_plan(target: int) -> dict:
    """target 은 **이번에 캘 개수**다 (보유량을 채우는 목표가 아니다 — 시작 전 가방 + target 에 닿으면 끝난다).
    CLI 는 호출 한 번에 최대 MAX_PER_PASS 개를 캐고 멈추므로 예상 회수는 그 단순 나눗셈이다."""
    need = max(0, target)
    return {"passesPlanned": math.ceil(need / MAX_PER_PASS) if need else 0, "need": need}


def is_fatal(error: str | None) -> bool:
    return bool(error) and error in FATAL_ERRORS


def _get_ci(d, key: str):
    """대소문자 무시 키 조회 — 카탈로그 예시는 combatState/dialogue 는 소문자, Dungeon/Mode 는 대문자로 시작한다."""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    lk = key.lower()
    return next((v for k, v in d.items() if isinstance(k, str) and k.lower() == lk), None)


def _field(b: dict, key: str, *wrappers: str):
    """키를 최상위에서 먼저 찾고, 없으면 래퍼(combatState/dialogue/autoPlay …) 안에서 찾는다 — 전부 대소문자 무시.
    **실제 게임 응답은 평면**(IsDead·IsReviving·IsWaitingForSelection 이 최상위, fixtures/get_activity.real.json)이고
    카탈로그 예시만 combatState/dialogue 래퍼로 적혀 있다. 둘 다 읽어야 실제 IsReviving=true 를 놓치지 않는다 (실제 사고)."""
    v = _get_ci(b, key)
    if v is not None:
        return v, key
    for w in wrappers:
        inner = _get_ci(b, w)
        if isinstance(inner, dict):
            v = _get_ci(inner, key)
            if v is not None:
                return v, f"{w}.{key}"
    return None, key


def perf_of(body) -> dict:
    """`get_activity` 의 Performance 칸 → 연주 상태. **읽기라 값이 안 든다.**

    카탈로그가 적어 둔 칸:
    `Performance: { IsPlaying, InstrumentName, MusicTitle, IsCopyingAllowed, IsLoop,
    StartAt, TotalDurationSeconds, ElapsedSeconds, RemainingSeconds, ChannelCount }`

    조회 **한 번**이 세 가지를 한꺼번에 답한다 — 연주 중인가 · 얼마나 남았나 · 반복인가.
    그래서 「연주를 멈추기 전에 먼저 확인」이 **추가 호출 없이** 된다.

    **`IsLoop` 으로 「끝이 없다」를 판정하면 안 된다.** `IsLoop` 은 **게임 쪽 설정**이고,
    모비폴리오 엔진은
    곡이 끝나기 조금 전에 `stop_action` 으로 끊고 다음 곡으로 넘긴다. 그러니 게임이
    반복이어도 **그쪽에서는 곡이 끝난다.** 정말 끝이 없는 경우는 **그쪽 설정이
    「한 곡 반복」일 때뿐**이고, 그건 `get_activity` 에 안 보인다.

    그래서 여기서는 **읽은 대로만 적는다** — `loop` 은 참고, `remaining` 은 게임이 준 값.
    「언제 끝납니까」의 진짜 답은 모비폴리오에게 물어야 한다 (`WorkQueue.perf_gate`).
    물을 데가 없을 때를 위해 기다리기에는 **상한**이 걸려 있다 (`PERF_WAIT_MAX`)."""
    b = body if isinstance(body, dict) else {}
    # **`Performance` 안을 먼저 본다** — `_field` 처럼 최상위부터 찾으면 안 된다.
    # `IsPlaying` 은 `Tutorial` 도 쓰는 이름이라, 평평한 응답에서 남의 칸을 집어 올 수 있다.
    # 다른 칸(MusicTitle 등)은 겹치지 않지만, 한 곳에서 다 읽는 편이 규칙이 하나다.
    p = _get_ci(b, "Performance")
    p = p if isinstance(p, dict) else {}
    playing = _get_ci(p, "IsPlaying")
    title = _get_ci(p, "MusicTitle")
    inst = _get_ci(p, "InstrumentName")
    loop = _get_ci(p, "IsLoop")
    left = _get_ci(p, "RemainingSeconds")
    total = _get_ci(p, "TotalDurationSeconds")
    try:
        left = None if left is None else max(0.0, float(left))
    except (TypeError, ValueError):
        left = None
    try:
        total = None if total is None else float(total)
    except (TypeError, ValueError):
        total = None
    return {"playing": bool(playing), "title": title if isinstance(title, str) else "",
            "instrument": inst if isinstance(inst, str) else "", "loop": bool(loop),
            "remaining": left, "total": total}


def activity_summary(body) -> dict:
    """get_activity 응답 → 실행 가능 여부. 평면(실측)과 중첩(카탈로그 예시) 모양을 둘 다 읽는다. 응답이 깨져도 던지지 않는다.
    막는 것(FATAL, 이 순서): 대화 선택 대기 → precheck_dialog, 던전(State≠NotInDungeon) → precheck_dungeon,
    지역 임무 진행·정산 중(IsAutoPlaying 이면서 AutoPlayTarget=goddess_mission) → precheck_mission,
    시나리오/튜토리얼/어비스 연출 → precheck_scenario.
    막지 않고 경고만: 자동사냥 중(IsAutoPlaying), **전장 안(Battlefield.IsInBattleField)**, **전투 중(IsInCombat)**,
    **사망/부활(IsDead/IsReviving)** — 실측(docs/CLI.md §7):
      · 전투 중: 출발은 된다(started, 날개 −5). 붙잡히면 stopped_by_user 로 끊긴다 → 경고 + 끊기면 힌트.
      · 사망: CLI 가 0.1초 만에 blocked kind=dead 로 스스로 거절한다(날개 0) → 막을 이유가 없다. 잔상은 _exec 가 1회 재시도.
      · 지역 임무: 출발해 석상까지 워프(추가 비용)한 뒤 포기 확인창·결과 화면에서 blocked kind=unknown_modal —
        날개만 태우고 get_activity 로는 그 창이 안 보인다 → 보내기 **전에** 막는다.

    전장은 예전에 precheck_battlefield 로 막았다. 그런데 광석·일부 목재는 전장에서만 캐지고
    execute_gathering 이 스스로 전장으로 이동한다 — 그래서 [채집 → 가공] 체인은 채집 직후 항상 전장 안에서
    가공을 만나 멈췄다. 가공·제작·수령 명령은 시설까지 이동을 포함하므로(docs/CLI.md §3)
    막지 않고 시도하고, 실패하면 CLI 의 답으로 판단한다. 실측: 전장 안에서 execute_altering 이 스스로
    시설까지 이동해(약 50초) 등록됐다 — 막던 것은 이 점검뿐이었다. precheck_battlefield 문자열은 옛 기록 때문에 남긴다."""
    b = body if isinstance(body, dict) else {}
    dead, f_dead = _field(b, "IsDead", "combatState")
    reviving, f_rev = _field(b, "IsReviving", "combatState")
    in_combat, f_cmb = _field(b, "IsInCombat", "combatState")
    waiting_sel, f_sel = _field(b, "IsWaitingForSelection", "dialogue")
    auto, _ = _field(b, "IsAutoPlaying", "autoPlay")
    target, _ = _field(b, "AutoPlayTarget", "autoPlay")
    target = target if isinstance(target, str) else None
    mission = bool(auto) and (target or "").lower() == "goddess_mission"
    dungeon = _get_ci(b, "Dungeon") or {}
    dstate = _get_ci(dungeon, "State")
    dstate = dstate if isinstance(dstate, str) else None
    battlefield = bool(_get_ci(_get_ci(b, "Battlefield") or {}, "IsInBattleField"))
    scen = _get_ci(b, "Scenario") or {}
    in_scenario = bool(_get_ci(scen, "IsInScenario")); seq = bool(_get_ci(scen, "IsSequencePlaying"))
    tutorial = bool(_get_ci(_get_ci(b, "Tutorial") or {}, "IsPlaying"))
    abyss = bool(_get_ci(b, "IsAbyssResultSequencePlaying"))
    mode = _get_ci(b, "Mode") or {}
    perf = perf_of(b)
    # 탈것 탑승 — Mode.MountPartState == "Mounted" (실측, 폴리오 engine._mount_state 와 같은 칸).
    # 작업 카드는 막지 않는다 (실행 명령이 스스로 내린다) — 「연주」 카드만 본다 (게임이 탄 채로는 연주를 받지 않는다)
    mounted = _s(_get_ci(mode, "MountPartState")) == "Mounted"
    dead, reviving, in_combat, waiting_sel, auto = bool(dead), bool(reviving), bool(in_combat), bool(waiting_sel), bool(auto)
    out = {"dead": dead, "reviving": reviving, "combat": in_combat, "dialog": waiting_sel, "dungeon": dstate,
           "battlefield": battlefield, "scenario": in_scenario or seq or tutorial or abyss, "autoPlaying": auto,
           "autoPlayTarget": target, "mission": mission, "mounted": mounted,
           "mainButton": _get_ci(mode, "MainButtonState"), "ok": True, "error": None, "field": None, "value": None,
           "perf": perf, "warnings": []}
    if waiting_sel:
        out.update(ok=False, error="precheck_dialog", field=f_sel, value=True)
    elif dstate is not None and dstate != "NotInDungeon":
        out.update(ok=False, error="precheck_dungeon", field="Dungeon.State", value=dstate)
    elif mission:
        out.update(ok=False, error="precheck_mission", field="AutoPlayTarget", value=target)
    elif in_scenario or seq or tutorial or abyss:
        fld = ("Scenario.IsInScenario" if in_scenario else "Scenario.IsSequencePlaying" if seq
               else "Tutorial.IsPlaying" if tutorial else "IsAbyssResultSequencePlaying")
        out.update(ok=False, error="precheck_scenario", field=fld, value=True)
    if dead or reviving:
        out["warnings"].append(DEAD_WARN)
    if in_combat:
        out["warnings"].append(COMBAT_WARN)
    if battlefield:
        out["warnings"].append(BATTLEFIELD_WARN)
    if auto and not mission:
        out["warnings"].append("자동사냥 중(IsAutoPlaying=true) — 실행 명령이 자동사냥을 멈출 수 있습니다")
    tf = lambda v: "T" if v else "F"   # noqa: E731
    out["text"] = (f"상태 점검: 사망 {tf(dead)} 부활 {tf(reviving)} 전투 {tf(in_combat)} 대화 {tf(waiting_sel)} "
                   f"던전 {dstate if dstate is not None else '?'} 전장 {tf(battlefield)} 시나리오 {tf(out['scenario'])}"
                   + (" 자동사냥 T" if auto else "") + (f"({target})" if auto and target else "")
                   + (" 연주 T" if perf["playing"] else "") + (" 탈것 T" if mounted else ""))
    return out


class Queue:
    """큐 모델 + 러너. cli_fn(command, body, timeout) 은 서버의 잠금 있는 호출, raw_cli_fn 은 잠금 없는 호출(정지용)."""

    def __init__(self, cli_fn, raw_cli_fn=None, probe_fn=None, say=print):
        self.cli = cli_fn
        self.raw_cli = raw_cli_fn or cli_fn
        self.probe = probe_fn
        self.say = say
        self.lock = threading.RLock()
        self.items: list[dict] = []
        self.config: dict = {"onError": "continue"}
        self.events: list[dict] = []
        self.running = False
        self.current: str | None = None
        self.current_group: str | None = None
        self.last_error: dict | None = None
        self.stop_reason: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executing = False   # 실행 명령(execute_*)이 파이프를 잡고 있는 중 — 정지 시 stop_action 을 보낼지 판단
        self._exec_n = 0          # 도는 실행 명령 수 — 넘겨준 채집(_orphan)과 다음 카드의 명령이 겹칠 수 있다
        self._exec_lock = threading.Lock()
        self._orphan: dict | None = None   # 목표에 닿아 다음 카드에 넘긴 채집 호출 {fin, it, thread} (_gather_watched)
        self._yielded = False     # 연주 쪽에 「양보」를 부탁한 상태 — 보드가 끝나면 **그 부탁을 거둔다** (이어서 틀지 않는다)
        self._yield_held = False  # 양보를 **받았다** — 그쪽이 경계에서 멈춰 서 있다. 항목마다 다시 부탁하지 않는다
        self.hold: dict | None = None      # 연주 대기 중이면 그 카드 (GET /api/queue 의 hold) — 저장하지 않는다
        self._hold_first = None   # ▶ 시작이 연주 중에 눌렸다 — 러너가 첫 카드 앞에서 이 연주를 기다린다 (perf_of 결과)
        self._removed: set[str] = set()   # 러너가 도는 중에 지워진 id — 결과를 조용히 버리고 남은 회차를 건너뛴다
        self._group_halt: str | None = None   # 회차 끝·카드 사이 수령에서 난 오류로 「오류 시 정지」 그룹을 멈추라는 신호 (그룹 id)
        self._closing = False     # 앱 종료가 시작됐다 — 그 뒤의 「▶ 시작」은 거절한다 (shutdown)
        # 마지막 실행 명령이 가공 등록이었나 — 그 직후 다른 명령이 blocked·unknown_modal 이면 「가공 대기열 가득」 창으로 본다 (N6)
        self._alter_recent = False
        self._post_rows = None    # 마지막 수령 뒤 읽은 대기열 (_exec_collect)
        self._look_err = None     # 마지막 대기열 읽기 실패 (_alter_look)
        # 날개 차단기: (시각, 날개, 헛소모, 명령, 이름). 메모리에만 — ▶ 시작으로 **지우지 않는다** (지우면 상한이 무의미하다)
        self._wing_log: deque = deque()
        self._wing_lock = threading.Lock()
        self._wing_limits = wing_limits(None)   # (총량, 헛소모, 총량 창 분, 헛소모 창 분) — start 가 그때의 설정으로 바꾼다
        self._wing_tripped: str | None = None   # 이번 실행을 세운 차단기 코드 (wing_cap | wing_waste)
        # 알림 — 알림 카드·큐 종료가 남긴 것. 메모리에만, 최근 NOTICES_KEEP 개. 화면(PC 창·폰)이 폴링으로 집어 간다
        self.notices: list = []
        self._notice_seq = 0
        self._load()

    # ── 저장 ──
    def _load_card(self, it: dict, seen_ids: set) -> dict | None:
        """저장된 단일 카드 하나를 되살린다. 재시작 후 자동 재개 없음 규칙과 채집 target 의미 보정이 여기 있다."""
        if not isinstance(it, dict) or not _s(it.get("name")) or it.get("type") not in CARD_TYPES:
            return None
        # 재시작 후 자동 재개 없음: 끝난(done) 것만 남기고 전부 pending 으로. 러너는 사용자가 다시 시작해야 돈다.
        # 단 waiting(가공 등록을 마치고 완료를 기다리는 중)은 그대로 둔다 — pending 으로 되돌리면 이미 등록·결제된
        # 가공을 다시 등록해 정령의 날개를 또 쓰고 같은 가공이 중복으로 걸린다.
        # waiting 은 **가공만** 가질 수 있다 — 다른 종류가 waiting 으로 적혀 있으면 `_awaiting_collect` 가 그 이름으로
        # complete_altering_work 를 보낸다 (손으로 고친 파일에서 재현). 가공이 아니면 pending 으로 돌린다.
        keep = ("done", "waiting") if it["type"] == "alter" else ("done",)
        if it.get("status") not in keep:
            it["status"] = "pending"
            it.pop("error", None); it.pop("message", None)
        # 수량은 add/update 와 **같은 상한**으로 자른다. 파일이 count=5000 을 들고 오면 러너는 그대로 믿고
        # 그만큼 등록했고, `start` 의 상한 검사는 저장된 passesPlanned(=1) 만 봐서 막지 못했다.
        if it["type"] == "gather":
            self._runs_to_target(it)
            it["target"] = max(1, min(TARGET_MAX, _n(it.get("target"), 1)))
        elif it["type"] in ("craft", "alter"):
            it["count"] = max(1, min(COUNT_MAX, _n(it.get("count"), 1)))
        else:
            it["count"] = max(0, _n(it.get("count")))
        # progress 는 **반드시 dict 이고 숫자 칸이 있어야 한다.** 옛 저장본·손으로 고친 파일에 빠져 있으면
        # 러너가 `p["done"]` 에서 KeyError → internal(치명)로 보드를 세웠다. 없는 값은 0 으로 메운다.
        # **음수도 0 이다** — done=-5 인 가공 카드는 count 보다 5번 더 등록했다 (회당 날개 5).
        p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
        for k in ("done", "passes", "totalDone", "have"):
            p[k] = max(0, _n(p.get(k)))
        if it["type"] == "alter":
            # 앱이 센 진행 (N6) — 등록(done)·수령(got). 재시작 뒤에도 여기서 이어 간다. 수령은 등록을 넘지 못한다
            if it.get("status") == "waiting" and "got" not in p:
                # N6 전의 저장본(백업 복원 포함) — 그때의 waiting 은 **등록을 다 마친** 카드였다. done 이 비어 있어도
                # 남은 등록으로 읽으면 이미 결제된 가공을 또 건다 — 다 건 것으로 본다
                p["done"] = it["count"]
            p["done"] = min(p["done"], it["count"])
            p["got"] = min(p["done"], max(0, _n(p.get("got"))))
        if it["type"] == "craft":
            p["done"] = min(p["done"], it["count"])
            p["calls"] = max(0, _n(p.get("calls")))   # 나눠 부른 제작(N7)의 성공한 호출 수
        # passesPlanned 는 **저장된 값을 믿지 않고 늘 다시 센다** — 수량에서 바로 나오는 값이라 잃을 것이 없다
        p["passesPlanned"] = (gather_runs(it) if it["type"] == "gather"
                              else alter_passes(it["count"], it.get("collect")) if it["type"] == "alter"
                              else craft_plan(it, p) if it["type"] == "craft"
                              else 0 if it["type"] in FREE_CARD_TYPES else 1)   # 연주·알림은 호출이 없다
        if it["type"] == "play":
            if it.get("mode") not in PLAY_MODES + (PLAY_MODE_LEGACY,):
                it["mode"] = "resume"
            # 회차 — song 은 1~PLAY_COUNT_MAX (옛 저장본에 없으면 1 = 한 번 재생), resume 은 뜻이 없어 1 로 고정
            it["count"] = max(1, min(PLAY_COUNT_MAX, _n(it.get("count"), 1))) if it["mode"] != "resume" else 1
        if it["type"] == "notify":
            it["text"] = _s(it.get("text"))[:NOTIFY_TEXT_MAX] or NOTIFY_DEFAULT_TEXT
            it["sound"] = it.get("sound") is not False
        it["progress"] = p
        if not isinstance(it.get("log"), list):
            it["log"] = []
        if not _s(it.get("id")) or it["id"] in seen_ids:   # id 는 그룹 안팎을 통틀어 유일해야 한다 (러너가 id 커서를 쓴다)
            it["id"] = uuid.uuid4().hex[:10]
        seen_ids.add(it["id"])
        # 채집 target 의 의미가 「목표 보유량」 → 「캘 개수」로 바뀌었다. 옛 항목은 절대값(보유 + 부족분)이라
        # 그대로 두면 가진 만큼 더 캐게 된다(보유 1,691 · target 1,901 → 1,901회분). 담을 때 기록해 둔
        # progress.have 를 빼서 실제 부족분으로 되돌린다. 한 번만 — 새 항목은 add 가 플래그를 달고 나온다.
        if it["type"] == "gather" and it["status"] == "pending" and not it.get("targetMigrated"):
            old = _n(it.get("target"))
            new = max(MAX_PER_PASS, old - _n(it.get("progress", {}).get("have")))
            it["targetMigrated"] = True
            if new != old:
                it["target"] = new
                it["log"].append({"t": time.time(), "msg": f"의미 변경으로 보정: 목표 {old}개 → {new}개 캐기"})
            it["progress"]["passesPlanned"] = gather_runs(it)
        return it

    # 1.0.9 의 회수 카드가 불러올 때 남긴 줄 — 「개수 → 회수로 바뀜: 250개 → 3회」. 이 줄이 있으면 그 개수로 되돌린다
    _RUNS_LOG = re.compile(r"개수 → 회수로 바뀜: (\d+)개 → (\d+)회")

    def _runs_to_target(self, it: dict) -> None:
        """1.0.9 의 회수 카드(runs) → 개수(target). 한 번만 (runs 칸을 지운다).

        1.0.9 는 채집을 회수로 담았고 target 에는 runs×100 을 적었다. 이제 개수가 기준이라 target = runs×100 으로 둔다.
        그 전(1.0.7)에 개수로 담았던 카드를 1.0.9 가 회수로 바꾼 것이면 로그에 원래 개수가 남아 있다 — 그 값으로 되돌린다."""
        if "runs" not in it:
            return
        runs = _n(it.pop("runs"))
        if runs < 1:
            return
        target = runs * MAX_PER_PASS
        for row in reversed(it.get("log") if isinstance(it.get("log"), list) else []):
            m = self._RUNS_LOG.search(str((row or {}).get("msg") or "")) if isinstance(row, dict) else None
            if m and _n(m.group(2)) == runs and 1 <= _n(m.group(1)) <= target:
                target = _n(m.group(1))
                break
        it["target"] = min(TARGET_MAX, target)
        it["targetMigrated"] = True   # 1.0.7 의 「목표 보유량」 보정은 이미 지난 카드다
        if not isinstance(it.get("log"), list):
            it["log"] = []
        it["log"].append({"t": time.time(), "msg": f"회수 → 개수로 바뀜: {runs}회 → {it['target']}개 "
                                                   f"(가방이 그만큼 늘면 멈춥니다)"})

    def _load(self) -> None:
        d = store.load(FILE, {})
        d = d if isinstance(d, dict) else {}
        items = d.get("items")
        seen_ids: set = set()
        out = []
        for it in (items if isinstance(items, list) else []):
            if isinstance(it, dict) and it.get("type") == "group":
                g = self._coerce_group(it, seen_ids)
                if g is not None:
                    out.append(g)
                continue
            c = self._load_card(it, seen_ids)
            if c is not None:
                out.append(c)
        cfg = d.get("config") if isinstance(d.get("config"), dict) else {}
        # ── 이관: 큐 전체 반복이 그룹 「큐 반복」으로 옮겨 간다. config.repeat 는 지워 저장하므로
        #    다음 기동에는 이 분기가 다시 타지 않는다 (정확히 한 번).
        old_repeat = min(REPEAT_MAX, max(1, _n(cfg.get("repeat"), 1)))
        if old_repeat > 1 and out:
            wrap = self._new_group("큐 반복", repeat=old_repeat)
            wrap["items"] = out
            wrap["log"].append({"t": time.time(), "msg": f"전체 반복 {old_repeat}회 → 그룹 「큐 반복」으로 옮김"})
            self.say(f"[queue] 전체 반복 {old_repeat}회 → 그룹 「큐 반복」으로 옮김")
            out = [wrap]
        self.items = out
        self.config = self._coerce_config(cfg, self.config)
        if old_repeat > 1 and self.items:
            self._save()   # repeat 를 지운 모양으로 곧바로 굳힌다

    def _coerce_group(self, g: dict, seen_ids: set) -> dict | None:
        """저장된 그룹 하나를 되살린다. 깊이 1단 — 그룹 안의 그룹은 자식만 뽑아 펼친다 (작업을 버리지 않는다)."""
        out = self._new_group(_s(g.get("name")), repeat=_n(g.get("repeat"), 1))
        if not _s(g.get("id")) or g["id"] in seen_ids:
            g["id"] = out["id"]
        out["id"] = g["id"]
        seen_ids.add(out["id"])
        out["onError"] = g.get("onError") if g.get("onError") in ("continue", "stop") else "continue"
        out["retryFailed"] = g.get("retryFailed") is not False
        out["waitAlter"] = g.get("waitAlter") is True
        out["log"] = g.get("log") if isinstance(g.get("log"), list) else []
        kids: list = []
        raw = g.get("items") if isinstance(g.get("items"), list) else []
        flat: list = []
        for k in raw:   # 깊이 1단 보장: 중첩 그룹은 자식만 끌어올린다
            if isinstance(k, dict) and k.get("type") == "group":
                flat += [x for x in (k.get("items") or []) if isinstance(x, dict)]
            else:
                flat.append(k)
        for k in flat:
            c = self._load_card(k, seen_ids)
            if c is not None:
                kids.append(c)
        out["items"] = kids
        # **그룹이 제 자식과 같은 말을 하게 한다.**
        #
        # 재시작하면 그룹은 `loop = 0`(시작 안 함)으로 돌아가는데, **자식의 `done`·`waiting`
        # 은 그대로 둔다** — 이미 등록·결제된 가공을 다시 등록하지 않으려는 규칙이다.
        # 그 결과 **그룹은 「시작도 안 했다」, 자식은 「3개 중 2개 끝났다」**가 된다.
        # 회차 막대는 `loop` 를 보고 칸을 가르므로 `loop == 0` 이면 두 분기 모두 거짓이라
        # **전부 빈 칸**이 됐다 (「2/3 · 누적 101」인데 막대가 텅 비었다).
        #
        # `loop` 는 **몇 회차를 보여 줄지**일 뿐 재개를 일으키지 않는다. 그래서 여기서
        # 맞춰 두면 「재시작 후 자동 재개 없음」과 부딪히지 않고, **이 값을 읽는 화면이
        # 한 번에 맞아진다** (화면에서 때우면 서버는 계속 어긋난 값을 낸다).
        # `done` 과 `waiting` 뿐이다 — 되살릴 때 그 둘만 살아남고 나머지는 `pending` 으로
        # 돌아간다(`_load_card`). `error`·`stopped` 를 여기 적어 두면 **아무 때도 안 걸리는
        # 죽은 값**이 되고, 그러면 「그것도 본다」는 착각만 남는다.
        started = any(k.get("status") in ("done", "waiting") for k in kids)
        loop = max(0, min(REPEAT_MAX, _n(g.get("loop"))))   # 음수·거대값은 파일이 준 것이지 회차가 아니다
        out["loop"] = max(1, loop) if started else loop
        # **끝난 그룹은 끝난 채로.** 새 그룹 틀은 pending 이라, 저장본의 done 을 그대로 버리면 마지막 회차까지
        # 끝낸 그룹이 재시작 뒤 **대기 열**로 넘어갔다(column_of 의 규칙 4 는 pending 을 늘 wait 로 본다) — 그런데
        # 할 일이 없어 「▶ 시작」은 「실행할 항목이 없습니다」였다. 남은 일이 정말 없을 때만 done 을 살린다.
        # 실패 자식은 `_load_card` 가 pending 으로 되돌리므로 그런 그룹은 여기 안 걸리고 대기 열로 간다 (규칙 그대로).
        if g.get("status") == "done" and kids and not group_has_left(out):
            out["status"] = "done"
        return out if kids or _s(g.get("name")) else None

    def _new_group(self, name: str = "", repeat: int = 1) -> dict:
        n = _s(name)[:GROUP_NAME_MAX]
        if not n:
            n = f"그룹 {sum(1 for x in self.items if x.get('type') == 'group') + 1}"
        return {"id": uuid.uuid4().hex[:10], "type": "group", "name": n,
                "repeat": min(REPEAT_MAX, max(1, _n(repeat, 1))), "loop": 0, "onError": "continue",
                "retryFailed": True, "waitAlter": False, "status": "pending", "log": [], "items": []}

    def _save(self) -> None:
        try:
            store.save(FILE, {"items": self.items, "config": self.config})
        except Exception as e:
            self.say(f"[queue] save failed: {type(e).__name__}: {e}")

    @staticmethod
    def _coerce_config(patch: dict, base: dict) -> dict:
        """보드 전역 설정은 onError 뿐이다. repeat 는 그룹으로 옮겨 갔다."""
        out = {"onError": base.get("onError", "continue")}
        oe = patch.get("onError")
        if oe in ("continue", "stop"):
            out["onError"] = oe
        return out

    # ── 조회 ──
    def _view(self, it: dict) -> dict:
        """API 로 나가는 카드 한 장. column 은 서버만 판정한다."""
        out = dict(it)
        out["column"] = column_of(it)
        if it.get("status") == "error" and self._stuck(it):
            out["stuck"] = True          # 남은 회차에서 뺀 카드 — 화면이 이유를 적는다

        if it.get("type") == "group":
            out["items"] = [self._view(k) for k in (it.get("items") or [])]
            out["summary"] = group_summary(it)
            out["failBadge"] = group_fail_badge(it)
        return out

    def state(self) -> dict:
        """GET /api/queue 의 응답. **CLI 를 부르지 않는다** — 오버레이가 1초마다 읽는다."""
        with self.lock:
            items = [self._view(x) for x in self.items]
            columns = {"wait": 0, "run": 0, "done": 0, "fail": 0}
            for v in items:
                columns[v["column"]] += 1
            cur = self._find(self.current) if self.current else None
            g = self._group_of(self.current) if self.current else None
            err = None
            if self.last_error:
                err = dict(self.last_error)
                err.setdefault("code", err.get("error"))
                err.setdefault("short", error_short(err.get("error")))
            return {"items": items, "running": self.running, "current": self.current,
                    "currentGroup": g["id"] if g else None,
                    "currentCard": ({"id": cur["id"], "type": cur["type"], "name": cur["name"],
                                     "text": card_text(cur), "column": column_of(cur)} if cur else None),
                    "currentGroupRound": ({"loop": _n(g.get("loop")), "repeat": _n(g.get("repeat"), 1)} if g else None),
                    "lastError": err, "stopReason": self.stop_reason, "onError": self.config["onError"],
                    # 연주 대기 — 러너가 연주가 끝나기를 기다리는 중. 카드가 아니다(저장 안 함). 보드가 작업 중 칸 맨 위에 그린다
                    "hold": dict(self.hold) if self.hold else None,
                    # 알림 — 알림 카드·큐 종료가 남긴 최근 열 개 {id, t, text, sound, kind, card}
                    "notices": [dict(n) for n in self.notices],
                    "columns": columns,
                    "progress": {"done": columns["done"] + columns["fail"], "total": len(items)},
                    **self.wing_window(),
                    "events": list(self.events)}

    # ── 날개 차단기 ──
    def wing_window(self) -> dict:
        """최근 창의 소모 — {"wings10m": 날개 수, "waste5m": 헛소모 횟수}. 창을 벗어난 기록은 여기서 버린다. CLI 를 부르지 않는다."""
        now = _now()
        cap_min, waste_min = self._wing_limits[2], self._wing_limits[3]
        keep = max(cap_min, waste_min) * 60      # 두 창 중 긴 쪽까지는 기록을 남긴다
        with self._wing_lock:
            while self._wing_log and self._wing_log[0][0] < now - keep:
                self._wing_log.popleft()
            rows = list(self._wing_log)
        return {"wings10m": sum(x[1] for x in rows if x[0] >= now - cap_min * 60),
                "waste5m": sum(1 for x in rows if x[2] and x[0] >= now - waste_min * 60)}

    def _wing_record(self, command: str, it: dict, r) -> None:
        """실행 명령 한 번의 소모를 적고, 상한을 넘었으면 체인을 세운다. `_exec` 한 군데에서만 부른다."""
        wings, wasted = wing_spend(command, r)
        if not wings:
            return
        if wasted and self._stop.is_set():
            wasted = False   # 우리 ■ 정지가 끊은 것(canceled 등)은 헛돈 것이 아니다 — 총량에는 센다
        with self._wing_lock:
            self._wing_log.append((_now(), wings, wasted, command, it.get("name") or ""))
        if self._wing_tripped:
            return
        w = self.wing_window()
        cap, waste, cap_min, waste_min = self._wing_limits
        if w["wings10m"] > cap:
            self._wing_trip(it, "wing_cap", wing_cap_msg(cap, cap_min) + f" (최근 {cap_min}분 {w['wings10m']})")
        elif w["waste5m"] >= waste:
            self._wing_trip(it, "wing_waste", wing_waste_msg(waste, waste_min))

    def _wing_trip(self, it: dict, code: str, msg: str) -> None:
        """■ 정지와 같은 플래그로 체인을 세운다. 배너는 stopReason fatal:<code> + 같은 코드의 lastError (chain_stop)."""
        self._wing_tripped = code
        self.stop_reason = self.stop_reason or f"fatal:{code}"
        self._log(it, msg)
        self._event("stop", it.get("id"), "보드 정지 — " + msg)
        self.last_error = {"id": it.get("id"), "name": it.get("name") or "", "type": it.get("type") or "",
                           "error": code, "message": msg, "at": time.time()}
        self._stop.set()

    def _wing_gate(self, settings: dict):
        """▶ 시작 관문 — 창이 지나지 않았는데 다시 시작하면 곧바로 또 넘는 상태면 거절. 아니면 None.
        총량: 지금 소모 + 한 번(5) 이 상한을 넘으면. 헛소모: 이미 상한에 닿아 있으면."""
        self._wing_limits = wing_limits(settings)   # 관문도 지금 설정의 창으로 잰다
        cap, waste, cap_min, waste_min = self._wing_limits
        w = self.wing_window()
        with self._wing_lock:
            rows = list(self._wing_log)
        now = _now()
        if w["wings10m"] + WINGS_PER_CALL > cap:
            # 가장 오래된 기록부터 빠질 때 상한 아래로 내려가는 시각
            left, need = 0.0, w["wings10m"] + WINGS_PER_CALL - cap
            for t, n, *_ in rows:
                if t < now - cap_min * 60:
                    continue
                need -= n
                left = t + cap_min * 60 - now
                if need <= 0:
                    break
            return {"ok": False, "error": "wing_cap",
                    "message": wing_cap_msg(cap, cap_min) + f" (최근 {cap_min}분 {w['wings10m']}) — "
                               f"약 {max(1, math.ceil(left / 60))}분 뒤 다시 시작할 수 있습니다"}
        if w["waste5m"] >= waste:
            ts = [t for t, _n2, wasted, *_ in rows if wasted and t >= now - waste_min * 60]
            k = w["waste5m"] - waste   # 이만큼이 빠지면 상한 아래
            left = (ts[k] + waste_min * 60 - now) if k < len(ts) else 60
            return {"ok": False, "error": "wing_waste",
                    "message": wing_waste_msg(waste, waste_min) + f" — 약 {max(1, math.ceil(left / 60))}분 뒤 다시 시작할 수 있습니다"}
        return None

    # ── 항목 찾기 (그룹 안팎을 통틀어 id 가 유일하다) ──
    def _leaves(self) -> list:
        """단일 카드 전부 (루트 + 그룹 자식). 실행 순서대로."""
        out = []
        for x in self.items:
            if x.get("type") == "group":
                out += list(x.get("items") or [])
            else:
                out.append(x)
        return out

    def _runnable_leaves(self) -> list:
        """「▶ 시작」이 집어 들 단일 카드 — `_leaves` 에서 **돌지 않는 그룹의 pending 자식**을 뺀 것.
        치명으로 멈춘(error)·끝난(done) 그룹은 러너가 집어 들지 않으므로 그 안의 pending 은 할 일이 아니다
        (「대기로」가 그룹을 되돌린다). 그 안의 waiting(걸린 가공)은 `_collect_ready` 가 그룹과 무관하게 수령하니 남긴다."""
        out = []
        for x in self.items:
            if x.get("type") == "group":
                kids = list(x.get("items") or [])
                out += kids if x.get("status") not in ("error", "done") else [k for k in kids if k.get("status") == "waiting"]
            else:
                out.append(x)
        return out

    def _find(self, iid: str) -> dict | None:
        if not iid:
            return None
        for x in self.items:
            if x.get("id") == iid:
                return x
            if x.get("type") == "group":
                k = next((y for y in (x.get("items") or []) if y.get("id") == iid), None)
                if k is not None:
                    return k
        return None

    def _group_of(self, iid: str) -> dict | None:
        """그 카드를 품은 그룹 (루트 카드면 None)."""
        if not iid:
            return None
        for x in self.items:
            if x.get("type") == "group" and any(y.get("id") == iid for y in (x.get("items") or [])):
                return x
        return None

    def _container_of(self, iid: str) -> list | None:
        """그 카드가 들어 있는 리스트 (루트 self.items 또는 그룹의 items)."""
        if any(x.get("id") == iid for x in self.items):
            return self.items
        g = self._group_of(iid)
        return g["items"] if g else None

    def _group(self, gid: str) -> dict | None:
        return next((x for x in self.items if x.get("type") == "group" and x.get("id") == gid), None)

    def _reopen_if_work(self, g: dict | None, why: str) -> None:
        """끝난(done)·치명으로 멈춘(error) 그룹에 **새 일**(담기·옮겨 넣기·회차 늘리기)이 생기면 그룹을 대기로 연다.

        러너는 pending 그룹만 집어 들고(`_run`), `_runnable_leaves` 는 done·error 그룹의 자식을 할 일로 치지 않는다.
        그래서 끝난 그룹에 카드를 담으면 **완료 열에 갇혀 보이지도 돌지도 않았다**(「▶ 시작」 → empty).
        `reset` 이 자식을 되돌릴 때 그룹도 여는 것과 같은 규칙이다. loop 는 그대로 — 멈췄던 회차부터 잇는다.
        정지(stopped) 그룹은 건드리지 않는다: `column_of` 가 이미 대기로 보고 `start()` 가 되돌린다."""
        if g is None or g.get("type") != "group" or self._group_running(g):
            return
        if g.get("status") in ("done", "error") and group_has_left(g):
            g["status"] = "pending"; g.pop("error", None); g.pop("message", None)
            self._log(g, f"{why} — 그룹을 다시 대기로")

    def _group_running(self, g: dict | None) -> bool:
        """실행 중인 그룹 — 러너가 돌고 있고 그 그룹이 running 일 때만. 중간에 죽은 상태값으로 영영 잠기지 않게 self.running 을 같이 본다."""
        return bool(g) and bool(self.running) and g.get("status") == "running"

    @staticmethod
    def _busy(msg: str) -> dict:
        return {"ok": False, "error": "busy", "message": msg}

    @staticmethod
    def _grp_busy() -> dict:
        return {"ok": False, "error": "group_running",
                "message": "실행 중인 그룹의 구성은 바꿀 수 없습니다 (회차 늘리기만 가능). 정지한 뒤 다시 시도하세요."}

    def _event(self, kind: str, iid: str | None, msg: str) -> None:
        if iid and iid in self._removed:   # 사용자가 지운 항목의 결과는 조용히 버린다 (타임라인에도 남기지 않는다)
            return
        self.events.append({"t": time.time(), "id": iid, "kind": kind, "msg": msg})
        del self.events[:-EVENTS_KEEP]

    # ── 편집 ──
    def configure(self, patch: dict) -> dict:
        patch = patch or {}
        if "repeat" in patch:   # 큐 전체 반복은 없어졌다. 그룹의 회차로 옮겨 갔다
            return {"ok": False, "error": "gone",
                    "message": "큐 전체 반복은 없어졌습니다. 그룹을 만들어 그 그룹의 회차를 쓰세요."}
        with self.lock:
            self.config = self._coerce_config(patch, self.config)
            self._save()
            return {"ok": True, "onError": self.config["onError"]}

    def _mergeable(self, container: list, typ: str, name: str, rid: str, collect: str | None) -> dict | None:
        """같은 것을 또 담았을 때 합칠 대기 항목. 담기 버튼을 연타하면 항목이 20개씩 쌓였다.
        **pending 만** 합친다 — running·waiting 은 이미 실행됐거나 진행 중이고, done·error·stopped 는
        사용자가 결과를 보고 판단할 기록이라 조용히 수량이 바뀌면 안 된다.
        가공은 collect(등록만/완료까지 기다림)가 같을 때만 — 다르면 사용자가 고른 방식이 조용히 덮인다.
        **같은 컨테이너 안에서만** 본다 — 루트 항목과 그룹 안 항목을 합치면 그룹의 회차 계획이 조용히 바뀐다."""
        for it in container:
            if it.get("status") != "pending" or it.get("type") != typ:
                continue
            if typ == "gather":
                if it.get("name") == name:
                    return it
            elif rid and it.get("recipeId") == rid or (not rid and it.get("name") == name):
                if typ != "alter" or it.get("collect") == collect:
                    return it
        return None

    def _container_for_add(self, group: str | None):
        """담을 곳 (선택 필드 group). (컨테이너, 오류) — 오류가 있으면 컨테이너는 None."""
        gid = _s(group)
        if not gid or gid == "root":
            return self.items, None
        g = self._group(gid)
        if g is None:
            return None, {"ok": False, "error": "not_found", "message": "그 그룹이 없습니다."}
        if self._group_running(g):
            return None, self._grp_busy()
        return g["items"], None

    def add(self, item: dict, snap: dict, settings: dict | None = None, merge: bool = True,
            group: str | None = None) -> dict:
        """검증 후 추가. snap 은 server.snapshot() (캐시) — 레시피·채집 목록 확인과 보유량에 쓴다.
        수량 검증은 update 와 같은 함수(check_gather/check_count)를 쓴다 — add 에만 상한이 없으면
        게임에 전달되는 craftCount·확인창 수치가 검증 없이 커진다. add_chain 도 이 경로를 지난다.
        merge=True 면 같은 대기 항목에 수량을 합친다(연타 방지). add_chain 은 끄고 부른다 — 아래 참조.
        group 을 주면 그 그룹 안 끝에 담는다 (없으면 루트)."""
        settings = settings if isinstance(settings, dict) else store.get_settings()
        max_passes = _n(settings.get("queue_max_passes"), 50)
        typ, name = _s(item.get("type")), _s(item.get("name"))
        if typ == "collect":
            return self._add_collect(item, snap, group)
        if typ in FREE_CARD_TYPES:
            return self._add_free(item, group)
        if typ not in ("craft", "alter", "gather") or not name:
            return {"ok": False, "error": "bad_request", "message": "type 은 craft/alter/gather/collect/play/notify, name 은 필수입니다."}
        it: dict = {"id": uuid.uuid4().hex[:10], "type": typ, "name": name, "status": "pending", "log": []}
        # 합칠 대상 찾기부터 저장까지 한 잠금 안에서 — 중간에 러너가 항목을 집어가거나 다른 요청이 끼어들면
        # 검증한 합계와 실제로 저장되는 값이 어긋난다. lock 은 RLock 이라 아래 _save 와 겹쳐도 된다.
        with self.lock:
            container, err = self._container_for_add(group)
            if err:
                return err
            if typ == "gather":
                # 개수(target). 1.0.9 화면이 보내는 회수(runs)는 ×100 개로 읽는다 (target_from_request)
                target = _n(target_from_request(item))
                g = next((x for x in snap.get("gather", []) if x["name"] == name), None)
                if not g:
                    return {"ok": False, "error": "not_gatherable", "message": ERROR_KO["not_gatherable"]}
                have = _have(snap, name)
                prev = self._mergeable(container, typ, name, "", None) if merge else None
                total = _n(prev.get("target")) + target if prev else target
                # 이번에 더하는 양 **자체**와 합친 총량을 둘 다 본다. 증분을 안 보면 target=0 이 「합쳐서 0 더하기」로
                # 조용히 통과해 update(0) 이 거부하는 값을 add 는 받는다(판정 불일치).
                err, _ = check_gather(target, max_passes)
                if err:
                    return err
                err, plan = check_gather(total, max_passes)   # 합치고 나서 상한을 넘으면 안 된다
                if err:
                    return err
                plan = {**plan, "have": have}   # 가방 수는 화면 표시용 (「가방 230 → 목표 480」)
                if prev:
                    prev["target"] = total
                    p = prev.setdefault("progress", {})
                    p["have"], p["passesPlanned"] = have, plan["passesPlanned"]
                    prev["log"].append({"t": time.time(), "msg": f"합침: +{target}개 → {total}개 캐기 (예상 {plan['passesPlanned']}회)"})
                    self._save()
                    return {"ok": True, "item": dict(prev), "plan": plan, "merged": True, "added": target}
                # targetMigrated: 새 의미(캘 개수)로 만든 항목이라는 표시 — _load 의 옛 항목 보정이 건너뛴다
                it.update(target=target, targetMigrated=True,
                          progress={"done": 0, "have": have, "passes": 0, "totalDone": 0,
                                    "passesPlanned": plan["passesPlanned"], "per": MAX_PER_PASS})
                if not g.get("toolOk"):
                    it["log"].append({"t": time.time(), "msg": "주의: 지금 도구 상태가 ToolOk=false 입니다 — 시작 전에 다시 확인합니다"})
            else:
                pool = snap.get("craft" if typ == "craft" else "alter", [])
                rid = _s(item.get("recipeId"))
                r = (next((x for x in pool if x.get("id") == rid), None) if rid
                     else next((x for x in pool if x.get("name") == name), None))
                if not r:
                    return {"ok": False, "error": "not_in_cache", "message": ERROR_KO["not_in_cache"]}
                # **이름은 캐시의 것이다.** recipeId 로 찾았을 때 요청의 name 을 그대로 쓰면 검증(not_in_cache)을 지난
                # 뒤에 아무 글자나 카드 이름·displayName 이 된다 — 1MB 이름이 queue.json·장부·로그마다 실렸다.
                # 화면은 언제나 r.name 을 같이 보내므로 (drawer/plan/list-core) 바뀌는 것이 없다.
                name = _s(r.get("name")) or name
                it["name"] = name
                if item.get("count") == "max":   # 서버가 재고·시설 상한으로 치환 (날개 효율: 한 번에 최대로)
                    ms = max_suggested(r, snap)
                    if not ms["maxSuggested"]:
                        return {"ok": False, "error": "no_stock", "message": "지금 보유로는 한 번도 만들 수 없습니다 (가방+창고 기준)."
                                + (" 재료 정보가 없어 계산할 수 없습니다." if ms["maxByStockPartial"] else "")}
                    count = ms["maxSuggested"]
                    it["log"].append({"t": time.time(), "msg": f"최대로 담음: 재고 기준 {ms['maxByStock']}회"
                                      + (f", 시설 상한 {ms['maxCount']}회" if ms["maxCount"] else "") + f" → {count}회"})
                else:
                    count = _n(item.get("count"), 1)
                # 가공 수령 모드 — 기본은 「걸기만」(none). 등록만 하고 수령까지 기다리지 않는다
                collect = (item["collect"] if item.get("collect") in ALTER_MODES else ALTER_MODE_DEFAULT) if typ == "alter" else None
                prev = self._mergeable(container, typ, name, _s(r.get("id", rid)), collect) if merge else None
                total = _n(prev.get("count")) + count if prev else count
                err = check_count(count) or check_count(total)   # 증분 자체와 합친 총량을 둘 다 (위 채집과 같은 이유)
                if err:
                    return err
                if prev:
                    prev["count"] = total
                    p = prev.setdefault("progress", {})
                    if typ == "alter":
                        # 등록 total 회 + 수령 왕복(7건마다 한 번 — 「걸기만」은 칸을 비울 때만). 합칠 대상은 collect 가 같은 항목뿐이다
                        p["passesPlanned"] = alter_passes(total, prev.get("collect"))
                    else:
                        p["passesPlanned"] = craft_plan(prev, p)   # 시설 상한으로 나눈 호출 수 (N7)
                    prev["log"].append({"t": time.time(), "msg": f"합침: +{count}회 → {total}회"})
                    self._save()
                    plan = {"passesPlanned": p.get("passesPlanned", 1), "have": p.get("have", 0),
                            "need": total * _n(p.get("per"), 1)}
                    return {"ok": True, "item": dict(prev), "plan": plan, "merged": True, "added": count}
                it.update(recipeId=r.get("id", rid), count=count,
                          progress={"done": 0, "have": _have(snap, name), "passes": 0, "totalDone": 0,
                                    "passesPlanned": (craft_calls(count, limits_get(_s(r.get("id", rid))))
                                                      if typ == "craft" else alter_passes(count, collect)),
                                    "per": _n(r.get("per"), 1)})
                if typ == "alter":
                    it["collect"] = collect
                plan = {"passesPlanned": it["progress"]["passesPlanned"], "have": it["progress"]["have"],
                        "need": count * it["progress"]["per"]}
                if not r.get("ready"):
                    it["log"].append({"t": time.time(), "msg": f"주의: 지금은 만들 수 없음({r.get('reasonKo') or r.get('reason') or '재료 부족'}) — 실행 시 CLI 가 거부하면 그 항목은 오류로 두고 다음으로 갑니다"})
            container.append(it)
            self._reopen_if_work(self._group_of(it["id"]), f"「{name}」을 담음")
            self._save()
        return {"ok": True, "item": dict(it), "plan": plan, "merged": False}

    def _add_free(self, item: dict, group: str | None = None) -> dict:
        """「연주」·「알림」 카드. CLI 를 부르지 않고 날개도 0 이라 재고·레시피 검증이 없다 —
        대신 **모양**을 엄격히 본다 (밖(폰)에서 edit 범위로 담을 수 있는 카드라 아무 글자나 큐·장부에 실리면 안 된다).
        합치지 않는다 — 같은 곡을 두 번 담으면 두 번 튼다는 뜻이다."""
        typ = _s(item.get("type"))
        it: dict = {"id": uuid.uuid4().hex[:10], "type": typ, "status": "pending", "log": [],
                    "progress": {"done": 0, "have": 0, "passes": 0, "totalDone": 0, "passesPlanned": 0, "per": 1}}
        if typ == "play":
            song = item.get("song") if isinstance(item.get("song"), str) and item.get("song").strip() else None
            lst = item.get("list") if isinstance(item.get("list"), str) and item.get("list").strip() else None
            mode = (item.get("mode") if item.get("mode") in PLAY_MODES + (PLAY_MODE_LEGACY,)
                    else ("song" if song else PLAY_MODE_LEGACY if lst else "resume"))
            if mode == "song" and not song:
                return {"ok": False, "error": "bad_request", "message": "이 곡만 연주하려면 song(곡 key)이 필요합니다."}
            if mode == PLAY_MODE_LEGACY and not lst:
                return {"ok": False, "error": "bad_request", "message": "재생목록을 담으려면 list(재생목록 id)가 필요합니다."}
            if (song and len(song) > 200) or (lst and len(lst) > 80):
                return {"ok": False, "error": "bad_request", "message": "곡 key·재생목록 id 가 너무 깁니다."}
            count, err = self._play_count(item, mode)
            if err:
                return err
            if mode == PLAY_MODE_LEGACY:
                # 재생목록은 카드 하나가 아니라 **그룹**이다
                return self._add_playlist_group(item, lst, count, group)
            title = _s(item.get("title"))[:120]
            name = title or (song if mode == "song" else "") or PLAY_MODE_KO["resume"]
            it.update(name=name, mode=mode, song=song if mode == "song" else None, list=None, count=count)
            plan_msg = f"연주 담음: {PLAY_MODE_KO[mode]} · {name}" + (f" · {count}회" if mode != "resume" else "")
        else:
            text = item.get("text")
            if text is not None and not isinstance(text, str):
                return {"ok": False, "error": "bad_request", "message": "text 는 글자여야 합니다."}
            text = " ".join((text or "").split())
            if len(text) > NOTIFY_TEXT_MAX:
                return {"ok": False, "error": "bad_request", "message": f"알림 글은 {NOTIFY_TEXT_MAX}자까지입니다."}
            sound = item.get("sound")
            if sound is not None and not isinstance(sound, bool):
                return {"ok": False, "error": "bad_request", "message": "sound 는 true/false 여야 합니다."}
            text = text or NOTIFY_DEFAULT_TEXT
            it.update(name=text, text=text, sound=sound is not False)
            plan_msg = f"알림 담음: 「{text}」" + ("" if it["sound"] else " (소리 없음)")
        with self.lock:
            container, err = self._container_for_add(group)
            if err:
                return err
            it["log"].append({"t": time.time(), "msg": plan_msg})
            container.append(it)
            self._reopen_if_work(self._group_of(it["id"]), f"「{it['name']}」을 담음")
            self._save()
        return {"ok": True, "item": dict(it), "plan": {"passesPlanned": 0, "have": 0, "need": 0}, "merged": False}

    @staticmethod
    def _play_count(item: dict, mode: str):
        """연주 회차 (기본 1 = 한 번 재생 · 1~PLAY_COUNT_MAX) → (회차, 오류). resume 은 폴리오 제 설정대로라 회차가 없다 (1 로 고정)."""
        if mode == "resume" or "count" not in item or item.get("count") is None:
            return 1, None
        c = item.get("count")
        if isinstance(c, bool) or not isinstance(c, (int, float, str)) or _n(c) < 1 or _n(c) > PLAY_COUNT_MAX:
            return 0, {"ok": False, "error": "bad_request", "message": f"연주 회차는 1~{PLAY_COUNT_MAX} 사이여야 합니다."}
        return _n(c), None

    # 재생목록의 곡 목록을 읽는 자리 — server.py 가 폴리오 엔진(`_lists`)에 꽂는다. 검사는 가짜를 꽂는다.
    #   (list_id) -> {"name": str, "items": [{"key", "title"}, …]} | None(없는 목록)
    playlist_songs = None

    def _add_playlist_group(self, item: dict, lst: str, count: int, group: str | None) -> dict:
        """재생목록 → **그룹 카드**.

        그룹 이름 = 재생목록 이름 · 그룹 회차 = 입력한 회차 · 자식 = 곡마다 「연주 · 이 곡만 · 1회」 카드 하나, 재생목록 순서 그대로.
        그러면 나머지는 전부 **그룹의 규칙**이다 — 회차 반복, 곡마다 제 카드(진행·경과), 그룹 안에서 순서 바꾸기·빼기, 정지·오류 정책.
        곡은 요청의 `songs`([{key, title}], 폰·옛 프리셋이 들고 온 것)가 있으면 그것, 없으면 갈고리(`playlist_songs`)로 폴리오에서 읽는다.
        그룹은 그룹 안에 못 들어간다 (깊이 1단) — 담을 곳이 그룹이면 거절한다."""
        if _s(group) and _s(group) != "root":
            return {"ok": False, "error": "nested",
                    "message": "재생목록은 그룹으로 담깁니다 — 그룹 안에는 넣을 수 없습니다 (깊이 1단). 담을 곳을 「대기 열」로 하세요."}
        name = _s(item.get("listName"))[:GROUP_NAME_MAX] or _s(item.get("title"))[:GROUP_NAME_MAX]
        songs = item.get("songs") if isinstance(item.get("songs"), list) else None
        if songs is None:
            fn = self.playlist_songs
            if fn is None:
                return {"ok": False, "error": "folio_unavailable", "message": "재생목록을 읽을 데가 없습니다 (폴리오 미연결)."}
            try:
                got = fn(lst)
            except Exception as e:
                return {"ok": False, "error": "folio_unavailable", "message": f"재생목록을 읽지 못했습니다 ({type(e).__name__})."}
            if got is None:
                return {"ok": False, "error": "list_not_found", "message": "그 재생목록이 이제 없습니다."}
            if isinstance(got, dict):
                name = name or _s(got.get("name"))[:GROUP_NAME_MAX]
                songs = got.get("items") if isinstance(got.get("items"), list) else []
            else:
                songs = list(got) if isinstance(got, (list, tuple)) else []
        rows = []
        for x in songs:
            if not isinstance(x, dict):
                continue
            key = _s(x.get("key")) or _s(x.get("title"))
            if key and len(key) <= 200:
                rows.append({"key": key, "title": (_s(x.get("title")) or key)[:120]})
        if not rows:
            return {"ok": False, "error": "bad_request", "message": "재생목록이 비어 있습니다 — 곡을 넣은 뒤 담으세요."}
        with self.lock:
            g = self._new_group(name or lst, repeat=count)
            now = time.time()
            for x in rows:
                c = {"id": uuid.uuid4().hex[:10], "type": "play", "status": "pending", "name": x["title"], "mode": "song",
                     "song": x["key"], "list": None, "count": 1,
                     "progress": {"done": 0, "have": 0, "passes": 0, "totalDone": 0, "passesPlanned": 0, "per": 1},
                     "log": [{"t": now, "msg": f"연주 담음: 이 곡만 · {x['title']} · 1회 (재생목록 「{g['name']}」)"}]}
                g["items"].append(c)
            g["log"].append({"t": now, "msg": f"재생목록 「{g['name']}」 을 그룹으로 담음: {len(rows)}곡 · {count}회차"})
            self.items.append(g)
            self._save()
            return {"ok": True, "item": self._view(g), "plan": {"passesPlanned": 0, "have": 0, "need": 0}, "merged": False,
                    "group": True, "children": len(rows)}

    def _add_collect(self, item: dict, snap: dict, group: str | None = None) -> dict:
        """독립 「수령」 항목: 가공 대기열 카드의 시설별 「수령」 버튼이 담는다.
        CLI 의 complete_altering_work 는 displayName 으로 시설을 고르고 그 시설의 완료분을 전부 수령하므로,
        name 은 그 시설에서 **완료된** 작업의 이름이어야 한다 (진행 중인 이름을 주면 not_completed_yet)."""
        facility, name = _s(item.get("facility")), _s(item.get("name"))
        rows = [w for w in (snap.get("works") or []) if isinstance(w, dict)]
        if not facility and name:   # 이름만 왔으면 완료된 작업의 시설을 찾는다
            facility = next((w.get("facility", "") for w in rows if w.get("name") == name and w.get("done")), "")
        if not facility:
            return {"ok": False, "error": "bad_request", "message": "collect 는 facility(시설명)가 필요합니다."}
        done_rows = [w for w in rows if w.get("facility") == facility and w.get("done")]
        if not done_rows:
            return {"ok": False, "error": "no_completed_work", "message": ERROR_KO["no_completed_work"]}
        done_names = list(dict.fromkeys(w["name"] for w in done_rows))
        if name and name not in done_names:
            in_progress = any(w.get("facility") == facility and w.get("name") == name for w in rows)
            if in_progress:
                return {"ok": False, "error": "not_completed_yet",
                        "message": f"「{name}」 은 아직 진행 중입니다. 완료된 작업 이름으로 담으세요: " + ", ".join(done_names)}
            name = ""   # 그 시설에 없는 이름이면 완료된 첫 작업 이름으로 대신한다
        if not name:
            name = done_names[0]
        with self.lock:
            container, err = self._container_for_add(group)
            if err:
                return err
            # 중복 판정은 **같은 컨테이너 안에서만**. 그룹은 반복 단위라 실행 시점이 다르다 —
            # 루트의 수령과 그룹 안의 수령은 서로 다른 때에 도는 별개 작업이고, 「채집 → 가공 등록 → 수령」을 한 그룹으로
            # 묶어 반복하는 것이 이 재설계의 핵심 쓰임이다. 컨테이너가 다르면 허용한다.
            dup = next((x for x in container if x.get("type") == "collect" and x.get("facility") == facility
                        and x.get("status") in ("pending", "running")), None)
            if dup:
                return {"ok": False, "error": "duplicate", "message": ERROR_KO["duplicate"], "item": dict(dup)}
            it: dict = {"id": uuid.uuid4().hex[:10], "type": "collect", "name": name, "facility": facility,
                        "status": "pending", "log": [], "count": len(done_rows),
                        "progress": {"done": 0, "have": _have(snap, name), "passes": 0, "totalDone": 0,
                                     "passesPlanned": 1, "per": 1}}
            it["log"].append({"t": time.time(), "msg": f"담을 때 「{facility}」 완료 {len(done_rows)}건: " + ", ".join(done_names)})
            container.append(it)
            self._reopen_if_work(self._group_of(it["id"]), f"「{name}」을 담음")
            self._save()
        return {"ok": True, "item": dict(it), "plan": {"passesPlanned": 1, "have": it["progress"]["have"], "need": 0}}

    def add_chain(self, rid: str, count, mode: str, snap: dict, source_fn, dry_run: bool = False, max_depth: int = 4,
                  group: str | None = None, collect: str | None = None) -> dict:
        """선행 제작까지 담기: 목표 레시피 rid 를 count 회 만들기 위한 부족 재료를 재귀적으로 해결하는 항목들을
        **선행 → 목표 순서**로 큐에 담는다. 보유는 가방+창고 합산. source_fn(재료명) → ("gather"|"craft"|"alter"|None, 레시피키).
        - 채집: target = 부족분 (mode "max" 면 부족분을 100 단위로 올림)
        - 제작/가공: ⌈부족/1회 산출⌉ 회, 그 레시피의 재료도 같은 규칙으로 재귀 (깊이 최대 max_depth, 같은 경로 재방문 = 순환 → 중단·경고)
        - 같은 재료·레시피가 여러 갈래에서 필요하면 수량을 합쳐 한 항목. 공급 경로가 없으면 unresolved.
        dry_run 이면 담지 않고 계산만.
        collect(가공 수령 모드)는 **목표 레시피 항목에만** 실린다 — 사용자가 실제로 고른 것이 그것뿐이다.
        선행으로 딸려 들어가는 가공은 언제나 "none"(걸기만): 그 산출을 다음 항목이 써야 하므로 수령을 강제하면 안 되고,
        수령이 필요하면 「수령」 항목을 따로 담는 구조다. 생략하면 목표도 "none" 이다."""
        pool = {r["id"]: r for r in list(snap.get("craft", [])) + list(snap.get("alter", [])) if r.get("id")}
        target = pool.get(rid)
        if not target:
            return {"ok": False, "error": "not_in_cache", "message": ERROR_KO["not_in_cache"]}
        with self.lock:   # 담을 곳을 먼저 확인한다 — 절반만 담고 실패하면 사용자가 정리해야 한다
            _, cerr = self._container_for_add(group)
        if cerr and not dry_run:
            return cerr
        count = max(1, _n(count, 1))
        bag, storage = snap.get("stock", {}) or {}, snap.get("storage", {}) or {}
        gathers: dict[str, int] = {}
        crafts: dict[str, int] = {}
        order: list = []
        unresolved: dict[str, int] = {}
        warnings: list = []

        def emit(key):
            if key not in order:
                order.append(key)

        def visit(r: dict, n: int, depth: int, path: list) -> None:
            for req in work.requirements(r, n, bag, storage):
                short = req["short"]
                if short <= 0:
                    continue
                src, sid = source_fn(req["name"])
                if src == "gather":
                    gathers[req["name"]] = gathers.get(req["name"], 0) + short
                    emit(("gather", req["name"]))
                elif src in ("craft", "alter") and sid in pool:
                    if sid in path:
                        warnings.append("순환 감지: " + " → ".join(path + [sid]) + " — 여기서 중단")
                        unresolved[req["name"]] = unresolved.get(req["name"], 0) + short
                        continue
                    if depth >= max_depth:
                        warnings.append(f"깊이 {max_depth} 초과: 「{req['name']}」 은 담지 않음")
                        unresolved[req["name"]] = unresolved.get(req["name"], 0) + short
                        continue
                    sub = pool[sid]
                    k = math.ceil(short / max(1, _n(sub.get("per"), 1)))
                    visit(sub, k, depth + 1, path + [sid])
                    crafts[sid] = crafts.get(sid, 0) + k
                    emit(("recipe", sid))
                else:
                    unresolved[req["name"]] = unresolved.get(req["name"], 0) + short

        visit(target, count, 0, [rid])
        specs = []
        for kind, key in order:
            if kind == "gather":
                short = gathers[key]
                if mode == "max":
                    short = max(MAX_PER_PASS, math.ceil(short / MAX_PER_PASS) * MAX_PER_PASS)
                specs.append({"type": "gather", "name": key, "target": short})   # target = 캘 개수 = 부족분 그대로
            else:
                r = pool[key]
                # 선행 가공은 「걸기만」 — 수령 여부는 사용자가 고른 목표 항목에서만 뜻이 있다
                specs.append({"type": r["kind"], "name": r["name"], "recipeId": key, "count": crafts[key],
                              **({"collect": "none"} if r["kind"] == "alter" else {})})
        specs.append({"type": target["kind"], "name": target["name"], "recipeId": rid, "count": count,
                      **({"collect": collect if collect in ALTER_MODES else ALTER_MODE_DEFAULT}
                         if target["kind"] == "alter" else {})})
        added, calls, wing_calls = [], 0, 0
        for spec in specs:
            if spec["type"] == "gather":
                n = gather_plan(spec["target"])["passesPlanned"]
                calls += n; wing_calls += n
            elif spec["type"] == "alter":
                # 날개를 쓰는 것은 등록뿐이다 — 수령(complete_altering_work)은 호출이지만 소모가 없다
                wing_calls += spec["count"]
                calls += spec["count"] + alter_trips(spec["count"], spec.get("collect"))   # 등록 + 수령 왕복 (alter_calls 와 같은 셈)
            else:
                n = craft_calls(spec["count"], limits_get(spec.get("recipeId", "")))   # 시설 상한으로 나눈 호출 (N7)
                calls += n; wing_calls += n
            if dry_run:
                added.append(spec)
                continue
            # merge=False: 체인은 이미 안에서 같은 재료·레시피를 합쳐 놓았다(gathers·crafts). 밖에 있던 항목과
            # 또 합치면 이 체인이 세운 계획이 흐트러지므로 별도 항목으로 담는다. target 은 「캘 개수」라
            # 체인을 두 번 돌리면 두 몫이 각각 남는데, 그게 맞다 — 두 레시피가 각자 필요로 하는 양이다.
            res = self.add(spec, snap, merge=False, group=group)
            if res.get("ok"):
                added.append(res["item"])
            else:
                warnings.append(f"「{spec['name']}」 담기 실패: {res.get('error')} {res.get('message', '')}".rstrip())
        return {"ok": True, "added": added, "unresolved": [{"name": n, "short": s} for n, s in unresolved.items()],
                "warnings": warnings, "calls": calls, "wingCalls": wing_calls,
                "wings": wing_calls * WINGS_PER_CALL, "dryRun": dry_run}

    def update(self, iid: str, patch: dict, settings: dict, snap: dict | None = None) -> dict:
        """큐 안에서 횟수·목표·수령 방식 수정.
        pending·stopped·error 항목만. craft/alter: count(1~999), gather: target(1~99,999 → passesPlanned 재계산,
        queue_max_passes 초과면 거부), alter: collect. collect 타입은 수정 불가. 러너 실행 중에도 pending 항목은 허용."""
        max_passes = _n(settings.get("queue_max_passes"), 50)
        with self.lock:
            it = self._find(iid)
            if not it:
                return {"ok": False, "error": "not_found"}
            if it.get("status") not in ("pending", "stopped", "error") or it.get("type") == "collect":
                return {"ok": False, "error": "not_editable",
                        "message": "실행 중·가공 대기·완료 항목과 수령 항목은 수정할 수 없습니다."}
            if it.get("type") == "notify" or (it.get("type") == "play" and it.get("mode") != "song"):
                # 알림·「지금 대기열 이어서」는 수량이 없다 — 빼고 다시 담는다
                return {"ok": False, "error": "not_editable", "message": "알림·「지금 대기열 이어서」 카드는 수량이 없습니다. 빼고 다시 담으세요."}
            p = it.setdefault("progress", {})
            changed = []
            if it["type"] == "play":               # 연주 회차 (song) — 1~PLAY_COUNT_MAX
                if "count" in patch:
                    c = patch.get("count")
                    if isinstance(c, bool) or _n(c) < 1 or _n(c) > PLAY_COUNT_MAX:
                        return {"ok": False, "error": "bad_request", "message": f"연주 회차는 1~{PLAY_COUNT_MAX} 사이여야 합니다."}
                    it["count"] = _n(c)
                    changed.append(f"연주 {it['count']}회")
            elif it["type"] == "gather":
                target = target_from_request(patch)   # target(개수) · 1.0.9 화면의 count(회수 → ×100)
                if target is not None:
                    err, plan = check_gather(target, max_passes)   # add 와 같은 검증
                    if err:
                        return err
                    it["target"] = target; p["passesPlanned"] = plan["passesPlanned"]
                    changed.append(f"{target}개 캐기 (예상 {plan['passesPlanned']}회)")
            else:
                if patch.get("count") == "max":   # 재고·시설 상한으로 치환 (스냅샷이 있어야 계산할 수 있다)
                    pool = (snap or {}).get(it["type"], [])
                    r = next((x for x in pool if x.get("id") == it.get("recipeId")), None)
                    if not r:
                        return {"ok": False, "error": "not_in_cache", "message": "최대 계산에 필요한 레시피가 캐시에 없습니다. 갱신해 보세요."}
                    ms = max_suggested(r, snap)
                    if not ms["maxSuggested"]:
                        return {"ok": False, "error": "no_stock", "message": "지금 보유로는 한 번도 만들 수 없습니다 (가방+창고 기준)."}
                    patch = {**patch, "count": ms["maxSuggested"]}
                    changed.append(f"최대(재고 {ms['maxByStock']}" + (f"·상한 {ms['maxCount']}" if ms["maxCount"] else "") + ")")
                if "count" in patch:
                    count = _n(patch.get("count"))
                    err = check_count(count)   # add 와 같은 검증
                    if err:
                        return err
                    it["count"] = count
                    changed.append(f"횟수 {count}회")
                if it["type"] == "alter" and patch.get("collect") in ALTER_MODES:
                    it["collect"] = patch["collect"]
                    changed.append({"wait": "완료까지 기다림", "later": "등록 후 다음으로",
                                    "none": "걸기만 (수령 안 함)"}[it["collect"]])
                if it["type"] == "alter":
                    # 횟수·모드 둘 중 무엇이 바뀌었든 마지막에 한 번 다시 센다 — 모드에 따라 수령 왕복이 붙고 빠진다
                    p["passesPlanned"] = alter_passes(_n(it.get("count"), 1), it.get("collect"))
                if it["type"] == "craft":
                    p["passesPlanned"] = craft_plan(it, p)   # 시설 상한으로 나눈 호출 수 (N7)
            if changed:
                self._log(it, "수정: " + ", ".join(changed))
                self._save()
            # 채집 need = 캘 개수 그대로 (보유를 빼지 않는다 — target 이 이미 「이번에 캘 개수」다)
            need = (_n(it.get("target")) if it["type"] == "gather"
                    else _n(it.get("count")) * _n(p.get("per"), 1))
            return {"ok": True, "item": dict(it),
                    "plan": {"passesPlanned": _n(p.get("passesPlanned")), "have": _n(p.get("have")), "need": need}}

    def _forget(self, cards: list) -> None:
        """지운 카드들을 러너에게 알린다 (잠금 안에서 부른다). 러너는 이 id 의 결과를 조용히 버리고,
        지워진 그룹이면 남은 회차를 건너뛴다. current 커서가 없는 항목을 가리킨 채 남지 않게 같이 정리한다."""
        for c in cards:
            self._removed.add(c["id"])
            if self.current == c["id"]:
                self.current = None
            if self.current_group == c["id"]:
                self.current_group = None

    @staticmethod
    def _cards_of(it: dict) -> list:
        """그 항목과 (그룹이면) 그 안의 자식 전부."""
        if it.get("type") != "group":
            return [it]
        return [it] + [k for k in (it.get("items") or []) if isinstance(k, dict)]

    @staticmethod
    def _altering_left(cards: list) -> dict:
        """지운 것 중에 **게임에 이미 등록·결제된** 가공(waiting)이 있으면 그 사실을 고지한다.
        카드를 지워도 게임에 걸린 가공은 취소되지 않는다 — 사용자가 가공 탭에서 직접 수령해야 한다."""
        left = [c for c in cards if c.get("status") == "waiting"]
        if not left:
            return {}
        names = ", ".join(dict.fromkeys(_s(c.get("name")) for c in left))
        return {"note": "altering_left",
                "message": f"게임에 걸린 가공 {len(left)}건({names})은 그대로입니다 — 가공 탭에서 수령하세요."}

    def remove(self, iid: str) -> dict:
        """**어떤 상태에서도 지운다** — 중간에 멈춘 큐도 상태와 상관없이 지울 수 있어야 한다.
        실행 중인 항목·실행 중인 그룹·그 안의 자식도 막지 않는다.

        실행 명령(execute_*)은 이미 CLI 로 나갔고 최대 11분까지 블로킹이라 **되돌릴 수 없다.** 그래서 지우기는
        정지가 아니다 — stop_action 을 보내지 않고 러너는 계속 돈다. 러너가 결과를 기록하려 할 때
        그 항목이 없으면 조용히 버린다(_removed). waiting 인 가공은 게임에 그대로 걸려 있으므로 note 로 고지한다."""
        with self.lock:
            it = self._find(iid)
            if it is None:
                return {"ok": False, "error": "not_found"}
            cards = self._cards_of(it)
            container = self._container_of(iid)
            container[:] = [x for x in container if x.get("id") != iid]
            self._forget(cards)
            self._save()
            return {"ok": True, "state": self.state(), **self._altering_left(cards)}

    def clear(self) -> dict:
        """비우기도 실행 중에 된다 (remove 와 같은 취지)."""
        with self.lock:
            cards = [c for x in self.items for c in self._cards_of(x)]
            self.items = []
            self._forget(cards)
            self._save()
            return {"ok": True, "state": self.state(), **self._altering_left(cards)}

    def reset(self, iid: str) -> dict:
        """실패·정지한 카드를 대기로 되돌린다. 그룹이면 그룹과 그 안의 실패·정지 자식을 함께 되돌린다."""
        with self.lock:
            it = self._find(iid)
            if not it:
                return {"ok": False, "error": "not_found"}
            if it.get("type") == "group":
                if self._group_running(it):
                    return self._grp_busy()
                it["status"] = "pending"; it["loop"] = 0
                it.pop("error", None); it.pop("message", None)
                for k in (it.get("items") or []):
                    if k.get("status") in ("error", "stopped"):
                        k["status"] = "pending"; k.pop("error", None); k.pop("message", None)
                        k.pop("streak", None); k.pop("streakError", None)   # 원인을 고쳤다고 보고 다시 센다
                        self._log(k, "다시 대기로")
                self._save()
                return {"ok": True, "item": self._view(it), "state": self.state()}
            if it.get("status") in ("error", "stopped"):
                it["status"] = "pending"; it.pop("error", None); it.pop("message", None)
                it.pop("streak", None); it.pop("streakError", None)
                self._log(it, "다시 대기로")
                # 끝난·정지한·치명으로 멈춘 그룹의 자식을 되돌렸으면 **그룹도 대기로** — 안 그러면 자식은 대기 열에
                # 서 있는데 러너는 그 그룹(done/error)을 집어 들지 않아 영영 안 돈다. loop 는 그대로 두므로
                # 「▶ 시작」이 멈췄던 회차부터 잇는다. 도는 그룹은 건드리지 않는다 (러너가 같은 회차에서 집어 든다).
                g = self._group_of(iid)
                if g is not None and not self._group_running(g) and g.get("status") in ("done", "stopped", "error"):
                    g["status"] = "pending"; g.pop("error", None); g.pop("message", None)
                    self._log(g, f"자식 「{it.get('name')}」을 대기로 되돌려 그룹도 대기로 — 멈췄던 회차부터 잇습니다")
                self._save()
            return {"ok": True, "item": dict(it), "state": self.state()}

    # ── 프리셋 ──
    def preset_capture(self, name: str) -> dict:
        """지금 보드를 프리셋으로. **진행 상태는 담지 않는다** — 「무엇을 할지」만이다."""
        with self.lock:
            return presets.capture(self.items, name)

    def preset_apply(self, spec: dict, snap: dict, settings: dict | None = None,
                     mode: str = "append") -> dict:
        """프리셋을 보드에 푼다. mode: "append"(뒤에 덧붙임) | "replace"(비우고 넣음).

        **들어오는 길은 `add`·`group_create` 하나뿐이다.** 카드를 직접 만들어 꽂으면
        재료·레시피·수량 검증을 건너뛰게 되고, 남이 준 코드에는 그게 그대로 구멍이 된다.
        그래서 담기지 않은 것은 **버리지 않고 이유와 함께 돌려준다** — 받은 사람이
        「왜 3장이 비었는지」를 알아야 한다 (레시피를 아직 관찰하지 못했을 뿐일 수도 있다).

        `merge=False` 로 담는다: 프리셋은 **모양 그대로**여야 한다. 합치면 이미 보드에
        있던 카드에 수량이 붙어 프리셋과 다른 것이 된다.
        """
        if self.running:
            return {"ok": False, "error": "running", "message": "실행 중에는 프리셋을 불러올 수 없습니다. 정지 후 다시 시도하세요."}
        rows = (spec or {}).get("i") or []
        added, skipped = 0, []

        def _put(card: dict, gid: str | None):
            nonlocal added
            r = self.add(presets.to_item(card), snap, settings, merge=False, group=gid)
            if r.get("ok"):
                added += 1
            else:
                skipped.append({"name": card.get("n", ""), "type": presets.T_IN.get(card.get("t"), ""),
                                "error": r.get("error", ""), "message": r.get("message", "")})

        with self.lock:
            if mode == "replace":
                self.clear()
            for x in rows:
                if x.get("t") != "G":
                    _put(x, None)
                    continue
                g = self.group_create(x.get("n") or "그룹")
                if not g.get("ok"):
                    skipped.append({"name": x.get("n", ""), "type": "group",
                                    "error": g.get("error", ""), "message": g.get("message", "")})
                    continue
                gid = g["id"]
                self.group_update(gid, {"repeat": x.get("r", 1), "onError": x.get("e", "continue"),
                                        "retryFailed": bool(x.get("f", True)), "waitAlter": bool(x.get("w"))})
                for k in (x.get("i") or []):
                    _put(k, gid)
                # 한 장도 못 담은 빈 그룹은 남겨 두지 않는다 — 화면에 뜻 없는 껍데기가 된다
                if not (self._group(gid) or {}).get("items"):
                    self.remove(gid)
            self._save()
        return {"ok": True, "added": added, "skipped": skipped, "name": (spec or {}).get("n", ""),
                "state": self.state()}

    # ── 그룹 ──
    def group_create(self, name: str = "", ids: list | None = None) -> dict:
        """그룹을 만들고 ids 의 카드들을 순서 그대로 안으로 옮긴다. 그룹은 ids[0] 이 있던 자리에, 없으면 맨 끝에."""
        ids = [i for i in (ids or []) if isinstance(i, str)]
        with self.lock:
            if self.running and ids:
                return self._busy("실행 중에는 카드를 묶을 수 없습니다. 먼저 정지하세요.")
            picked = []
            for i in ids:
                it = self._find(i)
                if it is None:
                    return {"ok": False, "error": "not_found", "message": f"카드 {i} 를 찾지 못했습니다."}
                if it.get("type") == "group":
                    return {"ok": False, "error": "nested", "message": "그룹은 그룹 안에 넣을 수 없습니다 (깊이 1단)."}
                if self._group_running(self._group_of(i)):
                    return self._grp_busy()
                picked.append(it)
            at = next((n for n, x in enumerate(self.items) if ids and x.get("id") == ids[0]), len(self.items))
            for it in picked:   # 원래 자리에서 뺀다 (루트에서 빠지면 at 이 밀리므로 다시 계산)
                c = self._container_of(it["id"])
                if c is not None:
                    c[:] = [x for x in c if x.get("id") != it["id"]]
            at = min(at, len(self.items))
            g = self._new_group(name)
            g["items"] = picked
            self.items.insert(at, g)
            self._save()
            return {"ok": True, "id": g["id"], "item": self._view(g), "state": self.state()}

    def group_update(self, gid: str, patch: dict) -> dict:
        """name·repeat·onError·retryFailed·waitAlter. 실행 중인 그룹은 **회차 늘리기만** 허용 (디자인 2c)."""
        patch = patch if isinstance(patch, dict) else {}
        with self.lock:
            g = self._group(gid)
            if g is None:
                return {"ok": False, "error": "not_found", "message": "그 그룹이 없습니다."}
            keys = [k for k in ("name", "repeat", "onError", "retryFailed", "waitAlter") if k in patch]
            if not keys:
                return {"ok": False, "error": "bad_request", "message": "바꿀 항목이 없습니다."}
            if self._group_running(g):
                new_rep = min(REPEAT_MAX, max(1, _n(patch.get("repeat"), g["repeat"]))) if "repeat" in patch else g["repeat"]
                if keys != ["repeat"] or new_rep < g["repeat"]:
                    return self._grp_busy()
            changed = []
            if "name" in patch:
                nm = _s(patch.get("name"))[:GROUP_NAME_MAX]
                if nm:
                    g["name"] = nm; changed.append(f"이름 「{nm}」")
            if "repeat" in patch:
                g["repeat"] = min(REPEAT_MAX, max(1, _n(patch.get("repeat"), g["repeat"])))
                changed.append(f"{g['repeat']}회차")
            if patch.get("onError") in ("continue", "stop"):
                g["onError"] = patch["onError"]
                changed.append("오류 시 " + ("정지" if g["onError"] == "stop" else "계속"))
            if "retryFailed" in patch:
                g["retryFailed"] = patch.get("retryFailed") is not False
                changed.append("실패 재시도 " + ("켬" if g["retryFailed"] else "끔"))
            if "waitAlter" in patch:
                g["waitAlter"] = patch.get("waitAlter") is True
                changed.append("가공 완료까지 대기 " + ("켬" if g["waitAlter"] else "끔"))
            if changed:
                self._log(g, "수정: " + ", ".join(changed))
                if "repeat" in patch:
                    self._reopen_if_work(g, "회차를 늘림")
                self._save()
            return {"ok": True, "item": self._view(g), "state": self.state()}

    def group_dissolve(self, gid: str) -> dict:
        """자식들을 그룹이 있던 자리에 순서 그대로 펼치고 그룹을 지운다."""
        with self.lock:
            g = self._group(gid)
            if g is None:
                return {"ok": False, "error": "not_found", "message": "그 그룹이 없습니다."}
            if self._group_running(g):
                return self._grp_busy()
            if self.running:
                return self._busy("실행 중에는 그룹을 해체할 수 없습니다. 먼저 정지하세요.")
            at = next(n for n, x in enumerate(self.items) if x.get("id") == gid)
            self.items[at:at + 1] = list(g.get("items") or [])
            self._save()
            return {"ok": True, "state": self.state()}

    def group_duplicate(self, gid: str) -> dict:
        """새 id 로 복제해 바로 뒤에 넣는다. 상태·진행·로그는 전부 초기화."""
        with self.lock:
            g = self._group(gid)
            if g is None:
                return {"ok": False, "error": "not_found", "message": "그 그룹이 없습니다."}
            at = next(n for n, x in enumerate(self.items) if x.get("id") == gid)
            base = g["name"]
            suffix = " (복사)"
            name = (base if len(base) + len(suffix) <= GROUP_NAME_MAX else base[:GROUP_NAME_MAX - len(suffix)]) + suffix
            new = self._new_group(name, repeat=g["repeat"])
            new.update(onError=g["onError"], retryFailed=g["retryFailed"], waitAlter=g["waitAlter"])
            new["items"] = [self._fresh_copy(k) for k in (g.get("items") or [])]
            self.items.insert(at + 1, new)
            self._save()
            return {"ok": True, "id": new["id"], "item": self._view(new), "state": self.state()}

    @staticmethod
    def _readd_request(it: dict) -> dict:
        """낱장 카드 → 그 카드를 처음 담을 때 보냈을 `add` 의 item. 사용자가 고른 것만 옮긴다
        (이름·레시피·목표/횟수·수령 방식·연주 방식/회차·알림 글/소리). 진행·상태·오류·기록은 싣지 않는다."""
        typ = _s(it.get("type"))
        if typ == "gather":
            return {"type": typ, "name": it.get("name"), "target": it.get("target")}
        if typ in ("craft", "alter"):
            req = {"type": typ, "name": it.get("name"), "recipeId": it.get("recipeId"), "count": it.get("count")}
            if typ == "alter":
                req["collect"] = it.get("collect")
            return req
        if typ == "collect":
            # 시설만 준다 — 수령은 그 시설의 완료분을 전부 받는 일이라 이름은 「지금 완료된 첫 작업」으로 다시 정해진다.
            # 옛 이름을 넘기면 같은 이름이 다시 진행 중일 때 not_completed_yet 로 괜히 막힌다.
            return {"type": typ, "facility": it.get("facility")}
        if typ == "play":
            mode = it.get("mode")
            req = {"type": typ, "mode": mode, "song": it.get("song"), "list": it.get("list"), "title": it.get("name")}
            if mode != "resume":
                req["count"] = it.get("count")
            if mode == PLAY_MODE_LEGACY:
                req["listName"] = it.get("name")
            return req
        if typ == "notify":
            return {"type": typ, "text": it.get("text"), "sound": it.get("sound") is not False}
        return {"type": typ}

    def card_duplicate(self, iid: str, snap: dict, settings: dict | None = None) -> dict:
        """낱장 카드 「다시 담기」 — 같은 설정으로 **처음 담는 것과 똑같이** `add` 를 다시 부른다.
        그래서 검증(캐시·수량 상한·수령 중복·재생목록→그룹)이 담기와 한 벌이고, 새 카드는 pending · 진행 0 · 기록 새것이다.
        합치지 않는다(merge=False) — 누른 만큼 카드가 하나 는다. 담는 곳은 원래 카드가 있던 컨테이너의 **끝**
        (루트면 대기 열 끝, 그룹 자식이면 그 그룹 끝). 원래 카드는 그대로 둔다(기록). 그룹이면 group_duplicate 로 넘긴다."""
        with self.lock:
            it = self._find(_s(iid))
            if it is None:
                return {"ok": False, "error": "not_found", "message": "그 카드가 없습니다."}
            if it.get("type") == "group":
                return self.group_duplicate(it["id"])
            if it.get("type") not in CARD_TYPES:
                return {"ok": False, "error": "bad_request", "message": "다시 담을 수 없는 카드입니다."}
            g = self._group_of(it["id"])
            req, gid, src = self._readd_request(it), (g["id"] if g else None), it["id"]
        # add 는 제 잠금을 따로 잡는다 — 재생목록 펼치기가 폴리오를 읽는 동안 큐 잠금을 쥐고 있지 않게 밖에서 부른다
        r = self.add(req, snap, settings, merge=False, group=gid)
        if r.get("ok"):
            r["source"] = src
        return r

    @staticmethod
    def _fresh_copy(it: dict) -> dict:
        """복제용 자식 한 장 — 무엇을 할지(수량·레시피)만 남기고 진행·기록은 전부 새것으로."""
        out = {k: v for k, v in it.items() if k not in ("id", "status", "progress", "log", "error", "message", "precheck", "precheck_bf",
                                                   "precheck_combat_warn", "regStop")}
        p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
        out.update(id=uuid.uuid4().hex[:10], status="pending", log=[],
                   progress={"done": 0, "have": _n(p.get("have")), "passes": 0, "totalDone": 0,
                             "passesPlanned": _n(p.get("passesPlanned"), 1), "per": _n(p.get("per"), 1)})
        return out

    def move_item(self, iid: str, to: str | None = None, index=None) -> dict:
        """카드를 루트↔그룹 사이로 옮기고 위치를 정한다. index 생략 시 끝."""
        with self.lock:
            it = self._find(iid)
            if it is None:
                return {"ok": False, "error": "not_found", "message": "그 카드가 없습니다."}
            src_group = self._group_of(iid)
            if self._group_running(src_group):
                return self._grp_busy()
            dest_group = None
            if to is None or _s(to) == "":
                dest = self._container_of(iid); dest_group = src_group
            elif _s(to) == "root":
                dest = self.items
            else:
                dest_group = self._group(_s(to))
                if dest_group is None:
                    return {"ok": False, "error": "not_found", "message": "그 그룹이 없습니다."}
                if it.get("type") == "group":
                    return {"ok": False, "error": "nested", "message": "그룹은 그룹 안에 넣을 수 없습니다 (깊이 1단)."}
                if self._group_running(dest_group):
                    return self._grp_busy()
                dest = dest_group["items"]
            # 루트 순서를 바꾸는 일은 러너가 도는 동안 거부한다 (busy)
            if self.running:
                return self._busy("실행 중에는 순서를 바꿀 수 없습니다. 먼저 정지하세요.")
            src = self._container_of(iid)
            src[:] = [x for x in src if x.get("id") != iid]
            at = len(dest) if index is None else max(0, min(len(dest), _n(index)))
            dest.insert(at, it)
            if dest_group is not None and dest_group is not src_group:
                self._reopen_if_work(dest_group, f"「{it.get('name')}」을 옮겨 넣음")
            self._save()
            return {"ok": True, "state": self.state()}

    # ── 미리보기 ──
    def _preview_card(self, it: dict, max_passes: int) -> dict | None:
        """카드 한 장의 회차당 호출 수. pending·waiting 만 센다 (이미 끝난 것은 다시 부르지 않는다)."""
        if it.get("status") not in ("pending", "waiting"):
            return None
        p = it.get("progress", {})
        acts = []
        calls = wings = 0
        if it["type"] == "gather":
            # **없으면 목표로 센다.** 예전엔 `passesPlanned` 가 없으면 그냥 0 이었고, 그래서
            # **채집만 있는 그룹의 요약이 「0회 · 0」** 으로 나왔다.
            # 화면은 이미 `p.passesPlanned || gpass(target)` 로 메우고 있었다 —
            # **같은 값을 두 곳이 다르게 세면 반드시 어긋난다.** 규칙을 서버에 맞춘다.
            n = _n(p.get("passesPlanned")) or gather_plan(_n(it.get("target")))["passesPlanned"]
            acts += [{"command": "get_items", "text": "시작 전: 가방 수 읽기 (가방이 시작 + 개수에 닿으면 끝)"},
                     {"command": "get_gatherable_items", "text": "매 회 전: 도구 상태 확인"},
                     {"command": "get_inventory", "text": "매 회 전: 무게 확인"},
                     {"command": "execute_gathering",
                      "text": f"「{it['name']}」 {_n(it.get('target')):,}개 캐기 (1회 최대 {MAX_PER_PASS}개 · 날개 {WINGS_PER_CALL}개) × 예상 {n}회"},
                     {"command": "get_items", "text": "마지막 회: 3초마다 가방 수 읽기 → 목표에 닿으면 stop_action"}]
            calls = wings = n
        elif it["type"] in FREE_CARD_TYPES:
            # 연주·알림 — CLI 호출 0 · 날개 0. 확인창은 날개 칸에 「—」 를 적는다
            if it["type"] == "play":
                times = f" · {_n(it.get('count'), 1)}회 치고 멈춤" if it.get("mode") == "song" else ""
                # 실행 명령은 없다 — 상태 점검(get_activity 읽기 1회, 날개 0)만 작업 카드와 같이 앞에 선다 (탈것·사망이면 부탁하지 않는다)
                acts += [{"command": "play", "text": f"상태 점검(탈것·사망·대화·던전·연출·연주 — 읽기 1회) → 폴리오에 「{it['name']}」 연주를 부탁 "
                                                     f"({PLAY_MODE_KO.get(it.get('mode'), '')}{times}) — 끝날 때까지 이 카드가 돈다 (다음 카드는 그 뒤)"}]
            else:
                acts += [{"command": "notify", "text": f"알림 「{_s(it.get('text'))}」 — PC 밴드·앱 창·모바일" + ("" if it.get("sound") is False else " (소리)")}]
            calls = wings = 0
        elif it["type"] == "collect":
            acts += [{"command": "complete_altering_work",
                      "text": f"「{it.get('facility', '')}」 완료 {it.get('count', 0)}건 수령 (그 시설의 완료분 전부, 시설까지 이동 포함)"},
                     {"command": "get_altering_works", "text": "수령 뒤 대기열 다시 읽기"}]
            calls = 1          # 수령은 날개를 쓰지 않는다
        elif it["type"] == "craft":
            left = max(0, _n(it.get("count"), 1) - _n(p.get("done")))
            mc = limits_get(it.get("recipeId", "")) if it.get("recipeId") else None
            n = craft_calls(left, mc)
            split = (f" — 시설 상한 {mc}회씩 {n}번 호출 (호출마다 날개 {WINGS_PER_CALL}개)" if n > 1
                     else " — 상한을 넘으면 거절 답(maxCount)을 보고 그 자리에서 나눠 잇습니다" if not mc else "")
            acts += [{"command": "execute_crafting", "text": f"「{it['name']}」 {left}회 제작 (산출 {left * _n(p.get('per'), 1)}개), 시설까지 이동 포함{split}"}]
            calls = wings = n   # 나눠 부르면 그 수만큼 날개가 든다 (N7). 상한을 모르면 한 번으로 센다
        else:
            mode = alter_mode(it.get("collect"))
            calls, wings = alter_calls(it)   # 남은 등록(날개) + 수령 왕복(날개 0) — 화면과 같은 규칙 (N6)
            if wings:
                acts += [{"command": "get_altering_works", "text": "등록마다 먼저 그 시설의 칸 수를 읽는다 (읽기 · 날개 0) — 칸이 찼으면 부르지 않는다"},
                         {"command": "execute_altering", "text": f"「{it['name']}」 가공 {wings}건 등록 (건마다 1회 호출 · 빈 칸만큼), 시설까지 이동 포함 — "
                          + {"wait": "칸이 차면 그 자리에서 완료를 기다렸다 받고 이어서 건다",
                             "later": "칸이 차면 다음 항목으로 넘어가고, 완료가 모이면 받고 이어서 건다",
                             "none": "걸기만 — 칸이 찰 때만 받아 비우고, 다 걸면 끝낸다"}[mode]}]
            if calls > wings:
                acts.append({"command": "complete_altering_work",
                             "text": f"완료가 모이면(설정 「n개 이상 모이면 받으러 가기」) 「{it['name']}」 시설에서 수령 — 약 {calls - wings}번 (날개 소모 없음)"})
        return {"id": it["id"], "type": it["type"], "name": it["name"], "status": it["status"],
                "passesPlanned": _n(p.get("passesPlanned")), "actions": acts,
                "calls": calls, "wingCalls": wings, "detail": card_text(it),
                "maxCount": limits_get(it.get("recipeId", "")) if it["type"] == "craft" else None}

    @staticmethod
    def _repeat_of(g: dict) -> int:
        return min(REPEAT_MAX, max(1, _n(g.get("repeat"), 1)))

    @staticmethod
    def loop_left(g: dict, running: bool) -> int:
        """남은 회차 = **러너가 실제로 돌 회차.**

        `loop ≥ 1` 이면 `repeat - loop + 1` — 도는 중이든, 정지했다가 「▶ 시작」으로 이어 가든
        러너는 `loop` 회차부터 잇는다(`_run_group` 의 first). 전에는 정지한 그룹을 `repeat` 로
        세어 **확인창·그룹 창·보드 요약이 날개를 부풀렸다**. `loop == 0`(새 그룹 · 「대기로」)
        이면 `repeat`. 끝난(done)·치명으로 멈춘(error) 그룹은 「대기로」 전에는 돌지 않으니 0 이다."""
        if g.get("status") in ("done", "error"):
            return 0
        rep = Queue._repeat_of(g)
        loop = _n(g.get("loop"))
        if loop >= 1:
            return max(0, rep - loop + 1)
        return rep

    def preview(self, settings: dict) -> dict:
        max_passes = _n(settings.get("queue_max_passes"), 50)
        rows = []          # 카드 평면 목록 (확인창의 「무엇을 몇 번 부르는가」 표) — 회차당 수치
        tree = []          # 루트 순서대로 card / group
        groups = []
        total_calls = 0    # 실행 명령 호출 수 (소요 시간 감) — 그룹 회차를 곱한 값
        wing_calls = 0     # 그중 정령의 날개를 쓰는 것만 — 카탈로그상 소모는 execute_gathering/crafting/altering 뿐,
                           # complete_altering_work(수령) 은 소모 없음
        with self.lock:
            for it in self.items:
                if it.get("type") == "group":
                    kids = [r for r in (self._preview_card(k, max_passes) for k in (it.get("items") or [])) if r]
                    left = self.loop_left(it, self.running)
                    if left == 0:
                        # 돌지 않는 그룹(끝남·치명) — 남는 것은 **게임에 이미 걸린 가공의 수령**뿐이다
                        # (`_collect_ready` 는 그룹 상태를 가리지 않는다). 수령은 날개를 쓰지 않는다.
                        kids = [r for r in kids if r["status"] == "waiting"]
                        left = 1 if kids else 0
                        if not kids:
                            # **다 끝난 그룹은 확인창에 싣지 않는다** — 할 일이 없다 (예전 테스트의 끝난 그룹이
                            # 「2회차 (남은 0) · 0항목」으로 새 큐 위에 떠 있었다). 보드의 완료 탭에는 그대로 남는다.
                            continue
                    rows += kids
                    per = sum(r["calls"] for r in kids)
                    per_w = sum(r["wingCalls"] for r in kids)
                    total_calls += per * left
                    wing_calls += per_w * left
                    groups.append({"id": it["id"], "name": it["name"], "repeat": _n(it.get("repeat"), 1),
                                   "loopLeft": left, "callsPerRound": per, "calls": per * left,
                                   "wingCallsPerRound": per_w, "wingCalls": per_w * left,
                                   "wings": per_w * left * WINGS_PER_CALL})
                    tree.append({"kind": "group", "id": it["id"], "name": it["name"],
                                 "detail": group_summary(it)["line"], "repeat": _n(it.get("repeat"), 1),
                                 "loopLeft": left, "callsPerRound": per, "calls": per * left,
                                 "children": kids})
                    continue
                r = self._preview_card(it, max_passes)
                if not r:
                    continue
                rows.append(r)
                total_calls += r["calls"]
                wing_calls += r["wingCalls"]
                tree.append({"kind": "card", "id": r["id"], "type": r["type"], "name": r["name"],
                             "detail": r["detail"], "calls": r["calls"]})
        # 안전 상한은 **채집**에만 댄다. 가공 등록·나눠 부르는 제작은 호출 수가 수량에서 정해지고(무한히 늘지 않는다)
        # 날개는 날개 차단기가 따로 지킨다 — 가공 63건·제작 600회가 상한(50)에 막히던 것을 푼다.
        over = [r for r in rows if r.get("type") not in PASS_EXEMPT and r["passesPlanned"] > max_passes]
        # 지금 캐릭터 상태 — 확인창이 "이 상태로는 거부된다"를 미리 보여줄 수 있게 실시간으로 읽는다 (캐시 아님, 잠금 밖에서 1회)
        activity = None
        act_warn = []
        if settings.get("queue_precheck", True) is not False:
            ar = self.cli("get_activity", "", READ_TIMEOUT)
            if ar.ok:
                activity = activity_summary(ar.body)
                if not activity["ok"]:
                    act_warn.append(f"지금 상태로는 실행이 거부됩니다: {ERROR_KO.get(activity['error'], activity['error'])} ({activity['field']}={activity['value']})")
                act_warn += activity.get("warnings", [])
                pmode = str(settings.get("queue_on_performance") or DEFAULT_ON_PERFORMANCE).lower()
                pmode = pmode if pmode in PERF_MODES else DEFAULT_ON_PERFORMANCE
                if (activity.get("perf") or {}).get("playing"):   # 시작하면 연주 대기로 들어간다 — 확인창에서 미리 말한다 (우선마다 다른 글)
                    act_warn.append(ERROR_KO["performance_playing" if pmode == "music" else f"performance_playing_{pmode}"])
            else:
                activity = {"ok": None, "error": ar.error, "text": f"상태 점검 실패: {ar.error} {ar.message}".rstrip()}
                act_warn.append(f"캐릭터 상태를 읽지 못했습니다({ar.error}) — 시작하면 같은 이유로 멈춥니다.")
        return {"items": rows, "rows": tree, "groups": groups,
                "calls": total_calls, "execCalls": total_calls, "wingCalls": wing_calls,
                "wings": wing_calls * WINGS_PER_CALL, "onError": self.config["onError"],
                "activity": activity,
                # 날개 차단기 — 확인창의 회색 한 줄 「최근 10분 소모 N · 헛소모 M회 — 상한 150 / 4」
                "wingGuard": {**self.wing_window(), "cap": wing_limits(settings)[0], "wasteCap": wing_limits(settings)[1],
                              "capMin": wing_limits(settings)[2], "wasteMin": wing_limits(settings)[3]},
                "cost":(f"채집·제작·가공 등록은 회당 정령의 날개 {WINGS_PER_CALL}개를 소모합니다 — 예상 {wing_calls}회"
                         + (" (그룹 회차 포함)" if groups else "")
                         + f" = 약 {wing_calls * WINGS_PER_CALL}개."
                         + (" 수령(complete_altering_work)은 소모하지 않습니다."
                            if total_calls > wing_calls else "")
                         + " 잔액은 CLI 가 성공 응답마다 알려줍니다."),
                # 알려진 방해 요소 — 시작 확인창이 「그래도 진행할까요」를 묻는 자리에 띄운다.
                # 막지는 않는다. CLI 가 미리 알려 주지 않는 것이라 사람이 판단할 일이다.
                "hazards": [{"name": r["name"], "note": gather_hazard(r["name"])}
                            for r in rows if r.get("type") == "gather" and gather_hazard(r["name"])],
                "warnings": act_warn
                            + ([f"안전 상한({max_passes}회)을 넘는 항목: " + ", ".join(r["name"] for r in over)] if over else [])
                            + ([("오류가 난 항목은 건너뛰고 다음으로 갑니다. 연결 끊김·blocked 는 체인을 멈춥니다."
                                 if self.config["onError"] == "continue" else "오류가 나면 그 항목에서 체인을 멈춥니다.")
                                + " 자동 재시도는 없습니다 (연결이 잠깐 끊긴 읽기만 두 번 더 읽습니다 — 실행 명령은 다시 보내지 않습니다)."])}

    # ── 실행 ──
    def start(self, settings: dict, override_breaker: bool = False) -> dict:
        """override_breaker — 날개 차단기가 막아도 **사람이 확인하고** 시작한다 (강제 시작까지 막지는 않고
        한 번 더 묻는다). 그때는 차단기 창을 비워 새로 센다 —
        안 비우면 시작하자마자 같은 기록으로 다시 멈춘다."""
        with self.lock:
            if self._closing:
                return {"ok": False, "error": "closing", "message": "앱을 끝내는 중입니다."}
            if self.running:
                return {"ok": False, "error": "busy", "message": "이미 실행 중입니다."}
        # 게임 연결 확인(status, 날개 0)은 **잠금 밖에서** — 끊겨 있으면 짧게 다시 본다(READ_RETRY_WAITS).
        # 잠금을 쥔 채 기다리면 그동안 화면의 상태 조회가 전부 막힌다.
        pre_probe = self._probe_retry() if self.probe else None
        with self.lock:
            if self._closing:   # 종료가 러너를 세운 뒤 새 러너가 뜨면 os._exit 가 CLI 명령 도중에 프로세스를 죽인다
                return {"ok": False, "error": "closing", "message": "앱을 끝내는 중입니다."}
            if self.running:
                return {"ok": False, "error": "busy", "message": "이미 실행 중입니다."}
            # 「오류 시 정지」·사용자 정지로 멈춘 그룹은 대기 열에 서 있다(column_of). 화면이 대기라고
            # 보여 주는데 러너가 pending 만 집어 들면 「시작」을 눌러도 그 그룹만 안 돈다 — 여기서 되돌린다.
            # 자식의 실패·완료 표시는 그대로다: 이번 회차의 남은 pending 자식부터 이어 간다.
            # 끊긴 자식(stopped)은 실패가 아니라 중단이다 — 같이 대기로 돌린다. 이어 갈 회차는 `loop` 에 남아 있다.
            for x in self.items:
                # 낱장 카드도 같다 — ■ 정지로 끊긴 카드는 대기 열에 서고(COLUMN_OF), ▶ 시작이 다시 집어 든다.
                # 그룹만 되살리면 대기 열에 보이는데 시작해도 안 돈다.
                if x.get("type") != "group" and x.get("status") == "stopped":
                    x["status"] = "pending"; x.pop("error", None); x.pop("message", None)
                if x.get("type") == "group" and x.get("status") == "stopped" and group_has_left(x):
                    x["status"] = "pending"
                    x.pop("message", None)
                    for k in (x.get("items") or []):
                        if k.get("status") == "stopped":
                            k["status"] = "pending"; k.pop("error", None); k.pop("message", None)
            todo = [x for x in self._runnable_leaves() if x.get("status") in ("pending", "waiting")]
            # 회차 사이에서 멈춘 그룹은 자식이 전부 done 이라도 남은 회차가 일이다
            rounds_left = [x for x in self.items if x.get("type") == "group" and x.get("status") == "pending"
                           and 0 < _n(x.get("loop")) < min(REPEAT_MAX, max(1, _n(x.get("repeat"), 1)))]
            if not todo and not rounds_left:
                return {"ok": False, "error": "empty", "message": "실행할 항목이 없습니다."}
            max_passes = _n(settings.get("queue_max_passes"), 50)
            over = [x["name"] for x in todo if x.get("type") not in PASS_EXEMPT and _n(x.get("progress", {}).get("passesPlanned")) > max_passes]
            if over:
                return {"ok": False, "error": "max_passes", "message": f"안전 상한({max_passes}회)을 넘습니다: " + ", ".join(over)}
            # 날개 차단기 — 날개를 쓸 일이 있을 때만 (수령만 남았으면 날개 0 이라 막을 이유가 없다)
            # 걸다 만 가공(waiting · 남은 등록)도 날개를 쓴다 (N6)
            if rounds_left or any((x.get("status") == "pending" and x.get("type") in ("gather", "craft", "alter"))
                                  or (x.get("type") == "alter" and x.get("status") == "waiting" and alter_calls(x)[1] > 0)
                                  for x in todo):
                refused = self._wing_gate(settings)
                if refused and not override_breaker:
                    return dict(refused, overridable=True)   # 화면이 「그래도 시작할까요?」를 묻는다
                if refused:
                    with self._wing_lock:
                        self._wing_log.clear()
                    self._event("breaker", "", f"날개 차단기를 사람이 확인하고 시작 ({refused.get('error')}) — 차단기 창을 새로 셉니다")
            if self.probe:
                pr, tries = pre_probe
                if pr.get("pipe") != "connected":
                    return {"ok": False, "error": "cli_disconnected",
                            "message": ERROR_KO["cli_disconnected"] + (f" ({pr.get('reason')})" if pr.get("reason") else "")
                            + (f" — {tries}회 다시 확인했습니다" if tries else "")}
            hold = self._music_start_gate(settings)   # 연주 중이면 거절이 아니라 **연주 대기** — 러너가 첫 카드 앞에서 기다린다
            self._stop.clear()
            self._removed.clear()
            self._group_halt = None
            self._wing_limits = wing_limits(settings)
            self._wing_tripped = None
            self.running = True
            self.last_error = None
            self.stop_reason = None
            self.current_group = None
            self.hold = None
            self._hold_first = hold
            self._thread = threading.Thread(target=self._run, args=(dict(settings),), daemon=True, name="queue-runner")
            self._thread.start()
            return {"ok": True, "hold": True} if hold else {"ok": True}

    def _probe_retry(self) -> tuple:
        """게임 연결 확인(status) → (답, 다시 본 횟수). 끊겨 있으면 READ_RETRY_WAITS 만큼 쉬고 다시 본다.
        CLI 가 꺼져 있거나 막힌 실행(disabled·exe 없음)은 다시 봐도 같으므로 한 번만 본다."""
        try:
            pr = self.probe() or {}
        except Exception as e:
            return {"pipe": None, "reason": f"{type(e).__name__}"}, 0
        tries = 0
        while (pr.get("pipe") not in ("connected", "disabled") and pr.get("found", True) is not False
               and tries < len(READ_RETRY_WAITS)):
            w = READ_RETRY_WAITS[tries]
            tries += 1
            self.say(f"[queue] 게임 연결 확인 실패({pr.get('pipe')}/{pr.get('reason')}) — {w:g}초 뒤 다시 ({tries}/{len(READ_RETRY_WAITS)})")
            time.sleep(w * wait_scale())
            try:
                pr = self.probe() or {}
            except Exception as e:
                pr = {"pipe": None, "reason": f"{type(e).__name__}"}
        return pr, tries

    def _music_start_gate(self, settings: dict):
        """「완전한 연주 우선」(music)인데 지금 연주 중인가 → 그 연주(perf_of 결과), 아니면 None.

        예전에는 여기서 `performance_playing` 으로 **거절**했다. 지금은 거절 대신 **연주 대기**다 —
        왜 시작되지 않는지 보이게 하려는 것이다. 돌려준 연주를 `start` 가 `_hold_first` 에 두면 러너가 첫 카드 앞에서
        기다린다(`_hold_for_music`). 연주를 건드리지 않는 규칙은 그대로다.
        get_activity 한 번(읽기, 비용 없음)으로 Performance.IsPlaying 을 본다.
        조회가 실패하면 여기서 막지 않는다 — 러너의 상태 점검이 그 오류로 멈춘다(같은 규칙 한 곳).
        상태 점검을 끈 설정(queue_precheck=false)이면 연주도 안 본다 (점검과 같이 꺼지는 결합 — test_performance.Coupling)."""
        mode = str(settings.get("queue_on_performance") or DEFAULT_ON_PERFORMANCE).lower()
        if mode not in PERF_MODES:
            mode = DEFAULT_ON_PERFORMANCE
        if mode != "music" or settings.get("queue_precheck", True) is False:
            return None
        try:
            r = self.cli("get_activity", "", READ_TIMEOUT)
            perf = perf_of(r.body) if r.ok else None
        except Exception:   # 읽기가 터져도 시작 자체를 죽이지 않는다 — 러너의 점검이 다시 본다
            return None
        if not perf or not perf["playing"]:
            return None
        return perf

    def stop(self) -> dict:
        """정지 플래그. 실행 명령이 파이프를 잡고 있으면 stop_action 을 잠금 없이 1회 보낸다 (다른 명령이 오면 canceled 로 끝난다 — 카탈로그)."""
        self._stop.set()
        sent = False
        if self.running and self._executing:
            try:
                r = self.raw_cli("stop_action", None, 30.0)
                sent = True
                self.say(f"[queue] stop_action → ok={r.ok} error={r.error}")
            except Exception as e:
                self.say(f"[queue] stop_action failed: {e}")
        return {"ok": True, "stopActionSent": sent}

    def is_busy(self) -> bool:
        """러너가 돌고 있거나 러너 스레드가 아직 살아 있다 (running 이 내려가도 finally 의 저장이 남아 있을 수 있다)."""
        with self.lock:
            th = self._thread
            return bool(self.running) or bool(th is not None and th.is_alive())

    def reload_from_disk(self) -> dict:
        """큐 파일을 다시 읽는다 — 백업 복원(server._restore_with_queue)이 파일을 바꾼 뒤 부른다.

        시작할 때와 같은 되살리기(`_load`: 재개 없음·waiting 유지·모양 보정)를 탄다. 러너가 살아 있으면 **거절한다** —
        도는 중에 메모리를 갈아 끼우면 러너가 끝나며(`_run` 의 finally → `_save`) 옛 사본으로 다시 덮어쓴다.
        옛 대기열을 가리키는 오류 배너(last_error)와 체인 정지 사유(stop_reason)도 걷는다."""
        with self.lock:
            if self.is_busy():
                return {"ok": False, "error": "busy", "message": "실행 중에는 대기열을 다시 읽을 수 없습니다."}
            self._load()
            self.last_error = None
            self.stop_reason = None
            return {"ok": True}

    def wait(self, timeout: float = 30.0) -> bool:
        t = self._thread
        if t:
            t.join(timeout)
            return not t.is_alive()
        return True

    def shutdown(self, timeout: float = 8.0) -> dict:
        """앱 종료. 정지 → 러너가 끝나기를 **짧게** 기다림 → 못 끝났으면 여기서 굳힌다.

        전에는 종료가 큐를 세우지 않아 **CLI 자식이 명령을 끝까지 수행했다**(앱은 꺼졌는데 게임은 계속 채집).
        실행 명령의 타임아웃(EXEC_TIMEOUT 660초)을 기다리지 않는다 — `timeout` 뒤에는 러너가 아직 CLI 답을
        기다리고 있어도, 파일에 running 이 남지 않게 **도는 카드·그룹을 stopped 로 저장**한다. 다음 기동은
        `_load` 가 running·stopped 를 pending 으로 되살리고(done·waiting 유지, 그룹 loop 유지) 「▶ 시작」이 끊긴 회차부터
        잇는다. 돌던 것이 없으면 게임에 아무것도 보내지 않는다. 한 번 부르면 이 큐는 다시 시작하지 않는다(`closing`).
        `timeout` 은 **호출 시작부터** 센다 — stop_action 이 늦게 답해도 늘어나지 않는다(최악 = timeout + 잠금 1초 + 저장)."""
        t0 = time.monotonic()
        # 종료 플래그는 `start()` 와 **같은 잠금 안에서** 세운다 — 밖에서 세우면 이미 잠금 안에 든 start 가
        # `_stop.clear()` 뒤 새 러너를 띄울 수 있다. 러너가 잠금을 쥐고 있어도 종료는 막히지 않게 1초만 기다린다.
        got = self.lock.acquire(timeout=1.0)
        try:
            self._closing = True
            self._stop.set()
            was = self.running
        finally:
            if got:
                self.lock.release()
        # stop_action 은 **따로** 보낸다. 파이프가 막혀 늦게 답하면(상한 30초) 그 뒤에야 러너 대기를 시작해
        # os._exit 타이머(대기 + 3초)가 먼저 터졌다 — stopped 저장도, 양보받은 연주 돌려주기도 못 했다.
        # 게임에 정지를 보내는 일 자체는 끊지 않는다(타임아웃을 줄이면 CLI 자식이 죽어 정지가 안 닿는다).
        if was:
            threading.Thread(target=self.stop, daemon=True, name="queue-stop").start()
        else:
            self.stop()   # 돌던 것이 없으면 게임에 보낼 것도 없다 — 곧바로 끝난다
        finished = self.wait(max(0.0, timeout - (time.monotonic() - t0)))
        if finished:
            return {"ok": True, "wasRunning": was, "finished": True}
        got = self.lock.acquire(timeout=1.0)   # 러너가 잠금을 쥔 채 멈춰 있어도 종료는 막히지 않는다
        try:
            # 여기서 굳힌 뒤 러너가 뒤늦게 돌아와도(CLI 가 답을 줌) running 은 다시 안 적힌다 — `_stop` 이 서 있어
            # 새 카드를 집지 않고, finally 가 running → stopped 로 저장한다. 그때 적히는 done 은 사실이다.
            self.stop_reason = self.stop_reason or "user"
            for x in self.items + self._leaves():
                if x.get("status") == "running":
                    x["status"] = "stopped"
                    self._log(x, "앱 종료로 정지")
            self._save()
        finally:
            if got:
                self.lock.release()
        self.say(f"[queue] 앱 종료 — 러너가 {timeout:.0f}초 안에 끝나지 않아 상태를 stopped 로 저장했습니다")
        return {"ok": True, "wasRunning": was, "finished": False}

    def _log(self, it: dict, msg: str) -> None:
        it.setdefault("log", []).append({"t": time.time(), "msg": msg})
        del it["log"][:-LOG_KEEP]
        self.say(f"[queue] {it['type']}:{it['name']} — {msg}")

    def _fail(self, it: dict, error: str, message: str = "") -> None:
        # **같은 오류가 연달아 몇 번인가**를 센다. 실패한 실행도 정령의 날개를 쓴다 —
        # 실제 로그에 `result=stopped, 획득 4개 → 정령의 날개 5 spent` 가 그대로 남아 있다.
        # 도구가 망가졌거나 재료가 없으면 회차마다 5개씩 계속 태우므로, 몇 번 보고 그 카드를 뺀다.
        # 다른 오류가 나오거나 한 번 성공하면 0 으로 돌아간다 (`_done`·`_reset_for_loop`).
        it["streak"] = (_n(it.get("streak")) + 1) if it.get("streakError") == error else 1
        it["streakError"] = error
        it["status"] = "error"
        it["error"] = error
        it["message"] = message or ERROR_KO.get(error, "")
        # 직전 상태 점검이 「전장 안」 경고를 달고 통과했는데 가공·제작·수령이 실패했다 — 이동 실패로 짐작된다
        if (it.pop("precheck_bf", None) and it.get("type") in ("alter", "craft", "collect")
                and error not in PRECHECK_ERRORS and error != "internal"):
            it["message"] = (it["message"] + " · " if it["message"] else "") + BATTLEFIELD_FAIL_HINT
        # 직전 점검이 「전투 중」 경고를 달고 통과했는데 게임이 끊었다 — 몬스터에 붙잡힌 것으로 짐작된다
        if it.pop("precheck_combat_warn", None) and error == "stopped_by_user":
            it["message"] = (it["message"] + " · " if it["message"] else "") + COMBAT_FAIL_HINT
        self._log(it, f"오류 {error}: {it['message']}")
        self._event("error", it["id"], f"{it['name']}: {error} — {it['message']}")
        # 날개 차단기가 체인을 세웠으면 배너(lastError)는 그 사유로 남긴다 — 카드 오류는 카드에 남아 있다
        if it["id"] not in self._removed and not self._wing_tripped:   # 지워진 카드의 오류를 배너로 띄우면 없는 항목을 가리킨다
            # **실패한 그 항목**의 이름·종류를 같이 싣는다. 전에는 id 만 있어서 오버레이가 「지금 도는 카드」
            # (currentCard) 이름에 이 오류를 붙였다 → 동 광석이 실패했는데 「거미줄 채집 실패」가 떴다.
            self.last_error = {"id": it["id"], "name": it.get("name") or "", "type": it.get("type") or "",
                               "error": error, "message": it["message"], "at": time.time()}

    def _done(self, it: dict, msg: str) -> None:
        it.pop("streak", None); it.pop("streakError", None)   # 한 번 되면 연속은 끊긴다
        it["status"] = "done"
        it["progress"]["totalDone"] = _n(it["progress"].get("totalDone")) + _n(it["progress"].get("done"))
        self._log(it, msg)
        self._event("done", it["id"], f"{it['name']}: {msg}")

    def _stuck(self, it: dict) -> bool:
        """같은 오류가 `STREAK_STOP` 번 연달아 났나 — 그러면 다음 회차에 다시 시도하지 않는다.

        **실패한 실행도 날개를 쓴다.** 도구 파손·재료 없음처럼 사람이 손대야 풀리는 것은
        회차를 돌수록 5개씩 그냥 태운다. 그렇다고 한 번 실패로 접으면 일시적인 실패
        (이동 중 방해 등)에 너무 예민하다. 그래서 **연달아 세 번**을 기준으로 둔다."""
        return _n(it.get("streak")) >= STREAK_STOP

    def _reset_for_loop(self, it: dict) -> None:
        """회차 시작: 상태 pending, 회차별 진행 초기화, 누적(totalDone)은 유지."""
        # **연속 횟수(streak)는 여기서 지우지 않는다.** 지우면 회차마다 1로 되돌아가
        # 영원히 상한에 닿지 않는다 — 날개만 계속 탄다. 지우는 곳은 두 군데뿐이다:
        # 한 번 성공했을 때(`_done`)와 사람이 「대기로」 되돌렸을 때(`reset`).
        it["status"] = "pending"
        it.pop("error", None); it.pop("message", None)
        p = it.setdefault("progress", {})
        p["done"] = 0; p["passes"] = 0
        p.pop("collectRetry", None); p.pop("collectRetryAt", None)   # 「수령분 없음」 재시도 횟수는 회차 단위
        p["have"] = bag_count(it["name"])
        if it["type"] == "gather":
            p["passesPlanned"] = gather_runs(it)
            p.pop("bag0", None)   # 회차마다 새로 센다 — 첫 회 전 가방을 다시 읽는다
        elif it["type"] == "alter":
            p["passesPlanned"] = alter_passes(_n(it.get("count"), 1), it.get("collect"))
            p["got"] = 0                           # 등록·수령은 회차 단위 (N6)
            p.pop("slot", None); p.pop("cap", None)
            it.pop("regStop", None)
        elif it["type"] == "craft":
            p["calls"] = 0
            p["passesPlanned"] = craft_plan(it, p)

    def _run(self, settings: dict) -> None:
        """루트를 한 바퀴. 그룹이면 _run_group, 아니면 카드 1회.

        위치(index)가 아니라 **id 로 고른다** — 실행 중에도 사용자가 항목을 빼거나 순서를 바꿀 수 있어서,
        위치 커서를 쓰면 리스트가 밀리며 아직 실행 안 한 항목이 조용히 건너뛰어진다.
        seen 은 이미 집어 든 항목 — 사용자가 도중에 「재시도」로 pending 을 만들어도 무한 반복하지 않게."""
        awake = keep_awake(True)   # 도는 동안 윈도 절전 막기 (이 스레드에 묶인다 — finally 에서 푼다)
        if awake:
            self.say("[queue] 실행 중 절전 막기 켬")
        try:
            first = self._hold_first
            self._hold_first = None
            if first is not None and not self._hold_for_music(None, settings, first):
                return      # 연주 대기 중 ■ 정지 — 아무것도 보내지 않았다. finally 가 정리한다 (사유 user)
            seen: set[str] = set()
            while not self._stop.is_set():
                with self.lock:
                    it = next((x for x in self.items
                               if x.get("status") == "pending" and x.get("id") not in seen), None)
                    if it is None:
                        break
                    seen.add(it["id"])
                res = (self._run_group(it, settings) if it.get("type") == "group"
                       else self._run_card(it, settings))
                if self._wing_tripped:   # 날개 차단기가 세웠다 — 사유·배너는 이미 적혔다. 카드 결과로 덮지 않는다
                    break
                if res == "fatal":
                    self.stop_reason = self.stop_reason or f"fatal:{it.get('error')}"
                    self._event("stop", it["id"], f"보드 정지 — {it.get('error')} 는 다음 항목도 성공할 수 없는 오류")
                    break
                if res == "error" and self.config["onError"] == "stop":
                    self.stop_reason = "onError"
                    self._event("stop", it["id"], "보드 정지 — 설정 onError=stop")
                    break
                if res == "stopped":
                    break
            self._collect_ready(settings)      # 끝: 완료된 가공 수령
            if not self._stop.is_set() and self.stop_reason is None:
                self._wait_pending_alters(settings)   # 체인이 다 끝났는데 가공이 남아 있으면 기다렸다 수령
        finally:
            try:
                self._settle_orphan()          # 넘겨준 채집이 아직 돌면 stop_action 을 다시 보내 본다
            except Exception as e:
                self.say(f"[queue] 넘겨준 채집 정리 실패: {type(e).__name__}: {e}")
            with self.lock:
                self.running = False
                self.current = None
                self.current_group = None
                self.hold = None
                self._executing = False
                if self._stop.is_set():
                    # 같은 플래그를 **보드 정지**(수령에서 난 치명·오류 시 정지)도 쓴다 — 사유가 먼저 적혀 있으면
                    # 사용자 정지가 아니다. 사용자 정지라고 적으면 화면이 「사용자가 정지했습니다」라고 거짓말한다.
                    user = self.stop_reason is None
                    self.stop_reason = self.stop_reason or "user"
                    for x in self.items + self._leaves():
                        if x.get("status") == "running":
                            x["status"] = "stopped"
                            self._log(x, "사용자 정지" if user else f"보드 정지 ({self.stop_reason})")
                    if user:
                        self._event("stop", None, "사용자 정지")
                for x in self.items:   # 끝나고 남은 「실행 중」 그룹 표시를 정리한다 (열 판정이 run 에 머물지 않게)
                    if x.get("type") == "group" and x.get("status") == "running":
                        x["status"] = "stopped" if self._stop.is_set() else "done"
                self._save()
            # **양보를 부탁했으면 그 부탁을 거둔다** — 잠금 밖에서. 거두지 않으면 폴리오에 「경계에서 멈춰 선」
            # 표시가 남아 나중에 다시 틀 수 있다. **이어서 틀지는 않는다** — 작업이 끝났다고 연주를 다시 트는 것은
            # 버그였다. 사용자 정지로 끝났어도 같다.
            if awake:
                keep_awake(False)
                self.say("[queue] 절전 막기 풂")
            self._release_performance()
            try:
                self._finish_notify(settings)   # 큐 종료 알림 (설정 queue_done_notify · 사용자 정지 제외) — 알림 탓에 정리가 죽지 않게
            except Exception as e:
                self.say(f"[queue] 종료 알림 실패: {type(e).__name__}: {e}")

    def _run_card(self, it: dict, settings: dict, wait_alter: bool = False) -> str:
        """카드 한 장 실행 → "ok" | "error" | "fatal" | "stopped". 정지 판단은 부르는 쪽(보드/그룹)이 한다."""
        with self.lock:
            it["status"] = "running"
            self.current = it["id"]
        self._collect_ready(settings)          # 항목 전환 시: 가공 돌봄 — 모였으면 수령 · 빈 칸에 남은 등록 (N6)
        with self.lock:
            self.current = it["id"]            # 돌봄이 가리켰던 커서를 이 카드로 되돌린다
        # 그 수령이 보드(치명·오류 시 정지)나 이 그룹(그룹의 오류 시 정지)을 세웠으면 **이 카드는 시작하지 않는다.**
        # 전에는 stopReason 만 적고 그대로 돌려서, 끝난 뒤 화면이 「오류 시 정지로 멈췄습니다」라고 거짓말했다.
        halted = self._stop.is_set() or (self._group_halt is not None and self._group_halt == self.current_group)
        if halted:
            with self.lock:
                it["status"] = "pending"
                self.current = None
                self._save()
            return "stopped" if self._stop.is_set() else "error"
        self._event("start", it["id"], f"{it['name']} 시작")
        self._run_item(it, settings, wait_alter)
        with self.lock:
            self.current = None
            self._save()
        if it.get("status") == "error":
            return "fatal" if is_fatal(it.get("error")) else "error"
        if it.get("status") == "stopped":
            return "stopped"
        return "ok"

    def _run_group(self, g: dict, settings: dict) -> str:
        """그룹 한 개 = 회차 반복. → "ok" | "error" | "fatal" | "stopped".

        그룹 안의 오류 정책(g.onError)은 **그 그룹만** 멈춘다. 보드를 멈추는 것은 fatal 과,
        그룹이 error 로 끝났을 때의 보드 전역 config.onError 뿐이다."""
        # 정지했다가 다시 시작한 그룹은 **멈췄던 회차부터** 잇는다 (loop 가 남아 있다). 새 그룹·「대기로」
        # 되돌린 그룹은 loop=0 이라 1회차부터. 잇는 회차에서는 자식을 되돌리지 않는다 — 이미 끝난 자식을
        # 또 돌리면 회차가 하나 더 도는 셈이 된다.
        first = min(self._repeat_of(g), max(1, _n(g.get("loop"))))
        with self.lock:
            g["status"] = "running"
            g.pop("error", None); g.pop("message", None)
            self.current_group = g["id"]
            self._group_halt = None
        try:
            loop = first
            while True:
                # **회차 수는 매 회차 다시 읽는다.** 실행 중에 허용된 유일한 수정이 「회차 늘리기」인데,
                # 한 번 읽고 for 를 돌면 늘린 값이 저장만 되고 이번 실행에는 안 먹었다.
                repeat = self._repeat_of(g)
                if loop > repeat:
                    break
                if self._stop.is_set():
                    g["status"] = "stopped"
                    return "stopped"
                if g["id"] in self._removed:   # 도중에 지워졌다 — 남은 회차를 건너뛰고 보드는 다음 항목으로
                    return "ok"
                with self.lock:
                    g["loop"] = loop
                    if loop > first:
                        for c in list(g.get("items") or []):
                            # retryFailed=false 면 지난 회차에 error 로 끝난 자식은 건드리지 않고 건너뛴다
                            if not g.get("retryFailed", True) and c.get("status") == "error":
                                continue
                            # 같은 오류가 연달아 STREAK_STOP 번 — 되돌리지 않는다. 되돌리면
                            # 다음 회차에 또 불러 날개만 태운다 (실패해도 5개가 나간다).
                            if c.get("status") == "error" and self._stuck(c):
                                self._log(c, f"같은 오류({c.get('error')})가 {_n(c.get('streak'))}번 연달아 나서 "
                                             f"이 카드는 남은 회차에서 뺍니다 — 원인을 고친 뒤 「대기로」 되돌리세요")
                                continue
                            # 이미 등록·결제된 가공(waiting)은 되돌리지 않는다 — 되돌리면 날개를 또 쓰고 중복 등록된다
                            if c.get("status") == "waiting":
                                continue
                            self._reset_for_loop(c)
                    self._save()
                self._event("loop", g["id"], f"「{g['name']}」 {loop}/{repeat}회차 시작")
                res = self._run_children(g, settings)
                if g["id"] in self._removed:       # 자식이 도는 사이에 지워졌다 — 그 결과를 그룹에 적지 않는다
                    return "ok"
                self._collect_ready(settings)      # 회차 끝: 완료된 가공 수령
                if self._group_halt == g["id"] and res == "ok":
                    res = "error"                  # 회차 끝 수령이 실패했고 이 그룹은 「오류 시 정지」다
                # 자식의 치명은 두 길로 온다 — 자식 실행에서 직접(res == "fatal"), 또는 카드 사이·회차 끝
                # **수령**에서(_collect_ready 가 보드를 세우고 stop_reason 에 fatal 을 적는다). 둘 다 그룹 실패다.
                bad = next((c for c in reversed(list(g.get("items") or []))
                            if c.get("status") == "error" and is_fatal(c.get("error"))), None)
                if res == "fatal" or (bad is not None and str(self.stop_reason or "").startswith("fatal:")):
                    # **그룹이 보드 실패 열로 가는 유일한 길.** 자식의 치명 코드(blocked·disconnected…)를
                    # 그룹에 그대로 적는다 — 「group_child」라고만 적으면 화면이 무슨 일인지 말할 수 없다.
                    g["status"] = "error"
                    g["error"] = (bad or {}).get("error") or "group_child"
                    g["message"] = (bad or {}).get("message") or "그룹 안 항목이 치명 오류로 끝났습니다."
                    self._log(g, f"{loop}/{repeat}회차에서 정지 — {g['error']} 는 다음 항목도 성공할 수 없는 오류")
                    return "fatal"
                if res == "stopped" or self._stop.is_set():
                    g["status"] = "stopped"
                    return "stopped"
                if res == "error":                 # g.onError == "stop" — 이 그룹만 멈춘다
                    # **실패가 아니라 정지다.** 실패한 자식은 그룹 안 실패 열에 남고,
                    # 그룹 카드는 남은 항목이 있으면 대기 열에 선다 — 「▶ 시작」이 다시 집어 든다.
                    g["status"] = "stopped"
                    g.pop("error", None)
                    g["message"] = "그룹 안 항목이 실패해 멈췄습니다 (이 그룹의 설정: 오류 시 정지)."
                    self._log(g, f"{loop}/{repeat}회차에서 그룹 정지 — 설정 오류 시 정지")
                    self._event("stop", g["id"], f"「{g['name']}」 그룹 정지 — 설정 오류 시 정지")
                    return "error"
                loop += 1
            g["status"] = "done"
            self._log(g, f"{repeat}회차 모두 끝남")
            self._event("done", g["id"], f"「{g['name']}」 {repeat}회차 완료")
            return "ok"
        finally:
            with self.lock:
                self.current_group = None
                self._group_halt = None
                self._save()

    def _run_children(self, g: dict, settings: dict) -> str:
        """그룹 안 한 회차. 루트와 같은 **id 커서 + seen 집합** — 위치 커서는 항목을 조용히 건너뛴다."""
        seen: set[str] = set()
        wait_alter = bool(g.get("waitAlter"))
        while True:
            if self._stop.is_set():
                return "stopped"
            if g["id"] in self._removed:   # 그룹째 지워졌다 — 자식 목록이 손에 남아 있어도 더 돌지 않는다
                return "ok"
            if self._group_halt == g["id"]:   # 카드 사이 수령이 실패했고 이 그룹은 「오류 시 정지」다
                return "error"
            with self.lock:
                c = next((x for x in (g.get("items") or [])
                          if x.get("status") == "pending" and x.get("id") not in seen), None)
                if c is None:
                    return "ok"
                seen.add(c["id"])
            res = self._run_card(c, settings, wait_alter)
            if res == "fatal":
                self.stop_reason = self.stop_reason or f"fatal:{c.get('error')}"
                self._event("stop", c["id"], f"보드 정지 — {c.get('error')} 는 다음 항목도 성공할 수 없는 오류")
                return "fatal"
            if res == "error" and g.get("onError") == "stop":
                return "error"
            if res == "stopped":
                return "stopped"

    # ── 채집 퀘스트 지킴이 ──────
    #
    # **CLI 에는 「채집 중이다」를 알려 주는 명령이 없다** (28개 전부 확인). 그래서
    # 「아직 캐고 있나」를 알 수 있는 길은 **퀘스트 추적창**뿐이다.
    #
    # 두 가지를 한다. **섞으면 안 된다.**
    #   ① 도는 동안: 1초마다 **읽기만** 한다. 실행이 파이프를 쥐고 있어 그 동안에는
    #      어차피 아무것도 못 보낸다 — 게다가 뭘 보내면 **도는 채집이 canceled 로 죽는다.**
    #      그래서 이때 하는 일은 「봤다」를 적어 두는 것뿐이다.
    #   ② 돌아온 뒤: 그 기록으로 판단한다. 여기서만 움직인다.
    QUEST_POLL = 1.0          # 1초에 한 번
    QUEST_TIMEOUT = 20.0      # 읽기 한 번의 상한 (실행 잠금을 안 타므로 짧아도 된다)

    def _quest_mode(self, settings: dict) -> str:
        m = str(settings.get("gather_quest_watch") or "off").lower()
        return m if m in ("off", "watch", "resume") else "off"

    def _quest_look(self, it: dict) -> dict:
        """추적창을 **한 번** 읽어 판정한다 → questwatch.classify 의 결과.

        **잠금을 안 탄다.** 실행 명령이 잠금을 몇 분 쥐고 있어서, 기다렸다가는
        채집이 끝난 뒤에야 첫 검사를 하게 된다 — 그러면 지켜본 뜻이 없다.
        읽기는 도는 동작을 갈아치우지 않는다.

        **공용 파일로 답을 메우지 않는다**(`allow_last_response=False`). 겹쳐 나가는
        자리라 `last-response.json` 이 남의 답일 수 있다. 못 읽으면 **모른다**로 둔다."""
        try:
            r = self.raw_cli("get_quests", "", self.QUEST_TIMEOUT, allow_last_response=False)
        except TypeError:          # 옛 서명(되돌아보기 끄기를 모르는 호출자)
            r = self.raw_cli("get_quests", "", self.QUEST_TIMEOUT)
        except Exception as e:
            return {"state": questwatch.BLIND, "row": None,
                    "why": f"퀘스트를 못 읽었습니다 ({type(e).__name__})"}
        if not getattr(r, "ok", False):
            return {"state": questwatch.BLIND, "row": None,
                    "why": f"퀘스트를 못 읽었습니다 ({getattr(r, 'error', '') or 'cli_error'})"}
        return questwatch.classify(r.body, it.get("name") or "")

    def _quest_watch(self, it: dict, stop: threading.Event, seen: dict) -> None:
        """채집이 도는 동안 1초마다 본다 — **읽기만 한다.**

        `seen` 에 마지막 판정을 적어 둔다. 상태가 바뀔 때만 로그를 남긴다
        (매초 같은 줄을 찍으면 로그가 못 쓰게 된다)."""
        base, gave_up = None, False
        while not stop.wait(self.QUEST_POLL):
            g = self._quest_look(it)
            seen["last"] = g
            # **도는 동안의 진행 숫자.**
            #
            # 예전 결론은 「올릴 방법이 없다」였다 — CLI 가 중간 보고를 안 하고, 찔러 보려고
            # 명령을 보내면 도는 채집이 죽기 때문이다. **그 결론이 틀렸다**: 퀘스트 추적창의
            # 설명글이 실시간으로 움직인다(실측 `8/30` → 1분 뒤 `12/30`). 읽기라 안 죽인다.
            #
            # 다만 그 숫자는 **게임이 낸 의뢰의 목표**(30)이지 우리 목표가 아니다. 그래서
            # 값 자체가 아니라 **시작점에서 얼마나 늘었는가**만 쓴다. 의뢰가 갱신되면
            # 숫자가 되레 줄어드는데, 그때는 **모른다로 돌아간다** — 지어내지 않는다.
            pr = questwatch.progress(g["row"]) if g.get("row") else None
            if it.get("progress", {}).get("liveSrc") == "bag":
                pass                               # 가방 지킴이(_gather_goal_watch)가 진짜 수를 적고 있다
            elif pr is None or gave_up:
                it.get("progress", {}).pop("live", None)
            elif base is None:
                base = pr[0]                       # 이 회차의 출발점
            elif pr[0] < base:
                # 의뢰가 갱신돼 숫자가 되감겼다. 여기서 다시 0 부터 세면 **화면의 수가
                # 뒤로 간다** — 사람이 그걸 오류로 읽는다. 이 회차에는 그냥 그만둔다.
                # 회차가 끝나면 회신의 `gained` 가 진짜 값을 준다.
                gave_up = True
                it.get("progress", {}).pop("live", None)
            else:
                it.setdefault("progress", {})["live"] = pr[0] - base
            if g["state"] != seen.get("said"):
                seen["said"] = g["state"]
                if g["state"] == questwatch.GONE:
                    self._log(it, f"주의: {g['why']} — 게임 화면(거래소·인벤토리 등)이 열렸을 수 있습니다")
                elif g["state"] == questwatch.BLIND:
                    self._log(it, f"주의: {g['why']}")

    def _quest_guard(self, it: dict, settings: dict):
        """지킴이를 켜고 끄는 문지기 → `(멈춤 신호, 본 것)`. 꺼져 있으면 `(None, {})`."""
        if self._quest_mode(settings) == "off":
            return None, {}
        stop = threading.Event()
        seen: dict = {}
        t = threading.Thread(target=self._quest_watch, args=(it, stop, seen),
                             daemon=True, name="quest-watch")
        t.start()
        return stop, seen

    def _quest_verdict(self, it: dict, settings: dict, r, seen: dict) -> str:
        """실행이 돌아왔다 — 퀘스트로 다시 본다 → `"keep"` | `"retry"` | `"as_is"`.

        **회신이 실패라고 해서 채집이 정말 끝난 것은 아니다.** 실행이 타임아웃으로
        돌아와도 채집 퀘스트가 살아 있으면 게임 안에서는 계속 캐고 있다는 관찰이 있다.
        사실이면 **멀쩡한 채집을 실패로 적게** 된다. (미검증 — 그래서
        이 판단은 **설정을 켠 사람에게만** 적용된다.)

          keep   퀘스트가 살아 있다 → 실패로 적지 않는다. 게임 안에서는 계속 캐는 중이다
          retry  퀘스트가 끊겼다 → 「다시 걸기」면 한 번 더 건다 (**날개가 든다**)
          as_is  모른다 · 꺼져 있다 → **원래대로** 처리한다. 모를 때는 아무것도 바꾸지 않는다
        """
        mode = self._quest_mode(settings)
        if mode == "off":
            return "as_is"
        g = self._quest_look(it)          # 돌아온 **지금**을 본다 (도는 동안 본 것은 참고)
        seen["final"] = g
        if g["state"] == questwatch.ALIVE:
            self._log(it, f"퀘스트 확인: {g['why']} — 회신은 {r.error or '중단'} 이지만 실패로 적지 않습니다")
            return "keep"
        if g["state"] == questwatch.BLIND:
            self._log(it, f"퀘스트 확인: {g['why']} — 판단을 바꾸지 않습니다")
            return "as_is"
        self._log(it, f"퀘스트 확인: {g['why']}")
        if mode != "resume":
            return "as_is"
        p = it.setdefault("progress", {})
        n = _n(p.get("quest_retry"))
        cap = _n(settings.get("gather_quest_retry_max"), 3)
        if n >= cap:
            self._log(it, f"다시 걸기 상한({cap}회)에 닿았습니다 — 여기서 멈춥니다")
            return "as_is"
        p["quest_retry"] = n + 1
        self._log(it, f"채집을 다시 겁니다 ({n + 1}/{cap}회째) — **정령의 날개 {WINGS_PER_CALL}개가 듭니다**")
        return "retry"

    QUEST_WAIT_CEIL = 1800.0   # 「끝날 때까지」의 천장(초) — 영영 물려 있지 않게

    def _quest_wait(self, it: dict, settings: dict) -> bool:
        """퀘스트가 **끝날 때까지** 지켜본다 → 끝까지 봤나.

        **여기서 아무것도 보내지 않는다.** 게임 안에서 아직 캐고 있는데 실행 명령을
        또 보내면 카탈로그대로 **도는 행동이 canceled 로 죽고**, 그 한 번에 날개가 5개다.
        할 수 있는 일은 기다리는 것뿐이다.

        「모른다」가 나와도 기다린다 — 화면이 잠깐 가려진 것일 수 있다. 다만 천장을 둔다."""
        end = time.time() + self.QUEST_WAIT_CEIL
        blind = 0
        while True:
            if self._stop.is_set():
                return False
            if self._stop.wait(self.QUEST_POLL):
                return False
            g = self._quest_look(it)
            if g["state"] == questwatch.GONE:
                return True
            if g["state"] == questwatch.BLIND:
                blind += 1
                if blind == 10:      # 10초쯤 계속 못 보면 한 번만 말한다
                    self._log(it, f"주의: {g['why']}")
            else:
                blind = 0
            if time.time() >= end:
                self._log(it, f"퀘스트가 {int(self.QUEST_WAIT_CEIL)}초 동안 끝나지 않았습니다 — 기다리기를 그만둡니다")
                return False

    def _bag_now(self, it: dict):
        """가방 수(가방+창고 합산)를 읽는다 → int, 못 읽으면 None. 연결 계열은 짧게 다시 읽는다 (_read)."""
        r = self._read(it, "get_items", None)
        if not r.ok:
            return None
        store.set_cache("items", r.body)
        return _n(work.stock(r.body)["total"].get(it["name"]))

    def _bag_count(self, it: dict, p: dict, reply_gained: int = 0, read: bool = True) -> int:
        """한 회가 끝난 뒤 가방으로 센다 → **이번 회에 늘어난 수**.

        progress.done = 지금 가방 − 첫 회 전 가방(bag0) — 카드의 「+N개」.
        가방을 못 읽으면(또는 read=False — 연결이 끊겨 읽어도 소용없을 때) 회신 gained 로 메운다 (지어내지 않는다 — 회신이 준 값이다)."""
        before = _n(p.get("have"))
        now = self._bag_now(it) if read else None
        if now is None:
            if read:
                self._log(it, f"가방을 못 읽었습니다 — 이번 회는 회신 획득 {reply_gained}개로 셉니다")
            p["have"] = before + max(0, reply_gained)
            p["done"] = _n(p.get("done")) + max(0, reply_gained)
            return max(0, reply_gained)
        if p.get("bag0") is None:
            p["bag0"] = before - _n(p.get("done"))
        p["have"] = now
        p["done"] = max(0, now - _n(p.get("bag0")))
        return now - before

    def _bag_gain(self, it: dict, p: dict) -> int:
        """가방을 다시 세어 **이번에 얼마나 늘었는지** → 개수 (퀘스트 지킴이가 회신 없이 끝을 본 경우).
        못 읽으면 0 을 더한다 — 없는 획득을 지어내는 쪽보다 안전하다."""
        return max(0, self._bag_count(it, p, 0))

    def _exec(self, command: str, body, it: dict):
        """실행 명령 호출 — 정지 시 stop_action 을 보낼 수 있게 플래그를 켠다.

        **날개·산출 장부도 여기서 적는다.** CLI 실행 명령은 전부 이 함수를 지나가므로
        여기 한 군데면 빠질 곳이 없다. 부르는 쪽마다 적게 하면 한 군데를 빠뜨렸을 때
        조용히 덜 세어진다 (그래 놓고 「기록이 있다」고 믿게 된다).

        **사망 잔상 재시도도 여기 한 군데다** (가공·제작·수령·채집 공통). CLI 가 blocked kind=dead 로 답하면
        DEAD_RETRY_WAIT 초 뒤 **같은 명령을 한 번만** 다시 보낸다 — 실측: 부활 직후 get_activity 는
        IsDead=false 인데 CLI 는 kind=dead 로 거절했다. 이 거절은 0.1초·날개 0 이라 다시 보내도 잃는 것이 없다.
        두 번째도 kind=dead 면 그대로 돌려주고, _exec_failed 가 부활 안내로 끝낸다. 정지 중이면 다시 보내지 않는다."""
        r = self._exec_once(command, body, it)
        if _dead_blocked(r) and not self._stop.is_set():
            self._log(it, f"게임이 사망으로 답했습니다 — {DEAD_RETRY_WAIT:g}초 뒤 한 번 다시 보냅니다 (부활 직후 잔상)")
            time.sleep(DEAD_RETRY_WAIT)
            if not self._stop.is_set():
                r = self._exec_once(command, body, it)
        # 마지막 실행 명령이 가공 등록이었나 (N6) — 그 직후 다른 명령이 unknown_modal 이면 「가공 대기열 가득」 창으로 본다.
        # 실패한 답은 바꾸지 않는다 — 막힌 그 명령이 바로 판단할 대상이다
        if command == "execute_altering":
            self._alter_recent = bool(r.ok)
        elif r.ok:
            self._alter_recent = False
        # 날개 차단기 — 마지막 답 하나만 센다 (사망 거절은 날개 0 이라 첫 시도를 셀 것이 없다). 기록 탓에 작업이 죽지 않게
        try:
            self._wing_record(command, it, r)
        except Exception as e:
            self.say(f"[queue] 날개 기록 실패: {type(e).__name__}: {e}")
        if r.ok and isinstance(r.body, dict) and r.body.get("result") == "started" and command in WING_COMMANDS:
            # 실측: 수락 즉시(t+1.2초) 이동 퀘스트·자동 이동이 서고 날개가 빠진다. 호출이 막혀 있어 도중에는 못 본다
            # — 폴링을 더하지 않는다. 돌아온 답만 적는다.
            self._log(it, f"출발·등록 확인: result=started {r.body.get('cost') or ''}".rstrip())
        return r

    def _exec_once(self, command: str, body, it: dict):
        with self._exec_lock:
            self._exec_n += 1
            self._executing = True
        try:
            r = self._call(command, body, EXEC_TIMEOUT)
        finally:
            with self._exec_lock:
                self._exec_n = max(0, self._exec_n - 1)
                self._executing = self._exec_n > 0
        try:
            self._ledger(command, it, r)
        except Exception:
            pass          # **장부 때문에 작업이 멈추면 안 된다** — 기록은 부차적이다
        return r

    def _ledger(self, command: str, it: dict, r) -> None:
        """실행 한 번을 장부에 한 줄. 여기서 터져도 작업은 계속한다 (`ledger.add` 가 삼킨다)."""
        b = r.body if isinstance(r.body, dict) else {}
        # 날개 수와 「헛소모」 판정은 차단기와 같은 규칙(wing_spend) — cost 줄·무료 거절·stopped_by_user 를 같은 눈으로 본다.
        # ok 답이라도 헛소모(이동 끊김 등)면 장부에는 **실패**로 남긴다 — 날개를 쓴 실패도 기록에 남아야 한다
        wings, wasted = wing_spend(command, r)
        # 산출은 **회신이 실제로 준 값만** 적는다. 지어내지 않는다.
        if command == "execute_gathering":
            out = _n(b.get("gained"))                     # 중단돼도 부분 획득은 준다
        elif command == "execute_crafting":
            # craftCount 는 **횟수**다. 한 번에 몇 개가 나오는지(per)는 레시피에서 온다.
            per = _n((it.get("progress") or {}).get("per"), 1) or 1
            out = _n(b.get("craftCount"), _n(it.get("count"), 0) if r.ok else 0) * per
        elif command == "complete_altering_work":
            out = _n(b.get("collected"))                  # **건수**다 (개수가 아니다)
        else:
            out = 0                                       # 가공 등록은 그 자리에서 나오는 것이 없다
        err = r.error or (str(b.get("result") or "") if wasted else "")
        ledger.add(command, it.get("name") or "", wings, out, bool(r.ok) and not wasted, err)

    def _stopped(self, it: dict) -> bool:
        if self._stop.is_set():
            it["status"] = "stopped"
            self._log(it, "사용자 정지" if self.stop_reason is None else f"보드 정지 ({self.stop_reason})")
            return True
        return False

    # 「연주」 카드가 탈것 위에서 폴리오 설정 「탈것은 제작으로 내리기」(folio_dismount_by_craft)로 스스로 내릴 수 있는가.
    # server 가 폴리오 설정을 읽어 꽂는다 — None 이면 모른다 = 못 내린다로 본다 (탄 채로 부탁하지 않는다)
    play_mount_ok = None      # () -> bool

    def _precheck(self, it: dict, settings: dict, play: bool = False) -> bool:
        """실행 명령을 부르기 **전에** get_activity 로 캐릭터 상태를 본다 (읽기, 비용 없음).
        대화 선택·던전·지역 임무·연출이면 CLI 실행 명령을 부르지 않고 그 항목을 error 로 끝낸다 (FATAL → 체인 정지).
        사망·부활·전투·전장은 막지 않는다 (activity_summary 참고 — 실측). 사망은 CLI 가 비용 없이 스스로
        blocked kind=dead 로 거절하고, 부활 직후의 잔상은 _exec 가 3초 뒤 한 번 다시 보내 걸러 낸다.
        점검을 통과했는데도 blocked 가 오면 _exec_failed 가 「게임 상태 보고 불일치」를 로그에 남긴다.

        `play=True` 는 「연주」 카드 — 같은 조회·같은 판정에 둘을 더 본다:
          · 탈것 탑승 중 → `precheck_mounted` (게임이 탄 채로는 연주를 받지 않는다). 폴리오 설정 「탈것은 제작으로 내리기」가
            켜져 있으면(`play_mount_ok`) 막지 않는다 — 폴리오의 `play()` 가 제 길(`_mount_gate` → 제작으로 내리기)로 내리고 튼다.
            치명이 아니다 — 뒤의 작업 카드는 탄 채로도 돈다.
          · 사망/부활 → `precheck_dead` (치명 — 보드가 선다). 작업 카드는 CLI 가 스스로 거절해 경고로만 두지만, 연주 부탁은
            폴리오를 거쳐 가서 그 거절이 카드에 안 돌아온다 — 보내기 전에 막는다.
        전투·전장은 작업 카드와 같이 경고만. 연주 중이면 「우선」(`_on_performance`)이 작업 카드와 똑같이 정한다."""
        it.pop("precheck", None)
        it.pop("precheck_bf", None)
        it.pop("precheck_combat_warn", None)
        if settings.get("queue_precheck", True) is False:
            self._log(it, "상태 점검 생략 (설정 queue_precheck=false)")
            return True
        a = self._read_activity(it)   # 연결 오류면 점검을 건너뛰지 않고 그 오류로 멈춘다 (기존 규칙)
        if a is None:
            return False
        self._log(it, a["text"])
        for w in a.get("warnings", []):
            self._log(it, "주의: " + w)
        if not a["ok"]:
            self._fail(it, a["error"], f"{ERROR_KO.get(a['error'], '')} ({a['field']}={a['value']})")
            return False
        if play:
            if a.get("dead") or a.get("reviving"):
                fld = "IsDead" if a.get("dead") else "IsReviving"
                self._fail(it, "precheck_dead", f"{ERROR_KO['precheck_dead']} ({fld}=True)")
                return False
            if a.get("mounted"):
                fn = self.play_mount_ok
                ok = False
                if fn is not None:
                    try:
                        ok = bool(fn())
                    except Exception as e:
                        self._log(it, f"주의: 폴리오의 탈것 설정을 읽지 못했습니다 ({type(e).__name__}) — 내릴 수 없는 것으로 봅니다")
                if not ok:
                    self._fail(it, "precheck_mounted", f"{ERROR_KO['precheck_mounted']} (Mode.MountPartState=Mounted)")
                    return False
                self._log(it, "탈것 탑승 중 — 폴리오 설정 「탈것은 제작으로 내리기」가 켜져 있어 폴리오가 내린 뒤 틉니다 (날개 5)")
        if not self._on_performance(it, a, settings):
            return False
        it["precheck"] = "ok"
        if a.get("battlefield"):
            it["precheck_bf"] = True   # 실패하면 _fail 이 「전장 안에서 실행」 을 덧붙인다
        if a.get("combat"):
            it["precheck_combat_warn"] = True   # stopped_by_user 로 끊기면 _fail 이 「전투 중 출발」 을 덧붙인다
        return True

    # 「작업 우선」인데 지금은 못 끊는 자리(합주·인사말)에서 얼마나 기다려 주는가. (server._perf_yield 도 이 값을 쓴다.)
    PERF_WAIT_MAX = 300.0     # 끝을 **모를 때**의 상한(초)
    PERF_WAIT_CEIL = 7200.0   # 알 때도 걸어 두는 천장(초) — 러너가 영영 물려 있지 않게
    PERF_POLL = 3.0           # 다시 물어보는 간격(초) — 조회라 값이 안 든다

    def _on_performance(self, it: dict, a: dict, settings: dict) -> bool:
        """연주 중일 때 무엇을 할지 → 계속해도 되면 True, 이 항목을 끝냈으면 False.

        **점검을 통과한 뒤에만 부른다.** 연주를 멈추는 명령을 보내기 **전에**
        지금 채집·가공이 가능한 상태인지 확인하고, 가능할 때만
        설정된 옵션대로 보낸다. 순서가 뒤집히면 **음악을 끊어 놓고 「사실 채집이
        안 되는 상태였다」**가 된다 — 제일 나쁜 결과다.

        `Performance` 는 위 점검이 이미 부른 `get_activity` 한 번에 같이 들어 있다.
        **호출이 늘지 않는다.**

        고를 수 있는 것이 셋이다 (설정 `queue_on_performance`):

        | | 뜻 | 연주 중이면 |
        |---|---|---|
        | `music` | **완전한 연주 우선** | 보내지 않고 **연주 대기**(hold) — 연주가 끝나면 이 카드부터 이어 간다 |
        | `song`  | **이 곡 끝나면 양보** | 연주 쪽에 부탁하고 경계에서 넘겨받는다 |
        | `work`  | 작업 우선 — 지금 바로 | 연주를 끊는다 (합주·인사말이면 그것만 기다린다) |

        `music` 은 세 번 바뀌었다. 처음 「기다리기」 → 「연주 중이면 큐가 **시작조차** 하지 않는다」(거절·정지)
        → 다시 **기다리되 보이게**(연주 대기 카드, `_hold_for_music`). 거절로 두니 폰에서 ▶ 시작이
        「아무 일도 안 하는」 것으로 보였다. 게임은 연주를 지켜 주지
        않는다 — 연주 중 execute_altering 은 거절 없이 날개 5 를 빼고 연주를 끊는다(실측) — 그래서 기다리는 동안
        아무것도 보내지 않는 규칙은 그대로다.

        가운데(`song`)는 나중에 더했다. 처음엔 `music`/`work` 둘뿐이었는데,
        **「수령만 예외로 빼자」**는 안이 두 군데서 깨졌다:

        1. 수령은 짧지 않다. 카탈로그 원문 — `complete_altering_work` =
           「Collect all completed altering works at one facility, **including travel
           to it**」, 「This call blocks through **travel**, collection, and reward
           handling」. **캐릭터가 시설까지 걸어간다.** 연주 중에 자리를 뜬다.
        2. 명령 하나를 예외로 빼면 **그 목록은 줄지 않는다.** 「수령은 짧으니까」가 서면
           다음엔 「이 채집은 30초짜리니까」가 선다. 그리고 예외의 근거가 틀렸을 때
           아무도 다시 안 본다.

        그래서 **명령별 예외가 아니라 사람이 고르는 값**으로 풀었다. 셋 다 「연주」라는
        한 가지만 보고 판단하므로 명령별 표를 들고 있을 필요가 없다."""
        if self._yield_held:
            # **이미 양보받았다.** 항목마다 다시 부탁하지 않는다 — 그쪽은 경계에서 멈춰
            # 선 채로 기다리고 있고, 돌려주는 것은 보드가 끝날 때 한 번이다.
            return True
        perf = a.get("perf") or {}
        # **연주 중이 아니어도 물어본다.** 인사말이 나간 뒤 연주가 시작되기 전(`lead`)에는
        # `IsPlaying` 이 거짓인데, 그때 끼어들면 「한 곡 들려드릴게요」라고 채팅에 말해 놓고
        # 아무것도 안 치는 꼴이 된다. **게임 조회로는 안 보이는 자리**라 물어봐야 한다.
        gate = self._ask_perf_gate(it) if (self.perf_gate is not None or perf.get("playing")) else None
        may_stop = True if gate is None else bool(gate.get("may_stop", True))
        if not perf.get("playing") and may_stop:
            return True

        what = (perf.get("title") or "").strip() or (gate or {}).get("title") or "제목 미상"
        mode = str(settings.get("queue_on_performance") or DEFAULT_ON_PERFORMANCE).lower()
        if mode not in ("music", "song", "work"):
            mode = DEFAULT_ON_PERFORMANCE
        now = time.time()

        if mode == "song":
            # **양보는 「읽는 값」이 아니라 「요청」이다.**
            #
            # 처음엔 곡 경계까지 남은 시간을 읽고 그 순간에 `stop_action` 을 쏘게 짰다.
            # 그것은 경주다: 곡이 끝나고 다음 곡이 시작되기까지
            # 창이 **`gap_sec` = 기본 2초**뿐이다. 그 안에 못 들어가면 **다음 곡을 중간에
            # 자른다** — 하려던 것과 정반대다. 게다가 우리 실행 명령은 **이동을 포함해
            # 블로킹**한다(`complete_altering_work`). 2초 창에 들어가는 종류가 아니다.
            #
            # 그래서 미리 말해 두고 **그쪽이 경계에서 멈춰 서서 기다리게** 한다. 그러면
            # 우리가 9분이 걸리든 상관이 없어진다. 그쪽에는 이미 그 기계가 있다 —
            # 합주가 끝나면 다음 곡으로 안 넘어가고 사람에게 물으며 기다리는 갈래.
            return self._yield_after_song(it, what, perf)
        if mode == "music":
            return self._hold_mid_run(it, settings, perf)

        # work — 지금 끼어든다. 못 끊는 자리(합주·인사말)면 그것이 걷힐 때까지만 기다린다.
        takeover = now
        deadline = min(takeover + self.PERF_WAIT_MAX, now + self.PERF_WAIT_CEIL)
        if may_stop:
            return self._stop_performance(it, what)   # 바로 끊는다 — 기다림이 없으니 카드도 없다
        self._log(it, self._wait_line(mode, what, perf, gate))
        # 못 끊는 자리를 기다리는 동안 **보이게** — 같은 연주 대기 카드, 갈래 `work` (「연주 중 — 합주/인사말이 끝나면 시작」)
        self._hold_set(it, "work", perf, what)
        try:
            while True:
                if self._stop.is_set():
                    return False
                self._stop.wait(self.PERF_POLL)
                if self._stop.is_set():
                    return False
                a2 = self._read_activity(it)
                if a2 is None:
                    return False                       # 연결 오류 — _read_activity 가 이미 끝냈다
                p2 = a2.get("perf") or {}
                g2 = self._ask_perf_gate(it, quiet=True)
                ok_to_stop = True if g2 is None else bool(g2.get("may_stop", True))
                if not p2.get("playing") and ok_to_stop:
                    self._hold_end(it, "연주가 끝났습니다 — 진행합니다")
                    if not a2["ok"]:                   # 기다리는 사이에 상태가 바뀌었을 수 있다
                        self._fail(it, a2["error"], f"{ERROR_KO.get(a2['error'], '')} ({a2['field']}={a2['value']})")
                        return False
                    return True
                if time.time() >= takeover and ok_to_stop:
                    self._hold_end(it, "합주·인사말이 걷혔습니다 — 진행합니다")
                    if not a2["ok"]:
                        self._fail(it, a2["error"], f"{ERROR_KO.get(a2['error'], '')} ({a2['field']}={a2['value']})")
                        return False
                    return self._stop_performance(it, (p2.get("title") or "").strip() or what)
                if time.time() >= deadline:
                    self._fail(it, "precheck_performance",
                               f"연주가 {int(deadline - now)}초 동안 이어집니다 — 기다리기를 그만둡니다")
                    return False
        finally:
            self.hold = None   # 어느 길로 나가든 카드는 걷힌다 (■ 정지·오류 포함)

    def _wait_line(self, mode: str, what: str, perf: dict, gate) -> str:
        """「작업 우선」인데 지금은 못 끊을 때(합주 중이거나 인사말이 나가는 중) 남기는 한 줄.
        `song`·`music` 은 여기 안 온다 — 부탁하고 답을 받거나(`_yield_after_song`), 연주 대기한다(`_hold_mid_run`)."""
        why = (gate or {}).get("why")
        return f"연주 중({what}) — {why or '지금은 멈출 수 없다고 합니다'}. 「작업 우선」이지만 끊지 않고 기다립니다"

    # ── 연주 대기 (hold) ──
    # 폴리오 엔진이 재 둔 지금 연주 상태를 읽는 자리 (server: folio_engine().work_now). CLI 를 부르지 않는다.
    # 돌려줄 것: {"ok", "age", "busy", "kind": music|song|game, "title", "elapsed", "left", "endless"}
    # None 이면 물을 데가 없다 — 그때는 get_activity 를 직접 읽는다 (HOLD_POLL_CLI 간격, 읽기라 날개 0).
    perf_now = None
    HOLD_POLL = HOLD_POLL
    HOLD_POLL_CLI = HOLD_POLL_CLI
    COLLECT_RETRY_WAIT = COLLECT_RETRY_WAIT   # 검사가 줄여 끼운다 — 「수령분 없음」 뒤 다시 읽기 전의 가라앉힘
    HOLD_SETTLE = HOLD_SETTLE
    HOLD_STALE = HOLD_STALE
    PLAY_COUNT_MAX = PLAY_COUNT_MAX   # 연주 카드 회차 상한 — server._play_start 가 읽는다

    def _hold_mid_run(self, it: dict, settings: dict, perf: dict) -> bool:
        """「완전한 연주 우선」인데 도는 중에 연주가 시작됐다 — **보내지 않고, 연주도 건드리지 않고** 이 카드 앞에서
        연주 대기한다. 끝나면 상태를 다시 읽고 이 카드부터 이어 간다 (스스로 — ▶ 시작을 다시 누르지 않아도 된다).
        ■ 정지면 이 카드는 stopped(대기 열), 사유 user. 날개 0.
        (예전에는 여기서 체인을 PERF_STOP_REASON 으로 세웠다 — 이제 안 낸다.)"""
        if not self._hold_for_music(it, settings, perf):
            if it.get("status") == "running":
                it["status"] = "stopped"
                self._log(it, "사용자 정지 (연주 대기 중)")
            return False
        a2 = self._read_activity(it)       # 기다리는 사이 상태가 바뀌었을 수 있다 (사망·대화·던전…)
        if a2 is None:
            return False
        if not a2["ok"]:
            self._fail(it, a2["error"], f"{ERROR_KO.get(a2['error'], '')} ({a2['field']}={a2['value']})")
            return False
        if (a2.get("perf") or {}).get("playing"):
            return self._on_performance(it, a2, settings)   # 끝나자마자 다른 연주가 시작됐다 — 다시 기다린다
        return True

    def _hold_for_music(self, it, settings: dict, perf0) -> bool:
        """연주가 끝나기를 **보이게** 기다린다 → 끝났으면 True, ■ 정지면 False. 아무것도 보내지 않는다.

        기다리는 동안 `self.hold` 가 카드다 (GET /api/queue.hold — 보드는 작업 중 칸 맨 위에, 밴드는 「연주 끝나면 시작」).
        무엇을 얼마나 자주 읽는가:
          · 폴리오가 붙어 있으면(perf_now) 그쪽이 1초마다 재 둔 값을 **메모리에서** HOLD_POLL(2초)마다 본다 — CLI 호출 0.
          · 없으면(검사·미연결) get_activity 를 HOLD_POLL_CLI(5초)마다 직접 읽는다 — 읽기라 날개 0.
        「끝났다」는 **HOLD_SETTLE(4초) 동안 조용해야** 친다 — 전체 재생의 곡 사이 틈(gap_sec 기본 2초)에 끼어들지 않으려고.
        폴리오 쪽은 틈·인사말·탈것 대기까지 busy 로 답하므로(work_now) 그 사이에도 시작하지 않는다.
        반복 재생(끝이 없다)이어도 기다린다 — 카드가 「끝을 알 수 없음」이라고 말하고, 사람이 ■ 정지하거나 우선을 바꾼다."""
        since = time.time()
        quiet_at = None
        last_busy = True
        v = self._hold_view(perf0, last_busy)
        self._hold_note(it, f"연주 대기 — {hold_text(v['kind'], v['left'], v['endless'])}"
                            + (f" · {v['title']}" if v.get("title") else "") + " (보내지 않음 · 날개 0)")
        while True:
            if self._stop.is_set():
                self.hold = None
                self._hold_note(it, "연주 대기를 거뒀습니다 (■ 정지)")
                return False
            last_busy = bool(v["busy"])
            now = time.time()
            el = float(v.get("elapsed") or 0.0)
            self.hold = {"kind": v["kind"], "title": v.get("title") or "", "elapsed": el, "started": now - el,
                         "left": v.get("left"), "endless": bool(v.get("endless")), "since": since,
                         "text": hold_text(v["kind"], v.get("left"), bool(v.get("endless"))),
                         "card": it["id"] if it else None, "src": v.get("src")}
            if not last_busy:
                quiet_at = quiet_at or now
                if now - quiet_at >= self.HOLD_SETTLE:
                    self._hold_end(it, "연주가 끝났습니다 — 시작합니다")
                    return True
            else:
                quiet_at = None
            self._stop.wait(self.HOLD_POLL if v.get("src") == "folio" else self.HOLD_POLL_CLI)
            v = self._hold_view(None, last_busy)

    def _hold_note(self, it, msg: str, kind: str = "hold") -> None:
        """연주 대기의 한 줄 — 카드가 있으면 그 카드 로그에, 없으면(첫 카드 앞) 보드 로그에. 회신 기록에도 남긴다."""
        if it is not None:
            self._log(it, msg)
        else:
            self.say(f"[queue] {msg}")
        self._event(kind, it["id"] if it else None, msg)

    def _hold_set(self, it, kind: str, perf0, what: str = "") -> None:
        """연주 대기 카드를 세운다 — `music` 밖의 갈래(`song`·`work`)용. 모양(제목·경과)은 폴리오(perf_now)가 답하면 그것, 아니면
        점검이 이미 읽어 온 연주(perf0)에서 — 여기서 게임에 다시 묻지 않는다. `left`·`endless` 는 이 갈래엔 뜻이 없다:
        `song` 은 전체 재생이어도 넘김이 **다음 곡 경계**라 「이 곡」이고, `work` 는 합주·인사말이 걷히는 때다."""
        v = self._hold_view(perf0, True)
        now = time.time()
        el = float(v.get("elapsed") or 0.0)
        self.hold = {"kind": kind, "title": v.get("title") or (what if what != "제목 미상" else "") or "", "elapsed": el,
                     "started": now - el, "left": None, "endless": False, "since": now, "text": hold_text(kind),
                     "card": it["id"] if it else None, "src": v.get("src")}
        # 회신 기록에도 「연주 대기」 한 줄 — 보드의 실행 줄 시계(board.js runStart)가 **이 줄 뒤의 start** 부터 세게
        # (대기 전의 start 까지 거슬러 올라가면 기다린 시간이 작업 시간으로 보인다 — 실측 「실행 중 00:58 / 카드 00:10」)
        self._event("hold", it["id"] if it else None,
                    f"연주 대기 — {hold_text(kind)}" + (f" · {self.hold['title']}" if self.hold["title"] else ""))

    def _hold_end(self, it, msg: str) -> None:
        """연주 대기가 끝나 진짜로 시작한다 — 카드를 걷고, 도는 카드가 있으면 **경과를 여기서부터** 센다.
        보드의 경과(board.js lastStart·runStart)는 마지막 `start` 회신의 시각이라, 기다린 시간이 작업 시간으로 보이지 않게
        `start` 를 한 번 더 적는다 (첫 카드 앞의 대기는 카드가 아직 없어 `_run_card` 의 start 가 그 일을 한다)."""
        h = self.hold
        self.hold = None
        if it is None:
            self._hold_note(None, msg)
            return
        waited = int(max(0.0, time.time() - float((h or {}).get("since") or time.time())))
        self._hold_note(it, f"{msg} — {it.get('name') or ''} 시작 (연주 대기 {waited}초)", kind="start")

    def _hold_view(self, perf0, last_busy: bool) -> dict:
        """지금 연주의 모양 한 장 — {"kind", "title", "elapsed", "left", "endless", "busy", "src"}.
        폴리오(perf_now)가 답하면 그것(src=folio), 아니면 get_activity 를 직접 읽는다(src=cli, 모양은 늘 game).
        읽기가 실패하면 **직전 busy 를 유지한다** — 모르는 채로 시작하지 않는다."""
        fn = self.perf_now
        n = None
        if fn is not None:
            try:
                n = fn()
            except Exception:
                n = None
        if isinstance(n, dict) and n.get("ok") and float(n.get("age") or 0) <= self.HOLD_STALE:
            kind = n.get("kind") if n.get("kind") in HOLD_KINDS else "game"
            left = n.get("left")
            out = {"kind": kind, "title": str(n.get("title") or ""), "elapsed": max(0.0, float(n.get("elapsed") or 0)),
                   "left": int(left) if isinstance(left, (int, float)) else None,
                   "endless": bool(n.get("endless")), "busy": bool(n.get("busy")), "src": "folio"}
            for k in ("own", "solo", "soloEnd", "error"):   # 「연주」 카드가 제 부탁의 끝을 가르는 값 (_play_wait) — 있을 때만
                if k in n:
                    out[k] = n[k]
            return out
        p = perf0
        if p is None:
            try:
                r = self._call("get_activity", "", READ_TIMEOUT)
                p = perf_of(r.body) if r.ok else None
            except Exception:
                p = None
        if p is None:
            return {"kind": "game", "title": "", "elapsed": 0.0, "left": None, "endless": False,
                    "busy": last_busy, "src": "cli"}
        el = 0.0
        if p.get("total") is not None and p.get("remaining") is not None:
            el = max(0.0, float(p["total"]) - float(p["remaining"]))
        return {"kind": "game", "title": str(p.get("title") or ""), "elapsed": el, "left": None,
                "endless": bool(p.get("loop")), "busy": bool(p.get("playing")), "src": "cli"}

    # 「다음 곡 경계에서 양보해 주세요」를 부르는 자리와, 끝나고 「부탁을 거둡니다 — 멈춘 채로 두세요」를
    # 부르는 자리. 통합할 때 꽂는다. 없으면 `song` 은 성립하지 않는다 — 대신 **아는 척하지
    # 않고 그 사실을 말한다.**
    # `perf_resume`(이어서 재생)은 **보드 끝에서 부르지 않는다** — server._perf_yield 가 경계에 서기 전에
    # 부탁을 거둘 때(정지·시한)만 그쪽에서 직접 쓴다. 작업이 끝났다고 연주를 다시 틀면 안 된다.
    perf_yield = None        # () -> True | {"ok": bool, "why": str}
    perf_resume = None       # () -> None   (server 가 꽂아 두지만 큐는 부르지 않는다)
    perf_release = None      # () -> None   양보 상태를 걷는다 — 이어서 틀지 않고 폴리오를 「작업에 양보해 멈춤」으로 남긴다

    def _yield_after_song(self, it: dict, what: str, perf=None) -> bool:
        """「이 곡 끝나면 양보」 — 부탁하고 곡 경계까지 **보이게** 기다린다 (연주 대기 카드, 갈래 `song`).
        부탁 자체가 기다림이다 (server._perf_yield 가 경계에 설 때까지 돌아오지 않는다) — 그래서 부르기 전에 카드를 세우고
        돌아오면 걷는다. 실측: 카드가 없을 때는 이 57초 동안 화면이 「실행 중 · 00:07」로만 보였다."""
        fn = self.perf_yield
        if fn is None:
            self._fail(it, "precheck_performance",
                       f"연주 중({what}) — 「이 곡 끝나면 양보」는 연주 쪽에 부탁해야 하는데 "
                       f"부탁할 데가 없습니다 (연주 쪽 미연결). 「연주 계속」이나 「제작 우선」을 고르세요")
            return False
        self._log(it, f"연주 중({what}) — 「이 곡 끝나면 양보」를 부탁하고 기다립니다")
        self._hold_set(it, "song", perf, what)
        # **부르기 전에** 적는다 — 부탁이 나간 뒤 어느 길로 끝나든(정지·거절·예외) 보드 끝에서 양보 상태를 걷는다.
        # 전에는 성공했을 때만 적어서, 경계에 선 직후 ■ 정지를 누르면 폴리오가 「멈춰 선」 채로 남았다.
        self._yielded = True
        try:
            r = fn()
        except Exception as e:
            self.hold = None
            self._fail(it, "precheck_performance", f"양보를 부탁하지 못했습니다 ({type(e).__name__})")
            return False
        if self._stop.is_set():
            # 부탁을 기다리는 사이 사람이 ■ 정지를 눌렀다 (server._perf_yield 는 우리 정지 플래그를 보고 돌아온다).
            # 이건 **정지**다 — 치명 오류(precheck_performance)로 적으면 그룹이 실패 열로 간다.
            self.hold = None
            it["status"] = "stopped"
            self._log(it, "사용자 정지 (양보를 기다리던 중)")
            return False
        ok = r.get("ok", True) if isinstance(r, dict) else bool(r)
        if not ok and isinstance(r, dict) and r.get("expired"):
            # **시한이 지났다** (곡 길이 + 30초, 또는 길이를 모르면 PERF_WAIT_MAX+30초) — 경계가 안 왔다. 연주 쪽은 이미 부탁을 거뒀다
            # (work_release). 영영 기다리지 않는다: 「이 곡」은 제 길이만큼은 다 울렸으니 **지금 멈추고 진행한다** (「작업 우선」과 같은
            # 길 — 아직 치고 있으면 stop_action 한 번, 이미 멈췄으면 그대로). 실측: 곡 하나 재생에 부탁했다가 2분 12초를
            # 「이 곡이 끝나면 시작」에 서 있었다 (원인은 엔진 — 곡 하나 재생이 경계를 안 지났다. 이제 지난다. 이 갈래는 그 다음 안전망이다).
            self.hold = None
            why = r.get("why") or "까닭 미상"
            self._log(it, f"양보를 받지 못함 — {why}. 부탁을 거두고 연주를 멈춘 뒤 진행합니다")
            a2 = self._read_activity(it)
            if a2 is None:
                return False
            if not a2["ok"]:
                self._fail(it, a2["error"], f"{ERROR_KO.get(a2['error'], '')} ({a2['field']}={a2['value']})")
                return False
            if (a2.get("perf") or {}).get("playing"):
                return self._stop_performance(it, (a2["perf"].get("title") or "").strip() or what)
            return True
        if not ok:
            self.hold = None
            why = r.get("why") if isinstance(r, dict) else ""
            self._fail(it, "precheck_performance", f"양보를 받지 못했습니다 — {why or '까닭 미상'}")
            return False
        self._yield_held = True
        self._hold_end(it, "양보받았습니다 — 진행합니다" + (f" ({r.get('why')})" if isinstance(r, dict) and r.get("why") else ""))
        return True

    def _release_performance(self) -> None:
        """보드가 끝났다 — 양보를 부탁했으면 **그 부탁을 거둔다. 이어서 틀지 않는다.**

        전에는 여기서 `perf_resume`(work_resume)을 불러 폴리오가 멈춘 자리의 다음 곡을 틀었다 — 작업이 끝나면
        연주가 다시 시작되는 버그였다.
        남던 것은 폴리오 엔진의 `yield_at`(부탁받은 시각)·`held`(경계에서 멈춰 섬) — `work_resume` 이 그걸 지우면서 **다음 곡을
        틀었다.** 이제는 `perf_release`(engine.work_release)로 그 둘을 지우고 대기열을 「작업에 양보해 멈춤」(■)으로 남긴다.
        연주를 다시 트는 것은 사람이 ▶ 를 누를 때뿐이다."""
        if not self._yielded:
            return
        self._yielded = False
        self._yield_held = False
        fn = self.perf_release
        if fn is None:
            self.say("[queue] 양보 상태를 걷을 데가 없습니다 (연주 쪽 미연결)")
            return
        try:
            fn()
            self.say("[queue] 연주에 부탁한 양보를 거뒀습니다 — 이어서 틀지 않습니다 (▶ 는 사람이)")
        except Exception as e:
            self.say(f"[queue] 양보를 거두지 못했습니다 ({type(e).__name__})")

    def _stop_performance(self, it: dict, what: str) -> bool:
        """연주를 멈추고 진행한다. **여기 오기 전에 「멈춰도 되는가」가 이미 확인돼 있어야 한다.**"""
        self._log(it, f"연주 중({what}) — 연주를 멈추고 진행합니다")
        r = self._call("stop_action", "", READ_TIMEOUT)
        # **「멈출 게 없다」는 실패가 아니다.** 조회와 정지 사이에 곡이 끝났을 수 있다.
        # (`invalid_state` 는 카탈로그의 stop_action 오류 목록에 없다 — 목록이 전수가
        #  아님이 이미 드러났고, 여기가 바로 그 자리다.)
        if not r.ok and r.error != "invalid_state":
            self._fail(it, r.error or "cli_error", r.message or "stop_action 실패")
            return False
        a2 = self._read_activity(it)
        if a2 is not None and (a2.get("perf") or {}).get("playing"):
            # 안 멈췄다고 보고한다. 그래도 진행한다 — 실행 명령이 같은 슬롯을 갈아치우므로
            # 어차피 연주는 끝난다. **다만 아는 척은 하지 않는다.**
            self._log(it, "주의: 정지를 보냈는데 아직 연주 중으로 보고됩니다 — 그대로 진행합니다")
        return True

    # 연주 쪽에 물어보는 자리. 통합할 때 여기에 함수를 꽂는다 (모비폴리오 `/api/now`).
    # 돌려줄 것: {"may_stop": bool, "ends_in": 초|None, "why": "사람이 읽을 한 줄"}
    # None 이면 **물어볼 데가 없다**는 뜻이고, 그때는 게임이 준 값만 가지고 판단한다.
    perf_gate = None

    def _ask_perf_gate(self, it: dict, quiet: bool = False):
        """**「지금 멈춰도 됩니까」를 연주 쪽에 묻는다.**

        게임의 `get_activity` 로는 **합주 중인지 알 수 없다.** 모비폴리오는 주변에서
        `StartAt` 이 같은 사람들을 보고 합주를 알아보는데, 그 정보는 우리 조회에 없다.
        그래서 이건 **추론으로 메울 수 없는 자리**이고, 물어보는 수밖에 없다.

        아직 안 꽂혀 있으면(통합 전) None 을 준다. **그때는 합주 여부를 모른 채
        움직인다는 사실을 기록에 남긴다** — 모르는 것을 아는 척하지 않는다."""
        fn = self.perf_gate
        if fn is None:
            if not quiet:   # 기다리는 동안 같은 줄을 매초 찍지 않는다
                self._log(it, "주의: 합주 중인지 확인할 데가 없습니다 (연주 쪽 미연결) — 게임 상태만 보고 판단합니다")
            return None
        try:
            g = fn()
        except Exception as e:
            if not quiet:
                self._log(it, f"주의: 연주 쪽에 물어보지 못했습니다 ({type(e).__name__}) — 게임 상태만 보고 판단합니다")
            return None
        return g if isinstance(g, dict) else None

    def _read_activity(self, it: dict):
        """조회 → activity_summary. 연결 계열이면 짧게 다시 읽고(_read), 그래도 안 되면 그 항목을 끝내고 None 을 준다.
        **값이 안 드는 호출이다** — 읽기는 도는 동작을 갈아치우지 않는다."""
        r = self._read(it, "get_activity", "")
        if not r.ok:
            self._read_failed(it, r, "get_activity 실패")
            return None
        return activity_summary(r.body)

    def _nap(self, sec: float) -> bool:
        """기다린다 — ■ 정지가 들어오면 곧바로 깬다. 정지로 깼으면 True."""
        return self._stop.wait(max(0.0, float(sec)) * wait_scale())

    def _read(self, it, command: str, body):
        """읽기 호출(날개 0) — 연결 계열 실패는 READ_RETRY_WAITS 만큼 다시 읽고, 로딩 중이면 기다린다.

        · disconnected·timeout·game_off·cli_disconnected → 3초, 6초 쉬고 다시 (기본 2회). 그래도 안 되면 마지막 답을 준다.
        · loading(재접속·캐릭터 선택) → LOADING_POLL 초마다 다시 읽는다. LOADING_MAX 초를 넘으면 오류 loading 으로 준다.
        · ■ 정지가 들어오면 기다리지 않고 마지막 답을 준다.
        실행 명령(execute_*)에는 쓰지 않는다 — 다시 보내면 날개가 또 나간다.
        마지막 답에 `retries`(다시 읽은 횟수)를 달아 준다 — 멈춤 안내가 「재시도 n회 후 멈춤」을 적는다."""
        r = self._call(command, body, READ_TIMEOUT)
        tries = 0
        polls = 0
        max_polls = max(1, int(math.ceil(LOADING_MAX / max(0.1, LOADING_POLL))))
        while not r.ok and not self._stop.is_set():
            if is_loading(r):
                if polls >= max_polls:
                    r.error = "loading"
                    r.message = ERROR_KO["loading"]
                    break
                if polls == 0 and it is not None:
                    self._log(it, f"게임이 로딩 중입니다(재접속·캐릭터 선택) — {LOADING_POLL:g}초마다 다시 보고 "
                                  f"최대 {int(LOADING_MAX // 60)}분 기다립니다 ({command})")
                polls += 1
                if self._nap(LOADING_POLL):
                    break
                r = self._call(command, body, READ_TIMEOUT)
                if r.ok and it is not None:
                    self._log(it, f"로딩이 끝났습니다 — 이어갑니다 ({polls * LOADING_POLL:g}초 기다림)")
                continue
            if r.error in READ_RETRY_ERRORS and tries < len(READ_RETRY_WAITS):
                w = READ_RETRY_WAITS[tries]
                tries += 1
                if it is not None:
                    self._log(it, f"{command} 읽기 실패({r.error}) — {w:g}초 뒤 다시 읽습니다 ({tries}/{len(READ_RETRY_WAITS)})")
                if self._nap(w):
                    break
                r = self._call(command, body, READ_TIMEOUT)
                if r.ok and it is not None:
                    self._log(it, f"{command} 다시 읽기 성공 ({tries}회째)")
                continue
            break
        try:
            r.retries = tries
        except Exception:
            pass
        return r

    def _read_failed(self, it: dict, r, fallback: str = "") -> None:
        """읽기가 끝내 실패했을 때 그 항목을 끝낸다. 연결 계열은 「재시도 n회 후 멈춤」 안내로 (코드는 그대로)."""
        err = r.error or "cli_error"
        if err in READ_RETRY_ERRORS:
            self._fail(it, err, conn_stop_msg(err, getattr(r, "retries", 0), raw=r.message or ""))
        else:
            self._fail(it, err, r.message or ERROR_KO.get(err, "") or fallback)

    def _run_item(self, it: dict, settings: dict, wait_alter: bool = False) -> None:
        try:
            if it["type"] == "notify":          # 알림 — 게임 상태와 무관하다 (CLI 를 부르지 않는다)
                self._run_free(it)
                return
            if it["type"] == "play":            # 연주 — 실행 명령은 없지만 **상태 점검은 작업 카드와 같이** (탈것·사망까지, 읽기 1회 · 날개 0)
                if self._precheck(it, settings, play=True):
                    self._run_free(it)
                return
            if not self._precheck(it, settings):
                return
            if it["type"] == "gather":
                self._run_gather(it, settings)
            elif it["type"] == "craft":
                self._run_craft(it)
            elif it["type"] == "collect":
                self._run_collect(it)
            else:
                self._run_alter(it, settings, wait_alter)
        except Exception as e:   # 러너가 죽지 않게 — 무엇이든 오류로 기록한다 (internal 은 FATAL: 원인을 모르니 멈춘다)
            self._fail(it, "internal", f"{type(e).__name__}: {e}")

    def _exec_failed(self, it: dict, r, body: dict) -> None:
        """실행 응답 실패 처리 공통: 정지 중 canceled 면 stopped, 아니면 error(+blocked kind / invalid_count maxCount)."""
        if self._stop.is_set() and r.error in ("canceled", "timeout"):
            it["status"] = "stopped"; self._log(it, "사용자 정지 (행동 취소됨)"); return
        if r.error == "blocked" and body.get("kind") == "unknown_modal" and self._alter_recent:
            # 가공을 건 바로 뒤에 막혔다 — 「가공 대기열이 가득 찼습니다」 창으로 본다 (실측: 그 창은 CLI 로 안 닫히고
            # 떠 있는 동안 실행 명령이 전부 막힌다). 자동으로 닫지 않고 보드를 세워 사람에게 닫아 달라고 한다
            self._alter_full_stop(it, r=r); return
        extra = ""
        if r.error == "blocked" and body.get("kind"):
            extra = f" kind={body.get('kind')}"
        if r.error == "blocked" and it.get("precheck") == "ok":
            self._log(it, f"점검은 통과했는데 CLI 가 blocked(kind={body.get('kind')}) — 게임 상태 보고 불일치 가능. "
                          "게임 화면을 확인하고(부활 창·대화 창 등) 「재시도」")
        if r.error == "invalid_count" and body.get("maxCount") is not None:
            extra = f" (시설 상한 maxCount={body.get('maxCount')})"
        msg = r.message or ERROR_KO.get(r.error or "", "")
        if r.error in READ_RETRY_ERRORS:
            # 실행 명령은 다시 보내지 않는다 (날개 5) — 그 사실을 안내에 적는다. 코드는 그대로
            msg = conn_stop_msg(r.error, 0, action=True, raw=r.message or "")
        elif is_loading(r):
            msg = "게임이 로딩 중이라(재접속·캐릭터 선택) 실행 명령이 거절됐습니다 — 게임에 들어간 뒤 ▶ 시작" + (
                f" · 원문: {r.message}" if r.message else "")
            r.error = "loading"
        kind_ko = BLOCKED_KIND_KO.get(body.get("kind")) if r.error == "blocked" else None
        if kind_ko:
            # 우리 안내를 앞에 — 원문(영문)은 뒤에 남겨 둔다 (무엇이 왔는지 지우지 않는다)
            msg = kind_ko + (f" · 원문: {r.message}" if r.message else "")
        self._fail(it, r.error or "cli_error", msg + extra)

    # 채집: 회마다 [도구 확인 → 무게 확인 → 실행 → 가방 재확인]
    # ── 연주·알림 카드 ──
    # 갈고리 셋 — server.py 가 꽂는다 (perf_yield·perf_resume 와 같은 자리). 검사는 가짜를 꽂는다 (실제 CLI 를 부르지 않는다).
    play_start = None    # (card: dict) -> {"ok": bool, "error"?: str, "message"?: str}  — 폴리오 엔진에 연주 시작을 부탁
    play_stop = None     # (card: dict) -> {"ok": bool, "stopped": bool, "why"?: str}  — ■ 정지: **우리가 부탁한 연주만** 멈춘다 (남의 것이면 안 건드린다)
    notify = None        # (notice: dict) -> None  — 알림 하나를 밖으로 (PC 밴드·로그). 화면은 state().notices 로 따로 받는다
    PLAY_END_GRACE = PLAY_END_GRACE

    def _push_notice(self, text: str, sound: bool, kind: str, card: str | None = None) -> dict:
        """알림 하나를 남기고(state().notices · 회신 기록) 갈고리에 넘긴다. 갈고리가 죽어도 큐는 계속한다."""
        with self.lock:
            self._notice_seq += 1
            n = {"id": self._notice_seq, "t": time.time(), "text": text, "sound": bool(sound), "kind": kind, "card": card}
            self.notices.append(n)
            del self.notices[:-NOTICES_KEEP]
            self._event("notice", card, text)
        fn = self.notify
        if fn is not None:
            try:
                fn(dict(n))
            except Exception as e:
                self.say(f"[queue] 알림 갈고리 실패: {type(e).__name__}: {e}")
        return n

    def _run_free(self, it: dict) -> None:
        """연주·알림 카드 한 장. **CLI 를 부르지 않고 날개 0** — 장부에는 `play`/`notify` 로 남긴다 (ledger 가 날개 호출로 세지 않는다)."""
        if self._stopped(it):
            return
        p = it["progress"]
        if it["type"] == "notify":
            n = self._push_notice(_s(it.get("text")) or NOTIFY_DEFAULT_TEXT, it.get("sound") is not False, "card", it["id"])
            p["done"] = 1
            ledger.add("notify", it.get("name") or "", 0, 0, True)
            self._done(it, f"알림 보냄 「{n['text']}」")
            return
        fn = self.play_start
        if fn is None:
            ledger.add("play", it.get("name") or "", 0, 0, False, "folio_unavailable")
            self._fail(it, "folio_unavailable", "연주 엔진(폴리오)에 닿지 못했습니다")
            return
        if it.get("mode") == PLAY_MODE_LEGACY:   # 옛 저장본의 「재생목록 전체」 카드 — 이제는 그룹으로 담는다
            ledger.add("play", it.get("name") or "", 0, 0, False, "list_gone")
            self._fail(it, "list_gone", "재생목록 카드는 이제 그룹으로 담습니다 — 이 카드를 빼고 서랍 「연주」에서 재생목록을 다시 담으세요")
            return
        times = f" · {_n(it.get('count'), 1)}회 치고 멈춤" if it.get("mode") == "song" else ""
        self._log(it, f"폴리오에 연주 부탁: {PLAY_MODE_KO.get(it.get('mode'), it.get('mode'))} · {it.get('name')}{times}")
        try:
            r = fn(dict(it))
        except Exception as e:   # 폴리오 쪽이 죽어도 **큐는 멈추지 않는다** — internal(치명)이 아니라 이 카드의 오류
            r = {"ok": False, "error": "play_failed", "message": f"연주 엔진 오류: {type(e).__name__}: {e}"}
        r = r if isinstance(r, dict) else {"ok": bool(r)}
        if not r.get("ok"):
            err = _s(r.get("error")) or "play_failed"
            ledger.add("play", it.get("name") or "", 0, 0, False, err)
            self._fail(it, err, _s(r.get("message")) or "연주를 시작하지 못했습니다")
            return
        t_req = time.time()
        p["done"] = 1
        # 우리가 새 연주를 시작시켰다 — 앞서 받아 둔 양보(「이미 양보받았다」)는 이 연주에는 없는 것이다.
        # 다음 작업 카드는 「우선」대로 다시 묻는다 (안 그러면 새 곡 위로 바로 끼어든다)
        self._yield_held = False
        ledger.add("play", it.get("name") or "", 0, 0, True)
        self._log(it, "연주 시작을 부탁했습니다" + (f" — {r['message']}" if r.get("message") else "") + " — 끝날 때까지 이 카드가 돕니다")
        # 경과는 **여기서부터** — 보드(board.js lastStart)는 마지막 start 회신부터 세므로 상태 점검 시간이 연주 시간으로 안 보이게 한 번 더 적는다
        self._event("start", it["id"], f"{it['name']} 연주 시작")
        self._play_wait(it, t_req)

    def _play_wait(self, it: dict, t_req: float) -> None:
        """**연주 카드는 막는 카드다** (전에는 부탁하고 바로 done
        이라 다음 작업 카드가 첫 곡 끝에서 양보를 받아 나머지 곡을 못 틀었다). 부탁한 연주가 **끝날 때까지** running 으로 남고, 그 뒤에야
        done — 다음 카드는 그 다음에 선다. 「우선」(music·song·work)은 우리 카드의 연주를 끊지 않는다 — 우리 카드가 도는 동안 다음 카드는
        시작조차 못 하니까. 연주 대기(hold) 카드도 세우지 않는다 — 이 카드 자체가 보이는 것이다.

        무엇을 읽는가 (CLI 0 · 날개 0):
          · 폴리오(perf_now → engine.work_now, 메모리)를 HOLD_POLL(2초)마다. 「이 곡만」(song) 은 `solo`(살아 있는 곡 하나 재생)가 사라진 때가 끝이고,
            어떻게 끝났는지는 `soloEnd.how` — done(회차 다 침) · stopped(폴리오 ■·다른 재생이 자리를 가져감 → 「연주가 중간에 멈춤」, 오류 아님) ·
            error(다음 회차를 못 틀었다 → play_failed, 치명 아님). 「지금 대기열 이어서」(resume) 는 `busy` 가 HOLD_SETTLE(4초) 동안 꺼지면 끝 —
            전체 반복이면 끝을 알 수 없다고 카드가 말하고 ■ 정지나 폴리오가 멈출 때까지 기다린다.
          · 폴리오에 물을 데가 없거나 값이 HOLD_STALE(30초) 넘게 묵었으면 get_activity 를 HOLD_POLL_CLI(5초)마다 직접 읽는다(읽기 · 날개 0) —
            Performance.IsPlaying 이 HOLD_SETTLE 동안 거짓이면 끝 (회차는 못 가른다 · done).
        ■ 정지 → 우리가 부탁한 연주만 멈추고(`play_stop`) 카드는 stopped."""
        p = it["progress"]
        mode = it.get("mode")
        passes = _n(it.get("count"), 1) if mode == "song" else None
        quiet_at = None
        ended = None          # (how, msg) — 폴리오가 「부탁이 끝났다」고 한 뒤
        ended_at = 0.0
        last_busy = True
        seen_solo = False
        src_last = None
        said_endless = False
        while True:
            if self._stop.is_set():
                self._play_abort(it)
                it["status"] = "stopped"
                self._log(it, "사용자 정지 (연주 중)")
                return
            v = self._hold_view(None, last_busy)
            now = time.time()
            folio = v.get("src") == "folio"
            if folio != src_last:
                if src_last is not None or not folio:
                    self._log(it, "폴리오가 재 둔 값을 봅니다 (2초마다 · CLI 0)" if folio
                              else "폴리오에 물을 데가 없어 게임에 직접 읽습니다 (5초마다 · 읽기 · 날개 0 · 회차는 못 가립니다)")
                src_last = folio
            busy = bool(v.get("busy"))
            so = v.get("solo") if folio else None
            cur = 1
            if folio and mode == "song":
                if isinstance(so, dict):
                    seen_solo = True
                    quiet_at = None
                    cur = max(1, min(passes or 1, _n(so.get("done")) + (1 if busy else 0)))
                elif ended is None:
                    e = v.get("soloEnd") if isinstance(v.get("soloEnd"), dict) else None
                    fresh = bool(e) and float(e.get("at") or 0) >= t_req - 1.0
                    if fresh or seen_solo:
                        how = (e or {}).get("how") if fresh else "stopped"
                        ended = (how if how in ("done", "stopped", "error") else "stopped", _s((e or {}).get("msg")) if fresh else "")
                        cur = min(passes or 1, max(1, _n((e or {}).get("done")))) if fresh else cur
                        ended_at = now
                    elif now - t_req > 10.0:   # 부탁은 됐다는데 폴리오가 우리 곡을 들고 있지 않다 — 다른 재생이 곧바로 자리를 가져갔다
                        ended, ended_at = ("stopped", ""), now
            elif folio:   # resume — 폴리오 대기열이 멈출 때까지
                if v.get("endless") and not said_endless:
                    said_endless = True
                    self._log(it, "끝을 알 수 없음 — 반복 재생 · ■ 정지를 누르거나 폴리오가 멈출 때까지 기다립니다")
                if busy:
                    quiet_at = None
                elif ended is None:
                    quiet_at = quiet_at or now
                    if now - quiet_at >= self.HOLD_SETTLE:
                        err = _s(v.get("error"))
                        ended, ended_at = (("error", err) if err else ("done", "")), now
            else:         # 폴리오 없음 — 게임 조회로 (모양은 늘 game)
                if busy:
                    quiet_at = None
                elif ended is None:
                    quiet_at = quiet_at or now
                    if now - quiet_at >= self.HOLD_SETTLE:
                        ended, ended_at = ("done", ""), now
            with self.lock:
                p["play"] = {"title": v.get("title") or it.get("name") or "", "pass": cur, "passes": passes,
                             "elapsed": round(float(v.get("elapsed") or 0.0), 1),
                             "endless": bool(v.get("endless")) and mode != "song", "left": v.get("left"),
                             "src": v.get("src"), "started": t_req, "busy": busy}
            if ended is not None and not (folio and busy and now - ended_at < self.PLAY_END_GRACE):
                self._play_finish(it, ended, passes)
                return
            last_busy = busy
            self._stop.wait(self.HOLD_POLL if folio else self.HOLD_POLL_CLI)

    def _play_finish(self, it: dict, ended, passes) -> None:
        how, msg = ended
        p = it["progress"]
        pl = p.get("play") if isinstance(p.get("play"), dict) else None
        if pl:
            pl["busy"] = False
        if how == "done":
            p.pop("note", None)
            if pl and passes:
                pl["pass"] = passes
            self._done(it, f"연주 끝 — {passes}회 다 쳤습니다" if passes else "연주 끝 — 폴리오 대기열이 멈췄습니다")
        elif how == "stopped":
            p["note"] = PLAY_NOTE_CUT
            self._done(it, f"{PLAY_NOTE_CUT} — 폴리오에서 ■ 를 눌렀거나 다른 재생이 자리를 가져갔습니다 (오류 아님 · 다음 카드로)")
        else:
            p["note"] = "연주가 중간에 끊김"
            self._fail(it, "play_failed", "연주가 중간에 끊겼습니다" + (f": {msg}" if msg else ""))

    def _play_abort(self, it: dict) -> None:
        """■ 정지 — **우리가 부탁한 연주만** 멈춘다 (server._play_stop 이 폴리오 엔진에 「지금 것이 우리 것인가」를 묻고 멈춘다).
        남의 연주면 손대지 않는다. 폴리오가 없으면 여기서 CLI 정지를 보내지 않는다 — 누구 것인지 모른 채 끊지 않는다."""
        fn = self.play_stop
        if fn is None:
            self._log(it, "부탁한 연주를 멈출 데가 없습니다 (폴리오 미연결) — 연주는 폴리오나 게임에서 멈추세요")
            return
        try:
            r = fn(dict(it))
        except Exception as e:
            self._log(it, f"연주를 멈추지 못했습니다 ({type(e).__name__}: {e}) — 폴리오나 게임에서 멈추세요")
            return
        r = r if isinstance(r, dict) else {"ok": bool(r), "stopped": bool(r)}
        if r.get("stopped"):
            self._log(it, "부탁한 연주를 멈췄습니다 (■ 정지)" + (f" — {r['message']}" if r.get("message") else ""))
        else:
            self._log(it, "지금 연주는 우리가 부탁한 것이 아니라 건드리지 않았습니다" + (f" ({r['why']})" if r.get("why") else ""))

    def _finish_notify(self, settings: dict) -> None:
        """보드가 끝났다 — 설정 `queue_done_notify` 가 켜져 있고 **사용자 정지가 아니면** 알림 하나.
        치명·오류 시 정지·날개 차단기로 선 것도 「끝」이다 — 사람이 와서 봐야 하니 더더욱 알린다. ■ 정지는 사람이 그 자리에 있다."""
        if str((settings or {}).get("queue_done_notify") or "on").lower() == "off":
            return
        if self.stop_reason == "user":
            return
        with self.lock:
            leaves = self._leaves()
            done = sum(1 for x in leaves if x.get("status") == "done")
            fail = sum(1 for x in leaves if x.get("status") == "error")
        self._push_notice(f"큐가 끝났습니다 — 완료 {done} · 실패 {fail}", True, "done")

    def _run_gather(self, it: dict, settings: dict) -> None:
        """채집 target 개. 1회 = execute_gathering 한 번 (최대 100개 · 날개 5 — 개수 인자가 없다).

        진행·결과는 **가방 수**로 센다: 시작 전 가방(bag0)을 읽고, 매 회 뒤 다시 읽는다. done = 지금 가방 − bag0.
        가방이 bag0 + target 에 닿으면 끝낸다 — 매 회 전에 보고, 닿았으면 다음 회를 부르지 않는다.
        남은 개수가 100 보다 적은 회는 넘칠 수 있다 → 도는 동안 가방을 읽다가 목표에 닿으면 끊는다 (_gather_watched)."""
        p = it["progress"]
        target = _n(it.get("target"))
        margin = max(0, _n(settings.get("queue_weight_margin"), 30))   # 음수 여유는 「무게 검사 없음」이 된다
        max_passes = _n(settings.get("queue_max_passes"), 50)
        idle = 0   # 연속으로 한 개도 못 캔 회 수 — 소모품이 떨어졌거나 채집지가 비었으면 남은 회를 태우지 않고 끊는다

        def plan_left() -> None:
            # 예상 회수 = 지나간 회 + ⌈남은 개수/100⌉ — 한 회에 100개보다 적게 들면 늘어난다
            left = max(0, target - _n(p.get("done")))
            p["passesPlanned"] = _n(p.get("passes")) + (math.ceil(left / MAX_PER_PASS) if left else 0)

        def where() -> str:
            return f"{p['passes']}/{p['passesPlanned']}회 · +{p['done']}/{target}개"

        def bag_line() -> str:
            return (f"가방 {_n(p.get('bag0'))} → {p['have']}" if p.get("bag0") is not None else f"가방 {p['have']}")

        # 시작 전 가방 — 이어서 도는 카드(멈췄다 다시 시작)는 이미 센 몫(done)을 빼서 기준을 맞춘다
        if p.get("bag0") is None or not p.get("passes"):
            now = self._bag_now(it)
            if now is not None:
                p["have"] = now
                p["bag0"] = now - (_n(p.get("done")) if p.get("passes") else 0)
                if not p.get("passes"):
                    p["done"] = 0
                plan_left()
                self._log(it, f"시작 전 가방 {now}개 — 목표 가방 {p['bag0'] + target}개 "
                              f"(+{target}개 · 예상 {p['passesPlanned']}회 · 날개 {p['passesPlanned'] * WINGS_PER_CALL})")
            elif self._stop.is_set():
                pass
            else:
                self._log(it, "시작 전 가방을 못 읽었습니다 — 이번에는 회신 획득으로 셉니다 (도중에 끊지 않습니다)")
        while True:
            if self._stopped(it):
                return
            plan_left()
            if p["done"] >= target:
                self._done(it, f"{p['done']}/{target}개 캐기 완료 ({p['passes']}회, {bag_line()})"); return
            if p["passes"] >= max_passes:
                self._fail(it, "max_passes"); return
            # 매 회 전: 도구
            g = self._read(it, "get_gatherable_items", it["name"])
            if not g.ok:
                if self._stopped(it):
                    return
                self._read_failed(it, g); return
            row = next((x for x in work.gatherables(g.body) if x["name"] == it["name"]), None)
            if row is None:
                self._fail(it, "not_gatherable"); return
            if not row["toolOk"]:
                self._fail(it, "tool_not_ok"); return
            # 매 회 전: 무게
            inv = self._read(it, "get_inventory", "")
            if not inv.ok:
                if self._stopped(it):
                    return
                self._read_failed(it, inv); return
            b = inv.body if isinstance(inv.body, dict) else {}
            cur = float(b.get("CurrentInventoryWeightAsDecimal") or b.get("CurrentInventoryWeight") or 0)
            mx = float(b.get("MaxInventoryWeightAsDecimal") or b.get("MaxInventoryWeight") or 0)
            if mx and cur >= mx - margin:
                self._fail(it, "overweight_soon", f"{ERROR_KO['overweight_soon']} (무게 {cur:.0f}/{mx:.0f}, 여유 {margin})"); return
            if self._stopped(it):
                return
            # 실행 — 남은 개수가 100 보다 적으면 넘칠 수 있는 회다. 가방 기준(bag0)이 있을 때만 도중에 끊는다
            left = target - p["done"]
            goal_bag = _n(p.get("bag0")) + target if p.get("bag0") is not None else None
            watch = left < MAX_PER_PASS and goal_bag is not None
            self._log(it, f"{p['passes'] + 1}회째 채집 시작 (+{p['done']}/{target}개, 가방 {p['have']}, 무게 {cur:.0f}/{mx:.0f})"
                          + (f" — 남은 {left}개: 가방이 {goal_bag}개에 닿으면 끊습니다" if watch else ""))
            qstop, qseen = self._quest_guard(it, settings)   # 도는 동안 지켜본다 (기본 꺼짐)
            ctl: dict = {}
            try:
                if watch:
                    r, ctl = self._gather_watched(it, goal_bag)
                else:
                    r = self._exec("execute_gathering", {"displayName": it["name"]}, it)
            finally:
                if qstop is not None:
                    qstop.set()                                  # **켠 것은 끈다**
                p.pop("live", None); p.pop("liveSrc", None)   # 회가 끝났다 — 도는 동안의 어림수는 지운다 (가방이 진짜다)
            p["passes"] += 1
            if r is None:
                # 다음 카드에 넘겼다 (_goal_stop) — 채집 호출은 뒤에서 돌아온다. 목표에 닿은 가방 수로 끝낸다
                p["have"] = _n(ctl.get("bag"))
                p["done"] = max(0, p["have"] - _n(p.get("bag0")))
                plan_left()
                self._done(it, f"목표 도달 · {p['done']}/{target}개 ({p['passes']}회, {bag_line()}) — "
                               f"남은 채집은 다음 카드의 명령이 끊습니다")
                return
            body = r.body if isinstance(r.body, dict) else {}
            gained = _n(body.get("gained"))   # 회신의 획득 — 가방을 못 읽을 때만 쓴다
            if ctl.get("reached"):
                # **목표에 닿아 우리가 끊은 회다** — 회신 result=stopped(stop_action) · error=canceled(연주로 밀어냄)는
                # 실패가 아니다. 결과는 가방 수로 적고 다음 회는 부르지 않는다
                read = r.error not in READ_RETRY_ERRORS and r.error != "loading"
                self._bag_count(it, p, gained, read=read)
                if p["done"] < target and _n(ctl.get("bag")) > _n(p.get("have")):
                    p["have"] = _n(ctl.get("bag"))      # 끝난 뒤 가방을 못 읽었다 — 도중에 본 가방 수를 믿는다
                    p["done"] = max(0, p["have"] - _n(p.get("bag0")))
                plan_left()
                how = {"stop": "stop_action", "music": "연주로 밀어내고 정지", "late": "호출이 먼저 끝남"}.get(ctl.get("how"), "")
                self._log(it, f"목표 도달 회신: result={body.get('result') or '-'} error={r.error or '-'} · 회신 {gained}개 "
                              f"({how}) {body.get('cost') or ''}".rstrip())
                self._done(it, f"{p['done']}/{target}개 캐기 완료 · 목표에서 끊음 ({p['passes']}회, {bag_line()})")
                return
            if not r.ok:
                # 시작 뒤 끊긴 오류(overweight·tool_broken·blocked…)도 부분 획득이 있다 — 가방으로 센다.
                # 연결이 끊긴 것이면 가방도 못 읽는다 — 회신 값으로 메운다
                before = p["done"]
                self._bag_count(it, p, gained, read=r.error not in READ_RETRY_ERRORS and r.error != "loading")
                got = p["done"] - before
                if got > 0:
                    self._log(it, f"중단 회신: 가방 +{got}개까지 (error={r.error}) → {where()}")
                # **회신이 실패라고 해서 채집이 정말 끝난 것은 아니다.**
                # 사용자가 지킴이를 켜 두었을 때만 퀘스트로 다시 본다.
                #
                # **사용자가 멈춘 것은 손대지 않는다** — 정지를 눌렀는데 퀘스트가 살아
                # 있다고 계속 돌면 정지가 안 먹는 것이다.
                v = "as_is"
                if not self._stop.is_set():
                    v = self._quest_verdict(it, settings, r, qseen if qstop is not None else {})
                if v == "keep":
                    # **또 부르지 않는다.** 게임 안에서 아직 캐는 중이라 실행을 또 보내면
                    # 도는 행동이 canceled 로 죽고 날개만 5개 더 나간다. 끝날 때까지 본다.
                    if not self._quest_wait(it, settings):
                        self._exec_failed(it, r, body); return
                    got = self._bag_gain(it, p)
                    self._log(it, f"퀘스트가 끝났습니다 — 가방으로 센 획득 {got}개 → {where()}")
                    idle = idle + 1 if got <= 0 else 0
                    if idle >= 2:
                        self._fail(it, "no_progress", f"{ERROR_KO['no_progress']} ({where()})"); return
                    continue
                if v == "retry":
                    continue          # 다시 건다 (회가 하나 늘고 **날개가 든다**)
                self._exec_failed(it, r, body); return
            if body.get("result") == "started":   # 낚시 전용: 목표 없이 자동 낚시만 켜고 돌아온다
                self._done(it, "완료 회신: result=started — 자동 낚시 시작됨. 목표가 없어 여기서 끝냅니다 (그만두려면 stop_action)")
                return
            # 매 회 후: 가방 수로 센다 (못 읽으면 회신 gained)
            before = p["done"]
            self._bag_count(it, p, gained)
            got = p["done"] - before
            self._log(it, f"완료 회신: result={body.get('result')}, 회신 {gained}개 · 가방 {got:+d}개 → {where()} "
                          f"{body.get('cost') or ''}".rstrip())
            if body.get("result") == "stopped_by_user":
                # 이동 중 끊김 (실측 — 전투·직접 정지). 가공·제작과 같은 안내로 이 카드만 오류.
                if self._stop.is_set():
                    it["status"] = "stopped"; self._log(it, "사용자 정지 중 회신: result=stopped_by_user"); return
                self._fail(it, "stopped_by_user", ERROR_KO["stopped_by_user"]); return
            if body.get("result") == "stopped":
                # **게임 쪽에서 이 채집 하나가 멈춘 것이다 — 우리 「■ 정지」도, 목표 도달 정지(위 ctl.reached)도 아니다.**
                # 예전에는 둘을 같은 `stopped` 로 적었고, `_run_card` 가 그걸 보고 "stopped" 를
                # 돌려주면 그룹도 보드도 통째로 섰다. 그래서 **자식 하나가 멈췄다는 이유로
                # 그룹 전체가 실패 열로 갔고, 「오류 시 계속」이 아무 일도 안 했다**.
                # 규칙: 그룹 안의 개별 실패는 그룹 카드 안 실패 열에 남고,
                # **blocked·연결 끊김이면** 그룹 전체가 실패 열로 간다.
                # 우리가 멈춘 것일 때만 `stopped` 로 둔다. 아니면 이 카드만 오류로 끝내고
                # 나머지는 `onError` 가 정한다. 오류 코드는 카탈로그의 result 값 그대로 쓴다.
                if self._stop.is_set():
                    it["status"] = "stopped"; self._log(it, "사용자 정지 중 회신: result=stopped"); return
                self._fail(it, "stopped", f"{ERROR_KO['stopped']} ({where()})"); return
            # 한 개도 못 캤다 — 소모품이 떨어졌거나 채집지가 비었을 수 있다. 두 번 연속이면 끊는다
            # (안 끊으면 남은 회를 돌며 회당 정령의 날개만 태운다).
            idle = idle + 1 if got <= 0 else 0
            if idle >= 2:
                self._fail(it, "no_progress", f"{ERROR_KO['no_progress']} ({where()})"); return

    # ── 채집 목표 도달 정지 ──────────
    #
    # CLI 는 개수를 받지 않고 한 번에 최대 100개를 캔다. 남은 개수가 100 보다 적은 회는 그대로 두면 넘친다
    # (250개 → 300개). 그래서 그 회만 **도는 동안 가방을 읽다가** 목표에 닿으면 끊는다.
    #
    # 실측 (docs/CLI.md 「채집 도중 읽기·정지」):
    #   · get_items·get_activity 는 도는 채집을 끊지 않는다 — 가방 수가 도중에 올라간다 (6 → 16 → 64).
    #   · stop_action 은 Mode.MainButtonState 가 "Stop" 일 때 받아들여진다 ("Stop confirmed").
    #     "Hide"(채집지 사이를 이동 중)일 때는 invalid_state 로 거절된다. stop_action 은 날개를 쓰지 않는다.
    #   · 받아들여지면 몇 초 뒤 execute_gathering 이 ok · result=stopped · gained 로 돌아온다.
    #
    # 실행 명령이 CLI 잠금을 쥐고 있으므로 이 자리의 호출은 전부 **잠금 없는 호출**(raw_cli)이고
    # 공용 파일로 답을 메우지 않는다(allow_last_response=False) — 퀘스트 지킴이(_quest_look)와 같은 길.
    #
    # 첫 stop_action 이 invalid_state 면 (대표 결정, 2026-09-30):
    #   ① 다음에 돌 작업 카드가 있으면 더 기다리지 않고 그 카드로 넘어간다 — 그 카드의 실행 명령이 도는 채집을
    #      갈아치운다 — 실측: 채집 중 execute_crafting 을 보내면 채집 호출이 1초 안에 canceled 로 끝나고 제작이 이어진다.
    #   ② 없으면 지금 든 악기로 악보 하나를 틀어 채집을 밀어내고 곧바로 stop_action 으로 연주를 멈춘다.
    #      **미검증**(연주로 채집이 끊기는지). 틀 수 없으면 ③ 으로.
    #   ③ STOP_RETRY_WAIT 초마다 STOP_RETRY_MAX 번까지 stop_action 을 다시 보낸다 (Hide 인 동안은 보내지 않는다).
    GOAL_POLL = 1.0          # 도는 동안 가방 읽기 간격 (초) — 3초면 실측 8~10개를 넘겨 캤다
    GOAL_READ_TIMEOUT = 20.0
    STOP_RETRY_WAIT = 1.5
    STOP_RETRY_MAX = 20
    PUSH_STOP_TRIES = 4      # 밀어내려고 튼 연주를 멈추는 stop_action 시도 수 (invalid_state 면 0.8초 뒤 다시)
    push_score = None        # () -> {"title": str} | {"why": str} — server 가 꽂는다 (폴리오 대기열의 지금 곡 · 보관함 첫 악보)

    def _raw(self, command: str, body=None, timeout: float = 20.0):
        """잠금 없는 호출 (공용 파일로 메우지 않는다)."""
        try:
            return self.raw_cli(command, body, timeout, allow_last_response=False)
        except TypeError:          # 옛 서명(되돌아보기 끄기를 모르는 호출자)
            return self.raw_cli(command, body, timeout)

    def _call(self, command: str, body, timeout: float):
        """러너의 CLI 호출. 넘겨준 채집 호출(_orphan)이 아직 잠금을 쥐고 있으면 잠금 없이 보낸다 —
        기다리면 그 채집이 끝날 때까지(최대 몇 분) 다음 카드가 서 있게 된다. 그 밖에는 잠금 있는 호출."""
        if self._orphan_alive():
            return self._raw(command, body, timeout)
        return self.cli(command, body, timeout)

    def _orphan_alive(self) -> bool:
        o = self._orphan
        return bool(o is not None and not o["fin"].is_set())

    @staticmethod
    def _poll(sec: float) -> float:
        """폴링 간격 — 검사(MOBIW_DEMO_FAST)에서도 0 이 되지 않게 아주 짧게 남긴다 (0 이면 헛도는 고리가 된다)."""
        return max(0.02, sec * wait_scale())

    def _gather_watched(self, it: dict, goal_bag: int) -> tuple:
        """채집 한 회를 부르고, 도는 동안 가방을 지켜본다 → (회신 | None, ctl).

        회신이 None 이면 다음 카드에 넘긴 것이다 (호출은 뒤에서 돌아온다 — _orphan). ctl 은
        {reached, bag, how, handoff} — 목표에 닿았는지, 그때의 가방 수, 어떻게 끊었는지."""
        box: dict = {}
        fin = threading.Event()
        handoff = threading.Event()
        ctl: dict = {"reached": False, "bag": None, "how": "", "handoff": False}

        def call() -> None:
            try:
                box["r"] = self._exec("execute_gathering", {"displayName": it["name"]}, it)
            except Exception as e:   # 러너 쪽에서 다시 던진다
                box["exc"] = e
            finally:
                fin.set()
                if ctl.get("handoff"):   # 넘긴 뒤 돌아왔다 — 무엇으로 끝났는지 카드에 남긴다
                    r2 = box.get("r")
                    b2 = r2.body if r2 is not None and isinstance(r2.body, dict) else {}
                    err2 = getattr(r2, "error", None)
                    self._log(it, f"넘겨준 채집 호출이 돌아왔습니다: result={b2.get('result') or '-'} "
                                  f"error={err2 or '-'} · 회신 {_n(b2.get('gained'))}개")
                    self._event("gather", it.get("id"), f"「{it.get('name')}」 넘겨준 채집이 끝났습니다 "
                                                         f"({err2 or b2.get('result') or 'ok'})")

        th = threading.Thread(target=call, daemon=True, name="gather-call")
        th.start()
        wt = threading.Thread(target=self._gather_goal_watch, args=(it, goal_bag, fin, handoff, ctl),
                              daemon=True, name="gather-goal")
        wt.start()
        while not fin.wait(0.05):
            if handoff.is_set():
                self._orphan = {"fin": fin, "it": it, "thread": th}
                return None, ctl
        wt.join(timeout=5.0)
        if "exc" in box:
            raise box["exc"]
        return box["r"], ctl

    def _raw_bag(self, it: dict):
        """가방 수를 잠금 없이 읽는다 → int | None. 몸 {"name": 이름} 은 비슷한 이름도 준다(「부드러운 통나무」) —
        DisplayName 이 같은 것만 센다 (work.stock)."""
        try:
            r = self._raw("get_items", {"name": it["name"]}, self.GOAL_READ_TIMEOUT)
        except Exception:
            return None
        if not getattr(r, "ok", False):
            return None
        return _n(work.stock(r.body)["total"].get(it["name"]))

    def _raw_main_button(self):
        """get_activity 의 Mode.MainButtonState ("Stop" | "Hide" | …) — 못 읽으면 None."""
        try:
            r = self._raw("get_activity", "", self.GOAL_READ_TIMEOUT)
        except Exception:
            return None
        if not getattr(r, "ok", False) or not isinstance(r.body, dict):
            return None
        m = r.body.get("Mode") if isinstance(r.body.get("Mode"), dict) else {}
        return _s(m.get("MainButtonState")) or None

    def _raw_stop(self):
        try:
            return self._raw("stop_action", None, self.GOAL_READ_TIMEOUT)
        except Exception as e:
            return _Failed(type(e).__name__, str(e))

    def _gather_goal_watch(self, it: dict, goal_bag: int, fin: threading.Event, handoff: threading.Event,
                           ctl: dict) -> None:
        """도는 채집 한 회를 GOAL_POLL 초마다 가방으로 본다 — **읽기만**. 목표에 닿으면 끊는다 (_goal_stop)."""
        p = it.setdefault("progress", {})
        have0 = _n(p.get("have"))
        while not fin.wait(self._poll(self.GOAL_POLL)):
            if self._stop.is_set():
                return          # ■ 정지는 stop() 가 stop_action 을 보낸다
            bag = self._raw_bag(it)
            if bag is None or fin.is_set():
                continue
            if p.get("liveSrc") in (None, "bag"):
                p["live"], p["liveSrc"] = max(0, bag - have0), "bag"   # 카드의 「+N개」가 도는 동안 움직인다
            if bag >= goal_bag:
                ctl.update(reached=True, bag=bag)
                self._log(it, f"가방 {bag}개 — 목표 {goal_bag}개에 닿았습니다 → stop_action")
                self._goal_stop(it, fin, handoff, ctl)
                return

    def _goal_stop(self, it: dict, fin: threading.Event, handoff: threading.Event, ctl: dict) -> None:
        """목표에 닿은 채집을 끊는다. 첫 stop_action → 거절(invalid_state)이면 ① 다음 카드에 넘기기 ② 연주로 밀어내기
        ③ stop_action 다시 보내기 (위 주석). 모든 시도를 카드 로그에 남긴다."""
        r = self._raw_stop()
        if r.ok:
            ctl["how"] = "stop"
            self._log(it, f"stop_action 1회째 → 수락 ({_s(getattr(r, 'message', '')) or 'ok'})")
            return
        self._log(it, f"stop_action 1회째 → 거절 ({r.error}: {_s(getattr(r, 'message', ''))})")
        if fin.is_set():
            ctl["how"] = "late"
            return
        if r.error == "invalid_state":
            nxt = self._next_action_card(it)
            if nxt is not None and not self._stop.is_set():
                ctl.update(handoff=True, how="handoff")
                msg = (f"「{it['name']}」 목표 도달 — 지금은 멈출 수 없는 순간(이동 중)이라 다음 카드 「{nxt.get('name')}」로 "
                       f"넘어갑니다. 그 카드의 명령이 채집을 대신 끊습니다")
                self._log(it, msg)
                self._event("gather", it.get("id"), msg)
                handoff.set()
                return
            if self._push_out(it, fin, ctl):
                return
        self._stop_retry(it, fin, ctl, 2)

    def _push_out(self, it: dict, fin: threading.Event, ctl: dict) -> bool:
        """다음 카드가 없을 때 — 지금 든 악기로 악보를 틀어 채집을 밀어내고 곧바로 연주를 멈춘다 → 틀었으면 True.
        틀 수 없으면(갈고리 없음·탈것·악보 없음·거절) False — 부르는 쪽이 stop_action 을 다시 보낸다."""
        pick = {}
        try:
            pick = (self.push_score() if callable(self.push_score) else {"why": "악보를 고를 수 없는 실행"}) or {}
        except Exception as e:
            pick = {"why": f"{type(e).__name__}"}
        title = _s(pick.get("title"))
        if not title:
            self._log(it, f"연주로 밀어내기 못 함 ({pick.get('why') or '틀 악보 없음'}) — stop_action 을 다시 보냅니다")
            return False
        msg = f"「{it['name']}」 목표 도달 — 다음 카드가 없어 「{title}」를 틀어 채집을 밀어내고 곧바로 멈춥니다 (미검증)"
        self._log(it, msg)
        self._event("gather", it.get("id"), msg)
        pr = self._raw("play_music_score", {"title": title}, self.GOAL_READ_TIMEOUT)
        if not pr.ok:
            self._log(it, f"연주 시작 거절 ({pr.error}: {_s(getattr(pr, 'message', ''))}) — stop_action 을 다시 보냅니다")
            return False
        self._log(it, "연주 시작 → 곧바로 stop_action 으로 멈춥니다")
        stopped = False
        for k in range(1, self.PUSH_STOP_TRIES + 1):
            sr = self._raw_stop()
            self._log(it, f"연주 정지 stop_action {k}/{self.PUSH_STOP_TRIES} → "
                          + ("수락" if sr.ok else f"거절 ({sr.error})"))
            if sr.ok:
                stopped = True
                break
            if sr.error != "invalid_state":
                break
            if self._stop.wait(self._poll(0.8)):
                break
        if not stopped:
            self._event("gather", it.get("id"), f"「{it['name']}」 밀어내려고 튼 연주를 멈추지 못했습니다 — 게임에서 확인하세요")
        ctl["how"] = "music"
        return True

    def _stop_retry(self, it: dict, fin: threading.Event, ctl: dict, first: int = 1) -> bool:
        """stop_action 을 STOP_RETRY_WAIT 초마다 STOP_RETRY_MAX 번까지. MainButtonState 가 Hide(이동 중)면 보내지 않고
        그 시도를 적은 뒤 기다린다. 호출이 돌아오거나 ■ 정지면 그만둔다. 받아들여지면 True."""
        n = self.STOP_RETRY_MAX
        for k in range(first, n + 1):
            if fin.wait(self._poll(self.STOP_RETRY_WAIT)):
                ctl["how"] = ctl.get("how") or "late"
                return False
            if self._stop.is_set():
                return False
            mb = self._raw_main_button()
            if mb == "Hide":
                self._log(it, f"stop_action {k}/{n}: 이동 중 (MainButtonState=Hide) — {self.STOP_RETRY_WAIT:g}초 뒤 다시")
                continue
            r = self._raw_stop()
            self._log(it, f"stop_action {k}/{n} → " + ("수락" if r.ok else f"거절 ({r.error})")
                          + (f" · MainButtonState={mb}" if mb else ""))
            if r.ok:
                ctl["how"] = "stop"
                return True
        self._log(it, f"stop_action 을 {n}번까지 보냈지만 멈추지 못했습니다 — 이 회가 끝날 때까지 둡니다 (넘친 만큼 더 캡니다)")
        self._event("gather", it.get("id"), f"「{it.get('name')}」 목표에서 끊지 못했습니다 — 이 회가 끝날 때까지 캡니다")
        return False

    def _next_action_card(self, it: dict):
        """이 카드 다음에 러너가 집어 들 카드 — 실행 명령을 보내는 종류(채집·제작·가공·수령)일 때만, 아니면 None.
        그룹 안이면 같은 회차의 다음 pending 자식, 없으면 다음 회차의 첫 자식, 그다음은 보드의 다음 pending 항목."""
        def first_pending(x):
            if x.get("type") == "group":
                if x.get("status") in ("error", "done"):
                    return None
                return next((c for c in (x.get("items") or []) if c.get("status") == "pending"), None)
            return x if x.get("status") == "pending" else None
        with self.lock:
            nxt = None
            g = self._group_of(it["id"])
            if g is not None:
                kids = list(g.get("items") or [])
                i = next((k for k, c in enumerate(kids) if c.get("id") == it["id"]), len(kids))
                nxt = next((c for c in kids[i + 1:] if c.get("status") == "pending"), None)
                if nxt is None and self._repeat_of(g) > _n(g.get("loop")):
                    # 다음 회차 — 되돌려질 첫 자식 (지난 회차에 실패해 빠질 것·걸린 가공은 건너뛴다)
                    nxt = next((c for c in kids if c.get("status") != "waiting"
                                and not (c.get("status") == "error"
                                         and (not g.get("retryFailed", True) or self._stuck(c)))), None)
                anchor = g
            else:
                anchor = it
            if nxt is None:
                roots = list(self.items)
                j = next((k for k, x in enumerate(roots) if x is anchor), len(roots))
                for x in roots[j + 1:]:
                    nxt = first_pending(x)
                    if nxt is not None:
                        break
        return nxt if nxt is not None and nxt.get("type") in ACTION_CARD_TYPES else None

    def _settle_orphan(self) -> None:
        """보드가 끝났는데 넘겨준 채집이 아직 돈다 — 다음 카드의 명령이 끊지 못했다. stop_action 을 다시 보내 본다."""
        o = self._orphan
        if o is None or o["fin"].is_set():
            return
        it = o["it"]
        self._log(it, "보드가 끝났는데 넘겨준 채집이 아직 돌고 있습니다 — stop_action 을 다시 보냅니다")
        self._event("gather", it.get("id"), f"「{it.get('name')}」 넘겨준 채집이 아직 돕니다 — stop_action 을 다시 보냅니다")
        self._stop_retry(it, o["fin"], {}, 1)

    # 제작: count 회를 시설 상한(maxCount)으로 나눠 여러 번 부른다 (N7). 상한을 모르면 한 번에 보내 보고, invalid_count 의
    # maxCount 를 배우면(limits_set) 그 자리에서 나눠 잇는다 — 그 거절은 시작 전이라 날개 0 이다. 호출마다 날개 5.
    # 진행은 카드에 남는다: done(제작한 횟수) · calls(성공한 호출 수) — ■ 정지 뒤 ▶ 시작은 남은 횟수부터 잇는다.
    def _run_craft(self, it: dict) -> None:
        p = it["progress"]
        if self._stopped(it):
            return
        total = max(1, _n(it.get("count"), 1))
        rid = _s(it.get("recipeId"))
        mc = limits_get(rid) if rid else None
        p["done"] = min(total, max(0, _n(p.get("done"))))
        p["calls"] = max(0, _n(p.get("calls")))
        p["passesPlanned"] = craft_plan(it, p)
        if p["done"]:
            self._log(it, f"제작 이어서 — {p['done']}/{total}회 끝남, 남은 {total - p['done']}회")
        else:
            self._log(it, f"제작 시작 {total}회" + (f" — 시설 상한 {mc}회씩 {p['passesPlanned']}번" if p["passesPlanned"] > 1 else ""))
        last = {}
        while p["done"] < total:
            if self._stopped(it):
                return
            if p["passes"] >= COUNT_MAX:   # 마지막 빗장 — 호출마다 날개 5
                self._fail(it, "max_passes", f"{ERROR_KO['max_passes']} (제작 호출 {p['passes']}회)"); return
            left = total - p["done"]
            n = min(mc, left) if mc else left
            r = self._exec("execute_crafting", {"displayName": it["name"], "craftCount": n}, it)
            p["passes"] += 1
            body = r.body if isinstance(r.body, dict) else {}
            if not r.ok:
                got_mc = _n(body.get("maxCount"))
                if r.error == "invalid_count" and 0 < got_mc < n:
                    # 시설 상한을 배웠다 — 나눠서 곧바로 잇는다 (카드를 실패로 두지 않는다). 같은 레시피의 다른 카드도 고쳐 센다
                    if rid:
                        limits_set(rid, got_mc)
                        self._relimit(rid)
                    mc = got_mc
                    p["passesPlanned"] = craft_plan(it, p)
                    self._log(it, f"시설 상한 {got_mc}회 — {got_mc}회씩 나눠 {p['passesPlanned']}번 부릅니다 "
                                  f"(거절은 시작 전이라 날개 0 · 다음부터 최대 버튼도 {got_mc})")
                    continue
                if r.error == "invalid_count" and got_mc > 0 and rid:   # 상한보다 적게 보냈는데도 거절 — 배우기만 하고 멈춘다
                    limits_set(rid, got_mc)
                    self._relimit(rid)
                self._exec_failed(it, r, body); return
            if body.get("result") == "stopped_by_user":
                # 게임 쪽에서 이 제작 하나가 멈춘 것 — 보드를 세우지 않는다 (채집 쪽 주석 참고)
                if self._stop.is_set():
                    it["status"] = "stopped"; self._log(it, "사용자 정지 중 회신: result=stopped_by_user"); return
                self._fail(it, "stopped_by_user", ERROR_KO["stopped_by_user"]); return
            made = _n(body.get("craftCount"), n) or n
            p["done"] = min(total, p["done"] + made)
            p["calls"] += 1
            p["passesPlanned"] = craft_plan(it, p)
            last = body
            if p["passesPlanned"] > 1:
                self._log(it, f"{p['calls']}/{p['passesPlanned']}번 · {p['done']}/{total}회 — result={body.get('result')} {body.get('cost') or ''}".rstrip())
                with self.lock:
                    self._save()
        self._refresh_bag(it)
        if p["calls"] > 1:
            self._done(it, f"완료: {p['calls']}번 나눠 {p['done']}회 제작 (산출 {p['done'] * _n(p.get('per'), 1)}개, 가방 {p['have']})")
        else:
            self._done(it, f"완료 회신: result={last.get('result')}, {p['done']}회 제작 (산출 {p['done'] * _n(p.get('per'), 1)}개, 가방 {p['have']}) {last.get('cost') or ''}".rstrip())

    def _relimit(self, rid: str) -> None:
        """시설 상한을 새로 배웠다 — 같은 레시피의 대기 카드들이 예상 호출 수를 다시 센다 (화면·확인창의 날개 수)."""
        with self.lock:
            for x in self._leaves():
                if x.get("type") == "craft" and _s(x.get("recipeId")) == rid and isinstance(x.get("progress"), dict):
                    x["progress"]["passesPlanned"] = craft_plan(x, x["progress"])

    # 가공 (N6): **등록 직전마다** 그 시설의 칸을 게임 대기열(get_altering_works, 읽기 · 날개 0)로 세어 빈 칸만큼만 건다.
    # 칸이 차면 오류가 아니다 — waiting(작업 중 열 · 가공 대기)으로 두고 다음 항목으로 넘어간다. 남은 등록은
    # 완료가 모여 받으러 간 뒤 빈 칸에 건다 (_collect_ready — 카드 사이·회차 끝·체인 끝).
    #   none(걸기만): 다 걸면 그 자리에서 done (끝의 몫은 받지 않는다). 칸이 차서 남았으면 waiting — 칸을 비울 만큼만 받는다
    #   later        : waiting → 완료가 모이면 받고 이어서 건다, 다 받으면 done
    #   wait         : 같은 규칙으로 그 자리에서 끝까지 (_wait_and_collect)
    # 진행은 앱이 세어 카드에 저장한다 — 등록(done) · 수령(got). 칸은 늘 게임 대기열의 그 시설 건수로 판단한다.
    def _run_alter(self, it: dict, settings: dict, wait_alter: bool = False) -> None:
        # 항목 옵션 collect 가 우선이다. 그룹 설정(waitAlter)은 **later 인 자식만** wait 로 올린다 —
        # 사용자가 「걸기만」(none)이라고 명시한 것을 그룹 설정이 뒤집으면 안 된다.
        mode = alter_mode(it.get("collect"))
        wait = mode == "wait" or (wait_alter and mode == "later")
        p = it["progress"]
        p["done"] = min(it["count"], max(0, _n(p.get("done"))))
        p["got"] = min(p["done"], max(0, _n(p.get("got"))))
        if self._stopped(it):
            return
        res = self._alter_register(it, settings)
        if res == "stop":
            self._stopped(it)
            return
        if res != "ok":          # 실패 — 상태·안내는 이미 적혔다
            return
        left = it["count"] - p["done"]
        if mode == "none" and left == 0:
            # 걸기만: 등록을 마쳤으면 그 자리에서 끝낸다. waiting 으로 두면 「작업 중」 열에 남고
            # 체인 끝에 기다린다 — 「걸기만」의 뜻과 다르다.
            self._done(it, f"가공 {p['done']}건 등록 완료 — 걸기만(수령하지 않습니다)")
            return
        it["status"] = "waiting"
        p.pop("collectRetry", None); p.pop("collectRetryAt", None)   # 새로 등록했다 — 지난 「수령분 없음」 횟수는 무관
        slot = f"칸 {p.get('slot')}/{p.get('cap')}" if p.get("cap") is not None else "칸 모름"
        if left:
            self._log(it, f"{slot} — 시설이 차서 남은 {left}건은 완료를 받아 칸이 비면 겁니다 · "
                          + ("그 자리에서 기다립니다" if wait else "다음 항목으로 넘어갑니다"))
        else:
            self._log(it, f"{p['done']}건 등록 · {slot} — "
                          + ("완료까지 기다립니다" if wait else "다음 항목으로 넘어가고, 완료가 모이면 수령합니다"))
        if wait:
            self._wait_and_collect([it], settings)

    def _alter_fac(self, it: dict, rows: list) -> str:
        """그 가공의 시설 이름 — 게임 대기열에 같은 이름의 작업이 있으면 그 FacilityName(기억해 둔다),
        없으면 카드·기억(alter_slots.json)·레시피 DB. 모르면 빈 글."""
        p = it["progress"]
        fac = next((x["facility"] for x in rows if x["name"] == it["name"] and x["facility"]), "")
        if fac:
            alter_facility_set(it["name"], fac)
        else:
            fac = _s(p.get("facility")) or alter_facility_get(it["name"])
        if fac:
            p["facility"] = fac
        return fac

    def _alter_look(self, it: dict, rows: list | None = None) -> dict | None:
        """그 카드 시설의 칸 사정. rows 가 없으면 get_altering_works 를 읽는다(날개 0) — 실패면 None (self._look_err).
        칸 수는 기억값(없으면 7). 게임이 그보다 많이 들고 있으면 칸이 늘어난 것이다 — 본 만큼으로 올려 기억한다."""
        if rows is None:
            w = self._read(it, "get_altering_works", "")
            if not w.ok:
                self._look_err = w
                return None
            store.set_cache("works", w.body)
            rows = work.works(w.body)["works"]
        p = it["progress"]
        fac = self._alter_fac(it, rows)
        here = [x for x in rows if fac and x["facility"] == fac]
        cap = alter_cap_get(fac) if fac else ALTER_SLOTS_DEFAULT
        if fac and len(here) > cap:
            cap = len(here)
            alter_cap_set(fac, cap)
            self._log(it, f"「{fac}」 칸이 {cap}건까지 차 있습니다 — 칸 수를 {cap}으로 기억합니다")
        mine = [x for x in (here if fac else rows) if x["name"] == it["name"]]
        if fac:
            p["slot"], p["cap"] = len(here), cap
        done_here = [x for x in here if x["done"]]
        return {"rows": rows, "fac": fac, "used": len(here), "cap": cap,
                "facDone": len(done_here),
                "mine": len(mine), "mineDone": sum(1 for x in mine if x["done"]),
                # 수령 명령의 displayName — 그 시설에서 **완료된** 작업 이름 (진행 중인 이름이면 not_completed_yet). 제 이름이 먼저
                "doneName": next((x["name"] for x in sorted(done_here, key=lambda x: x["name"] != it["name"])), ""),
                "next": min([x["left"] for x in (here or mine) if not x["done"]], default=None)}

    def _alter_register(self, it: dict, settings: dict, soft: bool = False) -> str:
        """빈 칸만큼 남은 등록을 건다 → "ok"(다 걸었거나 칸이 찼다) · "fail"(상태는 적혔다) · "stop"(■ 정지).
        **매 등록 직전에 대기열을 읽는다** — 앱이 센 칸 수를 믿지 않는다(사용자가 직접 건 가공·다른 카드·재시작·수령 사이 완료).
        soft — 카드 사이 돌봄(_collect_ready)에서 부를 때: 읽기 실패는 카드를 끝내지 않고 다음 기회로 미룬다."""
        p = it["progress"]
        while p["done"] < it["count"] and not it.get("regStop"):
            if self._stop.is_set():
                return "stop"
            if p["passes"] >= COUNT_MAX:   # 마지막 빗장 — 수량은 add/update/_load_card 가 이미 자르지만, 등록은 회당 날개 5 다
                self._fail(it, "max_passes", f"{ERROR_KO['max_passes']} (가공 등록 {p['passes']}회)"); return "fail"
            look = self._alter_look(it)
            if look is None:
                if soft:
                    self._log(it, "대기열을 읽지 못해 이번에는 걸지 않습니다 — 다음 기회에 다시 봅니다")
                    return "ok"
                self._read_failed(it, self._look_err, "get_altering_works 실패"); return "fail"
            if look["fac"] and look["used"] >= look["cap"]:
                self._log(it, f"칸 {look['used']}/{look['cap']} — 「{look['fac']}」이 찼습니다. 부르지 않습니다 "
                              f"(남은 {it['count'] - p['done']}건)")
                return "ok"
            if not look["fac"]:
                self._log(it, "이 가공의 시설을 아직 모릅니다 — 한 건 걸고 대기열에서 시설을 배웁니다")
            self._log(it, f"가공 등록 {p['done'] + 1}/{it['count']}"
                          + (f" · 칸 {look['used']}/{look['cap']}" if look["fac"] else ""))
            r = self._exec("execute_altering", {"displayName": it["name"]}, it)
            p["passes"] += 1
            body = r.body if isinstance(r.body, dict) else {}
            if not r.ok:
                if r.error == "blocked" and body.get("kind") == "unknown_modal":
                    # 칸을 셌는데도 막혔다 — 게임에 「가공 대기열이 가득 찼습니다」 창(날개 5 는 이미 빠졌다, 실측)
                    self._alter_full_stop(it, look["fac"], look["used"], r)
                    return "fail"
                if self._stop.is_set() and r.error in ("canceled", "timeout"):
                    return "stop"
                self._exec_failed(it, r, body)
                self._keep_outstanding(it)
                return "fail"
            if body.get("result") == "stopped_by_user":
                # 게임 쪽에서 이동이 멈춘 것 — 보드를 세우지 않는다 (채집 쪽 주석 참고)
                if self._stop.is_set():
                    self._log(it, "사용자 정지 중 회신: result=stopped_by_user (이동)")
                    return "stop"
                self._fail(it, "stopped_by_user", ERROR_KO["stopped_by_user"])
                self._keep_outstanding(it)
                return "fail"
            p["done"] += 1
            if look["fac"]:
                p["slot"] = look["used"] + 1
            self._log(it, f"등록됨: result={body.get('result')} {body.get('cost') or ''}".rstrip())
            with self.lock:
                self._save()
        return "ok"

    def _keep_outstanding(self, it: dict) -> None:
        """등록이 (치명이 아닌 이유로) 실패했는데 이미 건 작업이 남아 있다 — 카드를 waiting 으로 되돌려 그것은 마저 받는다.
        남은 등록은 걸지 않고(regStop), 다 받으면 그 오류로 닫는다. 「걸기만」은 받지 않으므로 그대로 실패다."""
        p = it["progress"]
        if it.get("status") != "error" or is_fatal(it.get("error")) or alter_mode(it.get("collect")) == "none":
            return
        if _n(p.get("done")) <= _n(p.get("got")):
            return
        it["regStop"] = {"error": it.get("error"), "message": it.get("message") or ""}
        it["status"] = "waiting"
        it.pop("error", None); it.pop("message", None)
        self._log(it, f"남은 {it['count'] - _n(p.get('done'))}건은 걸지 않습니다({it['regStop']['error']}) — "
                      f"이미 건 {_n(p.get('done')) - _n(p.get('got'))}건은 마저 받고 그 오류로 닫습니다")

    def _alter_full_stop(self, it: dict, fac: str = "", used: int = 0, r=None) -> None:
        """게임에 「가공 대기열이 가득 찼습니다」 창이 떴다 — CLI 로 닫히지 않는다(실측). 자동으로 닫으려 하지 않고 보드를 세운다.
        가공 등록에서 막혔으면 그때 센 건수를 그 시설의 칸 수로 기억한다 (칸 수는 시설 레벨마다 다를 수 있다).
        카드는 실패가 아니라 이어 갈 자리에 둔다 — 건 것이 있는 가공은 waiting, 그 밖은 stopped(▶ 시작이 다시 집는다)."""
        if fac and used > 0 and used != alter_cap_get(fac):
            alter_cap_set(fac, used)
            self._log(it, f"「{fac}」 칸 수를 {used}로 기억합니다 (그 건수에서 대기열이 가득 찼다고 막혔다)")
        msg = ERROR_KO["alter_full"]
        raw = getattr(r, "message", "") if r is not None else ""
        p = it.get("progress") if isinstance(it.get("progress"), dict) else {}
        if it.get("type") == "alter" and _n(p.get("done")) > 0:
            it["status"] = "waiting"
        else:
            it["status"] = "stopped"
            it["message"] = msg
        it.pop("error", None)
        self._log(it, f"blocked·unknown_modal — {msg}" + (f" · 원문: {raw}" if raw else ""))
        self._alter_recent = False
        self.stop_reason = self.stop_reason or "fatal:alter_full"
        self.last_error = {"id": it.get("id"), "name": it.get("name") or "", "type": it.get("type") or "",
                           "error": "alter_full", "message": msg, "at": time.time()}
        self._event("stop", it.get("id"), "보드 정지 — " + msg)
        self._stop.set()

    # 수령: complete_altering_work 1회 — 독립 「수령」 항목과 가공 항목의 2단계가 같은 함수를 쓴다
    def _exec_collect(self, it: dict, display: str = ""):
        """complete_altering_work 를 부르고, 성공하면 가방·대기열 캐시를 다시 읽는다. (응답, body) 를 돌려준다.
        display — 보낼 displayName (그 시설에서 **완료된** 작업 이름). 없으면 카드 이름.
        수령 뒤 읽은 대기열은 self._post_rows 에 둔다 (못 읽었으면 None) — 가공 카드가 받은 건수를 센다."""
        name = display or it["name"]
        self._post_rows = None
        r = self._exec("complete_altering_work", {"displayName": name}, it)
        it["progress"]["passes"] += 1
        body = r.body if isinstance(r.body, dict) else {}
        if r.ok:
            self._refresh_bag(it)
            w = self._call("get_altering_works", "", READ_TIMEOUT)   # 수령 뒤 대기열 갱신 — 화면의 대기열 카드가 바로 줄어들게
            if w.ok:
                store.set_cache("works", w.body)
                self._post_rows = work.works(w.body)["works"]
            # **ok 회신을 그대로 믿지 않는다.** 직접 정지·이동 실패면 게임은 ok 에 result=stopped_by_user·collected 0 으로 답한다.
            # 회신이 끊김이거나, 받은 것이 0 인데 그 시설(이름)에 완료분이 아직 남아 있으면 **수령이 안 된 것**.
            left = 0
            if w.ok:
                left = sum(1 for x in work.works(w.body)["works"] if x["done"] and x["name"] == name)
            if body.get("result") in WING_WASTE_RESULTS or (_n(body.get("collected")) <= 0 and left > 0):
                self._log(it, f"수령 회신을 믿지 않음: result={body.get('result')} collected={_n(body.get('collected'))} · 시설에 완료분 {left}건 남음")
                import dataclasses
                r = dataclasses.replace(r, body=dict(body, collected=0), ok=False, error="collect_interrupted",
                                        message=ERROR_KO["collect_interrupted"])
                return r, dict(body, collected=0)
            rw = body.get("rewards") if isinstance(body.get("rewards"), list) else []
            # 실측 회신은 {Name, Amount} 이다 (예전 가정 {DisplayName, Count} 도 받는다). 같은 이름은 합쳐 한 번만 적는다.
            tot: dict = {}
            for x in rw:
                if isinstance(x, dict):
                    nm = str(x.get("Name") or x.get("DisplayName") or "?")
                    tot[nm] = tot.get(nm, 0) + _n(x.get("Amount"), _n(x.get("Count")))
            summ = ", ".join(f"{k} ×{v}" for k, v in tot.items())[:200]
            crit = body.get("criticalRewards") if isinstance(body.get("criticalRewards"), list) else []
            self._log(it, f"수령 회신: collected {_n(body.get('collected'))} · rewards {summ or '없음'}"
                          + (f" · critical {len(crit)}건" if crit else "") + (f" {body.get('cost')}" if body.get("cost") else ""))
        return r, body

    def _run_collect(self, it: dict) -> None:
        p = it["progress"]
        if self._stopped(it):
            return
        self._log(it, f"수령 시작 「{it.get('facility', '')}」 (담을 때 완료 {it.get('count', 0)}건)")
        r, body = self._exec_collect(it)
        if not r.ok:
            if r.error == "not_completed_yet":
                self._fail(it, "not_completed_yet", (r.message or ERROR_KO["not_completed_yet"]) + " 진행 중 작업이 끝난 뒤 재시도하세요.")
                return
            if r.error == "collect_interrupted":
                if self._stop.is_set():
                    it["status"] = "stopped"; self._log(it, "사용자 정지 중 수령 끊김 — 대기로 둡니다"); return
                self._fail(it, "collect_interrupted", ERROR_KO["collect_interrupted"]); return
            self._exec_failed(it, r, body); return
        p["done"] = _n(body.get("collected"))
        self._done(it, f"수령 완료 (collected={p['done']}, 가방 {p['have']})")

    def _awaiting_collect(self) -> list:
        """러너가 돌봐야 할 가공 카드 (waiting). 「걸기만」(none)은 **남은 등록이 있을 때만** 대상이다 — 칸을 비워야 걸 수 있다.
        다 건 none 은 받지 않는다. collect 필드가 없는 옛 항목은 alter_mode() 가 "later" 로 보므로 그대로 수령한다."""
        return [x for x in self._leaves()
                if x.get("status") == "waiting"
                and (alter_mode(x.get("collect")) != "none" or alter_calls(x)[1] > 0)]

    def _alter_want(self, it: dict, look: dict, settings: dict, force: bool) -> bool:
        """지금 받으러 갈까 (N6). 수령은 날개 0 이지만 시설까지 이동이라 도는 작업을 끊는다 — 그래서 모았다 간다.
        · 시설 완료가 설정 n(alter_collect_at, 1 ~ 칸 수) 이상
        · 끝물 — 이 카드가 아직 받을 건(count − got)이 n 보다 적어 n 에 못 닿으면, 이 카드 이름의 작업이 전부 끝났을 때
        · 시설이 꽉 찼고 전부 끝났다 — 더 기다릴 것이 없다
        · 「걸기만」 — 남은 등록을 걸 칸이 없을 때, 그 몫(min(n, 남은 등록))만큼 모이면
        · force(마지막 기회 · batch=False) — 제 것이 하나라도 끝났으면
        · 게임이 같은 이름의 진행 중 작업 때문에 거절한 적이 있으면(collectWhole) 제 것이 전부 끝났을 때만"""
        p = it["progress"]
        if look["facDone"] <= 0:
            return False
        if p.get("collectWhole") and look["mineDone"] < look["mine"]:
            return False
        if force:
            return look["mineDone"] > 0
        n = collect_at(settings, look["cap"])
        regs = 0 if it.get("regStop") else it["count"] - _n(p.get("done"))
        full = bool(look["fac"]) and look["used"] >= look["cap"]
        if alter_mode(it.get("collect")) == "none":
            return regs > 0 and full and look["facDone"] >= min(n, regs)
        if full and look["facDone"] >= look["used"]:
            return True
        # 받을 건 = 앞으로 등록할 것까지 친 수. 등록을 멈춘 카드(regStop — 재료 부족 등)는 **이미 등록한 수**까지만 받는다 —
        # 전체 수로 세면 다 끝난 3건을 두고 6건이 모이기를 끝없이 기다린다.
        total = _n(p.get("done")) if it.get("regStop") else it["count"]
        if total - _n(p.get("got")) < n:
            return look["mineDone"] == look["mine"] and (regs == 0 or full)
        return look["facDone"] >= n

    def _alter_finish(self, it: dict, look: dict) -> None:
        """등록을 다 했고(또는 멈췄고) 받을 것도 없다 — 카드를 닫는다."""
        p = it["progress"]
        rs = it.pop("regStop", None)
        p.pop("collectWhole", None)
        if isinstance(rs, dict):
            it["status"] = "error"
            it["error"] = rs.get("error") or "cli_error"
            it["message"] = (rs.get("message") or ERROR_KO.get(it["error"], "")) + f" — 이미 건 {_n(p.get('got'))}건은 받았습니다"
            self._log(it, f"오류 {it['error']}: {it['message']}")
            self._event("error", it["id"], f"{it['name']}: {it['error']} — {it['message']}")
            return
        if alter_mode(it.get("collect")) == "none":
            self._done(it, f"가공 {_n(p.get('done'))}건 등록 완료 — 걸기만(수령하지 않습니다)")
        elif _n(p.get("got")) < _n(p.get("done")) and not look["mine"]:
            # 대기열에서 사라짐 — 게임에서 직접 수령했거나 등록이 안 된 것. 더 기다릴 게 없다
            self._done(it, "대기열에 없음 — 게임에서 이미 수령했거나 등록되지 않은 것으로 봅니다 "
                           f"(등록 {_n(p.get('done'))} · 앱이 받은 것 {_n(p.get('got'))})")
        else:
            self._done(it, f"가공 {_n(p.get('done'))}건 모두 받음 (등록 {_n(p.get('done'))}/{it['count']} · 수령 {_n(p.get('got'))})")

    def _tick_error(self, it: dict) -> None:
        """카드 사이 돌봄(수령·재등록)에서 난 오류 — 카드 실행에서 난 오류와 **같은 규칙**이다:
          치명            → 보드 정지 (설정 무관)
          그룹 안 + 그룹 「오류 시 정지」 → **그 그룹만** 멈춘다 (도는 그룹이면 이 회차를 여기서 끊는다)
          그룹 밖 + 보드 「오류 시 정지」 → 보드 정지
        전에는 stop_reason 만 적고 아무것도 세우지 않아 다음 카드가 그대로 돌았다.
        보드 정지는 사용자 정지와 같은 플래그(_stop)를 쓴다 — 러너의 모든 대기 자리가 그 플래그를 본다.
        사유(stop_reason)를 먼저 적어 두므로 _run 의 finally 가 사용자 정지로 오해하지 않는다."""
        err = it.get("error")
        g = self._group_of(it["id"])
        if is_fatal(err):
            self.stop_reason = self.stop_reason or f"fatal:{err}"
            self._event("stop", it["id"], f"보드 정지 — {err} 는 다음 항목도 성공할 수 없는 오류")
            self._stop.set()
        elif g is not None:
            if g.get("onError") == "stop" and g.get("status") == "running":
                self._group_halt = g["id"]
        elif self.config["onError"] == "stop":
            self.stop_reason = self.stop_reason or "onError"
            self._event("stop", it["id"], "보드 정지 — 설정 onError=stop")
            self._stop.set()

    def _collect_ready(self, settings: dict, batch: bool | None = None) -> bool:
        """가공 카드(waiting)를 돌본다 (N6) — 카드 사이·회차 끝·체인 끝, 그리고 완료 대기(_wait_and_collect)마다.
        대기열을 한 번 읽고(날개 0) 카드마다: 받을 때가 됐으면(_alter_want) 받으러 가고(날개 0 · 이동),
        빈 칸이 있으면 남은 등록을 건다(건당 날개 5 · 등록 직전마다 다시 읽어 칸을 센다). 할 일이 없으면 그대로 넘어간다 —
        n 개가 모이기 전에는 이동하지 않는다. 무언가 받았거나·걸었거나·끝냈으면 True.

        `batch=False` 는 마지막 기회(대기 상한에 닿았을 때) — 설정과 무관하게 **지금 받을 수 있는 것을 받는다**.
        안 그러면 이미 익은 것이 「아직 n개가 아니다」는 이유로 영영 안 나온다. (`alter_batch_collect` 설정은 이 n 으로 대신했다)"""
        force = batch is False
        with self.lock:
            waiting = self._awaiting_collect()
        if not waiting or self._stop.is_set():
            return False
        w = self._call("get_altering_works", "", READ_TIMEOUT)
        if not w.ok:
            return False
        store.set_cache("works", w.body)
        rows = work.works(w.body)["works"]
        acted = False
        for it in waiting:
            if it["id"] in self._removed:   # 대기열을 읽는 사이 지웠다 — 지운 카드에 명령을 보내지 않는다
                continue
            if self._stop.is_set():
                break
            if it.get("status") != "waiting":   # 앞 카드의 수령이 같은 시설을 받아 이 카드를 닫았다
                continue
            p = it["progress"]
            retry = _n(p.get("collectRetry"))
            if retry:
                # 직전 수령이 「수령분 없음」(COLLECT_RACE_ERRORS) — 조회와 어긋난 순간이다. 짧게 가라앉힌 뒤
                # 대기열을 **새로 읽어** 판단한다: 사라졌으면 게임에서 받은 것, Completed 면 다시 수령, InProgress 면 계속 대기.
                left_s = float(p.get("collectRetryAt") or 0) - time.time()
                if left_s > 0 and self._stop.wait(min(left_s, self.COLLECT_RETRY_WAIT)):
                    return acted
                w = self._call("get_altering_works", "", READ_TIMEOUT)
                if not w.ok:
                    continue    # 읽기 실패 — 다음 폴링에 다시
                store.set_cache("works", w.body)
                rows = work.works(w.body)["works"]
            look = self._alter_look(it, rows)
            regs = 0 if it.get("regStop") else it["count"] - _n(p.get("done"))
            if regs <= 0 and (alter_mode(it.get("collect")) == "none" or not look["mine"]):
                p.pop("collectRetry", None); p.pop("collectRetryAt", None)
                self._alter_finish(it, look)
                acted = True
                continue
            if not self._alter_want(it, look, settings, force):
                if regs > 0 and (not look["fac"] or look["used"] < look["cap"]) and not force:
                    acted = self._alter_refill(it, settings) or acted   # 빈 칸이 있다 — 이동 없이 받을 것은 없지만 걸 것은 있다
                    rows = self._rows_now(rows)
                elif look["facDone"] and p.get("seen") != look["facDone"]:   # 같은 말을 폴링마다 되풀이하지 않는다
                    p["seen"] = look["facDone"]
                    n = collect_at(settings, look["cap"])
                    self._log(it, f"완료 {look['facDone']}건 · 칸 {look['used']}/{look['cap']} — "
                                  f"{n}건 모이면 받으러 갑니다 (설정)")
                continue
            self._log(it, f"완료 감지(폴링): 「{look['fac'] or it['name']}」 완료 {look['facDone']}건 · 이 카드 {look['mineDone']}/{look['mine']}건"
                          + (f" — 다시 읽음 (재시도 {retry}/{COLLECT_RETRY_MAX})" if retry else ""))
            self._event("collect", it["id"], f"{it['name']} 완료 {look['facDone']}건 → 수령")
            if self._stop.is_set():
                return acted
            with self.lock:
                self.current = it["id"]
            ok = self._precheck(it, settings)   # 수령도 실행 명령 — 부르기 전에 상태 점검
            if ok:
                before = {x["id"]: self._mine_count(x, rows) for x in waiting if x.get("status") == "waiting"}
                r, body = self._exec_collect(it, look["doneName"] or it["name"])
                if not r.ok and r.error == "not_completed_yet":
                    # 대기열은 완료로 보였는데 게임은 아직이라고 한다 — 같은 이름의 작업이 아직 진행 중이면 그 이름으로는 못 받는다
                    # (카탈로그). 항목을 죽이면 이미 등록·결제된 가공을 영영 못 받는다 — waiting 으로 두고, 이 카드의 작업이
                    # 전부 끝났을 때 다시 받는다 (collectWhole).
                    it["status"] = "waiting"
                    p["collectWhole"] = True
                    self._log(it, "아직 진행 중이라고 응답 — 이 이름의 작업이 전부 끝나면 다시 수령합니다")
                    continue
                if not r.ok and r.error in COLLECT_RACE_ERRORS and retry + 1 < COLLECT_RETRY_MAX:
                    # 방금 Completed 로 읽었는데 「수령분 없음」 — 조회와 수령 캐시가 완료 순간에 어긋났거나(실제로 겪었다)
                    # 그 순간 게임에서 직접 받았다. 완료분은 시설에 그대로다. not_completed_yet 과 같이 waiting 으로 두고
                    # COLLECT_RETRY_WAIT 초 뒤 대기열을 다시 읽어 수령한다. 거절은 이동 전에 돌아오고 수령은 날개 0 — 재시도 비용 없음.
                    p["collectRetry"] = retry + 1
                    p["collectRetryAt"] = time.time() + self.COLLECT_RETRY_WAIT
                    it["status"] = "waiting"
                    self._log(it, f"수령분 없음이라 응답 — 조회와 어긋남, 잠시 뒤 다시 읽어 수령합니다 ({retry + 1}/{COLLECT_RETRY_MAX})")
                    continue
                if r.ok:
                    p.pop("collectRetry", None); p.pop("collectRetryAt", None); p.pop("collectWhole", None)
                if not r.ok and r.error == "collect_interrupted":
                    # 수령이 끊겼다(직접 정지·이동 실패) — 완료분은 시설에 그대로다. 카드를 닫지 않고 waiting 으로 두어 다시 받는다
                    it["status"] = "waiting"
                    self._log(it, ERROR_KO["collect_interrupted"] + " (자동 수령은 다음 기회에 다시)")
                    self._event("error", it["id"], f"{it['name']}: collect_interrupted — {ERROR_KO['collect_interrupted']}")
                    continue
                if not r.ok:
                    self._exec_failed(it, r, body)
            if it.get("status") == "error" and it["id"] not in self._removed:
                self._tick_error(it)
                continue
            if not ok or it.get("status") != "waiting":
                continue
            acted = True
            # 받은 건수는 **대기열에서 사라진 제 작업 수**로 센다 (회신 collected 는 그 시설 전부 — 다른 카드·직접 건 것도 섞인다).
            # 완료분만 세면 읽은 뒤·수령 전에 막 끝난 것이 빠진다 — 수령 사이에 등록은 없으니 사라진 것은 곧 받은 것이다.
            # 같은 시설의 다른 가공 카드도 같이 받았다 — 그 카드들의 수령 수도 같이 올린다.
            post = self._post_rows
            gained_it = 0
            for x in waiting:
                if x.get("status") != "waiting" or x["id"] not in before:
                    continue
                gained = before[x["id"]] - self._mine_count(x, post) if post is not None else 0
                if x is it and gained <= 0:
                    # 수령 뒤 대기열을 못 읽었거나 아직 옛 모습이다 — 회신은 받았다고 한다(_exec_collect 가 확인했다). 제 완료분만큼으로 본다
                    gained = min(look["mineDone"], _n(body.get("collected")))
                xp = x["progress"]
                xp["got"] = min(_n(xp.get("done")), _n(xp.get("got")) + max(0, gained))
                if x is it:
                    gained_it = max(0, gained)
                elif gained > 0:
                    self._log(x, f"같은 시설 수령에 함께 받음 {gained}건 — 수령 {xp['got']}")
            self._log(it, f"수령 완료 (collected={body.get('collected')}, 가방 {it['progress']['have']}) — "
                          f"등록 {_n(p.get('done'))}/{it['count']} · 수령 {_n(p.get('got'))}")
            # 시설에 남은 제 작업 — 받기 전 수에서 받은 만큼 뺀 것과 수령 뒤 읽은 것 중 작은 쪽 (읽은 것이 옛 모습일 수 있다)
            mine_left = max(0, look["mine"] - gained_it)
            if post is not None:
                mine_left = min(mine_left, self._mine_count(it, post))
            regs = 0 if it.get("regStop") else it["count"] - _n(p.get("done"))
            if regs > 0:
                # 빈 칸에 남은 건을 다시 건다 (등록 직전마다 대기열을 다시 읽는다)
                self._alter_refill(it, settings)
                rows = self._rows_now(rows)
                look = self._alter_look(it, rows)
                mine_left = look["mine"]
                regs = 0 if it.get("regStop") else it["count"] - _n(p.get("done"))
            else:
                look = dict(look, mine=mine_left)
            if it.get("status") == "waiting" and regs <= 0 and (alter_mode(it.get("collect")) == "none" or not mine_left):
                self._alter_finish(it, look)
            elif it.get("status") == "waiting":
                left = (_n(p.get("done")) if it.get("regStop") else it["count"]) - _n(p.get("got"))
                self._log(it, f"남은 {left}건 — 계속 기다립니다")
        with self.lock:
            self.current = None
            self._save()
        return acted

    def _mine_count(self, it: dict, rows: list) -> int:
        """그 카드 시설에 있는 그 이름의 작업 수 (상태 무관)."""
        fac = _s((it.get("progress") or {}).get("facility"))
        return sum(1 for x in rows if x["name"] == it["name"] and (not fac or x["facility"] == fac))

    def _rows_now(self, fallback: list) -> list:
        """마지막으로 읽은 대기열 (캐시) — 못 읽었으면 fallback."""
        try:
            d = store.get_cache("works").get("data")
            return work.works(d)["works"] if d is not None else fallback
        except Exception:
            return fallback

    def _alter_refill(self, it: dict, settings: dict) -> bool:
        """카드 사이에 빈 칸만큼 남은 등록을 건다 — 실행 명령이라 상태 점검을 먼저. 하나라도 걸었으면 True."""
        p = it["progress"]
        before = _n(p.get("done"))
        with self.lock:
            self.current = it["id"]
        if not self._precheck(it, settings):
            if it.get("status") == "error":
                self._tick_error(it)
            return False
        self._log(it, f"빈 칸에 남은 {it['count'] - before}건을 겁니다")
        res = self._alter_register(it, settings, soft=True)
        if res == "fail" and it.get("status") == "error" and it["id"] not in self._removed:
            self._tick_error(it)
        elif (res == "ok" and it.get("status") == "waiting" and alter_mode(it.get("collect")) == "none"
              and _n(p.get("done")) >= it["count"]):
            self._done(it, f"가공 {_n(p.get('done'))}건 등록 완료 — 걸기만(수령하지 않습니다)")   # 다 걸었다 — 끝의 몫은 받지 않는다
        return _n(p.get("done")) > before

    def _wait_and_collect(self, targets: list, settings: dict) -> None:
        """대상 가공 카드가 끝날 때까지 같은 규칙(_collect_ready — 모이면 받고 · 빈 칸에 다시 걸고)으로 돌보며,
        그 사이는 남은 시간만큼(5초~) 잔다. ■ 정지를 본다.
        상한: **진척 없이** WAIT_MAX_TOTAL(30분)이 지나면 멈춘다 — 완료가 늘거나·받거나·걸면 다시 센다
        (한 번에 하나씩 300초씩 도는 가공 63건은 몇 시간이 걸린다 — 전체 30분으로 자르면 안 된다)."""
        ids = {x["id"] for x in targets}
        t_end = time.time() + WAIT_MAX_TOTAL
        last = None
        while time.time() < t_end and not self._stop.is_set():
            with self.lock:
                still = [x for x in self._awaiting_collect() if x["id"] in ids]
            if not still:
                return
            acted = self._collect_ready(settings)
            with self.lock:
                still = [x for x in self._awaiting_collect() if x["id"] in ids]
            if not still or self._stop.is_set():
                return
            rows = self._rows_now([])
            facs = {_s((x.get("progress") or {}).get("facility")) for x in still} - {""}
            names = {x["name"] for x in still}
            rel = [r for r in rows if r["facility"] in facs or r["name"] in names]
            sig = (sum(1 for r in rel if r["done"]), len(rel))
            if acted or (last is not None and sig != last):
                t_end = max(t_end, time.time() + WAIT_MAX_TOTAL)
            last = sig
            left = min([r["left"] for r in rel if not r["done"]], default=WAIT_MIN)
            for x in still:
                self._log(x, f"완료 대기 — {left}초 남음 · 완료 {sig[0]}건")
            self._stop.wait(max(0.0, min(max(float(left), WAIT_MIN), t_end - time.time())))
        # 상한에 닿았다 — **n 개가 안 모였어도 여기서는 있는 것을 받는다.** 안 그러면
        # 이미 익은 것이 「아직 n개가 아니다」는 이유로 영영 안 나온다.
        if not self._stop.is_set():
            self._collect_ready(settings, batch=False)
        with self.lock:
            still = [x for x in self._awaiting_collect() if x["id"] in ids]
        for x in still:
            if not self._stop.is_set():
                self._log(x, "진척 없이 대기 상한(30분)에 닿음 — 다음 실행 때 이어서 받습니다")

    def _wait_pending_alters(self, settings: dict) -> None:
        with self.lock:
            waiting = self._awaiting_collect()
        if waiting:
            self._event("collect", None, f"체인 끝 — 가공 {len(waiting)}건 완료 대기")
            self._wait_and_collect(waiting, settings)

    def _refresh_bag(self, it: dict) -> None:
        items = self._read(it, "get_items", None)
        if items.ok:
            store.set_cache("items", items.body)
            it["progress"]["have"] = _n(work.stock(items.body)["total"].get(it["name"]))   # 보유 = 가방+창고 합산
