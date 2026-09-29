/* 카드형 연출 — 기본 테마.
   원래 ui/folio/opening2.html 의 스크립트 2·3절(링·연출)이던 것을 **그대로** 옮겼다. 1절(곡 목록·커버 주소·대기열)과
   4절(밖에서 부르는 길)은 호스트(opening2.html)에 남았다. 옮기며 바꾼 곳 — 움직임·시간·키프레임은 그대로다
   (tests/test_opening_themes.py 가 본다):
     · CSS 변수는 :root 가 아니라 이 팩의 뿌리(.op-card)에 산다 → `document.documentElement` 대신 `root`
     · 퇴장의 「화면 전체 옅어짐」은 body 가 아니라 이 팩의 뿌리에 건다 — 뿌리가 화면 전체(링·캡션·바탕·카드)를 담는다.
       .picked 의 조상이라 preserve-3d 를 평평하게 하지 않는 것은 body 와 같다
     · 곡 목록(SONGS·SONG_TOTAL·QUEUE_KEYS·PICK_KEY)은 호스트가 쥔다 (host.songs · host.total · host.queueKeys · host.pickKey)
     · 악기 줄은 호스트가 채워서 넘긴다 (instFor — 서버가 방금 낸 연출의 악기)
   선택자는 theme.css 에서 전부 .op-card 아래다. */
