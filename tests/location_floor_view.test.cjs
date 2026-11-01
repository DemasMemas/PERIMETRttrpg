const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');

test('cutaway keeps basement boundary walls and renders below surface openings without exposing labels', () => {
    const context=createSceneContext({locationViewLevel:-2,
        currentLocationData:{objects:[]},structureLevel:o=>o.properties?.level||0,
        structureBuildingId:o=>o.properties?.building_id||null,
        getLocationBuildingViews:()=>new Map(),locationBuildingAt:()=>null});
    vm.runInContext(functionSource('locationLevelVisible','lampDisplayLevel'),context);
    assert.equal(context.locationLevelVisible(-1,null,0,0,{type:'wall',properties:{exterior_wall:true}}),true);
    assert.equal(context.locationLevelVisible(-1,null,0,0,{type:'floor'}),false);
    assert.equal(context.locationLevelVisible(-1,null,0,0,{type:'wall'}),false);
    assert.equal(context.locationLevelVisible(-2,null,0,0,{type:'floor'}),true);
    context.locationViewLevel=0;
    assert.equal(context.locationLevelVisible(-1,null,0,0,{type:'wall',properties:{exterior_wall:true}}),false);
    context.currentLocationData.objects.push({type:'floor_opening',properties:{building_id:'house',level:0}});
    assert.equal(context.locationLevelVisible(-1,'house',0,0,{type:'floor'}),true);
    assert.equal(context.locationLevelVisible(-2,'house',0,0,{type:'wall'}),true);
    assert.equal(context.locationLevelVisible(-1,'neighbor',0,0,{type:'floor'}),false);
    assert.equal(context.locationLevelVisible(-1,'house',0,0),false,'basement labels stay hidden on the surface');
});

test('terrain occludes basement interaction rays except through the rendered cutaway', () => {
    const context=createSceneContext({locationTerrainCutawayFootprints:()=>[],cutawayContains:rects=>rects.length>0});
    vm.runInContext(functionSource('locationHitOccludedByTerrain','updateStoreyTerrainVisibility'),context);
    const terrain={distance:4,point:{x:2,z:3}},basement={distance:8};
    assert.equal(context.locationHitOccludedByTerrain(basement,terrain),true);
    assert.equal(context.locationHitOccludedByTerrain({distance:2},terrain),false);
    context.locationTerrainCutawayFootprints=()=>[{}];
    assert.equal(context.locationHitOccludedByTerrain(basement,terrain),false);
});

test('picking rejects other floors, hidden ancestors, and fully transparent materials', () => {
    const context=createSceneContext({scene:{},locationViewLevel:-2,
        structureLevel:o=>o.properties?.level||0,decorDisplayLevel:o=>o.level||0,
        lampDisplayLevel:o=>o.level||0,currentLocationData:{lighting:{local_lights:[{level:-1}]}}});
    vm.runInContext(functionSource('visibleEditorHit','projectedEditorLamp'),context);
    const root={visible:true,userData:{locationObject:{properties:{level:-1}}}};
    const hit={object:{visible:true,isMesh:true,material:{opacity:1},parent:root}};
    assert.equal(context.visibleEditorHit(hit),false);
    root.userData.locationObject.properties.level=-2;
    assert.equal(context.visibleEditorHit(hit),true);
    root.visible=false;assert.equal(context.visibleEditorHit(hit),false);root.visible=true;
    hit.object.material.opacity=0;assert.equal(context.visibleEditorHit(hit),false);
    hit.object.userData={pickOnly:true};assert.equal(context.visibleEditorHit(hit),true);
    root.userData.locationObject.properties.level=-1;assert.equal(context.visibleEditorHit(hit),false);
    root.userData.locationObject.properties.level=-2;delete hit.object.userData.pickOnly;
    delete root.userData.locationObject;root.userData.locationLampIndex=0;
    hit.object.material.opacity=1;assert.equal(context.visibleEditorHit(hit),false);
});

test('select and eraser pointer planes follow all five signed levels', () => {
    const context=createSceneContext({window:{locationEditMode:true,locationEditorTool:'select'},
        locationViewLevel:0,STOREY_HEIGHT:2.4,editorMoveMode:false,eraserMode:false,buildMode:'terrain',
        currentLocationData:{grid_width:40,grid_height:40},structureLevelOffset:()=>0,getTileHeight:()=>1,
        getPointerWorldPoint:(x,y,height)=>({x:12+height,z:12+height})});
    vm.runInContext(functionSource('editorHoverLevelOffset','editorMovePointerPoint'),context);
    for(const level of [-2,-1,0,1,2]){
        context.locationViewLevel=level;
        assert.equal(context.editorHoverLevelOffset(),level*2.4);
        const tile=context.hoveredEditorTile(0,0,{x:13,z:13});
        assert.equal(tile.x,Math.floor(13+level*2.4));
    }
    context.window.locationEditorTool='terrain';context.eraserMode=true;context.locationViewLevel=-2;
    assert.equal(context.editorHoverLevelOffset(),-4.8);
    context.window.locationEditMode=false;
    for(const level of [-2,-1,0,1,2]) {
        context.locationViewLevel=level;
        assert.equal(context.editorHoverLevelOffset(),level*2.4,'ordinary game mode uses the same plane');
    }
});

