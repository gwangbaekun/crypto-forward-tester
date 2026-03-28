(function () {
  "use strict";

  const grid = document.getElementById("price-grid");
  const panelsRoot = document.getElementById("panels-root");
  const panelErrors = document.getElementById("panel-errors");
  const connDot = document.getElementById("conn-dot");
  const connLabel = document.getElementById("conn-label");
  const serverClock = document.getElementById("server-clock");
  const symbolSelect = document.getElementById("symbol-select");

  const pollSec = (function () {
    var raw = document.body && document.body.dataset ? document.body.dataset.pollIntervalSec : "";
    var n = parseFloat(raw);
    return !isNaN(n) && n > 0.2 ? n : 1.0;
  })();

  let pollTimer = null;
  let inFlight = false;

  function apiUrl() {
    const sym = symbolSelect ? symbolSelect.value || "BTCUSDT" : "BTCUSDT";
    return "/api/market-stream?symbol=" + encodeURIComponent(sym);
  }

  function setConn(state, text) {
    connDot.dataset.state = state;
    connLabel.textContent = text;
  }

  function fmtNum(v, digits) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    if (digits != null) return n.toFixed(digits);
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (Math.abs(n) >= 1e3) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }

  function fmtPct(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toFixed(2) + "%";
  }

  function fmtFunding(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return (Number(v) * 100).toFixed(4) + "%";
  }

  function fmtClock(ts) {
    if (ts == null) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour12: false });
  }

  function renderMarks(marks) {
    if (!marks || !marks.symbols) {
      grid.innerHTML = '<p class="muted">Mark 가격 없음</p>';
      return;
    }
    const symbols = marks.symbols;
    const keys = Object.keys(symbols);
    grid.innerHTML = keys
      .map(function (sym) {
        const row = symbols[sym] || {};
        const price = row.price;
        const age = row.age_sec;
        const stale = row.stale;
        const metaClass = stale ? "card__meta card__meta--stale" : "card__meta";
        const ageText = age != null ? age.toFixed(1) + "s 전" : "수신 대기";
        return (
          '<article class="card">' +
          '<span class="card__sym">' +
          sym +
          "</span>" +
          '<span class="card__price">' +
          fmtNum(price, 2) +
          "</span>" +
          '<span class="' +
          metaClass +
          '">' +
          ageText +
          (stale ? " · 캐시 만료" : "") +
          "</span>" +
          "</article>"
        );
      })
      .join("");
  }

  function renderKvRow(items) {
    return (
      '<div class="panel__grid">' +
      items
        .map(function (it) {
          return (
            '<div class="kv"><span class="kv__k">' +
            it.k +
            '</span><span class="kv__v">' +
            it.v +
            "</span></div>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function renderPremium(p) {
    if (!p || p.error) {
      return '<p class="muted">' + (p && p.error ? p.error : "데이터 없음") + "</p>";
    }
    return renderKvRow([
      { k: "Mark", v: fmtNum(p.mark_price, 2) },
      { k: "Index", v: fmtNum(p.index_price, 2) },
      { k: "Funding (8h)", v: fmtFunding(p.last_funding_rate) },
      {
        k: "Next funding (ms)",
        v: p.next_funding_time_ms != null ? String(p.next_funding_time_ms) : "—",
      },
    ]);
  }

  function render24h(t) {
    if (!t || t.error) {
      return '<p class="muted">' + (t && t.error ? t.error : "데이터 없음") + "</p>";
    }
    return renderKvRow([
      { k: "Last", v: fmtNum(t.last_price, 2) },
      { k: "24h Δ%", v: fmtPct(t.price_change_pct) },
      { k: "High / Low", v: fmtNum(t.high, 2) + " / " + fmtNum(t.low, 2) },
      { k: "Volume (base)", v: fmtNum(t.volume_base, 2) },
      { k: "Volume (quote)", v: fmtNum(t.volume_quote, 0) },
    ]);
  }

  function renderOi(rows) {
    if (!rows || !rows.length) {
      return '<p class="muted">OI 없음</p>';
    }
    const head =
      "<table class=\"table\"><thead><tr><th>Exchange</th><th>OI</th></tr></thead><tbody>";
    const body = rows
      .map(function (r) {
        return (
          "<tr><td>" +
          (r.exchange || "—") +
          "</td><td>" +
          fmtNum(r.oi, null) +
          "</td></tr>"
        );
      })
      .join("");
    return head + body + "</tbody></table>";
  }

  function renderLsr(l) {
    if (!l) {
      return '<p class="muted">LSR 없음</p>';
    }
    return renderKvRow([
      { k: "Long / Short ratio", v: fmtNum(l.long_short_ratio, 4) },
      { k: "Long", v: fmtNum(l.long_ratio, 4) },
      { k: "Short", v: fmtNum(l.short_ratio, 4) },
    ]);
  }

  function renderCvd(cvd) {
    if (!cvd) {
      return '<p class="muted">CVD 없음</p>';
    }
    const bar = cvd.last_closed_15m_bar;
    const agg = cvd.recent_agg_trades;
    let html = "";

    if (bar && !bar.error) {
      html +=
        '<p class="panel__title" style="margin-top:0">직전 완료 15m 봉 (taker 불균형)</p>' +
        renderKvRow([
          { k: "Taker Δ (base est.)", v: fmtNum(bar.taker_delta_base, 4) },
          { k: "Taker buy / Vol", v: fmtNum(bar.taker_buy_base, 4) + " / " + fmtNum(bar.volume_base, 4) },
          { k: "봉 시각", v: bar.open_time || "—" },
        ]);
    } else if (bar && bar.error) {
      html += '<p class="muted">' + bar.error + "</p>";
    }

    if (agg && !agg.error) {
      html +=
        '<p class="panel__title">최근 aggTrades 누적 CVD</p>' +
        renderKvRow([
          { k: "CVD (last)", v: fmtNum(agg.cvd_last, 4) },
          { k: "윈도우 Δ", v: fmtNum(agg.cvd_delta_window, 4) },
          { k: "샘플 수", v: String(agg.trades_sampled || "—") },
        ]);
    } else if (agg && agg.error) {
      html += '<p class="muted">' + agg.error + "</p>";
    }

    return html || '<p class="muted">CVD 없음</p>';
  }

  function renderErrors(err) {
    if (!err || typeof err !== "object") {
      panelErrors.hidden = true;
      panelErrors.innerHTML = "";
      return;
    }
    const keys = Object.keys(err);
    if (!keys.length) {
      panelErrors.hidden = true;
      panelErrors.innerHTML = "";
      return;
    }
    panelErrors.hidden = false;
    panelErrors.innerHTML =
      '<p class="panel__title" style="color:var(--warn);margin-top:0">일부 소스 오류</p><ul class="muted" style="margin:0;padding-left:1.2rem">' +
      keys
        .map(function (k) {
          return "<li><strong>" + k + "</strong>: " + err[k] + "</li>";
        })
        .join("") +
      "</ul>";
  }

  function renderPayload(data) {
    if (data.server_ts != null) {
      serverClock.textContent = "server " + fmtClock(data.server_ts);
    }
    renderMarks(data.marks);
    panelsRoot.innerHTML =
      '<div class="panel"><h2 class="panel__title">Premium / Funding</h2>' +
      renderPremium(data.premium) +
      "</div>" +
      '<div class="panel"><h2 class="panel__title">24h Ticker</h2>' +
      render24h(data.ticker_24h) +
      "</div>" +
      '<div class="panel"><h2 class="panel__title">Open interest (거래소)</h2>' +
      renderOi(data.open_interest) +
      "</div>" +
      '<div class="panel"><h2 class="panel__title">Long / Short (Binance 15m 샘플)</h2>' +
      renderLsr(data.long_short_ratio) +
      "</div>" +
      '<div class="panel"><h2 class="panel__title">CVD 프록시</h2>' +
      renderCvd(data.cvd) +
      "</div>";
    renderErrors(data.errors);
  }

  async function tick() {
    if (inFlight) return;
    inFlight = true;
    setConn("connecting", "불러오는 중…");
    try {
      const res = await fetch(apiUrl(), { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      if (data.kind === "market_stream") {
        renderPayload(data);
      } else {
        renderMarks(data);
      }
      setConn("ok", "갱신 중 (약 " + pollSec.toFixed(1) + "s)");
    } catch (e) {
      setConn("error", "요청 실패 · 재시도");
      console.error(e);
    } finally {
      inFlight = false;
    }
  }

  function schedule() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = window.setInterval(tick, pollSec * 1000);
  }

  const chartPollSec = (function () {
    var raw = document.body && document.body.dataset ? document.body.dataset.chartPollSec : "";
    var n = parseFloat(raw);
    return !isNaN(n) && n > 5 ? n : 90;
  })();

  let chartPollTimer = null;
  let chartInstances = [];

  function sleep(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  function chartApiUrl() {
    const sym = symbolSelect ? symbolSelect.value || "BTCUSDT" : "BTCUSDT";
    return "/api/charts/liq?symbol=" + encodeURIComponent(sym);
  }

  function destroyCharts() {
    chartInstances.forEach(function (ch) {
      if (ch) ch.destroy();
    });
    chartInstances = [];
  }

  function setChartCardsVisible(step) {
    document.querySelectorAll("[data-chart-step]").forEach(function (el) {
      var s = parseInt(el.getAttribute("data-chart-step"), 10);
      if (s <= step) el.classList.add("chart-card--in");
      else el.classList.remove("chart-card--in");
    });
  }

  async function loadChartsSequential() {
    const metaEl = document.getElementById("chart-meta");
    const zonesEl = document.getElementById("chart-zones");
    const priceCtx = document.getElementById("chart-price");
    const oiCtx = document.getElementById("chart-oi");
    const cvdCtx = document.getElementById("chart-cvd");
    if (!window.Chart || !priceCtx || !oiCtx || !cvdCtx) return;

    setChartCardsVisible(0);
    destroyCharts();

    try {
      const res = await fetch(chartApiUrl(), { cache: "no-store" });
      if (!res.ok) {
        if (metaEl) metaEl.textContent = "차트 캐시 준비 중… (" + res.status + ")";
        return;
      }
      const data = await res.json();
      if (data.error) {
        if (metaEl) metaEl.textContent = data.hint || data.error;
        return;
      }
      const m = data.meta || {};
      if (metaEl) {
        metaEl.textContent =
          "1h " +
          (m.bars != null ? m.bars : "—") +
          "봉 · 저장 상한 " +
          (m.retain_bars != null ? m.retain_bars : "—") +
          " · window " +
          (m.window != null ? m.window : "—") +
          " · 갱신 " +
          (m.updated_at || "—");
      }

      const labels = (data.chart.t_ms || []).map(function (t) {
        var d = new Date(t);
        return (d.getMonth() + 1) + "/" + d.getDate() + " " + d.getHours() + "h";
      });
      const tc = "#58a6ff";
      const tc2 = "#3fb950";
      const grid = "rgba(255,255,255,0.06)";
      const tx = "#8b949e";
      const scales = {
        x: {
          ticks: { color: tx, maxTicksLimit: 10 },
          grid: { color: grid },
        },
        y: {
          ticks: { color: tx },
          grid: { color: grid },
        },
      };
      const common = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: tx } } },
        scales: scales,
      };

      await sleep(40);
      setChartCardsVisible(1);
      chartInstances.push(
        new Chart(priceCtx, {
          type: "line",
          data: {
            labels: labels,
            datasets: [
              {
                label: "종가",
                data: data.chart.close,
                borderColor: tc,
                backgroundColor: "rgba(88,166,255,0.08)",
                fill: true,
                tension: 0.15,
                pointRadius: 0,
              },
            ],
          },
          options: common,
        })
      );
      await sleep(140);
      setChartCardsVisible(2);
      chartInstances.push(
        new Chart(oiCtx, {
          type: "line",
          data: {
            labels: labels,
            datasets: [
              {
                label: "OI",
                data: data.chart.oi,
                borderColor: tc2,
                borderWidth: 1.5,
                pointRadius: 0,
              },
            ],
          },
          options: common,
        })
      );
      await sleep(140);
      setChartCardsVisible(3);
      chartInstances.push(
        new Chart(cvdCtx, {
          type: "bar",
          data: {
            labels: labels,
            datasets: [
              {
                label: "테이커 Δ",
                data: data.chart.cvd_delta,
                backgroundColor: "rgba(210,153,34,0.35)",
                borderRadius: 2,
              },
            ],
          },
          options: common,
        })
      );

      var lz = (data.liq_latest && data.liq_latest.map && data.liq_latest.map.long_liq_zones) || [];
      var sz = (data.liq_latest && data.liq_latest.map && data.liq_latest.map.short_liq_zones) || [];
      var dir = (data.liq_latest && data.liq_latest.direction) || {};
      if (zonesEl) {
        zonesEl.innerHTML =
          '<h3 class="panel__title">최근 청산 구간 (OI 유도)</h3>' +
          '<p class="muted" style="margin:0.25rem 0 0.75rem">bias <strong>' +
          (dir.bias || "—") +
          "</strong> · up " +
          (dir.up_strength != null ? dir.up_strength : "—") +
          " / down " +
          (dir.down_strength != null ? dir.down_strength : "—") +
          "</p>" +
          '<div class="zones__cols">' +
          '<div><strong>Long liq (아래)</strong><ul>' +
          lz
            .map(function (z) {
              return (
                "<li>" +
                z.price_low +
                " – " +
                z.price_high +
                ' <span class="muted">' +
                z.intensity +
                "</span></li>"
              );
            })
            .join("") +
          "</ul></div>" +
          '<div><strong>Short liq (위)</strong><ul>' +
          sz
            .map(function (z) {
              return (
                "<li>" +
                z.price_low +
                " – " +
                z.price_high +
                ' <span class="muted">' +
                z.intensity +
                "</span></li>"
              );
            })
            .join("") +
          "</ul></div></div>";
      }
    } catch (e) {
      if (metaEl) metaEl.textContent = "차트 로드 실패";
      console.error(e);
    }
  }

  function scheduleCharts() {
    if (chartPollTimer) clearInterval(chartPollTimer);
    chartPollTimer = window.setInterval(loadChartsSequential, chartPollSec * 1000);
  }

  function restartCharts() {
    if (chartPollTimer) clearInterval(chartPollTimer);
    loadChartsSequential();
    scheduleCharts();
  }

  function restart() {
    if (pollTimer) clearInterval(pollTimer);
    tick();
    schedule();
  }

  if (symbolSelect) {
    symbolSelect.addEventListener("change", function () {
      restart();
      restartCharts();
    });
  }

  restart();
  restartCharts();
})();
