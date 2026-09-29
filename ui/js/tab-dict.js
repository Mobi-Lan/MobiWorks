// tab-dict.js — 사전 탭
/* 위에서 아래로:
     #filterRow(검색 전폭 — index.html 공용 줄) → #dictHead[ 레시피→재료 / 재료→쓰이는 곳 2등분 → 출처·확인 세그먼트 한 줄 → 통계 한 줄 ]
     → #tabBody[ 행 ] → #dictFoot[ ⋯ 메뉴 — 좁은 폭에서 「JSON 내보내기」가 여기로 온다 ]
   #dictHead·#dictFoot 는 이 파일이 #tabCard 에 **한 번** 끼우는 노드다 (tab-stock.js 가 #stkSum 을 끼우는 방식과 같다).
   renderTab 이 #tabBody 의 innerHTML 만 갈아 끼우므로 살아남고, 재고 탭에서는 tab-dict.css 가 숨긴다.

   레시피 행 = ① 종류 · 이름 ×n · 상태 칩 ② 재료(보유/필요, **부족분만 빨강**) ③ 출처 · 관찰 수 · 확인 — 오른쪽에 담기.
   마크업은 **한 벌**이다: 좁은 폭(≤600)이 3줄 행이고, 넓은 폭은 CSS 가 ①②③ 을 열로 펼친다 (tab-dict.css).

   담기: 가능하면 **골드 채움** = 큐에 바로 (list-core 의 bindQA → queueAdd, 기존 경로).
         재료 부족이면 **골드 외곽선** = 담기 서랍(MW.openDrawer)을 그 이름으로 연다 — 서랍의 「부족 재료도 함께 제안」이 켜진 채라
         부족 재료가 같이 담긴다. 실행 명령은 어디서도 부르지 않는다.
   재료 → 쓰이는 곳: 재료 이름 · 보유 · 쓰이는 레시피를 **종류 색 칩**으로 (칩을 누르면 그 레시피 행으로 점프). */

/* ── 사전 (관찰 누적 DB: /api/recipes) ── */
// 백엔드가 아직 없거나(404) 비어 있으면 조용히 빈 상태 — 오류 토스트를 띄우지 않는다
/* 관찰 DB(1.7MB)는 스냅숏보다 **늦게** 온다 — 터널 너머의 폰에서는 재고 탭이 먼저 그려져 「가공/제작 담기」가 전부
   「—」였다 (폰 실측). 재고 탭도 이 답을 쓰므로(srcOf·등급 표식) 그 탭이 떠 있으면 다시 그린다. */
async function loadDict(){const d=await api("/api/recipes");S.dict=(d&&d.recipes&&typeof d.recipes==="object")?d:null;if(S.tab==="dict"||S.tab==="stock")renderTab();}
const KIND_KO={craft:"제작",alter:"가공"};

/* ── 축 필터: 출처(sources) · 재료 확인(confirmed). 단일 선택, 저장하지 않는다 (전체·기본·내 관찰 / 전체·확인됨·미확인) ── */
const DSRC=[["","전체"],["seed","기본"],["self","내 관찰"]];
const DVER=[["","전체"],["ok","확인됨"],["miss","미확인"]];
const srcPass=(r,v)=>!v||((r.sources||{})[v]||0)>0;
const verPass=(r,v)=>!v||(v==="ok"?!!r.confirmed:!r.confirmed);
/* 검색: 검색창 안내 그대로 「공백 = AND」 — 낱말마다 레시피 이름 또는 확인된 재료명 중 하나에 들어 있어야 한다.
   예전에는 검색어 통째로 includes 해서 「가죽 신발」 이 「가죽 갑옷 신발」 을 못 찾았다. 대소문자 무시 */
const dqTerms=(q)=>String(q||"").toLowerCase().split(/\s+/).filter(Boolean);
const dqMatch=(r,q)=>{const ts=dqTerms(q);if(!ts.length)return true;const nm=String(r.name||"").toLowerCase();const ings=Object.keys(r.ingredients||{}).map((x)=>x.toLowerCase());
  return ts.every((t)=>nm.includes(t)||ings.some((x)=>x.includes(t)));};
