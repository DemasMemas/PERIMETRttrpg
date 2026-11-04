// static/js/ui.js
import { showNotification } from './utils.js';
import { Server } from './api.js';
import { loadLobbyCharacters } from './characters.js';
import { chatMessageKind, chatMessageIdentity } from './chatPanel.js';
import {
    setGmId,
    getUserColorHex,
    setUserColor,
    updateMyColor,
} from './colors.js';

export let lobbyParticipants = [];
export let gmId = null;
export let isGM = false;
export let onlineUserIds = new Set();
export let settingsVisible = false;
export let currentVisibilityCharacterId = null;
let currentLobbyId;

export function setLobbyData(participants, gm) {
    lobbyParticipants = (participants || [])
        .filter(participant => !participant.is_banned)
        .map(participant => ({
            ...participant,
            user_id: Number(participant.user_id),
        }));
    lobbyParticipants.forEach((participant) => {
        setUserColor(Number(participant.user_id), participant.color);
    });
    window.lobbyParticipants = lobbyParticipants;
    gmId = Number(gm);
    setGmId(gmId);
    isGM = gmId === Number(localStorage.getItem('user_id'));
}

export function setCurrentLobbyId(id) {
    currentLobbyId = id;
}

export function updateParticipantsList() {
    const onlineList = document.getElementById('online-participants');
    const offlineList = document.getElementById('offline-participants');
    if (!onlineList || !offlineList) return;

    onlineList.innerHTML = '';
    offlineList.innerHTML = '';

    lobbyParticipants.forEach(p => {
        const participantId = Number(p.user_id);
        const li = document.createElement('li');
        const colorHex = getUserColorHex(participantId);
        const currentUserId = Number(localStorage.getItem('user_id'));
        const canChangeColor = participantId === currentUserId
            && participantId !== Number(gmId);
        const identity = document.createElement('span');
        identity.className = 'participant-identity';
        const colorCircle = document.createElement(
            canChangeColor ? 'button' : 'span'
        );
        colorCircle.className = `participant-color${canChangeColor ? ' participant-color-editable' : ''}`;
        colorCircle.style.backgroundColor = colorHex;
        if (canChangeColor) {
            colorCircle.type = 'button';
            colorCircle.title = 'Выбрать свой цвет';
            colorCircle.setAttribute('aria-label', 'Выбрать свой цвет');
            colorCircle.addEventListener('pointerdown', (event) => {
                event.stopPropagation();
            });
            colorCircle.addEventListener('click', (event) => {
                event.stopPropagation();
                openOwnColorPicker(p);
            });
        }
        const name = document.createElement('span');
        name.textContent = `${p.username} ${participantId === Number(gmId) ? '(ГМ)' : ''}`;
        identity.append(colorCircle, name);
        li.appendChild(identity);

        if (isGM && participantId !== Number(gmId)) {
            const banBtn = document.createElement('button');
            banBtn.className = 'ban-btn';
            banBtn.innerHTML = '⛔';
            banBtn.onclick = (e) => {
                banUserHandler(participantId);
            };
            li.appendChild(banBtn);
        }

        if (onlineUserIds.has(participantId)) {
            onlineList.appendChild(li);
        } else {
            offlineList.appendChild(li);
        }
    });
}

function openOwnColorPicker(participant) {
    const input = document.createElement('input');
    input.type = 'color';
    input.value = getUserColorHex(Number(participant.user_id));
    input.style.position = 'fixed';
    input.style.left = '-10000px';
    document.body.appendChild(input);

    const removeInput = () => input.remove();
    input.addEventListener('change', async () => {
        try {
            const result = await updateMyColor(input.value);
            participant.color = result.color;
            setUserColor(Number(participant.user_id), result.color);
            updateParticipantsList();
            showNotification('Цвет обновлён', 'success');
        } catch (error) {
            showNotification(
                error.message || 'Не удалось изменить цвет',
                'error',
            );
        } finally {
            removeInput();
        }
    }, { once: true });
    input.addEventListener('cancel', removeInput, { once: true });
    input.click();
}

async function banUserHandler(userId) {
    if (!confirm('Заблокировать этого участника?')) return;
    try {
        await Server.banUser(currentLobbyId, userId);
        lobbyParticipants = lobbyParticipants.filter(p => p.user_id !== userId);
        window.lobbyParticipants = lobbyParticipants;
        onlineUserIds.delete(userId);
        updateParticipantsList();
        showNotification('Участник заблокирован');
    } catch (error) {
        showNotification(error.message);
    }
}

