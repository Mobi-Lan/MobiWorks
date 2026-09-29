/* 쇼츠형 연출 — 6.0초 (카드형과 같은 예산, host.T)
   화면에 폰 한 대 — 쇼츠를 넘기듯 넘기면 다음 곡이 나온다.
     1.5  고르기 : 화면 가운데에 선 폰 한 대 — 세로 피드(곡 하나 = 쇼츠 한 편)가 휙휙 위로 넘어가다 느려지며 그 곡에서 멈춘다
     0.7  들어올림: 폰이 조금 커지고(1 → 1.04) 그 곡의 자막 줄이 펼쳐진다 — 제목이 커지고 킥커·배지·악기 줄, 아래 재생 막대가 흐른다
     0.6  드러냄 : 「NOW PLAYING」 알약이 뜨고 게스트(사람 줄)가 왼쪽에 댓글 말풍선처럼 하나씩 쌓인다
     2.4  유지   : 재생 막대는 계속 흐르고 ♥ 가 한 번 뛴다
     0.8  퇴장   : 다른 팩처럼 폰이 살짝 움츠렸다 위로 휙 날아가고 화면 전체가 옅어진다
   데이터 계약: { song, players, badge, kicker, inst } — 커버는 곡 항목의 `cover`(호스트가 검사한 `/folio/covers/<번호>?v=…`).
   피드에 올리는 곡: 대기열 → 뽑힐 곡 → 나머지 (host.featured) — 악보함 전부를 올리면 그림 수백 장을 한꺼번에 받는다.
   피드 칸의 자리는 % 로만 둔다 (칸 높이 = 폰 화면 높이) — 숨겨진 동안에도, 창 크기가 바뀌어도 잴 것이 없다. */
