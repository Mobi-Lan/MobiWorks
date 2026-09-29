// drawer.js — 담기 서랍(dialog#drawer). 목록·부족분은 전부 기존 데이터: /api/work 스냅숏(S.snap)과 /api/plan 계산(planRows/planCalc).
// 새 조회 API 는 만들지 않는다. 담기는 기존 op 를 쓰고, 담을 곳이 그룹이면 요청에 group:"<id>" 를 얹는다.
const DW={target:"root",q:"",type:"all",skill:null,chain:true,qty:new Map(),mode:new Map(),added:0,addedWhere:"",
  limit:200,rows:[],cache:new Map(),pending:false,note:"",
  // 장바구니 — **담기는 바로 담는다**. 이건 「뭘 골랐나」를 보여 주고 고치는 판이다.
  // `fresh` = 이번에 담은 큐 항목 id (대기 열에서 그 줄만 표시한다),
  // `dim` = 이번에 담기를 누른 행 id (버튼을 「✓」 외곽선으로 바꾼다).
  cart:false,fresh:new Set(),dim:new Set()};
const DW_PAGE=200;
const DW_KO={gather:"채집",craft:"제작",alter:"가공",collect:"수령",play:"연주",notify:"알림"};
const dwStep=(k)=>k==="gather"?100:1;                       // 채집 100 단위·하한 100 / 제작·가공 1 단위·하한 1
const dwQty=(r)=>{const v=DW.qty.get(r.id);return v!=null?v:dwStep(r.k);};
const dwUnit=(k)=>k==="gather"?"개":k==="collect"?"건":"회";
// 둘째 줄의 「 · 」 — 넓은 폭(6열 표)에서는 CSS 가 숨기고, 좁은 폭에서만 글자로 보인다
// (양옆 공백은 글줄 안에서 「재봉 · 상급 가죽 6/4」 처럼 띄우고, flex 항목의 가장자리에서는 저절로 잘린다)
const DW_SEP='<span class="sep"> · </span>';
// 담은 행의 담기 글자 — 담으면 「✓」 하나로 바꾼다
const DW_DONE="✓";
// 가공을 담을 때의 방식 — "none"(걸기만) 이 기본, "later"(가공 후 수령까지).
const dwMode=(r)=>DW.mode.get(r.id)||"none";
const dwEl=()=>{let d=$("drawer");if(!d){d=document.createElement("dialog");d.id="drawer";document.body.appendChild(d);}return d;};

/* ── 목록 (스냅숏 그대로) ── */
function dwAll(){const s=S.snap||{};const out=[];
  for(const x of s.gather||[])out.push({k:"gather",id:"gather:"+x.name,name:x.name,sub:x.skill||"",skill:x.skill||"미분류",per:null,rec:null,ok:!!x.toolOk,blocked:!x.toolOk});
  for(const k of ["craft","alter"])for(const r of s[k]||[])
    out.push({k,id:rid(k,r),name:r.name,sub:[r.cat,(r.variants>1?`경로 ${r.variant||1}/${r.variants}`:"")].filter(Boolean).join(" · "),
      skill:null,per:r.per||1,rec:r,ok:!r.blocked,blocked:!!r.blocked});
  out.push(...dwCollectRows());
  return out;}
/* 수령 행은 카탈로그가 아니다 — works.js 가 스냅숏에서 만든 「지금 완료분이 있는 시설」(S.collectable) 이 전부다.
   가공 대기열이 비어 있거나 완료분이 없으면 여기도 비고, 칩은 0 으로 남는다. 없는 시설을 지어내지 않는다. */
function dwCollectRows(){return (S.collectable||[]).map((c)=>({k:"collect",id:"collect:"+(c.facility||""),
  name:c.facility||"(시설 미상)",facility:c.facility||"",work:c.name||"",n:c.n||0,
  sub:c.name?`완료된 작업 · ${c.name}`:"",skill:null,per:null,rec:null,ok:true,blocked:false}));}
const dwColKeyOf=()=>(S.collectable||[]).map((c)=>`${c.facility}:${c.n}`).join("|");
// 검색: 공백 토큰 전부 AND — 이름(정규화·원문)과 확인된 재료명까지 (list-core 의 qMatch 와 같은 규칙, 서랍은 자기 검색어를 쓴다)
// 수령 행은 시설명이 이름이라, 완료된 작업 이름으로도 찾을 수 있게 한 칸 더 본다
function dwMatch(r,tokens){if(!tokens.length)return true;const nm=normKey(r.name),low=r.name.toLowerCase();
  const mats=r.rec&&r.rec.known?Object.keys(r.rec.known):null;
  const wk=r.work?r.work.toLowerCase():"",wn=r.work?normKey(r.work):"";
  const fc=(r.rec&&r.rec.facility)?String(r.rec.facility).toLowerCase():"";   // 가공 행의 시설명 — 가공 탭의 시설 「담기」가 시설 이름으로 이 서랍을 연다. 시설을 모르는 레시피는 빈 문자열이라 그냥 안 걸린다
  return tokens.every((t)=>nm.includes(normKey(t))||low.includes(t)||(wk&&(wk.includes(t)||wn.includes(normKey(t))))
    ||(fc&&(fc.includes(t)||normKey(fc).includes(normKey(t))))
    ||(mats?mats.some((m)=>m.toLowerCase().includes(t)):false));}
function dwFilter(){const tokens=DW.q.toLowerCase().split(/\s+/).filter(Boolean);
  const base=DW.rows.filter((r)=>dwMatch(r,tokens));
  const byType=(t)=>base.filter((r)=>t==="all"||r.k===t);
  const typed=byType(DW.type);
  const shown=DW.type==="gather"&&DW.skill?typed.filter((r)=>r.skill===DW.skill):typed;
  return {base,counts:{all:base.length,gather:byType("gather").length,craft:byType("craft").length,
    alter:byType("alter").length,collect:byType("collect").length},shown};}

/* ── 재료 칸: 「이름 (가방+창고)/필요」. 계산은 plan.js 의 planRows·planCalc 를 그대로 쓴다 ── */
function dwNeed(r){if(!r.rec)return [];let rows=DW.cache.get(r.id);if(!rows){rows=planRows(r.rec);DW.cache.set(r.id,rows);}return rows;}
function dwMats(r,n){const rows=dwNeed(r);if(!rows.length)return "";
  const cells=rows.map((x)=>({x,c:planCalc(x,n)}));
  cells.sort((a,b)=>(b.c.short>0?1:0)-(a.c.short>0?1:0));
  // 칩 사이의 `.sep`(·) 은 넓은 폭에서 숨고, 좁은 폭에서 「생가죽 73/10 · 가죽 867/5」 한 줄 글로 이어 준다
  const head=cells.slice(0,2).map(({x,c})=>{const cls=c.short>0?"short":(c.fromStorage?"move":"");
    const have=c.have==null?"?":fmtN(c.have);
    return `<span class="mat ${cls}" title="${esc(x.name)} · 가방 ${c.bag==null?"?":fmtN(c.bag)} · 창고 ${fmtN(c.sto)}${c.short>0?` · ${fmtN(c.short)}개 부족`:""}">${esc(x.name)} <b>${have}/${fmtN(c.req)}</b></span>`;}).join(DW_SEP);
  return head+(cells.length>2?`${DW_SEP}<span class="mat" title="${esc(cells.slice(2).map((y)=>y.x.name).join(", "))}">외 ${cells.length-2}</span>`:"");}
// 부족분 안내(발) — 「…를 담으면 부족한 A 2 · B 2 를 만드는 항목을 앞에 같이 담을지 묻습니다」
function dwNoteOf(r,n){if(!r.rec||r.k==="gather")return "";
  const lack=dwNeed(r).map((x)=>({x,c:planCalc(x,n)})).filter((y)=>y.c.short>0&&(y.x.source==="gather"||y.x.source==="craft"||y.x.source==="alter"));
  if(!lack.length)return "";
  // r.name 은 서버가 준 레시피 이름이다 — 이 문자열은 dwFoot 이 innerHTML 로 넣으므로 이스케이프한다
  return `「${esc(r.name)}」를 담으면 부족한 ${lack.slice(0,3).map((y)=>`<b>${esc(y.x.name)} ${fmtN(y.c.short)}</b>`).join(" · ")}${lack.length>3?` 외 ${lack.length-3}`:""} 를 만드는 항목을 앞에 같이 담을지 묻습니다`;}

