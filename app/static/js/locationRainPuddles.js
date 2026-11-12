const MAX_JOIN_RELIEF = .06;
const SIDES = [[1, 0], [0, 1], [-1, 0], [0, -1]];
const eligible = new Set(['grass', 'asphalt', 'concrete', 'concrete005', 'granite_tiles', 'gravel', 'bare_dirt',
    'dirt', 'ground', 'muddy_tracks', 'brown_mud', 'brown_mud_02', 'brown_mud_03', 'dry_mud_field_001']);
const hash = (x, z, salt = 0) => {
    const n = Math.sin(x * 127.1 + z * 311.7 + salt * 71.3) * 43758.5453;
    return n - Math.floor(n);
};

function findPool(pool) {
    while (pool.parent) pool = pool.parent;
    return pool;
}

export function joinRainPuddles(puddles, { surface, roof, terrainType }) {
    const byCell = new Map();
    const cells = puddles.map(puddle => {
        const cell = { ...puddle, links: Array(4).fill(null) };
        byCell.set(`${cell.x},${cell.z}`, cell);
        return cell;
    });
    let joins = 0;
    for (const a of cells) {
        // Каждую сторону проверяем один раз, сохраняя одинаковый профиль перемычки у обеих клеток.
        for (let side = 0; side < 2; side++) {
            const [dx, dz] = SIDES[side];
            const b = byCell.get(`${a.x + dx},${a.z + dz}`);
            if (!b) continue;
            const first = findPool(a), second = findPool(b);
            let low = Math.min(first.low, second.low), high = Math.max(first.high, second.high);
            if (high - low > MAX_JOIN_RELIEF) continue;
            const width = Math.min(.205, Math.min(a.depth, b.depth) * .88);
            const reach = width * 1.4 + .035;
            const acrossX = b.pz - a.pz, acrossZ = a.px - b.px;
            const span = Math.hypot(acrossX, acrossZ);
            let clear = true;
            for (let step = 0; step <= 8 && clear; step++) {
                for (const offset of [-reach, 0, reach]) {
                    const x = a.px + (b.px - a.px) * step / 8 + acrossX / span * offset;
                    const z = a.pz + (b.pz - a.pz) * step / 8 + acrossZ / span * offset;
                    const y = surface(x, z);
                    low = Math.min(low, y); high = Math.max(high, y);
                    if (!eligible.has(terrainType(Math.floor(x), Math.floor(z)))
                        || roof(x, z) > y + .15 || high - low > MAX_JOIN_RELIEF) {
                        clear = false;
                        break;
                    }
                }
            }
            if (!clear) continue;
            a.links[side] = [b.px - a.x, b.pz - a.z, width];
            b.links[side + 2] = [a.px - b.x, a.pz - b.z, width];
            first.low = low; first.high = high;
            if (first !== second) { second.parent = first; joins++; }
        }
    }
    return { cells: cells.map(({ parent, ...cell }, index) => ({ ...cell,
        level: findPool(cells[index]).high + .012 })), joins };
}

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
    const geometry = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
    const attribute = (name, size) => {
        const value = new THREE.InstancedBufferAttribute(new Float32Array(capacity * size), size)
            .setUsage(THREE.DynamicDrawUsage);
        geometry.setAttribute(name, value);
        return value;
    };
    const shape = attribute('rainPuddleShape', 4);
    const rotation = attribute('rainPuddleRotation', 2);
    const links = SIDES.map((_, index) => attribute(`rainPuddleLink${index}`, 3));
    const material = new THREE.MeshPhongMaterial({color: 0x6e858b, map: texture,
        transparent: true, opacity: .4, depthWrite: false, specular: 0xdce6e8, shininess: 90,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1});
    const rippleTime = { value: 0 };
    const varying = `
        varying vec4 vRainPuddleShape;
        varying vec4 vRainPuddleLocal;
        ${SIDES.map((_, index) => `varying vec3 vRainPuddleLink${index};`).join('\n')}`;
    material.onBeforeCompile = shader => {
        shader.uniforms.rainPuddleTime = rippleTime;
        shader.vertexShader = shader.vertexShader
            .replace('#include <common>', `#include <common>${varying}
                attribute vec4 rainPuddleShape;
                attribute vec2 rainPuddleRotation;
                ${SIDES.map((_, index) => `attribute vec3 rainPuddleLink${index};`).join('\n')}`)
            .replace('#include <begin_vertex>', `#include <begin_vertex>
                vRainPuddleShape = rainPuddleShape;
                vRainPuddleLocal = vec4(position.xz + 0.5, rainPuddleRotation);
                ${SIDES.map((_, index) => `vRainPuddleLink${index} = rainPuddleLink${index};`).join('\n')}`);
        shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>
                uniform float rainPuddleTime;
                ${varying}
                float rainPuddleBridge(vec2 relative, vec3 link) {
                    if (link.z <= 0.0) return 0.0;
                    vec2 segment = link.xy - vRainPuddleShape.xy;
                    float lengthSq = dot(segment, segment);
                    float t = clamp(dot(relative, segment) / lengthSq, 0.0, 1.0);
                    float arc = sin(t * 3.14159265);
                    float direction = abs(segment.x) > abs(segment.y) ? sign(segment.x) : sign(segment.y);
                    vec2 across = vec2(-segment.y, segment.x) * inversesqrt(lengthSq) * direction;
                    float bend = arc * 0.032 * sin(link.z * 71.0);
                    float width = link.z * (1.0 + 0.33 * arc + 0.07 * sin(t * 9.42477796));
                    return smoothstep(0.0, 0.055, width - length(relative - segment * t - across * bend));
                }
                float rainPuddleRing(vec2 offset, float phase) {
                    float radius = length(offset);
                    float antialias = max(fwidth(radius), 0.003);
                    float ring = 1.0 - smoothstep(0.006, 0.006 + antialias, abs(radius - phase * 0.15));
                    return ring * smoothstep(0.0, 0.12, phase) * (1.0 - phase)
                        * (1.0 - smoothstep(0.015, 0.055, antialias));
                }`)
            .replace('#include <map_fragment>', `
                vec2 relative = vRainPuddleLocal.xy - vRainPuddleShape.xy;
                float c = cos(vRainPuddleLocal.z), s = sin(vRainPuddleLocal.z);
                vec2 puddleUv = vec2(c * relative.x - s * relative.y, s * relative.x + c * relative.y)
                    / vRainPuddleShape.zw * 0.5 + 0.5;
                float edge = texture2D(map, clamp(puddleUv, 0.0, 1.0)).a;
                ${SIDES.map((_, index) => `edge = max(edge, rainPuddleBridge(relative, vRainPuddleLink${index}));`).join('\n')}
                float seed = vRainPuddleLocal.w;
                float ripple = rainPuddleRing(relative - vec2(seed * 0.12 - 0.06, 0.035),
                    fract(rainPuddleTime * 0.9 + seed));
                ripple += rainPuddleRing(relative - vec2(0.055, seed * 0.10 - 0.05),
                    fract(rainPuddleTime * 1.1 + seed * 2.0 + 0.45));
                if (edge <= 0.001) discard;
                diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.48, 0.58, 0.62), min(0.65, ripple));
                diffuseColor.a *= edge * (1.0 + 0.35 * min(1.0, ripple));`);
    };
    const mesh = new THREE.InstancedMesh(geometry, material, capacity);
    mesh.name = 'location-rain-puddles'; mesh.userData.locationWeather = true;
    mesh.count = 0; mesh.visible = false; mesh.receiveShadow = true; mesh.frustumCulled = false;
    mesh.raycast = () => {};
    scene.add(mesh);
    const matrix = new THREE.Matrix4();
    let key = null, samples = [], impacts = [], joined = 0, joinSample = null;
    return {
        update(strength, version, dt) {
            rippleTime.value = (rippleTime.value + dt) % 20;
            const b = bounds(), cx = Math.max(0, Math.min(b.width, b.centerX ?? b.width / 2));
            const cz = Math.max(0, Math.min(b.height, b.centerZ ?? b.height / 2));
            const nextKey = `${version}:${Math.floor(cx / 8)}:${Math.floor(cz / 8)}:${strength}`;
            if (key === nextKey) return;
            key = nextKey; samples = []; impacts = []; joinSample = null;
            const puddles = [];
            for (let z = Math.max(1, Math.floor(cz) - 22); z < Math.min(b.height - 1, cz + 22); z++) {
                for (let x = Math.max(1, Math.floor(cx) - 22); x < Math.min(b.width - 1, cx + 22); x++) {
                    if (puddles.length >= capacity || hash(x, z) > .08 + strength * .09 || !eligible.has(terrainType(x, z))) continue;
                    const px = x + .35 + hash(x, z, 1) * .3, pz = z + .35 + hash(x, z, 2) * .3;
                    const radius = Math.min(.22 + .28 * hash(x, z, 3),
                        px - x - .012, x + 1 - px - .012, pz - z - .012, z + 1 - pz - .012);
                    const h = surface(px, pz);
                    if (roof(px, pz) > h + .15) continue;
                    const probes = [h];
                    for (let direction = 0; direction < 8; direction++) {
                        const angle = direction * Math.PI / 4;
                        probes.push(surface(px + Math.cos(angle) * radius, pz + Math.sin(angle) * radius));
                    }
                    const high = Math.max(...probes), low = Math.min(...probes), flatness = high - low;
                    if (flatness > .025) continue;
                    puddles.push({ x, z, px, pz, high, low, ground: h, flatness, radius,
                        depth: radius * (.55 + .35 * hash(x, z, 5)),
                        angle: hash(x, z, 4) * Math.PI, seed: Math.fround(hash(x, z, 6)) });
                }
            }
            const result = joinRainPuddles(puddles, { surface, roof, terrainType });
            joined = result.joins;
            mesh.count = result.cells.length;
            for (const [index, cell] of result.cells.entries()) {
                const { x, z, px, pz, radius, depth, angle, seed, level, ground, flatness } = cell;
                mesh.setMatrixAt(index, matrix.makeTranslation(x + .5, level, z + .5));
                shape.setXYZW(index, px - x, pz - z, radius, depth);
                rotation.setXY(index, angle, seed);
                cell.links.forEach((link, side) => links[side].setXYZ(index, ...(link || [0, 0, 0])));
                impacts.push({ x: px + seed * .12 - .06, z: pz + .035, y: level, seed });
                const merged = cell.links.some(Boolean);
                if (samples.length < 3) samples.push({ x: px, z: pz, height: level, ground, flatness, radius, merged });
                if (merged) {
                    const link = cell.links.find(Boolean);
                    const mx = (px + x + link[0]) / 2, mz = (pz + z + link[1]) / 2;
                    const distance = (mx - cx) ** 2 + (mz - cz) ** 2;
                    if (!joinSample || distance < joinSample.distance) joinSample = { x: mx, z: mz, height: level, distance };
                }
            }
            for (const value of [shape, rotation, ...links]) value.needsUpdate = true;
            mesh.instanceMatrix.needsUpdate = true;
            material.opacity = .28 + .16 * strength;
            mesh.visible = mesh.count > 0;
        },
        get impacts() { return impacts; },
        get time() { return rippleTime.value; },
        hide() { mesh.visible = false; mesh.count = 0; key = null; samples = []; impacts = []; joined = 0; joinSample = null; },
        diagnostics() { return { puddles: mesh.count - joined, puddleJoins: joined, puddleJoinSample: joinSample,
            puddleSamples: samples, puddleDrawCalls: mesh.visible ? 1 : 0 }; },
        dispose() { scene.remove(mesh); geometry.dispose(); material.dispose(); texture.dispose(); },
    };
}
