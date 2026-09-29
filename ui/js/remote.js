// remote.js — 설정 탭의 「밖에서 접속 (폰)」. 이 PC 에서만 쓰는 화면이다.
/* 여기서 하는 일은 넷뿐이다: 켜고 끄기 · 인증키 · 터널 · 연결 코드.
   밖(폰)에서는 이 길들이 통째로 막혀 있다 (`REMOTE_NEVER_PREFIX = "/api/remote/"`) —
   문을 밖에서 여닫을 수 있으면 자물쇠를 채운 의미가 없다.

   **인증키는 받은 그 자리에서 한 번만 보여 준다.** 서버가 다시는 안 알려 준다 (설정 응답에서
   지운다). 화면이 들고 있으면 캡처 한 장으로 문이 열린다. 직접 정한 키도 같다 — 저장하면
   칸을 비우고, 노란 상자에 그 한 번만 보인다.

   **연결 코드 옆에 QR 을 띄운다** (모비폴리오 설정에 있던 것).
   QR 에 담는 주소는 서버가 준다(`link`) — 우편함 주소를 화면에 또 적지 않는다. */
const RM = { tun: null, devices: [], busy: false, linkT: null };

/* 인증키 규칙 — **칸의 속성을 그대로 읽는다** (minlength·maxlength). 규칙을 여기 숫자로 또 적으면
   한쪽만 바뀐다. 글자는 영문·숫자, 대소문자·빈칸·붙임표는 서버(`store.norm_remote_key`)와 같이 푼다. */
const RM_KEY_RX = /^[A-Z0-9]+$/;
const RM_KEY_DISTINCT = 4;   // 서로 다른 글자 최소 (store.REMOTE_KEY_DISTINCT 와 같다 — 「AAAAAAAA」를 막는다)
function rmNormKey(v) { return String(v || "").trim().toUpperCase().replace(/[\s-]/g, ""); }
function rmKeyProblem(v, el) {
  const min = (el && el.minLength > 0) ? el.minLength : 8, max = (el && el.maxLength > 0) ? el.maxLength : 32;
  if (!v) return "인증키를 적어 주세요.";
  if (!RM_KEY_RX.test(v)) return "영문과 숫자만 쓸 수 있습니다.";
  if (v.length < min) return `인증키는 ${min}자 이상이어야 합니다.`;
  if (v.length > max) return `인증키는 ${max}자까지입니다.`;
  if (new Set(v).size < RM_KEY_DISTINCT) return `너무 단순합니다 — 서로 다른 글자를 ${RM_KEY_DISTINCT}개 이상 섞어 주세요.`;
  return "";
}

/** 키를 **그 한 번** 보여 준다 — 발행한 것이든 직접 정한 것이든 같은 노란 상자. */
function rmKeyShow(key, mine) {
  const box = rmEl("sRmKeyBox");
  if (!box) return;
  box.hidden = false;
  box.innerHTML = `<span class="h">${mine ? "직접 정한 인증키입니다" : "새로 발행한 인증키입니다"} — 모바일에 옮겨 적으세요 · <b>다시 보여 주지 않습니다</b></span>`
    + `<span class="v">${esc(key)}</span>`
    + `<span class="h">지금까지 짝지었던 기기는 전부 끊겼습니다.</span>`;
}

const rmEl = (id) => $(id);
function rmFill(s) {
  const on = rmEl("sRmOn"); if (on) on.checked = !!s.remote_on;
  const idle = rmEl("sRmIdle"); if (idle) idle.value = s.remote_idle_min != null ? s.remote_idle_min : 30;
  const seg = rmEl("sRmScope");
  if (seg) seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === (s.remote_scope || "read")));
  const ks = rmEl("sRmKeyState");
  if (ks) ks.innerHTML = s.remote_key_set
    ? '<b style="color:var(--ok)">인증키가 정해져 있습니다</b> · 값은 다시 보여 주지 않습니다'
    : '<b style="color:var(--bad2)">인증키가 없습니다</b> — 발행해야 터널을 켤 수 있습니다';
}

function rmTunTxt(t) {
  if (!t) return "터널 꺼짐";
  if (t.busy) return `터널 ${esc(t.busy)}…`;
  if (!t.running) return t.error ? `터널 꺼짐 · ${esc(t.error)}` : "터널 꺼짐";
  if (!t.ready) return "터널 열림 — 주소가 아직 밖에서 안 닿습니다";
  const left = Number(t.idleLeft) || 0;
  // 자동으로 닫히기까지 남은 시간을 **적어 준다** — 안 적으면 「왜 갑자기 끊겼지」가 된다
  return `터널 열림 · 밖에서 닿습니다${left ? ` · ${Math.ceil(left / 60)}분 뒤 자동 닫힘` : ""}`;
}

