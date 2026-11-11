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
  updateState: "idle",
  deviceLog: "",
  deviceUpdateState: "idle",
  tenantId: localStorage.getItem("hibit-tenant") || "",
  ticket: "",
  editUser: "",
  totpSetup: null,
  recoveryCodes: null,
  network: null,
  networkId: "",
  networkMethod: "",
  networkSsids: [],
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

function durationLabel(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "–";
  const total = Math.floor(value);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days} T ${hours} Std`;
  if (hours > 0) return `${hours} Std ${minutes} Min`;
  if (minutes > 0) return `${minutes} Min`;
  return `${total} s`;
}

function localInput(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function durationPresets(current) {
  return `<div class="presets" role="group" aria-label="Dauer">${[15, 30, 45, 60, 120].map((seconds) => `<button type="button" class="ghost preset${Number(current) === seconds ? " active" : ""}" data-preset="${seconds}">${seconds}s</button>`).join("")}</div>`;
}

function runtimeLine(device) {
  const runtime = device.runtime || {};
  const bits = [`Uptime ${durationLabel(runtime.uptime_seconds)}`, `Laufzeit ${durationLabel(runtime.service_seconds)}`];
  if (device.update_at) bits.push(`Update ${when(device.update_at)}`);
  return bits.join(" · ");
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
  if (response.status === 401 && path !== "/api/login" && path !== "/api/login/totp") {
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

let updateWatch = 0;

function updateStatusText(stateName, live) {
  if (stateName === "running") return "Update läuft";
  if (stateName === "failed") return live ? "Update fehlgeschlagen" : "Letztes Update fehlgeschlagen";
  if (stateName === "done") return live ? "Update abgeschlossen, die Seite wird neu geladen" : "Letztes Update abgeschlossen";
  return "";
}

function startUpdateWatch(options) {
  const token = ++updateWatch;
  const button = options.buttonId ? document.getElementById(options.buttonId) : null;
  if (button) button.disabled = true;
  const started = Date.now();
  const tick = async () => {
    if (token !== updateWatch) return;
    if (options.deviceId && deviceId() !== options.deviceId) return;
    if (options.page && route().name !== options.page) return;
    const logEl = document.getElementById(options.logId);
    if (!logEl) return;
    if (Date.now() - started > 20 * 60 * 1000) {
      const stateEl = document.getElementById(options.stateId);
      if (stateEl) stateEl.textContent = "Das Update dauert ungewöhnlich lange. Der Verlauf steht im Protokoll.";
      if (button && button.isConnected) button.disabled = false;
      return;
    }
    try {
      const data = await options.fetchStatus();
      const currentLog = document.getElementById(options.logId);
      const currentState = document.getElementById(options.stateId);
      if (token !== updateWatch || !currentLog) return;
      currentLog.textContent = data.log || "";
      currentLog.scrollTop = currentLog.scrollHeight;
      if (data.state === "failed") {
        if (currentState) currentState.textContent = updateStatusText("failed", true);
        if (button && button.isConnected) button.disabled = false;
        toast("Update fehlgeschlagen", true);
        return;
      }
      if (data.state === "done") {
        if (currentState) currentState.textContent = updateStatusText("done", true);
        setTimeout(() => location.reload(), 800);
        return;
      }
      if (currentState) currentState.textContent = updateStatusText("running", true);
    } catch (error) {
      if (token !== updateWatch) return;
      const currentState = document.getElementById(options.stateId);
      if (currentState) currentState.textContent = options.offlineText;
    }
    setTimeout(tick, 1000);
  };
  tick();
}

function route() {
  const parts = (location.hash || "#/dashboard").replace(/^#\/?/, "").split("/").filter(Boolean);
  return { name: parts[0] || "dashboard", id: parts[1] || "", tab: parts[2] || "queue" };
}

function isAdmin() {
  return state.user && state.user.role === "superadmin";
}

function icon(path) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
}

function brand() {
  return `<a class="brand" href="#/dashboard"><img class="logo-on-dark" src="/logo-on-dark.png" alt="HiBit"><img class="logo-on-light" src="/logo-on-light.png" alt=""><span class="product">Signage Cloud</span></a>`;
}

function themeGlyph(theme) {
  if (theme === "light") {
    return icon('<circle cx="12" cy="12" r="3.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>');
  }
  return icon('<path d="M15.8 3.4a7.6 7.6 0 1 0 4.8 11.4A6.1 6.1 0 0 1 15.8 3.4z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>');
}

function themeSwitch() {
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const label = theme === "light" ? "Hell, auf dunkel wechseln" : "Dunkel, auf hell wechseln";
  return `<button type="button" class="icon theme-toggle" data-theme-toggle aria-label="${label}" title="${theme === "light" ? "Hell" : "Dunkel"}">${themeGlyph(theme)}</button>`;
}

function profileButton() {
  const active = route().name === "account" ? " active" : "";
  const letter = (state.user.name || "?").trim().charAt(0).toUpperCase() || "?";
  return `<a class="profile${active}" href="#/account" aria-label="Konto" title="${esc(state.user.name)}">${esc(letter)}</a>`;
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
  const out = icon('<path d="M10 7V5.2A1.2 1.2 0 0 1 11.2 4H19v16h-7.8A1.2 1.2 0 0 1 10 18.8V17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M4 12h10.2M11.2 8.2 15 12l-3.8 3.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>');
  return `<div class="shell"><aside class="side">${brand()}${tenantSwitch()}<nav class="nav">${nav()}</nav>
    <div class="side-foot">${profileButton()}<div class="who"><div class="who-name">${esc(state.user.name)}</div><div class="who-role">${esc(roleLabel(state.user.role))}${state.user.tenant_name ? " · " + esc(state.user.tenant_name) : ""}</div></div>
    <div class="side-tools">${themeSwitch()}<button class="icon" id="logout" type="button" aria-label="Abmelden" title="Abmelden">${out}</button></div></div></aside><main class="main">${inner}</main></div>`;
}

function loginHtml() {
  if (state.ticket) {
    return `<div class="login-wrap"><form class="card login" id="totp-form">${brand()}
      <h1>Zweiter Faktor</h1>
      <p class="muted">Code aus der Authenticator-App oder ein Wiederherstellungscode.</p>
      <label>Code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
      <p class="error" id="login-error"></p>
      <div class="row"><button type="submit">Bestätigen</button><button class="ghost" id="totp-back" type="button">Zurück</button></div>
    </form></div>`;
  }
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
  const devices = filterDevices(data.devices || []);
  const adopted = devices.filter((device) => device.status !== "pending");
  const pending = devices.filter((device) => device.status === "pending");
  const online = adopted.filter((device) => device.online).length;
  const offline = adopted.filter((device) => !device.online);
  const tenantCount = selectedTenant() ? 1 : data.tenants;
  const attention = pending.concat(offline);
  const running = adopted.filter((device) => device.online);
  return `<div class="page-head"><div><h1>Übersicht</h1><p class="muted">Was gerade läuft und wo etwas fehlt.</p></div></div>
    <div class="stats">
      <article class="card stat"><span class="muted">Online</span><b>${online}</b></article>
      <article class="card stat"><span class="muted">Offline</span><b>${offline.length}</b></article>
      <article class="card stat"><span class="muted">Warten</span><b>${pending.length}</b></article>
      <article class="card stat"><span class="muted">Mandanten</span><b>${tenantCount}</b></article>
    </div>
    <h2>Aufmerksamkeit</h2>
    ${attention.length ? `<div class="list">${attention.map(deviceCard).join("")}</div>` : '<p class="quiet">Alle adoptierten Geräte sind online.</p>'}
    <h2>Im Betrieb</h2>
    ${deviceGroups(running, "Gerade ist kein Gerät online.")}`;
}

function tenantOptions(selected) {
  return state.tenants.map((tenant) => `<option value="${esc(tenant.id)}" ${tenant.id === selected ? "selected" : ""}>${esc(tenant.name)}</option>`).join("");
}

function selectedTenant() {
  if (!isAdmin() || !state.tenantId) return "";
  return state.tenants.some((tenant) => tenant.id === state.tenantId) ? state.tenantId : "";
}

function tenantSwitch() {
  if (!isAdmin() || state.tenants.length < 1) return "";
  const current = selectedTenant();
  const currentName = current ? (state.tenants.find((tenant) => tenant.id === current) || {}).name || "Mandant" : "Alle Mandanten";
  const options = [{ id: "", name: "Alle Mandanten" }].concat(state.tenants.map((tenant) => ({ id: tenant.id, name: tenant.name })));
  const chevron = icon('<path d="M7 10l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>');
  return `<div class="tenant-switch">
    <button type="button" class="tenant-current" id="tenant-toggle" aria-haspopup="listbox" aria-expanded="false">
      <span><span class="kicker">Mandant</span><span class="tenant-name">${esc(currentName)}</span></span>${chevron}
    </button>
    <div class="tenant-menu" id="tenant-menu" role="listbox" hidden>
      ${options.map((option) => `<button type="button" role="option" data-tenant="${esc(option.id)}" class="${option.id === current ? "active" : ""}" aria-selected="${option.id === current ? "true" : "false"}">${esc(option.name)}</button>`).join("")}
    </div>
  </div>`;
}

function filterDevices(devices) {
  const tenantId = selectedTenant();
  if (!tenantId) return devices;
  return devices.filter((device) => device.tenant_id === tenantId);
}

function deviceCard(device) {
  const runtime = device.status === "pending" ? "" : `<div class="meta">${esc(runtimeLine(device))}</div>`;
  return `<a class="card item" href="#/device/${device.id}"><div><div class="title">${esc(device.name || device.hostname || "Gerät")}</div><div class="meta">${esc(device.tenant_name || "ohne Mandant")} · ${esc((device.ips || []).join(", ") || "keine IP")}${device.version ? " · " + esc(device.version) : ""}</div>${runtime}</div><div>${statusBadge(device)}</div></a>`;
}

function groupsFor(devices) {
  const buckets = new Map();
  devices.forEach((device) => {
    const key = device.tenant_id || "";
    if (!buckets.has(key)) buckets.set(key, { name: device.tenant_name || "Ohne Mandant", devices: [] });
    buckets.get(key).devices.push(device);
  });
  const ordered = [];
  state.tenants.forEach((tenant) => {
    if (buckets.has(tenant.id)) ordered.push(buckets.get(tenant.id));
  });
  if (buckets.has("")) ordered.push(buckets.get(""));
  buckets.forEach((group, key) => {
    if (key && !state.tenants.some((tenant) => tenant.id === key)) ordered.push(group);
  });
  return ordered;
}

function deviceGroups(devices, emptyText) {
  if (!devices.length) return `<div class="card panel muted">${emptyText}</div>`;
  if (selectedTenant() || state.tenants.length < 2) return `<div class="list">${devices.map(deviceCard).join("")}</div>`;
  return groupsFor(devices).map((group) => `<section class="tenant-block"><h2>${esc(group.name)}</h2><div class="list">${group.devices.map(deviceCard).join("")}</div></section>`).join("");
}

function devicesHtml() {
  const visible = filterDevices(state.devices);
  const pending = visible.filter((device) => device.status === "pending");
  const adopted = visible.filter((device) => device.status !== "pending");
  const pendingCards = pending.map((device) => `<article class="card item pending">
      <div><div class="title">${esc(device.hostname || "Neues Gerät")}</div>
      <div class="meta">${esc(device.tenant_name || "ohne Mandant")} · ${esc((device.ips || []).join(", "))} · ${esc(device.version || "")} · gesehen ${esc(when(device.last_seen))}</div></div>
      <a class="button" href="#/device/${device.id}">Adoptieren</a>
    </article>`).join("");
  return `<div class="page-head"><div><h1>Geräte</h1><p class="muted">Ausstehende Geräte brauchen einen Adopt, danach gehören sie zu einem Mandanten.</p></div><button class="ghost" id="reload" type="button">Aktualisieren</button></div>
    ${pendingCards ? `<h2>Warten auf Adopt</h2><div class="list">${pendingCards}</div>` : ""}
    <h2>Im Betrieb</h2>${deviceGroups(adopted, "Keine adoptierten Geräte.")}`;
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
  const runtime = device.runtime || {};
  const memory = runtime.mem_total ? `${sizeLabel(runtime.mem_total - (runtime.mem_available || 0))} von ${sizeLabel(runtime.mem_total)}` : "–";
  return `<div class="page-head"><div><h1>${esc(device.name)}</h1><p class="muted">${esc(device.tenant_name || "")} · ${statusBadge(device)} · Rev. ${device.state_revision}</p>
    <div class="facts"><span>Uptime <b>${durationLabel(runtime.uptime_seconds)}</b></span><span>System-Laufzeit <b>${durationLabel(runtime.service_seconds)}</b></span><span>Last <b>${esc(runtime.load || "–")}</b></span><span>RAM <b>${memory}</b></span></div></div></div>
    <section class="card panel">
      <div class="spread"><div><div class="title">Anzeige</div><p class="muted" id="preview-state">Warte auf den Pi</p></div></div>
      <div class="preview" id="preview-frame">
        <button class="ghost preview-full" id="preview-full" type="button">Vollbild</button>
        <p class="placeholder">Noch kein Bild. So ist zu sehen, ob der Pi eine Seite, ein Bild oder ein Video wirklich zeigt.</p>
      </div>
    </section>
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
      <label>Sekunden<input name="duration_seconds" type="number" min="1" value="10">${durationPresets(10)}</label>
      <label>Scroll px/s<input name="scroll_speed" type="number" min="0" max="4000" value="0"></label>
    </div>
    <label class="check"><input type="checkbox" name="fit_screen" checked> An Auflösung anpassen</label>
    <p class="muted">Scroll px/s schiebt Bilder nach unten. 0 bleibt stehen.</p>`;
  }
  if (mode === "web") {
    return `<div class="grid-2">
      <label>Name<input name="name" placeholder="Optional"></label>
      <label>Adresse<input name="url" type="url" placeholder="https://example.com" required></label>
      <label>Sekunden<input name="duration_seconds" type="number" min="1" value="60" required>${durationPresets(60)}</label>
      <label>Scroll px/s<input name="scroll_speed" type="number" min="0" max="4000" value="0"></label>
      <label>Zoom %<input name="zoom" type="number" min="100" max="300" step="10" value="100"></label>
      <label>Farbschema<select name="color_scheme"><option value="auto">Automatisch</option><option value="light">Hell</option><option value="dark">Dunkel</option></select></label>
    </div>
    <p class="muted">Der Player lädt die Seite selbst. Scroll px/s schiebt sie nach unten, 0 bleibt oben. Zoom % vergrößert Text und Seite, 100 bleibt original. Hell oder Dunkel zwingt das Farbschema, Automatisch folgt dem System.</p>`;
  }
  const image = mode === "image-url";
  return `<div class="grid-2">
    <label>Name<input name="name" placeholder="Optional"></label>
    <label>Adresse<input name="url" type="url" placeholder="${image ? "https://…/bild.jpg" : "https://…/clip.mp4"}" required></label>
    <label>Sekunden<input name="duration_seconds" type="number" min="1" value="${image ? "10" : ""}" placeholder="${image ? "" : "bis zum Ende"}">${durationPresets(image ? 10 : "")}</label>
    ${image ? `<label>Scroll px/s<input name="scroll_speed" type="number" min="0" max="4000" value="0"></label>` : ""}
  </div>
  <label class="check"><input type="checkbox" name="fit_screen" ${image ? "checked" : ""}> An Auflösung anpassen</label>
  <p class="muted">${image ? "Das ganze Bild bleibt sichtbar. Ohne Haken füllt es den Bildschirm und wird beschnitten. Scroll px/s schiebt es nach unten, 0 bleibt stehen." : "Ohne Haken füllt das Video den Bildschirm und wird beschnitten."}</p>`;
}

function queueTab() {
  const items = (state.playback && state.playback.queue) || [];
  const files = (state.playback && state.playback.files) || [];
  const mode = state.addMode || "image-url";
  const modes = [["image-url", "Bild-Adresse"], ["video-url", "Video-Adresse"], ["web", "Webseite"], ["local", "Lokale Datei"]];
  const rows = items.map((item, index) => `<article class="card item"><div class="item-body"><div><div class="title">${esc(item.name)}</div><div class="meta"><span class="badge">${item.kind === "web" ? "Webseite" : item.origin === "url" ? "Adresse" : item.origin === "share" ? "Freigabe" : "Datei"}</span> <span class="badge">${esc(kindLabel(item.kind))}</span> ${item.kind === "web" ? "" : `<span class="badge">${item.fit === "cover" ? "Beschnitten" : "Ganzes Bild"}</span>`} ${item.enabled ? "" : '<span class="badge warn">Pausiert</span>'}</div></div></div>
    <div class="item-tools">
      <div class="tool-fields">
        ${item.origin === "url" ? `<label class="tool wide">Adresse<input data-field="url" data-id="${esc(item.id)}" type="url" value="${esc(item.url || "")}"></label>` : ""}
        ${item.kind === "web" ? "" : `<label class="tool">Darstellung${fitSelect(item)}</label>`}
        <label class="tool">Sekunden<input data-duration="${esc(item.id)}" type="number" min="1" placeholder="Ende" value="${item.duration_seconds ?? ""}" title="Sekunden"></label>
        ${item.kind === "video" ? "" : `<label class="tool">Scroll px/s<input data-field="scroll" data-id="${esc(item.id)}" type="number" min="0" max="4000" value="${item.scroll_speed || 0}" title="Pixel pro Sekunde, 0 bleibt stehen"></label>`}
        ${item.kind === "web" ? `<label class="tool">Zoom %<input data-field="zoom" data-id="${esc(item.id)}" type="number" min="100" max="300" step="10" value="${item.zoom || 100}" title="100 ist original, höhere Werte vergrößern die Seite"></label>` : ""}
        ${item.kind === "web" ? `<label class="tool">Farbschema<select data-field="scheme" data-id="${esc(item.id)}" title="Hell oder Dunkel erzwingt das Farbschema dieser Seite"><option value="auto" ${(item.color_scheme || "auto") === "auto" ? "selected" : ""}>Automatisch</option><option value="light" ${item.color_scheme === "light" ? "selected" : ""}>Hell</option><option value="dark" ${item.color_scheme === "dark" ? "selected" : ""}>Dunkel</option></select></label>` : ""}
      </div>
      <div class="actions">
        <button class="ghost" data-action="up" data-id="${esc(item.id)}" ${index === 0 ? "disabled" : ""}>Hoch</button>
        <button class="ghost" data-action="down" data-id="${esc(item.id)}" ${index === items.length - 1 ? "disabled" : ""}>Runter</button>
        <button class="ghost" data-action="toggle" data-id="${esc(item.id)}">${item.enabled ? "Pause" : "Aktiv"}</button>
        <button class="danger" data-action="delete" data-id="${esc(item.id)}">Löschen</button>
      </div>
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

