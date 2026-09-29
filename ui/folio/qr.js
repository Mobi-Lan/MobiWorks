/* QR 만들기 — 폰 카메라로 찍어 바로 잇게 하려고 쓴다.
 *
 * 왜 직접 만드나: 보안 정책(CSP)이 바깥 스크립트를 막는다. 꾸러미를 받아 올 수 없으니
 * 필요한 만큼만 담는다 — **바이트 모드 · 오류정정 M · 1~10판**. 우리가 담을 것은
 * `https://link.mobimml.com/?c=A2K9PQ7M` 같은 짧은 주소라 10판(≤ 213바이트)이면 넉넉하다.
 *
 * 쓰는 법:  QR.make("https://…")  →  {n, dark(x,y)}   n = 한 변의 칸 수
 *           QR.svg("https://…", 200)  →  <svg> 글자
 *
 * 맞는지 어떻게 아는가: segno(파이썬)로 같은 글자를 찍어 **칸 하나하나까지** 맞춰 본다
 * (scratchpad/qa_qr.py). 눈으로는 못 보는 어긋남을 그쪽이 잡아 준다.
 */
(function () {
  "use strict";

  // ── 갈루아 체 GF(256) — 오류정정 코드가 여기서 돈다 ──
  var EXP = new Uint8Array(512), LOG = new Uint8Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11d;          // 0b100011101 — QR 이 쓰는 나눗셈 값
    }
    for (var j = 255; j < 512; j++) EXP[j] = EXP[j - 255];
  })();

  function mul(a, b) { return (a === 0 || b === 0) ? 0 : EXP[LOG[a] + LOG[b]]; }

  /** 오류정정 몫을 낼 때 쓰는 나눗수 (차수 n). */
  function genPoly(n) {
    var p = [1];
    for (var i = 0; i < n; i++) {
      var q = new Array(p.length + 1).fill(0);
      for (var j = 0; j < p.length; j++) {
        // 항은 **높은 차수가 앞**이다. x 를 곱하면 제자리, α^i 를 곱하면 한 칸 뒤로 간다.
        // 이 둘을 바꿔 써서 오류정정이 통째로 틀렸다 (규격 예제로 잡았다).
        q[j] ^= p[j];
        q[j + 1] ^= mul(p[j], EXP[i]);
      }
      p = q;
    }
    return p;
  }

  /** 자료 한 덩이에 붙일 오류정정 바이트. */
  function ecBytes(data, n) {
    var g = genPoly(n);
    var r = new Array(data.length + n).fill(0);
    for (var i = 0; i < data.length; i++) r[i] = data[i];
    for (var i2 = 0; i2 < data.length; i2++) {
      var c = r[i2];
      if (!c) continue;
      for (var j = 0; j < g.length; j++) r[i2 + j] ^= mul(g[j], c);
    }
    return r.slice(data.length);
  }

  // ── 판(version)별 크기표 — 오류정정 M 만 담는다 ──
  //   [담을 수 있는 바이트, 덩이당 오류정정 바이트, 1군 덩이 수, 1군 자료 바이트, 2군 덩이 수, 2군 자료 바이트]
  var VER = {
    1: [14, 10, 1, 16, 0, 0], 2: [26, 16, 1, 28, 0, 0], 3: [42, 26, 1, 44, 0, 0],
    4: [62, 18, 2, 32, 0, 0], 5: [84, 24, 2, 43, 0, 0], 6: [106, 16, 4, 27, 0, 0],
    7: [122, 18, 4, 31, 0, 0], 8: [152, 22, 2, 38, 2, 39], 9: [180, 22, 3, 36, 2, 37],
    10: [213, 26, 4, 43, 1, 44],
  };
  // 판별 맞춤 무늬(alignment) 자리
  var ALIGN = { 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
                7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50] };

  function pickVersion(len) {
    for (var v = 1; v <= 10; v++) if (len <= VER[v][0]) return v;
    throw new Error("QR: 담기에 너무 깁니다");
  }

  // ── 글자 → 비트 ──
  /** 그 판이 담는 **자료 바이트 수** (오류정정을 뺀 것). 덧붙이는 여기까지 채운다. */
  function dataWords(ver) {
    var m = VER[ver];
    return m[2] * m[3] + m[4] * m[5];
  }

  function bitsFor(bytes, ver) {
    // 첫 칸(담을 수 있는 바이트)은 **글자를 얼마나 넣느냐**이고,
    // 여기서 필요한 것은 **자료칸이 몇 개냐**다. 둔 것을 헷갈려 써서 비트가 모자랐다.
    var cap = dataWords(ver);
    var out = [];
    function put(val, n) { for (var i = n - 1; i >= 0; i--) out.push((val >> i) & 1); }
    put(4, 4);                                   // 바이트 모드
    put(bytes.length, ver < 10 ? 8 : 16);        // 길이 (1~9판은 8비트)
    for (var i = 0; i < bytes.length; i++) put(bytes[i], 8);
    var total = cap * 8;
    for (var t = 0; t < 4 && out.length < total; t++) out.push(0);   // 끝 표시
    while (out.length % 8) out.push(0);
    var pad = [0xec, 0x11], k = 0;
    while (out.length < total) { put(pad[k++ % 2], 8); }
    var data = [];
    for (var b = 0; b < out.length; b += 8) {
      var v = 0;
      for (var j = 0; j < 8; j++) v = (v << 1) | out[b + j];
      data.push(v);
    }
    return data;
  }

  /** 자료를 덩이로 나누고, 덩이별 오류정정을 붙여 **번갈아** 늘어놓는다. */
  function interleave(data, ver) {
    var m = VER[ver], ecLen = m[1], g1 = m[2], d1 = m[3], g2 = m[4], d2 = m[5];
    var blocks = [], at = 0;
    for (var i = 0; i < g1; i++) { blocks.push(data.slice(at, at + d1)); at += d1; }
    for (var j = 0; j < g2; j++) { blocks.push(data.slice(at, at + d2)); at += d2; }
    var ecs = blocks.map(function (b) { return ecBytes(b, ecLen); });
    var out = [];
    var maxD = Math.max(d1, d2);
    for (var c = 0; c < maxD; c++) {
      for (var b2 = 0; b2 < blocks.length; b2++) if (c < blocks[b2].length) out.push(blocks[b2][c]);
    }
    for (var e = 0; e < ecLen; e++) {
      for (var b3 = 0; b3 < ecs.length; b3++) out.push(ecs[b3][e]);
    }
    return out;
  }

  // ── 바탕 그리기 ──
  function blank(n) {
    var m = [], r = [];
    for (var y = 0; y < n; y++) {
      m.push(new Int8Array(n).fill(-1));      // -1 = 아직 안 정함
      r.push(new Uint8Array(n));              // 1 = 자료를 놓으면 안 되는 자리
    }
    return { m: m, r: r };
  }

  function finder(g, n, x0, y0) {
    for (var y = -1; y <= 7; y++) {
      for (var x = -1; x <= 7; x++) {
        var x1 = x0 + x, y1 = y0 + y;
        if (x1 < 0 || y1 < 0 || x1 >= n || y1 >= n) continue;
        var on = (0 <= x && x <= 6 && (y === 0 || y === 6)) ||
                 (0 <= y && y <= 6 && (x === 0 || x === 6)) ||
                 (2 <= x && x <= 4 && 2 <= y && y <= 4);
        g.m[y1][x1] = on ? 1 : 0;
        g.r[y1][x1] = 1;
      }
    }
  }

  function make(text) {
    var bytes = [];
    var esc = encodeURIComponent(text);       // UTF-8 바이트로 (한글도 담긴다)
    for (var i = 0; i < esc.length; i++) {
      if (esc[i] === "%") { bytes.push(parseInt(esc.substr(i + 1, 2), 16)); i += 2; }
      else bytes.push(esc.charCodeAt(i));
    }
    var ver = pickVersion(bytes.length);
    var n = 17 + ver * 4;
    var g = blank(n);

    finder(g, n, 0, 0);
    finder(g, n, n - 7, 0);
    finder(g, n, 0, n - 7);
    for (var i2 = 8; i2 < n - 8; i2++) {        // 눈금줄
      var on = (i2 % 2 === 0) ? 1 : 0;
      if (g.m[6][i2] === -1) { g.m[6][i2] = on; g.r[6][i2] = 1; }
      if (g.m[i2][6] === -1) { g.m[i2][6] = on; g.r[i2][6] = 1; }
    }
    var al = ALIGN[ver];
    var lastA = al.length ? al[al.length - 1] : 0;
    for (var a = 0; a < al.length; a++) {
      for (var b = 0; b < al.length; b++) {
        var cx = al[a], cy = al[b];
        // 빼는 것은 **찾기 무늬와 겹치는 세 모서리뿐**이다.
        // 「가운데가 6이면 건너뛴다」로 두었더니 7판부터 (6,26)·(26,6) 같은 멀쩡한 자리까지
        // 빠져 자료가 통째로 밀렸다. 2~6판에서는 우연히 같은 결과라 안 드러났다.
        if ((cx === 6 && cy === 6) || (cx === 6 && cy === lastA) || (cx === lastA && cy === 6)) continue;
        for (var dy = -2; dy <= 2; dy++) {
          for (var dx = -2; dx <= 2; dx++) {
            var v = (Math.max(Math.abs(dx), Math.abs(dy)) !== 1) ? 1 : 0;
            g.m[cy + dy][cx + dx] = v;
            g.r[cy + dy][cx + dx] = 1;
          }
        }
      }
    }
    g.m[n - 8][8] = 1; g.r[n - 8][8] = 1;       // 늘 검은 칸
    for (var k = 0; k < 9; k++) {               // 형식 정보 자리를 비워 둔다
      if (g.m[8][k] === -1) { g.m[8][k] = 0; g.r[8][k] = 1; }
      if (g.m[k][8] === -1) { g.m[k][8] = 0; g.r[k][8] = 1; }
    }
    for (var k2 = 0; k2 < 8; k2++) {
      g.r[8][n - 1 - k2] = 1;
      g.r[n - 1 - k2][8] = 1;
    }
    if (ver >= 7) {                             // 7판부터는 판 정보 자리도 있다
      for (var y2 = 0; y2 < 6; y2++) {
        for (var x2 = 0; x2 < 3; x2++) { g.r[y2][n - 11 + x2] = 1; g.r[n - 11 + x2][y2] = 1; }
      }
    }

    var bytesOut = interleave(bitsFor(bytes, ver), ver);
    var bits = [];
    for (var q = 0; q < bytesOut.length; q++) {
      for (var w = 7; w >= 0; w--) bits.push((bytesOut[q] >> w) & 1);
    }

    // 오른쪽 아래에서 두 칸씩, 위아래로 번갈아 오르내리며 채운다
    var at = 0, up = true;
    for (var col = n - 1; col > 0; col -= 2) {
      if (col === 6) col--;                     // 눈금줄은 건너뛴다
      for (var r2 = 0; r2 < n; r2++) {
        var y3 = up ? (n - 1 - r2) : r2;
        for (var c2 = 0; c2 < 2; c2++) {
          var x3 = col - c2;
          if (g.r[y3][x3]) continue;
          g.m[y3][x3] = at < bits.length ? bits[at++] : 0;
        }
      }
      up = !up;
    }

    var best = null;
    for (var mask = 0; mask < 8; mask++) {
      var cand = applyMask(g, n, mask, ver);
      var s = score(cand, n);
      if (!best || s < best.score) best = { score: s, m: cand, mask: mask };
    }
    return { n: n, ver: ver, mask: best.mask, m: best.m,
             dark: function (x, y) { return best.m[y][x] === 1; } };
  }

  var FMT_G = 0x537;                            // 형식 정보에 붙이는 나눗수
  function formatBits(mask) {
    var v = (0 /* M = 00 */ << 3) | mask;       // 오류정정 M
    var d = v << 10;
    for (var i = 4; i >= 0; i--) if (d & (1 << (i + 10))) d ^= FMT_G << i;
    return ((v << 10) | d) ^ 0x5412;
  }

  var VER_G = 0x1f25;
  function versionBits(ver) {
    var d = ver << 12;
    for (var i = 5; i >= 0; i--) if (d & (1 << (i + 12))) d ^= VER_G << i;
    return (ver << 12) | d;
  }

  function maskAt(mask, x, y) {
    switch (mask) {
      case 0: return (y + x) % 2 === 0;
      case 1: return y % 2 === 0;
      case 2: return x % 3 === 0;
      case 3: return (y + x) % 3 === 0;
      case 4: return (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0;
      case 5: return ((y * x) % 2) + ((y * x) % 3) === 0;
      case 6: return (((y * x) % 2) + ((y * x) % 3)) % 2 === 0;
      default: return (((y + x) % 2) + ((y * x) % 3)) % 2 === 0;
    }
  }

  function applyMask(g, n, mask, ver) {
    var out = [];
    for (var y = 0; y < n; y++) {
      out.push(new Int8Array(n));
      for (var x = 0; x < n; x++) {
        var v = g.m[y][x];
        if (!g.r[y][x] && maskAt(mask, x, y)) v ^= 1;
        out[y][x] = v;
      }
    }
    var f = formatBits(mask);
    for (var i = 0; i < 15; i++) {
      var bit = (f >> i) & 1;
      // 첫 사본 — 앞 6비트는 **8번 칸을 따라 아래로**, 뒤 6비트는 **8번 줄을 따라 왼쪽으로**.
      // 이 둘을 가로·세로 바꿔 넣고 있었다 — 우리끼리는 앞뒤가 맞아 되읽혔지만,
      // 진짜 스캐너는 못 읽는다 (segno 를 잉대로 써 잡았다).
      if (i < 6) out[i][8] = bit;
      else if (i === 6) out[7][8] = bit;
      else if (i === 7) out[8][8] = bit;
      else if (i === 8) out[8][7] = bit;
      else out[8][14 - i] = bit;
      // 둘째 사본 — 앞 8비트는 **8번 줄의 오른쪽**, 뒤 7비트는 **8번 칸의 아래쪽**.
      if (i < 8) out[8][n - 1 - i] = bit;
      else out[n - 15 + i][8] = bit;
    }
    out[n - 8][8] = 1;
    if (ver >= 7) {
      var vb = versionBits(ver);
      for (var j = 0; j < 18; j++) {
        var b = (vb >> j) & 1;
        out[Math.floor(j / 3)][n - 11 + (j % 3)] = b;
        out[n - 11 + (j % 3)][Math.floor(j / 3)] = b;
      }
    }
    return out;
  }

  /** 읽기 쉬운 무늬를 고르는 점수 (낮을수록 좋다) — 규격의 네 가지 벌점. */
  function score(m, n) {
    var p = 0, x, y, i, run, last, dark = 0;
    for (y = 0; y < n; y++) {                    // 1) 같은 색이 줄줄이
      run = 1; last = m[y][0];
      for (x = 1; x < n; x++) {
        if (m[y][x] === last) { run++; if (run === 5) p += 3; else if (run > 5) p++; }
        else { run = 1; last = m[y][x]; }
      }
    }
    for (x = 0; x < n; x++) {
      run = 1; last = m[0][x];
      for (y = 1; y < n; y++) {
        if (m[y][x] === last) { run++; if (run === 5) p += 3; else if (run > 5) p++; }
        else { run = 1; last = m[y][x]; }
      }
    }
    for (y = 0; y < n - 1; y++) {                // 2) 2×2 덩어리
      for (x = 0; x < n - 1; x++) {
        var v = m[y][x];
        if (v === m[y][x + 1] && v === m[y + 1][x] && v === m[y + 1][x + 1]) p += 3;
      }
    }
    var pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
    function hit(get, len) {                      // 3) 찾기 무늬를 닮은 줄
      for (var s = 0; s + 11 <= len; s++) {
        var a = true, b = true;
        for (i = 0; i < 11; i++) {
          var g2 = get(s + i);
          if (g2 !== pat1[i]) a = false;
          if (g2 !== pat2[i]) b = false;
        }
        if (a) p += 40;
        if (b) p += 40;
      }
    }
    for (y = 0; y < n; y++) hit(function (k) { return m[y][k]; }, n);
    for (x = 0; x < n; x++) hit(function (k) { return m[k][x]; }, n);
    for (y = 0; y < n; y++) for (x = 0; x < n; x++) if (m[y][x]) dark++;
    var pct = Math.abs(Math.round(dark * 100 / (n * n)) - 50);   // 4) 검고 흰 양의 치우침
    p += Math.floor(pct / 5) * 10;
    return p;
  }

  /** 화면에 붙일 수 있는 SVG 글자. 테두리 여백(4칸)은 규격대로 둔다. */
  function svg(text, px, dark, light) {
    var q = make(text), n = q.n, pad = 4, side = n + pad * 2;
    var d = "";
    for (var y = 0; y < n; y++) {
      for (var x = 0; x < n; x++) {
        if (q.dark(x, y)) d += "M" + (x + pad) + " " + (y + pad) + "h1v1h-1z";
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + px + '" height="' + px +
      '" viewBox="0 0 ' + side + " " + side + '" shape-rendering="crispEdges">' +
      '<rect width="' + side + '" height="' + side + '" fill="' + (light || "#fff") + '"/>' +
      '<path d="' + d + '" fill="' + (dark || "#000") + '"/></svg>';
  }

  window.QR = { make: make, svg: svg };
})();
