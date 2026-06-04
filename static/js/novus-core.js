// novus-core.js
// Shared State, API calls, DOM elements

export const TICKER_DATA = {
    "📊 Pharma Portfolio": [
        { value: "ALEMBICLTD", name: "Alembic Limited" },
        { value: "AUROPHARMA", name: "Aurobindo Pharma" },
        { value: "CIPLA", name: "Cipla" },
        { value: "DIVISLAB", name: "Divi's Laboratories" },
        { value: "DRREDDY", name: "Dr. Reddy's Laboratories" },
        { value: "GRANULES", name: "Granules India" },
        { value: "LAURUSLABS", name: "Laurus Labs" },
        { value: "LUPIN", name: "Lupin" },
        { value: "SUNPHARMA", name: "Sun Pharmaceuticals" },
        { value: "ZYDUSLIFE", name: "Zydus Lifesciences" }
    ],
    "🛒 FMCG": [
        { value: "HINDUNILVR", name: "Hindustan Unilever" }
    ]
};

export const tickerSelect = document.getElementById('ticker');
export const form = document.getElementById('report-form');
export const tickerInput = document.getElementById('ticker');
export const fileInput = document.getElementById('files');
export const dropZone = document.getElementById('drop-zone');
export const fileList = document.getElementById('file-list');
export const generateBtn = document.getElementById('generate-btn');
export const btnText = document.getElementById('btn-text');
export const btnLoader = document.getElementById('btn-loader');
export const reportPlaceholder = document.getElementById('report-placeholder');
export const reportContent = document.getElementById('report-content');
export const reportLoader = document.getElementById('report-loader');
export const loaderStage = document.getElementById('loader-stage');
export const errorMessage = document.getElementById('error-message');
export const exportPdfBtn = document.getElementById('export-pdf-btn');
export const agentPipeline = document.getElementById('agent-pipeline');
export const agentStatusList = document.getElementById('agent-status-list');
export const liveTerminal = document.getElementById('live-terminal');
export const liveAgentFeed = document.getElementById('live-agent-feed');
export const liveCounter = document.getElementById('live-counter');
export const insightDashboard = document.getElementById('insight-dashboard');
export const insightCards = document.getElementById('insight-cards');
export const ragBtn = document.getElementById('rag-analyze-btn');
export const ragBtnText = document.getElementById('rag-btn-text');
export const ragBtnLoader = document.getElementById('rag-btn-loader');

export const terminalStylesExt = {
    type: 'output',
    filter: function (text) {
        return text.replace(/\[([^<>\]\n]{1,50})\]/g, '<span class="calc-badge">[$1]</span>');
    }
};

export const semanticExt = {
    type: 'output',
    filter: function (text) {
        text = text.replace(/\[DATA WARNING\]/g, '<span class="bg-semantic-amber/20 text-semantic-amber px-1.5 py-[1px] rounded-sm border border-semantic-amber/50 font-bold text-[10px] tracking-wide font-mono inline-block mb-1">[DATA WARNING]</span>');
        text = text.replace(/DATA UNAVAILABLE/g, '<span class="bg-semantic-amber/20 text-semantic-amber px-1.5 py-[1px] rounded-sm border border-semantic-amber/50 font-bold text-[10px] tracking-wide font-mono inline-block">DATA UNAVAILABLE</span>');
        text = text.replace(/(gaining|up|increase[d]?|growth|positive|gains?)\s+([^\s<]*?(?:\d+\.?\d*[%x]?|bps|mn|cr))\b/gi, '$1 <span class="text-semantic-green font-mono font-bold">$2</span>');
        text = text.replace(/(declining|down|decrease[d]?|negative|drop|loss|falling|contraction)\s+([^\s<]*?(?:\d+\.?\d*[%x]?|bps|mn|cr))\b/gi, '$1 <span class="text-semantic-red font-mono font-bold">$2</span>');
        return text;
    }
};

export const markdownConverter = new showdown.Converter({ extensions: [terminalStylesExt, semanticExt], tables: true });
window.terminalStylesExt = terminalStylesExt;
window.semanticExt = semanticExt;

export function setLoadingState(isLoading) {
    generateBtn.disabled = isLoading;
    if (isLoading) { btnText.classList.add('hidden'); btnLoader.classList.remove('hidden'); }
    else {
        btnText.classList.remove('hidden'); btnLoader.classList.add('hidden'); reportLoader.classList.add('hidden');
        if (ragBtnLoader) ragBtnLoader.classList.add('hidden');
        if (ragBtnText) ragBtnText.classList.remove('hidden');
        if (ragBtn) ragBtn.disabled = false;
    }
}

export function displayError(message) {
    reportLoader.classList.add('hidden');
    liveTerminal.classList.add('hidden');
    insightDashboard.classList.add('hidden');
    reportPlaceholder.classList.add('hidden');
    errorMessage.textContent = `> FATAL_ERR: ${message}`;
    errorMessage.classList.remove('hidden');
}

export function hideAllPanels() {
    const gauge = document.getElementById('health-gauge');
    if (gauge) delete gauge.dataset.rendered;
    const ratingEl = document.getElementById('quant-rating');
    if (ratingEl) delete ratingEl.dataset.rendered;
}

export function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('active'); if (b.dataset.tab === tabName) b.classList.add('active'); });
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(`tab-${tabName}`).classList.add('active');
}

export function setRagLoadingState(isLoading) {
    if (!ragBtn) return;
    ragBtn.disabled = isLoading;
    if (isLoading) { ragBtnText.classList.add('hidden'); ragBtnLoader.classList.remove('hidden'); }
    else { ragBtnText.classList.remove('hidden'); ragBtnLoader.classList.add('hidden'); }
}
