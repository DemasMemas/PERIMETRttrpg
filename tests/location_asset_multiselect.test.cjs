const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));

const scene = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
const main = fs.readFileSync(path.join(__dirname, '../app/static/js/main.js'), 'utf8');

function section(start, end) {
    const first = scene.indexOf(start);
    const last = scene.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `${start} should exist`);
    return scene.slice(first, last).replaceAll('export ', '');
}

test('both construction tools inherit the active building and level', () => {
    const fields = { 'loc-structure-building-id': { value: 'building-a' },
        'loc-structure-level': { value: '1' } };
    const placement = { '[data-placement-building-id]': { value: 'building-b' },
        '[data-placement-level]': { value: '0' } };
    const context = createSceneContext({ window: { locationEditorTool: 'structure' },
        document: { getElementById: id => fields[id], querySelector: selector => placement[selector] } });
    vm.runInContext(section('function getStructurePlacementProperties()', 'const FLOOR_SURFACE_OFFSET'), context);
    assert.deepEqual({ ...context.getStructurePlacementProperties() }, { building_id: 'building-a', level: 1 });
    context.window.locationEditorTool = 'assets';
    assert.deepEqual({ ...context.getStructurePlacementProperties() }, { building_id: 'building-b', level: 0 });
    assert.match(main, /const setActiveBuilding = \(id, level = activeLevel\)/);
    assert.match(main, /selection\.buildingId, selection\.level/);
});

test('building choices derive from saved objects and omit legacy IDs', () => {
    const context = createSceneContext({ currentLocationData: { objects: [
        { type: 'floor', properties: { building_id: 'a' } },
        { type: 'wall', properties: { building_id: 'a' } },
        { type: 'floor', properties: {} },
        { type: 'door', properties: { building_id: 'b' } },
    ] }, structureBuildingId: object => object.properties?.building_id || null });
    vm.runInContext(section('export function getLocationBuildingIds()', 'function notifyEditorBuildingsChanged()'), context);
    assert.deepEqual(Array.from(context.getLocationBuildingIds()), ['a', 'b']);
});

test('anchored single-axis resize preserves the opposite edge after rotation and JSON storage', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const { anchoredResizeCenter, bearingToObjectRotation } = await import(
        `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`);
    for (const bearing of [0, 37, 90, 180]) for (const axis of ['width', 'depth']) for (const side of [-1, 1]) {
        const oldSize = 3;
        const newSize = 4.5;
        const center = JSON.parse(JSON.stringify(anchoredResizeCenter(5, 7, bearing, axis, oldSize, newSize, side)));
        const angle = bearingToObjectRotation(bearing);
        const direction = axis === 'width'
            ? { x: Math.cos(angle), z: -Math.sin(angle) }
            : { x: Math.sin(angle), z: Math.cos(angle) };
        const oppositeBefore = { x: 5 - side * oldSize / 2 * direction.x,
            z: 7 - side * oldSize / 2 * direction.z };
        const oppositeAfter = { x: center.x - side * newSize / 2 * direction.x,
            z: center.z - side * newSize / 2 * direction.z };
        assert.ok(Math.abs(oppositeAfter.x - oppositeBefore.x) < 1e-8);
        assert.ok(Math.abs(oppositeAfter.z - oppositeBefore.z) < 1e-8);
    }
});

test('camera-relative Q/E rotation uses the same 15 degree yaw for previews and saved objects', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const { cameraYawStepDegrees, bearingToObjectRotation, objectRotationToBearing } = await import(
        `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`);
    for (const azimuth of [0, Math.PI / 4, Math.PI / 2, Math.PI]) {
        const matrix = { elements: [] };
        const camera = { matrixWorld: matrix, updateMatrixWorld() {} };
        matrix.elements[0] = Math.cos(azimuth);
        matrix.elements[2] = -Math.sin(azimuth);
        matrix.elements[4] = -Math.sin(azimuth) * 0.7;
        matrix.elements[6] = -Math.cos(azimuth) * 0.7;
        assert.equal(cameraYawStepDegrees(camera, 1), -15);
        assert.equal(cameraYawStepDegrees(camera, -1), 15);
        assert.ok(Math.abs(objectRotationToBearing(bearingToObjectRotation(15)) - 15) < 1e-8);
        matrix.elements[4] *= -1;
        matrix.elements[6] *= -1;
        assert.equal(cameraYawStepDegrees(camera, 1), 15);
    }
    const context = createSceneContext({
        window: { isGM: true, locationEditMode: true, locationEditorTool: 'assets' },
        selectedLocationAsset: { kind: 'structure' }, structureRotation: 0,
        brushObjectRotation: 0, buildMode: 'terrain', camera: null,
        cameraYawStepDegrees: (_, direction) => -direction * 15,
        editorRotateMode: false, editorMoveMode: false, editorResizeMode: false,
        editorRotateHotkeyPending: false,
        editorSelection: { kind: 'structure' },
        editorSelections: [], editorHotkeyRotationQueue: Promise.resolve(),
        setLocationStructureRotation(value) { context.structureRotation = value; },
        setLocationBrushObjectRotation(value) { context.brushObjectRotation = value; },
        syncLocationPlacementAngle() {}, updateBuildPreview() {},
    });
    vm.runInContext(section('export function rotateLocationPlacement(', 'export function addLocationObject('), context);
    assert.equal(context.rotateLocationPlacement(1), true);
    assert.ok(Math.abs(context.structureRotation + Math.PI / 12) < 1e-8);
    assert.equal(context.rotateLocationPlacement(-1), true);
    assert.equal(context.structureRotation, 0);
    context.selectedLocationAsset = { kind: 'decor' };
    assert.equal(context.rotateLocationPlacement(1), true);
    assert.equal(context.brushObjectRotation, -15);
    context.selectedLocationAsset = { kind: 'lamp', lightKind: 'spot', azimuth: 0 };
    assert.equal(context.rotateLocationPlacement(1), true);
    assert.equal(context.selectedLocationAsset.azimuth, 15);
    context.selectedLocationAsset = null;
    context.window.locationEditorTool = 'select';
    const selection = { kind: 'structure', id: 7 };
    context.editorSelections = [selection];
    let savedRotation = 0;
    const saves = [];
    context.sameEditorSelection = (a, b) => a.kind === b.kind && a.id === b.id;
    context.updateSelectedLocationAssetGroup = async transform => {
        await Promise.resolve();
        savedRotation = transform({ kind: 'structure', rotation: savedRotation }).rotation;
        saves.push(savedRotation);
    };
    assert.equal(context.rotateLocationPlacement(1), true);
    assert.equal(context.rotateLocationPlacement(1), true);
    await context.editorHotkeyRotationQueue;
    assert.deepEqual(saves, [15, 30]);
    assert.ok(Math.abs(bearingToObjectRotation(savedRotation) + Math.PI / 6) < 1e-8);
    context.editorMoveMode = true;
    assert.equal(context.rotateLocationPlacement(1), true);
    await context.editorHotkeyRotationQueue;
    assert.deepEqual(saves, [15, 30]);
    context.editorMoveMode = false;
    context.editorRotateMode = true;
    context.editorRotateAngle = savedRotation;
    assert.equal(context.rotateLocationPlacement(-1), true);
    assert.equal(context.editorRotateAngle, 15);
    assert.equal(context.editorRotateHotkeyPending, true);
    context.editorRotateMode = false;
    context.editorSelections = [];
    assert.equal(context.rotateLocationPlacement(1), false);
});

