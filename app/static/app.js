// static/app.js
const API_BASE = '';

let myLobbiesOffset = 0;
const MY_LOBBIES_LIMIT = 5;
let hasMoreMyLobbies = true;

let joinedLobbiesOffset = 0;
const JOINED_LOBBIES_LIMIT = 5;
let hasMoreJoinedLobbies = true;

function getErrorMessage(data) {
    if (!data) return 'Неизвестная ошибка';
    if (typeof data === 'string') return data;
    if (data.error) {
        if (typeof data.error === 'string') return data.error;
        if (data.error.message) {
            if (data.error.details) {
                let detailsStr = '';
                for (let field in data.error.details) {
                    detailsStr += `${field}: ${data.error.details[field].join(', ')}; `;
                }
                return detailsStr ? `${data.error.message}: ${detailsStr}` : data.error.message;
            }
            return data.error.message;
        }
        if (data.error.details) {
            let detailsStr = '';
            for (let field in data.error.details) {
                detailsStr += `${field}: ${data.error.details[field].join(', ')}; `;
            }
            return detailsStr || 'Ошибка валидации';
        }
    }
    return 'Неизвестная ошибка';
}

function showMessage(text, isError = true) {
    const msgDiv = document.getElementById('auth-message');
    msgDiv.textContent = text;
    msgDiv.className = isError ? 'error' : 'success';
}

async function register() {
    const username = document.getElementById('reg-username').value;
    const email = document.getElementById('reg-email').value;
    const password = document.getElementById('reg-password').value;

    if (!username || !email || !password) {
        showMessage('Заполните все поля');
        return;
    }

    try {
        const response = await fetch('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, email, password })
        });
        const data = await response.json();
        if (response.ok) {
            showMessage('Регистрация успешна! Теперь войдите.', false);
        } else {
            showMessage(getErrorMessage(data));
        }
    } catch (error) {
        showMessage('Ошибка сети');
    }
}

async function login() {
    const username = document.getElementById('login-username').value;
    const password = document.getElementById('login-password').value;

    if (!username || !password) {
        showMessage('Введите имя и пароль');
        return;
    }

    try {
        const response = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await response.json();
        if (response.ok) {
            localStorage.setItem('access_token', data.access_token);
            localStorage.setItem('user_id', data.user_id);
            localStorage.setItem('username', username);
            showMessage('Вход выполнен', false);
            loadApp();
        } else {
            showMessage(getErrorMessage(data));
        }
    } catch (error) {
        showMessage('Ошибка сети');
    }
}

function logout() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('username');
    document.getElementById('auth-section').classList.remove('hidden');
    document.getElementById('app-section').classList.add('hidden');
    showMessage('');
}

async function loadApp() {
    const token = localStorage.getItem('access_token');
    if (!token || window.authSession?.isExpired()) {
        logout();
        return;
    }

    document.getElementById('auth-section').classList.add('hidden');
    document.getElementById('app-section').classList.remove('hidden');
    document.getElementById('username').textContent = localStorage.getItem('username') || '';

    await loadMyLobbies(true);
    await loadJoinedLobbies(true);
}

