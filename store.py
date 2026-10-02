"""로컬 저장소: 설정 하나만 다룬다. data/*.json, UTF-8, 원자적 쓰기.

MobiFolio store.py 와 같은 규칙:
- 모든 읽기·쓰기는 모듈 전역 RLock 아래 (ThreadingHTTPServer 가 핸들러를 동시에 돌린다).
- 임시 파일명은 스레드별로 유일, os.replace 는 공유 위반(WinError 32/5) 시 짧게 재시도.
- 못 읽는 파일(잘림·BOM·권한)은 <name>.corrupt-<ts> 로 옮겨 두고 기본값을 쓴다 → 다음 저장이 원본을 영구 덮어쓰지 않는다.
- 설정은 키별 타입·범위 강제.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
import zipfile

import datadir
import runmode   # 실행 방식 — exe · 임베디드 파이썬 · 개발 소스. 배포판 규칙은 exe 와 임베디드 판이 같다


# 데이터 위치: MOBIW_DATA_DIR(개발·테스트용 강제) > 사용자 폴더 %LOCALAPPDATA%\MobiWorks
def user_base() -> str:
    env = os.environ.get("MOBIW_DATA_DIR")
    if env and (not runmode.release() or os.environ.get("MOBIW_DEV") == "1"):   # 배포판(exe·임베디드)은 개발용 환경변수 무시
        return env
    # **검사 안에서는 실제 사용자 폴더에 절대 묶이지 않는다** (tests/__init__.py 참고).
    # unittest 가 들어와 있고 개발 환경이면, 강제 폴더가 없어도 임시 폴더로 간다.
    if not runmode.release() and "unittest" in sys.modules:
        import tempfile
        d = tempfile.mkdtemp(prefix="mobiw-guard-")
        os.environ["MOBIW_DATA_DIR"] = d
        sys.stderr.write(f"[store] 검사 중이라 사용자 폴더 대신 임시 폴더를 씁니다: {d}\n")
        return d
    la = os.environ.get("LOCALAPPDATA")
    if la:
        return datadir.migrate(la)      # 옛 이름(MobiWorkrs) 폴더가 있으면 한 번 옮긴다
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))


_BASE = user_base()
DATA_DIR = os.path.join(_BASE, "data")
LOCK = threading.RLock()


# 모비폴리오에서 온 자료는 **여기 아래**에 둔다. 이름이 겹치기 때문이다 —
# `presets.json` 이 우리 쪽은 **큐 프리셋**, 그쪽은 **연주 인사 프리셋**이다.
# 같이 두면 한쪽이 다른 쪽을 조용히 덮는다.
FOLIO = "folio/"


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


class ReadBusy(OSError):
    """파일은 있는데 잠깐 못 연다 (공유 위반·권한 — 백신·백업·동기화 도구가 잡고 있다). 내용이 깨진 것이 아니다."""


LOAD_TRIES = 6        # 못 열면 이만큼 다시 해 본다 (0.05·0.1·…초 — 모두 합쳐 1초 남짓)


def _read_bytes(p: str) -> bytes | None:
    """파일 바이트. 없으면 None. **잠깐 못 여는 것(OSError)은 몇 번 다시 해 본다** — 끝내 못 열면 ReadBusy.

    못 여는 것과 깨진 것은 다르다. 백신·백업 도구가 파일을 잠깐 잡고 있을 때 이것을 「깨짐」으로 보고
    옮겨 버리면 설정이 통째로 기본값이 된다 (1.0.7 제보: 오버레이 위치·크기 초기화)."""
    last: OSError | None = None
    for i in range(LOAD_TRIES):
        try:
            with open(p, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            last = e
            if i < LOAD_TRIES - 1:
                time.sleep(0.05 * (i + 1))
    raise ReadBusy(str(last))


def load(name: str, default, strict: bool = False):
    """파일이 없으면 default. **내용이** 깨져 있으면 .corrupt-<ts> 로 격리하고 default (원본 보존).

    **잠깐 못 여는 것은 깨짐이 아니다** — 몇 번 다시 해 보고, 끝내 못 열면 원본을 그대로 둔 채 default 를 준다.
    `strict=True` 면 그때 ReadBusy 를 던진다 — 읽은 값에 덧붙여 **다시 쓰는** 쪽(set_settings)이
    기본값으로 원본을 덮지 않게."""
    p = _path(name)
    with LOCK:
        try:
            raw = _read_bytes(p)
        except ReadBusy as e:
            print(f"[store] {name} 을 열지 못했습니다 (다른 프로그램이 잡고 있는 듯 — 파일은 그대로 둡니다): {e}", flush=True)
            if strict:
                raise
            return default
        if raw is None:
            return default
        try:
            return json.loads(raw.decode("utf-8-sig"))   # 메모장이 BOM 을 붙여도 읽힌다
        except Exception as e:     # 잘림·다른 인코딩·널바이트·너무 깊음(RecursionError) — 내용이 깨졌다
            bad = f"{p}.corrupt-{int(time.time())}"
            try:
                os.replace(p, bad)
                print(f"[store] {name} 을 읽지 못해 {os.path.basename(bad)} 로 옮겼습니다: {e}", flush=True)
            except OSError:
                pass
            return default


def _load_dict(name: str, strict: bool = False) -> dict:
    d = load(name, {}, strict=strict)
    return d if isinstance(d, dict) else {}


TMP_STALE_SEC = 3600   # 이보다 오래된 .tmp 는 죽은 저장의 잔재로 본다 (살아 있는 저장은 1초를 넘지 않는다)


def _sweep_tmp() -> None:
    """저장 도중 죽으면 <name>.<pid>.<tid>.tmp 가 남는다 — 아무도 지우지 않으면 영원히 쌓인다
    (recipes.json 은 2.5MB 라 반복되면 디스크를 먹는다). 한 시간 지난 것만 지운다: 다른 프로세스가
    지금 쓰는 중인 tmp 를 건드리면 그쪽 저장이 깨진다."""
    now = time.time()
    found = []
    for d in (DATA_DIR, os.path.join(DATA_DIR, FOLIO.rstrip("/"))):   # 한 겹 아래까지
        try:
            found += [os.path.join(d, f) for f in os.listdir(d)]
        except OSError:
            pass
    for p in found:
        if not p.endswith(".tmp"):
            continue
        try:
            if now - os.path.getmtime(p) > TMP_STALE_SEC:
                os.remove(p)
        except OSError:   # 다른 프로세스가 쓰는 중이거나 이미 사라졌다 — 다음 기회에
            pass


_swept = False


def save(name: str, obj) -> None:
    global _swept
    # **하위 폴더까지 만든다.** 폴리오 자료는 `folio/…` 로 들어오는데(FOLIO 참조),
    # DATA_DIR 만 만들면 그 첫 저장이 FileNotFoundError 로 죽는다.
    os.makedirs(os.path.dirname(_path(name)) or DATA_DIR, exist_ok=True)
    if not _swept:   # 프로세스당 한 번만 — 저장마다 폴더를 훑을 필요는 없다
        _swept = True
        _sweep_tmp()
    tmp = _path(f"{name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        last: Exception | None = None
        for i in range(6):   # 백신·백업 도구가 잠깐 잡고 있을 때 (WinError 32/5)
            try:
                os.replace(tmp, _path(name))
                return
            except PermissionError as e:
                last = e
                time.sleep(0.05 * (i + 1))
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise last if last else OSError(f"save failed: {name}")


# ── CLI 응답 캐시 ──
# CLI 는 파이프가 직렬이고 게임이 꺼져 있으면 느리다. 화면은 항상 캐시를 그리고, 갱신은 명시적으로 한다.
def get_cache(kind: str) -> dict:
    d = _load_dict(f"cache_{kind}.json")
    return {"fetched_at": d.get("fetched_at"), "data": d.get("data")}


def set_cache(kind: str, data) -> dict:
    """새 응답으로 덮기 전에 직전 캐시를 cache_<kind>.prev.json 으로 한 벌 남긴다 (직전 1회분만).
    캐시는 CLI 응답 원본이라 DB 재구성의 유일한 재료다 — 어떤 이유로 이관이 늦게 돌아도 직전 관찰은 남게."""
    cur = _path(f"cache_{kind}.json")
    prev = _path(f"cache_{kind}.prev.json")
    with LOCK:
        if os.path.exists(cur):
            tmp = f"{prev}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                shutil.copyfile(cur, tmp)
                os.replace(tmp, prev)
            except OSError as e:   # 보존 실패가 갱신을 막지는 않는다
                print(f"[store] cache_{kind}.prev.json 보존 실패: {e}", flush=True)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        d = {"fetched_at": time.time(), "data": data}
        save(f"cache_{kind}.json", d)
    return d


# ── 설정 ──
# 저장 파일에 함께 적는 스키마 버전. 나중에 키 이름·의미가 바뀔 때 이 값으로 이관 분기를 건다.
# (사용자가 고칠 수 있는 값이 아니므로 DEFAULT_SETTINGS 에 넣지 않는다 — API 패치로 덮이면 안 된다.)
SCHEMA = 1
SCHEMA_KEY = "_schema"

# **새로 설치한 사람의 업데이트 확인 주소.** 여기 한 곳만 채우면 된다.
#
# 비어 있으면 처음 받은 사람은 설정에서 주소를 손으로 붙여 넣기 전까지 **영원히 업데이트를
# 못 받는다** — 그리고 그 주소를 알려 줄 방법도 마땅치 않다. 그래서 배포 주소가 정해지면
# 반드시 여기에 박는다. 사용자는 `update_check` 체크박스로 끄거나 주소를 바꿀 수 있다.
#
# 도메인이 아직 없어서 비워 둔다. **`release.py` 가 이게 빈 채로 릴리스되는 것을 막는다** —
# 조용히 업데이트가 죽는 판을 내보내는 것이 제일 나쁘기 때문이다.
DEFAULT_UPDATE_URL = "https://wo.mobimml.com/latest.json"   # 1.0.0 부터 배포 사이트

DEFAULT_SETTINGS = {
    "cli_exe": "",            # 비우면 기본 설치 경로 사용 (C:\\Nexon\\MabinogiMobile\\MabinogiMobile_CLI.exe)
    "auto_sync": True,        # 시작 시 CLI 가 연결돼 있으면 자동 갱신
    "work_poll_sec": 30,      # 가공 대기열 갱신 주기(초). CLI 는 직렬이라 너무 짧으면 UI 가 굳는다
    # 경량판 자동 업데이트: latest.json 주소 (https). 비우면 확인하지 않는다.
    # 값은 위의 DEFAULT_UPDATE_URL 한 곳에서 온다 — 여기 또 적지 않는다.
    "update_url": DEFAULT_UPDATE_URL,
    "update_check": True,     # 시작 시 업데이트 확인
    # 제작·채집 큐 (workqueue.py)
    "queue_weight_margin": 30,   # 채집 매 회 전에 「최대 무게 - 이 값」에 닿았으면 멈춘다
    "queue_max_passes": 50,      # 한 항목이 실행 명령을 부를 수 있는 최대 회수 (채집은 회당 100개)
    # 날개 차단기 (workqueue). 창 길이(10분·5분)는 상수다
    "wing_cap_total": 150,       # 10분 안에 이 수를 **넘게** 날개를 쓰면 큐를 강제 정지 (150 = 30회)
    "wing_cap_waste": 4,         # 5분 안에 산출 없이 날개만 쓴 호출이 이 횟수에 닿으면 강제 정지
    "wing_cap_window_min": 10,   # 총량 상한을 재는 창(분) — 사용자가 바꿀 수 있고, 이 값이 초기값이다
    "wing_cap_waste_min": 5,     # 헛소모 상한을 재는 창(분)
    # 채집 퀘스트 지킴이
    #   off    아무것도 안 한다 (**기본**)
    #   watch  퀘스트가 끊겼는지 보고 **말만** 한다
    #   resume 끊겼으면 **다시 건다** — 새 execute_gathering 이고 **날개가 든다**
    # 기본이 off 인 까닭: 「누르기」에 해당하는 명령이 CLI 에 없어서, 재개는 실행을 새로
    # 부르는 것뿐이고 오작동하면 날개를 태운다. **사람이 켤 때만 켠다.**
    "gather_quest_watch": "off",
    "gather_quest_retry_max": 3,   # 「다시 걸기」를 연속으로 몇 번까지 — 닿으면 멈추고 알린다
    # 가공 완료가 n개 이상 모이면 받으러 가기 (N6, 1 ~ 시설 칸 수 7). 수령은 날개 0 이지만 시설까지 이동이라
    # 도는 작업(채집 등)을 끊는다 — 그래서 모았다 간다. 남은 건이 n 보다 적으면 그 카드의 작업이 다 끝났을 때 받는다.
    # **날개는 n 과 무관하다.** 예전 「전부 끝나면 한 번에 수령」(alter_batch_collect) 스위치를 대신한다.
    "alter_collect_at": 7,
    # ── 밖에서 접속 (모바일) ──
    # **기본은 전부 꺼져 있다.** 켜는 것은 사람이 이 PC 에서 한다.
    "remote_on": False,          # 밖에서 들어올 길을 여는가 (터널 + 인증)
    "remote_host": "",           # 사람이 적어 둔 주소 하나 (터널이 준 …trycloudflare.com). 이것만 받는다
    "remote_origin": "",         # 폰 화면을 올려 둔 사이트 (CORS 를 열어 줄 곳). 한 곳만
    # 범위: read(보기만) < edit(큐 편집 — 값이 안 든다) < run(실행 — **정령의 날개를 쓴다**)
    # 기본이 read 인 이유: 실행 1회 = 날개 5개이고 되돌릴 수 없다. 모비폴리오는 기본이
    # 「재생 조작」이지만 그쪽은 잘못 틀면 멈추면 그만이다. 우리는 재화가 준다.
    "remote_scope": "read",
    "remote_idle_min": 30,       # 마지막 원격 요청 뒤 이만큼 지나면 터널을 닫는다 (0 이면 안 닫음)
    # **인증키는 밖으로 나가는 문의 유일한 자물쇠다.** 여기 평문으로 있지만, 이 파일을 읽을 수
    # 있는 사람은 이미 이 PC 안에 있다. 절대 API 응답에 실어 보내지 않는다 (server.py 가 지운다).
    "remote_key": "",
    "queue_precheck": True,      # 항목 실행 전 get_activity 로 사망·전투·대화·던전 상태를 보고 막힌 상태면 실행 명령을 부르지 않는다
    # 연주 중일 때 큐가 무엇을 할지 — 제작을 우선할지 연주를 이어 갈지 사용자가 고른다.
    # music = 연주 계속(재생목록이 끝날 때까지) · song = 이 곡 끝나면
    # 양보(지금 곡만 마친다) · work = 제작 우선(지금 바로).
    # **기본은 music** — 사람이 고르지 않은 상태에서 남의 음악을 끊지 않는다.
    # 연주는 날개를 쓰지 않아 기다려서 잃는 것은 시간뿐이고, 반대는 되돌릴 수 없다.
    # 순서는 **점검 먼저, 정지는 그 다음** (workqueue._on_performance).
    # **queue_precheck 를 끄면 이것도 같이 꺼진다** — 연주 상태는 그 점검이 부르는
    # get_activity 한 번에 같이 들어 있어서다. 점검을 껐다는 것은 「조회 없이 바로 실행」을
    # 고른 것이고, 그러면 연주도 볼 수 없다.
    "queue_on_performance": "music",
    # 큐가 끝나면 알림 — on 이면 보드가 끝날 때(사용자 ■ 정지 제외) 알림 카드와 같은 길로
    # 「큐가 끝났습니다 — 완료 n · 실패 m」 을 낸다 (PC 밴드 토스트 · 앱 창 토스트 · 폰). 값이 안 드는 설정이라 밖(edit)에서도 바꾼다.
    "queue_done_notify": "on",
    # 게임 오버레이 (overlay.py). 밴드는 tkinter 창이라 화면 설정과 따로 논다.
    "overlay_enabled": False,       # 밴드 켜기. 기본 꺼짐 — 창은 사용자가 켤 때만 뜬다
    "overlay_show_queue": True,     # 표시 항목: 큐 진행 (2/5)
    "overlay_show_name": True,      # 표시 항목: 종류 배지 + 현재 항목 이름 + 진행
    "overlay_show_columns": True,   # 표시 항목: 대기 · 완료 · 실패 개수
    "overlay_show_round": True,     # 표시 항목: 2/3회차
    "overlay_show_alter": True,     # 표시 항목: 가공 대기 타이머 · 완료 수
    "overlay_show_collect": True,   # 표시 항목: 「일괄 수령」 버튼
    "overlay_show_error": True,     # 표시 항목: 오류 한 줄
    "overlay_show_elapsed": False,  # 표시 항목: 경과 시간 (디자인 1d 에서 꺼져 있는 항목)
    "overlay_show_wings": False,    # 표시 항목: 예상 소모(정령의 날개) — 실행 줄의 「예상 소모」와 같은 값
    "overlay_follow_game": True,    # 게임 창(클라이언트 영역)에 맞춰 자리를 잡고 따라간다. ⋮⋮ 로 끌면 꺼진다
    # 배경 투명도 스타일: glass(반투명 글래스) | solid(불투명 솔리드). **기본은 solid.**
    # 설계는 backdrop-filter: blur(6px) 를 요구하지만, DWM 배경 흐림을 WS_EX_LAYERED(LWA_ALPHA) 창에 걸면
    # 창이 아예 렌더되지 않는 것을 실기에서 확인했다 — 위치·크기·알파(255)·Z 순서가 전부 정상인데
    # 그 좌표를 화면 캡처하면 한 픽셀도 그려져 있지 않았다. 55%↔100% 전환이 설계의 핵심이라
    # 알파를 포기할 수 없으므로 흐림을 뺀다. glass 는 고를 수는 있게 남긴다.
    "overlay_backdrop": "solid",
                                    # 흐림을 못 거는 기계에서는 알아서 솔리드로 보인다 (조용히)
    # 밴드 불투명도. **기본 100.** 디자인은 `rgba(9,12,17,.82)` 로 **배경만** 82% 지만,
    # 창 알파(SetLayeredWindowAttributes)는 창 전체에 균일하게 걸려 **글자까지 같이 비친다** —
    # 게임 글자가 밴드를 뚫고 올라와 읽을 수가 없었다.
    "overlay_opacity": 100,          # 평소 불투명도 % (20~100). 사건 때는 자동 100%
    "overlay_flash": True,          # 완료·오류·회차 전환에 1.5초 100% 후 복귀
    "overlay_expand": "off",        # 펼침 기본값: off(접힘) | on(펼침) | error(오류 때만)
    # 밴드의 ▾ 로 **마지막에 손으로** 열고 닫은 상태: ""(없음 — 펼침 기본값을 따른다) | on | off.
    # 다음 실행에 이 상태로 뜬다. 「펼침 기본값」을 바꾸면 "" 로 지워진다 (바꾼 값이 다음 손 조작 전까지 기준).
    # 탭에 줄이 없는 값이다 — 밴드만 쓴다 (`overlay.Overlay._remember_expand`).
    "overlay_expand_last": "",
    "overlay_auto_collect": False,  # 가공 완료를 보면 수령 항목을 자동으로 큐에 담는다 (실행은 사용자가)
    "overlay_click_through": True,  # 클릭 통과. 「일괄 수령」·⋮⋮·▾ 만 예외로 눌린다
    "overlay_lock": False,          # 위치 잠금 (끄면 ⋮⋮ 를 끌어 옮긴다)
    "overlay_toast": "error",       # 오류 토스트: on(오류+회차·완료) | error(오류만) | off
    # 큐가 멈추면 밴드 숨김. **기본은 꺼짐** — 켜짐이 기본이면 사용자가 「오버레이 밴드」를 켰는데
    # 큐가 안 돌아 아무것도 안 나타나고, 그게 「오버레이가 안 켜진다」로 읽힌다 (실제로 그렇게 읽혔다).
    "overlay_hide_idle": False,
    "overlay_x": 250,               # 게임을 못 찾았을 때 쓰는 화면 좌표 (창을 보이기 전 자리잡기용)
    "overlay_y": 14,
    # 끌어 둔 자리는 **게임 클라이언트 영역 기준 오프셋**으로 저장한다 — 절대 좌표로 두면 게임 창이
    # 움직였을 때 따라갈 수 없다. 옛 overlay_x/y 와 뜻이 다르므로 **새 키**를 쓴다 (이관하지 않는다).
    # 본 적 있는 가공 시설과 그 최대 칸 수 {"금속": 4, ...}. **관찰로 배운다** —
    # CLI 는 등록된 작업만 주므로 가공기가 비면 시설 이름조차 모른다 (overlay.learn_facilities).
    "alter_seen": {},
    # 본 적 있는 가공 시설과 그 최대 칸 수 {"금속": 4, ...}. **관찰로 배운다** —
    # CLI 는 등록된 작업만 주므로 가공기가 비면 시설 이름조차 모른다 (overlay.learn_facilities).
    "alter_seen": {},
    # 가공 한 회에 걸리는 시간 {"산뜻 버섯 진액": 1800, ...}. 이것도 **관찰로 배운다** —
    # CLI 는 남은 시간만 주고 총 시간을 안 준다. 본 것 중 가장 큰 남은 시간이 곧 한 회다.
    "alter_dur": {},
    "overlay_off_x": 0,
    "overlay_off_y": 0,

    # ── 아래는 모비폴리오에서 가져온 설정 ──
    # **겹치는 11개는 우리 것을 남겼다**: auto_sync, cli_exe, remote_host, remote_key, remote_on, remote_origin, remote_scope, update_check, update_url.
    # 그중 `remote_scope` 는 값의 뜻 자체가 다르다 (우리 read/edit/run · 그쪽 play/full).
    # 범위를 하나로 합치는 것은 `server.py` 를 옮길 때 한다 — 지금 섞으면 **조용히 넓어진다.**
    "gap_sec": 2,             # 곡 사이 대기(초)
    "advance_margin": 0.0,    # 반복 설정이라 곡이 스스로 안 끝날 때 끝에서 몇 초 앞당겨 끊을지 — 기본 0 = 끝나는 시각에 맞춰
    "default_inst": "",       # 기본 악기 ("" = 악기 그대로)
    # 악기 고정 — 비어 있지 않으면 곡·기본 악기와 상관없이 **이 악기로** 튼다.
    # 오버레이 악기 고르기의 「장착」 옆 고정 단추 · 미니 악기 시트의 같은 단추가 적는다.
    "inst_pin": "",
    "stop_before_play": True, # 재생 전 현재 연주를 먼저 정지
    # 탈것이면 날개 5 로 내리고 재생 — 바로 제작 하나를 걸었다가 곧바로 정지해 탈것에서 내린다
    # (재료는 안 쓰고 날개 5 가 든다 · 실측, docs/CLI.md §4). **기본 꺼짐** — 날개가 든다.
    "folio_dismount_by_craft": False,
    # 폴리오 밴드(게임 위 연주 창)를 띄울지. **기본 꺼짐** — 창은 사람이 켤 때만 뜬다.
    # 우리 밴드(`overlay_enabled`)와 **따로** 산다: 둘 다 켤 수 있게 두고,
    # 겹치는 자리는 나중에 맞춘다 (둘 다 top 14 가운데로 시작해 포개진다 — 끌어서 옮길 수 있다).
    "overlay_folio": False,
    "ov_scale": 100,          # 오버레이 크기 %
    "ov_alpha": 85,           # 오버레이 배경 불투명도 %
    "repeat": "off",          # 반복: off · one(한 곡) · all(전체). **미니와 오버레이가 같은 값을 쓴다**
    "shuffle": False,         # 셔플: 켠 순간 지금 목록을 한 번 섞어 그 차례로 간다
    "opening": True,          # 연주를 시작하면(그리고 합주로 인식되면) 게임 화면 위아래에 시작 연출을 한 번 띄운다
    # 연출을 무엇으로 그리나 — "web": WebView2 카드 연출(ui/folio/opening2.html) ·
    # "tk": 예전 띠 두 개. web 이 안 되면(로더·런타임 없음 등) 저절로 tk 로 물러난다 (folio/opening_wv.py).
    "opening_engine": "web",
    # 웹 연출의 **테마 팩** — "card"(카드형, 기본) · "film"(필름형) · "ticket"(티켓형) · "phone"(쇼츠형). 팩은 ui/folio/themes/<이름>/
    # 기본은 카드형이고 설정에서 고른다.
    # 연출 페이지(opening2.html)가 연출마다 다시 읽어 갈아 끼운다 — 앱을 다시 켤 필요가 없다.
    "opening_theme": "card",
    # 연주 인사 (A안) — 채팅·행동·표정은 **주변 사람에게 그대로 보인다.** 그래서 기본은 꺼짐이다.
    "greet_on": False,        # 인사 기능 전체. 켜야 게임에 실제로 나간다
    "greet_lead": 8.0,        # 재생을 누르고 실제 연주까지(초). 그 사이에 인사·행동·표정·시작 연출이 들어간다
    "greet_step": 1.0,        # 인사말 → 행동 → 표정 사이 간격(초). 채팅 속도 제한을 피하는 간격이기도 하다
    # 끝 인사 시점 — **양수는 곡이 끝나기 n초 전, 음수는 끝난 뒤 n초, 0 은 끝난 직후**
    "greet_end_lead": 0.0,
    "greet_listen": False,    # 남의 연주가 끝나면 자동으로 인사
    "greet_listen_gap": 30.0,  # 그 자동 인사의 최소 간격(초) — 도배 방지
    # 밖에서 접속(터널) — **기본 꺼짐.** 켜면 그 주소를 아는 사람이 캐릭터를 조종할 수 있으므로
    # **주소와 인증키가 둘 다 맞아야** 들어올 수 있다. 인증키는 앱이 발행한다(아래 issue_remote_key).
    # 폰 앱(PWA)을 올려 둔 사이트 주소. 그 사이트에서 터널로 부르는 것은 **다른 출처**라
    # 브라우저가 미리 물어본다(CORS) — 여기 적힌 곳에만 열어 준다. 비면 안 열린다.
    "ensemble": True,         # 합주 인식: 주변에 같은 곡을 다채널로 연주하는 사람이 있거나 내 보관함에 없는 곡을 연주 중이면, 상대 곡 길이에 맞춰 끝낸다
    # 화면 테마 — 두 앱(작업·폴리오) 공통. auto = OS 설정(밝게·어둡게)을 따른다.
    # 서버가 화면을 낼 때 dark·light 면 `<html data-theme>` 을 박는다 (server.render_index). 폰은 기본으로 이 값을 따르고,
    # 폰에서 고르면 그 폰에만 적용된다 (ui/js/theme.js). 게임 위 화면(연출·오버레이)은 늘 다크 — 이 값과 상관없다.
    "ui_theme": "auto",
    # 방송 모드 (broadcast.py) — 방송 창 「방송 설정」 시트의 값과 마지막으로 공개한 재생목록
    "bc_interval_min": 3,     # 1인 신청 간격(분) — 한 손님이 다음 곡을 신청하기까지
    "bc_cap": 20,             # 대기 신청 상한 — 닿으면 손님 쪽 신청이 잠긴다
    "bc_auto": False,         # 자동 승인 — **기본 꺼짐** (켜면 신청이 확인 없이 현재 재생목록 끝에 들어간다)
    "bc_pl": "",              # 공개 목록(재생목록 id) — 비면 첫 재생목록
}
_RANGES = {
           # 모비폴리오에서 가져온 것 (그쪽 값 그대로)
           "gap_sec": (0, 60), "advance_margin": (0, 30), "ov_scale": (50, 250),
           "ov_alpha": (10, 100), "greet_lead": (0, 60),
           "greet_step": (0.3, 10), "greet_end_lead": (-60, 60), "greet_listen_gap": (5, 600),
           "work_poll_sec": (10, 600),   # 10초 미만은 CLI 직렬 파이프를 막는다
           "queue_weight_margin": (0, 200), "queue_max_passes": (1, 500), "gather_quest_retry_max": (0, 20),
           "alter_collect_at": (1, 7),   # 칸 수(실측 7)가 상한 — 그 시설이 기억한 칸 수가 더 적으면 러너가 거기서 또 자른다
           "wing_cap_total": (50, 1000), "wing_cap_waste": (2, 20),
           "wing_cap_window_min": (1, 120), "wing_cap_waste_min": (1, 120),
           "overlay_opacity": (20, 100), "overlay_x": (-32000, 32000), "overlay_y": (-32000, 32000),
           "overlay_off_x": (-32000, 32000), "overlay_off_y": (-32000, 32000),
           "remote_idle_min": (0, 720),
           "bc_interval_min": (1, 30), "bc_cap": (1, 99)}
# 값이 정해진 문자열 설정. 목록에 없는 값은 버린다(기본값 유지) — 오타가 조용히 저장되면 화면이 이상하게 돈다.
_ENUMS = {"opening_engine": ("web", "tk"),
          # 팩 이름은 폴더 이름이자 연출 페이지가 싣는 파일 경로(`/folio/themes/<이름>/…`)다 — 목록 밖의 값은 버린다
          # (연출 페이지의 THEMES 와 같아야 한다 · tests/test_opening_themes.py)
          "opening_theme": ("card", "film", "ticket", "phone", "random"),   # random = 연출마다 넷 중 하나를 뽑는다 (팩 이름이 아니다)
          "overlay_expand": ("off", "on", "error"), "overlay_expand_last": ("", "on", "off"), "overlay_toast": ("on", "error", "off"),
          "overlay_backdrop": ("solid", "glass"),
          # 목록에 없는 값이 저장되면 **범위가 넓어질 수 있다** — 오타 하나로 「run」이 되면 안 된다.
          # 여기 없는 값은 버려져 기본값(read)이 남는다.
          "remote_scope": ("read", "edit", "run"),
          # music·song·work. 오타가 저장되면 **남의 음악을 끊는 쪽**으로
          # 넘어갈 수 있으니 여기서 막는다 — 목록에 없는 값은 버려지고 기본값(music)이 남는다.
          "queue_on_performance": ("music", "song", "work"),
          "gather_quest_watch": ("off", "watch", "resume"),
          "queue_done_notify": ("on", "off"),
          # 모비폴리오: 반복 — off · one(한 곡) · all(전체)
          "repeat": ("off", "one", "all"),
          "ui_theme": ("auto", "dark", "light")}


def _coerce(k: str, v):
    """키별 타입 강제. 이상하면 None(→ 기본값 유지)."""
    d = DEFAULT_SETTINGS[k]
    if isinstance(d, bool):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return None
    if isinstance(d, (int, float)):
        if isinstance(v, bool):
            return None
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if x != x:   # NaN
            return None
        lo, hi = _RANGES.get(k, (-1e9, 1e9))
        x = min(max(x, lo), hi)
        return int(x) if isinstance(d, int) and x == int(x) else x
    if isinstance(d, dict):
        # 관찰로 배우는 표 (alter_seen·alter_dur). **키는 글자, 값은 0 이상 정수**만 받는다 —
        # 설정 파일은 사용자가 손댈 수 있는 곳이라 모양을 믿고 쓰면 안 된다.
        # 이 갈래가 없으면 dict 설정이 **조용히 저장되지 않는다** (alter_seen 이 늘 비어 있었다).
        if not isinstance(v, dict):
            return None
        out = {}
        for kk, vv in list(v.items())[:200]:
            if not isinstance(kk, str) or not kk.strip():
                continue
            try:
                n = int(vv)
            except (TypeError, ValueError):
                continue
            if 0 <= n <= 10 ** 7:
                out[kk.strip()[:80]] = n
        return out
    if isinstance(d, str):
        if not isinstance(v, str):
            return None
        v = v.strip()
        allowed = _ENUMS.get(k)
        return v if (allowed is None or v in allowed) else None
    return None


# 없앤 기능의 설정 키 — 옛 settings.json 에 남아 있을 수 있다. 읽을 때는 DEFAULT_SETTINGS 에 없어 어차피
# 안 쓰이고, 저장할 때 걷어 내고(set_settings), 백업에 넣지 않고, 복원 파일에 있어도 적용하지 않는다.
#   opening_video · opening_video_path   「연출 영상」(사용자 영상 고르기) — 없앤 기능.
#                                         경로에 사용자 이름이 들어 있으므로 백업으로 따라 나가면 안 된다.
#   alter_batch_collect                   「가공은 전부 끝나면 한 번에 수령」 스위치 — alter_collect_at(n개 모이면 수령)이 대신한다 (N6)
RETIRED_SETTINGS = ("opening_video", "opening_video_path", "ov_nearby", "ov_banner_sec", "alter_batch_collect")


_last_settings: dict | None = None   # 마지막으로 제대로 읽은 설정 — 파일을 잠깐 못 열 때 기본값 대신 이것을 준다


def get_settings() -> dict:
    """설정. **파일을 잠깐 못 열면(ReadBusy) 마지막으로 읽은 값**을 준다 — 기본값을 주면 그 순간
    오버레이가 꺼지고 위치가 0,0 으로 돌아간다(1.0.7 제보). 처음부터 못 열었으면 기본값이다."""
    global _last_settings
    with LOCK:
        try:
            d = _settings_from(_load_dict("settings.json", strict=True))
        except ReadBusy:
            return dict(_last_settings) if _last_settings is not None else dict(DEFAULT_SETTINGS)
        _last_settings = dict(d)
        return d


def _settings_from(raw: dict) -> dict:
    d = dict(DEFAULT_SETTINGS)
    for k, v in raw.items():
        if k in DEFAULT_SETTINGS:
            c = _coerce(k, v)
            if c is not None:
                d[k] = c
    # 옛 테마 옮기기 — `ui_theme` 이 생기기 전에는 테마가 화면 편의 값(`ui.prefs["mobiworks.theme"]`)에 있었다.
    # 설정에 `ui_theme` 이 한 번도 적힌 적 없을 때만 그 값을 읽는다. 다음 저장(set_settings)에서 `ui_theme` 으로 적힌다.
    if "ui_theme" not in raw:
        ui = raw.get(UI_KEY)
        old = ui.get("prefs", {}).get(THEME_LEGACY_PREF) if isinstance(ui, dict) and isinstance(ui.get("prefs"), dict) else None
        if old in ("dark", "light"):
            d["ui_theme"] = old
    return d


def settings_ahead() -> int:
    """저장된 설정의 스키마가 이 앱보다 높으면 그 값, 아니면 0. 높으면 쓰지 않는다 — 모르는 키를 지우면
    새 버전이 저장한 설정이 옛 앱을 거칠 때마다 깎인다(강등). recipes.json 과 같은 규칙."""
    return _ahead_of(_load_dict("settings.json"))


def _ahead_of(raw: dict) -> int:
    v = raw.get(SCHEMA_KEY)
    n = v if isinstance(v, int) and not isinstance(v, bool) else 0
    return n if n > SCHEMA else 0


def set_settings(patch: dict) -> dict:
    global _last_settings
    with LOCK:
        # **한 번만, 엄격하게 읽는다.** 잠깐 못 연 파일을 빈 설정으로 보고 덧붙여 쓰면 사용자의 설정 전부가
        # 기본값으로 덮인다 — 그때는 저장하지 않고 ReadBusy 를 올린다 (원본은 그대로 남는다).
        raw = _load_dict("settings.json", strict=True)
        ahead = _ahead_of(raw)
        if ahead:
            print(f"[store] settings schema {ahead} > {SCHEMA} — 저장하지 않습니다 (앱을 업데이트하세요)", flush=True)
            return _settings_from(raw)
        d = _settings_from(raw)
        for k, v in (patch or {}).items() if isinstance(patch, dict) else []:
            if k in DEFAULT_SETTINGS:
                c = _coerce(k, v)
                if c is not None:
                    d[k] = c
        # 모르는 키는 지우지 말고 그대로 넘긴다 — 새 버전이 추가한 설정이 옛 앱을 한 번 거치면 사라진다.
        # 단 **없앤 기능의 키**(RETIRED_SETTINGS)는 이때 걷어 낸다 — 옛 파일에 남은 이 PC 의 경로가 끝없이 따라다니지 않게.
        keep = {k: v for k, v in raw.items()
                if k not in DEFAULT_SETTINGS and k != SCHEMA_KEY and k not in RETIRED_SETTINGS}
        save("settings.json", {SCHEMA_KEY: SCHEMA, **keep, **d})
        _last_settings = dict(d)
        return d


# ── 화면 상태: 마지막에 보던 곳 · 화면 편의 값 ─────────────────────────
# `settings.json` 의 `ui` 칸에 둔다. DEFAULT_SETTINGS 에 넣지 않는 이유: 설정 화면이 고치는 값이
# 아니고, `/api/settings` 패치로 덮이면 안 된다. set_settings 는 모르는 키를 그대로 넘기므로
# (위 `keep`) 설정을 저장해도 이 칸은 남는다.
#
# **왜 서버에 두나.** 경량판은 실행마다 **빈 포트**를 새로 잡는다(server.PORT). localStorage 는
# 출처(주소+포트)마다 따로라서, 창 프로필이 남아 있어도 **다음 실행에는 빈 저장소**를 본다.
# 테마·레일 접힘·고정 재화가 매번 처음으로 돌아가던 까닭이 이것이다.
UI_KEY = "ui"
# 작업 화면(`/`)의 탭 — ui/js/main.js MWTABS 와 같은 이름. 여기 없는 것은 주소에 싣지 않는다.
UI_TABS = ("queue", "works", "stock", "dict", "overlay", "settings")
# 서버로 옮겨 두는 localStorage 열쇠. **여기 적힌 것만** 받는다 (모르는 열쇠·긴 값은 버린다).
UI_PREF_KEYS = (
    # 테마(`mobiworks.theme`)는 **여기 없다** — 이제 설정 `ui_theme` 이 기준이다.
    # 화면 편의 값으로 두면 값이 둘이 된다. 옛 값은 get_settings 가 한 번 옮겨 읽는다 (THEME_LEGACY_PREF).
    "mobiworks.rail",           # 레일 펼침/접힘 — ui/js/shell.js
    "mobiworks.seen.works",     # 레일: 작업 앱에서 마지막으로 보던 주소 — ui/js/shell.js
    "mobiworks.seen.folio",     # 레일: 연주 앱에서 마지막으로 보던 주소 — ui/js/shell.js
    "mw.boardSeg",              # 큐 보드 좁은 폭의 세그먼트 (대기/완료/실패) — ui/js/board.js
    "mw.evOpen",                # 큐 보드 회신 기록 펼침 — ui/js/board.js
    "mw.worksBand",             # 가공 탭 대기열 밴드 보기 — ui/js/works.js
    "mw.currencyPins",          # 재고 탭 고정 재화 — ui/js/tab-stock.js
)
THEME_LEGACY_PREF = "mobiworks.theme"   # 옛 테마 자리 (화면 편의 값) — get_settings 가 한 번 옮겨 읽는다
UI_PREF_MAX = 2000              # 값 하나의 최대 길이 (글자)


def norm_page(raw) -> str:
    """화면이 알려 준 주소 → 다음 실행에 열 주소. 모르는 것은 전부 `/`.

    - 연주 쪽(`/folio/…`)은 앱 첫 화면 `/folio/` 로 모은다 (설정·인사 화면은 들렀다 가는 곳이다).
    - 작업 쪽(`/`, `/index.html`)은 해시의 탭만 남긴다: `/#stock`. 탭 뒤의 `?q=…` 는 버린다.
      `queue`(기본 탭)는 그냥 `/`.
    - 다른 사이트·다른 경로·이상한 글자는 `/` — 이 값은 창 주소에 그대로 붙는다."""
    if not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    path, _, frag = raw.partition("#")
    path = path.split("?", 1)[0]
    if ".." in path.split("/"):
        return "/"
    if path == "/folio" or path.startswith("/folio/"):
        return "/folio/"
    if path not in ("/", "/index.html"):
        return "/"
    tab = frag.split("?", 1)[0]
    return f"/#{tab}" if tab in UI_TABS and tab != "queue" else "/"


def page_known(raw) -> bool:
    """우리가 내주는 화면 주소인가 (`/`·`/index.html`·`/folio/…`). 아니면 화면 보고를 **받지 않는다** —
    모르는 주소를 `/` 로 바꿔 적으면 보던 곳을 엉뚱하게 덮는다."""
    if not isinstance(raw, str) or len(raw) > 2000:
        return False
    if norm_page(raw) != "/":
        return True
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return False
    return raw.split("#", 1)[0].split("?", 1)[0] in ("/", "/index.html")


def page_app(page) -> str:
    """주소 → 앱 이름 (`folio` | `works`)."""
    return "folio" if norm_page(page) == "/folio/" else "works"


def _prefs_clean(d) -> dict:
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items()
            if k in UI_PREF_KEYS and isinstance(v, str) and len(v) <= UI_PREF_MAX}


def get_ui() -> dict:
    """`{"last_page": "/…", "prefs": {열쇠: 값} | None}`.

    prefs 가 None 이면 **한 번도 적힌 적 없음** — 그때는 화면의 localStorage 를 건드리지 않는다
    (이 판으로 올린 첫 실행에 개발 서버처럼 포트가 같은 곳의 값을 지우지 않게)."""
    raw = _load_dict("settings.json").get(UI_KEY)
    raw = raw if isinstance(raw, dict) else {}
    lp = raw.get("last_page")
    return {"last_page": norm_page(lp) if isinstance(lp, str) else "/",
            "prefs": _prefs_clean(raw["prefs"]) if isinstance(raw.get("prefs"), dict) else None}


def set_ui(page=None, prefs=None) -> dict:
    """주어진 것만 바꾼다. page 는 norm_page 를 거치고, prefs 는 **통째로** 갈아 끼운다
    (화면이 지운 열쇠 = 여기서도 없는 열쇠. 테마 「자동」이 키를 지우는 것으로 표현된다).
    settings.json 의 다른 칸은 그대로 둔다."""
    with LOCK:
        raw = _load_dict("settings.json", strict=True)   # 잠깐 못 열었으면 덮어쓰지 않는다
        ui = dict(raw.get(UI_KEY)) if isinstance(raw.get(UI_KEY), dict) else {}
        if page is not None:
            ui["last_page"] = norm_page(page)
        if prefs is not None:
            ui["prefs"] = _prefs_clean(prefs)
        raw[UI_KEY] = ui
        save("settings.json", raw)
    return get_ui()


# ── 밖에서 접속: 인증키와 기기 ────────────────────────────────────────────────
# 헷갈리는 글자(I·O·0·1)는 뺀다 — 사람이 폰에 옮겨 적다가 틀리면 잠금까지 간다.
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REMOTE_KEY_LEN = 8            # 발행할 때 길이
REMOTE_KEY_MIN = 8            # 사람이 직접 정할 때의 최소 (모비폴리오는 6 — 우리는 재화가 걸려 올렸다)
REMOTE_KEY_MAX = 32
# 사람이 정할 때 쓸 수 있는 글자 — 영문 대문자·숫자 (`norm_remote_key` 가 대문자로 바꾸고 빈칸·붙임표를
# 뺀 **뒤에** 잰다). 발행하는 키의 글자(_KEY_ALPHABET)는 이것의 부분집합이라 발행한 키는 늘 통과한다.
# **이 규칙 하나를 세 곳이 같이 쓴다**: 여기 · 설정 화면의 「직접 정하기」 칸(ui/index.html·remote.js) ·
# 폰 사이트의 잇기 화면. 검사(tests/test_remote_link.py)가 셋을 대조한다 — 한쪽만 7자로 남아 8자 키를
# 못 넣던 일이 실제로 있었다.
REMOTE_KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
# 직접 정한 키의 **최소 다양성**. 길이·글자만 재면 「AAAAAAAA」「11111111」이 통과한다 — 어깨너머로
# 한 번 보면 끝이고 추측 목록의 맨 앞이다. 서로 다른 글자가 이만큼은 섞여야 한다 (발행 키는 늘 통과한다 —
# 32자 알파벳에서 8자를 뽑아 4종 미만일 확률은 십만분의 일 아래).
REMOTE_KEY_DISTINCT = 4


def norm_remote_key(raw) -> str:
    """사람이 적은 키를 저장 모양으로. 대문자 · 사이의 빈칸과 붙임표는 뺀다."""
    return raw.strip().upper().replace("-", "").replace(" ", "") if isinstance(raw, str) else ""


def get_remote_key() -> str:
    return str(get_settings().get("remote_key") or "")


def has_remote_key() -> bool:
    return bool(get_remote_key())


def issue_remote_key() -> str:
    """새 인증키를 발행해 저장하고 돌려준다. **앞의 키는 그 자리에서 못 쓰게 된다.**"""
    while True:
        key = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(REMOTE_KEY_LEN))
        if len(set(key)) >= REMOTE_KEY_DISTINCT:   # 발행한 키도 직접 정한 키와 같은 규칙을 지킨다
            break
    set_settings({"remote_key": key})
    return key


def set_remote_key(raw) -> tuple:
    """사람이 직접 정한 키를 저장한다. 돌려주는 것: (된 것인가, 안 되면 까닭).

    앱이 뽑아 주는 것만 쓰게 하지 않는 이유 — 외우기 쉬운 값을 쓰고 싶어 한다.
    대신 **너무 짧은 것은 막는다.** 밖으로 열리는 문의 유일한 자물쇠다.
    """
    key = norm_remote_key(raw)
    if not key:
        return False, "인증키를 적어 주세요."
    if len(key) < REMOTE_KEY_MIN:
        return False, f"인증키는 {REMOTE_KEY_MIN}자 이상이어야 합니다."
    if len(key) > REMOTE_KEY_MAX:
        return False, f"인증키는 {REMOTE_KEY_MAX}자까지입니다."
    if not all(c in REMOTE_KEY_CHARS for c in key):
        return False, "영문과 숫자만 쓸 수 있습니다."
    if len(set(key)) < REMOTE_KEY_DISTINCT:
        return False, f"너무 단순합니다 — 서로 다른 글자를 {REMOTE_KEY_DISTINCT}개 이상 섞어 주세요."
    set_settings({"remote_key": key})
    return True, ""


def clear_remote_key() -> None:
    set_settings({"remote_key": ""})


def check_remote_key(raw) -> bool:
    """맞는 인증키인가. **발행해 두지 않았으면 무조건 거짓이다** — 빈 키로 들어오지 못하게."""
    want = get_remote_key()
    if not want or not isinstance(raw, str):
        return False
    # **bytes 로 잰다.** `compare_digest` 는 비ASCII str 에 TypeError 를 던진다 — 밖에서 「Ä…」 한 글자로
    # 500 이 났고, 그 시도는 **틀린 횟수로 안 세어져** 잠금을 비켜 갔다. 시간 안전성은 bytes 도 같다.
    return hmac.compare_digest(norm_remote_key(raw).encode("utf-8"), want.encode("utf-8"))


# 짝지어 둔 기기 — 인증키로 한 번 인사를 트고 나면 그 기기에 **긴 토큰**을 준다.
# 쪽지(12시간)가 만료돼도 이 토큰으로 조용히 다시 들어온다. 기기별로 끊을 수 있다.
DEVICES_MAX = 20
# **안 쓴 지 이만큼 된 기기 토큰은 만료다.** 잃어버린 폰·버린 브라우저에
# 남은 토큰이 영원히 열쇠로 남지 않게 한다. 만료된 기기는 목록에 「만료」로 남고(「모든 기기 끊기」로 지운다),
# 그 토큰으로는 못 들어온다 — 인증키로 다시 짝지으면 새 토큰을 받는다.
REMOTE_DEVICE_TTL_DAYS = 30


def device_expired(dev: dict, now: float | None = None) -> bool:
    """이 기기가 만료됐는가 — 마지막으로 쓴 때(`seen`, 한 번도 안 썼으면 짝지은 때 `added`)부터
    `REMOTE_DEVICE_TTL_DAYS` 일이 지났으면 참. 순수 함수 (시계는 `now` 로 받는다 — 검사가 얼린다)."""
    last = max(float(dev.get("seen") or 0), float(dev.get("added") or 0))
    t = time.time() if now is None else float(now)
    return t - last > REMOTE_DEVICE_TTL_DAYS * 86400


def get_devices() -> list:
    d = load("devices.json", [])
    out = []
    now = time.time()
    for x in (d if isinstance(d, list) else []):
        if not isinstance(x, dict) or not x.get("id") or not x.get("token"):
            continue
        dev = {"id": str(x["id"])[:32], "token": str(x["token"])[:128],
               "name": str(x.get("name") or "기기")[:40],
               "added": float(x.get("added") or 0), "seen": float(x.get("seen") or 0)}
        dev["expired"] = device_expired(dev, now)   # 목록이 「만료」로 보여 줄 값 — 저장본에서 읽지 않고 늘 새로 잰다
        out.append(dev)
    return out


def add_device(name: str = "") -> dict:
    """새 기기를 짝짓고 **긴 토큰**을 돌려준다 (이 값은 그 기기에만 남는다)."""
    with LOCK:
        items = get_devices()
        # 이름은 폰이 보내는 값이고 로그(「remote: login ok (이름)」)에 그대로 찍힌다 — 줄바꿈·제어문자를 빼서
        # 한 이름이 로그 두 줄이 되지 못하게 한다.
        clean = "".join(c for c in (name or "") if c.isprintable()).strip()[:40]
        dev = {"id": uuid.uuid4().hex[:12], "token": secrets.token_urlsafe(32),
               "name": clean or "기기", "added": time.time(), "seen": 0.0}
        items.append(dev)
        save("devices.json", items[-DEVICES_MAX:])
        return dev


def _find_device(items: list, token) -> dict | None:
    tb = str(token).encode("utf-8")   # bytes — 비ASCII 토큰이 와도 TypeError(500) 가 아니라 「없음」
    for x in items:
        if hmac.compare_digest(x["token"].encode("utf-8"), tb):
            return x
    return None


def device_token_expired(token) -> bool:
    """이 토큰이 **짝지었던 기기의 것인데 만료됐는가.** 로그인이 「없는 토큰」과 「만료된 토큰」을 갈라
    알려 주려고 쓴다 (401 + 다시 짝지으라는 말). 모르는 토큰이면 거짓."""
    if not token:
        return False
    x = _find_device(get_devices(), token)
    return bool(x and x["expired"])


def touch_device(token: str):
    """그 토큰의 기기를 찾고 마지막으로 본 때를 적는다. 없거나 **만료됐으면** None (만료된 기기는 `seen` 을
    새로 적지 않는다 — 두드리는 것만으로 되살아나면 만료가 아니다)."""
    if not token:
        return None
    with LOCK:
        items = get_devices()
        x = _find_device(items, token)
        if x is None or x["expired"]:
            return None
        x["seen"] = time.time()
        x["expired"] = False
        save("devices.json", items)
        return x


def drop_device(dev_id: str) -> bool:
    with LOCK:
        items = get_devices()
        left = [x for x in items if x["id"] != str(dev_id)]
        if len(left) == len(items):
            return False
        save("devices.json", left)
        return True


def drop_all_devices() -> None:
    with LOCK:
        save("devices.json", [])

# ═══════════════════════════════════════════════════════════════════════
# 모비폴리오에서 가져온 자료
#
# 파일은 전부 **`data/folio/` 아래**에 둔다 (`FOLIO`). 이름이 겹치기 때문이다.
# 이름이 같은 함수 22개(원격·기기·캐시·설정)는 **가져오지 않았다** — 우리 것과 같은
# 코드이고, 애초에 그쪽에서 가져온 것이다.
# ═══════════════════════════════════════════════════════════════════════

PRESET_KINDS = ("start", "end", "listen")
# 처음 켠 사람에게 줄 기본 프리셋.
# 없으면 「연주 인사」를 켜도 **한 줄도 안 나간다** — 기다리기만 하고 연출만 뜬다.
# 행동·표정은 일부러 비워 둔다: 게임이 아는 이름과 한 글자라도 다르면 CLI 가 거절하는데,
# 그 목록은 사람마다 다르다. 말만 먼저 되게 하고 행동·표정은 골라 넣게 둔다.
DEFAULT_PRESETS = (
    {"id": "d-start", "kind": "start", "name": "기본 인사",
     "chat": "♪ *(곡)* 연주합니다 — 잠시 들어 주세요", "action": "", "emoji": ""},
    {"id": "d-end", "kind": "end", "name": "기본 맺음",
     "chat": "*(곡)* 끝났습니다. 들어 주셔서 고맙습니다", "action": "", "emoji": ""},
    {"id": "d-listen", "kind": "listen", "name": "기본 감상",
     "chat": "좋은 연주 잘 들었습니다 ♪", "action": "", "emoji": ""},
)
_PRESET_FIELDS = ("name", "kind", "chat", "action", "emoji")

# 옛 앱이 두던 파일들 — `migrate_legacy`(옛 MabiScoreBox 이사)·`adopt_folio`(쓰던 모비폴리오
# 가져오기)가 같은 목록을 본다. **한 번만 적는다** — 가져오면서 세 번 적혀 있었고, `LOCK` 도
# 여기서 한 번 더 만들어져 있었다 (전역 이름을 다시 묶는 것이라 위 `load`·`save` 까지 두 번째
# 자물쇠를 쓰게 되어 다행히 하나로 돌았지만, 읽는 사람은 자물쇠가 둘로 보인다).
_DATA_FILES = ("folio/scores.json", "folio/instruments.json", "settings.json", "folio/recent.json", "folio/durations.json",
               "folio/cli_log.json", "folio/playlists.json", "folio/artists.json", "folio/ens.json", "folio/presets.json")


FIXTURE_DIR = os.path.join(_BASE, "fixtures")


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ── 아티스트 사전·수동 지정 ──


def _read_json_file(path: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _merge(name: str, docs: list) -> object:
    """같은 파일의 여러 판본을 하나로. docs 는 (mtime, 내용) — 최신이 앞."""
    docs = [d for _, d in sorted(docs, key=lambda x: -x[0])]
    if not docs:
        return None
    if name == "folio/artists.json":     # 아티스트·지정·잡음어 합집합 (같은 id 는 최신 우선, 별칭은 합침)
        out = {"artists": [], "assign": {}, "noise": []}
        seen: dict[str, dict] = {}
        for d in docs:
            if not isinstance(d, dict):
                continue
            for a in d.get("artists") or []:
                if isinstance(a, dict) and isinstance(a.get("id"), str):
                    if a["id"] in seen:
                        seen[a["id"]]["aliases"] = list(dict.fromkeys(seen[a["id"]].get("aliases", []) + list(a.get("aliases") or [])))
                    else:
                        seen[a["id"]] = dict(a); out["artists"].append(seen[a["id"]])
            for t, v in (d.get("assign") or {}).items():
                out["assign"].setdefault(t, v)
            for w in d.get("noise") or []:
                if w not in out["noise"]:
                    out["noise"].append(w)
        return out
    if name == "folio/durations.json":   # 합집합 (최신 우선)
        out = {}
        for d in docs:
            if isinstance(d, dict):
                for k, v in d.items():
                    out.setdefault(k, v)
        return out
    if name == "folio/recent.json":      # 제목별 최신 ts
        best: dict[str, dict] = {}
        for d in docs:
            for x in (d if isinstance(d, list) else []):
                if isinstance(x, dict) and isinstance(x.get("title"), str):
                    if x["title"] not in best or (x.get("ts") or 0) > (best[x["title"]].get("ts") or 0):
                        best[x["title"]] = x
        return sorted(best.values(), key=lambda x: -(x.get("ts") or 0))[:100]
    if name == "folio/playlists.json":   # 폴더·재생목록 id 합집합
        out = {"folders": [], "playlists": []}
        seen_f: set = set(); seen_p: set = set()
        for d in docs:
            if not isinstance(d, dict):
                continue
            for f in d.get("folders") or []:
                if isinstance(f, dict) and f.get("id") not in seen_f:
                    seen_f.add(f.get("id")); out["folders"].append(f)
            for pl in d.get("playlists") or []:
                if isinstance(pl, dict) and pl.get("id") not in seen_p:
                    seen_p.add(pl.get("id")); out["playlists"].append(pl)
        return out
    return docs[0]                 # settings/scores/instruments/cli_log: 최신 판본


def _norm_item(x) -> dict | None:
    """항목 = {"key": 내부 식별자, "title": DisplayTitle, "inst": 악기명|""}. key 가 없던 예전 항목·문자열 항목은 제목을 key 로 받아준다. 모양이 틀리면 None(버림)."""
    if isinstance(x, str):
        t = x.strip()
        return {"key": t, "title": t, "inst": ""} if t else None
    if isinstance(x, dict) and isinstance(x.get("title"), str) and x["title"].strip():
        inst = x.get("inst"); k = x.get("key")
        return {"key": k if isinstance(k, str) and k.strip() else x["title"], "title": x["title"], "inst": inst.strip() if isinstance(inst, str) else ""}
    return None


def _preset_clean(x) -> dict | None:
    if not isinstance(x, dict):
        return None
    kind = str(x.get("kind") or "start")
    if kind not in PRESET_KINDS:
        kind = "start"
    pid = str(x.get("id") or "").strip()[:32]
    if not pid:
        pid = uuid.uuid4().hex[:8]
    out = {"id": pid, "kind": kind}
    for f in ("name", "chat", "action", "emoji"):
        v = x.get(f)
        out[f] = v.strip()[:200] if isinstance(v, str) else ""
    if not out["name"]:
        out["name"] = "이름 없는 프리셋"
    return out


def get_lists() -> dict:
    d = _load_dict("folio/playlists.json")
    folders = [f for f in (d.get("folders") or []) if isinstance(f, dict) and isinstance(f.get("id"), str) and f["id"]]
    for f in folders:
        f["name"] = str(f.get("name") or "새 폴더")
        f["parent"] = f.get("parent") if isinstance(f.get("parent"), str) else None
    pls = [pl for pl in (d.get("playlists") or []) if isinstance(pl, dict) and isinstance(pl.get("id"), str) and pl["id"]]
    for pl in pls:
        pl["name"] = str(pl.get("name") or "새 재생목록")
        pl["folder"] = pl.get("folder") if isinstance(pl.get("folder"), str) else None
        pl["items"] = [it for it in (_norm_item(x) for x in (pl.get("items") or [])) if it]
        pl["memo"] = str(pl.get("memo") or "")
    return {"folders": folders, "playlists": pls}


def set_lists(d: dict) -> None:
    save("folio/playlists.json", d)


def get_recent() -> list:
    r = load("folio/recent.json", [])
    out = [x for x in r if isinstance(x, dict) and isinstance(x.get("title"), str)] if isinstance(r, list) else []
    for x in out:
        if not isinstance(x.get("key"), str):   # 손상된 key 는 제목으로
            x["key"] = x["title"]
    return out


def push_recent(title: str, inst: str = "", key: str = "") -> None:
    """key = 앱 내부 식별자(동명 악보 채번). 예전 항목(key 없음)은 제목이 key 다."""
    key = key or title
    with LOCK:
        r = [x for x in get_recent() if (x.get("key") or x.get("title")) != key]
        r.insert(0, {"key": key, "title": title, "inst": inst or "", "ts": time.time()})
        save("folio/recent.json", r[:100])


def get_durations() -> dict:
    out = {}
    for k, v in _load_dict("folio/durations.json").items():
        try:
            f = float(v)
            if f > 0:
                out[k] = f
        except (TypeError, ValueError):
            continue
    return out


def set_duration(title: str, seconds) -> None:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return
    if not title or not (s > 0):
        return
    with LOCK:
        d = get_durations()
        if abs(d.get(title, 0) - s) > 0.5:
            d[title] = round(s, 2)
            save("folio/durations.json", d)


# ── CLI 응답 로그 (재시작 후에도 보이게) ──


def get_artists() -> dict:
    """{"artists":[{id,name,aliases[]}], "assign":{원본제목: artistId}, "noise":[추가 잡음어]}. 항목 모양을 정규화한다."""
    d = _load_dict("folio/artists.json")
    arts = []
    for a in d.get("artists") or []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"]:
            continue
        a["name"] = str(a.get("name") or "").strip() or a["id"]
        a["aliases"] = [x for x in (a.get("aliases") or []) if isinstance(x, str) and x.strip()]
        arts.append(a)
    ids = {a["id"] for a in arts}
    assign = {t: v for t, v in (d.get("assign") or {}).items() if isinstance(t, str) and v in ids} if isinstance(d.get("assign"), dict) else {}
    noise = [x for x in (d.get("noise") or []) if isinstance(x, str) and x.strip()]
    return {"artists": arts, "assign": assign, "noise": noise}


def set_artists(d: dict) -> None:
    save("folio/artists.json", d)


def get_ens() -> dict:
    """악보별 합주 인원 — 사용자가 손으로 지정한 값. {악보 키: 1~12}.
    게임은 악보의 편성을 알려 주지 않는다(제목·복사가능·잠김·위치 넷뿐). 그래서 직접 적어 둔다."""
    out = {}
    for k, v in _load_dict("folio/ens.json").items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 12 and k:
            out[str(k)] = n
    return out


def set_ens(keys, n) -> dict:
    """여러 악보의 합주 인원을 한 번에 지정한다. n 이 없거나 0 이면 지정을 지운다."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    ks = [str(k) for k in (keys or []) if str(k)]
    if not ks:
        return get_ens()
    with LOCK:
        d = get_ens()
        for k in ks:
            if 1 <= n <= 12:
                d[k] = n
            else:
                d.pop(k, None)
        save("folio/ens.json", d)
        return d