test('delete policy uses the selected building and protects the base and intermediate storeys', () => {
    const objects=[-2,-1,0,1,2].map(level=>({properties:{building_id:'A',level}}));
    objects.push({properties:{building_id:'B',level:4}},{properties:{building_id:'B',level:-4}});
    const context=createSceneContext({currentLocationData:{objects},
        structureBuildingId:o=>o.properties.building_id,structureLevel:o=>o.properties.level});
    vm.runInContext(functionSource('locationFloorDeletionReason','refreshLocationFloorDeleteAction'),context);
    for(const level of [-1,0,1])assert.ok(context.locationFloorDeletionReason('A',level));
    for(const level of [-2,2])assert.equal(context.locationFloorDeletionReason('A',level),'');
    assert.ok(context.locationFloorDeletionReason('A',3));
    context.currentLocationData.objects=objects.filter(o=>o.properties.building_id!=='A'||Math.abs(o.properties.level)!==2);
    for(const level of [-1,1])assert.equal(context.locationFloorDeletionReason('A',level),'');
    assert.ok(context.locationFloorDeletionReason('A',0));
});

function functionSource(name, nextName) {
    const start = source.indexOf(`function ${name}(`);
    const end = source.indexOf(`function ${nextName}(`, start);
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end).replaceAll('export function ', 'function ')
        .replace(/\bexport\s*$/, '');
}

test('saved building IDs produce separate storey ranges and rotated footprints', () => {
    const objects = [
        { id: 1, type: 'floor', tile_x: 0, tile_y: 0,
            properties: { building_id: 'multi', level: 0, dimensions: { width: 4, depth: 4 } } },
        { id: 2, type: 'floor', tile_x: 0, tile_y: 0,
            properties: { building_id: 'multi', level: 1, dimensions: { width: 4, depth: 4 } } },
        { id: 3, type: 'floor', tile_x: 9, tile_y: 0,
            properties: { building_id: 'single', level: 0, dimensions: { width: 4, depth: 4 } } },
    ];
    const roofPlan = { floorId: 2, x: 0.5, z: 0.5, angle: Math.PI / 4,
        width: 4, depth: 4, eaveY: 5, rise: 1 };
    const context = createSceneContext({
        STOREY_HEIGHT: 2.4, locationViewLevel: 0, locationBuildingViews: new Map(),
        locationBuildingViewsDirty: true, currentLocationData: { objects },
        roofMeshes: [{ userData: { roofPlan } }], camera: null, controls: null,
        structureBuildingId: object => object?.properties?.building_id || null,
        structureLevel: object => Number(object?.properties?.level || 0),
        structureLevelOffset: object => Number(object?.properties?.level || 0) * 2.4,
        structurePose: object => ({ x: object.tile_x + 0.5, z: object.tile_y + 0.5,
            angle: 0, width: object.properties.dimensions.width,
            depth: object.properties.dimensions.depth, height: 0.12 }),
        getTileHeight: () => 0,
    });
    vm.runInContext(functionSource('getLocationBuildingViews', 'lampDisplayLevel'), context);
    const views = context.getLocationBuildingViews();
    assert.equal(views.get('multi').maxLevel, 1);
    assert.equal(views.get('single').maxLevel, 0);
    assert.equal(views.get('multi').plan, roofPlan);
    views.get('multi').distant = false;
    assert.equal(context.locationLevelVisible(1, 'multi', 0.5, 0.5), false);
    assert.equal(context.locationLevelVisible(1, null, 3.1, 0.5), false);
    assert.equal(context.locationLevelVisible(0, 'single', 9.5, 0.5), true);
    assert.equal(context.locationLevelVisible(1, null, 0.5, 0.5), false);
    assert.equal(context.locationLevelVisible(1, null, 9.5, 0.5), true);
    views.get('multi').distant = true;
    assert.equal(context.locationLevelVisible(1, null, 0.5, 0.5), true);
});

