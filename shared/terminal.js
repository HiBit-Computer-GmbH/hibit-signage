(function () {
  const FG = {
    30: "#8b9078",
    31: "#ff6b57",
    32: "#b6f25c",
    33: "#ffc857",
    34: "#8ec8ff",
    35: "#e3a6ff",
    36: "#7ee0d6",
    37: "#f4f6ef",
    90: "#9aa892",
    91: "#ff8f80",
    92: "#d6ff4a",
    93: "#ffe08a",
    94: "#b7dcff",
    95: "#f0c6ff",
    96: "#b8fff4",
    97: "#ffffff",
  };
  const BG = {
    40: "#1c1f18",
    41: "#5c2018",
    42: "#24380f",
    43: "#4a3b10",
    44: "#16304a",
    45: "#3a2048",
    46: "#123834",
    47: "#d5d8cc",
  };

  function escapeHtml(value) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
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
      this.esc = false;
      this.escBuf = "";
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
        if (text) this.emit(text);
        event.preventDefault();
      });
      this.el.addEventListener("click", () => this.el.focus());
      this._resize = () => {
        const size = this.measure();
        if (this.onResize) this.onResize(size);
      };
      window.addEventListener("resize", this._resize);
    }

    destroy() {
      window.removeEventListener("resize", this._resize);
    }

    measure() {
      const probe = document.createElement("span");
      probe.textContent = "0000000000";
      probe.className = "term-probe";
      this.el.appendChild(probe);
      const rect = probe.getBoundingClientRect();
      probe.remove();
      const charWidth = (rect.width || 84) / 10;
      const charHeight = rect.height || 18;
      const cols = Math.max(40, Math.floor((this.el.clientWidth - 20) / charWidth));
      const rows = Math.max(12, Math.floor((this.el.clientHeight - 16) / charHeight));
      if (!this.cells.length || cols !== this.cols || rows !== this.rows) {
        this.cols = cols;
        this.rows = rows;
        this.resetBuffer();
      }
      return { cols: this.cols, rows: this.rows };
    }

    resetBuffer() {
      this.cells = [];
      for (let row = 0; row < this.rows; row += 1) this.cells.push(this.blank());
      this.x = 0;
      this.y = 0;
    }

    blank() {
      const cells = [];
      for (let col = 0; col < this.cols; col += 1) cells.push({ ch: " ", fg: "", bg: "", bold: false });
      return cells;
    }

    emit(text) {
      if (this.onInput) this.onInput(text);
    }

    onKey(event) {
      const specials = {
        Enter: "\r",
        Backspace: "\u007f",
        Tab: "\t",
        ArrowUp: "\u001b[A",
        ArrowDown: "\u001b[B",
        ArrowRight: "\u001b[C",
        ArrowLeft: "\u001b[D",
        Home: "\u001b[H",
        End: "\u001b[F",
        Delete: "\u001b[3~",
      };
      if (event.ctrlKey && event.key.toLowerCase() === "c") {
        this.emit("\u0003");
        event.preventDefault();
        return;
      }
      if (event.ctrlKey && event.key.toLowerCase() === "d") {
        this.emit("\u0004");
        event.preventDefault();
        return;
      }
      if (specials[event.key]) {
        this.emit(specials[event.key]);
        event.preventDefault();
        return;
      }
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        this.emit(event.key);
        event.preventDefault();
      }
    }

    write(bytes) {
      const text = this.decoder.decode(bytes, { stream: true });
      for (let index = 0; index < text.length; index += 1) this.feed(text[index]);
      this.paint();
    }

    writeText(text) {
      for (let index = 0; index < text.length; index += 1) this.feed(text[index]);
      this.paint();
    }

    feed(ch) {
      if (this.esc) {
        this.escBuf += ch;
        if (this.escBuf.length > 32) {
          this.esc = false;
          this.escBuf = "";
          return;
        }
        if (this.escBuf.length === 1 && ch !== "[") {
          this.esc = false;
          this.escBuf = "";
          return;
        }
        if (/[@A-Za-z]/.test(ch)) {
          this.csi(this.escBuf);
          this.esc = false;
          this.escBuf = "";
        }
        return;
      }
      if (ch === "\u001b") {
        this.esc = true;
        this.escBuf = "";
        return;
      }
      if (ch === "\r") {
        this.x = 0;
        return;
      }
      if (ch === "\n") {
        this.newline();
        return;
      }
      if (ch === "\b") {
        this.x = Math.max(0, this.x - 1);
        return;
      }
      if (ch === "\t") {
        this.x = Math.min(this.cols - 1, this.x + (8 - (this.x % 8)));
        return;
      }
      if (ch < " ") return;
      this.put(ch);
    }

    csi(seq) {
      const final = seq[seq.length - 1];
      const params = seq
        .slice(1, -1)
        .split(";")
        .map((value) => parseInt(value || "0", 10) || 0);
      if (final === "m") this.sgr(params.length ? params : [0]);
      else if (final === "H" || final === "f") {
        this.y = Math.max(0, Math.min(this.rows - 1, (params[0] || 1) - 1));
        this.x = Math.max(0, Math.min(this.cols - 1, (params[1] || 1) - 1));
      } else if (final === "A") this.y = Math.max(0, this.y - (params[0] || 1));
      else if (final === "B") this.y = Math.min(this.rows - 1, this.y + (params[0] || 1));
      else if (final === "C") this.x = Math.min(this.cols - 1, this.x + (params[0] || 1));
      else if (final === "D") this.x = Math.max(0, this.x - (params[0] || 1));
      else if (final === "J" && (params[0] || 0) === 2) this.resetBuffer();
      else if (final === "K") {
        const row = this.cells[this.y];
        if (!row) return;
        for (let col = this.x; col < this.cols; col += 1) row[col] = { ch: " ", fg: "", bg: "", bold: false };
      }
    }

    sgr(codes) {
      codes.forEach((code) => {
        if (code === 0) {
          this.fg = "";
          this.bg = "";
          this.bold = false;
        } else if (code === 1) this.bold = true;
        else if (FG[code]) this.fg = FG[code];
        else if (BG[code]) this.bg = BG[code];
        else if (code === 39) this.fg = "";
        else if (code === 49) this.bg = "";
      });
    }

    put(ch) {
      if (this.x >= this.cols) this.newline();
      if (!this.cells[this.y]) return;
      this.cells[this.y][this.x] = { ch, fg: this.fg, bg: this.bg, bold: this.bold };
      this.x += 1;
    }

    newline() {
      this.x = 0;
      this.y += 1;
      if (this.y >= this.rows) {
        this.cells.shift();
        this.cells.push(this.blank());
        this.y = this.rows - 1;
      }
    }

    paint() {
      const rows = this.cells.map((row, y) => {
        let html = "";
        let run = "";
        let style = null;
        const flush = () => {
          if (!run) return;
          html += `<span style="${style}">${escapeHtml(run)}</span>`;
          run = "";
        };
        row.forEach((cell, x) => {
          const cursor = x === this.x && y === this.y;
          const next = `color:${cursor ? "#172000" : cell.fg || "inherit"};background:${cursor ? "#d6ff4a" : cell.bg || "transparent"};${cell.bold ? "font-weight:700;" : ""}`;
          if (next !== style) {
            flush();
            style = next;
          }
          run += cell.ch;
        });
        flush();
        return `<div class="term-row">${html}</div>`;
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
      if (socket.readyState === 1) {
        socket.send(JSON.stringify({ type: "input", data: bytesToBase64(text) }));
      }
    };
    term.onResize = (size) => {
      if (socket.readyState === 1) socket.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
    };
    socket.addEventListener("open", () => {
      const size = term.measure();
      socket.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
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

  window.HiBitTerm = { mount };
})();
