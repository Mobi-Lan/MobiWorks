/* overlay-tab.js — 게임 오버레이 설정 탭 (디자인 프레임 1d).
   밴드 자체는 파이썬(overlay.py)이 tkinter 로 그린다. 이 탭은 그 설정을 만지고, 지금 상태
   (떠 있는가 · 폭 · 전역 단축키가 실제로 등록됐는가)를 보여 준다.
   설정은 다른 설정과 같은 길로 간다 — GET/POST /api/settings. 오버레이 전용 API 는 없다. */

/* 표시 항목 — 이름·순서·기본 체크:
   큐 상태 ✓ / 현재 항목 이름 ✓ / 회차 ✓ / 가공 대기열 ✓ / 일괄 수령 버튼 ✓ / 오류 표시 ✓ /
        경과 시간 ✗ / 날개 소모 ✗
   「대기·완료·실패」는 밴드의 `3·2·1` 을 켜고 끄는 스위치다.
   「회차」 앞에 둔다(밴드에 나타나는 순서). */
const OV_SHOW = [
  ["overlay_show_queue", "큐 상태", "2/5", true],
  ["overlay_show_name", "현재 항목 이름", "종류 배지 · 이름 · 진행", true],
  ["overlay_show_columns", "대기·완료·실패", "3 · 2 · 1", true],
  ["overlay_show_round", "회차", "2/3회차", true],
  ["overlay_show_alter", "가공 대기열", "남은 시간 · 완료 수", true],
  ["overlay_show_collect", "일괄 수령 버튼", "완료가 있을 때만 나타납니다", true],
  ["overlay_show_error", "오류 표시", "실패 · 도구 없음", true],
  ["overlay_show_elapsed", "경과 시간", "이번 실행이 시작된 뒤 지난 시간 (실행 줄의 「경과」와 같은 값)", false],
  ["overlay_show_wings", "날개 소모", "정령의 날개 <b>예상</b> 소모 (실행 줄의 「예상 소모」와 같은 값)", false],
];
// 밴드가 스스로 바꾸는 값 — ⋮⋮ 로 끌면 「가운데 위에 자동 배치」가 꺼지고, 밴드의 🔒 · ↗ 는
// 「위치 잠금」·「클릭 통과」를 바꾼다. 폴링이 이 값들을 화면에 맞춘다 (화면에 남은 옛 값이 다시 저장되지 않게).
// 밴드의 자리(화면 좌표·게임 기준 오프셋)는 이 탭이 **절대 보내지 않는다** — 밴드만 정한다.
const OV_LIVE = ["overlay_follow_game", "overlay_lock", "overlay_click_through"];
const OV = { built: false, status: null, saveT: null, pollT: null, pending: {} };

const ovTg = (key, on) => `<button class="ov-tg${on ? " on" : ""}" data-ov="${key}" role="switch" aria-checked="${on ? "true" : "false"}"><span class="knob"></span></button>`;
const ovSeg = (key, val, opts) => `<span class="seg" data-ovseg="${key}">`
  + opts.map(([v, ko]) => `<button data-v="${v}"${v === val ? ' class="on"' : ""}>${esc(ko)}</button>`).join("") + "</span>";

