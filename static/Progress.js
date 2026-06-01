/* Progress.js — fixed */

const userId = window.USER_ID;
const topicStatus = window.TOPIC_STATUS;
const pastSchedule = window.PAST_SCHEDULE;
const fullSchedule = window.FULL_SCHEDULE;
const todayStr = window.TODAY_STR;
const isAdmin = window.IS_ADMIN || false;
const schedulePreferences = topicStatus.schedule_preferences || {};

function formatDateToCustom(dateStr) {
    try {
        const [day, month, year] = dateStr.split('-').map(Number);
        const dateObj = new Date(year, month - 1, day);
        if (isNaN(dateObj.getTime())) return dateStr;
        const dayName = dateObj.toLocaleDateString('en-US', { weekday: 'short' });
        const monthName = dateObj.toLocaleDateString('en-US', { month: 'long' });
        const shortYear = String(year).slice(-2);
        return `${dayName} ${day} ${monthName} ${shortYear}`;
    } catch (e) {
        return dateStr;
    }
}

function sanitizeField(s, maxLen) {
    if (typeof s !== 'string') return '';
    s = s.replace(/[\x00-\x1f\x7f]/g, '');
    s = s.replace(/<[^>]+>/g, '');
    return s.substring(0, maxLen).trim();
}

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
    const card = document.getElementById('progressTopicsCard');
    const subjects = topicStatus.Subjects || {};
    card.innerHTML = '';

    if (!Object.keys(subjects).length) {
        card.innerHTML = '<p style="color:#666;padding:20px;">No topic data found. Generate a schedule first.</p>';
        return;
    }

    Object.entries(subjects).forEach(([subjName, topics]) => {

        const block = document.createElement('div');
        block.className = 'subject-block';

        // Header
        const header = document.createElement('div');
        header.className = 'subject-header';

        const nameEl = document.createElement('h2');
        nameEl.textContent = subjName;

        const examEl = document.createElement('span');
        examEl.className = 'exam-date';
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
            const subtopics = typeof tdata === 'object' ? (tdata.subtopics || []) : [];

            const key = `${subjName}||${topicName}`;
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
            nameSpan.className = 'topic-name';
            nameSpan.textContent = topicName;

            if (isOverdue) {
                const badge = document.createElement('span');
                badge.className = 'overdue-badge';
                badge.textContent = 'Scheduled';
                badge.title = 'This topic was scheduled to be studied by today';
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
                    li.textContent = s;
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
            customSelect.className = 'custom-select';
            customSelect.dataset.subject = subjName;
            customSelect.dataset.topic = topicName;

            const trigger = document.createElement('div');
            trigger.className = 'select-trigger';

            const optionsData = [
                ['0', '0% — Not Started'],
                ['25', '25% — Just Begun'],
                ['50', '50% — Halfway'],
                ['75', '75% — Almost Done'],
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
                opt.className = 'option' + (String(val) === String(currentStatus) ? ' selected' : '');
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
        const subj = sel.dataset.subject;
        const topic = sel.dataset.topic;
        if (!subjects[subj]) subjects[subj] = {};
        subjects[subj][topic] = sel.dataset.value || '0';
    });
    return subjects;
}

function renderRegenerationControls() {
    const body = document.getElementById('regenPrefBody');
    const toggle = document.getElementById('regenPrefToggle');
    const hoursList = document.getElementById('regenHoursList');
    if (!body || !toggle || !hoursList) return;

    if (document.getElementById('regenPreferenceNote')) {
        document.getElementById('regenPreferenceNote').value = schedulePreferences.preference_note || '';
    }

    toggle.addEventListener('click', () => {
        const isHidden = body.classList.toggle('hidden');
        toggle.textContent = isHidden ? 'Customize' : 'Hide controls';
    });

    const days = topicStatus.study_days || {};
    hoursList.innerHTML = '';
    Object.entries(days).forEach(([date, value]) => {
        const row = document.createElement('label');
        row.className = 'regen-hour-row';
        row.dataset.date = date;

        const dateEl = document.createElement('span');
        dateEl.className = 'regen-hour-date';
        dateEl.textContent = formatDateToCustom(date);

        const input = document.createElement('input');
        input.className = 'regen-hour-input';
        input.type = 'number';
        input.min = '0';
        input.max = '24';
        input.step = '1';
        input.value = value && value !== 'none' ? value : '0';
        input.setAttribute('aria-label', `Study hours for ${date}`);

        row.appendChild(dateEl);
        row.appendChild(input);
        hoursList.appendChild(row);
    });

    if (!Object.keys(days).length) {
        hoursList.innerHTML = '<p style="color:#777;font-size:13px;">No study days are available yet.</p>';
    }

    document.getElementById('applyHoursPreset')?.addEventListener('click', () => {
        const valInput = document.getElementById('weekdayHoursInput');
        const hVal = valInput ? valInput.value : '3';
        document.querySelectorAll('.regen-hour-row').forEach(row => {
            const [day, month, year] = row.dataset.date.split('-').map(Number);
            const weekday = new Date(year, month - 1, day).getDay();
            row.querySelector('input').value = weekday === 0 ? '1' : hVal;
        });
        StudyFlowToast.info('Study hours updated. You can fine-tune any day.');
    });
}

function collectRegenerationInputs() {
    const studyDays = {};
    document.querySelectorAll('.regen-hour-row').forEach(row => {
        const input = row.querySelector('input');
        const value = Math.max(0, Math.min(24, parseInt(input.value || '0', 10)));
        studyDays[row.dataset.date] = String(Number.isFinite(value) ? value : 0);
    });

    const prefLimit = window.PREF_LIMIT || 200;
    const rawNote = document.getElementById('regenPreferenceNote')?.value || '';
    return {
        study_days: studyDays,
        schedule_preferences: {
            intensity: 'balanced',
            block_length: '1-2',
            preference_note: sanitizeField(rawNote, prefLimit)
        }
    };
}


// ── NOTICE BANNER (defined once) ─────────────────────────────
// BUG FIX 4: was defined twice; removed the duplicate

function showNoticeBanner(notice) {
    const existing = document.getElementById('llmNoticeBanner');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.id = 'llmNoticeBanner';
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
        pre.style.cssText = 'font-size:12px;color:#aaa;margin-top:8px;white-space:pre-wrap;';
        pre.textContent = notice.reasons.join('\n\n');
        details.appendChild(summary);
        details.appendChild(pre);
        banner.appendChild(details);
    }

    const wrapper = document.getElementById('progressWrapper');
    wrapper.parentNode.insertBefore(banner, wrapper);
}


