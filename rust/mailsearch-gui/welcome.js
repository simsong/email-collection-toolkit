// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Present startup actions without blocking Finder/Dock document delivery.
// Native IPC owns archive selection and initialization, not this page.
// The real helper capability response enables New; Open always stays available.
// Failures remain visible here and successful selection navigates to recovery.
// A status request also covers capabilities delivered before this page loads.
window.__rustWelcomeCapabilities = status => {
    window.__rustWelcomeWritable = Boolean(status.write_available);
    document.getElementById('new').disabled = !window.__rustWelcomeWritable;
};
document.addEventListener('DOMContentLoaded', () => {
    for (const [id, method] of [['open', 'welcome_open'], ['new', 'welcome_new'], ['quit', 'quit']]) {
        document.getElementById(id).addEventListener('click', () => {
            Promise.resolve().then(() => window.pywebview.api[method]()).catch(error => {
                document.getElementById('detail').textContent = error.message;
            });
        });
    }
    window.__rustWelcomeCapabilities({write_available: window.__rustWelcomeWritable});
    Promise.resolve().then(() => window.pywebview.api.welcome_status()).then(window.__rustWelcomeCapabilities).catch(error => {
        document.getElementById('detail').textContent = error.message;
    });
});
