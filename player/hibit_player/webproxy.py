"""Holt Webseiten für den Player, damit sie auch ohne Iframe-Freigabe laufen.

Der Kiosk bleibt auf der Player-Seite. Bilder, Videos, Scroll und die
Warteschlange ändern sich dadurch nicht. Nur Webseiten gehen über diesen Weg.
"""

from __future__ import annotations

import asyncio
import contextvars
import gzip
import json
import logging
import re
import os
import threading
import time
import urllib.error
import urllib.request
import zlib
from http.cookiejar import LWPCookieJar
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from hibit_player import db
from hibit_player.config import data_dir, port

log = logging.getLogger("hibit.player.web")

_BROWSER = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
def _cookie_path():
    return data_dir() / "web-cookies.txt"


def _open_jar() -> LWPCookieJar:
    path = _cookie_path()
    jar = LWPCookieJar(str(path))
    if path.is_file():
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            log.warning("Gespeicherte Web-Cookies konnten nicht gelesen werden")
    return jar


def _save_cookies() -> None:
    path = _cookie_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _JAR.save(ignore_discard=True, ignore_expires=True)
        os.chmod(path, 0o600)
    except Exception:
        log.debug("Web-Cookies konnten nicht gespeichert werden", exc_info=True)


_JAR = _open_jar()
_JAR_LOCK = threading.Lock()
_EXTRA: dict[str, float] = {}
_HOSTS: tuple[float, set[str]] = (0.0, set())
_HOSTS_LOCK = threading.Lock()
_COLOR: contextvars.ContextVar[str] = contextvars.ContextVar("hibit_color", default="")
_COLOR_QUERY = re.compile(r"prefers-color-scheme\s*:\s*(dark|light)", re.I)
_REWRITE_LIMIT = 8 * 1024 * 1024
_BODY_LIMIT = 32 * 1024 * 1024
_PASS_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-range",
    "etag",
    "expires",
    "last-modified",
    "vary",
}

