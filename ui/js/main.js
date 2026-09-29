// main.js — 셸: 탭 라우팅(큐·가공·재고·사전·오버레이), 스냅샷 적용·갱신(sync)·폴링, 큐 보드 갱신, 시작 순서
/* ── 탭 ──
   헤더 탭은 5개. 제작·가공·채집 목록은 담기 서랍으로 옮겨가 더 이상 탭이 아니다.
   재고와 사전은 기존 목록 카드(#tabCard) 하나를 같이 쓰므로, 켜진 탭 안으로 카드를 옮긴다.
   탭 강조는 맨 마지막에 한 번 더 칠한다 — core.js 의 selectTab 이 자기 기준(data-tab)으로 다시 칠하기 때문이다. */
const MWTABS=["queue","works","stock","dict","overlay","settings"];
const MWSEC={queue:"tabQueue",works:"tabWorks",stock:"tabStock",dict:"tabDict",overlay:"tabOverlay",settings:"tabSettings"};
let mwTab="queue";
function markTabs(name){
  // 한 줄 주석을 코드와 같은 줄에 붙이지 않는다 — 과거에 주석이 이 토글을 통째로 삼켜 탭이 죽은 적이 있다
  document.querySelectorAll("header .tabs button").forEach((b)=>b.classList.toggle("on",b.dataset.tab===name));
  // 헤더 아래 페이지 탭 줄(좁은 폭, #mtabs)도 같이 칠한다. 두 줄이 같은 name 을 보므로 어긋날 자리가 없다
  document.querySelectorAll("#mtabs button").forEach((b)=>b.classList.toggle("on",b.dataset.tab===name));
  // 지금 탭을 body 에 적는다 — 실행 줄(#runbar)은 main 밖에 있어 섹션 hidden 으로는 못 가린다.
  // 실행 줄은 **큐 탭에서만** (좁은 폭). mobile.css 가 body[data-tab] 을 보고 숨긴다
  document.body.dataset.tab=name;
}
function switchTab(name,push){
  if(!MWTABS.includes(name))name="queue";
  // 어느 줄에도 버튼이 없는 탭은 주소(#overlay)로도 열지 않는다 — 아직 비어 있는 화면이라 버튼을 뺀 것이므로,
  // 옛 북마크로 들어와 빈 화면을 보는 일이 없게 한다. 버튼이 돌아오면 이 판정도 저절로 풀린다.
  // **헤더 아래 페이지 탭 줄(#mtabs)도 센다.** 안 세면 폰에서만 있는 탭이 생겼을 때 그리로 못 간다.
  if(!document.querySelector(`header .tabs button[data-tab="${name}"], #mtabs button[data-tab="${name}"]`))name="queue";
  mwTab=name;
  if(name==="stock"||name==="dict"){
    const card=$("tabCard"),host=$(MWSEC[name]);
    if(card&&host&&card.parentElement!==host)host.appendChild(card);
  }
  for(const k of MWTABS){const s=$(MWSEC[k]);if(s)s.hidden=(k!==name);}
  if(push){try{history.replaceState(null,"","#"+name);}catch{}}
  try{
    if(name==="stock"||name==="dict"){
      if(typeof selectTab==="function"&&S.tab!==name)selectTab(name,false);
      else if(typeof renderTab==="function")renderTab();
    }
    else if(name==="works"){if(typeof renderWorks==="function")renderWorks();}
    else if(name==="overlay"){if(typeof renderOverlayTab==="function")renderOverlayTab();}
    else if(name==="settings"){if(typeof loadSettings==="function")loadSettings();}
    else if(name==="queue"){mwRefreshQueue();}
  }catch(e){console.error("switchTab",name,e);}
  markTabs(name);
  return name;
}
document.querySelectorAll("header .tabs button, #mtabs button").forEach((b)=>{b.onclick=()=>switchTab(b.dataset.tab,true);});
/* 페이지 탭 줄(#mtabs)은 **좁은 폭에서만** 보인다 (mobile.css). hidden 을 벗겨 두고 보이고 안 보이고는 CSS 가 정한다 —
   폭에 따라 JS 가 붙였다 뗐다 하면 창 크기를 바꿀 때마다 눌린 자리가 사라진다. */
{const mt=$("mtabs");if(mt)mt.hidden=false;}
/* 해시가 바뀌면 탭도 따라간다 — 뒤로가기·붙여넣은 #dict 링크·업데이트 뒤 되돌아오기는 페이지를 다시 읽지 않아
   시작 코드가 돌지 않는다. core.js 의 hashchange 가 먼저 돌아 stock/dict 를 selectTab 으로 처리하므로,
   여기서 다시 한 번 섹션 표시와 탭 강조를 맞춘다. */
