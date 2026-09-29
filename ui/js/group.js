// group.js — 그룹 설정 창(dialog#groupDlg). 좁은 폭에서는 위 28px 를 남긴 바텀 시트,
// 제목 줄에서 바로 이름 편집 · 요약 카드 한 장(회차 스테퍼 + 큰 숫자 3) · 진행 한 줄 · 대기/작업 중/완료/실패 **탭** + 해당 항목만 목록 ·
// 항목 행 한 줄(⋮⋮ · 종류 · 이름/상태·예상 · 수량 · ✕). 그룹 데이터는 보드 상태(MW.boardState)에서, 요약은 /api/queue/preview 의 groups[] 에서 온다.
// 편집은 전부 op 하나에 요청 하나 — 저장은 group_update 한 번, 응답의 state 로 다시 그린다.
const G={id:null,g:null,draft:null,prev:null,timer:null,drag:null,qT:new Map(),tab:"wait"};
const G_KO={gather:"채집",craft:"제작",alter:"가공",collect:"수령"};
/* 네 탭 = 서버가 실은 column 네 값 그대로. 빈 목록 글은 탭마다 다르다 —
   대기: 끌어 순서 · 작업 중: 「수량 잠김」 안내(행 아래에도 늘 붙는다) */
const G_COLS=[{k:"wait",t:"대기",mt:"대기 항목이 없습니다 · 「＋ 항목 담기」로 담거나 다른 탭의 행을 「대기」 탭에 끌어다 놓으면 되살아납니다"},
  {k:"run",t:"작업 중",mt:"작업 중인 항목은 수량 잠김 · 다른 탭에서 편집"},
  {k:"done",t:"완료",mt:"완료된 항목이 없습니다"},
  {k:"fail",t:"실패",mt:"실패한 항목이 없습니다"}];
const gEl=()=>{let d=$("groupDlg");if(!d){d=document.createElement("dialog");d.id="groupDlg";document.body.appendChild(d);}return d;};
const gFind=(id)=>{const st=(MW.boardState?MW.boardState():((window.Q&&Q.data)||null))||{};
  return ((st.items)||[]).find((i)=>i&&i.type==="group"&&i.id===id)||null;};
const gRunning=(g)=>!!g&&(g.column==="run"||g.status==="running");
const gCol=(c)=>c&&c.column?c.column:"wait";            // 열은 서버가 실은 column 을 그대로 쓴다 (UI 가 status 를 다시 해석하지 않는다)
const gColOf=(k)=>G_COLS.find((c)=>c.k===k)||G_COLS[0];
const gQty=(c)=>c.type==="gather"?(c.target||0):(c.count||1);
const gUnit=(c)=>c.type==="gather"?"개":"회";
const gStep=(c)=>c.type==="gather"?100:1;
/* 가공 방식 — "none"(걸기만) / "later"(등록 후 완료되면 수령) / "wait"(완료까지 기다림).
   값이 없거나 모르는 값이면 "later" 로 본다 — 서버 workqueue.alter_mode() 와 같은 규칙이다.
   (필드가 없던 시절의 저장본은 등록 뒤 수령이 기본이었다. 새로 담는 항목의 기본값이 "none" 인 것과는 별개다.) */
const gMode=(c)=>c.collect==="none"?"none":"later";
const G_MODE_KO={none:"걸기만 (등록하고 다음 항목)",later:"등록 후 다음 항목 진행 · 완료되면 수령",wait:"완료까지 그 자리에서 기다림"};
function gSub(c){if(c.type==="gather")return `${fmtN(gQty(c))}개 · 예상 ${gpass(gQty(c))}회`;
  if(c.type==="collect")return `${c.facility||""} 완료분 수령`;
  if(c.type==="alter")return `가공 ${fmtN(c.count||1)}건 · ${G_MODE_KO[c.collect]||G_MODE_KO.later}`;
  return `제작 ${fmtN(c.count||1)}회`;}
/* 행의 상태 줄 — 「상태 · 예상」(예: 「대기 · 예상 1회」, 「작업 중 · 가방 60 / 100 · 1/1회」).
   작업 중이면 보드와 같은 progOf 로 진행 글을 빌린다 (board.js 가 먼저 실려 있다). */
function gStatus(c){const col=gCol(c);const t=gColOf(col).t;
  if(col==="run"&&typeof progOf==="function"){try{const p=progOf(c,1);if(p&&p.txt)return `${t} · ${p.txt}`;}catch{}}
  if(col==="fail"){const w=gWhy(c);if(w)return `${t} · ${w} · ${gSub(c)}`;}
  return `${t} · ${gSub(c)}`;}
