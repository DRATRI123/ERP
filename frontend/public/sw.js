/* Private ERP service worker: lets the app open and bill without internet.
 * - Built files (/_next/static) are cached the first time they are used; they never change.
 * - Pages and page data are fetched from the network and kept as a fallback for when it is down.
 * - API calls (/api) are never cached: offline bills are queued by the app and sent later. */
const STATIC = "erp-static-v1";
const PAGES = "erp-pages-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (![STATIC, PAGES].includes(key)) await caches.delete(key);
    await self.clients.claim();
  })());
});

async function cacheFirst(request) {
  const cache = await caches.open(STATIC);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) await cache.put(request, res.clone());
  return res;
}

async function networkFirst(request, fallbacks) {
  const cache = await caches.open(PAGES);
  try {
    const res = await fetch(request);
    if (res.ok && res.type === "basic") await cache.put(request, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(request, { ignoreVary: true });
    if (hit) return hit;
    for (const path of fallbacks) {
      const page = await cache.match(path, { ignoreVary: true });
      if (page) return page;
    }
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/shop/")) return;
  if (url.pathname.startsWith("/_next/static/") || /\.(?:png|svg|ico|woff2?)$/.test(url.pathname)) {
    event.respondWith(cacheFirst(req));
  } else if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, ["/sales/new", "/dashboard"]));
  } else if (req.headers.get("RSC") === "1") {
    event.respondWith(networkFirst(req, []));
  }
});