// 재료 → 쓰이는 곳 검색: 낱말마다 재료 이름 또는 쓰이는 레시피 이름 중 하나에 (같은 AND 규칙)
const diMatch=(x,names,q)=>{const ts=dqTerms(q);if(!ts.length)return true;const xl=String(x).toLowerCase();const nl=names.map((n)=>String(n||"").toLowerCase());
  return ts.every((t)=>xl.includes(t)||nl.some((n)=>n.includes(t)));};

/* ── 행 상태 (① 의 상태 칩 + 담기 색을 정한다) ──
   보유는 가방+창고 합산(게임이 창고 재료를 원격으로 쓴다). 확인된 재료 중 하나라도 모자라면 「재료 부족」,
   재료를 하나도 모르면 마지막 관찰(lastReady / lastReason)로 대신하고, 그것도 없으면 「—」(모른다고 말한다). */
function dstate(r){const ing=r.ingredients||{};let known=false,short=false,unknown=false;
  for(const x in ing){const h=holdCalc(ing[x],bagOf(x),storeOf(x));if(h.unknown){unknown=true;continue;}known=true;if(h.short>0)short=true;}
  const rs=r.lastReason||"";
  if(rs&&rs!=="not_enough_ingredient"&&!r.lastReady)return {k:"warn",label:REASON_KO[rs]||rs};
  if(short)return {k:"bad",label:"재료 부족"};
  if(r.lastReady||(known&&!unknown))return {k:"ok",label:"가능"};
  if(rs)return {k:"bad",label:"재료 부족"};
  return {k:"unk",label:"—"};}
function facilityOf(name){const f=(S.dict&&S.dict.facilities)||{};for(const fn in f)if((f[fn].works||[]).includes(name))return fn;return "";}

/* ── ② 재료 줄: 「이름 보유/필요 · 이름 보유/필요 · …」. 부족분만 빨강(.short). 보유를 모르면 「?」 —
   행이 「재료 부족」인데 보유를 모르는 재료는 빨강으로 둔다. 가공은 끝에 시설 이름 ── */
function dmatsLine(r,st){const ing=r.ingredients||{};const names=Object.keys(ing);
  if(!names.length)return '<span class="dunk">재료 미확인 — 부족한 재료가 한 번이라도 잡혀야 쌓입니다</span>';
  const parts=names.map((x)=>{const need=ing[x];const bag=bagOf(x),sto=storeOf(x);const h=holdCalc(need,bag,sto);
    const red=h.unknown?st.k==="bad":h.short>0;
    const tip=h.unknown?"보유를 모릅니다 — 「갱신」하면 채워집니다":`가방 ${fmtN(bag)} · 창고 ${fmtN(sto)}${h.short>0?` · ${fmtN(h.short)}개 부족`:""}`;
    return `<span class="dmi${red?" short":""}" data-di="${esc(x)}" title="${esc(tip)}">${esc(x)} <span class="mono">${h.unknown?"?":fmtN(h.total)}/${fmtN(need)}</span></span>`;});
  const fac=r.kind==="alter"?facilityOf(r.name):"";if(fac)parts.push(`<span class="dfac">${esc(fac)}</span>`);
  return parts.join('<span class="ddot"> · </span>');}

/* ── ③ 출처 · 관찰 수 · 확인 (「기본 · 관찰 5,186회 · ✓ 확인」) ── */
function dsrcLine(r){const s=r.sources||{};const who=[s.seed?"기본":"",s.self?"내 관찰":""].filter(Boolean).join(" · ")||"—";
  const vf=r.confirmed
    ?'<span class="vf ok" title="서로 다른 출처 둘 이상이 모든 재료 수량에 동의">✓ 확인</span>'
    :'<span class="vf ms" title="한 출처만 봤거나 재료가 아직 안 쌓였습니다">미확인</span>';
  return `${esc(who)}${r.seen?` · 관찰 ${fmtN(r.seen)}회`:""} · ${vf}`;}

