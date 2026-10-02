(() => {
  "use strict";

  try {
    if (localStorage.getItem("pasi.popup.theme") === "light") {
      document.documentElement.classList.add("light-theme");
    }
  } catch (_) {
    // Fall back to the stylesheet's dark theme if localStorage is unavailable.
  }
})();
