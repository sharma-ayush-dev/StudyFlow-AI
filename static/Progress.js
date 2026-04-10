/* Progress.js - updated for new subtopic schema */

const userId       = window.USER_ID;
const topicStatus  = window.TOPIC_STATUS;
const pastSchedule = window.PAST_SCHEDULE;
const fullSchedule = window.FULL_SCHEDULE;
const todayStr     = window.TODAY_STR;
const isAdmin      = window.IS_ADMIN || false;

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

function renderTopicList() {
    const card     = document.getElementById('progressTopicsCard');
    const subjects = topicStatus.Subjects || {};
    card.innerHTML = '';
    if (!Object.keys(subjects).length) {
        card.innerHTML = '<p style="color:#666;padding:20px;">No topic data found.</p>';
        return;
    }
    Object.entries(subjects).forEach(([subjName, topics]) => {
        const block  = document.createElement('div'); block.className = 'subject-block';
        const header = document.createElement('div'); header.className = 'subject-header';
        const nameEl = document.createElement('h2'); nameEl.textContent = subjName;
        const examEl = document.createElement('span'); examEl.className = 'exam-date';
        examEl.textContent = (topicStatus.Exam_dates||{})[subjName] || 'No exam date';
        header.appendChild(nameEl); header.appendChild(examEl);
        block.appendChild(header);

        const topicsDiv = document.createElement('div'); topicsDiv.className = 'topics';
        Object.entries(topics).forEach(([topicName, tdata]) => {
            // tdata can be { status, subtopics } (new) or a string (old)
            const currentStatus = typeof tdata === 'object' ? (tdata.status || '0') : String(tdata);
            const subtopics     = typeof tdata === 'object' ? (tdata.subtopics || []) : [];

            const row  = document.createElement('div'); row.className = 'topic-row';
            const key  = `${subjName}||${topicName}`;
            const left = document.createElement('div'); left.style.flex = '1';

            const nameSpan = document.createElement('span');
            nameSpan.className = 'topic-name'; nameSpan.textContent = topicName;
            if (overdueSet.has(key)) {
                const badge = document.createElement('span');
                badge.className = 'overdue-badge'; badge.textContent = 'Scheduled';
                nameSpan.appendChild(badge);
            }
            left.appendChild(nameSpan);

            if (subtopics.length) {
                const subEl = document.createElement('div');
                subEl.style.cssText = 'font-size:12px;color:#666;margin-top:3px;';
                subEl.textContent = subtopics.slice(0,3).join(' · ') + (subtopics.length > 3 ? ' …' : '');
                left.appendChild(subEl);
            }

            const select = document.createElement('select');
            select.className = 'status-select';
            select.dataset.subject = subjName;
            select.dataset.topic   = topicName;
            [['0','0% — Not Started'],['25','25% — Just Begun'],
             ['50','50% — Halfway'],['75','75% — Almost Done'],['100','100% — Completed']
            ].forEach(([val,label]) => {
                const opt = document.createElement('option');
                opt.value = val; opt.textContent = label;
                if (String(currentStatus) === val) opt.selected = true;
                select.appendChild(opt);
            });

            row.appendChild(left); row.appendChild(select);
            topicsDiv.appendChild(row);
        });
        block.appendChild(topicsDiv);
        card.appendChild(block);
    });
}

function collectUpdatedSubjects() {
    const subjects = {};
    document.querySelectorAll('.status-select').forEach(sel => {
        const subj  = sel.dataset.subject;
        const topic = sel.dataset.topic;
        if (!subjects[subj]) subjects[subj] = {};
        // Send only the new status value — server preserves subtopics
        subjects[subj][topic] = sel.value;
    });
    return subjects;
}

const loaderMessages = {
    save:  ['Saving progress…','Updating percentages…'],
    regen: ['Analysing progress…','Rescheduling topics…','Optimising…','Almost ready…']
};
let loaderInterval = null;

function showLoader(type) {
    document.getElementById('progressActions').style.display = 'none';
    document.getElementById('progressLoader').style.display  = 'block';
    const msgs = loaderMessages[type]; let i = 0;
    document.getElementById('progressLoaderMsg').textContent = msgs[0];
    loaderInterval = setInterval(() => {
        i = (i+1)%msgs.length;
        document.getElementById('progressLoaderMsg').textContent = msgs[i];
    }, 3500);
}
function hideLoader() {
    clearInterval(loaderInterval);
    document.getElementById('progressLoader').style.display  = 'none';
    document.getElementById('progressActions').style.display = 'flex';
}

