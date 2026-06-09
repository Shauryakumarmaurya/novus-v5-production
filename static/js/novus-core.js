document.addEventListener('DOMContentLoaded', () => {
            // Display names for known tickers; anything else falls back to the symbol.
            const TICKER_NAMES = {
                "ALEMBICLTD": "Alembic Limited",
                "AUROPHARMA": "Aurobindo Pharma",
                "CIPLA": "Cipla",
                "DIVISLAB": "Divi's Laboratories",
                "DRREDDY": "Dr. Reddy's Laboratories",
                "GRANULES": "Granules India",
                "LAURUSLABS": "Laurus Labs",
                "LUPIN": "Lupin",
                "SUNPHARMA": "Sun Pharmaceuticals",
                "ZYDUSLIFE": "Zydus Lifesciences",
                "HINDUNILVR": "Hindustan Unilever"
            };

            const tickerSelect = document.getElementById('ticker');

            function populateTickers(tickers) {
                tickerSelect.innerHTML = '';
                const optgroup = document.createElement('optgroup');
                optgroup.label = "📊 Coverage Universe";
                tickers.forEach(t => {
                    const option = document.createElement('option');
                    option.value = t;
                    option.textContent = TICKER_NAMES[t] || t;
                    optgroup.appendChild(option);
                });
                tickerSelect.appendChild(optgroup);
            }

            // Drive the universe from what's actually ingested in the RAG store;
            // fall back to the known list if the endpoint is unreachable.
            populateTickers(Object.keys(TICKER_NAMES));
            fetch('/api/v1/tickers')
                .then(r => r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)))
                .then(data => {
                    const ingested = (data.tickers || []).map(t => t.ticker);
                    if (ingested.length) populateTickers(ingested);
                })
                .catch(err => console.warn('[Novus] Ticker list fallback (static):', err.message));
            const form = document.getElementById('report-form');
            const tickerInput = document.getElementById('ticker');
            const fileInput = document.getElementById('files');
            const dropZone = document.getElementById('drop-zone');
            const fileList = document.getElementById('file-list');
            const generateBtn = document.getElementById('generate-btn');
            const btnText = document.getElementById('btn-text');
            const btnLoader = document.getElementById('btn-loader');
            const reportPlaceholder = document.getElementById('report-placeholder');
            const reportContent = document.getElementById('report-content');
            const reportLoader = document.getElementById('report-loader');
            const loaderStage = document.getElementById('loader-stage');
            const errorMessage = document.getElementById('error-message');
            const exportPdfBtn = document.getElementById('export-pdf-btn');
            const agentPipeline = document.getElementById('agent-pipeline');
            const agentStatusList = document.getElementById('agent-status-list');
            const liveTerminal = document.getElementById('live-terminal');
            const liveAgentFeed = document.getElementById('live-agent-feed');
            const liveCounter = document.getElementById('live-counter');
            const insightDashboard = document.getElementById('insight-dashboard');
            const insightCards = document.getElementById('insight-cards');

            // ── Markdown Extensions ──
            const terminalStylesExt = {
                type: 'output',
                filter: function (text) {
                    return text.replace(/\[([^<>\]\n]{1,50})\]/g, '<span class="calc-badge">[$1]</span>');
                }
            };
            
            const semanticExt = {
                type: 'output',
                filter: function (text) {
                    // Highlight [DATA WARNING] and DATA UNAVAILABLE
                    text = text.replace(/\[DATA WARNING\]/g, '<span class="bg-semantic-amber/20 text-semantic-amber px-1.5 py-[1px] rounded-sm border border-semantic-amber/50 font-bold text-[10px] tracking-wide font-mono inline-block mb-1">[DATA WARNING]</span>');
                    text = text.replace(/DATA UNAVAILABLE/g, '<span class="bg-semantic-amber/20 text-semantic-amber px-1.5 py-[1px] rounded-sm border border-semantic-amber/50 font-bold text-[10px] tracking-wide font-mono inline-block">DATA UNAVAILABLE</span>');

                    // Highlight positive growth metrics
                    text = text.replace(/(gaining|up|increase[d]?|growth|positive|gains?)\s+([^\s<]*?(?:\d+\.?\d*[%x]?|bps|mn|cr))\b/gi, '$1 <span class="text-semantic-green font-mono font-bold">$2</span>');
                    
                    // Highlight negative growth metrics
                    text = text.replace(/(declining|down|decrease[d]?|negative|drop|loss|falling|contraction)\s+([^\s<]*?(?:\d+\.?\d*[%x]?|bps|mn|cr))\b/gi, '$1 <span class="text-semantic-red font-mono font-bold">$2</span>');

                    // Intercept Risk/Opportunity Arrays
                    text = text.replace(/<li>\s*<strong>(Risk|Opportunity):?<\/strong>\s*(.*?)\s*\|\s*<strong>(?:Probability|Likelihood):?<\/strong>\s*(.*?)\s*\|\s*<strong>Impact:?<\/strong>\s*(.*?)\s*<\/li>/gi, function(match, typeLabel, contentText, probText, impactText) {
                        const isRisk = typeLabel.toLowerCase() === 'risk';
                        const probClean = probText.replace(/<\/?[^>]+(>|$)/g, "").trim().toUpperCase(); // Strip HTML tags just in case
                        
                        let probClass = 'text-semantic-green bg-semantic-green/10 border-semantic-green/30';
                        if (probClean === 'HIGH' || probClean.includes('HIGH')) {
                            probClass = isRisk ? 'text-semantic-red bg-semantic-red/10 border-semantic-red/30' : 'text-semantic-green bg-semantic-green/10 border-semantic-green/30';
                        } else if (probClean === 'MEDIUM' || probClean.includes('MEDIUM')) {
                            probClass = 'text-semantic-amber bg-semantic-amber/10 border-semantic-amber/30';
                        }
                        
                        const accentBorder = isRisk ? 'border-l-semantic-red' : 'border-l-semantic-green';

                        return `
                            <div class="mb-4 bg-base-elevated rounded-r-md border border-base-border ${accentBorder} border-l-4 overflow-hidden shadow-sm not-prose">
                                <div class="px-4 py-3 border-b border-base-border/50 bg-black/20 flex flex-col sm:flex-row sm:items-start justify-between gap-4">
                                    <div class="font-sans font-medium text-txt-primary leading-snug">
                                        <span class="text-[10px] uppercase tracking-wider font-bold ${isRisk ? 'text-semantic-red' : 'text-semantic-green'} mr-2">${typeLabel.toUpperCase()}</span>
                                        ${contentText.trim()}
                                    </div>
                                    <div class="flex flex-col items-start sm:items-end shrink-0 gap-1 mt-1 sm:mt-0">
                                        <span class="text-[10px] uppercase tracking-wider font-semibold text-txt-muted">Probability</span>
                                        <span class="text-[11px] px-2 py-0.5 rounded border ${probClass} font-mono font-bold">${probText.trim()}</span>
                                    </div>
                                </div>
                                <div class="px-4 py-3 bg-base-bg/50">
                                    <div class="text-[10px] uppercase tracking-wider font-semibold text-txt-muted mb-1.5">Impact Analysis</div>
                                    <div class="text-sm font-sans text-txt-secondary leading-relaxed">${impactText.trim()}</div>
                                </div>
                            </div>
                        `;
                    });

                    // Cleanup empty <ul> wrappers left behind if all LIs are converted to divs
                    text = text.replace(/<ul>\s*(<div class="mb-4 bg-base-elevated[\s\S]*?<\/div>\s*)+<\/ul>/gi, function(match) {
                        return match.replace(/<ul>|<\/ul>/g, '');
                    });

                    return text;
                }
            };

            // Phase 2 Extension: Wrap H2 sections in beautiful UI cards
            const sectionCardExt = {
                type: 'output',
                filter: function (text) {
                    const temp = document.createElement('div');
                    temp.innerHTML = text;
                    
                    const h2s = temp.querySelectorAll('h2');
                    if (h2s.length === 0) return text; // If no H2, leave as is

                    const newContainer = document.createElement('div');
                    let currentSection = null;
                    let currentBody = null;

                    Array.from(temp.childNodes).forEach(node => {
                        if (node.nodeName === 'H2') {
                            currentSection = document.createElement('div');
                            currentSection.className = 'mb-8 bg-base-elevated rounded-lg border border-base-border/50 shadow-lg overflow-hidden';
                            
                            const headerBg = document.createElement('div');
                            headerBg.className = 'px-6 py-4 border-b border-base-border/30 bg-black/40';
                            
                            const h2Clone = node.cloneNode(true);
                            // Override default prose h2 styles with high specificity tailwind
                            h2Clone.className = 'font-sans font-semibold text-xl text-txt-primary !m-0 !p-0 !border-none';
                            headerBg.appendChild(h2Clone);
                            
                            currentSection.appendChild(headerBg);
                            
                            currentBody = document.createElement('div');
                            // Keep report-prose formatting inside the body, but add padding
                            currentBody.className = 'p-6 report-prose-body';
                            currentSection.appendChild(currentBody);
                            
                            newContainer.appendChild(currentSection);
                        } else if (currentBody) {
                            currentBody.appendChild(node.cloneNode(true));
                        } else {
                            newContainer.appendChild(node.cloneNode(true));
                        }
                    });
                    
                    return newContainer.innerHTML;
                }
            };

            const markdownConverter = new showdown.Converter({
                tables: true,
                extensions: [terminalStylesExt, semanticExt, sectionCardExt]
            });
            window.terminalStylesExt = terminalStylesExt;
            window.semanticExt = semanticExt;
            window.sectionCardExt = sectionCardExt;

            // ── Tab Switching ──
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    btn.classList.add('active');
                    document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
                });
            });

            function switchTab(tabName) {
                document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('active'); if (b.dataset.tab === tabName) b.classList.add('active'); });
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                document.getElementById(`tab-${tabName}`).classList.add('active');
            }

            // ── Screener Fetch ──
            async function fetchAndRenderScreener(ticker) {
                const container = document.getElementById('screener-container');
                container.innerHTML = '<div class="flex items-center gap-2 text-[11px] font-mono text-accent-brandLight animate-pulse py-4"><span class="w-2 h-2 rounded-none bg-accent-brand"></span>>_ Fetching quantitative datasets...</div>';
                try {
                    const res = await fetch(`/api/v1/screener_data?ticker=${ticker}`);
                    if (!res.ok) throw new Error('Screener fetch failed');
                    const data = await res.json();
                    if (!data.tables || Object.keys(data.tables).length === 0) {
                        container.innerHTML = '<p class="text-sm font-sans text-txt-muted py-10 text-center">No quantitative datasets located.</p>';
                        return;
                    }
                    let html = '<div class="space-y-6">';
                    for (const [title, rows] of Object.entries(data.tables)) {
                        html += `<div><h3 class="text-sm font-semibold font-sans text-txt-primary mb-3">${title}</h3>`;
                        html += '<div class="overflow-x-auto rounded-md border border-base-border"><table class="data-table">';
                        if (rows.length > 0) {
                            html += '<thead><tr>';
                            for (const key of Object.keys(rows[0])) {
                                const dk = key === "Unnamed: 0" || key === "Line Item" ? "Line Item" : key;
                                html += `<th>${dk}</th>`;
                            }
                            html += '</tr></thead><tbody>';
                            let isEvenParent = false;
                            for (const row of rows) {
                                const lineItemRaw = row["Line Item"] || row["Unnamed: 0"] || "";
                                const strCv = String(lineItemRaw);
                                const isSubItem = strCv.startsWith("  ");
                                
                                if (!isSubItem) {
                                    isEvenParent = !isEvenParent; // flip only on main items
                                }

                                const rowClass = isEvenParent ? 'bg-stripe bg-opacity-[0.05]' : '';
                                html += `<tr class="${rowClass} ${isSubItem ? 'text-txt-muted text-[11px]' : 'text-txt-primary'} hover:bg-accent-brand/10 transition-colors">`;
                                
                                let isFirst = true;
                                for (const [key, val] of Object.entries(row)) {
                                    const cv = val === null || val === "NaN" || Number.isNaN(val) ? "" : val;
                                    const isLineItem = key === "Line Item" || key === "Unnamed: 0" || isFirst;
                                    
                                    if (isLineItem) {
                                        const tdStyle = isSubItem 
                                            ? `font-weight: 400; padding-left: 2rem; color: #94A3B8; background: ${isEvenParent ? '#243147' : '#1E293B'}; white-space: nowrap;`
                                            : `font-weight: 700; color: #F8FAFC; background: ${isEvenParent ? '#243147' : '#1E293B'}; white-space: nowrap;`;
                                        html += `<td class="line-item" style="${tdStyle}">${cv}</td>`;
                                    } else {
                                        const numStyle = isSubItem ? `color: #94A3B8; font-size: 0.65rem;` : `color: #e2e8f0; font-size: 0.75rem;`;
                                        html += `<td class="num" style="${numStyle}">${cv}</td>`;
                                    }
                                    isFirst = false;
                                }
                                html += '</tr>';
                            }
                            html += '</tbody>';
                        }
                        html += '</table></div></div>';
                    }
                    html += '</div>';
                    container.innerHTML = html;
                    // ── Render screener-based charts ──
                    window._screenerData = data;
                    console.log('[NovusCharts] Screener data received. Tables:', Object.keys(data.tables || {}));
                    console.log('[NovusCharts] Chart.js loaded?', typeof Chart !== 'undefined');
                    console.log('[NovusCharts] NovusCharts engine?', window.NovusCharts);
                    if (window.NovusCharts) {
                        window.NovusCharts.renderFromScreener(data);
                        console.log('[NovusCharts] renderFromScreener() called');
                    } else {
                        console.error('[NovusCharts] ENGINE NOT LOADED — charts will not render');
                    }
                } catch (err) {
                    console.error('Screener error:', err);
                    container.innerHTML = '<p class="text-[10px] font-mono text-semantic-red py-4 uppercase">>_ DATALINK_ERROR</p>';
                }
            }

            // ── Stage mapping ──
            const stageLabels = {
                queued: 'Job Queued in Redis',
                ingest_rag: 'Vector Store Synchronization',
                extract_pdfs: 'Data Extraction & OCR Pipeline',
                fetch_financials: 'Financial Statement Ingestion',
                planning: 'Strategic Analysis Formulation',
                lead_analyst_planning: 'Strategic Analysis Formulation',
                investigation: 'Multi-Agent Investigation',
                reflection: 'Agent Reflection Phase',
                conflict_check: 'Cross-Agent Conflict Check',
                verification: 'Critic Verification',
                forensic_quant: 'Financial Forensics Quant',
                forensic_investigator: 'Deep Forensic Audit',
                narrative_decoder: 'Narrative & Bias Detection',
                moat_architect: 'Economic Moat Architecture',
                capital_allocator: 'Capital Allocation Review',
                management_quality: 'Management & Governance Analysis',
                pm_synthesis: 'Final Synthesis Generation',
                synthesis: 'Final Synthesis Generation',
                assemble: 'Assembling Final Report',
                complete: 'Finalizing Display',
            };
            const allAgents = ['extract_pdfs', 'planning', 'forensic_quant', 'forensic_investigator', 'narrative_decoder', 'moat_architect', 'capital_allocator', 'pm_synthesis'];

            function initAgentDots() {
                agentStatusList.innerHTML = '';
                allAgents.forEach((s, idx) => {
                    const div = document.createElement('div');
                    div.className = 'progress-step';
                    div.id = `step-container-${s}`;
                    
                    const iconBox = document.createElement('div');
                    iconBox.id = `icon-${s}`;
                    iconBox.className = 'step-icon idle';
                    iconBox.textContent = idx + 1;
                    
                    const content = document.createElement('div');
                    content.className = 'step-content';
                    
                    const textSpan = document.createElement('span');
                    textSpan.id = `text-${s}`;
                    textSpan.className = 'step-title';
                    textSpan.textContent = stageLabels[s] || s;

                    content.appendChild(textSpan);
                    div.appendChild(iconBox);
                    div.appendChild(content);
                    agentStatusList.appendChild(div);
                });
            }

            function updateAgentDots(meta) {
                const active = meta.active_agents || [];
                const completed = meta.completed_agents || [];
                allAgents.forEach(s => {
                    const stepContainer = document.getElementById(`step-container-${s}`);
                    const iconBox = document.getElementById(`icon-${s}`);
                    const text = document.getElementById(`text-${s}`);
                    if (!iconBox || !text || !stepContainer) return;
                    
                    if (completed.includes(s)) {
                        stepContainer.classList.add('completed');
                        iconBox.className = 'step-icon completed';
                        iconBox.innerHTML = '<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>';
                        text.className = 'step-title completed';
                    } else if (active.includes(s)) {
                        stepContainer.classList.remove('completed');
                        iconBox.className = 'step-icon active';
                        iconBox.innerHTML = '<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>';
                        text.className = 'step-title active';
                    } else {
                        stepContainer.classList.remove('completed');
                        iconBox.className = 'step-icon idle';
                        iconBox.textContent = allAgents.indexOf(s) + 1;
                        text.className = 'step-title';
                    }
                });
            }

            function completeAllAgentDots() {
                allAgents.forEach(s => {
                    const stepContainer = document.getElementById(`step-container-${s}`);
                    const iconBox = document.getElementById(`icon-${s}`);
                    const text = document.getElementById(`text-${s}`);
                    if (!iconBox || !text || !stepContainer) return;
                    stepContainer.classList.add('completed');
                    iconBox.className = 'step-icon completed';
                    iconBox.innerHTML = '<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>';
                    text.className = 'step-title completed';
                });
            }

            // ── File Upload ──
            dropZone.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', () => handleFiles(fileInput.files));
            dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('border-accent-brand'); });
            dropZone.addEventListener('dragleave', e => { e.preventDefault(); dropZone.classList.remove('border-accent-brand'); });
            dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('border-accent-brand'); fileInput.files = e.dataTransfer.files; handleFiles(fileInput.files); });

            function handleFiles(files) {
                fileList.innerHTML = '';
                const sourcesContainer = document.getElementById('sources-content');
                
                if (!files.length) {
                    if (sourcesContainer) {
                        sourcesContainer.innerHTML = '<p class="text-[10px] font-mono text-txt-dim text-center py-10 uppercase">> NO_DOCUMENTS_INDEXED</p>';
                    }
                    return;
                }
                
                const list = document.createElement('div');
                list.className = 'space-y-[2px] mt-2';
                let sourceStoreHtml = '<div class="space-y-2">';
                
                for (const file of files) {
                    const item = document.createElement('div');
                    item.className = 'flex items-center gap-1.5 text-[9px] bg-base-elevated px-2 py-1 border border-base-border';
                    item.innerHTML = `<span class="text-accent-brand">>_</span><span class="text-txt-secondary truncate flex-1 uppercase">${file.name}</span><span class="text-txt-dim">${(file.size/1024/1024).toFixed(1)}MB</span>`;
                    list.appendChild(item);
                    
                    sourceStoreHtml += `
                        <div class="p-3 border border-base-border bg-base-elevated flex items-center justify-between">
                            <div class="flex items-center gap-2">
                                <span class="text-accent-brand font-mono text-[11px]">>_</span>
                                <span class="font-mono text-[10px] text-txt-primary uppercase">${file.name}</span>
                            </div>
                            <span class="font-mono text-[9px] px-1.5 py-0.5 border border-base-border bg-base-bg text-txt-dim uppercase tracking-widest text-accent-brand">INDEXED</span>
                        </div>
                    `;
                }
                
                sourceStoreHtml += '</div>';
                fileList.appendChild(list);
                if (sourcesContainer) {
                    sourcesContainer.innerHTML = sourceStoreHtml;
                }
            }

            // ── Form Submit ──
            initAgentDots();
            form.addEventListener('submit', async e => {
                e.preventDefault();
                startAnalysis('upload');
                const formData = new FormData();
                formData.append('ticker', tickerInput.value.trim());
                // PDF requirement temporarily disabled
                for (const file of fileInput.files) formData.append('files', file);
                try {
                    const resp = await fetch('/api/v1/generate_report', { method: 'POST', body: formData });
                    if (!resp.ok) { let msg = `Status: ${resp.status}`; try { const e = await resp.json(); msg = e.error || msg; } catch {} throw new Error(msg); }
                    const { job_id } = await resp.json();
                    pollJob(job_id);
                } catch (err) {
                    displayError(err.message || 'Execution Fault.');
                    setLoadingState(false);
                }
            });

            function startAnalysis(mode) {
                setLoadingState(true);
                reportPlaceholder.classList.add('hidden');
                liveTerminal.classList.remove('hidden');
                liveAgentFeed.innerHTML = '';
                populatedCards.clear();
                insightDashboard.classList.add('hidden');
                reportContent.innerHTML = '';
                agentPipeline.classList.remove('hidden');
                initAgentDots();
                hideAllPanels();
                reportLoader.classList.remove('hidden');
                errorMessage.classList.add('hidden');
                exportPdfBtn.classList.add('hidden');
                
                // Destroy existing charts
                if (window.NovusCharts) window.NovusCharts.destroyAll();
                document.getElementById('quant-charts-row')?.classList.add('hidden');
                document.getElementById('forensic-charts-row')?.classList.add('hidden');
                
                // Set loading placeholders for AI charts
                ['chart-dupont', 'chart-cash-quality', 'chart-working-capital'].forEach(id => {
                    const ctx = document.getElementById(id);
                    if (ctx && ctx.parentElement) {
                        ctx.parentElement.innerHTML = `<div class="h-full flex items-center justify-center text-txt-muted text-sm italic font-mono animate-pulse"><canvas id="${id}" class="hidden"></canvas>Analyzing data...</div>`;
                    }
                });

                // Clear previous chart instances
                if (window.NovusCharts && window.NovusCharts.destroyAll) {
                    window.NovusCharts.destroyAll();
                }

                // Clear out quant and forensic data
                document.getElementById('quant-ticker').textContent = tickerInput.value.trim().toUpperCase() || 'TICKER';
                ['health-badge', 'triage-panel', 'forensic-panel'].forEach(id => {
                    const el = document.getElementById(id);
                    if (el) el.classList.add('hidden');
                });
                
                liveCounter.textContent = '0 / 5 Agents Active';
                switchTab('synthesis');
                fetchAndRenderScreener(tickerInput.value.trim());

                // Reset Copilot
                document.getElementById('chat-messages').innerHTML = `
                    <div class="chat-msg assistant">
                        <div class="chat-bubble">
                            <strong>Platform Initialized.</strong><br>
                            Target set to: ${tickerInput.value.trim().toUpperCase()}<br>
                            Awaiting queries...
                        </div>
                    </div>`;
            }

            // ── Polling ──
            async function pollJob(jobId) {
                let attempts = 0;
                const maxAttempts = 600;
                async function check() {
                    attempts++;
                    try {
                        const res = await fetch(`/api/v1/job_status/${jobId}`);
                        if (!res.ok) throw new Error(`Status ${res.status}`);
                        const data = await res.json();
                        if (data.status === 'completed') {
                            const result = data.result || {};
                            if (!result.final_report && data.progress && data.progress.final_report) {
                                result.final_report = data.progress.final_report;
                            }
                            renderReport(result);
                            exportPdfBtn.classList.remove('hidden');
                            exportPdfBtn.classList.add('flex');
                            setLoadingState(false);
                            completeAllAgentDots();
                            return;
                        }
                        if (data.status === 'failed') { displayError('PIPELINE_FAILED'); setLoadingState(false); return; }
                        if (data.status === 'error') { displayError(data.error || 'SYS_ERR'); setLoadingState(false); return; }
                        updateProgress(data.progress || {});
                        if (attempts < maxAttempts) setTimeout(check, 3000);
                        else { displayError('TIMEOUT_ERR'); setLoadingState(false); }
                    } catch (err) {
                        if (attempts < maxAttempts) setTimeout(check, 6000);
                        else { displayError('POLLING_ABORT'); setLoadingState(false); }
                    }
                }
                check();
            }

            function updateProgress(meta) {
                const stage = meta.stage || 'queued';
                const label = stageLabels[stage] || stage;
                loaderStage.textContent = '> ' + label.toUpperCase() + '...';
                updateAgentDots(meta);
                renderPartialReport(meta);
            }

            // ── Live Streaming Feed ──
            const agentLabels = {
                forensic_quant: { name: 'Forensic Quant', type: 'quant', thinkingMsg: 'Calculating ROE, DuPont decomposition, Beneish M-Score...' },
                forensic_investigator: { name: 'Forensic Investigator', type: 'forensic', thinkingMsg: 'Scanning for accounting anomalies & red flags...' },
                narrative_decoder: { name: 'Narrative Decoder', type: 'nlp', thinkingMsg: 'Parsing management commentary for evasion patterns...' },
                moat_architect: { name: 'Moat Architect', type: 'nlp', thinkingMsg: 'Evaluating competitive moat durability & pricing power...' },
                capital_allocator: { name: 'Capital Allocator', type: 'quant', thinkingMsg: 'Reviewing ROIC vs WACC, capital deployment efficiency...' },
                management_quality: { name: 'Management Quality', type: 'nlp', thinkingMsg: 'Evaluating governance, executive compensation, and capital discipline...' },
                pm_synthesis: { name: 'Final Synthesis', type: 'pm', thinkingMsg: 'Synthesizing findings into a final Institutional Report...' },
            };

            // Track which cards have already been populated to avoid re-rendering
            const populatedCards = new Set();

            function getBorderColor(type) {
                return type === 'quant' ? 'border-l-accent-brand' : type === 'forensic' ? 'border-l-semantic-red' : 'border-l-semantic-green';
            }

            function renderThinkingCard(agentName) {
                const cardId = `live-card-${agentName}`;
                if (document.getElementById(cardId)) return; // Already exists
                const info = agentLabels[agentName] || { name: agentName, type: 'other', thinkingMsg: 'Processing...' };
                const borderColor = getBorderColor(info.type);

                const card = document.createElement('div');
                card.id = cardId;
                card.className = `agent-card ws-card border-l-2 ${borderColor}`;
                card.style.opacity = '0';
                card.style.transform = 'translateY(8px)';

                card.innerHTML = `
                    <div class="flex items-center justify-between px-3 py-1.5 bg-base-elevated border-b border-base-border" data-header>
                        <span class="text-xs font-sans font-semibold text-txt-primary">${info.name} Engine</span>
                        <span class="text-[10px] font-mono px-2 py-0.5 rounded border border-accent-brand/30 bg-accent-brandDim text-accent-brandLight status-running" data-badge>RUNNING</span>
                    </div>
                    <div class="agent-card-body" data-body>
                        <div class="px-4 py-3" data-skeleton>
                            <p class="text-[11px] font-mono text-txt-muted mb-3 status-running cursor-blink" id="thinking-text-${agentName}"></p>
                            <div class="space-y-2">
                                <div class="skeleton-line w-full"></div>
                                <div class="skeleton-line"></div>
                                <div class="skeleton-line"></div>
                            </div>
                        </div>
                    </div>
                `;

                liveAgentFeed.appendChild(card);
                
                // Typing effect
                const textEl = card.querySelector(`#thinking-text-${agentName}`);
                if (textEl) {
                    const fullText = `> ${info.thinkingMsg}`;
                    textEl.textContent = '>';
                    let charIdx = 1;
                    const typingInt = setInterval(() => {
                        textEl.textContent += fullText.charAt(charIdx);
                        charIdx++;
                        if (charIdx >= fullText.length) clearInterval(typingInt);
                    }, 30);
                    // Save interval so we could clear it if needed
                    card.dataset.typingInterval = typingInt;
                }

                // Trigger entrance animation
                requestAnimationFrame(() => {
                    card.style.transition = 'opacity 0.35s ease-out, transform 0.35s ease-out';
                    card.style.opacity = '1';
                    card.style.transform = 'translateY(0)';
                });
            }

            function populateCard(agentName, output) {
                if (populatedCards.has(agentName)) return;
                populatedCards.add(agentName);

                const cardId = `live-card-${agentName}`;
                let card = document.getElementById(cardId);
                const info = agentLabels[agentName] || { name: agentName, type: 'other', thinkingMsg: 'Processing...' };
                const borderColor = getBorderColor(info.type);

                // If no thinking card was rendered (agent completed between polls), create fresh
                if (!card) {
                    card = document.createElement('div');
                    card.id = cardId;
                    card.className = `agent-card expanded ws-card border-l-2 ${borderColor}`;
                    card.style.opacity = '0';
                    card.style.transform = 'translateY(8px)';
                    liveAgentFeed.appendChild(card);
                    requestAnimationFrame(() => {
                        card.style.transition = 'opacity 0.35s ease-out, transform 0.35s ease-out';
                        card.style.opacity = '1';
                        card.style.transform = 'translateY(0)';
                    });
                }

                // Update badge to COMPLETED
                const badge = card.querySelector('[data-badge]');
                if (badge) {
                    badge.className = 'text-[10px] font-mono px-2 py-0.5 rounded border border-semantic-green/30 bg-semantic-greenDim text-semantic-green';
                    badge.textContent = 'COMPLETED';
                }

                // Replace body with real content
                const body = card.querySelector('[data-body]');
                const renderedHtml = markdownConverter.makeHtml(output);
                if (body) {
                    if (card.dataset.typingInterval) {
                        clearInterval(card.dataset.typingInterval);
                    }
                    body.innerHTML = `
                        <div class="px-4 py-3 text-sm text-txt-secondary leading-relaxed max-h-[175px] overflow-y-auto report-prose">
                            ${renderedHtml}
                        </div>
                    `;

                    // Apply staggered cascade to list items
                    const items = body.querySelectorAll('li, tr, p');
                    items.forEach((item, i) => {
                        item.classList.add('cascade-item');
                        item.style.animationDelay = `${i * 60}ms`;
                    });
                }

                // Expand via CSS grid transition
                card.classList.add('expanded');
            }

            function renderPartialReport(meta) {
                if (meta.final_report) {
                    liveTerminal.classList.add('hidden');
                    reportLoader.classList.add('hidden');
                    insightDashboard.classList.remove('hidden');
                    reportContent.innerHTML = markdownConverter.makeHtml(meta.final_report);
                    populateForensicTab(meta.agent_outputs, meta.evasion_data);
                    return;
                }

                const activeAgents = meta.active_agents || [];
                const completedAgents = meta.completed_agents || [];
                const agentOutputs = meta.agent_outputs || {};

                // Show the live terminal as soon as any agent is active, or we are past initialization
                const initStages = ['queued', 'ingest_rag', 'extract_pdfs', 'fetch_financials'];
                if (activeAgents.length > 0 || Object.keys(agentOutputs).length > 0 || (meta.stage && !initStages.includes(meta.stage))) {
                    reportLoader.classList.add('hidden');
                    liveTerminal.classList.remove('hidden');
                }

                // Step 1: Render thinking skeletons for active agents that don't have output yet
                for (const agentName of activeAgents) {
                    if (!agentOutputs[agentName] && !populatedCards.has(agentName)) {
                        renderThinkingCard(agentName);
                    }
                }

                // Step 2: Populate completed agents with real content
                for (const [agentName, output] of Object.entries(agentOutputs)) {
                    populateCard(agentName, output);
                }

                // Update counter dynamically
                const total = allAgents.filter(a => a !== 'planning' && a !== 'extract_pdfs').length;
                const agentsDone = completedAgents.filter(a => a !== 'planning' && a !== 'management_quality').length; // exclude management_quality since it's not in the UI pipeline
                liveCounter.textContent = `${agentsDone} / ${total} Agents Completed`;
            }

            function populateForensicTab(agentOutputs, evasionData) {
                if (!agentOutputs) return;
                const container = document.getElementById('forensic-tab-content');
                if (container) container.innerHTML = '';
                
                // ── Real NLP Evasion Score (computed by backend) ──
                const narrativeStr = agentOutputs['narrative_decoder'] || '';
                const evasionLog = document.getElementById('mgmt-dodging-log');
                const evasionScoreText = document.getElementById('evasion-score-text');
                const evasionProgress = document.getElementById('evasion-progress');

                if (evasionLog) {
                    // Use real score from backend if available, else fallback
                    let score = 50;
                    let verdict = '';
                    if (evasionData && typeof evasionData.score === 'number') {
                        score = evasionData.score;
                        verdict = evasionData.verdict || '';
                    }
                    
                    evasionScoreText.textContent = `${score}/100`;
                    evasionProgress.style.width = `${score}%`;
                    evasionProgress.className = `h-2 rounded-full transition-all duration-1000 ${score > 70 ? 'bg-semantic-red' : score > 40 ? 'bg-semantic-amber' : 'bg-semantic-green'}`;
                    
                    // Render narrative content + verdict banner
                    let verdictHtml = '';
                    if (verdict) {
                        const verdictColor = score > 70 ? 'text-semantic-red border-semantic-red/30 bg-semantic-red/10' : score > 40 ? 'text-semantic-amber border-semantic-amber/30 bg-semantic-amber/10' : 'text-semantic-green border-semantic-green/30 bg-semantic-green/10';
                        verdictHtml = `<div class="text-[11px] font-mono px-3 py-2 mb-3 border rounded ${verdictColor}">${verdict}</div>`;
                    }
                    if (evasionData && evasionData.breakdown) {
                        const b = evasionData.breakdown;
                        verdictHtml += `<div class="flex gap-3 mb-3 text-[10px] font-mono text-txt-muted">`;
                        verdictHtml += `<span>Guidance Misses: <span class="text-txt-primary font-semibold">${b.guidance_misses}</span></span>`;
                        verdictHtml += `<span>Tone Shifts: <span class="text-txt-primary font-semibold">${b.tone_shifts}</span></span>`;
                        verdictHtml += `<span>Dodges: <span class="text-txt-primary font-semibold">${b.analyst_dodges}</span></span>`;
                        verdictHtml += `<span>Flagged: <span class="text-txt-primary font-semibold">${b.flagged_phrases}</span></span>`;
                        verdictHtml += `</div>`;
                    }
                    evasionLog.innerHTML = verdictHtml + markdownConverter.makeHtml(narrativeStr);
                }

                // Render remaining agents into the bottom area of forensic tab
                for (const [name, output] of Object.entries(agentOutputs)) {
                    if (name === 'narrative_decoder') continue; // Already handled above
                    const info = agentLabels[name] || { name };
                    const div = document.createElement('div');
                    div.className = 'ws-card overflow-hidden animate-fadeUp p-5';
                    const fullHtml = markdownConverter.makeHtml(output);
                    div.innerHTML = `
                        <h3 class="text-sm font-sans font-semibold text-txt-primary border-b border-base-border pb-3 mb-4 clickable-section-heading" data-modal-title="${info.name}" data-modal-agent="${name}">${info.name}</h3>
                        <div class="text-sm text-txt-secondary leading-relaxed max-h-[300px] overflow-y-auto report-prose pr-2">
                            ${fullHtml}
                        </div>
                    `;
                    // Store full html for modal
                    div.querySelector('h3').addEventListener('click', function() {
                        openSectionModal(info.name, fullHtml);
                    });
                    if (container) container.appendChild(div);
                }
            }

            function renderTriagePanel(triage) {
                const gauge = document.getElementById('health-gauge');
                const badge = document.getElementById('health-badge');
                const alerts = document.getElementById('forensic-alerts');
                const status = document.getElementById('quant-triage-status');

                if (!gauge || gauge.dataset.rendered) return;

                if (triage.passed) {
                    gauge.textContent = 'Operational';
                    badge.textContent = 'LOW RISK';
                    badge.className = 'risk-badge risk-low ml-2';
                    badge.classList.remove('hidden');
                    status.textContent = 'Triage Cleared';
                    status.className = 'text-sm font-semibold font-sans text-semantic-green';
                    alerts.innerHTML = '<p class="text-sm text-semantic-green font-medium flex items-center gap-2"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg> No critical accounting anomalies detected.</p>';
                } else {
                    gauge.textContent = 'Critical Risk';
                    badge.textContent = 'INVESTIGATE';
                    badge.className = 'risk-badge risk-high ml-2';
                    badge.classList.remove('hidden');
                    status.textContent = 'Kill Criteria Met';
                    status.className = 'text-sm font-semibold font-sans text-semantic-red';
                    
                    let html = '';
                    (triage.kill_reasons || []).forEach(r => { 
                        html += `<div class="p-3 bg-semantic-red/10 border border-semantic-red/30 rounded-md mb-2 flex flex-col gap-1"><span class="text-xs font-bold text-semantic-red uppercase tracking-wider">FATAL</span><span class="text-sm text-txt-primary">${r}</span></div>`; 
                    });
                    if (triage.warnings?.length) {
                        triage.warnings.forEach(w => { 
                            html += `<div class="p-3 bg-semantic-amber/10 border border-semantic-amber/30 rounded-md mb-2 flex flex-col gap-1"><span class="text-xs font-bold text-semantic-amber uppercase tracking-wider">WARN</span><span class="text-sm text-txt-primary">${w}</span></div>`; 
                        });
                    }
                    alerts.innerHTML = html;
                }
                gauge.dataset.rendered = 'true';
            }

            function renderForensicPanel(scorecard) {
                if (!scorecard) return;
                
                // Map the actual outputs from ForensicQuantV3
                const ocf_ebitda = scorecard.ocf_ebitda_ratio;
                const eq = ocf_ebitda != null ? (ocf_ebitda >= 0.8 ? 'HIGH' : ocf_ebitda >= 0.5 ? 'MODERATE' : 'LOW') : '--';
                
                const ratingEl = document.getElementById('quant-rating');
                if (ratingEl && !ratingEl.dataset.rendered) {
                    ratingEl.textContent = eq === 'HIGH' ? 'A' : eq === 'MODERATE' ? 'B' : eq === 'LOW' ? 'C' : '--';
                    ratingEl.className = 'text-3xl font-bold font-sans ' + (eq === 'HIGH' ? 'text-semantic-green' : eq === 'LOW' ? 'text-semantic-red' : 'text-semantic-amber');
                    ratingEl.dataset.rendered = 'true';
                }

                const roicEl = document.getElementById('quant-roic');
                if (roicEl && scorecard.roic_latest != null) {
                    if (typeof scorecard.roic_latest === 'number') {
                        roicEl.textContent = (scorecard.roic_latest * 100).toFixed(1) + '%';
                    } else {
                        roicEl.textContent = 'N/A';
                    }
                }
                
                const cccEl = document.getElementById('quant-ccc');
                if (cccEl && scorecard.working_capital && scorecard.working_capital.ccc_days != null) {
                    cccEl.textContent = scorecard.working_capital.ccc_days + 'd';
                    document.getElementById('quant-ccc-sub').textContent = 'Net Cycle';
                }

                const dpText = document.getElementById('quant-dupont-text');
                if (dpText && scorecard.dupont && scorecard.dupont.primary_driver) {
                    dpText.innerHTML = `<span class="italic font-sans">Primary ROE Driver: </span><span class="font-bold text-txt-primary ml-2 uppercase">${scorecard.dupont.primary_driver}</span>`;
                }

                if (scorecard.flags?.length) {
                    // Append flags to forensic alerts if not passed triage
                    const alerts = document.getElementById('forensic-alerts');
                    if (alerts) {
                        let html = alerts.innerHTML;
                        if (html.includes('pending') || html.includes('No critical')) html = '';
                        scorecard.flags.forEach(f => {
                            html += `<div class="p-3 bg-semantic-amber/10 border border-semantic-amber/30 rounded-md mb-2 flex flex-col gap-1"><span class="text-xs font-bold text-semantic-amber uppercase tracking-wider">FLAG</span><span class="text-sm text-txt-primary">${f}</span></div>`;
                        });
                        alerts.innerHTML = html;
                    }
                }
            }

            function hideAllPanels() {
                // Clear state markers
                const gauge = document.getElementById('health-gauge');
                if (gauge) delete gauge.dataset.rendered;
                const ratingEl = document.getElementById('quant-rating');
                if (ratingEl) delete ratingEl.dataset.rendered;
            }

            function setLoadingState(isLoading) {
                generateBtn.disabled = isLoading;
                if (isLoading) { btnText.classList.add('hidden'); btnLoader.classList.remove('hidden'); }
                else {
                    btnText.classList.remove('hidden'); btnLoader.classList.add('hidden'); reportLoader.classList.add('hidden');
                    const ragL = document.getElementById('rag-btn-loader');
                    const ragT = document.getElementById('rag-btn-text');
                    const ragB = document.getElementById('rag-analyze-btn');
                    if (ragL) ragL.classList.add('hidden');
                    if (ragT) ragT.classList.remove('hidden');
                    if (ragB) ragB.disabled = false;
                }
            }

            function displayError(message) {
                reportLoader.classList.add('hidden');
                liveTerminal.classList.add('hidden');
                insightDashboard.classList.add('hidden');
                reportPlaceholder.classList.add('hidden');
                errorMessage.textContent = `> FATAL_ERR: ${message}`;
                errorMessage.classList.remove('hidden');
            }

            function renderSourceStore(stats) {
                const container = document.getElementById('sources-content');
                if (!container || !stats) return;
                
                // If it already has indexed files from the upload handler, we just append or prepend the chunk stats
                let existingHtml = container.innerHTML;
                if (existingHtml.includes('NO_DOCUMENTS_INDEXED')) {
                    existingHtml = '';
                }
                
                const docTypes = stats.doc_types && stats.doc_types.length > 0 
                  ? stats.doc_types.join(', ') 
                  : 'DOCUMENTS';
                  
                const headerHtml = `<div class="mb-4 pb-2 border-b border-base-border flex justify-between items-center">
                    <span class="text-[9px] font-mono text-txt-dim uppercase tracking-widest">> RAG_STORE_STATS</span>
                    <span class="text-[9px] font-mono text-accent-brand uppercase tracking-widest font-bold">${stats.total_chunks || 0} CHUNKS SECURED</span>
                </div>`;
                
                if (existingHtml === '') {
                    // For RAG-only querying where we didn't upload files this session
                    container.innerHTML = headerHtml + `
                        <div class="p-3 border border-base-border bg-base-elevated flex items-center justify-between">
                            <div class="flex flex-col gap-1">
                                <div class="flex items-center gap-2">
                                    <span class="text-accent-brand font-mono text-[11px]">>_</span>
                                    <span class="font-mono text-[10px] text-txt-primary uppercase">CLOUD_STORE_TYPES: ${docTypes}</span>
                                </div>
                            </div>
                            <span class="font-mono text-[9px] px-1.5 py-0.5 border border-base-border bg-base-bg text-txt-dim uppercase tracking-widest text-accent-brand">INDEXED</span>
                        </div>
                    `;
                } else {
                    // We already have files, just prepend the stats
                    container.innerHTML = headerHtml + existingHtml;
                }
            }

            function renderReport(data) {
                window._currentReportData = data;
                reportLoader.classList.add('hidden');
                renderPartialReport(data);
                if (!document.getElementById('report-disclaimer')) {
                    const disc = document.createElement('div');
                    disc.id = 'report-disclaimer';
                    disc.className = "mt-8 text-[10px] font-mono text-txt-dim border-t border-base-border pt-4 px-2 tracking-wide";
                    disc.innerHTML = '<p>> SYSTEM_DISCLAIMER: Generative output via MAS logic. NOT INVESTMENT ADVICE.</p>';
                    reportContent.appendChild(disc);
                }
                // Render additional panels if structured data exists
                if (data.triage_result) renderTriagePanel(data.triage_result);
                if (data.forensic_scorecard) renderForensicPanel(data.forensic_scorecard);
                if (data.rag_stats) renderSourceStore(data.rag_stats);
                if (data.signal_payload) {
                    renderSignalIntelligence(data.signal_payload);
                } else {
                    renderSignalIntelligence({signals: [], impacts: [], unavailable_sources: []});
                }

                // ── Render Charts ──
                if (data.agent_trails && window.NovusCharts) {
                    window.NovusCharts.renderFromAgentData(data.agent_trails);
                }
                // Re-render screener charts if data was already fetched
                if (window._screenerData && window.NovusCharts) {
                    window.NovusCharts.renderFromScreener(window._screenerData);
                }
            }

            window.injectCopilotQuery = function(cleanQuery, structuredContextText) {
                chatHistory.push({ role: 'user', content: "[Context Injected]\n" + structuredContextText });
                chatHistory.push({ role: 'assistant', content: "Context acknowledged." });
                chatInput.value = cleanQuery;
                chatSend.disabled = false;
                chatSend.click();
            };

            window.toggleLowerSignals = function() {
                const hidden = document.querySelectorAll('.signal-subthreshold');
                const btn = document.getElementById('signal-toggle-btn');
                const isShowing = btn.textContent.startsWith('Hide');
                hidden.forEach(el => {
                    if (isShowing) {
                        el.classList.add('hidden');
                    } else {
                        el.classList.remove('hidden');
                    }
                });
                if (isShowing) {
                    btn.innerHTML = `Show lower-materiality signals (<span id="signal-hidden-count">${hidden.length}</span>)`;
                } else {
                    btn.textContent = 'Hide lower-materiality signals';
                }
            };

            function renderSignalIntelligence(payload) {
                const panel = document.getElementById('signal-intel-panel');
                const loading = document.getElementById('signal-loading-state');
                const empty = document.getElementById('signal-empty-state');
                if (!panel || !loading || !empty) return;
                
                loading.classList.add('hidden');
                
                const { signals, impacts, events, unavailable_sources } = payload;
                const threshold = 60;
                
                // Show Empty State if completely empty
                if (!signals || signals.length === 0) {
                    panel.classList.add('hidden');
                    empty.classList.remove('hidden');
                    const date = new Date().toLocaleString('en-US', { timeZone: 'UTC', hour12: false });
                    document.getElementById('signal-empty-text').textContent = `No signals scored as of ${date} UTC.`;
                    return;
                }
                
                // Update badge
                const materialCount = signals.filter(s => s.materiality_score >= threshold).length;
                const badge = document.getElementById('tab-badge-signals');
                if (badge) {
                    if (materialCount > 0) {
                        badge.textContent = materialCount;
                        badge.classList.remove('hidden');
                    } else {
                        badge.classList.add('hidden');
                    }
                }
                
                if (materialCount === 0) {
                    panel.classList.add('hidden');
                    empty.classList.remove('hidden');
                    const date = new Date().toLocaleString('en-US', { timeZone: 'UTC', hour12: false });
                    document.getElementById('signal-empty-text').textContent = `No material signals (>= 60) detected as of ${date} UTC.`;
                    return;
                }
                
                empty.classList.add('hidden');
                panel.classList.remove('hidden');
                
                // Freshness & Unavailable
                const freshness = document.getElementById('signal-freshness');
                if (freshness && signals.length > 0) {
                    const latestDate = new Date(Math.max(...signals.map(s => new Date(s.as_of).getTime())));
                    freshness.textContent = `SYNC: ${latestDate.toLocaleString('en-US', { timeZone: 'UTC', hour12: false })} UTC`;
                }
                
                const unavailEl = document.getElementById('signal-unavailable');
                if (unavailable_sources && unavailable_sources.length > 0) {
                    unavailEl.textContent = `> WARN: Sources unavailable at fetch: ${unavailable_sources.join(', ')}`;
                    unavailEl.classList.remove('hidden');
                } else {
                    unavailEl.classList.add('hidden');
                }
                
                // Sort signals by materiality
                const sortedSignals = [...signals].sort((a, b) => b.materiality_score - a.materiality_score);
                
                // Separate macro vs specific
                const macroSignals = sortedSignals.filter(s => s.category === 'macro_sector');
                const specificSignals = sortedSignals.filter(s => s.category !== 'macro_sector');
                
                const renderSignalCard = (s) => {
                    const isSubthreshold = s.materiality_score < threshold;
                    const displayClass = isSubthreshold ? 'hidden signal-subthreshold' : '';
                    
                    // Match primary event
                    const eventIds = s.event_ids || [];
                    const primaryEventId = eventIds[0];
                    const event = events ? events.find(e => e.id === primaryEventId) : null;
                    const headline = event ? event.raw_title : (s.summary || "Signal Detected");
                    const sourceName = event ? event.source_name : "Unknown Source";
                    const url = event ? event.url : "#";
                    const publishedAt = event ? new Date(event.published_at).toLocaleString() : "";
                    const rawSummary = event ? event.raw_summary : "";
                    
                    const summaryText = s.summary || rawSummary;
                    
                    // Match impact
                    const impact = impacts ? impacts.find(i => i.signal_id === s.id) : null;
                    
                    // Color and icon encoding
                    let colorClass = 'text-semantic-amber bg-semantic-amber/10 border-semantic-amber/30';
                    let iconHtml = `<svg class="w-3 h-3" aria-label="Neutral" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 12H4" /></svg>`;
                    if (s.direction === 'positive') {
                        colorClass = 'text-semantic-green bg-semantic-green/10 border-semantic-green/30';
                        iconHtml = `<svg class="w-3 h-3" aria-label="Positive" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 10l7-7m0 0l7 7m-7-7v18" /></svg>`;
                    } else if (s.direction === 'negative') {
                        colorClass = 'text-semantic-red bg-semantic-red/10 border-semantic-red/30';
                        iconHtml = `<svg class="w-3 h-3" aria-label="Negative" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3" /></svg>`;
                    }
                    
                    const unconfirmedLabel = s.highest_source_tier === 2 ? `<span class="ml-2 text-[9px] px-1 py-0.5 bg-semantic-amber/20 text-semantic-amber rounded uppercase tracking-widest border border-semantic-amber/30">Reported, Not Confirmed</span>` : '';
                    const novelLabel = s.is_novel ? `<span class="ml-2 text-[9px] px-1 py-0.5 bg-accent-brand/20 text-accent-brand rounded uppercase tracking-widest border border-accent-brand/30">NOVEL</span>` : '';
                    
                    // Related events
                    let relatedHtml = '';
                    if (eventIds.length > 1 && events) {
                        const related = eventIds.slice(1).map(eid => events.find(e => e.id === eid)).filter(e => e);
                        if (related.length > 0) {
                            relatedHtml = `
                                <details class="mt-3 text-[11px] font-mono group">
                                    <summary class="cursor-pointer text-txt-muted hover:text-txt-primary flex items-center gap-1 focus:outline-none focus:ring-1 focus:ring-accent-brand rounded w-max">
                                        <svg class="w-3 h-3 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" /></svg>
                                        Related Coverage (${related.length})
                                    </summary>
                                    <div class="mt-2 pl-4 border-l border-base-border space-y-1">
                                        ${related.map(r => `<div class="truncate"><a href="${r.url}" target="_blank" class="text-accent-brand hover:underline focus:outline-none focus:ring-1 focus:ring-accent-brand rounded">${r.source_name}</a> - ${r.raw_title}</div>`).join('')}
                                    </div>
                                </details>
                            `;
                        }
                    }
                    
                    // Impact Block
                    let impactHtml = '';
                    let killBannerHtml = '';
                    if (impact) {
                        let killText = '';
                        if (impact.triggers_kill_criterion_id) {
                            // Resolve KC text
                            let kcText = impact.triggers_kill_criterion_id;
                            if (window._currentReportData && window._currentReportData.final_thesis && window._currentReportData.final_thesis.kill_criteria) {
                                const kcObj = window._currentReportData.final_thesis.kill_criteria.find(k => k.id === impact.triggers_kill_criterion_id);
                                if (kcObj) kcText = kcObj.criterion;
                            }
                            killText = `<div class="mt-2 p-2 bg-semantic-red/10 border border-semantic-red/30 rounded text-[11px] font-sans text-semantic-red flex items-start gap-2">
                                <svg class="w-4 h-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                                <div><strong>KILL CRITERION TRIGGERED:</strong> ${kcText}</div>
                            </div>`;
                            killBannerHtml = killText;
                            
                            // Propagate to cross tabs
                            injectCrossTabKillBanner(s, kcText);
                        }
                        
                        impactHtml = `
                            <div class="mt-3 p-3 bg-base-bg border border-base-border rounded-md text-[11px] font-sans">
                                <div class="flex justify-between items-center mb-1">
                                    <strong class="uppercase text-[10px] tracking-widest text-txt-dim">Thesis Impact Mapping</strong>
                                    <span class="opacity-80">${impact.horizon}</span>
                                </div>
                                <div class="mb-1 text-txt-secondary"><strong>Affects:</strong> ${impact.affected_thesis_drivers.join(', ')}</div>
                                <div class="text-txt-secondary"><strong>Watch:</strong> ${impact.what_to_watch}</div>
                                ${killBannerHtml}
                            </div>
                        `;
                    }
                    
                    // Copilot context packaging
                    const structuredContext = JSON.stringify({ Signal: s, Impact: impact, Event: event }, null, 2);
                    const cleanQuery = `Re: "${headline}" — why is this material and how does it impact the thesis?`;
                    
                    return `
                    <div class="p-4 border rounded-md mb-4 bg-base-elevated border-base-border ${displayClass}">
                        <div class="flex justify-between items-start gap-2 mb-2">
                            <h4 class="text-sm font-sans font-semibold text-txt-primary leading-tight"><a href="${url}" target="_blank" class="hover:text-accent-brand focus:outline-none focus:ring-1 focus:ring-accent-brand rounded">${headline}</a></h4>
                            <div class="flex items-center gap-2 shrink-0">
                                <span class="text-[10px] font-mono opacity-80 border rounded px-1.5 py-0.5 ${colorClass} flex items-center gap-1">${iconHtml} SCORE: ${s.materiality_score}</span>
                            </div>
                        </div>
                        <div class="flex flex-wrap items-center gap-x-2 gap-y-1 mb-3 text-[10px] font-mono text-txt-dim">
                            <span class="uppercase tracking-widest">${s.category}</span> &bull; 
                            <a href="${url}" target="_blank" class="hover:underline focus:outline-none focus:ring-1 focus:ring-accent-brand rounded text-txt-secondary">${sourceName}</a> &bull; 
                            <span>${publishedAt}</span>
                            ${unconfirmedLabel}
                            ${novelLabel}
                        </div>
                        <p class="text-[13px] font-sans text-txt-secondary leading-relaxed">${summaryText}</p>
                        
                        ${impactHtml}
                        ${relatedHtml}
                        
                        <div class="mt-3 flex justify-end">
                            <button class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-accent-brand/10 hover:bg-accent-brand/20 text-accent-brand border border-accent-brand/30 rounded text-[11px] font-mono uppercase tracking-widest transition-colors focus:outline-none focus:ring-2 focus:ring-accent-brand" onclick='window.injectCopilotQuery(${JSON.stringify(cleanQuery).replace(/'/g, "&#39;")}, ${JSON.stringify(structuredContext).replace(/'/g, "&#39;")})'>
                                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
                                Discuss
                            </button>
                        </div>
                    </div>`;
                };
                
                const sigList = document.getElementById('signal-list');
                sigList.innerHTML = specificSignals.map(renderSignalCard).join('');
                
                const macroBand = document.getElementById('signal-macro-band');
                if (macroSignals.length > 0) {
                    macroBand.classList.remove('hidden');
                    document.getElementById('signal-macro-list').innerHTML = macroSignals.map(renderSignalCard).join('');
                } else {
                    macroBand.classList.add('hidden');
                }
                
                // Toggle subthreshold
                const subCount = signals.filter(s => s.materiality_score < threshold).length;
                const toggleBtn = document.getElementById('signal-toggle-btn');
                if (subCount > 0) {
                    toggleBtn.classList.remove('hidden');
                    document.getElementById('signal-hidden-count').textContent = subCount;
                } else {
                    toggleBtn.classList.add('hidden');
                }
            }
            
            function injectCrossTabKillBanner(signal, kcText) {
                const bannerHtml = `
                    <div class="mb-6 p-4 bg-semantic-red/10 border-l-4 border-semantic-red rounded-r-md shadow-sm flex items-start gap-3 cursor-pointer hover:bg-semantic-red/20 transition-colors" onclick="document.querySelector('[data-tab=\\'signals\\']').click()">
                        <svg class="w-5 h-5 text-semantic-red shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                        <div>
                            <h4 class="text-xs font-bold font-sans text-semantic-red uppercase tracking-widest mb-1">Live Kill Criterion Triggered</h4>
                            <p class="text-sm font-sans text-txt-primary"><strong>${kcText}</strong></p>
                            <p class="text-[11px] font-sans text-txt-secondary mt-1">Via Signal ID: ${signal.id}. Click to view full signal intelligence.</p>
                        </div>
                    </div>
                `;
                
                // Insert into Exec Summary
                const reportContent = document.getElementById('report-content');
                if (reportContent && !reportContent.innerHTML.includes('Live Kill Criterion Triggered')) {
                    reportContent.insertAdjacentHTML('afterbegin', bannerHtml);
                }
                
                // Insert into Forensic Tab
                const forensicContent = document.getElementById('forensic-tab-content');
                if (forensicContent && !forensicContent.innerHTML.includes('Live Kill Criterion Triggered')) {
                    forensicContent.insertAdjacentHTML('afterbegin', bannerHtml);
                }
            }

            // ── PDF Export (Backend WeasyPrint) ──
            exportPdfBtn.addEventListener('click', async () => {
                const element = document.getElementById('report-content');
                if (!element || !element.innerHTML.trim()) { showToast('No report content to export.', 'error'); return; }
                const originalText = exportPdfBtn.innerHTML;
                exportPdfBtn.innerHTML = '<div class="w-3 h-3 border border-accent-brand/30 border-t-accent-brand rounded-full animate-spin mr-1.5"></div>GENERATING...';
                exportPdfBtn.disabled = true;
                try {
                    const ticker = (tickerInput.value || 'REPORT').toUpperCase();
                    
                    // Extract all charts as Base64 images
                    const charts = {};
                    const canvases = document.querySelectorAll('canvas');
                    canvases.forEach(canvas => {
                        if (canvas.id && canvas.width > 0 && canvas.height > 0 && canvas.clientWidth > 0 && canvas.clientHeight > 0) {
                            try {
                                // Convert canvas to white background image since PDF is light mode
                                const tempCanvas = document.createElement('canvas');
                                tempCanvas.width = canvas.width;
                                tempCanvas.height = canvas.height;
                                const tempCtx = tempCanvas.getContext('2d');
                                tempCtx.fillStyle = '#FFFFFF';
                                tempCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
                                tempCtx.drawImage(canvas, 0, 0);
                                charts[canvas.id] = tempCanvas.toDataURL('image/png');
                            } catch(e) {
                                console.warn('Could not export canvas', canvas.id, e);
                            }
                        }
                    });

                    const payload = {
                        ticker: ticker,
                        content_html: element.innerHTML,
                        raw_data: window._currentReportData || null,
                        charts: charts
                    };

                    const response = await fetch('/export_pdf', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    
                    if (!response.ok) {
                        const errorMsg = await response.text();
                        throw new Error(errorMsg);
                    }
                    
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `${ticker}_Novus_Analysis.pdf`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                    
                } catch (error) {
                    console.error('PDF Export Error:', error);
                    showToast("Failed to export: " + error.message, 'error');
                } finally {
                    exportPdfBtn.innerHTML = originalText;
                    exportPdfBtn.disabled = false;
                }
            });

            // ── Demo Mode ──
            document.getElementById('demo-mode-btn')?.addEventListener('click', async () => {
                document.getElementById('report-placeholder').classList.add('hidden');
                document.getElementById('report-loader').classList.remove('hidden');
                document.getElementById('loader-stage').textContent = "Loading Demo Data (CIPLA)...";
                
                document.getElementById('ticker').value = 'CIPLA';
                
                const header = document.querySelector('header .flex.items-center.gap-2');
                if (header && !document.getElementById('demo-badge')) {
                    header.insertAdjacentHTML('beforeend', '<span id="demo-badge" class="ml-4 px-2 py-0.5 text-[10px] font-mono font-bold bg-semantic-amber/20 text-semantic-amber border border-semantic-amber/50 rounded-sm tracking-widest">DEMO MODE</span>');
                }
                
                try {
                    const res = await fetch('/static/sample_report.json?v=' + new Date().getTime());
                    const data = await res.json();
                    
                    document.getElementById('report-loader').classList.add('hidden');
                    document.getElementById('insight-dashboard').classList.remove('hidden');
                    
                    renderReport(data);
                    
                    if(data.rag_stats) {
                        renderSourceStore(data.rag_stats);
                    }
                    
                    fetchAndRenderScreener('CIPLA');
                    
                } catch (e) {
                    console.error("Demo Mode Error:", e);
                    document.getElementById('error-message').classList.remove('hidden');
                    document.getElementById('error-message').textContent = "Failed to load sample report.";
                }
            });

            // ── RAG-Only Analysis ──
            const ragBtn = document.getElementById('rag-analyze-btn');
            const ragBtnText = document.getElementById('rag-btn-text');
            const ragBtnLoader = document.getElementById('rag-btn-loader');

            function setRagLoadingState(isLoading) {
                ragBtn.disabled = isLoading;
                generateBtn.disabled = isLoading;
                if (isLoading) { ragBtnText.classList.add('hidden'); ragBtnLoader.classList.remove('hidden'); }
                else { ragBtnText.classList.remove('hidden'); ragBtnLoader.classList.add('hidden'); }
            }

            ragBtn.addEventListener('click', async () => {
                const ticker = tickerInput.value.trim();
                if (!ticker) { showToast('Please enter a target ticker first.', 'error'); return; }
                setRagLoadingState(true);
                startAnalysis('rag');
                try {
                    const resp = await fetch('/api/v1/analyze_rag', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ticker }),
                    });
                    if (!resp.ok) { let msg = `Status: ${resp.status}`; try { const e = await resp.json(); msg = e.error || msg; } catch {} throw new Error(msg); }
                    const { job_id } = await resp.json();
                    pollJob(job_id);
                } catch (err) {
                    displayError(err.message || 'RAG Exception.');
                    setLoadingState(false);
                    setRagLoadingState(false);
                }
            });
        });