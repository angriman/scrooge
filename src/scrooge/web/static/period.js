// A From or To field (input[data-period]) gets a button beside it that opens a calendar under
// it: a year with its twelve months, or one month with its days. A month writes 2025-05, the
// year at the top writes 2025, a month's name at the top of its days writes the month, a day
// writes 2025-05-17: what the field is typed with, which the server reads as before (To with
// a month or a year is through its end). A few quick choices fill From and To together. The
// field stays a text field; without this script it is only typed in.
"use strict";

(function () {
  const fields = document.querySelectorAll("input[data-period]");
  if (!fields.length) return;
  const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
  let open = null; // the picker shown, if any

  const pad = (n) => String(n).padStart(2, "0");
  const monthText = (year, month) => `${year}-${pad(month + 1)}`; // month from 0
  const dayText = (year, month, day) => `${monthText(year, month)}-${pad(day)}`;

  function calendarDay(year, month, day) {
    const when = new Date(2000, 0, 1);
    when.setFullYear(year, month, day); // also for a year below 100
    return when;
  }

  const daysIn = (year, month) => calendarDay(year, month + 1, 0).getDate();

  function today() {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth(), day: now.getDate() };
  }

  // What the field names, as the server reads it: a year, a month or a day; else null.
  function read(text) {
    const found = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(text.trim());
    if (!found) return null;
    const year = Number(found[1]);
    if (found[2] === undefined) return { year };
    const month = Number(found[2]) - 1;
    if (month < 0 || month > 11) return null;
    if (found[3] === undefined) return { year, month };
    const day = Number(found[3]);
    return day >= 1 && day <= daysIn(year, month) ? { year, month, day } : null;
  }

  function make(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    if (tag === "button") element.type = "button"; // inside the form: never a submit
    return element;
  }

  function write(field, value) {
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // The quick choices: each gives the text of From and of To.
  const QUICK = [
    ["This month", (now) => monthText(now.year, now.month)],
    ["Last month", (now) => monthText(now.year - (now.month ? 0 : 1), (now.month + 11) % 12)],
    ["This year", (now) => String(now.year)],
    ["Last year", (now) => String(now.year - 1)],
  ];

  function picker(field) {
    const form = field.form;
    const name = field.closest("label")?.firstChild?.textContent.trim() || field.name;
    const box = make("span", "period");
    field.before(box);
    box.append(field);

    const button = make("button", "pick");
    const glyph = make("span", "glyph"); // a calendar's page, drawn by the stylesheet
    glyph.setAttribute("aria-hidden", "true");
    button.append(glyph);
    button.setAttribute("aria-label", `Pick ${name}`);
    button.title = `Pick ${name}: a year, a month or a day`;
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-expanded", "false");

    const pane = make("div", "calendar");
    pane.hidden = true;
    pane.id = `calendar-${field.name}`;
    pane.setAttribute("role", "dialog");
    pane.setAttribute("aria-label", `${name}: a year, a month or a day`);
    button.setAttribute("aria-controls", pane.id);

    const head = make("div", "head");
    const back = make("button", "step", "‹");
    const title = make("button", "whole");
    const ahead = make("button", "step", "›");
    head.append(back, title, ahead);
    const grid = make("div", "grid");
    const switcher = make("button", "switch");
    pane.append(head, grid, switcher);

    const from = form && form.querySelector('input[data-period="from"]');
    const to = form && form.querySelector('input[data-period="to"]');
    if (from && to) {
      const quick = make("div", "quick");
      for (const [label, text] of QUICK) {
        const choice = make("button", "", label);
        choice.addEventListener("click", () => {
          const value = text(today());
          write(from, value);
          write(to, value);
          close(true);
        });
        quick.append(choice);
      }
      pane.append(quick);
    }
    box.append(button, pane);

    let chosen = null; // what the field named when the picker opened
    const shown = { days: false, year: 0, month: 0 };

    function cell(text, label, value, isChosen, isNow) {
      const choice = make("button", "", text);
      choice.dataset.value = value;
      choice.setAttribute("aria-label", label);
      choice.classList.toggle("on", isChosen);
      choice.setAttribute("aria-pressed", String(isChosen));
      if (isNow) {
        choice.classList.add("now");
        choice.setAttribute("aria-current", "date");
      }
      grid.append(choice);
    }

    function render() {
      const { days, year, month } = shown;
      const now = today();
      const at = chosen && chosen.year === year ? chosen : null;
      grid.replaceChildren();
      grid.classList.toggle("days", days);
      if (days) {
        const whole = `${MONTHS[month]} ${year}`;
        title.textContent = whole;
        title.setAttribute("aria-label", `All of ${whole}`);
        title.classList.toggle("on", Boolean(at && at.month === month && at.day === undefined));
        back.setAttribute("aria-label", "Previous month");
        ahead.setAttribute("aria-label", "Next month");
        switcher.textContent = "Back to the months";
        for (const weekday of WEEKDAYS) {
          const heading = make("span", "weekday", weekday);
          heading.setAttribute("aria-hidden", "true");
          grid.append(heading);
        }
        const blanks = (calendarDay(year, month, 1).getDay() + 6) % 7; // the week from Monday
        for (let n = 0; n < blanks; n++) grid.append(make("span"));
        const thisMonth = now.year === year && now.month === month;
        for (let day = 1; day <= daysIn(year, month); day++) {
          const isChosen = Boolean(at && at.month === month && at.day === day);
          const label = `${day} ${MONTHS[month]} ${year}`;
          const isNow = thisMonth && now.day === day;
          cell(String(day), label, dayText(year, month, day), isChosen, isNow);
        }
      } else {
        title.textContent = String(year);
        title.setAttribute("aria-label", `All of ${year}`);
        title.classList.toggle("on", Boolean(at && at.month === undefined));
        back.setAttribute("aria-label", "Previous year");
        ahead.setAttribute("aria-label", "Next year");
        switcher.textContent = "Pick a day";
        MONTHS.forEach((monthName, n) => {
          const isChosen = Boolean(at && at.month === n && at.day === undefined);
          const label = `${monthName} ${year}`;
          const isNow = now.year === year && now.month === n;
          cell(monthName.slice(0, 3), label, monthText(year, n), isChosen, isNow);
        });
      }
    }

    function choose(value) {
      write(field, value);
      close(true);
    }

    function show() {
      if (open) open.close(false);
      chosen = read(field.value);
      const now = today();
      const start = chosen || now;
      shown.days = Boolean(chosen && chosen.day !== undefined);
      shown.year = start.year;
      shown.month = start.month ?? (start.year === now.year ? now.month : 0);
      render();
      pane.hidden = false;
      button.setAttribute("aria-expanded", "true");
      // Under the field from its left edge, or from its right edge where that would leave the
      // screen (the To field of a phone): the page never scrolls sideways.
      pane.classList.remove("flip");
      if (pane.getBoundingClientRect().right > document.documentElement.clientWidth) {
        pane.classList.add("flip");
      }
      open = { box, close };
      const first = grid.querySelector(".on") || grid.querySelector(".now");
      (first || grid.querySelector("button")).focus();
    }

    function close(focusField) {
      pane.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (open && open.box === box) open = null;
      if (focusField) field.focus();
    }

    button.addEventListener("click", () => (pane.hidden ? show() : close(true)));
    field.addEventListener("keydown", (event) => {
      if (event.altKey && event.key === "ArrowDown") {
        event.preventDefault();
        show();
      }
    });
    back.addEventListener("click", () => step(-1));
    ahead.addEventListener("click", () => step(1));

    function step(by) {
      if (!shown.days) shown.year += by;
      else {
        const month = shown.month + by;
        shown.year += Math.floor(month / 12);
        shown.month = (month + 12) % 12;
      }
      render();
    }

    title.addEventListener("click", () => {
      choose(shown.days ? monthText(shown.year, shown.month) : String(shown.year));
    });
    switcher.addEventListener("click", () => {
      if (!shown.days) {
        const now = today();
        const at = chosen && chosen.year === shown.year && chosen.month !== undefined;
        shown.month = at ? chosen.month : shown.year === now.year ? now.month : 0;
      }
      shown.days = !shown.days;
      render();
    });
    grid.addEventListener("click", (event) => {
      const choice = event.target.closest("button");
      if (choice) choose(choice.dataset.value);
    });
    // The arrows move between the months or the days, as they are laid out.
    grid.addEventListener("keydown", (event) => {
      const across = shown.days ? 7 : 3;
      const by = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -across, ArrowDown: across }[event.key];
      if (!by) return;
      const cells = [...grid.querySelectorAll("button")];
      const next = cells[cells.indexOf(event.target) + by];
      if (next) next.focus();
      event.preventDefault();
    });
    // The pane is inside the field's label: a click on it, not on a button, is not the
    // label's (which would put the focus in the field).
    pane.addEventListener("click", (event) => {
      if (!event.target.closest("button")) event.preventDefault();
    });
    // Tabbing out of the field and its pane closes it. A click inside that focuses nothing
    // (as in Safari) has no related target and leaves it open.
    box.addEventListener("focusout", (event) => {
      if (!pane.hidden && event.relatedTarget && !box.contains(event.relatedTarget)) close(false);
    });
  }

  for (const field of fields) picker(field);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      open.close(true);
    }
  });
  document.addEventListener("click", (event) => {
    if (open && !open.box.contains(event.target)) open.close(false);
  });
})();
