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

// ── LOGIN TABS & OTP ────────────────────────────────
const tabPassword = document.getElementById('loginTabPassword');
const tabOTP = document.getElementById('loginTabOTP');
const passwordForm = document.getElementById('passwordLoginForm');
const otpForm = document.getElementById('otpLoginForm');

tabPassword?.addEventListener('click', () => {
    tabPassword.classList.add('active');
    tabOTP?.classList.remove('active');
    passwordForm?.classList.remove('hidden');
    otpForm?.classList.add('hidden');
    clearErrors();
});

tabOTP?.addEventListener('click', () => {
    tabOTP.classList.add('active');
    tabPassword?.classList.remove('active');
    otpForm?.classList.remove('hidden');
    passwordForm?.classList.add('hidden');
    clearErrors();
});

const sendOtpBtn = document.getElementById('sendOtpBtn');
const otpVerifySection = document.getElementById('otpVerifySection');
const verifyOtpSubmit = document.getElementById('verifyOtpSubmit');

sendOtpBtn?.addEventListener('click', async () => {
    const email = document.getElementById('otpEmail').value.trim();
    if (!email) {
        showError(loginError, 'Please enter your email address.');
        return;
    }

    sendOtpBtn.disabled = true;
    sendOtpBtn.textContent = 'Sending…';
    clearErrors();

    try {
        const res = await fetch('/otp/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        if (!res.ok) {
            showError(loginError, data.error || 'Failed to send OTP.');
            sendOtpBtn.disabled = false;
            sendOtpBtn.textContent = 'Send OTP';
            return;
        }

        otpVerifySection?.classList.remove('hidden');
        sendOtpBtn.textContent = 'Resend OTP';
        sendOtpBtn.disabled = false;
        setTimeout(() => {
            document.querySelector('#loginOtpContainer .otp-digit')?.focus();
        }, 50);
    } catch (err) {
        showError(loginError, 'Network error. Please try again.');
        sendOtpBtn.disabled = false;
        sendOtpBtn.textContent = 'Send OTP';
    }
});

verifyOtpSubmit?.addEventListener('click', async () => {
    const email = document.getElementById('otpEmail').value.trim();
    const otpContainer = document.getElementById('loginOtpContainer');
    const otp = getOtpCode(otpContainer);

    if (!email || otp.length < 6) {
        showError(loginError, 'Please enter email and 6-digit OTP.');
        applyOtpStyle(otpContainer, false);
        return;
    }

    verifyOtpSubmit.disabled = true;
    verifyOtpSubmit.textContent = 'Verifying…';
    clearErrors();

    try {
        const res = await fetch('/otp/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, otp })
        });
        const data = await res.json();
        if (!res.ok) {
            showError(loginError, data.error || 'Invalid or expired OTP.');
            verifyOtpSubmit.disabled = false;
            verifyOtpSubmit.textContent = 'Verify & Log In';
            applyOtpStyle(otpContainer, false);
            return;
        }

        applyOtpStyle(otpContainer, true);
        setTimeout(() => {
            window.location.href = data.redirect || '/';
        }, 600);
    } catch (err) {
        showError(loginError, 'Network error. Please try again.');
        verifyOtpSubmit.disabled = false;
        verifyOtpSubmit.textContent = 'Verify & Log In';
        applyOtpStyle(otpContainer, false);
    }
});

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
    const fullName = document.getElementById('regFullName').value.trim();
    const username = document.getElementById('regUsername').value.trim();
    const email    = document.getElementById('regEmail').value.trim();
    const password = document.getElementById('regPassword').value;
    const confirm  = document.getElementById('regConfirm').value;
    const course   = document.getElementById('regCourse').value.trim();

    if (!fullName || !username || !email || !password || !confirm || !course) {
        showError(registerError, 'Please fill in all required fields.'); return; }
    if (fullName.length > 50) {
        showError(registerError, 'Full name must be 50 characters or fewer.'); return; }
    if (!/^[A-Za-z\s]+$/.test(fullName)) {
        showError(registerError, 'Full name must contain only letters and spaces.'); return; }
    if (username.length < 3) {
        showError(registerError, 'Username must be at least 3 characters.'); return; }
    if (password.length < 8) {
        showError(registerError, 'Password must be at least 8 characters.'); return; }
    if (password !== confirm) {
        showError(registerError, 'Passwords do not match.'); return; }
    if (course.length > 50) {
        showError(registerError, 'Course name must be 50 characters or fewer.'); return; }

    authFetch('/register', {full_name: fullName, username, email, password, course},
              registerError, registerSubmit, 'Create Account');
});
document.getElementById('regConfirm')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') registerSubmit?.click();
});

// ── OTP HELPER FUNCTIONS ──
function getOtpCode(container) {
    if (!container) return '';
    const inputs = container.querySelectorAll('.otp-digit');
    let code = '';
    inputs.forEach(input => code += input.value.trim());
    return code;
}

function resetOtpStyles(container) {
    if (!container) return;
    const inputs = container.querySelectorAll('.otp-digit');
    inputs.forEach(input => {
        input.classList.remove('success', 'error');
    });
}

function applyOtpStyle(container, isSuccess) {
    if (!container) return;
    const inputs = container.querySelectorAll('.otp-digit');
    inputs.forEach(input => {
        input.classList.remove('success', 'error');
        input.classList.add(isSuccess ? 'success' : 'error');
    });
}

function initOtpInputs(containerId, submitBtnId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const inputs = container.querySelectorAll('.otp-digit');
    const submitBtn = document.getElementById(submitBtnId);

    inputs.forEach((input, index) => {
        input.addEventListener('input', (e) => {
            const val = e.target.value;
            e.target.value = val.replace(/[^0-9]/g, '');
            if (e.target.value.length > 0) {
                if (index < inputs.length - 1) {
                    inputs[index + 1].focus();
                }
            }
            resetOtpStyles(container);
        });

        input.addEventListener('keydown', (e) => {
            resetOtpStyles(container);
            if (e.key === 'Backspace') {
                if (input.value === '') {
                    if (index > 0) {
                        inputs[index - 1].focus();
                        inputs[index - 1].value = '';
                    }
                } else {
                    input.value = '';
                }
                e.preventDefault();
            } else if (e.key === 'ArrowLeft') {
                if (index > 0) inputs[index - 1].focus();
            } else if (e.key === 'ArrowRight') {
                if (index < inputs.length - 1) inputs[index + 1].focus();
            } else if (e.key === 'Enter') {
                submitBtn?.click();
            }
        });

        input.addEventListener('paste', (e) => {
            e.preventDefault();
            resetOtpStyles(container);
            const pastedData = (e.clipboardData || window.clipboardData).getData('text').trim();
            if (/^\d{6}$/.test(pastedData)) {
                for (let i = 0; i < inputs.length; i++) {
                    inputs[i].value = pastedData[i];
                }
                inputs[inputs.length - 1].focus();
            } else if (/^\d+$/.test(pastedData)) {
                let fillLen = Math.min(pastedData.length, inputs.length - index);
                for (let i = 0; i < fillLen; i++) {
                    inputs[index + i].value = pastedData[i];
                }
                inputs[Math.min(index + fillLen, inputs.length - 1)].focus();
            }
        });
    });
}

// Initialize OTP inputs for login
initOtpInputs('loginOtpContainer', 'verifyOtpSubmit');