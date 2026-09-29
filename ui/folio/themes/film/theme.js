/* 필름형 연출 — 6.0초 (카드형과 같은 예산, host.T)
     1.5  고르기 : 화면 아래 필름 띠가 흐르다가 빨라졌다 느려지며 그 곡의 칸이 가운데에 선다 (나머지 칸은 어두워진다)
     0.7  들어올림: 그 칸이 띠에서 떠올라 커지며 스틸 왼쪽의 포스터 자리로 간다 · 레터박스가 위아래에서 밀려 들어온다 ·
                   스틸(커버를 흐리게 깐 와이드스크린 판)이 포스터에서부터 옆으로 열린다
     0.6  드러냄 : 영사기처럼 한 번 번쩍 · 그레인·비네트 · 위에 킥커와 배지, 가운데 아래에 자막처럼 악기 줄 → 곡 제목
     2.4  유지   : 오른쪽 기둥에서 크레딧(사람 줄)이 위로 올라간다 · 배경은 천천히 당겨진다(켄 번스)
     0.8  퇴장   : 띠가 왼쪽으로 달려 나가고 레터박스가 셔터처럼 닫히며 화면 전체가 옅어진다
   데이터 계약: { song, players, badge, kicker, inst } — 커버는 곡 항목의 `cover`(호스트가 검사한 `/folio/covers/<번호>?v=…`).
   띠에 올리는 곡: 대기열 → 뽑힐 곡 → 나머지 (host.featured) — 악보함 전부를 올리면 그림 수백 장을 한꺼번에 받는다. */