function networkLabel(item) {
  const kind = item.kind === "wifi" ? "WLAN" : "Ethernet";
  const where = item.device || "inaktiv";
  const addr = item.current || (item.address ? `${item.address}/${item.prefix || 24}` : "");
  return `${kind} · ${item.name} · ${where}${addr ? " · " + addr : ""}`;
}

function networkHtml() {
  const info = state.network;
  if (!info) return `<section class="card panel"><h2>Netzwerk</h2><p class="muted">Netzwerk wird gelesen.</p></section>`;
  if (!info.available) return `<section class="card panel"><h2>Netzwerk</h2><p class="muted">${esc(info.detail || "Netzwerk ist gerade nicht erreichbar.")}</p></section>`;
  const connections = info.connections || [];
  if (!connections.length) return `<section class="card panel"><h2>Netzwerk</h2><p class="muted">NetworkManager hat keine Ethernet- oder WLAN-Verbindung.</p></section>`;
  const selected = connections.find((item) => item.id === state.networkId) || connections.find((item) => item.active) || connections[0];
  state.networkId = selected.id;
  const method = state.networkMethod || selected.method || "auto";
  state.networkMethod = method;
  const options = connections.map((item) => `<option value="${esc(item.id)}" ${item.id === selected.id ? "selected" : ""}>${esc(networkLabel(item))}</option>`).join("");
  const ssids = (state.networkSsids || []).map((item) => `<option value="${esc(item.ssid)}"></option>`).join("");
  return `<form class="card composer" id="network-form">
    <div class="spread"><div><h2>Netzwerk</h2><p class="muted">Adresse und WLAN dieses Pi. Nach dem Speichern wird die Verbindung neu aufgebaut. Trägt die Adresse nicht, kommt die bisherige nach einer Minute zurück.</p></div><button class="ghost" id="network-reload" type="button">Aktualisieren</button></div>
    <label>Hostname<input name="hostname" value="${esc(info.hostname || "")}" required></label>
    <label>Anschluss<select name="connection" id="network-connection">${options}</select></label>
    <div class="mode-menu" role="group" aria-label="Adressvergabe">
      <button type="button" data-net-method="auto" class="${method === "auto" ? "active" : ""}">Automatisch</button>
      <button type="button" data-net-method="manual" class="${method === "manual" ? "active" : ""}">Feste Adresse</button>
    </div>
    <div class="grid-2" id="network-manual" ${method === "manual" ? "" : "hidden"}>
      <label>Adresse<input name="address" value="${esc(selected.address || "")}" placeholder="192.168.1.50"></label>
      <label>Präfix<input name="prefix" type="number" min="1" max="32" value="${esc(selected.prefix || 24)}"></label>
      <label>Gateway<input name="gateway" value="${esc(selected.gateway || "")}" placeholder="192.168.1.1"></label>
      <label>DNS<input name="dns" value="${esc((selected.dns || []).join(", "))}" placeholder="192.168.1.1"></label>
    </div>
    <div id="network-wifi" ${selected.kind === "wifi" ? "" : "hidden"}>
      <label>WLAN<input name="ssid" list="network-ssids" value="${esc(selected.ssid || "")}" placeholder="Name des Netzes"></label>
      <datalist id="network-ssids">${ssids}</datalist>
      <label>Passwort<input name="psk" type="password" placeholder="leer lassen behält das bisherige" autocomplete="new-password"></label>
      <button class="ghost" id="network-scan" type="button">Netze suchen</button>
    </div>
    <button type="submit">Netzwerk speichern</button>
  </form>`;
}

