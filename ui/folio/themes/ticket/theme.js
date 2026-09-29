/* 티켓형 연출 — 6.0초 (카드형과 같은 예산, host.T)
     2.2  들어옴 : 표 한 장이 화면 아래에서 살짝 기운 채 올라와 가운데에 바로 선다 (spin + lift)
     0.6  드러냄 : 절취선에서 스텁이 찢어져 벌어지고 · 「NOW PLAYING」 도장이 쾅 찍힌다(표가 한 번 눌린다) · 글이 들어온다
     2.4  유지   : 게스트 리스트(사람 줄)가 한 줄씩 · 스텁의 바코드를 붉은 빛이 한 번 훑는다
     0.8  퇴장   : 카드형처럼 표가 살짝 움츠렸다 위로 휙 날아가고 화면 전체가 옅어진다
   표는 그 곡 한 장뿐 — 아래에 부채처럼 꽂힌 다른 표들은 뺐다 (첫 연출에만 나오고 두 번째부터 안 나왔다).
   데이터 계약: { song, players, badge, kicker, inst } — 커버는 곡 항목의 `cover`(호스트가 검사한 `/folio/covers/<번호>?v=…`).
   바코드는 곡 번호·제목으로 늘 같게 그린다. */
OpeningHost.register("ticket", { label: "티켓형", mount(root, host) {
  const { T, esc, wait, fmt } = host;
  root.innerHTML = `
    <div class="tk-dim"></div>
    <div class="tk-layer"></div>
    <div class="tk-caption"><small>Mabinogi Mobile with MobiFolio</small><b class="tk-capsong">—</b></div>`;
  const $ = s => root.querySelector(s);
  const layer = $(".tk-layer"), dim = $(".tk-dim"), capSong = $(".tk-capsong");
  let playing = null, sceneFade = null;

  // 바코드 — 곡 번호·제목으로 늘 같은 줄무늬 (장식)
  function barcode(s) {
    const r = host.rng(`${s.id}|${s.title}`);
    let x = 0, bars = "";
    const bar = w => { bars += `<rect x="${x}" y="0" width="${w}" height="40"/>`; x += w; };
    bar(1); x += 1; bar(1); x += 2;
    while (x < 92) { bar(r() < .5 ? 1 : r() < .75 ? 2 : 3); x += r() < .6 ? 1 : 2; }
    x += 1; bar(1); x += 1; bar(1);
    let digits = "";
    for (let i = 0; i < 12; i++) digits += Math.floor(r() * 10);
    return { svg: `<svg viewBox="0 0 ${x} 40" preserveAspectRatio="none" fill="#1b1813">${bars}</svg>`, digits };
  }
  // 바코드 <svg> — 수(막대 자리·폭)와 고정 글자만 든다. 곡 제목은 씨앗으로만 쓰고 그림에 안 들어간다
  function barcodeHtml(bc) { return bc.svg; }
  // 스텁의 SEAT — 악기 줄의 [대괄호] 갈래 (「[피아노] 3화음 …」 → 피아노). 없으면 배지(솔로·n인 합주)
  function seatOf(inst, badge) {
    const m = /^\s*\[([^\]]{1,20})\]/.exec(inst || "");
    return m ? m[1] : (String(inst || "").trim().split(/\s+/)[0] || badge);
  }
  function stamp2(d) { const p = x => String(x).padStart(2, "0"); return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())}`; }
  function clock(d) { const p = x => String(x).padStart(2, "0"); return `${p(d.getHours())}:${p(d.getMinutes())}`; }

  function sceneReset() { if (sceneFade) { sceneFade.cancel(); sceneFade = null; } }
  function reset() {
    sceneReset();
    layer.textContent = "";
    dim.classList.remove("on");
    capSong.textContent = "—";
  }

  async function run({ song, players, badge, kicker, inst }) {
    sceneReset();
    layer.textContent = "";      // 지난 표(위로 날아가 화면 밖에 남은 것)를 치운다 — 호스트는 reset 을 부르지 않는다
    dim.classList.remove("on");
    const data = host.songData(song);
    const { n, shown, more } = host.roster(players);
    const instText = String(inst || "").trim();
    const badgeText = badge || host.autoBadge(n);

    // 표 자리 — 폭 80vw (높이 62vh 까지), 2.4 : 1, 화면 45% 높이에 가운데
    const W = Math.min(innerWidth * 0.8, innerHeight * 0.62 * 2.4), H = W / 2.4;
    const cx = innerWidth / 2, cy = innerHeight * 0.45;
    // 1) 들어옴의 출발 — 화면 아래 밖, 작고 살짝 기운 채 (부채는 없다 — 표 한 장뿐)
    const s0 = 0.55;
    const sx = 0, sy = innerHeight - cy + H * s0;

    const bc = barcode(data);
    const now = new Date();
    // 게스트 리스트 조립 (…Html = HTML 을 조립하는 함수 — 칭호·이름은 esc 를 지난다, 보안)
    const guestsHtml = () => shown.map(([t, nm], i) => `<div class="tk-g" style="--n:${Number(i)}"><span class="no">${String(i + 1).padStart(2, "0")}</span>` +
      (t ? `<span class="t">${esc(t)}</span>` : "") + `<span class="n">${esc(nm)}</span></div>`).join("") +
      (more > 0 ? `<div class="tk-g more" style="--n:${Number(shown.length)}"><span class="no">+</span><span class="n">외 ${Number(more)}</span></div>` : "");
    const el = document.createElement("div");
    el.className = "tk-ticket";
    el.style.cssText = `left:${cx - W / 2}px;top:${cy - H / 2}px;width:${W}px;height:${H}px;`;
    el.style.setProperty("--cover", data.cover);
    el.innerHTML = `
      <div class="tk-main"><div class="tk-tint"></div><div class="tk-cover"></div>
        <div class="tk-body">
          <div class="tk-hdr"><span class="tk-kicker"><b>●</b> ${esc(kicker || host.autoKicker(n))}</span><span class="tk-badge">${esc(badgeText)}</span></div>
          <div class="tk-spacer"></div>
          <div class="tk-inst"${instText ? "" : " hidden"}>${esc(instText)}</div>
          <div class="tk-song">${esc(data.title)}</div>
          <div class="tk-meta">
            ${data.duration != null ? `<span><b>RUN</b>${fmt(data.duration)}</span>` : ""}
            ${data.id ? `<span><b>NO.</b>${esc(host.idLine(data.id))}</span>` : ""}
            <span><b>DATE</b>${esc(stamp2(now).slice(5))} ${esc(clock(now))}</span>
          </div>
        </div>
        <div class="tk-guests"><div class="tk-gh">GUEST LIST</div>${guestsHtml()}</div>
        <div class="tk-stamp">NOW PLAYING<small>${esc(stamp2(now))}</small></div>
      </div>
      <div class="tk-stub"><div class="tk-tint"></div>
        <div class="tk-sbody">
          <div class="tk-admit">ADMIT ONE<small>MOBIFOLIO</small></div>
          <div class="tk-seat">SEAT <b>${esc(seatOf(instText, badgeText))}</b></div>
          <div class="tk-code">${barcodeHtml(bc)}<div class="tk-laser"></div></div>
          <div class="tk-serial"><span>${esc(bc.digits.slice(0, 6))}</span><span>${esc(bc.digits.slice(6))}</span></div>
        </div>
      </div>`;
    layer.appendChild(el);
    capSong.textContent = data.title;
    dim.classList.add("on");

    // 2) 들어옴 — 아래에서 올라와 살짝 넘쳤다가 바로 선다 (spin + lift 예산을 합쳐 한 동작)
    await el.animate([
      { transform: `translate(${sx}px,${sy}px) scale(${s0}) rotate(6deg)` },
      { transform: `translate(0px,${-H * 0.03}px) scale(1.02) rotate(-2.2deg)`, offset: .72 },
      { transform: "translate(0px,0px) scale(1) rotate(0deg)" },
    ], { duration: T.spin + T.lift, easing: "cubic-bezier(.22,.9,.25,1)", fill: "forwards" }).finished;

    // 3) 드러냄 — 스텁이 찢어져 벌어지고, 도장이 찍힌다
    el.classList.add("revealed");
    const u = W / 100;
    el.querySelector(".tk-stub").animate([
      { transform: "none" },
      { transform: `translate(${1.5 * u}px,${0.7 * u}px) rotate(3.6deg)`, offset: .45 },
      { transform: `translate(${1.1 * u}px,${0.45 * u}px) rotate(2.4deg)` },
    ], { duration: 420, easing: "cubic-bezier(.3,1.4,.5,1)", fill: "forwards" });
    el.querySelector(".tk-stamp").animate([
      { opacity: 0, transform: "rotate(-11deg) scale(2.1)" },
      { opacity: .95, transform: "rotate(-11deg) scale(.94)", offset: .72 },
      { opacity: .9, transform: "rotate(-11deg) scale(1)" },
    ], { duration: 300, delay: 180, easing: "cubic-bezier(.55,0,.9,.5)", fill: "forwards" });
    el.animate([{ transform: "translateY(0px)" }, { transform: `translateY(${0.5 * u}px) scale(.995)` }, { transform: "translateY(0px)" }],
      { duration: 200, delay: 400, composite: "add" });        // 도장이 닿는 순간 표가 한 번 눌린다
    await wait(T.flip);

    // 4) 유지 — 바코드를 붉은 빛이 한 번 훑는다
    const code = el.querySelector(".tk-code");
    el.querySelector(".tk-laser").animate([
      { transform: "translateX(0px)", opacity: 0 }, { opacity: 1, offset: .1 }, { opacity: 1, offset: .9 },
      { transform: `translateX(${code.clientWidth}px)`, opacity: 0 },
    ], { duration: 900, delay: 350, easing: "ease-in-out", fill: "forwards" });
    await wait(T.hold);

    // 5) 퇴장 — 카드형과 같은 「위로 휙」 (살짝 움츠렸다가 곧게 위로, 조금 작아지며) + 화면 전체 옅어짐
    const outMove = el.animate([
      { transform: "translate(0px,0px) scale(1) rotate(0deg)", offset: 0 },
      { transform: "translate(0px,40px) scale(.98) rotate(0deg)", offset: .18 },
      { transform: `translate(0px,${-innerHeight * 1.3}px) scale(.7) rotate(-4deg)`, offset: 1 },
    ], { duration: T.out, easing: "cubic-bezier(.5,-.2,.2,1)", fill: "forwards" });
    sceneFade = root.animate([{ opacity: 1 }, { opacity: 0 }], { duration: T.out, easing: "linear", fill: "forwards" });
    await Promise.all([outMove.finished, sceneFade.finished]);
    dim.classList.remove("on");
  }

  function play(d) {
    if (playing) return playing;
    playing = run(d).finally(() => { playing = null; });
    return playing;
  }
  // 곡 목록을 새로 받았을 때 (호스트가 부른다) — 표는 연출 때마다 새로 그리므로 할 일이 없다
  function refresh() {}
  return { play, reset, refresh };
} });