/* ── 담을 곳: 보드 상태에서 그룹 목록을 가져온다 (새 조회 없음) ── */
function dwBoard(){if(MW.boardState){try{return MW.boardState()||null;}catch{}}
  return (window.Q&&Q.data)?Q.data:null;}
function dwGroups(){const st=dwBoard();const items=(st&&st.items)||[];
  return items.filter((i)=>i&&i.type==="group").map((g)=>({id:g.id,name:g.name||"그룹",
    running:g.column==="run"||(st&&st.currentGroup===g.id)}));}
function dwTargetName(){if(DW.target==="root")return "대기 열";const g=dwGroups().find((x)=>x.id===DW.target);return g?`그룹 · ${g.name}`:"대기 열";}
function dwRenderTargets(){const box=$("dwTgts");if(!box)return;const gs=dwGroups();
  if(DW.target!=="root"&&!gs.some((g)=>g.id===DW.target))DW.target="root";   // 그룹이 사라졌으면 대기 열로
  box.innerHTML=`<button data-t="root" class="${DW.target==="root"?"on":""}">대기 열</button>`
    +gs.map((g)=>`<button data-t="${esc(g.id)}" class="${DW.target===g.id?"on":""}${g.running?" busy":""}"${g.running?' data-busy="1"':""}>${g.running?"⃠ ":""}그룹 · ${esc(g.name)}</button>`).join("")
    +'<button data-new class="new">+ 새 그룹</button>';
  box.querySelectorAll("[data-t]").forEach((b)=>{b.onclick=()=>{
    if(b.dataset.busy){dwMsg("실행 중인 그룹에는 담을 수 없습니다 — 정지 후 또는 그룹 설정에서",true);return;}
    DW.target=b.dataset.t;dwMsg("");dwRenderTargets();dwFoot();};});
  const nb=box.querySelector("[data-new]");if(nb)nb.onclick=dwNewGroup;}
// 한 줄에 다 안 들어가면 말줄임된다 — 전문은 title 로 남긴다 (서버가 내는 안내가 길 때가 있다)
function dwMsg(t,warn){const m=$("dwTgtMsg");if(!m)return;m.textContent=t||"";m.title=t||"";m.className="dw-tgtmsg"+(warn?" warn":"");}
// 「+ 새 그룹」 — 그 자리에서 이름을 받는다 (빈 이름이면 서버가 「그룹 N」으로 정한다)
function dwNewGroup(){const box=$("dwNew");if(!box)return;box.hidden=false;const inp=box.querySelector("input");inp.value="";inp.focus();}
async function dwNewGroupGo(){const box=$("dwNew");const inp=box.querySelector("input");const name=inp.value.trim().slice(0,24);
  const r=await api("/api/queue",{op:"group_create",...(name?{name}:{})});
  if(!r||!r.ok){dwMsg((r&&(r.message||r.error))||"그룹을 만들지 못했습니다",true);return;}
  box.hidden=true;
  const items=((r.state&&r.state.items)||[]).filter((i)=>i.type==="group");
  const made=r.id||(r.item&&r.item.id)||(items.length?items[items.length-1].id:null);
  if(made)DW.target=made;
  if(MW.refreshQueue)await MW.refreshQueue();
  dwRenderTargets();dwFoot();dwMsg("");}

/* ── 표 ── */
function dwRenderBody(){const d=dwEl();if(!d.open)return;
  if(MW.HOLDS&&MW.HOLDS.size){DW.pending=true;return;}   // 스테퍼를 누르고 있는 동안에는 다시 그리지 않는다
  const {counts,shown}=dwFilter();
  const nEl=$("dwN");if(nEl)nEl.textContent=`${fmtN(counts.all)}건`;
  $("dwSeg").querySelectorAll("[data-ty]").forEach((b)=>{const k=b.dataset.ty;const free=k==="play"||k==="notify";const n=free?null:counts[k==="all"?"all":k];
    b.classList.toggle("on",DW.type===k);b.disabled=!free&&n===0&&DW.type!==k;   // 연주·알림은 목록이 아니라 판 — 건수 없음, 늘 열린다
    // 이름 + 건수(.c) — 좁은 폭 칩은 건수만 Mono 9px
    b.textContent=`${k==="all"?"전체":DW_KO[k]} `;
    if(n!=null){const c=document.createElement("span");c.className="c";c.textContent=fmtN(n);b.appendChild(c);}});
  dwRenderSkills(shown);
  const hd=$("dwHd");if(hd)hd.hidden=dwFree();
  if(dwFree()){const body=$("dwBody");body.innerHTML=DW.type==="play"?dwPlayHTML():dwNotifyHTML();dwBindFree(body);DW.note="";dwFoot();
    if(DW.type==="play")dwFolioLoad();return;}
  const body=$("dwBody");const list=shown.slice(0,DW.limit);
  if(!list.length){body.innerHTML='<div class="dw-empty">해당하는 항목이 없습니다.</div>';DW.note="";dwFoot();return;}
  body.innerHTML=list.map(dwRow).join("")+(shown.length>list.length?`<div class="dw-more"><button class="small" id="dwMore">더 보기 (${fmtN(list.length)} / ${fmtN(shown.length)})</button></div>`:"");
  dwBind(body,list);
  DW.note=(()=>{for(const r of list){const t=dwNoteOf(r,dwQty(r));if(t)return t;}return "";})();
  dwFoot();}
function dwRenderSkills(shown){const box=$("dwSkills");if(!box)return;
  if(DW.type!=="gather"){box.innerHTML="";return;}
  const cc={};for(const r of DW.rows)if(r.k==="gather")cc[r.skill]=(cc[r.skill]||0)+1;
  const F=(S.snap&&S.snap.filters)||{};
  const lab=(x)=>typeof x==="string"?x:(x&&x.label)||"";
  const srv=(F.gatherSkills||[]).map(lab).filter(Boolean);
  const keys=[...new Set([...srv,...Object.keys(cc)])].filter((c)=>c!=="미분류");if(cc["미분류"])keys.push("미분류");
  box.innerHTML=keys.map((c)=>`<button data-sk="${esc(c)}" class="${DW.skill===c?"on":""}"${cc[c]?"":" disabled"}>${esc(c)} ${fmtN(cc[c]||0)}</button>`).join("");
  box.querySelectorAll("[data-sk]").forEach((b)=>{b.onclick=()=>{DW.skill=DW.skill===b.dataset.sk?null:b.dataset.sk;DW.limit=DW_PAGE;dwRenderBody();};});}
/* 가공 방식 두 갈래 — 「걸기만」(기본) / 「가공 후 수령까지」.
   넓은 폭 표: 스테퍼 위에 붙는 고정 폭(104px) 칩 (행마다 +/− 의 x 위치는 그대로).
   좁은 폭 시트: 둘째 줄 끝의 글자 크기 칩 한 쌍 (`.l2 .ops`) — 좁은 폭의 행은 **한 줄**이고
   스테퍼·담기 말고는 자리가 없다. 그래서 행 높이를 안 늘리는
   둘째 줄 안으로 옮겼다. 두 벌은 CSS 가 폭마다 한쪽만 보여 준다. */
// short = 좁은 폭 둘째 줄의 짧은 라벨(「걸기」·「수령」) — 375px 에서 경로·재료 자리를 남기려고. 뜻은 title 에 그대로.
function dwModeHtml(r,short){const m=dwMode(r);
  return `<span class="dwmode" role="group" aria-label="가공 방식">`
    +`<button data-md="none" class="${m==="none"?"on":""}" title="가공만 걸기 — 등록하고 바로 다음 항목으로 (기본)">${short?"걸기":"걸기만"}</button>`
    +`<button data-md="later" class="${m==="none"?"":"on"}" title="가공 후 수령까지 — 등록한 뒤 다음 항목을 먼저 처리하고, 완료되면 돌아와 수령합니다 — 큐는 멈추지 않습니다">${short?"수령":"수령까지"}</button></span>`;}