function bindNetwork(saveUrl, scanUrl) {
  const form = document.getElementById("network-form");
  if (!form) return;
  const reload = document.getElementById("network-reload");
  if (reload) {
    reload.addEventListener("click", async () => {
      state.networkMethod = "";
      await show();
    });
  }
  const select = document.getElementById("network-connection");
  if (select) {
    select.addEventListener("change", () => {
      state.networkId = select.value;
      const item = ((state.network && state.network.connections) || []).find((entry) => entry.id === select.value);
      fillNetwork(item);
    });
  }
  document.querySelectorAll("[data-net-method]").forEach((button) => {
    button.addEventListener("click", () => {
      state.networkMethod = button.dataset.netMethod === "manual" ? "manual" : "auto";
      const manual = document.getElementById("network-manual");
      if (manual) manual.hidden = state.networkMethod !== "manual";
      document.querySelectorAll("[data-net-method]").forEach((item) => item.classList.toggle("active", item.dataset.netMethod === state.networkMethod));
    });
  });
  const scan = document.getElementById("network-scan");
  if (scan) {
    scan.addEventListener("click", async () => {
      scan.disabled = true;
      try {
        const result = await api(scanUrl, { method: "POST", body: {} });
        state.networkSsids = result.networks || [];
        const list = document.getElementById("network-ssids");
        if (list) list.innerHTML = state.networkSsids.map((item) => `<option value="${esc(item.ssid)}"></option>`).join("");
        toast(state.networkSsids.length ? `${state.networkSsids.length} Netze gefunden` : "Kein WLAN gefunden");
      } catch (error) {
        toast(error.message, true);
      } finally {
        scan.disabled = false;
      }
    });
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    try {
      const result = await api(saveUrl, {
        method: "POST",
        body: {
          hostname: form.hostname.value.trim(),
          connection: form.connection.value,
          method: state.networkMethod || "auto",
          address: form.address.value.trim(),
          prefix: Number(form.prefix.value || 24),
          gateway: form.gateway.value.trim(),
          dns: form.dns.value,
          ssid: form.ssid ? form.ssid.value.trim() : "",
          psk: form.psk ? form.psk.value : "",
        },
      });
      if (form.psk) form.psk.value = "";
      toast(result.message || "Netzwerk gespeichert");
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  });
}

