const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');

test('weather audio starts on player input without an external library', () => {
    const source = read('weather.js');
    const start = source.indexOf('let rainSound = null;');
    const end = source.indexOf('export function applyWeather(', start);
    assert.ok(start >= 0 && end > start);
    const sounds = [];
    const listeners = new Map();
    const button = {
        hidden: true,
        addEventListener(name, handler) { this[name] = handler; },
    };
    class FakeAudio {
        constructor(src) { this.src = src; this.paused = true; this.plays = 0; sounds.push(this); }
        play() { this.paused = false; this.plays += 1; return Promise.resolve(); }
        pause() { this.paused = true; }
    }
    const context = createSceneContext({
        window: { Audio: FakeAudio, addEventListener: (name, handler) => listeners.set(name, handler) },
        document: {
            createElement() { return button; },
            body: { appendChild() {} },
        },
        console,
    });
    vm.runInContext(source.slice(start, end).replaceAll('export function ', 'function '), context);
    context.initWeather();
    vm.runInContext('desiredRainAudio = { enabled: true, volume: 0.4 }; syncWeatherAudio();', context);
    assert.equal(sounds[0].plays, 0);
    assert.equal(button.hidden, false);
    listeners.get('pointerdown')();
    assert.equal(sounds[0].plays, 1);
    vm.runInContext('desiredEmissionAudio = { enabled: true, volume: 0.05 }; syncWeatherAudio();', context);
    assert.equal(sounds[1].plays, 1);
    assert.equal(sounds[1].loop, true);
});

