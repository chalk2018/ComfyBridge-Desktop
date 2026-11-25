/* ComfyBridge · Service Worker
   只服务于「安装为应用」的资格，不缓存任何资源，也不改变任何请求的行为。
   唯一拦截的是页面导航请求，并且原样透传给网络。 */
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // 只接管地址栏导航；API、媒体、脚本等一律不拦，保持原行为
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request));
  }
});