function fillNetwork(item) {
  const form = document.getElementById("network-form");
  if (!form || !item) return;
  state.networkMethod = item.method === "manual" ? "manual" : "auto";
  form.address.value = item.address || "";
  form.prefix.value = item.prefix || 24;
  form.gateway.value = item.gateway || "";
  form.dns.value = (item.dns || []).join(", ");
  if (form.ssid) form.ssid.value = item.ssid || "";
  if (form.psk) form.psk.value = "";
  const manual = document.getElementById("network-manual");
  if (manual) manual.hidden = state.networkMethod !== "manual";
  const wifi = document.getElementById("network-wifi");
  if (wifi) wifi.hidden = item.kind !== "wifi";
  document.querySelectorAll("[data-net-method]").forEach((button) => button.classList.toggle("active", button.dataset.netMethod === state.networkMethod));
}

function certsHtml() {
  const info = state.certs;
  if (!info) return `<section class="card panel"><h2>Zertifikate</h2><p class="muted">Zertifikate werden gelesen.</p></section>`;
  if (info.available === false) return `<section class="card panel"><h2>Zertifikate</h2><p class="muted">${esc(info.detail || "Nach dem Player-Update verfügbar.")}</p></section>`;
  const offer = state.certOffer;
  const offerHtml = offer
    ? `<div class="confirm-box" id="cert-offer"><p>${esc(offer.detail || "")}</p><p class="meta">${esc(offer.subject || "")}${offer.not_after ? " · gültig bis " + esc(offer.not_after) : ""}</p>${offer.pem ? `<button type="button" id="cert-trust">Diesem Aussteller vertrauen</button>` : ""}</div>`
    : `<div id="cert-offer" hidden></div>`;
  const rows = (info.certs || []).map((item) => `<div class="spread"><div><strong>${esc(item.subject)}</strong><div class="meta">${esc(item.issuer)}${item.not_after ? " · gültig bis " + esc(item.not_after) : ""}</div></div><button class="ghost" type="button" data-cert-remove="${esc(item.id)}">Entfernen</button></div>`).join("");
  return `<section class="card panel" id="cert-panel">
    <h2>Zertifikate</h2>
    <p class="muted">Wenn eine Firewall HTTPS prüft und ein eigenes Zertifikat einsetzt, vertraut der Player den Seiten erst, nachdem das Zertifikat der Firewall hier liegt.</p>
    <div class="row"><label>Adresse<input id="cert-host" value="${esc(state.certHost || "")}" placeholder="uptimekuma.hibit.de"></label><button class="ghost" id="cert-inspect" type="button">Anzeigen</button></div>
    ${offerHtml}
    <form id="cert-file" class="row"><label>PEM-Datei<input name="file" type="file" accept=".pem,.crt,.cer,text/plain"></label><button class="ghost" type="submit">Hochladen</button></form>
    <div class="list">${rows || '<p class="muted">Noch keines.</p>'}</div>
  </section>`;
}

