/* 전송층 — 미니 화면 셋(index · settings · greet)이 **PC 안에서도 밖에서도** 같은 파일로 돌게 한다.
 *
 * 어떻게 어느 쪽인지 아는가:
 *   PC 가 화면 파일을 내줄 때 `__MOBIWORKS_MODE__` 자리에 local/remote 를 심는다.
 *   폰 앱 쪽(클라우드플레어)은 그 치환을 하지 않으므로 자리표시자가 남고, 그것도 「밖」으로 친다.
 *   토큰과 따로 둔 이유: 개발 모드에서는 토큰이 아예 비어 있어 그것만으로는 안팔을 못 가린다.
 *
 * 무엇이 다른가:
 *   PC 안 — 같은 주소로 그냥 부른다. 열쇠는 실행 토큰(X-MobiWorks-Token).
 *   밖    — 우편함에서 받아 둔 PC 주소로 부른다. 열쇠는 로그인해서 받은 쪽지(X-MobiWorks-Remote).
 *          아이폰은 남의 쿠키를 막으므로 쪽지는 **헤더로** 보낸다.
 *
 * 무엇이 밖에서는 안 되는가 (MF.CAN):
 *   이 PC 의 창·프로세스·파일을 건드리는 것 — 오버레이, 연출 창, 업데이트 설치, 터널, 커버 고르기,
 *   창 살아있음 신호. 서버도 같은 목록으로 막지만(REMOTE_NEVER), 화면에서도 아예 감춘다.
 */
