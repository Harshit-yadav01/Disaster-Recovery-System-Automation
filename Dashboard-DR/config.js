// ---------------------------------------------------------------------------
// Frontend runtime configuration.
//
// When the dashboard is served by the FastAPI backend (recommended), the API
// lives on the same origin under /api. If you instead open the HTML files with
// a separate static server (e.g. VS Code Live Server on :5500), we point at the
// backend on :8000. Adjust API_BASE below if your backend runs elsewhere.
// ---------------------------------------------------------------------------
(function () {
    const sameOrigin = `${window.location.origin}/api`;
    const servedByBackend =
        window.location.pathname.startsWith("/app") ||
        window.location.port === "8000";

    window.APP_CONFIG = {
        API_BASE: servedByBackend ? sameOrigin : "http://127.0.0.1:8000/api",
        TOKEN_KEY: "drToken",
        USER_KEY: "drUser",
        // Auto-refresh interval for live dashboard data (ms).
        REFRESH_MS: 30000,
    };
})();
