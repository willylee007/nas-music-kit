self.addEventListener('install', (event) => {
  self.skipWaiting(); // 立即激活
});

self.addEventListener('activate', (event) => {
  event.waitUntil(clients.claim()); // 立即接管页面
});

self.addEventListener('fetch', (event) => {
  // 零缓存，全透传。
  // 仅此函数存在即满足 PWA 安装条件，但不拦截任何流量。
  return; 
});