/* ── 행 한 벌의 마크업, 두 폭 ──────────────────────────────────────────
   넓은 폭 = 6열 표 (종류 · 이름/경로 · ×산출 · 상태 · 재료 · 수량/단위/담기).
   좁은 폭 = 카드 행 (종류 칩 · 이름 / 「상태 · 경로 · 재료|예상」 한 줄 · 스테퍼(단위 안) · 담기 알약).


   둘째 줄이 **한 요소 한 줄**이어야 말줄임이 한 번에 걸린다 (상태·경로·재료를 격자 칸에 따로 놓으면
   각자 잘린다). 그래서 `.l2` 가 상태(`.dwst`)와 글줄(`.tx` = 경로 · 재료|예상)을 품고, 넓은 폭에서는
   `.l2`·`.tx` 를 `display:contents` 로 풀어 `.dwst`·`.mats` 가 예전처럼 표의 4·5열에 선다.
   `.sep`(·)·`.sub`(경로)·`.est`(예상)·`.x`(×산출)·`.u`(단위) 는 좁은 폭에서만 보이는 짝이다 — 넓은 폭의
   `.nm i`·`.dwnote`·`.per`·`.unit` 과 같은 값이며, CSS 가 폭에 따라 한쪽만 보여 준다. */
function dwStatusHTML(r){
  if(r.k==="collect")return `<span class="badge ok">완료 ${fmtN(r.n)}건</span>`;
  if(r.k==="gather")return r.ok?'<span class="badge ok">도구 OK</span>':'<span class="badge bad">도구 없음</span>';
  return badge(r.rec);}
// 둘째 줄 = `상태 · line2`, line2 = [sub, mats || est].join(' · ')
//   채집: 「도구 OK · 낚시 · 예상 1회」 / 제작: 「가능 · 재봉 · 경로 1/2 · 상급 가죽 6/4 · 옷감+ 337/2」 / 부족: 「재료 부족 · 가죽 가공 시설 · …」
function dwLine2HTML(r,n,ops){
  const est=r.k==="gather"?`<span class="est">예상 ${gpass(n)}회</span>`:"";
  const mats=`<span class="mats">${(r.k==="gather"||r.k==="collect")?"":dwMats(r,n)}</span>`;
  const hasTail=r.k==="gather"||(r.k!=="collect"&&dwNeed(r).length>0);
  return `<span class="l2">`
    +`<span class="dwst">${dwStatusHTML(r)}</span>`
    +(r.sub||hasTail?DW_SEP:"")
    +`<span class="tx">${r.sub?`<i class="sub">${esc(r.sub)}</i>`:""}${r.sub&&hasTail?DW_SEP:""}${est}${mats}</span>`
    // 좁은 폭 전용 꼬리 — 가공 방식 · 「최대」 · 수령 안내. 글줄(.tx)이 먼저 말줄임되고 이건 늘 보인다.
    +(ops?`<span class="ops">${ops}</span>`:"")
    +`</span>`;}
// 수령 행: 수량 스테퍼가 없다 — 시설당 1개이고 CLI 가 그 시설 완료분을 한 번에 전부 가져온다
function dwRowCollect(r){
  return `<div class="dw-r k-collect" data-id="${esc(r.id)}">`
    +`<span class="kd collect">${DW_KO.collect}</span>`
    +`<span class="nm"><b>${esc(r.name)}</b><i>${esc(r.sub)}</i></span>`
    +`<span class="per">—</span>`
    +dwLine2HTML(r,r.n,DW_SEP+'<span class="opn">시설당 1회 호출</span>')
    +`<span class="dw-q"><span class="qty"><span class="cq">${fmtN(r.n)}</span><span class="dwnote">시설당 1회 호출</span></span>`
    +`<span class="unit">${dwUnit(r.k)}</span>`
    +`<button class="go gold" data-add title="이 시설의 완료분을 한 번에 수령하는 항목을 담습니다">담기</button></span>`
    +`</div>`;}
function dwRow(r){if(r.k==="collect")return dwRowCollect(r);
  const n=dwQty(r);const st=dwStep(r.k);
  // ×산출 — 넓은 폭은 제 열(`.per`), 좁은 폭은 이름 옆 「가죽 ×3」(`.nm .x`)
  const per=r.k==="gather"?'<span class="per">—</span>':`<span class="per">×${fmtN(r.per)}</span>`;
  const x=r.k==="gather"?"":`<em class="x">×${fmtN(r.per)}</em>`;
  const dis=r.blocked?" disabled":"";
  const maxBtn=(r.k!=="gather"&&!r.blocked)?`<button class="mx" data-max title="재료·시설 상한까지 한 번에 담습니다">최대</button>`:"";
  // 좁은 폭 둘째 줄 끝: [걸기만|수령까지] · 최대 (넓은 폭은 아래 `.qty` 의 같은 짝을 쓴다)
  const modeHtml=r.k==="alter"?dwModeHtml(r):"";
  const ops=(r.k==="alter"?dwModeHtml(r,true):"")+maxBtn;
  return `<div class="dw-r k-${r.k}" data-id="${esc(r.id)}">`
    +`<span class="kd ${r.k}">${DW_KO[r.k]}</span>`
    +`<span class="nm"><b>${esc(r.name)}${x}</b>${r.sub?`<i>${esc(r.sub)}</i>`:"<i></i>"}</span>`
    +per
    +dwLine2HTML(r,n,ops)
    /* 값 칸(`.vb`) = 입력칸 + 단위(`.u`) — 좁은 폭에서는 「100개」·「5회」 가 값 칸 **안**에 붙는다.
       넓은 폭에서는 `.u` 가 숨고 옆의 `.unit` 열이 그대로다. */
    +`<span class="dw-q"><span class="qty">${modeHtml}<span class="stepper"><button data-dec title="−${st}"${dis}>−</button><span class="vb"><input class="v" type="number" data-dwin min="1" max="${r.k==="gather"?99999:999}" value="${n}" title="직접 입력"${dis}><span class="u">${dwUnit(r.k)}</span></span><button data-inc title="+${st}"${dis}>+</button></span>${maxBtn||`<span class="dwnote">${r.k==="gather"?`예상 ${gpass(n)}회`:""}</span>`}</span>`
    +`<span class="unit">${dwUnit(r.k)}</span>`
    /* 담은 행은 「✓」 외곽선 알약이 되고 **수량은 그대로 남는다** — 다시 담을 수 있다(글자는 ✓ 하나). 즉 이건 「담았다」 표시지 잠금이 아니다. */
    +`<button class="go${r.blocked?"":(DW.dim.has(r.id)?" done":" gold")}" data-add${dis} title="${r.blocked?esc((r.rec&&r.rec.reasonKo)||"지금은 담을 수 없습니다"):(DW.dim.has(r.id)?"이미 담았습니다 — 또 담을 수 있습니다":"담을 곳에 담습니다")}">${DW.dim.has(r.id)?DW_DONE:"담기"}</button></span>`
    +`</div>`;}
function dwBind(body,list){const byId=new Map(list.map((r)=>[r.id,r]));
  body.querySelectorAll(".dw-r").forEach((el)=>{const r=byId.get(el.dataset.id);if(!r)return;
    const go=el.querySelector("[data-add]");
    if(r.k==="collect"){if(go&&!go.disabled)go.onclick=()=>dwAddCollect(r);return;}
    const inp=el.querySelector("[data-dwin]");
    /* **하한이 100 이 아니라 1 이다.** 예전엔 채집을 100 단위로만 담게 막아 뒀는데
       (한 번 부르면 최대 100개를 캐므로), 그러면 1,000개를 담으려고 `+` 를 **열 번**
       눌러야 했다. 막는 대신 **사실대로 적는다**: 아래 「예상 N회」가
       실제로 부를 횟수이고, 그 곱만큼 날개가 든다. */
    const put=(v,fromInput)=>{const hi=r.k==="gather"?99999:999;
      v=Math.max(1,Math.min(hi,Math.round(v)||1));DW.qty.set(r.id,v);
      if(inp&&!fromInput&&Number(inp.value)!==v)inp.value=v;   // 치는 중인 칸은 안 건드린다
      dwRowUpdate(el,r);};
    const set=(d)=>put(dwQty(r)+d*dwStep(r.k));
    const dec=el.querySelector("[data-dec]"),inc=el.querySelector("[data-inc]");
    if(dec&&!dec.disabled)MW.holdBtn(dec,()=>set(-1));
    if(inc&&!inc.disabled)MW.holdBtn(inc,()=>set(1));
    if(inp&&!inp.disabled){inp.oninput=()=>{const v=Number(inp.value);if(inp.value!==""&&isFinite(v))put(v,true);};
      inp.onblur=()=>{inp.value=dwQty(r);};}
    // 가공 방식은 그 행만 고친다 — 표를 다시 그리면 누르고 있던 스테퍼가 사라진다
    // 두 벌(넓은 폭 `.qty` · 좁은 폭 `.l2 .ops`)이 있다 — 어느 쪽을 눌러도 둘 다 맞춘다
    el.querySelectorAll(".dwmode [data-md]").forEach((b)=>{b.onclick=()=>{DW.mode.set(r.id,b.dataset.md);
      el.querySelectorAll(".dwmode [data-md]").forEach((x)=>x.classList.toggle("on",x.dataset.md===b.dataset.md));};});
    el.querySelectorAll("[data-max]").forEach((mx)=>{mx.onclick=()=>dwAdd(r,"max");});
    if(go&&!go.disabled)go.onclick=()=>dwAdd(r,dwQty(r));
    el.addEventListener("pointerenter",()=>{DW.note=dwNoteOf(r,dwQty(r));dwFoot();});});
  const more=$("dwMore");if(more)more.onclick=()=>{DW.limit+=DW_PAGE;dwRenderBody();};}
