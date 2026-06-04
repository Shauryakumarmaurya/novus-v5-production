window.NovusCharts = (() => {
        try {
            // ── Theme ──
            const T = {
                bg: '#000000', grid: 'rgba(0,229,255,0.1)', tick: '#00E5FF', label: '#FFFFFF',
                blue: '#00E5FF', blueLight: '#66FFFF', blueDim: 'rgba(0,229,255,0.15)',
                green: '#00FF41', greenDim: 'rgba(0,255,65,0.12)',
                amber: '#FFB300', amberDim: 'rgba(255,179,0,0.12)',
                red: '#FF0055', redDim: 'rgba(255,0,85,0.12)',
                cyan: '#00E5FF', purple: '#B026FF', pink: '#FF00A0',
                font: "'JetBrains Mono', monospace",
                mono: "'JetBrains Mono', monospace",
            };

            // Chart.js global defaults
            Chart.defaults.color = T.tick;
            Chart.defaults.font.family = T.font;
            Chart.defaults.font.size = 11;
            Chart.defaults.plugins.legend.labels.usePointStyle = true;
            Chart.defaults.plugins.legend.labels.pointStyleWidth = 8;
            Chart.defaults.plugins.legend.labels.padding = 16;
            Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(5,5,5,0.9)';
            Chart.defaults.plugins.tooltip.borderColor = '#00E5FF';
            Chart.defaults.plugins.tooltip.borderWidth = 1;
            Chart.defaults.plugins.tooltip.titleFont = { family: T.mono, weight: '600' };
            Chart.defaults.plugins.tooltip.bodyFont = { family: T.mono };
            Chart.defaults.plugins.tooltip.padding = 10;
            Chart.defaults.plugins.tooltip.cornerRadius = 2;

            const _instances = {};
            function _destroy(id) { if (_instances[id]) { _instances[id].destroy(); delete _instances[id]; } }
            function _create(id, config) { 
                _destroy(id); 
                const ctx = document.getElementById(id); 
                if (!ctx) return null; 
                ctx.classList.remove('hidden');
                if (ctx.parentElement && ctx.parentElement.textContent.includes('Analyzing data...')) {
                    Array.from(ctx.parentElement.childNodes).forEach(node => {
                        if (node.nodeType === Node.TEXT_NODE) node.remove();
                    });
                    ctx.parentElement.classList.remove('animate-pulse', 'border', 'border-dashed', 'border-base-border');
                }
                _instances[id] = new Chart(ctx, config); 
                return _instances[id]; 
            }

            function _gridOpts(showX = true) {
                return {
                    x: { grid: { color: T.grid, drawBorder: false }, ticks: { color: T.tick, font: { family: T.mono, size: 10 }, display: showX, maxRotation: 45 }, border: { display: false } },
                    y: { grid: { color: T.grid, drawBorder: false }, ticks: { color: T.tick, font: { family: T.mono, size: 10 }, callback: v => v >= 1000 ? (v/1000).toFixed(0)+'K' : v }, border: { display: false } },
                };
            }

            // ── Helper: extract row from screener tables ──
            function _getRow(tables, tableName, lineItem) {
                const tbl = tables[tableName];
                if (!tbl) return null;
                return tbl.find(r => {
                    const li = r['Line Item'] || r['Unnamed: 0'] || '';
                    return li.trim().toLowerCase().startsWith(lineItem.toLowerCase());
                });
            }
            function _getYears(row) {
                if (!row) return [];
                return Object.keys(row).filter(k => k !== 'Line Item' && k !== 'Unnamed: 0');
            }
            function _getValues(row, years) {
                return years.map(y => { const v = row[y]; return v === null || v === '' || v === 'NaN' ? null : parseFloat(String(v).replace(/,/g, '')); });
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 1: Revenue & PAT Trend (Executive Summary)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderRevenuePAT(screenerData) {
                const tables = screenerData.tables || {};
                const salesRow = _getRow(tables, 'Profit & Loss', 'Sales') || _getRow(tables, 'Quarterly Results', 'Sales');
                const patRow = _getRow(tables, 'Profit & Loss', 'Net Profit') || _getRow(tables, 'Quarterly Results', 'Net Profit');
                if (!salesRow) return;
                const years = _getYears(salesRow).slice(-8); // last 8 years
                const sales = _getValues(salesRow, years);
                const pat = patRow ? _getValues(patRow, years) : [];

                _create('chart-revenue-pat', {
                    type: 'line',
                    data: {
                        labels: years.map(y => y.replace('Mar ', "FY").replace('Dec ', "Q3 ").replace('Sep ', "Q2 ")),
                        datasets: [
                            { label: 'Revenue', data: sales, borderColor: T.blue, backgroundColor: T.blueDim, fill: true, tension: 0.3, pointRadius: 3, pointBackgroundColor: T.blue, borderWidth: 2 },
                            { label: 'Net Profit', data: pat, borderColor: T.green, backgroundColor: T.greenDim, fill: true, tension: 0.3, pointRadius: 3, pointBackgroundColor: T.green, borderWidth: 2 },
                        ],
                    },
                    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, scales: _gridOpts(), plugins: { legend: { position: 'top', align: 'end' } } },
                });
                document.getElementById('exec-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 2: Agent Confidence Radar (Executive Summary)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderAgentRadar(agentTrails) {
                if (!agentTrails || Object.keys(agentTrails).length === 0) return;
                const nameMap = {
                    forensic_quant: 'Forensic Quant', forensic_investigator: 'Forensic Audit',
                    narrative_decoder: 'Narrative', moat_architect: 'Moat',
                    capital_allocator: 'Capital', management_quality: 'Mgmt Quality',
                };
                const labels = []; const values = [];
                for (const [name, trail] of Object.entries(agentTrails)) {
                    if (name === 'pm_synthesis' || name === 'critic_agent') continue;
                    labels.push(nameMap[name] || name);
                    values.push(typeof trail.confidence === 'number' ? Math.round(trail.confidence * 100) : 50);
                }
                if (labels.length < 3) return;

                _create('chart-agent-radar', {
                    type: 'radar',
                    data: {
                        labels,
                        datasets: [{
                            label: 'Confidence %',
                            data: values,
                            borderColor: T.blue,
                            backgroundColor: 'rgba(59,130,246,0.15)',
                            pointBackgroundColor: T.blueLight,
                            pointBorderColor: T.blue,
                            pointRadius: 4,
                            borderWidth: 2,
                        }],
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        scales: {
                            r: {
                                beginAtZero: true, max: 100,
                                grid: { color: T.grid }, angleLines: { color: T.grid },
                                ticks: { display: false, stepSize: 25 },
                                pointLabels: { color: T.label, font: { family: T.font, size: 11, weight: '500' } },
                            },
                        },
                        plugins: { legend: { display: false } },
                    },
                });
                document.getElementById('exec-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 3: Cash Quality Gauge (Forensic Tab)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderCashQuality(quantFindings) {
                const ratio = quantFindings?.ocf_ebitda_ratio;
                const ctxEl = document.getElementById('chart-cash-quality');
                if (!ctxEl) return;
                
                if (ratio === undefined || ratio === null) {
                    ctxEl.parentElement.innerHTML = '<div class="h-full flex items-center justify-center text-txt-muted text-sm italic border border-dashed border-base-border rounded-md">Cash Quality data unavailable</div>';
                    document.getElementById('forensic-charts-row')?.classList.remove('hidden');
                    return;
                }
                const pct = Math.min(Math.round(ratio * 100), 100);
                const remaining = 100 - pct;
                const color = pct >= 80 ? T.green : pct >= 50 ? T.amber : T.red;
                const dimColor = pct >= 80 ? T.greenDim : pct >= 50 ? T.amberDim : T.redDim;
                const label = pct >= 80 ? 'STRONG' : pct >= 50 ? 'MODERATE' : 'WEAK';

                _create('chart-cash-quality', {
                    type: 'doughnut',
                    data: {
                        labels: ['OCF/EBITDA', 'Gap'],
                        datasets: [{ data: [pct, remaining], backgroundColor: [color, 'rgba(30,41,59,0.3)'], borderWidth: 0, cutout: '75%' }],
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: { enabled: false },
                        },
                    },
                    plugins: [{
                        id: 'centerText',
                        afterDraw(chart) {
                            const { ctx, chartArea: { width, height, top, left } } = chart;
                            const cx = left + width / 2; const cy = top + height / 2;
                            ctx.save();
                            ctx.fillStyle = color; ctx.font = `bold 28px ${T.mono}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                            ctx.fillText(`${pct}%`, cx, cy - 8);
                            ctx.fillStyle = T.tick; ctx.font = `600 11px ${T.font}`;
                            ctx.fillText(label, cx, cy + 18);
                            ctx.restore();
                        },
                    }],
                });
                document.getElementById('forensic-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 4: Working Capital Cycle (Forensic Tab)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderWorkingCapital(quantFindings) {
                const wc = quantFindings?.working_capital;
                const ctxEl = document.getElementById('chart-working-capital');
                if (!ctxEl) return;
                
                if (!wc) {
                    ctxEl.parentElement.innerHTML = '<div class="h-full flex items-center justify-center text-txt-muted text-sm italic border border-dashed border-base-border rounded-md">Working Capital data unavailable</div>';
                    document.getElementById('forensic-charts-row')?.classList.remove('hidden');
                    return;
                }
                const labels = []; const values = []; const colors = [];
                if (wc.dio != null) { labels.push('DIO (Inventory)'); values.push(wc.dio); colors.push(T.blue); }
                if (wc.dso != null) { labels.push('DSO (Receivables)'); values.push(wc.dso); colors.push(T.amber); }
                if (wc.dpo != null) { labels.push('DPO (Payables)'); values.push(wc.dpo); colors.push(T.green); }
                if (wc.ccc_days != null) { labels.push('CCC (Net Cycle)'); values.push(wc.ccc_days); colors.push(wc.ccc_days < 0 ? T.green : T.red); }
                
                if (!labels.length) {
                    ctxEl.parentElement.innerHTML = '<div class="h-full flex items-center justify-center text-txt-muted text-sm italic border border-dashed border-base-border rounded-md">Working Capital metrics empty</div>';
                    document.getElementById('forensic-charts-row')?.classList.remove('hidden');
                    return;
                }

                _create('chart-working-capital', {
                    type: 'bar',
                    data: { labels, datasets: [{ data: values, backgroundColor: colors.map(c => c + '33'), borderColor: colors, borderWidth: 1.5, borderRadius: 3 }] },
                    options: {
                        responsive: true, maintainAspectRatio: false, indexAxis: 'y',
                        scales: {
                            x: { grid: { color: T.grid }, ticks: { color: T.tick, font: { family: T.mono, size: 10 }, callback: v => v + 'd' }, border: { display: false } },
                            y: { grid: { display: false }, ticks: { color: T.label, font: { family: T.font, size: 11, weight: '500' } }, border: { display: false } },
                        },
                        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ctx.raw + ' days' } } },
                    },
                });
                document.getElementById('forensic-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 5: DuPont ROE Decomposition (Quant Canvas)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderDuPont(quantFindings) {
                const dp = quantFindings?.dupont;
                const ctxEl = document.getElementById('chart-dupont');
                if (!ctxEl) return;
                
                if (!dp) {
                    const parent = ctxEl.parentElement;
                    parent.innerHTML = '<div class="h-full flex items-center justify-center text-txt-muted text-sm italic border border-dashed border-base-border rounded-md">DuPont data unavailable</div>';
                    document.getElementById('quant-charts-row')?.classList.remove('hidden');
                    return;
                }
                
                const labels = ['Net Margin', 'Asset Turnover', 'Equity Multiplier', 'ROE'];
                const values = [dp.net_margin ? dp.net_margin * 100 : 0, dp.asset_turnover ? dp.asset_turnover * 100 : 0, dp.equity_multiplier ? dp.equity_multiplier * 100 : 0, dp.roe ? dp.roe * 100 : 0];
                const barColors = [T.cyan + 'AA', T.purple + 'AA', T.amber + 'AA', T.blue];
                const borderColors = [T.cyan, T.purple, T.amber, T.blue];

                _create('chart-dupont', {
                    type: 'bar',
                    data: { labels, datasets: [{ data: values, backgroundColor: barColors, borderColor: borderColors, borderWidth: 1.5, borderRadius: 4 }] },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        scales: {
                            x: { grid: { display: false }, ticks: { color: T.label, font: { family: T.font, size: 11, weight: '500' } }, border: { display: false } },
                            y: { grid: { color: T.grid }, ticks: { color: T.tick, font: { family: T.mono, size: 10 }, callback: v => v.toFixed(0) + '%' }, border: { display: false } },
                        },
                        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ctx.raw.toFixed(2) + '%' } } },
                    },
                });
                document.getElementById('quant-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 6: Revenue / EBITDA / PAT Grouped Bars (Quant Canvas)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderRevEbitdaPat(screenerData) {
                const tables = screenerData.tables || {};
                const salesRow = _getRow(tables, 'Profit & Loss', 'Sales') || _getRow(tables, 'Quarterly Results', 'Sales');
                const opProfitRow = _getRow(tables, 'Profit & Loss', 'Operating Profit') || _getRow(tables, 'Quarterly Results', 'Operating Profit');
                const patRow = _getRow(tables, 'Profit & Loss', 'Net Profit') || _getRow(tables, 'Quarterly Results', 'Net Profit');
                if (!salesRow) return;
                const years = _getYears(salesRow).slice(-6);
                const revenue = _getValues(salesRow, years);
                const ebitda = opProfitRow ? _getValues(opProfitRow, years) : [];
                const pat = patRow ? _getValues(patRow, years) : [];

                _create('chart-rev-ebitda-pat', {
                    type: 'bar',
                    data: {
                        labels: years.map(y => y.replace('Mar ', "FY").replace('Dec ', "Q3 ").replace('Sep ', "Q2 ")),
                        datasets: [
                            { label: 'Revenue', data: revenue, backgroundColor: T.blue + '55', borderColor: T.blue, borderWidth: 1.5, borderRadius: 3 },
                            { label: 'EBITDA', data: ebitda, backgroundColor: T.cyan + '55', borderColor: T.cyan, borderWidth: 1.5, borderRadius: 3 },
                            { label: 'PAT', data: pat, backgroundColor: T.green + '55', borderColor: T.green, borderWidth: 1.5, borderRadius: 3 },
                        ],
                    },
                    options: { responsive: true, maintainAspectRatio: false, scales: _gridOpts(), plugins: { legend: { position: 'top', align: 'end' } } },
                });
                document.getElementById('quant-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 7: Free Cash Flow Trend (Quant Canvas)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderFCFTrend(screenerData) {
                const tables = screenerData.tables || {};
                const ocfRow = _getRow(tables, 'Cash Flows', 'Cash from Operating') || _getRow(tables, 'Quarterly Results', 'Cash from Operating');
                const capexRow = _getRow(tables, 'Cash Flows', 'Fixed Assets Purchased') || _getRow(tables, 'Cash Flows', 'Capital Expenditure');
                if (!ocfRow) return;
                const years = _getYears(ocfRow).slice(-6);
                const ocf = _getValues(ocfRow, years);
                const capex = capexRow ? _getValues(capexRow, years).map(v => v ? Math.abs(v) : null) : [];
                const fcf = ocf.map((o, i) => o != null && capex[i] != null ? o - capex[i] : null);

                _create('chart-fcf-trend', {
                    type: 'bar',
                    data: {
                        labels: years.map(y => y.replace('Mar ', "FY").replace('Dec ', "Q3 ").replace('Sep ', "Q2 ")),
                        datasets: [
                            { label: 'OCF', data: ocf, backgroundColor: T.blue + '55', borderColor: T.blue, borderWidth: 1.5, borderRadius: 3, order: 2 },
                            { label: 'FCF', data: fcf, type: 'line', borderColor: T.green, backgroundColor: T.greenDim, pointBackgroundColor: T.green, pointRadius: 4, borderWidth: 2.5, tension: 0.3, fill: false, order: 1 },
                        ],
                    },
                    options: { responsive: true, maintainAspectRatio: false, scales: _gridOpts(), plugins: { legend: { position: 'top', align: 'end' } } },
                });
                document.getElementById('quant-charts-row')?.classList.remove('hidden');
            }

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // CHART 8: Debt vs Cash Position (Quant Canvas)
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            function renderDebtCash(screenerData) {
                const tables = screenerData.tables || {};
                const borrowRow = _getRow(tables, 'Balance Sheet', 'Borrowings');
                const cashRow = _getRow(tables, 'Balance Sheet', 'Cash Equivalents') || _getRow(tables, 'Balance Sheet', 'Cash');
                if (!borrowRow && !cashRow) return;
                const refRow = borrowRow || cashRow;
                const years = _getYears(refRow).slice(-6);
                const borrowings = borrowRow ? _getValues(borrowRow, years) : years.map(() => 0);
                const cash = cashRow ? _getValues(cashRow, years) : years.map(() => 0);

                _create('chart-debt-cash', {
                    type: 'bar',
                    data: {
                        labels: years.map(y => y.replace('Mar ', "FY").replace('Dec ', "Q3 ").replace('Sep ', "Q2 ")),
                        datasets: [
                            { label: 'Borrowings', data: borrowings, backgroundColor: T.red + '55', borderColor: T.red, borderWidth: 1.5, borderRadius: 3 },
                            { label: 'Cash & Equivalents', data: cash, backgroundColor: T.green + '55', borderColor: T.green, borderWidth: 1.5, borderRadius: 3 },
                        ],
                    },
                    options: { responsive: true, maintainAspectRatio: false, scales: _gridOpts(), plugins: { legend: { position: 'top', align: 'end' } } },
                });
                document.getElementById('quant-charts-row')?.classList.remove('hidden');
            }

            // ── Public API ──
            return {
                renderFromScreener(screenerData) {
                    if (!screenerData || !screenerData.tables) return;
                    renderRevenuePAT(screenerData);
                    renderRevEbitdaPat(screenerData);
                    renderFCFTrend(screenerData);
                    renderDebtCash(screenerData);
                },
                renderFromAgentData(agentTrails) {
                    if (!agentTrails) return;
                    // Quant findings
                    const quantTrail = agentTrails.forensic_quant;
                    const findings = quantTrail?.findings || quantTrail;
                    if (findings) {
                        renderCashQuality(findings);
                        renderWorkingCapital(findings);
                        renderDuPont(findings);
                    }
                    // Agent confidence radar
                    renderAgentRadar(agentTrails);
                },
                destroyAll() {
                    Object.keys(_instances).forEach(_destroy);
                },
            };
        } catch (e) {
            console.error("NovusCharts Engine Error:", e);
            document.getElementById('screener-container').insertAdjacentHTML('beforebegin', `<div class="p-4 bg-semantic-red/10 border border-semantic-red text-semantic-red text-xs font-mono mb-4">Chart Engine Error: ${e.message}</div>`);
            return null;
        }
    })();