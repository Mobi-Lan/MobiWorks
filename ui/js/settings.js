// settings.js — 설정 **탭** (갈래 탭 · 채우기 · 잠금 · 되돌리기 · 저장 · 연결 확인 · 숫자 ±)
/* 설정은 팝업이 아니라 **탭 하나**다 — 관련 작업을 그 자리에서 할 수 있게.

   모비폴리오 설정(ui/folio/settings.html)의 짜임을 그대로 따른다 —
     · 갈래는 **밑줄 탭**이고 한 번에 한 갈래만 보인다 (showSec). 보던 갈래는 sessionStorage 에 남긴다.
     · 값은 `cur` 에 모아 두고, 손대면 발에 「저장하지 않은 변경이 있습니다」가 뜬다 (dirty).
     · 「저장」은 발에 있고 한 번에 보낸다. 「되돌리기」는 마지막으로 읽은 값으로 화면을 되돌린다.
     · 「밖에서 접속」 갈래만 예외로 **바꾼 즉시** 저장된다 — remote.js 가 맡는다 (밖으로 나가는 문의 값이라
       「저장을 깜빡했는데 열려 있다」가 생기면 안 된다). 그래서 그 갈래는 dirty 를 켜지 않는다.

   값이 채워지기 전에 저장·확인을 누르면 **빈 주소가 저장돼 update_url 이 지워진다**
   (MobiFolio 0.2.3 에서 실제로 겪은 사고). 폼이 실제로 채워질 때까지 버튼을 잠근다 —
   느린 응답과 폼 초기화를 한 요청에 묶지 않는다. */
/* 밖(폰)에서는 이 PC 의 파일을 다루는 줄(백업 zip·복원·문제 신고 zip)을 감춘다 — 서버는 REMOTE_NEVER 로 403 이지만
   눌러서 실패하는 단추를 보여 주지 않는다. */
if (!NET.CAN.files) for (const el of document.querySelectorAll("[data-pconly]")) el.hidden = true;
/* 갈래째 이 PC 의 것 — 「업데이트」(이 PC 에 받아 설치 · /api/update/* = NEVER)와
   「밖에서 접속」(문 자체 · /api/remote/* = NEVER). 폰에서는 403 이라 **틀린 상태**(터널 꺼짐 등)를 그렸다.
   갈래와 그 탭을 함께 감추고 `data-off` 로 표시해 setShowSec 가 건너뛴다 (보던 갈래로 기억돼 있어도). */
for (const [can, id] of [[NET.CAN.update, "sSecUpd"], [NET.CAN.remote, "sSecRemote"]]) {
  if (can) continue;
  const sec = document.getElementById(id);
  if (sec) { sec.hidden = true; sec.dataset.off = "1"; }
  const tab = document.querySelector('#sNav button[data-s="' + id + '"]');
  if (tab) tab.hidden = true;
}
/* 날개가 드는 큐의 **안전장치**(반복 상한·날개 상한·헛소모 상한·두 창) — 밖에서는 **실행 범위(run)** 에서만 바꿀 수 있다
   (server.py `REMOTE_SETTINGS_RUN_ONLY` — edit 범위는 날개가 안 드는 값만). 폰은 제 범위를 몰라서
   `/api/settings` 의 답(`scope`)으로 안다 — 값이 오면 그때 edit 이면 줄을 감추고(data-pconly 와 같은 방식) 저장에서도 뺀다.
   목록은 서버와 같아야 한다 (tests/test_security_s7_decisions.py 가 대조한다). */
const RUN_ONLY_KEYS = ["gather_quest_watch", "queue_precheck", "queue_max_passes",
                       "wing_cap_total", "wing_cap_waste", "wing_cap_window_min", "wing_cap_waste_min"];
let runOnlyOk = !NET.REMOTE;   // 이 PC 에서는 늘 된다
function applyRunOnly(scope) {
  runOnlyOk = !NET.REMOTE || scope === "run";
  for (const el of document.querySelectorAll("[data-runonly]")) el.hidden = !runOnlyOk;
}
let setLoaded = false;
function setBusy(b) {
  setLoaded = !b;
  for (const id of ["sSave", "sReset", "sUpdNow", "sTest"]) { const e = $(id); if (e) e.disabled = b; }
}
function cliHint(c) {
  if (!c) return "연결 상태를 확인하는 중…";
  if (!c.found) return "CLI 를 찾지 못했습니다";
  return c.pipe === "connected" ? "연결됨" + (c.demo ? " (데모)" : "")
    : "게임이 꺼져 있거나 연결되지 않았습니다" + (c.reason ? " (" + c.reason + ")" : "");
}

