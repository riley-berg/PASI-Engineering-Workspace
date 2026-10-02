(() => {
  "use strict";

  try {
    const theme = localStorage.getItem("pasi.popup.theme");
    if (theme === "light") {
      document.documentElement.classList.add("light-theme");
    } else if (theme !== "dark") {
      document.documentElement.setAttribute("data-theme-pending", "true");
    }
  } catch (_) {
    // Fall back to the stylesheet's dark theme if localStorage is unavailable.
  }
})();
