(() => {
  "use strict";

  document.documentElement.setAttribute("data-popup-paint-pending", "true");

  const colorSchemeMeta = document.querySelector('meta[name="color-scheme"]');
  const setInitialColorScheme = (light) => {
    if (colorSchemeMeta) colorSchemeMeta.setAttribute("content", light ? "light dark" : "dark light");
    document.documentElement.style.colorScheme = light ? "light" : "dark";
    document.documentElement.style.backgroundColor = light ? "#FAF8F5" : "#0D0E11";
  };

  try {
    const theme = localStorage.getItem("pasi.popup.theme");
    if (theme === "light") {
      document.documentElement.classList.add("light-theme");
      setInitialColorScheme(true);
    } else if (theme === "dark") {
      setInitialColorScheme(false);
    } else {
      document.documentElement.style.backgroundColor = "#0D0E11";
      document.documentElement.style.colorScheme = "dark";
      document.documentElement.setAttribute("data-theme-pending", "true");
    }
  } catch (_) {
    // Fall back to the stylesheet's dark theme if localStorage is unavailable.
  }
})();
