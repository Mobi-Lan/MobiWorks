"""로컬 웹 서버 (127.0.0.1:19997). UI 는 ui/index.html, API 는 /api/*. 표준 라이브러리만 쓴다.

동기화: POST /api/sync → get_instruments, get_music_scores 를 CLI 로 받아 data/ 에 저장 (+ fixtures/ 원본).
재생:   POST /api/play {"title": DisplayTitle, "instrument": Name|null} → change_instrument → play_music_score.
정지:   POST /api/stop → get_activity.Performance.IsPlaying 확인 후 stop_action (invalid_state 는 짧게 재시도).
"""
from __future__ import annotations

import errno
import secrets
import urllib.request
import hashlib
import hmac
import base64
import io
import json
import random
import re
import socket
import os
import sys
import time
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
# 임베디드 판(서명된 pythonw 가 우리 .py 를 돌림)도 배포판이다 — FROZEN 은 PyInstaller 만의 뜻 그대로 두고
# 배포판 규칙(개발용 변수 무시·경량판)은 RELEASE 로 본다. 이 파일은 모비웍스 server 가 불러 쓰므로 저장소 뿌리가
# sys.path 에 있다 (아래 CLI 계층을 들여오는 것과 같은 조건).
import runmode  # noqa: E402
RELEASE = FROZEN or runmode.embed()
_DEV_ENV_OK = (not RELEASE) or os.environ.get("MOBIW_DEV") == "1"   # 배포판은 개발용 환경변수(MOBIW_DATA_DIR·MOBIW_CLI_EXE)를 무시
BASE = (os.environ.get("MOBIW_DATA_DIR") if _DEV_ENV_OK else None) or (os.path.join(os.environ["LOCALAPPDATA"], "MobiWorks") if os.environ.get("LOCALAPPDATA")
                                           else (os.path.dirname(sys.executable) if FROZEN else HERE))   # 데이터·로그 위치 (store.user_base 와 같은 규칙)
os.makedirs(BASE, exist_ok=True)
RES = getattr(sys, "_MEIPASS", HERE)                               # 묶인 리소스(ui/) 위치

# **모비웍스가 불러 쓰는 중인가.** 통합 뒤에는 이 파일이 서버를 띄우지 않는다 —
# 길만 내주고 창·포트·로그는 모비웍스 `server.py` 가 쥔다. 아래 로그 가로채기를 그대로
# 두면 **우리 로그가 통째로 이 파일 쪽으로 사라진다.**
HOSTED = __name__ != "__main__"

