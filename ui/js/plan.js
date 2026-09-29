// plan.js — 계획 팝업: 재료 행 계산·즉시 갱신·최대·필요한 것 전부 담기 미리보기·길게 누르기(holdBtn)
/* ── 계획 팝업 (dialog#plan) — 레시피 행 클릭으로 연다. 옆 열이 아니라 설정 창과 같은 규격의 모달.
   횟수(+/−·직접 입력·길게 누르기)는 클라이언트에서 즉시 계산해 해당 텍스트 노드만 갱신한다(패널 재생성 없음 → 깜빡임 없음).
   /api/plan 은 250ms 디바운스로 한 번만 불러 source/sourceId·창고 보강이 달라졌을 때만 조용히 반영한다. ── */
const P={sel:null,rec:null,count:1,rows:[],cards:new Map(),born:0,fetchT:null,btnKey:new Map()};
// 재료의 공급 경로: 채집 가능 → gather, 다른 레시피 산출 → 그 레시피(첫 경로). 관찰 DB(S.dict)에서 — 서버 /api/plan 의 source 와 같은 규칙
function planSource(name){const I=(S.dict&&S.dict.ingredients)||{};const R=(S.dict&&S.dict.recipes)||{};const i=I[name]||{};
  if(i.gatherable)return {source:"gather",sourceId:null};const id=(i.madeBy||[])[0];
  if(id){const kind=(R[id]&&R[id].kind)||id.split(":")[0];return {source:kind,sourceId:id};}return {source:null,sourceId:null};}
// 재료별 1회 필요량: missing(지금 부족한 것, CLI 의 Required) ∪ known(관찰 DB 의 확인된 재료). 보유는 가방(게임이 창고를 안 센다), 창고는 따로
function planRows(rec){const per={};const own={};
  for(const m of rec.missing||[]){per[m.name]=m.required;own[m.name]=m.owned;}
  for(const n in (rec.known||{}))if(per[n]==null)per[n]=rec.known[n];
  return Object.keys(per).map((n)=>{const bag=bagOf(n);const o=bag!=null?bag:(own[n]!=null?own[n]:null);return {name:n,perCount:per[n],owned:o,unknownOwned:o==null,storage:storeOf(n),...planSource(n)};});}
// 보유 = 가방 + 창고 합산. short 는 합산 부족, fromStorage 는 가방만으론 모자라 창고에서 옮겨야 할 양
function planCalc(r,count){const req=r.perCount*count;const bag=r.unknownOwned?null:(r.owned||0);const sto=r.storage||0;const h=holdCalc(req,bag,sto);
  const have=h.unknown?null:h.total;return {req,have,bag,sto,short:h.short,fromStorage:h.fromStorage,ok:h.short===0,pct:have==null?0:Math.min(100,Math.round(have/Math.max(1,req)*100))};}