test('Esc cancels the active location transform before clearing selection', () => {
    const start = scene.indexOf('const onKeyDown = (e) => {', scene.indexOf('function setupLocationControls('));
    const end = scene.indexOf("document.addEventListener('keydown', onKeyDown)", start);
    assert.ok(start >= 0 && end > start);
    let clears = 0;
    let cancels = 0;
    let modalVisible = false;
    const context = createSceneContext({
        window: { locationEditMode: true, locationEditorTool: 'select' },
        document: { querySelectorAll: () => modalVisible ? [{ getClientRects: () => [1] }] : [] },
        isKeyboardInputTarget: target => Boolean(target?.typing),
        editorSelections: [{ kind: 'structure', id: 4 }],
        editorSelection: { kind: 'structure', id: 4 },
        editorResizeMode: true, editorMoveMode: false, editorRotateMode: false,
        cancelCurrentAssetResizeDrag() { cancels++; return true; },
        cancelLocationAssetResize() { cancels++; },
        clearEditorSelection() { clears++; context.editorSelections = []; },
    });
    vm.runInContext(`${scene.slice(start, end)}\nglobalThis.testOnKeyDown = onKeyDown;`, context);
    const pressEsc = target => context.testOnKeyDown({ key: 'Escape', code: 'Escape', target: target || {},
        preventDefault() {} });
    pressEsc({ typing: true });
    modalVisible = true;
    pressEsc();
    assert.equal(cancels, 0);
    modalVisible = false;
    pressEsc();
    assert.equal(cancels, 1);
    assert.equal(clears, 0);
    context.editorResizeMode = false;
    pressEsc();
    assert.equal(clears, 1);
});

