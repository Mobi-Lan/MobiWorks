// board.js — 큐 보드(칸반): 4열(대기·작업 중·완료·실패) · 그룹 카드 · 실행 줄 · 드래그 · 인라인 스테퍼 · 회신 기록 · 시작 확인창
//            좁은 폭(≤600px)은 칸반 접힘 — 작업 중은 위에 고정, 나머지 셋은 탭 (drawNarrow)
/* 지키는 것 세 가지:
   ① 카드 배치는 서버가 준 column 만 믿는다 — UI 가 status 를 다시 해석하면 두 곳의 판정이 반드시 어긋난다.
   ② 페이지에는 스크롤이 생기지 않는다. 스크롤은 열 안쪽(.bcol-b)과 그룹 안쪽 칸반(.gin-b)에만.
   ③ 스테퍼를 누르는 중이거나 카드를 끌고 있는 동안에는 다시 그리지 않는다 — 누르고 있던 버튼이
      DOM 에서 빠지면 pointerup 이 영영 오지 않아 값이 혼자 움직인다(실측 사고). queue.js 의 방어를 그대로 옮겨 왔다. */

/* ── 상태 ── */
// Q 는 옛 이름 그대로 둔다 — works.js·plan.js·main.js·tab-stock.js 가 Q.data / Q.avail 을 본다
const Q={data:null,avail:null,pollT:null,lastErrKey:null,errFlag:false,   // lastErrKey: null = 아직 첫 응답 전 (etoastCheck)
  collectDone:null,
  evOpen:(function(){try{return localStorage.getItem("mw.evOpen")==="1";}catch{return false;}})(),evFilter:"all",
  stOpen:false,stats:null,stBusy:false,   // 날개·산출 기록 (열 때만 /api/stats 를 받는다 — 평소에는 아무것도 안 부른다)
  srKey:"",                               // 마지막으로 본 체인 정지(stopReason) — 새로 뜰 때 한 번만 실패 탭을 고른다
  prev:null,prevKey:"",prevSeq:0,local:{},editT:{},seq:{},pendRender:false,open:new Set(),
  st0:{},rs0:0};   // st0: 항목별 마지막으로 본 시작 시각 · rs0: 이번 실행의 시작 시각 — 한 응답에 start 회신이 빠져도 경과가 00:00 으로 튀지 않게
// ghost = 자리 표시 상자. 끄는 동안 하나만 만들어 두고 자리만 옮긴다 — 매번 새로 만들면 깜빡인다
const B={drag:null,ghost:null,ghostH:0,mock:false,
  // 좁은 폭에서 고른 탭 (대기·완료·실패 셋). 「작업 중」은 탭이 아니라 위에 고정된 슬롯이다 —
  // 옛 값 "run" 이 남아 있으면 대기로 돌린다. 넓은 폭에서는 쓰이지 않는다 (네 열이 다 보인다)
  seg:(function(){try{const v=localStorage.getItem("mw.boardSeg");return ["wait","done","fail"].includes(v)?v:"wait";}catch{return "wait";}})()};

const COLS=[["wait","대기"],["run","작업 중"],["done","완료"],["fail","실패"]];
const COL_KO={wait:"대기",run:"작업 중",done:"완료",fail:"실패"};
// 좁은 폭의 탭 셋 — viewmodel.TAB_COLS 와 짝
const TAB_COLS=COLS.filter(([k])=>k!=="run");
/* 좁은 폭인가 — board.css 의 `@media (width < 600px)` 와 **같은 경계**. 구조가 다르므로(작업 중 고정 + 탭)
   CSS 만으로는 못 바꾸고 그리는 함수가 갈린다. 경계를 넘으면 다시 그린다. */
const NARROW_MQ=(typeof window!=="undefined"&&typeof window.matchMedia==="function")?window.matchMedia("(width < 600px)"):null;
const isNarrow=()=>!!(NARROW_MQ&&NARROW_MQ.matches);
if(NARROW_MQ&&NARROW_MQ.addEventListener)NARROW_MQ.addEventListener("change",()=>{const ft=$("boardFoot");if(ft)ft.dataset.done="";draw();});
// 서버와 같은 표. 서버가 column 을 안 줬을 때만 쓰는 방어용 되돌림이지 별도 판정이 아니다
const COL_ST={pending:"wait",running:"run",waiting:"run",done:"done",error:"fail",stopped:"wait"};
const isGroup=(c)=>!!c&&c.type==="group";
/* 서버가 실은 column 을 믿는다. 없을 때의 되돌림은 **서버와 같은 규칙** — 낱장은 COL_ST 표, 그룹은
   workqueue.column_of 의 네 단계. 전에는 그룹에도 낱장 표를 써서 정지 그룹을 실패 열로, 자식이 도는
   pending 그룹을 대기 열로 봤다 — viewmodel(파이썬)과 열·「실패 n」 배지가 갈렸다. */
const REPEAT_MAX=20;   // workqueue.REPEAT_MAX
function groupHasLeft(g){const kids=(g.items||[]).filter(Boolean);
  if(kids.some((k)=>k.status==="pending"||k.status==="stopped"))return true;
  const loop=Number(g.loop)||0;const rep=Math.min(REPEAT_MAX,Math.max(1,Number(g.repeat)||1));return loop>0&&loop<rep;}
function groupColOf(g){const kids=(g.items||[]).filter(Boolean);
  if(g.status==="running"||kids.some((k)=>k.status==="running"||k.status==="waiting"))return "run";
  if(g.status==="error")return "fail";if(g.status==="done")return "done";
  if(g.status==="stopped")return groupHasLeft(g)?"wait":"done";return "wait";}
const colOf=(c)=>{const k=c&&c.column;if(COL_KO[k])return k;if(isGroup(c))return groupColOf(c);
  return COL_ST[(c&&c.status)||"pending"]||"wait";};

const QTYPE_KO={craft:"제작",alter:"가공",gather:"채집",collect:"수령",group:"그룹",play:"연주",notify:"알림"};
const QTYPE_CLS={gather:"gather",craft:"run",alter:"warn",collect:"collect",group:"gold",play:"gold",notify:"notify"};   // 종류 배지 색 — 연주 = 폴리오 금색 ♪ · 알림 = 파랑
const PLAY_MODE_KO={song:"이 곡만",list:"재생목록 전체 (옛 카드)",resume:"지금 대기열 이어서"};   // workqueue.PLAY_MODE_KO — 재생목록은 이제 그룹으로 담긴다
const QST_KO={pending:"대기",running:"실행 중",waiting:"가공 대기",done:"완료",stopped:"정지",error:"오류"};
// 앱이 만든 오류(게임이 아니라 시작 전 확인에서 걸린 것) — 「앱 검사」 표시로 게임 오류와 구분한다 (서버 workqueue.APP_ERRORS 와 같은 목록)
const APP_ERRS=new Set(["tool_not_ok","overweight_soon","max_passes","not_gatherable","not_in_cache","cli_disconnected"]);
// 오류코드별 안내 한 줄. 코드는 원문 그대로 보여 주고, 여기 없는 코드는 안내 없이 코드·메시지만
const ERR_HINT={tool_broken:"도구를 고치거나 새로 장착한 뒤 재시도",tool_not_ok:"이 채집에 맞는 도구가 없습니다",tool_missing:"채집 도구가 없습니다",overweight:"가방을 비우거나 창고에 넣은 뒤 재시도",overweight_soon:"가방이 거의 찼습니다",
  not_enough_ingredient:"부족한 재료를 앞에 담거나 가방으로 옮기세요",insufficient_transfer_cost:"재료를 직접 가방으로 옮기면 됩니다",insufficient_living_skill_level:"지금은 만들 수 없는 레시피입니다",insufficient_facility_level:"지금은 만들 수 없는 레시피입니다",insufficient_decor_score:"지금은 만들 수 없는 레시피입니다",
  invalid_count:"한 번에 만들 수 있는 횟수를 넘었습니다 — 나눠서 담으세요",requires_user_interaction:"게임 안에서 직접 등록해야 하는 가공입니다",not_completed_yet:"아직 완료된 가공이 없습니다",no_completed_work:"아직 완료된 가공이 없습니다",no_completed_work_at_facility:"이 시설에 완료된 가공이 없습니다",
  blocked:"게임 화면의 창을 닫은 뒤 「시작」",disconnected:"게임과 연결이 끊겼습니다 — 게임을 확인한 뒤 「▶ 시작」",game_off:"게임이 꺼져 있습니다",timeout:"응답이 없었습니다 — 게임 상태를 확인하세요",loading:"게임이 로딩 중이었습니다 — 게임에 들어간 뒤 「▶ 시작」",stopped_by_user:"게임에서 정지했습니다 — 이어하려면 「▶ 시작」",max_passes:"반복 상한에 닿았습니다. 설정에서 올리거나 개수를 나누세요",
  not_enough_currency:"정령의 날개가 부족합니다",required_consumable_missing:"채집에 필요한 소모품이 없습니다",no_route:"채집지까지 갈 수 없습니다",not_in_field:"필드가 아닙니다",facility_not_found:"시설을 찾지 못했습니다",cost_payment_failed:"비용 지불에 실패했습니다",canceled:"다른 명령이 끼어들어 취소됐습니다",
  not_gatherable:"채집 목록에 없는 항목입니다",not_in_cache:"레시피가 캐시에 없습니다 — 「갱신」 뒤 다시",cli_disconnected:"CLI 연결이 없습니다",cli_not_found:"CLI 를 찾지 못했습니다",cli_disabled:"CLI 실행이 차단된 실행입니다"};
// 보드 op 거절 코드 — 한글 한 줄
const OP_ERR={group_running:"실행 중인 그룹의 구성은 바꿀 수 없습니다 — 정지 후에",nested:"그룹 안에 그룹을 넣을 수 없습니다",not_found:"이미 없는 항목입니다",busy:"실행 중에는 순서를 바꿀 수 없습니다",bad_request:"요청을 처리하지 못했습니다",gone:"전체 반복은 그룹으로 바뀌었습니다 — 그룹 설정에서 회차를 정하세요"};
// blocked 는 kind 별로 안내가 다르다 — kind 는 응답 필드(it.kind / stopReason.kind) 또는 메시지 끝의 「kind=…」에서
function blockedKind(o){if(!o)return "";if(o.kind)return String(o.kind);const m=/kind=([A-Za-z0-9_-]+)/.exec(o.message||"");return m?m[1]:"";}
// blocked 의 kind(게임이 준 영어 낱말) → 한글. **viewmodel.BLOCK_KIND_KO 와 짝.** 모르는 kind 는 원문 그대로
const BLOCK_KIND_KO={dialog:"대화",popup:"팝업 창",combat:"전투",dungeon:"던전",battlefield:"전장",scenario:"시나리오",tutorial:"튜토리얼",
  reviving:"부활 대기",minigame:"미니게임",cutscene:"컷신",housing:"하우징 편집",fishing:"낚시",loading:"로딩",dead:"사망"};
const kindKo=(k)=>k?(BLOCK_KIND_KO[k]||k):"";
function blockedHint(kind,act){act=act||"다시 시작";
  if(kind==="dead")return `게임이 캐릭터를 사망/부활 대기 상태로 보고했습니다. 부활 선택 창이 있으면 닫고, 캐릭터가 멀쩡한데도 반복되면 캐릭터를 한 번 움직이거나 채널 이동 뒤 「${act}」. 그래도 같으면 게임 AI 커넥터 상태 오류 — 게임 재접속.`;
  if(kind)return `게임 화면의 「${kindKo(kind)}」 — 닫거나 끝낸 뒤 「${act}」`;
  return `게임 화면에 열린 창을 닫은 뒤 「${act}」`;}
// 오류 코드 → 안내. 게임 메시지가 이미 같은 말을 하고 있으면 안내를 붙이지 않는다
function errHint(o,act){const code=(o&&o.error)||"";const h=code==="blocked"?blockedHint(blockedKind(o),act):ERR_HINT[code];
  return h&&String((o&&o.message)||"").includes(h)?"":h;}
const opErrTxt=(r)=>{const c=r&&r.error;return (r&&r.message)||OP_ERR[c]||(c?`실패: ${c}`:"실패");};
/* 체인 정지(치명) 한 벌 → {error, message[, kind]} | null. **viewmodel.chain_stop 과 짝.**
   서버의 stopReason 은 **글자**다 — "user" · "onError" · "fatal:<code>". 전에는 객체일 때만 배너로 봐서
   실제 데이터에서는 배너·실패 탭 자동 선택이 한 번도 안 떴고 실행 줄에 「fatal:blocked」 원문이 찍혔다.
   코드는 글자에서, 메시지(blocked 의 kind 포함)는 같은 코드의 lastError 에서. 객체로 오면 그대로 받는다. */
function chainStop(d){const sr=d&&d.stopReason;
  if(sr&&typeof sr==="object")return sr.error?sr:null;
  if(typeof sr!=="string"||!sr.startsWith("fatal:"))return null;
  const code=sr.slice(6);const le=(d.lastError&&typeof d.lastError==="object")?d.lastError:{};
  const same=(le.error||le.code)===code;const out={error:code,message:same?(le.message||""):""};
  if(same&&le.kind)out.kind=le.kind;return out;}

/* ── 항목 찾기 (그룹 안팎 통틀어 id 는 전역 유일) ── */
const rootItems=()=>((Q.data&&Q.data.items)||[]).filter(Boolean);
function findItem(id){for(const it of rootItems()){if(it.id===id)return it;if(isGroup(it))for(const c of (it.items||[]))if(c&&c.id===id)return c;}return null;}
function findParent(id){for(const it of rootItems())if(isGroup(it))for(const c of (it.items||[]))if(c&&c.id===id)return it;return null;}

/* ── 진행·부제 텍스트 (queue.js 에서 그대로 옮김) ── */
const qtyTxt=(it)=>it.type==="gather"?`${fmtN(it.target||0)}개`:it.type==="collect"?`${fmtN(it.count||0)}건`
  :it.type==="play"?({song:"1곡",list:"목록",resume:"이어서"}[it.mode||""]||"이어서"):it.type==="notify"?"1회":`${fmtN(it.count||1)}회`;   // viewmodel.qty_txt 와 같은 글
// 가공 대기(waiting)는 정상 흐름 — 등록만 하고 다음 항목으로 넘어간다. 남은 시간은 대기열 카드와 같은 값
function workLeft(name){const s=S.snap;if(!s||!s.works)return null;const el2=Math.floor((Date.now()-S.fetchedAt)/1000);
  const ws=s.works.filter((w)=>w.name===name&&!w.done);if(!ws.length)return (s.works.some((w)=>w.name===name)?0:null);return Math.max(0,Math.min(...ws.map((w)=>w.left))-el2);}
function waitTxt(name){const l=workLeft(name);return l==null?"가공 대기":l===0?"완료 · 수령 대기":`${fmtT(l)} · ${fmtAt(l)} 완료`;}
// 이 항목의 마지막 「시작」 회신 시각 (경과 시간·등록 시각 표시용). 회신 기록(events)에서 찾는다
// 찾으면 기억해 두고, 이번 응답에 없으면(회신 기록이 잘렸거나 빠진 응답) **마지막으로 본 값**을 쓴다 — 0 으로 돌아가지 않는다
function lastStart(id){const ev=(Q.data&&Q.data.events)||[];
  for(let i=ev.length-1;i>=0;i--)if(ev[i].id===id&&ev[i].kind==="start"&&ev[i].t){Q.st0[id]=ev[i].t;return ev[i].t;}
  return Q.st0[id]||0;}
/* 경과 칸 한 개 — **그릴 때 이미 계산한 값**을 넣는다. 1초 틱(아래 setInterval)은 같은 data-el 로 같은 값을 다시 쓴다.
   전에는 좁은 폭 행(kbRowHTML)이 글자 "00:00" 을 박아 두고 틱이 고치기를 기다렸다 → 러너 중 1초 폴링이 다시 그릴 때마다
   00:50 → 00:00 → 00:51 로 깜빡였다. 경과를 보여 주는 곳은 전부 이 함수 하나를 쓴다. */
/* 도는 카드의 시계 시작점 — **연주 대기 중이면 0** (00:00). 서버는 카드의 start 를 대기 **전에** 적고(_run_card) 대기가 끝나면
   한 번 더 적는다(_hold_end). 그 사이에 첫 start 로 세면 「연주 대기 00:19 · 카드 00:11」처럼 기다리는 카드가 도는 것처럼
   보인다. 기다림은 hold 카드·머리·실행 줄이 hold.started 로 세고, 이 카드는 서지 않는다. */
function cardStart(it){const h=holdOf(Q.data);return h&&h.card===it.id?0:lastStart(it.id);}
/* 가공 카드의 수령 꼬리표 — **수령까지 하는 항목인지** 담기 전·대기·실행 중·가공 대기 내내 보인다.
   규칙은 서버 workqueue.alter_mode 그대로:
   collect 가 "none" 이면 걸기만(수령 안 함), 그 밖(later·wait·값 없음)은 큐가 완료를 감지해 수령한다. 완료·실패 카드에는 안 붙인다. */
const alterCollects=(it)=>it.collect!=="none";
function collectTag(it){if(it.type!=="alter"||!["pending","running","waiting"].includes(it.status||"pending"))return "";
  return alterCollects(it)?`<span class="tag col" title="큐가 완료를 감지해 수령까지 합니다 (날개 소모 없음)">완료 후 수령</span>`
    :`<span class="tag nocol" title="걸기만 — 수령하지 않습니다 (수령은 따로 담습니다)">수령 안 함</span>`;}
