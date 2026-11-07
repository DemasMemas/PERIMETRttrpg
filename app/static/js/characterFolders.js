const CATEGORIES = { players: 'Персонажи игроков', npcs: 'НПС', mutants: 'Мутанты' };
const isCategory = value => Object.prototype.hasOwnProperty.call(CATEGORIES, value);

export function characterCategory(character) {
    const data = character.data || {}, basic = data.basic || {};
    if ([data.is_mutant, data.isMutant, basic.is_mutant, basic.isMutant].some(value => value === true)
        || [data.character_type, data.characterType, data.species, basic.character_type, basic.characterType, basic.species]
            .some(value => String(value || '').toLocaleLowerCase().includes('мутант'))) return 'mutants';
    return data.is_npc === true || basic.is_npc === true || data.character_type === 'npc' || basic.character_type === 'npc'
        ? 'npcs' : 'players';
}

export function createCharacterFolderStore(lobbyId, userId, storage = localStorage) {
    const key = `character-folders:${lobbyId}:${userId}`;
    const read = () => {
        let state;
        try { state = JSON.parse(storage.getItem(key)); } catch { /* Ignore damaged local preferences. */ }
        const folders = Array.isArray(state?.folders) ? state.folders.filter(f => f && Number.isSafeInteger(f.id)
            && f.id > 0 && isCategory(f.category) && typeof f.name === 'string' && f.name.trim().length > 0) : [];
        const memberships = {};
        if (state?.memberships && typeof state.memberships === 'object') {
            for (const [id, folderId] of Object.entries(state.memberships)) {
                if (/^\d+$/.test(id) && folders.some(f => f.id === folderId)) memberships[id] = folderId;
            }
        }
        return { folders, memberships, nextId: Math.max(1, Number.isSafeInteger(state?.nextId) ? state.nextId : 1,
            ...folders.map(f => f.id + 1)) };
    };
    return { key, read,
        mutate(path, method, payload, characters) {
            const state = read();
            if (path === '/members' || path.startsWith('/members/')) {
                const ids = path === '/members' ? payload.character_ids : [Number(path.slice('/members/'.length))];
                if (!Array.isArray(ids) || !ids.length) throw new Error('Не выбран персонаж');
                const members = [...new Set(ids)].map(id => characters.find(c => c.id === id));
                if (members.some(character => !character)) throw new Error('Персонаж больше не доступен');
                const folder = state.folders.find(f => f.id === payload.folder_id);
                if (payload.folder_id !== null && (!folder || members.some(character => folder.category !== characterCategory(character)))) {
                    throw new Error('Папка не принадлежит этой вкладке');
                }
                for (const character of members) {
                    if (folder) state.memberships[character.id] = folder.id;
                    else delete state.memberships[character.id];
                }
            } else if (method === 'DELETE') {
                const id = Number(path.slice(1));
                state.folders = state.folders.filter(f => f.id !== id);
                for (const [characterId, folderId] of Object.entries(state.memberships)) {
                    if (folderId === id) delete state.memberships[characterId];
                }
            } else {
                const name = payload?.name?.trim();
                if (!name || name.length > 80) throw new Error('Название папки должно содержать от 1 до 80 символов');
                if (method === 'POST') {
                    if (!isCategory(payload.category)) throw new Error('Неизвестная вкладка');
                    state.folders.push({ id: state.nextId++, name, category: payload.category });
                } else {
                    const folder = state.folders.find(f => f.id === Number(path.slice(1)));
                    if (!folder) throw new Error('Папка больше не существует');
                    folder.name = name;
                }
            }
            storage.setItem(key, JSON.stringify(state));
            return state;
        },
    };
}

