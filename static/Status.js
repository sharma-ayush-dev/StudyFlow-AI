/* ═══════════════════════════════════════════════════════════
   STATUS.JS

   Architecture:
   - `state`    : single source of truth (mutated by all edit ops)
   - `snapshot` : copy taken when edit mode opens (used for cancel)
   - All rendering reads from `state` — nothing reads the DOM for data

   Edit mode:
   - Toggled by the toolbar button
   - In edit mode: subject names / exam dates become inputs,
     add/delete buttons appear for subjects and topics
   - "Save Changes" → POST /save_extracted/<id> → recalc study_days → re-render hours
   - "Cancel"       → restore snapshot, exit edit mode

   Generate flow:
   1. If unsaved edits exist → auto-save first
   2. POST /submit_status/<id>   with { Exam_dates, Subjects(%), study_days(hrs) }
   3. POST /generate_schedule/<id>
   4. Navigate to /schedule_page
═══════════════════════════════════════════════════════════ */

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────

let state = JSON.parse(JSON.stringify(window.INITIAL_DATA));  // deep copy
let snapshot = null;    // pre-edit snapshot for cancel
let editMode = false;
let userId = null;
let isGenerating = false;
let hasUnsavedEdits = false;


// ─────────────────────────────────────────────
// DATE HELPERS  (all dates are DD-MM-YYYY)
// ─────────────────────────────────────────────

function parseDMY(str) {
    // "DD-MM-YYYY"  →  Date object
    const [d, m, y] = str.split('-').map(Number);
    return new Date(y, m - 1, d);
}

function formatDMY(date) {
    const d = String(date.getDate()).padStart(2, '0');
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const y = date.getFullYear();
    return `${d}-${m}-${y}`;
}

// Convert DD-MM-YYYY  →  YYYY-MM-DD  for <input type="date">
function dmyToInputVal(dmy) {
    if (!dmy) return '';
    const [d, m, y] = dmy.split('-');
    return `${y}-${m}-${d}`;
}

// Convert YYYY-MM-DD (from <input type="date">)  →  DD-MM-YYYY
function inputValToDmy(val) {
    if (!val) return null;
    const [y, m, d] = val.split('-');
    return `${d}-${m}-${y}`;
}

function recalcStudyDays() {
    // Rebuild study_days from today → last exam date
    const examDates = Object.values(state.Exam_dates)
        .filter(Boolean)
        .map(d => parseDMY(d));

    if (!examDates.length) {
        state.study_days = {};
        return;
    }

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const lastExam = new Date(Math.max(...examDates.map(d => d.getTime())));

    const days = {};
    const cur = new Date(today);
    while (cur <= lastExam) {
        const key = formatDMY(cur);
        // Preserve any hours the user already entered
        days[key] = state.study_days[key] ?? 'none';
        cur.setDate(cur.getDate() + 1);
    }
    state.study_days = days;
}


// ─────────────────────────────────────────────
// RENDER — HOURS PANEL  (right card)
// ─────────────────────────────────────────────

function renderHoursPanel() {
    const list = document.getElementById('hoursList');
    list.innerHTML = '';

    const dates = Object.keys(state.study_days);
    if (!dates.length) {
        list.innerHTML = '<p style="color:#666;font-size:14px;padding:10px 0;">No study days found. Add an exam date first.</p>';
        return;
    }

    dates.forEach(date => {
        const row = document.createElement('div');
        row.className = 'date-row';
        row.dataset.date = date;

        const label = document.createElement('span');
        label.textContent = date;

        // ── CUSTOM STEPPER ──
        const stepper = document.createElement('div');
        stepper.className = 'hours-stepper';

        const btnMinus = document.createElement('button');
        btnMinus.className = 'step-btn';
        btnMinus.type = 'button'; // Prevent any form submission
        btnMinus.innerHTML = '&minus;'; // Clean look

        const input = document.createElement('input');
        input.type = 'number';
        input.value = '0';
        input.min = '0';
        input.max = '24';

        const btnPlus = document.createElement('button');
        btnPlus.className = 'step-btn';
        btnPlus.type = 'button';
        btnPlus.innerHTML = '&plus;';

        // Initial value
        const stored = state.study_days[date];
        if (stored && stored !== 'none') {
            input.value = stored;
        } else {
            input.value = '0';
            state.study_days[date] = '0';
        }

        // Logic
        btnMinus.onclick = (e) => {
            e.stopPropagation();
            let val = parseInt(input.value) || 0;
            if (val > 0) {
                val--;
                input.value = val;
                state.study_days[date] = String(val);
                hasUnsavedEdits = true;
            }
        };

        btnPlus.onclick = (e) => {
            e.stopPropagation();
            let val = parseInt(input.value) || 0;
            if (val < 24) {
                val++;
                input.value = val;
                state.study_days[date] = String(val);
                hasUnsavedEdits = true;
            }
        };

        stepper.appendChild(btnMinus);
        stepper.appendChild(input);
        stepper.appendChild(btnPlus);

        row.appendChild(label);
        row.appendChild(stepper);
        list.appendChild(row);
    });
}