OpeningHost.register("phone", { label: "쇼츠형", mount(root, host) {
  const { T, esc, wait, fmt, mod } = host;
  root.innerHTML = `
    <div class="ph-dim"></div>
    <div class="ph-stage">
      <div class="ph-phone">
        <div class="ph-screen">
          <div class="ph-feed"></div>
          <div class="ph-top">
            <div class="ph-status"><span class="ph-clock">9:41</span><span class="ph-sig"><i></i><i></i><i></i><i></i><b></b></span></div>
            <div class="ph-tabs"><span>팔로잉</span><span class="on">추천</span></div>
          </div>
          <div class="ph-pill"><i></i>NOW PLAYING</div>
          <div class="ph-prog"><i></i></div>
          <div class="ph-island"></div>
        </div>
      </div>
    </div>
    <div class="ph-caption"><small>Mabinogi Mobile with MobiFolio</small><b class="ph-capsong">—</b></div>`;
  const $ = s => root.querySelector(s);
  const phone = $(".ph-phone"), screen = $(".ph-screen"), feed = $(".ph-feed"), dim = $(".ph-dim");
  const progBar = $(".ph-prog i"), clock = $(".ph-clock"), capSong = $(".ph-capsong");

  const FEATURED = 12;        // 피드에 올리는 곡 수의 위
  const MIN_ITEMS = 8;        // 피드 칸 수의 아래 — 곡이 적으면 되풀이한다 (지금 보이는 칸과 도착할 칸이 겹치지 않게)
  // 넘김은 **두 번** — 지금 칸과 다음 칸에 직전 곡 둘(대기열에서 바로 앞부터, 모자라면 아무 곡), 그다음 칸에 그 곡.
  // 잠깐 보다가 위로 올린다. 한 번 넘기는 속도는 그대로 두고 두 번째 넘김만큼 유지 시간을 줄인다
  const STEPS = 2;
  const SWIPE_ONE = 0.6;      // 한 번 넘기는 데 T.spin 의 이만큼 (0.9초)
  const LOOK_MS = 500;        // 넘기기 전에 그 장을 보는 시간 — 처음 장도, 넘긴 뒤 다음 장도 (사람도 잠깐 보고 넘기니까)
  const EXTRA_MS = (STEPS - 1) * (T.spin * SWIPE_ONE + LOOK_MS);   // 두 번째부터의 넘김·보기 — 유지에서 뺀다 (전체 6초)
  let items = [], count = 1, pos = 0, playing = null, sceneFade = null, sig = "", swapped = [];

  // 오른쪽 아이콘 — 고정 <svg> (바깥 자원 없음)
  function iconHtml(k) {
    const d = {
      heart: "M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.8 4.5c2.2 0 3.6 1.2 5.2 3 1.6-1.8 3-3 5.2-3 3.8 0 5.9 3.9 4.4 7.3C19.5 16.4 12 21 12 21z",
      talk: "M12 3C6.5 3 2 6.9 2 11.6c0 2.5 1.3 4.8 3.4 6.4L4.6 21.5l4.2-2.2c1 .3 2.1.4 3.2.4 5.5 0 10-3.9 10-8.6S17.5 3 12 3z",
      share: "M14 4l8 7.5-8 7.5v-4.6c-5.3 0-8.8 1.6-12 5.6 1-5.7 4.3-10.7 12-11.6V4z",
    }[k] || "";
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  }
  // 좋아요·댓글 수 — 곡 번호·제목으로 늘 같게 (장식, 티켓형의 바코드와 같은 결)
  function counts(s) {
    const r = host.rng(`${s.id}|${s.title}|phone`);
    const k = v => v >= 10000 ? `${(v / 10000).toFixed(1)}만` : v >= 1000 ? `${(v / 1000).toFixed(1)}천` : String(v);
    return [k(Math.floor(900 + r() * 48000)), k(Math.floor(20 + r() * 900))];
  }
  function setItem(el, s) {
    el._song = s;
    el.dataset.title = s.title || "";
    el.style.setProperty("--cover", s.cover || host.UNKNOWN_COVER);
    const [likes, talks] = counts(s);
    el.innerHTML = `
      <div class="ph-blur"></div><div class="ph-art"></div><div class="ph-shade"></div>
      <div class="ph-side">
        <span class="ph-ic ph-like">${iconHtml("heart")}<small>${esc(likes)}</small></span>
        <span class="ph-ic">${iconHtml("talk")}<small>${esc(talks)}</small></span>
        <span class="ph-ic">${iconHtml("share")}<small>공유</small></span>
        <span class="ph-disc"></span>
      </div>
      <div class="ph-cap">
        <div class="ph-chat"></div>
        <div class="ph-user"><b class="ph-av"></b>@mobifolio<span class="ph-follow">팔로우</span></div>
        <div class="ph-more"></div>
        <div class="ph-title">${esc(s.title || "")}</div>
      </div>`;
  }
  const listSig = list => JSON.stringify(list.map(s => [s.id, s.title, s.cover]));
  function build() {
    const list = host.songs.length ? host.featured(FEATURED) : [{ id: null, title: "", cover: host.UNKNOWN_COVER }];
    sig = listSig(list);
    count = Math.ceil(Math.max(MIN_ITEMS, list.length) / list.length) * list.length;
    feed.textContent = "";
    items = [];
    swapped = [];
    for (let i = 0; i < count; i++) {
      const el = document.createElement("div");
      el.className = "ph-item";
      el._i = i;
      setItem(el, list[i % list.length]);
      feed.appendChild(el);
      items.push(el);
    }
    pos = 0;
    place();
  }
  // 칸 i 의 자리 (칸 높이 단위) — 지금 보이는 칸이 0, 그 위 칸이 −1, 아래로 1, 2, …
  function place() {
    for (const el of items) {
      const y = mod(el._i - pos + 1, count) - 1;
      el.style.transform = `translate3d(0,${(y * 100).toFixed(3)}%,0)`;
      el.style.visibility = y > -1 && y < 1 ? "" : "hidden";
    }
  }
  // 지금 칸부터 STEPS−1 칸 = 직전 곡들(먼 것이 먼저 보이고 가까운 것이 그 곡 바로 앞) — 모자라면 그 곡이 아닌 아무 곡을
  // 무작위로(겹치지 않게), STEPS 칸 = 그 곡. 입힌 칸은 연출 뒤 되돌린다 (clear).
  function pickItem(data) {
    const c = Math.round(pos);
    const prevs = host.prevSongs(STEPS - 1);                       // 가까운 것이 먼저
    const pool = host.songs.filter(s => s.title !== data.title && !prevs.includes(s));
    while (prevs.length < STEPS - 1 && pool.length) prevs.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
    const fills = [];
    for (let k = 0; k < STEPS - 1; k++) fills.push([items[mod(c + k, count)], prevs[STEPS - 2 - k] || null]);   // 먼 것부터
    const nxt = items[mod(c + STEPS, count)];
    fills.push([nxt, data]);
    for (const [el, s] of fills) {
      if (!s || el.dataset.title === s.title) continue;
      swapped.push({ el, song: el._song });
      setItem(el, s);
    }
    return { el: nxt, steps: STEPS };
  }
  // 넘기기 — 전체는 ease-out cubic 으로 느려지고, 칸 하나하나는 휙 넘어가 멈추는 손가락질 (빠를 때는 살짝 번진다)
  function swipeTo(target, ms) {
    return new Promise(res => {
      const from = pos, dist = target - from, t0 = performance.now();
      let last = from, lastT = t0;
      (function step(t) {
        const k = Math.min(1, (t - t0) / ms);
        const x = dist * (1 - Math.pow(1 - k, 3));
        const whole = Math.floor(x), f = x - whole;
        pos = k >= 1 ? target : from + whole + (1 - Math.pow(1 - f, 2));   // 칸 하나는 2차 곡선 — 손가락질이 덜 급하다
        place();
        const v = Math.abs(pos - last) / Math.max(1, t - lastT) * 1000;
        last = pos; lastT = t;
        feed.style.filter = k < 1 && v > 3 ? `blur(${Math.min(2.5, (v - 3) * 0.2).toFixed(2)}px)` : "";
        k < 1 ? requestAnimationFrame(step) : res();
      })(t0);
    });
  }

  function sceneReset() { if (sceneFade) { sceneFade.cancel(); sceneFade = null; } }
  // 지난 연출의 흔적을 치운다 — 날아간 폰·재생 막대·펼친 자막·말풍선·옅어진 화면. 호스트는 reset 을 부르지 않으므로
  // run 이 처음에 이것을 부른다 (필름형에서 지난 스틸이 남아 보였던 것과 같은 일을 막는다)
  function clear() {
    sceneReset();
    for (const a of [...phone.getAnimations(), ...progBar.getAnimations()]) a.cancel();
    feed.style.filter = "";
    dim.classList.remove("on");
    screen.classList.remove("live", "revealed");
    for (const el of items) {
      el.classList.remove("on", "revealed");
      const chat = el.querySelector(".ph-chat"), more = el.querySelector(".ph-more"), like = el.querySelector(".ph-like");
      if (chat) chat.textContent = "";
      if (more) more.textContent = "";
      if (like) like.classList.remove("liked");
    }
    for (const w of swapped) setItem(w.el, w.song);   // 직전 곡·그 곡을 입혔던 칸을 되돌린다
    swapped = [];
  }
  function reset() {
    clear();
    capSong.textContent = "—";
  }

  async function run({ song, players, badge, kicker, inst }) {
    clear();                     // 지난 연출의 흔적을 먼저 치운다 (호스트는 reset 을 부르지 않는다)
    const data = host.songData(song);
    const { n, shown, more } = host.roster(players);
    const instText = String(inst || "").trim();
    const now = new Date();
    clock.textContent = `${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}`;
    capSong.textContent = data.title;
    dim.classList.add("on");

    // 1) 고르기 — 폰이 살짝 떠오르며 서고, 피드가 넘어가다 그 곡에서 멈춘다
    const picked = pickItem(data);
    phone.animate([{ opacity: 0, transform: "translateY(6vh) scale(.94)" }, { opacity: 1, transform: "translateY(0px) scale(1)" }],
      { duration: 360, easing: "cubic-bezier(.22,.9,.25,1)" });
    for (let k = 0; k < picked.steps; k++) {         // 장마다: 잠깐 보고(LOOK_MS) → 한 장 위로 (SWIPE_ONE)
      await wait(LOOK_MS);
      await swipeTo(pos + 1, T.spin * SWIPE_ONE);
    }
    const el = picked.el;

    // 2) 들어올림 — 폰이 조금 커지고, 그 곡의 자막 줄이 펼쳐진다 · 재생 막대가 흐르기 시작한다
    const meta = [data.duration != null ? `▶ ${fmt(data.duration)}` : "", host.idLine(data.id)].filter(Boolean).join("  ·  ");
    el.querySelector(".ph-more").innerHTML = `<div class="ph-more-in">
        <div class="ph-tags"><span class="ph-kick"><b>●</b> ${esc(kicker || host.autoKicker(n))}</span><span class="ph-badge">${esc(badge || host.autoBadge(n))}</span></div>
        <div class="ph-meta"${meta ? "" : " hidden"}>${esc(meta)}</div>
        <div class="ph-inst"${instText ? "" : " hidden"}>${esc(instText)}</div>
      </div>`;
    el.classList.add("on");
    screen.classList.add("live");
    progBar.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }],
      { duration: (T.lift + T.flip + T.hold + T.out + 1500) * 2, easing: "linear", fill: "forwards" });   // 두 배 느리게 — 연출이 끝날 때 절반쯤
    await phone.animate([{ transform: "scale(1)" }, { transform: "scale(1.04)" }],
      { duration: T.lift, easing: "cubic-bezier(.22,.9,.25,1)", fill: "forwards" }).finished;

    // 3) 드러냄 — NOW PLAYING 알약 · 게스트가 댓글 말풍선처럼 하나씩 (…Html = HTML 을 조립하는 함수 — 칭호·이름은 esc, 보안)
    const initial = nm => [...String(nm || "").trim()][0] || "?";
    const hue = nm => Math.floor(host.rng(String(nm))() * 360);
    const chatHtml = () => shown.map(([t, nm], i) => `<div class="ph-c" style="--n:${Number(i)};--h:${Number(hue(nm))}"><b class="ph-cav">${esc(initial(nm))}</b><span>` +
      (t ? `<small>${esc(t)}</small>` : "") + `${esc(nm)}</span></div>`).join("") +
      (more > 0 ? `<div class="ph-c more" style="--n:${Number(shown.length)}"><b class="ph-cav">+</b><span>외 ${Number(more)}</span></div>` : "");
    el.querySelector(".ph-chat").innerHTML = chatHtml();
    el.classList.add("revealed");
    screen.classList.add("revealed");
    await wait(T.flip);

    // 4) 유지 — 재생 막대는 계속 흐르고, ♥ 가 한 번 뛴다 (CSS .liked)
    el.querySelector(".ph-like").classList.add("liked");
    await wait(Math.max(400, T.hold - EXTRA_MS));      // 넘김·보기가 늘어난 만큼 유지에서 뺀다 — 전체 6초는 그대로

    // 5) 퇴장 — 다른 팩과 같은 「위로 휙」 (살짝 움츠렸다가 곧게 위로, 조금 작아지며) + 화면 전체 옅어짐
    const outMove = phone.animate([
      { transform: "translate(0px,0px) scale(1.04) rotate(0deg)", offset: 0 },
      { transform: "translate(0px,40px) scale(1.02) rotate(0deg)", offset: .18 },
      { transform: `translate(0px,${-innerHeight * 1.3}px) scale(.73) rotate(-4deg)`, offset: 1 },
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
  build();
  // 곡 목록을 새로 받았을 때 (호스트가 부른다) — 피드에 올릴 곡이 바뀐 때만 다시 짓는다 · 연출 중에는 안 건드린다
  function refresh() {
    if (playing) return;
    if (listSig(host.songs.length ? host.featured(FEATURED) : []) !== sig) build();
  }
  return { play, reset, refresh };
} });
