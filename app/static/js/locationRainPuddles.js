const PATCH_SIZE = 6;
const SIDES = [[1, 0], [0, 1], [-1, 0], [0, -1]];
const paved = new Set(['asphalt', 'concrete', 'concrete005', 'granite_tiles']);
const eligible = new Set([...paved, 'grass', 'gravel', 'bare_dirt', 'dirt', 'ground',
    'muddy_tracks', 'brown_mud', 'brown_mud_02', 'brown_mud_03', 'dry_mud_field_001']);
const OUTLINE_PADDING = 1.27;
const hash = (x, z, salt) => {
    const n = Math.sin(x * 127.1 + z * 311.7 + salt * 71.3) * 43758.5453;
    return n - Math.floor(n);
};

// Граница имеет широкие неровности и меняется от лужи к луже.
function outline(angle, seed) {
    return 1 + .085 * Math.sin(3 * angle + seed * Math.PI * 2)
        + .04 * Math.sin(5 * angle - seed * Math.PI * 3);
}

function fitPuddle(x, z, dx, dz, { surface, roof }) {
    const merged = dx !== 0 || dz !== 0;
    const width = 1 + Math.abs(dx), depth = 1 + Math.abs(dz);
    const localX = width * .5 + (hash(x, z, 1) - .5) * .22;
    const localZ = depth * .5 + (hash(x, z, 2) - .5) * .22;
    const px = Math.min(x, x + dx) + localX;
    const pz = Math.min(z, z + dz) + localZ;
    const angle = merged ? (dz ? Math.PI / 2 : 0) + (hash(x, z, 3) - .5) * .30
        : hash(x, z, 3) * Math.PI;
    const cos = Math.cos(angle), sin = Math.sin(angle);
    let radius = merged ? .63 + .25 * hash(x, z, 4) : .22 + .16 * hash(x, z, 4);
    let across = merged ? .34 + .14 * hash(x, z, 5) : radius * (.68 + .28 * hash(x, z, 5));
    const scale = Math.min(1,
        (Math.min(localX, width - localX) - .015) / (OUTLINE_PADDING * Math.hypot(cos * radius, sin * across)),
        (Math.min(localZ, depth - localZ) - .015) / (OUTLINE_PADDING * Math.hypot(sin * radius, cos * across)));
    radius *= scale;
    across *= scale;

    const ground = surface(px, pz);
    let low = ground, high = ground;
    const maxRelief = merged ? .045 : .025;
    function hasSupport(sx, sz) {
        const y = surface(sx, sz);
        if (!Number.isFinite(y) || roof(sx, sz) > y + .15) return false;
        low = Math.min(low, y);
        high = Math.max(high, y);
        return high - low <= maxRelief;
    }
    if (!hasSupport(px, pz)) return null;
    if (merged && (!hasSupport(x + .5, z + .5) || !hasSupport(x + dx + .5, z + dz + .5))) return null;

    const seed = Math.fround(hash(x, z, 6));
    for (const ring of [.52, 1]) {
        for (let i = 0; i < 12; i++) {
            const a = i * Math.PI / 6, reach = ring * outline(a, seed);
            const u = Math.cos(a) * radius * reach, v = Math.sin(a) * across * reach;
            if (!hasSupport(px + u * cos - v * sin, pz + u * sin + v * cos)) return null;
        }
    }
    return { x: px, z: pz, ground, flatness: high - low, height: high + .009, radius, across, angle, seed,
        merged, edge: merged ? { x: dx ? x + .5 + dx * .5 : px, z: dz ? z + .5 + dz * .5 : pz } : null };
}

export function planRainPuddles({ width, height }, strength, terrain) {
    const pools = [];
    const { terrainType } = terrain;
    // Один водосбор на участок. При усилении дождя заполняются новые участки.
    for (let z0 = 0; z0 < height; z0 += PATCH_SIZE) {
        for (let x0 = 0; x0 < width; x0 += PATCH_SIZE) {
            const rain = hash(x0 / PATCH_SIZE, z0 / PATCH_SIZE, 7);
            if (rain > .34 + .54 * strength) continue;
            const x1 = Math.min(width, x0 + PATCH_SIZE), z1 = Math.min(height, z0 + PATCH_SIZE);
            const candidates = [];
            for (let z = z0; z < z1; z++) {
                for (let x = x0; x < x1; x++) {
                    const type = terrainType(x, z);
                    if (!eligible.has(type)) continue;
                    const bias = paved.has(type) ? .22 : type === 'grass' ? -.19 : 0;
                    candidates.push({ x, z, type, rank: hash(x, z, 8) + bias });
                }
            }
            candidates.sort((a, b) => b.rank - a.rank);
            for (const { x, z, type } of candidates.slice(0, 8)) {
                let pool = null;
                if (hash(x, z, 9) < .47) {
                    const first = Math.floor(hash(x, z, 10) * SIDES.length);
                    for (let i = 0; i < SIDES.length && !pool; i++) {
                        const [dx, dz] = SIDES[(first + i) % SIDES.length];
                        const nx = x + dx, nz = z + dz;
                        if (nx < x0 || nx >= x1 || nz < z0 || nz >= z1 || !eligible.has(terrainType(nx, nz))) continue;
                        pool = fitPuddle(x, z, dx, dz, terrain);
                    }
                }
                pool ||= fitPuddle(x, z, 0, 0, terrain);
                if (!pool) continue;
                if (type !== 'grass' || rain <= .20 + .46 * strength) pools.push(pool);
                break;
            }
        }
    }
    return pools;
}

