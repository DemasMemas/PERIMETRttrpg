const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../app/static/js/locationRainPuddles.js');
const pool = (x, z, height = 1) => ({ x, z, px: x + .5, pz: z + .5,
    low: height, high: height, depth: .2 });
const ground = { surface: () => 1, roof: () => 1, terrainType: () => 'grass' };

test('дождевые лужи с общей стороной используют одну высоту и одинаковую ширину стыка', async () => {
    const { joinRainPuddles } = await source;
    const puddles = [pool(2, 2), pool(3, 2, 1.03), pool(3, 3, 1.04)];
    puddles[1].pz -= .1;
    const saved = structuredClone(puddles);
    const { cells, joins } = joinRainPuddles(puddles, ground);
    assert.equal(joins, 2);
    assert.ok(cells.every(cell => cell.level === 1.052));
    const east = cells[0].links[0], west = cells[1].links[2];
    assert.deepEqual(east, [puddles[1].px - 2, puddles[1].pz - 2, .2 * .88]);
    assert.deepEqual(west, [puddles[0].px - 3, puddles[0].pz - 2, .2 * .88]);
    assert.equal(cells[0].links[1], null);
    assert.deepEqual(puddles, saved);
});

test('диагональ, перепад и преграда оставляют дождевые лужи раздельными', async () => {
    const { joinRainPuddles } = await source;
    const a = pool(2, 2), b = pool(3, 2);
    assert.equal(joinRainPuddles([a, pool(3, 3)], ground).joins, 0);
    assert.equal(joinRainPuddles([a, pool(3, 2, 1.09)], ground).joins, 0);
    assert.equal(joinRainPuddles([a, b], { ...ground, surface: x => Math.abs(x - 3) < .1 ? 1.08 : 1 }).joins, 0);
    assert.equal(joinRainPuddles([a, b], { ...ground, roof: x => Math.abs(x - 3) < .1 ? 2 : 1 }).joins, 0);
});

test('цепочка дождевых луж не поднимает всю поверхность над низкой клеткой', async () => {
    const { joinRainPuddles } = await source;
    const surface = x => 1 + .04 * Math.max(0, Math.floor(x) - 2);
    const { cells, joins } = joinRainPuddles([pool(2, 2), pool(3, 2, 1.04), pool(4, 2, 1.08)],
        { ...ground, surface, roof: surface });
    assert.equal(joins, 1);
    assert.equal(cells[0].level, cells[1].level);
    assert.notEqual(cells[1].level, cells[2].level);
    assert.equal(cells[1].links[0], null);
});
