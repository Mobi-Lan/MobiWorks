"""로컬 웹 서버 (127.0.0.1). UI 는 ui/index.html, API 는 /api/*. 표준 라이브러리만 쓴다.

MobiFolio 와 같은 구조:
- 경량판(LITE)은 exe 를 바로 실행하면 스스로 빈 포트·토큰을 만들고 Edge/Chrome 앱 창을 띄운다.
- 모든 API 는 출처 검사(_guard) + 실행마다 다른 토큰을 요구한다.
- 자동 업데이트는 경량판만: latest.json(https) → SHA256 검증 → 실행 중 exe 를 .bak 으로 밀고 제자리 교체 → 같은 토큰으로 재실행.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import select
import socket
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

APP = "mobiworks"          # /api/health 식별자 · X-Requested-With 값
# **보이는 이름과 파일 이름을 나눠 둔다.**
#  · APP_NAME 은 *파일·폴더* 이름이다 — `%LOCALAPPDATA%\MobiWorks`, `MobiWorksLite.exe`,
#    뮤텍스 이름. 바꾸면 사용자의 기존 자료를 못 찾는다. 함부로 건드리지 않는다.
#  · APP_TITLE 은 *화면에 보이는* 이름이다 — 페이지 제목 = 앱 창 제목이고, 창을 찾을 때
#    쓰는 것도 이것이다 (앱 이름은 바뀔 수 있으니 이름을 박아 두지 않고 이 값으로 찾는다).
APP_NAME = "MobiWorks"
APP_TITLE = "모비웍스"
VERSION = "1.0.10"

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
# **여기서 먼저 올린다** — 로그 파일을 열기 전에 자료 폴더를 정해야 하고, 그 규칙(옛 이름
# 이사 포함)은 datadir 한 곳에 있다. 나머지 지역 모듈 import 는 아래 그대로 둔다.
sys.path.insert(0, HERE)
import datadir                # noqa: E402
import runmode                # noqa: E402  (실행 방식 — exe · 임베디드 파이썬 · 개발 소스)
# 임베디드 판(서명된 pythonw.exe 가 우리 .py 를 돌림)은 **배포판처럼** 군다 — 단 FROZEN 은 PyInstaller 만의 뜻 그대로.
# `sys.executable`·`_MEIPASS` 에 기대는 자리(자동 업데이트의 exe 교체 등)는 FROZEN 만 본다 (runmode.py).
EMBED = runmode.EMBED
RELEASE = FROZEN or EMBED
_DEV_ENV_OK = (not RELEASE) or os.environ.get("MOBIW_DEV") == "1"   # 배포판은 개발용 환경변수(MOBIW_DATA_DIR)를 무시
BASE = (os.environ.get("MOBIW_DATA_DIR") if _DEV_ENV_OK else None) or (
    datadir.migrate(os.environ["LOCALAPPDATA"], APP_NAME) if os.environ.get("LOCALAPPDATA")
    else (os.path.dirname(sys.executable) if FROZEN else HERE))   # 데이터·로그 위치 (store.user_base 와 같은 규칙)
os.makedirs(BASE, exist_ok=True)
RES = getattr(sys, "_MEIPASS", HERE)                              # 묶인 리소스(ui/) 위치
LOG_MAX = 2_000_000   # 로그 한 세대 상한(바이트). 넘으면 `.1` 로 밀고 새로 쓴다 — 두 벌(지금 + .1)만 남는다


class _RotatingLog:
    """stdout·stderr 자리에 앉는 로그 파일. **실행 중에** 상한을 넘으면 `<이름>.1` 로 밀고 새로 쓴다.

    예전에는 기동할 때만 2MB 를 재서 지웠다 — 며칠 켜 두면 끝없이 자랐고, 지울 때는 직전 기록이
    통째로 사라졌다(문제가 난 바로 그 기록). 이제는 쓰는 자리에서 재고, 한 세대(`.1`)를 남긴다.

      · **잠금 하나**가 쓰기·회전·flush 를 모두 감싼다 — 여러 스레드가 `_say`·print 를 부르므로,
        회전(닫기 → 밀기 → 열기) 사이에 끼어든 쓰기가 닫힌 파일에 가서 사라지지 않게 한다.
      · 한 번의 write 는 한 파일에만 간다 — 줄이 두 파일로 찢어지지 않게 **쓰기 전에** 잰다.
      · Windows 는 **다른 프로세스가 열어 둔 파일**의 이름을 못 바꾼다(업데이트 중에는 옛·새 인스턴스가
        같은 로그를 연다). 밀기가 실패하면 지우기 → 그것도 안 되면 비우고(`w`) 새로 쓰며, 그 사실을
        새 파일 **첫 줄**에 적는다. 셋 다 안 되면 이어 쓰고 다음 상한까지 다시 시도하지 않는다."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, path: str, limit: int = LOG_MAX):
        self.path = path
        self.limit = int(limit)
        self._cap = self.limit       # 이번 세대의 상한 (회전을 통째로 못 한 때만 한 번 더 늘린다)
        self.lock = threading.RLock()
        self._f = None
        self._size = 0
        with self.lock:
            self._open("a")          # 못 열면 OSError — 부르는 쪽이 임시 폴더로 돌린다
            if self._size > self._cap:   # 기동 때 이미 넘어 있으면 그 자리에서 민다
                self._rotate()

    def _open(self, mode: str) -> None:
        self._f = open(self.path, mode, encoding="utf-8", errors="replace", buffering=1)
        try:
            self._size = os.path.getsize(self.path)
        except OSError:
            self._size = 0

    def _rotate(self) -> None:
        """잠금 안에서만 부른다."""
        old, self._f = self._f, None
        try:
            if old:
                old.close()
        except Exception:
            pass
        name = os.path.basename(self.path)
        note = ""
        try:
            os.replace(self.path, self.path + ".1")
            mode = "a"
        except OSError as e:
            why = f"{type(e).__name__}: {e}"
            try:
                os.remove(self.path)
                mode = "a"
                note = f"[log] 이전 로그를 {name}.1 로 밀지 못해 지웠습니다 ({why})"
            except OSError:
                mode = "w"
                note = f"[log] 이전 로그를 {name}.1 로 밀지도 지우지도 못해 비우고 새로 씁니다 ({why})"
        try:
            if mode == "w":
                # **비우는 핸들로 계속 쓰지 않는다**. `w` 핸들은 제 위치에 쓰므로, 같은 파일에 덧붙이는
                # 다른 인스턴스(업데이트 중 옛·새 판)의 줄을 **제 줄로 덮어썼다**(검사로 재현). 비우기만 하고
                # 덧붙이기(`a`, 쓸 때마다 끝으로)로 다시 연다.
                open(self.path, "w", encoding="utf-8").close()
            self._open("a")
        except OSError as e:
            self._open("a")          # 비우기조차 못 하면 이어 쓴다 — 기록을 잃는 것보다 낫다
            note = f"[log] 로그를 새로 시작하지 못해 이어 씁니다 ({type(e).__name__}: {e})"
        self._cap = self.limit if self._size <= self.limit else self._size + self.limit
        if note:
            self._write_raw(f"[{time.strftime('%H:%M:%S')} {os.getpid()}] {note}\n")

    @staticmethod
    def _bytes(s: str) -> int:
        """파일에 실제로 쓰이는 바이트 수 — 텍스트 모드는 줄바꿈을 os.linesep(Windows 는 CRLF 두 바이트)로 바꿔 쓴다."""
        return len(s.encode("utf-8", "replace")) + s.count("\n") * (len(os.linesep) - 1)

    def _resync(self) -> None:
        """잠금 안에서만 부른다. 버퍼를 비우고 이 핸들이 가리키는 파일의 실제 크기로 `_size` 를 맞춘다."""
        try:
            self._f.flush()
            self._size = os.fstat(self._f.fileno()).st_size
        except (OSError, ValueError):
            pass                     # 못 재면 제 셈 그대로 — 예전 동작

    def _write_raw(self, s: str) -> int:
        n = self._f.write(s)
        self._size += self._bytes(s)
        return n

    def write(self, s) -> int:
        if not s:
            return 0
        s = str(s)
        with self.lock:
            try:
                # 회전에서 다시 열기가 **두 번 다** 실패하면 `_f` 가 None 으로 남는다. 그대로 두면 다음
                # 쓰기가 AttributeError 를 내는데, 그건 아래 except 도 `_say` 도 안 잡아서 **로그 한 줄이
                # 요청 처리 스레드를 죽인다** (print·traceback 도 같은 길). 쓰기마다 다시 열어 보고, 안 되면 버린다.
                if self._f is None:
                    self._open("a")
                if self._size > 0 and self._size + self._bytes(s) > self._cap:
                    # **밀기 전에 실제 크기를 다시 잰다**. `_size` 는 **제가 쓴 것만** 센다 — 업데이트 중에는
                    # 옛·새 인스턴스가 같은 파일에 쓰고, 새 쪽이 이미 비웠는데도 옛 쪽은 제 셈만 믿고 또 비워
                    # 새 인스턴스의 기동 기록과 「비우고 새로 씁니다」 안내 줄을 몇 초 만에 지웠다(검사로 재현).
                    # fstat 은 **이 핸들이 쓰는 파일**의 크기다 — 남이 쓴 줄도 들어가고, 남이 비웠으면 작아진다.
                    self._resync()
                    if self._size > 0 and self._size + self._bytes(s) > self._cap:
                        self._rotate()
                return self._write_raw(s)
            except (OSError, ValueError):   # 디스크가 찼거나 닫혔다 — 로그 때문에 스레드가 죽지는 않게
                return 0

    def flush(self) -> None:
        with self.lock:
            try:
                if self._f:
                    self._f.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        with self.lock:
            if self._f is None:      # 다시 열기에 실패한 사이 — AttributeError 대신 파일 객체 관례대로
                raise ValueError("log file is not open")
            return self._f.fileno()

    def close(self) -> None:
        with self.lock:
            try:
                if self._f:
                    self._f.close()
            except OSError:
                pass

    @property
    def closed(self) -> bool:
        return self._f is None or self._f.closed

    def reconfigure(self, **kw) -> None:
        """TextIOWrapper 흉내 — 이미 utf-8 이다. 폴리오 엔진이 `sys.stdout.reconfigure(encoding="utf-8")` 를
        부르는데 이 객체에 없어서 exe 에서 엔진 import 가 죽었다(연주 감시 없음). 없는 메서드는 조용히 받는다."""
        return None


if FROZEN or EMBED:
    # --noconsole(임베디드 판은 pythonw) 이면 stdout 이 없다 → 로그를 사용자 폴더 파일로 (실행 중 2MB 마다 `.1` 로 회전)
    _logp = os.path.join(BASE, "mobiworks.log")
    try:
        _logf = _RotatingLog(_logp)
    except OSError:   # 쓸 수 없으면(읽기 전용 폴더 등) 임시 폴더로
        _logf = _RotatingLog(os.path.join(os.environ.get("TEMP", "."), "mobiworks.log"))
    sys.stdout = sys.stderr = _logf
elif sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
import broadcast              # noqa: E402  (방송 모드 방 상태 — 손님 길·우편함·터널 감시. CLI 를 부르지 않는다)
import categories             # noqa: E402
import cli_transport as cli   # noqa: E402
import ledger                 # noqa: E402  (날개·산출 장부 — 파일만 읽고 쓴다)
import mailseal               # noqa: E402  (우편함 봉투 — 터널 주소를 인증키로 봉한다)
import overlay                # noqa: E402  (게임 오버레이 — tkinter 창. CLI 를 부르지 않는다)
import presets                # noqa: E402  (큐 프리셋·공유 코드)
import recipedb               # noqa: E402
import shortcut               # noqa: E402  (임베디드 판의 시작 메뉴·바탕화면 바로가기 — .cmd 에는 아이콘을 못 붙인다)
import updater                # noqa: E402  (임베디드 판의 제자리 업데이트 — zip 을 받아 app\·python\ 을 바꾼다)
import store                  # noqa: E402
import viewmodel              # noqa: E402  (화면 상태 계산 — 판단은 서버에서)
import work                   # noqa: E402
import workqueue              # noqa: E402  (이름이 queue 면 표준 라이브러리 queue 를 가린다)

# 경량판(LITE): 셸 없이 이 exe 를 바로 실행한 경우 — 스스로 빈 포트·토큰을 만들고 앱 창을 띄운다
LITE_REUSE = os.environ.get("MOBIW_LITE_REUSE") == "1"   # 자동 업데이트로 다시 뜬 경우: 창은 이미 있으니 새로 띄우지 않는다
# `MOBIW_NO_BROWSER` 가 경량판 모드를 **끄는 것은 의도다** — 「창을 안 띄운다」가 아니라
# 「창 수명 규칙이 적용되지 않는 검증 모드」라는 뜻이다. 두 가지가 이 위에 얹혀 있다:
#   · 시험 10여 개 파일이 이 변수로 서버를 띄운다. 경량판이면 임의 포트 + 토큰 필수가 된다.
#   · 「창이 90초 안에 안 붙으면 종료」(_lite_watchdog) 가 걸린다 — 창을 안 띄우는 실행은 자살한다.
# 대신 **창 없이 경량판 경로를 시험할 수 없다**는 구멍이 남는다. 첫 실행 확인은 창을 띄우고
# 한다. 이 줄을 "정리"하지 말 것.
LITE = RELEASE and not os.environ.get("MOBIW_PARENT_PID") and (not os.environ.get("MOBIW_NO_BROWSER") or LITE_REUSE)


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = int(os.environ.get("MOBIW_PORT") or (_free_port() if LITE else 19995))
TOKEN = os.environ.get("MOBIW_TOKEN", "") or (secrets.token_hex(24) if LITE else "")   # 실행마다 만드는 비밀 — 있으면 모든 API 가 이 값을 요구한다
PARENT = os.environ.get("MOBIW_PARENT_PID", "")
LITE_FILE = os.path.join(BASE, "lite.json")   # 경량판이 떠 있는 포트 (두 번째 실행이 창만 다시 열 때 씀)
# 앱 창의 Edge 전용 프로필. 경량판 exe 와 개발 서버(python server.py)가 같은 프로필을 쓰면 exe 의 창 생존 확인
# (_app_window_alive: 그 프로필의 Chrome_MessageWindow 를 찾음)이 이미 서버가 죽은 개발용 창을 보고 "창 살아 있음"으로
# 오판한다 — 실제로 exe 가 자기 창 없이 계속 살아 있었고 사용자는 죽은 개발 창에서 「Failed to fetch」를 봤다. 그래서 분리한다.
APP_PROFILE = os.path.join(BASE, ".appwindow-profile" if LITE else f".appwindow-profile-dev-{PORT}")
UPDATE_DIR = os.path.join(BASE, "update")     # 받은 새 exe
MAX_UPDATE_BYTES = 200 * 1024 * 1024
MAX_BODY = 1_000_000
UI_DIR = os.path.join(RES, "ui")
TOKEN_MARK = b"__MOBIWORKS_TOKEN__"
# 화면이 「이 PC 가 내준 것인가」를 아는 자리. 바깥 사이트(CDN)에 올린 사본은 이 치환을 하지
# 않으므로 자리표시자가 남고, 화면은 그것을 「밖」으로 친다 (ui/js/net.js).
# **토큰과 따로 두는 이유**: 개발 모드는 토큰이 비어 있어 토큰만으로는 안팎을 못 가린다.
MODE_MARK = b"__MOBIWORKS_MODE__"
TOKEN_HEADER = "X-MobiWorks-Token"
_ok_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"}

# ── 밖에서 접속 (모바일) ─────────────────────────────────────────
# 이 앱은 원래 **이 PC 안에서만** 쓰는 것이라, 페이지가 열리면 실행 토큰이 HTML 에 박혀 나간다.
# 밖으로 열 때 그대로 두면 **주소만 알면 누구나** 큐를 돌린다 — 그리고 우리 실행은
# **정령의 날개를 쓴다**(1회 = 5개, 되돌릴 수 없다). 그래서 밖에서 온 요청은 토큰 대신
# 인증키로 받은 쪽지를 요구하고, **기본은 보기만**으로 묶는다.
#
# 분류 기준은 이름이 아니라 「무슨 일을 하느냐」다.
REMOTE_TTL = 12 * 3600    # 쪽지 수명 — 12시간
REMOTE_MAX_TRY = 5        # 인증키를 이만큼 틀리면
REMOTE_LOCK = 600.0       # 10분 잠근다 (모비폴리오는 5분 — 우리는 재화가 걸려 10분)
REMOTE_KEY_MIN = store.REMOTE_KEY_MIN   # 인증키 최소 길이 (8 · 모비폴리오는 6) — 규칙은 store 한 곳에 둔다

# **(가) 이 PC 의 창·프로세스를 건드린다 / (나) 밖으로 나가는 문 자체를 여닫는다 /
#   (다) 이 PC 의 파일을 고르거나 게임에 직접 묻는다** — 범위가 무엇이든 밖에서는 절대 안 된다.
REMOTE_NEVER = {
    "/api/quit",            # 앱을 끈다
    "/api/bye",             # **창이 닫혔다는 신호. 밖에서 부르면 앱이 꺼진다** (모비폴리오가 실제로 열어 뒀던 구멍)
    "/api/hold",            # 창 살아있음 신호 — 끊기면 종료 판정
    "/api/update/apply",    # 이 PC 에 설치한다
    "/api/update/check",    # 이 PC 가 `update_url` 로 **밖에 나간다** — 밖(read)에서 부르면 남이 이 PC 를 나가게 한다
                            # (범위와 무관하게 막는다)
    "/api/cli_test",        # 지정한 경로로 **프로세스를 띄운다**
    "/api/overlay",         # 오버레이 창 여닫기·테스트 표시·위치 초기화 — **이 PC 의 창**이다
    "/api/backup",          # 이 PC 의 설정·재생목록을 zip 으로 내준다 — **이 PC 의 파일**이다
    "/api/report",          # 문제 신고 zip — 이 PC 의 로그·설정·스레드 스택이다
    "/api/shortcut/desktop",   # 이 PC 의 바탕화면에 파일을 만든다 (임베디드 판 설정 → 일반)
    "/api/recipes/export",  # 관찰 DB 원본 파일 내려받기 — 공용 문을 쓰므로
                            # 밖의 쪽지로도 열리게 되지 않도록 여기 적는다 (보기는 `/api/recipes` 로 된다)
    "/api/restore",         # zip 으로 이 PC 의 파일을 덮는다 — 밖에서 설정을 통째로 바꾸는 길이 된다
    "/api/ui/state",        # 이 PC 의 앱 창이 **다음에 어느 화면으로 뜰지** — 폰이 바꿀 일이 아니다
    "/api/sync",            # 게임을 읽어 화면을 갱신하는 길 — **폰은 PC 가 읽은 값만 본다**.
                            # 화면은 이미 안 부르고(test_phone_reads_pc), 문도 닫는다. 명령(큐·시작·연주)은 이 길과 무관하다
}
# 앞자리로 걸러야 하는 것 (집합으로는 못 잡는다).
#   `/api/remote/` — **문 자체를 여닫는 길**(터널·인증키·우편함·잠금 풀기·기기 목록).
#     밖에서 열게 두면 자물쇠를 채운 의미가 없다. 로그인(`/api/remote/login`)만 예외이며,
#     그건 애초에 `_guard` 를 타지 않는다.
#   `/api/cli/` — 게임에 바로 묻는 디버그 통로. 읽기 전용이어도 밖에 둘 이유가 없다.
# **앞자리로 막는 이유**: 길이 아직 없어도 규칙이 먼저 선다. 집합에 개별 경로를 적어 두면
# 그 길이 생기기 전까지는 「막았다고 착각」만 하게 된다 (검사가 실제로 그걸 잡았다).
# ── 폴리오 길 ───────────────────────────────────────────────────────────
# 폴리오 서버(`folio/engine.py`)는 **불러 쓰는 모듈**이고, 그쪽 길은 전부
# `/api/folio/…` 아래에 붙는다. 접두어가 필요한 까닭은 **이름이 겹치기 때문**이다 —
# `/api/queue` 가 우리는 **작업 큐**, 그쪽은 **재생목록**이다. 평평하게 붙이면 한쪽이
# 다른 쪽을 가린다.
#
# **문은 우리 것 하나를 쓴다.** 아래 위임은 `_guard()` 를 지난 뒤에 일어난다 —
# 그쪽에도 문이 있지만 두 문을 따로 두면 언젠가 한쪽만 고친다.
FOLIO_PREFIX = "/api/folio/"

# **넘기지 않는 길.** 합친 뒤에는 앱이 하나이고, 아래 일은 **모비웍스가** 한다.
# 그쪽 것을 그대로 넘기면 같은 일을 하는 기계가 **둘**이 되고, 둘은 반드시 갈라진다.
#
#   갱신    `update_url` 은 **같은 설정 키**다. 둘 다 그 주소를 보고 받아서
#           **서로의 exe 를 덮는다** (그쪽은 `MobiFolio.download.part` 로 받는다).
#   수명    창이 닫혔다·살아있다·끄기 — 창과 포트를 쥔 쪽이 판단해야 한다.
#   문      밖에서 들어오는 길은 우리 것 하나다 (그쪽 문은 우리 `_guard` 뒤에 있다).
#   직결    지정한 경로로 **프로세스를 띄운다.**
#
# 이름 뒤에 `/` 가 붙은 것은 앞자리다.
FOLIO_NEVER = {"quit", "bye", "hold", "cli_test"}

# **밖에서 읽어도 되는 폴리오 길.** 폰 화면이 「지금 뭐가 나오는지」를 보려면 이것들이
# 필요하다 (레일의 골드 점, 재생목록, 주변 연주).
#
# **「있으면 통과」 목록이라 줄지 않고 아무 소리도 안 난다** — 그래서 두 가지를 검사가
# 지킨다: 적힌 길이 그쪽에 **실제로 있는가**, 그리고 여기 없는 폴리오 길은 **정말 막히는가**.
FOLIO_READ_OK = {
    "now", "state", "playlists", "presets", "scores", "instruments",
    "artists", "near", "ensemble", "log", "queue", "health",
    # ── 폰 = PC ── 미니 화면이 뜨려면 **넷이 다 와야** 한다
    # (scores·playlists·instruments·settings — index.html `boot`). settings 가 빠지면
    # 폰에서 폴리오가 빈 화면 + 「서버에서 데이터를 받지 못했습니다」가 된다.
    "settings",       # 폴리오 키만 나간다 (`FOLIO_SETTINGS_REMOTE_OK` ∩ `_remote_setting_ok`, CLI 조회 없음)
    "songs", "song",  # 곡 번호 DB · 곡 상세 시트 (읽기)
    "activity",       # 게임의 지금 연주 상태 — 밖에서는 **PC 감시 스레드가 마지막으로 본 값**(engine `_activity_cached`, CLI 안 탐)
    "social",         # 연주 인사 화면의 행동·표정 목록 — 밖에서는 **PC 가 마지막으로 받아 둔 목록**(engine `_SOCIAL`, CLI 안 탐)
    "covers",         # 커버 고르기 시트의 칸 — 이 PC 의 폴더 경로(dir·folder·folders)는 밖에 싣지 않는다
    "bc/state",       # 방송 창 — 방 상태 (모바일 리모컨에서도 같은 화면 · read 는 보기만)
}

# **밖에서 연주를 조작할 수 있는 길.** 이것은 새로 여는 것이 아니라 **되돌리는 것**이다 —
# 모비폴리오는 폰 작업을 이미 끝내 두었고, 그쪽 `REMOTE_PLAY_OK`(engine.py) 가 바로 이
# 여덟이다. 합치면서 `/api/folio/` 를 앞자리째 NEVER 에 넣는 바람에 **되던 것이 죽었다.**
# 그래서 목록을 **그쪽에서 그대로 베껴 온다** — 우리가 다시 고르지 않는다 (고르면 그건
# 복구가 아니라 새 설계다).
#
# **범위는 `edit` 부터.** 연주는 **날개가 안 든다** — 그러니 재화를 쓰는
# `run` 이 아니라 「값이 안 드는 것」 칸에 놓는다. 그쪽 `play` 가 우리 `edit` 에 해당한다.
#   그쪽 play  = 재생 조작만        → 우리 edit
#   그쪽 full  = 그 밖의 전부       → **안 가져온다.** 그 안에는
#                `/api/folio/settings` 가 들어 있다 — 그 길은 우리 설정 필터(`overlay*`
#                걸러내기·`remote_scope` 지키기)를 **타지 않으므로** 밖에 두면 폰이 제
#                권한을 스스로 올릴 수 있다.
FOLIO_PLAY_OK = {
    "play", "stop",   # `sync`(게임 읽기 갱신)는 뺐다 — 폰은 PC 가 읽은 값만 본다
    "queue", "queue/step", "queue/at", "queue/ask", "queue/clear",
    "queue/inst",     # 대기열의 한 곡에 악기 저장 (미니 「현재곡 칸」) — 그쪽 REMOTE_PLAY_OK 와 같은 목록
    "queue/next", "queue/append",   # 줄 메뉴 「다음에 재생」·「현재 재생목록 끝에 담기」 — 그쪽 REMOTE_PLAY_OK 와 같은 목록
}

# **밖에서 폴리오를 「고칠」 수 있는 길** (범위 `edit` 부터) — 폰 사이트는 PC 앱의
# 리모컨이라 PC 화면과 똑같이 된다. 위 `FOLIO_PLAY_OK` 는 그쪽에서
# 베낀 목록이라 건드리지 않고, 새로 여는 것은 여기 따로 적는다 (무엇을 언제 열었는지 보이게).
# 값이 안 들고(날개·재화 없음) **이 PC 의 창·파일·밖으로 나가는 일이 아닌 것**만이다.
#   settings  — **폴리오 키만** (`FOLIO_SETTINGS_REMOTE_OK`). 그 밖의 키는 버리고, 남는 게 없으면 403
#   songs     — 곡마다 커버 고르기 (`op:"cover"` — 이미 폴더에 있는 그림의 이름만. 올리기·온라인 찾기는 NEVER)
# 여전히 이 PC 에서만: overlay(창) · opening(연출 창) · covers POST(폴더 열기·지우기) ·
# covers/upload·search·fetch · artists · update/ · remote/ · cli/
FOLIO_EDIT_OK = {
    "playlists",      # 재생목록 만들기·이름·순서·지우기 (미니의 목록 편집)
    "instrument",     # 악기 장착 (미니 악기 시트 「장착」)
    "ens",            # 선택 막대 「인원」 — 악보별 합주 인원
    "greet/assign",   # 곡 상세 「연주 인사」
    "songs",          # 곡 상세 「커버」 — 있는 그림 중에서 고르기만
    "presets",        # 연주 인사 화면의 프리셋 (항목·기본·곡별) — 폰(edit)이 프리셋 `chat` 을 바꿀 수 있다 (의도한 것이다)
    "settings",       # 폴리오 설정 네 갈래 + 연주 인사 — `FOLIO_SETTINGS_REMOTE_OK` 만
    # 방송 창의 조작 (모바일 리모컨에서도 같은 화면) — 값이 안 들고 이 PC 의 창·파일이 아니다.
    # `bc/window`(방송 창 열기·앞으로)는 **여기 없다** — 이 PC 의 창이다.
    "bc/on", "bc/off", "bc/code", "bc/pl", "bc/cfg", "bc/ok", "bc/no", "bc/block",
    # `greet/test`(연주 인사 「보내 보기」)는 **여기 없다** — 게임 채팅으로 나가 남에게 보인다. 어느 범위든 이 PC 에서만.
    # 폰 화면은 그 단추를 감춘다 (folio/net.js `MF.CAN.greetTest`).
}

# 밖에서 `/api/folio/settings` 로 **보고 바꿀 수 있는 키** — 이 목록이 전부다 (거절이 기본).
# 폴리오 설정 화면(ui/folio/settings.html `SAVE_KEYS` + 기본 악기)·연주 인사 화면(greet.html `SAVE_KEYS`)·
# 미니 화면(반복·셔플·악기 고정·연출 켜기)이 쓰는 키. `cli_exe`·`overlay*`·`remote*`·`update_url` 은
# 여기 없다 — 검사가 이 목록의 키가 전부 `_remote_setting_ok` 를 지나는지도 본다.
# `folio_dismount_by_craft` 도 없다 — 켜 두면 폰의 `play`(edit) 가 탈것에서 내리려고 제작 하나를 걸어 **날개 5** 가 든다.
# 「edit = 값이 안 드는 것」이라 밖에서는 못 바꾼다 — `REMOTE_SETTINGS_WINGS` 에 있다.
FOLIO_SETTINGS_REMOTE_OK = frozenset({
    "gap_sec", "advance_margin", "default_inst", "inst_pin", "stop_before_play",
    "ensemble", "opening", "opening_theme",
    "ui_theme",                       # 화면 테마 — 폰은 **읽어서 따른다**(기본). 폰에서 고르면 그 폰에만 적용하고 보내지 않는다 (ui/js/theme.js)
    "ov_scale", "ov_alpha",           # 폴리오 밴드의 크기·불투명도 (창을 여닫는 것은 아니다 — 그건 POST /api/overlay, NEVER)
    "auto_sync", "repeat", "shuffle",
    "greet_on", "greet_lead", "greet_step", "greet_end_lead", "greet_listen", "greet_listen_gap",
})
FOLIO_NEVER_PREFIX = ("update/", "remote/", "cli/")
_folio_engine = None


_folio_title_cache = ""


def _folio_title(default: str = "모비폴리오") -> str:
    """폴리오 화면의 `<title>` 에서 그쪽 이름을 읽는다 (한 번 읽고 기억한다).

    못 읽으면 기본값을 쓴다 — 레일에 빈 칸이 뜨느니 이름 하나가 낡은 편이 낫다."""
    global _folio_title_cache
    if not _folio_title_cache:
        try:
            with open(os.path.join(UI_DIR, "folio", "index.html"), encoding="utf-8") as f:
                m = re.search(r"<title>([^<]{1,60})</title>", f.read(4096))
            _folio_title_cache = (m.group(1).strip() if m else "") or default
        except OSError:
            _folio_title_cache = default
    return _folio_title_cache


def shell_state() -> dict:
    """통합 셸의 **레일**이 쓰는 값.

    레일은 두 가지를 보여 준다 — 워커스는 **파랑 숫자 배지**(실행 중 작업 수),
    폴리오는 **골드 점**(재생 중). 그리고 맨 아래 **우선 스위치**.

    **한 번에 준다.** 레일은 1초마다 새로 그리는데 길이 둘이면 두 번 두드리게 되고,
    그러면 두 값이 **서로 다른 순간**의 것이 된다 — 배지는 도는데 점은 꺼져 있는 그림이
    나온다. (미니 1초·오버레이 2초로 따로 두드리면 두 화면이 다른 값을 보게 된다.)

    **CLI 를 부르지 않는다** — 큐는 메모리, 연주는 그쪽이 폴링해 둔 값이다. 그래서
    몇 분짜리 실행이 도는 중에도 이 길은 즉시 답한다."""
    st = QUEUE.state()
    running = sum(1 for x in st.get("items", []) if x.get("status") == "running")
    if not running and st.get("running"):
        running = 1                     # 그룹 안에서 돌고 있을 때도 「도는 중」이다
    out = {"works": {"running": bool(st.get("running")), "badge": running},
           "folio": {"playing": False, "title": ""},
           "priority": store.get_settings().get("queue_on_performance", "music"),
           # **이름을 화면에 적지 않는다** (이 저장소의 규칙 — `js` 에 이름을 또 적으면
           # 바꿀 때 한 곳이 빠진다). 레일은 **두 앱 이름이 다 필요한데**, 화면은 제 것
           # 하나만 안다(`document.title`). 그래서 여기서 준다.
           #
           # 폴리오 이름은 **그쪽 화면의 제목에서 읽는다** — 우리가 따로 적어 두면
           # 그쪽 제목이 바뀌었을 때 레일만 옛 이름을 말한다.
           "apps": [{"key": "works", "name": APP_TITLE, "href": "/"},
                    {"key": "folio", "name": _folio_title(), "href": "/folio/"}]}
    try:                                # 폴리오가 아직 안 붙었어도 셸은 떠야 한다
        eng = folio_engine()
        now = eng._NOW if isinstance(getattr(eng, "_NOW", None), dict) else {}
        out["folio"] = {"playing": bool(now.get("playing")), "title": str(now.get("title") or "")}
    except Exception as e:
        out["folio"]["error"] = type(e).__name__
    return out