// ─────────────────────────────────────────────
// RENDER — TOPICS PANEL  (left card)
// ─────────────────────────────────────────────

function renderTopicsPanel() {
    const card = document.getElementById('topicsCard');
    card.innerHTML = '';

    const subjects = Object.keys(state.Subjects);

    if (!subjects.length) {
        card.innerHTML = '<p style="color:#666;padding:20px;text-align:center;">No subjects found. Use the Edit button to add one.</p>';
        if (editMode) card.appendChild(buildAddSubjectRow());
        return;
    }

    subjects.forEach(subjectName => {
        card.appendChild(buildSubjectBlock(subjectName));
    });

    // "Add subject" button — only visible in edit mode
    if (editMode) {
        card.appendChild(buildAddSubjectRow());
    }
}



function buildSubjectBlock(subjectName) {
    const block = document.createElement('div');
    block.className = 'subject-block';

    block.appendChild(buildSubjectHeader(subjectName));
    block.appendChild(buildTopicsList(subjectName));

    if (editMode) {
        block.appendChild(buildAddTopicRow(subjectName));
    }

    return block;
}


function buildSubjectHeader(subjectName) {
    const header = document.createElement('div');
    header.className = 'subject-header';

    if (!editMode) {
        // ── READ VIEW ──
        const nameEl = document.createElement('h2');
        nameEl.textContent = subjectName;

        const dateEl = document.createElement('span');
        dateEl.className = 'exam-date';
        dateEl.textContent = state.Exam_dates[subjectName] || 'No exam date';

        header.appendChild(nameEl);
        header.appendChild(dateEl);

    } else {
        // ── EDIT VIEW ──
        const leftCol = document.createElement('div');
        leftCol.className = 'edit-col';

        // Subject name input
        const nameInput = document.createElement('input');
        nameInput.className = 'edit-inline-input edit-name-input';
        nameInput.value = subjectName;
        nameInput.placeholder = 'Subject name';
        nameInput.addEventListener('change', () => {
            renameSubject(subjectName, nameInput.value.trim());
        });

        // Exam date picker
        const dateLabel = document.createElement('label');
        dateLabel.className = 'edit-date-label';
        dateLabel.textContent = 'Exam date:';

        const dateInput = document.createElement('input');
        dateInput.type = 'text'; // Use text so flatpickr handles the UI
        dateInput.className = 'edit-date-input';
        dateInput.placeholder = 'DD-MM-YYYY';
        
        // Initialize Flatpickr for a premium experience
        flatpickr(dateInput, {
            dateFormat: "d-m-Y",
            defaultDate: state.Exam_dates[subjectName] || null,
            disableMobile: "true",
            onChange: (selectedDates, dateStr) => {
                if (dateStr) {
                    state.Exam_dates[subjectName] = dateStr;
                } else {
                    delete state.Exam_dates[subjectName];
                }
                hasUnsavedEdits = true;
            }
        });


        const dateRow = document.createElement('div');
        dateRow.className = 'edit-date-row';
        dateRow.appendChild(dateLabel);
        dateRow.appendChild(dateInput);

        leftCol.appendChild(nameInput);
        leftCol.appendChild(dateRow);

        // Delete subject button
        const delBtn = document.createElement('button');
        delBtn.className = 'edit-delete-btn';
        delBtn.textContent = '🗑 Delete Subject';
        delBtn.title = 'Remove this subject entirely';
        delBtn.addEventListener('click', () => deleteSubject(subjectName));

        header.appendChild(leftCol);
        header.appendChild(delBtn);
    }

    return header;
}