OpeningHost.register("film", { label: "필름형", mount(root, host) {
  const { T, esc, wait, fmt, mod } = host;
  root.innerHTML = `
    <div class="fm-dim"></div>
    <div class="fm-bar top"></div><div class="fm-bar bot"></div>
    <div class="fm-strip"></div>
    <div class="fm-layer"></div>
    <div class="fm-caption"><small>Mabinogi Mobile with MobiFolio</small><b class="fm-capsong">—</b></div>`;
  const $ = s => root.querySelector(s);
  const strip = $(".fm-strip"), layer = $(".fm-layer"), dim = $(".fm-dim"), capSong = $(".fm-capsong");
  const barTop = $(".fm-bar.top"), barBot = $(".fm-bar.bot");

  let cells = [], pitch = 0, total = 1, offset = 0, running = true, playing = null, sceneFade = null, lastT = performance.now();
  const IDLE = 34;                                    // 평소에 흐르는 속도 (px/s)
  const FEATURED = 24;                                // 띠에 올리는 곡 수의 위 (칸은 이 수의 배수로 창 폭을 채운다)
  let sig = "";                                       // 지난 띠의 모습 — 같으면 다시 짓지 않는다 (흐르던 띠가 튀지 않게)

  // 칸 폭은 창 높이로 정한다 (CSS 의 --strip-h 17vh · 칸 72% · 1:1.42 · 칸 사이 여백) — 숨겨진 동안에도 잴 수 있게 계산으로
  function geom() {
    const sh = innerHeight * 0.17, fh = sh * 0.72, fw = fh / 1.42;
    return { sh, fh, fw, pitch: fw + sh * 0.2 };
  }
  function edgeCode(s, i) {
    const r = host.rng(`${s.id}|${s.title}`);
    return `MF ${String(Math.floor(r() * 9000) + 1000)}  ${i % 36 + 1}A`;      // 필름 가장자리 번호 흉내 (곡마다 같게)
  }
  function setCell(el, s, i) {
    el.dataset.title = s.title || "";
    el.style.setProperty("--cover", s.cover || host.UNKNOWN_COVER);
    el.innerHTML = `<div class="fm-frame"></div><div class="fm-edge">${esc(edgeCode(s, i))}</div>`;
    el._song = s;
  }
  const listSig = list => JSON.stringify(list.map(s => [s.id, s.title, s.cover]));
  function build() {
    const g = geom();
    pitch = g.pitch;
    root.style.setProperty("--pitch", `${pitch.toFixed(2)}px`);
    const list = host.songs.length ? host.featured(FEATURED) : [{ id: null, title: "", cover: host.UNKNOWN_COVER }];
    sig = listSig(list);
    const need = Math.ceil((innerWidth + pitch * 3) / pitch);
    const count = Math.ceil(Math.max(need, list.length) / list.length) * list.length;   // 이음새에서 같은 곡이 붙지 않게
    total = count * pitch;
    strip.textContent = "";
    cells = [];
    for (let i = 0; i < count; i++) {
      const el = document.createElement("div");
      el.className = "fm-cell";
      el._i = i;
      setCell(el, list[i % list.length], i);
      strip.appendChild(el);
      cells.push(el);
    }
    place();
  }
  const xOf = i => mod(i * pitch - offset, total) - pitch;       // 칸의 왼쪽 끝 (−pitch … total−pitch)
  function place() { for (const el of cells) el.style.transform = `translateX(${xOf(el._i).toFixed(1)}px)`; }
  (function tick(t) {
    const dt = Math.min(0.1, Math.max(0, (t - lastT) / 1000)); lastT = t;
    if (running && !root.hidden) { offset += IDLE * dt; place(); }
    requestAnimationFrame(tick);
  })(lastT);
  let rz = 0;
  addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (!playing) build(); }, 150); });

  // 그 곡의 칸 중 흐르는 방향으로 가장 가까운 것 — 적어도 화면 폭의 35% 는 흐르게. 띠에 없는 곡이면 화면 밖 칸에 입힌다.
  function pickCell(title, data) {
    const minRun = innerWidth * 0.35;
    const need = el => { let d = mod(xOf(el._i) + pitch / 2 - innerWidth / 2, total); if (d < minRun) d += total; return d; };
    let best = null, bd = Infinity;
    for (const el of cells) if (el.dataset.title === title) { const d = need(el); if (d < bd) { bd = d; best = el; } }
    if (!best) {
      for (const el of cells) { const x = xOf(el._i), d = need(el); if (x > innerWidth && d < bd) { bd = d; best = el; } }
      if (best) setCell(best, data, best._i);
    }
    return best ? { el: best, delta: bd } : null;
  }
  function runTo(target, ms, ease) {
    return new Promise(res => {
      const from = offset, t0 = performance.now();
      (function step(t) {
        const k = Math.min(1, (t - t0) / ms);
        offset = from + (target - from) * ease(k); place();
        k < 1 ? requestAnimationFrame(step) : res();
      })(t0);
    });
  }

  function sceneReset() { if (sceneFade) { sceneFade.cancel(); sceneFade = null; } }
  // 지난 연출의 흔적을 치운다 — 스틸·포스터, 레터박스 애니메이션, 뽑혔던 칸의 on/gone. 퇴장은 화면 전체를 옅게만 하고
  // 끝나므로 이걸 안 하면 다음 연출이 화면을 되돌리는 순간 지난 스틸이 가운데에 보인다
  function clear() {
    sceneReset();
    for (const a of [barTop, barBot]) a.getAnimations().forEach(x => x.cancel());
    layer.textContent = "";
    dim.classList.remove("on");
    strip.classList.remove("sel");
    for (const el of cells) el.classList.remove("on", "gone");
  }
  function reset() {
    clear();
    capSong.textContent = "—";
    build();
    running = true;
  }

  async function run({ song, players, badge, kicker, inst }) {
    clear();                     // 지난 스틸·포스터·옅어진 화면을 먼저 치운다 (호스트는 reset 을 부르지 않는다)
    const data = host.songData(song);
    const { n, shown, more } = host.roster(players);
    const instText = String(inst || "").trim();

    // 1) 고르기 — 띠를 굴려 그 칸을 가운데에
    running = false;
    const picked = pickCell(data.title, data);
    strip.classList.add("sel");
    if (picked) {
      picked.el.classList.add("on");
      await runTo(offset + picked.delta, T.spin, k => 1 - Math.pow(1 - k, 3));
    } else await wait(T.spin);

    // 스틸·포스터 자리 (스틸 폭 72vw, 2.35:1, 높이 52vh 까지 · 화면 41% 높이에 가운데)
    const SW = Math.min(innerWidth * 0.72, innerHeight * 0.52 * 2.35), SH = SW / 2.35;
    const S = { left: (innerWidth - SW) / 2, top: innerHeight * 0.41 - SH / 2, w: SW, h: SH };
    const PH = SH * 0.84, PW = PH / 1.42;
    const P = { left: S.left + SW * 0.045, top: S.top + SH * 0.08, w: PW, h: PH };
    let src;
    if (picked) {
      const f = picked.el.querySelector(".fm-frame").getBoundingClientRect();
      src = { left: f.left, top: f.top, w: f.width, h: f.height };
      picked.el.classList.add("gone");
    } else src = { left: innerWidth / 2 - 40, top: innerHeight, w: 80, h: 114 };

    const cover = data.cover;
    const still = document.createElement("div");
    still.className = "fm-still";
    still.style.cssText = `left:${S.left}px;top:${S.top}px;width:${S.w}px;height:${S.h}px;`;
    still.style.setProperty("--cover", cover);
    // 크레딧 조립 (…Html = HTML 을 조립하는 함수 — 칭호·이름은 esc 를 지난다, 보안)
    const rollHtml = () => `<div class="fm-ch">CAST</div>` + shown.map(([t, nm]) =>
      `<div class="fm-p"><span class="t">${esc(t) || "&nbsp;"}</span><span class="n">${esc(nm)}</span></div>`).join("") +
      (more > 0 ? `<div class="fm-p more"><span class="t">&nbsp;</span><span class="n">외 ${Number(more)}</span></div>` : "");
    const meta = [data.duration != null ? `▶ ${fmt(data.duration)}` : "", host.idLine(data.id)].filter(Boolean).join("   ·   ");
    still.innerHTML = `
      <div class="fm-bg"></div><div class="fm-vig"></div>
      <div class="fm-top"><span class="fm-kicker">${esc(kicker || host.autoKicker(n))}</span><span class="fm-badge">${esc(badge || host.autoBadge(n))}</span></div>
      <div class="fm-credits"><div class="fm-roll">${rollHtml()}</div></div>
      <div class="fm-sub">
        <div class="fm-inst"${instText ? "" : " hidden"}>${esc(instText)}</div>
        <div class="fm-song">${esc(data.title)}</div>
        <div class="fm-meta">${esc(meta)}</div>
      </div>
      <div class="fm-grain"></div><div class="fm-flash"></div>`;
    const poster = document.createElement("div");
    poster.className = "fm-poster";
    poster.style.cssText = `width:${P.w}px;height:${P.h}px;`;
    poster.style.setProperty("--cover", cover);
    layer.append(still, poster);
    capSong.textContent = data.title;
    dim.classList.add("on");

    // 2) 들어올림 — 포스터: 띠의 칸 → 스틸 왼쪽 · 스틸: 포스터 자리에서 양옆으로 열린다 · 레터박스 들어옴
    const at = r => `translate(${r.left}px,${r.top}px) scale(${r.w / P.w},${r.h / P.h})`;
    const ease = "cubic-bezier(.22,.9,.25,1)";
    const clipFrom = `inset(${P.top - S.top}px ${S.left + S.w - (P.left + P.w)}px ${S.top + S.h - (P.top + P.h)}px ${P.left - S.left}px)`;
    const lifts = [
      poster.animate([{ transform: at(src) }, { transform: at(P) }], { duration: T.lift, easing: ease, fill: "forwards" }),
      still.animate([{ clipPath: clipFrom, opacity: 0 }, { clipPath: clipFrom, opacity: 1, offset: .45 }, { clipPath: "inset(0px 0px 0px 0px)", opacity: 1 }],
        { duration: T.lift, easing: "cubic-bezier(.6,0,.2,1)", fill: "forwards" }),
      barTop.animate([{ transform: "translateY(-100%)" }, { transform: "translateY(0)" }], { duration: T.lift, easing: ease, fill: "forwards" }),
      barBot.animate([{ transform: "translateY(100%)" }, { transform: "translateY(0)" }], { duration: T.lift, easing: ease, fill: "forwards" }),
    ];
    await Promise.all(lifts.map(a => a.finished));

    // 3) 드러냄 — 영사기 번쩍 + 글
    still.classList.add("revealed");
    still.querySelector(".fm-flash").animate([{ opacity: 0 }, { opacity: .55, offset: .12 }, { opacity: .08, offset: .3 }, { opacity: .3, offset: .42 }, { opacity: 0 }],
      { duration: T.flip, easing: "linear", fill: "forwards" });
    still.querySelector(".fm-bg").animate([{ transform: "scale(1.1)" }, { transform: "scale(1.2)" }],
      { duration: T.flip + T.hold + T.out, easing: "linear", fill: "forwards" });     // 켄 번스
    // 크레딧: 아래에서 올라와 끝날 무렵 마지막 줄이 보이게 (짧으면 가운데 조금 위에서 멈춘다)
    const box = still.querySelector(".fm-credits"), roll = still.querySelector(".fm-roll");
    const bh = box.clientHeight, ch = roll.scrollHeight;
    const y0 = bh * 0.62, y1 = ch < bh * 0.8 ? (bh - ch) / 2 - bh * 0.08 : bh * 0.86 - ch;
    roll.animate([{ transform: `translateY(${y0}px)`, opacity: 0 }, { opacity: 1, offset: .12 }, { transform: `translateY(${y1}px)`, opacity: 1 }],
      { duration: T.flip + T.hold, easing: "cubic-bezier(.3,.1,.4,1)", fill: "forwards" });
    await wait(T.flip);

    // 4) 유지
    await wait(T.hold);

    // 5) 퇴장 — 띠는 왼쪽으로 달려 나가고, 레터박스가 닫히고, 화면 전체가 옅어진다
    const outs = [
      runTo(offset + innerWidth * 1.6, T.out, k => k * k * k),
      barTop.animate([{ transform: "translateY(0) scaleY(1)" }, { transform: `translateY(0) scaleY(${(0.5 / 0.11).toFixed(3)})` }], { duration: T.out, easing: "cubic-bezier(.7,0,.3,1)", fill: "forwards" }).finished,
      barBot.animate([{ transform: "translateY(0) scaleY(1)" }, { transform: `translateY(0) scaleY(${(0.5 / 0.11).toFixed(3)})` }], { duration: T.out, easing: "cubic-bezier(.7,0,.3,1)", fill: "forwards" }).finished,
      still.animate([{ transform: "scale(1)" }, { transform: "scale(.97,.9)" }], { duration: T.out, easing: "ease-in", fill: "forwards" }).finished,
      poster.animate([{ transform: at(P) }, { transform: `translate(${P.left - P.w * 0.2}px,${P.top + P.h * 0.05}px) scale(.9)` }], { duration: T.out, easing: "ease-in", fill: "forwards" }).finished,
    ];
    sceneFade = root.animate([{ opacity: 1 }, { opacity: 0 }], { duration: T.out, easing: "linear", fill: "forwards" });
    await Promise.all([...outs, sceneFade.finished]);
    dim.classList.remove("on");
  }

  function play(d) {
    if (playing) return playing;
    playing = run(d).finally(() => { playing = null; });
    return playing;
  }
  build();
  // 곡 목록을 새로 받았을 때 (호스트가 부른다) — 띠에 올릴 곡이 바뀐 때만 다시 짓는다 · 연출 중에는 안 건드린다
  function refresh() {
    if (playing) return;
    if (listSig(host.songs.length ? host.featured(FEATURED) : []) !== sig) build();
  }
  return { play, reset, refresh };
} });
