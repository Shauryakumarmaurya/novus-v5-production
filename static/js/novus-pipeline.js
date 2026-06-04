import { 
    agentStatusList, loaderStage, liveCounter, liveAgentFeed, liveTerminal, 
    insightDashboard, reportContent, reportLoader, markdownConverter, 
    errorMessage, hideAllPanels 
} from './novus-core.js';

export const stageLabels = {
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
    synthesis: 'Final Synthesis Generation',
    assemble: 'Assembling Final Report',
    complete: 'Finalizing Display',
};

export const allAgents = ['extract_pdfs', 'planning', 'forensic_quant', 'forensic_investigator', 'narrative_decoder', 'moat_architect', 'capital_allocator', 'synthesis'];

export const agentLabels = {
    forensic_quant: { name: 'Forensic Quant', type: 'quant', thinkingMsg: 'Calculating ROE, DuPont decomposition, Beneish M-Score...' },
    forensic_investigator: { name: 'Forensic Investigator', type: 'forensic', thinkingMsg: 'Scanning for accounting anomalies & red flags...' },
    narrative_decoder: { name: 'Narrative Decoder', type: 'nlp', thinkingMsg: 'Parsing management commentary for evasion patterns...' },
    moat_architect: { name: 'Moat Architect', type: 'nlp', thinkingMsg: 'Evaluating competitive moat durability & pricing power...' },
    capital_allocator: { name: 'Capital Allocator', type: 'quant', thinkingMsg: 'Reviewing ROIC vs WACC, capital deployment efficiency...' },
};

export const populatedCards = new Set();

