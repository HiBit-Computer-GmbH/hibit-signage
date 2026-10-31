const state = {
  user: null,
  queue: [],
  files: [],
  shares: [],
  system: null,
  browse: null,
  shareId: "",
  path: "",
  term: null,
  confirm: "",
};

const views = [
  ["queue", "Warteschlange"],
  ["media", "Medien"],
  ["shares", "Freigaben"],
  ["terminal", "Terminal"],
  ["system", "System"],
];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function sizeLabel(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${Math.round(value / 1024)} KB`;
  if (value < 1073741824) return `${(value / 1048576).toFixed(1)} MB`;
  return `${(value / 1073741824).toFixed(2)} GB`;
}

function originLabel(item) {
  if (item.origin === "url") return "Web";
  if (item.origin === "local") return "Lokal";
  return "Freigabe";
}

function cloudLabel(status) {
  return {
    disabled: "Nicht verbunden",
    connecting: "Verbinde",
    pending: "Wartet auf Adopt",
    adopted: "Adoptiert",
    offline: "Offline",
  }[status] || status || "Unbekannt";
}

function toast(message, bad) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.className = bad ? "toast bad" : "toast";
  el.textContent = message;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.remove(), 4200);
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = null;
    }
  }
  if (response.status === 401 && path !== "/api/login") {
    state.user = null;
    render();
    throw new Error("Anmeldung erforderlich");
  }
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : "Anfrage fehlgeschlagen");
  }
  return data;
}

function viewName() {
  const name = (location.hash || "#/queue").replace(/^#\/?/, "").split("/")[0];
  return views.some(([id]) => id === name) ? name : "queue";
}

function brand() {
  return '<div class="brand"><span class="mark">HiBit</span><span class="product">Signage</span></div>';
}

function layout(inner) {
  const current = viewName();
  return `<div class="shell">
    <aside class="side">
      ${brand()}
      <nav class="nav">
        ${views.map(([id, label]) => `<a href="#/${id}" class="${id === current ? "active" : ""}">${label}</a>`).join("")}
      </nav>
      <div class="muted" style="margin-top:auto">Angemeldet als ${esc(state.user.username)}</div>
      <button class="ghost" id="logout" type="button">Abmelden</button>
    </aside>
    <main class="main">${inner}</main>
  </div>`;
}

function loginHtml() {
  return `<div class="login-wrap"><form class="card login" id="login-form">
    ${brand()}
    <h1>Webkonsole</h1>
    <p class="muted">Wiedergabe, Dateien und Freigaben dieses Geräts.</p>
    <label>Benutzer<input name="username" autocomplete="username" value="admin" required></label>
    <label>Passwort<input name="password" type="password" autocomplete="current-password" required></label>
    <p class="error" id="login-error"></p>
    <button type="submit">Anmelden</button>
  </form></div>`;
}

function queueHtml() {
  const rows = state.queue.map((item, index) => `<article class="card item" draggable="true" data-id="${esc(item.id)}">
      <span class="handle" title="Ziehen">⋮⋮</span>
      <div>
        <div class="title">${esc(item.name)}</div>
        <div class="meta"><span class="badge">${esc(originLabel(item))}</span> <span class="badge">${item.kind === "video" ? "Video" : "Bild"}</span> ${item.enabled ? "" : '<span class="badge warn">Pausiert</span>'}</div>
      </div>
      <div class="row">
        <input data-field="duration" data-id="${esc(item.id)}" type="number" min="1" max="86400" placeholder="Ende" value="${item.duration_seconds ?? ""}" style="width:96px" title="Sekunden, bei Videos leer = bis zum Ende">
        <button class="ghost" data-action="up" data-id="${esc(item.id)}" ${index === 0 ? "disabled" : ""}>Hoch</button>
        <button class="ghost" data-action="down" data-id="${esc(item.id)}" ${index === state.queue.length - 1 ? "disabled" : ""}>Runter</button>
        <button class="ghost" data-action="toggle" data-id="${esc(item.id)}">${item.enabled ? "Pause" : "Aktiv"}</button>
        <button class="danger" data-action="delete" data-id="${esc(item.id)}">${state.confirm === item.id ? "Wirklich" : "Löschen"}</button>
      </div>
    </article>`).join("");
  return `<div class="page-head"><div><h1>Warteschlange</h1><p class="muted">Was auf dem Bildschirm nacheinander erscheint.</p></div><a class="button ghost" href="/player/" target="_blank">Vorschau</a></div>
    <form class="card composer" id="url-form">
      <div class="grid-2">
        <label>Name<input name="name" placeholder="Optional"></label>
        <label>Adresse<input name="url" placeholder="https://…/clip.mp4" required></label>
        <label>Typ<select name="kind"><option value="">Automatisch</option><option value="image">Bild</option><option value="video">Video</option></select></label>
        <label>Sekunden<input name="duration_seconds" type="number" min="1" value="10"></label>
      </div>
      <div class="row"><button type="submit">Zur Warteschlange</button><span class="muted">Bilder und Videos direkt aus dem Web.</span></div>
    </form>
    <div class="list" id="queue-list">${rows || '<div class="card panel muted">Noch keine Inhalte. Die Anzeige zeigt dann die IP der Webkonsole.</div>'}</div>`;
}

function mediaHtml() {
  const cards = state.files.map((file) => `<article class="card item">
      <div>
        <div class="title">${esc(file.name)}</div>
        <div class="meta">${file.kind === "video" ? "Video" : "Bild"} · ${sizeLabel(file.size)} · ${file.location === "cloud" ? "Cloud" : "Gerät"}</div>
      </div>
      <div class="row">
        <button data-action="enqueue" data-id="${esc(file.id)}">Abspielen</button>
        <button class="danger" data-action="delete-file" data-id="${esc(file.id)}">${state.confirm === file.id ? "Wirklich" : "Löschen"}</button>
      </div>
    </article>`).join("");
  return `<div class="page-head"><div><h1>Medien</h1><p class="muted">Dateien auf diesem Pi. MP4 und WebM laufen am zuverlässigsten.</p></div></div>
    <form class="card composer" id="upload-form">
      <label>Datei hochladen<input name="file" type="file" accept="image/*,video/*" required></label>
      <button type="submit">Hochladen</button>
    </form>
    <div class="list">${cards || '<div class="card panel muted">Noch keine lokalen Dateien.</div>'}</div>`;
}

function sharesHtml() {
  const options = state.shares.map((share) => `<option value="${esc(share.id)}" ${share.id === state.shareId ? "selected" : ""}>${esc(share.name)}</option>`).join("");
  const entries = (state.browse && state.browse.entries) || [];
  const listing = entries.map((entry) => `<tr>
      <td>${entry.dir ? "Ordner" : entry.kind === "video" ? "Video" : entry.kind === "image" ? "Bild" : "Datei"}</td>
      <td>${esc(entry.name)}</td>
      <td>${entry.dir ? "" : sizeLabel(entry.size)}</td>
      <td>${entry.dir ? `<button class="ghost" data-action="open-dir" data-path="${esc(entry.path)}">Öffnen</button>` : entry.kind ? `<button data-action="enqueue-share" data-path="${esc(entry.path)}" data-kind="${esc(entry.kind)}">Abspielen</button>` : ""}</td>
    </tr>`).join("");
  const rows = state.shares.map((share) => `<article class="card item">
      <div>
        <div class="title">${esc(share.name)}</div>
        <div class="meta">${share.protocol.toUpperCase()} · ${esc(share.server)} / ${esc(share.export_path)} ${share.mounted ? '<span class="badge ok">Eingehängt</span>' : '<span class="badge warn">Nicht eingehängt</span>'} ${share.error ? `<span class="badge bad">${esc(share.error)}</span>` : ""}</div>
      </div>
      <div class="row">
        <button class="ghost" data-action="browse" data-id="${esc(share.id)}">Durchsuchen</button>
        <button class="ghost" data-action="mount" data-id="${esc(share.id)}">Verbinden</button>
        <button class="danger" data-action="delete-share" data-id="${esc(share.id)}">${state.confirm === share.id ? "Wirklich" : "Löschen"}</button>
      </div>
    </article>`).join("");
  return `<div class="page-head"><div><h1>Freigaben</h1><p class="muted">SMB und NFS, nur lesend für die Wiedergabe.</p></div></div>
    <form class="card composer" id="share-form">
      <div class="grid-2">
        <label>Name<input name="name" required></label>
        <label>Protokoll<select name="protocol"><option value="smb">SMB</option><option value="nfs">NFS</option></select></label>
        <label>Server<input name="server" placeholder="192.168.1.10" required></label>
        <label>Freigabe<input name="export_path" placeholder="signage oder export/signage" required></label>
        <label>Benutzer<input name="username"></label>
        <label>Passwort<input name="password" type="password"></label>
        <label>Domäne<input name="domain" placeholder="WORKGROUP"></label>
      </div>
      <button type="submit">Freigabe speichern</button>
    </form>
    <div class="list">${rows || '<div class="card panel muted">Noch keine Freigabe.</div>'}</div>
    ${state.shareId ? `<section class="card panel">
      <div class="spread"><h2 style="margin:0">Inhalt</h2><div class="row"><button class="ghost" id="browse-up" type="button">Nach oben</button><span class="muted">${esc(state.path || "/")}</span></div></div>
      <table><thead><tr><th>Typ</th><th>Name</th><th>Größe</th><th></th></tr></thead><tbody>${listing || '<tr><td colspan="4" class="muted">Leer</td></tr>'}</tbody></table>
    </section>` : ""}`;
}

function systemHtml() {
  const info = state.system || {};
  const cloud = info.cloud || {};
  const ips = (info.ips || []).map((ip) => `<div class="title">${esc(ip)}:${info.port || ""}</div>`).join("") || '<div class="muted">Keine Adresse</div>';
  return `<div class="page-head"><div><h1>System</h1><p class="muted">Version ${esc(info.version || "")} ${info.commit ? "· " + esc(info.commit) : ""}</p></div></div>
    <div class="stats">
      <article class="card stat"><span class="muted">Webkonsole</span>${ips}</article>
      <article class="card stat"><span class="muted">Cloud</span><b style="font-size:1.15rem">${esc(cloudLabel(cloud.status))}</b><span class="meta">${esc(cloud.tenant || cloud.last_error || "")}</span></article>
      <article class="card stat"><span class="muted">Speicher frei</span><b>${sizeLabel(info.disk_free)}</b><span class="meta">von ${sizeLabel(info.disk_total)}</span></article>
    </div>
    <form class="card composer" id="cloud-form">
      <h2 style="margin:0">Cloud</h2>
      <label>Adresse<input name="url" value="${esc(cloud.url || "")}" placeholder="https://signage.example.com"></label>
      <label>Geräte-Schlüssel<input name="device_key" value="${esc(cloud.device_key || "")}" placeholder="HB-XXXX-XXXX-XXXX-XXXX"></label>
      <p class="muted">Leer lassen trennt die Cloud. Nach dem Speichern meldet sich das Gerät und wartet auf den Adopt.</p>
      <button type="submit">Cloud speichern</button>
    </form>
    <form class="card composer" id="password-form">
      <h2 style="margin:0">Passwort</h2>
      <div class="grid-2">
        <label>Aktuell<input name="current" type="password" required></label>
        <label>Neu<input name="next" type="password" minlength="8" required></label>
      </div>
      <button type="submit">Passwort ändern</button>
    </form>
    <section class="card panel">
      <div class="spread"><div><div class="title">Update aus GitHub</div><p class="muted">Zieht den aktuellen Stand und startet den Player neu.</p></div><button id="update" type="button">Update</button></div>
      <div class="row" style="margin-top:12px"><button class="ghost" id="restart-display" type="button">Anzeige neu starten</button></div>
      <pre class="log" id="update-log">${esc(state.log || "")}</pre>
    </section>`;
}

function render() {
  if (state.term) {
    state.term.close();
    state.term = null;
  }
  const app = document.getElementById("app");
  if (!state.user) {
    app.innerHTML = loginHtml();
    document.getElementById("login-form").addEventListener("submit", onLogin);
    return;
  }
  const current = viewName();
  const pages = { queue: queueHtml, media: mediaHtml, shares: sharesHtml, terminal: () => `<div class="page-head"><div><h1>Terminal</h1><p class="muted">Shell auf diesem Gerät. Es ist nur eine Sitzung gleichzeitig möglich.</p></div></div><div id="term"></div>`, system: systemHtml };
  app.innerHTML = layout(pages[current]());
  document.getElementById("logout").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: {} });
    state.user = null;
    render();
  });
  if (current === "queue") bindQueue();
  if (current === "media") bindMedia();
  if (current === "shares") bindShares();
  if (current === "terminal") state.term = window.HiBitTerm.mount(document.getElementById("term"), "/ws/shell");
  if (current === "system") bindSystem();
}

async function onLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    state.user = await api("/api/login", { method: "POST", body: { username: form.username.value, password: form.password.value } });
    location.hash = "#/queue";
    await show();
  } catch (error) {
    document.getElementById("login-error").textContent = error.message;
  }
}

async function load() {
  const current = viewName();
  if (current === "queue") state.queue = (await api("/api/queue")).items;
  if (current === "media") state.files = (await api("/api/media")).files;
  if (current === "shares") state.shares = (await api("/api/shares")).shares;
  if (current === "system") state.system = await api("/api/system");
}

async function show() {
  if (state.user) {
    try {
      await load();
    } catch (error) {
      toast(error.message, true);
    }
  }
  render();
}

function bindQueue() {
  document.getElementById("url-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/api/queue", {
        method: "POST",
        body: {
          origin: "url",
          name: form.name.value,
          url: form.url.value,
          kind: form.kind.value || null,
          duration_seconds: form.duration_seconds.value,
        },
      });
      form.reset();
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("queue-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const id = button.dataset.id;
    try {
      if (button.dataset.action === "delete") {
        if (state.confirm !== id) {
          state.confirm = id;
          render();
          return;
        }
        await api(`/api/queue/${id}`, { method: "DELETE" });
        state.confirm = "";
      }
      if (button.dataset.action === "toggle") {
        const item = state.queue.find((entry) => entry.id === id);
        await api(`/api/queue/${id}`, { method: "PATCH", body: { enabled: !item.enabled } });
      }
      if (button.dataset.action === "up" || button.dataset.action === "down") {
        const ids = state.queue.map((item) => item.id);
        const index = ids.indexOf(id);
        const swap = button.dataset.action === "up" ? index - 1 : index + 1;
        [ids[index], ids[swap]] = [ids[swap], ids[index]];
        await api("/api/queue/order", { method: "POST", body: { ids } });
      }
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("queue-list").addEventListener("change", async (event) => {
    const input = event.target.closest("[data-field='duration']");
    if (!input) return;
    try {
      await api(`/api/queue/${input.dataset.id}`, { method: "PATCH", body: { duration_seconds: input.value } });
      toast("Dauer gespeichert");
    } catch (error) {
      toast(error.message, true);
    }
  });
  let dragId = "";
  document.getElementById("queue-list").addEventListener("dragstart", (event) => {
    dragId = event.target.closest("[data-id]")?.dataset.id || "";
  });
  document.getElementById("queue-list").addEventListener("dragover", (event) => event.preventDefault());
  document.getElementById("queue-list").addEventListener("drop", async (event) => {
    const target = event.target.closest("[data-id]")?.dataset.id;
    if (!dragId || !target || dragId === target) return;
    const ids = state.queue.map((item) => item.id);
    const from = ids.indexOf(dragId);
    const to = ids.indexOf(target);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    try {
      await api("/api/queue/order", { method: "POST", body: { ids } });
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindMedia() {
  document.getElementById("upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = new FormData();
    body.append("file", form.file.files[0]);
    form.querySelector("button").disabled = true;
    try {
      await api("/api/media/upload", { method: "POST", body });
      toast("Datei gespeichert");
      await show();
    } catch (error) {
      toast(error.message, true);
      form.querySelector("button").disabled = false;
    }
  });
  document.querySelector(".list").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.dataset.action === "enqueue") {
        await api("/api/queue", { method: "POST", body: { origin: "local", file_id: button.dataset.id, duration_seconds: 10 } });
        toast("Zur Warteschlange hinzugefügt");
      }
      if (button.dataset.action === "delete-file") {
        if (state.confirm !== button.dataset.id) {
          state.confirm = button.dataset.id;
          render();
          return;
        }
        await api(`/api/media/${button.dataset.id}`, { method: "DELETE" });
        state.confirm = "";
      }
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function browse(shareId, path) {
  state.shareId = shareId;
  state.path = path || "";
  state.browse = await api(`/api/shares/${shareId}/browse?path=${encodeURIComponent(state.path)}`);
  render();
}

function bindShares() {
  document.getElementById("share-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      await api("/api/shares", { method: "POST", body });
      toast("Freigabe gespeichert");
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector(".list").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.dataset.action === "browse") await browse(button.dataset.id, "");
      if (button.dataset.action === "mount") {
        await api(`/api/shares/${button.dataset.id}/mount`, { method: "POST", body: {} });
        toast("Freigabe verbunden");
        await show();
      }
      if (button.dataset.action === "delete-share") {
        if (state.confirm !== button.dataset.id) {
          state.confirm = button.dataset.id;
          render();
          return;
        }
        await api(`/api/shares/${button.dataset.id}`, { method: "DELETE" });
        state.confirm = "";
        if (state.shareId === button.dataset.id) state.shareId = "";
        await show();
      }
    } catch (error) {
      toast(error.message, true);
    }
  });
  const panel = document.querySelector("section.panel");
  if (!panel) return;
  panel.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.id === "browse-up") {
        const parent = state.path.split("/").slice(0, -1).join("/");
        await browse(state.shareId, parent);
      }
      if (button.dataset.action === "open-dir") await browse(state.shareId, button.dataset.path);
      if (button.dataset.action === "enqueue-share") {
        await api("/api/queue", {
          method: "POST",
          body: { origin: "share", share_id: state.shareId, share_path: button.dataset.path, kind: button.dataset.kind, duration_seconds: button.dataset.kind === "image" ? 10 : "" },
        });
        toast("Zur Warteschlange hinzugefügt");
      }
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindSystem() {
  document.getElementById("cloud-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/api/system/cloud", { method: "POST", body: { url: form.url.value, device_key: form.device_key.value } });
      toast("Cloud-Einstellungen gespeichert");
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/api/me/password", { method: "POST", body: { current: form.current.value, next: form.next.value } });
      form.reset();
      toast("Passwort geändert");
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("update").addEventListener("click", async () => {
    if (!confirm("Update aus dem Git-Repository starten und den Player neu starten?")) return;
    try {
      const result = await api("/api/system/update", { method: "POST", body: {} });
      toast(result.message);
      pollLog();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("restart-display").addEventListener("click", async () => {
    try {
      await api("/api/system/restart-display", { method: "POST", body: {} });
      toast("Anzeige wird neu gestartet");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function pollLog() {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try {
      const data = await api("/api/system/update-log");
      const el = document.getElementById("update-log");
      if (el) el.textContent = data.log || "";
    } catch (error) {
      break;
    }
  }
}

window.addEventListener("hashchange", () => show());

api("/api/me").then((user) => {
  state.user = user;
  return show();
}).catch(() => render());
