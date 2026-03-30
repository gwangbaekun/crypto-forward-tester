/**
 * Shared Forward Test dashboard renderer (Renaissance-style).
 *
 * Usage:
 *   ForwardTestDashboard.init({
 *     basePath: "/quant/renaissance",
 *     symbolFn: () => "BTCUSDT",
 *     statsElId: "forward-stats",
 *     tradesListElId: "forward-trades-list",
 *     tradesPagerElId: "forward-trades-pagination",
 *     onRendered: (statsJson) => {},
 *     pollMs: 30000,
 *   });
 */
(function () {
  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtPrice(v) {
    if (v == null) return "—";
    return "$" + Math.round(Number(v)).toLocaleString();
  }

  function formatTradeDate(iso) {
    if (!iso) return "—";
    try {
      var formatter = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Seoul",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      var parts = formatter.formatToParts(new Date(iso));
      var get = function (name) {
        var p = parts.find(function (x) { return x.type === name; });
        return p ? p.value : "";
      };
      return get("month") + "-" + get("day") + " " + get("hour") + ":" + get("minute");
    } catch (e) {
      return iso;
    }
  }

  function confDots(n) {
    n = Math.min(Math.max(parseInt(n) || 0, 0), 5);
    var dotColor = n >= 4 ? "#238636" : n >= 2 ? "#f0883e" : "#8b949e";
    var html = "";
    for (var i = 0; i < 5; i++) {
      var c = i < n ? dotColor : "rgba(255,255,255,0.1)";
      html += "<span style='display:inline-block;width:7px;height:7px;border-radius:50%;background:" + c + ";margin:0 1px;'></span>";
    }
    return html;
  }

  function pnlColor(v) {
    v = Number(v || 0);
    if (v > 0) return "var(--accent-green)";
    if (v < 0) return "var(--accent-red)";
    return "var(--text-secondary)";
  }

  function init(opts) {
    opts = opts || {};
    var basePath    = opts.basePath || "";
    var symbolFn    = opts.symbolFn || function () { return "BTCUSDT"; };
    var strategyTag = opts.strategyTag || null;   // e.g. "atr_breakout_v2"
    var statsEl = document.getElementById(opts.statsElId || "forward-stats");
    var tradesListEl = document.getElementById(opts.tradesListElId || "forward-trades-list");
    var pagerEl = document.getElementById(opts.tradesPagerElId || "forward-trades-pagination");

    if (!basePath || !statsEl || !tradesListEl) return;

    var pollMs = opts.pollMs != null ? opts.pollMs : 30 * 1000;
    var tradesLimit = opts.tradesLimit != null ? opts.tradesLimit : 200;

    var leverage = 3;
    var trades = [];
    var page = 1;
    var perPage = 10;
    var timer = null;

    function renderTradesPage() {
      if (!tradesListEl) return;
      var total = trades.length;
      if (total === 0) {
        tradesListEl.innerHTML = "<span style='color:var(--text-secondary);'>No trades yet</span>";
        if (pagerEl) pagerEl.innerHTML = "";
        return;
      }

      var totalPages = Math.max(1, Math.ceil(total / perPage));
      page = Math.max(1, Math.min(page, totalPages));
      var start = (page - 1) * perPage;
      var slice = trades.slice(start, start + perPage);

      var rows = slice.map(function (t) {
        var pnl = t.pnl_pct != null ? Number(t.pnl_pct) : 0;
        var pnlClass = pnl >= 0 ? "pnl-pos" : "pnl-neg";
        var pnlStr = (pnl >= 0 ? "+" : "") + (isFinite(pnl) ? pnl.toFixed(2) : "—") + "%";
        return "<tr>" +
          "<td>" + formatTradeDate(t.opened_at) + "</td>" +
          "<td>" + formatTradeDate(t.closed_at) + "</td>" +
          "<td><strong style='color:" + (t.side === "long" ? "var(--accent-green)" : "var(--accent-red)") + ";'>" + esc((t.side || "").toUpperCase()) + "</strong></td>" +
          "<td>" + fmtPrice(t.entry_price) + "</td>" +
          "<td>" + fmtPrice(t.exit_price) + "</td>" +
          "<td class='" + pnlClass + "'>" + pnlStr + "</td>" +
          "<td>" + (t.duration_min != null ? esc(String(t.duration_min)) + "m" : "—") + "</td>" +
          "</tr>";
      }).join("");

      tradesListEl.innerHTML =
        "<table class='trades-table'><thead><tr>" +
        "<th>Open</th><th>Close</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL %</th><th>Dur</th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table>";

      if (!pagerEl) return;
      var prevDisabled = page <= 1;
      var nextDisabled = page >= totalPages;
      pagerEl.innerHTML = [
        "<button type='button' class='trades-pager' " + (prevDisabled ? "disabled" : "") + " data-page='" + (page - 1) + "'>Prev</button>",
        "<span style='color:var(--text-secondary);'>" + page + " / " + totalPages + "</span>",
        "<button type='button' class='trades-pager' " + (nextDisabled ? "disabled" : "") + " data-page='" + (page + 1) + "'>Next</button>",
      ].join("");

      pagerEl.querySelectorAll("button[data-page]").forEach(function (b) {
        b.addEventListener("click", function () {
          var p = parseInt(b.getAttribute("data-page"), 10);
          if (!p || p < 1) return;
          page = p;
          renderTradesPage();
        });
      });
    }

    function renderStats(d) {
      var pos = d.current_position;
      var pnl = d.total_pnl_pct != null ? Number(d.total_pnl_pct) : 0;
      var win = d.win_count || 0;
      var loss = d.loss_count || 0;
      var total = d.closed_trades_count || 0;
      var halted = d.edge_halted;
      var ev = d.edge_validation || {};
      var recent = d.recent_trades || [];

      var effPnl = pnl * leverage;
      var html = "";

      if (halted) {
        html += "<div style='padding:8px;background:rgba(218,54,51,0.15);border:1px solid rgba(218,54,51,0.4);border-radius:6px;margin-bottom:10px;font-size:0.85rem;color:var(--accent-red);'>⛔ Trading halted (edge validation failed)</div>";
      }

      html += "<div style='display:flex;gap:16px;align-items:center;margin-bottom:10px;flex-wrap:wrap;'>" +
        "<div>" +
          "<div style='font-size:0.75rem;color:var(--text-secondary);display:flex;align-items:center;gap:6px;'>Cumulative PnL" +
            "<select data-forward-leverage style='background:var(--card-bg);border:1px solid var(--border-color);color:var(--text-secondary);font-size:0.7rem;border-radius:4px;padding:1px 4px;'>" +
              "<option value='1'" + (leverage === 1 ? " selected" : "") + ">1x</option>" +
              "<option value='2'" + (leverage === 2 ? " selected" : "") + ">2x</option>" +
              "<option value='3'" + (leverage === 3 ? " selected" : "") + ">3x</option>" +
            "</select>" +
          "</div>" +
          "<div style='font-size:1.2rem;font-weight:bold;color:" + pnlColor(effPnl) + ";'>" + (effPnl >= 0 ? "+" : "") + effPnl.toFixed(4) + "%</div>" +
          "<div style='font-size:0.7rem;color:var(--text-secondary);'>1x basis: " + (pnl >= 0 ? "+" : "") + pnl + "%</div>" +
        "</div>" +
        "<div><div style='font-size:0.75rem;color:var(--text-secondary);'>W / L / Total</div>" +
          "<div style='font-size:0.95rem;font-weight:bold;'><span style='color:var(--accent-green);'>" + win + "</span> / <span style='color:var(--accent-red);'>" + loss + "</span> / " + total + "</div></div>" +
        (ev.profit_factor != null ? "<div><div style='font-size:0.75rem;color:var(--text-secondary);'>Profit Factor</div><div style='font-size:0.95rem;font-weight:bold;color:" + (Number(ev.profit_factor) >= 1.2 ? "var(--accent-green)" : "var(--accent-red)") + ";'>" + esc(ev.profit_factor) + "</div></div>" : "") +
        (ev.rolling_win_rate != null ? "<div><div style='font-size:0.75rem;color:var(--text-secondary);'>Rolling win %</div><div style='font-size:0.95rem;font-weight:bold;'>" + (Number(ev.rolling_win_rate) * 100).toFixed(1) + "%</div></div>" : "") +
      "</div>";

      if (pos) {
        var pc = pos.side === "long" ? "var(--accent-green)" : "var(--accent-red)";
        var curPrice = pos.current_price != null ? pos.current_price : null;
        var unrealizedPct = pos.unrealized_pnl_pct;
        if (unrealizedPct == null && curPrice != null && curPrice > 0 && pos.entry_price != null && pos.entry_price > 0) {
          unrealizedPct = pos.side === "long"
            ? (curPrice - pos.entry_price) / pos.entry_price * 100
            : (pos.entry_price - curPrice) / pos.entry_price * 100;
        }
        var pnlStr = "";
        if (unrealizedPct != null && isFinite(unrealizedPct)) {
          pnlStr = " &nbsp;<strong style='color:" + pnlColor(unrealizedPct) + ";'>" + (unrealizedPct >= 0 ? "+" : "") + Number(unrealizedPct).toFixed(2) + "%</strong>";
          if (curPrice != null) pnlStr = " · mark " + fmtPrice(curPrice) + pnlStr;
        }

        var tpsl = pos.tpsl || {};
        var slVal = tpsl.sl != null ? Number(tpsl.sl) : null;
        var tp1Val = tpsl.tp1 != null ? Number(tpsl.tp1) : null;
        var entryVal = pos.entry_price != null ? Number(pos.entry_price) : null;
        var lockedPct = null;
        if (slVal != null && entryVal != null) {
          if (pos.side === "long" && slVal > entryVal) lockedPct = (slVal - entryVal) / entryVal * 100;
          if (pos.side === "short" && slVal < entryVal) lockedPct = (entryVal - slVal) / entryVal * 100;
        }
        var slHtml = slVal != null
          ? "<span style='color:#e74c3c;'>SL " + fmtPrice(slVal) + "</span>" +
            (lockedPct != null ? " <span style='color:var(--accent-green);font-size:0.76em;'>🔒 +" + lockedPct.toFixed(2) + "% locked</span>" : "")
          : "";
        var tpHtml = tp1Val != null ? "<span style='color:var(--accent-green);'>Target " + fmtPrice(tp1Val) + "</span>" : "";
        var tpslLine = (slHtml || tpHtml)
          ? "<div style='margin-top:5px;font-size:0.78rem;display:flex;gap:6px;align-items:center;flex-wrap:wrap;'>" +
              slHtml +
              (slHtml && tpHtml ? "<span style='color:var(--text-secondary);opacity:0.5;'>→</span>" : "") +
              tpHtml +
            "</div>"
          : "";

        html += "<div style='background:rgba(0,0,0,0.2);padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:0.85rem;border:1px solid var(--border-color);border-left:3px solid " + pc + ";'>" +
          "Open position: <strong style='color:" + pc + ";'>" + esc((pos.side || "").toUpperCase()) + "</strong> @ " + fmtPrice(pos.entry_price) +
          (pos.trigger_tfs ? " <span style='color:var(--text-secondary);font-size:0.8em;'>(" + esc(pos.trigger_tfs) + " " + confDots(pos.confidence) + ")</span>" : "") +
          pnlStr +
          tpslLine +
          "</div>";
      } else {
        html += "<div style='font-size:0.8rem;color:var(--text-secondary);margin-bottom:10px;'>No open position — enters when conditions match</div>";
      }

      if (recent.length) {
        html += "<div style='font-size:0.75rem;border-top:1px solid var(--border-color);padding-top:8px;'>Recent trades: " +
          recent.slice(0, 6).map(function (t) {
            var c = (Number(t.pnl_pct || 0)) >= 0 ? "var(--accent-green)" : "var(--accent-red)";
            return "<span style='color:" + c + ";'>" + esc(t.side || "") + " " + ((Number(t.pnl_pct || 0)) >= 0 ? "+" : "") + esc(t.pnl_pct || 0) + "%</span>";
          }).join(" &nbsp;|&nbsp; ") +
          "</div>";
      }

      if ((ev.warnings || []).length) {
        html += "<div style='margin-top:8px;'>" +
          ev.warnings.map(function (w) {
            return "<div style='font-size:0.75rem;color:var(--accent-orange);padding:4px 0;'>⚠️ " + esc(w) + "</div>";
          }).join("") + "</div>";
      }

      statsEl.innerHTML = html;

      var sel = statsEl.querySelector("select[data-forward-leverage]");
      if (sel) {
        sel.addEventListener("change", function () {
          var x = parseFloat(sel.value);
          if (!x || x <= 0) x = 1;
          leverage = x;
          refreshStats();
        });
      }
    }

    function refreshStats() {
      var sym = symbolFn();
      var statsUrl = basePath + "/forward_test/stats?symbol=" + encodeURIComponent(sym);
      if (strategyTag) statsUrl += "&strategy_tag=" + encodeURIComponent(strategyTag);
      return fetch(statsUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.error) {
            statsEl.innerHTML = "<span style='color:var(--text-secondary);font-size:0.85rem;'>Could not load forward test stats.</span>";
            return;
          }
          renderStats(d || {});
          if (typeof opts.onRendered === "function") opts.onRendered(d || {});
        })
        .catch(function () {
          statsEl.innerHTML = "<span style='color:var(--text-secondary);font-size:0.85rem;'>Could not load forward test stats.</span>";
        });
    }

    function refreshTrades() {
      var sym = symbolFn();
      tradesListEl.innerHTML = "<span style='color:var(--text-secondary);'>Loading…</span>";
      var tradesUrl = basePath + "/forward_test/trades?symbol=" + encodeURIComponent(sym) + "&limit=" + tradesLimit;
      if (strategyTag) tradesUrl += "&strategy_tag=" + encodeURIComponent(strategyTag);
      return fetch(tradesUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          trades = Array.isArray(d) ? d : (d && d.trades ? d.trades : []);
          page = 1;
          renderTradesPage();
        })
        .catch(function () {
          tradesListEl.innerHTML = "<span style='color:var(--text-secondary);'>Could not load trade list.</span>";
          if (pagerEl) pagerEl.innerHTML = "";
        });
    }

    function refresh() {
      refreshStats();
      refreshTrades();
    }

    refresh();
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, pollMs);
  }

  window.ForwardTestDashboard = { init: init };
})();

