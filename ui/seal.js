/* seal.js — 우편함 봉투 열기 (보안 감사 ④-a). **PC 쪽 `mailseal.py` 와 바이트까지 같아야 한다.**
 *
 * PC 는 터널 주소를 인증키로 봉해 우편함에 넣는다. 폰은 사람이 친 인증키로 이 봉투를 연다.
 * 키가 틀리면 MAC 이 안 맞아 **주소 한 글자도 안 나온다** — 코드만 주운 사람은 주소를 못 본다.
 *
 *   봉투 = "mw1." + base64url(N(16) ‖ C ‖ T(32))
 *   M  = PBKDF2-HMAC-SHA256(인증키, "mobiworks-mailbox-v1" ‖ N, 600000, 32바이트)
 *   Ke = HMAC(M, "enc")   Km = HMAC(M, "mac")
 *   흐름 = HMAC(Ke, N ‖ 0u32be) ‖ HMAC(Ke, N ‖ 1u32be) ‖ …     C = P XOR 흐름
 *   T  = HMAC(Km, "mobiworks-mailbox-v1" ‖ N ‖ C)  — **먼저 재고**(crypto.subtle.verify, 상수 시간) 맞을 때만 푼다
 *
 * 자세한 까닭(왜 PBKDF2 인가 등)은 `mailseal.py` 머리글에 있다.
 * 브라우저에서는 `window.MWSeal`, node 검사에서는 같은 파일을 읽어 `globalThis.MWSeal` 로 쓴다.
 * 이 파일은 잇기 때만 필요해 net.js 가 **그때 불러 온다** — 그래서 `ui/js/`(화면이 처음부터 싣는 목록, 로드 점검 대상)가
 * 아니라 `ui/` 바로 아래에 둔다. 사이트에서는 `/seal.js`.
 */
