/* ═══════════════════════════════════════════════════════════
   STUDY.JS — complete rewrite with streaming + rich rendering
═══════════════════════════════════════════════════════════ */

const chatId     = window.CHAT_ID;
const messagesEl = document.getElementById('chatMessages');
const inputEl    = document.getElementById('chatInput');
const sendBtn    = document.getElementById('sendBtn');
const quizBtn    = document.getElementById('quizBtn');
const typingEl   = document.getElementById('typingIndicator');
const noticeEl   = document.getElementById('llmNotice');

let isSending    = false;
let chatFontSize = 16;


// ══════════════════════════════════════════════
// FONT SIZE CONTROLS
// ══════════════════════════════════════════════

function applyFontSize(size) {
    chatFontSize = Math.max(13, Math.min(24, size));
    document.documentElement.style.setProperty('--chat-font-size', `${chatFontSize}px`);
    document.getElementById('fontSizeDisplay').textContent = `${chatFontSize}px`;
}

document.getElementById('fontIncBtn')?.addEventListener('click', () => applyFontSize(chatFontSize + 1));
document.getElementById('fontDecBtn')?.addEventListener('click', () => applyFontSize(chatFontSize - 1));


// ══════════════════════════════════════════════
// RICH MARKDOWN RENDERER
// Converts LLM markdown to safe HTML with code
// highlighting and proper structure.
// ══════════════════════════════════════════════

function renderRichMarkdown(text) {
    if (!text) return '';
    let html = text;

    // Fenced code blocks with language — wrap with header + highlight.js
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const language   = lang || 'plaintext';
        const escaped    = code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const highlighted = (typeof hljs !== 'undefined' && hljs.getLanguage(language))
            ? hljs.highlight(code, { language }).value
            : escaped;
        return `<pre><div class="code-block-header">
            <span>${language}</span>
            <button class="copy-code-btn" onclick="copyCode(this)">Copy</button>
        </div><code class="hljs language-${language}">${highlighted}</code></pre>`;
    });

    // Remaining inline backtick code
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

    // Bold / italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g,      '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g,           '<em>$1</em>');

    // Horizontal rule
    html = html.replace(/^---+$/gm, '<hr>');

    // Ordered list
    html = html.replace(/^(\d+)\.\s+(.+)$/gm, (_, n, content) =>
        `<li class="ol-item" data-n="${n}">${content}</li>`);
    html = html.replace(/(<li class="ol-item"[^>]*>[\s\S]*?<\/li>)+/g,
        match => `<ol>${match.replace(/ class="ol-item" data-n="\d+"/g,'')}</ol>`);

    // Unordered list
    html = html.replace(/^[-*]\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>[^<]+<\/li>\n?)+/g, match => `<ul>${match}</ul>`);

    // "Next up:" hint styling
    html = html.replace(
        /\*\*Next up:\*\*\s*(.+)/g,
        '<div class="next-up-hint">⟶ <strong>Next up:</strong> $1</div>'
    );

    // Paragraphs (double newline)
    html = html.replace(/\n\n+/g, '</p><p>');
    html = `<p>${html}</p>`;

    // Clean up empty paragraphs and fix tags around block elements
    html = html.replace(/<p>\s*(<(?:pre|ul|ol|h[1-6]|hr|div)[^>]*>)/g, '$1');
    html = html.replace(/(<\/(?:pre|ul|ol|h[1-6]|hr|div)>)\s*<\/p>/g, '$1');
    html = html.replace(/<p>\s*<\/p>/g, '');

    // Single newlines → <br> inside paragraphs only
    html = html.replace(/([^>])\n([^<])/g, '$1<br>$2');

    return html;
}

window.copyCode = function(btn) {
    const code = btn.closest('pre').querySelector('code').textContent;
    navigator.clipboard.writeText(code).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
    });
};


// ══════════════════════════════════════════════
// ANSWER TAG PARSER  ([ANS]...[/ANS])
// ══════════════════════════════════════════════

