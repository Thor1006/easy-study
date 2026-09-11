"use strict";
// The host validates all illustration data. Only text nodes and fixed SVG shapes
// are rendered; model-supplied markup, URLs, and code are never evaluated.
function illustratedAnswer(run) {
  const box = h("div", {class: "answer"});
  const parts = run.answer_parts || [{type: "text", text: run.answer || ""}];
  for (const part of parts) {
    if (part.type === "text") {
      box.append(h("div", {class: "answer-prose", text: part.text}));
      continue;
    }
    const card = h("figure", {class: "illustration"}, h("h3", {text: part.title}));
    if (part.type === "flow") {
      const steps = h("ol", {class: "visual-steps"});
      part.steps.forEach(step => steps.append(h("li", {text: step})));
      card.append(steps);
    } else if (part.type === "table") {
      const table = h("table", {class: "visual-table"},
        h("thead", {}, h("tr", {}, ...part.columns.map(c => h("th", {scope: "col", text: c})))),
        h("tbody", {}, ...part.rows.map(row => h("tr", {}, ...row.map(c => h("td", {text: c}))))));
      card.append(h("div", {class: "visual-scroll", tabindex: "0"}, table));
    } else if (part.type === "bar") {
      const lo = Math.min(0, ...part.values), hi = Math.max(0, ...part.values);
      const span = hi - lo || 1, left = 160, width = 360;
      const zero = left + (-lo / span) * width;
      const chart = svg("svg", {viewBox: `0 0 600 ${part.values.length * 58 + 20}`, role: "img", "aria-label": part.title});
      chart.append(svg("title", {}, part.title));
      chart.append(svg("line", {x1: zero, x2: zero, y1: 0, y2: part.values.length*58, stroke: "currentColor", opacity: ".25"}));
      part.values.forEach((v, i) => {
        const x = left + ((v-lo)/span)*width, y = i*58 + 26;
        chart.append(svg("text", {x: 0, y, fill: "currentColor", "font-size": 13}, part.labels[i].slice(0, 22)),
          svg("rect", {x: Math.min(x,zero), y: y-15, width: Math.abs(x-zero), height: 24, rx: 5, fill: "var(--accent)"}),
          svg("text", {x: 535, y, fill: "currentColor", "font-size": 13}, String(v)));
      });
      card.append(chart, h("details", {}, h("summary", {text: "View chart data"}),
        ...part.labels.map((label, i) => h("div", {text: `${label}: ${part.values[i]}`}))));
    }
    card.append(h("figcaption", {text: part.caption}));
    box.append(card);
  }
  return box;
}
