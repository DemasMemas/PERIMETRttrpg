const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const sceneSource = read('locationScene.js');
const editorSource = read('main.js');
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const load = async () => {
    const architectureUrl = dataUrl(read('locationArchitecture.js'));
    const roofSource = read('locationRoof.js').replace("'./locationArchitecture.js'", `'${architectureUrl}'`);
    return import(dataUrl(roofSource));
};

function room() {
    const floor = { id: 1, type: 'floor', tile_x: 9, tile_y: 8,
        properties: { offset_x: 0.5, offset_z: 0.5,
            dimensions: { width: 13, depth: 11, height: 0.12 } } };
    const wall = (id, x, z, width, rotation = 0, offsetX = 0, offsetZ = 0) => ({
        id, type: 'wall', tile_x: x, tile_y: z,
        properties: { rotation, offset_x: offsetX, offset_z: offsetZ,
            dimensions: { width, depth: 0.2, height: 2.5 } },
    });
    return [floor,
        wall(2, 9, 3, 13, 0, 0.5), wall(3, 9, 14, 13, 0, 0.5),
        wall(4, 3, 8, 11, Math.PI / 2, 0, 0.5),
        wall(5, 16, 8, 11, Math.PI / 2, 0, 0.5)];
}

test('only enclosed floors receive roofs above their walls', async () => {
    const { roofPlans } = await load();
    const objects = room();
    const [roof] = roofPlans(objects, () => 1);
    assert.ok(roof);
    assert.equal(roof.floorId, 1);
    assert.equal(roof.x, 10);
    assert.equal(roof.z, 9);
    assert.ok(roof.width > 13 && roof.depth > 11);
    assert.equal(roof.eaveY, 3.62);
    assert.equal(roof.gableColor, '#aa8866');
    assert.equal(roof.type, 'gable');
    assert.deepEqual(roofPlans(objects.slice(0, 4)), []);
    objects[0].properties.roof_enabled = false;
    assert.deepEqual(roofPlans(objects), []);
    objects[0].properties.roof_enabled = true;
    objects[2].properties.gm_hidden = true;
    assert.deepEqual(roofPlans(objects), []);
});

test('roof geometry has two sloping faces and camera cutaway preserves outside view', async () => {
    const { roofPlans, createRoofGeometry, createRoofGableGeometry, roofVisibleForCamera } = await load();
    const plan = roofPlans(room())[0];
    class BufferGeometry {
        setAttribute(name, value) { this[name] = value; }
        getAttribute(name) { return this[name]; }
        computeVertexNormals() {}
    }
    class Float32BufferAttribute {
        constructor(array) { this.array = array; }
    }
    const geometry = createRoofGeometry({ BufferGeometry, Float32BufferAttribute }, plan);
    assert.equal(geometry.position.array.length, 180);
    assert.ok(geometry.position.array.includes(plan.rise));
    const gable = createRoofGableGeometry({ BufferGeometry, Float32BufferAttribute }, plan);
    assert.equal(gable.position.array.length, 54);
    assert.ok(gable.position.array.includes(plan.rise - 0.12));
    assert.ok(gable.position.array.includes(-0.12));
    const halfDepth = (plan.depth - 0.48) / 2;
    const shoulderY = plan.rise * (1 - halfDepth / (plan.depth / 2)) - 0.12;
    assert.ok(gable.position.array.some(value => Math.abs(value - shoulderY) < 0.000001));
    assert.equal(plan.eaveY - 0.12, 3.5);
    for (const start of [0, 90]) {
        const vertices = geometry.position.array;
        const ax = vertices[start + 3] - vertices[start];
        const az = vertices[start + 5] - vertices[start + 2];
        const bx = vertices[start + 6] - vertices[start];
        const bz = vertices[start + 8] - vertices[start + 2];
        assert.ok(az * bx - ax * bz > 0);
    }
    assert.equal(roofVisibleForCamera(plan, { x: 50, z: 50 }, 10, true, false), true);
    assert.equal(roofVisibleForCamera(plan, { x: plan.x, z: plan.z }, 10, true, false), false);
    assert.equal(roofVisibleForCamera(plan, { x: plan.x, z: plan.z }, 30, true, false), true);
    assert.equal(roofVisibleForCamera(plan, { x: 50, z: 50 }, 30, false, false), false);
    assert.equal(roofVisibleForCamera(plan, { x: 50, z: 50 }, 30, true, true), false);
});

