// core.js — 공통: $, api(), TOKEN, hold/bye, 서버 끊김 배너, 상태 S, toast/esc/fmt, 연결 상태, 해시 라우터·selectTab, 검색창 입력, lsGet/lsSet, 보유 계산(holdCalc)
window.MW=window.MW||{};   // 파일 간 공유 네임스페이스 (전역도 그대로 남아 있다 — 일반 스크립트를 순서대로 로드)
// TOKEN 은 index.html 의 인라인 <script> 에서 온다 — 서버가 index.html 을 낼 때만 __MOBIWORKS_TOKEN__ 을 치환하므로 정적 js 파일에 둘 수 없다
// 앱 이름은 **index.html 의 <title> 하나**가 기준이다. 여기서 붙잡아 두고 창 제목을 다시 쓸 때 쓴다.
// 이름을 js 에 또 적으면 안 된다 — 서버가 앱 창을 **제목으로** 찾으므로(server.APP_TITLE) 둘이
// 어긋나면 설정 버튼이 누를 때마다 새 창을 띄운다.
const APP_TITLE=document.title;
const $=(id)=>document.getElementById(id);
const esc=(s)=>String(s==null?"":s).replace(/[&<>"']/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let toastT=null;
function toast(msg){const t=$("toast");t.textContent=msg;t.hidden=false;try{t.showPopover();}catch{}clearTimeout(toastT);toastT=setTimeout(()=>{try{t.hidePopover();}catch{}t.hidden=true;},3000);}   // popover: dialog(계획·설정·확인창) 위에도 뜬다

/* 「창이 닫혔다」·「창이 살아 있다」 신호는 **이 PC 에서만** 보낸다.
   밖에서 보내면 PC 앱이 꺼진다 — 서버도
   REMOTE_NEVER 로 막지만 화면에서 아예 안 보내는 것이 맞다 (NET.CAN.hold). */
if(NET.CAN.hold){
  // 경량판: 창을 닫으면 백엔드도 끝나게
  window.addEventListener("pagehide",()=>{try{navigator.sendBeacon("/api/bye",new Blob([JSON.stringify({token:TOKEN})],{type:"application/json"}));}catch{}});
  // 창이 살아 있는 동안 열어 두는 연결 (끊기면 서버가 창이 닫힌 것으로 본다)
  (async function hold(){for(;;){try{const r=await fetch("/api/hold",{headers:NET.headers(false),cache:"no-store"});const rd=r.body.getReader();while(!(await rd.read()).done){}}catch{}await new Promise((res)=>setTimeout(res,1000));}})();
}

/* 부르기는 전부 여기를 지난다 — 이 PC 에서는 같은 주소로, 밖에서는 우편함에서 받아 둔
   PC 주소로 간다(net.js). 쪽지가 만료되면 기기 열쇠로 조용히 한 번 다시 들어간다. */
const api=async(path,body)=>{
  const shot=async()=>{const r=await fetch(NET.url(path),{method:body?"POST":"GET",headers:NET.headers(true),
    body:body?JSON.stringify(body):undefined,...(NET.REMOTE?{mode:"cors"}:{})});
    const j=await r.json();return {r,j};};
  // 밖인데 아직 안 이어졌으면(잇기 화면) **부르지 않는다** — 주소가 없어 사이트 제 주소(/api/…)로 나가 404 만 쌓였다
  if(NET.blocked)return {ok:false,error:"unpaired",message:"아직 PC 와 이어지지 않았습니다."};
  try{
    let {r,j}=await shot();
    if(NET.REMOTE&&(r.status===401||r.status===403)&&await NET.relogin())({r,j}=await shot());
    netOk();
    if(j&&typeof j==="object"&&!("ok"in j)&&r.ok)return j;
    if(!r.ok&&j&&typeof j==="object"&&!("ok"in j))j.ok=false;
    return j;
  }catch(e){netFail();return {ok:false,error:"network",message:String(e.message||e)};}};
// 앱 서버 자체에 못 붙는 상태(Failed to fetch)는 「게임 꺼짐」이 아니다 — 종료된 서버의 창이 남은 경우(실제 발생). 연속 3회면 배너.
let netFails=0,serverGone=false,healthT=null;
/* ── 물어보기 ──────────────────────────────────────────
   `confirm()` · `prompt()` · `alert()` 를 **안 쓴다**.
   기본 창은 우리 창이 아니다 — 제목에 「127.0.0.1:53338의 메시지」가 박히고,
   색도 글꿴도 다르고, 폰에서는 화면 꼭대기에 붙어 어느 앱이 물었는지조차 흐려진다.
   게다가 떠 있는 동안 화면이 통째로 멈춘다(동기).

   다른 창(그룹 설정 등) 위에서도 뜼다 — `showModal` 은 겹쳐도 된다. */
let askDone=null;
function askEnd(v){const d=$("ask");if(!d)return;const f=askDone;askDone=null;
  try{d.close();}catch{}
  if(f)f(v);}
function ask(o){o=o||{};const d=$("ask");
  // 창을 못 띄우면 **묻지 않은 것으로** 친다 — 조용히 「예」로 가면 안 된다
  if(!d)return Promise.resolve(o.input?null:false);
  askEnd(o.input?null:false);                       // 앞서 열린 물음이 있으면 취소로 닫는다
  $("askT").textContent=o.title||"확인";
  $("askMsg").innerHTML=o.html||esc(o.message||"");
  $("askNote").textContent=o.note||"";
  const yes=$("askYes"),no=$("askNo");
  yes.textContent=o.ok||"확인";
  // `cancel:""` 면 **알림**이다 — 고를 것이 없는데 「취소」를 두면 뭐가 달라지나 싶어진다
  no.hidden=o.cancel==="";no.textContent=o.cancel||"취소";
  yes.classList.toggle("danger",!!o.danger);
  const wrap=$("askInWrap"),inp=$("askIn");
  wrap.hidden=!o.input;
  if(o.input){$("askLbl").textContent=o.label||"";inp.value=o.value||"";inp.maxLength=o.maxLength||120;inp.placeholder=o.placeholder||"";}
  return new Promise((res)=>{askDone=res;
    try{d.showModal();}catch{res(o.input?null:false);return;}
    if(o.input){inp.focus();inp.select();}else yes.focus();});}
function askBind(){const d=$("ask");if(!d)return;
  const val=()=>{const v=$("askIn").value.trim();return v||null;};
  const ok=()=>askEnd($("askInWrap").hidden?true:val());
  $("askYes").onclick=ok;
  $("askNo").onclick=()=>askEnd($("askInWrap").hidden?false:null);
  $("askX").onclick=()=>askEnd($("askInWrap").hidden?false:null);
  // Esc 로 닫는 것도 **아니오**다 — 브라우저가 제스스로 닫아도 약속을 끝낸다
  d.addEventListener("close",()=>{if(askDone)askEnd($("askInWrap").hidden?false:null);});
  $("askIn").addEventListener("keydown",(e)=>{if(e.key==="Enter"){e.preventDefault();ok();}});}
/** 「할까요?」 — 예/아니오 */
const askOk=(message,o)=>ask({message,...(o||{})});
/** 「이름을 적어 주세요」 — 문자열 또는 null */
const askText=(label,value,o)=>ask({input:true,label,value,...(o||{})});

function netFail(){netFails++;if(netFails>=3&&!serverGone){serverGone=true;$("srvBar").hidden=false;if(typeof renderConn==="function")renderConn();
  if(!healthT)healthT=setInterval(()=>{fetch(NET.url("/api/health"),{cache:"no-store"}).then((r)=>{if(r.ok)netOk();}).catch(()=>{});},5000);}}   // 서버가 돌아오면 배너 제거
function netOk(){netFails=0;if(serverGone){serverGone=false;$("srvBar").hidden=true;$("srvHint").textContent="";$("srvPhoneHint").textContent="";if(healthT){clearInterval(healthT);healthT=null;}if(typeof renderConn==="function")renderConn();}}
$("srvClose").onclick=()=>{window.close();setTimeout(()=>{if(!window.closed)$("srvHint").textContent="(창이 닫히지 않으면 직접 닫아 주세요)";},400);};
/* 배너는 **어디서 보느냐**에 따라 말이 다르다 (PC exe 를 다시 켠 뒤 폰이 「이 창의 앱 서버가
   종료되었습니다 … MobiWorksLite.exe 를 다시 실행하세요 [이 창 닫기]」를 보였다 — 폰에는 닫을 창도, 다시 켤 exe 도 없다).
   폰에서 이 배너가 뜨는 까닭은 둘뿐이다: PC 앱이 꺼졌거나, exe 를 다시 켜서 **터널 주소가 바뀌었다**(Quick Tunnel 은 켤 때마다
   새 주소). 옛 주소는 영원히 안 살아나므로 「다시 시도」로 안 되면 「연결 정보 지우고 처음으로」 — 이 기기에 남은 **우리 것만**
   지우고(브라우저 전체 삭제가 아니라) 잇기 화면으로 간다 (net.js `wipe`). */
if(NET.REMOTE){
  $("srvPc").hidden=true;$("srvClose").hidden=true;
  $("srvPhone").hidden=false;$("srvRetry").hidden=false;$("srvReset").hidden=false;
  $("srvRetry").onclick=async()=>{const b=$("srvRetry");b.disabled=true;$("srvPhoneHint").textContent="(다시 잇는 중…)";
    try{await NET.relogin();const c=await refreshState();if(!serverGone||c)return;
      $("srvPhoneHint").textContent="(PC 에 닿지 않습니다 — 주소가 바뀌었으면 아래로 처음부터)";}
    finally{b.disabled=false;}};
  $("srvReset").onclick=async()=>{
    if(!await askOk("이 기기에 저장된 두 앱(작업·연주)의 연결 정보와 화면 설정만 지우고 잇기 화면으로 갑니다. 브라우저의 다른 자료는 건드리지 않습니다.",{title:"연결 정보 지우고 처음으로",ok:"지우고 처음으로",danger:true}))return;
    await NET.wipe();};
}

/* ── 상태 ── */
const S={snap:null,tab:"craft",q:"",sel:null,syncing:false,settings:{},cli:null,doneKeys:new Set(),fetchedAt:0,
  dict:null,open:new Set(),dsub:"recipes",dfocus:null,limit:{}};   // dict: /api/recipes 관찰 DB, open: 펼친 행(id), dsub: 사전 소탭, dfocus: 점프한 항목, limit: 탭별 표시 상한(실데이터 1,800행)
const PAGE=200;
const WINGS_PER_CALL=5;   // 카탈로그 원문: 실행 명령은 호출당 정령의 날개 5개 (서버 workqueue.WINGS_PER_CALL 과 같은 값)
// 레시피 식별자: 동명 경로가 있어 이름만으로는 모자란다. 옛 백엔드(id 없음)면 kind:name 으로
const rid=(kind,r)=>r.id||(kind+":"+r.name);
/* 가방 — 게임이 보유로 세는 것. **아이템 목록을 한 번이라도 받았으면**(fetchedAt.items, 옛 백엔드면 목록이 비어 있지 않음)
   목록에 없는 이름은 「모름」이 아니라 **0개**다 — get_items 는 가진 것만 준다. 그걸 null 로 두면 한 번도 안 가진 재료가
   영원히 「?」로 남고, holdCalc 가 unknown 이 되어 **창고에 있는 양까지 버려진다**.
   목록을 아직 한 번도 못 받았을 때만 null(「?」)이다. */
const stockKnown=()=>{const s=S.snap;if(!s)return false;if(s.fetchedAt&&s.fetchedAt.items)return true;return !!(s.stock&&typeof s.stock==="object"&&(Object.keys(s.stock).length||Object.keys(s.storage||{}).length));};
const bagOf=(n)=>{const st=(S.snap&&S.snap.stock)||{};return st[n]!=null?st[n]:(stockKnown()?0:null);};
const storeOf=(n)=>{const st=(S.snap&&S.snap.storage)||{};return st[n]||0;};                  // 창고 합계 — 옮겨야 쓸 수 있다
// 보유는 가방 + 창고 합산으로 보여 준다. 가방을 모르면(「?」) 합산도 「?」
const holdTxt=(bag,sto)=>bag==null?"보유 ?":`보유 ${fmtN(bag+(sto||0))}${sto?` (가방 ${fmtN(bag)} · 창고 ${fmtN(sto)})`:""}`;
// 재료 판정(합산): 필요 need 에 대해 가방 bag·창고 sto → {total, short(합산 부족), fromStorage(가방만으론 모자라 창고에서 옮길 양)}
function holdCalc(need,bag,sto){sto=sto||0;if(bag==null)return {total:null,short:0,fromStorage:0,unknown:true};const total=bag+sto;return {total,short:Math.max(0,need-total),fromStorage:(bag<need&&total>=need)?need-bag:0,unknown:false};}
const moveBadge=(n)=>n>0?`<span class="badge warn" title="게임은 가방만 셉니다 — 창고에서 옮겨야 제작에 쓸 수 있습니다">창고에서 ${fmtN(n)}개 이송</span>`:"";
const wingsHave=()=>{const c=((S.snap&&S.snap.currencies)||[]).find((x)=>x.name==="정령의 날개");return c&&typeof c.amount==="number"?c.amount:null;};
function limitOf(key,total){const l=S.limit[key]||PAGE;return S.q?total:Math.min(l,total);}
function moreBtn(key,shown,total){return shown<total?`<div class="lc-more"><button class="small" data-more="${esc(key)}">더 보기 (${shown} / ${total})</button></div>`:"";}
const REASON_KO={insufficient_living_skill_level:"생활 스킬 레벨 부족",insufficient_facility_level:"시설 레벨 부족",insufficient_decor_score:"장식 점수 부족",not_enough_ingredient:"재료 부족",ingredient_locked:"재료가 잠김",insufficient_transfer_cost:"이동 비용 부족"};
const TABS=["craft","alter","gather","stock","dict"];
const fmtT=(s)=>{s=Math.max(0,Math.floor(s));return `${String(Math.floor(s/60)).padStart(2,"0")}:${String(s%60).padStart(2,"0")}`;};
const fmtN=(n)=>typeof n==="number"?n.toLocaleString("ko-KR"):esc(n);
const ago=(ts)=>{if(!ts)return "";const d=Math.max(0,Math.floor(Date.now()/1000-ts));return d<60?`${d}초 전`:d<3600?`${Math.floor(d/60)}분 전`:`${Math.floor(d/3600)}시간 전`;};
/* 「지금부터 sec 초 뒤」가 몇 시인가 — 「02:10 남음」보다 「15:42 완료」가 계획을 세우기 쉽다.
   날짜가 넘어가면 그렇게 적는다. **경과일은 시각 차가 아니라 달력 날짜로 센다** —
   23:50 에 30분이 남았으면 24시간이 안 지났어도 「내일 00:20」이다. */
const fmtAt=(sec)=>{if(sec==null||!isFinite(sec))return "";
  const now=new Date(),d=new Date(now.getTime()+Math.max(0,sec)*1000);
  const hm=`${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
  const day=(a)=>Math.floor(new Date(a.getFullYear(),a.getMonth(),a.getDate()).getTime()/86400000);
  const dd=day(d)-day(now);
  return dd<=0?hm:dd===1?`내일 ${hm}`:`${d.getMonth()+1}/${d.getDate()} ${hm}`;};
const hhmm=(t)=>{if(!t)return "";const d=new Date(t*1000);return [d.getHours(),d.getMinutes(),d.getSeconds()].map((x)=>String(x).padStart(2,"0")).join(":");};   // 14:02:31

/* ── 연결 상태 ── */
function renderConn(){const c=S.cli;const dot=$("connDot"),tx=$("connTxt");
  setTimeout(()=>{dot.title=tx.textContent||"";},0);   // 좁은 창에서는 글자를 숨기므로 점에 툴팁으로 남긴다
  if(serverGone){dot.className="dot off";tx.textContent=NET.REMOTE?"PC 와 연결 끊김":"앱 서버 연결 끊김";return;}   // 서버가 살아 있고 pipe 가 끊겼을 때만 「게임 꺼짐」
  if(!c){dot.className="dot";tx.textContent=(NET.REMOTE&&S.cliCached)?"PC 가 아직 게임 상태를 읽지 않았습니다":"확인 중…";return;}   // 폰: PC 가 한 번도 안 읽었으면 값이 없다
  if(!c.found){dot.className="dot off";tx.textContent="CLI 없음";return;}
  if(c.pipe==="disabled"){dot.className="dot warn";tx.textContent="CLI 차단";$("demoBadge").hidden=true;return;}   // MOBIW_NO_CLI=1 (실데이터 검증용 — 자동 갱신도 안 돈다)
  if(c.pipe==="connected"){dot.className="dot on";tx.textContent=c.demo?"데모 연결":"게임 연결됨";}
  else{dot.className="dot off";tx.textContent=c.reason==="game_off"?"게임 꺼짐":"게임 연결 없음";}
  $("demoBadge").hidden=!c.demo;}
/* 폰(밖)은 `nocli=1` — PC 가 마지막으로 읽어 둔 상태만 받는다. 서버도 밖에서 온 `/api/state` 에는 CLI 를 안 부르지만,
   화면에서도 그렇게 부른다 — 폰은 PC 가 읽은 값만 본다. 이 PC 는 그대로 probe 다. */
async function refreshState(){const d=await api(NET.REMOTE?"/api/state?nocli=1":"/api/state");S.cliCached=!!(d&&d.cached);
  if(d&&d.cli){S.cli=d.cli;renderConn();renderTabBadges();renderQueue();}else if(d&&d.cached&&!S.cli)renderConn();return S.cli;}
/* 탭 옆 숫자 — 「가공 2」처럼. 가공 대기열에 걸린 작업 수(진행·대기·수령 대기 합)를 보여 준다.
   스냅샷을 그리는 곳(main.js·works.js)과 떨어져 있어, 1초마다 메모리 상태만 읽어 바뀐 때만 DOM 을 건드린다 */
function renderTabBadges(){
  // 위 탭 줄과 **하단 탭바** 둘 다 — 한 값을 두 곳에 적는다 (좁은 폭에서는 아래만 보인다)
  const w=(S.snap&&Array.isArray(S.snap.works))?S.snap.works.length:0;const t=w>0?String(w):"";
  for(const id of ["tabAlterN","mTabAlterN"]){const el=$(id);if(!el)continue;
    if(el.textContent!==t)el.textContent=t;if(el.hidden!==!w)el.hidden=!w;}}
setInterval(renderTabBadges,1000);renderTabBadges();

/* ── 탭 ── */
function selectTab(t,push){if(!TABS.includes(t))t="craft";S.tab=t;S.sel=null;{const pd=$("plan");if(pd&&pd.open)pd.close();}
  // 탭 이동·목록 점프는 계획 팝업을 닫고, 탭 강조를 옮긴다 (주석이 같은 줄의 코드를 삼켜 강조가 안 옮겨가던 버그 수정)
  document.querySelectorAll(".tabs button").forEach((x)=>x.classList.toggle("on",x.dataset.tab===t));
  // 하단 탭바도 같이 (재고·사전은 이 함수가 탭을 옮긴다 — 안 칠하면 폰에서 강조가 안 따라간다)
  document.querySelectorAll("#mtabs button").forEach((x)=>x.classList.toggle("on",x.dataset.tab===t));
  if(push){try{history.replaceState(null,"","#"+t);}catch{}}renderTab();}
document.querySelectorAll(".tabs button").forEach((b)=>{b.onclick=()=>selectTab(b.dataset.tab,true);});
// 주소의 해시가 바뀌면 탭도 따라간다 — 해시만 바뀌는 이동(뒤로가기·붙여넣은 #dict 링크·업데이트 뒤 되돌아오기)은
// 페이지를 다시 읽지 않아 시작 코드가 안 돌고, 이 리스너가 없으면 탭이 그대로 남는다.
window.addEventListener("hashchange",()=>{const t=(location.hash||"").slice(1).split("?")[0];
  if(TABS.includes(t)&&t!==S.tab)selectTab(t,false);});
let qT=null;$("q").oninput=()=>{clearTimeout(qT);qT=setTimeout(()=>{S.q=$("q").value.trim();$("qClearBtn").hidden=!S.q;for(const k in S.limit)S.limit[k]=PAGE;keepScroll(renderTab);},120);};   // 120ms 디바운스, 검색창 자체는 다시 그리지 않는다(IME 조합 보호)
$("qClearBtn").onclick=()=>{$("q").value="";S.q="";$("qClearBtn").hidden=true;renderTab();$("q").focus();};
/* ── 내려받기 (백업 zip·사전 JSON) ──
   **토큰은 주소에 싣지 않는다.** 예전의 `<a download>` 링크는 실행 토큰을 주소(쿼리)에 실어
   기록·Referer·확장 프로그램에 남을 수 있었다. 헤더(NET.headers — X-Requested-With + X-MobiWorks-Token)로
   fetch → blob → 임시 blob: 주소를 단 <a download> 를 눌러 저장 → 주소를 거둔다. 파일 이름은 서버의
   Content-Disposition 을 따른다 (없으면 fallback). 서버는 쿼리 토큰을 더는 받지 않는다. */
async function saveDownload(url,fallback){
  let r;try{r=await fetch(url,{headers:NET.headers(false)});}catch(e){toast("내려받지 못했습니다");return false;}
  if(!r.ok){toast(`내려받지 못했습니다 (${r.status})`);return false;}
  const m=/filename="([^"]+)"/.exec(r.headers.get("Content-Disposition")||"");
  const blob=await r.blob();const href=URL.createObjectURL(blob);
  const a=document.createElement("a");a.href=href;a.download=(m&&m[1])||fallback;a.hidden=true;
  document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(href),1000);   // 클릭이 저장을 시작한 뒤에 거둔다 (바로 거두면 빈 파일이 되는 브라우저가 있다)
  return a.download;}   // 성공이면 저장한 파일 이름(참 값) — 「<파일> 저장했습니다」에 쓴다 (문제 신고 zip)
function lsGet(k){try{return localStorage.getItem(k);}catch{return null;}}
function lsSet(k,v){try{if(v==null)localStorage.removeItem(k);else localStorage.setItem(k,String(v));}catch{}}
Object.assign(window.MW,{APP_TITLE,TOKEN,$,esc,toast,api,netFail,netOk,S,PAGE,WINGS_PER_CALL,rid,stockKnown,bagOf,storeOf,holdTxt,holdCalc,moveBadge,wingsHave,limitOf,moreBtn,REASON_KO,TABS,fmtT,fmtAt,fmtN,ago,hhmm,renderConn,renderTabBadges,refreshState,selectTab,lsGet,lsSet,saveDownload});
