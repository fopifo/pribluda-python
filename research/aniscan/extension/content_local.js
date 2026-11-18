/*
content_local.js v7.0 — сидит в вкладке-мосте http://127.0.0.1:8765/*.
Принимает от background команду write и делает SAME-ORIGIN fetch к серверу
(localhost->localhost: ни PNA, ни CORS, ни CSP страницы aniscan).
*/
chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (!msg || msg.type !== "write") return;
  fetch("/" + msg.path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(msg.obj)
  }).then(function (r) { return r.json(); })
    .then(function (j) { sendResponse(j); })
    .catch(function (e) { sendResponse({ error: String(e) }); });
  return true;
});