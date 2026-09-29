# -*- coding: utf-8 -*-
r"""임베디드 판(zip)의 제자리 업데이트 (1.0.2) — 새 zip 을 받아 **다시 내려받기 없이** 판을 바꾼다.

1.0.1 까지 임베디드 판의 「업데이트」는 받는 곳 페이지만 열었다 (사용자가 새 zip 을 받아 폴더를 바꿨다).
1.0.2 부터는 `latest.json` 의 `zip` 칸(주소·SHA256·크기)을 보고 앱이 직접 받는다. 흐름 (server.update_apply):

    받기   `<설치 폴더>\.update\` 에 받는다 — https 만 · 크기 상한 · SHA256 대조
    검사   zip 짜임(`MobiWorks\` 아래 `python\`·`app\`·`app\EMBED`·`MobiWorks.cmd`)과 이름(절대 경로·`..`·드라이브
           글자 = zip-slip 거절), 풀린 판의 VERSION 이 latest.json 과 같은가, **모든 .exe·.dll·.pyd 의 Authenticode 가
           Valid 이고 서명자가 PSF·Microsoft 인가** (tools/build_embed.py 와 같은 허용 목록 — WinVerifyTrust 를 ctypes 로)
    바꾸기 `python\` 이 같으면(대부분) 이 프로세스가 `app\` 만 바꾼다: 지금 `app` → `.update\app.old-<옛판>`,
           풀린 `app` → 제자리. 새 판을 새 포트로 띄워 응답을 본 뒤 자리를 넘긴다 (exe 판과 같은 길).
           `python\` 이 다르거나 `app\` 이 잡혀 이름을 못 바꾸면 **작은 도우미**가 우리가 끝난 뒤에 바꾼다 —
           실행 중인 `pythonw.exe` 가 `python\` 을 잡고 있기 때문이다 (`helper_main`).
    치우기 새 판이 뜬 뒤 `.update\` 를 지운다 (server._cleanup_update).

**반쯤 바뀐 폴더를 남기지 않는다** — 바꾸다 실패하면 바꾼 것을 되돌리고, 새 판이 응답하지 않으면 옛 판을 되살린다.

도우미는 이 파일을 그대로 `.update\swap.py` 로 복사해 돌린다 (표준 라이브러리만 쓴다). 임베디드 파이썬의
`._pth` 는 스크립트 폴더를 경로에 넣지 않으므로 우리 모듈을 들여오지 않는다.

**CLI 를 부르지 않는다** — 파일을 받고 옮기기만 한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

TOP = "MobiWorks"                       # tools/build_embed.py 의 TOP 과 같아야 한다
UPDATE_DIRNAME = ".update"
MAX_ZIP_BYTES = 100 * 1024 * 1024       # 받는 zip 상한 (1.0.2 는 약 25 MB)
MAX_UNPACKED_BYTES = 400 * 1024 * 1024  # 풀린 크기 상한 — 작은 zip 이 수 GB 로 풀리는 것을 막는다
BINARY_EXT = (".exe", ".dll", ".pyd")
# build_embed.ALLOWED_SIGNERS 와 같은 사람들 (저쪽은 PowerShell 의 Subject 앞머리, 여기는 CN 값)
ALLOWED_SIGNERS = ("Python Software Foundation", "Microsoft Corporation",
                   "Microsoft Windows Software Compatibility Publisher")
# 바꿔 끼우는 것 — 설치 폴더 바로 아래 이름. 자료는 %LOCALAPPDATA%\MobiWorks 에 있어 건드리지 않는다
SWAP_NAMES = ("app", "python", "MobiWorks.cmd")
REQUIRED = ("MobiWorks.cmd", "app/EMBED", "app/server.py", "python/pythonw.exe")
SKIP_DIRS = {"__pycache__"}
SKIP_EXT = {".pyc", ".pyo"}
PID_WAIT = 60.0                         # 도우미가 옛 프로세스를 기다리는 상한(초)
HEALTH_WAIT = 30.0                      # 새 판의 응답을 기다리는 상한(초)


class UpdateError(Exception):
    """사용자에게 보일 한국어 문구와 화면·로그용 오류 이름."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def update_dir(root: str) -> str:
    return os.path.join(root, UPDATE_DIRNAME)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log(path: str, msg: str) -> None:
    """update.log 한 줄 — 실패해도 업데이트를 멈추지 않는다."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ── zip 검사 ──
def check_members(infos: list) -> list:
    """zip 항목 이름·짜임 검사. 통과한 파일 항목(ZipInfo) 목록을 돌려주고, 아니면 UpdateError.

    zip-slip: 이름은 늘 `MobiWorks/` 로 시작하는 상대 경로여야 한다 — 절대 경로(`/`·`\\`), 드라이브 글자(`C:`),
    `..`·`.` 조각, 역슬래시, 심볼릭 링크를 거절한다. 맨 위에는 `python/`·`app/`·`MobiWorks.cmd` 만 있어야 한다."""
    files, total, seen = [], 0, set()
    for zi in infos:
        name = zi.filename
        if not name or "\\" in name or ":" in name or name.startswith("/") or "\0" in name:
            raise UpdateError("bad_zip", f"zip 안의 이름이 이상합니다: {name!r}")
        parts = name.rstrip("/").split("/")
        if parts[0] != TOP or any(p in ("", ".", "..") for p in parts):
            raise UpdateError("bad_zip", f"zip 안의 경로가 {TOP}\\ 밖을 가리킵니다: {name!r}")
        if len(parts) >= 2 and parts[1] not in SWAP_NAMES:
            raise UpdateError("bad_zip", f"zip 에 모르는 항목이 있습니다: {name!r}")
        if len(parts) == 2 and parts[1] == "MobiWorks.cmd" and name.endswith("/"):
            raise UpdateError("bad_zip", "MobiWorks.cmd 가 폴더입니다.")
        if ((zi.external_attr >> 16) & 0o170000) == 0o120000:
            raise UpdateError("bad_zip", f"zip 에 링크가 있습니다: {name!r}")
        if name.endswith("/"):
            continue
        total += zi.file_size
        if total > MAX_UNPACKED_BYTES:
            raise UpdateError("bad_zip", "zip 을 풀면 너무 큽니다.")
        seen.add("/".join(parts[1:]))
        files.append(zi)
    missing = [r for r in REQUIRED if r not in seen]
    if missing:
        raise UpdateError("bad_zip", "모비웍스 zip 이 아닙니다 (없는 것: " + ", ".join(missing) + ").")
    return files


def extract(zip_path: str, dest: str) -> str:
    """검사를 통과한 zip 을 `dest` 에 푼다. 풀린 판의 뿌리(`dest\\MobiWorks`)를 돌려준다.
    이름 검사에 더해 풀 자리가 정말 `dest` 안인지 한 번 더 본다."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    base = os.path.realpath(dest)
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise UpdateError("bad_zip", f"zip 을 열지 못했습니다: {type(e).__name__}")
    with z:
        for zi in check_members(z.infolist()):
            out = os.path.realpath(os.path.join(base, *zi.filename.split("/")))
            if os.path.commonpath([base, out]) != base:
                raise UpdateError("bad_zip", f"zip 안의 경로가 풀 곳 밖을 가리킵니다: {zi.filename!r}")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(zi) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return os.path.join(dest, TOP)


