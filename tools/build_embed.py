# -*- coding: utf-8 -*-
r"""임베디드 파이썬 배포 시험판 zip 만들기 — 우리 exe 없이, **PSF 가 서명한 pythonw.exe** 가 우리 .py 를 돌린다.

왜: PyInstaller onefile exe 가 Defender ML 판정(Trojan:Win32/Wacatac.B!ml)에 걸린다 (부트로더를 직접 빌드해도
같았다). 서명 안 된 **우리 exe** 를 아예 없애 보는 시험이다. 사용자가 띄우는 것은 python.org 의 공식 임베디드
패키지에 든 `pythonw.exe`(Python Software Foundation 서명)이고, 우리 것은 평문 `.py` 와 화면 파일뿐이다.

만드는 것 (release\MobiWorks_Beta-<VERSION>.zip — 1.0.1 부터 이것이 배포판이다):

    MobiWorks\
      MobiWorks.cmd      실행기 — MOBIW_EMBED=1 을 넣고 python\pythonw.exe app\server.py 를 띄운다
      python\            python.org 임베디드 패키지 (SHA256 고정) + tkinter 조각 (_tkinter.pyd·tcl86t.dll·tk86t.dll·zlib1.dll·
                         tkinter\·tcl\) — 임베디드 패키지에는 tkinter 가 없는데 게임 오버레이·연출이 쓴다
        python312._pth   ..\app 을 경로에 넣는다. `import site` 는 **넣지 않는다** (.pth 코드 실행 길을 닫는다)
      app\               앱 묶음 (이 목록이 원본이다): 뿌리 *.py(release.py 뺌) · folio\ · ui\ ·
                         data\recipes.seed.json · vendor\WebView2Loader.dll
        EMBED            표지 파일 — 이것이 있으면 환경변수 없이도 임베디드 판으로 안다 (runmode.layout_embed).
                         시작 메뉴 바로가기(.lnk)는 환경변수를 못 실어서 필요하다 (shortcut.py)

tkinter 조각은 **이 스크립트를 돌리는 파이썬**(같은 3.12.10 공식 설치판 — `sys.base_prefix` 로 찾는다,
`--tk-base` 로 바꿀 수 있다)에서 가져온다. 섞이지 않게
python312.dll 의 SHA256 이 임베디드 패키지의 것과 같아야 진행한다 (같은 빌드라는 증거).

끝에 zip 안의 모든 .exe·.dll·.pyd 의 Authenticode 서명을 PowerShell `Get-AuthenticodeSignature` 로 확인한다.
Valid 가 아니거나 서명자가 PSF·Microsoft 가 아니면 실패한다. 우리 파일은 전부 .py·화면 파일이다.

같은 커밋 · 같은 파이썬이면 같은 zip 이 나온다 (항목 순서·시각·권한 고정, SOURCE_DATE_EPOCH 로 시각을 바꿀 수 있다).

쓰는 법:
    python tools\build_embed.py [--work DIR] [--cache DIR] [--out-dir DIR] [--tk-base DIR] [--no-verify-signatures]

**CLI 를 부르지 않는다** — 파일을 모으고 묶기만 한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PY_VERSION = "3.12.10"
EMBED_NAME = f"python-{PY_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{EMBED_NAME}"
# python.org 의 .asc(Steve Dower, Python Release Signing — 7ED1 0B65 31D7 C8E1 BC29 6021 FC62 4643 4870 34E5)로
# 서명을 확인한 파일의 해시. 바뀌면 받지 않는다.
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
PTH_NAME = "python312._pth"

TOP = "MobiWorks"
LAUNCHER = "MobiWorks.cmd"
MARKER = "EMBED"             # runmode.MARKER 와 같아야 한다 (tests/test_embed_mode.py 가 대조한다)
RELEASE_NAME = "MobiWorks_Beta"

# tkinter 조각 — 원본 설치판(Python312\) 기준 상대 경로
TK_FILES = ("DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "DLLs/zlib1.dll")   # zlib1 은 tcl86t 가 부른다
TK_PACKAGE = "Lib/tkinter"
# tcl\ 에서 tkinter 가 실제로 읽는 것만 (dde·reg 확장, 사라진 tix, 빌드용 .lib·.sh 는 뺀다)
TCL_DIRS = ("tcl8", "tcl8.6", "tk8.6")

# 앱 묶음 — 배포판에 실리는 것은 이 목록이 원본이다
APP_EXCLUDE_ROOT_PY = {"release.py"}          # 개발 도구
APP_DIRS = ("folio", "ui")
APP_FILES = ("data/recipes.seed.json", "vendor/WebView2Loader.dll", "vendor/LICENSE-WebView2.txt")
SKIP_DIRS = {"__pycache__"}
SKIP_EXT = {".pyc", ".pyo"}

ALLOWED_SIGNERS = ("CN=Python Software Foundation,", "CN=Microsoft Corporation,",
                   "CN=Microsoft Windows Software Compatibility Publisher,")

FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def pth_text() -> str:
    """python312._pth — 표준 두 줄 + 앱 폴더. `import site` 는 넣지 않는다 (site-packages·.pth 를 안 읽는다)."""
    return "\r\n".join([
        "python312.zip",
        ".",
        r"..\app",
        "# MobiWorks: app sources live in ..\\app (tools/build_embed.py).",
        "# 'import site' is intentionally absent: no site-packages, no .pth code.",
        "",
    ])


def launcher_text() -> str:
    """실행기 (.cmd, ASCII·CRLF). 창 없는 pythonw 를 띄우고 곧바로 끝난다 — 콘솔 창은 잠깐 떴다 닫힌다.

    `MOBIW_EMBED=1` 이 이 판을 「배포판(경량판)」으로 만든다 (runmode.py). `start` 로 띄우므로 이 cmd 창은
    기다리지 않고 닫힌다. 경로에 공백·한글이 있어도 되도록 `%~dp0` 를 따옴표로 감싼다."""
    return "\r\n".join([
        "@echo off",
        "rem MobiWorks (embedded Python trial). Runs app\\server.py with the Python Software Foundation-signed",
        "rem python\\pythonw.exe - no custom exe. ASCII only. See tools/build_embed.py in the source repo.",
        "setlocal",
        'set "MOBIW_EMBED=1"',
        'start "" "%~dp0python\\pythonw.exe" "%~dp0app\\server.py"',
        "endlocal",
        "",
    ])


def app_version() -> str:
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    m = re.search(r'(?m)^VERSION = "([^"]+)"', src)
    if not m:
        raise SystemExit("server.py 에서 VERSION 을 찾지 못했습니다.")
    return m.group(1)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_embed(cache: str) -> str:
    os.makedirs(cache, exist_ok=True)
    dst = os.path.join(cache, EMBED_NAME)
    if not (os.path.isfile(dst) and sha256(dst) == EMBED_SHA256):
        print(f"[embed] 받는 중: {EMBED_URL}")
        part = dst + ".part"
        with urllib.request.urlopen(EMBED_URL, timeout=60) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        got = sha256(part)
        if got != EMBED_SHA256:
            os.remove(part)
            raise SystemExit(f"임베디드 패키지 SHA256 이 다릅니다: {got} (기대 {EMBED_SHA256})")
        os.replace(part, dst)
    print(f"[embed] {EMBED_NAME} sha256 {EMBED_SHA256} 확인")
    return dst


def app_files(root: str = ROOT) -> list[str]:
    """zip 의 app\\ 에 들어갈 파일 (저장소 기준 상대 경로, / 구분, 정렬)."""
    out = [n for n in os.listdir(root)
           if n.endswith(".py") and os.path.isfile(os.path.join(root, n)) and n not in APP_EXCLUDE_ROOT_PY]
    for d in APP_DIRS:
        for base, dirs, files in os.walk(os.path.join(root, d)):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for f in files:
                if os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                out.append(os.path.relpath(os.path.join(base, f), root).replace(os.sep, "/"))
    for f in APP_FILES:
        if not os.path.isfile(os.path.join(root, f)):
            raise SystemExit(f"없는 파일: {f}")
        out.append(f)
    return sorted(set(out))


def _copy(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def marker_text() -> str:
    """app\\EMBED — 내용은 사람을 위한 설명뿐이다. 있는지만 본다 (runmode.layout_embed)."""
    return "MobiWorks embedded-Python layout marker (tools/build_embed.py). Do not delete.\r\n"


def zip_name(ver: str) -> str:
    return f"{RELEASE_NAME}-{ver}.zip"


def tk_source(override: str | None = None) -> str:
    """tkinter 조각을 가져올 설치판 — 이 스크립트를 돌리는 파이썬(`sys.base_prefix`). 판이 같아야 한다.
    `override` 는 다른 설치판을 가리킬 때 — 그때도 python312.dll 해시가 같아야 한다 (build_layout 이 본다)."""
    base = override or sys.base_prefix
    for rel in TK_FILES + (TK_PACKAGE, "python312.dll"):
        if not os.path.exists(os.path.join(base, rel)):
            raise SystemExit(f"tkinter 를 가져올 설치판({base})에 {rel} 이 없습니다 — tcl/tk 를 넣어 설치한 공식 {PY_VERSION} 가 필요합니다.")
    if override:
        return base
    if platform_version() != PY_VERSION:
        raise SystemExit(f"이 스크립트는 파이썬 {PY_VERSION} 로 돌려야 합니다 (지금 {platform_version()}) — tkinter 조각을 같은 판에서 가져온다.")
    return base


def platform_version() -> str:
    return "%d.%d.%d" % sys.version_info[:3]


def build_layout(work: str, embed_zip: str, tk_base: str) -> str:
    top = os.path.join(work, TOP)
    if os.path.exists(top):
        shutil.rmtree(top)
    py = os.path.join(top, "python")
    os.makedirs(py)
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(py)
    # 같은 빌드인지: python312.dll 이 같아야 tkinter 조각을 섞어도 된다
    a, b = sha256(os.path.join(py, "python312.dll")), sha256(os.path.join(tk_base, "python312.dll"))
    if a != b:
        raise SystemExit(f"tkinter 를 가져올 설치판({tk_base})의 python312.dll 이 임베디드 패키지와 다릅니다 — 같은 {PY_VERSION} 공식 설치판이 필요합니다.")
    for rel in TK_FILES:
        _copy(os.path.join(tk_base, rel), os.path.join(py, os.path.basename(rel)))
    shutil.copytree(os.path.join(tk_base, TK_PACKAGE), os.path.join(py, "tkinter"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for d in TCL_DIRS:
        shutil.copytree(os.path.join(tk_base, "tcl", d), os.path.join(py, "tcl", d),
                        ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(py, PTH_NAME), "w", encoding="ascii", newline="") as f:
        f.write(pth_text())
    app = os.path.join(top, "app")
    for rel in app_files():
        _copy(os.path.join(ROOT, rel), os.path.join(app, rel.replace("/", os.sep)))
    with open(os.path.join(app, MARKER), "w", encoding="ascii", newline="") as f:
        f.write(marker_text())
    with open(os.path.join(top, LAUNCHER), "w", encoding="ascii", newline="") as f:
        f.write(launcher_text())
    return top


def verify_signatures(top: str) -> list[dict]:
    """모든 .exe·.dll·.pyd 의 Authenticode 서명 (PowerShell). 하나라도 Valid 가 아니거나 서명자가 허용 목록 밖이면 실패."""
    ps = ("$ErrorActionPreference='Stop'; "
          "Get-ChildItem -LiteralPath $env:EMB_TOP -Recurse -File | "
          "Where-Object { $_.Extension -in '.exe','.dll','.pyd' } | ForEach-Object { "
          "$s = Get-AuthenticodeSignature -LiteralPath $_.FullName; "
          "[pscustomobject]@{ path = $_.FullName; status = [string]$s.Status; "
          "signer = $(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' }) } } | ConvertTo-Json -Compress")
    env = dict(os.environ, EMB_TOP=top)
    # GitHub 러너는 pwsh(7) 에서 이 스크립트를 부른다 — 그 PSModulePath 를 물려받으면 Windows PowerShell 5.1 이
    # 7 용 Microsoft.PowerShell.Security 를 읽으려다 실패한다 (CI 실측). 5.1 이 제 기본 경로를 쓰게 지운다.
    env.pop("PSModulePath", None)
    # pwsh(7) 가 있으면 그쪽을 쓴다 — 러너에서는 5.1 이 보안 모듈을 못 읽는다(PSModulePath 를 지워도 같았다, CI 실측)
    import shutil
    shell = shutil.which("pwsh") or "powershell"
    r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"서명 확인 실패: {r.stderr.strip()[:400]}")
    rows = json.loads(r.stdout or "[]")
    if isinstance(rows, dict):
        rows = [rows]
    bad = []
    for row in rows:
        row["path"] = os.path.relpath(row["path"], top)
        ok = row["status"] == "Valid" and row["signer"].startswith(ALLOWED_SIGNERS)
        row["ok"] = ok
        if not ok:
            bad.append(row)
    rows.sort(key=lambda x: x["path"].lower())
    for row in rows:
        who = row["signer"].split(",")[0].replace("CN=", "")
        print(f"  {'OK ' if row['ok'] else 'BAD'} {row['status']:<8} {who:<55} {row['path']}")
    if bad:
        raise SystemExit(f"서명이 없거나 허용되지 않은 파일 {len(bad)}개: " + ", ".join(b["path"] for b in bad))
    return rows


def _zip_time() -> tuple:
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    if sde and sde.isdigit():
        return time.gmtime(int(sde))[:6]
    return FIXED_TIME


def make_zip(top: str, out: str) -> None:
    """항목 순서·시각·권한을 고정해 같은 입력이면 같은 zip 이 나오게 한다."""
    base = os.path.dirname(top)
    entries = []
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in files:
            entries.append(os.path.join(d, f))
    entries.sort(key=lambda p: os.path.relpath(p, base).replace(os.sep, "/"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    part = out + ".part"
    when = _zip_time()
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in entries:
            arc = os.path.relpath(p, base).replace(os.sep, "/")
            zi = zipfile.ZipInfo(arc, date_time=when)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o100644 << 16
            zi.create_system = 0
            with open(p, "rb") as f:
                z.writestr(zi, f.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    os.replace(part, out)


def tree_size(top: str) -> int:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(top) for f in fs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="임베디드 파이썬 배포 시험판 zip")
    ap.add_argument("--work", default=os.path.join(ROOT, "build", "embed"), help="펼칠 작업 폴더")
    ap.add_argument("--cache", default=os.path.join(ROOT, "build", "embed-cache"), help="받은 임베디드 패키지를 둘 곳")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "release"), help="zip 을 둘 곳")
    ap.add_argument("--tk-base", default=None, help="tkinter 조각을 가져올 파이썬 설치 폴더 (기본: 이 파이썬의 sys.base_prefix)")
    ap.add_argument("--no-verify-signatures", action="store_true", help="Authenticode 확인을 건너뛴다 (Windows 가 아닐 때만)")
    a = ap.parse_args(argv)
    ver = app_version()
    tk_base = tk_source(a.tk_base)
    embed_zip = fetch_embed(a.cache)
    top = build_layout(a.work, embed_zip, tk_base)
    if not a.no_verify_signatures:
        print("[embed] 서명 확인 (.exe·.dll·.pyd)")
        verify_signatures(top)
    out = os.path.join(a.out_dir, zip_name(ver))
    make_zip(top, out)
    print(f"[embed] 펼친 크기 {tree_size(top) / 1e6:.1f} MB · {top}")
    print(f"[embed] zip {os.path.getsize(out) / 1e6:.1f} MB · sha256 {sha256(out)}")
    print(f"[embed] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
