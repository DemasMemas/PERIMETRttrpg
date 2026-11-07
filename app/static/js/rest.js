import { Server } from './api.js';
import { showNotification } from './utils.js';

let submitting = false;

function modal() {
    return document.getElementById('lobby-rest-modal');
}

function setAllCharacters(checked) {
    modal()?.querySelectorAll('input[name="lobby-rest-character"]:not(:disabled)')
        .forEach(input => { input.checked = checked; });
}

function activeCharacterIds() {
    return [...(modal()?.querySelectorAll('input[name="lobby-time-active"]:checked') || [])]
        .map(input => Number(input.value))
        .filter(Number.isFinite);
}

export async function openLobbyRestModal() {
    if (!window.isGM) return;
    const dialog = modal();
    const list = document.getElementById('lobby-rest-character-list');
    if (!dialog || !list) return;
    dialog.style.display = 'flex';
    list.innerHTML = '<div>Загрузка персонажей...</div>';
    try {
        const characters = await Server.getLobbyCharacters(window.currentLobbyId);
        list.innerHTML = '';
        if (!characters.length) {
            list.innerHTML = '<div>В комнате нет персонажей.</div>';
            return;
        }
        characters.forEach(character => {
            const row = document.createElement('div');
            row.className = 'lobby-rest-character';
            const participantLabel = document.createElement('label');
            participantLabel.className = 'lobby-rest-participant';
            const participant = document.createElement('input');
            participant.type = 'checkbox';
            participant.name = 'lobby-rest-character';
            participant.value = character.id;
            participant.checked = character.time_active !== false;
            participant.disabled = character.time_active === false;
            const name = document.createElement('span');
            name.textContent = character.name || `Персонаж #${character.id}`;
            const sleepMode = character.data?.health?.combatMeta?.sleepMode || 'none';
            const sleepPlace = document.createElement('small');
            sleepPlace.textContent = ` · ${({none:'без удобств',bed:'кровать',tent:'палатка',sleeping_bag:'спальный мешок'})[sleepMode] || 'без удобств'}`;
            participantLabel.append(participant, name, sleepPlace);

            const activeLabel = document.createElement('label');
            activeLabel.className = 'lobby-time-active-toggle';
            const active = document.createElement('input');
            active.type = 'checkbox';
            active.name = 'lobby-time-active';
            active.value = character.id;
            active.checked = character.time_active !== false;
            active.addEventListener('change', () => {
                participant.disabled = !active.checked;
                participant.checked = active.checked;
                row.classList.toggle('time-inactive', !active.checked);
            });
            activeLabel.append(active, document.createTextNode('Активен во времени'));
            row.classList.toggle('time-inactive', !active.checked);
            row.append(participantLabel, activeLabel);
            list.appendChild(row);
        });
    } catch (error) {
        list.textContent = error.message || 'Не удалось загрузить персонажей';
    }
}

export function closeLobbyRestModal() {
    const dialog = modal();
    if (dialog && !submitting) dialog.style.display = 'none';
}

export async function saveLobbyTimeActivity(showSuccess = true) {
    if (submitting || !window.isGM) return false;
    try {
        await Server.updateTimeActiveCharacters(
            window.currentLobbyId,
            activeCharacterIds(),
        );
        if (showSuccess) showNotification('Активные листы сохранены', 'success');
        return true;
    } catch (error) {
        showNotification(error.message || 'Не удалось сохранить активных персонажей');
        return false;
    }
}

export async function startLobbyRest(type) {
    if (submitting || !window.isGM) return;
    const selected = [...(modal()?.querySelectorAll('input[name="lobby-rest-character"]:checked') || [])]
        .map(input => Number(input.value))
        .filter(Number.isFinite);
    if (!selected.length) {
        showNotification('Выберите хотя бы одного персонажа');
        return;
    }
    submitting = true;
    try {
        await Server.updateTimeActiveCharacters(
            window.currentLobbyId,
            activeCharacterIds(),
        );
        const result = await Server.startLobbyRest(window.currentLobbyId, type, selected);
        submitting = false;
        closeLobbyRestModal();
        const names = (result.characters || []).map(character => character.name).join(', ');
        showNotification(
            `${type === 'sleep' ? 'Сон завершён' : 'Отдых завершён'}: ${names}`,
            'success',
        );
    } catch (error) {
        submitting = false;
        showNotification(error.message || 'Не удалось завершить отдых');
    }
}

document.addEventListener('click', event => {
    if (event.target === modal()) closeLobbyRestModal();
});

document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && modal()?.style.display !== 'none') {
        closeLobbyRestModal();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('lobby-rest-select-all')
        ?.addEventListener('click', () => setAllCharacters(true));
    document.getElementById('lobby-rest-clear')
        ?.addEventListener('click', () => setAllCharacters(false));
});
