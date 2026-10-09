// The net-worth chart, drawn with uPlot from the JSON of the page's own address: the total as
// a line and, when it is broken down, its parts as bands stacked in the order they are served.
// On each day what is above zero is stacked upwards from zero and what is below (a card, cash
// owed) downwards, so no band hides another and the tops less the bottoms are the line.
// Nothing here computes money: amounts arrive to the cent and are only drawn and printed; the
// running sums place the bands and are never shown.
"use strict";

(function () {
  const figure = document.getElementById("networth");
  if (!figure || typeof uPlot === "undefined") return;
  const plot = figure.querySelector(".plot");
  const DAY = 86400;
  const HEIGHT = 420;

  // Colours come from the stylesheet, so that light and dark are chosen in one place. They
  // are read off an element, which turns whatever the stylesheet says into what a canvas takes.
  const probe = document.createElement("span");
  probe.hidden = true;
  figure.append(probe);
  function ink(name) {
    probe.style.color = "var(" + name + ")";
    return getComputedStyle(probe).color;
  }

  function row(className, texts) {
    const line = document.createElement("div");
    line.className = className;
    for (const [tag, text] of texts) {
      const cell = document.createElement(tag);
      cell.textContent = text;
      line.append(cell);
    }
    return line;
  }

  function draw(data) {
    const start = Date.parse(data.first + "T00:00:00Z") / 1000;
    const count = data.series[0].values.length;
    const days = Array.from({ length: count }, (_, n) => start + n * DAY);
    const columns = data.series.map((line) => line.values.map(Number));
    const money = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });
    const short = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
    const dated = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" });
    const colour = (line) => ink(line.slot ? "--series-" + line.slot : "--text-primary");
    const total = 0; // the first series served; the rest are its parts, from the bottom up
    const hidden = new Set(); // by slot
    const shown = (n) => !hidden.has(data.series[n].slot);
    let chart = null;
    let listed = []; // what the readout prints, as it is drawn from the top down

    const tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    plot.append(tip);

    function readout(u) {
      const at = u.cursor.idx;
      if (at == null || u.cursor.left < 0) {
        tip.hidden = true;
        return;
      }
      tip.replaceChildren(row("when", [["span", dated.format(new Date(days[at] * 1000))]]));
      for (const n of listed) {
        const line = data.series[n];
        const entry = row("entry", [
          ["i", ""],
          ["strong", money.format(columns[n][at])],
          ["span", line.name],
        ]);
        entry.firstChild.className = "key slot-" + line.slot;
        tip.append(entry);
      }
      tip.hidden = false;
      // Beside the cursor, on whichever side has room.
      const left = u.cursor.left + u.over.offsetLeft;
      const room = left + tip.offsetWidth + 24 < plot.clientWidth;
      tip.style.left = (room ? left + 16 : left - tip.offsetWidth - 16) + "px";
      tip.style.top = u.over.offsetTop + 8 + "px";
    }

    // The series uPlot draws for what is shown now. A band is two running sums: the one
    // below it and that plus its own values, upwards where they are above zero and downwards
    // where they are below.
    function stacked() {
      const series = [{}];
      const columnsDrawn = [days];
      const bands = [];
      const edge = { stroke: ink("--surface"), width: 1, points: { show: false }, fillTo: 0 };
      const sums = { 1: new Array(count).fill(0), [-1]: new Array(count).fill(0) };
      const below = { 1: 0, [-1]: 0 }; // the series each side's next band rests on
      const above = [];
      const under = []; // what is never above zero: listed after the rest
      for (let n = 1; n < data.series.length; n++) {
        if (!shown(n)) continue;
        (columns[n].some((value) => value > 0) ? above : under).push(n);
        for (const side of [1, -1]) {
          if (!columns[n].some((value) => value * side > 0)) continue;
          sums[side] = sums[side].map((sum, at) => sum + side * Math.max(side * columns[n][at], 0));
          series.push({ ...edge, label: data.series[n].name, fill: colour(data.series[n]) });
          columnsDrawn.push(sums[side]);
          if (below[side]) bands.push({ series: [series.length - 1, below[side]], dir: -side });
          below[side] = series.length - 1;
        }
      }
      listed = above.reverse().concat(under);
      if (shown(total)) {
        listed.unshift(total);
        series.push({
          label: data.series[total].name,
          stroke: colour(data.series[total]),
          width: 2,
          points: { show: false },
        });
        columnsDrawn.push(columns[total]);
      }
      return { series, bands, data: columnsDrawn };
    }

    // Drawn anew whenever a series is switched: the rest restack, the period shown stays.
    function build() {
      const period = chart && { min: chart.scales.x.min, max: chart.scales.x.max };
      if (chart) chart.destroy();
      const drawn = stacked();
      const line = shown(total) ? drawn.series.length - 1 : 0;
      const options = {
        width: plot.clientWidth,
        height: HEIGHT,
        tzDate: (stamp) => uPlot.tzDate(new Date(stamp * 1000), "UTC"),
        legend: { show: false },
        cursor: {
          // A dot on the total only: the edges of the bands are running sums.
          points: { size: (_, n) => (n === line ? 8 : 0), width: (_, n) => (n === line ? 2 : 0) },
          y: false,
          drag: { x: true, y: false },
        },
        scales: { x: { time: true } },
        axes: [
          {
            stroke: ink("--text-secondary"),
            grid: { show: false },
            ticks: { stroke: ink("--grid") },
          },
          {
            stroke: ink("--text-secondary"),
            grid: { stroke: ink("--grid"), width: 1 },
            ticks: { show: false },
            size: 64,
            values: (_, ticks) => ticks.map((tick) => short.format(tick)),
          },
        ],
        series: drawn.series,
        bands: drawn.bands,
        hooks: { setCursor: [readout] },
      };
      chart = new uPlot(options, drawn.data, plot);
      if (period) chart.setScale("x", period);
    }
    build();

    new ResizeObserver(() => chart.setSize({ width: plot.clientWidth, height: HEIGHT })).observe(
      plot
    );

    for (const button of figure.querySelectorAll(".legend button")) {
      button.addEventListener("click", () => {
        const show = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", String(show));
        hidden[show ? "delete" : "add"](Number(button.dataset.slot));
        build();
      });
    }

    const ranges = figure.querySelectorAll(".ranges button");
    for (const button of ranges) {
      button.addEventListener("click", () => {
        const months = Number(button.dataset.months);
        const last = days[count - 1];
        const from = new Date(last * 1000);
        from.setUTCMonth(from.getUTCMonth() - months);
        chart.setScale("x", { min: months ? Math.max(from / 1000, days[0]) : days[0], max: last });
        for (const other of ranges) other.setAttribute("aria-pressed", String(other === button));
      });
    }
  }

  fetch(figure.dataset.source)
    .then((answer) => (answer.ok ? answer.json() : Promise.reject(new Error(answer.status))))
    .then((data) => {
      if (data.first && data.series.length) draw(data);
    })
    .catch(() => {
      plot.textContent = "The chart could not be loaded: the figures are in the table below.";
    });

  // The pickers apply at once; the button stays for a browser without scripts.
  const view = document.getElementById("view");
  for (const select of view.querySelectorAll("select")) {
    select.addEventListener("change", () => view.submit());
  }
})();
