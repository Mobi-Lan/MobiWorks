// theme.js — 테마 (자동·다크·라이트) · **두 앱 공통** (작업 화면 · 폴리오 네 화면)
/* ── 값은 하나다: 설정 `ui_theme` (store.py · auto|dark|light · 기본 auto) ──────────────────────
   예전에는 localStorage `mobiworks.theme` 만 있었고, 서버의 「화면 편의 값」(ui_prelude)이 그것을 되살렸다.
   그 길은 **머리줄 ☀ 단추 하나**였는데, 좁은 폭에서는 단추가 감춰지고(mobile.css — 「테마는 설정 안에서」)
   설정 안에는 테마 줄이 **만들어진 적이 없었다.** 이제 설정의 한 값이 기준이다.

   이 PC (경량판 창)
     · 서버가 화면을 낼 때 `ui_theme` 이 dark·light 면 `<html data-theme>` 을 박아 낸다 (server.render_index — 번쩍임 없음).
       auto 면 아무것도 안 박고 CSS `prefers-color-scheme` 이 정한다 (tokens.css 네 블록).
     · 같은 값을 페이지 맨 앞 스크립트(server.ui_prelude)가 localStorage `mobiworks.theme` 에 도로 넣는다 — **캐시일 뿐**이다
       (번쩍임 방지 인라인 스크립트가 읽는다). 이 PC 에서 캐시에 직접 쓰는 것은 이 파일뿐이고, 다음 로드에 서버 값으로 덮인다.
     · ☀ 단추와 설정의 「테마」 줄은 `/api/settings` 로 `ui_theme` 을 저장한다.
   폰 (바깥 사이트 — 서버가 화면을 내지 않으므로 박아 줄 수 없다)
     · 기본은 **PC 설정을 따른다** (설정을 받을 때 `fromSettings` → `mobiworks.theme.pc`).
     · 폰에서 고르면 **그 폰에만** 적용된다 (`mobiworks.theme.mine`) — PC 화면은 안 바뀐다. 「PC 설정」을 고르면 다시 따른다.
   `?theme=light|dark` (검토·스크린샷용) 은 이번 로드만 고정한다 — 저장하지 않는다. */