// ── LOADER (Multi-stage) ───────────────────────────────────────

const progressLoaderEl = document.getElementById('progressLoader');
const progressErrorEl = document.getElementById('progressError');

const saveStages = [
    { label: 'Saving progress', detail: 'Capturing your latest topic percentages.' },
    { label: 'Updating study record', detail: 'Keeping your progress ready for the next plan.' },
    { label: 'Progress saved', detail: 'Your check-in is up to date.' }
];

const regenStages = [
    { label: 'Saving your progress', detail: 'Applying topic updates and custom study hours.' },
    { label: 'Understanding preferences', detail: 'Reading intensity, available time, and schedule notes.' },
    { label: 'Rescheduling topics', detail: 'Rebalancing your plan around exams and remaining work.' },
    { label: 'Optimizing study flow', detail: 'Choosing a practical order for the next schedule.' },
    { label: 'Preparing your new schedule', detail: 'Building the comparison so you stay in control.' }
];

let activeLoader = null;
let progressStageTimers = [];

function showLoader(type) {
    document.getElementById('progressActions').style.display = 'none';
    progressErrorEl.innerHTML = '';

    const stages = type === 'save' ? saveStages : regenStages;
    const options = type === 'save'
        ? {
            checkpoints: ['Progress received', 'Record updated'],
            idleMessages: ['Saving the latest check-in so future schedules stay accurate.']
        }
        : {
            checkpoints: [
                'Progress saved',
                'Preferences applied',
                'Schedule rebuilt',
                'Comparison ready'
            ],
            idleMessages: [
                'Regeneration is still running. StudyFlow is rebalancing the plan carefully.',
                'Your custom hours are being used to avoid an unrealistic schedule.',
                'We are preparing a side-by-side comparison so you can choose confidently.'
            ]
        };
    activeLoader = new StudyFlowLoader(progressLoaderEl, stages, options);
    activeLoader.start();
    clearProgressTimers();

    if (type === 'save') {
        progressStageTimers = [setTimeout(() => activeLoader?.advance(1), 1200)];
    } else {
        progressStageTimers = [
            setTimeout(() => activeLoader?.advance(1), 1800),
            setTimeout(() => activeLoader?.advance(2), 5000),
            setTimeout(() => activeLoader?.advance(3), 9000),
            setTimeout(() => activeLoader?.advance(4), 14000)
        ];
    }
}