function parseAnswerTags(text) {
    const segments = [];
    const regex    = /\[ANS\]([\s\S]*?)\[\/ANS\]/g;
    let lastIndex  = 0, match;
    while ((match = regex.exec(text)) !== null) {
        if (match.index > lastIndex)
            segments.push({ type: 'text', content: text.slice(lastIndex, match.index) });
        segments.push({ type: 'answer', content: match[1].trim() });
        lastIndex = regex.lastIndex;
    }
    if (lastIndex < text.length)
        segments.push({ type: 'text', content: text.slice(lastIndex) });
    return segments;
}

function buildAnswerBlock(answerText) {
    const wrapper = document.createElement('div');
    wrapper.className = 'answer-block';

    const header = document.createElement('div');
    header.className = 'answer-header';
    header.innerHTML = '<span>Answer</span>';

    const btn = document.createElement('button');
    btn.className   = 'answer-toggle-btn';
    btn.textContent = 'Show Answer';

    const body = document.createElement('div');
    body.className = 'answer-body';
    body.innerHTML  = renderRichMarkdown(answerText);

    let revealed = false;
    btn.addEventListener('click', () => {
        revealed = !revealed;
        body.classList.toggle('revealed', revealed);
        btn.textContent = revealed ? 'Hide Answer' : 'Show Answer';
        if (revealed) triggerMathRender(body);
    });

    header.appendChild(btn);
    wrapper.appendChild(header);
    wrapper.appendChild(body);
    return wrapper;
}


// ══════════════════════════════════════════════
// BUILD BUBBLE CONTENT
// ══════════════════════════════════════════════

function buildBubbleContent(text, isQuiz) {
    const container = document.createElement('div');

    if (!isQuiz && !text.includes('[ANS]')) {
        container.innerHTML = renderRichMarkdown(text);
        return container;
    }

    const segments = parseAnswerTags(text);
    if (segments.every(s => s.type === 'text')) {
        container.innerHTML = renderRichMarkdown(text);
        return container;
    }

    segments.forEach(seg => {
        if (seg.type === 'text') {
            const d = document.createElement('div');
            d.innerHTML = renderRichMarkdown(seg.content);
            container.appendChild(d);
        } else {
            container.appendChild(buildAnswerBlock(seg.content));
        }
    });

    return container;
}


// ══════════════════════════════════════════════
// MATH RENDERING (KaTeX)
// ══════════════════════════════════════════════

function triggerMathRender(el) {
    if (typeof renderMathInElement !== 'undefined') {
        renderMathInElement(el, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$',  right: '$',  display: false },
                { left: '\\(', right: '\\)', display: false },
                { left: '\\[', right: '\\]', display: true }
            ],
            throwOnError: false
        });
    }
}


// ══════════════════════════════════════════════
// APPEND MESSAGE (non-streaming)
// ══════════════════════════════════════════════

function appendMessage(role, content, isQuiz = false, timestamp = null, msgId = null) {
    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    const wrapper = document.createElement('div');
    wrapper.className = `msg-wrapper ${role === 'user' ? 'user-msg' : 'assistant-msg'}${isQuiz ? ' quiz-msg' : ''}`;
    if (msgId) wrapper.dataset.msgId = msgId;

    const avatar = document.createElement('div');
    avatar.className   = 'msg-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';

    const inner  = document.createElement('div');
    inner.className = 'msg-inner';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (role === 'assistant') {
        bubble.appendChild(buildBubbleContent(content, isQuiz));
        triggerMathRender(bubble);
    } else {
        bubble.textContent = content;
    }

    const ts = document.createElement('div');
    ts.className   = 'msg-timestamp';
    ts.textContent = timestamp
        ? new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Action buttons
    const actions = buildMessageActions(role, content, msgId, wrapper, bubble, inner);

    inner.appendChild(bubble);
    inner.appendChild(ts);
    inner.appendChild(actions);
    wrapper.appendChild(avatar);
    wrapper.appendChild(inner);

    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return wrapper;
}


// ══════════════════════════════════════════════
// MESSAGE ACTION BUTTONS
// ══════════════════════════════════════════════