OpeningHost.register("card", { label: "카드형", mount(root, host) {
root.innerHTML = `
<div class="stage">
  <div class="ring" id="ring"></div>
</div>
<div class="dim" id="dim"></div>
<div class="picked-layer" id="pickedLayer"></div>

<div class="caption">
  <small>Mabinogi Mobile with MobiFolio</small>
  <b id="captionSong">—</b>
</div>
`;
const UNKNOWN_COVER = host.UNKNOWN_COVER;   // 번호가 없는 곡 (보관함에 없는 제목)
function songTag(id) { return Number(id) > 0 ? "#" + String(Number(id)).padStart(4, "0") : ""; }
const findSong = song => host.findSong(song);
let SONGS_SIG = "";            // 지난 링의 모습 — 같으면 링을 다시 짓지 않는다

/* ────────────────────────────────────────────────
   2. 링 구성
      현재 재생목록의 곡은 반드시 나오고, 나머지는 무작위로 뽑는다.
      ※ 남은 자리의 씨앗을 **연출마다 새로** 정한다(host.seed) — 전에는 창을 열 때 한 번이라 늘 같은 곡이 나왔다.
      · 지금 대기열(현재 재생목록)의 곡은 **전부** 링에 오른다 — 재생 차례대로, 링의 앞(0°)부터.
      · 남은 자리는 악보함의 나머지 곡에서 **무작위**로. 씨앗은 연출마다 새로(host.seed · OpeningHost.newRound) 정하고, 곡마다
        그 씨앗으로 섞은 순번을 매긴다 — 목록을 다시 받아도(연출마다) 같은 곡이 같은 순번이라 링이 깜빡이지 않는다.
      · 대기열이 한 바퀴 자리(360 / --spread = 13장)보다 길면 카드 사이 각도를 줄여 더 싣는다 — 24° 까지
        (15장). 그보다 길면 대기열 앞에서부터 15곡만 (뽑힐 곡은 늘 포함). 카드 폭은 아래 buildRing 이 간격에 맞춰 줄인다.
        (처음엔 12°·30장이었다 — 너무 조밀해서 줄였다)
      · 대기열이 없으면(보관함에서 바로 튼 곡) 뽑힐 곡 한 장 + 무작위.
      계산은 아래 ring-pick 구간의 순수 함수다 — tests/js/ring_pick.js 가 이 구간을 떼어 node 로 돌린다.
   ──────────────────────────────────────────────── */
/* ring-pick:begin */
const RING_SPREAD_FLOOR = 24;          // 대기열이 길 때 줄일 수 있는 카드 사이 각도의 바닥 (도) — 15장까지
// 반드시 올릴 곡 수 → {slots: 링의 자리 수, spread: 카드 사이 각도}
function ringLayout(mustCount, spread, floor) {
  spread = spread > 0 ? spread : 28;
  floor = floor > 0 ? Math.min(floor, spread) : spread;
  const base = Math.ceil(360 / spread);
  if (mustCount <= base) return { slots: base, spread };
  const slots = Math.min(mustCount, Math.floor(360 / floor));
  return { slots, spread: 360 / slots };
}
// 씨앗 + 글자 → 32비트 순번 (FNV-1a 뒤에 섞기). 같은 씨앗·같은 곡이면 늘 같은 값
function ringHash(seed, str) {
  let h = (2166136261 ^ (seed >>> 0)) >>> 0;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  h ^= h >>> 15; h = Math.imul(h, 2246822507) >>> 0; h ^= h >>> 13; h = Math.imul(h, 3266489909) >>> 0; h ^= h >>> 16;
  return h >>> 0;
}
// lib: 곡 목록 [{key, keys?, …}] · queueKeys: 대기열 key (재생 차례) · pickedKey: 이번에 뽑힐 곡
// → {list: 링에 올릴 곡(앞은 대기열), slots, spread, must: 대기열(+뽑힐 곡)에서 올린 수, cut: 자리가 없어 못 올린 대기열 곡 수}
function ringCompose(lib, queueKeys, pickedKey, spread, floor, seed) {
  lib = Array.isArray(lib) ? lib : [];
  const byKey = new Map();
  for (const s of lib) for (const k of (s && s.keys) || [s && s.key]) if (k != null && k !== "" && !byKey.has(String(k))) byKey.set(String(k), s);
  const must = [], inMust = new Set();
  const add = s => { if (s && !inMust.has(s)) { inMust.add(s); must.push(s); } };
  for (const k of queueKeys || []) add(byKey.get(String(k)));
  const picked = pickedKey != null ? byKey.get(String(pickedKey)) || null : null;
  add(picked);                                  // 대기열에 없으면 끝에 붙는다 (대기열이 없으면 이 한 장뿐)
  const lay = ringLayout(must.length, spread, floor);
  const head = must.slice(0, lay.slots);
  if (picked && !head.includes(picked)) head[head.length - 1] = picked;   // 자리가 모자라도 뽑힐 곡은 링에
  const rest = lib.map((s, i) => [s, i]).filter(([s]) => s && !inMust.has(s))
    .map(([s, i]) => [ringHash(seed, String(s.key != null ? s.key : s.title)), i, s])
    .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1])).map(x => x[2]);
  const list = head.concat(rest.slice(0, Math.max(0, lay.slots - head.length)));
  return { list, slots: lay.slots, spread: lay.spread, must: head.length, cut: Math.max(0, must.length - lay.slots) };
}
/* ring-pick:end */
// 무작위 채움의 씨앗은 **호스트의 판 씨앗**(host.seed) — 연출마다 새로 섞인다. 예전 씨앗은 창을 열 때 한 번뿐이라
// 앱을 켜 둔 동안 링의 나머지가 늘 같았다. 재생목록 곡은 그대로 1순위다.
const NO_SONG = { id: null, key: "", title: "", raw: "", duration: null, cover: UNKNOWN_COVER };
function composeRing() {
  const SONGS = host.songs, QUEUE_KEYS = host.queueKeys, PICK_KEY = host.pickKey;   // 곡 목록은 호스트가 쥔다
  const spread = parseFloat(rootCS().getPropertyValue("--spread")) || 28;
  return ringCompose(SONGS.length ? SONGS : [NO_SONG], QUEUE_KEYS, PICK_KEY, spread, RING_SPREAD_FLOOR, host.seed);
}
const ring = document.getElementById("ring");
let cardEls = [];     // 링 위의 카드 인스턴스 (곡은 반복될 수 있음)
const rootCS = () => getComputedStyle(root);   // CSS 변수는 이 팩의 뿌리(.op-card)에 산다
const SQ_HTML = `<div class="sqbg"></div><div class="sqimg"></div>`;   // 정사각형 그림: 흐린 확대 배경 + 가운데 원본
function sqHtml(on) { return on ? SQ_HTML : ""; }
// 생성 커버는 제목·아티스트가 그림에 이미 있다 — 링 카드의 제목 글씨(.label)를 겹쳐 쓰지 않는다
function cardHTML(s) { return (s.sq ? SQ_HTML : "") + `<div class="grain"></div>` + (s.gen ? "" : `<div class="label">${esc(s.title)}</div>`); }
function setCard(el, s) {
  el.dataset.title = s.title;
  el.style.setProperty("--cover", s.cover);
  el.classList.toggle("sq", !!s.sq);
  el.innerHTML = cardHTML(s);
}
function buildRing(plan) {
  plan = plan || composeRing();
  // 원 한 바퀴를 채울 만큼 카드 생성. 곡 수가 모자라면 목록을 반복.
  // 이음새에서 같은 곡이 붙지 않도록 곡 수의 배수로 맞춘다.
  // 악보함은 수백 곡일 수 있다 — 링에는 한 바퀴 자리 수만큼만 올린다 (카드가 실처럼 가늘어지지 않게).
  // 자리 수는 ringCompose 가 정한다 (대기열이 길면 13장보다 많다 — 최대 15장).
  const slots = plan.slots;
  const list = plan.list.length ? plan.list : [NO_SONG];
  const count = Math.ceil(slots / list.length) * list.length;
  const step = 360 / count;
  // 카드가 서로 겹치지 않게 간격에 맞춰 카드 폭을 줄인다.
  root.style.removeProperty("--card-w");
  const r = parseFloat(rootCS().getPropertyValue("--ring-r"));
  const arc = r * step * Math.PI / 180;
  const curW = parseFloat(rootCS().getPropertyValue("--card-w"));
  if (curW > arc / 1.18) root.style.setProperty("--card-w", `${(arc / 1.18).toFixed(1)}px`);
  ring.textContent = "";
  cardEls = [];
  for (let i = 0; i < count; i++) {
    const el = document.createElement("div");
    el.className = "card";
    el.style.setProperty("--a", `${i * step}deg`);
    setCard(el, list[i % list.length]);
    ring.appendChild(el);
    cardEls.push(el);
  }
}
// 곡 목록을 새로 받았을 때 (호스트가 부른다) — 링의 모습이 바뀐 때만 다시 짓는다 · 연출 중에는 링을 안 건드린다 (다음에 다시 본다)
function refresh() {
  const plan = composeRing();
  const sig = JSON.stringify([plan.slots, plan.list.map(s => [s.id, s.title, s.cover, !!s.sq])]);
  if (sig === SONGS_SIG || playing) return;
  SONGS_SIG = sig;
  buildRing(plan);
}
// ── 링 회전은 JS 로 제어 (평소엔 천천히 돌고, 재생 시 목표 카드를 12시로 맞춤)
const IDLE_SPEED = 360 / (parseFloat(rootCS().getPropertyValue("--idle-speed")) || 60); // deg/s
let ringAngle = 0, spinning = true, lastT = performance.now();
function setRing(a) { ringAngle = a; ring.style.transform = `rotate(${a}deg)`; }
(function tick(t) {
  const dt = Math.min(0.1, Math.max(0, (t - lastT) / 1000)); lastT = t;   // 숨겨졌다 돌아와도 튀지 않게
  if (spinning) setRing(ringAngle + IDLE_SPEED * dt);
  requestAnimationFrame(tick);
})(lastT);
const mod = (a, n) => ((a % n) + n) % n;
const deltaOf = el => { let d = mod(-(ringAngle + parseFloat(el.style.getPropertyValue("--a"))), 360); if (d < 60) d += 360; return d; };