function showNotification(message, type = 'error') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        background: ${type === 'error' ? '#f44336' : '#4caf50'};
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        animation: slideIn 0.3s ease;
    `;
    let stack = document.getElementById('notification-stack-top-right');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'notification-stack-top-right';
        stack.className = 'notification-stack top-right';
        document.body.appendChild(stack);
    }
    stack.appendChild(notification);
    setTimeout(() => {
        notification.classList.add('is-leaving');
        setTimeout(() => {
            notification.remove();
            if (!stack.children.length) stack.remove();
        }, 200);
    }, 4800);
}

async function createLobby() {
    const token = localStorage.getItem('access_token');
    if (!token) return;

    const name = document.getElementById('lobby-name').value;
    if (!name) {
        showNotification('Введите название комнаты');
        return;
    }

    const createBtn = document.querySelector('button[onclick="createLobby()"]');
    const loadingDiv = document.getElementById('loading-indicator');
    if (createBtn) createBtn.disabled = true;
    if (loadingDiv) loadingDiv.style.display = 'block';

    try {
        const mapType = document.getElementById('map-type').value;

        if (mapType === 'imported') {
            const fileInput = document.getElementById('map-file');
            if (!fileInput.files || fileInput.files.length === 0) {
                showNotification('Выберите файл карты');
                return;
            }
            const file = fileInput.files[0];
            const reader = new FileReader();
            const jsonStr = await new Promise((resolve, reject) => {
                reader.onload = (e) => {
                    const arrayBuffer = e.target.result;
                    if (file.name.endsWith('.gz')) {
                        if (typeof pako === 'undefined') {
                            reject('Библиотека pako не загружена');
                            return;
                        }
                        try {
                            const uint8Array = new Uint8Array(arrayBuffer);
                            const decompressed = pako.ungzip(uint8Array, { to: 'string' });
                            resolve(decompressed);
                        } catch (err) {
                            reject('Ошибка распаковки gzip');
                        }
                    } else {
                        const text = new TextDecoder('utf-8').decode(arrayBuffer);
                        resolve(text);
                    }
                };
                reader.onerror = () => reject('Ошибка чтения файла');
                reader.readAsArrayBuffer(file);
            });

            if (!jsonStr || jsonStr.trim().length === 0) throw new Error('Файл пуст');
            if (!jsonStr.trim().startsWith('{')) throw new Error('Файл не является JSON');

            const formData = new FormData();
            formData.append('name', name);
            formData.append('map_type', mapType);
            formData.append('faction_source', document.getElementById('faction-source').value);
            const blob = new Blob([jsonStr], { type: 'application/json' });
            const fileName = file.name.replace(/\.gz$/, '') + '.json';
            formData.append('map_file', blob, fileName);

            const response = await fetch('/lobbies/', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: formData
            });

            if (response.ok) {
                const lobby = await response.json();
                window.location.href = `/lobbies/${lobby.id}/page`;
            } else {
                const err = await response.json();
                throw new Error(getErrorMessage(err) || 'Ошибка создания комнаты');
            }
        } else {
            let chunksWidth = 16, chunksHeight = 16;
            if (mapType !== 'predefined') {
                chunksWidth = parseInt(document.getElementById('chunks-width').value) || 16;
                chunksHeight = parseInt(document.getElementById('chunks-height').value) || 16;
                chunksWidth = Math.min(32, Math.max(1, chunksWidth));
                chunksHeight = Math.min(32, Math.max(1, chunksHeight));
            }

            const body = { name, map_type: mapType, chunks_width: chunksWidth, chunks_height: chunksHeight,
                faction_source: document.getElementById('faction-source').value };
            const response = await fetch('/lobbies/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify(body)
            });

            if (response.ok) {
                const lobby = await response.json();
                window.location.href = `/lobbies/${lobby.id}/page`;
            } else {
                const err = await response.json();
                throw new Error(getErrorMessage(err) || 'Ошибка создания комнаты');
            }
        }
    } catch (error) {
        showNotification(error.message || 'Ошибка создания комнаты');
        console.error(error);
    } finally {
        if (createBtn) createBtn.disabled = false;
        if (loadingDiv) loadingDiv.style.display = 'none';
    }
}

async function joinByCode() {
    const token = localStorage.getItem('access_token');
    if (!token) return;

    const code = document.getElementById('invite-code').value.trim().toUpperCase();
    if (!code) {
        showNotification('Введите код');
        return;
    }

    try {
        const response = await fetch('/lobbies/join_by_code', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ code })
        });
        if (response.ok) {
            const data = await response.json();
            window.location.href = `/lobbies/${data.lobby_id}/page`;
        } else {
            const err = await response.json();
            showNotification(getErrorMessage(err));
        }
    } catch (error) {
        showNotification('Ошибка сети');
    }
}

async function loadMyLobbies(reset = false) {
    const token = localStorage.getItem('access_token');
    if (!token) return;

    if (reset) {
        myLobbiesOffset = 0;
        hasMoreMyLobbies = true;
        document.getElementById('my-lobbies-list').innerHTML = '';
    }

    if (!hasMoreMyLobbies) return;

    try {
        const url = `/lobbies/my?limit=${MY_LOBBIES_LIMIT}&offset=${myLobbiesOffset}`;
        const response = await fetch(url, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.status === 401 || response.status === 422) {
            logout();
            return;
        }
        const lobbies = await response.json();
        const container = document.getElementById('my-lobbies-list');

        if (lobbies.length === 0) {
            hasMoreMyLobbies = false;
            if (myLobbiesOffset === 0) {
                container.innerHTML = '<p>У вас пока нет созданных комнат</p>';
            }
        } else {
            lobbies.forEach(lobby => {
                const div = document.createElement('div');
                div.className = 'lobby-item';
                div.innerHTML = `
                    <strong>${lobby.name}</strong> (код: ${lobby.invite_code})
                    <div>
                        <button onclick="joinLobby(${lobby.id})">Войти</button>
                        <button class="delete-btn" onclick="deleteLobby(${lobby.id})">🗑️ Удалить</button>
                    </div>
                `;
                container.appendChild(div);
            });
            myLobbiesOffset += lobbies.length;
            if (lobbies.length < MY_LOBBIES_LIMIT) {
                hasMoreMyLobbies = false;
            }
        }
    } catch (error) {
        console.error('Ошибка загрузки моих комнат', error);
    }
}

window.loadMoreMyLobbies = function() {
    loadMyLobbies(false);
};

async function deleteLobby(lobbyId) {
    if (!confirm('Вы уверены, что хотите удалить комнату? Это действие необратимо.')) return;

    const token = localStorage.getItem('access_token');
    if (!token) return;

    try {
        const response = await fetch(`/lobbies/${lobbyId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
            showNotification('Комната удалена', 'success');
            loadMyLobbies(true);
        } else {
            const err = await response.json();
            showNotification(getErrorMessage(err));
        }
    } catch (error) {
        showNotification('Ошибка сети');
    }
}

