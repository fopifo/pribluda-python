/*
content_bridge.js v7.0 — ISOLATED world на aniscan. Пересылает MAIN->background
и возвращает ack обратно в MAIN (postMessage).
*/
(function () {
  window.addEventListener("message", function (e) {
    if (e.source !== window) return;
    const d = e.data;
    if (!d || !d.__pb) return;
    try {
      chrome.runtime.sendMessage({ type: d.type, data: d.data }, function (resp) {
        if (chrome.runtime.lastError) {
          window.postMessage({ __pb_ack: true, id: d.id, resp: { error: chrome.runtime.lastError.message } }, "*");
          return;
        }
        window.postMessage({ __pb_ack: true, id: d.id, resp: resp || {} }, "*");
      });
    } catch (err) {
      window.postMessage({ __pb_ack: true, id: d.id, resp: { error: String(err) } }, "*");
    }
  }, false);
})();