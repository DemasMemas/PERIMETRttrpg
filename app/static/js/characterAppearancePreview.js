import * as THREE from 'three';
import {
    applyCharacterAppearanceModel, HUMAN_APPEARANCE_ASSET, HUMAN_APPEARANCE_DIMENSIONS,
} from './characterAppearance.js';
import { mountLocationModel, releaseLocationModel } from './locationModelAssets.js';

export function mountCharacterAppearancePreview(host, initialAppearance) {
    if (!host) return { update() {}, dispose() {} };
    let renderer;
    try {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
    } catch {
        host.textContent = 'Предпросмотр недоступен.';
        return { update() {}, dispose() {} };
    }
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.useLegacyLights = true;
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.domElement.setAttribute('aria-label', 'Трёхмерный вид персонажа');
    renderer.domElement.setAttribute('role', 'img');
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-0.8, 0.8, 1.05, -1.05, 0.1, 12);
    camera.position.set(1.85, 1.5, 4);
    camera.lookAt(0, 0.88, 0);
    scene.add(new THREE.HemisphereLight(0xe8edf3, 0x777066, 1.34));
    const key = new THREE.DirectionalLight(0xffedd7, 1.5);
    key.position.set(-2.5, 3, 3);
    scene.add(key);
    const root = new THREE.Group();
    scene.add(root);
    let appearance = initialAppearance;
    let signature = JSON.stringify(appearance);
    let model = null;
    let disposed = false;
    let renderedWidth = 0;
    let renderedHeight = 0;
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';

    function draw() {
        if (disposed || !model || !host.clientWidth || !host.clientHeight) return;
        const width = host.clientWidth, height = host.clientHeight;
        if (width !== renderedWidth || height !== renderedHeight) {
            const halfHeight = Math.max(1.03, 0.40 * height / width);
            const halfWidth = halfHeight * width / height;
            camera.left = -halfWidth;
            camera.right = halfWidth;
            camera.top = halfHeight;
            camera.bottom = -halfHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(width, height, false);
            renderedWidth = width;
            renderedHeight = height;
        }
        renderer.render(scene, camera);
    }

    const observer = new ResizeObserver(draw);
    observer.observe(host);
    let dragging = null;
    renderer.domElement.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        dragging = event.clientX;
        renderer.domElement.setPointerCapture(event.pointerId);
    });
    renderer.domElement.addEventListener('pointermove', event => {
        if (dragging === null) return;
        root.rotation.y += (event.clientX - dragging) * 0.015;
        dragging = event.clientX;
        draw();
    });
    renderer.domElement.addEventListener('lostpointercapture', () => { dragging = null; });

    mountLocationModel(root, HUMAN_APPEARANCE_ASSET, HUMAN_APPEARANCE_DIMENSIONS, instance => {
        model = instance;
        root.add(instance);
        applyCharacterAppearanceModel(model, appearance);
        host.replaceChildren(renderer.domElement);
        draw();
    }, () => { host.textContent = 'Не удалось загрузить модель.'; });

    return {
        update(value) {
            const next = JSON.stringify(value);
            if (next === signature) return;
            signature = next;
            appearance = value;
            if (model) applyCharacterAppearanceModel(model, appearance);
            draw();
        },
        dispose() {
            if (disposed) return;
            disposed = true;
            observer.disconnect();
            releaseLocationModel(root);
            const materials = new Set();
            root.traverse(child => {
                for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
                    if (material) materials.add(material);
                }
            });
            materials.forEach(material => material.dispose());
            renderer.dispose();
            renderer.forceContextLoss();
            renderer.domElement.remove();
        },
    };
}