if FROZEN and not HOSTED:
    # --noconsole 이면 stdout 이 없다 → 로그를 exe 옆 파일로
    _logp = os.path.join(BASE, "mobiworks.log")
    try:
        if os.path.getsize(_logp) > 2_000_000:   # 무한 성장 방지: 2MB 넘으면 새로 시작
            os.remove(_logp)
    except OSError:
        pass
    try:
        _logf = open(_logp, "a", encoding="utf-8", buffering=1)
    except OSError:   # exe 옆에 쓸 수 없으면(읽기 전용 폴더 등) 임시 폴더로
        _logf = open(os.path.join(os.environ.get("TEMP", "."), "mobiworks.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _logf
elif sys.stdout and hasattr(sys.stdout, "reconfigure"):
    # 서버 안에서 불릴 때(HOSTED) 배포판의 stdout 은 `_RotatingLog` 다 — reconfigure 가 없어 여기서 AttributeError 로
    # 엔진 import 가 통째로 실패했고, **exe 에서는 연주 감시가 한 번도 뜨지 않았다** (exe 로그에서 발견).
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, HERE)   # `import library` 가 folio/library.py 를 집는다
import cli_transport as cli   # noqa: E402
import library as lib         # noqa: E402
import store                  # noqa: E402
import broadcast as _bc       # noqa: E402  방송 모드 방 상태 (저장소 뿌리의 broadcast.py)
# **`store.get_cache` 를 쓰면 안 된다.** 이름은 같은데 모비웍스 것은 `cache_<kind>.json` 의
# `{fetched_at, data}` 이고 여기가 기대하는 것은 `<kind>.json` 의 `{fetched_at, items}` 다.
# 그래서 합치면서 `store.folio_cache` / `store.set_folio_cache` 로 갈랐다.

# 예전 위치(일렉트론이 넘긴 앱 폴더, exe 옆, 프로젝트 폴더)의 data/ 를 사용자 폴더로 한 번 옮긴다.
#
# **불러 쓰일 때(HOSTED)는 안 돈다.** 통합 뒤에 쓰던 자료를 가져오는 길은 `store.adopt_folio()`
# 하나이고, 그건 부르는 쪽이 정한다. 여기서 또 돌면 **import 만 해도 자료가 움직인다** —
# 실제로 검사가 이 파일을 불러 보는 것만으로 사용자 폴더에 파일이 생겼다.
if not HOSTED:
    store.migrate_legacy([os.path.join(os.environ["LOCALAPPDATA"], "MabiScoreBox") if os.environ.get("LOCALAPPDATA") else None,   # 이전 이름(악보함) 시절 사용자 폴더
                          os.environ.get("MABI_LEGACY_DIR"), os.path.dirname(sys.executable) if FROZEN else None,
                          os.path.dirname(os.path.dirname(sys.executable)) if FROZEN else None,   # 패키지: resources\.. = 앱 폴더
                          HERE if not FROZEN else None])

# 경량판(LITE): 일렉트론 셸 없이 이 exe 를 바로 실행한 경우 — 스스로 빈 포트·토큰을 만들고 Edge/Chrome 앱 창을 띄운다
LITE_REUSE = os.environ.get("MABI_LITE_REUSE") == "1"   # 자동 업데이트로 다시 뜬 경우: 창은 이미 있으니 새로 띄우지 않는다
# 창을 띄울지(MABI_NO_BROWSER)와 어떤 판인지는 별개다 — 예전에는 「창 안 띄우기」가 판까지 바꿔서,
# 창 없이 돌린 시험이 배포판과 다른 길(고정 포트·토큰 없음·오버레이 없음)을 재고 있었다.
LITE = RELEASE and not os.environ.get("MABI_PARENT_PID")


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = int(os.environ.get("MABI_PLAYLIST_PORT") or (_free_port() if LITE else 19997))
TOKEN = os.environ.get("MABI_TOKEN", "") or (secrets.token_hex(24) if LITE else "")   # 셸(또는 경량판 스스로)이 실행마다 만드는 비밀 — 있으면 모든 API 가 이 값을 요구한다
PARENT = os.environ.get("MABI_PARENT_PID", "")
# **불러 쓰일 때는 이름을 비켜 둔다.** 모비웍스도 `BASE\lite.json` 에 제 포트를 적는다 —
# 같은 이름이면 한쪽이 다른 쪽 포트를 덮어쓰고, 그러면 두 번째 실행이 **엉뚱한 창**을 연다.
LITE_FILE = os.path.join(BASE, "folio-lite.json" if HOSTED else "lite.json")
UPDATE_DIR = os.path.join(BASE, "update")     # 받은 새 exe 와 교체 스크립트
MAX_UPDATE_BYTES = 200 * 1024 * 1024
VERSION = "0.3.0"
_srv = None   # ThreadingHTTPServer (종료용)


def _host_ident() -> tuple:
    """`/api/health` 가 말할 (앱 이름, 버전).

    **불러 쓰일 때(HOSTED)는 부르는 쪽 것**을 말한다 — 합친 뒤에 앱은 하나(모비웍스)이고,
    폴리오 화면 머리의 버전 칸도 이 값을 그대로 적는다. 엔진 자체 `VERSION`(0.3.0)을 말하면
    화면이 앱과 다른 버전을 보여 준다.

    부르는 쪽을 **import 하지 않는다** (서로 부르는 고리가 된다). 이미 불려 와 있는 모듈에서
    `APP`·`VERSION` 을 읽는다 — 소스로 돌리면 `server`, 묶은 exe 에서는 `__main__` 이다.
    못 찾으면 엔진 자체 값으로 물러난다 (화면에 빈 칸이 뜨는 것보다 낫다)."""
    if HOSTED:
        for name in ("server", "__main__"):
            m = sys.modules.get(name)
            app, ver = getattr(m, "APP", None), getattr(m, "VERSION", None)
            if isinstance(app, str) and app and isinstance(ver, str) and ver:
                return app, ver
    return "mobifolio", VERSION


# 로그는 한 줄이 한 사건이다. 게임이 준 글자(곡 제목·사람 이름)가 로그 문장에 들어오므로 줄바꿈·ESC(ANSI)·
# 다른 제어 문자를 눈에 보이는 글자로 바꾼다 — 가짜 로그 줄을 끼워 넣지 못하게 (보안).
_LOG_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


def _log_safe(msg) -> str:
    s = str(msg).replace("\r", "\\r").replace("\n", "\\n").replace("\t", " ")
    return _LOG_CTRL.sub("\ufffd", s)


def _say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {_log_safe(msg)}", flush=True)


# 터널 쪽 로그에서 **주소를 지운다** (server._say_tunnel 과 같은 규칙, server 를 import 하지 않으려고
# 여기 따로 둔다). tunnel.py 는 「열렸습니다 — https://…」처럼 주소를 그대로 말한다. 터널 주소는 문 위치라
# 백업에서도 빼는 비밀이고, 로그는 문의 글에 통째로 붙여지기 쉽다. 말은 남기고 주소만 뺀다.
_URL_IN_LOG = re.compile(r"(?i)(?:https?|wss?)://[^\s\"'<>)]+|[\w.-]+\.trycloudflare\.com")


def _say_tunnel(msg: str) -> None:
    _say(_URL_IN_LOG.sub("(주소 생략)", str(msg)))


def _shutdown() -> None:
    if _TUNNEL is not None:          # 앱이 꺼지면 터널도 닫는다 (= 주소가 사라진다)
        try:
            _TUNNEL.stop()
        except Exception:
            pass
    time.sleep(0.2)
    threading.Timer(3.0, lambda: os._exit(0)).start()   # 어떤 이유로든 3초 안에 못 끝나면 강제 종료 (멈춘 프로세스를 남기지 않게)
    if LITE:
        try:
            with open(LITE_FILE, encoding="utf-8") as f:
                mine = int(json.load(f).get("pid", 0)) == os.getpid()
            if mine:
                os.remove(LITE_FILE)   # 내 기록일 때만 지운다 (업데이트로 뜬 새 인스턴스의 기록은 남겨야 한다)
        except (OSError, ValueError):
            pass
    try:
        _say("shutdown: stopping server")
        try:
            import faulthandler
            faulthandler.dump_traceback_later(2.0, exit=False, file=sys.stderr)   # 2초 넘게 걸리면 어디서 막혔는지 로그에 남긴다
        except Exception:
            pass
        if _srv:
            _srv.shutdown()
        _say("shutdown: done")
    finally:
        os._exit(0)


def _watch_parent() -> None:
    """MABI_PARENT_PID(일렉트론)가 죽으면 같이 끝난다 — 셸이 강제 종료돼도 고아 백엔드가 남지 않게."""
    pid = os.environ.get("MABI_PARENT_PID")
    if not pid or not pid.isdigit():
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if not h:
            return
        def wait():
            k32.WaitForSingleObject(h, 0xFFFFFFFF)
            os._exit(0)
        threading.Thread(target=wait, daemon=True).start()
    except Exception:
        pass
UI_DIR = os.path.join(RES, "ui")
if _DEV_ENV_OK and os.environ.get("MABI_UI_DIR"):   # 개발용: 다른 화면 꾸러미(미니판 mini/ui 등)를 같은 백엔드로 띄운다
    UI_DIR = os.path.abspath(os.environ["MABI_UI_DIR"])


def _load_ui_meta() -> dict:
    """ui/app.json (선택). {"name": "Mini", "width": 440, "height": 920, "update": false}
    화면 꾸러미가 앱 창 크기, 단일 실행·창 프로필 이름(name), 자동 업데이트 사용 여부를 정한다. 없으면 기본(정식 화면)."""
    try:
        with open(os.path.join(UI_DIR, "app.json"), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


UI_META = _load_ui_meta()
UI_NAME = re.sub(r"[^A-Za-z0-9]", "", str(UI_META.get("name") or ""))[:16]   # "" = 기본 화면
if UI_NAME:
    LITE_FILE = os.path.join(BASE, f"lite-{UI_NAME}.json")   # 화면 꾸러미마다 따로 (미니판과 경량판을 같이 띄울 수 있게)
_APP_PROFILE = ".appwindow-profile" + (f"-{UI_NAME}" if UI_NAME else "")


def _screen_h_dip() -> int:
    """기본 모니터의 세로 크기 (브라우저가 쓰는 논리 픽셀). 못 재면 0."""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        try:                                   # 화면 배율을 알아야 실제 픽셀 → 논리 픽셀로 바꾼다
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        px = int(u.GetSystemMetrics(1))        # SM_CYSCREEN
        dpi = 96
        try:
            mon = u.MonitorFromPoint(wt.POINT(0, 0), 1)   # MONITOR_DEFAULTTOPRIMARY
            dx, dy = ctypes.c_uint(), ctypes.c_uint()
            if mon and ctypes.windll.shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                dpi = int(dy.value) or 96
        except Exception:
            pass
        return max(300, int(px * 96 / dpi))
    except Exception:
        return 0


def _window_size() -> str:
    """창 크기. 높이는 **화면 높이의 2/3** 로 맞춘다 (미니 창이 너무 커지지 않게).
    app.json 의 height 는 화면을 못 잴 때 쓰는 값이다."""
    try:
        w = int(UI_META.get("width") or 1280); h = int(UI_META.get("height") or 860)
    except (TypeError, ValueError):
        w, h = 1280, 860
    sh = _screen_h_dip()
    if sh:
        h = int(sh * 2 / 3)
    return f"{min(max(w, 300), 4000)},{min(max(h, 300), 4000)}"
_cli_lock = threading.Lock()   # CLI 는 한 번에 하나만 (게임 파이프 직렬)
_log: list[dict] = store.get_log()   # 최근 CLI 응답 요약 (UI 「CLI 응답」) — data/cli_log.json 에 남겨 재시작 후에도 보인다
_last_play: dict = {"title": "", "inst": ""}   # 길이 캐시 키(DisplayTitle)용

cli.set_exe_override(str(store.get_settings().get("cli_exe") or ""))
_log_lock = threading.Lock()
_ok_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"}

# ── 밖에서 접속 (터널) ────────────────────────────────────────────────────────────────
# 이 앱은 원래 **이 PC 안에서만** 쓰는 것이라, 페이지가 열리면 실행 토큰이 HTML 에 박혀 나간다.
# 밖으로 열 때 그대로 두면 **주소만 알면 누구나** 캐릭터를 조종한다(연주·정지·악기·채팅).
# 그래서 밖에서 온 요청은 토큰 대신 **비밀번호로 받은 쪽지(세션)** 를 요구하고,
# 기본은 「재생 조작만」으로 묶는다. 주소도 사용자가 적어 둔 것 하나만 받는다.
_REMOTE = {}             # 쪽지 → {"until": 만료 시각, "dev": 기기 id(없으면 인증키로 들어온 것)}
_REMOTE_TRY = {}         # 보낸 곳 → (틀린 횟수, 잠금 해제 시각)
REMOTE_TTL = 12 * 3600   # 한 번 들어오면 12시간
REMOTE_MAX_TRY = 5       # 이만큼 틀리면
REMOTE_LOCK = 300.0      # 5분 잠근다
# 「재생 조작만」일 때 밖에서 보낼 수 있는 것 — 여기 없는 POST 는 막는다.
REMOTE_PLAY_OK = {"/api/play", "/api/stop", "/api/queue", "/api/queue/step", "/api/queue/at",
                  "/api/queue/clear", "/api/queue/ask", "/api/queue/inst",
                  "/api/queue/next", "/api/queue/append"}   # `/api/sync` 는 뺐다 — 폰은 PC 가 읽은 값만
# 범위가 「전부」여도 밖에서는 **절대** 안 되는 것들.
#   * 이 PC 의 창·프로세스를 건드린다 (오버레이·연출 창·종료·업데이트 설치)
#   * 밖으로 나가는 문 자체를 여닫는다 (터널·인증키·우편함·잠금 풀기) — 밖에서 열게 두면
#     문을 잠근 의미가 없다
REMOTE_NEVER = {"/api/quit", "/api/bye", "/api/hold", "/api/cli_test",
                "/api/overlay", "/api/opening", "/api/update/apply",
                "/api/covers",          # 커버 목록(이 PC 의 폴더 경로)·폴더 열기·지우기 (「내 커버 관리」)
                "/api/covers/open",     # 이 PC 의 탐색기 창을 연다
                "/api/covers/upload",   # 이 PC 의 커버 폴더에 파일을 쓴다
                "/api/covers/search",   # 이 PC 가 바깥(iTunes·Deezer)에 묻는다 — 사람이 이 PC 에서 누를 때만
                "/api/covers/fetch",    # 이 PC 의 커버 폴더에 바깥 그림을 받아 쓴다 (개인 감상용)
                "/api/songs",           # 곡 번호 DB (읽기도 이 PC 안에서만)
                "/api/song",            # 곡 상세 시트 — 번호 DB·커버·인사를 한데 모은 읽기 (위와 같은 까닭)
                "/api/greet/assign",    # 곡 상세 시트 — 곡별 시작 인사 붙이기 (밖에서는 읽기 전용)
                "/api/remote/tunnel", "/api/remote/link", "/api/remote/key",
                "/api/remote/unlock", "/api/remote/devices"}
# 앞자리로 걸러야 하는 것 (집합으로는 못 잡는다).
REMOTE_NEVER_PREFIX = ("/api/cli/",)
# **모비웍스가 불러 쓸 때** 그쪽이 채워 넣는다 (server.py `folio_engine` ③): 밖에서 `/api/settings` 로
# 보고 바꿀 수 있는 키의 목록. None 이면 단독판 규칙(아래 `overlay`·`remote`·`ov_` 앞자리 거르기) 그대로다.
REMOTE_SETTINGS_OK = None


def _hosted_pass(handler) -> bool:
    """모비웍스 문이 **폴리오 길 이름으로 이미 판정한** 요청인가 (server.py `_folio` 의 `_folio_pass`).

    그러면 밖에서 무엇이 되고 안 되는지는 그쪽 허용 목록(`FOLIO_READ_OK`·`FOLIO_PLAY_OK`·`FOLIO_EDIT_OK`,
    거절이 기본)이 정한다. 여기 목록으로 한 번 더 재면 **두 문이 서로 다른 답**을 내고, 폰에서 되어야 할
    것(설정·곡 상세·재생목록 편집)이 이쪽 옛 목록에 걸려 403 이 된다."""
    return bool(getattr(handler, "_folio_pass", False))
_TUNNEL = None           # 터널 하나 (밖에서 들어올 길) — 처음 켤 때 만든다


def _tunnel():
    global _TUNNEL
    if _TUNNEL is None:
        import tunnel
        _TUNNEL = tunnel.Tunnel(store.DATA_DIR, log=_say_tunnel)
    return _TUNNEL


def _mailbox_put(url: str) -> dict:
    """우편함에 주소를 넣고 **코드**를 받아 온다.

    터널 주소는 켤 때마다 바뀌고 길다. 사람이 폰에 옮겨 적게 두면 반드시 틀리므로,
    짧은 코드만 옮기게 하고 주소는 우편함이 건네준다. 우편함은 「폰 앱 사이트 주소」와
    같은 곳이다(한 주소에 화면과 우편함이 같이 올라가 있다).
    """
    base = _remote_cfg()["origin"]
    if not base:
        return {"ok": False, "error": "no_site",
                "message": "「모바일 앱 사이트 주소」를 먼저 적어 주세요."}
    req = urllib.request.Request(
        base.rstrip("/") + "/new",
        data=json.dumps({"url": url}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "MobiFolio"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": False, "error": "mailbox", "message": f"우편함에 넣지 못했습니다: {e}"}
    if not out.get("code"):
        return {"ok": False, "error": "mailbox", "message": "우편함이 코드를 주지 않았습니다."}
    # 코드는 적지 않는다 — 3분 동안 이 코드만 있으면 누구나 터널 주소를 받아 간다. 화면에만 보인다.
    _say(f"[link] 우편함에 넣었습니다 ({int(out.get('ttl') or 180)}초)")
    return {"ok": True, "code": out["code"], "ttl": int(out.get("ttl") or 180)}


def _tunnel_got_url(url: str) -> None:
    """터널이 열리면 그 주소를 **허용할 주소로 자동 반영**한다.

    Quick Tunnel 은 켤 때마다 주소가 바뀐다 — 사람이 매번 옮겨 적게 두면 반드시 어긋난다.
    """
    host = url.split("://", 1)[-1].strip("/")
    store.set_settings({"remote_host": host})
    _say("[tunnel] 허용할 주소를 새 터널 주소로 맞췄습니다")   # 주소는 적지 않는다 (문 위치)


# 만남 코드 — 짝지을 때만 쓰는 **짧고 곧 사라지는** 값. 인증키(오래 쓰는 것)와 갈라 둔다:
# 같은 값을 둘 다로 쓰면 코드를 맞힌 사람이 주소도 알아내고 인증까지 통과한다.
_PAIR = {}               # 코드 → {"until", "used", "dev"}
PAIR_TTL = 180.0         # 3분
PAIR_LEN = 6


def _remote_cfg() -> dict:
    d = store.get_settings()
    host = str(d.get("remote_host") or "").strip().lower()
    return {"on": bool(d.get("remote_on")) and bool(host) and store.has_remote_key(),
            "host": host, "scope": str(d.get("remote_scope") or "play"),
            "origin": str(d.get("remote_origin") or "").strip().rstrip("/").lower()}


def _cors_origin(handler) -> str:
    """이 요청에 열어 줄 출처. 안 열어 줄 것이면 빈 문자열.

    폰 앱을 올려 둔 사이트에서 터널로 부르는 것은 **다른 출처**라, 브라우저가 먼저 물어보고
    (OPTIONS) 답에 허락이 없으면 그냥 버린다. 사용자가 적어 둔 사이트 하나에만 열어 준다.
    """
    org = (handler.headers.get("Origin") or "").strip().rstrip("/").lower()
    if not org:
        return ""
    cfg = _remote_cfg()
    if cfg["on"] and cfg["origin"] and org == cfg["origin"]:
        return org
    return ""


def _cors_headers(handler) -> None:
    """허락한 사이트에만 붙인다 (그 외에는 아무것도 안 붙어 브라우저가 답을 버린다)."""
    org = _cors_origin(handler)
    if not org:
        return
    handler.send_header("Access-Control-Allow-Origin", org)
    handler.send_header("Access-Control-Allow-Credentials", "true")
    handler.send_header("Vary", "Origin")


def _host_ok_remote(host: str) -> bool:
    """Host 헤더가 사용자가 적어 둔 주소인가 (포트는 떼고 본다)."""
    cfg = _remote_cfg()
    if not cfg["on"]:
        return False
    h = (host or "").lower().split("/")[0]
    return h == cfg["host"] or h.split(":")[0] == cfg["host"].split(":")[0]


def _pair_new() -> dict:
    """만남 코드를 하나 만든다 (3분 · 한 번 쓰면 끝)."""
    now = time.time()
    for k, v in list(_PAIR.items()):
        if v["until"] < now:
            _PAIR.pop(k, None)
    code = "".join(secrets.choice(store._KEY_ALPHABET) for _ in range(PAIR_LEN))
    _PAIR[code] = {"until": now + PAIR_TTL, "used": False, "dev": None}
    return {"code": code, "ttl": int(PAIR_TTL)}


def _pair_take(code: str):
    """코드를 쓴다. 맞고 아직 안 썼으면 그 자리를 돌려주고 바로 닫는다."""
    c = (code or "").strip().upper().replace("-", "").replace(" ", "")
    v = _PAIR.get(c)
    if not v or v["used"] or v["until"] < time.time():
        return None
    v["used"] = True
    return v


def _remote_new(dev_id: str = "") -> str:
    tok = secrets.token_urlsafe(24)
    now = time.time()
    for k, v in list(_REMOTE.items()):        # 만료된 쪽지는 버린다
        if v["until"] < now:
            _REMOTE.pop(k, None)
    _REMOTE[tok] = {"until": now + REMOTE_TTL, "dev": dev_id or ""}
    return tok


def _remote_valid(tok: str) -> bool:
    if not tok:
        return False
    v = _REMOTE.get(tok)
    if not v:
        return False
    if v["until"] < time.time():
        _REMOTE.pop(tok, None)
        return False
    return True


def _remote_drop_dev(dev_id: str) -> None:
    """그 기기 것만 끊는다 — 한 대를 끊는다고 다른 기기까지 쫓아내면 안 된다 (시험이 잡았다)."""
    for k, v in list(_REMOTE.items()):
        if v.get("dev") == dev_id:
            _REMOTE.pop(k, None)


def _cookie(handler, name: str) -> str:
    raw = handler.headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _remote_tok(handler) -> str:
    """이 요청이 들고 온 쪽지. **헤더가 먼저**다.

    폰에서 홈 화면 앱(PWA)으로 쓸 때는 사이트 주소와 터널 주소가 달라서, 사파리가 쿠키를
    통째로 막는다(ITP, 3자 쿠키 차단). 그래서 쪽지는 헤더로 들고 다니고 기기에 저장한다.
    같은 주소로 브라우저를 직접 연 경우에는 쿠키도 그대로 쓴다.
    """
    return (handler.headers.get("X-MobiFolio-Remote") or "").strip() or _cookie(handler, "mf_remote")
MAX_BODY = 1_000_000
_PFX = re.compile(r"^악보\s*[:：]\s*")


def _note(r, summary: str = "") -> None:
    """CLI 결과를 로그에 남긴다 (최근 60건, UI 는 40건). 여러 스레드가 동시에 불러도 안전하게."""
    row = {"ts": time.time(), "command": r.command, "ok": r.ok, "error": r.error, "message": r.message,
           "elapsed": round(r.elapsed, 3), "summary": summary,
           "raw": (json.dumps(r.body, ensure_ascii=False)[:1500] if r.body is not None else r.raw[:1500])}
    with _log_lock:
        _log.insert(0, row)
        del _log[60:]
        snap = list(_log)
    try:
        store.set_log(snap)
    except Exception as e:
        print(f"[log] 저장 실패: {e}", flush=True)


_build_memo: dict = {"key": None, "items": None}


def _songs_stamp():
    """곡 번호 DB 파일의 모습 (mtime·크기) — 복원이 파일을 바꾸면 메모를 버리게 (번호가 옛 값으로 남지 않게)."""
    try:
        st = os.stat(os.path.join(store.DATA_DIR, store.SONGS_FILE))
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _build_items() -> list:
    """lib.build 는 193곡 기준 수 ms 지만 요청마다 다시 도는 것을 막는다 (같은 캐시·같은 아티스트 상태면 재사용).

    **곡 번호(`id`)는 여기서 붙는다** — 보관함을 새로 읽을 때마다 `store.songs_sync` 가 처음 보는 곡에
    다음 번호를 주고, 모든 아이템이 `id` 를 들고 나간다. 화면 셋·오버레이·대기열이 전부
    이 함수에서 목록을 받으므로 번호를 붙이는 곳은 여기 한 곳이다."""
    sc = store.folio_cache("scores")
    art = store.get_artists()
    key = (sc["fetched_at"], len(sc["items"]), json.dumps(art, sort_keys=True, ensure_ascii=False))
    with store.LOCK:
        if (_build_memo["key"] == key and _build_memo["items"] is not None
                and _build_memo.get("songs") == _songs_stamp()):
            return _build_memo["items"]
        items = lib.build(sc["items"], art)
        try:
            ids = store.songs_sync(items)
        except Exception as e:            # 번호 DB 를 못 써도 보관함은 떠야 한다 (번호만 빠진다)
            _say(f"[songs] 곡 번호를 맞추지 못했습니다: {type(e).__name__}: {e}")
            ids = {}
        for it in items:
            it["id"] = ids.get(it["key"])
        _build_memo["key"], _build_memo["items"], _build_memo["songs"] = key, items, _songs_stamp()
        return items


def song_total() -> int:
    """지금까지 번호를 받은 곡 수 (사라진 곡 포함) — 연출 카드의 `#0056 / 184` 뒤쪽."""
    return store.get_songs()["next"] - 1


def _s(v, default: str = "") -> str:
    """문자열 강제 (None/숫자/딕셔너리가 와도 .strip() 에서 죽지 않게)."""
    return v.strip() if isinstance(v, str) else (str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default)


def _titles(v) -> list[str]:
    """titles 는 문자열 배열만 (문자열 하나를 글자 단위로 돌지 않게)."""
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    return []


def _json(handler, obj, status=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    _cors_headers(handler)          # 폰 앱 사이트에서 부를 수 있게 (허용한 곳에만 붙는다)
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler) -> dict:
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {"_error": "bad_length"}
    if n <= 0:
        return {}
    if n > MAX_BODY:
        left = min(n, 8 * MAX_BODY)   # 상한까지만 비우고 답한다 (그 이상은 연결을 닫는다)
        while left > 0:
            chunk = handler.rfile.read(min(65536, left))
            if not chunk:
                break
            left -= len(chunk)
        handler.close_connection = True
        return {"_error": "too_large"}
    raw = handler.rfile.read(n)
    for enc in ("utf-8", "mbcs"):   # 브라우저는 utf-8. 콘솔 도구가 cp949 로 보내도 조용히 빈 값이 되지 않게
        try:
            v = json.loads(raw.decode(enc))
            return v if isinstance(v, dict) else {"_error": "not_object"}
        except Exception:
            continue
    return {"_error": "decode_error"}


def _cli(command, body=None, timeout=660.0):
    with _cli_lock:
        return cli.call(command, body, timeout)


def _probe() -> dict:
    """cli.probe() 를 CLI 잠금 안에서 (한 번에 하나 규칙)."""
    with _cli_lock:
        return cli.probe()


# ── 동기화 ──
def sync() -> dict:
    out = {"ok": True, "steps": []}
    st = _cli("status", timeout=20)
    out["steps"].append(st.to_dict())
    _note(st, "연결 확인")
    if not st.ok:
        out["ok"] = False
        return out
    for command, kind, label in (("get_instruments", "instruments", "악기"), ("get_music_scores", "scores", "악보")):
        r = _cli(command, "", timeout=120)   # 빈 필터 = 전체
        n = len(r.body) if isinstance(r.body, list) else None
        out["steps"].append({**r.to_dict(), "body": None, "count": n})
        _note(r, f"{label} {n}건 수신" if r.ok else f"{label} 수신 실패")
        if not r.ok:
            out["ok"] = False
            continue
        items = r.body if isinstance(r.body, list) else (r.body.get("items") if isinstance(r.body, dict) else None)
        if not isinstance(items, list):
            out["ok"] = False
            out["steps"][-1]["error"] = "bad_shape"
            _note(r, f"{label} 응답 모양 이상 — 캐시 유지")
            continue
        if not items and store.folio_cache(kind)["items"]:
            out["steps"][-1]["error"] = "empty_result"
            _note(r, f"{label} 0건 — 기존 캐시 유지")
            continue
        store.set_folio_cache(kind, items)
        if not RELEASE:   # 원본 응답 사본(fixtures/)은 개발 실행에서만 — 배포판에 중복 사본을 남기지 않는다
            store.save_fixture(command, r.body)
    return out


# ── 재생목록 ──
def _lists() -> dict:
    """store.get_lists() + 항목 key 보정 (예전 '제목' 키, 없어진 '제목#n' → 같은 제목의 첫 악보)."""
    d = store.get_lists()
    items = _build_items()
    for pl in d["playlists"]:
        seen: set[str] = set(); kept = []
        for it in pl["items"]:
            it["key"] = lib.resolve_key(it["key"], it["title"], items)
            if it["key"] not in seen:   # 이어붙인 결과 같은 악보가 둘이면 첫 항목만 (reorder 가 key 로 합치며 항목이 사라지지 않게)
                seen.add(it["key"]); kept.append(it)
        pl["items"] = kept
    return d


def lists_op(op: str, p: dict) -> dict:
    with store.LOCK:   # 읽고-고쳐-쓰기 전체를 잠가 동시 요청이 서로의 변경을 덮어쓰지 않게
        return _lists_op(op, p)


def _lists_op(op: str, p: dict) -> dict:
    d = _lists()
    cur_items = _build_items()
    key_title = lib.key_map(cur_items)   # key → 제목 (담을 때 제목을 같이 저장해 두면 보관함이 바뀌어도 이름은 남는다)
    pls, fds = d["playlists"], d["folders"]
    now = time.time()
    fids = {f["id"] for f in fds}
    name = _s(p.get("name"))[:200]
    folder = p.get("folder") if p.get("folder") in fids else None
    if op == "folder_create":
        parent = p.get("parent") if p.get("parent") in fids else None
        f = {"id": store.new_id(), "name": name or "새 폴더", "parent": parent, "created": now}
        fds.append(f)
    elif op == "folder_rename":
        for f in fds:
            if f["id"] == p.get("id") and name:
                f["name"] = name
    elif op == "folder_delete":
        fid = p.get("id")
        # 하위 폴더는 상위로, 재생목록은 폴더 없음으로
        parent = next((f.get("parent") for f in fds if f["id"] == fid), None)
        d["folders"] = [f for f in fds if f["id"] != fid]
        for f in d["folders"]:
            if f.get("parent") == fid:
                f["parent"] = parent
        for pl in pls:
            if pl.get("folder") == fid:
                pl["folder"] = parent
    elif op == "create":
        pl = {"id": store.new_id(), "name": name or "새 재생목록", "folder": folder,
              "items": [], "created": now, "updated": now}
        pls.append(pl)
    elif op == "rename":
        for pl in pls:
            if pl["id"] == p.get("id") and name:
                pl["name"] = name; pl["updated"] = now
    elif op == "move":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["folder"] = folder; pl["updated"] = now
    elif op == "delete":
        d["playlists"] = [pl for pl in pls if pl["id"] != p.get("id")]
    elif op == "add":
        for pl in pls:
            if pl["id"] == p.get("id"):
                have = {it["key"] for it in pl["items"]}
                for k in _titles(p.get("keys") or p.get("titles")):   # keys = 내부 식별자 (예전 UI 의 titles 도 받는다)
                    k = lib.resolve_key(k, key_title.get(k) or lib.key_title_guess(k), cur_items)   # 제목만 온 동명 악보·없는 채번은 그 제목의 첫 장으로
                    if k not in have:
                        pl["items"].append({"key": k, "title": key_title.get(k) or lib.key_title_guess(k), "inst": ""}); have.add(k)
                pl["updated"] = now
    elif op == "remove":
        rm = set(_titles(p.get("keys") or p.get("titles")))
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["items"] = [it for it in pl["items"] if it["key"] not in rm]; pl["updated"] = now
    elif op == "reorder":   # items = key 순서 배열
        for pl in pls:
            if pl["id"] == p.get("id"):
                by = {it["key"]: it for it in pl["items"]}
                order = [by[k] for k in _titles(p.get("items")) if k in by]
                seen = {it["key"] for it in order}
                pl["items"] = order + [it for it in pl["items"] if it["key"] not in seen]; pl["updated"] = now
    elif op == "set_inst":   # 곡별 악기. key(예전 UI 는 title) 없으면 목록 전체
        one = next((v for v in (p.get("key"), p.get("title")) if isinstance(v, str) and v.strip()), "")   # 원문 그대로 비교 (끝 공백 제목도 있다)
        for pl in pls:
            if pl["id"] == p.get("id"):
                for it in pl["items"]:
                    if not one or it["key"] == one or (not p.get("key") and it["title"] == one):
                        it["inst"] = _s(p.get("inst"))
                pl["updated"] = now
    elif op == "memo":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["memo"] = _s(p.get("memo"))[:4000]; pl["updated"] = now
    else:
        return {"ok": False, "error": "unknown_op"}
    store.set_lists(d)
    return {"ok": True, **d}


# ── 아티스트 사전·수동 지정 ──
def artists_op(op: str, p: dict) -> dict:
    with store.LOCK:
        return _artists_op(op, p)


def _artists_op(op: str, p: dict) -> dict:
    d = store.get_artists()
    arts = d["artists"]
    by_id = {a["id"]: a for a in arts}

    def ensure(name: str) -> str:
        """이름으로 아티스트를 찾거나 만든다 (별칭 포함, 대소문자·기호 무시). id 반환."""
        n = lib.norm(name)
        for a in arts:
            if lib.norm(a["name"]) == n or any(lib.norm(x) == n for x in a.get("aliases", [])):
                return a["id"]
        a = {"id": store.new_id(), "name": name.strip(), "aliases": []}
        arts.append(a); by_id[a["id"]] = a
        return a["id"]

    name = _s(p.get("name"))[:200]
    if op == "create":
        ensure(name or "이름 없음")
    elif op == "rename":
        a = by_id.get(p.get("id"))
        if not a:
            return {"ok": False, "error": "not_found"}
        if name:
            a["name"] = name
    elif op == "alias":
        a = by_id.get(p.get("id"))
        al = _s(p.get("alias"))[:200]
        if not a:
            return {"ok": False, "error": "not_found"}
        if al and al not in a["aliases"] and lib.norm(al) != lib.norm(a["name"]):
            a["aliases"].append(al)
    elif op == "merge":   # from → into : from 의 이름은 into 의 별칭으로, 지정도 옮긴다
        src, dst = by_id.get(p.get("from")), by_id.get(p.get("into"))
        if not src or not dst:
            return {"ok": False, "error": "not_found"}
        if src is not dst:
            dst["aliases"] = list(dict.fromkeys(dst["aliases"] + [src["name"]] + src.get("aliases", [])))
            for t, aid in list(d["assign"].items()):
                if aid == src["id"]:
                    d["assign"][t] = dst["id"]
            d["artists"] = [a for a in arts if a["id"] != src["id"]]
    elif op == "assign":   # titles[] → id 또는 name(없으면 생성). 자동 추출 키('auto:…')를 넘기면 그 이름으로 생성
        aid = p.get("id")
        if not aid or aid not in by_id:
            if not name:
                return {"ok": False, "error": "empty_name", "message": "아티스트 이름이 비어 있습니다."}
            aid = ensure(name)
        for t in _titles(p.get("titles")):
            d["assign"][t] = aid
    elif op == "unassign":
        for t in _titles(p.get("titles")):
            d["assign"].pop(t, None)
    elif op == "delete":
        aid = p.get("id")
        if aid not in by_id:
            return {"ok": False, "error": "not_found"}
        d["artists"] = [a for a in arts if a["id"] != aid]
        d["assign"] = {t: v for t, v in d["assign"].items() if v != aid}
    elif op == "noise":   # 잡음어 추가/제거
        w = _s(p.get("word")).lower()[:50]
        if w:
            if p.get("remove"):
                d["noise"] = [x for x in d["noise"] if x != w]
            elif w not in d["noise"]:
                d["noise"].append(w)
    else:
        return {"ok": False, "error": "unknown_op"}
    store.set_artists(d)
    return {"ok": True, **d}


# ── 재생·정지 ──
def _ensemble() -> dict:
    """주변 플레이어의 연주 상태만 간추린다 (합주 인식용).

    이름은 CLI 가 주는 두 값을 그대로 넘긴다 — RealmName 과 Title. 어느 쪽이 닉네임인지는 실기로 확인해야 한다
    (다른 도구 제작자는 "닉네임이 아니라 칭호가 표시된다"고 적었다). 화면은 name 을 먼저 쓰고 없으면 title 을 쓴다."""
    r = _cli("get_near_pcs", timeout=30)
    out = {"ok": r.ok, "error": r.error, "message": r.message, "players": []}
    rows = r.body if isinstance(r.body, list) else []
    for x in rows:
        if not isinstance(x, dict):
            continue
        pf = x.get("Performance") if isinstance(x.get("Performance"), dict) else {}
        out["players"].append({
            "name": str(x.get("RealmName") or ""), "title2": str(x.get("Title") or ""), "distance": x.get("Distance"),
            "playing": bool(pf.get("IsPlaying")), "title": str(pf.get("MusicTitle") or ""),
            "channels": pf.get("ChannelCount") or 0, "total": pf.get("TotalDurationSeconds") or 0,
            "elapsed": pf.get("ElapsedSeconds") or 0, "remaining": pf.get("RemainingSeconds"), "loop": bool(pf.get("IsLoop")),
        })
    return out


def _game_rect(native: bool = False) -> dict:
    """게임 창(마비노기 모바일)의 화면상 위치·크기를 실제 픽셀로 돌려준다 — 오프닝 연출을 게임 화면에 딱 맞추려고 쓴다.

    유니티 창(UnityWndClass) 중 프로세스 이름이 MabinogiMobile.exe 인 것을 찾는다.
    테두리·제목 표시줄을 뺀 안쪽(클라이언트) 영역만 준다. 게임이 꺼져 있으면 found=False.
    기본은 실제 픽셀 — DPI 를 이 호출 동안만 창별 인식으로 바꿨다가 되돌린다(정식판은 이 값을 논리 좌표로 환산해 쓴다).
    native=True 면 DPI 를 건드리지 않아 이 프로세스가 보는 좌표 그대로 준다 (tkinter 가 쓰는 공간)."""
    if os.name != "nt":
        return {"ok": True, "found": False, "reason": "not_windows"}
    try:
        import ctypes
        import ctypes.wintypes as wt
        # **공용 windll(user32) 객체를 쓰지 않는다.** 그 객체의 함수는 프로세스 안에서 하나라, 오버레이
        # (opening_tk._u32)가 ClientToScreen 등에 제 POINT 형으로 argtypes 를 박아 두면 여기서 wintypes.POINT 를
        # 넘기는 순간 `ArgumentError: expected LP__PT instance` 로 콜백이 죽고 → 게임 창을 못 찾아 연출이
        # **화면 전체**로 떨어졌다 (오버레이가 같은 프로세스에서 먼저 뜨기
        # 시작하면서 생긴 충돌). 새 WinDLL 인스턴스는 함수 객체가 따로라 남의 argtypes 를 안 탄다.
        u, k32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
        prev = None
        if not native:
            try:
                u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
                prev = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
            except Exception:
                prev = None
        try:
            u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
            u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
            found = {}

            k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
            k32.OpenProcess.restype = wt.HANDLE          # 안 박으면 64비트에서 핸들이 c_int 로 잘린다
            k32.CloseHandle.argtypes = [wt.HANDLE]

            def _exe_name(hwnd) -> str:
                pid = wt.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                h = k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
                if not h:
                    return ""
                try:
                    buf = ctypes.create_unicode_buffer(600)
                    n = wt.DWORD(600)
                    if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                        return ""
                    return os.path.basename(buf.value)
                finally:
                    k32.CloseHandle(h)

            @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            def _cb(hwnd, _l):
                if found or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                    return True
                cls = ctypes.create_unicode_buffer(128)
                u.GetClassNameW(hwnd, cls, 128)
                if cls.value != "UnityWndClass":
                    return True
                title = ctypes.create_unicode_buffer(300)
                u.GetWindowTextW(hwnd, title, 300)
                if _exe_name(hwnd).lower() != "mabinogimobile.exe" and "마비노기" not in title.value:
                    return True
                cr, pt = wt.RECT(), wt.POINT(0, 0)
                if not u.GetClientRect(hwnd, ctypes.byref(cr)) or not u.ClientToScreen(hwnd, ctypes.byref(pt)):
                    return True
                if cr.right < 200 or cr.bottom < 150:   # 최소화 직후 등 쓸 수 없는 크기
                    return True
                found.update({"hwnd": int(hwnd), "x": pt.x, "y": pt.y, "width": cr.right, "height": cr.bottom,
                              "title": title.value, "fg": int(u.GetForegroundWindow()) == int(hwnd)})
                return False

            u.EnumWindows(_cb, 0)
        finally:
            if prev is not None:
                try:
                    u.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
                except Exception:
                    pass
        if not found:
            return {"ok": True, "found": False, "reason": "game_window_not_found"}
        found.update({"ok": True, "found": True})
        return found
    except Exception as e:
        return {"ok": True, "found": False, "reason": str(e)}


def _song_meta(title: str) -> dict:
    """게임이 준 곡 제목을 악보함과 맞춰 곡명·아티스트를 찾는다 (오버레이 밴드가 쓴다).
    못 찾으면 known=False — 화면의 「미확인 곡」 상태가 된다."""
    t = _PFX.sub("", str(title or "")).strip()
    if not t:
        return {"song": "", "artist": "", "known": False}
    want = lib.norm(t)
    for it in _build_items():
        if lib.norm(it.get("title") or "") == want or lib.norm(it.get("cleaned") or "") == want or lib.norm(it.get("song") or "") == want:
            return {"song": it.get("song") or it.get("cleaned") or t, "artist": it.get("artist") or "", "known": True}
    # 악보함에 없는 곡 — 남이 만든 파일 이름이라 접두·악기 약칭이 붙어 있다.
    # 내 악보함에 쓰는 것과 같은 규칙으로 다듬어서 읽을 수 있게 만든다 (상태는 「미확인 곡」 그대로).
    try:
        cleaned, _removed, _tags, _variant = lib.clean_title(t)
        artist, song, _rule = lib.split_artist(cleaned)
    except Exception:
        artist, song = "", t
    return {"song": song or cleaned or t, "artist": artist or "", "known": False}


_ov = None   # 경량판·미니판의 tkinter 오버레이 (정식판은 일렉트론 창을 쓴다)


def _ov_can() -> bool:
    """이 판에서 오버레이 창을 **만들 수 있나** — 아직 안 만들었어도. 화면의 「오버레이 켜기」 항목은 이 값을 본다.
    창은 설정 `overlay_folio` 가 켜져 있을 때만 시작 시 만들어지므로(server.folio_overlay_sync), 꺼 둔 채로는
    `_ov` 가 None 이라 available=false → ☰ 메뉴에 오버레이 항목이 아예 안 나왔다."""
    return LITE or HOSTED or (_DEV_ENV_OK and os.environ.get("MOBIW_OVERLAY") == "1")


def _ov_start() -> None:
    """경량판에서만: 창을 만들어 두고(첫 실행은 숨김) F9·설정으로 띄운다."""
    global _ov
    # **불러 쓰일 때(HOSTED)는 부르는 쪽이 정한다.** 이 함수는 원래 `if __name__` 뒤에서만
    # 불렸고(2970줄), 그래서 통합 뒤에는 **영영 안 떴다.** 모비웍스가 설정을 보고 부른다.
    if _ov is not None or not _ov_can():
        return
    try:
        import overlay_tk
        _ov = overlay_tk.start(BASE, _activity, _ensemble, store.get_settings, _say,
                               game_rect=lambda: _game_rect(native=True), meta=_song_meta, ops=_ov_ops(),
                               app_gone=_app_gone)
    except Exception as e:
        _say(f"[overlay] not available: {e}")


def _ov_ops() -> dict:
    """오버레이 상세창이 쓰는 기능. 같은 프로세스라 HTTP 를 거치지 않고 바로 부른다."""
    def library() -> dict:
        items = [dict(it) for it in _build_items()]
        dur, ens = store.get_durations(), store.get_ens()
        rec = {(x.get("key") or x["title"]): x["ts"] for x in store.get_recent()}
        for it in items:
            it["duration"] = dur.get(it["title"])
            it["ens"] = ens.get(it["key"])
            it["lastPlayed"] = rec.get(it["key"])
        insts = [x for x in store.folio_cache("instruments")["items"] if isinstance(x, dict)]
        return {"scores": items, "playlists": store.get_lists()["playlists"],
                "instruments": [{"name": lib.pick(x, lib.NAME_KEYS), "equipped": bool(x.get("IsEquipped")),
                                 "type": lib.inst_kind(lib.pick(x, lib.NAME_KEYS))} for x in insts]}

    def stop() -> dict:
        r = _cli("stop_action", timeout=60)
        _note(r, "정지 (오버레이)")
        return {"ok": r.ok, "error": r.error, "message": r.message}

    def open_app() -> None:
        if LITE:
            _launch_app_window(f"http://127.0.0.1:{PORT}")

    def set_opening(on) -> None:
        store.set_settings({"opening": bool(on)})
        _say(f"[overlay] 연주 시작 연출 {'켜짐' if on else '꺼짐'}")

    def set_cfg(key, val) -> dict:
        """오버레이에서 바꾼 값을 설정에 적어 미니 창도 같은 값을 보게 한다 (반복·셔플·악기 고정).

        **설정만 적고 끝내면 안 된다** — 예전에는 여기서 `store.set_settings` 만 불러, 오버레이의 셔플이
        서버 대기열의 차례를 다시 만들지 않았다.
        미니 창의 길(`/api/settings`)과 **같은 뒤처리**(`settings_changed`)를 지난다."""
        return settings_put({str(key): val})

    def q_set(keys, start, src, name, insts) -> dict:
        return queue_set(keys, start, src, name, insts)

    return {"library": library, "play": play, "stop": stop, "instrument": equip,
            "open_app": open_app, "set_opening": set_opening, "set_cfg": set_cfg,
            # 대기열은 서버 한 곳에 있다 — 오버레이는 읽고 명령만 보낸다 (미니 창과 같은 하나)
            "queue_state": queue_state, "queue_set": q_set, "queue_step": queue_step,
            "queue_at": queue_play_index, "now": now_state,
            # 새 연주가 보였을 때의 연출도 서버의 「연주 한 번에 카드 한 번」 문을 지난다
            "opening_seen": opening_seen,
            # 주변 연주도 서버가 한 번만 조회한다 — 미니 창과 오버레이가 같은 값을 본다
            "near": near_state,
            # 밴드 곡명 옆 「방송 · 신청 n」 배지를 누르면 방송 창을 앞으로 (없으면 새로 띄운다)
            "bc_front": lambda: _bc.streamer("window", {})}


def _ov_state() -> dict:
    st = {"ok": True, "available": _ov is not None or _ov_can(), "visible": bool(_ov and _ov._visible),
          "clickThrough": bool(_ov and _ov._through), "player": bool(_ov and _ov._player),
          "locked": bool(_ov and _ov._locked), "auto": bool(_ov and _ov._auto)}
    if _ov is not None:      # 진단용 — 창 자리와 단추 판정이 어긋나는지 밖에서 볼 수 있어야 한다
        try:
            st["debug"] = {"pwin": list(_ov._pwin), "hits": len(_ov._hits), "pass_ui": bool(_ov._pass_ui),
                           "hover": list(_ov._hover) if _ov._hover else None,
                           "keep": [[h[0], h[1], h[2], h[3]] for h in _ov._hits if h[5]]}
        except Exception:
            pass
    return st


def _activity() -> dict:
    a = _cli("get_activity", timeout=30)
    perf = (a.body or {}).get("Performance") if isinstance(a.body, dict) else None
    # 재생 중이면 마지막으로 튼 곡의 길이를 기억해 둔다 (목록 총길이·진행 막대용).
    # 게임 쪽 MusicTitle 은 '악보: ' 접두가 없으므로 접두를 뗀 뒤 같은 곡일 때만 기록 (게임에서 직접 튼 다른 곡이 덮어쓰지 않게)
    if isinstance(perf, dict) and perf.get("IsPlaying") and _last_play.get("title"):
        mine = _PFX.sub("", _last_play["title"]).strip()
        theirs = _PFX.sub("", str(perf.get("MusicTitle") or "")).strip()
        if mine and (not theirs or mine == theirs):
            store.set_duration(_last_play["title"], perf.get("TotalDurationSeconds") or 0)
    return a.to_dict()



# ── 연주 인사 (프리셋) ────────────────────────────────────────────────
#
# 흐름:
#   내가 연주할 때 (A)
#     재생 누름 → 인사말 → 행동 → 표정 → (시작 연출 6초가 연주 시작에 맞춰 끝나게) → 연주 시작
#     곡이 끝나면(또는 끝나기 n초 전) 끝 인사
#   남의 연주를 들을 때 (P)
#     그 연주가 끝나면 「잘 들었습니다」 인사
#
# 인사말·행동·표정은 모두 write_chat 한 통로로 나간다(행동은 /손인사1 같은 명령, 표정은 ^^ 같은 글자).
# 50자 제한과 속도 제한(rate_limited)이 있어 한 단계씩 간격을 두고 보낸다.
_GREET = {"seq": 0, "last_listen": 0.0, "said_end": "", "pending": None}
_TOKEN_RE = re.compile(r"\*\(([^)]+)\)\*")
# 채팅 한 줄에 들어가면 안 되는 글자 — 제어 문자·줄바꿈(U+2028/2029 포함)·NUL. write_chat 은 날것 글자 하나를
# argv 로 받으므로 줄바꿈이 들어가면 게임이 어떻게 자르는지 우리가 모른다 (보안).
_CHAT_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
# 게임 채팅의 예약 글자 — `/` 는 행동 명령·채널 전환, `#` 은 비밀번호 (카탈로그 write_chat Note).
_CHAT_RESERVED = "/#"


def _chat_text(s) -> str:
    """채팅으로 나갈 글자 정리: 제어 문자를 빈칸으로, 연속 빈칸은 하나로, 홀로 남은 서로게이트는 U+FFFD."""
    s = cli.clean_text(str(s or ""))
    return re.sub(r"\s+", " ", _CHAT_CTRL.sub(" ", s)).strip()


def _chat(text: str) -> dict:
    """채팅 한 줄 (행동·표정도 여기로 나간다). 50자를 넘으면 자른다."""
    t = _chat_text(text)
    if not t:
        return {"ok": True, "skipped": True}
    if len(t) > 50:
        t = t[:49] + "…"
    r = _cli("write_chat", t, timeout=30)
    body = r.body if isinstance(r.body, dict) else {}
    if body.get("error") == "rate_limited":
        wait = float(body.get("retryAfterSeconds") or 1)
        time.sleep(min(5.0, max(0.2, wait)))
        r = _cli("write_chat", t, timeout=30)
        body = r.body if isinstance(r.body, dict) else {}
    ok = bool(r.ok) and not body.get("error")
    if not ok:
        _say(f"[인사] 못 보냈습니다: {body.get('error') or r.error or ''} · {t}")
    return {"ok": ok, "text": t, "error": body.get("error") or r.error}


def greet_render(text: str, it: dict | None, nxt: dict | None = None) -> str:
    """인사말의 *(곡)* 같은 자리를 채운다. 값이 없으면 그 자리는 빈칸으로 둔다."""
    it, nxt = it or {}, nxt or {}

    def one(m):
        k = m.group(1).strip()
        if k in ("곡", "노래제목", "제목", "song", "title"):
            return _chat_text(it.get("song") or it.get("title") or "")
        if k in ("아티스트", "artist"):
            return _chat_text(it.get("artist") or "")
        if k in ("길이", "재생시간", "시간", "duration"):
            d = it.get("duration")
            return _fmt_sec(d) if d else ""
        if k in ("다음곡", "다음", "next"):
            return _chat_text(nxt.get("song") or nxt.get("title") or "")
        if k in ("인원", "합주", "people"):
            return str(_GREET.get("mates") or 0)
        return m.group(0)
    tpl = (text or "").strip()
    out = _TOKEN_RE.sub(one, tpl).strip()
    # 채운 값(곡 제목·아티스트 — 게임이 준 글자, 남의 연주 제목도 온다)이 줄 맨 앞에 서서 `/행동`·`#비밀번호`
    # 모양이 되면 게임이 채팅 대신 **명령으로 실행**한다. 사람이 프리셋에 직접 `/` 로 적은 것만 명령이다 —
    # 자리 채우기로 생긴 앞머리 예약 글자는 뗀다 (보안).
    if out[:1] in _CHAT_RESERVED and tpl[:1] not in _CHAT_RESERVED:
        out = out.lstrip(_CHAT_RESERVED + " \t").strip()
    return out


def _fmt_sec(v) -> str:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return ""
    return f"{n // 60}:{n % 60:02d}"


def greet_pick(kind: str, key: str = "") -> dict | None:
    """이번에 쓸 프리셋. 곡별 지정이 먼저, 없으면 kind 의 기본값. '*' 면 무작위."""
    d = store.get_presets()
    items = [x for x in d["items"] if x.get("kind") == kind]
    if not items:
        return None
    # 곡별 지정(`by_song` — 곡 상세 시트·greet.html 「이 곡」)은 **시작 프리셋 id** 다. 그 종류에 없는 id 면
    # (끝 인사를 고르는데 곡에는 시작 프리셋이 붙어 있다) 곡별 지정은 건너뛰고 그 종류의 기본값으로 간다 —
    # 예전에는 그 id 를 못 찾아 **기본값이 아니라 첫 장**으로 떨어졌다.
    bound = d["by_song"].get(key) if key else ""
    if bound and bound != "*" and not any(x.get("id") == bound for x in items):
        bound = ""
    want = bound or d["pick"].get(kind) or ""
    if want == "*":
        return random.choice(items)
    if not want:
        # 정해 둔 것이 없으면 **그 종류의 첫 프리셋**을 쓴다. 예전에는 아무것도 안 보냈는데,
        # 프리셋을 만들어 두고도 인사가 한 줄도 안 나가 「채팅이 안 나온다」가 됐다.
        # 지금 화면에는 「안 씀」 고르기가 없으므로 빈 값은 「아직 안 정함」이라는 뜻뿐이다.
        return items[0]
    return next((x for x in items if x.get("id") == want), None) or items[0]


def greet_run(kind: str, it: dict | None = None, nxt: dict | None = None, key: str = "") -> dict:
    """프리셋 한 벌을 순서대로 내보낸다 (인사말 → 행동 → 표정). 빈 칸은 건너뛴다."""
    s = store.get_settings()
    if not s.get("greet_on"):
        return {"ok": True, "off": True}
    ps = greet_pick(kind, key)
    if not ps:
        return {"ok": True, "none": True}
    gap = max(0.3, float(s.get("greet_step") if s.get("greet_step") is not None else 1.0))
    sent = []
    for part in ("chat", "action", "emoji"):
        txt = ps.get(part) or ""
        if part == "chat":
            txt = greet_render(txt, it, nxt)
        if not txt:
            continue
        if sent:
            time.sleep(gap)
        sent.append(_chat(txt))
    return {"ok": True, "preset": ps.get("id"), "name": ps.get("name"), "sent": len(sent)}


def _greet_bg(kind: str, it: dict | None = None, key: str = "", delay: float = 0.0) -> None:
    """인사를 배경에서 내보낸다 (조회 루프를 막지 않게 — 단계마다 1초씩 쉰다).

    delay 를 주면 그만큼 기다렸다 보낸다 — 끝 인사를 **곡이 끝난 뒤 n초**에 할 때 쓴다.
    """
    def run():
        if delay > 0:
            time.sleep(min(120.0, delay))
        greet_run(kind, it, None, key)
    threading.Thread(target=run, daemon=True).start()


def greet_lead_sec() -> float:
    """재생을 누르고 실제 연주가 시작되기까지의 대기(초).

    **할 말이 없으면 기다리지 않는다.** 예전에는 프리셋이 하나도 없어도 8초를 기다린 뒤
    연출만 띄웠다 — 연출은 나오는데 인사는 안 나가는 것처럼 보였다.
    """
    s = store.get_settings()
    if not s.get("greet_on"):
        return 0.0
    ps = store.get_presets()
    if not any(x.get("kind") == "start" for x in ps.get("items") or ()):
        return 0.0
    v = s.get("greet_lead")
    return max(0.0, float(v if v is not None else 8.0))


def covers_state(song_id=None) -> dict:
    """커버 폴더 경로와 장수(설정 화면 「카드 커버」 줄) + 커버 고르기 시트의 칸 + 「내 커버 관리」 화면의 목록.
    칸: `defaults`(앱 기본) · `mine`(내 이미지 = `covers/mine/`) — 한 칸 = {name, value, url, size, usedBy},
    `value` 가 songs.json `cover` 에 들어가는 값, `usedBy` = 그 그림을 고른 곡 [{id, title}].
    `numbered`(바로 아래 번호 이름 파일) · `userDefaults`(내 기본 풀) · `folder`(커버 폴더) · `folders`(셋의 경로).
    장수: `own`(내 이미지 + 번호 파일) · `pool`(내 기본 풀) · `bundled`(앱 기본).
    폴더는 만들지 않는다 (읽기에 부작용이 없게 — 옛 폴더를 처음 한 번 `mine/` 으로 정리하는 것만 예외)."""
    d = store.covers_dir()
    m = store.covers_manage(COVERS_BUNDLED)
    mine = [dict(e, value=e["name"]) for e in m["mine"]]
    out = {"ok": True, "dir": d, "exists": os.path.isdir(d), "own": len(mine) + len(m["numbered"]),
           "pool": len(m["userDefaults"]), "bundled": len(m["defaults"]), "maxUpload": store.MAX_UPLOAD_COVER,
           "defaults": m["defaults"], "mine": mine, "numbered": m["numbered"], "userDefaults": m["userDefaults"],
           "generated": m["generated"], "generatedCount": m["generatedCount"],
           "folder": m["folder"], "folders": m["folders"]}
    sid = store._sid(song_id)
    if sid > 0:     # 커버 고르기 시트의 「번호대로」 칸 — 그 곡이 자동으로 받는 그림 (대개 생성 커버)
        scan = store.CoverScan(COVERS_BUNDLED)
        out["auto"] = {"id": sid, "where": scan.auto(sid)[2], "url": scan.auto_url(sid)}
    return out


def _cover_fields(items: list) -> None:
    """악보 아이템마다 커버를 붙인다 — `coverUrl`(연출 페이지가 그대로 쓰는 주소, `?v=` 붙음) ·
    `coverSel`(고른 값: "" = 번호대로). 폴더는 한 번만 훑는다."""
    try:
        scan = store.CoverScan(COVERS_BUNDLED)
        sels = {k: r.get("cover") or "" for k, r in store.get_songs()["songs"].items()}
    except Exception as e:            # 커버를 못 붙여도 악보함은 떠야 한다
        _say(f"[covers] 커버 주소를 만들지 못했습니다: {type(e).__name__}: {e}")
        return
    for it in items:
        sel = sels.get(it.get("key"), "")
        it["coverSel"] = sel
        it["coverUrl"] = scan.url(it.get("id"), sel)
        # 거의 정사각형(앨범 그림 — 온라인에서 받은 것 등)이면 연출 카드가 흐린 확대 배경 위에 원본을 가운데 둔다
        it["coverSquare"] = store.is_square(scan.resolve(it.get("id"), sel)[0]) if it.get("id") else False


def song_detail(key: str) -> dict:
    """곡 상세 시트 (미니 창 줄 메뉴 「곡 상세…」 · 펼친 플레이어 제목) — 「이 곡엔 이 인사·이 커버·이 악기」를
    한 번에 모아 준다. 흩어진 원본을 **읽기만** 한다:
      · 번호 기록(`songs.json`) — 번호·재생 횟수·처음/마지막·고른 커버·온라인 출처
      · 커버 — 지금 받는 그림의 주소와 차례(`CoverScan.resolve` 의 pick/own/default/gen)
      · 재생목록 — 이 곡이 든 목록과 그 목록에 적힌 곡별 악기
      · 시작 인사 — 곡별 지정(`presets.json` `by_song`)과 기본값(`pick.start`), 고를 수 있는 시작 프리셋
      · 악기 고정·기본 악기 (설정)
    → {"ok": True, song, cover, playlists, greet, inst} | {"ok": False, "error": "no_song"}.
    **이 PC 안에서만** (REMOTE_NEVER — 번호 DB 를 읽는다)."""
    key = key if isinstance(key, str) else ""
    rec = store.get_songs()["songs"].get(key) if key else None
    it = next((x for x in _build_items() if x.get("key") == key), None) if key else None
    if rec is None and it is None:
        return {"ok": False, "error": "no_song", "message": "그 곡을 찾지 못했습니다."}
    rec = dict(rec or {})
    title = rec.get("title") or (it or {}).get("title") or key
    dur = store.get_durations().get(title) or rec.get("duration")
    sid = int(rec.get("id") or (it or {}).get("id") or 0)
    song = {"key": key, "id": sid, "title": title,
            "song": rec.get("song") or (it or {}).get("song") or (it or {}).get("cleaned") or title,
            "artist": rec.get("artist") or (it or {}).get("artist") or "",
            "duration": dur, "plays": int(rec.get("plays") or 0),
            "first_seen": rec.get("first_seen"), "last_seen": rec.get("last_seen"), "gone": bool(rec.get("gone"))}
    sel = rec.get("cover") or ""
    cover = {"sel": sel, "url": "", "where": "", "src": None}
    if sid > 0:
        scan = store.CoverScan(COVERS_BUNDLED)
        cover["url"] = scan.url(sid, sel)
        cover["where"] = scan.resolve(sid, sel)[2]
        src = rec.get("cover_src")
        if isinstance(src, dict) and src.get("source"):
            cover["src"] = {"source": src["source"], "link": src.get("link") or "",
                            "sourceName": store.ONLINE_SOURCE_KO.get(src["source"], src["source"])}
    pls = []
    for pl in _lists()["playlists"]:
        for i, x in enumerate(pl["items"]):
            if x.get("key") == key:
                pls.append({"id": pl["id"], "name": pl["name"], "inst": x.get("inst") or "", "index": i,
                            "count": len(pl["items"])})
                break
    pr = store.get_presets()
    starts = [{"id": x["id"], "name": x["name"]} for x in pr["items"] if x.get("kind") == "start"]
    names = {x["id"]: x["name"] for x in starts}
    bound = pr["by_song"].get(key, "")
    dflt = pr["pick"].get("start", "")
    greet = {"bound": bound if bound in names else "", "boundName": names.get(bound, ""),
             "pick": dflt, "pickName": names.get(dflt, "무작위" if dflt == "*" else ""),
             "presets": starts}
    s = store.get_settings()
    greet["on"] = bool(s.get("greet_on"))
    return {"ok": True, "song": song, "cover": cover, "playlists": pls, "greet": greet,
            "inst": {"pin": s.get("inst_pin") or "", "default": s.get("default_inst") or ""}}


def greet_assign(key, preset) -> tuple:
    """곡 상세 시트 「연주 인사」 줄 — 이 곡을 틀 때 쓸 시작 인사를 붙인다 (`preset` "" = 기본으로).
    붙이는 곳은 `presets.json` 의 `by_song` 이고 `greet_pick("start", key)` 가 그 값을 먼저 본다.
    → (답, 상태 코드). **이 PC 안에서만** (REMOTE_NEVER)."""
    r = store.greet_bind(key, preset)
    return r, (200 if r.get("ok") else 400)


def cover_upload(handler, name: str) -> None:
    """커버 고르기 시트의 「내 이미지 추가…」 — 본문이 그림 파일 그대로다 (JSON 이 아니다).
    8MB 까지, png·jpg·webp·gif 만 (앞머리 바이트로 본다), 커버 폴더에 안전한 새 이름으로 (store.cover_upload_save).
    **이 PC 안에서만** (REMOTE_NEVER)."""
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        n = 0
    if n <= 0:
        return _json(handler, {"ok": False, "error": "empty", "message": "빈 파일입니다."}, 400)
    if n > store.MAX_UPLOAD_COVER:
        left = min(n, store.MAX_UPLOAD_COVER + (1 << 20))   # 조금만 비우고 연결을 닫는다
        while left > 0:
            chunk = handler.rfile.read(min(65536, left))
            if not chunk:
                break
            left -= len(chunk)
        handler.close_connection = True
        return _json(handler, {"ok": False, "error": "too_large",
                               "message": f"{store.MAX_UPLOAD_COVER // (1024 * 1024)}MB 까지 받습니다."}, 413)
    buf, left = [], n
    while left > 0:
        chunk = handler.rfile.read(min(1 << 20, left))
        if not chunk:
            break
        buf.append(chunk)
        left -= len(chunk)
    if left:
        handler.close_connection = True
        return _json(handler, {"ok": False, "error": "short_read", "message": "파일을 다 받지 못했습니다."}, 400)
    try:
        saved = store.cover_upload_save(b"".join(buf), name)
    except ValueError as e:
        msg = {"not_image": "그림 파일이 아닙니다 (png · jpg · webp · gif).", "empty": "빈 파일입니다.",
               "too_large": "파일이 너무 큽니다."}.get(str(e), str(e))
        return _json(handler, {"ok": False, "error": str(e), "message": msg}, 400)
    except OSError as e:
        return _json(handler, {"ok": False, "error": "write", "message": f"저장하지 못했습니다: {e}"}, 500)
    st = covers_state()
    item = next((c for c in st["mine"] if c["name"] == saved), None)
    return _json(handler, {"ok": True, "name": saved, "value": saved, "item": item,
                           "defaults": st["defaults"], "mine": st["mine"]})


def covers_open(which: str = "root") -> dict:
    """커버 폴더(`root`) · 내 이미지(`mine`) · 내 기본 풀(`default`)을 탐색기로 연다 (없으면 `mine/`·`default/` 까지
    만든다). 경로는 이 셋 중에서만 고른다 — 받은 글자로 경로를 만들지 않는다. 개발·검사 서버(`MOBIW_NO_BROWSER=1`)는
    창을 띄우지 않는다 — 경로만 돌려준다."""
    store.covers_migrate()
    root = store.covers_dir()
    d = {"root": root, "mine": store.covers_mine_dir(), "default": store.covers_pool_dir()}.get(which)
    if d is None:
        return {"ok": False, "error": "bad_which", "message": "which 는 root · mine · default 중 하나입니다."}
    try:
        os.makedirs(store.covers_mine_dir(), exist_ok=True)
        os.makedirs(store.covers_pool_dir(), exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": "mkdir", "message": f"폴더를 만들지 못했습니다: {e}", "dir": d}
    if os.environ.get("MOBIW_NO_BROWSER") == "1" or not hasattr(os, "startfile"):
        return {"ok": True, "dir": d, "opened": False}
    try:
        os.startfile(d)   # noqa: S606 — 우리 폴더 하나만 (받은 글자로 경로를 만들지 않는다)
    except OSError as e:
        return {"ok": False, "error": "open", "message": f"폴더를 열지 못했습니다: {e}", "dir": d}
    return {"ok": True, "dir": d, "opened": True}


# 앱에 딸린 기본 커버 풀 (ui/folio/covers/default/*.svg) — 사용자 풀이 비었을 때 쓴다.
# 묶인 exe 는 `_MEIPASS/ui`, 소스로 돌 때는 **저장소의** `ui/` 다 (이 파일은 `folio/` 안에 있어 UI_DIR 가 다르다).
COVERS_BUNDLED = os.path.join(RES if FROZEN else os.path.dirname(HERE), "ui", "folio", "covers", "default")


# ── 재생 대기열 — **미니 창과 오버레이가 같은 하나를 본다** ───────────────
#
# 규칙:
#  1) 어느 목록에서 곡을 틀면 그 목록의 **그 곡 아래부터** 이어서 재생한다.
#  2) 한 곡만 재생하는 것은 목록에 곡이 하나뿐일 때와 **한 곡 반복**일 때뿐이다.
#  3) 이 대기열은 두 화면이 같은 하나다 — 어느 창에서 눌러도 같은 것이 움직인다.
#
# 예전에는 미니(브라우저 S.queue)와 오버레이(프로세스 _queue)가 각자 대기열과 넘김 엔진을
# 들고 있어서, 같은 순간에 두 창의 「다음 곡」이 달랐다. 이제 여기 한 곳만 있다.
OPENING_SEC = 6.0        # 시작 연출 길이 (opening_tk 의 T_TOTAL 과 같은 값이어야 한다)
ASK_NEXT_SEC = 60.0      # 합주가 끝난 뒤 「다음 곡으로?」 를 물어보고 기다리는 시간
_Q_LOCK = threading.Lock()
_Q = {"keys": [], "order": [], "pos": -1, "src": "", "name": "", "inst": {}, "gen": 0, "rev": 0,
      # **요청 단위 회차 제한** (모비웍스 「연주」 카드 — 큐에 담긴 요청은 정한 회차만큼만 재생한다).
      #   passes: None = 폴리오 제 설정(repeat·shuffle)대로 · N = 목록을 **N번 돌고 멈춘다** (repeat·shuffle 무시)
      #   pass  : 지금 몇 번째 회차인가 (1부터)
      "passes": None, "pass": 1,
      # 방송 신청으로 끝에 담긴 곡 — key → 신청자 이름 (`queue_append`). 화면의 「· 신청 이름」 표시에만 쓴다.
      # 게임 채팅·연주 인사로는 나가지 않는다 (`greet_render` 는 이 칸을 읽지 않는다).
      "req": {}}
SOLO_PASSES_MAX = 20     # 곡 하나·목록 회차 상한 (workqueue.PLAY_COUNT_MAX 와 같은 값)
_NOW = {"playing": False, "title": "", "el": 0.0, "tot": 0.0, "loop": False, "inst": "",
        "at": 0.0, "ok": False, "error": "", "perf": {}, "start_at": None,
        # 탈것 탑승 중 — get_activity.Mode.MountPartState == "Mounted" (실측, docs/CLI.md §4)
        "mounted": False}
# 이 PC 가 마지막으로 받아 둔 행동·표정 목록 (`/api/social`) — **폰(밖)은 이것만 본다** (게임에 묻지 않는다)
_SOCIAL = {"ok": False, "error": "not_read", "message": "PC 가 아직 목록을 읽지 않았습니다.", "behaviours": [], "facials": [], "at": 0.0}


def _ensemble_cached() -> dict:
    """`/api/ensemble` 의 **밖(폰) 답** — 감시 스레드(`_near_step`)가 마지막으로 묶어 둔 주변 연주(`_NEAR["groups"]`)를
    `_ensemble()` 의 players 모양으로 편다. 연주 중인 사람만 있다(조용한 사람은 수만 센다 — `quiet`)."""
    players = []
    for g in _NEAR.get("groups") or []:
        for m in g.get("members") or []:
            players.append({"name": _s(m.get("realm")), "title2": _s(m.get("title")), "distance": m.get("distance"),
                            "playing": True, "title": _s(g.get("title")), "channels": m.get("channels") or 0,
                            "total": g.get("total") or 0, "elapsed": g.get("elapsed") or 0,
                            "remaining": g.get("remaining"), "loop": bool(g.get("loop"))})
    return {"ok": bool(_NEAR.get("ok")), "error": _NEAR.get("error") or None, "message": _NEAR.get("message") or "",
            "players": players, "quiet": _NEAR.get("quiet") or 0, "cached": True, "at": _NEAR.get("at") or 0.0}


def _activity_cached() -> dict:
    """`/api/activity` 의 **밖(폰) 답** — 감시 스레드(`_watch`)가 마지막으로 본 `get_activity` 값을 그 모양대로.
    폰이 열릴 때 게임에 `get_activity` 를 보내지 않는다. 아직 한 번도 못 봤으면 ok:false."""
    perf = dict(_NOW.get("perf") or {})
    body = {"Performance": perf} if perf else {}
    if _NOW.get("mounted"):
        body["Mode"] = {"MountPartState": "Mounted"}
    return {"ok": bool(_NOW.get("ok")), "command": "get_activity", "error": _NOW.get("error") or None,
            "message": "" if _NOW.get("ok") else "PC 가 아직 연주 상태를 읽지 않았습니다.",
            "body": body, "cached": True, "at": _NOW.get("at") or 0.0}
# 합주 판정 — **StartAt 이 같은 사람들이 한 합주단이다.** 게임이 합주 여부를 알려 주지 않아서
# 예전에는 「같은 곡 + 채널 2개 이상」으로 어림짐작했는데, 옆에서 같은 곡을 따로 트는 사람과
# 구분이 안 됐다. StartAt 은 한 연주 안에서 변하지 않고 합주단 전원이 **같은 값**을 갖는다(실측).
_ENS = {"on": False, "n": 0, "at": 0.0, "since": 0.0, "start_at": None, "members": [], "near": 0}
# 주변 연주 — 한 번 조회해서 **합주 판정과 화면 표시를 같이** 만든다 (조회를 두 번 하지 않는다).
_NEAR = {"at": 0.0, "want": 0.0, "ok": False, "error": "", "message": "", "groups": [],
         "playing": 0, "quiet": 0, "realm": "", "realm_at": 0.0, "rev": 0,
         # 아까 보이던 연주들 — 사라지면 「끝났다」로 보고 들었을 때 인사를 한다
         "seen": {}}
_NEAR_PLAY = 1.0         # 내가 연주 중일 때 주기(초) — A 가 시작하는 순간을 그 자리에서 잡는다
_NEAR_IDLE = 2.0         # 화면이 주변 연주를 보고 있을 때
_NEAR_WANT = 12.0        # 「보고 있다」로 치는 시간 — 아무도 안 보면 조회하지 않는다
_NEAR_SAME = 0.05        # StartAt 이 이만큼 안이면 같은 연주로 본다
_ENG = {"own": "", "started": 0.0, "adv_at": 0.0, "stop_at": 0.0, "gap_until": 0.0, "cut_at": 0.0,   # cut_at: 반복 설정에서 끝에 맞춰 끊기로 잡아 둔 시각
        "last": (False, 0.0, 0.0), "fail": 0, "msg": "",
        # 합주가 끝났다 — 다음 곡으로 넘어갈지 **사람에게 물어본다**.
        # 붙어 있던 사람들이 그 곡을 계속 치고 있을 수 있어 우리가 임의로 넘기지 않는다.
        "ask_at": 0.0, "ask_n": 0,
        # 모비웍스 큐가 「이 곡 끝나면 양보해 주세요」를 부탁했다 (work_yield).
        #   yield_at : 부탁받은 시각 (0 이면 부탁 없음)
        #   held     : 곡 경계에 **멈춰 서 있다** — 다음 곡으로 안 넘어간다
        "yield_at": 0.0, "held": False,
        #   work_stop: 큐가 일을 끝내고 **부탁을 거둔** 시각 (0 이면 없음) — 이어서 틀지 않고 「작업에 양보해 멈춤」(■)으로
        #              남긴다 (작업이 끝났다고 옛 연주가 다시 시작되면 안 된다). 다음 재생·새 부탁·정지가 지운다
        "work_stop": 0.0,
        # **곡 하나 재생**(`play_solo` — /api/play · 모비웍스 「연주」 카드 「이 곡만」)을 감시가 **주인으로** 들고 있다.
        #   {"title", "inst", "key", "passes": N|None, "done": k} — 곡이 끝나면(또는 게임 반복이라 우리가 끊으면) 그 경계를
        #   대기열과 **같은 자리**(`_engine_step` 의 gap 끝)에서 지난다: 양보 부탁이 있으면 `held`, 아니면 done<passes 면 같은 곡을
        #   다시, 아니면 **멈춘다** (폴리오 대기열로 이어 가지 않는다). None = 곡 하나 재생 중이 아니다.
        #   실측: 이것이 없어서 「이 곡만」 연주에 「이 곡 끝나면 양보」를 부탁하면 경계가 영영 안 왔다 (own 이 비어
        #   `_engine_step` 이 첫 줄에서 돌아갔다) — 큐는 곡 길이+30초를 기다린 뒤 「양보를 받지 못했습니다」로 실패했다.
        "solo": None,
        # 곡 하나 재생이 **어떻게 끝났는가** — 모비웍스 「연주」 카드가 끝을 판정한다 (work_now.soloEnd). solo 가 None 이 될 때마다 적는다:
        #   {"title", "key", "passes", "done", "how": done|stopped|error, "msg", "at"} — done = 회차를 다 쳤다 · stopped = 사람이 ■ 를
        #   눌렀거나 다른 재생이 자리를 가져갔다(대기열·탈것·정지·양보 뒤 걷힘) · error = 다음 회차를 틀지 못했다
        "solo_end": None,
        # 대기열 곡을 **트는 중**(play() 가 CLI 를 부르는 사이) — 그 몇 초는 playing 도 gap 도 아니라 work_now.busy 가 꺼졌다.
        # 「지금 대기열 이어서」 카드가 그 구멍을 「끝났다」로 읽지 않게 busy 에 넣는다 (0 이면 아니다)
        "starting": 0.0,
        # 탈것 탑승 중이라 보내지 않은 재생 — 내리면(감시가 mounted 거짓을 보면) 한 번 이어서 튼다.
        #   대기열 곡: {"gen", "pos", "at", "seen"} · 곡 하나(/api/play): {"title", "inst", "key", "at", "seen"}
        #   seen: 탑승을 **실제로 본** 뒤에만 잇는다 (Mode 를 안 주는 CLI 에서 거절 → 재시도가 돌지 않게)
        "wait_mount": None}


def _q_items() -> list:
    """대기열을 화면이 그릴 수 있는 줄로 (재생 차례대로)."""
    by = {}
    for it in _build_items():
        by[it["key"]] = it
    dur, ens = store.get_durations(), store.get_ens()
    out = []
    for i in _Q["order"]:
        if not (0 <= i < len(_Q["keys"])):
            continue
        k = _Q["keys"][i]
        src = by.get(k) or {}
        t = src.get("title") or k
        out.append({"key": k, "title": t, "song": src.get("song") or src.get("cleaned") or t,
                    "artist": src.get("artist") or "", "duration": dur.get(t), "ens": ens.get(k),
                    "inst": _Q["inst"].get(k) or "", "missing": not src,
                    "req": _Q["req"].get(k) or ""})
    return out


def queue_state() -> dict:
    """두 화면이 그대로 그리는 값. CLI 를 쓰지 않는다."""
    with _Q_LOCK:
        items = _q_items()
        pos = _Q["pos"]
        s = store.get_settings()
        return {"ok": True, "src": _Q["src"], "name": _Q["name"], "pos": pos, "total": len(items),
                "items": items, "repeat": s.get("repeat"), "shuffle": bool(s.get("shuffle")),
                # 악기 고정 — 비어 있지 않으면 모든 곡이 이 악기로 시작한다 (두 화면이 같은 값을 그린다)
                "instPin": inst_pin(),
                "now": items[pos] if 0 <= pos < len(items) else None,
                "next": _q_peek(items, pos, s.get("repeat")), "gen": _Q["gen"], "rev": _Q["rev"],
                # 요청 단위 회차 제한 (None 이면 폴리오 제 설정대로) · 지금 회차 — 「연주」 카드의 「목록 N회」
                "passes": _Q["passes"], "pass": _Q["pass"],
                # 마지막으로 낸 연출 {song, inst, at} — 연출 페이지가 카드의 악기 줄을 여기서 다시 읽는다
                "opening": dict(_OPENING_LAST),
                # 탈것 탑승 중 (두 화면의 「탈것 탑승 중」 · 「내리면 재생」 표시)
                "mounted": bool(_NOW.get("mounted")), "wait_mount": bool(_ENG.get("wait_mount")),
                # 방송 — 본창 머리줄·오버레이 밴드의 「방송 · 신청 n」 (켜짐 · 대기 수)
                "bc": _bc.ROOM.summary()}


def _q_peek(items: list, pos: int, repeat) -> dict | None:
    """다음 곡 (없으면 None). 전체 반복이면 끝에서 처음으로 돌아온다."""
    n = len(items)
    if not n:
        return None
    j = (pos if pos >= 0 else -1) + 1
    if j >= n:
        if _Q["passes"] is not None:               # 회차 제한 — 남은 회차가 있으면 처음으로, 없으면 끝 (repeat 무시)
            if _Q["pass"] >= _Q["passes"]:
                return None
        elif repeat != "all":
            return None
        j = 0
    return items[j]


def _q_order_for(keys: list, shuffle: bool, cur: int) -> list:
    """재생 차례. 셔플이면 지금 곡을 맨 앞에 두고 나머지를 섞는다."""
    idx = list(range(len(keys)))
    if not shuffle or len(idx) < 2:
        return idx
    rest = [i for i in idx if i != cur]
    random.shuffle(rest)
    return ([cur] + rest) if 0 <= cur < len(keys) else rest


def queue_set(keys: list, start=None, src: str = "", name: str = "", insts: dict | None = None,
              play_now: bool = True, passes=None) -> dict:
    """목록에서 재생 시작 — 그 목록 전체를 대기열로 삼고 고른 곡부터 간다.

    `passes=N` 은 **요청 단위 회차 제한** (모비웍스 「연주」 카드 「재생목록 전체 · N회」): 목록을 차례대로 N번 돌고
    **멈춘다** — 그 요청에 한해 폴리오의 repeat·shuffle 설정을 무시한다 (차례는 담긴 순서, 끝에서 처음으로 도는 것도
    N회까지만). None 이면 예전 그대로 폴리오 제 설정대로다 (재생목록 화면·「지금 대기열 이어서」)."""
    keys = [str(k) for k in (keys or []) if str(k or "")]
    passes = max(1, min(SOLO_PASSES_MAX, int(passes))) if isinstance(passes, int) and not isinstance(passes, bool) else None
    with _Q_LOCK:
        _Q["keys"] = keys
        _Q["inst"] = {str(k): str(v) for k, v in (insts or {}).items() if v}
        _Q["req"] = {}                             # 새 대기열 — 옛 신청 표시는 걷는다
        _Q["src"], _Q["name"] = str(src or ""), str(name or "")
        cur = 0
        if isinstance(start, int):
            cur = max(0, min(start, len(keys) - 1)) if keys else 0
        elif isinstance(start, str) and start in keys:
            cur = keys.index(start)
        s = store.get_settings()
        _Q["order"] = _q_order_for(keys, bool(s.get("shuffle")) and passes is None, cur)
        _Q["pos"] = _Q["order"].index(cur) if (keys and cur in _Q["order"]) else -1
        _Q["passes"], _Q["pass"] = passes, 1
        _Q["gen"] += 1
        _Q["rev"] += 1
    if not keys:
        return {"ok": False, "error": "empty_queue", "message": "재생할 곡이 없습니다."}
    if passes is not None:
        _say(f"[queue] 「{_Q['name'] or 'ㅡ'}」 {len(keys)}곡 · {passes}회 돌고 멈춥니다 (이 요청은 반복·셔플 설정을 안 봅니다)")
    return _q_play_here() if play_now else queue_state()


def queue_passes(passes=None) -> dict:
    """대기열의 회차 제한을 걷거나(None) 새로 건다 — 「지금 대기열 이어서」는 폴리오 제 설정대로 가야 하므로
    서버 `_play_start(resume)` 가 None 으로 걷는다. 자리(pos)는 건드리지 않는다."""
    with _Q_LOCK:
        _Q["passes"] = max(1, min(SOLO_PASSES_MAX, int(passes))) if isinstance(passes, int) and not isinstance(passes, bool) else None
        _Q["pass"] = 1
        _Q["rev"] += 1
    return queue_state()


def queue_shuffle_changed() -> None:
    """셔플을 켜고 끄면 **그 자리에서** 차례를 다시 만든다 (지금 곡은 그대로 이어서)."""
    with _Q_LOCK:
        if not _Q["keys"]:
            return
        cur = _Q["order"][_Q["pos"]] if 0 <= _Q["pos"] < len(_Q["order"]) else 0
        s = store.get_settings()
        _Q["order"] = _q_order_for(_Q["keys"], bool(s.get("shuffle")), cur)
        _Q["pos"] = _Q["order"].index(cur) if cur in _Q["order"] else 0
        _Q["rev"] += 1                             # 차례가 바뀌었다 — 두 화면이 다시 그린다


# 두 화면(미니·오버레이)이 같이 쓰는 재생 설정 — 바뀌면 대기열의 rev 를 올려 두 화면이 다시 읽게 한다.
_SHARED_PLAY_KEYS = ("shuffle", "repeat", "inst_pin")


def settings_changed(before: dict, after: dict) -> None:
    """설정을 저장한 **뒤처리** — 미니(`/api/settings`)와 오버레이(`set_cfg`)가 같은 이 함수를 지난다.

    · 셔플이 바뀌면 그 자리에서 차례를 다시 만든다 (`queue_shuffle_changed`).
    · 반복·셔플·악기 고정이 바뀌면 `_Q["rev"]` 를 올린다 — 미니는 `/api/now` 의 rev 가 바뀌면
      `/api/queue` 를 다시 읽고, 오버레이는 2초마다 `queue_state` 를 읽는다. 대기열이 비어 있어도 올린다
      (예전에는 대기열이 있을 때만 올려, 한쪽에서 켠 셔플을 다른 쪽이 창을 다시 열 때까지 몰랐다)."""
    before, after = before or {}, after or {}
    if bool(before.get("shuffle")) != bool(after.get("shuffle")):
        queue_shuffle_changed()
    if any(before.get(k) != after.get(k) for k in _SHARED_PLAY_KEYS):
        with _Q_LOCK:
            _Q["rev"] += 1


def settings_put(patch: dict) -> dict:
    """설정 저장 + 뒤처리 (같은 프로세스 안에서 부르는 길 — 오버레이)."""
    before = dict(store.get_settings())
    after = store.set_settings(patch)
    settings_changed(before, after)
    return after


def inst_pin() -> str:
    """고정한 악기 이름 ("" = 고정 안 함). 이름 양끝 공백은 살린다 — 그게 이름의 일부인 악기가 있다."""
    v = store.get_settings().get("inst_pin")
    return v if isinstance(v, str) and v.strip() else ""


def _stop_for_switch() -> bool:
    """다른 곡을 고르면 **지금 곡부터 끊는다** → 끊었으면 True.

    예전 차례는 「인사말 → 행동 → 표정 → 시작 연출 → 연주」 를 도는 `lead` 초 동안 **옛 곡이 계속
    울리고**, 끊는 것은 맨 끝 `play()` 안에서(그것도 `stop_before_play` 를 켰을 때만) 했다. 사람은
    곡을 바꿨는데 귀에는 옛 곡이 lead 초 더 들린다 — 그래서 바꾸는 **첫머리에** 끊는다.

    「지금 우리가 치고 있나」는 **감시가 재 둔 값**(`_NOW["playing"]` + `_ENG["own"]`)으로 본다 —
    곡을 누를 때마다 CLI 에 새로 묻지 않는다. 우리 곡이 아니면(게임에서 손으로 튼 것 등)
    예전처럼 `play()` 의 설정(`stop_before_play`)에 맡긴다.

    **합주 게이트는 그대로다** (`may_stop = not ens.on …`):
    합주 중이면 여기서 끊지 않는다. 붙어 있는 사람들은 내가 멈춰도 계속 치므로, 끊을지는
    예전 길(`play()` 의 `stop_before_play`)이 정하던 그대로 둔다.
    인사말 대기(`lead`) 중에 또 고른 경우는 이미 끊겨 있어(`playing` 이 거짓) 아무것도 안 한다.

    끊었으면 **그 자리에서** `_NOW`/`_ENG` 를 「안 치는 중」으로 돌린다 — 두 화면(미니·오버레이)이
    `lead` 동안 옛 제목 대신 「대기 중」과 `lead_title`(다음 곡)을 그리게. 미니는 `perf.IsPlaying` 을
    보므로 그것도 거짓으로 맞춘다. `_ENG["own"]` 을 비우는 것은 감시(`_engine_step`)가
    「곡이 멈췄다 = 끝났다」로 읽고 옛 곡의 끝 인사·다음 곡 넘김을 하지 않게 하려는 것이기도 하다.
    """
    if not (_NOW.get("playing") and _ENG.get("own")):
        return False
    if _ENS.get("on"):
        _say("[queue] 합주 중이라 곡을 바꿀 때 먼저 끊지 않습니다 — 예전 차례대로")
        return False
    ok = False
    for attempt in range(4):          # 상태 전이 중(invalid_state)이면 짧게 다시 (stop() 과 같은 규칙)
        r = _cli("stop_action", timeout=60)
        _note(r, "곡 바꿈 — 지금 연주 먼저 정지")
        if r.ok:
            ok = True
            break
        if r.error != "invalid_state":
            break
        time.sleep(0.8 + 0.4 * attempt)
    if not ok:
        return False                  # 못 끊었다 — 예전처럼 play() 가 설정대로 다시 본다
    perf = _NOW.get("perf") if isinstance(_NOW.get("perf"), dict) else {}
    _NOW.update(playing=False, title="", el=0.0, tot=0.0, loop=False, start_at=None, at=time.time(),
                perf=dict(perf, IsPlaying=False, MusicTitle="", ElapsedSeconds=0.0, TotalDurationSeconds=0.0))
    _ENG["own"] = ""
    _solo_finish("stopped")
    _ENG["last"] = (False, 0.0, 0.0)
    _ENG["ask_at"] = 0.0
    return True


# ── 합주 시작 연출 — **연주 한 번에 카드 한 번** (두 번씩 뜨지 않게) ──
# 부르는 곳이 여럿이었다: 인사 타임라인(lead−6초) · 미니 창의 재생 직후 · 합주로 갓 인식된 순간 ·
# 오버레이의 「새 연주가 보였다」 감시. 각자 7초 간격(`Overlay.OPEN_GAP`)만 보고 있어서, 인사 연출 뒤
# 곡이 실제로 울리고 감시가 그것을 보는 데 8초쯤 걸리면 **두 번째 카드**가 나왔다 (20:59:06 · 20:59:14).
# 이제 판정은 서버 한 곳이다 — 모든 길이 `opening_fire(...)` → `opening_once(key)` 를 지난다.
#
# 「한 연주」의 이름(key):
#   · 우리가 튼 연주   → ("own", 대기열 세대, 트는 차례 번호). `_q_play_here` 가 트는 순간 정한다.
#                        감시가 그 곡을 보면(같은 제목, 처음 본 StartAt) 같은 연주로 묶는다.
#   · 게임에서 직접 튼 연주 → ("game", 다듬은 제목, StartAt)
# 합주로 인식된 순간은 **새 카드가 아니다** — 카드가 아직 안 나갔으면 그 카드에 사람들을 싣는다.
_OPEN_LOCK = threading.Lock()
_OPEN = {"seq": 0, "own": None, "shown": {}}
_OPEN_KEEP = 3600.0      # 이만큼 지난 기록은 버린다 (같은 key 가 다시 올 일이 없다)
_OPEN_OWN_SEC = 30.0     # 트는 차례를 연 뒤 이 안(+lead)에 보인 같은 제목은 우리 연주로 본다


def opening_once(key) -> bool:
    """이 연주(key)에 카드를 **처음** 내는가. 처음이면 적어 두고 True, 이미 냈으면 False."""
    now = time.time()
    with _OPEN_LOCK:
        shown = _OPEN["shown"]
        for k in [k for k, t in shown.items() if now - t > _OPEN_KEEP]:
            shown.pop(k, None)
        if key in shown:
            return False
        shown[key] = now
        return True


def _opening_own_begin(title: str, lead: float) -> tuple:
    """우리가 곡을 트는 차례를 연다 — 이 차례의 연출은 이 key 하나로만 나간다."""
    with _OPEN_LOCK:
        _OPEN["seq"] += 1
        key = ("own", int(_Q.get("gen") or 0), _OPEN["seq"])
        _OPEN["own"] = {"key": key, "title": _norm_title(title), "at": time.time(),
                        "lead": float(lead or 0), "start_at": None}
    return key


def _opening_key(title: str, start_at=None):
    """지금 보인 연주(title, StartAt)가 어느 연주인가. 우리가 막 튼 것이면 그 차례의 key."""
    t = _norm_title(title)
    own = _OPEN.get("own")
    if own and t and own["title"] == t:
        fresh = time.time() - own["at"] < own["lead"] + _OPEN_OWN_SEC
        still = _norm_title(_ENG.get("own") or "") == t
        if fresh or still:
            sa = own.get("start_at")
            if sa is None or start_at is None or abs(float(sa) - float(start_at)) <= _NEAR_SAME:
                if sa is None and start_at is not None:
                    own["start_at"] = float(start_at)      # 처음 본 StartAt 이 이 연주의 것이다
                return own["key"]
    return ("game", t, round(float(start_at), 2) if isinstance(start_at, (int, float)) else None)


def _opening_players() -> list:
    """카드에 실을 사람들. 합주가 이미 인식됐으면 그 사람들(칭호·영지), 아니면 「나」 한 명."""
    if _ENS.get("on") and _ENS.get("members"):
        out = [["", "나"]]           # 합주단에는 나도 든다 — 「2인 합주」 배지·게스트 리스트가 실제 인원과 같게
        for m in _ENS["members"]:
            name = _s(m.get("realm")) or _s(m.get("title"))
            if name:
                out.append([_s(m.get("title")), name])
        if len(out) > 1:
            return out
    return [["", "나"]]


_OPENING = None   # 앱 시작 때 만드는 연출(웹). 오버레이 밴드가 있으면 밴드의 play_opening 이 같은 것을 쓴다


def opening_boot() -> None:
    """연출을 **오버레이와 무관하게** 앱 시작 때 만든다 (웜 스타트).

    예전에는 연출이 오버레이 밴드의 `_build` 안에서만 만들어져, 밴드를 안 켜면 `_ov` 가 None 이라
    연출이 아예 없었다 (배포판 실측). 웹 연출(WebView2)은 tk 창이 필요 없으니 여기서 만든다."""
    global _OPENING
    if _OPENING is not None or not (LITE or HOSTED):
        return
    try:
        import opening_wv
        _OPENING = opening_wv.make_opening(None, lambda: _game_rect(native=True), _say)
    except Exception as e:
        _say(f"[opening] 연출을 못 만들었습니다: {type(e).__name__}: {e}")


def _opening_target():
    """연출을 실제로 띄울 것 — 오버레이 밴드가 있으면 그것(7초 겹침 방지 포함), 없으면 앱 시작 때 만든 것."""
    if _ov is not None:
        return _ov.play_opening
    if _OPENING is not None:
        return _OPENING.play
    return None


# 마지막으로 낸 연출 — 연출 페이지가 연출 직전에 `/api/queue` 의 `opening` 으로 다시 읽는다.
# 창 쪽(opening_wv)이 `playOpening` 에 넘기는 값을 song·players·badge·kicker 로 추려서 `inst` 가 빠지기 때문이다.
_OPENING_LAST = {"song": "", "inst": "", "at": 0.0}


def _opening_inst(inst=None) -> str:
    """연출 카드 앞면 제목 위 줄 — **이 연주를 시작하는 악기**, 화면 표기 그대로(「[피아노] 3화음 고결한 서약의 피아노」,
    library.inst_label = 미니 창·설정과 같은 모양).
    차례: 부른 쪽이 준 악기(대기열 곡의 악기 → 없으면 기본 악기 — play() 가 바꿔 드는 것) → 감시가 본 연주 악기
    (`_NOW`) → 캐시의 지금 든 악기(IsEquipped). 모르면 "" — 화면은 그 줄을 감춘다."""
    name = _s(inst)
    if not name.strip():
        name = _s(_NOW.get("inst"))
    if not name.strip():
        try:
            name = next((lib.pick(x, lib.NAME_KEYS) for x in store.folio_cache("instruments")["items"]
                         if isinstance(x, dict) and x.get("IsEquipped")), "") or ""
        except Exception:
            name = ""
    return lib.inst_label(name)


def _queue_inst(it: dict) -> str:
    """대기열 곡이 시작하는 악기 이름 — 고정한 악기 → 곡에 적은 악기 → 설정의 기본 악기 (play() 와 같은 차례)."""
    return inst_pin() or _s((it or {}).get("inst")) or _s(store.get_settings().get("default_inst"))


def opening_fire(key, song: str, why: str = "", players=None, inst=None) -> dict:
    """연출을 내는 **유일한 길**. 같은 연주(key)에는 한 번만 — 두 번째부터는 건너뛴다.
    `inst` = 이 연주를 시작하는 악기 이름 (모르면 None — `_opening_inst` 가 지금 든 악기로 채운다)."""
    target = _opening_target()
    if target is None:
        return {"ok": False, "error": "no_overlay", "message": "이 판에는 연출이 없습니다."}
    if store.get_settings().get("opening") is False:
        return {"ok": True, "skipped": True, "reason": "off"}
    if not opening_once(key):
        _say(f"[opening] 이 연주는 이미 연출했습니다 — 건너뜁니다 ({why})")
        return {"ok": True, "skipped": True, "reason": "once"}
    data = {"song": _clean_title(song), "players": players or _opening_players(), "badge": "", "kicker": "",
            "inst": _opening_inst(inst)}
    _OPENING_LAST.update(song=data["song"], inst=data["inst"], at=time.time())
    try:
        return target(data) or {"ok": True}
    except Exception as e:
        return {"ok": False, "error": "failed", "message": str(e)}


# ── 게임에서 시작한 연주의 카드 (합주) ──
# 합주는 게임 안에서 시작된다 — 우리 대기열이 아니라 감시(`_watch`)가 「내가 연주를 시작했다」를 본다.
# 그 순간 합주단은 이미 같은 StartAt 으로 함께 울리고 있으므로, **주변 조회 한 번을 기다렸다가** 그 사람들을 실어
# 카드 한 장을 띄운다 (합주곡 정보를 함께 보여 준다).
# 조회가 안 오면 GAME_CARD_WAIT 뒤에 「나」 혼자로 띄운다. 우리가 튼 곡은 이미 카드가 나갔으니 once 문에서 걸러진다.
# 예전에는 오버레이 창(overlay_tk)의 폴링이 보자마자 불러 사람들이 채워지기 전이었고, 오버레이를 꺼 두면 아예 안 떴다.
_GAMECARD = {"key": None, "at": 0.0, "done": True}
GAME_CARD_WAIT = 2.5     # 주변 조회가 이만큼 안 오면 「나」 혼자로 띄운다 (조회는 연주 중 1초 주기)


def _game_card_step(now: float) -> None:
    """감시 틱마다 — 내 연주가 새로 시작됐으면 합주단이 잡히는 첫 조회 뒤에 카드 한 장."""
    if not _NOW.get("playing") or not _NOW.get("title"):
        _GAMECARD.update(key=None, done=True)
        return
    key = (_norm_title(_NOW["title"]), _NOW.get("start_at"))
    if _GAMECARD["key"] != key:
        _GAMECARD.update(key=key, at=now, done=False)
    if _GAMECARD["done"]:
        return
    sa, mine = _NOW.get("start_at"), _ENS.get("start_at")
    polled = _NEAR["at"] >= _GAMECARD["at"] and _NEAR.get("ok") and sa is not None and mine is not None \
        and abs(float(mine) - float(sa)) <= _NEAR_SAME
    if polled or now - _GAMECARD["at"] >= GAME_CARD_WAIT:
        _GAMECARD["done"] = True
        opening_seen(_NOW["title"])


def opening_seen(title: str, song: str = "") -> dict:
    """감시가 「새 연주가 울린다」를 봤다 — 게임에서 직접 튼 것일 수도, 우리가 튼 것일 수도 (`_game_card_step`)."""
    same = _norm_title(_NOW.get("title")) == _norm_title(title)
    key = _opening_key(title, _NOW.get("start_at") if same else None)
    return opening_fire(key, song or title, "감시")


def _q_play_here() -> dict:
    """지금 자리의 곡을 튼다 (대기열은 그대로).

    차례: **(우리 곡이 울리고 있으면) 정지** → 인사말 → 행동 → 표정 → 시작 연출 → 연주.
    정지는 인사 스레드를 띄우기 **전에** 한다 (`_stop_for_switch`). 합주 중이면 정지를 건너뛴다.
    """
    with _Q_LOCK:
        items = _q_items()
        pos = _Q["pos"]
        it = items[pos] if 0 <= pos < len(items) else None
        gen = _Q["gen"]
    if it is None:
        return {"ok": False, "error": "empty_queue", "message": "재생할 곡이 없습니다."}
    # 탈것 위에서는 게임이 연주를 받지 않는다 — 정지·인사·연출 **전에** 본다 (설정이 켜져 있으면 제작으로 내린다)
    r = _mount_gate({"gen": gen, "pos": pos})
    if r is not None:
        out = dict(queue_state())
        out["play"] = {"ok": False, "error": r["error"], "message": r["message"], "gen": gen, "riding": True}
        return out
    _ENG["gap_until"] = 0.0
    stopped = _stop_for_switch()      # 곡을 바꾸면 옛 곡부터 끊는다
    _wake()          # 여기서부터 곡 경계를 초 단위로 봐야 한다
    lead = greet_lead_sec()
    if lead > 0:                      # (정지 →) 인사 → 행동 → 표정 → 시작 연출 → 연주
        _GREET["seq"] += 1
        tok = _GREET["seq"]
        _GREET["pending"] = {"until": time.time() + lead, "title": it["title"], "tok": tok,
                             "song": it.get("song") or it["title"]}
        okey = _opening_own_begin(it["title"], lead)
        threading.Thread(target=_greet_then_play, args=(it, lead, tok, gen, okey), daemon=True).start()
        out = dict(queue_state())
        out["play"] = {"ok": True, "pending": True, "lead": lead, "gen": gen, "stopped": stopped}
        return out
    # 대기 없이 바로 튼다 — 방금 끊었으면 play() 가 또 묻고 또 끊지 않게 알려 준다
    okey = _opening_own_begin(it["title"], 0.0)
    _ENG["starting"] = time.time()    # 트는 중 — work_now.busy 가 이 몇 초를 「끝났다」로 읽지 않게
    try:
        r = play(it["title"], it.get("inst") or None, it.get("key") or "", stopped=stopped,
                 resume={"gen": gen, "pos": pos})
    finally:
        _ENG["starting"] = 0.0
    _ENG["work_stop"] = 0.0           # 다시 튼다 — 「작업에 양보해 멈춤」 표시는 여기서 걷힌다
    _ENG["own"] = it["title"] if r.get("ok") else ""
    _solo_finish("stopped")           # 대기열이 주인이다 — 곡 하나 재생이 있었으면 그것은 끝났다
    if r.get("ok"):                   # 인사가 꺼져 있으면 연출은 **서버가 트는 순간** 한 번 (미니 창은 부르지 않는다)
        opening_fire(okey, it.get("song") or it["title"], "재생", inst=_queue_inst(it))
    _ENG["started"] = time.time()
    _ENG["last"] = (bool(r.get("ok")), 0.0, 0.0)
    _ENG["msg"] = "" if r.get("ok") else str(r.get("message") or r.get("error") or "")
    if r.get("ok"):
        _ENG["fail"] = 0
        _GREET["said_end"] = ""
        _ENG["ask_at"] = 0.0
        _NOW.update(playing=True, title=it["title"], el=0.0, tot=float(it.get("duration") or 0), at=time.time())
    else:
        _ENG["fail"] += 1
    out = dict(queue_state())
    out["play"] = {"ok": bool(r.get("ok")), "error": r.get("error"), "message": r.get("message"), "gen": gen}
    return out


def _greet_then_play(it: dict, lead: float, tok: int, gen: int, okey=None) -> None:
    """재생을 누른 뒤 실제 연주까지의 사이. 인사말 → 행동 → 표정 → 시작 연출 → 연주.

    연출은 **연주 시작에 딱 맞춰 끝나도록** 거꾸로 계산해 띄운다(= lead − 연출 길이 지점).
    도중에 다른 곡을 고르거나 정지하면 그 자리에서 접는다 (토큰과 대기열 세대로 판별).

    옛 곡은 이 스레드를 띄우기 **전에** `_q_play_here` 가 이미 끊었다 (합주 중이 아니면).
    그래서 끝의 `play()` 는 `stop_before_play` 여도 `IsPlaying` 을 한 번 보고, 거짓이면 또 끊지 않는다.
    `lead` 동안 누가 게임에서 다시 틀었으면 그때는 예전처럼 설정대로 끊는다.
    """
    t0 = time.time()

    def alive() -> bool:
        pend = _GREET.get("pending") or {}
        return pend.get("tok") == tok and _Q["gen"] == gen

    try:
        nxt = (queue_state().get("next") or {})
        greet_run("start", it, nxt, it.get("key") or "")
        if not alive():
            return
        wait = (t0 + lead - OPENING_SEC) - time.time()
        if wait > 0:
            time.sleep(wait)
        if not alive():
            return
        # 이 차례의 연출은 여기서 한 번 — 곡이 울린 뒤 감시가 또 불러도 같은 key 라 건너뛴다
        opening_fire(okey or _opening_own_begin(it["title"], lead),
                     it.get("song") or it.get("title") or "", "인사", inst=_queue_inst(it))
        wait = (t0 + lead) - time.time()
        if wait > 0:
            time.sleep(wait)
        if not alive():
            return
        _ENG["starting"] = time.time()
        try:
            r = play(it["title"], it.get("inst") or None, it.get("key") or "",
                     resume={"gen": gen, "pos": _Q["pos"]})
        finally:
            _ENG["starting"] = 0.0
        _ENG["work_stop"] = 0.0
        _ENG["own"] = it["title"] if r.get("ok") else ""
        _solo_finish("stopped")
        _ENG["started"] = time.time()
        _ENG["last"] = (bool(r.get("ok")), 0.0, 0.0)
        _ENG["msg"] = "" if r.get("ok") else str(r.get("message") or r.get("error") or "")
        if r.get("ok"):
            _ENG["fail"] = 0
            _GREET["said_end"] = ""
            _ENG["ask_at"] = 0.0
            _NOW.update(playing=True, title=it["title"], el=0.0,
                        tot=float(it.get("duration") or 0), at=time.time())
        else:
            _ENG["fail"] += 1
    finally:
        if (_GREET.get("pending") or {}).get("tok") == tok:
            _GREET["pending"] = None


def queue_step(dir_: int, from_engine: bool = False) -> dict:
    """이전·다음 — 대기열 차례대로. 사람이 누른 것은 한 곡 반복이어도 옮겨 간다."""
    s = store.get_settings()
    end = False
    with _Q_LOCK:                                  # 자물쇠를 쥔 채 queue_state() 를 부르면 스스로 막힌다
        n = len(_Q["order"])
        if not n:
            return {"ok": False, "error": "empty_queue", "message": "대기열이 비어 있습니다."}
        if from_engine and _Q["passes"] is not None:
            # 요청 단위 회차 제한 — repeat 설정을 보지 않는다. 끝에 닿으면 남은 회차가 있을 때만 처음으로 돈다
            j = _Q["pos"] + 1
            if j >= n:
                if _Q["pass"] >= _Q["passes"]:
                    end = True
                    _say(f"[queue] 「{_Q['name'] or 'ㅡ'}」 {_Q['passes']}회 연주 끝 — 멈춥니다 (이어서 틀지 않음)")
                else:
                    _Q["pass"] += 1
                    j = 0
                    _say(f"[queue] 「{_Q['name'] or 'ㅡ'}」 {_Q['pass']}/{_Q['passes']}회째 — 처음부터")
            if not end:
                _Q["pos"] = j
                _Q["rev"] += 1
        elif from_engine and s.get("repeat") == "one":
            pass                                   # 제자리 (곡이 끝나 다시 트는 경우)
        else:
            j = _Q["pos"] + (1 if dir_ >= 0 else -1)
            if j < 0 or j >= n:
                if s.get("repeat") != "all":
                    end = True
                else:
                    j %= n
            if not end:
                _Q["pos"] = j
                _Q["rev"] += 1
    if end:
        return {"ok": False, "error": "queue_end", "message": "대기열 끝입니다.", **queue_state()}
    return _q_play_here()


def queue_play_index(i: int) -> dict:
    """대기열의 i번째(재생 차례 기준)를 튼다 — 현재 재생목록에서 줄을 누른 경우."""
    with _Q_LOCK:
        if not (0 <= i < len(_Q["order"])):
            return {"ok": False, "error": "bad_index"}
        _Q["pos"] = i
        _Q["rev"] += 1
    return _q_play_here()


def queue_inst(key: str, inst: str) -> dict:
    """대기열의 한 곡에 악기를 붙인다 — 다음에 그 곡을 틀 때 `play()` 가 이 악기로 바꿔 든다.

    미니 화면의 「현재곡 칸 악기 고르기 = 이 곡에 저장」 이 부르는 길. 대기열은 서버가 하나만
    들고 있으므로(오버레이와 같은 것) 여기 적으면 두 화면이 같은 값을 본다.

    대기열이 재생목록에서 왔으면(`src` = `pl:<id>`) **그 재생목록 항목에도 같이 저장**한다 —
    대기열은 메모리뿐이라 다음에 같은 목록을 틀면 사라지기 때문이다. 어디까지 적었는지는
    `saved` 로 알려 준다 (`queue` | `playlist`). 빈 악기는 「기본」(설정의 기본 악기) 으로 되돌린다.
    """
    key, inst = str(key or ""), str(inst or "")
    if not key:
        return {"ok": False, "error": "bad_request", "message": "곡 key 가 없습니다."}
    with _Q_LOCK:
        if key not in _Q["keys"]:
            return {"ok": False, "error": "not_in_queue", "message": "대기열에 없는 곡입니다."}
        if inst.strip():
            _Q["inst"][key] = inst
        else:
            _Q["inst"].pop(key, None)
        _Q["rev"] += 1
        src = _Q["src"]
    saved = "queue"
    if src.startswith("pl:"):
        pl_id = src[3:]
        if any(pl.get("id") == pl_id for pl in store.get_lists()["playlists"]):
            lists_op("set_inst", {"id": pl_id, "key": key, "inst": inst})
            saved = "playlist"
    out = dict(queue_state())
    out["saved"] = saved
    return out


def queue_resync_inst() -> dict:
    """백업 복원 뒤: 대기열의 곡별 악기(`_Q["inst"]`)는 재생목록에서 **떠 온 사본**이라, 파일이
    바뀌어도 메모리는 옛 값 그대로다. 대기열이 재생목록에서 왔으면(`src` = `pl:<id>`) 저장된 목록의
    악기로 다시 맞춘다. 재생목록이 사라졌으면 그대로 둔다 (지금 도는 연주를 끊지 않는다)."""
    with _Q_LOCK:
        src, keys = _Q["src"], list(_Q["keys"])
    if not src.startswith("pl:") or not keys:
        return {"ok": True, "resynced": False}
    pl = next((x for x in store.get_lists()["playlists"] if x.get("id") == src[3:]), None)
    if not pl:
        return {"ok": True, "resynced": False, "reason": "playlist_gone"}
    insts = {it["key"]: str(it.get("inst") or "") for it in pl.get("items") or [] if it.get("key") in keys}
    with _Q_LOCK:
        _Q["inst"] = {k: v for k, v in insts.items() if v}
        _Q["rev"] += 1
    return {"ok": True, "resynced": True, "n": len(_Q["inst"])}


def _q_new_with(key: str) -> None:
    """빈 대기열을 곡 하나로 만든다 — 재생은 시작하지 않는다. `_Q_LOCK` 을 쥔 채 부른다."""
    _Q["keys"], _Q["order"], _Q["pos"] = [key], [0], 0
    _Q["src"], _Q["name"], _Q["inst"], _Q["req"] = "", "", {}, {}
    _Q["passes"], _Q["pass"] = None, 1
    _Q["gen"] += 1


def _q_drop_others(key: str) -> None:
    """대기열에서 `key` 의 줄을 걷는다 — **지금 곡 자리(pos)는 남긴다.** `_Q_LOCK` 을 쥔 채 부른다.

    `keys` 에서도 실제로 지우고 `order` 의 번호를 다시 매긴다 (셔플을 켜고 끄면 `keys` 로 차례를 다시 만들므로
    `order` 에서만 빼면 걷은 줄이 되살아난다)."""
    keys, order, pos = _Q["keys"], _Q["order"], _Q["pos"]
    drop = [j for j, i in enumerate(order) if j != pos and 0 <= i < len(keys) and keys[i] == key]
    if not drop:
        return
    gone = {order[j] for j in drop}
    new_order = [i for j, i in enumerate(order) if j not in drop]
    remap, new_keys = {}, []
    for i, k in enumerate(keys):
        if i in gone:
            continue
        remap[i] = len(new_keys)
        new_keys.append(k)
    _Q["keys"] = new_keys
    _Q["order"] = [remap[i] for i in new_order if i in remap]
    _Q["pos"] = pos - sum(1 for j in drop if j < pos) if pos >= 0 else pos


def queue_append(key, who: str = "", move: bool = False) -> dict:
    """곡 하나를 **현재 대기열 끝**에 담는다 (방송 신청 승인 · 줄 메뉴 「현재 재생목록 끝에 담기」).

    · 지금 곡은 끊지 않는다 — 차례(`order`)의 끝에 붙일 뿐이다. 셔플 중이어도 차례는 이미 섞인 순서이므로
      끝에 붙이면 곧 **남은 차례의 끝**이다.
    · 대기열이 비어 있으면 그 곡 하나로 대기열을 만들되 **재생은 시작하지 않는다** (`queue_set(play_now=False)` 와 같은 자리).
    · `who` 는 신청자 이름 — `_Q["req"]` 에 적어 두고 `/api/queue` 줄의 `req` 로 나간다. 화면 표시에만 쓴다.
    · `move=True`(줄 메뉴) 이면 이미 담긴 같은 곡을 **옮긴다** — 지금 곡이 아닌 줄은 걷고 끝에 하나만 둔다.
      지금 곡 자체는 걷지 않는다(재생 중인 자리라서) — 그래서 지금 곡을 담으면 끝에 한 번 더 붙는다.
      방송 신청(`move=False`)은 신청 하나가 한 번의 연주라 겹쳐도 그대로 붙인다.
    답: `{ok, pos}` — pos 는 화면 차례 기준 1부터."""
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "error": "bad_request", "message": "곡 key 가 없습니다."}
    who = str(who or "")[:40]
    with _Q_LOCK:
        if not _Q["keys"]:
            _q_new_with(key)
        else:
            if move:
                _q_drop_others(key)
            _Q["keys"].append(key)
            _Q["order"].append(len(_Q["keys"]) - 1)
        if who:
            _Q["req"][key] = who
        _Q["rev"] += 1
        pos = len(_Q["order"])
    return {"ok": True, "pos": pos}


def queue_insert_next(key) -> dict:
    """곡 하나를 **지금 곡 바로 다음 차례**에 끼운다 (줄 메뉴 「다음에 재생」). 지금 곡은 끊지 않는다.

    · 「다음」은 **재생 차례(`order`) 기준**이다 — 셔플 중이면 섞인 차례에서 지금 곡 바로 뒤.
    · 이미 담긴 같은 곡은 **옮긴다** (`queue_append(move=True)` 와 같은 규칙) — 지금 곡이 아닌 줄은 걷고
      다음 자리에 하나만 둔다. 지금 곡 자체를 고르면 다음 자리에 한 번 더 붙는다(한 번 더 듣기).
    · 대기열이 비어 있으면 그 곡 하나로 대기열을 만들되 **재생은 시작하지 않는다** (`queue_append` 와 같다).
    답: `{ok, pos}` — pos 는 화면 차례 기준 1부터."""
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "error": "bad_request", "message": "곡 key 가 없습니다."}
    with _Q_LOCK:
        if not _Q["keys"]:
            _q_new_with(key)
            at = 0
        else:
            _q_drop_others(key)
            _Q["keys"].append(key)
            at = _Q["pos"] + 1 if 0 <= _Q["pos"] < len(_Q["order"]) else 0
            _Q["order"].insert(at, len(_Q["keys"]) - 1)
        _Q["rev"] += 1
    return {"ok": True, "pos": at + 1}


def queue_clear() -> dict:
    with _Q_LOCK:
        _Q.update(keys=[], order=[], pos=-1, src="", name="", inst={}, passes=None, req={})
        _Q["pass"] = 1
        _Q["gen"] += 1
        _Q["rev"] += 1
    _ENG["own"] = ""
    _solo_finish("stopped")
    _ENG["wait_mount"] = None
    return queue_state()


def now_state() -> dict:
    """마지막으로 본 연주 상태. **CLI 를 쓰지 않는다** — 조회는 아래 감시 스레드가 혼자 한다."""
    d = dict(_NOW)
    d["queue"] = {"pos": _Q["pos"], "total": len(_Q["order"]), "src": _Q["src"], "name": _Q["name"],
                  "gen": _Q["gen"], "rev": _Q["rev"]}
    d["gap"] = max(0.0, _ENG["gap_until"] - time.time())
    d["msg"] = _ENG["msg"]
    d["work_stop"] = bool(_ENG.get("work_stop"))   # 큐에 양보했다가 그대로 멈춘 채 — 두 화면의 ■ 「작업에 양보해 멈춤」
    d["mounted"] = bool(_NOW.get("mounted"))
    d["wait_mount"] = bool(_ENG.get("wait_mount"))
    pend = _GREET.get("pending") or {}
    d["lead"] = max(0.0, float(pend.get("until") or 0) - time.time())   # 인사·연출 뒤 연주까지 남은 시간
    d["lead_title"] = pend.get("song") or ""
    d["ens"] = {"on": _ENS["on"], "n": _ENS["n"], "near": _ENS["near"],
                "members": _ENS["members"], "since": _ENS["since"]}
    ask = _ENG["ask_at"] and (time.time() - _ENG["ask_at"] < ASK_NEXT_SEC)
    d["ask_next"] = {"on": bool(ask), "n": _ENG["ask_n"] if ask else 0,
                     "left": round(max(0.0, ASK_NEXT_SEC - (time.time() - _ENG["ask_at"])), 1) if ask else 0.0}
    d["bc"] = _bc.ROOM.summary()                   # 방송 켜짐 · 대기 신청 수 (본창 머리줄 알약)
    return d


def _my_realm() -> str:
    """내 영지(서버) 이름. 자주 바뀌지 않으므로 10분에 한 번만 물어본다.
    **RealmName 은 닉네임이 아니라 영지다** — 화면에 사람 이름으로 쓰면 안 된다."""
    if _NEAR["realm"] and time.time() - _NEAR["realm_at"] < 600:
        return _NEAR["realm"]
    r = _cli("get_my_info", timeout=30)
    b = r.body if isinstance(r.body, dict) else {}
    v = b.get("RealmName")
    if isinstance(v, dict):
        v = v.get("Value")
    _NEAR.update(realm=_s(v) or _NEAR["realm"], realm_at=time.time())
    return _NEAR["realm"]


def _ens_step(now: float) -> None:
    """주변을 한 번 보고 **합주 판정과 주변 연주 화면을 같이** 만든다.

    언제 보나: 내가 연주 중이거나(1초 주기), 화면이 주변 연주를 보고 있을 때(2초 주기).
    아무도 안 보고 나도 안 치면 조회하지 않는다 — 조회는 213ms 쯤 걸리고 게임 CPU 를 조금 쓴다.

    묶는 법: **StartAt 이 같은 사람들이 한 합주단이다.** 게임은 「누가 누구와 합주 중인가」를
    알려 주지 않지만, StartAt 은 한 연주 안에서 변하지 않고 합주단 전원이 같은 값을 갖는다(실측).

    조회가 실패하면 **지난 판정을 그대로 둔다** — 합주 중이 아니라고 단정하면 남의 연주를 끊는다.
    """
    playing = bool(_NOW["playing"])
    watched = (now - _NEAR["want"]) < _NEAR_WANT
    if not playing and not watched:
        if _ENS["on"] or _ENS["n"]:
            _ENS.update(on=False, n=0, since=0.0, start_at=None, members=[])
        return
    if now - _NEAR["at"] < (_NEAR_PLAY if playing else _NEAR_IDLE):
        return
    _NEAR["at"] = now
    mine = _NOW.get("start_at") if playing else None
    r = _cli("get_near_pcs", timeout=30)
    rows = r.body if isinstance(r.body, list) else []
    if not r.ok:
        _NEAR.update(ok=False, error=_s(r.error), message=_s(r.message))
        return                                  # 못 물어봤다 — 지난 판정을 지키는 쪽이 안전하다
    groups, quiet, n_play = {}, 0, 0
    for x in rows:
        if not isinstance(x, dict):
            continue
        pf = x.get("Performance") if isinstance(x.get("Performance"), dict) else {}
        if not pf.get("IsPlaying"):
            quiet += 1
            continue
        n_play += 1
        sa = pf.get("StartAt")
        title = _s(pf.get("MusicTitle"))
        key = round(float(sa), 2) if isinstance(sa, (int, float)) else f"t:{_norm_title(title)}"
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"start_at": (float(sa) if isinstance(sa, (int, float)) else None),
                               "title": title, "song": _clean_title(title), "members": [],
                               "total": 0.0, "elapsed": 0.0, "remaining": None,
                               "loop": False, "copy": False, "mine": False, "distance": None}
        g["members"].append({"title": _s(x.get("Title")), "realm": _s(x.get("RealmName")),
                             "distance": x.get("Distance"), "channels": pf.get("ChannelCount") or 0})
        g["total"] = max(g["total"], float(pf.get("TotalDurationSeconds") or 0))
        g["elapsed"] = max(g["elapsed"], float(pf.get("ElapsedSeconds") or 0))
        rem = pf.get("RemainingSeconds")
        if isinstance(rem, (int, float)):
            g["remaining"] = rem if g["remaining"] is None else min(g["remaining"], float(rem))
        g["loop"] = g["loop"] or bool(pf.get("IsLoop"))
        g["copy"] = g["copy"] or bool(pf.get("IsCopyingAllowed"))
        d = x.get("Distance")
        if isinstance(d, (int, float)):
            g["distance"] = d if g["distance"] is None else min(g["distance"], float(d))
        if mine is not None and isinstance(sa, (int, float)) and abs(float(sa) - float(mine)) <= _NEAR_SAME:
            g["mine"] = True
    if playing and mine is not None:            # 내 연주도 한 자리 차지한다
        key = round(float(mine), 2)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"start_at": float(mine), "title": _NOW["title"],
                               "song": _clean_title(_NOW["title"]), "members": [],
                               "total": float(_NOW["tot"] or 0), "elapsed": float(_NOW["el"] or 0),
                               "remaining": None, "loop": bool(_NOW["loop"]), "copy": False,
                               "mine": True, "distance": 0}
        g["mine"] = True
        g["me"] = True
        g["distance"] = 0          # 내가 낀 합주단이다 — 「5m 떨어짐」으로 보이면 안 된다
    out = []
    for g in groups.values():
        g["size"] = len(g["members"]) + (1 if g.get("me") else 0)
        g["pct"] = (min(100.0, g["elapsed"] / g["total"] * 100) if g["total"] else 0.0)
        out.append(g)
    out.sort(key=lambda g: (not g.get("mine"), g["distance"] if g["distance"] is not None else 9999))
    members = next((g["members"] for g in out if g.get("me")), [])
    on = bool(members)
    was, was_n = _ENS["on"], int(_ENS["n"] or 0)   # 갱신 전 값 — 「몇 명이었는지」는 여기서만 알 수 있다
    _ENS.update(on=on, n=len(members), start_at=mine, members=members, near=n_play,
                since=(now if on and not was else (_ENS["since"] if on else 0.0)))
    _NEAR.update(ok=True, error="", message="", groups=out, playing=n_play, quiet=quiet,
                 rev=_NEAR["rev"] + 1, realm=_my_realm() if watched else _NEAR["realm"])
    if on and not was:
        _say(f"[합주] 나와 같은 연주에 {len(members)}명 — 자동 정지·자동 넘김을 하지 않습니다")
    elif was and not on:
        _say("[합주] 함께 치던 사람이 없습니다 — 평소대로 돌아갑니다")
        if _ENG["own"]:                 # 내가 아직 이 곡의 주인이면 다음 곡을 물어본다
            _ENG["ask_at"], _ENG["ask_n"] = now, max(1, was_n)
    _listen_step(now, out, mine)


def _listen_step(now: float, groups: list, mine) -> None:
    """**남의 연주가 끝나면** 「잘 들었습니다」 인사.

    게임은 「연주가 끝났다」를 알려 주지 않는다 — 아까 보이던 연주(StartAt)가 이번 조회에서
    사라지면 끝난 것으로 본다. 내 연주는 세지 않는다.
    도배 방지: 같은 연주는 한 번만, 그리고 설정한 최소 간격을 지킨다.
    """
    seen = _NEAR["seen"]
    live = {}
    for g in groups:
        sa = g.get("start_at")
        if sa is None or g.get("me"):
            continue
        live[round(float(sa), 2)] = {"song": g.get("song") or g.get("title") or "",
                                     "at": now, "size": g.get("size") or 1}
    s = store.get_settings()
    gap = float(s.get("greet_listen_gap") if s.get("greet_listen_gap") is not None else 30)
    ended = [(k, v) for k, v in seen.items() if k not in live]
    for k, v in ended:
        seen.pop(k, None)
        if not s.get("greet_on") or not s.get("greet_listen"):
            continue
        if now - float(_GREET.get("last_listen") or 0) < max(5.0, gap):
            continue
        if now - float(v.get("at") or 0) > 15.0:      # 한참 전에 놓친 것은 인사하지 않는다
            continue
        _GREET["last_listen"] = now
        _say(f"[인사] 주변 연주가 끝났습니다 — 들었을 때 인사를 보냅니다 ({v.get('song') or '미확인 곡'})")
        _greet_bg("listen", {"song": v.get("song") or "", "title": v.get("song") or ""})
    for k, v in live.items():
        if k in seen:
            seen[k]["at"] = now
        else:
            seen[k] = v
    if len(seen) > 64:                               # 오래된 것은 버린다
        for k in [k for k, v in seen.items() if now - float(v.get("at") or 0) > 300][:32]:
            seen.pop(k, None)


def near_state(want: bool = True) -> dict:
    """주변 연주 화면이 읽는 값. want=True 면 「보고 있다」고 알려 조회를 계속하게 한다."""
    if want:
        _NEAR["want"] = time.time()
    age = time.time() - _NEAR["at"] if _NEAR["at"] else None
    return {"ok": bool(_NEAR["ok"]), "error": _NEAR["error"], "message": _NEAR["message"],
            "groups": _NEAR["groups"], "playing": _NEAR["playing"], "quiet": _NEAR["quiet"],
            "realm": _NEAR["realm"], "rev": _NEAR["rev"],
            "age": round(age, 1) if age is not None else None}


def _engine_step(now: float) -> None:
    """곡이 끝났으면(또는 반복 설정이라 끝 무렵이면) 다음 곡으로. **여기 한 곳만** 넘긴다."""
    s = store.get_settings()
    repeat = s.get("repeat")
    # 「or 2」 로 기본값을 주면 **0 이 2로 둔갑한다** (0 도 falsy) — 곡 사이 대기 0초가 안 먹었다
    mv, gv = s.get("advance_margin"), s.get("gap_sec")
    # 「여유」는 이제 **끝에서 얼마나 앞당겨 끊을지**이고 기본 0 — 곡이 끝나는 시각에 맞춰 끊는다.
    # 예전엔 max(2.0, …) 로 2초를 강제했다.
    margin = max(0.0, float(mv if mv is not None else 0))
    # 조회는 1초 간격이라 「el >= tot」 를 그 순간에 보기 어렵다 — 끝나기 CUT_WINDOW 안에 들어오면 남은 시간을 재서
    # **정확히 그 시각에** 정지를 보낸다 (_cut_timer). 시계는 조회 시각 기준이라 ±조회 간격의 오차는 있다.
    gap = max(0.0, float(gv if gv is not None else 2))
    playing, el, tot = bool(_NOW["playing"]), float(_NOW["el"] or 0), float(_NOW["tot"] or 0)
    was, wel, wtot = _ENG["last"]
    _ENG["last"] = (playing, el, tot) if playing else (False, wel, wtot)
    solo = _ENG.get("solo")          # 곡 하나 재생(play_solo)도 **같은 경계**를 지난다 — 대기열 대신 _solo_next 가 받는다
    if _ENG["gap_until"] and now >= _ENG["gap_until"]:
        _ENG["gap_until"] = 0.0
        # **양보를 부탁받았으면 여기서 멈춰 선다.** 곡은 끝났고 다음 곡은 아직 안 튼
        # 자리 — 모비웍스가 몇 분짜리를 돌리든 상관없어지는 지점이다. 다음 곡은
        # `work_resume()` 이 올 때 튼다. (막는 곳은 **여기 하나**다. 곡이 저절로
        # 끝나든, 반복이라 우리가 끊든, 곡 하나 재생이든 대기열이든, 마지막엔 전부 이 줄을 지난다.)
        if _ENG["yield_at"]:
            _ENG["held"] = True
            return
        if solo:
            _solo_next()
            return
        queue_step(1, from_engine=True)
        return
    if _ENG["gap_until"] or not _ENG["own"] or (_Q["pos"] < 0 and not solo):
        return
    if now - _ENG["adv_at"] < 5.0 or now - _ENG["stop_at"] < 4.0:
        return
    mine = _norm_title(_ENG["own"]) == _norm_title(_NOW["title"] or _ENG["own"])
    if not mine:
        return
    # **합주 중에는 우리가 정지하지도, 다음 곡으로 넘기지도 않는다**.
    # 붙어 있는 사람들은 내가 멈춰도 계속 친다 — 우리가 임의로 끊으면 남의 연주를 망친다.
    if _ENS["on"]:
        return
    # 합주가 막 끝났다 — 사람이 「다음 곡」을 누르거나 물음이 만료될 때까지 기다린다
    if _ENG["ask_at"] and now - _ENG["ask_at"] < ASK_NEXT_SEC:
        return
    if playing:
        # 게임이 「반복 설정」이면 곡이 스스로 끝나지 않는다 — 끝 몇 초 전에 우리가 끊고 다음 곡으로.
        # 한 곡 반복이면 그대로 둔다 (게임이 알아서 계속 돌린다). **다만 회차 제한이 걸린 요청**(대기열 N회 ·
        # 곡 하나 N회)과 **양보를 부탁받은 곡 하나 재생**은 경계가 와야 하므로 끊는다 — 경계가 영영 안 오면
        # 「N회 뒤 멈춤」도 「이 곡 끝나면 양보」도 성립하지 않는다.
        # 끝 인사를 곡이 끝나기 n초 전에 하도록 해 둘 수 있다
        # 양수면 **곡이 끝나기 ev 초 전**에 미리 보낸다 (음수·0 은 끝난 뒤에 보낸다 — 아래에서)
        ev = s.get("greet_end_lead")
        ev = float(ev if ev is not None else 0)
        if ev > 0 and tot and el >= tot - ev and _ENG["own"] and _GREET.get("said_end") != _ENG["own"]:
            _GREET["said_end"] = _ENG["own"]
            _greet_bg("end", {"title": _ENG["own"], "song": _clean_title(_NOW["title"] or _ENG["own"])})
        cut = (bool(_ENG["yield_at"]) or solo.get("passes") is not None) if solo \
            else (repeat != "one" or _Q["passes"] is not None)
        if _NOW["loop"] and cut and tot and el >= tot - max(margin, CUT_WINDOW) and not _ENG.get("cut_at"):
            delay = max(0.0, (tot - el) - margin)          # 끝나는 시각(에서 margin 만큼 앞)까지 남은 시간
            _ENG["adv_at"] = now
            _ENG["cut_at"] = now + delay
            def _cut(exp=_ENG["cut_at"], g=gap):
                if _ENG.get("cut_at") != exp or _ENG["stop_at"] > exp - delay - 0.001:   # 그 사이 사람이 멈췄거나 곡이 바뀌었다
                    _ENG["cut_at"] = 0.0
                    return
                _ENG["cut_at"] = 0.0
                _NOW["playing"] = False
                _song_over(time.time(), g)
                _cli("stop_action", timeout=60)
            threading.Timer(delay, _cut).start() if delay > 0 else _cut()
        return
    if not was or _NOW["loop"]:
        return
    end_el = max(wel, el if tot == wtot else 0.0)
    if not wtot or end_el < wtot - 6:          # 한참 남았는데 멈췄다 = 사람이 멈춘 것
        return
    if _ENG["own"] and _GREET.get("said_end") != _ENG["own"]:      # 곡이 끝났다 — 끝 인사
        _GREET["said_end"] = _ENG["own"]
        ev2 = s.get("greet_end_lead")
        ev2 = float(ev2 if ev2 is not None else 0)
        # 음수면 **끝난 뒤 그만큼 기다렸다** 보낸다 (0 이면 바로). 양수는 위에서 이미 보냈다.
        _greet_bg("end", {"title": _ENG["own"], "song": _clean_title(_NOW["title"] or _ENG["own"])},
                  delay=(-ev2 if ev2 < 0 else 0.0))
    _song_over(now, gap)


CUT_WINDOW = 1.5   # 반복 설정에서 곡 끝을 잡는 창(초) — 조회 간격(1초)보다 조금 넓게. 정지는 이 창 안에서 남은 시간을 재서 끝에 맞춘다


def _song_over(now: float, gap: float) -> None:
    """곡 하나가 끝났다(저절로, 또는 반복이라 우리가 끊어서) — 틈(gap)을 잡는다. 곡 하나 재생이면 회차를 하나 센다.
    다음에 무엇을 할지는 틈이 끝나는 자리(`_engine_step` 첫 갈래)가 정한다: 양보 → 멈춰 섬 · 곡 하나 → `_solo_next` · 대기열 → 다음 곡."""
    _ENG["adv_at"] = now
    _ENG["gap_until"] = now + gap
    so = _ENG.get("solo")
    if so:
        so["done"] = int(so.get("done") or 0) + 1


def _solo_finish(how: str, msg: str = "") -> None:
    """곡 하나 재생(`_ENG.solo`)을 걷으면서 **어떻게 끝났는지** 남긴다 (`_ENG.solo_end` → `work_now().soloEnd`).
    모비웍스 「연주」 카드는 solo 가 사라진 것만으로는 「다 쳤다」와 「중간에 멈췄다」를 못 가른다 — 회차가 오르고 걷히는 사이가
    큐의 읽기 간격(2초)보다 짧을 수 있어서다. solo 를 None 으로 두는 자리는 **전부 여기**를 지난다."""
    so = _ENG.get("solo")
    if so:
        _ENG["solo_end"] = {"title": so.get("title") or "", "key": so.get("key") or "", "passes": so.get("passes"),
                            "done": int(so.get("done") or 0), "how": how, "msg": msg or "", "at": time.time()}
    _ENG["solo"] = None


def _solo_next() -> None:
    """곡 하나 재생의 경계 (틈이 끝난 자리, 양보 부탁이 없을 때). done < passes 면 **같은 곡을 다시**, 아니면 **멈춘다** —
    폴리오 대기열(`_Q`)로 이어 가지 않는다. `passes` 가 None(/api/play 로 튼 곡 하나)이면 한 번으로 끝이다."""
    so = _ENG.get("solo")
    if not so:
        return
    n, done = so.get("passes"), int(so.get("done") or 0)
    if n is None or done >= n:
        _say(f"[play] 「{_clean_title(so.get('title') or '')}」 {done}회 연주 끝 — 멈춥니다 (대기열로 이어 가지 않음)")
        _ENG["own"] = ""
        _solo_finish("done")
        _ENG["last"] = (False, 0.0, 0.0)
        return
    _say(f"[play] 「{_clean_title(so.get('title') or '')}」 {done + 1}/{n}회째")
    r = play(so["title"], so.get("inst") or None, so.get("key") or "", stopped=True,
             resume={"title": so["title"], "inst": so.get("inst"), "key": so.get("key") or "", "passes": n})
    if r.get("ok"):
        _solo_begin(so["title"], so.get("inst"), so.get("key") or "", n, done)
    else:
        _ENG["own"] = ""
        _ENG["msg"] = str(r.get("message") or r.get("error") or "")
        _solo_finish("error", _ENG["msg"])
        _ENG["fail"] += 1


def _solo_begin(title: str, inst, key: str, passes, done: int = 0) -> None:
    """곡 하나가 방금 **우리 손으로** 시작됐다 — 감시가 주인으로 들도록 적는다 (`_q_play_here` 가 대기열 곡에 하는 것과 같은 줄).
    `_NOW.playing` 을 미리 참으로 두는 까닭: 감시 틱이 오기 전에 큐가 `work_yield` 를 부르면 「지금 재생 중이 아닙니다」로
    바로 양보한 셈이 된다 (실측: 연주 카드 1초 뒤에 가공 카드가 물었다)."""
    now = time.time()
    _ENG["solo"] = {"title": title, "inst": inst or "", "key": key or "", "passes": passes, "done": int(done or 0)}
    _ENG["own"] = title
    _ENG["work_stop"] = 0.0
    _ENG["started"] = now
    _ENG["last"] = (True, 0.0, 0.0)
    _ENG["msg"] = ""
    _ENG["fail"] = 0
    _ENG["ask_at"] = 0.0
    _GREET["said_end"] = ""
    dur = store.get_durations().get(title)
    _NOW.update(playing=True, title=title, el=0.0, tot=float(dur or 0), at=now)
    _wake()


def play_solo(title: str, instrument: str | None, key: str = "", passes=None) -> dict:
    """곡 **하나**를 튼다 — `/api/play` 와 모비웍스 「연주」 카드 「이 곡만」의 길. `play()` 와 다른 점 하나: 성공하면 감시가
    이 곡의 **주인**이 된다 (`_ENG.solo`·`own`). 그래서
      · 곡이 끝나면(또는 게임 반복이라 끝 무렵에 우리가 끊으면) 대기열과 같은 경계를 지난다 — 「이 곡 끝나면 양보」가 여기서도 선다
      · 경계에서 **폴리오 대기열로 이어 가지 않는다** — `passes=N` 이면 같은 곡을 N번 치고 멈추고, None 이면 한 번으로 끝
      · 게임 「반복」이 켜져 있어도 `passes` 가 있거나 양보 부탁이 있으면 끝 무렵에 끊는다 (경계를 만든다).
        `passes=None`(폴리오 화면의 재생 버튼) 이고 부탁도 없으면 게임 반복은 그대로 둔다 — 그건 사람이 게임에서 고른 것이다
    탈것 위면 `riding` — 내린 뒤 `_mount_step` 이 같은 회차로 다시 부른다."""
    passes = max(1, min(SOLO_PASSES_MAX, int(passes))) if isinstance(passes, int) and not isinstance(passes, bool) else None
    r = play(title, instrument, key, resume={"title": title, "inst": instrument, "key": key or "", "passes": passes})
    if r.get("ok"):
        _solo_begin(title, instrument, key, passes, 0)
        if passes:
            _say(f"[play] 「{_clean_title(title)}」 {passes}회 치고 멈춥니다 (대기열로 이어 가지 않음)")
    return r


# ── 모비웍스 큐가 물어보는 자리 ────────────────────────────────────────
# 큐(`workqueue.py`)는 작업을 걸기 전에 **연주 쪽에 묻는다.** 게임 조회로는 알 수 없는
# 것이 둘 있기 때문이다: **합주 중인가**(게임이 안 알려 준다 — StartAt 으로 추론한다)와
# **인사말이 나가는 중인가**(「한 곡 들려드릴게요」를 말해 놓고 안 치면 곤란하다).
#
# **셋 다 CLI 를 부르지 않는다.** `_watch` 가 1초마다 재 둔 값만 본다 — 그래서
# 채집이 파이프를 몇 분씩 쥐고 있어도 이 물음은 **그 자리에서** 답한다.
def work_now() -> dict:
    """큐의 **연주 대기**(`workqueue._hold_for_music`)가 2초마다 읽는 「지금 연주의 모양」. **CLI 를 부르지 않는다** —
    `_watch` 가 재 둔 값만 본다 (연주 중엔 1초, 아니면 15초 간격이라 `age` 가 그만큼일 수 있다).

    돌려줄 것: `{ok, age, busy, kind, title, elapsed, left, endless, own, solo, soloEnd, error}`
      · `kind`    `music` = 우리 대기열(전체 재생) · `song` = 우리 한 곡(`/api/play`) · `game` = 우리 것이 아닌 연주
      · `busy`    아직 「연주가 가는 중」 — 치는 중이거나, 곡 사이 틈(gap)·인사말(lead)·탈것 대기·양보 정지(held)·트는 중(starting) 안.
                  큐는 `busy` 가 `HOLD_SETTLE` 동안 꺼져 있어야 시작한다 — **곡 사이 틈에 끼어들지 않으려고.**
                  전체 재생의 끝 = 마지막 곡이 끝나고 틈까지 지난 뒤 (`_engine_step` 이 `queue_end` 로 멈춘 자리).
      · `left`    `music` 일 때 이 곡 뒤에 남은 곡 수 (전체 반복이면 None)
      · `endless` 끝이 안 온다 — 대기열의 전체/한 곡 반복 설정, 또는 게임 「반복」인 한 곡·남의 연주
                  (`_engine_step` 은 대기열이 돌 때만 게임 반복을 끊는다). 큐는 그래도 기다리고 카드가 그렇게 말한다.
      · `own`     지금 연주(또는 방금 끝난 자리)가 **우리 것**인가 (`_ENG.own`)
      · `solo`    곡 하나 재생이 살아 있으면 `{title, key, passes, done}`, 아니면 None — 모비웍스 「연주」 카드(「이 곡만」)가
                  **자기 부탁이 끝났는지**를 이것으로 본다 (카드는 끝날 때까지 돈다)
      · `soloEnd` 마지막 곡 하나 재생이 어떻게 끝났나 (`_solo_finish`) — `{title, key, passes, done, how, msg, at}` · 없으면 None
      · `error`   마지막 재생 시도가 실패했으면 그 메시지 (`_ENG.fail > 0`), 아니면 ""."""
    s = store.get_settings()
    rep = str(s.get("repeat") or "off")
    now = time.time()
    playing = bool(_NOW.get("playing"))
    title = _s(_NOW.get("title"))
    own_t = _s(_ENG.get("own"))
    own = bool(own_t) and (not playing or _norm_title(own_t) == _norm_title(title or own_t))
    gap = max(0.0, float(_ENG.get("gap_until") or 0) - now)
    pend = _GREET.get("pending") or {}
    lead = max(0.0, float(pend.get("until") or 0) - now)
    with _Q_LOCK:
        pos, n = int(_Q["pos"]), len(_Q["order"])
        passes, pas = _Q["passes"], int(_Q["pass"] or 1)
    solo = _ENG.get("solo")
    queue = own and pos >= 0 and not solo
    kind = "music" if queue else ("song" if own else "game")
    if queue and passes is not None:       # 회차 제한 — 이 곡 뒤에 남은 곡 = 이번 회차의 나머지 + 남은 회차 전부
        left = max(0, n - pos - 1) + max(0, passes - pas) * n
    else:
        left = max(0, n - pos - 1) if (queue and rep != "all") else None
    if queue:
        endless = rep in ("all", "one") and passes is None
    elif solo:
        # 곡 하나 재생 — 회차가 있거나 양보 부탁이 있으면 게임 반복이어도 우리가 끊는다 (끝이 온다)
        endless = bool(playing and _NOW.get("loop")) and solo.get("passes") is None and not _ENG["yield_at"]
    else:
        endless = bool(playing and _NOW.get("loop"))
    starting = bool(_ENG.get("starting")) and now - float(_ENG.get("starting") or 0) < 120.0   # 트는 중 (play() 가 CLI 를 부르는 사이)
    busy = playing or gap > 0 or lead > 0 or bool(_ENG.get("wait_mount")) or bool(_ENG.get("held")) or starting
    el = float(_NOW.get("el") or 0)
    at = float(_NOW.get("at") or 0)
    if playing and at:
        el += max(0.0, now - at)          # 감시 틱 사이의 시간을 보탠다 — 카드의 경과가 1초씩 끊기지 않게
    so_end = _ENG.get("solo_end")
    return {"ok": bool(_NOW.get("ok")), "age": round(max(0.0, now - at), 1) if at else None,
            "busy": busy, "kind": kind, "title": _clean_title(title or (own_t if own else "")),
            "elapsed": round(el, 1), "left": left, "endless": endless,
            "own": own,
            "solo": ({"title": _clean_title(solo.get("title") or ""), "key": solo.get("key") or "",
                      "passes": solo.get("passes"), "done": int(solo.get("done") or 0)} if solo else None),
            "soloEnd": dict(so_end) if isinstance(so_end, dict) else None,
            "error": str(_ENG.get("msg") or "") if int(_ENG.get("fail") or 0) > 0 else ""}


def work_gate() -> dict:
    """「지금 연주를 끊어도 됩니까」 → `{may_stop, why, title, ends_in?}`.

    `ends_in` 은 **있을 때와 없을 때의 뜻이 다르다** (큐가 그렇게 읽는다):
      · 키가 없다      = **모른다.** 큐는 게임이 준 값으로 판단한다
      · 키가 있고 숫자 = 그만큼 뒤에 재생목록이 끝난다
      · 키가 있고 None = **끝나지 않는다** (반복 설정)
    「모른다」를 None 으로 적으면 **끝나지 않는다고 거짓말**이 된다. 그래서 나눈다."""
    ens_on = bool(_ENS["on"])
    pending = bool((_GREET.get("pending") or {}))
    why = ""
    if ens_on:
        n = int(_ENS.get("n") or 0)
        why = f"합주 중입니다{f' ({n}명)' if n else ''} — 내가 멈춰도 붙어 있는 사람들은 계속 칩니다"
    elif pending:
        why = "인사말이 나가는 중입니다 — 말해 놓고 안 치는 꼴이 됩니다"
    out = {"may_stop": not (ens_on or pending), "why": why,
           "title": _clean_title(_NOW.get("title") or _ENG.get("own") or ""),
           "ensemble": ens_on, "greeting": pending}
    known, secs = _ends_in()
    if known:
        out["ends_in"] = secs
    return out


def _ends_in():
    """재생목록이 **언제 끝나는가** → `(아는가, 초|None)`.

    셋을 가른다: **안다**(True, 초) / **끝나지 않는다**(True, None) / **모른다**(False, None).
    길이를 모르는 곡이 하나라도 남아 있으면 **모른다**로 답한다 — 빼고 더하면
    「30초 뒤에 끝납니다」라고 해 놓고 한 시간을 치게 된다."""
    s = store.get_settings()
    rep = str(s.get("repeat") or "off")
    solo = _ENG.get("solo")
    limited = _Q["passes"] is not None or bool(solo and solo.get("passes") is not None)   # 요청 단위 회차 제한 — 끝이 있다
    if rep in ("all", "one") and not limited and not solo:
        return True, None                       # 돌고 또 돈다
    if _NOW.get("loop") and not limited and not (solo and _ENG["yield_at"]):
        return True, None                       # 게임이 「반복」이다 (회차 제한·양보 부탁이 있으면 우리가 끊는다)
    gv = s.get("gap_sec")
    gap = max(0.0, float(gv if gv is not None else 2))
    left = 0.0
    if _NOW.get("playing"):
        tot, el = float(_NOW.get("tot") or 0), float(_NOW.get("el") or 0)
        if not tot:
            return False, None                  # 이 곡 길이조차 모른다
        left = max(0.0, tot - el)
    if solo:                                    # 곡 하나 — 남은 회차만큼 같은 곡이 더 온다 (대기열은 안 본다)
        n, done = solo.get("passes"), int(solo.get("done") or 0)
        more = max(0, (n or 1) - done - (1 if _NOW.get("playing") else 0))
        if more:
            tot = float(_NOW.get("tot") or 0)
            if not tot:
                return False, None
            left += more * (tot + gap)
        return True, left
    with _Q_LOCK:                               # 차례와 목록을 **같은 순간의 것**으로 읽는다
        pos = int(_Q["pos"])                    # (queue_state 도 이 안에서 _q_items 를 부른다)
        items = _q_items()
        rest = items[pos + 1:] if pos >= 0 else None
        if rest is not None and _Q["passes"] is not None:
            rest = rest + items * max(0, int(_Q["passes"]) - int(_Q["pass"] or 1))
    if rest is None:
        return True, left                       # 대기열이 안 돈다 — 이 곡이 끝이다
    for it in rest:
        d = it.get("duration")
        if not d:
            return False, None                  # 뒤에 길이 모르는 곡이 있다
        left += float(d) + gap
    return True, left


def work_yield() -> dict:
    """「이 곡 끝나면 양보해 주세요」 → `{ok, why, held}`. **곧바로 답한다.**

    멈추는 것이 아니라 **다음 곡을 안 트는 것**이다. 곡 경계는 `gap_sec`(기본 2초)뿐이라
    그 창에 맞춰 `stop_action` 을 쏘는 것은 경주다 — 늦으면 **다음 곡을 중간에 자른다.**
    그래서 미리 말해 두고 경계에서 멈춰 서게 한다.

    기다리는 것은 **부르는 쪽**이 한다 (`held` 가 참이 될 때까지). 여기서 기다리면
    감시 루프가 아니라 부르는 스레드가 묶이는데, 그쪽은 큐를 멈출 수 있는 곳이라
    거기서 기다리는 편이 끊을 수 있다."""
    s = store.get_settings()
    solo = _ENG.get("solo")
    if _NOW.get("loop") and str(s.get("repeat") or "off") == "one" and _Q["passes"] is None and not solo:
        # 게임이 한 곡을 무한 반복한다 — **경계가 영영 안 온다.** 아는 척하지 않는다.
        # (회차 제한이 걸린 대기열·곡 하나 재생은 예외 — 부탁이 있으면 끝 무렵에 우리가 끊어 경계를 만든다)
        return {"ok": False, "held": False,
                "why": "「한 곡 반복」이라 곡이 끝나지 않습니다 — 양보할 경계가 없습니다"}
    _ENG["yield_at"] = time.time()
    _ENG["work_stop"] = 0.0
    idle = not _NOW.get("playing") and not _ENG["gap_until"] and _Q["pos"] < 0 and not solo
    if idle:
        _ENG["held"] = True
        return {"ok": True, "held": True, "why": "지금 재생 중이 아닙니다"}
    return {"ok": True, "held": bool(_ENG["held"]),
            "why": "이 곡이 끝나면 다음 곡으로 넘어가지 않고 기다립니다"}


def work_resume() -> dict:
    """「일이 끝났습니다 — 이어서 트세요」.

    **안 돌려주면 음악이 영영 멈춰 있다.** 그래서 부르는 쪽은 실패해도 이걸 부른다."""
    was = bool(_ENG["held"])
    _ENG["yield_at"] = 0.0
    _ENG["held"] = False
    _ENG["work_stop"] = 0.0
    if was:
        gv = store.get_settings().get("gap_sec")
        _ENG["gap_until"] = time.time() + max(0.0, float(gv if gv is not None else 2))
        _wake()      # 곧 다음 곡을 튼다 — 15초 뒤에 깨어나면 그만큼 늦는다
    return {"ok": True, "resumed": was}


def work_release() -> dict:
    """「일이 끝났습니다 — 부탁을 거둡니다. **이어서 틀지는 마세요.**」 → `{ok, released, stopped}`.

    보드가 끝날 때 `work_resume` 대신 부른다 — 작업이 끝났다고 이전 연주 요청이 되살아나면 안 된다.

    남아 있던 것: `yield_at`(부탁받은 시각) 과 `held`(곡 경계에서 멈춰 섬). `work_resume` 은 그 둘을 지우면서
    `gap_until` 을 잡아 **다음 곡을 틀었다** — 그것이 「끝나면 다시 연주」였다. 여기서는
      · 둘을 지우고(부탁 없음 · 멈춰 선 것 없음)
      · 경계에서 멈춰 선 채였으면(`held`) 대기열을 **정지된 채**로 남긴다 — `own` 을 비워 감시(`_engine_step`)가
        「우리 곡이 끝났다 → 다음 곡」으로 읽지 않게 하고, `gap_until` 을 0 으로, `work_stop` 에 시각을 적어
        두 화면이 ■ 과 「작업에 양보해 멈춤」 을 그리게 한다. 자리(`pos`)는 그대로 — ▶ 는 사람이 누른다.
      · 경계에 서기 전이었으면(부탁만 있고 `held` 거짓 — 아직 치는 중) 부탁만 거둔다: 음악은 끊긴 적이 없으니
        그대로 이어진다 (`server._perf_yield` 가 정지·시한에서 거둘 때와 같은 뜻).
    CLI 를 부르지 않는다."""
    asked = bool(_ENG["yield_at"]) or bool(_ENG["held"])
    was = bool(_ENG["held"])
    solo = _ENG.get("solo")
    _ENG["yield_at"] = 0.0
    _ENG["held"] = False
    if was:
        _ENG["own"] = ""
        _ENG["gap_until"] = 0.0
        _ENG["ask_at"] = 0.0
        _ENG["last"] = (False, 0.0, 0.0)
        if solo:
            # 곡 하나 재생이 경계에서 양보한 채였다 — 남은 회차가 있어도 **이어서 치지 않는다** (같은 규칙: 이어서 틀지 않는다).
            # 대기열이 아니라 ■ 「작업에 양보해 멈춤」(work_stop) 도 적지 않는다 — ▶ 로 이을 자리가 없다
            _solo_finish("stopped")
            _say(f"[play] 작업이 끝났습니다 — 「{_clean_title(solo.get('title') or '')}」 은 곡 끝에서 양보한 채 멈춥니다 (남은 회차 없음)")
        else:
            _ENG["work_stop"] = time.time()
            with _Q_LOCK:
                _Q["rev"] += 1                    # 두 화면이 다시 읽게 (자리는 그대로)
            _say("[queue] 작업이 끝났습니다 — 양보 부탁을 거두고 멈춘 채로 둡니다 (▶ 로 이어서 재생)")
        _wake()
    return {"ok": True, "released": asked, "stopped": was}


def _norm_title(t: str) -> str:
    return _PFX.sub("", str(t or "")).strip().lower()


def _clean_title(t: str) -> str:
    """화면에 보일 제목 — 「악보: 」 접두어만 뗀다 (게임에 보내는 값은 늘 원본 그대로)."""
    return _PFX.sub("", str(t or "")).strip()


# **두드리는 주기.** 조회 한 번은 프로세스 하나다 — 공짜가 아니다.
#
# 합치기 전(모비폴리오)에는 쉴 때도 3초마다 두드렸다. 그 앱을 켠 사람은 음악을 쓰려고
# 켠 것이니 맞는 값이었다. **합친 뒤에는 음악을 한 번도 안 쓰는 사람에게도 그 두드림이
# 생긴다** — 그래서 늦춘다.
#
# 규칙은 「음악을 쓰나」가 아니라 **「지금 이 값을 볼 사람이 있나」**다. 그래야 12번
# (채집 퀘스트 지킴이)과 부딪히지 않는다 — 그쪽도 **제 때에만** 촘촘해지면 되고,
# 두 개의 타이머를 따로 두지 않는다. (미니 1초·오버레이 2초로 따로 두드리다 두 화면이
# 다른 값을 본 일이 인수인계 문서에 있다. 두드리는 곳은 **한 곳**이다.)
WATCH_BUSY = 1.0         # 연주 중·대기열이 도는 중 — 곡 경계를 초 단위로 봐야 한다
WATCH_IDLE = 15.0        # 아무도 안 본다
# **왜 쉴 때 15초여도 되는가**: 재생은 거의 언제나 **우리를 거쳐** 시작된다
# (`/api/play`·`/api/queue`). 그때 `_wake()` 가 잠을 깨우므로 그 자리에서 1초로 돌아온다.
# 놓치는 경우는 **사람이 게임 안에서 직접 연주를 시작한 때**뿐이고, 그러면 최대 15초 뒤에
# 알아차린다. 모르는 척하지 않고 적어 둔다.
_WAKE = threading.Event()


def _wake() -> None:
    """지금 상태가 바뀌었을 수 있다 — 자고 있으면 깨운다 (재생·정지 명령 뒤에 부른다)."""
    _WAKE.set()


# ── 탈것 탑승 중 (실측, docs/CLI.md §4) ──
# 게임은 탈것 위에서 연주를 받지 않는다 (play_music_score → not_available_on_riding) 그리고 CLI 에는
# 내리는 명령이 없다 (stop_action → invalid_state · stand_up → not_sitting · 행동·표정도 안 내린다).
# 그래서 **보내지 않고 알린 뒤, 사람이 내리면 이어서 튼다.** 탑승 여부는 감시가 매 틱 읽는
# get_activity 의 Mode.MountPartState ("Mounted" | "None") 다.
RIDING_CLI_ERROR = "not_available_on_riding"
RIDING_MSG = "탈것에서 내린 뒤 재생하세요 — 내리면 이어서 재생합니다"


# "Dismounted" 는 **내리는 중**이다 (제작으로 내릴 때 실측: Mounted → Dismounted 0.7~1.2초 → None 2.6~3.3초).
# 아직 다 내린 것이 아니므로 연주를 보내지 않는 쪽으로 친다.
MOUNT_STATES = ("Mounted", "Dismounted")


def _mount_state(body) -> str:
    mode = body.get("Mode") if isinstance(body, dict) else None
    return _s(mode.get("MountPartState")) if isinstance(mode, dict) else ""


def _mount_read(body) -> None:
    """get_activity 몸통에서 탑승 여부를 읽는다. Mode 가 없으면 거짓(안 탄 것)으로 본다."""
    _NOW["mounted"] = _mount_state(body) in MOUNT_STATES
    wm = _ENG.get("wait_mount")
    if _NOW["mounted"] and isinstance(wm, dict):
        wm["seen"] = True


def _riding(where: dict, seen: bool = True, msg: str = "") -> dict:
    """재생을 보내지 않는다 — 내린 뒤 이어서 틀 자리를 적고 안내를 돌려준다."""
    msg = msg or RIDING_MSG
    _ENG["wait_mount"] = dict(where, at=time.time(), seen=bool(seen))
    _ENG["msg"] = msg
    _ENG["own"] = ""
    _solo_finish("stopped", msg)
    _say("[queue] 탈것 탑승 중 — 내리면 이어서 재생합니다")
    return {"ok": False, "steps": [], "error": "riding", "message": msg}


def _mount_gate(where: dict) -> dict | None:
    """탑승 중이면 → None(이제 쳐도 된다) 또는 `riding` 안내.

    설정 `folio_dismount_by_craft` 가 켜져 있으면 먼저 제작으로 내려 본다 (`dismount_by_craft`).
    꺼져 있거나 못 내렸으면 보내지 않고 기다린다 (`_riding`)."""
    if not _NOW.get("mounted"):
        return None
    if store.get_settings().get(DISMOUNT_SETTING):
        d = dismount_by_craft()
        if d.get("ok"):
            return None
        return _riding(where, msg=_s(d.get("message")))
    return _riding(where)


# ── 탈것 내리기 — 바로 제작 하나를 걸었다가 곧바로 정지 (실측) ──
# 탄 채로 execute_crafting 을 보내면 게임이 **탈것에서 내린다** (Mounted → Dismounted → None).
# 정지 단추가 뜨자마자(Mode.MainButtonState == "Stop", 약 0.9초) stop_action 을 보내면 제작이 취소된다:
# result "stopped_by_user" · **재료는 안 쓰고** · 날개 5 는 접수 때 빠져 돌려받지 못한다 · 내린 채로 남는다.
# 설정으로 켜야만 돈다 (기본 꺼짐) — 날개가 든다.
DISMOUNT_SETTING = "folio_dismount_by_craft"
DISMOUNT_GUARD_SEC = 10.0     # 이 안에 두 번 돌지 않는다 (날개를 연달아 쓰지 않게)
DISMOUNT_STOP_AFTER = 2.5     # 정지 단추가 안 보여도 이만큼 지나면 정지를 보낸다
DISMOUNT_WAIT_NONE = 6.0      # 정지 뒤 MountPartState == "None" 을 기다리는 최대 시간
DISMOUNT_POLL = 0.3
DISMOUNT_NO_ITEM = "내릴 수 있는 바로 제작 품목이 없습니다 — 탈것에서 내린 뒤 재생하세요"
_DISMOUNT = {"at": 0.0, "busy": False, "last": None}
# 제작 스레드는 **진짜 스레드**여야 한다 — 제작이 파이프를 쥔 동안 곁에서 조회·정지를 보낸다.
_THREAD = threading.Thread


def _cli_side(command, body=None, timeout=30.0):
    """실행 명령(execute_crafting)이 파이프 잠금을 쥔 **동안 곁에서** 보내는 조회·정지.

    `_cli` 를 타면 제작이 끝날 때까지(8초 넘게) 잠금에 막혀 정지가 늦는다 — 모비웍스 큐의 정지
    (workqueue `stop`)와 같은 규칙으로 잠금을 거치지 않는다. 공용 파일(last-response)로 답을 메우지
    않는다 — 겹쳐 나가는 자리라 남의 답을 집어 올 수 있다 (못 읽으면 모른다로 둔다)."""
    return cli.call(command, body, timeout, allow_last_response=False)


def dismount_by_craft() -> dict:
    """탈것에서 내린다 — 바로 제작 품목 하나를 걸고 곧바로 정지. 날개 5 가 든다 (재료는 안 쓴다).

    돌려주는 값: {"ok": 내렸나, "error"?, "message"?, "item"?, "result"?}. 설정이 꺼져 있으면 돌지 않는다."""
    if not store.get_settings().get(DISMOUNT_SETTING):
        return {"ok": False, "error": "off", "message": RIDING_MSG}
    now = time.time()
    if _DISMOUNT["busy"] or now - _DISMOUNT["at"] < DISMOUNT_GUARD_SEC:
        _say("[queue] 탈것 내리기: 방금 했습니다 — 10초 안에는 다시 하지 않습니다")
        return {"ok": False, "error": "dismount_guard", "message": RIDING_MSG}
    _DISMOUNT.update(at=now, busy=True)
    try:
        return _dismount_run()
    finally:
        _DISMOUNT["busy"] = False


def _dismount_run() -> dict:
    r = _cli("get_craftable_items", timeout=60)
    _note(r, "탈것 내리기 — 제작 목록")
    b = r.body
    items = b.get("items") if isinstance(b, dict) else (b if isinstance(b, list) else [])
    name = ""
    for it in items if (r.ok and isinstance(items, list)) else []:   # 목록 순서대로 첫 「만들 수 있음」
        if isinstance(it, dict) and it.get("Craftable") is True and _s(it.get("DisplayName")).strip():
            name = _s(it.get("DisplayName"))
            break
    if not name:
        _say("[queue] 탈것 내리기: 만들 수 있는 바로 제작 품목이 없습니다")
        return {"ok": False, "error": "no_craftable", "message": DISMOUNT_NO_ITEM}
    _say(f"[queue] 탈것 내리기: 「{name}」 제작 걸고 정지 — 날개 5")
    box: dict = {}

    def craft() -> None:
        try:
            box["r"] = _cli("execute_crafting", {"displayName": name, "craftCount": 1}, timeout=120)
        except Exception as e:           # 스레드가 죽어도 아래 기다림은 끝나야 한다
            box["e"] = e

    th = _THREAD(target=craft, daemon=True)
    th.start()
    t0, stopped = time.time(), False
    while th.is_alive() and not stopped:
        time.sleep(DISMOUNT_POLL)
        if not th.is_alive():
            break
        a = _cli_side("get_activity", timeout=10)
        mode = (a.body or {}).get("Mode") if isinstance(a.body, dict) else None
        if (isinstance(mode, dict) and mode.get("MainButtonState") == "Stop") \
                or time.time() - t0 >= DISMOUNT_STOP_AFTER:
            s = _cli_side("stop_action", timeout=30)
            _note(s, "탈것 내리기 — 제작 정지")
            stopped = True
    th.join(130)
    rc = box.get("r")
    body = rc.body if (rc is not None and isinstance(rc.body, dict)) else {}
    result = _s(body.get("result"))
    if rc is not None:
        _note(rc, f"탈것 내리기 — 「{name}」 제작 (날개 5)")
    if result == "stopped_by_user":
        _say(f"[queue] 탈것 내리기: 「{name}」 제작을 멈췄습니다 — 재료는 그대로, 날개 5 소모")
    elif result == "completed":
        _say("[queue] 탈것 내리기: 정지가 늦어 제작이 끝났습니다 (재료 소모)")
    else:
        err = (rc.error or rc.message) if rc is not None else _s(box.get("e"))
        _say(f"[queue] 탈것 내리기: 제작이 걸리지 않았습니다 — {err}")
        return {"ok": False, "error": "dismount_failed", "item": name,
                "message": f"탈것에서 내리지 못했습니다 ({err}) — 탈것에서 내린 뒤 재생하세요"}
    t1 = time.time()
    while True:                          # 다 내릴 때까지 (Dismounted → None)
        a = _cli("get_activity", timeout=30)
        st = _mount_state(a.body) if a.ok else ""
        if a.ok and st not in MOUNT_STATES:
            _NOW["mounted"] = False
            _say("[queue] 탈것 내리기: 내렸습니다 — 재생합니다")
            return {"ok": True, "item": name, "result": result}
        if time.time() - t1 >= DISMOUNT_WAIT_NONE:
            break
        time.sleep(DISMOUNT_POLL)
    _say("[queue] 탈것 내리기: 6초 안에 다 내리지 못했습니다 — 내리면 이어서 재생합니다")
    return {"ok": False, "error": "still_mounted", "item": name, "result": result, "message": RIDING_MSG}


def _mount_step() -> dict | None:
    """감시 틱마다 — 미뤄 둔 재생이 있고 방금 내렸으면 **한 번** 튼다. 대기열이 바뀌었으면(세대) 버린다."""
    wm = _ENG.get("wait_mount")
    if not isinstance(wm, dict) or _NOW.get("mounted") or not wm.get("seen"):
        return None
    _ENG["wait_mount"] = None          # 한 번만 — 다시 막히면 _riding 이 새로 적는다
    _ENG["msg"] = ""
    if "gen" in wm:
        if wm.get("gen") != _Q["gen"]:
            _say("[queue] 탈것에서 내렸지만 대기열이 바뀌어 미뤄 둔 재생을 버립니다")
            return None
        with _Q_LOCK:
            pos = int(wm.get("pos", -1))
            if 0 <= pos < len(_Q["order"]) and pos != _Q["pos"]:
                _Q["pos"] = pos
                _Q["rev"] += 1
        _say("[queue] 탈것에서 내림 — 미뤄 둔 곡을 이어서 재생합니다")
        return _q_play_here()
    _say("[queue] 탈것에서 내림 — 미뤄 둔 곡을 재생합니다")
    return play_solo(_s(wm.get("title")), wm.get("inst") or None, _s(wm.get("key")), passes=wm.get("passes"))


def _watch_period() -> float:
    if _NOW["playing"] or _Q["pos"] >= 0 or _ENG["gap_until"] or _ENG["held"]:
        return WATCH_BUSY
    return WATCH_IDLE


def _watch() -> None:
    """연주 상태를 **한 곳에서만** 조회한다 (예전엔 미니 1초·오버레이 2초로 따로 두드렸다).
    두 화면은 /api/now 로 같은 값을 읽고, 다음 곡 넘김도 여기서만 한다."""
    while True:
        try:
            _WAKE.wait(_watch_period())
            _WAKE.clear()
            a = _activity()
            ok = bool(a.get("ok"))
            perf = ((a.get("body") or {}).get("Performance") or {}) if ok else {}
            if ok and isinstance(perf, dict):
                tot = float(perf.get("TotalDurationSeconds") or 0)
                el = float(perf.get("ElapsedSeconds") or 0)
                loop = bool(perf.get("IsLoop"))
                # 반복 연주는 경과가 곡 길이를 넘겨 계속 올라간다.
                # **주의**: 이 대비가 있다는 것이 「게임이 정말 그렇게 준다」의 증거는 아니다.
                # 가짜 CLI(MABI_DEMO_LOOP)도 이 줄을 보고 그렇게 굴도록 만든 것이라,
                # 둘이 서로를 근거로 삼고 있다 — 실기로 확인된 바 없다.
                if tot > 0 and el > tot:
                    el = el % tot if loop else tot
                sa = perf.get("StartAt")
                _NOW.update(playing=bool(perf.get("IsPlaying")), title=_s(perf.get("MusicTitle")),
                            el=el, tot=tot, loop=loop, inst=_s(perf.get("InstrumentName")),
                            at=time.time(), ok=True, error="", perf=perf,
                            start_at=(float(sa) if isinstance(sa, (int, float)) else None))
            else:
                _NOW.update(ok=False, error=_s(a.get("error")), at=time.time())
            if ok:
                _mount_read(a.get("body"))
            _mount_step()                     # 탈것에서 내렸으면 미뤄 둔 재생을 한 번 잇는다
            _ens_step(time.time())
            _game_card_step(time.time())     # 게임에서 시작한 연주(합주)의 카드 — 조회가 사람들을 잡은 뒤에
            _engine_step(time.time())
        except Exception as e:      # 감시가 죽으면 다음 곡이 영영 안 나온다 — 삼키고 계속 돈다
            try:
                _say(f"[queue] 감시 실패: {e}")
            except Exception:
                pass
            time.sleep(2.0)

# ── 자동 업데이트 (경량판) ──
def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _safe_url(u: str) -> bool:
    """https 만 허용. 루프백 http 는 로컬 시험용으로만 허용한다.

    호스트를 **파싱해서** 본다. 문자열 앞부분만 보면
    `http://127.0.0.1.evil.com` 과 `http://127.0.0.1@evil.com` 이 둘 다 통과한다 (실측).
    그러면 https 만 받겠다는 뜻이 무너진다."""
    try:
        p = urlparse((u or "").strip())
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    if p.scheme == "https":
        return bool(host)
    return p.scheme == "http" and host in ("127.0.0.1", "::1", "localhost")


def update_check() -> dict:
    """설정의 latest.json 을 읽어 새 버전이 있는지 본다. 보내는 것은 없다(사용자 정보 없음)."""
    if UI_META.get("update") is False:   # 이 화면 꾸러미는 자동 업데이트를 쓰지 않는다 (미니판: latest.json 이 경량판 exe 를 가리키므로)
        return {"ok": True, "available": False, "current": VERSION, "disabled": True}
    url = str(store.get_settings().get("update_url") or "").strip()
    if not _safe_url(url):
        return {"ok": False, "error": "no_url", "message": "업데이트 확인 주소(https)가 설정되지 않았습니다.", "current": VERSION}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"MobiFolio/{VERSION}", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read(65536).decode("utf-8-sig"))
    except Exception as e:
        return {"ok": False, "error": "fetch_failed", "message": f"업데이트 정보를 받지 못했습니다: {type(e).__name__}", "current": VERSION}
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad_manifest", "message": "latest.json 형식이 잘못되었습니다.", "current": VERSION}
    latest = str(data.get("version") or "")
    dl = str(data.get("url") or "")
    sha = str(data.get("sha256") or "").lower()
    ok_manifest = bool(latest) and _safe_url(dl) and re.fullmatch(r"[0-9a-f]{64}", sha or "") is not None
    return {"ok": True, "current": VERSION, "latest": latest, "available": ok_manifest and _vtuple(latest) > _vtuple(VERSION),
            "url": dl, "sha256": sha, "notes": str(data.get("notes") or "")[:2000], "lite": LITE, "manifest_ok": ok_manifest}