def folio_overlay_sync(settings: dict | None = None) -> bool:
    """설정 `overlay_folio` 가 켜져 있으면 폴리오 밴드를 띄운다 → 띄웠나.

    **띄우는 것만 한다.** 끄는 것은 그쪽 `/api/folio/overlay` 가 한다 (창을 숨긴다).
    창은 한 번 만들면 그쪽이 들고 있으므로 여기서 다시 만들지 않는다.

    창을 띄우는 일이라 **검사에서는 절대 부르지 않는다** — 아래 `_ov` 를 보기만 한다."""
    s = settings if isinstance(settings, dict) else store.get_settings()
    if not s.get("overlay_folio"):
        return False
    try:
        eng = folio_engine()
        eng._ov_start()
        return eng._ov is not None
    except Exception as e:
        _say(f"[folio] 오버레이를 띄우지 못했습니다: {type(e).__name__}: {e}")
        return False


def folio_engine():
    """`folio/engine.py` 를 처음 쓸 때 한 번 불러온다 (import 비용을 시작에 얹지 않는다).

    불러오면서 **두 가지를 꽂는다.** 둘 다 「`__main__` 에서만 하던 일」이라
    불러 쓰는 모듈이 되면서 **조용히 사라졌던** 것들이다 (오버레이와 같은 자리)."""
    global _folio_engine
    if _folio_engine is None:
        folio_dir = os.path.join(HERE, "folio")
        if folio_dir not in sys.path:
            sys.path.insert(0, folio_dir)   # engine 이 `import library` 로 folio/library.py 를 집는다
        import engine as _e                 # noqa: E402
        # ① **잠금을 하나로.** 그쪽도 `_cli_lock` 을 따로 들고 있었다. 앱이 둘일 때는 맞는
        #    모양이었지만 합친 뒤에는 **자물쇠가 둘이고 문이 하나**다. 게임 파이프는 하나이고,
        #    `cli_transport` 는 표준출력이 JSON 이 아닐 때 **공용 파일**(last-response)을 읽어
        #    답을 메운다 — 두 호출이 겹치면 **서로의 답을 집어 갈 수 있다.**
        #    같은 자물쇠를 쓰게 해서 한 번에 하나만 나가게 한다.
        _e._cli_lock = _cli_lock
        # ② **나가는 문도 하나로.** 그쪽 `_json` 을 우리 것으로 바꿔 끼운다 — 밖으로 나가는
        #    답의 경로 지우기·CORS 머리가 폴리오 길에도 같이 걸리게. 두 문이면 한쪽만 고친다.
        _e._json = _json
        # ③ **밖에서 보고 바꾸는 설정 키도 하나로.** 그쪽 `/api/settings` 가 밖이면 이 목록만
        #    보여 주고 이 목록만 저장한다 (목록은 여기 한 곳 — `FOLIO_SETTINGS_REMOTE_OK`).
        _e.REMOTE_SETTINGS_OK = frozenset(k for k in FOLIO_SETTINGS_REMOTE_OK if _remote_setting_ok(k))
        _folio_engine = _e
    return _folio_engine


_folio_watch = None


def folio_watch_start() -> bool:
    """폴리오 연주 감시를 띄운다 → 띄웠나.

    **이게 없으면 음악이 죽은 채로 돈다.** 그쪽 `_watch` 는 `main()` 안에서만 떴는데,
    `main()` 은 `__main__` 에서만 돈다. 불러 쓰는 모듈이 되자 **한 번도 안 떴다** —
    그러면 `_NOW` 가 처음 값 그대로 멈춰 있어서

      · 레일의 골드 점이 영영 안 켜지고 (`shell_state`)
      · **다음 곡으로 안 넘어간다** (`_engine_step` 이 여기서만 불린다)
      · 합주 판정·인사말도 안 돈다

    즉 재생목록이 **한 곡에서 멈춘다.** `_ov_start` 와 똑같은 자리이고, 그때와 달리
    이건 기본 기능이라 **옵션이 아니라 항상 띄운다.**

    **부르는 자리는 앱이 실제로 뜨는 곳 하나뿐이다.** 처음엔 `folio_engine()` 안에서
    띄웠는데, 그러면 **검사가 엔진을 불러오기만 해도 감시가 돌았다** — 게임에 1초마다
    조회를 넣는 스레드가 검사 내내 도는 것이다 (검사는 실제 게임 CLI 를 부르지 않아야 한다).
    (`cli.NO_CLI` 로 막는 것은 안 된다 — 그 값은 `test_cli_transport` 가 `importlib.reload` 로
    바꿔 놓는다. **다른 검사가 바꾸는 값에 기대면 안 된다.**) 그래서 막는 대신 **부르는 자리를 옮겼다.**"""
    global _folio_watch
    if _folio_watch is not None and _folio_watch.is_alive():
        return False
    eng = folio_engine()
    _folio_watch = threading.Thread(target=eng._watch, daemon=True, name="folio-watch")
    _folio_watch.start()
    return True


REMOTE_NEVER_PREFIX = ("/api/cli/", "/api/remote/", FOLIO_PREFIX)
# 위 앞자리 중 **아직 그런 길이 없는 것.** 앞자리 규칙은 길이 생기기 전에 먼저 서 있으라고
# 두는 것이라 그 자체는 맞는데, 그러면 오타로 죽은 규칙과 구별이 안 된다 —
# 둘 다 「아무 길에도 안 걸리는 규칙」이기 때문이다. 그래서 **일부러 미리 둔 것만 여기 적고**,
# 검사는 이 목록에 없는 앞자리가 아무 길에도 안 걸리면 오타로 본다.
#   `/api/cli/` — 모비폴리오에는 게임에 바로 묻는 디버그 통로가 있고 우리는 아직 없다.
#                 생기면 밖에 둘 이유가 없으므로 규칙을 먼저 세워 둔다.
#   `/api/folio/` — 여기 있는 것은 **기본이 거절**이라는 뜻이지 전부 막는다는 뜻이 아니다.
#                 `_remote_blocked` 가 이 줄보다 **먼저** 폴리오를 처리하고, 거기서
#                 `FOLIO_READ_OK`(보기)·`FOLIO_PLAY_OK`(연주 조작, edit 부터)만 빠진다.
REMOTE_NEVER_PREFIX_FUTURE = ("/api/cli/",)

# POST 중에 밖에서 할 수 있는 것 — **범위별 허용 목록**. 여기 없으면 막힌다 (거절이 기본).
REMOTE_POST_OK = {
    "read": set(),                                     # 보기만 — 바꾸는 것은 하나도 없다
    "edit": {"/api/queue", "/api/plan", "/api/settings"},   # 큐 편집·계산·설정 — **값이 안 든다**
    "run": {"/api/queue", "/api/plan", "/api/settings",
            "/api/queue/start", "/api/queue/stop"},                 # `/api/sync` 는 REMOTE_NEVER
}
_REMOTE: dict = {}        # 쪽지 → {"until": 만료, "dev": 기기 id}
_REMOTE_TRY: dict = {}    # 보낸 곳 → (틀린 횟수, 잠금 해제 시각)
_last_remote = 0.0        # 마지막 원격 요청 시각 (유휴 자동 닫기용)
# ── 로그인 잠금을 세는 단위 ──
# 터널 뒤에서는 모든 폰이 127.0.0.1 로 보인다. cloudflared 는 진짜 보낸 곳을 `Cf-Connecting-Ip` 에 적어
# 주므로 **터널 주소로 들어온 요청**은 그 값으로 가른다. 그 머리는 터널 밖(이 PC 안)에서는 아무나 적을
# 수 있지만, 그쪽은 어차피 `_ok_hosts` 로 들어오니 로그인 길 자체가 닫혀 있다(`_host_ok_remote`).
# 가르는 것만으로는 부족하다 — 주소를 바꿔 가며 두드리면 IP 별 5회는 무의미하다. 그래서 **합계 상한**을
# 따로 둔다(`_TRY_ALL`). 그리고 짝지어 둔 기기(토큰)는 잠금과 무관하게 통한다 — `_remote_login` 참고.
REMOTE_MAX_TRY_ALL = 20   # 어디서 왔든 10분 안에 합쳐서 이만큼 틀리면 전부 잠근다
_TRY_ALL = "*"            # `_REMOTE_TRY` 에서 합계를 세는 칸 (IP 로는 안 나오는 글자)


def _remote_try_key(handler) -> str:
    ip = handler.client_address[0] if handler.client_address else "?"
    cf = (handler.headers.get("Cf-Connecting-Ip") or "").strip()[:64]
    return f"cf:{cf}" if cf and not any(c.isspace() for c in cf) else ip


def _remote_cfg() -> dict:
    d = store.get_settings()
    host = str(d.get("remote_host") or "").strip().lower()
    return {"on": bool(d.get("remote_on")) and bool(host),
            "host": host,
            "scope": str(d.get("remote_scope") or "read"),
            # 적어 둔 사이트가 없으면 **우편함 사이트**를 쓴다. 폰은 거기서 코드를 넣고 곧장
            # 이 PC 로 로그인한다(다른 출처 → CORS). 비어 있을 때 이것마저 없으면
            # 로그인 예비요청이 늘 403 이 되어 폰에서는 「PC 를 찾지 못했습니다」만 보인다.
            "origin": (str(d.get("remote_origin") or "").strip() or MAILBOX).rstrip("/").lower(),
            "idleMin": int(d.get("remote_idle_min") or 0)}


def _host_ok_remote(host: str) -> bool:
    """Host 헤더가 **사용자가 적어 둔 그 주소**인가 (포트는 떼고 본다)."""
    cfg = _remote_cfg()
    if not cfg["on"]:
        return False
    h = (host or "").lower().split("/")[0]
    return h == cfg["host"] or h.split(":")[0] == cfg["host"].split(":")[0]


def _cors_origin(handler) -> str:
    """이 요청에 열어 줄 출처. 안 열어 줄 것이면 빈 문자열.

    폰 화면을 올려 둔 사이트에서 터널로 부르는 것은 **다른 출처**라, 브라우저가 먼저
    물어보고(OPTIONS) 답에 허락이 없으면 그냥 버린다. **적어 둔 한 곳에만** 열어 준다.
    """
    org = (handler.headers.get("Origin") or "").strip().rstrip("/").lower()
    if not org:
        return ""
    cfg = _remote_cfg()
    return org if (cfg["on"] and cfg["origin"] and org == cfg["origin"]) else ""


def _cors_headers(handler) -> None:
    # 방송 손님 길은 제 출처 판정을 따로 한다 (`_bc_cors_origin` — 원격 켜짐과 무관)
    org = getattr(handler, "_bc_cors", "") or _cors_origin(handler)
    if not org:
        return
    handler.send_header("Access-Control-Allow-Origin", org)
    handler.send_header("Vary", "Origin")


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
    """**그 기기 것만** 끊는다 — 한 대를 끊는다고 다른 기기까지 쫓아내면 안 된다."""
    for k, v in list(_REMOTE.items()):
        if v.get("dev") == dev_id:
            _REMOTE.pop(k, None)


def _remote_drop_all() -> None:
    _REMOTE.clear()


# 우편함(폰에 주소를 건네주는 곳) — **모비폴리오와 같은 곳**을 쓴다.
# 터널 주소는 켤 때마다 바뀌고 길다. 사람이 폰에 옮겨 적게 두면 반드시 틀리므로,
# 짧은 코드만 옮기게 하고 주소는 우편함이 건네준다. 코드는 3분 · 한 번 읽히면 소멸.
MAILBOX = "https://link.mobimml.com"
# 우편함 코드 모양 — 워커(`MobiFolio_LINK/worker.js`)의 CODE_LEN·ALPHABET 과 같다 (8자).
_PAIR_CODE_RX = re.compile(r"^[A-Z0-9]{8}$")
_TUNNEL = None


# 터널 쪽 로그에서 **주소를 지운다**. tunnel.py 는 「열렸습니다 — https://…」 「밖에서 닿습니다 — …」
# 처럼 주소를 그대로 말한다. 터널 주소는 문 위치라 백업에서도 빼는 비밀이고(store.BACKUP_SECRET_KEYS),
# 로그는 실행 중 회전하며 한 세대(`.1`)를 더 남겨 문의 글에 통째로 붙여지기 쉽다. 말은 남기고 주소만 뺀다.
_URL_IN_LOG = re.compile(r"(?i)(?:https?|wss?)://[^\s\"'<>)]+|[\w.-]+\.trycloudflare\.com")


def _say_tunnel(msg: str) -> None:
    _say(_URL_IN_LOG.sub("(주소 생략)", str(msg)))


def _tunnel():
    global _TUNNEL
    if _TUNNEL is None:
        import tunnel
        _TUNNEL = tunnel.Tunnel(store.DATA_DIR, log=_say_tunnel)
        # 유휴 자동 닫기 — 마지막 원격 요청 뒤 설정한 만큼 지나면 스스로 닫는다.
        # **문 너머에 날개를 쓰는 실행이 있다.** 켜 두고 잊는 것을 그냥 둘 수 없다.
        # 방송 중에는 닫지 않는다 — 손님 요청도 활동으로 세지만(`_bc_guest`), 손님이 잠깐 없다고 방송을 끊으면 안 된다.
        _TUNNEL.watch_idle(lambda: _last_remote, _tunnel_idle_min)
    return _TUNNEL


def _tunnel_idle_min() -> int:
    """유휴 자동 닫기 분 (0 = 안 닫음). **방송 중에는 0** — 손님이 잠깐 없다고 방송을 끊지 않는다."""
    return 0 if broadcast.ROOM.on else _remote_cfg()["idleMin"]


def _tunnel_got_url(url: str) -> None:
    """터널이 열리면 그 주소를 **허용할 주소로 자동 반영**한다.
    Quick Tunnel 은 켤 때마다 주소가 바뀐다 — 사람이 매번 옮겨 적게 두면 반드시 어긋난다."""
    host = url.split("://", 1)[-1].strip("/")
    store.set_settings({"remote_host": host})
    # **주소는 로그에 적지 않는다**. 터널 주소는 문 위치라 백업에서도 빼는 비밀이고(BACKUP_SECRET_KEYS),
    # 로그는 실행 중 회전하며 한 세대(`.1`)를 더 남긴다 — 문의 글로 옮겨 붙이기 쉬운 파일이다.
    _say("[tunnel] 허용할 주소를 새 터널 주소로 맞췄습니다")
    broadcast.ROOM._wake.set()      # 방송 중이면 우편함의 코드 → 주소를 곧바로 새 주소로 (PUT /bc/<코드>)


def _mailbox_put(url: str) -> dict:
    """우편함에 주소를 **인증키로 봉해서** 넣고 **코드**를 받아 온다.

    봉투(`mailseal`, 보안 감사 ④-a): 코드만 가진 사람·우편함 서버는 주소를 못 본다 — 인증키를 친 폰만 연다.
    **평문으로 되돌아가지 않는다.** 우편함이 봉투를 모르는 옛 워커면(`ready:false` 로 빈 상자를 연다)
    코드를 버리고 까닭을 말한다 — 평문으로 다시 넣으면 봉한 의미가 없다."""
    key = store.get_remote_key()
    if not key:
        return {"ok": False, "error": "no_key", "message": "인증키를 먼저 발행하세요."}
    try:
        sealed = mailseal.seal_url(key, url)
    except ValueError as e:
        return {"ok": False, "error": "mailbox", "message": f"주소를 봉하지 못했습니다: {e}"}
    req = urllib.request.Request(
        MAILBOX.rstrip("/") + "/new",
        data=json.dumps({"sealed": sealed}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": APP_NAME})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": False, "error": "mailbox", "message": f"우편함에 넣지 못했습니다: {e}"}
    if not isinstance(out, dict):
        return {"ok": False, "error": "mailbox", "message": "우편함이 코드를 주지 않았습니다."}
    if out.get("ready") is not True:
        # 옛 워커는 `sealed` 를 모르고 빈 상자를 연다 — 그 코드를 폰에 주면 「아직 안 넣었다」만 본다
        return {"ok": False, "error": "mailbox_old",
                "message": "우편함 사이트가 아직 봉인 형식을 모릅니다 — 사이트를 새로 올려야 합니다."}
    code = str(out.get("code") or "").strip().upper()
    # 우편함이 준 값은 **화면·QR 주소에 그대로 들어간다** — 모양이 다르면 받지 않는다
    if not _PAIR_CODE_RX.match(code):
        return {"ok": False, "error": "mailbox", "message": "우편함이 코드를 주지 않았습니다."}
    # 코드는 적지 않는다 — 3분 동안은 그 코드로 우편함에서 터널 주소를 꺼낼 수 있다
    _say(f"[link] 우편함에 넣었습니다 ({int(out.get('ttl') or 180)}초)")
    # `site`·`link` — 폰에서 열 곳과 QR 에 담을 주소. **여기 한 곳에서 만든다**
    # (화면이 주소를 따로 적으면 우편함을 옮길 때 한쪽만 바뀐다).
    return {"ok": True, "code": code, "ttl": int(out.get("ttl") or 180),
            "site": MAILBOX, "link": f"{MAILBOX.rstrip('/')}/?c={code}"}


# ── 방송 모드 (broadcast.py) ─────────────────────────────────────────────
# 손님은 이 PC 의 터널로 바로 붙는다. 방송 창에서 켜면 **원격 리모컨이 꺼져 있어도** 터널을 연다(인증키 없이 —
# 손님 길은 기기 쪽지와 따로 논다). 우편함에는 `/bc` 길로 코드 → 터널 주소만 적는다.
# 개발·검사 판(`MOBIW_NO_CLI=1`)은 밖에 나가지 않는다 — 가짜 터널·가짜 우편함 (cloudflared 를 받지도 띄우지도 않는다).
_BC_OFFLINE = (not RELEASE) and os.environ.get("MOBIW_NO_CLI") == "1"
_BC_OFFLINE_TUNNEL = broadcast.OfflineTunnel()
BC_WINDOW_TITLE = "모비폴리오 · 방송"     # 방송 창 제목 (ui/folio/broadcast.html `<title>`) — 창을 찾아 앞으로 올릴 때 쓴다
BC_WINDOW_W, BC_WINDOW_H = 820, 560      # 방송 창 안쪽 크기 (CSS px, 시안)


def _bc_tunnel():
    return _BC_OFFLINE_TUNNEL if _BC_OFFLINE else _tunnel()


def _bc_activity() -> None:
    """손님 요청·방송 터널 열기를 **원격 활동**으로 센다 — 유휴 자동 닫기가 방송을 끊지 않게."""
    global _last_remote
    _last_remote = time.time()


def _bc_hosts() -> set:
    """손님 길을 받을 Host — 방송이 우편함에 적어 둔 터널 주소 · 지금 떠 있는 터널 주소."""
    hs = set()
    h = broadcast.ROOM.tunnel_host()
    if h:
        hs.add(h)
    t = _BC_OFFLINE_TUNNEL if _BC_OFFLINE else _TUNNEL
    if t is not None:
        try:
            st = t.state()
            if st.get("running") and st.get("url"):
                hs.add(str(st["url"]).split("://", 1)[-1].strip("/").lower())
        except Exception:
            pass
    return hs


def _bc_cors_origin(handler) -> str:
    """손님 페이지의 출처 — 우편함 사이트(`link.mobimml.com`)와 적어 둔 폰 사이트만. 원격 켜짐과 무관하다."""
    org = (handler.headers.get("Origin") or "").strip().rstrip("/").lower()
    if not org:
        return ""
    allowed = {MAILBOX.rstrip("/").lower()}
    extra = str(store.get_settings().get("remote_origin") or "").strip().rstrip("/").lower()
    if extra:
        allowed.add(extra)
    return org if org in allowed else ""


def _bc_open_window() -> dict:
    """방송 창 — 떠 있으면 앞으로, 없으면 새 앱 창(820×560). 개발·검사 서버는 창을 띄우지 않는다."""
    if os.environ.get("MOBIW_NO_BROWSER"):
        return {"ok": True, "opened": False}
    if _front_window_titled(BC_WINDOW_TITLE):
        return {"ok": True, "opened": "front"}
    url = f"http://127.0.0.1:{PORT}/folio/broadcast.html"
    exe = _find_app_browser()
    if exe:
        _seed_profile(APP_PROFILE)
        args = [f"--window-size={BC_WINDOW_W},{BC_WINDOW_H}" if a.startswith("--window-size=") else a
                for a in _app_window_args(exe, url, APP_PROFILE)]
        try:
            import subprocess
            subprocess.Popen(args, creationflags=0x00000008)   # DETACHED_PROCESS
            threading.Thread(target=_fit_app_window, kwargs={"title": BC_WINDOW_TITLE,
                                                              "css": (BC_WINDOW_W, BC_WINDOW_H)},
                             daemon=True, name="fit-bc-window").start()
            return {"ok": True, "opened": "new"}
        except OSError as e:
            return {"ok": False, "error": "window", "message": f"창을 띄우지 못했습니다: {e}"}
    import webbrowser
    webbrowser.open(url)
    return {"ok": True, "opened": "browser"}


broadcast.configure(
    mailbox=lambda: MAILBOX,
    http=broadcast.offline_http if _BC_OFFLINE else broadcast._http_json,
    tunnel=_bc_tunnel, port=lambda: PORT,
    # 가짜 터널은 허용 주소(remote_host)를 건드리지 않는다
    on_url=None if _BC_OFFLINE else (lambda url: _tunnel_got_url(url)),
    remote_in_use=lambda: bool(store.get_settings().get("remote_on")),
    activity=_bc_activity,
    engine=lambda: folio_engine(),
    settings=store.get_settings, save=store.set_settings,
    open_window=lambda: _bc_open_window(),
    say=lambda msg: _say(msg), thread=True)


# ── 밖에서 못 만지는 설정 ──────────────────────────────────────────────
# **범위가 edit 이어도 밖에서는 이 값들을 못 바꾼다 — 그리고 못 본다.** 이유는 셋이다.
#   문       `remote_*` — 범위·인증키·허용 주소. 밖에서 바꿀 수 있으면 「보기만」으로 들어온
#            폰이 스스로 `run` 이 되고, 인증키를 제 것으로 갈아 끼운다 (실측: edit 쪽지로
#            `remote_scope:"run"` 이 그대로 저장됐다 — 날개를 쓰는 실행이 그 뒤에 열린다).
#   파일     `cli_exe` — 이 PC 의 파일을 가리킨다 (다음 「연결 확인」이 그 exe 를 띄운다).
#   갱신     `update_url` — 여기가 바뀌면 다음 「지금 업데이트」가 남의 exe 를 받아 실행한다.
#   창       `overlay*` — 이 PC 화면 위의 창.
# GET 은 감추고 POST 는 버린다 — **같은 목록**을 쓴다. 두 목록이면 한쪽만 고친다.
REMOTE_SETTINGS_NEVER = {"cli_exe", "update_url"}
REMOTE_SETTINGS_NEVER_PREFIX = ("overlay", "remote")
# **날개가 드는 설정** — 밖에서는 어느 범위든 못 바꾸고 못 본다 (위와 같은 문 `_remote_setting_ok`). 따로 두는 까닭: 위 목록은
# 「이 PC 의 문·파일·갱신」이라 백업 복원도 무시하는 키(`store.RESTORE_IGNORE_KEYS`)와 같은 묶음인데, 이것은 복원해도 되는 값이다.
#   `folio_dismount_by_craft` — 켜 두면 폰의 연주(edit)가 탈것에서 내리려고 제작 하나를 걸어 **날개 5** 가 든다
#   「edit = 값이 안 드는 것」에 맞춘다.
REMOTE_SETTINGS_WINGS = frozenset({"folio_dismount_by_craft"})
# **밖에서는 `run` 범위에서만** 바꿀 수 있는 키 — PC 에서 도는 큐의 **안전장치**다.
# `gather_quest_watch:"resume"`(재개 = 새 채집 = 날개) · `queue_precheck:false`(막힌 상태 점검 끄기) · `wing_cap_*`(차단기 상한·창) ·
# `queue_max_passes`(반복 상한). 실행 자체는 run 이어야 하지만, edit 폰이 이것을 늦추면 「값이 안 드는 범위」가 값을 건드린다.
# edit 에서 오면 버리고 `ignored` 에 적는다 (남는 키가 없으면 403 `remote_setting`). 보기(GET)는 그대로다 — 폰 설정 화면은
# edit 에서 이 줄들을 감춘다 (ui/js/settings.js `RUN_ONLY_KEYS`, 이 목록과 같아야 한다 — 검사가 본다).
REMOTE_SETTINGS_RUN_ONLY = frozenset({
    "gather_quest_watch", "queue_precheck", "queue_max_passes",
    "wing_cap_total", "wing_cap_waste", "wing_cap_window_min", "wing_cap_waste_min",
})


def _remote_setting_ok(k) -> bool:
    k = str(k)
    return not (k in REMOTE_SETTINGS_NEVER or k in REMOTE_SETTINGS_WINGS or k.startswith(REMOTE_SETTINGS_NEVER_PREFIX))


def _remote_setting_writable(k, scope: str) -> bool:
    """밖에서 온 `/api/settings` 저장이 이 키를 받는가 — NEVER 는 어느 범위든 안 받고, RUN_ONLY 는 `run` 에서만."""
    return _remote_setting_ok(k) and (scope == "run" or str(k) not in REMOTE_SETTINGS_RUN_ONLY)


# 이 PC 의 파일 경로 모양 (`C:\…`). 밖으로 나가는 답에서 **값째로** 지운다 — 키 이름을
# 하나씩 적어 두면 `/api/state` 의 `cli.exe` 처럼 다른 이름으로 새는 자리를 놓친다 (실제로
# `/api/settings` 는 걸러 놓고 그 옆의 `cli` 칸으로 같은 경로가 나가고 있었다).
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
# 글 **속에** 박힌 경로 (`… 못 찾았습니다: C:\Users\…`) — CLI 응답 원문·오류 문구가 폴리오 로그(`/api/folio/log`)로
# 밖에 나간다. 값째 지우기만으로는 이런 자리를 놓친다. 역슬래시 경로만 본다 — `Re:/Zero` 같은 제목을 다치지 않게.
_DRIVE_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s\"'<>|]*")
_REMOTE_DROP_KEYS = {"remote_key"}   # 값 모양과 무관하게 절대 안 나가는 키


def _scrub_remote(obj):
    """밖으로 나가는 응답에서 이 PC 의 속사정(파일 경로·인증키)을 지운다."""
    if isinstance(obj, dict):
        return {k: _scrub_remote(v) for k, v in obj.items() if k not in _REMOTE_DROP_KEYS}
    if isinstance(obj, list):
        return [_scrub_remote(v) for v in obj]
    if isinstance(obj, str):
        if _DRIVE_PATH.match(obj):
            return ""
        if ":\\" in obj:
            return _DRIVE_IN_TEXT.sub("(경로)", obj)
    return obj


def _public_settings(d: dict) -> dict:
    """응답에 실어도 되는 설정만. **인증키는 어디로도 안 나간다** — 이 PC 의 화면에도.

    화면이 키를 받아 쥐고 있으면 그 화면을 캡처하거나 다른 창이 읽는 순간 문이 열린다.
    발행한 **그 한 번**만 보여 주고, 그 뒤로는 「정해져 있음」만 알린다.
    """
    out = dict(d)
    out.pop("remote_key", None)
    out["remote_key_set"] = bool(d.get("remote_key"))
    return out


def _remote_tok(handler) -> str:
    """이 요청이 들고 온 쪽지. **쿠키가 아니라 헤더다.**

    폰에서 홈 화면 앱(PWA)으로 쓸 때는 화면 주소와 터널 주소가 달라서 **사파리가 쿠키를
    통째로 막는다**(3자 쿠키 차단).
    """
    return (handler.headers.get("X-MobiWorks-Remote") or "").strip()
_srv = None   # ThreadingHTTPServer (종료용)


def _say(msg: str) -> None:
    # pid 를 붙인다 — 업데이트 중에는 옛·새 인스턴스가 같은 로그 파일에 동시에 쓴다
    # **한 줄을 한 번의 write 로.** print 는 본문과 줄바꿈을 두 번에 나눠 쓴다 — 여러 스레드가 부르면
    # 그 사이에 남의 줄이 끼고, 로그 회전(_RotatingLog)이 그 틈에 일어나면 줄이 두 파일로 찢어진다.
    out = sys.stdout
    if out is None:   # pythonw 등 출력이 없는 실행
        return
    try:
        out.write(f"[{time.strftime('%H:%M:%S')} {os.getpid()}] {msg}\n")
        out.flush()
    except (OSError, ValueError, UnicodeError):
        pass


QUEUE_BUSY_SKIP = "제작·채집 대기열이 도는 중이라 대기열은 건너뛰었습니다 — 멈춘 뒤 다시 복원하면 적용됩니다."


def _restore_with_queue(raw: bytes) -> dict:
    """백업 복원. 대기열(queue.json)은 **러너가 멈춰 있을 때만** 적용하고, 적용했으면 메모리의
    `QUEUE` 가 그 파일을 다시 읽게 한다.

    **판정·적용·되읽기를 `QUEUE.lock` 한 번 안에서** 한다. `start()` 가 같은 잠금 안에서 `running` 을 세우므로
    그 사이에 러너가 새로 뜰 수 없고, 다른 요청의 큐 편집(add·move …)도 같은 잠금을 잡아 파일과 메모리가
    어긋나지 않는다. 도는 중에 파일만 바꾸면 러너가 끝나며(`_run` 의 finally → `_save`) 메모리 사본으로 다시
    덮어써 복원이 조용히 사라진다 — 그래서 건너뛰고 `skipped` 로 알린다. 나머지 파일은 그대로 적용한다.
    잠금 순서는 QUEUE.lock → store.LOCK (workqueue 의 `_save` 와 같은 방향)이다.
    큐 내부(`_load`·`_thread`·`last_error`)는 만지지 않는다 — 공개 함수 `is_busy`·`reload_from_disk` 만."""
    with QUEUE.lock:
        busy = QUEUE.is_busy()
        r = store.restore_apply(raw, VERSION, {store.QUEUE_FILE: QUEUE_BUSY_SKIP} if busy else None)
        if r.get("pruned"):   # 복원 전 보관본은 최근 BACKUP_KEEP_MAX 개만 남긴다 — 지운 것을 적는다
            _say("restore: 옛 보관본 정리 — " + ", ".join(r["pruned"]))
        if r.get("ok") and store.QUEUE_FILE in r.get("applied", ()):
            # 시작할 때와 같은 되살리기 (재개 없음·waiting 유지·모양 보정) + 옛 오류 배너·체인 정지 사유 걷기.
            # 같은 잠금 안이라 여기서 busy 로 거절될 일은 없다 — 그래도 결과를 믿고 적는다.
            rr = QUEUE.reload_from_disk()
            if rr.get("ok"):
                r["queue_reloaded"] = True
    return r


# ── 문제 신고 zip ─────────────────────────────────────────────────
# 사용 통계 없는 문제 신고 zip — 로그(마스킹된)·설정(비밀 제외)·버전을 한 번에
# 묶어 붙이기 좋게. `GET /api/report` 가 이 zip 을 내준다 — 공용 문(헤더 토큰) · REMOTE_NEVER (이 PC 의 파일이다).
# **가림(mask)은 zip 의 모든 글자 파일에 똑같이 건다** — 로그만이 아니라 설정·대기열·장부·스레드 덤프까지.
# 한 파일만 빼먹는 일이 없게 zip 을 쓰는 자리 한 곳(`_report_pack`)에서 건다.
REPORT_ROOT = "mobiworks-report"
REPORT_MAX = 8 * 1024 * 1024          # zip 에 넣는 글자 전체(풀었을 때) 상한 — 넘으면 로그의 **앞쪽**을 버린다
REPORT_LOG_LINES = 2000               # 로그 끝에서 이만큼 (지금 파일이 모자라면 `.1` 에서 채운다)
REPORT_LEDGER_LINES = 200             # 장부 끝에서 이만큼
REPORT_DIAG_LINES = 60                # 연출·오버레이 진단 줄 (`[opening]`·`[overlay]`) 끝에서 이만큼
REPORT_LINE_MAX = 4000                # 한 줄 상한 (글자) — 몇 MB 짜리 한 줄이 상한을 혼자 먹지 않게
_REPORT_T0 = time.time()              # 경과 시간의 기준 (모듈을 들인 때 = 서버가 뜬 때)

