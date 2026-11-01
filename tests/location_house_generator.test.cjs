const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const asUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;

test('eraser intersects a wide slab away from its anchor and deletes only the active level in one batch',async()=>{
    const {architecture}=await modules();
    const objects=[-2,-1,0,1,2].map((level,i)=>({id:i+1,type:'floor',tile_x:8,tile_y:8,
        properties:{level,building_id:'house',dimensions:{width:10,depth:10,height:.12}}}));
    const scene=read('locationScene.js'),start=scene.indexOf('async function removeStructuresAtTile(');
    const calls=[];
    const context=createSceneContext({...architecture,window:{currentLobbyId:1},currentLocationId:1,
        currentLocationData:{objects},locationViewLevel:-2,structureDeletionInFlight:new Set(),
        Server:{changeLocationObjects:async(...args)=>{calls.push(args);return {deleted_ids:args[2].object_ids}}},
        applyLocationObjectsChange(){},showNotification(){throw Error('Unexpected error');}});
    vm.runInContext(scene.slice(start,scene.indexOf('export function setLocationBuildMode',start)),context);
    await context.removeStructuresAtTile(4,4);
    assert.equal(calls.length,1);
    assert.deepEqual(Array.from(calls[0][2].object_ids),[1]);
    assert.equal(calls[0][2].level,-2);
    assert.equal(calls[0][3],'DELETE');
    assert.equal(context.structureDeletionInFlight.size,0);
});

test('intact rectangular shells compact slabs and walls without duplicating coverage',async()=>{
    const {house}=await modules();
    const plan=house.planBuildingShell({minX:2,minZ:2,width:12,depth:12,storeys:2,basements:2,buildingId:'compact'});
    assert.equal(plan.objects.filter(o=>o.type==='floor').length,4);
    assert.equal(plan.objects.filter(o=>o.type==='roof').length,1);
    assert.equal(plan.objects.filter(o=>o.type==='wall'&&!o.properties.stairwell).length,16);
    assert.equal(plan.objects.filter(o=>o.type==='wall'&&o.properties.stairwell).length,4);
    assert.ok(plan.objects.filter(o=>o.type==='window').length<24);
    assert.ok(plan.objects.some(o=>o.type==='window'),'other rooms retain their windows');
    const {architecture}=await modules();
    for(const window of plan.objects.filter(o=>o.type==='window')) {
        const p=architecture.structurePose(window),level=window.properties.level;
        for(const stair of plan.objects.filter(o=>o.type==='stairs'&&[level,level-1].includes(o.properties.level))) {
            const q=architecture.structurePose(stair),dx=p.x-q.x,dz=p.z-q.z;
            const distance=Math.hypot(Math.max(0,Math.abs(dx*q.axis.x+dz*q.axis.z)-q.width/2),
                Math.max(0,Math.abs(dx*q.normal.x+dz*q.normal.z)-q.depth/2));
            assert.ok(distance>p.width/2+.5,'stairs and upper landings have no nearby windows');
        }
    }
    for (const roofType of ['flat','shed','gable']) {
        const edge=house.planBuildingShell({minX:0,minZ:0,width:12,depth:12,storeys:2,basements:2,
            buildingId:'edge',roofType});
        assert.ok(edge.objects.every(o=>o.tile_x>=0&&o.tile_x<12&&o.tile_y>=0&&o.tile_y<12), 'boundary anchors include overhanging eaves');
    }
    for(const level of [-2,-1,0,1]){
        const floors=plan.objects.filter(o=>o.type==='floor'&&o.properties.level===level);
        assert.equal(floors.reduce((n,o)=>n+o.properties.dimensions.width*o.properties.dimensions.depth,0),144);
    }
});