def update_apply(info: dict) -> dict:
    """새 exe 를 받아 SHA256 을 검증하고 제자리에 바꿔 넣은 뒤, 같은 포트·토큰으로 새 프로세스를 띄우고 자신은 끝난다 (경량판만).
    Windows 는 실행 중인 exe 의 '이름 바꾸기'를 허용하므로 외부 스크립트 없이 된다: 현재 exe → .bak, 새 파일 → 현재 이름.
    열려 있는 창의 페이지는 새 포트로 이동하므로 창이 닫혔다 열리지 않는다. .bak 은 새 프로세스가 지운다."""
    if not LITE or not FROZEN:   # 임베디드 판은 exe 를 바꿔 끼우지 않는다 (모비웍스 server.update_apply 가 받는 곳만 연다)
        return {"ok": False, "error": "not_lite", "message": "자동 업데이트는 단일 실행파일 판에서만 지원합니다 (정식판은 설치기로 받으세요)."}
    dl, sha, latest = str(info.get("url") or ""), str(info.get("sha256") or "").lower(), str(info.get("latest") or "")
    if not _safe_url(dl) or not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return {"ok": False, "error": "bad_manifest", "message": "다운로드 주소나 SHA256 이 없습니다."}
    os.makedirs(UPDATE_DIR, exist_ok=True)
    part = os.path.join(UPDATE_DIR, "MobiFolio.download.part")
    h = hashlib.sha256(); size = 0
    try:
        req = urllib.request.Request(dl, headers={"User-Agent": f"MobiFolio/{VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPDATE_BYTES:
                    raise ValueError("too large")
                h.update(chunk); f.write(chunk)
    except Exception as e:
        try:
            os.remove(part)
        except OSError:
            pass
        return {"ok": False, "error": "download_failed", "message": f"다운로드 실패: {type(e).__name__}"}
    if h.hexdigest() != sha:
        os.remove(part)
        return {"ok": False, "error": "sha_mismatch", "message": "받은 파일의 SHA256 이 latest.json 과 다릅니다. 적용하지 않았습니다."}
    cur = sys.executable   # onefile: 부모(부트로더) exe 경로 = 실제 배포 파일
    bak = cur + ".bak"
    try:
        try:
            os.remove(bak)
        except OSError:
            pass
        os.rename(cur, bak)          # 실행 중이어도 이름 바꾸기는 된다
        try:
            os.replace(part, cur)
        except OSError:
            os.rename(bak, cur); raise
    except OSError as e:
        return {"ok": False, "error": "replace_failed", "message": f"파일을 바꾸지 못했습니다: {e}"}
    # 새 프로세스: 같은 포트를 물려주면 페이지가 그대로 이동할 수 있다 — 이 프로세스가 포트를 놓아야 하므로 새 포트를 준다
    new_port = _free_port()
    # 옛 부트로더(부모)는 자식이 끝난 뒤에도 남는 경우가 있어(실측), 새 인스턴스가 그 pid 를 넘겨받아 정리한다 (+ 옛 임시 폴더)
    # PyInstaller 부트로더가 자식에게 주는 내부 변수(_PYI_*, _MEIPASS2)를 물려주면 새 exe 가 '이미 풀린 임시 폴더'를 쓰는
    # 자식 모드로 떠서(우리 옛 임시 폴더 → 곧 삭제됨) 화면 파일을 잃는다 — 반드시 걷어내고 띄운다
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env.update(MABI_PLAYLIST_PORT=str(new_port), MABI_TOKEN=TOKEN, MABI_LITE_REUSE="1",
               MABI_OLD_PID=str(os.getppid()), MABI_OLD_MEI=getattr(sys, "_MEIPASS", ""))
    env.pop("MABI_NO_BROWSER", None)
    _lite_release()   # 단일 인스턴스 뮤텍스·lite.json 을 먼저 놓는다
    try:
        import subprocess
        # PyInstaller 부트로더는 자식을 Job 객체에 넣고 그 안의 프로세스가 다 끝날 때까지 기다린다 — 새 인스턴스는 Job 에서 떼어 띄운다
        flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED | NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        try:
            subprocess.Popen([cur], cwd=os.path.dirname(cur), env=env, close_fds=True, creationflags=flags)
        except OSError:
            subprocess.Popen([cur], cwd=os.path.dirname(cur), env=env, close_fds=True, creationflags=flags & ~0x01000000)
    except OSError as e:
        return {"ok": False, "error": "spawn_failed", "message": f"새 버전을 실행하지 못했습니다: {e}"}
    _say(f"update: {VERSION} -> {latest}, new instance on port {new_port}")
    threading.Timer(2.0, _shutdown).start()
    return {"ok": True, "message": f"{latest} 로 업데이트합니다.", "restart": True, "port": new_port}


def _cleanup_bak() -> None:
    """업데이트 뒤처리: 옛 부트로더가 남아 있으면 끝내고(우리 프로세스), 옛 임시 폴더와 <exe>.bak 을 지운다."""
    bak = sys.executable + ".bak"
    old_pid = os.environ.get("MABI_OLD_PID", "")
    old_mei = os.environ.get("MABI_OLD_MEI", "")
    if not FROZEN or not (os.path.exists(bak) or old_pid):
        return
    def go():
        if old_pid.isdigit():
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.OpenProcess(0x00100000 | 0x0001, False, int(old_pid))   # SYNCHRONIZE | TERMINATE
                if h:
                    if k32.WaitForSingleObject(h, 15000) != 0:   # 15초 안에 스스로 안 끝나면
                        k32.TerminateProcess(h, 0); _say(f"update: old bootloader {old_pid} did not exit — terminated")
                    k32.CloseHandle(h)
            except Exception as e:
                _say(f"update: old process check failed: {e}")
        if old_mei and os.path.isdir(old_mei) and os.path.basename(old_mei).startswith("_MEI"):
            import shutil
            shutil.rmtree(old_mei, ignore_errors=True)
        err = None
        for _ in range(30):
            if not os.path.exists(bak):
                return
            try:
                os.remove(bak); _say("update: removed old .bak"); return
            except OSError as e:
                err = e; time.sleep(1.0)
        _say(f"update: could not remove .bak after 30s: {err}")
    threading.Thread(target=go, daemon=True).start()


def _mark_equipped(name: str) -> None:
    """change_instrument 성공 후 instruments 캐시의 IsEquipped 를 앱이 아는 대로 맞춘다 (fetched_at 은 유지)."""
    try:
        with store.LOCK:
            c = store.folio_cache("instruments")
            changed = False
            for x in c["items"]:
                if isinstance(x, dict):
                    want = (lib.pick(x, lib.NAME_KEYS).strip() == name.strip())
                    if bool(x.get("IsEquipped")) != want:
                        x["IsEquipped"] = want; changed = True
            if changed:
                store.save("instruments.json", c)
    except Exception as e:
        print(f"[play] 장착 표시 갱신 실패: {e}", flush=True)


def equip(name: str) -> dict:
    """연주하지 않고 악기만 바꿔 든다. play() 의 악기 규칙과 같다 (API·오버레이가 같이 쓴다).
    이름 양끝 공백은 일부러 살린다 — 그게 이름의 일부인 악기가 있다."""
    if not name.strip():
        return {"ok": False, "error": "empty", "message": "악기 이름이 없습니다."}
    insts = [lib.pick(x, lib.NAME_KEYS) for x in store.folio_cache("instruments")["items"] if isinstance(x, dict)]
    if insts and name not in insts and name.strip() not in [x.strip() for x in insts]:
        return {"ok": False, "error": "not_found", "message": f"보유하지 않은 악기입니다: {name}"}
    # 이름 끝에 공백이 있는 악기는 CLI 가 못 찾으므로, 이미 장착 중이면 부르지 않는다
    broken = name != name.strip() or name not in insts
    equipped = next((lib.pick(x, lib.NAME_KEYS) for x in store.folio_cache("instruments")["items"]
                     if isinstance(x, dict) and x.get("IsEquipped")), "")
    if broken and equipped and equipped.strip() == name.strip():
        return {"ok": True, "skipped": True, "message": "이미 장착 중입니다.", "instrument": name}
    r = _cli("change_instrument", {"name": name}, timeout=120)
    _note(r, f"악기 → {name}")
    if not r.ok and r.error == "not_found" and name != name.strip():   # 공백 뗀 이름으로 한 번 더
        r = _cli("change_instrument", {"name": name.strip()}, timeout=120)
        _note(r, f"악기 → {name.strip()} (공백 제거 재시도)")
    if r.ok:
        _mark_equipped(name)
    return {"ok": r.ok, "error": r.error, "message": r.message, "instrument": name}


def play(title: str, instrument: str | None, key: str = "", stopped: bool = False,
         resume: dict | None = None) -> dict:
    """(설정) 연주 중이면 먼저 정지 → (악기 지정 시) change_instrument → play_music_score.

    `stopped=True` 는 「부른 쪽이 **방금** 끊었다」(`_q_play_here` → `_stop_for_switch`, 대기 없이
    바로 트는 경우) — 그러면 `stop_before_play` 의 조회·정지를 건너뛴다 (같은 연주를 두 번 끊지 않는다).

    탈것 탑승 중이면 CLI 를 부르지 않고 `riding` 으로 돌려준다 (`_riding`). `resume` 은 내린 뒤 이어서 틀
    자리 — 대기열이 부르면 `{"gen", "pos"}`, 없으면(곡 하나 재생) 이 제목·악기 그대로 다시 튼다."""
    out = {"ok": True, "steps": []}
    if not (title or "").strip():
        return {"ok": False, "steps": [], "error": "empty_title", "message": "재생할 악보 제목이 비어 있습니다."}
    if _NOW.get("mounted"):
        r0 = _mount_gate(resume or {"title": title, "inst": instrument, "key": key or ""})
        if r0 is not None:
            return r0
    s = store.get_settings()
    inst = instrument if isinstance(instrument, str) and instrument.strip() else (s.get("default_inst") or None)
    # 악기 고정 — 곡에 적은 악기·기본 악기와 **상관없이** 고정한 악기로 튼다.
    # 바꿔 드는 명령은 아래 한 번 그대로다 (고정이라고 따로 더 부르지 않는다).
    pin = s.get("inst_pin")
    if isinstance(pin, str) and pin.strip():
        inst = pin
    # 없는 악보/악기는 현재 연주를 끊기 전에 걸러낸다 (캐시가 있을 때만 검사)
    cache = [lib.pick(x, lib.TITLE_KEYS) for x in store.folio_cache("scores")["items"] if isinstance(x, dict)]
    if cache and title not in cache:
        return {"ok": False, "steps": [], "error": "not_found", "message": "보관함에 없는 악보입니다. 갱신 후 다시 시도하세요."}
    if title != title.strip():   # 실측: 제목 끝에 공백이 있으면 CLI 가 어떤 표기로도 못 찾는다 → 현재 연주를 끊기 전에 알린다
        return {"ok": False, "steps": [], "error": "cli_title_name",
                "message": "게임 CLI 가 제목 끝에 공백이 있는 악보를 찾지 못합니다 (CLI 쪽 문제). 게임에서 악보 이름의 끝 공백을 지운 뒤 갱신하면 재생됩니다."}
    insts = [lib.pick(x, lib.NAME_KEYS) for x in store.folio_cache("instruments")["items"] if isinstance(x, dict)]
    if inst and insts and inst not in insts and inst.strip() not in [x.strip() for x in insts]:
        return {"ok": False, "steps": [], "error": "not_found", "message": f"보유하지 않은 악기입니다: {inst}"}
    if s.get("stop_before_play") and not stopped:
        a = _cli("get_activity", timeout=30)
        perf = (a.body or {}).get("Performance") if isinstance(a.body, dict) else None
        if isinstance(perf, dict) and perf.get("IsPlaying"):
            r0 = _cli("stop_action", timeout=60)
            out["steps"].append(r0.to_dict()); _note(r0, "현재 연주 정지")
            if r0.error == "invalid_state":
                time.sleep(0.8)
    if inst:
        # 이름 양끝에 공백이 있는 악기는 CLI 가 어떤 표기로도 못 찾는다(실측) — 이런 악기만 '이미 장착 중'이면 변경을 건너뛴다.
        # 정상 이름은 항상 CLI 에 맡긴다 (CLI 가 "Already equipped." 로 즉시 답하고, 캐시는 마지막 갱신 시점이라 믿을 수 없다).
        broken = inst != inst.strip() or inst not in insts
        equipped = next((lib.pick(x, lib.NAME_KEYS) for x in store.folio_cache("instruments")["items"] if isinstance(x, dict) and x.get("IsEquipped")), "")
        if broken and equipped and equipped.strip() == inst.strip():
            out["steps"].append({"command": "change_instrument", "ok": True, "skipped": True, "message": "이미 장착 중 (마지막 갱신 기준)"})
        else:
            r = _cli("change_instrument", {"name": inst}, timeout=120)
            out["steps"].append(r.to_dict()); _note(r, f"악기 → {inst}")
            if not r.ok and r.error == "not_found" and inst != inst.strip():   # 공백 뗀 이름으로 한 번 더 (CLI 가 고쳐질 때를 대비)
                r = _cli("change_instrument", {"name": inst.strip()}, timeout=120)
                out["steps"].append(r.to_dict()); _note(r, f"악기 → {inst.strip()} (공백 제거 재시도)")
            if not r.ok:
                out["ok"] = False
                if r.error == "not_found" and broken:
                    out["error"] = "cli_instrument_name"
                    out["message"] = "게임 CLI 가 이 악기를 찾지 못합니다 (이름 끝 공백 때문 — CLI 쪽 문제). 게임에서 직접 장착한 뒤 악기를 「악기 그대로」로 두고 재생하세요."
                return out
            _mark_equipped(inst)   # 성공했으면 캐시의 장착 표시도 맞춘다 (다음 갱신 전까지 UI 「장착」 배지·건너뛰기 판정에 쓰임)
    for attempt in range(4):   # 정지 직후 상태 전이 중이면 invalid_state → 짧게 재시도 (다음 곡으로 건너뛰지 않게)
        r = _cli("play_music_score", {"title": title}, timeout=60)   # 즉시 반환 명령: 잠금을 오래 잡지 않게
        out["steps"].append(r.to_dict()); _note(r, f"재생 · {title}" + (f" (재시도 {attempt})" if attempt else ""))
        if r.ok or r.error != "invalid_state":
            break
        time.sleep(0.8 + 0.4 * attempt)
    out["ok"] = r.ok
    if not r.ok and r.error == RIDING_CLI_ERROR:     # 감시가 탑승을 아직 못 봤다 — 같은 안내 · 내리면 이어서
        out.update(_riding(resume or {"title": title, "inst": instrument, "key": key or ""},
                           seen=bool(_NOW.get("mounted"))), steps=out["steps"])
    if r.ok:
        _ENG["wait_mount"] = None
        _last_play.update({"title": title, "inst": inst or ""})
        store.push_recent(title, inst or "", key or "")
        try:
            store.songs_played(key or "", title)     # 곡 번호 DB 의 연주 횟수 (우리가 시작한 연주만)
        except Exception as e:
            _say(f"[songs] 연주 횟수를 적지 못했습니다: {type(e).__name__}: {e}")
    _wake()          # 쉬는 주기(15초)로 자고 있었으면 지금 깨운다
    return out


def stop() -> dict:
    """연주 정지. get_activity.Performance.IsPlaying 이 false 면 정지할 게 없으니 성공으로 본다(invalid_state 재시도 낭비 방지).
    연주 중인데 invalid_state 가 오면 상태 전이 중이라 짧게 재시도."""
    out = {"ok": False, "steps": []}
    _ENG["wait_mount"] = None         # 사람이 멈췄다 — 내린 뒤 이어서 틀 것도 없앤다
    _ENG["work_stop"] = 0.0
    if _ENG.get("solo"):              # 곡 하나 재생을 사람이 멈췄다 — 남은 회차도 없앤다 (대기열 주인 표시는 그대로 둔다)
        _solo_finish("stopped")
        _ENG["own"] = ""
    a = _cli("get_activity", timeout=30)
    out["steps"].append(a.to_dict())
    perf = (a.body or {}).get("Performance") if isinstance(a.body, dict) else None
    if isinstance(perf, dict) and not perf.get("IsPlaying"):
        out["ok"] = True
        out["message"] = "재생 중인 곡이 없습니다."
        return out
    for attempt in range(4):
        r = _cli("stop_action", timeout=60)
        out["steps"].append(r.to_dict()); _note(r, "정지")
        if r.ok:
            out["ok"] = True
            _wake()      # 멈췄다 — 감시가 그걸 바로 보게 한다
            return out
        if r.error != "invalid_state":
            return out
        time.sleep(1.0 + attempt * 0.5)
    return out


class H(SimpleHTTPRequestHandler):
    timeout = 30   # 느린/멈춘 클라이언트가 스레드를 무한 점유하지 않게

    def __init__(self, *a, **k):
        super().__init__(*a, directory=UI_DIR, **k)

    def log_message(self, fmt, *args):   # 조용히
        pass

    def do_OPTIONS(self):
        """브라우저가 먼저 물어보는 예비요청. 허락한 사이트에만 답한다.

        폰 앱은 Content-Type·X-Requested-With·X-MobiFolio-Remote 를 붙이므로 **반드시** 이 단계를
        거친다. 여기서 답이 없으면 본 요청은 보내지지도 않는다.
        """
        org = _cors_origin(self)
        if not org:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", org)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Requested-With, X-MobiFolio-Token, X-MobiFolio-Remote")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # 열쇠 없이 답해도 되는 읽기 길. **"여기가 모비폴리오다"** 밖에 안 알려 준다
    # (밖에서 부르면 그마저도 app 하나로 줄여서 답한다).
    GET_OPEN = {"/api/health"}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if self._is_remote() and not _hosted_pass(self) and (u.path in REMOTE_NEVER
                                  or u.path.startswith(REMOTE_NEVER_PREFIX)):   # 이 PC 전용 길은 읽기도 막는다
            return _json(self, {"ok": False, "error": "forbidden"}, 403)
        # 읽기도 문을 지난다. 길마다 손으로 부르던 때는 여섯 개를 빠뜨렸고, 그 길로
        # 악보함·재생목록이 **열쇠 없이** 밖으로 나갔다. 문은 한 곳에만 둔다.
        if u.path.startswith("/api/") and u.path not in self.GET_OPEN and not self._guard():
            return _json(self, {"ok": False, "error": "forbidden"}, 403)
        if u.path == "/api/health":   # 일렉트론 셸이 "이 포트가 정말 모비폴리오인지" 확인하는 용도
            app, ver = _host_ident()     # 불러 쓰일 때는 모비웍스의 이름·버전
            if self._is_remote():        # 밖에서는 "여기 맞다" 만 — 버전·pid 는 알려 줄 이유가 없다
                return _json(self, {"app": app})
            return _json(self, {"app": app, "version": ver, "pid": os.getpid(), "frozen": FROZEN, "lite": LITE, "ui": UI_NAME or "full",
                                "parent": int(PARENT) if PARENT.isdigit() else None})
        if u.path == "/api/state":
            sc, ins = store.folio_cache("scores"), store.folio_cache("instruments")
            # **폰(밖)은 PC 가 읽어 둔 값만 본다** — 밖과 `nocli=1` 은 `status` 를 실행하지 않고
            # 이 PC 의 화면이 마지막으로 읽어 둔 값(`cli.last_probe`)을 낸다. 모비웍스 `/api/state` 와 같은 규칙.
            remote = self._is_remote()
            return _json(self, {"cli": cli.last_probe() if (remote or q.get("nocli")) else _probe(),
                                "cached": bool(remote or q.get("nocli")),
                                "scores": {"fetched_at": sc["fetched_at"], "count": len(sc["items"])},
                                "instruments": {"fetched_at": ins["fetched_at"], "count": len(ins["items"])}})
        if u.path == "/api/scores":
            items = [dict(it) for it in _build_items()]
            dur = store.get_durations(); rec = {(x.get("key") or x["title"]): x["ts"] for x in store.get_recent()}
            ens = store.get_ens()
            for it in items:
                it["duration"] = dur.get(it["title"])
                it["ens"] = ens.get(it["key"])
                it["lastPlayed"] = rec.get(it["key"]) or (rec.get(it["title"]) if it["dupNo"] == 1 else None)   # 채번 전 기록('제목')은 첫 악보에
            found = lib.search(items, q.get("q", ""))
            if q.get("view") == "solo":            # 합주 인원 1 로 지정한 곡
                found = [it for it in found if it.get("ens") == 1]
            if q.get("view") == "ens":             # 2인 이상으로 지정한 곡
                found = [it for it in found if (it.get("ens") or 0) >= 2]
            if q.get("view") == "recent":
                found = sorted([it for it in found if it["lastPlayed"]], key=lambda it: -it["lastPlayed"])
            if q.get("initial"):
                found = [it for it in found if it["initial"] == q["initial"]]
            if q.get("artist"):                       # artistKey (수동 id 또는 'auto:…')
                found = [it for it in found if it["artistKey"] == q["artist"]]
            if q.get("bucket"):                       # 'other' = 미분류
                found = [it for it in found if it["bucket"] == q["bucket"]]
            _cover_fields(found)
            # randomCover — 보관함에 없는 곡(남이 튼 합주곡)의 카드에 쓸 기본 풀의 한 장, 부를 때마다 무작위
            return _json(self, {"items": found, "summary": lib.summary(items), "songTotal": song_total(),
                                "randomCover": store.CoverScan(COVERS_BUNDLED).random_url()})
        if u.path == "/api/songs":        # 곡 번호 DB 전부 (번호 순)
            _build_items()                    # 보관함이 새로 읽혔으면 번호부터 맞춘다
            songs = store.songs_list()
            scan = store.CoverScan(COVERS_BUNDLED)
            for r in songs:
                r["coverUrl"] = scan.url(r["id"], r.get("cover") or "")
            return _json(self, {"ok": True, "songs": songs, "total": song_total()})
        if u.path == "/api/song":         # 곡 상세 시트 — 곡 하나의 번호·커버·재생목록·인사
            r = song_detail(q.get("key", ""))
            return _json(self, r, 200 if r.get("ok") else 404)
        if u.path == "/api/covers":       # 카드 커버 폴더 (설정 화면 「카드 커버」 줄) + 커버 고르기 시트의 칸
            cs = covers_state(q.get("id"))
            if self._is_remote():         # 이 PC 의 폴더 경로는 밖에 안 싣는다 (칸·주소·장수만)
                for k in ("dir", "folder", "folders", "exists"):
                    cs.pop(k, None)
            return _json(self, cs)
        if u.path == "/api/artists":
            items = _build_items()
            return _json(self, {"artists": lib.artists_summary(items), "other": sum(1 for it in items if it["bucket"] == "other"),
                                "state": store.get_artists()})
        if u.path == "/api/normalize":
            # 정규화 검토용: 제목 → 정리·분리·규칙 을 전부 보여 준다
            items = _build_items()
            return _json(self, {"summary": lib.summary(items),
                                "rows": [{k: it[k] for k in ("title", "cleaned", "removed", "tags", "variant", "artist", "song", "rule", "bucket")} for it in items]})
        if u.path == "/api/instruments":
            return _json(self, {"items": lib.build_instruments(store.folio_cache("instruments")["items"])})
        if u.path == "/api/playlists":
            return _json(self, _lists())
        if u.path == "/api/activity":
            if not self._guard():   # CLI 를 실행시키는 GET 이라 출처 검사
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if self._is_remote():   # 폰은 PC 감시 스레드(`_watch`)가 마지막으로 본 값만 — 게임에 묻지 않는다
                return _json(self, _activity_cached())
            return _json(self, _activity())
        if u.path == "/api/now":       # 마지막으로 본 연주 상태 (CLI 를 쓰지 않는다 — 감시 스레드가 조회한다)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, now_state())
        if u.path == "/api/presets":   # 연주 인사 프리셋
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"ok": True, **store.get_presets()})
        if u.path == "/api/social":    # 쓸 수 있는 행동·표정 목록 (프리셋 고르기용) — 이 PC 에서만 CLI 를 탄다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if self._is_remote():   # 폰은 PC 가 마지막으로 받아 둔 목록만 (아직 없으면 ok:false — 화면은 「게임에 연결되면 목록이 옵니다」)
                return _json(self, dict(_SOCIAL, cached=True))
            r = _cli("get_social_actions", timeout=30)
            b = r.body if isinstance(r.body, dict) else {}
            out = {"ok": bool(r.ok), "error": r.error, "message": r.message,
                   "behaviours": b.get("Behaviours") or [], "facials": b.get("Facials") or []}
            if out["ok"]:
                _SOCIAL.update(out, at=time.time())
            return _json(self, out)
        if u.path == "/api/remote/tunnel":   # 터널 상태 (여는 중·주소·오류)
            if not self._guard() or self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, _tunnel().state())
        if u.path == "/api/remote/devices":   # 짝지어 둔 기기 보기
            if not self._guard() or self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"ok": True,
                                "devices": [{k: v for k, v in x.items() if k != "token"}
                                            for x in store.get_devices()]})
        if u.path == "/api/bc/state":  # 방송 창 — 방 상태 (밖에서는 read 범위부터 · 모비웍스 문이 거른다)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, _bc.streamer("state", {}))
        if u.path == "/api/near":      # 주변 연주 (StartAt 으로 묶은 합주단) — 두 화면이 같은 값을 본다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, near_state())
        if u.path == "/api/queue":     # 지금 대기열 = 「현재 재생목록」 (두 화면이 같은 값을 본다)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, queue_state())
        if u.path == "/api/overlay":   # 경량판 오버레이 상태
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, _ov_state())
        if u.path == "/api/gamerect":   # 게임 창 위치·크기 (오프닝 연출을 게임 화면에 맞추려고 — CLI 를 쓰지 않는다)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            r = _game_rect()
            try:   # above=<창 핸들> 을 주면 그 창이 게임보다 위에 있는지도 알려 준다 (일렉트론 창이 쓴다)
                mine = int(q.get("above") or 0)
            except ValueError:
                mine = 0
            if mine and r.get("found"):
                import opening_tk
                r["above"] = opening_tk._is_above(mine, r["hwnd"])
            if mine and r.get("found") and not r.get("above", True):
                import opening_tk
                opening_tk.keep_above_game([mine], r["hwnd"], force=True)   # 정식판 창도 같은 규칙으로 끼운다
                r["above"] = True
            return _json(self, r)
        if u.path == "/api/ensemble":   # 합주 인식용 주변 플레이어 연주 상태 (읽기 전용) — 이 PC 의 오버레이 창이 쓴다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if self._is_remote():   # 폰은 PC 감시 스레드가 마지막으로 본 주변 연주(`_NEAR`)만 — 게임에 `get_near_pcs` 를 보내지 않는다
                return _json(self, _ensemble_cached())
            return _json(self, _ensemble())
        if u.path == "/api/hold":   # 창이 살아 있는 동안 열어 두는 연결 (경량판 종료 판정). 1초마다 한 바이트를 보내 끊김을 감지
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            global _holds, _had_hold, _last_hold_close
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            with _hold_lock:
                _holds += 1; _had_hold = True
            try:
                while True:
                    self.wfile.write(b".")
                    self.wfile.flush()
                    time.sleep(HOLD_TICK)
            except (OSError, ValueError):
                pass
            finally:
                with _hold_lock:
                    _holds -= 1; _last_hold_close = time.time()
            return
        if u.path == "/api/log":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"items": _log[:40]})
        if u.path == "/api/settings":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            # nocli=1: 설정 창을 열 때 — CLI 상태는 /api/state 가 이미 주기적으로 주므로 다시 묻지 않는다 (게임이 꺼져 있으면 probe 가 5초 걸린다)
            # **인증키는 어디로도 안 나간다 — 이 PC 의 화면에도** (모비웍스 `_public_settings` 와
            # 같은 규칙). 원래는 밖에서만 지웠는데, 합친 뒤 이 길이 `/api/folio/settings` 로 이 PC
            # 화면 셋에 그대로 열려 있어 키가 화면까지 내려왔다. 발행한 그 한 번만 보여 준다.
            st = dict(store.get_settings())
            st["remote_key_set"] = bool(st.pop("remote_key", ""))
            if self._is_remote():
                # 이 PC 의 속사정(파일 경로·업데이트 주소·창 설정·문 설정)은 밖으로 안 보낸다.
                # 모비웍스가 불러 쓸 때는 **그쪽이 준 목록만** (REMOTE_SETTINGS_OK — 거절이 기본).
                if REMOTE_SETTINGS_OK is not None:
                    st = {k: v for k, v in st.items() if k in REMOTE_SETTINGS_OK}
                else:
                    st = {k: v for k, v in st.items()
                          if not (k in ("cli_exe", "update_url") or k.startswith(("overlay", "remote", "ov_")))}
                # 밖에서는 CLI 상태를 묻지 않는다 — 폰이 설정을 열 때마다 게임에 조회가 나갈 이유가 없다
                return _json(self, {"settings": st, "cli": None})
            return _json(self, {"settings": st, "cli": None if q.get("nocli") else _probe()})
        if u.path == "/api/update/check":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, update_check())
        if u.path.startswith("/api/cli/"):
            # 읽기 전용 명령 디버그 통로 (필드 모양 확인용). 실행 명령은 막고, 다른 사이트의 <img>/fetch 로는 못 부르게 출처 검사
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            command = u.path[len("/api/cli/"):]
            if not (command in ("status", "capabilities") or command.startswith("get_")):   # 읽기 명령만 (화이트리스트)
                return _json(self, {"ok": False, "error": "not_allowed"}, 403)
            return _json(self, _cli(command, q.get("body"), timeout=120).to_dict())
        if u.path.startswith("/api/"):
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        # ── 밖(터널)에서 온 요청에는 화면 파일을 함부로 내주지 않는다 ──
        # index.html 에는 **실행 토큰이 박혀** 나가고, 터널 주소는 남이 주워 볼 수도 있다.
        # 밖에서 접속을 켜고, 허용한 주소로, 쪽지를 들고 들어온 것만 화면을 받는다.
        # (폰 앱은 제 사이트에 있고 /api/remote/login 으로 들어오므로 이 문과 무관하다)
        if self._is_remote():
            if not _host_ok_remote((self.headers.get("Host") or "").lower())                or not _remote_valid(_remote_tok(self)):
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
        if u.path in ("/", "/index.html"):
            return self._serve_index()
        if u.path == "/greet.html":    # 연주 인사 — **인사말 전용 페이지** (일반 설정과 따로)
            return self._serve_index("greet.html")
        if u.path == "/settings.html":  # 설정 — 미니판의 **별개 페이지**. 다른 판에는 파일이 없다
            return self._serve_index("settings.html")
        if u.path == "/net.js":         # 전송층 — 화면들과 **같은 토큰·모드**를 심어 내보낸다
            try:
                with open(os.path.join(UI_DIR, "net.js"), "rb") as f:
                    js = f.read()
            except OSError:
                return _json(self, {"ok": False, "error": "ui_missing"}, 500)
            js = self._stamp(js)
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(js)))
            self.end_headers()
            return self.wfile.write(js)
        if u.path.endswith((".py", ".tmp", ".json", ".log")) or "/." in u.path:   # ui/ 아래에 없지만, 혹시 몰라 원천 차단
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        return super().do_GET()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _stamp(self, data: bytes) -> bytes:
        """화면 파일에 **실행 토큰**과 **어디서 보는지**를 심는다.

        모드를 토큰과 따로 두는 이유: 개발 모드에서는 토큰이 아예 비어 있어서, 토큰만 보고는
        「이 PC 안」과 「밖」을 가릴 수 없다. 폰 앱 쪽(클라우드플레어)은 이 치환을 하지 않으므로
        자리표시자가 그대로 남고, 그것도 「밖」으로 친다.
        """
        remote = self._is_remote()
        data = data.replace(b"__MOBIFOLIO_MODE__", b"remote" if remote else b"local")
        # 밖에서는 **쪽지**가 열쇠라 토큰이 필요 없다 → 아예 비워 내보낸다 (새어도 쓸모없게)
        return data.replace(b"__MOBIFOLIO_TOKEN__",
                            b"" if remote else TOKEN.encode("ascii"))

    def _serve_index(self, name: str = "index.html"):
        """화면 파일(index.html 등)에 실행 토큰을 심고, 인라인 스크립트 해시로 CSP 를 건다 (외부 스크립트·인라인 핸들러 전부 차단)."""
        try:
            with open(os.path.join(UI_DIR, name), "rb") as f:
                html = f.read()
        except OSError:
            return _json(self, {"ok": False, "error": "ui_missing"}, 500)
        # 브라우저는 인라인 스크립트를 LF 로 정규화한 뒤 CSP 해시를 잰다 → CRLF 로 체크아웃된 파일이면
        # 해시가 어긋나 스크립트가 통째로 차단된다(core.autocrlf=true 인 Windows 클론). 서빙 전에 맞춘다.
        html = html.replace(b"\r\n", b"\n")
        html = self._stamp(html)
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if q.get("theme") in ("light", "dark"):   # 스크린샷·검토용: 저장된 선택과 무관하게 이번 로드만 테마 고정
            html = html.replace(b'<html lang="ko">', f'<html lang="ko" data-theme="{q["theme"]}">'.encode("ascii"), 1)
        hashes = []
        pos = 0
        while True:
            a = html.find(b"<script>", pos)
            if a < 0:
                break
            b = html.find(b"</script>", a)
            hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(html[a + 8:b]).digest()).decode("ascii") + "'")
            pos = b + 9
        # 'self' 는 net.js 때문이다 — 미니 화면 셋이 같은 전송층을 쓰고, 그 파일은
        # 폰(다른 주소)에서도 같은 이름으로 올라간다.
        csp = ("default-src 'self'; script-src 'self' " + (" ".join(hashes) or "") +
               "; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:*; font-src 'self'; "
               "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(html)

    def _is_remote(self) -> bool:
        """이 PC 밖에서 온 요청인가 (터널을 지나온 것)."""
        return (self.headers.get("Host") or "").lower() not in _ok_hosts

    def _guard(self) -> bool:
        """CSRF 방지: 브라우저의 다른 사이트가 보낸 폼/스크립트 요청을 거른다.
        (1) Host 가 우리 주소, (2) Origin 이 있으면 우리 출처, (3) UI/셸이 붙이는 X-Requested-With 헤더.

        **밖에서 온 요청**은 (1) 사용자가 적어 둔 주소이고 (2) 비밀번호로 받은 쪽지를 들고 있을 때만 받는다.
        토큰은 HTML 에 박혀 나가므로 밖에서는 열쇠 구실을 못 한다 — 쪽지가 그 자리를 대신한다.
        """
        host = (self.headers.get("Host") or "").lower()
        origin = (self.headers.get("Origin") or "").lower()
        if self.headers.get("X-Requested-With") != "mobifolio":
            return False
        if host in _ok_hosts:
            if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
                return False
            if TOKEN and not hmac.compare_digest(self.headers.get("X-MobiFolio-Token") or "", TOKEN):   # 배포판: 실행마다 다른 토큰 — 페이지(index.html)와 셸만 안다
                return False
        else:
            if not _host_ok_remote(host):
                return False
            if origin:                       # 제 페이지(터널 주소) 또는 사용자가 적어 둔 사이트만
                oh = origin.split("://", 1)[-1].lower()
                if not _host_ok_remote(oh) and not _cors_origin(self):
                    return False
            if not _remote_valid(_remote_tok(self)):
                return False
        global _last_ui
        _last_ui = time.time()   # 살아 있는 UI 의 심장박동
        return True

    def _remote_blocked(self, path: str) -> bool:
        """밖에서 온 요청을 막아야 하는가.

        두 겹이다. (1) 범위와 무관하게 이 PC 에서만 되는 것은 언제나 막는다,
        (2) 「재생 조작만」이면 그 밖의 것도 막는다.
        """
        if not self._is_remote():
            return False
        if _hosted_pass(self):       # 모비웍스 문이 폴리오 길 이름으로 이미 판정했다 (거절이 기본인 그쪽 목록)
            return False
        if path in REMOTE_NEVER or path.startswith(REMOTE_NEVER_PREFIX):
            return True
        return _remote_cfg()["scope"] != "full" and path not in REMOTE_PLAY_OK

    def do_POST(self):
        try:
            # **`self._post()` 가 아니라 `H._post(self)` 다.** 모비웍스가 `/api/folio/…` 를 넘길 때
            # **자기 핸들러**를 `self` 로 준다 (소켓·헤더가 그쪽 것이라). 그쪽 핸들러에도 `_post` 가
            # 있어서 `self._post()` 는 **그쪽 `_post` 로 되돌아갔다** — 경로는 이미 `/api/play` 로
            # 바뀐 채라 queue/clear·ens·stop 이 404, presets 는 그쪽 **큐 프리셋** 처리기로,
            # opening/video 는 그쪽 `_read_json` 이 본문을 먹어 400 이 됐다. 폴리오 POST 가 전부
            # 죽어 있었는데 기존 검사는 404 도 「답이 왔다」로 쳐서 못 잡았다 (tests/test_folio_qa.py).
            H._post(self)
        except Exception as e:   # 어떤 입력에도 연결을 끊지 않고 JSON 으로 답한다
            import traceback; traceback.print_exc()
            try:
                _json(self, {"ok": False, "error": "internal", "message": type(e).__name__}, 500)
            except Exception:
                pass

    def _post(self):
        u = urlparse(self.path)
        if u.path == "/api/remote/login":   # 밖에서 들어오는 문 — 비밀번호를 받고 쪽지를 준다
            if not _host_ok_remote(self.headers.get("Host") or ""):
                return _json(self, {"ok": False, "error": "remote_off",
                                    "message": "밖에서 접속이 꺼져 있거나 주소가 다릅니다."}, 403)
            who = self.client_address[0] if self.client_address else "?"
            tries, until = _REMOTE_TRY.get(who, (0, 0.0))
            if until > time.time():
                return _json(self, {"ok": False, "error": "locked",
                                    "message": f"{int(until - time.time())}초 뒤에 다시 해 보세요.",
                                    "retryAfterSeconds": int(until - time.time())}, 429)
            p0 = _read_json(self)
            given = _s(p0.get("key")) or _s(p0.get("code")) or _s(p0.get("pass"))
            dev_tok = _s(p0.get("device"))
            dev = store.touch_device(dev_tok) if dev_tok else None
            paired = None
            if not dev and given:
                paired = _pair_take(given)          # 만남 코드로 새로 짝짓기
            if dev or paired or store.check_remote_key(given):
                _REMOTE_TRY.pop(who, None)
                tok = _remote_new(dev["id"] if dev else "")
                out = {"ok": True, "token": tok, "ttl": REMOTE_TTL}
                if dev:
                    out["device"] = dev_tok
                    out["name"] = dev["name"]
                    _say(f"[remote] 「{dev['name']}」이(가) 들어왔습니다 ({who})")
                elif paired or store.check_remote_key(given):
                    # 인증키로 들어와도 기기 열쇠를 준다 — 안 그러면 12시간마다 7자를
                    # 다시 쳐야 한다. 열쇠는 설정에서 기기별로 끓을 수 있다.
                    new_dev = store.add_device(_s(p0.get("name")))   # 이 기기에 오래 쓸 열쇠를 준다
                    _REMOTE[tok]["dev"] = new_dev["id"]
                    out["device"] = new_dev["token"]
                    out["name"] = new_dev["name"]
                    _say(f"[remote] 새 기기를 짝지었습니다 — {new_dev['name']} ({who})")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie",
                                 f"mf_remote={tok}; Path=/; Max-Age={REMOTE_TTL}; HttpOnly; SameSite=Lax")
                _cors_headers(self)
                # 쿠키는 같은 주소로 브라우저를 직접 연 경우용. 폰 앱은 **token 을 저장해**
                # 다음부터 X-MobiFolio-Remote 헤더로 보내고, **device** 는 더 오래 들고 있는다
                # (아이폰은 3자 쿠키를 막는다 · 저장소가 지워져도 device 만 있으면 다시 들어온다).
                body = json.dumps(out).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            tries += 1
            until = time.time() + REMOTE_LOCK if tries >= REMOTE_MAX_TRY else 0.0
            _REMOTE_TRY[who] = (0 if until else tries, until)
            _say(f"[remote] 인증키가 틀렸습니다 ({who}, {tries}회)")
            if until:      # 마지막으로 틀린 그 자리에서 잠금을 알려 준다 (다음 번에 알리면 늦다)
                return _json(self, {"ok": False, "error": "locked",
                                    "message": f"{int(REMOTE_LOCK)}초 뒤에 다시 해 보세요.",
                                    "retryAfterSeconds": int(REMOTE_LOCK)}, 429)
            return _json(self, {"ok": False, "error": "bad_key", "message": "인증키가 다릅니다.",
                                "left": max(0, REMOTE_MAX_TRY - tries)}, 401)
        if u.path == "/api/remote/logout":
            # 폰에서 「연결 끕기」를 누른 것은 **이 기기를 떼겠다**는 뜻이다.
            # 쯪지만 버리면 PC 의 기기 목록에 쓸모없는 줄이 남는다.
            tok = _remote_tok(self)
            sess = _REMOTE.pop(tok, None) or {}
            dev = str(sess.get("dev") or "")
            if dev:
                store.drop_device(dev)
                _remote_drop_dev(dev)      # 같은 기기의 다른 쯪지도 같이 죽인다
            return _json(self, {"ok": True})
        if u.path == "/api/bye":   # 페이지 닫힘 신호 (sendBeacon 은 헤더를 못 붙이므로 토큰은 본문으로)
            if self._is_remote():      # 밖에서 부르면 PC 앱이 꺼진다 — 창 신호는 이 PC 것만 받는다
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            p = _read_json(self)
            if not TOKEN or hmac.compare_digest(str(p.get("token") or ""), TOKEN):
                global _bye_at
                _bye_at = time.time()
                _say("[lite] 창이 닫힌다고 알려 왔습니다")
            return _json(self, {"ok": True})
        if not self._guard():
            return _json(self, {"ok": False, "error": "forbidden", "message": "허용되지 않은 출처의 요청입니다."}, 403)
        # `H._remote_blocked` 로 **이 파일 것**을 명시해 부른다 — `self` 가 모비웍스 핸들러일 때
        # 그쪽 `_remote_blocked(path, post)` 는 인자 수가 달라 TypeError(500) 가 났다. 밖에서 온
        # 요청은 그쪽 문(`FOLIO_PLAY_OK`)이 이미 걸렀고, 여기 판정은 그와 같은 여덟 길이다.
        if H._remote_blocked(self, u.path):
            return _json(self, {"ok": False, "error": "remote_scope",
                                "message": "밖에서는 재생 조작만 할 수 있습니다 (설정에서 범위를 바꿀 수 있습니다)."}, 403)
        if u.path == "/api/covers/upload":     # 커버 고르기 「내 이미지 추가…」 — 본문이 그림 그대로 (REMOTE_NEVER)
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return cover_upload(self, parse_qs(u.query).get("name", [""])[0])
        p = _read_json(self)
        if "_error" in p:
            return _json(self, {"ok": False, "error": "bad_request", "message": p["_error"]}, 400)
        # ── 방송 (스트리머) — 밖(모바일 리모컨)에서는 모비웍스 문이 edit 범위부터만 넘긴다 (`FOLIO_EDIT_OK`) ──
        # 글자로 적는다 — 길 뽑는 눈(tests/test_security_q1.py)이 이 길들을 전수 행렬에 넣는다.
        if (u.path == "/api/bc/on" or u.path == "/api/bc/off" or u.path == "/api/bc/code" or u.path == "/api/bc/pl"
                or u.path == "/api/bc/cfg" or u.path == "/api/bc/ok" or u.path == "/api/bc/no" or u.path == "/api/bc/block"):
            r = _bc.streamer(u.path[len("/api/bc/"):], p)
            return _json(self, r, 200 if r.get("ok") else (410 if r.get("error") == "ended" else 400))
        if u.path == "/api/bc/window":    # 방송 창 열기·앞으로 — **이 PC 의 창**이라 밖에서는 안 된다
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, _bc.streamer("window", p))
        if u.path == "/api/quit":     # 셸이 창을 닫을 때 정상 종료 요청 (taskkill 대신 → PyInstaller 임시폴더 정리됨)
            _json(self, {"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
            return
        if u.path == "/api/sync":
            return _json(self, sync())
        if u.path == "/api/play":
            # 제목·악기 이름은 게임이 준 문자열 그대로 (끝에 공백이 있는 이름이 실제로 있다 — strip 하면 못 찾는다)
            t, inst = p.get("title"), p.get("instrument")
            t = t if isinstance(t, str) else _s(t)
            inst = inst if isinstance(inst, str) and inst.strip() else None
            k = p.get("key")
            return _json(self, play_solo(t, inst, k if isinstance(k, str) else ""))
        if u.path == "/api/queue":        # 목록에서 재생 — 그 목록 전체가 대기열이 된다 (규칙 1)
            keys = p.get("keys")
            if not isinstance(keys, list):
                return _json(self, {"ok": False, "error": "bad_request", "message": "keys 는 배열이어야 합니다."}, 400)
            insts = p.get("inst") if isinstance(p.get("inst"), dict) else {}
            start = p.get("start")
            if not isinstance(start, (int, str)):
                start = 0
            return _json(self, queue_set(keys, start, _s(p.get("src")), _s(p.get("name")), insts,
                                         play_now=p.get("play") is not False))
        if u.path == "/api/queue/step":   # 이전·다음
            try:
                d = int(p.get("dir") or 1)
            except (TypeError, ValueError):
                d = 1
            return _json(self, queue_step(1 if d >= 0 else -1))
        if u.path == "/api/queue/at":     # 현재 재생목록에서 줄을 눌러 그 곡으로
            try:
                i = int(p.get("index"))
            except (TypeError, ValueError):
                return _json(self, {"ok": False, "error": "bad_request"}, 400)
            return _json(self, queue_play_index(i))
        # 줄 메뉴 「다음에 재생」·「현재 재생목록 끝에 담기」 — 지금 곡은 끊지 않는다 · 비었으면 만들되 틀지 않는다
        if u.path == "/api/queue/next" or u.path == "/api/queue/append":
            k = p.get("key")
            k = k if isinstance(k, str) else ""
            r = queue_insert_next(k) if u.path == "/api/queue/next" else queue_append(k, move=True)
            if not r.get("ok"):
                return _json(self, r, 400)
            return _json(self, {**queue_state(), "at": r["pos"]})   # at = 담긴 자리 (화면 차례 1부터)
        if u.path == "/api/queue/ask":    # 합주가 끝난 뒤의 물음에 답한다
            _ENG["ask_at"] = 0.0
            if p.get("go"):
                return _json(self, queue_step(1))
            return _json(self, queue_state())
        if u.path == "/api/queue/clear":
            return _json(self, queue_clear())
        if u.path == "/api/queue/inst":   # 대기열의 한 곡에 악기 저장 (미니 「현재곡 칸」·줄 메뉴)
            # key·악기 이름은 원문 그대로 (끝에 공백이 있는 악기 이름이 실제로 있다 — strip 하면 다른 이름이 된다)
            k, inst = p.get("key"), p.get("inst")
            return _json(self, queue_inst(k if isinstance(k, str) else "", inst if isinstance(inst, str) else ""))
        if u.path == "/api/ens":   # 악보별 합주 인원 지정 (게임이 안 알려 줘서 사용자가 적는다)
            keys = p.get("keys")
            if not isinstance(keys, list):
                keys = [p.get("key")] if p.get("key") else []
            d = store.set_ens(keys, p.get("ens"))
            return _json(self, {"ok": True, "ens": d, "count": len(keys)})
        if u.path == "/api/instrument":   # 연주하지 않고 악기만 바꿔 든다 (미니판 「장착」)
            out = equip(_s(p.get("instrument")))
            return _json(self, out, 400 if out.get("error") == "empty" else 200)
        if u.path == "/api/presets":      # 프리셋 저장 (항목·기본 선택·곡별 지정 중 보낸 것만)
            return _json(self, {"ok": True, **store.set_presets(p if isinstance(p, dict) else {})})
        if u.path == "/api/greet/assign":   # 곡 상세 시트 「연주 인사」 — 이 곡의 시작 인사 (preset "" = 기본)
            if self._is_remote() and not _hosted_pass(self):   # 모비웍스 안에서는 그쪽 목록(FOLIO_EDIT_OK)이 정한다
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            k = p.get("key") if isinstance(p, dict) else None      # key 는 원문 그대로 (「제목#2」 등)
            r, code = greet_assign(k, p.get("preset") if isinstance(p, dict) else "")
            return _json(self, r, code)
        if u.path == "/api/greet/test":   # 프리셋 한 벌을 지금 내보내 본다 — **실제로 게임에 나간다**
            kind = _s(p.get("kind")) or "start"
            it = p.get("item") if isinstance(p.get("item"), dict) else (queue_state().get("now") or {})
            pid = _s(p.get("id"))
            if not pid:
                return _json(self, greet_run(kind, it, None, _s((it or {}).get("key"))))
            ps = next((x for x in store.get_presets()["items"] if x.get("id") == pid), None)
            if not ps:
                return _json(self, {"ok": False, "error": "not_found"}, 404)
            s2 = store.get_settings()
            gap = max(0.3, float(s2.get("greet_step") if s2.get("greet_step") is not None else 1.0))
            sent = []
            for part in ("chat", "action", "emoji"):
                txt = ps.get(part) or ""
                if part == "chat":
                    txt = greet_render(txt, it)
                if not txt:
                    continue
                if sent:
                    time.sleep(gap)
                sent.append(_chat(txt))
            return _json(self, {"ok": all(x.get("ok") for x in sent) if sent else True, "sent": sent})
        if u.path == "/api/songs":   # 곡 번호 DB 고치기 — 지금은 곡마다 커버 고르기 하나 (REMOTE_NEVER)
            if self._is_remote() and not _hosted_pass(self):   # 모비웍스 안에서는 그쪽 목록(FOLIO_EDIT_OK)이 정한다
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if p.get("op") != "cover":
                return _json(self, {"ok": False, "error": "bad_op", "message": "op 는 cover 만 됩니다."}, 400)
            sid = p.get("id")
            try:
                rec = store.song_set_cover(key=p.get("key") if isinstance(p.get("key"), str) else "", song_id=sid if isinstance(sid, (int, str)) and not isinstance(sid, bool) else None,
                                           cover=p.get("cover") if isinstance(p.get("cover"), (str, type(None))) else "\0",   # 글자가 아니면 bad_cover
                                           bundled_dir=COVERS_BUNDLED)
            except ValueError as e:
                msg = {"no_song": "번호 DB 에 없는 곡입니다 — 악보함을 한 번 갱신해 주세요.",
                       "bad_cover": "커버 이름이 올바르지 않습니다.",
                       "no_file": "그 그림이 커버 폴더에 없습니다."}.get(str(e), str(e))
                return _json(self, {"ok": False, "error": str(e), "message": msg}, 400)
            rec["coverUrl"] = store.CoverScan(COVERS_BUNDLED).url(rec["id"], rec["cover"])
            return _json(self, {"ok": True, "song": rec})
        if u.path in ("/api/covers/search", "/api/covers/fetch"):
            # 「온라인에서 찾기」 (선택 기능). **이 PC 에서 사람이 누를 때만**
            # (REMOTE_NEVER). 앱이 알아서 부르는 곳은 없다 — 시트의 단추 하나뿐이다. 받은 그림은 이 PC 의 mine/ 에만.
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            sid = p.get("id")
            sid = sid if isinstance(sid, (int, str)) and not isinstance(sid, bool) else None
            key = p.get("key") if isinstance(p.get("key"), str) else ""
            msgs = {"no_song": "번호 DB 에 없는 곡입니다 — 악보함을 한 번 갱신해 주세요.",
                    "offline": "인터넷에 연결되지 않았거나 검색 서비스가 답하지 않습니다. 잠시 뒤 다시 해 보세요.",
                    "bad_answer": "검색 서비스의 답을 읽지 못했습니다. 잠시 뒤 다시 해 보세요.",
                    "bad_url": "방금 찾은 후보가 아닙니다 — 다시 찾아 주세요.",
                    "too_large": "그림이 너무 큽니다 (8MB 까지).", "not_image": "그림 파일이 아닙니다."}
            try:
                if u.path == "/api/covers/search":
                    q_ = p.get("q") if isinstance(p.get("q"), str) else ""
                    r = store.cover_search_online(key=key, song_id=sid, q=q_)
                    _say(f"[covers] 온라인에서 찾기: 「{r['query']}」 → {r['source'] or '없음'} {len(r['items'])}건")
                    return _json(self, dict(r, ok=True, notice="개인 감상용입니다 · 출처 표시 · 백업·공유에 넣지 않습니다"))
                rec = store.cover_fetch_online(key=key, song_id=sid, url=p.get("url") if isinstance(p.get("url"), str) else "",
                                               source=p.get("source") if isinstance(p.get("source"), str) else "")
            except ValueError as e:
                code = str(e)
                return _json(self, {"ok": False, "error": code, "message": msgs.get(code, code)},
                             502 if code in ("offline", "bad_answer") else 400)
            except OSError as e:
                return _json(self, {"ok": False, "error": "write", "message": f"저장하지 못했습니다: {e}"}, 500)
            _say(f"[covers] 온라인 커버를 받았습니다: #{rec['id']:04d} ← {rec['cover']} ({(rec.get('cover_src') or {}).get('source')})")
            rec["coverUrl"] = store.CoverScan(COVERS_BUNDLED).url(rec["id"], rec["cover"])
            st = covers_state(rec["id"])
            return _json(self, {"ok": True, "song": rec, "value": rec["cover"], "mine": st["mine"], "defaults": st["defaults"],
                                "auto": st.get("auto")})
        if u.path == "/api/covers/open":   # 카드 커버 폴더를 탐색기로 연다 — **이 PC 안에서만** (REMOTE_NEVER)
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, covers_open())
        if u.path == "/api/covers":        # 「내 커버 관리」 — 폴더 열기 · 내 이미지 지우기 (REMOTE_NEVER)
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            op = p.get("op")
            if op == "open":
                which = p.get("which") if isinstance(p.get("which"), str) else ""
                r = covers_open(which or "root")
                return _json(self, r, 200 if r.get("ok") or r.get("error") != "bad_which" else 400)
            if op == "delete":
                name = p.get("name") if isinstance(p.get("name"), str) else ""
                try:
                    gone = store.cover_delete(name)
                except ValueError as e:
                    msg = {"bad_name": "이름이 올바르지 않습니다.", "no_file": "내 이미지 폴더에 그 그림이 없습니다.",
                           "in_use": "쓰는 곡이 있어 지우지 않았습니다 — 먼저 그 곡의 커버를 바꿔 주세요."}.get(str(e), str(e))
                    out = {"ok": False, "error": str(e), "message": msg}
                    if str(e) == "in_use":
                        out["usedBy"] = next((c["usedBy"] for c in covers_state()["mine"] if c["name"] == name), [])
                    return _json(self, out, 409 if str(e) == "in_use" else 400)
                except OSError as e:
                    return _json(self, {"ok": False, "error": "write", "message": f"지우지 못했습니다: {e}"}, 500)
                _say(f"[covers] 내 이미지를 지웠습니다: {gone}")
                return _json(self, dict(covers_state(), deleted=gone))
            return _json(self, {"ok": False, "error": "bad_op", "message": "op 는 open · delete 만 됩니다."}, 400)
        if u.path == "/api/opening":   # 경량판·미니판 합주 시작 연출 (정식판은 일렉트론 창으로 한다)
            # 오버레이 밴드(`_ov`)가 없어도 앱 시작 때 만든 연출(`_OPENING`)이 있으면 된다 — 밴드를 꺼 둔 채
            # 설정의 「미리 보기」가 「이 판에는 연출이 없습니다」로 끝나던 것을 막는다
            if _opening_target() is None:
                return _json(self, {"ok": False, "error": "no_overlay", "message": "이 판에는 연출이 없습니다."}, 400)
            # 손으로 부르는 길(시험용)도 같은 문을 지난다 — 지금 울리는 연주에 이미 카드가 나갔으면 건너뛴다.
            # 미니 창은 이제 재생 직후 스스로 부르지 않는다 (서버가 인사 타임라인·재생 순간에 한 번 낸다).
            # `preview: true` 는 설정의 「미리 보기」 — 지금 곡이 울리는 중이어도 한 번 띄운다 (매번 새 열쇠).
            players = [list(x) for x in (p.get("players") or []) if isinstance(x, (list, tuple)) and len(x) == 2]
            if p.get("preview") is True:
                key = ("preview", time.time())
            elif _NOW.get("playing") and _NOW.get("title"):
                key = _opening_key(_NOW["title"], _NOW.get("start_at"))
            else:
                key = ("manual", time.time())
            return _json(self, opening_fire(key, _s(p.get("song")) or _s(_NOW.get("title")), "api",
                                            players=players or None, inst=_s(p.get("inst")) or None))
        if u.path == "/api/overlay":   # 경량판 오버레이 제어 (정식판은 일렉트론 IPC 로 한다)
            if _ov is None and p.get("on"):
                _ov_start()   # 「오버레이 켜기」 — 설정을 꺼 둔 채라 아직 창이 없으면 지금 만든다 (사람이 눌렀을 때만)
            if _ov is None:
                return _json(self, {"ok": False, "error": "no_overlay", "message": "이 판에는 오버레이가 없습니다."}, 400)
            if p.get("reset"):
                _ov.move_default()
            elif "through" in p:
                _ov._set_through(bool(p.get("through")))
            elif "auto" in p:
                _ov.set_auto(bool(p.get("auto")))
            elif "player" in p:
                _ov.set_player(bool(p.get("player")))
            elif "on" in p:
                _ov._show(bool(p.get("on")))
            return _json(self, _ov_state())
        if u.path == "/api/stop":
            _GREET["pending"] = None        # 인사 중이었으면 연주를 시작하지 않는다
            _ENG["stop_at"] = time.time()   # 사람이 멈춘 것 — 엔진이 다음 곡으로 잇지 않는다
            return _json(self, stop())
        if u.path == "/api/playlists":
            r = lists_op(_s(p.get("op")), p)
            if not r.get("ok"):
                r = {**_lists(), **r}   # 실패해도 UI 가 목록 상태를 잃지 않게
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/artists":
            r = artists_op(_s(p.get("op")), p)
            if not r.get("ok"):
                r = {**store.get_artists(), **r}
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/cli_test":   # 설정 창 「연결 확인」: 저장하지 않고 그 경로로 status 만 호출
            exe = _s(p.get("cli_exe"))
            if exe and not cli.valid_cli_path(exe):
                return _json(self, {"ok": False, "error": "bad_cli_exe", "message": "로컬 드라이브의 MabinogiMobile_CLI.exe 절대 경로가 아닙니다."})
            with _cli_lock:
                old = cli._override
                try:
                    cli.set_exe_override(exe)
                    res = cli.probe()
                finally:
                    cli.set_exe_override(old)
            return _json(self, {"ok": True, "cli": res})
        if u.path == "/api/update/apply":
            return _json(self, update_apply(p))
        if u.path == "/api/remote/link":    # 폰과 잇기 — **이 PC 안에서만**
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            d = store.get_settings()
            if not d.get("remote_on"):
                return _json(self, {"ok": False, "error": "remote_off",
                                    "message": "「밖에서 접속 허용」을 먼저 켜 주세요."})
            if not store.has_remote_key():
                return _json(self, {"ok": False, "error": "no_key",
                                    "message": "인증키를 먼저 발행해 주세요."})
            tn = _tunnel().state()
            url = tn.get("url") or ""
            if not url:
                return _json(self, {"ok": False, "error": "no_tunnel",
                                    "message": "터널을 먼저 켜 주세요."})
            if not tn.get("ready"):
                # 우편함 코드는 한 번 읽히면 사라진다. 닿지도 않는 주소를 담아 주면
                # 폰이 헛걸음하고 코드만 타 버린다 (실제로 그렇게 됐다).
                return _json(self, {"ok": False, "error": "not_ready",
                                    "message": "주소가 아직 밖에서 안 닿습니다. 20~30초 뒤에 다시 눌러 주세요."})
            return _json(self, _mailbox_put(url))
        if u.path == "/api/remote/tunnel":  # 터널 켜기·끄기 — **이 PC 안에서만**
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            t = _tunnel()
            if p.get("stop"):
                t.stop()
                return _json(self, t.state())
            t.start(PORT, on_url=_tunnel_got_url)
            return _json(self, t.state())
        if u.path == "/api/remote/unlock":  # 잠금 풀기 — **이 PC 안에서만**
            # 폰에서 인증키를 몇 번 잘못 눌러 잠겼을 때, 5분을 기다리지 않고 여기서 푼다.
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            n = len(_REMOTE_TRY)
            _REMOTE_TRY.clear()
            return _json(self, {"ok": True, "cleared": n})
        if u.path == "/api/remote/pair":  # 만남 코드 발급 — **이 PC 안에서만**
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            d = _pair_new()
            # 코드는 적지 않는다 — 로그를 본 사람이 3분 안에 짝지을 수 있다. 화면에만 보인다.
            _say(f"[remote] 만남 코드를 냈습니다 ({int(PAIR_TTL)}초)")
            return _json(self, {"ok": True, **d})
        if u.path == "/api/remote/devices":   # 짝지어 둔 기기 — 보기·끊기 (이 PC 안에서만)
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if p.get("drop_all"):
                store.drop_all_devices()
                _REMOTE.clear()
            elif p.get("drop"):
                did = _s(p.get("drop"))
                store.drop_device(did)
                _remote_drop_dev(did)         # **그 기기 것만** 끊는다
            items = [{k: v for k, v in x.items() if k != "token"} for x in store.get_devices()]
            return _json(self, {"ok": True, "devices": items})
        if u.path == "/api/remote/key":   # 인증키 발행·삭제 — **이 PC 안에서만** (밖에서는 못 만든다)
            if self._is_remote():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if p.get("clear"):
                store.clear_remote_key()
                store.drop_all_devices()      # 짝지어 둔 기기도 전부 끊는다
                _PAIR.clear()
                _REMOTE.clear()               # 들고 있던 쪽지도 전부 무효
                return _json(self, {"ok": True, "key": ""})
            if isinstance(p.get("key"), str):     # 사용자가 직접 적은 키
                ok, why = store.set_remote_key(p["key"])
                if not ok:
                    return _json(self, {"ok": False, "error": "bad_key", "message": why})
                _REMOTE.clear()               # 키가 바뀌면 앞의 접속은 끊긴다
                store.drop_all_devices()
                _PAIR.clear()
                return _json(self, {"ok": True, "key": store.get_remote_key()})
            key = store.issue_remote_key()
            _REMOTE.clear()                   # 새 키를 내면 앞의 접속은 끊긴다
            _say("[remote] 인증키를 새로 발행했습니다")
            return _json(self, {"ok": True, "key": key})
        if u.path == "/api/settings":
            # 인증키는 설정 API 로 못 바꾼다 — /api/remote/key 로만 발행한다
            # (아래에서 저장한 뒤 오버레이에 곧바로 알린다 — 10초를 기다리면 「안 먹는다」가 된다)
            patch = p.get("settings")
            if not isinstance(patch, dict):
                return _json(self, {"ok": False, "error": "bad_request", "message": "settings 는 객체여야 합니다."}, 400)
            patch.pop("remote_key", None)
            # **밖에서는 이 PC 의 창·문을 건드리는 설정을 못 바꾼다** (모비웍스 `/api/settings` 와
            # 같은 규칙 — 그쪽 문이 이 길을 밖에 안 열지만, 두 문이 따로 있으면 언젠가 한쪽만
            # 고친다). `remote_scope` 를 폰이 스스로 올리는 일이 특히 안 된다.
            dropped = []
            if self._is_remote():
                if REMOTE_SETTINGS_OK is not None:   # 모비웍스가 준 **폴리오 키 목록**만 (거절이 기본)
                    dropped = sorted(str(k) for k in patch if k not in REMOTE_SETTINGS_OK)
                else:
                    dropped = sorted(k for k in patch if str(k).startswith(("overlay", "remote", "ov_")))
                patch = {k: v for k, v in patch.items() if k not in dropped}
                if dropped and not patch:            # 바꿀 수 있는 것이 하나도 없었다 — 조용히 200 을 주지 않는다
                    return _json(self, {"ok": False, "error": "remote_setting", "ignored": dropped,
                                        "message": "밖에서는 폴리오 설정만 바꿀 수 있습니다."}, 403)
            uu = patch.get("update_url")
            if isinstance(uu, str) and uu.strip() and not _safe_url(uu):
                return _json(self, {"ok": False, "error": "bad_update_url", "message": "업데이트 주소는 https:// 로 시작해야 합니다."}, 400)
            exe = patch.get("cli_exe")
            if isinstance(exe, str) and exe.strip() and not cli.valid_cli_path(exe):
                return _json(self, {"ok": False, "error": "bad_cli_exe", "message": "CLI 경로는 로컬 드라이브의 MabinogiMobile_CLI.exe 절대 경로여야 합니다."}, 400)
            before = store.get_settings().get("cli_exe")
            was = dict(store.get_settings())
            s = store.set_settings(patch)
            settings_changed(was, s)         # 셔플이면 차례를 다시 · 반복·셔플·고정이면 두 화면에 알린다 (오버레이와 같은 길)
            cli.set_exe_override(s.get("cli_exe"))
            if _ov is not None:              # 오버레이는 10초마다 읽는다 — 그냥 두면 크기·투명도가
                try:                         # 「안 먹는다」로 보인다. 저장한 자리에서 알려 준다.
                    _ov.reload_cfg()
                except Exception:
                    pass
            s = dict(s)
            s["remote_key_set"] = bool(s.pop("remote_key", ""))    # 답에도 키는 안 싣는다
            if self._is_remote():            # 답도 GET 과 **같은 목록**으로 (POST 답만 넓으면 그 길로 샌다)
                if REMOTE_SETTINGS_OK is not None:
                    s = {k: v for k, v in s.items() if k in REMOTE_SETTINGS_OK}
                else:
                    s = {k: v for k, v in s.items()
                         if not (k in ("cli_exe", "update_url") or k.startswith(("overlay", "remote", "ov_")))}
                return _json(self, {"ok": True, "settings": s, "cli": None,
                                    **({"ignored": dropped, "message": "밖에서는 이 PC 의 창·접속 설정을 바꿀 수 없습니다."}
                                       if dropped else {})})
            return _json(self, {"ok": True, "settings": s,
                                "cli": _probe() if s.get("cli_exe") != before else None,   # 경로가 바뀐 때만 다시 확인 (저장이 5초 걸리지 않게)
                                **({"ignored": dropped, "message": "밖에서는 이 PC 의 창·접속 설정을 바꿀 수 없습니다."}
                                   if dropped else {})})
        return _json(self, {"ok": False, "error": "not_found"}, 404)


def _find_app_browser() -> str | None:
    """앱 모드(주소창·탭 없는 독립 창)를 지원하는 크로미엄 계열 브라우저. Edge 는 Windows 10/11 기본 내장."""
    cands = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _seed_profile(profile: str) -> None:
    """엣지 전용 프로필을 손봐서 창을 띄울 때 안내가 뜨지 않게 한다.

    - 'First Run' 파일이 있으면 크로미엄이 첫 실행 마법사를 건너뛴다.
    - profile.exit_type 이 'Crashed' 로 남아 있으면 복원 안내가 뜬다 — 우리가 창을 닫거나 백엔드가 먼저 끝나면 늘 이 상태가 된다.
      우리 창은 복원할 탭이 없으므로(항상 같은 주소를 연다) 띄우기 전에 정상 종료로 표시한다.
    """
    try:
        os.makedirs(os.path.join(profile, "Default"), exist_ok=True)
        with open(os.path.join(profile, "First Run"), "a", encoding="ascii"):
            pass
        pref = os.path.join(profile, "Default", "Preferences")
        d = {}
        try:
            with open(pref, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        prof = d.setdefault("profile", {}) if isinstance(d.get("profile", {}), dict) else {}
        d["profile"] = prof
        prof["exit_type"] = "Normal"
        prof["exited_cleanly"] = True
        br = d.setdefault("browser", {}) if isinstance(d.get("browser", {}), dict) else {}
        d["browser"] = br
        br["has_seen_welcome_page"] = True
        br["show_home_button"] = False
        tmp = pref + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, pref)
    except OSError as e:
        print(f"[lite] profile seed failed: {e}", flush=True)


def _launch_app_window(url: str):
    """Edge/Chrome 앱 창(주소창 없음, 전용 프로필)을 띄우고 프로세스 핸들을 돌려준다. 없으면 기본 브라우저 탭(None)."""
    global _app_profile
    exe = _find_app_browser()
    if exe:
        profile = os.path.join(BASE, _APP_PROFILE)
        _app_profile = profile
        _seed_profile(profile)
        args = [exe, f"--app={url}", f"--window-size={_window_size()}", f"--user-data-dir={profile}",
                # 안내·권유 창을 전부 끈다: 첫 실행 마법사, 기본 브라우저 묻기, 복원 안내, 로그인·동기화 권유, 자동 업데이트 점검
                "--no-first-run", "--no-default-browser-check", "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
                "--no-service-autorun", "--disable-sync", "--disable-component-update", "--disable-background-networking",
                "--disable-extensions", "--disable-features=TranslateUI,msEdgeStartupBoost,msImplicitSignin,ImplicitSignIn,EdgeFirstRunExperience,MicrosoftEdgeWelcomePage,msEdgeWelcomePage,msUndersideButton,msEdgeShoppingAssistant",
                # 최소화·가림 상태에서도 페이지 타이머(재생 감시 1초 폴링)가 늦춰지지 않게
                "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"]
        try:
            import subprocess
            return subprocess.Popen(args, creationflags=0x00000008)   # DETACHED_PROCESS
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)
    return None