_BOOTSTRAP = """
(function () {
  const REAL = __REAL__;
  const ORIGIN = location.origin;
  const FORCE = __SCHEME__;
  window.__hibitProxy = true;
  function withScheme(value) {
    if (FORCE !== "dark" && FORCE !== "light") return value;
    try {
      const parsed = new URL(value, ORIGIN);
      if (parsed.origin !== ORIGIN || parsed.pathname.indexOf("/player/site/") !== 0) return value;
      if (parsed.searchParams.get("__hibit_scheme") === FORCE) return value;
      parsed.searchParams.set("__hibit_scheme", FORCE);
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (e) {
      return value;
    }
  }
  function already(value) {
    try {
      const parsed = new URL(value, ORIGIN);
      return parsed.origin === ORIGIN && parsed.pathname.indexOf("/player/site/") === 0;
    } catch (e) {
      return false;
    }
  }
  function proxied(value) {
    if (value == null) return value;
    const raw = String(value);
    if (!raw || /^(data:|blob:|javascript:|mailto:|tel:|about:|#)/i.test(raw)) return raw;
    if (already(raw)) return withScheme(raw);
    let abs;
    try { abs = new URL(raw, REAL); } catch (e) { return raw; }
    const scheme = abs.protocol.replace(":", "");
    if (scheme !== "http" && scheme !== "https" && scheme !== "ws" && scheme !== "wss") return raw;
    const path = abs.pathname.split("/").filter(Boolean).join("/");
    const base = scheme === "ws" || scheme === "wss" ? ORIGIN.replace(/^http/, "ws") : ORIGIN;
    return withScheme(base + "/player/site/" + scheme + "/" + abs.host + "/" + path + abs.search + abs.hash);
  }
  function visible(value) {
    if (value == null) return value;
    const raw = String(value);
    if (!raw || /^(data:|blob:|javascript:|mailto:|tel:|about:|#)/i.test(raw)) return raw;
    let abs;
    let real;
    try {
      abs = new URL(raw, REAL);
      real = new URL(REAL);
    } catch (e) {
      return proxied(raw);
    }
    if (abs.origin === ORIGIN && abs.pathname.indexOf("/player/site/") !== 0 && !playerPath(abs.pathname)) {
      return (abs.pathname || "/") + abs.search + abs.hash;
    }
    if (abs.origin !== real.origin) return proxied(raw);
    return (abs.pathname || "/") + abs.search + abs.hash;
  }
  function playerPath(pathname) {
    return pathname === "/"
      || pathname === "/player" || pathname.indexOf("/player/") === 0
      || pathname === "/console" || pathname.indexOf("/console/") === 0
      || pathname.indexOf("/api/") === 0
      || pathname.indexOf("/media/") === 0;
  }
  try {
    Object.defineProperty(window, "top", { configurable: true, get: function () { return window; } });
    Object.defineProperty(window, "parent", { configurable: true, get: function () { return window; } });
    Object.defineProperty(window, "frameElement", { configurable: true, get: function () { return null; } });
  } catch (e) {}
  try {
    if (!delete Navigator.prototype.serviceWorker) throw new Error("keep");
  } catch (e) {
    const stub = {
      addEventListener: function () {},
      removeEventListener: function () {},
      register: function () { return Promise.reject(new Error("disabled")); },
      getRegistration: function () { return Promise.resolve(undefined); },
      getRegistrations: function () { return Promise.resolve([]); },
      controller: null,
      ready: Promise.resolve({ active: null, installing: null, waiting: null })
    };
    try {
      Object.defineProperty(navigator, "serviceWorker", { configurable: true, get: function () { return stub; } });
    } catch (e2) {}
  }
  if (window.fetch) {
    const orig = window.fetch;
    window.fetch = function (input, init) {
      try {
        if (typeof input === "string" || input instanceof URL) return orig.call(this, proxied(String(input)), init);
        if (window.Request && input instanceof Request) return orig.call(this, new Request(proxied(input.url), input), init);
      } catch (e) {}
      return orig.call(this, input, init);
    };
  }
  const xhrOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function () {
    var args = Array.prototype.slice.call(arguments);
    if (args.length > 1) args[1] = proxied(args[1]);
    return xhrOpen.apply(this, args);
  };
  if (window.EventSource) {
    const NativeES = window.EventSource;
    function PatchedES(url, conf) { return new NativeES(proxied(url), conf); }
    PatchedES.prototype = NativeES.prototype;
    window.EventSource = PatchedES;
  }
  if (window.WebSocket) {
    const NativeWS = window.WebSocket;
    function PatchedWS(url, protocols) { return new NativeWS(proxied(url), protocols); }
    PatchedWS.prototype = NativeWS.prototype;
    PatchedWS.CONNECTING = NativeWS.CONNECTING;
    PatchedWS.OPEN = NativeWS.OPEN;
    PatchedWS.CLOSING = NativeWS.CLOSING;
    PatchedWS.CLOSED = NativeWS.CLOSED;
    window.WebSocket = PatchedWS;
  }
  if (navigator.sendBeacon) {
    const beacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function (url, data) { return beacon(proxied(url), data); };
  }
  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    const key = String(name).toLowerCase();
    if (typeof value === "string" && (key === "src" || key === "href" || key === "action" || key === "poster" || key === "formaction")) {
      value = proxied(value);
    }
    return setAttribute.call(this, name, value);
  };
  function patch(proto, prop) {
    if (!proto) return;
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set || !desc.get) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set: function (value) {
        desc.set.call(this, typeof value === "string" ? proxied(value) : value);
      }
    });
  }
  [window.HTMLImageElement, window.HTMLScriptElement, window.HTMLIFrameElement, window.HTMLSourceElement, window.HTMLMediaElement, window.HTMLEmbedElement, window.HTMLObjectElement].forEach(function (ctor) {
    if (ctor) patch(ctor.prototype, "src");
  });
  if (window.HTMLObjectElement) patch(HTMLObjectElement.prototype, "data");
  if (window.HTMLLinkElement) patch(HTMLLinkElement.prototype, "href");
  if (window.HTMLAnchorElement) patch(HTMLAnchorElement.prototype, "href");
  if (window.HTMLFormElement) patch(HTMLFormElement.prototype, "action");
  const pushState = history.pushState;
  const replaceState = history.replaceState;
  try {
    const wanted = visible(REAL);
    const now = location.pathname + location.search + location.hash;
    if (wanted && wanted !== now) replaceState.call(history, history.state, "", wanted);
  } catch (e) {}
  history.pushState = function (state, title, url) {
    return pushState.call(this, state, title, url == null ? url : visible(url));
  };
  history.replaceState = function (state, title, url) {
    return replaceState.call(this, state, title, url == null ? url : visible(url));
  };
  try {
    const assign = Location.prototype.assign;
    const replace = Location.prototype.replace;
    Location.prototype.assign = function (url) { return assign.call(this, proxied(url)); };
    Location.prototype.replace = function (url) { return replace.call(this, proxied(url)); };
    const hrefDesc = Object.getOwnPropertyDescriptor(Location.prototype, "href");
    if (hrefDesc && hrefDesc.set && hrefDesc.configurable) {
      Object.defineProperty(Location.prototype, "href", {
        configurable: true,
        enumerable: hrefDesc.enumerable,
        get: hrefDesc.get,
        set: function (value) { hrefDesc.set.call(this, proxied(value)); }
      });
    }
  } catch (e) {}
  window.open = function (url) {
    if (url) location.assign(proxied(url));
    return null;
  };
  if (window.navigation && window.navigation.addEventListener) {
    window.navigation.addEventListener("navigate", function (event) {
      const url = event.destination && event.destination.url || "";
      if (!url || /^(about:|blob:|data:)/i.test(url)) return;
      let dest = null;
      try { dest = new URL(url); } catch (e) { return; }
      if (dest.origin === ORIGIN && dest.pathname.indexOf("/player/site/") !== 0) {
        if (!event.destination || event.destination.sameDocument !== false) return;
        event.preventDefault();
        if (playerPath(dest.pathname)) return;
        const next = proxied(dest.pathname + dest.search + dest.hash);
        if (next && next !== location.href) location.replace(next);
        return;
      }
      if (url.indexOf("/player/site/") !== -1) {
        const kept = withScheme(url);
        if (kept !== url) {
          event.preventDefault();
          location.replace(kept);
        }
        return;
      }
      event.preventDefault();
      if (/adblockwall|adblock[.]html|blockadblock/i.test(url)) return;
      const next = proxied(url);
      if (next && next !== location.href) location.replace(next);
    });
  }
  if (FORCE === "dark" || FORCE === "light") {
    const wanted = FORCE === "dark" ? /prefers-color-scheme\\s*:\\s*dark/ig : /prefers-color-scheme\\s*:\\s*light/ig;
    const other = FORCE === "dark" ? /prefers-color-scheme\\s*:\\s*light/ig : /prefers-color-scheme\\s*:\\s*dark/ig;
    function paint(text) {
      return String(text).replace(wanted, "min-width: 0px").replace(other, "prefers-color-scheme: __hibit_off__");
    }
    const nativeMatch = window.matchMedia.bind(window);
    window.matchMedia = function (query) {
      const raw = String(query || "");
      const flat = raw.toLowerCase().replace(/\\s+/g, "");
      if (flat.indexOf("prefers-color-scheme") === -1) return nativeMatch(raw);
      const wantsDark = flat.indexOf("prefers-color-scheme:dark") !== -1;
      const wantsLight = flat.indexOf("prefers-color-scheme:light") !== -1;
      let matches;
      if (wantsDark && !wantsLight) matches = FORCE === "dark";
      else if (wantsLight && !wantsDark) matches = FORCE === "light";
      else matches = nativeMatch(raw).matches;
      if (/^\\s*not\\b/i.test(raw)) matches = !matches;
      const list = nativeMatch(matches ? "(min-width: 0px)" : "(min-width: 999999px)");
      try { Object.defineProperty(list, "media", { configurable: true, get: function () { return raw; } }); } catch (e) {}
      return list;
    };
    const insertRule = CSSStyleSheet.prototype.insertRule;
    CSSStyleSheet.prototype.insertRule = function (rule, index) {
      if (arguments.length > 1) return insertRule.call(this, paint(rule), index);
      return insertRule.call(this, paint(rule));
    };
    if (CSSStyleSheet.prototype.replaceSync) {
      const replaceSync = CSSStyleSheet.prototype.replaceSync;
      CSSStyleSheet.prototype.replaceSync = function (text) { return replaceSync.call(this, paint(text)); };
    }
    const watch = new MutationObserver(function (records) {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (!node || node.nodeType !== 1 || node.tagName !== "STYLE" || node.dataset.hibitPainted) continue;
          const next = paint(node.textContent || "");
          if (next !== node.textContent) {
            node.dataset.hibitPainted = "1";
            node.textContent = next;
          }
        }
      }
    });
    watch.observe(document.documentElement || document, { childList: true, subtree: true });
    document.addEventListener("DOMContentLoaded", function () {
      try { document.documentElement.style.colorScheme = FORCE; } catch (e) {}
    });
  }
  const consentCss = "#onetrust-consent-sdk,#onetrust-banner-sdk,#usercentrics-root,#CybotCookiebotDialog,#CybotCookiebotDialogBodyUnderlay,.cc-window,.cc-banner,#cookie-law-info-bar,.cky-consent-container,.cky-modal,#borlabs-cookie,#BorlabsCookieBox,.brlbs-cmpnt-dialog,#cmplz-cookiebanner-container,.cmplz-cookiebanner,.fc-consent-root,.fc-dialog-overlay,#didomi-host,#didomi-notice,#qc-cmp2-container,.qc-cmp2-container,#cmpbox,#cmpbox2,#cookie-notice,.cookie-notice-container,#moove_gdpr_cookie_info_bar,.sp_message_container,#sp_message_container_id,.rcb-banner,.rcb-dialog,#real-cookie-banner{display:none!important}";
  function dismissConsent() {
    if (!document.getElementById("hibit-consent-style") && document.head) {
      const style = document.createElement("style");
      style.id = "hibit-consent-style";
      style.textContent = consentCss;
      document.head.appendChild(style);
    }
    const reject = /ohne einwilligung|alle ablehnen|ablehnen|reject all|decline|nur notwendige|nur essenzielle|nur erforderliche|continue without|deny all/i;
    const accept = /alle akzeptieren|alles akzeptieren|accept all|zustimmen|einverstanden|allow all/i;
    const nodes = document.querySelectorAll("button, a, [role='button'], input[type='button'], input[type='submit']");
    let choice = null;
    for (const el of nodes) {
      const text = String(el.innerText || el.value || el.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim();
      if (!text || text.length > 80) continue;
      if (reject.test(text)) { choice = el; break; }
      if (!choice && accept.test(text)) choice = el;
    }
    if (choice) choice.click();
  }
  let consentTries = 0;
  const consentTimer = setInterval(function () {
    consentTries += 1;
    try { dismissConsent(); } catch (e) {}
    if (consentTries > 24) clearInterval(consentTimer);
  }, 500);
  document.addEventListener("DOMContentLoaded", function () {
    try { dismissConsent(); } catch (e) {}
  });
})();
"""


