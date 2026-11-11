// Подключается только к модулю изолированного браузерного сценария №105.
let terrainVisibilitySample = null;

function measuredTerrainLineHit(...args) {
    const sample = terrainVisibilitySample;
    if (!sample) return terrainLineHit(...args);
    sample.rays++;
    if (sample.bypass) return null;
    const started = performance.now();
    try {
        return terrainLineHit(...args);
    } finally {
        sample.terrainMs += performance.now() - started;
    }
}

export async function measureTerrainVisibilityForTest(characterId) {
    if (gmVisionCharacterId !== null) throw Error('Для замера нужен обычный обзор ГМа');
    const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
    const stats = values => {
        const sorted = [...values].sort((a, b) => a - b);
        const percentile = n => +sorted[Math.ceil(sorted.length * n) - 1].toFixed(2);
        return {median: percentile(0.5), p95: percentile(0.95)};
    };
    const measure = async (refresh, bypass) => {
        const frameMs = [], refreshMs = [], terrainMs = [], rays = [];
        const sample = {bypass, rays: 0, terrainMs: 0};
        terrainVisibilitySample = sample;
        for (let i = 0; i < 12; i++) {
            await nextFrame();
            if (refresh) applyFogOfWar();
        }
        let previous = await nextFrame();
        for (let i = 0; i < 60; i++) {
            sample.rays = 0;
            sample.terrainMs = 0;
            if (refresh) {
                const started = performance.now();
                applyFogOfWar();
                refreshMs.push(performance.now() - started);
            }
            const timestamp = await nextFrame();
            frameMs.push(timestamp - previous);
            previous = timestamp;
            terrainMs.push(sample.terrainMs);
            rays.push(sample.rays);
        }
        return {refresh, bypass, frames: frameMs.length, frameMs: stats(frameMs),
            fogCpuMs: refresh ? stats(refreshMs) : null, terrainCpuMs: stats(terrainMs),
            rays: stats(rays), totalRays: rays.reduce((sum, count) => sum + count, 0)};
    };
    toggleGmVision(characterId);
    try {
        const runs = [];
        for (const [refresh, bypass] of [[false, false], [true, false], [true, true], [true, true], [true, false]]) {
            runs.push(await measure(refresh, bypass));
        }
        return {viewport: [innerWidth, innerHeight], devicePixelRatio, observers: 1, runs};
    } finally {
        terrainVisibilitySample = null;
        toggleGmVision();
    }
}
