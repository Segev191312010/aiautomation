self.addEventListener('push', (event) => {
  let payload = {}
  try {
    const decoded = event.data ? event.data.json() : {}
    payload = decoded && typeof decoded === 'object' && !Array.isArray(decoded)
      ? decoded
      : {}
  } catch {
    payload = {}
  }

  const title = typeof payload.title === 'string' ? payload.title : 'Trading Dashboard'
  const options = {
    body: typeof payload.body === 'string' ? payload.body : 'A trading alert fired.',
    icon: '/icon.svg',
    badge: '/icon.svg',
    tag: typeof payload.tag === 'string' ? payload.tag : 'trading-alert',
    data: payload.data && typeof payload.data === 'object' ? payload.data : {},
  }

  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const alertsUrl = new URL('/alerts', self.location.origin).href

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const existing = clients.find((client) => new URL(client.url).origin === self.location.origin)
      if (existing) {
        return existing.navigate(alertsUrl).then(() => existing.focus())
      }
      return self.clients.openWindow(alertsUrl)
    }),
  )
})
