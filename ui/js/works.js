// works.js — 가공 탭.
/* 구조 (위에서 아래로):
     [페이지 탭 줄 — index.html #mtabs, 셸] → [수령 배너 · sticky] → [필터 줄: 시설 n · 갱신 · 전체/진행/완료 · ＋ 담기]
     → [시설 카드 목록 (이 안에서만 스크롤)]
   시설 카드 = 머리 **한 줄**(이름 · 상태 배지 · grow · 수령 n · 담기) + 칸 **3열 그리드** (repeat(3,minmax(0,1fr)) gap 5).
   칸 = 이름 / 상태 점 · 글자(완료: 「수령 대기」 · 진행: 남은 시간 Mono · 대기: 「대기 중」).
   **빈 칸은 점선 칸**(「— / 빈 칸」)으로 채워 시설의 남은 슬롯이 보이게 — 시설당 7칸(3·3·1).
   넓은 폭의 배치(5열 타일·머리줄 격자)는 같은 마크업에 works.css 가 폭으로 갈라 준다 — 화면 코드는 한 벌이다.

   지어내지 않는 것 (CLI 가 값을 주지 않는다): 「칸 4 / 12 사용 중」 · 카드 부제(회당 N분) · 「잠김」 칸 · 진행 막대(%).
   빈 칸의 7 은 정원 데이터가 아니라 **화면 규칙**이다 — 실제로 걸린 것이 7 을 넘으면 줄이지 않는다.

   배너 둘째 줄의 「수령은 호출당 정령의 날개 5개」는 **틀린 문구다** —
   `complete_altering_work` 는 날개를 쓰지 않는다 (workqueue.py 「날개를 쓰는 것은 등록뿐이다」).
   → 「한 번에 받으면 N회 호출 · 날개 0」.

   카드 안쪽 껍데기를 여기서 **한 번만** 짓는다 (index.html 은 #worksCard 껍데기만 둔다).
   main.js 가 #wkAt 에 갱신 시각을 넣은 뒤 renderWorks() 를 부르므로, 렌더마다 innerHTML 을 갈아엎으면
   그 값이 지워진다. 렌더는 안쪽 조각(#wkBan·#wkBand·#works·#wkFacN)만 바꾼다. */
$("worksCard").innerHTML =
  // 수령 배너 (flex · 큰 수 | 예상 획득 / 호출·날개 | 「전체 수령 n」)
  '<div class="wkban" id="wkBan" hidden></div>'
  // 필터 줄 (시설 n · 갱신 · (grow) · 전체/진행/완료 · ＋ 담기)
  + '<div class="wkh">'
  + '<span class="wkfac" id="wkFacN"></span>'
  + '<span class="wkat" id="wkAt"></span>'
  + '<span class="grow"></span>'
  + '<span class="wband" id="wkBand"></span>'
  + '<button class="wkadd" id="wkAdd" title="담기 서랍을 「가공」으로 엽니다">＋ 담기</button>'
  + '</div>'
  // 시설 카드 목록 — 스크롤은 이 안에서만
  + '<div class="cb" id="works"></div>';
// 「＋ 담기」 — 기존 담기 경로. 실행 명령을 직접 부르지 않는다
$("wkAdd").onclick = () => { if (MW.openDrawer) MW.openDrawer({ type: "alter" }); };
// 옛 「접기」는 없앴다 — 가공이 탭 전체가 되면서 접을 이유도 사라졌다
lsSet("mw.worksFold", null);