window.addEventListener("hashchange",()=>{const t=(location.hash||"").slice(1).split("?")[0];
  if(MWTABS.includes(t))switchTab(t,false);});

/* ── 큐 보드 ──
   상태를 바꾸는 모든 호출은 끝나고 MW.refreshQueue() 를 부른다.
   board.js 가 자기 폴링(scheduleQueuePoll)과 함께 loadQueue 를 들고 있으면 그쪽에 맡긴다 —
   폴링 루프가 두 개 돌면 실행 중 1초 간격이 두 배가 된다. 보드가 없을 때만 아래 자체 경로로 내려간다.
   전역 이름을 refreshQueue 로 쓰지 않는 이유: board.js 가 같은 이름을 const 로 선언해 두어
   같은 전역 스코프에서 재선언 오류(SyntaxError)가 나고 main.js 가 통째로 죽는다. */
const QEMPTY={items:[],running:false,current:null,currentGroup:null,lastError:null,stopReason:null,onError:"continue",columns:{wait:0,run:0,done:0,fail:0},events:[]};
let qPollT=null,qBusy=false;
function scheduleBoardPoll(running){clearTimeout(qPollT);qPollT=setTimeout(mwFetchQueue,running?1000:5000);}
async function mwFetchQueue(){
  if(qBusy)return null;
  qBusy=true;let d=null;
  try{
    d=await api("/api/queue");
    // 서버가 500 이거나 큐를 아직 지원하지 않아도(404) 화면이 하얗게 비면 안 된다 — 빈 보드를 그린다
    const state=(d&&Array.isArray(d.items))?d:Object.assign({},QEMPTY,{unavailable:true,error:(d&&(d.error||""))||"",message:(d&&(d.message||""))||""});
    if(typeof renderBoard==="function")renderBoard(state);
  }catch(e){console.error("refreshQueue",e);}
  finally{qBusy=false;}
  scheduleBoardPoll(!!(d&&d.running));
  return d;
}
async function mwRefreshQueue(){
  if(typeof loadQueue==="function")return loadQueue();
  return mwFetchQueue();
}

/* ── 스냅샷 적용 ── */
function applySnap(s){if(!s)return;S.snap=s;S.fetchedAt=Date.now();
  const fa=s.fetchedAt||{};const empty=!Object.values(fa).some(Boolean);$("emptyCard").hidden=!empty;
  const wa=$("wkAt");if(wa)wa.textContent=fa.works?"갱신 "+ago(fa.works):"";
  if(typeof renderWorks==="function")renderWorks();
  // 목록 카드는 재고·사전 탭에서만 보인다 — 다른 탭에서 1,800행을 다시 그릴 이유가 없다
  if((mwTab==="stock"||mwTab==="dict")&&typeof renderTab==="function")renderTab();
  if(typeof planRefresh==="function")planRefresh();
  if(typeof loadDict==="function")loadDict();}
/* 스냅샷을 한 번도 못 받았는데 응답이 이상하면(500·null·배열 등) 목록이 통째로 빈 화면이 된다 — 이유를 남긴다.
   이미 그린 스냅샷이 있으면 건드리지 않는다(폴링 한 번 실패로 화면을 지우지 않게). */
function workFailed(d){if(S.snap)return;const why=(d&&(d.message||d.error))||"응답을 읽지 못했습니다";
  $("tabBody").innerHTML=`<div class="empty">목록을 불러오지 못했습니다 · ${esc(why)}<br><button class="small" id="wkRetry" style="margin-top:8px">다시 시도</button></div>`;
  const b=$("wkRetry");if(b)b.onclick=()=>{$("tabBody").innerHTML='<div class="empty">불러오는 중…</div>';loadWork();};}
async function loadWork(){const d=await api("/api/work");if(d&&d.craft)applySnap(d);else{workFailed(d);if(typeof loadDict==="function")loadDict();}}
/* **글자를 써넣지 않는다** — 갱신은 아이콘 버튼이다. `textContent` 를 쓰면 안에 든
   `<svg>` 가 지워져 아이콘이 사라진다. 도는 중은 `.busy` 로 보여 준다. */
