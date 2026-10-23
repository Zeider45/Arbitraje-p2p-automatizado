/* Service worker mínimo: hace instalable la app y acelera la carga. Nunca guarda datos del panel (/api/*):
   lo que ves siempre viene del servidor. El servidor reemplaza __VERSION__ por una huella de los archivos de
   la web, así cada actualización limpia la caché vieja sola. */
const VERSION = "__VERSION__";
const CACHE = "arbitraje-" + VERSION;
const BASE = ["/static/estilos.css", "/static/panel.js", "/static/graficos.js", "/static/inter.woff2"];
const CAMBIA = /\.(js|css|html)$/;   // código de la web: red primero, para no mezclar un panel viejo con una API nueva

const SIN_CONEXION = `<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sin conexión</title><body style="font:16px system-ui;display:grid;place-items:center;min-height:100vh;margin:0;text-align:center;background:#0e0f12;color:#f2f3f5">
<main><h1 style="font-size:20px">Sin conexión</h1><p style="color:#a4a9b5">No llego a tu PC. Revisa que Tailscale esté conectado en el teléfono<br>y que el panel esté encendido en tu PC.</p>
<button onclick="location.reload()" style="font:inherit;padding:10px 20px;border:0;border-radius:999px;background:#2563eb;color:#fff">Reintentar</button></main></body></html>`;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(BASE)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((claves) => Promise.all(claves.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.open(CACHE).then(async (cache) => {
      const red = () => fetch(req).then((r) => { if (r.ok) cache.put(req, r.clone()); return r; });
      if (CAMBIA.test(url.pathname)) return red().catch(() => cache.match(req));   // red primero
      return (await cache.match(req)) || red();                                    // fuentes e iconos: caché primero
    }));
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => new Response(SIN_CONEXION, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
  }
  // el resto (/api/*) va siempre a la red, sin caché
});