def get_log() -> list:
    r = load("folio/cli_log.json", [])
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def set_log(items: list) -> None:
    save("folio/cli_log.json", list(items)[:60])


# ── 재생목록·폴더 ──


def get_presets() -> dict:
    d = _load_dict("folio/presets.json")
    # 파일이 아예 없다 = 아직 한 번도 손대지 않았다. 그때만 기본을 깔아 준다.
    # (일부러 전부 지운 사람에게 되살려 주지 않으려고 「items 칸이 있는가」로 가린다.)
    first = not isinstance(d.get("items"), list)
    items, seen = [], set()
    for x in (d.get("items") if isinstance(d.get("items"), list) else []):
        c = _preset_clean(x)
        if c and c["id"] not in seen:
            seen.add(c["id"])
            items.append(c)
    pick = d.get("pick") if isinstance(d.get("pick"), dict) else {}
    by = d.get("by_song") if isinstance(d.get("by_song"), dict) else {}
    if first and not items:
        items = [dict(x) for x in DEFAULT_PRESETS]
        pick = {x["kind"]: x["id"] for x in items}
    return {"items": items,
            "pick": {k: (str(pick.get(k) or "")) for k in PRESET_KINDS},
            "by_song": {str(k): str(v) for k, v in by.items() if isinstance(v, str)}}


