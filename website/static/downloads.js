/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
// Enhance the homepage's ordinary installer links with a platform suggestion.
// Published release metadata supplies URLs; this code never constructs assets.
// The server selects stable installers, or previews before a stable release exists.
// Browser platform hints determine the primary button's macOS or Windows label.
// Unknown platforms and missing assets keep the generic downloads destination.
// Explicit platform links and Show all installers remain usable without scripting.
// Update stream preferences belong exclusively to the installed application.
(() => {
    "use strict";
    const root = document.getElementById("installer-downloads");
    if (!root) return;
    const button = document.getElementById("download-installer");
    const fallback = button.href;
    const agent = navigator.userAgent;
    const platformHint = navigator.userAgentData?.platform || agent || navigator.platform;
    const mobile = /Android|iPhone|iPad|iPod|Windows Phone/i.test(agent) ||
        (/Mac/i.test(platformHint) && navigator.maxTouchPoints > 1);
    const platform = mobile ? "" : /Win/i.test(platformHint) ? "windows" : /Mac/i.test(platformHint) ? "mac" : "";
    const url = platform ? root.dataset[platform] : "";
    button.textContent = platform ? `Download ${platform === "mac" ? "macOS" : "Windows"} installer` : "Download the installers";
    button.href = url || fallback;
    document.getElementById("download-status").textContent = !platform ?
        "Choose your platform on the downloads page." : url ? root.dataset.version :
        "No published installer is available for this platform. Show all installers to see available downloads.";
})();