_RX_URL = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>)\]]+")
_RX_TUNNEL_HOST = re.compile(r"(?i)[\w.-]+\.trycloudflare\.com")
_RX_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{32,}(?![0-9A-Za-z])")
_RX_LONGTOK = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
# 「코드」·「code」·「인증키」·「key」 바로 뒤의 대문자·숫자 8~32자 (우편함 코드 8자 · 인증키 8~32자).
# 낱말은 대소문자를 가리지 않고, **값은 대문자·숫자만** 본다 (소문자 낱말까지 가리면 문장이 사라진다).
_RX_CODE = re.compile(r"((?:코드|인증키|[Cc][Oo][Dd][Ee]|[Kk][Ee][Yy]|PIN|pin)\s*[:=：]?\s*[「『\"'(\[]?\s*)"
                      r"([A-Z0-9]{8,32})(?![A-Za-z0-9])")
_RX_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
# IPv6: 폰의 공인 주소는 cloudflared 가 `Cf-Connecting-Ip` 에 IPv6 로 적어 올 때가 많다. 콜론 두 개
# 이상의 16진 조각 — 시계(12:34:56)·판 번호와 갈라야 하므로 **글자(a-f)가 하나라도 있거나 `::` 가 든 것만** 가린다.
_RX_IPV6 = re.compile(r"(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{1,4}(?![0-9A-Za-z:])")
_RX_USERDIR = re.compile(r"(?i)(?:\b[a-z]:|(?<![\w])/[a-z])[\\/]+users[\\/]+[^\\/\s\"'<>|:*?]+")
_LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


def _report_secrets() -> list:
    """지금 이 PC 에 있는 **실제 비밀 값** — 모양 규칙이 놓쳐도 글자 그대로 한 번 더 지운다."""
    out = []
    try:
        st = store.get_settings()
        for k in store.BACKUP_SECRET_KEYS:
            v = str(st.get(k) or "").strip()
            if v:
                out.append(v)
                if "://" in v:                       # 사이트 주소는 호스트만 따로도
                    out.append(urlparse(v).hostname or "")
    except Exception:
        pass
    try:
        out += [d.get("token", "") for d in store.get_devices()]
    except Exception:
        pass
    out.append(TOKEN)
    out += list(_REMOTE.keys())
    return sorted({s for s in out if s and len(s) >= 6}, key=len, reverse=True)


def _report_keep_urls() -> tuple:
    """가리지 않고 **통째로 남기는** 주소 — 설정의 업데이트 주소(`update_url`). 비밀이 아니고, 업데이트가
    안 될 때 어느 주소를 봤는지가 신고의 핵심이다. 쿼리가 붙은 주소는 남기지 않는다 (토큰이 실릴 수 있다)."""
    try:
        u = str(store.get_settings().get("update_url") or "").strip()
    except Exception:
        u = ""
    return (u,) if u and "?" not in u and "trycloudflare" not in u.lower() else ()


def _mask_url(m, keep=()) -> str:
    raw = m.group(0)
    if raw in keep:
        return raw
    try:
        p = urlparse(raw)
        host = (p.hostname or "").lower()
    except ValueError:
        return "(주소 생략)"
    if host.endswith("trycloudflare.com"):
        return "(터널 주소 생략)"
    netloc = p.netloc.rsplit("@", 1)[-1]            # user:pass@ 는 버린다
    if host in ("127.0.0.1", "localhost", "::1"):
        return f"{p.scheme}://{netloc}{p.path}" + ("?…" if p.query else "")   # 쿼리에는 토큰이 실릴 수 있다
    return f"{p.scheme}://{netloc}" + ("/…" if (p.path.strip("/") or p.query) else "")   # 길(경로)에 코드가 실릴 수 있다


def _mask_ip(m) -> str:
    parts = [int(x) for x in m.groups()]
    if any(x > 255 for x in parts):
        return m.group(0)                            # 주소가 아니다 (판 번호 등)
    return m.group(0) if parts == [127, 0, 0, 1] else "(IP 생략)"


def _mask_tok(m) -> str:
    s = m.group(0)
    if re.fullmatch(r"[a-z0-9_]+", s) or re.fullmatch(r"[A-Z0-9_]+", s):
        return s                                     # 이름(식별자)이다 — 토큰은 대소문자가 섞인다
    return "(토큰 생략)"


def _mask_ip6(m) -> str:
    s = m.group(0)
    if "::" not in s and not re.search(r"[A-Fa-f]", s):
        return s                                     # 12:34:56 같은 시계·숫자 나열 — 주소가 아니다
    if s.lower().rstrip(":") in ("::1", "0:0:0:0:0:0:0:1"):
        return s                                     # 루프백은 127.0.0.1 처럼 둔다
    return "(IP 생략)"


def report_mask(text: str, secrets_=None, env=None, keep=None) -> str:
    """신고 zip 에 들어가는 글자를 가린다. **순서가 뜻이다** — 실제 비밀 값 → 주소 → 사용자 폴더 →
    터널 호스트 → 긴 16진 → 긴 토큰 → 「코드/키」 뒤 값 → IPv4(127.0.0.1 만 남김).
    `secrets_`·`env`·`keep`(통째로 남길 주소) 은 검사가 넣는다 (기본은 지금 이 PC 의 값)."""
    s = str(text or "")
    for v in (_report_secrets() if secrets_ is None else secrets_):
        if v:
            # 대소문자를 가리지 않는다: 이름 붙인 터널 주소·사이트 호스트는 로그에 적힌 대소문자가
            # 설정과 다를 수 있고, 아래 주소 규칙은 트라이클라우드플레어가 아닌 호스트를 **남긴다**.
            s = re.sub(re.escape(v), "(비밀 생략)", s, flags=re.I)
    kept = _report_keep_urls() if keep is None else tuple(keep)
    s = _RX_URL.sub(lambda m: _mask_url(m, kept), s)
    env = os.environ if env is None else env
    for var, tag in (("LOCALAPPDATA", "<LOCALAPPDATA>"), ("APPDATA", "<APPDATA>"), ("USERPROFILE", "<USERPROFILE>")):
        v = str(env.get(var) or "").rstrip("\\/")
        if len(v) >= 3:
            for form in {v, v.replace("\\", "/"), v.replace("\\", "\\\\")}:
                s = re.sub(re.escape(form), lambda _m, t=tag: t, s, flags=re.I)
    s = _RX_USERDIR.sub("<USERPROFILE>", s)          # 환경변수와 다른 사용자 폴더 (옛 경로·다른 계정)
    s = _RX_TUNNEL_HOST.sub("(터널 주소 생략)", s)
    s = _RX_HEX.sub("(토큰 생략)", s)
    s = _RX_LONGTOK.sub(_mask_tok, s)
    s = _RX_CODE.sub(lambda m: m.group(1) + "(코드 생략)", s)
    s = _RX_IPV4.sub(_mask_ip, s)
    s = _RX_IPV6.sub(_mask_ip6, s)
    return s


def _thread_dump() -> list:
    """스레드마다 이름과 스택 끝 8줄 (`/api/debug/threads` 와 신고 zip 이 같이 쓴다)."""
    import traceback
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for tid, frame in sys._current_frames().items():
        out.append({"name": names.get(tid, "?"), "stack": traceback.format_stack(frame)[-8:]})
    return out


def _tail_lines(path: str, n: int) -> list:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:] if n > 0 else []
    except OSError:
        return []


def _report_log_path() -> str:
    lf = globals().get("_logf")
    return getattr(lf, "path", "") or os.path.join(BASE, "mobiworks.log")


def _report_log(path: str) -> tuple:
    """(줄 목록, `.1` 에서 채웠나) — 지금 파일 끝 2,000줄, 모자라면 앞 세대(`.1`) 끝에서 채운다."""
    try:
        sys.stdout.flush()
    except Exception:
        pass
    cur = _tail_lines(path, REPORT_LOG_LINES)
    old = []
    if len(cur) < REPORT_LOG_LINES:
        old = _tail_lines(path + ".1", REPORT_LOG_LINES - len(cur))
    return old + cur, bool(old)


def _screen() -> dict:
    """DPI·화면 크기 (주 모니터). 창을 만들지 않는 조회만 — 윈도우가 아니면 빈 값."""
    out = {"dpi": None, "screen": None}
    if os.name != "nt":
        return out
    try:
        import ctypes
        u = ctypes.windll.user32
        try:
            out["dpi"] = int(u.GetDpiForSystem())
        except Exception:
            pass
        out["screen"] = [int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))]
    except Exception:
        pass
    return out


def _fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    return f"{h}시간 {r // 60}분 {r % 60}초"


REPORT_README = """모비웍스 문제 신고 파일
==========================

이 파일을 대화에 붙여 주세요. 무엇이 어디서 막혔는지 보는 데 필요한 것만 담았습니다.
사용 통계는 없습니다 — 이 파일은 사람이 직접 내려받을 때만 만들어지고, 어디로도 보내지지 않습니다.

■ 들어 있는 것
  manifest.json   앱 판 · 빌드 시각 · 파이썬/윈도우 판 · DPI · 화면 크기 · exe 파일 이름 · 켜진 뒤 경과 시간 ·
                  CLI 상태(데모/실제/꺼짐) · 포트 · 개수(곡 · 재생목록 · 대기열 항목)
  log.txt         앱 로그 끝 2,000줄 (모자라면 앞 세대 로그에서 채움) — 가림 규칙을 거친 것
  diag.txt        로그 중 연출([opening]) · 오버레이([overlay]) 진단 줄만 다시 모은 것
  settings.json   설정 — 비밀은 값 대신 「있음/없음」만
  queue.json      제작·채집 대기열의 항목과 상태별 개수 (카드마다의 실행 기록은 뺌)
  ledger.txt      날개·산출 장부 끝 200줄
  songs.json      곡 · 재생목록 **개수만** (곡 제목 · 목록 이름은 없음)
  threads.txt     만든 순간의 스레드 스택 (「어디서 기다리나」)

■ 뺀 것 · 가린 것
  · 인증키 · 터널 주소 · 폰 사이트 주소 → 값 없이 「있음/없음」만
  · 짝지은 기기 토큰 · 실행 토큰 · 긴 16진/토큰 글자 → (토큰 생략)
  · 터널 주소(클라우드플레어 임시 주소) → (터널 주소 생략)
  · 「코드」「인증키」「code」「key」 뒤의 대문자·숫자 → (코드 생략)
  · 사용자 폴더 경로 → <USERPROFILE> · <LOCALAPPDATA> (사용자 이름이 남지 않게)
  · CLI 경로 → 파일 이름만
  · 127.0.0.1 이 아닌 IP 주소 → (IP 생략)
  · 이 PC 밖 주소는 호스트만 남기고 뒤(경로·쿼리)는 …

이 파일을 대화에 붙여 주세요.
"""


def _report_pack(members: dict, secrets_: list, keep: tuple) -> bytes:
    """**zip 에 쓰는 자리는 여기 하나다** — 어떤 파일이든 가림을 거친 뒤에만 들어간다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(REPORT_ROOT + "/" + name, report_mask(text, secrets_, keep=keep).encode("utf-8"))
    return buf.getvalue()


def report_filename(ts: float | None = None) -> str:
    return "mobiworks-report-" + time.strftime("%Y%m%d-%H%M", time.localtime(ts or time.time())) + ".zip"


def report_zip(log_path: str | None = None) -> tuple:
    """문제 신고 zip → (파일 이름, bytes). CLI 를 부르지 않는다 — 파일과 메모리만 읽는다."""
    import platform
    js = lambda o: json.dumps(o, ensure_ascii=False, indent=1)   # noqa: E731
    sec = _report_secrets()
    keep = _report_keep_urls()
    lp = log_path or _report_log_path()
    lines, from_old = _report_log(lp)
    # 줄마다 **먼저** 가린다 — 가림이 글자 수를 바꾸므로(IP → 「(IP 생략)」) 상한은 가린 뒤의 크기로 잰다
    lines = [report_mask(ln if len(ln) <= REPORT_LINE_MAX else ln[:REPORT_LINE_MAX] + " …(줄 잘림)", sec, keep=keep)
             for ln in lines]
    diag = [ln for ln in lines if "[opening]" in ln or "[overlay]" in ln][-REPORT_DIAG_LINES:]
    try:
        ledger_lines = _tail_lines(os.path.join(store.DATA_DIR, ledger.FILE), REPORT_LEDGER_LINES)
    except Exception:
        ledger_lines = []
    ledger_lines = [ln[:REPORT_LINE_MAX] for ln in ledger_lines]
    q = store.report_queue()
    counts = store.report_counts()
    try:
        busy = bool(QUEUE.is_busy())
    except Exception:
        busy = None
    threads = "\n\n".join(f"── {t['name']}\n" + "".join(t["stack"]) for t in _thread_dump())
    exe = sys.executable if FROZEN else os.path.abspath(__file__)
    try:
        built = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(exe)))
    except OSError:
        built = ""
    up = time.time() - _REPORT_T0
    manifest = {
        "app": APP_NAME, "report": 1, "version": VERSION, "build_time": built,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "frozen": FROZEN, "embed": EMBED, "lite": LITE,
        "python": platform.python_version(), "python_bits": 64 if sys.maxsize > 2 ** 32 else 32,
        "windows": platform.platform(), **_screen(),
        "exe": os.path.basename(exe),                      # **이름만** — 경로에는 사용자 이름이 든다
        "uptime_sec": int(up), "uptime": _fmt_dur(up),
        "cli": "demo" if cli.DEMO else ("disabled" if cli.NO_CLI else "real"),
        "port": PORT, "data_dir": store.DATA_DIR,          # 가림을 거쳐 <LOCALAPPDATA>\… 로 나간다
        "counts": {**counts, "queue_items": q["total"], "queue_by_status": q["counts"]},
        "queue_running": busy,
        "log": {"lines": len(lines), "from_rotated": from_old, "trimmed_for_size": False},
    }
    members = {
        "README.txt": REPORT_README,
        "settings.json": js(store.report_settings()),
        "queue.json": js(q),
        "ledger.txt": "\n".join(ledger_lines) + ("\n" if ledger_lines else ""),
        "songs.json": js(counts),
        "diag.txt": "\n".join(diag) + ("\n" if diag else ""),
        "threads.txt": threads,
    }
    # 상한: 로그를 뺀 나머지 + manifest 여유분을 먼저 잰다. 남은 만큼만 로그의 **끝**을 둔다.
    fixed = sum(len(v.encode("utf-8")) for v in members.values()) + 64 * 1024
    budget = max(0, REPORT_MAX - fixed)
    tail: list = []
    used = 0
    for ln in reversed(lines):
        n = len(ln.encode("utf-8")) + 1
        if used + n > budget:
            manifest["log"]["trimmed_for_size"] = True
            break
        tail.append(ln)
        used += n
    tail.reverse()
    manifest["log"]["lines"] = len(tail)
    log_txt = "\n".join(tail) + ("\n" if tail else "")
    ordered = {"README.txt": members.pop("README.txt"), "manifest.json": js(manifest), "log.txt": log_txt, **members}
    return report_filename(), _report_pack(ordered, sec, keep)


QUIT_QUEUE_WAIT = 8.0   # 종료 때 큐 러너를 기다리는 상한(초). 실행 명령 타임아웃(660초)은 기다리지 않는다


def _stop_queue_for_exit() -> None:
    """종료 1단계: 큐를 세우고 러너를 짧게 기다린다. 도는 중이었으면
    stop_action 이 한 번 나가고(QUEUE.stop), 양보받은 연주는 러너의 finally 가 돌려준다(work_resume).
    못 끝나면 QUEUE.shutdown 이 파일을 stopped 로 굳힌다 — 어떤 오류도 종료를 막지 않는다."""
    try:
        r = QUEUE.shutdown(QUIT_QUEUE_WAIT)
        if r.get("wasRunning"):
            _say(f"shutdown: queue stopped (runner finished={r.get('finished')})")
    except Exception as e:
        _say(f"shutdown: queue stop failed: {type(e).__name__}: {e}")


# 종료 스레드가 마지막 줄(`shutdown: done`)을 **쓴 뒤** 세운다. `/api/quit` 는 _shutdown 을 daemon
# 스레드로 돌리는데, `_srv.shutdown()` 이 돌아오는 순간 주 스레드가 serve_forever 를 빠져나가 main()
# 이 끝나고 인터프리터 정리가 stdout(로그 파일)을 닫는다 — daemon 스레드는 그 뒤 줄을 못 쓴다.
# 그러면 종료 로그가 늘 `stopping server` 에서 끊긴다. 주 스레드가 이 이벤트를 잠깐 기다린다.
_EXIT_LOGGED = threading.Event()


def _shutdown() -> None:
    time.sleep(0.2)
    # 강제 종료 타이머는 큐 대기 **뒤**에 터져야 한다 — 먼저 터지면 파일이 running 으로 남는다
    threading.Timer(QUIT_QUEUE_WAIT + 3.0, lambda: os._exit(0)).start()
    _stop_queue_for_exit()   # 큐 → 오버레이 → 서버 순
    try:   # 방송 중이면 우편함의 코드를 지운다 (손님이 죽은 주소를 받지 않게)
        broadcast.ROOM.shutdown()
    except Exception:
        pass
    try:   # 오버레이 창을 닫는다 — 유령 창을 남기지 않는다
        overlay.OVERLAY.stop()
    except Exception:
        pass
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
        if _srv:
            _srv.shutdown()
        _say("shutdown: done")
        _EXIT_LOGGED.set()
    finally:
        os._exit(0)


def _watch_parent() -> None:
    """MOBIW_PARENT_PID(셸)가 죽으면 같이 끝난다 — 셸이 강제 종료돼도 고아 백엔드가 남지 않게."""
    pid = os.environ.get("MOBIW_PARENT_PID")
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


def _s(v, default: str = "") -> str:
    """문자열 강제 (None/숫자/딕셔너리가 와도 .strip() 에서 죽지 않게)."""
    return v.strip() if isinstance(v, str) else (str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default)


# ── JSON 입출력 ──
def _json(handler, obj, status=200):
    """JSON 답 — **밖으로 나가는 문 하나.** 폴리오 핸들러의 답도 여기로 온다 (`folio_engine`).

    밖에서 온 요청이면 (1) 이 PC 의 파일 경로·인증키를 값째로 지우고, (2) 허용한 사이트에
    CORS 머리를 붙인다. (2) 가 없으면 폰 사이트(다른 출처)는 예비요청만 통과하고 **본 답은
    브라우저가 버린다**."""
    remote = getattr(handler, "_is_remote", None)
    if remote is not None and remote():
        obj = _scrub_remote(obj)
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    _cors_headers(handler)
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
        try:
            while left > 0:
                chunk = handler.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:               # 비우다 시간 초과 — 어차피 닫는다. 500 traceback 이 아니라 400
            pass
        handler.close_connection = True
        return {"_error": "too_large"}
    try:
        raw = handler.rfile.read(n)
    except OSError:                 # 본문이 다 안 온 채 시간 초과 — 500 traceback 이 아니라 400 으로
        handler.close_connection = True
        return {"_error": "timeout"}
    for enc in ("utf-8", "mbcs"):   # 브라우저는 utf-8. 콘솔 도구가 cp949 로 보내도 조용히 빈 값이 되지 않게
        try:
            v = json.loads(raw.decode(enc))
            return v if isinstance(v, dict) else {"_error": "not_object"}
        except Exception:
            continue
    return {"_error": "decode_error"}


def _read_bytes(handler):
    """본문을 **바이트 그대로** (복원 zip). 상한(MAX_BODY)은 `_read_json` 과 같고, 넘으면 같은 방식으로
    비운 뒤 사유 문자열을 돌려준다 (bytes 면 성공, str 이면 거절 사유)."""
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return "bad_length"
    if n <= 0:
        return "empty"
    if n > MAX_BODY:
        left = min(n, 8 * MAX_BODY)
        try:
            while left > 0:
                chunk = handler.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass
        handler.close_connection = True
        return "too_large"
    try:
        return handler.rfile.read(n)
    except OSError:
        handler.close_connection = True
        return "timeout"


def _multipart_file(body: bytes, ctype: str):
    """multipart/form-data 에서 **첫 파일 칸**의 내용만 뽑는다 (복원 zip). 표준 라이브러리의 cgi 는
    3.13 에서 사라졌고, 우리가 받는 것은 파일 하나뿐이라 경계로 자르는 것으로 충분하다. 없으면 None."""
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return None
    sep = b"--" + m.group(1).strip().encode("utf-8")
    for part in body.split(sep)[1:]:
        if part.startswith(b"--"):        # 끝 표시
            break
        head, nl, rest = part.partition(b"\r\n\r\n")
        if not nl:
            continue
        if b"filename=" not in head.lower():
            continue
        return rest[:-2] if rest.endswith(b"\r\n") else rest
    return None


# ── CLI ──
_cli_lock = threading.Lock()   # 게임 파이프가 직렬이다 — 한 번에 하나만
# 읽기 명령들. 이 목록만 부르는 한 게임 상태는 바뀌지 않는다.
READ_COMMANDS = [
    ("craft", "get_craftable_items", None),
    ("alter", "get_alterable_items", None),
    ("works", "get_altering_works", ""),
    ("items", "get_items", None),
    ("inventory", "get_inventory", ""),
    ("currencies", "get_currencies", ""),
    ("gather", "get_gatherable_items", None),
]


def _cli(command: str, body=None, timeout: float = 120.0):
    with _cli_lock:
        return cli.call(command, body, timeout)


def _probe() -> dict:
    with _cli_lock:
        return cli.probe()


def _raw_cli(command: str, body=None, timeout: float = 30.0, **kw):
    """잠금 없는 호출 — 실행 명령이 잠금을 최대 11분(EXEC_TIMEOUT) 쥐고 있어도 들어가야 하는 것들.

      · 큐 정지용 `stop_action` (카탈로그: 다른 명령이 오면 진행 중인 행동은 canceled 로 끝난다)
      · 채집이 도는 동안의 **퀘스트 읽기** — 읽기는 도는 동작을 갈아치우지 않는다

    겹쳐 나가는 자리라 부르는 쪽이 `allow_last_response=False` 를 줄 수 있다 (공용 파일로
    답을 메우지 않는다 — 남의 답을 집어 올 수 있으므로)."""
    return cli.call(command, body, timeout, **kw)


QUEUE = workqueue.Queue(_cli, _raw_cli, _probe, _say)


# ── 큐 ↔ 연주 이음새 ──────────────────────────────────────────────────
# 큐는 작업을 걸기 전에 **연주 쪽에 묻는다.** 게임 조회로는 알 수 없는 것이 둘 있다:
# **합주 중인가**(게임이 안 알려 준다)와 **인사말이 나가는 중인가**.
#
# 셋 다 **CLI 를 부르지 않는다** — 그쪽 감시가 1초마다 재 둔 값만 본다. 그래서 채집이
# 파이프를 몇 분씩 쥐고 있어도 그 자리에서 답한다.
#
# **엔진을 못 불러오면 예외가 그대로 올라간다.** 큐가 그걸 잡아서 「물어보지 못했습니다」로
# 적고 게임 상태만 보고 움직인다 — 모르는 것을 아는 척하지 않는다.
def _perf_gate() -> dict:
    return folio_engine().work_gate()


def _perf_resume() -> None:
    folio_engine().work_resume()


def _perf_release() -> None:
    """보드가 끝났다 — 양보 부탁을 거두되 **이어서 틀지 않는다** (engine.work_release)."""
    folio_engine().work_release()


def _perf_now() -> dict:
    """「완전한 연주 우선」의 연주 대기가 2초마다 읽는 지금 연주의 모양 — 그쪽 감시가 재 둔 값(메모리)뿐, CLI 0."""
    return folio_engine().work_now()


def _perf_yield() -> dict:
    """「이 곡 끝나면 양보」를 부탁하고 **경계에 설 때까지 기다린다.**

    기다리는 것을 그쪽이 아니라 **여기서** 하는 까닭: 그쪽에서 기다리면 감시 루프가
    묶이고(다음 곡도 합주 판정도 같이 멈춘다), 무엇보다 **큐를 멈춰도 안 풀린다.**
    여기는 큐 스레드라 정지 신호를 같이 볼 수 있다."""
    eng = folio_engine()
    r = eng.work_yield()
    if not r.get("ok") or r.get("held"):
        return r
    now = eng._NOW
    tot, el = float(now.get("tot") or 0), float(now.get("el") or 0)
    # 이 **곡** 하나가 끝나기를 기다리는 것이다 (목록 전체가 아니다).
    # 시한 = 남은 길이 + 30초. 길이를 아직 모르면(방금 튼 곡 — 감시가 아직 안 봤다) 일단 PERF_WAIT_MAX+30초로 두고,
    # 기다리는 동안 길이가 보이면 **그때 다시 잰다** (실측: 연주 카드 1초 뒤의 부탁은 tot=0 이라 그대로 두면 5분짜리 시한이 걸린다).
    known = bool(tot)
    left = max(0.0, tot - el) if tot else QUEUE.PERF_WAIT_MAX
    cap = min(left + 30.0, QUEUE.PERF_WAIT_CEIL)     # 남은 길이 + 여유 (곡 하나치)
    deadline = time.time() + cap
    while not QUEUE._stop.wait(0.5):
        if eng._ENG["held"]:
            return {"ok": True, "why": "곡이 끝나 다음 곡 앞에서 멈춰 섰습니다"}
        if not known and float(now.get("tot") or 0) > 0:
            known = True
            tot, el = float(now.get("tot") or 0), float(now.get("el") or 0)
            cap = min(max(0.0, tot - el) + 30.0, QUEUE.PERF_WAIT_CEIL)
            deadline = time.time() + cap
        if time.time() >= deadline:
            # **양보를 받지 못함.** 부탁을 거둔다 (work_release — 경계에 서기 전이라 음악은 그대로다) 하고, 큐에는 시한이 지났다고
            # 답한다 — 큐가 「연주를 멈추고 진행」한다 (workqueue._yield_after_song). 영영 기다리지 않는다.
            _say(f"[queue] 양보를 받지 못함 — {int(cap)}초(곡 길이+30초)를 기다렸는데 곡 경계가 오지 않았습니다. 부탁을 거둡니다")
            eng.work_release()
            return {"ok": False, "expired": True, "why": f"{int(cap)}초(곡 길이+30초)를 기다렸는데 곡 경계가 오지 않았습니다"}
    eng.work_resume()
    return {"ok": False, "why": "큐가 멈췄습니다"}


QUEUE.perf_gate = _perf_gate
QUEUE.perf_yield = _perf_yield
QUEUE.perf_resume = _perf_resume
QUEUE.perf_release = _perf_release
QUEUE.perf_now = _perf_now


# ── 연주·알림 카드의 갈고리 ──────────────────────────
# 러너가 「연주」 카드를 만나면 **폴리오 엔진의 공개 함수**로 연주를 시작시키고, 그 연주가 **끝날 때까지** 카드가 돈다
# (`workqueue._play_wait` 가 `QUEUE.perf_now` = `engine.work_now` 를 2초마다 읽는다 — CLI 0). 다음 카드는 그 뒤에 선다.
# CLI 는 폴리오가 제 길(play → change_instrument·play_music_score)로 부른다 — 큐가 직접 부르지 않는다.
# 검사는 이 갈고리를 가짜로 바꿔 끼운다 (실제 CLI 에 닿지 않는다).
def _play_start(card: dict) -> dict:
    eng = folio_engine()
    mode = str(card.get("mode") or "resume")
    # 회차 — song 은 **정확히 count 번 치고 멈춘다** (그 요청은 폴리오의 반복·셔플을 안 본다)
    try:
        count = max(1, min(QUEUE.PLAY_COUNT_MAX, int(card.get("count") or 1)))
    except (TypeError, ValueError):
        count = 1
    if mode == "list":
        # 옛 「재생목록 전체」 카드 — 이제 재생목록은 **그룹**(곡마다 「이 곡만」 카드)으로 담는다 (_playlist_songs)
        return {"ok": False, "error": "list_gone", "message": "재생목록 카드는 이제 그룹으로 담습니다 — 빼고 서랍 「연주」에서 다시 담으세요."}
    if mode == "resume":
        # 「지금 대기열 이어서」 — 폴리오가 들고 있는 대기열의 지금 자리를 튼다 (없으면 empty_queue). 회차 제한은 걷는다 — 제 설정대로
        st = eng.queue_state()
        if not st.get("items"):
            return {"ok": False, "error": "empty_queue", "message": "폴리오 대기열이 비어 있습니다."}
        eng.queue_passes(None)
        r = eng.queue_play_index(max(0, int(st.get("pos") or 0)))
        pl = r.get("play") if isinstance(r.get("play"), dict) else r
        return {"ok": bool(pl.get("ok")), "error": pl.get("error"), "message": pl.get("message"),
                "title": (st.get("now") or {}).get("title") or ""}
    # song — 곡 key 로 제목·악기를 찾아 한 곡만 (폴리오의 `/api/play` 와 같은 길 `play_solo`: 감시가 주인이 되어 곡 끝을 경계로 지난다 —
    # count 번 치고 멈추고, 폴리오 대기열로 이어 가지 않는다)
    key = str(card.get("song") or "")
    it = next((x for x in eng._build_items() if x.get("key") == key), None) \
        or next((x for x in eng._build_items() if x.get("title") == key), None)
    if not it:
        return {"ok": False, "error": "song_not_found", "message": "보관함에 그 악보가 없습니다 — 폴리오에서 갱신한 뒤 다시 담으세요."}
    r = eng.play_solo(it["title"], None, it.get("key") or "", passes=count)
    return {"ok": bool(r.get("ok")), "error": r.get("error"), "message": r.get("message"), "title": it["title"]}


def _play_stop(card: dict) -> dict:
    """■ 정지 중의 「연주」 카드 — **우리가 부탁한 연주만** 멈춘다 → `{ok, stopped, why?}`.
    「이 곡만」은 엔진이 곡 하나 재생(`_ENG.solo`)을 들고 있을 때만, 「지금 대기열 이어서」는 대기열이 우리 것(`work_now().own`)일 때만
    `engine.stop()`(폴리오의 ■ 과 같은 길 — get_activity 로 보고 치는 중이면 stop_action 1회, 날개 0). 남의 연주는 건드리지 않는다."""
    eng = folio_engine()
    w = eng.work_now()
    mode = str(card.get("mode") or "resume")
    if mode == "song":
        if not eng._ENG.get("solo"):
            return {"ok": True, "stopped": False, "why": "곡 하나 재생이 이미 끝났거나 우리 것이 아닙니다"}
    elif not w.get("own"):
        return {"ok": True, "stopped": False, "why": "지금 연주는 폴리오 대기열의 것이 아닙니다"}
    r = eng.stop()
    return {"ok": bool(r.get("ok")), "stopped": True, "message": r.get("message")}


def _playlist_songs(lid: str):
    """재생목록 → `{"name", "items": [{"key", "title"}, …]}` (없으면 None) — 큐가 재생목록을 **그룹**으로 펼칠 때 읽는다."""
    eng = folio_engine()
    pl = next((x for x in (eng._lists().get("playlists") or []) if x.get("id") == str(lid or "")), None)
    if not pl:
        return None
    return {"name": str(pl.get("name") or ""),
            "items": [{"key": str(x.get("key") or x.get("title") or ""), "title": str(x.get("title") or x.get("key") or "")}
                      for x in (pl.get("items") or []) if isinstance(x, dict)]}


def _play_mount_ok() -> bool:
    """「연주」 카드의 상태 점검 — 탈것 위일 때 폴리오가 스스로 내릴 수 있는가 (폴리오 설정 `folio_dismount_by_craft`)."""
    eng = folio_engine()
    return bool(eng.store.get_settings().get(eng.DISMOUNT_SETTING))


def _notify(notice: dict) -> None:
    """알림 하나 — 로그 한 줄. PC 밴드 토스트는 오버레이가 `state().notices` 를 보고 스스로 띄우고(overlay._detect_notice),
    앱 창·폰은 `/api/queue` 폴링의 `notices` 로 띄운다 — 밴드가 꺼져 있어도 앱 창 토스트는 그 길로 온다."""
    _say(f"[notify] {notice.get('text')}" + ("" if notice.get("sound") else " (소리 없음)"))


def _push_score() -> dict:
    """채집 목표 도달 뒤 「연주로 밀어내기」에 쓸 악보 → `{"title"}` | `{"why"}` (CLI 0 — 폴리오가 들고 있는 값만 읽는다).

    폴리오 대기열의 지금 곡, 없으면 보관함의 첫 악보. 악기는 바꾸지 않는다(지금 든 악기로 튼다).
    탈것 위면 게임이 연주를 받지 않으므로 고르지 않는다. 제목 끝 공백은 CLI 가 못 찾으므로(실측) 건너뛴다."""
    eng = folio_engine()
    if eng._NOW.get("mounted"):
        return {"why": "탈것 탑승 중"}
    usable = [x for x in eng._build_items()
              if isinstance(x, dict) and str(x.get("title") or "").strip() and not x.get("cliBroken") and not x.get("locked")]
    now = (eng.queue_state().get("now") or {}).get("title") or ""
    pick = next((x for x in usable if x.get("title") == now), None) or (usable[0] if usable else None)
    if not pick:
        return {"why": "보관함에 틀 수 있는 악보가 없음"}
    return {"title": str(pick["title"])}


QUEUE.push_score = _push_score
QUEUE.play_start = _play_start
QUEUE.play_stop = _play_stop
QUEUE.playlist_songs = _playlist_songs
QUEUE.play_mount_ok = _play_mount_ok
QUEUE.notify = _notify


# ── 게임 오버레이 (overlay.py) ──
# 밴드는 CLI 를 한 번도 부르지 않는다: 큐는 메모리(QUEUE.state()), 가공 대기열은 캐시 파일만 읽는다.
def _overlay_alter() -> tuple:
    """(가공 작업 목록, 캐시 시각, 본 적 있는 시설). 남은 시간 깎기는 overlay.alter_view 가 한다.

    **시설 기억을 여기서 갱신한다.** 가공기 판은 작업이 하나도 없어도 시설 알약을 전부 보여 주는데,
    CLI 는 등록된 작업만 주므로 비면 이름조차 모른다 (overlay.learn_facilities 주석)."""
    c = store.get_cache("works")
    rows = work.works(c["data"] or {}).get("works") or []
    st = store.get_settings()
    seen, dur = st.get("alter_seen") or {}, st.get("alter_dur") or {}
    grown = overlay.learn_facilities(rows, seen)
    grown_dur = overlay.learn_durations(rows, dur)
    patch = {}
    if grown != seen:
        patch["alter_seen"] = grown
    if grown_dur != dur:
        patch["alter_dur"] = grown_dur
    if patch:
        store.set_settings(patch)
    return rows, (c.get("fetched_at") or 0.0), grown, grown_dur


def _overlay_collect_all() -> dict:
    """밴드의 「일괄 수령」 — 보드의 collectAll 과 **같은 일**이다: 완료가 있는 시설마다 수령 항목을 큐에 담는다.
    실행 명령은 부르지 않는다 (사용자가 「시작」을 눌러야 돈다)."""
    snap = snapshot()
    view = overlay.alter_view(snap.get("works") or [], (store.get_cache("works").get("fetched_at") or 0.0), time.time())
    added = dup = 0
    msgs = []
    for t in view.get("targets") or []:
        r = QUEUE.add({"type": "collect", "facility": t["facility"], "name": t["name"]}, snap)
        if r.get("ok"):
            added += 1
        elif r.get("error") == "duplicate":
            dup += 1
        else:
            msgs.append(f"{t['facility']}: {r.get('message') or r.get('error')}")
    if added or dup or msgs:
        _say(f"overlay: 일괄 수령 — 담음 {added} · 중복 {dup}" + (f" · 실패 {len(msgs)} ({'; '.join(msgs)})" if msgs else ""))
    return {"ok": bool(added or dup), "added": added, "dup": dup, "failed": msgs}


def _overlay_save_setting(key: str, value) -> None:
    """단축키(F10·F2)가 바꾼 값을 설정에 남긴다 — 다음 실행에도 이어지게."""
    store.set_settings({key: value})


def _overlay_wings():
    """예상 소모(정령의 날개) — 실행 줄의 「예상 소모」와 같은 값(`QUEUE.preview`). CLI 를 부르지 않는다.
    밴드가 이 값을 5초에 한 번만 물어 온다 (overlay._sync_wings)."""
    return QUEUE.preview(store.get_settings()).get("wings")


def _overlay_open_drawer() -> None:
    """밴드의 「창에서 담기」 — 다음 `/api/queue` 응답에 `goDrawer` 를 한 번 실어 페이지가 담기 서랍을 열게 한다.
    창을 앞으로 올리는 것은 `_overlay_bring_front("queue")` 가 따로 한다 (페이지는 창이 앞으로 오면 바로 큐를 다시 읽는다)."""
    global _go_drawer
    _go_drawer = True


def _overlay_bring_front(tab: str = "") -> None:
    """밴드의 `⏎`·`⚙` — 앱 창을 앞으로 (펼친 판 컨트롤 줄).

    단축키를 없앴으므로 밴드에서 창으로 돌아갈 길은 이것뿐이다. 이미 떠 있는 창이 있으면
    그 창을 앞으로 올리고, 없으면 새로 띄운다. `tab` 을 주면 그 탭으로 열린다."""
    global _go_tab
    if tab:
        _go_tab = tab       # 이미 떠 있는 창은 주소를 못 바꾸므로 폴링으로 알린다
    url = f"http://127.0.0.1:{PORT}" + (f"/#{tab}" if tab else "")
    if _front_existing_window():
        return
    _launch_app_window(url)


def _front_existing_window() -> bool:
    """이미 떠 있는 우리 앱 창을 앞으로. 찾지 못하면 False (그러면 새로 띄운다).

    **프로세스 트리로 찾지 않는다.** 브라우저는 자기를 다시 띄우고 처음 프로세스를 끝내기도
    해서 우리가 들고 있는 pid 와 창 주인이 다르다 — 그러면 매번 못 찾고 **새 창을 또 띄워**
    설정을 누를 때마다 창이 하나씩 는다.

    **창 제목과 클래스로 찾는다.** 페이지 제목이 `MobiWorks` 이고 브라우저 앱 창의 클래스는
    `Chrome_WidgetWin_*` 이다. 우리 Tk 오버레이(TkTopLevel)·탐색기(CabinetWClass)는 걸리지 않는다.
    실측: `pid=45356 msedge.exe class=Chrome_WidgetWin_1 제목='(18 완료) MobiWorks'`."""
    global _app_hwnd
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        # 한 번 찾은 창은 기억해 둔다 — 매번 훑을 이유가 없고, 제목이 바뀌어도 계속 맞는다
        if _app_hwnd and u.IsWindow(_app_hwnd) and u.IsWindowVisible(_app_hwnd):
            if u.IsIconic(_app_hwnd):
                u.ShowWindow(_app_hwnd, 9)
            u.SetForegroundWindow(_app_hwnd)
            u.BringWindowToTop(_app_hwnd)
            return True
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        me = os.getpid()
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _l):
            if not u.IsWindowVisible(h):
                return True
            cls = ctypes.create_unicode_buffer(120)
            u.GetClassNameW(h, cls, 120)
            if not cls.value.startswith("Chrome_WidgetWin"):
                return True
            t = ctypes.create_unicode_buffer(300)
            u.GetWindowTextW(h, t, 300)
            # **이름을 여기 적지 않는다.** 창 제목은 페이지 제목(= APP_TITLE)이고, 앱 이름을
            # 바꾸면 APP_TITLE 한 곳만 바뀌면 된다.
            if APP_TITLE not in t.value:
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if int(pid.value) == me:
                return True
            found.append(h)
            return False        # 첫 하나면 충분하다

        u.EnumWindows(cb, 0)
        if not found:
            return False
        h = found[0]
        _app_hwnd = h               # 다음부터는 바로 이 창을 쓴다
        if u.IsIconic(h):
            u.ShowWindow(h, 9)      # SW_RESTORE
        u.SetForegroundWindow(h)
        u.BringWindowToTop(h)
        return True
    except Exception:
        return False


def _front_window_titled(needle: str) -> bool:
    """제목에 `needle` 이 든 우리 앱 창(브라우저 앱 창)을 앞으로 → 찾았나. 방송 창을 두 번 띄우지 않으려고 쓴다."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _l):
            if not u.IsWindowVisible(h):
                return True
            cls = ctypes.create_unicode_buffer(120)
            u.GetClassNameW(h, cls, 120)
            if not cls.value.startswith("Chrome_WidgetWin"):
                return True
            t = ctypes.create_unicode_buffer(300)
            u.GetWindowTextW(h, t, 300)
            if needle not in t.value:
                return True
            found.append(h)
            return False

        u.EnumWindows(cb, 0)
        if not found:
            return False
        h = found[0]
        if u.IsIconic(h):
            u.ShowWindow(h, 9)      # SW_RESTORE
        u.SetForegroundWindow(h)
        u.BringWindowToTop(h)
        return True
    except Exception:
        return False


