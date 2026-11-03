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
