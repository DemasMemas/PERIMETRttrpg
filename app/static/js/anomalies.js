import * as THREE from 'three';

function material(color, opacity = 1) {
    return new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, blending: THREE.AdditiveBlending, depthWrite: false });
}

function anomalyLabel(text) {
    if (!text) return null;
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = '700 30px Georgia, serif';
    const width = Math.min(620, Math.ceil(context.measureText(text).width + 38));
    canvas.width = Math.max(160, width);
    canvas.height = 58;
    context.font = '700 30px Georgia, serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillStyle = 'rgba(10, 12, 9, 0.78)';
    context.fillRect(0, 4, canvas.width, 50);
    context.strokeStyle = 'rgba(191, 167, 111, 0.72)';
    context.strokeRect(1, 5, canvas.width - 2, 48);
    context.fillStyle = '#e8dfc6';
    context.fillText(text, canvas.width / 2, 30, canvas.width - 24);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(canvas),
        transparent: true,
        depthTest: true,
        depthWrite: false,
    }));
    sprite.scale.set(Math.max(1.2, canvas.width / 150), 0.38, 1);
    sprite.userData.anomalyLabel=true;
    sprite.userData.anomalyLabelText=text;
    sprite.userData.labelBaseScale=[sprite.scale.x,sprite.scale.y];
    sprite.position.y = 2.25;
    sprite.renderOrder = 20;
    return sprite;
}

function anomalyKeyMarker(key, color) {
    if (!key) return null;
    const hash = [...key].reduce((value, char) => ((value * 31) + char.charCodeAt(0)) >>> 0, 7);
    const sides = 3 + (hash % 6);
    const marker = new THREE.Mesh(
        new THREE.TorusGeometry(0.5 + ((hash >> 3) % 4) * 0.04, 0.025, 5, sides),
        material(color, 0.7),
    );
    marker.rotation.x = Math.PI / 2;
    marker.position.y = 0.12 + ((hash >> 6) % 4) * 0.08;
    marker.userData.anomalyMarkerSpeed = 0.25 + ((hash >> 8) % 7) * 0.08;
    return marker;
}

function createLightningLines(color) {
    const geometry = new THREE.BufferGeometry();
    const positions = new THREE.BufferAttribute(new Float32Array(5 * 6 * 2 * 3), 3);
    positions.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('position', positions);
    geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1.5);
    const lines = new THREE.LineSegments(geometry,
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 }));
    lines.userData.lightningSeeds = Array.from({ length: 5 }, () => Math.random() * 10);
    lines.frustumCulled = true;
    return lines;
}

function updateLightning(lines, time) {
    const positions = lines.geometry.attributes.position;
    lines.userData.lightningSeeds.forEach((seed, index) => {
        const angle = time * 1.8 + index * 2.1 + seed;
        const endX = Math.cos(angle) * 0.5;
        const endY = 0.25 + Math.sin(time + index) * 0.25;
        const endZ = Math.sin(angle) * 0.5;
        let previousX = 0;
        let previousY = 0;
        let previousZ = 0;
        for (let step = 1; step <= 6; step++) {
            const t = step / 6;
            const x = endX * t + (step < 6 ? Math.sin(time * 12 + step * 9 + index) * 0.12 : 0);
            const y = endY * t + (step < 6 ? Math.cos(time * 13 + step * 7 + index) * 0.1 : 0);
            const z = endZ * t + (step < 6 ? Math.cos(time * 11 + step * 5 + index) * 0.12 : 0);
            const vertex = (index * 6 + step - 1) * 2;
            positions.setXYZ(vertex, previousX, previousY, previousZ);
            positions.setXYZ(vertex + 1, x, y, z);
            previousX = x;
            previousY = y;
            previousZ = z;
        }
    });
    positions.needsUpdate = true;
}

const particleDummy = new THREE.Object3D();
const labelWorldScale = new THREE.Vector3();