// 수량만 바뀐 행은 그 행 안의 숫자·재료·안내만 고친다 (표 전체를 다시 그리면 누르고 있던 버튼이 사라진다)
function dwRowUpdate(el,r){const n=dwQty(r);const v=el.querySelector(".v");
  // `.v` 는 이제 <input> 이다 — `textContent` 로는 아무 일도 안 일어난다.
  // **치고 있는 칸은 건드리지 않는다**: 커서가 튀고 「10」을 치다 「1」에서 잘린다.
  if(v&&v.tagName==="INPUT"){if(document.activeElement!==v&&Number(v.value)!==n)v.value=n;}
  else if(v)v.textContent=fmtN(n);
  const m=el.querySelector(".mats");if(m&&r.k!=="gather")m.innerHTML=dwMats(r,n);
  // 예상 회수는 두 자리에 있다 — 넓은 폭의 스테퍼 아래(.dwnote)와 좁은 폭의 둘째 줄(.est)
  if(r.k==="gather"){const t=`예상 ${gpass(n)}회`;
    const note=el.querySelector(".dwnote");if(note)note.textContent=t;
    const est=el.querySelector(".est");if(est)est.textContent=t;}
  DW.note=dwNoteOf(r,n);dwFoot();}
function dwFoot(){const i=$("dwInfo");if(i)i.innerHTML=DW.chain?DW.note:"";
  const w=$("dwWrap");if(w)w.hidden=!(DW.chain&&DW.note);
  const c=$("dwCnt");if(c)c.innerHTML=`이번에 담은 것 <b>${fmtN(DW.added)}</b> · ${esc(DW.addedWhere||dwTargetName())}`;}

/* ── 장바구니 ────────────────────────────────────────────────
   **담기는 바로 담는다.** 이 판은 보여 주고 고치는 창이다 — 확정 단계가 아니다.
   담기는 재화를 쓰지 않아 되돌릴 수 있고, 재화가 나가는 자리(▶ 시작)에는 이미
   확인창이 있다. **되돌릴 수 있으면 바로, 되돌릴 수 없으면 확정.**

   보여 주는 것은 **대기 열 그 자체**다 — 「이번에 담은 것」만 보여 주면 화면을 옮기거나
   새로 고치는 순간 사라져, 정작 「뭘 골랐나」를 못 보게 된다. 이번 줄만 표시한다. */
function dwWaiting(){
  const d=dwBoard();const out=[];
  const walk=(list,where)=>{for(const it of (list||[])){
    if(!it)continue;
    if(it.type==="group"){walk(it.items,it.name||"그룹");continue;}
    if((it.status||"pending")!=="pending")continue;      // 대기 열만 (이미 돈 것은 뺀다)
    out.push({it,where});}};
  walk(d&&d.items,"");
  return out;}

function dwCartToggle(){DW.cart=!DW.cart;dwCartRender();}

/* 담은 **그 순간** 하단 배지에 얹는다.

   왜 따로 필요한가: 배지 숫자는 `dwWaiting()` 이 **큐 스냅샷**에서 세는데, 담기 직후에는
   그 스냅샷이 아직 옛것이다 — `MW.refreshQueue()` 가 뒤에 온다. 그래서 `dwRowDone()` 안에서
   `dwCartRender()` 를 불러도 **담기 전 숫자**가 그려졌고, 나중에 다른 일로 다시 그릴 때에야
   맞았다.

   여기서는 보이는 숫자만 먼저 올린다. 진짜 값은 스냅샷이 온 뒤 `dwCartRender()` 가 다시 센다 —
   그래서 이 셈이 틀려도 한 박자 뒤에 저절로 맞는다. */
function dwCartBump(n){const btn=$("dwCart"),el=$("dwCartN");if(!btn||!el)return;
  const now=(el.hidden?0:parseInt(el.textContent,10)||0)+(n||1);
  el.hidden=false;el.textContent=now>99?"99+":String(now);
  btn.classList.add("has");}

function dwCartRender(){
  const d=dwEl();if(!d.open)return;
  const old=d.querySelector(".dw-cart"),sc=d.querySelector(".dw-scrim");
  if(old)old.remove();
  if(sc)sc.remove();
  const btn=$("dwCart"),n=$("dwCartN");
  const rows=dwWaiting();
  if(btn){btn.classList.toggle("has",rows.length>0);btn.setAttribute("aria-expanded",DW.cart?"true":"false");}
  if(n){n.hidden=!rows.length;n.textContent=rows.length>99?"99+":String(rows.length);}
  if(!DW.cart)return;

  const scrim=document.createElement("div");scrim.className="dw-scrim";
  scrim.onclick=()=>{DW.cart=false;dwCartRender();};
  const box=document.createElement("div");box.className="dw-cart";

  const hd=document.createElement("div");hd.className="dw-cart-hd hd";
  const b=document.createElement("b");b.textContent="담은 작업";
  const cnt=document.createElement("span");cnt.className="n";cnt.textContent=`${fmtN(rows.length)}건`;
  const sp=document.createElement("span");sp.className="grow";sp.style.flex="1";
  const all=document.createElement("button");all.className="xs";all.type="button";all.textContent="전부 빼기";
  all.disabled=!rows.length;
  all.onclick=()=>dwCartClear(rows);
  const x=document.createElement("button");x.className="xs";x.type="button";x.textContent="✕";
  x.title="닫기";x.onclick=()=>{DW.cart=false;dwCartRender();};
  hd.append(b,cnt,sp,all,x);

  const host=document.createElement("div");host.className="rows";
  if(!rows.length){
    const e=document.createElement("div");e.className="empty";
    e.textContent="대기 열이 비어 있습니다 — 아래에서 담아 보세요.";
    host.appendChild(e);
  } else for(const {it,where} of rows) host.appendChild(dwCartRow(it,where));

  const sum=document.createElement("div");sum.className="sum";
  let calls=0,wings=0;
  for(const {it} of rows){const c=dwCalls(it);calls+=c;wings+=dwWingCalls(it);}
  const s1=document.createElement("span");s1.textContent="호출 예상 ";
  const s1b=document.createElement("b");s1b.textContent=`약 ${fmtN(calls)}회`;
  const s2=document.createElement("span");s2.className="gold";
  s2.textContent=`정령의 날개 약 ${fmtN(wings*MW.WINGS_PER_CALL)}`;
  sum.append(s1,s1b,document.createTextNode(" · "),s2);

  box.append(hd,host,sum);
  d.appendChild(scrim);d.appendChild(box);}

/* 호출·날개 수는 **보드와 같은 규칙**을 쓴다 — 여기서 따로 세면 또 어긋난다.
   날개는 `wingCalls` 에서 나온다: 수령은 호출이지만 날개를 안 쓴다. */
function dwCalls(it){const p=it.progress||{};
  if(it.type==="play"||it.type==="notify")return 0;   // 연주·알림 — 호출 없음
  if(it.type==="gather")return p.passesPlanned||gpass(it.target||0);
  if(it.type==="craft"||it.type==="collect")return 1;
  return it.count||1;}
