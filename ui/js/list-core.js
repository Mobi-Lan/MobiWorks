// list-core.js — 목록 공통: 배지·펼침 행, 검색 토큰 필터·초성 색인·칩·정렬, 필터 줄 렌더, 큐 담기 버튼(gatherBtns 등)·bindQA, renderTab 분기
function badge(r){if(r.ready)return '<span class="badge ok">가능</span>';if(r.blocked)return `<span class="badge warn">${esc(r.reasonKo||"막힘")}</span>`;return '<span class="badge bad">재료 부족</span>';}
// 펼친 행: 관찰 DB 에서 확인된 재료(known)를 현재 재고와 견주어 보여 준다. missing 에 있는 재료는 강조
// 부족 판정은 가방 기준(게임이 창고를 안 센다). 가방엔 없어도 창고에 있으면 warn — 옮기면 된다는 뜻
function knownRow(r){const k=r.known||{};const names=Object.keys(k);if(!names.length)return '<div class="lc-sub muted">재료 미확인 — 부족한 재료가 한 번이라도 잡혀야 쌓입니다</div>';
  const hot=new Set((r.missing||[]).filter((m)=>m.short>0).map((m)=>m.name));
  // 칩의 숫자 = 합산 보유 / 필요. 합산으로 부족할 때만 빨강, 가방만 모자라면 주황(「창고에서 n개 이송」)
  return '<div class="lc-sub">'+names.map((n)=>{const need=k[n];const bag=bagOf(n),sto=storeOf(n);const h=holdCalc(need,bag,sto);const short=h.unknown?hot.has(n):h.short>0;
    const cls=short?"bad":(h.fromStorage?"warn":(h.unknown?"":"ok"));
    return `<span class="kchip ${cls}${hot.has(n)?" hot":""}" title="${h.unknown?"보유를 모릅니다 — 갱신하면 채워집니다":`가방 ${fmtN(bag)} · 창고 ${fmtN(sto)}`}">${esc(n)} ×${need} · ${h.unknown?"?":fmtN(h.total)}/${need}${h.fromStorage?` · 창고 이송 ${fmtN(h.fromStorage)}`:""}</span>`;}).join("")+`<span class="muted">확인된 재료 ${names.length}개 · 미확인이 있을 수 있음</span></div>`;}
const varBadge=(r)=>r.variants>1?`<span class="badge line">경로 ${r.variant||1}/${r.variants}</span>`:"";
/* ── 목록 검색·필터·정렬 (MobiFolio 방식, 전부 클라이언트). 새로고침이면 초기화(저장 안 함) ──
   검색: 공백 토큰 전부 AND, norm(NFKC 소문자·공백·-_. 제거) 부분 문자열 또는 이름 소문자 또는 재료명. 축(초성 색인·상태·분류·도구)은 단일 토글, 전부 AND */