def staged_version(stage: str) -> str:
    try:
        with open(os.path.join(stage, "app", "server.py"), encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return ""
    m = re.search(r'(?m)^VERSION = "([^"]+)"', src)
    return m.group(1) if m else ""


# ── Authenticode (WinVerifyTrust) ──
def authenticode(path: str) -> tuple:
    """(상태, 서명자 CN) — 상태는 "Valid" 이거나 실패 사유. PowerShell 을 띄우지 않는다 (shortcut.py 와 같은 까닭:
    앱이 숨은 PowerShell 을 띄우는 것은 백신 휴리스틱이 싫어한다). 폐기 목록은 조회하지 않는다 (밖에 나가지 않게)."""
    import ctypes
    from ctypes import wintypes as w

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    class FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", w.DWORD), ("pcwszFilePath", w.LPCWSTR), ("hFile", w.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class TRUST_DATA(ctypes.Structure):
        _fields_ = [("cbStruct", w.DWORD), ("pPolicyCallbackData", ctypes.c_void_p), ("pSIPClientData", ctypes.c_void_p),
                    ("dwUIChoice", w.DWORD), ("fdwRevocationChecks", w.DWORD), ("dwUnionChoice", w.DWORD),
                    ("pFile", ctypes.POINTER(FILE_INFO)), ("dwStateAction", w.DWORD), ("hWVTStateData", w.HANDLE),
                    ("pwszURLReference", w.LPCWSTR), ("dwProvFlags", w.DWORD), ("dwUIContext", w.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    # WINTRUST_ACTION_GENERIC_VERIFY_V2 (SoftPub.h) — 공개 상수, 비밀이 아니다
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    wt = ctypes.WinDLL("wintrust")
    c32 = ctypes.WinDLL("crypt32")
    wt.WinVerifyTrust.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.POINTER(TRUST_DATA)]
    wt.WinVerifyTrust.restype = ctypes.c_long
    wt.WTHelperProvDataFromStateData.argtypes = [w.HANDLE]
    wt.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wt.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, w.DWORD, w.BOOL, w.DWORD]
    wt.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
    wt.WTHelperGetProvCertFromChain.argtypes = [ctypes.c_void_p, w.DWORD]
    wt.WTHelperGetProvCertFromChain.restype = ctypes.c_void_p
    c32.CertGetNameStringW.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p, w.LPWSTR, w.DWORD]
    c32.CertGetNameStringW.restype = w.DWORD

    fi = FILE_INFO(ctypes.sizeof(FILE_INFO), os.path.abspath(path), None, None)
    td = TRUST_DATA()
    td.cbStruct = ctypes.sizeof(TRUST_DATA)
    td.dwUIChoice = 2              # WTD_UI_NONE
    td.fdwRevocationChecks = 0     # WTD_REVOKE_NONE
    td.dwUnionChoice = 1           # WTD_CHOICE_FILE
    td.pFile = ctypes.pointer(fi)
    td.dwStateAction = 1           # WTD_STATEACTION_VERIFY
    td.dwProvFlags = 0x10 | 0x1000  # WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL (밖에 나가지 않는다)
    rc = wt.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(td))
    signer = ""
    try:
        if rc == 0:
            prov = wt.WTHelperProvDataFromStateData(td.hWVTStateData)
            sgnr = wt.WTHelperGetProvSignerFromChain(prov, 0, False, 0) if prov else None
            cert = wt.WTHelperGetProvCertFromChain(sgnr, 0) if sgnr else None
            if cert:
                pcert = ctypes.cast(cert + ctypes.sizeof(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p))[0]  # CRYPT_PROVIDER_CERT.pCert
                oid = ctypes.c_char_p(b"2.5.4.3")   # CN
                buf = ctypes.create_unicode_buffer(512)
                if pcert and c32.CertGetNameStringW(pcert, 3, 0, ctypes.cast(oid, ctypes.c_void_p), buf, 512) > 1:  # CERT_NAME_ATTR_TYPE
                    signer = buf.value
    finally:
        td.dwStateAction = 2       # WTD_STATEACTION_CLOSE
        wt.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(td))
    if rc != 0:
        return ("0x%08X" % (rc & 0xFFFFFFFF), "")
    return ("Valid", signer)


