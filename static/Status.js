/* ═══════════════════════════════════════════════════════════
   STATUS.JS  — subtopics fixed + editable

   Schema:  state.Subjects[subj][topic] = { status: "0-100", subtopics: [...] }
   Read view:  topic name + collapsible subtopic list + custom dropdown
   Edit view:  rename topic, add/delete/rename subtopics inline
═══════════════════════════════════════════════════════════ */

let state           = JSON.parse(JSON.stringify(window.INITIAL_DATA));
let snapshot        = null;
let editMode        = false;
let userId          = null;
let isGenerating    = false;
let hasUnsavedEdits = false;


// ── HELPERS ──────────────────────────────────────────────────

function _topicData(subj, topic) {
    const raw = state.Subjects[subj][topic];
    if (typeof raw === 'object' && raw !== null) return raw;
    return { status: raw || '0', subtopics: [] };
}

function _topicStatus(subj, topic) { return _topicData(subj, topic).status || '0'; }
function _topicSubs(subj, topic)   { return _topicData(subj, topic).subtopics || []; }


// ── DATE HELPERS ─────────────────────────────────────────────

function parseDMY(str) {
    const [d, m, y] = str.split('-').map(Number);
    return new Date(y, m - 1, d);
}
function formatDMY(date) {
    return [String(date.getDate()).padStart(2,'0'),
            String(date.getMonth()+1).padStart(2,'0'),
            date.getFullYear()].join('-');
}
function dmyToInputVal(dmy) {
    if (!dmy) return '';
    const [d, m, y] = dmy.split('-');
    return `${y}-${m}-${d}`;
}
function inputValToDmy(val) {
    if (!val) return null;
    const [y, m, d] = val.split('-');
    return `${d}-${m}-${y}`;
}

function recalcStudyDays() {
    const examDates = Object.values(state.Exam_dates).filter(Boolean).map(d => parseDMY(d));
    if (!examDates.length) { state.study_days = {}; return; }
    const today    = new Date(); today.setHours(0,0,0,0);
    const lastExam = new Date(Math.max(...examDates.map(d => d.getTime())));
    const days = {}; const cur = new Date(today);
    while (cur <= lastExam) {
        const k = formatDMY(cur);
        days[k] = state.study_days[k] ?? 'none';
        cur.setDate(cur.getDate() + 1);
    }
    state.study_days = days;
}


// ── HOURS PANEL ──────────────────────────────────────────────

function renderHoursPanel() {
    const list = document.getElementById('hoursList');
    list.innerHTML = '';
    const dates = Object.keys(state.study_days);
    if (!dates.length) {
        list.innerHTML = '<p style="color:#555;font-size:13px;padding:10px;">No study days — add an exam date.</p>';
        return;
    }
    dates.forEach(date => {
        const row = document.createElement('div');
        row.className    = 'date-row';
        row.dataset.date = date;

        const label = document.createElement('span');
        label.textContent = date;

        // Stepper widget
        const stepper = document.createElement('div');
        stepper.className = 'hours-stepper';

        const minusBtn = document.createElement('button');
        minusBtn.className   = 'step-btn';
        minusBtn.textContent = '−';

        const input = document.createElement('input');
        input.type        = 'number';
        input.placeholder = '0';
        input.min         = '0';
        input.max         = '24';
        const stored = state.study_days[date];
        if (stored && stored !== 'none') input.value = stored;

        const plusBtn = document.createElement('button');
        plusBtn.className   = 'step-btn';
        plusBtn.textContent = '+';

        minusBtn.addEventListener('click', () => {
            const v = Math.max(0, parseInt(input.value || 0) - 1);
            input.value = v; state.study_days[date] = String(v);
        });
        plusBtn.addEventListener('click', () => {
            const v = Math.min(24, parseInt(input.value || 0) + 1);
            input.value = v; state.study_days[date] = String(v);
        });
        input.addEventListener('input', () => {
            state.study_days[date] = input.value.trim() || '0';
        });

        stepper.appendChild(minusBtn);
        stepper.appendChild(input);
        stepper.appendChild(plusBtn);
        row.appendChild(label);
        row.appendChild(stepper);
        list.appendChild(row);
    });
}