/* 「연결 확인」 설명 칸 — 처음 열 때 빈칸이던 것.
   설정은 `nocli=1` 로 읽으므로 CLI 상태는 **첫 상태 조회**(main.js `refreshState` → `S.cli`)가 채운다.
   탭을 그보다 먼저 열면 `S.cli` 가 아직 없다 — 여기서 CLI 를 한 번 더 찌르지 않고 그 조회가
   끝나기를 기다렸다가 적는다. 게임이 꺼져 있으면 probe 가 5초 걸리므로 넉넉히 30초. */
let cliWaitT = 0;
function paintCliHint(tries) {
  const h = $("sCliHint"); if (!h) return;
  clearTimeout(cliWaitT);
  h.textContent = cliHint(S.cli);
  tries = tries || 0;
  if (!S.cli && tries < 60) cliWaitT = setTimeout(() => paintCliHint(tries + 1), 500);
}

/* 업데이트 주소 규칙 — **서버가 준 규칙·문구를 그대로** 쓴다 (`/api/settings` 의 update_url_rule).
   배포판은 https 만, 개발 모드만 이 PC 의 http(127.0.0.1·localhost·[::1]). 화면에서 따로 문구를 지어내면
   서버 판정(`server._safe_url`)과 또 어긋난다 — 예전 문구 「https 로 시작해야」가 개발 모드의 http 허용과 달랐다.
   규칙을 못 받았으면(옛 서버) 막지 않고 서버 판정에 맡긴다. */
let updRule = null;
function updUrlProblem(u) {
  u = String(u || "").trim();
  if (!u || !updRule) return "";
  if (/^https:\/\/[^\s]/i.test(u)) return "";
  if (!updRule.https_only && /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i.test(u)) return "";
  return updRule.message || "";
}

/* 발의 한 줄 — 폴리오의 say() 와 같다. kind: "" | "ok" | "bad" */
function setSay(m, kind) { const e = $("sMsg"); if (!e) return; e.textContent = m || ""; e.className = "set-msg" + (kind ? " " + kind : ""); }
function setDirty() { if (setLoaded) setSay("저장하지 않은 변경이 있습니다"); }

/* ── 갈래 = 탭 (폴리오 방식) ──
   길게 이어 놓고 스크롤하는 대신 한 번에 한 갈래만 보인다. 저장하고 돌아와도 보던 갈래로. */
function setShowSec(id) {
  const secs = [...document.querySelectorAll("#sBody .set-sec")].filter((x) => !x.dataset.off);   // 폰에서 감춘 갈래는 고르지 않는다
  const pick = secs.find((x) => x.id === id) || secs[0];
  if (!pick) return;
  for (const x of secs) x.hidden = (x !== pick);
  document.querySelectorAll("#sNav button").forEach((b) => b.classList.toggle("on", b.dataset.s === pick.id));
  const body = $("sBody"); if (body) body.scrollTop = 0;   // 갈래를 바꾸면 위에서부터 본다
  try { sessionStorage.setItem("mw.set.tab", pick.id); } catch (e) {}
}
document.querySelectorAll("#sNav button").forEach((b) => { b.onclick = () => setShowSec(b.dataset.s); });
{
  let first = "sSecGen";
  try { first = sessionStorage.getItem("mw.set.tab") || first; } catch (e) {}
  setShowSec(first);
}