class _Redirect(Exception):
    def __init__(self, code: int, url: str):
        self.code = code
        self.url = url


class _HeaderBag:
    def __init__(self, headers):
        self._headers = headers

    def info(self):
        return self._headers


class _StopRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            fp.close()
        except Exception:
            pass
        try:
            _JAR.extract_cookies(_HeaderBag(headers), req)
        except Exception:
            log.debug("Cookies der Weiterleitung konnten nicht gelesen werden", exc_info=True)
        raise _Redirect(code, newurl)


def reset_runtime() -> None:
    """Leert Merkliste und Cookies. Nur für Tests."""
    global _HOSTS
    with _JAR_LOCK:
        _JAR.clear()
        _save_cookies()
        _EXTRA.clear()
        _HOSTS = (0.0, set())


def proxy_path(url: str) -> str | None:
    parsed = urlsplit((url or "").strip())
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        return None
    if not _safe_host(parsed.netloc):
        return None
    path = quote(unquote(parsed.path.lstrip("/")), safe="/:@!$&'()*+,;=~")
    query = f"?{parsed.query}" if parsed.query else ""
    frag = f"#{parsed.fragment}" if parsed.fragment else ""
    return _with_scheme(f"/player/site/{parsed.scheme}/{parsed.netloc}/{path}{query}{frag}", _COLOR.get())


