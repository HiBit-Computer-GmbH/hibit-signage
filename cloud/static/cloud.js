const state = {
  user: null,
  dashboard: null,
  devices: [],
  device: null,
  playback: null,
  tenants: [],
  users: [],
  keys: [],
  system: null,
  browse: null,
  path: "",
  shareId: "",
  term: null,
  confirm: "",
  poweroff: false,
  addMode: "image-url",
  log: "",
};

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

function kindLabel(kind) {
  return { image: "Bild", video: "Video", web: "Webseite" }[kind] || "Inhalt";
}

function roleLabel(role) {
  return { superadmin: "Cloud-Admin", tenant_admin: "Mandanten-Admin", tenant_user: "Mandanten-Benutzer" }[role] || role;
}

function when(value) {
  if (!value) return "noch nie";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("de-DE");
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
  toast.timer = setTimeout(() => el.remove(), 4500);
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

function route() {
  const parts = (location.hash || "#/dashboard").replace(/^#\/?/, "").split("/").filter(Boolean);
  return { name: parts[0] || "dashboard", id: parts[1] || "", tab: parts[2] || "queue" };
}

function isAdmin() {
  return state.user && state.user.role === "superadmin";
}

function brand() {
  return '<div class="brand"><span class="mark">HiBit</span><span class="product">Cloud</span></div>';
}

function nav() {
  const current = route().name;
  const items = [["dashboard", "Übersicht"], ["devices", "Geräte"]];
  if (isAdmin()) items.push(["tenants", "Mandanten"], ["keys", "Schlüssel"]);
  if (state.user.role !== "tenant_user") items.push(["users", "Benutzer"]);
  if (isAdmin()) items.push(["system", "System"]);
  return items.map(([id, label]) => `<a href="#/${id}" class="${current === id || (current === "device" && id === "devices") ? "active" : ""}">${label}</a>`).join("");
}

function layout(inner) {
  return `<div class="shell"><aside class="side">${brand()}<nav class="nav">${nav()}</nav>
    <div class="side-foot"><div class="muted">${esc(state.user.name)}<br>${esc(roleLabel(state.user.role))}${state.user.tenant_name ? "<br>" + esc(state.user.tenant_name) : ""}</div>
    <button class="ghost" id="logout" type="button">Abmelden</button></div></aside><main class="main">${inner}</main></div>`;
}

function loginHtml() {
  return `<div class="login-wrap"><form class="card login" id="login-form">${brand()}
    <h1>Cloud-Konsole</h1>
    <p class="muted">Geräte adoptieren, Mandanten zuweisen und Wiedergabe steuern.</p>
    <label>E-Mail<input name="email" type="email" autocomplete="username" required></label>
    <label>Passwort<input name="password" type="password" autocomplete="current-password" required></label>
    <p class="error" id="login-error"></p>
    <button type="submit">Anmelden</button></form></div>`;
}

function statusBadge(device) {
  if (device.status === "pending") return '<span class="badge warn">Wartet auf Adopt</span>';
  if (device.online) return '<span class="badge ok">Online</span>';
  return '<span class="badge bad">Offline</span>';
}

function dashboardHtml() {
  const data = state.dashboard || { adopted: 0, online: 0, pending: 0, tenants: 0, devices: [] };
  const rows = (data.devices || []).map((device) => `<a class="card item" href="#/device/${device.id}"><div><div class="title">${esc(device.name || device.hostname || "Gerät")}</div><div class="meta">${esc(device.tenant_name || "ohne Mandant")} · ${esc((device.ips || []).join(", ") || "keine IP")}</div></div><div>${statusBadge(device)}</div></a>`).join("");
  return `<div class="page-head"><div><h1>Übersicht</h1><p class="muted">Geräte melden sich selbst und bleiben verbunden.</p></div></div>
    <div class="stats">
      <article class="card stat"><span class="muted">Adoptiert</span><b>${data.adopted}</b></article>
      <article class="card stat"><span class="muted">Online</span><b>${data.online}</b></article>
      <article class="card stat"><span class="muted">Warten</span><b>${data.pending}</b></article>
      <article class="card stat"><span class="muted">Mandanten</span><b>${data.tenants}</b></article>
    </div>
    <div class="list">${rows || '<div class="card panel muted">Noch keine Geräte. Lege einen Einschreibeschlüssel an und installiere einen Player.</div>'}</div>`;
}

function tenantOptions(selected) {
  return state.tenants.map((tenant) => `<option value="${esc(tenant.id)}" ${tenant.id === selected ? "selected" : ""}>${esc(tenant.name)}</option>`).join("");
}

function devicesHtml() {
  const pending = state.devices.filter((device) => device.status === "pending");
  const adopted = state.devices.filter((device) => device.status !== "pending");
  const pendingCards = pending.map((device) => `<article class="card item pending">
      <div><div class="title">${esc(device.hostname || "Neues Gerät")}</div>
      <div class="meta">${esc((device.ips || []).join(", "))} · ${esc(device.version || "")} · gesehen ${esc(when(device.last_seen))}</div></div>
      <a class="button" href="#/device/${device.id}">Adoptieren</a>
    </article>`).join("");
  const rows = adopted.map((device) => `<a class="card item" href="#/device/${device.id}"><div><div class="title">${esc(device.name)}</div><div class="meta">${esc(device.tenant_name || "")} · ${esc((device.ips || []).join(", "))}</div></div><div>${statusBadge(device)}</div></a>`).join("");
  return `<div class="page-head"><div><h1>Geräte</h1><p class="muted">Ausstehende Geräte brauchen einen Adopt, danach gehören sie zu einem Mandanten.</p></div><button class="ghost" id="reload" type="button">Aktualisieren</button></div>
    ${pendingCards ? `<h2>Warten auf Adopt</h2><div class="list">${pendingCards}</div>` : ""}
    <h2>Im Betrieb</h2><div class="list">${rows || '<div class="card panel muted">Keine adoptierten Geräte.</div>'}</div>`;
}

function deviceHtml() {
  const device = state.device;
  const tab = route().tab;
  if (!device) return '<p class="muted">Gerät nicht gefunden.</p>';
  if (device.status === "pending") {
    return `<div class="page-head"><div><h1>${esc(device.hostname || "Neues Gerät")}</h1><p class="muted">${esc((device.ips || []).join(", "))} · ${statusBadge(device)}</p></div></div>
      <form class="card composer" id="adopt-form">
        <h2>Adoptieren</h2>
        <p class="muted">Wie bei einer UniFi-Konsole wird das Gerät erst nach dem Adopt verwaltet.</p>
        <label>Name<input name="name" value="${esc(device.hostname || "Signage")}" required></label>
        ${isAdmin() ? `<label>Mandant<select name="tenant_id" required><option value="">Bitte wählen</option>${tenantOptions(device.tenant_id)}</select></label>` : `<p>Mandant: ${esc(device.tenant_name || "")}</p>`}
        <button type="submit">Adoptieren</button>
      </form>`;
  }
  const tabs = [["queue", "Warteschlange"], ["media", "Medien"], ["shares", "Freigaben"], ["terminal", "Terminal"], ["settings", "Gerät"]];
  return `<div class="page-head"><div><h1>${esc(device.name)}</h1><p class="muted">${esc(device.tenant_name || "")} · ${statusBadge(device)} · Rev. ${device.state_revision}</p></div></div>
    <div class="tabs">${tabs.map(([id, label]) => `<a class="${tab === id ? "active" : ""}" href="#/device/${device.id}/${id}">${label}</a>`).join("")}</div>
    ${tab === "media" ? mediaTab() : tab === "shares" ? sharesTab() : tab === "terminal" ? '<section class="term-frame"><div class="term-bar"><span>Shell auf dem Pi</span><span>Strg+C bricht ab</span></div><div id="term"></div></section>' : tab === "settings" ? settingsTab() : queueTab()}`;
}

function fitSelect(item) {
  if (item.kind === "web") return "";
  const contain = item.fit !== "cover";
  return `<select data-field="fit" data-id="${esc(item.id)}" title="Darstellung">
    <option value="contain" ${contain ? "selected" : ""}>An Auflösung anpassen</option>
    <option value="cover" ${contain ? "" : "selected"}>Ausfüllen</option>
  </select>`;
}

function enqueueFields(files) {
  const mode = state.addMode || "image-url";
  if (mode === "local") {
    const options = files.map((file) => `<option value="${esc(file.id)}">${esc(file.name)}</option>`).join("");
    if (!options) return `<p class="muted">Noch keine Datei. Zuerst unter <a href="#/device/${deviceId()}/media">Medien</a> hochladen.</p>`;
    return `<div class="grid-2">
      <label>Datei<select name="file_id" required>${options}</select></label>
      <label>Sekunden<input name="duration_seconds" type="number" min="1" value="10"></label>
    </div>
    <label class="check"><input type="checkbox" name="fit_screen" checked> An Auflösung anpassen</label>`;
  }
  if (mode === "web") {
    return `<div class="grid-2">
      <label>Name<input name="name" placeholder="Optional"></label>
      <label>Adresse<input name="url" type="url" placeholder="https://example.com" required></label>
      <label>Sekunden<input name="duration_seconds" type="number" min="1" value="60" required></label>
    </div>
    <p class="muted">Seiten, die eine Einbettung verbieten, bleiben leer.</p>`;
  }
  const image = mode === "image-url";
  return `<div class="grid-2">
    <label>Name<input name="name" placeholder="Optional"></label>
    <label>Adresse<input name="url" type="url" placeholder="${image ? "https://…/bild.jpg" : "https://…/clip.mp4"}" required></label>
    <label>Sekunden<input name="duration_seconds" type="number" min="1" value="${image ? "10" : ""}" placeholder="${image ? "" : "bis zum Ende"}"></label>
  </div>
  <label class="check"><input type="checkbox" name="fit_screen" ${image ? "checked" : ""}> An Auflösung anpassen</label>
  <p class="muted">Das ganze Bild bleibt sichtbar. Ohne Haken füllt es den Bildschirm und wird beschnitten.</p>`;
}

function queueTab() {
  const items = (state.playback && state.playback.queue) || [];
  const files = (state.playback && state.playback.files) || [];
  const mode = state.addMode || "image-url";
  const modes = [["image-url", "Bild-Adresse"], ["video-url", "Video-Adresse"], ["web", "Webseite"], ["local", "Lokale Datei"]];
  const rows = items.map((item, index) => `<article class="card item"><div><div class="title">${esc(item.name)}</div><div class="meta"><span class="badge">${item.kind === "web" ? "Webseite" : item.origin === "url" ? "Adresse" : item.origin === "share" ? "Freigabe" : "Datei"}</span> <span class="badge">${esc(kindLabel(item.kind))}</span> ${item.kind === "web" ? "" : `<span class="badge">${item.fit === "cover" ? "Beschnitten" : "Ganzes Bild"}</span>`} ${item.enabled ? "" : '<span class="badge warn">Pausiert</span>'}</div></div>
    <div class="row">
      ${fitSelect(item)}
      <input data-duration="${esc(item.id)}" type="number" min="1" placeholder="Ende" value="${item.duration_seconds ?? ""}" title="Sekunden">
      <button class="ghost" data-action="up" data-id="${esc(item.id)}" ${index === 0 ? "disabled" : ""}>Hoch</button>
      <button class="ghost" data-action="down" data-id="${esc(item.id)}" ${index === items.length - 1 ? "disabled" : ""}>Runter</button>
      <button class="ghost" data-action="toggle" data-id="${esc(item.id)}">${item.enabled ? "Pause" : "Aktiv"}</button>
      <button class="danger" data-action="delete" data-id="${esc(item.id)}">Löschen</button>
    </div></article>`).join("");
  return `<form class="card composer" id="enqueue-form">
      <div class="mode-menu">${modes.map(([id, label]) => `<button type="button" data-mode="${id}" class="${mode === id ? "active" : ""}">${label}</button>`).join("")}</div>
      ${enqueueFields(files)}
      ${mode === "local" && !files.length ? "" : `<button type="submit">In die Warteschlange</button>`}
    </form>
    <div class="list" id="queue-list">${rows || '<div class="card panel muted">Die Warteschlange ist leer. Der Bildschirm zeigt die IP.</div>'}</div>`;
}

function mediaTab() {
  const files = (state.playback && state.playback.files) || [];
  const rows = files.map((file) => `<article class="card item"><div><div class="title">${esc(file.name)}</div><div class="meta">${file.kind === "video" ? "Video" : "Bild"} · ${sizeLabel(file.size)} · ${file.location === "cloud" ? "Cloud" : "auf dem Gerät"}</div></div>
    <div class="row">${file.location === "cloud" ? `<a class="button ghost" href="/api/devices/${state.device.id}/files/${file.id}/raw" target="_blank">Ansehen</a>` : ""}<button class="danger" data-action="delete-file" data-id="${esc(file.id)}">Löschen</button></div></article>`).join("");
  return `<form class="card composer" id="upload-form"><label>Datei für dieses Gerät<input type="file" name="file" required></label><button type="submit">Hochladen</button><p class="muted">Der Pi lädt die Datei selbst. Einreihen geht in der Warteschlange unter Lokale Datei.</p></form><div class="list">${rows || '<div class="card panel muted">Keine Dateien.</div>'}</div>`;
}

function sharesTab() {
  const shares = (state.playback && state.playback.shares) || [];
  const runtime = (state.device.runtime && state.device.runtime.mounts) || {};
  const rows = shares.map((share) => {
    const info = runtime[share.id] || {};
    return `<article class="card item"><div><div class="title">${esc(share.name)}</div><div class="meta">${share.protocol.toUpperCase()} · ${esc(share.server)} / ${esc(share.export_path)} ${info.mounted ? '<span class="badge ok">Eingehängt</span>' : '<span class="badge warn">Prüfen</span>'} ${info.error ? `<span class="badge bad">${esc(info.error)}</span>` : ""}</div></div>
      <div class="row"><button class="ghost" data-action="browse" data-id="${esc(share.id)}">Durchsuchen</button><button class="danger" data-action="delete-share" data-id="${esc(share.id)}">Löschen</button></div></article>`;
  }).join("");
  const entries = (state.browse && state.browse.entries) || [];
  const listing = entries.map((entry) => `<tr><td>${entry.dir ? "Ordner" : esc(entry.kind || "Datei")}</td><td>${esc(entry.name)}</td><td>${entry.dir ? `<button class="ghost" data-action="open-dir" data-path="${esc(entry.path)}">Öffnen</button>` : entry.kind ? `<button data-action="enqueue-share" data-path="${esc(entry.path)}" data-kind="${esc(entry.kind)}">Abspielen</button>` : ""}</td></tr>`).join("");
  return `<form class="card composer" id="share-form"><div class="grid-2">
      <label>Name<input name="name" required></label>
      <label>Protokoll<select name="protocol"><option value="smb">SMB</option><option value="nfs">NFS</option></select></label>
      <label>Server<input name="server" required></label>
      <label>Freigabe<input name="export_path" required></label>
      <label>Benutzer<input name="username"></label>
      <label>Passwort<input name="password" type="password"></label>
      <label>Domäne<input name="domain"></label>
    </div><button type="submit">Freigabe speichern</button></form>
    <div class="list">${rows || '<div class="card panel muted">Keine Freigabe.</div>'}</div>
    ${state.shareId ? `<section class="card panel"><div class="spread"><strong>${esc(state.path || "/")}</strong><button class="ghost" id="browse-up" type="button">Nach oben</button></div><table><tbody>${listing || '<tr><td class="muted">Leer oder nicht erreichbar</td></tr>'}</tbody></table></section>` : ""}`;
}

function settingsTab() {
  const device = state.device;
  const power = `<section class="card panel" id="power-panel">
      <h2>Strom</h2>
      <p class="muted">Neustart startet den Pi neu. Herunterfahren schaltet ihn aus, bis jemand ihn wieder einschaltet.</p>
      <div class="row">
        <button class="ghost" id="reboot" type="button">Neustart</button>
        <button class="danger" id="poweroff" type="button">Herunterfahren</button>
      </div>
      ${state.poweroff ? `<div class="confirm-box"><p>Zweite Bestätigung: der Pi wird jetzt ausgeschaltet.</p><div class="row"><button class="danger" id="poweroff-now" type="button">Wirklich herunterfahren</button><button class="ghost" id="poweroff-cancel" type="button">Abbrechen</button></div></div>` : ""}
    </section>`;
  return `${power}<form class="card composer" id="rename-form"><label>Name<input name="name" value="${esc(device.name)}" required></label><button type="submit">Speichern</button></form>
    ${isAdmin() ? `<form class="card composer" id="move-form"><label>Mandant wechseln<select name="tenant_id">${tenantOptions(device.tenant_id)}</select></label><button class="ghost" type="submit">Verschieben</button></form>
    <section class="card panel"><div class="spread"><div><div class="title">Geräte-Update</div><p class="muted">Der Pi zieht das GitHub-Repository und startet neu.</p></div><button id="device-update" type="button">Update</button></div></section>
    <button class="danger" id="forget" type="button">${state.confirm === "forget" ? "Wirklich entfernen" : "Gerät entfernen"}</button>` : `<p class="muted">Updates startet nur ein Cloud-Admin.</p>`}
    <p class="meta">Hardware ${esc(device.hardware_id)} · zuletzt ${esc(when(device.last_seen))}</p>`;
}

function tenantsHtml() {
  const rows = state.tenants.map((tenant) => `<tr><td>${esc(tenant.name)}</td><td>${tenant.devices}</td><td>${tenant.users}</td><td><button class="danger" data-action="delete-tenant" data-id="${esc(tenant.id)}">Löschen</button></td></tr>`).join("");
  return `<div class="page-head"><div><h1>Mandanten</h1><p class="muted">Jedes Gerät gehört nach dem Adopt genau zu einem Mandanten.</p></div></div>
    <form class="card composer" id="tenant-form"><label>Name<input name="name" required></label><button type="submit">Mandant anlegen</button></form>
    <table><thead><tr><th>Name</th><th>Geräte</th><th>Benutzer</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">Noch keine Mandanten.</td></tr>'}</tbody></table>`;
}

function usersHtml() {
  const rows = state.users.map((user) => `<tr><td>${esc(user.name)}</td><td>${esc(user.email)}</td><td>${esc(roleLabel(user.role))}</td><td>${esc(user.tenant_name || "–")}</td><td>${user.id === state.user.id ? "" : `<button class="danger" data-action="delete-user" data-id="${esc(user.id)}">Löschen</button>`}</td></tr>`).join("");
  const roles = isAdmin()
    ? '<option value="superadmin">Cloud-Admin</option><option value="tenant_admin">Mandanten-Admin</option><option value="tenant_user">Mandanten-Benutzer</option>'
    : '<option value="tenant_admin">Mandanten-Admin</option><option value="tenant_user">Mandanten-Benutzer</option>';
  return `<div class="page-head"><div><h1>Benutzer</h1><p class="muted">Mandanten sehen nur ihre eigenen Geräte.</p></div></div>
    <form class="card composer" id="user-form"><div class="grid-2">
      <label>Name<input name="name" required></label>
      <label>E-Mail<input name="email" type="email" required></label>
      <label>Passwort<input name="password" type="password" minlength="8" required></label>
      <label>Rolle<select name="role">${roles}</select></label>
      ${isAdmin() ? `<label>Mandant<select name="tenant_id"><option value="">Kein Mandant</option>${tenantOptions("")}</select></label>` : ""}
    </div><button type="submit">Benutzer anlegen</button></form>
    <table><thead><tr><th>Name</th><th>E-Mail</th><th>Rolle</th><th>Mandant</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

function keysHtml() {
  const rows = state.keys.map((key) => `<tr><td>${esc(key.label)}</td><td><code>${esc(key.key)}</code></td><td>${esc(key.tenant_name || "beim Adopt wählen")}</td><td>${key.uses}${key.max_uses ? " / " + key.max_uses : ""}</td><td>${key.revoked ? "gesperrt" : "aktiv"}</td><td>${key.revoked ? "" : `<button class="ghost" data-action="revoke" data-id="${esc(key.id)}">Sperren</button>`}</td></tr>`).join("");
  return `<div class="page-head"><div><h1>Einschreibeschlüssel</h1><p class="muted">Diesen Schlüssel bekommt die Installation auf dem Pi.</p></div></div>
    <form class="card composer" id="key-form"><div class="grid-3">
      <label>Bezeichnung<input name="label" required></label>
      <label>Mandant vorauswählen<select name="tenant_id"><option value="">Erst beim Adopt</option>${tenantOptions("")}</select></label>
      <label>Max. Geräte<input name="max_uses" type="number" min="1" placeholder="unbegrenzt"></label>
      <button type="submit">Erzeugen</button>
    </div></form>
    <table><thead><tr><th>Name</th><th>Schlüssel</th><th>Mandant</th><th>Nutzung</th><th>Status</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="muted">Noch kein Schlüssel.</td></tr>'}</tbody></table>`;
}

function systemHtml() {
  const info = state.system || {};
  return `<div class="page-head"><div><h1>Cloud-System</h1><p class="muted">Version ${esc(info.version || "")} ${info.commit ? "· " + esc(info.commit) : ""}</p></div></div>
    <article class="card stat" style="margin-bottom:14px"><span class="muted">Speicher frei</span><b>${sizeLabel(info.disk_free)}</b></article>
    <section class="card panel"><div class="spread"><div><div class="title">Cloud aus GitHub updaten</div><p class="muted">Nur für Cloud-Admins. Mandanten sehen diesen Schalter nicht.</p></div><button id="cloud-update" type="button">Update</button></div>
    <pre class="log">${esc(state.log || "")}</pre></section>
    <form class="card composer" id="password-form"><h2>Eigenes Passwort</h2><div class="grid-2"><label>Aktuell<input name="current" type="password" required></label><label>Neu<input name="next" type="password" minlength="8" required></label></div><button type="submit">Ändern</button></form>`;
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
  const current = route();
  if (!(current.name === "device" && current.tab === "settings")) state.poweroff = false;
  const pages = { dashboard: dashboardHtml, devices: devicesHtml, device: deviceHtml, tenants: tenantsHtml, users: usersHtml, keys: keysHtml, system: systemHtml };
  const page = pages[current.name] || dashboardHtml;
  app.innerHTML = layout(page());
  document.getElementById("logout").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: {} });
    state.user = null;
    render();
  });
  bind(current);
}

