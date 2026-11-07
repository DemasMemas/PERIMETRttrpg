export function fillModificationOptions(select, rules) {
    select.replaceChildren();
    const groups = new Map();
    for (const rule of rules) {
        let parent = select;
        if (rule.kind !== 'weapon' && rule.branch) {
            if (!groups.has(rule.branch)) {
                const group = document.createElement('optgroup');
                group.label = rule.branch;
                groups.set(rule.branch, group);
                select.append(group);
            }
            parent = groups.get(rule.branch);
        }
        const option = document.createElement('option');
        option.value = rule.id;
        option.textContent = `Ур. ${rule.level || 1} · ${rule.name || rule.description}`;
        parent.append(option);
    }
}