test('rotate click commits a Q/E preview without recomputing the pointer angle', () => {
    assert.match(scene, /if \(!editorRotateHotkeyPending\) updateEditorRotatePreview\(e\.clientX, e\.clientY/);
    assert.match(scene, /if \(editorRotateMode && e\.target === canvas\) \{\s*editorRotateHotkeyPending = false;/);
});

test('selected structure hotkey angle survives the existing save and JSON load path', async () => {
    let saved;
    const source = { id: 7, type: 'wall', properties: { rotation: 0, level: 0 } };
    const context = createSceneContext({
        editorSelection: { kind: 'structure', id: 7 },
        currentLocationData: { grid_width: 8, grid_height: 8 },
        getSelectedEditorSource: () => source,
        window: { currentLobbyId: 19 },
        Server: { updateLocationObject: async (_lobby, _id, payload) => {
            saved = JSON.parse(JSON.stringify(payload));
            return { ...source, ...saved };
        } },
        structureLevel: () => 0,
        structureBuildingId: () => '',
        bearingToObjectRotation: bearing => -bearing * Math.PI / 180,
        clampAssetElevation: value => Math.max(0, Math.min(10, Number(value) || 0)),
        updateLocationObject() {}, notifyEditorSelection() {}, showNotification() {},
    });
    vm.runInContext(section('export async function updateSelectedLocationAssetDetails(',
        'export function previewSelectedLocationLamp('), context);
    assert.equal(await context.updateSelectedLocationAssetDetails({ x: 2.5, z: 3.5,
        rotation: 15, elevation: 0 }), true);
    assert.ok(Math.abs(saved.properties.rotation + Math.PI / 12) < 1e-8);
    const reloaded = JSON.parse(JSON.stringify(saved));
    assert.ok(Math.abs(-reloaded.properties.rotation * 180 / Math.PI - 15) < 1e-8);
});

test('upper-storey hover projects onto the active level while ground decor stays on terrain', () => {
    const planes = [];
    let projectedPoint = { x: 5.2, z: 4.7 };
    const context = createSceneContext({
        window: { locationEditMode: true, locationEditorTool: 'assets' },
        selectedLocationAsset: { kind: 'structure' }, buildMode: 'structure', editorMoveMode: false, locationViewLevel: 1,
        document: { querySelector: selector => ({ value: selector === '[data-placement-level]' ? '1' : 'building-a' }),
            getElementById: () => ({ value: '1' }) },
        structureLevelOffset: object => object.properties.level * 2.4,
        getTileHeight: () => 1, getPointerWorldPoint: (x, y, planeY) => {
            planes.push(planeY); return projectedPoint;
        },
        currentLocationData: { grid_width: 10, grid_height: 10 },
    });
    vm.runInContext(section('function getStructurePlacementProperties()', 'const FLOOR_SURFACE_OFFSET'), context);
    assert.equal(context.editorHoverLevelOffset(), 2.4);
    assert.deepEqual({ ...context.hoveredEditorTile(10, 20, { x: 1, z: 1 }) }, { x: 5, z: 4 });
    assert.ok(planes.length > 0 && planes.every(y => y === 3.4));
    projectedPoint = { x: 12, z: 4 };
    assert.equal(context.hoveredEditorTile(10, 20, { x: 1, z: 1 }), null);
    context.selectedLocationAsset = { kind: 'lamp' };
    assert.equal(context.editorHoverLevelOffset(), 2.4);
    context.selectedLocationAsset = { kind: 'decor' };
    assert.equal(context.editorHoverLevelOffset(), 2.4); // All assets follow the viewed floor.
    context.locationViewLevel = 0; projectedPoint = { x: 1.2, z: 1.3 };
    assert.equal(context.editorHoverLevelOffset(), 0);
    assert.deepEqual({ ...context.hoveredEditorTile(10, 20, { x: 1, z: 1 }) }, { x: 1, z: 1 });
});

test('move cursor uses the saved surface height for structures, decor and lights', () => {
    const planes = [];
    const source = { type: 'wall', properties: { level: 1, elevation: 0.2 } };
    const context = createSceneContext({
        editorSelection: { kind: 'structure' }, editorSelections: [{ kind: 'structure' }],
        getSelectedEditorSource: () => source,
        structureLevelOffset: object => object.properties.level * 2.4,
        clampAssetElevation: value => Number(value || 0),
        getTileHeight: x => x === 1 ? -2 : 0,
        getPointerWorldPoint: (_x, _y, planeY) => {
            planes.push(planeY);
            return { x: planeY < 1 ? 2.4 : 2.2, z: 1.6 };
        },
        currentLocationData: { grid_width: 10, grid_height: 10 },
    });
    vm.runInContext(section('function editorMovePointerPoint(', 'const FLOOR_SURFACE_OFFSET'), context);
    assert.deepEqual({ ...context.editorMovePointerPoint(10, 20, { x: 1, z: 1 }) }, { x: 2.2, z: 1.6 });
    assert.ok(Math.abs(planes[0] - 0.6) < 1e-8);
    assert.equal(planes[1], 2.6);
    context.editorSelection.kind = 'decor';
    source.elevation = 0.3;
    planes.length = 0;
    context.editorMovePointerPoint(10, 20, { x: 2, z: 1 });
    assert.ok(Math.abs(planes[0] - 2.7) < 1e-8); // Decor retains its saved storey as well as elevation.
    context.editorSelection.kind = 'lamp';
    source.height = 1.1;
    planes.length = 0;
    context.editorMovePointerPoint(10, 20, { x: 2, z: 1 });
    assert.equal(planes[0], 1.1);
});

test('moving a trimmed floor keeps its visible center on the cursor', () => {
    const source = { type: 'floor', properties: { level: 1, dimensions: { width: 3, depth: 3 } } };
    const context = createSceneContext({
        editorSelection: { kind: 'structure' }, editorSelections: [{ kind: 'structure' }],
        getSelectedEditorSource: () => source,
        structureLevelOffset: () => 2.4, clampAssetElevation: () => 0,
        getTileHeight: () => 0, getPointerWorldPoint: () => ({ x: 5.25, z: 6.75 }),
        currentLocationData: { grid_width: 10, grid_height: 10, objects: [] },
        floorVisualPose: object => ({ x: object.tile_x + 0.5 + object.properties.offset_x + 0.18,
            z: object.tile_y + 0.5 + object.properties.offset_z - 0.12 }),
    });
    vm.runInContext(section('function editorMovePointerPoint(', 'const FLOOR_SURFACE_OFFSET'), context);
    const center = context.editorMovePointerPoint(10, 20, { x: 5, z: 6 });
    assert.ok(Math.abs(center.x - 5.07) < 1e-8);
    assert.ok(Math.abs(center.z - 6.87) < 1e-8);
    assert.equal(center.cursorX, 5.25);
    assert.equal(center.cursorZ, 6.75);
    assert.match(scene, /\(editorMoveMode \|\| editorResizeMode\) && source\.type === 'floor' \? buildPreviewMesh\.position\.x/);
});

test('single move commits the preview coordinates and preserves the saved rotation', async () => {
    const saved = [];
    const selection = { x: 2, z: 3, rotation: 135, elevation: 0.4 };
    const context = createSceneContext({
        editorSelections: [{ kind: 'structure' }], editorMoveMode: true,
        editorMovePreviewPoint: { x: 5.2, z: 6.3 },
        getSelectedLocationEditorAsset: () => selection,
        getSelectedEditorSource: () => ({ height: 2.4 }),
        async updateSelectedLocationAssetDetails(details) { saved.push(details); return true; },
        updateBuildPreview() {},
    });
    vm.runInContext(section('async function commitEditorAssetMove(', 'async function placeSelectedLocationAsset('), context);
    await context.commitEditorAssetMove(context.editorMovePreviewPoint.x, context.editorMovePreviewPoint.z);
    assert.equal(saved[0].x, 5.2);
    assert.equal(saved[0].z, 6.3);
    assert.equal(saved[0].rotation, 135);
    assert.equal(saved[0].elevation, 0.4);
    assert.equal(context.editorMoveMode, false);
    context.editorSelections = [{ kind: 'decor' }];
    context.editorMoveMode = true;
    await context.commitEditorAssetMove(1.1, 2.2);
    assert.equal(saved[1].x, 1.1);
    assert.equal(saved[1].rotation, 135);
});

test('Volume Scale exposes all six face handles and captures a chosen axis', () => {
    assert.match(scene, /\['width', -1/);
    assert.match(scene, /\['width', 1/);
    assert.match(scene, /\['height', -1/);
    assert.match(scene, /\['height', 1/);
    assert.match(scene, /\['depth', -1/);
    assert.match(scene, /\['depth', 1/);
    assert.match(scene, /editorResizeHandleAxis = axis/);
    assert.match(scene, /editorResizeHandleSign = sign/);
    assert.match(scene, /canvas\.setPointerCapture\(e\.pointerId\)/);
    assert.match(scene, /updateEditorResizePreview\(e\.clientX, e\.clientY, e\.shiftKey, e\.ctrlKey\)/);
    assert.match(main, /Volume Scale: тяните маркер грани/);
    assert.match(main, /Ctrl: шаг 0,1 м \(пол: 1 м\)/);
});

test('Volume Scale anchors the opposite face after rotation and supports Shift, snap and limits', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const { volumeResize, bearingToObjectRotation, projectedAxisDrag } = await import(
        `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`);
    const dimensions = { width: 4, depth: 0.2, height: 2.4 };
    for (const rotation of [0, 45, 90]) for (const axis of ['width', 'depth']) for (const side of [-1, 1]) {
        const resized = volumeResize({ dimensions, center: { x: 4, z: 5 }, elevation: 0.4,
            rotation, axis, side, distance: 1.5 });
        const yaw = bearingToObjectRotation(rotation);
        const direction = axis === 'width'
            ? { x: Math.cos(yaw), z: -Math.sin(yaw) }
            : { x: Math.sin(yaw), z: Math.cos(yaw) };
        const oldFace = { x: 4 - side * dimensions[axis] / 2 * direction.x,
            z: 5 - side * dimensions[axis] / 2 * direction.z };
        const newFace = { x: resized.center.x - side * resized.dimensions[axis] / 2 * direction.x,
            z: resized.center.z - side * resized.dimensions[axis] / 2 * direction.z };
        assert.ok(Math.abs(oldFace.x - newFace.x) < 1e-8);
        assert.ok(Math.abs(oldFace.z - newFace.z) < 1e-8);
    }
    const symmetric = volumeResize({ dimensions, center: { x: 4, z: 5 }, elevation: 0,
        rotation: 45, axis: 'width', side: 1, distance: 0.55, symmetric: true });
    assert.equal(symmetric.dimensions.width, 5.1);
    assert.deepEqual(symmetric.center, { x: 4, z: 5 });
    const snapped = volumeResize({ dimensions, center: { x: 4, z: 5 }, elevation: 0,
        rotation: 0, axis: 'width', side: 1, distance: 0.37, snap: 0.1 });
    assert.equal(snapped.dimensions.width, 4.4);
    const minimum = volumeResize({ dimensions, center: { x: 4, z: 5 }, elevation: 0,
        rotation: 0, axis: 'depth', side: -1, distance: 5 });
    assert.equal(minimum.dimensions.depth, 0.01);
    assert.equal(projectedAxisDrag(10, 10, 50, 10, 40, 0), 1);
});

test('Volume Scale anchors vertical faces for walls and floors, then persists elevation', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const { volumeResize } = await import(`data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`);
    const dimensions = { width: 3, depth: 2, height: 2.4 };
    const args = { dimensions, center: { x: 5, z: 7 }, elevation: 1,
        rotation: 0, axis: 'height', distance: 0.5 };
    const wallBottom = volumeResize({ ...args, side: -1 });
    assert.equal(wallBottom.elevation + wallBottom.dimensions.height, args.elevation + dimensions.height);
    const wallTop = volumeResize({ ...args, side: 1 });
    assert.equal(wallTop.elevation, args.elevation);
    const floorTop = volumeResize({ ...args, side: 1, floor: true });
    assert.equal(floorTop.elevation - floorTop.dimensions.height, args.elevation - dimensions.height);
    const floorBottom = volumeResize({ ...args, side: -1, floor: true });
    assert.equal(floorBottom.elevation, args.elevation);
    const limited = volumeResize({ ...args, elevation: 0, side: -1, distance: -1 });
    assert.equal(limited.elevation, 0);
    assert.equal(limited.dimensions.height, dimensions.height);
    const secondFloor = volumeResize({ ...args, elevation: 0.2, side: 1, distance: 0.3 });
    assert.equal(secondFloor.elevation, 0.2);
    const result = volumeResize({ ...args, axis: 'width', side: 1, distance: 1 });
    let saved;
    let previewUpdates = 0;
    const context = createSceneContext({
        editorResizeMode: true, editorResizeAnchor: { volume: true, result },
        editorResizeDimensions: result.dimensions, editorResizeElevation: result.elevation,
        editorResizeBaseCenter: { x: 5, z: 7 },
        getSelectedLocationEditorAsset: () => ({ kind: 'structure', x: 5, z: 7, rotation: 0,
            elevation: 1, color: '#ffffff' }),
        async updateSelectedLocationAssetDetails(details) { saved = details; return true; },
        notifyEditorResize() {}, updateBuildPreview() { previewUpdates++; },
    });
    vm.runInContext(section('function editorResizeCenter(', 'async function commitEditorAssetRotation()'), context);
    await context.commitEditorAssetResize();
    assert.equal(saved.x, 5.5);
    assert.equal(saved.z, 7);
    assert.equal(saved.dimensions.width, 4);
    assert.equal(saved.elevation, 1);
    assert.equal(context.editorResizeMode, true);
    assert.equal(context.editorResizeAnchor, null);
    assert.equal(context.editorResizeBaseCenter.x, 5.5);
    assert.equal(previewUpdates, 1);
});

test('Volume Scale preview tracks the captured face and Esc discards unsaved dimensions', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const { volumeResize, projectedAxisDrag } = await import(
        `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`);
    const selection = { kind: 'structure', x: 5, z: 7, rotation: 45, elevation: 0.4 };
    let updates = 0;
    const context = createSceneContext({
        editorResizeMode: true, editorResizeDragPointerId: 3,
        editorResizeDimensions: { width: 3, depth: 0.2, height: 2.4 },
        editorResizeBaseDimensions: { width: 3, depth: 0.2, height: 2.4 },
        editorResizeElevation: 0.4, editorResizeSymmetric: false,
        editorResizeBaseCenter: { x: 5, z: 7 },
        editorResizeAnchor: { volume: true, axis: 'width', sign: 1, startX: 10, startY: 10,
            screenAxisX: 40, screenAxisY: 0,
            baseDimensions: { width: 3, depth: 0.2, height: 2.4 },
            baseCenter: { x: 5, z: 7 }, baseElevation: 0.4 },
        renderer: { domElement: { hasPointerCapture: () => false } },
        getSelectedLocationEditorAsset: () => selection,
        getSelectedEditorSource: () => ({ type: 'wall' }),
        volumeResize, projectedAxisDrag,
        notifyEditorResize() { updates++; }, updateBuildPreview() {},
    });
    vm.runInContext(section('function updateEditorResizePreview(', 'async function commitEditorAssetResize()'), context);
    context.updateEditorResizePreview(50, 10, false);
    assert.equal(context.editorResizeDimensions.width, 4);
    assert.ok(context.editorResizeCenter(selection).x > 5);
    context.updateEditorResizePreview(50, 10, true);
    assert.equal(context.editorResizeDimensions.width, 5);
    assert.equal(context.editorResizeCenter(selection).x, 5);
    assert.equal(updates, 2);
    vm.runInContext(section('export function cancelLocationAssetResize(', 'function clearEditorSelection()'), context);
    assert.equal(context.cancelLocationAssetResize(), true);
    assert.equal(context.editorResizeMode, false);
    assert.equal(context.editorResizeDimensions, null);
    assert.equal(context.editorResizeElevation, null);
});

test('switching R to Eraser cancels the drag, releases capture and leaves Esc available', () => {
    const released = [];
    let previews = 0;
    const context = createSceneContext({
        editorResizeMode: true, editorResizeDragPointerId: 9,
        editorResizeDimensions: { width: 4, depth: 0.2, height: 2.4 },
        editorResizeBaseDimensions: { width: 3, depth: 0.2, height: 2.4 },
        editorResizeAnchor: { volume: true, baseDimensions: { width: 3, depth: 0.2, height: 2.4 } },
        editorResizeElevation: 0.4, editorResizeSymmetric: true, editorResizeSnapped: true,
        renderer: { domElement: { hasPointerCapture: id => id === 9,
            releasePointerCapture: id => released.push(id) } },
        notifyEditorResize() {}, updateBuildPreview() { previews++; },
    });
    vm.runInContext(section('export function cancelLocationAssetResize(', 'function clearEditorSelection()'), context);
    assert.equal(context.cancelLocationAssetResize(false), true);
    assert.deepEqual(released, [9]);
    assert.equal(context.editorResizeMode, false);
    assert.equal(context.editorResizeDragPointerId, null);
    assert.equal(context.editorResizeSymmetric, false);
    assert.equal(previews, 0);
    assert.equal(context.cancelLocationAssetResize(false), false);
    const main = fs.readFileSync(path.join(__dirname, '../app/static/js/main.js'), 'utf8');
    assert.match(main, /const selectLocationTool = tool => \{[\s\S]*?cancelLocationAssetResize\(false\);[\s\S]*?window\.locationEditorTool = tool;/);
    assert.match(main, /setLocationEraserMode\(tool === 'erase'\)/);
    assert.match(scene, /if \(editorResizeMode\) \{\s*e\.preventDefault\(\);\s*if \(!cancelCurrentAssetResizeDrag\(\)\) cancelLocationAssetResize\(\);/);
});

test('canceling one Volume Scale drag restores its starting size without exiting the tool', () => {
    const released = [];
    const context = createSceneContext({
        editorResizeMode: true, editorResizeDragPointerId: 7,
        editorResizeAnchor: { volume: true, baseDimensions: { width: 3, depth: 0.2, height: 2.4 },
            baseCenter: { x: 5, z: 7 }, baseElevation: 0.4 },
        editorResizeDimensions: { width: 4, depth: 0.2, height: 2.4 },
        editorResizeBaseDimensions: { width: 3, depth: 0.2, height: 2.4 },
        editorResizeBaseCenter: { x: 5.5, z: 7 }, editorResizeElevation: 0.4,
        editorResizeSymmetric: true, editorResizeSnapped: true,
        renderer: { domElement: { hasPointerCapture: () => true,
            releasePointerCapture: id => released.push(id) } },
        notifyEditorResize() {}, updateBuildPreview() {},
    });
    vm.runInContext(section('function cancelCurrentAssetResizeDrag()', 'function clearEditorSelection()'), context);
    assert.equal(context.cancelCurrentAssetResizeDrag(), true);
    assert.equal(context.editorResizeMode, true);
    assert.equal(context.editorResizeDimensions.width, 3);
    assert.equal(context.editorResizeBaseCenter.x, 5);
    assert.equal(context.editorResizeAnchor, null);
    assert.equal(context.editorResizeDragPointerId, null);
    assert.equal(context.editorResizeSymmetric, false);
    assert.equal(context.editorResizeSnapped, false);
    assert.deepEqual(released, [7]);
    assert.equal(context.cancelCurrentAssetResizeDrag(), false);
});

test('door and window previews draw their model and opening outline over an opaque wall', () => {
    const material = { depthTest: true };
    const model = { isMesh: true, material, renderOrder: 0 };
    const children = [];
    let boxDimensions;
    let boxDisposed = false;
    const context = createSceneContext({ THREE: {
        BoxGeometry: class { constructor(...size) { boxDimensions = size; } dispose() { boxDisposed = true; } },
        EdgesGeometry: class {},
        LineBasicMaterial: class { constructor(options) { Object.assign(this, options); } },
        LineSegments: class { constructor(geometry, lineMaterial) {
            this.geometry = geometry; this.material = lineMaterial;
            this.position = { y: 0 };
        } },
    } });
    vm.runInContext(section('function revealOpeningPlacementPreview(', 'function createEditorGroupMovePreview('), context);
    context.revealOpeningPlacementPreview({ traverse(fn) { fn(model); }, add(child) { children.push(child); } },
        { width: 0.9, height: 2, depth: 0.18 }, true);
    assert.equal(material.depthTest, false);
    assert.equal(model.renderOrder, 90);
    assert.equal(children.length, 1);
    assert.deepEqual(boxDimensions, [0.9, 2, 0.26]);
    assert.equal(children[0].position.y, 1);
    assert.equal(children[0].material.depthTest, false);
    assert.equal(children[0].material.depthWrite, false);
    assert.equal(boxDisposed, true);
    const unmatched = [];
    context.revealOpeningPlacementPreview({ traverse(fn) { fn(model); }, add(child) { unmatched.push(child); } },
        { width: 0.9, height: 2, depth: 0.18 }, false);
    assert.equal(unmatched.length, 0);
    assert.match(scene, /\['door', 'window'\]\.includes\(placementForLocationAsset\(source\)\.type\)/);
    assert.match(scene, /const movableStructurePreview = [\s\S]*?&& !openingPlacementPreview/);
    assert.match(scene, /doorOnWall\(object, wall\)/);
});

test('shift-click adds and removes objects without discarding the rest', () => {
    const selections = [{ kind: 'structure', id: 1 }];
    const context = createSceneContext({
        editorSelection: selections[0], editorSelections: selections,
        pickEditorAsset: x => ({ kind: 'structure', id: x }),
        applyLocationLighting() {}, notifyEditorResize() {},
        updateEditorSelectionVisual() {}, notifyEditorSelection() {},
    });
    vm.runInContext(section('function sameEditorSelection(', 'const editorHistory ='), context);
    assert.equal(context.selectEditorAsset(2, 0, true), true);
    assert.deepEqual(context.editorSelections.map(item => item.id), [1, 2]);
    assert.equal(context.selectEditorAsset(2, 0, true), true);
    assert.deepEqual(context.editorSelections.map(item => item.id), [1]);
    assert.equal(context.selectEditorAsset(3, 0, false), true);
    assert.deepEqual(Array.from(context.editorSelections, item => item.id), [3]);
});

test('stationary right click clears selection with any tool before canceling placement', () => {
    const calls = [];
    const window = {
        isGM: true, locationEditMode: true, locationEditorTool: 'assets',
        cancelLocationAssetPlacement() { calls.push('placement'); },
    };
    const context = createSceneContext({
        window, selectedLocationAsset: { id: 'tree' },
        editorSelections: [{ kind: 'structure', id: 1 }],
        clearLocationAssetPreset() { calls.push('preset fallback'); },
        clearEditorSelection() { calls.push('selection'); context.editorSelections = []; },
        updateBuildPreview() { calls.push('preview'); },
    });
    vm.runInContext(section('function cancelLocationEditorContextAction(',
        'function updateEditorSelectionVisual('), context);
    assert.equal(context.cancelLocationEditorContextAction(), true);
    assert.deepEqual(calls, ['selection', 'preview']);
    assert.equal(context.cancelLocationEditorContextAction(), true);
    assert.deepEqual(calls, ['selection', 'preview', 'placement']);
    window.locationEditorTool = 'erase';
    context.editorSelections = [{ kind: 'structure', id: 2 }];
    assert.equal(context.cancelLocationEditorContextAction(), true);
    assert.deepEqual(calls, ['selection', 'preview', 'placement', 'selection', 'preview']);
    assert.equal(context.cancelLocationEditorContextAction(), false);
    window.locationEditorTool = 'light';
    context.editorSelections = [{ kind: 'lamp', index: 0 }];
    assert.equal(context.cancelLocationEditorContextAction(), true);
    window.locationEditMode = false;
    context.editorSelections = [{ kind: 'structure', id: 1 }];
    assert.equal(context.cancelLocationEditorContextAction(), false);
    const contextMenu=scene.slice(scene.indexOf('const onContextMenu = (e) => {',scene.indexOf('function setupLocationControls(')));
    assert.ok(contextMenu.indexOf('if (cancelLocationEditorContextAction()) return;')<contextMenu.indexOf('const obj = getCharacterAtScreen'));
    assert.match(scene, /if \(cancelLocationEditorContextAction\(\)\) return;/);
    assert.match(main, /window\.cancelLocationAssetPlacement = \(\) => \{\s*activeAsset = null;\s*clearLocationAssetPreset\(\);\s*renderAssetCards\(\);\s*renderPlacementSettings\(\);/);
});

test('canceling placement clears the structure ghost and does not recreate a cube on hover', () => {
    let clears = 0;
    let adds = 0;
    const context = createSceneContext({
        window: { locationEditMode: true, locationEditorTool: 'assets' },
        selectedLocationAsset: { id: 'crate', kind: 'structure' },
        buildMode: 'structure', brushObjectMode: true,
        houseDrawMode: false,
        buildPreviewMesh: null, buildPreviewKey: null,
        lampEditTrace: null,
        scene: { add() { adds++; } }, hoveredTileCoords: { x: 2, z: 3 },
        editorRotateMode: false, editorMoveMode: false, editorResizeMode: false,
        editorSelections: [], editorMovePreviewPoint: null,
        editorRotateAngle: 0, editorResizeAxis: 'all', editorResizeDimensions: null,
        editorResizeElevation: null, editorResizeAnchor: null,
        editorResizeSymmetric: false, brushObjectRotation: 0,
        structureWidth: 1, structureDepth: 1, structureHeight: 1,
        structureColor: '#ffffff', structureRotation: 0,
        getSelectedEditorSource: () => null,
        getStructurePlacementProperties: () => ({ building_id: 'test-building', level: 0 }),
        clearBuildPreview() { clears++; },
    });
    vm.runInContext(section('export function clearLocationAssetPreset()',
        'export function beginMoveSelectedLocationAsset()'), context);
    context.clearLocationAssetPreset();
    assert.equal(context.selectedLocationAsset, null);
    assert.equal(context.buildMode, 'terrain');
    assert.equal(context.brushObjectMode, false);
    assert.equal(clears, 1);

    // Even a stale build mode must not resurrect the generic structure preview.
    context.buildMode = 'structure';
    vm.runInContext(section('function updateBuildPreview()', 'async function placeStructureAtTile('), context);
    context.updateBuildPreview();
    assert.equal(adds, 0);
    assert.equal(clears, 2);
});

test('group move preserves two decor objects originally sharing a tile', async () => {
    const tiles = Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => ({ objects: [] })));
    const first = { type: 'anomaly', x: 0, z: 0, scale: 1.5 };
    const second = { type: 'rock', x: 0.1, z: 0 };
    tiles[1][1].objects.push(first, second);
    const selections = [0, 1].map(index => ({ kind: 'decor', x: 1, z: 1, index }));
    const savedDetails = [];
    let context;
    context = createSceneContext({
        editorSelection: selections[1], editorSelections: selections,
        editorMoveMode: true, editorMovePreviewPoint: null,
        editorSelectionRing: { position: { set() {} } },
        currentLocationData: { tiles_data: tiles, grid_width: 5, grid_height: 5 },
        getEditorSource: selection => tiles[selection.z][selection.x].objects[selection.index],
        getSelectedEditorSource() { return tiles[context.editorSelection.z][context.editorSelection.x].objects[context.editorSelection.index]; },
        getSelectedLocationEditorAsset() {
            const selection = context.editorSelection;
            const source = tiles[selection.z][selection.x].objects[selection.index];
            return { kind: 'decor', x: selection.x + 0.5 + source.x, z: selection.z + 0.5 + source.z,
                rotation: 0, elevation: 0, dimensions: { width: 1, depth: 1, height: 1 }, resizable: true };
        },
        async updateSelectedLocationAssetDetails(details) {
            savedDetails.push(details);
            const selection = context.editorSelection;
            const source = tiles[selection.z][selection.x].objects.splice(selection.index, 1)[0];
            const nextX = Math.floor(details.x);
            const nextZ = Math.floor(details.z);
            source.x = details.x - nextX - 0.5;
            source.z = details.z - nextZ - 0.5;
            tiles[nextZ][nextX].objects.push(source);
            context.editorSelection = { kind: 'decor', x: nextX, z: nextZ, index: tiles[nextZ][nextX].objects.length - 1 };
            return true;
        },
        updateEditorSelectionVisual() {}, notifyEditorSelection() {}, updateBuildPreview() {},
        showNotification(message) { throw new Error(message); },
    });
    vm.runInContext(section('async function updateSelectedLocationAssetGroup(', 'export async function updateSelectedLocationAssetDetails('), context);
    vm.runInContext(section('async function commitEditorAssetMove(', 'async function placeSelectedLocationAsset('), context);
    await context.commitEditorAssetMove(3.6, 3.5);
    assert.equal(tiles[1][1].objects.length, 0);
    assert.deepEqual(tiles[3][3].objects, [first, second]);
    assert.equal(first.x, 0);
    assert.ok(Math.abs(second.x - 0.1) < 0.00001);
    assert.equal(first.scale, 1.5);
    assert.ok(savedDetails.every(details => !Object.hasOwn(details, 'dimensions')));
});

test('anomaly accepts dimensions and keeps them on its tile', async () => {
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => ({ objects: [] })));
    const anomaly = { type: 'anomaly', anomalyKey: 'test', scale: 1.5, rotation: 0 };
    tiles[1][1].objects.push(anomaly);
    let selection = { kind: 'decor', x: 1, z: 1, index: 0 };
    let sent = 0;
    const context = createSceneContext({
        editorSelection: selection, editorSelections: [selection],
        currentLocationData: { grid_width: 3, grid_height: 3, tiles_data: tiles },
        getSelectedEditorSource: () => anomaly,
        validAssetDimensions: value => value.width > 0 && value.depth > 0 && value.height > 0,
        clampAssetElevation: value => Number(value) || 0,
        sendDecorTileUpdates() { sent++; },
        showNotification(message) { throw new Error(message); },
    });
    vm.runInContext(section('export async function updateSelectedLocationAssetDetails(',
        'export function previewSelectedLocationLamp('), context);
    const dimensions = { width: 2, depth: 3, height: 4 };
    assert.equal(await context.updateSelectedLocationAssetDetails({
        x: 1.5, z: 1.5, rotation: 0, dimensions,
    }), true);
    assert.deepEqual({ ...anomaly.dimensions }, dimensions);
    assert.equal(anomaly.scale, 1);
    assert.equal(sent, 1);
    assert.equal(tiles[1][1].objects[0], anomaly);

    const source = scene.slice(scene.indexOf('function createEditorGroupMovePreview('),
        scene.indexOf('function updateBuildPreview()'));
    assert.match(source, /applyDecorSize\(mesh, source, type\)/);
    assert.match(scene, /applyDecorSize\(buildPreviewMesh, decorPreview, decorType\)/);
    assert.match(scene, /applyDecorSize\(mesh, obj, obj\.type\)/);
    assert.match(scene, /resizable: true,\s*\};\s*\}\s*export function getSelectedLocationEditorAsset/);
});

test('anomaly visual size supports legacy scale and independent dimensions', () => {
    const dimensionSource = fs.readFileSync(path.join(__dirname,
        '../app/static/js/locationAssetDimensions.js'), 'utf8');
    const context = createSceneContext({
        findLocationAsset: () => null,
        baseAssetDimensions: () => ({ width: 1, depth: 1, height: 1 }),
    });
    vm.runInContext(dimensionSource.replace(/^import[^\n]*\n/, '').replaceAll('export ', ''), context);
    vm.runInContext(section('function applyDecorSize(', 'function createEditorLampPreview('), context);
    const mesh = { scale: { set(width, height, depth) { this.width = width; this.height = height; this.depth = depth; } } };
    context.applyDecorSize(mesh, { type: 'anomaly', scale: 1.5 }, 'anomaly');
    assert.deepEqual([mesh.scale.width, mesh.scale.height, mesh.scale.depth], [1.5, 1.5, 1.5]);
    context.applyDecorSize(mesh, { type: 'anomaly', dimensions: { width: 2, depth: 3, height: 4 } }, 'anomaly');
    assert.deepEqual([mesh.scale.width, mesh.scale.height, mesh.scale.depth], [2, 4, 3]);
});

test('hide toggles mixed structure, decor and lamp selections together', async () => {
    const structure = { id: 7, properties: {} };
    const decor = { type: 'rock' };
    const lamp = { kind: 'point', x: 2.5, z: 2.5 };
    const selections = [
        { kind: 'structure', id: 7 },
        { kind: 'decor', x: 1, z: 1, index: 0 },
        { kind: 'lamp', index: 0 },
    ];
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => ({ objects: [] })));
    tiles[1][1].objects.push(decor);
    const location = { lighting: { local_lights: [lamp] }, tiles_data: tiles };
    const context = createSceneContext({
        editorSelections: selections, currentLocationData: location,
        getEditorSource(selection) {
            if (selection.kind === 'structure') return structure;
            if (selection.kind === 'lamp') return location.lighting.local_lights[selection.index];
            return tiles[selection.z][selection.x].objects[selection.index];
        },
        window: { currentLobbyId: 1 },
        Server: { async updateLocationObject(_lobbyId, _id, changes) {
            return { ...structure, properties: { ...structure.properties, ...changes.properties } };
        } },
        updateLocationObject(updated) { structure.properties = updated.properties; },
        async persistLocationLights(lights) { location.lighting.local_lights = lights; return true; },
        sendDecorTileUpdates() {}, applyFogOfWar() {}, notifyEditorSelection() {},
        showNotification(message) { throw new Error(message); },
    });
    vm.runInContext(section('export async function toggleSelectedLocationAssetsHidden(', 'async function updateSelectedLocationAssetGroup('), context);
    await context.toggleSelectedLocationAssetsHidden();
    assert.equal(structure.properties.gm_hidden, true);
    assert.equal(decor.gm_hidden, true);
    assert.equal(location.lighting.local_lights[0].gm_hidden, true);
    await context.toggleSelectedLocationAssetsHidden();
    assert.equal(structure.properties.gm_hidden, false);
    assert.equal(decor.gm_hidden, false);
    assert.equal(location.lighting.local_lights[0].gm_hidden, false);
});

