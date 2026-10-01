// tab-gather.js — 채집 탭: 이름 | 도구 | 회당 상한 | 캘 개수 → 담기
// 「캘 개수」는 **이번에 캘 개수**다 — 보유량을 그 수까지 채우는 목표가 아니다.
// CLI 는 호출 한 번에 최대 100개를 캐고 멈춘다(목표 인자 없음) → 러너가 시작 전 가방을 적고, 가방이 「시작 + 캘 개수」에
// 닿으면 멈춘다 (마지막 회는 도는 동안 가방을 읽다가 stop_action). 행에는 「3회 · 날개 15」와 「가방 47 → 목표 297」을 적는다.
// 검색·초성·스킬 칩·「도구 있음만」·정렬은 list-core 의 공통 축(qMatch/axPass/sortRows/fx)을 그대로 쓴다.
const GCAP = 100;                                   // 게임 규칙: execute_gathering 회당 상한
const GT = {};                                      // 항목별 캘 개수 — 필터·정렬로 다시 그려도 유지
// #gather?gt=250 : 모든 행의 기본 캘 개수 (헤드리스 검증용 — 클릭 없이 입력 상태를 띄운다)
const GT_DEF = (() => {const m = /(?:^|&)gt=(\d+)/.exec((location.hash || "").split("?")[1] || "");
  return m ? Math.max(1, Math.min(99999, Number(m[1]))) : GCAP;})();
const gtOf = (n) => GT[n] || GT_DEF;
const gclamp = (v) => Math.max(1, Math.min(99999, Math.round(Number(String(v).replace(/[^0-9]/g, "")) || 0) || 1));
// 안내 두 줄 — 「3회 · 날개 15」 · 「가방 47 → 목표 297」 (가방을 모르면 둘째 줄 없음)
const gBag = (name) => (typeof bagOf === "function" ? bagOf(name) : null);
const gHelpTitle = (name, t) => {const g = gGoalTxt(gBag(name), t);
  return `${fmtN(t)}개 캐기 · 예상 ${gRunsTxt(t)} (1회 최대 ${GCAP}개)${g ? ` · ${g}` : ""} — 가방이 목표에 닿으면 멈춥니다`;};
const gHelpHTML = (name, t) => {const g = gGoalTxt(gBag(name), t);
  return `<b>${esc(gRunsTxt(t))}</b>${g ? `<i>${esc(g)}</i>` : ""}`;};
// 낚시 전용 항목은 자동낚시만 켜고 즉시 돌아온다(카탈로그) — 캘 개수로 반복하면 가방이 안 늘어 상한까지 헛돈다.
// 스킬은 이름 추정값이라(칩 줄의 「추정」 배지) 도구가 있을 때만 이 표시를 쓴다
const isFish = (x) => (x.skill || "") === "낚시" && !!x.toolOk;

function renderGather(){const g=(S.snap&&S.snap.gather)||[];const f=fx("gather");const rows=sortRows(g.filter((x)=>qMatch(x)&&axPass("gather",x,f)),f,"gather");
  $("tabSum").textContent=`${fmtN(rows.length)}종${rows.length!==g.length?` · 전체 ${fmtN(g.length)}`:""}`;
  if(!rows.length)return '<div class="empty">채집 가능한 항목이 없습니다.</div>';
  const n=limitOf("gather",rows.length);const shown=rows.slice(0,n);
  const hd=`<div class="rc g hd"><span>이름</span><span>도구</span><span>회당 상한</span><span class="thd">캘 개수 → 담기</span></div>`;
  return '<div class="list gl">'+hd+shown.map(gatherRow).join("")+'</div>'+moreBtn("gather",shown.length,rows.length)
    +`<div class="gfoot">「캘 개수」만큼 가방이 늘면 멈춥니다 (시작 전 가방 + 캘 개수) · 1회 최대 ${GCAP}개 · 날개 5개 — 마지막 회는 도는 동안 가방을 읽다가 목표에서 끊습니다 · 소모품·무게에 따라 1회에 ${GCAP}개보다 적게 들면 회수가 늘어납니다 · 낚시는 자동낚시만 켜고 즉시 완료 처리</div>`;}