// ── TOPICS PANEL ─────────────────────────────────────────────

function renderTopicsPanel() {
    const card = document.getElementById('topicsCard');
    card.innerHTML = '';
    const subjects = Object.keys(state.Subjects);
    if (!subjects.length) {
        card.innerHTML = '<p style="color:#555;padding:20px;text-align:center;">No subjects. Click Edit to add one.</p>';
        if (editMode) card.appendChild(buildAddSubjectRow());
        return;
    }
    subjects.forEach(s => card.appendChild(buildSubjectBlock(s)));
    if (editMode) card.appendChild(buildAddSubjectRow());
}

function buildSubjectBlock(subjectName) {
    const block = document.createElement('div');
    block.className = 'subject-block';
    block.appendChild(buildSubjectHeader(subjectName));
    block.appendChild(buildTopicsList(subjectName));
    if (editMode) block.appendChild(buildAddTopicRow(subjectName));
    return block;
}

function buildSubjectHeader(subjectName) {
    const header = document.createElement('div');
    header.className = 'subject-header';

    if (!editMode) {
        const nameEl = document.createElement('h2');
        nameEl.textContent = subjectName;
        const dateEl = document.createElement('span');
        dateEl.className   = 'exam-date';
        dateEl.textContent = state.Exam_dates[subjectName] || 'No exam date';
        header.appendChild(nameEl);
        header.appendChild(dateEl);
    } else {
        const leftCol = document.createElement('div');
        leftCol.className = 'edit-col';
        leftCol.style.cssText = 'display:flex;flex-direction:column;flex:1;gap:6px;margin-right:12px;';

        const nameInput = document.createElement('input');
        nameInput.className   = 'edit-inline-input edit-name-input';
        nameInput.value       = subjectName;
        nameInput.addEventListener('change', () => renameSubject(subjectName, nameInput.value.trim()));

        const dateRow   = document.createElement('div');
        dateRow.className = 'edit-date-row';
        const dateLabel = document.createElement('label');
        dateLabel.className   = 'edit-date-label';
        dateLabel.textContent = 'Exam date:';
        const dateInput = document.createElement('input');
        dateInput.type      = 'date';
        dateInput.className = 'edit-date-input';
        dateInput.value     = dmyToInputVal(state.Exam_dates[subjectName] || '');
        dateInput.addEventListener('change', () => {
            const dmy = inputValToDmy(dateInput.value);
            if (dmy) state.Exam_dates[subjectName] = dmy;
            else delete state.Exam_dates[subjectName];
            hasUnsavedEdits = true;
        });
        dateRow.appendChild(dateLabel);
        dateRow.appendChild(dateInput);
        leftCol.appendChild(nameInput);
        leftCol.appendChild(dateRow);

        const delBtn = document.createElement('button');
        delBtn.className   = 'edit-delete-btn';
        delBtn.textContent = '🗑 Delete';
        delBtn.addEventListener('click', () => deleteSubject(subjectName));

        header.appendChild(leftCol);
        header.appendChild(delBtn);
    }
    return header;
}

function buildTopicsList(subjectName) {
    const wrapper = document.createElement('div');
    wrapper.className = 'topics';
    Object.keys(state.Subjects[subjectName] || {}).forEach(t =>
        wrapper.appendChild(buildTopicRow(subjectName, t)));
    return wrapper;
}

