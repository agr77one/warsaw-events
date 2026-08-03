const CACHE_NAME = "warsaw-weekend-v2-2";
const SHELL = [
  "./",
  "index.html",
  "styles.css",
  "enhancements.css",
  "app.js",
  "submit-event.js",
  "manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isData = url.pathname.endsWith("/data/events.json") || url.pathname.endsWith("/data/source_health.json");
  if (isData) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request)),
    );
    return;
  }
  if (event.request.method === "GET") {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
  }
});