test('lobby clock blends twilight and cloud clarity changes lighting', () => {
    const weather = read('weather.js');
    const lobby = read('lobbyData.js');
    const sky = read('lobby3d.js');
    const start = weather.indexOf('const DEFAULT_FOG_INTENSITY');
    const end = weather.indexOf('\nfunction setLoopingSound(', start);
    const conditions = [];
    const times = [];
    class FakeColor {
        setHex() { return this; }
        lerpColors() { return this; }
        lerp() { return this; }
    }
    const directionalLight = { color: new FakeColor(), intensity: 0 };
    const ambientLight = { color: new FakeColor(), intensity: 0 };
    const fillLight = { intensity: 0 };
    const context = createSceneContext({
        Math, Number, THREE: { Color: FakeColor }, directionalLight, ambientLight, fillLight,
        setSkyConditions: (daylight, clarity, starlight) => conditions.push({ daylight, clarity, starlight }),
        setCelestialTime: minutes => times.push(minutes),
    });
    vm.runInContext(weather.slice(start, end).replaceAll('export function ', 'function '), context);
    assert.equal(context.daylightAtMinutes(8 * 60), 1);
    assert.equal(context.daylightAtMinutes(12 * 60), 1);
    assert.equal(context.daylightAtMinutes(14 * 60), 1);
    assert.equal(context.daylightAtMinutes(22 * 60), 0);
    assert.equal(context.daylightAtMinutes(0), 0);
    assert.ok(context.daylightAtMinutes(5 * 60 + 30) > 0);
    assert.ok(context.daylightAtMinutes(5 * 60 + 30) < 1);
    assert.ok(context.daylightAtMinutes(18 * 60 + 30) > 0);
    assert.ok(context.daylightAtMinutes(18 * 60 + 30) < 1);
    assert.equal(context.starlightAtMinutes(18 * 60), 0);
    assert.equal(context.starlightAtMinutes(20 * 60 + 50), 0);
    assert.ok(context.starlightAtMinutes(21 * 60 + 30) > 0);
    assert.ok(context.starlightAtMinutes(21 * 60 + 30) < 1);
    assert.equal(context.starlightAtMinutes(22 * 60 + 30), 1);
    assert.equal(context.starlightAtMinutes(2 * 60), 1);
    assert.ok(context.starlightAtMinutes(5 * 60 + 30) < 1);
    vm.runInContext('currentClouds.enabled = true; currentClouds.intensity = 0', context);
    context.applyWorldTime(22 * 60);
    assert.equal(conditions.at(-1).daylight, 0);
    assert.equal(conditions.at(-1).clarity, 0);
    assert.equal(conditions.at(-1).starlight, 2 / 3 * 2 / 3 * (3 - 2 * 2 / 3));
    assert.equal(times.at(-1), 22 * 60);
    const nightAmbient = ambientLight.intensity;
    context.applyWorldTime(14 * 60);
    assert.equal(conditions.at(-1).starlight, 0);
    assert.ok(ambientLight.intensity > nightAmbient * 4);
    const overcastDaylight = directionalLight.intensity + ambientLight.intensity + fillLight.intensity;
    assert.ok(overcastDaylight >= 0.9 && overcastDaylight <= 1.0);
    context.applyWorldTime(8 * 60);
    assert.equal(conditions.at(-1).daylight, 1);
    assert.ok(ambientLight.intensity > nightAmbient);
    const overcastDirect = directionalLight.intensity;
    assert.ok(overcastDirect > nightAmbient * 4);
    vm.runInContext('currentClouds.intensity = 1', context);
    context.applyWorldTime(8 * 60);
    assert.equal(conditions.at(-1).clarity, 1);
    assert.ok(directionalLight.intensity > overcastDirect * 2);
    context.applyWorldTime(14 * 60);
    const clearDaylight = directionalLight.intensity + ambientLight.intensity + fillLight.intensity;
    assert.ok(clearDaylight >= 2.0 && clearDaylight <= 2.2);
    context.applyWorldTime(10 * 60);
    assert.equal(conditions.at(-1).daylight, 1);
    assert.ok(directionalLight.intensity + ambientLight.intensity + fillLight.intensity >= 2.0);
    context.applyWorldTime(3 * 60);
    assert.equal(conditions.at(-1).daylight, 0);
    const clearNight = directionalLight.intensity + ambientLight.intensity + fillLight.intensity;
    assert.ok(clearNight >= 0.3 && clearNight <= 0.34);
    assert.ok(clearNight < clearDaylight / 5);
    assert.match(lobby, /applyWorldTime\(minutes\)/);
    assert.match(sky, /sunDisc\.position\.copy\(sunDirection\)/);
    assert.match(sky, /moonDisc\.position\.copy\(moonDirection\)/);
    assert.match(sky, /worldSkySphere\.material\.uniforms\.starRotation\.value = angle/);
    assert.match(sky, /stars\.rotation\.z = angle/);
    assert.match(sky, /float cloudCover = mix\(0\.82 \+ cloudPattern \* 0\.18, cloudPattern \* 0\.5, clarity\)/);
    assert.match(sky, /uniform float starlight/);
    assert.match(read('main.js'), /settings\.sun\?\.intensity \?\? 0\.5/);
    assert.match(read('main.js'), /checkbox\?\.addEventListener\('change', \(\) => \{\s*slider\.disabled = !checkbox\.checked/);
});

test('sky conditions fade stars and celestial discs with cloud cover and twilight', () => {
    const source = read('lobby3d.js');
    const start = source.indexOf('export function setSkyConditions(');
    const end = source.indexOf('// ===== Конец неба =====', start);
    assert.ok(start >= 0 && end > start);
    class FakeColor {
        lerp() { return this; }
    }
    const worldSkySphere = { material: { uniforms: { daylight: {}, clarity: {}, starlight: {} } } };
    const stars = { material: {}, visible: false };
    const sunDisc = { material: {} };
    const moonDisc = { material: {} };
    const context = createSceneContext({
        THREE: { MathUtils: { clamp: (value, low, high) => Math.max(low, Math.min(high, value)) }, Color: FakeColor },
        worldSkySphere, stars, sunDisc, moonDisc, scene: { background: { copy() {} } },
    });
    vm.runInContext(source.slice(start, end).replace('export function ', 'function '), context);
    context.setSkyConditions(0, 0, 1);
    assert.equal(stars.visible, false);
    assert.equal(stars.material.opacity, 0);
    const hiddenSun = sunDisc.material.opacity;
    context.setSkyConditions(0, 1, 1);
    assert.equal(stars.visible, true);
    assert.equal(stars.material.opacity, 1);
    assert.ok(sunDisc.material.opacity > hiddenSun);
    context.setSkyConditions(0.5, 1, 0.5);
    assert.equal(stars.material.opacity, 0.5);
    assert.equal(worldSkySphere.material.uniforms.daylight.value, 0.5);
    assert.equal(worldSkySphere.material.uniforms.starlight.value, 0.5);
    context.setSkyConditions(0.3, 1, 0);
    assert.equal(stars.visible, false);
});

test('low sun and moon cap world object shadows at about 1.25 times their height', () => {
    const source = read('lobby3d.js');
    const start = source.indexOf('let celestialMinutes = 480;');
    const end = source.indexOf('export function setSkyConditions(', start);
    assert.ok(start >= 0 && end > start);
    class Vector3 {
        constructor(x = 0, y = 0, z = 0) { Object.assign(this, { x, y, z }); }
        clone() { return new Vector3(this.x, this.y, this.z); }
        negate() { this.x *= -1; this.y *= -1; this.z *= -1; return this; }
        normalize() {
            const length = Math.hypot(this.x, this.y, this.z);
            this.x /= length; this.y /= length; this.z /= length;
            return this;
        }
        copy(other) { Object.assign(this, other); return this; }
        multiplyScalar(value) { this.x *= value; this.y *= value; this.z *= value; return this; }
        set(x, y, z) { Object.assign(this, { x, y, z }); return this; }
    }
    const position = new Vector3();
    const target = { position: new Vector3(), updateMatrixWorld() {} };
    const context = createSceneContext({
        Math, Number, THREE: { Vector3 }, MAX_CHUNK_X: 31, MAX_CHUNK_Y: 31, CHUNK_SIZE: 16,
        sunDisc: { position: new Vector3() }, moonDisc: { position: new Vector3() },
        worldSkySphere: { material: { uniforms: { starRotation: {} } } },
        stars: { rotation: {} }, directionalLight: { position, target }, markWorldShadowsDirty() {},
    });
    vm.runInContext(source.slice(start, end).replaceAll('export function ', 'function '), context);
    for (const minutes of [6 * 60 + 10, 8 * 60, 19 * 60 + 50, 20 * 60 + 10]) {
        context.setCelestialTime(minutes);
        const horizontal = Math.hypot(position.x - target.position.x, position.z - target.position.z);
        assert.ok(horizontal / position.y <= 1.26, `shadow ratio exceeded at ${minutes} minutes`);
    }
});

test('fog overlay distinguishes explored from unknown tiles and hides for GM', () => {
    const source = read('locationScene.js');
    const start = source.indexOf('function clearFogOverlay()');
    const end = source.indexOf('function applyFogOfWar()', start);
    assert.ok(start >= 0 && end > start);
    const fogOverlayMeshes = new Map();
    const context = createSceneContext({
        fogOverlayMeshes,
        fogMemory: new Map([['1:0', {}]]),
        currentLocationData: { grid_width: 2, grid_height: 2, tiles_data: [] },
        scene: { add() {}, remove() {} },
        fogSurfaceData: null,
        waterFogSurfaces: null,
        buildFogSurfaceData: () => ({
            positions: Array(4 * 9 * 3).fill(0),
            tileIndices: Array.from({ length: 4 }, (_, tile) => [tile * 9, tile * 9 + 1, tile * 9 + 2]),
        }),
        isTileVisibleToPlayer: (x, y) => x === 0 && y === 0,
        THREE: {
            DoubleSide: 2,
            DynamicDrawUsage: 35048,
            BufferGeometry: class {
                setAttribute() {}
                setIndex(index) { this.index = index; }
                setDrawRange(_start, count) { this.drawCount = count; }
                dispose() {}
            },
            Float32BufferAttribute: class {},
            BufferAttribute: class {
                constructor(array) { this.array = array; }
                setUsage() {}
            },
            MeshBasicMaterial: class { dispose() {} },
            Mesh: class {
                constructor(geometry, material) {
                    this.geometry = geometry; this.material = material;
                }
            },
        },
    });
    vm.runInContext(source.slice(start, end), context);
    context.updateFogOverlay(true, []);
    assert.equal(fogOverlayMeshes.get('explored').geometry.drawCount, 3);
    assert.equal(fogOverlayMeshes.get('unexplored').geometry.drawCount, 6);
    context.updateFogOverlay(false, []);
    assert.equal(fogOverlayMeshes.get('explored').visible, false);
    assert.equal(fogOverlayMeshes.get('unexplored').visible, false);
});

test('deleting ground container closes the open exchange and removes its model', () => {
    const source = read('locationScene.js');
    const start = source.indexOf('export function removeLocationObject(');
    const end = source.indexOf('window.updateLocationObject = updateLocationObject;', start);
    assert.ok(start >= 0 && end > start);
    let closed = 0;
    let removed = 0;
    let refreshed = 0;
    const context = createSceneContext({
        window: {},
        currentLocationData: { objects: [{ id: 17, type: 'ground_item' }] },
        containerInteractionState: { objectId: '17' },
        containerInteractionRefresh: () => { refreshed += 1; },
        closeContainerInteractionMenu: () => { closed += 1; },
        removeLocationObjectMesh: () => { removed += 1; },
        replaceLocationObjectMesh: () => {},
        invalidateMovementMapCache: () => {},
        applyFogOfWar: () => {},
        isFireObject: () => false,
        isObjectLightSource: () => false,
        applyLocationLighting: () => {},
        syncPortableLights: () => {},
    });
    vm.runInContext(source.slice(start, end).replaceAll('export function ', 'function '), context);
    context.removeLocationObject('17');
    assert.equal(closed, 1);
    assert.equal(removed, 1);
    assert.equal(context.currentLocationData.objects.length, 0);
    context.updateLocationObject({ id: 17, type: 'ground_item', properties: { contents: [1] } });
    assert.equal(refreshed, 1);
});

test('night stars render after sky and headphones band is unfilled', () => {
    const sky = read('lobby3d.js');
    const sheet = read('characterSheet.js');
    const styles = fs.readFileSync(path.join(__dirname, '../app/static/style.css'), 'utf8');
    assert.match(sky, /stars\.renderOrder = 1/);
    assert.match(sky, /const elevation = -0\.75 \+ Math\.random\(\) \* 1\.35/);
    assert.match(sky, /new THREE\.PointsMaterial\(\{[\s\S]*?fog: false/);
    assert.match(sky, /float starShape = 1\.0 - smoothstep/);
    assert.match(sky, /finalColor \+= vec3\(0\.9, 0\.95, 1\.0\) \* star/);
    assert.match(sheet, /class="loadout-art-headband"/);
    assert.match(styles, /\.loadout-art-headphones \.loadout-art-headband \{ fill: none !important/);
    assert.match(read('locationScene.js'), /<b>\(\$\{x\}, \$\{z\}\)<\/b>/);
});

test('known blood packet type is visible in inventory and GM combat addition matches the turn panel', () => {
    const sheet = read('characterSheet.js');
    const combat = read('locationScene.js');
    const styles = fs.readFileSync(path.join(__dirname, '../app/static/style.css'), 'utf8');
    assert.match(sheet, /bloodBadge\.textContent = knownType \? formatBloodType\(knownType\) : 'Группа неизвестна'/);
    assert.match(sheet, /<strong>Группа крови:<\/strong>/);
    assert.match(combat, /class="combat-participants-panel"/);
    assert.match(combat, /В начале списка со следующего раунда, без прерывания текущего хода/);
    assert.match(styles, /\.combat-participants-controls/);
    assert.match(styles, /#combat-status-panel \.combat-add-participant-select \{/);
    assert.match(styles, /#combat-status-panel \.combat-add-participant-select option \{/);
    assert.match(styles, /color-scheme: dark/);
});

test('ground items remain accessible beneath another character', () => {
    const source = read('locationScene.js');
    const start = source.indexOf('function showCombatActionMenu(');
    const end = source.indexOf('function getFogVisionSources()', start);
    const menu = source.slice(start, end);
    assert.match(menu, /if \(groundItemsHere\) \{[\s\S]*?label: 'Вещи',[\s\S]*?allowAlways: true/);
    assert.match(menu, /showContainerInteractionMenu\(groundItemsHere\)/);
});

test('combat state refreshes model health and effects after GM healing', () => {
    const source = read('locationScene.js');
    const start = source.indexOf('export function setCombatState(');
    const end = source.indexOf('function showAvailableOpportunityAttack()', start);
    const sync = source.slice(start, end);
    assert.match(sync, /entry\.hpZones = character\.hp_zones/);
    assert.match(sync, /entry\.effects = character\.effects/);
});

test('mutant group placement uses nearby distinct free cells', async () => {
    const source = read('locationScene.js');
    const start = source.indexOf('async function spawnMutantGroupNearTile(');
    const end = source.indexOf('export async function openOwnerSelectionModal(', start);
    assert.ok(start >= 0 && end > start);
    const placed = [];
    const context = createSceneContext({
        getMovementMap: () => ({ blockedTiles: new Set(['3:3', '2:2']) }),
        currentLocationData: { grid_width: 8, grid_height: 8 },
        spawnCharacterAtTile: async (id, x, y, owner) => { placed.push({ id, x, y, owner }); },
        showNotification() {},
    });
    vm.runInContext(source.slice(start, end), context);
    await context.spawnMutantGroupNearTile([10, 11, 12, 13, 14], 3, 3, 99);
    assert.equal(placed.length, 5);
    assert.equal(new Set(placed.map(item => `${item.x}:${item.y}`)).size, 5);
    assert.ok(placed.every(item => Math.max(Math.abs(item.x - 3), Math.abs(item.y - 3)) <= 1));
    assert.ok(placed.every(item => !['3:3', '2:2'].includes(`${item.x}:${item.y}`)));
    assert.ok(placed.every(item => item.owner === 99));
});