def reject_target(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        return "Die Adresse ist ungültig."
    host = parsed.hostname.lower().strip("[]")
    try:
        remote_port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    except ValueError:
        return "Die Adresse ist ungültig."
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} and remote_port == port():
        return "Diese Adresse kann der Player nicht laden."
    if host in {"169.254.169.254", "metadata.google.internal"}:
        return "Diese Adresse ist nicht erlaubt."
    return None


def rewrite_url(value: str, base: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.startswith(("#", "data:", "blob:", "javascript:", "mailto:", "tel:", "about:")):
        return value
    if "/player/site/" in raw:
        return value
    absolute = urljoin(base, raw)
    proxied = proxy_path(absolute)
    return proxied or value


def rewrite_css(text: str, base: str) -> str:
    def url_repl(match: re.Match) -> str:
        quote_char = match.group(1) or ""
        target = match.group(2).strip()
        return f"url({quote_char}{rewrite_url(target, base)}{quote_char})"

    text = re.sub(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", url_repl, text, flags=re.I)
    text = re.sub(
        r"""@import\s+(['"])([^'"]+)\1""",
        lambda match: f"@import {match.group(1)}{rewrite_url(match.group(2), base)}{match.group(1)}",
        text,
        flags=re.I,
    )
    return force_color_scheme(text, _COLOR.get())


def force_color_scheme(text: str, scheme: str) -> str:
    if scheme not in {"dark", "light"}:
        return text

    def repl(match: re.Match) -> str:
        if match.group(1).lower() == scheme:
            return "min-width: 0px"
        return "prefers-color-scheme: __hibit_off__"

    return _COLOR_QUERY.sub(repl, text)


def _with_scheme(url: str, scheme: str) -> str:
    if scheme not in {"dark", "light"} or "__hibit_scheme=" in url:
        return url
    base, sep, frag = url.partition("#")
    join = "&" if "?" in base else "?"
    return f"{base}{join}__hibit_scheme={scheme}" + (f"#{frag}" if sep else "")


def _split_query(query: str) -> tuple[str, str]:
    kept = []
    color = ""
    for bit in (query or "").split("&"):
        if not bit:
            continue
        name, _sep, value = bit.partition("=")
        if name == "__hibit_scheme":
            raw = unquote(value)
            if raw in {"dark", "light"} and not color:
                color = raw
            continue
        kept.append(bit)
    return "&".join(kept), color


def rewrite_html(html: str, page_url: str) -> str:
    base = _document_base(html, page_url)
    html = _rewrite_meta(html, base)
    html = _rewrite_attrs(html, base, ("src", "href", "action", "poster", "formaction"))
    html = _rewrite_srcset(html, base)
    html = _rewrite_object_data(html, base)
    html = _rewrite_style_attrs(html, base)
    html = re.sub(
        r"(<style\b[^>]*>)(.*?)(</style>)",
        lambda match: match.group(1) + rewrite_css(match.group(2), base) + match.group(3),
        html,
        flags=re.I | re.S,
    )
    real = json.dumps(base).replace("<", "\\u003c")
    script = _BOOTSTRAP.replace("__REAL__", real).replace("__SCHEME__", json.dumps(_COLOR.get() if _COLOR.get() in {"dark", "light"} else ""))
    html = _keep_referrer(html)
    return f'<script data-hibit-proxy="1">{script}</script>{html}'


def build_upstream(scheme: str, host: str, path: str, query: str) -> str:
    if scheme not in {"http", "https", "ws", "wss"} or not _safe_host(host):
        raise ValueError("invalid")
    host = _idn(host)
    quoted = quote(path or "", safe="/:@!$&'()*+,;=~")
    url = f"{scheme}://{host}/{quoted}" if quoted else f"{scheme}://{host}/"
    if query:
        url = f"{url}?{query}"
    return url


def authorize(target: str, referer: str | None, *, follow: bool = False) -> str | None:
    reason = reject_target(target)
    if reason:
        return reason
    host = _hostname(urlparse(target).netloc)
    if follow or _listed(host) or _known(host) or _from_player(referer or ""):
        _remember(host)
        return None
    ref_host = _referer_host(referer or "")
    if ref_host and (_known(ref_host) or _listed(ref_host)):
        _remember(host)
        return None
    return "Diese Adresse ist nicht in der Warteschlange."


async def handle(scheme: str, host: str, path: str, request: Request) -> Response:
    query, color = _split_query(request.url.query)
    token = _COLOR.set(color)
    try:
        try:
            target = build_upstream(scheme, host, path, query)
        except ValueError:
            return _plain("Die Adresse ist ungültig.", 400)
        if scheme not in {"http", "https"}:
            return _plain("Die Adresse ist ungültig.", 400)
        reason = authorize(target, request.headers.get("referer"))
        if reason:
            return _plain(reason, 403)
        body = await request.body()
        if len(body) > _BODY_LIMIT:
            return _plain("Die Anfrage ist zu groß.", 413)
        headers = {key.lower(): value for key, value in request.headers.items()}
        try:
            return await asyncio.to_thread(_fetch, request.method.upper(), target, headers, body)
        except Exception:
            log.warning("Webseite nicht erreichbar: %s", urlparse(target).netloc)
            return _plain("Die Seite ist nicht erreichbar.", 502)
    finally:
        _COLOR.reset(token)


async def handle_ws(ws: WebSocket, scheme: str, host: str, path: str) -> None:
    if scheme not in {"ws", "wss"}:
        await ws.close(code=1008)
        return
    query, _color = _split_query(ws.scope.get("query_string", b"").decode("latin1"))
    http_scheme = "https" if scheme == "wss" else "http"
    try:
        target = build_upstream(http_scheme, host, path, query)
    except ValueError:
        await ws.close(code=1008)
        return
    if authorize(target, ws.headers.get("referer")):
        await ws.close(code=1008)
        return
    ws_target = scheme + target[len(http_scheme) :]
    await ws.accept()
    headers = {"Origin": f"{http_scheme}://{urlparse(target).netloc}", "User-Agent": ws.headers.get("user-agent") or _BROWSER}
    cookie = _cookie_header(target)
    if cookie:
        headers["Cookie"] = cookie
    try:
        import websockets
    except ImportError:
        await ws.close(code=1011)
        return
    try:
        kwargs = {"additional_headers": headers, "open_timeout": 15, "max_size": _REWRITE_LIMIT}
        try:
            remote_cm = websockets.connect(ws_target, **kwargs)
        except TypeError:
            remote_cm = websockets.connect(ws_target, extra_headers=headers, open_timeout=15, max_size=_REWRITE_LIMIT)
        async with remote_cm as remote:
            await _pump_ws(ws, remote)
    except Exception:
        log.warning("WebSocket nicht erreichbar: %s", urlparse(target).netloc)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


def _fetch(method: str, target: str, headers: dict, body: bytes) -> Response:
    upstream = {
        "User-Agent": headers.get("user-agent") or _BROWSER,
        "Accept": headers.get("accept") or "*/*",
        "Accept-Language": headers.get("accept-language") or "de-DE,de;q=0.9,en;q=0.8",
    }
    for source, dest in (
        ("content-type", "Content-Type"),
        ("range", "Range"),
    ):
        if headers.get(source):
            upstream[dest] = headers[source]
    referer = referer_to_upstream(headers.get("referer") or "")
    if referer:
        upstream["Referer"] = referer
    payload = body if method in {"POST", "PUT", "PATCH", "DELETE"} and body else None
    req = urllib.request.Request(target, data=payload, headers=upstream, method=method)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR), _StopRedirect())
    redirected: _Redirect | None = None
    with _JAR_LOCK:
        try:
            response = opener.open(req, timeout=25)
        except _Redirect as exc:
            response = None
            redirected = exc
        except urllib.error.HTTPError as exc:
            try:
                _JAR.extract_cookies(exc, req)
            except Exception:
                log.debug("Cookies konnten nicht gelesen werden", exc_info=True)
            response = exc
        _save_cookies()
    if redirected is not None:
        return _redirect(redirected, target)
    status = getattr(response, "status", None) or getattr(response, "code", 200)
    info = response.info()
    content_type = info.get("Content-Type") or ""
    encoding = (info.get("Content-Encoding") or "").lower()
    kind = _kind(content_type)
    if method == "HEAD" or status in {204, 304}:
        response.close()
        return Response(status_code=status, headers=_pass_headers(info, content_type, rewritten=False))
    if kind and _small_enough(info):
        page = target
        getter = getattr(response, "geturl", None)
        if getter:
            try:
                page = getter() or target
            except Exception:
                page = target
        raw = response.read(_REWRITE_LIMIT + 1)
        response.close()
        if len(raw) <= _REWRITE_LIMIT:
            text = _text(raw, encoding, content_type)
            if len(text) > _REWRITE_LIMIT:
                return _plain("Die Seite ist zu groß.", 502)
            if kind == "html":
                text = rewrite_html(text, page or target)
                media = "text/html; charset=utf-8"
            else:
                text = rewrite_css(text, page or target)
                media = "text/css; charset=utf-8"
            header_map = _pass_headers(info, media, rewritten=True)
            return Response(text.encode("utf-8"), status_code=status, headers=header_map, media_type=media)
        return _plain("Die Seite ist zu groß.", 502)
    stream = response
    if encoding in {"gzip", "x-gzip"}:
        stream = gzip.GzipFile(fileobj=response)
        encoding = ""
    header_map = _pass_headers(info, content_type, rewritten=False)
    if info.get("Content-Length") and not encoding and stream is response:
        header_map["Content-Length"] = info.get("Content-Length")
    if encoding:
        header_map["Content-Encoding"] = encoding
    return StreamingResponse(_chunks(stream, response), status_code=status, headers=header_map)


