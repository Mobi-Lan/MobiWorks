// tab-stock.js — 재고 탭
/* 위에서 아래로:
     #stkSum[ 가방 무게 한 줄 → 막대 → 경고 한 줄 → 재화 3칸 + 「+N ▾」 → (펼치면) 나머지 재화 3열 ]
     → #filterRow(검색 — index.html 공용 줄, 오른쪽 끝에 결과 수 #stkN)
     → #stkFilt[ 보기 4분할 전폭 → 위치 3분할 + 정렬 2분할 한 줄 ]
     → #tabBody[ 행 ]
   #stkSum·#stkFilt·#stkN 은 이 파일이 #tabCard 에 **한 번** 끼우는 노드다 (tab-dict.js 가 #dictHead 를 끼우는 방식과 같다).
   renderTab 이 #tabBody 의 innerHTML 만 갈아 끼우므로 살아남고, 사전 탭에서는 tab-stock.css 가 숨긴다.

   행 = 1줄: 이름 · 종류 칩 (· 부족 −n) ······ 합계(굵게, 우측) / 2줄: 가방 · 창고 │ 쓰이는 곳 ····· 담기.
   마크업은 **한 벌**이다: 좁은 폭(≤600)이 2줄 행이고, 넓은 폭은 CSS 가 같은 조각을
   이름|가방|창고|합계|쓰이는 곳|담기 여섯 열로 편다 (grid-template-areas · tab-stock.css).

   보유 판정은 가방+창고 합산이다(게임이 창고 재료를 원격으로 쓴다). 「부족」은 **큐 기준** — 보드에 담긴
   항목이 소모할 양보다 합산 보유가 모자란 재료만이다.

   **모르는 값은 지어내지 않으므로 만들지 않는 것**: 정렬의 「무게」 칸 — get_items 는 아이템별 무게를 주지 않는다.
   「가방으로」 액션도 만들지 않는다 — CLI 에 창고→가방 이송 명령이 없다 (docs/CLI.md §5).

   새 조회 API 는 만들지 않는다. 쓰는 것은 /api/work 스냅숏(S.snap) · /api/recipes 관찰 DB(S.dict) ·
   보드 상태(Q.data·Q.prev) 뿐이고, 담기는 기존 경로(queueAdd → /api/queue op:add)를 쓴다. */

/* ── 게임 원문 등급 태그 ──
   실데이터 892종 중 106종 이름에 「인챈트 스크롤: 반달곰(<color=#FFC448>★8</color>)」 같은 표식이 섞여 온다
   (색 #FFC448·#FFFFFF, ★6·8·9·10). **이름 앞의 배지**로 뽑는다 — 태그를 떼고, 태그만 감싸던 빈 괄호까지
   지워 이름이 「반달곰()」으로 남지 않게 한다. 검색·정렬은 태그를 벗긴 평문으로 한다. */
