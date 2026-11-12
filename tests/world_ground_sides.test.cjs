const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const helper = vm.runInNewContext(fs.readFileSync(path.join(root, 'app/static/js/worldGroundSides.js'), 'utf8')
    .replace('export function', 'function') + '\nbuildWorldGroundSides;');
const three = import('data:text/javascript;base64,' + fs.readFileSync(path.join(root,
    'app/static/vendor/three-0.162.0/build/three.module.min.js')).toString('base64'));

function ground(T, heights, size) {
    const box = new T.BoxGeometry(1, 1, 1), indices = [];
    for (let i = 0; i < box.index.count; i += 3) {
        if (box.attributes.normal.getY(box.index.getX(i)) === 0) indices.push(...box.index.array.slice(i, i + 3));
    }
    box.setIndex(indices);
    const mesh = new T.InstancedMesh(box, new T.MeshBasicMaterial(), size * size);
    const tiles = new Int16Array(size * size).fill(-1), matrix = new T.Matrix4();
    let count = 0;
    for (let i = 0; i < heights.length; i++) {
        if (heights[i] === null) continue;
        tiles[i] = count;
        matrix.makeScale(1, heights[i], 1).setPosition(i % size + .5, heights[i] / 2, Math.floor(i / size) + .5);
        mesh.setMatrixAt(count, matrix); mesh.setColorAt(count++, new T.Color(.2, .3, .4));
    }
    mesh.count = count;
    return { mesh, tiles };
}

test('плоский чанк оставляет наружные стенки и сохраняет все клетки для выбора', async () => {
    const T = await three, { mesh, tiles } = ground(T, Array(1024).fill(1.1), 32);
    const original = mesh.instanceMatrix.array.slice();
    const sides = helper(T, mesh, tiles, 32);
    assert.equal(sides.attributes.position.count / 3, 32 * 4 * 2);
    assert.equal(mesh.count, 1024);
    assert.equal(tiles[1023], 1023);
    assert.deepEqual(mesh.instanceMatrix.array, original);
    sides.computeBoundingBox();
    assert.deepEqual(sides.boundingBox.min.toArray(), [0, 0, 0]);
    assert.deepEqual(sides.boundingBox.max.toArray(), [32, Math.fround(1.1), 32]);
});

test('перепад высоты и открытый край у воды остаются видимыми', async () => {
    const T = await three, { mesh, tiles } = ground(T, [2, 1, null, null], 2);
    const sides = helper(T, mesh, tiles, 2);
    assert.equal(sides.attributes.position.count / 3, 14, 'Убрана лишь стенка низкой клетки, закрытая высокой');
    const p = sides.attributes.position, n = sides.attributes.normal;
    assert.ok(Array.from({ length: p.count }, (_, i) => i).some(i => p.getX(i) === 1 && p.getY(i) === 2 && n.getX(i) === 1));
    assert.ok(Array.from({ length: p.count }, (_, i) => i).some(i => p.getZ(i) === 1 && n.getZ(i) === 1));
    assert.equal(sides.attributes.color.count, p.count);
    assert.equal(sides.attributes.uv.count, p.count);
});

test('после смены высоты и удаления клетки соседние грани возвращаются', async () => {
    const T = await three, fixture = ground(T, [1, 1, null, null], 2);
    assert.equal(helper(T, fixture.mesh, fixture.tiles, 2).attributes.position.count / 3, 12);
    fixture.mesh.setMatrixAt(1, new T.Matrix4().makeScale(1, 2, 1).setPosition(1.5, 1, .5));
    assert.equal(helper(T, fixture.mesh, fixture.tiles, 2).attributes.position.count / 3, 14);
    fixture.tiles[0] = -1;
    assert.equal(helper(T, fixture.mesh, fixture.tiles, 2).attributes.position.count / 3, 8);
});