export function createCharacterFolderPanel(lobbyId, userId) {
    const key = `character-panel:${lobbyId}:${userId}`;
    let preferences = {};
    try { preferences = JSON.parse(localStorage.getItem(key)) || {}; } catch { /* Defaults for damaged preferences. */ }
    let active = isCategory(preferences.active) ? preferences.active : 'players';
    const expanded = new Set(Array.isArray(preferences.expanded) ? preferences.expanded : []);
    let draggedIds = null;
    let openTimer = null;
    const save = () => {
        try { localStorage.setItem(key, JSON.stringify({ active, expanded: [...expanded] })); } catch { /* Session remains usable. */ }
    };
    return ({ container, characters, organization, renderCharacters, renderCategoryOptions, mutate }) => {
        const clearDropFeedback = () => {
            clearTimeout(openTimer); openTimer = null;
            container.querySelectorAll('.character-folder-drop-active').forEach(zone => zone.classList.remove('character-folder-drop-active'));
        };
        container.ondragstart = event => {
            draggedIds = null;
            try {
                const payload = JSON.parse(event.dataTransfer.getData('text/plain'));
                const ids = payload.characterIds || [payload.characterId];
                if (!Array.isArray(ids) || !ids.length || !ids.every(id => characters.some(c => c.id === id))) return;
                draggedIds = [...new Set(ids)];
                event.dataTransfer.effectAllowed = 'copyMove';
            } catch { /* Other draggable content is not a character. */ }
        };
        container.ondragend = () => { draggedIds = null; clearDropFeedback(); };
        const dropZone = (zone, category, folderId = null) => {
            zone.dataset.characterFolderDrop = String(folderId ?? 'root');
            zone.title = folderId === null ? 'Перетащите сюда персонажа, чтобы оставить без папки' : 'Перетащите сюда персонажа или группу';
            const accepts = () => draggedIds?.length && draggedIds.every(id => characters.some(c => c.id === id && characterCategory(c) === category));
            zone.addEventListener('dragover', event => {
                if (!accepts()) return;
                event.preventDefault(); event.stopPropagation();
                event.dataTransfer.dropEffect = 'move';
                if (zone.classList.contains('character-folder-drop-active')) return;
                clearDropFeedback(); zone.classList.add('character-folder-drop-active');
                if (folderId !== null && !zone.open) openTimer = setTimeout(() => { zone.open = true; }, 600);
            });
            zone.addEventListener('dragleave', event => {
                if (!zone.contains(event.relatedTarget)) clearDropFeedback();
            });
            zone.addEventListener('drop', event => {
                if (!accepts()) return;
                event.preventDefault(); event.stopPropagation();
                const ids = draggedIds; draggedIds = null; clearDropFeedback();
                if (folderId !== null) { expanded.add(folderId); save(); }
                if (ids.every(id => (organization.memberships[id] ?? null) === folderId)) return;
                mutate('/members', 'PUT', { character_ids: ids, folder_id: folderId });
            });
        };
        const scrollTop = container.scrollTop;
        container.replaceChildren();
        const tabs = document.createElement('div');
        tabs.className = 'character-tabs'; tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Персонажи комнаты');
        container.appendChild(tabs);
        const panels = [];
        const buttons = [];
        const activate = category => {
            active = category; save();
            panels.forEach(panel => { panel.hidden = panel.dataset.category !== active; });
            buttons.forEach(button => {
                const selected = button.dataset.category === active;
                button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
            });
        };
        const button = (label, action, title = label) => {
            const item = document.createElement('button'); item.type = 'button'; item.textContent = label;
            item.className = 'btn btn-sm btn-secondary'; item.title = title;
            item.addEventListener('click', action); return item;
        };
        for (const [category, label] of Object.entries(CATEGORIES)) {
            const members = characters.filter(c => characterCategory(c) === category);
            const folders = organization.folders.filter(f => f.category === category);
            const folderIds = new Set(folders.map(f => f.id));
            const folderFor = c => folderIds.has(organization.memberships[c.id]) ? organization.memberships[c.id] : null;
            const tab = button(`${label} (${members.length})`, () => activate(category));
            tab.id = `character-tab-${category}`; tab.dataset.category = category;
            tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', `character-panel-${category}`);
            tabs.appendChild(tab); buttons.push(tab);
            const panel = document.createElement('section'); panel.id = `character-panel-${category}`;
            panel.dataset.category = category; panel.className = 'character-category';
            panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id);
            panels.push(panel); container.appendChild(panel);
            renderCategoryOptions?.(category, panel);
            const createFolder = button('+ Папка', () => {
                const name = prompt('Название папки:');
                if (name?.trim()) mutate('', 'POST', { name: name.trim(), category });
            });
            createFolder.classList.add('character-folder-create');
            panel.appendChild(createFolder);
            const root = document.createElement('div'); root.className = 'character-folder-root';
            dropZone(root, category);
            const heading = document.createElement('div'); heading.className = 'character-list-heading'; heading.textContent = 'Без папки';
            root.appendChild(heading); panel.appendChild(root);
            const appendMembers = (target, subset) => {
                if (!subset.length) {
                    const empty = document.createElement('p'); empty.className = 'character-folder-empty'; empty.textContent = 'Пока пусто';
                    target.appendChild(empty); return;
                }
                renderCharacters(subset, target, category);
            };
            appendMembers(root, members.filter(c => folderFor(c) === null));
            for (const folder of folders) {
                const details = document.createElement('details'); details.className = 'character-folder';
                dropZone(details, category, folder.id);
                details.dataset.folderId = folder.id; details.open = expanded.has(folder.id);
                details.addEventListener('toggle', () => {
                    if (!details.isConnected) return;
                    if (details.open) expanded.add(folder.id); else expanded.delete(folder.id);
                    save();
                });
                const subset = members.filter(c => folderFor(c) === folder.id);
                const summary = document.createElement('summary'); summary.textContent = `${folder.name} (${subset.length})`;
                details.appendChild(summary);
                {
                    const actions = document.createElement('div'); actions.className = 'character-folder-actions';
                    actions.appendChild(button('Переименовать', () => {
                        const name = prompt('Название папки:', folder.name);
                        if (name?.trim()) mutate(`/${folder.id}`, 'PATCH', { name: name.trim() });
                    }));
                    actions.appendChild(button('Удалить папку', () => {
                        if (confirm('Удалить папку? Персонажи останутся в комнате без папки.')) mutate(`/${folder.id}`, 'DELETE');
                    }));
                    details.appendChild(actions);
                }
                const contents = document.createElement('div'); contents.className = 'character-folder-contents';
                details.appendChild(contents); appendMembers(contents, subset); panel.appendChild(details);
            }
        }
        tabs.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            const index = buttons.indexOf(document.activeElement);
            if (index < 0) return;
            event.preventDefault(); event.stopPropagation();
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
                : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
            activate(buttons[next].dataset.category); buttons[next].focus();
        });
        activate(active); container.scrollTop = scrollTop;
    };
}
