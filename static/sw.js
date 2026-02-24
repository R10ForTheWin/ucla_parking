// Justin EMBAlake service worker — PWA installability + push notifications
self.addEventListener('install',  e => self.skipWaiting());
self.addEventListener('activate', e => clients.claim());
self.addEventListener('fetch',    e => e.respondWith(fetch(e.request)));

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  e.waitUntil(
    self.registration.showNotification(data.title || 'Justin EMBAlake', {
      body:      data.body || "Today is a parking day! Tap to buy.",
      icon:      '/static/icon-192.png',
      badge:     '/static/icon-192.png',
      tag:       'parking-reminder',
      renotify:  true,
      data:      { url: '/' },
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list)
        if (client.url.includes(self.location.origin) && 'focus' in client)
          return client.focus();
      return clients.openWindow('/');
    })
  );
});