function rmPaint() {
  const st = rmEl("sRmTunState"); if (st) st.innerHTML = rmTunTxt(RM.tun);
  const go = rmEl("sRmTunGo");
  if (go) go.textContent = (RM.tun && RM.tun.running) ? "터널 끄기" : "터널 켜기";
  const dn = rmEl("sRmDevN");
  if (dn) dn.textContent = RM.devices.length ? `짝지은 기기 ${fmtN(RM.devices.length)}대` : "짝지은 기기 없음";
}

async function rmOp(path, body) {
  if (RM.busy) return null;
  RM.busy = true;
  const r = await api(path, body || {});
  RM.busy = false;
  if (!r || !r.ok) { toast((r && (r.message || r.error)) || "실패했습니다"); return null; }
  if (r.tunnel) RM.tun = r.tunnel;
  if (r.devices) RM.devices = r.devices;
  rmPaint();
  return r;
}

/* 설정 저장은 settings.js 가 한다 — 여기서는 **바꾼 즉시** 보낸다.
   밖으로 나가는 문의 값이라 「저장을 깜빡했는데 열려 있다」가 생기면 안 된다. */
function rmSave(patch) { return api("/api/settings", { settings: patch }); }

function rmBind() {
  const on = rmEl("sRmOn");
  if (on) on.onchange = async () => { await rmSave({ remote_on: on.checked }); toast(on.checked ? "밖에서 접속을 켰습니다" : "밖에서 접속을 껐습니다"); };
  const idle = rmEl("sRmIdle");
  if (idle) idle.oninput = () => { clearTimeout(RM.idleT); RM.idleT = setTimeout(() => rmSave({ remote_idle_min: Number(idle.value) || 0 }), 400); };
  const seg = rmEl("sRmScope");
  if (seg) seg.querySelectorAll("button").forEach((b) => {
    b.onclick = async () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      await rmSave({ remote_scope: b.dataset.v });
      if (b.dataset.v === "run") toast("모바일에서 실행할 수 있게 했습니다 — 실행 1회에 정령의 날개 5개를 씁니다");
    };
  });

  const nk = rmEl("sRmKeyNew");
  if (nk) nk.onclick = async () => {
    const r = await rmOp("/api/remote/key", {});
    if (!r) return;
    rmKeyShow(r.key, false);        // **여기가 키를 보는 유일한 자리다.** 서버는 다시 안 알려 준다
    const s = await api("/api/settings?nocli=1"); if (s && s.settings) rmFill(s.settings);
  };
  /* 직접 정하기 (모비폴리오에 있던 것).
     「인증키 발행」(무작위)은 그대로 두고, 외우기 쉬운 값을 쓰고 싶을 때 이 칸을 쓴다. */
  const kin = rmEl("sRmKeyIn"), km = rmEl("sRmKeyMine");
  if (kin) kin.onkeydown = (e) => { if (e.key === "Enter" && km) { e.preventDefault(); km.click(); } };
  if (km) km.onclick = async () => {
    const v = rmNormKey(kin && kin.value);
    const bad = rmKeyProblem(v, kin);
    if (bad) { toast(bad); if (kin) kin.focus(); return; }
    const r = await rmOp("/api/remote/key", { key: v });
    if (!r) return;
    if (kin) kin.value = "";        // 칸에 남겨 두지 않는다 — 보여 주는 곳은 아래 상자 한 곳
    rmKeyShow(r.key, true);
    toast("인증키를 정했습니다 — 모든 기기가 끊겼습니다");
    const s = await api("/api/settings?nocli=1"); if (s && s.settings) rmFill(s.settings);
  };
  const ck = rmEl("sRmKeyClear");
  if (ck) ck.onclick = async () => {
    const r = await rmOp("/api/remote/key", { clear: true });
    if (!r) return;
    const box = rmEl("sRmKeyBox"); if (box) { box.hidden = true; box.innerHTML = ""; }
    toast("인증키를 지웠습니다 — 모든 기기가 끊겼습니다");
    const s = await api("/api/settings?nocli=1"); if (s && s.settings) rmFill(s.settings);
  };

  const tg = rmEl("sRmTunGo");
  if (tg) tg.onclick = async () => {
    const running = !!(RM.tun && RM.tun.running);
    const r = await rmOp("/api/remote/tunnel", running ? { stop: true } : { start: true });
    if (!r) return;
    if (!running) {
      toast("터널을 엽니다 — 처음 한 번은 cloudflared 를 내려받습니다 (약 52MB)");
      rmPoll();
    }
  };
  const lk = rmEl("sRmLink");
  if (lk) lk.onclick = async () => {
    const r = await rmOp("/api/remote/link", {});
    if (!r) return;
    rmLinkShow(r);
  };
  const dd = rmEl("sRmDevDrop");
  if (dd) dd.onclick = async () => { if (await rmOp("/api/remote/devices", { dropAll: true })) toast("모든 기기를 끊었습니다"); };
}

