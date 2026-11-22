// The draft on the import page: when a category is chosen for a row, or for the counterparty
// a waiting row belongs to (its data-group), the totals by category are worked out again here,
// at once, from the rows on the page, and the row is marked. A row chosen on its own keeps its
// choice, as the server does; else its field shows its counterparty's choice.
// Money is never a float: each amount arrives as exact decimal text (data-amount) and is
// counted in billionths, the unit the database stores, as a BigInt; only the sum is rounded,
// to the cent, half to even as the server does, when it is written. Without this script the
// page is the same, and the totals follow once the categories are saved.
"use strict";

(function () {
  const rows = document.querySelector("#rows tbody");
  const totals = document.querySelector("#totals tbody");
  const list = document.getElementById("categories");
  if (!rows || !totals || !list) return;
  const SCALE = 9; // decimals of a billionth
  const SEP = "\u0000";

  // "-45.2" -> -45200000000n
  function nano(text) {
    const negative = text.startsWith("-");
    const [whole, fraction = ""] = text.replace(/^[-+]/, "").split(".");
    const value = BigInt(whole + fraction.padEnd(SCALE, "0").slice(0, SCALE));
    return negative ? -value : value;
  }

  // billionths -> "-1,234.50", the cent rounded half to even
  function money(value) {
    const unit = 10n ** BigInt(SCALE - 2);
    const negative = value < 0n;
    const size = negative ? -value : value;
    let cents = size / unit;
    const rest = size % unit;
    const half = unit / 2n;
    if (rest > half || (rest === half && cents % 2n === 1n)) cents += 1n;
    const whole = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const fraction = (cents % 100n).toString().padStart(2, "0");
    return (negative ? "-" : "") + whole + "." + fraction;
  }

  // Each category name and the sides it is on: a name can be both income and an expense.
  const sides = new Map();
  for (const option of list.options) {
    if (!sides.has(option.value)) sides.set(option.value, new Set());
    sides.get(option.value).add(option.dataset.side);
  }

  // The side a category name takes for an amount, as the server does it: a transfer is a
  // transfer; otherwise the side the sign points to, then the other one.
  function sideOf(name, amount) {
    const on = sides.get(name);
    if (on.has("transfer")) return "transfer";
    const order = amount < 0n ? ["uscite", "entrate"] : ["entrate", "uscite"];
    return order.find((side) => on.has(side)) || "";
  }

  // The category a field holds, "" while it holds none or a name that is no category.
  function named(field) {
    return field && sides.has(field.value) ? field.value : "";
  }

  function chosen(row) {
    const field = row.querySelector('input[list="categories"]');
    const group = row.dataset.group === undefined ? null
      : document.querySelector('input[name="group:' + row.dataset.group + '"]');
    if (field) {
      if (field.dataset.shown === undefined) field.dataset.shown = field.placeholder;
      field.placeholder = named(group) || field.dataset.shown;
    }
    const amount = nano(row.dataset.amount);
    const name = named(field) || named(group);
    const changed = name !== "";
    if (!changed) return { side: row.dataset.side, category: row.dataset.category, amount, changed };
    return { side: sideOf(name, amount), category: name, amount, changed };
  }

  function line(side, category, currency) {
    const tr = document.createElement("tr");
    tr.dataset.side = side;
    tr.dataset.category = category;
    tr.dataset.currency = currency;
    const lead = document.createElement("td");
    lead.className = "lead";
    lead.append(category);
    if (side === "transfer") {
      const small = document.createElement("small");
      small.textContent = "transfer";
      lead.append(small);
    }
    const sum = document.createElement("td");
    sum.className = "number total sum";
    sum.dataset.unit = currency;
    const count = document.createElement("td");
    count.className = "number count";
    count.dataset.label = "Rows";
    tr.append(lead, sum, count);
    return tr;
  }

  function update() {
    const sums = new Map();
    for (const row of rows.querySelectorAll("tr[data-id]")) {
      const { side, category, amount, changed } = chosen(row);
      row.classList.toggle("changed", changed);
      const key = [side, category, row.dataset.currency].join(SEP);
      const entry = sums.get(key) || { count: 0, total: 0n };
      entry.count += 1;
      entry.total += amount;
      sums.set(key, entry);
    }
    for (const tr of totals.querySelectorAll("tr")) {
      const key = [tr.dataset.side, tr.dataset.category, tr.dataset.currency].join(SEP);
      show(tr, sums.get(key));
      sums.delete(key);
    }
    for (const [key, entry] of sums) {
      const [side, category, currency] = key.split(SEP);
      const tr = line(side, category, currency);
      const waiting = totals.querySelector('tr[data-category=""]');
      totals.insertBefore(tr, waiting);
      show(tr, entry);
    }
  }

  function show(tr, entry) {
    tr.hidden = !entry;
    if (!entry) return;
    tr.querySelector(".sum").textContent = money(entry.total) + " " + tr.dataset.currency;
    tr.querySelector(".count").textContent = String(entry.count);
  }

  document.addEventListener("input", (event) => {
    if (event.target.matches('input[list="categories"]')) update();
  });
  update();
})();