test('roof shadow includes gable caps without an extra shadow draw', async () => {
    const { roofPlans, createRoofVisual } = await load();
    const plan = roofPlans(room())[0];
    const patterns = [];
    class BufferGeometry {
        setAttribute(name, value) { this[name] = value; }
        getAttribute(name) { return this[name]; }
        computeVertexNormals() { this.normalsComputed = true; }
    }
    class Float32BufferAttribute {
        constructor(array) { this.array = array; }
    }
    class BoxGeometry {
        constructor(width, height, depth) { Object.assign(this, { width, height, depth }); }
        translate(x, y, z) { this.offset = { x, y, z }; return this; }
    }
    class Material {
        constructor(options) { Object.assign(this, options); }
    }
    class Mesh {
        constructor(geometry, material) {
            this.geometry = geometry;
            this.material = material;
            this.isMesh = true;
        }
    }
    class Group {
        constructor() {
            this.children = [];
            this.position = { set(x, y, z) { Object.assign(this, { x, y, z }); } };
            this.rotation = {};
            this.userData = {};
        }
        add(child) { this.children.push(child); }
        traverse(callback) { this.children.forEach(callback); }
    }
    const THREE = { BufferGeometry, Float32BufferAttribute, BoxGeometry, MeshStandardMaterial: Material,
        MeshBasicMaterial: Material, Mesh, Group, DoubleSide: 'double' };
    const roofTexture = {};
    const wallTexture = {};
    const roof = createRoofVisual(THREE, plan, {
        roofTexture, wallTexture, gableColor: '#aabbcc',
        applySurfacePattern: (...args) => patterns.push(args),
    });
    const [face, gable, shadow] = roof.children;
    assert.notEqual(face.geometry, shadow.geometry);
    assert.notEqual(gable.geometry, face.geometry);
    assert.equal(face.geometry.normalsComputed, true);
    assert.equal(gable.geometry.normalsComputed, true);
    assert.equal(shadow.geometry.normalsComputed, true);
    const roofPositions = face.geometry.getAttribute('position').array;
    const gablePositions = gable.geometry.getAttribute('position').array;
    const shadowPositions = shadow.geometry.getAttribute('position').array;
    assert.equal(shadowPositions.length, roofPositions.length + gablePositions.length);
    for (let index = 0; index < gablePositions.length; index++) {
        assert.ok(Math.abs(shadowPositions[roofPositions.length + index] - gablePositions[index]) < 1e-5);
    }
    const gableX = (plan.width - 0.48) / 2;
    const sampleY = (plan.rise - 0.12) / 2;
    const coversGableCenter = positions => {
        const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]);
        const point = [sampleY, 0];
        for (let index = 0; index < positions.length; index += 9) {
            const vertices = [0, 3, 6].map(offset => [positions[index + offset + 1],
                positions[index + offset + 2]]);
            if ([0, 3, 6].some(offset =>
                Math.abs(positions[index + offset] - gableX) > 1e-5)) continue;
            const area = cross(vertices[0], vertices[1], vertices[2]);
            if (Math.abs(area) < 1e-6) continue;
            const signs = [cross(vertices[0], vertices[1], point),
                cross(vertices[1], vertices[2], point), cross(vertices[2], vertices[0], point)];
            if (signs.every(value => value >= -1e-5) || signs.every(value => value <= 1e-5)) return true;
        }
        return false;
    };
    assert.equal(coversGableCenter(roofPositions), false);
    assert.equal(coversGableCenter(shadowPositions), true);
    assert.equal(face.material.color, plan.color);
    assert.equal(face.material.roughness, 0.94);
    assert.equal(gable.material.color, '#aabbcc');
    assert.equal(gable.material.roughness, 1);
    assert.deepEqual(patterns, [
        [face.material, roofTexture, 0.22, 'top'],
        [gable.material, wallTexture, 0.25, 'sides'],
    ]);
    assert.equal(shadow.castShadow, true);
    assert.equal(roof.children.filter(child => child.castShadow).length, 1);
    assert.equal(shadow.material.colorWrite, false);
    assert.equal(shadow.material.depthWrite, false);
    assert.ok(roof.children.every(child => child.receiveShadow));
    assert.deepEqual(roof.userData.visualMeshes, [face, gable]);
    assert.equal(roof.userData.roofPlan, plan);
    assert.equal(roof.position.y, plan.eaveY);
    assert.equal(roof.rotation.y, plan.angle);
    assert.match(sceneSource, /const roof = createRoofVisual\(THREE, plan,/);
    const rebuild = sceneSource.slice(sceneSource.indexOf('function rebuildLocationRoofs()'),
        sceneSource.indexOf('function refreshArchitecturalMeshes()'));
    assert.match(rebuild, /for \(const roof of roofMeshes\) \{[\s\S]*?scene\?\.remove\(roof\);\s*disposeLocationObject\(roof\)/);

    const objects = room();
    objects[0].properties.roof_type = 'flat';
    const [flatPlan] = roofPlans(JSON.parse(JSON.stringify(objects)), () => 1);
    const flatRoof = createRoofVisual(THREE, flatPlan, {
        roofTexture, wallTexture, gableColor: '#aabbcc',
        applySurfacePattern: (...args) => patterns.push(args),
    });
    const [slab, flatShadow] = flatRoof.children;
    assert.equal(flatRoof.children.length, 2);
    assert.equal(slab.geometry, flatShadow.geometry);
    assert.equal(slab.geometry.width, flatPlan.width);
    assert.equal(slab.geometry.depth, flatPlan.depth);
    assert.equal(slab.geometry.height, 0.13);
    assert.deepEqual(slab.geometry.offset, { x: 0, y: -0.065, z: 0 });
    assert.equal(flatShadow.castShadow, true);
    assert.equal(flatRoof.userData.visualMeshes.length, 1);
    assert.deepEqual(patterns[2], [slab.material, roofTexture, 0.22, 'top']);
});

