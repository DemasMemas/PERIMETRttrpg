import { Server } from './api.js';
import { getErrorMessage } from './utils.js';

export function journalResources(entry) {
    return [`${Number(entry.action_points) || 0} ОД`, `${Number(entry.movement_points) || 0} ОП`,
        `${Number(entry.free_actions) || 0} СД`].join(' · ');
}

export function initActionJournal() {
    const panel = document.getElementById('panel-chat');
    const messages = panel?.querySelector('.chat-messages');
    if (!messages || panel.querySelector('.action-journal')) return;
    const list = document.createElement('div');
    list.className = 'action-journal';
    list.setAttribute('role', 'log');
    list.setAttribute('aria-label', 'Последние действия');
    messages.after(list);
    let generation = 0, timer;
    const refresh = async () => {
        if (panel.hidden || panel.dataset.chatView !== 'actions') return;
        const request = ++generation, lobbyId = window.currentLobbyId;
        try {
            const result = await Server.getActionJournal(lobbyId);
            if (request !== generation || lobbyId !== window.currentLobbyId) return;
            list.replaceChildren();
            const entries = [...new Map(result.entries.map(entry => [`${entry.character_id}:${entry.id}`, entry])).values()];
            if (!entries.length) list.textContent = 'Записанных действий пока нет';
            entries.forEach(entry => {
                const card = document.createElement('article');
                card.className = 'action-journal-entry';
                card.dataset.actionId = entry.id;
                card.dataset.actionStatus = entry.status;
                const heading = document.createElement('strong');
                heading.textContent = `${entry.character_name} · ${entry.label}`;
                const time=document.createElement('time');time.dateTime=entry.timestamp;
                time.textContent=new Date(entry.timestamp).toLocaleTimeString('ru');
                const status = document.createElement('div');
                status.textContent = entry.status_label;
                if (entry.remaining_action_points > 0 && entry.status === 'paying') status.textContent += ` · осталось ${entry.remaining_action_points} ОД`;
                const resources = document.createElement('small');
                resources.textContent = journalResources(entry);
                card.append(heading,time,status);
                if (entry.kind!=='roll') card.append(resources);
                if (entry.outcome || entry.reason) {
                    const outcome = document.createElement('div');
                    outcome.textContent = entry.reason ? getErrorMessage({error:entry.reason}) : entry.outcome;
                    card.append(outcome);
                }
                list.append(card);
            });
            list.scrollTop = list.scrollHeight;
        } catch (error) {
            if (request === generation) list.textContent = error.message || 'Не удалось загрузить действия';
        }
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(refresh, 30); };
    new MutationObserver(schedule).observe(panel, {attributes:true, attributeFilter:['data-chat-view','hidden']});
    window.addEventListener('action-journal-updated', schedule);
    refresh();
}
