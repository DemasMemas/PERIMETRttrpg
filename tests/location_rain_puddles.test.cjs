const test = require('node:test');
const assert = require('node:assert/strict');

const source = import('../app/static/js/locationRainPuddles.js');
const ground = { surface: () => 1, roof: () => 1, terrainType: () => 'asphalt' };

test('дождь заполняет один широкий водосбор на соседних клетках близкой высоты', async () => {
    const { planRainPuddles } = await source;
    const surface = x => x < 1 ? 1 : 1.03;
    const [pool, extra] = planRainPuddles({ width: 2, height: 1 }, .5, { ...ground, surface, roof: surface });
    assert.equal(extra, undefined);
    assert.equal(pool.merged, true);
    assert.equal(pool.edge.x, 1);
    assert.equal(pool.height, 1.039);
    assert.ok(pool.across >= .3 && pool.across / pool.radius >= .45);
    // Ширина в самой узкой точке стыка сохраняется и при максимальном искажении контура.
    const c = Math.cos(pool.angle), s = Math.sin(pool.angle);
    for (const offset of [-.2, .2]) {
        const x = pool.edge.x - pool.x, z = pool.edge.z + offset - pool.z;
        assert.ok(Math.hypot((x * c + z * s) / pool.radius, (-x * s + z * c) / pool.across) < .875);
    }
});

test('дождевые лужи остаются раздельными на диагонали, у перепада и под крышей', async () => {
    const { planRainPuddles } = await source;
    const pair = terrain => planRainPuddles({ width: 2, height: 1 }, 1, { ...ground, ...terrain });
    const diagonal = planRainPuddles({ width: 2, height: 2 }, 1, {
        ...ground, terrainType: (x, z) => x === z ? 'asphalt' : 'water',
    });
    assert.ok(diagonal.length && diagonal.every(pool => !pool.merged));
    assert.ok(pair({ surface: x => x < 1 ? 1 : 1.09 }).every(pool => !pool.merged));
    assert.ok(pair({ surface: x => Math.abs(x - 1) < .12 ? 1.09 : 1 }).every(pool => !pool.merged));
    const covered = pair({ roof: x => x < 1 ? 2 : 1 });
    assert.equal(covered.length, 1);
    assert.ok(!covered[0].merged && covered[0].x > 1);
    assert.deepEqual(pair({ terrainType: () => 'water' }), []);
});

test('редкие лужи распределены по всей подлокации, а ливень сохраняет существующие водосборы', async () => {
    const { planRainPuddles } = await source;
    const bounds = { width: 60, height: 60 };
    const rain = planRainPuddles(bounds, .5, ground);
    const storm = planRainPuddles(bounds, 1, ground);
    assert.ok(rain.length > 25 && rain.length <= 80);
    assert.ok(storm.length > rain.length && storm.length <= 100);
    const stormPositions = new Set(storm.map(pool => JSON.stringify(pool)));
    assert.ok(rain.every(pool => stormPositions.has(JSON.stringify(pool))));
    for (const [x, z] of [[0, 0], [54, 0], [0, 54], [54, 54]]) {
        assert.ok(storm.some(pool => Math.abs(pool.x - x - 3) < 9 && Math.abs(pool.z - z - 3) < 9));
    }
});