/* ── 담기 (가능 = 골드 채움 → 큐에 바로 / 재료 부족 = 골드 외곽선 → 담기 서랍에서 부족 재료 제안과 함께) ──
   [data-qa] 는 list-core 의 bindQA 가 잡아 queueAdd 로 보낸다 (data-fixed: 글자를 바꾸지 않음).
   [data-ddw] 는 아래 dictMount 가 잡아 MW.openDrawer({query:이름}) 을 연다. 막힘(스킬·시설 레벨)은 비활성 — 담아도 실패한다. */
function daddBtn(k,r,st){
  if(st.k==="ok")return `<button class="dadd go" data-qa="${esc(r.kind)}" data-n="${esc(r.name)}" data-id="${esc(k)}" data-c="1" data-fixed="1" title="큐에 바로 담습니다">담기</button>`;
  if(st.k==="warn")return `<button class="dadd line" disabled title="${esc(st.label)} — 지금은 만들 수 없습니다">담기</button>`;
  return `<button class="dadd line" data-ddw="${esc(r.name)}" title="재료가 모자랍니다 — 담기 서랍에서 부족 재료 제안과 함께 담습니다">담기</button>`;}

/* ── 레시피 행 (한 벌): 종류 | ①②③ | 담기 ── */
function drowHTML(k,r){const st=dstate(r);
  return `<div class="drow${S.dfocus===k?" focus":""}" data-dr="${esc(k)}">`
    +`<span class="dkind ${r.kind==="alter"?"alter":"craft"}">${esc(KIND_KO[r.kind]||r.kind)}</span>`
    +`<span class="dc1">`
    +`<span class="dl1"><span class="nm">${esc(r.name)}</span><span class="per mono">×${fmtN(r.per||1)}</span><span class="dst ${esc(st.k)}">${esc(st.label)}</span>`
    +`${r.variants>1?`<span class="dpath">경로 ${fmtN(r.variant||1)}/${fmtN(r.variants)}</span>`:""}</span>`
    +`<span class="dl2">${dmatsLine(r,st)}</span>`
    +`<span class="dl3">${dsrcLine(r)}</span>`
    +`</span><span class="dgo">${daddBtn(k,r,st)}</span></div>`;}

/* ── 재료 행: 이름 · 보유 · 쓰이는 곳 n / 쓰이는 레시피를 종류 색 칩으로 ──
   보유는 합산(툴팁에 가방·창고). 칩 = 「종류 이름 ×1회 사용량」, 동명 경로면 「(경로 n)」 을 붙여 어느 경로인지 보인다 */
/* 칩이 많으면 접는다 — 실데이터 「상급 가죽+」 는 쓰이는 레시피가 79개라 한 재료가 375px 화면을 통째로 덮었다.
   칩 줄은 두 줄 안쪽을 기준으로 → 처음 DCHIP_MAX 개만 보이고 「외 n ▾」 로 그 재료만 편다(S.dchips, 저장 안 함) */
