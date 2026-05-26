(function() {
    if (window.__ghostty || window.__ghosttyLoading) return;
    window.__ghosttyLoading = true;
    var s = document.createElement('script');
    s.type = 'module';
    s.textContent = [
        "import { Ghostty, Terminal, FitAddon } from '/ghostty/ghostty-web.js';",
        "try {",
        "  const instance = await Ghostty.load('/ghostty/ghostty-vt.wasm');",
        "  window.__ghostty = { Ghostty, Terminal, FitAddon, instance };",
        "} catch(e) {",
        "  console.error('ghostty-web init failed:', e);",
        "  window.__ghosttyError = e;",
        "}",
        "window.dispatchEvent(new CustomEvent('ghostty-ready'));",
    ].join('\n');
    document.head.appendChild(s);
})();
