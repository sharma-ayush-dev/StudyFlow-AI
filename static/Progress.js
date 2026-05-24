/* Progress.js — fixed */

const userId       = window.USER_ID;
const topicStatus  = window.TOPIC_STATUS;
const pastSchedule = window.PAST_SCHEDULE;
const fullSchedule = window.FULL_SCHEDULE;
const todayStr     = window.TODAY_STR;
const isAdmin      = window.IS_ADMIN || false;

// ── OVERDUE SET ──────────────────────────────────────────────

function buildOverdueSet() {
    const overdue = new Set();
    Object.values(pastSchedule).forEach(subjects => {
        Object.entries(subjects).forEach(([subj, topics]) => {
            Object.keys(topics).forEach(topic => overdue.add(`${subj}||${topic}`));
        });
    });
    return overdue;
}
const overdueSet = buildOverdueSet();


// ── RENDER TOPIC LIST ────────────────────────────────────────

function renderTopicList() {
    const card     = document.getElementById('progressTopicsCard');
    const subjects = topicStatus.Subjects || {};
    card.innerHTML = '';

    if (!Object.keys(subjects).length) {
        card.innerHTML = '<p style="color:#666;padding:20px;">No topic data found. Generate a schedule first.</p>';
        return;
    }

    Object.entries(subjects).forEach(([subjName, topics]) => {

        const block  = document.createElement('div');
        block.className = 'subject-block';

        // Header
        const header = document.createElement('div');
        header.className = 'subject-header';

        const nameEl = document.createElement('h2');
        nameEl.textContent = subjName;

        const examEl = document.createElement('span');
        examEl.className   = 'exam-date';
        examEl.textContent = (topicStatus.Exam_dates || {})[subjName] || 'No exam date';

        header.appendChild(nameEl);
        header.appendChild(examEl);
        block.appendChild(header);

        // Topics
        const topicsDiv = document.createElement('div');
        topicsDiv.className = 'topics';

        Object.entries(topics).forEach(([topicName, tdata]) => {

            // BUG FIX 3: was using undefined `currentPct`, correct var is `currentStatus`
            const currentStatus = typeof tdata === 'object' ? (tdata.status || '0') : String(tdata);
            const subtopics     = typeof tdata === 'object' ? (tdata.subtopics || []) : [];

            const key       = `${subjName}||${topicName}`;
            const isOverdue = overdueSet.has(key);

            // BUG FIX 2: `row` was never created
            const row = document.createElement('div');
            row.className = 'topic-row';

            // BUG FIX 1: `left` was never created
            const left = document.createElement('div');
            left.style.flex = '1';

            const topLine = document.createElement('div');
            topLine.style.cssText = 'display:flex;align-items:center;gap:8px;';

            const nameSpan = document.createElement('span');
            nameSpan.className   = 'topic-name';
            nameSpan.textContent = topicName;

            if (isOverdue) {
                const badge = document.createElement('span');
                badge.className   = 'overdue-badge';
                badge.textContent = 'Scheduled';
                badge.title       = 'This topic was scheduled to be studied by today';
                nameSpan.appendChild(badge);
            }
            topLine.appendChild(nameSpan);

            if (subtopics.length) {
                const toggle = document.createElement('span');
                toggle.className = 'subtopics-toggle';
                toggle.innerHTML = `<span class="toggle-arrow">▸</span> ${subtopics.length}`;
                
                const subList = document.createElement('ul');
                subList.className = 'subtopics-list';
                subtopics.forEach(s => {
                    const li = document.createElement('li');
                    li.style.cssText = 'font-size:12px;color:#777;margin:3px 0;';
                    li.textContent   = s;
                    subList.appendChild(li);
                });
                
                toggle.addEventListener('click', (e) => {
                    e.stopPropagation();
                    toggle.classList.toggle('open');
                    subList.classList.toggle('open');
                });
                
                topLine.appendChild(toggle);
                left.appendChild(topLine);
                left.appendChild(subList);
            } else {
                left.appendChild(topLine);
            }

            // Custom glassmorphic dropdown
            const customSelect = document.createElement('div');
            customSelect.className       = 'custom-select';
            customSelect.dataset.subject = subjName;
            customSelect.dataset.topic   = topicName;

            const trigger = document.createElement('div');
            trigger.className = 'select-trigger';

            const optionsData = [
                ['0',   '0% — Not Started'],
                ['25',  '25% — Just Begun'],
                ['50',  '50% — Halfway'],
                ['75',  '75% — Almost Done'],
                ['100', '100% — Completed']
            ];

            // BUG FIX 3 (cont): was referencing undefined `currentPct`; now uses `currentStatus`
            const initOpt = optionsData.find(o => o[0] === String(currentStatus));
            trigger.innerHTML = `<span class="value">${initOpt ? initOpt[1] : optionsData[0][1]}</span>`;
            customSelect.dataset.value = currentStatus;

            const optionsContainer = document.createElement('div');
            optionsContainer.className = 'select-options';

            optionsData.forEach(([val, label]) => {
                const opt = document.createElement('div');
                opt.className   = 'option' + (String(val) === String(currentStatus) ? ' selected' : '');
                opt.textContent = label;
                opt.dataset.value = val;

                opt.addEventListener('click', (e) => {
                    e.stopPropagation();
                    trigger.querySelector('.value').textContent = label;
                    customSelect.dataset.value = val;
                    customSelect.classList.remove('active');
                    optionsContainer.querySelectorAll('.option')
                        .forEach(o => o.classList.remove('selected'));
                    opt.classList.add('selected');
                });
                optionsContainer.appendChild(opt);
            });

            trigger.addEventListener('click', (e) => {
                e.stopPropagation();
                document.querySelectorAll('.custom-select').forEach(s => {
                    if (s !== customSelect) s.classList.remove('active');
                });
                customSelect.classList.toggle('active');
            });

            customSelect.appendChild(trigger);
            customSelect.appendChild(optionsContainer);

            row.appendChild(left);
            row.appendChild(customSelect);
            topicsDiv.appendChild(row);
        });

        block.appendChild(topicsDiv);
        card.appendChild(block);
    });
}


