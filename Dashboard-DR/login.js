// Password visibility toggle
const pwd = document.getElementById("password");
const pwdToggle = document.getElementById("pwdToggle");

function togglePassword() {
    const showing = pwd.type === "text";
    pwd.type = showing ? "password" : "text";
    pwdToggle.classList.toggle("fa-eye-slash", showing);
    pwdToggle.classList.toggle("fa-eye", !showing);
    pwdToggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
}

pwdToggle.addEventListener("click", togglePassword);
pwdToggle.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        togglePassword();
    }
});

// Sign in -> authenticate against the backend, store the JWT, then load the
// dashboard (index.html). Shows an inline error if credentials are rejected.
const form = document.getElementById("loginForm");
const errorEl = document.getElementById("loginError");
const submitBtn = form.querySelector(".btn-signin");

function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = false;
}

function clearError() {
    if (errorEl) errorEl.hidden = true;
}

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError();

    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;

    if (!username || !password) {
        showError("Please enter both username and password.");
        return;
    }

    submitBtn.disabled = true;
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = 'Signing in… <i class="fa-solid fa-spinner fa-spin"></i>';

    try {
        await window.api.login(username, password);
        window.location.href = "index.html";
    } catch (err) {
        showError(err.message || "Unable to sign in. Please try again.");
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
});