const DCHIP_MAX=6;
function irowHTML(x,i,R,pass){const bag=i.stock!=null?i.stock:bagOf(x);const sto=i.storage!=null?i.storage:storeOf(x);const need=i.need||{};
  const uses=(i.usedBy||[]).filter(pass);
  if(!S.dchips)S.dchips=new Set();const openC=S.dchips.has(x);const shownU=openC?uses:uses.slice(0,DCHIP_MAX);const restN=uses.length-shownU.length;
  const chips=shownU.map((k)=>{const r=R[k]||{};const kind=r.kind||k.split(":")[0];
    return `<span class="duse" data-dr="${esc(k)}"><span class="dk ${kind==="alter"?"alter":"craft"}">${esc(KIND_KO[kind]||kind)}</span>${esc(r.name||k)}${r.variants>1?` (경로 ${fmtN(r.variant||1)})`:""} <span class="mono">×${need[k]?fmtN(need[k]):"?"}</span></span>`;}).join("")
    +(uses.length>DCHIP_MAX?`<button class="dmorec" data-dchips="${esc(x)}" title="${openC?"접기":"나머지 쓰이는 곳 펼치기"}">${openC?"접기 ▴":`외 ${fmtN(restN)} ▾`}</button>`:"");
  return `<div class="irow${S.dfocus===x?" focus":""}" data-din="${esc(x)}">`
    +`<span class="dl1"><span class="nm">${esc(x)}</span><span class="dih" title="${bag==null?"보유를 모릅니다 — 「갱신」하면 채워집니다":`가방 ${fmtN(bag)} · 창고 ${fmtN(sto||0)}`}">보유 <b class="mono">${bag==null?"?":fmtN(bag+(sto||0))}</b></span><span class="grow"></span><span class="dun">쓰이는 곳 ${fmtN(uses.length)}</span></span>`
    +`<span class="dl2">${chips||'<span class="dunk">아직 쓰이는 레시피가 없습니다</span>'}</span></div>`;}

/* ── 머리(#dictHead): 2등분 소탭 → 출처·확인 세그먼트 한 줄(넓은 폭엔 오른쪽에 JSON 내보내기) → 통계 한 줄 ── */
// 내보내기는 fetch(헤더 토큰) → blob 저장 — 토큰을 주소에 싣지 않는다. 클릭은 아래 dictMount 가 받는다
function dExportLink(id){return `<a class="dlink" id="${id}" href="/api/recipes/export" download="mobiworks-recipes.json" data-dexport>JSON 내보내기</a>`;}
function dheadHTML(n,st,cSrc,cVer){
  const sub=`<div class="dsub">${[["recipes","레시피 → 재료"],["ings","재료 → 쓰이는 곳"]].map(([v,l])=>`<button data-ds="${v}" class="${S.dsub===v?"on":""}">${l}</button>`).join("")}</div>`;
  // 「전체」에는 수를 붙이지 않는다 — 수는 툴팁으로만
  const seg=(cls,list,cur,cnt)=>`<span class="dseg">${list.map(([v,l])=>`<button data-${cls}="${v}" class="${(cur||"")===v?"on":""}"${v&&cnt?` title="${fmtN(cnt[v]||0)}건"`:""}>${l}</button>`).join("")}</span>`;
  // 라벨과 세그먼트는 한 묶음(.dsg) — 좁은 창에서 줄이 넘어갈 때 라벨만 떨어지지 않게 (tab-dict.css ≤360)
  const segs=`<div class="dsegs"><span class="dsg"><span class="dl">출처</span>${seg("dsrc",DSRC,S.dsrc,cSrc)}</span><span class="dsg"><span class="dl" title="재료 확인 — 서로 다른 출처 둘 이상이 수량에 동의했는지">확인</span>${seg("dver",DVER,S.dver,cVer)}</span><span class="grow"></span>${dExportLink("dExport")}</div>`;
  const stat=`<div class="dstat">레시피 <b class="mono">${fmtN(n.r)}</b> · 재료 ${fmtN(n.i)} · 시설 ${fmtN(n.f)}`
    +`${st.confirmed?` · <span class="vf ok">✓ 확인 ${fmtN(st.confirmed)}</span>`:""}${st.updatedAt?` · 갱신 ${ago(st.updatedAt)}`:""}</div>`;
  return sub+segs+stat;}
// 발(#dictFoot): ⋯ 메뉴 — 좁은 폭에서만 보인다 (tab-dict.css). 안에 「JSON 내보내기」
function dfootHTML(){return `<span class="dfnote">재료를 누르면 그 재료의 「쓰이는 곳」으로</span><span class="grow"></span>`
  +`<button class="dmore" data-dmenu title="더 보기">⋯</button><div class="dmenu" hidden>${dExportLink("dExportM")}</div>`;}

