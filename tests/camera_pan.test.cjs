const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const url = `data:text/javascript;base64,${Buffer.from(read('cameraPan.js')).toString('base64')}`;

test('overhead camera keeps a horizontal WASD basis', async () => {
    const { horizontalCameraAxes } = await import(url);
    class Vector3 {
        constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
        set(x, y, z) { Object.assign(this, { x, y, z }); return this; }
        setFromMatrixColumn(matrix, column) {
            const offset = column * 4;
            return this.set(...matrix.elements.slice(offset, offset + 3));
        }
        lengthSq() { return this.x ** 2 + this.y ** 2 + this.z ** 2; }
        normalize() {
            const length = Math.sqrt(this.lengthSq());
            this.x /= length; this.y /= length; this.z /= length;
            return this;
        }
        crossVectors(a, b) {
            return this.set(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z,
                a.x * b.y - a.y * b.x);
        }
    }
    const camera = {
        up: new Vector3(0, 1, 0),
        matrixWorld: { elements: [1, 0, 0, 0] },
        updateMatrixWorld() {},
    };
    const { forward, right } = horizontalCameraAxes({ Vector3 }, camera);
    assert.deepEqual([forward.x, forward.y, forward.z], [0, 0, -1]);
    assert.deepEqual([right.x, right.y, right.z], [1, 0, 0]);
    assert.match(read('lobby3d.js'), /horizontalCameraAxes\(THREE, camera\)/);
    assert.match(read('locationScene.js'), /horizontalCameraAxes\(THREE, camera\)/);
});

test('range sliders do not seize movement keys while text fields still do', async () => {
    const { isTypingTarget } = await import(url);
    const target = type => ({ closest: selector => selector === 'input' ? { type } : null });
    assert.equal(isTypingTarget(target('range')), false);
    assert.equal(isTypingTarget(target('number')), true);
    assert.equal(isTypingTarget(target('text')), true);
    assert.equal(isTypingTarget(target('checkbox')), false);
    assert.equal(isTypingTarget({ closest: selector => selector === 'input'
        ? { id: 'lobby-game-time', type: 'time' } : null }), false);
    assert.equal(isTypingTarget(target('time')), true);
    const select = id => ({ closest: selector => selector === 'select' ? { id } : null });
    assert.equal(isTypingTarget(select('tile-type-select')), false);
    assert.equal(isTypingTarget(select('loc-edit-terrain')), false);
    assert.equal(isTypingTarget(select('loc-grass-subtype')), false);
    assert.equal(isTypingTarget(select('world-water-level-mode')), false);
    assert.equal(isTypingTarget(select('loc-water-level-mode')), false);
    assert.equal(isTypingTarget(select('other-select')), true);
    assert.match(read('lobby3d.js'), /isTypingTarget\(e\.target\)/);
    assert.match(read('ui_interactions.js'), /const isInput = isTypingTarget\(target\)/);
    assert.match(read('mapEditorQuickMenu.js'), /if \(isTypingTarget\(event\.target\)\s*\|\|[^\n]*modal[^\n]*\) return/);
});
