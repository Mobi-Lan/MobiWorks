/* sw.js — 폰에서 **앱처럼** 열리게 하는 최소 서비스 워커 (PWA).
 *
 * 하는 일은 딱 둘이다:
 *   1. 설치할 수 있게 해 준다 (브라우저가 서비스 워커를 요구한다)
 *   2. 네트워크가 잠깐 끊겼을 때 **껍데기라도** 띄운다
 *
 * ## 안 하는 일 — 여기가 더 중요하다
 *
 * **캐시를 먼저 보지 않는다.** 언제나 네트워크가 먼저고, 실패했을 때만 캐시를 꺼낸다
 * (network-first). 캐시를 먼저 보면 앱을 새로 빌드해도 **옛 화면이 계속 뜬다** —
 * exe 를 새로 만들어도 화면이 그대로인 사고가 된다.
 *
 * **`/api/` 는 손대지 않는다.** 큐 상태·재고·설정은 그때그때의 값이고, 캐시에 남으면
 * 지난 값을 현재처럼 보여 준다. 게다가 실행 토큰이 오가는 길이라 남길 자리가 아니다.
 *
 * **GET 만, 같은 출처만.** POST 는 지나가게 두고, 다른 출처는 아예 안 만진다.
 *
 * **토큰이 박힌 것은 담지 않는다.** 화면(`text/html`)과 `/folio/net.js` 에는 이 PC 의 실행
 * 토큰이 심겨 나간다. 캐시 저장소는 디스크에 남고 `Cache-Control: no-store` 도 안 듣는다 —
 * 처음엔 `./`·`./index.html` 을 미리 담고 200 이면 뭐든 담아서, 토큰이 브라우저 프로필에
 * 파일로 남았다. 껍데기 없이도 앱은 뜬다: 서버가 죽었으면 어차피 화면도 못 쓴다.
 */
"use strict";
const CACHE = "mobiworks-shell-v2";
const SHELL = ["./icon.svg", "./manifest.webmanifest"];
// 토큰이 심겨 나가는 조각 — 화면 파일은 Content-Type 으로 거르고, 이것은 이름으로 거른다
const STAMPED = ["/folio/net.js"];

function cacheable(url, res) {
  if (!res || res.status !== 200 || res.type !== "basic") return false;
  if (STAMPED.indexOf(url.pathname) >= 0) return false;
  const ct = (res.headers.get("Content-Type") || "").toLowerCase();
  return ct.indexOf("text/html") < 0;
}

self.addEventListener("install", (e) => {
  // 껍데기를 미리 담아 둔다. 하나라도 실패하면 설치를 접지 않고 그냥 넘어간다 —
  // 서비스 워커 때문에 앱이 안 열리는 일이 없어야 한다.
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => { }));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  // 옛 판의 캐시는 지운다 — 이름에 판이 들어 있으므로 새 판이 뜨면 옛 것은 쓸 일이 없다
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()).catch(() => { }));
});

/* 큐 알림을 눌렀다 — 열려 있는 이 앱의 창을 앞으로. 알림은 페이지가 `reg.showNotification` 으로 띄운 것이다
 * (푸시 서버는 없다 — 닫힌 브라우저에는 애초에 알림이 오지 않는다). */
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) return c.focus(); }
    return self.clients.openWindow ? self.clients.openWindow("./") : null;
  }));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  let url;
  try { url = new URL(req.url); } catch { return; }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;      // 그때그때의 값 — 남기지 않는다
  // 폰 사이트(link.mobimml.com)에서는 **같은 출처에 우편함이 있다** (`/box/<코드>` · `/new` · `/health`).
  // 여기를 담으면 한 번 읽고 사라져야 할 봉투가 폰 디스크에 남고, 끊겼을 때 옛 상자를 새것처럼 내준다.
  if (url.pathname.startsWith("/box/") || url.pathname === "/new" || url.pathname === "/health") return;

  e.respondWith(
    fetch(req).then((res) => {
      // 200 인 것만, 조각 응답(206)·화면·토큰 박힌 조각은 빼고 담는다
      if (cacheable(url, res)) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => { });
      }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || Response.error()))
  );
});