function updateAcidParticles(mesh, time) {
    const { phases, radii, vapor } = mesh.userData.acidParticles;
    for (let index = 0; index < phases.length; index++) {
        const rise = (time * (vapor ? 0.18 : 0.45) + phases[index]) % 1;
        const angle = phases[index] + (vapor ? time : 0);
        particleDummy.position.set(Math.cos(angle) * radii[index],
            vapor ? 0.25 + rise * 0.7 : 0.08 + rise * 0.42,
            Math.sin(angle) * radii[index]);
        particleDummy.scale.setScalar(vapor ? 0.7 + rise * 1.4 : 1 - rise * 0.55);
        particleDummy.updateMatrix();
        mesh.setMatrixAt(index, particleDummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
}

function createAcidParticles(count, radius, color, opacity, vapor = false) {
    const geometry = new THREE.SphereGeometry(radius, vapor ? 8 : 6, vapor ? 6 : 5);
    geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1.5);
    const mesh = new THREE.InstancedMesh(
        geometry,
        material(color, opacity), count);
    mesh.userData.acidParticles = {
        vapor,
        phases: Array.from({ length: count }, () => Math.random()),
        radii: Array.from({ length: count }, () => Math.random() * (vapor ? 0.25 : 0.35) + (vapor ? 0.15 : 0)),
    };
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.frustumCulled = true;
    updateAcidParticles(mesh, 0);
    return mesh;
}

export function createAnomalyEffect(type = 'electric', color = '#00ffff', scale = 1, key = '', name = '') {
    const group = new THREE.Group();
    group.userData.anomalyEffect = { type, phase: Math.random() * Math.PI * 2, scale };
    const visual = new THREE.Group();
    group.userData.anomalyVisual = visual;
    group.add(visual);
    const tint = new THREE.Color(color);

    if (type === 'fire') {
        const base = new THREE.Mesh(new THREE.CylinderGeometry(0.52, 0.68, 0.1, 12), material(0xb51c00, 0.9));
        base.position.y = 0.04;
        visual.add(base);
        [
            { radius: 0.48, height: 2.15, color: 0xff2600, opacity: 0.45 },
            { radius: 0.34, height: 1.75, color: 0xff7300, opacity: 0.7 },
            { radius: 0.18, height: 1.28, color: 0xffff9a, opacity: 0.92 }
        ].forEach((layer, index) => {
            const flame = new THREE.Mesh(new THREE.ConeGeometry(layer.radius, layer.height, 12), material(layer.color, layer.opacity));
            flame.position.y = layer.height / 2;
            flame.userData.flame = { index, height: layer.height };
            visual.add(flame);
        });
    } else if (type === 'electric') {
        visual.position.y = 0.62;
        visual.add(createLightningLines(0xd6f6ff));
    } else if (type === 'acid') {
        [0.58, 0.43, 0.27].forEach((radius, index) => {
            const puddle = new THREE.Mesh(new THREE.CircleGeometry(radius, 28), material(index ? 0x8dff3d : tint, 0.34 + index * 0.12));
            puddle.rotation.x = -Math.PI / 2;
            puddle.position.y = 0.025 + index * 0.012;
            puddle.userData.puddle = index;
            visual.add(puddle);
        });
        visual.add(createAcidParticles(7, 0.06, 0xb6ff8a, 0.7));
        visual.add(createAcidParticles(4, 0.12, 0x8cff6a, 0.18, true));
    } else if (type === 'radiation') {
        const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.3, 1), material(0xb8e336, 0.35));
        core.position.y = 0.65;
        visual.add(core);
        for (let index = 0; index < 3; index++) {
            const ring = new THREE.Mesh(new THREE.TorusGeometry(0.35 + index * 0.12, 0.018, 6, 28), material(0xd9ff61, 0.45));
            ring.position.y = 0.65;
            ring.rotation.set(index * 0.7, index * 1.1, index * 0.45);
            ring.userData.radiationRing = index;
            visual.add(ring);
        }
    } else if (type === 'psi') {
        for (let index = 0; index < 4; index++) {
            const shell = new THREE.Mesh(new THREE.SphereGeometry(0.2 + index * 0.11, 16, 10), material(0x68a0ff, 0.12));
            shell.position.y = 0.7;
            shell.userData.psiShell = index;
            visual.add(shell);
        }
    } else {
        const bubble = new THREE.Mesh(new THREE.SphereGeometry(0.38, 20, 14), material(tint, 0.22));
        bubble.position.y = 0.68;
        bubble.userData.voidBubble = true;
        visual.add(bubble);
        for (let index = 0; index < 3; index++) {
            const ring = new THREE.Mesh(new THREE.TorusGeometry(0.28 + index * 0.07, 0.018, 6, 24), material(0xd8ccff, 0.55));
            ring.rotation.x = Math.PI / 2;
            ring.position.y = 0.68;
            ring.userData.voidRing = index;
            visual.add(ring);
        }
    }
    const marker = anomalyKeyMarker(key, tint);
    if (marker) group.add(marker);
    const label = anomalyLabel(name);
    if (label) group.add(label);
    group.scale.setScalar(scale);
    return group;
}

