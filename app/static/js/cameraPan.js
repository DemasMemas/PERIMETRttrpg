export function horizontalCameraAxes(THREE, camera) {
    camera.updateMatrixWorld();
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
    right.y = 0;
    if (right.lengthSq() < 1e-6) right.set(1, 0, 0);
    right.normalize();
    const forward = new THREE.Vector3().crossVectors(camera.up, right).normalize();
    return { forward, right };
}

export function isTypingTarget(target) {
    const input = target?.closest?.('input');
    if (input) {
        if (input.id === 'lobby-game-time' && input.type === 'time') return false;
        return !['range', 'checkbox', 'radio', 'button', 'submit', 'color'].includes(input.type);
    }
    const select = target?.closest?.('select');
    if (select) return !['tile-type-select', 'loc-edit-terrain', 'loc-grass-subtype',
        'world-water-level-mode', 'loc-water-level-mode'].includes(select.id);
    return Boolean(target?.closest?.('textarea, [contenteditable="true"]'));
}