def binaries(stage: str) -> list:
    out = []
    for d, dirs, files in os.walk(stage):
        dirs.sort()
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in BINARY_EXT:
                out.append(os.path.join(d, f))
    return out


def verify_signatures(stage: str, check=None) -> int:
    """풀린 판의 모든 .exe·.dll·.pyd 가 Valid 이고 서명자가 허용 목록에 있어야 한다. 검사한 개수를 돌려준다."""
    check = check or authenticode
    files = binaries(stage)
    if not files:
        raise UpdateError("bad_signature", "새 판에 실행 파일이 없습니다.")
    bad = []
    for p in files:
        try:
            status, signer = check(p)
        except Exception as e:
            status, signer = type(e).__name__, ""
        if status != "Valid" or signer not in ALLOWED_SIGNERS:
            bad.append(f"{os.path.relpath(p, stage)} ({status} {signer})".strip())
    if bad:
        raise UpdateError("bad_signature", "서명이 없거나 허용되지 않은 파일이 있어 적용하지 않았습니다: " + ", ".join(bad[:5]))
    return len(files)


# ── 파이썬이 같은가 ──
def tree_digest(top: str) -> dict:
    """상대 경로 → sha256 (`__pycache__`·.pyc 는 뺀다 — 실행하며 생긴다)."""
    out = {}
    for d, dirs, files in os.walk(top):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in SKIP_EXT:
                continue
            p = os.path.join(d, f)
            out[os.path.relpath(p, top).replace(os.sep, "/").lower()] = sha256_file(p)
    return out