const deliveredChatMessages = new WeakMap();
export function addMessage(username, text, timestamp, id = null) {
    const chat = document.getElementById('chat');
    if (!chat) return;
    const identity = chatMessageIdentity(username, text, timestamp, id);
    let delivered = deliveredChatMessages.get(chat);
    if (!delivered) { delivered = new Set(); deliveredChatMessages.set(chat, delivered); }
    if (identity && delivered.has(identity)) return;
    if (identity) delivered.add(identity);
    const msgDiv = document.createElement('div');
    const kind = chatMessageKind(username, text);
    msgDiv.className = `message chat-${kind}${kind === 'event' ? ' system' : ''}`;
    msgDiv.dataset.messageKind = kind;
    msgDiv.style.whiteSpace = 'pre-wrap';
    const timeStr = timestamp ? new Date(timestamp).toLocaleTimeString() : '';
    const usernameSpan = document.createElement('span');
    usernameSpan.className = 'username';
    usernameSpan.textContent = `${username}:`;
    const timestampSpan = document.createElement('span');
    timestampSpan.className = 'timestamp';
    timestampSpan.textContent = timeStr;
    if (kind !== 'human') {
        const badge = document.createElement('span');
        badge.className = 'message-kind'; badge.textContent = kind === 'roll' ? 'Бросок' : 'Событие';
        msgDiv.appendChild(badge);
    }
    msgDiv.append(
        usernameSpan,
        document.createTextNode(` ${text} `),
        timestampSpan,
    );
    chat.appendChild(msgDiv);
    chat.scrollTop = chat.scrollHeight;
}

export function toggleParticipants() {
    const panel = document.getElementById('participants-panel');
    panel.classList.toggle('collapsed');
    const icon = panel.querySelector('.toggle-icon');
    if (icon) icon.textContent = panel.classList.contains('collapsed') ? '▶' : '▼';
}

export function toggleSettings() {
    settingsVisible ? closeSettings() : openSettings();
}

export function openSettings() {
    const panel = document.getElementById('settings-panel');
    panel.style.display = 'block';
    settingsVisible = true;
    const selected = panel.querySelector('.settings-tabs .tab-btn.active')
        || panel.querySelector('.settings-tabs .tab-btn');
    if (selected) showSettingsTab(selected.dataset.settingsTab || 'banned', selected);
}

export function closeSettings() {
    document.getElementById('settings-panel').style.display = 'none';
    settingsVisible = false;
}

export function showSettingsTab(tab, btn) {
    const panel = document.getElementById('settings-panel');
    if (!panel || !btn) return;
    panel.querySelectorAll('.settings-tabs .tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    panel.querySelectorAll('.settings-tab-content').forEach(el => el.style.display = 'none');

    if (tab === 'banned') {
        document.getElementById('settings-content').style.display = 'block';
        loadBannedList();
    } else if (tab === 'export') {
        document.getElementById('export-settings').style.display = 'block';
    } else if (tab === 'weather') {
        document.getElementById('weather-settings').style.display = 'block';
        if (window.loadWeatherSettings && window.weatherSettings) {
            window.loadWeatherSettings(window.weatherSettings);
        }
    }
}

async function loadBannedList() {
    try {
        const banned = await Server.getBannedList(currentLobbyId);
        const content = document.getElementById('settings-content');
        if (banned.length === 0) {
            content.innerHTML = '<p>Нет забаненных пользователей</p>';
            return;
        }
        let html = '';
        banned.forEach(user => {
            html += `
                <div class="banned-user">
                    <span>${user.username}</span>
                    <button class="unban-btn" onclick="window.unbanUserHandler(${user.user_id})">Разбанить</button>
                </div>
            `;
        });
        content.innerHTML = html;
    } catch (error) {
        document.getElementById('settings-content').innerHTML = `<p class="error">Ошибка: ${error.message}</p>`;
    }
}

export async function unbanUserHandler(userId) {
    if (!confirm('Разбанить этого пользователя?')) return;
    try {
        await Server.unbanUser(currentLobbyId, userId);
        showNotification('Пользователь разбанен');
        loadBannedList();
    } catch (error) {
        showNotification(error.message);
    }
}

export function openVisibilityModal(characterId, characterName, currentVisibleTo) {
    currentVisibilityCharacterId = characterId;
    document.getElementById('visibility-character-name').textContent = `Персонаж: ${characterName}`;
    const container = document.getElementById('visibility-participants-list');
    container.innerHTML = '';
    lobbyParticipants.forEach(p => {
        const div = document.createElement('div');
        div.className = 'visibility-participant';
        div.innerHTML = `
            <input type="checkbox" value="${p.user_id}" ${currentVisibleTo.includes(p.user_id) ? 'checked' : ''}>
            <label>${p.username}</label>
        `;
        container.appendChild(div);
    });
    document.getElementById('visibility-modal').style.display = 'flex';
}

export function closeVisibilityModal() {
    document.getElementById('visibility-modal').style.display = 'none';
    currentVisibilityCharacterId = null;
}

export async function saveVisibility() {
    if (!currentVisibilityCharacterId) return;
    const checkboxes = document.querySelectorAll('#visibility-participants-list input:checked');
    const visibleTo = Array.from(checkboxes).map(cb => parseInt(cb.value, 10));
    try {
        await Server.setCharacterVisibility(currentVisibilityCharacterId, visibleTo);
        showNotification('Видимость обновлена');
        closeVisibilityModal();
        loadLobbyCharacters();
    } catch (error) {
        showNotification(error.message);
    }
}
