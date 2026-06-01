/* ScheduleView.js — renders new schedule format with Study button */

let userId = null;

async function getUserId() {
    const res = await fetch('/me');
    const data = await res.json();
    userId = data.id;
    return data.id;
}

async function fetchSchedule(uid) {
    const res = await fetch(`/schedule/${uid}`);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    return res.json();
}

function sortDMY(dates) {
    return dates.sort((a, b) => {
        const ms = s => { const [d, m, y] = s.split('-'); return new Date(+y, +m - 1, +d).getTime(); };
        return ms(a) - ms(b);
    });
}

function renderSchedule(data) {
    const container = document.getElementById('scheduleContainer');
    container.innerHTML = '';

    if (!data || !Object.keys(data).length) {

        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                </div>
                <h2>No Active Schedule Yet</h2>
                <p>It looks like you haven't generated a study plan. Head over to the Upload page to submit your syllabus and start your journey.</p>
                <a href="/upload_page" class="btn-primary" style="text-decoration: none; display: inline-block;">Start Your Journey</a>
            </div>
        `;

        return;
    }

    sortDMY(Object.keys(data)).forEach(date => {
        const dayCard = document.createElement('div');
        dayCard.className = 'date-block';

        const dateTitle = document.createElement('div');
        dateTitle.className = 'date-title';
        dateTitle.innerText = date;
        dayCard.appendChild(dateTitle);

        Object.entries(data[date]).forEach(([subjectName, topics]) => {
            const subBlock = document.createElement('div');
            subBlock.className = 'subject-block';

            const subTitle = document.createElement('div');
            subTitle.className = 'subject-name';
            subTitle.innerText = subjectName;
            subBlock.appendChild(subTitle);

            Object.entries(topics).forEach(([topicName, topicData]) => {
                // topicData can be { hours: N, subtopics: [...] } or just an integer (old format)
                const hours = typeof topicData === 'object' ? topicData.hours : topicData;
                const subtopics = typeof topicData === 'object' ? (topicData.subtopics || []) : [];

                const topicRow = document.createElement('div');
                topicRow.className = 'topic-row';

                // Left: topic name + subtopics
                const leftDiv = document.createElement('div');
                leftDiv.style.flex = '1';

                const topLine = document.createElement('div');
                topLine.style.cssText = 'display:flex;align-items:center;gap:8px;';
                const nameEl = document.createElement('span');
                nameEl.className = 'topic-name';
                nameEl.innerText = topicName;
                topLine.appendChild(nameEl);

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

                    toggle.addEventListener('click', () => {
                        toggle.classList.toggle('open');
                        subList.classList.toggle('open');
                    });

                    topLine.appendChild(toggle);
                    leftDiv.appendChild(topLine);
                    leftDiv.appendChild(subList);
                } else {
                    leftDiv.appendChild(topLine);
                }

                // Right: hours + Study button
                const rightDiv = document.createElement('div');
                rightDiv.style.cssText = 'display:flex;align-items:center;gap:10px;flex-shrink:0;';

                const hoursEl = document.createElement('span');
                hoursEl.className = 'duration';
                hoursEl.innerText = `${hours}h`;

                const studyBtn = document.createElement('button');
                studyBtn.className = 'study-btn';
                studyBtn.innerText = 'Study Now';
                studyBtn.addEventListener('click', () => {
                    const enc = (s) => encodeURIComponent(s);
                    window.location.href = `/study/${enc(subjectName)}/${enc(topicName)}?date=${enc(date)}`;
                });

                rightDiv.appendChild(hoursEl);
                rightDiv.appendChild(studyBtn);

                topicRow.appendChild(leftDiv);
                topicRow.appendChild(rightDiv);
                subBlock.appendChild(topicRow);
            });

            dayCard.appendChild(subBlock);
        });

        container.appendChild(dayCard);
    });
}

(async () => {
    try {
        const uid = await getUserId();
        const data = await fetchSchedule(uid);
        renderSchedule(data);
    } catch (err) {
        console.error('Failed to load schedule:', err);
        renderSchedule(null);
    }
})();