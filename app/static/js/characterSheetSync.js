const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function sameScalar(left, right) {
    return Object.is(left, right) || (
        typeof left === 'number'
        && typeof right === 'number'
        && Number.isNaN(left)
        && Number.isNaN(right)
    );
}

function sameArray(left, right) {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    if (left.length !== right.length) return false;
    return left.every((value, index) => sameValue(value, right[index]));
}

function sameValue(left, right) {
    if (sameScalar(left, right)) return true;
    if (Array.isArray(left) || Array.isArray(right)) return sameArray(left, right);
    if (!isRecord(left) || !isRecord(right)) return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length
        && keys.every(key => Object.hasOwn(right, key) && sameValue(left[key], right[key]));
}

export function diffCharacterData(previous, next) {
    const scalarPaths = [];
    const structuralPaths = [];

    function visit(left, right, path) {
        if (sameScalar(left, right)) return;
        if (Array.isArray(left) || Array.isArray(right)) {
            if (sameArray(left, right)) return;
            structuralPaths.push(path);
            return;
        }
        if (isRecord(left) && isRecord(right)) {
            const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
            for (const key of keys) {
                const childPath = path ? `${path}.${key}` : key;
                if (!(key in left) || !(key in right)) {
                    const value = key in right ? right[key] : left[key];
                    (isRecord(value) || Array.isArray(value) ? structuralPaths : scalarPaths).push(childPath);
                    continue;
                }
                visit(left[key], right[key], childPath);
            }
            return;
        }
        (isRecord(left) || isRecord(right) ? structuralPaths : scalarPaths).push(path);
    }

    visit(previous || {}, next || {}, '');
    return {
        scalarPaths: scalarPaths.filter(path => path && path !== '_revision' && !pathTouches(path,'_artifact_profile')),
        structuralPaths: structuralPaths.filter(path => path && path !== '_revision' && !pathTouches(path,'_artifact_profile')),
    };
}

export function pathTouches(path, root) {
    return path === root || path.startsWith(`${root}.`);
}
