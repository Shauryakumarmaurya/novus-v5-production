(() => {
        const chatInput = document.getElementById('chat-input');
        const chatSend = document.getElementById('chat-send');
        const chatMessages = document.getElementById('chat-messages');
        const chatClear = document.getElementById('chat-clear');
        const mainTickerInput = document.getElementById('ticker');
        const copilotLogsExt = {
            type: 'lang',
            filter: function(text) {
                // Wrap lines starting with [Copilot] or [Tool] in a styled log block
                return text.replace(/^(\[(Copilot|Tool)\][^\n]*)$/gm, function(match) {
                    // By wrapping in backticks, we ensure showdown treats it as code 
                    // and doesn't parse special markdown characters inside the JSON payload.
                    return `\n<div class="font-mono text-[10px] text-txt-muted border-l-2 border-accent-brand/30 pl-2 py-0.5 my-0.5 bg-black/20 break-all rounded-r-sm">\n\`${match}\`\n</div>\n`;
                });
            }
        };

        const mdOptions = { 
            tables: true, 
            literalMidWordUnderscores: true,
            simplifiedAutoLink: true,
            strikethrough: true
        };
        
        if (window.terminalStylesExt) {
            mdOptions.extensions = [window.terminalStylesExt, window.semanticExt, copilotLogsExt];
        } else {
            mdOptions.extensions = [copilotLogsExt];
        }
        
        const md = new showdown.Converter(mdOptions);

        let chatHistory = [];
        let isSending = false;

        chatInput.addEventListener('input', () => { chatSend.disabled = !chatInput.value.trim() || isSending; });
        chatInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !chatSend.disabled) sendMessage(); });
        chatSend.addEventListener('click', sendMessage);

        // Initial Chat State
        chatMessages.innerHTML = `
            <div class="chat-msg assistant">
                <div class="chat-bubble">
                    <strong>Welcome to Novus FinLLM</strong><br>
                    Please configure parameters via the command center to begin.
                </div>
            </div>`;

        chatClear.addEventListener('click', () => {
            chatHistory = [];
            const t = mainTickerInput.value.trim().toUpperCase() || 'Pending Target';
            chatMessages.innerHTML = `
                <div class="chat-msg assistant">
                    <div class="chat-bubble">
                        <strong>Chat Cleared.</strong><br>
                        Current Target: ${t}<br>
                        Awaiting new queries...
                    </div>
                </div>`;
        });

        async function sendMessage() {
            const ticker = mainTickerInput.value.trim().toUpperCase();
            const question = chatInput.value.trim();
            if (!ticker) { alert('NO_TICKER_DEFINED'); return; }
            if (!question || isSending) return;
            isSending = true; chatSend.disabled = true; chatInput.value = '';
            appendMessage('user', question);
            chatHistory.push({ role: 'user', content: question });

            const typingEl = document.createElement('div');
            typingEl.className = 'chat-msg assistant'; typingEl.id = 'typing-indicator';
            typingEl.innerHTML = `<div class="chat-bubble"><div class="typing-indicator"><span></span><span></span><span></span></div></div>`;
            chatMessages.appendChild(typingEl);
            chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: 'smooth' });

            try {
                const resp = await fetch('/api/v1/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker, question, history: chatHistory.slice(0, -1) }) });
                const ti = document.getElementById('typing-indicator'); if (ti) ti.remove();
                if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.error || `Status ${resp.status}`); }
                
                // Immediately create the DOM element for streamed content
                const msgDiv = document.createElement('div');
                msgDiv.className = `chat-msg assistant`;
                const bubble = document.createElement('div');
                bubble.className = 'chat-bubble report-prose !text-[13px]';
                msgDiv.appendChild(bubble);
                chatMessages.appendChild(msgDiv);

                const reader = resp.body.getReader();
                const decoder = new TextDecoder("utf-8");
                let fullAnswer = "";
                let sourcesData = null;
                let sseBuffer = "";
                let lastRender = 0;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    sseBuffer += decoder.decode(value, { stream: true });
                    
                    let newlineIdx;
                    while ((newlineIdx = sseBuffer.indexOf('\n\n')) >= 0) {
                        const eventStr = sseBuffer.slice(0, newlineIdx).trim();
                        sseBuffer = sseBuffer.slice(newlineIdx + 2);
                        
                        if (eventStr === 'data: [DONE]') continue;
                        if (eventStr.startsWith('data: ')) {
                            const dataStr = eventStr.slice(6);
                            if (dataStr) {
                                try {
                                    const parsed = JSON.parse(dataStr);
                                    if (parsed.type === 'meta') {
                                        sourcesData = parsed.sources;
                                    } else if (parsed.type === 'error') {
                                        throw new Error(parsed.text);
                                    } else if (parsed.type === 'clear') {
                                        fullAnswer = '';
                                        bubble.innerHTML = '';
                                    } else if (parsed.type === 'content') {
                                        fullAnswer += parsed.text;
                                    }
                                } catch (e) {
                                    console.error("SSE JSON Parse Error:", e, dataStr);
                                }
                            }
                        }
                    }

                    // Throttle markdown parsing (max 10 fps) to avoid blocking main thread parsing huge payloads
                    const now = Date.now();
                    if (now - lastRender > 100) {
                        bubble.innerHTML = md.makeHtml(fullAnswer);
                        lastRender = now;
                        
                        // Smart Scrolling: Only auto-scroll if the user is already at the bottom
                        const isAtBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight < 50;
                        if (isAtBottom) {
                            chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: 'instant' });
                        }
                    }
                }

                // Final render
                bubble.innerHTML = md.makeHtml(fullAnswer);
                
                // Append Sources if present
                if (sourcesData && sourcesData.length > 0) {
                    const srcDiv = document.createElement('div');
                    srcDiv.className = 'chat-sources mt-3 border-t border-base-border pt-2';
                    srcDiv.innerHTML = '> SOURCES: ' + sourcesData.map(s => `<span class="text-accent-brand text-xs mr-2 cursor-help" title="${s.section || ''}">${s.filename} <span class="text-txt-dim">(${s.doc_type})</span></span>`).join('');
                    msgDiv.appendChild(srcDiv);
                }
                
                chatHistory.push({ role: 'assistant', content: fullAnswer });

                // Final scroll safety
                const isAtBottomFinal = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight < 100;
                if (isAtBottomFinal) {
                    chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: 'smooth' });
                }

            } catch (err) {
                const ti = document.getElementById('typing-indicator'); if (ti) ti.remove();
                appendMessage('assistant', `> ERR: ${err.message}`);
            }
            isSending = false; chatSend.disabled = false; chatInput.focus();
        }

        function appendMessage(role, content, sources) {
            const msgDiv = document.createElement('div');
            msgDiv.className = `chat-msg ${role}`;
            const bubble = document.createElement('div');
            bubble.className = role === 'user' ? 'chat-bubble' : 'chat-bubble report-prose !text-[13px]';
            
            // Format content with showdown
            let htmlStr = role === 'user' ? escapeHtml(content) : md.makeHtml(content);
            bubble.innerHTML = htmlStr;
            msgDiv.appendChild(bubble);
            
            if (sources && sources.length > 0) {
                const srcDiv = document.createElement('div');
                srcDiv.className = 'chat-sources';
                srcDiv.innerHTML = '> SOURCES: ' + sources.map(s => `<span title="${s.section || ''}">${s.filename} (${s.doc_type})</span>`).join(' | ');
                msgDiv.appendChild(srcDiv);
            }
            chatMessages.appendChild(msgDiv);
            chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: 'smooth' });
        }

        function escapeHtml(text) { const d = document.createElement('div'); d.textContent = text; return d.innerHTML; }

        // Wire up Evasion Likelihood heading click
        document.getElementById('evasion-heading').addEventListener('click', function() {
            const content = document.getElementById('mgmt-dodging-log').innerHTML;
            openSectionModal('Management Evasion Likelihood', content);
        });
    })();