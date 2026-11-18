/*
content_main.js v7.0 — MAIN world. Качает историю Aniscan same-origin fetch
(куки приложатся). Данные шлёт в content_bridge через window.postMessage,
далее background -> вкладка-мост localhost -> сервер (same-origin, без PNA/CSP).
*/
(function () {
  const HIST_BASE = "https://aniscan.ru/api/robot-histories";
  const API_RE = /aniscan\.ru\/api\//;
  const MAX_PAGES = 40;
  let historyLoaded = false;
  const seenDebug = new Set();
  const wsLogged = new Set();
  let seq = 0;
  const waiters = {};

  function nowIso() { return new Date().toISOString(); }
  function yesterdayStr() { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); }

  window.addEventListener("message", function (e) {
    if (e.source !== window) return;
    const d = e.data || {};
    if (!d.__pb_ack) return;
    const w = waiters[d.id];
    if (w) { delete waiters[d.id]; w(d.resp || {}); }
  });

  function send(path, obj, timeoutMs) {
    const id = ++seq;
    return new Promise(function (resolve) {
      waiters[id] = resolve;
      setTimeout(function () { if (waiters[id]) { delete waiters[id]; resolve({ error: "timeout" }); } }, timeoutMs || 10000);
      window.postMessage({ __pb: true, type: path, data: obj, id: id }, "*");
    });
  }

  async function loadHistory() {
    if (historyLoaded) return;
    historyLoaded = true;
    const size = 1000, sort = "createDttm,desc", yest = yesterdayStr();
    try {
      const r0 = await fetch(HIST_BASE + "?page=0&size=" + size + "&sort=" + encodeURIComponent(sort), { credentials: "include" });
      console.log("[pb-aniscan] history page0 HTTP " + r0.status);
      if (!r0.ok) { historyLoaded = false; return; }
      const j0 = await r0.json();
      const a0 = await send("aniscan_history", { received_at: nowIso(), page: 0, url: r0.url, data: j0 });
      console.log("[pb-aniscan] p0 resp: " + JSON.stringify(a0));
      if (a0 && a0.stop) return;
      let totalPages = (j0 && j0.page && j0.page.totalPages) || 1;
      for (let p = 1; p < Math.min(totalPages, MAX_PAGES); p++) {
        const url = HIST_BASE + "?page=" + p + "&size=" + size + "&sort=" + encodeURIComponent(sort);
        const r = await fetch(url, { credentials: "include" });
        if (!r.ok) break;
        const data = await r.json();
        const content = (data && data.content) || [];
        if (content.length === 0) break;
        const a = await send("aniscan_history", { received_at: nowIso(), page: p, url: url, data: data });
        if (a && a.stop) break;
        const last = content[content.length - 1];
        if (last && (last.createDttm || "").slice(0, 10) < yest) break;
      }
    } catch (e) { console.log("[pb-aniscan] loadHistory err: " + e); historyLoaded = false; }
  }

  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    const resp = await origFetch.apply(this, arguments);
    try {
      const url = (typeof input === "string") ? input : (input && input.url) || "";
      if (API_RE.test(url) && url.indexOf("robot-histories") === -1 && !seenDebug.has(url)) {
        seenDebug.add(url);
        send("aniscan_debug", { received_at: nowIso(), kind: "fetch", url: url });
      }
    } catch (e) {}
    return resp;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, url) { this.__pbUrl = url; return origOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    const url = this.__pbUrl || "";
    this.addEventListener("load", function () {
      try {
        if (API_RE.test(url) && url.indexOf("robot-histories") === -1 && !seenDebug.has(url)) {
          seenDebug.add(url);
          send("aniscan_debug", { received_at: nowIso(), kind: "xhr", url: url });
        }
      } catch (e) {}
    });
    return origSend.apply(this, arguments);
  };

  function logWs(ws, ev) {
    try {
      const key = "ws:" + ws.url;
      if (wsLogged.has(key)) return;
      wsLogged.add(key);
      let sample = "";
      try { sample = (typeof ev.data === "string") ? ev.data.slice(0, 400) : "<binary>"; } catch (e) {}
      send("aniscan_debug", { received_at: nowIso(), kind: "ws_msg", url: ws.url, sample: sample });
    } catch (e) {}
  }
  const WSProto = window.WebSocket.prototype;
  const origAdd = WSProto.addEventListener;
  WSProto.addEventListener = function (type, fn, opts) {
    if (type === "message") {
      const wrapped = function (ev) { logWs(this, ev); return fn.apply(this, arguments); };
      return origAdd.call(this, type, wrapped, opts);
    }
    return origAdd.apply(this, arguments);
  };
  const OrigWS = window.WebSocket;
  function PbWebSocket(url, protocols) {
    const ws = protocols === undefined ? new OrigWS(url) : new OrigWS(url, protocols);
    let _onmessage = null;
    Object.defineProperty(ws, "onmessage", {
      configurable: true, enumerable: true,
      get: function () { return _onmessage; },
      set: function (fn) { _onmessage = function (ev) { logWs(ws, ev); return fn.apply(this, arguments); }; }
    });
    return ws;
  }
  PbWebSocket.prototype = OrigWS.prototype;
  PbWebSocket.CONNECTING = OrigWS.CONNECTING; PbWebSocket.OPEN = OrigWS.OPEN;
  PbWebSocket.CLOSING = OrigWS.CLOSING; PbWebSocket.CLOSED = OrigWS.CLOSED;
  window.WebSocket = PbWebSocket;

  if (location.pathname.indexOf("robot-history") !== -1) setTimeout(loadHistory, 800);
  console.log("[pb-aniscan] v7.0 MAIN активен (вкладка-мост localhost)");
})();