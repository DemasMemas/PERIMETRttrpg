// static/js/socketHandlers.js
import { showNotification } from './utils.js';
import { loadLobbyCharacters, updateMutantHealthInList } from './characters.js';
import { loadLobbyInfo, loadAllChunks, updateDisplayedLobbyTime } from './lobbyData.js';
import { addMessage, updateParticipantsList, onlineUserIds, lobbyParticipants } from './ui.js';
import { updateTileInChunk, chunksMap, removeChunk } from './lobby3d.js';
import { applyWeather } from './weather.js';
import { setUserColor } from './colors.js';
import { receiveLocationObjectEvent, applyLocationLighting, refreshUserColor, getLocationViewLevel, setLocationViewLevel } from './locationScene.js';
import AppState from './ui_interactions.js';

let socket;
let currentLobbyId;
let networkMonitorTimer = null;
let pingInFlight = false;

function updateNetworkStatus(state, ping = null) {
    const root = document.getElementById('network-status');
    const label = document.getElementById('network-status-label');
    const value = document.getElementById('network-status-ping');
    if (!root || !label || !value) return;
    root.classList.remove('is-online', 'is-slow', 'is-offline', 'is-connecting');
    root.classList.add(`is-${state}`);
    if (state === 'offline') {
        label.textContent = navigator.onLine ? 'Нет связи' : 'Нет сети';
        value.textContent = '—';
        root.title = navigator.onLine ? 'Связь с игровым сервером потеряна. Выполняется переподключение.' : 'Устройство не подключено к интернету.';
        return;
    }
    if (state === 'connecting') {
        label.textContent = 'Связь';
        value.textContent = '...';
        root.title = 'Подключение к игровому серверу.';
        return;
    }
    const milliseconds = Math.max(0, Math.round(Number(ping) || 0));
    label.textContent = state === 'slow' ? 'Задержка' : 'Сеть';
    value.textContent = `${milliseconds} мс`;
    root.title = state === 'slow' ? `Высокая задержка до сервера: ${milliseconds} мс.` : `Соединение с сервером активно. Пинг: ${milliseconds} мс.`;
}

function measureServerPing() {
    if (!socket?.connected || pingInFlight) return;
    pingInFlight = true;
    const startedAt = performance.now();
    let completed = false;
    const timeout = window.setTimeout(() => {
        if (completed) return;
        completed = true;
        pingInFlight = false;
        updateNetworkStatus('offline');
    }, 5000);
    socket.emit('network_ping', {}, () => {
        if (completed) return;
        completed = true;
        window.clearTimeout(timeout);
        pingInFlight = false;
        const ping = performance.now() - startedAt;
        updateNetworkStatus(ping >= 250 ? 'slow' : 'online', ping);
    });
}

function startNetworkMonitor() {
    if (networkMonitorTimer) window.clearInterval(networkMonitorTimer);
    measureServerPing();
    networkMonitorTimer = window.setInterval(measureServerPing, 5000);
}

