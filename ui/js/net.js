/* net.js — 전송층. **같은 화면 파일이 이 PC 에서도, 폰에서도 돌게 한다.**
 *
 * 모비폴리오의 `mini/ui/net.js` 를 이식했다. 폰 화면을 따로 두 벌 두면 반드시 갈라지므로
 * 한 벌로 양쪽을 돌린다.
 *
 * ## 어느 쪽인지 어떻게 아는가
 *
 * PC 가 화면을 내줄 때 `__MOBIWORKS_MODE__` 자리에 `local` 을 심는다. 바깥 사이트(CDN)는
 * 그 치환을 하지 않으므로 자리표시자가 그대로 남고, 그것도 「밖」으로 친다.
 * **토큰과 따로 두는 이유**: 개발 모드에서는 토큰이 아예 비어 있어 그것만으로는 안팎을 못 가린다.
 *
 * ## 무엇이 다른가
 *
 *   이 PC — 같은 주소로 그냥 부른다. 열쇠는 실행 토큰(`X-MobiWorks-Token`).
 *   밖    — 우편함에서 받아 둔 PC 주소로 부른다. 열쇠는 로그인해서 받은 쪽지
 *           (`X-MobiWorks-Remote`). **쿠키가 아니라 헤더다** — 아이폰이 3자 쿠키를 막는다
 *           (그쪽이 쿠키로 만들었다가 갈아엎은 자리).
 *
 * ## 밖에서는 아예 감추는 것 (NET.CAN)
 *
 * 이 PC 의 창·프로세스를 건드리는 것 — 오버레이, 업데이트 설치, 터널·인증키, 창 살아있음
 * 신호. 서버도 같은 목록으로 막지만(`REMOTE_NEVER`), **눌러 봐야 안 되는 단추를 보여 줄
 * 이유가 없다.**
 */
