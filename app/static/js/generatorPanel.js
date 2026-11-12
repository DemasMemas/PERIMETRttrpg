import {Server} from './api.js';

export async function showGenerator({lobbyId, locationId, object, characters}) {
    document.getElementById('generator-modal')?.closeGenerator?.();
    const node = (tag, text) => {
        const element = document.createElement(tag);
        if (text) element.textContent = text;
        return element;
    };
    const modal = node('div');
    modal.id = 'generator-modal';
    modal.className = 'modal';
    modal.style.display = 'flex';
    const panel = node('div');
    panel.className = 'modal-content';
    panel.style.cssText = 'max-width:620px;max-height:85vh;overflow:auto';
    const select = node('select');
    select.className = 'form-control';
    select.setAttribute('aria-label', 'Кто работает с генератором');
    for (const character of characters) {
        const option = node('option', character.name || `Персонаж ${character.characterId}`);
        option.value = character.characterId;
        select.append(option);
    }
    const body = node('div');
    const socket = window.socket;
    let snapshot = null;
    let busy = false;
    let renderVersion = 0;
    const button = (label, action) => {
        const element = node('button', label);
        element.type = 'button';
        element.className = 'btn btn-sm btn-secondary';
        element.onclick = action;
        return element;
    };
    const close = () => {
        socket?.off?.('lobby_time_updated', refreshWhenIdle);
        socket?.off?.('location_object_updated', refreshOnUpdate);
        document.removeEventListener('keydown', onKey);
        modal.remove();
    };
    const onKey = event => { if (event.key === 'Escape') close(); };
    const minutes = seconds => (seconds / 60).toLocaleString('ru', {maximumFractionDigits: 1});
    const charge = value => value.toLocaleString('ru', {maximumFractionDigits: 1});

    function render(errorMessage = '') {
        body.replaceChildren();
        if (errorMessage) {
            const error = node('p', errorMessage);
            error.className = 'error-message';
            error.setAttribute('role', 'alert');
            body.append(error);
        }
        if (!snapshot) return;
        const status = node('p', `${snapshot.on ? 'Включён' : 'Выключен'} · топлива на ${minutes(snapshot.fuel_seconds)} мин`);
        const toggle = button(snapshot.on ? 'Выключить' : 'Включить', () => run('toggle'));
        toggle.disabled = !snapshot.on && !snapshot.fuel_seconds;
        body.append(status, toggle, node('h4', 'Заправка'));
        const fuel = snapshot.inputs.filter(entry => entry.kind === 'fuel');
        if (!fuel.length) body.append(node('p', 'В ёмкостях персонажа нет топлива.'));
        for (const entry of fuel) {
            const row = node('p', `${entry.vessel} · ${entry.quantity} л `);
            const amount = node('input');
            amount.type = 'number';
            amount.setAttribute('aria-label', 'Количество топлива в литрах');
            amount.min = .1;
            amount.max = entry.quantity;
            amount.step = .1;
            amount.value = entry.quantity;
            row.append(amount, button('Заправить', () => {
                if (amount.reportValidity() && Number(amount.value) > 0) {
                    void run('refuel', {path: entry.path, item_id: entry.id, liters: Number(amount.value)});
                }
            }));
            body.append(row);
        }
        body.append(node('h4', 'Аккумуляторы в генераторе'));
        if (!snapshot.batteries.length) body.append(node('p', 'Аккумуляторы не установлены.'));
        for (const cell of snapshot.batteries) {
            const row = node('p', `${cell.name} · ${charge(cell.charge)}% `);
            row.append(button('Забрать', () => run('unload', {item_id: cell.id})));
            body.append(row);
        }
        for (const cell of snapshot.inputs.filter(entry => entry.kind !== 'fuel')) {
            const row = node('p', `${cell.name} в инвентаре · ${charge(cell.charge)}% `);
            row.append(button('Поставить на зарядку', () => run('load', {path: cell.path, item_id: cell.id})));
            body.append(row);
        }
        body.append(node('h4', `Станки · ${snapshot.connected_stations}/${snapshot.connection_limit}`));
        if (!snapshot.stations.length) body.append(node('p', 'Рядом нет доступных станков.'));
        for (const station of snapshot.stations) {
            const status = station.powered ? ' · питание есть, −10 к СЛ' : station.linked ? ' · питание не подаётся' : '';
            const row = node('p', `${station.name}${status} `);
            const connection = button(station.linked ? 'Отключить' : 'Подключить', () => run(
                station.linked ? 'disconnect' : 'connect', {station_id: station.id, station_revision: station.revision}));
            connection.disabled = !station.linked && snapshot.connected_stations >= snapshot.connection_limit;
            row.append(connection);
            body.append(row);
        }
    }

    async function refresh(errorMessage = '') {
        const version = ++renderVersion;
        try {
            const fresh = await Server.getGenerator(lobbyId, locationId, object.id, Number(select.value));
            if (version !== renderVersion || !modal.isConnected) return;
            snapshot = fresh;
            render(errorMessage);
        } catch (error) {
            if (version !== renderVersion || !modal.isConnected) return;
            snapshot = null;
            render(error.message);
        }
    }

    async function run(operation, extra = {}) {
        if (busy || !snapshot) return;
        busy = true;
        ++renderVersion;
        select.disabled = true;
        body.querySelectorAll('button').forEach(element => { element.disabled = true; });
        const characterId = Number(select.value);
        try {
            await Server.waitForCharacterSave(characterId);
            snapshot = await Server.operateGenerator(lobbyId, locationId, object.id, {
                operation, character_id: characterId, revision: snapshot.revision,
                character_revision: snapshot.character_revision, ...extra,
            });
            if (modal.isConnected) render();
        } catch (error) {
            await refresh(error.message);
        } finally {
            select.disabled = false;
            busy = false;
        }
    }

    const refreshWhenIdle = () => { if (!busy && modal.isConnected) void refresh(); };
    const refreshOnUpdate = event => {
        const id = event?.object?.id;
        if (id === object.id || snapshot?.stations.some(station => station.id === id)) refreshWhenIdle();
    };
    modal.closeGenerator = close;
    select.onchange = () => { snapshot = null; render(); void refresh(); };
    panel.append(node('h3', object.name || 'Электрогенератор'), select, body, button('Закрыть', close));
    modal.append(panel);
    document.body.append(modal);
    document.addEventListener('keydown', onKey);
    socket?.on?.('lobby_time_updated', refreshWhenIdle);
    socket?.on?.('location_object_updated', refreshOnUpdate);
    await refresh();
}
