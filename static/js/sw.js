/* ================================================================
   MemeAssistant Service Worker — P3-2 离线包
   策略：静态资产 Cache-First，表情图片 Cache-First+后台更新，
         API Network-First，其余 Network-Only
================================================================ */

const CACHE_VERSION = "meme-v1";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const IMAGE_CACHE = `${CACHE_VERSION}-images`;

// 安装时要预缓存的静态资源（启动即下载）
const PRECACHE_URLS = [
  "/",
  "/static/css/styles.css",
  "/static/js/script.js",
  "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css",
];

// ── Install: 预缓存核心静态资源 ──────────────────────────
self.addEventListener("install", (event) => {
  console.log("[SW] 安装中…");
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      console.log("[SW] 预缓存静态资源");
      return Promise.allSettled(
        PRECACHE_URLS.map((url) =>
          cache.add(url).catch((err) =>
            console.warn(`[SW] 预缓存失败: ${url}`, err)
          )
        )
      );
    })
  );
  self.skipWaiting();
});

// ── Activate: 清理旧版本缓存 ─────────────────────────────
self.addEventListener("activate", (event) => {
  console.log("[SW] 激活");
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith("meme-") && k !== STATIC_CACHE && k !== IMAGE_CACHE)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch: 智能缓存策略 ──────────────────────────────────
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 只处理 GET
  if (request.method !== "GET") return;

  // ── 1. 表情图片 /memes/* → Cache-First + 后台更新 ──
  if (url.pathname.startsWith("/memes/")) {
    event.respondWith(
      caches.open(IMAGE_CACHE).then((cache) =>
        cache.match(request).then((cached) => {
          // 后台更新（Stale-While-Revalidate）
          const fetchPromise = fetch(request)
            .then((response) => {
              if (response.ok) cache.put(request, response.clone());
              return response;
            })
            .catch(() => null);

          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // ── 2. API 请求 → Network-First（不缓存） ──
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request).catch(() => {
        // 离线时返回提示
        return new Response(
          JSON.stringify({ message: "离线模式，此操作不可用" }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      })
    );
    return;
  }

  // ── 3. 静态资源 → Network-First（每次都拉新，后端重启后自动生效）──
  if (
    url.pathname.startsWith("/static/") ||
    url.pathname === "/sw.js" ||
    url.hostname === "cdnjs.cloudflare.com"
  ) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // 缓存一份副本备用
          if (response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // ── 4. 页面 HTML → Network-First ──
  if (request.destination === "document") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/"))
    );
    return;
  }

  // ── 5. 其他：直接走网络 ──
});
