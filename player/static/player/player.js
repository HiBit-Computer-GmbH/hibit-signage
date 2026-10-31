const image = document.getElementById("image");
const video = document.getElementById("video");
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

function showSplash(info, message) {
  clearTimeout(timer);
  progress.hidden = true;
  progress.innerHTML = "";
  image.hidden = true;
  video.hidden = true;
  video.pause();
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

function stopMedia() {
  clearTimeout(timer);
  video.pause();
  image.removeAttribute("src");
  video.removeAttribute("src");
  video.load();
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

function play() {
  clearTimeout(timer);
  const item = items[index];
  if (!item || !item.src) {
    fail();
    return;
  }
  splash.hidden = true;
  playingId = item.id;
  const fit = item.fit === "contain" ? "contain" : "cover";
  if (item.kind === "video") {
    image.hidden = true;
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
  image.hidden = false;
  image.style.objectFit = fit;
  image.src = item.src;
  const duration = (item.duration_seconds || 10) * 1000;
  animateProgress(duration);
  timer = setTimeout(next, duration);
}

image.addEventListener("load", () => {
  failures = 0;
});
image.addEventListener("error", () => {
  if (!image.hidden) fail();
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
