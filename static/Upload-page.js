/* Upload page: multi-stage progress + friendly error cards */

const fileInput = document.getElementById('fileInput');
const fileNames = document.getElementById('fileNames');
const uploadBtn = document.getElementById('uploadBtn');
const uploadLoaderEl = document.getElementById('uploadLoader');
const uploadErrorEl = document.getElementById('uploadError');
const manualText = document.getElementById('manualText');
const wordCountText = document.getElementById('wordCountText');
const WORD_LIMIT = window.WORD_LIMIT || 2000;

const loader = new StudyFlowLoader(uploadLoaderEl, [
    { label: 'Uploading content', detail: 'Receiving your files and text securely.' },
    { label: 'Reading content', detail: 'Extracting useful text from the uploaded material.' },
    { label: 'Understanding the content', detail: 'Finding subjects, topics, subtopics, and exam dates.' },
    { label: 'Generating schema', detail: 'Organizing everything into a clean study structure.' },
    { label: 'Schema ready', detail: 'Your content is ready for planning.' }
], {
    checkpoints: [
        'Content received',
        'Content understood',
        'Schema generated',
        'Ready to continue'
    ],
    idleMessages: [
        'Large files can take a little longer, but the upload is still moving.',
        'We are checking the content carefully so the plan has better context.',
        'StudyFlow is turning messy files into structured study data.',
        'Almost there. The schema is being polished now.'
    ]
});

let isUploading = false;
let uploadStageTimers = [];

document.getElementById('manualToggle').addEventListener('click', () => {
    const box = document.getElementById('manualTextBox');
    box.style.display = box.style.display === 'none' ? 'flex' : 'none';
});

function countWords(text) {
    return text.trim() ? text.trim().split(/\s+/).length : 0;
}

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

fileInput.addEventListener('change', () => {
    updateFileLabel(fileInput.files);
});

function updateFileLabel(files) {
    if (!files || !files.length) {
        fileNames.textContent = 'No files chosen';
        return;
    }
    fileNames.textContent = Array.from(files).map(file => file.name).join(', ');
}

const dropArea = document.getElementById('fileDropArea');

dropArea.addEventListener('dragover', event => {
    event.preventDefault();
    dropArea.classList.add('drag-over');
});

dropArea.addEventListener('dragleave', () => dropArea.classList.remove('drag-over'));

dropArea.addEventListener('drop', event => {
    event.preventDefault();
    dropArea.classList.remove('drag-over');
    const droppedFiles = event.dataTransfer.files;
    try {
        fileInput.files = droppedFiles;
    } catch (_) {
        // Some browsers do not allow assigning DataTransfer files to inputs.
    }
    updateFileLabel(droppedFiles);
});

function startUploadLoader() {
    uploadBtn.style.display = 'none';
    uploadErrorEl.innerHTML = '';
    loader.start();
    clearUploadTimers();
    uploadStageTimers = [
        setTimeout(() => loader.advance(1), 1800),
        setTimeout(() => loader.advance(2), 4800),
        setTimeout(() => loader.advance(3), 8500)
    ];
}

function stopUploadLoader() {
    clearUploadTimers();
    loader.reset();
    uploadBtn.style.display = 'block';
}

function showUploadSuccess() {
    clearUploadTimers();
    loader.advance(4);
    loader.complete('Schema ready');
    setTimeout(() => {
        loader.showSuccess('Content processed!', 'Redirecting to your study plan...');
    }, 600);
}

function showUploadError(errorMsg, retryFn) {
    stopUploadLoader();
    const config = StudyFlowError.forUpload(errorMsg);
    StudyFlowError.show(uploadErrorEl, {
        ...config,
        retryFn,
        dismissFn: () => { }
    });
}

function clearUploadTimers() {
    uploadStageTimers.forEach(timer => clearTimeout(timer));
    uploadStageTimers = [];
}

async function doUpload() {
    if (isUploading) return;

    const hasFiles = fileInput.files && fileInput.files.length > 0;
    const rawText = manualText?.value || '';
    const cleanText = sanitizeText(rawText);
    const words = countWords(cleanText);

    if (!hasFiles && !cleanText) {
        StudyFlowError.show(uploadErrorEl, {
            title: 'No Content Provided',
            what: 'There is no syllabus or datesheet content to work with yet.',
            why: 'StudyFlow needs uploaded files or pasted text before it can build a plan.',
            action: 'Choose files using the upload area, or paste your syllabus and datesheet text below.',
            dismissFn: () => { }
        });
        return;
    }

    if (words > WORD_LIMIT) {
        StudyFlowError.show(uploadErrorEl, {
            title: 'Text Is Too Long',
            what: `Your pasted text has ${words} words, which is above the ${WORD_LIMIT}-word limit.`,
            why: 'Shorter inputs help StudyFlow read the content accurately and quickly.',
            action: 'Trim repeated sections, upload a file instead, or split the content into a smaller version.',
            dismissFn: () => { }
        });
        return;
    }

    isUploading = true;
    startUploadLoader();

    try {
        const formData = new FormData();
        if (hasFiles) {
            Array.from(fileInput.files).forEach(file => formData.append('files', file));
        }
        if (cleanText) {
            formData.append('manual_text', cleanText);
        }

        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
            showUploadError(data.error || 'upload_failed', doUpload);
            isUploading = false;
            return;
        }

        showUploadSuccess();
        setTimeout(() => {
            window.location.href = '/status';
        }, 1500);
    } catch (err) {
        showUploadError(err, doUpload);
        isUploading = false;
    }
}

uploadBtn.addEventListener('click', doUpload);
