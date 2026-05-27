/*
   STUDYFLOW-UX.JS - shared progress, error, toast, and chat feedback
*/

class StudyFlowLoader {
    constructor(containerEl, stages, options = {}) {
        this.container = containerEl;
        this.stages = stages.map(stage => (
            typeof stage === 'string' ? { label: stage } : stage
        ));
        this.checkpoints = options.checkpoints || [];
        this.idleMessages = options.idleMessages || [
            'Keeping everything moving in the background.',
            'Still shaping this into something useful.',
            'A few more pieces are coming together.',
            'Thanks for hanging tight, this step can take a moment.',
            'Almost there. We are polishing the result now.'
        ];
        this.currentIdx = -1;
        this.percent = 0;
        this._msgTimer = null;
        this._built = false;
    }

    _build() {
        if (this._built || !this.container) return;
        this._built = true;
        this.container.innerHTML = '';
        this.container.classList.remove('sf-hidden');

        const root = document.createElement('div');
        root.className = 'sf-progress';

        this._percentEl = document.createElement('div');
        this._percentEl.className = 'sf-progress-percent';
        this._percentEl.textContent = '0%';
        root.appendChild(this._percentEl);

        const track = document.createElement('div');
        track.className = 'sf-progress-bar-track';
        this._barFill = document.createElement('div');
        this._barFill.className = 'sf-progress-bar-fill';
        track.appendChild(this._barFill);
        root.appendChild(track);

        const list = document.createElement('ul');
        list.className = 'sf-stages';
        this._stageEls = [];

        this.stages.forEach(stage => {
            const li = document.createElement('li');
            li.className = 'sf-stage pending';

            const icon = document.createElement('span');
            icon.className = 'sf-stage-icon';

            const copy = document.createElement('span');
            copy.className = 'sf-stage-copy';

            const label = document.createElement('span');
            label.className = 'sf-stage-label';
            label.textContent = stage.label;
            copy.appendChild(label);

            if (stage.detail) {
                const detail = document.createElement('span');
                detail.className = 'sf-stage-detail';
                detail.textContent = stage.detail;
                copy.appendChild(detail);
            }

            li.appendChild(icon);
            li.appendChild(copy);
            list.appendChild(li);
            this._stageEls.push(li);
        });
        root.appendChild(list);

        if (this.checkpoints.length) {
            const checkpoints = document.createElement('div');
            checkpoints.className = 'sf-checkpoints';
            this._checkpointEls = this.checkpoints.map(label => {
                const item = document.createElement('span');
                item.className = 'sf-checkpoint pending';
                item.textContent = label;
                checkpoints.appendChild(item);
                return item;
            });
            root.appendChild(checkpoints);
        } else {
            this._checkpointEls = [];
        }

        this._msgEl = document.createElement('div');
        this._msgEl.className = 'sf-progress-message';
        root.appendChild(this._msgEl);

        this.container.appendChild(root);
    }

    start() {
        this._build();
        this.container.classList.remove('sf-hidden');
        this.advance(0);
    }

    advance(stageIndex) {
        if (stageIndex < 0 || stageIndex >= this.stages.length) return;
        this._build();
        this.currentIdx = stageIndex;

        this._stageEls.forEach((el, i) => {
            el.classList.remove('pending', 'active', 'completed');
            if (i < stageIndex) el.classList.add('completed');
            else if (i === stageIndex) el.classList.add('active');
            else el.classList.add('pending');
        });

        const pct = Math.round(((stageIndex + 1) / this.stages.length) * 100);
        this.setProgress(pct);
        this._setMessage(this.stages[stageIndex].detail || this.stages[stageIndex].label);
        this._updateCheckpoints(stageIndex);
        this._startIdleMessages();
    }

    setProgress(percent) {
        this.percent = Math.min(100, Math.max(0, Math.round(percent)));
        if (this._barFill) this._barFill.style.width = this.percent + '%';
        if (this._percentEl) this._percentEl.textContent = this.percent + '%';
    }

    complete(message) {
        this._stopIdleMessages();
        this.currentIdx = this.stages.length;
        this._stageEls?.forEach(el => {
            el.classList.remove('pending', 'active');
            el.classList.add('completed');
        });
        this._checkpointEls?.forEach(el => {
            el.classList.remove('pending');
            el.classList.add('completed');
        });
        this.setProgress(100);
        this._setMessage(message || 'Everything is ready.');
    }

