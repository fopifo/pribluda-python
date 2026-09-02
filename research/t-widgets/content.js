'use strict';

function injectScript(url, tag) {
    var tagName = document.getElementsByTagName(tag)[0],
        el = document.createElement("script");

    el.setAttribute("type", "module")
    el.setAttribute("src", url)
    tagName.appendChild(el)
}

function injectToken(token) {
    let div = document.getElementById('twidgets-token');
    if (!div) {
        div = document.createElement('div');
        div.id = 'twidgets-token';
        div.style.display = 'none';
        document.documentElement.appendChild(div);
    }
    div.textContent = token || '';
}

function injectExperimentalFeaturesEnabled(enabled) {
    let div = document.getElementById('twidgets-experimental-features');
    if (!div) {
        div = document.createElement('div');
        div.id = 'twidgets-experimental-features';
        div.style.display = 'none';
        document.documentElement.appendChild(div);
    }
    div.textContent = enabled ? 'true' : 'false';
    window.dispatchEvent(new CustomEvent('twidgets-experimental-features-changed'));
}

chrome.storage.sync.get(['token', 'experimentalFeaturesEnabled'], data => {
    if(data && data.token) {
        injectToken(data.token);
    }
    injectExperimentalFeaturesEnabled(Boolean(data && data.experimentalFeaturesEnabled));
    injectScript(chrome.runtime.getURL("js/main.js?t=" + Date.now()), "body");
});

chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'sync' || !changes.experimentalFeaturesEnabled) return;
    injectExperimentalFeaturesEnabled(Boolean(changes.experimentalFeaturesEnabled.newValue));
});