test('group move creates a separate ghost for every selected asset type', () => {
    const selections = [
        { kind: 'structure', id: 1 },
        { kind: 'decor', x: 2, z: 3, index: 0 },
        { kind: 'lamp', index: 0 },
    ];
    const sources = [
        { type: 'wall', tile_x: 1, tile_y: 1 },
        { type: 'rock', x: 0.1, z: 0.2, elevation: 0 },
        { kind: 'point', x: 4.5, z: 2.5, height: 2 },
    ];
    const makeMesh = () => ({
        position: { x: 0, y: 0, z: 0, set(x, y, z) { Object.assign(this, { x, y, z }); } },
        rotation: { y: 0 }, userData: {},
    });
    const lamps = [];
    const context = createSceneContext({
        editorSelections: selections,
        getEditorSource: selection => sources[selections.indexOf(selection)],
        THREE: {
            Group: class { constructor() { this.children = []; } add(mesh) { this.children.push(mesh); } },
            MathUtils: { degToRad: angle => angle * Math.PI / 180 },
        },
        createLocationObjectMesh() {
            const mesh = makeMesh();
            mesh.position.set(1.5, 1, 1.5);
            return mesh;
        },
        createEditorDecorPreview: () => makeMesh(),
        createEditorLampPreview(source, includeLight) {
            lamps.push({ source, includeLight });
            return makeMesh();
        },
        applyDecorSize() {}, getTileHeight: () => 1,
        clampAssetElevation: value => Number(value) || 0,
        fadeEditorPreview: group => group,
    });
    vm.runInContext(section('function createEditorGroupMovePreview(', 'function updateBuildPreview()'), context);
    const group = context.createEditorGroupMovePreview();
    assert.equal(group.children.length, 3);
    assert.deepEqual(Array.from(group.children, mesh => mesh.position.x), [1.5, 2.6, 4.5]);
    assert.equal(lamps[0].includeLight, false);
});