test('empty shells preserve orthogonal boundaries, independent wealth/damage, and valid editable openings', async () => {
    const { house, architecture, catalog } = await modules();
    const base = { minX: 2, minZ: 2, width: 10, depth: 12, storeys: 3, basements: 2,
        seed: 'shell', buildingId: 'shell' };
    for (const shape of ['rectangle', 'L', 'U', 'C', 'compound']) {
        for (const wealth of ['poor', 'ordinary', 'rich']) for (const condition of ['intact', 'worn', 'damaged', 'ruins']) {
            const options = { ...base, shape, wealth, condition };
            const plan = house.planBuildingShell(options);
            assert.equal(plan.error, undefined);
            assert.deepEqual(plan, house.planBuildingShell(options));
            assert.deepEqual(JSON.parse(JSON.stringify(plan)), plan);
            assert.deepEqual([...new Set(plan.objects.map(o => o.properties.level))].sort(), [-1,-2,0,1,2]);
            assert.equal(plan.objects.filter(o => o.type === 'ladder').length, 2);
            assert.equal(plan.objects.filter(o => o.type === 'stairs').length, condition === 'ruins' ? 1 : 2);
            assert.ok(plan.objects.filter(o=>o.type==='ladder').every(o=>o.properties.level<0));
            assert.ok(plan.objects.filter(o=>o.type==='hatch').every(o=>o.properties.is_open===false));
            assert.equal(plan.objects.filter(o => o.type === 'door').length, 1);
            for (const object of plan.objects) {
                assert.ok(['floor','wall','roof','door','window','ladder','stairs','hatch','floor_opening','household_rubble'].includes(object.type));
                assert.equal(object.properties.building_id, 'shell');
                assert.equal(object.properties.building_generation.wealth, wealth);
                if (object.properties.visual_asset) assert.ok(catalog.findLocationAsset(object.properties.visual_asset));
                if (['door','window'].includes(object.type)) {
                    assert.equal(plan.objects.filter(w => w.type === 'wall' && architecture.doorOnWall(object,w)).length, 1);
                }
            }
            const area=(p,type)=>p.objects.filter(o=>o.type===type).reduce((n,o)=>n+o.properties.dimensions.width*o.properties.dimensions.depth,0);
            const original = house.planBuildingShell({ ...options, condition: 'intact' });
            if (condition === 'damaged' || condition === 'ruins') {
                assert.ok(area(plan, 'roof') < area(original, 'roof'));
                assert.ok(plan.objects.some(o=>o.type==='wall'&&o.properties.dimensions.height<1));
                assert.ok(area(plan, 'floor') <= area(original, 'floor'),'damage preserves essential landings even in a narrow wing');
            }
            for (const floor of plan.objects.filter(o=>o.type==='floor')) {
                assert.equal(floor.properties.roof_enabled, false);
                assert.ok(architecture.floorSlabParts(floor, plan.objects).every(p=>p.width>0&&p.depth>0));
            }
        }
    }
    assert.ok(house.planBuildingShell({...base, width:9}).error);
    assert.ok(house.planBuildingShell({...base, shape:'U', width:6}).error);
    assert.ok(house.planBuildingShell({...base, storeys:5}).error);
});

test('failed atomic shell creation preserves unrelated manual objects', async () => {
    const {house, architecture} = await modules();
    const scene = read('locationScene.js');
    const source = scene.slice(scene.indexOf('async function createResidentialHouse('),
        scene.indexOf('export function canRestoreGeneratedLocationFloor('));
    const manual = {id:99,type:'floor',tile_x:20,tile_y:20,properties:{building_id:'manual'}};
    const data = {objects:[manual],grid_width:30,grid_height:30};
    const saved = [], deleted = [];
    let requests = 0;
    const context = createSceneContext({
        performance,houseCreationPending:false,currentLocationData:data,currentLocationId:1,houseSceneEpoch:1,
        window:{currentLobbyId:2},crypto:{randomUUID:()=> 'unique-run'},
        document:{querySelector:s=>({value:s==='[data-house-kind]'?'shell':'0'})},
        houseFootprint:house.houseFootprint,houseBuildingId:house.houseBuildingId,
        planBuildingShell:house.planBuildingShell,structureBuildingId:architecture.structureBuildingId,
        structurePose:architecture.structurePose,
        chooseBuildingShell:async footprint=>({...footprint,storeys:2,basements:1}),getTileHeight:()=>0,
        Server:{changeLocationObjects:async()=>{requests++;throw Error('save failed');}},
        applyLocationObjectsChange(){throw Error('Failed transaction must not update scene');},
        showNotification(){},
    });
    vm.runInContext(scene.slice(scene.indexOf('async function planNewBuilding('),scene.indexOf('export async function generateLocationBuildingAnomalies('))+source,context);
    await context.createResidentialHouse({x:2,z:2},{x:11,z:11});
    assert.equal(requests,1);
    assert.deepEqual(deleted,[]);
    assert.equal(data.objects.length,1);
    assert.equal(data.objects[0],manual);
    assert.equal(context.houseCreationPending,false);
});

