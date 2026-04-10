/* ═══════════════════════════════════════════════════════════
   STUDY.JS
   - Loads chat history, starts AI teaching on first open
   - Sends messages, handles quiz with blurred answers
   - [ANS]...[/ANS] tags in quiz responses become blurred
     blocks with a Show/Hide button
═══════════════════════════════════════════════════════════ */

const chatId     = window.CHAT_ID;
const userId     = window.USER_ID;
const subject    = window.SUBJECT;
const topic      = window.TOPIC;
const messagesEl = document.getElementById('chatMessages');
const inputEl    = document.getElementById('chatInput');
const sendBtn    = document.getElementById('sendBtn');
const quizBtn    = document.getElementById('quizBtn');
const typingEl   = document.getElementById('typingIndicator');
const noticeEl   = document.getElementById('llmNotice');

let isSending = false;


// ── ANSWER TAG PARSER ────────────────────────────────────────
// Splits a quiz response into segments: plain text and answer blocks.
// Returns an array like:
//   [ {type:'text', content:'Q1. What is...'}, {type:'answer', content:'...'}, ... ]

function parseAnswerTags(text) {
    const segments = [];
    const regex    = /\[ANS\]([\s\S]*?)\[\/ANS\]/g;
    let lastIndex  = 0;
    let match;

    while ((match = regex.exec(text)) !== null) {
        // Text before this answer tag
        if (match.index > lastIndex) {
            segments.push({ type: 'text', content: text.slice(lastIndex, match.index) });
        }
        segments.push({ type: 'answer', content: match[1].trim() });
        lastIndex = regex.lastIndex;
    }

    // Remaining text after last tag
    if (lastIndex < text.length) {
        segments.push({ type: 'text', content: text.slice(lastIndex) });
    }

    return segments;
}


// ── MARKDOWN-LITE RENDERER ───────────────────────────────────

function renderMarkdown(text) {
    if (!text) return '';
    return text
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`([^`]+)`/g, '<code style="background:#1a1a2e;padding:1px 5px;border-radius:4px;font-size:13px;font-family:monospace;">$1</code>')
        .replace(/^### (.+)$/gm, '<div style="font-size:14px;font-weight:700;margin:10px 0 4px;color:#bf7fff;">$1</div>')
        .replace(/^## (.+)$/gm,  '<div style="font-size:16px;font-weight:700;margin:10px 0 4px;color:#fff;">$1</div>')
        .replace(/^# (.+)$/gm,   '<div style="font-size:18px;font-weight:700;margin:10px 0 4px;color:#fff;">$1</div>')
        .replace(/^\d+\.\s+(.+)$/gm, '<div style="padding-left:16px;margin:3px 0;">$1</div>')
        .replace(/^[-*]\s+(.+)$/gm,  '<div style="padding-left:16px;margin:3px 0;">• $1</div>')
        .replace(/\n\n/g, '<br><br>')
        .replace(/\n/g, '<br>');
}


// ── BUILD BUBBLE CONTENT ─────────────────────────────────────
// For assistant messages, checks for [ANS] tags.
// If found, renders mixed content with blurred answer blocks.
// If not found, renders plain markdown.

function buildBubbleContent(text, isQuiz) {
    const container = document.createElement('div');

    if (!isQuiz && !text.includes('[ANS]')) {
        // Plain teaching message — just markdown
        container.innerHTML = renderMarkdown(text);
        return container;
    }

    const segments = parseAnswerTags(text);

    if (segments.every(s => s.type === 'text')) {
        // No answer tags found even though it's a quiz — render as markdown
        container.innerHTML = renderMarkdown(text);
        return container;
    }

    segments.forEach(seg => {
        if (seg.type === 'text') {
            const textDiv = document.createElement('div');
            textDiv.innerHTML = renderMarkdown(seg.content);
            container.appendChild(textDiv);
        } else {
            // Answer block
            const answerWrapper = document.createElement('div');
            answerWrapper.style.cssText = `
                margin: 8px 0;
                border: 1px solid rgba(123,47,247,0.3);
                border-radius: 10px;
                overflow: hidden;
            `;

            const answerHeader = document.createElement('div');
            answerHeader.style.cssText = `
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 6px 12px;
                background: rgba(123,47,247,0.12);
                font-size: 12px;
                color: #9f55ff;
                font-weight: 600;
            `;
            answerHeader.innerHTML = '<span>Answer</span>';

            const toggleBtn = document.createElement('button');
            toggleBtn.textContent = 'Show Answer';
            toggleBtn.style.cssText = `
                background: none;
                border: 1px solid rgba(123,47,247,0.4);
                border-radius: 8px;
                padding: 3px 10px;
                color: #9f55ff;
                font-size: 11px;
                cursor: pointer;
                transition: 0.2s;
            `;

            answerHeader.appendChild(toggleBtn);

            const answerBody = document.createElement('div');
            answerBody.style.cssText = `
                padding: 10px 14px;
                font-size: 14px;
                line-height: 1.6;
                color: #ddd;
                filter: blur(6px);
                user-select: none;
                transition: filter 0.25s ease;
            `;
            answerBody.innerHTML = renderMarkdown(seg.content);

            let revealed = false;
            toggleBtn.addEventListener('click', () => {
                revealed = !revealed;
                answerBody.style.filter    = revealed ? 'none' : 'blur(6px)';
                answerBody.style.userSelect = revealed ? 'text' : 'none';
                toggleBtn.textContent      = revealed ? 'Hide Answer' : 'Show Answer';
                toggleBtn.style.background = revealed
                    ? 'rgba(123,47,247,0.15)'
                    : 'none';
            });

            answerWrapper.appendChild(answerHeader);
            answerWrapper.appendChild(answerBody);
            container.appendChild(answerWrapper);
        }
    });

    return container;
}


// ── APPEND MESSAGE ───────────────────────────────────────────

function appendMessage(role, content, isQuiz = false, timestamp = null) {
    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    const wrapper = document.createElement('div');
    wrapper.className = `msg-wrapper ${role === 'user' ? 'user-msg' : 'assistant-msg'}${isQuiz ? ' quiz-msg' : ''}`;

    const avatar = document.createElement('div');
    avatar.className   = 'msg-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';

    const inner  = document.createElement('div');

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    if (role === 'assistant') {
        bubble.appendChild(buildBubbleContent(content, isQuiz));
    } else {
        bubble.textContent = content;
    }

    const ts = document.createElement('div');
    ts.className   = 'msg-timestamp';
    ts.textContent = timestamp
        ? new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    inner.appendChild(bubble);
    inner.appendChild(ts);
    wrapper.appendChild(avatar);
    wrapper.appendChild(inner);

    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return wrapper;
}

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


// ── LOAD HISTORY ─────────────────────────────────────────────

async function loadHistory() {
    const res  = await fetch(`/api/chat/${chatId}/history`);
    const msgs = await res.json();

    if (!Array.isArray(msgs) || msgs.length === 0) {
        await startChat();
        return;
    }

    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    // Detect which messages are quiz responses (heuristic: contains [ANS] tag)
    msgs.forEach(m => {
        const isQuiz = m.role === 'assistant' && m.content.includes('[ANS]');
        appendMessage(m.role, m.content, isQuiz, m.timestamp);
    });
}


// ── START CHAT ───────────────────────────────────────────────

async function startChat() {
    showTyping();
    try {
        const res  = await fetch(`/api/chat/${chatId}/start`, { method: 'POST' });
        const data = await res.json();
        hideTyping();

        if (data.already_started) { await loadHistory(); return; }
        if (!res.ok) {
            appendMessage('assistant', `⚠ Couldn't load the AI tutor: ${data.error || 'Unknown error'}. Try refreshing.`);
            return;
        }
        if (data.notice) showNotice(data.notice);
        appendMessage('assistant', data.content, false);
    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error. Please refresh and try again.');
    }
}