overlay.OVERLAY.configure(get_state=lambda: QUEUE.state(), get_alter=_overlay_alter, get_wings=_overlay_wings,
                          get_stats=lambda: ledger.stats(7),     # 일간 리포트 한 줄 + 7일 표 (파일만 읽는다)
                          collect_all=_overlay_collect_all, save_setting=_overlay_save_setting,
                          bring_front=_overlay_bring_front, open_drawer=_overlay_open_drawer, version=lambda: VERSION,
                          save_pos=lambda x, y: store.set_settings({"overlay_x": x, "overlay_y": y}),
                          save_offset=lambda x, y: store.set_settings({"overlay_off_x": x, "overlay_off_y": y}),
                          # 람다로 감싸는 이유: `app_window_gone` 은 이 줄보다 **아래**에 있다
                          # (hold 상태와 같이 두려고). 이름을 부를 때 찾게 미뤄 둔다.
                          app_gone=lambda: app_window_gone(), say=_say)


def sync() -> dict:
    """읽기 명령을 차례로 불러 캐시를 갱신한다. 하나가 실패해도 나머지는 계속한다. 큐가 도는 동안은 거부한다(파이프 직렬)."""
    if QUEUE.running:
        return {"ok": False, "error": "busy", "message": "큐가 실행 중입니다. 끝나거나 정지한 뒤 갱신하세요.", "steps": []}
    steps = []
    bodies = {}
    # 이관(옛 스키마 → 캐시로 재구성)은 반드시 캐시를 새 응답으로 덮기 전에 끝나야 한다.
    # 늦게 돌면 재구성 재료가 새 응답이 되어 옛 관찰이 통째로 사라진다 (실제로 1차 조회를 잃었다). 기동 시에도 한 번 부르지만 여기서도 보장한다.
    try:
        recipedb.load()
    except Exception as e:
        _say(f"recipedb: load before sync failed: {type(e).__name__}: {e}")
    for kind, command, body in READ_COMMANDS:
        r = _cli(command, body, timeout=60)
        steps.append({"kind": kind, "command": command, "ok": r.ok, "error": r.error, "message": r.message,
                      "elapsed": round(r.elapsed, 2)})
        if r.ok:
            store.set_cache(kind, r.body)
            bodies[kind] = r.body
    ok = all(s["ok"] for s in steps)
    if bodies:   # 관찰 누적 DB — 실패해도 sync 결과에는 영향 없다
        try:
            recipedb.observe(bodies)
        except Exception as e:
            _say(f"recipedb: observe failed: {type(e).__name__}: {e}")
    return {"ok": ok, "steps": steps, **snapshot()}


def snapshot() -> dict:
    """캐시만 읽어 화면이 쓸 모양으로. CLI 를 부르지 않으므로 언제든 즉시 답한다."""
    c = {k: store.get_cache(k) for k, _, _ in READ_COMMANDS}
    craft = work.recipes(c["craft"]["data"], "craft")
    alter = work.recipes(c["alter"]["data"], "alter")
    # 관찰 누적 DB 로 각 행의 경로 id(kind:이름#n)와 확인된 재료(known)를 채운다. 지금 부족한 것(missing)은 DB 에도 있으므로 known ⊇ missing
    try:
        db = recipedb.load()
    except Exception:
        db = recipedb.empty()
    recipedb.assign(db, "craft", craft)
    recipedb.assign(db, "alter", alter)
    w = work.works(c["works"]["data"] or {})
    st = work.stock(c["items"]["data"])
    # missing 행에 창고 수량과 합산 기준 short 를 덧붙인다 — 보유 판정은 가방+창고 (게임이 창고 재료를 원격 사용)
    work.apply_storage(craft, st["storage"])
    work.apply_storage(alter, st["storage"])
    inv = c["inventory"]["data"] if isinstance(c["inventory"]["data"], dict) else {}
    cur = c["currencies"]["data"] if isinstance(c["currencies"]["data"], list) else []
    gather = work.gatherables(c["gather"]["data"])
    unlocked = bool(c["craft"]["data"].get("craftingUnlocked", True)) if isinstance(c["craft"]["data"], dict) else True
    # 스킬·분류는 CLI 가 안 준다 → 이름 규칙으로 추정 (categories.py, data/categories.json 으로 덮어쓰기). inferred:true 로 표시
    ov = categories.load_overrides()
    for g in gather:
        g["skill"] = categories.gather_skill(g["name"], ov)
    for r in craft:
        r["cat"] = categories.recipe_category(r["name"], "craft", ov)
    fac_of = {wn: fname for fname, e in (db.get("facilities") or {}).items() if isinstance(e, dict)
              for wn in (e.get("works") or [])}   # 가공 작업명 → 시설 (대기열에서 관찰된 것만)
    for r in alter:
        r["cat"] = categories.recipe_category(r["name"], "alter", ov)
        r["facility"] = fac_of.get(r["name"])
    filters = {"inferred": True,
               "gatherSkills": categories.counts([g["skill"] for g in gather]),
               "recipeCats": {"craft": categories.counts([r["cat"] for r in craft]),
                              "alter": categories.counts([r["cat"] for r in alter])},
               "facilities": categories.counts([r["facility"] for r in alter if r.get("facility")])}
    # 검색·필터용 norm(이름+재료명)·initial(초성) + 탭별 요약 (summary.craft / .alter / .gather)
    work.index_rows(craft, "craft"); work.index_rows(alter, "alter"); work.index_rows(gather, "gather")
    tab_sum = {k: work.tab_summary(rows, k) for k, rows in (("craft", craft), ("alter", alter), ("gather", gather))}
    # stock = 가방, storage = 창고 합계. 보유 판정(short)은 둘의 합산 (apply_storage)
    return {"craft": craft, "alter": alter, **w, "stock": st["bag"], "storage": st["storage"], "gather": gather, "filters": filters,
            "inventory": {"cur": inv.get("CurrentInventoryWeightAsDecimal") or inv.get("CurrentInventoryWeight"),
                          "max": inv.get("MaxInventoryWeightAsDecimal") or inv.get("MaxInventoryWeight")},
            "currencies": [{"name": str(x.get("DisplayName", "")), "amount": x.get("Amount")}
                           for x in cur if isinstance(x, dict)],
            "craftingUnlocked": unlocked,
            "fetchedAt": {k: c[k]["fetched_at"] for k in c},
            "summary": {**work.summary(craft, alter, w), **tab_sum}}   # 기존 키(craftReady …) + 탭별 craft/alter/gather 요약


# ── 자동 업데이트 (경량판) ──
def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _update_url_rule() -> dict:
    """업데이트 주소 규칙 — 서버 판정(`_safe_url`)과 화면 문구가 **한 곳**에서 나온다.
    배포판(FROZEN·EMBED)은 https 만. 개발 모드(run.cmd)만 이 PC 의 http(127.0.0.1·localhost·[::1])를 받는다."""
    if FROZEN or EMBED:
        return {"https_only": True, "message": "업데이트 주소는 https:// 로 시작해야 합니다."}
    return {"https_only": False,
            "message": "업데이트 주소는 https:// 로 시작해야 합니다 (개발 모드에서만 http://127.0.0.1 같은 이 PC 주소도 됩니다)."}


def _safe_url(u: str) -> bool:
    """https 만 허용. 루프백 http 는 **개발 모드(FROZEN 아님)에서만** 로컬 테스트용으로 허용한다.
    배포판에서는 루프백 http 도 거절한다 — 설정 화면 문구(「https 로 시작해야」)와 규칙이 같아야 한다.

    프리픽스 비교(startswith("http://127.0.0.1"))로 판정하면 안 된다 — `http://127.0.0.1.evil.com` 과
    `http://127.0.0.1@evil.com` 이 둘 다 통과한다(실측). 이 함수는 update_url 저장은 물론
    자동 업데이트의 **exe 다운로드 주소**까지 검사하므로, 뚫리면 평문 HTTP 로 외부 서버에서 받은 파일을
    실행 중인 exe 자리에 넣게 된다. 호스트를 파싱해 정확히 루프백일 때만 http 를 허용한다."""
    u = (u or "").strip()
    if not u:
        return False
    try:
        p = urlparse(u)
        scheme = (p.scheme or "").lower()
        if scheme == "https":
            return True
        if scheme != "http" or FROZEN or EMBED:
            return False
        return (p.hostname or "").lower() in _LOOPBACK   # hostname 은 userinfo·포트를 뺀 순수 호스트
    except ValueError:   # 포트가 숫자가 아닌 등 파싱 불가
        return False


def update_check() -> dict:
    """설정의 latest.json 을 읽어 새 버전이 있는지 본다. 보내는 것은 없다(사용자 정보 없음)."""
    url = str(store.get_settings().get("update_url") or "").strip()
    if not _safe_url(url):
        # 주소가 있는데 규칙에 안 맞으면(개발 때 적은 http 를 배포판이 읽은 경우 등) 「없다」가 아니라 규칙을 말한다
        msg = _update_url_rule()["message"] if url else "업데이트 확인 주소(https)가 설정되지 않았습니다."
        return {"ok": False, "error": "no_url", "message": msg, "current": VERSION}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=8, **_update_tls()) as r:
            # urllib 은 https→http 되돌림(redirect)도 조용히 따라간다 — 마지막 주소도 같은 규칙(https)이어야
            # 한다. 아니면 중간자가 latest.json 의 url·sha256 을 갈아 끼울 수 있다.
            final = str(getattr(r, "geturl", lambda: url)() or url)
            if not _safe_url(final):
                return {"ok": False, "error": "bad_redirect", "message": "업데이트 정보가 https 가 아닌 곳으로 되돌려졌습니다.",
                        "current": VERSION}
            data = json.loads(r.read(65536).decode("utf-8-sig"))
    except Exception as e:
        return {"ok": False, "error": "fetch_failed", "message": f"업데이트 정보를 받지 못했습니다: {type(e).__name__}", "current": VERSION}
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad_manifest", "message": "latest.json 형식이 잘못되었습니다.", "current": VERSION}
    latest = str(data.get("version") or "")
    dl = str(data.get("url") or "")
    sha = str(data.get("sha256") or "").lower()
    ok_manifest = bool(latest) and _safe_url(dl) and re.fullmatch(r"[0-9a-f]{64}", sha or "") is not None
    zinfo = _manifest_zip(data.get("zip"))
    return {"ok": True, "current": VERSION, "latest": latest, "available": ok_manifest and _vtuple(latest) > _vtuple(VERSION),
            "url": dl, "sha256": sha, "notes": str(data.get("notes") or "")[:2000], "lite": LITE, "manifest_ok": ok_manifest,
            "embed": EMBED, "page": _update_page(url) if EMBED else "",
            "zip": zinfo, "inplace": bool(EMBED and zinfo)}


def _manifest_zip(z) -> dict | None:
    """latest.json 의 `zip` 칸 (1.0.2) — 임베디드 판이 제자리 업데이트로 받는 zip. 형식이 하나라도 틀리면 없는 것으로 본다
    (받는 곳을 여는 예전 길로 간다). 1.0.0 exe 는 이 칸을 모른다 — 그쪽은 `url`(받는 곳 페이지)·`sha256` 만 본다."""
    if not isinstance(z, dict):
        return None
    u, sha, size = str(z.get("url") or ""), str(z.get("sha256") or "").lower(), z.get("size")
    if not _safe_url(u) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= updater.MAX_ZIP_BYTES:
        return None
    return {"url": u, "sha256": sha, "size": size}


def _update_tls() -> dict:
    """업데이트 받기의 TLS — 평소에는 윈도 인증서 저장소 그대로(빈 dict). **시험 전용**: `MOBIW_DEV=1` 과
    `MOBIW_UPDATE_TEST_CA=<인증서 파일>` 이 **둘 다** 있을 때만 그 인증서를 믿는다 (자체 서명 https 로 끝까지 시험).
    https 규칙(`_safe_url`)은 그대로다 — http 를 허용하는 길이 아니다."""
    ca = os.environ.get("MOBIW_UPDATE_TEST_CA", "")
    if not ca or os.environ.get("MOBIW_DEV") != "1" or not os.path.isfile(ca):
        return {}
    import ssl
    return {"context": ssl.create_default_context(cafile=ca)}


def _update_page(update_url: str) -> str:
    """임베디드 판이 여는 **받는 곳** — 설정의 update_url(latest.json) 과 같은 사이트의 `/#download`.
    화면이 보낸 값이 아니라 서버 설정에서만 만든다 (update_apply 와 같은 원칙)."""
    from urllib.parse import urljoin
    u = (update_url or "").strip()
    if not _safe_url(u):
        return ""
    page = urljoin(u, "/") + "#download"
    return page if _safe_url(page) else ""


def _update_open_page() -> dict:
    """임베디드 판의 「업데이트」: **아무것도 바꿔 끼우지 않는다.** 실행 중인 것은 PSF 가 서명한 `pythonw.exe` 라
    exe 교체식 자동 업데이트를 하면 파이썬 자체를 우리 exe 로 덮어쓴다. 받는 곳만 기본 브라우저로 연다 —
    사용자가 새 zip 을 받아 폴더를 바꾼다 (자료는 `%LOCALAPPDATA%\\MobiWorks` 에 있어 그대로 남는다)."""
    page = _update_page(str(store.get_settings().get("update_url") or ""))
    if not page:
        return {"ok": False, "error": "no_url", "message": _update_url_rule()["message"]}
    if os.environ.get("MOBIW_NO_BROWSER") != "1":
        try:
            import webbrowser
            webbrowser.open(page)
        except Exception as e:
            return {"ok": False, "error": "open_failed", "message": f"받는 곳을 열지 못했습니다: {type(e).__name__} — {page}"}
    return {"ok": True, "opened": page, "restart": False,
            "message": f"받는 곳을 열었습니다 ({page}). 새 zip 을 받아 이 폴더를 바꿔 주세요 — 기록·설정은 그대로 남습니다."}


class _BadRedirect(Exception):
    """업데이트 파일 받기가 https 아닌 곳으로 되돌려졌다."""