export function initAgentDots() {
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

export function updateAgentDots(meta) {
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

export function completeAllAgentDots() {
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

export function updateProgress(meta) {
    const stage = meta.stage || 'queued';
    const label = stageLabels[stage] || stage;
    loaderStage.textContent = '> ' + label.toUpperCase() + '...';
    updateAgentDots(meta);
    renderPartialReport(meta);
}

export function getBorderColor(type) {
    return type === 'quant' ? 'border-l-accent-brand' : type === 'forensic' ? 'border-l-semantic-red' : 'border-l-semantic-green';
}

export function renderThinkingCard(agentName) {
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
                <p class="text-[11px] font-mono text-txt-muted mb-3 status-running">> ${info.thinkingMsg}</p>
                <div class="space-y-2">
                    <div class="skeleton-line w-full"></div>
                    <div class="skeleton-line"></div>
                    <div class="skeleton-line"></div>
                </div>
            </div>
        </div>
    `;

    liveAgentFeed.appendChild(card);

    // Trigger entrance animation
    requestAnimationFrame(() => {
        card.style.transition = 'opacity 0.35s ease-out, transform 0.35s ease-out';
        card.style.opacity = '1';
        card.style.transform = 'translateY(0)';
    });
}

export function populateCard(agentName, output) {
    if (populatedCards.has(agentName)) return;
    populatedCards.add(agentName);

    const cardId = `live-card-${agentName}`;
    let card = document.getElementById(cardId);
    const info = agentLabels[agentName] || { name: agentName, type: 'other', thinkingMsg: 'Processing...' };
    const borderColor = getBorderColor(info.type);

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

    const badge = card.querySelector('[data-badge]');
    if (badge) {
        badge.className = 'text-[10px] font-mono px-2 py-0.5 rounded border border-semantic-green/30 bg-semantic-greenDim text-semantic-green';
        badge.textContent = 'COMPLETED';
    }

    const body = card.querySelector('[data-body]');
    const renderedHtml = markdownConverter.makeHtml(output);
    if (body) {
        body.innerHTML = `
            <div class="px-4 py-3 text-sm text-txt-secondary leading-relaxed max-h-[175px] overflow-y-auto report-prose">
                ${renderedHtml}
            </div>
        `;

        const items = body.querySelectorAll('li, tr, p');
        items.forEach((item, i) => {
            item.classList.add('cascade-item');
            item.style.animationDelay = `${i * 60}ms`;
        });
    }

    card.classList.add('expanded');
}

export function populateForensicTab(agentOutputs, evasionData) {
    if (!agentOutputs) return;
    const container = document.getElementById('forensic-tab-content');
    if (container) container.innerHTML = '';
    
    const narrativeStr = agentOutputs['narrative_decoder'] || '';
    const evasionLog = document.getElementById('mgmt-dodging-log');
    const evasionScoreText = document.getElementById('evasion-score-text');
    const evasionProgress = document.getElementById('evasion-progress');

    if (evasionLog) {
        let score = 50;
        let verdict = '';
        if (evasionData && typeof evasionData.score === 'number') {
            score = evasionData.score;
            verdict = evasionData.verdict || '';
        }
        
        evasionScoreText.textContent = `${score}/100`;
        evasionProgress.style.width = `${score}%`;
        evasionProgress.className = `h-2 rounded-full transition-all duration-1000 ${score > 70 ? 'bg-semantic-red' : score > 40 ? 'bg-semantic-amber' : 'bg-semantic-green'}`;
        
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

    for (const [name, output] of Object.entries(agentOutputs)) {
        if (name === 'narrative_decoder') continue; 
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
        div.querySelector('h3').addEventListener('click', function() {
            window.openSectionModal(info.name, fullHtml);
        });
        if (container) container.appendChild(div);
    }
}

export function renderPartialReport(meta) {
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

    const initStages = ['queued', 'ingest_rag', 'extract_pdfs', 'fetch_financials'];
    if (activeAgents.length > 0 || Object.keys(agentOutputs).length > 0 || (meta.stage && !initStages.includes(meta.stage))) {
        reportLoader.classList.add('hidden');
        liveTerminal.classList.remove('hidden');
    }

    for (const agentName of activeAgents) {
        if (!agentOutputs[agentName] && !populatedCards.has(agentName)) {
            renderThinkingCard(agentName);
        }
    }

    for (const [agentName, output] of Object.entries(agentOutputs)) {
        populateCard(agentName, output);
    }

    const total = 5;
    const agentsDone = completedAgents.filter(a => a !== 'planning').length;
    liveCounter.textContent = `${agentsDone} / ${total} Agents Completed`;
}

export function renderTriagePanel(triage) {
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

export function renderForensicPanel(scorecard) {
    if (!scorecard) return;
    
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

export function renderSourceStore(stats) {
    const container = document.getElementById('sources-content');
    if (!container || !stats) return;
    
    const docCount = stats.document_count || 0;
    const chunkCount = stats.chunk_count || 0;
    const collName = stats.collection_name || 'unknown';
    
    let statsHtml = `
        <div class="mt-4 pt-4 border-t border-base-border">
            <div class="grid grid-cols-2 gap-4">
                <div class="p-3 bg-base-bg border border-base-border">
                    <p class="text-[9px] font-mono text-txt-muted uppercase mb-1">Vector Store</p>
                    <p class="text-sm font-semibold font-sans text-txt-primary">${collName}</p>
                </div>
                <div class="p-3 bg-base-bg border border-base-border">
                    <p class="text-[9px] font-mono text-txt-muted uppercase mb-1">Knowledge Chunks</p>
                    <p class="text-sm font-semibold font-sans text-txt-primary">${chunkCount}</p>
                </div>
            </div>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', statsHtml);
}

export function renderSignalIntelligence(payload) {
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

export function injectCrossTabKillBanner(signal, kcText) {
                const bannerHtml = `
                    <div class="mb-6 p-4 bg-semantic-red/10 border-l-4 border-semantic-red rounded-r-md shadow-sm flex items-start gap-3 cursor-pointer hover:bg-semantic-red/20 transition-colors" onclick="document.querySelector('[data-tab=\'signals\']').click()">
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