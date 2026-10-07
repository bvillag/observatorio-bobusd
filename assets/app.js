(() => {
  const $ = (s) => document.querySelector(s);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const nf = (d) => new Intl.NumberFormat('es-BO', { minimumFractionDigits: d, maximumFractionDigits: d });
  const f = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? '—' : nf(d).format(x));
  const sg = (x, d = 2) => (x === null || x === undefined ? '—' : (x > 0 ? '+' : '') + f(x, d));
  const kfmt = (x) => (x === null || x === undefined ? '—' : x >= 1e6 ? f(x / 1e6, 1) + ' M' : x >= 1e3 ? f(x / 1e3, 0) + ' mil' : f(x, 0));
  const BOT = 'America/La_Paz';
  const tfmt = (iso, opts) => new Date(iso).toLocaleString('es-BO', { timeZone: BOT, ...opts });

  let D = null, range = 7;
  const charts = {};

  // ---------- tema
  const themeBtn = $('#theme');
  const syncThemeLabel = () => { themeBtn.textContent = document.documentElement.dataset.theme === 'dark' ? 'Claro' : 'Oscuro'; };
  syncThemeLabel();
  themeBtn.addEventListener('click', () => {
    const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('obs-theme', t); } catch (e) {}
    syncThemeLabel();
    renderAll();
  });

  // ---------- base de ECharts
  function base() {
    const text2 = css('--text-2'), grid = css('--grid'), border = css('--border');
    return {
      textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: text2 },
      grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis', backgroundColor: css('--surface-2'), borderColor: border, borderWidth: 1,
        textStyle: { color: css('--text'), fontFamily: 'IBM Plex Mono, monospace', fontSize: 12 },
        axisPointer: { type: 'line', lineStyle: { color: css('--border-2') } }
      },
      xAxis: { axisLine: { lineStyle: { color: border } }, axisTick: { show: false }, axisLabel: { color: css('--text-3'), fontSize: 11, hideOverlap: true }, splitLine: { show: false } },
      yAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: css('--text-3'), fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }, splitLine: { lineStyle: { color: grid } }, scale: true },
      legend: { top: 0, right: 0, textStyle: { color: text2, fontSize: 12 }, itemWidth: 16, itemHeight: 2, icon: 'rect' },
      animation: false
    };
  }
  const merge = (a, b) => {
    const o = { ...a };
    for (const k in b) o[k] = b[k] && typeof b[k] === 'object' && !Array.isArray(b[k]) && a[k] && typeof a[k] === 'object' && !Array.isArray(a[k]) ? merge(a[k], b[k]) : b[k];
    return o;
  };
  function draw(id, opt) {
    const el = document.getElementById(id);
    if (!el) return;
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el, null, { renderer: 'canvas' });
    charts[id].setOption(merge(base(), opt));
  }
  function empty(id, msg) {
    if (charts[id]) { charts[id].dispose(); delete charts[id]; }
    document.getElementById(id).innerHTML = `<div class="empty">${msg}</div>`;
  }
  window.addEventListener('resize', () => Object.values(charts).forEach((c) => c.resize()));

  const tile = (k, v, s = '') => `<div class="tile"><p class="k">${k}</p><p class="v">${v}</p>${s ? `<p class="s">${s}</p>` : ''}</div>`;
  const narrow = () => window.innerWidth < 600;
  const timeAxis = { type: 'time', splitNumber: 5, axisLabel: { hideOverlap: true, formatter: (v) => tfmt(new Date(v).toISOString(), { day: '2-digit', month: 'short' }) } };
  const tipTime = (p) => tfmt(new Date(p[0].value[0]).toISOString(), { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });

  // ---------- secciones
  function renderStatus() {
    const p = D.p2p;
    const last = p ? p.last : D.generated;
    const mins = Math.round((Date.now() - new Date(last)) / 60000);
    const el = $('#status');
    el.classList.toggle('stale', mins > 30);
    $('#status-text').textContent = p
      ? `Último dato P2P: ${tfmt(last, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })} (hace ${mins} min) · TCO al corte ${D.tco.cutoff.at(-1)}`
      : `Datos del BCB al corte ${D.tco.cutoff.at(-1)} · datos P2P en camino`;
    $('#gen').textContent = 'Generado ' + tfmt(D.generated, { dateStyle: 'medium', timeStyle: 'short' }) + ' (hora de Bolivia)';
  }

  function renderNow() {
    const p = D.p2p;
    const tcoLast = D.tco.tco.at(-1);
    if (!p) { $('#tiles').innerHTML = tile('TCO vigente', f(tcoLast, 2), 'Bs por USD'); return; }
    const n = p.now;
    $('#tiles').innerHTML =
      tile('Mid P2P', f(n.mid, 3), 'Bs por USDT') +
      tile('TCO vigente', f(n.tco, 2), 'Bs por USD · BCB') +
      tile('Diferencia P2P vs TCO', sg(n.basis_pct, 2) + '%', n.basis_pct >= 0 ? 'el P2P está más caro' : 'el P2P está más barato') +
      tile('Spread compra-venta', f(n.spread_pct, 2) + '%', 'P2P, para 1.000 USDT') +
      tile('Profundidad a ±1%', kfmt((n.depth1_ask || 0) + (n.depth1_bid || 0)), `USDT · ${kfmt(n.depth1_ask)} venta / ${kfmt(n.depth1_bid)} compra`);
  }

  function cut(s) {
    if (!range) return s.t.map((_, i) => i);
    const lim = Date.parse(s.t.at(-1)) - range * 86400000;
    return s.t.map((t, i) => (Date.parse(t) >= lim ? i : -1)).filter((i) => i >= 0);
  }

  function renderPrice() {
    const s1 = css('--s1'), s2 = css('--s2');
    if (D.p2p) {
      const s = D.p2p.s10, ix = cut(s);
      const pt = (k) => ix.map((i) => [s.t[i], s[k][i]]);
      draw('c-price', {
        legend: { data: ['Mid P2P', 'TCO vigente', 'Banda compra-venta'] },
        tooltip: { formatter: (ps) => `${tipTime(ps)}<br>` + ps.filter((x) => x.seriesName !== '_lo').map((x) => `${x.marker}${x.seriesName === '_hi' ? 'Venta / compra' : x.seriesName}: ${x.seriesName === '_hi' ? f(s.ask[ix[x.dataIndex]], 3) + ' / ' + f(s.bid[ix[x.dataIndex]], 3) : f(x.value[1], 3)}`).join('<br>') },
        xAxis: timeAxis, yAxis: { type: 'value' },
        series: [
          { name: '_lo', type: 'line', data: pt('bid'), stack: 'b', symbol: 'none', lineStyle: { opacity: 0 }, silent: true },
          { name: 'Banda compra-venta', type: 'line', data: ix.map((i) => [s.t[i], s.ask[i] - s.bid[i]]), stack: 'b', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: css('--band') }, color: css('--band'), tooltip: { show: false } },
          { name: 'Mid P2P', type: 'line', data: pt('mid'), symbol: 'none', lineStyle: { width: 2, color: s1 }, color: s1 },
          { name: 'TCO vigente', type: 'line', step: 'end', data: pt('tco'), symbol: 'none', lineStyle: { width: 2, type: [6, 4], color: s2 }, color: s2 }
        ]
      });
    } else empty('c-price', 'Los datos P2P aparecerán aquí en cuanto el servidor haga su primer envío.');

    const t = D.tco;
    draw('c-tco', {
      tooltip: { formatter: (ps) => `Corte ${ps[0].value[0]}<br>${ps[0].marker}TCO: ${f(ps[0].value[1], 2)}<br>Método: ${t.method[ps[0].dataIndex]}` },
      xAxis: { type: 'category', data: t.cutoff, axisLabel: { formatter: (v) => v.slice(5).replace('-', '/') } },
      yAxis: { type: 'value' },
      series: [{ type: 'line', step: 'end', data: t.cutoff.map((d, i) => [d, t.tco[i]]), symbol: 'none', lineStyle: { width: 2, color: s2 }, color: s2,
        markLine: { symbol: 'none', silent: true, label: { formatter: 'RD 142', color: css('--text-3'), fontSize: 11 }, lineStyle: { color: css('--border-2'), type: 'dashed' }, data: [{ xAxis: '2026-09-25' }] } }]
    });
  }

  function renderBasis() {
    const p = D.p2p;
    if (!p) { $('#tiles-basis').innerHTML = ''; empty('c-basis', 'Sin datos P2P todavía.'); empty('c-hist', 'Sin datos P2P todavía.'); return; }
    const b = p.basis, tar = b.tar;
    $('#b-prelim-basis').hidden = !b.preliminary;
    const hl = b.half_life_min;
    $('#tiles-basis').innerHTML =
      tile('Base media', sg(b.mean, 2) + '%', `desvío ${f(b.sd, 2)} pp`) +
      tile('Rango 90%', `${sg(b.p5, 2)} a ${sg(b.p95, 2)}%`, 'percentiles 5 y 95') +
      tile('Vida media AR(1)', hl === null ? '—' : hl >= 120 ? f(hl / 60, 1) + ' h' : f(hl, 0) + ' min', `φ = ${f(b.ar1_phi, 3)} por 10 min`) +
      tile('Fuera de la banda', tar ? f(tar.pct_outside, 0) + '%' : '—', tar ? `banda ±${f(tar.theta_pct, 2)} pp` : 'faltan datos');
    const s = p.s10, ix = cut(s);
    const s1 = css('--s1');
    const marks = tar ? { silent: true, itemStyle: { color: css('--zone') }, data: [[{ yAxis: tar.center_pct - tar.theta_pct }, { yAxis: tar.center_pct + tar.theta_pct }]] } : undefined;
    draw('c-basis', {
      tooltip: { formatter: (ps) => `${tipTime(ps)}<br>${ps[0].marker}Base: ${sg(ps[0].value[1], 2)}%` },
      xAxis: timeAxis, yAxis: { type: 'value', axisLabel: { formatter: (v) => f(v, 1) + '%' } },
      series: [{ name: 'Base', type: 'line', data: ix.map((i) => [s.t[i], s.basis[i]]), symbol: 'none', lineStyle: { width: 1.6, color: s1 }, color: s1, markArea: marks,
        markLine: { symbol: 'none', silent: true, lineStyle: { color: css('--border-2') }, label: { show: false }, data: [{ yAxis: 0 }] } }]
    });
    const e = b.hist_edges, tot = b.hist.reduce((a, c) => a + c, 0) || 1;
    draw('c-hist', {
      tooltip: { trigger: 'item', formatter: (x) => `${f(e[x.dataIndex], 2)} a ${f(e[x.dataIndex + 1], 2)}%<br>${f(x.value, 1)}% del tiempo` },
      xAxis: { type: 'category', data: b.hist.map((_, i) => f((e[i] + e[i + 1]) / 2, 2)), axisLabel: { interval: 'auto' } },
      yAxis: { type: 'value', scale: false, axisLabel: { formatter: (v) => v + '%' } },
      series: [{ type: 'bar', data: b.hist.map((c) => +(c / tot * 100).toFixed(2)), barCategoryGap: '8%', itemStyle: { color: s1, borderRadius: [3, 3, 0, 0] } }]
    });
  }

  function renderSize() {
    const s1 = css('--s1');
    const p = D.p2p;
    if (p) {
      const c = p.size_curve, lab = c.sizes.map((x) => x >= 1000 ? f(x / 1000, 0) + ' mil' : String(x));
      draw('c-size', {
        legend: { data: ['Comprar USDT', 'Vender USDT'] },
        tooltip: { valueFormatter: (v) => (v === null ? 'sin profundidad' : f(v, 2) + '%') },
        xAxis: { type: 'category', data: lab, name: 'USDT', nameTextStyle: { color: css('--text-3'), fontSize: 11 } },
        yAxis: { type: 'value', scale: false, axisLabel: { formatter: (v) => f(v, 2) + '%' } },
        series: [
          { name: 'Comprar USDT', type: 'line', data: c.ask_24h, symbolSize: 8, lineStyle: { width: 2, color: s1 }, itemStyle: { color: s1 }, connectNulls: false },
          { name: 'Vender USDT', type: 'line', data: c.bid_24h, symbolSize: 8, symbol: 'emptyCircle', lineStyle: { width: 2, type: [6, 4], color: s1 }, itemStyle: { color: s1 } }
        ]
      });
    } else empty('c-size', 'Sin datos P2P todavía.');
    const tk = D.banks.tickets;
    draw('c-tickets', {
      tooltip: { trigger: 'item', formatter: (x) => `Operaciones de ${tk[x.dataIndex].bin} USD<br>Precio vs TCO: ${sg(x.value, 2)}%<br>${f(tk[x.dataIndex].ops_pct, 0)}% de las operaciones · ${f(tk[x.dataIndex].usd_pct, 1)}% del monto` },
      grid: { bottom: 24 },
      xAxis: { type: 'category', data: tk.map((t) => t.bin), axisLabel: { interval: 0, fontSize: 10.5 } },
      yAxis: { type: 'value', scale: false, axisLabel: { formatter: (v) => f(v, 1) + '%' } },
      series: [{ type: 'bar', data: tk.map((t) => t.dev_pct), barWidth: '46%', itemStyle: { color: s1, borderRadius: 3 },
        label: { show: true, position: 'outside', color: css('--text-2'), fontFamily: 'IBM Plex Mono', fontSize: 11, formatter: (x) => sg(x.value, 1) + '%' } }]
    });
  }

  function renderLiquidity() {
    const p = D.p2p;
    if (!p) { $('#tiles-liq').innerHTML = ''; empty('c-depth', 'Sin datos P2P todavía.'); empty('c-heat', 'Sin datos P2P todavía.'); return; }
    const L = p.limits, n = p.now;
    $('#tiles-liq').innerHTML =
      tile('Anuncios activos', `${n.n_ask} / ${n.n_bid}`, 'venta / compra') +
      tile('Monto mínimo típico', f(L.min_bob_median.ask, 0) + ' Bs', 'mediana de anuncios, 24 h') +
      tile('Monto máximo típico', f(L.max_bob_median.ask, 0) + ' Bs', 'por operación') +
      tile('Comerciantes distintos', f(L.merchants_24h, 0), `cumplimiento mediano ${f(L.finish_rate_median, 0)}%`);
    const s = p.s10, ix = cut(s), s1 = css('--s1'), s2 = css('--s2');
    draw('c-depth', {
      legend: { data: ['Venta (asks)', 'Compra (bids)'] },
      tooltip: { valueFormatter: (v) => kfmt(v) + ' USDT' },
      xAxis: timeAxis, yAxis: { type: 'value', axisLabel: { formatter: (v) => kfmt(v) } },
      series: [
        { name: 'Venta (asks)', type: 'line', data: ix.map((i) => [s.t[i], s.depth_ask[i]]), symbol: 'none', lineStyle: { width: 2, color: s1 }, color: s1 },
        { name: 'Compra (bids)', type: 'line', data: ix.map((i) => [s.t[i], s.depth_bid[i]]), symbol: 'none', lineStyle: { width: 2, color: s2, type: [6, 4] }, color: s2 }
      ]
    });
    const days = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
    const vals = p.heat.map((h) => h[2]).filter((v) => v !== null);
    draw('c-heat', {
      tooltip: { trigger: 'item', formatter: (x) => `${days[x.value[1]]} ${String(x.value[0]).padStart(2, '0')}:00<br>Spread mediano: ${f(x.value[2], 3)}%` },
      grid: { left: 8, right: 8, top: 8, bottom: 44, containLabel: true },
      xAxis: { type: 'category', data: [...Array(24).keys()].map((h) => String(h).padStart(2, '0')), splitArea: { show: false } },
      yAxis: { type: 'category', data: days, inverse: true, scale: false, splitLine: { show: false }, axisLabel: { fontFamily: 'Inter' } },
      visualMap: { min: Math.min(...vals), max: Math.max(...vals), calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 120, itemWidth: 10,
        text: ['más caro', 'más barato'], textStyle: { color: css('--text-3'), fontSize: 11 }, inRange: { color: [css('--surface-2'), s1] } },
      series: [{ type: 'heatmap', data: p.heat, itemStyle: { borderColor: css('--surface'), borderWidth: 2, borderRadius: 3 } }]
    });
  }

  function renderDynamics() {
    const p = D.p2p, s1 = css('--s1'), t = D.tco;
    const vr = p ? p.dynamics.vr : null;
    const vrTile = (q, lab) => { const v = vr && vr[q]; return tile(`Razón de varianzas ${lab}`, v && v.vr !== null ? f(v.vr, 2) : '—', v && v.z !== null ? `z = ${f(v.z, 2)}` : 'faltan datos'); };
    $('#tiles-dyn').innerHTML =
      tile('Volatilidad anualizada P2P', p && p.dynamics.vol_ann_pct !== null ? f(p.dynamics.vol_ann_pct, 0) + '%' : '—', 'retornos de 10 min') +
      vrTile('q6', '1 h') + vrTile('q36', '6 h') +
      tile('Razón de varianzas TCO (5 días)', f(t.vr5.vr, 2), `z = ${f(t.vr5.z, 2)} · vol. diaria ${f(t.vol_daily_pct, 2)}%`);
    if (!p) { empty('c-rv', 'Sin datos P2P todavía.'); empty('c-acf', 'Sin datos P2P todavía.'); return; }
    $('#b-prelim-dyn').hidden = !p.dynamics.preliminary;
    const rv = p.dynamics.rv_daily;
    draw('c-rv', {
      tooltip: { valueFormatter: (v) => f(v, 2) + '%' },
      xAxis: { type: 'category', data: rv.date.map((d) => d.slice(5).replace('-', '/')) },
      yAxis: { type: 'value', scale: false, axisLabel: { formatter: (v) => f(v, 1) + '%' } },
      series: [{ name: 'Volatilidad', type: 'bar', data: rv.pct, barMaxWidth: 28, itemStyle: { color: s1, borderRadius: [3, 3, 0, 0] } }]
    });
    const acf = p.dynamics.acf, n = Math.max(1, p.s10.t.length - 1), band = 2 / Math.sqrt(n);
    draw('c-acf', {
      tooltip: { valueFormatter: (v) => f(v, 3) },
      xAxis: { type: 'category', data: acf.map((_, i) => String(i + 1)), name: 'rezago', nameTextStyle: { color: css('--text-3'), fontSize: 11 } },
      yAxis: { type: 'value', scale: false },
      series: [{ name: 'Autocorrelación', type: 'bar', data: acf, barWidth: '40%', itemStyle: { color: s1, borderRadius: 2 },
        markLine: { symbol: 'none', silent: true, label: { show: false }, lineStyle: { color: css('--border-2'), type: 'dashed' }, data: [{ yAxis: band }, { yAxis: -band }] } }]
    });
  }

  function renderBanks() {
    const B = D.banks, d = B.daily, s1 = css('--s1'), s2 = css('--s2');
    $('#tiles-banks').innerHTML =
      tile('Días con datos', f(B.n_days, 0), `USD ${f(B.total_usd_m, 0)} millones comprados`) +
      tile('HHI promedio', f(B.hhi_mean, 2), '> 0,25 = muy concentrado') +
      tile('3 bancos principales', f(B.top3_mean, 0) + '%', 'del monto diario, promedio') +
      tile('Dispersión típica', f(B.dispersion_median, 1) + ' pp', 'p90 − p10 dentro del día');
    const x = d.date.map((v) => v.slice(5).replace('-', '/'));
    draw('c-disp', {
      legend: { data: ['Percentil 90', 'Percentil 10'] },
      tooltip: { valueFormatter: (v) => sg(v, 2) + '%' },
      xAxis: { type: 'category', data: x }, yAxis: { type: 'value', axisLabel: { formatter: (v) => f(v, 0) + '%' } },
      series: [
        { name: 'Percentil 90', type: 'line', data: d.p90, symbol: 'none', lineStyle: { width: 2, color: s1 }, color: s1 },
        { name: 'Percentil 10', type: 'line', data: d.p10, symbol: 'none', lineStyle: { width: 2, color: s1, type: [6, 4] }, color: s1,
          markLine: { symbol: 'none', silent: true, label: { show: false }, lineStyle: { color: css('--border-2') }, data: [{ yAxis: 0 }] } }
      ]
    });
    const sh = [...B.share].reverse();
    draw('c-share', {
      tooltip: { trigger: 'item', formatter: (v) => `${v.name}: ${f(v.value, 1)}%` },
      grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: 'value', show: false, scale: false }, yAxis: { type: 'category', data: sh.map((b) => b.bank.replace('Banco ', '')), axisLabel: { fontFamily: 'Inter', color: css('--text-2') }, splitLine: { show: false } },
      series: [{ type: 'bar', data: sh.map((b) => b.pct), barWidth: '56%', itemStyle: { color: s1, borderRadius: 3 },
        label: { show: true, position: 'right', color: css('--text-2'), fontFamily: 'IBM Plex Mono', fontSize: 11, formatter: (v) => f(v.value, 1) + '%' } }]
    });
    draw('c-hhi', {
      tooltip: { valueFormatter: (v) => f(v, 3) },
      xAxis: { type: 'category', data: x }, yAxis: { type: 'value', scale: false },
      series: [{ name: 'HHI', type: 'line', data: d.hhi, symbol: 'none', lineStyle: { width: 2, color: s2 }, color: s2,
        markLine: { symbol: 'none', silent: true, label: { formatter: 'umbral 0,25', color: css('--text-3'), fontSize: 11 }, lineStyle: { color: css('--border-2'), type: 'dashed' }, data: [{ yAxis: 0.25 }] } }]
    });
  }

  function renderDownloads() {
    const ul = $('#downloads');
    const days = (D.csv_days || []).slice(-10).reverse();
    ul.innerHTML = '<li><a href="data/obs.json">obs.json</a> (todo lo que dibuja la página)</li><li><a href="data/csv/tco.csv">tco.csv</a> (serie del TCO)</li>' +
      days.map((d) => `<li><a href="data/csv/p2p_${d}.csv">p2p_${d}.csv</a></li>`).join('') +
      (D.csv_days && D.csv_days.length > 10 ? '<li>Días anteriores: en la carpeta <a href="https://github.com/bvillag/observatorio-bobusd/tree/main/data/csv">data/csv</a></li>' : '');
  }

  function renderAll() {
    if (!D) return;
    renderStatus(); renderNow(); renderPrice(); renderBasis(); renderSize(); renderLiquidity(); renderDynamics(); renderBanks(); renderDownloads();
  }

  document.querySelectorAll('#range button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('#range button').forEach((x) => x.classList.toggle('on', x === b));
    range = +b.dataset.r; renderPrice(); renderBasis(); renderLiquidity();
  }));

  async function load() {
    try {
      const r = await fetch('data/obs.json?ts=' + Date.now(), { cache: 'no-store' });
      D = await r.json();
      renderAll();
    } catch (e) {
      $('#status-text').textContent = 'No se pudieron cargar los datos. Intenta recargar la página.';
    }
  }
  load();
  setInterval(load, 5 * 60 * 1000);
})();