test('shell roof sections use the saved editable building extent for camera cutaway', async () => {
    const {house,architecture,roof} = await modules();
    const plan = house.planBuildingShell({minX:2,minZ:2,width:10,depth:12,shape:'L',storeys:3,buildingId:'shell'});
    const objects=plan.objects.map((o,i)=>({...o,id:i+1}));
    const scene=read('locationScene.js');
    const context=createSceneContext({...architecture,...roof,currentLocationData:{objects},
        locationBuildingViewsDirty:true,locationBuildingViews:new Map(),roofMeshes:[],camera:null,controls:null,
        getTileHeight:()=>1});
    vm.runInContext(scene.slice(scene.indexOf('function getLocationBuildingViews('),scene.indexOf('function locationBuildingAt(')),context);
    const view=context.getLocationBuildingViews().get('shell');
    assert.equal(view.plan.width,10);assert.equal(view.plan.depth,12);assert.equal(view.plan.eaveY,8.2);
    const object=objects.find(o=>o.type==='roof'), child={isMesh:true,material:{}};
    context.camera={position:{x:11,y:8,z:13,distanceTo:()=>10}};
    context.controls={target:{x:7,y:5,z:8}};
    context.locationObjectMeshes=[{position:{y:8.2},userData:{locationObject:object},traverse:fn=>fn(child)}];
    Object.assign(context,{locationViewLevel:2,locationRoofsVisible:true,editMode:false,locationObjectPickCache:{key:null},
        updateStoreyTerrainVisibility(){},applyFogOfWar(){},updateLocationLampFixtureFloors(){},lampRenderSources:[]});
    const start=scene.indexOf('function updateRoofVisibility(');
    vm.runInContext(scene.slice(start,scene.indexOf('\n}',start)+2),context);
    context.updateRoofVisibility();assert.equal(child.visible,false);
    context.camera.position.y=40;context.updateRoofVisibility();assert.equal(child.visible,true);
    objects.find(o=>o.type==='floor').tile_x+=10;context.locationBuildingViewsDirty=true;
    assert.ok(context.getLocationBuildingViews().get('shell').plan.width>10,'manual edits update the cutaway extent');
});

test('a wall across a courtyard cannot shrink an unrelated floor section on the same level', async () => {
    const {architecture} = await modules();
    const floor={type:'floor',tile_x:3,tile_y:5,properties:{building_id:'C',dimensions:{width:2,depth:2,height:.12}}};
    const wall={type:'wall',tile_x:9,tile_y:4,properties:{building_id:'C',dimensions:{width:2,depth:.2,height:2.4}}};
    const original=architecture.structurePose(floor);
    const actual=architecture.floorVisualPose(floor,[floor,wall]);
    assert.equal(actual.width,original.width);assert.equal(actual.depth,original.depth);
    wall.tile_x=floor.tile_x;
    assert.ok(architecture.floorVisualPose(floor,[floor,wall]).depth<original.depth);
});

