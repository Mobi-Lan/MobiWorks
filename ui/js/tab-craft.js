// tab-craft.js — 제작·가공 탭 목록 렌더(renderRecipes)
// 행 44px 그리드 [▸ | 이름+배지 | ×산출 | 상태 | 부족 재료 | 회수 스테퍼+담기], sticky 헤더, 발에 안내·범위
const CRAFT_HINT="행을 클릭하면 재료·경로 상세 · 「재료 부족」 행의 담기는 부족 재료도 함께 담을지 묻습니다";
function renderRecipes(list,kind){const f=fx(kind);const all=sortRows(list.filter((r)=>qMatch(r)&&axPass(kind,r,f)),f,kind);
  $("tabSum").innerHTML=`가능 <span class="num">${fmtN(all.filter((r)=>r.ready).length)}</span> / ${fmtN(all.length)}${all.length!==list.length?` <span class="muted">· 전체 ${fmtN(list.length)}</span>`:""}`;
  if(kind==="craft"&&S.snap&&S.snap.craftingUnlocked===false)return '<div class="empty">제작이 아직 해금되지 않았습니다.</div>';
  if(!all.length)return '<div class="empty">표시할 항목이 없습니다.</div>';
  const n=limitOf(kind,all.length);const rows=all.slice(0,n);
  const hd='<div class="rc hd"><span></span><span>이름</span><span class="per num">×산출</span><span>상태</span><span class="miss">부족 재료</span><span class="act">담기</span></div>';
  return '<div class="list">'+hd+rows.map((r)=>{const id=rid(kind,r);const open=S.open.has(id);const kc=r.knownCount||Object.keys(r.known||{}).length;
    const lack=(r.missing||[]).filter((m)=>m.short>0);
    // 부족 재료 칸: 재료는 「이름 보유/필요」(보유 = 가방+창고 합산), 막힘 행은 막힌 이유를 여기에
    const miss=r.blocked
      ?(r.reasonKo?`<span class="chip why">${esc(r.reasonKo)}</span>`:"")
      :lack.map((m)=>`<span class="chip" title="가방 ${fmtN(m.bag||0)} · 창고 ${fmtN(m.storage||0)} · ${fmtN(m.short)}개 부족">${esc(m.name)} <b>${fmtN((m.bag||0)+(m.storage||0))}/${fmtN(m.required||m.short)}</b></span>`).join("");
    return `<div class="rc${S.sel&&S.sel.id===id?" sel":""}" data-k="${kind}" data-n="${esc(r.name)}" data-id="${esc(id)}" data-v="${r.variant||1}" data-vs="${r.variants||1}">`+
      `<button class="tg${open?" open":""}${kc?"":" dim"}" data-key="${esc(id)}" title="${kc?`확인된 재료 ${kc}개`:"재료 미확인"}">▸</button>`+
      `<span class="c1"><span class="nm">${esc(r.name)}</span>${varBadge(r)}${r.cat?`<span class="badge line">${esc(r.cat)}</span>`:""}</span>`+
      `<span class="per num">×${r.per}</span>`+
      `<span>${r.ready?'<span class="badge ok">가능</span>':(r.blocked?'<span class="badge warn">막힘</span>':'<span class="badge bad">재료 부족</span>')}</span>`+
      `<span class="miss">${miss}</span>`+
      `<span class="act">${rowAdd(kind,r,id)}</span>`+
      `</div>${open?knownRow(r):""}`;}).join("")+'</div>'+listFoot(kind,rows.length,all.length,CRAFT_HINT);}
Object.assign(window.MW,{renderRecipes,CRAFT_HINT});