/* **폰(밖)은 게임을 읽지 않는다** — PC 가 읽은 값만 본다.
   `/api/sync` 는 PC 의 CLI 를 돌리는 길이라 폰에서는 **아무 때도** 안 부른다 — 시작할 때(auto_sync)도, 주기 폴링도,
   머리줄 갱신 단추도. 대신 PC 가 이미 읽어 둔 것(`/api/work` 캐시 · `/api/state?nocli=1` · 큐)을 다시 받는다.
   서버도 같은 규칙을 지킨다. */
async function pull(bg){if(S.syncing)return;S.syncing=true;const _b=$("btnSync");
  _b.disabled=true;if(!bg){_b.classList.add("busy");_b.title="갱신 중…";}
  try{await loadWork();await refreshState();if(!bg)toast("PC 가 읽어 둔 값으로 갱신했습니다");}
  finally{S.syncing=false;_b.disabled=false;_b.classList.remove("busy");_b.title="갱신";mwRefreshQueue();}}
async function sync(bg){if(NET.REMOTE)return pull(bg);   // 폰: PC 가 읽어 둔 값만 (위)
  if(S.syncing)return;S.syncing=true;const _b=$("btnSync");
  _b.disabled=true;if(!bg){_b.classList.add("busy");_b.title="갱신 중…";}
  try{const r=await api("/api/sync",{});if(r&&r.craft)applySnap(r);
    if(r&&r.error==="busy"){if(!bg)toast("큐가 실행 중입니다 — 끝나거나 정지한 뒤 갱신하세요");}
    else if(!r.ok){const bad=(r.steps||[]).find((x)=>!x.ok)||{};if(!bg)toast("갱신 실패: "+(bad.error||r.error||"")+" "+(bad.message||r.message||""));}
    else if(!bg)toast("갱신했습니다");}
  finally{S.syncing=false;_b.disabled=false;_b.classList.remove("busy");_b.title="갱신";mwRefreshQueue();}}
$("btnSync").onclick=()=>sync(false);
let pollT=null;
function schedulePoll(){clearInterval(pollT);const sec=Math.min(600,Math.max(10,Number(S.settings.work_poll_sec)||30));
  // 폰: 같은 주기로 PC 가 읽어 둔 값만 다시 받는다 (CLI 안 탐). 화면이 안 보이면 쉰다 — 터널 너머 요청을 아낀다
  if(NET.REMOTE){pollT=setInterval(()=>{if(!document.hidden&&!S.syncing)pull(true);},sec*1000);return;}
  pollT=setInterval(()=>{if(S.cli&&S.cli.pipe==="connected"&&!S.syncing)sync(true);},sec*1000);}