// ── COLLECT UPDATED SUBJECTS ─────────────────────────────────

function collectUpdatedSubjects() {
    const subjects = {};
    document.querySelectorAll('.custom-select').forEach(sel => {
        const subj  = sel.dataset.subject;
        const topic = sel.dataset.topic;
        if (!subjects[subj]) subjects[subj] = {};
        subjects[subj][topic] = sel.dataset.value || '0';
    });
    return subjects;
}


// ── NOTICE BANNER (defined once) ─────────────────────────────
// BUG FIX 4: was defined twice; removed the duplicate

function showNoticeBanner(notice) {
    const existing = document.getElementById('llmNoticeBanner');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.id    = 'llmNoticeBanner';
    banner.style.cssText = `
        max-width:850px; margin:0 auto 20px; padding:14px 20px;
        background:rgba(255,180,0,0.1); border:1px solid rgba(255,180,0,0.35);
        border-radius:12px; font-size:14px; color:#ffd060; line-height:1.6;`;

    banner.innerHTML = `⚠ Primary AI unavailable. Used <strong>${notice.model}</strong> instead.`;

    if (isAdmin && notice.reasons && notice.reasons.length) {
        const details = document.createElement('details');
        details.style.marginTop = '8px';
        const summary = document.createElement('summary');
        summary.textContent = 'Admin: show failure details';
        summary.style.cursor = 'pointer';
        const pre = document.createElement('pre');
        pre.style.cssText   = 'font-size:12px;color:#aaa;margin-top:8px;white-space:pre-wrap;';
        pre.textContent     = notice.reasons.join('\n\n');
        details.appendChild(summary);
        details.appendChild(pre);
        banner.appendChild(details);
    }

    const wrapper = document.getElementById('progressWrapper');
    wrapper.parentNode.insertBefore(banner, wrapper);
}


