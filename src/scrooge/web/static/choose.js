// A form marked data-auto is sent as soon as one of its menus changes: the currency in the
// header, the breakdown of the net-worth chart. Its button is for a browser without this
// script, and is taken away here.
"use strict";

(function () {
  for (const form of document.querySelectorAll("form[data-auto]")) {
    for (const button of form.querySelectorAll("button")) button.remove();
    form.addEventListener("change", (event) => {
      if (event.target.matches("select")) form.requestSubmit();
    });
  }
})();