def referer_to_upstream(referer: str) -> str | None:
    parsed = urlparse(referer)
    parts = parsed.path.split("/")
    if len(parts) < 5 or parts[1] != "player" or parts[2] != "site":
        return None
    scheme, host = parts[3], parts[4]
    if scheme not in {"http", "https", "ws", "wss"} or not _safe_host(host):
        return None
    rest = "/".join(parts[5:])
    url = f"{scheme}://{host}/{rest}"
    query, _color = _split_query(parsed.query)
    if query:
        url = f"{url}?{query}"
    return url


def _redirect(exc: _Redirect, target: str) -> Response:
    if exc.code not in {301, 302, 303, 307, 308}:
        return _plain("Die Seite ist nicht erreichbar.", 502)
    location = urljoin(target, exc.url)
    reason = authorize(location, None, follow=True)
    if reason:
        return _plain(reason, 403)
    path = proxy_path(location)
    if not path:
        return _plain("Die Adresse ist ungültig.", 400)
    return RedirectResponse(path, status_code=exc.code)


def _chunks(stream, response=None):
    closer = response if response is not None else stream
    try:
        while True:
            block = stream.read(64 * 1024)
            if not block:
                break
            yield block
    finally:
        closer.close()


def _text(raw: bytes, encoding: str, content_type: str) -> str:
    data = _decompress(raw, encoding)
    charset = "utf-8"
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    else:
        head = data[:2500].decode("ascii", errors="ignore")
        meta = re.search(r"charset=['\"]?\s*([A-Za-z0-9._-]+)", head, re.I)
        if meta:
            charset = meta.group(1)
    return data.decode(charset, errors="replace")


