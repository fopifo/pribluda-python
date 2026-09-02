chrome.runtime.onInstalled.addListener(function (object) {
    const currentVersion = chrome.runtime.getManifest().version;
    const previousVersion = object.previousVersion;

    if (object.reason === 'update') {
        console.log(`update ${previousVersion} -> ${currentVersion}`)
    }

    if (object.reason === 'install') {
    }
});