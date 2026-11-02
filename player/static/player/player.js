if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/player/sw.js", { scope: "/" }).catch(() => {});
}

const imageClip = document.getElementById("image-clip");
const image = document.getElementById("image");
const video = document.getElementById("video");
const webClip = document.getElementById("web-clip");
const web = document.getElementById("web");
const splash = document.getElementById("splash");
const splashMessage = document.getElementById("splash-message");
const addresses = document.getElementById("addresses");
const progress = document.getElementById("progress");

let items = [];
let index = 0;
let revision = null;
let timer = null;
let failures = 0;
let playingId = null;
let generation = 0;

function resetMotion(node) {
  node.classList.remove("pan");
  node.style.transition = "none";
  node.style.transform = "";
  node.style.height = "";
  node.style.width = "";
  node.style.objectFit = "";
}

function showSplash(info, message) {
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
  splash.hidden = false;
  splashMessage.textContent = message || "Keine Inhalte in der Warteschlange";
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
  resetMotion(web);
  web.hidden = true;
  webClip.hidden = true;
  if (web.getAttribute("src")) web.removeAttribute("src");
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
  if (failures >= Math.max(items.length, 1)) {
    failures = 0;
    showSplash(window.__display, "Inhalte können gerade nicht abgespielt werden");
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
  requestAnimationFrame(() => {
    node.style.transition = `transform ${ms}ms linear`;
    node.style.transform = `translateY(-${distance}px)`;
  });
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

function startWebPan(item, ms) {
  resetMotion(web);
  const speed = Number(item && item.scroll_speed) || 0;
  if (!speed) return;
  const view = webClip.clientHeight || window.innerHeight;
  const distance = Math.min(30000, Math.round(speed * ms / 1000));
  web.classList.add("pan");
  web.style.height = `${view + distance}px`;
  slide(web, distance, ms);
}

function play() {
  clearTimeout(timer);
  const token = ++generation;
  const item = items[index];
  if (!item || !item.src) {
    fail();
    return;
  }
  splash.hidden = true;
  playingId = item.id;
  const fit = item.fit === "contain" ? "contain" : "cover";
  if (item.kind === "web") {
    resetMotion(image);
    image.hidden = true;
    imageClip.hidden = true;
    video.hidden = true;
    video.pause();
    webClip.hidden = false;
    web.hidden = false;
    const duration = (item.duration_seconds || 30) * 1000;
    startWebPan(item, duration);
    if (web.getAttribute("src") !== item.src) web.src = item.src;
    animateProgress(duration);
    timer = setTimeout(next, duration);
    failures = 0;
    return;
  }
  hideWeb();
  if (item.kind === "video") {
    resetMotion(image);
    image.hidden = true;
    imageClip.hidden = true;
    video.hidden = false;
    video.style.objectFit = fit;
    video.muted = Boolean(item.muted);
    if (video.getAttribute("src") !== item.src) video.src = item.src;
    const started = video.play();
    if (started) started.catch(() => fail());
    if (item.duration_seconds) {
      animateProgress(item.duration_seconds * 1000);
      timer = setTimeout(next, item.duration_seconds * 1000);
    } else {
      progress.hidden = true;
    }
    return;
  }
  video.pause();
  video.hidden = true;
  image.dataset.token = String(token);
  imageClip.hidden = false;
  image.hidden = false;
  image.style.objectFit = fit;
  const duration = (item.duration_seconds || 10) * 1000;
  const show = () => {
    if (image.dataset.token !== String(generation)) return;
    failures = 0;
    startImagePan(item, duration);
  };
  image.onload = show;
  if (image.getAttribute("src") !== item.src) image.src = item.src;
  if (image.complete && image.naturalWidth) show();
  animateProgress(duration);
  timer = setTimeout(next, duration);
}

image.addEventListener("error", () => {
  if (image.hidden || image.dataset.token !== String(generation)) return;
  fail();
});
video.addEventListener("playing", () => {
  failures = 0;
  if (!items[index] || items[index].duration_seconds || !video.duration) return;
  animateProgress(video.duration * 1000);
});
video.addEventListener("ended", () => {
  if (items[index] && !items[index].duration_seconds) next();
});
video.addEventListener("error", () => {
  if (!video.hidden) fail();
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
      stopMedia();
      showSplash(info, "");
      return;
    }
    const changed = list.revision !== revision;
    if (changed || splash.hidden === false) {
      const current = items[index] && items[index].id;
      items = list.items;
      revision = list.revision;
      const keep = items.findIndex((item) => item.id === current);
      index = keep >= 0 ? keep : 0;
      if (changed || playingId !== items[index].id || !splash.hidden) {
        failures = 0;
        play();
      }
    }
  } catch (error) {
    if (!items.length) showSplash(window.__display, "Player wartet auf den Dienst");
  }
}

poll();
setInterval(poll, 3000);