def _open_window() -> None:
    """브라우저 탭이 아니라 앱 창으로 연다. 경량판은 창이 닫히면(브라우저 프로세스 종료) 서버도 끝낸다."""
    if os.environ.get("MABI_NO_BROWSER"):
        return
    url = f"http://127.0.0.1:{PORT}"

    if LITE_REUSE:
        global _app_profile
        _app_profile = os.path.join(BASE, _APP_PROFILE)   # 창은 이미 떠 있고(페이지가 새 포트로 이동) 감시만 이어간다
    else:
        threading.Thread(target=lambda: _launch_app_window(url), daemon=True).start()
    if LITE:
        threading.Thread(target=_lite_watchdog, daemon=True).start()


_last_ui = time.time()   # UI 가 마지막으로 요청한 시각
_bye_at = 0.0            # 페이지가 닫히며 보낸 신호 시각
_start_at = time.time()
_hold_lock = threading.Lock()
_holds = 0               # 열려 있는 /api/hold 연결 수 (= 살아 있는 창 수)
_had_hold = False
_last_hold_close = 0.0


_app_profile = ""        # 앱 창을 띄운 브라우저의 전용 프로필 경로 ("" = 기본 브라우저 탭으로 열었음)


def _app_window_alive() -> bool | None:
    """전용 프로필로 띄운 Edge/Chrome 창이 아직 있는가. 앱 창 모드가 아니면 None(판단 불가).
    Edge 는 한동안 안 쓴 창을 절전(슬리핑 탭)시키며 연결을 끊을 수 있어, 연결 끊김만으로는 '창 닫힘'을 단정할 수 없다 (실측).
    Chromium 은 프로필마다 제목이 프로필 경로인 메시지 전용 창(클래스 Chrome_MessageWindow)을 하나 둔다 — 그 창을 user32 로 찾는다.
    (예전엔 PowerShell 로 프로세스 명령줄을 뒤졌는데, 백신이 'PowerShell 실행' 행동으로 오탐하는 요인이라 뺐다.)"""
    if not _app_profile:
        return None
    try:
        import ctypes
        from ctypes import wintypes as w
        u = ctypes.windll.user32
        u.FindWindowExW.argtypes = [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]; u.FindWindowExW.restype = w.HWND
        u.GetWindowTextLengthW.argtypes = [w.HWND]; u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        want = os.path.normcase(os.path.normpath(_app_profile))
        h = None
        for _ in range(4096):
            h = u.FindWindowExW(w.HWND(-3), h, "Chrome_MessageWindow", None)   # HWND_MESSAGE 아래의 메시지 전용 창들
            if not h:
                break
            n = u.GetWindowTextLengthW(h)
            if n <= 0:
                continue
            buf = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, buf, n + 1)
            if os.path.normcase(os.path.normpath(buf.value)) == want:
                return True
        else:
            return None   # 상한까지 뒤졌는데 끝을 못 봄 → 판단 불가 (닫힘으로 단정하지 않는다)
        return False
    except Exception as e:
        print(f"[lite] window check failed: {e}", flush=True)
        return None