test('near multi-storey houses retain lower floors while single-storey houses stay intact', () => {
    const multi = { minLevel: 0, maxLevel: 2, distant: false,
        plan: { x: 1, z: 1, angle: 0, width: 4, depth: 4 } };
    const single = { minLevel: 0, maxLevel: 0, distant: false,
        plan: { x: 10, z: 1, angle: 0, width: 4, depth: 4 } };
    const structures = [0, 1, 2].map(level => ({ visible: true, position: { x: 1, z: 1 },
        userData: { locationObject: { properties: { level, building_id: 'multi' } } } }));
    structures.push({ visible: true, position: { x: 10, z: 1 },
        userData: { locationObject: { properties: { level: 0, building_id: 'single' } } } });
    const fracture={visible:true,castShadow:true};
    structures.push({visible:true,position:{x:1,z:1},userData:{floorAttachedMeshes:[fracture],
        locationObject:{type:'floor_opening',properties:{level:1,building_id:'multi'}}}});
    const decor = [0, 2.4, 4.8].map(elevation => ({ visible: true, position: { x: 1, z: 1 },
        userData: { sourceObject: { elevation, type: 'chair' } } }));
    decor.push({ visible: true, position: { x: 10, z: 1 },
        userData: { sourceObject: { elevation: 2.4, type: 'chair' } } });
    decor.push({ visible: true, position: { x: 20, z: 1 },
        userData: { sourceObject: { elevation: 2.4, type: 'chair' } } });
    const character = { gmHidden: false, camouflaged: false,
        model: { visible: true, position: { x: 1, z: 1 } }, label: { visible: true } };
    const context = createSceneContext({
        STOREY_HEIGHT: 2.4, locationViewLevel: 0, scene: {}, currentLocationData: {},
        locationBuildingViews: new Map([['multi', multi], ['single', single]]),
        locationBuildingViewsDirty: false,
        characterModels: new Map([[1, character]]), locationObjectMeshes: structures,
        objectMeshes: decor, window: { isGM: true }, fogIsActive: () => false,
        loadFogMemory() {}, updateFogOverlay() {}, syncFogMemoryGhosts() {},
        isFogHiddenObject: () => false,
        getObjectGridPosition: object=>({x:object.tile_x||0,y:object.tile_y||0}),
        structureBuildingId: object => object?.properties?.building_id || null,
        structureLevel: object => Number(object?.properties?.level || 0),
    });
    vm.runInContext(functionSource('decorDisplayLevel', 'locationFloorCount')
        + functionSource('applyFogOfWar', 'closeTeamManagementModal'), context);
    for (const level of [0, 1, 2]) {
        context.locationViewLevel = level;
        context.applyFogOfWar();
        assert.deepEqual(structures.map(mesh => mesh.visible), [true, level >= 1, level >= 2, true, level>=1]);
        assert.equal(fracture.visible,level>=1,'Обломки следуют своему перекрытию, а не поверхности выбора проёма');
        assert.deepEqual(decor.map(mesh => mesh.visible), [true, level >= 1, level >= 2, true, true]);
        assert.equal(character.model.visible, true);
        assert.equal(character.label.visible, true);
    }
    context.locationViewLevel = 0;
    multi.distant = true;
    context.applyFogOfWar();
    assert.deepEqual(structures.map(mesh => mesh.visible), [true, true, true, true, true]);
    assert.equal(fracture.visible,true,'В общем виде здания обломки остаются вместе с видимым верхним перекрытием');
    assert.deepEqual(decor.map(mesh => mesh.visible), [true, true, true, true, true]);
});

test('floor list includes saved upper floors and lamp fixtures follow the viewed level', () => {
    const sources = [2.2, 2.6, 4.6, 7.0].map(height => ({ height, x: 1, z: 1 }));
    sources.push({ height: 2.2, x: 10, z: 1 });
    const fixtures = sources.map((source, index) => ({ visible: true,
        userData: { locationLampIndex: index }, height: source.height }));
    const dynamicLight = { visible: true, userData: {} };
    const multi = { minLevel: 0, maxLevel: 2, distant: false,
        plan: { x: 1, z: 1, angle: 0, width: 4, depth: 4 } };
    const single = { minLevel: 0, maxLevel: 0, distant: false,
        plan: { x: 10, z: 1, angle: 0, width: 4, depth: 4 } };
    const context = createSceneContext({
        STOREY_HEIGHT: 2.4, locationViewLevel: 0,
        locationBuildingViews: new Map([['multi', multi], ['single', single]]),
        locationBuildingViewsDirty: false,
        currentLocationData: {
            objects: [{ properties: { level: 2 } }],
            tiles_data: [[{ objects: [] }]],
            lighting: { local_lights: sources },
        },
        locationLightGroup: { children: [...fixtures, dynamicLight] },
        window: { isGM: true },
        structureLevel: object => Number(object?.properties?.level || 0),
    });
    vm.runInContext(functionSource('decorDisplayLevel', 'setLocationViewLevel'), context);
    assert.equal(context.locationFloorCount(), 3);
    context.currentLocationData.objects = [];
    context.currentLocationData.lighting.local_lights = [];
    assert.equal(context.locationFloorCount(), 2);
    context.window.isGM = false;
    assert.equal(context.locationFloorCount(), 1);
    context.currentLocationData.lighting.local_lights = sources;
    for (const level of [0, 1, 2]) {
        context.locationViewLevel = level;
        context.updateLocationLampFixtureFloors();
        assert.deepEqual(fixtures.map(fixture => fixture.visible),
            [true, true, level >= 1, level >= 2, true]);
        assert.equal(dynamicLight.visible, true);
    }
    context.locationViewLevel = 0;
    multi.distant = true;
    context.updateLocationLampFixtureFloors();
    assert.deepEqual(fixtures.map(fixture => fixture.visible), [true, true, true, true, true]);
});

