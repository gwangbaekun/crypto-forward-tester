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
        second: "2-digit",
        hour12: false,
      });
      var parts = formatter.formatToParts(new Date(iso));
      var get = function (name) {
        var p = parts.find(function (x) { return x.type === name; });
        return p ? p.value : "";
      };
      return get("month") + "-" + get("day") + " " + get("hour") + ":" + get("minute") + ":" + get("second");
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

    var trades = [];
    var page = 1;
    var perPage = 10;
    var timer = null;
    var _selectedGlobalIdx = -1;
    var hasRenderedStats = false;
    var hasRenderedTrades = false;

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

      var hasOnClick = typeof opts.onTradeClick === "function";

      var rows = slice.map(function (t, i) {
        var globalIdx = start + i;
        var pnl = t.pnl_pct != null ? Number(t.pnl_pct) : 0;
        var net = t.net_pnl_pct != null ? Number(t.net_pnl_pct) : null;
        var pnlClass = pnl >= 0 ? "pnl-pos" : "pnl-neg";
        var netClass = net != null ? (net >= 0 ? "pnl-pos" : "pnl-neg") : "";
        var pnlStr = (pnl >= 0 ? "+" : "") + (isFinite(pnl) ? pnl.toFixed(2) : "—") + "%";
        var netStr = net != null
          ? "<span style='font-size:0.68rem;opacity:0.7;'> (" + (net >= 0 ? "+" : "") + net.toFixed(2) + "%)</span>"
          : "";
        var isActive = globalIdx === _selectedGlobalIdx;
        var rowStyle = isActive
          ? "cursor:pointer;background:rgba(139,92,246,0.12);border-left:2px solid #7b2d8b;"
          : (hasOnClick ? "cursor:pointer;" : "");
        var clickAttr = hasOnClick
          ? " onclick=\"ForwardTestDashboard._handleTradeClick(" + globalIdx + ")\"" : "";
        return "<tr data-gidx='" + globalIdx + "' style='" + rowStyle + "'" + clickAttr + ">" +
          "<td>" + formatTradeDate(t.opened_at) + "</td>" +
          "<td>" + formatTradeDate(t.closed_at) + "</td>" +
          "<td><strong style='color:" + (t.side === "long" ? "var(--accent-green)" : "var(--accent-red)") + ";'>" + esc((t.side || "").toUpperCase()) + "</strong></td>" +
          "<td>" + fmtPrice(t.entry_price) + "</td>" +
          "<td>" + fmtPrice(t.exit_price) + "</td>" +
          "<td class='" + pnlClass + "'>" + pnlStr + "<span class='" + netClass + "'>" + netStr + "</span></td>" +
          "<td>" + (t.duration_min != null ? esc(String(t.duration_min)) + "m" : "—") + "</td>" +
          "</tr>";
      }).join("");

      tradesListEl.innerHTML =
        "<table class='trades-table'><thead><tr>" +
        "<th>Open</th><th>Close</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL (net)</th><th>Dur</th>" +
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
      window._fwdDashLastPos = d.current_position || null;
      var pos = window._fwdDashLastPos;
      var _btnEl = document.getElementById("btn-open-pos-detail");
      if (_btnEl) _btnEl.style.display = pos ? "" : "none";
      var pnl = d.total_pnl_pct != null ? Number(d.total_pnl_pct) : 0;
      var win = d.win_count || 0;
      var loss = d.loss_count || 0;
      var total = d.closed_trades_count || 0;
      var halted = d.edge_halted;
      var ev = d.edge_validation || {};
      var recent = d.recent_trades || [];

      var html = "";

      if (halted) {
        html += "<div style='padding:8px;background:rgba(218,54,51,0.15);border:1px solid rgba(218,54,51,0.4);border-radius:6px;margin-bottom:10px;font-size:0.85rem;color:var(--accent-red);'>⛔ Trading halted (edge validation failed)</div>";
      }

      html += "<div style='display:flex;gap:16px;align-items:center;margin-bottom:10px;flex-wrap:wrap;'>" +
        "<div>" +
          "<div style='font-size:0.75rem;color:var(--text-secondary);'>Cumulative PnL</div>" +
          "<div style='font-size:1.2rem;font-weight:bold;color:" + pnlColor(pnl) + ";'>" + (pnl >= 0 ? "+" : "") + pnl.toFixed(4) + "%</div>" +
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

        // pos.tpsl 래퍼 없이 직접 필드 접근
        var slVal = pos.sl != null ? Number(pos.sl) : null;
        var tpLevels = pos.tp_levels || (pos.tp != null ? [pos.tp] : []);
        var slLevels = pos.sl_levels || (slVal != null ? [slVal] : []);
        var tpAdvances = pos.tp_advances || 0;
        var entryVal = pos.entry_price != null ? Number(pos.entry_price) : null;

        var lockedPct = null;
        if (slVal != null && entryVal != null) {
          if (pos.side === "long" && slVal > entryVal) lockedPct = (slVal - entryVal) / entryVal * 100;
          if (pos.side === "short" && slVal < entryVal) lockedPct = (entryVal - slVal) / entryVal * 100;
        }

        var tpPal = ["#26a69a", "#4db6ac", "#66bb6a", "#81c784", "#a5d6a7"];
        var slPal = ["#e74c3c", "#e57373", "#ff8a80"];
        var tpRows = tpLevels.map(function(p, i) {
          var hit = i < tpAdvances;
          return "<div style='display:flex;justify-content:space-between;padding:1px 0;'>" +
            "<span style='color:#8b949e;'>TP" + (i + 1) + (hit ? " ✓" : "") + "</span>" +
            "<span style='color:" + tpPal[i % tpPal.length] + ";" + (hit ? "text-decoration:line-through;opacity:0.6;" : "") + "'>" + fmtPrice(p) + "</span></div>";
        }).join("");
        var slRows = slLevels.map(function(p, i) {
          return "<div style='display:flex;justify-content:space-between;padding:1px 0;'>" +
            "<span style='color:#8b949e;'>" + (slLevels.length > 1 ? "SL" + (i + 1) : "SL") + "</span>" +
            "<span style='color:" + slPal[i % slPal.length] + ";'>" + fmtPrice(p) + "</span></div>";
        }).join("");
        var ratchetHtml = tpAdvances > 0
          ? "<div style='font-size:0.75rem;color:var(--accent-green);margin-top:3px;'>🔄 TP advanced ×" + tpAdvances +
            (lockedPct != null ? " · 🔒 +" + lockedPct.toFixed(2) + "% locked" : "") + "</div>"
          : (lockedPct != null ? "<div style='font-size:0.75rem;color:var(--accent-green);margin-top:3px;'>🔒 +" + lockedPct.toFixed(2) + "% locked</div>" : "");

        var openPosClickAttr = typeof opts.onOpenPosClick === "function"
          ? " onclick=\"window._fwdDashOpenPosClick&&window._fwdDashOpenPosClick(window._fwdDashLastPos)\" style='cursor:pointer;background:rgba(0,0,0,0.2);padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:0.85rem;border:1px solid var(--border-color);border-left:3px solid " + pc + ";'"
          : " style='background:rgba(0,0,0,0.2);padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:0.85rem;border:1px solid var(--border-color);border-left:3px solid " + pc + ";'";

        html += "<div" + openPosClickAttr + ">" +
          "Open position: <strong style='color:" + pc + ";'>" + esc((pos.side || "").toUpperCase()) + "</strong> @ " + fmtPrice(pos.entry_price) +
          (pos.trigger_tfs ? " <span style='color:var(--text-secondary);font-size:0.8em;'>(" + esc(pos.trigger_tfs) + " " + confDots(pos.confidence) + ")</span>" : "") +
          pnlStr +
          (tpRows || slRows ? "<div style='margin-top:6px;border-top:1px solid #30363d;padding-top:6px;font-size:0.78rem;'>" + tpRows + slRows + "</div>" : "") +
          ratchetHtml +
          (typeof opts.onOpenPosClick === "function" ? "<div style='font-size:0.68rem;color:var(--text-secondary);margin-top:4px;'>클릭 → 차트 라인 + 상세</div>" : "") +
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

    }

    function refreshStats() {
      var sym = symbolFn();
      var statsUrl = basePath + "/forward_test/stats?symbol=" + encodeURIComponent(sym);
      if (strategyTag) statsUrl += "&strategy_tag=" + encodeURIComponent(strategyTag);
      if (!hasRenderedStats) {
        statsEl.innerHTML = "<span style='color:var(--text-secondary);font-size:0.85rem;'>Loading forward test stats…</span>";
      }
      return fetch(statsUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.error) {
            if (!hasRenderedStats) {
              statsEl.innerHTML = "<span style='color:var(--text-secondary);font-size:0.85rem;'>Could not load forward test stats.</span>";
            }
            if (typeof opts.onStatsFetch === "function") opts.onStatsFetch(false, d);
            return;
          }
          renderStats(d || {});
          hasRenderedStats = true;
          if (typeof opts.onRendered === "function") opts.onRendered(d || {});
          if (typeof opts.onStatsFetch === "function") opts.onStatsFetch(true, d || {});
        })
        .catch(function () {
          if (!hasRenderedStats) {
            statsEl.innerHTML = "<span style='color:var(--text-secondary);font-size:0.85rem;'>Could not load forward test stats.</span>";
          }
          if (typeof opts.onStatsFetch === "function") opts.onStatsFetch(false, null);
        });
    }

    function refreshTrades() {
      var sym = symbolFn();
      if (!hasRenderedTrades) {
        tradesListEl.innerHTML = "<span style='color:var(--text-secondary);'>Loading…</span>";
      }
      var tradesUrl = basePath + "/forward_test/trades?symbol=" + encodeURIComponent(sym) + "&limit=" + tradesLimit;
      if (strategyTag) tradesUrl += "&strategy_tag=" + encodeURIComponent(strategyTag);
      return fetch(tradesUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          trades = Array.isArray(d) ? d : (d && d.trades ? d.trades : []);
          renderTradesPage();
          hasRenderedTrades = true;
          if (typeof opts.onTradesFetch === "function") opts.onTradesFetch(true, d);
        })
        .catch(function () {
          if (!hasRenderedTrades) {
            tradesListEl.innerHTML = "<span style='color:var(--text-secondary);'>Could not load trade list.</span>";
            if (pagerEl) pagerEl.innerHTML = "";
          }
          if (typeof opts.onTradesFetch === "function") opts.onTradesFetch(false, null);
        });
    }

    function refresh() {
      refreshStats();
      refreshTrades();
    }

    function handleTradeClick(globalIdx) {
      _selectedGlobalIdx = globalIdx;
      renderTradesPage();
      var t = trades[globalIdx];
      if (t && typeof opts.onTradeClick === "function") {
        opts.onTradeClick(t, globalIdx);
      }
    }

    if (typeof opts.onOpenPosClick === "function") {
      window._fwdDashOpenPosClick = opts.onOpenPosClick;
    }

    // 전역 노출 (onclick 문자열에서 호출)
    window.ForwardTestDashboard._handleTradeClick = handleTradeClick;

    refresh();
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, pollMs);
  }

  window.ForwardTestDashboard = { init: init, _handleTradeClick: function() {} };
})();