HOLD_TICK = 1.0          # /api/hold 가 한 바이트를 보내는 간격 = 끊김을 알아채는 데 걸리는 최대 시간
HOLD_LAG = HOLD_TICK + 2.0   # bye 뒤 이 시간 안에 끊기면 그 bye 때문에 끊긴 것으로 본다


def app_gone_step(now: float, holds: int, bye_at: float, last_close: float,
                  gap: float = 1.5, lag: float = HOLD_LAG) -> bool:
    """창이 **닫혔다고 알려 왔는가**. 종료 판정과 별개로, 밴드를 먼저 내리는 데만 쓴다 (모비웍스 방식).

    판정 기준은 「연결이 끊겼나」가 아니라 **「닫는다고 알려 왔나」**다:
    - 창을 닫으면 pagehide → /api/bye 가 온다 → 내린다.
    - **Edge 가 탭을 재우면 hold 만 끊기고 bye 는 안 온다** → 창은 살아 있으므로 내리지 않는다.
      이걸 구분하지 않으면 가만히 둔 오버레이가 저절로 사라진다 (모비웍스가 짚은 제일 위험한 자리).
    - F5 도 bye 를 보내지만 1초 안에 다시 붙어 gap 을 못 넘긴다 → 안 내려간다.
    끊김이 bye 보다 **한참 뒤**면 그 끊김은 이 bye 와 무관하다(오래된 bye + 절전) → 내리지 않는다.
    「한참」의 기준은 우리가 끊김을 알아채는 데 걸리는 시간이다(HOLD_TICK + 여유) — 모비웍스는
    1초를 쓰지만 그쪽은 끊김을 즉시 안다. 우리는 hold 가 한 바이트를 보낼 때 알아채므로,
    그 간격보다 짧게 잡으면 **정상적인 닫힘이 전부 「알리지 않고 끊김」으로 걸러진다**(실측: 안 내려감).
    sendBeacon 이 비동기라 bye 가 끊김보다 늦게 닿는 경우는 max() 가 알아서 처리한다."""
    if holds > 0 or bye_at <= 0:
        return False
    if last_close > bye_at + lag:
        return False
    return (now - max(bye_at, last_close)) > gap