function dwWingCalls(it){if(it.type==="collect")return 0;return dwCalls(it);}

function dwCartRow(it,where){
  const row=document.createElement("div");
  row.className="row"+(DW.fresh.has(it.id)?" new":"");
  const kd=document.createElement("span");
  kd.className="kd "+(it.type||"");
  kd.textContent=DW_KO[it.type]||it.type||"";
  const nm=document.createElement("span");nm.className="nm";
  const b=document.createElement("b");b.textContent=it.type==="notify"?(it.text||it.name||"알림"):(it.name||"");
  const i=document.createElement("i");
  const unit=it.type==="gather"?"개":it.type==="collect"?"건":"회";
  const qty=it.type==="gather"?(it.target||0):(it.count||1);
  // 가공은 **수령까지 하는 항목인지**를 담긴 줄에서도 적는다 — 보드 카드의 꼬리표(board.js collectTag)와 같은 규칙 (collect "none" 만 수령 안 함)
  const ctail=it.type==="alter"?(it.collect==="none"?" · 수령 안 함":" · 완료 후 수령"):"";
  const playN=it.type==="play"&&it.mode==="song";   // 연주 회차 (song) — 「연주 · 이 곡만 · 2회」 (재생목록은 그룹 · 회차는 그룹의 것)
  const free=it.type==="notify"||(it.type==="play"&&!playN);   // 알림·「지금 대기열 이어서」 — 수량·호출이 없다
  i.textContent=(where?`그룹 · ${where} · `:"")+(it.type==="collect"
    ? `${it.facility||""} 완료분 수령`
    : it.type==="play"?`연주 · ${PLAY_MODE_KO_DW[it.mode||""]||""}${playN?` · ${fmtN(qty)}회`:""} · 호출 없음`
    : it.type==="notify"?`알림${it.sound===false?" · 소리 끔":""} · 호출 없음`
    : `${fmtN(qty)}${unit} · 예상 ${fmtN(dwCalls(it))}회${ctail}`);
  nm.append(b,i);
  row.append(kd,nm);

  // 수량 — 수령·알림·「지금 대기열 이어서」는 개념이 없다 (시설 완료분을 한 번에 받는다 / 호출이 없다). 연주 song·list 는 회차 1~20
  if(it.type!=="collect"&&!free){
    const st=document.createElement("span");st.className="stepper";
    const dec=document.createElement("button");dec.type="button";dec.textContent="−";
    const inp=document.createElement("input");inp.className="v";inp.type="number";
    inp.min="1";inp.max=it.type==="gather"?"99999":playN?String(PLAY_COUNT_MAX_DW):"999";inp.value=String(qty);
    const inc=document.createElement("button");inc.type="button";inc.textContent="+";
    const step=it.type==="gather"?100:1;
    const put=(v,fromInput)=>{const hi=it.type==="gather"?99999:playN?PLAY_COUNT_MAX_DW:999;
      v=Math.max(1,Math.min(hi,Math.round(v)||1));
      if(!fromInput)inp.value=String(v);
      dwCartQty(it,v);};
    dec.onclick=()=>put(Number(inp.value)-step);
    inc.onclick=()=>put(Number(inp.value)+step);
    inp.oninput=()=>{const v=Number(inp.value);if(inp.value!==""&&isFinite(v))put(v,true);};
    inp.onblur=()=>{inp.value=String(it.type==="gather"?(it.target||0):(it.count||1));};
    st.append(dec,inp,inc);
    row.appendChild(st);
  } else row.appendChild(document.createElement("span"));

  const x=document.createElement("button");x.className="x";x.type="button";x.textContent="✕";
  x.title="대기 열에서 뺍니다";
  x.onclick=()=>dwCartRemove(it);
  row.appendChild(x);
  return row;}

/* 고치기·빼기는 **기존 큐 op 를 그대로** 쓴다 — 서랍만의 길을 새로 내면 규칙이 갈라진다. */
let dwQT=null;
function dwCartQty(it,v){
  if(it.type==="gather")it.target=v;else it.count=v;
  clearTimeout(dwQT);
  dwQT=setTimeout(async()=>{
    const patch=it.type==="gather"?{target:v}:{count:v};
    const r=await api("/api/queue",{op:"update",id:it.id,...patch});
    if(!r||!r.ok){toast("수량을 바꾸지 못했습니다: "+((r&&(r.message||r.error))||""));}
    if(MW.refreshQueue)await MW.refreshQueue();
    dwCartRender();},350);}

async function dwCartRemove(it){
  const r=await api("/api/queue",{op:"remove",id:it.id});
  if(!r||!r.ok){toast("빼지 못했습니다: "+((r&&(r.message||r.error))||""));return;}
  DW.fresh.delete(it.id);
  if(MW.refreshQueue)await MW.refreshQueue();
  dwCartRender();}

async function dwCartClear(rows){
  for(const {it} of rows){
    const r=await api("/api/queue",{op:"remove",id:it.id});
    if(r&&r.ok)DW.fresh.delete(it.id);}
  if(MW.refreshQueue)await MW.refreshQueue();
  toast("대기 열을 비웠습니다");
  dwCartRender();}

/* 담은 행의 버튼만 그 자리에서 고친다 — 표를 다시 그리면 누르고 있던 스테퍼가 사라진다 */
function dwRowDone(id){
  const el=dwEl().querySelector(`.dw-r[data-id="${CSS.escape(String(id))}"] [data-add]`);
  if(!el)return;
  el.classList.remove("gold");el.classList.add("done");
  el.textContent=DW_DONE;   // 담으면 「✓」 하나 (외곽선 알약)
  el.title="이미 담았습니다 — 또 담을 수 있습니다";
  dwCartRender();}

/* ── 담기 ── */
async function dwAdd(r,count){const grp=DW.target!=="root"?{group:DW.target}:{};
  let res=null,added=1;
  if(r.k==="gather"){res=await api("/api/queue",{op:"add",item:{type:"gather",name:r.name,target:count},...grp});}
  else if(DW.chain&&count!=="max"){   // 「부족 재료도 함께 제안」 — 기존 선행 담기 op (부족분이 없으면 목표 항목만 담긴다)
    // collect 는 요청에 그대로 싣는다 — 서버가 **목표 항목에만** 실어 준다 (선행으로 딸려 가는 가공은 항상 「걸기만」)
    res=await api("/api/queue",{op:"add_chain",id:r.id,name:r.name,kind:r.k,count,mode:"need",...grp,
      ...(r.k==="alter"?{collect:dwMode(r)}:{})});
    added=((res&&(res.added||res.items))||[]).length||1;}
  else{res=await api("/api/queue",{op:"add",item:{type:r.k,name:r.name,recipeId:r.id,count,...(r.k==="alter"?{collect:dwMode(r)}:{})},...grp});}
  if(!res||!res.ok){const e=(res&&res.error)||"";
    if(e==="group_running"){dwMsg("실행 중인 그룹에는 담을 수 없습니다 — 정지 후 또는 그룹 설정에서",true);return;}
    toast("담지 못했습니다: "+((res&&(res.message||res.error))||""));return;}
  DW.added+=added;DW.addedWhere=dwTargetName();DW.dim.add(r.id);
  // 같은 항목에 **합쳐지면** 서버는 `added` 에 개수(숫자)를 준다 (workqueue merged) — 숫자를 for…of 로 돌려 TypeError 가 났고,
  // 그 뒤의 토스트·담음 표시가 통째로 안 나왔다 (PC·폰 모두). 목록일 때만 돈다.
  const got=res&&(Array.isArray(res.added)?res.added:Array.isArray(res.items)?res.items:null);
  for(const it of (got||[(res&&res.item)||null])) if(it&&it.id)DW.fresh.add(it.id);
  dwFoot();dwRowDone(r.id);dwCartBump(added);
  toast(`큐에 담음: ${r.name}${added>1?` 외 ${added-1}항목`:""} · ${dwTargetName()}`);
  if(MW.refreshQueue)await MW.refreshQueue();
  dwRenderTargets();dwCartRender();}
/* 수령 담기 — 기존 add op 를 그대로 쓴다. 수량이 없으므로 count 를 보내지 않는다.
   거절은 토스트가 아니라 「담을 곳」 줄에 그 자리에서 보여 준다 (서랍의 기존 규칙). */