test('generated L-shaped slabs meet across the wing junction without a ground gap', async () => {
    const {house,architecture}=await modules();
    for(const wealth of ['poor','ordinary','rich']) {
        const plan=house.planBuildingShell({minX:2,minZ:2,width:10,depth:10,shape:'L',storeys:2,basements:1,
            condition:'intact',wealth,buildingId:'joined',seed:'joint'});
        for(const level of [-1,0,1])for(const z of [5.99,6,6.01]) {
            const covered=plan.objects.filter(o=>o.type==='floor'&&o.properties.level===level).some(f=>{
                const p=architecture.floorVisualPose(f,plan.objects),dx=3.5-p.x,dz=z-p.z;
                return Math.abs(dx*p.axis.x+dz*p.axis.z)<=p.width/2+1e-6
                    && Math.abs(dx*p.normal.x+dz*p.normal.z)<=p.depth/2+1e-6;
            });
            assert.ok(covered,`${wealth}, level ${level}: floor must reach joint at z=${z}`);
        }
    }
});

async function modules() {
    const architectureUrl = asUrl(read('locationArchitecture.js'));
    const [house, architecture, roof, catalog] = await Promise.all([
        import(asUrl(read('locationHouseGenerator.js').replace("'./locationRoofSections.js'", `'${asUrl(read('locationRoofSections.js'))}'`))),
        import(architectureUrl),
        import(asUrl(read('locationRoof.js').replace(
            "from './locationArchitecture.js'", `from '${architectureUrl}'`))),
        import(asUrl(read('locationAssetCatalog.js'))),
    ]);
    return { house, architecture, roof, catalog };
}

function reachable(rooms, connections) {
    const visited = new Set(['outside']);
    let changed = true;
    while (changed) {
        changed = false;
        for (const [from, to] of connections) {
            if (visited.has(from) && !visited.has(to)) {
                visited.add(to);
                changed = true;
            }
            if (visited.has(to) && !visited.has(from)) {
                visited.add(from);
                changed = true;
            }
        }
    }
    assert.ok(rooms.every(room => visited.has(room.id)));
    for (const privateRoom of rooms.filter(room => ['bedroom', 'bathroom'].includes(room.id))) {
        assert.equal(connections.filter(link => link.includes(privateRoom.id)).length, 1);
    }
}

test('small, medium and large houses have reachable terminal private rooms and valid openings', async () => {
    const { house, architecture, roof } = await modules();
    for (let width = 4; width <= 12; width++) for (let depth = 4; depth <= 12; depth++) {
        const expectedRooms = width >= 7 && depth >= 7 && width * depth >= 48 ? 5
            : width >= 6 && depth >= 5 && width * depth >= 30 ? 3 : 2;
        for (const seed of ['0', '1']) {
            const plan = house.planResidentialHouse({
                minX: 2, minZ: 3, width, depth, seed, buildingId: 'house-test',
            });
            assert.equal(plan.error, undefined);
            assert.equal(plan.rooms.length, expectedRooms);
            reachable(plan.rooms, plan.connections);
            assert.equal(plan.connections.length, plan.objects.filter(object => object.type === 'door').length);
            assert.equal(plan.objects.filter(object => object.type === 'floor').length, 1);
            assert.equal(roof.roofPlans(plan.objects, () => 0).length, 1);
            for (const object of plan.objects) {
                assert.equal(object.properties.building_id, 'house-test');
                assert.equal(object.properties.level, 0);
                assert.ok(object.tile_x >= 2 && object.tile_x < 2 + width);
                assert.ok(object.tile_y >= 3 && object.tile_y < 3 + depth);
                if (['door', 'window'].includes(object.type)) {
                    const matches = plan.objects.filter(wall => wall.type === 'wall'
                        && architecture.doorOnWall(object, wall));
                    assert.equal(matches.length, 1, `${width}×${depth} ${seed}: ${object.name}`);
                }
            }
            assert.deepEqual(
                JSON.parse(JSON.stringify(plan.objects)),
                JSON.parse(JSON.stringify(house.planResidentialHouse({
                    minX: 2, minZ: 3, width, depth, seed, buildingId: 'house-test',
                }).objects)),
            );
        }
    }
});

