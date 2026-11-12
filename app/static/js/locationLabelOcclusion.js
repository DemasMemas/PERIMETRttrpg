// Границы статического окружения вычисляются при изменении сцены, а не камеры.
export function createLabelOcclusionIndex(THREE, roots) {
    const point = new THREE.Vector3(), instance = new THREE.Matrix4(), world = new THREE.Matrix4();
    const records = [];
    for (const root of new Set(roots)) {
        root.updateWorldMatrix(true, true);
        const bounds = new THREE.Box3(), meshes = [];
        root.traverse(mesh => {
            if (!mesh.isMesh || !mesh.geometry || mesh.userData?.pickOnly) return;
            const geometry = mesh.geometry;
            if (!geometry.boundingBox) geometry.computeBoundingBox();
            const box = new THREE.Box3();
            if (mesh.isInstancedMesh) {
                for (let i = 0; i < mesh.count; i++) {
                    mesh.getMatrixAt(i, instance);
                    world.multiplyMatrices(mesh.matrixWorld, instance);
                    box.union(geometry.boundingBox.clone().applyMatrix4(world));
                }
            } else box.copy(geometry.boundingBox).applyMatrix4(mesh.matrixWorld);
            bounds.union(box);
            meshes.push({ mesh, box });
        });
        if (meshes.length) records.push({ root, bounds, meshes });
    }
    function intersects(picker, box) {
        if (box.containsPoint(picker.ray.origin)) return true;
        return picker.ray.intersectBox(box, point) !== null
            && point.distanceToSquared(picker.ray.origin) <= picker.far * picker.far;
    }
    return {
        blocked(picker, eligible, accept) {
            for (const { root, bounds, meshes } of records) {
                if (!root.visible || !intersects(picker, bounds)) continue;
                for (const { mesh, box } of meshes) {
                    if (!eligible(mesh) || !intersects(picker, box)) continue;
                    // Луч пересекает только близкую геометрию, без обхода всей сцены.
                    if (picker.intersectObject(mesh, false).some(accept)) return true;
                }
            }
            return false;
        },
    };
}

const labelVisibilityBones = ['Head', 'Chest', 'Abdomen', 'Hips',
    'LowerArm.L', 'LowerArm.R', 'LowerLeg.L', 'LowerLeg.R'];