/* 실패 행의 **사유** — 「실패 · 제작 3회」만으로는 왜 실패했는지 모른다. 보드 카드와 같은 말을 쓴다:
   board.js 의 오류 안내(ERR_HINT · blocked 는 kind 별)가 있으면 그 한글, 없으면 서버 메시지, 그것도 없으면 코드 원문
   (지어내지 않는다 — 서버 error_short 와 같은 규칙). 오류 없이 정지만 된 행은 「정지됨」. */
function gWhy(c){const code=(c&&c.error)||"";
  if(!code)return c&&c.status==="stopped"?"정지됨":"";
  let h="";
  if(code==="blocked"&&typeof blockedHint==="function"){try{h=blockedHint(typeof blockedKind==="function"?blockedKind(c):"");}catch{}}
  else if(typeof ERR_HINT==="object"&&ERR_HINT)h=ERR_HINT[code]||"";
  return h||String(c.message||"")||code;}

/* ── 렌더 ── */
function gRender(){const d=gEl();const g=G.g;if(!g)return;const run=gRunning(g);const dr=G.draft;
  const kids=g.items||[];const cols=gSplit(kids);
  if(!gColOf(G.tab))G.tab="wait";
  d.dataset.run=run?"1":"0";
  d.innerHTML=`<div class="g-grip"><span></span></div>
  <div class="dlg-h g-hd"><span class="badge gold">그룹</span>
    <label class="g-title" title="${run?"실행 중에는 이름을 바꿀 수 없습니다":"제목에서 바로 이름을 고칩니다"}"><input type="text" id="gName" maxlength="24" value="${esc(dr.name)}" placeholder="그룹 이름" aria-label="그룹 이름"${run?" disabled":""}><span class="pen" aria-hidden="true">✎</span></label>
    <button class="x" id="gX" title="닫기 (Esc)" aria-label="닫기">✕</button></div>
  <div class="dlg-b">
    ${gSumHtml()}
    ${gProgHtml()}
    <div class="g-hr"></div>
    <div class="g-items"><span class="t">항목 <span class="n">${fmtN(kids.length)}</span></span><span class="grow"></span><button class="xs" id="gAdd"${run?" disabled":""}>＋ 항목 담기</button></div>
    ${gTabsHtml(cols,run)}
    ${gListHtml(cols,run)}
    <div class="g-opts">
      <div class="g-opt"><span class="tx"><b>오류 시</b><i>이 그룹에만 적용 · blocked·연결 끊김은 항상 정지</i></span>
        <span class="seg" id="gErr"><button data-v="continue" class="${dr.onError==="continue"?"on":""}"${run?" disabled":""}>계속</button><button data-v="stop" class="${dr.onError==="stop"?"on":""}"${run?" disabled":""}>정지</button></span></div>
      <div class="g-opt"><span class="tx"><b>회차마다 실패 항목도 재시도</b><i>끄면 실패 카드는 그룹 안 실패 탭에 남습니다</i></span>
        <button class="sw${dr.retryFailed?" on":""}" id="gRetry" role="switch" aria-checked="${dr.retryFailed?"true":"false"}" aria-label="회차마다 실패 항목도 재시도"${run?" disabled":""}></button></div>
      <div class="g-opt"><span class="tx"><b>가공은 완료까지 기다렸다 다음으로</b><i><b>꺼두면(기본) 큐가 멈추지 않습니다</b> — 등록하고 다음 항목을 먼저 처리하다가 완료되면 수령합니다.<br>켜면 가공이 끝날 때까지 그 자리에서 기다립니다. 「수령까지」로 담은 가공에만 적용되고 「걸기만」 카드는 그대로 등록만 하고 넘어갑니다</i></span>
        <button class="sw${dr.waitAlter?" on":""}" id="gWait" role="switch" aria-checked="${dr.waitAlter?"true":"false"}" aria-label="가공은 완료까지 기다렸다 다음으로"${run?" disabled":""}></button></div>
    </div>
  </div>
  <div class="dlg-f"><button id="gDis"${run?" disabled title=\"실행 중에는 해체할 수 없습니다\"":""}>그룹 해체</button><button id="gDup">복제</button><span class="grow"></span><button id="gCancel">취소</button><button class="pri" id="gSave">저장</button></div>`;
  gBind(run);}
function gSplit(kids){const cols={};for(const c of G_COLS)cols[c.k]=[];
  for(const c of kids)(cols[gCol(c)]||cols.wait).push(c);return cols;}