    showSuccess(title, subtitle) {
        this._stopIdleMessages();
        this.container.innerHTML = '';

        const root = document.createElement('div');
        root.className = 'sf-success';

        const icon = document.createElement('div');
        icon.className = 'sf-success-icon';
        icon.textContent = '✓';

        const heading = document.createElement('div');
        heading.className = 'sf-success-title';
        heading.textContent = title || 'Ready';

        root.appendChild(icon);
        root.appendChild(heading);

        if (subtitle) {
            const sub = document.createElement('div');
            sub.className = 'sf-success-sub';
            sub.textContent = subtitle;
            root.appendChild(sub);
        }

        this.container.appendChild(root);
    }

    hide() {
        this._stopIdleMessages();
        this.container.classList.add('sf-hidden');
    }

    reset() {
        this._stopIdleMessages();
        this._built = false;
        this.currentIdx = -1;
        this.percent = 0;
        if (this.container) {
            this.container.innerHTML = '';
            this.container.classList.add('sf-hidden');
        }
    }

    _setMessage(message) {
        if (!this._msgEl) return;
        this._msgEl.classList.add('fading');
        window.setTimeout(() => {
            this._msgEl.textContent = message;
            this._msgEl.classList.remove('fading');
        }, 120);
    }

    _updateCheckpoints(stageIndex) {
        if (!this._checkpointEls?.length) return;
        const completedCount = Math.min(this._checkpointEls.length, stageIndex);
        this._checkpointEls.forEach((el, i) => {
            el.classList.toggle('completed', i < completedCount);
            el.classList.toggle('pending', i >= completedCount);
        });
    }

    _startIdleMessages() {
        this._stopIdleMessages();
        let idx = 0;
        this._msgTimer = window.setInterval(() => {
            this._setMessage(this.idleMessages[idx % this.idleMessages.length]);
            idx++;
        }, 5200);
    }

    _stopIdleMessages() {
        if (this._msgTimer) {
            window.clearInterval(this._msgTimer);
            this._msgTimer = null;
        }
    }
}

class StudyFlowError {
    static show(container, { title, what, why, action, retryFn, dismissFn }) {
        if (!container) return null;
        container.querySelectorAll('.sf-error-card').forEach(el => el.remove());

        const card = document.createElement('div');
        card.className = 'sf-error-card';

        const header = document.createElement('div');
        header.className = 'sf-error-header';

        const icon = document.createElement('div');
        icon.className = 'sf-error-icon';
        icon.textContent = '!';

        const titleEl = document.createElement('div');
        titleEl.className = 'sf-error-title';
        titleEl.textContent = title || 'Something needs attention';

        header.appendChild(icon);
        header.appendChild(titleEl);
        card.appendChild(header);

        const sections = document.createElement('div');
        sections.className = 'sf-error-sections';
        if (what) sections.appendChild(StudyFlowError._section('What happened', what));
        if (why) sections.appendChild(StudyFlowError._section('Why it happened', why));
        if (action) sections.appendChild(StudyFlowError._section('What you can do', action));
        card.appendChild(sections);

        const actions = document.createElement('div');
        actions.className = 'sf-error-actions';
        if (retryFn) {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'sf-error-retry-btn';
            retryBtn.textContent = 'Try again';
            retryBtn.addEventListener('click', () => {
                card.remove();
                retryFn();
            });
            actions.appendChild(retryBtn);
        }

        const dismissBtn = document.createElement('button');
        dismissBtn.className = 'sf-error-dismiss-btn';
        dismissBtn.textContent = 'Dismiss';
        dismissBtn.addEventListener('click', () => {
            card.classList.add('leaving');
            window.setTimeout(() => {
                card.remove();
                if (dismissFn) dismissFn();
            }, 280);
        });
        actions.appendChild(dismissBtn);
        card.appendChild(actions);

        container.appendChild(card);
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return card;
    }

    static _section(label, text) {
        const section = document.createElement('div');
        section.className = 'sf-error-section';

        const heading = document.createElement('div');
        heading.className = 'sf-error-section-label';
        heading.textContent = label;

        const body = document.createElement('div');
        body.className = 'sf-error-section-text';
        body.textContent = text;

        section.appendChild(heading);
        section.appendChild(body);
        return section;
    }