function ovBuild() {
  const rows = OV_SHOW.map(([k, t, d, on]) =>
    `<div class="srow"><span><span class="t">${esc(t)}</span><br><span class="d">${d}</span></span>${ovTg(k, on)}</div>`).join("");
  $("tabOverlay").innerHTML =
    '<div class="card ovcard">'
    + '<div class="ch"><span class="ct">오버레이 밴드</span><span class="lbl">게임 위 한 줄</span><span class="grow"></span>'
    + '<span class="lbl" id="ovState"></span>' + ovTg("overlay_enabled", false) + '</div>'
    + '<div class="cb">'
    + '<div class="ssec">표시 항목</div>' + rows
    + '<div class="row ovnote"><span class="small-t muted">항목을 끄면 밴드가 그만큼 짧아집니다</span><span class="grow"></span><span class="small-t muted" id="ovWidth"></span></div>'
    + '<div class="srow"><span><span class="t">배경 투명도 스타일</span><br><span class="d">「반투명 글래스」는 밴드 뒤를 흐리게 합니다 · <b>일부 컴퓨터에서는 밴드가 아예 보이지 않습니다</b> — 그럴 때는 「불투명 솔리드」로 되돌리세요</span></span>'
    + ovSeg("overlay_backdrop", "solid", [["solid", "불투명 솔리드"], ["glass", "반투명 글래스"]]) + '</div>'
    + '<div class="srow"><span><span class="t">밴드 불투명도</span><br><span class="d">평소 값 · 사건 때는 자동 100%</span></span>'
    + '<span class="row" style="flex-wrap:nowrap"><input type="range" id="ovOpac" min="20" max="100" step="5" class="ovrange">'
    + '<span class="mono small-t" id="ovOpacN" style="width:38px;text-align:right"></span></span></div>'
    + '<div class="srow"><span><span class="t">사건 때만 밝게</span><br><span class="d">완료·오류·회차 전환에 1.5초 100% 후 복귀</span></span>' + ovTg("overlay_flash", true) + '</div>'
    + '<div class="srow"><span><span class="t">펼침 기본값</span><br><span class="d">▾ 를 눌러 대기 목록·가공 3칸 표시 · 밴드에서 ▾ 로 열고 닫은 상태는 다음 실행에도 이어집니다</span></span>'
    + ovSeg("overlay_expand", "off", [["off", "접힘"], ["on", "펼침"], ["error", "오류 때만"]]) + '</div>'
    + '<div class="ssec">동작</div>'
    + '<div class="srow"><span><span class="t">가공 완료 시 자동 일괄 수령</span><br><span class="d">끄면 밴드의 「일괄 수령」 버튼을 눌러야 합니다 · 어느 쪽이든 큐에 <b>담기만</b> 하고 실행은 「시작」입니다</span></span>' + ovTg("overlay_auto_collect", false) + '</div>'
    + '<div class="srow"><span><span class="t">클릭 통과 (관통 모드)</span><br><span class="d">켜면 마우스를 받지 않습니다 · ⋮⋮ · ▾ · 「일괄 수령」만 예외</span></span>' + ovTg("overlay_click_through", true) + '</div>'
    + '<div class="srow"><span><span class="t">가운데 위에 자동 배치</span><br><span class="d">켜면 게임 클라이언트 영역 <b>가운데 위</b>에 자리를 잡습니다 · 끄면 <b>⋮⋮ 로 끌어 둔 자리</b>입니다 · <b>어느 쪽이든 게임 창이 움직이면 따라갑니다</b> (끌어 둔 자리는 게임 기준 간격으로 기억합니다)</span></span>' + ovTg("overlay_follow_game", true) + '</div>'
    + '<div class="srow"><span><span class="t">위치 잠금</span><br><span class="d">끄면 ⋮⋮ 를 끌어 옮길 수 있습니다</span></span>' + ovTg("overlay_lock", false) + '</div>'
    + '<div class="srow"><span><span class="t">오류 토스트</span><br><span class="d">밴드 아래 5초 · 오류·회차 완료만</span></span>'
    + ovSeg("overlay_toast", "error", [["on", "켜짐"], ["error", "오류만"], ["off", "끔"]]) + '</div>'
    + '<div class="srow"><span><span class="t">큐가 멈추면 밴드 숨김</span><br><span class="d">대기 상태에서는 아무것도 표시하지 않습니다 (오류·정지는 계속 보입니다)</span></span>' + ovTg("overlay_hide_idle", false) + '</div>'
    + '<div class="srow"><span><span class="t">위치 초기화</span><br><span class="d">끌어 둔 자리를 지우고 「가운데 위에 자동 배치」로 되돌립니다 · 관통을 켠 채 화면 밖으로 끌어 놓았을 때 되찾는 길입니다</span></span><button class="small" id="ovReset">위치 초기화</button></div>'
    + '<div class="ssec">미리보기 · 현재 설정</div>'
    + '<div class="ovprev"><div class="ovband" id="ovPrev"></div>'
    + '<div class="row"><span class="small-t muted">드래그로 위치를 잡고 「위치 잠금」을 켜세요</span><span class="grow"></span>'
    + '<button class="small" id="ovTest" title="실제 밴드를 8초 동안 무조건 보이게 합니다 (설정은 바뀌지 않습니다)">테스트 표시</button></div></div>'
    + '</div></div>';
  $("ovTest").onclick = async () => {
    // **이 PC 의 창을 건드리는 것은 전부 `/api/overlay` 로 간다** — 밖(폰)에서는 절대 안 되는 길이라
    // 통째로 막아 두었기 때문이다. 설정 저장(`/api/settings`)과 섞여 있으면 둘 다 막아야 했다.
    // 폼 전체(ovForm)를 보내지 않는다 — 3초 늦은 화면 값이 방금 끝낸 끌기·밴드에서 바꾼 잠금·관통을 덮는다.
    // 아직 안 보낸 변경(OV.pending)만 함께 보낸다. 테스트 표시 자체는 설정을 바꾸지 않는다.
    clearTimeout(OV.saveT);
    const patch = OV.pending; OV.pending = {};
    const r = await api("/api/overlay", { settings: patch, overlayTest: true });
    if (r && r.settings) S.settings = r.settings;
    if (r && r.overlay) { OV.status = r.overlay; ovStatus(); }
    if (r && r.overlay && !r.overlay.running) toast("먼저 「오버레이 밴드」를 켜 주세요");
  };
  $("tabOverlay").querySelectorAll("[data-ov]").forEach((b) => {
    b.onclick = () => {
      const on = !b.classList.contains("on");
      b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
      ovSave(b.dataset.ov, on);
    };
  });
  $("tabOverlay").querySelectorAll("[data-ovseg]").forEach((g) => {
    g.querySelectorAll("button").forEach((b) => {
      b.onclick = () => {
        g.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        ovSave(g.dataset.ovseg, b.dataset.v);
      };
    });
  });
  $("ovOpac").oninput = () => { $("ovOpacN").textContent = $("ovOpac").value + "%"; ovSave("overlay_opacity", Number($("ovOpac").value) || 55); };
  $("ovReset").onclick = async () => {
    const r = await api("/api/overlay", { settings: {}, overlayReset: true });
    if (r && r.settings) { S.settings = r.settings; ovFill(r.settings); }
    if (r && r.overlay) { OV.status = r.overlay; ovStatus(); }
    toast("밴드 위치를 기본값으로 되돌렸습니다");
  };
  OV.built = true;
}