test('flat roof matches wall outline and survives location object serialization', async () => {
    const { roofPlans, roofVisibleForCamera } = await load();
    const objects = room();
    const [gable] = roofPlans(objects, () => 1);
    objects[0].properties.roof_type = 'flat';
    const savedObjects = JSON.parse(JSON.stringify(objects));
    const [flat] = roofPlans(savedObjects, () => 1);
    assert.equal(flat.type, 'flat');
    assert.equal(flat.rise, 0);
    assert.equal(flat.x, gable.x);
    assert.equal(flat.z, gable.z);
    assert.ok(Math.abs(flat.width - (gable.width - 0.46)) < 0.000001);
    assert.ok(Math.abs(flat.depth - (gable.depth - 0.46)) < 0.000001);
    assert.ok(Math.abs(flat.x - flat.width / 2 - 3.39) < 0.000001);
    assert.ok(Math.abs(flat.x + flat.width / 2 - 16.61) < 0.000001);
    assert.equal(flat.eaveY - 0.13, 3.49);
    assert.equal(roofVisibleForCamera(flat, { x: flat.x, z: flat.z }, 10, true, false), false);
    assert.equal(roofVisibleForCamera(flat, { x: flat.x, z: flat.z }, 10, false, false), false);
    assert.match(editorSource, /data-placement-roof-type/);
    assert.match(editorSource, /data-selected-roof-type/);
    assert.match(sceneSource, /roof_type: roofType === 'flat' \? 'flat' : 'gable'/);
});