(function () {
  "use strict";

  // MODE 는 index.html 의 인라인 <script> 에서 온다 (TOKEN 과 같은 자리).
  // **정적 js 에 두면 안 된다** — 서버가 js 파일은 치환 없이 내주므로 이 PC 도 「밖」이 된다.
  var REMOTE = (function () { try { return MODE !== "local"; } catch (e) { return true; } })();
  var SAVE = "mobiworks.remote";        // {url, token, device, name}
  // 폴리오 화면(`/folio/`, folio/net.js)이 같은 PC·같은 기기라며 **여기서 빌려 간 뒤 제 이름으로 저장**하는 열쇠.
  // 연결을 잊을 때 이것도 같이 지워야 한다 — 한쪽만 지우면 다른 쪽이 도로 빌려 와 「끊었는데 이어져 있다」가 된다.
  var SAVE_FOLIO = "mobiworks.folio.remote";
  var S = {};

  function load() { try { S = JSON.parse(localStorage.getItem(SAVE) || "{}") || {}; } catch (e) { S = {}; } }
  function save() { try { localStorage.setItem(SAVE, JSON.stringify(S)); } catch (e) { /* 못 저장해도 이번 판은 된다 */ } }
  function forget() {
    S = {};
    try { localStorage.removeItem(SAVE); } catch (e) { /* 무시 */ }
    try { localStorage.removeItem(SAVE_FOLIO); } catch (e) { /* 무시 */ }
  }
  load();

  /* ── 우리 것만 지우기 ──────────────────────────────────────────────
     연결이 꼬였을 때 브라우저 자료를 통째로 지우게 할 수는 없다. 이 기기의 브라우저에 **우리 코드가 쓴 것**만 지운다 — 다른 사이트 자료는 안 건드린다.
     무엇이 우리 것인가는 `ui/` 를 `localStorage`·`sessionStorage`·`caches` 로 훑어 정했다 — 연결(PC 주소·쪽지·기기 열쇠,
     두 앱 + 옛 미니 잇기), 테마·레일·보드의 화면 설정, 설정·시트의 「보던 갈래」, 서비스 워커 캐시(ui/sw.js)와 그 등록.
     이름을 하나씩 적지 않고 **앞자리**로 지운다 (아래 OUR_PREFIX) — 새 열쇠가 생겨도 빠지지 않게. 우리 코드가 쓰는 열쇠가
     전부 이 앞자리에 들어가는지는 tests/test_phone_reads_pc.py 가 `ui/` 를 훑어 대조한다.
     쿠키는 쓰지 않는다 (훑어서 확인). */
  var OUR_PREFIX = ["mobiworks.", "mobifolio.", "mw.", "mf."];
  var OUR_CACHE = "mobiworks-shell-";
  function ours(k) { for (var i = 0; i < OUR_PREFIX.length; i++) if (String(k).indexOf(OUR_PREFIX[i]) === 0) return true; return false; }
  function dropStore(st) {
    var keys = [];
    try { for (var i = 0; i < st.length; i++) keys.push(st.key(i)); } catch (e) { return; }
    for (var j = 0; j < keys.length; j++) if (keys[j] && ours(keys[j])) { try { st.removeItem(keys[j]); } catch (e) { /* 무시 */ } }
  }
  /** PC 의 쪽지를 끝낸다 — **되는 대로만** (PC 가 꺼졌거나 주소가 죽었어도 이 기기 것은 지운다). 죽은 주소에 매달리지 않게 4초. */
  async function logout() {
    if (!REMOTE || !S.url || !S.token) return false;
    var ac = typeof AbortController === "function" ? new AbortController() : null;
    var t = ac ? setTimeout(function () { ac.abort(); }, 4000) : 0;
    try {
      await fetch(S.url + "/api/remote/logout", { method: "POST", mode: "cors", signal: ac ? ac.signal : undefined,
        headers: { "Content-Type": "application/json", "X-Requested-With": "mobiworks", "X-MobiWorks-Remote": S.token }, body: "{}" });
      return true;
    } catch (e) { return false; }
    finally { if (t) clearTimeout(t); }
  }
  /** 우리 것만 전부 지우고 잇기 화면(`/`)으로. 배너의 「연결 정보 지우고 처음으로」가 부른다. */
  async function wipe() {
    await logout();
    forget();
    try { dropStore(localStorage); } catch (e) { /* 사생활 모드 */ }
    try { dropStore(sessionStorage); } catch (e) { /* 무시 */ }
    try {
      if (typeof caches !== "undefined" && caches.keys) {
        var ks = await caches.keys();
        for (var i = 0; i < ks.length; i++) if (ks[i].indexOf(OUR_CACHE) === 0) { try { await caches.delete(ks[i]); } catch (e) { /* 무시 */ } }
      }
    } catch (e) { /* 무시 */ }
    try {
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
        var rs = await navigator.serviceWorker.getRegistrations();
        for (var j = 0; j < rs.length; j++) { try { await rs[j].unregister(); } catch (e) { /* 무시 */ } }
      }
    } catch (e) { /* 무시 */ }
    location.replace("/");                 // 뒤로 가기로 돌아오지 못하게 (폴리오 「연결 끊기」와 같다)
  }
  /** 연결 끊기 — 폴리오 `btnOut` 과 같은 일: 쪽지를 끝내고 이 기기의 연결(주소·쪽지·기기 열쇠)을 지우고 잇기 화면으로.
   *  화면 설정(테마 등)은 남긴다 — 그것까지 지우는 것은 `wipe`. */
  async function disconnect() {
    await logout();
    forget();
    location.replace("/");
  }

  function tokenOf() { try { return typeof TOKEN === "string" ? TOKEN : ""; } catch (e) { return ""; } }

  // core.js 의 esc 는 아직 없다 (net.js 가 먼저 실린다) — 여기서 쓰는 만큼만 둔다
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function headers(json) {
    var h = { "X-Requested-With": "mobiworks" };
    if (json) h["Content-Type"] = "application/json";
    if (REMOTE) h["X-MobiWorks-Remote"] = S.token || "";
    else h["X-MobiWorks-Token"] = tokenOf();
    return h;
  }

  function url(path) { return (REMOTE ? (S.url || "") : "") + path; }

  /** 쪽지가 만료되면 기기 열쇠로 조용히 다시 들어간다 (12시간마다 키를 다시 치지 않게). */
  async function relogin() {
    if (!REMOTE || !S.url || !S.device) return false;
    try {
      var r = await fetch(S.url + "/api/remote/login", {
        method: "POST", mode: "cors",
        headers: { "Content-Type": "application/json", "X-Requested-With": "mobiworks" },
        body: JSON.stringify({ device: S.device }),
      });
      var j = await r.json();
      if (!j || !j.token) return false;
      S.token = j.token;
      save();
      return true;
    } catch (e) { return false; }
  }

  /** PC 에 인증키로 들어간다.
   *  **갓 연 터널 주소는 이름이 퍼지기까지 수십 초** 걸려 브라우저가 아예 못 찾는다
   *  (ERR_NAME_NOT_RESOLVED). 그동안 몇 번 더 두드린다 — 한 번 실패로 접으면
   *  사람은 코드를 다시 받으러 가고, 우편함 코드는 한 번 읽히면 사라져 코드만 탄다. */
  async function login(pcUrl, key, onWait) {
    for (var i = 0; i < 12; i++) {
      try {
        var r = await fetch(pcUrl + "/api/remote/login", {
          method: "POST", mode: "cors",
          headers: { "Content-Type": "application/json", "X-Requested-With": "mobiworks" },
          body: JSON.stringify({ key: key, name: "모바일" }),
        });
        return await r.json();      // 답이 왔으면(틀린 키라도) 그대로 돌려준다
      } catch (e) {
        if (onWait) onWait(i + 1, 12);
        await new Promise(function (res) { setTimeout(res, 4000); });
      }
    }
    return { ok: false, message: "PC 를 찾지 못했습니다. 터널이 켜져 있는지 보세요." };
  }

  /* ── 우편함 봉투 (보안 감사 ④-a) ──
     PC 는 터널 주소를 **인증키로 봉해서** 우편함에 넣는다 (`mailseal.py`). 여는 코드는 `ui/seal.js` 한 곳에
     있고 **잇기 때만** 불러 온다 — 이 PC 화면에서는 한 번도 안 쓰인다. */
  var sealLoad = null;
  function needSeal() {
    if (window.MWSeal) return Promise.resolve(window.MWSeal);
    if (!sealLoad) {
      sealLoad = new Promise(function (ok, bad) {
        var sc = document.createElement("script");
        sc.src = "/seal.js";            // **절대 경로** — 폴리오 화면(/folio/)도 같은 파일을 쓴다
        sc.onload = function () { window.MWSeal ? ok(window.MWSeal) : bad(new Error("seal")); };
        sc.onerror = function () { sealLoad = null; bad(new Error("seal")); };
        document.head.appendChild(sc);
      });
    }
    return sealLoad;
  }

  /** 우편함에서 봉투를 꺼내 인증키로 연다. **열리면 곧바로 저장한다** (로그인보다 먼저 — 실패해도 주소는 남는다).
   *  답의 `error:"legacy"` 는 봉하지 않은 주소 — 모비폴리오 단독판 PC 다 (부르는 쪽이 옛 화면으로 넘긴다). */
  async function takeBox(code, key) {
    var seal;
    try { seal = await needSeal(); } catch (e) {
      return { ok: false, message: "잇기 도구를 못 읽었습니다. 새로 고친 뒤 다시 해 보세요." };
    }
    var got = await seal.takeBox(code, key);
    if (!got.ok) return got;
    S = { url: got.url };
    save();
    return { ok: true, url: got.url };
  }

  /** 사람이 친 인증키를 PC(`store.norm_remote_key`)와 같은 모양으로 — 봉투는 이 모양으로만 열린다. */
  function normKey(v) { return String(v || "").trim().toUpperCase().replace(/[\s-]/g, ""); }

  /* ── 잇기 화면 (밖에서, 아직 안 이어졌을 때) ──
     화면 코드는 아무것도 몰라도 된다. 이어지면 그 자리에서 다시 읽어 평소대로 뜬다. */
  var PAIR_CSS =
    "#mwpair{position:fixed;inset:0;z-index:99999;background:var(--bg,#0b0f16);color:var(--fg,#e9ecf1);overflow:auto;" +
    "font:16px/1.45 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}" +
    "#mwpair .in{max-width:520px;margin:0 auto;padding:calc(env(safe-area-inset-top,0px) + 22px) 18px 24px}" +
    "#mwpair h1{font-size:19px;margin:0 0 4px}" +
    "#mwpair p{color:var(--sub,#8b97a6);font-size:13px;margin:0 0 18px}" +
    "#mwpair .c{background:var(--card,#121a26);border:1px solid var(--line3,#23303f);border-radius:14px;padding:16px;margin-bottom:12px}" +
    "#mwpair label{display:block;font-size:13px;color:var(--sub,#8b97a6);margin:0 0 6px}" +
    "#mwpair input{width:100%;box-sizing:border-box;background:var(--inset,#0b0f16);border:1px solid var(--line3,#23303f);" +
    "border-radius:10px;padding:14px;color:var(--fg,#e9ecf1);font:700 26px/1.2 Consolas,monospace;" +
    "letter-spacing:.16em;text-align:center;text-transform:uppercase}" +
    "#mwpair button{display:block;width:100%;padding:16px;border:0;border-radius:12px;background:var(--gold,#e2b866);" +
    "color:var(--ongold,#1a1608);font-weight:700;font-size:16px;margin-top:12px;cursor:pointer}" +
    "#mwpair button[disabled]{opacity:.45}" +
    "#mwpair .ghost{background:none;border:1px solid var(--line3,#23303f);color:var(--sub,#8b97a6);font-size:14px;padding:13px}" +
    "#mwpair .m{margin-top:12px;padding:12px;border-radius:10px;font-size:14px;display:none}" +
    "#mwpair .m.on{display:block;background:var(--badbg,#4a2330);color:var(--bad2,#f5b8c8)}" +
    "#mwpair .m.go{display:block;background:var(--okbg,#17301f);color:var(--ok,#7ee6a0)}" +
    "#mwpair .risk{margin-top:16px;padding:11px 13px;border:1px solid var(--badline,#4a2330);border-radius:10px;" +
    "color:var(--bad,#e08a9e);font-size:12px;line-height:1.6;word-break:keep-all}";

  function pairScreen() {
    var st = document.createElement("style");
    st.textContent = PAIR_CSS;
    document.head.appendChild(st);

    // PC 가 띄운 QR 을 찍고 들어오면 주소에 코드가 실려 온다 (`/?c=…`) — 그대로 채워 준다.
    // 모양을 거른 뒤에만 칸에 넣는다 (innerHTML 로 들어간다). 폴리오 쪽(`folio/net.js`)과 같은 규칙.
    var fromQr = "";
    try { fromQr = (new URLSearchParams(location.search).get("c") || "").trim().toUpperCase(); } catch (e) { /* 옛 브라우저 */ }
    if (!/^[A-Z0-9]{8}$/.test(fromQr)) fromQr = "";
    // 이 사이트는 전에 **모비폴리오 폰 화면**이었다. 그때 이어 둔 연결이 있으면 그리로 가는 길을 남긴다.
    var legacy = false;
    try { var lg = JSON.parse(localStorage.getItem("mobifolio.remote") || "{}") || {}; legacy = Boolean(lg.url && (lg.token || lg.device)); } catch (e) { legacy = false; }
    var half = Boolean(S.url);          // 주소는 이미 받아 뒀다 — 코드는 다시 필요 없다
    var box = document.createElement("div");
    box.id = "mwpair";
    box.innerHTML =
      '<div class="in">' +
      // **이름을 여기 또 적지 않는다.** 앱 이름은 index.html 의 <title> 하나가 기준이다
      // (core.js 의 APP_TITLE 과 같은 규칙 — 다만 net.js 는 core 보다 먼저 실려 직접 읽는다).
      '<h1>' + esc(document.title) + ' — 모바일에서 잇기</h1>' +
      '<p>PC 에서 <b>설정 → 밖에서 접속</b> 으로 가서 「연결 코드 받기」를 누르세요.</p>' +
      '<div class="c" id="mwc"' + (half ? ' style="display:none"' : "") + ">" +
      '<label for="mwcode">연결 코드 (8자) — 3분 뒤 사라집니다</label>' +
      '<input id="mwcode" maxlength="8" placeholder="A2K9PQ7M" autocapitalize="characters" ' +
      'autocomplete="off" autocorrect="off" spellcheck="false" inputmode="latin" value="' + fromQr + '"></div>' +
      '<div class="c"><label for="mwkey">인증키 (8자 이상) — PC 설정에서 발행합니다</label>' +
      '<input id="mwkey" maxlength="32" placeholder="K7M2XQ4P" autocapitalize="characters" ' +
      'autocomplete="off" autocorrect="off" spellcheck="false" inputmode="latin"></div>' +
      '<button id="mwgo">' + (half ? "다시 잇기" : "잇기") + "</button>" +
      '<div class="m" id="mwmsg"></div>' +
      '<button class="ghost" id="mwdrop">저장된 연결 지우기</button>' +
      (legacy ? '<button class="ghost" id="mwlegacy">모비폴리오 PC 에 이어 둔 연결로 가기</button>' : "") +
      '<div class="risk">모바일에서 <b>실행</b>을 누르면 이 PC 가 게임을 조작하고 ' +
      '<b>정령의 날개를 실제로 씁니다</b> (실행 1회에 5개). 처음에는 「보기만」으로 열려 있고, ' +
      'PC 에서 범위를 올려야 실행할 수 있습니다.</div></div>';
    document.body.appendChild(box);

    // 소문자로 쳐도 같게 본다 — **칸 안의 값까지 대문자로 맞춘다.** 보기만 대문자로 해 두면
    // 적은 값과 보이는 값이 달라 헷갈린다.
    ["#mwcode", "#mwkey"].forEach(function (sel) {
      var el = box.querySelector(sel);
      if (!el) return;
      el.addEventListener("input", function () {
        var at = el.selectionStart, up = el.value.toUpperCase();
        if (up === el.value) return;
        el.value = up;
        try { el.setSelectionRange(at, at); } catch (e) { /* 안 되면 그만 */ }
      });
    });

    var msg = box.querySelector("#mwmsg");
    function say(t, bad) { msg.textContent = t; msg.className = "m " + (bad ? "on" : "go"); }
    if (fromQr) {
      // 코드는 채워졌다 — 인증키 칸에 손이 가게 두고, **주소창에서는 코드를 지운다**
      // (새로 고치거나 주소를 남에게 보여 줘도 코드가 남지 않게).
      setTimeout(function () { var k = box.querySelector("#mwkey"); if (k) k.focus(); }, 60);
      try { history.replaceState(null, "", location.pathname); } catch (e) { /* 무시 */ }
    }

    box.querySelector("#mwdrop").onclick = function () { forget(); location.reload(); };
    var lgb = box.querySelector("#mwlegacy");
    if (lgb) lgb.onclick = function () { location.href = "/mobifolio/"; };

    box.querySelector("#mwgo").onclick = async function () {
      var go = box.querySelector("#mwgo");
      var key = normKey(box.querySelector("#mwkey").value);
      var code = (box.querySelector("#mwcode").value || "").trim().toUpperCase();
      if (key.length < 8) return say("인증키는 8자 이상입니다.", true);
      if (!/^[A-Z0-9]+$/.test(key)) return say("인증키는 영문과 숫자만 씁니다.", true);
      if (!S.url && !/^[A-Z0-9]{8}$/.test(code)) return say("연결 코드는 8자입니다.", true);
      go.disabled = true;
      try {
        if (!S.url) {
          say("우편함에서 받아 인증키로 여는 중…");
          var got = await takeBox(code, key);
          if (!got.ok && got.error === "legacy") {
            // 봉하지 않은 주소 = 모비폴리오 단독판 PC(→ /mobifolio/, 거기서 인증키만 다시 친다)
            // 또는 봉투를 모르는 옛 모비웍스(→ 업데이트 안내). 가르는 것은 seal.js 의 legacy().
            return window.MWSeal.legacy(got.url, say);
          }
          if (!got.ok) return say(got.message, true);
        }
        say("PC 에 들어가는 중…");
        var j = await login(S.url, key, function (i, n) {
          say("PC 주소가 퍼지기를 기다리는 중… (" + i + "/" + n + ")");
        });
        if (!j || !j.ok) {
          // 키가 틀렸거나 잠겼으면 **주소까지 버린다** — 코드를 다시 받아야 한다
          if (j && (j.error === "bad_key" || j.error === "locked")) forget();
          return say((j && j.message) || "들어가지 못했습니다.", true);
        }
        if (j.app && j.app !== "mobiworks") {
          // 봉투가 열렸으니 여기 올 일은 없지만(봉투에 앱 이름이 있다), 답이 다른 앱이면 짝을 남기지 않는다
          forget();
          return say(document.title + " PC 가 아닙니다.", true);
        }
        S = { url: S.url, token: j.token, device: j.device || "", name: j.name || "" };
        save();
        say("이어졌습니다.");
        location.reload();          // 평소 화면으로 다시 뜬다
      } finally {
        go.disabled = false;
      }
    };
  }

  function paired() { return !REMOTE || Boolean(S.url && (S.token || S.device)); }

  /** 밖인데 아직 안 이어졌으면 화면 대신 잇기를 띄운다. */
  function guard() {
    if (paired()) return false;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", pairScreen);
    else pairScreen();
    return true;
  }

  window.NET = {
    REMOTE: REMOTE,
    url: url,
    headers: headers,
    relogin: relogin,
    login: login,
    takeBox: takeBox,
    forget: forget,
    logout: logout,
    disconnect: disconnect,
    wipe: wipe,
    paired: paired,
    session: function () { return S; },
    onLost: null,
    // 밖에서는 **이 PC 의 창·프로세스**를 건드리는 것을 아예 감춘다
    CAN: {
      overlay: !REMOTE,     // 게임 위 오버레이 — 이 PC 의 창
      update: !REMOTE,      // 업데이트 확인·설치
      remote: !REMOTE,      // 터널·인증키·우편함 — 문 자체를 여닫는 길
      hold: !REMOTE,        // 창 살아있음 신호 (밖에서 보내면 PC 앱이 꺼진다)
      quit: !REMOTE,
      files: !REMOTE,       // 백업 zip·복원·문제 신고 zip — 이 PC 의 파일 (서버도 REMOTE_NEVER)
    },
  };
  // index.html 아래쪽의 로드 점검이 파일마다 대표 함수 하나를 확인한다 — 이 파일 몫.
  // 다른 파일과 같은 모양으로 내놓는다 (`tests/test_blank_screen.py` 가 그 모양을 본다).
  function netReady() { return true; }
  window.MW = window.MW || {};
  Object.assign(window.MW, { netReady: netReady });
  // 밖인데 열쇠가 없으면 여기서 멈춘다 — 화면 코드는 그대로 두고 위에 잇기를 덮는다
  window.NET.blocked = guard();
})();
