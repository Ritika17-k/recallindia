// Caches the app's own files so it opens instantly and installs as an app. API calls always go to the server.
const CACHE = "recallindia-v2";
const SHELL = ["/app/", "/app/app.css", "/app/app.js", "/app/manifest.webmanifest", "/app/icon-192.png", "/app/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || !url.pathname.startsWith("/app/")) return;
  // Network first, so a new version shows up straight away; cached copy when offline.
  e.respondWith(
    fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("/app/")))
  );
});

// Phone notifications for recall alerts.
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "RecallIndia", {
    body: data.body || "Something you bought matches a recall notice.",
    icon: "/app/icon-192.png", badge: "/app/icon-192.png", tag: data.tag, data: { url: data.url || "/app/#/alerts" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/app/#/alerts";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if (c.url.includes("/app/")) { c.navigate(url); return c.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