/* 서버에서 읽은 값을 칸에 붓는다. 「되돌리기」도 이 함수로 마지막 값을 다시 붓는다. */
function fillSettings(s) {
  const set = (id, v) => { const e = $(id); if (e) e.value = v; };
  const chk = (id, v) => { const e = $(id); if (e) e.checked = !!v; };
  set("sCli", s.cli_exe || "");
  chk("sAuto", s.auto_sync);
  set("sPoll", s.work_poll_sec || 30);
  set("sQWeight", s.queue_weight_margin != null ? s.queue_weight_margin : 30);
  set("sQPasses", s.queue_max_passes != null ? s.queue_max_passes : 50);
  set("sQWingCap", s.wing_cap_total != null ? s.wing_cap_total : 150);
  set("sQWingWaste", s.wing_cap_waste != null ? s.wing_cap_waste : 4);
  set("sQWingMin", s.wing_cap_window_min != null ? s.wing_cap_window_min : 10);
  set("sQWingWasteMin", s.wing_cap_waste_min != null ? s.wing_cap_waste_min : 5);
  set("sUpdUrl", s.update_url || "");
  chk("sUpdChk", s.update_check);
  chk("sBatchCollect", s.alter_batch_collect);
  chk("sQDoneNotify", s.queue_done_notify !== "off");   // 큐 종료 알림 — 기본 on
  // 테마 — 이 PC 는 설정 값을 칠하고 **입힌다**(「되돌리기」가 미리 보기를 되돌린다). 폰은 그 폰의 선택(없으면 「PC 설정」)
  if (window.MWTheme) {
    if (MWTheme.remote()) { MWTheme.fromSettings(s); themeSel = MWTheme.choice(); }
    else { themeSel = ["auto", "dark", "light"].includes(s.ui_theme) ? s.ui_theme : "auto"; MWTheme.apply(themeSel); }
    paintThemeSeg();
  }
}

/* ── 테마 줄 ──
   이 PC: 누르면 **바로 입히고**(미리 보기) 발에 「저장하지 않은 변경」 — 「저장」이 `ui_theme` 을 보낸다.
   폰: 「PC 설정」 칸이 더 있고, 고르면 **그 폰에만 바로** 적용된다 (저장 단추와 무관 · PC 화면은 안 바뀐다). */
let themeSel = "auto";
function paintThemeSeg() {
  const seg = $("sTheme"); if (!seg) return;
  const rem = !!(window.MWTheme && MWTheme.remote());
  for (const b of seg.querySelectorAll("button")) {
    if (b.dataset.v === "pc") b.hidden = !rem;
    const on = b.dataset.v === themeSel;
    b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
  }
  const h = $("sThemeHint");
  if (h && rem) h.textContent = "기본은 PC 설정을 따릅니다 · 여기서 고르면 이 기기에만 바로 적용됩니다 (PC 화면은 그대로)";
}
{
  const seg = $("sTheme");
  if (seg) seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]"); if (!b || !window.MWTheme) return;
    themeSel = b.dataset.v;
    if (MWTheme.remote()) MWTheme.save(themeSel);          // 폰 — 그 폰에만, 바로
    else { MWTheme.apply(themeSel); setDirty(); }           // 이 PC — 미리 보기, 저장은 발의 「저장」
    paintThemeSeg();
  });
}
/* 머리줄 ☀ 로 바꾸면(theme.js — 바로 저장) 설정 줄도 같이 */
if (window.MWTheme) MWTheme.onChange = (t, r) => {
  if (r && r.settings && !MWTheme.remote()) S.settings = Object.assign({}, S.settings, { ui_theme: r.settings.ui_theme });
  themeSel = MWTheme.choice(); paintThemeSeg();
};

/* 탭을 열 때마다 서버에서 다시 읽는다 — 다른 창·오버레이에서 바꾼 값이 있을 수 있다.
   `nocli=1`: 설정을 읽는 김에 CLI 를 찔러 보지 않는다 (직렬이라 화면이 굳는다). */
async function loadSettings() {
  const box = $("sUpdBox"); if (box) box.hidden = true;
  setBusy(true);
  setSay("값이 채워지기 전엔 저장이 잠깁니다");
  const d = await api("/api/settings?nocli=1");
  const s = (d && d.settings) || {};
  S.settings = s;
  updRule = (d && d.update_url_rule) || null;
  applyRunOnly(d && d.scope);   // 밖에서는 답에 이 기기의 범위가 실린다 — run 이 아니면 날개 안전장치 줄을 감춘다
  fillSettings(s);
  paintOvBtn(s);
  paintCliHint();
  setBusy(false);
  setSay("");
  // 「밖에서 접속」 칸은 remote.js 가 채운다 (이 PC 에서만 쓰는 화면이다)
  if (MW.renderRemote) { try { await MW.renderRemote(s); } catch (e) { console.error("renderRemote", e); } }
}