test('перемещение и зум камеры не меняют экземпляры луж даже на большой карте', async () => {
    const { createRainPuddles, planRainPuddles } = await source;
    const THREE = await import('../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const scene = new THREE.Scene();
    const bounds = { width: 200, height: 200, centerX: 10, centerZ: 10, viewDistance: 8 };
    let queries = 0;
    const puddles = createRainPuddles(THREE, scene, { ...ground, bounds: () => bounds,
        surface: () => { queries++; return 1; } });
    puddles.update(.5, 'исходная геометрия', .016);
    const mesh = scene.children[0];
    const initial = puddles.diagnostics();
    const checked = queries;
    assert.equal(initial.puddleDrawCalls, 1);
    assert.equal(initial.puddles, mesh.count);
    assert.ok(mesh.count > 192);
    const allImpacts = planRainPuddles(bounds, .5, ground).map(({ x, z, height: y, seed }) => ({ x, z, y, seed }));
    assert.ok(allImpacts.length > 192);
    for (const [x, z, radius] of [[10, 10, 10], [60, 36, 6], [190, 190, 10], [0, 0, 3]]) {
        const expected = allImpacts.filter(impact => Math.abs(impact.x - x) <= radius && Math.abs(impact.z - z) <= radius);
        assert.deepEqual([...puddles.impactsNear(x, z, radius)], expected);
    }
    assert.ok([...puddles.impactsNear(190, 190, 10)].some(pool => pool.x > 185 && pool.z > 185));
    Object.assign(bounds, { centerX: 190, centerZ: 190, viewDistance: 130 });
    puddles.update(.5, 'исходная геометрия', .016);
    assert.equal(scene.children[0], mesh);
    assert.equal(queries, checked);
    assert.deepEqual(puddles.diagnostics(), initial);
    puddles.hide();
    assert.equal(mesh.visible, false);
    assert.equal(puddles.diagnostics().puddles, 0);
    assert.deepEqual([...puddles.impactsNear(10, 10, 10)], []);
    puddles.dispose();
    assert.equal(scene.children.length, 0);
});

test('погода пересчитывает лужи при изменении грунта и крыш', async () => {
    const { createLocationWeather } = await import('../app/static/js/locationWeather.js');
    const THREE = await import('../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const scene = new THREE.Scene(), roofs = [];
    let queries = 0, blockerScans = 0, groundHeight = 1;
    const weather = createLocationWeather(THREE, scene, {
        bounds: () => ({ width: 12, height: 12 }), terrain: () => groundHeight, terrainType: ground.terrainType,
        surface: () => { queries++; return groundHeight; }, blockers: () => { blockerScans++; return roofs; },
    });
    const groundGeometry = {};
    weather.apply({ rain: { enabled: true, intensity: .5 } });
    weather.update(.016, groundGeometry);
    const puddleCount = weather.diagnostics().puddles;
    assert.ok(puddleCount > 0);

    const roof = new THREE.Mesh(new THREE.PlaneGeometry(12, 12).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial());
    roof.position.set(6, 3, 6);
    roofs.push(roof);
    weather.invalidateBlockers();
    weather.update(0, groundGeometry);
    assert.equal(weather.diagnostics().puddles, 0);
    assert.equal(weather.diagnostics({ x: 4, z: 4 }).surfaceHeight, 3);

    const attribute = scene.getObjectByName('location-weather-rain').geometry.getAttribute('position');
    const drops = weather.diagnostics().drops;
    const positions = attribute.array.slice(0, drops * 6);
    const checked = queries;
    weather.apply({ rain: { enabled: true, intensity: .5 }, fog: { enabled: true, intensity: .7 } });
    weather.update(0, groundGeometry);
    assert.ok(weather.diagnostics().fogDensity > 0);
    assert.equal(blockerScans, 2);
    assert.equal(queries, checked);
    assert.deepEqual(attribute.array.slice(0, drops * 6), positions);
    assert.deepEqual(attribute.updateRanges, [{ start: 0, count: drops * 6 }]);

    roof.position.x = 18;
    weather.invalidateBlockers();
    weather.update(0, groundGeometry);
    assert.equal(weather.diagnostics().puddles, puddleCount);
    assert.equal(weather.diagnostics({ x: 4, z: 4 }).surfaceHeight, 1);
    assert.equal(blockerScans, 3);

    groundHeight = 2;
    weather.invalidateGround();
    weather.update(0, groundGeometry);
    assert.equal(weather.diagnostics({ x: 4, z: 4 }).surfaceHeight, 2);
    assert.ok(queries > checked);
    assert.equal(blockerScans, 3);

    groundHeight = 2.5;
    weather.update(0, {});
    assert.equal(weather.diagnostics({ x: 4, z: 4 }).surfaceHeight, 2.5);
    assert.equal(blockerScans, 3);
    roof.geometry.dispose();
    roof.material.dispose();
    weather.dispose();
});
