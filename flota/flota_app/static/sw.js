/* =====================================================
   SERVICE WORKER — PWA CONTROL DE TRANSPORTE
   Compatible con Django + Render
   Estable y sin rutas inválidas
===================================================== */

const VERSION = "v1";
const CACHE_NAME = `control-transporte-${VERSION}`;

/* =====================================================
   📦 ARCHIVOS MÍNIMOS A CACHEAR
   (SOLO lo que realmente existe)
===================================================== */
const APP_SHELL = [
  "/sistema/conductor/",
  "/static/flota_app/pwa/manifest.json"
];

/* =====================================================
   🔧 INSTALL
===================================================== */
self.addEventListener("install", event => {
  self.skipWaiting();

  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(APP_SHELL);
    })
  );
});

/* =====================================================
   🔁 ACTIVATE
===================================================== */
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    ).then(() => self.clients.claim())
  );
});

/* =====================================================
   🌐 FETCH
   - Red primero
   - Cache solo si falla
===================================================== */
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request).catch(() => {
      return caches.match(event.request);
    })
  );
});