function buildTopicRow(subjectName, topicName) {
    const row = document.createElement('div');
    row.className = 'topic-row';

    // Always read using helper to handle both old (string) and new (object) schema
    const subtopics    = _topicSubs(subjectName, topicName);
    const currentValue = _topicStatus(subjectName, topicName);

    if (!editMode) {
        // ── READ VIEW ──
        const leftDiv = document.createElement('div');
        leftDiv.style.flex = '1';

        const nameEl = document.createElement('span');
        nameEl.className   = 'topic-name';
        nameEl.textContent = topicName;
        leftDiv.appendChild(nameEl);

        // Subtopics collapsible toggle
        if (subtopics.length) {
            const toggleRow = document.createElement('div');
            toggleRow.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:3px;';

            const toggle = document.createElement('span');
            toggle.style.cssText = 'font-size:11px;color:#7b2ff7;cursor:pointer;user-select:none;';
            toggle.textContent   = `▸ ${subtopics.length} subtopic${subtopics.length > 1 ? 's' : ''}`;

            const subList = document.createElement('div');
            subList.style.cssText = 'display:none;font-size:12px;color:#666;padding:4px 0 0 2px;line-height:1.6;';
            subList.textContent   = subtopics.join(' · ');

            toggle.addEventListener('click', e => {
                e.stopPropagation();
                const open = subList.style.display === 'block';
                subList.style.display = open ? 'none' : 'block';
                toggle.textContent = open
                    ? `▸ ${subtopics.length} subtopic${subtopics.length > 1 ? 's' : ''}`
                    : `▾ ${subtopics.length} subtopic${subtopics.length > 1 ? 's' : ''}`;
            });

            toggleRow.appendChild(toggle);
            leftDiv.appendChild(toggleRow);
            leftDiv.appendChild(subList);
        }

        row.appendChild(leftDiv);

        // Custom dropdown
        const customSelect = document.createElement('div');
        customSelect.className       = 'custom-select';
        customSelect.dataset.subject = subjectName;
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

        const initOpt = optionsData.find(o => o[0] === currentValue);
        trigger.innerHTML = `<span class="value">${initOpt ? initOpt[1] : optionsData[0][1]}</span>`;
        customSelect.dataset.value = currentValue;

        const optionsContainer = document.createElement('div');
        optionsContainer.className = 'select-options';

        optionsData.forEach(([val, label]) => {
            const opt = document.createElement('div');
            opt.className   = 'option' + (val === currentValue ? ' selected' : '');
            opt.textContent = label;
            opt.dataset.value = val;
            opt.addEventListener('click', e => {
                e.stopPropagation();
                // Update state — preserve subtopics
                const existing = state.Subjects[subjectName][topicName];
                if (typeof existing === 'object' && existing !== null) {
                    existing.status = val;
                } else {
                    state.Subjects[subjectName][topicName] = { status: val, subtopics: [] };
                }
                trigger.querySelector('.value').textContent = label;
                customSelect.dataset.value = val;
                customSelect.classList.remove('active');
                optionsContainer.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
                opt.classList.add('selected');
            });
            optionsContainer.appendChild(opt);
        });

        trigger.addEventListener('click', e => {
            e.stopPropagation();
            document.querySelectorAll('.custom-select').forEach(s => {
                if (s !== customSelect) s.classList.remove('active');
            });
            customSelect.classList.toggle('active');
        });

        customSelect.appendChild(trigger);
        customSelect.appendChild(optionsContainer);
        row.appendChild(customSelect);

    } else {
        // ── EDIT VIEW ──
        row.style.flexDirection = 'column';
        row.style.alignItems    = 'stretch';
        row.style.gap           = '8px';

        // Topic name + delete button row
        const topRow = document.createElement('div');
        topRow.style.cssText = 'display:flex;gap:8px;align-items:center;';

        const nameInput = document.createElement('input');
        nameInput.className   = 'edit-inline-input';
        nameInput.value       = topicName;
        nameInput.addEventListener('change', () =>
            renameTopic(subjectName, topicName, nameInput.value.trim()));

        const delBtn = document.createElement('button');
        delBtn.className   = 'edit-delete-topic-btn';
        delBtn.textContent = '✕';
        delBtn.addEventListener('click', () => deleteTopic(subjectName, topicName));

        topRow.appendChild(nameInput);
        topRow.appendChild(delBtn);
        row.appendChild(topRow);

        // Subtopics edit section
        const subSection = document.createElement('div');
        subSection.style.cssText = 'padding-left:12px;display:flex;flex-direction:column;gap:4px;';

        subtopics.forEach((sub, idx) => {
            const subRow = document.createElement('div');
            subRow.style.cssText = 'display:flex;gap:6px;align-items:center;';

            const subInput = document.createElement('input');
            subInput.className   = 'edit-inline-input';
            subInput.value       = sub;
            subInput.style.fontSize = '13px';
            subInput.addEventListener('change', () =>
                renameSubtopic(subjectName, topicName, idx, subInput.value.trim()));

            const subDel = document.createElement('button');
            subDel.className   = 'edit-delete-topic-btn';
            subDel.textContent = '✕';
            subDel.style.cssText += 'width:22px;height:22px;font-size:11px;';
            subDel.addEventListener('click', () =>
                deleteSubtopic(subjectName, topicName, idx));

            subRow.appendChild(subInput);
            subRow.appendChild(subDel);
            subSection.appendChild(subRow);
        });

        // Add subtopic row
        const addSubRow = document.createElement('div');
        addSubRow.style.cssText = 'display:flex;gap:6px;align-items:center;margin-top:2px;';

        const addSubInput = document.createElement('input');
        addSubInput.className   = 'edit-inline-input';
        addSubInput.placeholder = '+ Add subtopic…';
        addSubInput.style.fontSize = '12px';

        const addSubBtn = document.createElement('button');
        addSubBtn.className   = 'edit-add-btn';
        addSubBtn.textContent = 'Add';
        addSubBtn.style.cssText += 'font-size:12px;padding:5px 12px;';
        addSubBtn.addEventListener('click', () => {
            const name = addSubInput.value.trim();
            if (name) { addSubtopic(subjectName, topicName, name); addSubInput.value = ''; }
        });
        addSubInput.addEventListener('keydown', e => { if (e.key === 'Enter') addSubBtn.click(); });

        addSubRow.appendChild(addSubInput);
        addSubRow.appendChild(addSubBtn);
        subSection.appendChild(addSubRow);
        row.appendChild(subSection);
    }

    return row;
}

