// Hermes service worker: keeps the app shell available offline.
// API calls always go to the network; nothing private is cached here.
"use strict";

const CACHE = "hermes-shell-v26";
const SHELL = [
  "/", "/index.html", "/hermes.css", "/app.css", "/icons.js", "/theme.js", "/app.js", "/vendor/dompurify/purify.min.js", "/vendor/fuse/fuse.basic.min.js", "/vendor/sortable/Sortable.min.js", "/vendor/uplot/uPlot.iife.min.js", "/vendor/uplot/uPlot.min.css", "/manifest.webmanifest",
  "/fonts/geist-latin-400-normal.woff2", "/fonts/geist-latin-600-normal.woff2",
  "/icons/icon.svg", "/icons/icon-192.png", "/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // network only
  // Network first so updates land at once; fall back to the cached shell.
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/index.html")))
  );
});
