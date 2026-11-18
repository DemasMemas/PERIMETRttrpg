export function createLocationStoreyFade() {
    const copies = new Map();
    const applied = [];

    function prepare(root, opacity, covering = false) {
        if (!root.visible || opacity <= 0 || opacity >= 1) return;
        if (!copies.has(root)) copies.set(root, new Map());
        const materials = copies.get(root);
        function fade(material) {
            let entry = materials.get(material);
            if (!entry || entry.version !== material.version) {
                entry?.copy.dispose();
                const copy = material.clone();
                // Копия должна сохранить шейдеры фактуры и освещения исходного объекта.
                copy.userData = material.userData;
                copy.onBeforeCompile = material.onBeforeCompile;
                copy.customProgramCacheKey = material.customProgramCacheKey;
                copy.transparent = true;
                copy.depthWrite = false;
                entry = { copy, version: material.version };
                materials.set(material, entry);
            }
            const { copy } = entry;
            copy.color?.copy(material.color);
            copy.emissive?.copy(material.emissive);
            copy.emissiveIntensity = material.emissiveIntensity;
            copy.visible = material.visible;
            copy.opacity = covering ? Math.min(material.opacity, opacity) : material.opacity * opacity;
            return copy;
        }
        root.traverseVisible(mesh => {
            if (!mesh.isMesh || !mesh.material) return;
            const original = mesh.material;
            const faded = Array.isArray(original) ? original.map(fade) : fade(original);
            applied.push({ mesh, original, faded });
            mesh.material = faded;
        });
    }

    function withOriginals(draw) {
        for (const { mesh, original } of applied) mesh.material = original;
        try { return draw(); }
        finally { for (const { mesh, faded } of applied) mesh.material = faded; }
    }

    function restore() {
        for (const { mesh, original } of applied) mesh.material = original;
        applied.length = 0;
        for (const [root, materials] of copies) {
            // Переиспользуем готовые варианты материалов при повторном приближении.
            if (root.parent) continue;
            for (const { copy } of materials.values()) copy.dispose();
            copies.delete(root);
        }
    }

    return { prepare, withOriginals, restore, dispose() {
        restore();
        for (const materials of copies.values()) {
            for (const { copy } of materials.values()) copy.dispose();
        }
        copies.clear();
    } };
}