document.getElementById('saveProgressBtn').addEventListener('click', async () => {
    showLoader('save');
    try {
        const res = await fetch(`/update_progress/${userId}`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({Subjects: collectUpdatedSubjects()})
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Failed');
        hideLoader();
        const btn = document.getElementById('saveProgressBtn');
        const orig = btn.textContent;
        btn.textContent = '✓ Saved!';
        setTimeout(() => { btn.textContent = orig; }, 2000);
    } catch (err) { hideLoader(); alert('Failed: '+err.message); }
});

document.getElementById('regenBtn').addEventListener('click', async () => {
    showLoader('regen');
    try {
        const saveRes = await fetch(`/update_progress/${userId}`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({Subjects: collectUpdatedSubjects()})
        });
        if (!saveRes.ok) throw new Error('Failed to save progress');

        const regenRes = await fetch(`/regenerate_schedule/${userId}`, {method:'POST'});
        const data     = await regenRes.json();
        if (!regenRes.ok) {
            let msg = data.error || 'Regeneration failed';
            if (isAdmin && data.details) msg += '\n\nAdmin details:\n'+data.details;
            throw new Error(msg);
        }
        hideLoader();
        if (data.notice) showNoticeBanner(data.notice);
        renderComparison(data.old_schedule, data.new_schedule);
    } catch (err) { hideLoader(); alert('Regeneration failed:\n'+err.message); }
});

function showNoticeBanner(notice) {
    const existing = document.getElementById('llmNoticeBanner');
    if (existing) existing.remove();
    const banner = document.createElement('div');
    banner.id    = 'llmNoticeBanner';
    banner.style.cssText = 'max-width:760px;margin:0 auto 20px;padding:14px 20px;background:rgba(255,180,0,0.1);border:1px solid rgba(255,180,0,0.35);border-radius:12px;font-size:14px;color:#ffd060;';
    banner.innerHTML = `⚠ Primary AI unavailable. Used <strong>${notice.model}</strong> instead.`;
    if (isAdmin && notice.reasons?.length) {
        const details = document.createElement('details');
        details.style.marginTop = '8px';
        const summary = document.createElement('summary');
        summary.textContent = 'Admin: failure details'; summary.style.cursor = 'pointer';
        const pre = document.createElement('pre');
        pre.style.cssText = 'font-size:12px;color:#aaa;margin-top:8px;white-space:pre-wrap;';
        pre.textContent   = notice.reasons.join('\n\n');
        details.appendChild(summary); details.appendChild(pre);
        banner.appendChild(details);
    }
    document.getElementById('progressWrapper').parentNode.insertBefore(
        banner, document.getElementById('progressWrapper'));
}

function sortDMY(dates) {
    return dates.sort((a,b) => {
        const ms = s => { const [d,m,y]=s.split('-'); return new Date(+y,+m-1,+d).getTime(); };
        return ms(a)-ms(b);
    });
}

function renderScheduleInto(containerId, scheduleData) {
    const el = document.getElementById(containerId); el.innerHTML = '';
    const dates = sortDMY(Object.keys(scheduleData));
    if (!dates.length) { el.innerHTML = '<p style="color:#666;padding:16px;">Empty schedule.</p>'; return; }
    dates.forEach(date => {
        const dateBlock = document.createElement('div'); dateBlock.className = 'cmp-date-block';
        const dateTitle = document.createElement('div'); dateTitle.className = 'cmp-date-title';
        dateTitle.textContent = date; dateBlock.appendChild(dateTitle);
        Object.entries(scheduleData[date]).forEach(([subj, topics]) => {
            const subjEl = document.createElement('div'); subjEl.className = 'cmp-subject';
            const subjTitle = document.createElement('div'); subjTitle.className = 'cmp-subject-name';
            subjTitle.textContent = subj; subjEl.appendChild(subjTitle);
            Object.entries(topics).forEach(([topic, tdata]) => {
                const hours = typeof tdata === 'object' ? tdata.hours : tdata;
                const row = document.createElement('div'); row.className = 'cmp-topic-row';
                const t = document.createElement('span'); t.textContent = topic;
                const h = document.createElement('span'); h.className = 'cmp-hours'; h.textContent = `${hours}h`;
                row.appendChild(t); row.appendChild(h); subjEl.appendChild(row);
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
    document.getElementById('comparisonSection').scrollIntoView({behavior:'smooth'});
}

async function handleKeep(choice) {
    const btn = document.getElementById(choice==='old'?'keepOldBtn':'keepNewBtn');
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
        const res = await fetch(`/keep_schedule/${userId}`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({choice})
        });
        if (!res.ok) throw new Error((await res.json()).error||'Failed');
        window.location.href = '/schedule_page';
    } catch(err) { alert('Failed: '+err.message); btn.disabled=false; btn.textContent='Keep This'; }
}

document.getElementById('keepOldBtn').addEventListener('click', ()=>handleKeep('old'));
document.getElementById('keepNewBtn').addEventListener('click', ()=>handleKeep('new'));

renderTopicList();