/* 네 탭 — 4등분 · 높이 28 · 점 5px · 수는 mono, 0 이면 흐리게. 켜진 탭은 아랫변 2px 를 그 열의 색으로.
   「대기」 탭은 드롭 자리이기도 하다 — 완료·실패 행을 여기에 끌어다 놓으면 대기로 되살아난다 (옛 창의 열 사이 끌기). */
function gTabsHtml(cols,run){
  return `<div class="g-tabs" role="tablist">${G_COLS.map((c)=>{const n=cols[c.k].length;
    return `<button class="${c.k}${G.tab===c.k?" on":""}" role="tab" aria-selected="${G.tab===c.k?"true":"false"}" data-tab="${c.k}"${c.k==="wait"&&!run?' data-drop="1"':""}><span class="dot"></span>${c.t} <span class="n${n?"":" z"}">${fmtN(n)}</span></button>`;}).join("")}</div>`;}
/* 목록 — **켜진 탭의 항목만**. 대기 탭(실행 중 아님)만 놓기를 받는다 (나머지 열은 러너만 옮긴다). */
function gListHtml(cols,run){const k=G.tab;const rows=cols[k]||[];const c=gColOf(k);
  const drop=k==="wait"&&!run;
  const note=k==="run"?`<div class="mt">${esc(c.mt)}</div>`:(rows.length?"":`<div class="mt">${esc(c.mt)}</div>`);
  return `<div class="g-list" data-tab="${k}"${drop?' data-drop="1"':""}>${rows.map((x)=>gRowHtml(x,run)).join("")}${note}</div>`;}
/* 가공 행의 두 갈래 — 담기 서랍의 것과 같은 선택이다. 「걸기만」이 기본이고, 이 선택은 그룹의
   「가공은 완료까지 기다렸다 다음으로」(waitAlter) 보다 우선한다 — waitAlter 는 「수령까지」인 자식만 건드린다.
   이름 아래 상태 줄 옆에 작게 붙인다. */
function gModeHtml(c,run){const m=gMode(c);
  return `<span class="gmode" role="group" aria-label="가공 방식">`
    +`<button data-md="none" class="${m==="none"?"on":""}"${run?" disabled":""} title="가공만 걸기 — 등록하고 바로 다음 항목으로 (기본)">걸기만</button>`
    +`<button data-md="later" class="${m==="later"?"on":""}"${run?" disabled":""} title="${c.collect==="wait"?"수령까지 — 지금은 그 자리에서 완료를 기다리는 항목입니다 (아래 「완료까지 기다렸다 다음으로」가 켜져 있습니다)":"등록한 뒤 다음 항목을 먼저 처리하고, 완료되면 돌아와 수령합니다 — 큐는 멈추지 않습니다"}">수령까지</button></span>`;}
/* 항목 행 한 줄: ⋮⋮ · 종류 · 이름/상태·예상 · 수량 스테퍼 · ✕.
   ✕ 하나 = 「빼기」(그룹 밖 대기 열로 · 지우지 않는다). 작업 중 행은 수량 잠김(스테퍼 흐리게).
   **완료 행도 잠근다** — 서버 `update` 는 pending·stopped·error 만 받는다(workqueue.update:
   done 은 `not_editable`). 열어 두면 + 를 눌러 200→300 이 되고 서버는 거절하는데 화면은 300 을 남겼다.
   실패 열(error·stopped)은 서버가 받으므로 열어 둔다 — 수량을 고쳐 「대기」로 되살리는 길이다.
   잠금 규칙은 서버의 편집 가능 규칙과 **같은 것 하나**여야 한다: gEditable. */
const gEditable=(c,run)=>!run&&c.type!=="collect"&&(gCol(c)==="wait"||gCol(c)==="fail");
function gRowHtml(c,run){const col=gCol(c);const lock=!gEditable(c,run);
  return `<div class="g-row ${col}" data-card="${esc(c.id)}" data-col="${col}" draggable="${run?"false":"true"}">
    <span class="hd" aria-hidden="true">⋮⋮</span>
    <span class="kd ${c.type}">${G_KO[c.type]||c.type}</span>
    <span class="tx"><span class="nm" title="${esc(c.name||"")}">${esc(c.name||"")}</span><span class="st"${gCol(c)==="fail"?` title="${esc(gStatus(c))}"`:""}><span class="dot"></span><span class="gsub">${esc(gStatus(c))}</span>${c.type==="alter"?gModeHtml(c,run):""}</span></span>
    <span class="stepper${lock?" lock":""}"><button data-dec title="−${gStep(c)}"${lock?" disabled":""}>−</button><input class="v" type="number" data-gin min="1" max="${c.type==="gather"?99999:999}" value="${gQty(c)}" title="직접 입력"${lock?" disabled":""}><span class="u">${gUnit(c)}</span><button data-inc title="+${gStep(c)}"${lock?" disabled":""}>+</button></span>
    <button class="x" data-out title="이 항목을 그룹에서 빼 대기 열로 보냅니다 (지우지 않습니다)" aria-label="그룹에서 빼기"${run?" disabled":""}>✕</button></div>`;}
