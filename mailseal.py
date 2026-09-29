# -*- coding: utf-8 -*-
"""우편함 봉투 — 터널 주소를 **인증키로 봉해서** 우편함에 넣는다.

## 왜

우편함(link.mobimml.com)은 8자 코드만 알면 넣어 둔 것을 **한 번** 내준다. 예전에는 그것이 터널 주소
평문이었다 — 코드를 어깨너머로 본 사람·주운 사람이 PC 의 문 위치를 알았다. 이제는 봉투를 넣는다.
봉투는 **인증키를 아는 폰만** 연다. 코드만 가진 사람은 주소를 못 본다. 우편함 서버도 못 본다.

## 봉투 모양 (v1) — 폰 쪽(`ui/seal.js`)과 **바이트까지 같아야 한다**

    봉투 글자 = "mw1." + base64url(N ‖ C ‖ T)          (= 없는 base64url)

    N  = 16바이트 난수 (봉투마다 새로)
    M  = PBKDF2-HMAC-SHA256(비밀번호 = 인증키(정리한 모양, ASCII),
                            소금     = "mobiworks-mailbox-v1" ‖ N,
                            반복     = 600,000,  길이 32)
    Ke = HMAC-SHA256(M, "enc")          Km = HMAC-SHA256(M, "mac")
    흐름 i번째 32바이트 = HMAC-SHA256(Ke, N ‖ i(4바이트 big-endian)),  i = 0, 1, 2, …
    C  = P XOR 흐름[:len(P)]             (P = 알맹이 JSON, UTF-8)
    T  = HMAC-SHA256(Km, "mobiworks-mailbox-v1" ‖ N ‖ C)   (32바이트 전부)

    알맹이 P = {"v":1, "app":"mobiworks", "url":"https://…", "iat": 봉한 시각(유닉스 초)}

**암호화한 뒤 MAC** (encrypt-then-MAC). 여는 쪽은 T 를 **먼저** 상수 시간으로 재고, 맞을 때만 푼다 —
틀린 키로는 주소 한 글자도 안 나온다. 표준 라이브러리에 AES 가 없어 HMAC 카운터 흐름을 쓴다
(HMAC-SHA256 을 PRF 로 쓰는 CTR — 봉투마다 N 이 새로우므로 흐름이 겹치지 않는다).

### 왜 HKDF 가 아니라 PBKDF2 인가

봉투를 주운 사람은 **자기 컴퓨터에서** 인증키를 넣어 보며 T 가 맞는지 잴 수 있다 (오프라인 추측).
발행 키는 32^8 ≈ 2^40 이라, 한 번 재는 데 HMAC 몇 번이면 그래픽카드로 몇 시간 안에 **키 자체가 나온다** —
주소만 새던 것이 키까지 새는 것이 된다. 그래서 키에서 M 을 뽑는 데 60만 번을 돌린다
(PC 0.2초 · 폰 1초 안쪽, 한 번 잇는 데만 든다). 소금에 N 을 섞어 봉투마다 따로 깨야 한다.

### 판 올리기

형식이 바뀌면 앞머리를 `mw2.` 로 바꾸고 라벨도 바꾼다. 옛 폰 화면은 `mw1.` 이 아닌 것을
「형식을 모름」으로 **깔끔히 거절**한다 (주소를 짐작해 쓰지 않는다).
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import time

PREFIX = "mw1."
LABEL = b"mobiworks-mailbox-v1"
ITER = 600_000
NONCE_LEN = 16
TAG_LEN = 32
APP = "mobiworks"
# 우편함 워커가 받는 봉투 글자 길이 상한과 맞춘다 (`MobiFolio_LINK/worker.js` 의 SEALED_RX — "mw1." + 64~436자).
MAX_CHARS = 440


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _keys(key: str, nonce: bytes) -> tuple:
    m = hashlib.pbkdf2_hmac("sha256", key.encode("ascii"), LABEL + nonce, ITER, 32)
    return (hmac.new(m, b"enc", hashlib.sha256).digest(),
            hmac.new(m, b"mac", hashlib.sha256).digest())


def _stream(ke: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(ke, nonce + struct.pack(">I", i), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def seal_bytes(key: str, plain: bytes, nonce: bytes | None = None) -> str:
    """P 를 봉한다. `nonce` 는 **검사용 벡터에서만** 넘긴다 — 평소에는 늘 새 난수다."""
    if not key or not key.isascii():
        raise ValueError("인증키가 없습니다")
    n = os.urandom(NONCE_LEN) if nonce is None else nonce
    if len(n) != NONCE_LEN:
        raise ValueError("nonce 길이")
    ke, km = _keys(key, n)
    c = bytes(a ^ b for a, b in zip(plain, _stream(ke, n, len(plain))))
    t = hmac.new(km, LABEL + n + c, hashlib.sha256).digest()
    return PREFIX + _b64u(n + c + t)


def open_bytes(key: str, sealed: str) -> bytes | None:
    """봉투를 연다. 키가 틀렸거나 형식이 다르거나 한 글자라도 바뀌었으면 None (까닭은 말하지 않는다)."""
    if not isinstance(sealed, str) or not sealed.startswith(PREFIX) or not key or not key.isascii():
        return None
    try:
        raw = _unb64u(sealed[len(PREFIX):])
    except Exception:
        return None
    if len(raw) < NONCE_LEN + TAG_LEN + 1:
        return None
    n, c, t = raw[:NONCE_LEN], raw[NONCE_LEN:-TAG_LEN], raw[-TAG_LEN:]
    ke, km = _keys(key, n)
    if not hmac.compare_digest(hmac.new(km, LABEL + n + c, hashlib.sha256).digest(), t):
        return None                       # **MAC 먼저** — 틀린 키로는 풀어 보지도 않는다
    return bytes(a ^ b for a, b in zip(c, _stream(ke, n, len(c))))


def seal_url(key: str, url: str, now: float | None = None) -> str:
    """터널 주소를 우편함에 넣을 봉투로."""
    p = {"v": 1, "app": APP, "url": url, "iat": int(time.time() if now is None else now)}
    s = seal_bytes(key, json.dumps(p, separators=(",", ":")).encode("utf-8"))
    if len(s) > MAX_CHARS:
        raise ValueError("주소가 너무 깁니다")
    return s
