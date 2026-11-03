const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
const start = source.indexOf('function isFogHiddenObject(');
const end = source.indexOf('\nfunction closeTeamManagementModal(', start);

test('terrain decorations and structures remain visible outside line of sight', () => {
    assert.ok(start >= 0 && end > start);
    let canSee = false;
    const tree = { visible: false, userData: { objType: 'tree', tileX: 2, tileZ: 2 } };
    const anomaly = { visible: true, userData: { objType: 'anomaly', tileX: 3, tileZ: 3 } };
    const wall = { visible: false, userData: { locationObject: { type: 'wall', x: 4, y: 4 } } };
    const item = { visible: true, userData: { locationObject: { type: 'ground_item', x: 5, y: 5 } } };
    const character = { model: { visible: true }, label: { visible: true } };
    for (const mesh of [tree, anomaly, wall, item, character.model]) mesh.position = {x:0,z:0};
    const context = vm.createContext({pendingFacingSelection:null,pendingCharacterPlacement:null,scoutedCharacters:new Set(),
        locationLevelVisible: () => true, decorDisplayLevel: () => 0,
        structureLevel: () => 0, structureBuildingId: () => null,
        scene: {}, currentLocationData: {}, locationProfiler: null,
        fogIsActive: () => true, loadFogMemory() {}, getFogVisionSources: () => [],
        fogMemory: new Map(), objectMeshes: [tree, anomaly], locationObjectMeshes: [wall, item],
        characterModels: new Map([[1, character]]),
        isTileVisibleToPlayer: () => canSee, isCharacterVisibleToPlayer: () => canSee,
        isPointVisibleToPlayer: () => canSee,
        rememberFogTile: () => ({ changed: false }), rememberFogContents: () => false,
        rememberVisibleCharacters: () => false, saveFogMemory() {},
        getObjectGridPosition: object => ({ x: object.x, y: object.y }),
        getTileHeight: () => 1, getFogObjectHeight: () => 1,
        syncFogMemoryGhosts() {},
    });
    vm.runInContext(source.slice(start, end), context);
    context.applyFogOfWar();
    assert.equal(tree.visible, true);
    assert.equal(wall.visible, true);
    assert.equal(anomaly.visible, false);
    assert.equal(item.visible, false);
    assert.equal(character.model.visible, false);

    canSee = true;
    context.applyFogOfWar();
    assert.equal(anomaly.visible, true);
    assert.equal(item.visible, true);
    assert.equal(character.model.visible, true);
});

test('old fog memory does not create duplicate ghosts for visible structures', () => {
    const ghostStart = source.indexOf('function syncFogMemoryGhosts(');
    const ghostEnd = source.indexOf('\nfunction getFogVisionSources(', ghostStart);
    assert.ok(ghostStart >= 0 && ghostEnd > ghostStart);
    const context = vm.createContext({
        fogMemory: new Map([['1:1', { structures: [{ id: 7, type: 'wall' }], decorations: ['tree'] }]]),
        fogMemoryGhosts: new Map(),
        characterModels: new Map(),
        scene: { add() {}, remove() {} },
        isTileVisibleToPlayer: () => false,
        fogMemoryDecorationTemplates: new Map(),
        getFogGhost() { throw new Error('Static map ghost must not be created'); },
        disposeObject() {},
    });
    vm.runInContext(source.slice(ghostStart, ghostEnd), context);
    context.syncFogMemoryGhosts(true, []);
    assert.equal(context.fogMemoryGhosts.size, 0);
});

test('a removed character leaves no ghost on a visible empty tile', () => {
    const ghostStart = source.indexOf('function syncFogMemoryGhosts(');
    const ghostEnd = source.indexOf('\nfunction getFogVisionSources(', ghostStart);
    const oldGhost = { visible: true };
    const ghosts = new Map([['character:42', oldGhost]]);
    const removed = [];
    const context = vm.createContext({
        fogMemory: new Map([['2:0', { characters: [{ characterId: 42, posX: 2, posY: 0 }] }]]),
        fogMemoryGhosts: ghosts,
        characterModels: new Map(),
        fogMemoryDecorationTemplates: new Map(),
        scene: { add() {}, remove(ghost) { removed.push(ghost); } },
        disposeObject() {},
        isTileVisibleToPlayer: () => true,
        getFogGhost() { throw new Error('A visible empty tile must not create a ghost'); },
    });
    vm.runInContext(source.slice(ghostStart, ghostEnd), context);
    context.syncFogMemoryGhosts(true, []);
    assert.equal(ghosts.size, 0);
    assert.deepEqual(removed, [oldGhost]);
});