function bindCerts(base) {
  const panel = document.getElementById("cert-panel");
  if (!panel) return;
  const inspect = document.getElementById("cert-inspect");
  if (inspect) {
    inspect.addEventListener("click", async () => {
      const host = document.getElementById("cert-host");
      state.certHost = host ? host.value : "";
      inspect.disabled = true;
      try {
        state.certOffer = await api(`${base}/inspect`, { method: "POST", body: { host: state.certHost } });
        await show();
      } catch (error) {
        toast(error.message, true);
      } finally {
        inspect.disabled = false;
      }
    });
  }
  const trust = document.getElementById("cert-trust");
  if (trust) {
    trust.addEventListener("click", async () => {
      if (!state.certOffer || !state.certOffer.pem) return;
      trust.disabled = true;
      try {
        const result = await api(base, { method: "POST", body: { pem: state.certOffer.pem } });
        state.certOffer = null;
        toast(result.message || "Zertifikat gespeichert");
        await show();
      } catch (error) {
        toast(error.message, true);
        trust.disabled = false;
      }
    });
  }
  const fileForm = document.getElementById("cert-file");
  if (fileForm) {
    fileForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = fileForm.file.files[0];
      if (!file) {
        toast("Bitte eine PEM-Datei wählen", true);
        return;
      }
      try {
        const pem = await file.text();
        const result = await api(base, { method: "POST", body: { pem } });
        state.certOffer = null;
        toast(result.message || "Zertifikat gespeichert");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  panel.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-cert-remove]");
    if (!button) return;
    button.disabled = true;
    try {
      const result = await api(`${base}/${button.dataset.certRemove}`, { method: "DELETE" });
      state.certOffer = null;
      toast(result.message || "Zertifikat entfernt");
      await show();
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  });
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
  const splash = `<form class="card composer" id="splash-form">
      <h2>Boot-Logo</h2>
      <p class="muted">PNG, höchstens 1,5 MB. Es erscheint auf dem Pi beim Linux-Start, bis die Anzeige da ist. Sichtbar nach dem nächsten Neustart.</p>
      <label>Logo<input name="file" type="file" accept="image/png,.png" required></label>
      <div class="row"><button type="submit">Logo speichern</button><button class="ghost" id="splash-reset" type="button">Standardlogo</button></div>
    </form>`;
  return `${networkHtml()}${certsHtml()}${power}${splash}<form class="card composer" id="rename-form"><label>Name<input name="name" value="${esc(device.name)}" required></label><button type="submit">Speichern</button></form>
    ${isAdmin() ? `<form class="card composer" id="move-form"><label>Mandant wechseln<select name="tenant_id">${tenantOptions(device.tenant_id)}</select></label><button class="ghost" type="submit">Verschieben</button></form>
    <section class="card panel"><div class="spread"><div><div class="title">Geräte-Update</div><p class="muted">Sofort, oder zu einem Zeitpunkt. Offline wartet der Plan, bis der Pi wieder da ist.</p></div><button id="device-update" type="button">Jetzt</button></div>
    <form class="row" id="device-schedule"><label>Zeitpunkt<input type="datetime-local" name="run_at" required value="${localInput(device.update_at)}"></label><button class="ghost" type="submit">Planen</button>${device.update_at ? `<button class="ghost" id="device-schedule-clear" type="button">Planung löschen</button>` : ""}</form>
    ${device.update_at ? `<p class="muted">Geplant für ${esc(when(device.update_at))}.</p>` : ""}
    <p class="muted" id="device-update-state">${esc(updateStatusText(state.deviceUpdateState, false))}</p>
    <pre class="log" id="device-update-log">${esc(state.deviceLog || "")}</pre></section>
    <section class="card panel"><div class="spread"><div><div class="title">Gerät entfernen</div><p class="muted">Das Gerät verschwindet aus der Cloud. Ein erneuter Adopt ist danach nötig.</p></div><button class="danger" id="forget" type="button">${state.confirm === "forget" ? "Wirklich entfernen" : "Gerät entfernen"}</button></div></section>` : `<p class="muted">Updates startet nur ein Cloud-Admin.</p>`}
    <p class="meta">Hardware ${esc(device.hardware_id)} · zuletzt ${esc(when(device.last_seen))}</p>`;
}

function tenantsHtml() {
  const rows = state.tenants.map((tenant) => `<tr><td>${esc(tenant.name)}</td><td>${tenant.devices}</td><td>${tenant.users}</td><td><button class="danger" data-action="delete-tenant" data-id="${esc(tenant.id)}">Löschen</button></td></tr>`).join("");
  return `<div class="page-head"><div><h1>Mandanten</h1><p class="muted">Jedes Gerät gehört nach dem Adopt genau zu einem Mandanten.</p></div></div>
    <form class="card composer" id="tenant-form"><label>Name<input name="name" required></label><button type="submit">Mandant anlegen</button></form>
    <table><thead><tr><th>Name</th><th>Geräte</th><th>Benutzer</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">Noch keine Mandanten.</td></tr>'}</tbody></table>`;
}

function roleOptions(selected) {
  const roles = isAdmin()
    ? [["superadmin", "Cloud-Admin"], ["tenant_admin", "Mandanten-Admin"], ["tenant_user", "Mandanten-Benutzer"]]
    : [["tenant_admin", "Mandanten-Admin"], ["tenant_user", "Mandanten-Benutzer"]];
  return roles.map(([id, label]) => `<option value="${id}" ${id === selected ? "selected" : ""}>${label}</option>`).join("");
}

function userEditHtml() {
  const user = state.users.find((item) => item.id === state.editUser);
  if (!user) return "";
  return `<form class="card composer" id="user-edit"><h2>${esc(user.name)} bearbeiten</h2><div class="grid-2">
      <label>Name<input name="name" value="${esc(user.name)}" required></label>
      <label>E-Mail<input name="email" type="email" value="${esc(user.email)}" required></label>
      <label>Neues Passwort<input name="password" type="password" minlength="8" placeholder="leer lassen"></label>
      <label>Rolle<select name="role">${roleOptions(user.role)}</select></label>
      ${isAdmin() ? `<label>Mandant<select name="tenant_id"><option value="">Kein Mandant</option>${tenantOptions(user.tenant_id || "")}</select></label>` : ""}
    </div>
    <div class="row"><button type="submit">Speichern</button><button class="ghost" id="user-edit-cancel" type="button">Abbrechen</button>
      ${user.totp_enabled ? `<button class="ghost" id="user-totp-reset" type="button">2FA zurücksetzen</button>` : ""}
    </div></form>`;
}