/* ── 시작 ── */
askBind();   // 물어보는 창(브라우저 기본 창 대신)을 묶는다
(async()=>{const h=await api("/api/health");if(h&&h.version)$("ver").textContent=`v${h.version}${h.lite?" · 경량판":""}`;S.demo=!!(h&&h.cli==="demo");{const sr=$("sShortRow");if(sr)sr.hidden=!(h&&h.embed&&NET.CAN.files);}if(S.demo)$("demoBadge").hidden=false;
  const d=await api("/api/settings?nocli=1");S.settings=(d&&d.settings)||{};if(MW.paintOvBtn)MW.paintOvBtn();   // 머리줄 오버레이 단추 — 흐린 자리에서 지금 켜짐으로
  if(window.MWTheme)MWTheme.fromSettings(S.settings);   // 테마 = 설정 ui_theme (이 PC 는 서버가 이미 박아 냈다 · 폰은 PC 값을 따른다 — theme.js)
  // #dict 처럼 주소로 탭 지정, #stock?q=검색어 로 검색까지 (스크린샷·링크·헤드리스 검증용)
  const [hs,hq]=(location.hash||"").slice(1).split("?");
  switchTab(MWTABS.includes(hs)?hs:"queue",false);
  const qm=/(?:^|&)q=([^&]*)/.exec(hq||"");if(qm){try{S.q=decodeURIComponent(qm[1]).trim();$("q").value=S.q;$("qClearBtn").hidden=!S.q;}catch{}}
  // ?band=done,run 은 가공 대기열 밴드를 그 분류만 켠 채로 (저장하지 않음 — 헤드리스 검증용)
  const bm=/(?:^|&)band=([^&]*)/.exec(hq||"");if(bm&&typeof BAND_KEYS!=="undefined"){const ks=bm[1].split(",").filter((k)=>BAND_KEYS.includes(k));if(ks.length)S.band=new Set(ks);}
  await loadWork();
  // #dict?dq=<레시피 id> 는 그 행을 강조(S.dfocus)한 채로, #dict?ds=ings 는 「재료 → 쓰이는 곳」 소탭으로.
  // 옛 사전의 「담기」 폼(S.dform)은 사전 탭을 다시 지으면서 없어졌다 — 담기는 행 오른쪽 버튼 하나다 (tab-dict.js)
  const dqm=/(?:^|&)dq=([^&]*)/.exec(hq||"");if(dqm){let k="";try{k=decodeURIComponent(dqm[1]);}catch{}if(k){S.dsub="recipes";S.dfocus=k;}}
  if(/(?:^|&)ds=ings/.test(hq||""))S.dsub="ings";
  // ?ev=1 은 회신 기록을 펼친 채로 (헤드리스 검증용 — 클릭 없이 보이게)
  if(/(?:^|&)ev=1/.test(hq||"")&&typeof Q!=="undefined")Q.evOpen=true;
  await mwRefreshQueue();
  if(/(?:^|&)confirm=1/.test(hq||"")&&typeof openConfirm==="function")openConfirm();
  if(/(?:^|&)settings=1/.test(hq||""))switchTab("settings",true);
  if(S.settings.update_check&&S.settings.update_url){setTimeout(()=>checkUpdate(true),1500);}
  try{await refreshState();}catch(e){console.error("refreshState",e);}
  // 시작할 때 자동 갱신은 **이 PC 에서만** — 폰은 방금 받은 캐시가 곧 PC 가 읽은 값이다 (다시 받을 것도, 읽힐 게임도 없다)
  if(S.settings.auto_sync&&!NET.REMOTE&&S.cli&&S.cli.pipe==="connected")sync(true);
  schedulePoll();
  setInterval(()=>{try{refreshState();}catch(e){console.error("refreshState",e);}},15000);   // 게임이 꺼져 있으면 probe 가 5초 걸리므로 백그라운드로만
  {const sm=/(?:^|&)scroll=(\d+)/.exec(hq||"");if(sm)setTimeout(()=>{const b=$("tabBody");if(b)b.scrollTop=Number(sm[1]);},300);}
  if(/(?:^|&)dump=1/.test(hq||"")){layoutDump();setTimeout(layoutDump,1200);}   // ?dump=1: 페이지 스크롤 여부 확인용
})();
/* ── 연결 끊기 (폰에서만) ── 폴리오 머리줄의 `btnOut` 과 **같은 자리·같은 말·같은 일**.
   이 기기가 들고 있던 쪽지·기기 열쇠·PC 주소를 지우고(폴리오가 빌려 간 것까지)
   PC 쪽 쪽지도 끝낸 뒤 잇기 화면으로. 이 PC 에서는 숨긴다 — 끊을 연결이 없다. */
$("btnOut").hidden=!NET.REMOTE;
$("btnOut").onclick=async()=>{
  if(!await askOk("이 기기에 저장된 연결을 지웁니다. 다시 쓰려면 PC 에서 코드를 새로 받아야 합니다.",{title:"연결 끊기",ok:"끊기",danger:true}))return;
  await NET.disconnect();
};
/* ── 폰에서 앱처럼 (PWA) ──
   서비스 워커는 **설치를 가능하게 하고 껍데기를 남기는 것**이 전부다. 캐시를 먼저 보지 않으므로
   새 판을 빌드해도 옛 화면이 뜨지 않는다 (ui/sw.js 주석 참고).
   등록이 실패해도 앱은 그대로 돈다 — 조용히 넘어간다. */
if("serviceWorker" in navigator&&location.protocol!=="file:"){
  window.addEventListener("load",()=>{navigator.serviceWorker.register("sw.js").catch(()=>{});});
}

Object.assign(window.MW,{MWTABS,MWSEC,switchTab,refreshQueue:mwRefreshQueue,mwRefreshQueue,mwFetchQueue,scheduleBoardPoll,applySnap,loadWork,workFailed,sync,pull,schedulePoll});