test('group ghost geometry is reused while cursor moves', () => {
    let context;
    let builds = 0;
    let adds = 0;
    const ghost = {
        position: { x: 0, z: 0, set(x, _y, z) { this.x = x; this.z = z; } },
        children: [{
            position: { y: 1 },
            userData: { editorMoveOrigin: { x: 1.5, z: 1.5, groundOffset: 0.25 } },
        }],
    };
    context = createSceneContext({
        window: { locationEditMode: true, locationEditorTool: 'select' },
        scene: { add() { adds++; } }, hoveredTileCoords: { x: 2, z: 2 },
        editorRotateMode: false, editorMoveMode: true,
        houseDrawMode: false,
        editorSelections: [{ kind: 'decor', x: 1, z: 1, index: 0 }, { kind: 'lamp', index: 0 }],
        editorMovePreviewPoint: { x: 2.5, z: 2.5 },
        buildPreviewMesh: null, buildPreviewKey: null,
        currentLocationData: { grid_width: 10, grid_height: 10 },
        clearBuildPreview() { context.buildPreviewMesh = null; context.buildPreviewKey = null; },
        createEditorGroupMovePreview() { builds++; return ghost; },
        getSelectedLocationEditorAsset: () => ({ x: 1.5, z: 1.5 }),
        getTileHeight: (x, z) => x + z,
    });
    vm.runInContext(section('function updateBuildPreview()', 'async function placeStructureAtTile('), context);
    context.updateBuildPreview();
    context.editorMovePreviewPoint = { x: 3.5, z: 2.5 };
    context.updateBuildPreview();
    assert.equal(builds, 1);
    assert.equal(adds, 1);
    assert.equal(ghost.position.x, 2);
    assert.equal(ghost.children[0].position.y, 5.25);
});