def _app_gone() -> bool:
    with _hold_lock:
        h, lc = _holds, _last_hold_close
    return app_gone_step(time.time(), h, _bye_at, lc)


def _lite_watchdog() -> None:
    """경량판 종료 판정.
    1) 페이지가 /api/hold 연결을 계속 열어 둔다 — 창이 닫히거나 브라우저가 죽으면 끊긴다 (타이머와 무관, 최소화해도 유지).
    2) 연결이 끊겨도 바로 끝내지 않고, 전용 프로필의 브라우저 프로세스가 아직 있으면(절전된 창) 살아 있는 것으로 본다.
       프로세스까지 없어졌을 때만 종료. 앱 창 모드가 아니면(기본 브라우저 탭) 연결이 60초 이상 없을 때 종료.
    3) 창이 90초 안에 한 번도 붙지 않으면(브라우저를 못 띄운 경우) 고아로 남지 않게 끝낸다."""
    last_check = 0.0; misses = 0
    while True:
        time.sleep(1.0)
        now = time.time()
        with _hold_lock:
            h, had, lc = _holds, _had_hold, _last_hold_close
        gone_for = now - max(lc, _bye_at) if (had or _bye_at) else 0.0
        if (had or _bye_at) and h == 0 and gone_for > 4.0 and now - last_check >= 5.0:
            last_check = now
            alive = _app_window_alive()
            misses = misses + 1 if alive is False else 0
            if misses >= 2:   # 5초 간격 2회 연속 없음 (Edge 가 스스로 재시작하는 짧은 구간에 오판하지 않게)
                _say("[lite] window closed — exiting"); _shutdown()
            if alive is None and gone_for > 60.0:
                _say("[lite] no window connection for 60s — exiting"); _shutdown()
        if not had and now - _start_at > 90.0:
            _say("[lite] no window attached in 90s — exiting"); _shutdown()