const FX={};const fx=(t)=>FX[t]||(FX[t]={st:null,cat:null,init:null,tool:false,sort:"default"});
const normKey=(n)=>String(n||"").normalize("NFKC").toLowerCase().replace(/[\s\-_.]/g,"");
const CHO=["ㄱ","ㄱ","ㄴ","ㄷ","ㄷ","ㄹ","ㅁ","ㅂ","ㅂ","ㅅ","ㅅ","ㅇ","ㅈ","ㅈ","ㅊ","ㅋ","ㅌ","ㅍ","ㅎ"];const INIT_KEYS=["ㄱ","ㄴ","ㄷ","ㄹ","ㅁ","ㅂ","ㅅ","ㅇ","ㅈ","ㅊ","ㅋ","ㅌ","ㅍ","ㅎ",..."ABCDEFGHIJKLMNOPQRSTUVWXYZ","#"];
function initialOf(name){const t=String(name||"").replace(/^[\s"'「『(\[<【《]+/,"");const ch=t.charAt(0);if(!ch)return "#";const c=ch.charCodeAt(0);
  if(c>=0xAC00&&c<=0xD7A3)return CHO[Math.floor((c-0xAC00)/588)];const u=ch.toUpperCase();if(/[A-Z]/.test(u))return u;if(/[ㄱ-ㅎ]/.test(ch))return CHO["ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ".indexOf(ch)]||"#";return "#";}
const stOf=(r)=>r.ready?"ready":(r.blocked?"blocked":"short");const FILT_ST={ready:"가능",short:"재료 부족",blocked:"막힘"};
function qMatch(i){if(!S.q)return true;const qt=S.q.toLowerCase().split(/\s+/).filter(Boolean);const nm=(i.norm||normKey(i.name));const low=(i.name||"").toLowerCase();
  return qt.every((t)=>nm.includes(t.replace(/[\s\-_.]/g,""))||low.includes(t)||(i.known?Object.keys(i.known).some((k)=>k.toLowerCase().includes(t)):false));}
// 축 필터: skip 에 든 축은 적용하지 않는다 (그 축의 칩 개수를 셀 때)
function axPass(tab,r,f,skip){if(!skip)skip={};if(!skip.init&&f.init&&initialOf(r.name)!==f.init)return false;
  if(tab==="gather"){if(!skip.cat&&f.cat&&(r.skill||"미분류")!==f.cat)return false;if(!skip.tool&&f.tool&&!r.toolOk)return false;return true;}
  if(!skip.st&&f.st&&stOf(r)!==f.st)return false;if(!skip.cat&&f.cat&&(r.cat||"미분류")!==f.cat)return false;return true;}
function sortRows(rows,f,tab){if(f.sort==="name")return [...rows].sort((a,b)=>(a.name||"힣").localeCompare(b.name||"힣","ko"));
  if(f.sort==="ready")return [...rows].sort((a,b)=>(tab==="gather"?(b.toolOk?1:0)-(a.toolOk?1:0):(b.ready?1:0)-(a.ready?1:0)));return rows;}
const chipEl=(k,v,label,n,on,cls)=>`<button class="chip${on?" on":""} ${cls||""}" data-fx="${k}" data-v="${esc(v)}" ${n===0&&!on?"disabled":""}>${esc(label)} <span class="n">${n}</span></button>`;
/* 필터 줄(v2): 한 줄에 [상태 세그먼트 | 분류 칩 | 초성 토글]. 초성 색인은 접어 두고 토글로 편다(2줄 → 1줄 압축) */
function renderFilters(tab,list){const box=$("chips");if(!["craft","alter","gather"].includes(tab)){box.hidden=true;box.innerHTML="";const sg0=$("sortSeg");if(sg0)sg0.hidden=true;return;}
  const f=fx(tab);const base=list.filter(qMatch);const F=(S.snap&&S.snap.filters)||{};
  if(f.idxOpen===undefined)f.idxOpen=/[?&]idx=1(?:&|$)/.test(location.hash||"");   // ?idx=1: 초성 색인을 편 채로 (헤드리스 검증용)
  // 초성·알파벳 색인 (다른 축 적용 후 개수) — 서버 summary.byInitial 이 있어도 화면 값은 현재 필터 기준
  const ic={};for(const r of base)if(axPass(tab,r,f,{init:1})){const k=initialOf(r.name);ic[k]=(ic[k]||0)+1;}
  const cc={};for(const r of base)if(axPass(tab,r,f,{cat:1})){const k=tab==="gather"?(r.skill||"미분류"):(r.cat||"미분류");cc[k]=(cc[k]||0)+1;}
  // 서버 filters: gatherSkills=[{label,count}], recipeCats={craft:[{label,count}],alter:[…]} (옛 모양의 문자열 배열도 받는다)
  const lab=(x)=>typeof x==="string"?x:(x&&x.label)||"";const srvCats=(tab==="gather"?(F.gatherSkills||[]):(Array.isArray(F.recipeCats)?F.recipeCats:((F.recipeCats||{})[tab]||[]))).map(lab).filter(Boolean);
  const cats=[...new Set([...srvCats,...Object.keys(cc)])].filter((c)=>c!=="미분류");if(cc["미분류"])cats.push("미분류");
  let h='<div class="crow">';
  if(tab!=="gather"){const sc={ready:0,short:0,blocked:0};let tot=0;for(const r of base)if(axPass(tab,r,f,{st:1})){sc[stOf(r)]++;tot++;}
    h+=`<div class="seg stseg"><button data-fx="st" data-v="" class="${f.st?"":"on"}">전체 ${tot}</button>`+
       Object.keys(FILT_ST).map((k)=>`<button data-fx="st" data-v="${k}" class="st-${k}${f.st===k?" on":""}">${FILT_ST[k]} ${sc[k]}</button>`).join("")+
       '</div><span class="vr"></span>';}
  else h+=`<button class="chip${f.tool?" on":""}" data-fx="tool" data-v="1">도구 있음만</button><span class="vr"></span>`;
  h+=`<div class="cats">${cats.map((c)=>chipEl("cat",c,c,cc[c]||0,f.cat===c)).join("")}</div>`;
  if(cats.length&&(F.inferred||(!F.recipeCats&&!F.gatherSkills)))h+='<span class="inf" title="게임이 분류·스킬을 주지 않아 이름으로 추정했습니다. data/categories.json 으로 고칠 수 있습니다">추정</span>';
  h+='<span class="grow"></span>';
  h+=`<button class="idxbtn${f.init?" on":""}" data-idx>${f.init?`초성 ${esc(f.init)}`:"ㄱ–ㅎ · A–Z"}<span class="cv">${f.idxOpen?"▴":"▾"}</span></button></div>`;
  if(f.idxOpen)h+=`<div class="idxrow"><div class="idx">${INIT_KEYS.map((k)=>`<span class="${f.init===k?"on":(ic[k]?"":"zero")}" data-init="${k}" title="${ic[k]||0}">${k}</span>`).join("")}</div></div>`;
  box.innerHTML=h;box.hidden=false;
  const el=box.querySelector("[data-idx]");if(el)el.onclick=()=>{f.idxOpen=!f.idxOpen;renderFilters(tab,list);};
  box.querySelectorAll("[data-init]").forEach((x)=>{x.onclick=()=>{if(x.classList.contains("zero"))return;const k=x.dataset.init;f.init=f.init===k?null:k;S.limit[tab]=PAGE;renderTab();};});
  box.querySelectorAll("[data-fx]").forEach((b)=>{b.onclick=()=>{const k=b.dataset.fx,v=b.dataset.v;if(k==="tool")f.tool=!f.tool;else f[k]=(!v||f[k]===v)?null:v;S.limit[tab]=PAGE;renderTab();};});
  const sg=$("sortSeg");if(sg){sg.hidden=false;sg.querySelectorAll("button").forEach((b)=>{b.classList.toggle("on",b.dataset.s===f.sort);if(b.dataset.s==="ready")b.textContent=tab==="gather"?"도구 먼저":"가능 먼저";b.onclick=()=>{f.sort=b.dataset.s;renderTab();};});}}
/* 행 안의 「− n + · 담기」 (v2: 담기에 회수).
   가능한 행은 골드 = 바로 큐에 담고, 재료 부족·막힘 행은 외곽선 = 계획 팝업을 연다(부족 재료도 함께 담을지 거기서 고른다) */
function rowAdd(kind,r,id){const d=r.blocked?" disabled":"";
  const st=`<span class="stepper"><button data-gd title="−1"${d}>−</button><span class="v">1</span><button data-gi title="+1"${d}>+</button></span>`;
  const b=r.blocked
    ?`<button class="pbtn" disabled title="${esc(r.reasonKo||"지금은 만들 수 없습니다")}">담기</button>`
    :(r.ready
      ?`<button class="pbtn gold" data-qa="${kind}" data-n="${esc(r.name)}" data-id="${esc(id)}" data-c="1" data-mul="1" data-fixed="1" title="큐에 바로 담습니다">담기</button>`
      :`<button class="pbtn" data-plan="${esc(id)}" data-c="1" title="부족 재료도 함께 담을지 계획 창에서 고릅니다">담기</button>`);
  return `<span class="radd" data-gl="${esc(r.name)}">${st}${b}</span>`;}
// 목록 발: 안내 · 「더 보기」 · 범위 카운터 (스크롤해도 카드 아래에 붙는다)
function listFoot(key,shown,total,hint){const more=shown<total?`<button class="small" data-more="${esc(key)}">더 보기</button>`:"";
  return `<div class="lfoot">${hint?`<span class="ft">${hint}</span>`:""}<span class="grow"></span>${more}<span class="num rng">${total?`1–${fmtN(shown)} / ${fmtN(total)}`:"0"}</span></div>`;}
// 목록 재렌더 때 스크롤 위치 보존 (안 하면 클릭마다 맨 위로 튄다)
function keepScroll(fn){const l=$("tabBody");const top=l?l.scrollTop:0;fn();if(l)l.scrollTop=top;}
/* ── 큐 담기 공통 (B 규칙). 채집은 CLI 가 호출당 무조건 최대 100개를 캔다(목표 인자 없음).
   큐의 target 은 **이번에 캘 개수**다 — 보유량을 채우는 목표가 아니라 여기서 보내는 값 그대로 캔다:
   필요분 = 부족분 N (예상 ⌈N/100⌉회) · 최대 = ⌈N/100⌉×100 (최소 100, 그 회차를 꽉 채움) · 문맥 없으면 100개 × N회 ── */
const gmax=(n)=>Math.max(100,Math.ceil(Math.max(1,n)/100)*100);
const gpass=(n)=>Math.max(1,Math.ceil(n/100));
/* 채집 개수 스테퍼 한 칸 — 100 단위 눈금에 맞춰 움직인다 (250 → + 300 / − 200). 직접 입력은 1~99,999 그대로.
   100 아래 값(예: 50)에서 − 는 그대로 두고, + 는 100 으로 간다. d 는 +1 | −1 */
const GCOUNT_MAX=99999;
const gstepN=(v,d)=>{v=Math.max(0,Math.round(Number(v)||0));
  return d>0?Math.min(GCOUNT_MAX,Math.floor(v/100)*100+100):Math.max(Math.min(v,100)||1,Math.ceil(v/100)*100-100);};
/* 채집 안내 한 줄 — 「3회 · 날개 15」 / 「가방 47 → 목표 297」 (가방을 모르면 빈 글). 담는 화면이 같은 글을 쓴다 */
const gRunsTxt=(n)=>`${fmtN(gpass(n))}회 · 날개 ${fmtN(gpass(n)*5)}`;
const gGoalTxt=(bag,n)=>bag!=null?`가방 ${fmtN(bag)} → 목표 ${fmtN(bag+n)}`:"";
function gatherBtns(name,short){return `<span class="gseg"><button class="pbtn" data-qa="gather" data-n="${esc(name)}" data-t="${short}" title="${fmtN(short)}개 캐기 · 예상 ${gpass(short)}회">필요분 ${fmtN(short)}</button><button class="pbtn" data-qa="gather" data-n="${esc(name)}" data-t="${gmax(short)}" title="회차를 꽉 채움 · ${gpass(short)}회 × 100">최대 ${fmtN(gmax(short))}</button></span><span class="hint">회당 최대 100개</span>`;}
function gatherLoop(name,ok){const d=ok?"":" disabled";return `<span class="gloop" data-gl="${esc(name)}"><span class="stepper"><button data-gd title="−1"${d}>−</button><span class="v">1</span><button data-gi title="+1"${d}>+</button></span><button class="pbtn" data-qa="gather" data-n="${esc(name)}" data-mul="1"${d}>100개 × 1회 담기</button><span class="hint">${ok?"회당 최대 100개":"도구 없음"}</span></span>`;}
function madeLoop(name,id,kind){return `<span class="gloop" data-gl="${esc(name)}"><span class="stepper"><button data-gd title="−1">−</button><span class="v">1</span><button data-gi title="+1">+</button></span><button class="pbtn" data-qa="${kind}" data-n="${esc(name)}" data-id="${esc(id)}" data-c="1" data-mul="1">${KIND_KO[kind]||kind} 1회 담기</button></span>`;}
// [data-qa] 버튼 → 큐 add. 스테퍼([data-gl])가 있으면 그 횟수를 쓴다. 러너 실행 중에도 담을 수 있다(서버가 허용)
function bindQA(root){
  root.querySelectorAll("[data-gd],[data-gi]").forEach((b)=>{b.onclick=(e)=>{e.stopPropagation();const w=b.closest("[data-gl]");const v=w.querySelector(".v");const n=Math.max(1,Math.min(999,Number(v.textContent)+(b.hasAttribute("data-gi")?1:-1)));v.textContent=n;
    const p=w.querySelector("[data-plan]");if(p)p.dataset.c=n;   // 계획 팝업을 여는 담기(재료 부족 행)도 이 횟수를 쓴다
    const q=w.querySelector("[data-qa]");if(!q)return;q.dataset.mul=n;if(q.dataset.qa!=="gather")q.dataset.c=n;
    if(q.dataset.fixed!=null)return;   // 행 안의 작은 담기 버튼은 글자를 바꾸지 않는다
    if(q.dataset.qa==="gather")q.textContent=`100개 × ${n}회 담기`;else q.textContent=`${KIND_KO[q.dataset.qa]||q.dataset.qa} ${n}회 담기`;};});
  root.querySelectorAll("[data-qa]").forEach((x)=>{x.onclick=(e)=>{e.stopPropagation();if(x.disabled)return;const t=x.dataset.qa;
    if(t==="gather"){const target=x.dataset.t!=null?Number(x.dataset.t):100*(Number(x.dataset.mul)||1);queueAdd({type:"gather",name:x.dataset.n,target});}
    else queueAdd({type:t,name:x.dataset.n,recipeId:x.dataset.id||undefined,count:Number(x.dataset.c)||1,...(t==="alter"?{collect:"later"}:{})});};});
  // 재료 부족·막힘 행의 담기 → 그 행을 고르고 계획 팝업을 연다
  root.querySelectorAll("[data-plan]").forEach((x)=>{x.onclick=(e)=>{e.stopPropagation();const rc=x.closest(".rc");if(!rc)return;
    S.sel={kind:rc.dataset.k,name:rc.dataset.n,id:rc.dataset.id,variant:Number(rc.dataset.v)||1,variants:Number(rc.dataset.vs)||1};
    renderTab();openPlan(Math.max(1,Number(x.dataset.c)||1));};});}
function renderTab(){const s=S.snap||{};let h="";
  if(S.tab==="craft")h=renderRecipes(s.craft||[],"craft");else if(S.tab==="alter")h=renderRecipes(s.alter||[],"alter");else if(S.tab==="gather")h=renderGather();else if(S.tab==="dict")h=renderDict();else h=renderStock();
  $("tabBody").innerHTML=h;renderFilters(S.tab,S.tab==="gather"?(s.gather||[]):(s[S.tab]||[]));
  $("tabBody").querySelectorAll(".rc[data-k]").forEach((el)=>{el.onclick=()=>{S.sel={kind:el.dataset.k,name:el.dataset.n,id:el.dataset.id,variant:Number(el.dataset.v)||1,variants:Number(el.dataset.vs)||1};renderTab();openPlan(1);};});
  // ▸ 토글은 행 클릭(계획 패널)과 분리 — 재료가 없는(dim) 행도 눌리면 "미확인" 안내를 보여 준다
  $("tabBody").querySelectorAll(".tg[data-key]").forEach((b)=>{b.onclick=(e)=>{e.stopPropagation();const k=b.dataset.key;if(S.open.has(k))S.open.delete(k);else S.open.add(k);renderTab();};});
  $("tabBody").querySelectorAll("[data-more]").forEach((b)=>{b.onclick=()=>{const k=b.dataset.more;S.limit[k]=(S.limit[k]||PAGE)+PAGE;renderTab();};});
  if(S.tab==="dict")bindDict();
  bindQA($("tabBody"));
  const showP=S.sel&&(S.tab==="craft"||S.tab==="alter");$("plan").hidden=!showP;if(!showP)$("plan").innerHTML="";}

Object.assign(window.MW,{badge,knownRow,varBadge,FX,normKey,CHO,initialOf,stOf,qMatch,axPass,sortRows,chipEl,renderFilters,keepScroll,gmax,gpass,gatherBtns,gatherLoop,madeLoop,rowAdd,listFoot,bindQA,renderTab});