def python_differs(cur_py: str, new_py: str) -> bool:
    return tree_digest(cur_py) != tree_digest(new_py)


# ── 바꿔 끼우기 ──
def old_name(root: str, name: str, old_ver: str) -> str:
    return os.path.join(update_dir(root), f"{name}.old-{old_ver}")


def swap(root: str, stage: str, names, old_ver: str, copy=(), logf: str = "") -> list:
    """`names` 를 차례로 바꾼다: 지금 것 → `.update\\<이름>.old-<옛판>`, 풀린 것 → 제자리.
    `copy` 에 든 이름은 옮기지 않고 복사한다 (도우미가 그 폴더의 파이썬으로 돌고 있을 때).
    하나라도 실패하면 **이미 바꾼 것까지 되돌리고** UpdateError. 성공하면 되돌리기용 목록을 돌려준다."""
    done = []
    try:
        os.makedirs(update_dir(root), exist_ok=True)
        for name in names:
            cur, new, old = os.path.join(root, name), os.path.join(stage, name), old_name(root, name, old_ver)
            if not os.path.exists(new):
                raise UpdateError("bad_zip", f"새 판에 {name} 이 없습니다.")
            if os.path.exists(old):
                _remove(old)
            had = os.path.exists(cur)
            if had:
                os.rename(cur, old)
            try:
                if name in copy:
                    (shutil.copytree if os.path.isdir(new) else shutil.copy2)(new, cur)
                else:
                    os.rename(new, cur)
            except OSError:
                if os.path.exists(cur):
                    _remove(cur)
                if had:
                    os.rename(old, cur)
                raise
            done.append((name, had))
            log(logf, f"swap: {name} 바꿈")
    except (OSError, UpdateError) as e:
        log(logf, f"swap: 실패 ({type(e).__name__}: {e}) — 되돌림")
        unswap(root, done, old_ver, "", logf)
        if isinstance(e, UpdateError):
            raise
        raise UpdateError("swap_failed", f"파일을 바꾸지 못했습니다 (사용 중일 수 있습니다): {e}")
    return done


def unswap(root: str, done: list, old_ver: str, failed_ver: str = "", logf: str = "") -> bool:
    """`swap` 을 거꾸로 — 새 것은 `.update\\failed-<판>-<이름>` 으로 치우고(원인 분석용) 옛것을 제자리로."""
    ok = True
    for name, had in reversed(done):
        cur, old = os.path.join(root, name), old_name(root, name, old_ver)
        try:
            if os.path.exists(cur):
                bad = os.path.join(update_dir(root), f"failed-{failed_ver or 'new'}-{name}")
                if os.path.exists(bad):
                    _remove(bad)
                os.rename(cur, bad)
            if had:
                os.rename(old, cur)
            log(logf, f"swap: {name} 되돌림")
        except OSError as e:
            ok = False
            log(logf, f"swap: {name} 되돌리기 실패 — {e}")
    return ok


def _remove(p: str) -> None:
    if os.path.isdir(p):
        shutil.rmtree(p)
    else:
        os.remove(p)


# ── 띄우기 · 기다리기 ──
DETACHED = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB


