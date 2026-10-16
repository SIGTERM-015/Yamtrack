// Magpie service worker. Pages and data always come from the network (they
// are per-user and dynamic); only static assets are cached, plus an offline
// page shown when a navigation fails.
// Registered as <BASE_URL>/serviceworker.js?<mtime>: the prefix and the cache
// version are derived from the script URL itself.
const BASE = new URL('.', self.location).pathname;
const VERSION = self.location.search.slice(1) || '0';
const STATIC_CACHE = `magpie-static-${VERSION}`;
const OFFLINE_URL = `${BASE}offline/`;
const STATIC_PREFIX = `${BASE}static/`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' })))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((name) => name !== STATIC_CACHE).map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  if (url.pathname.startsWith(STATIC_PREFIX)) {
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) =>
        cache.match(request).then((hit) => hit || fetch(request).then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        }))
      )
    );
  }
});
