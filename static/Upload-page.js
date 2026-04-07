/* ═══════════════════════════════════════════
   UPLOAD-PAGE.JS
═══════════════════════════════════════════ */

const fileInput     = document.getElementById('fileInput');
const fileNames     = document.getElementById('fileNames');
const uploadBtn     = document.getElementById('uploadBtn');
const uploadLoader  = document.getElementById('uploadLoader');
const loaderMsg     = document.getElementById('loaderMsg');
const manualText    = document.getElementById('manualText');
const wordCountText = document.getElementById('wordCountText');
const WORD_LIMIT    = window.WORD_LIMIT || 2000;

// ── MANUAL TEXT TOGGLE ──────────────────────────────────────

document.getElementById('manualToggle').addEventListener('click', () => {
    const box = document.getElementById('manualTextBox');
    box.style.display = box.style.display === 'none' ? 'flex' : 'none';
});

// ── WORD COUNT + CLIENT-SIDE SANITIZATION ───────────────────

function countWords(text) {
    return text.trim() ? text.trim().split(/\s+/).length : 0;
}

// Strip HTML tags client-side before sending (defence in depth — server also sanitizes)
function sanitizeText(text) {
    return text
        .replace(/<script[\s\S]*?<\/script>/gi, '')
        .replace(/<[^>]+>/g, '')
        .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
        .trim();
}

manualText?.addEventListener('input', () => {
    const words = countWords(manualText.value);
    wordCountText.textContent = `${words} word${words !== 1 ? 's' : ''}`;
    wordCountText.style.color = words > WORD_LIMIT ? '#ff7c7c' : '#666';
});

// ── FILE SELECT + DRAG & DROP ───────────────────────────────

fileInput.addEventListener('change', () => {
    updateFileLabel(fileInput.files);
});

function updateFileLabel(files) {
    if (!files || !files.length) {
        fileNames.textContent = 'No files chosen';
        return;
    }
    fileNames.textContent = Array.from(files).map(f => f.name).join(', ');
}

const dropArea = document.getElementById('fileDropArea');

dropArea.addEventListener('dragover', e => {
    e.preventDefault();
    dropArea.classList.add('drag-over');
});
dropArea.addEventListener('dragleave', () => dropArea.classList.remove('drag-over'));
dropArea.addEventListener('drop', e => {
    e.preventDefault();
    dropArea.classList.remove('drag-over');
    const dt  = e.dataTransfer;
    // Assign dropped files to the input
    try {
        fileInput.files = dt.files;
    } catch (_) {
        // DataTransfer assignment may fail in some browsers — show names anyway
    }
    updateFileLabel(dt.files);
});

// ── LOADER ──────────────────────────────────────────────────

const loaderMessages = [
    'Extracting content…',
    'Reading your files…',
    'Identifying topics…',
    'Matching exam dates…',
    'Almost there…'
];
let loaderInterval = null;

function startLoader() {
    uploadBtn.style.display  = 'none';
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

// ── UPLOAD ──────────────────────────────────────────────────

uploadBtn.addEventListener('click', async () => {
    const hasFiles  = fileInput.files && fileInput.files.length > 0;
    const rawText   = manualText?.value || '';
    const cleanText = sanitizeText(rawText);
    const words     = countWords(cleanText);

    if (!hasFiles && !cleanText) {
        alert('Please upload files or paste your syllabus text.');
        return;
    }
    if (words > WORD_LIMIT) {
        alert(`Text exceeds the ${WORD_LIMIT}-word limit (you have ${words} words). Please shorten it.`);
        return;
    }

    startLoader();

    try {
        const formData = new FormData();

        if (hasFiles) {
            Array.from(fileInput.files).forEach(f => formData.append('files', f));
        }
        if (cleanText) {
            formData.append('manual_text', cleanText);
        }

        const res  = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (!res.ok) {
            alert('Upload failed: ' + (data.error || 'Unknown error'));
            stopLoader();
            return;
        }

        window.location.href = '/status';

    } catch (err) {
        console.error(err);
        alert('Upload failed. Check your connection and try again.');
        stopLoader();
    }
});