# -*- coding: utf-8 -*-
"""자료 폴더 이름 한 곳 — 이름이 바뀌었을 때 **옛 폴더를 이사**시킨다.

`server.py` 와 `store.py` 가 **각자** 폴더를 계산한다 (server 는 로그 파일을 열어야 해서
`store` 를 import 하기 전에 경로가 필요하다). 규칙이 두 곳에 흩어지면 반드시 어긋나므로
여기 한 곳에 둔다.

`MobiWorkrs` → `MobiWorks` 로 이름을 바꿨다 (한글 「모비웍스」와
맞추고, 받는 사람이 오타를 내지 않게). 이름만 바꾸면 이미 쌓인 것이 통째로 안 보이므로
첫 실행 때 한 번 옮긴다: 레시피 DB(수 MB) · 큐 · 설정 · 캐시.
"""
import os

APP_DIR = "MobiWorks"
LEGACY_DIRS = ("MobiWorkrs",)     # 옛 이름. 새 폴더가 없을 때만 본다


def migrate(parent: str, name: str = APP_DIR) -> str:
    """`parent/name` 이 없고 옛 이름 폴더가 있으면 옮긴다. 돌려주는 것: **쓸 폴더 경로**.

    **복사가 아니라 이름 바꾸기다.** 레시피 DB 가 수 MB 라 복사는 느리고, 중간에 끊기면
    반쪽이 남는다. 이름 바꾸기는 한 번에 끝난다.

    실패하면 **새 폴더로 그냥 시작한다.** 옛 폴더는 손대지 않고 그 자리에 남는다 —
    지우지 않는다. (옛 앱이 아직 떠 있어 폴더가 잠겨 있을 수 있다.)
    """
    new = os.path.join(parent, name)
    if os.path.isdir(new):
        return new                      # 이미 새 이름으로 쓰고 있다 — 건드리지 않는다
    for old in LEGACY_DIRS:
        if old == name:
            continue
        src = os.path.join(parent, old)
        if not os.path.isdir(src):
            continue
        try:
            os.rename(src, new)
        except OSError:
            pass                        # 잠겨 있으면 새 폴더로 시작 (옛 자료는 남는다)
        break
    return new
