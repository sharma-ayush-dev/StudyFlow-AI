/* Upload page: multi-stage progress + friendly error cards */

const fileInput = document.getElementById('fileInput');
const fileNames = document.getElementById('fileNames');
const fileListContainer = document.getElementById('fileListContainer');
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

let uploadedFiles = [];
const ALLOWED_EXTS = ['.pdf', '.docx', '.jpg', '.png', '.jpeg', '.webp', '.txt', '.xlsx', '.pptx', '.ppt'];
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB in bytes

function isValidExtension(filename) {
    const ext = filename.substring(filename.lastIndexOf('.')).toLowerCase();
    return ALLOWED_EXTS.includes(ext);
}

function addFiles(files) {
    let addedCount = 0;
    let invalidCount = 0;
    let duplicateCount = 0;
    let tooLargeCount = 0;
    let combinedTooLargeCount = 0;

    Array.from(files).forEach(file => {
        if (!isValidExtension(file.name)) {
            invalidCount++;
            return;
        }
        if (file.size > MAX_FILE_SIZE) {
            tooLargeCount++;
            return;
        }
        const currentTotal = uploadedFiles.reduce((sum, f) => sum + f.size, 0);
        if (currentTotal + file.size > 15 * 1024 * 1024) {
            combinedTooLargeCount++;
            return;
        }
        const isDuplicate = uploadedFiles.some(f => f.name === file.name && f.size === file.size);
        if (isDuplicate) {
            duplicateCount++;
            return;
        }
        uploadedFiles.push(file);
        addedCount++;
    });

    if (invalidCount > 0) {
        StudyFlowToast.error(`Skipped ${invalidCount} file(s) with unsupported formats.`);
    }
    if (tooLargeCount > 0) {
        StudyFlowToast.error(`Skipped ${tooLargeCount} file(s) exceeding the 5MB size limit.`);
    }
    if (combinedTooLargeCount > 0) {
        StudyFlowToast.error(`Skipped ${combinedTooLargeCount} file(s) as the combined size would exceed the 15MB limit.`);
    }
    if (duplicateCount > 0) {
        StudyFlowToast.info(`Skipped ${duplicateCount} duplicate file(s).`);
    }
    if (addedCount > 0) {
        StudyFlowToast.success(`Added ${addedCount} file(s).`);
    }

    renderFilesList();
}

function removeFile(index) {
    if (index >= 0 && index < uploadedFiles.length) {
        const removed = uploadedFiles.splice(index, 1);
        if (removed.length > 0) {
            StudyFlowToast.info(`Removed ${removed[0].name}`);
        }
        renderFilesList();
    }
}

function renderFilesList() {
    if (!fileListContainer) return;

    if (uploadedFiles.length === 0) {
        fileListContainer.style.display = 'none';
        fileListContainer.innerHTML = '';
        fileNames.style.display = 'inline';
        fileNames.textContent = 'No files chosen';
    } else {
        fileNames.style.display = 'none';
        fileListContainer.style.display = 'flex';
        fileListContainer.innerHTML = '';

        uploadedFiles.forEach((file, index) => {
            const item = document.createElement('div');
            item.className = 'file-item';

            const info = document.createElement('div');
            info.className = 'file-info';

            const icon = document.createElement('span');
            icon.className = 'file-icon';
            icon.textContent = '📄';

            const name = document.createElement('span');
            name.className = 'file-name-text';
            name.textContent = file.name;

            info.appendChild(icon);
            info.appendChild(name);
            item.appendChild(info);

            const removeBtn = document.createElement('button');
            removeBtn.className = 'remove-file-btn';
            removeBtn.innerHTML = '&times;';
            removeBtn.type = 'button';
            removeBtn.title = 'Remove file';
            removeBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                removeFile(index);
            });

            item.appendChild(removeBtn);
            fileListContainer.appendChild(item);
        });
    }
}

fileInput.addEventListener('change', () => {
    addFiles(fileInput.files);
    fileInput.value = '';
});

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
    addFiles(droppedFiles);
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

    const hasFiles = uploadedFiles.length > 0;
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
            uploadedFiles.forEach(file => formData.append('files', file));
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