    static forUpload(errorMsg) {
        const msg = StudyFlowError._message(errorMsg);

        if (msg.includes('network') || msg.includes('failed to fetch') || msg.includes('connection')) {
            return StudyFlowError.forNetwork();
        }
        if (msg.includes('unsupported file type') || msg.includes('unsupported format')) {
            if (msg.includes('png') || msg.includes('jpg') || msg.includes('jpeg') || msg.includes('webp') || msg.includes('image')) {
                return {
                    title: 'Unsupported Image Format',
                    what: 'This image format cannot be read by StudyFlow.',
                    why: 'The image may use a format outside the supported upload list.',
                    action: 'Upload a PNG, JPG, JPEG, or WebP image, or convert it to PDF.'
                };
            }
            return {
                title: 'Unsupported File Format',
                what: 'This file type is not supported yet.',
                why: 'StudyFlow can currently read PDF, DOCX, PNG, JPG, JPEG, and WebP files.',
                action: 'Convert the file to one of those formats and upload it again.'
            };
        }
        if (msg.includes('empty') || msg.includes('no valid files') || msg.includes('no content') || msg.includes('blank')) {
            return {
                title: 'Empty File Upload',
                what: 'There was no readable content to study from.',
                why: 'The file may be blank, unsaved, or missing from the upload.',
                action: 'Choose a file with syllabus or datesheet content, or paste the text manually.'
            };
        }
        if (msg.includes('pdf') && (msg.includes('read') || msg.includes('extract') || msg.includes('parse') || msg.includes('process'))) {
            return {
                title: 'Could Not Read PDF',
                what: 'The PDF could not be processed clearly.',
                why: 'It may be scanned, password-protected, corrupted, or saved in a way that hides text.',
                action: 'Try a clearer PDF, remove password protection, or paste the syllabus text instead.'
            };
        }
        if ((msg.includes('doc') || msg.includes('docx')) && (msg.includes('corrupt') || msg.includes('read') || msg.includes('process'))) {
            return {
                title: 'Could Not Read Document',
                what: 'The Word document could not be opened safely.',
                why: 'The file may be corrupted, partially downloaded, or saved in an older format.',
                action: 'Re-save it as DOCX or PDF, then upload the fresh copy.'
            };
        }
        if (msg.includes('blurry') || msg.includes('scan') || msg.includes('scanned') || msg.includes('quality') || msg.includes('unreadable')) {
            return {
                title: 'Image Too Blurry To Read',
                what: 'The scanned image did not contain enough readable detail.',
                why: 'Low resolution, shadows, tilted pages, or poor lighting can hide important text.',
                action: 'Upload a clearer scan, crop the page tightly, or paste the text manually.'
            };
        }
        if (msg.includes('timeout') || msg.includes('timed out')) {
            return {
                title: 'Upload Took Too Long',
                what: 'Processing did not finish in time.',
                why: 'Large files or a busy server can slow down content reading.',
                action: 'Try again in a moment, or upload fewer/smaller files.'
            };
        }

        return {
            title: 'Upload Failed',
            what: 'StudyFlow could not process the content.',
            why: 'Something unexpected happened while reading the upload.',
            action: 'Try again, use a clearer file, or paste the syllabus text manually.'
        };
    }

    static forGeneration(errorMsg) {
        const msg = StudyFlowError._message(errorMsg);

        if (msg.includes('network') || msg.includes('failed to fetch') || msg.includes('connection')) {
            return StudyFlowError.forNetwork();
        }
        if (msg.includes('timeout') || msg.includes('timed out')) {
            return {
                title: 'Schedule Took Too Long',
                what: 'StudyFlow needed more time than expected to build the schedule.',
                why: 'This can happen with many subjects, long date ranges, or temporary server load.',
                action: 'Try again in a moment, or simplify the schedule inputs before regenerating.'
            };
        }
        if (msg.includes('json') || msg.includes('schema') || msg.includes('invalid')) {
            return {
                title: 'Schedule Format Needed A Retry',
                what: 'The AI response did not match the schedule format StudyFlow needs.',
                why: 'Occasionally the model returns incomplete or malformed structure.',
                action: 'Try again. StudyFlow will ask for a clean schedule format on the next attempt.'
            };
        }
        if (msg.includes('all') && msg.includes('models') && msg.includes('failed')) {
            return {
                title: 'AI Generation Failed',
                what: 'The schedule could not be generated right now.',
                why: 'The AI service did not return a usable answer after retries.',
                action: 'Try again in a few minutes. If it repeats, reduce the number of topics or days.'
            };
        }
        if (msg.includes('too many') || msg.includes('rate limit')) {
            return {
                title: 'Too Many Requests',
                what: 'StudyFlow is slowing requests down for a moment.',
                why: 'This protects generation quality and keeps the app stable for everyone.',
                action: 'Wait a little while, then generate again.'
            };
        }
        if (msg.includes('save') || msg.includes('status')) {
            return {
                title: 'Could Not Save Your Inputs',
                what: 'Your study preferences were not saved before generation.',
                why: 'There may have been a connection issue or temporary server problem.',
                action: 'Check your connection and try again.'
            };
        }

        return {
            title: 'Could Not Generate Schedule',
            what: 'StudyFlow could not create the schedule this time.',
            why: 'The AI service or server hit a temporary issue.',
            action: 'Try again, or adjust study hours and preferences before regenerating.'
        };
    }