/* 헤더의 ⚙ 는 이제 **탭으로 데려가는 단추**다 (창을 열지 않는다) */
{
  const b = $("btnSettings");
  if (b) b.onclick = () => { if (MW.switchTab) MW.switchTab("settings", true); };
}
/* 헤더의 오버레이 단추 — 폴리오 머리줄과 같은 자리. 켜짐은 설정 overlay_enabled 그대로.
   동작은 `POST /api/overlay`(이 PC 의 창 — REMOTE_NEVER) 라 밖에서는 단추를 감춘다. */
function paintOvBtn(s) {
  const b = $("btnOv"); if (!b) return;
  if (!NET.CAN.overlay) { b.hidden = true; return; }     // 할 수 있는 일(밖에서는 못 함) — 폭·불러오기와 무관
  /* 설정이 오기 전에도 **자리는 그대로** 둔다 (단추가 나타났다 사라지면 안 된다) — 흐리게 두고 누르지 못하게.
     지금 켜짐을 모르는 채로 누르면 뒤집을 값이 틀린다. */
  const ready = !!(s || S.settings);
  b.disabled = !ready;
  if (!ready) { b.title = "불러오는 중 — 설정을 받으면 누를 수 있습니다"; return; }
  const on = !!(s || S.settings || {}).overlay_enabled;
  b.setAttribute("aria-pressed", on ? "true" : "false");
  b.classList.toggle("on", on);
  b.title = on ? "게임 위 오버레이 끄기" : "게임 위 오버레이 켜기";
}
{
  const b = $("btnOv");
  if (b) b.onclick = async () => {
    const want = !((S.settings || {}).overlay_enabled);
    paintOvBtn({ overlay_enabled: want });                       // 누른 대로 먼저 그린다
    const r = await api("/api/overlay", { settings: { overlay_enabled: want } });
    if (!r || r.ok === false) { paintOvBtn(); toast("오버레이를 바꾸지 못했습니다: " + ((r && (r.message || r.error)) || "")); return; }
    S.settings = Object.assign({}, S.settings, (r.settings || {}), { overlay_enabled: want });
    paintOvBtn();
    if (typeof fillSettings === "function" && $("tabSettings") && !$("tabSettings").hidden) fillSettings(S.settings);   // 설정 탭이 열려 있으면 스위치도 같이
  };
  paintOvBtn();
}

/* 손대면 발에 알린다 — 「밖에서 접속」 갈래는 즉시 저장이라 제외. 복원할 zip 을 고르는 것도 설정 변경이 아니다 */
{
  const body = $("sBody");
  const rm = $("sSecRemote");
  const mark = (e) => { if (rm && rm.contains(e.target)) return; if (e.target && e.target.id === "sBkFile") return; setDirty(); };
  if (body) { body.addEventListener("input", mark); body.addEventListener("change", mark); }
}

/* ── 백업·복원 ──
   내려받기는 fetch(헤더 토큰) → blob 저장 (`saveDownload`, core.js) — 토큰을 주소에 싣지 않는다.
   사전 탭의 「JSON 내보내기」와 같은 길이다. 복원은 zip 을 몸통째 POST 한다 —
   `api()` 는 JSON 만 보내므로 여기서 직접 fetch 한다. 위험한 확인은 askOk(danger) — 브라우저 창은 안 쓴다. */
/* 복원해도 **지금 값이 남는** 설정 — 서버의 무시 목록(store.py `RESTORE_IGNORE_KEYS` + `remote*`)을
   사람 말로 옮긴 것이다. 예전 문구는 「인증키·CLI 경로·업데이트 주소」 셋만 말해 밖에서 접속
   켜기·범위·자동 끊김도 안 바뀐다는 것을 숨겼다. (「연출 영상」은 기능째 없앴다.) 목록이 바뀌면 tests/test_f5_folio_settings.py
   가 이 문구와 서버 목록을 맞대어 본다 — 한쪽만 고치면 검사가 떨어진다. */