export function initSocket(lobbyId, token) {
    currentLobbyId = lobbyId;
    let authenticatedOnce = false;
    socket = io();
    const originalEmit = socket.emit.bind(socket);
    socket.emit = (event, ...args) => {
        if (args[0] && typeof args[0] === 'object'
            && Object.prototype.hasOwnProperty.call(args[0], 'token')) {
            const currentToken = window.authSession?.getToken();
            if (currentToken) args[0] = { ...args[0], token: currentToken };
        }
        return originalEmit(event, ...args);
    };

    socket.on('connect', () => {
        updateNetworkStatus('connecting');
        socket.emit('authenticate', { token, lobby_id: lobbyId });
        startNetworkMonitor();
    });
    socket.on('disconnect', () => updateNetworkStatus('offline'));
    socket.on('connect_error', () => updateNetworkStatus('offline'));
    socket.io.on('reconnect_attempt', () => updateNetworkStatus('connecting'));
    socket.io.on('reconnect_failed', () => updateNetworkStatus('offline'));

    socket.on('authenticated', async (data) => {
        const connectionId = socket.id;
        const reconnecting = authenticatedOnce;
        authenticatedOnce = true;
        showNotification(`Вы вошли как ${data.username}`, 'system', 'bottom-left');
        window.isGM = data.is_gm === true;
        AppState.setIsGM(window.isGM);
        const myId = parseInt(localStorage.getItem('user_id'));
        onlineUserIds.add(myId);
        loadLobbyInfo();
        loadLobbyCharacters();
        if (reconnecting) {
            await loadAllChunks();
            if (!socket.connected || socket.id !== connectionId) return;
            window.clearDirectWorldObjectSelection?.();
            for (const key of [...chunksMap.keys()]) removeChunk(...key.split(',').map(Number));
        }
        loadAllChunks();
        if (window.isLocationActive && window.currentLocationId) {
            const locationId = window.currentLocationId;
            const characterId = window.currentLocationCharacterId || null;
            if (reconnecting) {
                // Room membership alone cannot recover edits missed while offline.
                const level = getLocationViewLevel();
                const position = window.locationControls?.object.position.clone();
                const target = window.locationControls?.target.clone();
                await window.enterLocation(locationId);
                if (!socket.connected || socket.id !== connectionId
                    || !window.isLocationActive || window.currentLocationId !== locationId) return;
                setLocationViewLevel(level);
                if (position && target && window.locationControls) {
                    window.locationControls.object.position.copy(position);
                    window.locationControls.target.copy(target);
                    window.locationControls.update();
                }
                window.currentLocationCharacterId = characterId;
            }
            socket.emit('join_location', {
                token,
                location_id: locationId,
                character_id: characterId,
            });
            if (window.isGM) {
                import('./locationScene.js').then(module => module.loadCombatStartRequests());
            }
        }
    });

    socket.on('new_message', (data) => {
        addMessage(data.username, data.message, data.timestamp, data.id);
        if (/^(?:\/roll(?:\s|$)|🎲)/i.test(data.message) || data.username==='System (Roll)') window.dispatchEvent(new CustomEvent('action-journal-updated'));
        if (data.username.startsWith('System')) {
            showNotification(data.message, 'system', 'bottom-left');
        }
    });

    socket.on('error', (data) => {
        if (data.message === 'Invalid token') {
            showNotification('Сессия истекла, войдите заново', 'error');
            setTimeout(() => { window.location.href = '/'; }, 2000);
        } else {
            showNotification('Ошибка: ' + data.message, 'error');
        }
    });

    socket.on('action_journal_updated', data => {
        if (Number(data.lobby_id) === Number(window.currentLobbyId)) window.dispatchEvent(new CustomEvent('action-journal-updated'));
    });
    socket.on('combat_state_updated', () => window.dispatchEvent(new CustomEvent('action-journal-updated')));
    socket.on('chat_history', (messages) => {
        messages.forEach(msg => {
            addMessage(msg.username, msg.message, msg.timestamp, msg.id);
        });
        requestAnimationFrame(() => {
            const chat = document.getElementById('chat');
            if (chat) chat.scrollTop = chat.scrollHeight;
        });
    });

    socket.on('online_users', (userIds) => {
        onlineUserIds.clear();
        userIds.forEach(id => onlineUserIds.add(Number(id)));
        updateParticipantsList();
    });

    socket.on('user_joined', (data) => {
        showNotification(`${data.username} присоединился к комнате`, 'system', 'bottom-left');
        const userId = Number(data.user_id);
        if (!lobbyParticipants.some(p => Number(p.user_id) === userId)) {
            lobbyParticipants.push({
                user_id: userId,
                username: data.username,
                color: data.color,
            });
        }
        onlineUserIds.add(userId);
        updateParticipantsList();
        loadLobbyCharacters();
    });

    socket.on('user_left', (data) => {
        const participant = lobbyParticipants.find(item => Number(item.user_id) === Number(data.user_id));
        const username = data.username || participant?.username;
        if (username) showNotification(`${username} покинул комнату`, 'system', 'bottom-left');
        onlineUserIds.delete(Number(data.user_id));
        updateParticipantsList();
        loadLobbyCharacters();
    });

    socket.on('participant_banned', (data) => {
        onlineUserIds.delete(Number(data.user_id));
        loadLobbyInfo();
        loadLobbyCharacters();
    });

    socket.on('user_color_updated', (data) => {
        const userId = Number(data.user_id);
        setUserColor(userId, data.color);
        const participant = lobbyParticipants.find(
            item => Number(item.user_id) === userId
        );
        if (participant) participant.color = data.color;
        updateParticipantsList();
        refreshUserColor(userId);
    });

    socket.on('lobby_time_updated', (data) => {
        updateDisplayedLobbyTime(data);
        applyLocationLighting();
    });

    socket.on('location_updated', data => {
        if (Number(data?.location_id) === Number(window.currentLocationId) && data?.updates?.lighting) {
            receiveLocationObjectEvent('applyLocationLighting', data.updates.lighting, true, 'socket');
        }
    });

    socket.on('kicked', () => {
        showNotification('Вы были заблокированы в этой комнате', 'error', 'top-right');
        window.location.href = '/';
    });

    socket.on('character_created', () => loadLobbyCharacters());
    socket.on('character_deleted', () => loadLobbyCharacters());
    socket.on('character_updated', () => loadLobbyCharacters());
    socket.on('mutant_health_updated', data => {
        if (Number(data?.lobby_id) !== Number(currentLobbyId)) return;
        updateMutantHealthInList(data.character_id, data.health);
    });

    socket.on('tile_updated', (data) => {
        if (window.deferWorldEditorUpdate?.(data)) return;
        updateTileInChunk(data.chunk_x, data.chunk_y, data.tile_x, data.tile_y, data.state?{__editorState:data.state}:data.updates);
    });

    socket.on('tiles_updated', (updates) => {
        updates.forEach(item => {
            if (window.deferWorldEditorUpdate?.(item)) return;
            updateTileInChunk(item.chunk_x, item.chunk_y, item.tile_x, item.tile_y, item.state?{__editorState:item.state}:item.updates);
        });
    });

    socket.on('weather_updated', (settings) => {
        applyWeather(settings);
        window.weatherSettings = settings;
        applyLocationLighting();
    });

    return socket;
}

export function sendMessage(message) {
    if (!socket) return;
    socket.emit('send_message', { token: localStorage.getItem('access_token'), lobby_id: currentLobbyId, message });
}

export function getSocket() { return socket; }
