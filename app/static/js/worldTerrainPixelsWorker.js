import { createTerrainPixelsRegion, setGrassGroundPixelData } from './locationTerrainTexture.js';

self.onmessage = ({ data }) => {
    try {
        if (data.initialize) {
            setGrassGroundPixelData(data.groundPixels);
            self.postMessage({ ready: true });
            return;
        }
        for (const { task, index } of data.jobs) {
            const startedAt = performance.now();
            const profile = { pixels: 0, terrainLookups: 0, photoColorCalls: 0,
                photoSampledCalls: 0, photoSampledMs: 0 };
            const image = createTerrainPixelsRegion((x, z) => task.rows[z - task.minZ][x - task.minX],
                task.originX, task.originZ, task.textureSize, task.pixelsPerTile,
                task.photos, profile, true);
            self.postMessage({ index, image, profile, workMs: performance.now() - startedAt }, [image.data.buffer]);
        }
    } catch (error) {
        self.postMessage({ error: String(error) });
    }
};
