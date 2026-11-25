/* ComfyBridge · PWA 注册
   只做一件事：在安全上下文里注册 Service Worker，让浏览器可以「安装应用」。
   不缓存资源、不改动任何业务逻辑；注册失败时静默忽略，页面照常使用。 */
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  });
}
