(function () {
    'use strict';
    if (window.__pribluda_tw_intercept) return;
    window.__pribluda_tw_intercept = true;

    var SERVER = 'http://127.0.0.1:8765/robots';
    var RS = '\u001e'; // разделитель кадров SignalR
    var OrigWS = window.WebSocket;
    if (!OrigWS) return;

    function send(payload) {
        try {
            fetch(SERVER, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ robots: payload.robots || [], wc: payload.wc }),
                keepalive: true
            }).catch(function () { /* сервер не поднят — молча пропускаем */ });
        } catch (e) { /* игнор */ }
    }

    function handleFrame(text) {
        var frames = text.split(RS);
        for (var i = 0; i < frames.length; i++) {
            var frame = frames[i];
            if (!frame) continue;
            var msg;
            try { msg = JSON.parse(frame); } catch (e) { continue; }
            if (!msg || msg.type !== 1) continue; // 1 = Invocation
            var target = String(msg.target || '').toLowerCase();
            if (target !== 'onrobots2') continue;
            var args = msg.arguments || [];
            // args[0] = subscription info, args[1] = data payload
            var payload = args.length > 1 ? args[1] : args[0];
            if (!payload || !Array.isArray(payload.robots)) continue;
            send(payload);
        }
    }

    window.WebSocket = function (url, protocols) {
        var ws = (protocols !== undefined) ? new OrigWS(url, protocols) : new OrigWS(url);
        ws.addEventListener('message', function (ev) {
            try { if (typeof ev.data === 'string') handleFrame(ev.data); } catch (e) { /* игнор */ }
        });
        return ws;
    };
    window.WebSocket.prototype = OrigWS.prototype;
    Object.setPrototypeOf(window.WebSocket, OrigWS);
    // КОНСТАНТЫ CONNECTING/OPEN/CLOSING/CLOSED НЕ присваиваем:
    // они читаются по наследству от OrigWS через setPrototypeOf.
    // Присвоение бросало TypeError (read only property) в strict mode.

    console.log('[pribluda] WebSocket patched, жду OnRobots2 ->', SERVER);
})();