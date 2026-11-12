import { mountLocationModel, releaseLocationModel } from './locationModelAssets.js';

const FORMATION = [[0, 0], [-.7, -.9], [-1.35, .65], [-2, -.4]];
const FLIGHT_ASSET = {
    visual: {
        renderMode: 'glb', modelPath: '/static/models/art/crow-flight.glb',
        authoredHeight: 100,
    },
};

export function createCrowFlight(THREE, scene, { scale, speed, altitude, spacing, camera }) {
    const flock = new THREE.Group();
    flock.name = 'Летящие вороны';
    flock.visible = false;
    scene.add(flock);
    const frustum = new THREE.Frustum();
    const viewProjection = new THREE.Matrix4();
    const birdBounds = new THREE.Sphere(new THREE.Vector3(), scale*1.2);
    const birds = FORMATION.map((_, index) => {
        const root = new THREE.Group();
        root.name = 'Ворона в полёте '+(index+1);
        root.raycast = () => {};
        flock.add(root);
        const bird = { root, model: null, mixer: null, lastAnimationAt: 0 };
        mountLocationModel(root, FLIGHT_ASSET, {height: scale}, model => {
            const source = model.animations[0];
            if (!source) throw new Error('У модели вороны отсутствует анимация');
            // Полный TakeOff содержит посадочную позу. Замыкаем готовый взмах после взлёта.
            const clip = THREE.AnimationUtils.subclip(source, 'CrowFlight', 242, 276, 30);
            bird.mixer = new THREE.AnimationMixer(model);
            const action = bird.mixer.clipAction(clip);
            action.play();
            bird.mixer.setTime(clip.duration*index/FORMATION.length);
            model.updateMatrixWorld(true);
            const bounds = new THREE.Box3().setFromObject(model);
            model.position.sub(bounds.getCenter(new THREE.Vector3()));
            model.traverse(child => {
                if (!child.isMesh) return;
                child.raycast = () => {};
                child.castShadow = false;
                child.receiveShadow = false;
                // Поза меняет границы скелета. Общая проверка сферы птицы выполняется ниже.
                child.frustumCulled = false;
                for (const material of [].concat(child.material)) {
                    material.color?.multiplyScalar(1.18);
                    material.transparent = false;
                    material.depthWrite = true;
                    material.alphaTest = .45;
                    material.side = THREE.DoubleSide;
                }
            });
            bird.model = model;
            root.add(model);
        }, error => {
            root.userData.assetLoadError = true;
            console.error('Не удалось загрузить летящую ворону', error);
        });
        return bird;
    });
    let elapsed = 0;
    let distance = null;
    return {
        update(delta, width, depth) {
            if (!(width > 0 && depth > 0)) { flock.visible = false; return; }
            elapsed += delta;
            distance = ((distance ?? width*.26)+speed*delta)%(width+spacing*20);
            const x = distance-spacing*4;
            flock.visible = x > -spacing*4 && x < width+spacing*4;
            if (!flock.visible) return;
            const z = depth*.48+Math.sin(elapsed*.21)*spacing*2;
            if (camera) frustum.setFromProjectionMatrix(viewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
            for (let index = 0; index < birds.length; index++) {
                const bird = birds[index];
                const {root, mixer} = bird;
                const [behind, beside] = FORMATION[index];
                root.position.set(x+behind*spacing, altitude+Math.sin(elapsed*.83+index)*scale,
                    z+beside*spacing);
                root.rotation.set(.06, Math.PI/2+Math.cos(elapsed*.21)*.09, Math.sin(elapsed*.7+index)*.11);
                root.scale.setScalar(1-index*.045);
                birdBounds.center.copy(root.position);
                root.visible = !camera || frustum.intersectsSphere(birdBounds);
                if (mixer && root.visible) {
                    mixer.update(elapsed-bird.lastAnimationAt);
                    bird.lastAnimationAt = elapsed;
                }
            }
        },
        dispose() {
            scene.remove(flock);
            for (const {root, model, mixer} of birds) {
                mixer?.stopAllAction();
                if (model) {
                    mixer?.uncacheRoot(model);
                    const materials = new Set();
                    model.traverse(child => [].concat(child.material || []).forEach(material => materials.add(material)));
                    materials.forEach(material => material.dispose());
                }
                releaseLocationModel(root);
            }
        },
    };
}
