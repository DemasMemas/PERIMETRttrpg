// Декоративная дождевая вода: не меняет грунт, коллизии или стоимость перемещения.
export function createRainPuddles(THREE, scene, { bounds, surface, roof, terrainType }) {
    const capacity = 192;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createRadialGradient(32, 32, 7, 32, 32, 31);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(.7, 'rgba(255,255,255,.9)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.beginPath();
    for (let i = 0; i <= 48; i++) {
        const a = i / 48 * Math.PI * 2, r = 26 + 3 * Math.sin(a * 3) + 2 * Math.cos(a * 5);
        const x = 32 + Math.cos(a) * r, y = 32 + Math.sin(a) * r;
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.closePath(); ctx.fill();
    const texture = new THREE.CanvasTexture(canvas);
    const geometry = new THREE.CircleGeometry(1, 16).rotateX(-Math.PI / 2);
    const material = new THREE.MeshPhongMaterial({color: 0x6e858b, map: texture,
        transparent: true, opacity: .4, depthWrite: false, specular: 0xdce6e8, shininess: 90,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1});
    const mesh = new THREE.InstancedMesh(geometry, material, capacity);
    mesh.name = 'location-rain-puddles'; mesh.userData.locationWeather = true;
    mesh.count = 0; mesh.visible = false; mesh.receiveShadow = true; mesh.frustumCulled = false;
    mesh.raycast = () => {};
    scene.add(mesh);
    const pose = new THREE.Object3D();
    const eligible = new Set(['grass', 'asphalt', 'concrete', 'gravel', 'bare_dirt', 'dirt', 'ground']);
    const hash = (x, z, salt = 0) => { const n = Math.sin(x * 127.1 + z * 311.7 + salt * 71.3) * 43758.5453; return n - Math.floor(n); };
    let key = null, samples = [];
    return {
        update(strength, version) {
            const b = bounds(), cx = Math.max(0, Math.min(b.width, b.centerX ?? b.width / 2));
            const cz = Math.max(0, Math.min(b.height, b.centerZ ?? b.height / 2));
            const nextKey = `${version}:${Math.floor(cx / 8)}:${Math.floor(cz / 8)}:${strength}`;
            if (key === nextKey) return;
            key = nextKey; mesh.count = 0; samples = [];
            for (let z = Math.max(1, Math.floor(cz) - 22); z < Math.min(b.height - 1, cz + 22); z++) {
                for (let x = Math.max(1, Math.floor(cx) - 22); x < Math.min(b.width - 1, cx + 22); x++) {
                    if (mesh.count >= capacity || hash(x, z) > .08 + strength * .09 || !eligible.has(terrainType(x, z))) continue;
                    const px = x + .35 + hash(x, z, 1) * .3, pz = z + .35 + hash(x, z, 2) * .3;
                    const radius = .22 + .28 * hash(x, z, 3);
                    const h = surface(px, pz);
                    if (roof(px, pz) > h + .15) continue;
                    const probes = [h, surface(px - radius, pz), surface(px + radius, pz),
                        surface(px, pz - radius), surface(px, pz + radius)];
                    const flatness = Math.max(...probes) - Math.min(...probes);
                    if (flatness > .025) continue;
                    pose.position.set(px, Math.max(...probes) + .012, pz);
                    pose.rotation.y = hash(x, z, 4) * Math.PI;
                    pose.scale.set(radius, 1, radius * (.55 + .35 * hash(x, z, 5)));
                    pose.updateMatrix(); mesh.setMatrixAt(mesh.count++, pose.matrix);
                    if (samples.length < 3) samples.push({x: px, z: pz, height: pose.position.y, ground: h, flatness, radius});
                }
            }
            mesh.instanceMatrix.needsUpdate = true;
            material.opacity = .28 + .16 * strength;
            mesh.visible = mesh.count > 0;
        },
        hide() { mesh.visible = false; mesh.count = 0; key = null; samples = []; },
        diagnostics() { return {puddles: mesh.count, puddleSamples: samples, puddleDrawCalls: mesh.visible ? 1 : 0}; },
        dispose() { scene.remove(mesh); geometry.dispose(); material.dispose(); texture.dispose(); },
    };
}