/* 지금 화면에 찍힌 값 → 설정 객체 */
function ovForm() {
  const out = {};
  $("tabOverlay").querySelectorAll("[data-ov]").forEach((b) => { out[b.dataset.ov] = b.classList.contains("on"); });
  $("tabOverlay").querySelectorAll("[data-ovseg]").forEach((g) => {
    const on = g.querySelector("button.on"); out[g.dataset.ovseg] = on ? on.dataset.v : "off";
  });
  out.overlay_opacity = Number($("ovOpac").value) || 55;
  return out;
}

function ovFill(s) {
  $("tabOverlay").querySelectorAll("[data-ov]").forEach((b) => {
    const on = !!s[b.dataset.ov]; b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
  });
  $("tabOverlay").querySelectorAll("[data-ovseg]").forEach((g) => {
    const v = s[g.dataset.ovseg]; g.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.v === v));
  });
  $("ovOpac").value = s.overlay_opacity != null ? s.overlay_opacity : 55;
  $("ovOpacN").textContent = $("ovOpac").value + "%";
  ovPreview();
}

/* 저장 — 다른 설정과 같은 길(POST /api/settings). **바뀐 키 하나만** 보낸다.
   폼 전체를 보내면 화면에 잠깐 남아 있던 옛 값이 다른 설정을 덮어쓴다 — 「위치 잠금을 눌렀더니
   오버레이가 꺼졌다」가 그 모양이다. 서버의 set_settings 는 원래 patch 를 병합하므로 한 키만 보내면 된다. */
function ovSave(key, value) {
  ovPreview();
  if (!key) return;
  OV.pending[key] = value;              // 저장이 끝나기 전엔 폴링이 이 키를 되돌리지 못하게
  clearTimeout(OV.saveT);
  OV.saveT = setTimeout(async () => {
    const patch = OV.pending; OV.pending = {};
    // 오버레이 설정도 창에 바로 반영돼야 하므로 같은 길로 (이 PC 에서만 부른다)
    const r = await api("/api/overlay", { settings: patch });
    if (!r.ok) { toast(r.message || r.error || "저장 실패"); return; }
    S.settings = r.settings || S.settings;
    if (r.overlay) { OV.status = r.overlay; ovStatus(); }
  }, 250);
}

/* 밴드 상태: 지금 보이는가 · 안 보이면 왜 · 폭.
   「왜 안 보이는가」를 꼭 보여 준다 — 게임이 없으면 밴드를 내리는데, 그게 「스위치를 켰는데 아무것도
   안 나타난다」로 읽히면 안 된다 (overlay_hide_idle 로 이미 한 번 겪었다). 못 만드는 기능의 변명이 아니라
   지금 상태를 알려 주는 표시다. */