// ── SEND MESSAGE ─────────────────────────────────────────────

async function sendMessage(text) {
    if (!text.trim() || isSending) return;
    isSending        = true;
    sendBtn.disabled = true;

    appendMessage('user', text);
    inputEl.value        = '';
    inputEl.style.height = 'auto';
    showTyping();

    try {
        const res  = await fetch(`/api/chat/${chatId}/send`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ message: text })
        });
        const data = await res.json();
        hideTyping();

        if (!res.ok) {
            appendMessage('assistant', `⚠ Error: ${data.error || 'Something went wrong.'}`);
        } else {
            if (data.notice) showNotice(data.notice);
            appendMessage('assistant', data.content, false);
        }
    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error. Check your connection.');
    } finally {
        isSending        = false;
        sendBtn.disabled = false;
        inputEl.focus();
    }
}


// ── QUIZ ─────────────────────────────────────────────────────

quizBtn.addEventListener('click', async () => {
    if (isSending) return;
    isSending           = true;
    quizBtn.disabled    = true;
    quizBtn.textContent = '…Generating Quiz';

    appendMessage('user', 'Quiz me on what we have covered so far.');
    showTyping();

    try {
        const res  = await fetch(`/api/chat/${chatId}/quiz`, { method: 'POST' });
        const data = await res.json();
        hideTyping();

        if (!res.ok) {
            appendMessage('assistant', `⚠ ${data.error || 'Quiz generation failed.'}`);
        } else {
            if (data.notice) showNotice(data.notice);
            // isQuiz=true → triggers answer-tag parsing + blurred answers
            appendMessage('assistant', data.content, true);
        }
    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error. Try again.');
    } finally {
        isSending           = false;
        quizBtn.disabled    = false;
        quizBtn.textContent = '🎯 Quiz Me';
    }
});


// ── SEND BUTTON & ENTER KEY ───────────────────────────────────

sendBtn.addEventListener('click', () => sendMessage(inputEl.value));

inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(inputEl.value);
    }
});

// Auto-resize textarea
inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
});


// ── INIT ─────────────────────────────────────────────────────

loadHistory();
inputEl.focus();