async function dwAddCollect(r){const grp=DW.target!=="root"?{group:DW.target}:{};
  const res=await api("/api/queue",{op:"add",item:{type:"collect",facility:r.facility,name:r.work},...grp});
  if(!res||!res.ok){const e=(res&&res.error)||"";
    if(e==="group_running"){dwMsg("실행 중인 그룹에는 담을 수 없습니다 — 정지 후 또는 그룹 설정에서",true);return;}
    if(e==="duplicate"){dwMsg(`이미 「${dwTargetName()}」 에 있습니다 — ${r.name} 수령`,true);return;}
    if(e==="no_completed_work"||e==="no_completed_work_at_facility"){dwMsg(`완료된 가공이 없습니다 — ${r.name}`,true);return;}
    // not_completed_yet 등 나머지는 서버 안내를 그대로 (완료된 작업 이름을 알려 준다)
    dwMsg("담지 못했습니다: "+((res&&(res.message||res.error))||""),true);return;}
  dwMsg("");
  DW.added+=1;DW.addedWhere=dwTargetName();DW.dim.add(r.id);
  if(res.item&&res.item.id)DW.fresh.add(res.item.id);
  dwFoot();dwRowDone(r.id);dwCartBump(1);
  toast(`큐에 담음: ${r.name} 수령 · ${dwTargetName()}`);
  if(MW.refreshQueue)await MW.refreshQueue();
  dwRenderTargets();dwCartRender();}

/* ── 열기/닫기 ── */
const DW_GRIP_TAP=4;   // 손잡이: 4px 이하 움직임은 누르기(click 이 닫는다) — 그보다 움직이면 끌기
function dwShell(){const d=dwEl();
  /* 좁은 폭에서는 **네 줄**이다: 손잡이 · 머리줄(제목·건수·닫기) ·
     검색 · 분류 칩. 이 넷을 한 줄에 욱여넣었더니 검색창(360px 고정)이 뒤의 것을
     전부 밀어냈고, **닫기 버튼까지 화면 밖으로** 나갔다.
     손잡이는 시트를 「끌어 내릴 수 있는 것」으로 보이게 하는 표시다 (34×4). */
  d.innerHTML=`<div class="dw-grip"><span></span></div>
  <div class="dw-hdr"><b>작업 추가</b><span class="n" id="dwN">0건</span><span class="grow"></span>
    <button class="dw-x" id="dwEsc"><span class="esck">Esc · </span>닫기</button></div>
  <div class="dw-top">
    <span class="dw-search"><span class="mag">⌕</span><input id="dwQ" type="text" placeholder="레시피·채집물 이름으로 찾기" aria-label="찾기"></span>
    <span class="dw-seg" id="dwSeg"><button data-ty="all">전체</button><button data-ty="gather" class="t-gather">채집</button><button data-ty="craft" class="t-craft">제작</button><button data-ty="alter" class="t-alter">가공</button><button data-ty="collect" class="t-collect" title="지금 완료분이 있는 시설만 — 시설당 1개">수령</button><button data-ty="play" class="t-play" title="차례가 오면 폴리오에 연주를 부탁 — 호출 0 · 날개 0">연주</button><button data-ty="notify" class="t-notify" title="차례가 오면 알림 — PC 밴드·앱 창·모바일">알림</button></span>
    <span class="dw-skills" id="dwSkills"></span></div>
  <div class="dw-tgt">
    <span class="lb">담을 곳</span><span class="dw-tgtwrap"><span class="dw-tgts" id="dwTgts"></span></span>
    <span class="dw-newg" id="dwNew" hidden><input type="text" maxlength="24" placeholder="그룹 이름" aria-label="새 그룹 이름"><button class="xs" id="dwNewGo">만들기</button><button class="xs" id="dwNewX">취소</button></span>
    <span class="dw-tgtmsg" id="dwTgtMsg"></span><span class="grow"></span>
    <label class="dw-chain"><span class="long">담을 때 부족 재료도 <em>함께 제안</em></span><span class="short">부족 재료 제안</span><input type="checkbox" id="dwChain" checked></label></div>
  <div class="dw-hd" id="dwHd"><span>종류</span><span>이름</span><span class="per">×산출</span><span>상태</span><span>재료 (가방+창고 / 필요)</span><span class="qh"><span>수량</span><span>단위</span><span>담기</span></span></div>
  <div class="dw-body" id="dwBody"></div>
  <div class="dw-foot"><button class="dw-cartbtn" id="dwCart" type="button" title="담은 작업 보기"><svg class="ci" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4h2l2.2 10.2a1.5 1.5 0 001.5 1.2h8.6a1.5 1.5 0 001.5-1.1L21 8H6.2"/><circle cx="9.5" cy="19.5" r="1.3"/><circle cx="17" cy="19.5" r="1.3"/></svg><span class="lb"><b>담은 작업</b><i>대기 열</i></span><span class="n" id="dwCartN" hidden>0</span></button><span class="i" id="dwWrap" hidden>ⓘ</span><span class="info" id="dwInfo"></span><span class="grow"></span><span class="cnt" id="dwCnt"></span><button class="pri" id="dwGo">닫고 보드로</button></div>`;
  let t=null;$("dwQ").oninput=()=>{clearTimeout(t);t=setTimeout(()=>{DW.q=$("dwQ").value.trim();DW.limit=DW_PAGE;dwRenderBody();},120);};   // IME 조합 보호 — 검색창은 다시 그리지 않는다
  $("dwSeg").querySelectorAll("[data-ty]").forEach((b)=>{b.onclick=()=>{DW.type=b.dataset.ty;if(DW.type!=="gather")DW.skill=null;DW.limit=DW_PAGE;dwRenderBody();};});
  $("dwEsc").onclick=()=>d.close();
  /* 손잡이 — **눌러도 닫히고 끌어 내려도 닫힌다**.
     손잡이처럼 생긴 것이 아무 일도 안 하면
     그건 장식이 아니라 **고장난 것**으로 보인다. 폰에서 시트를 닫는 가장 익숙한 손짓이
     아래로 끌기라, 누르기와 끌기를 둘 다 받는다. */
  /* 끌기가 끝난 뒤의 click 은 **삼킨다**. 마우스로 30px 끌고 놓으면 pointerup 뒤에
     브라우저가 click 을 한 번 더 보내고, 그것이 「누르면 닫힘」으로 가서 판정(60px)과 상관없이 닫혔다.
     움직임이 DW_GRIP_TAP 이하면 누르기(click 이 닫는다), 넘으면 끌기(pointerup 이 60px 로 판정하고 click 은 버린다). */
  const grip=d.querySelector(".dw-grip");
  if(grip){grip.onclick=()=>{if(grip.dataset.dragged){delete grip.dataset.dragged;return;}d.close();};
    grip.onpointerdown=(e)=>{const y0=e.clientY;let y=y0,moved=false;delete grip.dataset.dragged;
      try{grip.setPointerCapture(e.pointerId);}catch{}
      grip.onpointermove=(ev)=>{y=ev.clientY;const dy=Math.max(0,y-y0);
        if(Math.abs(y-y0)>DW_GRIP_TAP)moved=true;
        d.style.transform=dy?`translateY(${dy}px)`:"";};
      const up=()=>{grip.onpointermove=null;grip.onpointerup=null;grip.onpointercancel=null;
        d.style.transform="";
        if(moved)grip.dataset.dragged="1";          // 뒤따르는 click 이 닫지 못하게
        if(y-y0>60)d.close();};     // 60px 넘게 내리면 닫는다 — 그 아래는 손이 떨린 것으로 본다
      grip.onpointerup=up;grip.onpointercancel=up;};}
  $("dwCart").onclick=dwCartToggle;
  $("dwGo").onclick=()=>{d.close();if(MW.switchTab)MW.switchTab("queue");};
  $("dwChain").onchange=()=>{DW.chain=$("dwChain").checked;dwFoot();};
  $("dwNewGo").onclick=dwNewGroupGo;
  $("dwNewX").onclick=()=>{$("dwNew").hidden=true;};
  $("dwNew").querySelector("input").onkeydown=(e)=>{if(e.key==="Enter"){e.preventDefault();dwNewGroupGo();}
    if(e.key==="Escape"){e.stopPropagation();$("dwNew").hidden=true;}};
  d.addEventListener("close",()=>{DW.pending=false;});}
