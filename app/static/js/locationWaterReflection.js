const REFLECTION_PATCH_SIZE = 8;
const REFLECTION_VIEWPORT_PADDING = 8;

function collectReflectionBounds(THREE, geometry, level) {
    const positions = geometry.getAttribute('position');
    const band = geometry.hasAttribute('waterDepth') ? .14 : .035;
    const patches = new Map();
    const point = new THREE.Vector3();
    for (let i = 0; i < positions.count; i += 3) {
        const a = positions.getY(i), b = positions.getY(i + 1), c = positions.getY(i + 2);
        if (Math.min(a, b, c) >= level + band || Math.max(a, b, c) <= level - band) continue;
        const x = Math.floor(positions.getX(i) / REFLECTION_PATCH_SIZE);
        const z = Math.floor(positions.getZ(i) / REFLECTION_PATCH_SIZE);
        const key = `${x}:${z}`;
        if (!patches.has(key)) patches.set(key, new THREE.Box3());
        const box = patches.get(key);
        for (let vertex = i; vertex < i + 3; vertex++) box.expandByPoint(point.fromBufferAttribute(positions, vertex));
    }
    return [...patches.values()];
}

function fitReflectionViewport(target, bounds, count, worldToClip, point) {
    const { width, height, viewport } = target;
    viewport.set(0, 0, width, height);
    let left = Infinity, right = -Infinity, bottom = Infinity, top = -Infinity;
    for (let i = 0; i < count; i++) {
        const { min, max } = bounds[i];
        for (let corner = 0; corner < 8; corner++) {
            point.set(corner & 1 ? max.x : min.x, corner & 2 ? max.y : min.y,
                corner & 4 ? max.z : min.z, 1).applyMatrix4(worldToClip);
            // У плоскости камеры нельзя надёжно ограничить проекцию углами AABB.
            if (point.w <= 0) return;
            const x = (point.x / point.w + 1) * .5 * width;
            const y = (point.y / point.w + 1) * .5 * height;
            left = Math.min(left, x);
            right = Math.max(right, x);
            bottom = Math.min(bottom, y);
            top = Math.max(top, y);
        }
    }
    const padding = REFLECTION_VIEWPORT_PADDING;
    const x = Math.max(0, Math.floor(left) - padding), y = Math.max(0, Math.floor(bottom) - padding);
    const w = Math.min(width, Math.ceil(right) + padding) - x;
    const h = Math.min(height, Math.ceil(top) + padding) - y;
    if (w > 0 && h > 0) viewport.set(x, y, w, h);
}

