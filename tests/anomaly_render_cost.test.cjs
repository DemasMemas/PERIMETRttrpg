const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/anomalies.js'), 'utf8');

function fakeThree() {
    class Object3D {
        constructor() {
            this.children = [];
            this.userData = {};
            this.position = { x: 0, y: 0, z: 0, set(x, y, z) { Object.assign(this, { x, y, z }); } };
            this.rotation = { x: 0, y: 0, z: 0 };
            this.scale = {x:1,y:1,z:1,set(x,y,z){Object.assign(this,{x,y,z});},setScalar(value){this.set(value,value,value);}};
            this.visible = true;
        }
        add(child) { child.parent = this; this.children.push(child); }
        traverse(callback) { callback(this); this.children.forEach(child => child.traverse(callback)); }
        updateMatrix() { this.matrix = {}; }
    }
    class Mesh extends Object3D {
        constructor(geometry, material) { super(); this.geometry = geometry; this.material = material; }
    }
    class Geometry {
        constructor() { this.attributes = {}; }
        setAttribute(name, attribute) { this.attributes[name] = attribute; }
    }
    class Vector3 {
        constructor(x = 0, y = 0, z = 0) { Object.assign(this, { x, y, z }); }
    }
    class Sphere {
        constructor(center, radius) { Object.assign(this, { center, radius }); }
    }
    class BufferAttribute {
        constructor(array) { this.array = array; }
        setUsage() {}
        setXYZ(index, x, y, z) { this.array.set([x, y, z], index * 3); }
    }
    class InstancedMesh extends Mesh {
        constructor(geometry, material, count) {
            super(geometry, material);
            this.count = count;
            this.instanceMatrix = { setUsage() {} };
        }
        setMatrixAt() {}
    }
    return {
        Group: Object3D, Object3D, Mesh, InstancedMesh, LineSegments: Mesh,
        Sprite:class extends Object3D{constructor(material){super();this.material=material;}},
        SpriteMaterial:class{constructor(options){Object.assign(this,options);}},CanvasTexture:class{},
        BufferGeometry: Geometry, BufferAttribute, Vector3, Sphere,
        LineBasicMaterial: class {}, MeshBasicMaterial: class {}, Color: class {},
        CylinderGeometry: class {}, ConeGeometry: class {}, CircleGeometry: class {},
        SphereGeometry: class {}, TorusGeometry: class {}, IcosahedronGeometry: class {},
        DynamicDrawUsage: 1, AdditiveBlending: 2,
    };
}

test('electric and acid anomaly particles are batched without losing animation', () => {
    const context = vm.createContext({ THREE: fakeThree(), Math });
    vm.runInContext(source.replace(/^import[^\n]*\n/, '').replaceAll('export ', ''), context);
    const electric = context.createAnomalyEffect('electric');
    const electricVisual = electric.userData.anomalyVisual;
    assert.equal(electricVisual.children.length, 1);
    assert.equal(electricVisual.children[0].geometry.boundingSphere.radius, 1.5);
    assert.equal(electricVisual.children[0].frustumCulled, true);
    context.animateAnomalyEffects([electric], 1000);
    assert.equal(electricVisual.children[0].geometry.attributes.position.needsUpdate, true);
    const acid = context.createAnomalyEffect('acid');
    const acidVisual = acid.userData.anomalyVisual;
    assert.equal(acidVisual.children.length, 5);
    assert.deepEqual(Array.from(acidVisual.children.slice(3), mesh => mesh.count), [7, 4]);
    assert.ok(acidVisual.children.slice(3).every(mesh => mesh.geometry.boundingSphere.radius === 1.5));
    assert.ok(acidVisual.children.slice(3).every(mesh => mesh.frustumCulled));
    context.animateAnomalyEffects([acid], 1000);
    assert.ok(acidVisual.children.slice(3).every(mesh => mesh.instanceMatrix.needsUpdate));
});

test('аномалия сохраняет модель и фигурный маркер без текстовой метки', () => {
    const context = vm.createContext({ THREE: fakeThree(), Math });
    vm.runInContext(source.replace(/^import[^\n]*\n/,'').replaceAll('export ',''),context);
    const effect = context.createAnomalyEffect('fire', '#ff5500', 1, 'banya');
    assert.equal(effect.children.length, 2);
    assert.equal(effect.children[0], effect.userData.anomalyVisual);
    assert.ok(effect.children[1].userData.anomalyMarkerSpeed);
});