test('group rotation uses a common pivot and keeps directional rotations', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const url = `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`;
    const { rotatePointAround } = await import(url);
    const rotated = rotatePointAround(3, 2, 2, 2, 90);
    assert.ok(Math.abs(rotated.x - 2) < 0.00001);
    assert.ok(Math.abs(rotated.z - 3) < 0.00001);
    const assets = [
        { kind: 'structure', x: 3, z: 2, rotation: 10 },
        { kind: 'decor', x: 2, z: 3, rotation: 350 },
        { kind: 'lamp', lightKind: 'point', x: 1, z: 2, rotation: 0 },
        { kind: 'lamp', lightKind: 'spot', x: 2, z: 1, rotation: 45 },
    ];
    let saved;
    const context = createSceneContext({
        editorGroupRotatePivot: { x: 2, z: 2 }, editorRotateAngle: 90,
        editorRotateMode: true, editorGroupRotateStartBearing: 0, editorGroupRotateWithPivot: true,
        currentLocationData: { grid_width: 10, grid_height: 10 },
        rotatePointAround,
        async updateSelectedLocationAssetGroup(transform) {
            saved = assets.map(transform);
            return saved.every(Boolean);
        },
        updateBuildPreview() {},
    });
    vm.runInContext(section('async function commitEditorAssetRotation()', 'async function commitEditorAssetMove('), context);
    await context.commitEditorAssetRotation();
    assert.ok(Math.abs(saved[0].x - 2) < 0.00001);
    assert.ok(Math.abs(saved[0].z - 3) < 0.00001);
    assert.deepEqual(Array.from(saved, item => item.rotation), [100, 80, 0, 135]);
    assert.equal(context.editorRotateMode, false);
    assert.equal(context.editorGroupRotatePivot, null);
});