def spawn(args: list, cwd: str, env: dict):
    try:
        return subprocess.Popen(args, cwd=cwd, env=env, close_fds=True, creationflags=DETACHED)
    except OSError:
        return subprocess.Popen(args, cwd=cwd, env=env, close_fds=True, creationflags=DETACHED & ~0x01000000)


def health(port: int, timeout: float = 2.0) -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            d = json.loads(r.read(4096).decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def wait_health(port: int, version: str, not_pid: int, timeout: float, proc=None) -> bool:
    """새 판이 `port` 에서 `version` 으로 답할 때까지. `proc` 이 먼저 끝나면(기동 중 죽음) 기다리지 않고 False."""
    end = time.time() + timeout
    while time.time() < end:
        if proc is not None and proc.poll() is not None:
            return False
        d = health(port)
        if d.get("app") == "mobiworks" and d.get("pid") != not_pid and (not version or d.get("version") == version):
            return True
        time.sleep(0.5)
    return False


def wait_pid(pid: int, timeout: float) -> bool:
    """옛 프로세스가 끝날 때까지 기다린다. 넘으면 끝낸다 (exe 판의 옛 부트로더 처리와 같다). 끝났으면 True."""
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000 | 0x0001, False, int(pid))   # SYNCHRONIZE | TERMINATE
    if not h:
        return True                    # 이미 없다
    try:
        if k32.WaitForSingleObject(h, int(timeout * 1000)) == 0:
            return True
        k32.TerminateProcess(h, 0)
        return k32.WaitForSingleObject(h, 10000) == 0
    finally:
        k32.CloseHandle(h)


# ── 도우미 (우리가 끝난 뒤에 바꾼다) ──
def helper_main(argv: list) -> int:
    """`.update\\swap.py <plan.json>` — 옛 프로세스가 끝나기를 기다렸다가 바꾸고, 새 판을 띄워 응답을 본다.
    새 판이 안 뜨면 되돌리고 **옛 판을 같은 포트·토큰으로 다시 띄운다** (화면이 이어서 붙는다)."""
    with open(argv[0], encoding="utf-8") as f:
        plan = json.load(f)
    root, stage, logf = plan["root"], plan["stage"], plan.get("log", "")
    old_ver, new_ver, port = plan["old_version"], plan["new_version"], int(plan["port"])
    env = dict(os.environ)
    env.update(plan.get("env") or {})
    env["MOBIW_UPDATE_HELPER_PID"] = str(os.getpid())
    for k in plan.get("env_drop") or ():
        env.pop(k, None)
    log(logf, f"helper: 시작 pid={os.getpid()} {old_ver} -> {new_ver} ({', '.join(plan['names'])})")
    if not wait_pid(int(plan["old_pid"]), PID_WAIT):
        log(logf, "helper: 옛 프로세스가 끝나지 않았다 — 바꾸지 않는다")
        return 2
    launch = [os.path.join(root, "python", "pythonw.exe"), os.path.join(root, "app", "server.py")]
    try:
        done = swap(root, stage, plan["names"], old_ver, copy=tuple(plan.get("copy") or ()), logf=logf)
    except UpdateError as e:
        log(logf, f"helper: 바꾸지 못함 — {e.message}. 옛 판을 다시 띄운다")
        spawn(launch, root, env)
        return 3
    proc = spawn(launch, root, env)
    if wait_health(port, new_ver, 0, HEALTH_WAIT, proc):
        log(logf, f"helper: {new_ver} 응답 확인 (포트 {port})")
        return 0
    log(logf, f"helper: {new_ver} 가 {int(HEALTH_WAIT)}초 안에 응답하지 않음 — 되돌린다")
    try:
        proc.kill()
        proc.wait(10)
    except Exception:
        pass
    ok = unswap(root, done, old_ver, new_ver, logf)
    spawn(launch, root, env)
    log(logf, "helper: 옛 판을 다시 띄움" if ok else "helper: 되돌리기 실패 — 다시 설치가 필요할 수 있다")
    return 4


if __name__ == "__main__":
    sys.exit(helper_main(sys.argv[1:]))