def update_apply(info: dict) -> dict:
    """새 exe 를 받아 SHA256 을 검증하고 제자리에 바꿔 넣은 뒤, 같은 토큰으로 새 프로세스를 띄우고 자신은 끝난다 (경량판만).
    Windows 는 실행 중인 exe 의 '이름 바꾸기'를 허용하므로 외부 스크립트 없이 된다: 현재 exe → .bak, 새 파일 → 현재 이름.
    열려 있는 창의 페이지는 새 포트로 이동하므로 창이 닫혔다 열리지 않는다. .bak 은 새 프로세스가 지운다.

    **임베디드 판은 여기서 갈라진다** — latest.json 에 `zip` 칸이 있으면 제자리 업데이트(`_update_embed`), 없으면
    (옛 매니페스트) 받는 곳만 연다 (`_update_open_page`). 어느 쪽이든 화면이 보낸 주소·해시는 쓰지 않는다."""
    if EMBED:
        chk = update_check()
        if chk.get("ok") and chk.get("zip"):
            return _update_embed(info, chk)
        return _update_open_page()
    if not LITE:
        return {"ok": False, "error": "not_lite", "message": f"자동 업데이트는 경량판({APP_NAME}Lite.exe)에서만 지원합니다."}
    # **큐가 도는 중에는 업데이트하지 않는다**. 새 판이 queue.json 을 먼저 읽은 뒤 옛 판의
    # 러너가 카드를 끝내고 done 을 저장하면, 새 판의 첫 편집이 그것을 pending 으로 되돌려
    # **같은 카드를 다시 실행**한다 — 정령의 날개를 두 번 쓴다. 복원(`_restore_with_queue`)과 같은 검사.
    if QUEUE.is_busy():
        return {"ok": False, "error": "queue_busy",
                "message": "제작·채집 큐가 도는 중입니다. 정지한 뒤 업데이트하세요 — 도는 중에 바꾸면 끝난 카드가 다시 실행됩니다."}
    dl, sha, latest = str(info.get("url") or ""), str(info.get("sha256") or "").lower(), str(info.get("latest") or "")
    if not _safe_url(dl) or not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return {"ok": False, "error": "bad_manifest", "message": "다운로드 주소나 SHA256 이 없습니다."}
    # **화면이 보낸 url·sha256 을 믿지 않는다**. 서버가 설정의 latest.json 을 **지금 다시 읽어** 거기 적힌
    # 것과 같을 때만 받는다 — 그러지 않으면 토큰을 아는 화면(또는 확장 프로그램)이 아무 https 파일이나 그 파일의
    # 해시와 함께 보내 실행 중인 exe 자리에 넣을 수 있다. 설정의 `cli_exe`·`update_url` 을 검사해 임의 exe 를 막는
    # 뜻이 이 길 하나로 무너진다.
    chk = update_check()
    if not chk.get("ok"):
        return {"ok": False, "error": chk.get("error") or "no_url", "message": chk.get("message") or "업데이트 정보를 받지 못했습니다."}
    if not chk.get("manifest_ok") or dl != chk.get("url") or sha != chk.get("sha256"):
        return {"ok": False, "error": "manifest_mismatch",
                "message": "설정된 업데이트 주소의 latest.json 과 다른 파일입니다. 업데이트 확인을 다시 해 주세요."}
    os.makedirs(UPDATE_DIR, exist_ok=True)
    part = os.path.join(UPDATE_DIR, f"{APP_NAME}Lite.download.part")
    h = hashlib.sha256(); size = 0
    try:
        req = urllib.request.Request(dl, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            final = str(getattr(r, "geturl", lambda: dl)() or dl)
            if not _safe_url(final):           # https 아닌 곳으로 되돌려졌다 — 해시가 맞아도 안 받는다
                raise _BadRedirect(final)
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
        if isinstance(e, _BadRedirect):
            return {"ok": False, "error": "bad_redirect", "message": "업데이트 파일이 https 가 아닌 곳으로 되돌려져 받지 않았습니다."}
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
    # 이 프로세스가 포트를 놓아야 하므로 새 인스턴스에는 새 포트를 준다 (토큰은 그대로 → 열린 페이지가 이어갈 수 있다)
    new_port = _free_port()
    # PyInstaller 부트로더가 자식에게 주는 내부 변수(_PYI_*, _MEIPASS2)를 물려주면 새 exe 가 '이미 풀린 임시 폴더'를 쓰는
    # 자식 모드로 떠서(우리 옛 임시 폴더 → 곧 삭제됨) 화면 파일을 잃는다 — 반드시 걷어내고 띄운다
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env.update(MOBIW_PORT=str(new_port), MOBIW_TOKEN=TOKEN, MOBIW_LITE_REUSE="1",
               MOBIW_OLD_PID=str(os.getppid()), MOBIW_OLD_MEI=getattr(sys, "_MEIPASS", ""))
    env.pop("MOBIW_NO_BROWSER", None)
    _lite_release()   # 단일 인스턴스 뮤텍스·lite.json 을 먼저 놓는다
    try:
        import subprocess
        # PyInstaller 부트로더는 자식을 Job 객체에 넣고 그 안의 프로세스가 다 끝날 때까지 기다린다 — 새 인스턴스는 Job 에서 떼어 띄운다
        flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED | NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        try:
            proc = subprocess.Popen([cur], cwd=os.path.dirname(cur), env=env, close_fds=True, creationflags=flags)
        except OSError:
            proc = subprocess.Popen([cur], cwd=os.path.dirname(cur), env=env, close_fds=True, creationflags=flags & ~0x01000000)
    except OSError as e:
        _rollback(cur, bak, latest, "spawn 실패")
        return {"ok": False, "error": "spawn_failed", "message": f"새 버전을 실행하지 못했습니다: {e}"}
    # 새 판이 정말 뜨는지 확인한 뒤에만 자리를 넘긴다. 확인 전에 끝내면 새 판이 기동 중 죽었을 때 복구 수단이 없다.
    if not _wait_healthy(new_port, 20.0):
        try:
            proc.kill()
        except OSError:
            pass
        if _rollback(cur, bak, latest, "새 판이 20초 안에 응답하지 않음"):
            return {"ok": False, "error": "new_version_unhealthy",
                    "message": f"{latest} 가 실행되지 않아 이전 버전({VERSION})으로 되돌렸습니다. 계속 사용하셔도 됩니다."}
        return {"ok": False, "error": "rollback_failed",
                "message": f"{latest} 가 실행되지 않았고 되돌리기도 실패했습니다. 앱을 다시 설치해 주세요."}
    _say(f"update: {VERSION} -> {latest}, new instance healthy on port {new_port}")
    threading.Timer(2.0, _shutdown).start()
    return {"ok": True, "message": f"{latest} 로 업데이트합니다.", "restart": True, "port": new_port}


def _lite_hand_off(release: bool) -> None:
    """경량판일 때만 단일 인스턴스 자리를 놓거나(True) 되찾는다(False). 창 없는 검증 실행(경량판 아님)은 자리가 없다."""
    if LITE:
        _lite_release() if release else _lite_reacquire()


UPDATE_KEEP_SEC = 30.0   # 새 판이 뜬 뒤 .update\(옛 app·python)를 지우기까지 — 곧바로 죽는 판이면 손으로 되돌릴 수 있게


def _update_embed(info: dict, chk: dict) -> dict:
    """임베디드 판의 제자리 업데이트 (1.0.2 · updater.py 머리 주석). exe 길과 같은 가드를 그대로 둔다:
    큐가 돌면 안 받는다 · 주소·해시는 서버가 방금 다시 읽은 latest.json 의 것만 · https 만 · 크기 상한 · SHA256.
    그 위에 zip 짜임·이름(zip-slip)·풀린 판의 VERSION·Authenticode 를 본 뒤에야 바꾼다.
    어디서 실패하든 설치 폴더는 옛 판 그대로다 — `.update\\` 만 치우고 한국어 사유를 돌려준다."""
    if QUEUE.is_busy():   # exe 길과 같은 까닭 (update_apply 의 주석) — 받기도 전에 막는다
        return {"ok": False, "error": "queue_busy",
                "message": "제작·채집 큐가 도는 중입니다. 정지한 뒤 업데이트하세요 — 도는 중에 바꾸면 끝난 카드가 다시 실행됩니다."}
    latest, z = str(chk.get("latest") or ""), chk["zip"]
    if not chk.get("available"):
        return {"ok": False, "error": "not_newer", "message": f"이미 최신 버전입니다 (v{VERSION})."}
    want = str(info.get("latest") or "")
    if want and want != latest:   # 화면이 보여 준 판과 지금 latest.json 이 다르다 — 사용자가 고른 것이 아니다
        return {"ok": False, "error": "manifest_mismatch",
                "message": "설정된 업데이트 주소의 latest.json 이 바뀌었습니다. 업데이트 확인을 다시 해 주세요."}
    root = os.path.dirname(HERE)
    upd = updater.update_dir(root)
    logf = os.path.join(BASE, "update.log")
    updater.log(logf, f"embed: {VERSION} -> {latest} 시작 ({z['url']})")

    import shutil

    def fail(code: str, message: str) -> dict:
        updater.log(logf, f"embed: 실패 {code} — {message}")
        shutil.rmtree(upd, ignore_errors=True)
        return {"ok": False, "error": code, "message": message}

    shutil.rmtree(upd, ignore_errors=True)   # 지난번에 받다 만 것
    try:
        os.makedirs(upd, exist_ok=True)
    except OSError as e:
        return fail("replace_failed", f"설치 폴더에 쓸 수 없습니다: {e}")
    zpath = os.path.join(upd, f"update-{latest}.zip")
    h = hashlib.sha256(); size = 0
    try:
        req = urllib.request.Request(z["url"], headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
        with urllib.request.urlopen(req, timeout=30, **_update_tls()) as r, open(zpath, "wb") as f:
            final = str(getattr(r, "geturl", lambda: z["url"])() or z["url"])
            if not _safe_url(final):           # https 아닌 곳으로 되돌려졌다 — 해시가 맞아도 안 받는다
                raise _BadRedirect(final)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > min(updater.MAX_ZIP_BYTES, z["size"]):
                    raise ValueError("too large")
                h.update(chunk); f.write(chunk)
    except _BadRedirect:
        return fail("bad_redirect", "업데이트 파일이 https 가 아닌 곳으로 되돌려져 받지 않았습니다.")
    except Exception as e:
        return fail("download_failed", f"다운로드 실패: {type(e).__name__}")
    if h.hexdigest() != z["sha256"]:
        return fail("sha_mismatch", "받은 파일의 SHA256 이 latest.json 과 다릅니다. 적용하지 않았습니다.")
    try:
        stage = updater.extract(zpath, os.path.join(upd, latest))
        got = updater.staged_version(stage)
        if got != latest:
            raise updater.UpdateError("bad_zip", f"받은 zip 의 판({got or '?'})이 latest.json({latest})과 다릅니다.")
        n = updater.verify_signatures(stage)
        py_changed = updater.python_differs(os.path.join(root, "python"), os.path.join(stage, "python"))
    except updater.UpdateError as e:
        return fail(e.code, e.message)
    except OSError as e:
        return fail("extract_failed", f"zip 을 풀지 못했습니다: {e}")
    try:
        os.remove(zpath)
    except OSError:
        pass
    updater.log(logf, f"embed: 검사 통과 (서명 {n}개 Valid · 파이썬 {'바뀜' if py_changed else '같음'})")
    # 새 판에 새 포트를 준다 — 이 프로세스가 포트를 놓아야 한다. 토큰은 그대로 → 열린 창이 새 포트로 이어 간다 (exe 길과 같다)
    new_port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env.update(MOBIW_PORT=str(new_port), MOBIW_TOKEN=TOKEN, MOBIW_EMBED="1", MOBIW_OLD_PID=str(os.getpid()))
    if LITE:
        env["MOBIW_LITE_REUSE"] = "1"
        env.pop("MOBIW_NO_BROWSER", None)
    pyw = os.path.join(root, "python", "pythonw.exe")
    names = ["app", "MobiWorks.cmd"]
    _lite_hand_off(True)   # 단일 인스턴스 뮤텍스·lite.json 을 먼저 놓는다
    if not py_changed:
        # 흔한 경우: 파이썬은 같고 app\ 만 바뀐다 — 이 프로세스가 바꾸고 새 판이 응답하는 것을 본 뒤 넘긴다
        try:
            done = updater.swap(root, stage, names, VERSION, logf=logf)
        except updater.UpdateError as e:
            if e.code != "swap_failed":
                _lite_hand_off(False)
                return fail(e.code, e.message)
            done = None   # app\ 이 잡혀 있다 — 도우미가 우리가 끝난 뒤에 바꾼다
        if done is not None:
            try:
                proc = updater.spawn([pyw, os.path.join(root, "app", "server.py")], root, env)
            except OSError as e:
                updater.unswap(root, done, VERSION, latest, logf); _lite_hand_off(False)
                return fail("spawn_failed", f"새 버전을 실행하지 못했습니다: {e}")
            if not updater.wait_health(new_port, latest, os.getpid(), 20.0, proc):
                try:
                    proc.kill()
                    proc.wait(10)
                except Exception:
                    pass
                ok = updater.unswap(root, done, VERSION, latest, logf); _lite_hand_off(False)
                if ok:
                    return fail("new_version_unhealthy",
                                f"{latest} 가 실행되지 않아 이전 버전({VERSION})으로 되돌렸습니다. 계속 사용하셔도 됩니다.")
                return {"ok": False, "error": "rollback_failed",
                        "message": f"{latest} 가 실행되지 않았고 되돌리기도 실패했습니다. zip 을 다시 받아 풀어 주세요."}
            _say(f"update: {VERSION} -> {latest} (embed), new instance healthy on port {new_port}")
            updater.log(logf, f"embed: {latest} 응답 확인 (포트 {new_port}) — 자리를 넘긴다")
            threading.Timer(2.0, _shutdown).start()
            return {"ok": True, "message": f"{latest} 로 업데이트합니다.", "restart": True, "port": new_port}
    # 도우미: 파이썬이 바뀌었거나(실행 중인 pythonw.exe 가 python\ 을 잡고 있다) app\ 이 잡혀 있다.
    # 우리가 끝나기를 기다렸다가 바꾸고 새 판을 띄운다. 파이썬이 바뀌면 도우미는 **새 판의 파이썬**으로 돌고 python\ 을 복사한다.
    if py_changed:
        names = ["python"] + names
    helper_py = os.path.join(stage, "python", "pythonw.exe") if py_changed else pyw
    plan = {"root": root, "stage": stage, "old_pid": os.getpid(), "old_version": VERSION, "new_version": latest,
            "port": new_port, "names": names, "copy": ["python"] if py_changed else [], "log": logf}
    try:
        swap_py = os.path.join(upd, "swap.py")
        shutil.copyfile(os.path.join(HERE, "updater.py"), swap_py)
        plan_path = os.path.join(upd, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        updater.spawn([helper_py, swap_py, plan_path], upd, env)   # 토큰은 환경으로만 넘긴다 — 파일에 적지 않는다
    except OSError as e:
        _lite_hand_off(False)
        return fail("spawn_failed", f"업데이트 도우미를 띄우지 못했습니다: {e}")
    _say(f"update: {VERSION} -> {latest} (embed, helper: {', '.join(names)}) on port {new_port}")
    updater.log(logf, f"embed: 도우미에게 넘김 ({', '.join(names)}) — 끝나면 포트 {new_port} 로 뜬다")
    threading.Timer(2.0, _shutdown).start()
    return {"ok": True, "message": f"{latest} 로 업데이트합니다. 앱이 잠깐 닫혔다 다시 뜹니다.", "restart": True,
            "port": new_port, "wait": 90}


def _cleanup_update() -> None:
    """임베디드 판: 업데이트 뒤처리 — 옛 판·도우미가 끝나기를 기다렸다가 한동안 멀쩡히 돈 뒤 `.update\\` 를 지운다.
    지난번에 받다 만 것이 남아 있어도 같이 지운다 (업데이트 중이 아니면 거기 쓸 것이 없다)."""
    if not EMBED:
        return
    upd = updater.update_dir(os.path.dirname(HERE))
    if not os.path.isdir(upd):
        return

    def go():
        import ctypes
        import shutil
        k32 = ctypes.windll.kernel32
        for key in ("MOBIW_UPDATE_HELPER_PID", "MOBIW_OLD_PID"):
            pid = os.environ.get(key, "")
            if pid.isdigit() and int(pid) != os.getpid():
                h = k32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE — 끝나기만 기다린다
                if h:
                    k32.WaitForSingleObject(h, 60000); k32.CloseHandle(h)
        time.sleep(UPDATE_KEEP_SEC)
        err = None
        for _ in range(30):
            try:
                shutil.rmtree(upd)
                updater.log(os.path.join(BASE, "update.log"), f"cleanup: .update 지움 (v{VERSION})")
                return
            except FileNotFoundError:
                return
            except OSError as e:
                err = e; time.sleep(1.0)
        _say(f"update: could not remove .update: {err}")
    threading.Thread(target=go, daemon=True, name="update-cleanup").start()


def _shortcut_boot() -> None:
    """임베디드 판: 시작 메뉴 「모비웍스」를 만들거나 고친다 (켤 때마다 · 묻지 않는다 · 같으면 그대로).
    `.cmd` 실행기에는 아이콘을 못 붙여서 둔 길이다 (shortcut.py). 실패해도 앱은 떠야 하므로 로그만 남긴다."""
    try:
        r = shortcut.ensure_start_menu(BASE)
        if r.get("changed"):
            _say(f"shortcut: start menu -> {os.path.basename(r['path'])}")
    except Exception as e:
        _say(f"shortcut: start menu failed: {type(e).__name__}: {e}")


def _shortcut_desktop() -> dict:
    """설정 → 일반 「바탕화면에 바로가기 만들기」 (임베디드 판만)."""
    if not EMBED:
        return {"ok": False, "error": "not_embed", "message": "zip 으로 받은 판에서만 만들 수 있습니다."}
    try:
        r = shortcut.make_desktop()
    except Exception as e:
        return {"ok": False, "error": "shortcut_failed", "message": f"바로가기를 만들지 못했습니다: {type(e).__name__}"}
    return {"ok": True, "message": f"바탕화면에 「{shortcut.NAME}」 바로가기를 만들었습니다."}


def _wait_healthy(port: int, timeout: float) -> bool:
    """새 인스턴스가 실제로 서빙하는지 확인 (/api/health 가 우리 앱이고 내 pid 가 아닐 것)."""
    end = time.time() + timeout
    mine = os.getpid()
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                d = json.loads(r.read(4096).decode("utf-8"))
            if d.get("app") == APP and d.get("pid") != mine:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _rollback(cur: str, bak: str, latest: str, why: str) -> bool:
    """새 판을 물리고 이전 exe 를 제자리로 되돌린다. 우리는 아직 살아 있으므로 그대로 계속 쓰면 된다."""
    _say(f"update: rollback ({why})")
    try:
        if os.path.exists(cur):   # 실패한 새 판은 지우지 않고 보관 — 원인 분석용
            os.replace(cur, os.path.join(UPDATE_DIR, f"failed-{latest}.exe"))
        os.rename(bak, cur)
    except OSError as e:
        _say(f"update: rollback failed: {e}")
        return False
    _lite_reacquire()
    return True


def _cleanup_bak() -> None:
    """업데이트 뒤처리: 옛 부트로더가 남아 있으면 끝내고(우리 프로세스), 옛 임시 폴더와 <exe>.bak 을 지운다."""
    bak = sys.executable + ".bak"
    old_pid = os.environ.get("MOBIW_OLD_PID", "")
    old_mei = os.environ.get("MOBIW_OLD_MEI", "")
    if not FROZEN or not (os.path.exists(bak) or old_pid):   # 임베디드 판(FROZEN 아님)은 exe 를 바꾸지 않으니 치울 것도 없다
        return

    def go():
        if old_pid.isdigit():
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.OpenProcess(0x00100000 | 0x0001, False, int(old_pid))   # SYNCHRONIZE | TERMINATE
                if h:
                    # 옛 프로세스는 우리가 정말 뜨는지 확인한 뒤(최대 20초) 스스로 끝난다 — 그보다 넉넉히 기다린다
                    if k32.WaitForSingleObject(h, 30000) != 0:
                        k32.TerminateProcess(h, 0); _say(f"update: old bootloader {old_pid} did not exit — terminated")
                    k32.CloseHandle(h)
            except Exception as e:
                _say(f"update: old process check failed: {e}")
        if old_mei and os.path.isdir(old_mei) and os.path.basename(old_mei).startswith("_MEI"):
            import shutil
            shutil.rmtree(old_mei, ignore_errors=True)
        # 2단계 정리: 여기까지 왔어도 바로 지우지 않는다. 한동안 멀쩡히 서빙한 뒤에 지워야
        # 기동 직후 죽는 판일 때 사용자가 <exe>.bak 을 되돌려 쓸 수 있다.
        time.sleep(60.0)
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


def ui_prelude(prefs, theme: str | None = None) -> bytes:
    """화면 편의 값(localStorage)을 **다른 스크립트보다 먼저** 되살리는 인라인 스크립트.

    경량판은 실행마다 포트가 바뀌어 localStorage 가 매번 빈다. 서버에 적어 둔 값(store.get_ui prefs)을
    페이지 맨 앞에서 localStorage 에 도로 넣으므로, 각 화면 파일(theme.js·board.js …)은 **고칠 필요 없이**
    예전처럼 localStorage 를 읽는다. 서버 쪽 값이 기준이다: 목록의 열쇠 중 서버에 없는 것은 지운다
    (테마 「자동」 = 키 없음). prefs 가 None(한 번도 적힌 적 없음)이면 아무것도 건드리지 않는다
    — 그때는 shell.js 가 지금 localStorage 를 한 번 올려 서버 쪽 기록을 시작한다.
    값을 서버로 올리는 쪽은 ui/js/shell.js `syncPrefs`.

    `theme` (설정 `ui_theme` — auto|dark|light) 은 화면 편의 값이 **아니다**. 여기서는
    localStorage `mobiworks.theme` **캐시**를 그 값에 맞춰 둘 뿐이다 (auto = 키 없음) — 번쩍임 방지 스크립트와
    ui/js/theme.js 가 그 캐시를 읽는다. 실행마다 포트가 바뀌어 캐시는 늘 비지만, 같은 포트로 다시 뜬 창에 옛 값이
    남아 있으면 auto 로 바꾼 뒤에도 옛 고정이 번쩍인다 — 그래서 prefs 와 상관없이 늘 맞춘다."""
    data = json.dumps({"k": list(store.UI_PREF_KEYS), "v": prefs if isinstance(prefs, dict) else None,
                       "t": theme if theme in ("auto", "dark", "light") else None}, ensure_ascii=True).replace("<", "\\u003c")
    # 열쇠 목록은 window.__mwPrefs.keys 로 shell.js 에 넘긴다 — 목록을 두 곳에 적으면 언젠가 갈라진다.
    js = ("(function(){var d=" + data + ";window.__mwPrefs={keys:d.k,seeded:false};"
          "if(d.t)try{if(d.t==='auto')localStorage.removeItem('mobiworks.theme');else localStorage.setItem('mobiworks.theme',d.t);}catch(e){}"
          "if(!d.v)return;"
          "try{var s=window.localStorage;for(var i=0;i<d.k.length;i++){var k=d.k[i];"
          "if(Object.prototype.hasOwnProperty.call(d.v,k))s.setItem(k,d.v[k]);else s.removeItem(k);}"
          "window.__mwPrefs.seeded=true;}catch(e){}})();")
    return b"<script>" + js.encode("ascii") + b"</script>\n"


def render_index(html: bytes, token: str, theme: str | None = None, prelude: bytes = b"") -> tuple[bytes, str]:
    """index.html 바이트 → (서빙할 HTML, CSP 헤더). 순서가 중요하다:
    1) CRLF→LF — 브라우저는 인라인 스크립트를 LF 로 정규화한 뒤 CSP 해시를 잰다. CRLF 로 체크아웃된 파일이면 해시가 어긋나
       스크립트가 통째로 차단된다(core.autocrlf=true 인 Windows 클론).
    2) 토큰 치환, 3) ?theme=light|dark 검토·스크린샷용 고정(화이트리스트 두 값만, <html lang="ko"> 를 1회 치환 — 스크립트 본문은
       안 바뀌므로 해시 영향 없음), 4) **그 다음** 모든 <script> 블록을 해시한다 (UI 의 FOUC 방지용 두 번째 블록도 같이)."""
    html = html.replace(b"\r\n", b"\n")
    html = html.replace(TOKEN_MARK, token.encode("ascii"))
    html = html.replace(MODE_MARK, b"local")      # **이 PC 가 내준 화면**이라는 표시
    if theme in ("light", "dark"):
        html = html.replace(b'<html lang="ko">', f'<html lang="ko" data-theme="{theme}">'.encode("ascii"), 1)
    if prelude:   # 첫 <script> 앞 — 테마 FOUC 방지 스크립트보다도 먼저 돌아야 한다 (ui_prelude)
        at = html.find(b"<script")
        html = html[:at] + prelude + html[at:] if at >= 0 else html
    hashes = []
    pos = 0
    while True:
        a = html.find(b"<script>", pos)
        if a < 0:
            break
        b = html.find(b"</script>", a)
        if b < 0:
            break
        hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(html[a + 8:b]).digest()).decode("ascii") + "'")
        pos = b + 9
    # script-src 'self': ui/js/*.js 를 같은 서버에서 읽는다(화면을 파일별로 쪼갬). 인라인은 여전히 해시로만 허용
    csp = ("default-src 'self'; script-src 'self' " + " ".join(hashes) +
           "; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:*; font-src 'self'; "
           "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    return html, csp


def _token_ok(given) -> bool:
    """실행 토큰 비교. `hmac.compare_digest` 는 **비ASCII 문자열에 TypeError 를 던진다** —
    `?token=한글` 한 번이면 do_GET 이 통째로 터져 응답 없이 연결이 끊겼다(실측). 바이트로 바꿔 비교한다.
    타이밍 안전성은 그대로(compare_digest 는 bytes 도 상수 시간)."""
    if not TOKEN:
        return True   # 개발 서버(토큰 없음) — 출처 검사(_guard)만으로 막는다
    try:
        return hmac.compare_digest(str(given or "").encode("utf-8"), TOKEN.encode("utf-8"))
    except Exception:
        return False


def _static_key(raw_path: str) -> str:
    """정적 경로를 **Windows 가 여는 이름** 기준의 열쇠로. 대소문자·후행 점/공백·역슬래시·`.` 조각을
    고른다 (`..` 은 남겨 둔다 — 부르는 쪽이 보고 거절한다)."""
    segs = []
    for seg in raw_path.replace("\\", "/").split("/"):
        if seg == ".":
            continue
        if seg and not seg.strip(". "):      # `..`·`...`·공백뿐 — 전부 「위로」로 친다 (거절된다)
            segs.append("..")
            continue
        segs.append(seg.strip().rstrip(". ").lower())
    return "/".join(segs)


def _is_index_path(path: str) -> bool:
    """Windows 는 파일명의 대소문자·후행 공백·후행 점을 무시한다. `/index.HTML` 로 요청하면 정적 서빙이
    **토큰 치환도 CSP 헤더도 없는 원본**을 내준다(실측: CSP 헤더 0건). index.html 은 오직 _serve_index 로만 낸다."""
    base = path.rsplit("/", 1)[-1]
    return base.strip().rstrip(". ").lower() in ("index.html", "index.htm")


# 연결을 열어 두기만 하는 소켓이 동시연결 슬롯을 쥐는 시간.
# 슬롯은 스레드를 띄우기 **전**(process_request)에 잡을 수밖에 없다 — 요청 라인을 읽은 뒤에 잡으면
# 유휴 연결마다 스레드가 생겨 스레드 고갈로 더 나빠진다(socketserver 구조). 그래서 상한을 올리는 대신
# **점유 시간을 줄이는 것**이 유일한 실효 방어다: 헤더가 다 올 때까지는 짧게, 파싱된 뒤에는 넉넉히.
HEADER_TIMEOUT = 5    # 요청 라인 + 헤더. 로컬 브라우저는 즉시 보낸다 — 이 안에 안 오면 공격이거나 죽은 연결
BODY_TIMEOUT = 30     # 본문 읽기·응답 쓰기 (localhost 라 넉넉하다). 오래 도는 핸들러 자체는 소켓 I/O 가 아니라 영향 없다
# 밖(터널)에서 온 요청의 본문 시간. 폰이 보내는 본문은 몇백 바이트다 — 30초를 주면 `Content-Length` 만 적고
# 본문을 안 보내는 연결 하나가 슬롯을 30초 쥔다 (실측: 128개면 앱이 30초씩 먹통). 터널을 지나면
# 보낸 곳이 전부 127.0.0.1 로 보여 IP 로는 못 가른다 — 시간을 줄이는 것이 유일한 방어다.
BODY_TIMEOUT_REMOTE = 10
DRAIN_TIMEOUT = 2     # 거절하며 본문을 비울 때 — 안 오는 본문을 기다리지 않는다 (같은 이유)


# ── HTTP ──
def _preset_op(op: str, p: dict):
    """프리셋 다섯 가지. 돌려주는 것: (본문, HTTP 코드).

    **공유 코드를 푸는 자리(`preset_import`)가 유일하게 남의 자료를 받는 곳이다.**
    `presets.decode` 가 길이·압축 상한·crc·모양을 다 보고, 그래도 담는 것은 `Queue.add` 를
    지난다 — 검증이 두 겹이다. 여기서 편의로 직접 꽂으면 그 두 겹이 다 사라진다.
    """
    rows = presets.load_all()
    if op == "preset_save":
        name = _s(p.get("name")).strip()[:presets.MAX_NAME] or time.strftime("%m/%d %H:%M 프리셋")
        spec = QUEUE.preset_capture(name)
        if not presets.count_cards(spec):
            return {"ok": False, "error": "empty", "message": "보드가 비어 있어 저장할 것이 없습니다."}, 400
        row = {"id": uuid.uuid4().hex[:10], "name": name, "at": int(time.time()), "spec": spec}
        rows = [x for x in rows if _s(x.get("name")) != name]   # 같은 이름은 덮어쓴다
        rows.insert(0, row)
        if len(rows) > presets.MAX_SAVED:
            return {"ok": False, "error": "too_many",
                    "message": f"프리셋은 {presets.MAX_SAVED}개까지입니다. 쓰지 않는 것을 지우세요."}, 400
        presets.save_all(rows)
        return {"ok": True, "preset": _preset_view(row), "presets": [_preset_view(x) for x in rows]}, 200
    if op == "preset_delete":
        pid = _s(p.get("id"))
        left = [x for x in rows if _s(x.get("id")) != pid]
        if len(left) == len(rows):
            return {"ok": False, "error": "not_found", "message": "그 프리셋이 없습니다."}, 400
        presets.save_all(left)
        return {"ok": True, "presets": [_preset_view(x) for x in left]}, 200
    if op == "preset_code":
        row = next((x for x in rows if _s(x.get("id")) == _s(p.get("id"))), None)
        if row is None:
            return {"ok": False, "error": "not_found", "message": "그 프리셋이 없습니다."}, 400
        try:
            spec = presets.validate(row.get("spec"))
        except presets.BadCode as e:
            return {"ok": False, "error": "bad_preset", "message": e.reason}, 400
        return {"ok": True, "code": presets.encode(spec), "name": spec["n"],
                "cards": presets.count_cards(spec)}, 200
    # 여기서부터는 보드를 바꾼다
    mode = "replace" if _s(p.get("mode")) == "replace" else "append"
    if op == "preset_load":
        row = next((x for x in rows if _s(x.get("id")) == _s(p.get("id"))), None)
        if row is None:
            return {"ok": False, "error": "not_found", "message": "그 프리셋이 없습니다."}, 400
        try:
            spec = presets.validate(row.get("spec"))
        except presets.BadCode as e:
            return {"ok": False, "error": "bad_preset", "message": e.reason}, 400
    else:                                   # preset_import — 남이 준 코드
        try:
            spec = presets.decode(_s(p.get("code")))
        except presets.BadCode as e:
            return {"ok": False, "error": "bad_code", "message": e.reason}, 400
    r = QUEUE.preset_apply(spec, snapshot(), store.get_settings(), mode)
    return r, 200 if r.get("ok") else 400


def _preset_view(row: dict) -> dict:
    """목록에 나가는 모양 — 내용(spec)은 싣지 않는다. 필요할 때 코드로 뽑는다."""
    spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
    return {"id": row.get("id", ""), "name": row.get("name", ""), "at": row.get("at", 0),
            "cards": presets.count_cards(spec),
            "groups": sum(1 for x in (spec.get("i") or []) if isinstance(x, dict) and x.get("t") == "G")}


class H(SimpleHTTPRequestHandler):
    timeout = HEADER_TIMEOUT   # setup() 이 소켓에 건다 — 첫 요청 라인을 기다리는 시간
    # 기본 `Server:` 머리는 파이썬 판까지 적는다 (`SimpleHTTP/0.6 Python/3.12.10`) — 밖(터널)에도 그대로. 이름만 남긴다.
    server_version = APP_NAME
    sys_version = ""

    def do_HEAD(self):
        """**HEAD 는 받지 않는다.** 물려받은 `do_HEAD` 는 우리 `_get` 을 안 거쳐 정적 서빙으로 바로 간다 —
        밖에서 `HEAD /` 가 문 없이 200 이 되고 화면 파일의 크기·수정 시각이 샌다. 쓰는 곳이 없다."""
        self.send_response(405)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def list_directory(self, path):
        """폴더 목록은 내주지 않는다 (`/js/`·`/css/`·`/folio/fonts/` 의 파일 이름이 전부 보인다)."""
        _json(self, {"ok": False, "error": "not_found"}, 404)
        return None

    def parse_request(self):
        """헤더까지 다 읽었으면 타임아웃을 늘린다. 여기까지 온 연결은 정상 요청이므로 본문·응답에 시간을 준다."""
        ok = super().parse_request()
        if ok:
            try:
                self.connection.settimeout(BODY_TIMEOUT_REMOTE if self._is_remote() else BODY_TIMEOUT)
            except OSError:
                pass
        return ok

    def handle_one_request(self):
        """요청을 기다리는 동안에는 짧게. (이 서버는 HTTP/1.0 이라 응답마다 연결을 닫는다 — 요청 하나에
        연결 하나이므로 이 타임아웃이 곧 유휴 연결의 슬롯 점유 시간이다.)"""
        try:
            self.connection.settimeout(HEADER_TIMEOUT)
        except OSError:
            pass
        return super().handle_one_request()

    # `.webmanifest` 는 파이썬 mimetypes 표에 없다 — 그냥 두면 application/octet-stream 으로
    # 나가고 브라우저가 매니페스트로 받아 주지 않아 **설치가 안 된다**(PWA). 여기서 못 박는다.
    # (dict 사본을 만든다 — SimpleHTTPRequestHandler 의 클래스 표를 그대로 고치면 다른 서버까지 바뀐다)
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json",
                      ".svg": "image/svg+xml",
                      # 연출 화면(opening2.html)의 글꼴 — mimetypes 표에 없어 octet-stream 으로 나가면
                      # nosniff 아래에서 글꼴로 안 받아 줄 수 있다 (ui/folio/fonts/README.md)
                      ".woff2": "font/woff2"}

    def __init__(self, *a, **k):
        super().__init__(*a, directory=UI_DIR, **k)

    def log_message(self, fmt, *args):   # 조용히
        pass

    def do_GET(self):
        # do_POST 와 같이 어떤 입력에도 응답 없이 끊기지 않게 감싼다 (경로의 널바이트가 open() 에서
        # ValueError 를 던져 연결이 리셋되던 실측 사례가 있었다)
        try:
            self._get()
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                _json(self, {"ok": False, "error": "internal", "message": type(e).__name__}, 500)
            except Exception:
                pass

    # 열쇠 없이 답해도 되는 유일한 읽기 길 — 「여기가 모비웍스다」밖에 안 알려 준다.
    GET_OPEN = {"/api/health"}
    # 내려받기 두 길(`/api/recipes/export`·`/api/backup`)도 **공용 문을 탄다**.
    # `?token=` 쿼리로 받으면 토큰이 주소에 실려 기록·Referer·확장 프로그램으로 샐 수 있다.
    # 화면이 fetch(헤더) → blob 으로 받으므로
    # **쿼리 토큰은 어디서도 받지 않는다.**

    def _folio(self, path: str):
        """폴리오 길로 넘긴다. **문은 이미 지났다** — 여기서 다시 묻지 않는다.

        `/api/folio/now` → 그쪽이 아는 `/api/now` 로 바꿔서 그쪽 핸들러를 부른다.
        핸들러는 **우리 `self`** 를 받는다 — 소켓도 헤더도 우리 것이고, 그래서 답도
        우리 연결로 나간다."""
        rest = path[len(FOLIO_PREFIX):].split("?", 1)[0]
        if rest in FOLIO_NEVER or rest.startswith(FOLIO_NEVER_PREFIX):
            return _json(self, {"ok": False, "error": "not_found",
                                "message": "이 일은 모비웍스 쪽 길로 합니다."}, 404)
        eng = folio_engine()
        keep = self.path
        # **`_post` 를 직접 부른다, `do_POST` 가 아니라.** 그쪽 `do_POST` 는 `self._post()` 를
        # 부르는데 `self` 는 우리 인스턴스라 **우리 `_post` 로 되돌아왔다** — 그래서
        # `/api/folio/play` 가 404 였고, `/api/folio/queue` 는 **우리 작업 큐를** 비웠다(실측).
        # GET 은 그쪽 `do_GET` 이 본문째라 그대로 된다.
        # `_folio_pass`: 문은 이미 폴리오 길 이름으로 지났다. 그쪽 핸들러가 바뀐 이름
        # (`/api/play`)으로 `_remote_blocked` 를 다시 물으면 **우리 표로 다시 재게 되어**
        # 틀린 답이 나온다 — 그 두 번째 물음은 통과시킨다.
        self._folio_pass = True
        try:
            self.path = "/api/" + path[len(FOLIO_PREFIX):] + (("?" + keep.split("?", 1)[1]) if "?" in keep else "")
            return eng.H.do_GET(self) if self.command == "GET" else eng.H._post(self)
        finally:
            self.path = keep
            self._folio_pass = False

    # 그쪽 핸들러가 쓰는데 우리에게 없던 것 — **빌려만 온다.** 여기서 다시 구현하면
    # 두 벌이 되고, 두 벌이면 갈라진다.
    _stamp = property(lambda self: folio_engine().H._stamp.__get__(self))
    _take_video = property(lambda self: folio_engine().H._take_video.__get__(self))

    # ── 방송 손님 길 (broadcast.py · docs/handoff/broadcast-api.md §4) ──────────────
    # 손님은 기기 쪽지가 없다. 그래서 이 네 길은 **아래 문(`_remote_blocked`·`_guard`)보다 먼저** 여기서 끝나고,
    # 다른 어떤 길로도 이어지지 않는다 — 손님 토큰(`X-MobiWorks-Guest`)은 이 네 길에서만 읽는다.
    # Host 는 방송 터널 주소(또는 이 PC)만, Origin 은 우편함 사이트·적어 둔 폰 사이트만.
    BC_GUEST = {"/api/bc/join": "join", "/api/bc/room": "room", "/api/bc/req": "req", "/api/bc/leave": "leave"}
    BC_GUEST_HEADER = "X-MobiWorks-Guest"
    _bc_cors = ""

    def _bc_guest(self, path: str):
        post = self.command == "POST"
        host = (self.headers.get("Host") or "").lower()
        local = host in _ok_hosts
        if not local and host.split(":")[0] not in _bc_hosts():
            if post:
                self._drain()
            return _json(self, {"ok": False, "error": "forbidden"}, 403)
        origin = (self.headers.get("Origin") or "").strip().rstrip("/").lower()
        org = _bc_cors_origin(self)
        if origin and not org:
            same = origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}") if local                 else origin.split("://", 1)[-1] in _bc_hosts()
            if not same:
                if post:
                    self._drain()
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
        self._bc_cors = org
        if not local:
            _bc_activity()                    # 손님 요청도 터널 활동이다 (유휴 자동 닫기)
        body = {}
        if post:
            body = _read_json(self)
            if "_error" in body:
                return _json(self, {"ok": False, "error": "bad_request", "message": body["_error"]}, 400)
        tok = (self.headers.get(self.BC_GUEST_HEADER) or "").strip()[:64]
        st, out = broadcast.guest(self.BC_GUEST[path], body, tok, _remote_try_key(self))
        return _json(self, out, st)

    def _get(self):
        u = urlparse(self.path)
        if u.path == "/api/bc/room":   # 방송 손님 길 — 기기 쪽지 없이, 아래 문과 따로 논다 (`_bc_guest`)
            return self._bc_guest(u.path)
        # ── 문은 여기 한 곳이다 ────────────────────────────────────────────
        # 길마다 손으로 `_guard()` 를 부르면 **반드시 빠뜨린다** — 하나만 빠져도 터널 주소만 알면
        # 인증 없이 읽힌다.
        # 새 길이 생겨도 저절로 막히도록 **거절이 기본**인 문을 맨 앞에 둔다.
        # (아래 길들이 각자 부르는 `_guard()` 는 그대로 둔다 — 두 번 불러도 해롭지 않다.)
        if self._is_remote():
            if self._remote_blocked(u.path, post=False):
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            # **밖에는 화면을 내주지 않는다.** index.html 에는 실행 토큰이 박혀 나가고,
            # 그 토큰이면 이 PC 의 모든 API 가 열린다 (밖에서 `/` 를
            # 부르면 토큰이 그대로 딸려 나간다). 정적 파일도 같이 막는다: 화면을 안 내주는데
            # 조각만 내줄 이유가 없다.
            # 폰 화면은 **바깥 사이트에 둔다** — 그래야 「로그인할 화면조차 PC 가 안 준다」는
            # 닭·달걀이 풀린다.
            if not u.path.startswith("/api/"):
                # **예외 하나 — 커버 그림** (폰 = PC). 화면·스크립트가 아니라 그림이고,
                # 쪽지(머리)를 든 요청만 받는다 — `<img>` 는 머리를 못 붙이므로 폰 화면이 fetch → blob 으로 받는다.
                # 쪽지 없는 요청(주소만 아는 남)은 여전히 403.
                if u.path.lower().startswith("/folio/covers/") and self._guard():
                    return self._serve_cover(unquote(u.path).replace("\\", "/")[len("/folio/covers/"):])
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
        if u.path.startswith("/api/") and u.path not in self.GET_OPEN and not self._guard():
            return _json(self, {"ok": False, "error": "forbidden"}, 403)
        # **글자로 적는다.** 변수로 쓰면 길 뽑는 눈(tests/test_remote.py)이 못 본다 —
        # 그러면 이 길이 원격 전수 행렬에서 통째로 빠진다.
        if u.path.startswith("/api/folio/"):
            return self._folio(u.path)
        if u.path == "/api/shell":   # 레일이 1초마다 두드린다 — CLI 를 안 탄다
            return _json(self, shell_state())
        if u.path == "/api/ui/state":   # 마지막에 보던 곳 · 화면 편의 값
            return _json(self, {"ok": True, "ui": store.get_ui(), "start": _start_path()})
        # 검사는 **퍼센트 디코딩한** 경로로 해야 한다 — urlparse 는 %00·%20 을 풀지 않으므로
        # `/server.py%00.css`(파일 열기에서 ValueError) 와 `/index.html%20`(후행 공백 → Windows 가 같은 파일로 연다)이
        # 그냥 통과했다(실측). 정적 서빙(SimpleHTTPRequestHandler)은 자기 안에서 unquote 하므로 여기서 맞춰 본다.
        try:
            raw_path = unquote(u.path, errors="surrogatepass")
        except Exception:
            raw_path = u.path
        if "\0" in raw_path:   # 널바이트 경로 — 파일 열기에서 ValueError 가 난다
            return _json(self, {"ok": False, "error": "bad_request"}, 400)
        if u.path == "/api/health":   # "이 포트가 정말 우리 앱인지" 확인하는 용도 (토큰 없이 답한다)
            if self._is_remote():     # 밖에서는 「여기 맞다」만 — 판·pid 를 알려 줄 이유가 없다
                return _json(self, {"app": APP})
            return _json(self, {"app": APP, "version": VERSION, "pid": os.getpid(), "frozen": FROZEN, "embed": EMBED, "lite": LITE,
                                "cli": "demo" if cli.DEMO else ("disabled" if cli.NO_CLI else "real"),   # 화면 배지용
                                "parent": int(PARENT) if PARENT.isdigit() else None})
        if u.path == "/api/debug/threads":   # 스레드 스택 덤프 — 「어디서 기다리나」를 밖에서 본다 (이 PC 에서만, 토큰 없이는 안 됨)
            if self._is_remote() or not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"ok": True, "threads": _thread_dump()})
        if u.path == "/api/hold":   # 창이 살아 있는 동안 열어 두는 연결 (경량판 종료 판정). 5초마다 한 바이트를 보내 끊김을 감지
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            global _holds, _had_hold, _last_hold_close, _hold_seq
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            _release_slot()   # 이 연결은 창이 사는 내내 열려 있다 — 일반 요청용 동시연결 슬롯을 먼저 놓는다
            now = time.time()
            with _hold_lock:
                _hold_seq += 1; key = _hold_seq
                _holds += 1; _had_hold = True
                _hold_conns[key] = {"at": now, "stop": ""}
                _hold_evict(_hold_conns)
            try:
                last_write = 0.0; gone = False
                while True:
                    with _hold_lock:
                        stop = _hold_conns[key]["stop"]
                    why = _hold_step(time.time(), now, stop, gone)
                    if why:
                        break
                    if time.time() - last_write >= 5.0:
                        self.wfile.write(b".")
                        self.wfile.flush()
                        last_write = time.time()
                    gone = _peer_gone(self.connection, HOLD_POLL)   # 기다리며 상대가 닫혔는지 본다 (닫히면 즉시 깬다)
            except (OSError, ValueError):
                pass
            finally:
                with _hold_lock:
                    _holds -= 1; _last_hold_close = time.time(); _hold_conns.pop(key, None)
            return
        if u.path == "/api/state":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            # **폰(밖)은 PC 가 읽어 둔 값만 본다** — 밖에서 온 요청과 `nocli=1` 은
            # `cli.probe()`(= `status` 실행)를 부르지 않고 이 PC 의 화면이 15초마다 읽어 둔 마지막 값을 낸다.
            # 폰이 열릴 때·15초마다 PC 의 CLI 가 도는 일이 없게 — 화면이 안 부르는 것으로는 모자라 서버가 막는다.
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if self._is_remote() or q.get("nocli"):
                return _json(self, {"cli": cli.last_probe(), "cached": True})
            return _json(self, {"cli": _probe()})
        if u.path == "/api/work":   # 캐시만 읽는다 — CLI 를 부르지 않으므로 즉시 답한다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, snapshot())
        if u.path == "/api/recipes":   # 관찰 누적 DB (레시피 사전)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            # sources: 아이템 → 얻는 방법. 재고 탭의 「가공/제작/채집 담기」 버튼이 쓴다.
            # 새 엔드포인트를 만들지 않고 기존 응답에 얹는다. 내보내기(/api/recipes/export)는 DB 원본이라 그대로 둔다.
            db = recipedb.load()
            out = recipedb.public(db)
            out["sources"] = recipedb.source_index(db)
            return _json(self, out)
        if u.path == "/api/recipes/export":
            # 화면은 fetch(헤더) → blob 으로 받는다 — 토큰은 주소에 싣지 않는다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            data = json.dumps(recipedb.public(recipedb.load()), ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="mobiworks-recipes.json"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/backup":   # 개인화 자료 zip 내려받기 — 공용 문(헤더 토큰)·REMOTE_NEVER. 쿼리 토큰은 안 받는다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            data = store.backup_bytes(VERSION)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{store.backup_filename()}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/report":   # 문제 신고 zip — 공용 문(헤더 토큰)·REMOTE_NEVER. CLI 는 부르지 않는다
            if self._is_remote() or not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            name, data = report_zip()
            _say(f"report: 문제 신고 zip {len(data)} bytes")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/settings":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            # nocli=1: 설정 화면을 열 때. 게임이 꺼져 있으면 probe 가 5초 걸려 폼이 늦게 채워진다
            # overlay: 밴드가 실제로 떠 있는지·폭·단축키 등록 결과 (오버레이 탭이 그대로 보여 준다)
            st = _public_settings(store.get_settings())
            if self._is_remote():
                # **이 PC 의 속사정은 밖으로 안 보낸다** — 파일 경로·업데이트 주소·터널 주소.
                # 폰에서 못 바꾸는 값이기도 하다 (POST 쪽에서 오버레이 키를 걸러 낸다).
                st = {k: v for k, v in st.items() if _remote_setting_ok(k)}
            # 밖에서는 `nocli` 가 없어도 CLI 를 묻지 않는다 — 폰은 PC 가 읽은 값만 본다 (`/api/state` 와 같은 규칙)
            return _json(self, {"settings": st, "cli": None if (q.get("nocli") or self._is_remote()) else _probe(),
                                "overlay": None if self._is_remote() else overlay.OVERLAY.status(),
                                # 업데이트 주소 규칙 — 화면이 저장 전에 같은 규칙·같은 문구로 알려 준다
                                "update_url_rule": None if self._is_remote() else _update_url_rule(),
                                # 폰(살아 있는 쪽지 — `_guard` 를 지났다)에게 이 기기의 범위를 같이 준다 — 설정 화면이
                                # `run` 에서만 바꿀 수 있는 줄(`REMOTE_SETTINGS_RUN_ONLY`)을 edit 에서 감춘다
                                **({"scope": _remote_cfg()["scope"]} if self._is_remote() else {})})
        if u.path == "/api/queue":   # 큐 상태 — CLI 를 부르지 않는다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            st = QUEUE.state()
            # 밴드에서 「대기 목록」·「⚙」를 누르면 창을 앞으로 올린 뒤 **어느 탭을 열지**를
            # 여기에 한 번만 실어 보낸다. 창을 새로 띄울 때는 주소(#탭)로 가지만, 이미 떠 있는
            # 창은 주소를 바꿀 길이 없다.
            global _go_tab, _go_drawer
            # 이 신호는 **이 PC 의 창** 몫이다 — 폰(밖)이 폴링 중이면 먼저 읽는 쪽이 가져가 폰에서 탭·서랍이 열린다.
            # 밖의 요청에는 싣지도 지우지도 않는다.
            if not self._is_remote():
                if _go_tab:
                    st = dict(st, goTab=_go_tab)
                    _go_tab = ""
                if _go_drawer:
                    st = dict(st, goDrawer=True)
                    _go_drawer = False
            return _json(self, st)
        if u.path == "/api/queue/preview":   # 시작 전 확인창용: 무엇을 몇 번 부를지
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, QUEUE.preview(store.get_settings()))
        if u.path == "/api/view/queue":
            # **화면 상태 한 벌** — 칸 판정·진행 글자·예상 회수·오류 안내를 서버가 계산한다.
            # 화면은 이 값을 그대로 뿌리기만 한다. CLI 를 부르지 않는다.
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            try:
                # 미리보기의 **실행 전 점검**(`queue_precheck` → `get_activity`)은 여기서 끈다 — 이 길은 날개 예상치(`wings`)만
                # 쓰고, 점검은 시작 확인창(`/api/queue/preview`)이 한다. 켜 둔 채로는 폰(밖)이 이 길을 부를 때마다
                # PC 의 CLI 가 돌았다 (tests/test_phone_reads_pc.py 가 잡았다). wings 는 점검과 무관하게 같은 값이다.
                wings = QUEUE.preview(dict(store.get_settings(), queue_precheck=False)).get("wings")
            except Exception:
                wings = None          # 미리보기가 실패해도 화면은 떠야 한다
            return _json(self, viewmodel.queue_view(QUEUE.state(), snapshot(), wings))
        if u.path == "/api/view/works":   # 가공 화면 상태 — 캐시만 읽는다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, viewmodel.works_view(snapshot()))
        if u.path == "/api/presets":   # 저장해 둔 프리셋 목록 — 파일만 읽는다
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"ok": True, "presets": [_preset_view(x) for x in presets.load_all()]})
        if u.path == "/api/stats":   # 날개·산출 기록 — 파일만 읽는다 (CLI 를 부르지 않는다)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            days = 14
            try:
                days = int((parse_qs(u.query).get("days") or ["14"])[0])
            except (TypeError, ValueError):
                pass          # 이상한 값이면 기본값. 범위는 ledger.stats 가 자른다
            st = ledger.stats(days)
            # 오늘 한 줄 — 오버레이 밴드와 **같은 조립기**다 (`ledger.day_line`). 화면마다 따로 짜면 어긋난다
            st["line"] = ledger.day_line(st)
            return _json(self, st)
        if u.path == "/api/update/check":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, update_check())
        if u.path.startswith("/api/"):
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if raw_path.replace("\\", "/").lower().startswith("/folio/covers/"):   # 곡 번호로 고르는 카드 커버
            return self._serve_cover(raw_path.replace("\\", "/")[len("/folio/covers/"):])
        if [x for x in _static_key(raw_path).split("/") if x][:1] == ["live"]:
            # 방송 손님 페이지(`ui/live/`)는 **우편함 사이트에만** 있는 화면이다 — 이 PC 는 어떤 철자로도 내주지 않는다
            # (정적 서빙으로 나가면 CSP 도 치환도 없는 원본이 된다 · `/live/index.html` 이 우리 첫 화면으로 가지도 않게 먼저 본다)
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if u.path in ("/", "/index.html"):
            return self._serve_index()
        if _is_index_path(raw_path):   # /index.HTML · "/index.html " 같은 변형이 정적 서빙(CSP·토큰 없음)으로 새지 않게
            return self._serve_index()
        # **폴리오 화면·조각은 이름을 고른 뒤 찾는다.** `/folio/settings.html.`·`%20`·대문자·
        # `\`·`/folio/../index.html` 이 전부 정적 서빙(CSP 도 치환도 없는 원본)으로 샐 수
        # 있다(`_is_index_path` 는 index 만 본다). Windows 가 같은 파일로 여는
        # 변형은 전부 같은 열쇠가 되게 한다.
        nkey = _static_key(raw_path)
        if ".." in nkey.split("/"):                # 위로 올라가는 길은 쓸 데가 없다
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if ":" in raw_path:
            # NTFS 대체 스트림 — `/index.html::$DATA` 는 Windows 가 index.html 그 파일로 열어 준다. 아래의
            # 「.html 은 안 낸다」·「.json 은 안 낸다」를 전부 지나쳐 CSP 도 치환도 없는 원본이 나간다.
            # ui/ 아래 파일 이름에는 `:` 가 있을 수 없다 (Windows 가 금지한다).
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if nkey in self.FOLIO_PAGES:
            return self._serve_index(self.FOLIO_PAGES[nkey])
        if nkey in self.WINDOW_PAGES:
            return self._serve_index(self.WINDOW_PAGES[nkey])
        if nkey in self.OVERLAY_PAGES:
            return self._serve_index(self.OVERLAY_PAGES[nkey], prefs=False)
        if nkey in self.FOLIO_ASSETS:
            return self._serve_folio_asset(*self.FOLIO_ASSETS[nkey])
        if nkey.endswith((".html", ".htm")):      # 화면은 오직 `_serve_index` 로만 나간다
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if raw_path.lower().rstrip(". ").endswith((".py", ".tmp", ".json", ".log")) or "/." in raw_path:   # ui/ 아래에 없지만, 혹시 몰라 원천 차단 (대소문자·후행 점/공백 무시)
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        return super().do_GET()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", getattr(self, "_cache_ctl", "") or "no-store")
        self._cache_ctl = ""
        super().end_headers()

    # 앱에 딸린 기본 커버 풀 — 사용자 폴더에 커버·기본 풀이 없을 때 (store.cover_for)
    COVERS_BUNDLED = os.path.join(UI_DIR, "folio", "covers", "default")

    def _serve_cover(self, rest: str):
        """`/folio/covers/<곡 번호>` — 그 번호의 커버 그림. **404 가 없다**: 고른 그림 → 번호 이름 파일 →
        기본 풀에서 번호로 정해진 한 장, 번호가 숫자가 아니면(`../`·글자) 기본 풀로 (store.cover_for).
        `/folio/covers/file/<이름>`(내 이미지 mine/ · 번호 파일) · `/folio/covers/pool/<이름>`(내 기본 풀) ·
        `/folio/covers/bundled/<이름>`(앱 기본) — 커버 고르기 시트·「내 커버 관리」의 썸네일. 이름은 폴더에
        실제로 있는 파일과 똑같을 때만 (없으면 404). 경로는 폴더 목록에서만 고르므로 받은 글자가 경로가 되지 않는다.

        캐시: 주소에 `?v=`(그림의 모습)가 붙어 오면 하루 — 그림이 바뀌면 주소가 바뀐다 (store.CoverScan.url).
        `?v=` 없이 오면 **저장하지 않는다** (`no-store`). 예전의 `max-age=60` 은 「1분 안에 바뀐다」고 적었지만
        실제로는 연출 페이지(하루 종일 떠 있는 웜 스타트)가 같은 주소를 다시 받지 않아 **끝까지 안 바뀌었다**."""
        kind, _, name = rest.partition("/")
        gen = b""
        if kind.lower() == "gen" and name:     # 생성 커버 그대로 (시트의 「번호대로」 칸·관리 화면) — 번호가 아니면 404
            gen = store.cover_generated(name.split("?", 1)[0], self.COVERS_BUNDLED)
            if not gen:
                return _json(self, {"ok": False, "error": "not_found"}, 404)
            path, ctype = None, "image/svg+xml"
        elif kind.lower() in ("file", "pool", "bundled") and name:
            hit = store.cover_file(kind.lower(), name, self.COVERS_BUNDLED)
            if not hit:
                return _json(self, {"ok": False, "error": "not_found"}, 404)
            path, ctype = hit
        else:
            sid = kind.split("?", 1)[0]
            path, ctype, where = store.cover_for(sid, self.COVERS_BUNDLED)
            if where == "gen":                 # 번호대로의 마지막 차례 — 곡 제목·아티스트로 그린 커버 (store.cover_generate)
                gen = store.cover_generated(sid, self.COVERS_BUNDLED)
        body = gen or store.FALLBACK_COVER
        if path:
            try:
                with open(path, "rb") as f:
                    body = f.read(store.MAX_COVER_BYTES + 1)
            except OSError:
                body, ctype = store.FALLBACK_COVER, "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 그림을 탭에서 바로 열어도(특히 SVG) 스크립트가 우리 출처에서 돌지 못하게 — 배경 그림으로 쓸 때는 영향이 없다
        # 생성 커버는 글꼴 조각을 SVG 안에 data: 로 싣는다 — font-src data: 가 없으면 탭에서 바로 열 때 시스템 글꼴로 나온다
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox")
        _cors_headers(self)            # 폰 사이트가 쪽지 머리로 받아 blob 으로 그린다 (ui/folio/net.js `hydrate`) — 적어 둔 사이트만
        versioned = "v=" in (urlparse(self.path).query or "")
        self._cache_ctl = "private, max-age=86400" if versioned else "no-store"
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self, rel: str = "index.html", prefs: bool = True):
        """화면에 실행 토큰을 심고, 인라인 스크립트 해시로 CSP 를 건다 (외부 스크립트·인라인 핸들러 전부 차단).

        `rel` 은 `ui/` 아래 상대 경로다. **정적 서빙으로 내면 안 된다** — 그 길에는 CSP 도
        토큰 치환도 없다(이 저장소의 실제 사고). 화면은 오직 여기로만 나간다.
        폴리오에서 가져온 화면 셋(`folio/…`)도 같은 길을 지난다."""
        try:
            with open(os.path.join(UI_DIR, rel.replace("/", os.sep)), "rb") as f:
                html = f.read()
        except OSError:
            return _json(self, {"ok": False, "error": "ui_missing"}, 500)
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        pre = b""
        theme = q.get("theme") if q.get("theme") in ("light", "dark") else None   # 검토·스크린샷용 고정이 먼저
        if prefs:   # 화면 편의 값 되살리기 — 레일이 있는 네 화면만 (연출 창 같은 조각은 아니다)
            # 테마도 이 화면들만 — 게임 위 화면(OVERLAY_PAGES, prefs=False)은 늘 다크다
            ui_theme = None
            try:
                ui_theme = store.get_settings().get("ui_theme")
            except Exception as e:
                _say(f"[ui] 테마 설정을 못 읽었습니다: {type(e).__name__}: {e}")
            if theme is None and ui_theme in ("light", "dark"):
                theme = ui_theme                     # 설정 `ui_theme` 을 박아 낸다 — 번쩍임 없음 (auto 는 CSS 가 정한다)
            try:
                pre = ui_prelude(store.get_ui().get("prefs"), ui_theme)
            except Exception as e:
                _say(f"[ui] 화면 편의 값을 못 읽었습니다: {type(e).__name__}: {e}")
        html, csp = render_index(html, TOKEN, theme, pre)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(html)

    # 폴리오에서 가져온 화면. **여기 적힌 것만** 나간다 — 목록에 없는 파일은 정적 서빙으로
    # 가는데 그 길엔 자물쇠가 없다. 값은 `ui/` 아래 상대 경로.
    FOLIO_PAGES = {
        "/folio/": "folio/index.html",
        "/folio/index.html": "folio/index.html",
        "/folio/settings.html": "folio/settings.html",
        "/folio/greet.html": "folio/greet.html",
        "/folio/covers.html": "folio/covers.html",     # 내 커버 관리
    }
    # 따로 띄우는 창 — 레일(shell.js)이 없는 폴리오 화면. 테마는 따른다 (`prefs=True`). 모바일 리모컨에서도 같은 화면이다.
    WINDOW_PAGES = {
        "/folio/broadcast.html": "folio/broadcast.html",   # 방송 창 (820×560 · `_bc_open_window`)
    }
    # 게임 위에 얹는 화면 — 레일(shell.js)이 없는 **창 속 내용**이라 FOLIO_PAGES 와 따로 둔다.
    # 같은 `_serve_index` 를 지나므로 CSP·토큰은 똑같이 걸린다 (WebView2 연출이 읽는다: folio/opening_wv.py).
    OVERLAY_PAGES = {
        "/folio/opening2.html": "folio/opening2.html",
    }
    # 자리표시자가 든 조각. html 이 아니라 CSP 는 안 걸지만 **치환은 해야** 한다 —
    # 폴리오는 토큰·모드를 `net.js` 안에 둔다 (우리 index.html 은 인라인 스크립트에 둔다).
    FOLIO_ASSETS = {
        "/folio/net.js": ("folio/net.js", "text/javascript; charset=utf-8"),
        "/folio/qr.js": ("folio/qr.js", "text/javascript; charset=utf-8"),
    }

    def _serve_folio_asset(self, rel: str, ctype: str):
        try:
            with open(os.path.join(UI_DIR, rel.replace("/", os.sep)), "rb") as f:
                body = f.read()
        except OSError:
            return _json(self, {"ok": False, "error": "ui_missing"}, 500)
        body = body.replace(b"\r\n", b"\n").replace(TOKEN_MARK, TOKEN.encode("ascii")).replace(MODE_MARK, b"local")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_remote(self) -> bool:
        """이 PC 밖에서 온 요청인가 (터널을 지나온 것).

        **Host 헤더로 가른다.** 검사에서 밖을 흉내 낼 때도 Host 만 바꾸면 된다 —
        진짜 터널이 필요 없다.
        """
        return (self.headers.get("Host") or "").lower() not in _ok_hosts

    def _guard(self) -> bool:
        """CSRF 방지: 브라우저의 다른 사이트가 보낸 폼/스크립트 요청을 거른다.
        (1) Host 가 우리 주소, (2) Origin 이 있으면 우리 출처, (3) UI 가 붙이는 X-Requested-With 헤더.

        **밖에서 온 요청**은 (1) 사용자가 적어 둔 주소이고 (2) 인증키로 받은 쪽지를 들고 있을 때만
        받는다. 실행 토큰은 HTML 에 박혀 나가므로 밖에서는 열쇠 구실을 못 한다 — 쪽지가 그 자리다.
        """
        host = (self.headers.get("Host") or "").lower()
        origin = (self.headers.get("Origin") or "").lower()
        if self.headers.get("X-Requested-With") != APP:
            return False
        if host in _ok_hosts:
            if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
                return False
            if not _token_ok(self.headers.get(TOKEN_HEADER)):   # 배포판: 실행마다 다른 토큰 — 페이지(index.html)만 안다
                return False
        else:
            if not _host_ok_remote(host):
                return False
            if origin:                        # 제 페이지(터널 주소) 또는 적어 둔 사이트만
                oh = origin.split("://", 1)[-1].lower()
                if not _host_ok_remote(oh) and not _cors_origin(self):
                    return False
            if not _remote_valid(_remote_tok(self)):
                return False
            global _last_remote
            _last_remote = time.time()        # 유휴 자동 닫기가 보는 값
        global _last_ui
        _last_ui = time.time()   # 살아 있는 UI 의 심장박동
        return True

    def _remote_login(self):
        cfg = _remote_cfg()
        host = self.headers.get("Host") or ""
        if not _host_ok_remote(host):
            return _json(self, {"ok": False, "error": "remote_off",
                                "message": "밖에서 접속이 꺼져 있거나 주소가 다릅니다."}, 403)
        if not store.has_remote_key():
            return _json(self, {"ok": False, "error": "no_key",
                                "message": "이 PC 에서 인증키를 먼저 발행하세요."}, 403)
        # 브라우저에서 온 것이면 출처를 본다. 로그인은 `_guard` 를 안 타므로 이것이 없으면 **아무 사이트의
        # 페이지**가 폰 브라우저에서 이 길로 틀린 키를 다섯 번 던져 사용자의 폰을 잠글 수 있다.
        # 제 페이지(터널 주소)와 적어 둔 사이트만 — `_guard` 와 같은 규칙.
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin:
            oh = origin.split("://", 1)[-1]
            if not _host_ok_remote(oh) and not _cors_origin(self):
                self._drain()
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
        who = _remote_try_key(self)
        tries, until = _REMOTE_TRY.get(who, (0, 0.0))
        left = int(until - time.time())
        a_tries, a_until = _REMOTE_TRY.get(_TRY_ALL, (0, 0.0))
        left = max(left, int(a_until - time.time()))
        p = _read_json(self)
        if "_error" in p:   # 본문이 안 왔거나 JSON 이 아니다 — 열쇠를 재기 전에 끝낸다 (틀린 횟수로도 안 센다)
            return _json(self, {"ok": False, "error": "bad_request", "message": p["_error"]}, 400)
        # **짝지어 둔 기기의 토큰은 잠금과 무관하게 통한다**. 터널 뒤에서는 모든 폰이 127.0.0.1 이라
        # 밖의 누군가가 다섯 번 틀리면 **사용자의 폰까지 10분 잠긴다**. 기기 토큰은 32바이트 난수라
        # 추측으로는 못 맞히고, 맞는 토큰을 든 것은 이미 짝지은 그 기기다 — 잠금이 막을 대상이 아니다.
        dev = store.touch_device(_s(p.get("device")))
        if dev is None and not p.get("key") and store.device_token_expired(_s(p.get("device"))):   # 만료된 기기 토큰은 키 없이 통하지 않는다
            return _json(self, {"ok": False, "error": "device_expired", "message": f"이 기기는 {store.REMOTE_DEVICE_TTL_DAYS}일 동안 쓰지 않아 만료됐습니다. 인증키로 다시 짝지어 주세요."}, 401)
        if dev is None and left > 0:
            return _json(self, {"ok": False, "error": "locked", "retryAfterSeconds": left,
                                "message": f"{left}초 뒤에 다시 해 보세요."}, 429)
        if dev is None and not store.check_remote_key(p.get("key")):
            tries += 1
            a_tries += 1
            # 전체 상한 — 보낸 곳을 갈라 세더라도(Cf-Connecting-Ip) 주소를 바꿔 가며 두드리면 그 수는 안 줄어든다.
            # 그래서 **어디서 왔든 합쳐서** 이만큼이면 잠근다. 32^8 의 열쇠에 10분 20번은 아무것도 아니다.
            if a_tries >= REMOTE_MAX_TRY_ALL:
                _REMOTE_TRY[_TRY_ALL] = (0, time.time() + REMOTE_LOCK)
            else:
                _REMOTE_TRY[_TRY_ALL] = (a_tries, 0.0)
            if tries >= REMOTE_MAX_TRY or a_tries >= REMOTE_MAX_TRY_ALL:
                _REMOTE_TRY[who] = (0, time.time() + REMOTE_LOCK)
                return _json(self, {"ok": False, "error": "locked",
                                    "retryAfterSeconds": int(REMOTE_LOCK),
                                    "message": f"{REMOTE_MAX_TRY}번 틀려 {int(REMOTE_LOCK // 60)}분 잠급니다."}, 429)
            _REMOTE_TRY[who] = (tries, 0.0)
            return _json(self, {"ok": False, "error": "bad_key",
                                "left": REMOTE_MAX_TRY - tries,
                                "message": "인증키가 맞지 않습니다."}, 403)
        _REMOTE_TRY.pop(who, None)
        if dev is None:                      # 키로 처음 들어왔다 — 이 기기에 긴 토큰을 준다
            dev = store.add_device(_s(p.get("name")))
        tok = _remote_new(dev["id"])
        _say(f"remote: login ok ({dev['name']})")
        # `device` 는 **처음 한 번만** 의미가 있지만, 매번 돌려줘도 같은 값이라 폰이 잃어버리지 않는다
        return _json(self, {"ok": True, "token": tok, "ttl": REMOTE_TTL,
                            "device": dev["token"], "deviceId": dev["id"], "name": dev["name"],
                            "scope": cfg["scope"],
                            # 폰 사이트(link.mobimml.com)는 모비폴리오와 **같이 쓴다.** 어느 앱에 붙었는지
                            # 알아야 맞는 화면으로 간다 — 모르면 폴리오 화면이 우리 길을 두드린다.
                            "app": APP})

    def _remote_who(self) -> str:
        """이 요청을 보낸 기기 이름 (장부에 적을 값). 못 찾으면 「밖」."""
        v = _REMOTE.get(_remote_tok(self)) or {}
        dev = next((x for x in store.get_devices() if x["id"] == v.get("dev")), None)
        return f"밖: {dev['name']}" if dev else "밖"

    def _remote_blocked(self, path: str, post: bool = True) -> bool:
        """밖에서 온 요청을 막아야 하는가. **거절이 기본이다.**

        두 겹이다.
          (1) 범위와 무관하게 이 PC 에서만 되는 것은 언제나 막는다 (REMOTE_NEVER)
          (2) 바꾸는 것(POST)은 **범위별 허용 목록에 있을 때만** 지나간다
        읽기(GET)는 범위가 read 여도 열려 있다 — 「보기만」이 read 의 뜻이다.

        `post` 의 기본값이 True 인 이유: 폴리오 핸들러가 `self._remote_blocked(u.path)` 로
        (한 개 인자로) 부른다. 기본값이 없으면 그 자리에서 TypeError → 500 이다.
        """
        if not self._is_remote():
            return False
        if getattr(self, "_folio_pass", False):   # 폴리오 길 이름으로 이미 판정했다 (`_folio`)
            return False
        # **폴리오 길은 여기서 끝낸다.** 앞자리는 통째로 NEVER 이고, 그 안에서 **적어 둔
        # 목록만** 뺀다 — 보기는 `FOLIO_READ_OK`, 연주 조작은 `FOLIO_PLAY_OK`(범위 edit 부터).
        # 아래 NEVER 줄보다 **먼저** 와야 한다. 뒤에 두면 앞자리에 먼저 걸려 목록이 죽는다.
        if path.startswith(FOLIO_PREFIX):
            rest = path[len(FOLIO_PREFIX):]
            if not post:
                return rest not in FOLIO_READ_OK
            return (rest not in FOLIO_PLAY_OK and rest not in FOLIO_EDIT_OK) or _remote_cfg()["scope"] == "read"
        if path in REMOTE_NEVER or path.startswith(REMOTE_NEVER_PREFIX):
            return True
        if not post:
            return False
        return path not in REMOTE_POST_OK.get(_remote_cfg()["scope"], set())

    def do_OPTIONS(self):
        """브라우저가 먼저 물어보는 예비요청. **허락한 사이트에만** 답한다.
        폰 화면은 Content-Type·X-Requested-With·X-MobiWorks-Remote 를 붙이므로 반드시 이 단계를
        거친다 — 여기서 답이 없으면 본 요청은 보내지지도 않는다."""
        if urlparse(self.path).path in self.BC_GUEST:
            # 방송 손님 길 — 손님 페이지(우편함 사이트)에만. 손님 토큰 머리를 허락한다 (기기 쪽지 머리는 아니다)
            org = _bc_cors_origin(self)
            if not org:
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", org)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With, " + self.BC_GUEST_HEADER)
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        org = _cors_origin(self)
        if not org:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", org)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Requested-With, " + TOKEN_HEADER + ", X-MobiWorks-Remote")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _drain(self) -> None:
        """거절하기 전에 **본문을 비운다.** 안 읽은 본문을 남기고 소켓을 닫으면 Windows 가 RST 를
        보내고, 상대는 403 대신 「연결이 끊겼다」를 본다 (전수 검사에서 실제로 났다 —
        WinError 10053). 상한(MAX_BODY)까지만 비운다 — 그 이상은 어차피 안 받는다."""
        try:
            n = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
        except ValueError:
            return
        # **안 오는 본문은 기다리지 않는다.** `Content-Length` 만 적고 본문을 안 보내면 여기서 BODY_TIMEOUT(30초)
        # 동안 슬롯을 쥔다 — 문 앞(인증 전)에서다. 짧게 기다리고 그냥 닫는다.
        try:
            self.connection.settimeout(DRAIN_TIMEOUT)
        except OSError:
            pass
        try:
            while n > 0:
                chunk = self.rfile.read(min(65536, n))
                if not chunk:
                    break
                n -= len(chunk)
        except OSError:            # 시간 초과 — 남은 본문은 포기하고 답만 보낸 뒤 닫는다
            self.close_connection = True

    def do_POST(self):
        try:
            self._post()
        except Exception as e:   # 어떤 입력에도 연결을 끊지 않고 JSON 으로 답한다
            import traceback; traceback.print_exc()
            try:
                _json(self, {"ok": False, "error": "internal", "message": type(e).__name__}, 500)
            except Exception:
                pass

    def _post(self):
        u = urlparse(self.path)
        if u.path == "/api/bc/join" or u.path == "/api/bc/req" or u.path == "/api/bc/leave":   # 방송 손님 길 (`_bc_guest`)
            return self._bc_guest(u.path)
        if u.path == "/api/remote/login":
            # ── 밖에서 들어오는 문 ──
            # 여기만 `_guard()` 를 안 탄다 (아직 쪽지가 없으니 당연하다). 대신 **세 겹**이다:
            #   1. 밖에서 접속이 켜져 있고 사용자가 적어 둔 주소로 들어왔는가
            #   2. 이 IP 가 잠겨 있지 않은가 (틀린 횟수)
            #   3. 인증키가 맞는가 — 또는 이미 짝지은 **기기 토큰**을 들고 왔는가
            # 기기 토큰이 있는 이유: 쪽지는 12시간이라, 없으면 하루 두 번 키를 다시 쳐야 한다.
            return self._remote_login()
        if u.path == "/api/remote/logout":
            # ── 밖에서 스스로 나가기 ──
            # **`/api/remote/` 앞자리 규칙보다 앞에 둔다.** 그 규칙은 문 자체를 여닫는 길을
            # 밖에서 못 건드리게 막는 것인데, **나가기는 밖에서 돼야** 한다. 로그인과 같은 자리다.
            #
            # 왜 필요한가: 기기를 끊는 길은 지금까지 **이 PC 에서 지우는 것**뿐이었다.
            # 폰을 잃어버렸을 때는 그게 맞지만, **남의 폰이나 공용 기기에서 잠깐 봤을 때**는
            # 그 자리에서 끊을 수 있어야 한다. (폴리오 화면이 이 길을 이미 부르고 있었다 —
            # 통합하면서 404 가 될 뻔했다.)
            #
            # 열쇠는 **들고 온 쪽지 자체**다. 없으면 끊을 것도 없다.
            # **쪽지가 없으면 403.** 「끊을 게 없으니 200」으로 두면 이 길이 **쪽지 없이
            # 지나는 길**이 되고, 전수 행렬이 바로 그것을 잡는다 (실제로 잡혔다).
            tok = _remote_tok(self)
            if not tok or not _REMOTE.pop(tok, None):
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"ok": True, "gone": True})
        # 밖에서 온 것은 **허용 목록에 있을 때만** 지나간다 (범위별). 아래의 개별 판정보다 먼저다 —
        # `/api/bye` 처럼 `_guard()` 를 안 타는 길이 있어서, 문을 뒤에 두면 그리로 샌다.
        if self._is_remote() and self._remote_blocked(u.path, post=True):
            self._drain()
            # **왜 막혔는지 말한다** — 「forbidden 시작하지 못했습니다」만으로는
            # 범위가 모자란 것인지 고장인지 알 수 없다. `error` 는 그대로 둔다 (화면·검사가 본다).
            # **살아 있는 쪽지를 든 폰에게만** 말한다. 이 자리는 `_guard` 앞이라 주소만 아는 남도 온다 —
            # 그에게 이 PC 의 범위 설정(read/edit/run)과 「그 길이 있다·없다」를 알려 줄 이유가 없다.
            if not _remote_valid(_remote_tok(self)):
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            scope = _remote_cfg()["scope"]
            pc_only = (u.path in REMOTE_NEVER or u.path.startswith(REMOTE_NEVER_PREFIX)) and not u.path.startswith(FOLIO_PREFIX)
            if not pc_only and u.path.startswith(FOLIO_PREFIX):
                rest = u.path[len(FOLIO_PREFIX):]
                pc_only = rest not in FOLIO_PLAY_OK and rest not in FOLIO_EDIT_OK
            if not pc_only and not u.path.startswith(FOLIO_PREFIX):
                pc_only = not any(u.path in v for v in REMOTE_POST_OK.values())
            label = {"read": "보기만", "edit": "바꾸기", "run": "실행"}.get(scope, scope)
            msg = ("이 일은 PC 에서만 할 수 있습니다." if pc_only else
                   f"이 기기의 범위({label})로는 할 수 없습니다 — PC 의 설정 → 밖에서 접속에서 범위를 올려 주세요.")
            return _json(self, {"ok": False, "error": "forbidden", "scope": scope, "message": msg}, 403)
        if u.path == "/api/bye":   # 페이지 닫힘 신호 (sendBeacon 은 헤더를 못 붙이므로 토큰은 본문으로)
            # 토큰이 없는 개발 서버에서는 아무 사이트나 이 신호를 보내 경량판 종료 판정을 건드릴 수 있어
            # Host 만이라도 확인한다 (다른 사이트는 Host 를 우리 주소로 못 바꾼다)
            if (self.headers.get("Host") or "").lower() not in _ok_hosts:
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            p = _read_json(self)
            if _token_ok(p.get("token")):
                global _bye_at
                _bye_at = time.time()
            return _json(self, {"ok": True})
        if not self._guard():
            self._drain()
            return _json(self, {"ok": False, "error": "forbidden", "message": "허용되지 않은 출처의 요청입니다."}, 403)
        # **본문을 읽기 전에** 넘긴다 — `_read_json` 이 `rfile` 을 비우고 나면 그쪽이 읽을 게 없다.
        # **글자로 적는다.** 변수로 쓰면 길 뽑는 눈(tests/test_remote.py)이 못 본다 —
        # 그러면 이 길이 원격 전수 행렬에서 통째로 빠진다.
        if u.path.startswith("/api/folio/"):
            return self._folio(u.path)
        if u.path == "/api/restore":
            # ── 백업 zip 으로 복원. **본문이 JSON 이 아니라 zip 이다** — `_read_json` 앞에서 받는다.
            # 밖에서는 REMOTE_NEVER 라 여기 못 온다 (이 PC 의 파일을 통째로 바꾸는 길이다).
            # 몸통은 zip 그대로(application/zip)든 multipart 의 첫 파일 칸이든 받는다. 상한은 MAX_BODY.
            raw = _read_bytes(self)
            if isinstance(raw, str):
                return _json(self, {"ok": False, "error": "bad_request", "message": raw}, 400)
            ctype = self.headers.get("Content-Type") or ""
            if ctype.lower().startswith("multipart/form-data"):
                raw = _multipart_file(raw, ctype)
                if raw is None:
                    return _json(self, {"ok": False, "error": "bad_request", "message": "multipart 에 파일 칸이 없습니다."}, 400)
            r = _restore_with_queue(raw)
            if not r.get("ok"):
                return _json(self, r, 400)
            # ── 복원 뒤 다시 읽기 ──
            # 서버는 설정·재생목록·프리셋을 **매번 파일에서** 읽는다 (store.get_* 에 메모리 캐시가 없다).
            # 메모리에 남는 것은 둘뿐이다: ① 폴리오 대기열의 곡별 악기(`_Q["inst"]`, 재생목록에서 떠 온
            # 사본) → 엔진이 이미 떠 있으면 저장된 목록으로 다시 맞춘다. ② 오버레이 창(`overlay.OVERLAY`)의
            # 설정 사본 → `apply()` 는 창을 여는 부작용이 있어 **부르지 않는다** — 다음 설정 저장·재시작 때 반영.
            if _folio_engine is not None and "folio/playlists.json" in r.get("applied", ()):
                try:
                    r["queue_resync"] = _folio_engine.queue_resync_inst()
                except Exception as e:   # 되읽기 실패가 복원 성공을 실패로 만들지는 않는다
                    _say(f"[restore] 대기열 악기 되읽기 실패: {type(e).__name__}: {e}")
            return _json(self, r)
        p = _read_json(self)
        if "_error" in p:
            return _json(self, {"ok": False, "error": "bad_request", "message": p["_error"]}, 400)
        if u.path == "/api/ui/state":   # 화면이 알려 주는 「지금 보는 곳」·편의 값 → 다음 실행의 첫 화면
            page, prefs = p.get("page"), p.get("prefs")
            if page is not None and not store.page_known(page):
                return _json(self, {"ok": False, "error": "bad_page", "message": "우리 화면 주소가 아닙니다."}, 400)
            if prefs is not None and not isinstance(prefs, dict):
                return _json(self, {"ok": False, "error": "bad_request", "message": "prefs 는 객체여야 합니다."}, 400)
            return _json(self, {"ok": True, "ui": store.set_ui(page=page, prefs=prefs)})
        if u.path == "/api/quit":     # 창을 닫을 때 정상 종료 요청 (taskkill 대신 → PyInstaller 임시폴더 정리됨)
            _note_quit_page(p.get("page"), self.headers.get("Referer"))   # 끈 화면을 다음 첫 화면으로
            _json(self, {"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
            return
        if u.path == "/api/sync":   # CLI 를 실제로 부른다 (읽기 명령만)
            return _json(self, sync())
        if u.path == "/api/plan":   # "이걸 N개 만들려면 뭐가 얼마나 부족한가" — CLI 를 부르지 않는다
            # id("craft:이름#n") 가 있으면 그 경로를, 없으면 이름+종류로 첫 경로를 (동명 레시피는 경로마다 재료가 다르다)
            rid, name, kind = _s(p.get("id")), _s(p.get("name")), _s(p.get("kind"))
            if rid and not kind:
                kind = rid.split(":", 1)[0]
            snap = snapshot()
            pool = snap["craft"] if kind != "alter" else snap["alter"]
            r = (next((x for x in pool if x["id"] == rid), None) if rid
                 else next((x for x in pool if x["name"] == name), None))
            if not r:
                return _json(self, {"ok": False, "error": "not_found", "message": "그 레시피가 캐시에 없습니다. 갱신해 보세요."}, 404)
            plan = work.plan(r, p.get("count"), snap["stock"], snap["storage"])
            # 재료마다 구하는 길(채집 / 다른 레시피) — 화면이 「큐에 담기」 버튼 종류를 정한다
            try:
                db = recipedb.load()
            except Exception:
                db = recipedb.empty()
            for row in plan["need"]:
                row["source"], row["sourceId"] = recipedb.ingredient_source(db, row["name"])
            plan.update(workqueue.max_suggested(r, snap))   # maxByStock / maxByStockPartial / maxCount(학습) / maxSuggested
            return _json(self, {"ok": True, "plan": plan})
        if u.path == "/api/queue":   # 큐 편집 (실행은 /api/queue/start)
            op = _s(p.get("op"))

            def done(r):
                """상태를 바꾼 응답에는 GET /api/queue 와 같은 모양의 state 를 실어 준다.
                화면이 부분 갱신 없이 통째로 다시 그린다."""
                if r.get("ok") and "state" not in r:
                    r["state"] = QUEUE.state()
                return _json(self, r, 200 if r.get("ok") else 400)

            if op == "add":
                item = p.get("item")
                if not isinstance(item, dict):
                    return _json(self, {"ok": False, "error": "bad_request", "message": "item 은 객체여야 합니다."}, 400)
                return done(QUEUE.add(item, snapshot(), group=_s(p.get("group"))))
            if op == "remove":
                return done(QUEUE.remove(_s(p.get("id"))))
            if op == "clear":
                return done(QUEUE.clear())
            if op == "reset":
                return done(QUEUE.reset(_s(p.get("id"))))
            if op == "config":
                return done(QUEUE.configure({k: p.get(k) for k in ("onError", "repeat") if k in p}))
            if op == "update":   # 큐 안에서 횟수·목표·수령 방식 수정 (pending·stopped·error 만). count:"max" 는 재고·상한으로 치환
                patch = {k: p.get(k) for k in ("count", "target", "collect") if k in p}
                return done(QUEUE.update(_s(p.get("id")), patch, store.get_settings(),
                                         snapshot() if patch.get("count") == "max" else None))
            if op in ("chain", "add_chain"):   # 선행 제작까지 담기 — 부족 재료를 재귀적으로 해결하는 항목들을 선행 → 목표 순서로
                snap = snapshot()
                try:
                    db = recipedb.load()
                except Exception:
                    db = recipedb.empty()
                return done(QUEUE.add_chain(_s(p.get("id")), p.get("count", 1), _s(p.get("mode")) or "need", snap,
                                            lambda n: recipedb.ingredient_source(db, n),
                                            dry_run=p.get("dryRun") is True, group=_s(p.get("group")),
                                            collect=_s(p.get("collect")) or None))   # 가공 수령 모드 — 목표 항목에만 실린다
            if op == "group_create":
                ids = p.get("ids") if isinstance(p.get("ids"), list) else []
                return done(QUEUE.group_create(_s(p.get("name")), [_s(x) for x in ids]))
            if op == "group_update":
                patch = p.get("patch") if isinstance(p.get("patch"), dict) else {}
                return done(QUEUE.group_update(_s(p.get("id")), patch))
            if op == "group_dissolve":
                return done(QUEUE.group_dissolve(_s(p.get("id"))))
            if op == "group_duplicate":
                return done(QUEUE.group_duplicate(_s(p.get("id"))))
            if op == "card_duplicate":   # 낱장 「다시 담기」 — 같은 설정으로 add 를 다시 부른다 (캐시만 읽는다 · CLI 없음)
                return done(QUEUE.card_duplicate(_s(p.get("id")), snapshot()))
            if op == "move_item":
                return done(QUEUE.move_item(_s(p.get("id")), p.get("to"), p.get("index")))
            if op in ("preset_save", "preset_delete", "preset_load", "preset_code", "preset_import"):
                return _json(self, *_preset_op(op, p))
            return _json(self, {"ok": False, "error": "bad_request",
                                "message": "op 는 add/remove/clear/reset/config/update/chain/"
                                           "group_create/group_update/group_dissolve/group_duplicate/card_duplicate/move_item/"
                                           "preset_save/preset_load/preset_delete/preset_code/preset_import"}, 400)
        if u.path == "/api/queue/start":   # 실행 명령을 부른다 — 명시적 confirm 없이는 절대 돌지 않는다
            if p.get("confirm") is not True:
                return _json(self, {"ok": False, "error": "confirm_required", "message": "confirm:true 가 필요합니다."}, 400)
            # **누가 시켰는지 장부에 남긴다.** 밖에서 시킨 실행이면 그 기기 이름을 같이 적는다 —
            # 날개가 줄었는데 왜 줄었는지 못 밝히면 곤란하다.
            ledger.set_actor(self._remote_who() if self._is_remote() else "")
            # 날개 차단기에 막혔을 때 사람이 「그래도 시작」을 눌렀으면 넘긴다 — 폰도 run 범위면 같다
            r = QUEUE.start(store.get_settings(), override_breaker=p.get("override_breaker") is True)
            if r.get("ok"):
                _say("queue: started by " + (ledger.actor() or "user"))
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/queue/stop":
            r = QUEUE.stop()
            _say(f"queue: stop requested (stop_action sent={r.get('stopActionSent')})")
            return _json(self, r)
        if u.path == "/api/cli_test":   # 설정 창 「연결 확인」: 저장하지 않고 그 경로로 status 만 호출
            exe = _s(p.get("cli_exe"))
            if exe and not cli.valid_cli_path(exe):
                return _json(self, {"ok": False, "error": "bad_cli_exe",
                                    "message": "로컬 드라이브의 MabinogiMobile_CLI.exe 절대 경로가 아닙니다."})
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
        if u.path == "/api/settings":
            patch = p.get("settings")
            if not isinstance(patch, dict):
                return _json(self, {"ok": False, "error": "bad_request", "message": "settings 는 객체여야 합니다."}, 400)
            # 인증키는 **`/api/remote/key` 로만** 정한다 — 거기서 길이·글자를 재고 기기를 전부
            # 끊는다. 이 길로 들어오면 그 검사를 다 건너뛴다 (1자 키도 저장된다).
            patch.pop("remote_key", None)
            # **밖에서는 문·파일·갱신 주소도 못 바꾼다** (REMOTE_SETTINGS_NEVER). 처음엔 오버레이만
            # 걸렀고, 그래서 edit 쪽지가 `remote_scope` 를 `run` 으로 올릴 수 있었다.
            # **검사(경로가 실제 파일인가)보다 먼저 버린다** — 뒤에 두면 400/200 의 차이로
            # 밖에서 이 PC 의 파일이 있는지 없는지를 알아낼 수 있다.
            dropped = []
            drop_msg = ""
            if self._is_remote():
                # 날개가 드는 큐의 안전장치(`REMOTE_SETTINGS_RUN_ONLY`)는 **run 에서만** 받는다 — NEVER 와 같은 자리에서,
                # 같은 모양(`ignored`)으로 버린다. 남는 키가 없으면 조용히 200 을 주지 않는다 (폴리오 `/api/settings` 와 같은 403).
                scope = _remote_cfg()["scope"]
                dropped = sorted(str(k) for k in patch if not _remote_setting_writable(k, scope))
                patch = {k: v for k, v in patch.items() if _remote_setting_writable(k, scope)}
                run_only = [k for k in dropped if k in REMOTE_SETTINGS_RUN_ONLY]
                drop_msg = ("실행 범위에서만 바꿀 수 있는 설정입니다 (날개가 드는 큐의 안전장치)." if len(run_only) == len(dropped) else
                            "밖에서는 바꿀 수 없는 설정입니다 (이 PC 의 창·파일·문 · 날개의 안전장치는 실행 범위에서만)." if run_only else
                            "밖에서는 바꿀 수 없는 설정입니다 (이 PC 의 창·파일·문).")
                if dropped and not patch:
                    return _json(self, {"ok": False, "error": "remote_setting", "ignored": dropped, "message": drop_msg}, 403)
            uu = patch.get("update_url")
            if isinstance(uu, str) and uu.strip() and not _safe_url(uu):
                return _json(self, {"ok": False, "error": "bad_update_url", "message": _update_url_rule()["message"]}, 400)
            exe = patch.get("cli_exe")
            if isinstance(exe, str) and exe.strip() and not cli.valid_cli_path(exe):
                return _json(self, {"ok": False, "error": "bad_cli_exe",
                                    "message": "CLI 경로는 로컬 드라이브의 MabinogiMobile_CLI.exe 절대 경로여야 합니다."}, 400)
            # **밖에서는 이 PC 의 창을 건드리는 설정을 못 바꾼다.** `overlay_enabled` 하나로
            # 폰에서 PC 화면 위에 창을 띄울 수 있으면 안 된다. 창을 여닫는 **동작**은 아예
            # 다른 길(`POST /api/overlay`)로 갈라 뒀고 그 길은 REMOTE_NEVER 다.
            # (한 엔드포인트가 둘을 겸하면 통째로 NEVER 로 가야 하고, 그러면 폰에서 설정을 못 바꾼다.)
            before = store.get_settings().get("cli_exe")
            s = store.set_settings(patch)
            cli.set_exe_override(s.get("cli_exe"))
            overlay.OVERLAY.apply(s)   # 밴드 켜짐/꺼짐·표시 항목·불투명도를 바로 반영한다
            # 경로가 바뀐 때만 다시 확인한다 (게임이 꺼져 있으면 probe 가 5초 — 저장이 느려지지 않게)
            pub = _public_settings(s)
            if self._is_remote():   # GET 과 **같은 목록**으로 감춘다 — POST 의 답도 `update_url`·`overlay*`·`remote_*` 를 싣지 않게
                pub = {k: v for k, v in pub.items() if _remote_setting_ok(k)}
            return _json(self, {"ok": True, "settings": pub,
                                "cli": _probe() if s.get("cli_exe") != before else None,
                                "overlay": None if self._is_remote() else overlay.OVERLAY.status(),
                                **({"ignored": dropped, "message": drop_msg} if dropped else {})})
        if u.path.startswith("/api/remote/"):
            # ── 문 자체를 여닫는 길 — **이 PC 에서만** (REMOTE_NEVER_PREFIX 가 밖을 막는다) ──
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            if u.path == "/api/remote/key":
                if p.get("clear") is True:
                    store.clear_remote_key()
                    _remote_drop_all()          # 키를 지우면 **떠 있던 쪽지도 전부** 무효다
                    store.drop_all_devices()
                    return _json(self, {"ok": True, "key": "", "hasKey": False})
                raw = p.get("key")
                if isinstance(raw, str) and raw.strip():
                    ok, why = store.set_remote_key(raw)
                    if not ok:
                        return _json(self, {"ok": False, "error": "bad_key", "message": why}, 400)
                    key = store.get_remote_key()
                else:
                    key = store.issue_remote_key()
                # 키를 바꾸면 **전 기기·전 쪽지가 무효**다 — 폰을 잃었을 때 확실히 끊는 길이다
                _remote_drop_all()
                store.drop_all_devices()
                # 이 응답이 키를 보여 주는 **유일한 자리**다 (그 뒤로는 「정해져 있음」만 알린다)
                return _json(self, {"ok": True, "key": key, "hasKey": True})
            if u.path == "/api/remote/devices":
                if p.get("dropAll") is True:
                    store.drop_all_devices(); _remote_drop_all()
                elif _s(p.get("drop")):
                    dev = _s(p.get("drop"))
                    store.drop_device(dev)
                    _remote_drop_dev(dev)       # 그 기기 쪽지만 — 다른 기기는 그대로 둔다
                return _json(self, {"ok": True, "devices": [
                    {k: v for k, v in x.items() if k != "token"} for x in store.get_devices()]})
            if u.path == "/api/remote/tunnel":
                t = _tunnel()
                if p.get("stop") is True:
                    t.stop()
                    return _json(self, {"ok": True, "tunnel": t.state()})
                if p.get("start") is True:
                    if not store.has_remote_key():
                        return _json(self, {"ok": False, "error": "no_key",
                                            "message": "인증키를 먼저 발행하세요 — 자물쇠 없이 문을 열 수 없습니다."}, 400)
                    # 유휴 시계를 되감는다. 지난번 마지막 원격 요청 시각이 남아 있으면 유휴 감시가
                    # 다시 연 터널을 **20초 안에** 「30분 동안 아무 요청이 없다」며 도로 닫는다.
                    global _last_remote
                    _last_remote = 0.0
                    t.start(PORT, on_url=_tunnel_got_url)
                return _json(self, {"ok": True, "tunnel": t.state()})
            if u.path == "/api/remote/link":
                # **밖에서 실제로 닿는 것을 확인한 뒤에야 코드를 준다.**
                # 갓 연 주소는 수십 초간 DNS 가 안 퍼져 브라우저가 못 찾는다. 그때 코드를
                # 건네면 폰이 헛걸음하고, 우편함 코드는 **한 번 읽히면 사라져** 코드만 탄다.
                t = _tunnel()
                st = t.state()
                if not st["running"] or not st["url"]:
                    return _json(self, {"ok": False, "error": "no_tunnel",
                                        "message": "먼저 터널을 켜세요."}, 400)
                if not st["ready"]:
                    return _json(self, {"ok": False, "error": "not_ready",
                                        "message": "주소가 아직 밖에서 안 닿습니다. 20~30초 뒤에 다시 눌러 주세요."}, 409)
                return _json(self, _mailbox_put(st["url"]))
            if u.path == "/api/remote/unlock":
                # 잠금 풀기는 **이 PC 에서만** — 밖에서 풀 수 있으면 잠근 의미가 없다
                _REMOTE_TRY.clear()
                return _json(self, {"ok": True})
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if u.path == "/api/shortcut/desktop":   # 임베디드 판 — 이 PC 의 바탕화면에 파일을 만든다 (REMOTE_NEVER)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            r = _shortcut_desktop()
            return _json(self, r, 200 if r["ok"] else 400)
        if u.path == "/api/overlay":
            # **이 PC 의 창을 직접 건드리는 동작만** 여기 있다 — 그래서 통째로 REMOTE_NEVER 다.
            # 저장하는 값이 아니라 한 번뿐인 동작이라 설정과 섞지 않는다.
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            patch = p.get("settings")
            if isinstance(patch, dict) and patch:
                # 밴드의 자리(overlay_x·y·off_x·off_y)·마지막 ▾ 상태는 밴드만 정한다 — 탭이 옛 값을 실어 보내도 받지 않는다
                store.set_settings({k: v for k, v in patch.items()
                                    if str(k).startswith("overlay") and k not in overlay.BAND_HELD_KEYS})
                overlay.OVERLAY.apply(store.get_settings())
                # 폴리오 밴드도 같은 길로 켠다 — `overlay_folio` 도 `overlay` 로 시작하므로
                # **밖에서는 못 켠다**는 규칙을 그대로 탄다 (이 길은 REMOTE_NEVER 다).
                folio_overlay_sync()
            if p.get("overlayTest") is True:
                overlay.OVERLAY.test_show()
            # 「위치 초기화」 — 단축키를 없앤 짝이다. 관통을 켠 채 밴드를 화면 밖으로 끌면 되돌릴 길이 없다
            if p.get("overlayReset") is True:
                overlay.OVERLAY.reset_pos()
            return _json(self, {"ok": True, "settings": _public_settings(store.get_settings()),
                                "overlay": overlay.OVERLAY.status()})
        return _json(self, {"ok": False, "error": "not_found"}, 404)


# ── 앱 창 (경량판) ──
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


# 끄는 기능 목록. 앞의 둘은 원래 쓰던 것이고, 뒤는 첫 실행 안내·로그인 유도·환영 페이지를 노린 것이다.
# **뒤쪽 이름 일부는 미확인이다** (출처가 추측이다).
# 크로미엄은 모르는 feature 이름을 그냥 무시하므로 해는 없지만, **이 목록을 「그 기능이 있다」는 근거로 삼지 마라.**
# 실제로 효과가 확인된 것은 「Preferences 손보기 + --hide-crash-restore-bubble」 쪽이다.
DISABLE_FEATURES = ("TranslateUI,msEdgeStartupBoost,"
                    "msImplicitSignin,ImplicitSignIn,EdgeFirstRunExperience,MicrosoftEdgeWelcomePage,"
                    "msEdgeWelcomePage,msUndersideButton,msEdgeShoppingAssistant")


# 앱 창이 뜨는 크기 — **폰 폭**이 기준이다. 단위는 **CSS px** 이다.
WINDOW_W, WINDOW_H = 375, 860

# ── 앱 창의 마지막 자리·크기 (1.0.7 제보: 켤 때마다 375×860 으로 돌아간다) ──
# 375×860 은 **처음 실행**의 크기다. 사용자가 창을 옮기거나 늘리면 그 값을 기억해 두고 다음 실행에 되살린다.
# 단위는 **물리 px, 바깥 테두리 포함**(GetWindowRect 그대로) — 되살릴 때 SetWindowPos 에 그대로 준다.
# 설정(`settings.json`)이 아니라 따로 둔다: 설정 화면이 고치는 값이 아니고, 백업으로 다른 PC 에 따라가면
# 그 PC 의 모니터 배치와 맞지 않는다.
WINDOW_FILE = "window.json"
WINDOW_MIN_W, WINDOW_MIN_H = 240, 240   # 이보다 작은 기록은 믿지 않는다 (최소화 중에 잰 값 등)
WINDOW_WATCH_SEC = 1.0                  # 창 자리를 이만큼마다 본다 — 두 번 연속 같으면(끌기가 끝났다) 저장


def window_rect_clean(r) -> tuple | None:
    """저장된 창 기록 → (x, y, w, h). 모양이 이상하면 None (처음 실행처럼 375×860)."""
    try:
        if isinstance(r, dict):
            x, y, w, h = (int(r[k]) for k in ("x", "y", "w", "h"))
        else:
            x, y, w, h = (int(v) for v in r)
    except (TypeError, ValueError, KeyError):
        return None
    if w < WINDOW_MIN_W or h < WINDOW_MIN_H or w > 32000 or h > 32000:
        return None
    if abs(x) >= 32000 or abs(y) >= 32000:   # 최소화된 창은 (-32000, -32000) 에 있다
        return None
    return x, y, w, h


def clamp_window_rect(rect, work) -> tuple:
    """창 (x, y, w, h) 를 작업 영역 (x, y, w, h) 안으로 — 모니터가 바뀌었거나 줄었을 때 창이 화면 밖으로 가지 않게.
    크기는 작업 영역을 넘지 않고, 자리는 창 전체가 들어오게 당긴다."""
    x, y, w, h = (int(v) for v in rect)
    wx, wy, ww, wh = (int(v) for v in work)
    w = max(min(w, ww), min(WINDOW_MIN_W, ww))
    h = max(min(h, wh), min(WINDOW_MIN_H, wh))
    x = min(max(x, wx), wx + ww - w)
    y = min(max(y, wy), wy + wh - h)
    return x, y, w, h


def window_watch_step(saved, prev, cur, iconic: bool = False, zoomed: bool = False) -> tuple:
    """창 지켜보기 한 번 → (저장할 사각형 | None, 다음 prev).

    최소화·최대화 중에는 기록하지 않는다 (최대화를 풀면 원래 크기로 돌아가야 한다).
    **두 번 연속 같은 값**일 때만 저장한다 — 끄는 도중의 값은 쓰지 않는다. 이미 저장한 값과 같으면 쓰지 않는다."""
    if iconic or zoomed:
        return None, None
    cur = window_rect_clean(cur)
    if cur is None:
        return None, None
    if cur == prev and cur != saved:
        return cur, cur
    return None, cur


def _saved_window_rect() -> tuple | None:
    return window_rect_clean(store.load(WINDOW_FILE, None))


def _save_window_rect(rect) -> None:
    x, y, w, h = rect
    try:
        store.save(WINDOW_FILE, {"x": x, "y": y, "w": w, "h": h})
    except Exception as e:
        _say(f"[window] 창 자리를 저장하지 못했습니다: {type(e).__name__}: {e}")


_watched_windows: set = set()    # 지켜보는 중인 앱 창 핸들 (같은 창을 두 번 지켜보지 않는다)


def fit_window_size(css_w: int, css_h: int, scale: float, frame_w: int, frame_h: int, work_h: int) -> tuple:
    """창 바깥 크기(물리 px)를 돌려준다 — 안쪽이 `css_w × css_h` CSS px 가 되게.

    **`--window-size=375` 는 물리 px 다.** 125% 배율이면 375 물리 창의 안쪽 360 이
    CSS 로는 288px 이라 화면 오른쪽이 통째로 잘린다. 검사로는 이걸 못 잡는다 —
    CDP 로 재면 배율이 1 로 고정되기 때문이다. 배율과 테두리를 **창이 뜬 뒤 실제로 재서** 맞춘다.
    높이는 작업 영역을 넘지 않는다(넘으면 창이 화면 밖으로 나간다)."""
    w = int(round(css_w * scale)) + max(0, frame_w)
    h = int(round(css_h * scale)) + max(0, frame_h)
    if work_h > 0:
        h = min(h, work_h)
    return w, h


_user32_mine = None


def _user32_own():
    """이 모듈만 쓰는 user32 사본. `ctypes.windll.user32` 는 프로세스 전체가 함께 쓰는 객체라
    오버레이(`overlay._U32`)가 `GetMonitorInfoW.argtypes` 를 제 구조체로 박아 두면, 다른 구조체를 넘기는
    호출은 TypeError 로 조용히 실패한다 (작업 영역 높이를 못 재 창이 화면 밑으로 나갔다)."""
    global _user32_mine
    if _user32_mine is None:
        import ctypes
        _user32_mine = ctypes.WinDLL("user32")
    return _user32_mine


def _work_area_for(wintypes, rect) -> tuple | None:
    """사각형에 가장 가까운 모니터의 작업 영역 (x, y, w, h). 못 읽으면 None."""
    import ctypes
    try:
        u = _user32_own()
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        x, y, w, h = rect
        r = wintypes.RECT(x, y, x + w, y + h)
        u.MonitorFromRect.restype = ctypes.c_void_p      # 핸들 — 64비트에서 잘리지 않게
        u.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
        mon = u.MonitorFromRect(ctypes.byref(r), 2)      # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not mon or not u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            return None
        wk = mi.rcWork
        return wk.left, wk.top, wk.right - wk.left, wk.bottom - wk.top
    except Exception:
        return None


def _watch_app_window(u, wintypes, hwnd) -> None:
    """앱 창의 자리·크기를 지켜보다가 바뀌면 저장한다 (`window_watch_step`). 창이 사라지면 끝난다.
    닫는 순간에는 창이 이미 없어 잴 수 없다 — 그래서 닫을 때가 아니라 **바뀌고 멈출 때** 저장한다."""
    import ctypes
    key = int(hwnd or 0)
    if not key or key in _watched_windows:
        return
    _watched_windows.add(key)

    def loop():
        try:
            try:
                u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # 이 스레드도 물리 px 로 본다
            except Exception:
                pass
            wr = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(wr))
            saved = window_rect_clean((wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top))
            prev = saved
            while u.IsWindow(hwnd):
                time.sleep(WINDOW_WATCH_SEC)
                if not u.IsWindow(hwnd) or not u.GetWindowRect(hwnd, ctypes.byref(wr)):
                    break
                cur = (wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top)
                rect, prev = window_watch_step(saved, prev, cur, bool(u.IsIconic(hwnd)), bool(u.IsZoomed(hwnd)))
                if rect:
                    saved = rect
                    _save_window_rect(rect)
        except Exception as e:
            _say(f"[window] 창 자리 지켜보기가 멈췄습니다: {type(e).__name__}: {e}")
        finally:
            _watched_windows.discard(key)
    threading.Thread(target=loop, daemon=True, name="window-watch").start()


def _fit_app_window(timeout: float = 20.0, title: str = "", css: tuple = (), remember: bool = True) -> bool:
    """앱 창이 뜨면 안쪽을 WINDOW_W×WINDOW_H CSS px 로 맞춘다. 창을 새로 띄우지 않는다.
    **기억해 둔 창 자리(`window.json`)가 있으면 그 자리·크기로** 되살린다 (지금 화면 안으로 당겨서) —
    375×860 은 처음 실행에만. 그 뒤로 창 자리를 지켜보며 바뀌면 저장한다 (`_watch_app_window`).
    `title`·`css` 를 주면 그 제목의 창을 그 크기로 (방송 창 — `_bc_open_window`, 기억하지 않는다)."""
    title = title or APP_TITLE
    css_w, css_h = css if len(css) == 2 else (WINDOW_W, WINDOW_H)
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        # 이 스레드만 물리 px 로 본다 — 프로세스 전체의 DPI 인식은 건드리지 않는다(Tk 오버레이가 제 몫을 정한다)
        try:
            u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
        except Exception:
            pass
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        me = os.getpid()
        deadline = time.time() + timeout
        hwnd = None
        while time.time() < deadline and not hwnd:
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def cb(h, _l):
                if not u.IsWindowVisible(h):
                    return True
                cls = ctypes.create_unicode_buffer(120); u.GetClassNameW(h, cls, 120)
                if not cls.value.startswith("Chrome_WidgetWin"):
                    return True
                t = ctypes.create_unicode_buffer(300); u.GetWindowTextW(h, t, 300)
                if title not in t.value:
                    return True
                pid = wintypes.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if int(pid.value) == me:
                    return True
                found.append(h)
                return False
            u.EnumWindows(cb, 0)
            hwnd = found[0] if found else None
            if not hwnd:
                time.sleep(0.25)
        if not hwnd:
            return False
        try:
            dpi = int(u.GetDpiForWindow(hwnd)) or 96
        except Exception:
            dpi = 96
        scale = dpi / 96.0
        wr, cr = wintypes.RECT(), wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(wr)); u.GetClientRect(hwnd, ctypes.byref(cr))
        frame_w = (wr.right - wr.left) - (cr.right - cr.left)
        frame_h = (wr.bottom - wr.top) - (cr.bottom - cr.top)
        work_h = 0
        try:
            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
            um = _user32_own()      # 공유 user32 에는 오버레이가 제 구조체로 argtypes 를 박아 둔다 (그대로 부르면 TypeError)
            um.MonitorFromWindow.restype = ctypes.c_void_p
            mon = um.MonitorFromWindow(hwnd, 2)
            if mon and um.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
                work_h = mi.rcWork.bottom - mi.rcWork.top
        except Exception:
            pass
        main = remember and title == APP_TITLE and len(css) != 2    # 방송 창은 기억하지 않는다
        saved = _saved_window_rect() if main else None
        work = _work_area_for(wintypes, saved) if saved else None
        if saved and work:
            x, y, w, h = clamp_window_rect(saved, work)
            u.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004 | 0x0010)             # NOZORDER | NOACTIVATE
            _say(f"[window] 지난번 창 자리로 — ({x},{y}) {w}×{h}")
        else:
            w, h = fit_window_size(css_w, css_h, scale, frame_w, frame_h, work_h)
            u.SetWindowPos(hwnd, 0, 0, 0, w, h, 0x0002 | 0x0004 | 0x0010)   # NOMOVE | NOZORDER | NOACTIVATE
            _say(f"[window] 배율 {int(scale * 100)}% · 테두리 {frame_w}×{frame_h} → 창 {w}×{h} (안쪽 {css_w}×{css_h} CSS px)")
        if main:
            _watch_app_window(u, wintypes, hwnd)
        return True
    except Exception as e:
        _say(f"[window] 크기를 못 맞췄습니다: {type(e).__name__}: {e}")
        return False


