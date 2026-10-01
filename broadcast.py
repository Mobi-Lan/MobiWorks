# -*- coding: utf-8 -*-
"""방송 모드 — 스트리머 PC 쪽 방 상태 (docs/handoff/broadcast-api.md).

손님은 스트리머 PC 의 Quick Tunnel 로 바로 붙는다. 우편함 Worker 는 **코드 → 터널 주소**만 건넨다.
이 모듈은 표준 라이브러리만 쓰고, 바깥(터널·우편함·폴리오 엔진·창 띄우기)은 전부 `configure()` 로 받는다 —
검사는 가짜를 꽂아 실제 cloudflared·우편함·게임 CLI 를 건드리지 않는다.

지키는 것 (계약 §7):
  · 손님이 보낸 글(이름)은 게임 채팅·연주 인사로 나가지 않는다 — 이 모듈은 그런 길을 부르지 않는다.
    이름은 방 상태와 대기열의 표시 칸(`_Q["req"]`)에만 들어간다.
  · 손님은 공개 목록 안의 key 만 신청한다. 자유 입력 없음.
  · 손님 토큰은 `secrets.token_urlsafe(18)` · 방송을 끄면 전부 무효.
  · CLI 를 부르지 않는다 — 「지금 연주 중」은 엔진이 이미 들고 있는 값(`_NOW`)에서 읽는다.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.request

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # 헷갈리는 글자(0 O 1 I) 뺀 32자
CODE_RX = re.compile(r"^[%s]{6}$" % ALPHABET)
NAME_MAX = 12                  # 손님 이름 상한 (자른다)
NAME_DEFAULT = "익명"
MINE_MAX = 20                  # 손님 「내 신청」 최근 개수
DONE_MAX = 20                  # 스트리머 「처리됨」 최근 개수
JOIN_PER_MIN = 10              # 같은 곳(IP)에서 들어가기 — 분당
REQ_PER_MIN = 6                # 손님 한 명의 신청 — 분당 (간격 규칙과 별개)
RETRY_MAX = 5                  # 터널이 끊겼을 때 다시 잇는 횟수
TICK_SEC = 2.0                 # 감시 주기
GUEST_ALIVE_SEC = 30.0         # 손님 수 — 이 안에 한 번이라도 두드린 손님만 센다 (손님 화면은 5초마다 읽는다)
INTERVAL_RANGE = (1, 30)       # 1인 신청 간격(분)
CAP_RANGE = (1, 99)            # 대기 신청 상한

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def clean_name(v) -> str:
    """손님 이름 — 제어문자·방향 제어 글자를 빼고 12자로 자른다. 비면 「익명」."""
    s = _CTRL.sub("", v if isinstance(v, str) else "")
    s = re.sub(r"\s+", " ", s).strip()[:NAME_MAX].strip()
    return s or NAME_DEFAULT


def norm_code(v) -> str:
    """입력 코드 — 대소문자·`-`·공백 무시."""
    return re.sub(r"[\s\-]", "", v if isinstance(v, str) else "").upper()[:16]


def show_code(code: str) -> str:
    """`7K2Q9M` → `7K2Q-9M` (시안 표기)."""
    return f"{code[:4]}-{code[4:]}" if code else ""


# ── 바깥 ────────────────────────────────────────────────────────────────
def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
    """우편함 부르기. 답이 JSON 객체가 아니면 ok:false."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": "MobiWorks"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": False, "error": "mailbox", "message": f"{type(e).__name__}"}
    return out if isinstance(out, dict) else {"ok": False, "error": "mailbox"}


class OfflineTunnel:
    """개발·검사용 가짜 터널 — cloudflared 를 띄우지 않는다. 주소는 닿지 않는 이름이다 (이 PC 안에서 손님 길을 시험할 때만)."""
    HOST = "bc-offline.trycloudflare.com"

    def __init__(self):
        self._on = False

    def start(self, port, on_url=None) -> dict:
        self._on = True
        if on_url:
            on_url("https://" + self.HOST)
        return {"ok": True}

    def stop(self) -> dict:
        self._on = False
        return {"ok": True}

    def state(self) -> dict:
        return {"ok": True, "running": self._on, "url": ("https://" + self.HOST) if self._on else "",
                "busy": "", "ready": self._on, "error": ""}