function buildTopicsList(subjectName) {
    const wrapper = document.createElement('div');
    wrapper.className = 'topics';

    const topics = Object.keys(state.Subjects[subjectName] || {});

    if (!topics.length) {
        const empty = document.createElement('p');
        empty.style.cssText = 'color:#666;font-size:14px;padding:8px 0;';
        empty.textContent = 'No topics. Add one below.';
        wrapper.appendChild(empty);
        return wrapper;
    }

    topics.forEach(topicName => {
        wrapper.appendChild(buildTopicRow(subjectName, topicName));
    });

    return wrapper;
}


function buildTopicRow(subjectName, topicName) {
    const row = document.createElement('div');
    row.className = 'topic-row';

    if (!editMode) {
        // ── READ VIEW ──
        const nameEl = document.createElement('span');
        nameEl.className = 'topic-name';
        nameEl.textContent = topicName;

        // ── CUSTOM SELECT ──
        const customSelect = document.createElement('div');
        customSelect.className = 'custom-select';
        customSelect.dataset.subject = subjectName;
        customSelect.dataset.topic = topicName;

        const trigger = document.createElement('div');
        trigger.className = 'select-trigger';

        const currentValue = state.Subjects[subjectName][topicName] || '0';
        const optionsData = [
            ['0', '0% — Not Started'],
            ['25', '25% — Just Begun'],
            ['50', '50% — Halfway'],
            ['75', '75% — Almost Done'],
            ['100', '100% — Completed']
        ];

        // Set initial trigger text
        const initOpt = optionsData.find(o => o[0] === currentValue);
        trigger.innerHTML = `<span class="value">${initOpt ? initOpt[1] : optionsData[0][1]}</span>`;
        customSelect.dataset.value = currentValue;

        const optionsContainer = document.createElement('div');
        optionsContainer.className = 'select-options';

        optionsData.forEach(([val, label]) => {
            const opt = document.createElement('div');
            opt.className = 'option' + (val === currentValue ? ' selected' : '');
            opt.textContent = label;
            opt.dataset.value = val;

            opt.addEventListener('click', (e) => {
                e.stopPropagation();
                // Update state
                state.Subjects[subjectName][topicName] = val;
                // Update UI
                trigger.querySelector('.value').textContent = label;
                customSelect.dataset.value = val;
                // Close menu
                customSelect.classList.remove('active');
                // Update selected class
                optionsContainer.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
                opt.classList.add('selected');
            });
            optionsContainer.appendChild(opt);
        });

        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            // Close all other dropdowns
            document.querySelectorAll('.custom-select').forEach(s => {
                if (s !== customSelect) s.classList.remove('active');
            });
            customSelect.classList.toggle('active');
        });

        customSelect.appendChild(trigger);
        customSelect.appendChild(optionsContainer);
        row.appendChild(nameEl);
        row.appendChild(customSelect);

    } else {
        // ── EDIT VIEW ──
        const nameInput = document.createElement('input');
        nameInput.className = 'edit-inline-input';
        nameInput.value = topicName;
        nameInput.placeholder = 'Topic name';
        nameInput.addEventListener('change', () => {
            renameTopic(subjectName, topicName, nameInput.value.trim());
        });

        const delBtn = document.createElement('button');
        delBtn.className = 'edit-delete-topic-btn';
        delBtn.textContent = '✕';
        delBtn.title = 'Delete this topic';
        delBtn.addEventListener('click', () => deleteTopic(subjectName, topicName));

        row.appendChild(nameInput);
        row.appendChild(delBtn);
    }

    return row;
}


function buildAddTopicRow(subjectName) {
    const row = document.createElement('div');
    row.className = 'edit-add-row';

    const input = document.createElement('input');
    input.className = 'edit-inline-input';
    input.placeholder = 'New topic name…';

    const addBtn = document.createElement('button');
    addBtn.className = 'edit-add-btn';
    addBtn.textContent = '+ Add Topic';
    addBtn.addEventListener('click', () => {
        const name = input.value.trim();
        if (!name) return;
        addTopic(subjectName, name);
        input.value = '';
    });

    // Also add on Enter
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') addBtn.click();
    });

    row.appendChild(input);
    row.appendChild(addBtn);
    return row;
}


