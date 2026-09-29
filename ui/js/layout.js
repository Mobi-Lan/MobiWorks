// layout.js — 고정 프레임 점검 훅(?dump=1). 좌우·상하 드래그 분할은 보드가 4열이 되면서 없어졌다.
/* 옛 #qwRow / #splitH / #splitV 와 localStorage 의 mw.splitH·mw.splitV 는 더 이상 쓰지 않는다.
   읽는 곳이 하나도 없으므로 남아 있던 저장값은 시작할 때 한 번 지운다 (값만 남아 혼동을 준다). */
function layoutInit(){for(const k of ["mw.splitH","mw.splitV"])lsSet(k,null);}
layoutInit();
/* ?dump=1 은 프레임 상태를 화면 구석과 콘솔에 찍는다 (검증용).
   pageScroll 은 반드시 false 여야 한다 — 스크롤은 열·카드 안쪽에만 생긴다. */
function layoutDump(){const de=document.documentElement;
  const box=(id)=>{const el=$(id);if(!el)return null;const r=el.getBoundingClientRect();return Math.round(r.width)+"×"+Math.round(r.height);};
  const o={scrollH:de.scrollHeight,clientH:de.clientHeight,bodyScrollH:document.body.scrollHeight,
    pageScroll:de.scrollHeight>de.clientHeight||document.body.scrollHeight>de.clientHeight,
    tab:(typeof mwTab!=="undefined")?mwTab:"?",
    runbar:box("runbar"),board:box("board"),boardFoot:box("boardFoot"),tabCard:box("tabCard"),worksCard:box("worksCard")};
  console.log("LAYOUT_DUMP "+JSON.stringify(o));
  let b=$("dumpBox");if(!b){b=document.createElement("div");b.id="dumpBox";
    b.style.cssText="position:fixed;left:8px;bottom:8px;z-index:99;background:var(--backdrop);color:var(--ok);font:11px Consolas,monospace;padding:4px 8px;border-radius:6px;pointer-events:none";
    document.body.appendChild(b);}
  b.textContent=JSON.stringify(o);}

Object.assign(window.MW,{layoutInit,layoutDump});