/* 진행 막대와 회차는 **한 줄** (「진행」 · 4px 막대 · 「회차 n / m」 mono 골드) */
/* `loop` 는 **지금(또는 멈춘) 회차**다 — 러너는 정지한 그룹을 `loop` 회차부터 잇는다 (workqueue._run_group 의 first).
   그래서 끝난 회차는 늘 `loop-1` 이다. 전에는 멈춘 그룹을 `loop` 로 세어, 1회차에서 멈춘 그룹이
   막대 33% 인데 요약 카드는 「남은 회차 3」(서버 loop_left = repeat-loop+1)이었다.
   다 끝난(done) 그룹만 가득 채운다. 도는 회차는 반쯤 찬 것으로. */
function gLoopsDone(g){const loop=g.loop||0,rep=g.repeat||1;
  if(g.status==="done")return rep;
  return Math.max(0,loop-1)+(gRunning(g)&&loop?0.5:0);}
function gProgHtml(){const g=G.g;const loop=g.loop||0,rep=g.repeat||1;
  const done=gLoopsDone(g);
  const pct=Math.max(0,Math.min(100,Math.round(done/Math.max(1,rep)*100)));
  return `<div class="g-prog"><span class="lb">진행</span><span class="bar"><i style="width:${pct}%"></i></span><span class="rt">회차 ${fmtN(gLoopShown(g))} / ${fmtN(rep)}</span></div>`;}
/* 회차 글 — 도는 중이면 **지금 회차**(「회차 2 / 3」 · 남은 회차 2). 멈춰 있으면 끝낸 회차 수
   (1회차에서 멈춘 그룹 = 「회차 0 / 3」 · 남은 회차 3 — 새 그룹 「회차 0 / 1」과 같은 읽기). 끝난 그룹은 가득. */
function gLoopShown(g){const loop=g.loop||0,rep=g.repeat||1;
  if(g.status==="done")return rep;
  return gRunning(g)?loop:Math.max(0,loop-1);}
/* 요약 카드 한 장: 왼쪽 반복 회차 스테퍼 / 오른쪽 남은 회차 · 호출 예상 · 날개 소모 (큰 숫자 3) + 보유 한 줄.
   오른쪽만 미리보기가 올 때 갈아 끼운다 (gSumRefresh) — 스테퍼는 손잡이가 묶여 있어 다시 만들지 않는다. */
function gSumHtml(){const run=gRunning(G.g);const rep=G.draft.repeat;
  return `<div class="g-sum">
    <span class="rep"><span class="k">반복 회차</span><span class="stepper" title="${run?"실행 중에는 회차를 늘리기만 할 수 있습니다":"1 ~ 20"}"><button id="gRepD" title="−1">−</button><span class="v" id="gRepV">${fmtN(rep)}</span><span class="u">회</span><button id="gRepI" title="+1">+</button></span></span>
    ${gEstHtml()}</div>`;}
// 남은 회차: 실행 중이면 이번 회차를 포함해 repeat - loop + 1, 아니면 repeat (서버와 같은 규칙)
function gEstHtml(){const p=G.prev;const g=G.g;const loop=(g&&g.loop)||0;
  const left=p&&p.loopLeft!=null?p.loopLeft:(g&&(g.status==="done"||g.status==="error")?0:Math.max(0,(G.draft.repeat||1)-loop+(loop?1:0)));
  const have=wingsHave();
  /* **호출 수로 곱하지 않는다** — 수령은 날개를 안 쓴다.
     서버가 `groups[]` 에 `wings`·`wingCalls` 를 실어 준다. 없으면 「—」. */
  const wc=p&&p.wingCalls!=null?Number(p.wingCalls):null;
  const wings=p&&p.wings!=null?Number(p.wings):(wc!=null?wc*WINGS_PER_CALL:null);
  const calls=p&&p.calls!=null?`약 ${fmtN(p.calls)}<small>회</small>`:"—";
  return `<div class="g-est">
    <span class="cell"><span class="k">남은 회차</span><span class="v">${fmtN(left)}</span></span>
    <span class="cell"><span class="k">호출 예상</span><span class="v">${calls}</span></span>
    <span class="cell"><span class="k">날개 소모</span><span class="v">${wings!=null?`약 ${fmtN(wings)}`:"—"}</span></span>
    <span class="note">정령의 날개 보유 <b>${have!=null?fmtN(have):"—"}</b></span></div>`;}