function buildMessageActions(role, content, msgId, wrapper, bubble, inner) {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';

    if (role === 'user' && msgId) {
        // Edit button
        const editBtn = document.createElement('button');
        editBtn.className   = 'msg-action-btn';
        editBtn.textContent = '✏ Edit';
        editBtn.addEventListener('click', () => startEdit(wrapper, bubble, inner, msgId, content));
        actions.appendChild(editBtn);
    }

    if (role === 'assistant') {
        // Regenerate button
        const regenBtn = document.createElement('button');
        regenBtn.className   = 'msg-action-btn regen';
        regenBtn.textContent = '↺ Regenerate';
        regenBtn.addEventListener('click', () => regenerateLast(wrapper));
        actions.appendChild(regenBtn);
    }

    if (msgId) {
        // Delete button
        const delBtn = document.createElement('button');
        delBtn.className   = 'msg-action-btn danger';
        delBtn.textContent = '🗑 Delete';
        delBtn.addEventListener('click', () => deleteMsg(msgId, wrapper));
        actions.appendChild(delBtn);
    }

    return actions;
}

function startEdit(wrapper, bubble, inner, msgId, originalContent) {
    const textarea = document.createElement('textarea');
    textarea.className = 'msg-edit-area';
    textarea.value     = originalContent;

    const btnRow = document.createElement('div');
    btnRow.className = 'msg-edit-btns';

    const saveBtn   = document.createElement('button');
    saveBtn.className   = 'msg-edit-save';
    saveBtn.textContent = 'Save';

    const cancelBtn = document.createElement('button');
    cancelBtn.className   = 'msg-edit-cancel';
    cancelBtn.textContent = 'Cancel';

    cancelBtn.addEventListener('click', () => {
        inner.replaceChild(bubble, textarea);
        btnRow.remove();
    });

    saveBtn.addEventListener('click', async () => {
        const newContent = textarea.value.trim();
        if (!newContent) return;

        try {
            const res = await fetch(`/api/chat/${chatId}/message/${msgId}`, {
                method:  'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ content: newContent })
            });
            if (!res.ok) throw new Error('Edit failed');
            bubble.textContent = newContent;
            inner.replaceChild(bubble, textarea);
            btnRow.remove();
        } catch (err) {
            alert('Failed to save edit: ' + err.message);
        }
    });

    btnRow.appendChild(saveBtn);
    btnRow.appendChild(cancelBtn);
    inner.replaceChild(textarea, bubble);
    inner.appendChild(btnRow);
    textarea.focus();
}

