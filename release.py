"""릴리스 조립·검증: release/ 에 버전 박힌 zip + latest.json + SHA256SUMS.txt 를 만든다.

**1.0.1 부터 배포판은 임베디드 파이썬 zip 이다.** PyInstaller exe 가
Defender ML 판정에 걸려서, PSF 가 서명한 `pythonw.exe` 가 우리 `.py` 를 돌리는 zip 으로 바꿨다
(`tools/build_embed.py`).

MobiFolio 배포에서 실제로 터진 사고를 막는 가드를 넣었다 (사람이 아니라 스크립트가 검증한다):
- 자리표시 URL 을 그대로 릴리스 → 업데이트 확인이 조용히 죽는다 (에러 없이 "최신입니다")
- 같은 번호로 내용이 다른 재빌드 → 이미 받은 사람이 새 판을 영원히 못 받는다 (0.1.2·0.1.4 에서 두 번 겪음)
- 3자 불일치 (실제 zip ↔ latest.json.sha256 ↔ SHA256SUMS.txt)

**latest.json 의 url 은 zip 이 아니라 받는 곳(`<base>/#download`)이다 — 일부러다.** 아직 1.0.0 exe 를 쓰는
사람의 업데이터는 url 을 받아 sha256 을 맞춰 본 뒤 **실행 중인 exe 자리에 그대로 넣는다**
(`server.update_apply`). url 이 zip 이면 해시가 맞아 exe 가 zip 으로 덮인다 — 앱이 다시 안 뜬다. 받는 곳은
HTML 이라 해시가 안 맞고 아무것도 바꾸지 않는다 (「적용하지 않았습니다」 + notes 의 안내가 남는다).
임베디드 판은 url 을 받지 않는다 — 설정의 업데이트 주소와 같은 사이트의 `/#download` 를 열 뿐이다.

**1.0.2 부터 `zip` 칸을 더한다** — `{"url": "<base>/MobiWorks_Beta-<판>.zip", "sha256": …, "size": …}`. 1.0.2 이상의
임베디드 판은 이것을 받아 제자리에서 바꾼다 (updater.py). 1.0.0 exe 와 1.0.1 은 이 칸을 모른다 — 예전처럼
`url`(받는 곳)·`sha256` 만 보므로 위의 안전은 그대로다. 3자 대조에 `zip.sha256`·`zip.size` 도 넣는다.
zip 은 **latest.json 과 같은 곳(<base>/)에** 올려야 한다 — 올리지 않으면 1.0.2 의 업데이트가 받기에서 실패한다.

사용법: python release.py <base-url> [--force]
        python release.py --stage-only [--force]
  <base-url>    latest.json 이 놓일 곳. 예: https://wk.example.com
  --stage-only  도메인이 아직 없을 때. 버전 박힌 zip 과 SHA256SUMS 만 만들고
                **latest.json 은 만들지 않는다** (거짓 매니페스트를 내보내느니 없는 게 낫다)
  --force       같은 번호 재빌드 거부를 무시한다 (아직 아무에게도 안 나간 판일 때만)

zip 은 먼저 만들어 둔다: `python tools/build_embed.py --out-dir build/embed-out` (release.cmd 가 한다).
여기서 release/ 로 옮기며 검사한다 — 빌드가 release/ 에 바로 쓰면 같은 번호 가드가 비교할 옛 파일이 사라진다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys

# cp949 콘솔에서 한글·기호를 찍다 UnicodeEncodeError 로 죽지 않게 (chcp 를 안 바꾸고 직접 실행해도 돌아야 한다)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
BUILT_DIR = os.path.join(HERE, "build", "embed-out")    # tools/build_embed.py --out-dir 이 쓰는 곳
REL = os.path.join(HERE, "release")
# 배포 파일 이름 — 1.0.1 부터 「MobiWorks_Beta-<버전>.zip」 (tools/build_embed.py 의 zip_name 과 같다)
RELEASE_NAME = "MobiWorks_Beta"
# 1.0.0 exe 가 업데이트를 누르면 받아 가는 곳 — **파일이 아니라 페이지**여야 한다 (머리 주석)
DOWNLOAD_PAGE = "/#download"
NOTES = "1.0.2 부터는 앱 안에서 바로 업데이트돼요 — 1.0.1 이하는 사이트에서 한 번 새로 받아 주세요"


def rel_zip(ver: str) -> str:
    return f"{RELEASE_NAME}-{ver}.zip"


def is_release_zip(name: str) -> bool:
    return name.endswith(".zip") and name.startswith(RELEASE_NAME + "-")


def zip_url(ver: str, base: str) -> str:
    return base.rstrip("/") + "/" + rel_zip(ver)


def manifest(ver: str, base: str, sha: str, size: int) -> dict:
    """latest.json — url 은 받는 곳 페이지 (zip 이 아니다 · 머리 주석). zip 은 `zip` 칸에 (1.0.2 제자리 업데이트)."""
    return {"version": ver, "url": base.rstrip("/") + DOWNLOAD_PAGE, "sha256": sha, "notes": NOTES,
            "zip": {"url": zip_url(ver, base), "sha256": sha, "size": size}}


# 자리표시·로컬 주소를 실수로 릴리스하지 않게 (①)
BAD_HOSTS = ("example.", ".invalid", ".test", ".local", "localhost", "127.0.0.1", "todo", "changeme", "yourdomain")


def die(msg: str) -> None:
    print(f"릴리스 중단: {msg}", file=sys.stderr)
    sys.exit(1)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_control_chars() -> None:
    """코드·스크립트에 **눈에 안 보이는 제어문자**가 들어갔으면 막는다.

    체크리스트에만 적어 두면 건너뛴다 — 실제로 그랬다
    (빌드 스크립트의 가드가 죽어 있는데 「가드가 있다」고 믿고 넘어갔다). 그래서 사람이
    아니라 여기서 본다.

    **사람만 읽는 문서(`.md`)는 막지 않는다** (`--code-only`). 예시가 깨져 보일 뿐이고
    그것 때문에 릴리스를 멈추는 건 과하다. 그 밖에는 전부 막는다 — `.svg`(아이콘 원본)도
    `.gitattributes`(git 이 읽는다)도 기계가 읽는 파일이다."""
    sys.path.insert(0, os.path.join(HERE, "tools"))
    try:
        import textcheck
    except Exception as e:
        print(f"참고: 제어문자 검사를 건너뜁니다 ({type(e).__name__}) — tools/textcheck.py 를 확인하세요.")
        return
    block, warn, _, unread = textcheck.scan(code_only=True)
    if block is None:
        return                      # git 이 없는 곳 — 릴리스를 막을 사유는 아니다
    if block:
        die("\n".join(["기계가 읽는 파일에 제어문자가 있습니다 (눈에 안 보입니다):"]
                      + block + ["", textcheck.HINT]))
    if unread:
        # **「못 읽었다」를 「이상 없다」로 통과시키지 않는다.** UTF-16 으로 저장된 `.py`
        # 하나면 검사한 적 없이 릴리스가 나간다.
        die("\n".join(["기계가 읽는 파일을 **검사하지 못했습니다** — 통과시키지 않습니다:"]
                      + unread
                      + ["", "UTF-8 이 아니면 그 자체가 결함입니다 (UTF-16 `.py` 는 파이썬도 못 읽습니다)."]))


def built_zip(ver: str) -> str:
    return os.path.join(BUILT_DIR, rel_zip(ver))


def copy_zip(src: str, staged: str) -> None:
    """빌드 산출물을 `release/` 로 옮긴다. 잡혀 있으면(압축 풀기 도구·백신 검사 중) 무슨 일인지 말해 준다."""
    try:
        shutil.copy2(src, staged)
    except PermissionError:
        die(f"{os.path.basename(staged)} 이 다른 프로세스에 잡혀 있습니다 — 열어 둔 압축 도구나 탐색기 미리 보기를 닫고 다시 하세요.")


def read_version() -> str:
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    if not m:
        die("server.py 에서 VERSION 을 찾지 못했습니다.")
    return m.group(1)


def check_version_format(ver: str) -> None:
    """VERSION 형식을 본다 — 오타가 나면 zip 이름·latest.json·업데이트 비교에 그대로 퍼진다."""
    if not re.fullmatch(r"\d+(\.\d+){1,3}", ver):
        die(f"VERSION 형식이 이상합니다: {ver!r} — x.y.z 로 적으세요. 오타가 나면 모든 곳에 퍼집니다.")


def check_default_update_url(base: str | None) -> None:
    """**새로 설치한 사람이 업데이트를 받을 수 있는가.**

    `store.DEFAULT_UPDATE_URL` 이 비어 있으면 처음 받은 사람은 설정에서 주소를 손으로
    붙여 넣기 전까지 영원히 업데이트를 못 받는다 — 그리고 알려 줄 방법도 마땅치 않다.
    「조용히 죽는 업데이트」가 제일 나쁘므로 릴리스에서 막는다.

    `base` 가 있으면 **박아 둔 주소가 그 도메인을 가리키는지도** 본다. zip 은 이미 빌드된
    뒤라 여기서 고칠 수 없다 — 어긋나면 상수를 고치고 **다시 빌드**해야 한다."""
    sys.path.insert(0, HERE)
    try:
        import store
    except Exception as e:
        die(f"store 를 읽지 못했습니다: {type(e).__name__}: {e}")
    got = str(getattr(store, "DEFAULT_UPDATE_URL", "") or "").strip()
    if base is None:                      # --stage-only: 매니페스트를 안 만드니 경고만
        if not got:
            print("경고: store.DEFAULT_UPDATE_URL 이 비어 있습니다 — 이 판을 받은 사람은")
            print("      설정에서 주소를 직접 넣기 전까지 자동 업데이트를 못 받습니다.")
            print("      도메인이 정해지면 상수를 채우고 **다시 빌드**해서 릴리스하세요.")
        return
    if not got:
        die("\n".join([
            "store.DEFAULT_UPDATE_URL 이 비어 있습니다.",
            f"  '{base}/latest.json' 을 넣고 **다시 빌드**한 뒤 릴리스하세요.",
            "  (zip 안의 store.py 에 박히는 값이라 릴리스 단계에서는 못 고칩니다.)",
            "  도메인 없이 파일만 만들려면: python release.py --stage-only"]))
    want = f"{base}/latest.json"
    if got.rstrip("/") != want:
        die("\n".join([
            f"store.DEFAULT_UPDATE_URL='{got}' 이 이번 배포 주소와 다릅니다 (기대: '{want}').",
            "  상수를 고치고 다시 빌드하세요 — 이대로 내면 앱이 엉뚱한 곳을 봅니다."]))


def _prepare(force: bool) -> tuple:
    """공통 앞부분 — 버전·자원 검사, 같은 번호 가드, release/ 로 옮기기. (버전, 놓은 경로, sha256) 을 돌려준다."""
    ver = read_version()
    src = built_zip(ver)
    if not os.path.exists(src):
        die(f"{os.path.relpath(src, HERE)} 가 없습니다. 먼저: python tools/build_embed.py --out-dir build/embed-out")
    check_version_format(ver)
    new_sha = sha256_file(src)
    os.makedirs(REL, exist_ok=True)
    staged = os.path.join(REL, rel_zip(ver))
    # ② 같은 번호로 내용이 다른 재빌드 금지 — 제일 아팠던 사고
    if os.path.exists(staged):
        old_sha = sha256_file(staged)
        if old_sha != new_sha and not force:
            die("\n".join([
                f"v{ver} 가 이미 release/ 에 있고 내용이 다릅니다.",
                f"  기존 {old_sha[:16]}…",
                f"  신규 {new_sha[:16]}…",
                "이미 배포된 판이면 받은 사람이 새 판을 구별하지 못합니다. server.py 의 VERSION 을 올리세요.",
                "(아직 아무에게도 안 나갔다면 --force)"]))
        if old_sha == new_sha:
            print(f"참고: v{ver} 는 이미 같은 내용으로 준비돼 있습니다 (재생성).")
    copy_zip(src, staged)
    return ver, staged, new_sha


def _vt(ver: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in ver.split("."))


def site_zips() -> list:
    """사이트에 두는 zip = **새 판 + 직전 한 판** (받는 중에 옛 판이 지워져 404 가 나지 않게). release/ 에 더 오래된 zip 이 쌓여 있어도
    목록에는 넣지 않는다 — 사이트에 없는 파일의 줄이 SHA256SUMS 에 섞이면 안 된다."""
    vers = []
    for x in os.listdir(REL):
        if is_release_zip(x):
            vers.append(x[len(RELEASE_NAME) + 1:-4])
    vers.sort(key=_vt)
    return [rel_zip(v) for v in vers[-2:]]


def write_sums() -> dict:
    """SHA256SUMS: 사이트에 올릴 zip(새 판 + 직전 한 판)만 적고, 적은 이름이 그 목록과 같은지 대조한다."""
    zips = site_zips()
    sums = {x: sha256_file(os.path.join(REL, x)) for x in zips}
    with open(os.path.join(REL, "SHA256SUMS.txt"), "w", encoding="ascii", newline="\n") as f:
        for name in zips:
            f.write(f"{sums[name]}  {name}\n")
    # 대조 — SUMS 의 파일 이름 = 사이트에 놓을 파일 목록 (어긋나면 멈춘다)
    with open(os.path.join(REL, "SHA256SUMS.txt"), encoding="ascii") as f:
        listed = sorted(line.split("  ", 1)[1].strip() for line in f if line.strip())
    if listed != sorted(zips):
        die(f"SHA256SUMS 의 파일 목록 {listed} 이 사이트에 올릴 목록 {sorted(zips)} 과 다릅니다.")
    return sums


def stage(force: bool) -> None:
    """도메인 없이 **올릴 수 있는 것까지만** 만든다 — 버전 박힌 zip 과 SHA256SUMS.

    `latest.json` 은 **일부러 안 만든다.** 주소가 없는 매니페스트는 쓸모가 없고, 자리표시
    주소를 넣으면 그게 그대로 나가 업데이트가 조용히 죽는다 (이 파일 머리 주석의 ①)."""
    check_default_update_url(None)
    check_control_chars()
    ver, staged, new_sha = _prepare(force)
    sums = write_sums()
    if sums[os.path.basename(staged)] != new_sha:
        die("SHA256SUMS 의 값이 실제 zip 과 다릅니다.")
    lj = os.path.join(REL, "latest.json")
    if os.path.exists(lj):
        os.remove(lj)      # 옛 매니페스트가 남아 새 판을 가리키는 척하면 안 된다
    print(f"릴리스 준비 (도메인 없음)  v{ver}")
    print(f"  {staged}  ({os.path.getsize(staged):,} B)")
    print(f"  sha256 {new_sha}")
    print(f"  {os.path.join(REL, 'SHA256SUMS.txt')}")
    print()
    print("도메인이 정해지면: store.py 의 DEFAULT_UPDATE_URL 을 채우고 release.cmd https://<도메인>")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv[1:]
    stage_only = "--stage-only" in sys.argv[1:]
    if stage_only and args:
        die("--stage-only 와 base-url 은 같이 못 씁니다. 도메인이 있으면 그냥 주소만 주세요.")
    if not args and not stage_only:
        die("\n".join([
            "사용법: python release.py <base-url> [--force]   예: https://wk.example.com",
            "       도메인이 아직 없으면: python release.py --stage-only"]))
    if stage_only:
        return stage(force)
    base = args[0].rstrip("/")
    low = base.lower()
    if not low.startswith("https://"):
        die(f"base-url 은 https:// 여야 합니다 (받은 값: {base}). 앱이 https 아닌 주소는 거부합니다.")
    host = low.split("://", 1)[1].split("/", 1)[0]
    if any(b in host for b in BAD_HOSTS):
        die(f"자리표시 주소로 보입니다: {host}. 실제 배포 도메인을 넣으세요 — 이대로 나가면 업데이트 확인이 조용히 실패합니다.")

    check_default_update_url(base)
    check_control_chars()
    ver, staged, new_sha = _prepare(force)

    man = manifest(ver, base, new_sha, os.path.getsize(staged))
    with open(os.path.join(REL, "latest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, ensure_ascii=False)
    sums = write_sums()

    # ④ 3자 대조 — 쓴 다음 다시 읽어서 확인한다
    back = json.load(open(os.path.join(REL, "latest.json"), encoding="utf-8"))
    again = sha256_file(staged)
    bz = back.get("zip") or {}
    if not (back["sha256"] == bz.get("sha256") == again == sums[os.path.basename(staged)] == new_sha):
        die("3자 불일치: zip · latest.json(sha256·zip.sha256) · SHA256SUMS 의 sha256 이 다릅니다.")
    if bz.get("size") != os.path.getsize(staged) or bz.get("url") != zip_url(ver, base):
        die(f"latest.json 의 zip 칸이 실제 파일과 다릅니다: {bz}")
    if back["version"] != ver:
        die("latest.json 의 version 이 빌드한 판과 다릅니다.")
    # ⑥ url 이 파일(zip·exe)을 가리키면 1.0.0 exe 가 그것으로 제 자신을 덮는다 (머리 주석)
    if back["url"] != base + DOWNLOAD_PAGE or back["url"].lower().endswith((".zip", ".exe")):
        die(f"latest.json 의 url 이 받는 곳 페이지가 아닙니다: {back['url']}")

    print(f"릴리스 준비 완료  v{ver}")
    print(f"  {staged}  ({os.path.getsize(staged):,} B)")
    print(f"  sha256 {new_sha}")
    print(f"  url    {back['url']}  (받는 곳 — 1.0.0 exe 가 zip 으로 제 자신을 덮지 않게)")
    print(f"  zip    {bz['url']}  ({bz['size']:,} B · 1.0.2 이상이 제자리 업데이트로 받는다)")
    print()
    print(f"올릴 것: release/{rel_zip(ver)} · latest.json · SHA256SUMS.txt — zip 도 {base}/ 에 (latest.json 의 zip.url)")
    print(f"사이트에 둘 zip (SHA256SUMS 와 같음): {', '.join(site_zips())} — 그보다 오래된 zip 은 사이트에서 내린다")
    print("VirusTotal: python tools/vt_check.py release/" + rel_zip(ver) + " --json release/virustotal.json  (0 이 아니면 올리지 않는다)")
    print(f"확인:   {base}/latest.json 을 실제로 받아 sha256 이 위와 같은지 대조 (푸시만으로 반영됐다고 보지 말 것)")


if __name__ == "__main__":
    main()