function elapsedHTML(t0,cls){return `<span${cls?` class="${cls}"`:""} data-el="${esc(String(t0||0))}">${t0?fmtT(Date.now()/1000-t0):"00:00"}</span>`;}
const routeOf=(it)=>{const m=/#(\d+)$/.exec(it.recipeId||"");return m?m[1]:"";};
function progOf(it,rep){const p=it.progress||{};const st=it.status;const acc=(p.totalDone!=null&&rep>1)?` · 누적 ${fmtN(p.totalDone)}`:"";
  // 연주·알림 — 막대 없음, 한 줄. viewmodel.prog_of 와 **같은 글**
  if(it.type==="play"||it.type==="notify"){const fresh=st==="pending"&&!p.done;
    return {txt:it.type==="play"?playProg(it,p,st):(fresh?"알림 예정 (호출 없음 · 날개 0)":"알림 보냄"),pct:null};}
  if(it.type==="collect"){const c=it.count||0;return {txt:st==="pending"&&!p.done?`수령 예정 ${fmtN(c)}건 (1회 호출)`:`수령 ${fmtN(p.done||0)} / ${fmtN(c)}건${acc}`,pct:st==="pending"&&!p.done?null:Math.min(100,Math.round((p.done||0)/Math.max(1,c)*100))};}
  /* 채집 = 개수(target) (N3). 진행 = 「지나간 회/예상 회 · +가방으로 센 개수/목표 개수 · 가방」.
     개수는 가방 수로 센다 (시작 전 가방 → 지금 가방). 남은 개수가 100 보다 적은 마지막 회는 서버가 도는 동안
     가방을 읽어 `progress.live`(이번 회에 늘어난 개수)를 적는다 — 그때는 진짜 숫자를 쓰고, 없으면 「n회째 도는 중」.
     viewmodel.prog_of 와 **같은 글**. */
  if(it.type==="gather"){const t=it.target||0;const n=p.passesPlanned||gpass(t);const got=p.done||0;const ps=p.passes||0;
    const bag=p.have!=null?` · 가방 ${fmtN(p.have)}`:"";
    const live=st==="running"&&p.live!=null?Number(p.live):null;
    const now=live!=null?got+live:got;
    const fresh=st==="pending"&&!ps;
    const txt=fresh?`${fmtN(n)}회 · 날개 ${fmtN(n*5)}${p.have!=null?` · 가방 ${fmtN(p.have)} → 목표 ${fmtN(p.have+t)}`:""}`
      :st==="running"&&live==null
        ?`${fmtN(ps)}/${fmtN(n)}회 · +${fmtN(got)}/${fmtN(t)}개 · ${fmtN(ps+1)}회째 도는 중${bag}${acc}`
        :`${fmtN(ps)}/${fmtN(n)}회 · +${fmtN(now)}/${fmtN(t)}개${bag}${acc}`;
    return {txt,pct:fresh?null:(st==="done"?100:(t?Math.min(100,Math.round(now/t*100)):0))};}
  /* 가공 — 칸·수령을 센 카드(N6)는 「등록 14/63 · 수령 7 · 칸 5/7」. 막대는 걸기만이면 등록, 그 밖은 수령으로 찬다.
     viewmodel.prog_of 와 **같은 글** */
  if(it.type==="alter"){const c=it.count||1;const fresh=st==="pending"&&!p.done;
    if(!fresh&&(p.cap!=null||p.got)){const slot=p.cap!=null?` · 칸 ${fmtN(p.slot||0)}/${fmtN(p.cap||0)}`:"";
      const base=it.collect==="none"?(p.done||0):(p.got||0);
      return {txt:`등록 ${fmtN(p.done||0)}/${fmtN(c)} · 수령 ${fmtN(p.got||0)}${slot}${acc}`,pct:Math.min(100,Math.round(base*100/Math.max(1,c)))};}
    if(st==="waiting")return {txt:`등록됨 · 완료 감지 → 수령${p.passes?` · ${p.passes}건`:""}${acc}`,pct:null};
    return {txt:fresh?`${fmtN(c)}건 등록 예정`:`${fmtN(p.done||0)} / ${fmtN(c)}건${acc}`,pct:fresh?null:Math.min(100,Math.round((p.done||0)/Math.max(1,c)*100))};}
  const c=it.count||1;const mul=p.per>1?` (×${p.per})`:"";const fresh=st==="pending"&&!p.done;
  /* 시설 상한으로 나눠 부르는 제작(N7) — 「3/15번 · 30/150회」. 나눌 수는 서버가 progress.passesPlanned 에 적는다 */
  const planned=it.type==="craft"?(p.passesPlanned||1):1;
  if(planned>1)return {txt:fresh?`${fmtN(c)}회 예정${mul} · ${fmtN(planned)}번 나눠 호출`:`${fmtN(p.calls||0)}/${fmtN(planned)}번 · ${fmtN(p.done||0)}/${fmtN(c)}회${mul}${acc}`,pct:fresh?null:Math.min(100,Math.round((p.done||0)/c*100))};
  return {txt:fresh?`${fmtN(c)}회 예정${mul}`:`${fmtN(p.done||0)} / ${fmtN(c)}회${mul}${acc}`,pct:fresh?null:Math.min(100,Math.round((p.done||0)/c*100))};}
/* 연주 카드의 진행 한 줄 — **카드는 연주가 끝날 때까지 돈다**. 도는 동안 폴리오가 답한 회차·경과(progress.play), 끝나면 note.
   viewmodel.play_prog 와 **같은 글** */
function playProg(it,p,st){const pl=p.play||null;
  if(st==="pending"&&!p.done)return "연주 시작 예정 (호출 없음 · 날개 0)";
  if(st==="running"){if(!pl)return "연주 부탁 중…";
    const el=pl.started?fmtT(Date.now()/1000-pl.started):"00:00";
    if(pl.endless)return `연주 중 · ${pl.title||it.name||""} · 끝을 알 수 없음 — 반복 재생 · ${el}`;
    return `연주 중 · ${pl.title||it.name||""}${pl.passes?` · ${fmtN(pl.pass||1)}/${fmtN(pl.passes)}회`:""} · ${el}`;}
  if(st==="done")return p.note||(pl&&pl.passes?`연주 끝 · ${fmtN(pl.passes)}회`:"연주 끝");
  if(st==="stopped")return "연주 멈춤 (■ 정지)";
  if(st==="error")return "연주 실패";
  return "연주 시작을 부탁함";}
function subOf(it){const p=it.progress||{};
  if(it.type==="play")return (PLAY_MODE_KO[it.mode||""]||"")+(it.mode==="song"?` · ${fmtN(it.count||1)}회`:"");   // viewmodel.sub_of 와 같은 글 (「이 곡만 · 2회」)
  if(it.type==="notify")return it.sound===false?"소리 끔":"소리 켬";
  if(it.type==="collect")return "시설당 1회 호출로 전부 수령";
  if(it.type==="gather")return `회당 최대 100${it.status==="pending"&&p.totalDone?` · 직전 누적 ${fmtN(p.totalDone)}`:""}`;
  if(it.type==="alter"){const fac=typeof facilityOf==="function"?facilityOf(it.name):"";const t0=lastStart(it.id);
    return [fac,t0?`등록 ${hhmm(t0).slice(0,5)}`:"",it.collect==="wait"?"완료까지 기다림":""].filter(Boolean).join(" · ");}
  return p.per>1?`1회 ${fmtN(p.per)}개`:"";}
// 진행 텍스트 + 부제를 한 줄로. sub 는 HTML 을 담을 수 있어 title 용 평문을 따로 만든다
function txOf(it,rep){const pr=progOf(it,rep||1);const sub=subOf(it);
  return {pr,sub,html:`${esc(pr.txt)}${sub?`<span class="sb"> · ${esc(sub)}</span>`:""}`,plain:`${pr.txt}${sub?" · "+sub:""}`};}
// 호출 예상 회수 — 서버 preview(_preview_card) 와 **같은 규칙**. 가공은 남은 등록 + 수령 왕복(workqueue.alter_calls —
// 칸 7 로 보고 7건마다 한 번, 「걸기만」은 칸을 비울 때만), 제작은 시설 상한으로 나눈 남은 호출(N7 · progress.passesPlanned)
function alterCalls(it){const p=it.progress||{};const c=Math.max(1,it.count||1);const done=Math.min(c,Math.max(0,p.done||0));
  const got=Math.min(done,Math.max(0,p.got||0));const regs=it.regStop?0:c-done;const none=it.collect==="none";const S=7;   // workqueue.ALTER_SLOTS_DEFAULT
  const trips=none?(done>0?Math.ceil(regs/S):Math.ceil(Math.max(0,c-S)/S)):Math.ceil(Math.max(0,c-got)/S);
  if(it.status==="waiting"&&!none&&regs===0)return [Math.max(1,trips),0];   // 수령만 남았다 — 날개 없음
  return [regs+trips,regs];}
function itemCalls(it){const p=it.progress||{};if(it.type==="play"||it.type==="notify")return 0;   // 연주·알림 — 호출 없음
  if(it.type==="gather")return p.passesPlanned||gpass(it.target||0);if(it.type==="collect")return 1;
  if(it.type==="craft")return Math.max(1,(p.passesPlanned||1)-(p.calls||0));
  return alterCalls(it)[0];}
/* 정령의 날개를 쓰는 호출만 센다. 소모하는 것은 execute_gathering·execute_crafting·execute_altering 셋뿐이고
   complete_altering_work(수령)는 쓰지 않는다 — 수령까지 세면 실제와 어긋난다. */
function itemWingCalls(it){if(it.wingCalls!=null)return Number(it.wingCalls)||0;
  if(it.type==="collect"||it.type==="play"||it.type==="notify")return 0;if(it.type==="alter")return alterCalls(it)[1];return itemCalls(it);}

/* ── 인라인 스테퍼 (queue.js 의 방어를 그대로 옮겨 왔다 — 다시 쓰지 않는다) ──
   pending·stopped·error 만 편집. 값은 즉시 화면 반영 후 250ms 디바운스로 {op:"update"}.
   서버가 거부하면 이전 값으로 되돌리고 토스트. Q.local 은 서버 확인 전 화면값(폴링 재렌더가 되돌리지 않게).
   길게 누르기는 plan.js 의 holdBtn — window 레벨 pointerup/pointercancel/blur, visibilitychange,
   setPointerCapture, el.isConnected 검사가 전부 그 안에 있다. */
// 수령(collect)은 시설의 완료분을 한 번에 받는 것이라 수량 개념이 없다 — 서버도 not_editable 로 거부한다
// 연주는 song·list 의 회차(1~20)만 고친다 (resume 은 폴리오 제 설정대로라 없다) · 알림은 수량이 없다 — viewmodel.editable 과 같은 규칙
const PLAY_COUNT_MAX=20;   // workqueue.PLAY_COUNT_MAX
const editable=(it)=>["pending","stopped","error"].includes(it.status||"pending")&&it.type!=="collect"&&it.type!=="notify"&&!isGroup(it)
  &&(it.type!=="play"||it.mode==="song");
/* 수량 줄은 「스테퍼 | 단위」 두 칸뿐이다. 옛 큐에 있던 「최대」·「등록만/기다림」 버튼은 여기 두지 않는다 —
   「최대」는 담기 서랍에 있고, 가공을 기다릴지는 그룹 설정의 항목이다. 카드를 한 줄 더 키우면서까지 둘 자리가 아니다. */
/* 수량을 고칠 수 있나 — 못 고치면 스테퍼 대신 **이름 줄 오른쪽에 글자로** 붙인다 (qtyOnName). */
function qtyEditable(it){return editable(it)&&Q.avail!==false;}
const qtyMax=(it)=>it.type==="gather"?99999:it.type==="play"?PLAY_COUNT_MAX:999;   // 연주 회차는 1~20 (서버도 자른다)
function qtyCell(it){const g=it.type==="gather";const v=g?(it.target||0):(it.count||1);const unit=g?"개":it.type==="collect"?"건":"회";
  const stepN=g?100:1;
  return `<div class="bqty" data-qe="${esc(it.id)}"><span class="stepper"><button data-qst="${-stepN}" title="${g?"−100":"−1"} (길게 누르면 연속)">−</button>`
    +`<input class="v" type="number" data-qin min="1" max="${qtyMax(it)}" value="${v}" title="직접 입력"><button data-qst="${stepN}" title="${g?"+100":"+1"} (길게 누르면 연속)">+</button></span>`
    +`<span class="unit">${unit}</span></div>`;}
function qEditApply(id,patch){const it=findItem(id);if(!it)return;
  const prev=Q.local[id]||{count:it.count,target:it.target,collect:it.collect};if(!Q.local[id])Q.local[id]={...prev,_prev:{...prev}};
  if(patch.count!=null){patch.count=Math.max(1,Math.min(qtyMax(it),Math.round(patch.count)));it.count=patch.count;Q.local[id].count=patch.count;}
  if(patch.target!=null){patch.target=Math.max(1,Math.min(99999,Math.round(patch.target)));it.target=patch.target;Q.local[id].target=patch.target;it.progress=it.progress||{};it.progress.passesPlanned=gpass(patch.target);}
  if(patch.collect!=null){it.collect=patch.collect;Q.local[id].collect=patch.collect;}
  // 즉시 반영: 그 카드의 입력값·예상 회수·부제만 (재렌더 없음 — 깜빡임도 없다)
  const cell=document.querySelector(`[data-qe="${CSS.escape(id)}"]`);const card=cell&&cell.closest(".bcard");
  if(cell){const inp=cell.querySelector("[data-qin]");const v=it.type==="gather"?it.target:it.count;
    if(inp&&document.activeElement!==inp&&Number(inp.value)!==v)inp.value=v;}
  const cb=document.querySelector(`[data-qe2="${CSS.escape(id)}"] [data-qcol]`);if(cb)cb.textContent=it.collect==="wait"?"기다림":"등록만";
  if(card){const g=findParent(id);const rep=g?(Number(g.repeat)||1):1;const t=txOf(it,rep);
    const es=card.querySelector(".est");if(es)es.textContent=(it.type==="play"||it.type==="notify")?"호출 없음":`예상 ${itemCalls(it)}회`;
    const sb=card.querySelector(".bsub");if(sb){sb.innerHTML=t.html;sb.title=t.plain;}}
  // 마지막 값 한 번만 보낸다. 응답에 일련번호를 달아 늦게 온 옛 응답이 새 값을 덮지 않게 한다.
  clearTimeout(Q.editT[id]);Q.editT[id]=setTimeout(async()=>{const body={op:"update",id};if(patch.count!=null)body.count=it.count;if(patch.target!=null)body.target=it.target;if(patch.collect!=null)body.collect=it.collect;
    const seq=Q.seq[id]=(Q.seq[id]||0)+1;
    const r=await api("/api/queue",body);
    if(seq!==Q.seq[id])return;   // 그 사이 더 새 수정이 나갔다 — 이 응답은 버린다
    if(r&&r.ok){delete Q.local[id];if(r.item)Object.assign(it,r.item);Q.prevKey="";if(r.state)applyState(r.state);else draw();return;}   // prevKey 를 비워야 새 수량을 반영한 미리보기를 다시 받는다
    const p=Q.local[id]&&Q.local[id]._prev;if(p){it.count=p.count;it.target=p.target;it.collect=p.collect;if(it.progress&&it.type==="gather")it.progress.passesPlanned=gpass(it.target||0);}delete Q.local[id];
    const code=r&&r.error;toast(code==="not_editable"?"실행 중이거나 끝난 항목은 수정할 수 없습니다":code==="max_passes"?`반복 상한을 넘습니다 — ${r.message||"캘 개수를 줄이거나 설정에서 상한을 올리세요"}`:code==="group_running"?OP_ERR.group_running:opErrTxt(r));draw();},250);}
/* 주의: 항목 객체(it)를 붙잡아 두면 안 된다. 폴링이 Q.data 를 통째로 새 객체로 바꾸므로
   붙잡아 둔 it 은 곧 낡은 사본이 되고, 길게 누르기가 그 낡은 값으로 같은 수를 계속 써서 값이 제자리걸음한다.
   항상 id 로 그때그때 찾는다. */
function bindQEdit(root){if(!root)return;root.querySelectorAll("[data-qe]").forEach((cell)=>{const id=cell.dataset.qe;
    const it0=findItem(id);if(!it0)return;const g=it0.type==="gather";
    const cur=()=>{const it=findItem(id);if(!it)return g?0:1;return g?(it.target||0):(it.count||1);};
    // 채집은 100 단위로 움직이고 100 아래로는 안 내려간다 (하한이 1 이면 바닥을 찍은 뒤 값이 전부 …01 이 된다).
    // 직접 입력은 1~99,999 를 그대로 받는다 — 이미 저장된 작은 값도 고칠 수 있어야 한다.
    cell.querySelectorAll("[data-qst]").forEach((b)=>{const n=Number(b.dataset.qst);holdBtn(b,()=>{if(!findItem(id))return;qEditApply(id,g?{target:gstepN(cur(),Math.sign(n))}:{count:cur()+n});});});
    const inp=cell.querySelector("[data-qin]");if(inp){inp.oninput=()=>{const v=Number(inp.value);if(v>=1)qEditApply(id,g?{target:v}:{count:v});};inp.onchange=()=>{inp.value=cur();};}});}

/* ── 재렌더 미루기 ──
   ① 스테퍼를 누르는 중(HOLDS) ② 수량 입력에 포커스 ③ 카드를 끄는 중 — 이 셋 동안은 다시 그리지 않는다 */
function bBusy(){if(B.drag)return true;if(typeof HOLDS!=="undefined"&&HOLDS.size)return true;
  const a=document.activeElement;return !!(a&&a.matches&&a.matches("[data-qin]"));}
function flushBoardRender(){if(Q.pendRender&&!bBusy()){Q.pendRender=false;draw();}}
window.addEventListener("mw:holdend",()=>setTimeout(flushBoardRender,0));
document.addEventListener("focusout",(e)=>{if(e.target&&e.target.matches&&e.target.matches("[data-qin]"))setTimeout(flushBoardRender,0);});

/* ── 스크롤 유지 ── 1초 폴링이 열 스크롤·회신 기록 스크롤을 맨 위로 되돌리지 않게 */
function keepScrolls(fn){const m=new Map();document.querySelectorAll("[data-sk]").forEach((el)=>m.set(el.dataset.sk,el.scrollTop));
  fn();
  document.querySelectorAll("[data-sk]").forEach((el)=>{const v=m.get(el.dataset.sk);if(v)el.scrollTop=v;});}

/* ── 카드 ── */
const typeBadge=(t)=>`<span class="badge bd-type ${QTYPE_CLS[t]||"warn"}">${esc(QTYPE_KO[t]||t||"")}</span>`;
function nameOf(it){return it.type==="collect"?`${(it.facility||(typeof facilityOf==="function"?facilityOf(it.name):"")||"(시설 미상)")} 수령`
  :it.type==="notify"?(it.text||it.name||"알림"):(it.name||"(이름 없음)");}
// 실행 중 카드의 단계 — 서버가 단계 필드를 주지 않으므로 종류에서 만든다(없는 필드를 지어내지 않는다)
const stageOf=(it)=>`${QTYPE_KO[it.type]||""} 중`;
// 낱장 카드 「다시 담기」 버튼 — 넓은 폭은 글자(그룹 카드와 같은 .pri), 좁은 폭·폰은 그룹 행과 같은 ⧉
const dupBtn=(id,narrow)=>narrow
  ?`<button class="act rt" data-op="card_duplicate" data-id="${esc(id)}" title="같은 설정으로 새 카드를 대기 열에 담습니다">⧉</button>`
  :`<button class="pri" data-op="card_duplicate" data-id="${esc(id)}" title="같은 설정으로 새 카드를 대기 열에 담습니다">다시 담기</button>`;
function cardHTML(it,o){o=o||{};const col=colOf(it);const st=it.status||"pending";const rep=o.rep||1;
  const t=txOf(it,rep);const pr=t.pr;const inner=!!o.inner;
  const isCur=!!o.current&&it.id===o.current;
  const cls=["bcard",st==="running"?"run":"",st==="done"?"done":"",st==="waiting"?"waiting":"",st==="error"?"err":""].filter(Boolean).join(" ");
  // 대기 열의 카드와, 대기로 되살릴 수 있는 완료·실패 카드만 끌 수 있다. 실행 중 카드는 러너의 것이다.
  // 그룹 안쪽(inner) 자식도 끌 수 있다 — 대기 열에 놓으면 그룹에서 빠진다. 러너가 도는 동안에는 주지 않는다.
  const drag=inner?(!!o.pull&&!isCur&&col!=="run"):(!isCur&&col!=="run");
  const h=[];
  h.push(`<div class="${cls}" data-id="${esc(it.id)}" data-col="${col}"${inner?` data-parent="${esc(o.gid||"")}"`:""}${drag?' draggable="true"':""}>`);
  h.push(`<div class="l1">${typeBadge(it.type)}${collectTag(it)}<span class="grow"></span>`
    +(inner?`<span class="qty">${esc(qtyTxt(it))}</span>`:(it.type==="play"||it.type==="notify")?`<span class="est" title="게임을 부르지 않습니다 — 날개 0">호출 없음</span>`:`<span class="est" title="게임 호출 예상 회수">예상 ${itemCalls(it)}회</span>`)
    // 삭제는 상태와 관계없이 언제나 할 수 있다 — 작업 중·가공 대기·그룹 안 자식도 포함.
    // 화면이 미리 막지 않는다. 서버가 어떤 상태에서도 받아 준다. 실행 중 카드만 bindOps 가 한 번 확인한다
    +`<button class="del" data-op="remove" data-id="${esc(it.id)}" title="${isCur?"삭제 (지금 실행 중)":"삭제"}" draggable="false">×</button>`+`</div>`);
  // 수량: 고칠 수 있으면 스테퍼 행, 못 고치면 **이름 줄 오른쪽에 글자로**. 완료 카드에 빈 스테퍼 행을
  // 두면 「100개 … 개」처럼 단위가 두 번 나오고 (qtyTxt 가 이미 단위를 붙인다) 카드만 길어진다.
  const qEdit=!inner&&qtyEditable(it);
  h.push(`<div class="nmrow"><span class="nm" title="${esc(nameOf(it))}">${esc(nameOf(it))}</span>`
    +(!inner&&!qEdit?`<span class="nqty">${esc(qtyTxt(it))}</span>`:"")+`</div>`);
  if(qEdit)h.push(qtyCell(it));
  if(st==="running"){const t0=cardStart(it);
    h.push(`<div class="brun"><div class="l"><span class="stage"><span class="dot blink"></span>${esc(stageOf(it))}</span>`
      +elapsedHTML(t0,"el")+`</div>`
      +`<div class="bbar"><i style="width:${pr.pct!=null?pr.pct:0}%"></i></div></div>`);}
  else if(pr.pct!=null&&!inner&&st!=="error")h.push(`<div class="bbar ${st==="done"?"done":st==="waiting"?"warn":""}"><i style="width:${pr.pct}%"></i></div>`);   // 실패 카드는 오류 배지가 이미 상태를 말한다 — 빈 막대를 덧붙이지 않는다
  if(st==="waiting")h.push(`<div class="bwait"><span class="k">가공 대기</span><span class="v mono" data-qw="${esc(it.name||"")}">${esc(waitTxt(it.name))}</span></div>`);
  if(st==="error"){const hint=errHint(it,"대기로");
    // stuck = 같은 오류가 연달아 나서 남은 회차에서 뺀 카드. **왜 다시 안 도는지 적어 준다** —
    // 안 적으면 「회차가 남았는데 왜 멈춰 있지」가 된다 (실패한 실행도 날개를 쓰기에 뺀 것이다).
    h.push(`<div class="berr"><div class="top"><span class="code">${esc(it.error||"error")}</span>${APP_ERRS.has(it.error)?'<span class="appchk">앱 검사</span>':""}${it.stuck?`<span class="appchk">${it.streak||0}회 연속 · 회차 제외</span>`:""}</div>`
      +`<span class="msg">${esc(it.message||"")}</span>${it.stuck?'<span class="ehint">같은 오류가 반복돼 남은 회차에서 뺐습니다 (실패해도 날개를 씁니다). 원인을 고친 뒤 「대기로」 되돌리세요</span>':hint?`<span class="ehint">${esc(hint)}</span>`:""}</div>`);}
  h.push(`<span class="bsub" title="${esc(t.plain)}">${t.html}</span>`);
  // 발: 대기로 되돌리기. 그룹 안에서는 실패한 자식만 — 끝난 자식을 되돌리면 이번 회차의 진행이 어긋난다
  // 「다시 담기」 = 같은 설정으로 새 카드를 대기 열에 담는다 (card_duplicate) — 그룹 카드의 「다시 담기」와 같은 자리·같은 꼴.
  // 그룹 안 자식에는 두지 않는다 — 끝난 그룹은 그룹의 「다시 담기」로 통째로 담는다
  if(col==="fail"||(col==="done"&&!inner))
    h.push(`<div class="bacts"><span class="grow"></span><button data-op="reset" data-id="${esc(it.id)}" title="대기 열로 되돌립니다">↺ 대기로</button>`
      +(!inner?dupBtn(it.id,false):"")+`</div>`);
  h.push("</div>");return h.join("");}

/* ── 안쪽 2×2 칸반 ──
   「작업 중」 열의 펼친 그룹 카드와, 대기·완료·실패 열 그룹의 인라인 펼침이 같은 것을 쓴다.
   pull=true 면 자식에 draggable 이 붙는다 — 대기 열로 끌어내면 그룹에서 빠진다. */
function groupInnerHTML(g,d,pull){const kids=(g.items||[]).filter(Boolean);const rep=Number(g.repeat)||1;
  const inner={wait:[],run:[],done:[],fail:[]};for(const c of kids)(inner[colOf(c)]||inner.wait).push(c);
  const h=['<div class="gin">'];
  for(const [k,ko] of COLS){const list=inner[k];
    h.push(`<div class="gin-c" data-col="${k}"><div class="gin-h"><span class="dot${k==="run"?" blink":""}" style="background:${DOT[k]}"></span>`
      +`<span class="t">${ko}</span><span class="n">${fmtN(list.length)}</span></div>`
      +`<div class="gin-b" data-sk="gin:${esc(g.id)}:${k}">`
      +(list.length?list.map((c)=>cardHTML(c,{inner:true,rep,current:d&&d.current,pull,gid:g.id})).join(""):"")
      +`</div></div>`);}
  h.push("</div>");return h.join("");}
/* 자식을 끌어 뺄 수 있는가. 러너가 도는 동안에는 안 된다 —
   서버도 move_item 을 busy/group_running 으로 거절한다. 거절하면 사유를 토스트로 보여 준다. */
const canPull=(g,d)=>!(d&&d.running)&&g.status!=="running";

/* 완료 열 그룹 카드의 「실패 n」. 마지막 회차의 마지막 자식이 「오류 시 정지」로
   멈춘 그룹은 남은 항목이 없어 완료 열에 선다 — 접힌 카드만 보면 실패가 안에 숨는다. 서버가 실은 failBadge 를
   믿고, 없으면 같은 규칙(workqueue.group_fail_badge)으로 센다: 완료 열 그룹일 때만 · 안쪽 실패 열의 수. */
function groupFailBadge(g){if(!g||!isGroup(g))return 0;if(g.failBadge!=null)return Number(g.failBadge)||0;
  if(colOf(g)!=="done")return 0;return (g.items||[]).filter((c)=>c&&colOf(c)==="fail").length;}
/* 화면에 다는 수 — 완료 열은 위 규칙 그대로, **대기 열 그룹도** 안쪽 실패 열의 수를 단다.
   「오류 시 정지」로 멈춘 그룹은 남은 자식이 있어 대기 열에 선다 — 접힌 카드만 보면 실패한 자식이 거기서도 숨는다.
   groupFailBadge(서버 failBadge 와 짝, viewmodel 동치 검사 대상)는 건드리지 않는다: 서버가 대기 열에 0 을 싣기
   때문이다. 서버(workqueue.group_fail_badge)가 대기 열까지 세게 바뀌면 이 갈래는 그 값과 같아진다. */
function groupFailShown(g){const n=groupFailBadge(g);if(n)return n;if(!g||!isGroup(g)||colOf(g)!=="wait")return 0;
  return (g.items||[]).filter((c)=>c&&colOf(c)==="fail").length;}
function failBadgeHTML(g){const n=groupFailShown(g);
  return n?`<span class="badge bad gfail" title="그룹 안에서 실패한 항목 ${fmtN(n)}개 — 펼쳐서 봅니다">실패 ${fmtN(n)}</span>`:"";}

/* ── 그룹 카드 (접힘) — 대기·완료·실패 열. 「▸ 펼치기」로 그 자리에서 2×2 를 연다 ── */
function groupCollapsedHTML(g,d){const col=colOf(g);const sm=g.summary||{};const st=g.status||"pending";
  const open=Q.open.has(g.id);
  const cls=["bcard","gr",open?"open":"",st==="done"||col==="done"?"done":"",col==="fail"?"err":""].filter(Boolean).join(" ");
  const h=[];
  h.push(`<div class="${cls}" data-id="${esc(g.id)}" data-col="${col}" data-group="1"${col==="run"?' data-locked="1"':' draggable="true"'}>`);
  h.push(`<div class="l1">${typeBadge("group")}<span class="rep" title="회차 수">×${fmtN(Number(g.repeat)||1)}</span><span class="grow"></span>`
    +`<span class="cnt">${fmtN(sm.count!=null?sm.count:(g.items||[]).length)}항목</span>`
    +`<button class="cog" data-op="settings" data-id="${esc(g.id)}" title="그룹 설정 (이름·회차·수량)">⚙</button>`
    +`<button class="del" data-op="remove" data-id="${esc(g.id)}" title="그룹 삭제" draggable="false">×</button>`+`</div>`);
  // 「실패 n」 배지는 이름 줄에 — 머리줄(종류·회차·수·⚙·×)은 좁은 열에서 이미 꽉 차 배지가 카드 밖으로 밀려났다
  h.push(`<div class="nmrow"><span class="nm" title="${esc(g.name||"")}">${esc(g.name||"그룹")}</span>${failBadgeHTML(g)}</div>`);
  if(sm.line)h.push(`<span class="line" title="${esc(sm.line)}">${esc(sm.line)}</span>`);
  if(col==="fail"&&g.error){const hint=errHint(g,"대기로");
    h.push(`<div class="berr"><div class="top"><span class="code">${esc(g.error)}</span></div><span class="msg">${esc(g.message||"")}</span>${hint?`<span class="ehint">${esc(hint)}</span>`:""}</div>`);}
  // 펼침: 카드 안에 「작업 중」 열과 같은 2×2 를 그대로 보여 준다. 대기 열 그룹이면 자식을 끌어 뺄 수 있다
  if(open)h.push(groupInnerHTML(g,d,col==="wait"&&canPull(g,d)));
  h.push(`<span class="bnote">${open&&col==="wait"?"자식을 대기 열로 끌면 그룹에서 빠집니다":"수량은 그룹 설정(⚙)에서"}</span>`);
  h.push(`<div class="bacts"><button class="open" data-op="expand" data-id="${esc(g.id)}" title="${open?"항목 목록을 접습니다":"이 카드 안에서 항목을 봅니다 (설정은 ⚙)"}">${open?"▾ 접기":"▸ 펼치기"}</button><span class="grow"></span>`
    +(col==="done"?`<button class="pri" data-op="group_duplicate" data-id="${esc(g.id)}" title="같은 그룹을 새로 만들어 대기 열에 담습니다">다시 담기</button>`:"")
    +(col==="fail"?`<button data-op="reset" data-id="${esc(g.id)}" title="대기 열로 되돌립니다">↺ 대기로</button>`:"")+`</div>`);
  h.push("</div>");return h.join("");}

/* ── 그룹 카드 (펼침) — 작업 중 열. 안쪽 4열을 2×2 로 접어 넣는다 ── */
function groupExpandedHTML(g,d){const sm=g.summary||{};const rep=Number(g.repeat)||1;const loop=Number(g.loop)||0;
  const kids=(g.items||[]).filter(Boolean);
  /* 「작업 중」 열 그룹은 러너의 것이라 자식을 끌 수 없다. 다만 가공 waiting 자식이 남아
     그룹이 이 열에 걸려 있는데 러너는 멈춰 있는 실제 상황이 있어, 그때만 canPull 이 참이 된다. */
  const pull=canPull(g,d);
  const inner={wait:[],run:[],done:[],fail:[]};for(const c of kids)(inner[colOf(c)]||inner.wait).push(c);
  const rd=sm.roundDone!=null?sm.roundDone:inner.done.length,rt=sm.roundTotal!=null?sm.roundTotal:kids.length;
  const h=[];
  h.push(`<div class="gexp" data-id="${esc(g.id)}" data-group="1" data-locked="1">`);
  // 머리: 그룹 배지 · 이름 · ⚙ / N항목 · 회차 스테퍼 · 회차 N / M
  h.push(`<div class="gexp-h"><div class="r">${typeBadge("group")}<span class="nm" title="${esc(g.name||"")}">${esc(g.name||"그룹")}</span><span class="grow"></span>`
    +`<button class="cog" data-op="settings" data-id="${esc(g.id)}" title="그룹 설정">⚙</button>`
    +`<button class="del" data-op="remove" data-id="${esc(g.id)}" title="그룹 삭제 (실행 중이어도 됩니다)" draggable="false">×</button></div>`);
  h.push(`<div class="r"><span class="cnt">${fmtN(sm.count!=null?sm.count:kids.length)}항목</span><span class="grow"></span>`
    +`<span class="stepper" data-ge="${esc(g.id)}"><button data-gst="-1" title="회차 −1 (실행 중에는 늘리기만)">−</button>`
    +`<input class="v" type="number" data-gin min="1" max="20" value="${rep}" title="회차 수 1~20"><button data-gst="1" title="회차 +1">+</button></span>`
    +`<span class="loop">회차 ${fmtN(Math.max(1,loop||1))} / ${fmtN(rep)}</span></div></div>`);
  // 회차 진행 막대: 지난 회차는 꽉 찬 초록, 이번 회차는 roundDone/roundTotal
  const segs=[];for(let i=1;i<=Math.min(rep,20);i++){
    if(loop&&i<loop)segs.push('<i class="done"></i>');
    else if(loop&&i===loop)segs.push(`<i><b style="width:${rt?Math.min(100,Math.round(rd/rt*100)):0}%"></b></i>`);
    else segs.push("<i></i>");}
  h.push(`<div class="gexp-p"><div class="segs">${segs.join("")}</div>`
    +`<span class="rt"><span class="a">${fmtN(rd)}/${fmtN(rt)}</span>${sm.totalDone!=null?` · 누적 <span class="b">${fmtN(sm.totalDone)}</span>`:""}</span></div>`);
  h.push(groupInnerHTML(g,d,pull));   // 안쪽 2×2 칸반 (대기 열 인라인 펼침과 같은 것)
  // 발
  h.push(`<div class="gexp-f"><span class="bnote">대기 비면 다음 회차 자동</span><span class="grow"></span>`
    +`<button data-op="group_dissolve" data-id="${esc(g.id)}" title="자식 카드를 그룹 자리에 펼치고 그룹을 지웁니다">해체</button>`
    +`<button data-op="group_duplicate" data-id="${esc(g.id)}" title="같은 그룹을 하나 더 만듭니다">복제</button></div>`);
  h.push("</div>");return h.join("");}

/* ── 열 ── */
const DOT={wait:"var(--sub)",run:"var(--run)",done:"var(--ok)",fail:"var(--bad)"};

/* 좁은 폭의 세그먼트 칩 — 칸반 4열을 한 번에 한 칸으로.
   **열 자체는 넷 그대로 그린다.** 드롭 판정(.bcol-b[data-drop])과 검사가 그 구조를 보므로,
   숨기는 것은 CSS 가 한다. 여기서는 어느 칸을 고를지만 정한다.
   고른 칸이 비면 자동으로 옮기지 않는다 — 사람이 고른 것을 화면이 뒤집으면 더 헷갈린다. */
/* 나머지 3열은 3등분 탭 · 탭 색 점은 칸반 열 머리의 점과 같음 · 실패가 생기면 실패 탭 글자가 빨강.
   한 테두리 안에 1fr 씩 · 높이 30 · 점 5px. 넷을 다 탭으로 만들면 **지금 뭐가 도는지 보려고 탭을 눌러야 한다** —
   도는 것은 늘 보여야 하는 하나뿐인 정보라 「작업 중」은 위에 고정하고(kbRunHTML) 탭은 셋이다. */
function segsHTML(by){const cur=TAB_COLS.some(([k])=>k===B.seg)?B.seg:"wait";
  return '<div class="kb-tabs" role="tablist">'+TAB_COLS.map(([k,ko])=>{const n=by[k].length;
    const cls=[cur===k?"on":"",k==="fail"&&n?"bad":""].filter(Boolean).join(" ");
    return `<button data-seg="${k}"${cls?` class="${cls}"`:""} role="tab" aria-selected="${cur===k}"><i class="sd" style="background:${DOT[k]}"></i>${ko} <b>${fmtN(n)}</b></button>`;}).join("")+"</div>";}
function bindSegs(root){if(!root)return;root.querySelectorAll("[data-seg]").forEach((b)=>{
  b.onclick=()=>{B.seg=b.dataset.seg;lsSet("mw.boardSeg",B.seg);draw();};});}

/* ── 좁은 폭 (칸반 접힘) ──
   **새로 짓는다.** 위의 columnHTML·cardHTML 은 넓은 폭 것이고, 여기서는 데이터 함수
   (txOf·itemCalls·qtyCell·errHint…)만 같이 쓴다. 조작 갈고리는 같다 — .bcard[data-id][data-col] · [data-op] ·
   [data-qe] · .bcol-b[data-drop] — 그래서 bindOps·bindQEdit·bindDrag 가 그대로 듣는다.
   구조: [배너] → [작업 중 슬롯] → [탭 셋] (#bsegs) / [고른 열의 행들] (#board) / [검색 · 작업 추가 · ⋯] (#boardFoot) */
// 체인 정지 배너 — 작업 중 슬롯 **위**. stopReason 이 객체(치명 오류)일 때만. viewmodel.chain_banner 와 짝
function kbBannerHTML(sr){const code=sr.error||"";const kind=code==="blocked"?blockedKind(sr):"";
  const msg=errHint(sr,"▶ 시작")||sr.message||"";
  return `<div class="kb-banner" role="alert"><span class="bar"></span><span class="tx"><span class="hd"><span class="code">${esc(code)}${kind?` (${esc(kindKo(kind))})`:""}</span><span class="t">체인 정지</span></span><span class="m">${esc(msg)}</span></span></div>`;}
// 한 줄 행 (종류 · 이름/예상·회당 · 스테퍼(단위 포함) · ✕ · 약 46px). 담기 서랍의 행과 같은 꼴
function kbRowHTML(it,o){o=o||{};const col=colOf(it);const st=it.status||"pending";const rep=o.rep||1;const inner=!!o.inner;
  const t=txOf(it,rep);const pr=t.pr;const p=it.progress||{};const isCur=!!o.current&&it.id===o.current;
  const drag=inner?(!!o.pull&&!isCur&&col!=="run"):(!isCur&&col!=="run");
  const cls=["bcard","kb-row",st==="running"?"run":"",st==="done"?"done":"",st==="waiting"?"waiting":"",st==="error"?"err":""].filter(Boolean).join(" ");
  // 둘째 줄: 대기면 「예상 N회 · 회당 최대 100」, 돌면 단계·경과·진행, 실패면 코드·안내
  let note,noteCls="";
  if(st==="error"){const hint=errHint(it,"↻");note=`${it.error||"error"}${it.stuck?` · ${it.streak||0}회 연속 · 회차 제외`:""} · ${hint||it.message||""}`;noteCls="bad";}
  else if(st==="running")note=`<i class="sd blink"></i>${esc(stageOf(it))} · ${elapsedHTML(cardStart(it))} · ${esc(pr.txt)}`;
  // 칸·수령을 센 가공(N6)은 그 줄도 — 「가공 대기 · 등록 14/63 · 수령 7 · 칸 5/7 · 04:12 · 21:30 완료」
  else if(st==="waiting")note=`<i class="sd warn"></i>가공 대기 · ${(p.cap!=null||p.got)?`${esc(pr.txt)} · `:""}<span data-qw="${esc(it.name||"")}">${esc(waitTxt(it.name))}</span>`;
  // 채집 대기: 「예상 3회 · 날개 15 · 가방 47 → 목표 297」 (가방을 모르면 「… · 회당 최대 100」)
  else if(st==="pending"&&it.type==="gather"){const n=itemCalls(it);const h=p.have;
    note=`${inner?'<i class="sd"></i>대기 · ':""}예상 ${fmtN(n)}회 · 날개 ${fmtN(n*5)} · ${esc(h!=null?`가방 ${fmtN(h)} → 목표 ${fmtN(h+(it.target||0))}`:t.sub)}`;}
  else if(st==="pending")note=`${inner?'<i class="sd"></i>대기 · ':(it.type==="play"||it.type==="notify")?"호출 없음 · ":`예상 ${fmtN(itemCalls(it))}회 · `}${esc(t.sub||pr.txt)}`;
  else note=esc(t.plain);
  const noteHtml=(st==="error")?esc(note):note;
  const qEdit=qtyEditable(it);
  const meta=st==="error"&&it.type==="gather"?`${fmtN(p.passes||0)}/${fmtN(p.passesPlanned||"?")}회`:qtyTxt(it);
  const acts=[];
  if(col==="fail")acts.push(`<button class="act rt" data-op="reset" data-id="${esc(it.id)}" title="대기 열로 되돌립니다">↻</button>`);
  if((col==="done"||col==="fail")&&!inner)acts.push(dupBtn(it.id,true));
  if(st==="running"&&inner)acts.push(`<button class="act stop" data-op="stopboard" data-id="${esc(it.id)}" title="보드를 정지합니다 (진행 중인 동작이 끝나면)">■</button>`);
  else acts.push(`<button class="act del" data-op="remove" data-id="${esc(it.id)}" title="${isCur?"삭제 (지금 실행 중)":"삭제"}" draggable="false">✕</button>`);
  const ctag=collectTag(it);const nmHtml=`<span class="nm" title="${esc(nameOf(it))}">${esc(nameOf(it))}</span>`;
  return `<div class="${cls}" data-id="${esc(it.id)}" data-col="${col}"${inner?` data-parent="${esc(o.gid||"")}"`:""}${drag?' draggable="true"':""}>`
    +typeBadge(it.type)
    +`<span class="kb-nm">${ctag?`<span class="nml">${nmHtml}${ctag}</span>`:nmHtml}<span class="bsub ${noteCls}" title="${esc(t.plain)}">${noteHtml}</span></span>`
    +(qEdit?qtyCell(it):`<span class="kb-meta${st==="error"?" bad":""}">${esc(meta)}</span>`)
    +`<span class="kb-act">${acts.join("")}</span></div>`;}
// 탭 안의 그룹 행 (접힘). ▸ 로 그 자리에서 자식 행을 펼친다 (인라인 펼침의 좁은 폭 판)
function kbGroupRowHTML(g,d){const col=colOf(g);const sm=g.summary||{};const st=g.status||"pending";const open=Q.open.has(g.id);
  const kids=(g.items||[]).filter(Boolean);const rep=Number(g.repeat)||1;
  const cls=["bcard","kb-row","gr",open?"open":"",col==="done"?"done":"",col==="fail"?"err":""].filter(Boolean).join(" ");
  let note,noteCls="";
  if(col==="fail"&&g.error){note=`${g.error} · ${errHint(g,"↻")||g.message||""}`;noteCls="bad";}
  else if(st==="stopped"&&g.message){note=g.message;}
  else note=`×${fmtN(rep)} · ${fmtN(sm.count!=null?sm.count:kids.length)}항목${sm.line?` · ${sm.line}`:""}`;
  const acts=[`<button class="act" data-op="expand" data-id="${esc(g.id)}" title="${open?"항목 목록을 접습니다":"이 자리에서 항목을 봅니다"}">${open?"▾":"▸"}</button>`,
    `<button class="act" data-op="settings" data-id="${esc(g.id)}" title="그룹 설정 (이름·회차·수량)">⚙</button>`];
  if(col==="fail")acts.push(`<button class="act rt" data-op="reset" data-id="${esc(g.id)}" title="대기 열로 되돌립니다">↻</button>`);
  if(col==="done")acts.push(`<button class="act rt" data-op="group_duplicate" data-id="${esc(g.id)}" title="같은 그룹을 새로 만들어 대기 열에 담습니다">⧉</button>`);
  acts.push(`<button class="act del" data-op="remove" data-id="${esc(g.id)}" title="그룹 삭제" draggable="false">✕</button>`);
  const h=[`<div class="${cls}" data-id="${esc(g.id)}" data-col="${col}" data-group="1"${col==="run"?' data-locked="1"':' draggable="true"'}>`
    +typeBadge("group")
    // 「실패 n」 배지는 **이름 칸 안**(이름 줄 오른쪽)에 둔다. 행은 4칸 격자(종류 · 이름/상태 · 수 · 동작)라
    // 배지를 칸으로 넣으면 5번째 칸이 되어 격자가 무너졌다 — 행 94px · 종류 칩이 늘어남 · 동작이 둘째 줄
    +`<span class="kb-nm"><span class="nml"><span class="nm" title="${esc(g.name||"")}">${esc(g.name||"그룹")}</span>${failBadgeHTML(g)}</span><span class="bsub ${noteCls}" title="${esc(note)}">${esc(note)}</span></span>`
    +`<span class="kb-meta">${fmtN(kids.length)}항목</span><span class="kb-act">${acts.join("")}</span></div>`];
  if(open){const pull=col==="wait"&&canPull(g,d);
    h.push(`<div class="kb-kids gin-b" data-sk="gin:${esc(g.id)}">${kids.map((c)=>kbRowHTML(c,{inner:true,rep,current:d&&d.current,pull,gid:g.id})).join("")||'<span class="bempty">비어 있음</span>'}</div>`);}
  return h.join("");}
// 작업 중 슬롯의 낱장 카드 (종류 · 이름 · 진행 / 막대 / 부제)
function kbCurHTML(it,d){const st=it.status||"pending";const t=txOf(it,1);const pr=t.pr;const t0=cardStart(it);
  return `<div class="bcard kb-cur ${st==="waiting"?"waiting":"run"}" data-id="${esc(it.id)}" data-col="run">`
    +`<div class="l1">${typeBadge(it.type)}${collectTag(it)}<span class="nm" title="${esc(nameOf(it))}">${esc(nameOf(it))}</span><span class="grow"></span>`
    +(st==="waiting"?`<span class="st mono" data-qw="${esc(it.name||"")}">${esc(waitTxt(it.name))}</span>`:elapsedHTML(t0,"st mono"))
    +`<button class="del" data-op="remove" data-id="${esc(it.id)}" title="삭제 (지금 실행 중)" draggable="false">×</button></div>`
    +`<div class="bbar${st==="waiting"?" warn":""}"><i style="width:${pr.pct!=null?pr.pct:0}%"></i></div>`
    +`<span class="bsub" title="${esc(t.plain)}">${esc(t.plain)}</span></div>`;}
/* 작업 중 슬롯의 그룹 카드 — 작업 중 슬롯에 통째로 (반복 스테퍼 · 회차 · 진행 · 누적 /
   안쪽 4열은 4등분 탭 줄로 접고 그 아래 자식 행 / 하단 해체 · 복제).
   4등분 줄은 **열 머리를 접은 것**이다 — 걸러 보이는 탭이 아니라 수를 보여 주는 줄이고, 그 아래에는 자식이
   **전부** 순서대로 온다 (행 수 = 자식 수). 지금 일이 있는 열이 밝다. */
function kbGroupHTML(g,d){const sm=g.summary||{};const rep=Number(g.repeat)||1;const loop=Number(g.loop)||0;
  const kids=(g.items||[]).filter(Boolean);const pull=canPull(g,d);
  const inner={wait:[],run:[],done:[],fail:[]};for(const c of kids)(inner[colOf(c)]||inner.wait).push(c);
  const rd=sm.roundDone!=null?sm.roundDone:inner.done.length,rt=sm.roundTotal!=null?sm.roundTotal:kids.length;
  const lit=inner.run.length?"run":inner.wait.length?"wait":inner.fail.length?"fail":"done";
  const h=[];
  h.push(`<div class="kb-g" data-id="${esc(g.id)}" data-group="1" data-locked="1">`);
  h.push(`<div class="h">${typeBadge("group")}<span class="nm" title="${esc(g.name||"")}">${esc(g.name||"그룹")}</span><span class="cnt">${fmtN(sm.count!=null?sm.count:kids.length)}항목</span><span class="grow"></span>`
    +`<button class="cog" data-op="settings" data-id="${esc(g.id)}" title="그룹 설정">⚙</button>`
    +`<button class="del" data-op="remove" data-id="${esc(g.id)}" title="그룹 삭제 (실행 중이어도 됩니다)" draggable="false">×</button></div>`);
  h.push(`<div class="r"><span class="stepper" data-ge="${esc(g.id)}"><button data-gst="-1" title="회차 −1 (실행 중에는 늘리기만)">−</button>`
    +`<input class="v" type="number" data-gin min="1" max="20" value="${rep}" title="회차 수 1~20"><button data-gst="1" title="회차 +1">+</button></span>`
    +`<span class="loop">회차 ${fmtN(Math.max(1,loop||1))} / ${fmtN(rep)}</span>`
    +`<span class="bar"><i style="width:${rt?Math.min(100,Math.round(rd/rt*100)):0}%"></i></span>`
    +`<span class="rt mono">${fmtN(rd)}/${fmtN(rt)}${sm.totalDone!=null?` · 누적 ${fmtN(sm.totalDone)}`:""}</span></div>`);
  h.push('<div class="kb-gtabs">'+COLS.map(([k,ko])=>`<span class="c${lit===k?" on":""}" data-col="${k}"><i class="sd" style="background:${DOT[k]}"></i>${ko} ${fmtN(inner[k].length)}</span>`).join("")+"</div>");
  h.push(`<div class="kb-kids gin-b" data-sk="gin:${esc(g.id)}">${kids.map((c)=>kbRowHTML(c,{inner:true,rep,current:d&&d.current,pull,gid:g.id})).join("")||'<span class="bempty">비어 있음</span>'}</div>`);
  h.push(`<div class="f"><span class="bnote">대기 비면 다음 회차 자동</span><span class="grow"></span>`
    +`<button data-op="group_dissolve" data-id="${esc(g.id)}" title="자식 카드를 그룹 자리에 펼치고 그룹을 지웁니다">해체</button>`
    +`<button data-op="group_duplicate" data-id="${esc(g.id)}" title="같은 그룹을 하나 더 만듭니다">복제</button></div>`);
  h.push("</div>");return h.join("");}
/* 연주 대기 카드 — 연주 때문에 러너가 기다리는 중. 왜 시작하지 않는지 화면에 보여야 한다. 서버의 hold(GET /api/queue.hold)를
   그대로 그린다 — 작업 중 칸 **맨 위**. 우선(music·song·work)이 무엇이든 같은 카드다 — 갈래는 서버의 hold.kind·text 에만 있다.
   큐 항목이 아니다: id 도, 끌기도, 삭제도 없다. 제목·경과는 실행 중 머리와 같은 꼴, 둘째 줄이 「언제 시작하는가」(서버 text:
   전체 재생 → 끝나면 (n곡 남음) · 한 곡/「이 곡」 우선 → 이 곡이 끝나면 · 게임에서 튼 것 → 끝나면 · 「작업」 우선의 합주·인사말 →
   합주/인사말이 끝나면). 넓은 폭·좁은 폭(폰) 같은 함수. */
const holdOf=(d)=>d&&d.running&&d.hold&&typeof d.hold==="object"?d.hold:null;
function holdCardHTML(h){const title=h.title||"제목 미상";
  return `<div class="bcard kb-hold" data-hold="1" data-col="run" role="status"><div class="l1"><span class="badge bd-type hold">♪ 연주</span>`
    +`<span class="nm" title="${esc(title)}">${esc(title)}</span><span class="grow"></span>${elapsedHTML(h.started,"st mono")}</div>`
    +`<span class="bsub" title="${esc(h.text||"")}">${esc(h.text||"연주가 끝나면 시작")}</span></div>`;}
// 작업 중 슬롯 (radius 11 · 머리 28px · 도는 중이면 파란 테두리·점 깜빡임 · 비면 안내 한 줄). 연주 대기면 머리가 「연주 대기」·주황
function kbRunHTML(by,d){const list=by.run;const live=!!d.running;const hold=holdOf(d);const t0=live?runStart():0;const ng=list.filter(isGroup).length;
  return `<div class="kb-run${live?" live":""}${hold?" hold":""}" data-col="run"><div class="kb-run-h"><i class="sd${live?" blink":""}"></i><b>${hold?"연주 대기":"작업 중"}</b><span class="n mono">${fmtN(list.length)}</span><span class="grow"></span>`
    +(hold?elapsedHTML(hold.started,"t mono"):ng?`<span class="t">그룹 ${fmtN(ng)}장</span>`:live?elapsedHTML(t0,"t mono"):"")+`</div>`
    +`<div class="kb-run-b" data-sk="kb:run">${hold?holdCardHTML(hold):""}${list.length?list.map((it)=>isGroup(it)?kbGroupHTML(it,d):kbCurHTML(it,d)).join(""):hold?"":'<div class="bempty">게임은 한 번에 하나만 합니다 · 시작하면 여기에 카드가 옵니다</div>'}</div></div>`;}
// 고른 탭의 열 — 행이 한 상자(radius 11)에 이어 붙는다. 드롭 판정은 넓은 폭과 같은 .bcol-b[data-drop]
function kbColumnHTML(key,list,d){const body=[];
  for(const it of list)body.push(isGroup(it)?kbGroupRowHTML(it,d):kbRowHTML(it,{current:d.current}));
  if(key==="wait"&&list.length>=2)body.push('<div class="bhint pc-hint">카드 <b>사이</b>로 끌면 순서가 바뀝니다 · 카드 <b>가운데</b>에 겹치면 그룹이 됩니다</div>');
  if(key==="wait"&&!list.length)body.push('<div class="bhint">아래 「＋ 작업 추가」로 채집·제작·가공을 담으세요</div>');
  if(!list.length&&key==="fail")body.push('<div class="bempty">그룹 안의 개별 실패는 그룹 카드 안 「실패」 열에 남고, blocked·연결 끊김이면 그룹 전체가 여기로</div>');
  if(!list.length&&key==="done")body.push('<div class="bempty">끝난 카드가 여기에 쌓입니다</div>');
  return `<section class="bcol" data-col="${key}"><div class="bcol-b" data-sk="col:${key}" data-drop="${key}">${body.join("")}</div></section>`;}
// 하단: 검색 · 작업 추가 · ⋯ (데스크톱 하단 바와 같은 순서 · 「날개·산출」「회신 기록」「새 그룹」「프리셋」은 ⋯ 메뉴로)
function kbFootHTML(){return `<button class="srch" data-op="search" title="담기 서랍을 엽니다"><span class="i">⌕</span><span class="lb">레시피·채집물 찾기</span></button>`
  +`<button class="small pri" data-op="adddrawer">＋ 작업 추가</button>`
  +`<button class="more" data-op="menu" title="더 보기 — 날개·산출 · 회신 기록 · 새 그룹 · 프리셋" aria-haspopup="menu">⋯</button>`;}
function lastStopT(d){const ev=(d&&d.events)||[];for(let i=ev.length-1;i>=0;i--)if(ev[i]&&ev[i].kind==="stop")return ev[i].t||"";
  const sr=d&&d.stopReason;return sr&&typeof sr==="object"?(sr.message||""):"";}
function drawNarrow(board,by,d){const sr=chainStop(d);const srObj=!!sr;
  // 체인 정지가 **새로** 떴을 때 한 번 실패 탭을 고른다. 그 뒤 사람이 옮기면 따르지 않는다.
  // **이 페이지를 연 동안에만** 고른다 — 저장(mw.boardSeg)은 사람이 누른 탭만(bindSegs). 전에는 여기서도 저장해
  // 다음 기동에서도 실패 탭이 먼저 열렸다
  // 열쇠 = 정지 사유 **글자** + 마지막 「stop」 이벤트 시각. 메시지(lastError)는 넣지 않는다 — 복원이 lastError 만
  // 지워도 새 정지로 보고 사람이 옮긴 탭을 다시 끌어갔다. 시각을 넣어 폴링 사이에 같은 코드로 또 선 것도 새 정지로 본다.
  const key=srObj?`${typeof d.stopReason==="string"?d.stopReason:sr.error||""}|${lastStopT(d)}`:"";
  if(key&&key!==Q.srKey)B.seg="fail";
  Q.srKey=key;
  if(!TAB_COLS.some(([k])=>k===B.seg))B.seg="wait";
  const top=$("bsegs");if(top)top.innerHTML=(srObj?kbBannerHTML(sr):"")+kbRunHTML(by,d)+segsHTML(by);
  board.innerHTML=kbColumnHTML(B.seg,by[B.seg],d);}
/* ⋯ 메뉴 (좁은 폭) — popover 라 레일·dialog 위에 뜬다. 여기서 만들어 body 에 붙인다 */
function kbMenuBox(){let el=$("kbMenu");
  if(!el){el=document.createElement("div");el.id="kbMenu";el.setAttribute("popover","manual");el.setAttribute("role","menu");el.hidden=true;document.body.appendChild(el);}
  return el;}
function kbMenuHide(){const el=$("kbMenu");if(!el)return;try{el.hidePopover();}catch{}el.hidden=true;}
function kbMenuShow(){const el=kbMenuBox();const d=Q.data||{};const ev=(d.events||[]).length;
  el.innerHTML=`<button data-op="stats" role="menuitem">날개·산출</button><button data-op="events" role="menuitem">회신 기록 <b>${fmtN(ev)}</b></button>`
    +`<button data-op="group_new" role="menuitem">＋ 새 그룹</button><button data-op="presets" role="menuitem">프리셋</button>`;
  el.querySelectorAll("[data-op]").forEach((b)=>{b.onclick=async()=>{kbMenuHide();const op=b.dataset.op;const MWx=window.MW||{};
    if(op==="stats"){Q.stOpen=true;Q.evOpen=false;draw();loadStats();return;}
    if(op==="events"){Q.evOpen=true;Q.stOpen=false;lsSet("mw.evOpen","1");draw();return;}
    if(op==="presets"){if(MWx.openPresets)MWx.openPresets();return;}
    if(op==="group_new"){const n=await askText("새 그룹 이름","새 그룹",{title:"새 그룹",maxLength:24,note:"1~24자",ok:"만들기"});if(n)await bop({op:"group_create",name:n.slice(0,24),ids:[]});}};});
  // 발 바로 위, 오른쪽 — 화면을 재서 놓는다 (하단 탭바가 아래 있으니 bottom 고정값은 안 맞는다)
  const ft=$("boardFoot");const r=ft&&ft.getBoundingClientRect?ft.getBoundingClientRect():null;
  el.style.bottom=r?`${Math.max(0,Math.round(window.innerHeight-r.top+6))}px`:"120px";
  el.hidden=false;try{el.showPopover();}catch{}
  setTimeout(()=>document.addEventListener("pointerdown",(e)=>{if(!el.contains(e.target))kbMenuHide();},{once:true}),0);}
function columnHTML(key,list,d){const ko=COL_KO[key];const n=(d.columns&&d.columns[key]!=null)?d.columns[key]:list.length;
  const hasExp=key==="run"&&list.some((x)=>isGroup(x));const hold=key==="run"?holdOf(d):null;   // 연주 대기 카드 — 작업 중 열 맨 위
  const h=[];
  h.push(`<section class="bcol${hold?" hold":""}" data-col="${key}"><div class="bcol-h"><span class="dot${key==="run"&&(list.length||hold)?" blink":""}" style="background:${hold?"var(--warn)":DOT[key]}"></span>`
    +`<span class="t">${ko}</span><span class="n">${fmtN(n)}</span><span class="grow"></span>`);
  if(key==="wait")h.push(`<button class="add" data-op="adddrawer" title="담기 서랍을 엽니다">+ 작업 추가</button>`);
  else if(key==="fail")h.push(`<span class="bnote">그룹은 체인 정지 때만</span>`);
  else if(key==="run"&&hasExp)h.push(`<span class="bnote">그룹 ${fmtN(list.filter(isGroup).length)}장</span>`);
  h.push("</div>");
  const body=[];
  if(hold)body.push(holdCardHTML(hold));
  for(const it of list)body.push(isGroup(it)?(key==="run"?groupExpandedHTML(it,d):groupCollapsedHTML(it,d)):cardHTML(it,{current:d.current}));
  // 드래그 안내. 「겹치면 그룹」만 적어 두면 순서를 바꿀 수 있다는 걸 알 길이 없다 — 순서를 먼저 적는다
  if(key==="wait"&&list.length>=2)body.push('<div class="bhint pc-hint">카드 <b>사이</b>로 끌면 순서가 바뀝니다<br>카드 <b>가운데</b>에 겹치면 그룹이 됩니다</div>');
  if(key==="wait"&&!list.length)body.push('<div class="bhint">아래 「+ 작업 추가」로<br>채집·제작·가공을 담으세요</div>');
  if(!list.length&&key==="fail")body.push('<div class="bempty">그룹 안의 개별 실패는<br>그룹 카드 안 「실패」 열에 남고<br>blocked·연결 끊김이면<br>그룹 전체가 여기로</div>');
  if(!list.length&&key==="run"&&!hold)body.push('<div class="bempty">게임은 한 번에 하나만 합니다<br>시작하면 여기에 카드가 옵니다</div>');
  if(!list.length&&key==="done")body.push('<div class="bempty">끝난 카드가 여기에 쌓입니다</div>');
  const center=!list.length&&key!=="wait"&&!hold;
  h.push(`<div class="bcol-b${center?" center":""}${hasExp?" hasgroup":""}" data-sk="col:${key}" data-drop="${key}">${body.join("")}</div></section>`);
  return h.join("");}

/* ── 실행 줄 ── */
function runbarHTML(d){const running=!!d.running;const connected=!!(S.cli&&S.cli.pipe==="connected");
  const items=rootItems();const sr=d.stopReason;const cs=chainStop(d);
  const canStart=!running&&items.some((x)=>colOf(x)==="wait")&&connected&&Q.avail!==false;
  const cg=d.currentGroup?findItem(d.currentGroup):null;
  const gName=cg?`「${cg.name||"그룹"}」 회차 ${fmtN(Number(cg.loop)||1)} / ${fmtN(Number(cg.repeat)||1)}`:"";
  const cur=d.current?findItem(d.current):null;const hold=holdOf(d);
  let state;
  if(hold)state=`<span class="rb-state hold"><span class="dot blink"></span>연주 대기 · ${esc(hold.title||"제목 미상")}</span>`;   // 연주가 끝나면 스스로 시작한다
  else if(running)state=`<span class="rb-state"><span class="dot blink"></span>실행 중${gName?` · ${esc(gName)}`:cur?` · ${esc(nameOf(cur))}`:""}</span>`;
  else if(cs)state=`<span class="rb-state bad">체인 정지 · ${esc(cs.error||"")}</span>`;
  else if(sr)state=`<span class="rb-state stop">${sr==="user"?"사용자가 정지했습니다":sr==="onError"?"오류 시 정지로 멈췄습니다":sr==="performance"?"연주가 시작돼 멈췄습니다 — 연주가 끝난 뒤 ▶ 시작으로 이어집니다":esc(String(sr))}</span>`;
  else if(!items.length)state='<span class="rb-state idle">비어 있음</span>';
  else state=`<span class="rb-state idle">대기 ${fmtN((d.columns&&d.columns.wait)||items.filter((x)=>colOf(x)==="wait").length)}장</span>`;
  const t0=hold?hold.started:running?runStart():0;   // 연주 대기 중의 경과 = 연주의 경과 (실행 시작 회신이 아직 없다)
  const have=wingsHave();const wings=Q.prev&&Q.prev.wings!=null?Q.prev.wings:null;
  const oe=d.onError||"continue";const ev=(d.events||[]).length;
  return `<button class="small pri" id="rbStart"${canStart?"":" disabled"} title="${canStart?"확인창을 거쳐 시작합니다":running?"이미 실행 중입니다":!connected?"게임에 연결되면 시작할 수 있습니다":"대기 열에 카드가 없습니다"}">▶ 시작</button>`
    +`<button class="small stopb" id="rbStop"${running?"":" disabled"}>■ 정지</button>`
    // 상태·경과는 .rb-mid 로 묶는다 — 넓은 폭에서는 display:contents 라 없는 것과 같고, 좁은 폭에서 오른쪽 세로 두 줄이 된다
    +`<span class="vl"></span><span class="rb-mid">${state}`
    +(running?`<span class="rb-k rb-el">경과 ${elapsedHTML(t0,"rb-v")}</span>`:"")+`</span>`   // 경과는 도는 동안만
    +`<span class="rb-k rb-cost">예상 소모 <span class="rb-v gold">${wings!=null?`정령의 날개 약 ${fmtN(wings)}`:"—"}</span> <span class="rb-v dim">/ 보유 ${have!=null?fmtN(have):"?"}</span></span>`
    +`<span class="grow"></span><span class="rb-k">오류 시</span>`
    +`<span class="seg" id="rbOnErr"><button data-v="continue"${oe==="continue"?' class="on"':""}${running?" disabled":""}>계속</button><button data-v="stop"${oe==="stop"?' class="on"':""}${running?" disabled":""}>정지</button></span>`
    +`<span class="rb-ev"><button class="small${Q.stOpen?" on":""}" id="rbStBtn" title="실행 명령마다 한 줄씩 쌓인 기록입니다 — 날개 예상 소모와 산출">날개·산출</button></span>`
    +`<span class="rb-ev"><button class="small${Q.evOpen?" on":""}" id="rbEvBtn" title="항목이 끝날 때마다 한 줄씩 남습니다">회신 기록 <span class="rb-v dim">${fmtN(ev)}</span></button></span>`;}
/* 날개·산출 / 회신 기록 패널 — **popover 로 띄운다** (top layer). 전에는 #runbar 안에 absolute 로 두었는데
   #runbar 가 overflow:hidden(넓은 폭)·가로 스크롤 상자(좁은 폭)라 제 안에서 잘리고, 레일(z-index 40)보다
   낮아 **다른 것 뒤로 숨었다**. top layer 는 z-index 를 안 탄다. */
function panelBox(){let el=$("rbPanel");
  if(!el){el=document.createElement("div");el.id="rbPanel";el.setAttribute("popover","manual");el.hidden=true;document.body.appendChild(el);}
  return el;}
function panelDraw(d){const el=panelBox();const want=Q.stOpen||Q.evOpen;
  if(!want){if(!el.hidden){try{el.hidePopover();}catch{}el.hidden=true;el.innerHTML="";}return;}
  el.innerHTML=Q.stOpen?stPanelHTML():evPanelHTML(d);
  // 실행 줄 바로 아래, 오른쪽 끝 — 창 크기가 바뀌어도 그릴 때마다 다시 잰다
  const rb=$("runbar");const r=rb&&rb.getBoundingClientRect?rb.getBoundingClientRect():null;
  el.style.top=r?`${Math.round(r.bottom+6)}px`:"60px";
  if(el.hidden){el.hidden=false;try{el.showPopover();}catch{}}}
// 이번 실행이 시작된 시각: 마지막 정지 이후로 거슬러 올라가 가장 이른 회차·시작 회신
// 도는 중인데 이번 응답에서 못 찾으면(회신 기록이 빠졌거나 잘렸다) 마지막으로 본 값을 쓴다 — 실행 줄 경과가 00:00 으로 튀지 않게
// **연주 대기(hold 회신)도 경계다** — 대기가 끝나면 서버가 start 를 한 번 더 적으므로(workqueue._hold_end) 그 뒤의 start 부터 센다.
// 대기 전의 start 까지 거슬러 올라가면 기다린 시간이 작업 시간으로 보인다 (머리 「실행 중 · 가죽 00:58」, 카드 00:10)
function runStart(){const ev=(Q.data&&Q.data.events)||[];let t=0;
  for(let i=ev.length-1;i>=0;i--){const e=ev[i];if(e.kind==="stop"||e.kind==="hold")break;if(e.kind==="loop"||e.kind==="start")t=e.t||t;}
  const live=!!(Q.data&&Q.data.running);
  if(t){if(live)Q.rs0=t;return t;}
  return live?Q.rs0:0;}

/* ── 회신 기록 — 사용자가 누를 때만 열린다 (이벤트가 생겼다고 자동으로 열지 않는다) ── */
const EV_KO={done:"완료",error:"오류",start:"시작",collect:"수령",loop:"회차",stop:"정지",hold:"연주 대기",notice:"알림"};
function evName(e,items){return (items.find((x)=>x.id===e.id)||{}).name||e.name||(e.kind==="loop"?"—":"");}
function evPanelHTML(d){const items=rootItems();const ev=(d.events||[]).slice(-100).reverse();const f=Q.evFilter;
  const isAlter=(e)=>{const it=findItem(e.id);return e.kind==="collect"||(it&&it.type==="alter");};
  const rows=ev.filter((e)=>f==="all"||(f==="done"&&e.kind==="done")||(f==="error"&&e.kind==="error")||(f==="alter"&&isAlter(e)));
  return `<div id="rbEvPanel"><div class="evtools"><span class="evseg" id="rbEvSeg">`
    +["all","done","error","alter"].map((k,i)=>`<button data-f="${k}"${f===k?' class="on"':""}>${["전체","완료","오류","가공"][i]}</button>`).join("")
    +`</span><span class="grow" style="flex:1"></span><button class="xs" id="rbEvCopy">복사</button><button class="xs" id="rbEvX">닫기</button></div>`
    +`<div id="rbEvList" data-sk="ev">`
    +(rows.length?rows.map((e)=>{const k=e.kind||"";const m=e.msg||"";
      return `<div class="ev ${esc(k)}"><span class="lt">${hhmm(e.t)}</span><span class="msg" title="${esc(EV_KO[k]||k)} · ${esc(evName(e,items))} · ${esc(m)}">${esc(m)}</span></div>`;}).join("")
      :'<span class="muted small-t" style="padding:6px 0">아직 회신이 없습니다. 항목이 끝날 때마다 여기에 한 줄씩 남습니다.</span>')
    +`</div></div>`;}

/* ── 날개·산출 기록 ──
   실행 명령마다 한 줄씩 쌓인 장부(ledger.jsonl)를 날짜로 끊어 보여 준다.
   **「예상」이라고 적는다** — 게임이 실제로 뺀 값이 아니라 카탈로그 기준(실행 1회 = 5개)이다.
   거절된 호출까지 실제로 빠졌는지는 회신으로 알 수 없어, 실패분은 따로 적는다. */
const stNum=(n)=>`<b>${fmtN(n||0)}</b>`;
function stOutTxt(out){if(!out||!out.length)return '<span class="dim">—</span>';
  return out.map((o)=>`<span class="sto${o.rest?" rest":""}">${esc(o.name)} ${fmtN(o.n)}</span>`).join("");}
function stBoxHTML(title,a){a=a||{};
  return `<div class="stbox"><div class="h">${esc(title)}</div>`
    +`<div class="k">정령의 날개 <span class="v gold">약 ${fmtN(a.wings||0)}</span>`
    +(a.fail?` <span class="dim">(실패 ${fmtN(a.fail)}회분 ${fmtN(a.failWings||0)} 포함)</span>`:"")+`</div>`
    +`<div class="k">실행 <span class="v">${fmtN(a.calls||0)}회</span>${a.collected?` <span class="dim">· 수령 ${fmtN(a.collected)}건</span>`:""}</div>`
    // 밖(폰)에서 시킨 실행은 **따로 적는다** — 「내가 안 켰는데 왜 줄었지」에 답할 수 있어야 한다
    +(a.remote?`<div class="k rmt">밖에서 <span class="v gold">${fmtN(a.remoteWings||0)}</span> <span class="dim">(${fmtN(a.remote)}회 · ${esc(Object.keys(a.by||{}).join(", "))})</span></div>`:"")
    +`<div class="o">${stOutTxt(a.out)}</div></div>`;}
function stPanelHTML(){const st=Q.stats;
  const head=`<div class="evtools"><span style="font-weight:600;font-size:var(--fs-sm)">날개·산출 기록</span>`
    +`<span class="grow" style="flex:1"></span><button class="xs" id="rbStX">닫기</button></div>`;
  if(!st)return `<div id="rbStPanel">${head}<span class="muted small-t" style="padding:6px 0">${Q.stBusy?"불러오는 중…":"기록을 불러오지 못했습니다"}</span></div>`;
  const days=(st.days||[]);const mx=Math.max(1,...days.map((d)=>d.wings||0));
  const rows=days.slice().reverse().map((d)=>`<div class="strow"><span class="d">${esc(d.d.slice(5))}</span>`
    +`<span class="bar"><i style="width:${Math.round((d.wings||0)/mx*100)}%"></i></span>`
    +`<span class="w">${d.wings?fmtN(d.wings):""}${d.failWings?` <span class="bad">실패 ${fmtN(d.failWings)}</span>`:""}</span></div>`).join("");
  // 오늘 한 줄 — 서버가 조립한 글(`line` = ledger.day_line) 그대로. 오버레이 밴드와 같은 글이다
  const line=st.line?`<div class="stline" data-sk="stline" style="font-size:var(--fs-sm);font-weight:600;padding:2px 0 6px">${esc(st.line)}</div>`:"";
  return `<div id="rbStPanel">${head}${line}`
    +`<div class="stsum">${stBoxHTML("오늘",st.today)}${stBoxHTML("최근 7일",st.week)}</div>`
    +`<div class="stdays" data-sk="st">${rows}</div>`
    +`<span class="muted small-t">날개 수는 카탈로그 기준 예상입니다 (실행 명령 1회 = ${fmtN(st.wingsPerCall||5)}개). 수령은 날개를 쓰지 않습니다 · ${fmtN(st.keepDays||90)}일치를 남깁니다.</span>`
    +`</div>`;}
async function loadStats(){Q.stBusy=true;draw();
  const r=await api("/api/stats?days=14");Q.stBusy=false;
  Q.stats=(r&&r.ok)?r:null;draw();}

/* ── 발 ── */
function footHTML(){return `<button class="srch" data-op="search" title="담기 서랍을 엽니다"><span class="i">⌕</span><span class="lb">레시피·채집물 이름으로 찾아 담기</span></button>`
  +`<button class="small pri" data-op="adddrawer">+ 작업 추가</button>`
  +`<span class="bnote" title="카드 사이로 끌면 순서가 바뀝니다 · 카드 가운데에 겹치면 그룹이 됩니다 · 그룹 카드 위로 끌면 그 안으로 · 그룹 밖으로 끌면 빠집니다">카드 <b>사이</b>로 끌면 순서 · <b>가운데</b>에 겹치면 그룹 · <b>그룹 밖</b>으로 끌면 빠짐</span>`
  +`<span class="grow"></span><span class="bnote">그룹 = 반복 단위 · 반복 없는 카드는 1회</span>`
  +`<button class="small outline" data-op="group_new">+ 새 그룹</button>`
  +`<button class="small outline" data-op="presets" title="지금 보드를 이름 붙여 저장하거나, 남이 준 공유 코드로 불러옵니다">프리셋</button>`;}

/* ── 그리기 ── */
function draw(){const board=$("board");if(!board)return;
  if(bBusy()){Q.pendRender=true;return;}   // 편집·드래그가 끝나면 mw:holdend·blur·dragend 로 다시 그린다
  const d=Q.data;
  try{
    if(Q.avail===false){board.innerHTML='<div class="bempty" style="grid-column:1/-1">서버가 아직 큐를 지원하지 않습니다.</div>';const rb=$("runbar");if(rb)rb.innerHTML="";const sg=$("bsegs");if(sg)sg.innerHTML="";return;}
    if(!d){board.innerHTML='<div class="bempty" style="grid-column:1/-1">불러오는 중…</div>';const sg=$("bsegs");if(sg)sg.innerHTML="";return;}
    const items=rootItems();
    const by={wait:[],run:[],done:[],fail:[]};for(const it of items)(by[colOf(it)]||by.wait).push(it);
    const narrow=isNarrow();const mode=narrow?"narrow":"wide";
    keepScrolls(()=>{
      if(narrow)drawNarrow(board,by,d);   // 작업 중 고정 + 탭 셋 (구조가 달라 함수가 갈린다)
      else{
        /* 배치는 3열 1 : 2 : 1 이다.
           오른쪽 한 칸은 .bstack 이 위아래로 쪼개 완료(위) · 실패(아래)를 담는다.
           열 자체(.bcol / .bcol-b[data-drop]) 는 넷 그대로다 — 드롭 판정과 스테퍼 검사가 그 구조를 본다. */
        board.innerHTML=columnHTML("wait",by.wait,d)+columnHTML("run",by.run,d)
          +`<div class="bstack">${columnHTML("done",by.done,d)}${columnHTML("fail",by.fail,d)}</div>`;
        const sg=$("bsegs");if(sg)sg.innerHTML="";}
      board.dataset.seg=B.seg;board.dataset.mode=mode;
      const rb=$("runbar");if(rb){rb.innerHTML=runbarHTML(d);rb.dataset.running=d.running?"1":"0";}
      const ft=$("boardFoot");if(ft&&ft.dataset.done!==mode){ft.innerHTML=narrow?kbFootHTML():footHTML();ft.dataset.done=mode;}
      panelDraw(d);
    });
    const top=$("bsegs");
    bindQEdit(board);bindQEdit(top);bindOps(board);bindOps(top);bindOps($("runbar"));bindOps($("boardFoot"));bindGroupRepeat(board);bindGroupRepeat(top);bindDrag(board);bindRunbar();bindSegs(top);
    etoastCheck(d);
    noticeCheck(d);   // 알림 카드·큐 종료 — 새 알림만 한 번씩
  }catch(e){
    // 한 군데가 터져도 화면이 하얗게 비면 안 된다 — 이유를 남기고 다시 시도할 길을 준다
    board.innerHTML=`<div class="bempty" style="grid-column:1/-1">보드를 그리지 못했습니다 · ${esc(String(e&&e.message||e))}</div>`;
    try{console.error("renderBoard",e);}catch{}}
  refreshPreviewCost();}
// 큐 응답을 받아 #board·#runbar 를 다시 그린다
function renderBoard(state){if(state&&typeof state==="object")applyState(state,true);else draw();}
function boardState(){return Q.data;}
function applyState(st,noDraw){if(!st||typeof st!=="object")return;
  if(Array.isArray(st.items)){Q.avail=true;Q.data=st;
    // 서버 확인 전인 인라인 편집값은 화면값을 유지한다 (폴링이 되돌리지 않게)
    for(const it of [...st.items,...st.items.filter(isGroup).flatMap((g)=>g.items||[])]){if(!it)continue;const l=Q.local[it.id];if(!l)continue;
      if(l.count!=null)it.count=l.count;if(l.target!=null){it.target=l.target;it.progress=it.progress||{};it.progress.passesPlanned=gpass(l.target);}if(l.collect!=null)it.collect=l.collect;}
    // 수령 항목이 새로 done 되면 가공 대기열이 비었을 테니 스냅샷을 다시 받는다
    const cd=st.items.filter((x)=>x&&x.type==="collect"&&x.status==="done").length;if(Q.collectDone!=null&&cd>Q.collectDone&&typeof loadWork==="function")loadWork();Q.collectDone=cd;}
  draw();}

/* ── 오류 토스트 (우하단, 8초) ── */
let etT=null;
/* 안내 글자는 **배너와 같은 말**을 쓴다. 체인 정지(치명)로 선 오류면 배너처럼 「▶ 시작」 —
   전에는 같은 blocked 를 배너는 「창을 닫은 뒤 ▶ 시작」, 토스트는 「…닫은 뒤 「대기로」」라고 달리 말했다.
   체인 정지가 아닌 낱장 오류(오류 시 계속)는 그 카드를 되살리는 「대기로」가 맞다. */
function etoast(it,onErr){const t=$("etoast");if(!t)return;const code=it.error||"error";
  const cs=chainStop(Q.data);const chain=!!(cs&&cs.error===code);
  const hint=errHint(it,chain?"▶ 시작":"대기로");const cont=onErr==="continue"&&!(Q.data&&Q.data.stopReason);
  const b=$("etBody");if(b)b.innerHTML=`<span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="code">${esc(code)}</span>${APP_ERRS.has(code)?'<span class="appchk">앱 검사</span>':""}<span style="font-weight:600;color:var(--gold2)">${esc(it.name||"")} ${esc(QTYPE_KO[it.type]||"")}</span></span><span style="color:var(--fg2)">${esc(it.message||"")}${hint?` — ${esc(hint)}`:""}</span><span style="color:var(--sub2)">${cont?"다음 항목으로 넘어갑니다 (오류 시 계속)":chain?"체인이 멈췄습니다 · 이 카드는 실패 열에서 「↺ 대기로」 뒤 다시":"체인이 멈췄습니다"}</span>`;
  t.hidden=false;try{t.showPopover();}catch{}t.dataset.id=it.id||"";clearTimeout(etT);etT=setTimeout(()=>{try{t.hidePopover();}catch{}t.hidden=true;},8000);}
/* 토스트는 **페이지를 연 뒤에 난** 오류만. 서버는 lastError 를 저장해 두고 다시 켜도 그대로 싣는다 —
   첫 응답을 「새 오류」로 보면 페이지를 열 때마다 지난 오류가 8초씩 떴다.
   첫 응답의 열쇠는 적어 두기만 하고(제목 (!) 표시는 켠다), 그 뒤 열쇠가 바뀔 때만 띄운다. */
function etoastCheck(d){const le=d.lastError;const key=le?`${le.id}|${le.error}|${le.message}`:"";
  if(Q.lastErrKey===null){Q.lastErrKey=key;Q.errFlag=!!le;return;}
  if(le&&key!==Q.lastErrKey){const evn=[...(d.events||[])].reverse().find((e)=>e.id===le.id&&e.msg);const fallback=le.name||(evn?String(evn.msg).split(/ — | · /)[0]:"")||"(삭제된 항목)";
    const it=findItem(le.id)||{id:le.id,name:fallback,type:le.type};etoast({...it,error:le.error,message:le.message,kind:le.kind},d.onError||"continue");Q.errFlag=true;}
  if(!le)Q.errFlag=false;Q.lastErrKey=key;}
{const et=$("etoast");if(et)et.onclick=(e)=>{if(e.target&&e.target.id==="etX"){try{et.hidePopover();}catch{}et.hidden=true;return;}
  const el=document.querySelector(`.bcard[data-id="${CSS.escape(et.dataset.id||"")}"]`);if(el)el.scrollIntoView({block:"center"});
  try{et.hidePopover();}catch{}et.hidden=true;};}

/* ── op 버튼 ── */
async function bop(body){const r=await api("/api/queue",body);
  if(r&&r.ok){if(r.state)applyState(r.state);else await loadQueue();return r;}
  toast(opErrTxt(r));await loadQueue();return r;}
function bindOps(root){if(!root)return;root.querySelectorAll("[data-op]").forEach((b)=>{b.onclick=async(e)=>{e.stopPropagation();
    const op=b.dataset.op,id=b.dataset.id;const MWx=window.MW||{};
    if(op==="adddrawer"){if(MWx.openDrawer)MWx.openDrawer({target:"root"});return;}
    if(op==="search"){if(MWx.openDrawer)MWx.openDrawer({target:"root",query:""});return;}
    if(op==="presets"){if(MWx.openPresets)MWx.openPresets();return;}
    if(op==="menu"){kbMenuShow();return;}                                   // 좁은 폭 하단 ⋯
    if(op==="stopboard"){const sp=$("rbStop");if(sp&&!sp.disabled)sp.click();return;}   // 그룹 자식 행의 ■ = ■ 정지
    if(op==="settings"){if(MWx.openGroup)MWx.openGroup(id);return;}
    // 「▸ 펼치기」는 그 자리에서 여는 토글이다. 창을 여는 것은 ⚙ 다.
    // 펼침은 화면 상태(Q.open)로만 기억한다 — 서버에 저장하지 않는다
    if(op==="expand"){if(Q.open.has(id))Q.open.delete(id);else Q.open.add(id);draw();return;}
    if(op==="group_new"){const n=await askText("새 그룹 이름","새 그룹",{title:"새 그룹",maxLength:24,note:"1~24자",ok:"만들기"});if(!n)return;await bop({op:"group_create",name:n.slice(0,24),ids:[]});return;}
    /* 삭제 — 상태와 관계없이 언제나 된다. 화면은 막지 않고, 되돌릴 수 없는 두 경우만 한 번 묻는다:
       ① 그룹(안의 항목까지 사라진다) ② 지금 실행 중인 카드(이미 보낸 명령은 되돌릴 수 없다).
       가공 대기(waiting) 카드를 지우면 서버가 note:"altering_left" 를 실어 준다 —
       게임에 걸린 가공은 그대로 남는다는 **사실 고지**이므로 그대로 보여 준다(이미 날개를 쓴 작업이다). */
    if(op==="remove"){const it=findItem(id);
      if(isGroup(it)){if(!await askOk(`그룹 「${(it&&it.name)||""}」과 안의 항목을 지웁니다.`,{title:"그룹을 지울까요?",ok:"지우기",danger:true}))return;}
      else if(it&&(it.status==="running"||(Q.data&&Q.data.current)===id)){
        if(!await askOk("이미 보낸 명령은 되돌릴 수 없습니다. 카드만 사라지고, 게임에서 도는 일은 그대로 끝납니다.",{title:"실행 중인 카드를 지울까요?",ok:"지우기",danger:true}))return;}
      const r=await bop({op:"remove",id});
      if(r&&r.ok&&r.note==="altering_left")toast(r.message||"게임에 걸린 가공은 그대로입니다 — 가공 탭에서 수령하세요");
      return;}
    /* 낱장 「다시 담기」 — 새 카드는 대기 열에 선다. 좁은 폭에서는 다른 탭이라 안 보이므로 한 줄 알려 준다.
       수령은 같은 시설 수령이 이미 대기·작업 중이면 서버가 duplicate 로 거절한다 (담기와 같은 규칙) */
    if(op==="card_duplicate"){const it=findItem(id);b.disabled=true;
      const r=await api("/api/queue",{op,id});
      if(r&&r.ok){toast(r.group?`대기 열에 다시 담음: 재생목록 그룹 (${fmtN(r.children||0)}곡)`:`대기 열에 다시 담음: ${(it&&nameOf(it))||""}`);
        if(r.state)applyState(r.state);else await loadQueue();return;}
      b.disabled=false;
      if(r&&r.error==="duplicate")toast(`이미 보드에 있음: ${(it&&it.facility)||""} 수령`);
      else if(r&&(r.error==="no_completed_work"||r.error==="no_completed_work_at_facility"))toast(`완료된 가공이 없습니다: ${(it&&it.facility)||""}`);
      else toast("다시 담지 못했습니다: "+opErrTxt(r));
      await loadQueue();return;}
    b.disabled=true;await bop({op,id});};});}
// 그룹 회차 스테퍼 (실행 중에는 늘리기만 — 서버가 group_running 으로 거절하는 자리를 미리 잠근다)
function bindGroupRepeat(root){if(!root)return;root.querySelectorAll("[data-ge]").forEach((cell)=>{const id=cell.dataset.ge;
    const g0=findItem(id);if(!g0)return;const running=colOf(g0)==="run";
    const cur=()=>{const g=findItem(id);return Number(g&&g.repeat)||1;};
    cell.querySelectorAll("[data-gst]").forEach((b)=>{const n=Number(b.dataset.gst);
      if(running&&n<0){b.disabled=true;b.title="실행 중에는 회차를 줄일 수 없습니다";return;}
      holdBtn(b,()=>{const v=Math.max(running?cur():1,Math.min(20,cur()+n));if(v===cur())return;gRepeatApply(id,v);});});
    const inp=cell.querySelector("[data-gin]");if(inp){inp.oninput=()=>{const v=Number(inp.value);if(v>=1&&v<=20)gRepeatApply(id,v);};inp.onchange=()=>{inp.value=cur();};}});}
function gRepeatApply(id,v){const g=findItem(id);if(!g)return;g.repeat=v;
  const cell=document.querySelector(`[data-ge="${CSS.escape(id)}"]`);if(cell){const inp=cell.querySelector("[data-gin]");if(inp&&document.activeElement!==inp)inp.value=v;
    const lp=cell.parentElement&&cell.parentElement.querySelector(".loop");if(lp)lp.textContent=`회차 ${fmtN(Math.max(1,Number(g.loop)||1))} / ${fmtN(v)}`;}
  clearTimeout(Q.editT[id]);Q.editT[id]=setTimeout(async()=>{const seq=Q.seq[id]=(Q.seq[id]||0)+1;
    const r=await api("/api/queue",{op:"group_update",id,patch:{repeat:v}});
    if(seq!==Q.seq[id])return;
    if(r&&r.ok){Q.prevKey="";if(r.state)applyState(r.state);else await loadQueue();return;}
    toast(opErrTxt(r));await loadQueue();},250);}

/* ── 드래그 ──
   받는 드롭은 넷:
     ① 대기 열의 카드 사이 — 순서 바꾸기 / 완료·실패 카드 되살리기 (reset → move_item root)
     ② 대기 열 카드의 가운데 20% — 두 장을 그룹으로 (group_create)
     ③ 그룹 카드 위 — 그 그룹 안으로 (move_item to:그룹)
     ④ 그룹 안 자식을 대기 열로 — 그룹에서 빼내기 (move_item to:root)
   그 밖에는 드롭 표시조차 내지 않는다. 작업 중·완료·실패 열과 실행 중 그룹은 러너의 자리다.

   자리 표시는 가는 선이 아니라 「점선 테두리 빈 상자」(.bghost)다 — 놓이게 될 자리에 실제로 끼워 넣어
   다른 카드를 밀어낸다. 사용자는 놓기 전에 결과 배치를 그대로 본다.
   상자는 끄는 동안 **하나만** 만들고 자리만 옮긴다. 매번 지웠다 다시 넣으면 깜빡인다. */
function ghostEl(sm){if(!B.ghost){B.ghost=document.createElement("div");B.ghost.setAttribute("aria-hidden","true");}
  const g=B.ghost;const cls="bghost"+(sm?" sm":"");
  if(g.className!==cls)g.className=cls;
  const txt=sm?"여기에 담김":"";if(g.textContent!==txt)g.textContent=txt;
  // 크기는 끌고 있는 카드와 같게. 그룹 카드를 끌면 그룹 카드 크기가 된다
  const h=sm?"":(B.ghostH?B.ghostH+"px":"");if(g.style.height!==h)g.style.height=h;
  return g;}
function ghostOut(){if(B.ghost&&B.ghost.parentNode)B.ghost.parentNode.removeChild(B.ghost);}
function clearDrop(){document.querySelectorAll(".dropmerge,.dropin").forEach((x)=>x.classList.remove("dropmerge","dropin"));
  ghostOut();}
// 열 안에서 커서 높이에 맞는 자리 — 카드 사이의 틈이나 빈 곳에 커서가 있을 때. 끌던 카드(감춰져 있다)는 세지 않는다
function orderAt(body,y){for(const c of body.children){
    if(!c.classList||!c.classList.contains("bcard")||c.classList.contains("dragging"))continue;
    const r=c.getBoundingClientRect();if(y<r.top+r.height/2)return {kind:"order",el:c,after:false};}
  return {kind:"order",body,after:true};}
function dropTarget(e){const d=B.drag;if(!d)return null;const tgt=e.target&&e.target.closest?e.target:null;if(!tgt)return null;
  const card=tgt.closest(".bcard[data-id], .gexp[data-id]");
  if(card&&card.dataset.id!==d.id){
    const grp=card.dataset.group==="1";
    // 그룹 카드 위 = 그 그룹 안으로. 실행 중 그룹(locked)·그룹 안에 그룹(nested)·이미 그 그룹의 자식은 받지 않는다
    if(grp){if(card.dataset.locked==="1"||d.isGroup||d.parent)return null;return {kind:"into",gid:card.dataset.id,el:card};}
    /* 그룹 안쪽 목록 위 = 그 그룹 안으로. 자식 카드 자체는 드롭 대상이 아니다 —
       펼친 그룹 카드는 안쪽 2×2 가 면적의 대부분이라, 여기서 안 받으면 사실상 담을 자리가 없다 */
    if(card.closest(".gin-b")){const own=card.closest('.bcard[data-group="1"][data-id], .gexp[data-id]');
      if(!own||own.dataset.locked==="1"||d.isGroup||d.parent||own.dataset.id===d.id)return null;
      return {kind:"into",gid:own.dataset.id,el:own};}
    if(card.dataset.col!=="wait")return null;
    const r=card.getBoundingClientRect();const y=(e.clientY-r.top)/Math.max(1,r.height);
    // 위 40% / 가운데 20%(그룹) / 아래 40%. 드래그의 기본 뜻은 순서이고, 그룹은 분명히 겨눴을 때만.
    // 그룹 카드를 끌 때와 그룹에서 빼내는 중에는 merge 를 내지 않는다(서버가 nested·group_running 으로 거절할 자리)
    if(y>0.4&&y<0.6&&!d.isGroup&&!d.parent)return {kind:"merge",gid:card.dataset.id,el:card};
    return {kind:"order",el:card,after:y>=0.5};}
  const body=tgt.closest('.bcol-b[data-drop="wait"]');
  if(body)return orderAt(body,e.clientY);
  return null;}
function showDrop(t){
  // 테두리 강조는 필요한 카드에만 남긴다 — 매번 전부 지웠다 다시 칠하면 깜빡인다
  const mg=t&&t.kind==="merge"?t.el:null,into=t&&t.kind==="into"?t.el:null;
  document.querySelectorAll(".dropmerge").forEach((x)=>{if(x!==mg)x.classList.remove("dropmerge");});
  document.querySelectorAll(".dropin").forEach((x)=>{if(x!==into)x.classList.remove("dropin");});
  if(mg)mg.classList.add("dropmerge");
  if(into)into.classList.add("dropin");
  if(!t||t.kind==="merge"){ghostOut();return;}   // 합쳐짐은 그 카드 모양이 바뀌는 것으로 보인다 — 자리 상자를 내지 않는다
  const g=ghostEl(t.kind==="into");
  const parent=t.kind==="into"?t.el:(t.el?t.el.parentNode:t.body);
  // into 는 그룹 카드의 **바로 아래** .bacts 앞. querySelector 로 찾으면 펼친 그룹 안 자식 카드의 .bacts 를 잡는다
  const before=t.kind==="into"?([...t.el.children].find((c)=>c.classList&&c.classList.contains("bacts"))||null)
    :t.el?(t.after?t.el.nextSibling:t.el)
    :(t.body.querySelector(".bhint")||null);
  if(before===g)return;                                     // 이미 그 자리다 (자기 앞에 자기를 넣지 않는다)
  if(g.parentNode===parent&&g.nextSibling===before)return;   // 자리가 그대로면 DOM 을 건드리지 않는다 — 깜빡임 방지
  parent.insertBefore(g,before);}
// 대기 열에서 놓은 자리의 index (끌고 온 카드를 뺀 뒤 기준). 끌던 id 를 인자로 받는다 —
// drop 처리 중에 B.drag 를 이미 비웠기 때문이다(비우기 전 값을 쓰려다 null 을 읽어 드롭이 통째로 죽었다)
function orderIndex(t,dragId){const wait=rootItems().filter((x)=>colOf(x)==="wait").map((x)=>x.id).filter((x)=>x!==dragId);
  if(!t.el)return wait.length;
  const at=wait.indexOf(t.el.dataset.id);if(at<0)return wait.length;
  return t.after?at+1:at;}
function bindDrag(root){
  root.querySelectorAll('.bcard[draggable="true"]').forEach((el)=>{
    /* e.target!==el 이면 무시한다 — 펼친 그룹 카드는 자신도 draggable 이고 안쪽 자식도 draggable 이라
       자식의 dragstart 가 부모까지 올라온다. 막지 않으면 자식을 끌었는데 그룹이 끌린다. */
    /* 버튼·입력 위에서 누르면 끌기를 시작하지 않는다. 카드마다 ×(삭제)·⚙·수량 스테퍼가 있어서,
       막지 않으면 지우려고 × 를 누른 순간 카드가 끌려간다. dragstart 의 target 은 언제나
       끌기 원본(카드)이라 e.target 으로는 가를 수 없다 — 직전 pointerdown 이 어디서 났는지를 본다.
       (holdBtn 의 setPointerCapture 는 건드리지 않는다 — 여기서는 읽기만 한다) */
    /* 손가락으로 누른 것이면 끌기를 시작하지 않는다. 폰에서 카드를 끌면 **화면을 내리려던 것**이고,
       끌기를 잡아 버리면 스크롤이 통째로 안 된다.
       카드 순서 바꾸기는 마우스에서만 — 폰에서는 열 이동을 세그먼트로 한다. */
    let hitCtl=false,touch=false;
    el.addEventListener("pointerdown",(e)=>{touch=e.pointerType==="touch";
      hitCtl=!!(e.target&&e.target.closest&&e.target.closest("button,input,.stepper"));});
    el.addEventListener("dragstart",(e)=>{if(hitCtl||touch){e.preventDefault();return;}
      if(e.target!==el)return;const id=el.dataset.id;
      B.drag={id,col:el.dataset.col,isGroup:el.dataset.group==="1",parent:el.dataset.parent||""};
      B.ghostH=Math.round(el.getBoundingClientRect().height)||0;
      try{e.dataTransfer.effectAllowed="move";e.dataTransfer.setData("text/plain",id);}catch{}
      // 원본은 감춘다 — 자리 상자와 원본이 동시에 두 자리를 차지하지 않게.
      // 끌기가 시작된 뒤에 감춰야 한다: dragstart 안에서 바로 감추면 브라우저가 끌기를 취소한다
      setTimeout(()=>{if(B.drag&&B.drag.id===id)el.classList.add("dragging");},0);});
    el.addEventListener("dragend",(e)=>{hitCtl=false;if(e.target!==el)return;el.classList.remove("dragging");B.drag=null;B.ghostH=0;clearDrop();B.ghost=null;setTimeout(flushBoardRender,0);});});
  /* 열 단위 리스너는 한 번만 — draw() 는 #board 의 innerHTML 만 갈아 끼우고 #board 자신은 그대로라서,
     다시 그릴 때마다 붙이면 리스너가 쌓이고 drop 이 여러 번 돈다 */
  if(root.dataset.dragBound==="1")return;
  root.dataset.dragBound="1";
  root.addEventListener("dragover",(e)=>{const t=dropTarget(e);if(!t){clearDrop();return;}e.preventDefault();try{e.dataTransfer.dropEffect="move";}catch{}showDrop(t);});
  root.addEventListener("dragleave",(e)=>{if(e.target===root)clearDrop();});
  root.addEventListener("drop",async(e)=>{const t=dropTarget(e);const d=B.drag;clearDrop();if(!t||!d)return;e.preventDefault();
    const id=d.id,from=d.col,parent=d.parent;const idx=t.kind==="order"?orderIndex(t,id):0;   // 자리는 화면이 아직 그대로일 때 센다
    B.drag=null;
    // 완료·실패에서 끌어온 카드는 먼저 대기로 되돌린다
    if(from==="done"||from==="fail"){const r=await api("/api/queue",{op:"reset",id});if(r&&!r.ok){toast(opErrTxt(r));await loadQueue();return;}}
    if(t.kind==="merge"){const r=await bop({op:"group_create",ids:[t.gid,id]});if(r&&r.ok&&r.id)undoBar(r.id);return;}   // 드래그로 만든 그룹만 되돌리기를 띄운다
    if(t.kind==="into"){await bop({op:"move_item",id,to:t.gid});return;}
    const r=await bop({op:"move_item",id,to:"root",index:idx});
    if(parent&&r&&r.ok)toast("그룹에서 뺐습니다");});}

/* ── 되돌리기 줄 ──
   드래그로 group_create 가 일어난 직후에만 5초. 서랍의 「+ 새 그룹」은 사용자가 의도해서 만든 것이라 띄우지 않는다.
   앵커는 여기서 만든다 — #toast·#etoast 와 같은 popover 방식(창 위에도 뜬다). */
let undoT=null;
function undoBox(){let el=$("bundo");
  if(!el){el=document.createElement("div");el.id="bundo";el.setAttribute("popover","manual");el.hidden=true;document.body.appendChild(el);}
  return el;}
function undoHide(){clearTimeout(undoT);const el=$("bundo");if(!el)return;try{el.hidePopover();}catch{}el.hidden=true;}
function undoBar(gid){if(!gid)return;const el=undoBox();
  el.innerHTML=`<span class="tx">그룹을 만들었습니다</span><button class="u">되돌리기</button><button class="x" title="닫기">×</button>`;
  el.querySelector("button.u").onclick=async()=>{undoHide();const r=await bop({op:"group_dissolve",id:gid});if(r&&r.ok)toast("그룹을 되돌렸습니다");};
  el.querySelector("button.x").onclick=undoHide;
  el.hidden=false;try{el.showPopover();}catch{}
  clearTimeout(undoT);undoT=setTimeout(undoHide,5000);}

/* ── 실행 줄 조작 ── */
function bindRunbar(){const rb=$("runbar");if(!rb)return;
  const st=$("rbStart");if(st)st.onclick=openConfirm;
  const sp=$("rbStop");if(sp)sp.onclick=async()=>{sp.disabled=true;const r=await api("/api/queue/stop",{});toast(r&&r.ok?"정지를 요청했습니다 — 진행 중인 동작이 끝나면 멈춥니다":opErrTxt(r));await loadQueue();};
  rb.querySelectorAll("#rbOnErr button").forEach((b)=>{b.onclick=async()=>{if(b.disabled)return;await bop({op:"config",onError:b.dataset.v});};});
  const sb=$("rbStBtn");if(sb)sb.onclick=()=>{Q.stOpen=!Q.stOpen;Q.evOpen=false;draw();if(Q.stOpen)loadStats();};
  const sx=$("rbStX");if(sx)sx.onclick=()=>{Q.stOpen=false;draw();};
  const ev=$("rbEvBtn");if(ev)ev.onclick=()=>{Q.evOpen=!Q.evOpen;Q.stOpen=false;lsSet("mw.evOpen",Q.evOpen?"1":null);draw();};
  const x=$("rbEvX");if(x)x.onclick=()=>{Q.evOpen=false;lsSet("mw.evOpen",null);draw();};
  const pn=$("rbPanel");if(pn)pn.querySelectorAll("#rbEvSeg button").forEach((b)=>{b.onclick=()=>{Q.evFilter=b.dataset.f;draw();};});
  const cp=$("rbEvCopy");if(cp)cp.onclick=async()=>{const d=Q.data||{};const items=rootItems();
    const lines=(d.events||[]).slice(-100).map((e)=>`${hhmm(e.t)}\t${EV_KO[e.kind]||e.kind}\t${evName(e,items)}\t${e.msg||""}`).join("\n");
    try{await navigator.clipboard.writeText(lines);toast("회신 기록을 복사했습니다");}catch{toast("복사하지 못했습니다");}};}

/* ── 예상 소모 (미리보기) ──
   화면에서 지어내지 않는다. 큐가 바뀔 때만 다시 받고, 실행 중에는 시작 시점 추정치를 그대로 둔다. */
async function refreshPreviewCost(){const d=Q.data;if(!d)return;
  const items=rootItems();const pend=items.some((x)=>colOf(x)==="wait"||colOf(x)==="run");
  if(!pend&&!d.running){Q.prev=null;Q.prevKey="";return;}
  if(d.running)return;
  if(Object.keys(Q.local).length)return;   // 서버가 아직 못 받은 수정이 있으면 옛 값으로 계산된 답이 캐시된다
  /* **자물쇠 글자에 자식이 빠져 있었다.** 그룹 안 카드의 수량을 고쳐도 이 글자가 안 바뀌어
     옛 값이 그대로 남았다 — 보드는 「50」, 확인창은 새로 받아 「25」. 계산이 틀린 게 아니라
     **다른 순간의 값 둘을 나란히 보여 준 것**이다.
     그리고 `collect`(걸기만/수령까지)도 빠져 있었다 — 그게 바뀌면 수령 호출이 사라져
     날개 수가 움직인다. 자식까지 훑는다. */
  const sig=(x)=>[x.id,x.status,x.count,x.target,x.repeat,x.collect,(x.progress||{}).passesPlanned,
                  (x.items||[]).map(sig)];
  const key=JSON.stringify(items.map(sig));
  if(key===Q.prevKey&&Q.prev)return;
  const seq=Q.prevSeq=(Q.prevSeq||0)+1;
  const p=await api("/api/queue/preview");
  if(seq!==Q.prevSeq)return;   // 더 새 요청이 나갔다 — 이 응답은 버린다
  if(!p||p.ok===false)return;
  Q.prev=p;Q.prevKey=key;
  const el=document.querySelector("#runbar .rb-cost .rb-v.gold");if(el&&p.wings!=null)el.textContent=`정령의 날개 약 ${fmtN(p.wings)}`;}

/* 그룹 한 줄의 날개 — **호출 수가 아니라 `wingCalls`.** 서버가 `groups[]` 에 실어 준다
   (`wings` · `wingCalls` 둘 다). 없으면 자식에서 세고, 그것도 없으면 「—」라고 적는다:
   **모르면 지어내지 않는다.** 예전엔 `calls × 5` 로 곱해 수령까지 세는 바람에
   같은 창의 헤더(25)와 그룹 행(35)이 서로 어긋났다. */
function groupWings(g,r0){
  const wc=g&&g.wingCalls!=null?Number(g.wingCalls)
    :(r0.children||[]).length?(r0.children||[]).reduce((a,c)=>a+itemWingCalls({...(findItem(c.id)||{}),...c}),0)*(g&&g.loopLeft!=null?g.loopLeft:1)
    :null;
  if(wc==null)return "—";
  return wc?`날개 ${fmtN(g&&g.wings!=null?g.wings:wc*WINGS_PER_CALL)}`:"날개 0";}

/* 날개 부족 경고. 시작 확인은 「안전 규칙 한 줄 · 취소 / ▶ 시작」 — **막는 규칙이 없다.**
   그리고 이 창 자체가 「그래도 진행할까요」를 묻는 자리다(알려진 방해와 같은 원칙). 그래서 ▶ 시작은 켜 둔 채
   **빨간 상자로 못 박아 말한다**: 전에는 「보유 41 → 실행 후 약 −14 · 부족」 한 줄 글자뿐이라 지나쳤고,
   시작하면 뒤 항목이 not_enough_currency 로 실패했다. 예상치(채집 회수는 추정)라 막지 않는다.
   상자는 여기서 만든다 — 발(.dlg-f) 바로 위, 누르기 직전에 보이는 자리. */
function cfShortBox(short){const dlg=$("qConfirm");if(!dlg)return;let el=$("qcShort");
  if(!short){if(el)el.hidden=true;const g=$("qcGo");if(g)g.classList.remove("warnGo");return;}
  if(!el){el=document.createElement("div");el.id="qcShort";el.setAttribute("role","alert");
    const f=dlg.querySelector(".dlg-f");dlg.insertBefore(el,f||null);}
  el.innerHTML=`<b>정령의 날개가 약 ${fmtN(short)} 모자랍니다</b>`
    +`<span>이대로 시작하면 날개가 떨어진 뒤의 항목은 <span class="code">not_enough_currency</span> 로 실패합니다. 날개를 채우거나 담은 수를 줄인 뒤 시작하세요.</span>`;
  el.hidden=false;const g=$("qcGo");if(g){g.classList.add("warnGo");g.title="날개가 모자란 채로 시작합니다 — 뒤 항목은 실패합니다";}}
/* ── 시작 확인창 (#qConfirm 은 그대로 거친다) ── */
async function openConfirm(){const dlg=$("qConfirm");if(!dlg)return;
  const set=(id,k,v)=>{const e=$(id);if(e)e[k]=v;};
  set("qcErr","hidden",true);set("qcList","textContent","불러오는 중…");set("qcConsume","textContent","");set("qcGo","disabled",true);cfShortBox(0);
  set("qcDemo","hidden",!((S.cli&&S.cli.demo)||S.demo));const mg=S.settings.queue_weight_margin!=null?S.settings.queue_weight_margin:30;set("qcMargin","textContent",mg);set("qcMargin2","textContent",mg);
  // 날개 차단기 상한 (설정 wing_cap_total · wing_cap_waste) — 안전 규칙 줄에 그 값 그대로
  {const wc=S.settings.wing_cap_total!=null?S.settings.wing_cap_total:150,ww=S.settings.wing_cap_waste!=null?S.settings.wing_cap_waste:4;
   const wm=S.settings.wing_cap_window_min!=null?S.settings.wing_cap_window_min:10,wwm=S.settings.wing_cap_waste_min!=null?S.settings.wing_cap_waste_min:5;
   set("qcWingCap","textContent",wc);set("qcWingCap2","textContent",wc);set("qcWingWaste","textContent",ww);set("qcWingWaste2","textContent",ww);
   set("qcWingMin","textContent",wm);set("qcWingMin2","textContent",wm);set("qcWingWasteMin","textContent",wwm);set("qcWingWasteMin2","textContent",wwm);set("qcWingLine","hidden",true);}
  if(!dlg.open)dlg.showModal();   // 「다시 확인」은 열린 채로 다시 읽는다 — 열린 dialog 에 showModal 은 오류
  set("qcRecheck","hidden",true);
  const p=await api("/api/queue/preview");const d=Q.data||{};const onErr=(p&&p.onError)||d.onError||"continue";
  if(!p||p.ok===false){set("qcList","innerHTML",`<span style="color:var(--bad2)">${esc((p&&(p.message||p.error))||"미리보기를 받지 못했습니다")}</span>`);return;}
  // 날개 차단기 — 회색 한 줄 「최근 10분 소모 N · 헛소모 M회 — 상한 150 / 4」 (preview.wingGuard, 없으면 큐 상태의 wings10m·waste5m)
  {const g=(p.wingGuard&&typeof p.wingGuard==="object")?p.wingGuard:{};const w=g.wings10m!=null?g.wings10m:d.wings10m,m=g.waste5m!=null?g.waste5m:d.waste5m;
   if(w!=null){const cap=g.cap!=null?g.cap:(S.settings.wing_cap_total!=null?S.settings.wing_cap_total:150),wcap=g.wasteCap!=null?g.wasteCap:(S.settings.wing_cap_waste!=null?S.settings.wing_cap_waste:4);
     set("qcWingLine","textContent",`최근 ${g.capMin||10}분 소모 ${fmtN(w)} · 헛소모 ${fmtN(m||0)}회(${g.wasteMin||5}분) — 상한 ${fmtN(cap)} / ${fmtN(wcap)}`);set("qcWingLine","hidden",false);
     if(g.capMin){set("qcWingMin","textContent",g.capMin);set("qcWingMin2","textContent",g.capMin);}if(g.wasteMin){set("qcWingWasteMin","textContent",g.wasteMin);set("qcWingWasteMin2","textContent",g.wasteMin);}
     set("qcWingCap","textContent",cap);set("qcWingCap2","textContent",cap);set("qcWingWaste","textContent",wcap);set("qcWingWaste2","textContent",wcap);}}
  const rows=Array.isArray(p.rows)?p.rows:Array.isArray(p.items)?p.items:[];
  const groups=Array.isArray(p.groups)?p.groups:[];
  const flat=[];for(const r of rows){if(r&&r.kind==="group"){flat.push(r);for(const c of (r.children||[]))flat.push({...c,_in:r.name});}else flat.push(r);}
  const calls=p.calls!=null?p.calls:p.execCalls!=null?p.execCalls:flat.filter((r)=>r&&r.kind!=="group").reduce((a,r)=>a+(r.calls!=null?Number(r.calls):itemCalls({...(findItem(r.id)||{}),...r})),0);
  /* **날개는 `wingCalls` 에서 나온다**. 호출 수로 곱하면 수령까지 세어
     실제보다 많아진다 — 수령(`complete_altering_work`)은 날개를 안 쓴다(카탈로그 확정). */
  const wingCalls=p.wingCalls!=null?p.wingCalls
    :flat.filter((r)=>r&&r.kind!=="group").reduce((a,r)=>a+itemWingCalls({...(findItem(r.id)||{}),...r}),0);
  const wings=p.wings!=null?p.wings:wingCalls*WINGS_PER_CALL;
  const have=wingsHave();
  set("qcN","textContent",fmtN(rows.length));
  set("qcNs","textContent",groups.length?`그룹 ${fmtN(groups.length)}장 포함`:"그룹 없음 · 카드마다 1회");
  set("qcCalls","innerHTML",`약 ${fmtN(calls)}<small>회</small>`);set("qcWings","textContent",`약 ${fmtN(wings)}`);
  set("qcWingsS","textContent",wingCalls?`정령의 날개 · ${fmtN(wingCalls)} × ${WINGS_PER_CALL}${wingCalls!==calls?" (수령 제외)":""}`:"정령의 날개를 쓰지 않습니다 (수령만)");
  cfShortBox(have!=null&&have-wings<0?wings-have:0);
  set("qcHave","innerHTML",have!=null?`보유 정령의 날개 <b>${fmtN(have)}</b> → 실행 후 약 <b style="${have-wings<0?"color:var(--bad2)":""}">${fmtN(have-wings)}</b>${have-wings<0?' <span style="color:var(--bad2)">· 부족</span>':""}`:"보유 정령의 날개: 「갱신」 뒤 표시");
  set("qcOnErr","innerHTML",onErr==="continue"?'오류 시 <span style="color:var(--gold);font-weight:600">계속</span> · 다음 카드로 넘어갑니다':'오류 시 <span style="color:var(--gold);font-weight:600">정지</span> · 오류가 나면 보드 전체가 멈춥니다');
  // 알려진 방해 (계란의 수닭 등) — CLI 가 미리 안 알려 주는 것이라 **시작 전에** 사람에게 말한다.
  // 막지 않는다. 이 창 자체가 「그래도 진행할까요」를 묻는 자리다.
  {const hz=Array.isArray(p.hazards)?p.hazards:[];const line=$("qcHzLine");
   if(line){line.hidden=!hz.length;
     if(hz.length)set("qcHz","innerHTML",hz.map((x)=>`<b>${esc(x.name)}</b> — ${esc(x.note)}`).join("<br>")
       +'<br><span class="hz-s">실패해도 정령의 날개는 이미 쓰입니다. 그래도 진행하려면 「시작」을 누르세요.</span>');}}
  if(!rows.length){set("qcList","innerHTML",'<span class="muted small-t">실행할 대기 카드가 없습니다. 완료·실패 카드는 「↺ 대기로」로 되돌린 뒤 시작하세요.</span>');return;}
  set("qcList","innerHTML",flat.map((r0,i)=>{if(!r0)return "";
    if(r0.kind==="group"){const g=groups.find((x)=>x.id===r0.id)||{};
      return `<div class="cf-i"><span class="i">${i+1}</span><span class="tx"><span>그룹 「${esc(r0.name||"")}」 · ${fmtN(g.repeat||1)}회차${g.loopLeft!=null?` (남은 ${fmtN(g.loopLeft)})`:""} · ${fmtN((r0.children||[]).length)}항목</span><span class="cmd">${esc(r0.detail||"")}</span></span><span class="rt"><span class="calls">${fmtN(g.calls!=null?g.calls:(r0.calls||0))}회</span><span class="cost">${groupWings(g,r0)}</span></span></div>`;}
    const it={...(findItem(r0.id)||{}),...r0};const c=r0.calls!=null?Number(r0.calls):itemCalls(it);const wc=itemWingCalls(it);
    const text=r0.detail||(it.type==="collect"?`「${it.facility||""}」 완료분 수령 (시설당 1회 호출로 전부)`:it.type==="gather"?`「${it.name}」 채집 ${fmtN(it.target||0)}개 캐기 — 예상 ${c}회 (1회 최대 100개 · 날개 5개) · 가방이 목표에 닿으면 멈춤`:it.type==="craft"?`「${it.name}」 제작 ${fmtN(it.count||1)}회`:`「${it.name}」 가공 ${fmtN(it.count||1)}건 등록`);
    // 그룹 자식의 호출·날개는 위의 그룹 줄이 이미 회차까지 곱해 세었다 — 여기서 또 세면 두 번 센 것처럼 보인다
    const rt=r0._in?`<span class="calls">회차당 ${it.type==="gather"?"약 ":""}${fmtN(c)}회</span>`
      :`<span class="calls">${it.type==="gather"?"약 ":""}${fmtN(c)}회</span><span class="cost">${wc?`날개 ${it.type==="gather"?"약 ":""}${fmtN(wc*WINGS_PER_CALL)}`:"—"}</span>`;
    return `<div class="cf-i"><span class="i">${i+1}</span><span class="tx"><span>${r0._in?`<span style="color:var(--gold)">${esc(r0._in)} ›</span> `:""}${esc(text)}</span><span class="cmd">${esc(it.type?QTYPE_KO[it.type]||"":"")}</span></span><span class="rt">${rt}</span></div>`;}).join(""));
  // 연주 중 = 거절이 아니라 **연주 대기** — 빨간 상자가 아니라 ♪ 안내 줄을 맨 앞에. 우선마다 글이 다르다
  // (서버 ERROR_KO performance_playing[_song|_work] — 셋 다 「연주 대기 카드」를 말한다). 시작하면 작업 중 칸에 카드가 뜨고 끝나면 스스로 시작한다.
  const perfWarn=(p.warnings||[]).find((w)=>/연주 대기 카드/.test(w));
  const warn=(p.warnings||[]).filter((w)=>!/자동 재시도는 없습니다/.test(w)&&w!==perfWarn);
  const lines=(perfWarn?[`<span class="qc-hold">♪ ${esc(perfWarn)}</span>`]:[]).concat(warn.map((w)=>esc(w)));
  if(lines.length)set("qcConsume","innerHTML",lines.join("<br>"));
  // 지금 캐릭터 상태 (preview.activity): 사망·부활·전투·대화·던전이면 빨강 — blocked 로 끝날 큐를 시작 전에 알린다
  let blocked=false;
  {const a=p.activity;const L=$("qcActLine");if(L){if(a&&typeof a==="object"){const bad=[a.dead&&"사망",a.reviving&&"부활 대기",a.dialog&&"대화 중",(a.dungeon&&a.dungeon!=="NotInDungeon")&&`던전 ${a.dungeon}`,a.mission&&"지역 임무 중",a.scenario&&"시나리오"].filter(Boolean);   // 전투·전장은 출발이 된다 (실측) — 빨강 아님
      // 막힌 상태(precheck_*)도 activity.error 로 온다 — 그건 「읽기 실패」가 아니라 **이유가 있는 막힘**이다.
      // 예전엔 둘을 한데 묶어 대화 중이면 칩이 「확인 실패」 하나로 뭉개졌다
      const readFail=!!a.error&&!/^precheck_/.test(String(a.error));
      const txt=a.text||(readFail?`확인 실패 (${a.error})`:(bad.length?bad.join(" · "):a.error?`시작할 수 없는 상태 (${a.error})`:"대기 중 — 실행 가능"));L.hidden=false;
      set("qcAct","innerHTML",`지금 캐릭터 상태: <b style="color:${bad.length||a.error?"var(--bad2)":"var(--ok)"}">${esc(txt)}</b>${bad.length?' <span style="color:var(--sub)">— 이 상태면 시작해도 그 자리에서 멈춥니다</span>':""}`);
      // 좁은 폭은 같은 판단을 **칩**으로 — 사망 F · 전투 F · 대화 F · 던전 밖 · 전장 F. 초록=괜찮음, 빨강=blocked 로 거부될 상태
      const chips=readFail?[["확인 실패",true,""]]:[["사망",!!a.dead],["부활",!!a.reviving],["전투",!!a.combat],["대화",!!a.dialog],["던전",!!(a.dungeon&&a.dungeon!=="NotInDungeon"),"안","밖"],["전장",!!a.battlefield]];
      set("qcActChips","innerHTML",chips.map(([k,v,yes,no])=>`<span class="chip${v?" bad":""}">${esc(k)} ${v?(yes!=null?yes:"T"):(no!=null?no:"F")}</span>`).join(""));
      // **막힌 상태면 「시작」을 잠근다** — 다시 확인하게 한다. 상태를 못 읽은 것도 같다.
      // 게임에서 상태를 고친 뒤 「다시 확인」이 상태를 다시 읽는다 — 확인창을 닫았다 열 필요가 없다.
      blocked=!!(bad.length||a.error);}else L.hidden=true;}}
  const rc=$("qcRecheck");if(rc)rc.hidden=!blocked;
  const go=$("qcGo");if(go){go.disabled=blocked;go.title=blocked?"지금 상태로는 시작할 수 없습니다 — 게임에서 고친 뒤 「다시 확인」":"";}}
{const rc=$("qcRecheck");if(rc)rc.onclick=()=>openConfirm();}
// 날개 차단기에 막히면(overridable) 막고 끝내지 않는다 — 「그래도 시작할까요?」를 묻고, 예면 차단기를 넘어 시작
async function startWithOverride(){let r=await api("/api/queue/start",{confirm:true});
  if(r&&!r.ok&&r.overridable){const yes=await askOk(`${r.message||"날개 차단기가 걸려 있어요."} — 그래도 시작할까요? 차단기 기록을 비우고 새로 세기 시작해요.`,{title:"날개 차단기",ok:"그래도 시작",danger:true});
    if(yes)r=await api("/api/queue/start",{confirm:true,override_breaker:true});}
  return r;}
{const g=$("qcGo");if(g)g.onclick=async()=>{g.disabled=true;const r=await startWithOverride();
  if(r&&r.ok){$("qConfirm").close();toast(r.hold?"연주 중 — 연주가 끝나면 시작합니다 (작업 중 칸에 연주 대기 카드)":"보드를 시작했습니다");await loadQueue();}   // 회신 기록은 자동으로 펼치지 않는다
  else{const e=$("qcErr");if(e){e.hidden=false;e.innerHTML=`<span class="code">${esc((r&&r.error)||"error")}</span><span>${esc((r&&r.message)||"시작하지 못했습니다")}</span>`;}g.disabled=false;}};}
{const c=$("qcClose");if(c)c.onclick=()=>$("qConfirm").close();const x=$("qcX");if(x)x.onclick=()=>$("qConfirm").close();
 const g=document.querySelector("#qConfirm .cf-grip");if(g)g.onclick=()=>$("qConfirm").close();}   // 위쪽 손잡이 막대도 닫기

/* ── 폴링 ── */
// 밴드가 창을 앞으로 올리면(「창에서 담기」·「대기 목록」·「⚙」) 5초 폴링을 기다리지 않고 바로 큐를 읽는다 — goTab·goDrawer 가 그때 실려 온다
let focusPollAt=0;
window.addEventListener("focus",()=>{const now=Date.now();if(B.mock||now-focusPollAt<800)return;focusPollAt=now;loadQueue();});
function scheduleQueuePoll(){clearTimeout(Q.pollT);const running=!!(Q.data&&Q.data.running);Q.pollT=setTimeout(loadQueue,running?1000:5000);}   // 러너 중 1초, 대기 중 5초
async function loadQueue(){if(B.mock){draw();scheduleQueuePoll();return Q.data;}
  const r=await api("/api/queue");
  // 밴드에서 「대기 목록」·「⚙」를 눌렀다 — 서버가 열어야 할 탭을 한 번 실어 보낸다.
  // 이미 떠 있는 창은 주소(#탭)를 바꿀 길이 없어 이 경로로만 탭이 바뀐다.
  if(r&&r.goTab&&typeof window.MW==="object"&&typeof MW.switchTab==="function")MW.switchTab(r.goTab,true);
  // 밴드의 「창에서 담기」 — 서버가 goDrawer 를 한 번 실어 보내면 담기 서랍을 연다
  if(r&&r.goDrawer===true&&typeof window.MW==="object"&&typeof MW.openDrawer==="function")MW.openDrawer({target:"root"});
  if(r&&Array.isArray(r.items))applyState(r);
  else if(r&&r.error==="not_found"){Q.avail=false;Q.data=null;draw();}   // 백엔드가 아직 없다 — 조용히
  else draw();   // 500·network: 마지막으로 받은 화면을 그대로 둔다 (하얗게 비우지 않는다)
  scheduleQueuePoll();return Q.data;}
/* `MW.refreshQueue` 는 셸(main.js) 것이다 — 여기서 같은 이름을 최상위 const 로 선언하면
   main.js 의 같은 이름 선언과 재선언 SyntaxError 가 나 main.js 가 통째로 안 실린다(실측: 화면이 빈다).
   board.js 안에서는 loadQueue 를 쓰고, 폴링 루프도 여기 하나만 돈다(main.js 는 loadQueue 가 있으면 위임한다). */
const renderQueue=draw;   // 옛 이름 (core.js 의 refreshState 가 인자 없이 부른다)

/* ── 담기 (옛 이름 그대로 — list-core.js·plan.js·tab-gather.js·tab-dict.js·works.js 가 부른다) ── */
async function queueAdd(item,silent,group){if(Q.avail===false){toast("큐 기능 준비 중입니다");return null;}
  const body={op:"add",item};if(group&&group!=="root")body.group=group;
  const r=await api("/api/queue",body);
  if(!r||!r.ok){toast("담지 못했습니다: "+opErrTxt(r));return null;}
  const pl=r.plan||{},ri=r.item||{};
  // 같은 대기 항목이 있으면 서버가 수량을 합친다 — 합쳤으면 총량을 알려 준다
  if(!silent)toast(r.merged
    ?`보드에 합침: ${item.name} +${fmtN(r.added||0)} → 총 ${fmtN(ri.target||ri.count||0)}${ri.type==="gather"?"개":"회"}${pl.passesPlanned?` · 예상 ${pl.passesPlanned}회`:""}`
    :`보드에 담음: ${item.name} ×${fmtN(item.target||item.count||1)}${pl.passesPlanned?` · 예상 ${pl.passesPlanned}회`:""}`);
  if(r.state)applyState(r.state);else await loadQueue();return r;}
// 수령 항목: 시설당 1개 (CLI 가 그 시설의 완료분을 한 번에 전부 수령)
const COLLECT_NA=(r)=>!r||r.error==="not_found"||/type|unknown|invalid_item|bad_item/i.test(String((r&&(r.error+" "+(r.message||"")))||""));
async function collectAdd(facility,name,n,silent,group){if(Q.avail===false){toast("큐 기능 준비 중입니다");return null;}
  const body={op:"add",item:{type:"collect",facility,name}};if(group&&group!=="root")body.group=group;
  const r=await api("/api/queue",body);
  if(!r||!r.ok){const e=(r&&r.error)||"";if(e==="duplicate"){if(!silent)toast(`이미 보드에 있음: ${facility} 수령`);return {dup:true};}
    if(e==="no_completed_work"||e==="no_completed_work_at_facility"){if(!silent)toast(`완료된 가공이 없습니다: ${facility}`);return null;}
    if(COLLECT_NA(r)){if(!silent)toast("수령 기능 준비 중");return null;}
    if(!silent)toast("담지 못했습니다: "+opErrTxt(r));return null;}
  if(!silent)toast(`보드에 담음: ${facility} 수령 ${fmtN((r.item&&r.item.count)||n||0)}건`);
  await loadQueue();return r;}
/* 「전체 수령」 — 완료된 시설마다 수령 카드를 담는다.

   **큐가 놀고 있으면 하나의 그룹으로 묶어 곧바로 실행한다** (묻지 않고 바로).
   확인창을 띄우지 않는 근거: 수령(`complete_altering_work`)은 **정령의 날개를 쓰지 않는다**
   (카탈로그에 소모 문장도, 결제 오류도 없다). 물어볼 비용이 없다.
   대신 이미 대기 중이던 다른 카드가 있으면 **그것도 같이 돈다**는 사실을 토스트로 말한다 —
   그쪽은 날개를 쓸 수 있고, 사용자가 시킨 적 없는 일이다.

   큐가 돌고 있으면 담기만 한다. 러너가 순서대로 가져간다. */
async function collectAll(){const list=S.collectable||[];if(!list.length)return;
  const running=!!(Q.data&&Q.data.running);
  const otherPending=((Q.data&&Q.data.items)||[]).filter((x)=>colOf(x)==="wait").length;   // 담기 **전**에 센다
  const ids=[];let ok=0,dup=0,sum=0;
  for(const c of list){const r=await collectAdd(c.facility,c.name,c.n,true);
    if(r&&r.dup)dup++;
    else if(r&&r.ok){ok++;sum+=(r.item&&r.item.count)||c.n;if(r.item&&r.item.id)ids.push(r.item.id);}}
  if(!ok&&!dup)return;   // 실패 토스트는 collectAdd 가 이미 냈다
  const what=`수령 ${ok}시설 · 총 ${fmtN(sum)}건${dup?` (이미 있음 ${dup})`:""}`;
  if(running||!ids.length){toast(`보드에 담음: ${what}`);await loadQueue();return;}
  if(ids.length>1){const g=await bop({op:"group_create",name:`수령 ${ok}시설`,ids});
    if(!g||!g.ok){toast(`보드에 담음: ${what} — 묶지 못해 낱장으로 뒀습니다`);await loadQueue();return;}}
  const r=await startWithOverride();await loadQueue();
  if(r&&r.ok)toast(`${what} — 바로 시작했습니다`+(otherPending?` · 먼저 대기 중이던 카드 ${otherPending}장도 같이 돕니다`:""));
  else toast(`보드에 담음: ${what} — 시작하지 못했습니다: ${opErrTxt(r)}`);}
{const w=$("wkCollectAll");if(w)w.onclick=collectAll;}
async function queueConfig(patch){return bop({op:"config",...patch});}

/* ── 1초마다 제자리 갱신 (경과·가공 남은 시간) — 보드 전체를 다시 그리면 스크롤·드래그가 흐트러진다 ── */
setInterval(()=>{
  document.querySelectorAll("[data-qw]").forEach((el)=>{el.textContent=waitTxt(el.dataset.qw);});
  document.querySelectorAll("[data-el]").forEach((el)=>{const t0=Number(el.dataset.el)||0;el.textContent=t0?fmtT(Date.now()/1000-t0):"00:00";});},1000);

/* ── 가짜 state (백엔드 전 화면 확인용) ──
   주소에 #queue?mockboard=1 을 붙였을 때만. 서버 응답 모양 그대로이고, 그 밖의 필드는 만들지 않는다. */
function mockState(){const now=Math.floor(Date.now()/1000);
  const C=(id,type,name,o)=>({id,type,name,status:"pending",column:"wait",progress:{},log:[],...o});
  return {items:[
    C("aaaa000001","gather","통나무",{target:250,progress:{passesPlanned:3}}),
    C("aaaa000002","craft","가죽 갑옷 상의",{count:1}),
    {id:"gggg000001",type:"group",name:"비약 루틴",repeat:5,loop:0,onError:"continue",retryFailed:true,waitAlter:false,status:"pending",column:"wait",log:[],
      summary:{count:2,line:"약초 채집 100 → 비약 제작 3회",roundDone:0,roundTotal:2,totalDone:0,inner:{wait:2,run:0,done:0,fail:0}},
      items:[C("bbbb000001","gather","약초",{target:100}),C("bbbb000002","craft","비약",{count:3})]},
    {id:"gggg000002",type:"group",name:"판재 루틴",repeat:3,loop:2,onError:"continue",retryFailed:true,waitAlter:false,status:"running",column:"run",log:[],
      summary:{count:4,line:"통나무 채집 100 → 판재 가공 10회 외 2",roundDone:2,roundTotal:4,totalDone:460,inner:{wait:1,run:1,done:2,fail:0}},
      items:[C("cccc000001","gather","통나무",{target:100,status:"running",column:"run",progress:{done:60,passes:1,passesPlanned:1,have:60}}),
        C("cccc000002","alter","판재",{count:10,status:"waiting",column:"run",progress:{passes:10}}),
        C("cccc000003","gather","통나무",{target:200,status:"done",column:"done",progress:{done:200,passes:2,passesPlanned:2,totalDone:200}}),
        C("cccc000004","craft","판재 상자",{count:1})]},
    {id:"gggg000003",type:"group",name:"가죽 루틴",repeat:2,loop:2,status:"done",column:"done",log:[],
      summary:{count:2,line:"생가죽 200 · 가죽 60",roundDone:2,roundTotal:2,totalDone:260,inner:{wait:0,run:0,done:2,fail:0}},items:[]},
    C("dddd000001","craft","가죽",{count:3,status:"error",column:"fail",error:"tool_not_ok",message:"tool is not ok"}),
  ],running:true,current:"cccc000001",currentGroup:"gggg000002",lastError:null,stopReason:null,onError:"continue",
  columns:{wait:3,run:1,done:1,fail:1},
  events:[{t:now-1625,kind:"start",id:"cccc000001",msg:"통나무 채집 시작"},{t:now-900,kind:"loop",id:"gggg000002",msg:"판재 루틴 회차 2 시작"},{t:now-120,kind:"done",id:"cccc000003",msg:"통나무 200개 완료"}]};}

/* ── 시작 ── */
(function boot(){const hq=(location.hash||"").split("?")[1]||"";
  if(/(?:^|&)mockboard=1/.test(hq)){B.mock=true;Q.avail=true;Q.data=mockState();}
  if(/(?:^|&)ev=1/.test(hq))Q.evOpen=true;})();

// 첫 줄에 renderBoard(index.html 로드 점검이 보는 대표 함수)와 옛 queue.js 호환 이름을 둔다 — 점검이 이 줄만 읽는다

/* ── 알림 ──────────────────────────────────────────
   알림 카드·큐 종료가 남긴 `notices`(GET /api/queue)를 폴링에서 집어 **한 번씩** 띄운다 — PC 창과 폰이 같은 코드다.
   「본 것」은 localStorage `mw.noticeSeen` = {t, id} — 페이지를 다시 열어도 지난 알림을 또 띄우지 않는다.
   id 는 서버 프로세스마다 1부터라 **시각(t)을 먼저** 본다. 처음 받은 응답의 알림은 (저장된 것이 없으면) 전부 본 것으로 친다 —
   etoastCheck 와 같은 까닭: 페이지를 열 때마다 지난 알림이 울리면 안 된다. */
const NOTICE_LS="mw.noticeSeen";
Q.noticeSeen=null;   // null = 첫 응답 전
const FREE_TYPES=new Set(["play","notify"]);   // 호출 0 · 날개 0 (workqueue.FREE_CARD_TYPES)
function noticeSeenLoad(){try{const v=JSON.parse(localStorage.getItem(NOTICE_LS)||"null");if(v&&typeof v==="object")return {t:Number(v.t)||0,id:Number(v.id)||0};}catch{}return null;}
function noticeSeenSave(s){try{localStorage.setItem(NOTICE_LS,JSON.stringify(s));}catch{}}
const noticeNewer=(n,s)=>!s||(Number(n.t)||0)>s.t||((Number(n.t)||0)===s.t&&(Number(n.id)||0)>s.id);
const isPhone=()=>{try{return !!(window.NET&&window.NET.REMOTE);}catch{return false;}};
function noticeCheck(d){const list=Array.isArray(d&&d.notices)?d.notices.filter((n)=>n&&typeof n==="object"):[];
  if(Q.noticeSeen===null){const saved=noticeSeenLoad();const last=list[list.length-1];
    Q.noticeSeen=saved||(last?{t:Number(last.t)||0,id:Number(last.id)||0}:{t:0,id:0});
    if(!saved)noticeSeenSave(Q.noticeSeen);}
  const fresh=list.filter((n)=>noticeNewer(n,Q.noticeSeen));
  if(!fresh.length)return [];
  const last=fresh[fresh.length-1];Q.noticeSeen={t:Number(last.t)||0,id:Number(last.id)||0};noticeSeenSave(Q.noticeSeen);
  const fire=fresh.slice(-3);   // 폴링이 오래 끊겼다 돌아왔으면 마지막 셋만
  for(const n of fire)fireNotice(n);
  return fire;}
let ntT=null;
function noticeBox(){let el=$("ntoast");if(!el){el=document.createElement("div");el.id="ntoast";el.setAttribute("popover","manual");el.hidden=true;
  el.innerHTML='<span class="bar"></span><span class="body" id="ntBody"></span><span class="x" id="ntX">×</span>';document.body.appendChild(el);
  el.onclick=()=>{try{el.hidePopover();}catch{}el.hidden=true;};}return el;}
/* 화면 토스트(10초, 누르면 닫힘) + 소리(카드의 sound) + 폰이면 진동 + 시스템 알림(허용했을 때). */
function fireNotice(n){const t=noticeBox();const body=$("ntBody")||t.querySelector(".body");
  if(body)body.innerHTML=`<b>${n.kind==="done"?"큐 종료":"큐 알림"}</b><span>${esc(n.text||"")}</span><span class="tm">${esc(hhmm(n.t||0))}</span>`;
  t.hidden=false;try{t.showPopover();}catch{}clearTimeout(ntT);ntT=setTimeout(()=>{try{t.hidePopover();}catch{}t.hidden=true;},10000);
  if(n.sound!==false)noticeSound();
  if(isPhone()&&typeof navigator!=="undefined"&&typeof navigator.vibrate==="function"){try{navigator.vibrate([200,100,200]);}catch{}}
  noticeSystem(n);}
/* 짧은 두 음 — WebAudio 오실레이터 (파일 없음). 브라우저는 사용자 손짓 전엔 소리를 막을 수 있다 — 「모바일 알림 허용」이 깨워 둔다. */
let ntAC=null;
function noticeSound(){try{const AC=window.AudioContext||window.webkitAudioContext;if(!AC)return false;ntAC=ntAC||new AC();const ac=ntAC;
  if(ac.state==="suspended"&&typeof ac.resume==="function"){const p=ac.resume();if(p&&p.catch)p.catch(()=>{});}
  const t0=ac.currentTime;
  for(const [f,dt] of [[880,0],[1175,0.18]]){const o=ac.createOscillator(),g=ac.createGain();o.type="sine";o.frequency.value=f;
    g.gain.setValueAtTime(0.0001,t0+dt);g.gain.exponentialRampToValueAtTime(0.25,t0+dt+0.02);g.gain.exponentialRampToValueAtTime(0.0001,t0+dt+0.35);
    o.connect(g);g.connect(ac.destination);o.start(t0+dt);o.stop(t0+dt+0.4);}
  return true;}catch{return false;}}
/* 시스템 알림 — 폰(밖)이거나 창이 가려져 있을 때만 (보이는 PC 창은 토스트로 충분하다). 서비스 워커가 있으면 그쪽으로
   (안드로이드 크롬은 페이지의 `new Notification` 을 막는다), 없으면 페이지에서. 닫힌 브라우저에는 닿지 않는다 — 푸시 서버가 없다. */
function noticeSystem(n){try{if(typeof Notification==="undefined"||Notification.permission!=="granted")return false;
  if(!isPhone()&&!(typeof document!=="undefined"&&document.hidden))return false;
  const app=(typeof APP_TITLE!=="undefined"&&APP_TITLE)||document.title||"";
  const title=`${app} · ${n.kind==="done"?"큐 종료":"큐 알림"}`;
  const opt={body:n.text||"",tag:"mw-notice",renotify:true,vibrate:[200,100,200],icon:"./icon.png",silent:n.sound===false};
  const sw=typeof navigator!=="undefined"&&navigator.serviceWorker&&navigator.serviceWorker.ready;
  if(sw&&sw.then)sw.then((reg)=>reg.showNotification(title,opt)).catch(()=>{try{new Notification(title,opt);}catch{}});
  else new Notification(title,opt);
  return true;}catch{return false;}}
/* 「모바일 알림 허용」 (담기 서랍 알림 갈래) — 사용자 손짓 안에서 한 번: 알림 권한 + 오디오 깨우기. 결과는 토스트 한 줄. */
async function noticePermission(){let msg="";noticeSound();
  if(typeof Notification==="undefined")msg="이 브라우저는 시스템 알림을 지원하지 않습니다 — 화면 토스트·소리만 옵니다 (iPhone 은 홈 화면에 추가한 뒤에만)";
  else{let p=Notification.permission;if(p==="default"){try{p=await Notification.requestPermission();}catch{}}
    msg=p==="granted"?"알림 허용됨 — 이 화면이 열려 있을 때 큐 알림을 받습니다 (닫힌 브라우저에는 닿지 않습니다)"
      :p==="denied"?"알림이 차단돼 있습니다 — 브라우저 설정에서 이 사이트의 알림을 허용하세요":"알림 허용을 정하지 않았습니다";}
  toast(msg);return msg;}
Object.assign(window.MW,{renderBoard,boardState,applyQueueState:applyState,loadQueue,renderQueue,queueAdd,collectAdd,collectAll,openConfirm,queueConfig,Q,
  B,COLS,COL_KO,colOf,isGroup,QTYPE_KO,QTYPE_CLS,QST_KO,APP_ERRS,ERR_HINT,OP_ERR,blockedKind,blockedHint,BLOCK_KIND_KO,kindKo,errHint,opErrTxt,chainStop,
  rootItems,findItem,findParent,qtyTxt,workLeft,waitTxt,lastStart,cardStart,collectTag,alterCollects,routeOf,progOf,subOf,txOf,itemCalls,itemWingCalls,
  editable,qtyCell,qEditApply,bindQEdit,bBusy,flushBoardRender,keepScrolls,
  segsHTML,cardHTML,groupFailBadge,groupFailShown,failBadgeHTML,groupCollapsedHTML,groupExpandedHTML,groupInnerHTML,canPull,columnHTML,runbarHTML,runStart,elapsedHTML,evPanelHTML,footHTML,undoBar,
  TAB_COLS,isNarrow,kbBannerHTML,kbRowHTML,kbGroupRowHTML,kbCurHTML,kbGroupHTML,kbRunHTML,kbColumnHTML,kbFootHTML,drawNarrow,kbMenuShow,kbMenuHide,panelDraw,
  draw,applyState,etoast,bop,bindOps,bindGroupRepeat,bindDrag,bindRunbar,
  refreshPreviewCost,scheduleQueuePoll,cfShortBox,COLLECT_NA,EV_KO,evName,mockState,
  noticeCheck,fireNotice,noticeSound,noticePermission,FREE_TYPES});   // 알림
