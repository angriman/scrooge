// The net-worth chart: one line for the total and one per part, drawn with uPlot from the
// JSON of the page's own address. Nothing here computes money: amounts arrive to the cent
// and are only drawn and printed.
"use strict";

(function () {
  const figure = document.getElementById("networth");
  if (!figure || typeof uPlot === "undefined") return;
  const plot = figure.querySelector(".plot");
  const DAY = 86400;
  const HEIGHT = 420;

  // Colours come from the stylesheet, so that light and dark are chosen in one place.
  function ink(name) {
    return getComputedStyle(figure).getPropertyValue(name).trim();
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
    const colour = (line) => (line.slot ? ink("--series-" + line.slot) : ink("--text-primary"));

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
      data.series.forEach((line, n) => {
        if (!u.series[n + 1].show) return;
        const entry = row("entry", [
          ["i", ""],
          ["strong", money.format(columns[n][at])],
          ["span", line.name],
        ]);
        entry.firstChild.style.background = colour(line);
        tip.append(entry);
      });
      tip.hidden = false;
      // Beside the cursor, on whichever side has room.
      const left = u.cursor.left + u.over.offsetLeft;
      const room = left + tip.offsetWidth + 24 < plot.clientWidth;
      tip.style.left = (room ? left + 16 : left - tip.offsetWidth - 16) + "px";
      tip.style.top = u.over.offsetTop + 8 + "px";
    }

    const options = {
      width: plot.clientWidth,
      height: HEIGHT,
      tzDate: (stamp) => uPlot.tzDate(new Date(stamp * 1000), "UTC"),
      legend: { show: false },
      cursor: { points: { size: 8 }, y: false, drag: { x: true, y: false } },
      scales: { x: { time: true } },
      axes: [
        { stroke: ink("--text-secondary"), grid: { show: false }, ticks: { stroke: ink("--grid") } },
        {
          stroke: ink("--text-secondary"),
          grid: { stroke: ink("--grid"), width: 1 },
          ticks: { show: false },
          size: 64,
          values: (_, ticks) => ticks.map((tick) => short.format(tick)),
        },
      ],
      series: [{}].concat(
        data.series.map((line) => ({
          label: line.name,
          stroke: colour(line),
          width: line.slot ? 2 : 2.5,
          points: { show: false },
        }))
      ),
      hooks: { setCursor: [readout] },
    };
    const chart = new uPlot(options, [days].concat(columns), plot);

    new ResizeObserver(() => chart.setSize({ width: plot.clientWidth, height: HEIGHT })).observe(
      plot
    );

    for (const button of figure.querySelectorAll(".legend button")) {
      button.addEventListener("click", () => {
        const shown = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", String(shown));
        chart.setSeries(Number(button.dataset.series), { show: shown });
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
