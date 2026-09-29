// update.js — 업데이트 확인·적용(경량판)
/* ── 업데이트 ── */
let updInfo=null;
// 결과 표시 대상: 설정 창이 열려 있으면 그 안의 카드, 아니면 상단 바
// 업데이트 결과를 어디에 적을까 — **설정 탭이 켜져 있을 때만** 그 안 상자에. (설정이 팝업이던 시절에는
// 창이 열렸는지를 봤다. 탭이 되면서 「그 섹션이 보이는가」로 바뀐다.)
function updTarget(){const sec=$("tabSettings");const box=(sec&&!sec.hidden)?$("sUpdBox"):null;if(box){box.hidden=false;}return box;}
// 버튼 글: 임베디드 판(서명된 pythonw 로 도는 zip)은 latest.json 에 zip 칸이 있으면 제자리 업데이트(1.0.2), 없으면
// (옛 매니페스트) 받는 곳만 연다 — 서버가 `inplace` 로 알려 준다
function updPageOnly(){return !!(updInfo&&updInfo.embed&&!updInfo.inplace);}
function updBtnLabel(){return updPageOnly()?"받는 곳 열기":"지금 업데이트";}
function updShow(box,cls,html,btn){ if(box){box.className="updbox "+cls;box.innerHTML=`<span class="dot ${cls==="ok"?"on":cls==="bad"?"off":""}"></span><span class="updtxt">${html}</span>${btn?`<button class="small pri" id="sUpdGo">${esc(updBtnLabel())}</button>`:""}`;const g=$("sUpdGo");if(g)g.onclick=()=>applyUpdate(box);}
  else{$("updText").innerHTML=html;$("updGo").textContent=updBtnLabel();$("updGo").hidden=!btn;$("updLater").hidden=false;$("updBar").hidden=false;} }
function updText(box,t){const el=box?box.querySelector(".updtxt"):$("updText");if(el)el.textContent=t;}
async function checkUpdate(quiet){const box=updTarget();if(box)updShow(box,"","확인 중…",false);
  const r=await api("/api/update/check");
  if(!r.ok){if(box)updShow(box,"bad",esc(r.message||"업데이트 확인 실패"),false);else if(!quiet)toast(r.message||"업데이트 확인 실패");return r;}
  if(r.available){updInfo=r;const html=`<b>새 버전 v${esc(r.latest)}</b> · 현재 v${esc(r.current)}${r.notes?" · "+esc(r.notes):""}${r.embed&&!r.inplace?" · 새 zip 을 받아 폴더를 바꿔 주세요 (기록·설정은 그대로)":r.lite?"":" · 경량판에서만 자동 적용됩니다"}`;
    if(box)updShow(box,"new",html,!!r.lite);else updShow(null,"new",html,!!r.lite);}
  else{if(box)updShow(box,"ok",`최신 버전입니다 · v${esc(r.current)}`,false);else if(!quiet)toast(`최신 버전입니다 (v${r.current})`);}
  return r;}
$("updLater").onclick=()=>{$("updBar").hidden=true;};
$("updGo").onclick=()=>applyUpdate(null);
async function applyUpdate(box){if(!updInfo)return;const btn=box?$("sUpdGo"):$("updGo");if(btn)btn.disabled=true;updText(box,updPageOnly()?"받는 곳을 여는 중…":"다운로드·검증 중…");
  const r=await api("/api/update/apply",{url:updInfo.url,sha256:updInfo.sha256,latest:updInfo.latest});
  if(r.ok&&r.port){updText(box,r.message+" 새 버전이 뜨면 이 창이 그대로 이어집니다…");if(!box)$("updLater").hidden=true;const origin=`http://127.0.0.1:${r.port}`;
    // 도우미가 바꾸는 길(임베디드 판의 파이썬이 바뀐 경우)은 옛 판이 먼저 끝나야 해서 더 오래 걸린다 — 서버가 `wait`(초)로 알려 준다
    let ok=false;const tries=Math.max(40,Math.round((r.wait||20)*2));for(let i=0;i<tries;i++){await new Promise((res)=>setTimeout(res,500));try{await fetch(origin+"/api/health",{mode:"no-cors",cache:"no-store"});ok=true;break;}catch{}}
    if(ok){location.href=origin+"/";return;}
    updText(box,"업데이트가 완료되었습니다. 앱을 다시 실행해 주세요.");askOk("새 창으로 자동 전환되지 않았습니다. 앱을 다시 실행해 주세요.",{title:"업데이트가 끝났습니다",ok:"알겠습니다",cancel:""});}
  else if(r.ok){updText(box,r.message);if(btn)btn.disabled=false;}
  else{if(btn)btn.disabled=false;updText(box,"업데이트 실패: "+(r.message||r.error||""));}}
$("sUpdNow").onclick=async()=>{if(!setLoaded)return;const u=$("sUpdUrl").value.trim();
  if(u){const r=await api("/api/settings",{settings:{update_url:u}});if(!r.ok){toast(r.message||r.error);return;}}   // 빈 값이면 저장하지 않는다 (기존 주소를 지우지 않게)
  await checkUpdate(false);};

Object.assign(window.MW,{updTarget,updShow,updText,checkUpdate,applyUpdate});
