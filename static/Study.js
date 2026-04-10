/* ═══════════════════════════════════════════════════════════
   STUDY.JS

   Flow:
   1. Load message history from /api/chat/<id>/history
   2. If empty → call /api/chat/<id>/start to get first AI teaching message
   3. User sends message → POST /api/chat/<id>/send
   4. Quiz Me → POST /api/chat/<id>/quiz
   5. All responses saved server-side; JS only handles display

   Token optimization:
   - Sliding window is handled server-side in teacher.py (last 12 msgs)
   - Client never needs to manage context size
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


// ── MARKDOWN-LITE RENDERER ───────────────────────────────────
// Converts the most common LLM markdown to HTML.
// Avoids a full markdown library dependency.

function renderMarkdown(text) {
    if (!text) return '';
    return text
        // Bold
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        // Italic
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code style="background:#1a1a2e;padding:1px 5px;border-radius:4px;font-size:13px;">$1</code>')
        // Headers (##)
        .replace(/^## (.+)$/gm, '<div style="font-size:16px;font-weight:700;margin:10px 0 4px;color:#fff;">$1</div>')
        // Headers (#)
        .replace(/^# (.+)$/gm, '<div style="font-size:18px;font-weight:700;margin:10px 0 4px;color:#fff;">$1</div>')
        // Numbered list items
        .replace(/^\d+\.\s+(.+)$/gm, '<div style="padding-left:16px;margin:3px 0;">• $1</div>')
        // Bullet list items
        .replace(/^[-*]\s+(.+)$/gm, '<div style="padding-left:16px;margin:3px 0;">• $1</div>')
        // Double newline → paragraph break
        .replace(/\n\n/g, '<br><br>')
        // Single newline
        .replace(/\n/g, '<br>');
}


// ── APPEND MESSAGE ───────────────────────────────────────────

function appendMessage(role, content, isQuiz = false, timestamp = null) {
    // Remove loading placeholder if present
    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    const wrapper = document.createElement('div');
    wrapper.className = `msg-wrapper ${role === 'user' ? 'user-msg' : 'assistant-msg'}${isQuiz ? ' quiz-msg' : ''}`;

    const avatar = document.createElement('div');
    avatar.className   = 'msg-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (role === 'assistant') {
        bubble.innerHTML = renderMarkdown(content);
    } else {
        // User messages: plain text, HTML-escape
        bubble.textContent = content;
    }

    const ts = document.createElement('div');
    ts.className   = 'msg-timestamp';
    ts.textContent = timestamp
        ? new Date(timestamp).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})
        : new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});

    wrapper.appendChild(avatar);
    const inner = document.createElement('div');
    inner.appendChild(bubble);
    inner.appendChild(ts);
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
        // No messages yet — trigger first teaching message
        await startChat();
        return;
    }

    // Remove loading placeholder
    const loading = document.getElementById('chatLoading');
    if (loading) loading.remove();

    msgs.forEach(m => appendMessage(m.role, m.content, false, m.timestamp));
}


// ── START CHAT ───────────────────────────────────────────────

async function startChat() {
    showTyping();
    try {
        const res  = await fetch(`/api/chat/${chatId}/start`, { method: 'POST' });
        const data = await res.json();
        hideTyping();

        if (data.already_started) {
            await loadHistory();
            return;
        }
        if (!res.ok) {
            appendMessage('assistant',
                `⚠ Couldn't load the AI tutor: ${data.error || 'Unknown error'}. Try refreshing.`);
            return;
        }
        if (data.notice) showNotice(data.notice);
        appendMessage('assistant', data.content);

    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error. Please refresh and try again.');
    }
}


// ── SEND MESSAGE ─────────────────────────────────────────────

async function sendMessage(text) {
    if (!text.trim() || isSending) return;
    isSending = true;
    sendBtn.disabled = true;

    appendMessage('user', text);
    inputEl.value    = '';
    inputEl.style.height = 'auto';
    showTyping();

    try {
        const res  = await fetch(`/api/chat/${chatId}/send`, {
            method:  'POST',
            headers: {'Content-Type': 'application/json'},
            body:    JSON.stringify({ message: text })
        });
        const data = await res.json();
        hideTyping();

        if (!res.ok) {
            appendMessage('assistant', `⚠ Error: ${data.error || 'Something went wrong.'}`);
        } else {
            if (data.notice) showNotice(data.notice);
            appendMessage('assistant', data.content);
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
    isSending        = true;
    quizBtn.disabled = true;
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
            appendMessage('assistant', data.content, true);  // isQuiz=true → purple border
        }
    } catch (err) {
        hideTyping();
        appendMessage('assistant', '⚠ Network error. Try again.');
    } finally {
        isSending        = false;
        quizBtn.disabled = false;
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