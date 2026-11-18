/*
background.js v7.0 — держит вкладку-мост http://127.0.0.1:8765/bridge.html
(открывает сам через chrome.tabs.create — не режется CSP/popup-блокером).
Пересылает данные от aniscan-bridge в эту вкладку; вкладка пишет на сервер
same-origin fetch (без PNA). Возвращает ответ обратно по цепочке.
*/
const SERVER = "http://127.0.0.1:8765";
const BRIDGE_URL = SERVER + "/bridge.html";

function ensureTab(cb) {
  chrome.tabs.query({ url: BRIDGE_URL }, function (tabs) {
    if (tabs && tabs.length) { cb(tabs[0].id); return; }
    chrome.tabs.create({ url: BRIDGE_URL, active: false }, function (t) { cb(t.id); });
  });
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (!msg || !msg.type) return;
  if (msg.type !== "aniscan_history" && msg.type !== "aniscan_debug") { sendResponse({ ok: true }); return; }
  ensureTab(function (tabId) {
    chrome.tabs.sendMessage(tabId, { type: "write", path: msg.type, obj: msg.data }, function (resp) {
      if (chrome.runtime.lastError) sendResponse({ error: chrome.runtime.lastError.message });
      else sendResponse(resp || {});
    });
  });
  return true;
});