let dhead="";
function renderDict(){const d=S.dict;const q=S.q;const R=(d&&d.recipes)||{};const I=(d&&d.ingredients)||{};const st=(d&&d.stats)||{};
  const n={r:Object.keys(R).length,i:Object.keys(I).length,f:Object.keys((d&&d.facilities)||{}).length};
  const keys=Object.keys(R);const base=keys.filter((k)=>dqMatch(R[k],q));
  // 축 개수(툴팁용): 검색 + 다른 축을 적용한 뒤 센다
  const cSrc={},cVer={};for(const k of base){const r=R[k];
    if(verPass(r,S.dver))for(const[v]of DSRC)if(v&&srcPass(r,v))cSrc[v]=(cSrc[v]||0)+1;
    if(srcPass(r,S.dsrc))for(const[v]of DVER)if(v&&verPass(r,v))cVer[v]=(cVer[v]||0)+1;}
  dhead=dheadHTML(n,st,cSrc,cVer);
  if(!n.r)return '<div class="empty">아직 관찰된 레시피가 없습니다. 「갱신」을 누르면 쌓이기 시작합니다.</div>';
  let h="";
  // 저장 파일이 이 앱보다 새 스키마면 서버가 쓰기를 막는다 — 모르는 섹션을 옛 앱이 깎아 내리지 않게
  if(d.schemaAhead)h+=`<div class="dwarn">이 앱보다 새 버전이 만든 데이터입니다 (스키마 ${esc(String(d.schema||""))}) — 앱을 업데이트하세요. 지금은 <b>읽기 전용</b>이라 관찰이 저장되지 않습니다.</div>`;
  const pass=(k)=>!!R[k]&&srcPass(R[k],S.dsrc)&&verPass(R[k],S.dver);
  if(S.dsub==="recipes"){
    if(d.conflicts&&d.conflicts.length)h+=`<div class="dwarn">⚠ 필요량이 다르게 관찰된 재료 ${fmtN(d.conflicts.length)}건 — 큰 값을 씁니다</div>`;
    // 평평한 행 하나 = 경로 하나 (동명 경로는 「경로 n/N」 으로 구분, 이름순으로 붙어 나온다)
    const rows=base.filter(pass).sort((a,b)=>R[a].name.localeCompare(R[b].name,"ko")||(R[a].variant||1)-(R[b].variant||1));
    if(!rows.length)return h+'<div class="empty">표시할 레시피가 없습니다.</div>';
    const lim=limitOf("dict",rows.length);
    // 표 머리는 넓은 폭에서만 보인다 (좁은 폭은 3줄 행이라 열이 없다)
    h+='<div class="dlist"><div class="drow hd"><span>종류</span><span>레시피</span><span>재료 (보유 / 필요)</span><span>출처 · 확인</span><span></span></div>'
      +rows.slice(0,lim).map((k)=>drowHTML(k,R[k])).join("")+'</div>'+moreBtn("dict",Math.min(lim,rows.length),rows.length);
    return h;}
  // ── 재료 → 쓰이는 곳: 출처·확인 축은 칩(쓰이는 레시피)에 건다. 축을 켰는데 남는 칩이 없는 재료는 뺀다
  const names=Object.keys(I).filter((x)=>{const uses=(I[x].usedBy||[]).filter(pass);if((S.dsrc||S.dver)&&!uses.length)return false;
    return diMatch(x,uses.map((k)=>R[k].name),q);}).sort((a,b)=>a.localeCompare(b,"ko"));
  if(!names.length)return h+'<div class="empty">표시할 재료가 없습니다.</div>';
  const lim=limitOf("dictI",names.length);
  h+='<div class="dlist"><div class="irow hd"><span>재료 · 보유</span><span>쓰이는 곳</span></div>'
    +names.slice(0,lim).map((x)=>irowHTML(x,I[x],R,pass)).join("")+'</div>'+moreBtn("dictI",Math.min(lim,names.length),names.length);
  return h;}