export function createCharacterLabelOcclusion(THREE, environmentBlocks) {
    const picker = new THREE.Raycaster();
    const anchor = new THREE.Vector3(), projected = new THREE.Vector3();
    const ndc = new THREE.Vector2(), offset = new THREE.Vector3();
    const bounds = new THREE.Sphere(new THREE.Vector3(), 2.1);
    const hits = [];
    const visibleModels = [];
    const posedMeshes = new WeakMap();
    const vertex = new THREE.Vector3();
    const bodyBones = new WeakMap();
    const entryStates = new WeakMap();
    const cameraWorld = new THREE.Matrix4(), cameraProjection = new THREE.Matrix4();
    let checkedAt = -Infinity, environmentKey, entryCount = 0;
    let queue = [], queuePosition = 0;
    let sample = -1, visibleSamples = 0;

    function posedMesh(mesh, pose) {
        const source = mesh.geometry;
        const sourcePositions = source.attributes.position;
        let cached = posedMeshes.get(mesh);
        if (cached?.source !== source || cached.sourcePositions !== sourcePositions) {
            const geometry = new THREE.BufferGeometry();
            geometry.setIndex(source.index);
            geometry.groups = source.groups;
            geometry.drawRange = source.drawRange;
            geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(sourcePositions.count * 3), 3));
            cached = { source, sourcePositions, proxy: new THREE.Mesh(geometry) };
            posedMeshes.set(mesh, cached);
        }
        const { proxy } = cached;
        // Проверяем треугольники модели в позе, не пересчитывая веса костей для каждого луча.
        if (cached.pose !== pose || cached.positionVersion !== sourcePositions.version) {
            const positions = proxy.geometry.attributes.position;
            for (let i = 0; i < positions.count; i++) {
                mesh.getVertexPosition(i, vertex);
                positions.setXYZ(i, vertex.x, vertex.y, vertex.z);
            }
            proxy.geometry.computeBoundingBox();
            proxy.geometry.computeBoundingSphere();
            cached.pose = pose;
            cached.positionVersion = sourcePositions.version;
        }
        proxy.material = mesh.material;
        proxy.matrixWorld.copy(mesh.matrixWorld);
        proxy.layers.mask = mesh.layers.mask;
        return proxy;
    }

    function meshBlocksLabel(mesh, pose) {
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        const opaque = material => material && material.visible && (!material.transparent || material.opacity >= .65);
        if (!materials.some(opaque)) return false;
        hits.length = 0;
        picker.intersectObject(mesh.isSkinnedMesh ? posedMesh(mesh, pose) : mesh, false, hits);
        return hits.some(hit => opaque(materials[hit.face?.materialIndex ?? 0]));
    }

    function isOccluded(entry, camera, time) {
        projected.copy(anchor).project(camera);
        if (Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1 || Math.abs(projected.z) > 1) return true;
        picker.setFromCamera(ndc.set(projected.x, projected.y), camera);
        picker.far = offset.subVectors(anchor, picker.ray.origin).dot(picker.ray.direction) - .02;
        if (picker.far <= 0 || environmentBlocks?.(picker, anchor)) return true;
        for (const { model, humanAppearanceModel } of visibleModels) {
            if (model === entry.model) continue;
            // Сфера охватывает позы персонажа. Треугольники проверяем только рядом с лучом.
            bounds.center.setFromMatrixPosition(model.matrixWorld);
            if (offset.subVectors(bounds.center, picker.ray.origin).dot(picker.ray.direction)
                > picker.far + bounds.radius || !picker.ray.intersectsSphere(bounds)) continue;
            const pose = humanAppearanceModel?.userData.shadowPoseRevision ?? time;
            let blocked = false;
            model.traverseVisible(mesh => {
                if (!blocked && mesh.isMesh && !mesh.userData.pickOnly && mesh.layers.test(camera.layers)) {
                    blocked = meshBlocksLabel(mesh, pose);
                }
            });
            if (blocked) return true;
        }
        return false;
    }

    function setBodyAnchor(entry, index) {
        const rig = entry.humanAppearanceModel;
        if (rig && !bodyBones.has(rig)) {
            bodyBones.set(rig, labelVisibilityBones.map(name => rig.getObjectByName(name)));
        }
        const bone = bodyBones.get(rig)?.[index];
        if (bone) {
            anchor.setFromMatrixPosition(bone.matrixWorld);
            return;
        }
        const height = entry.label.position.y - entry.model.position.y;
        const side = index % 2 === 0 ? -1 : 1;
        const section = Math.floor(index / 2);
        const flat = entry.posture === 'prone' || entry.isMutant;
        anchor.set(side * .13, height * (flat ? .44 : .24 + section * .19), flat ? (section - 1.5) * .42 : 0)
            .applyMatrix4(entry.model.matrixWorld);
    }

    function stepLabel(entry, camera, time) {
        if (sample < 0) {
            anchor.setFromMatrixPosition(entry.label.matrixWorld);
            const blocked = isOccluded(entry, camera, time);
            if (blocked || entry.labelState === 'marker') return blocked;
            sample = 0;
            return undefined;
        }
        setBodyAnchor(entry, sample);
        if (!isOccluded(entry, camera, time)) visibleSamples++;
        sample++;
        if (visibleSamples >= 6) return false;
        if (sample - visibleSamples > 2) return true;
        return undefined;
    }

    function snapshotChanged(entries, camera, key) {
        let changed = key !== environmentKey || entryCount !== entries.size
            || !cameraWorld.equals(camera.matrixWorld) || !cameraProjection.equals(camera.projectionMatrix);
        environmentKey = key;
        entryCount = entries.size;
        cameraWorld.copy(camera.matrixWorld);
        cameraProjection.copy(camera.projectionMatrix);
        for (const entry of entries.values()) {
            const rig = entry.humanAppearanceModel;
            const pose = rig?.userData.shadowPoseRevision;
            const old = entryStates.get(entry);
            if (old?.visible === entry.model.visible && old.rig === rig && old.pose === pose
                && old.labelState === entry.labelState && old.matrix.equals(entry.model.matrixWorld)
                && old.anchor.equals(entry.label.position)) continue;
            const state = old || { matrix: new THREE.Matrix4(), anchor: new THREE.Vector3() };
            Object.assign(state, { visible: entry.model.visible, rig, pose, labelState: entry.labelState });
            state.matrix.copy(entry.model.matrixWorld);
            state.anchor.copy(entry.label.position);
            entryStates.set(entry, state);
            changed = true;
        }
        return changed;
    }

    return function update(entries, camera, time, hoveredId, key) {
        if (!queue.length && time - checkedAt >= 67) {
            checkedAt = time;
            if (snapshotChanged(entries, camera, key)) {
                queue = [...entries.entries()];
                queuePosition = 0;
            }
        }
        if (queue.length) {
            for (const entry of entries.values()) {
                if (entry.model.visible && !entry.model.userData.gmHiddenGhost) visibleModels.push(entry);
            }
            // Даже для одной метки проверяем время после каждого луча.
            const deadline = performance.now() + 1.5;
            do {
                const [id, entry] = queue[queuePosition];
                if (entries.get(id) === entry && entry.model.visible) {
                    const blocked = stepLabel(entry, camera, time);
                    if (blocked === undefined) continue;
                    entry.labelOccluded = blocked;
                }
                queuePosition++;
                sample = -1;
                visibleSamples = 0;
            } while (queuePosition < queue.length && performance.now() < deadline);
            if (queuePosition === queue.length) queue = [];
            visibleModels.length = 0;
        }
        for (const [id, entry] of entries) {
            const hovered = hoveredId != null && String(hoveredId) === String(id);
            entry.label.visible = entry.model.visible && (hovered || entry.labelOccluded === false);
        }
    };
}
