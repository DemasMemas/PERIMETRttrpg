// Локальный дождь: один вызов отрисовки, реальные метры и физические крыши.
export function createLocationWeather(THREE, scene, { bounds, terrain, blockers }) {
    const capacity = 2400;
    const positions = new Float32Array(capacity * 6);
    const speeds = new Float32Array(capacity);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
    const material = new THREE.LineBasicMaterial({color: 0xa6b7bf, transparent: true, opacity: .38, depthWrite: false});
    const rain = new THREE.LineSegments(geometry, material);
    rain.name = 'location-weather-rain';
    rain.userData.locationWeather = true;
    rain.frustumCulled = false;
    rain.visible = false;
    scene.add(rain);
    const ray = new THREE.Raycaster();
    const down = new THREE.Vector3(0, -1, 0);
    const origin = new THREE.Vector3();
    let count = 0, strength = .5, version = null, columns = new Map(), heights = new Map();

    function index(nextVersion) {
        version = nextVersion;
        columns.clear(); heights.clear();
        for (const root of new Set(blockers())) {
            root.updateWorldMatrix(true, true);
            const box = new THREE.Box3().setFromObject(root);
            if (box.isEmpty()) continue;
            for (let x = Math.floor(box.min.x); x <= Math.floor(box.max.x); x++) {
                for (let z = Math.floor(box.min.z); z <= Math.floor(box.max.z); z++) {
                    const key = `${x}:${z}`;
                    if (!columns.has(key)) columns.set(key, []);
                    columns.get(key).push(root);
                }
            }
        }
    }
    function landing(x, z) {
        const key = `${Math.floor(x)}:${Math.floor(z)}`;
        if (heights.has(key)) return heights.get(key);
        const ground = terrain(Math.floor(x), Math.floor(z));
        origin.set(Math.floor(x) + .5, 1000, Math.floor(z) + .5);
        ray.set(origin, down);
        // Raycaster проверяет и скрытые этажи: скрытие для камеры не убирает крышу.
        const hits = ray.intersectObjects(columns.get(key) || [], true);
        const height = Math.max(ground, hits[0]?.point.y ?? ground);
        heights.set(key, height);
        return height;
    }
    function reset(i, initial = false) {
        const {width, height, centerX = width / 2, centerZ = height / 2} = bounds();
        const left = Math.max(0, centerX - 24), right = Math.min(width, centerX + 24);
        const top = Math.max(0, centerZ - 24), bottom = Math.min(height, centerZ + 24);
        const p = i * 6, x = left + Math.random() * (right - left), z = top + Math.random() * (bottom - top);
        const floor = landing(x, z);
        positions[p] = x; positions[p + 2] = z;
        positions[p + 1] = floor + (initial ? Math.random() * 18 : 18);
        speeds[i] = 9 + Math.random() * 5;
    }
    return {
        apply(settings) {
            const rainSetting = settings?.rain || {}, fog = settings?.fog || {};
            strength = Math.max(0, Math.min(1, Number(rainSetting.intensity) || 0));
            count = rainSetting.enabled ? Math.round(600 + 1800 * strength) : 0;
            rain.visible = count > 0;
            geometry.setDrawRange(0, count * 2);
            material.opacity = .22 + strength * .2;
            scene.fog = fog.enabled ? new THREE.FogExp2(0xaeb8bc,
                Number(fog.intensity) > .5 ? .008 + Number(fog.intensity) * .008 : .003 + Number(fog.intensity) * .009) : null;
            version = null;
        },
        update(dt, nextVersion) {
            if (!count) return;
            if (version !== nextVersion) {
                index(nextVersion);
                for (let i = 0; i < count; i++) reset(i, true);
            }
            const length = .18 + strength * .22;
            const area = bounds();
            for (let i = 0; i < count; i++) {
                const p = i * 6;
                positions[p + 1] -= speeds[i] * dt;
                const floor = landing(positions[p], positions[p + 2]) + .02;
                if (positions[p + 1] < floor || Math.abs(positions[p] - area.centerX) > 24
                    || Math.abs(positions[p + 2] - area.centerZ) > 24) reset(i);
                positions[p + 3] = positions[p] + .04;
                positions[p + 4] = Math.max(landing(positions[p], positions[p + 2]) + .02, positions[p + 1] - length);
                positions[p + 5] = positions[p + 2];
            }
            geometry.attributes.position.needsUpdate = true;
        },
        diagnostics(point) { return {attached: rain.parent === scene, drops: count, fogDensity: scene.fog?.density || 0, cachedColumns: heights.size, rainDrawCalls: count && rain.parent === scene ? 1 : 0,
            ...(point ? {surfaceHeight: landing(point.x, point.z), groundHeight: terrain(Math.floor(point.x), Math.floor(point.z))} : {})}; },
        dispose() { scene.remove(rain); geometry.dispose(); material.dispose(); scene.fog = null; columns.clear(); heights.clear(); },
    };
}
