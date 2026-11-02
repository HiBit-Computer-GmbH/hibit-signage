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
  poweroff: false,
  addMode: "image-url",
  log: "",
  updateState: "idle",
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

function kindLabel(kind) {
  return { image: "Bild", video: "Video", web: "Webseite" }[kind] || "Inhalt";
}

function originLabel(item) {
  if (item.kind === "web") return "Webseite";
  if (item.origin === "url") return "Adresse";
  if (item.origin === "local") return "Datei";
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
    const logEl = document.getElementById(options.logId);
    const stateEl = document.getElementById(options.stateId);
    if (!logEl) return;
    if (Date.now() - started > 20 * 60 * 1000) {
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
      <div class="side-foot">
        <div class="muted">Angemeldet als ${esc(state.user.username)}</div>
        <button class="ghost" id="logout" type="button">Abmelden</button>
      </div>
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

function fitLabel(item) {
  if (item.kind === "web") return "";
  return item.fit === "cover" ? "Beschnitten" : "Ganzes Bild";
}

function fitSelect(item) {
  if (item.kind === "web") return "";
  const contain = item.fit !== "cover";
  return `<select data-field="fit" data-id="${esc(item.id)}" title="Darstellung">
    <option value="contain" ${contain ? "selected" : ""}>An Auflösung anpassen</option>
    <option value="cover" ${contain ? "" : "selected"}>Ausfüllen</option>
  </select>`;
}

function enqueueFields() {
  const mode = state.addMode || "image-url";
  if (mode === "local") {
    const options = state.files.map((file) => `<option value="${esc(file.id)}">${esc(file.name)}</option>`).join("");
    if (!options) return `<p class="muted">Noch keine Datei. Zuerst unter <a href="#/media">Medien</a> hochladen.</p>`;
    return `<div class="grid-2">
      <label>Datei<select name="file_id" required>${options}</select></label>
      <label>Sekunden<input name="duration_seconds" type="number" min="1" value="10"></label>
      <label>Scroll px/s<input name="scroll_speed" type="number" min="0" max="4000" value="0"></label>
    </div>
    <label class="check"><input type="checkbox" name="fit_screen" checked> An Auflösung anpassen</label>
    <p class="muted">Scroll px/s schiebt Bilder nach unten. 0 bleibt stehen.</p>`;
  }
  if (mode === "web") {
    return `<div class="grid-2">
      <label>Name<input name="name" placeholder="Optional"></label>
      <label>Adresse<input name="url" type="url" placeholder="https://example.com" required></label>
      <label>Sekunden<input name="duration_seconds" type="number" min="1" value="60" required></label>
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
    <label>Sekunden<input name="duration_seconds" type="number" min="1" value="${image ? "10" : ""}" placeholder="${image ? "" : "bis zum Ende"}"></label>
    ${image ? `<label>Scroll px/s<input name="scroll_speed" type="number" min="0" max="4000" value="0"></label>` : ""}
  </div>
  <label class="check"><input type="checkbox" name="fit_screen" ${image ? "checked" : ""}> An Auflösung anpassen</label>
  <p class="muted">${image ? "Das ganze Bild bleibt sichtbar. Ohne Haken füllt es den Bildschirm und wird beschnitten. Scroll px/s schiebt es nach unten, 0 bleibt stehen." : "Ohne Haken füllt das Video den Bildschirm und wird beschnitten."}</p>`;
}

function queueHtml() {
  const mode = state.addMode || "image-url";
  const modes = [["image-url", "Bild-Adresse"], ["video-url", "Video-Adresse"], ["web", "Webseite"], ["local", "Lokale Datei"]];
  const rows = state.queue.map((item, index) => `<article class="card item" draggable="true" data-id="${esc(item.id)}">
      <div class="item-body">
        <span class="handle" title="Ziehen">⋮⋮</span>
        <div>
          <div class="title">${esc(item.name)}</div>
          <div class="meta"><span class="badge">${esc(originLabel(item))}</span> <span class="badge">${esc(kindLabel(item.kind))}</span> ${fitLabel(item) ? `<span class="badge">${fitLabel(item)}</span>` : ""} ${item.enabled ? "" : '<span class="badge warn">Pausiert</span>'}</div>
        </div>
      </div>
      <div class="item-tools">
        <div class="tool-fields">
          ${item.origin === "url" ? `<label class="tool wide">Adresse<input data-field="url" data-id="${esc(item.id)}" type="url" value="${esc(item.url || "")}"></label>` : ""}
          ${item.kind === "web" ? "" : `<label class="tool">Darstellung${fitSelect(item)}</label>`}
          <label class="tool">Sekunden<input data-field="duration" data-id="${esc(item.id)}" type="number" min="1" max="86400" placeholder="Ende" value="${item.duration_seconds ?? ""}" title="Sekunden, bei Videos leer = bis zum Ende"></label>
          ${item.kind === "video" ? "" : `<label class="tool">Scroll px/s<input data-field="scroll" data-id="${esc(item.id)}" type="number" min="0" max="4000" value="${item.scroll_speed || 0}" title="Pixel pro Sekunde, 0 bleibt stehen"></label>`}
          ${item.kind === "web" ? `<label class="tool">Zoom %<input data-field="zoom" data-id="${esc(item.id)}" type="number" min="100" max="300" step="10" value="${item.zoom || 100}" title="100 ist original, höhere Werte vergrößern die Seite"></label>` : ""}
          ${item.kind === "web" ? `<label class="tool">Farbschema<select data-field="scheme" data-id="${esc(item.id)}" title="Hell oder Dunkel erzwingt das Farbschema dieser Seite"><option value="auto" ${(item.color_scheme || "auto") === "auto" ? "selected" : ""}>Automatisch</option><option value="light" ${item.color_scheme === "light" ? "selected" : ""}>Hell</option><option value="dark" ${item.color_scheme === "dark" ? "selected" : ""}>Dunkel</option></select></label>` : ""}
        </div>
        <div class="actions">
          <button class="ghost" data-action="up" data-id="${esc(item.id)}" ${index === 0 ? "disabled" : ""}>Hoch</button>
          <button class="ghost" data-action="down" data-id="${esc(item.id)}" ${index === state.queue.length - 1 ? "disabled" : ""}>Runter</button>
          <button class="ghost" data-action="toggle" data-id="${esc(item.id)}">${item.enabled ? "Pause" : "Aktiv"}</button>
          <button class="danger" data-action="delete" data-id="${esc(item.id)}">${state.confirm === item.id ? "Wirklich" : "Löschen"}</button>
        </div>
      </div>
    </article>`).join("");
  return `<div class="page-head"><div><h1>Warteschlange</h1><p class="muted">Was auf dem Bildschirm nacheinander erscheint.</p></div><a class="button ghost" href="/player/" target="_blank">Vorschau</a></div>
    <form class="card composer" id="enqueue-form">
      <div class="mode-menu">${modes.map(([id, label]) => `<button type="button" data-mode="${id}" class="${mode === id ? "active" : ""}">${label}</button>`).join("")}</div>
      ${enqueueFields()}
      ${mode === "local" && !state.files.length ? "" : `<button type="submit">In die Warteschlange</button>`}
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
        <button class="danger" data-action="delete-file" data-id="${esc(file.id)}">${state.confirm === file.id ? "Wirklich" : "Löschen"}</button>
      </div>
    </article>`).join("");
  return `<div class="page-head"><div><h1>Medien</h1><p class="muted">Dateien auf diesem Pi. Einreihen geht in der Warteschlange unter Lokale Datei.</p></div></div>
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
      <div class="spread"><h2>Inhalt</h2><div class="row"><button class="ghost" id="browse-up" type="button">Nach oben</button><span class="muted">${esc(state.path || "/")}</span></div></div>
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
      <h2>Cloud</h2>
      <label>Adresse<input name="url" value="${esc(cloud.url || "")}" placeholder="https://signage.example.com"></label>
      <label>Geräte-Schlüssel<input name="device_key" value="${esc(cloud.device_key || "")}" placeholder="HB-XXXX-XXXX-XXXX-XXXX"></label>
      <p class="muted">Leer lassen trennt die Cloud. Nach dem Speichern meldet sich das Gerät und wartet auf den Adopt.</p>
      <button type="submit">Cloud speichern</button>
    </form>
    <form class="card composer" id="splash-form">
      <h2>Boot-Logo</h2>
      <p class="muted">PNG, höchstens 1,5 MB. Es erscheint beim Linux-Start, bis die Anzeige da ist. ${info.boot_logo ? "Ein eigenes Logo ist gespeichert." : "Aktuell gilt das HiBit-Logo."} Sichtbar nach dem nächsten Neustart.</p>
      <label>Logo<input name="file" type="file" accept="image/png,.png"></label>
      <div class="row"><button type="submit">Logo speichern</button><button class="ghost" id="splash-reset" type="button">Standardlogo</button></div>
    </form>
    <form class="card composer" id="password-form">
      <h2>Passwort</h2>
      <div class="grid-2">
        <label>Aktuell<input name="current" type="password" required></label>
        <label>Neu<input name="next" type="password" minlength="8" required></label>
      </div>
      <button type="submit">Passwort ändern</button>
    </form>
    <section class="card panel" id="power-panel">
      <h2>Strom</h2>
      <p class="muted">Neustart startet den Pi neu. Herunterfahren schaltet ihn aus, bis jemand ihn wieder einschaltet.</p>
      <div class="row">
        <button class="ghost" id="reboot" type="button">Neustart</button>
        <button class="danger" id="poweroff" type="button">Herunterfahren</button>
      </div>
      ${state.poweroff ? `<div class="confirm-box"><p>Zweite Bestätigung: der Pi wird jetzt ausgeschaltet.</p><div class="row"><button class="danger" id="poweroff-now" type="button">Wirklich herunterfahren</button><button class="ghost" id="poweroff-cancel" type="button">Abbrechen</button></div></div>` : ""}
    </section>
    <section class="card panel">
      <div><div class="title">Update aus GitHub</div><p class="muted">Zieht den aktuellen Stand und startet den Player neu. Der Verlauf erscheint hier, danach lädt die Seite neu.</p></div>
      <div class="actions"><button id="update" type="button">Update</button><button class="ghost" id="restart-display" type="button">Anzeige neu starten</button></div>
      <p class="muted" id="update-state">${esc(updateStatusText(state.updateState, false))}</p>
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
  if (current !== "system") state.poweroff = false;
  const pages = { queue: queueHtml, media: mediaHtml, shares: sharesHtml, terminal: () => `<div class="page-head"><div><h1>Terminal</h1><p class="muted">Shell auf diesem Gerät. Es ist nur eine Sitzung gleichzeitig möglich.</p></div></div><section class="term-frame"><div class="term-bar"><span>Shell</span><span>Strg+C bricht ab</span></div><div id="term"></div></section>`, system: systemHtml };
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
  if (current === "system") {
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

function bindQueue() {
  const form = document.getElementById("enqueue-form");
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
      await api("/api/queue", { method: "POST", body });
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
    const fit = event.target.closest("[data-field='fit']");
    const scroll = event.target.closest("[data-field='scroll']");
    const zoom = event.target.closest("[data-field='zoom']");
    const scheme = event.target.closest("[data-field='scheme']");
    const address = event.target.closest("[data-field='url']");
    try {
      if (input) {
        await api(`/api/queue/${input.dataset.id}`, { method: "PATCH", body: { duration_seconds: input.value } });
        toast("Dauer gespeichert");
      }
      if (address) {
        await api(`/api/queue/${address.dataset.id}`, { method: "PATCH", body: { url: address.value } });
        toast("Adresse gespeichert");
      }
      if (scroll) {
        await api(`/api/queue/${scroll.dataset.id}`, { method: "PATCH", body: { scroll_speed: scroll.value } });
        toast(Number(scroll.value) > 0 ? "Scrollgeschwindigkeit gespeichert" : "Scroll aus");
      }
      if (zoom) {
        await api(`/api/queue/${zoom.dataset.id}`, { method: "PATCH", body: { zoom: zoom.value } });
        toast(Number(zoom.value) > 100 ? `Zoom ${zoom.value} % gespeichert` : "Zoom auf Originalgröße");
      }
      if (scheme) {
        const label = scheme.value === "dark" ? "Dunkel" : scheme.value === "light" ? "Hell" : "Automatisch";
        await api(`/api/queue/${scheme.dataset.id}`, { method: "PATCH", body: { color_scheme: scheme.value } });
        toast(`Farbschema ${label}`);
      }
      if (fit) {
        await api(`/api/queue/${fit.dataset.id}`, { method: "PATCH", body: { fit: fit.value } });
        toast(fit.value === "contain" ? "Bild wird vollständig gezeigt" : "Bild füllt den Bildschirm");
        await show();
      }
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
          body: { origin: "share", share_id: state.shareId, share_path: button.dataset.path, kind: button.dataset.kind, duration_seconds: button.dataset.kind === "image" ? 10 : "", fit: button.dataset.kind === "image" ? "contain" : "cover" },
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
  document.getElementById("splash-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = event.currentTarget.file.files[0];
    if (!file) {
      toast("Bitte ein PNG wählen", true);
      return;
    }
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api("/api/system/boot-logo", { method: "POST", body });
      toast(result.message || "Logo gespeichert");
      await show();
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("splash-reset").addEventListener("click", async () => {
    try {
      const result = await api("/api/system/boot-logo", { method: "DELETE" });
      toast(result.message || "Standardlogo wiederhergestellt");
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
    const button = document.getElementById("update");
    const stateEl = document.getElementById("update-state");
    button.disabled = true;
    if (stateEl) stateEl.textContent = "Update wird gestartet";
    try {
      const result = await api("/api/system/update", { method: "POST", body: {} });
      toast(result.message);
      state.updateState = "running";
      startUpdateWatch({
        buttonId: "update",
        logId: "update-log",
        stateId: "update-state",
        offlineText: "Verbindung unterbrochen, der Dienst startet neu",
        fetchStatus: () => api("/api/system/update-log", { cache: "no-store" }),
      });
    } catch (error) {
      button.disabled = false;
      if (stateEl) stateEl.textContent = updateStatusText(state.updateState, false);
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
  document.getElementById("reboot").addEventListener("click", async () => {
    if (!confirm("Den Pi neu starten?")) return;
    try {
      const result = await api("/api/system/power", { method: "POST", body: { action: "reboot" } });
      toast(result.message || "Neustart wurde gestartet");
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.getElementById("poweroff").addEventListener("click", () => {
    state.poweroff = true;
    render();
  });
  const poweroffNow = document.getElementById("poweroff-now");
  if (poweroffNow) {
    poweroffNow.addEventListener("click", async () => {
      try {
        const result = await api("/api/system/power", { method: "POST", body: { action: "poweroff", confirm: "poweroff" } });
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
  if (state.updateState === "running") {
    startUpdateWatch({
      buttonId: "update",
      logId: "update-log",
      stateId: "update-state",
      offlineText: "Verbindung unterbrochen, der Dienst startet neu",
      fetchStatus: () => api("/api/system/update-log", { cache: "no-store" }),
    });
  }
}

window.addEventListener("hashchange", () => show());

api("/api/me").then((user) => {
  state.user = user;
  return show();
}).catch(() => render());
