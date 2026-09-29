# -*- coding: utf-8 -*-
"""실행 방식 한 곳 — **PyInstaller exe** · **임베디드 파이썬(배포 시험)** · **개발 소스(run.cmd)**.

임베디드 판: 사용자가 띄우는 것은 우리가 만든 exe 가 아니라 **Python Software
Foundation 이 서명한 `pythonw.exe`** 이고, 그것이 우리 `.py` 소스를 그대로 돌린다 (`tools/build_embed.py`).
PyInstaller onefile exe 가 Defender ML 판정(Wacatac)에 걸려서 시험하는 길이다.

이 판은 **배포판처럼** 굴어야 한다 — 경량판 창·`%LOCALAPPDATA%\\MobiWorks` 자료·개발용 환경변수 무시·
https 만 받는 업데이트. 그러나 `sys.frozen` 을 흉내 내지는 않는다: `sys.frozen`·`sys._MEIPASS`·
`sys.executable` 에 기대는 자리(묶인 리소스 경로, 실행 중인 exe 를 바꿔 끼우는 자동 업데이트, 부트로더
임시 폴더 정리)는 PyInstaller 만의 것이라, 흉내 내면 **`pythonw.exe` 를 새 exe 로 덮어쓴다.**

그래서 스위치를 따로 둔다: 실행기(`MobiWorks.cmd`)가 `MOBIW_EMBED=1` 을 넣고 띄운다.
**변수 없이도 안다** (1.0.1) — 시작 메뉴 바로가기(.lnk)는 환경변수를 실을 수 없다. 그래서 zip 의 짜임 자체를
본다: `sys.executable` 이 `…\\python\\pythonw.exe` 이고, 그 옆 `app\\` 이 이 파일이 있는 폴더이며, 거기에
`build_embed.py` 가 남긴 표지 파일 `EMBED` 가 있으면 임베디드 판이다 (`layout_embed`).
  · FROZEN  = PyInstaller exe 안인가 (지금까지의 뜻 그대로)
  · EMBED   = 임베디드 판인가 (exe 안이면 늘 False — 두 판은 겹치지 않는다)
  · RELEASE = 배포판인가 (둘 중 하나) — 개발용 환경변수를 무시하고, 경량판으로 뜬다

**함수로도 둔다** — 검사가 `sys.frozen`·환경변수를 바꿔 가며 부르는 자리(store.user_base 등)는 부를 때
다시 재야 한다. 모듈 값은 들여올 때 한 번 잰 것이다.
"""
import os
import sys

EMBED_ENV = "MOBIW_EMBED"
MARKER = "EMBED"                     # build_embed.py 가 app\ 에 남기는 표지 파일
HERE = os.path.dirname(os.path.abspath(__file__))


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def layout_embed(executable: str | None = None, app_dir: str | None = None) -> bool:
    """zip 을 푼 짜임인가 — `<폴더>\\python\\pythonw.exe` + `<폴더>\\app\\`(= 이 파일이 있는 곳) + `app\\EMBED`.

    파이썬 이름·폴더 이름·표지 파일이 **모두** 맞아야 한다. 개발 저장소(`Python312\\python.exe` + 저장소 뿌리)나
    exe 는 어느 하나에서 걸러진다 — 표지 파일은 저장소에 없다(빌드할 때 작업 폴더에만 쓴다)."""
    exe = os.path.abspath(executable or sys.executable or "")
    app = os.path.abspath(app_dir or HERE)
    if os.path.basename(exe).lower() not in ("pythonw.exe", "python.exe"):
        return False
    pydir = os.path.dirname(exe)
    if os.path.basename(pydir).lower() != "python":
        return False
    want = os.path.join(os.path.dirname(pydir), "app")
    if os.path.normcase(os.path.normpath(want)) != os.path.normcase(os.path.normpath(app)):
        return False
    return os.path.isfile(os.path.join(app, MARKER)) and os.path.isfile(os.path.join(app, "server.py"))


def embed() -> bool:
    return (not frozen()) and (os.environ.get(EMBED_ENV) == "1" or layout_embed())


def release() -> bool:
    return frozen() or embed()


FROZEN = frozen()
EMBED = embed()
RELEASE = FROZEN or EMBED
