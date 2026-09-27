(() => {
  "use strict";

  function copyText(value) {
    const text = String(value ?? "");
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-10000px";
    area.style.top = "-10000px";
    (document.body || document.documentElement).appendChild(area);
    area.focus();
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } finally {
      area.remove();
    }
    if (!ok) {
      return Promise.reject(new Error("PASI copy operation was rejected by the browser"));
    }
    return Promise.resolve({ok: true});
  }

  globalThis.PASIClipboardAPI = Object.freeze({copyText});
  const previous = globalThis.PASI;
  if (previous) {
    globalThis.PASI = Object.freeze({...previous, copyText});
  }
})();