def _decompress(raw: bytes, encoding: str) -> bytes:
    if encoding in {"gzip", "x-gzip"}:
        return gzip.decompress(raw)
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _kind(content_type: str) -> str | None:
    base = content_type.split(";", 1)[0].strip().lower()
    if base in {"text/html", "application/xhtml+xml"}:
        return "html"
    if base == "text/css":
        return "css"
    return None


def _small_enough(info) -> bool:
    length = info.get("Content-Length")
    if not length:
        return True
    try:
        return int(length) <= _REWRITE_LIMIT
    except ValueError:
        return True


def _pass_headers(info, content_type: str, *, rewritten: bool) -> dict[str, str]:
    headers = {"Referrer-Policy": "same-origin"}
    for key in _PASS_HEADERS:
        value = info.get(key)
        if value and not (rewritten and key in {"content-length", "etag", "last-modified"}):
            headers[key.title() if key != "etag" else "ETag"] = value
    if content_type:
        headers["Content-Type"] = content_type
    if rewritten:
        headers["Cache-Control"] = "no-store"
    return headers


def _plain(message: str, status: int) -> HTMLResponse:
    body = f"<!DOCTYPE html><meta charset=utf-8><title>HiBit Signage</title><p>{_escape(message)}</p>"
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _document_base(html: str, page_url: str) -> str:
    match = re.search(r"""<base\b[^>]*\bhref\s*=\s*(["'])(.*?)\1""", html, re.I | re.S)
    if not match:
        return page_url
    return urljoin(page_url, match.group(2).strip())