def set_presets(patch: dict) -> dict:
    """items·pick·by_song 중 **보낸 것만** 바꾼다 (한 칸만 고칠 때 나머지가 날아가지 않게)."""
    with LOCK:
        d = get_presets()
        if isinstance(patch.get("items"), list):
            items, seen = [], set()
            for x in patch["items"][:200]:
                c = _preset_clean(x)
                if c and c["id"] not in seen:
                    seen.add(c["id"])
                    items.append(c)
            d["items"] = items
        # 값의 길이를 자른다: `pick`·`by_song` 값은 프리셋 id(32자, `_preset_clean`) 또는 `*` 이고, `by_song` 의
        # 열쇠는 곡 key(GREET_KEY_MAX) 다. 밖(edit)에서 1MB 짜리 열쇠를 되풀이해 넣으면 presets.json 이 끝없이
        # 자란다 — 아래 `ids` 거르기는 값만 보고 열쇠는 안 보았다.
        if isinstance(patch.get("pick"), dict):
            for k in PRESET_KINDS:
                if k in patch["pick"]:
                    d["pick"][k] = str(patch["pick"][k] or "")[:32]
        if isinstance(patch.get("by_song"), dict):
            for k, v in patch["by_song"].items():
                if not isinstance(k, str) or not k or len(k) > GREET_KEY_MAX:
                    continue
                if v:
                    d["by_song"][k] = str(v)[:32]
                else:
                    d["by_song"].pop(k, None)     # 빈 값 = 지정 해제
        ids = {x["id"] for x in d["items"]}
        for k in PRESET_KINDS:                    # 사라진 프리셋을 가리키고 있으면 놓아 준다
            if d["pick"][k] not in ids and d["pick"][k] != "*":
                d["pick"][k] = ""
        # 그 종류에 프리셋은 있는데 고른 것이 없으면 첫 장을 기본으로 삼는다 — 안 그러면 만들어 두고도
        # 인사가 한 줄도 안 나간다. 화면은 pick 을 늘 같이 보내므로 **맨 끝에서** 해야 한다
        # (가운데서 정하면 바로 뒤의 pick 처리에 빈 값으로 덮어써졌다).
        # 「그 종류가 처음 생겼을 때만」으로 가리던 예전 규칙은 기본 프리셋을 깔면서 헛돌았다 —
        # 깔아 둔 것을 지우고 새로 만든 사람은 「처음」이 아니라 고른 것 없이 남았다.
        for k in PRESET_KINDS:
            if not d["pick"][k]:
                first = next((x for x in d["items"] if x.get("kind") == k), None)
                if first:
                    d["pick"][k] = first["id"]
        d["by_song"] = {k: v for k, v in d["by_song"].items() if v in ids}
        save("folio/presets.json", d)
        return d