(function () {
  var KEY = "mobiworks.theme", MINE = "mobiworks.theme.mine", PC = "mobiworks.theme.pc";
  var ORDER = ["auto", "dark", "light"], LABEL = { auto: "자동", dark: "다크", light: "라이트", pc: "PC 설정" };
  var root = document.documentElement;
  function ok(t) { return t === "auto" || t === "dark" || t === "light"; }
  function ls(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* 저장소가 막힌 창 */ } }
  function sysDark() { try { return window.matchMedia("(prefers-color-scheme: dark)").matches; } catch (e) { return true; } }
  // 이 PC 의 창인가 — 전송층(작업 NET · 폴리오 MF)이 판단한다. 이 파일이 먼저 실리는 화면(폴리오 머리)에서는 부를 때 본다
  function remote() { var N = window.NET, F = window.MF; return !!((N && N.REMOTE) || (F && F.REMOTE)); }

  // 검토용 고정 — 주소 `?theme=` (서버도 같은 값을 박는다) 또는 작업 화면 해시 `#tab?theme=`
  var forced = (function () {
    var m = /(?:^|[?&])theme=(light|dark)(?:&|$)/.exec((location.search || "").slice(1)) ||
            /(?:^|[?&])theme=(light|dark)(?:&|$)/.exec((location.hash || "").split("?")[1] || "");
    return m ? m[1] : "";
  })();

  /* 지금 고른 값 (auto|dark|light). 이 PC: 서버가 캐시에 넣어 둔 값 · 폰: 제 것 → PC 것 → 자동 */
  function pref() {
    var t = remote() ? (ls(MINE) || ls(PC)) : null;
    if (!ok(t)) t = ls(KEY);
    return ok(t) ? t : "auto";
  }
  /* 폰에서 설정 줄에 칠할 값 — 제 것이 없으면 "pc" */
  function choice() { if (remote()) { var m = ls(MINE); return ok(m) ? m : "pc"; } return pref(); }

  /* 화면에 입힌다 (저장하지 않는다). 캐시(`mobiworks.theme`)는 **지금 입힌 값**이다 — 번쩍임 방지 스크립트가 읽는다 */
  function apply(t) {
    if (!ok(t)) t = "auto";
    var show = forced || (t === "auto" ? "" : t);
    if (show) root.setAttribute("data-theme", show); else root.removeAttribute("data-theme");
    lsSet(KEY, t === "auto" ? null : t);
    paintButtons(t);
    return t;
  }

  function paintButtons(t) {
    /* **글자를 써넣지 않는다** — ☀ 는 아이콘 단추다. `textContent` 를 쓰면 안에 든 `<svg>` 가 지워진다. */
    var b = document.getElementById("themeBtn");
    if (!b) return;
    var nx = ORDER[(ORDER.indexOf(t) + 1) % ORDER.length];
    b.setAttribute("aria-label", "테마: " + LABEL[t]);
    b.title = (t === "auto" ? "테마: 자동 — OS 설정을 따릅니다 (지금 " + (sysDark() ? "다크" : "라이트") + ")" : "테마: " + LABEL[t]) +
      " · 누르면 " + LABEL[nx] + (remote() ? " (이 기기에만)" : "");
  }

  // 작업 화면은 core.js 의 `MW.api`(전역 `const api` 는 window 에 안 붙는다), 폴리오 화면은 `MF.api`(`/api/folio/…` 로 간다)
  var saveSeq = 0;
  function api() { return (window.MF && window.MF.api) || (window.MW && window.MW.api) || null; }

  /* 저장 — 이 PC 는 설정 `ui_theme` 에, 폰은 그 폰에만. 폰의 "pc" = 제 것을 지우고 PC 설정을 따른다.
     답: Promise<{ok, settings?}> */
  function save(t) {
    if (remote()) {
      if (t === "pc") { lsSet(MINE, null); apply(ok(ls(PC)) ? ls(PC) : "auto"); }
      else if (ok(t)) { lsSet(MINE, t); apply(t); }
      return Promise.resolve({ ok: true, local: true });
    }
    if (!ok(t)) return Promise.resolve({ ok: false });
    apply(t);
    var call = api();
    if (!call) return Promise.resolve({ ok: false, error: "no_api" });
    var my = ++saveSeq;                            // 빨리 여러 번 누르면 답이 뒤섞여 온다 — **마지막 것만** 화면에 되입힌다
    return Promise.resolve(call("/api/settings", { settings: { ui_theme: t } })).then(function (r) {
      if (my === saveSeq && r && r.ok !== false && r.settings && ok(r.settings.ui_theme)) apply(r.settings.ui_theme);
      return r || { ok: false };
    }, function () { return { ok: false }; });
  }

  /* 설정을 받았을 때 (작업 main.js 첫 조회 · 설정 탭 · 폴리오 boot·설정 화면). 이 PC 는 서버 값이 곧 기준이고,
     폰은 PC 값을 적어 두고 제 것이 없을 때만 따른다 */
  function fromSettings(s) {
    var t = s && s.ui_theme;
    if (!ok(t)) return;
    if (remote()) { lsSet(PC, t); if (!ok(ls(MINE))) apply(t); return; }
    apply(t);
  }

  function cycle() {
    var t = pref(), nx = ORDER[(ORDER.indexOf(t) + 1) % ORDER.length];
    forced = "";                                    // 검토용 고정은 사람이 누르면 푼다
    return save(nx).then(function (r) {
      if (window.MWTheme.onChange) { try { window.MWTheme.onChange(nx, r); } catch (e) { /* 화면 쪽 칠하기 실패는 무시 */ } }
      if (r && r.ok === false && typeof window.toast === "function") window.toast("테마를 저장하지 못했습니다" + (r.message ? ": " + r.message : ""));
      return r;
    });
  }

  function bind() {
    var b = document.getElementById("themeBtn");
    if (b && !b.dataset.themeBound) { b.dataset.themeBound = "1"; b.addEventListener("click", function () { return cycle(); }); }
    paintButtons(pref());
  }

  apply(pref());
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
  try { window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { paintButtons(pref()); }); } catch (e) { /* 옛 브라우저 */ }

  window.MWTheme = { KEY: KEY, MINE: MINE, PC: PC, ORDER: ORDER, LABEL: LABEL, pref: pref, choice: choice, apply: apply,
                     save: save, fromSettings: fromSettings, cycle: cycle, remote: remote, sysDark: sysDark, onChange: null };
  // 작업 화면의 로드 점검(index.html 맨 끝)이 `MW.applyTheme` 을 본다
  var applyTheme = apply;
  if (window.MW) Object.assign(window.MW, { applyTheme: applyTheme, readTheme: pref, THEME_KEY: KEY });
})();