/* ── 분류 ── */
// 작업 분류: done → 완료, State NotStarted → 대기 중, 그 외(InProgress·남은 시간 있음) → 진행 중
const BAND_KEYS = ["run", "wait", "done"]; const BAND_KO = { run: "진행", wait: "대기", done: "완료" };
const bandOf = (w) => w.d ? "done" : (w.state === "NotStarted" ? "wait" : "run");
/* 필터는 **전체 / 진행 / 완료** 3칸이다 (단일 선택).
   「진행」은 아직 수령하지 않은 것 전부 — 진행 중 + 대기 중. 시설 카드의 배지도 같은 두 갈래라
   이렇게 묶어야 어디에도 안 보이는 작업이 생기지 않는다. **칸의 상태 글자는 「대기 중」 그대로 보인다.**
   저장 모양(S.band = 켜진 분류 Set)은 그대로 둔다 — main.js 의 `?band=run,done` 훅이 그 모양을 쓴다. */
const WVIEWS = [["all", "전체", BAND_KEYS], ["run", "진행", ["run", "wait"]], ["done", "완료", ["done"]]];
function bandLoad() { try { const v = JSON.parse(lsGet("mw.worksBand") || "null"); if (Array.isArray(v)) return new Set(v.filter((k) => BAND_KEYS.includes(k))); } catch { } return new Set(BAND_KEYS); }
S.band = bandLoad();
// 지금 켜진 Set 이 세 갈래 중 어디인지 (`?band=` 로 다른 조합이 들어오면 아무 칸도 강조하지 않는다 — 걸러내기는 그대로 돈다)
function wviewOf() { const b = S.band; for (const [k, , keys] of WVIEWS) if (b.size === keys.length && keys.every((x) => b.has(x))) return k; return ""; }

/* ── 수령 요약 ──
   예상 획득: 완료된 작업 이름 × ProducedPerWork(= /api/work 의 alter 행 per).
   그 이름이 지금 가공 목록에 없으면 per 를 모른다 — 1 로 채우지 않고 **개수 없이 이름만** 적는다. */
function alterPerMap() { const m = new Map(); for (const r of (S.snap && S.snap.alter) || []) if (r && r.name && !m.has(r.name)) m.set(r.name, Math.max(1, Number(r.per) || 1)); return m; }
function gainList(byF) {
  const per = alterPerMap(); const n = new Map();
  for (const f in byF) for (const w of byF[f]) if (w.d) n.set(w.name, (n.get(w.name) || 0) + 1);
  return [...n.entries()].map(([name, c]) => ({ name, c, per: per.get(name) || null }))
    .sort((a, b) => (b.per ? b.c * b.per : b.c) - (a.per ? a.c * a.per : a.c));
}

/* ── 빈 칸 (점선 칸으로 채워 시설의 남은 슬롯이 보이게) ──
   칸이 셋만 그려져 있으면 「이 시설이 꽉 찼나」를 알 수 없다. 남은 자리가 보여야 지금 더 걸 수 있는지가
   한눈에 온다. 시설당 **일곱 칸**을 3·3·1 로 배치한다.
   일곱을 넘는 시설이 있으면 **줄이지 않는다.** 실제로 걸린 것을 숨기는 쪽이 더 나쁘다.
   빈 칸의 이름에는 title 을 달지 않는다 — 검사(worksview_parity)가 title 있는 .nm 만 진짜 칸으로 센다. */
const FAC_SLOTS = 7;
function emptySlots(used) {
  let h = "";
  for (let i = used; i < FAC_SLOTS; i++)
    h += '<div class="wk empty"><span class="nm">—</span><span class="st"><i class="sd"></i>빈 칸</span></div>';
  return h;
}

/* ── 칸 하나 (이름 / 상태 점 · 글자) ──
   진행 중은 남은 시간을 Mono 로 (「07:35」). 완료는 「수령 대기」, 대기(NotStarted)는 「대기 중」.
   이름 옆의 **끝나는 시각**(.at)은 넓은 폭에서만 보인다 (남은 시간만으로는
   「그때 내가 자리에 있나」를 계산해야 한다). 진행 중일 때만 적는다 — 대기는 언제 시작할지 모르므로
   끝나는 시각도 모른다. 모르는 값을 지어내지 않는다. */