# ── 곡별 시작 인사 (곡 상세 시트 「연주 인사」 줄) ──
# 붙여 두는 곳은 **새로 만들지 않는다** — `folio/presets.json` 의 `by_song`(곡 key → 시작 프리셋 id)이
# 이미 있고, `engine.greet_pick` 과 연주 인사 화면(greet.html 「이 곡」 갈래)이 그 값을 본다.
# 아래 둘은 그 한 칸을 **곡 하나 단위로** 읽고 적는 입구다 (검사를 한 곳에 모은다).
GREET_KEY_MAX = 200


def greet_bound(key) -> str:
    """곡 key 에 붙여 둔 시작 인사 프리셋 id. 없으면 "" (= 기본 프리셋)."""
    if not isinstance(key, str) or not key:
        return ""
    return get_presets()["by_song"].get(key, "")


def greet_bind(key, preset) -> dict:
    """곡 key 에 시작 인사 프리셋을 붙인다 (`preset` "" = 지정 해제 → 기본).

    붙일 수 있는 것은 **있는 시작(start) 프리셋**뿐이다 — `by_song` 은 곡을 틀 때(시작 인사) 쓰는 값이라
    끝·감상 프리셋을 붙이면 시작 인사 목록에서 못 찾는다.
    → {"ok": True, "key", "preset"} | {"ok": False, "error": "bad_key" | "no_preset", "message"}"""
    if not isinstance(key, str) or not key.strip() or len(key) > GREET_KEY_MAX:
        return {"ok": False, "error": "bad_key", "message": "곡 key 가 없거나 너무 깁니다."}
    pid = preset if isinstance(preset, str) else ""
    if pid and not any(x.get("id") == pid and x.get("kind") == "start" for x in get_presets()["items"]):
        return {"ok": False, "error": "no_preset", "message": "그런 시작 인사 프리셋이 없습니다."}
    d = set_presets({"by_song": {key: pid}})
    return {"ok": True, "key": key, "preset": d["by_song"].get(key, "")}


# ── 최근 재생 · 곡 길이 캐시 ──


def save_fixture(command: str, body) -> None:
    """CLI 응답 원본을 fixtures/<command>.json 으로도 남긴다 (오프라인 개발·회귀용). 실패해도 동기화는 막지 않는다."""
    try:
        os.makedirs(FIXTURE_DIR, exist_ok=True)
        tmp = os.path.join(FIXTURE_DIR, f"{command}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(FIXTURE_DIR, f"{command}.json"))
    except Exception as e:
        print(f"[store] fixture 저장 실패 {command}: {e}", flush=True)


# ── 수신 캐시 ──


def migrate_legacy(candidates: list) -> list:
    """예전 위치(exe 옆 data/, 프로젝트 data/)의 파일을 한 번만 사용자 폴더로 옮긴다.
    새 위치에 data/ 가 아직 없을 때만 실행. 두 저장소가 갈라져 있던 경우 파일 종류별로 병합한다."""
    # **개발용 강제 위치면 이전하지 않는다.** 원래 `MABI_DATA_DIR` 을 보고 있었는데, 통합하며
    # 환경변수를 `MOBIW_DATA_DIR` 로 바꾸면서 **이 안전장치가 조용히 죽어 있었다** —
    # 검사가 임시 폴더를 가리켜도 옛 이사가 돌았다. 둘 다 본다 (옛 이름은 아직 쓰는 데가 있을 수 있다).
    if _BASE in (os.environ.get("MOBIW_DATA_DIR"), os.environ.get("MABI_DATA_DIR")) \
            or os.path.isfile(_path("folio/migrated.json")):
        return []
    # 새 위치에 '사용자 데이터'(설정·최근·길이·재생목록·아티스트)가 이미 있으면 건드리지 않는다. 캐시(scores/instruments/cli_log)만 있으면 이전 진행
    if any(os.path.isfile(_path(n)) for n in ("settings.json", "folio/recent.json", "folio/durations.json", "folio/playlists.json", "folio/artists.json")):
        return []
    found: dict[str, list] = {}
    srcs: list[str] = []
    for base in candidates:
        if not base:
            continue
        d = os.path.join(os.path.abspath(base), "data")
        if os.path.abspath(d) == os.path.abspath(DATA_DIR) or not os.path.isdir(d):
            continue
        for name in _DATA_FILES:
            src = os.path.join(d, name)
            if os.path.isfile(src):
                doc = _read_json_file(src)
                if doc is not None:
                    found.setdefault(name, []).append((os.path.getmtime(src), doc)); srcs.append(src)
    if not found:
        return []
    os.makedirs(DATA_DIR, exist_ok=True)
    for name, docs in found.items():
        if os.path.isfile(_path(name)):   # 새 위치에 이미 있는 파일(캐시)은 예전 것으로 덮지 않는다
            continue
        merged = _merge(name, docs)
        if merged is not None:
            save(name, merged)
    save("folio/migrated.json", {"at": time.time(), "from": srcs})
    print(f"[store] 예전 데이터 {len(srcs)}개 파일을 병합해 {DATA_DIR} 로 옮겼습니다", flush=True)
    return srcs


def folio_cache(kind: str) -> dict:
    """폴리오 캐시 — `kind` = 'scores' | 'instruments' → {"fetched_at": ts, "items": [...]}

    **우리 `get_cache` 와 이름이 같아서는 안 된다.** 합치면서 같은 이름 22개는 우리 것을
    남겼는데, `get_cache` 만은 **파일도 모양도 다르다** — 우리는 `cache_<kind>.json` 의
    `{fetched_at, data}`, 그쪽은 `<kind>.json` 의 `{fetched_at, items}`. 이름이 같다고
    같은 것이 아니었고, 그대로 두니 `/api/folio/state` 가 `KeyError: 'items'` 로 터졌다."""
    d = _load_dict(FOLIO + f"{kind}.json")
    items = d.get("items")
    return {"fetched_at": d.get("fetched_at"), "items": items if isinstance(items, list) else []}


def set_folio_cache(kind: str, items: list) -> dict:
    d = {"fetched_at": time.time(), "items": list(items or [])}
    save(FOLIO + f"{kind}.json", d)
    return d



# ── 곡 번호 — 「내 음악 DB」 (`folio/songs.json`) ─────────────────────────
# 곡을 처음 수집할 때 순번을 매겨 DB 로 관리한다. 사용자는 그 번호로 커버 이미지를 붙이고, 없으면 기본 커버를 쓴다.
#
# 규칙:
#   · 곡을 **처음 본 순간** 다음 번호(1, 2, 3…)를 준다. 화면 표기는 `#0001`.
#   · 번호는 **다시 쓰지도, 다시 매기지도 않는다.** 보관함에서 사라진 곡도 기록은 남고 `gone: true`
#     로만 표시한다 — 돌아오면 같은 번호로 `gone: false`. 사람이 그 번호로 커버 파일을 붙여 두기
#     때문이다 (번호가 바뀌면 남의 커버가 붙는다).
#   · 곡의 정체 = 오늘의 `key` (`library.item_key` — 제목, 동명이면 `제목#n`). 새 매칭 규칙을
#     만들지 않는다. 손으로 「이 곡은 #12 다」 하고 잇는 길은 `songs_link` (아직 화면이 없다).
SONGS_FILE = FOLIO + "songs.json"
_SONG_FIELDS = ("id", "key", "title", "song", "artist", "first_seen", "last_seen", "plays", "duration", "gone", "cover",
               "cover_src")


def _song_rec(key: str, r) -> dict | None:
    """저장본의 한 줄을 모양대로 — 번호가 없거나 망가진 줄은 버린다 (번호 없는 기록은 기록이 아니다)."""
    if not isinstance(r, dict):
        return None
    try:
        sid = int(r.get("id"))
    except (TypeError, ValueError):
        return None
    if sid <= 0:
        return None

    def num(v, cast=float):
        try:
            return cast(v)
        except (TypeError, ValueError):
            return None
    dur = num(r.get("duration"))
    return {"id": sid, "key": key, "title": str(r.get("title") or ""), "song": str(r.get("song") or ""),
            "artist": str(r.get("artist") or ""), "first_seen": num(r.get("first_seen")) or 0.0,
            "last_seen": num(r.get("last_seen")) or 0.0, "plays": max(0, num(r.get("plays"), int) or 0),
            "duration": dur if dur and dur > 0 else None, "gone": bool(r.get("gone")),
            # 곡마다 고른 커버 — "" = 번호대로(자동) · "<파일 이름>" = covers 폴더의 그림 · "default:<이름>" = 앱 기본
            # 모양만 본다: 글자가 아니거나 경로 글자가 섞였으면 「자동」으로 둔다.
            "cover": _cover_sel_clean(r.get("cover")),
            # 온라인에서 받은 커버의 출처 {source, link, at} — 「온라인에서 찾기」로 고른 때만 (아래 cover_fetch_online)
            "cover_src": _cover_src_clean(r.get("cover_src"))}


def _cover_src_clean(v) -> dict | None:
    if not isinstance(v, dict) or v.get("source") not in ("itunes", "deezer"):
        return None
    link = v.get("link") if isinstance(v.get("link"), str) else ""
    try:
        at = float(v.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    return {"source": v["source"], "link": link[:500] if link.startswith("https://") else "", "at": at}


def get_songs() -> dict:
    """{"next": 다음 번호, "songs": {key: 기록}} — 없으면 빈 DB. `next` 는 가장 큰 번호보다 늘 크다
    (손으로 고친 파일이 `next` 를 줄여 놓아도 번호가 겹치지 않게)."""
    d = _load_dict(SONGS_FILE)
    songs = {}
    raw = d.get("songs") if isinstance(d.get("songs"), dict) else {}
    used: set = set()
    for k, r in raw.items():
        rec = _song_rec(str(k), r)
        if rec is None or rec["id"] in used:    # 같은 번호가 둘이면 먼저 것만 (번호는 곡 하나에 하나)
            continue
        used.add(rec["id"])
        songs[rec["key"]] = rec
    try:
        nxt = int(d.get("next") or 1)
    except (TypeError, ValueError):
        nxt = 1
    nxt = max(nxt, max(used, default=0) + 1, 1)
    return {"next": nxt, "songs": songs}


def _songs_save(d: dict) -> None:
    save(SONGS_FILE, {"next": d["next"], "songs": {k: {f: r[f] for f in _SONG_FIELDS} for k, r in d["songs"].items()}})


def songs_sync(items: list, durations: dict | None = None, now: float | None = None) -> dict:
    """보관함 목록(`library.build` 의 아이템)을 DB 에 맞춘다 → {key: 번호}.

    처음 보는 key 는 **목록 순서대로** 다음 번호를 받는다. 목록에 없는 기록은 `gone: true`,
    돌아온 기록은 `gone: false`. `last_seen`·제목·아티스트·길이는 지금 값으로 새로 쓴다.
    목록이 비어 있으면 아무것도 바꾸지 않는다 — 게임이 꺼져 캐시가 빈 것을 「전부 사라졌다」로 읽지 않는다."""
    if not items:
        return {k: r["id"] for k, r in get_songs()["songs"].items()}
    dur = durations if durations is not None else get_durations()
    t = time.time() if now is None else now
    with LOCK:
        d = get_songs()
        songs = d["songs"]
        here: set = set()
        for it in items:
            k = str(it.get("key") or it.get("title") or "")
            if not k or k in here:
                continue
            here.add(k)
            title = str(it.get("title") or "")
            rec = songs.get(k)
            if rec is None:
                rec = songs[k] = {"id": d["next"], "key": k, "title": title, "song": "", "artist": "",
                                  "first_seen": t, "last_seen": t, "plays": 0, "duration": None, "gone": False,
                                  "cover": "", "cover_src": None}
                d["next"] += 1
            rec["title"] = title
            rec["song"] = str(it.get("song") or it.get("cleaned") or "")
            rec["artist"] = str(it.get("artist") or "")
            rec["last_seen"] = t
            rec["gone"] = False
            if dur.get(title):
                rec["duration"] = dur[title]
        for k, rec in songs.items():
            if k not in here:
                rec["gone"] = True
        _songs_save(d)
        return {k: r["id"] for k, r in songs.items()}


def songs_played(key: str, title: str = "") -> None:
    """우리 연주가 시작됐다 — 그 곡의 `plays` 를 하나 올린다. 모르는 곡이면(아직 동기화 전) 조용히 넘긴다
    — 번호는 보관함을 읽을 때만 준다 (연주 한 번으로 번호가 생기면 순서가 보관함과 어긋난다)."""
    k = key or title
    if not k:
        return
    with LOCK:
        d = get_songs()
        rec = d["songs"].get(k)
        if rec is None:
            return
        rec["plays"] += 1
        _songs_save(d)


def songs_list() -> list:
    """DB 전부를 번호 순으로. 길이는 durations.json 이 더 새로 알면 그 값으로 채워 보여 준다."""
    dur = get_durations()
    out = []
    for r in sorted(get_songs()["songs"].values(), key=lambda r: r["id"]):
        r = dict(r)
        if dur.get(r["title"]):
            r["duration"] = dur[r["title"]]
        out.append(r)
    return out


def songs_link(key: str, song_id: int) -> dict:
    """**손으로 잇는 자리 (아직 화면 없음).** 「이 key 의 곡은 번호 #song_id 다」 — 게임에서 악보 이름을
    바꿔 key 가 달라졌을 때 옛 번호(와 거기 붙인 커버)를 새 이름으로 옮긴다.

    옮기는 것은 기록 하나다: 번호 `song_id` 의 기록이 `key` 로 이사하고, `key` 가 따로 받아 두었던
    새 번호의 기록은 `gone: true` 로 비켜 둔다 (번호는 지우지 않는다 — 규칙 그대로). 없는 번호면 ValueError."""
    with LOCK:
        d = get_songs()
        songs = d["songs"]
        src = next((k for k, r in songs.items() if r["id"] == int(song_id)), None)
        if src is None:
            raise ValueError(f"no such song id: {song_id}")
        if src != key:
            rec = songs.pop(src)
            if key in songs:                     # 그 key 가 받아 둔 새 번호는 옆으로 비켜 둔다
                old = songs.pop(key)
                old["gone"] = True
                old["key"] = f"{key}#unlinked-{old['id']}"
                songs[old["key"]] = old
            rec["key"] = key
            rec["gone"] = False
            songs[key] = rec
            _songs_save(d)
        return dict(songs[key])


def song_tag(song_id) -> str:
    """화면 표기 — `#0012`. 번호가 네 자리를 넘으면 그대로 (`#12345`)."""
    try:
        return "#" + str(int(song_id)).zfill(4)
    except (TypeError, ValueError):
        return ""


# ── 곡 번호로 고르는 카드 커버 ───────────────────────────────────────────
# 폴더: `DATA_DIR/folio/covers/`
#   covers/          바로 아래 = **번호 이름 파일만** (`12.png` · `0012.png` · `#0012.jpg`) — 그 번호 곡의 커버
#   covers/mine/     내 이미지 — 올린 그림·아무 이름 그림. 커버 고르기 시트의 「내 이미지」 칸이 이 폴더다
#   covers/default/  내 기본 풀 — 번호 파일이 없는 곡이 여기서 번호로 정해진 한 장을 받는다
# 없으면 기본 커버: 사용자 풀 `covers/default/` → (비었으면) 앱에 딸린 풀 `ui/folio/covers/default/`.
# (예전에는 올린 그림이 번호 파일과 한 폴더에 섞여 많아지면 관리가 어려웠다.
#  처음 한 번 옮긴다: `covers_migrate`.)
# 기본 커버의 「랜덤」은 **번호로 정해지는 랜덤**이다 — 같은 곡은 늘 같은 기본 커버.
# 매번 새로 뽑으면 링이 돌 때마다(목록을 다시 받을 때마다) 카드 그림이 바뀌어 깜빡인다.
COVER_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".svg": "image/svg+xml"}
_COVER_NAME = re.compile(r"#?0*(\d+)(\.(?:png|jpe?g|webp|gif|svg))", re.IGNORECASE)
MAX_COVER_BYTES = 15 * 1024 * 1024          # 이보다 큰 파일은 커버로 안 쓴다 (실수로 넣은 영상 등)
# 기본 풀도 비었을 때(앱 폴더가 깨졌을 때) — 404 대신 이 그림 (디자인 팔레트의 회색)
FALLBACK_COVER = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1420" viewBox="0 0 1000 1420">'
                  b'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#cccccc"/>'
                  b'<stop offset="1" stop-color="#777777"/></linearGradient></defs><rect width="1000" height="1420" fill="url(#g)"/></svg>')


def covers_dir() -> str:
    return os.path.join(DATA_DIR, "folio", "covers")


COVER_MINE = "mine"          # 내 이미지 폴더 이름 (covers/mine/)
COVER_POOL = "default"       # 내 기본 풀 폴더 이름 (covers/default/)


def covers_mine_dir() -> str:
    return os.path.join(covers_dir(), COVER_MINE)


def covers_pool_dir() -> str:
    return os.path.join(covers_dir(), COVER_POOL)


def covers_migrate() -> list:
    """**한 번만**: 커버 폴더 바로 아래의 **번호 이름이 아닌** 그림을 `mine/` 으로 옮긴다 → 옮긴 이름들.

    「한 번」의 표시는 `mine/` 폴더다 — 그 폴더가 이미 있으면 아무것도 하지 않는다(옮긴 뒤에 사람이 손으로
    바로 아래에 넣은 그림은 건드리지 않는다). 커버 폴더 자체가 없으면 만들지 않는다 (읽기에 부작용이 없게).
    곡마다 고른 값(`songs.json` 의 `cover`)은 **이름**이라 그대로 둔다 — 옮긴 뒤에도 같은 이름으로 `mine/` 에서 찾는다.
    번호 이름 파일(`0012.png`)은 옮기지 않는다 — 그 자리가 번호 규칙이다."""
    d = covers_dir()
    mine = os.path.join(d, COVER_MINE)
    if os.path.isdir(mine) or not os.path.isdir(d):
        return []
    with LOCK:
        if os.path.isdir(mine):
            return []
        try:
            names = sorted(os.listdir(d), key=lambda n: n.lower())
            os.makedirs(mine, exist_ok=True)
        except OSError as e:
            print(f"[store] 커버 폴더 정리(mine/)를 하지 못했습니다: {e}", flush=True)
            return []
        moved = []
        for n in names:
            p = os.path.join(d, n)
            if n.startswith(".") or os.path.splitext(n)[1].lower() not in COVER_TYPES or not os.path.isfile(p):
                continue
            if _COVER_NAME.fullmatch(n):
                continue
            try:
                os.replace(p, os.path.join(mine, n))
                moved.append(n)
            except OSError as e:
                print(f"[store] 커버 {n} 을 mine/ 으로 옮기지 못했습니다: {e}", flush=True)
        if moved:
            print(f"[store] 커버 폴더 정리: 번호 이름이 아닌 그림 {len(moved)}장을 mine/ 으로 옮겼습니다 — "
                  + ", ".join(moved[:20]) + (" …" if len(moved) > 20 else ""), flush=True)
        return moved


