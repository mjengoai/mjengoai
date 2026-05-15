/* ══════════════════════════════════════════════
   MjengoAI Service Worker  v1.0
   Caches the full app for offline use
   ══════════════════════════════════════════════ */

const CACHE = 'mjengoai-v1';

/* All assets to pre-cache on install */
const PRECACHE = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

/* ── Install: cache core assets ─────────────────────────────────────────── */
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => {
      return cache.addAll(PRECACHE);
    }).then(() => self.skipWaiting())
  );
});

/* ── Activate: delete old caches ────────────────────────────────────────── */
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

/* ── Fetch: cache-first for assets, network-first for navigation ─────────── */
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  /* Skip non-GET and cross-origin requests */
  if (request.method !== 'GET') return;
  if (url.origin !== location.origin) return;

  /* Navigation (HTML pages): network-first, fallback to cache */
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          const clone = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, clone));
          return response;
        })
        .catch(() => caches.match('/index.html'))
    );
    return;
  }

  /* Everything else: cache-first */
  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(response => {
        if (!response || response.status !== 200 || response.type === 'opaque') {
          return response;
        }
        const clone = response.clone();
        caches.open(CACHE).then(cache => cache.put(request, clone));
        return response;
      });
    })
  );
});

/* ── Background sync placeholder ────────────────────────────────────────── */
self.addEventListener('sync', event => {
  if (event.tag === 'sync-contacts') {
    /* Future: sync contact updates when back online */
    console.log('[SW] Background sync: contacts');
  }
});

/* ── Push notifications placeholder ─────────────────────────────────────── */
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'MjengoAI';
  const options = {
    body: data.body || 'New update available',
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-96.png',
    vibrate: [200, 100, 200],
    data: { url: data.url || '/' }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow(event.notification.data.url || '/')
  );
});
