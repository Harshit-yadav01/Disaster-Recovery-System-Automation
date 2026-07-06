// ---------------------------------------------------------------------------
// Tiny API client shared by the login page and the dashboard.
// Handles auth token storage and authenticated fetches to the backend.
// ---------------------------------------------------------------------------
(function () {
    const cfg = window.APP_CONFIG;

    const api = {
        getToken() {
            return localStorage.getItem(cfg.TOKEN_KEY);
        },

        setSession(token, username) {
            localStorage.setItem(cfg.TOKEN_KEY, token);
            if (username) localStorage.setItem(cfg.USER_KEY, username);
        },

        clearSession() {
            localStorage.removeItem(cfg.TOKEN_KEY);
        },

        isAuthenticated() {
            return Boolean(this.getToken());
        },

        // Log in and store the returned JWT. Throws on invalid credentials.
        async login(username, password, environment) {
            const res = await fetch(`${cfg.API_BASE}/auth/login`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password, environment }),
            });

            if (!res.ok) {
                const detail = await safeDetail(res);
                throw new Error(detail || "Login failed");
            }

            const data = await res.json();
            this.setSession(data.access_token, data.username);
            return data;
        },

        logout() {
            this.clearSession();
            window.location.href = "login.html";
        },

        // Authenticated GET that returns parsed JSON.
        async get(path) {
            const res = await fetch(`${cfg.API_BASE}${path}`, {
                headers: { Authorization: `Bearer ${this.getToken()}` },
            });

            if (res.status === 401) {
                this.clearSession();
                window.location.href = "login.html";
                throw new Error("Session expired");
            }
            if (!res.ok) {
                throw new Error(`Request failed (${res.status})`);
            }
            return res.json();
        },

        getDashboard() {
            return this.get("/dashboard");
        },
    };

    async function safeDetail(res) {
        try {
            const data = await res.json();
            return data.detail;
        } catch {
            return null;
        }
    }

    window.api = api;
})();