def _cover_files(d: str) -> list:
    """폴더 안의 받는 확장자 파일 (이름순). 폴더가 없으면 빈 목록."""
    try:
        names = sorted(os.listdir(d), key=lambda n: n.lower())
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(d, n)
        if os.path.splitext(n)[1].lower() in COVER_TYPES and os.path.isfile(p):
            try:
                if os.path.getsize(p) <= MAX_COVER_BYTES:
                    out.append(p)
            except OSError:
                pass
    return out


def _pick_default(pool: list, song_id: int) -> str:
    """번호 → 풀의 한 장. 번호를 섞어(곱셈 해시) 이웃한 번호가 줄지어 같은 순서로 돌지 않게 한다."""
    h = (int(song_id) * 2654435761) & 0xFFFFFFFF
    return pool[(h >> 7) % len(pool)]


def _sid(song_id) -> int:
    """받은 번호 글자 → 번호. 숫자가 아니면(`../`·글자·전각 숫자) 0."""
    s = str(song_id if song_id is not None else "")
    return int(s) if s.isascii() and s.isdigit() and 0 < len(s) <= 9 else 0


# 곡마다 고른 커버의 값 (songs.json `cover`) — "" | "<파일 이름>" | "default:<앱 기본 이름>".
# 파일 이름은 **이름만**이다 (폴더 글자·`..`·제어 글자 없음). 실제로 있는지는 쓸 때 폴더 목록에서 다시 본다.
COVER_BUNDLED_PREFIX = "default:"
_COVER_SEL_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _cover_sel_clean(v) -> str:
    if not isinstance(v, str):
        return ""
    v = v.strip()
    if not v or len(v) > 200:
        return ""
    name = v[len(COVER_BUNDLED_PREFIX):] if v.startswith(COVER_BUNDLED_PREFIX) else v
    if not name or _COVER_SEL_BAD.search(name) or name in (".", "..") or name.startswith("."):
        return ""
    return v


def _file_tag(p: str) -> str:
    """파일의 모습(수정 시각·크기) → 짧은 글자. 커버 주소의 `?v=` 에 붙는다 — 그림이 바뀌면 주소가 바뀐다."""
    try:
        st = os.stat(p)
    except OSError:
        return "0"
    return hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8", "surrogatepass")).hexdigest()[:10]


# ── 생성 커버 — 「번호대로」의 마지막 차례 (기본) ─────────────────────────────
# **기본 = 곡마다 그려 만든 커버** (앨범 그림은 저작권이 있어
# 앱이 알아서 모아 오지 않는다), **선택 = 사용자가 직접 찾아 고르는 온라인 앨범 그림** (아래 「온라인에서 찾기」).
#
# 무늬 갈래는 앱 기본 여섯 장(ui/folio/covers/default)과 같은 여섯 — 저녁놀·밤하늘·악보 종이·사선 띠·숲·잉크.
# 갈래는 예전 「번호 → 여섯 장 중 한 장」과 **같은 곱셈 해시**로 고른다(`_pick_default` — 같은 곡은 예전과 같은 갈래),
# 색·각도·자리는 번호를 씨앗으로 한 난수로 바꾼다 → 카드마다 다르고, **같은 번호는 늘 같은 그림**.
# 그 위에 곡 제목(크게, 3줄까지) · 아티스트(작게) · `#0012`(옅게)를 쓴다.
#
# 글꼴: SVG 를 <img>·CSS 배경으로 쓰면 그 SVG 는 **따로 떨어진 문서**라 페이지의 @font-face(Noto Sans KR)를 **못 본다**
# (헤드리스 Edge 실측). 그래서 제목·아티스트에 쓰인 글자가 든 Noto Sans KR 조각
# (ui/folio/fonts 의 woff2 — Google Fonts 가 나눈 그대로)만 골라 **SVG 안에 data: 로 싣는다** — 연출·시트·관리 화면
# 어디서 보든 같은 글꼴이다. 조각이 너무 커지면(한자가 많은 제목 등) 싣지 않고 시스템 글꼴(맑은 고딕)로 그린다.
GEN_VERSION = "1"                   # 그리는 규칙이 바뀌면 올린다 — 주소(`?v=`)가 바뀌어 새로 받는다
GEN_FAMILIES = ("sunset", "night", "paper", "stripes", "forest", "ink")     # 앱 기본 여섯 장의 이름순과 같은 차례
GEN_FAMILY_KO = {"sunset": "저녁놀", "night": "밤하늘", "paper": "악보 종이", "stripes": "사선 띠", "forest": "숲", "ink": "잉크"}
GEN_FONT_CAP = 600 * 1024           # 한 장에 싣는 글꼴 조각의 합 상한 (넘으면 시스템 글꼴)
GEN_W, GEN_H = 1000, 1420
_GEN_SANS = "'Noto Sans KR', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif"
_GEN_MONO = "'Space Mono', Consolas, 'Courier New', monospace"
_GEN_CACHE: dict = {}               # (번호, 제목, 아티스트, 글꼴 폴더) → SVG 바이트 (최근 것만)
_GEN_CACHE_MAX = 256
_FONT_INDEX: dict = {}              # 글꼴 폴더 → [(파일, [(시작, 끝)], unicode-range 글자, 크기)]


def cover_fonts_dir(bundled_dir: str) -> str:
    """앱 기본 커버 폴더(`ui/folio/covers/default`) → 같은 `ui/folio` 의 글꼴 폴더(`ui/folio/fonts`)."""
    return os.path.join(os.path.dirname(os.path.dirname(bundled_dir)), "fonts") if bundled_dir else ""


def _font_slices(fonts_dir: str) -> list:
    """fonts.css 의 Noto Sans KR 조각 목록 (굵기 900 줄 — 조각 파일은 굵기마다 같은 가변 글꼴이다)."""
    if not fonts_dir:
        return []
    hit = _FONT_INDEX.get(fonts_dir)
    if hit is not None:
        return hit
    try:
        with open(os.path.join(fonts_dir, "fonts.css"), encoding="utf-8") as f:
            css = f.read()
    except OSError:
        css = ""
    out, seen = [], set()
    for block in re.findall(r"@font-face\s*\{(.*?)\}", css, re.S):
        if "Noto Sans KR" not in block or not re.search(r"font-weight:\s*900\b", block):
            continue
        m = re.search(r"url\(([^)]+)\)", block)
        r = re.search(r"unicode-range:\s*([^;]+);", block)
        if not m or not r:
            continue
        name = m.group(1).strip("'\" ")
        if name in seen or "/" in name or "\\" in name or not name.endswith(".woff2"):
            continue
        ranges = []
        for part in r.group(1).split(","):
            a, _, b = part.strip().upper().replace("U+", "").partition("-")
            try:
                ranges.append((int(a, 16), int(b or a, 16)))
            except ValueError:
                pass
        try:
            size = os.path.getsize(os.path.join(fonts_dir, name))
        except OSError:
            continue
        seen.add(name)
        out.append((name, ranges, r.group(1).strip(), size))
    _FONT_INDEX[fonts_dir] = out
    return out