def _keep_referrer(html: str) -> str:
    html = re.sub(r"""\sreferrerpolicy\s*=\s*(['"]).*?\1""", "", html, flags=re.I)
    html = re.sub(r"""<meta\b[^>]*\bname\s*=\s*(['"])referrer\1[^>]*>""", "", html, flags=re.I)

    def rel(match: re.Match) -> str:
        parts = [part for part in match.group(2).split() if part.lower() != "noreferrer"]
        if not parts:
            return ""
        quote = match.group(1)
        return f" rel={quote}{' '.join(parts)}{quote}"

    return re.sub(r"""\srel\s*=\s*(['"])([^'"]*)\1""", rel, html, flags=re.I)


def _rewrite_meta(html: str, base: str) -> str:
    def repl(match: re.Match) -> str:
        tag = match.group(0)
        if re.search(r"""http-equiv\s*=\s*['"]?\s*(content-security-policy|x-frame-options)\b""", tag, re.I):
            return ""
        if re.search(r"""http-equiv\s*=\s*['"]?\s*refresh\b""", tag, re.I):
            return re.sub(
                r"""(url\s*=\s*)([^;"']+)""",
                lambda part: part.group(1) + rewrite_url(part.group(2).strip(), base),
                tag,
                flags=re.I,
            )
        return tag

    return re.sub(r"<meta\b[^>]*>", repl, html, flags=re.I)


def _rewrite_attrs(html: str, base: str, names: tuple[str, ...]) -> str:
    group = "|".join(names)
    for quote_char in ('"', "'"):
        pattern = re.compile(
            rf"""(\b(?:{group})\s*=\s*{re.escape(quote_char)})([^{quote_char}]*){re.escape(quote_char)}""",
            re.I,
        )
        html = pattern.sub(lambda match: match.group(1) + rewrite_url(match.group(2), base) + quote_char, html)
    return html


