// A panel that shows the same things two ways (the holdings as a map or as a list): its
// buttons choose, and the choice is kept in this browser. Until one is made a wide screen
// shows the map and a phone the list; without this script the stylesheet decides alone.
"use strict";

(function () {
  const PHONE = "(max-width: 40rem)";
  for (const panel of document.querySelectorAll("[data-views]")) {
    const kept = "scrooge.views." + panel.dataset.views;
    const buttons = panel.querySelectorAll("button[data-view]");

    function show(view) {
      panel.dataset.view = view;
      for (const button of buttons) {
        button.setAttribute("aria-pressed", String(button.dataset.view === view));
      }
    }

    let chosen = null;
    try {
      chosen = localStorage.getItem(kept);
    } catch (error) {
      chosen = null; // no storage here: the screen decides
    }
    show(chosen || (window.matchMedia(PHONE).matches ? "list" : "map"));
    for (const button of buttons) {
      button.hidden = false;
      button.addEventListener("click", () => {
        show(button.dataset.view);
        try {
          localStorage.setItem(kept, button.dataset.view);
        } catch (error) {
          // kept for this page only
        }
      });
    }
  }
})();
