/* ════════════════════════════════════════
   AUTH.JS
   Handles login and register modal on Landing.html.
   On success, server returns { redirect: "/upload_page" }
   and JS performs the navigation.
════════════════════════════════════════ */

const overlay        = document.getElementById('authOverlay');
const loginPanel     = document.getElementById('loginPanel');
const registerPanel  = document.getElementById('registerPanel');
const loginError     = document.getElementById('loginError');
const registerError  = document.getElementById('registerError');

// ── OPEN / CLOSE ──────────────────────────────────────

function openAuth(panel = 'login') {
    overlay.classList.remove('hidden');
    showPanel(panel);
}

function closeAuth() {
    overlay.classList.add('hidden');
    clearErrors();
}

function showPanel(name) {
    loginPanel.classList.toggle('hidden',    name !== 'login');
    registerPanel.classList.toggle('hidden', name !== 'register');
    clearErrors();
}

document.getElementById('openAuthBtn')  .addEventListener('click', () => openAuth('login'));
document.getElementById('heroGetStarted').addEventListener('click', () => openAuth('login'));
document.getElementById('closeAuthBtn') .addEventListener('click', closeAuth);
document.getElementById('goToRegister') .addEventListener('click', () => showPanel('register'));
document.getElementById('goToLogin')    .addEventListener('click', () => showPanel('login'));

// Close when clicking outside the modal box
overlay.addEventListener('click', e => {
    if (e.target === overlay) closeAuth();
});

// Allow keyboard close
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeAuth();
});


// ── ERROR HELPERS ─────────────────────────────────────

function showError(el, msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
}

function clearErrors() {
    loginError.classList.add('hidden');
    registerError.classList.add('hidden');
}


// ── SHARED FETCH HELPER ───────────────────────────────

async function authFetch(url, body, errorEl, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = 'Please wait…';
    clearErrors();

    try {
        const res  = await fetch(url, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body)
        });
        const data = await res.json();

        if (!res.ok) {
            showError(errorEl, data.error || 'Something went wrong. Try again.');
            return;
        }

        // Server returns { redirect: "/upload_page" } on success
        if (data.redirect) {
            window.location.href = data.redirect;
        }

    } catch (err) {
        showError(errorEl, 'Network error. Please check your connection.');
    } finally {
        btnEl.disabled = false;
        // Button text is restored by panel re-render on error;
        // on success, page navigates away so it doesn't matter.
    }
}


// ── LOGIN ─────────────────────────────────────────────

const loginSubmit = document.getElementById('loginSubmit');

loginSubmit.addEventListener('click', () => {

    const identifier = document.getElementById('loginIdentifier').value.trim();
    const password   = document.getElementById('loginPassword').value;
    const remember   = document.getElementById('loginRemember').checked;

    if (!identifier || !password) {
        showError(loginError, 'Please fill in all fields.');
        return;
    }

    loginSubmit.textContent = 'Logging in…';

    authFetch('/login', { identifier, password, remember }, loginError, loginSubmit);
});

// Submit on Enter key
document.getElementById('loginPassword').addEventListener('keydown', e => {
    if (e.key === 'Enter') loginSubmit.click();
});


// ── REGISTER ──────────────────────────────────────────

const registerSubmit = document.getElementById('registerSubmit');

registerSubmit.addEventListener('click', () => {

    const username = document.getElementById('regUsername').value.trim();
    const email    = document.getElementById('regEmail').value.trim();
    const password = document.getElementById('regPassword').value;
    const confirm  = document.getElementById('regConfirm').value;

    if (!username || !email || !password || !confirm) {
        showError(registerError, 'Please fill in all fields.');
        return;
    }
    if (password.length < 8) {
        showError(registerError, 'Password must be at least 8 characters.');
        return;
    }
    if (password !== confirm) {
        showError(registerError, 'Passwords do not match.');
        return;
    }

    registerSubmit.textContent = 'Creating account…';

    authFetch('/register', { username, email, password }, registerError, registerSubmit);
});

document.getElementById('regConfirm').addEventListener('keydown', e => {
    if (e.key === 'Enter') registerSubmit.click();
});