def _rewrite_srcset(html: str, base: str) -> str:
    for quote_char in ('"', "'"):
        pattern = re.compile(
            rf"""(\bsrcset\s*=\s*{re.escape(quote_char)})([^{quote_char}]*){re.escape(quote_char)}""",
            re.I,
        )

        def repl(match: re.Match, q=quote_char) -> str:
            parts = []
            for piece in match.group(2).split(","):
                bits = piece.strip().split()
                if not bits:
                    continue
                bits[0] = rewrite_url(bits[0], base)
                parts.append(" ".join(bits))
            return match.group(1) + ", ".join(parts) + q

        html = pattern.sub(repl, html)
    return html


def _rewrite_object_data(html: str, base: str) -> str:
    def repl(match: re.Match) -> str:
        return _rewrite_attrs(match.group(0), base, ("data",))

    return re.sub(r"<object\b[^>]*>", repl, html, flags=re.I)


def _rewrite_style_attrs(html: str, base: str) -> str:
    html = re.sub(
        r'(\bstyle\s*=\s*")([^"]*)"',
        lambda match: match.group(1) + rewrite_css(match.group(2), base) + '"',
        html,
        flags=re.I,
    )
    html = re.sub(
        r"(\bstyle\s*=\s*')([^']*)'",
        lambda match: match.group(1) + rewrite_css(match.group(2), base) + "'",
        html,
        flags=re.I,
    )
    return html


def _playlist_hosts() -> set[str]:
    global _HOSTS
    now = time.time()
    with _HOSTS_LOCK:
        cached_at, hosts = _HOSTS
        if now - cached_at < 1 and hosts:
            return set(hosts)
    found: set[str] = set()
    for item in db.list_queue():
        if not item.get("enabled") or item.get("kind") != "web" or not item.get("url"):
            continue
        host = (urlparse(item["url"]).hostname or "").lower()
        if host:
            found.add(host)
    with _HOSTS_LOCK:
        _HOSTS = (now, found)
    return found


def _remember(host: str) -> None:
    if not host:
        return
    now = time.time()
    with _HOSTS_LOCK:
        dead = [key for key, expiry in _EXTRA.items() if expiry < now]
        for key in dead:
            _EXTRA.pop(key, None)
        _EXTRA[host] = now + 1800


def _listed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for name in _playlist_hosts():
        if host == name or host.endswith("." + name):
            return True
    return False


def _from_player(referer: str) -> bool:
    path = urlparse(referer).path.rstrip("/")
    return path == "/player"


def _known(host: str) -> bool:
    if _listed(host):
        return True
    with _HOSTS_LOCK:
        return _EXTRA.get(host, 0) > time.time()


def _referer_host(referer: str) -> str | None:
    upstream = referer_to_upstream(referer)
    if not upstream:
        return None
    return _hostname(urlparse(upstream).netloc)


def _hostname(netloc: str) -> str:
    host = netloc.split("@")[-1]
    if host.startswith("["):
        end = host.find("]")
        return host[1:end].lower() if end > 0 else host.lower()
    return host.split(":")[0].lower()


def _safe_host(host: str) -> bool:
    if not host or any(char in host for char in "/\\\x00 \t"):
        return False
    return host not in {".", ".."}


def _idn(host: str) -> str:
    if "@" in host:
        user, _, rest = host.rpartition("@")
        return f"{user}@{_idn(rest)}"
    if host.startswith("["):
        return host
    name, sep, port_no = host.partition(":")
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return name + (sep + port_no if sep else "")


def _cookie_header(url: str) -> str:
    req = urllib.request.Request(url)
    with _JAR_LOCK:
        try:
            _JAR.add_cookie_header(req)
        except Exception:
            return ""
    return req.get_header("Cookie") or ""


async def _pump_ws(ws: WebSocket, remote) -> None:
    async def from_client() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await remote.send(message["text"])
            elif message.get("bytes") is not None:
                await remote.send(message["bytes"])

    async def from_remote() -> None:
        async for data in remote:
            if isinstance(data, str):
                await ws.send_text(data)
            else:
                await ws.send_bytes(data)

    tasks = [asyncio.create_task(from_client()), asyncio.create_task(from_remote())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except Exception:
                pass
    finally:
        for task in tasks:
            task.cancel()