def offline_http(method: str, url: str, body: dict | None = None, **_kw) -> dict:
    """개발·검사용 가짜 우편함 — 밖으로 나가지 않고 코드만 만든다."""
    if method == "POST" and url.endswith("/bc"):
        return {"ok": True, "code": "".join(secrets.choice(ALPHABET) for _ in range(6)),
                "secret": secrets.token_urlsafe(18), "ttl": 43200}
    return {"ok": True}


HOOKS: dict = {
    "mailbox": lambda: "https://link.mobimml.com",   # 우편함 뿌리 주소 (server.MAILBOX)
    "http": _http_json,                               # (method, url, body) → dict
    "tunnel": None,                                   # () → 터널 (start(port, on_url) · stop() · state())
    "port": lambda: 0,
    "on_url": None,                                   # 터널 주소가 나왔을 때 (server._tunnel_got_url)
    "remote_in_use": lambda: False,                   # 원격 리모컨이 터널을 쓰는가 (끌 때 터널을 닫을지)
    "activity": lambda: None,                         # 손님 요청을 터널 활동으로 센다 (유휴 자동 닫기)
    "engine": None,                                   # () → 폴리오 엔진 모듈
    "settings": None,                                 # () → 설정 dict
    "save": None,                                     # (patch) → 설정 저장
    "open_window": lambda: {"ok": False, "error": "no_window"},
    "say": lambda msg: None,
    "thread": False,                                  # 감시 스레드를 띄울지 (검사는 끄고 tick() 을 직접 부른다)
}


def configure(**kw) -> None:
    for k, v in kw.items():
        if k not in HOOKS:
            raise KeyError(k)
        HOOKS[k] = v


def _say(msg: str) -> None:
    try:
        HOOKS["say"](msg)
    except Exception:
        pass


def _clamp(v, lo, hi, dflt):
    if isinstance(v, bool):
        return dflt
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return dflt
    return max(lo, min(hi, n))