async function deleteMsg(msgId, wrapper) {
    if (!confirm('Delete this message?')) return;
    try {
        const res = await fetch(`/api/chat/${chatId}/message/${msgId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Delete failed');

        // Remove this message and possibly the next one (AI response)
        const next = wrapper.nextElementSibling;
        if (next && next.classList.contains('assistant-msg')) next.remove();
        wrapper.remove();
    } catch (err) {
        alert('Failed to delete: ' + err.message);
    }
}

async function regenerateLast(assistantWrapper) {
    if (isSending) return;
    isSending = true;
    assistantWrapper.remove();
    showTyping();

    try {
        const res  = await fetch(`/api/chat/${chatId}/regenerate_last`, { method: 'POST' });
        const data = await res.json();
        hideTyping();
        if (!res.ok) {
            appendMessage('assistant', `⚠ ${data.error || 'Regeneration failed.'}`);
        } else {
            if (data.notice) showNotice(data.notice);
            appendMessage('assistant', data.content, false, null, data.id);
        }
    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error during regeneration.');
    } finally {
        isSending = false;
    }
}


// ══════════════════════════════════════════════
// STREAMING  (SSE via fetch + ReadableStream)
// ══════════════════════════════════════════════

async function streamResponse(url, isQuiz = false) {
    showTyping();

    // Create a placeholder bubble for streamed content
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-wrapper assistant-msg' + (isQuiz ? ' quiz-msg' : '');

    const avatar = document.createElement('div');
    avatar.className   = 'msg-avatar';
    avatar.textContent = '🤖';

    const inner  = document.createElement('div');
    inner.className = 'msg-inner';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble streaming-cursor';

    const rawContainer = document.createElement('div');
    rawContainer.style.whiteSpace = 'pre-wrap';
    bubble.appendChild(rawContainer);

    inner.appendChild(bubble);
    wrapper.appendChild(avatar);
    wrapper.appendChild(inner);

    // Remove loading spinner if present
    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    messagesEl.appendChild(wrapper);

    let fullText = '';

    try {
        const res = await fetch(url, { method: 'POST' });

        if (!res.ok) {
            const err = await res.json();
            bubble.classList.remove('streaming-cursor');
            rawContainer.textContent = `⚠ ${err.error || 'Request failed'}`;
            hideTyping();
            return;
        }

        hideTyping();

        const reader  = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer    = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer      = lines.pop();  // keep incomplete line

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6);

                if (payload === '[DONE]' || payload === '[DONE_QUIZ]') {
                    // Streaming complete — replace raw text with rich rendering
                    bubble.classList.remove('streaming-cursor');
                    bubble.innerHTML = '';
                    bubble.appendChild(buildBubbleContent(fullText, isQuiz));
                    triggerMathRender(bubble);

                    // Add action buttons
                    const ts = document.createElement('div');
                    ts.className   = 'msg-timestamp';
                    ts.textContent = new Date().toLocaleTimeString([],
                        { hour: '2-digit', minute: '2-digit' });

                    const actions = buildMessageActions('assistant', fullText, null, wrapper, bubble, inner);
                    inner.appendChild(ts);
                    inner.appendChild(actions);

                    return;
                }

                if (payload.startsWith('[ERROR]')) {
                    bubble.classList.remove('streaming-cursor');
                    rawContainer.textContent = `⚠ ${payload.slice(7).trim()}`;
                    return;
                }

                // Normal text chunk
                try {
                    const chunk = JSON.parse(payload);
                    fullText   += chunk;
                    rawContainer.textContent = fullText;
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                } catch { /* skip malformed chunk */ }
            }
        }

    } catch (err) {
        bubble.classList.remove('streaming-cursor');
        rawContainer.textContent = '⚠ Network error during streaming.';
        hideTyping();
    }
}


// ══════════════════════════════════════════════
// LOAD HISTORY
// ══════════════════════════════════════════════

async function loadHistory() {
    const res  = await fetch(`/api/chat/${chatId}/history`);
    const msgs = await res.json();

    if (!Array.isArray(msgs) || msgs.length === 0) {
        await startChat();
        return;
    }

    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    msgs.forEach(m => {
        const isQuiz = m.role === 'assistant' && m.content.includes('[ANS]');
        appendMessage(m.role, m.content, isQuiz, m.timestamp, m.id);
    });
}

async function startChat() {
    showTyping();
    try {
        const res  = await fetch(`/api/chat/${chatId}/start`, { method: 'POST' });
        const data = await res.json();
        hideTyping();
        if (data.already_started) { await loadHistory(); return; }
        if (!res.ok) {
            appendMessage('assistant', `⚠ Couldn't start the tutor: ${data.error || 'Unknown error.'}`);
            return;
        }
        if (data.notice) showNotice(data.notice);
        appendMessage('assistant', data.content, false, null, null);
    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error. Please refresh.');
    }
}


// ══════════════════════════════════════════════
// SEND MESSAGE (streaming)
// ══════════════════════════════════════════════

async function sendMessage(text) {
    if (!text.trim() || isSending) return;
    isSending        = true;
    sendBtn.disabled = true;

    appendMessage('user', text);
    inputEl.value        = '';
    inputEl.style.height = 'auto';

    // Save user message then stream AI reply
    // (user message is saved server-side inside the stream endpoint)
    await streamResponse(`/api/chat/${chatId}/send/stream`);

    isSending        = false;
    sendBtn.disabled = false;
    inputEl.focus();
}

// Override: the stream endpoint needs the message body.
// We store it temporarily for the fetch call.
let _pendingMessage = '';