def _app_window_args(exe: str, url: str, profile: str, rect=None) -> list:
    """앱 창 명령줄 (순수 함수 — 테스트가 창을 띄우지 않고 플래그를 확인한다).
    `rect`(기억해 둔 창 자리)를 주면 그 크기·자리로 뜬다 — 뜬 뒤 `_fit_app_window` 가 지금 화면 안으로 다시 당긴다."""
    r = window_rect_clean(rect) if rect is not None else None
    size = f"--window-size={r[2]},{r[3]}" if r else f"--window-size={WINDOW_W},{WINDOW_H}"
    pos = [f"--window-position={r[0]},{r[1]}"] if r else []
    # 창은 **폰 폭(375px)으로 뜬다**.
    # 이 앱은 폰 화면이 기준이다 — 디자인도 그 폭으로 그렸다.
    # 1280 으로 뜨면 넓은 배치만 보게 되고, 좁은 배치는 창을 줄여 봐야 나온다.
    # 창 테두리가 몇 px 을 먹으므로 본문은 375 보다 조금 좁게 잡힌다 — 우리 규칙은
    # 380 아래를 다시 한 번 받쳐 주므로(mobile.css) 그 몇 px 에서 깨지지 않는다.
    return [exe, f"--app={url}", size, *pos, f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            f"--disable-features={DISABLE_FEATURES}",
            # 「복원하시겠습니까」 말풍선 — exit_type 이 Crashed 로 남을 때 뜬다
            "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
            # 앱 창 하나 띄우자고 배경에서 돌 필요가 없는 것들
            "--no-service-autorun", "--disable-sync", "--disable-component-update",
            "--disable-background-networking",
            # 최소화·가림 상태에서도 페이지 타이머가 늦춰지지 않게
            "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding"]