test('drag direction and seed produce stable footprints, IDs and JSON-saveable objects', async () => {
    const { house } = await modules();
    const footprint = house.houseFootprint(9, 8, 2, 3);
    assert.deepEqual(footprint, { minX: 2, minZ: 3, width: 8, depth: 6 });
    const id = house.houseBuildingId(17, footprint, 'perimeter');
    assert.equal(id, house.houseBuildingId(17, footprint, 'perimeter'));
    assert.notEqual(id, house.houseBuildingId(17, footprint, 'other'));
    const plan = house.planResidentialHouse({ ...footprint, seed: 'perimeter', buildingId: id });
    assert.equal(plan.objects[0].properties.generation_seed, 'perimeter');
    assert.deepEqual(JSON.parse(JSON.stringify(plan.objects)).length, plan.objects.length);
    assert.ok(house.planResidentialHouse({
        minX: 0, minZ: 0, width: 3, depth: 4, buildingId: id,
    }).error);
});

test('a generated floor is restorable exactly from saved wall metadata, without replacing the house', async () => {
    const { house, architecture, roof } = await modules();
    const plan = house.planResidentialHouse({ minX: 2, minZ: 3, width: 8, depth: 7,
        seed: 'home', buildingId: 'house-restore' });
    const saved = JSON.parse(JSON.stringify(plan.objects));
    const original = saved.find(object => object.type === 'floor');
    assert.equal(house.restorableGeneratedFloor(saved, 'house-restore', 0), null);
    const withoutFloor = saved.filter(object => object !== original);
    const restored = house.restorableGeneratedFloor(withoutFloor, 'house-restore', 0);
    assert.deepEqual(restored, original);
    assert.deepEqual(architecture.floorVisualPose(restored, withoutFloor),
        architecture.floorVisualPose(original, saved));
    assert.equal(roof.roofPlans([...withoutFloor, restored], () => 0).length, 1);
    assert.equal(withoutFloor.length, saved.length - 1);
    assert.equal(house.restorableGeneratedFloor([...withoutFloor, restored], 'house-restore', 0), null);
    assert.equal(house.restorableGeneratedFloor(withoutFloor, 'house-restore', 1), null);
    assert.equal(house.restorableGeneratedFloor(withoutFloor, 'other-house', 0), null);
    const oldHouse = JSON.parse(JSON.stringify(withoutFloor));
    delete oldHouse[0].properties.generated_floor_spec;
    assert.equal(house.restorableGeneratedFloor(oldHouse, 'house-restore', 0), null);
});

test('fitting a resized generated floor recovers its saved footprint without replacing rooms', async () => {
    const { house, architecture } = await modules();
    const plan = house.planResidentialHouse({ minX: 2, minZ: 3, width: 8, depth: 7,
        seed: 'fit', buildingId: 'house-fit' });
    const floor = plan.objects.find(object => object.type === 'floor');
    const altered = { ...floor, properties: { ...floor.properties,
        dimensions: { ...floor.properties.dimensions, width: 3, depth: 3 } } };
    const objects = plan.objects.map(object => object === floor ? altered : object);
    const blueprint = house.generatedFloorBlueprint(objects, 'house-fit', 0);
    const fitted = architecture.floorFitPlan(altered, objects, blueprint);
    assert.equal(fitted.error, undefined);
    assert.equal(fitted.pose.width, 8);
    assert.equal(fitted.pose.depth, 7);
    assert.equal(objects.length, plan.objects.length);
});

