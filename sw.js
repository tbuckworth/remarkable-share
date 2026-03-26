// Minimal service worker required for PWA installability
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