function buildAddTopicRow(subjectName) {
    const row = document.createElement('div');
    row.className = 'edit-add-row';
    const input   = document.createElement('input');
    input.className   = 'edit-inline-input';
    input.placeholder = 'New topic name…';
    const btn = document.createElement('button');
    btn.className   = 'edit-add-btn';
    btn.textContent = '+ Add Topic';
    btn.addEventListener('click', () => { const n = input.value.trim(); if (n) { addTopic(subjectName, n); input.value = ''; } });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });
    row.appendChild(input); row.appendChild(btn);
    return row;
}

function buildAddSubjectRow() {
    const row = document.createElement('div');
    row.className = 'edit-add-subject-row';
    const input   = document.createElement('input');
    input.className   = 'edit-inline-input';
    input.placeholder = 'New subject name…';
    const btn = document.createElement('button');
    btn.className   = 'edit-add-btn';
    btn.textContent = '+ Add Subject';
    btn.addEventListener('click', () => { const n = input.value.trim(); if (n) { addSubject(n); input.value = ''; } });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });
    row.appendChild(input); row.appendChild(btn);
    return row;
}


// ── STATE MUTATIONS ───────────────────────────────────────────

function renameSubject(old, name) {
    if (!name || name === old) return;
    if (state.Subjects[name]) { alert(`"${name}" already exists.`); return; }
    state.Subjects[name] = state.Subjects[old]; delete state.Subjects[old];
    if (state.Exam_dates[old]) { state.Exam_dates[name] = state.Exam_dates[old]; delete state.Exam_dates[old]; }
    hasUnsavedEdits = true; renderTopicsPanel();
}
function deleteSubject(name) {
    if (!confirm(`Delete "${name}" and all its topics?`)) return;
    delete state.Subjects[name]; delete state.Exam_dates[name];
    hasUnsavedEdits = true; recalcStudyDays(); renderTopicsPanel(); renderHoursPanel();
}
function addSubject(name) {
    if (state.Subjects[name]) { alert(`"${name}" already exists.`); return; }
    state.Subjects[name] = {}; hasUnsavedEdits = true; renderTopicsPanel();
}
function renameTopic(subj, old, name) {
    if (!name || name === old) return;
    if (state.Subjects[subj][name] !== undefined) { alert(`"${name}" already exists.`); return; }
    const rebuilt = {};
    Object.keys(state.Subjects[subj]).forEach(k => { rebuilt[k === old ? name : k] = state.Subjects[subj][k]; });
    state.Subjects[subj] = rebuilt;
    hasUnsavedEdits = true; renderTopicsPanel();
}
function deleteTopic(subj, topic) {
    delete state.Subjects[subj][topic]; hasUnsavedEdits = true; renderTopicsPanel();
}
function addTopic(subj, name) {
    if (state.Subjects[subj][name] !== undefined) { alert(`"${name}" exists.`); return; }
    state.Subjects[subj][name] = { status: 'none', subtopics: [] };
    hasUnsavedEdits = true; renderTopicsPanel();
}
function renameSubtopic(subj, topic, idx, name) {
    if (!name) return;
    const t = state.Subjects[subj][topic];
    if (typeof t === 'object') t.subtopics[idx] = name;
    hasUnsavedEdits = true;
}
function deleteSubtopic(subj, topic, idx) {
    const t = state.Subjects[subj][topic];
    if (typeof t === 'object') t.subtopics.splice(idx, 1);
    hasUnsavedEdits = true; renderTopicsPanel();
}
function addSubtopic(subj, topic, name) {
    const t = state.Subjects[subj][topic];
    if (typeof t === 'object') { t.subtopics.push(name); }
    else { state.Subjects[subj][topic] = { status: t || '0', subtopics: [name] }; }
    hasUnsavedEdits = true; renderTopicsPanel();
}


