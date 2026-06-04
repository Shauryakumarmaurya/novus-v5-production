import {
    tickerSelect, form, tickerInput, fileInput, dropZone, fileList,
    reportPlaceholder, reportContent, reportLoader, loaderStage, errorMessage, exportPdfBtn,
    agentPipeline, liveTerminal, liveAgentFeed, liveCounter, insightDashboard,
    markdownConverter, setLoadingState, displayError, hideAllPanels, switchTab, TICKER_DATA
} from './novus-core.js';

import {
    initAgentDots, completeAllAgentDots, updateProgress, populateForensicTab,
    renderTriagePanel, renderForensicPanel, renderSourceStore, renderSignalIntelligence
} from './novus-pipeline.js';

// Expose these for inline HTML handlers
window.switchTab = switchTab;

// ── Initialize Ticker Dropdown ──
if (tickerSelect) {
    Object.keys(TICKER_DATA).forEach(groupLabel => {
        const optgroup = document.createElement('optgroup');
        optgroup.label = groupLabel;
        TICKER_DATA[groupLabel].forEach(ticker => {
            const option = document.createElement('option');
            option.value = ticker.value;
            option.textContent = ticker.name;
            optgroup.appendChild(option);
        });
        tickerSelect.appendChild(optgroup);
    });
}

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
                        isEvenParent = !isEvenParent; 
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
        window._screenerData = data;
        
        if (window.NovusCharts) {
            window.NovusCharts.renderFromScreener(data);
        } else {
            console.error('[NovusCharts] ENGINE NOT LOADED — charts will not render');
        }
    } catch (err) {
        console.error('Screener error:', err);
        container.innerHTML = '<p class="text-[10px] font-mono text-semantic-red py-4 uppercase">>_ DATALINK_ERROR</p>';
    }
}

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

function startAnalysis(mode) {
    setLoadingState(true);
    reportPlaceholder.classList.add('hidden');
    liveTerminal.classList.remove('hidden');
    liveAgentFeed.innerHTML = '';
    
    // populatedCards from novus-pipeline
    import('./novus-pipeline.js').then(({ populatedCards }) => {
        populatedCards.clear();
    });

    insightDashboard.classList.add('hidden');
    reportContent.innerHTML = '';
    agentPipeline.classList.remove('hidden');
    initAgentDots();
    hideAllPanels();
    reportLoader.classList.remove('hidden');
    errorMessage.classList.add('hidden');
    exportPdfBtn.classList.add('hidden');
    
    if (window.NovusCharts) window.NovusCharts.destroyAll();
    const qCR = document.getElementById('quant-charts-row');
    if (qCR) qCR.classList.add('hidden');
    const fCR = document.getElementById('forensic-charts-row');
    if (fCR) fCR.classList.add('hidden');
    
    ['chart-dupont', 'chart-cash-quality', 'chart-working-capital'].forEach(id => {
        const ctx = document.getElementById(id);
        if (ctx && ctx.parentElement) {
            ctx.parentElement.innerHTML = `<div class="h-full flex items-center justify-center text-txt-muted text-sm italic font-mono animate-pulse"><canvas id="${id}" class="hidden"></canvas>Analyzing data...</div>`;
        }
    });

    if (window.NovusCharts && window.NovusCharts.destroyAll) {
        window.NovusCharts.destroyAll();
    }

    const qt = document.getElementById('quant-ticker');
    if (qt) qt.textContent = tickerInput.value.trim().toUpperCase() || 'TICKER';
    ['health-badge', 'triage-panel', 'forensic-panel'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
    
    liveCounter.textContent = '0 / 5 Agents Active';
    switchTab('synthesis');
    fetchAndRenderScreener(tickerInput.value.trim());

    const cm = document.getElementById('chat-messages');
    if (cm) {
        cm.innerHTML = `
            <div class="chat-msg assistant">
                <div class="chat-bubble">
                    <strong>Platform Initialized.</strong><br>
                    Target set to: ${tickerInput.value.trim().toUpperCase()}<br>
                    Awaiting queries...
                </div>
            </div>`;
    }
}

// Expose globally for RAG and Copilot buttons
window.startAnalysis = startAnalysis;

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

window.pollJob = pollJob; // Expose globally for RAG

function renderReport(data) {
    if (!data) return;
    
    if (data.final_report) {
        reportContent.innerHTML = markdownConverter.makeHtml(data.final_report);
    } else if (data.synthesis) {
        reportContent.innerHTML = markdownConverter.makeHtml(data.synthesis);
    } else {
        reportContent.innerHTML = '<p class="text-txt-muted italic">No synthesis report generated.</p>';
    }
    
    let quantProcessed = false;
    
    if (data.agent_outputs) {
        populateForensicTab(data.agent_outputs, data.evasion_data);
        
        if (data.agent_outputs.forensic_quant) {
            try {
                const quantData = JSON.parse(data.agent_outputs.forensic_quant);
                if (quantData && quantData.scorecard) {
                    document.getElementById('triage-panel').classList.remove('hidden');
                    document.getElementById('health-badge').classList.remove('hidden');
                    document.getElementById('forensic-panel').classList.remove('hidden');
                    renderTriagePanel(quantData.scorecard.triage);
                    renderForensicPanel(quantData.scorecard);
                    
                    if (window.NovusCharts && window.NovusCharts.renderAICharts) {
                        window.NovusCharts.renderAICharts(quantData.scorecard);
                    }
                    quantProcessed = true;
                }
            } catch (e) {
                console.error('Failed to parse forensic_quant output:', e);
            }
        }
    }
    
    if (!quantProcessed && tickerInput.value) {
        const t = tickerInput.value.trim();
        if (t) fetchAndRenderScreener(t);
    }
    
    if (data.vector_store_stats) {
        renderSourceStore(data.vector_store_stats);
    }

    if (data.signal_intelligence) {
        renderSignalIntelligence(data.signal_intelligence);
    }
}

// ── DOM Wiring ──
dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => handleFiles(fileInput.files));
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('border-accent-brand'); });
dropZone.addEventListener('dragleave', e => { e.preventDefault(); dropZone.classList.remove('border-accent-brand'); });
dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('border-accent-brand'); fileInput.files = e.dataTransfer.files; handleFiles(fileInput.files); });

initAgentDots();

form.addEventListener('submit', async e => {
    e.preventDefault();
    startAnalysis('upload');
    const formData = new FormData();
    formData.append('ticker', tickerInput.value.trim());
    if (!fileInput.files.length) { displayError("NO_PDFS_INDEXED"); setLoadingState(false); return; }
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

const modalBackdrop = document.getElementById('section-modal-backdrop');
const modalTitle = document.getElementById('section-modal-title-text');
const modalBody = document.getElementById('section-modal-body');
const modalCloseBtn = document.getElementById('section-modal-close-btn');

window.openSectionModal = function(title, contentHtml) {
    modalTitle.textContent = title;
    modalBody.innerHTML = contentHtml;
    modalBackdrop.classList.add('visible');
    document.body.style.overflow = 'hidden';
};

function closeModal() {
    modalBackdrop.classList.remove('visible');
    document.body.style.overflow = '';
}

modalCloseBtn.addEventListener('click', closeModal);
modalBackdrop.addEventListener('click', (e) => {
    if (e.target === modalBackdrop) closeModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalBackdrop.classList.contains('visible')) closeModal();
});

// ── RAG UI Bindings ──
import { ragBtn, setRagLoadingState } from './novus-core.js';

if (ragBtn) {
    ragBtn.addEventListener('click', async () => {
        const ticker = tickerInput.value.trim();
        if (!ticker) { alert('ENTER_TICKER'); return; }
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
}