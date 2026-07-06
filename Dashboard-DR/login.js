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

// Animated environment dropdown
const selectEnv = document.getElementById("selectEnv");
const envTrigger = document.getElementById("envTrigger");
const envMenu = document.getElementById("envMenu");
const envValue = document.getElementById("envValue");
const envInput = document.getElementById("environment");

function closeEnv() {
    selectEnv.classList.remove("open");
    envTrigger.setAttribute("aria-expanded", "false");
}

envTrigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = selectEnv.classList.toggle("open");
    envTrigger.setAttribute("aria-expanded", String(isOpen));
});

envMenu.querySelectorAll("li").forEach((option) => {
    option.addEventListener("click", () => {
        const value = option.dataset.value;
        envValue.textContent = value;
        envInput.value = value;
        envMenu.querySelectorAll("li").forEach((li) => li.classList.remove("selected"));
        option.classList.add("selected");
        closeEnv();
    });
});

// Close the dropdown when clicking outside
document.addEventListener("click", (e) => {
    if (!selectEnv.contains(e.target)) closeEnv();
});

// Sign in -> store user, then load the dashboard (index.html)
const form = document.getElementById("loginForm");

form.addEventListener("submit", (e) => {
    e.preventDefault();

    const username = document.getElementById("username").value.trim();
    const environment = document.getElementById("environment").value;

    // Persist a display name for the dashboard avatar (no password is stored)
    if (username) {
        localStorage.setItem("drUser", username);
    }
    localStorage.setItem("drEnvironment", environment);

    window.location.href = "index.html";
});