test('the restore action creates only one ordinary floor and refuses a second restore', async () => {
    const { house } = await modules();
    const plan = house.planResidentialHouse({ minX: 1, minZ: 1, width: 7, depth: 7,
        seed: 'again', buildingId: 'house-action' });
    const data = { objects: JSON.parse(JSON.stringify(plan.objects.filter(object => object.type !== 'floor'))) };
    const sceneSource = read('locationScene.js');
    const start = sceneSource.indexOf('export function canRestoreGeneratedLocationFloor(');
    const end = sceneSource.indexOf('function createPosterMesh(', start);
    assert.ok(start > 0 && end > start);
    const created = [];
    const context = createSceneContext({
        currentLocationData: data, currentLocationId: 5, houseFloorRestorePending: false, houseSceneEpoch: 1,
        window: { currentLobbyId: 9 }, restorableGeneratedFloor: house.restorableGeneratedFloor,
        Server: { createLocationObject: async (lobby, location, spec) => {
            assert.equal(lobby, 9);
            assert.equal(location, 5);
            created.push(spec);
            return { ...spec, id: 101 };
        } },
        addLocationObject: object => data.objects.push(object), showNotification() {},
    });
    vm.runInContext(sceneSource.slice(start, end).replaceAll('export ', ''), context);
    assert.equal(context.canRestoreGeneratedLocationFloor('house-action', 0), true);
    assert.equal(await context.restoreGeneratedLocationFloor('house-action', 0), true);
    assert.equal(await context.restoreGeneratedLocationFloor('house-action', 0), false);
    assert.equal(created.length, 1);
    assert.equal(data.objects.filter(object => object.type === 'floor').length, 1);
    assert.equal(data.objects.length, plan.objects.length);
});

test('catalog furniture stays inside its room, clear of doorways and other furniture', async () => {
    const { house, architecture, catalog } = await modules();
    for (let width = 4; width <= 12; width++) for (let depth = 4; depth <= 12; depth++) {
        for (const seed of ['0', '1']) {
            const options = { minX: 2, minZ: 3, width, depth, seed, buildingId: 'house-test',
                assetById: catalog.findLocationAsset };
            const plan = house.planResidentialHouse(options);
            const furniture = plan.objects.filter(object => ['table', 'chair', 'shelf'].includes(object.type));
            assert.ok(furniture.length > 0);
            assert.ok(furniture.some(object => object.type === 'table'));
            const doors = plan.objects.filter(object => object.type === 'door');
            for (const item of furniture) {
                const pose = architecture.structurePose(item);
                const room = plan.rooms.find(entry => pose.x > entry.bounds.minX
                    && pose.x < entry.bounds.maxX && pose.z > entry.bounds.minZ
                    && pose.z < entry.bounds.maxZ);
                assert.ok(room, `furniture outside rooms: ${item.name}`);
                assert.ok(pose.x - pose.width / 2 >= room.bounds.minX + 0.25 - 1e-6);
                assert.ok(pose.x + pose.width / 2 <= room.bounds.maxX - 0.25 + 1e-6);
                assert.ok(pose.z - pose.depth / 2 >= room.bounds.minZ + 0.25 - 1e-6);
                assert.ok(pose.z + pose.depth / 2 <= room.bounds.maxZ - 0.25 + 1e-6);
                for (const door of doors) {
                    const at = architecture.structurePose(door);
                    assert.ok(Math.abs(pose.x - at.x) >= pose.width / 2 + 0.75
                        || Math.abs(pose.z - at.z) >= pose.depth / 2 + 0.75);
                }
                for (const other of furniture) {
                    if (other === item) continue;
                    const at = architecture.structurePose(other);
                    assert.ok(Math.abs(pose.x - at.x) >= (pose.width + at.width) / 2 + 0.1 - 1e-6
                        || Math.abs(pose.z - at.z) >= (pose.depth + at.depth) / 2 + 0.1 - 1e-6);
                }
            }
            assert.deepEqual(plan.objects, house.planResidentialHouse(options).objects);
        }
    }
});

test('house draw uses the existing editor pointer and object persistence paths', () => {
    const scene = read('locationScene.js');
    const main = read('main.js');
    assert.match(scene, /houseDrawStart = \{ \.\.\.hoveredTileCoords \}/);
    assert.match(scene, /void createResidentialHouse\(start, end\)/);
    assert.match(scene, /editorChangeObjects\(lobbyId, locationId, \{ objects: plan.objects \}\)/);
    assert.match(main, /data-house-draw/);
    assert.match(main, /setLocationHouseDrawMode\(enabled\)/);
});