const perOfId=(id)=>{const R=(S.dict&&S.dict.recipes)||{};return (R[id]&&R[id].per)||1;};
function openPlan(count){if(!S.sel)return;const d=$("plan");const sel=S.sel;
  const rec=((S.snap&&S.snap[sel.kind])||[]).find((x)=>rid(sel.kind,x)===sel.id)||{};
  P.sel=sel;P.rec=rec;P.count=Math.max(1,Math.min(999,Number(count)||1));P.rows=planRows(rec);P.cards=new Map();P.btnKey=new Map();P.born=Date.now();d.dataset.born=String(P.born);
  const fac=sel.kind==="alter"?facilityOf(sel.name):"";
  const sub=[fac,`1회 ${fmtN(rec.per||1)}개`,sel.variants>1?`경로 ${sel.variant}/${sel.variants}`:""].filter(Boolean).join(" · ");
  d.innerHTML=`<div class="dlg-h"><span class="pt"><span class="eyebrow">레시피 · ${sel.kind==="craft"?"제작":"가공"}</span><span class="pname">${esc(sel.name)}</span><span class="psub">${esc(sub)}</span></span><span class="grow"></span><span class="x" id="pX" title="닫기 (Esc)">×</span></div>
    <div class="dlg-b"><div class="row" style="flex-wrap:nowrap"><span class="lbl">횟수</span><span class="stepper"><button id="pDec" title="−1 (길게 누르면 연속)">−</button><input type="number" id="pCount" class="v" min="1" max="999" value="${P.count}" title="직접 입력"><button id="pInc" title="+1 (길게 누르면 연속)">+</button></span><button class="xs" id="pMaxBtn" disabled title="계산 중…">최대</button><span class="grow"></span><button class="xs pri" id="pQueueRecipe" style="padding:6px 12px;font-size:12px;border-radius:7px"></button></div>
    <div style="display:flex;flex-direction:column;gap:6px"><span class="ph"><span><span id="pSum"></span> <span id="pEff" style="color:var(--gold3);letter-spacing:0"></span></span><span id="pShort"></span></span><div id="pCards" style="display:flex;flex-direction:column;gap:6px"></div></div>
    <div class="row" id="pAllRow" hidden style="flex-wrap:nowrap;gap:8px"><button class="pall" id="pChain" style="flex:1" title="부족 재료를 만드는 선행 제작·가공·채집까지 순서대로 큐에 담습니다 (먼저 미리보기)">필요한 것 전부 담기 <small>· 선행 제작 포함</small></button><button class="xs" id="pQueueAll" title="선행 제작 없이 부족 재료(채집·1단계 제작)만">부족 재료만 <small id="pAllN"></small></button><label class="small-t" style="display:flex;align-items:center;gap:4px;white-space:nowrap;margin:0" title="채집을 ⌈부족/100⌉×100 개로 (회차를 꽉 채움)"><input type="checkbox" id="pMax"><span style="font-size:12px;color:var(--fg2)">최대로</span></label></div>
    <div id="pChainPrev" hidden></div>
    ${sel.kind==="alter"?'<div class="row small-t"><input type="checkbox" id="pWait"><label for="pWait" style="margin:0"><span style="font-size:12px;color:var(--fg2)">완료·수령까지 기다렸다 다음으로</span></label></div>':""}
    ${Q.avail===false?'<div class="small-t muted">큐 기능 준비 중 — 서버가 아직 큐를 지원하지 않습니다.</div>':""}</div>`;
  $("pX").onclick=()=>d.close();
  const setCount=(n)=>{n=Math.min(999,Math.max(1,Math.round(Number(n)||1)));if(n===P.count)return;P.count=n;planUpdate();planFetch();};
  holdBtn($("pDec"),()=>setCount(P.count-1));holdBtn($("pInc"),()=>setCount(P.count+1));
  $("pCount").oninput=()=>{const v=Number($("pCount").value);if(v>=1)setCount(v);};$("pCount").onchange=()=>{$("pCount").value=P.count;};
  $("pQueueRecipe").onclick=()=>queueAdd({type:sel.kind,name:sel.name,recipeId:sel.id,count:P.count,...(sel.kind==="alter"?{collect:($("pWait")&&$("pWait").checked)?"wait":"later"}:{})});   // 담은 뒤에도 닫지 않는다 — 연속 담기
  $("pQueueAll").onclick=async()=>{const add=planAddable();for(const {r,c} of add)await queueAdd(r.source==="gather"?{type:"gather",name:r.name,target:($("pMax")&&$("pMax").checked)?gmax(c.short):c.short}:{type:r.source,name:r.name,recipeId:r.sourceId,count:Math.ceil(c.short/perOfId(r.sourceId)),...(r.source==="alter"?{collect:"later"}:{})},true);toast(`부족 재료 ${add.length}건을 큐에 담았습니다`);};
  $("pMaxBtn").onclick=()=>{if(P.max>0)setCount(P.max);};
  $("pChain").onclick=()=>chainPreview();
  P.max=null;P.maxInfo=null;planBuildCards();planUpdate();planFetch();
  if(!d.open)d.showModal();}
