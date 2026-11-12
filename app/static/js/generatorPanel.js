import { Server } from './api.js';

const formatNumber = value => Number(value).toLocaleString('ru', { maximumFractionDigits: 1 });

function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
}

function button(label, action, style = 'secondary') {
    const element = node('button', label, 'btn btn-sm btn-' + style);
    element.type = 'button';
    element.onclick = action;
    return element;
}

function choices(items, label, title, placeholder) {
    const select = node('select', '', 'form-control');
    select.setAttribute('aria-label', label);
    if (placeholder) select.add(new Option(placeholder, ''));
    items.forEach((item, index) => select.add(new Option(title(item), String(index))));
    return select;
}

function meter(value, max, label) {
    const progress = node('progress', '', 'generator-meter');
    progress.max = max;
    progress.value = value;
    progress.setAttribute('aria-label', label);
    return progress;
}

function section(title, className) {
    const element = node('section', '', 'generator-section ' + className);
    element.append(node('h3', title));
    return element;
}

export async function showGenerator({ lobbyId, locationId, object, characters, actorCharacterId = null }) {
    document.getElementById('generator-modal')?.closeGenerator?.();
    const modal = node('dialog', '', 'generator-dialog');
    modal.id = 'generator-modal';
    modal.setAttribute('aria-labelledby', 'generator-title');
    const panel = node('div', '', 'modal-content generator-content');
    const heading = node('header', '', 'generator-heading');
    const headingText = node('div');
    const title = node('h2', object.name || 'Электрогенератор');
    title.id = 'generator-title';
    const status = node('p', 'Загрузка…', 'generator-status');
    status.setAttribute('role', 'status');
    headingText.append(title, status);
    const headerActions = node('div', '', 'generator-header-actions');
    const toggle = button('', () => run('toggle'));
    toggle.classList.add('modal-icon-close', 'generator-power');
    toggle.hidden = true;
    const closeButton = button('×', () => modal.close());
    closeButton.classList.add('modal-icon-close');
    closeButton.setAttribute('aria-label', 'Закрыть');
    headerActions.append(toggle, closeButton);
    heading.append(headingText, headerActions);

    const characterLabel = node('label', 'Персонаж', 'generator-character');
    const select = choices(characters, 'Кто работает с генератором',
        character => character.name || 'Персонаж ' + character.characterId);
    const selectedIndex = characters.findIndex(character => Number(character.characterId) === Number(actorCharacterId));
    select.selectedIndex = Math.max(0, selectedIndex);
    characterLabel.append(select);
    const body = node('div', '', 'generator-body');
    panel.append(heading, characterLabel, body);
    modal.append(panel);

    const socket = window.socket;
    let snapshot = null;
    let busy = false;
    let renderVersion = 0;
    let fuelDraft = null;
    const characterId = () => Number(characters[Number(select.value)]?.characterId);

    function renderFuel() {
        const fuel = section('Топливо', 'generator-fuel');
        fuel.append(node('p', 'Осталось на ' + formatNumber(snapshot.fuel_seconds / 60) + ' мин', 'generator-fuel-time'));
        const inputs = snapshot.inputs.filter(entry => entry.kind === 'fuel');
        if (!inputs.length) {
            fuel.append(node('p', 'У персонажа нет топлива в ёмкостях.', 'generator-hint'));
            return fuel;
        }
        const source = choices(inputs, 'Ёмкость с топливом',
            entry => entry.vessel + ' · ' + formatNumber(entry.quantity) + ' л');
        const matching = inputs.findIndex(entry => JSON.stringify(entry.path) === fuelDraft?.path && entry.id === fuelDraft?.id);
        source.selectedIndex = Math.max(0, matching);
        const amount = node('input', '', 'form-control');
        amount.type = 'number';
        amount.min = 0.1;
        amount.step = 0.1;
        amount.setAttribute('aria-label', 'Количество топлива в литрах');
        const setAmount = restore => {
            const entry = inputs[source.selectedIndex];
            amount.max = entry.quantity;
            amount.value = restore ? fuelDraft.value : entry.quantity;
        };
        const remember = () => {
            const entry = inputs[source.selectedIndex];
            fuelDraft = { id: entry.id, path: JSON.stringify(entry.path), value: amount.value };
        };
        setAmount(matching >= 0);
        source.onchange = () => { setAmount(false); remember(); };
        amount.oninput = remember;
        const liters = node('label', '', 'generator-liters');
        liters.append(amount, node('span', 'л'));
        const controls = node('div', '', 'generator-fuel-controls');
        controls.append(source, liters, button('Заправить', () => {
            if (!amount.reportValidity() || Number(amount.value) <= 0) return;
            const entry = inputs[source.selectedIndex];
            fuelDraft = null;
            void run('refuel', { path: entry.path, item_id: entry.id, liters: Number(amount.value) });
        }, 'primary'));
        fuel.append(controls);
        return fuel;
    }

    function renderBatteries() {
        const sectionBody = section('Аккумуляторы', 'generator-batteries');
        const slots = node('div', '', 'generator-slots');
        for (const cell of snapshot.batteries) {
            const card = node('div', '', 'generator-slot');
            const heading = node('div', '', 'generator-slot-heading');
            heading.append(node('strong', cell.name), node('span', formatNumber(cell.charge) + '%'));
            card.append(heading, meter(cell.charge, 100, 'Заряд ' + cell.name),
                button('Забрать', () => run('unload', { item_id: cell.id })));
            slots.append(card);
        }
        const inputs = snapshot.inputs.filter(entry => entry.kind !== 'fuel');
        const empty = node('div', '', 'generator-slot is-empty');
        empty.append(node('strong', 'Место для аккумулятора'));
        if (inputs.length) {
            const input = choices(inputs, 'Аккумулятор из инвентаря',
                cell => cell.name + ' · ' + formatNumber(cell.charge) + '%');
            empty.append(input, button('Поставить на зарядку', () => {
                const cell = inputs[input.selectedIndex];
                void run('load', { path: cell.path, item_id: cell.id });
            }));
        } else {
            empty.append(node('p', 'В инвентаре нет аккумуляторов.', 'generator-hint'));
        }
        slots.append(empty);
        sectionBody.append(slots);
        return sectionBody;
    }

    function renderStations() {
        const sectionBody = section('Станки', 'generator-stations');
        const total = snapshot.connected_stations;
        const limit = snapshot.connection_limit;
        const count = node('div', '', 'generator-slot-heading');
        count.append(node('span', 'Подключено'), node('strong', total + ' / ' + limit));
        sectionBody.append(count, meter(total, limit, 'Занятые подключения'));
        const slots = node('div', '', 'generator-slots');
        for (const station of snapshot.stations.filter(station => station.linked)) {
            const card = node('div', '', 'generator-slot');
            card.append(node('strong', station.name),
                node('p', station.powered ? 'Есть питание · −10 к СЛ' : 'Ожидает питания',
                    station.powered ? 'generator-powered' : 'generator-hint'),
                button('Отключить', () => run('disconnect', { station_id: station.id, station_revision: station.revision })));
            slots.append(card);
        }
        const nearby = snapshot.stations.filter(station => !station.linked);
        for (let index = total; index < limit; index++) {
            const card = node('div', '', 'generator-slot is-empty');
            card.append(node('strong', 'Свободное подключение'));
            if (nearby.length) {
                const choice = choices(nearby, 'Станок для подключения', station => station.name, 'Выберите станок');
                const connect = button('Подключить', () => {
                    const station = nearby[Number(choice.value)];
                    void run('connect', { station_id: station.id, station_revision: station.revision });
                });
                connect.disabled = true;
                choice.onchange = () => { connect.disabled = choice.value === ''; };
                card.append(choice, connect);
            }
            slots.append(card);
        }
        if (!nearby.length && total < limit) {
            sectionBody.append(node('p', 'Рядом нет свободных станков.', 'generator-hint'));
        }
        sectionBody.append(slots);
        return sectionBody;
    }

    function render(errorMessage = '') {
        body.replaceChildren();
        toggle.hidden = !snapshot;
        status.textContent = snapshot ? (snapshot.on ? 'Включён' : 'Выключен') : errorMessage ? '' : 'Загрузка…';
        status.classList.toggle('generator-powered', Boolean(snapshot?.on));
        if (errorMessage) {
            const error = node('p', errorMessage, 'error-message');
            error.setAttribute('role', 'alert');
            body.append(error);
        }
        if (!snapshot) return;
        const toggleLabel = snapshot.on ? 'Выключить генератор' : 'Включить генератор';
        toggle.setAttribute('aria-label', toggleLabel);
        toggle.setAttribute('aria-pressed', String(snapshot.on));
        toggle.title = !snapshot.on && !snapshot.fuel_seconds ? 'Сначала заправьте генератор' : toggleLabel;
        toggle.disabled = !snapshot.on && !snapshot.fuel_seconds;
        const columns = node('div', '', 'generator-columns');
        columns.append(renderBatteries(), renderStations());
        body.append(renderFuel(), columns);
    }

    async function refresh(errorMessage = '') {
        const version = ++renderVersion;
        try {
            const fresh = await Server.getGenerator(lobbyId, locationId, object.id, characterId());
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
        select.disabled = toggle.disabled = true;
        body.querySelectorAll('button, input, select').forEach(element => { element.disabled = true; });
        const actor = characterId();
        try {
            await Server.waitForCharacterSave(actor);
            snapshot = await Server.operateGenerator(lobbyId, locationId, object.id, {
                operation, character_id: actor, revision: snapshot.revision,
                character_revision: snapshot.character_revision, ...extra,
            });
            if (modal.isConnected) render();
        } catch (error) {
            await refresh(error.message);
        } finally {
            select.disabled = characters.length < 2;
            busy = false;
        }
    }

    const refreshWhenIdle = () => { if (!busy && modal.isConnected) void refresh(); };
    const refreshOnUpdate = event => {
        const id = event?.object?.id;
        if (id === object.id || snapshot?.stations.some(station => station.id === id)) refreshWhenIdle();
    };
    const cleanup = () => {
        socket?.off?.('lobby_time_updated', refreshWhenIdle);
        socket?.off?.('location_object_updated', refreshOnUpdate);
        modal.remove();
    };
    modal.closeGenerator = () => { modal.close(); cleanup(); };
    modal.addEventListener('close', cleanup, { once: true });
    modal.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'Escape') {
            event.preventDefault();
            modal.close();
        }
    });
    select.disabled = characters.length < 2;
    select.onchange = () => { fuelDraft = null; snapshot = null; render(); void refresh(); };
    document.body.append(modal);
    modal.showModal();
    socket?.on?.('lobby_time_updated', refreshWhenIdle);
    socket?.on?.('location_object_updated', refreshOnUpdate);
    await refresh();
}