function usersHtml() {
  const rows = state.users.map((user) => `<tr><td>${esc(user.name)}</td><td>${esc(user.email)}</td><td>${esc(roleLabel(user.role))}${user.totp_enabled ? ' <span class="badge ok">2FA</span>' : ""}</td><td>${esc(user.tenant_name || "–")}</td><td class="actions"><button class="ghost" data-action="edit-user" data-id="${esc(user.id)}">Bearbeiten</button>${user.id === state.user.id ? "" : `<button class="danger" data-action="delete-user" data-id="${esc(user.id)}">Löschen</button>`}</td></tr>`).join("");
  return `<div class="page-head"><div><h1>Benutzer</h1><p class="muted">Angelegt, später geändert, und 2FA lässt sich hier zurücksetzen.</p></div></div>
    <form class="card composer" id="user-form"><div class="grid-2">
      <label>Name<input name="name" required></label>
      <label>E-Mail<input name="email" type="email" required></label>
      <label>Passwort<input name="password" type="password" minlength="8" required></label>
      <label>Rolle<select name="role">${roleOptions("tenant_user")}</select></label>
      ${isAdmin() ? `<label>Mandant<select name="tenant_id"><option value="">Kein Mandant</option>${tenantOptions("")}</select></label>` : ""}
    </div><button type="submit">Benutzer anlegen</button></form>
    ${userEditHtml()}
    <table><thead><tr><th>Name</th><th>E-Mail</th><th>Rolle</th><th>Mandant</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

function accountHtml() {
  const user = state.user || {};
  const setup = state.totpSetup;
  const codes = state.recoveryCodes;
  const setupBlock = setup
    ? `<form class="card composer" id="totp-confirm"><h2>Code bestätigen</h2><p class="muted">In der Authenticator-App den Schlüssel eintragen. Der Name ist HiBit Signage.</p><p class="secret">${esc(setup.secret)}</p><button class="ghost" id="totp-copy" type="button">Schlüssel kopieren</button><label>Code aus der App<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label><button type="submit">Aktivieren</button></form>`
    : user.totp_enabled
      ? `<form class="card composer" id="totp-disable"><h2>2FA ist aktiv</h2><p class="muted">Zum Abschalten Passwort und einen gültigen Code.</p><div class="grid-2"><label>Passwort<input name="password" type="password" required></label><label>Code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label></div><button class="danger" type="submit">2FA abschalten</button></form>`
      : `<section class="card panel"><div class="spread"><div><div class="title">Zwei-Faktor-Anmeldung</div><p class="muted">Ein Code aus einer Authenticator-App, zusätzlich zum Passwort.</p></div><button id="totp-start" type="button">Einrichten</button></div></section>`;
  const recovery = codes ? `<section class="card panel"><h2>Wiederherstellungscodes</h2><p class="muted">Jeder Code gilt einmal. Sie werden nur jetzt angezeigt.</p><pre class="secret">${esc(codes.join("\n"))}</pre><button class="ghost" id="recovery-done" type="button">Verstanden</button></section>` : "";
  return `<div class="page-head"><div><h1>Konto</h1><p class="muted">${esc(user.email || "")} · ${esc(roleLabel(user.role))}</p></div></div>
    ${recovery}${setupBlock}
    <form class="card composer" id="password-form"><h2>Passwort</h2><div class="grid-2"><label>Aktuell<input name="current" type="password" required></label><label>Neu<input name="next" type="password" minlength="8" required></label></div><button type="submit">Ändern</button></form>`;
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
    <article class="card stat"><span class="muted">Speicher frei</span><b>${sizeLabel(info.disk_free)}</b></article>
    <section class="card panel"><div class="spread"><div><div class="title">Cloud aus GitHub updaten</div><p class="muted">Sofort, oder zu einem Zeitpunkt. Passwort und 2FA liegen im Profil.</p></div><button id="cloud-update" type="button">Jetzt</button></div>
    <form class="row" id="cloud-schedule"><label>Zeitpunkt<input type="datetime-local" name="run_at" required value="${localInput(info.update_at)}"></label><button class="ghost" type="submit">Planen</button>${info.update_at ? `<button class="ghost" id="cloud-schedule-clear" type="button">Planung löschen</button>` : ""}</form>
    ${info.update_at ? `<p class="muted">Geplant für ${esc(when(info.update_at))}.</p>` : ""}
    <p class="muted" id="update-state">${esc(updateStatusText(state.updateState, false))}</p>
    <pre class="log" id="update-log">${esc(state.log || "")}</pre></section>`;
}

function render() {
  if (state.term) {
    state.term.close();
    state.term = null;
  }
  const app = document.getElementById("app");
  if (!state.user) {
    app.innerHTML = loginHtml();
    const login = document.getElementById("login-form");
    if (login) login.addEventListener("submit", onLogin);
    const totp = document.getElementById("totp-form");
    if (totp) totp.addEventListener("submit", onTotp);
    const back = document.getElementById("totp-back");
    if (back) back.addEventListener("click", () => { state.ticket = ""; render(); });
    return;
  }
  const current = route();
  if (!(current.name === "device" && current.tab === "settings")) state.poweroff = false;
  const pages = { dashboard: dashboardHtml, devices: devicesHtml, device: deviceHtml, tenants: tenantsHtml, users: usersHtml, keys: keysHtml, account: accountHtml, system: systemHtml };
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
    const result = await api("/api/login", { method: "POST", body: { email: form.email.value, password: form.password.value } });
    if (result && result.totp_required) {
      state.ticket = result.ticket;
      render();
      return;
    }
    state.ticket = "";
    state.user = result;
    location.hash = "#/dashboard";
    await show();
  } catch (error) {
    document.getElementById("login-error").textContent = error.message;
  }
}

async function onTotp(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    state.user = await api("/api/login/totp", { method: "POST", body: { ticket: state.ticket, code: form.code.value } });
    state.ticket = "";
    location.hash = "#/dashboard";
    await show();
  } catch (error) {
    const target = document.getElementById("login-error");
    if (target) target.textContent = error.message;
  }
}

async function load() {
  const current = route();
  if (isAdmin()) state.tenants = (await api("/api/tenants")).tenants;
  if (current.name === "dashboard") state.dashboard = await api("/api/dashboard");
  if (current.name === "devices" || current.name === "device") state.devices = (await api("/api/devices")).devices;
  if (current.name === "device") {
    const data = await api(`/api/devices/${current.id}`);
    state.device = data.device;
    state.playback = data.state;
    if (current.tab === "settings") {
      const [network, certs] = await Promise.all([
        api(`/api/devices/${current.id}/network`).catch((error) => ({ available: false, detail: error.message, hostname: "", connections: [] })),
        api(`/api/devices/${current.id}/certs`).catch((error) => ({ available: false, detail: error.message, certs: [] })),
      ]);
      state.network = network;
      state.certs = certs;
    }
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
  if (current.name === "account") state.user = await api("/api/me");
  if (current.name === "system") {
    state.system = await api("/api/system");
    try {
      const update = await api("/api/system/update-log", { cache: "no-store" });
      state.log = update.log || "";
      state.updateState = update.state || "idle";
    } catch (error) {
      state.updateState = "idle";
    }
  }
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

let previewWatch = 0;

function previewLabel(playback) {
  const name = playback && playback.name ? `„${playback.name}“ ` : "";
  const phase = playback && playback.phase;
  if (phase === "failed") return `${name}kann gerade nicht geladen werden`;
  if (phase === "loading") return `${name}wird geladen`;
  if (phase === "blank") return `${name}bleibt leer`;
  if (phase === "playing") return `${name}wird angezeigt`;
  if (phase === "empty") return "Die Warteschlange ist leer";
  return "Warte auf den Pi";
}

function bindPreview() {
  const button = document.getElementById("preview-full");
  const frame = document.getElementById("preview-frame");
  if (!button || !frame || button.dataset.bound) return;
  button.dataset.bound = "1";
  button.addEventListener("click", () => {
    const open = frame.classList.toggle("is-full");
    button.textContent = open ? "Schließen" : "Vollbild";
  });
  if (!window.__previewEscape) {
    window.__previewEscape = true;
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const current = document.getElementById("preview-frame");
      const toggle = document.getElementById("preview-full");
      if (!current || !current.classList.contains("is-full")) return;
      current.classList.remove("is-full");
      if (toggle) toggle.textContent = "Vollbild";
    });
  }
}

