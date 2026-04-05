/* ════════════════════════════════════════
   STATUS.JS
   - Fetches userid from /me (no hardcoded 1)
   - Loading animation while schedule generates
   - Disables generate button on click (rate limit UI)
════════════════════════════════════════ */

let currentUserId = null;
let isGenerating  = false;

// Fetch the logged-in user's id before anything else
async function loadUserId() {
    const res  = await fetch('/me');
    const data = await res.json();
    currentUserId = data.id;
}


// ── COLLECT PAYLOAD ───────────────────────────────────

function collectPayload() {

    const examDates = {};
    const subjects  = {};

    document.querySelectorAll('.subject-block').forEach(block => {

        const subjectName = block.querySelector('h2').innerText.trim();
        const examText    = block.querySelector('.exam-date').innerText.trim();

        if (examText && examText !== 'No exam date') {
            examDates[subjectName] = examText;
        }

        subjects[subjectName] = {};

        block.querySelectorAll('.topic-row').forEach(row => {
            const topic = row.dataset.topic;
            const pct   = row.querySelector('.status-select').value;
            subjects[subjectName][topic] = pct;
        });

    });

    const studyDays = {};
    document.querySelectorAll('#hoursList .date-row').forEach(row => {
        const date  = row.dataset.date;
        const hours = row.querySelector('input').value.trim() || '0';
        studyDays[date] = hours;
    });

    return { Exam_dates: examDates, Subjects: subjects, study_days: studyDays };
}


// ── LOADING HELPERS ───────────────────────────────────

const loaderMessages = [
    'Building your schedule…',
    'Prioritising topics…',
    'Optimising for exam dates…',
    'Almost ready…'
];
let loaderInterval = null;

function startLoader() {
    document.getElementById('generateBtn').style.display  = 'none';
    document.getElementById('statusLoader').style.display = 'block';
    const msgEl = document.getElementById('statusLoaderMsg');
    let i = 0;
    msgEl.textContent = loaderMessages[0];
    loaderInterval = setInterval(() => {
        i = (i + 1) % loaderMessages.length;
        msgEl.textContent = loaderMessages[i];
    }, 4000);
}

function stopLoader() {
    clearInterval(loaderInterval);
    document.getElementById('statusLoader').style.display  = 'none';
    document.getElementById('generateBtn').style.display   = 'block';
}


// ── GENERATE BUTTON ───────────────────────────────────

document.getElementById('generateBtn').addEventListener('click', async () => {

    // Rate-limit guard: ignore double-clicks
    if (isGenerating) return;
    isGenerating = true;
    startLoader();

    try {
        const payload = collectPayload();

        // 1. Save status
        const saveRes = await fetch(`/submit_status/${currentUserId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload)
        });
        if (!saveRes.ok) throw new Error('Failed to save status');

        // 2. Generate schedule
        const genRes = await fetch(`/generate_schedule/${currentUserId}`, {
            method: 'POST'
        });
        if (!genRes.ok) throw new Error('Failed to generate schedule');

        // 3. Navigate to schedule page
        window.location.href = '/schedule_page';

    } catch (err) {
        console.error('Schedule generation failed:', err);
        alert('Failed to generate schedule. Please try again.');
        stopLoader();
        isGenerating = false;
    }

});


// ── INIT ──────────────────────────────────────────────

loadUserId();