const RESTORE_KEEPS = "밖에서 접속 설정 전부(인증키·켜기·범위·자동 끊김)·CLI 경로·업데이트 주소";
{
  const dl = $("sBkDown");
  if (dl) dl.onclick = (e) => { e.preventDefault(); saveDownload("/api/backup", "mobiworks-backup.zip"); };
  const inp = $("sBkFile"), pick = $("sBkPick"), name = $("sBkName"), go = $("sBkGo"), out = $("sBkOut");
  // 결과는 **글자로만** 넣는다 (textContent) — 파일 이름은 서버가 준 값이라 innerHTML 에 싣지 않는다. 줄바꿈은 CSS pre-line
  const show = (text, kind) => { if (!out) return; out.hidden = false; out.className = "set-out" + (kind ? " " + kind : ""); out.textContent = text; };
  if (pick && inp) pick.onclick = () => inp.click();
  if (inp) inp.onchange = () => {
    const f = inp.files && inp.files[0];
    if (name) name.textContent = f ? `${f.name} (${Math.max(1, Math.round(f.size / 1024))} KB)` : "고른 파일 없음";
    if (go) go.disabled = !f;
    if (out) out.hidden = true;
  };
  if (go && inp) go.onclick = async () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const yes = await askOk(`「${f.name}」 의 설정·재생목록·프리셋·대기열·합주 인원으로 지금 값을 덮습니다. 대기열이 도는 중이면 대기열만 건너뜁니다. 적용 전에 지금 자료를 data 폴더에 자동 보관합니다. 복원해도 지금 값이 남는 것: ${RESTORE_KEEPS}.`,
      { title: "백업에서 복원할까요?", ok: "복원", danger: true });
    if (!yes) return;
    go.disabled = true;
    show("검사하고 적용하는 중…");
    let j;
    try {
      const r = await fetch("/api/restore", { method: "POST", headers: { ...NET.headers(false), "Content-Type": "application/zip" }, body: f });
      j = await r.json();
    } catch (e) { j = { ok: false, error: "network", message: String(e.message || e) }; }
    go.disabled = false;
    if (!j || !j.ok) {
      show("복원하지 못했습니다: " + ((j && (j.message || j.error)) || "") + (j && j.file ? " (" + j.file + ")" : ""), "bad");
      toast("복원 실패");
      return;
    }
    const files = (j.applied || []).join(" · ");
    const ign = (j.ignored_keys || []).length ? "\n무시한 키 (지금 값 유지): " + j.ignored_keys.join(" · ") : "";
    // 건너뛴 파일(대기열이 도는 중이던 queue.json)은 **따로 한 줄로** 알린다. 성공 줄에 묻히면 복원된 줄 안다
    const sk = (j.skipped || []).map((x) => `${x.file} — ${x.reason}`).join("\n");
    show(`${(j.applied || []).length}개 파일을 적용했습니다: ${files}${ign}${sk ? "\n건너뜀: " + sk : ""}\n복원 전 자료는 ${j.kept || ""} 로 보관했습니다.${(j.pruned || []).length ? ` (옛 보관본 ${j.pruned.length}개 정리 — 최근 5개만 남깁니다)` : ""}`, "ok");
    toast("복원했습니다");
    inp.value = "";
    if (name) name.textContent = "고른 파일 없음";
    go.disabled = true;
    // 서버는 파일에서 다시 읽지만 **이 화면의 사본**(S.settings·폼·갱신 주기)은 여기서 다시 채운다
    await loadSettings();
    if (typeof schedulePoll === "function") schedulePoll();
  };
}

/* ── 문제 신고 zip ──
   백업 내려받기와 같은 길 — `saveDownload`(fetch + 헤더 토큰 → blob). 토큰은 주소에 싣지 않는다.
   서버가 로그·설정·대기열을 가린(마스킹) 뒤 묶는다. 저장한 파일 이름은 서버의 Content-Disposition 이다. */
{
  const b = $("sReport"), nm = $("sReportName");
  if (b) b.onclick = async () => {
    b.disabled = true;
    if (nm) { nm.className = "set-file"; nm.textContent = "만드는 중…"; }
    const file = await saveDownload("/api/report", "mobiworks-report.zip");
    b.disabled = false;
    if (!file) { if (nm) nm.textContent = "만들지 못했습니다"; return; }
    const msg = `${file} 저장했습니다 — 대화에 붙여 주세요`;
    if (nm) nm.textContent = msg;   // 글자로만 (파일 이름은 서버가 준 값)
    toast(msg);
  };
}

/* ── 바탕화면 바로가기 (임베디드 판만 · 줄은 main.js 가 /api/health 의 embed 로 연다) ── */
{
  const b = $("sShortcut");
  if (b) b.onclick = async () => {
    b.disabled = true;
    const r = await api("/api/shortcut/desktop", {});
    b.disabled = false;
    toast((r && r.message) || (r && r.error) || "바로가기를 만들지 못했습니다");
  };
}

