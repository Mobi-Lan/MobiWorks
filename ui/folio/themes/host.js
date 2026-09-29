/* 연주 시작 연출 — 테마 팩의 공용 뼈대 `OpeningHost`.
   연출은 테마 폴더(카드형·필름형·티켓형·쇼츠형)로 나뉘고 설정에서 고른다 (기본 카드형).

   누가 무엇을 쥐나:
     ui/folio/opening2.html            호스트 페이지 — 서버에서 곡 목록·대기열·설정(opening_theme)을 받고, 커버 주소를 검사하고,
                                       고른 팩을 싣고 붙이고, 데이터 계약 playOpening({ song, players, badge, kicker, inst }) 을 연다
     ui/folio/themes/host.js           이 파일 — 팩이 같이 쓰는 도구·곡 목록 자리·팩 등록/붙이기 (서버를 모른다: 미리보기도 이것을 쓴다)
     ui/folio/themes/<이름>/theme.css   선택자를 전부 `.op-<이름>` 아래에 가둔다 (@keyframes 이름도 <이름>- 로 시작)
     ui/folio/themes/<이름>/theme.js    OpeningHost.register("<이름>", { label, mount(root, host) → { play, reset, refresh } })

   팩이 지킬 것:
     · play(data) 는 6000ms(DUR) 안에 끝나는 Promise — 창은 6.0 + 0.5초 뒤 숨는다 (folio/opening_wv.py HIDE_PAD)
     · 퇴장이 화면을 옅게 둔 채 끝나도 된다 — 다음 play 가 시작할 때 스스로 되돌린다 (reset 도 같은 일)
     · 밖에서 온 글자(곡 제목·칭호·이름·악기)는 전부 esc() 를 지나 innerHTML 에 들어간다 (보안)
     · 커버는 곡 항목의 `cover`(호스트가 검사한 CSS 값 `url('/folio/covers/<번호>?v=…')`)를 그대로 쓴다 */