def _gen_font_css(text: str, fonts_dir: str) -> str:
    """글자들이 든 Noto Sans KR 조각만 골라 @font-face(data:) 로 — 가장 적은 바이트로 덮는 조각을 차례로 고른다.
    조각이 없거나 합이 상한을 넘으면 "" (시스템 글꼴로 그린다)."""
    import base64
    slices = _font_slices(fonts_dir)
    want = {ord(c) for c in text if not c.isspace()}
    if not slices or not want:
        return ""
    cover = [(name, rr, size, {cp for cp in want if any(a <= cp <= b for a, b in ranges)})
             for name, ranges, rr, size in slices]
    left, pick, total = set(want), [], 0
    while left:
        best = max(cover, key=lambda c: (len(c[3] & left), -c[2]))
        got = best[3] & left
        if not got:
            break                      # 조각에 없는 글자(이모지 등) — 시스템 글꼴이 그린다
        pick.append(best)
        left -= got
        total += best[2]
        if total > GEN_FONT_CAP:
            return ""
    parts = []
    for name, rr, _size, _cps in pick:
        try:
            with open(os.path.join(fonts_dir, name), "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return ""
        parts.append("@font-face{font-family:'Noto Sans KR';font-weight:100 900;"
                     f"src:url(data:font/woff2;base64,{b64}) format('woff2');unicode-range:{rr}}}")
    return "".join(parts)


def _hsl(h: float, s: float, l: float) -> str:
    import colorsys
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _xml(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("\r", " ").replace("\n", " "))


def _gen_char_w(c: str) -> float:
    """글자 폭 근사 (em — Noto Sans KR Black). 줄 나눔·크기 고르기에만 쓴다 (넘치면 textLength 로 눌러 맞춘다)."""
    o = ord(c)
    if c == " ":
        return 0.24
    if 0x1100 <= o <= 0x11FF or 0x2E80 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF:
        return 1.0
    if c in "iIl.,:;'!|`":
        return 0.3
    if c in "mwMW@%":
        return 0.86
    if c.isupper():
        return 0.66
    if c.isdigit():
        return 0.57
    if c.islower():
        return 0.55
    return 0.5


def _gen_w(s: str, size: float) -> float:
    return sum(_gen_char_w(c) for c in s) * size


def _gen_wrap(text: str, size: float, maxw: float, split_words: bool = True) -> list | None:
    """띄어쓰기에서 나누고(한국어 keep-all 처럼), 한 낱말이 한 줄보다 길 때만 글자로 자른다.
    `split_words=False` 면 낱말을 잘라야 할 때 None (그 크기는 안 된다 — 더 작은 크기로)."""
    lines, cur = [], ""
    for word in text.split(" "):
        if not word:
            continue
        cand = (cur + " " + word) if cur else word
        if _gen_w(cand, size) <= maxw:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if not split_words and _gen_w(word, size) > maxw:
            return None
        while _gen_w(word, size) > maxw:          # 한 낱말이 한 줄보다 길다 — 글자로 자른다
            i = 1
            while i < len(word) and _gen_w(word[:i + 1], size) <= maxw:
                i += 1
            lines.append(word[:i])
            word = word[i:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def _gen_fit(text: str, size: float, maxw: float) -> str:
    """한 줄에 안 들어가면 끝을 「…」 로."""
    if _gen_w(text, size) <= maxw:
        return text
    while text and _gen_w(text + "…", size) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def gen_title_layout(title: str, maxw: float = 832.0) -> tuple:
    """제목 → (글자 크기, [줄]) — 3줄까지. 큰 크기부터 줄여 가며 3줄에 들어가는 첫 크기. 가장 작은 크기로도
    넘치면 셋째 줄 끝을 「…」 로 자른다."""
    title = " ".join(str(title or "").split())
    if not title:
        return 0, []
    sizes = (176, 156, 140, 126, 112, 100, 90, 80, 72)
    for split in (False, True):            # 먼저 낱말을 자르지 않고 들어가는 크기, 없으면 잘라서라도
        for size in sizes:
            lines = _gen_wrap(title, size, maxw, split)
            if lines is not None and len(lines) <= 3:
                return size, lines
    size = 72
    lines = _gen_wrap(title, size, maxw)
    return size, lines[:2] + [_gen_fit(" ".join(lines[2:]), size, maxw)]


def gen_family(song_id: int) -> str:
    """번호 → 무늬 갈래. 예전 「번호 → 앱 기본 여섯 장 중 한 장」과 같은 해시 (같은 곡은 예전과 같은 갈래)."""
    return _pick_default(list(GEN_FAMILIES), int(song_id))


def _gen_rng(song_id: int):
    import random
    return random.Random(int.from_bytes(hashlib.sha256(f"mobiworks-cover|{int(song_id)}".encode()).digest()[:8], "big"))


def _gen_art(fam: str, rnd) -> tuple:
    """무늬 → (배경 SVG 조각, 글씨 색, 글씨 뒤 그림자 여부, 글씨 뒤 받침 색 | "")."""
    W, H = GEN_W, GEN_H
    u = rnd.uniform
    if fam == "sunset":
        h = u(0, 360)
        x2 = u(.55, 1)
        body = (f'<defs><linearGradient id="g" x1="0" y1="0" x2="{x2:.2f}" y2="1"><stop offset="0" stop-color="{_hsl(h, .82, .76)}"/>'
                f'<stop offset="1" stop-color="{_hsl(h - 18, .52, .52)}"/></linearGradient>'
                '<radialGradient id="r"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>'
                f'<rect width="{W}" height="{H}" fill="url(#g)"/>'
                f'<circle cx="{u(180, 820):.0f}" cy="{u(220, 610):.0f}" r="{u(360, 470):.0f}" fill="url(#r)"/>'
                f'<circle cx="{u(560, 940):.0f}" cy="{u(980, 1240):.0f}" r="{u(300, 400):.0f}" fill="{_hsl(h - 25, .45, .3)}" opacity=".25"/>')
        return body, "#ffffff", True, ""
    if fam == "night":
        h = u(195, 290)
        top, bot = _hsl(h, .5, .08), _hsl(h + 6, .36, .26)
        stars = "".join(f'<circle cx="{u(0, W):.0f}" cy="{u(0, H):.0f}" r="{rnd.choice((1.2, 1.8, 2.6, 3.5))}" fill="#fff" '
                        f'opacity="{rnd.choice((.35, .6, .9))}"/>' for _ in range(150))
        mx, my, mr = u(200, 800), u(170, 520), u(70, 100)
        body = (f'<defs><linearGradient id="g" x1="0" y1="0" x2="{u(0, .3):.2f}" y2="1"><stop offset="0" stop-color="{top}"/>'
                f'<stop offset="1" stop-color="{bot}"/></linearGradient></defs><rect width="{W}" height="{H}" fill="url(#g)"/>{stars}'
                f'<circle cx="{mx:.0f}" cy="{my:.0f}" r="{mr:.0f}" fill="#f4e7c8" opacity=".9"/>'
                f'<circle cx="{mx - mr * .33:.0f}" cy="{my - mr * .22:.0f}" r="{mr * .89:.0f}" fill="{top}" opacity=".88"/>')
        return body, "#f4e7c8", False, ""
    if fam == "paper":
        h = u(28, 52)
        ang = u(-4, 4)
        y0, gap = u(150, 200), u(150, 190)
        lines = []
        for grp in range(4):
            for k in range(5):
                y = y0 + grp * gap + k * 22
                lines.append(f'<line x1="-40" y1="{y:.0f}" x2="1040" y2="{y:.0f}" stroke="#2a2622" stroke-opacity=".26" stroke-width="2"/>')
        glyph = rnd.choice(("♪", "♫", "♩"))
        body = (f'<rect width="{W}" height="{H}" fill="{_hsl(h, .42, .91)}"/><rect width="{W}" height="{H}" fill="{_hsl(h, .35, .57)}" opacity=".12"/>'
                f'<g transform="rotate({ang:.1f} 500 500)">{"".join(lines)}</g>'
                f'<text x="{u(560, 860):.0f}" y="{u(300, 820):.0f}" font-family="Georgia,serif" font-size="{u(150, 230):.0f}" fill="#2a2622" opacity=".2">{glyph}</text>')
        return body, "#2a2622", False, ""
    if fam == "stripes":
        h0 = u(0, 360)
        cols = [_hsl(h0 + k * 72, .62, .83) for k in range(5)]
        ang = rnd.choice((-1, 1)) * u(16, 34)
        sw = u(56, 88)
        rects, x, i = [], -520.0, 0
        while x < 1560:
            rects.append(f'<rect x="{x:.0f}" y="-300" width="{sw:.0f}" height="2020" fill="{cols[i % 5]}"/>')
            x += sw * 2
            i += 1
        body = (f'<rect width="{W}" height="{H}" fill="#fbfaf7"/><g transform="rotate({ang:.1f} 500 710)">{"".join(rects)}</g>')
        return body, "#17161a", False, "#fbfaf7"
    if fam == "forest":
        h = u(88, 190)
        cx, cy, step = u(260, 740), u(560, 1000), u(118, 160)
        rings = "".join(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{step * (k + 1) - step * .15:.0f}" fill="none" stroke="#e6f1e8" '
                        f'stroke-opacity="{op}" stroke-width="14"/>' for k, op in enumerate((.55, .4, .3, .22, .15, .1)))
        body = (f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{_hsl(h, .2, .66)}"/>'
                f'<stop offset="1" stop-color="{_hsl(h + 6, .33, .27)}"/></linearGradient></defs><rect width="{W}" height="{H}" fill="url(#g)"/>{rings}')
        return body, "#ffffff", True, ""
    # ink
    acc = rnd.choice(("#e2b866", "#e7a0b4", "#8fd3c1", "#9cc3ef", "#c3a9ef"))
    ins = u(48, 72)
    glyph = rnd.choice(("♫", "♪", "♩"))
    body = (f'<rect width="{W}" height="{H}" fill="{_hsl(u(240, 290), .08, .095)}"/>'
            f'<rect x="{ins:.0f}" y="{ins:.0f}" width="{W - 2 * ins:.0f}" height="{H - 2 * ins:.0f}" fill="none" stroke="{acc}" stroke-width="3" rx="28"/>'
            f'<text x="{u(380, 610):.0f}" y="{u(760, 920):.0f}" text-anchor="middle" font-family="Georgia,serif" font-size="{u(600, 760):.0f}" fill="{acc}" opacity=".2">{glyph}</text>')
    return body, acc, False, ""


def cover_generate(song_id, title: str = "", artist: str = "", fonts_dir: str = "") -> bytes:
    """곡 번호 · 제목 · 아티스트 → 생성 커버 SVG (1000×1420). 같은 값이면 늘 같은 바이트다 (결정적)."""
    sid = _sid(song_id)
    title = " ".join(str(title or "").split())[:120]
    artist = " ".join(str(artist or "").split())[:80]
    key = (sid, title, artist, fonts_dir, GEN_VERSION)
    hit = _GEN_CACHE.get(key)
    if hit is not None:
        return hit
    rnd = _gen_rng(sid)
    fam = gen_family(sid) if sid > 0 else "ink"
    art, ink, shadow, plate = _gen_art(fam, rnd)
    maxw = 832.0
    size, lines = gen_title_layout(title, maxw)
    a_size = 46
    a_text = _gen_fit(artist, a_size, maxw) if artist else ""
    base = 1222 if a_text else 1290             # 제목 마지막 줄의 글자 바닥
    lh = size * 1.1
    out = []
    for i, ln in enumerate(lines):
        y = base - (len(lines) - 1 - i) * lh
        fit = f' textLength="{maxw:.0f}" lengthAdjust="spacingAndGlyphs"' if _gen_w(ln, size) > maxw else ""
        out.append(f'<text x="84" y="{y:.0f}"{fit}>{_xml(ln)}</text>')
    txt = ""
    filt = ' filter="url(#ts)"' if shadow else ""
    if lines:
        txt += (f'<g font-family="{_GEN_SANS}" font-weight="900" font-size="{size}" fill="{ink}" letter-spacing="-1"{filt}>'
                + "".join(out) + "</g>")
    if a_text:
        txt += (f'<text x="86" y="{base + 82:.0f}" font-family="{_GEN_SANS}" font-weight="700" font-size="{a_size}" fill="{ink}" '
                f'opacity=".82"{filt}>{_xml(a_text)}</text>')
    if sid > 0:
        txt += (f'<text x="916" y="128" text-anchor="end" font-family="{_GEN_MONO}" font-weight="700" font-size="36" '
                f'fill="{ink}" opacity=".5">#{str(sid).zfill(4)}</text>')
    back = ""
    if plate and lines:                          # 사선 띠처럼 바탕이 번잡하면 글씨 뒤에 옅은 받침
        top = base - (len(lines) - 1) * lh - size * .95
        bot = (base + 82 + 18) if a_text else (base + size * .3)
        back = f'<rect x="54" y="{top:.0f}" width="892" height="{bot - top:.0f}" rx="22" fill="{plate}" opacity=".78"/>'
    fonts = _gen_font_css(title + artist, fonts_dir)
    defs = ('<defs><filter id="ts" x="-10%" y="-30%" width="120%" height="160%"><feDropShadow dx="0" dy="4" stdDeviation="9" '
            'flood-color="#000" flood-opacity=".38"/></filter></defs>') if shadow else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{GEN_W}" height="{GEN_H}" viewBox="0 0 {GEN_W} {GEN_H}">'
           f'<title>{_xml(title)}{" — " + _xml(artist) if artist else ""}</title>'
           + (f"<style>{fonts}</style>" if fonts else "") + defs + art + back + txt + "</svg>").encode("utf-8")
    if len(_GEN_CACHE) >= _GEN_CACHE_MAX:
        _GEN_CACHE.pop(next(iter(_GEN_CACHE)))
    _GEN_CACHE[key] = svg
    return svg


def gen_tag(song_id, title: str = "", artist: str = "") -> str:
    """생성 커버의 모습 → 짧은 글자 (`?v=g…`). 제목·아티스트·그리는 규칙이 바뀌면 바뀐다."""
    return hashlib.sha1(f"{GEN_VERSION}|{_sid(song_id)}|{title}|{artist}".encode("utf-8", "surrogatepass")).hexdigest()[:10]


def _song_meta(rec: dict) -> tuple:
    """번호 기록 → (커버에 쓸 제목, 아티스트)."""
    return (str(rec.get("song") or rec.get("title") or rec.get("key") or ""), str(rec.get("artist") or ""))


class CoverScan:
    """커버 폴더를 **한 번** 훑은 결과. 악보함 수백 곡의 커버 주소를 만들 때 곡마다 폴더를 다시 읽지 않게.

    고르는 차례:
      1. 곡마다 고른 것 (`songs.json` 의 `cover`) — 그 파일이 지금 있을 때만 (`mine/` 에서, 없으면 바로 아래 번호 파일)
      2. 번호 이름 파일 (`covers/0012.png` — 폴더 바로 아래)
      3. 내 기본 풀 (`covers/default/`) — 번호로 정해진 한 장
      4.**생성 커버** — 번호로 정한 무늬 + 곡 제목·아티스트 (위 `cover_generate`)
    번호가 숫자가 아니면(`../`·글자) 생성할 곡이 없으므로 내 기본 풀 → 앱 기본 풀 → 회색 그림.
    앱 기본 여섯 장은 이제 자동 차례에 없다 — 시트에서 고를 수 있는 「기본 커버」로만 남는다.
    경로는 폴더 목록에서만 고른다 — 받은 글자로 경로를 만들지 않는다."""

    def __init__(self, bundled_dir: str = ""):
        covers_migrate()                  # 처음 한 번: 바로 아래의 아무 이름 그림 → mine/ (그 뒤로는 바로 돈다)
        d = covers_dir()
        self.mine = {os.path.basename(p): p for p in _cover_files(os.path.join(d, COVER_MINE))}   # 내 이미지 (mine/)
        self.root = {os.path.basename(p): p for p in _cover_files(d)}          # 폴더 바로 아래 그림 전부
        self.own: dict = {}                                                     # 번호 → 그 번호의 파일
        by_id: dict = {}
        for n, p in self.root.items():
            m = _COVER_NAME.fullmatch(n)
            if m:
                by_id.setdefault(int(m.group(1)), {}).setdefault(m.group(2).lower(), p)
        for sid, exts in by_id.items():
            for ext in COVER_TYPES:                 # 같은 번호가 여러 확장자로 있으면 정해진 차례로
                if ext in exts:
                    self.own[sid] = exts[ext]
                    break
        self.pool = _cover_files(os.path.join(d, COVER_POOL))
        self.bundled = _cover_files(bundled_dir) if bundled_dir else []
        self.bundled_by = {os.path.basename(p): p for p in self.bundled}
        self.fonts_dir = cover_fonts_dir(bundled_dir)
        self._meta = None                   # 번호 → (제목, 아티스트) — 생성 커버를 그리거나 주소를 만들 때 한 번 읽는다

    def meta(self, song_id) -> tuple:
        if self._meta is None:
            self._meta = {r["id"]: _song_meta(r) for r in get_songs()["songs"].values()}
        return self._meta.get(_sid(song_id), ("", ""))

    def generate(self, song_id) -> bytes:
        """그 번호의 생성 커버 (SVG 바이트)."""
        title, artist = self.meta(song_id)
        return cover_generate(_sid(song_id), title, artist, self.fonts_dir)

    def picked(self, sel: str):
        """고른 값 → 경로 (없는 파일이면 None — 그러면 다음 차례로 넘어간다)."""
        sel = _cover_sel_clean(sel)
        if not sel:
            return None
        if sel.startswith(COVER_BUNDLED_PREFIX):
            return self.bundled_by.get(sel[len(COVER_BUNDLED_PREFIX):])
        # 내 이미지(mine/)가 먼저. 예전 시트는 폴더 바로 아래 파일을 고르게 했으므로(번호 이름 파일도 고를 수
        # 있었다) 그 값도 계속 받는다 — 정리한 뒤 바로 아래에 남는 것은 번호 이름 파일뿐이다.
        return self.mine.get(sel) or self.root.get(sel)

    def resolve(self, song_id, sel: str = "") -> tuple:
        """→ (경로 | None, content-type, 어디서: "pick" | "own" | "default" | "gen" | "bundled" | "fallback").
        "gen" 은 경로가 없다 — 그림은 `generate()` 가 그린다."""
        sid = _sid(song_id)
        p = self.picked(sel) if sid > 0 else None
        if p:
            return p, COVER_TYPES[os.path.splitext(p)[1].lower()], "pick"
        return self.auto(sid)

    def auto(self, song_id) -> tuple:
        """「번호대로」 — 곡마다 고른 것을 빼고 본 차례: 번호 이름 파일 → 내 기본 풀 → 생성 커버."""
        sid = _sid(song_id)
        if sid > 0 and sid in self.own:
            p = self.own[sid]
            return p, COVER_TYPES[os.path.splitext(p)[1].lower()], "own"
        if self.pool:
            p = _pick_default(self.pool, sid)
            return p, COVER_TYPES[os.path.splitext(p)[1].lower()], "default"
        if sid > 0:
            return None, "image/svg+xml", "gen"
        if self.bundled:                    # 번호가 없는 요청(`../`·글자) — 그릴 곡이 없다
            p = _pick_default(self.bundled, sid)
            return p, COVER_TYPES[os.path.splitext(p)[1].lower()], "bundled"
        return None, "image/svg+xml", "fallback"

    def _tag(self, sid: int, p, where: str) -> str:
        if where == "gen":
            return "g" + gen_tag(sid, *self.meta(sid))
        return where[0] + (_file_tag(p) if p else "0")

    def random_url(self) -> str:
        """기본 풀(없으면 앱 기본)에서 **무작위 한 장**의 주소 — 번호가 없는 곡(보관함에 없는 남의 합주곡)의 카드용.
        부를 때마다 다르다. 풀이 비었으면 ""."""
        from urllib.parse import quote
        import random as _random
        if self.pool:
            p, route = _random.choice(self.pool), "pool"
        elif self.bundled:
            p, route = _random.choice(self.bundled), "bundled"
        else:
            return ""
        return f"/folio/covers/{route}/{quote(os.path.basename(p))}?v={_file_tag(p)}"

    def auto_url(self, song_id) -> str:
        """커버 고르기 시트의 「번호대로」 칸 썸네일 — 자동으로 받는 그림을 그 그림의 길로 (`?v=` 붙음)."""
        from urllib.parse import quote
        sid = _sid(song_id)
        if sid <= 0:
            return ""
        p, _t, where = self.auto(sid)
        v = self._tag(sid, p, where)
        if where == "gen":
            return f"/folio/covers/gen/{sid}?v={v}"
        route = "file" if where == "own" else "pool"
        return f"/folio/covers/{route}/{quote(os.path.basename(p))}?v={v}"

    def url(self, song_id, sel: str = "") -> str:
        """연출 페이지가 쓸 주소 — `/folio/covers/<번호>?v=<그림의 모습>`.

        **`?v=` 가 이 기능의 목숨이다.** 연출 페이지는 앱을 켤 때 한 번 떠서 하루 종일 산다(웜 스타트).
        브라우저는 한 문서 안에서 같은 주소의 그림을 **문서가 살아 있는 동안 다시 받지 않는다** —
        헤드리스 실측: 그림 파일을 넣고 75초 뒤에 다시 연출해도 요청이 한 번도 안 나가고 옛 기본 커버가
        나왔다(`max-age=60` 과 무관). 그래서 그림이 바뀌면 **주소가 바뀌게** 한다."""
        sid = _sid(song_id)
        if sid <= 0:
            return ""
        p, _t, where = self.resolve(sid, sel)
        return f"/folio/covers/{sid}?v={self._tag(sid, p, where)}"


def cover_for(song_id, bundled_dir: str = "", sel=None) -> tuple:
    """곡 번호 → (파일 경로 | None, content-type, 어디서: "pick" | "own" | "default" | "gen" | "bundled" | "fallback").
    "gen" 이면 경로가 없다 — 그림은 `cover_generated(번호)` 가 그린다.

    `sel` 을 안 주면 번호 DB(`songs.json`)에서 그 번호의 `cover` 를 찾아 쓴다.
    `song_id` 가 숫자가 아니면(`../`·글자) 번호 0 으로 치고 기본 커버로 간다 — 이 길은 **404 가 없다.**"""
    sid = _sid(song_id)
    if sel is None:
        sel = song_cover_sel(sid) if sid > 0 else ""
    return CoverScan(bundled_dir).resolve(sid, sel)


def cover_generated(song_id, bundled_dir: str = "") -> bytes:
    """그 번호의 생성 커버 (번호 DB 의 제목·아티스트로 그린다). 번호가 숫자가 아니면 b"" ."""
    if _sid(song_id) <= 0:
        return b""
    return CoverScan(bundled_dir).generate(song_id)


def song_cover_sel(song_id: int) -> str:
    """번호 → 그 곡에 고른 커버 값 ("" = 자동)."""
    for r in get_songs()["songs"].values():
        if r["id"] == int(song_id):
            return r.get("cover") or ""
    return ""


def cover_file(kind: str, name: str, bundled_dir: str = ""):
    """썸네일 길(`/folio/covers/file/<이름>` = mine/(없으면 바로 아래) · `/folio/covers/pool/<이름>` = 내 기본 풀 ·
    `/folio/covers/bundled/<이름>` = 앱 기본) → (경로, type) | None.
    이름은 **폴더에 실제로 있는 파일 이름과 똑같을 때만** 받는다 (목록에서 고른다 — 경로를 만들지 않는다)."""
    if not isinstance(name, str) or not name:
        return None
    if kind == "file":
        covers_migrate()
        files = {os.path.basename(p): p for p in _cover_files(covers_dir())}
        files.update({os.path.basename(p): p for p in _cover_files(covers_mine_dir())})   # 같은 이름이면 mine/ 이 이긴다
    elif kind == "pool":
        files = {os.path.basename(p): p for p in _cover_files(covers_pool_dir())}
    elif kind == "bundled":
        files = {os.path.basename(p): p for p in _cover_files(bundled_dir)} if bundled_dir else {}
    else:
        return None
    p = files.get(name)
    return (p, COVER_TYPES[os.path.splitext(p)[1].lower()]) if p else None


def cover_choices(bundled_dir: str = "") -> dict:
    """커버 고르기 시트의 칸 — {"defaults": [...], "mine": [...]}. 한 칸 = {name, value, url}.
    `value` 가 `songs.json` 의 `cover` 에 들어가는 값이다. 주소에는 `?v=` 가 붙는다 (그림이 바뀌면 주소도)."""
    from urllib.parse import quote
    scan = CoverScan(bundled_dir)
    return {"defaults": [{"name": n, "value": COVER_BUNDLED_PREFIX + n,
                         "url": f"/folio/covers/bundled/{quote(n)}?v={_file_tag(p)}"}
                        for n, p in scan.bundled_by.items()],
            "mine": [{"name": n, "value": n, "url": f"/folio/covers/file/{quote(n)}?v={_file_tag(p)}"}
                     for n, p in scan.mine.items()]}


def _cover_entry(n: str, p: str, route: str) -> dict:
    from urllib.parse import quote
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    return {"name": n, "url": f"/folio/covers/{route}/{quote(n)}?v={_file_tag(p)}", "size": size}


def covers_manage(bundled_dir: str = "") -> dict:
    """「내 커버 관리」 화면의 목록.

    `mine`: 내 이미지(mine/) 한 장마다 {name, url, size, usedBy:[{id, title}]} — usedBy = 그 이름을 `cover` 로
    고른 곡 (번호 순). `numbered`: 바로 아래 번호 이름 파일 {name, url, size, id, title}. `userDefaults`: 내 기본 풀.
    `defaults`: 앱 기본 (value 는 `default:<이름>`, usedBy 도 붙는다). 경로는 폴더 목록에서만 만든다."""
    scan = CoverScan(bundled_dir)
    songs = sorted(get_songs()["songs"].values(), key=lambda r: r["id"])
    used: dict = {}
    for r in songs:
        c = r.get("cover") or ""
        if c:
            used.setdefault(c, []).append({"id": r["id"], "title": r.get("song") or r.get("title") or r["key"]})
    by_id = {r["id"]: r for r in songs}
    mine = []
    for n, p in scan.mine.items():
        e = _cover_entry(n, p, "file")
        e["usedBy"] = used.get(n, [])
        src = _online_src_for(n, songs) if n.startswith(ONLINE_PREFIX) else None
        if src:                                    # 온라인에서 받은 그림 — 출처를 같이 보인다 (개인 감상용)
            e["src"] = dict(src, sourceName=ONLINE_SOURCE_KO.get(src["source"], src["source"]))
        mine.append(e)
    # 생성 커버를 쓰는 곡 (「번호대로」의 마지막 차례) — 관리 화면 「기본 커버」 탭의 보기 (앞 24곡)
    generated = []
    gen_count = 0
    for r in songs:
        if (r.get("cover") and scan.picked(r["cover"])) or scan.auto(r["id"])[2] != "gen":
            continue
        gen_count += 1
        if len(generated) < 24:
            title, artist = _song_meta(r)
            generated.append({"id": r["id"], "title": title, "artist": artist, "family": GEN_FAMILY_KO[gen_family(r["id"])],
                              "url": scan.auto_url(r["id"])})
    numbered = []
    for sid, p in sorted(scan.own.items()):
        e = _cover_entry(os.path.basename(p), p, "file")
        r = by_id.get(sid) or {}
        e.update(id=sid, title=r.get("song") or r.get("title") or "")
        numbered.append(e)
    defaults = []
    for n, p in scan.bundled_by.items():
        e = _cover_entry(n, p, "bundled")
        e["value"] = COVER_BUNDLED_PREFIX + n
        e["usedBy"] = used.get(COVER_BUNDLED_PREFIX + n, [])
        defaults.append(e)
    return {"mine": mine, "numbered": numbered, "defaults": defaults, "generated": generated, "generatedCount": gen_count,
            "userDefaults": [_cover_entry(os.path.basename(p), p, "pool") for p in scan.pool],
            "folder": covers_dir(),
            "folders": {"root": covers_dir(), "mine": covers_mine_dir(), "default": covers_pool_dir()}}


def _online_src_for(name: str, records) -> dict | None:
    """`covers/mine/online-<번호>-<출처>.jpg` 의 출처 — 그 그림을 고른 곡의 기록(`cover_src`)에서, 없으면 이름에서."""
    for r in records:
        if (r.get("cover") or "") == name and r.get("cover_src"):
            return dict(r["cover_src"])
    m = re.fullmatch(re.escape(ONLINE_PREFIX) + r"\d+-(itunes|deezer)\.\w+", name or "")
    return {"source": m.group(1), "link": "", "at": 0.0} if m else None


def image_size(p: str) -> tuple:
    """그림 파일의 (가로, 세로) — 앞머리만 읽는다 (png · jpg · gif · webp). 모르면 (0, 0)."""
    import struct
    try:
        with open(p, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return 0, 0
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", head[6:10])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            kind = head[12:16]
            if kind == b"VP8X":
                return 1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little")
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                b = int.from_bytes(head[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if head[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(head):
                if head[i] != 0xFF:
                    i += 1
                    continue
                mk = head[i + 1]
                if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                    i += 2
                    continue
                seg = int.from_bytes(head[i + 2:i + 4], "big")
                if mk in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", head[i + 5:i + 9])
                    return w, h
                i += 2 + seg
    except (struct.error, IndexError):
        pass
    return 0, 0


_SQUARE_MEMO: dict = {}


def is_square(p) -> bool:
    """거의 정사각형(앨범 그림)인가 — 연출 카드가 흐린 확대 배경 위에 원본을 가운데 둔다 (opening2.html `.sq`)."""
    if not p:
        return False
    tag = _file_tag(p)
    hit = _SQUARE_MEMO.get((p, tag))
    if hit is None:
        w, h = image_size(p)
        hit = bool(w and h and 0.88 <= w / h <= 1.14)
        if len(_SQUARE_MEMO) > 512:
            _SQUARE_MEMO.clear()
        _SQUARE_MEMO[(p, tag)] = hit
    return hit


def cover_delete(name: str) -> str:
    """내 이미지(mine/) 한 장을 지운다 → 지운 이름. **mine/ 안의 그림만**, 이름이 목록에 똑같이 있을 때만
    (받은 글자로 경로를 만들지 않는다). 어느 곡이든 그 그림을 고른 채면 지우지 않는다 — 먼저 그 곡의 커버를 바꾼다.
    ValueError: "bad_name" · "no_file" · "in_use"."""
    if not isinstance(name, str) or not name or _cover_sel_clean(name) != name or name.startswith(COVER_BUNDLED_PREFIX):
        raise ValueError("bad_name")
    with LOCK:
        files = {os.path.basename(p): p for p in _cover_files(covers_mine_dir())}
        p = files.get(name)
        if not p:
            raise ValueError("no_file")
        if any((r.get("cover") or "") == name for r in get_songs()["songs"].values()):
            raise ValueError("in_use")
        os.remove(p)
    return name


def song_set_cover(key: str = "", song_id=None, cover: str = "", bundled_dir: str = "") -> dict:
    """곡 하나의 커버를 고른다 (`cover` 가 "" 이면 「번호대로(자동)」로 되돌린다) → 바뀐 기록.

    곡은 key 로도, 번호로도 찾는다. 값은 **지금 폴더에 있는 것만** 받는다 — 없는 파일을 적어 두면
    화면에는 고른 것으로 보이는데 연출은 자동 커버가 나와서 「안 된다」가 된다.
    ValueError: "no_song" · "bad_cover" · "no_file"."""
    raw = cover if isinstance(cover, str) else ""
    sel = _cover_sel_clean(raw)
    if raw.strip() and not sel:
        raise ValueError("bad_cover")
    if sel and CoverScan(bundled_dir).picked(sel) is None:
        raise ValueError("no_file")
    with LOCK:
        d = get_songs()
        songs = d["songs"]
        rec = songs.get(key) if key else None
        if rec is None and song_id is not None:
            sid = _sid(song_id)
            rec = next((r for r in songs.values() if r["id"] == sid), None) if sid else None
        if rec is None:
            raise ValueError("no_song")
        if rec.get("cover") != sel:
            # 다른 것을 고르면 온라인 출처 기록도 그 그림과 함께 내려놓는다. 온라인에서 받은 그림(`online-…`)을
            # 다시 고르면 그 그림의 출처를 되살린다 (다른 곡이 가진 기록 → 없으면 이름의 출처만)
            rec["cover_src"] = _online_src_for(sel, songs.values()) if sel.startswith(ONLINE_PREFIX) else None
        rec["cover"] = sel
        _songs_save(d)
        return dict(rec)


# ── 내 이미지 올리기 (커버 고르기 시트의 「내 이미지 추가…」) ──────────────────
MAX_UPLOAD_COVER = 8 * 1024 * 1024
# 올리는 길은 **그림 네 가지만** 받는다 — 앞머리 바이트로 본다 (이름·Content-Type 은 믿지 않는다).
# SVG 는 받지 않는다: 스크립트를 품을 수 있고, 올린 파일은 같은 주소(우리 출처)에서 나간다.
# (사람이 폴더에 직접 넣은 SVG 는 그대로 쓴다 — 그건 이 PC 주인이 넣은 것이다.)
_UPLOAD_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))
_UPLOAD_STEM_BAD = re.compile(r"[^\w\-. ()가-힣]+")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(10)} | {f"LPT{i}" for i in range(10)}


def cover_upload_kind(data: bytes) -> str:
    """앞머리 바이트 → 확장자 ("" = 그림이 아니다)."""
    for magic, ext in _UPLOAD_MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def cover_upload_save(data: bytes, name: str = "") -> str:
    """올린 그림을 **내 이미지 폴더(`covers/mine/`)** 에 **안전한 새 이름**으로 저장 → 파일 이름.

    이름: 보낸 이름의 몸통에서 글자·숫자·한글·`-_.() ` 만 남기고(40자까지), 확장자는 **내용으로 정한다.**
    숫자만 남으면(`12`) 앞에 `img-` 를 붙인다 — 번호 이름이 되면 12번 곡의 커버가 저절로 바뀌기 때문이다.
    같은 이름이 있으면 `-2`, `-3` … 을 붙인다 (덮어쓰지 않는다). ValueError: "empty" · "too_large" · "not_image"."""
    if not data:
        raise ValueError("empty")
    if len(data) > MAX_UPLOAD_COVER:
        raise ValueError("too_large")
    ext = cover_upload_kind(data)
    if not ext:
        raise ValueError("not_image")
    stem = os.path.splitext(os.path.basename(str(name or "").replace("\\", "/")))[0]
    stem = _UPLOAD_STEM_BAD.sub("", stem).strip(" .")[:40].strip(" .") or "cover"
    if _COVER_NAME.fullmatch(stem + ext) or stem.lstrip("#").isdigit() or stem.split(".")[0].upper() in _WIN_RESERVED:
        stem = "img-" + stem          # 번호 이름이 되거나 윈도우 예약 이름(CON·NUL…)이면
    covers_migrate()                   # 옛 폴더면 먼저 정리 — 올린 그림과 옮긴 그림의 이름이 겹치지 않게
    d = covers_mine_dir()
    with LOCK:
        os.makedirs(d, exist_ok=True)
        taken = {n.lower() for n in os.listdir(d)}
        cand, i = stem + ext, 1
        while cand.lower() in taken:
            i += 1
            cand = f"{stem}-{i}{ext}"
        tmp = os.path.join(d, f".{cand}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(d, cand))
    return cand


# ── 온라인에서 찾기 (선택 — 사용자가 누를 때만) ─────────────────────────────
# 기본은 생성 커버이고, 선택으로 **사용자가 직접 찾아 고르는** 앨범 그림을 쓸 수 있다.
# 앨범 그림은 저작권이 있다. 그래서:
#   · 앱이 알아서 모아 오지 않는다 — 커버 고르기 시트에서 사람이 「온라인에서 찾기」를 누를 때 **그 곡 하나만** 찾는다.
#   · 사람이 후보 중 **한 장을 골라야** 받는다. 받은 그림은 이 PC 의 `covers/mine/online-<번호>-<출처>.<확장자>` 에만 둔다.
#   · 출처(어디서 · 원래 페이지 주소 · 받은 때)를 번호 기록(`cover_src`)에 남기고 관리 화면에 보인다.
#   · 백업 zip·공유에 넣지 않는다 (커버 폴더는 원래 백업에 없다 — 검사로 지킨다).
# 찾는 곳: iTunes Search API 먼저, 없으면 Deezer. 받는 주소는 **방금 찾은 후보 중 하나**이고 그 서비스의 그림 서버일 때만
# 받는다 (이 PC 서버가 아무 주소나 받아 오는 통로가 되지 않게).
ONLINE_SOURCES = ("itunes", "deezer")
ONLINE_SOURCE_KO = {"itunes": "Apple Music (iTunes)", "deezer": "Deezer"}
ONLINE_TIMEOUT = 10                     # 초 — 한 번 요청
ONLINE_MAX_BYTES = 8 * 1024 * 1024      # 받는 그림 상한
ONLINE_THUMB_MAX = 512 * 1024           # 후보 썸네일 상한
ONLINE_JSON_MAX = 2 * 1024 * 1024
ONLINE_LIMIT = 6
ONLINE_PREFIX = "online-"               # mine/ 안에서 온라인에서 받은 그림의 이름 앞자리
_ONLINE_IMG_HOSTS = ("mzstatic.com", "dzcdn.net")                            # 그림을 받는 곳 (끝자리)
_ONLINE_LINK_HOSTS = ("apple.com", "deezer.com")                              # 출처 페이지 (끝자리)
_ONLINE_UA = "MobiWorks/1 (personal cover search; +user-initiated)"
_ONLINE_SEEN: dict = {}                 # 번호 → {그림 주소: {source, link}} — 방금 보여 준 후보만 받는다


def _host_ok(url: str, hosts: tuple) -> bool:
    from urllib.parse import urlparse
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return False
    h = (u.hostname or "").lower()
    return u.scheme == "https" and bool(h) and any(h == x or h.endswith("." + x) for x in hosts)


def _http_get(url: str, limit: int, timeout: float = ONLINE_TIMEOUT) -> tuple:
    """GET → (바이트, Content-Type, 마지막 주소). 상한을 넘으면 ValueError("too_large").
    네트워크가 없거나 막히면 urllib 의 URLError/OSError 가 그대로 올라간다 (부르는 쪽이 친절한 말로 바꾼다)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _ONLINE_UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:     # noqa: S310 — https 만, 호스트는 부르는 쪽이 고른다
        ctype = str(r.headers.get("Content-Type") or "")
        try:
            n = int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > limit:
            raise ValueError("too_large")
        data = r.read(limit + 1)
        final = r.geturl() if hasattr(r, "geturl") else url
    if len(data) > limit:
        raise ValueError("too_large")
    return data, ctype, final


def _online_itunes(term: str) -> list:
    from urllib.parse import urlencode
    data, _ct, _u = _http_get("https://itunes.apple.com/search?" + urlencode(
        {"term": term, "entity": "song", "country": "KR", "limit": ONLINE_LIMIT}), ONLINE_JSON_MAX)
    j = json.loads(data.decode("utf-8", "replace"))
    out = []
    for r in (j.get("results") or []) if isinstance(j, dict) else []:
        art = str((r or {}).get("artworkUrl100") or "")
        if not art or "100x100bb" not in art:
            continue
        out.append({"source": "itunes", "title": str(r.get("trackName") or ""), "artist": str(r.get("artistName") or ""),
                    "album": str(r.get("collectionName") or ""),
                    "thumbUrl": art.replace("100x100bb", "300x300bb"), "full": art.replace("100x100bb", "1000x1000bb"),
                    "link": str(r.get("trackViewUrl") or r.get("collectionViewUrl") or "")})
    return out


def _online_deezer(term: str) -> list:
    from urllib.parse import urlencode
    data, _ct, _u = _http_get("https://api.deezer.com/search?" + urlencode({"q": term, "limit": ONLINE_LIMIT}), ONLINE_JSON_MAX)
    j = json.loads(data.decode("utf-8", "replace"))
    out = []
    for r in (j.get("data") or []) if isinstance(j, dict) else []:
        al = (r or {}).get("album") or {}
        full = str(al.get("cover_xl") or al.get("cover_big") or "")
        if not full:
            continue
        out.append({"source": "deezer", "title": str(r.get("title") or ""), "artist": str(((r.get("artist") or {}).get("name")) or ""),
                    "album": str(al.get("title") or ""), "thumbUrl": str(al.get("cover_medium") or full), "full": full,
                    "link": str(r.get("link") or "")})
    return out


def _thumb_data(url: str) -> str:
    """후보 썸네일 → data: 주소 (화면의 CSP 는 img-src 'self' data: 라 바깥 그림을 바로 못 쓴다). 못 받으면 ""."""
    if not _host_ok(url, _ONLINE_IMG_HOSTS):
        return ""
    import base64
    try:
        data, _ct, final = _http_get(url, ONLINE_THUMB_MAX, timeout=6)
    except (OSError, ValueError):
        return ""
    ext = cover_upload_kind(data)
    if not ext or not _host_ok(final, _ONLINE_IMG_HOSTS):
        return ""
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}[ext]
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _song_find(key: str = "", song_id=None) -> dict | None:
    songs = get_songs()["songs"]
    rec = songs.get(key) if key else None
    if rec is None and song_id is not None:
        sid = _sid(song_id)
        rec = next((r for r in songs.values() if r["id"] == sid), None) if sid else None
    return rec


def cover_search_online(key: str = "", song_id=None, q: str = "") -> dict:
    """「온라인에서 찾기」 — 그 곡의 제목·아티스트로 iTunes → (없으면) Deezer 를 찾는다.
    → {query, source, items: [{source, title, artist, album, thumb(data:), full, link}]}.
    ValueError: "no_song" · "offline"(네트워크) · "bad_answer"(받은 답이 이상하다)."""
    rec = _song_find(key, song_id)
    if rec is None:
        raise ValueError("no_song")
    title, artist = _song_meta(rec)
    term = " ".join(str(q or "").split())[:120] or " ".join(x for x in (title, artist) if x)
    if not term:
        raise ValueError("no_song")
    items, source, net_err = [], "", None
    for name, fn in (("itunes", _online_itunes), ("deezer", _online_deezer)):
        try:
            got = fn(term)
        except (OSError, ValueError) as e:          # URLError·시간 초과는 OSError, 망가진 JSON 은 ValueError
            print(f"[covers] 온라인 찾기 {name} 실패: {type(e).__name__}: {e}", flush=True)
            net_err = e
            continue
        print(f"[covers] 온라인 찾기 {name}: 「{term}」 → {len(got)}건", flush=True)
        if got:
            items, source = got, name
            break
    if not items and net_err is not None:
        raise ValueError("offline" if isinstance(net_err, OSError) else "bad_answer")
    seen, uniq = set(), []
    for it in items:                                # 같은 앨범의 여러 곡 = 같은 그림 — 한 칸만
        if it["full"] in seen or not _host_ok(it["full"], _ONLINE_IMG_HOSTS):
            continue
        seen.add(it["full"])
        if not _host_ok(it["link"], _ONLINE_LINK_HOSTS):
            it["link"] = ""
        uniq.append(it)
    uniq = uniq[:ONLINE_LIMIT]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        thumbs = list(ex.map(lambda it: _thumb_data(it["thumbUrl"]), uniq))
    out = []
    for it, th in zip(uniq, thumbs):
        out.append({"source": it["source"], "sourceName": ONLINE_SOURCE_KO[it["source"]], "title": it["title"][:200],
                    "artist": it["artist"][:200], "album": it["album"][:200], "thumb": th, "full": it["full"], "link": it["link"]})
    with LOCK:
        if len(_ONLINE_SEEN) > 64:
            _ONLINE_SEEN.clear()
        _ONLINE_SEEN[rec["id"]] = {x["full"]: {"source": x["source"], "link": x["link"]} for x in out}
    return {"query": term, "source": source, "items": out}


def online_cover_name(song_id: int, source: str, ext: str) -> str:
    return f"{ONLINE_PREFIX}{int(song_id)}-{source}{ext}"


def cover_fetch_online(key: str = "", song_id=None, url: str = "", source: str = "", link: str = "") -> dict:
    """후보 한 장을 받아 `covers/mine/online-<번호>-<출처>.<확장자>` 로 두고 그 곡의 커버로 고른다 → 바뀐 기록.
    받는 주소는 **그 곡에서 방금 찾은 후보**여야 하고 그 서비스의 그림 서버(https)여야 한다. 그림(png·jpg·webp·gif —
    앞머리 바이트로 본다)·8MB 까지·10초. 출처는 `cover_src = {source, link, at}` 로 번호 기록에 남는다.
    ValueError: "no_song" · "bad_url" · "offline" · "too_large" · "not_image"."""
    rec = _song_find(key, song_id)
    if rec is None:
        raise ValueError("no_song")
    sid = rec["id"]
    with LOCK:
        cand = (_ONLINE_SEEN.get(sid) or {}).get(str(url or ""))
    if cand is None or not _host_ok(url, _ONLINE_IMG_HOSTS) or (source and source != cand["source"]):
        raise ValueError("bad_url")
    source = cand["source"]
    try:
        data, ctype, final = _http_get(url, ONLINE_MAX_BYTES)
    except ValueError:
        raise
    except OSError as e:
        raise ValueError("offline") from e
    if not _host_ok(final, _ONLINE_IMG_HOSTS):
        raise ValueError("bad_url")
    ext = cover_upload_kind(data)
    if not ext or not ctype.lower().startswith("image/"):
        raise ValueError("not_image")
    name = online_cover_name(sid, source, ext)
    covers_migrate()
    d = covers_mine_dir()
    with LOCK:
        os.makedirs(d, exist_ok=True)
        for old in os.listdir(d):                   # 같은 곡·같은 출처의 옛 확장자 파일은 비운다 (한 곡 한 출처 한 장)
            if old != name and old.startswith(f"{ONLINE_PREFIX}{sid}-{source}.") and \
                    not any((r.get("cover") or "") == old for r in get_songs()["songs"].values()):
                try:
                    os.remove(os.path.join(d, old))
                except OSError:
                    pass
        tmp = os.path.join(d, f".{name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(d, name))
        db = get_songs()
        r = next((x for x in db["songs"].values() if x["id"] == sid), None)
        if r is None:
            raise ValueError("no_song")
        r["cover"] = name
        r["cover_src"] = {"source": source, "link": cand["link"] if _host_ok(cand["link"], _ONLINE_LINK_HOSTS) else "",
                          "at": round(time.time(), 1)}
        _songs_save(db)
        return dict(r)


# ── 쓰던 모비폴리오 자료 가져오기 ────────────────────────────────────────

FOLIO_OLD_APP = "MobiFolio"        # 옛 앱이 자료를 두던 폴더 이름 (%LOCALAPPDATA% 아래)
# 볼 차례. **앞의 것이 있으면 뒤는 안 본다** (섞지 않는다 — 두 곳을 합치면 어느 쪽 재생목록이
# 이겼는지 아무도 모른다). `MabiScoreBox` 는 모비폴리오의 옛 이름(악보함) 시절 폴더다.
# 그쪽 exe 는 켜질 때 그 폴더를 `MobiFolio` 로 옮겼지만(engine.py `migrate_legacy`), 옛 판에서
# 모비폴리오를 한 번도 안 켜고 바로 모비웍스로 온 사람은 자료가 옛 폴더에 그대로 있다.
FOLIO_OLD_APPS = (FOLIO_OLD_APP, "MabiScoreBox")
FOLIO_ADOPTED = FOLIO + ".adopted"  # 한 번 가져왔다는 표시


def folio_old_dir() -> str:
    """쓰던 모비폴리오 자료 폴더. 없으면 빈 문자열.

    `MobiFolio\\data` → 없을 때만 `MabiScoreBox\\data`. 파일 이름·모양이 옛것이어도
    `adopt_folio` 가 **있는 파일 중 JSON 으로 읽히는 것만** 가져오므로 따로 가리지 않는다.

    **「있다」는 폴더가 아니라 가져올 파일이 있다는 뜻이다.** 폴더만 보면 빈 `MobiFolio\\data`
    (옛 판을 설치만 했거나 자료를 지운 사람)가 뒤의 `MabiScoreBox\\data` 를 가리고, 그 빈 폴더에서
    아무것도 못 가져온 채 `.adopted` 표시가 남아 **다시는 안 본다** — 자료가 옛 폴더에 그대로 있는데도.
    가져올 이름(`_adoptable_names`)이 하나라도 있는 첫 폴더를 고른다. 둘 다 비었으면 빈 문자열
    → `adopt_folio` 는 표시를 남기지 않고 다음 기동에 다시 본다.

    `MOBIW_FOLIO_OLD_DIR` 는 **개발·검사 전용**이다 — 배포판은 무시한다 (`MOBIW_DATA_DIR` 과 같은 규칙).
    환경변수 하나로 다른 폴더의 자료를 사용자 자료 집에 들이는 길을 배포판에 남기지 않는다."""
    la = os.environ.get("MOBIW_FOLIO_OLD_DIR") or ""        # 검사가 갈아 끼우는 자리
    if la and (not runmode.release() or os.environ.get("MOBIW_DEV") == "1"):
        return la if os.path.isdir(la) else ""
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base or _forced_dir_hides_real_localappdata(base):
        return ""
    for app in FOLIO_OLD_APPS:
        d = os.path.join(base, app, "data")
        if os.path.isdir(d) and any(os.path.isfile(os.path.join(d, n)) for n in _adoptable_names()):
            return d
    return ""


def _forced_dir_hides_real_localappdata(la: str) -> bool:
    """개발용 강제 폴더(`MOBIW_DATA_DIR`)로 뜬 서버는 **진짜** `%LOCALAPPDATA%` 를 보지 않는다.

    실제로 겪은 일이다: 임시 폴더를 줬는데도 서버가 사용자의
    `MobiFolio\\data`(재생목록·아티스트)를 임시 폴더로 **복사해 왔다** — 백업 zip 에 실제 사용자 자료
    사본이 들어갔다. `migrate_legacy` 는 같은 상황을 `_BASE in (MOBIW_DATA_DIR, …)` 로 막는데
    이 길만 열려 있었다. 검사가 `LOCALAPPDATA` 를 가짜 임시 폴더로 갈아 끼운 경우는 그대로
    본다 — 후보 순서(MobiFolio → MabiScoreBox)를 재는 검사가 그 길을 쓴다. 「진짜」는
    `%USERPROFILE%\\AppData\\Local` 과 같은 폴더인지로 가른다."""
    if _BASE not in (os.environ.get("MOBIW_DATA_DIR"), os.environ.get("MABI_DATA_DIR")):
        return False
    prof = os.environ.get("USERPROFILE") or ""
    if not prof:
        return False
    real = os.path.normcase(os.path.normpath(os.path.join(prof, "AppData", "Local")))
    return os.path.normcase(os.path.normpath(la)) == real


def _adoptable_names() -> list:
    """옛 폴더에서 가져오는 파일 이름 (`folio/` 를 뗀 것). 설정은 뺀다 — 키 뜻이 다르다 (remote_scope 등).
    `folio_old_dir`(어느 폴더를 볼까)와 `adopt_folio`(무엇을 가져올까)가 **같은 목록**을 본다."""
    out = []
    for n in _DATA_FILES:
        name = n[len(FOLIO):] if n.startswith(FOLIO) else n
        if name != "settings.json":
            out.append(name)
    return out


def adopt_folio() -> dict:
    """옛 자료를 `data/folio/` 로 **한 번만** 복사한다 → {"moved": [...], "skipped": [...]}.

    **왜 복사인가**: 옮기면(이동) 돌아갈 곳이 없어진다. 통합이 막히면 옛 앱을 그대로
    다시 켤 수 있어야 하므로 **원본은 건드리지 않는다.**

    **왜 한 번인가**: 통합 뒤에 여기서 고친 것을 옛 파일이 덮으면 안 된다. 표시 파일
    (`FOLIO_ADOPTED`)이 있으면 두 번 다시 안 한다.

    **이미 있는 파일은 건너뛴다** — 통합 뒤에 만든 것을 옛것으로 덮지 않는다.
    """
    out = {"moved": [], "skipped": [], "from": ""}
    if os.path.exists(_path(FOLIO_ADOPTED)):
        out["skipped"].append("이미 가져왔습니다")
        return out
    old = folio_old_dir()
    if not old:
        return out                      # 쓰던 게 없다 — 처음 쓰는 사람
    out["from"] = old
    with LOCK:
        os.makedirs(_path(FOLIO.rstrip("/")), exist_ok=True)
        for name in _adoptable_names():   # 설정은 안 가져온다 — 키 뜻이 다르다 (remote_scope 등)
            src, dst = os.path.join(old, name), _path(FOLIO + name)
            if not os.path.exists(src):
                continue
            if os.path.exists(dst):
                out["skipped"].append(name)
                continue
            try:
                with open(src, "rb") as f:
                    body = f.read()
                json.loads(body.decode("utf-8-sig"))     # 못 읽는 것은 안 가져온다
                with open(dst, "wb") as f:
                    f.write(body)
                out["moved"].append(name)
            except Exception as e:
                out["skipped"].append(f"{name} ({type(e).__name__})")
        try:
            save(FOLIO_ADOPTED, {"at": time.time(), "from": old, "moved": out["moved"]})
        except Exception:
            pass
    return out


# ── 개인화 자료 백업·복원 ──────────────────────────────────────────
# 설정·재생목록·곡별 악기 설정 같은 개인화 자료를 zip 하나로 백업하고 한 번에 불러온다.
#
# **묶음은 여기 한 곳에서 안다.** 넣는 것은 사람이 손으로 만든 것 — 게임에서 다시 받을 수 없는
# 것이다. 게임이 다시 주는 것(cache_*·folio/scores·instruments)과 실행 기록(ledger·cli_log·
# recent)은 뺀다.
# `queue.json`(제작·채집 대기열)은 **넣는다** — 사람이 짠 순서·그룹·
# 반복이다. 다만 러너가 도는 중에는 복원이 그 파일을 **건너뛴다**(러너가 끝나며 메모리 사본으로 다시
# 덮어쓰므로 적용해도 사라진다). 그 판정과 되읽기는 서버가 한다(`server._restore_with_queue`).
# `folio/ens.json`(악보별 합주 인원)도 넣는다 — 게임이 알려 주지 않아 사람이 적은 값이다.
# **자료 집은 옮기지 않는다.** `save/`·`setting/` 같은 폴더 구성은 **zip 안의 모양**으로만 둔다 —
# 실제 폴더를 옮기면 이사(`migrate_legacy`)·가져오기(`adopt_folio`) 규칙이 전부 흔들린다.
PERSONAL_FILES = (
    "settings.json",          # 작업·연주·오버레이 설정 (아래 비밀 키는 뺀다)
    "presets.json",           # 제작·채집 큐 프리셋
    "folio/playlists.json",   # 재생목록 — 곡별 악기(`inst`)가 여기 산다
    "folio/presets.json",     # 연주 인사 프리셋
    "folio/artists.json",     # 아티스트·별칭·잡음어
    "folio/durations.json",   # 곡 길이 (사람이 재생해 얻은 값 — 다시 얻으려면 곡마다 다시 틀어야 한다)
    "folio/ens.json",         # 악보별 합주 인원 — 게임이 편성을 알려 주지 않아 사람이 적는다
    "folio/songs.json",       # 곡 번호 DB — 커버 파일 이름이 이 번호다. 다시 매길 수 없다
    "queue.json",             # 제작·채집 대기열 — 러너가 도는 중이면 복원에서 건너뛴다
)
QUEUE_FILE = "queue.json"   # 복원 때 따로 다루는 파일 (러너 상태에 매인다)
BACKUP_ROOT = "mobiworks-backup"        # zip 안의 한 폴더 — 풀면 이 폴더 하나가 나온다
BACKUP_MANIFEST = "manifest.json"
# **비밀은 백업에 넣지 않는다.** 인증키는 밖으로 나가는 문의 유일한 자물쇠이고, 터널 주소·사이트
# 주소는 그 문의 위치다. zip 은 파일이라 옮겨 다닌다 — 복원할 때는 지금 값이 그대로 남는다.
BACKUP_SECRET_KEYS = ("remote_key", "remote_host", "remote_origin")
# **이 PC 의 파일을 가리키는 설정도 넣지 않는다.** 다른 PC 에서는 뜻이 없고,
# zip 이 옮겨 다니면 이 PC 의 폴더 구성(사용자 이름이 든 경로)이 따라 나간다. 복원은 어차피 무시한다
# (`RESTORE_IGNORE_KEYS`). 기본값 표(`DEFAULTS`)에서 경로·파일을 담는 키 전부다:
#   cli_exe              CLI exe 절대 경로
# (「연출 영상」의 경로 키 두 개는 기능과 함께 없앴다 — 옛 파일에 남은 것은 RETIRED_SETTINGS 로 뺀다.)
BACKUP_LOCAL_PATH_KEYS = ("cli_exe",)
# 복원 파일에 있어도 **무시하는** 설정 키 — server.REMOTE_SETTINGS_NEVER 와 같은 묶음이다.
#   remote*               밖으로 나가는 문 (범위 상승·자물쇠 교체)
#   cli_exe               임의 exe 경로 → 다음 「연결 확인」이 그 파일을 띄운다
#   update_url            남의 exe 를 받아 실행하게 된다
RESTORE_IGNORE_KEYS = ("cli_exe", "update_url")
RESTORE_IGNORE_PREFIX = ("remote",)
MAX_BACKUP_MEMBER = 4 * 1024 * 1024      # 풀었을 때 한 파일 상한 (zip 폭탄 방지 — 가장 큰 재생목록도 수십 KB 다)
MAX_BACKUP_TOTAL = 16 * 1024 * 1024      # 풀었을 때 전체 상한
BACKUP_KEEP_PREFIX = "backup-before-restore-"   # 복원 직전 자동 보관 파일 이름 앞자리 (DATA_DIR 안)
BACKUP_KEEP_MAX = 5   # 그 보관본은 최근 이만큼만 남긴다 — 복원할 때마다 한 벌씩 끝없이 쌓였다


def _restore_ignored(k) -> bool:
    k = str(k)
    return k in RESTORE_IGNORE_KEYS or k.startswith(RESTORE_IGNORE_PREFIX)


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def backup_filename(ts: float | None = None) -> str:
    return "mobiworks-backup-" + time.strftime("%Y%m%d-%H%M", time.localtime(ts or time.time())) + ".zip"


def _queue_items_without_log(items: list) -> list:
    """백업용 대기열 항목 — 카드·그룹의 `log`(실행 기록)를 뺀 사본. 백업은 사람이 짠 **상태**를
    옮기는 것이지 실행 기록을 옮기는 것이 아니다 (ledger·cli_log 를 빼는 것과 같은 원칙). 원본은 건드리지 않는다.
    복원 쪽은 그대로다 — `log` 가 든 옛 백업도 받고, 없는 `log` 는 대기열이 읽을 때 빈 목록으로 메운다."""
    out: list = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it)
            continue
        c = {k: v for k, v in it.items() if k != "log"}
        if isinstance(c.get("items"), list):          # 그룹 — 자식 카드의 기록도 뺀다
            c["items"] = _queue_items_without_log(c["items"])
        out.append(c)
    return out


def backup_bytes(version: str = "") -> bytes:
    """개인화 자료를 zip 한 벌로 (메모리). 있는 파일만 넣고, 없는 파일은 manifest 에 없다.
    `settings.json` 은 비밀 키를 뺀 사본이다. manifest 에 파일마다 sha256 을 적어 복원 때 대조한다."""
    files: dict = {}
    with LOCK:
        for name in PERSONAL_FILES:
            if not os.path.exists(_path(name)):
                continue
            if name == "settings.json":
                body = {k: v for k, v in _load_dict(name).items()
                        if k not in BACKUP_SECRET_KEYS and k not in BACKUP_LOCAL_PATH_KEYS
                        and k not in RETIRED_SETTINGS}
            elif name == QUEUE_FILE:
                # 저장본은 늘 {items, config} 지만, 옛 파일·손으로 고친 파일이 모양을 어겨도 **백업이 복원에서
                # 거절되지 않게** 여기서 모양을 맞춘다 (없는 칸은 빈 값 — workqueue 가 읽을 때도 그렇게 본다).
                d = load(name, None)
                if not isinstance(d, dict):
                    continue
                body = {"items": _queue_items_without_log(d.get("items") if isinstance(d.get("items"), list) else []),
                        "config": d.get("config") if isinstance(d.get("config"), dict) else {}}
            else:
                body = load(name, None)
                if body is None:
                    continue
            files[name] = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
    manifest = {
        "app": "MobiWorks", "version": str(version or ""), "schema": SCHEMA,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "files": {n: {"sha256": _sha256(b), "bytes": len(b)} for n, b in files.items()},
        # 왜 없는지를 파일 안에 적는다 — 복원한 사람이 「인증키가 안 돌아왔다」를 사고로 읽지 않게
        # **키 이름을 적지 않는다** — zip 어디에도 인증키 키 이름 글자가 없어야 한다. 사람이 읽는 말로만.
        "omitted": {"인증키 · 원격 접속 주소 · 사이트 주소":
                    "비밀(밖에서 접속 자물쇠·문 위치) — 백업에 넣지 않는다. 복원해도 지금 값이 남는다.",
                    "CLI 경로":
                    "이 PC 의 파일 경로 — 백업에 넣지 않는다. 복원해도 지금 값이 남는다."},
        "excluded": {"cache_*.json · folio/scores.json · folio/instruments.json": "게임에서 다시 받는다",
                     "recipes.json · ledger.jsonl · folio/cli_log.json · folio/recent.json": "관찰·실행 기록",
                     # 온라인에서 받은 앨범 그림(covers/mine/online-*)은 개인 감상용이다 — 백업·공유로 퍼뜨리지 않는다
                     "folio/covers/ (온라인에서 받은 그림 포함)": "이 PC 의 그림 — 넣지 않는다 (옮길 때는 폴더째 복사)"},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(BACKUP_ROOT + "/" + BACKUP_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
        for n, b in files.items():
            z.writestr(BACKUP_ROOT + "/" + n, b)
    return buf.getvalue()


def _bad(error: str, message: str, **extra) -> dict:
    return {"ok": False, "error": error, "message": message, **extra}


def _member_name(raw: str):
    """zip 항목 이름 → 묶음 안의 상대 이름, 또는 거절 사유. 화이트리스트 밖은 전부 거절한다 (zip slip).
    `mobiworks-backup/settings.json` 도 `settings.json` 도 받는다 (사람이 폴더째 다시 묶어도 되게)."""
    if not raw or raw != raw.strip() or "\\" in raw or raw.startswith("/") or ":" in raw or "\0" in raw:
        return None, "bad_path"
    parts = raw.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None, "bad_path"
    if parts[0] == BACKUP_ROOT:
        parts = parts[1:]
    rel = "/".join(parts)
    if rel == BACKUP_MANIFEST or rel in PERSONAL_FILES:
        return rel, ""
    return None, "unknown_file"


def _queue_shape(o) -> bool:
    """대기열 저장본의 모양 — `workqueue.Queue._save` 가 쓰는 그대로 {items: list, config: dict}."""
    return isinstance(o, dict) and isinstance(o.get("items"), list) and isinstance(o.get("config"), dict)


def _songs_shape(o) -> bool:
    """곡 번호 DB — {next: 정수, songs: {key: {id: 정수, …}}}."""
    return (isinstance(o, dict) and isinstance(o.get("next"), int) and isinstance(o.get("songs"), dict)
            and all(isinstance(r, dict) and isinstance(r.get("id"), int) for r in o["songs"].values()))


# 파일마다 모양 검사 — 타입이면 isinstance, 함수면 그 함수가 참이어야 한다
_SHAPE = {"settings.json": dict, "presets.json": list, "folio/playlists.json": dict,
          "folio/presets.json": dict, "folio/artists.json": dict, "folio/durations.json": dict,
          "folio/ens.json": dict, "folio/songs.json": _songs_shape, QUEUE_FILE: _queue_shape}


def _shape_ok(rel: str, obj) -> bool:
    rule = _SHAPE[rel]
    return isinstance(obj, rule) if isinstance(rule, type) else bool(rule(obj))


def restore_check(data: bytes) -> dict:
    """zip 을 **적용하지 않고** 검사만. 통과하면 {"ok":True, "files":{이름: 객체}, "manifest":{…}}.
    거절 사유(error): not_zip · too_big · bad_path · unknown_file · symlink · no_manifest · bad_manifest ·
    bad_json · bad_shape · hash_mismatch · not_in_manifest · missing_file · empty"""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return _bad("not_zip", "zip 파일이 아닙니다.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError):
        return _bad("not_zip", "zip 파일이 아닙니다.")
    total = 0
    members: dict = {}
    for zi in infos:
        if zi.is_dir():
            continue
        if (zi.external_attr >> 16) & 0o170000 == 0o120000:   # 심볼릭 링크 항목
            return _bad("symlink", f"심볼릭 링크는 받지 않습니다: {zi.filename}", file=zi.filename)
        rel, why = _member_name(zi.filename)
        if not rel:
            return _bad(why, f"백업에 있을 수 없는 파일입니다: {zi.filename}", file=zi.filename)
        if zi.file_size > MAX_BACKUP_MEMBER:
            return _bad("too_big", f"파일이 너무 큽니다: {rel}", file=rel)
        total += zi.file_size
        if total > MAX_BACKUP_TOTAL:
            return _bad("too_big", "백업이 너무 큽니다.")
        if rel in members:
            return _bad("bad_path", f"같은 파일이 두 번 있습니다: {rel}", file=rel)
        try:
            body = z.read(zi)
        except Exception:
            return _bad("not_zip", f"항목을 풀지 못했습니다: {rel}", file=rel)
        if len(body) > MAX_BACKUP_MEMBER:      # 머리의 크기와 실제가 다를 수 있다 — 푼 뒤에도 잰다
            return _bad("too_big", f"파일이 너무 큽니다: {rel}", file=rel)
        members[rel] = body
    if BACKUP_MANIFEST not in members:
        return _bad("no_manifest", "manifest.json 이 없습니다 — 모비웍스가 만든 백업이 아닙니다.")
    try:
        manifest = json.loads(members.pop(BACKUP_MANIFEST).decode("utf-8-sig"))
        listed = manifest["files"]
        assert isinstance(manifest, dict) and isinstance(listed, dict)
        assert all(isinstance(v, dict) and isinstance(v.get("sha256"), str) for v in listed.values())
    except Exception:
        return _bad("bad_manifest", "manifest.json 을 읽지 못했습니다.")
    if not members:
        return _bad("empty", "복원할 파일이 하나도 없습니다.")
    missing = sorted(n for n in listed if n not in members)
    if missing:
        return _bad("missing_file", f"manifest 에는 있는데 zip 에 없는 파일: {', '.join(missing)}", files=missing)
    files: dict = {}
    for rel, body in members.items():
        if rel not in listed:
            return _bad("not_in_manifest", f"manifest 에 없는 파일입니다: {rel}", file=rel)
        if not hmac.compare_digest(_sha256(body), str(listed[rel]["sha256"])):
            return _bad("hash_mismatch", f"파일이 manifest 와 다릅니다 (손상되었거나 손으로 고쳤습니다): {rel}", file=rel)
        try:
            obj = json.loads(body.decode("utf-8-sig"))
        except Exception:
            return _bad("bad_json", f"JSON 을 읽지 못했습니다: {rel}", file=rel)
        if not _shape_ok(rel, obj):
            return _bad("bad_shape", f"파일 모양이 다릅니다: {rel}", file=rel)
        files[rel] = obj
    return {"ok": True, "files": files, "manifest": manifest}


def keep_before_restore(version: str = "") -> str:
    """복원 직전에 지금 자료를 DATA_DIR 안에 한 벌 보관한다 → 파일 이름. 같은 초에 두 번이면 뒤에 번호."""
    os.makedirs(DATA_DIR, exist_ok=True)
    data = backup_bytes(version)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    name = f"{BACKUP_KEEP_PREFIX}{stamp}.zip"
    n = 1
    while os.path.exists(_path(name)):
        n += 1
        name = f"{BACKUP_KEEP_PREFIX}{stamp}-{n}.zip"
    tmp = _path(f"{name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, _path(name))
    return name


def prune_before_restore(keep: int = BACKUP_KEEP_MAX) -> list:
    """`backup-before-restore-*.zip` 을 최근 `keep` 개만 남기고 지운다 → 지운 파일 이름 목록 (오래된 것부터).
    순서는 **이름**으로 잰다 — 이름에 초 단위 시각이 박혀 있고(같은 초면 `-2`…), 수정 시각은 복사로 바뀐다.
    지우지 못한 파일(열려 있음 등)은 조용히 남긴다 — 정리 실패가 복원 성공을 실패로 만들지 않는다."""
    try:
        names = [n for n in os.listdir(DATA_DIR)
                 if n.startswith(BACKUP_KEEP_PREFIX) and n.endswith(".zip") and os.path.isfile(_path(n))]
    except OSError:
        return []

    def order(n: str):
        # `YYYYMMDD-HHMMSS` 또는 `YYYYMMDD-HHMMSS-N` — 같은 초의 N 은 글자가 아니라 수로 (`-10` 이 `-9` 뒤)
        parts = n[len(BACKUP_KEEP_PREFIX):-len(".zip")].split("-")
        dup = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        return ("-".join(parts[:2]), dup, n)

    names.sort(key=order)
    gone = []
    for n in names[:max(0, len(names) - max(0, int(keep)))]:
        try:
            os.remove(_path(n))
            gone.append(n)
        except OSError:
            pass
    return gone


def restore_apply(data: bytes, version: str = "", skip: dict | None = None) -> dict:
    """검사(restore_check) → 지금 자료 자동 보관 → 파일 단위로 원자적 적용(save = tmp + os.replace).
    settings.json 은 **덮어쓰지 않고 합친다**: 무시 키(RESTORE_IGNORE_KEYS·remote*)와 스키마 표시는
    지금 값이 남고, 나머지 키만 백업 값으로 바뀐다. 돌려주는 것: applied(적용한 파일)·ignored_keys·kept(보관 파일)·
    skipped([{file, reason}] — `skip`={파일: 사유} 로 건너뛰라고 받은 것 중 백업에 실제로 있던 것).
    `skip` 은 부르는 쪽이 정한다 (대기열이 도는 중이면 queue.json — 서버가 러너 잠금 안에서 판정한다)."""
    chk = restore_check(data)
    if not chk["ok"]:
        return chk
    files = chk["files"]
    skip = skip or {}
    with LOCK:
        kept = keep_before_restore(version)
        applied, ignored, skipped = [], [], []
        for rel, obj in files.items():
            if rel in skip:
                skipped.append({"file": rel, "reason": str(skip[rel])})
                continue
            if rel == "settings.json":
                raw = _load_dict("settings.json", strict=True)
                new = dict(raw)
                for k, v in obj.items():
                    if k == SCHEMA_KEY or k in RETIRED_SETTINGS:   # 없앤 기능의 키는 들이지 않는다
                        continue
                    if _restore_ignored(k):
                        ignored.append(k)
                        continue
                    new[k] = v
                new[SCHEMA_KEY] = raw[SCHEMA_KEY] if isinstance(raw.get(SCHEMA_KEY), int) else SCHEMA
                save("settings.json", new)
            else:
                save(rel, obj)
            applied.append(rel)
        pruned = prune_before_restore()
    return {"ok": True, "applied": applied, "ignored_keys": sorted(set(ignored)), "kept": kept, "skipped": skipped,
            "pruned": pruned,
            "from": {"version": str(chk["manifest"].get("version") or ""),
                     "created": str(chk["manifest"].get("created") or "")}}


# ── 문제 신고 zip ──────────────────────────────────────────────
# 사용 통계 없는 문제 신고 zip — 로그(마스킹된)·설정(비밀 제외)·버전을 한 번에 묶어 붙이기 좋게.
# zip 을 짓는 것은 server.report_zip 이고, **이 파일에 사는 자료**를 신고용으로 추리는 것만 여기 있다.
# 백업(`backup_bytes`)과 다른 점: 백업은 **옮기려고** 사람이 만든 것을 담고, 신고는 **보여 주려고** 모양·개수만 담는다.
# 비밀을 빼는 규칙은 백업보다 **넓다** — 백업에서 빼는 키(`BACKUP_SECRET_KEYS`)는 「있음/없음」 참거짓만 남기고,
# 경로 키(`BACKUP_LOCAL_PATH_KEYS`)는 파일 이름(basename)만 남긴다. 이름에 key·token·secret 이 든 글자값 키도 참거짓만.
REPORT_BOOL_ONLY = BACKUP_SECRET_KEYS          # 값 대신 `<키>_set: bool` 만 (인증키는 remote_key_set)
REPORT_BASENAME = BACKUP_LOCAL_PATH_KEYS       # 경로 → 파일 이름만
_REPORT_SECRETISH = re.compile(r"(?i)(key|token|secret|passw|device)")


def _base_name(v) -> str:
    s = str(v or "")
    return re.split(r"[\\/]", s)[-1] if s else ""


def report_settings() -> dict:
    """신고용 설정 — **비밀은 값이 없다.** 기본값이 채워진 지금 설정(`get_settings`)에서 시작한다.
      · 인증키·터널 주소·사이트 주소(`BACKUP_SECRET_KEYS`) → `<키>_set` 참거짓
      · `cli_exe` → 파일 이름만
      · 이름에 key/token/secret/passw/device 가 든 그 밖의 글자값 키도 참거짓만 (앞으로 생길 비밀까지)
      · `update_url` 은 그대로 둔다 — 업데이트 문제를 볼 때 필요하고, 비밀이 아니다."""
    out: dict = {}
    for k, v in get_settings().items():
        if k in REPORT_BOOL_ONLY:
            out[k + "_set"] = bool(v)
        elif k in REPORT_BASENAME:
            out[k] = _base_name(v)
        elif k.endswith("_set"):
            out[k] = bool(v)
        elif k != "update_url" and isinstance(v, str) and _REPORT_SECRETISH.search(k):
            out[k + "_set"] = bool(v)
        else:
            out[k] = v
    return out


def report_queue() -> dict:
    """신고용 대기열 — 카드·그룹의 `log`(실행 기록)를 뺀 항목과 상태별 개수.
    아이템 이름은 게임 아이템이라 둔다 (무엇을 돌리다 막혔는지가 신고의 핵심이다)."""
    d = load(QUEUE_FILE, None)
    if not isinstance(d, dict):
        return {"items": [], "config": {}, "counts": {}, "total": 0}
    items = _queue_items_without_log(d.get("items") if isinstance(d.get("items"), list) else [])
    counts: dict = {}
    total = 0

    def walk(xs):
        nonlocal total
        for it in xs:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("items"), list):     # 그룹 — 안의 카드를 센다
                walk(it["items"])
                continue
            total += 1
            s = str(it.get("status") or "?")
            counts[s] = counts.get(s, 0) + 1

    walk(items)
    return {"items": items, "config": d.get("config") if isinstance(d.get("config"), dict) else {},
            "counts": counts, "total": total}


def report_counts() -> dict:
    """곡·재생목록 **개수만** (곡 제목·목록 이름은 넣지 않는다)."""
    out = {"songs": 0, "playlists": 0, "playlist_items": 0, "folders": 0}
    try:
        out["songs"] = len(get_songs().get("songs") or {})
    except Exception:
        pass
    try:
        ls = get_lists()
        pls = [p for p in (ls.get("playlists") or []) if isinstance(p, dict)]
        out["playlists"] = len(pls)
        out["playlist_items"] = sum(len(p.get("items") or []) for p in pls)
        out["folders"] = len(ls.get("folders") or [])
    except Exception:
        pass
    return out