/* 연결 코드 + QR — **살아 있는 동안만** 보인다 (3분). 지나면 코드도 QR 도 걷는다:
   이미 사라진 코드를 찍게 두면 폰은 「없는 코드」만 본다. */
function rmMmss(sec) { const s = Math.max(0, Math.ceil(sec)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
function rmLinkShow(r) {
  const box = rmEl("sRmLinkBox");
  if (!box) return;
  clearInterval(RM.linkT);
  const until = Date.now() + (Number(r.ttl) || 180) * 1000;
  const site = String(r.site || "").replace(/^https?:\/\//, "").replace(/\/+$/, "");
  // QR 그림은 qr.js 가 **우리 손으로 만든 <svg>** 다 (들어가는 글은 서버가 모양을 거른 코드 주소뿐)
  let qrHtml = "";
  try { if (r.link && window.QR) qrHtml = QR.svg(String(r.link), 132, "#0b1018", "#ffffff"); } catch (e) { qrHtml = ""; }
  box.hidden = false;
  box.innerHTML = `<div class="rmlink">`
    + `<div class="rmlink-t"><span class="h">모바일에서 <b>${esc(site || "우편함 사이트")}</b> 를 열고 이 코드를 넣으세요 — 한 번 읽으면 끝입니다</span>`
    + `<span class="v">${esc(r.code)}</span>`
    + `<span class="h"><b class="rmleft">${esc(rmMmss((until - Date.now()) / 1000))}</b> 뒤 사라집니다</span></div>`
    + (qrHtml ? `<div class="rmqr" title="모바일 카메라로 비추면 코드까지 채워집니다">${qrHtml}<span>QR 스캔</span></div>` : "")
    + `</div>`;
  const tick = () => {
    const left = (until - Date.now()) / 1000;
    const t = box.querySelector(".rmleft");   // 상자 안에서 찾는다 — 그 때만 있는 칸
    if (left > 0) { if (t) t.textContent = rmMmss(left); return; }
    clearInterval(RM.linkT);
    // 지났다 — 코드와 QR 을 **지운다** (숨기기만 하면 DOM 에 남아 캡처·복사된다)
    box.innerHTML = `<span class="h">연결 코드 시간이 지났습니다 — 「연결 코드 받기」를 다시 누르세요</span>`;
  };
  RM.linkT = setInterval(tick, 1000);
}

/* 터널은 여는 데 시간이 걸린다 (내려받기 → 열기 → 주소가 퍼지기). 여는 동안만 짧게 물어본다 —
   평소에는 아무것도 안 부른다. */
function rmPoll() {
  clearTimeout(RM.pollT);
  RM.pollT = setTimeout(async () => {
    const r = await api("/api/remote/tunnel", {});
    if (r && r.tunnel) { RM.tun = r.tunnel; rmPaint(); }
    if (RM.tun && (RM.tun.busy || (RM.tun.running && !RM.tun.ready))) rmPoll();
  }, 3000);
}

async function renderRemote(settings) {
  // 폰(밖)에서는 이 갈래가 감춰져 있고(settings.js) 문을 여닫는 길은 403 이다 — 부르지 않는다
  if (!NET.CAN.remote) return;
  if (settings) rmFill(settings);
  rmBind();
  const r = await api("/api/remote/tunnel", {});
  if (r && r.tunnel) RM.tun = r.tunnel;
  const d = await api("/api/remote/devices", {});
  if (d && d.devices) RM.devices = d.devices;
  rmPaint();
}

Object.assign(window.MW, { renderRemote });
