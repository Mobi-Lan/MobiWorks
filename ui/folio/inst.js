/* 악기 이름 표기 — 「[바이올린] 3화음 폼폼푸린의 스위트 푸딩 바이올린」.
 *
 * 화면 셋(index · greet · settings)이 같은 모양으로 보이려면 붙이는 곳이 하나여야 한다.
 * **종류는 여기서 뽑지 않는다.** 서버 `/api/instruments` 가 항목마다 `type` 을 주고
 * (`folio/library.py` `inst_kind`), 여기서는 그 값을 받아 앞에 붙이기만 한다 — 목록이
 * 두 벌이면 반드시 갈라진다. 서버가 종류를 모르면 `type` 이 빈 값이고, 그때는 접두 없이
 * 원래 이름 그대로 보인다 (「[?]」 를 붙이지 않는다).
 *
 *   MFInst.label(name, type)      → "[type] name" (type 이 없으면 name)
 *   MFInst.typeOf(name, items)    → /api/instruments 의 items 에서 그 이름의 type
 *   MFInst.labelOf(name, items)   → 위 둘을 이어서. 목록에 없는 이름(게임이 알려 준 지금 든 악기 등)은 그대로
 *
 * 「3화음」·「⚠」 같은 것은 원래 이름 쪽에 남긴다: "[피아노] 3화음 고결한 서약의 피아노".
 * 글자만 만든다 — HTML 로 넣는 쪽이 esc() 를 건다.
 */
(function () {
  "use strict";

  function label(name, type) {
    name = String(name == null ? "" : name);
    if (!name.trim()) return "";
    type = String(type == null ? "" : type).trim();
    return type ? "[" + type + "] " + name : name;
  }

  function typeOf(name, items) {
    name = String(name == null ? "" : name);
    if (!name || !items || !items.length) return "";
    var i, it;
    for (i = 0; i < items.length; i++) {           // 원문 그대로 먼저 (끝 공백이 이름의 일부인 악기가 있다)
      it = items[i];
      if (it && it.name === name) return String(it.type || "");
    }
    var t = name.trim();
    for (i = 0; i < items.length; i++) {           // 게임 쪽 표기(공백 뗀 것)로 온 이름
      it = items[i];
      if (it && String(it.name || "").trim() === t) return String(it.type || "");
    }
    return "";
  }

  function labelOf(name, items) {
    return label(name, typeOf(name, items));
  }

  var MFInst = { label: label, typeOf: typeOf, labelOf: labelOf };
  if (typeof window !== "undefined") window.MFInst = MFInst;
  if (typeof module !== "undefined" && module.exports) module.exports = MFInst;   // node 검사용
})();
