// A ring beside its table (the overview's of what is held, the analysis page's of spending
// and income): pointing at a slice, or at its row in the table beside it, prints its name,
// amount and share in the middle of the ring; leaving it prints the whole again. On a phone a
// tap does the same and a tap elsewhere goes back. Nothing here computes money: every figure
// arrives printed, in the slice's own attributes.
"use strict";

(function () {
  const rings = [];
  for (const figure of document.querySelectorAll(".today .donut")) {
    const today = figure.closest(".today");
    const svg = figure.querySelector("svg");
    const lines = ["what", "amount", "share"].map((name) => svg.querySelector("text." + name));
    const whole = lines.map((line) => line.textContent);
    const slices = new Map();
    for (const slice of svg.querySelectorAll(".slice")) slices.set(slice.dataset.slot, slice);

    function show(slot) {
      const slice = slices.get(slot);
      for (const other of slices.values()) other.classList.toggle("on", other === slice);
      svg.classList.toggle("pointing", Boolean(slice));
      const texts = slice ? [slice.dataset.name, slice.dataset.value, slice.dataset.share] : whole;
      lines.forEach((line, n) => (line.textContent = texts[n]));
    }

    for (const element of today.querySelectorAll(".slice, tr[data-slot]")) {
      element.addEventListener("pointerenter", () => show(element.dataset.slot));
      element.addEventListener("pointerleave", (event) => {
        if (event.pointerType === "mouse") show(null);
      });
    }
    rings.push({ today, show });
  }
  document.addEventListener("click", (event) => {
    const pointed = event.target.closest(".today .slice, .today tr[data-slot]");
    for (const ring of rings) {
      ring.show(pointed && ring.today.contains(pointed) ? pointed.dataset.slot : null);
    }
  });
})();
