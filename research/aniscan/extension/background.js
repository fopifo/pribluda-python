/*
background.js v7.1 — держит вкладку-мост http://127.0.0.1:8765/bridge.html.
Если слушатель в мосте мёртв (после перезагрузки расширения) — САМ перезагружает
вкладку-мост и повторяет отправку один раз. Данные: aniscan bridge -> background
-> мост (same-origin fetch) -> сервер.
*/
const SERVER = "http://127.0.0.1:8765";
const BRIDGE_URL = SERVER + "/bridge.html";

function ensureTab(cb) {
  chrome.tabs.query({ url: BRIDGE_URL }, function (tabs) {
    if (tabs && tabs.length) { cb(tabs[0].id); return; }
    chrome.tabs.create({ url: BRIDGE_URL, active: false }, function (t) { cb(t.id); });
  });
}

function sendToTab(tabId, msg, sendResponse, retries) {
  chrome.tabs.sendMessage(tabId, { type: "write", path: msg.type, obj: msg.data }, function (resp) {
    if (chrome.runtime.lastError) {
      if (retries > 0) {
        chrome.tabs.reload(tabId, function () {
          setTimeout(function () {
            ensureTab(function (newId) { sendToTab(newId, msg, sendResponse, retries - 1); });
          }, 1500);
        });
      } else {
        sendResponse({ error: chrome.runtime.lastError.message });
      }
      return;
    }
    sendResponse(resp || {});
  });
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (!msg || !msg.type) return;
  if (msg.type !== "aniscan_history" && msg.type !== "aniscan_debug") { sendResponse({ ok: true }); return; }
  ensureTab(function (tabId) { sendToTab(tabId, msg, sendResponse, 1); });
  return true;
});