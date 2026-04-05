/* ════════════════════════════════════════
   UPLOAD-PAGE.JS
   - Fetches userid dynamically from /me
   - Sends files + optional manual text
   - Shows loading animation during upload
   - Disables button after first click (rate limit UI)
════════════════════════════════════════ */

const syllabusInput = document.getElementById('syllabusInput');
const datesheetInput = document.getElementById('datesheetInput');
const syllabusName  = document.getElementById('syllabusName');
const datesheetName = document.getElementById('datesheetName');
const uploadBtn     = document.getElementById('uploadBtn');
const uploadLoader  = document.getElementById('uploadLoader');
const loaderMsg     = document.getElementById('loaderMsg');

// ── MANUAL TEXT TOGGLE ────────────────────────────────

document.getElementById('manualToggle').addEventListener('click', () => {
    const box = document.getElementById('manualTextBox');
    const isHidden = box.style.display === 'none';
    box.style.display = isHidden ? 'flex' : 'none';
});


// ── FILE SELECT LISTENERS ─────────────────────────────

syllabusInput.addEventListener('change', () => {
    if (syllabusInput.files.length > 0) {
        syllabusName.textContent = syllabusInput.files.length + ' file(s) selected';
    }
});

datesheetInput.addEventListener('change', () => {
    if (datesheetInput.files.length > 0) {
        datesheetName.textContent = datesheetInput.files[0].name;
    }
});


// ── DRAG & DROP ───────────────────────────────────────

document.getElementById('syllabusDrop').addEventListener('dragover', e => e.preventDefault());
document.getElementById('syllabusDrop').addEventListener('drop', e => {
    e.preventDefault();
    syllabusInput.files = e.dataTransfer.files;
    syllabusName.textContent = e.dataTransfer.files.length + ' file(s) selected';
});

document.getElementById('datesheetDrop').addEventListener('dragover', e => e.preventDefault());
document.getElementById('datesheetDrop').addEventListener('drop', e => {
    e.preventDefault();
    datesheetInput.files = e.dataTransfer.files;
    datesheetName.textContent = e.dataTransfer.files[0].name;
});


// ── LOADING HELPERS ───────────────────────────────────

const loaderMessages = [
    'Extracting content…',
    'Reading your syllabus…',
    'Identifying topics…',
    'Matching exam dates…',
    'Almost there…'
];
let loaderInterval = null;

function startLoader() {
    uploadBtn.style.display   = 'none';
    uploadLoader.style.display = 'block';
    let i = 0;
    loaderMsg.textContent = loaderMessages[0];
    loaderInterval = setInterval(() => {
        i = (i + 1) % loaderMessages.length;
        loaderMsg.textContent = loaderMessages[i];
    }, 4000);
}

function stopLoader() {
    clearInterval(loaderInterval);
    uploadLoader.style.display = 'none';
    uploadBtn.style.display    = 'block';
}


// ── UPLOAD ────────────────────────────────────────────

uploadBtn.addEventListener('click', async () => {

    const hasFiles  = syllabusInput.files.length > 0 || datesheetInput.files.length > 0;
    const manualTxt = (document.getElementById('manualText')?.value || '').trim();

    if (!hasFiles && !manualTxt) {
        alert('Please upload at least one file or paste your syllabus text.');
        return;
    }

    // Must have a datesheet if files are being uploaded
    if (syllabusInput.files.length > 0 && !datesheetInput.files.length) {
        alert('Please also upload your datesheet.');
        return;
    }

    startLoader();

    try {
        const formData = new FormData();

        for (const file of syllabusInput.files) {
            formData.append('files', file);
        }
        if (datesheetInput.files.length) {
            formData.append('files', datesheetInput.files[0]);
        }
        if (manualTxt) {
            formData.append('manual_text', manualTxt);
        }

        const res = await fetch('/upload', {
            method: 'POST',
            body:   formData
        });

        const data = await res.json();

        if (!res.ok) {
            alert('Upload failed: ' + (data.error || 'Unknown error'));
            stopLoader();
            return;
        }

        window.location.href = '/status';

    } catch (err) {
        console.error(err);
        alert('Upload failed. Please check your connection and try again.');
        stopLoader();
    }
});