function ovStatus() {
  const st = OV.status || {};
  const on = $("tabOverlay").querySelector('[data-ov="overlay_enabled"]').classList.contains("on");
  const why = st.reasonText || "";
  $("ovState").textContent = st.error ? "창을 띄우지 못했습니다"
    : !st.running ? (on ? "켜는 중…" : "꺼짐")
      : st.visible ? "보이는 중" : (why || "숨김");
  const HINT = {
    no_game: "게임이 켜지면 밴드가 다시 나타납니다",
    minimized: "게임 창을 되살리면 밴드가 다시 나타납니다",
    idle: "「큐가 멈추면 밴드 숨김」이 켜져 있습니다 — 끄면 바로 보입니다",
  };
  const bits = [];
  if (st.error) bits.push(st.error);
  else if (st.visible && st.width) bits.push(`현재 폭 ${st.width}px` + (st.blur ? " · 배경 흐림 켜짐" : ""));
  else if (st.running && HINT[st.reason]) bits.push(HINT[st.reason]);
  else bits.push("밴드가 보일 때 실제 폭이 표시됩니다");
  // 전용 전체화면에서는 어떤 창도 게임 위에 못 뜬다 — 밴드가 아니라 여기서 알려 준다
  if (st.fullscreen === "exclusive") bits.push("게임이 전체화면 전용이라 밴드가 게임 위에 보이지 않습니다 — 「테두리 없는 창」 모드로 바꿔 주세요");
  $("ovWidth").textContent = bits.join(" · ");
}

/* 미리보기 — 켠 항목만 나오는 밴드 한 줄 (값은 예시) */
function ovPreview() {
  const f = ovForm();
  const g = [];
  if (f.overlay_show_queue) g.push('<i class="ov-dot ov-run"></i><span class="ov-q">2/5</span>');
  if (f.overlay_show_name) g.push('<span class="ov-badge">채집</span><span class="ov-name">통나무</span><span class="ov-prog">60/100</span>');
  if (f.overlay_show_columns) g.push('<span class="ov-cols"><span class="w">3</span>·<span class="d">2</span>·<span class="f">1</span></span>');
  if (f.overlay_show_round) g.push('<span class="ov-round">2/3회차</span>');
  if (f.overlay_show_elapsed) g.push('<span class="ov-prog">12:04</span>');
  if (f.overlay_show_wings) g.push('<span class="ov-round">날개 45</span>');
  if (f.overlay_show_alter) g.push('<span class="ov-alt">가공</span><span class="ov-time">02:10</span><i class="ov-dot ov-ok"></i><span class="ov-done">1</span>');
  g.push('<span class="ov-x">▾</span>' + (f.overlay_show_collect ? '<span class="ov-btn">일괄 수령</span>' : ""));
  if (f.overlay_show_error) g.push('<i class="ov-dot ov-bad"></i><span class="ov-err">실패 · 도구 없음</span>');
  // 밴드 맨 끝에도 ▾ 를 둔다 (overlay.py 의 expand2 칸)
  $("ovPrev").innerHTML = '<span class="ov-h">⋮⋮</span>' + g.join('<span class="ov-sep"></span>') + '<span class="ov-x">▾</span>';
  $("ovPrev").style.opacity = String(Math.max(20, Math.min(100, f.overlay_opacity)) / 100);
}

async function renderOverlayTab() {
  if (!OV.built) ovBuild();
  const d = await api("/api/settings?nocli=1");
  if (d && d.settings) { S.settings = d.settings; ovFill(d.settings); }
  if (d && d.overlay) OV.status = d.overlay;
  ovStatus();
  clearInterval(OV.pollT);
  // 탭이 보이는 동안만 상태를 다시 읽는다 (단축키 등록 결과·실제 폭은 창이 뜬 뒤에야 정해진다)
  OV.pollT = setInterval(async () => {
    const sec = $("tabOverlay");
    if (!sec || sec.hidden) { clearInterval(OV.pollT); OV.pollT = null; return; }
    const r = await api("/api/settings?nocli=1");
    if (r && r.overlay) { OV.status = r.overlay; ovStatus(); }
    // 밴드 쪽에서 바뀐 값(OV_LIVE — ⋮⋮ 끌기가 끄는 자동 배치, 🔒 잠금, ↗ 관통)만 화면에 맞춘다.
    // 전부 다시 채우면 지금 만지고 있던 항목이 되돌아간다 — 그래서 이것만 본다.
    if (r && r.settings) {
      S.settings = r.settings;
      for (const k of OV_LIVE) {
        if (k in OV.pending) continue;      // 방금 눌러 저장 중인 키는 건드리지 않는다 (경쟁 상태)
        const b = $("tabOverlay").querySelector(`[data-ov="${k}"]`);
        if (!b) continue;
        const on = !!r.settings[k];
        if (b.classList.contains("on") !== on) {
          b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false"); ovPreview();
        }
      }
    }
  }, 3000);
}

Object.assign(window.MW, { OV_SHOW, OV_LIVE, renderOverlayTab, ovBuild, ovForm, ovFill, ovSave, ovStatus, ovPreview });
