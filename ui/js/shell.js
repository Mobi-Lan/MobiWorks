/* 통합 셸 — 왼쪽 레일
   ───────────────────────────────────────────────────────────────────────
   **네 화면 전부가 이 파일 하나를 쓴다** (작업 화면 하나 · 연주 쪽 셋). 화면마다 따로
   그리면 네 벌이 갈라진다 — 이 저장소가 날개 숫자에서 이미 겪은 그 패턴이다.

   값은 `GET /api/shell` **한 번**에 다 온다. 길을 둘로 나누면 두 값이 서로 다른
   순간의 것이 되어, 배지는 도는데 점은 꺼진 그림이 나온다 (연주 쪽이 미니 1초·
   오버레이 2초로 따로 두드리다 겪은 일). 그 길은 CLI 를 타지 않으므로 몇 분짜리
   실행이 도는 중에도 즉시 답한다.

   **앱 이름은 이 파일에 적지 않는다** — 서버가 `apps[]` 로 준다. 아래 `name()` 참조.

   정해 둔 것:
     · 차례는 **연주(♪) 먼저, 작업(∞) 나중**
     · 칸 26×26 · radius 8 · 활성은 **앱 색** 테두리 + 채움
     · 폴리오 점 7px · 워커스 배지 파랑 숫자
     · 「우선」 8px 라벨 + 스위치 22×46 + ⚙(앱 공통 설정)
     · **머리줄 로고 버튼이 레일 토글**이고, 접으면 **알림이 그 배지로 옮겨 온다** */