test('changing viewed floor updates fog, fixtures and roofs without changing placement data', () => {
    const calls = [];
    const context = createSceneContext({
        locationViewLevel: 0, editorSelection: { kind: 'structure', id: 7 },
        lampRenderSources: [],
        locationFloorCount: () => 3, locationMinimumLevel: () => 0, hideHighlight() {},
        document: { getElementById: () => null },
        clearEditorSelection() { calls.push('selection'); this.editorSelection = null; },
        applyFogOfWar: () => calls.push('fog'),
        updateLocationLampFixtureFloors: () => calls.push('fixtures'),
        updateRoofVisibility: () => calls.push('roofs'),
        refreshLocationFloorMenu: () => calls.push('menu'),
    });
    vm.runInContext(functionSource('setLocationViewLevel', 'updateRoofVisibility'), context);
    context.setLocationViewLevel(2);
    assert.equal(context.locationViewLevel, 2);
    assert.deepEqual(calls, ['selection', 'roofs', 'fog', 'fixtures', 'menu']);
    context.setLocationViewLevel(2);
    assert.equal(calls.length, 5);
    context.setLocationViewLevel(99);
    assert.equal(context.locationViewLevel, 2);
    context.setLocationViewLevel(-1);
    assert.equal(context.locationViewLevel, 0);
});

test('GM floor menu synchronizes the existing placement level without editing saved objects', () => {
    let changes = 0;
    const placementLevel = { value: '0', options: [{ value: '0' }, { value: '1' }],
        dispatchEvent() { changes++; } };
    const context = createSceneContext({
        locationViewLevel: 0, editorSelection: null, lampRenderSources: [],
        locationFloorCount: () => 3, locationMinimumLevel: () => 0, hideHighlight() {},
        document: { getElementById: () => placementLevel }, Event: class {},
        applyFogOfWar() {}, updateLocationLampFixtureFloors() {},
        updateRoofVisibility() {}, refreshLocationFloorMenu() {},
    });
    vm.runInContext(functionSource('setLocationViewLevel', 'updateRoofVisibility'), context);
    context.setLocationViewLevel(1);
    assert.equal(placementLevel.value, '1');
    assert.equal(changes, 1);
    context.setLocationViewLevel(2);
    assert.equal(placementLevel.value, '1');
    assert.equal(changes, 1);
});

test('floor menu releases keyboard focus after choosing a level or using its buttons', () => {
    let menu;
    const makeElement = tag => ({ tag, style: {}, dataset: {}, children: [], blurred: false,
        setAttribute() {}, remove() {}, blur() { this.blurred = true; },
        append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); },
    });
    const chosen = [];
    const context = createSceneContext({
        window: { isGM: true }, addFloorAboveSelectedLocationAsset() {},
        locationViewLevel: 0,
        document: { createElement: makeElement, getElementById(id) {
            return id === 'location-container' ? { appendChild(element) { menu = element; } } : null;
        } },
        setLocationViewLevel: level => chosen.push(level),
        refreshLocationFloorMenu() {}, applyFogOfWar() {},
        updateLocationLampFixtureFloors() {}, updateRoofVisibility() {}, lampRenderSources: [],
    });
    const start = source.indexOf('export function addLocationFloorMenu()');
    const end = source.indexOf('export function destroyLocationScene()', start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(source.slice(start, end).replace('export function', 'function').replace(/\bexport\s*$/, ''), context);
    context.addLocationFloorMenu();
    const [down, label, up] = menu.children;
    const select = label.children[0];
    select.value = '1';
    select.onchange();
    down.onclick();
    up.onclick();
    assert.deepEqual(chosen, ['1', -1, 1]);
    assert.equal(select.blurred, true);
    assert.equal(down.blurred, true);
    assert.equal(up.blurred, true);
});