test('group rotation refuses to move any object outside the map', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const url = `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`;
    const { rotatePointAround } = await import(url);
    const context = createSceneContext({
        editorGroupRotatePivot: { x: 0.5, z: 0.5 }, editorRotateAngle: 180,
        editorRotateMode: true, editorGroupRotateWithPivot: true,
        currentLocationData: { grid_width: 10, grid_height: 10 },
        rotatePointAround,
        async updateSelectedLocationAssetGroup(transform) {
            return Boolean(transform({ kind: 'decor', x: 1.5, z: 0.5, rotation: 0 }));
        },
        updateBuildPreview() {},
    });
    vm.runInContext(section('async function commitEditorAssetRotation()', 'async function commitEditorAssetMove('), context);
    await context.commitEditorAssetRotation();
    assert.equal(context.editorRotateMode, true);
});

test('group rotation without Ctrl turns every object in place', () => {
    const assets = [
        { kind: 'structure', x: 3, z: 2, rotation: 10 },
        { kind: 'decor', x: 2, z: 3, rotation: 350 },
        { kind: 'lamp', lightKind: 'point', x: 1, z: 2, rotation: 0 },
        { kind: 'lamp', lightKind: 'spot', x: 2, z: 1, rotation: 45 },
    ];
    let saved;
    const context = createSceneContext({
        editorGroupRotatePivot: { x: 2, z: 2 }, editorRotateAngle: 90,
        editorGroupRotateWithPivot: false, editorRotateMode: true,
        currentLocationData: { grid_width: 10, grid_height: 10 },
        rotatePointAround() { throw new Error('positions must not rotate without Ctrl'); },
        async updateSelectedLocationAssetGroup(transform) {
            saved = assets.map(transform);
            return saved.every(Boolean);
        },
        updateBuildPreview() {},
    });
    vm.runInContext(section('async function commitEditorAssetRotation()', 'async function commitEditorAssetMove('), context);
    return context.commitEditorAssetRotation().then(() => {
        assert.deepEqual(Array.from(saved, item => [item.x, item.z]), assets.map(item => [item.x, item.z]));
        assert.deepEqual(Array.from(saved, item => item.rotation), [100, 80, 0, 135]);
        assert.equal(context.editorRotateMode, false);
    });
});