def _seed_profile(profile: str) -> bool:
    """앱 창을 띄우기 **직전에** 전용 프로필을 손본다.

    Edge 는 시작하자마자 `profile.exit_type` 을 `"Crashed"` 로 써 두고 **정상 종료 때만** `"Normal"` 로 되돌린다.
    앱 창 구조에서는 정상 종료가 거의 안 난다(창을 닫으면 서버가 먼저 끝나거나, 자동 업데이트로 프로세스가 갈아끼워진다)
    — 그래서 **매 실행마다** 복원 안내와 설정 유도가 뜬다. `--no-first-run`·`--no-default-browser-check` 로는
    안 막힌다: 그것은 첫 실행 마법사·기본 브라우저 묻기 전용이다.

    규칙:
    - 남의 키를 지우지 않는다 — 읽어서 해당 키만 바꾼다. JSON 이 깨져 있으면 그 키만 있는 새 파일로 대신한다.
    - 원자적 교체(tmp → os.replace). store.save 와 같은 규칙이다.
    - **실패해도 조용히 넘어간다.** 이것 때문에 앱 창이 안 뜨면 안 된다 — 로그 한 줄만 남기고 False 를 돌려준다.
    """
    ok = True
    p = os.path.join(profile, "Default", "Preferences")
    tmp = f"{p}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            with open(p, encoding="utf-8-sig") as f:   # 메모장이 BOM 을 붙여도 읽힌다
                prefs = json.load(f)
            if not isinstance(prefs, dict):
                prefs = {}
        except (OSError, ValueError):   # 없거나 깨짐 → 그 키만 있는 새 파일
            prefs = {}
        for key, patch in (("profile", {"exit_type": "Normal", "exited_cleanly": True}),
                           ("browser", {"has_seen_welcome_page": True})):
            sub = prefs.get(key)
            prefs[key] = {**(sub if isinstance(sub, dict) else {}), **patch}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        ok = False
        _say(f"[edge] 프로필 Preferences 를 손보지 못했습니다 ({type(e).__name__}: {e}) — 창은 그대로 띄웁니다")
        try:
            os.remove(tmp)
        except Exception:   # 경로 자체가 이상하면 remove 도 던진다 (널바이트 → ValueError)
            pass
    try:   # 크로미엄은 이 파일이 있으면 첫 실행을 마친 것으로 본다. 있으면 그대로 둔다(내용은 상관없다)
        fr = os.path.join(profile, "First Run")
        os.makedirs(profile, exist_ok=True)
        if not os.path.exists(fr):
            with open(fr, "w", encoding="utf-8"):
                pass
    except Exception as e:
        ok = False
        _say(f"[edge] First Run 파일을 만들지 못했습니다 ({type(e).__name__}: {e}) — 창은 그대로 띄웁니다")
    return ok


