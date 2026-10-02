(() => {
  "use strict";

  document.documentElement.setAttribute("data-popup-paint-pending", "true");

  try {
    const theme = localStorage.getItem("pasi.popup.theme");
    if (theme === "light") {
      document.documentElement.classList.add("light-theme");
      document.documentElement.style.backgroundColor = "#FAF8F5";
      document.documentElement.style.colorScheme = "light";
    } else if (theme === "dark") {
      document.documentElement.style.backgroundColor = "#0D0E11";
      document.documentElement.style.colorScheme = "dark";
    } else {
      document.documentElement.style.backgroundColor = "#0D0E11";
      document.documentElement.style.colorScheme = "dark";
      document.documentElement.setAttribute("data-theme-pending", "true");
    }
  } catch (_) {
    // Fall back to the stylesheet's dark theme if localStorage is unavailable.
  }
})();