test('two serialized storeys keep their own walls and only the upper roof', async () => {
    const { roofPlans } = await load();
    const lower = room().map(object => ({ ...object,
        properties: { ...object.properties, building_id: 'house-a', level: 0 } }));
    const upper = lower.map(object => ({ ...object, id: object.id + 10,
        properties: { ...object.properties, level: 1,
            ...(object.type === 'floor' ? { roof_type: 'flat' } : {}) } }));
    const other = lower.map(object => ({ ...object, id: object.id + 20,
        properties: { ...object.properties, building_id: 'house-b' } }));
    const saved = JSON.parse(JSON.stringify([...lower, ...upper, ...other]));
    const plans = roofPlans(saved, () => 1);
    assert.deepEqual(plans.map(plan => plan.floorId).sort((a, b) => a - b), [11, 21]);
    const upperRoof = plans.find(plan => plan.floorId === 11);
    const otherRoof = plans.find(plan => plan.floorId === 21);
    assert.equal(upperRoof.type, 'flat');
    assert.equal(upperRoof.rise, 0);
    assert.ok(Math.abs(upperRoof.eaveY - 6.02) < 0.000001);
    assert.equal(otherRoof.type, 'gable');
    assert.equal(otherRoof.eaveY, 3.62);
    assert.deepEqual(roofPlans(saved.filter(object => object.id !== 13), () => 1)
        .map(plan => plan.floorId), [21]);
    assert.match(editorSource, /data-placement-building-id/);
    assert.match(editorSource, /data-placement-level/);
    assert.match(sceneSource, /structureLevelOffset\(obj\)/);
    assert.match(sceneSource, /\.\.\.building,/);
});

test('roof gables align with walls even when the floor is offset', async () => {
    const { roofPlans } = await load();
    const objects = room();
    objects[0].properties.offset_x = 0.29539168381616676;
    objects[0].properties.offset_z = 0.43729457403232885;
    objects[1].properties.offset_x = 0.29304344667146687;
    objects[1].properties.offset_z = 0.09003750743589567;
    const [plan] = roofPlans(objects, () => 1);
    assert.ok(plan);
    assert.ok(Math.abs(plan.x - 10) < 0.001);
    assert.ok(Math.abs(plan.x - (plan.width - 0.48) / 2 - 3.4) < 0.001);
    assert.ok(Math.abs(plan.x + (plan.width - 0.48) / 2 - 16.6) < 0.001);
    assert.equal(plan.eaveY - 0.12, 3.5);
});