function buildAddSubjectRow() {
    const row = document.createElement('div');
    row.className = 'edit-add-subject-row';

    const input = document.createElement('input');
    input.className = 'edit-inline-input';
    input.placeholder = 'New subject name…';

    const addBtn = document.createElement('button');
    addBtn.className = 'edit-add-btn';
    addBtn.textContent = '+ Add Subject';
    addBtn.addEventListener('click', () => {
        const name = input.value.trim();
        if (!name) return;
        addSubject(name);
        input.value = '';
    });

    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') addBtn.click();
    });

    row.appendChild(input);
    row.appendChild(addBtn);
    return row;
}


// ─────────────────────────────────────────────
// STATE MUTATIONS  (all call renderTopicsPanel after)
// ─────────────────────────────────────────────

function renameSubject(oldName, newName) {
    if (!newName || newName === oldName) return;
    if (state.Subjects[newName]) {
        alert(`A subject called "${newName}" already exists.`);
        return;
    }

    // Move topics
    state.Subjects[newName] = state.Subjects[oldName];
    delete state.Subjects[oldName];

    // Move exam date
    if (state.Exam_dates[oldName]) {
        state.Exam_dates[newName] = state.Exam_dates[oldName];
        delete state.Exam_dates[oldName];
    }

    hasUnsavedEdits = true;
    renderTopicsPanel();
}


function deleteSubject(name) {
    if (!confirm(`Delete "${name}" and all its topics?`)) return;
    delete state.Subjects[name];
    delete state.Exam_dates[name];
    hasUnsavedEdits = true;
    recalcStudyDays();
    renderTopicsPanel();
    renderHoursPanel();
}


function addSubject(name) {
    if (state.Subjects[name]) {
        alert(`"${name}" already exists.`);
        return;
    }
    state.Subjects[name] = {};
    hasUnsavedEdits = true;
    renderTopicsPanel();
}


function renameTopic(subjectName, oldTopic, newTopic) {
    if (!newTopic || newTopic === oldTopic) return;
    if (state.Subjects[subjectName][newTopic] !== undefined) {
        alert(`Topic "${newTopic}" already exists in this subject.`);
        return;
    }

    // Preserve insertion order by rebuilding the object
    const rebuilt = {};
    Object.keys(state.Subjects[subjectName]).forEach(k => {
        rebuilt[k === oldTopic ? newTopic : k] = state.Subjects[subjectName][k];
    });
    state.Subjects[subjectName] = rebuilt;

    hasUnsavedEdits = true;
    renderTopicsPanel();
}


function deleteTopic(subjectName, topicName) {
    delete state.Subjects[subjectName][topicName];
    hasUnsavedEdits = true;
    renderTopicsPanel();
}


function addTopic(subjectName, topicName) {
    if (state.Subjects[subjectName][topicName] !== undefined) {
        alert(`"${topicName}" already exists.`);
        return;
    }
    state.Subjects[subjectName][topicName] = 'none';
    hasUnsavedEdits = true;
    renderTopicsPanel();
}


// ─────────────────────────────────────────────
// EDIT MODE TOGGLE
// ─────────────────────────────────────────────

function enterEditMode() {
    snapshot = JSON.parse(JSON.stringify(state));   // save for cancel
    editMode = true;
    hasUnsavedEdits = false;

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
    if (hasUnsavedEdits &&
        !confirm('Discard all unsaved changes?')) return;
    state = JSON.parse(JSON.stringify(snapshot));
    snapshot = null;
    hasUnsavedEdits = false;
    exitEditMode();
    renderHoursPanel();
});


// ─────────────────────────────────────────────
// SAVE EDITS  →  POST /save_extracted/<id>
// ─────────────────────────────────────────────

