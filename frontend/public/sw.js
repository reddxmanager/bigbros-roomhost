// Minimal service worker for installability and an offline shell.
// It intercepts only navigation requests (network first, cached shell on
// failure). Module and asset requests fall through untouched, so local dev
// hot-reload is not affected.

const CACHE = 'bigbros-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.mode !== 'navigate') return
  event.respondWith(
    fetch(req).catch(() => caches.match('/index.html').then((r) => r || fetch(req))),
  )
})