    static forNetwork() {
        return {
            title: 'Connection Interrupted',
            what: 'StudyFlow could not reach the server.',
            why: 'Your internet connection may have dropped or the server may be temporarily unavailable.',
            action: 'Check your connection, then try again.'
        };
    }

    static _message(errorMsg) {
        if (!errorMsg) return '';
        if (errorMsg instanceof Error) return String(errorMsg.message || '');
        return String(errorMsg).toLowerCase();
    }
}

class StudyFlowToast {
    static _container = null;

    static _ensureContainer() {
        if (!StudyFlowToast._container) {
            StudyFlowToast._container = document.createElement('div');
            StudyFlowToast._container.className = 'sf-toast-container';
            document.body.appendChild(StudyFlowToast._container);
        }
        return StudyFlowToast._container;
    }

    static _show(type, icon, message, duration = 3500) {
        const container = StudyFlowToast._ensureContainer();
        const toast = document.createElement('div');
        toast.className = `sf-toast ${type}`;

        const iconEl = document.createElement('span');
        iconEl.className = 'sf-toast-icon';
        iconEl.textContent = icon;

        const msgEl = document.createElement('span');
        msgEl.textContent = message;

        toast.appendChild(iconEl);
        toast.appendChild(msgEl);
        container.appendChild(toast);

        window.setTimeout(() => {
            toast.classList.add('leaving');
            window.setTimeout(() => toast.remove(), 300);
        }, duration);

        return toast;
    }

    static success(message, duration) {
        return StudyFlowToast._show('success', '✓', message, duration);
    }

    static error(message, duration) {
        return StudyFlowToast._show('error', '!', message, duration || 5000);
    }

    static info(message, duration) {
        return StudyFlowToast._show('info', 'i', message, duration);
    }
}

class StudyFlowChatError {
    static create(title, text, retryFn) {
        const card = document.createElement('div');
        card.className = 'sf-chat-error';

        const heading = document.createElement('div');
        heading.className = 'sf-chat-error-title';
        heading.textContent = title || 'Something needs attention';

        const body = document.createElement('div');
        body.className = 'sf-chat-error-text';
        body.textContent = StudyFlowChatError.friendlyText(text);

        card.appendChild(heading);
        card.appendChild(body);

        if (retryFn) {
            const btn = document.createElement('button');
            btn.className = 'sf-chat-error-retry';
            btn.textContent = 'Try again';
            btn.addEventListener('click', () => {
                card.remove();
                retryFn();
            });
            card.appendChild(btn);
        }

        return card;
    }

    static friendlyText(text) {
        const msg = String(text || '').toLowerCase();
        if (msg.includes('network') || msg.includes('connection') || msg.includes('failed to fetch')) {
            return 'The connection dropped while the teacher was responding. Check your connection and try again.';
        }
        if (msg.includes('timeout') || msg.includes('timed out')) {
            return 'The teacher needed more time than expected. Try again in a moment.';
        }
        if (msg.includes('rate limit') || msg.includes('too many')) {
            return 'The teacher is handling too many requests right now. Wait a moment, then retry.';
        }
        if (msg.includes('context') || msg.includes('token')) {
            return 'The conversation has become too large for one response. Ask a shorter follow-up or start a fresh topic.';
        }
        return text || 'Something unexpected happened. Please try again.';
    }
}

window.StudyFlowLoader = StudyFlowLoader;
window.StudyFlowError = StudyFlowError;
window.StudyFlowToast = StudyFlowToast;
window.StudyFlowChatError = StudyFlowChatError;
