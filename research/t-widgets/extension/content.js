(function () {
    'use strict';
    if (window.__pribluda_tw_intercept) return;
    window.__pribluda_tw_intercept = true;

    var SERVER = 'http://127.0.0.1:8765/robots';
    var RS = '\u001e';
    var OrigWS = window.WebSocket;
    if (!OrigWS) return;

    function send(payload) {
        try {
            fetch(SERVER, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ robots: payload.robots || [], wc: payload.wc }),
                keepalive: true
            }).catch(function () {});
        } catch (e) {}
    }

    function handle(text) {
        var frames = text.split(RS);
        for (var i = 0; i < frames.length; i++) {
            var f = frames[i];
            if (!f) continue;
            var msg;
            try { msg = JSON.parse(f); } catch (e) { continue; }
            if (!msg || msg.type !== 1) continue;
            if (String(msg.target || '').toLowerCase() !== 'onrobots2') continue;
            var args = msg.arguments || [];
            var payload = args.length > 1 ? args[1] : args[0];
            if (!payload || !Array.isArray(payload.robots)) continue;
            console.log('[pribluda] OnRobots2:', payload.robots.length, 'robots');
            send(payload);
        }
    }

    window.WebSocket = function (url, protocols) {
        var ws = (protocols !== undefined) ? new OrigWS(url, protocols) : new OrigWS(url);
        ws.addEventListener('message', function (ev) {
            try { if (typeof ev.data === 'string') handle(ev.data); } catch (e) {}
        });
        return ws;
    };
    window.WebSocket.prototype = OrigWS.prototype;
    Object.setPrototypeOf(window.WebSocket, OrigWS);
    window.WebSocket.CONNECTING = OrigWS.CONNECTING;
    window.WebSocket.OPEN = OrigWS.OPEN;
    window.WebSocket.CLOSING = OrigWS.CLOSING;
    window.WebSocket.CLOSED = OrigWS.CLOSED;

    console.log('[pribluda] WebSocket patched, жду OnRobots2 ->', SERVER);
})();