async function joinLobby(lobbyId) {
    const token = localStorage.getItem('access_token');
    if (!token) return;

    try {
        const response = await fetch(`/lobbies/${lobbyId}/join`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
            window.location.href = `/lobbies/${lobbyId}/page`;
        } else {
            const err = await response.json();
            showNotification(getErrorMessage(err));
        }
    } catch (error) {
        showNotification('Ошибка сети');
    }
}

async function loadJoinedLobbies(reset = false) {
    const token = localStorage.getItem('access_token');
    if (!token) return;

    if (reset) {
        joinedLobbiesOffset = 0;
        hasMoreJoinedLobbies = true;
        document.getElementById('joined-lobbies-list').innerHTML = '';
    }

    if (!hasMoreJoinedLobbies) return;

    try {
        const url = `/lobbies/joined?limit=${JOINED_LOBBIES_LIMIT}&offset=${joinedLobbiesOffset}`;
        const response = await fetch(url, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.status === 401 || response.status === 422) {
            logout();
            return;
        }
        const lobbies = await response.json();
        const container = document.getElementById('joined-lobbies-list');

        if (lobbies.length === 0) {
            hasMoreJoinedLobbies = false;
            if (joinedLobbiesOffset === 0) {
                container.innerHTML = '<p>Вы пока не присоединились ни к одной комнате</p>';
            }
        } else {
            lobbies.forEach(lobby => {
                const div = document.createElement('div');
                div.className = 'lobby-item';
                div.innerHTML = `
                    <strong>${lobby.name}</strong> (код: ${lobby.invite_code})
                    <div>
                        <button onclick="joinLobby(${lobby.id})">Войти</button>
                        <button class="delete-btn" onclick="leaveJoinedLobby(${lobby.id})">🚪 Покинуть</button>
                    </div>
                `;
                container.appendChild(div);
            });
            joinedLobbiesOffset += lobbies.length;
            if (lobbies.length < JOINED_LOBBIES_LIMIT) {
                hasMoreJoinedLobbies = false;
            }
        }
    } catch (error) {
        console.error('Ошибка загрузки присоединённых комнат', error);
    }
}

window.loadMoreJoinedLobbies = function() {
    loadJoinedLobbies(false);
};

async function leaveJoinedLobby(lobbyId) {
    if (!confirm('Покинуть комнату?')) return;

    const token = localStorage.getItem('access_token');
    if (!token) return;

    try {
        const response = await fetch(`/lobbies/${lobbyId}/leave`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
            showNotification('Вы покинули комнату', 'success');
            loadJoinedLobbies(true);
        } else {
            const err = await response.json();
            showNotification(getErrorMessage(err));
        }
    } catch (error) {
        showNotification('Ошибка сети');
    }
}
window.leaveJoinedLobby = leaveJoinedLobby;

window.onload = () => {
    if (localStorage.getItem('access_token')) {
        loadApp();
    }
};