async function saveEdits() {
    recalcStudyDays();

    // Rebuild a clean payload: reset all topic values to "none" for storage
    const cleanSubjects = {};
    Object.entries(state.Subjects).forEach(([subj, topics]) => {
        cleanSubjects[subj] = {};
        Object.keys(topics).forEach(t => { cleanSubjects[subj][t] = 'none'; });
    });

    const payload = {
        Exam_dates: state.Exam_dates,
        Subjects: cleanSubjects,
        study_days: state.study_days
    };

    const res = await fetch(`/save_extracted/${userId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Failed to save');
    }

    // Update state to reflect clean storage state
    state.Subjects = cleanSubjects;
    hasUnsavedEdits = false;
}


document.getElementById('saveEditsBtn').addEventListener('click', async () => {
    const btn = document.getElementById('saveEditsBtn');
    btn.disabled = true;
    btn.textContent = '…Saving';

    try {
        await saveEdits();
        renderHoursPanel();
        exitEditMode();
    } catch (err) {
        alert('Failed to save: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '✓ Save Changes';
    }
});


// ─────────────────────────────────────────────
// COLLECT GENERATE PAYLOAD
// ─────────────────────────────────────────────

function collectPayload() {
    const examDates = {};
    const subjects = {};

    // Read completion % from the custom dropdowns currently in the DOM
    document.querySelectorAll('.custom-select').forEach(sel => {
        const subj = sel.dataset.subject;
        const topic = sel.dataset.topic;
        if (!subjects[subj]) subjects[subj] = {};
        subjects[subj][topic] = sel.dataset.value || '0';
    });

    // Exam dates come from state (may differ from dropdowns)
    Object.assign(examDates, state.Exam_dates);

    // Study hours come from the hours panel inputs
    const studyDays = {};
    document.querySelectorAll('#hoursList .date-row').forEach(row => {
        const date = row.dataset.date;
        const hours = row.querySelector('input').value.trim() || '0';
        studyDays[date] = hours;
    });

    return { Exam_dates: examDates, Subjects: subjects, study_days: studyDays };
}


// ─────────────────────────────────────────────
// LOADING HELPERS
// ─────────────────────────────────────────────

const loaderMessages = [
    'Building your schedule…',
    'Prioritising topics…',
    'Optimising for exam dates…',
    'Almost ready…'
];
let loaderInterval = null;

function startLoader() {
    document.getElementById('generateBtn').style.display = 'none';
    document.getElementById('statusLoader').style.display = 'block';
    document.getElementById('editToolbar').style.display = 'none';
    let i = 0;
    document.getElementById('statusLoaderMsg').textContent = loaderMessages[0];
    loaderInterval = setInterval(() => {
        i = (i + 1) % loaderMessages.length;
        document.getElementById('statusLoaderMsg').textContent = loaderMessages[i];
    }, 4000);
}

function stopLoader() {
    clearInterval(loaderInterval);
    document.getElementById('statusLoader').style.display = 'none';
    document.getElementById('generateBtn').style.display = 'block';
    document.getElementById('editToolbar').style.display = 'flex';
}


// ─────────────────────────────────────────────
// GENERATE SCHEDULE
// ─────────────────────────────────────────────
document.getElementById('generateBtn').addEventListener('click', async () => {
    if (isGenerating) return;

    // If the user made edits but didn't hit Save, auto-save silently first
    if (editMode || hasUnsavedEdits) {
        try {
            await saveEdits();
            if (editMode) exitEditMode();
        } catch (err) {
            alert('Could not save your edits before generating: ' + err.message);
            return;
        }
    }

    isGenerating = true;
    startLoader();

    try {
        const payload = collectPayload();

        const saveRes = await fetch(`/submit_status/${userId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!saveRes.ok) throw new Error('Failed to save status');

        const genRes = await fetch(`/generate_schedule/${userId}`, {
            method: 'POST'
        });
        if (!genRes.ok) {
            const d = await genRes.json();
            throw new Error(d.error || 'Failed to generate schedule');
        }

        window.location.href = '/schedule_page';

    } catch (err) {
        console.error('Schedule generation failed:', err);
        alert('Failed to generate schedule: ' + err.message);
        stopLoader();
        isGenerating = false;
    }
});


// ─────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────

async function init() {
    // Get userId from /me
    const res = await fetch('/me');
    const data = await res.json();
    userId = data.id;

    // Close dropdowns if clicking outside
    document.addEventListener('click', () => {
        document.querySelectorAll('.custom-select').forEach(s => s.classList.remove('active'));
    });

    // Initial render
    renderTopicsPanel();
    renderHoursPanel();
}

init();