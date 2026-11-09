if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/player/sw.js", { scope: "/" }).catch(() => {});
}

const imageClip = document.getElementById("image-clip");
const image = document.getElementById("image");
const video = document.getElementById("video");
const webClip = document.getElementById("web-clip");
const webFrames = [...document.querySelectorAll(".web-frame")];
let webSlot = 0;
const splash = document.getElementById("splash");
const splashMessage = document.getElementById("splash-message");
const splashHint = document.querySelector("#splash .hint");
const addresses = document.getElementById("addresses");
const progress = document.getElementById("progress");

let items = [];
let index = 0;
let revision = null;
let timer = null;
let failures = 0;
let playingId = null;
let generation = 0;
let retryAt = 0;

function resetMotion(node) {
  node.classList.remove("pan");
  node.style.transition = "none";
  node.style.transform = "";
  node.style.transformOrigin = "";
  node.style.height = "";
  node.style.width = "";
  node.style.objectFit = "";
}

function report(phase, item, detail) {
  const body = {
    phase,
    name: item && item.name ? item.name : "",
    kind: item && item.kind ? item.kind : "",
    detail: detail || "",
  };
  fetch("/api/display/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {});
}

function hold(message) {
  addresses.hidden = true;
  splashHint.hidden = true;
  splash.hidden = false;
  splashMessage.textContent = message || "Wird geladen";
}

function reveal() {
  splash.hidden = true;
}

function tryDirect(node, item) {
  if (!item || !item.remote || node.dataset.direct === "1") return false;
  node.dataset.direct = "1";
  node.src = item.remote;
  return true;
}

function giveUp(node) {
  if (node.dataset.gaveUp === String(generation)) return;
  node.dataset.gaveUp = String(generation);
  fail();
}

function showSplash(info, message, phase) {
  clearTimeout(timer);
  progress.hidden = true;
  progress.innerHTML = "";
  generation += 1;
  resetMotion(image);
  image.hidden = true;
  imageClip.hidden = true;
  video.hidden = true;
  video.pause();
  hideWeb();
  addresses.hidden = false;
  splashHint.hidden = false;
  splash.hidden = false;
  splashMessage.textContent = message || "Keine Inhalte in der Warteschlange";
  report(phase || "empty", items[index], splashMessage.textContent);
  const ips = info && info.ips && info.ips.length ? info.ips : [];
  const port = info && info.port ? info.port : location.port;
  if (!ips.length) {
    addresses.innerHTML = '<p class="address"><small>Webkonsole</small>Keine Netzwerkadresse</p>';
    return;
  }
  addresses.innerHTML = ips
    .map((ip) => `<p class="address"><small>Webkonsole</small>${ip}:${port}</p>`)
    .join("");
}

function hideWeb() {
  for (const frame of webFrames) {
    resetMotion(frame);
    frame.hidden = true;
    frame.onload = null;
    if (frame.getAttribute("src")) frame.removeAttribute("src");
  }
  webClip.hidden = true;
}

function stopMedia() {
  clearTimeout(timer);
  generation += 1;
  video.pause();
  resetMotion(image);
  image.hidden = true;
  imageClip.hidden = true;
  image.removeAttribute("src");
  video.hidden = true;
  video.removeAttribute("src");
  video.load();
  hideWeb();
}

function animateProgress(ms) {
  progress.hidden = false;
  progress.innerHTML = "<span></span>";
  const bar = progress.firstElementChild;
  bar.style.transition = "none";
  bar.style.width = "0%";
  requestAnimationFrame(() => {
    bar.style.transition = `width ${ms}ms linear`;
    bar.style.width = "100%";
  });
}

function fail() {
  failures += 1;
  const item = items[index];
  if (failures >= Math.max(items.length, 1)) {
    failures = 0;
    retryAt = Date.now() + 15000;
    const message = item && item.name
      ? `„${item.name}“ kann gerade nicht geladen werden`
      : "Inhalte können gerade nicht abgespielt werden";
    const visible = webFrames.some((frame) => !frame.hidden) || !image.hidden || !video.hidden;
    if (!visible) hold(message);
    report("failed", item, message);
    return;
  }
  next();
}

function next() {
  if (!items.length) {
    showSplash(window.__display, "");
    return;
  }
  index = (index + 1) % items.length;
  play();
}

function slide(node, distance, ms) {
  if (distance < 2) return;
  node.style.transition = "none";
  node.style.transform = "translateY(0)";
  void node.offsetWidth;
  node.style.transition = `transform ${ms}ms linear`;
  node.style.transform = `translateY(-${distance}px)`;
}

function startImagePan(item, ms) {
  resetMotion(image);
  const speed = Number(item && item.scroll_speed) || 0;
  if (!speed) {
    image.style.objectFit = item && item.fit === "contain" ? "contain" : "cover";
    return;
  }
  image.classList.add("pan");
  requestAnimationFrame(() => {
    const view = imageClip.clientHeight || window.innerHeight;
    const height = image.clientHeight;
    const maxShift = Math.max(0, height - view);
    const distance = Math.min(maxShift, Math.round(speed * ms / 1000));
    slide(image, distance, ms);
  });
}

function applyColorScheme(frame, item) {
  const scheme = item && item.color_scheme;
  frame.style.colorScheme = scheme === "light" || scheme === "dark" ? scheme : "normal";
}

function webSrc(item) {
  const src = item && item.src || "";
  const scheme = item && item.color_scheme;
  if (!src || (scheme !== "dark" && scheme !== "light")) return src;
  if (src.includes("__hibit_scheme=")) return src;
  return src + (src.includes("?") ? "&" : "?") + "__hibit_scheme=" + scheme;
}

function zoomFactor(item) {
  const percent = Number(item && item.zoom) || 100;
  if (!Number.isFinite(percent)) return 1;
  return Math.min(3, Math.max(1, percent / 100));
}

function hideZoomScrollbars(frame) {
  try {
    const doc = frame.contentDocument;
    if (!doc || doc.getElementById("hibit-zoom-style")) return;
    const style = doc.createElement("style");
    style.id = "hibit-zoom-style";
    style.textContent = "html{scrollbar-width:none}::-webkit-scrollbar{width:0;height:0;display:none}";
    (doc.head || doc.documentElement).appendChild(style);
  } catch (error) {}
}

function startWebPan(frame, item, ms) {
  resetMotion(frame);
  const zoom = zoomFactor(item);
  const speed = Number(item && item.scroll_speed) || 0;
  const viewH = webClip.clientHeight || window.innerHeight;
  const viewW = webClip.clientWidth || window.innerWidth;
  const distance = speed ? Math.min(30000, Math.round(speed * ms / 1000)) : 0;
  if (zoom <= 1 && distance < 2) return;
  if (zoom > 1) hideZoomScrollbars(frame);
  frame.classList.add("pan");
  frame.style.transformOrigin = "0 0";
  frame.style.width = `${Math.round(viewW / zoom)}px`;
  frame.style.height = `${Math.round((viewH + distance) / zoom)}px`;
  if (distance < 2) {
    frame.style.transform = `scale(${zoom})`;
    return;
  }
  frame.style.transition = "none";
  frame.style.transform = `translateY(0) scale(${zoom})`;
  void frame.offsetWidth;
  frame.style.transition = `transform ${ms}ms linear`;
  frame.style.transform = `translateY(-${distance}px) scale(${zoom})`;
}

function webFrameReady(frame) {
  try {
    const doc = frame.contentDocument;
    return Boolean(doc && doc.readyState === "complete" && !webLooksEmpty(frame));
  } catch (error) {
    return false;
  }
}

function reuseWeb(item, duration) {
  const src = webSrc(item);
  if (!src) return false;
  const current = webFrames[webSlot];
  const frame = [current].concat(webFrames.filter((entry) => entry !== current))
    .find((entry) => entry.getAttribute("src") === src && webFrameReady(entry));
  if (!frame) return false;
  applyColorScheme(frame, item);
  webClip.hidden = false;
  reveal();
  frame.hidden = false;
  for (const other of webFrames) {
    if (other !== frame) {
      other.hidden = true;
      resetMotion(other);
    }
  }
  webSlot = webFrames.indexOf(frame);
  startWebPan(frame, item, duration);
  failures = 0;
  report("playing", item, "");
  animateProgress(duration);
  timer = setTimeout(next, duration);
  return true;
}

function play() {
  clearTimeout(timer);
  const token = ++generation;
  const item = items[index];
  if (!item || !item.src) {
    fail();
    return;
  }
  report("loading", item, "");
  playingId = item.id;
  const fit = item.fit === "contain" ? "contain" : "cover";
  if (item.kind === "web") {
    resetMotion(image);
    image.hidden = true;
    imageClip.hidden = true;
    video.hidden = true;
    video.pause();
    const duration = (item.duration_seconds || 30) * 1000;
    if (reuseWeb(item, duration)) return;
    const frame = webFrames.find((entry) => entry.hidden) || webFrames[(webSlot + 1) % webFrames.length];
    frame.dataset.direct = "";
    frame.dataset.gaveUp = "";
    webClip.hidden = false;
    const show = () => {
      if (token !== generation) return;
      try {
        if (!frame.contentWindow || !frame.contentWindow.__hibitProxy) return;
      } catch (error) {
        return;
      }
      window.setTimeout(() => {
        if (token !== generation) return;
        clearTimeout(timer);
        if (webLooksEmpty(frame)) {
          if (webFrames[webSlot].hidden || webFrames[webSlot] === frame) {
            hold(item.name ? `„${item.name}“ bleibt leer` : "Die Seite bleibt leer");
          }
          report("blank", item, "");
          timer = setTimeout(next, duration);
          return;
        }
        failures = 0;
        reveal();
        frame.hidden = false;
        for (const other of webFrames) {
          if (other !== frame) {
            other.hidden = true;
            resetMotion(other);
          }
        }
        webSlot = webFrames.indexOf(frame);
        startWebPan(frame, item, duration);
        report("playing", item, "");
        animateProgress(duration);
        timer = setTimeout(next, duration);
      }, 1500);
    };
    frame.onload = show;
    frame.onerror = () => {
      if (token !== generation) return;
      giveUp(frame);
    };
    timer = setTimeout(() => {
      if (token !== generation) return;
      next();
    }, duration + 8000);
    applyColorScheme(frame, item);
    frame.removeAttribute("src");
    requestAnimationFrame(() => {
      if (token !== generation) return;
      frame.src = webSrc(item);
    });
    return;
  }
  hideWeb();
  if (item.kind === "video") {
    resetMotion(image);
    image.hidden = true;
    imageClip.hidden = true;
    video.dataset.direct = "";
    video.dataset.gaveUp = "";
    video.dataset.token = String(token);
    video.hidden = false;
    video.style.objectFit = fit;
    video.muted = Boolean(item.muted);
    const start = () => {
      const started = video.play();
      if (!started) return;
      started.catch(() => {
        if (video.dataset.token !== String(generation)) return;
        video.muted = true;
        const again = video.play();
        if (again) again.catch(() => {
          if (tryDirect(video, item)) return;
          giveUp(video);
        });
      });
    };
    if (video.getAttribute("src") !== item.src || !(video.readyState >= 2 && video.videoWidth)) {
      video.removeAttribute("src");
      video.load();
      video.src = item.src;
    }
    start();
    if (item.duration_seconds) {
      animateProgress(item.duration_seconds * 1000);
      timer = setTimeout(next, durationTimer(item));
    } else {
      progress.hidden = true;
    }
    return;
  }
  video.pause();
  video.hidden = true;
  image.dataset.token = String(token);
  image.dataset.direct = "";
  image.dataset.gaveUp = "";
  imageClip.hidden = false;
  image.hidden = false;
  image.style.objectFit = fit;
  const duration = (item.duration_seconds || 10) * 1000;
  const show = () => {
    if (image.dataset.token !== String(generation)) return;
    failures = 0;
    reveal();
    report("playing", item, "");
    startImagePan(item, duration);
  };
  image.onload = show;
  if (image.getAttribute("src") !== item.src || !(image.complete && image.naturalWidth)) {
    image.removeAttribute("src");
    image.src = item.src;
  }
  if (image.complete && image.naturalWidth) show();
  animateProgress(duration);
  timer = setTimeout(next, duration);
}

function durationTimer(item) {
  return (item.duration_seconds || 10) * 1000;
}

function webLooksEmpty(frame) {
  try {
    const doc = frame.contentDocument;
    if (!doc || !doc.body) return true;
    const text = (doc.body.innerText || "").replace(/\s+/g, " ").trim();
    if (text.length > 1) return false;
    return ![...doc.body.children].some((node) => !["SCRIPT", "STYLE", "LINK", "META"].includes(node.tagName));
  } catch (error) {
    return false;
  }
}

image.addEventListener("error", () => {
  if (!image.getAttribute("src") || image.dataset.token !== String(generation)) return;
  if (tryDirect(image, items[index])) return;
  giveUp(image);
});
video.addEventListener("playing", () => {
  if (video.dataset.token !== String(generation)) return;
  failures = 0;
  reveal();
  report("playing", items[index], "");
  if (!items[index] || items[index].duration_seconds || !video.duration) return;
  animateProgress(video.duration * 1000);
});
video.addEventListener("ended", () => {
  if (items[index] && !items[index].duration_seconds) next();
});
video.addEventListener("error", () => {
  if (!video.getAttribute("src") || video.dataset.token !== String(generation)) return;
  if (tryDirect(video, items[index])) return;
  giveUp(video);
});

async function poll() {
  try {
    const [list, info] = await Promise.all([
      fetch("/api/playlist").then((response) => response.json()),
      fetch("/api/display").then((response) => response.json()),
    ]);
    window.__display = info;
    if (!list.items.length) {
      items = [];
      revision = list.revision;
      playingId = null;
      retryAt = 0;
      stopMedia();
      showSplash(info, "", "empty");
      return;
    }
    const changed = list.revision !== revision;
    const current = items[index] && items[index].id;
    items = list.items;
    revision = list.revision;
    const keep = items.findIndex((item) => item.id === current);
    const nextIndex = keep >= 0 ? keep : 0;
    if (!changed && retryAt && Date.now() < retryAt) return;
    if (!changed && !retryAt && playingId && items[nextIndex] && playingId === items[nextIndex].id) {
      index = nextIndex;
      return;
    }
    index = nextIndex;
    failures = 0;
    retryAt = 0;
    play();
  } catch (error) {
    if (!items.length) showSplash(window.__display, "Player wartet auf den Dienst", "empty");
  }
}

async function postFrame() {
  if (!window.__display || !window.__display.watching) return;
  const source = !image.hidden && image.naturalWidth ? image : !video.hidden && video.videoWidth ? video : null;
  if (!source) return;
  const canvas = postFrame.canvas || (postFrame.canvas = document.createElement("canvas"));
  const width = source.videoWidth || source.naturalWidth;
  const height = source.videoHeight || source.naturalHeight;
  const scale = Math.min(1, 960 / width);
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  try {
    canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  } catch (error) {
    return;
  }
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.6));
  if (!blob) return;
  await fetch("/api/display/frame", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob }).catch(() => {});
}

poll();
setInterval(poll, 3000);
setInterval(() => { postFrame(); }, 2000);
