// Keep one short-lived access token current for both classic and module scripts.
(() => {
    const nativeFetch = window.fetch.bind(window);
    const refreshWindowMs = 5 * 60 * 1000;
    const recentActivityMs = 60 * 1000;
    const retryDelayMs = 30 * 1000;
    let lastActivityAt = Date.now();
    let lastCheckAt = 0;
    let lastFailureAt = -Infinity;
    let blockedToken = null;
    let refreshPromise = null;
    let cachedToken = null;
    let cachedExpiresAt = 0;

    function getToken() {
        return localStorage.getItem('access_token');
    }

    function expiresAt(token) {
        if (token === cachedToken) return cachedExpiresAt;
        cachedToken = token;
        cachedExpiresAt = 0;
        try {
            const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            cachedExpiresAt = Number(JSON.parse(atob(payload)).exp) * 1000 || 0;
        } catch (_) {
            // A malformed token cannot be renewed by the client.
        }
        return cachedExpiresAt;
    }

    function isExpired() {
        const token = getToken();
        return !token || expiresAt(token) <= Date.now();
    }

    function refreshIfNeeded() {
        const token = getToken();
        if (!token || token === blockedToken) return Promise.resolve(token);
        const now = Date.now();
        const remaining = expiresAt(token) - now;
        if (remaining <= 0 || remaining > refreshWindowMs
            || now - lastActivityAt > recentActivityMs
            || document.visibilityState === 'hidden'
            || now - lastFailureAt < retryDelayMs) return Promise.resolve(token);
        if (refreshPromise) return refreshPromise;
        // При возвращении на вкладку зависший refresh не должен навсегда
        // задержать все последующие запросы страницы.
        const controller = new AbortController();
        let timeout;
        const deadline = new Promise((resolve, reject) => {
            timeout = setTimeout(() => {
                controller.abort();
                reject(new Error('Token refresh timed out'));
            }, 10000);
        });
        const renewal = nativeFetch('/auth/refresh', {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
        }).then(async response => {
            if (response.status === 401) blockedToken = token;
            if (!response.ok) throw new Error(`Token refresh failed: ${response.status}`);
            const data = await response.json();
            if (!data.access_token) throw new Error('Token refresh returned no access token');
            if (getToken() === token) localStorage.setItem('access_token', data.access_token);
            blockedToken = null;
            lastFailureAt = -Infinity;
            return getToken();
        });
        refreshPromise = Promise.race([renewal, deadline]).catch(() => {
            lastFailureAt = Date.now();
            return getToken();
        }).finally(() => { clearTimeout(timeout); refreshPromise = null; });
        return refreshPromise;
    }

    function noteActivity() {
        const now = Date.now();
        if (now - lastActivityAt >= 1000) lastActivityAt = now;
        if (now - lastCheckAt < 30000) return;
        lastCheckAt = now;
        void refreshIfNeeded();
    }

    for (const event of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']) {
        document.addEventListener(event, noteActivity, { passive: true });
    }
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') noteActivity();
    });

    window.fetch = async (input, options) => {
        const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url,
            window.location.href);
        if (url.origin !== window.location.origin || url.pathname === '/auth/refresh') {
            return nativeFetch(input, options);
        }
        const headers = new Headers(options?.headers || input?.headers);
        if (!/^Bearer\s+/i.test(headers.get('Authorization') || '')) {
            return nativeFetch(input, options);
        }
        await refreshIfNeeded();
        const token = getToken();
        if (token) headers.set('Authorization', `Bearer ${token}`);
        return nativeFetch(input, { ...options, headers });
    };

    window.authSession = { getToken, isExpired, refreshIfNeeded };
})();