_mutex = None


def _lite_release() -> None:
    """뮤텍스와 lite.json 을 놓는다 (업데이트로 새 프로세스에 자리를 넘길 때)."""
    global _mutex
    try:
        if _mutex:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_mutex); _mutex = None
        os.remove(LITE_FILE)
    except OSError:
        pass


def _lite_single_instance() -> bool:
    """경량판 중복 실행: 이미 떠 있으면 그 포트로 창만 하나 더 열고 False. 처음이면 lite.json 에 포트를 적고 True."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        global _mutex
        _mutex = k32.CreateMutexW(None, False, "Local\\MobiFolioLite" + UI_NAME)
        if k32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            try:
                with open(LITE_FILE, encoding="utf-8") as f:
                    port = int(json.load(f).get("port", 0))
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2).read()
                _launch_app_window(f"http://127.0.0.1:{port}")
                return False
            except Exception:
                pass   # 죽은 기록이면 그냥 새로 뜬다
        with open(LITE_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": PORT, "pid": os.getpid()}, f)
    except Exception as e:
        print(f"[lite] single-instance check failed: {e}", flush=True)
    return True


_open_browser = _open_window   # 이전 이름 호환


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False   # 같은 사용자의 다른 프로세스가 포트를 가로채지 못하게 (SO_REUSEADDR 끔 + 배타 사용)
    daemon_threads = True
    _slots = threading.BoundedSemaphore(32)   # 동시 연결 상한 — 느린 연결로 스레드를 무한히 잡아 두지 못하게

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _reexec_if_inherited_mei() -> None:
    """옛 버전(0.1.x)이 업데이트로 우리를 띄울 때 PyInstaller 내부 변수를 물려줬으면, 우리는 옛 임시 폴더를 빌려 쓰는 상태다
    (곧 삭제되어 화면 파일이 사라진다). 그 경우 깨끗한 환경으로 자신을 다시 실행하고 끝난다."""
    if not (FROZEN and LITE_REUSE):
        return
    old_mei = os.environ.get("MABI_OLD_MEI", ""); mine = getattr(sys, "_MEIPASS", "")
    if not (old_mei and mine and os.path.normcase(os.path.abspath(old_mei)) == os.path.normcase(os.path.abspath(mine))):
        return
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    try:
        import subprocess
        subprocess.Popen([sys.executable], cwd=os.path.dirname(sys.executable), env=env, close_fds=True,
                         creationflags=0x00000008 | 0x00000200 | 0x01000000)
        _say("update: inherited temp dir detected — re-executing cleanly")
    except OSError as e:
        _say(f"update: re-exec failed: {e}")
        return
    os._exit(0)


def main() -> None:
    global _srv
    _reexec_if_inherited_mei()
    os.makedirs(store.DATA_DIR, exist_ok=True)
    if LITE and not _lite_single_instance():
        return
    try:
        srv = _Server(("127.0.0.1", PORT), H)
        _srv = srv
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):
            # 이미 떠 있음(포트 사용 중) → 창만 다시 연다
            print(f"[mobifolio] port {PORT} busy — opening browser only", flush=True)
            _open_browser(); time.sleep(1.5)
            return
        print(f"[mobifolio] port {PORT} bind failed: {e} (errno {e.errno}) — 예약 포트(Hyper-V/WinNAT)일 수 있습니다", flush=True)
        sys.exit(1)
    _say(f"[mobifolio] http://127.0.0.1:{PORT}  cli={cli.find_exe()}  frozen={FROZEN} lite={LITE} reuse={LITE_REUSE} pid={os.getpid()} v{VERSION}")
    _cleanup_bak()
    threading.Thread(target=_watch, daemon=True).start()   # 연주 조회·다음 곡 넘김은 여기 한 곳에서만
    _watch_parent()
    _ov_start()
    _open_browser()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
