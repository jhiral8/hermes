// Applies the saved appearance before the page paints (no flash). "system" follows the device.
"use strict";
try {
  const t = localStorage.getItem("hermes-theme");
  if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
} catch (_) { /* private mode: follow the device */ }
