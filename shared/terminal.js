(function () {
  const FG = {
    30: "#8b9078", 31: "#ff6b57", 32: "#b6f25c", 33: "#ffc857",
    34: "#8ec8ff", 35: "#e3a6ff", 36: "#7ee0d6", 37: "#f4f6ef",
    90: "#9aa892", 91: "#ff8f80", 92: "#d6ff4a", 93: "#ffe08a",
    94: "#b7dcff", 95: "#f0c6ff", 96: "#b8fff4", 97: "#ffffff",
  };
  const BG = {
    40: "#1c1f18", 41: "#5c2018", 42: "#24380f", 43: "#4a3b10",
    44: "#16304a", 45: "#3a2048", 46: "#123834", 47: "#d5d8cc",
  };

  function escapeHtml(value) {
    return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function color256(index) {
    const n = index & 255;
    if (n < 16) {
      const basic = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
      const named = ["#1c1f18", "#ff6b57", "#b6f25c", "#ffc857", "#8ec8ff", "#e3a6ff", "#7ee0d6", "#f4f6ef", "#9aa892", "#ff8f80", "#d6ff4a", "#ffe08a", "#b7dcff", "#f0c6ff", "#b8fff4", "#ffffff"];
      return named[basic[n]];
    }
    if (n >= 232) {
      const level = 8 + (n - 232) * 10;
      return `rgb(${level},${level},${level})`;
    }
    const cube = n - 16;
    const steps = [0, 95, 135, 175, 215, 255];
    const r = steps[Math.floor(cube / 36)];
    const g = steps[Math.floor(cube / 6) % 6];
    const b = steps[cube % 6];
    return `rgb(${r},${g},${b})`;
  }

  class Term {
    constructor(el) {
      this.el = el;
      this.cols = 80;
      this.rows = 24;
      this.x = 0;
      this.y = 0;
      this.fg = "";
      this.bg = "";
      this.bold = false;
      this.inverse = false;
      this.cursor = true;
      this.appCursor = false;
      this.autowrap = true;
      this.wrapPending = false;
      this.scrollTop = 0;
      this.scrollBottom = 23;
      this.alt = false;
      this.savedMain = null;
      this.saved = null;
      this.mode = "";
      this.escBuf = "";
      this.osc = "";
      this.decoder = new TextDecoder("utf-8");
      this.onInput = null;
      this.onResize = null;
      this.el.classList.add("terminal");
      this.el.tabIndex = 0;
      this.cells = [];
      this.measure();
      this.el.addEventListener("keydown", (event) => this.onKey(event));
      this.el.addEventListener("paste", (event) => {
        const text = event.clipboardData.getData("text");
        if (text) this.emit(text.replace(/\n/g, "\r"));
        event.preventDefault();
      });
      this.el.addEventListener("click", () => this.el.focus());
      this._resize = () => {
        const size = this.measure();
        if (size && this.onResize) this.onResize(size);
      };
      window.addEventListener("resize", this._resize);
      if (window.ResizeObserver) {
        this.observer = new ResizeObserver(() => {
          clearTimeout(this._resizeTimer);
          this._resizeTimer = setTimeout(this._resize, 60);
        });
        this.observer.observe(this.el);
      }
    }

    destroy() {
      window.removeEventListener("resize", this._resize);
      if (this.observer) this.observer.disconnect();
      clearTimeout(this._resizeTimer);
    }

    measure() {
      const probe = document.createElement("span");
      probe.className = "term-probe";
      probe.textContent = "0000000000";
      this.el.appendChild(probe);
      const width = (probe.getBoundingClientRect().width || 84) / 10;
      probe.remove();
      const style = getComputedStyle(this.el);
      const innerW = this.el.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
      const innerH = this.el.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0);
      const rowHeight = parseFloat(style.getPropertyValue("--term-row")) || 16;
      if (innerW < 40 || innerH < 40) return null;
      const cols = Math.max(20, Math.floor(innerW / width));
      const rows = Math.max(6, Math.floor(innerH / rowHeight));
      if (!this.cells.length || cols !== this.cols || rows !== this.rows) {
        this.cols = cols;
        this.rows = rows;
        this.scrollTop = 0;
        this.scrollBottom = rows - 1;
        this.resetBuffer();
        this.paint();
        return { cols, rows };
      }
      return null;
    }

    resetBuffer() {
      this.cells = [];
      for (let row = 0; row < this.rows; row += 1) this.cells.push(this.blank());
      this.x = 0;
      this.y = 0;
      this.wrapPending = false;
    }

    blank() {
      const cells = [];
      for (let col = 0; col < this.cols; col += 1) cells.push(this.cell());
      return cells;
    }

    cell(ch) {
      return { ch: ch || " ", fg: "", bg: "", bold: false, inverse: false };
    }

    styled() {
      return { ch: " ", fg: this.fg, bg: this.bg, bold: this.bold, inverse: this.inverse };
    }

    emit(text) {
      if (this.onInput) this.onInput(text);
    }

    onKey(event) {
      if (event.ctrlKey && !event.altKey && !event.metaKey && event.key.length === 1) {
        const key = event.key.toLowerCase();
        if (key >= "a" && key <= "z") {
          this.emit(String.fromCharCode(key.charCodeAt(0) - 96));
          event.preventDefault();
          return;
        }
      }
      const arrows = this.appCursor
        ? { ArrowUp: "\u001bOA", ArrowDown: "\u001bOB", ArrowRight: "\u001bOC", ArrowLeft: "\u001bOD" }
        : { ArrowUp: "\u001b[A", ArrowDown: "\u001b[B", ArrowRight: "\u001b[C", ArrowLeft: "\u001b[D" };
      const specials = {
        Enter: "\r",
        Backspace: "\u007f",
        Tab: "\t",
        Home: this.appCursor ? "\u001bOH" : "\u001b[H",
        End: this.appCursor ? "\u001bOF" : "\u001b[F",
        Delete: "\u001b[3~",
        Insert: "\u001b[2~",
        PageUp: "\u001b[5~",
        PageDown: "\u001b[6~",
        ...arrows,
      };
      if (specials[event.key]) {
        this.emit(specials[event.key]);
        event.preventDefault();
        return;
      }
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
        this.emit(event.altKey ? `\u001b${event.key}` : event.key);
        event.preventDefault();
      }
    }

    write(bytes) {
      const text = this.decoder.decode(bytes, { stream: true });
      this.writeText(text, true);
    }

    writeText(text, already) {
      const value = already ? text : text;
      for (let index = 0; index < value.length; index += 1) this.feed(value[index]);
      this.paint();
    }

    feed(ch) {
      if (this.mode === "osc") {
        if (ch === "\u0007" || (this.mode === "osc" && ch === "\\")) {
          this.mode = "";
          this.osc = "";
          return;
        }
        if (ch === "\u001b") {
          this.mode = "osc-st";
          return;
        }
        if (this.osc.length < 512) this.osc += ch;
        return;
      }
      if (this.mode === "osc-st") {
        this.mode = "";
        this.osc = "";
        return;
      }
      if (this.mode === "skip") {
        this.mode = "";
        return;
      }
      if (this.mode === "csi") {
        this.escBuf += ch;
        if (this.escBuf.length > 64 || (ch >= "@" && ch <= "~")) {
          if (ch >= "@" && ch <= "~") this.csi(this.escBuf);
          this.mode = "";
          this.escBuf = "";
        }
        return;
      }
      if (this.mode === "esc") {
        if (ch === "[") {
          this.mode = "csi";
          this.escBuf = "";
          return;
        }
        if (ch === "]") {
          this.mode = "osc";
          this.osc = "";
          return;
        }
        if (ch === "7") {
          this.saved = { x: this.x, y: this.y };
          this.mode = "";
          return;
        }
        if (ch === "8") {
          if (this.saved) {
            this.x = this.saved.x;
            this.y = this.saved.y;
          }
          this.mode = "";
          return;
        }
        if (ch === "M") {
          this.reverse();
          this.mode = "";
          return;
        }
        if (ch === "E") {
          this.x = 0;
          this.index();
          this.mode = "";
          return;
        }
        if (ch === "c") {
          this.resetBuffer();
          this.mode = "";
          return;
        }
        if ("()%#".includes(ch)) {
          this.mode = "skip";
          return;
        }
        this.mode = "";
        return;
      }
      if (ch === "\u001b") {
        this.mode = "esc";
        return;
      }
      if (ch === "\r") {
        this.x = 0;
        this.wrapPending = false;
        return;
      }
      if (ch === "\n") {
        this.index();
        return;
      }
      if (ch === "\b") {
        this.wrapPending = false;
        this.x = Math.max(0, this.x - 1);
        return;
      }
      if (ch === "\t") {
        this.wrapPending = false;
        this.x = Math.min(this.cols - 1, this.x + (8 - (this.x % 8)));
        return;
      }
      if (ch < " " || ch === "\u007f") return;
      this.put(ch);
    }

    csi(seq) {
      const final = seq[seq.length - 1];
      const priv = seq.startsWith("?");
      const body = seq.slice(0, -1).replace(/^[?>]/, "");
      const params = body.length ? body.split(";").map((value) => {
        const number = parseInt(value, 10);
        return Number.isFinite(number) ? number : 0;
      }) : [];
      const first = (fallback) => (params.length && params[0] ? params[0] : fallback);
      if (final === "m") this.sgr(params.length ? params : [0]);
      else if (final === "H" || final === "f") this.place((params[0] || 1) - 1, (params[1] || 1) - 1);
      else if (final === "A") this.place(this.y - first(1), this.x);
      else if (final === "B") this.place(this.y + first(1), this.x);
      else if (final === "C") this.place(this.y, this.x + first(1));
      else if (final === "D") this.place(this.y, this.x - first(1));
      else if (final === "E") { this.x = 0; this.place(this.y + first(1), 0); }
      else if (final === "F") { this.x = 0; this.place(this.y - first(1), 0); }
      else if (final === "G") this.place(this.y, (params[0] || 1) - 1);
      else if (final === "d") this.place((params[0] || 1) - 1, this.x);
      else if (final === "J") this.eraseDisplay(params[0] || 0);
      else if (final === "K") this.eraseLine(params[0] || 0);
      else if (final === "X") this.eraseChars(first(1));
      else if (final === "@") this.insertChars(first(1));
      else if (final === "P") this.deleteChars(first(1));
      else if (final === "L") this.insertLines(first(1));
      else if (final === "M") this.deleteLines(first(1));
      else if (final === "S") this.scrollUp(first(1));
      else if (final === "T") this.scrollDown(first(1));
      else if (final === "r" && !priv) this.setScroll(params[0] || 1, params[1] || this.rows);
      else if (final === "s") this.saved = { x: this.x, y: this.y };
      else if (final === "u" && this.saved) this.place(this.saved.y, this.saved.x);
      else if (final === "n" && !priv && (params[0] || 0) === 6) this.emit(`\u001b[${this.y + 1};${this.x + 1}R`);
      else if (final === "n" && !priv && (params[0] || 0) === 5) this.emit("\u001b[0n");
      else if (final === "c") this.emit("\u001b[?1;2c");
      else if ((final === "h" || final === "l") && priv) params.forEach((mode) => this.setMode(mode, final === "h"));
    }

    setMode(mode, on) {
      if (mode === 25) this.cursor = on;
      else if (mode === 1) this.appCursor = on;
      else if (mode === 7) this.autowrap = on;
      else if (mode === 1049 || mode === 1047 || mode === 47) {
        if (on) this.enterAlt();
        else this.leaveAlt();
      }
    }

    enterAlt() {
      if (this.alt) return;
      this.savedMain = { cells: this.cells, x: this.x, y: this.y, scrollTop: this.scrollTop, scrollBottom: this.scrollBottom };
      this.alt = true;
      this.scrollTop = 0;
      this.scrollBottom = this.rows - 1;
      this.resetBuffer();
    }

    leaveAlt() {
      if (!this.alt || !this.savedMain) return;
      this.cells = this.savedMain.cells;
      this.x = this.savedMain.x;
      this.y = this.savedMain.y;
      this.scrollTop = this.savedMain.scrollTop;
      this.scrollBottom = this.savedMain.scrollBottom;
      this.alt = false;
      this.savedMain = null;
      this.wrapPending = false;
    }

    setScroll(top, bottom) {
      const start = Math.max(0, Math.min(this.rows - 1, top - 1));
      const end = Math.max(0, Math.min(this.rows - 1, bottom - 1));
      if (end <= start) {
        this.scrollTop = 0;
        this.scrollBottom = this.rows - 1;
      } else {
        this.scrollTop = start;
        this.scrollBottom = end;
      }
      this.place(0, 0);
    }

    place(y, x) {
      this.wrapPending = false;
      this.y = Math.max(0, Math.min(this.rows - 1, y));
      this.x = Math.max(0, Math.min(this.cols - 1, x));
    }

    sgr(codes) {
      for (let index = 0; index < codes.length; index += 1) {
        const code = codes[index];
        if (code === 0) {
          this.fg = "";
          this.bg = "";
          this.bold = false;
          this.inverse = false;
        } else if (code === 1) this.bold = true;
        else if (code === 22) this.bold = false;
        else if (code === 7) this.inverse = true;
        else if (code === 27) this.inverse = false;
        else if (code === 39) this.fg = "";
        else if (code === 49) this.bg = "";
        else if (FG[code]) this.fg = FG[code];
        else if (BG[code]) this.bg = BG[code];
        else if (code === 38 || code === 48) {
          const kind = codes[index + 1];
          if (kind === 5) {
            const color = color256(codes[index + 2] || 0);
            if (code === 38) this.fg = color;
            else this.bg = color;
            index += 2;
          } else if (kind === 2) {
            const hex = `#${[codes[index + 2] || 0, codes[index + 3] || 0, codes[index + 4] || 0].map((part) => part.toString(16).padStart(2, "0")).join("")}`;
            if (code === 38) this.fg = hex;
            else this.bg = hex;
            index += 4;
          }
        }
      }
    }

    put(ch) {
      if (this.wrapPending) {
        this.wrapPending = false;
        this.x = 0;
        this.index();
      }
      if (!this.cells[this.y]) return;
      const next = this.styled();
      next.ch = ch;
      this.cells[this.y][this.x] = next;
      this.x += 1;
      if (this.x >= this.cols) {
        this.x = this.cols - 1;
        this.wrapPending = this.autowrap;
      }
    }

    index() {
      this.wrapPending = false;
      if (this.y === this.scrollBottom) this.scrollUp(1);
      else if (this.y < this.rows - 1) this.y += 1;
    }

    reverse() {
      this.wrapPending = false;
      if (this.y === this.scrollTop) this.scrollDown(1);
      else if (this.y > 0) this.y -= 1;
    }

    scrollUp(count) {
      const amount = Math.max(1, count);
      for (let step = 0; step < amount; step += 1) {
        this.cells.splice(this.scrollTop, 1);
        this.cells.splice(this.scrollBottom, 0, this.blank());
      }
    }

    scrollDown(count) {
      const amount = Math.max(1, count);
      for (let step = 0; step < amount; step += 1) {
        this.cells.splice(this.scrollTop, 0, this.blank());
        this.cells.splice(this.scrollBottom + 1, 1);
      }
    }

    insertLines(count) {
      if (this.y < this.scrollTop || this.y > this.scrollBottom) return;
      for (let step = 0; step < count; step += 1) {
        this.cells.splice(this.y, 0, this.blank());
        this.cells.splice(this.scrollBottom + 1, 1);
      }
    }

    deleteLines(count) {
      if (this.y < this.scrollTop || this.y > this.scrollBottom) return;
      for (let step = 0; step < count; step += 1) {
        this.cells.splice(this.y, 1);
        this.cells.splice(this.scrollBottom, 0, this.blank());
      }
    }

    insertChars(count) {
      const row = this.cells[this.y];
      if (!row) return;
      const blanks = [];
      for (let step = 0; step < count; step += 1) blanks.push(this.styled());
      row.splice(this.x, 0, ...blanks);
      row.length = this.cols;
    }

    deleteChars(count) {
      const row = this.cells[this.y];
      if (!row) return;
      row.splice(this.x, count);
      while (row.length < this.cols) row.push(this.cell());
    }

    eraseChars(count) {
      const row = this.cells[this.y];
      if (!row) return;
      for (let col = this.x; col < Math.min(this.cols, this.x + count); col += 1) row[col] = this.cell();
    }

    eraseLine(mode) {
      const row = this.cells[this.y];
      if (!row) return;
      const start = mode === 1 || mode === 2 ? 0 : this.x;
      const end = mode === 1 ? this.x : this.cols - 1;
      for (let col = start; col <= end; col += 1) row[col] = this.cell();
    }

    eraseDisplay(mode) {
      if (mode === 2 || mode === 3) {
        this.cells = [];
        for (let row = 0; row < this.rows; row += 1) this.cells.push(this.blank());
        return;
      }
      if (mode === 0) {
        this.eraseLine(0);
        for (let row = this.y + 1; row < this.rows; row += 1) this.cells[row] = this.blank();
      } else if (mode === 1) {
        for (let row = 0; row < this.y; row += 1) this.cells[row] = this.blank();
        this.eraseLine(1);
      }
    }

    lineText(row) {
      return (this.cells[row] || []).map((cell) => cell.ch).join("");
    }

    paint() {
      const showCursor = this.cursor;
      const cursorX = this.wrapPending ? this.cols - 1 : this.x;
      const rows = this.cells.map((row, y) => {
        const cells = row.map((cell, x) => {
          const cursor = showCursor && x === cursorX && y === this.y;
          const fg = cursor ? "#172000" : (cell.inverse ? cell.bg || "#172000" : cell.fg || "");
          const bg = cursor ? "#d6ff4a" : (cell.inverse ? cell.fg || "#d6ff4a" : cell.bg || "");
          const style = `${fg ? `color:${fg};` : ""}${bg ? `background:${bg};` : ""}${cell.bold && !cursor ? "font-weight:700;" : ""}`;
          return `<span class="term-cell"${style ? ` style="${style}"` : ""}>${escapeHtml(cell.ch)}</span>`;
        }).join("");
        return `<div class="term-row">${cells}</div>`;
      });
      this.el.innerHTML = rows.join("");
    }
  }

  function bytesToBase64(text) {
    const bytes = new TextEncoder().encode(text);
    let binary = "";
    bytes.forEach((value) => {
      binary += String.fromCharCode(value);
    });
    return btoa(binary);
  }

  function mount(el, url) {
    const term = new Term(el);
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${location.host}${url}`);
    term.onInput = (text) => {
      if (socket.readyState === 1) socket.send(JSON.stringify({ type: "input", data: bytesToBase64(text) }));
    };
    term.onResize = (size) => {
      if (socket.readyState === 1) socket.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
    };
    socket.addEventListener("open", () => {
      const size = term.measure();
      if (size) socket.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
      else socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      term.el.focus();
    });
    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (message.type === "data" && message.data) {
        const binary = atob(message.data);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
        term.write(bytes);
      } else if (message.type === "error") {
        term.writeText(`\r\n${message.detail || "Fehler"}\r\n`);
      } else if (message.type === "exit") {
        term.writeText(`\r\n[Sitzung beendet${message.detail ? ": " + message.detail : ""}]\r\n`);
      }
    });
    socket.addEventListener("close", () => term.writeText("\r\n[Verbindung geschlossen]\r\n"));
    return {
      focus() {
        term.el.focus();
      },
      close() {
        term.destroy();
        socket.close();
      },
    };
  }

  window.HiBitTerm = { mount, create(el) { return new Term(el); } };
})();
