// The draft on the import page: when a category is chosen for a row, the totals by category
// are worked out again here, at once, from the rows on the page, and the row is marked.
// Money is never a float: each amount arrives as exact decimal text (data-amount) and is
// counted in billionths, the unit the database stores, as a BigInt; only the sum is rounded,
// to the cent, half to even as the server does, when it is written. Without this script the
// page is the same, and the totals follow once the categories are saved.
"use strict";

(function () {
  const rows = document.querySelector("#rows tbody");
  const totals = document.querySelector("#totals tbody");
  if (!rows || !totals) return;
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

  // The side a category name takes for an amount, as the server does it: a transfer is a
  // transfer; otherwise the side the sign points to, then the other one.
  function sideOf(select, name, amount) {
    const sides = new Set();
    for (const option of select.options) {
      if (option.value === name) sides.add(option.dataset.side);
    }
    if (sides.has("transfer")) return "transfer";
    const order = amount < 0n ? ["uscite", "entrate"] : ["entrate", "uscite"];
    return order.find((side) => sides.has(side)) || "";
  }

  function chosen(row) {
    const select = row.querySelector("select");
    const amount = nano(row.dataset.amount);
    const name = select ? select.value : "";
    const changed = name !== "";
    if (!changed) return { side: row.dataset.side, category: row.dataset.category, amount, changed };
    return { side: sideOf(select, name, amount), category: name, amount, changed };
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

  rows.addEventListener("change", update);
  update();
})();
