/* ScheduleView.js — renders new schedule format with Study button */

let userId = null;

async function getUserId() {
    const res  = await fetch('/me');
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
        const ms = s => { const [d,m,y]=s.split('-'); return new Date(+y,+m-1,+d).getTime(); };
        return ms(a) - ms(b);
    });
}

function renderSchedule(data) {
    const container = document.getElementById('scheduleContainer');
    container.innerHTML = '';

    if (!data || !Object.keys(data).length) {
        const msg = document.createElement('div');
        msg.className = 'date-block';
        msg.style.cssText = 'text-align:center;padding:30px;';
        msg.innerText = 'No schedule yet. Go back to Topics and generate one.';
        container.appendChild(msg);
        return;
    }

    sortDMY(Object.keys(data)).forEach(date => {
        const dayCard = document.createElement('div');
        dayCard.className = 'date-block';

        const dateTitle = document.createElement('div');
        dateTitle.className   = 'date-title';
        dateTitle.innerText   = date;
        dayCard.appendChild(dateTitle);

        Object.entries(data[date]).forEach(([subjectName, topics]) => {
            const subBlock = document.createElement('div');
            subBlock.className = 'subject-block';

            const subTitle = document.createElement('div');
            subTitle.className   = 'subject-name';
            subTitle.innerText   = subjectName;
            subBlock.appendChild(subTitle);

            Object.entries(topics).forEach(([topicName, topicData]) => {
                // topicData can be { hours: N, subtopics: [...] } or just an integer (old format)
                const hours     = typeof topicData === 'object' ? topicData.hours    : topicData;
                const subtopics = typeof topicData === 'object' ? (topicData.subtopics || []) : [];

                const topicRow = document.createElement('div');
                topicRow.className = 'topic-row';

                // Left: topic name + subtopics
                const leftDiv = document.createElement('div');
                leftDiv.style.flex = '1';

                const topLine = document.createElement('div');
                topLine.style.cssText = 'display:flex;align-items:center;gap:8px;';
                const nameEl = document.createElement('span');
                nameEl.className   = 'topic-name';
                nameEl.innerText   = topicName;
                topLine.appendChild(nameEl);

                if (subtopics.length) {
                    const toggle = document.createElement('span');
                    toggle.style.cssText = 'font-size:11px;color:#7b2ff7;cursor:pointer;';
                    toggle.textContent   = `▸ ${subtopics.length}`;
                    const subList = document.createElement('ul');
                    subList.style.cssText = 'display:none;margin:4px 0 0 10px;padding:0;';
                    subtopics.forEach(s => {
                        const li = document.createElement('li');
                        li.style.cssText = 'font-size:12px;color:#777;margin:2px 0;';
                        li.textContent   = s;
                        subList.appendChild(li);
                    });
                    toggle.addEventListener('click', () => {
                        const open = subList.style.display === 'block';
                        subList.style.display = open ? 'none' : 'block';
                        toggle.textContent = open ? `▸ ${subtopics.length}` : `▾ ${subtopics.length}`;
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
                hoursEl.className   = 'duration';
                hoursEl.innerText   = `${hours}h`;

                const studyBtn = document.createElement('button');
                studyBtn.className = 'study-btn';
                studyBtn.innerText = '📖 Study';
                studyBtn.addEventListener('click', () => {
                    const enc = (s) => encodeURIComponent(s);
                    window.location.href = `/study/${enc(subjectName)}/${enc(topicName)}`;
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
        const uid  = await getUserId();
        const data = await fetchSchedule(uid);
        renderSchedule(data);
    } catch (err) {
        console.error('Failed to load schedule:', err);
        renderSchedule(null);
    }
})();