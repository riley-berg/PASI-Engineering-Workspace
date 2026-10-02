(() => {
  "use strict";

  const root = document.documentElement;
  const colorSchemeMeta = document.querySelector('meta[name="color-scheme"]');

  let theme = "dark";
  try {
    theme = localStorage.getItem("pasi.popup.theme") === "light" ? "light" : "dark";
  } catch (_) {
    theme = "dark";
  }

  const light = theme === "light";
  root.classList.toggle("light-theme", light);
  root.style.colorScheme = light ? "light" : "dark";
  root.style.backgroundColor = light ? "#FAF8F5" : "#0D0E11";

  if (colorSchemeMeta) {
    colorSchemeMeta.setAttribute("content", light ? "light dark" : "dark light");
  }
})();