// 해당 곡 카드 중, 지금 도는 방향으로 가장 가까운 것을 고른다 (최소 60° 는 돌게 해서 회전이 보이도록)
// 링에 없는 곡이면(악보함이 링보다 크다) 화면 밖(90° 이상 떨어진) 카드 하나에 그 곡을 입힌다.
function pickCard(title, data) {
  let best = null, bestDelta = Infinity;
  for (const el of cardEls) {
    if (el.dataset.title !== title) continue;
    // 카드가 12시에 오려면 ringAngle + a ≡ 0 이어야 함 → 필요한 추가 회전량
    const delta = deltaOf(el);
    if (delta < bestDelta) { bestDelta = delta; best = el; }
  }
  if (!best && title) {
    for (const el of cardEls) {
      const delta = deltaOf(el);
      if (delta >= 90 && delta < bestDelta) { bestDelta = delta; best = el; }
    }
    if (best) setCard(best, data);
  }
  return best ? { el: best, delta: bestDelta } : null;
}
// 링을 목표 각도까지 부드럽게 회전
function spinTo(target, ms) {
  return new Promise(res => {
    const from = ringAngle, t0 = performance.now();
    const ease = x => 1 - Math.pow(1 - x, 3);
    (function step(t) {
      const k = Math.min(1, (t - t0) / ms);
      setRing(from + (target - from) * ease(k));
      k < 1 ? requestAnimationFrame(step) : res();
    })(t0);
  });
}

/* ────────────────────────────────────────────────
   3. 연출
   ──────────────────────────────────────────────── */
const layer = document.getElementById("pickedLayer");
const dim = document.getElementById("dim");
const captionSong = document.getElementById("captionSong");
let playing = null;