function openDrawer(opts){opts=opts||{};const d=dwEl();
  DW.cart=false;DW.fresh.clear();DW.dim.clear();   // 「이번에」는 이번 열림까지다
  if(!d.dataset.built){dwShell();d.dataset.built="1";}
  DW.target=opts.target||"root";DW.type=opts.type||"all";DW.q=opts.query||"";DW.skill=null;
  DW.added=0;DW.addedWhere="";DW.limit=DW_PAGE;DW.cache=new Map();DW.qty=new Map();DW.mode=new Map();DW.note="";
  DW.rows=dwAll();dwSnapAt=S.fetchedAt;dwColKey=dwColKeyOf();
  $("dwQ").value=DW.q;$("dwChain").checked=DW.chain;$("dwNew").hidden=true;dwMsg("");
  if(!d.open)d.showModal();
  dwRenderTargets();dwRenderBody();
  setTimeout(()=>{const q=$("dwQ");if(q)q.focus();},0);}
// 누르고 있던 스테퍼가 끝나면 미뤄 둔 재렌더를 한다
window.addEventListener("mw:holdend",()=>{if(DW.pending&&dwEl().open){DW.pending=false;dwRenderBody();}});
// 스냅숏이 갱신되면(갱신 버튼·폴링) 열려 있는 서랍의 보유·부족분도 따라간다. 수량(DW.qty)·가공 방식(DW.mode)은 그대로 둔다
// 수령 목록은 works.js 가 1초마다 남은 시간을 깎으며 다시 세므로, 스냅숏이 그대로여도 완료 건수가 바뀔 수 있다 — 그때도 따라간다
let dwSnapAt=0,dwColKey="";
setInterval(()=>{const d=$("drawer");if(!d||!d.open)return;
  const ck=dwColKeyOf();if(S.fetchedAt===dwSnapAt&&ck===dwColKey)return;
  dwSnapAt=S.fetchedAt;dwColKey=ck;
  DW.rows=dwAll();DW.cache=new Map();dwRenderTargets();dwRenderBody();},1500);


/* ── 「연주」·「알림」 갈래 ──────────────────────────────
   행 표가 아니라 **판 하나**다. 연주: 폴리오의 곡·재생목록을 `api()` 로 받는다 (폰도 같은 길 — `/api/folio/scores`·`/api/folio/playlists`
   는 FOLIO_READ_OK). 알림: 글(80자)·소리. 담기는 기존 `op:"add"` 다 — 서버가 모양을 검사한다 (workqueue._add_free). */
Object.assign(DW,{play:{q:"",song:null,title:"",list:"",mode:"resume",count:1},notify:{text:"",sound:true},
  folio:{items:[],lists:[],at:0,busy:false,err:""}});
const dwFree=()=>DW.type==="play"||DW.type==="notify";
function dwSongMatch(x,tokens){const hay=[x.title,x.song,x.cleaned,x.artist].filter(Boolean).map((v)=>String(v).toLowerCase());
  return tokens.every((t)=>hay.some((h)=>h.includes(t)));}
function dwSongRowsHTML(){const P=DW.play,F=DW.folio;const tokens=P.q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits=(F.items||[]).filter((x)=>dwSongMatch(x,tokens)).slice(0,40);
  if(!hits.length)return `<div class="dw-empty">${F.busy?"불러오는 중…":F.err?esc(`폴리오 목록을 받지 못했습니다: ${F.err}`):(F.items||[]).length?"해당하는 곡이 없습니다":"곡이 없습니다 — 폴리오에서 갱신하세요"}</div>`;
  return hits.map((x)=>`<button type="button" class="dw-song${P.song===x.key?" on":""}" data-song="${esc(x.key)}" data-title="${esc(x.title)}"><b>${esc(x.song||x.cleaned||x.title)}</b>${x.artist?`<i>${esc(x.artist)}</i>`:""}</button>`).join("");}
function dwPlayHTML(){const P=DW.play,F=DW.folio;const lists=F.lists||[];
  const status=F.busy?"불러오는 중…":F.err?`폴리오 목록을 받지 못했습니다: ${F.err}`:(F.items||[]).length?`${fmtN(F.items.length)}곡 · 재생목록 ${fmtN(lists.length)}개`:"";
  const PM_TITLE={song:"이 곡만 · 회차만큼 치고 멈춤",list:"재생목록을 그룹으로 담기 — 곡마다 연주 카드",resume:"폴리오의 지금 대기열을 이어서"};
  const mode=(k)=>`<button type="button" data-pm="${k}" class="${P.mode===k?"on":""}" title="${PM_TITLE[k]}">${PLAY_MODE_KO_DW[k]}</button>`;
  return `<div class="dw-free dw-play">
    <div class="dw-free-h"><span class="kd play">연주</span><span class="tx"><b>차례가 오면 폴리오에 연주를 부탁하고, 끝날 때까지 기다립니다</b><i>호출 0 · 날개 0 · 곡×회차가 다 울린 뒤에 다음 카드로 — 재생목록은 곡마다 카드 하나인 그룹으로 담깁니다</i></span></div>
    <div class="dw-modes" role="group" aria-label="연주 방식">${mode("song")}${mode("list")}${mode("resume")}</div>
    <div class="dw-pick" data-pick="song"${P.mode==="song"?"":" hidden"}>
      <span class="dw-search dw-wide"><span class="mag">⌕</span><input id="dwPq" type="text" placeholder="곡 제목·가수로 찾기" value="${esc(P.q)}" aria-label="곡 찾기"></span>
      <div class="dw-songs" id="dwSongs">${dwSongRowsHTML()}</div>
      <div class="dw-sel" id="dwPsel">${P.song?`고른 곡: <b>${esc(P.title||P.song)}</b>`:"곡을 고르세요"}</div></div>
    <div class="dw-pick" data-pick="list"${P.mode==="list"?"":" hidden"}>
      <select id="dwPl" aria-label="재생목록"><option value="">재생목록 고르기</option>${lists.map((l)=>`<option value="${esc(l.id)}"${P.list===l.id?" selected":""}>${esc(l.name||l.id)} (${fmtN((l.items||[]).length)}곡)</option>`).join("")}</select><span class="dw-note">그룹 「재생목록 이름」 안에 곡마다 연주 카드 하나 — 순서 바꾸기·빼기는 보드의 그룹에서</span></div>
    <div class="dw-pick" data-pick="resume"${P.mode==="resume"?"":" hidden"}><span class="dw-note">폴리오가 들고 있는 대기열의 지금 자리부터 이어서 틉니다 — 비어 있으면 그 카드는 실패로 남고 큐는 계속 갑니다 (회차 없음 · 폴리오 반복 설정대로)</span></div>
    <div class="dw-count" id="dwPcount"${P.mode==="resume"?" hidden":""}><span class="lbl">회차</span><span class="stepper"><button type="button" data-pc="-1" title="−1">−</button><input class="v" id="dwPn" type="number" min="1" max="${PLAY_COUNT_MAX_DW}" value="${P.count||1}" aria-label="연주 회차"><button type="button" data-pc="1" title="+1">+</button></span><span class="dw-note">${P.mode==="list"?"회 돌고 멈춥니다 — 그룹의 회차":"회 치고 멈춥니다"} (1~${PLAY_COUNT_MAX_DW} · 그 요청은 폴리오의 반복·셔플 설정을 안 봅니다)</span></div>
    <div class="dw-free-f"><span class="dw-note">${esc(status)}</span><span class="grow"></span><button type="button" class="xs" id="dwPlayReload" title="폴리오 목록 다시 받기">↻</button><button type="button" class="go gold" id="dwPlayAdd">담기</button></div></div>`;}
