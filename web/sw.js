/* QuantiDawn Estimator service worker.
   Caches only public static files (fonts, logos) and an offline page. It never stores plans, project data,
   pages, tiles, downloads or any signed-in HTML: those always come live from the server. */
const VERSION = "qd-v1";
const STATIC = /^\/(fonts|brand)\//;
const SHELL = ["/offline", "/fonts/fonts.css", "/brand/favicon-192.png", "/brand/logo-full.png", "/brand/logo-white.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;       // uploads, API writes and storage links pass straight through
  if (STATIC.test(url.pathname)) {                                           // cache-first for fonts and logos
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then(c => c.put(req, copy)); }
      return res;
    })));
    return;
  }
  if (req.mode === "navigate") {                                             // pages: always live; a friendly screen when offline
    e.respondWith(fetch(req).catch(() => caches.match("/offline")));
  }
});
