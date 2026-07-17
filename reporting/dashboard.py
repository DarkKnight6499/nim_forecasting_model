"""
Self-contained interactive HTML dashboard: one file, inline CSS/JS, all data
embedded as a single JSON blob, charts drawn as inline SVG by the embedded
JS. Zero external requests (no CDN, no fonts, no fetch), so it opens from
disk anywhere.

Scope note on the scenario toggle: config.RATE_SCENARIOS (Base, +/-100/200bps,
basis shocks) drives the NIM/LCR/NSFR charts and shares one label set, so one
multi-toggle filters all three together. Full-revaluation EVE uses a
different, fixed set of six standard IRRBB scenarios (parallel/steepener/
flattener/short-rate shocks) - a bar chart of six values, not a monthly
series - so it isn't toggle-filtered; there's nothing to decongest by hiding
bars one at a time. CET1, FTP, and AFS MTM are base-scenario-only reports
(pipeline.py doesn't compute them per scenario), so they're static too.
"""

import json
import math

import config

_TEMPLATE_PATH_TOKEN = "__DASHBOARD_DATA_JSON__"


def _clean(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _records(df, columns):
    return [{c: _clean(row[c]) for c in columns} for row in df.to_dict("records")]


def _build_data(r):
    scenarios = list(r.combined_summary["scenario"].unique())

    nim_by_scenario = {}
    for label, grp in r.combined_summary.groupby("scenario"):
        nim_by_scenario[label] = _records(grp[["month", "nim"]], ["month", "nim"])

    lcr_by_scenario = {}
    for label, grp in r.lcr_df.groupby("scenario"):
        lcr_by_scenario[label] = _records(grp[["month", "lcr", "lcr_stressed"]], ["month", "lcr", "lcr_stressed"])

    nsfr_by_scenario = {}
    for label, grp in r.nsfr_df.groupby("scenario"):
        nsfr_by_scenario[label] = _records(grp[["month", "nsfr"]], ["month", "nsfr"])

    cet1 = _records(r.capital_df[["month", "cet1_ratio", "rwa", "cet1_capital"]],
                     ["month", "cet1_ratio", "rwa", "cet1_capital"])

    eve_linear = _records(r.eve_df[["scenario", "delta_eve_pct_equity"]], ["scenario", "delta_eve_pct_equity"])
    eve_full_reval = _records(r.full_reval_eve_df[["scenario", "delta_eve_pct_equity_full_reval"]],
                               ["scenario", "delta_eve_pct_equity_full_reval"])

    ftp_rows = _records(
        r.ftp_monthly_df[["month", "total_customer_margin", "alm_desk_pnl", "total_nii"]],
        ["month", "total_customer_margin", "alm_desk_pnl", "total_nii"],
    )

    joint_view = _records(
        r.joint_view_df[["position", "side", "nim_contribution", "ftp_customer_margin",
                          "lcr_role", "lcr_impact", "nim_per_unit_lcr_cost"]],
        ["position", "side", "nim_contribution", "ftp_customer_margin", "lcr_role", "lcr_impact",
         "nim_per_unit_lcr_cost"],
    )

    mtm_rows = _records(
        r.mtm_summary_df[["month", "total_unrealized_gain", "buffer_limit", "buffer_available"]],
        ["month", "total_unrealized_gain", "buffer_limit", "buffer_available"],
    )

    backtest = None
    if r.backtest_df is not None:
        backtest = {"kind": "csv", "label": r.backtest_path, "rows": _records(
            r.backtest_df[["month", "net_interest_income", "actual_nii", "nii_error", "cumulative_nii_error",
                            "rate_variance", "volume_variance", "residual_unmodellable"]],
            ["month", "net_interest_income", "actual_nii", "nii_error", "cumulative_nii_error",
             "rate_variance", "volume_variance", "residual_unmodellable"],
        )}
    elif r.fdic_backtest_df is not None:
        label = f"{r.fdic_snapshot_fin.get('NAME')} (CERT {r.bank_cert}), as-of {r.fdic_snapshot_fin.get('REPDTE')}"
        backtest = {"kind": "fdic", "label": label, "rows": _records(
            r.fdic_backtest_df[["month", "net_interest_income", "actual_nii", "nii_error", "cumulative_nii_error",
                                 "rate_variance", "volume_variance", "residual_unmodellable"]],
            ["month", "net_interest_income", "actual_nii", "nii_error", "cumulative_nii_error",
             "rate_variance", "volume_variance", "residual_unmodellable"],
        )}

    return {
        "scenarios": scenarios,
        "base_scenario": r.base_label,
        "nim_by_scenario": nim_by_scenario,
        "lcr_by_scenario": lcr_by_scenario,
        "nsfr_by_scenario": nsfr_by_scenario,
        "cet1": cet1,
        "eve_linear": eve_linear,
        "eve_full_reval": eve_full_reval,
        "ftp": ftp_rows,
        "joint_view": joint_view,
        "mtm": mtm_rows,
        "backtest": backtest,
        "thresholds": {
            "lcr_regulatory_min": config.LCR_REGULATORY_MIN,
            "lcr_ras_threshold": config.LCR_RAS_THRESHOLD,
            "lcr_internal_target": config.LCR_INTERNAL_TARGET,
            "nsfr_regulatory_min": config.NSFR_REGULATORY_MIN,
            "cet1_regulatory_min": config.CET1_REGULATORY_MIN,
            "cet1_buffered_min": config.CET1_BUFFERED_MIN,
            "cet1_internal_target": config.CET1_INTERNAL_TARGET,
        },
    }


def write_dashboard(r, out_path):
    """r: a completed pipeline.RunResults. Writes a single self-contained HTML file to out_path."""
    data = _build_data(r)
    data_json = json.dumps(data).replace("</", "<\\/")
    html = _HTML_TEMPLATE.replace(_TEMPLATE_PATH_TOKEN, data_json)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NIM Forecasting Model - Dashboard</title>
<style>
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 0 24px 48px;
         background: #0e1116; color: #e6e6e6; }
  h1 { font-size: 1.4rem; margin: 24px 0 4px; }
  h2 { font-size: 1.1rem; margin: 0 0 8px; color: #9fd3ff; }
  .subtitle { color: #9aa4b2; margin-bottom: 16px; }
  .section { background: #161b22; border: 1px solid #2a3138; border-radius: 8px; padding: 16px; margin: 16px 0; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }
  .toggles { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
  .toggles label { background: #1f2630; border: 1px solid #333c48; border-radius: 14px; padding: 4px 10px;
                    font-size: 0.85rem; cursor: pointer; user-select: none; }
  svg { width: 100%; height: auto; background: #10151c; border-radius: 6px; }
  .axis text { fill: #9aa4b2; font-size: 10px; }
  .gridline { stroke: #2a3138; stroke-width: 1; }
  .threshold-label { font-size: 9px; fill: #cbd5e1; }
  table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
  th, td { border-bottom: 1px solid #2a3138; padding: 5px 8px; text-align: right; }
  th:first-child, td:first-child { text-align: left; }
  th { cursor: pointer; color: #9fd3ff; white-space: nowrap; }
  th:hover { color: #fff; }
  .legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 0.8rem; margin-top: 6px; }
  .legend span.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
  .empty-note { color: #6b7684; font-style: italic; }
</style>
</head>
<body>
<h1>NIM Forecasting Model - Interactive Dashboard</h1>
<div class="subtitle">Base scenario: <span id="base-scenario-label"></span>. Toggle scenarios below to filter the
  NIM / LCR / NSFR charts; every other section is base-scenario or fixed-scenario-set by construction (see page
  source comment in reporting/dashboard.py for why).</div>

<div class="section">
  <h2>Scenario filter</h2>
  <div class="toggles" id="scenario-toggles"></div>
</div>

<div class="grid">
  <div class="section"><h2>NIM by scenario (annualized, %)</h2><div id="chart-nim"></div></div>
  <div class="section"><h2>LCR by scenario (%)</h2><div id="chart-lcr"></div></div>
  <div class="section"><h2>LCR, stressed outflow assumptions (%)</h2><div id="chart-lcr-stressed"></div></div>
  <div class="section"><h2>NSFR by scenario (%)</h2><div id="chart-nsfr"></div></div>
  <div class="section"><h2>CET1 ratio (base scenario, %)</h2><div id="chart-cet1"></div></div>
  <div class="section"><h2>AFS MTM buffer (base scenario, $)</h2><div id="chart-mtm"></div></div>
  <div class="section"><h2>FTP: customer margin vs. ALM desk P&amp;L (base scenario, $/mo)</h2><div id="chart-ftp"></div></div>
  <div class="section"><h2>EVE sensitivity, linear vs. full revaluation (% of equity)</h2><div id="chart-eve"></div></div>
</div>

<div class="section">
  <h2>Joint LCR-NIM view (base scenario, month 0) - click a column to sort</h2>
  <table id="table-joint"><thead></thead><tbody></tbody></table>
</div>

<div class="section">
  <h2>Back-test attribution</h2>
  <div id="backtest-label" class="subtitle"></div>
  <table id="table-backtest"><thead></thead><tbody></tbody></table>
</div>

<script>
const DATA = __DASHBOARD_DATA_JSON__;
const PALETTE = ["#4fc3f7", "#ff8a65", "#aed581", "#ba68c8", "#ffd54f", "#4db6ac", "#f06292", "#90a4ae"];
let visibleScenarios = new Set(DATA.scenarios);

function fmtPct(v, d) { return v === null || v === undefined ? "" : (v * 100).toFixed(d === undefined ? 2 : d) + "%"; }
function fmtNum(v) { return v === null || v === undefined ? "" : Number(v).toLocaleString(undefined, {maximumFractionDigits: 0}); }

function svgLineChart(containerId, seriesMap, opts) {
  opts = opts || {};
  const width = 560, height = 260, padL = 52, padR = 16, padT = 14, padB = 26;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const labels = Object.keys(seriesMap);
  let xs = [], ys = [];
  labels.forEach(l => seriesMap[l].forEach(p => { xs.push(p[0]); ys.push(p[1]); }));
  (opts.thresholds || []).forEach(t => ys.push(t.value));
  if (xs.length === 0) {
    document.getElementById(containerId).innerHTML = '<div class="empty-note">No data.</div>';
    return;
  }
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const pad = (yMax - yMin) * 0.08;
  yMin -= pad; yMax += pad;
  const xScale = x => padL + (xMax === xMin ? 0 : (x - xMin) / (xMax - xMin)) * plotW;
  const yScale = y => padT + plotH - ((y - yMin) / (yMax - yMin)) * plotH;

  let svg = `<svg viewBox="0 0 ${width} ${height}">`;
  for (let i = 0; i <= 4; i++) {
    const gy = padT + (plotH / 4) * i;
    const val = yMax - (yMax - yMin) * (i / 4);
    svg += `<line class="gridline" x1="${padL}" y1="${gy}" x2="${width - padR}" y2="${gy}"/>`;
    svg += `<text class="axis" x="${padL - 6}" y="${gy + 3}" text-anchor="end">${opts.yFormat ? opts.yFormat(val) : val.toFixed(2)}</text>`;
  }
  const xTickCount = Math.min(6, xMax - xMin + 1);
  for (let i = 0; i <= xTickCount; i++) {
    const xv = Math.round(xMin + (xMax - xMin) * (i / xTickCount));
    svg += `<text class="axis" x="${xScale(xv)}" y="${height - 6}" text-anchor="middle">${xv}</text>`;
  }
  (opts.thresholds || []).forEach(t => {
    const gy = yScale(t.value);
    svg += `<line x1="${padL}" y1="${gy}" x2="${width - padR}" y2="${gy}" stroke="${t.color}" stroke-dasharray="4,3" stroke-width="1"/>`;
    svg += `<text class="threshold-label" x="${width - padR}" y="${gy - 2}" text-anchor="end">${t.label}</text>`;
  });
  labels.forEach((label, i) => {
    const color = PALETTE[i % PALETTE.length];
    const pts = seriesMap[label];
    const d = pts.map((p, idx) => `${idx === 0 ? "M" : "L"}${xScale(p[0]).toFixed(1)},${yScale(p[1]).toFixed(1)}`).join(" ");
    svg += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>`;
  });
  svg += "</svg>";
  let legend = '<div class="legend">';
  labels.forEach((label, i) => {
    legend += `<span><span class="swatch" style="background:${PALETTE[i % PALETTE.length]}"></span>${label}</span>`;
  });
  legend += "</div>";
  document.getElementById(containerId).innerHTML = svg + legend;
}

function seriesFor(byScenario, valueKey) {
  const out = {};
  DATA.scenarios.filter(s => visibleScenarios.has(s)).forEach(s => {
    out[s] = (byScenario[s] || []).map(row => [row.month, row[valueKey]]);
  });
  return out;
}

function renderScenarioCharts() {
  svgLineChart("chart-nim", seriesFor(DATA.nim_by_scenario, "nim"), {yFormat: v => fmtPct(v, 1)});
  svgLineChart("chart-lcr", seriesFor(DATA.lcr_by_scenario, "lcr"), {
    yFormat: v => fmtPct(v, 0),
    thresholds: [
      {value: DATA.thresholds.lcr_regulatory_min, label: "Reg min", color: "#ef5350"},
      {value: DATA.thresholds.lcr_ras_threshold, label: "RAS", color: "#ffa726"},
      {value: DATA.thresholds.lcr_internal_target, label: "Target", color: "#66bb6a"},
    ],
  });
  svgLineChart("chart-lcr-stressed", seriesFor(DATA.lcr_by_scenario, "lcr_stressed"), {
    yFormat: v => fmtPct(v, 0),
    thresholds: [
      {value: DATA.thresholds.lcr_regulatory_min, label: "Reg min", color: "#ef5350"},
      {value: DATA.thresholds.lcr_ras_threshold, label: "RAS", color: "#ffa726"},
      {value: DATA.thresholds.lcr_internal_target, label: "Target", color: "#66bb6a"},
    ],
  });
  svgLineChart("chart-nsfr", seriesFor(DATA.nsfr_by_scenario, "nsfr"), {
    yFormat: v => fmtPct(v, 0),
    thresholds: [{value: DATA.thresholds.nsfr_regulatory_min, label: "Reg min", color: "#ef5350"}],
  });
}

function renderStaticCharts() {
  svgLineChart("chart-cet1", {"CET1 ratio": DATA.cet1.map(r => [r.month, r.cet1_ratio])}, {
    yFormat: v => fmtPct(v, 1),
    thresholds: [
      {value: DATA.thresholds.cet1_regulatory_min, label: "Reg min", color: "#ef5350"},
      {value: DATA.thresholds.cet1_buffered_min, label: "Buffered min", color: "#ffa726"},
      {value: DATA.thresholds.cet1_internal_target, label: "Target", color: "#66bb6a"},
    ],
  });
  svgLineChart("chart-mtm", {
    "Unrealized gain": DATA.mtm.map(r => [r.month, r.total_unrealized_gain]),
    "Buffer limit": DATA.mtm.map(r => [r.month, r.buffer_limit]),
  }, {yFormat: v => fmtNum(v)});
  svgLineChart("chart-ftp", {
    "Customer margin": DATA.ftp.map(r => [r.month, r.total_customer_margin]),
    "ALM desk P&L": DATA.ftp.map(r => [r.month, r.alm_desk_pnl]),
    "Total NII": DATA.ftp.map(r => [r.month, r.total_nii]),
  }, {yFormat: v => fmtNum(v)});

  const eveScenarios = DATA.eve_linear.map(r => r.scenario);
  const width = 560, height = 260, padL = 52, padR = 16, padT = 14, padB = 70;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const linearVals = DATA.eve_linear.map(r => r.delta_eve_pct_equity);
  const fullVals = eveScenarios.map(s => {
    const row = DATA.eve_full_reval.find(r => r.scenario === s);
    return row ? row.delta_eve_pct_equity_full_reval : null;
  }).filter(v => v !== null);
  const allVals = linearVals.concat(fullVals).filter(v => v !== null && v !== undefined);
  let yMin = Math.min(0, ...allVals), yMax = Math.max(0, ...allVals);
  if (yMin === yMax) { yMin -= 0.01; yMax += 0.01; }
  const pad = (yMax - yMin) * 0.1;
  yMin -= pad; yMax += pad;
  const yScale = y => padT + plotH - ((y - yMin) / (yMax - yMin)) * plotH;
  const n = eveScenarios.length;
  const groupW = plotW / Math.max(n, 1);
  let svg = `<svg viewBox="0 0 ${width} ${height}">`;
  const zeroY = yScale(0);
  svg += `<line class="gridline" x1="${padL}" y1="${zeroY}" x2="${width - padR}" y2="${zeroY}"/>`;
  eveScenarios.forEach((s, i) => {
    const cx = padL + groupW * (i + 0.5);
    const lv = DATA.eve_linear[i] ? DATA.eve_linear[i].delta_eve_pct_equity : null;
    const fullRow = DATA.eve_full_reval.find(r => r.scenario === s);
    const fv = fullRow ? fullRow.delta_eve_pct_equity_full_reval : null;
    const barW = groupW * 0.28;
    if (lv !== null && lv !== undefined) {
      const y = yScale(lv), h = Math.abs(zeroY - y);
      svg += `<rect x="${cx - barW - 2}" y="${Math.min(y, zeroY)}" width="${barW}" height="${h}" fill="${PALETTE[0]}"/>`;
    }
    if (fv !== null && fv !== undefined) {
      const y = yScale(fv), h = Math.abs(zeroY - y);
      svg += `<rect x="${cx + 2}" y="${Math.min(y, zeroY)}" width="${barW}" height="${h}" fill="${PALETTE[1]}"/>`;
    }
    svg += `<text class="axis" x="${cx}" y="${height - padB + 14}" text-anchor="middle" transform="rotate(20 ${cx} ${height - padB + 14})">${s}</text>`;
  });
  svg += "</svg>";
  svg += `<div class="legend"><span><span class="swatch" style="background:${PALETTE[0]}"></span>Linear duration approx</span>` +
         `<span><span class="swatch" style="background:${PALETTE[1]}"></span>Full revaluation</span></div>`;
  document.getElementById("chart-eve").innerHTML = svg;
}

function makeSortableTable(tableId, rows, columns) {
  const table = document.getElementById(tableId);
  if (!rows || rows.length === 0) {
    table.innerHTML = '<caption class="empty-note">No data.</caption>';
    return;
  }
  let sortKey = columns[0].key, sortAsc = true;
  function render() {
    const sorted = rows.slice().sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av === bv) return 0;
      const cmp = (av > bv) ? 1 : -1;
      return sortAsc ? cmp : -cmp;
    });
    const thead = columns.map(c => `<th data-key="${c.key}">${c.label}</th>`).join("");
    const tbody = sorted.map(row => "<tr>" + columns.map(c => `<td>${c.fmt ? c.fmt(row[c.key]) : (row[c.key] === null ? "" : row[c.key])}</td>`).join("") + "</tr>").join("");
    table.querySelector("thead").innerHTML = `<tr>${thead}</tr>`;
    table.querySelector("tbody").innerHTML = tbody;
    table.querySelectorAll("th").forEach(th => {
      th.onclick = () => {
        const key = th.getAttribute("data-key");
        if (key === sortKey) { sortAsc = !sortAsc; } else { sortKey = key; sortAsc = true; }
        render();
      };
    });
  }
  render();
}

function renderTables() {
  makeSortableTable("table-joint", DATA.joint_view, [
    {key: "position", label: "Position"}, {key: "side", label: "Side"},
    {key: "nim_contribution", label: "NIM contribution", fmt: v => fmtPct(v, 3)},
    {key: "ftp_customer_margin", label: "FTP customer margin", fmt: fmtNum},
    {key: "lcr_role", label: "LCR role"}, {key: "lcr_impact", label: "LCR impact", fmt: fmtNum},
    {key: "nim_per_unit_lcr_cost", label: "NIM / unit LCR cost", fmt: v => (v === null ? "" : v.toFixed(4))},
  ]);

  if (DATA.backtest) {
    document.getElementById("backtest-label").textContent = DATA.backtest.label;
    makeSortableTable("table-backtest", DATA.backtest.rows, [
      {key: "month", label: "Month"},
      {key: "net_interest_income", label: "Forecast NII", fmt: fmtNum},
      {key: "actual_nii", label: "Actual NII", fmt: fmtNum},
      {key: "nii_error", label: "NII error", fmt: fmtNum},
      {key: "cumulative_nii_error", label: "Cumulative error", fmt: fmtNum},
      {key: "rate_variance", label: "Rate variance", fmt: fmtNum},
      {key: "volume_variance", label: "Volume variance", fmt: fmtNum},
      {key: "residual_unmodellable", label: "Residual", fmt: fmtNum},
    ]);
  } else {
    document.getElementById("backtest-label").innerHTML = '<span class="empty-note">No back-test was run this session (--backtest or --backtest-fdic).</span>';
  }
}

function renderScenarioToggles() {
  const el = document.getElementById("scenario-toggles");
  el.innerHTML = DATA.scenarios.map(s =>
    `<label><input type="checkbox" data-scenario="${s}" ${visibleScenarios.has(s) ? "checked" : ""}/> ${s}</label>`
  ).join("");
  el.querySelectorAll("input").forEach(cb => {
    cb.onchange = () => {
      const s = cb.getAttribute("data-scenario");
      if (cb.checked) { visibleScenarios.add(s); } else { visibleScenarios.delete(s); }
      renderScenarioCharts();
    };
  });
}

document.getElementById("base-scenario-label").textContent = DATA.base_scenario;
renderScenarioToggles();
renderScenarioCharts();
renderStaticCharts();
renderTables();
</script>
</body>
</html>
"""