/* ── 이벤트 ── */
function gBind(run){const d=gEl();
  $("gX").onclick=()=>d.close();$("gCancel").onclick=()=>d.close();
  const nm=$("gName");if(nm)nm.oninput=()=>{G.draft.name=nm.value.slice(0,24);};
  // 실행 중이면 회차는 늘리기만 (group_update 는 repeat 증가만 허용)
  const lo=run?Math.max(1,G.g.repeat||1):1;
  const setRep=(dv)=>{const v=Math.max(lo,Math.min(20,G.draft.repeat+dv));if(v===G.draft.repeat)return;G.draft.repeat=v;
    const el=$("gRepV");if(el)el.textContent=fmtN(v);gSumRefresh();};
  MW.holdBtn($("gRepD"),()=>setRep(-1));MW.holdBtn($("gRepI"),()=>setRep(1));
  $("gAdd").onclick=()=>{if(MW.openDrawer)MW.openDrawer({target:G.id});};
  $("gErr").querySelectorAll("[data-v]").forEach((b)=>{b.onclick=()=>{if(b.disabled)return;G.draft.onError=b.dataset.v;
    $("gErr").querySelectorAll("[data-v]").forEach((x)=>x.classList.toggle("on",x.dataset.v===G.draft.onError));};});
  const sw=(el,key)=>{if(!el)return;el.onclick=()=>{if(el.disabled)return;G.draft[key]=!G.draft[key];
    el.classList.toggle("on",G.draft[key]);el.setAttribute("aria-checked",G.draft[key]?"true":"false");};};
  sw($("gRetry"),"retryFailed");sw($("gWait"),"waitAlter");
  $("gSave").onclick=gSave;$("gDup").onclick=gDup;$("gDis").onclick=gDissolve;
  d.querySelectorAll(".g-tabs [data-tab]").forEach((b)=>{b.onclick=()=>gTab(b.dataset.tab);});
  gBindRows(run);
  d.querySelectorAll("[data-drop]").forEach(gDropZone);}
/* 탭 바꾸기 — 목록만 갈아 끼운다 (창 전체를 다시 만들면 스크롤·이름 입력이 튄다) */
function gTab(k){if(!gColOf(k)||k===G.tab)return;G.tab=k;const d=gEl();const run=gRunning(G.g);
  d.querySelectorAll(".g-tabs [data-tab]").forEach((b)=>{const on=b.dataset.tab===k;b.classList.toggle("on",on);b.setAttribute("aria-selected",on?"true":"false");});
  const old=d.querySelector(".g-list");if(!old)return;
  const tmp=document.createElement("div");tmp.innerHTML=gListHtml(gSplit(G.g.items||[]),run);
  const nu=tmp.firstElementChild;old.replaceWith(nu);gBindRows(run);if(nu.dataset.drop)gDropZone(nu);}
function gBindRows(run){const d=gEl();
  d.querySelectorAll(".g-row").forEach((el)=>{const id=el.dataset.card;
    const c=(G.g.items||[]).find((x)=>x.id===id);if(!c)return;
    const dec=el.querySelector("[data-dec]"),inc=el.querySelector("[data-inc]");
    const inp=el.querySelector("[data-gin]");
    /* 값을 쓰는 자리는 **하나**다. 스테퍼도 직접 치기도 여기로 모은다 — 둘로 나누면
       한쪽만 고쳤을 때 조용히 어긋난다 (날개 숫자에서 겪은 그 패턴). */
    const put=(v,fromInput)=>{const hi=c.type==="gather"?99999:999;
      v=Math.max(1,Math.min(hi,Math.round(v)||1));
      if(c.type==="gather")c.target=v;else c.count=v;
      // 치고 있는 칸은 건드리지 않는다 — 커서가 튀고 「10」을 치다 「1」에서 잘린다
      if(inp&&!fromInput&&Number(inp.value)!==v)inp.value=v;
      const sb=el.querySelector(".gsub");if(sb){sb.textContent=gStatus(c);if(sb.parentNode&&sb.parentNode.title)sb.parentNode.title=sb.textContent;}gQtyPush(c);};
    const bump=(dv)=>put(gQty(c)+dv*gStep(c));
    if(dec&&!dec.disabled)MW.holdBtn(dec,()=>bump(-1));
    if(inc&&!inc.disabled)MW.holdBtn(inc,()=>bump(1));
    if(inp&&!inp.disabled){inp.oninput=()=>{const v=Number(inp.value);if(inp.value!==""&&isFinite(v))put(v,true);};
      inp.onblur=()=>{inp.value=gQty(c);};}   // 비워 두고 나가면 지금 값으로 되돌린다
    const md=el.querySelector(".gmode");
    if(md)md.querySelectorAll("[data-md]").forEach((b)=>{b.onclick=()=>{if(b.disabled)return;
      if(b.dataset.md===gMode(c))return;gModeSet(el,c,b.dataset.md);};});
    const out=el.querySelector("[data-out]");if(out&&!out.disabled)out.onclick=()=>gPullOut(id);
    if(!run)gDragRow(el,id);});}