/* ── 머리·발 노드를 카드에 한 번 끼우고, 클릭은 #tabCard 에 위임한다 (renderTab 이 #tabBody 를 갈아 끼워도 살아남는다).
   인라인 이벤트 속성(onclick=)은 CSP 가 막으므로 쓰지 않는다. bindQA 가 잡는 [data-qa] 는 stopPropagation 이라 여기까지 오지 않는다. ── */
function dictJump(sub,key){S.dsub=sub;S.dfocus=key;S.q="";const q=$("q");if(q)q.value="";const x=$("qClearBtn");if(x)x.hidden=true;renderTab();
  const t=$("tabBody").querySelector(".focus");if(t)t.scrollIntoView({block:"center"});}
(function dictMount(){const card=$("tabCard"),body=$("tabBody");if(!card||!body)return;
  const mk=(id,cls)=>{let e=$(id);if(!e){e=document.createElement("div");e.id=id;e.className=cls;}return e;};
  card.insertBefore(mk("dictHead","dhead"),body);   // 검색 줄 아래 · 목록 위
  card.appendChild(mk("dictFoot","dfootbar"));      // 목록 아래 — 카드 발에 고정
  card.addEventListener("click",(e)=>{if(S.tab!=="dict")return;const t=e.target;
    const ds=t.closest("[data-ds]");if(ds){S.dsub=ds.dataset.ds;S.dfocus=null;renderTab();return;}
    const sr=t.closest("[data-dsrc]");if(sr){S.dsrc=sr.dataset.dsrc||null;S.limit.dict=PAGE;keepScroll(renderTab);return;}
    const vr=t.closest("[data-dver]");if(vr){S.dver=vr.dataset.dver||null;S.limit.dict=PAGE;keepScroll(renderTab);return;}
    const dc=t.closest("[data-dchips]");if(dc){if(!S.dchips)S.dchips=new Set();const x=dc.dataset.dchips;if(S.dchips.has(x))S.dchips.delete(x);else S.dchips.add(x);keepScroll(renderTab);return;}
    const dx=t.closest("[data-dexport]");if(dx){e.preventDefault();const m=card.querySelector(".dmenu");if(m)m.hidden=true;saveDownload("/api/recipes/export","mobiworks-recipes.json");return;}
    const mn=t.closest("[data-dmenu]");if(mn){const m=card.querySelector(".dmenu");if(m)m.hidden=!m.hidden;return;}
    // 재료 부족 행의 담기 → 담기 서랍을 그 이름으로 (부족 재료 제안은 서랍의 스위치가 정한다 — 기본 켜짐)
    const dw=t.closest("[data-ddw]");if(dw){if(!dw.disabled){if(MW.openDrawer)MW.openDrawer({target:"root",query:dw.dataset.ddw});else toast("담기 서랍 준비 중입니다");}return;}
    const di=t.closest("[data-di]");if(di){dictJump("ings",di.dataset.di);return;}
    const dr=t.closest("[data-dr]");if(dr&&!dr.classList.contains("drow")){dictJump("recipes",dr.dataset.dr);return;}});
  // 메뉴 밖을 누르면 닫는다
  document.addEventListener("click",(e)=>{const m=card.querySelector(".dmenu");if(m&&!m.hidden&&!e.target.closest("[data-dmenu],.dmenu"))m.hidden=true;});})();
// renderTab 이 #tabBody 를 그린 뒤 부른다 — 머리·발을 채우고 요약 자리(#tabSum)는 비운다 (검색이 전폭이 되게, 앞 탭의 요약이 남지 않게)
function bindDict(){const ts=$("tabSum");if(ts)ts.innerHTML="";
  const hd=$("dictHead");if(hd)hd.innerHTML=dhead;
  const ft=$("dictFoot");if(ft&&!ft.dataset.built){ft.innerHTML=dfootHTML();ft.dataset.built="1";}}

Object.assign(window.MW,{loadDict,KIND_KO,dqTerms,dqMatch,diMatch,DCHIP_MAX,DSRC,DVER,dstate,dmatsLine,dsrcLine,daddBtn,drowHTML,irowHTML,dheadHTML,dfootHTML,facilityOf,renderDict,bindDict,dictJump});