{
  const sv = $("sSave");
  if (sv) sv.onclick = async () => {
    if (!setLoaded) return;
    const val = (id, d) => { const e = $(id); return e ? e.value : d; };
    const on = (id) => { const e = $(id); return !!(e && e.checked); };
    const bad = updUrlProblem(val("sUpdUrl", ""));
    if (bad) { setSay("저장하지 못했습니다: " + bad, "bad"); toast(bad); return; }
    const r = await api("/api/settings", {
      settings: {
        cli_exe: String(val("sCli", "")).trim(), auto_sync: on("sAuto"),
        work_poll_sec: Number(val("sPoll", 30)) || 30,
        queue_weight_margin: Number(val("sQWeight", 30)),
        // 날개 안전장치 — 밖에서 edit 이면 줄을 감췄으니 보내지도 않는다 (보내면 서버가 `ignored` 로 버린다, RUN_ONLY_KEYS)
        ...(runOnlyOk ? {
          queue_max_passes: Number(val("sQPasses", 50)) || 50,
          wing_cap_total: Number(val("sQWingCap", 150)) || 150,   // 날개 차단기 — 범위(50–1000·2–20)는 서버가 자른다
          wing_cap_waste: Number(val("sQWingWaste", 4)) || 4,
          wing_cap_window_min: Number(val("sQWingMin", 10)) || 10,       // 창 길이도 설정 (1–120분, 서버가 자른다)
          wing_cap_waste_min: Number(val("sQWingWasteMin", 5)) || 5,
        } : {}),
        update_url: String(val("sUpdUrl", "")).trim(), update_check: on("sUpdChk"),
        alter_batch_collect: on("sBatchCollect"),
        queue_done_notify: on("sQDoneNotify") ? "on" : "off",   // 값이 안 드는 설정 — 폰(edit)에서도 바꾼다
        // 테마 — 이 PC 에서만 보낸다. 폰의 선택은 그 폰에만 둔다 (theme.js)
        ...(NET.REMOTE ? {} : { ui_theme: themeSel }),
      },
    });
    if (!r.ok) { setSay("저장하지 못했습니다: " + (r.message || r.error || ""), "bad"); toast(r.message || r.error || "저장 실패"); return; }
    S.settings = r.settings || S.settings;
    if (r.cli) { S.cli = r.cli; renderConn(); }
    if (typeof schedulePoll === "function") schedulePoll();
    setSay("저장했습니다", "ok");
    toast("저장했습니다");
  };
}
{
  const rs = $("sReset");
  if (rs) rs.onclick = () => {
    if (!setLoaded) return;
    fillSettings(S.settings || {});
    setSay("되돌렸습니다");
  };
}
{
  const t = $("sTest");
  if (t) t.onclick = async () => {
    if (!setLoaded) return;
    clearTimeout(cliWaitT);   // 기다리던 첫 조회가 확인 결과를 덮지 않게
    const h = $("sCliHint"); if (h) h.textContent = "확인 중…";
    const d = await api("/api/cli_test", { cli_exe: String(($("sCli") || {}).value || "").trim() });
    if (h) h.textContent = d.ok ? cliHint(d.cli)
      : (d.error === "network" ? "앱 서버에 연결할 수 없습니다 (이 창은 종료된 서버를 보고 있습니다)"
        : "확인 실패: " + (d.message || d.error || ""));
  };
}

// 숫자 입력의 ± (.set-num): data-for 의 input 을 data-step 만큼, min/max 안에서. 길게 누르면 연속
document.querySelectorAll("[data-ss]").forEach((b) => {
  holdBtn(b, () => {
    const i = $(b.dataset.for); if (!i) return;
    const st = Number(b.dataset.step || 1) * Number(b.dataset.ss);
    const lo = Number(i.min || 0), hi = Number(i.max || 999999);
    const v = Math.max(lo, Math.min(hi, (Number(i.value) || lo) + st));
    if (v !== Number(i.value)) { i.value = v; i.dispatchEvent(new Event("input", { bubbles: true })); }
  });
});

Object.assign(window.MW, { paintOvBtn, setBusy, cliHint, loadSettings, fillSettings, setShowSec, setSay, updUrlProblem });
