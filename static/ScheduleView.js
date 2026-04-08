/* ════════════════════════════════════════
   SCHEDULEVIEW.JS
   - Fetches userid from /me (no hardcoded 1)
   - Renders { DD-MM-YYYY: { Subject: { Topic: hours } } }
════════════════════════════════════════ */

async function getUserId() {
    const res  = await fetch('/me');
    const data = await res.json();
    return data.id;
}

async function fetchSchedule(userid) {
    const res = await fetch(`/schedule/${userid}`);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    return res.json();
}

function sortDates(dates) {
    return dates.sort((a, b) => {
        const toMs = str => {
            const [d, m, y] = str.split('-');
            return new Date(`${y}-${m}-${d}`).getTime();
        };
        return toMs(a) - toMs(b);
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

    sortDates(Object.keys(data)).forEach(date => {

        const dayCard = document.createElement('div');
        dayCard.className = 'date-block';

        const dateTitle = document.createElement('div');
        dateTitle.className = 'date-title';
        dateTitle.innerText = date;
        dayCard.appendChild(dateTitle);

        const subjectsOnDay = data[date];

        Object.entries(subjectsOnDay).forEach(([subjectName, topics]) => {

            const subBlock = document.createElement('div');
            subBlock.className = 'subject-block';

            const subTitle = document.createElement('div');
            subTitle.className = 'subject-name';
            subTitle.innerText = subjectName;
            subBlock.appendChild(subTitle);

            Object.entries(topics).forEach(([topicName, hours]) => {

                const row = document.createElement('div');
                row.className = 'topic-row';

                const nameEl = document.createElement('span');
                nameEl.className = 'topic-name';
                nameEl.innerText = topicName;

                const hoursEl = document.createElement('span');
                hoursEl.className = 'duration';
                hoursEl.innerText = `${hours}h`;

                row.appendChild(nameEl);
                row.appendChild(hoursEl);
                subBlock.appendChild(row);
            });

            dayCard.appendChild(subBlock);
        });

        container.appendChild(dayCard);
    });
}

(async () => {
    try {
        const userid = await getUserId();
        const data   = await fetchSchedule(userid);
        renderSchedule(data);
    } catch (err) {
        console.error('Failed to load schedule:', err);
        renderSchedule(null);
    }
})();