export function animateAnomalyEffects(roots, time) {
    roots.forEach(root => root?.traverse(node => {
        const effect = node.userData.anomalyEffect;
        if (!effect || !node.visible) return;
        if (node.parent?.isLOD && typeof node.parent.getCurrentLevel === 'function' && node.parent.levels?.[node.parent.getCurrentLevel()]?.object !== node) return;
        const t = time * 0.001 + effect.phase;
        const visual = node.userData.anomalyVisual || node;
        node.children.forEach(child=>{
            if(!child.userData.anomalyLabel)return;
            node.getWorldScale(labelWorldScale);
            const base=child.userData.labelBaseScale;
            child.scale.set(base[0]/Math.max(.0001,Math.abs(labelWorldScale.x)),base[1]/Math.max(.0001,Math.abs(labelWorldScale.y)),1);
        });
        node.children.forEach(child => {
            if (child.userData.anomalyMarkerSpeed) child.rotation.z = t * child.userData.anomalyMarkerSpeed;
        });
        if (effect.type === 'fire') {
            const burst = Math.max(0.035, Math.max(0, Math.sin(t * 0.62)) ** 10);
            visual.children.forEach(child => {
                if (!child.userData.flame) return;
                const flame = child.userData.flame;
                child.position.y = flame.height * 0.5 * burst;
                child.scale.set(1 + Math.sin(t * 4 + flame.index) * 0.05, burst, 1 + Math.cos(t * 3 + flame.index) * 0.05);
            });
        } else if (effect.type === 'electric') {
            visual.children.forEach((child, index) => {
                if (child.userData.lightningSeeds) updateLightning(child, t);
            });
            visual.rotation.y = t * 0.8;
        } else if (effect.type === 'acid') {
            visual.children.forEach(child => {
                if (child.userData.puddle !== undefined) {
                    const pulse = 1 + Math.sin(t * 1.5 + child.userData.puddle) * 0.06;
                    child.scale.set(pulse, 1, pulse);
                    child.rotation.z = t * 0.12 * (child.userData.puddle + 1);
                }
                if (child.userData.acidParticles) updateAcidParticles(child, t);
            });
        } else if (effect.type === 'radiation') {
            visual.children.forEach(child => {
                if (child.userData.radiationRing === undefined) return;
                child.rotation.x += 0.004 * (child.userData.radiationRing + 1);
                child.rotation.y += 0.006 * (child.userData.radiationRing + 1);
            });
        } else if (effect.type === 'psi') {
            visual.children.forEach(child => {
                if (child.userData.psiShell === undefined) return;
                const pulse = 0.8 + ((Math.sin(t * 2 + child.userData.psiShell) + 1) * 0.18);
                child.scale.setScalar(pulse);
            });
        } else {
            const pulse = 1 + Math.sin(t * 2.5) * 0.16;
            visual.scale.setScalar(pulse);
            visual.children.forEach(child => {
                if (child.userData.voidRing !== undefined) {
                    child.rotation.z = t * (0.8 + child.userData.voidRing * 0.3);
                    child.position.y = 0.68 + Math.sin(t * 2 + child.userData.voidRing) * 0.12;
                }
                if (child.userData.voidBubble) child.material.opacity = 0.18 + Math.sin(t * 3) * 0.08;
            });
        }
    }));
}
