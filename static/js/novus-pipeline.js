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

export function injectCrossTabKillBanner(source, kcText) {
    const target = document.getElementById('tab-synthesis');
    if (target) {
        const existing = document.getElementById('cross-tab-kill-banner');
        const banner = existing || document.createElement('div');
        if (!existing) {
            banner.id = 'cross-tab-kill-banner';
            banner.className = 'mb-6 bg-semantic-red/10 border border-semantic-red/30 p-4 animate-fadeUp';
            banner.innerHTML = `<h3 class="text-sm font-bold text-semantic-red flex items-center gap-2 mb-2"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg> CRITICAL RISKS IDENTIFIED</h3><ul id="cross-tab-kill-list" class="space-y-2 text-sm text-txt-primary"></ul>`;
            target.insertBefore(banner, target.firstChild);
        }
        const list = banner.querySelector('#cross-tab-kill-list');
        const li = document.createElement('li');
        li.innerHTML = `<span class="font-mono text-[10px] text-txt-muted mr-2">[${source}]</span> ${kcText}`;
        list.appendChild(li);
    }
}

export function renderSignalIntelligence(payload) {
    const signalsContent = document.getElementById('signals-content');
    if (!signalsContent) return;
    if (!payload || Object.keys(payload).length === 0) {
        signalsContent.innerHTML = '<p class="text-sm text-txt-muted italic px-4 py-8 text-center">No signal intelligence available.</p>';
        return;
    }
    
    let html = '<div class="space-y-6">';
    for (const [agentName, signalStr] of Object.entries(payload)) {
        if (!signalStr) continue;
        let signalData = null;
        try { signalData = JSON.parse(signalStr); } catch (e) { continue; }
        if (!signalData) continue;
        
        const info = agentLabels[agentName] || { name: agentName };
        
        html += `
            <div class="ws-card p-5 border-l-2 border-l-semantic-blue">
                <h3 class="text-sm font-sans font-semibold text-txt-primary mb-4 flex items-center gap-2">
                    <svg class="w-4 h-4 text-semantic-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                    ${info.name} Signals
                </h3>
        `;
        
        if (signalData.kill_criteria_met && signalData.kill_criteria_met.length > 0) {
            html += `<div class="mb-5 space-y-2">`;
            signalData.kill_criteria_met.forEach(kc => {
                html += `
                    <div class="bg-semantic-red/10 border border-semantic-red/30 p-3 flex gap-3">
                        <span class="text-[10px] font-bold font-mono text-semantic-red mt-0.5">FATAL</span>
                        <span class="text-sm text-txt-primary">${kc}</span>
                    </div>
                `;
                injectCrossTabKillBanner(info.name, kc);
            });
            html += `</div>`;
        }
        
        if (signalData.core_findings && signalData.core_findings.length > 0) {
            html += `<h4 class="text-[10px] uppercase font-mono text-txt-muted mb-2 tracking-widest">Key Findings</h4><ul class="space-y-2 mb-5">`;
            signalData.core_findings.forEach(cf => {
                html += `<li class="flex gap-2 text-sm text-txt-secondary"><span class="text-accent-brand mt-0.5">•</span> <span>${cf}</span></li>`;
            });
            html += `</ul>`;
        }
        
        if (signalData.evidence_links && signalData.evidence_links.length > 0) {
            html += `<h4 class="text-[10px] uppercase font-mono text-txt-muted mb-2 tracking-widest">Evidence</h4><div class="space-y-2">`;
            signalData.evidence_links.forEach(ev => {
                const snippet = markdownConverter.makeHtml(ev.snippet).replace(/<p>/g, '').replace(/<\/p>/g, '');
                html += `
                    <div class="bg-base-elevated border border-base-border p-3 text-xs text-txt-secondary">
                        <div class="mb-1 font-mono text-[10px] text-accent-brand">${ev.source}</div>
                        <div class="italic">"${snippet}"</div>
                    </div>
                `;
            });
            html += `</div>`;
        }
        
        html += `</div>`;
    }
    html += '</div>';
    signalsContent.innerHTML = html;
}
