import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { getTreeGeometry } from './locationHouseholdModels.js';

const species = ['oak', 'birch', 'pine'];
const crownWidth = { oak: .6, birch: .46, pine: .52 };
const forestParts = [
    ['oak', -.23, .19, .02, 1, .2],
    ['pine', .16, .28, -.16, 1.18, 1.7],
    ['birch', .22, .12, .24, .86, 3.2],
];

export function worldTreeSpecies(x, z) {
    const seed = Math.imul(Math.floor(x), 73856093) ^ Math.imul(Math.floor(z), 19349663);
    return species[(seed >>> 0) % species.length];
}

export function createWorldTrees(THREE, scene, chunks, material, defaultColor) {
    const geometryCache = new Map();
    const sourceGroups = new WeakMap();
    const visualBatches = new Map();
    const shadowBatches = new Map();
    const depthSurface = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
    const matrix = new THREE.Matrix4();
    const instanceBox = new THREE.Box3();
    const tint = new THREE.Color();
    const defaultTints = { tree: new THREE.Color(defaultColor.tree), forest: new THREE.Color(defaultColor.forest) };
    const white = new THREE.Color(0xffffff);

    function geometry(name, detail) {
        const key = `${name}:${detail}`;
        if (geometryCache.has(key)) return geometryCache.get(key);
        let meshGeometry;
        if (name === 'forest') {
            const parts = forestParts.map(([tree, x, y, z, scale, rotation]) => {
                // Сосна выше остальных. При приближении она первой достигает порога подробной модели.
                const partDetail = detail === 'mixed' ? (tree === 'pine' ? 'full' : 'middle') : detail;
                return geometry(tree, partDetail).clone().rotateY(rotation).scale(scale, scale, scale).translate(x, y, z);
            });
            meshGeometry = mergeGeometries(parts);
            for (const part of parts) part.dispose();
        } else {
            const width = crownWidth[name];
            meshGeometry = getTreeGeometry(THREE, `${name}_green`, detail).clone().scale(width, 1, width).translate(0, -.5, 0);
        }
        meshGeometry.computeBoundingBox();
        geometryCache.set(key, meshGeometry);
        return meshGeometry;
    }

    function groupsFor(source, type) {
        const matrixVersion = source.instanceMatrix.version;
        const colorVersion = source.instanceColor.version;
        const previous = sourceGroups.get(source);
        if (previous?.matrixVersion === matrixVersion && previous.colorVersion === colorVersion) return previous.groups;
        if (!source.geometry.boundingBox) source.geometry.computeBoundingBox();

        // Малые группы ограничивают и площадь отсечения, и число соседних деревьев,
        // которые получат дорогую модель из-за одного крупного экземпляра.
        const groups = new Map();
        for (let index = 0; index < source.count; index++) {
            source.getMatrixAt(index, matrix);
            const x = matrix.elements[12], z = matrix.elements[14];
            const name = type === 'forest' ? type : worldTreeSpecies(x, z);
            const key = `${Math.floor(x / 2)},${Math.floor(z / 2)}:${name}`;
            let group = groups.get(key);
            if (!group) {
                group = { source, type, name, indices: [], bounds: new THREE.Box3(), height: 0,
                    visualLevel: previous?.groups.get(key)?.visualLevel, shadowLevel: previous?.groups.get(key)?.shadowLevel };
                groups.set(key, group);
            }
            instanceBox.copy(source.geometry.boundingBox).applyMatrix4(matrix);
            instanceBox.expandByScalar((instanceBox.max.y - instanceBox.min.y) * .015);
            group.bounds.union(instanceBox);
            group.height = Math.max(group.height, instanceBox.max.y - instanceBox.min.y);
            group.indices.push(index);
        }
        sourceGroups.set(source, { matrixVersion, colorVersion, groups });
        return groups;
    }

    function collect(frustum, includeChunk, getLevel, visibleOnly) {
        const groups = new Map();
        for (const chunk of chunks.values()) {
            if (!includeChunk(chunk)) continue;
            for (const [source, type] of [[chunk.trees, 'tree'], [chunk.landmarks?.forest, 'forest']]) {
                if (!source?.count || (visibleOnly && !source.visible)) continue;
                for (const group of groupsFor(source, type).values()) {
                    if (!frustum.intersectsBox(group.bounds)) continue;
                    const detail = getLevel(group);
                    const key = `${group.name}:${detail}`;
                    if (!groups.has(key)) groups.set(key, { name: group.name, detail, groups: [], count: 0 });
                    const batch = groups.get(key);
                    batch.groups.push(group);
                    batch.count += group.indices.length;
                }
            }
        }
        return groups;
    }

    function sync(batches, collected, shadows) {
        for (const [key, batch] of batches) {
            if (collected.has(key)) continue;
            batch.mesh.visible = false;
            batch.groups = [];
        }
        for (const [key, { name, detail, groups, count }] of collected) {
            let batch = batches.get(key);
            if (!batch || batch.capacity < count) {
                if (batch) {
                    scene.remove(batch.mesh);
                    batch.mesh.dispose();
                }
                const capacity = 2 ** Math.ceil(Math.log2(count));
                const mesh = new THREE.InstancedMesh(geometry(name, detail), shadows ? depthSurface : material, capacity);
                mesh.castShadow = shadows;
                mesh.frustumCulled = false;
                mesh.matrixAutoUpdate = false;
                mesh.raycast = () => {};
                mesh.userData.worldTreeBatch = { name, detail, shadows };
                if (!shadows) mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(capacity * 3), 3);
                scene.add(mesh);
                batch = { mesh, capacity, groups: [] };
                batches.set(key, batch);
            }
            if (batch.groups.length !== groups.length || groups.some((group, i) => batch.groups[i] !== group)) {
                let next = 0;
                for (const { source, type, indices } of groups) {
                    for (const index of indices) {
                        source.getMatrixAt(index, matrix);
                        batch.mesh.setMatrixAt(next, matrix);
                        if (!shadows) {
                            source.getColorAt(index, tint);
                            batch.mesh.setColorAt(next, colorTint(tint, type));
                        }
                        next++;
                    }
                }
                batch.mesh.count = count;
                batch.mesh.instanceMatrix.clearUpdateRanges();
                batch.mesh.instanceMatrix.addUpdateRange(0, count * 16);
                batch.mesh.instanceMatrix.needsUpdate = true;
                if (!shadows) {
                    batch.mesh.instanceColor.clearUpdateRanges();
                    batch.mesh.instanceColor.addUpdateRange(0, count * 3);
                    batch.mesh.instanceColor.needsUpdate = true;
                }
                batch.groups = groups;
            }
            batch.mesh.visible = true;
        }
    }

    function colorTint(color, type) {
        // Сохранённый стандартный цвет совпадает с окраской породы. Пользовательский
        // оттенок накладывается на ствол и листья с сохранением фактуры и освещения.
        const base = defaultTints[type];
        if (Math.abs(color.r - base.r) + Math.abs(color.g - base.g) + Math.abs(color.b - base.b) < 1e-5) return white;
        return color.lerp(white, .36);
    }

    function updateVisible(camera, frustum, viewportHeight) {
        const pixels = viewportHeight * camera.projectionMatrix.elements[5] * .5;
        const collected = collect(frustum, chunk => chunk.terrainVisible, group => {
            const height = pixels * group.height / Math.max(.1, group.bounds.distanceToPoint(camera.position));
            const previous = group.visualLevel;
            const forest = group.name === 'forest';
            const fullThreshold = forest ? (previous === 'full' ? 215 : 255) : (previous === 'full' ? 155 : 185);
            if (height >= fullThreshold) group.visualLevel = 'full';
            else if (forest && height >= (previous === 'mixed' || previous === 'full' ? 155 : 185)) group.visualLevel = 'mixed';
            else group.visualLevel = height >= (previous === 'far' ? 30 : 23) ? 'middle' : 'far';
            return group.visualLevel;
        }, true);
        sync(visualBatches, collected, false);
    }

    function prepareShadows(light, camera, viewportHeight, includeChunk) {
        const { shadow } = light;
        shadow.updateMatrices(light);
        const texelsPerUnit = shadow.mapSize.x / (shadow.camera.right - shadow.camera.left);
        const pixels = viewportHeight * camera.projectionMatrix.elements[5] * .5;
        const collected = collect(shadow.getFrustum(), includeChunk, group => {
            const size = group.height * Math.min(texelsPerUnit, pixels / Math.max(.1, group.bounds.distanceToPoint(camera.position)));
            group.shadowLevel = size >= (group.shadowLevel === 'nearShadow' ? 36 : 46) ? 'nearShadow' : 'shadow';
            return group.shadowLevel;
        }, false);
        sync(shadowBatches, collected, true);
    }

    function hideShadows() {
        for (const batch of shadowBatches.values()) batch.mesh.visible = false;
    }

    return { geometry, colorTint, updateVisible, prepareShadows, hideShadows };
}