async function onLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    state.user = await api("/api/login", { method: "POST", body: { email: form.email.value, password: form.password.value } });
    location.hash = "#/dashboard";
    await show();
  } catch (error) {
    document.getElementById("login-error").textContent = error.message;
  }
}

async function load() {
  const current = route();
  if (current.name === "dashboard") state.dashboard = await api("/api/dashboard");
  if (current.name === "devices" || current.name === "device") state.devices = (await api("/api/devices")).devices;
  if (current.name === "device") {
    const data = await api(`/api/devices/${current.id}`);
    state.device = data.device;
    state.playback = data.state;
  }
  if (["device", "tenants", "users", "keys"].includes(current.name) || (current.name === "device" && state.device && state.device.status === "pending")) {
    if (isAdmin() || current.name === "device") state.tenants = (await api("/api/tenants")).tenants;
  }
  if (current.name === "tenants" && isAdmin()) state.tenants = (await api("/api/tenants")).tenants;
  if (current.name === "users") {
    state.users = (await api("/api/users")).users;
    state.tenants = (await api("/api/tenants")).tenants;
  }
  if (current.name === "keys") {
    state.keys = (await api("/api/keys")).keys;
    state.tenants = (await api("/api/tenants")).tenants;
  }
  if (current.name === "system") state.system = await api("/api/system");
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

function deviceId() {
  return route().id;
}

function bind(current) {
  const reload = document.getElementById("reload");
  if (reload) reload.addEventListener("click", () => show());
  if (current.name === "device" && state.device && state.device.status === "pending") {
    document.getElementById("adopt-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        await api(`/api/devices/${deviceId()}/adopt`, { method: "POST", body: { name: form.name.value, tenant_id: form.tenant_id ? form.tenant_id.value : null } });
        toast("Gerät adoptiert");
        location.hash = `#/device/${deviceId()}/queue`;
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  if (current.name === "device" && state.device && state.device.status === "adopted") {
    if (current.tab === "queue" || !current.tab) bindQueue();
    if (current.tab === "media") bindMedia();
    if (current.tab === "shares") bindShares();
    if (current.tab === "terminal") state.term = window.HiBitTerm.mount(document.getElementById("term"), `/ws/devices/${deviceId()}/shell`);
    if (current.tab === "settings") bindSettings();
  }
  if (current.name === "tenants") bindTenants();
  if (current.name === "users") bindUsers();
  if (current.name === "keys") bindKeys();
  if (current.name === "system") bindSystem();
}

function bindQueue() {
  const form = document.getElementById("enqueue-form");
  if (!form) return;
  form.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    state.addMode = button.dataset.mode;
    render();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = state.addMode || "image-url";
    const fit = form.fit_screen && form.fit_screen.checked ? "contain" : "cover";
    let body;
    if (mode === "local") {
      body = { origin: "local", file_id: form.file_id.value, duration_seconds: form.duration_seconds.value, fit };
    } else if (mode === "web") {
      body = { origin: "url", kind: "web", name: form.name.value, url: form.url.value, duration_seconds: form.duration_seconds.value };
    } else {
      body = {
        origin: "url",
        kind: mode === "video-url" ? "video" : "image",
        name: form.name.value,
        url: form.url.value,
        duration_seconds: form.duration_seconds.value,
        fit,
      };
    }
    try {
      await api(`/api/devices/${deviceId()}/queue`, { method: "POST", body });
      toast("In die Warteschlange gelegt");
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("queue-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const id = button.dataset.id;
    const items = state.playback.queue;
    try {
      if (button.dataset.action === "delete") await api(`/api/devices/${deviceId()}/queue/${id}`, { method: "DELETE" });
      if (button.dataset.action === "toggle") {
        const item = items.find((entry) => entry.id === id);
        await api(`/api/devices/${deviceId()}/queue/${id}`, { method: "PATCH", body: { enabled: !item.enabled } });
      }
      if (button.dataset.action === "up" || button.dataset.action === "down") {
        const ids = items.map((item) => item.id);
        const index = ids.indexOf(id);
        const swap = button.dataset.action === "up" ? index - 1 : index + 1;
        [ids[index], ids[swap]] = [ids[swap], ids[index]];
        await api(`/api/devices/${deviceId()}/queue/order`, { method: "POST", body: { ids } });
      }
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("queue-list").addEventListener("change", async (event) => {
    const input = event.target.closest("[data-duration]");
    const fit = event.target.closest("[data-field='fit']");
    try {
      if (input) {
        await api(`/api/devices/${deviceId()}/queue/${input.dataset.duration}`, { method: "PATCH", body: { duration_seconds: input.value } });
        toast("Dauer gespeichert");
      }
      if (fit) {
        await api(`/api/devices/${deviceId()}/queue/${fit.dataset.id}`, { method: "PATCH", body: { fit: fit.value } });
        toast(fit.value === "contain" ? "Bild wird vollständig gezeigt" : "Bild füllt den Bildschirm");
        await show();
      }
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
      await api(`/api/devices/${deviceId()}/files`, { method: "POST", body });
      toast("Datei liegt in der Cloud und wird vom Gerät geholt");
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
      if (button.dataset.action === "delete-file") await api(`/api/devices/${deviceId()}/files/${button.dataset.id}`, { method: "DELETE" });
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindShares() {
  document.getElementById("share-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api(`/api/devices/${deviceId()}/shares`, { method: "POST", body: Object.fromEntries(new FormData(form).entries()) });
      toast("Freigabe gespeichert");
      state.shareId = "";
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  const list = document.querySelector(".list");
  list.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.dataset.action === "delete-share") {
        await api(`/api/devices/${deviceId()}/shares/${button.dataset.id}`, { method: "DELETE" });
        state.shareId = "";
        await show();
      }
      if (button.dataset.action === "browse") {
        state.shareId = button.dataset.id;
        state.path = "";
        state.browse = await api(`/api/devices/${deviceId()}/shares/${state.shareId}/browse`);
        render();
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
        state.path = state.path.split("/").slice(0, -1).join("/");
        state.browse = await api(`/api/devices/${deviceId()}/shares/${state.shareId}/browse?path=${encodeURIComponent(state.path)}`);
        render();
      }
      if (button.dataset.action === "open-dir") {
        state.path = button.dataset.path;
        state.browse = await api(`/api/devices/${deviceId()}/shares/${state.shareId}/browse?path=${encodeURIComponent(state.path)}`);
        render();
      }
      if (button.dataset.action === "enqueue-share") {
        await api(`/api/devices/${deviceId()}/queue`, { method: "POST", body: { origin: "share", share_id: state.shareId, share_path: button.dataset.path, kind: button.dataset.kind, duration_seconds: button.dataset.kind === "image" ? 10 : "", fit: button.dataset.kind === "image" ? "contain" : "cover" } });
        toast("Zur Warteschlange hinzugefügt");
      }
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindSettings() {
  document.getElementById("rename-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api(`/api/devices/${deviceId()}`, { method: "PATCH", body: { name: event.currentTarget.name.value } });
      toast("Name gespeichert");
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  const move = document.getElementById("move-form");
  if (move) {
    move.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api(`/api/devices/${deviceId()}/move`, { method: "POST", body: { tenant_id: move.tenant_id.value } });
        toast("Mandant geändert");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const update = document.getElementById("device-update");
  if (update) {
    update.addEventListener("click", async () => {
      if (!confirm("Update auf dem Gerät starten?")) return;
      try {
        const result = await api(`/api/devices/${deviceId()}/update`, { method: "POST", body: {} });
        toast(result.message || "Update gestartet");
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const reboot = document.getElementById("reboot");
  if (reboot) {
    reboot.addEventListener("click", async () => {
      if (!confirm("Den Pi neu starten?")) return;
      try {
        const result = await api(`/api/devices/${deviceId()}/power`, { method: "POST", body: { action: "reboot" } });
        toast(result.message || "Neustart wurde gestartet");
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const poweroff = document.getElementById("poweroff");
  if (poweroff) {
    poweroff.addEventListener("click", () => {
      state.poweroff = true;
      render();
    });
  }
  const poweroffNow = document.getElementById("poweroff-now");
  if (poweroffNow) {
    poweroffNow.addEventListener("click", async () => {
      try {
        const result = await api(`/api/devices/${deviceId()}/power`, { method: "POST", body: { action: "poweroff", confirm: "poweroff" } });
        state.poweroff = false;
        toast(result.message || "Das Gerät fährt herunter");
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const poweroffCancel = document.getElementById("poweroff-cancel");
  if (poweroffCancel) {
    poweroffCancel.addEventListener("click", () => {
      state.poweroff = false;
      render();
    });
  }
  const forget = document.getElementById("forget");
  if (forget) {
    forget.addEventListener("click", async () => {
      if (state.confirm !== "forget") {
        state.confirm = "forget";
        render();
        return;
      }
      await api(`/api/devices/${deviceId()}`, { method: "DELETE" });
      location.hash = "#/devices";
      state.confirm = "";
      await show();
    });
  }
}

function bindTenants() {
  document.getElementById("tenant-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/tenants", { method: "POST", body: { name: event.currentTarget.name.value } });
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector("table").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      await api(`/api/tenants/${button.dataset.id}`, { method: "DELETE" });
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindUsers() {
  document.getElementById("user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/api/users", { method: "POST", body: { name: form.name.value, email: form.email.value, password: form.password.value, role: form.role.value, tenant_id: form.tenant_id ? form.tenant_id.value : null } });
      toast("Benutzer angelegt");
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector("table").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      await api(`/api/users/${button.dataset.id}`, { method: "DELETE" });
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindKeys() {
  document.getElementById("key-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const created = await api("/api/keys", { method: "POST", body: { label: form.label.value, tenant_id: form.tenant_id.value || null, max_uses: form.max_uses.value || null } });
      toast(`Schlüssel ${created.key}`);
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.querySelector("table").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      await api(`/api/keys/${button.dataset.id}/revoke`, { method: "POST", body: {} });
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindSystem() {
  const update = document.getElementById("cloud-update");
  if (update) {
    update.addEventListener("click", async () => {
      if (!confirm("Cloud-Konsole aus GitHub updaten und neu starten?")) return;
      try {
        const result = await api("/api/system/update", { method: "POST", body: {} });
        toast(result.message);
        const data = await api("/api/system/update-log");
        state.log = data.log;
        render();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
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
}

window.addEventListener("hashchange", () => show());
api("/api/me").then((user) => {
  state.user = user;
  return show();
}).catch(() => render());
