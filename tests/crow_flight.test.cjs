const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function fixture() {
    const THREE = await import('../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const mixers = [];
    const releases = [];
    let disposedMaterials = 0;
    class Mixer extends THREE.AnimationMixer {
        constructor(root) { super(root); this.samples = 0; mixers.push(this); }
        update(delta) { this.samples++; return super.update(delta); }
    }
    const mountLocationModel = (root, asset, dimensions, ready) => {
        assert.equal(asset.visual.modelPath, '/static/models/art/crow-flight.glb');
        const model = new THREE.Group();
        const material = new THREE.MeshBasicMaterial({transparent: true, depthWrite: false});
        material.addEventListener('dispose', () => disposedMaterials++);
        const wing = new THREE.Mesh(new THREE.BoxGeometry(1, .1, .3), material);
        wing.name = 'Wings';
        model.add(wing);
        const times = Array.from({length: 317}, (_, index) => index/30);
        model.animations = [new THREE.AnimationClip('root|TakeOff', 10.52, [
            new THREE.NumberKeyframeTrack('Wings.rotation[z]', times, times.map(time => Math.sin(time*6))),
        ])];
        ready(model);
        return true;
    };
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/crowFlight.js'), 'utf8')
        .replace(/^import[^\n]+\n/, '').replace('export function', 'function');
    const context = vm.createContext({console, mountLocationModel, releaseLocationModel: root => releases.push(root)});
    vm.runInContext(source, context);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, 16/9, .1, 100);
    camera.position.set(5, 2, 20);
    camera.lookAt(5, 2, 10);
    camera.updateMatrixWorld();
    const flight = context.createCrowFlight({...THREE, AnimationMixer: Mixer}, scene, {
        scale: 1, speed: 1, altitude: 2, spacing: 1, camera,
    });
    return {THREE, flight, scene, camera, mixers, releases, disposed: () => disposedMaterials};
}

test('стая использует четыре модели, готовый клип и смотрит по направлению полёта', async () => {
    const {THREE, flight, scene, mixers} = await fixture();
    flight.update(.1, 20, 20);
    const flock = scene.getObjectByName('Летящие вороны');
    assert.equal(flock.children.length, 4);
    assert.equal(mixers.length, 4);
    const material=flock.children[0].children[0].getObjectByName('Wings').material;
    assert.equal(material.transparent,false);
    assert.equal(material.depthWrite,true);
    assert.ok(mixers.every(mixer => mixer._actions[0].getClip().duration < 1.2));
    const bird = flock.children[0];
    const before = bird.position.x;
    flight.update(.2, 20, 20);
    assert.ok(bird.position.x > before);
    scene.updateMatrixWorld(true);
    assert.ok(bird.getWorldDirection(new THREE.Vector3()).x > .9);
    flight.dispose();
});

test('анимация за камерой пропускается и продолжает общее время при возвращении', async () => {
    const {flight, camera, mixers} = await fixture();
    camera.lookAt(5, 2, 100);
    camera.updateMatrixWorld();
    const before = mixers.reduce((total, mixer) => total+mixer.samples, 0);
    flight.update(1, 20, 20);
    assert.equal(mixers.reduce((total, mixer) => total+mixer.samples, 0), before);
    camera.lookAt(5, 2, 10);
    camera.updateMatrixWorld();
    flight.update(.1, 20, 20);
    assert.ok(mixers.reduce((total, mixer) => total+mixer.samples, 0) > before);
    assert.ok(mixers.some(mixer => mixer.time >= 1.1));
    flight.dispose();
});

test('выход со сцены освобождает экземпляры, материалы и проигрыватели стаи', async () => {
    const {flight, scene, mixers, releases, disposed} = await fixture();
    flight.update(.1, 20, 20);
    flight.dispose();
    assert.equal(scene.getObjectByName('Летящие вороны'), undefined);
    assert.equal(releases.length, 4);
    assert.equal(disposed(), 4);
    assert.ok(mixers.every(mixer => mixer._actions.length === 0));
});