function startPreview() {
  const token = ++previewWatch;
  const id = deviceId();
  const tick = async () => {
    if (token !== previewWatch || deviceId() !== id || route().name !== "device") return;
    const frame = document.getElementById("preview-frame");
    const stateEl = document.getElementById("preview-state");
    if (!frame) return;
    try {
      const data = await api(`/api/devices/${id}/preview`, { cache: "no-store" });
      if (token !== previewWatch) return;
      if (stateEl) stateEl.textContent = previewLabel(data.state);
      if (data.image) {
        let img = document.getElementById("preview-img");
        if (!img) {
          const placeholder = frame.querySelector(".placeholder");
          if (placeholder) placeholder.remove();
          img = document.createElement("img");
          img.id = "preview-img";
          img.alt = "Aktuelle Anzeige";
          frame.appendChild(img);
        }
        img.src = `data:image/jpeg;base64,${data.image}`;
      }
    } catch (error) {
      if (token !== previewWatch) return;
      if (stateEl) stateEl.textContent = error.message || "Gerät ist gerade nicht erreichbar";
    }
    setTimeout(tick, 2000);
  };
  tick();
}

function bind(current) {
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("hibit-theme", next);
      button.innerHTML = themeGlyph(next);
      button.title = next === "light" ? "Hell" : "Dunkel";
      button.setAttribute("aria-label", next === "light" ? "Hell, auf dunkel wechseln" : "Dunkel, auf hell wechseln");
    });
  });
  const tenantToggle = document.getElementById("tenant-toggle");
  const tenantMenu = document.getElementById("tenant-menu");
  if (tenantToggle && tenantMenu) {
    tenantToggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = tenantMenu.hasAttribute("hidden");
      tenantMenu.toggleAttribute("hidden", !open);
      tenantToggle.setAttribute("aria-expanded", open ? "true" : "false");
      tenantToggle.closest(".tenant-switch").classList.toggle("open", open);
    });
    tenantMenu.addEventListener("click", (event) => {
      const option = event.target.closest("[data-tenant]");
      if (!option) return;
      state.tenantId = option.dataset.tenant;
      localStorage.setItem("hibit-tenant", state.tenantId);
      render();
    });
  }
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
    startPreview();
    bindPreview();
  }
  if (current.name === "tenants") bindTenants();
  if (current.name === "users") bindUsers();
  if (current.name === "keys") bindKeys();
  if (current.name === "account") bindAccount();
  if (current.name === "system") bindSystem();
}

