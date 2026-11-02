self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/player/site/")) return;
  const referrer = event.request.referrer || "";
  if (!referrer) return;
  let ref;
  try {
    ref = new URL(referrer);
  } catch (error) {
    return;
  }
  if (ref.origin !== url.origin) return;
  const match = ref.pathname.match(/^\/player\/site\/(https?)\/([^/]+)/);
  if (!match) return;
  const target = `${url.origin}/player/site/${match[1]}/${match[2]}${url.pathname}${url.search}`;
  if (event.request.mode === "navigate") {
    event.respondWith(Response.redirect(target, 302));
    return;
  }
  const init = {
    method: event.request.method,
    headers: event.request.headers,
    redirect: "follow",
    credentials: "same-origin",
  };
  if (event.request.method !== "GET" && event.request.method !== "HEAD") {
    init.body = event.request.body;
    init.duplex = "half";
  }
  event.respondWith(fetch(target, init));
});
