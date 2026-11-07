import { Server } from './api.js';
import { characterCategory } from './characterFolders.js';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => (
    {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
const newEntry = name => ({ id: crypto.randomUUID(), name });
const RELATION_VALUES = ['Враги', 'Союзники', 'Нейтральные'];

export async function openWorldFactionSettings(lobbyId) {
    if (document.querySelector('.world-faction-modal')) return;
    let [config, characters] = await Promise.all([
        Server.getWorldFactions(lobbyId), Server.getLobbyCharacters(lobbyId),
    ]);
    if (document.querySelector('.world-faction-modal')) return;
    characters = characters.filter(character => characterCategory(character) !== 'mutants');
    let savedGroups = structuredClone(config.groups);
    let activeTab = 'groups';
    let relationGroupId = config.groups[0]?.id || '';
    let saveQueue = Promise.resolve();
    const assignmentQueues = new Map();

    const root = document.createElement('div');
    root.className = 'modal world-faction-modal';
    root.style.display = 'flex';
    root.style.zIndex = '1330';
    root.innerHTML = `<div class="modal-content faction-dialog">
        <div class="character-trade-heading faction-heading">
            <h3>Группировки мира</h3>
            <button type="button" class="btn btn-secondary faction-close" aria-label="Закрыть">×</button>
        </div>
        <nav class="faction-tabs" aria-label="Разделы группировок">
            <button type="button" class="faction-tab" data-tab="groups">Группировки и фракции</button>
            <button type="button" class="faction-tab" data-tab="relations">Отношения</button>
            <button type="button" class="faction-tab" data-tab="characters">Персонажи</button>
        </nav>
        <div class="faction-status" role="status" aria-live="polite"></div>
        <section class="faction-panel" data-panel="groups">
            <p>Фракция — отделение внутри группировки. Название сохраняется после ввода; отдельная кнопка внизу окна не нужна.</p>
            <button type="button" class="btn btn-secondary faction-copy-ready" hidden>Создать копию готового набора для изменений</button>
            <div class="faction-add-row"><input class="form-control faction-new-group" maxlength="100" placeholder="Название новой группировки" aria-label="Название новой группировки">
                <button type="button" class="btn btn-secondary faction-add-group">Добавить группировку</button></div>
            <div class="faction-groups"></div>
        </section>
        <section class="faction-panel" data-panel="relations" hidden>
            <p>Выберите группировку и укажите, как она относится к каждой другой. Обратное отношение задаётся отдельно. Это справочник отношений; само по себе значение не меняет правила боя.</p>
            <label class="faction-relation-source">Чьи отношения показывать<select class="form-control faction-source-group"></select></label>
            <div class="faction-relations"></div>
        </section>
        <section class="faction-panel" data-panel="characters" hidden>
            <p>Выберите группировку и при необходимости фракцию. Выбор сохраняется сразу.</p>
            <input class="form-control faction-character-search" type="search" placeholder="Найти персонажа или НПС" aria-label="Найти персонажа или НПС">
            <div class="faction-assignments"></div>
        </section>
    </div>`;
    document.body.append(root);
    root.querySelector('.faction-close').onclick = () => root.remove();
    const dialog = root.querySelector('.faction-dialog');
    const status = root.querySelector('.faction-status');
    const showStatus = (message, error = false) => {
        status.textContent = message;
        status.classList.toggle('is-error', error);
    };
    const activateTab = tab => {
        activeTab = tab;
        root.querySelectorAll('.faction-tab').forEach(button => {
            button.setAttribute('aria-current', button.dataset.tab === tab ? 'page' : 'false');
        });
        root.querySelectorAll('.faction-panel').forEach(panel => {
            panel.hidden = panel.dataset.panel !== tab;
        });
    };
    root.querySelectorAll('.faction-tab').forEach(button => {
        button.onclick = () => activateTab(button.dataset.tab);
    });

    const editConfig = update => {
        saveQueue = saveQueue.then(async () => {
            const previous = structuredClone(config);
            try {
                update(config);
                showStatus('Сохраняю…');
                const saved = await Server.saveWorldFactions(lobbyId, config);
                config = { revision: saved.revision, source: saved.source,
                    groups: saved.groups, relations: saved.relations };
                savedGroups = structuredClone(config.groups);
                render();
                showStatus('Сохранено');
                return true;
            } catch (error) {
                config = previous;
                render();
                showStatus(error.message || 'Не удалось сохранить изменения', true);
                return false;
            }
        });
        return saveQueue;
    };

    const renderGroups = () => {
        const ready = config.source === 'ready';
        root.querySelector('.faction-copy-ready').hidden = !ready;
        root.querySelector('.faction-new-group').disabled = ready;
        root.querySelector('.faction-add-group').disabled = ready;
        const list = root.querySelector('.faction-groups');
        list.replaceChildren();
        config.groups.forEach(group => {
            const card = document.createElement('section');
            card.className = 'faction-group-card';
            card.innerHTML = `<div class="faction-group-heading">
                <input class="form-control faction-group-name" maxlength="100" aria-label="Название группировки" value="${escapeHtml(group.name)}" ${ready ? 'disabled' : ''}>
                <button type="button" class="btn btn-sm btn-danger faction-remove-group" ${ready ? 'disabled' : ''}>Убрать группировку</button></div>
                <div class="faction-members"></div>
                <div class="faction-add-row"><input class="form-control faction-new-member" maxlength="100" placeholder="Новая фракция внутри этой группировки" aria-label="Название новой фракции" ${ready ? 'disabled' : ''}>
                    <button type="button" class="btn btn-sm btn-secondary faction-add-member" ${ready ? 'disabled' : ''}>Добавить фракцию</button></div>`;
            const nameInput = card.querySelector('.faction-group-name');
            nameInput.onchange = () => {
                const name = nameInput.value.trim();
                if (name !== group.name) editConfig(state => {
                    state.groups.find(entry => entry.id === group.id).name = name;
                });
            };
            card.querySelector('.faction-remove-group').onclick = () => editConfig(state => {
                state.groups = state.groups.filter(entry => entry.id !== group.id);
                state.relations = state.relations.filter(entry => entry.from_id !== group.id && entry.to_id !== group.id);
            });
            const addMember = () => {
                const input = card.querySelector('.faction-new-member');
                const name = input.value.trim();
                if (!name) { input.focus(); return; }
                editConfig(state => state.groups.find(entry => entry.id === group.id).factions.push(newEntry(name)));
            };
            card.querySelector('.faction-add-member').onclick = addMember;
            card.querySelector('.faction-new-member').onkeydown = event => {
                if (event.key === 'Enter') { event.preventDefault(); addMember(); }
            };
            const members = card.querySelector('.faction-members');
            group.factions.forEach(faction => {
                const row = document.createElement('div');
                row.className = 'faction-member-row';
                row.innerHTML = `<input class="form-control" maxlength="100" aria-label="Название фракции" value="${escapeHtml(faction.name)}" ${ready ? 'disabled' : ''}>
                    <button type="button" class="btn btn-sm btn-danger" ${ready ? 'disabled' : ''}>Убрать</button>`;
                row.querySelector('input').onchange = event => {
                    const name = event.target.value.trim();
                    if (name !== faction.name) editConfig(state => {
                        state.groups.find(entry => entry.id === group.id).factions
                            .find(entry => entry.id === faction.id).name = name;
                    });
                };
                row.querySelector('button').onclick = () => editConfig(state => {
                    const target = state.groups.find(entry => entry.id === group.id);
                    target.factions = target.factions.filter(entry => entry.id !== faction.id);
                });
                members.append(row);
            });
            list.append(card);
        });
    };

    const renderRelations = () => {
        if (!config.groups.some(group => group.id === relationGroupId)) relationGroupId = config.groups[0]?.id || '';
        const picker = root.querySelector('.faction-source-group');
        picker.innerHTML = config.groups.map(group => `<option value="${escapeHtml(group.id)}">${escapeHtml(group.name)}</option>`).join('');
        picker.value = relationGroupId;
        picker.onchange = () => { relationGroupId = picker.value; renderRelations(); };
        const list = root.querySelector('.faction-relations');
        list.replaceChildren();
        if (config.groups.length < 2) {
            list.textContent = 'Добавьте хотя бы две группировки, чтобы указать отношения.';
            return;
        }
        config.groups.filter(group => group.id !== relationGroupId).forEach(other => {
            const sourceId = relationGroupId;
            const relation = config.relations.find(entry => entry.from_id === sourceId && entry.to_id === other.id);
            const custom = relation && !RELATION_VALUES.includes(relation.value);
            const row = document.createElement('div');
            row.className = 'faction-relation-row';
            row.innerHTML = `<span>${escapeHtml(other.name)}</span>
                <select class="form-control faction-relation-value" aria-label="Отношение к ${escapeHtml(other.name)}" ${config.source === 'ready' ? 'disabled' : ''}>
                    <option value="">Не указано</option>
                    ${RELATION_VALUES.map(value => `<option value="${value}">${value}</option>`).join('')}
                    <option value="__custom__">Свой вариант…</option>
                </select>
                <input class="form-control faction-relation-custom" maxlength="100" placeholder="Опишите отношение" aria-label="Свой вариант отношения к ${escapeHtml(other.name)}" ${custom ? '' : 'hidden'} ${config.source === 'ready' ? 'disabled' : ''} value="${custom ? escapeHtml(relation.value) : ''}">`;
            const select = row.querySelector('select');
            const input = row.querySelector('input');
            select.value = custom ? '__custom__' : relation?.value || '';
            const changeRelation = value => editConfig(state => {
                const index = state.relations.findIndex(entry => entry.from_id === sourceId && entry.to_id === other.id);
                if (!value) {
                    if (index !== -1) state.relations.splice(index, 1);
                } else if (index !== -1) state.relations[index].value = value;
                else state.relations.push({ from_id: sourceId, to_id: other.id, value });
            });
            select.onchange = () => {
                if (select.value === '__custom__') {
                    input.hidden = false;
                    input.focus();
                } else changeRelation(select.value);
            };
            input.onchange = () => {
                const value = input.value.trim();
                if (value) changeRelation(value);
                else { input.focus(); showStatus('Укажите отношение или выберите «Не указано»', true); }
            };
            list.append(row);
        });
    };

    const renderAssignments = () => {
        const search = root.querySelector('.faction-character-search');
        const list = root.querySelector('.faction-assignments');
        list.replaceChildren();
        characters.forEach((character, index) => {
            const current = character.data?.basic || {};
            const row = document.createElement('div');
            row.className = 'faction-assignment-row';
            row.dataset.search = character.name.toLocaleLowerCase();
            row.innerHTML = `<strong>${escapeHtml(character.name)}</strong>
                <select class="form-control faction-assignment-group" aria-label="Группировка: ${escapeHtml(character.name)}">
                    <option value="">Без группировки</option>
                    ${savedGroups.map(group => `<option value="${escapeHtml(group.id)}">${escapeHtml(group.name)}</option>`).join('')}
                </select>
                <select class="form-control faction-assignment-member" aria-label="Фракция: ${escapeHtml(character.name)}"></select>
                <small class="faction-assignment-status" role="status"></small>`;
            const groupSelect = row.querySelector('.faction-assignment-group');
            const memberSelect = row.querySelector('.faction-assignment-member');
            const rowStatus = row.querySelector('.faction-assignment-status');
            const renderMembers = selected => {
                const group = savedGroups.find(entry => entry.id === groupSelect.value);
                memberSelect.innerHTML = '<option value="">Без фракции</option>' + (group?.factions || []).map(faction =>
                    `<option value="${escapeHtml(faction.id)}">${escapeHtml(faction.name)}</option>`).join('');
                memberSelect.disabled = !group;
                memberSelect.value = selected || '';
            };
            groupSelect.value = current.group_id || '';
            renderMembers(current.faction_id);
            const assign = () => {
                const groupId = groupSelect.value || null;
                const factionId = memberSelect.value || null;
                rowStatus.textContent = 'Сохраняю…';
                const previous = assignmentQueues.get(character.id) || Promise.resolve();
                const next = previous.then(async () => {
                    try {
                        const result = await Server.assignWorldFaction(lobbyId, character.id, {
                            character_revision: characters[index].revision,
                            group_id: groupId, faction_id: factionId,
                        });
                        characters[index] = result.character;
                        rowStatus.textContent = 'Сохранено';
                    } catch (error) {
                        const actual = characters[index].data?.basic || {};
                        groupSelect.value = actual.group_id || '';
                        renderMembers(actual.faction_id);
                        rowStatus.textContent = error.message || 'Не удалось сохранить';
                        showStatus(`Принадлежность «${character.name}» не сохранена`, true);
                    }
                });
                assignmentQueues.set(character.id, next);
            };
            groupSelect.onchange = () => { renderMembers(''); assign(); };
            memberSelect.onchange = assign;
            list.append(row);
        });
        const filter = () => {
            const query = search.value.trim().toLocaleLowerCase();
            list.querySelectorAll('.faction-assignment-row').forEach(row => {
                row.hidden = Boolean(query) && !row.dataset.search.includes(query);
            });
        };
        search.oninput = filter;
        filter();
    };

    const render = () => {
        const scrollTop = dialog.scrollTop;
        renderGroups();
        renderRelations();
        renderAssignments();
        activateTab(activeTab);
        dialog.scrollTop = scrollTop;
    };
    root.querySelector('.faction-copy-ready').onclick = () => editConfig(state => { state.source = 'ready_customized'; });
    const addGroup = () => {
        const input = root.querySelector('.faction-new-group');
        const name = input.value.trim();
        if (!name) { input.focus(); return; }
        editConfig(state => state.groups.push({ ...newEntry(name), factions: [] }))
            .then(saved => { if (saved) input.value = ''; });
    };
    root.querySelector('.faction-add-group').onclick = addGroup;
    root.querySelector('.faction-new-group').onkeydown = event => {
        if (event.key === 'Enter') { event.preventDefault(); addGroup(); }
    };
    render();
}