// Декоративная дождевая вода на всей подлокации, одним вызовом отрисовки.
export function createRainPuddles(THREE, scene, { bounds, surface, roof, terrainType }) {
    const material = new THREE.MeshPhongMaterial({color: 0x414e50, specular: 0x889da2, shininess: 104,
        transparent: true, opacity: .8, depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1});
    material.extensions = { derivatives: true };
    const rippleTime = { value: 0 };
    const varying = `
        varying vec2 vRainPuddlePoint;
        varying vec2 vRainPuddleAxis;
        varying vec3 vRainPuddleShape;`;
    material.onBeforeCompile = shader => {
        shader.uniforms.rainPuddleTime = rippleTime;
        shader.vertexShader = shader.vertexShader
            .replace('#include <common>', `#include <common>${varying}
                attribute vec3 rainPuddleShape;`)
            .replace('#include <begin_vertex>', `#include <begin_vertex>
                vRainPuddlePoint = position.xz * ${OUTLINE_PADDING};
                vRainPuddleAxis = normalize(instanceMatrix[0].xz);
                vRainPuddleShape = rainPuddleShape;`);
        shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>
                uniform float rainPuddleTime;
                ${varying}
                vec3 rainPuddleWave(vec2 offset, float phase) {
                    float radius = length(offset);
                    float pixel = fwidth(radius);
                    float crest = (radius - phase * 0.18) / max(0.009, pixel);
                    float wave = exp(-crest * crest) * smoothstep(0.0, 0.12, phase) * (1.0 - phase)
                        * (1.0 - smoothstep(0.012, 0.041, pixel));
                    return vec3(offset / max(radius, 0.01) * crest * wave, wave);
                }`)
            .replace('#include <map_fragment>', `
                float radius = length(vRainPuddlePoint);
                float angle = radius > 0.001 ? atan(vRainPuddlePoint.y, vRainPuddlePoint.x) : 0.0;
                float seed = vRainPuddleShape.z;
                float edge = 1.0 + 0.085 * sin(3.0 * angle + seed * 6.2831853)
                    + 0.04 * sin(5.0 * angle - seed * 9.42477796) - radius;
                vec2 metres = vRainPuddlePoint * vRainPuddleShape.xy;
                float grainPhase = dot(metres, vec2(113.0, 87.0));
                float grain = sin(grainPhase + 1.7 * sin(metres.y * 106.0))
                    * (1.0 - smoothstep(0.45, 2.3, fwidth(grainPhase)));
                float rimWidth = min(0.032, 0.13 * vRainPuddleShape.y);
                float shore = edge * vRainPuddleShape.y + 0.0035 * grain;
                float wet = smoothstep(-rimWidth, 0.0, shore);
                float cover = smoothstep(0.0, max(0.018, fwidth(shore)), shore);
                float puddleDepth = smoothstep(0.012, 0.145,
                    shore * (0.92 + 0.12 * sin(dot(metres, vec2(9.0, 6.0)) + seed * 5.0)));
                vec3 waves = rainPuddleWave(metres, fract(rainPuddleTime * 0.9 + seed));
                waves += 0.5 * rainPuddleWave(metres - vec2(0.072, -0.04),
                    fract(rainPuddleTime * 1.1 + seed * 2.0 + 0.45));
                if (wet <= 0.001) discard;
                // У кромки читается грунт, в центре вода чуть мутнее и темнее.
                diffuseColor.rgb *= mix(vec3(0.52, 0.45, 0.38), vec3(0.81, 0.89, 0.92), puddleDepth);
                diffuseColor.a *= wet * (0.105 + cover * (0.105 + 0.22 * puddleDepth));`)
            .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
                vec2 along = vRainPuddleAxis, across = vec2(-along.y, along.x);
                vec2 slope = (along * waves.x + across * waves.y) * 0.26 * cover;
                normal = normalize(normal + (viewMatrix * vec4(slope.x, 0.0, slope.y, 0.0)).xyz);`)
            .replace('#include <opaque_fragment>', `
                float puddleView = clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0);
                float fresnel = 0.025 + 0.66 * pow(1.0 - puddleView, 4.0);
                float daylight = clamp(max(max(ambientLightColor.r, ambientLightColor.g),
                    ambientLightColor.b) * 0.75, 0.03, 1.0);
                vec3 skyTint = mix(vec3(0.027, 0.048, 0.072), vec3(0.34, 0.47, 0.52), daylight);
                float glint = cover * (0.08 + fresnel + min(0.16, waves.z * 0.18));
                outgoingLight = mix(outgoingLight, skyTint, glint);
                diffuseColor.a += cover * fresnel * 0.15;
                #include <opaque_fragment>`);
    };

    const matrix = new THREE.Matrix4(), position = new THREE.Vector3(), scale = new THREE.Vector3();
    const rotation = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
    const impactRows = [];
    let mesh = null, key = null, pools = [], joined = 0, joinSample = null;
    function disposeMesh() {
        if (!mesh) return;
        scene.remove(mesh);
        mesh.geometry.dispose();
        mesh.dispose();
    }
    function ensureCapacity(count) {
        if (mesh?.instanceMatrix.count >= count) return;
        disposeMesh();
        const geometry = new THREE.PlaneGeometry(2, 2).rotateX(-Math.PI / 2);
        geometry.setAttribute('rainPuddleShape', new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3));
        mesh = new THREE.InstancedMesh(geometry, material, count);
        mesh.name = 'location-rain-puddles';
        mesh.userData.locationWeather = true;
        mesh.receiveShadow = true;
        mesh.raycast = () => {};
        scene.add(mesh);
    }

    return {
        update(strength, version, dt) {
            rippleTime.value = (rippleTime.value + dt) % 20;
            const { width, height } = bounds();
            const nextKey = `${version}:${width}:${height}:${strength}`;
            if (key === nextKey) return;
            key = nextKey;
            pools = planRainPuddles({ width, height }, strength, { surface, roof, terrainType });
            joined = 0;
            joinSample = null;
            impactRows.length = 0;
            for (const { x, z, height: y, seed } of pools) {
                const row = (impactRows[Math.floor(z / PATCH_SIZE)] ??= []);
                row[Math.floor(x / PATCH_SIZE)] = { x, z, y, seed };
            }
            if (!pools.length) {
                if (mesh) mesh.visible = false;
                return;
            }
            ensureCapacity(pools.length);
            const shape = mesh.geometry.getAttribute('rainPuddleShape');
            mesh.count = pools.length;
            for (const [i, pool] of pools.entries()) {
                position.set(pool.x, pool.height, pool.z);
                rotation.setFromAxisAngle(up, -pool.angle);
                scale.set(pool.radius * OUTLINE_PADDING, 1, pool.across * OUTLINE_PADDING);
                mesh.setMatrixAt(i, matrix.compose(position, rotation, scale));
                shape.setXYZ(i, pool.radius, pool.across, pool.seed);
                if (pool.merged) {
                    joined++;
                    const { x, z } = pool.edge;
                    const distance = (x - width / 2) ** 2 + (z - height / 2) ** 2;
                    if (!joinSample || distance < joinSample.distance) joinSample = { x, z, height: pool.height, distance };
                }
            }
            shape.needsUpdate = true;
            mesh.instanceMatrix.needsUpdate = true;
            mesh.computeBoundingSphere();
            material.opacity = .65 + .25 * strength;
            mesh.visible = true;
        },
        *impactsNear(x, z, radius) {
            const left = Math.max(0, Math.floor((x - radius) / PATCH_SIZE));
            const right = Math.floor((x + radius) / PATCH_SIZE);
            const top = Math.max(0, Math.floor((z - radius) / PATCH_SIZE));
            const bottom = Math.min(impactRows.length - 1, Math.floor((z + radius) / PATCH_SIZE));
            // Обход по порядку водосборов сохраняет приоритет капель при достижении лимита.
            for (let rowIndex = top; rowIndex <= bottom; rowIndex++) {
                const row = impactRows[rowIndex];
                if (!row) continue;
                const end = Math.min(right, row.length - 1);
                for (let column = left; column <= end; column++) {
                    const impact = row[column];
                    if (impact && Math.abs(impact.x - x) <= radius && Math.abs(impact.z - z) <= radius) yield impact;
                }
            }
        },
        get time() { return rippleTime.value; },
        hide() {
            if (mesh) mesh.visible = false;
            key = null; pools = []; impactRows.length = 0; joined = 0; joinSample = null;
        },
        diagnostics() { return { puddles: pools.length, puddleJoins: joined, puddleJoinSample: joinSample,
            puddleSamples: pools.slice(0, 3), puddleDrawCalls: mesh?.visible ? 1 : 0 }; },
        dispose() { disposeMesh(); material.dispose(); },
    };
}