(function (root) {
  "use strict";

  var PREFIX = "mw1.";
  var LABEL = "mobiworks-mailbox-v1";
  var ITER = 600000;
  var NONCE = 16, TAG = 32;

  function subtle() {
    var c = root.crypto || (typeof crypto !== "undefined" ? crypto : null);
    return c && c.subtle ? c.subtle : null;
  }
  function ascii(s) {
    var out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
    return out;
  }
  function cat() {
    var n = 0, i;
    for (i = 0; i < arguments.length; i++) n += arguments[i].length;
    var out = new Uint8Array(n), at = 0;
    for (i = 0; i < arguments.length; i++) { out.set(arguments[i], at); at += arguments[i].length; }
    return out;
  }
  function unb64u(s) {
    if (!/^[A-Za-z0-9_-]*$/.test(s)) return null;
    var b = s.replace(/-/g, "+").replace(/_/g, "/");
    while (b.length % 4) b += "=";
    try {
      var bin = atob(b), out = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    } catch (e) { return null; }
  }
  async function hmacKey(raw, use) {
    return subtle().importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, [use]);
  }
  async function hmac(raw, data) {
    return new Uint8Array(await subtle().sign("HMAC", await hmacKey(raw, "sign"), data));
  }

  /** 봉투를 연다 → 알맹이 바이트, 안 열리면 null (키가 틀림·형식이 다름·바뀜을 가리지 않는다). */
  async function openBytes(key, sealed) {
    if (!subtle() || typeof sealed !== "string" || sealed.indexOf(PREFIX) !== 0) return null;
    if (typeof key !== "string" || !key || !/^[\x21-\x7e]+$/.test(key)) return null;
    var raw = unb64u(sealed.slice(PREFIX.length));
    if (!raw || raw.length < NONCE + TAG + 1) return null;
    var n = raw.slice(0, NONCE), c = raw.slice(NONCE, raw.length - TAG), t = raw.slice(raw.length - TAG);
    var s = subtle();
    var base = await s.importKey("raw", ascii(key), "PBKDF2", false, ["deriveBits"]);
    var m = new Uint8Array(await s.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt: cat(ascii(LABEL), n), iterations: ITER }, base, 256));
    var ke = await hmac(m, ascii("enc"));
    var km = await hmac(m, ascii("mac"));
    // **MAC 먼저** — verify 는 상수 시간 비교다. 틀리면 흐름을 만들지도 않는다.
    var ok = await s.verify("HMAC", await hmacKey(km, "verify"), t, cat(ascii(LABEL), n, c));
    if (!ok) return null;
    var out = new Uint8Array(c.length);
    for (var i = 0, blk = 0; i < c.length; blk++) {
      var ctr = new Uint8Array([(blk >>> 24) & 255, (blk >>> 16) & 255, (blk >>> 8) & 255, blk & 255]);
      var ks = await hmac(ke, cat(n, ctr));
      for (var j = 0; j < ks.length && i < c.length; j++, i++) out[i] = c[i] ^ ks[j];
    }
    return out;
  }

  /** 우편함 봉투 → {ok, url} 또는 {ok:false, error, message}.
   *  `now` 는 검사에서만 넘긴다 (봉한 시각 검사를 고정하려고). */
  async function openBox(key, sealed, now) {
    if (typeof sealed !== "string" || sealed.indexOf(PREFIX) !== 0) {
      return { ok: false, error: "format", message: "PC 가 넣은 봉투의 형식을 모릅니다 — 이 화면을 새로 고치거나 PC 앱을 업데이트하세요." };
    }
    if (!subtle()) {
      return { ok: false, error: "no_crypto", message: "이 브라우저는 봉투를 열 수 없습니다 (https 로 열었는지 보세요)." };
    }
    var p = await openBytes(key, sealed);
    if (!p) return { ok: false, error: "bad_key", message: "인증키가 맞지 않습니다." };
    var j = null;
    try { j = JSON.parse(new TextDecoder().decode(p)); } catch (e) { j = null; }
    if (!j || j.v !== 1 || typeof j.url !== "string") {
      return { ok: false, error: "format", message: "PC 가 넣은 봉투의 형식을 모릅니다." };
    }
    if (j.app !== "mobiworks") {
      return { ok: false, error: "app", message: "모비웍스 PC 의 코드가 아닙니다." };
    }
    var u = null;
    try { u = new URL(j.url); } catch (e) { u = null; }
    if (!u || u.protocol !== "https:" || u.username || u.password || u.hostname.indexOf(".") < 0) {
      return { ok: false, error: "format", message: "봉투 안의 주소가 올바르지 않습니다." };
    }
    // 봉한 시각 — 옛 봉투를 누가 다시 넣는 것을 막는다. 우편함이 3분이라 넉넉히 15분.
    var t = typeof now === "number" ? now : Date.now() / 1000;
    if (typeof j.iat !== "number" || Math.abs(t - j.iat) > 15 * 60) {
      return { ok: false, error: "stale", message: "오래된 코드입니다 — PC 에서 새 코드를 받으세요. (모바일 기기 시계가 크게 틀려도 이렇게 보입니다)" };
    }
    return { ok: true, url: u.origin };
  }

  /* ── 우편함에서 꺼내기 ────────────────────────────────────────────────────────
   * 우편함은 **한 번 읽으면 지운다.** 그래서 꺼낸 봉투는 이 화면의 메모리에 코드별로 들고 있다가,
   * 인증키를 틀렸으면 **코드를 다시 받지 않고** 키만 다시 치게 한다 (봉투는 저장하지 않는다 —
   * 새로 고치면 사라진다). 열리면 바로 버린다.
   *
   * 답: {ok:true, url} · {ok:false, error, message} ·
   *     {ok:false, error:"legacy", url} — 봉하지 않은 옛 주소. **모비폴리오 PC**(단독판)가 넣은 것이다
   *     (모비웍스 PC 는 늘 봉한다). 부르는 쪽이 `toLegacy` 로 옛 화면에 넘긴다. */
  var held = {};

  async function takeBox(code, key, now) {
    var sealed = held[code];
    if (!sealed) {
      var box = null;
      try { box = await (await fetch("/box/" + encodeURIComponent(code))).json(); } catch (e) { box = null; }
      if (box && box.ok && box.ready === false) {
        return { ok: false, error: "not_ready", message: "PC 가 아직 주소를 넣지 않았습니다. 잠시 뒤 다시 해 보세요." };
      }
      if (!box || !box.ok) {
        return { ok: false, error: "gone", message: "코드가 없거나 이미 쓴 코드입니다. PC 에서 다시 받으세요." };
      }
      if (!box.sealed && typeof box.url === "string" && box.url) return { ok: false, error: "legacy", url: box.url };
      sealed = String(box.sealed || "");
      held[code] = sealed;
    }
    var r = await openBox(key, sealed, now);
    if (r.ok || r.error !== "bad_key") delete held[code];
    else r.message = "인증키가 맞지 않습니다. 코드는 그대로 두고 인증키만 다시 쳐 보세요.";
    return r;
  }

  /** 모비폴리오 PC(단독판)의 주소를 옛 모비폴리오 폰 화면(`/mobifolio/`)에 넘긴다.
   *  그 화면은 주소가 저장돼 있으면 인증키 칸만 보인다 (「다시 잇기」). 사이트에서만 뜻이 있다. */
  function toLegacy(url) {
    try { localStorage.setItem("mobifolio.remote", JSON.stringify({ url: url })); } catch (e) { /* 무시 */ }
    location.href = "/mobifolio/";
  }

  /** 봉하지 않은 주소를 받았을 때 — 그 PC 가 어느 앱인가 (`/api/health` 는 열쇠 없이 `{app}` 만 답한다).
   *  모비웍스인데 봉하지 않았으면 **봉투를 모르는 옛 판**이다 — 옛 화면으로 넘기지 않고 업데이트하라고 한다
   *  (넘기면 그쪽 화면이 「모비웍스입니다」라며 다시 여기로 돌려보내 빙빙 돈다). 모르면 "". */
  async function appOf(url) {
    try {
      var j = await (await fetch(url + "/api/health", { mode: "cors" })).json();
      return (j && typeof j.app === "string") ? j.app : "";
    } catch (e) { return ""; }
  }

  /** 옮기기 전에 **한 번 묻는다**. 봉하지 않은 상자는 누구나 `POST /new {url}` 로 만들 수
   *  있다 — 남이 만든 코드를 건네받아 쳤다면(사회공학) 묻지 않고 옮기는 순간 옛 화면에서 친 인증키가 그 서버로 간다.
   *  앱의 물음 창을 쓴다 (브라우저 confirm 은 안 쓴다 — 폰에서 주소가 같이 뜨고 화면이 굳는다): 폴리오 잇기 화면은 `MF.ask`,
   *  작업 잇기 화면은 core.js 의 `askOk`. `ask` 를 넘겨 주면 그것을 쓴다. 둘 다 없으면 **옮기지 않는다** — 묻지 못했으면 「예」가 아니다. */
  var MOVE_TITLE = "모비폴리오 PC 의 코드입니다";
  var MOVE_TEXT = "그 화면으로 옮길까요? 옮기면 모비폴리오 화면에서 인증키를 한 번 더 칩니다. 이 코드를 남에게서 받았다면 옮기지 마세요.";
  async function askMove(ask) {
    try {
      if (typeof ask === "function") return Boolean(await ask({ title: MOVE_TITLE, text: MOVE_TEXT, ok: "옮기기", cancel: "취소" }));
      if (root.MF && typeof root.MF.ask === "function") {
        return Boolean(await root.MF.ask({ title: MOVE_TITLE, text: MOVE_TEXT, ok: "옮기기", cancel: "취소" }));
      }
      if (typeof askOk === "function") {   // core.js 의 최상위 const — 같은 문서의 스크립트에서 이름으로 보인다
        return Boolean(await askOk(MOVE_TEXT, { title: MOVE_TITLE, ok: "옮기기", cancel: "취소" }));
      }
    } catch (e) { /* 창을 못 띄웠다 — 아래에서 「안 옮김」 */ }
    return false;
  }

  async function legacy(url, say, ask) {
    if (await appOf(url) === "mobiworks") {
      return say("이 PC 의 모비웍스가 옛 판이라 주소를 봉하지 않았습니다 — PC 에서 업데이트한 뒤 새 코드를 받으세요.", true);
    }
    if (!(await askMove(ask))) {
      return say("옮기지 않았습니다. 모비웍스 PC 에 이으려면 PC 에서 새 코드를 받아 다시 쳐 주세요.", true);
    }
    say("모비폴리오 PC 입니다 — 모비폴리오 화면으로 옮깁니다. 인증키를 한 번 더 쳐 주세요.");
    toLegacy(url);
  }

  root.MWSeal = { PREFIX: PREFIX, ITER: ITER, openBytes: openBytes, openBox: openBox,
                  takeBox: takeBox, toLegacy: toLegacy, appOf: appOf, legacy: legacy };
})(typeof window !== "undefined" ? window : globalThis);
