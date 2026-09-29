"""터널 — 집 방화벽을 건드리지 않고 밖에서 들어올 길을 연다 (Cloudflare Quick Tunnel).

**모비폴리오(마비 위젯)의 `tunnel.py` 를 그대로 가져왔다.** 이름만 바꾸고, 우리 사정에
맞춰 **유휴 자동 닫기** 하나를 더했다 — 그쪽은 「켜 두고 잊으면 계속 열려 있다」를 빈 곳으로
인정했고, 우리는 문 너머에 **정령의 날개를 쓰는 실행**이 있어 그냥 둘 수 없다.

왜 이렇게 하나:
  포트 포워딩·공유기 설정·고정 IP 가 전부 필요 없다. `cloudflared` 가 **밖으로 나가는** 연결을
  하나 만들고, 폰의 요청이 그 연결을 타고 되돌아온다. 통신사 CGNAT 뒤에서도 된다.
  계정도 필요 없다 — `--url` 만 주면 그 자리에서 `https://…trycloudflare.com` 주소를 준다.
  프로세스를 끄면 주소도 사라진다(= 세션이 끝나면 저절로 수거된다).

알아 둘 것:
  * **주소가 켤 때마다 바뀐다.** 고정 주소는 계정·도메인이 있어야 한다(Named Tunnel).
  * 클라우드플레어가 Quick Tunnel 을 「시험용, 보장 없음」이라고 못박아 두었다. 끊기거나
    제한될 수 있으니 실패를 정상 경로로 다룬다.
  * 실행 파일이 크다(수십 MB). exe 에 넣지 않고 **처음 켤 때 받아** 데이터 폴더에 둔다.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import urllib.request

# 윈도 64비트 정식 배포본. 클라우드플레어가 직접 올리는 자리다.
DL_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Tunnel:
    """터널 하나. `start()` 로 열고 `stop()` 으로 닫는다. 상태는 `state()` 로 본다."""

    def __init__(self, data_dir: str, log=print):
        self._dir = data_dir
        self._log = log
        self._p: subprocess.Popen | None = None
        self._url = ""
        self._err = ""
        self._busy = ""            # "" · "내려받는 중" · "여는 중" · "주소 퍼지는 중"
        self._ready = False        # 주소가 밖에서 **실제로 닿는가** (DNS 가 퍼졌는가)
        # 재진입 가능해야 한다 — start() 가 락을 쥔 채 running() 을 부른다 (일반 Lock 이면 멈춘다)
        self._lock = threading.RLock()
        self._since = 0.0

    # ── 실행 파일 ──
    def exe_path(self) -> str:
        return os.path.join(self._dir, "cloudflared.exe")

    def have_exe(self) -> bool:
        p = self.exe_path()
        return os.path.isfile(p) and os.path.getsize(p) > 1_000_000

    def _wait_ready(self, url: str) -> None:
        """주소가 밖에서 닿을 때까지 기다린다.

        cloudflared 가 「열렸다」고 말한 직후에는 그 이름이 아직 **DNS 에 퍼지지 않아**
        브라우저가 ERR_NAME_NOT_RESOLVED 를 낸다(실측 수십 초). 이때 코드를 건네면
        폰은 못 들어가고, 우편함 코드는 한 번 읽히면 사라지므로 헛걸음이 된다.
        그래서 닿는 것을 확인한 뒤에야 다 됐다고 한다.
        """
        self._busy = "주소 퍼지는 중"
        end = time.time() + 120
        while time.time() < end and self.running():
            try:
                req = urllib.request.Request(url + "/api/health",
                                             headers={"User-Agent": "MobiWorks"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    if r.status == 200:
                        self._ready = True
                        self._busy = ""
                        self._log(f"[tunnel] 밖에서 닿습니다 — {url}")
                        return
            except Exception:
                pass
            time.sleep(3)
        self._busy = ""
        if not self._ready:
            self._log("[tunnel] 주소가 아직 밖에서 안 닿습니다")

    def _fetch_exe(self) -> bool:
        """처음 한 번만 받는다. 받는 동안 상태를 알려 준다."""
        if self.have_exe():
            return True
        tmp = self.exe_path() + ".part"
        try:
            os.makedirs(self._dir, exist_ok=True)
            self._busy = "내려받는 중"
            self._log("[tunnel] cloudflared 를 내려받습니다 (처음 한 번)")
            req = urllib.request.Request(DL_URL, headers={"User-Agent": "MobiWorks"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.exists(self.exe_path()):
                os.remove(self.exe_path())
            os.replace(tmp, self.exe_path())
            self._log(f"[tunnel] 받았습니다 ({os.path.getsize(self.exe_path()) // (1 << 20)}MB)")
            return True
        except Exception as e:
            self._err = f"내려받지 못했습니다: {e}"
            self._log(f"[tunnel] {self._err}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        finally:
            self._busy = ""

    # ── 열고 닫기 ──
    def start(self, port: int, on_url=None) -> dict:
        """터널을 연다. 주소는 곧바로 나오지 않으므로 `on_url(주소)` 로 알려 준다."""
        with self._lock:
            if self.running():
                return {"ok": True, "url": self._url, "already": True}
            self._err = ""
            self._url = ""
            self._ready = False
        threading.Thread(target=self._run, args=(int(port), on_url), daemon=True).start()
        return {"ok": True, "starting": True}

    def _run(self, port: int, on_url) -> None:
        if not self._fetch_exe():
            return
        self._busy = "여는 중"
        args = [self.exe_path(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            self._err, self._busy = f"실행하지 못했습니다: {e}", ""
            self._log(f"[tunnel] {self._err}")
            return
        with self._lock:
            self._p, self._since = p, time.time()
        self._log("[tunnel] 여는 중…")
        try:
            for raw in p.stdout:                      # cloudflared 는 주소를 로그로 뱉는다
                line = raw.decode("utf-8", "replace").strip()
                m = URL_RE.search(line)
                if m and not self._url:
                    self._url = m.group(0)
                    self._busy = ""
                    self._log(f"[tunnel] 열렸습니다 — {self._url}")
                    if on_url:
                        try:
                            on_url(self._url)
                        except Exception:
                            pass
                    # 이름이 퍼질 때까지는 아직 「다 된 것」이 아니다
                    threading.Thread(target=self._wait_ready, args=(self._url,),
                                     daemon=True).start()
                elif "failed" in line.lower() and not self._url:
                    self._err = line[:200]
        finally:
            code = p.poll()
            with self._lock:
                self._p = None
            self._busy = ""
            if not self._url and not self._err:
                self._err = f"열리지 않았습니다 (종료 코드 {code})"
            self._url = ""
            self._ready = False
            self._log(f"[tunnel] 닫혔습니다 (종료 코드 {code})")

    def stop(self) -> dict:
        with self._lock:
            p = self._p
        if p is None:
            return {"ok": True, "already": True}
        try:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        except Exception as e:
            return {"ok": False, "message": str(e)}
        self._url = ""
        self._ready = False
        return {"ok": True}

    def running(self) -> bool:
        with self._lock:
            return self._p is not None and self._p.poll() is None

    def state(self) -> dict:
        return {"ok": True, "running": self.running(), "url": self._url, "busy": self._busy,
                "ready": self._ready, "error": self._err, "have_exe": self.have_exe(),
                "since": round(time.time() - self._since, 1) if self.running() else 0.0,
                "idleLeft": self.idle_left()}

    # ── 유휴 자동 닫기 (모비폴리오에는 없는 것) ──
    # 그쪽은 「켜 두고 잊으면 계속 열려 있다」를 빈 곳으로 인정했다. 우리는 문 너머에
    # **날개를 쓰는 실행**이 있어 그냥 둘 수 없다. 마지막 원격 요청 뒤 일정 시간이
    # 지나면 스스로 닫는다. 잠깐 안 쓰는 것과 잊은 것을 구별할 길은 없으니 **시간으로** 센다.
    def watch_idle(self, last_seen, minutes_fn) -> None:
        """`last_seen()` 은 마지막 원격 요청 시각, `minutes_fn()` 은 설정값(0 이면 안 닫음)."""
        def loop():
            while True:
                time.sleep(20)
                try:
                    if not self.running():
                        continue
                    m = int(minutes_fn() or 0)
                    if m <= 0:
                        continue
                    seen = float(last_seen() or 0) or self._since
                    if time.time() - seen > m * 60:
                        self._log(f"[tunnel] {m}분 동안 밖에서 아무 요청이 없어 닫습니다")
                        self.stop()
                except Exception:
                    pass          # 감시가 터져도 터널은 계속 돌아야 한다
        if not getattr(self, "_watching", False):
            self._watching = True
            self._last_seen, self._minutes = last_seen, minutes_fn
            threading.Thread(target=loop, daemon=True).start()

    def idle_left(self) -> int:
        """자동으로 닫히기까지 남은 초. 안 닫는 설정이거나 안 돌면 0."""
        if not self.running() or not getattr(self, "_watching", False):
            return 0
        try:
            m = int(self._minutes() or 0)
            if m <= 0:
                return 0
            seen = float(self._last_seen() or 0) or self._since
            return max(0, int(seen + m * 60 - time.time()))
        except Exception:
            return 0