function bindQueue() {
  const form = document.getElementById("enqueue-form");
  if (!form) return;
  form.addEventListener("click", (event) => {
    const preset = event.target.closest("[data-preset]");
    if (preset) {
      const input = form.querySelector("[name=duration_seconds]");
      if (input) input.value = preset.dataset.preset;
      form.querySelectorAll("[data-preset]").forEach((item) => item.classList.toggle("active", item === preset));
      return;
    }
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    state.addMode = button.dataset.mode;
    render();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = state.addMode || "image-url";
    const fit = form.fit_screen && form.fit_screen.checked ? "contain" : "cover";
    const scroll = form.scroll_speed ? form.scroll_speed.value : 0;
    let body;
    if (mode === "local") {
      body = { origin: "local", file_id: form.file_id.value, duration_seconds: form.duration_seconds.value, fit, scroll_speed: scroll };
    } else if (mode === "web") {
      body = { origin: "url", kind: "web", name: form.name.value, url: form.url.value, duration_seconds: form.duration_seconds.value, scroll_speed: scroll, zoom: form.zoom ? form.zoom.value : 100, color_scheme: form.color_scheme ? form.color_scheme.value : "auto" };
    } else {
      body = {
        origin: "url",
        kind: mode === "video-url" ? "video" : "image",
        name: form.name.value,
        url: form.url.value,
        duration_seconds: form.duration_seconds.value,
        fit,
        scroll_speed: scroll,
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
    const scroll = event.target.closest("[data-field='scroll']");
    const zoom = event.target.closest("[data-field='zoom']");
    const scheme = event.target.closest("[data-field='scheme']");
    const address = event.target.closest("[data-field='url']");
    try {
      if (input) {
        await api(`/api/devices/${deviceId()}/queue/${input.dataset.duration}`, { method: "PATCH", body: { duration_seconds: input.value } });
        toast("Dauer gespeichert");
      }
      if (address) {
        await api(`/api/devices/${deviceId()}/queue/${address.dataset.id}`, { method: "PATCH", body: { url: address.value } });
        toast("Adresse gespeichert");
      }
      if (scroll) {
        await api(`/api/devices/${deviceId()}/queue/${scroll.dataset.id}`, { method: "PATCH", body: { scroll_speed: scroll.value } });
        toast(Number(scroll.value) > 0 ? "Scrollgeschwindigkeit gespeichert" : "Scroll aus");
      }
      if (zoom) {
        await api(`/api/devices/${deviceId()}/queue/${zoom.dataset.id}`, { method: "PATCH", body: { zoom: zoom.value } });
        toast(Number(zoom.value) > 100 ? `Zoom ${zoom.value} % gespeichert` : "Zoom auf Originalgröße");
      }
      if (scheme) {
        const label = scheme.value === "dark" ? "Dunkel" : scheme.value === "light" ? "Hell" : "Automatisch";
        await api(`/api/devices/${deviceId()}/queue/${scheme.dataset.id}`, { method: "PATCH", body: { color_scheme: scheme.value } });
        toast(`Farbschema ${label}`);
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
  bindNetwork(`/api/devices/${deviceId()}/network`, `/api/devices/${deviceId()}/network/scan`);
  bindCerts(`/api/devices/${deviceId()}/certs`);
  const splash = document.getElementById("splash-form");
  if (splash) {
    splash.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = splash.file.files[0];
      if (!file) {
        toast("Bitte ein PNG wählen", true);
        return;
      }
      const body = new FormData();
      body.append("file", file);
      try {
        const result = await api(`/api/devices/${deviceId()}/boot-logo`, { method: "POST", body });
        toast(result.message || "Logo gespeichert");
      } catch (error) {
        toast(error.message, true);
      }
    });
    document.getElementById("splash-reset").addEventListener("click", async () => {
      try {
        const result = await api(`/api/devices/${deviceId()}/boot-logo`, { method: "DELETE" });
        toast(result.message || "Standardlogo wiederhergestellt");
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
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
      const stateEl = document.getElementById("device-update-state");
      update.disabled = true;
      if (stateEl) stateEl.textContent = "Update wird gestartet";
      try {
        const result = await api(`/api/devices/${deviceId()}/update`, { method: "POST", body: {} });
        toast(result.message || "Update gestartet");
        state.deviceUpdateState = "running";
        startUpdateWatch(deviceUpdateOptions());
      } catch (error) {
        update.disabled = false;
        if (stateEl) stateEl.textContent = updateStatusText(state.deviceUpdateState, false);
        toast(error.message, true);
      }
    });
    loadDeviceLog();
  }
  const schedule = document.getElementById("device-schedule");
  if (schedule) {
    schedule.addEventListener("submit", async (event) => {
      event.preventDefault();
      const runAt = new Date(schedule.run_at.value);
      if (Number.isNaN(runAt.getTime())) {
        toast("Der Zeitpunkt ist ungültig", true);
        return;
      }
      try {
        await api(`/api/devices/${deviceId()}/update-schedule`, { method: "POST", body: { run_at: runAt.toISOString() } });
        toast("Update geplant");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const clearSchedule = document.getElementById("device-schedule-clear");
  if (clearSchedule) {
    clearSchedule.addEventListener("click", async () => {
      try {
        await api(`/api/devices/${deviceId()}/update-schedule`, { method: "DELETE" });
        toast("Planung gelöscht");
        await show();
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
  const edit = document.getElementById("user-edit");
  if (edit) {
    edit.addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = { name: edit.name.value, email: edit.email.value, role: edit.role.value, tenant_id: edit.tenant_id ? edit.tenant_id.value : null };
      if (edit.password.value) body.password = edit.password.value;
      try {
        await api(`/api/users/${state.editUser}`, { method: "PATCH", body });
        state.editUser = "";
        toast("Benutzer gespeichert");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
    document.getElementById("user-edit-cancel").addEventListener("click", () => {
      state.editUser = "";
      render();
    });
    const reset = document.getElementById("user-totp-reset");
    if (reset) {
      reset.addEventListener("click", async () => {
        if (!confirm("2FA für diesen Benutzer zurücksetzen?")) return;
        try {
          await api(`/api/users/${state.editUser}/totp/reset`, { method: "POST", body: {} });
          toast("2FA zurückgesetzt");
          await show();
        } catch (error) {
          toast(error.message, true);
        }
      });
    }
  }
  document.querySelector("table").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.action === "edit-user") {
      state.editUser = button.dataset.id;
      render();
      return;
    }
    try {
      await api(`/api/users/${button.dataset.id}`, { method: "DELETE" });
      if (state.editUser === button.dataset.id) state.editUser = "";
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindPassword(form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/me/password", { method: "POST", body: { current: form.current.value, next: form.next.value } });
      form.reset();
      toast("Passwort geändert");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function bindAccount() {
  bindPassword(document.getElementById("password-form"));
  const start = document.getElementById("totp-start");
  if (start) {
    start.addEventListener("click", async () => {
      try {
        state.totpSetup = await api("/api/me/totp/start", { method: "POST", body: {} });
        render();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const copy = document.getElementById("totp-copy");
  if (copy && state.totpSetup) {
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(state.totpSetup.secret);
        toast("Schlüssel kopiert");
      } catch (error) {
        toast(state.totpSetup.secret);
      }
    });
  }
  const confirmForm = document.getElementById("totp-confirm");
  if (confirmForm) {
    confirmForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await api("/api/me/totp/confirm", { method: "POST", body: { code: confirmForm.code.value } });
        state.totpSetup = null;
        state.recoveryCodes = result.recovery_codes || [];
        state.user.totp_enabled = true;
        toast("2FA ist aktiv");
        render();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const disable = document.getElementById("totp-disable");
  if (disable) {
    disable.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api("/api/me/totp/disable", { method: "POST", body: { password: disable.password.value, code: disable.code.value } });
        state.user.totp_enabled = false;
        toast("2FA abgeschaltet");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const done = document.getElementById("recovery-done");
  if (done) {
    done.addEventListener("click", () => {
      state.recoveryCodes = null;
      render();
    });
  }
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

function deviceUpdateOptions() {
  return {
    buttonId: "device-update",
    logId: "device-update-log",
    stateId: "device-update-state",
    deviceId: deviceId(),
    offlineText: "Gerät ist kurz nicht erreichbar und meldet sich nach dem Neustart",
    fetchStatus: () => api(`/api/devices/${deviceId()}/update-log`, { cache: "no-store" }),
  };
}

async function loadDeviceLog() {
  const id = deviceId();
  try {
    const data = await api(`/api/devices/${id}/update-log`, { cache: "no-store" });
    if (deviceId() !== id) return;
    const button = document.getElementById("device-update");
    if (button && button.disabled && data.state !== "running") return;
    state.deviceLog = data.log || "";
    state.deviceUpdateState = data.state || "idle";
    const logEl = document.getElementById("device-update-log");
    const stateEl = document.getElementById("device-update-state");
    if (logEl) logEl.textContent = state.deviceLog;
    if (stateEl && data.state !== "running") stateEl.textContent = updateStatusText(data.state, false);
    if (data.state === "running") startUpdateWatch(deviceUpdateOptions());
  } catch (error) {
    return;
  }
}

function bindSystem() {
  const update = document.getElementById("cloud-update");
  if (update) {
    update.addEventListener("click", async () => {
      if (!confirm("Cloud-Konsole aus GitHub updaten und neu starten?")) return;
      const stateEl = document.getElementById("update-state");
      update.disabled = true;
      if (stateEl) stateEl.textContent = "Update wird gestartet";
      try {
        const result = await api("/api/system/update", { method: "POST", body: {} });
        toast(result.message);
        state.updateState = "running";
        startUpdateWatch({
          buttonId: "cloud-update",
          logId: "update-log",
          stateId: "update-state",
          page: "system",
          offlineText: "Verbindung unterbrochen, der Dienst startet neu",
          fetchStatus: () => api("/api/system/update-log", { cache: "no-store" }),
        });
      } catch (error) {
        update.disabled = false;
        if (stateEl) stateEl.textContent = updateStatusText(state.updateState, false);
        toast(error.message, true);
      }
    });
    if (state.updateState === "running") {
      startUpdateWatch({
        buttonId: "cloud-update",
        logId: "update-log",
        stateId: "update-state",
        page: "system",
        offlineText: "Verbindung unterbrochen, der Dienst startet neu",
        fetchStatus: () => api("/api/system/update-log", { cache: "no-store" }),
      });
    }
  }
  const schedule = document.getElementById("cloud-schedule");
  if (schedule) {
    schedule.addEventListener("submit", async (event) => {
      event.preventDefault();
      const runAt = new Date(schedule.run_at.value);
      if (Number.isNaN(runAt.getTime())) {
        toast("Der Zeitpunkt ist ungültig", true);
        return;
      }
      try {
        await api("/api/system/update-schedule", { method: "POST", body: { run_at: runAt.toISOString() } });
        toast("Update geplant");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  const clearSchedule = document.getElementById("cloud-schedule-clear");
  if (clearSchedule) {
    clearSchedule.addEventListener("click", async () => {
      try {
        await api("/api/system/update-schedule", { method: "DELETE" });
        toast("Planung gelöscht");
        await show();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
}

document.addEventListener("click", () => {
  const menu = document.getElementById("tenant-menu");
  const toggle = document.getElementById("tenant-toggle");
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  if (toggle) {
    toggle.setAttribute("aria-expanded", "false");
    toggle.closest(".tenant-switch").classList.remove("open");
  }
});
window.addEventListener("hashchange", () => show());
api("/api/me").then((user) => {
  state.user = user;
  return show();
}).catch(() => render());