# ── 방 ──────────────────────────────────────────────────────────────────
class Room:
    """방송 하나 (앱에 하나). 모든 바꿈은 `_lock` 안에서, 바깥 부르기(우편함·터널)는 자물쇠 밖에서."""

    def __init__(self):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.on = False
            self.since = 0.0
            self.code = ""
            self.secret = ""
            self.url = ""                 # 우편함에 적어 둔 터널 주소
            self.guests: dict = {}        # 토큰 → {name, blocked, last_req, req_times, seen}
            self.wait: list = []          # 대기 신청 (들어온 차례)
            self.done: list = []          # 처리됨 (최근이 앞)
            self.mine: dict = {}          # 토큰 → 그 손님의 신청 id (최근이 앞)
            self.reqs: dict = {}          # id → 신청 (wait·done 모두)
            self.seq = 0
            self.conn = "off"
            self.retry = 0
            self.busy = ""                # 여는 중 (코드가 아직 없다)
            self.ever_ok = False
            self.started = False          # 이번 방송에서 터널을 한 번 열어 봤나
            self.join_hits: dict = {}     # 보낸 곳 → [시각…]
            self.pl_cache = (0.0, "", None)

    # ── 설정 (store 설정에 산다) ──
    def cfg(self) -> dict:
        s = HOOKS["settings"]() if HOOKS["settings"] else {}
        return {"interval_min": _clamp(s.get("bc_interval_min"), *INTERVAL_RANGE, 3),
                "cap": _clamp(s.get("bc_cap"), *CAP_RANGE, 20),
                "auto": bool(s.get("bc_auto"))}

    def _pl_id(self) -> str:
        s = HOOKS["settings"]() if HOOKS["settings"] else {}
        return str(s.get("bc_pl") or "")

    def set_cfg(self, p: dict) -> dict:
        patch = {}
        if "interval_min" in p:
            patch["bc_interval_min"] = _clamp(p.get("interval_min"), *INTERVAL_RANGE, self.cfg()["interval_min"])
        if "cap" in p:
            patch["bc_cap"] = _clamp(p.get("cap"), *CAP_RANGE, self.cfg()["cap"])
        if "auto" in p and isinstance(p.get("auto"), bool):
            patch["bc_auto"] = p["auto"]
        if patch and HOOKS["save"]:
            HOOKS["save"](patch)
        return self.cfg()

    # ── 공개 목록 ──
    def _eng(self):
        return HOOKS["engine"]() if HOOKS["engine"] else None

    def playlists(self) -> list:
        eng = self._eng()
        if eng is None:
            return []
        try:
            return list(eng._lists().get("playlists") or [])
        except Exception:
            return []

    def public(self) -> dict | None:
        """공개 목록 {id, name, songs:[{key,title,artist,sec}]} — 없으면 None. 2초 동안 같은 값을 쓴다."""
        now = time.time()
        pid = self._pl_id()
        at, cid, cached = self.pl_cache
        if cached is not None and cid == pid and now - at < 2.0:
            return cached
        pls = self.playlists()
        pl = next((x for x in pls if x.get("id") == pid), None) or (pls[0] if pls else None)
        out = None
        if pl is not None:
            eng = self._eng()
            by, dur = {}, {}
            try:
                by = {it["key"]: it for it in eng._build_items()}
                dur = eng.store.get_durations()
            except Exception:
                pass
            songs = []
            for it in pl.get("items") or []:
                k = str(it.get("key") or "")
                if not k:
                    continue
                lib = by.get(k) or {}
                t = lib.get("title") or it.get("title") or k
                songs.append({"key": k, "title": str(lib.get("song") or lib.get("cleaned") or t),
                              "artist": str(lib.get("artist") or ""), "sec": dur.get(t)})
            out = {"id": str(pl.get("id") or ""), "name": str(pl.get("name") or ""), "songs": songs}
        self.pl_cache = (now, pid, out)
        return out

    def _queue(self) -> dict:
        eng = self._eng()
        try:
            return eng.queue_state() if eng is not None else {}
        except Exception:
            return {}

    def _now(self, q: dict | None = None) -> dict | None:
        """지금 연주 중 — 엔진이 이미 본 값. CLI 를 부르지 않는다."""
        eng = self._eng()
        now = getattr(eng, "_NOW", None) if eng is not None else None
        if not isinstance(now, dict) or not now.get("playing"):
            return None
        q = q if q is not None else self._queue()
        it = q.get("now") if isinstance(q.get("now"), dict) else None
        title = str(now.get("title") or "")
        seen = float(now.get("at") or 0)
        gone = max(0.0, time.time() - seen) if seen else 0.0      # 감시가 마지막으로 본 뒤 흐른 시간
        left = max(0, int(float(now.get("tot") or 0) - float(now.get("el") or 0) - gone))
        if it and (it.get("title") == title or not title):
            return {"key": it.get("key") or "", "title": it.get("song") or it.get("title") or title,
                    "artist": it.get("artist") or "", "left": left}
        return {"key": "", "title": title, "artist": "", "left": left}

    # ── 상태 ──
    def _guests_alive(self) -> int:
        if self.conn == "down":
            return 0
        cut = time.time() - GUEST_ALIVE_SEC
        return sum(1 for g in self.guests.values() if g["seen"] >= cut)

    def _pos_in_queue(self, q: dict) -> dict:
        """key → 현재 재생목록에서 처음 나오는 자리 (1부터)."""
        out = {}
        for i, it in enumerate(q.get("items") or []):
            out.setdefault(it.get("key"), i + 1)
        return out

    def summary(self) -> dict:
        """본창·오버레이의 「방송 · 신청 n」 — 가볍게."""
        with self._lock:
            return {"on": self.on, "wait": len(self.wait)}

    def state(self) -> dict:
        pub = self.public()
        q = self._queue()
        nowp = self._now(q)
        at_q = self._pos_in_queue(q)
        with self._lock:
            cfg = self.cfg()
            out = {"on": self.on, "since": int(self.since) if self.on else 0, "code": self.code,
                   "conn": self.conn if self.on else "off",
                   "guests": self._guests_alive() if self.on else 0,
                   "cfg": cfg, "busy": self.busy if self.on else "",
                   "link": (HOOKS["mailbox"]().rstrip("/") + "/live/?c=" + self.code) if (self.on and self.code) else "",
                   "wait": [{"id": r["id"], "key": r["key"], "title": r["title"], "sec": r["sec"], "who": r["who"],
                             "at": int(r["at"]), "dup": at_q.get(r["key"])} for r in self.wait],
                   "done": [{"id": r["id"], "title": r["title"], "who": r["who"], "state": r["state"],
                             "pos": r.get("pos"), "at": int(r["done_at"])} for r in self.done[:DONE_MAX]],
                   "pl": None, "playlists": [{"id": str(p.get("id") or ""), "name": str(p.get("name") or ""),
                                              "count": len(p.get("items") or [])} for p in self.playlists()]}
            if self.conn == "down" and self.on:
                out["retry"] = {"n": self.retry, "max": RETRY_MAX}
            if pub is not None:
                out["pl"] = {"id": pub["id"], "name": pub["name"], "count": len(pub["songs"]),
                             "songs": [dict(s, playing=bool(nowp and nowp["key"] and s["key"] == nowp["key"]))
                                       for s in pub["songs"]]}
            return out

    # ── 켜고 끄기 ──
    def turn_on(self, pl=None) -> dict:
        if isinstance(pl, str) and pl:
            if not any(p.get("id") == pl for p in self.playlists()):
                return {"ok": False, "error": "not_found", "message": "그 재생목록이 없습니다."}
            if HOOKS["save"]:
                HOOKS["save"]({"bc_pl": pl})
            self.pl_cache = (0.0, "", None)
        elif not self._pl_id():
            pls = self.playlists()
            if pls and HOOKS["save"]:
                HOOKS["save"]({"bc_pl": str(pls[0].get("id") or "")})
        with self._lock:
            if not self.on:
                self.reset()
                self.on, self.since = True, time.time()
                self.conn, self.busy = "ok", "여는 중"
        _say("[bc] 방송을 켭니다")
        self._start_worker()
        self._wake.set()
        return {"ok": True}

    def turn_off(self) -> dict:
        with self._lock:
            was_on, code, secret = self.on, self.code, self.secret
            self.reset()
        if was_on:
            _say("[bc] 방송을 끕니다")
            self._wake.set()
            threading.Thread(target=self._close_out, args=(code, secret), daemon=True).start()
        return {"ok": True}

    def _close_out(self, code: str, secret: str) -> None:
        """끈 뒤처리 — 우편함에서 코드를 지우고, 원격 리모컨이 안 쓰면 터널을 닫는다."""
        if code and secret:
            HOOKS["http"]("DELETE", self._mb(f"/bc/{code}"), {"secret": secret})
        try:
            if not HOOKS["remote_in_use"]() and HOOKS["tunnel"]:
                HOOKS["tunnel"]().stop()
        except Exception as e:
            _say(f"[bc] 터널을 닫지 못했습니다: {type(e).__name__}")

    def shutdown(self) -> None:
        """앱을 끌 때 — 우편함에서 코드를 지운다 (짧게 기다린다. 못 지워도 우편함의 12시간 수명이 거둔다)."""
        with self._lock:
            code, secret, on = self.code, self.secret, self.on
            self.reset()
        if on and code and secret:
            try:
                HOOKS["http"]("DELETE", self._mb(f"/bc/{code}"), {"secret": secret}, timeout=3.0)
            except Exception:
                pass

    def _mb(self, path: str) -> str:
        return HOOKS["mailbox"]().rstrip("/") + path

    def new_code(self) -> dict:
        with self._lock:
            if not self.on:
                return {"ok": False, "error": "ended", "message": "방송이 꺼져 있습니다."}
            url, old, old_secret = self.url, self.code, self.secret
        if not url:
            return {"ok": False, "error": "no_tunnel", "message": "아직 터널 주소가 없습니다. 잠시 뒤에 다시 해 보세요."}
        got = self._register(url)
        if not got:
            return {"ok": False, "error": "mailbox", "message": "우편함에서 새 코드를 받지 못했습니다."}
        with self._lock:
            if not self.on:
                return {"ok": False, "error": "ended"}
            self.code, self.secret, self.url = got["code"], got["secret"], url
        if old and old_secret:
            HOOKS["http"]("DELETE", self._mb(f"/bc/{old}"), {"secret": old_secret})
        _say("[bc] 코드를 새로 받았습니다")
        return {"ok": True, "code": got["code"]}

    def _register(self, url: str) -> dict | None:
        out = HOOKS["http"]("POST", self._mb("/bc"), {"url": url})
        code = str(out.get("code") or "").strip().upper()
        secret = out.get("secret")
        if out.get("ok") is False or not CODE_RX.match(code) or not isinstance(secret, str) or not 0 < len(secret) <= 200:
            return None
        return {"code": code, "secret": secret}

    # ── 감시 (터널 · 우편함) ──
    def _start_worker(self) -> None:
        if not HOOKS["thread"]:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bc-watch")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            self._wake.wait(TICK_SEC)
            self._wake.clear()
            with self._lock:
                if not self.on:
                    self._thread = None
                    return
            try:
                self.tick()
            except Exception as e:
                _say(f"[bc] 감시가 넘어졌습니다: {type(e).__name__}: {e}")

    def tick(self) -> None:
        """한 번 본다: 터널이 살아 있나 → 우편함에 코드가 있나 → 터널 주소가 바뀌었나."""
        with self._lock:
            if not self.on:
                return
        if not HOOKS["tunnel"]:
            return
        t = HOOKS["tunnel"]()
        st = t.state()
        if not st.get("running") and not st.get("busy"):
            # 터널이 없다 — 처음이면 연다. 한 번 열었는데 없으면 끊긴 것이다 → 다시 잇기 (RETRY_MAX 번까지)
            with self._lock:
                if self.started:
                    self.conn = "down"
                    if self.retry >= RETRY_MAX:
                        return
                    self.retry += 1
                self.started = True
            HOOKS["activity"]()                       # 유휴 시계를 되감는다 — 다시 연 터널을 곧바로 닫지 않게
            t.start(HOOKS["port"](), on_url=HOOKS["on_url"])
            return
        with self._lock:
            self.started = True
        url = str(st.get("url") or "")
        if not url:
            return                                    # 여는 중 — 주소를 기다린다
        with self._lock:
            code, secret, reg = self.code, self.secret, self.url
        if not code:
            got = self._register(url)
            with self._lock:
                if not self.on:
                    return
                if got:
                    self.code, self.secret, self.url = got["code"], got["secret"], url
                    self._ok()
                else:
                    self._fail()
            return
        if url != reg:
            out = HOOKS["http"]("PUT", self._mb(f"/bc/{code}"), {"url": url, "secret": secret})
            with self._lock:
                if out.get("ok"):
                    self.url = url
                    self._ok()
                else:
                    self._fail()
            return
        with self._lock:
            self._ok()

    def _ok(self) -> None:
        self.conn, self.busy, self.retry, self.ever_ok = "ok", "", 0, True

    def _fail(self) -> None:
        self.conn = "down"
        self.retry = min(RETRY_MAX, self.retry + 1)

    def tunnel_host(self) -> str:
        with self._lock:
            u = self.url
        return u.split("://", 1)[-1].strip("/").lower() if u else ""

    # ── 스트리머 조작 ──
    def _take(self, rid: str):
        r = self.reqs.get(rid)
        if r is None or r["state"] != "wait":
            return None
        self.wait = [x for x in self.wait if x["id"] != rid]
        return r

    def _finish(self, r: dict, state: str, pos=None) -> None:
        r["state"], r["done_at"] = state, time.time()
        if pos is not None:
            r["pos"] = pos
        self.done.insert(0, r)
        del self.done[DONE_MAX * 2:]

    def approve(self, rid: str) -> dict:
        with self._lock:
            r = self._take(str(rid or ""))
            if r is None:
                return {"ok": False, "error": "not_found", "message": "대기 중인 신청이 아닙니다."}
        return self._append(r)

    def _append(self, r: dict) -> dict:
        eng = self._eng()
        try:
            out = eng.queue_append(r["key"], r["who"]) if eng is not None else {"ok": False}
        except Exception as e:
            out = {"ok": False, "error": type(e).__name__}
        with self._lock:
            if not out.get("ok"):                    # 담지 못했다 — 대기로 되돌린다
                r["state"] = "wait"
                self.wait.insert(0, r)
                return {"ok": False, "error": out.get("error") or "queue", "message": "재생목록에 담지 못했습니다."}
            self._finish(r, "ok", int(out.get("pos") or 0))
        return {"ok": True, "id": r["id"], "pos": r["pos"]}

    def reject(self, rid=None, all_=False) -> dict:
        with self._lock:
            ids = [x["id"] for x in self.wait] if all_ else [str(rid or "")]
            n = 0
            for i in ids:
                r = self._take(i)
                if r is not None:
                    self._finish(r, "no")
                    n += 1
            if not n and not all_:
                return {"ok": False, "error": "not_found", "message": "대기 중인 신청이 아닙니다."}
        return {"ok": True, "n": n}

    def block(self, rid: str) -> dict:
        with self._lock:
            r = self.reqs.get(str(rid or ""))
            if r is None:
                return {"ok": False, "error": "not_found", "message": "그 신청이 없습니다."}
            g = self.guests.get(r["guest"])
            if g is not None:
                g["blocked"] = True
            for x in [x for x in self.wait if x["guest"] == r["guest"]]:
                self._take(x["id"])
                self._finish(x, "cut" if x is r else "no")
        return {"ok": True}

    def set_pl(self, pid) -> dict:
        if not isinstance(pid, str) or not any(p.get("id") == pid for p in self.playlists()):
            return {"ok": False, "error": "not_found", "message": "그 재생목록이 없습니다."}
        if HOOKS["save"]:
            HOOKS["save"]({"bc_pl": pid})
        self.pl_cache = (0.0, "", None)
        return {"ok": True}

    # ── 손님 ──
    def _rate(self, bucket: list, limit: int, now: float) -> bool:
        """분당 상한을 넘었나 (넘지 않았으면 이번 것을 센다)."""
        bucket[:] = [t for t in bucket if now - t < 60.0]
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False

    def join(self, code, name, who: str) -> tuple:
        now = time.time()
        with self._lock:
            if not self.on:
                return 410, {"ok": False, "error": "ended"}
            for k in [k for k, v in self.join_hits.items() if not v or now - v[-1] > 60.0]:
                self.join_hits.pop(k, None)
            if self._rate(self.join_hits.setdefault(who or "?", []), JOIN_PER_MIN, now):
                return 429, {"ok": False, "error": "rate", "wait": 60}
            if not self.code or not secrets.compare_digest(norm_code(code).encode(), self.code.encode()):
                return 403, {"ok": False, "error": "bad_code"}
            tok = secrets.token_urlsafe(18)
            nm = clean_name(name)
            self.guests[tok] = {"name": nm, "blocked": False, "last_req": 0.0, "req_times": [], "seen": now}
            self.mine[tok] = []
        HOOKS["activity"]()
        return 200, {"ok": True, "guest": tok, "name": nm}

    def _guest(self, tok):
        if not isinstance(tok, str) or not tok:
            return None
        g = self.guests.get(tok)
        if g is not None:
            g["seen"] = time.time()
        return g

    def room(self, tok) -> tuple:
        pub = self.public()
        q = self._queue()
        nowp = self._now(q)
        with self._lock:
            if not self.on:
                return 410, {"ok": False, "error": "ended"}
            g = self._guest(tok)
            if g is None:
                return 401, {"ok": False, "error": "bad_guest"}
            cfg = self.cfg()
            songs = pub["songs"] if pub else []
            mine = []
            for rid in self.mine.get(tok, [])[:MINE_MAX]:
                r = self.reqs.get(rid)
                if r is None:
                    continue
                st = {"wait": "wait", "ok": "ok"}.get(r["state"], "no")
                row = {"id": r["id"], "key": r["key"], "title": r["title"], "state": st}
                if st == "ok":
                    row["pos"] = r.get("pos")
                mine.append(row)
            wait = max(0, int(g["last_req"] + cfg["interval_min"] * 60 - time.time() + 0.999)) if g["last_req"] else 0
            out = {"ok": True, "name": pub["name"] if pub else "", "count": len(songs), "now": nowp,
                   "songs": [{"key": s["key"], "title": s["title"], "artist": s["artist"], "sec": s["sec"]} for s in songs],
                   "mine": mine, "wait": wait, "full": len(self.wait) >= cfg["cap"], "blocked": bool(g["blocked"]),
                   "interval_min": cfg["interval_min"], "cap": cfg["cap"]}
        HOOKS["activity"]()
        return 200, out

    def request(self, tok, key) -> tuple:
        pub = self.public()
        now = time.time()
        with self._lock:
            if not self.on:
                return 410, {"ok": False, "error": "ended"}
            g = self._guest(tok)
            if g is None:
                return 401, {"ok": False, "error": "bad_guest"}
            if g["blocked"]:
                return 403, {"ok": False, "error": "blocked"}
            song = next((s for s in (pub["songs"] if pub else []) if isinstance(key, str) and s["key"] == key), None)
            if song is None:
                return 404, {"ok": False, "error": "not_in_list"}
            cfg = self.cfg()
            left = int(g["last_req"] + cfg["interval_min"] * 60 - now + 0.999) if g["last_req"] else 0
            if left > 0:
                return 429, {"ok": False, "error": "interval", "wait": left}
            if len(self.wait) >= cfg["cap"]:
                return 409, {"ok": False, "error": "full"}
            if self._rate(g["req_times"], REQ_PER_MIN, now):
                # 분당 상한 — 손님 화면은 간격 제한과 같은 모양(「n분 뒤에 다시」)으로 받는다
                return 429, {"ok": False, "error": "interval",
                             "wait": max(1, int(60 - (now - g["req_times"][0]) + 0.999))}
            self.seq += 1
            r = {"id": f"r{self.seq}", "key": song["key"], "title": song["title"], "sec": song["sec"],
                 "who": g["name"], "guest": tok, "at": now, "state": "wait"}
            self.reqs[r["id"]] = r
            self.wait.append(r)
            self.mine.setdefault(tok, []).insert(0, r["id"])
            del self.mine[tok][MINE_MAX * 2:]
            g["last_req"] = now
            auto = cfg["auto"]
        HOOKS["activity"]()
        if auto:
            with self._lock:
                r2 = self._take(r["id"])
            if r2 is not None:
                self._append(r2)
        return 200, {"ok": True, "id": r["id"]}

    def leave(self, tok) -> tuple:
        with self._lock:
            if not self.on:
                return 410, {"ok": False, "error": "ended"}
            if not isinstance(tok, str) or tok not in self.guests:
                return 401, {"ok": False, "error": "bad_guest"}
            self.guests.pop(tok, None)          # 이미 낸 신청은 스트리머 쪽에 그대로 남는다
        return 200, {"ok": True}