const _origSend = sendMessage;
sendMessage = async function(text) {
    if (!text.trim() || isSending) return;
    isSending        = true;
    sendBtn.disabled = true;

    appendMessage('user', text);
    inputEl.value        = '';
    inputEl.style.height = 'auto';

    showTyping();
    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    // Build the placeholder bubble before the stream starts
    await streamResponseWithBody(`/api/chat/${chatId}/send/stream`,
                                  { message: text }, false);

    isSending        = false;
    sendBtn.disabled = false;
    inputEl.focus();
};


async function streamResponseWithBody(url, body, isQuiz) {
    // Create placeholder
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-wrapper assistant-msg' + (isQuiz ? ' quiz-msg' : '');

    const avatar = document.createElement('div');
    avatar.className   = 'msg-avatar';
    avatar.textContent = '🤖';

    const inner  = document.createElement('div');
    inner.className = 'msg-inner';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble streaming-cursor';

    const rawContainer = document.createElement('div');
    rawContainer.style.whiteSpace = 'pre-wrap';
    bubble.appendChild(rawContainer);

    inner.appendChild(bubble);
    wrapper.appendChild(avatar);
    wrapper.appendChild(inner);
    messagesEl.appendChild(wrapper);
    hideTyping();

    let fullText = '';

    try {
        const res = await fetch(url, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body)
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: 'Request failed' }));
            bubble.classList.remove('streaming-cursor');
            rawContainer.textContent = `⚠ ${err.error}`;
            return;
        }

        const reader  = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer    = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6);

                if (payload === '[DONE]' || payload === '[DONE_QUIZ]') {
                    bubble.classList.remove('streaming-cursor');
                    bubble.innerHTML = '';
                    bubble.appendChild(buildBubbleContent(fullText, payload === '[DONE_QUIZ]'));
                    triggerMathRender(bubble);
                    const ts = document.createElement('div');
                    ts.className = 'msg-timestamp';
                    ts.textContent = new Date().toLocaleTimeString([],
                        { hour: '2-digit', minute: '2-digit' });
                    const actions = buildMessageActions('assistant', fullText, null, wrapper, bubble, inner);
                    inner.appendChild(ts);
                    inner.appendChild(actions);
                    return;
                }

                if (payload.startsWith('[ERROR]')) {
                    bubble.classList.remove('streaming-cursor');
                    rawContainer.textContent = `⚠ ${payload.slice(7).trim()}`;
                    return;
                }

                try {
                    const chunk = JSON.parse(payload);
                    fullText   += chunk;
                    rawContainer.textContent = fullText;
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                } catch { /* skip */ }
            }
        }
    } catch (err) {
        bubble.classList.remove('streaming-cursor');
        rawContainer.textContent = '⚠ Network error.';
    }
}


// ══════════════════════════════════════════════
// QUIZ (streaming)
// ══════════════════════════════════════════════

quizBtn.addEventListener('click', async () => {
    if (isSending) return;
    isSending           = true;
    quizBtn.disabled    = true;
    quizBtn.textContent = '…Generating Quiz';

    appendMessage('user', 'Quiz me on what we have covered so far.');
    showTyping();

    await streamResponseWithBody(`/api/chat/${chatId}/quiz/stream`, {}, true);

    isSending           = false;
    quizBtn.disabled    = false;
    quizBtn.textContent = '🎯 Quiz Me';
});


// ══════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════

function showTyping() {
    typingEl.classList.remove('hidden');
    messagesEl.scrollTop = messagesEl.scrollHeight;
}
function hideTyping() { typingEl.classList.add('hidden'); }

function showNotice(notice) {
    if (!notice) return;
    noticeEl.textContent = `⚠ Primary AI unavailable. Using ${notice.model} instead.`;
    noticeEl.classList.remove('hidden');
    setTimeout(() => noticeEl.classList.add('hidden'), 8000);
}

sendBtn.addEventListener('click', () => sendMessage(inputEl.value));

inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(inputEl.value);
    }
});

inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
});


// ══════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════

applyFontSize(16);
loadHistory();
inputEl.focus();