// ── LOADER ───────────────────────────────────────────────────

const loaderMessages = {
    save:  ['Saving progress…', 'Updating percentages…'],
    regen: ['Analysing your progress…', 'Rescheduling topics…',
            'Optimising for exam dates…', 'Almost ready…']
};
let loaderInterval = null;

function showLoader(type) {
    document.getElementById('progressActions').style.display = 'none';
    document.getElementById('progressLoader').style.display  = 'block';
    const msgs = loaderMessages[type];
    let i = 0;
    document.getElementById('progressLoaderMsg').textContent = msgs[0];
    loaderInterval = setInterval(() => {
        i = (i + 1) % msgs.length;
        document.getElementById('progressLoaderMsg').textContent = msgs[i];
    }, 3500);
}

function hideLoader() {
    clearInterval(loaderInterval);
    document.getElementById('progressLoader').style.display  = 'none';
    document.getElementById('progressActions').style.display = 'flex';
}


// ── SAVE PROGRESS ────────────────────────────────────────────

document.getElementById('saveProgressBtn').addEventListener('click', async () => {
    showLoader('save');
    try {
        const res = await fetch(`/update_progress/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ Subjects: collectUpdatedSubjects() })
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Failed');
        hideLoader();
        const btn  = document.getElementById('saveProgressBtn');
        const orig = btn.textContent;
        btn.textContent = '✓ Saved!';
        setTimeout(() => { btn.textContent = orig; }, 2000);
    } catch (err) {
        hideLoader();
        alert('Failed: ' + err.message);
    }
});


// ── REGENERATE  (async job polling) ──────────────────────────────────────

async function pollJob(jobId, intervalMs = 2000, timeoutMs = 180000) {
    const start = Date.now();
    return new Promise((resolve, reject) => {
        const tick = async () => {
            if (Date.now() - start > timeoutMs) {
                return reject(new Error('Timed out waiting for schedule generation.'));
            }
            try {
                const res  = await fetch(`/job/${jobId}/status`);
                const data = await res.json();
                if (data.status === 'done')   return resolve(data.result);
                if (data.status === 'error')  return reject(new Error(data.error || 'Generation failed'));
                // still pending — keep polling
                setTimeout(tick, intervalMs);
            } catch (err) {
                reject(err);
            }
        };
        tick();
    });
}

document.getElementById('regenBtn').addEventListener('click', async () => {
    showLoader('regen');
    try {
        // 1. Save progress first
        const saveRes = await fetch(`/update_progress/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ Subjects: collectUpdatedSubjects() })
        });
        if (!saveRes.ok) throw new Error('Failed to save progress before regenerating');

        // 2. Kick off async job — returns immediately with job_id
        const regenRes = await fetch(`/regenerate_schedule/${userId}`, { method: 'POST' });
        const regenData = await regenRes.json();
        if (!regenRes.ok) {
            let msg = regenData.error || 'Regeneration failed';
            if (isAdmin && regenData.details) msg += '\n\nAdmin details:\n' + regenData.details;
            throw new Error(msg);
        }

        // Persist job_id so a page refresh can resume polling
        sessionStorage.setItem('pendingRegenJob', regenData.job_id);

        // 3. Poll until done
        const result = await pollJob(regenData.job_id);
        sessionStorage.removeItem('pendingRegenJob');

        hideLoader();
        if (result.notice) showNoticeBanner(result.notice);
        renderComparison(result.old_schedule, result.new_schedule);

    } catch (err) {
        hideLoader();
        sessionStorage.removeItem('pendingRegenJob');
        alert('Regeneration failed:\n' + err.message);
    }
});

