const GAME_EVENT_SENDERS = new Set(['Бой', 'Система', 'Событие', 'Зависимость', 'Разделка', 'Взрыв', 'Аномалия', 'ГМ', 'Мутант', 'Действие', 'Стресс', 'Проверка']);

export function chatMessageIdentity(username, text, timestamp, id) {
    if (id != null) return `id:${id}`;
    return timestamp ? JSON.stringify([username, text, timestamp]) : null;
}

export function chatMessageKind(username, text) {
    if (/^System\s*\(Roll\)/i.test(String(username)) || /^(?:\/roll(?:\s|$)|🎲)/i.test(String(text))) return 'roll';
    return /^System(?:\s|$|\()/i.test(String(username)) || GAME_EVENT_SENDERS.has(username) ? 'event' : 'human';
}

export function readChatPanelState(storage, key) {
    let saved;
    try { saved = JSON.parse(storage.getItem(key)); } catch { /* Defaults for missing or damaged preferences. */ }
    const position = saved?.floatingPosition;
    return {
        open: saved?.open === true,
        docked: saved?.docked !== false,
        collapsed: saved?.collapsed === true,
        view: saved?.view === 'actions' ? 'actions' : 'all',
        floatingPosition: position && Number.isFinite(position.left) && Number.isFinite(position.top)
            ? { left: position.left, top: position.top } : null,
    };
}

export function initChatPanel() {
    const panel = document.getElementById('panel-chat');
    const toggle = document.getElementById('chat-toggle');
    const pin = document.getElementById('chat-pin');
    const close = document.getElementById('chat-close');
    const actions = document.getElementById('actions-toggle');
    if (!panel || !toggle || !pin || !close || panel.dataset.chatInitialized === 'true') return;
    panel.dataset.chatInitialized = 'true';
    const key = `chat-panel:${window.currentLobbyId}:${localStorage.getItem('user_id')}`;
    const state = readChatPanelState(localStorage, key);
    const scrollLatest = () => requestAnimationFrame(() => {
        if (!state.open || panel.classList.contains('collapsed')) return;
        const messages = panel.querySelector('.chat-messages');
        if (messages) messages.scrollTop = messages.scrollHeight;
    });
    const save = () => {
        try { localStorage.setItem(key, JSON.stringify(state)); } catch { /* Keep chat usable without persistent storage. */ }
    };
    const restoreFloatingPosition = () => {
        const position = state.floatingPosition || { left: Math.max(15, (window.innerWidth - 340) / 2), top: 96 };
        const left = Math.max(0, Math.min(position.left, window.innerWidth - panel.offsetWidth));
        const top = Math.max(0, Math.min(position.top, window.innerHeight - panel.offsetHeight));
        panel.style.left = `${left}px`; panel.style.top = `${top}px`;
        panel.style.right = 'auto'; panel.style.bottom = 'auto'; panel.style.transform = 'none';
    };
    const apply = () => {
        panel.hidden = !state.open;
        panel.dataset.chatDocked = String(state.docked);
        panel.dataset.chatView = state.view;
        const title = panel.querySelector('.chat-panel-title');
        if (title) title.textContent = state.view === 'actions' ? 'Последние действия' : 'Чат';
        const input = panel.querySelector('.chat-input-area');
        if (input) input.hidden = state.view === 'actions';
        panel.classList.toggle('collapsed', state.collapsed);
        const collapse = panel.querySelector('.toggle-btn');
        if (collapse) collapse.textContent = state.collapsed ? '▶' : '▼';
        toggle.setAttribute('aria-expanded', String(state.open && state.view === 'all'));
        toggle.classList.toggle('is-active', state.open && state.view === 'all');
        actions?.classList.toggle('is-active', state.open && state.view === 'actions');
        actions?.setAttribute('aria-expanded', String(state.open && state.view === 'actions'));
        pin.setAttribute('aria-pressed', String(state.docked));
        pin.title = state.docked ? 'Открепить чат' : 'Закрепить чат справа';
        pin.setAttribute('aria-label', pin.title);
        if (state.open && !state.docked) restoreFloatingPosition();
        if (state.open && !state.collapsed) scrollLatest();
    };
    const rememberFloatingPosition = () => {
        if (state.docked || !state.open) return;
        const rect = panel.getBoundingClientRect();
        if (state.floatingPosition?.left === rect.left && state.floatingPosition?.top === rect.top) return;
        state.floatingPosition = { left: rect.left, top: rect.top }; save();
    };
    const selectView = view => {
        state.open = state.open && state.view === view ? false : true;
        state.view = view; apply(); save();
    };
    toggle.addEventListener('click', () => selectView('all'));
    actions?.addEventListener('click', () => selectView('actions'));
    close.addEventListener('click', () => {
        rememberFloatingPosition(); state.open = false; apply(); save();
        (state.view === 'actions' && actions ? actions : toggle).focus();
    });
    pin.addEventListener('click', () => {
        rememberFloatingPosition(); state.docked = !state.docked; apply(); save();
    });
    document.addEventListener('mouseup', rememberFloatingPosition);
    new MutationObserver(() => {
        const collapsed = panel.classList.contains('collapsed');
        if (state.collapsed === collapsed) return;
        state.collapsed = collapsed; save();
        if (!collapsed) scrollLatest();
    }).observe(panel, { attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', () => {
        if (state.open && !state.docked) { restoreFloatingPosition(); rememberFloatingPosition(); }
    });
    window.addEventListener('pagehide', () => {
        rememberFloatingPosition();
        state.open = !panel.hidden;
        state.collapsed = panel.classList.contains('collapsed');
        save();
    });
    apply();
}