const DUR = 6000;
const T = { spin: 1500, lift: 700, flip: 600, hold: 2400, out: 800 }; // 합 6000

function autoBadge(n)  { return n >= 2 ? `${n}인 합주` : "솔로"; }
function autoKicker(n) { return n >= 3 ? "ENSEMBLE" : n === 2 ? "DUET" : "SOLO"; }
function fmt(sec) { return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`; }
// 앞면의 사람 줄 (players 블록) — 칭호·이름은 esc 를 지난다
function playersHtml(shown, more) {
  return shown.map(([t, nm], i) => `
          <div class="player" style="--n:${Number(i)}">
            <span class="title">${esc(t) || "&nbsp;"}</span>
            <span class="name">${esc(nm)}</span>
          </div>`).join("") +
    (more > 0 ? `<div class="player more" style="--n:${Number(shown.length)}"><span class="title">&nbsp;</span><span class="name">외 ${Number(more)}</span></div>` : "");
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }   // ' 도 (보안)

// 호스트의 playOpening 이 부른다 — 겹침(순서대로)·연출 직전 목록 새로 받기·악기 줄 채우기는 호스트가 한다
function play({ song = "", players = [], badge = "", kicker = "", inst = "" } = {}) {
  if (!Array.isArray(players)) players = [];     // 밖에서 온 값 — 모양을 믿지 않는다 (보안)
  playing = run({ song, players, badge, kicker, inst }).finally(() => { playing = null; });
  return playing;
}

async function run({ song, players, badge, kicker, inst }) {
  sceneReset();                // 지난 퇴장이 화면을 옅게 둔 채였다 — 이번 연출은 온전한 화면에서 시작
  const instText = String(inst || "").trim();    // 호스트가 채운 악기 줄 (instFor) — 모르면 "" 이라 줄을 감춘다
  const data = findSong(song) ||
               { id: null, title: song, duration: null, cover: UNKNOWN_COVER };
  const picked = pickCard(data.title, data);
  const src = picked?.el ?? null;
  const n = players.length;
  const shown = players.slice(0, 6);
  const more = n - shown.length;

  // 1) 링을 돌려 목표 카드를 12시 방향에 세운다
  spinning = false;
  if (picked) await spinTo(ringAngle + picked.delta, T.spin);
  else await wait(T.spin);

  // 출발 위치: 12시에 선 카드 자리, 없으면 화면 아래 중앙
  let r;
  if (src) {
    const b = src.getBoundingClientRect();           // 회전된 카드의 바운딩 박스
    const w = src.offsetWidth, h = src.offsetHeight; // 실제 카드 크기
    r = { left: b.left + b.width / 2 - w / 2, top: b.top + b.height / 2 - h / 2, width: w, height: h };
    src.classList.add("gone");
  } else { r = { left: innerWidth / 2 - 80, top: innerHeight, width: 160, height: 227 }; }
  const srcAngle = 0; // 12시에 정렬됐으므로 회전 없음

  // 목표: 화면 중앙, 높이의 68%
  const H = Math.min(innerHeight * 0.68, innerWidth * 0.5 * 1.42);
  const W = H / 1.42;
  const scale = W / r.width;
  const cx = innerWidth / 2, cy = innerHeight / 2;
  const dx = cx - (r.left + r.width / 2), dy = cy - (r.top + r.height / 2);

  // 앞면 아래 줄 오른쪽 — 곡 번호 / 번호를 받은 곡 수 (`#0056 / 184`). 번호 없는 곡은 비운다
  const SONGS = host.songs, SONG_TOTAL = host.total;   // 곡 목록은 호스트가 쥔다 (예전처럼 이 순간의 값)
  const idLine = data.id ? `${songTag(data.id)} / ${Number(SONG_TOTAL) || SONGS.length}` : "";
  const el = document.createElement("div");
  el.className = "picked";
  el.style.cssText = `left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;`;
  el.style.setProperty("--cover", data.cover);
  el.innerHTML = `
    <div class="face back${data.sq ? " sq" : ""}">${sqHtml(data.sq)}<div class="grain"></div></div>
    <div class="face front">
      <div class="hdr">
        <div class="kicker">${esc(kicker || autoKicker(n))}</div>
        <div class="badge">${esc(badge || autoBadge(n))}</div>
      </div>
      <div class="players">${playersHtml(shown, more)}</div>
      <div class="spacer"></div>
      <div class="inst"${instText ? "" : " hidden"}>${esc(instText)}</div>
      <div class="song">${esc(data.title)}</div>
      <div class="meta">
        <span>${data.duration != null ? fmt(data.duration) : ""}</span>
        <span>${esc(idLine)}</span>
      </div>
    </div>`;
  layer.appendChild(el);
  captionSong.textContent = data.title;
  dim.classList.add("on");

  const ease = "cubic-bezier(.22,.9,.25,1)";
  // 2) 12시 카드가 위로 튀어나오며 커진다
  await el.animate([
    { transform: `rotate(${srcAngle}deg) translate(0,0) scale(1) rotateY(0deg)` },
    { transform: `rotate(0deg) translate(${dx}px,${dy}px) scale(${scale}) rotateY(0deg)` },
  ], { duration: T.lift, easing: ease, fill: "forwards" }).finished;

  // 3) 가로로 뒤집기
  await el.animate([
    { transform: `translate(${dx}px,${dy}px) scale(${scale}) rotateY(0deg)` },
    { transform: `translate(${dx}px,${dy}px) scale(${scale * 1.04}) rotateY(90deg)`, offset: .5 },
    { transform: `translate(${dx}px,${dy}px) scale(${scale}) rotateY(180deg)` },
  ], { duration: T.flip, easing: "cubic-bezier(.45,0,.2,1)", fill: "forwards" }).finished;
  el.classList.add("revealed");

  // 4) 유지
  await wait(T.hold);

  // 5) 퇴장 — 앞면을 보인 **그대로**(다시 뒤집지 않는다, rotateY 180 유지) 링의 제자리로 돌아가며
  //    T.out 내내 옅어진다. 원래는 다시 뒤집히며 돌아가 마지막 15%에만 옅어졌다.
  //    움직임은 가속·감속 곡선, 옅어짐은 처음부터 끝까지 고르게 — 그래서 애니메이션을 둘로 나눈다.
  //    옅어짐은 카드(.picked)가 아니라 **두 면(.face)에** 건다: preserve-3d 인 카드에 opacity<1 을 주면
  //    3D 가 평평해져(그룹화) 앞면 대신 **뒷면이 거울상으로** 보인다 (헤드리스 Edge 에서 실측).
  //    **화면 전체가 같이 옅어진다** — 카드만이 아니라 링·캡션·어둡게 한 바탕까지.
  //    옅어짐은 이 팩의 뿌리(.op-card — 화면 전체를 덮는 판,
  //    예전의 body 자리)에 건다: .picked 자체에 걸면 preserve-3d 가 평평해져 뒷면이 거울상으로 보이지만(헤드리스 실측),
  //    조상인 뿌리는 그 안쪽 3D 를 건드리지 않는다.
  //    다 옅어진 채로 두고(창은 곧 숨는다) 다음 연출이 시작할 때 되돌린다 — 되돌리는 순간 링이 보이면 안 되므로.
  //    **위로 휙**: 제자리로 돌아가는 대신 살짝 움츠렸다가
  //    기울임 없이 곧게 위로 날아간다 — 조금 작아지며. 화면 전체 페이드아웃과 같이 간다.
  const outMove = el.animate([
    { transform: `translate(${dx}px,${dy}px) scale(${scale}) rotateY(180deg)`, offset: 0 },
    { transform: `translate(${dx}px,${dy + 40}px) scale(${scale * 0.98}) rotateY(180deg)`, offset: .18 },   // 살짝 움츠렸다가
    { transform: `translate(${dx}px,${dy - innerHeight * 1.3}px) scale(${scale * 0.7}) rotateY(180deg)`, offset: 1 },   // 위로 휙
  ], { duration: T.out, easing: "cubic-bezier(.5,-.2,.2,1)", fill: "forwards" });
  sceneFade = root.animate([{ opacity: 1 }, { opacity: 0 }],
    { duration: T.out, easing: "linear", fill: "forwards" });
  await Promise.all([outMove.finished, sceneFade.finished]);

  el.remove();
  dim.classList.remove("on");
  if (src) src.classList.remove("gone");
  lastT = performance.now();
  spinning = true;
}
let sceneFade = null;          // 퇴장 때 화면 전체를 옅게 한 애니메이션 — 다음 연출이 시작할 때 걷는다
function sceneReset() { if (sceneFade) { sceneFade.cancel(); sceneFade = null; } }

const wait = ms => new Promise(r => setTimeout(r, ms));

refresh();
return { play, reset: sceneReset, refresh };
} });