// Resume polling if the page was refreshed while a job was running
(function resumeJobOnLoad() {
    const jobId = sessionStorage.getItem('pendingRegenJob');
    if (!jobId) return;
    showLoader('regen');
    pollJob(jobId)
        .then(result => {
            sessionStorage.removeItem('pendingRegenJob');
            hideLoader();
            if (result.notice) showNoticeBanner(result.notice);
            renderComparison(result.old_schedule, result.new_schedule);
        })
        .catch(err => {
            sessionStorage.removeItem('pendingRegenJob');
            hideLoader();
            // Silently fail on resume (job may have expired on server restart)
        });
})();


// ── COMPARISON RENDER ────────────────────────────────────────

function sortDMY(dates) {
    return dates.sort((a, b) => {
        const ms = s => { const [d, m, y] = s.split('-'); return new Date(+y, +m - 1, +d).getTime(); };
        return ms(a) - ms(b);
    });
}

function renderScheduleInto(containerId, scheduleData) {
    const el = document.getElementById(containerId);
    el.innerHTML = '';
    const dates = sortDMY(Object.keys(scheduleData));
    if (!dates.length) {
        el.innerHTML = '<p style="color:#666;padding:16px;">Empty schedule.</p>';
        return;
    }
    dates.forEach(date => {
        const dateBlock = document.createElement('div');
        dateBlock.className = 'cmp-date-block';

        const dateTitle = document.createElement('div');
        dateTitle.className   = 'cmp-date-title';
        dateTitle.textContent = date;
        dateBlock.appendChild(dateTitle);

        Object.entries(scheduleData[date]).forEach(([subj, topics]) => {
            const subjEl = document.createElement('div');
            subjEl.className = 'cmp-subject';

            const subjTitle = document.createElement('div');
            subjTitle.className   = 'cmp-subject-name';
            subjTitle.textContent = subj;
            subjEl.appendChild(subjTitle);

            Object.entries(topics).forEach(([topic, tdata]) => {
                // BUG FIX 5: new schema is {hours, subtopics}; old schema is plain integer
                const hours = typeof tdata === 'object' ? tdata.hours : tdata;

                const row = document.createElement('div');
                row.className = 'cmp-topic-row';

                const t = document.createElement('span');
                t.textContent = topic;

                const h = document.createElement('span');
                h.className   = 'cmp-hours';
                h.textContent = `${hours}h`;

                row.appendChild(t);
                row.appendChild(h);
                subjEl.appendChild(row);
            });

            dateBlock.appendChild(subjEl);
        });

        el.appendChild(dateBlock);
    });
}

function renderComparison(oldSched, newSched) {
    renderScheduleInto('oldScheduleContent', oldSched);
    renderScheduleInto('newScheduleContent', newSched);
    document.getElementById('comparisonSection').classList.remove('hidden');
    document.getElementById('progressWrapper').style.display  = 'none';
    document.getElementById('progressActions').style.display  = 'none';
    document.getElementById('comparisonSection').scrollIntoView({ behavior: 'smooth' });
}


// ── KEEP CHOICE ──────────────────────────────────────────────

async function handleKeep(choice) {
    const btn = document.getElementById(choice === 'old' ? 'keepOldBtn' : 'keepNewBtn');
    btn.disabled    = true;
    btn.textContent = 'Saving…';
    try {
        const res = await fetch(`/keep_schedule/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ choice })
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Failed');
        window.location.href = '/schedule_page';
    } catch (err) {
        alert('Failed: ' + err.message);
        btn.disabled    = false;
        btn.textContent = 'Keep This';
    }
}

document.getElementById('keepOldBtn').addEventListener('click', () => handleKeep('old'));
document.getElementById('keepNewBtn').addEventListener('click', () => handleKeep('new'));


// ── INIT ─────────────────────────────────────────────────────

// Close all dropdowns when clicking anywhere outside
document.addEventListener('click', () => {
    document.querySelectorAll('.custom-select').forEach(s => s.classList.remove('active'));
});

renderTopicList();