const PLAY_COUNT_MAX_DW=20;   // workqueue.PLAY_COUNT_MAX — 연주 회차 상한 (서버도 자른다)
const PLAY_MODE_KO_DW={song:"이 곡만",list:"재생목록",resume:"대기열 이어서"};   // 단추 글자 — 짧게 (긴 설명은 판의 안내 줄에)   // 판의 방식 셋 — list 는 카드가 아니라 그룹으로 펼쳐진다
function dwNotifyHTML(){const N=DW.notify;
  return `<div class="dw-free dw-notify">
    <div class="dw-free-h"><span class="kd notify">알림</span><span class="tx"><b>차례가 오면 알립니다</b><i>PC 게임 위 밴드 토스트 · 앱 창 토스트 · 모바일(이 화면이 열려 있을 때) 토스트·진동 · 호출 0 · 날개 0</i></span></div>
    <label class="dw-field"><span>알림 글 (80자까지)</span><input id="dwNtext" type="text" maxlength="80" placeholder="큐가 여기까지 왔습니다" value="${esc(N.text)}"></label>
    <label class="dw-chain dw-sw"><span>소리</span><input type="checkbox" id="dwNsound"${N.sound?" checked":""}></label>
    <div class="dw-free-f"><span class="dw-note">닫힌 모바일 브라우저에는 닿지 않습니다 — 알림 서버가 없습니다. 화면을 열어 두세요 (iPhone 은 홈 화면에 추가한 앱에서만 시스템 알림)</span><span class="grow"></span>
      <button type="button" class="xs" id="dwNperm" title="시스템 알림 허용 + 소리 깨우기 (지원하지 않는 브라우저면 그렇다고 알려 줍니다)">모바일 알림 허용</button><button type="button" class="go gold" id="dwNotifyAdd">담기</button></div></div>`;}
function dwBindFree(body){const P=DW.play,N=DW.notify;
  body.querySelectorAll("[data-pm]").forEach((b)=>{b.onclick=()=>{P.mode=b.dataset.pm;
    body.querySelectorAll("[data-pm]").forEach((x)=>x.classList.toggle("on",x.dataset.pm===P.mode));
    body.querySelectorAll("[data-pick]").forEach((x)=>{x.hidden=x.dataset.pick!==P.mode;});
    const pc=$("dwPcount");if(pc)pc.hidden=P.mode==="resume";};});   // 회차는 song·list 에만
  // 회차 스테퍼 (1~20) — 서랍 안에서만 값이 바뀌고, 담을 때 count 로 나간다
  const pn=$("dwPn");const putCount=(v)=>{v=Math.max(1,Math.min(PLAY_COUNT_MAX_DW,Math.round(Number(v))||1));P.count=v;if(pn&&Number(pn.value)!==v)pn.value=String(v);};
  body.querySelectorAll("[data-pc]").forEach((b)=>{b.onclick=()=>putCount((P.count||1)+Number(b.dataset.pc));});
  if(pn){pn.oninput=()=>{if(pn.value!==""&&isFinite(Number(pn.value)))putCount(pn.value);};pn.onblur=()=>{pn.value=String(P.count||1);};}
  const bindSongs=()=>{body.querySelectorAll("[data-song]").forEach((b)=>{b.onclick=()=>{P.song=b.dataset.song;P.title=b.dataset.title||"";
    body.querySelectorAll("[data-song]").forEach((x)=>x.classList.toggle("on",x.dataset.song===P.song));
    const s=$("dwPsel");if(s)s.innerHTML=`고른 곡: <b>${esc(P.title||P.song)}</b>`;};});};
  bindSongs();
  const q=$("dwPq");if(q){let t=null;q.oninput=()=>{clearTimeout(t);t=setTimeout(()=>{P.q=q.value.trim();const box=$("dwSongs");if(box){box.innerHTML=dwSongRowsHTML();bindSongs();}},120);};}
  const pl=$("dwPl");if(pl)pl.onchange=()=>{P.list=pl.value;};
  const rl=$("dwPlayReload");if(rl)rl.onclick=()=>dwFolioLoad(true);
  const pa=$("dwPlayAdd");if(pa)pa.onclick=dwAddPlay;
  const nt=$("dwNtext");if(nt)nt.oninput=()=>{N.text=nt.value;};
  const ns=$("dwNsound");if(ns)ns.onchange=()=>{N.sound=!!ns.checked;};
  const np=$("dwNperm");if(np)np.onclick=()=>{if(MW.noticePermission)MW.noticePermission();};
  const na=$("dwNotifyAdd");if(na)na.onclick=dwAddNotify;}
async function dwFolioLoad(force){const F=DW.folio;if(F.busy)return;if(!force&&F.at&&Date.now()-F.at<60000)return;
  F.busy=true;F.err="";
  try{const [sc,pl]=await Promise.all([api("/api/folio/scores"),api("/api/folio/playlists")]);
    if(sc&&Array.isArray(sc.items))F.items=sc.items.filter((x)=>x&&x.key).map((x)=>({key:x.key,title:x.title||x.key,song:x.song||"",cleaned:x.cleaned||"",artist:x.artist||""}));
    else F.err=(sc&&(sc.message||sc.error))||"scores";
    if(pl&&Array.isArray(pl.playlists))F.lists=pl.playlists.filter((l)=>l&&l.id).map((l)=>({id:l.id,name:l.name||"",items:l.items||[]}));
    F.at=Date.now();}
  catch(e){F.err=String((e&&e.message)||e);}
  F.busy=false;
  if(dwEl().open&&DW.type==="play"){const box=$("dwSongs");if(box){box.innerHTML=dwSongRowsHTML();}dwRenderBody();}}
async function dwAddFree(item,label){const grp=DW.target!=="root"?{group:DW.target}:{};
  const res=await api("/api/queue",{op:"add",item,...grp});
  if(!res||!res.ok){const e=(res&&res.error)||"";
    if(e==="group_running"){dwMsg("실행 중인 그룹에는 담을 수 없습니다 — 정지 후 또는 그룹 설정에서",true);return null;}
    dwMsg("담지 못했습니다: "+((res&&(res.message||res.error))||""),true);return null;}
  dwMsg("");DW.added+=1;DW.addedWhere=dwTargetName();
  if(res.item&&res.item.id)DW.fresh.add(res.item.id);
  dwFoot();dwCartBump(1);
  toast(`큐에 담음: ${label} · ${dwTargetName()}`);
  if(MW.refreshQueue)await MW.refreshQueue();
  dwRenderTargets();dwCartRender();return res;}
function dwAddPlay(){const P=DW.play;
  if(P.mode==="song"&&!P.song){dwMsg("곡을 먼저 고르세요",true);return;}
  if(P.mode==="list"&&!P.list){dwMsg("재생목록을 먼저 고르세요",true);return;}
  const l=(DW.folio.lists||[]).find((x)=>x.id===P.list);
  const count=P.mode==="resume"?1:Math.max(1,Math.min(PLAY_COUNT_MAX_DW,Math.round(Number(P.count))||1));
  if(P.mode==="list"&&DW.target!=="root"){dwMsg("재생목록은 그룹으로 담깁니다 — 담을 곳을 「대기 열」로 하세요 (그룹 안에 그룹은 못 넣습니다)",true);return;}
  const item={type:"play",mode:P.mode,song:P.mode==="song"?P.song:null,list:P.mode==="list"?P.list:null,
    title:P.mode==="song"?P.title:"",listName:P.mode==="list"&&l?l.name:"",count};
  // 재생목록 → 서버가 **그룹**(곡마다 「이 곡만」 카드)으로 펼친다 (workqueue._add_playlist_group) — 폰도 같은 길
  const nsong=P.mode==="list"&&l?(l.items||[]).length:0;
  return dwAddFree(item,P.mode==="list"?`재생목록 「${(l&&l.name)||P.list}」 → 그룹 (${fmtN(nsong)}곡 · ${count}회차)`
    :`연주 · ${P.mode==="song"?(P.title||P.song):PLAY_MODE_KO_DW.resume}${P.mode==="resume"?"":` · ${count}회`}`);}
function dwAddNotify(){const N=DW.notify;const text=N.text.trim().slice(0,80);
  return dwAddFree({type:"notify",text,sound:!!N.sound},`알림 · ${text||"큐가 여기까지 왔습니다"}`);}
Object.assign(window.MW,{DW,openDrawer,dwAll,dwFilter,dwMats,dwNoteOf,dwGroups,dwRenderBody,dwRenderTargets,dwAdd,dwAddCollect,dwCollectRows,
  dwPlayHTML,dwNotifyHTML,dwSongRowsHTML,dwFolioLoad,dwAddPlay,dwAddNotify,dwFree});   // 연주·알림 갈래