// ── EDIT MODE ─────────────────────────────────────────────────

function enterEditMode() {
    snapshot = JSON.parse(JSON.stringify(state));
    editMode = true; hasUnsavedEdits = false;
    document.getElementById('editToggleBtn').classList.add('hidden');
    document.getElementById('editActions').classList.remove('hidden');
    renderTopicsPanel();
}
function exitEditMode() {
    editMode = false;
    document.getElementById('editToggleBtn').classList.remove('hidden');
    document.getElementById('editActions').classList.add('hidden');
    renderTopicsPanel();
}

document.getElementById('editToggleBtn').addEventListener('click', enterEditMode);
document.getElementById('cancelEditsBtn').addEventListener('click', () => {
    if (hasUnsavedEdits && !confirm('Discard all unsaved changes?')) return;
    state = JSON.parse(JSON.stringify(snapshot)); snapshot = null; hasUnsavedEdits = false;
    exitEditMode(); renderHoursPanel();
});


// ── SAVE EDITS ────────────────────────────────────────────────

async function saveEdits() {
    recalcStudyDays();

    // Clean payload: preserve subtopics, reset status to 'none' for storage
    const cleanSubjects = {};
    Object.entries(state.Subjects).forEach(([subj, topics]) => {
        cleanSubjects[subj] = {};
        Object.entries(topics).forEach(([t, tdata]) => {
            cleanSubjects[subj][t] = {
                status:    'none',
                subtopics: typeof tdata === 'object' ? (tdata.subtopics || []) : []
            };
        });
    });

    const payload = { Exam_dates: state.Exam_dates, Subjects: cleanSubjects, study_days: state.study_days };
    const res = await fetch(`/save_extracted/${userId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!res.ok) { const d = await res.json(); throw new Error(d.error || 'Failed to save'); }
    state.Subjects = cleanSubjects; hasUnsavedEdits = false;
}

document.getElementById('saveEditsBtn').addEventListener('click', async () => {
    const btn = document.getElementById('saveEditsBtn');
    btn.disabled = true; btn.textContent = '…Saving';
    try { await saveEdits(); renderHoursPanel(); exitEditMode(); }
    catch (err) { alert('Save failed: ' + err.message); }
    finally { btn.disabled = false; btn.textContent = '✓ Save Changes'; }
});


// ── COLLECT GENERATE PAYLOAD ──────────────────────────────────

function collectPayload() {
    const examDates = {}; const subjects = {};

    document.querySelectorAll('.custom-select').forEach(sel => {
        const subj  = sel.dataset.subject;
        const topic = sel.dataset.topic;
        if (!subjects[subj]) subjects[subj] = {};
        // Preserve subtopics from state when building payload
        const existing = (state.Subjects[subj] || {})[topic];
        subjects[subj][topic] = {
            status:    sel.dataset.value || '0',
            subtopics: typeof existing === 'object' ? (existing.subtopics || []) : []
        };
    });

    Object.assign(examDates, state.Exam_dates);

    const studyDays = {};
    document.querySelectorAll('#hoursList .date-row').forEach(row => {
        const date  = row.dataset.date;
        const input = row.querySelector('input');
        studyDays[date] = input ? (input.value.trim() || '0') : '0';
    });

    return { Exam_dates: examDates, Subjects: subjects, study_days: studyDays };
}


// ── LOADING ───────────────────────────────────────────────────

const loaderMessages = ['Building your schedule…','Prioritising topics…','Optimising…','Almost ready…'];
let loaderInterval = null;

function startLoader() {
    document.getElementById('generateBtn').style.display = 'none';
    document.getElementById('statusLoader').style.display = 'block';
    document.getElementById('editToolbar').style.display = 'none';
    let i = 0;
    document.getElementById('statusLoaderMsg').textContent = loaderMessages[0];
    loaderInterval = setInterval(() => {
        i = (i+1) % loaderMessages.length;
        document.getElementById('statusLoaderMsg').textContent = loaderMessages[i];
    }, 4000);
}
function stopLoader() {
    clearInterval(loaderInterval);
    document.getElementById('statusLoader').style.display = 'none';
    document.getElementById('generateBtn').style.display = 'block';
    document.getElementById('editToolbar').style.display = 'flex';
}


// ── GENERATE ─────────────────────────────────────────────────

document.getElementById('generateBtn').addEventListener('click', async () => {
    if (isGenerating) return;
    if (editMode || hasUnsavedEdits) {
        try { await saveEdits(); if (editMode) exitEditMode(); }
        catch (err) { alert('Could not save edits: ' + err.message); return; }
    }
    isGenerating = true; startLoader();
    try {
        const payload = collectPayload();
        const saveRes = await fetch(`/submit_status/${userId}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!saveRes.ok) throw new Error('Failed to save status');
        const genRes = await fetch(`/generate_schedule/${userId}`, { method: 'POST' });
        if (!genRes.ok) { const d = await genRes.json(); throw new Error(d.error || 'Failed'); }
        window.location.href = '/schedule_page';
    } catch (err) {
        alert('Failed to generate schedule: ' + err.message);
        stopLoader(); isGenerating = false;
    }
});


// ── INIT ─────────────────────────────────────────────────────

async function init() {
    const res  = await fetch('/me');
    const data = await res.json();
    userId = data.id;
    document.addEventListener('click', () => {
        document.querySelectorAll('.custom-select').forEach(s => s.classList.remove('active'));
    });
    renderTopicsPanel();
    renderHoursPanel();
}

init();