test('small and large roofs cut away at comparable framing, and enclosed floors keep overhead shade', async () => {
    const { roofVisibleForCamera, applyInteriorFloorShade } = await load();
    const small = { x: 4, z: 4, angle: 0, width: 6, depth: 5 };
    const large = { ...small, width: 12, depth: 10 };
    small.eaveY = large.eaveY = 3;
    small.rise = large.rise = 1;
    assert.equal(roofVisibleForCamera(small, { x: 100, z: 100 }, 80, true, false,
        { x: 4, y: 8, z: 4 }), false);
    assert.equal(roofVisibleForCamera(small, { x: 4, z: 4 }, 4, true, false,
        { x: 40, y: 8, z: 40 }), true);
    assert.equal(roofVisibleForCamera(large, { x: 4, z: 4 }, 14, true, false,
        { x: 4, y: 12, z: 4 }), false);
    assert.equal(roofVisibleForCamera(large, { x: 4, z: 4 }, 18, true, false,
        { x: 4, y: 16, z: 4 }), true);

    const material = { userData: {}, onBeforeCompile: shader => {
        shader.vertexShader = shader.vertexShader.replace('#include <project_vertex>',
            '// surface pattern\n#include <project_vertex>');
    } };
    applyInteriorFloorShade(material, 6, 5, [1.5, -2]);
    const shader = { uniforms: {}, vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: '#include <common>\n#include <color_fragment>' };
    material.onBeforeCompile(shader);
    assert.deepEqual(shader.uniforms.interiorFloorHalfSize.value, [3, 2.5]);
    assert.equal(shader.uniforms.interiorShadeIntensity, material.userData.interiorShade);
    assert.deepEqual(shader.uniforms.interiorEntrance.value, [1.5, -2]);
    assert.equal(shader.uniforms.interiorEntranceEnabled.value, 1);
    assert.match(shader.vertexShader, /vInteriorFloorXZ = position\.xz/);
    assert.match(shader.fragmentShader, /interiorWallDistance/);
    assert.match(shader.fragmentShader, /smoothstep/);
    assert.match(shader.fragmentShader, /0\.035 \* interiorEdge\.x \* interiorEdge\.y/);
    assert.match(shader.fragmentShader, /interiorEntranceEnabled \* 0\.045/);
    assert.match(sceneSource, /enclosedFloors\.has\(object\.userData\.locationObjectId\)/);
    assert.match(sceneSource, /const FLOOR_SURFACE_OFFSET = 0\.035/);
    assert.match(sceneSource, /height - FLOOR_SURFACE_OFFSET/);
});

test('small roofs become transparent at a useful interior viewing distance', async () => {
    const { roofOpacityForCamera, roofVisibleForCamera } = await load();
    const plan = { x: 5, z: 5, angle: 0, width: 6, depth: 5, eaveY: 5, rise: 1 };
    const threshold = 10;
    const opacityAtHeight = height => roofOpacityForCamera(plan, { x: 5, z: 5 }, height,
        true, false, { x: 5, y: plan.eaveY + plan.rise + height, z: 5 });
    assert.equal(opacityAtHeight(threshold - 2), 0);
    assert.ok(Math.abs(opacityAtHeight(threshold) - 0.5) < 1e-10);
    assert.equal(opacityAtHeight(threshold + 2), 1);
    assert.equal(roofVisibleForCamera(plan, { x: 5, z: 5 }, threshold - 2, true, false), false);
    assert.equal(roofVisibleForCamera(plan, { x: 5, z: 5 }, threshold + 2, true, false), true);
    assert.equal(roofOpacityForCamera(plan, { x: 5, z: 5 }, threshold + 2, false, false), 0);
    assert.equal(roofOpacityForCamera(plan, { x: 5, z: 5 }, threshold + 2, true, true), 0);
    const horizontal = roofOpacityForCamera(plan, { x: 5, z: 5 }, threshold, true, false,
        { x: 5 + plan.width / 2 + threshold, y: plan.eaveY + plan.rise, z: 5 });
    assert.ok(Math.abs(horizontal - 0.5) < 1e-10);
});