(function () {
  "use strict";
  var RAIL_ID = "rail";
  var POLL = 1000;
  var NARROW = 600;          // 이 **미만**이면 기본이 접힘 — CSS 의 `(width < 600px)` 와 같은 값
  var KEY = "mobiworks.rail";
  var SEEN = "mobiworks.seen.";    // 앱마다 마지막으로 보던 주소
  var SCROLL = "mobiworks.scroll.";// 그 주소에서의 스크롤
  // 앱 아이콘 — 레일 칸과 앱 목록이 **같은 그림**을 쓴다 (자리마다 다르게 보이지 않게)
  var ICON = { folio: "/folio/icon-256.png", works: "/icon.svg" };
  var ORDER = ["folio", "works"];  // 연주(♪) 먼저, 작업(∞) 나중 — 레일과 목록이 같은 차례
  var APPS_ID = "railApps";        // 로고 버튼을 길게 누르면 뜨는 앱 목록
  var HOLD = 500;                  // 길게 누르기로 보는 시간 (ms)
  var SLOP = 6;                    // 이만큼(px) 움직이면 누르기가 아니라 끌기 — 취소

  // 이 화면이 어느 앱인가. 주소 하나로 가른다 — 화면마다 표시를 박아 두면 새 화면이
  // 생겼을 때 조용히 틀린 칸이 켜진다.
  /* 좁은 폭인가 — **CSS 와 같은 질의문으로 묻는다.** `innerWidth` 는 정수로 반올림되어, 배율 125%·150%
     창에서 폭이 599.6px 이면 JS 는 600(넓음) · CSS `(width < 600px)` 는 좁음으로 갈린다 — 레일이 보이는데
     JS 는 접힌 줄 알아 꺾쇠가 거꾸로 선다. matchMedia 가 없는 곳(시험용 얕은 DOM)만 innerWidth 로 본다.
     경계는 「창 폭 600px **미만**」 — 정확히 600 은 넓은 쪽이다. */
  function isNarrow() {
    if (typeof window.matchMedia === "function") return window.matchMedia("(width < " + NARROW + "px)").matches;
    return window.innerWidth < NARROW;
  }

  function here() { return location.pathname.indexOf("/folio") === 0 ? "folio" : "works"; }

  /* 토큰은 화면마다 다른 곳에 있다: 우리 화면은 `MW.TOKEN`(인라인 스크립트),
     폴리오 화면은 `MF.TOKEN`(net.js). **부를 때마다 찾는다** — 붙잡아 두면
     스크립트 순서가 바뀌었을 때 빈 값이 굳는다. */
  function token() {
    try {
      if (window.MW && MW.TOKEN) return MW.TOKEN;
      if (window.MF && MF.TOKEN) return MF.TOKEN;
    } catch (e) { /* 없으면 없는 대로 */ }
    return "";
  }

  /* 헤더 두 개의 **철자가 곧 열쇠**다. 서버는 `X-Requested-With` 가 `mobiworks` 와
     **글자 그대로** 같을 때만 받는다(server.py `_guard`). 여기서 `MobiWorks` 라고
     적어 둔 탓에 레일이 서버와 **한 번도 말을 못 했다** — 배지도 연주 점도 안 들어왔고,
     「우선」을 눌러도 저장이 403 으로 튕겨 손잡이가 도로 올라갔다.
     403 은 화면에 아무 표시도 없어서 **아무 일도 안 일어난 것처럼** 보인다. */
  /* **폰에서는 화면의 전송층을 탄다**. 폰 사이트(link.mobimml.com)는
     화면만 내주고 API 는 **터널 너머의 PC** 에 있다. 레일이 제 주소(`/api/shell`)로 불렀더니
     사이트가 404 를 돌려줘 배지·점이 안 들어오고, 「우선」을 눌러도 저장이 안 됐다.
     작업 화면은 `NET`(ui/js/net.js), 연주 화면은 `MF`(ui/folio/net.js) — 둘 다 주소·머리를 만든다.
     `MF.api` 는 `/api/…` 앞에 `/api/folio` 를 붙이므로 쓰지 않고 뿌리 주소(`rootUrl`)를 쓴다. */
  function transport() {
    try {
      var N = window.NET, F = window.MF;
      if (N && typeof N.url === "function" && typeof N.headers === "function")
        return { remote: !!N.REMOTE, url: N.url, headers: N.headers };
      if (F && typeof F.rootUrl === "function" && typeof F.headers === "function")
        return { remote: !!F.REMOTE, url: F.rootUrl, headers: F.headers };
    } catch (e) { /* 없으면 이 PC 주소로 */ }
    return null;
  }

  function api(path, body) {
    var tr = transport();
    // 밖인데 아직 안 이어졌으면(잇기 화면) 레일도 부르지 않는다 — 사이트 제 주소로 나가 404 만 쌓였다
    if (tr && tr.remote) {
      var N = window.NET, F = window.MF;
      if ((N && N.blocked) || (F && typeof F.paired === "function" && !F.paired())) return Promise.resolve(null);
    }
    var h = tr ? tr.headers(true) : { "X-Requested-With": "mobiworks", "Content-Type": "application/json" };
    if (!tr) {
      var t = token();
      if (t && t.indexOf("__") !== 0) h["X-MobiWorks-Token"] = t;
    }
    var init = { method: body ? "POST" : "GET", headers: h, body: body ? JSON.stringify(body) : undefined };
    if (tr && tr.remote) init.mode = "cors";
    return fetch(tr ? tr.url(path) : path, init)
      .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  function ls(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsPut(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 못 적어도 그만 */ } }

  /* ── 있던 자리로 돌아오기 (전환해도 각 앱의 스크롤·탭 선택 유지) ──

     **iframe 으로 두 앱을 동시에 띄우지 않는다.** 「백그라운드 계속 동작」은
     이 앱에서는 이미 참이다 — 큐 러너도 연주 감시도 **서버가** 돌리므로 화면을 떠나도
     아무것도 멈추지 않는다. 화면에서 실제로 잃는 것은 **보던 자리** 둘뿐이라, 그 둘만
     적어 뒀다 되돌린다. 뼈대를 갈아엎는 것보다 싸고, 새로고침·북마크에도 그대로 듣는다.

     탭은 따로 안 적어도 된다 — 작업 화면은 탭이 **주소(`#dict` 등)에 있다.**
     그래서 「마지막 주소」 하나면 탭이 저절로 따라온다. */
  function urlNow() { return location.pathname + location.search + location.hash; }

  function lastOf(app) {
    var v = ls(SEEN + app);
    // **남의 주소로 새지 않게.** `//evil` 만 막았더니 `/\evil` 이 남았다 — 브라우저는 주소의
    // `\` 를 `/` 로 읽으므로 그것도 다른 사이트다. 글자를 하나씩 막는 대신 **브라우저가
    // 실제로 어디로 가는지**를 물어 같은 출처일 때만 쓴다.
    if (v && v.charAt(0) === "/" && v.charAt(1) !== "/" && v.charAt(1) !== "\\") {
      try { if (new URL(v, location.origin).origin === location.origin) return v; } catch (e) { /* 못 읽는 주소 */ }
    }
    return app === "folio" ? "/folio/" : "/";
  }

  function remember() { lsPut(SEEN + here(), urlNow()); }

  /* ── 다음 실행으로 넘기기 ──
     경량판은 실행마다 **포트가 바뀐다** → localStorage 는 출처(포트 포함)마다 따로라 매번 빈다.
     그래서 둘을 서버(`settings.json` 의 `ui`)에 적는다:
       1) **지금 보는 곳** — 다음 실행의 창이 이 주소로 뜬다 (폴리오를 쓰다 껐으면
          다음에도 폴리오). 탭은 `replaceState` 로 바뀌어 `hashchange` 가 안 온다 → 1초 틱에서 주소를 비교한다.
       2) **화면 편의 값** — 열쇠 목록은 서버가 페이지 맨 앞 스크립트로 준다(`window.__mwPrefs.keys`,
          server.ui_prelude). 되살리기도 그 스크립트가 한다 — 여기서는 **바뀌면 올리기만** 한다.
     **이 PC 의 창에서만.** 폰(바깥 사이트)의 주소·저장소는 이 PC 의 다음 창과 상관없다 (서버도 막는다). */
  // 이 PC 의 창인가 — 전송층이 있으면 **그 판단**(`REMOTE`)을 따른다. 주소로만 보면 폰 사이트를
  // 이 PC 에서 흉내 낸 검사(127.0.0.1 의 다른 포트)가 「이 PC」로 잘못 갈린다.
  var LOCAL = (function () {
    var tr = transport();
    if (tr) return !tr.remote;
    return /^(127\.0\.0\.1|localhost)$/.test(location.hostname);
  })();
  var sentPage = null, sentPrefs = null;

  function post(body, keep) {
    var h = { "X-Requested-With": "mobiworks", "Content-Type": "application/json" };
    var t = token();
    if (t && t.indexOf("__") !== 0) h["X-MobiWorks-Token"] = t;
    // keepalive: 창을 닫는 순간(pagehide)에도 요청이 끝까지 간다
    try { return fetch("/api/ui/state", { method: "POST", headers: h, body: JSON.stringify(body), keepalive: !!keep }).catch(function () {}); }
    catch (e) { return null; }
  }

  function reportPage(keep) {
    if (!LOCAL) return;
    var u = urlNow();
    if (u === sentPage) return;
    sentPage = u;
    post({ page: u }, keep);
  }

  function prefsNow() {
    var P = window.__mwPrefs, out = {};
    if (!P || !P.keys) return null;                 // 서버가 준 목록이 없으면 (정적 사본 등) 올리지 않는다
    try {
      for (var i = 0; i < P.keys.length; i++) {
        var v = localStorage.getItem(P.keys[i]);
        if (v !== null) out[P.keys[i]] = v;
      }
    } catch (e) { return null; }
    return JSON.stringify(out);
  }

  function syncPrefs(keep) {
    if (!LOCAL) return;
    var now = prefsNow();
    if (now === null || now === sentPrefs) return;
    sentPrefs = now;
    post({ prefs: JSON.parse(now) }, keep);
  }

  function persist(keep) { reportPage(keep); syncPrefs(keep); }

  // 서버가 되살려 준 값은 이미 서버에 있다 — 처음 한 번은 올리지 않는다. 한 번도 적힌 적 없으면(seeded=false) 올린다.
  if (window.__mwPrefs && window.__mwPrefs.seeded) sentPrefs = prefsNow();

  /* 스크롤은 **주소마다** 적는다 — 같은 앱이라도 사전과 큐는 다른 자리다.
     통은 「제 안이 넘치는, id 가 있는 것」으로 찾는다: 화면마다 이름이 달라
     목록을 손으로 적으면 새 화면이 생겼을 때 조용히 빠진다. */
  function scrollers() {
    var out = [], all = document.querySelectorAll("[id]");
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.scrollHeight > e.clientHeight + 8) out.push(e);
    }
    return out;
  }

  function saveScroll() {
    var m = {}, list = scrollers();
    for (var i = 0; i < list.length; i++) if (list[i].scrollTop > 0) m[list[i].id] = list[i].scrollTop;
    var w = (document.scrollingElement || document.documentElement).scrollTop;
    if (w > 0) m["#window"] = w;
    try { lsPut(SCROLL + urlNow(), JSON.stringify(m)); } catch (e) { /* 그만 */ }
  }

  /* 되돌리기는 **내용이 들어온 뒤**여야 한다 — 목록은 늦게 온다. 자리가 잡힐 때까지
     몇 번 다시 시도하고, 그래도 안 되면 **그냥 둔다**(억지로 맨 위로 보내지 않는다). */
  function restoreScroll() {
    var raw = ls(SCROLL + urlNow());
    if (!raw) return;
    var m;
    try { m = JSON.parse(raw); } catch (e) { return; }
    if (!m || typeof m !== "object") return;
    var tries = 0;
    (function again() {
      var done = true;
      for (var id in m) {
        var want = Number(m[id]) || 0;
        var e = id === "#window" ? (document.scrollingElement || document.documentElement)
          : document.getElementById(id);
        if (!e) { done = false; continue; }
        if (e.scrollTop === want) continue;
        if (e.scrollHeight - e.clientHeight < want) { done = false; continue; }  // 아직 짧다
        e.scrollTop = want;
      }
      if (!done && ++tries < 20) setTimeout(again, 120);      // 2.4초까지만 기다린다
    })();
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function build() {
    if (document.getElementById(RAIL_ID)) return document.getElementById(RAIL_ID);
    var at = here();
    var rail = el("nav");
    rail.id = RAIL_ID;
    rail.setAttribute("aria-label", "앱 전환");

    /* 앱 칸. **아이콘은 앱이 이미 가진 것을 쓴다** — 레일에서 글리프로 다시
       그리면 같은 앱이 자리마다 다르게 보인다.
       차례는 **연주 먼저, 작업 나중**. */
    function app(kind, icon, href) {
      var a = el("a", "app" + (at === kind ? " on" : ""));
      a.href = href;
      a.dataset.app = kind;
      if (at === kind) a.setAttribute("aria-current", "page");
      var img = el("img");
      img.src = icon;
      img.alt = "";                                   // 이름은 aria-label 로 붙는다
      img.draggable = false;
      a.appendChild(img);
      rail.appendChild(a);
      return a;
    }
    // **기억한 자리로 돌아간다.** 지금 보고 있는 앱은 제 주소 그대로(눌러도 안 움직인다).
    rail.folio = app("folio", ICON.folio, at === "folio" ? urlNow() : lastOf("folio"));
    rail.works = app("works", ICON.works, at === "works" ? urlNow() : lastOf("works"));

    rail.appendChild(el("span", "grow"));

    var lb = el("span", "prilb");
    lb.textContent = "연주";                         // 지금 고른 우선을 글자로 — paintPri 가 「연주」「이 곡」「작업」으로 바꾼다
    rail.appendChild(lb);
    rail.prilb = lb;

    var pri = el("button", "pri");
    pri.type = "button";
    pri.dataset.pri = "music";
    var knob = el("span", "knob");
    pri.appendChild(knob);
    rail.appendChild(pri);
    rail.pri = pri;
    rail.knob = knob;

    pri.onclick = function () {
      // 세 자리를 돈다: 연주(music) → 이 곡(song) → 작업(work) → 연주.
      // 전에는 두 갈래뿐이라 「이 곡 끝나면」을 고를 곳이 없었다. 셋이어도 어디인지 알 수 있게
      // 손잡이 자리(위·가운데·아래)·글리프(♪ · ⏭ · ∞)·아래 글자(연주·이 곡·작업)를 전부 다르게 그린다.
      var was = pri.dataset.pri;
      var next = PRI_NEXT[was] || "song";
      paintPri(next);                                  // 누른 대로 먼저 그린다
      /* **`{settings:{…}}` 로 감싸야 한다.** 그냥 보냈더니 서버가 `bad_request` 로
         거절했고, 손잡이가 그려졌다가 1초 뒤 폴링에 **도로 올라갔다**.
         되돌릴 때는 **누르기 전 값**을 쓴다 — 이미 바꿔 놓은 `dataset` 을 다시 읽으면
         그 자리에서 또 뒤집힌다. */
      api("/api/settings", { settings: { queue_on_performance: next } }).then(function (r) {
        if (!r || r.ok === false) { paintPri(was); if (last) last.priority = was; return; }
        if (last) last.priority = next;                // 다음 폴링이 옛 값으로 덮지 않게
      });
    };

    // ⚙ — 앱 공통 설정. 화면마다 설정이 있는 자리가 달라 **그 화면의 것**으로 보낸다.
    var gear = el("button", "gear");
    gear.type = "button";
    gear.textContent = "⚙";
    gear.title = "설정";
    gear.setAttribute("aria-label", "설정");
    gear.onclick = function () {
      if (at === "folio") { location.href = "/folio/settings.html"; return; }
      var b = document.getElementById("btnSettings");
      if (b) b.click();
      else if (window.MW && MW.selectTab) MW.selectTab("settings");
    };
    rail.appendChild(gear);

    document.body.appendChild(rail);
    document.body.classList.add("has-rail");

    /* 로고 버튼 — **머리줄 왼쪽, 이것이 레일 토글이다**.
       화면마다 머리줄 이름이 다르므로 차례로 찾아 넣는다. 하나도 못 찾으면 그때만
       왼쪽 위에 띄운다 — 버튼이 없으면 접은 뒤 되돌릴 길이 사라진다. */
    var scrim = el("div");
    scrim.id = "railScrim";
    var btn = el("button");
    btn.id = "railBtn";
    btn.type = "button";
    btn.dataset.app = at;
    var gl = el("span", "gl");
    gl.textContent = at === "folio" ? "♪" : "∞";   // ♪ / ∞ — 지금 앱
    var caret = el("span", "caret");
    btn.appendChild(gl);
    btn.appendChild(caret);
    btn.caret = caret;

    /* `save` 를 안 넘기면 **사람이 고른 것**으로 보고 적어 둔다.
       폭이 바뀌어서 접히는 것은 사람의 뜻이 아니므로 `false` 로 부른다 —
       안 그러면 창을 한 번 줄였다는 이유로 **넓은 화면의 취향까지 접힘으로 덮인다.** */
    /* **취향은 넓은 폭에서 사람이 고른 것 하나만 적는다**.
       좁은 폭의 열고 닫기는 잠깐 보는 것이다 — 그걸 적었더니 스크림을 한 번 눌러 닫은 것만으로
       넓은 폭의 「펼침」이 「접힘」으로 덮였고, 되넓히면 사람이 고른 적 없는 모양이 나왔다. */
    function setOpen(on, save) {
      var narrow = isNarrow();
      if (narrow) railTop();
      document.body.classList.toggle("rail-open", !!on);
      document.body.classList.toggle("rail-off", !on && !narrow);
      caret.textContent = on ? "◀" : "▶";          // ◀ 접기 / ▶ 펼치기
      btn.title = on ? "앱 전환 레일 접기" : "앱 전환 레일 펼치기";
      btn.setAttribute("aria-label", btn.title);
      btn.setAttribute("aria-expanded", on ? "true" : "false");
      if (save !== false && !narrow) {
        want = !!on;                                         // 저장소가 막혀도 이 창에서는 기억한다
        try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* 못 적어도 그만 */ }
      }
      paint();                                               // 알림이 어디 붙을지가 바뀐다
    }
    rail.setOpen = setOpen;

    /* 좁은 폭에서 펼친 레일(z 40)·스크림(z 39)은 **머리줄 아래에서 시작한다**.
       맨 위부터 덮었더니 레일의 연주 칸이 로고 버튼 바로 위에 얹혀, 닫으려고 버튼 가운데를 다시
       누르면 `/folio/` 로 넘어갔다. 머리줄은 z 5 의 쌓임 맥락이라 버튼만 위로 올릴 수 없다 —
       그래서 덮는 쪽을 버튼 아래로 내린다. 그러면 버튼은 그대로 토글(◀ 접기)이다. */
    function railTop() {
      // 폴리오 첫 화면은 머리줄(#brandrow) 아래에 **찾기 칸·알약 줄(#finder)** 이 한 묶음(#hdr)으로 붙어 있다.
      // 머리줄 아래에서 시작하면 펼친 레일이 찾기 칸 왼쪽을 덮었다 — 묶음 아랫변에서 시작한다.
      var h = document.getElementById("hdr") || (btn.parentNode && btn.parentNode !== document.body ? btn.parentNode : btn);
      var r = h.getBoundingClientRect ? h.getBoundingClientRect() : null;
      var px = r ? Math.max(0, Math.round(r.bottom)) : 0;
      var st = document.documentElement && document.documentElement.style;
      if (st && st.setProperty) st.setProperty("--rail-top", px + "px");
    }
    btn.setAttribute("aria-haspopup", "menu");       // 길게 누르면 앱 목록 (아래 holdToList)
    btn.onclick = function () {
      /* 길게 눌러 목록을 띄운 손은 **떼는 순간 click 도 낸다** — 그것까지 토글로 받으면
         목록이 뜨자마자 레일이 뒤집힌다. 목록이 떠 있을 때 짧게 누르면 목록만 닫는다. */
      if (btn.held) { btn.held = false; return; }
      if (appsOpen()) { closeApps(); return; }
      setOpen(!document.body.classList.contains("rail-open"));
    };
    holdToList(btn);
    scrim.onclick = function () { setOpen(false); };
    // 앱을 고르면 어차피 화면이 넘어간다 — 좁을 때는 닫아 두어야 돌아왔을 때 안 덮인다
    rail.works.addEventListener("click", function () { if (isNarrow()) setOpen(false); });
    rail.folio.addEventListener("click", function () { if (isNarrow()) setOpen(false); });

    var host = document.querySelector("header") ||        // 작업 화면
      document.getElementById("brandrow") ||              // 연주 미니
      document.getElementById("hd") ||                    // 연주 설정
      document.querySelector("#wrap .card .hd");          // 연주 인사
    if (host) host.insertBefore(btn, host.firstChild);
    else {
      btn.style.position = "fixed"; btn.style.left = "8px"; btn.style.top = "8px";
      btn.style.zIndex = "41"; document.body.appendChild(btn);
    }
    document.body.appendChild(scrim);
    rail.btn = btn;

    /* 처음 상태 — 넓으면 펼침, 좁으면 접힘. 사람이 한 번 고르면 그걸 따른다.
       **못 읽어도 돌아가야 한다**: 사생활 모드·저장소 차단에서 `localStorage` 는 던진다. */
    var want = null;
    function readPref() {
      try { var v = localStorage.getItem(KEY); if (v === "0" || v === "1") want = v === "1"; } catch (e) { /* 기억한 값으로 */ }
      return want;
    }
    readPref();
    /* **좁으면 무조건 접고 시작한다.** 적어 둔 취향은 넓은 화면의 것이다 —
       그걸 그대로 따르면 폰 폭에서 레일이 본문 위에 얹힌 채로 켜지고, 뒤가 어두워져
       「왜 화면이 가려져 있나」가 된다. */
    /* **부를 때마다 다시 읽는다** — 연 순간 값만 붙잡아 두면 그 뒤 넓은 폭에서 고른 것이
       되넓힐 때 반영되지 않는다(1-5: 「사람이 접은/펼친 취향이 뒤집힘」). */
    rail.pref = function () { var w = readPref(); return w === null ? true : w; };
    setOpen(isNarrow() ? false : rail.pref(), false);
    return rail;
  }

  // 「우선」 세 자리 — 서버 값(store._ENUMS queue_on_performance)과 같은 이름. 누르면 다음 자리로 돈다.
  var PRI_NEXT = { music: "song", song: "work", work: "music" };
  // 손잡이 아이콘 — 16×16, 가운데 (8,8). 선 굵기 1.6, 색은 currentColor
  function _el(svg, NS, tag, attrs) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    svg.appendChild(e); return e;
  }
  var PRI_ICON = {
    note: function (svg, NS) {         // ♪ — 머리(원) + 기둥 + 깃발, 전체가 4~12 안에
      _el(svg, NS, "circle", { cx: "6.2", cy: "10.9", r: "1.9", fill: "currentColor" });
      _el(svg, NS, "path", { d: "M8 10.9V4.6l3.6 1.6v2.2L8 6.8", fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linejoin": "round", "stroke-linecap": "round" });
    },
    skip: function (svg, NS) {         // ⏭ — 세모 + 막대
      _el(svg, NS, "path", { d: "M4.5 4.6l5.4 3.4-5.4 3.4z", fill: "currentColor" });
      _el(svg, NS, "rect", { x: "10.6", y: "4.6", width: "1.7", height: "6.8", rx: "0.6", fill: "currentColor" });
    },
    loop: function (svg, NS) {         // ∞ — 두 고리, 가로 4~12 · 세로 5.8~10.2
      _el(svg, NS, "path", { d: "M8 8c-1-1.6-1.9-2.2-2.7-2.2C4.1 5.8 3.3 6.8 3.3 8s.8 2.2 2 2.2c.8 0 1.7-.6 2.7-2.2 1-1.6 1.9-2.2 2.7-2.2 1.2 0 2 1 2 2.2s-.8 2.2-2 2.2c-.8 0-1.7-.6-2.7-2.2z", fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linejoin": "round" });
    }
  };
  var PRI_SEAT = {
    music: { word: "연주", icon: "note",
      tip: "우선: 완전한 연주 우선 — 연주 중엔 연주가 끝나기를 기다렸다가 시작합니다 (작업 중 칸에 연주 대기 카드 · 연주는 끊지 않음)" },
    song: { word: "이곡", icon: "skip",              // 「이곡」 — 띄어쓰기 없이
      tip: "우선: 이 곡 끝나면 양보 — 지금 곡을 끝까지 들려준 뒤 작업으로 넘어갑니다" },
    work: { word: "작업", icon: "loop",
      tip: "우선: 작업 우선 — 지금 바로 합니다, 연주는 끊깁니다" }
  };

  function paintPri(v) {
    var rail = document.getElementById(RAIL_ID);
    if (!rail) return;
    var pos = PRI_SEAT[v] ? v : "music";            // 모르는 값은 연주를 지키는 쪽으로 보여 준다
    var seat = PRI_SEAT[pos];
    rail.pri.dataset.pri = pos;
    rail.pri.title = seat.tip;
    rail.pri.setAttribute("aria-label", seat.tip);
    if (rail.prilb) rail.prilb.textContent = seat.word;
    /* **아이콘은 선으로 그린다** (글자 아님). 글자 글리프(♪ ∞ ⏭)는 글꼴마다 상자·기준선이 달라
       SVG text 로 가운데를 맞춰도 16px 원 안에서 위아래로 치우쳤다. 16×16 상자의 기하학적 가운데(8,8)에 맞춰 그린 path 라 글꼴과 무관하다. */
    // innerHTML 이 아니라 DOM 으로 — 글자열 조립은 이스케이프 검사(test_ui_escaping)가 막는다.
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16"); svg.setAttribute("width", "16"); svg.setAttribute("height", "16");
    svg.setAttribute("aria-hidden", "true");
    PRI_ICON[seat.icon](svg, NS);
    rail.knob.textContent = "";
    rail.knob.appendChild(svg);
  }

  /* 앱 이름 — 서버가 준 것만 쓴다. 폴리오 칸에는 **지금 나오는 곡**을 덧붙인다:
     레일에 점만 켜 두면 「뭐가 나오는지」를 보려고 굳이 화면을 넘겨야 한다. */
  function name(rail, s) {
    var list = (s && s.apps) || [];
    for (var i = 0; i < list.length; i++) {
      var a = list[i], e = rail[a.key];
      if (!e || !a.name) continue;
      var t = a.name;
      if (a.key === "folio" && s.folio && s.folio.title) t += " — " + s.folio.title;
      if (e.title !== t) { e.title = t; e.setAttribute("aria-label", t); }
    }
  }

  function mark(host, kind, n) {
    var old = host.querySelector(".badge,.dot");
    if (old) old.remove();
    if (!n) return;
    var s = el("span", kind === "badge" ? "badge" : "dot");
    if (kind === "badge") s.textContent = n > 99 ? "99+" : String(n);
    host.appendChild(s);
  }

  var last = null;

  /* 알림을 **어디에 붙일지**가 레일을 접었느냐에 따라 바뀐다 —
     레일을 숨기면 알림이 로고 버튼 배지로 옮겨 간다. 접은 채로 레일에만 붙이면
     **접은 동안 아무것도 모른다** — 그게 알림의 뜻을 통째로 없앤다.

     켜진 앱에는 안 붙인다. 보고 있는 화면의 일을 레일이 또 말할 이유가 없다. */
  function paint() {
    var rail = document.getElementById(RAIL_ID);
    if (!rail || !last) return;
    var at = here();
    var badge = at === "works" ? 0 : ((last.works && last.works.badge) || 0);
    var dot = at === "folio" ? 0 : ((last.folio && last.folio.playing) ? 1 : 0);
    var off = !document.body.classList.contains("rail-open");
    mark(rail.works, "badge", off ? 0 : badge);
    mark(rail.folio, "dot", off ? 0 : dot);
    // 접혀 있으면 로고 버튼이 대신 말한다. 숫자가 없고 점만 있을 때는 「•」 로 센다.
    mark(rail.btn, "badge", off ? (badge || (dot ? 1 : 0)) : 0);
    name(rail, last);
    paintPri(last.priority);
    // 목록이 떠 있으면 그 안의 이름·알림도 같은 값으로 (행을 다시 만들지 않는다 — 포커스가 날아간다)
    if (appsOpen()) markApps(document.getElementById(APPS_ID));
  }

  /* ── 길게 누르면 앱 목록 ────────────────────────────────
     레일을 접어 둔 채로도 **다른 앱으로 바로 건너갈 길**이다. 짧게 누르기(레일 토글)는
     그대로 두고, 500ms 를 버티면 로고 버튼 바로 아래에 두 앱을 띄운다.
     행 = 아이콘 + 이름 + 배지/점. 이름·알림은 레일과 **같은 값**(`/api/shell` 한 번의 답)이고,
     알림 규칙도 같다 — 보고 있는 앱에는 안 붙인다. 누르면 그 앱의 **마지막 자리**(`lastOf`).
     글자열 조립(innerHTML) 없이 DOM 으로만 만든다 — 이름은 서버가 준 글자다. */
  function appsOpen() {
    var m = document.getElementById(APPS_ID);
    return !!(m && !m.hidden);
  }

  function closeApps() {
    var m = document.getElementById(APPS_ID);
    var rail = document.getElementById(RAIL_ID);
    if (rail && rail.btn) rail.btn.held = false;     // 오른쪽 단추로 연 뒤 다음 click 을 먹지 않게
    if (!m || m.hidden) return false;
    m.hidden = true;
    return true;
  }

  function markApps(m) {
    if (!m) return;
    var at = here(), s = last || {}, list = s.apps || [];
    var rows = m.querySelectorAll(".ra-row");
    for (var i = 0; i < rows.length; i++) {
      var a = rows[i], key = a.dataset.app, nm = "";
      for (var j = 0; j < list.length; j++) if (list[j] && list[j].key === key) nm = String(list[j].name || "");
      var t = a.querySelector(".ra-nm");
      if (t && t.textContent !== nm) t.textContent = nm;
      if (nm) { a.title = nm; a.setAttribute("aria-label", nm); }
      if (key === "works") mark(a, "badge", at === "works" ? 0 : ((s.works && s.works.badge) || 0));
      else mark(a, "dot", at === "folio" ? 0 : ((s.folio && s.folio.playing) ? 1 : 0));
    }
  }

  function openApps(btn) {
    var m = document.getElementById(APPS_ID);
    if (!m) {
      m = el("div");
      m.id = APPS_ID;
      m.setAttribute("role", "menu");
      m.setAttribute("aria-label", "앱 목록");
      m.hidden = true;
      document.body.appendChild(m);
    }
    var at = here();
    m.textContent = "";
    for (var i = 0; i < ORDER.length; i++) {
      var key = ORDER[i];
      var a = el("a", "ra-row" + (at === key ? " on" : ""));
      a.dataset.app = key;
      a.setAttribute("role", "menuitem");
      a.href = at === key ? urlNow() : lastOf(key);
      if (at === key) {
        a.setAttribute("aria-current", "page");
        // 보고 있는 앱 — 제 자리 그대로. 같은 주소로 다시 가면 새로고침이 되어 보던 것이 날아간다
        a.addEventListener("click", function (e) { e.preventDefault(); closeApps(); });
      } else {
        a.addEventListener("click", function () { closeApps(); });
      }
      var img = el("img");
      img.src = ICON[key];
      img.alt = "";
      img.draggable = false;
      a.appendChild(img);
      a.appendChild(el("span", "ra-nm"));
      m.appendChild(a);
    }
    markApps(m);
    /* 로고 버튼 **바로 아래**. 좁은 창에서 오른쪽으로 넘치지 않게 먼저 보이고 잰 뒤 당긴다. */
    var r = btn.getBoundingClientRect();
    m.style.top = Math.round(r.bottom + 4) + "px";
    m.style.left = Math.round(r.left) + "px";
    m.hidden = false;
    var room = window.innerWidth - (m.offsetWidth || 0) - 8;
    if (room < r.left) m.style.left = Math.max(8, Math.round(room)) + "px";
    var first = m.querySelector(".ra-row");
    if (first && first.focus) first.focus();
  }

  /* 길게 누르기 — pointerdown 500ms. **떼면 취소 · 움직이면 취소**(끌기·스크롤이 목록을
     띄우면 안 된다). 오른쪽 단추·터치 길게 누르기가 내는 `contextmenu` 도 같은 목록을
     띄운다 — 브라우저 기본 메뉴 대신. */
  function holdToList(btn) {
    var timer = 0, x0 = 0, y0 = 0;
    function cancel() { if (timer) { clearTimeout(timer); timer = 0; } }
    btn.addEventListener("pointerdown", function (e) {
      btn.held = false;
      cancel();
      if (e.button != null && e.button !== 0) return;   // 오른쪽 단추는 contextmenu 가 맡는다
      x0 = e.clientX; y0 = e.clientY;
      timer = setTimeout(function () { timer = 0; btn.held = true; openApps(btn); }, HOLD);
    });
    btn.addEventListener("pointermove", function (e) {
      if (timer && (Math.abs(e.clientX - x0) > SLOP || Math.abs(e.clientY - y0) > SLOP)) cancel();
    });
    btn.addEventListener("pointerup", cancel);
    btn.addEventListener("pointercancel", cancel);
    btn.addEventListener("pointerleave", cancel);
    btn.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      cancel();
      btn.held = true;              // 터치에서는 뒤따르는 click 이 토글이 되지 않게 (다음 pointerdown 이 푼다)
      if (!appsOpen()) openApps(btn);
    });
  }

  /* 목록 닫기 — 밖을 누르면 · Esc · 창 크기가 바뀌면(자리가 어긋난다). */
  function watchApps() {
    document.addEventListener("pointerdown", function (e) {
      if (!appsOpen()) return;
      var m = document.getElementById(APPS_ID), rail = document.getElementById(RAIL_ID);
      var t = e.target;
      if (m && m.contains(t)) return;
      if (rail && rail.btn && rail.btn.contains(t)) return;   // 버튼 위는 버튼의 click 이 맡는다
      closeApps();
    }, true);
    /* Esc 는 **붙잡기 단계에서 먼저** 받고 거기서 멈춘다. 화면마다 제 Esc 가 있다(폴리오: 시트 닫기·검색 지우기).
       올리기 단계에서 받았더니 한 번의 Esc 가 목록도 닫고 **뒤의 시트까지 닫거나 검색어를 지웠다** —
       목록이 떠 있을 때의 Esc 는 목록만 닫는다. */
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !appsOpen()) return;
      e.stopPropagation();
      e.preventDefault();
      closeApps();
      var rail = document.getElementById(RAIL_ID);
      if (rail && rail.btn && rail.btn.focus) rail.btn.focus();
    }, true);
    window.addEventListener("resize", function () { closeApps(); });
  }

  function tick() {
    api("/api/shell").then(function (s) {
      if (!s) return;                                  // 못 받았으면 **옛 그림을 그대로 둔다**
      last = s;
      paint();
    });
  }

  function start() {
    build();
    watchApps();
    restoreScroll();
    remember();
    persist(false);
    tick();
    setInterval(tick, POLL);
    // 탭 전환은 replaceState 라 이벤트가 없다 — 주소·편의 값을 1초마다 비교해 바뀐 것만 올린다
    var seenUrl = urlNow();
    setInterval(function () {
      if (urlNow() !== seenUrl) { seenUrl = urlNow(); remember(); }
      persist(false);
    }, POLL);

    /* 적는 때 셋. **`beforeunload` 만 믿으면 안 된다** — 폰에서는 그게 안 불리고
       앱이 그냥 얼어붙는 일이 흔하다. `pagehide` 와 화면 숨김을 같이 본다. */
    window.addEventListener("pagehide", function () { remember(); saveScroll(); persist(true); });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") { remember(); saveScroll(); persist(true); }
    });
    // 탭을 옮기면 주소가 바뀐다 (작업 화면은 탭이 주소에 있다)
    window.addEventListener("hashchange", function () { remember(); persist(false); });
    // 폭이 바뀌면 접힘의 뜻이 바뀐다 (넓을 때 접으면 본문이 38px 넓어지고,
    // 좁을 때는 얹히는 것이라 자리를 비우지 않는다)
    var wasNarrow = isNarrow();
    window.addEventListener("resize", function () {
      var rail = document.getElementById(RAIL_ID);
      if (!rail || !rail.setOpen) return;
      var narrow = isNarrow();
      if (narrow !== wasNarrow) {
        wasNarrow = narrow;
        /* **폭을 넘나들면 그 폭의 기본값으로 돌아간다**. 넓을 때의 펼침을 좁은 폭까지 끌고 오면
           레일이 본문 위에 얹히고 뒤가 어두워진다 — 줄이자마자 화면이 가려진다.
           이 접힘은 **사람의 뜻이 아니므로 적어 두지 않는다**(둘째 인자 false). */
        rail.setOpen(narrow ? false : (rail.pref ? rail.pref() : true), false);
        return;
      }
      rail.setOpen(document.body.classList.contains("rail-open"), false);
    });
  }

  /* 로드 점검이 파일마다 대표 함수 하나를 본다 (index.html 끝) — 없으면 이 파일이
     안 실려도 **아무 말 없이** 레일만 사라진다.
     내보내는 **모양까지** 다른 파일과 같아야 한다 — 검사가 그 줄을 글자로 찾는다. */
  function railReady() { return !!document.getElementById(RAIL_ID); }
  window.MW = window.MW || {};
  Object.assign(window.MW, { railReady: railReady });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