(function () {
  "use strict";
  const DUR = 6000;
  // 시간 예산 — 1.5 고르기 · 0.7 들어올림 · 0.6 드러냄 · 2.4 유지 · 0.8 퇴장 = 6.0초 (engine.OPENING_SEC)
  const T = Object.freeze({ spin: 1500, lift: 700, flip: 600, hold: 2400, out: 800 });
  if (T.spin + T.lift + T.flip + T.hold + T.out !== DUR) throw new Error("연출 예산이 6000ms 가 아니다");
  const UNKNOWN_COVER = "linear-gradient(160deg,#ccc,#777)";   // 번호가 없는 곡 (보관함에 없는 제목)
  const ID_OK = /^[a-z][a-z0-9-]{0,23}$/;                        // 팩 이름 = 폴더 이름 = `.op-<이름>`

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const fmt = sec => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  const songTag = id => Number(id) > 0 ? "#" + String(Number(id)).padStart(4, "0") : "";
  const autoBadge = n => n >= 2 ? `${n}인 합주` : "솔로";
  const autoKicker = n => n >= 3 ? "ENSEMBLE" : n === 2 ? "DUET" : "SOLO";
  const mod = (a, n) => ((a % n) + n) % n;
  // 씨앗 문자열 → 0..1 난수열 (바코드·필름 가장자리 번호처럼 「곡마다 늘 같은」 장식에 쓴다)
  function rng(seedStr) {
    let h = 2166136261 >>> 0;
    for (const ch of String(seedStr)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
    return () => { h ^= h << 13; h >>>= 0; h ^= h >>> 17; h ^= h << 5; h >>>= 0; return h / 4294967296; };
  }

  const defs = new Map();       // 이름 → { label, mount }
  const inst = new Map();       // 이름 → 붙인 것 { id, root, play, reset, refresh }
  let current = null;

  const host = window.OpeningHost = {
    DUR, T, UNKNOWN_COVER, esc, wait, fmt, songTag, autoBadge, autoKicker, mod, rng,
    // ── 곡 목록 자리 — 호스트 페이지(opening2.html)가 서버에서 받아 채운다 (미리보기는 견본으로) ──
    songs: [],        // [{ id, key, keys, title, raw, duration, cover, gen, sq }] — 악보함 순서, 제목 하나에 한 장
    total: 0,         // 번호를 받은 곡 수 (사라진 곡 포함) — `#0056 / 184` 뒤쪽
    queueKeys: [],    // 지금 대기열(= 현재 재생목록)의 곡 key, 재생 차례대로
    pickKey: null,    // 이번에 뽑힐 곡의 key
    dimA: null,       // 게임을 어둡게 하는 정도 (?dim=) — null 이면 팩의 기본값
    randomCover: null,   // 번호 없는 곡의 커버 — 기본 풀의 무작위 한 장 (호스트 페이지가 연출마다 새로 받는다)
    // 곁들이는 곡(링·띠·피드)의 **나머지 자리**를 섞는 씨앗 — **연출마다 새로** (newRound). 재생목록 곡은 늘 먼저다.
    // 예전엔 카드형 씨앗이 창을 열 때 한 번만 정해지고 띠·피드는 악보함 순서라, 앱을 켜 둔 동안 늘 같은 곡이 나왔다
    seed: (Math.random() * 4294967296) >>> 0,
    newRound() { host.seed = (Math.random() * 4294967296) >>> 0; return host.seed; },
    hash(seed, s) {                        // 씨앗 + 글자 → 32비트 (FNV-1a) — 같은 판 안에서는 같은 순서라 목록을 다시 받아도 깜빡이지 않는다
      let h = (2166136261 ^ (seed >>> 0)) >>> 0;
      for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
      return h >>> 0;
    },

    findSong(title) {
      const t = String(title ?? "");
      return host.songs.find(s => s.title === t) || host.songs.find(s => s.raw === t) || null;
    },
    // 곡 → 연출에 쓰는 한 장 (곡 목록에 없으면 제목 + 기본 풀의 무작위 커버, 그것도 없으면 빈 커버)
    songData(title) {
      return host.findSong(title) || { id: null, key: "", title: String(title ?? ""), raw: "", duration: null,
                                       cover: host.randomCover || UNKNOWN_COVER };
    },
    // 띠·피드처럼 한 줄로 늘어놓는 팩이 쓸 곡 — **지금 재생목록(대기열) 1순위 → 뽑힐 곡 → 나머지는 악보함에서 무작위**, n 장까지.
    // 나머지의 순서는 이번 판의 씨앗(seed)으로 섞는다 — 판마다 다르고, 같은 판 안에서는 같다
    // (예전엔 나머지를 악보함 순서대로 채워 특정 곡만 나왔다).
    // 악보함은 수백 곡일 수 있다 — 전부 올리면 그림 수백 장을 한꺼번에 받는다.
    featured(n) {
      const lim = Math.max(1, Number(n) || 1);
      const byKey = new Map();
      for (const s of host.songs) for (const k of (s.keys || [s.key])) if (k != null && k !== "" && !byKey.has(String(k))) byKey.set(String(k), s);
      const out = [], seen = new Set();
      const add = s => { if (s && !seen.has(s) && out.length < lim) { seen.add(s); out.push(s); } };
      for (const k of host.queueKeys || []) add(byKey.get(String(k)));
      if (host.pickKey != null) add(byKey.get(String(host.pickKey)));
      const seed = host.seed >>> 0;
      host.songs.map((s, i) => [host.hash(seed, String(s.key != null ? s.key : s.title)), i, s])
        .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1])).forEach(x => add(x[2]));
      return out;
    },
    // 직전 곡들 — 대기열(재생 차례)에서 뽑힐 곡 바로 앞부터 거꾸로 n 곡 (가까운 것이 먼저). 뽑힐 곡이 첫 곡이거나
    // 대기열에 없으면 빈 배열 (쇼츠형: 지금 칸·다음 칸에 직전 곡들, 그다음 칸에 그 곡)
    prevSongs(n) {
      const keys = (host.queueKeys || []).map(String), pk = host.pickKey != null ? String(host.pickKey) : null;
      const i = pk == null ? -1 : keys.indexOf(pk);
      if (i <= 0) return [];
      const byKey = new Map();
      for (const s of host.songs) for (const k of (s.keys || [s.key])) if (k != null && k !== "" && !byKey.has(String(k))) byKey.set(String(k), s);
      const out = [];
      for (let j = i - 1; j >= 0 && out.length < Math.max(1, Number(n) || 1); j--) { const s = byKey.get(keys[j]); if (s) out.push(s); }
      return out;
    },
    prevSong() { return host.prevSongs(1)[0] || null; },
    // 사람 줄 규칙 — 6명까지 보이고 나머지는 「외 n」 (카드형과 같다)
    roster(players) {
      players = Array.isArray(players) ? players.filter(p => Array.isArray(p)) : [];
      const shown = players.slice(0, 6);
      return { n: players.length, shown, more: players.length - shown.length };
    },
    idLine(id) { return Number(id) > 0 ? `${songTag(id)} / ${Number(host.total) || host.songs.length}` : ""; },

    // ── 팩 ──
    register(id, def) {
      if (!ID_OK.test(String(id)) || defs.has(id) || !def || typeof def.mount !== "function") return false;
      defs.set(id, { label: String(def.label || id), mount: def.mount });
      return true;
    },
    has: id => defs.has(id),
    ids: () => [...defs.keys()],
    label: id => (defs.get(id) || {}).label || id,
    // 팩을 붙인다 (한 번만). 뿌리 = <div class="op-theme op-<이름>"> — 붙인 뒤에 mount 를 부른다(문서 안에서 id 로 찾을 수 있게)
    mount(id, container) {
      if (inst.has(id)) return inst.get(id);
      const def = defs.get(id);
      if (!def || !container) return null;
      const root = document.createElement("div");
      root.className = `op-theme op-${id}`;
      root.dataset.theme = id;
      root.hidden = true;
      if (host.dimA != null) root.style.setProperty("--dim-a", String(host.dimA));
      container.appendChild(root);
      let x = null;
      try { x = def.mount(root, host) || null; } catch (e) { console.error(`[opening] ${id} 팩을 붙이지 못했다:`, e); }
      if (!x || typeof x.play !== "function") { root.remove(); return null; }
      const it = { id, root, play: x.play, reset: typeof x.reset === "function" ? x.reset : () => {},
                   refresh: typeof x.refresh === "function" ? x.refresh : () => {} };
      inst.set(id, it);
      return it;
    },
    // 이 팩으로 — 고른 것만 보인다. 연출과 연출 사이에만 부른다 (호스트가 지킨다)
    use(id, container) {
      const it = host.mount(id, container);
      if (!it) return null;
      if (current !== id) {
        for (const [k, v] of inst) v.root.hidden = k !== id;
        current = id;
        it.refresh();
      }
      return it;
    },
    current: () => current,
    active: () => (current ? inst.get(current) || null : null),
    refresh() { const it = host.active(); if (it) it.refresh(); },
  };
})();
