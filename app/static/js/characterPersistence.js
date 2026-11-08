const clone = value => JSON.parse(JSON.stringify(value));
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const snapshots = new Map();

export function consumeChangedSheetInputs(form) {
    return Array.from(form.querySelectorAll('input, select, textarea')).filter(input => {
        if (!input.getAttribute('name')) return false;
        if (input.type === 'checkbox' || input.type === 'radio') {
            if (input.checked === input.defaultChecked) return false;
            input.defaultChecked = input.checked;
        } else if (input.tagName === 'SELECT') {
            const options = Array.from(input.options);
            const defaults = options.filter(option => option.defaultSelected);
            const previous = defaults.length ? defaults : options.slice(0, 1);
            if (options.every(option => option.selected === previous.includes(option))) return false;
            options.forEach(option => { option.defaultSelected = option.selected; });
        } else {
            if (input.value === input.defaultValue) return false;
            input.defaultValue = input.value;
        }
        return true;
    });
}

export function rememberCharacterSnapshots(value) {
    if (!value || typeof value !== 'object') return;
    if (Number.isInteger(value._revision) && Number.isInteger(value._character_id)) {
        const key = `${value._character_id}:${value._revision}`;
        if (!snapshots.has(key)) snapshots.set(key, clone(value));
        if (snapshots.size > 200) snapshots.delete(snapshots.keys().next().value);
        return;
    }
    if (Array.isArray(value)) value.forEach(rememberCharacterSnapshots);
    else for (const key of ['data', 'updates', 'character_data', 'target_data', 'actor_data', 'character', 'characters', 'result']) {
        if (key in value) rememberCharacterSnapshots(value[key]);
    }
}

export function getCharacterBase(id, data) {
    return snapshots.get(`${id}:${data?._revision}`);
}

// Preserve edits typed during a save while accepting server normalization.
export function mergeLocalChanges(base, local, saved) {
    if (same(base, local)) return saved === undefined ? undefined : clone(saved);
    if (!record(base) || !record(local) || !record(saved)) return clone(local);
    const result = clone(saved);
    for (const key of new Set([...Object.keys(base), ...Object.keys(local)])) {
        if (key === '_revision' || key === '_artifact_profile') continue;
        if (!(key in local)) delete result[key];
        else if (!same(base[key], local[key])) {
            Object.defineProperty(result, key, {
                value: mergeLocalChanges(base[key], local[key], saved[key]),
                enumerable: true, configurable: true, writable: true,
            });
        }
    }
    return result;
}

export function createCharacterSaver(send, getBase = () => null) {
    const queues = new Map();
    const sources = new WeakMap();
    const ownSaves = new Set();
    let sequence = 0;

    function save(characterId, updates) {
        const id = Number(characterId);
        const snapshot = clone(updates);
        const source = updates.data;
        let state = source && sources.get(source);
        if (source && !state) {
            state = { accepted: new Set(), lastSubmitted: null, lastSaved: null, error: null };
            sources.set(source, state);
        }
        const run = async () => {
            if (state?.error) throw state.error;
            const outgoing = clone(snapshot);
            if (state?.lastSaved && state.accepted.has(snapshot.data._revision)
                && snapshot.data._revision !== state.lastSaved._revision) {
                outgoing.data = mergeLocalChanges(state.lastSubmitted, snapshot.data, state.lastSaved);
            }
            outgoing._save_id = `${Date.now()}-${Math.random().toString(36).slice(2)}-${++sequence}`;
            const base = getBase(id, outgoing.data);
            if (base) outgoing._base_data = clone(base);
            ownSaves.add(outgoing._save_id);
            if (ownSaves.size > 100) ownSaves.delete(ownSaves.values().next().value);
            try {
                const result = await send(id, outgoing);
                if (state && result.data) {
                    state.accepted.add(snapshot.data._revision);
                    state.accepted.add(outgoing.data._revision);
                    state.lastSubmitted = snapshot.data;
                    state.lastSaved = clone(result.data);
                    const merged = mergeLocalChanges(snapshot.data, source, result.data);
                    for (const key of Object.keys(source)) delete source[key];
                    Object.assign(source, merged);
                }
                return result;
            } catch (error) {
                // A failed or uncertain save must not be retried with a newer token.
                if (state) state.error = error;
                throw error;
            }
        };
        const task = (queues.get(id) || Promise.resolve()).catch(() => {}).then(run);
        queues.set(id, task);
        const cleanup = () => { if (queues.get(id) === task) queues.delete(id); };
        task.then(cleanup, cleanup);
        return task;
    }
    return { save, whenIdle: id => queues.get(Number(id)) || Promise.resolve(),
        isSaving: id => queues.has(Number(id)), isOwnSave: id => ownSaves.has(id) };
}
