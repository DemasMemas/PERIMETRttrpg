// Локальный дождь: один вызов отрисовки, реальные метры и физические крыши.
import { createRainPuddles } from './locationRainPuddles.js';
export function createLocationWeather(THREE, scene, { bounds, terrain, blockers, surface = terrain, terrainType = () => 'grass' }) {
    const capacity = 2400;
    const impactCapacity = 48;
    const positions = new Float32Array((capacity + impactCapacity) * 6);
    const speeds = new Float32Array(capacity);
    const floors = new Float32Array(capacity);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
    const colors = new Float32Array((capacity + impactCapacity) * 8).fill(1);
    for (let i = 0; i < capacity + impactCapacity; i++) colors[i * 8 + 3] = .25;
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 4));
    const material = new THREE.LineBasicMaterial({color: 0xb8cbd4, vertexColors: true,
        transparent: true, opacity: .42, depthWrite: false});
    const rain = new THREE.LineSegments(geometry, material);
    rain.name = 'location-weather-rain';
    rain.userData.locationWeather = true;
    rain.frustumCulled = false;
    rain.visible = false;
    scene.add(rain);
    const fogState = new THREE.FogExp2(0xaeb8bc, 0);
    scene.fog = fogState;
    let fogStrength = null;
    let localFogDensity = 0;
    const worldFogColor = fogState.color.clone();
    const localFogColor = fogState.color.clone();
    const ray = new THREE.Raycaster();
    const down = new THREE.Vector3(0, -1, 0);
    const origin = new THREE.Vector3();
    let count = 0, strength = .5, version = null, columns = new Map(), heights = new Map();
    let impactDrops = 0, nearDrops = 0;
    const puddles = createRainPuddles(THREE, scene, {bounds, surface, roof: landing, terrainType});

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
    function reset(i, {width, height, centerX, centerZ}, radius, initial = false) {
        const left = Math.max(0, centerX - radius), right = Math.min(width, centerX + radius);
        const top = Math.max(0, centerZ - radius), bottom = Math.min(height, centerZ + radius);
        const p = i * 6, x = left + Math.random() * (right - left), z = top + Math.random() * (bottom - top);
        positions[p] = x; positions[p + 2] = z;
        const floor = landing(positions[p], positions[p + 2]);
        floors[i] = floor + .02;
        const fallHeight = Math.min(18, radius + 3);
        positions[p + 1] = floor + fallHeight * (initial ? Math.random() : 1);
        speeds[i] = 9 + Math.random() * 5;
        return floors[i];
    }
    return {
        setLocalFog(density, color) {
            localFogDensity = density;
            localFogColor.set(color);
        },
        apply(settings) {
            const rainSetting = settings?.rain || {}, fog = settings?.fog || {};
            strength = Math.max(0, Math.min(1, Number(rainSetting.intensity) || 0));
            count = rainSetting.enabled ? Math.round(600 + 1800 * strength) : 0;
            if (!count) puddles.hide();
            rain.visible = count > 0;
            geometry.setDrawRange(0, count * 2);
            material.opacity = .30 + strength * .28;
            if (!count) impactDrops = nearDrops = 0;
            fogStrength = fog.enabled ? Math.max(0, Math.min(1, Number(fog.intensity ?? .5))) : null;
            version = null;
        },
        update(dt, nextVersion) {
            // Масштаб камеры не должен вымывать туман: сохраняем оптическую плотность у точки обзора.
            const area = bounds();
            area.centerX = Math.max(0, Math.min(area.width, area.centerX ?? area.width / 2));
            area.centerZ = Math.max(0, Math.min(area.height, area.centerZ ?? area.height / 2));
            const distance = Math.max(4, Number(area.viewDistance) || 40);
            const worldDensity = fogStrength === null ? 0 : (.15 + 1.45 * fogStrength) / distance;
            fogState.density = Math.max(localFogDensity, worldDensity);
            fogState.color.copy(localFogDensity > worldDensity ? localFogColor : worldFogColor);
            if (!count) return;
            const nearRadius = Math.max(2.25, Math.min(24, distance * .43));
            nearDrops = nearRadius < 15 ? Math.min(count, Math.round(18 + nearRadius * 4 + 22 * strength)) : 0;
            if (version !== nextVersion) {
                index(nextVersion);
                for (let i = 0; i < count; i++) reset(i, area, i < nearDrops ? nearRadius : 24, true);
            }
            puddles.update(strength, nextVersion, dt);
            const length = .18 + strength * .22;
            for (let i = 0; i < count; i++) {
                const p = i * 6;
                positions[p + 1] -= speeds[i] * dt;
                const radius = i < nearDrops ? nearRadius : 24;
                let floor = floors[i];
                if (positions[p + 1] < floor || (i < nearDrops && positions[p + 1] > floor + radius + 3)
                    || Math.abs(positions[p] - area.centerX) > radius
                    || Math.abs(positions[p + 2] - area.centerZ) > radius) floor = reset(i, area, radius, true);
                positions[p + 3] = positions[p] + .09;
                positions[p + 4] = Math.max(floor, positions[p + 1] - length);
                positions[p + 5] = positions[p + 2] + .06;
            }
            impactDrops = 0;
            const impactRadius = Math.min(10, nearRadius + 2);
            for (const impact of puddles.impacts) {
                if (impactDrops >= impactCapacity) break;
                if (Math.abs(impact.x - area.centerX) > impactRadius || Math.abs(impact.z - area.centerZ) > impactRadius) continue;
                const untilImpact = (1 - ((puddles.time * .9 + impact.seed) % 1)) / .9;
                if (untilImpact > .27) continue;
                const p = (count + impactDrops++) * 6;
                positions[p + 3] = impact.x - .22 * untilImpact;
                positions[p + 4] = impact.y + 10 * untilImpact;
                positions[p + 5] = impact.z - .14 * untilImpact;
                positions[p] = positions[p + 3] - .10;
                positions[p + 1] = positions[p + 4] + .34;
                positions[p + 2] = positions[p + 5] - .065;
            }
            geometry.setDrawRange(0, (count + impactDrops) * 2);
            geometry.attributes.position.needsUpdate = true;
        },
        diagnostics(point) { return {attached: rain.parent === scene, drops: count, fogDensity: scene.fog?.density || 0, cachedColumns: heights.size, rainDrawCalls: count && rain.parent === scene ? 1 : 0,
            fogStrength, nearDrops, impactDrops, ...puddles.diagnostics(),
            ...(point ? {surfaceHeight: landing(point.x, point.z), groundHeight: terrain(Math.floor(point.x), Math.floor(point.z))} : {})}; },
        dispose() { scene.remove(rain); geometry.dispose(); material.dispose(); puddles.dispose(); scene.fog = null; columns.clear(); heights.clear(); },
    };
}