function hideLoader() {
    clearProgressTimers();
    if (activeLoader) {
        activeLoader.reset();
        activeLoader = null;
    }
    document.getElementById('progressActions').style.display = 'flex';
}

function clearProgressTimers() {
    progressStageTimers.forEach(timer => clearTimeout(timer));
    progressStageTimers = [];
}


// ── SAVE PROGRESS ────────────────────────────────────────

document.getElementById('saveProgressBtn').addEventListener('click', async () => {
    const btn = document.getElementById('saveProgressBtn');
    btn.classList.add('sf-btn-disabled');
    showLoader('save');
    try {
        const res = await fetch(`/update_progress/${userId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                Subjects: collectUpdatedSubjects(),
                ...collectRegenerationInputs()
            })
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Failed');
        hideLoader();
        btn.classList.remove('sf-btn-disabled');
        StudyFlowToast.success('Progress saved successfully');
    } catch (err) {
        hideLoader();
        btn.classList.remove('sf-btn-disabled');
        StudyFlowError.show(progressErrorEl, {
            title: 'Save Failed',
            what: 'Your progress couldn\'t be saved.',
            why: err.message || 'A connection or server issue occurred.',
            action: 'Check your connection and try again.',
            retryFn: () => document.getElementById('saveProgressBtn').click(),
            dismissFn: () => { }
        });
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
                const res = await fetch(`/job/${jobId}/status`);
                const data = await res.json();
                if (data.status === 'done') return resolve(data.result);
                if (data.status === 'error') return reject(new Error(data.error || 'Generation failed'));
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
    const btn = document.getElementById('regenBtn');
    btn.classList.add('sf-btn-disabled');
    showLoader('regen');
    try {
        // 1. Save progress first
        const saveRes = await fetch(`/update_progress/${userId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                Subjects: collectUpdatedSubjects(),
                ...collectRegenerationInputs()
            })
        });
        if (!saveRes.ok) throw new Error('Failed to save progress before regenerating');

        // 2. Kick off async job — returns immediately with job_id
        const regenRes = await fetch(`/regenerate_schedule/${userId}`, { method: 'POST' });
        const regenData = await regenRes.json();
        if (!regenRes.ok) {
            let msg = regenData.error || 'Regeneration failed';
            throw new Error(msg);
        }

        // Persist job_id so a page refresh can resume polling
        sessionStorage.setItem('pendingRegenJob', regenData.job_id);

        // 3. Poll until done
        const result = await pollJob(regenData.job_id);
        sessionStorage.removeItem('pendingRegenJob');

        hideLoader();
        btn.classList.remove('sf-btn-disabled');
        if (result.notice) showNoticeBanner(result.notice);
        StudyFlowToast.success('New schedule ready!');
        renderComparison(result.old_schedule, result.new_schedule);

    } catch (err) {
        hideLoader();
        btn.classList.remove('sf-btn-disabled');
        sessionStorage.removeItem('pendingRegenJob');
        StudyFlowError.show(progressErrorEl, {
            ...StudyFlowError.forGeneration(err.message),
            retryFn: () => document.getElementById('regenBtn').click(),
            dismissFn: () => { }
        });
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
        dateTitle.className = 'cmp-date-title';
        dateTitle.textContent = formatDateToCustom(date);
        dateBlock.appendChild(dateTitle);

        Object.entries(scheduleData[date]).forEach(([subj, topics]) => {
            const subjEl = document.createElement('div');
            subjEl.className = 'cmp-subject';

            const subjTitle = document.createElement('div');
            subjTitle.className = 'cmp-subject-name';
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
                h.className = 'cmp-hours';
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
    document.getElementById('progressWrapper').style.display = 'none';
    document.getElementById('progressActions').style.display = 'none';
    document.getElementById('comparisonSection').scrollIntoView({ behavior: 'smooth' });
}


// ── KEEP CHOICE ──────────────────────────────────────────────

async function handleKeep(choice) {
    const btn = document.getElementById(choice === 'old' ? 'keepOldBtn' : 'keepNewBtn');
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
        const res = await fetch(`/keep_schedule/${userId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ choice })
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Failed');
        window.location.href = '/schedule_page';
    } catch (err) {
        StudyFlowToast.error('Failed: ' + err.message);
        btn.disabled = false;
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
renderRegenerationControls();
