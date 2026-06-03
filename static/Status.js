/* ═══════════════════════════════════════════════════════════
   STATUS.JS  — Redesigned UX for separate subject cards & accordions
   ═══════════════════════════════════════════════════════════ */

let state = JSON.parse(JSON.stringify(window.INITIAL_DATA));
let snapshot = null;
let editMode = false;
let userId = null;
let isGenerating = false;
let hasUnsavedEdits = false;

// Accordion states
let subjectCollapsedState = {};
let activeTopicState = {};


// ── HELPERS ──────────────────────────────────────────────────

function sanitizeField(s, maxLen) {
    if (typeof s !== 'string') return '';
    s = s.replace(/[\x00-\x1f\x7f]/g, '');
    s = s.replace(/<[^>]+>/g, '');
    return s.substring(0, maxLen).trim();
}

function _topicData(subj, topic) {
    const raw = state.Subjects[subj][topic];
    if (typeof raw === 'object' && raw !== null) return raw;
    return { status: raw || '0', subtopics: [] };
}

function _topicStatus(subj, topic) { return _topicData(subj, topic).status || '0'; }
function _topicSubs(subj, topic) { return _topicData(subj, topic).subtopics || []; }


// ── DATE HELPERS ─────────────────────────────────────────────

function parseDMY(str) {
    const [d, m, y] = str.split('-').map(Number);
    return new Date(y, m - 1, d);
}
function formatDMY(date) {
    return [String(date.getDate()).padStart(2, '0'),
    String(date.getMonth() + 1).padStart(2, '0'),
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
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const lastExam = new Date(Math.max(...examDates.map(d => d.getTime())));
    const days = {}; const cur = new Date(today);
    while (cur <= lastExam) {
        const k = formatDMY(cur);
        days[k] = state.study_days[k] ?? 'none';
        cur.setDate(cur.getDate() + 1);
    }
    state.study_days = days;
}


// ── PROGRESS HELPER ──────────────────────────────────────────

function calculateSubjectProgress(subjectName) {
    const topics = state.Subjects[subjectName] || {};
    const topicKeys = Object.keys(topics);
    if (topicKeys.length === 0) return 0;
    let total = 0;
    topicKeys.forEach(t => {
        const val = _topicStatus(subjectName, t);
        total += parseInt(val) || 0;
    });
    return Math.round(total / topicKeys.length);
}


// ── HOURS PANEL ──────────────────────────────────────────────

function renderHoursPanel() {
    const list = document.getElementById('hoursList');
    list.innerHTML = '';
    
    // Sort dates chronologically
    const dates = Object.keys(state.study_days).sort((a, b) => {
        return parseDMY(a) - parseDMY(b);
    });

    if (!dates.length) {
        list.innerHTML = '<p style="color:#555;font-size:13px;padding:10px;">No study days — add an exam date.</p>';
        return;
    }
    dates.forEach(date => {
        const row = document.createElement('div');
        row.className = 'date-row';
        row.dataset.date = date;

        const label = document.createElement('div');
        label.className = 'date-label-multiline';
        
        const parsedDate = parseDMY(date);
        const dayNames = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
        const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        
        const dayOfWeek = dayNames[parsedDate.getDay()];
        const day = parsedDate.getDate();
        const month = monthNames[parsedDate.getMonth()];
        const yearStr = parsedDate.getFullYear().toString().substring(2);
        
        label.innerHTML = `<div class="date-day-name">${dayOfWeek}</div><div class="date-full-val">${day} ${month} '${yearStr}</div>`;

        // Stepper widget
        const stepper = document.createElement('div');
        stepper.className = 'hours-stepper';

        const minusBtn = document.createElement('button');
        minusBtn.className = 'step-btn';
        minusBtn.textContent = '−';

        const input = document.createElement('input');
        input.type = 'number';
        input.placeholder = '0';
        input.min = '0';
        input.max = '24';
        const stored = state.study_days[date];
        if (stored && stored !== 'none') input.value = stored;

        const plusBtn = document.createElement('button');
        plusBtn.className = 'step-btn';
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

function renderSubjectBlock(subjectName) {
    const oldBlock = document.getElementById(`subject-card-${subjectName}`);
    if (oldBlock) {
        const newBlock = buildSubjectBlock(subjectName);
        oldBlock.replaceWith(newBlock);
    }
}

function buildSubjectBlock(subjectName) {
    const block = document.createElement('div');
    block.id = `subject-card-${subjectName}`;
    block.className = 'subject-card' + (editMode ? ' edit-mode-card' : '');

    const isCollapsed = subjectCollapsedState[subjectName] ?? false;

    // Header
    const header = buildSubjectHeader(subjectName, isCollapsed);
    block.appendChild(header);

    // Content container (rendered only if not collapsed)
    if (!isCollapsed) {
        const contentDiv = document.createElement('div');
        contentDiv.className = 'subject-card-content';

        // Topics list
        const topicsList = buildTopicsList(subjectName);
        contentDiv.appendChild(topicsList);

        if (editMode) {
            contentDiv.appendChild(buildAddTopicRow(subjectName));
        }

        block.appendChild(contentDiv);
    }

    return block;
}

function buildSubjectHeader(subjectName, isCollapsed) {
    const header = document.createElement('div');
    header.className = 'subject-card-header';

    // Collapsible click trigger
    header.addEventListener('click', (e) => {
        if (e.target.closest('button') || e.target.closest('input') || e.target.closest('.custom-select')) {
            return;
        }
        subjectCollapsedState[subjectName] = !isCollapsed;
        renderSubjectBlock(subjectName);
    });

    // Arrow indicator
    const toggleIcon = document.createElement('span');
    toggleIcon.className = 'subject-collapse-toggle' + (isCollapsed ? ' collapsed' : '');
    toggleIcon.innerHTML = isCollapsed ? '▶' : '▼';
    header.appendChild(toggleIcon);

    if (!editMode) {
        // Subject titles
        const infoCol = document.createElement('div');
        infoCol.className = 'subject-info-col';

        const nameEl = document.createElement('h3');
        nameEl.className = 'subject-title-text';
        nameEl.textContent = subjectName;
        infoCol.appendChild(nameEl);

        const metaRow = document.createElement('div');
        metaRow.className = 'subject-meta-row';

        // Exam Date badge
        const dateEl = document.createElement('span');
        dateEl.className = 'exam-date-badge';
        dateEl.innerHTML = `📅 ${state.Exam_dates[subjectName] || 'No exam date'}`;
        metaRow.appendChild(dateEl);

        // Stats badge
        const topics = Object.keys(state.Subjects[subjectName] || {});
        let subCount = 0;
        topics.forEach(t => {
            subCount += _topicSubs(subjectName, t).length;
        });
        const statsEl = document.createElement('span');
        statsEl.className = 'subject-stats-badge';
        statsEl.textContent = `${topics.length} Topic${topics.length !== 1 ? 's' : ''} • ${subCount} Subtopic${subCount !== 1 ? 's' : ''}`;
        metaRow.appendChild(statsEl);

        infoCol.appendChild(metaRow);
        header.appendChild(infoCol);

        // Progress bar on the right
        const progress = calculateSubjectProgress(subjectName);
        const progressWrapper = document.createElement('div');
        progressWrapper.className = 'subject-progress-wrapper';

        const progressText = document.createElement('span');
        progressText.className = 'subject-progress-text';
        progressText.textContent = `${progress}% Complete`;

        const progressTrack = document.createElement('div');
        progressTrack.className = 'subject-progress-track';

        const progressFill = document.createElement('div');
        progressFill.className = 'subject-progress-fill';
        progressFill.style.width = `${progress}%`;

        progressTrack.appendChild(progressFill);
        progressWrapper.appendChild(progressText);
        progressWrapper.appendChild(progressTrack);
        header.appendChild(progressWrapper);

    } else {
        // Edit Mode Subject Header
        const leftCol = document.createElement('div');
        leftCol.className = 'edit-col';
        leftCol.style.cssText = 'display:flex;flex-direction:column;flex:1;gap:12px;margin-right:12px;';

        // Subject Name Field Group
        const subjectGroup = document.createElement('div');
        subjectGroup.className = 'edit-field-group';
        const subjectLabel = document.createElement('label');
        subjectLabel.className = 'edit-field-label subject-label';
        subjectLabel.innerHTML = '📚 Subject Title';
        const nameInput = document.createElement('input');
        nameInput.className = 'edit-inline-input edit-name-input';
        nameInput.placeholder = 'Subject name...';
        nameInput.value = subjectName;
        nameInput.addEventListener('change', () => renameSubject(subjectName, nameInput.value.trim()));
        subjectGroup.appendChild(subjectLabel);
        subjectGroup.appendChild(nameInput);

        // Exam Date Field Group
        const dateGroup = document.createElement('div');
        dateGroup.className = 'edit-field-group';
        const dateLabel = document.createElement('label');
        dateLabel.className = 'edit-field-label date-label';
        dateLabel.innerHTML = '📅 Exam Date';
        const dateInput = document.createElement('input');
        dateInput.type = 'text';
        dateInput.className = 'edit-date-input';
        dateInput.placeholder = 'Select date...';
        const initVal = dmyToInputVal(state.Exam_dates[subjectName] || '');
        dateInput.value = initVal;
        
        flatpickr(dateInput, {
            dateFormat: 'Y-m-d',
            defaultDate: initVal || null,
            disableMobile: "true",
            onChange: (selectedDates, dateStr) => {
                const dmy = inputValToDmy(dateStr);
                if (dmy) state.Exam_dates[subjectName] = dmy;
                else delete state.Exam_dates[subjectName];
                hasUnsavedEdits = true;
            }
        });

        dateGroup.appendChild(dateLabel);
        dateGroup.appendChild(dateInput);

        leftCol.appendChild(subjectGroup);
        leftCol.appendChild(dateGroup);

        const delBtn = document.createElement('button');
        delBtn.className = 'edit-delete-icon-btn';
        delBtn.innerHTML = '🗑️';
        delBtn.title = 'Delete Subject';
        delBtn.addEventListener('click', () => deleteSubject(subjectName));

        header.appendChild(leftCol);
        header.appendChild(delBtn);
    }
    return header;
}

function buildTopicsList(subjectName) {
    const wrapper = document.createElement('div');
    wrapper.className = 'topics' + (editMode ? ' edit-topics-container' : ' normal-topics-container');
    Object.keys(state.Subjects[subjectName] || {}).forEach(t =>
        wrapper.appendChild(buildTopicRow(subjectName, t)));
    return wrapper;
}

function buildTopicRow(subjectName, topicName) {
    const row = document.createElement('div');
    
    const subtopics = _topicSubs(subjectName, topicName);
    const currentValue = _topicStatus(subjectName, topicName);

    // Active accordion check
    const isExpanded = activeTopicState[subjectName] === topicName;
    row.className = 'topic-accordion' + (isExpanded ? ' expanded' : ' collapsed');

    // Accordion Header
    const accordionHeader = document.createElement('div');
    accordionHeader.className = 'topic-accordion-header';

    accordionHeader.addEventListener('click', (e) => {
        if (e.target.closest('.custom-select') || e.target.closest('input') || e.target.closest('button')) {
            return;
        }
        if (activeTopicState[subjectName] === topicName) {
            activeTopicState[subjectName] = null;
        } else {
            activeTopicState[subjectName] = topicName;
        }
        renderSubjectBlock(subjectName);
    });

    // Accordion header structure
    const leftSide = document.createElement('div');
    leftSide.className = 'topic-header-left';

    const arrow = document.createElement('span');
    arrow.className = 'topic-accordion-arrow';
    arrow.innerHTML = isExpanded ? '▼' : '▶';
    leftSide.appendChild(arrow);

    if (!editMode) {
        const nameEl = document.createElement('span');
        nameEl.className = 'topic-title-text';
        nameEl.textContent = topicName;
        leftSide.appendChild(nameEl);

        const badge = document.createElement('span');
        badge.className = 'topic-subtopic-count-badge';
        badge.textContent = `${subtopics.length} Subtopic${subtopics.length !== 1 ? 's' : ''}`;
        leftSide.appendChild(badge);

        accordionHeader.appendChild(leftSide);

        // Progress Dropdown Selector
        const customSelect = document.createElement('div');
        customSelect.className = 'custom-select';
        customSelect.dataset.subject = subjectName;
        customSelect.dataset.topic = topicName;

        const trigger = document.createElement('div');
        trigger.className = `select-trigger progress-${currentValue}`;

        const optionsData = [
            ['0', '0% — Not Started'],
            ['25', '25% — Just Begun'],
            ['50', '50% — Halfway'],
            ['75', '75% — Almost Done'],
            ['100', '100% — Completed']
        ];

        const initOpt = optionsData.find(o => o[0] === currentValue);
        trigger.innerHTML = `<span class="value">${initOpt ? initOpt[1] : optionsData[0][1]}</span>`;
        customSelect.dataset.value = currentValue;

        const optionsContainer = document.createElement('div');
        optionsContainer.className = 'select-options';

        optionsData.forEach(([val, label]) => {
            const opt = document.createElement('div');
            opt.className = 'option' + (val === currentValue ? ' selected' : '') + ` option-progress-${val}`;
            opt.textContent = label;
            opt.dataset.value = val;
            opt.addEventListener('click', e => {
                e.stopPropagation();
                const existing = state.Subjects[subjectName][topicName];
                if (typeof existing === 'object' && existing !== null) {
                    existing.status = val;
                } else {
                    state.Subjects[subjectName][topicName] = { status: val, subtopics: [] };
                }
                trigger.querySelector('.value').textContent = label;
                trigger.className = `select-trigger progress-${val}`;
                customSelect.dataset.value = val;
                customSelect.classList.remove('active');
                optionsContainer.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
                opt.classList.add('selected');
                
                // Real-time update overall stats
                renderSubjectBlock(subjectName);
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
        accordionHeader.appendChild(customSelect);

    } else {
        // Edit Mode Topic Header
        const nameInput = document.createElement('input');
        nameInput.className = 'edit-inline-input edit-topic-input';
        nameInput.value = topicName;
        nameInput.placeholder = 'Topic name...';
        nameInput.addEventListener('change', () => {
            const trimmed = nameInput.value.trim();
            if (trimmed && trimmed !== topicName) {
                renameTopic(subjectName, topicName, trimmed);
            }
        });
        leftSide.appendChild(nameInput);
        accordionHeader.appendChild(leftSide);

        const delBtn = document.createElement('button');
        delBtn.className = 'edit-delete-icon-btn';
        delBtn.innerHTML = '🗑️';
        delBtn.title = 'Delete Topic';
        delBtn.addEventListener('click', () => deleteTopic(subjectName, topicName));
        accordionHeader.appendChild(delBtn);
    }

    row.appendChild(accordionHeader);

    // Accordion Content (expanded only)
    if (isExpanded) {
        const accordionContent = document.createElement('div');
        accordionContent.className = 'topic-accordion-content';

        // Subtopics
        const subList = document.createElement('div');
        subList.className = 'subtopics-list-wrapper';

        subtopics.forEach((sub, idx) => {
            const subRow = document.createElement('div');
            subRow.className = 'subtopic-row';

            const bullet = document.createElement('span');
            bullet.className = 'subtopic-bullet';
            bullet.innerHTML = '•';
            subRow.appendChild(bullet);

            if (!editMode) {
                const subText = document.createElement('span');
                subText.className = 'subtopic-text';
                subText.textContent = sub;
                subRow.appendChild(subText);
            } else {
                const subInput = document.createElement('input');
                subInput.className = 'edit-inline-input edit-subtopic-input';
                subInput.value = sub;
                subInput.placeholder = 'Subtopic name...';
                subInput.addEventListener('change', () =>
                    renameSubtopic(subjectName, topicName, idx, subInput.value.trim()));
                subRow.appendChild(subInput);

                const subDel = document.createElement('button');
                subDel.className = 'edit-delete-icon-btn';
                subDel.innerHTML = '🗑️';
                subDel.title = 'Delete Subtopic';
                subDel.addEventListener('click', () =>
                    deleteSubtopic(subjectName, topicName, idx));
                subRow.appendChild(subDel);
            }
            subList.appendChild(subRow);
        });

        accordionContent.appendChild(subList);

        // Add subtopic input at bottom of list
        if (editMode) {
            const addSubRow = document.createElement('div');
            addSubRow.className = 'edit-add-subtopic-row';

            const addSubInput = document.createElement('input');
            addSubInput.className = 'edit-inline-input edit-subtopic-input';
            addSubInput.placeholder = '+ Add subtopic…';

            const addSubBtn = document.createElement('button');
            addSubBtn.className = 'edit-add-btn edit-add-subtopic-btn';
            addSubBtn.innerHTML = 'Add';
            addSubBtn.addEventListener('click', () => {
                const name = addSubInput.value.trim();
                if (name) { addSubtopic(subjectName, topicName, name); addSubInput.value = ''; }
            });
            addSubInput.addEventListener('keydown', e => { if (e.key === 'Enter') addSubBtn.click(); });

            addSubRow.appendChild(addSubInput);
            addSubRow.appendChild(addSubBtn);
            accordionContent.appendChild(addSubRow);
        }

        row.appendChild(accordionContent);
    }

    return row;
}

function buildAddTopicRow(subjectName) {
    const row = document.createElement('div');
    row.className = 'edit-add-row edit-add-topic-container';
    const input = document.createElement('input');
    input.className = 'edit-inline-input edit-topic-input';
    input.placeholder = 'New topic name…';
    const btn = document.createElement('button');
    btn.className = 'edit-add-btn';
    btn.innerHTML = '+ Add Topic';
    btn.addEventListener('click', () => { const n = input.value.trim(); if (n) { addTopic(subjectName, n); input.value = ''; } });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });
    row.appendChild(input); row.appendChild(btn);
    return row;
}

function buildAddSubjectRow() {
    const row = document.createElement('div');
    row.className = 'edit-add-subject-row';
    const input = document.createElement('input');
    input.className = 'edit-inline-input';
    input.placeholder = 'New subject name…';
    const btn = document.createElement('button');
    btn.className = 'edit-add-btn';
    btn.textContent = '+ Add Subject';
    btn.addEventListener('click', () => { const n = input.value.trim(); if (n) { addSubject(n); input.value = ''; } });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });
    row.appendChild(input); row.appendChild(btn);
    return row;
}


// ── STATE MUTATIONS ───────────────────────────────────────────

function renameSubject(old, name) {
    if (!name || name === old) return;
    if (state.Subjects[name]) { StudyFlowToast.error(`"${name}" already exists.`); return; }
    state.Subjects[name] = state.Subjects[old]; delete state.Subjects[old];
    if (state.Exam_dates[old]) { state.Exam_dates[name] = state.Exam_dates[old]; delete state.Exam_dates[old]; }
    
    subjectCollapsedState[name] = subjectCollapsedState[old]; delete subjectCollapsedState[old];
    activeTopicState[name] = activeTopicState[old]; delete activeTopicState[old];

    hasUnsavedEdits = true; renderTopicsPanel();
}
function deleteSubject(name) {
    if (!confirm(`Delete "${name}" and all its topics?`)) return;
    delete state.Subjects[name]; delete state.Exam_dates[name];
    delete subjectCollapsedState[name];
    delete activeTopicState[name];

    hasUnsavedEdits = true; recalcStudyDays(); renderTopicsPanel(); renderHoursPanel();
}
function addSubject(name) {
    if (state.Subjects[name]) { StudyFlowToast.error(`"${name}" already exists.`); return; }
    state.Subjects[name] = {};
    subjectCollapsedState[name] = false;
    activeTopicState[name] = null;

    hasUnsavedEdits = true; renderTopicsPanel();
}
function renameTopic(subj, old, name) {
    if (!name || name === old) return;
    if (state.Subjects[subj][name] !== undefined) { StudyFlowToast.error(`"${name}" already exists.`); return; }
    const rebuilt = {};
    Object.keys(state.Subjects[subj]).forEach(k => { rebuilt[k === old ? name : k] = state.Subjects[subj][k]; });
    state.Subjects[subj] = rebuilt;
    
    if (activeTopicState[subj] === old) {
        activeTopicState[subj] = name;
    }

    hasUnsavedEdits = true; renderTopicsPanel();
}
function deleteTopic(subj, topic) {
    delete state.Subjects[subj][topic];
    if (activeTopicState[subj] === topic) {
        const remaining = Object.keys(state.Subjects[subj]);
        activeTopicState[subj] = remaining.length > 0 ? remaining[0] : null;
    }

    hasUnsavedEdits = true; renderTopicsPanel();
}
function addTopic(subj, name) {
    if (state.Subjects[subj][name] !== undefined) { StudyFlowToast.error(`"${name}" exists.`); return; }
    state.Subjects[subj][name] = { status: 'none', subtopics: [] };
    activeTopicState[subj] = name; // Auto expand new topic

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
    hasUnsavedEdits = true; renderSubjectBlock(subj);
}
function addSubtopic(subj, topic, name) {
    const t = state.Subjects[subj][topic];
    if (typeof t === 'object') { t.subtopics.push(name); }
    else { state.Subjects[subj][topic] = { status: t || '0', subtopics: [name] }; }
    hasUnsavedEdits = true; renderSubjectBlock(subj);
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
    const prefNoteEl = document.getElementById('statusPreferenceNote');
    if (prefNoteEl) {
        prefNoteEl.value = (state.schedule_preferences && state.schedule_preferences.preference_note) || '';
        const counterEl = document.getElementById('statusPrefCounter');
        if (counterEl) {
            counterEl.textContent = `${prefNoteEl.value.length} / 200`;
        }
    }
});


// ── SAVE EDITS ────────────────────────────────────────────────

async function saveEdits() {
    recalcStudyDays();

    // 1. Clean payload: preserve subtopics, reset status to 'none' for storage
    const cleanSubjects = {};
    Object.entries(state.Subjects).forEach(([subj, topics]) => {
        cleanSubjects[subj] = {};
        Object.entries(topics).forEach(([t, tdata]) => {
            cleanSubjects[subj][t] = {
                status: 'none',
                subtopics: typeof tdata === 'object' ? (tdata.subtopics || []) : []
            };
        });
    });

    // 2. Status payload: retains actual user progress statuses
    const statusSubjects = {};
    Object.entries(state.Subjects).forEach(([subj, topics]) => {
        statusSubjects[subj] = {};
        Object.entries(topics).forEach(([t, tdata]) => {
            statusSubjects[subj][t] = {
                status: typeof tdata === 'object' ? (tdata.status || '0') : (tdata || '0'),
                subtopics: typeof tdata === 'object' ? (tdata.subtopics || []) : []
            };
        });
    });

    const prefLimit = 200;
    const rawNote = document.getElementById('statusPreferenceNote')?.value || '';
    const schedulePreferences = {
        preference_note: sanitizeField(rawNote, prefLimit)
    };
    state.schedule_preferences = schedulePreferences;

    const extractedPayload = { Exam_dates: state.Exam_dates, Subjects: cleanSubjects, study_days: state.study_days, schedule_preferences: schedulePreferences };
    const statusPayload = { Exam_dates: state.Exam_dates, Subjects: statusSubjects, study_days: state.study_days, schedule_preferences: schedulePreferences };

    // Save extracted structure
    const res1 = await fetch(`/save_extracted/${userId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(extractedPayload)
    });
    if (!res1.ok) { const d = await res1.json(); throw new Error(d.error || 'Failed to save extracted data'); }

    // Save status progress structures in sync
    const res2 = await fetch(`/submit_status/${userId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(statusPayload)
    });
    if (!res2.ok) { const d = await res2.json(); throw new Error(d.error || 'Failed to save status progress'); }

    // Keep actual progress values on client side
    state.Subjects = statusSubjects;
    hasUnsavedEdits = false;
}

document.getElementById('saveEditsBtn').addEventListener('click', async () => {
    const btn = document.getElementById('saveEditsBtn');
    btn.disabled = true; btn.textContent = '…Saving';
    try {
        await saveEdits();
        renderHoursPanel();
        exitEditMode();
        StudyFlowToast.success('Changes saved successfully');
    }
    catch (err) {
        StudyFlowError.show(statusErrorEl, {
            title: 'Save Failed',
            what: 'Your edits couldn\'t be saved.',
            why: err.message || 'A connection or server issue occurred.',
            action: 'Check your connection and try again.',
            retryFn: () => document.getElementById('saveEditsBtn').click(),
            dismissFn: () => { }
        });
    }
    finally { btn.disabled = false; btn.textContent = '✓ Save Changes'; }
});


// ── COLLECT GENERATE PAYLOAD ──────────────────────────────────

function collectPayload() {
    const examDates = {}; const subjects = {};

    document.querySelectorAll('.custom-select').forEach(sel => {
        const subj = sel.dataset.subject;
        const topic = sel.dataset.topic;
        if (!subjects[subj]) subjects[subj] = {};
        // Preserve subtopics from state when building payload
        const existing = (state.Subjects[subj] || {})[topic];
        subjects[subj][topic] = {
            status: sel.dataset.value || '0',
            subtopics: typeof existing === 'object' ? (existing.subtopics || []) : []
        };
    });

    Object.assign(examDates, state.Exam_dates);

    const studyDays = {};
    document.querySelectorAll('#hoursList .date-row').forEach(row => {
        const date = row.dataset.date;
        const input = row.querySelector('input');
        studyDays[date] = input ? (input.value.trim() || '0') : '0';
    });

    const prefLimit = 200;
    const rawNote = document.getElementById('statusPreferenceNote')?.value || '';
    const schedulePreferences = {
        preference_note: sanitizeField(rawNote, prefLimit)
    };

    return { Exam_dates: examDates, Subjects: subjects, study_days: studyDays, schedule_preferences: schedulePreferences };
}


// ── LOADING (Multi-stage) ─────────────────────────────────────

const statusLoaderEl = document.getElementById('statusLoader');
const statusErrorEl = document.getElementById('statusError');

const statusLoader = new StudyFlowLoader(statusLoaderEl, [
    { label: 'Sending request', detail: 'Saving your latest progress and study hours.' },
    { label: 'Reading your data', detail: 'Reviewing subjects, topics, and exam dates.' },
    { label: 'Understanding preferences', detail: 'Balancing what is done, pending, and urgent.' },
    { label: 'Personalizing the schedule', detail: 'Fitting the plan around your available time.' },
    { label: 'Finalizing output', detail: 'Checking the schedule before opening it.' }
], {
    checkpoints: [
        'Inputs saved',
        'Preferences understood',
        'Schedule personalized',
        'Ready to study'
    ],
    idleMessages: [
        'Complex plans can take a little longer, but generation is still running.',
        'StudyFlow is balancing topics against exam dates and available hours.',
        'We are shaping a schedule that is practical, not just mathematically packed.',
        'Almost there. The final plan is being checked now.'
    ]
});
let statusStageTimers = [];

function startLoader() {
    document.getElementById('generateBtn').style.display = 'none';
    document.getElementById('editToolbar').style.display = 'none';
    statusErrorEl.innerHTML = '';
    statusLoader.start();

    clearStatusTimers();
    statusStageTimers = [
        setTimeout(() => statusLoader.advance(1), 1800),
        setTimeout(() => statusLoader.advance(2), 4600),
        setTimeout(() => statusLoader.advance(3), 8200),
        setTimeout(() => statusLoader.advance(4), 13000)
    ];
}

function stopLoader() {
    clearStatusTimers();
    statusLoader.reset();
    document.getElementById('generateBtn').style.display = 'block';
    document.getElementById('editToolbar').style.display = 'flex';
}

function showGenerateSuccess() {
    clearStatusTimers();
    statusLoader.complete('Schedule ready');
    statusLoader.setProgress(100);
    setTimeout(() => {
        statusLoader.showSuccess('Schedule generated!', 'Redirecting to your plan…');
    }, 600);
}

function showGenerateError(errorMsg) {
    stopLoader();
    const config = StudyFlowError.forGeneration(errorMsg);
    StudyFlowError.show(statusErrorEl, {
        ...config,
        retryFn: () => document.getElementById('generateBtn').click(),
        dismissFn: () => { }
    });
}

async function readErrorPayload(res) {
    const raw = await res.text();
    try {
        return JSON.parse(raw);
    } catch {
        return { error: raw || `Request failed with status ${res.status}` };
    }
}

function clearStatusTimers() {
    statusStageTimers.forEach(timer => clearTimeout(timer));
    statusStageTimers = [];
}

async function pollScheduleJob(jobId, intervalMs = 2000, timeoutMs = 180000) {
    const start = Date.now();
    while (Date.now() - start <= timeoutMs) {
        const res = await fetch(`/job/${jobId}/status`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Generation status could not be checked.');
        if (data.status === 'done') return data.result;
        if (data.status === 'error') throw new Error(data.error || 'Generation failed');
        await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
    throw new Error('Timed out waiting for schedule generation.');
}


// ── GENERATE ─────────────────────────────────────────────────

document.getElementById('generateBtn').addEventListener('click', async () => {
    if (isGenerating) return;
    if (editMode || hasUnsavedEdits) {
        try { await saveEdits(); if (editMode) exitEditMode(); }
        catch (err) {
            showGenerateError('Could not save edits: ' + err.message);
            return;
        }
    }
    isGenerating = true;
    const btn = document.getElementById('generateBtn');
    btn.classList.add('sf-btn-disabled');
    startLoader();

    try {
        const payload = collectPayload();
        const saveRes = await fetch(`/submit_status/${userId}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!saveRes.ok) throw new Error('Failed to save status');
        const genRes = await fetch(`/generate_schedule/${userId}`, { method: 'POST' });
        if (!genRes.ok) {
            const d = await readErrorPayload(genRes);
            throw new Error(d.error || 'Failed');
        }
        const genData = await genRes.json().catch(() => ({}));
        if (!genData.job_id) throw new Error('invalid_schema_response');
        await pollScheduleJob(genData.job_id);
        showGenerateSuccess();
        setTimeout(() => {
            window.location.href = '/schedule_page';
        }, 1500);
    } catch (err) {
        showGenerateError(err.message);
        isGenerating = false;
        btn.classList.remove('sf-btn-disabled');
    }
});


// ── INIT ─────────────────────────────────────────────────────

async function init() {
    const res = await fetch('/me');
    const data = await res.json();
    userId = data.id;
    document.addEventListener('click', () => {
        document.querySelectorAll('.custom-select').forEach(s => s.classList.remove('active'));
    });

    // Initialize subject states
    const subjects = Object.keys(state.Subjects);
    subjects.forEach(s => {
        subjectCollapsedState[s] = false; // default expanded
        const topics = Object.keys(state.Subjects[s]);
        if (topics.length > 0) {
            activeTopicState[s] = topics[0]; // first topic open by default
        } else {
            activeTopicState[s] = null;
        }
    });

    renderTopicsPanel();
    renderHoursPanel();

    // Initialize schedule preference note
    const prefNoteEl = document.getElementById('statusPreferenceNote');
    const counterEl = document.getElementById('statusPrefCounter');
    const prefLimit = 200;
    if (prefNoteEl) {
        prefNoteEl.value = (state.schedule_preferences && state.schedule_preferences.preference_note) || '';
        const updateCounter = () => {
            if (counterEl) {
                counterEl.textContent = `${prefNoteEl.value.length} / ${prefLimit}`;
            }
        };
        prefNoteEl.addEventListener('input', () => {
            if (!state.schedule_preferences) state.schedule_preferences = {};
            state.schedule_preferences.preference_note = prefNoteEl.value;
            updateCounter();
            if (editMode) {
                hasUnsavedEdits = true;
            }
        });
        updateCounter();
    }
}

init();