ROOM = Room()


def guest(op: str, body: dict, tok: str, who: str) -> tuple:
    """손님 길 네 개 → (상태, 답). `op` = join · room · req · leave."""
    body = body if isinstance(body, dict) else {}
    if op == "join":
        return ROOM.join(body.get("code"), body.get("name"), who)
    if op == "room":
        return ROOM.room(tok)
    if op == "req":
        return ROOM.request(tok, body.get("key"))
    if op == "leave":
        return ROOM.leave(tok)
    return 404, {"ok": False, "error": "not_found"}


def streamer(op: str, p: dict) -> dict:
    """스트리머 길 (`/api/folio/bc/*`) → 답."""
    p = p if isinstance(p, dict) else {}
    if op == "state":
        return {"ok": True, **ROOM.state()}
    if op == "on":
        pl = p.get("pl")
        return ROOM.turn_on(pl if isinstance(pl, str) else None)
    if op == "off":
        return ROOM.turn_off()
    if op == "code":
        return ROOM.new_code()
    if op == "pl":
        return ROOM.set_pl(p.get("id"))
    if op == "cfg":
        return {"ok": True, "cfg": ROOM.set_cfg(p)}
    if op == "ok":
        return ROOM.approve(p.get("id"))
    if op == "no":
        return ROOM.reject(p.get("id"), all_=p.get("all") is True)
    if op == "block":
        return ROOM.block(p.get("id"))
    if op == "window":
        return HOOKS["open_window"]()
    return {"ok": False, "error": "not_found"}