// 「최대」 버튼 상태: 서버 plan.maxSuggested(재료로 가능한 최대 횟수) — 0 이면 비활성 「재료 부족」, 모르면 계산 중
function planMaxUI(){const b=$("pMaxBtn");if(!b)return;const m=P.max,mi=P.maxInfo||{};
  if(m==null){b.disabled=true;b.title="최대 횟수를 아직 계산하지 못했습니다 (서버 갱신 필요)";b.textContent="최대";return;}
  const fac=mi.facilityMax!=null?`시설 상한 ${fmtN(mi.facilityMax)}`:"시설 상한 미확인 — 초과 시 게임이 알려줍니다(invalid_count)";
  b.disabled=!(m>0);b.textContent=m>0?`최대 ${fmtN(m)}`:"재료 부족";b.title=`재료로 가능한 최대 ${fmtN(m)}회 · ${fac}${mi.partial?" · 미확인 재료 있음(보유를 모르는 재료는 제외)":""}`;
  b.style.color=mi.partial&&m>0?"var(--warn)":"";}
// 「필요한 것 전부 담기」: add_chain dryRun 으로 담길 항목을 먼저 보여 주고, 「담기」 확정 때만 실제로 담는다
async function chainPreview(){const sel=P.sel;if(!sel)return;const box=$("pChainPrev");box.hidden=false;box.innerHTML='<div class="small-t muted">담길 항목 계산 중…</div>';
  const mode=($("pMax")&&$("pMax").checked)?"max":"need";
  const r=await api("/api/queue",{op:"add_chain",id:sel.id,name:sel.name,kind:sel.kind,count:P.count,mode,dryRun:true});
  if(!r||!r.ok){box.innerHTML=`<div class="small-t" style="color:var(--bad2)">${esc((r&&(r.message||r.error))||"")||"선행 제작 담기를 아직 지원하지 않는 서버입니다"}</div>`;return;}
  const items=r.added||r.items||(r.plan&&r.plan.items)||[];const calls=r.execCalls!=null?r.execCalls:(r.calls!=null?r.calls:items.reduce((a,x)=>a+(x.type==="gather"?gpass(x.target||0):x.type==="alter"?(x.count||1):1),0));const wings=r.wings!=null?r.wings:(r.wingCalls!=null?r.wingCalls*WINGS_PER_CALL:null);const unres=r.unresolved||[];   // 날개는 wingCalls 에서 온다 — 호출 수로 곱하면 수령까지 센다
  const line=(x,i)=>`<div class="dfr"><span><span class="badge ${QTYPE_CLS[x.type]||"warn"}" style="margin-right:6px">${esc(QTYPE_KO[x.type]||x.type)}</span>${esc(x.name)} ×${fmtN(x.target||x.count||1)}${x.type==="gather"?` (${gpass(x.target||0)}회)`:""}${x.recipeId&&/#([2-9]|\d\d)$/.test(x.recipeId)?` <span class="tag">경로 ${x.recipeId.split("#")[1]}</span>`:""}</span><span class="mono muted">${i+1}</span></div>`;
  box.innerHTML=`<div class="ph"><span>담길 항목 · ${items.length}개 · 순서대로</span><span>호출 약 ${fmtN(calls)}회${wings!=null?` · 정령의 날개 약 ${fmtN(wings)}`:""}</span></div><div style="display:flex;flex-direction:column;gap:4px;margin-top:6px">${items.map(line).join("")||'<span class="small-t muted">담을 것이 없습니다 (이미 충분)</span>'}</div>${unres.length?`<div class="small-t" style="color:var(--bad2);margin-top:6px">구할 방법 없음: ${unres.map((u)=>esc((u.name||u)+(u.short?` ×${fmtN(u.short)}`:""))).join(", ")}${unres.some((u)=>u.reason)?" — "+esc(unres.map((u)=>u.reason).filter(Boolean).join("; ")):""}</div>`:""}<div class="row" style="margin-top:8px;gap:8px;justify-content:flex-end"><button class="xs" id="pChainX">닫기</button><button class="xs pri" id="pChainGo" ${items.length?"":"disabled"}>담기 · ${items.length}항목</button></div>`;
  $("pChainX").onclick=()=>{box.hidden=true;box.innerHTML="";};
  $("pChainGo").onclick=async()=>{$("pChainGo").disabled=true;const r2=await api("/api/queue",{op:"add_chain",id:sel.id,name:sel.name,kind:sel.kind,count:P.count,mode});
    if(!r2||!r2.ok){toast("담지 못했습니다: "+((r2&&(r2.message||r2.error))||""));$("pChainGo").disabled=false;return;}
    const n=(r2.added||r2.items||[]).length||items.length;const c2=r2.execCalls!=null?r2.execCalls:calls;const w2=r2.wings!=null?r2.wings:(r2.wingCalls!=null?r2.wingCalls*WINGS_PER_CALL:null);toast(`큐에 담음: ${n}항목${w2!=null?` · 정령의 날개 약 ${fmtN(w2)}`:""}`);box.hidden=true;box.innerHTML="";await loadQueue();};}
