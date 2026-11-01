// Cached render-only batches of opaque model parts. Originals
// remain authoritative for picking, editing, floors, fog and physical shadows.
import { isFireObject } from './locationFire.js';

export function createLocationRenderBatches(THREE, scene, roots) {
    const batches = new Map(), hidden = [];
    const materials = new WeakMap();
    const watched = ['version', 'onBeforeCompile', 'roughness', 'metalness', 'side', 'vertexColors',
        'flatShading', 'alphaTest', 'depthTest', 'depthWrite', 'colorWrite', 'blending',
        'premultipliedAlpha', 'polygonOffset', 'polygonOffsetFactor', 'polygonOffsetUnits',
        'bumpScale', 'aoMapIntensity', 'lightMapIntensity', 'envMapIntensity', 'fog', 'toneMapped',
        'map', 'alphaMap', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap',
        'lightMap', 'bumpMap', 'envMap', 'emissiveIntensity'];
    const geometries = new WeakMap();
    let previous = [];
    const visible = [];
    const staticRoot = root => root.visible && root.userData?.locationObject?.type !== 'anomaly'
        && !isFireObject(root.userData?.locationObject) && !root.userData?.fireFlames;
    function materialKey(material) {
        if (!material?.isMeshStandardMaterial || !material.visible || material.transparent || material.opacity !== 1
            || material.clippingPlanes?.length || material.wireframe || material.displacementMap) return null;
        const cached = materials.get(material);
        if (cached && cached.r === material.emissive.r && cached.g === material.emissive.g
            && cached.b === material.emissive.b && cached.nx === material.normalScale.x
            && cached.ny === material.normalScale.y
            && watched.every((name, i) => material[name] === cached.watched[i])) return cached.key;
        // Only known shader extensions can share uniforms. Arbitrary callbacks
        // keep their original draw, even if their program cache keys coincide.
        let compile = material.onBeforeCompile;
        const fields = [];
        for (const name of ['directionalLightFieldPrototype', 'surfacePattern']) {
            const state = material.userData[name];
            if (!state) continue;
            if (!state.batchKey || state.compile !== compile) return null;
            fields.push(name, state.batchKey);
            compile = state.previousCompile;
        }
        if (compile !== THREE.Material.prototype.onBeforeCompile) return null;
        const maps = ['map', 'alphaMap', 'normalMap', 'roughnessMap', 'metalnessMap',
            'emissiveMap', 'aoMap', 'lightMap', 'bumpMap', 'displacementMap', 'envMap'];
        const values = [material.type, material.version, material.emissive.r, material.emissive.g, material.emissive.b,
            material.emissiveIntensity, material.roughness, material.metalness, material.side,
            material.vertexColors, material.flatShading, material.alphaTest, material.depthTest,
            material.depthWrite, material.colorWrite, material.blending, material.premultipliedAlpha,
            material.polygonOffset, material.polygonOffsetFactor, material.polygonOffsetUnits,
            material.normalScale.x, material.normalScale.y, material.bumpScale,
            material.displacementScale, material.displacementBias, material.aoMapIntensity,
            material.lightMapIntensity, material.envMapIntensity, material.fog, material.toneMapped,
            ...maps.map(name => material[name]?.id ?? ''), ...fields];
        const key = values.join(':');
        materials.set(material, { key, watched: watched.map(name => material[name]),
            r: material.emissive.r, g: material.emissive.g, b: material.emissive.b,
            nx: material.normalScale.x, ny: material.normalScale.y });
        return key;
    }
    function release(key, batch) {
        scene.remove(batch.mesh);
        batch.mesh.geometry.dispose(); // Only the derived render buffers.
        batch.mesh.material.dispose();
        batches.delete(key);
    }
    function geometryVersion(geometry) {
        const attrs = geometry.attributes;
        const old = geometries.get(geometry);
        let attributeCount = 0;
        for (const name in attrs) attributeCount++;
        if (old && old.index === geometry.index && old.indexVersion === geometry.index?.version
            && old.start === geometry.drawRange.start && old.count === geometry.drawRange.count
            && old.names.length === attributeCount
            && old.names.every((name, i) => attrs[name] === old.attributes[i]
                && attrs[name].version === old.versions[i])) return old;
        const names = Object.keys(attrs);
        const state = { names, attributes: names.map(name => attrs[name]),
            versions: names.map(name => attrs[name].version), index: geometry.index,
            indexVersion: geometry.index?.version, start: geometry.drawRange.start, count: geometry.drawRange.count };
        geometries.set(geometry, state);
        return state;
    }
    function prepare() {
        scene.updateMatrixWorld();
        // Список нужен каждый кадр, но записи не меняют владельца геометрии.
        // Переиспользуем их, чтобы обход квартала не создавал тысячи объектов.
        let visibleCount = 0;
        for (const root of roots()) if (staticRoot(root)) root.traverseVisible(mesh => {
            if (!mesh.isMesh) return;
            const entry = visible[visibleCount] || (visible[visibleCount] = {});
            entry.root = root; entry.mesh = mesh; entry.version = geometryVersion(mesh.geometry);
            visibleCount++;
        });
        visible.length = visibleCount;
        const unchanged = previous.length === visible.length && visible.every(({ root, mesh, version }, i) => {
            const old = previous[i], m = mesh.material, c = m?.color, e = m?.emissive;
            return old.mesh === mesh && old.material === m && old.materialVersion === m?.version
                && old.compile === m?.onBeforeCompile && old.opacity === m?.opacity
                && old.transparent === m?.transparent && old.materialVisible === m?.visible
                && old.side === m?.side && old.roughness === m?.roughness && old.metalness === m?.metalness
                && old.er === e?.r && old.eg === e?.g && old.eb === e?.b
                && old.emissiveIntensity === m?.emissiveIntensity
                && old.geometry === mesh.geometry && old.version === version
                && old.r === c?.r && old.g === c?.g && old.b === c?.b
                && old.receiveShadow === mesh.receiveShadow && old.layers === mesh.layers.mask
                && old.renderOrder === mesh.renderOrder && old.rootOrder === root.renderOrder
                && mesh.matrixWorld.elements.every((v, j) => v === old.world[j]);
        });
        if (unchanged) {
            for (const batch of batches.values()) {
                batch.mesh.visible = true;
                for (const { source } of batch.records) { hidden.push(source); source.visible = false; }
            }
            return;
        }
        previous = visible.map(({ root, mesh, version }) => ({ mesh, version,
            materialVersion: mesh.material?.version, compile: mesh.material?.onBeforeCompile,
            opacity: mesh.material?.opacity, transparent: mesh.material?.transparent, materialVisible: mesh.material?.visible,
            side: mesh.material?.side, roughness: mesh.material?.roughness, metalness: mesh.material?.metalness,
            er: mesh.material?.emissive?.r, eg: mesh.material?.emissive?.g, eb: mesh.material?.emissive?.b,
            emissiveIntensity: mesh.material?.emissiveIntensity,
            material: mesh.material, geometry: mesh.geometry, r: mesh.material?.color?.r,
            g: mesh.material?.color?.g, b: mesh.material?.color?.b,
            receiveShadow: mesh.receiveShadow, layers: mesh.layers.mask, renderOrder: mesh.renderOrder,
            rootOrder: root.renderOrder, world: mesh.matrixWorld.elements.slice() }));
        const groups = new Map();
        for (const root of roots()) {
            if (!staticRoot(root)) continue;
            root.traverseVisible(mesh => {
                if (!mesh.isMesh || mesh.isSkinnedMesh || mesh.isInstancedMesh
                    || mesh.customDepthMaterial || mesh.matrixWorld.determinant() <= 0) return;
                // The side-projected surface pattern uses mat3(modelMatrix)
                // for its normal. Keep non-uniform scales on the original path.
                if (mesh.material?.userData?.surfacePattern?.normal) {
                    const e = mesh.matrixWorld.elements;
                    const x = e[0] ** 2 + e[1] ** 2 + e[2] ** 2;
                    const y = e[4] ** 2 + e[5] ** 2 + e[6] ** 2;
                    const z = e[8] ** 2 + e[9] ** 2 + e[10] ** 2;
                    if (Math.abs(x - y) + Math.abs(x - z) > 1e-8) return;
                }
                const appearance = materialKey(mesh.material), geometry = mesh.geometry;
                if (appearance === null || geometry.drawRange.start !== 0
                    || geometry.drawRange.count !== Infinity || Object.keys(geometry.morphAttributes).length) return;
                const attributes = Object.entries(geometry.attributes).sort(([a], [b]) => a.localeCompare(b));
                if (attributes.some(([, a]) => a.isInterleavedBufferAttribute || a.normalized
                    || !(a.array instanceof Float32Array)) || geometry.attributes.color?.itemSize === 4) return;
                // Spatial chunks retain frustum culling without rebuilding on
                // camera movement. A local edit invalidates only its own batches.
                const e = root.matrixWorld.elements;
                const key = [Math.floor(e[12] / 12), Math.floor(e[14] / 12), appearance,
                    mesh.receiveShadow, mesh.layers.mask, mesh.renderOrder, root.renderOrder,
                    attributes.map(([name, a]) => `${name}:${a.itemSize}`).join('|')].join(':');
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(mesh);
            });
        }
        for (const [key, batch] of batches) if ((groups.get(key)?.length || 0) < 2) release(key, batch);
        for (const [key, sources] of groups) {
            if (sources.length < 2) continue;
            let batch = batches.get(key);
            const changed = !batch || batch.records.length !== sources.length || sources.some((mesh, i) => {
                const r = batch.records[i], c = mesh.material.color;
                return r?.source !== mesh || r.geometry !== mesh.geometry || r.version !== geometryVersion(mesh.geometry)
                    || r.r !== c.r || r.g !== c.g || r.b !== c.b
                    || mesh.matrixWorld.elements.some((v, j) => v !== r.world[j]);
            });
            if (changed) {
                if (batch) release(key, batch);
                const source = sources[0], pieces = [];
                try {
                    for (const mesh of sources) {
                        const part = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
                        part.applyMatrix4(mesh.matrixWorld);
                        const n = part.attributes.position.count, color = new Float32Array(n * 3);
                        const existing = mesh.material.vertexColors ? part.attributes.color : null;
                        for (let i = 0; i < n; i++) {
                            color[i * 3] = mesh.material.color.r * (existing?.getX(i) ?? 1);
                            color[i * 3 + 1] = mesh.material.color.g * (existing?.getY(i) ?? 1);
                            color[i * 3 + 2] = mesh.material.color.b * (existing?.getZ(i) ?? 1);
                        }
                        part.setAttribute('color', new THREE.BufferAttribute(color, 3));
                        pieces.push(part);
                    }
                    const geometry = new THREE.BufferGeometry();
                    for (const name of Object.keys(pieces[0].attributes)) {
                        const attrs = pieces.map(p => p.attributes[name]);
                        const array = new Float32Array(attrs.reduce((n, a) => n + a.array.length, 0));
                        let offset = 0;
                        for (const a of attrs) { array.set(a.array, offset); offset += a.array.length; }
                        geometry.setAttribute(name, new THREE.BufferAttribute(array, attrs[0].itemSize));
                    }
                    geometry.computeBoundingSphere();
                    const material = source.material.clone();
                    material.color.setHex(0xffffff);
                    material.vertexColors = true;
                    material.onBeforeCompile = source.material.onBeforeCompile;
                    material.customProgramCacheKey = source.material.customProgramCacheKey;
                    const mesh = new THREE.Mesh(geometry, material);
                    mesh.receiveShadow = source.receiveShadow;
                    mesh.castShadow = false; // Sun and lamp passes use originals.
                    mesh.layers.mask = source.layers.mask;
                    mesh.renderOrder = source.renderOrder;
                    mesh.matrixAutoUpdate = false;
                    mesh.raycast = () => {};
                    mesh.userData.locationRenderBatch = true;
                    scene.add(mesh);
                    batch = { mesh, records: sources.map(mesh => ({ source: mesh,
                        geometry: mesh.geometry, version: geometryVersion(mesh.geometry),
                        r: mesh.material.color.r, g: mesh.material.color.g, b: mesh.material.color.b,
                        world: mesh.matrixWorld.elements.slice() })) };
                    batches.set(key, batch);
                } finally { for (const part of pieces) part.dispose(); }
            }
            batch.mesh.visible = true;
            for (const mesh of sources) { hidden.push(mesh); mesh.visible = false; }
        }
    }
    function restore() {
        for (const root of hidden) root.visible = true;
        hidden.length = 0;
        for (const batch of batches.values()) batch.mesh.visible = false;
    }
    function withOriginals(draw) {
        for (const mesh of hidden) mesh.visible = true;
        try { return draw(); } finally { for (const mesh of hidden) mesh.visible = false; }
    }
    return { prepare, restore, withOriginals,
        dispose() { restore(); for (const [key, batch] of batches) release(key, batch); } };
}
