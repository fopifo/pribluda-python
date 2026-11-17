(function () {
    'use strict';
    if (window.__pribluda_tw_intercept) return;
    window.__pribluda_tw_intercept = true;

    var SERVER = 'http://127.0.0.1:8765/robots';
    var RS = '\u001e'; // разделитель кадров SignalR
    var OrigWS = window.WebSocket;
    if (!OrigWS) return;

    function send(robots) {
        try {
            fetch(SERVER, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ robots: robots }),
                keepalive: true
            }).catch(function () { /* сервер не поднят — молча пропускаем */ });
        } catch (e) { /* игнор */ }
    }

    function collectRobots(payload) {
        var out = [];
        var fields = ['active', 'completed', 'completedRemoved'];
        for (var i = 0; i < fields.length; i++) {
            var arr = payload[fields[i]];
            if (Array.isArray(arr)) {
                for (var j = 0; j < arr.length; j++) {
                    out.push(arr[j]);
                }
            }
        }
        return out;
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
            if (target.indexOf('onrobot') !== 0) continue;
            var args = msg.arguments || [];
            var payload = args.length > 1 ? args[1] : args[0];
            if (!payload) continue;
            var robots = collectRobots(payload);
            if (robots.length === 0) continue;
            console.log('[tw-dbg] ' + target + ' robots=' + robots.length);
            send(robots);
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

    console.log('[pribluda] WebSocket patched, жду OnRobots ->', SERVER);
})();