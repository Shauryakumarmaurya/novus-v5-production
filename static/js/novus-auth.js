// Novus API auth shim.
// Attaches X-API-Key to all same-origin API calls. The key is sourced from
// (1) a ?api_key= URL param (persisted to localStorage, then stripped), or
// (2) localStorage 'novus_api_key'. On a 401 the user is prompted once.
(() => {
    const STORAGE_KEY = 'novus_api_key';

    try {
        const params = new URLSearchParams(window.location.search);
        const fromUrl = params.get('api_key');
        if (fromUrl) {
            localStorage.setItem(STORAGE_KEY, fromUrl);
            params.delete('api_key');
            const qs = params.toString();
            window.history.replaceState({}, '', window.location.pathname + (qs ? '?' + qs : ''));
        }
    } catch (e) { /* localStorage unavailable — fall through unauthenticated */ }

    const getKey = () => {
        try { return localStorage.getItem(STORAGE_KEY) || ''; } catch (e) { return ''; }
    };

    const isApiPath = (url) => {
        if (typeof url !== 'string') return false;
        // Same-origin relative API paths only; never leak the key cross-origin.
        return url.startsWith('/api/') || url.startsWith('/export_pdf')
            || url.startsWith('/ingest_local') || url.startsWith('/rag_stats')
            || url.startsWith('/list_local_pdfs');
    };

    const origFetch = window.fetch.bind(window);
    let promptedOnce = false;

    window.fetch = async (input, init = {}) => {
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        if (isApiPath(url)) {
            const key = getKey();
            if (key) {
                init = { ...init, headers: { ...(init.headers || {}), 'X-API-Key': key } };
            }
        }
        const resp = await origFetch(input, init);
        if (resp.status === 401 && isApiPath(url) && !promptedOnce) {
            promptedOnce = true;
            const entered = window.prompt('This Novus server requires an API key. Enter your X-API-Key:');
            if (entered) {
                try { localStorage.setItem(STORAGE_KEY, entered.trim()); } catch (e) { /* ignore */ }
                promptedOnce = false;
                return window.fetch(input, init);
            }
        }
        return resp;
    };
})();