(function () {
  "use strict";

  var TOKEN = "__MOBIWORKS_TOKEN__";
  var MODE = "__MOBIWORKS_MODE__";
  // PC 가 서빙할 때만 위 두 자리가 채워진다. 「local」이 아니면 전부 밖이다
  // (폰 앱 쪽은 치환을 안 하므로 자리표시자가 그대로 남는다).
  var REMOTE = MODE !== "local";
  var SAVE = "mobiworks.folio.remote";        // {url, token, device, name}
  var S = {};

  function load() {
    try { S = JSON.parse(localStorage.getItem(SAVE) || "{}") || {}; } catch (e) { S = {}; }
    // 큐 화면(`/`, `mobiworks.remote`)에서 이미 이었으면 **같은 PC·같은 기기**다 — 코드를 또 받게 하지 않는다.
    // 기기 열쇠(device)만 빌려 온다. 쪽지(token)는 relogin 이 새로 받는다.
    if (REMOTE && !S.url) {
      try {
        var q = JSON.parse(localStorage.getItem("mobiworks.remote") || "{}") || {};
        if (q.url && q.device) S = { url: q.url, device: q.device, name: q.name || "" };
      } catch (e) { /* 무시 */ }
    }
  }
  function save() {
    try { localStorage.setItem(SAVE, JSON.stringify(S)); } catch (e) { /* 못 저장해도 이번 판은 된다 */ }
  }
  function forget() {
    S = {};
    try { localStorage.removeItem(SAVE); } catch (e) { /* 무시 */ }
    // 큐 화면에서 빌려 온 열쇠(`mobiworks.remote`)도 같이 — 제 것만 지우면 다음 `load()` 가 도로 빌려 와
    // 「연결 끊기」를 눌렀는데 이어져 있었다. 같은 PC·같은 기기이므로 끊는 것도 한 번이다.
    try { localStorage.removeItem("mobiworks.remote"); } catch (e) { /* 무시 */ }
  }
  load();

  /* ── 부르기 ─────────────────────────────────────────────────────────────── */
  function headers(json) {
    var h = { "X-Requested-With": "mobiworks" };
    if (json) h["Content-Type"] = "application/json";
    // 쪽지 헤더 이름은 **서버(server.py `_remote_tok`)와 같아야 한다.** 옛 철자
    // (X-MobiFolio-Remote)로는 밖에서 한 번도 문을 못 지났다 — 403 → 다시 로그인 → 403 의 되돌이.
    if (REMOTE) h["X-MobiWorks-Remote"] = S.token || "";
    else h["X-MobiWorks-Token"] = TOKEN;
    return h;
  }

  // **API 길에는 `/api/folio` 를 앞에 붙인다.** 통합 뒤에 그쪽 길과 이름이 겹치기
  // 때문이다 — `/api/queue` 가 작업 큐(모비웍스)와 재생목록(여기) 둘 다이다.
  // 화면 34곳을 고치는 대신 **길을 만드는 이 한 곳**에서 붙인다.
  function url(path) {
    var p = path.indexOf("/api/") === 0 ? "/api/folio/" + path.slice(5) : path;
    return (REMOTE ? (S.url || "") : "") + p;
  }

  async function raw(path, body) {
    var init = {
      method: body === undefined ? "GET" : "POST",
      headers: headers(true),
      body: body === undefined ? undefined : JSON.stringify(body),
    };
    if (REMOTE) init.mode = "cors";
    var r = await fetch(url(path), init);
    var j = null;
    try { j = JSON.parse(await r.text()); } catch (e) { j = null; }
    if (j && typeof j === "object") {
      if (!("ok" in j)) j.ok = r.ok;
      j.status = r.status;
      return j;
    }
    return { ok: r.ok, status: r.status, error: r.ok ? "" : "bad_answer" };
  }

  /** 쪽지가 만료되면 기기 열쇠로 조용히 다시 들어간다 (12시간마다 손대지 않게). */
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

  /** 화면들이 쓰는 그 함수. 답은 언제나 객체다 — 끊겨도 터지지 않는다. */
  async function api(path, body) {
    // 밖인데 아직 안 이어졌으면(잇기 화면) 부르지 않는다 — 주소가 없어 사이트 제 주소로 나가 404 만 쌓였다
    if (REMOTE && !MF.paired()) return { ok: false, error: "unpaired", message: "아직 PC 와 이어지지 않았습니다." };
    try {
      var j = await raw(path, body);
      if (REMOTE && (j.status === 401 || j.status === 403) && await relogin()) {
        j = await raw(path, body);
      }
      if (REMOTE && (j.status === 401 || j.status === 403) && !S.device) {
        // 더는 들어갈 길이 없다 — 잇기 화면으로 돌려보낸다
        forget();
        if (MF.onLost) MF.onLost();
      }
      return j;
    } catch (e) {
      return { ok: false, error: "network", message: String((e && e.message) || e) };
    }
  }

  /* ── 잇기 (밖에서만) ────────────────────────────────────────────────────── */

  /** PC 에 인증키로 들어간다.
   *  갓 연 터널 주소는 **이름이 퍼지기까지 수십 초** 걸려 브라우저가 아예 못 찾는다
   *  (ERR_NAME_NOT_RESOLVED). 그동안 몇 번 더 두드린다. */
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

  /* 우편함 봉투 (보안 감사 ④-a) — PC 는 주소를 **인증키로 봉해** 넣는다. 여는 코드는 큐 화면과 같은
     `/seal.js`(ui/seal.js) 한 곳에 있고 잇기 때만 불러 온다 (두 벌을 두지 않는다). */
  var sealLoad = null;
  function needSeal() {
    if (window.MWSeal) return Promise.resolve(window.MWSeal);
    if (!sealLoad) {
      sealLoad = new Promise(function (ok, bad) {
        var sc = document.createElement("script");
        sc.src = "/seal.js";
        sc.onload = function () { window.MWSeal ? ok(window.MWSeal) : bad(new Error("seal")); };
        sc.onerror = function () { sealLoad = null; bad(new Error("seal")); };
        document.head.appendChild(sc);
      });
    }
    return sealLoad;
  }

  /** 우편함에서 봉투를 꺼내 인증키로 연다. 열리면 **곧바로 저장한다**.
   *  `error:"legacy"` — 봉하지 않은 주소, 곧 모비폴리오 단독판 PC (부르는 쪽이 옛 화면으로 넘긴다). */
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


  /* ── 잇기 화면 (밖에서, 아직 안 이어졌을 때) ─────────────────────────────────
   * 화면 셋이 같은 파일을 쓰므로 잇기도 여기 한 곳에 둔다. 화면 쪽 코드는 아무것도
   * 몰라도 된다 — 이어지면 그 자리에서 다시 읽어 평소대로 뜬다.
   */
  var PAIR_CSS =
    "#mfpair{position:fixed;inset:0;z-index:99999;background:var(--bg,#0b0f16);color:var(--fg,#e9ecf1);overflow:auto;" +
    "font:16px/1.45 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}" +
    "#mfpair .in{max-width:520px;margin:0 auto;padding:calc(env(safe-area-inset-top,0px) + 22px) 18px 24px}" +
    "#mfpair h1{font-size:19px;margin:0 0 4px}" +
    "#mfpair p{color:var(--sub,#8b97a6);font-size:13px;margin:0 0 18px}" +
    "#mfpair .c{background:var(--card,#121a26);border:1px solid var(--line3,#23303f);border-radius:14px;padding:16px;margin-bottom:12px}" +
    "#mfpair label{display:block;font-size:13px;color:var(--sub,#8b97a6);margin:0 0 6px}" +
    "#mfpair input{width:100%;box-sizing:border-box;background:var(--inset,#0b0f16);border:1px solid var(--line3,#23303f);" +
    "border-radius:10px;padding:14px;color:var(--fg,#e9ecf1);font:700 26px/1.2 Consolas,monospace;" +
    "letter-spacing:.16em;text-align:center;text-transform:uppercase}" +
    "#mfpair button{display:block;width:100%;padding:16px;border:0;border-radius:12px;background:var(--gold,#e2b866);" +
    "color:var(--ongold,#1a1608);font-weight:700;font-size:16px;margin-top:12px;cursor:pointer}" +
    "#mfpair button[disabled]{opacity:.45}" +
    "#mfpair .ghost{background:none;border:1px solid var(--line3,#23303f);color:var(--sub,#8b97a6);font-size:14px;padding:13px}" +
    "#mfpair .m{margin-top:12px;padding:12px;border-radius:10px;font-size:14px;display:none}" +
    "#mfpair .m.on{display:block;background:var(--badbg,#4a2330);color:var(--bad2,#f5b8c8)}" +
    "#mfpair .m.go{display:block;background:var(--okbg,#17301f);color:var(--ok,#7ee6a0)}" +
    "#mfpair .risk{margin-top:16px;padding:11px 13px;border:1px solid var(--badline,#4a2330);border-radius:10px;" +
    "color:var(--bad,#e08a9e);font-size:12px;line-height:1.6;word-break:keep-all}";

  // 잇기 화면 제목에 쓰는 만큼만 (innerHTML 로 들어간다) — 작업 쪽 net.js 와 같은 모양
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pairScreen() {
    var st = document.createElement("style");
    st.textContent = PAIR_CSS;
    document.head.appendChild(st);

    // PC 가 띄운 QR 을 찍고 들어오면 주소에 코드가 실려 온다 (?c=…) — 그대로 채워 준다.
    var fromQr = "";
    try {
      fromQr = (new URLSearchParams(location.search).get("c") || "").trim().toUpperCase();
    } catch (e) { /* 옛 브라우저면 그냥 손으로 친다 */ }
    if (!/^[A-Z0-9]{8}$/.test(fromQr)) fromQr = "";
    var half = Boolean(S.url);          // 주소는 이미 받아 뒀다 — 코드는 다시 필요 없다
    var box = document.createElement("div");
    box.id = "mfpair";
    window.MF_PAIRING = true;          // 화면의 쪽지(toast)는 잇기 화면이 떠 있는 동안 조용히 (index.html)
    box.innerHTML =
      // 두 앱의 잇기 화면은 **같은 PC · 같은 우편함**이다 — 제목·말·색을 작업 화면(ui/js/net.js)과 맞춘다.
      '<div class="in"><h1>' + esc(document.title) + ' — 모바일에서 잇기</h1>' +
      '<p>PC 에서 <b>설정 → 밖에서 접속</b> 으로 가서 「연결 코드 받기」를 누르세요.</p>' +
      '<div class="c" id="mfc"' + (half ? ' style="display:none"' : '') + '>' +
      '<label for="mfcode">연결 코드 (8자) — 3분 뒤 사라집니다</label>' +
      '<input id="mfcode" maxlength="8" placeholder="A2K9PQ7M" autocapitalize="characters" ' +
      'autocomplete="off" autocorrect="off" spellcheck="false" inputmode="latin" ' +
      'value="' + fromQr + '"></div>' +
      // 인증키는 **8자 이상 32자까지** (store.REMOTE_KEY_MIN/MAX — 모비웍스는 재화가 걸려 8 로 올렸다).
      // 「7자」로 못 박아 두면 PC 가 발행한 8자 키를 **아예 넣을 수 없다.**
      '<div class="c"><label for="mfkey">인증키 (8자 이상) — PC 설정에서 발행합니다</label>' +
      '<input id="mfkey" maxlength="32" placeholder="K7M2XQ4P" autocapitalize="characters" ' +
      'autocomplete="off" autocorrect="off" spellcheck="false" inputmode="latin"></div>' +
      '<button id="mfgo">' + (half ? "다시 잇기" : "잇기") + '</button>' +
      '<div class="m" id="mfmsg"></div>' +
      '<button class="ghost" id="mfdrop">저장된 연결 지우기</button>' +
      '<div class="risk">이 기능이 게임 운영사가 <b>허가한 사용 방식인지는 확인되지 않았습니다.</b> ' +
      '운영사는 <b>모든 책임이 사용자에게 있다</b>고 밝히고 있습니다 — 계정 제재를 포함해 ' +
      '일어날 수 있는 일은 본인 책임입니다.</div></div>';
    document.body.appendChild(box);

    // 소문자로 쏠든 대문자로 쏠든 같게 본다 — 칸 안의 값까지 대문자로 맞춘다.
    // (보기만 대문자로 해 두면 적은 값과 보이는 값이 달라 헷갈린다.)
    ["#mfcode", "#mfkey"].forEach(function (sel) {
      var el = box.querySelector(sel);
      if (!el) return;
      el.addEventListener("input", function () {
        var at = el.selectionStart, up = el.value.toUpperCase();
        if (up === el.value) return;
        el.value = up;
        try { el.setSelectionRange(at, at); } catch (e) { /* 안 되면 그만 */ }
      });
    });

    var msg = box.querySelector("#mfmsg");
    if (fromQr) {
      // 코드는 이미 채워졌다 — 바로 인증키 칸에 손이 가게 두고, 주소에서는 코드를 지운다
      // (새로고침하거나 남에게 주소를 보여 줘도 코드가 남지 않게).
      setTimeout(function () { var k = box.querySelector("#mfkey"); if (k) k.focus(); }, 60);
      try { history.replaceState(null, "", location.pathname); } catch (e) { /* 무시 */ }
    }
    function say(t, bad) { msg.textContent = t; msg.className = "m " + (bad ? "on" : "go"); }

    box.querySelector("#mfdrop").onclick = function () {
      forget();
      location.reload();
    };

    box.querySelector("#mfgo").onclick = async function () {
      var go = box.querySelector("#mfgo");
      // PC(`store.norm_remote_key`)와 같은 모양으로 — 봉투는 이 모양으로만 열린다
      var key = (box.querySelector("#mfkey").value || "").trim().toUpperCase().replace(/[\s-]/g, "");
      var code = (box.querySelector("#mfcode").value || "").trim().toUpperCase();
      if (key.length < 8 || key.length > 32) return say("인증키는 8자 이상 32자까지입니다.", true);
      if (!/^[A-Z0-9]+$/.test(key)) return say("인증키는 영문과 숫자만 씁니다.", true);
      if (!S.url && !/^[A-Z0-9]{8}$/.test(code)) return say("코드는 8자입니다.", true);
      go.disabled = true;
      try {
        if (!S.url) {
          say("우편함에서 받아 인증키로 여는 중…");
          var got = await takeBox(code, key);
          if (!got.ok && got.error === "legacy") {
            return window.MWSeal.legacy(got.url, say);
          }
          if (!got.ok) return say(got.message, true);
        }
        say("PC 에 들어가는 중…");
        var j = await login(S.url, key, function (i, n) {
          say("PC 주소가 퍼지기를 기다리는 중… (" + i + "/" + n + ")");
        });
        if (!j || !j.ok) {
          if (j && (j.error === "bad_key" || j.error === "locked")) forget();
          return say((j && j.message) || "들어가지 못했습니다.", true);
        }
        if (j.app && j.app !== "mobiworks") { forget(); return say("모비웍스 PC 가 아닙니다.", true); }
        S = { url: S.url, token: j.token, device: j.device || "", name: j.name || "" };
        save();
        say("이어졌습니다.");
        location.reload();          // 평소 화면으로 다시 뜬다
      } finally {
        go.disabled = false;
      }
    };
  }

  /** 밖인데 아직 안 이어졌으면 화면 대신 잇기를 띄운다. */
  function guard() {
    if (!REMOTE || MF.paired()) return false;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", pairScreen);
    } else {
      pairScreen();
    }
    return true;
  }


  /* ── 물어보는 창 ─────────────────────────────────────────────────────────────
   * 브라우저 기본 confirm·prompt 는 앱과 따로 놀고, 폰에서는 주소까지 같이 뜬다.
   * 바깥 꾸러미(SweetAlert 등)는 CSP 가 외부 스크립트를 막아 못 쓴다 — 그래서 직접 만든다.
   * 색은 화면의 변수를 그대로 쓰고, 없으면 어두운 기본값으로 떨어진다(잇기 화면처럼 홀로 뜰 때).
   *
   *   MF.ask({title, text, ok, cancel, danger})        → true/false
   *   MF.ask({... , input:"처음값", placeholder:"..."}) → 적은 글 (취소하면 null)
   */
  var DLG_CSS =
    "#mfdlg{position:fixed;inset:0;z-index:99998;display:flex;align-items:center;justify-content:center;" +
    "padding:20px;background:var(--backdrop,rgba(0,0,0,.55));backdrop-filter:blur(2px);" +
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;" +
    "animation:mfdlgIn .12s ease-out}" +
    "@keyframes mfdlgIn{from{opacity:0}to{opacity:1}}" +
    "@keyframes mfdlgPop{from{transform:translateY(6px) scale(.98);opacity:0}to{transform:none;opacity:1}}" +
    "#mfdlg .box{width:100%;max-width:340px;border-radius:14px;padding:18px 18px 14px;" +
    "background:var(--bar,#121a26);border:1px solid var(--line,#26303d);color:var(--ink,#e9ecf1);" +
    "box-shadow:0 18px 50px var(--shadow,rgba(0,0,0,.5));animation:mfdlgPop .14s ease-out}" +
    "#mfdlg h3{margin:0 0 6px;font-size:15px;color:var(--title,#f4e7c8);word-break:keep-all}" +
    "#mfdlg p{margin:0;font-size:13px;line-height:1.6;color:var(--row-sub,#8b97a6);word-break:keep-all;" +
    "white-space:pre-wrap}" +
    "#mfdlg input{width:100%;box-sizing:border-box;margin-top:12px;padding:10px 12px;border-radius:9px;" +
    "background:var(--inset,#111a26);border:1px solid var(--ctl,#23303f);color:var(--ink,#e9ecf1);font:inherit}" +
    "#mfdlg input:focus{outline:0;border-color:var(--gold,#e2b866)}" +
    "#mfdlg .btns{display:flex;gap:8px;margin-top:16px}" +
    "#mfdlg button{flex:1;padding:11px 0;border:1px solid var(--ctl,#23303f);border-radius:10px;" +
    "background:none;color:var(--ctl-ink,#c9d0da);font:inherit;font-size:14px;cursor:pointer}" +
    "#mfdlg button:hover{border-color:var(--gold-line,rgba(226,184,102,.45))}" +
    "#mfdlg button.pri{background:var(--gold,#e2b866);border-color:var(--gold,#e2b866);" +
    "color:var(--on-gold,#1a1608);font-weight:700}" +
    "#mfdlg button.danger{background:var(--pink,#7d3a3a);border-color:var(--pink,#7d3a3a);color:var(--onpink,#ffe9e9);font-weight:700}";

  var dlgCss = false;

  function ask(o) {
    o = o || {};
    if (!dlgCss) {
      var st = document.createElement("style");
      st.textContent = DLG_CSS;
      document.head.appendChild(st);
      dlgCss = true;
    }
    var wants = typeof o.input === "string";
    return new Promise(function (done) {
      var back = document.createElement("div");
      back.id = "mfdlg";
      var box = document.createElement("div");
      box.className = "box";

      if (o.title) {
        var h = document.createElement("h3");
        h.textContent = o.title;
        box.appendChild(h);
      }
      if (o.text) {
        var p = document.createElement("p");
        p.textContent = o.text;           // 글로만 넣는다 — 남의 값이 섞여도 심어지지 않게
        box.appendChild(p);
      }
      var field = null;
      if (wants) {
        field = document.createElement("input");
        field.type = "text";
        field.value = o.input;
        if (o.placeholder) field.placeholder = o.placeholder;
        box.appendChild(field);
      }

      var row = document.createElement("div");
      row.className = "btns";
      var no = document.createElement("button");
      no.textContent = o.cancel || "취소";
      var yes = document.createElement("button");
      yes.className = o.danger ? "danger" : "pri";
      yes.textContent = o.ok || "확인";
      row.append(no, yes);
      box.appendChild(row);
      back.appendChild(box);
      document.body.appendChild(back);

      var prev = document.activeElement;
      setTimeout(function () { (field || yes).focus(); if (field) field.select(); }, 0);

      function close(v) {
        document.removeEventListener("keydown", key, true);
        back.remove();
        try { if (prev && prev.focus) prev.focus(); } catch (e) { /* 사라졌으면 그만 */ }
        done(v);
      }
      function key(e) {
        // 화면마다 Esc 로 되돌아가는 처리가 따로 있다. 여기서 막지 않으면
        // 「창만 닫기」가 아니라 **페이지까지 같이 닫힌다** (검사에서 잡혔다).
        if (e.key !== "Escape" && e.key !== "Enter") return;
        if (e.key === "Enter" && field && document.activeElement !== field) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (e.key === "Escape") close(wants ? null : false);
        else close(wants ? field.value : true);
      }
      no.onclick = function () { close(wants ? null : false); };
      yes.onclick = function () { close(wants ? field.value : true); };
      back.onclick = function (e) { if (e.target === back) close(wants ? null : false); };
      document.addEventListener("keydown", key, true);
    });
  }

  /* 뿌리 주소 — `/api/folio` 를 붙이지 **않는다.** 레일(shell.js)이 `/api/shell` 을 부를 때 쓴다. */
  function rootUrl(path) { return (REMOTE ? (S.url || "") : "") + path; }

  /* ── 커버 그림 (폰 = PC) ──
     커버는 **PC 가 내주는 그림**(`/folio/covers/…`)이다. 폰 사이트에서 그 주소를 그대로 `<img src>` 에
     넣으면 **사이트**를 가리켜 빈 칸이 된다. 그리고 PC 는 밖의 요청에 쪽지 없이 아무것도 안 내준다
     (`<img>` 는 머리를 못 붙인다). 그래서 밖에서는 **fetch(쪽지 머리) → blob 주소**로 받아 넣는다.
     `imgSrc` 는 그릴 때 쓸 값: PC 안이면 원래 주소, 밖이면 빈 투명 그림 + `data-mfsrc` 로 두고
     `hydrate(root)` 가 받아 채운다. 한 번 받은 것은 주소별로 기억한다 (시트를 다시 열어도 또 받지 않게). */
  var BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
  var imgCache = {};
  function isCover(u) { return typeof u === "string" && u.indexOf("/folio/covers/") === 0; }
  function imgAttr(u) {
    // innerHTML 조립에 그대로 붙인다 — 값은 부르는 쪽이 esc 한다 (src="…" data-mfsrc="…")
    if (!REMOTE || !isCover(u)) return { src: u, data: "" };
    return { src: imgCache[u] || BLANK, data: imgCache[u] ? "" : u };
  }
  function hydrate(root) {
    if (!REMOTE || !root || !root.querySelectorAll) return;
    var list = root.querySelectorAll("img[data-mfsrc]");
    Array.prototype.forEach.call(list, function (img) {
      var u = img.getAttribute("data-mfsrc");
      img.removeAttribute("data-mfsrc");
      if (!isCover(u)) return;
      if (imgCache[u]) { img.src = imgCache[u]; return; }
      fetch(rootUrl(u), { mode: "cors", headers: headers(false) })
        .then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) { if (!b) return; imgCache[u] = URL.createObjectURL(b); img.src = imgCache[u]; })
        .catch(function () { /* 못 받으면 빈 칸 그대로 */ });
    });
  }

  var MF = {
    ask: ask,
    REMOTE: REMOTE,
    TOKEN: TOKEN,
    api: api,
    rootUrl: rootUrl,
    headers: headers,
    imgAttr: imgAttr,
    hydrate: hydrate,
    login: login,
    takeBox: takeBox,
    relogin: relogin,
    forget: forget,
    session: function () { return S; },
    setSession: function (v) { S = v || {}; save(); },
    paired: function () { return !REMOTE || Boolean(S.url && (S.token || S.device)); },
    onLost: null,                    // 쪽지가 끊겼을 때 화면이 알아서 하도록
    // 밖에서는 이 PC 의 창·프로세스·파일을 건드리는 것을 아예 감춘다
    CAN: {
      overlay: !REMOTE,              // 게임 위 오버레이
      opening: !REMOTE,              // 연출 창 띄우기
      update: !REMOTE,               // 업데이트 확인·설치
      tunnel: !REMOTE,               // 터널·인증키·우편함
      pickFile: !REMOTE,             // 커버 고르기 (이 PC 의 그림 폴더)
      hold: !REMOTE,                 // 창 살아있음 신호 (밖에서 보내면 PC 앱이 꺼진다)
      greetTest: !REMOTE,            // 연주 인사 「지금 보내 보기」 — 게임 채팅으로 나가 남에게 보인다 (서버도 밖에서는 403)
      dismount: !REMOTE,             // 「탈것이면 날개 5 로 내리고 재생」 — 날개가 드는 설정 (서버도 밖에서는 안 받는다)
    },
  };
  MF.needPair = guard;
  window.MF = MF;
  // 밖인데 열쇠가 없으면 여기서 멈춘다 — 화면 코드는 그대로 두고 위에 잇기를 덮는다.
  MF.blocked = guard();
})();