export function createLocationWaterReflection(THREE, level, geometry) {
    const target = new THREE.WebGLRenderTarget(768, 432);
    const mirror = new THREE.PerspectiveCamera();
    const surfacePlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -level + .006);
    const mirrorPlane = new THREE.Plane();
    const clip = new THREE.Vector4();
    const farCorner = new THREE.Vector4();
    const screenSize = new THREE.Vector2();
    const forward = new THREE.Vector3();
    const up = new THREE.Vector3();
    const previousView = new THREE.Matrix4();
    const previousProjection = new THREE.Matrix4();
    const viewProjection = new THREE.Matrix4();
    const viewportProjection = new THREE.Matrix4();
    const viewportPoint = new THREE.Vector4();
    const frustum = new THREE.Frustum();
    const patches = collectReflectionBounds(THREE, geometry, level);
    const visibleBounds = patches.map(() => new THREE.Box3());
    const uniforms = {
        waterReflectionMap: { value: target.texture },
        waterReflectionMatrix: { value: new THREE.Matrix4() },
        waterReflectionReady: { value: 0 },
        waterReflectionLevel: { value: level },
        waterReflectionTexel: { value: new THREE.Vector2() },
    };
    let previousRevision = -1;
    let invalidated = true;

    return {
        uniforms,
        invalidate() { invalidated = true; },
        update(renderer, scene, camera, water, revision) {
            if (camera.position.y <= level) {
                uniforms.waterReflectionReady.value = 0;
                return;
            }
            renderer.getDrawingBufferSize(screenSize);
            const width = Math.max(640, Math.min(1024, Math.round(screenSize.x * .72)));
            const height = Math.max(160, Math.min(900, Math.round(width / camera.aspect)));
            camera.updateMatrixWorld();
            const refresh = invalidated || !uniforms.waterReflectionReady.value || previousRevision !== revision
                || !previousProjection.equals(camera.projectionMatrix) || target.width !== width || target.height !== height;
            if (!refresh && previousView.equals(camera.matrixWorld)) return;

            frustum.setFromProjectionMatrix(viewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
            let visibleCount = 0;
            for (const patch of patches) {
                const bounds = visibleBounds[visibleCount].copy(patch).applyMatrix4(water.matrixWorld);
                if (frustum.intersectsBox(bounds)) visibleCount++;
            }
            if (!visibleCount) {
                invalidated = true;
                return;
            }

            target.setSize(width, height);
            uniforms.waterReflectionTexel.value.set(1 / width, 1 / height);
            mirror.position.copy(camera.position);
            mirror.position.y = 2 * level - mirror.position.y;
            camera.getWorldDirection(forward);
            forward.y *= -1;
            up.set(0, 1, 0).applyQuaternion(camera.quaternion);
            up.y *= -1;
            mirror.up.copy(up);
            mirror.lookAt(forward.add(mirror.position));
            mirror.near = camera.near;
            mirror.far = camera.far;
            mirror.layers.mask = camera.layers.mask;
            mirror.projectionMatrix.copy(camera.projectionMatrix);
            mirror.updateMatrixWorld();
            uniforms.waterReflectionMatrix.value.multiplyMatrices(
                mirror.projectionMatrix, mirror.matrixWorldInverse);
            fitReflectionViewport(target, visibleBounds, visibleCount, uniforms.waterReflectionMatrix.value, viewportPoint);

            // Плоскость воды отсекает подводную геометрию без нового набора шейдеров.
            mirrorPlane.copy(surfacePlane).applyMatrix4(mirror.matrixWorldInverse);
            clip.set(mirrorPlane.normal.x, mirrorPlane.normal.y, mirrorPlane.normal.z, mirrorPlane.constant);
            farCorner.set(Math.sign(clip.x), Math.sign(clip.y), 1, 1)
                .applyMatrix4(camera.projectionMatrixInverse);
            clip.multiplyScalar(2 / clip.dot(farCorner));
            const projection = mirror.projectionMatrix.elements;
            projection[2] = clip.x;
            projection[6] = clip.y;
            projection[10] = clip.z + 1;
            projection[14] = clip.w;
            // Сужаем область отрисовки и отсечение зеркальной камеры. Координаты
            // пикселей в текстуре сохраняются, поэтому шейдер читает прежнюю проекцию.
            const { x, y, z: w, w: h } = target.viewport;
            viewportProjection.set(
                width / w, 0, 0, (width - 2 * x - w) / w,
                0, height / h, 0, (height - 2 * y - h) / h,
                0, 0, 1, 0,
                0, 0, 0, 1,
            );
            mirror.projectionMatrix.premultiply(viewportProjection);
            mirror.projectionMatrixInverse.copy(mirror.projectionMatrix).invert();
            target.scissor.copy(target.viewport);
            target.scissorTest = true;

            const previousTarget = renderer.getRenderTarget();
            const { autoUpdate, needsUpdate } = renderer.shadowMap;
            const wasVisible = water.visible;
            try {
                water.visible = false;
                renderer.shadowMap.autoUpdate = false;
                renderer.shadowMap.needsUpdate = false;
                renderer.setRenderTarget(target);
                renderer.render(scene, mirror);
            } finally {
                renderer.setRenderTarget(previousTarget);
                renderer.shadowMap.autoUpdate = autoUpdate;
                renderer.shadowMap.needsUpdate = needsUpdate;
                water.visible = wasVisible;
            }
            uniforms.waterReflectionReady.value = 1;
            invalidated = false;
            previousRevision = revision;
            previousView.copy(camera.matrixWorld);
            previousProjection.copy(camera.projectionMatrix);
        },
        dispose() { target.dispose(); },
    };
}