// 수량 수정은 400ms 모아 한 번만 보낸다 (스테퍼 연타로 요청이 쏟아지지 않게)
function gQtyPush(c){clearTimeout(G.qT.get(c.id));
  G.qT.set(c.id,setTimeout(async()=>{G.qT.delete(c.id);
    const patch=c.type==="gather"?{target:gQty(c)}:{count:gQty(c)};
    const r=await api("/api/queue",{op:"update",id:c.id,...patch});
    if(!r||!r.ok){toast("수량을 바꾸지 못했습니다: "+((r&&(r.message||r.error))||""));}
    await gAfter(r,true);},400));}   // 목록을 다시 그리지 않는다 — 스크롤이 튄다

/* 가공 방식 바꾸기 — 기존 update op 하나. 그 행만 제자리에서 고치고 목록은 다시 그리지 않는다
   (다시 그리면 목록 스크롤이 맨 위로 돌아간다). 거절당하면 눌렀던 것을 되돌린다. */
async function gModeSet(el,c,v){const before=c.collect;
  const paint=(m)=>{const md=el.querySelector(".gmode");
    if(md)md.querySelectorAll("[data-md]").forEach((x)=>x.classList.toggle("on",x.dataset.md===m));
    const sb=el.querySelector(".gsub");if(sb)sb.textContent=gStatus(c);};
  c.collect=v;paint(v);
  const r=await api("/api/queue",{op:"update",id:c.id,collect:v});
  if(!r||!r.ok){c.collect=before;paint(gMode(c));
    toast("가공 방식을 바꾸지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  if(r.state&&MW.renderBoard){try{MW.renderBoard(r.state);}catch{}}else if(MW.refreshQueue)await MW.refreshQueue();
  const g=gFind(G.id);if(g)G.g=g;                 // 새 상태를 받아 둔다 — gTick 이 이 변화로 다시 그리지 않게
  gFetchPreview();}                               // 걸기만은 수령 호출이 없다 → 호출 예상·소모가 바뀐다

/* ── 드래그: 받는 자리는 대기 목록(순서)과 「대기」 탭(되살리기) 둘뿐 (나머지 열은 러너만 옮긴다) ── */
function gDragRow(el,id){el.addEventListener("dragstart",(e)=>{G.drag=id;el.classList.add("dragging");
  try{e.dataTransfer.setData("text/plain",id);e.dataTransfer.effectAllowed="move";}catch{}});
  el.addEventListener("dragend",()=>{G.drag=null;el.classList.remove("dragging");
    gEl().querySelectorAll("[data-drop]").forEach((c)=>c.classList.remove("over"));});}
function gDropZone(zone){
  zone.addEventListener("dragover",(e)=>{if(!G.drag)return;e.preventDefault();e.dataTransfer.dropEffect="move";zone.classList.add("over");});
  zone.addEventListener("dragleave",(e)=>{if(!zone.contains(e.relatedTarget))zone.classList.remove("over");});
  zone.addEventListener("drop",async(e)=>{e.preventDefault();zone.classList.remove("over");const id=G.drag;G.drag=null;if(!id)return;
    // 놓은 자리의 위/아래로 index 를 정한다 (대기 목록의 위아래 = 실행 순서). 「대기」 탭에 놓으면 맨 끝
    const inList=zone.classList.contains("g-list");
    const rows=inList?[...zone.querySelectorAll(".g-row")]:[];
    let index=inList?rows.length:undefined;   // 「대기」 탭에 놓았다 = 대기 맨 끝
    for(let i=0;i<rows.length;i++){const r=rows[i].getBoundingClientRect();if(e.clientY<r.top+r.height/2){index=i;break;}}
    const moving=rows.findIndex((c)=>c.dataset.card===id);if(moving>=0&&moving<index)index--;
    const c=(G.g.items||[]).find((x)=>x.id===id);
    // 완료·실패 행을 대기로 되살릴 때는 reset 을 먼저
    if(c&&gCol(c)!=="wait"){const r0=await api("/api/queue",{op:"reset",id});
      if(!r0||!r0.ok){toast("되살리지 못했습니다: "+((r0&&(r0.message||r0.error))||""));return;}}
    const req={op:"move_item",id,to:G.id};if(index!=null)req.index=index;
    const r=await api("/api/queue",req);
    if(!r||!r.ok){toast("옮기지 못했습니다: "+((r&&(r.message||r.error))||""));}
    if(c&&gCol(c)!=="wait")G.tab="wait";           // 되살린 행이 간 곳을 보여 준다
    await gAfter(r);});}

/* ── 편집 op ── */
/* `keep` 이면 **목록을 다시 그리지 않는다.**

   그룹 창에서 아래쪽 행의 `+`/`−` 를 누르면 목록 스크롤이 맨 위로 튀어 누르던 행이
   화면 밖으로 밀려났다. 까닭은 수량을 보낸 뒤
   여기서 창을 통째로 다시 만들었기 때문이다. 수량은 이미 그 자리에서 고쳐 뒀으므로
   다시 만들 이유가 없다 — 요약만 새로 받는다. 가공 방식(`gModeSet`)은 이미 그렇게 하고 있었다.

   **`G.g` 도 갈아치우지 않는다**: 갈아치우면 버튼이 쥐고 있던 항목이 옛 객체가 되어
   다음 누름이 허공을 고친다 (board.js 가 같은 까닭으로 「it 을 붙잡아 두지 마라」를 적어 뒀다). */
/* **거절당하면 `keep` 이어도 서버 상태로 다시 그린다**. 화면은 누른 값을 먼저 적어 두므로,
   거절 뒤에 그대로 두면 서버에 없는 값(300 · 예상 3회)이 남는다. 틀린 값이 남는 것보다 스크롤을 한 번
   다시 맞추는 편이 낫다 — 목록·본문 스크롤은 제자리로 돌려놓는다. */
async function gAfter(r,keep){const ok=!!(r&&r.ok);
  if(ok&&r.state&&MW.renderBoard){try{MW.renderBoard(r.state);}catch{}}
  else if(MW.refreshQueue)await MW.refreshQueue();
  const g=gFind(G.id);if(!g){gEl().close();return;}
  if(keep&&ok){gSumRefresh();gFetchPreview();return;}
  const d=gEl(),sc=(q)=>{const e=d.querySelector(q);return e?e.scrollTop:0;};
  const top={b:sc(".dlg-b"),l:sc(".g-list")};
  G.g=g;gRender();
  const b=d.querySelector(".dlg-b"),l=d.querySelector(".g-list");if(b)b.scrollTop=top.b;if(l)l.scrollTop=top.l;
  gFetchPreview();}
/* 그룹에서 빼기 — 루트 대기 열 맨 아래로 보낸다. 행의 ✕ 하나가 이것이다.
   서버는 처음부터 `move_item {to:"root"}` 로 할 수 있었다. 막고 있던 것은 화면뿐이었다
   (전에는 한 장 잘못 넣으면 그룹을 통째로 해체하거나 지우고 다시 담는 수밖에 없었다).
   완료·실패 행은 보드 드롭과 같은 규칙으로 먼저 reset 해 대기로 되살린다. */
async function gPullOut(id){const c=(G.g.items||[]).find((x)=>x.id===id);
  if(c&&gCol(c)!=="wait"){const r0=await api("/api/queue",{op:"reset",id});
    if(!r0||!r0.ok){toast("되살리지 못했습니다: "+((r0&&(r0.message||r0.error))||""));return;}}
  const r=await api("/api/queue",{op:"move_item",id,to:"root"});   // index 생략 = 루트 맨 끝
  if(!r||!r.ok){toast("빼지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  toast(`그룹에서 뺐습니다: ${(c&&c.name)||"항목"} — 대기 열 맨 아래`);
  await gAfter(r);}
/* 지우기(remove) — 행에서는 ✕ 가 「빼기」 하나로 합쳐졌다. 지우는 길은 보드 카드의 × 로 남아 있고,
   여기서는 다른 화면(MW.gRemove)이 부를 수 있게만 둔다. */
async function gRemove(id){const r=await api("/api/queue",{op:"remove",id});
  if(!r||!r.ok){toast("지우지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  await gAfter(r);}
async function gSave(){const g=G.g,dr=G.draft;const patch={};
  const name=(dr.name||"").trim();if(name!==(g.name||""))patch.name=name;
  if(dr.repeat!==(g.repeat||1))patch.repeat=dr.repeat;
  if(dr.onError!==(g.onError||"continue"))patch.onError=dr.onError;
  if(!!dr.retryFailed!==!!g.retryFailed)patch.retryFailed=!!dr.retryFailed;
  if(!!dr.waitAlter!==!!g.waitAlter)patch.waitAlter=!!dr.waitAlter;
  if(!Object.keys(patch).length){gEl().close();return;}
  const r=await api("/api/queue",{op:"group_update",id:G.id,patch});
  if(!r||!r.ok){toast("저장하지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  toast("그룹 설정을 저장했습니다");
  if(r.state&&MW.renderBoard){try{MW.renderBoard(r.state);}catch{}}else if(MW.refreshQueue)await MW.refreshQueue();
  gEl().close();}
async function gDup(){const r=await api("/api/queue",{op:"group_duplicate",id:G.id});
  if(!r||!r.ok){toast("복제하지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  toast("그룹을 복제했습니다");await gAfter(r);}
async function gDissolve(){if(!await askOk(`「${G.draft.name||G.g.name}」 을 풀고 안의 항목을 대기 열로 펼칩니다. 항목은 지워지지 않습니다.`,{title:"그룹을 해체할까요?",ok:"해체"}))return;
  const r=await api("/api/queue",{op:"group_dissolve",id:G.id});
  if(!r||!r.ok){toast("해체하지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  toast("그룹을 해체했습니다");
  if(r.state&&MW.renderBoard){try{MW.renderBoard(r.state);}catch{}}else if(MW.refreshQueue)await MW.refreshQueue();
  gEl().close();}

/* ── 요약(미리보기) ── */
async function gFetchPreview(){const id=G.id;const p=await api("/api/queue/preview");
  if(G.id!==id||!p)return;const row=((p.groups||[]).find((x)=>x&&x.id===id))||null;
  G.prev=row;gSumRefresh();}
function gSumRefresh(){const d=gEl();if(!d.open)return;const box=d.querySelector(".g-est");if(!box)return;
  const tmp=document.createElement("div");tmp.innerHTML=gEstHtml();box.replaceWith(tmp.firstElementChild);}

/* ── 열기 ── */
function openGroup(id){const g=gFind(id);if(!g){toast("그룹을 찾지 못했습니다");return;}
  const d=gEl();G.id=id;G.g=g;G.prev=null;G.drag=null;
  G.draft={name:g.name||"",repeat:g.repeat||1,onError:g.onError||"continue",retryFailed:!!g.retryFailed,waitAlter:!!g.waitAlter};
  // 실행 중이면 「작업 중」 탭으로 연다 — 아니면 「대기」
  G.tab=gRunning(g)&&(g.items||[]).some((c)=>gCol(c)==="run")?"run":"wait";
  gRender();if(!d.open)d.showModal();gFetchPreview();
  clearInterval(G.timer);G.timer=setInterval(gTick,2000);
  if(!d.dataset.built){d.dataset.built="1";d.addEventListener("close",()=>{clearInterval(G.timer);G.timer=null;G.id=null;G.g=null;});}}
// 러너가 진행하면 행이 탭을 옮긴다 — 편집 중(스테퍼 누름·드래그·이름 입력)에는 다시 그리지 않는다
function gTick(){const d=gEl();if(!d.open||!G.id)return;
  if(G.drag||(MW.HOLDS&&MW.HOLDS.size)||G.qT.size)return;
  const ae=document.activeElement;if(ae&&ae.id==="gName")return;
  const g=gFind(G.id);if(!g){d.close();return;}
  if(JSON.stringify(g)===JSON.stringify(G.g))return;
  G.g=g;gRender();}

Object.assign(window.MW,{G,G_COLS,gEditable,gWhy,gLoopsDone,gLoopShown,gAfter,openGroup,gRender,gFind,gSave,gDup,gDissolve,gFetchPreview,gMode,gModeSet,gPullOut,gRemove,gTab,
  gTabsHtml,gListHtml,gRowHtml,gSumHtml,gEstHtml,gProgHtml,gSplit});
