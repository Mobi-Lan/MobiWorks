/* 악기 고르기 — 종류 탭 · 검색의 **모델** (그리기 없이 계산만). 루트 `inst_picker.py` 를 그대로 옮긴 것.
 *
 * 미니의 악기 고르기도 오버레이와 같은 양식을 따른다 — 오버레이(tk)와 미니(웹)가
 * **같은 규칙**으로 거르려면 규칙이 두 벌이어도 결과가 같아야 한다. `tests/test_inst_picker_js.py` 가
 * 파이썬 쪽 검사와 같은 경우를 여기에도 돌리고, 두 쪽 결과를 한 줄씩 견준다. 한쪽만 고치면 그 검사가 깨진다.
 *
 * 종류는 **여기서 뽑지 않는다** (inst.js 와 같은 규칙): 이름 앞의 「[종류]」 접두, 없으면 항목의 `type`
 * (서버 `/api/instruments` 가 `library.inst_kind` 로 채운 값). 둘 다 없으면 「그 외」.
 * 항목은 문자열이든 `{name, type}` 이든 받는다.
 *
 *   MFPick.tabs(items)                  → [[이름, 개수], …] (「전체」 먼저)
 *   MFPick.filter(items, fam, query)    → 거른 이름 목록 (원래 차례)
 *   MFPick.validTab(items, fam) · move(hi, n, d) · keepVisible(off, hi, n, rows) · scroll(off, n, rows, delta, step)
 */
(function () {
  "use strict";

  var ALL = "전체";          // 첫 탭 — 거르지 않는다
  var OTHER = "그 외";       // 종류를 모르는 이름 (「기타」는 진짜 악기 이름이라 쓰지 않는다)
  var PREFIX = /^\s*\[([^\]]+)\]\s*/;

  function nameOf(it) {
    if (it && typeof it === "object") return String(it.name == null ? "" : it.name);
    return String(it == null ? "" : it);
  }

  function typeOf(it) {
    return it && typeof it === "object" ? String(it.type == null ? "" : it.type).trim() : "";
  }

  /* 이름 → [종류, 접두 뺀 이름]. 「[피아노] 고결한 서약」 → ["피아노", "고결한 서약"]. */
  function split(it) {
    var n = nameOf(it), m = PREFIX.exec(n);
    if (m) return [m[1].trim(), n.slice(m[0].length)];
    return [typeOf(it), n];
  }

  function family(it) {
    return split(it)[0] || OTHER;
  }

  function isBlank(s) { return !String(s).trim(); }

  /* 종류와 개수 — 처음 나온 차례대로 (게임 목록 순서가 곧 사람 눈의 순서다). */
  function families(items) {
    var order = [], count = {};
    (items || []).forEach(function (it) {
      var n = nameOf(it);
      if (isBlank(n)) return;
      var f = family(it);
      if (!Object.prototype.hasOwnProperty.call(count, f)) { order.push(f); count[f] = 0; }
      count[f] += 1;
    });
    return order.map(function (f) { return [f, count[f]]; });
  }

  function tabs(items) {
    var fams = families(items), total = 0;
    fams.forEach(function (x) { total += x[1]; });
    return [[ALL, total]].concat(fams);
  }

  /* 파이썬 str.split() 과 같게 — 공백류로 나누고 빈 조각은 버린다. */
  function words(q) {
    return String(q == null ? "" : q).toLowerCase().split(/\s+/).filter(Boolean);
  }

  function squash(s) { return words(s).join(""); }

  /* 띄어 쓴 낱말이 **모두** 들어 있으면 맞다 (대소문자 무시, 접두 제외). 붙여 친 것도 한 번 더 본다. */
  function matches(it, query) {
    var ws = words(query);
    if (!ws.length) return true;
    var bare = split(it)[1].toLowerCase(), flat = squash(bare);
    return ws.every(function (w) { return bare.indexOf(w) >= 0 || flat.indexOf(w) >= 0; });
  }

  function filter(items, fam, query) {
    var out = [];
    (items || []).forEach(function (it) {
      var n = nameOf(it);
      if (isBlank(n)) return;
      if (fam && fam !== ALL && family(it) !== fam) return;
      if (matches(it, query)) out.push(n);
    });
    return out;
  }

  function validTab(items, fam) {
    return tabs(items).some(function (x) { return x[0] === fam; }) ? fam : ALL;
  }

  function move(hi, n, d) {
    if (n <= 0) return 0;
    return Math.max(0, Math.min(n - 1, (hi | 0) + (d | 0)));
  }

  function keepVisible(off, hi, n, rows) {
    rows = Math.max(1, rows | 0);
    var top = Math.max(0, n - rows);
    off = Math.max(0, Math.min(off | 0, top));
    if (hi < off) off = hi;
    else if (hi >= off + rows) off = hi - rows + 1;
    return Math.max(0, Math.min(off, top));
  }

  function scroll(off, n, rows, delta, step) {
    if (step == null) step = 3;
    var top = Math.max(0, n - Math.max(1, rows | 0));
    return Math.max(0, Math.min(top, (off | 0) - (delta > 0 ? 1 : -1) * step));
  }

  var MFPick = { ALL: ALL, OTHER: OTHER, nameOf: nameOf, split: split, family: family, families: families,
                 tabs: tabs, matches: matches, filter: filter, validTab: validTab, move: move,
                 keepVisible: keepVisible, scroll: scroll };
  if (typeof window !== "undefined") window.MFPick = MFPick;
  if (typeof module !== "undefined" && module.exports) module.exports = MFPick;   // node 검사용
})();
