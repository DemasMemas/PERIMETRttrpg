const number = new Intl.NumberFormat('ru-RU');

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

export function modificationTitle(rule) {
    return rule.name || rule.description;
}

export function renderModificationMap(container, {target, rules, selectedId, onSelect, unavailableReason}) {
    const installed = (target.item.modifications || []).filter(record => record.ruleId);
    const branches = new Map();
    for (const rule of rules) {
        const branch = rule.kind === 'pda' ? rule.difficulty_group : rule.branch || 'Улучшения оружия';
        if (!branches.has(branch)) branches.set(branch, []);
        branches.get(branch).push(rule);
    }
    container.replaceChildren();
    const heading = element('header', 'modification-map-heading');
    heading.append(element('h4', '', 'Ветки модификаций'));
    const {used, available, maximum, family} = target.slots;
    heading.append(element('small', '', maximum == null ? family : `${family} · Занято ${number.format(used)} из ${number.format(available)} · Всего слотов ${number.format(maximum)}`));
    container.append(heading);
    const cards = new Map();
    let selected = rules.find(rule => rule.id === selectedId) || rules[0];
    for (const [branch, entries] of branches) {
        const section = element('section', 'modification-track');
        section.dataset.modificationBranch = branch;
        section.append(element('h5', '', branch));
        const levels = element('div', 'modification-levels');
        const maxLevel = Math.max(...entries.map(rule => rule.level || 1));
        if (maxLevel > 1) levels.classList.add('has-progression');
        levels.style.setProperty('--modification-level-count', maxLevel);
        for (let level = 1; level <= maxLevel; level++) {
            const cell = element('div', 'modification-level');
            if (entries[0].kind !== 'pda') cell.append(element('div', 'modification-level-label', `Уровень ${level}`));
            const choices = element('div', 'modification-choices');
            for (const rule of entries.filter(rule => (rule.level || 1) === level)) {
                const owned = installed.find(record => record.ruleId === rule.id);
                const missingLevels = rule.kind === 'pda' ? [] : Array.from({length:level - 1}, (_, index) => index + 1)
                    .filter(previous => !installed.some(record => record.level === previous &&
                        (rule.kind === 'weapon' || record.branch === rule.branch)));
                const unavailable = unavailableReason?.(rule);
                const card = element('button', 'modification-node');
                card.type = 'button';
                card.dataset.modificationId = rule.id;
                card.dataset.modificationLevel = level;
                const status = owned ? ['installed', 'Установлено']
                    : unavailable ? ['locked', unavailable]
                    : missingLevels.length ? ['locked', missingLevels.length === 1
                        ? `Нужен уровень ${missingLevels[0]}` : `Нужны уровни ${missingLevels.join(' и ')}`]
                    : rule.kind === 'weapon' && installed.some(record => record.level === level) ? ['replace', 'Можно заменить']
                    : rule.kind !== 'pda' && used >= available ? ['locked', 'Нужен слот или замена']
                    : ['available', 'Условия установки'];
                card.dataset.modificationState = status[0];
                card.setAttribute('aria-pressed', String(selected?.id === rule.id));
                const title = modificationTitle(rule);
                card.append(element('strong', '', title));
                const description = owned?.description || rule.description;
                if (description && description !== title) card.append(element('span', 'modification-node-effect', description));
                card.append(element('small', 'modification-node-state', status[1]));
                cards.set(rule.id, card);
                card.onclick = () => {
                    cards.get(selected?.id)?.setAttribute('aria-pressed', 'false');
                    selected = rule;
                    card.setAttribute('aria-pressed', 'true');
                    onSelect(rule);
                };
                choices.append(card);
            }
            cell.append(choices);
            levels.append(cell);
        }
        section.append(levels);
        container.append(section);
    }
    if (!selected) container.append(element('p', '', 'Для этого предмета пока нет улучшений.'));
    return selected;
}
