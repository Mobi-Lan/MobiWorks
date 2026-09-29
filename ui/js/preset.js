// preset.js — 큐 프리셋과 공유 코드 창 (dialog#presetDlg).
//
// 서버가 다 한다: 저장·목록·코드 만들기·코드 풀기·보드에 담기. 여기는 **누르는 자리**일 뿐이다.
// 코드를 화면에서 만들지 않는 이유 — 만드는 규칙이 두 군데가 되면 반드시 갈라진다
// (그리고 받는 쪽 검사는 어차피 서버에만 있다).
// 이름은 PS 다 — plan.js 가 이미 전역 `P` 를 쓴다. 겹치면 SyntaxError 로
// **화면이 통째로 안 그려진다** (같은 전역 판이다). tests/test_server_index.py 가 막는다.
// delId: 지우기는 **두 번 눌러야** 된다. 프리셋은 되돌릴 수 없고, 옆 단추가 「불러오기」다.
const PS = { rows: null, busy: false, code: "", codeFor: "", delId: "" };

const pEl = () => {
  let d = $("presetDlg");
  if (!d) { d = document.createElement("dialog"); d.id = "presetDlg"; document.body.appendChild(d); }
  return d;
};
const pWhen = (t) => { if (!t) return ""; const d = new Date(t * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

function pRender() {
  const d = pEl();
  const rows = PS.rows;
  const list = rows == null ? '<span class="muted small-t">불러오는 중…</span>'
    : !rows.length ? '<span class="muted small-t">저장한 프리셋이 없습니다. 지금 보드를 담아 두면 다음에 한 번에 꺼낼 수 있습니다.</span>'
      : rows.map((r) => `<div class="prow" data-id="${esc(r.id)}">`
        + `<span class="nm" title="${esc(r.name)}">${esc(r.name)}</span>`
        + `<span class="mt">카드 ${fmtN(r.cards)}${r.groups ? ` · 그룹 ${fmtN(r.groups)}` : ""} · ${esc(pWhen(r.at))}</span>`
        + `<span class="grow"></span>`
        + `<button class="xs" data-pload="${esc(r.id)}" title="지금 보드 뒤에 덧붙입니다">불러오기</button>`
        + `<button class="xs" data-prepl="${esc(r.id)}" title="보드를 비우고 이 프리셋으로 채웁니다">바꾸기</button>`
        + `<button class="xs" data-pcode="${esc(r.id)}" title="남에게 줄 수 있는 글자로 만듭니다">공유 코드</button>`
        + (PS.delId === r.id
          ? `<button class="xs bad on" data-pdel="${esc(r.id)}" title="한 번 더 누르면 지워집니다">정말 지울까요?</button>`
          : `<button class="xs bad" data-pdel="${esc(r.id)}" title="지웁니다 (한 번 더 눌러야 지워집니다)">✕</button>`)
        + `</div>`).join("");

  d.innerHTML = `<div class="dlg-h"><span class="badge gold">프리셋</span><span class="gname">큐 프리셋과 공유 코드</span><span class="grow"></span><button class="x" id="pX" title="닫기 (Esc)">×</button></div>
  <div class="dlg-b">
    <div class="psec">지금 보드를 저장</div>
    <div class="prow">
      <input type="text" id="pName" maxlength="60" placeholder="이름 (비우면 날짜로)" style="flex:1;min-width:0">
      <button class="small pri" id="pSave">저장</button>
    </div>
    <span class="muted small-t">담긴 것만 저장합니다 — 진행 상태나 기록은 담기지 않습니다.</span>

    <div class="psec">저장한 프리셋</div>
    <div class="plist">${list}</div>

    <div class="psec">받은 코드로 불러오기</div>
    <div class="prow">
      <input type="text" id="pCode" placeholder="MW1. 로 시작하는 코드를 붙여 넣으세요" style="flex:1;min-width:0">
      <button class="small" id="pImp">덧붙이기</button>
      <button class="small" id="pImpR">바꾸기</button>
    </div>
    <span class="muted small-t">코드 안에 내용이 전부 들어 있습니다 — 서버에 아무것도 올라가지 않고, 인터넷도 쓰지 않습니다.</span>
    ${PS.code ? `<div class="pcode"><div class="h"><b>${esc(PS.codeFor)}</b><span class="grow"></span><button class="xs" id="pCopy">복사</button></div><textarea readonly rows="3" id="pCodeT">${esc(PS.code)}</textarea><span class="muted small-t">${fmtN(PS.code.length)}자 · 그대로 전해 주면 됩니다.</span></div>` : ""}
    <div id="pMsg"></div>
  </div>`;
  pBind();
}

/* 담지 못한 카드를 **이유와 함께** 보여 준다. 조용히 빠지면 「왜 세 장이 비었지」가 된다 —
   받은 사람 쪽에 그 레시피가 아직 없을 뿐일 수도 있다(갱신하면 담긴다). */
function pReport(r) {
  const m = $("pMsg"); if (!m) return;
  const sk = r.skipped || [];
  m.innerHTML = `<div class="pmsg${sk.length ? " warn" : " ok"}">`
    + `<b>${fmtN(r.added || 0)}장</b>을 담았습니다${r.name ? ` · 「${esc(r.name)}」` : ""}`
    + (sk.length ? `<div class="sk"><span>담지 못한 ${fmtN(sk.length)}장</span>`
      + sk.map((x) => `<div><b>${esc(x.name)}</b> <span class="c">${esc(x.error || "")}</span> ${esc(x.message || "")}</div>`).join("")
      + `<span class="h">게임을 켜고 상단 「갱신」을 누른 뒤 다시 해 보세요 — 아직 관찰하지 못한 레시피일 수 있습니다.</span></div>` : "")
    + `</div>`;
}

async function pOp(payload, after) {
  if (PS.busy) return;
  PS.busy = true;
  const r = await api("/api/queue", payload);
  PS.busy = false;
  if (!r || !r.ok) {
    const m = $("pMsg");
    if (m) m.innerHTML = `<div class="pmsg bad"><span class="c">${esc((r && r.error) || "error")}</span> ${esc((r && r.message) || "실패했습니다")}</div>`;
    return null;
  }
  if (r.presets) PS.rows = r.presets;
  if (r.state && MW.applyQueueState) MW.applyQueueState(r.state);
  if (after) after(r);
  return r;
}

function pBind() {
  const d = pEl();
  const x = $("pX"); if (x) x.onclick = () => d.close();
  const sv = $("pSave");
  if (sv) sv.onclick = async () => {
    const name = ($("pName") || {}).value || "";
    const r = await pOp({ op: "preset_save", name });
    if (r) { PS.code = ""; pRender(); toast(`「${r.preset.name}」 저장했습니다`); }
  };
  d.querySelectorAll("[data-pload],[data-prepl]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.pload || b.dataset.prepl;
      const mode = b.dataset.prepl ? "replace" : "append";
      const r = await pOp({ op: "preset_load", id, mode });
      if (r) { pRender(); pReport(r); }
    };
  });
  d.querySelectorAll("[data-pcode]").forEach((b) => {
    b.onclick = async () => {
      const r = await pOp({ op: "preset_code", id: b.dataset.pcode });
      if (r) { PS.code = r.code; PS.codeFor = r.name; pRender(); }
    };
  });
  d.querySelectorAll("[data-pdel]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.pdel;
      if (PS.delId !== id) { PS.delId = id; pRender(); return; }   // 첫 번째 누름 — 되묻는다
      PS.delId = "";
      const r = await pOp({ op: "preset_delete", id });
      if (r) { PS.code = ""; pRender(); }
    };
  });
  const cp = $("pCopy");
  if (cp) cp.onclick = async () => {
    try { await navigator.clipboard.writeText(PS.code); toast("공유 코드를 복사했습니다"); }
    catch { const t = $("pCodeT"); if (t) { t.focus(); t.select(); } toast("복사하지 못했습니다 — 글자를 직접 선택해 주세요"); }
  };
  for (const [id, mode] of [["pImp", "append"], ["pImpR", "replace"]]) {
    const b = $(id);
    if (b) b.onclick = async () => {
      const code = (($("pCode") || {}).value || "").trim();
      if (!code) { const m = $("pMsg"); if (m) m.innerHTML = '<div class="pmsg bad">코드를 붙여 넣으세요.</div>'; return; }
      const r = await pOp({ op: "preset_import", code, mode });
      if (r) { pRender(); pReport(r); }
    };
  }
}

async function openPresets() {
  PS.code = ""; PS.rows = null; PS.delId = "";
  const d = pEl();
  pRender();
  if (!d.open) d.showModal();
  const r = await api("/api/presets");
  PS.rows = (r && r.ok) ? r.presets : [];
  pRender();
}

Object.assign(window.MW, { openPresets });
