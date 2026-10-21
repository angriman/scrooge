// A category is typed into a field that suggests the categories there are (the page's
// <datalist id="categories">), but a browser lets anything be typed there. Here a name typed in
// other letters is written as the category spells it, and a name that is no category marks the
// field, so the form is not sent with it. Without this script the server says which name it
// does not know, and nothing is set.
"use strict";

(function () {
  const list = document.getElementById("categories");
  if (!list) return;
  const names = new Map();
  for (const option of list.options) names.set(option.value.toLowerCase(), option.value);
  const FIELD = 'input[list="categories"]';

  function check(field, tidy) {
    const typed = tidy ? field.value.trim() : field.value;
    const name = typed === "" ? "" : names.get(typed.toLowerCase());
    if (tidy && name && name !== field.value) {
      field.value = name;
      field.dispatchEvent(new Event("input", { bubbles: true })); // the totals follow
    }
    field.setCustomValidity(name === undefined ? "Not a category: choose one of the list." : "");
  }

  document.addEventListener("input", (event) => {
    if (event.target.matches(FIELD)) check(event.target, false);
  });
  document.addEventListener("change", (event) => {
    if (event.target.matches(FIELD)) check(event.target, true);
  });
})();