// 스테퍼 길게 누르기: 400ms 뒤부터 200ms 마다 반복
// 주의: 정지 신호를 버튼에만 걸면 안 된다. 큐 카드는 폴링(5초)·수정 응답마다 innerHTML 을 다시 그려
// 누르고 있던 버튼이 사라지고, 그러면 pointerup 이 그 버튼에 영영 오지 않아 setInterval 이 고아로 남는다
// (실측: 손을 뗀 뒤에도 200ms 인터벌이 계속 살아 값이 혼자 움직였다). 그래서 ① 창 전체에서도 멈추고
// ② 버튼이 DOM 에서 빠지면 스스로 멈추고 ③ 포인터를 캡처해 버튼 밖으로 나가도 정확히 끝나게 한다.
const HOLDS=new Set();
function holdStopAll(){for(const s of [...HOLDS])s();}
function holdBtn(el,fn){let t=null,iv=null,pid=null;
  const stop=()=>{clearTimeout(t);clearInterval(iv);t=iv=null;HOLDS.delete(stop);
    if(pid!=null){try{if(el.hasPointerCapture&&el.hasPointerCapture(pid))el.releasePointerCapture(pid);}catch{}pid=null;}
    try{window.dispatchEvent(new Event("mw:holdend"));}catch{}};
  el.addEventListener("pointerdown",(e)=>{if(e.button!==0)return;e.preventDefault();
    stop();   // 같은 버튼의 이전 hold 가 남아 있으면 먼저 정리 (연타로 겹치지 않게)
    pid=e.pointerId;try{el.setPointerCapture(pid);}catch{}
    HOLDS.add(stop);fn();
    t=setTimeout(()=>{iv=setInterval(()=>{if(!el.isConnected){stop();return;}fn();},200);},400);});
  for(const ev of ["pointerup","pointerleave","pointercancel"])el.addEventListener(ev,stop);}
// 버튼이 사라진 뒤에도 확실히 끝나도록 창 수준에서 한 번 더
for(const ev of ["pointerup","pointercancel","blur"])window.addEventListener(ev,holdStopAll);
document.addEventListener("visibilitychange",()=>{if(document.hidden)holdStopAll();});
function planAddable(){const out=[];for(const r of P.rows){const c=planCalc(r,P.count);if(c.short>0&&(r.source==="gather"||r.source==="craft"||r.source==="alter"))out.push({r,c});}return out;}
// 재료 카드 DOM 은 한 번만 만든다 — 이후 횟수가 바뀌면 planUpdate 가 숫자·막대·문구·버튼 라벨만 바꾼다
function planBuildCards(){const wrap=$("pCards");if(!wrap)return;wrap.innerHTML="";P.cards=new Map();P.btnKey=new Map();
  if(!P.rows.length){wrap.innerHTML=`<div class="ing"><span class="small-t muted">${Object.keys(P.rec.known||{}).length?"부족한 재료가 없습니다.":"확인된 재료가 없습니다 — 부족한 재료가 한 번이라도 잡혀야 쌓입니다."}</span></div>`;return;}
  for(const r of P.rows){const el=document.createElement("div");el.className="ing";el.dataset.ing=r.name;
    el.innerHTML=`<div class="r1"><span>${esc(r.name)}</span><span class="q"><span data-have></span><span style="color:var(--sub2)"> / <span data-req></span></span></span></div><div class="small-t" data-hold style="font-size:11px;color:var(--sub2);margin-top:-2px"></div><div class="tb" data-tb><i data-bar></i></div><div class="r2"><span data-sub></span><span data-btns></span></div>`;
    wrap.appendChild(el);P.cards.set(r.name,el);}}