def _launch_app_window(url: str):
    """Edge/Chrome 앱 창(주소창 없음, 전용 프로필)을 띄우고 프로세스 핸들을 돌려준다. 없으면 기본 브라우저 탭(None)."""
    global _app_profile
    exe = _find_app_browser()
    if exe:
        profile = APP_PROFILE
        _app_profile = profile
        _seed_profile(profile)   # 반드시 띄우기 전에
        try:
            import subprocess
            global _app_win
            _app_win = subprocess.Popen(_app_window_args(exe, url, profile, _saved_window_rect()),
                                        creationflags=0x00000008)   # DETACHED_PROCESS
            # 창이 뜨면 안쪽을 CSS px 기준으로 다시 맞춘다 — `--window-size` 는 배율을 모른다
            threading.Thread(target=_fit_app_window, daemon=True, name="fit-window").start()
            return _app_win      # 밴드의 `⏎` 가 이 창을 앞으로 올리려면 핸들을 들고 있어야 한다
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)
    return None


def _start_path() -> str:
    """다음 창이 열 주소의 경로 — 마지막에 보던 곳 (`/`, `/#stock`, `/folio/` …).

    **브라우저의 세션 복원을 쓰지 않는다** — 그건 포트까지 기억해서, 포트가 실행마다 바뀌는 경량판에서는
    죽은 주소를 연다. 값은 store.norm_page 를 한 번 더 지나므로 손으로 고친 settings.json 도 안전하다."""
    try:
        return store.norm_page(store.get_ui().get("last_page"))
    except Exception as e:   # 이것 때문에 창이 안 뜨면 안 된다
        _say(f"[ui] 마지막 화면을 못 읽었습니다 ({type(e).__name__}: {e}) — 첫 화면으로 엽니다")
        return "/"


def _start_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}{_start_path()}"


def _note_quit_page(page, referer) -> None:
    """`/api/quit` 를 부른 화면을 마지막 화면으로 적는다.

    본문에 `page` 가 있으면 그것. 없으면 **Referer** 의 경로 — 연주 화면에서 끄면 `/folio/` 가 된다.
    Referer 에는 해시(탭)가 없으므로, 이미 적힌 곳이 **같은 앱**이면 그대로 둔다(보던 탭을 지우지 않게).
    우리 주소가 아닌 Referer 는 보지 않는다. 실패해도 종료는 막지 않는다."""
    try:
        if isinstance(page, str) and store.page_known(page):
            store.set_ui(page=page)
            return
        if not isinstance(referer, str) or not referer:
            return
        ru = urlparse(referer)
        if ru.scheme != "http" or (ru.netloc or "").lower() not in _ok_hosts:
            return
        path = ru.path or "/"
        if not store.page_known(path):
            return
        if store.page_app(path) != store.page_app(store.get_ui().get("last_page")):
            store.set_ui(page=path)
    except Exception as e:
        _say(f"[ui] 끈 화면을 적지 못했습니다: {type(e).__name__}: {e}")


def _open_window() -> None:
    """브라우저 탭이 아니라 앱 창으로 연다. 경량판은 창이 닫히면(브라우저 프로세스 종료) 서버도 끝낸다.
    **마지막에 보던 화면으로 연다** (폴리오를 쓰다 껐으면 다음에도 폴리오)."""
    if os.environ.get("MOBIW_NO_BROWSER"):
        return
    url = _start_url(PORT)
    if LITE_REUSE:
        global _app_profile
        _app_profile = APP_PROFILE   # 창은 이미 떠 있고(페이지가 새 포트로 이동) 감시만 이어간다
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
_hold_conns: dict = {}   # 열려 있는 hold: 번호 → {"at": 시작 시각, "stop": 종료 이유(빈 문자열이면 계속)}
_hold_seq = 0
APP_GONE_SEC = 1.5       # 「닫는다」신호 뒤 이만큼 다시 안 붙으면 **밴드를 먼저 내린다** (app_gone_step 주석)
HOLD_POLL = 1.0          # hold 루프가 상대 종료를 보는 주기 (select 대기) — 닫히면 그 자리에서 깬다
# `bye` 와 「끊김을 알아챈 시각」 사이에 허용할 틈. **우리가 알아채는 데 걸리는 시간**이 기준이다.
# 모비폴리오는 이 값을 1.0 으로 그대로 옮겼다가 **정상적인 닫힘이 전부 걸러졌다** — 그쪽 hold 는
# 5초마다 쓰기가 실패할 때 끊김을 알아서, `last_close` 가 늘 `bye` 보다 5초 뒤에 찍혔다.
# 우리는 select 로 즉시 깨지만(HOLD_POLL), 스케줄러가 밀리면 1초를 넘길 수 있어 여유를 둔다.
HOLD_LAG = HOLD_POLL + 1.5
HOLD_MAX = 8             # 동시에 열어 둘 hold 상한 — 넘으면 오래된 것부터 끊는다 (창 8개면 충분하다)
HOLD_TTL = 1800.0        # hold 한 개의 최대 수명(초). 넘으면 스스로 끊는다 — UI 가 1초 뒤 다시 잇는다(ui/js/core.js)
_app_profile = ""        # 앱 창을 띄운 브라우저의 전용 프로필 경로 ("" = 기본 브라우저 탭으로 열었음)
_app_win = None          # 앱 창 프로세스 핸들 (밴드의 `⏎`·`⚙` 가 그 창을 앞으로 올릴 때 쓴다)
_app_hwnd = 0            # 한 번 찾은 앱 창 (제목이 바뀌어도 계속 맞는다)
_go_tab = ""             # 밴드가 요청한 탭 — /api/queue 가 한 번 싣고 지운다
_go_drawer = False       # 밴드의 「창에서 담기」 — /api/queue 가 goDrawer 를 한 번 싣고 지운다 (페이지가 담기 서랍을 연다)


def _hold_evict(conns: dict, limit: int = HOLD_MAX) -> list:
    """상한을 넘으면 오래된 hold 부터 종료 표시 (순수 함수 — 테스트용). 돌려주는 것: 표시한 번호 목록.
    브라우저가 페이지를 떠났는데 서버가 아직 모르는 연결이 쌓여도 상한 위로는 자라지 못하게 하는 마지막 방어다."""
    live = sorted((k for k, v in conns.items() if not v["stop"]), key=lambda k: conns[k]["at"])
    doomed = live[:max(0, len(live) - limit)]
    for k in doomed:
        conns[k]["stop"] = "evicted"
    return doomed


def _hold_step(now: float, started: float, stop: str, peer_gone: bool, ttl: float = HOLD_TTL) -> str:
    """hold 루프 한 걸음의 종료 판정 (순수 함수 — 테스트용). 돌려주는 것: 종료 이유 | ""."""
    if stop:
        return stop
    if peer_gone:
        return "peer closed"
    if now - started >= ttl:
        return "max age"
    return ""


def _peer_gone(sock, timeout: float) -> bool:
    """상대가 연결을 닫았는가 (최대 timeout 초 기다린다).
    Windows 에서는 브라우저가 페이지를 떠나도 wfile.write 가 한동안 성공한다 — 쓰기 실패만 믿으면 스레드가 남아
    동시연결 슬롯이 마르고 정적 파일까지 거부된다(실측: hold 31개 버린 뒤 ERR_CONNECTION_REFUSED).
    읽기 가능해졌는데 0바이트면 EOF = 상대가 닫은 것이다."""
    try:
        if not select.select([sock], [], [], timeout)[0]:
            return False
        try:
            if not sock.recv(1, socket.MSG_PEEK):
                return True
        except BlockingIOError:
            return False
        time.sleep(timeout)   # 읽을 것이 남아 있으면(파이프라인) 바쁜 대기를 피한다
        return False
    except (OSError, ValueError):   # 이미 닫힌 소켓은 select 가 ValueError (fileno -1)
        return True


def _app_window_alive() -> bool | None:
    """전용 프로필로 띄운 Edge/Chrome 창이 아직 있는가. 앱 창 모드가 아니면 None(판단 불가).
    Edge 는 한동안 안 쓴 창을 절전(슬리핑 탭)시키며 연결을 끊을 수 있어, 연결 끊김만으로는 '창 닫힘'을 단정할 수 없다 (실측).
    Chromium 은 프로필마다 제목이 프로필 경로인 메시지 전용 창(클래스 Chrome_MessageWindow)을 하나 둔다 — 그 창을 user32 로 찾는다.
    (프로세스 명령줄을 PowerShell 로 뒤지면 백신이 'PowerShell 실행' 행동으로 오탐한다 — MobiFolio 0.2.2 에서 실제로 겪어 뺀 방식이다.)"""
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


def _lite_watchdog() -> None:
    """경량판 종료 판정.
    1) 페이지가 /api/hold 연결을 계속 열어 둔다 — 창이 닫히거나 브라우저가 죽으면 끊긴다 (타이머와 무관, 최소화해도 유지).
    2) 연결이 끊겨도 바로 끝내지 않고, 전용 프로필의 브라우저 프로세스가 아직 있으면(절전된 창) 살아 있는 것으로 본다.
       프로세스까지 없어졌을 때만 종료. 앱 창 모드가 아니면(기본 브라우저 탭) 연결이 60초 이상 없을 때 종료.
    3) 창이 90초 안에 한 번도 붙지 않으면(브라우저를 못 띄운 경우) 고아로 남지 않게 끝낸다.
    창이 있는데(alive) hold 가 오래 0 이어도 끝내지 않는다 — Edge 가 재운 정상 창이 그 모양이다(MobiFolio 실제 항의).
    같은 프로필의 죽은 개발 창이 exe 를 속이던 사고는 APP_PROFILE 분리로 막는다."""
    last_check = 0.0; misses = 0
    while True:
        time.sleep(1.0)
        now = time.time()
        with _hold_lock:
            h, had, lc = _holds, _had_hold, _last_hold_close
        alive = None
        if (had or _bye_at) and h == 0 and (now - max(lc, _bye_at)) > 4.0 and now - last_check >= 5.0:
            last_check = now
            alive = _app_window_alive()
        reason, misses = _watchdog_step(now, h, had, lc, _bye_at, _start_at, alive, misses,
                                        checked=(last_check == now))
        if reason:
            _say(f"[lite] {reason} — exiting"); _shutdown()


def app_gone_step(now: float, holds: int, bye_at: float, last_close: float,
                  gap: float = 1.5, lag: float = 2.5) -> bool:
    """창이 **닫혔다고 스스로 알려 왔고** 그 뒤로 다시 안 붙었나 (순수 함수 — 테스트용).

    왜 따로 두나 — 프로세스 종료 판정(`_watchdog_step`)은 **10초**가 걸린다. 창이 닫힌 걸
    한 번 보고 믿지 않기 때문이다(5초 간격 2회). 그 신중함은 「앱이 혼자 꺼진다」를 막는
    값이라 그대로 두지만, 그동안 **오버레이 밴드가 게임 위에 유령으로 남는다**.
    밴드는 지우고 다시 띄우는 비용이 없으므로 먼저 내렸다가 창이 돌아오면 다시 띄운다.

    **「알리지 않고 끊긴 것」과 구별하는 것이 핵심이다.** Edge 가 탭을 재우면 hold 만 끊기고
    `bye` 는 안 온다 — 그건 창이 살아 있는 것이므로 밴드를 내리면 안 된다.
    그래서 `bye` 가 없거나(`bye_at <= 0`), 끊김이 `bye` 보다 **한참 뒤**면 내리지 않는다.

    「한참」의 기준(`lag`)은 **우리가 끊김을 알아채는 데 걸리는 시간**이다. 이걸 남의 값으로
    베끼면 안 된다 — 1.0 같은 값으로 두면 hold 감지가 5초라서
    **정상적인 닫힘이 전부 걸러진다**(밴드가 하나도 안 내려간다).

    F5 새로고침도 `bye` 를 보내지만 1초 안에 다시 붙으므로 `gap`(1.5초)을 못 넘겨 안 내려간다."""
    if holds > 0 or bye_at <= 0:
        return False
    if last_close > bye_at + lag:      # 끊김이 bye 보다 **한참** 뒤 = 이 bye 와 무관한 끊김 (절전 등)
        return False
    return (now - max(bye_at, last_close)) > gap


def app_window_gone() -> bool:
    """오버레이가 매 틱 물어 온다 (`overlay.OVERLAY.configure(app_gone=…)`)."""
    with _hold_lock:
        h, lc = _holds, _last_hold_close
    return app_gone_step(time.time(), h, _bye_at, lc, APP_GONE_SEC, HOLD_LAG)


def _watchdog_step(now: float, holds: int, had: bool, last_close: float, bye_at: float, start_at: float,
                   alive, misses: int, checked: bool = True) -> tuple:
    """종료 판정 한 걸음 (순수 함수 — 테스트용). alive 는 이번 틱에 창을 확인했을 때만 True/False/None, 안 했으면 None.
    돌려주는 것: (종료 이유 | None, 갱신된 misses)."""
    gone_for = now - max(last_close, bye_at) if (had or bye_at) else 0.0
    if checked and (had or bye_at) and holds == 0 and gone_for > 4.0:
        misses = misses + 1 if alive is False else 0
        if misses >= 2:   # 5초 간격 2회 연속 없음 (Edge 가 스스로 재시작하는 짧은 구간에 오판하지 않게)
            return "window closed", misses
        if alive is None and gone_for > 60.0:
            return "no window connection for 60s", misses
        # alive is True 이고 hold 가 0 인 상태는 아무리 길어도 종료하지 않는다 (절전 탭 관용)
    if not had and now - start_at > 90.0:
        return "no window attached in 90s", misses
    return None, misses


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


def _lite_reacquire() -> None:
    """롤백 뒤 단일 인스턴스 자리를 다시 잡는다 (업데이트를 넘기려고 놓았던 것)."""
    global _mutex
    try:
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}Lite")
        with open(LITE_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": PORT, "pid": os.getpid()}, f)
    except Exception as e:
        _say(f"[lite] reacquire failed: {e}")


def _lite_single_instance() -> bool:
    """경량판 중복 실행: 이미 떠 있으면 그 포트로 창만 하나 더 열고 False. 처음이면 lite.json 에 포트를 적고 True."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        global _mutex
        _mutex = k32.CreateMutexW(None, False, f"Local\\{APP_NAME}Lite")
        if k32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            try:
                with open(LITE_FILE, encoding="utf-8") as f:
                    port = int(json.load(f).get("port", 0))
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2).read()
                _launch_app_window(_start_url(port))
                return False
            except Exception:
                pass   # 죽은 기록이면 그냥 새로 뜬다
        with open(LITE_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": PORT, "pid": os.getpid()}, f)
    except Exception as e:
        print(f"[lite] single-instance check failed: {e}", flush=True)
    return True


_slot_tl = threading.local()   # 이 스레드가 동시연결 슬롯을 아직 쥐고 있는가


def _release_slot() -> None:
    """이 스레드가 쥔 동시연결 슬롯을 미리 놓는다 (오래 사는 /api/hold 가 일반 요청 자리를 먹지 않게).
    슬롯을 놓아도 hold 자체는 HOLD_MAX 로 따로 묶여 있어 스레드가 무한히 늘지 않는다."""
    if getattr(_slot_tl, "held", False):
        _slot_tl.held = False
        try:
            _Server._slots.release()
        except ValueError:   # 이미 놓았으면 무시 (BoundedSemaphore 초과 해제 방지)
            pass


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False   # 같은 사용자의 다른 프로세스가 포트를 가로채지 못하게 (SO_REUSEADDR 끔 + 배타 사용)
    daemon_threads = True
    # 동시 연결 상한 — 느린 연결로 스레드를 무한히 잡아 두지 못하게.
    # 32 는 너무 낮았다: 연결만 열고 아무것도 안 보내는 소켓 40개면 앱이 통째로 먹통이 됐다(실측).
    # 브라우저는 페이지 하나에 커넥션을 여럿 열고 /api/hold 까지 물리므로 정상 사용만으로도 가깝게 찬다.
    # 점유 시간은 HEADER_TIMEOUT(5초)이 막으므로 상한을 넉넉히 둔다 — 상한을 올리는 것만으로는
    # 공격 연결 수를 조금 늘릴 뿐이고, 실제 방어는 "빨리 놓게" 하는 쪽이다.
    _slots = threading.BoundedSemaphore(128)
    # listen 백로그. socketserver 기본값 5 는 너무 작다 — 페이지 하나가 js 14 + css 11 + /api/hold 를
    # 거의 동시에 요청하는데, 그 사이 무거운 작업(관찰 DB 2.5MB JSON 파싱 등)이 GIL 을 잡으면
    # accept 루프가 잠깐 멈춘다. 그동안 들어온 SYN 이 5개를 넘으면 Windows 가 RST 로 끊어
    # 브라우저에 ERR_CONNECTION_REFUSED 가 나고 스크립트가 통째로 안 실려 빈 화면이 된다
    # (실측: 파싱 중 60연결 버스트 → 720건 중 266건 거부).
    # _slots 와 같은 값으로 둔다 — 백로그가 슬롯보다 좁으면 조용히 RST 가 나고, 같거나 넓으면
    # 슬롯 상한에 걸려 503 으로 이유를 알려 줄 수 있다.
    request_queue_size = 128

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:   # 그냥 끊지 말고 이유를 알려 준다 (브라우저 콘솔에서 원인이 보이게)
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                                b"Content-Length: 0\r\nCache-Control: no-store\r\n\r\n")
            except OSError:
                pass
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
        _slot_tl.held = True   # 슬롯은 process_request 가 이미 잡았다 — 이 스레드가 놓을 책임을 진다
        try:
            super().process_request_thread(request, client_address)
        finally:
            if getattr(_slot_tl, "held", False):
                _slot_tl.held = False
                self._slots.release()

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _reexec_if_inherited_mei() -> None:
    """옛 버전이 업데이트로 우리를 띄울 때 PyInstaller 내부 변수를 물려줬으면, 우리는 옛 임시 폴더를 빌려 쓰는 상태다
    (곧 삭제되어 화면 파일이 사라진다). 그 경우 깨끗한 환경으로 자신을 다시 실행하고 끝난다."""
    if not (FROZEN and LITE_REUSE):
        return
    old_mei = os.environ.get("MOBIW_OLD_MEI", ""); mine = getattr(sys, "_MEIPASS", "")
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
    if EMBED:
        # 시작 폴더를 설치 폴더로 — 시작 메뉴 바로가기는 `app\` 에서 띄운다(shortcut.spec). 프로세스의 시작 폴더는
        # 그 폴더를 잡아 **이름을 못 바꾸게** 한다(실측 WinError 32) — 제자리 업데이트가 app\ 을 바꿔야 한다
        try:
            os.chdir(os.path.dirname(HERE))
        except OSError:
            pass
    os.makedirs(store.DATA_DIR, exist_ok=True)
    cli.set_exe_override(str(store.get_settings().get("cli_exe") or ""))
    try:
        recipedb.load()   # 이관을 기동 시에 먼저 끝낸다 — 첫 sync 가 캐시를 덮기 전에 (멱등)
    except Exception as e:
        _say(f"recipedb: load at startup failed: {type(e).__name__}: {e}")
    try:   # 앱에 묶인 시드(익명 관찰 합본) — 같은 파일은 한 번만 병합, 내 관찰을 덮지 않는다
        recipedb.apply_seed_file(os.path.join(RES, "data", "recipes.seed.json"))
    except Exception as e:
        _say(f"recipedb: seed failed: {type(e).__name__}: {e}")
    if LITE and not _lite_single_instance():
        return
    try:
        srv = _Server(("127.0.0.1", PORT), H)
        _srv = srv
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):
            # 이미 떠 있음(포트 사용 중) → 창만 다시 연다
            print(f"[{APP}] port {PORT} busy — opening browser only", flush=True)
            _open_window(); time.sleep(1.5)
            return
        print(f"[{APP}] port {PORT} bind failed: {e} (errno {e.errno}) — 예약 포트(Hyper-V/WinNAT)일 수 있습니다", flush=True)
        sys.exit(1)
    _say(f"[{APP}] http://127.0.0.1:{PORT}  frozen={FROZEN} embed={EMBED} lite={LITE} reuse={LITE_REUSE} demo={cli.DEMO} nocli={cli.NO_CLI} "
         f"pid={os.getpid()} v{VERSION}")
    _cleanup_bak()
    _cleanup_update()
    _watch_parent()
    if EMBED:
        threading.Thread(target=_shortcut_boot, daemon=True, name="shortcut").start()
    # **연주 감시를 띄운다.** 여기가 유일한 자리다 (검사는 이 함수를 돌리지 않는다).
    # 엔진을 불러오는 값이 시작 시간에 얹히지 않도록 곁스레드에서 한다 — 실패해도
    # 앱은 떠야 하므로 삼킨다.
    def _folio_boot():
        # **쓰던 악보를 먼저 가져온다.** 통합하면서 자료 집이 `MobiFolio\data` 에서
        # `MobiWorks\data\folio` 로 바뀌었는데, 옮겨 오는 길(`store.adopt_folio`)을
        # 여기서 부르지 않으면 악보함도 악기도 재생목록도 **빈 채로** 뜬다.
        #
        # 복사이고 한 번뿐이다: 옛 폴더는 그대로 두고(통합이 막히면 옛 앱을 다시 켤 수
        # 있어야 한다), 이미 있는 파일은 건너뛴다(통합 뒤에 고친 것을 옛것이 덮지 않게).
        # 감시보다 **먼저** 해야 한다 — 감시가 빈 악보함을 먼저 읽으면 그 판은 빈 채로 돈다.
        try:
            r = store.adopt_folio()
            if r.get("moved"):
                _say(f"[folio] 쓰던 자료를 가져왔습니다: {', '.join(r['moved'])} ({r.get('from')})")
        except Exception as e:
            _say(f"[folio] 쓰던 자료를 가져오지 못했습니다: {type(e).__name__}: {e}")
        try:
            folio_watch_start()
        except Exception as e:
            _say(f"[folio] 연주 감시를 띄우지 못했습니다: {type(e).__name__}: {e}")
        # 시작 연출(WebView2)도 여기서 — 오버레이 밴드를 켜지 않아도 연출은 있어야 한다
        try:
            folio_engine().opening_boot()
        except Exception as e:
            _say(f"[opening] 연출을 못 만들었습니다: {type(e).__name__}: {e}")

    threading.Thread(target=_folio_boot, daemon=True, name="folio-boot").start()
    _open_window()
    # 오버레이 밴드: 설정에서 켜 두었을 때만 뜬다 (기본 꺼짐). MOBIW_NO_BROWSER 는 창을 띄우지 않는 검증용이라 같이 막는다.
    if not os.environ.get("MOBIW_NO_BROWSER"):
        try:
            overlay.OVERLAY.apply(store.get_settings())
        except Exception as e:
            _say(f"overlay: 시작 실패 — {type(e).__name__}: {e}")
        folio_overlay_sync()   # 폴리오 밴드 (설정에서 켜 두었을 때만 — 기본 꺼짐)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    _EXIT_LOGGED.wait(3.0)   # 종료 스레드가 마지막 로그 줄을 쓸 때까지 인터프리터 정리를 미룬다 (위 _EXIT_LOGGED 참고)


if __name__ == "__main__":
    main()