test('near roof cuts away while distant roof remains complete on any viewed floor', async () => {
    const { roofOpacityForCamera, roofVisibleForCamera } = await load();
    const start = sceneSource.indexOf('function updateRoofVisibility()');
    const end = sceneSource.indexOf('function rebuildLocationRoofs()', start);
    assert.ok(start >= 0 && end > start);
    const lowerFloor = { visible: true, userData: { locationObject: {
        type: 'floor', properties: { level: 0, building_id: 'house-a' },
    } } };
    const upperFloor = { visible: true, userData: { locationObject: {
        type: 'floor', properties: { level: 1, building_id: 'house-a' },
    } } };
    const face = { visible: true, material: { opacity: 1, transparent: false, depthWrite: true, side: 2 } };
    const gable = { visible: true, material: { opacity: 1, transparent: false, depthWrite: true, side: 2 } };
    const shadow = { visible: true, castShadow: true };
    const roof = { userData: { roofPlan: { x: 5, z: 5, angle: 0, width: 6, depth: 5,
        eaveY: 5, rise: 1 }, floorLevel: 1, visualMeshes: [face, gable], cutawayFloorMesh: upperFloor },
    children: [face, gable, shadow] };
    const view = { minLevel: 0, maxLevel: 1, distant: false, plan: roof.userData.roofPlan };
    let visibilityUpdates = 0;
    const context = vm.createContext({
        firstPersonView: null,
        currentLocationData: { tiles_data: [] },
        camera: { position: { x: 5, y: 8, z: 5,
            distanceTo(target) { return Math.hypot(this.x - target.x, this.y - target.y, this.z - target.z); } } },
        controls: { target: { x: 5, y: 0, z: 5 } }, roofMeshes: [roof], locationObjectMeshes: [],
        roofOpacityForCamera, roofVisibleForCamera, locationRoofsVisible: true,
        updateStoreyTerrainVisibility() {}, editMode: false, locationViewLevel: 0,
        THREE: { FrontSide: 0, DoubleSide: 2 },
        locationBuildingViewsDirty: false, getLocationBuildingViews: () => new Map([['house-a', view]]),
        applyFogOfWar: () => visibilityUpdates++, updateLocationLampFixtureFloors() {},
        lampRenderSources: [],
    });
    vm.runInContext(sceneSource.slice(start, end), context);
    context.updateRoofVisibility();
    assert.equal(face.visible, false);
    assert.equal(shadow.visible, false);
    assert.equal(upperFloor.visible, true);
    assert.equal(lowerFloor.visible, true);

    context.camera.position.x = 9;
    context.camera.position.z = 8;
    context.updateRoofVisibility();
    assert.equal(upperFloor.visible, true);
    assert.equal(lowerFloor.visible, true);

    context.camera.position.x = 10;
    context.camera.position.y = 15;
    context.camera.position.z = 10;
    context.updateRoofVisibility();
    assert.equal(upperFloor.visible, true);
    assert.equal(lowerFloor.visible, true);
    assert.ok(face.material.opacity > 0 && face.material.opacity < 1);
    assert.equal(face.material.transparent, true);
    assert.equal(face.material.depthWrite, true);
    assert.equal(face.material.side, 0);
    assert.equal(gable.material.side, 2);
    assert.equal(gable.material.opacity, face.material.opacity);
    assert.equal(shadow.visible, false);

    context.camera.position.x = 40;
    context.updateRoofVisibility();
    assert.equal(face.visible, true);
    assert.equal(face.material.opacity, 1);
    assert.equal(face.material.transparent, false);
    assert.equal(face.material.depthWrite, true);
    assert.equal(face.material.side, 2);
    assert.equal(view.distant, true);
    assert.equal(visibilityUpdates, 1);
    assert.equal(shadow.visible, true);
    context.locationViewLevel = 1;
    context.updateRoofVisibility();
    assert.equal(face.visible, true);
    assert.equal(shadow.visible, true);
    assert.equal(upperFloor.visible, true);
    assert.equal(lowerFloor.visible, true);

    context.editMode = true;
    context.updateRoofVisibility();
    assert.equal(upperFloor.visible, true);
    assert.equal(face.visible, true);
    assert.equal(shadow.visible, true);
    context.editMode = false;
    context.locationRoofsVisible = false;
    context.updateRoofVisibility();
    assert.equal(face.visible, false);
    assert.equal(upperFloor.visible, true);
    assert.equal(shadow.visible, false);
    assert.equal(lowerFloor.visible, true);

    const oldRoof = { userData: { ...roof.userData, roofOpacity: undefined,
        cutawayFloorMesh: null }, children: [face, gable, shadow] };
    shadow.visible = true;
    context.roofMeshes = [oldRoof];
    context.updateRoofVisibility();
    assert.equal(lowerFloor.visible, true);
    assert.equal(shadow.visible, false);
    assert.match(sceneSource, /structureBuildingId\(lower\) === structureBuildingId\(floor\)/);
});