// 한 행. 도구가 없으면 담기를 잠근다(큐에 담아도 실행 전 점검에서 tool_not_ok 로 끝난다).
// 낚시는 캘 개수가 의미 없어 스테퍼를 잠그고 한 번만 담는다
function gatherRow(x){const ok=!!x.toolOk;const fish=isFish(x);const t=fish?GCAP:gtOf(x.name);const d=ok?"":" disabled";
  const tool=fish?'<span class="badge gold">자동낚시</span>':(ok?'<span class="badge ok">도구 OK</span>':'<span class="badge bad">도구 없음</span>');
  // 안내 문구는 고정 폭 열이라(tab-gather.css) 길면 잘린다 — 전체는 title 로 남긴다
  const step=fish
    ?`<span class="stepper off"><button disabled>−</button><span class="v">—</span><button disabled>+</button></span><span class="gp num" title="자동낚시는 목표 개수로 반복할 수 없습니다">진행 셀 수 없음</span>`
    :`<span class="stepper"><button data-gstep="-1" title="−100">−</button><input data-gt type="text" inputmode="numeric" value="${t}" aria-label="캘 개수" title="캘 개수 — 1~99,999 직접 입력"><button data-gstep="1" title="+100">+</button></span><span class="gp num" title="${esc(gHelpTitle(x.name,t))}">${gHelpHTML(x.name,t)}</span>`;
  return `<div class="rc g${ok?"":" off"}" data-gname="${esc(x.name)}"${fish?' data-gfish="1"':""}>`
    +`<span class="c1"><span class="nm">${esc(x.name)}</span>${x.skill?`<span class="badge line">${esc(x.skill)}</span>`:""}${fish?"":`<span class="gsm num">${esc([gRunsTxt(t),gGoalTxt(gBag(x.name),t)].filter(Boolean).join(" · "))}</span>`}</span>`
    +`<span>${tool}</span>`
    +`<span class="cap num">${fish?"즉시 완료":`회당 ${GCAP}`}</span>`
    +`<span class="gact">${step}<button class="pbtn ${ok?"gold":"dim"}" data-gadd${d}>담기</button></span></div>`;}

// 캘 개수를 바꾸면 그 행의 예상 회수만 고쳐 쓴다 (목록 전체를 다시 그리지 않는다 — 입력 포커스·스크롤 유지)
function gsync(row){if(row.dataset.gfish)return GCAP;const inp=row.querySelector("[data-gt]");if(!inp)return gtOf(row.dataset.gname);
  const t=gclamp(inp.value);GT[row.dataset.gname]=t;const name=row.dataset.gname;const gp=row.querySelector(".gp");
  if(gp){gp.innerHTML=gHelpHTML(name,t);gp.title=gHelpTitle(name,t);}
  // 좁은 폭은 안내 열이 빠지고 이름 아래 작은 줄(.gsm)이 같은 글을 적는다
  const sm=row.querySelector(".gsm");if(sm)sm.textContent=[gRunsTxt(t),gGoalTxt(gBag(name),t)].filter(Boolean).join(" · ");
  return t;}

// 행은 필터·정렬마다 다시 그려지므로 개별 바인딩 대신 document 위임 (tab-gather.js 로드 시 한 번만 건다)
document.addEventListener("click",(e)=>{const el=e.target;if(!(el&&el.closest))return;
  const st=el.closest("[data-gstep]");
  if(st){const row=st.closest(".rc.g");const inp=row&&row.querySelector("[data-gt]");if(!inp||st.disabled)return;
    inp.value=gclamp(gstepN(gclamp(inp.value),Number(st.dataset.gstep)));gsync(row);return;}
  const ad=el.closest("[data-gadd]");if(!ad||ad.disabled)return;
  const row=ad.closest(".rc.g");if(!row)return;const t=gsync(row);
  queueAdd({type:"gather",name:row.dataset.gname,target:t});});
document.addEventListener("input",(e)=>{const i=e.target;if(!(i&&i.closest)||!i.matches("[data-gt]"))return;
  i.value=String(i.value).replace(/[^0-9]/g,"").slice(0,5);const row=i.closest(".rc.g");if(row)gsync(row);});

Object.assign(window.MW,{renderGather,gatherRow,gsync,isFish,GCAP,GT,gtOf,gclamp});
