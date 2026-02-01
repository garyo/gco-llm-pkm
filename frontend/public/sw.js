// Simple service worker for PWA support
const BUILD_TIMESTAMP = '__BUILD_TIMESTAMP__'; // Replaced during build
const CACHE_NAME = `pkm-assistant-${BUILD_TIMESTAMP}`;
const OFFLINE_PAGE = '/offline.html';
const STATIC_ASSETS = [
  '/',
  '/favicon.svg',
  '/manifest.json',
  OFFLINE_PAGE
];

// Install event - cache static assets (best-effort, don't block install on failures)
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => Promise.all(
        STATIC_ASSETS.map(url => cache.add(url).catch(() => {}))
      ))
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames
          .filter(name => name !== CACHE_NAME)
          .map(name => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event - network first with timeout, cache fallback for static assets
self.addEventListener('fetch', (event) => {
  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // Skip API calls (always go to network)
  if (event.request.url.includes('/api/') ||
      event.request.url.includes('/query') ||
      event.request.url.includes('/sessions')) {
    return;
  }

  // Check if this is a navigation request (page load)
  const isNavigationRequest = event.request.mode === 'navigate';

  event.respondWith(
    Promise.race([
      fetch(event.request),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Network timeout')), 5000)
      )
    ])
      .then(response => {
        // Cache successful responses
        if (response.ok) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      })
      .catch(async () => {
        // Network failed or timed out, try cache
        const cachedResponse = await caches.match(event.request);
        if (cachedResponse) {
          return cachedResponse;
        }
        // For navigation requests, show offline page instead of blank screen
        if (isNavigationRequest) {
          const offlineResponse = await caches.match(OFFLINE_PAGE);
          if (offlineResponse) {
            return offlineResponse;
          }
        }
        // Return a basic error response as last resort
        return new Response('Offline - please check your connection', {
          status: 503,
          statusText: 'Service Unavailable',
          headers: { 'Content-Type': 'text/plain' }
        });
      })
  );
});