function planUpdate(){const d=$("plan");if(!P.sel||!d.open&&!d.dataset.born)return;const count=P.count;const rec=P.rec||{};
  const pc=$("pCount");if(pc&&document.activeElement!==pc&&Number(pc.value)!==count)pc.value=count;
  const qr=$("pQueueRecipe");if(qr){const t=`${P.sel.kind==="craft"?"제작":"가공"} ${count}회 담기`;if(qr.textContent!==t)qr.textContent=t;}
  let shortN=0;
  for(const r of P.rows){const el=P.cards.get(r.name);if(!el)continue;const c=planCalc(r,count);if(c.short>0)shortN++;const src=r.source;
    const setT=(sel,t)=>{const n=el.querySelector(sel);if(n&&n.textContent!==t)n.textContent=t;};
    // 큰 숫자 = 합산 보유 / 필요, 아래 작은 글씨 「가방 b · 창고 s」. 막대도 합산. 「부족」은 합산 부족일 때만 빨강
    const hv=el.querySelector("[data-have]");setT("[data-have]",c.have==null?"?":fmtN(c.have));hv.style.color=c.ok?"var(--ok)":"var(--bad)";hv.title=c.have==null?"보유를 모릅니다 — 갱신하면 채워집니다":"";setT("[data-req]",fmtN(c.req));
    setT("[data-hold]",c.have==null?"가방 ? · 창고 "+fmtN(c.sto):`가방 ${fmtN(c.bag)} · 창고 ${fmtN(c.sto)}`);
    const tb=el.querySelector("[data-tb]");tb.hidden=c.have==null;const bar=el.querySelector("[data-bar]");bar.style.width=c.pct+"%";bar.classList.toggle("ok",c.ok);
    el.classList.toggle("unknown",!c.ok&&!src);
    let sub,key;
    if(c.ok){sub="충분";key=`ok:${c.fromStorage}`;}
    else if(src==="gather"){sub=`부족 ${fmtN(c.short)} · 채집 · 예상 ${gpass(c.short)}회`;key=`gather:${c.short}`;}
    else if(src==="craft"||src==="alter"){const per=perOfId(r.sourceId);sub=`부족 ${fmtN(c.short)} · ${src==="craft"?"제작":"가공"} · 1회 ${per}개`;key=`${src}:${Math.ceil(c.short/per)}:${r.sourceId}`;}
    else{sub="채집·제작 경로가 아직 관찰되지 않았습니다";key="none";}
    const subEl=el.querySelector("[data-sub]");if(subEl.textContent!==sub){subEl.textContent=sub;subEl.style.color=c.ok?"var(--sub2)":"";}
    // 버튼·이송 배지는 라벨·target 이 바뀔 때만 다시 만든다 (그 외엔 손대지 않아 hover·포커스가 유지된다). 이송은 버튼이 아니라 안내
    if(P.btnKey.get(r.name)!==key){P.btnKey.set(r.name,key);const b=el.querySelector("[data-btns]");
      b.innerHTML=c.ok?moveBadge(c.fromStorage):src==="gather"?gatherBtns(r.name,c.short):(src==="craft"||src==="alter")?`<button class="pbtn" data-qa="${src}" data-n="${esc(r.name)}" data-id="${esc(r.sourceId||"")}" data-c="${Math.ceil(c.short/perOfId(r.sourceId))}">${src==="craft"?"제작":"가공"} ${Math.ceil(c.short/perOfId(r.sourceId))}회 담기</button>`:'<span class="pbtn dim" title="합산으로도 부족한데 채집 목록에도, 관찰된 레시피 산출에도 없는 재료">구할 방법 미확인</span>';
      bindQA(b);}}
  const enough=shortN===0;d.classList.toggle("enough",enough);
  const ps=$("pSum");if(ps){const t=`재료 · ${count}회 기준 · 산출 ${fmtN((rec.per||1)*count)}개`;if(ps.textContent!==t)ps.textContent=t;}
  // 날개 효율: 제작은 호출 1회에 count 회를 한꺼번에(craftCount), 가공은 등록 1건 = 호출 1회 — 최대로 담을 이유가 보이게
  const pe=$("pEff");if(pe){const per=rec.per||1;const t=P.sel.kind==="craft"?`· 호출 1회 · 날개 ${WINGS_PER_CALL} → ${fmtN(per*count)}개`:`· 호출 ${count}회 · 날개 ${WINGS_PER_CALL*count} → ${fmtN(per*count)}개`;if(pe.textContent!==t)pe.textContent=t;}
  planMaxUI();
  const sh=$("pShort");if(sh){const t=enough?"충분":`부족 ${shortN}`;if(sh.textContent!==t)sh.textContent=t;sh.style.color=enough?"var(--ok)":"var(--bad)";}
  const add=planAddable();const ar=$("pAllRow");if(ar){ar.hidden=!add.length;const an=$("pAllN");if(an){const t=`· ${add.length}항목`;if(an.textContent!==t)an.textContent=t;}}}
