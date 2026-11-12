// Rendering-only batches. Chunk instances remain authoritative for editing/picking.
// Copy only when visible membership or instance attributes change, never every frame.
export function createWorldInstanceBatches(THREE, scene, chunks) {
    const batches = new Map();
    const hidden = [];
    const matrix = new THREE.Matrix4();
    const combined = new THREE.Matrix4();
    const color = new THREE.Color();
    const eligible = mesh => mesh?.isInstancedMesh && mesh.visible && mesh.count > 0
        && !Array.isArray(mesh.material) && mesh.material.visible && !mesh.material.transparent;
    function sources() {
        const groups = new Map();
        for (const chunk of chunks.values()) {
            if (!chunk.terrainVisible) continue;
            for (const mesh of [chunk.trees, chunk.houses, chunk.fences, chunk.farAnomalies,
                ...Object.values(chunk.landmarks || {})]) {
                if (!eligible(mesh)) continue;
                const key = `${mesh.geometry.id}:${mesh.material.id}:${mesh.castShadow}:${mesh.receiveShadow}:${mesh.layers.mask}:${mesh.renderOrder}:${!!mesh.instanceColor}`;
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(mesh);
            }
        }
        return groups;
    }
    function prepare() {
        const groups = sources();
        for (const [key, batch] of batches) {
            if ((groups.get(key)?.length || 0) < 2) {
                scene.remove(batch.mesh);
                batch.mesh.dispose(); // Instance buffers only; shared geometry/material stay alive.
                batches.delete(key);
            }
        }
        for (const [key, meshes] of groups) {
            if (meshes.length < 2) continue;
            const count = meshes.reduce((sum, mesh) => sum + mesh.count, 0);
            let batch = batches.get(key);
            if (!batch || batch.capacity < count) {
                if (batch) { scene.remove(batch.mesh); batch.mesh.dispose(); }
                const capacity = 2 ** Math.ceil(Math.log2(count));
                const source = meshes[0];
                const mesh = new THREE.InstancedMesh(source.geometry, source.material, capacity);
                if (source.instanceColor) mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(capacity * 3), 3);
                mesh.castShadow = source.castShadow;
                mesh.receiveShadow = source.receiveShadow;
                mesh.layers.mask = source.layers.mask;
                mesh.renderOrder = source.renderOrder;
                mesh.frustumCulled = false; // Membership already uses existing chunk visibility.
                mesh.matrixAutoUpdate = false;
                mesh.raycast = () => {}; // Original chunk meshes retain stable instance indices.
                mesh.userData.worldInstanceBatch = true;
                scene.add(mesh);
                batch = { mesh, capacity, records: [] };
                batches.set(key, batch);
            }
            const changed = batch.records.length !== meshes.length || meshes.some((mesh, i) => {
                const record = batch.records[i];
                return record?.source !== mesh || record.count !== mesh.count
                    || record.matrixVersion !== mesh.instanceMatrix.version
                    || record.colorVersion !== mesh.instanceColor?.version
                    || mesh.matrixWorld.elements.some((value, axis) => value !== record.world[axis]);
            });
            if (changed) {
                let index = 0;
                for (const source of meshes) {
                    for (let i = 0; i < source.count; i++, index++) {
                        source.getMatrixAt(i, matrix);
                        combined.multiplyMatrices(source.matrixWorld, matrix);
                        batch.mesh.setMatrixAt(index, combined);
                        if (source.instanceColor) {
                            source.getColorAt(i, color);
                            batch.mesh.setColorAt(index, color);
                        }
                    }
                }
                batch.mesh.count = count;
                batch.mesh.instanceMatrix.needsUpdate = true;
                if (batch.mesh.instanceColor) batch.mesh.instanceColor.needsUpdate = true;
                batch.records = meshes.map(source => ({ source, count: source.count,
                    matrixVersion: source.instanceMatrix.version, colorVersion: source.instanceColor?.version,
                    world: source.matrixWorld.elements.slice() }));
            }
            batch.mesh.visible = true;
            for (const mesh of meshes) { hidden.push(mesh); mesh.visible = false; }
        }
    }
    function restore() {
        for (const mesh of hidden) mesh.visible = true;
        hidden.length = 0;
        for (const batch of batches.values()) batch.mesh.visible = false;
    }
    return { prepare, restore };
}
