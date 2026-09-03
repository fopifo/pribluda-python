// ==UserScript==
// @name         T-Widgets Robots Interceptor
// @namespace    pribluda
// @version      1.0
// @description  Перехватывает SignalR-событие OnRobots2 расширения T-Widgets и шлёт на локальный сервер приблуды
// @match        https://www.tbank.ru/terminal/*
// @match        https://www.tbank.ru/terminal-beta/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const SERVER = 'http://127.0.0.1:8765/robots';
    const RS = '\u001e'; // разделитель кадров SignalR

    const OrigWS = window.WebSocket;
    if (!OrigWS) return;

    function send(payload) {
        const body = JSON.stringify({ robots: payload.robots });
        fetch(SERVER, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body,
            keepalive: true
        }).catch(function () { /* сервер не поднят — молча пропускаем */ });
        console.debug('[tw-intercept] OnRobots2:', (payload.robots || []).length, 'robots');
    }

    function handleFrame(text) {
        const frames = text.split(RS);
        for (let i = 0; i < frames.length; i++) {
            const frame = frames[i];
            if (!frame) continue;
            let msg;
            try { msg = JSON.parse(frame); } catch (e) { continue; }
            if (!msg || msg.type !== 1) continue; // 1 = Invocation
            const target = String(msg.target || '').toLowerCase();
            if (target !== 'onrobots2') continue;
            const args = msg.arguments || [];
            const payload = args.length > 1 ? args[1] : args[0];
            if (!payload || !Array.isArray(payload.robots)) continue;
            try { send(payload); } catch (e) { /* никогда не ломаем страницу */ }
        }
    }

    window.WebSocket = function (url, protocols) {
        const ws = (protocols !== undefined) ? new OrigWS(url, protocols) : new OrigWS(url);
        ws.addEventListener('message', function (ev) {
            try {
                if (typeof ev.data === 'string') handleFrame(ev.data);
            } catch (e) { /* игнор */ }
        });
        return ws;
    };
    // наследуем прототип и статику, чтобы SignalR-клиент работал как с родным классом
    window.WebSocket.prototype = OrigWS.prototype;
    Object.setPrototypeOf(window.WebSocket, OrigWS);
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) {
        window.WebSocket[k] = OrigWS[k];
    });

    console.log('[tw-intercept] WebSocket patched, жду OnRobots2 ->', SERVER);
})();