// 서버 계획(source/sourceId·창고 보강)은 250ms 디바운스로 한 번만 — 값이 같으면 DOM 을 건드리지 않는다
function planFetch(){clearTimeout(P.fetchT);const sel=P.sel,count=P.count;
  P.fetchT=setTimeout(async()=>{const r=await api("/api/plan",{id:sel.id,name:sel.name,kind:sel.kind,count});if(P.sel!==sel||!r||!r.ok||!r.plan)return;
    let changed=false,rebuilt=false;
    // 「최대」: 서버가 재료로 가능한 최대 횟수를 주면 버튼을 켠다 (옛 서버는 필드가 없어 계산 중 상태로 남는다)
    if(r.plan.maxSuggested!=null){const m=Number(r.plan.maxSuggested)||0;const mi={partial:!!r.plan.maxByStockPartial,facilityMax:r.plan.facilityMax!=null?r.plan.facilityMax:(r.plan.maxCount!=null?r.plan.maxCount:null)};if(m!==P.max||JSON.stringify(mi)!==JSON.stringify(P.maxInfo)){P.max=m;P.maxInfo=mi;planMaxUI();}}
    for(const n of r.plan.need||[]){let row=P.rows.find((x)=>x.name===n.name);
      if(!row){row={name:n.name,perCount:Math.max(1,Math.round(n.required/Math.max(1,r.plan.count||count))),owned:n.owned,unknownOwned:n.owned==null,storage:n.storage!=null?n.storage:storeOf(n.name),source:n.source||null,sourceId:n.sourceId||null};P.rows.push(row);rebuilt=true;continue;}
      if((n.source||null)!==row.source||(n.sourceId||null)!==row.sourceId){row.source=n.source||null;row.sourceId=n.sourceId||null;changed=true;}
      if(n.storage!=null&&n.storage!==row.storage){row.storage=n.storage;changed=true;}
      if(row.unknownOwned&&n.owned!=null){row.owned=n.owned;row.unknownOwned=false;changed=true;}}
    if(rebuilt){planBuildCards();planUpdate();}else if(changed)planUpdate();},250);}
// 스냅샷이 바뀌면(sync·폴링) 보유·창고만 다시 읽어 갱신 — 팝업은 그대로
function planRefresh(){if(!P.sel||!$("plan").open)return;for(const r of P.rows){const bag=bagOf(r.name);if(bag!=null){r.owned=bag;r.unknownOwned=false;}r.storage=storeOf(r.name);}planUpdate();planFetch();}
{const d=$("plan");
  d.addEventListener("close",()=>{P.sel=null;if(S.sel){S.sel=null;renderTab();}});   // ×·Esc·backdrop 어느 쪽이든 선택 해제
  d.addEventListener("click",(e)=>{if(e.target===d)d.close();});}   // backdrop 클릭 = 닫기 (내용 밖)

Object.assign(window.MW,{P,planSource,planRows,planCalc,perOfId,openPlan,planMaxUI,chainPreview,holdBtn,HOLDS,holdStopAll,planAddable,planBuildCards,planUpdate,planFetch,planRefresh});
