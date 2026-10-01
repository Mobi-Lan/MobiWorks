/* live.js — 손님 신청 페이지 (방송 모드 · 시안 B1–B5 · B+)
 *
 * 흐름 (docs/handoff/broadcast-api.md):
 *   1. 접속 코드(6자, 표시 XXXX-XX) → 같은 출처 우편함 `GET /bc/<code>` → 터널 주소
 *   2. `POST <터널>/api/bc/join {code, name}` → 손님 토큰 (sessionStorage — 새로고침해도 다시 안 묻는다)
 *   3. `GET <터널>/api/bc/room` 을 5초마다 (머리 `X-MobiWorks-Guest`)
 *   4. 신청은 물음 창 한 번 → `POST <터널>/api/bc/req {key}`
 *
 * 안전: 서버가 준 글·손님이 쓴 글은 전부 textContent 로만 넣는다 (HTML 글로 조립해 넣는 자리가 없다).
 * 터널 주소는 https://<이름>.trycloudflare.com 만 받는다 (우편함이 잘못된 주소를 내줘도 다른 곳에 붙지 않게).
 */
(function () {
  "use strict";

  var ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  var CODE_RX = /^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$/;
  var POLL_MS = 5000;
  var RETRY_S = 10;           // 닿지 않을 때 다시 잇기까지 (시안 「(10초)」)
  var TOAST_MS = 3000;
  var SKEY = "mw.live";

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  // ── 코드 ──
  /** 대소문자·`-`·공백 무시 → 대문자 영숫자 6자까지. */
  function normCode(s) { return String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6); }
  /** 화면 표시: 4자 + `-` + 2자. */
  function showCode(c) { return c.length > 4 ? c.slice(0, 4) + "-" + c.slice(4) : c; }

  /** 우편함이 준 터널 주소를 믿어도 되는가. 개발용 가짜 서버(이 PC)에서 열었을 때만 http://127.0.0.1 을 받는다. */
  function okTunnel(u) {
    var p;
    try { p = new URL(u); } catch (e) { return ""; }
    if (p.username || p.password) return "";
    if (p.protocol === "https:" && /^[a-z0-9-]+\.trycloudflare\.com$/i.test(p.hostname)) return p.origin;
    var here = location.hostname;
    if ((here === "127.0.0.1" || here === "localhost") && p.protocol === "http:" &&
        (p.hostname === "127.0.0.1" || p.hostname === "localhost")) return p.origin;
    return "";
  }

  // ── 시간·글 ──
  function mmss(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }
  /** 「봄날」을 · 「밤편지」를 — 받침으로 조사를 고른다. */
  function eulReul(word) {
    var w = String(word || "").trim();
    var ch = w.charAt(w.length - 1);
    var code = ch.charCodeAt(0);
    if (code >= 0xAC00 && code <= 0xD7A3) return (code - 0xAC00) % 28 ? "을" : "를";
    if (/[0136789]/.test(ch)) return "을";
    if (/[lmnLMN]/.test(ch)) return "을";
    return "를";
  }

  // ── 저장 (sessionStorage — 막혀 있어도 페이지는 돈다) ──
  function load() {
    try { var o = JSON.parse(sessionStorage.getItem(SKEY) || "null"); return o && typeof o === "object" ? o : null; }
    catch (e) { return null; }
  }
  function save(o) { try { sessionStorage.setItem(SKEY, JSON.stringify(o)); } catch (e) { /* 저장 못 해도 이번 화면은 돈다 */ } }
  function forget() { try { sessionStorage.removeItem(SKEY); } catch (e) { /* 그만 */ } }

  // ── 상태 ──
  var S = {
    code: "", url: "", guest: "", name: "",
    room: null,             // 마지막으로 받은 방
    waitUntil: 0,           // 다음 신청까지 (ms 시각)
    nowUntil: 0,            // 지금 곡이 끝나는 때 (ms 시각)
    nowKey: "",
    open: false,            // 「내 신청」 펼침
    pollT: 0, tickT: 0, retryT: 0, toastT: 0,
    retryLeft: 0,
    sending: false,
    shownBlocked: false, shownFull: false,
    fromQr: "",
  };

  // ── 통신 ──
  function Fail(kind, status, body) { this.kind = kind; this.status = status; this.body = body || {}; }

  /** fetch → {status, body}. 네트워크가 끊기면 Fail("net"). */
  function call(url, opt) {
    opt = opt || {};
    var headers = {};
    if (opt.body !== undefined) headers["Content-Type"] = "application/json";
    if (opt.guest) headers["X-MobiWorks-Guest"] = opt.guest;
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var t = ctl ? setTimeout(function () { ctl.abort(); }, 8000) : 0;
    return fetch(url, {
      method: opt.method || "GET",
      headers: headers,
      body: opt.body !== undefined ? JSON.stringify(opt.body) : undefined,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: ctl ? ctl.signal : undefined,
    }).then(function (r) {
      clearTimeout(t);
      return r.text().then(function (txt) {
        var b = null;
        try { b = txt ? JSON.parse(txt) : {}; } catch (e) { b = null; }
        return { status: r.status, body: b && typeof b === "object" ? b : null };
      });
    }, function () {
      clearTimeout(t);
      throw new Fail("net", 0);
    });
  }

  /** 우편함: 코드 → 터널 주소. */
  function resolve(code) {
    return call("/bc/" + encodeURIComponent(code)).then(function (r) {
      if (r.status === 404) throw new Fail("no_code", 404, r.body);
      var url = r.body && r.body.ok ? okTunnel(r.body.url) : "";
      if (!url) throw new Fail("net", r.status, r.body);
      return url;
    });
  }

  /** 손님 API 한 번. 터널 쪽이 죽었을 때(클라우드플레어가 내는 5xx·HTML) 는 「닿지 않음」으로 본다. */
  function api(path, opt) {
    if (!S.url) return Promise.reject(new Fail("net", 0));
    return call(S.url + "/api/bc/" + path, opt).then(function (r) {
      if (r.status >= 500 || !r.body) throw new Fail("net", r.status, r.body);
      if (r.status === 410) throw new Fail("ended", 410, r.body);
      if (r.status >= 400 || r.body.ok === false) throw new Fail((r.body && r.body.error) || "error", r.status, r.body);
      return r.body;
    });
  }

  // ── 화면 전환 ──
  function show(view) {
    $("entry").hidden = view !== "entry";
    $("room").hidden = view !== "room";
    if (view !== "room") { $("toast").hidden = true; closeDlg(false); }
    $("me").hidden = view !== "room" || !S.name;
  }

  // ── B1·B2 입장 ──
  function setCodeError(on) {
    $("codeErr").hidden = !on;
    $("code").classList.toggle("bad", !!on);
    $("code").setAttribute("aria-invalid", on ? "true" : "false");
    if (on) $("qrHint").hidden = true;       // 틀렸다는 줄과 「QR로 채워졌다」 줄을 같이 두지 않는다
  }
  function markFilled() {
    $("code").classList.toggle("has", !!$("code").value);
    $("name").classList.toggle("has", !!$("name").value);
  }
  function onCodeInput() {
    var inp = $("code");
    var c = normCode(inp.value);
    var shown = showCode(c);
    if (inp.value !== shown) inp.value = shown;
    setCodeError(false);
    $("qrHint").hidden = !(S.fromQr && c === S.fromQr);
    markFilled();
  }

  function enter(ev) {
    if (ev) ev.preventDefault();
    if (S.sending) return;
    var code = normCode($("code").value);
    var name = $("name").value.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, 12);
    if (!CODE_RX.test(code)) { setCodeError(true); $("code").focus(); return; }
    S.sending = true;
    $("go").disabled = true;
    resolve(code).then(function (url) {
      S.url = url;
      return api("join", { method: "POST", body: { code: code, name: name } });
    }).then(function (b) {
      S.code = code;
      S.guest = String(b.guest || "");
      S.name = String(b.name || name || "익명");
      save({ code: S.code, url: S.url, guest: S.guest, name: S.name });
      startRoom();
    }, function (e) {
      if (e && e.kind === "no_code") { setCodeError(true); $("code").focus(); }
      else if (e && e.kind === "bad_code") notice("bad");
      else if (e && e.kind === "ended") notice("ended");
      else if (e && e.status === 429) notice("unreach");
      else notice("unreach");
    }).then(function () {
      S.sending = false;
      $("go").disabled = false;
    });
  }

  // ── B3 방 ──
  function startRoom() {
    $("me").textContent = S.name;
    show("room");
    hideNotice();
    poll();
    if (!S.tickT) S.tickT = setInterval(tick, 1000);
  }

  function stopTimers() {
    clearTimeout(S.pollT); S.pollT = 0;
    clearInterval(S.tickT); S.tickT = 0;
    clearInterval(S.retryT); S.retryT = 0;
  }

  function poll() {
    clearTimeout(S.pollT);
    api("room", { guest: S.guest }).then(function (room) {
      applyRoom(room);
      S.pollT = setTimeout(poll, POLL_MS);
    }, function (e) { roomFail(e); });
  }

  function roomFail(e) {
    var k = e && e.kind;
    if (k === "ended") { endAll(); notice("ended"); return; }
    if (k === "bad_guest") { rejoin(); return; }
    // 네트워크·터널 오류 — 「닿지 않음」 → 10초마다 다시
    notice("unreach");
  }

  /** 토큰이 무효가 됐다 (PC 가 다시 켜졌다) — 같은 코드·이름으로 한 번 다시 들어간다. */
  function rejoin() {
    resolve(S.code).then(function (url) {
      S.url = url;
      return api("join", { method: "POST", body: { code: S.code, name: S.name } });
    }).then(function (b) {
      S.guest = String(b.guest || "");
      S.name = String(b.name || S.name);
      save({ code: S.code, url: S.url, guest: S.guest, name: S.name });
      startRoom();
    }, function (e) {
      if (e && (e.kind === "no_code" || e.kind === "ended")) { endAll(); notice("ended"); }
      else if (e && e.kind === "bad_code") { endAll(); notice("bad"); }
      else notice("unreach");
    });
  }

  function applyRoom(r) {
    S.room = r;
    var now = r.now && r.now.key ? r.now : null;
    S.nowKey = now ? String(now.key) : "";
    S.nowUntil = now ? Date.now() + Math.max(0, Number(now.left) || 0) * 1000 : 0;
    var w = Math.max(0, Number(r.wait) || 0);
    S.waitUntil = w ? Date.now() + w * 1000 : 0;

    $("now").hidden = !now;
    if (now) {
      $("nowTitle").textContent = String(now.title || "");
      $("nowArtist").textContent = now.artist ? "· " + now.artist : "";
    }
    $("plName").textContent = String(r.name || "");
    var songs = Array.isArray(r.songs) ? r.songs : [];
    $("plCount").textContent = (Number(r.count) || songs.length) + "곡";

    renderList();
    renderMine();
    tick();

    // 끊김·가득 참 — 넘어가는 순간 한 번 카드로 알리고, 목록은 계속 본다
    if (r.blocked && !S.shownBlocked) { S.shownBlocked = true; notice("blocked"); }
    else if (!r.blocked && r.full && !S.shownFull) { S.shownFull = true; notice("full"); }
    if (!r.full) S.shownFull = false;
  }

  /** 지금 신청을 받을 수 있는가 (전체). */
  function canRequest() {
    var r = S.room;
    if (!r || r.blocked || r.full) return false;
    return S.waitUntil <= Date.now();
  }

  function mineByKey() {
    var m = {};
    var list = S.room && Array.isArray(S.room.mine) ? S.room.mine : [];
    for (var i = 0; i < list.length; i++) {
      var x = list[i];
      if (x && (x.state === "wait" || x.state === "ok")) m[String(x.key)] = true;
    }
    return m;
  }

  function renderList() {
    var box = $("list");
    var r = S.room;
    var songs = r && Array.isArray(r.songs) ? r.songs : [];
    var q = $("q").value.trim().toLowerCase();
    var mine = mineByKey();
    var open = canRequest();
    var frag = document.createDocumentFragment();
    var n = 0;
    for (var i = 0; i < songs.length; i++) {
      var s = songs[i];
      if (!s || s.key === undefined) continue;
      var title = String(s.title || ""), artist = String(s.artist || "");
      if (q && title.toLowerCase().indexOf(q) < 0 && artist.toLowerCase().indexOf(q) < 0) continue;
      var key = String(s.key);
      var row = el("div", "row");
      row.setAttribute("role", "listitem");
      var playing = key === S.nowKey;
      if (playing) row.classList.add("playing");
      row.appendChild(el("span", "cover", "♪"));
      var meta = el("span", "meta");
      meta.appendChild(el("span", "t", title));
      var sub = el("span", "s");
      sub.appendChild(el("span", "a", artist));
      if (s.sec) sub.appendChild(el("span", "mono", mmss(s.sec)));
      meta.appendChild(sub);
      row.appendChild(meta);
      if (playing) {
        row.appendChild(el("span", "chip play", "연주 중"));
      } else if (mine[key]) {
        var done = el("button", "req off", "신청함");
        done.type = "button";
        done.disabled = true;
        row.appendChild(done);
      } else {
        var b = el("button", "req" + (open ? "" : " off"), "신청");
        b.type = "button";
        b.setAttribute("data-key", key);
        if (!open) b.setAttribute("aria-disabled", "true");
        row.appendChild(b);
      }
      frag.appendChild(row);
      n++;
    }
    box.textContent = "";
    if (!n && songs.length) frag.appendChild(el("div", "empty", "찾는 곡이 없습니다"));
    box.appendChild(frag);
  }

  var STATE_TXT = { wait: "대기 중", ok: "승인됨", no: "거절됨" };

  function renderMine() {
    var r = S.room;
    var list = r && Array.isArray(r.mine) ? r.mine : [];
    var waiting = S.waitUntil > Date.now();
    $("mine").hidden = !list.length && !waiting;
    $("mine").classList.toggle("open", S.open);
    $("mineCount").textContent = String(list.length);
    $("mineArrow").textContent = S.open ? "▾" : "▴";
    $("mineHead").setAttribute("aria-expanded", S.open ? "true" : "false");
    var box = $("mineList");
    box.textContent = "";
    var shown = S.open ? list : list.slice(0, 3);   // 접힌 띠는 최근 3개, 펼치면 전체
    for (var i = 0; i < shown.length; i++) {
      var x = shown[i] || {};
      var st = x.state === "ok" || x.state === "no" ? x.state : "wait";
      var row = el("div", "mrow");
      row.appendChild(el("span", "t", String(x.title || "")));
      var label = STATE_TXT[st];
      if (st === "ok" && Number(x.pos) > 0) label += " · " + Number(x.pos) + "번째";
      row.appendChild(el("span", "chip " + st, label));
      box.appendChild(row);
    }
  }

  /** 1초마다 — 남은 시간·간격 제한 글자를 다시 쓴다 (서버는 5초마다). */
  function tick() {
    var t = Date.now();
    if (S.nowUntil) $("nowLeft").textContent = mmss((S.nowUntil - t) / 1000) + " 남음";
    var left = Math.max(0, Math.ceil((S.waitUntil - t) / 1000));
    var wasOn = !$("waitNote").hidden;
    $("waitNote").hidden = !left;
    if (left) $("waitMin").textContent = Math.ceil(left / 60) + "분";
    if (wasOn && !left) { S.waitUntil = 0; renderList(); renderMine(); }
  }

  // ── B4 물음 창 → 신청 ──
  var dlgKey = "";
  var dlgPrev = null;
  function openDlg(song) {
    dlgKey = String(song.key);
    var title = String(song.title || "");
    $("dlgTitle").textContent = "「" + title + "」" + eulReul(title) + " 신청할까요?";
    var min = Math.max(1, Number(S.room && S.room.interval_min) || 1);
    $("dlgMin").textContent = min + "분";
    dlgPrev = document.activeElement;
    $("dlg").hidden = false;
    setTimeout(function () { $("dlgYes").focus(); }, 0);
  }
  function closeDlg(yes) {
    if ($("dlg").hidden) return;
    $("dlg").hidden = true;
    var key = dlgKey;
    dlgKey = "";
    try { if (dlgPrev && dlgPrev.focus) dlgPrev.focus(); } catch (e) { /* 사라졌으면 그만 */ }
    if (yes && key) send(key);
  }

  function send(key) {
    if (S.sending) return;
    S.sending = true;
    api("req", { method: "POST", guest: S.guest, body: { key: key } }).then(function () {
      showToast();
      poll();                         // 바로 다시 읽어 「신청함」·대기 중·간격을 채운다
    }, function (e) {
      var k = e && e.kind;
      if (k === "interval" || e.status === 429) {
        var w = Math.max(1, Number(e.body && e.body.wait) || 60);
        S.waitUntil = Date.now() + w * 1000;
        renderList(); renderMine(); tick();
      } else if (k === "full") { S.shownFull = true; if (S.room) S.room.full = true; renderList(); notice("full"); }
      else if (k === "blocked") { S.shownBlocked = true; if (S.room) S.room.blocked = true; renderList(); notice("blocked"); }
      else if (k === "ended") { endAll(); notice("ended"); }
      else if (k === "bad_guest") rejoin();
      else if (k === "not_in_list") poll();   // 목록이 바뀌었다 — 다시 읽는다
      else if (e && e.kind === "net") notice("unreach");
      else poll();
    }).then(function () { S.sending = false; });
  }

  function showToast() {
    clearTimeout(S.toastT);
    $("toast").hidden = false;
    S.toastT = setTimeout(function () { $("toast").hidden = true; }, TOAST_MS);
  }

  function onListClick(ev) {
    var b = ev.target && ev.target.closest ? ev.target.closest("button.req") : null;
    if (!b || !b.hasAttribute("data-key")) return;
    var r = S.room;
    if (r && r.blocked) { notice("blocked"); return; }
    if (r && r.full) { notice("full"); return; }
    if (!canRequest()) return;
    var key = b.getAttribute("data-key");
    var songs = r && Array.isArray(r.songs) ? r.songs : [];
    for (var i = 0; i < songs.length; i++) if (String(songs[i].key) === key) { openDlg(songs[i]); return; }
  }

  // ── B+ 안내 상태 ──
  var NOTICE = {
    bad: { icon: "!", tone: "pink", title: "코드가 맞지 않습니다",
      text: "대소문자 없이 7자리입니다. 스트리머에게 코드를 다시 받아 주세요.", btn: "다시 입력", pri: true },
    ended: { icon: "■", tone: "", title: "방송이 끝났습니다",
      text: "스트리머가 방송을 껐습니다. 다음 방송은 새 코드로 들어올 수 있어요.", btn: "처음으로", pri: false },
    unreach: { icon: "⋯", tone: "warn", title: "방송에 닿지 않습니다",
      text: "", btn: "지금 다시 잇기", pri: true },
    blocked: { icon: "⊘", tone: "pink", title: "신청할 수 없습니다",
      text: "스트리머가 이 방송에서 신청을 막았습니다. 목록 보기는 계속할 수 있어요.", btn: "목록 보기", pri: false },
    full: { icon: "", tone: "warn", title: "신청이 가득 찼습니다",
      text: "대기 신청이 많아 잠시 받지 않아요. 곡이 줄어들면 다시 열립니다.", btn: "목록 보기", pri: false },
  };
  var noticeKind = "";

  function unreachText() {
    return "스트리머 PC 와 연결이 끊겼습니다. 잠시 뒤 자동으로 다시 이어요 (" + S.retryLeft + "초).";
  }

  function notice(kind) {
    var n = NOTICE[kind];
    if (!n) return;
    noticeKind = kind;
    closeDlg(false);
    $("toast").hidden = true;
    var icon = $("nIcon");
    icon.className = n.tone;
    // 가득 참의 동그라미 안은 대기 상한 수 (방 응답에 cap 이 있으면)
    var cap = S.room && Number(S.room.cap) > 0 ? String(Math.floor(Number(S.room.cap))) : "!";
    icon.textContent = kind === "full" ? cap : n.icon;
    $("nTitle").textContent = n.title;
    $("nBtn").textContent = n.btn;
    $("nBtn").className = n.pri ? "pri" : "";
    clearInterval(S.retryT); S.retryT = 0;
    if (kind === "unreach") {
      clearTimeout(S.pollT); S.pollT = 0;
      S.retryLeft = RETRY_S;
      $("nText").textContent = unreachText();
      S.retryT = setInterval(function () {
        S.retryLeft -= 1;
        if (S.retryLeft <= 0) { retryNow(); return; }
        $("nText").textContent = unreachText();
      }, 1000);
    } else {
      $("nText").textContent = n.text;
    }
    if (kind === "ended" || kind === "bad") $("me").hidden = true;
    $("notice").hidden = false;
    setTimeout(function () { $("nBtn").focus(); }, 0);
  }

  function hideNotice() {
    $("notice").hidden = true;
    noticeKind = "";
    clearInterval(S.retryT); S.retryT = 0;
  }

  /** 닿지 않을 때 — 우편함에서 주소를 다시 받고(터널이 새로 열렸을 수 있다) 방을 다시 읽는다. */
  function retryNow() {
    clearInterval(S.retryT); S.retryT = 0;
    $("nText").textContent = unreachText();
    if (!S.guest) {             // 입장 전 — 입장을 다시 해 본다
      hideNotice(); show("entry"); enter(); return;
    }
    resolve(S.code).then(function (url) {
      S.url = url;
      save({ code: S.code, url: S.url, guest: S.guest, name: S.name });
      return api("room", { guest: S.guest });
    }).then(function (room) {
      hideNotice();
      show("room");
      applyRoom(room);
      clearTimeout(S.pollT);
      S.pollT = setTimeout(poll, POLL_MS);
      if (!S.tickT) S.tickT = setInterval(tick, 1000);
    }, function (e) {
      var k = e && e.kind;
      if (k === "no_code" || k === "ended") { endAll(); notice("ended"); }
      else if (k === "bad_guest") rejoin();
      else notice("unreach");
    });
  }

  function onNoticeBtn() {
    var k = noticeKind;
    if (k === "unreach") { retryNow(); return; }
    hideNotice();
    if (k === "blocked" || k === "full") return;               // 목록 보기 — 목록 그대로
    if (k === "ended") {
      $("code").value = ""; onCodeInput();
      show("entry");
      $("code").focus();
      return;
    }
    if (k === "bad") {                                          // 다시 입력
      show("entry");
      setCodeError(true);
      $("code").focus();
      try { $("code").select(); } catch (e) { /* 그만 */ }
    }
  }

  /** 방송이 끝났다 — 이 방의 모든 것을 잊는다. */
  function endAll() {
    stopTimers();
    forget();
    S.guest = ""; S.url = ""; S.room = null; S.name = "";
    S.shownBlocked = S.shownFull = false;
    S.waitUntil = S.nowUntil = 0;
  }

  // ── 시작 ──
  function boot() {
    $("entry").addEventListener("submit", enter);
    $("code").addEventListener("input", onCodeInput);
    $("name").addEventListener("input", markFilled);
    $("q").addEventListener("input", renderList);
    $("list").addEventListener("click", onListClick);
    $("mineHead").addEventListener("click", function () { S.open = !S.open; renderMine(); });
    $("dlgNo").addEventListener("click", function () { closeDlg(false); });
    $("dlgYes").addEventListener("click", function () { closeDlg(true); });
    $("dlg").querySelector(".scrim").addEventListener("click", function () { closeDlg(false); });
    $("nBtn").addEventListener("click", onNoticeBtn);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("dlg").hidden) { e.preventDefault(); closeDlg(false); }
    });
    // 숨은 탭에서는 읽지 않는다 — 돌아오면 바로 한 번
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { clearTimeout(S.pollT); S.pollT = 0; }
      else if (S.guest && $("notice").hidden && !S.pollT) poll();
    });

    // QR 로 열었으면 코드가 채워진다 — 주소창에서는 지운다 (기록·공유에 코드가 남지 않게)
    var fromQr = "";
    try { fromQr = normCode(new URLSearchParams(location.search).get("c") || ""); } catch (e) { fromQr = ""; }
    if (!CODE_RX.test(fromQr)) fromQr = "";
    if (location.search) { try { history.replaceState(null, "", location.pathname); } catch (e) { /* 그만 */ } }
    S.fromQr = fromQr;

    var saved = load();
    if (saved && saved.guest && CODE_RX.test(String(saved.code || "")) && okTunnel(saved.url) &&
        (!fromQr || fromQr === saved.code)) {
      S.code = saved.code; S.url = okTunnel(saved.url); S.guest = String(saved.guest); S.name = String(saved.name || "");
      startRoom();
      return;
    }
    if (fromQr) $("code").value = showCode(fromQr);
    onCodeInput();
    show("entry");
  }

  // 검사용 — 글자 규칙만 밖으로 (화면 상태는 내보내지 않는다)
  window.MWLive = { normCode: normCode, showCode: showCode, okTunnel: okTunnel, eulReul: eulReul, mmss: mmss, ALPHA: ALPHA };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
