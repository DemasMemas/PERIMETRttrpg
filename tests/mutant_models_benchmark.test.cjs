const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const models = read('app/static/js/mutantModels.js');
const benchmark = read('app/static/js/performanceBenchmark.js');

test('игровой Кабан использует одну сетку с цветами вершин', () => {
    const bytes=fs.readFileSync(path.join(__dirname,'../app/static/models/mutants/boar/wild-boar-light.glb'));
    assert.equal(bytes.readUInt32LE(0),0x46546c67);
    const jsonLength=bytes.readUInt32LE(12),asset=JSON.parse(bytes.subarray(20,20+jsonLength));
    const primitives=asset.meshes.flatMap(mesh=>mesh.primitives);
    const triangles=primitives.reduce((sum,p)=>sum+asset.accessors[p.indices].count/3,0);
    assert.equal(triangles,2308);
    assert.equal(primitives.length,1);
    assert.ok(Number.isInteger(primitives[0].attributes.COLOR_0));
    assert.ok(bytes.length<256*1024);
    assert.equal(asset.images?.length ?? 0,0);
    assert.ok(asset.materials.every(m=>!m.alphaMode||m.alphaMode==='OPAQUE'));
});

function threeStub() {
    class Object3D {
        constructor() {
            this.children = [];
            this.userData = {};
            this.position = { set() {} };
            this.scale = { set() {}, setScalar() {} };
            this.rotation = { set() {} };
        }
        add(child) { this.children.push(child); }
        remove(child) { this.children = this.children.filter(item => item !== child); }
        traverse(visit) { visit(this);this.children.forEach(child=>child.traverse(visit)); }
        dispatchEvent(event) { this.lastEvent=event; }
        updateMatrixWorld() { this.matrix = {}; this.children.forEach(child => child.updateMatrixWorld()); }
    }
    class Geometry {
        constructor() { this.index = null; this.attributes = {}; }
        clone() { return new Geometry(); }
        applyMatrix4() {}
        getAttribute() { return { count: 3, getX: () => 0, getY: () => 0, getZ: () => 0 }; }
        setAttribute(name, attribute) { this.attributes[name] = attribute; }
        dispose() {}
    }
    class Mesh extends Object3D {
        constructor(geometry, material) {
            super();
            this.geometry = geometry;
            this.material = material;
        }
    }
    return {
        Group: Object3D, Mesh, BufferGeometry: Geometry,LinearSRGBColorSpace:'srgb-linear',SRGBColorSpace:'srgb',
        Float32BufferAttribute: class { constructor(array) { this.array = array; } },
        SphereGeometry: Geometry, ConeGeometry: Geometry, CylinderGeometry: Geometry,
        MeshLambertMaterial: class {
            constructor(options) { this.options = options; this.color = { r: 0.5, g: 0.5, b: 0.5 }; }
            dispose() {}
        },
    };
}

test('every catalog mutant appears in the benchmark and modeled types build selectable shapes', () => {
    const mounts=[];
    const context = vm.createContext({ THREE: threeStub(),mountLocationModel:(group,asset,dimensions)=>{mounts.push({group,asset,dimensions});return true;} });
    vm.runInContext(models.replace(/^import[^\n]*\n/gm, '').replaceAll('export ', ''), context);
    const all = Array.from(vm.runInContext('ALL_MUTANT_TYPES', context));
    const modeled = Array.from(vm.runInContext('MODELED_MUTANT_TYPES', context));
    const catalog = require('../app/data/world_rules.json').mutants.map(item => item.name);
    assert.deepEqual(all, catalog);
    assert.equal(new Set(modeled).size, modeled.length);
    assert.ok(modeled.length > 0 && modeled.length < all.length);
    for (const type of all) {
        const model = context.createMutantModel(type);
        if (!modeled.includes(type)) {
            assert.equal(model, null, `${type} should use the explicit fallback`);
            continue;
        }
        assert.equal(model.userData.isCharacter, true, type);
        assert.equal(model.userData.mutantModel, true, type);
        if(type==='Кабан'){
            assert.equal(mounts.length,1);
            assert.equal(mounts[0].asset.visual.modelPath,'/static/models/mutants/boar/wild-boar-light.glb');
            assert.ok(model.children[0].children[0].geometry.attributes.color,'исходная форма доступна до загрузки GLB');
            continue;
        }
        assert.equal(model.children.length, 1, `${type} should use one draw call`);
        assert.ok(model.children[0].geometry.attributes.color, type);
    }
    assert.match(benchmark, /ALL_MUTANT_TYPES\.forEach/);
    assert.match(benchmark, /createMutantModel\(type\) \|\| fallbackMutant\(\)/);
    assert.match(benchmark, /renderer\.info\.render\.calls/);
    assert.match(benchmark, /percentile\(frames, 0\.95\)/);
});

test('цвет Кабана соответствует выводу сцены, карты нормалей не перекодируются', () => {
    for(const colorSpace of ['srgb-linear','srgb']){
        let ready;
        const context=vm.createContext({THREE:threeStub(),mountLocationModel:(_root,_asset,_dimensions,callback)=>{ready=callback;return true;}});
        vm.runInContext(models.replace(/^import[^\n]*\n/gm,'').replaceAll('export ',''),context);
        const root=context.createMutantModel('Кабан',colorSpace),map={colorSpace:'srgb',needsUpdate:false},normalMap={colorSpace:''};
        const material={map,normalMap,needsUpdate:false},mesh={isMesh:true,material};
        ready({traverse:visit=>visit(mesh)});
        assert.equal(map.colorSpace,colorSpace);assert.equal(normalMap.colorSpace,'');
        assert.equal(map.needsUpdate,colorSpace!=='srgb');
        assert.equal(root.children.length,1);assert.equal(root.lastEvent.type,'model-ready');
        assert.ok(mesh.castShadow&&mesh.receiveShadow,'материал продолжает получать свет и отбрасывать тень');
    }
});

test('mutant type reaches initial state, spawns, live model and fog memory', () => {
    assert.match(read('app/sockets/location.py'), /'mutant_type': CombatService\._mutant_profile_name\(character\.data\)/);
    assert.match(read('app/lobbies/__init__.py'), /'mutant_type': CombatService\._mutant_profile_name\(character\.data\)/);
    assert.match(read('app/services/combat.py'), /'mutant_type': CombatService\._mutant_profile_name\(data\) if is_mutant else ''/);
    assert.match(read('app/static/js/main.js'), /s\.gm_hidden,\s*s\.mutant_type/);
    assert.match(read('app/static/js/main.js'), /data\.character\.gm_hidden,\s*data\.character\.mutant_type/);
    const scene = read('app/static/js/locationScene.js');
    assert.match(scene, /isMutant && createMutantModel\(mutantType, renderer.outputColorSpace\)/);
    assert.match(scene, /if \(characterModels\.has\(characterId\)\) \{\s*const old = characterModels\.get\(characterId\);\s*disposeObject\(old\.model\)/);
    assert.match(scene, /mutantType: entry\.mutantType \|\| ''/);
    assert.match(scene, /createMutantModel\(snapshot\.mutantType, renderer.outputColorSpace\)/);
});

test('benchmark isolates hidden anomaly animation from baseline and grass measurements', () => {
    assert.match(benchmark, /if \(localAnomalies\.visible\) animateAnomalyEffects\(anomalyRoots\.local, now\)/);
    assert.match(benchmark, /if \(worldAnomalies\.visible\) animateAnomalyEffects\(anomalyRoots\.world, now\)/);
    assert.match(benchmark, /createGrassDetailGroup\(THREE, tiles/);
});
