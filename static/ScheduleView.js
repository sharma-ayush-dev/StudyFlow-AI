/* ------------------------------------------------
  FETCH SCHEDULE FROM BACKEND
------------------------------------------------ */

async function fetchSchedule(userid) {
    const res = await fetch(`/schedule/${userid}`);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    return res.json();
}


/* ------------------------------------------------
  SORT DD-MM-YYYY DATES CHRONOLOGICALLY
------------------------------------------------ */

function sortDates(dates) {
    return dates.sort((a, b) => {
        const toMs = str => {
            const [d, m, y] = str.split("-");
            return new Date(`${y}-${m}-${d}`).getTime();
        };
        return toMs(a) - toMs(b);
    });
}


/* ------------------------------------------------
  RENDER SCHEDULE
  Expects shape:
  {
    "DD-MM-YYYY": {
      "SubjectName": { "TopicName": <integer hours> }
    }
  }
------------------------------------------------ */

function renderSchedule(data) {

    const container = document.getElementById("scheduleContainer");
    container.innerHTML = "";

    if (!data || !Object.keys(data).length) {

        const msg = document.createElement("div");
        msg.className = "date-block";
        msg.style.textAlign = "center";
        msg.style.padding   = "30px";
        msg.innerText = "No schedule found. Go back to the Status page and generate one.";
        container.appendChild(msg);
        return;

    }

    sortDates(Object.keys(data)).forEach(date => {

        /* DATE CARD */

        const dayCard = document.createElement("div");
        dayCard.className = "date-block";

        const dateTitle = document.createElement("div");
        dateTitle.className = "date-title";
        dateTitle.innerText = date;
        dayCard.appendChild(dateTitle);

        /* SUBJECTS */

        const subjectsOnDay = data[date];

        Object.entries(subjectsOnDay).forEach(([subjectName, topics]) => {

            const subBlock = document.createElement("div");
            subBlock.className = "subject-block";

            const subTitle = document.createElement("div");
            subTitle.className = "subject-name";
            subTitle.innerText = subjectName;
            subBlock.appendChild(subTitle);

            /* TOPICS */

            Object.entries(topics).forEach(([topicName, hours]) => {

                const row = document.createElement("div");
                row.className = "topic-row";

                const nameEl = document.createElement("span");
                nameEl.className = "topic-name";
                nameEl.innerText = topicName;

                const hoursEl = document.createElement("span");
                hoursEl.className = "duration";
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


/* ------------------------------------------------
  INIT
------------------------------------------------ */

(async () => {
    try {
        const data = await fetchSchedule(1);
        renderSchedule(data);
    } catch (err) {
        console.error("Failed to load schedule:", err);
        renderSchedule(null);
    }
})();