test('group rotation preview includes every ghost even when the last selection is a lamp', async () => {
    const transforms = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetTransforms.js'), 'utf8');
    const url = `data:text/javascript;base64,${Buffer.from(transforms).toString('base64')}`;
    const { rotatePointAround, bearingToObjectRotation } = await import(url);
    const makePosition = () => ({ x: 0, y: 0, z: 0, set(x, y, z) { Object.assign(this, { x, y, z }); } });
    const first = { position: makePosition(), rotation: { y: 0 },
        userData: { editorMoveOrigin: { x: 3, z: 2, groundOffset: 0.25, rotation: 0 } } };
    const second = { position: makePosition(), rotation: { y: 0 },
        userData: { editorMoveOrigin: { x: 1, z: 2, groundOffset: 2, rotation: 0 } } };
    const ghost = { children: [first, second], add(child) { this.children.push(child); } };
    let context;
    let builds = 0;
    context = createSceneContext({
        editorRotateMode: true, editorSelection: { kind: 'lamp', index: 0 },
        editorSelections: [{ kind: 'structure', id: 1 }, { kind: 'lamp', index: 0 }],
        editorGroupRotatePivot: { x: 2, z: 2 }, editorRotateAngle: 90, editorGroupRotateWithPivot: true,
        editorMoveMode: false, hoveredTileCoords: { x: 2, z: 2 },
        houseDrawMode: false,
        window: { locationEditMode: true, locationEditorTool: 'select' },
        scene: { add() {} }, currentLocationData: { grid_width: 10, grid_height: 10 },
        buildPreviewMesh: null, buildPreviewKey: null,
        clearBuildPreview() { context.buildPreviewMesh = null; context.buildPreviewKey = null; },
        createEditorGroupMovePreview() { builds++; return ghost; },
        THREE: {
            DoubleSide: 2,
            RingGeometry: class {}, MeshBasicMaterial: class {},
            Mesh: class { constructor() { this.position = makePosition(); this.rotation = { x: 0 }; this.userData = {}; } },
        },
        getTileHeight: (x, z) => x + z,
        rotatePointAround, bearingToObjectRotation,
    });
    vm.runInContext(section('function updateBuildPreview()', 'async function placeStructureAtTile('), context);
    context.updateBuildPreview();
    assert.equal(builds, 1);
    assert.ok(Math.abs(first.position.x - 2) < 0.00001);
    assert.ok(Math.abs(first.position.z - 3) < 0.00001);
    assert.ok(Math.abs(second.position.x - 2) < 0.00001);
    assert.ok(Math.abs(second.position.z - 1) < 0.00001);
    assert.equal(first.position.y, 5.25);
    assert.equal(first.rotation.y, -Math.PI / 2);
    assert.equal(ghost.children.length, 3);
    context.editorRotateAngle = 180;
    context.updateBuildPreview();
    assert.equal(builds, 1);
    context.editorGroupRotateWithPivot = false;
    context.updateBuildPreview();
    assert.deepEqual([first.position.x, first.position.z], [3, 2]);
    assert.deepEqual([second.position.x, second.position.z], [1, 2]);
    assert.equal(first.rotation.y, -Math.PI);
    context.editorGroupRotateWithPivot = true;
    context.updateBuildPreview();
    assert.equal(builds, 1);
    assert.ok(Math.abs(first.position.x - 1) < 0.00001);
    assert.ok(Math.abs(first.position.z - 2) < 0.00001);
});

test('group rotation starts without a jump and snaps the delta with Shift', () => {
    let point = { x: 3, z: 2 };
    let previews = 0;
    const context = createSceneContext({
        editorRotateMode: true, editorSelection: { kind: 'structure', id: 1 },
        editorGroupRotatePivot: { x: 2, z: 2 },
        editorGroupRotateStartBearing: null, editorGroupRotateWithPivot: false, editorRotateAngle: 0,
        getPointerWorldPoint: () => point,
        getTileHeight: () => 1,
        bearingFromDelta: (x, z) => Math.round(Math.atan2(z, x) * 180 / Math.PI),
        updateBuildPreview() { previews++; },
    });
    vm.runInContext(section('function updateEditorRotatePreview(', 'function updateEditorResizePreview('), context);
    context.updateEditorRotatePreview(0, 0);
    assert.equal(context.editorRotateAngle, 0);
    assert.equal(previews, 0);
    point = { x: 1.8, z: 3 };
    context.updateEditorRotatePreview(0, 0, true);
    assert.equal(context.editorRotateAngle, 105);
    assert.equal(previews, 1);
    context.updateEditorRotatePreview(0, 0, true, true);
    assert.equal(context.editorRotateAngle, 105);
    assert.equal(context.editorGroupRotateWithPivot, true);
    assert.equal(previews, 2);
    context.updateEditorRotatePreview(0, 0, true, false);
    assert.equal(previews, 3);
    assert.equal(context.editorGroupRotateWithPivot, false);
    assert.match(scene, /updateEditorRotatePreview\(e\.clientX, e\.clientY, e\.shiftKey, e\.ctrlKey\)/);
});

test('GM controls and visual-only lamp fixtures remain wired', () => {
    assert.match(main, /data-asset-action="hide"/);
    assert.match(main, /toggleSelectedLocationAssetsHidden\(\)/);
    assert.match(scene, /bulb\.visible = Boolean\(window\.isGM && \(editMode \|\| source\.gm_hidden\)\)/);
    assert.match(scene, /picker\.visible = Boolean\(window\.isGM && editMode\)/);
    assert.match(scene, /if \(e\.code === 'KeyH'\) toggleSelectedLocationAssetsHidden\(\)/);
    assert.match(scene, /else if \(e\.code === 'Delete'\) deleteSelectedLocationAsset\(\)/);
    assert.match(scene, /const scaleUp = [^;]*e\.code === 'Equal'[^;]*e\.key === '=';/);
});
