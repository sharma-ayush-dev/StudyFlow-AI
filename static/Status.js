/* ------------------------------------------------
  COLLECT PAYLOAD
  Builds the JSON that matches the new backend format:
  {
    "Exam_dates":  { "Subject": "DD-MM-YYYY" },
    "Subjects":    { "Subject": { "Topic": "0"-"100" } },
    "study_days":  { "DD-MM-YYYY": "hours" }
  }
------------------------------------------------ */

function collectPayload() {

    const examDates = {};
    const subjects  = {};

    /* --- topics + completion % --- */

    document.querySelectorAll(".subject-block").forEach(block => {

        const subjectName = block.querySelector("h2").innerText.trim();
        const examText    = block.querySelector(".exam-date").innerText.trim();

        if (examText && examText !== "No exam date") {
            examDates[subjectName] = examText;
        }

        subjects[subjectName] = {};

        block.querySelectorAll(".topic-row").forEach(row => {
            const topic = row.dataset.topic;
            const pct   = row.querySelector(".status-select").value;
            subjects[subjectName][topic] = pct;
        });

    });

    /* --- daily study hours --- */

    const studyDays = {};

    document.querySelectorAll("#hoursList .date-row").forEach(row => {
        const date  = row.dataset.date;
        const hours = row.querySelector("input").value.trim() || "0";
        studyDays[date] = hours;
    });

    return {
        Exam_dates: examDates,
        Subjects:   subjects,
        study_days: studyDays
    };

}


/* ------------------------------------------------
  GENERATE SCHEDULE BUTTON
------------------------------------------------ */

document.getElementById("generateBtn").addEventListener("click", async () => {

    const payload = collectPayload();

    try {

        /* 1. Save the filled-in status */
        const saveRes = await fetch("/submit_status/1", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify(payload)
        });

        if (!saveRes.ok) {
            throw new Error("Failed to save status");
        }

        /* 2. Trigger schedule generation */
        const genRes = await fetch("/generate_schedule/1", {
            method: "POST"
        });

        if (!genRes.ok) {
            throw new Error("Failed to generate schedule");
        }

        /* 3. Navigate to schedule page */
        window.location.href = "/schedule_page";

    } catch (err) {
        console.error("Schedule generation failed:", err);
        alert("Failed to generate schedule. Please try again.");
    }

});