function tileHTML(w) {
  const st = w.b === "done" ? "수령 대기" : w.b === "wait" ? "대기 중" : fmtT(w.left);
  return `<div class="wk ${w.b}"><span class="nm" title="${esc(w.name)}">${esc(w.name)}</span>`
    + (w.b === "run" ? `<span class="at">${esc(fmtAt(w.left))} 완료</span>` : "")
    + `<span class="st${w.b === "run" ? " mono" : ""}"><i class="sd"></i>${st}</span></div>`;
}

/* ── 렌더 ── */
function renderWorks() {
  const s = S.snap; const el = $("works"); const band = $("wkBand"); const card = $("worksCard"); const ban = $("wkBan");
  if (!el || !band || !ban) return;
  // 빈 상태: 필터 줄에 문구 한 줄만. 배너·필터는 숨기고 「＋ 담기」는 남긴다
  if (!s || !s.works || !s.works.length) {
    card.setAttribute("data-empty", "1");
    ban.hidden = true; ban.dataset.key = ""; ban.innerHTML = ""; ban.classList.remove("dim");
    if (band.dataset.key !== "empty") { band.dataset.key = "empty"; band.innerHTML = '<span class="wkempty">진행 중인 가공이 없습니다</span>'; }
    $("wkFacN").textContent = ""; el.innerHTML = "";
    S.collectable = []; S.worksDone = 0; setTitle(); return;
  }
  card.removeAttribute("data-empty");
  const el2 = Math.floor((Date.now() - S.fetchedAt) / 1000);   // fetch 이후 지난 초
  const byF = {}; const cnt = { done: 0, run: 0, wait: 0 };
  // 남은 시간이 0 이면 완료로 본다 — 단 아직 시작 안 한 작업(NotStarted)은 예외.
  // 게임은 NotStarted 의 RemainingSeconds 를 0 으로 주기도 해서, 그대로 두면 「완료」로 세고 수령까지 권하게 된다
  for (const w of s.works) { const left = w.done ? 0 : Math.max(0, w.left - el2); const d = w.done || (w.state !== "NotStarted" && left === 0); const b = d ? "done" : bandOf({ ...w, d }); cnt[b]++; (byF[w.facility] || (byF[w.facility] = [])).push({ ...w, left, d, b }); }
  const done = cnt.done;
  const facs = Object.keys(byF).sort();
  $("wkFacN").textContent = `시설 ${facs.length}`;   // 「시설 5」

  // 완료된 시설 목록(완료 수 많은 순) — 「전체 수령」·서랍의 「수령」 칩·board.js 의 collectAll 이 모두 이 값을 읽는다
  S.collectable = facs.map((f) => { const ws = byF[f]; const dn = ws.filter((w) => w.d).length; const first = ws.find((w) => w.d); return dn ? { facility: f, name: first.name, n: dn } : null; }).filter(Boolean).sort((a, b) => b.n - a.n);

  /* 수령 배너 — 큰 수 · 예상 획득 · 호출/날개 · 「전체 수령 n」. **0건이면 흐리게** (배너를 빼지 않는다 —
     자리가 바뀌면 눈이 매번 다시 찾는다). 내용이 바뀔 때만 다시 그린다 — 1초 타이머에서 버튼의 hover·포커스가 깜박이지 않게 */
  {
    const calls = S.collectable.length;   // 시설당 1회 호출로 그 시설의 완료분을 전부 가져온다
    const gain = gainList(byF);
    const bkey = `${done}|${calls}|` + gain.map((g) => `${g.name}:${g.c}:${g.per || ""}`).join(",");
    if (ban.dataset.key !== bkey) {
      ban.dataset.key = bkey;
      // 예상 획득 = 「이름 개수 · 이름 개수 · … 외 n」 (「끈적 풀 가루 20 · 산뜻 버섯 진액 15 · 철괴(광석) 6 외 7」)
      const gtxt = gain.slice(0, 3).map((g) => `${esc(g.name)}${g.per ? ` ${fmtN(g.c * g.per)}` : ""}`).join(" · ")
        + (gain.length > 3 ? ` 외 ${gain.length - 3}` : "");
      ban.innerHTML = `<span class="wb-n"><b>${fmtN(done)}</b><i>수령 대기</i></span>`
        + `<span class="wb-m"><span class="l1">${done ? `예상 획득 <span class="g">${gtxt}</span>` : "완료된 가공이 없습니다"}</span>`
        + `<span class="l2">한 번에 받으면 <b>${fmtN(calls)}회 호출 · 날개 0</b></span></span>`
        + `<button class="wb-go" id="wkCollectAll"${done ? "" : " disabled"} title="완료된 시설마다 수령을 담습니다 — 시설당 1회 호출, 정령의 날개를 쓰지 않습니다. 큐가 놀고 있으면 하나로 묶어 바로 실행합니다">전체 수령 ${fmtN(done)}</button>`;
      $("wkCollectAll").onclick = collectAll;
    }
    ban.classList.toggle("dim", !done);
    ban.hidden = false;
  }

  // 필터 세그먼트(전체/진행/완료) — 상태가 바뀔 때만 다시 그린다
  const cur = wviewOf();
  const vkey = WVIEWS.map(([k, , keys]) => `${k}:${keys.reduce((a, x) => a + cnt[x], 0)}`).join("|") + "|" + cur;
  if (band.dataset.key !== vkey) {
    band.dataset.key = vkey;
    band.innerHTML = '<span class="seg">' + WVIEWS.map(([k, ko, keys]) =>
      `<button data-wv="${k}" class="${cur === k ? "on" : ""}" title="${esc(ko)}만 보기">${ko} ${keys.reduce((a, x) => a + cnt[x], 0)}</button>`).join("") + '</span>';
    band.querySelectorAll("[data-wv]").forEach((b) => {
      b.onclick = () => { const v = WVIEWS.find((x) => x[0] === b.dataset.wv); if (!v) return; S.band = new Set(v[2]); lsSet("mw.worksBand", JSON.stringify([...S.band])); renderWorks(); };
    });
  }

  /* 시설 카드 — 머리 한 줄(이름 · 상태 배지 · grow · 수령 n · 담기) + 칸 3열 그리드.
     상태 배지는 **하나**: 진행이 있으면 「진행 n」(주황), 다 끝났으면 「완료 n」(초록) — 예: 「강철괴 완료 + 철괴 둘 진행」
     카드는 「진행 2 · 수령 1」이다. 카드 테두리는 전부 완료일 때만 초록.
     「수령」은 완료가 없으면 비활성(흐린 외곽)으로 남긴다 — 자리를 비우지 않는다. */
  const html = facs.map((f) => {
    const ws = byF[f]; const dn = ws.filter((w) => w.d).length; const rn = ws.length - dn; const first = ws.find((w) => w.d);
    const vis = ws.filter((w) => S.band.has(w.b)); if (!vis.length) return "";   // 필터로 전부 걸러진 시설은 통째로 숨김
    const fname = esc(f || "(시설 미상)");
    const st = rn ? "run" : (dn ? "done" : "");
    const badge = rn ? `<span class="badge warn">진행 ${rn}</span>` : dn ? `<span class="badge ok">완료 ${dn}</span>` : `<span class="badge warn">비어 있음</span>`;
    const btn = dn
      ? `<button class="wkgo" data-cfac="${esc(f)}" data-cname="${esc(first.name)}" data-cn="${dn}" title="이 시설의 완료분을 한 번에 수령하는 항목을 담습니다 (complete_altering_work 1회 · 날개 0)${rn ? " — 완료되지 않은 작업이 남아 있으면 게임이 not_completed_yet 으로 거부할 수 있습니다" : ""}">수령 ${dn}</button>`
      : `<button class="wkgo off" disabled title="수령할 완료분이 없습니다">수령</button>`;
    // **한 건당 한 칸이다.** 예전에는 같은 이름을 한 칸으로 묶어 「×10」으로 적었는데,
    // 그 수가 「몇 건 걸려 있나」인지 「한 번에 몇 개 나오나」인지 읽는 사람이 알 수 없었다.
    // 수령은 어차피 시설 단위(호출 1회)라 묶어 보여 줄 이유도 없다.
    // 순서: 완료 → 진행(빨리 끝나는 것부터) → 대기. 수령할 것이 눈에 먼저 들어와야 한다.
    const ORD = { done: 0, run: 1, wait: 2 };
    const grp = vis.slice().sort((a, b) => (ORD[a.b] - ORD[b.b]) || (a.left - b.left) || a.name.localeCompare(b.name));
    return `<div class="fcard${st ? " " + st : ""}">`
      + `<div class="fch"><span class="fn" title="${fname}">${fname}</span>${badge}<span class="grow"></span>`
      + `${btn}<button class="wkadd2" data-fadd="${esc(f)}" title="담기 서랍을 이 시설의 가공으로 엽니다">담기</button></div>`
      // 빈 칸 수는 **시설에 실제로 걸린 수**(필터 전)로 센다 — 필터로 가려진 칸은 비어 있는 것이 아니다
      + `<div class="wkg">${grp.map(tileHTML).join("")}${emptySlots(ws.length)}</div></div>`;
  }).join("");
  el.innerHTML = html || '<div class="muted small-t wkempty2">고른 분류에 해당하는 작업이 없습니다.</div>';
  el.querySelectorAll("[data-cfac]").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); collectAdd(b.dataset.cfac, b.dataset.cname, Number(b.dataset.cn)); }; });
  // 시설의 「담기」 — 그 시설 이름을 검색어로 담기 서랍을 연다 (drawer.js 의 dwMatch 가 가공 행의 시설명도 본다)
  el.querySelectorAll("[data-fadd]").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); if (MW.openDrawer) MW.openDrawer({ type: "alter", query: b.dataset.fadd || "" }); }; });

  // 새로 완료된 작업 알림: 이름+시설 키로 이전 완료 집합과 비교
  const nowDone = new Set(); for (const f in byF) for (const w of byF[f]) if (w.d) nowDone.add(f + "|" + w.name);
  const fresh = [...nowDone].filter((k) => !S.doneKeys.has(k));
  if (fresh.length && S.doneKeys.size + fresh.length > 0 && S.everRendered) { card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash"); toast(`가공 완료: ${fresh.map((k) => k.split("|")[1]).join(", ")}`); }
  S.doneKeys = nowDone; S.everRendered = true; S.worksDone = done; setTitle();
}
// 창 제목: 「(!) 모비웍스 — 큐 실행 중 2/5 · 오류 1」. 큐가 안 돌면 가공 완료 수만.
// **이름은 `APP_TITLE`(= index.html 의 <title>) 에서 가져온다.** 여기 또 적으면 서버가
// 앱 창을 못 찾는다 — 창을 제목으로 찾기 때문이다 (server.APP_TITLE).
function setTitle() {
  const d = Q.data; let t = APP_TITLE;
  if (d && d.running) { const items = d.items || []; const dn = items.filter((x) => x.status === "done").length; const er = items.filter((x) => x.status === "error").length; t += ` — 큐 실행 중 ${dn}/${items.length}${er ? ` · 오류 ${er}` : ""}`; }
  else if (S.worksDone) t = `(${S.worksDone} 완료) ` + t;
  document.title = (Q.errFlag ? "(!) " : "") + t;
}
setInterval(() => { if (S.snap) renderWorks(); }, 1000);

Object.assign(window.MW, { BAND_KEYS, BAND_KO, WVIEWS, FAC_SLOTS, bandLoad, bandOf, wviewOf, alterPerMap, gainList, emptySlots, tileHTML, renderWorks, setTitle });