const CTAG=/<color=(#[0-9A-Fa-f]{3,8})>([\s\S]*?)<\/color>/g;
const plainName=(n)=>String(n==null?"":n).replace(CTAG,"$2");
// 이름에서 등급 표식을 떼어 낸다 → {tags:[{color,text}], name:"표식과 빈 괄호를 뺀 이름"}
function starSplit(n){const s=String(n==null?"":n);const tags=[];CTAG.lastIndex=0;
  const body=s.replace(CTAG,(m,c,t)=>{tags.push({c,t});return "";});
  return {tags,name:body.replace(/[(（]\s*[)）]/g,"").replace(/\s{2,}/g," ").trim()};}
// 태그를 그 색 그대로 칠한 배지로 (색은 게임 원문 값이라 토큰이 아니다 — --gc 로 넘긴다)
const starHtml=(tags)=>tags.map((x)=>`<b class="stg" style="--gc:${x.c}" title="게임 원문 등급 표식">${esc(x.t)}</b>`).join("");
// 옛 이름 (다른 파일이 부를 수 있다): 태그 자리를 그대로 두고 색만 입힌 이름
function nameHtml(n){const s=String(n==null?"":n);let out="",last=0,m;CTAG.lastIndex=0;
  while((m=CTAG.exec(s))){out+=esc(s.slice(last,m.index));out+=`<b class="stg" style="--gc:${m[1]}" title="게임 원문 등급 표식">${esc(m[2])}</b>`;last=m.index+m[0].length;}
  return out+esc(s.slice(last));}

/* ── 상태 (새로고침이면 초기화 — 핀만 저장) ── */
const SK={view:"all",loc:"all",sort:"name",curOpen:false,sortBefore:null};   // sortBefore: 「부족」 보기에 들어가기 전 정렬 (나올 때 되돌린다)
const PIN_KEY="mw.currencyPins";const PIN_DEF=["골드","정령의 날개","데카"];   // 기본: 골드 · 날개 · 데카 3칸
function pinList(){try{const v=JSON.parse(lsGet(PIN_KEY)||"null");return Array.isArray(v)?v.filter((x)=>typeof x==="string"):PIN_DEF.slice();}catch{return PIN_DEF.slice();}}
function pinToggle(name){const a=pinList();const i=a.indexOf(name);if(i<0)a.push(name);else a.splice(i,1);lsSet(PIN_KEY,JSON.stringify(a));toast(i<0?`재화 고정: ${name}`:`고정 해제: ${name}`);renderTab();}

/* ── 큐가 쓰는 재료 ──
   보드의 항목이 실제로 소모할 재료를 이름별로 합친다. 그룹은 남은 회차만큼 곱한다(repeat 중 loop 회는 이미 돌았다).
   채집 항목은 재료를 쓰지 않으므로 세지 않는다. */
function queueItems(){const out=[];const st=(typeof Q!=="undefined"&&Q.data)||null;
  for(const it of (st&&st.items)||[]){if(!it)continue;
    if(it.type==="group"){const rounds=Math.max(1,(Number(it.repeat)||1)-(Number(it.loop)||0));
      for(const c of it.items||[])if(c)out.push({it:c,mul:rounds});}
    else out.push({it,mul:1});}
  return out;}
// 큐에 담긴 항목이 쓰는 재료 이름 (채집은 그 자체) — 「큐에 쓰임」 보기용
function queueNames(){const set=new Set();const R=(S.dict&&S.dict.recipes)||{};
  for(const {it} of queueItems()){if(it.type==="gather"&&it.name)set.add(it.name);
    const r=it.recipeId&&R[it.recipeId];if(r&&r.ingredients)for(const k in r.ingredients)set.add(k);}
  return set;}
// 이름 → 큐가 소모할 총량 (「부족」 = 이 값 − 합산 보유)
function queueNeed(){const need=new Map();const R=(S.dict&&S.dict.recipes)||{};
  for(const {it,mul} of queueItems()){const r=it.recipeId&&R[it.recipeId];if(!r||!r.ingredients)continue;
    const c=Math.max(1,Number(it.count)||1)*mul;
    for(const k in r.ingredients)need.set(k,(need.get(k)||0)+(Number(r.ingredients[k])||0)*c);}
  return need;}
// 큐에 들어 있는 레시피 키 (「쓰이는 곳」에서 그 레시피를 강조한다)
function queueRecipeKeys(){const set=new Set();for(const {it} of queueItems())if(it.recipeId)set.add(it.recipeId);return set;}

/* ── 「쓰이는 곳」 — 그 아이템을 재료로 쓰는 레시피 (「가죽+ ×3 · 가죽 갑옷 신발 ×2」 한 줄, 넘치면 말줄임) ──
   관찰 DB 의 역방향 색인(ingredients[name].usedBy / .need)을 그대로 쓴다. 문구는 「<레시피> ×<1회 사용량>」.
   큐 때문에 모자라면 그 조각에 「· 부족 N」을 붙이고 강조하며, 잘려 나가지 않게 앞으로 보낸다. */
const UCH_MAX=8;
function usesText(name,short,qkeys){const I=(S.dict&&S.dict.ingredients)||{};const R=(S.dict&&S.dict.recipes)||{};
  const i=I[name];if(!i)return "";
  const keys=i.usedBy||[];if(!keys.length)return "";const need=i.need||{};
  const rows=keys.map((k)=>({k,hot:qkeys.has(k)&&short>0}));
  rows.sort((a,b)=>(b.hot?1:0)-(a.hot?1:0));
  return rows.slice(0,UCH_MAX).map(({k,hot})=>{
    const r=R[k];const nm=plainName((r&&r.name)||k.split(":").slice(1).join(":").replace(/#\d+$/,""));
    const txt=`${esc(nm)}${need[k]?` ×${fmtN(need[k])}`:""}${hot?` · 부족 ${fmtN(short)}`:""}`;
    return `<span class="stk-uch${hot?" hot":""}" title="${esc(nm)}${need[k]?` · 1회에 ${fmtN(need[k])}개`:""}${hot?` · 큐 기준 ${fmtN(short)}개 부족`:""}">${txt}</span>`;}).join('<span class="stk-dot"> · </span>');}

/* ── 「담기」 — 그 아이템을 **구하는 방법**에 따라 가공/채집/제작 담기 (골드 채움 알약) ──
   서버가 /api/recipes 에 실어 주는 sources(= recipedb.source_index)를 쓴다.
   그 값이 없으면(옛 백엔드) 관찰 DB 의 재료 항목으로 대신한다 — 규칙은 같다(채집 우선 → 산출 레시피).
   구하는 방법을 모르면 지어내지 않고 「—」 비활성. */
const SRC_KO={gather:"채집 담기",craft:"제작 담기",alter:"가공 담기"};
function srcOf(name){const src=(S.dict&&S.dict.sources)||null;
  const v=src?src[name]:null;
  if(Array.isArray(v)&&v[0])return {kind:String(v[0]),id:v[1]?String(v[1]):""};
  const e=((S.dict&&S.dict.ingredients)||{})[name];
  if(e&&e.gatherable)return {kind:"gather",id:""};
  const made=(e&&e.madeBy)||[];
  if(made.length){const k=String(made[0]);const kd=k.split(":")[0];if(kd==="craft"||kd==="alter")return {kind:kd,id:k};}
  return null;}
function addBtn(name,short){const s=srcOf(name);
  if(!s||!SRC_KO[s.kind])return '<button class="stk-add none" disabled title="이 아이템을 얻는 방법을 아직 관찰하지 못했습니다">—</button>';
  if(s.kind==="gather"){const t=short>0?short:100;   // 채집은 회당 최대 100개 — 부족분이 있으면 그만큼, 없으면 한 회차
    return `<button class="stk-add go" data-sa="gather" data-n="${esc(name)}" data-t="${t}" title="${fmtN(t)}개 채집을 대기 열에 담습니다 (예상 ${gpass(t)}회)">${SRC_KO.gather}</button>`;}
  const c=runsFor(s.id,short);
  return `<button class="stk-add go" data-sa="${s.kind}" data-n="${esc(name)}" data-id="${esc(s.id)}" data-c="${c}" title="${short>0?`부족 ${fmtN(short)}개를 채우도록 `:""}${fmtN(c)}회를 대기 열에 담습니다">${SRC_KO[s.kind]}</button>`;}
/* 제작·가공 담기 횟수 — 채집이 부족분만큼 담는 것과 같은 규칙. 부족 n 이면 ⌈n / 1회 산출⌉ 회, 부족이 없으면 1회.
   예전에는 늘 1회라 「고급 무명실 −7」 행의 가공 담기가 2개만 만들고 끝났다. 1회 산출을 모르면 1개로 본다.
   상한 999 는 list-core 스테퍼와 같은 값 — 시설별 상한은 서버·러너가 나눠 처리한다 */
function runsFor(id,short){if(!(short>0))return 1;const r=id&&((S.dict&&S.dict.recipes)||{})[id];const per=Math.max(1,Number(r&&r.per)||1);
  return Math.max(1,Math.min(999,Math.ceil(short/per)));}
// 담기는 기존 경로만 쓴다 (board.js 의 queueAdd → /api/queue op:add). 실행 명령은 부르지 않는다
function stockAdd(b){const k=b.dataset.sa,n=b.dataset.n;
  if(typeof queueAdd!=="function"){toast("큐 기능 준비 중입니다");return;}
  if(k==="gather")queueAdd({type:"gather",name:n,target:Math.max(1,Number(b.dataset.t)||100)});
  else queueAdd({type:k,name:n,recipeId:b.dataset.id||undefined,count:Math.max(1,Number(b.dataset.c)||1),...(k==="alter"?{collect:"later"}:{})});}

// 세그먼트 (높이 24 · radius 7 · 10px · 활성 골드 채움). 라벨은 코드 안 고정 글자값뿐이다
const SEG=(k,opts,cur,cls)=>`<span class="stk-seg${cls?" "+cls:""}">${opts.map(([v,l,dis])=>`<button data-sk="${k}" data-v="${v}" class="${cur===v?"on":""}"${dis?" disabled":""}>${esc(l)}</button>`).join("")}</span>`;

/* ── 상단 요약: 무게 한 줄 + 막대 + 경고 한 줄 · 재화 골드/날개/데카 3칸 + 「+N ▾」 → 펼치면 나머지 3열 ── */
function sumHtml(s){const inv=s.inventory||{};const cur=(s.currencies||[]).filter((c)=>c&&c.name);
  /* 가방 무게: get_inventory 의 CurrentInventoryWeight / MaxInventoryWeight.
     여유 표식은 설정 queue_weight_margin 지점 — 그 안으로 들어오면 채집이 overweight_soon 으로 멈춘다.
     최대 무게를 모르면 이 블록을 통째로 뺀다 (0 으로 채우지 않는다). */
  const margin=Math.max(0,Number(S.settings.queue_weight_margin)||30);
  const max=Number(inv.max)||0,now=Number(inv.cur)||0;
  const pct=max?Math.round(now/max*100):0;const free=max?Math.max(0,max-now):0;const near=max?free<=margin:false;
  const wgt=max
    ?`<div class="stk-wgt"><div class="stk-w1"><span class="lb">가방 무게</span><b class="mono">${fmtN(now)}</b>`
      +`<span class="mx mono">/ ${fmtN(max)}</span><span class="grow"></span>`
      +`<span class="stk-wb ${near?"bad":(pct>=80?"warn":"line")}">${pct}% · 여유 ${fmtN(free)}</span></div>`
      +`<div class="stk-bar${near?" near":""}"><i style="width:${Math.min(100,Math.max(0,pct))}%"></i>`
      +`<u style="left:${Math.max(0,Math.min(100,(max-margin)/max*100))}%" title="설정 여유 ${fmtN(margin)} 선"></u></div>`
      +`<div class="stk-w3">여유 <b>${fmtN(margin)}</b> 아래면 채집이 <em>무게 초과</em>로 멈춤</div></div>`
    :'<div class="stk-wgt"></div>';

  /* 재화: 고정한 것(기본 골드·날개·데카)만 칸으로, 나머지는 「+N ▾」 안에 3열로. 우클릭 = 고정/해제.
     「큐 소모 N」은 보드가 이미 받아 둔 예상 소모(Q.prev.wings)다 — 여기서 새로 조회하지 않고 툴팁으로만 적는다. */
  const wings=(typeof Q!=="undefined"&&Q.prev&&Q.prev.wings!=null)?Number(Q.prev.wings):null;
  const P=pinList();
  const tile=(c)=>{const k=c.name==="골드"?"gold":(c.name==="정령의 날개"?"wings":"");
    const tip=`${c.name}${(k==="wings"&&wings)?` · 큐 소모 ${fmtN(wings)}`:""} · 우클릭하면 고정 해제`;
    return `<span class="stk-cc${k?" "+k:""}" data-skpin="${esc(c.name)}" title="${esc(tip)}"><span class="cn">${esc(c.name)}</span><span class="cv mono">${fmtN(c.amount)}</span></span>`;};
  let curHtml="";
  if(cur.length){const pinned=P.map((n)=>cur.find((c)=>c.name===n)).filter(Boolean);
    const rest=cur.filter((c)=>!P.includes(c.name)).sort((a,b)=>(b.amount||0)-(a.amount||0));
    curHtml=`<div class="stk-cc3">${pinned.map(tile).join("")}`
      +(rest.length?`<button class="stk-cmore${SK.curOpen?" on":""}" data-sk="cur" data-v="t" title="${SK.curOpen?"나머지 재화 접기":"나머지 재화 펼치기"}">+${fmtN(rest.length)} ${SK.curOpen?"▴":"▾"}</button>`:"")
      +`</div>`;
    if(SK.curOpen&&rest.length)curHtml+=`<div class="stk-call">${rest.map((c)=>`<span class="stk-ci" data-skpin="${esc(c.name)}" title="우클릭하면 상단에 고정"><span class="cn">${esc(c.name)}</span><span class="cv mono">${fmtN(c.amount)}</span></span>`).join("")}`
      +`<span class="stk-cnote">${fmtN(rest.length)}종 · 우클릭 = 상단 고정</span></div>`;}
  return wgt+`<div class="stk-cur">${curHtml}</div>`;}

/* ── 보기 4분할 전폭 → 위치 3분할 + 정렬 2분할 한 줄. 라벨은 코드 안 고정 글자값뿐이다.
   「무게」 정렬은 없다 — 아이템별 무게를 CLI 가 주지 않는다 ── */
function filtHtml(hasCat,nQueue,nShort){
  return `<div class="stk-view">`
    +SEG("view",[["all","전체"],["ing","재료만",!hasCat],["queue","큐에 쓰임",!nQueue],["short","부족",!nShort]],SK.view,"v4")
    +`</div><div class="stk-ls">`+SEG("loc",[["all","가방+창고"],["bag","가방"],["store","창고"]],SK.loc,"l3")
    +SEG("sort",[["name","이름"],["total","합계"]],SK.sort,"s2")+`</div>`;}

// 종류 칩의 의미색 (재료 회색 테두리 · 상자 보라 · 음식 초록). 그 밖은 회색
const catCls=(c)=>c==="상자"?" c-box":(c==="음식"?" c-food":"");

/* ── 행 한 벌: 1줄 이름·종류(·−n) ····· 합계 / 2줄 가방·창고 │ 쓰이는 곳 ····· 담기 ──
   넓은 폭은 CSS 가 .stk-l2 를 display:contents 로 풀어 여섯 열에 놓는다 (grid-template-areas) */
function rowHTML(r,qkeys){const uses=usesText(r.n,r.short,qkeys);
  return `<div class="stk-r${r.short>0?" short":""}${r.q?" q":""}" data-n="${esc(r.n)}">`
    +`<span class="stk-nm">${starHtml(r.tags)}<span class="t" title="${esc(r.disp)}">${esc(r.disp)}</span>`
    +`${r.cat?`<span class="stk-tg${catCls(r.cat)}">${esc(r.cat)}</span>`:""}`
    +`${r.short>0?`<span class="stk-sh" title="큐 기준 ${fmtN(r.short)}개 부족">−${fmtN(r.short)}</span>`:""}</span>`
    +`<span class="stk-tt mono">${fmtN(r.t)}</span>`
    +`<span class="stk-l2">`
    +`<span class="stk-b"><i>가방 </i><b class="mono${r.b?"":" z"}">${fmtN(r.b)}</b></span>`
    +`<span class="stk-s"><i>창고 </i><b class="mono${r.w?"":" z"}">${fmtN(r.w)}</b></span>`
    +`<span class="stk-us">${uses?`<span class="stk-vr">│</span>${uses}`:""}</span>`
    +`</span>`
    +`<span class="stk-ad">${addBtn(r.n,r.short)}</span></div>`;}

function renderStock(){const s=S.snap||{};const bag=s.stock||{};const sto=s.storage||{};
  const DI=(S.dict&&S.dict.items)||{};const hasCat=Object.keys(DI).length>0;
  const qset=queueNames();const qneed=queueNeed();const qkeys=queueRecipeKeys();

  // 요약·세그먼트·결과 수는 #tabBody 밖(카드 머리)에 있다 — 여기서 채운다
  const sum=$("stkSum");if(sum){sum.innerHTML=sumHtml(s);sum.hidden=false;}
  const ts=$("tabSum");if(ts)ts.innerHTML="";   // 검색 옆 요약 자리는 비운다 — 결과 수는 검색창 안 오른쪽 끝(#stkN)

  /* 행 = 가방·창고에 있는 것 + **큐가 쓰는데 하나도 없는 것**. 가진 것만으로 행을 만들면 가장 급한 재료(보유 0)가
     「부족」·「큐에 쓰임」 보기에서 빠진다 — 「양배추 −8 · 0 · 가방 0 창고 0」 같은 행이다.
     「전체」 보기는 가진 것만 — 0개 행은 큐 두 보기에서만 보탠다 */
  const held=[...new Set([...Object.keys(bag),...Object.keys(sto)])];
  const heldSet=new Set(held);
  const want=[...new Set([...[...qneed.keys()].filter((n)=>(qneed.get(n)||0)>0),...qset])].filter((n)=>!heldSet.has(n));
  const all=(SK.view==="short"||SK.view==="queue")?held.concat(want):held;
  const q=S.q.toLowerCase().split(/\s+/).filter(Boolean);
  let rows=all.map((n)=>{const b=Number(bag[n])||0,w=Number(sto[n])||0;const d=DI[n]||{};
    const t=b+w;const req=qneed.get(n)||0;const sp=starSplit(n);
    return {n,b,w,t,short:Math.max(0,req-t),q:qset.has(n),tags:sp.tags,disp:sp.name,plain:plainName(n),cat:d.categoryKo||d.category||""};})
    .filter((r)=>{if(SK.loc==="bag"&&r.b<=0)return false;if(SK.loc==="store"&&r.w<=0)return false;
      if(SK.view==="ing"&&!(r.cat==="재료"||r.cat==="Ingredient"))return false;
      if(SK.view==="queue"&&!r.q)return false;
      if(SK.view==="short"&&r.short<=0)return false;   // 「부족」 = 큐 기준 모자란 재료만
      if(q.length){const low=r.plain.toLowerCase();if(!q.every((t)=>low.includes(t)))return false;}return true;});
  const byName=(a,b)=>a.plain.localeCompare(b.plain,"ko");
  if(SK.sort!=="total")rows.sort(byName);
  else if(SK.view==="short")rows.sort((a,b)=>b.short-a.short||b.t-a.t||byName(a,b));   // 부족 보기의 합계 정렬 = 모자란 양이 큰 것부터
  else rows.sort((a,b)=>b.t-a.t||byName(a,b));
  const total=rows.length;const shown=limitOf("stock",total);const view=rows.slice(0,shown);
  const shortN=held.concat(want).reduce((a,n)=>a+((qneed.get(n)||0)>(Number(bag[n])||0)+(Number(sto[n])||0)?1:0),0);
  const qN=held.concat(want).reduce((a,n)=>a+(qset.has(n)?1:0),0);

  const cnt=$("stkN");if(cnt){cnt.textContent=`${fmtN(total)}종`;cnt.hidden=false;}

  const fl=$("stkFilt");if(fl){fl.innerHTML=filtHtml(hasCat,qN,shortN);fl.hidden=false;}

  let h='<div class="stk">';
  if(!held.length&&!all.length)h+='<div class="empty">재고 정보가 없습니다. 「갱신」을 눌러 게임에서 불러오세요.</div>';
  else if(!total)h+='<div class="empty">조건에 맞는 아이템이 없습니다.</div>';
  else{
    // 표 머리는 넓은 폭에서만 보인다 (좁은 폭은 2줄 행이라 열이 없다)
    h+='<div class="stk-r hd"><span class="stk-nm">이름</span><span class="stk-b">가방</span><span class="stk-s">창고</span><span class="stk-tt">합계</span><span class="stk-us">쓰이는 곳</span><span class="stk-ad">담기</span></div>';
    h+='<div class="stk-list">'+view.map((r)=>rowHTML(r,qkeys)).join("")+'</div>'+moreBtn("stock",shown,total);
    h+=`<div class="stk-foot"><span>보유는 가방+창고 합산 · 부족은 큐 기준</span><span class="grow"></span><span class="mono">${total?`1–${shown}`:0} / ${fmtN(total)}</span></div>`;}
  return h+'</div>';}

/* 「부족」 보기의 정렬은 합계 — 들어갈 때 그 전 정렬을 기억했다가, **나올 때 되돌린다**.
   예전에는 합계로 바꾼 채 남아 「전체」 로 돌아와도 이름순이 안 돌아왔다. 부족 보기 안에서 사람이 정렬을 바꾸면 그 뜻을 따른다 */
function viewSort(k,was){
  if(k==="view"&&SK.view==="short"&&was!=="short"){SK.sortBefore=SK.sort;SK.sort="total";}
  else if(k==="view"&&was==="short"&&SK.view!=="short"){if(SK.sortBefore)SK.sort=SK.sortBefore;SK.sortBefore=null;}
  else if(k==="sort"&&SK.view==="short")SK.sortBefore=null;}
/* 요약·세그먼트·결과 수 노드를 카드에 한 번 끼우고, 이벤트는 #tabCard 에 위임한다 —
   renderTab 이 #tabBody 의 innerHTML 을 갈아 끼워도 살아남는다.
   인라인 이벤트 속성(onclick=)은 CSP 가 막으므로 쓰지 않는다. */
(function bindStock(){const card=$("tabCard"),row=$("filterRow");if(!card||!row)return;
  const mk=(id,cls,tag)=>{let e=$(id);if(!e){e=document.createElement(tag||"div");e.id=id;e.className=cls;e.hidden=true;}return e;};
  const sum=mk("stkSum","stk-sum");card.insertBefore(sum,row);                       // 요약 — 검색 줄 위
  const fl=mk("stkFilt","stk-filt");card.insertBefore(fl,row.nextSibling);          // 보기·위치·정렬 — 검색 줄 아래
  const qw=row.querySelector(".qwrap");const cnt=mk("stkN","stk-n","span");if(qw)qw.appendChild(cnt);   // 결과 수 — 검색창 안 오른쪽 끝
  // #stock?view=ing&loc=bag&sort=total&cur=1 — 검증용 훅 (클릭 없이 그 상태로 렌더, 저장 안 함)
  {const hq=(location.hash||"").split("?")[1]||"";const g=(k)=>{const m=new RegExp("(?:^|&)"+k+"=([^&]*)").exec(hq);return m?decodeURIComponent(m[1]):null;};
    if(g("view")){SK.view=g("view");if(SK.view==="short"&&!g("sort")){SK.sortBefore=SK.sort;SK.sort="total";}}if(g("loc"))SK.loc=g("loc");if(g("sort"))SK.sort=g("sort");if(g("cur")==="1")SK.curOpen=true;}
  card.addEventListener("click",(e)=>{if(S.tab!=="stock")return;
    const add=e.target.closest("[data-sa]");if(add){e.stopPropagation();if(!add.disabled)stockAdd(add);return;}
    const pin=e.target.closest("[data-skpin]");if(pin&&pin.closest(".stk-call")){pinToggle(pin.dataset.skpin);return;}   // 펼침 안에서는 클릭도 고정 (터치)
    const b=e.target.closest("[data-sk]");if(!b||b.disabled)return;
    const k=b.dataset.sk,v=b.dataset.v;
    const was=SK.view;
    if(k==="cur")SK.curOpen=!SK.curOpen;else if(SK[k]!==undefined)SK[k]=v;
    viewSort(k,was);
    if(k==="view"||k==="loc")S.limit.stock=PAGE;
    keepScroll(renderTab);});
  // 우클릭 = 고정 토글 (펼침 안에서 고정 · 칸에서 해제)
  // (lp.fired: 손가락 길게 누르기가 이미 토글했다 — 안드로이드는 제 contextmenu 도 낸다. 다음 pointerdown 이 푼다)
  card.addEventListener("contextmenu",(e)=>{if(S.tab!=="stock")return;const pin=e.target.closest("[data-skpin]");if(!pin)return;e.preventDefault();if(lp.fired)return;pinToggle(pin.dataset.skpin);});
  /* **폰에서는 길게 누르기**. 아이폰 사파리는 길게 눌러도 contextmenu 를 안 내서
     상단 칸의 「고정 해제」가 폰에서 닿을 길이 없었다. 손가락으로 500ms — 우클릭과 같은 토글. 움직이면(스크롤) 취소,
     토글한 뒤 손을 떼며 나는 click 은 삼킨다 (펼침 안에서는 click 도 토글이라 두 번 뒤집히지 않게). */
  const lp={t:0,x:0,y:0,fired:false};
  const lpStop=()=>{if(lp.t){clearTimeout(lp.t);lp.t=0;}};
  card.addEventListener("pointerdown",(e)=>{if(e.pointerType!=="touch"||S.tab!=="stock")return;
    const pin=e.target.closest("[data-skpin]");lp.fired=false;lpStop();if(!pin)return;
    lp.x=e.clientX;lp.y=e.clientY;lp.t=setTimeout(()=>{lp.t=0;lp.fired=true;pinToggle(pin.dataset.skpin);},500);});
  card.addEventListener("pointermove",(e)=>{if(lp.t&&(Math.abs(e.clientX-lp.x)>8||Math.abs(e.clientY-lp.y)>8))lpStop();});
  card.addEventListener("pointerup",lpStop);card.addEventListener("pointercancel",lpStop);
  card.addEventListener("click",(e)=>{if(!lp.fired)return;lp.fired=false;e.stopPropagation();e.preventDefault();},true);})();

Object.assign(window.MW,{renderStock,runsFor,viewSort,rowHTML,plainName,nameHtml,starSplit,starHtml,sumHtml,SK,pinList,pinToggle,queueItems,queueNames,queueNeed,queueRecipeKeys,usesText,srcOf,addBtn,stockAdd});
