/* auth.js — login/register modal. On success: reload landing page. */

const overlay       = document.getElementById('authOverlay');
const loginPanel    = document.getElementById('loginPanel');
const registerPanel = document.getElementById('registerPanel');
const loginError    = document.getElementById('loginError');
const registerError = document.getElementById('registerError');

function openAuth(panel = 'login') { overlay.classList.remove('hidden'); showPanel(panel); }
function closeAuth() { overlay.classList.add('hidden'); clearErrors(); }
function showPanel(name) {
    loginPanel.classList.toggle('hidden',    name !== 'login');
    registerPanel.classList.toggle('hidden', name !== 'register');
    clearErrors();
}

document.getElementById('openAuthBtn')?.addEventListener('click',   () => openAuth('login'));
document.getElementById('heroGetStarted')?.addEventListener('click', () => openAuth('login'));
document.getElementById('closeAuthBtn')?.addEventListener('click',  closeAuth);
document.getElementById('goToRegister')?.addEventListener('click',  () => showPanel('register'));
document.getElementById('goToLogin')?.addEventListener('click',     () => showPanel('login'));
overlay?.addEventListener('click', e => { if (e.target === overlay) closeAuth(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAuth(); });

function showError(el, msg) { el.textContent = msg; el.classList.remove('hidden'); }
function clearErrors() {
    loginError?.classList.add('hidden');
    registerError?.classList.add('hidden');
}

async function authFetch(url, body, errorEl, btnEl, btnLabel) {
    btnEl.disabled = true; btnEl.textContent = 'Please wait…'; clearErrors();
    try {
        const res  = await fetch(url, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (!res.ok) {
            showError(errorEl, data.error || 'Something went wrong.');
            btnEl.disabled = false; btnEl.textContent = btnLabel;
            return;
        }
        window.location.reload();
    } catch (err) {
        showError(errorEl, 'Network error. Check your connection.');
        btnEl.disabled = false; btnEl.textContent = btnLabel;
    }
}

// ── LOGIN ──────────────────────────────────────────
const loginSubmit = document.getElementById('loginSubmit');
loginSubmit?.addEventListener('click', () => {
    const identifier = document.getElementById('loginIdentifier').value.trim();
    const password   = document.getElementById('loginPassword').value;
    const remember   = document.getElementById('loginRemember').checked;
    if (!identifier || !password) { showError(loginError, 'Please fill in all fields.'); return; }
    authFetch('/login', {identifier, password, remember}, loginError, loginSubmit, 'Log In');
});
document.getElementById('loginPassword')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') loginSubmit?.click();
});

// ── REGISTER ──────────────────────────────────────
const registerSubmit = document.getElementById('registerSubmit');
registerSubmit?.addEventListener('click', () => {
    const username = document.getElementById('regUsername').value.trim();
    const email    = document.getElementById('regEmail').value.trim();
    const password = document.getElementById('regPassword').value;
    const confirm  = document.getElementById('regConfirm').value;
    const course   = document.getElementById('regCourse').value.trim();

    if (!username || !email || !password || !confirm) {
        showError(registerError, 'Please fill in all required fields.'); return; }
    if (password.length < 8) {
        showError(registerError, 'Password must be at least 8 characters.'); return; }
    if (password !== confirm) {
        showError(registerError, 'Passwords do not match.'); return; }
    if (course.length > 50) {
        showError(registerError, 'Course name must be 50 characters or fewer.'); return; }

    authFetch('/register', {username, email, password, course},
              registerError, registerSubmit, 'Create Account');
});
document.getElementById('regConfirm')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') registerSubmit?.click();
});