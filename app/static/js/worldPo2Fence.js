// One baked model and one material for every world fence instance and preview.
// Local fences deliberately keep their existing assets.
export function createPo2Fence(THREE, onReady = () => {}) {
    const geometry = new THREE.BoxGeometry(0.0466, 0.5, 0.8);
    const material = new THREE.MeshStandardMaterial({ roughness: 0.97, metalness: 0 });
    const diagnostics = { geometryReady: false, textureReady: false, errors: [] };
    const ready = Promise.all([
        new Promise(resolve => new THREE.BufferGeometryLoader().load('/static/models/art/po2_section.json', loaded => {
            geometry.dispose();
            geometry.copy(loaded);
            geometry.computeBoundingBox();
            geometry.computeBoundingSphere();
            loaded.dispose();
            diagnostics.geometryReady = true;
            onReady();
            resolve();
        }, undefined, error => { diagnostics.errors.push(String(error)); resolve(); })),
        new Promise(resolve => new THREE.TextureLoader().load('/static/models/art/po2_concrete.png', texture => {
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.anisotropy = 4;
            material.map = texture;
            material.needsUpdate = true;
            diagnostics.textureReady = true;
            onReady();
            resolve();
        }, undefined, error => { diagnostics.errors.push(String(error)); resolve(); })),
    ]);
    return { geometry, material, ready, diagnostics: () => ({ ...diagnostics,
        errors: [...diagnostics.errors], triangles: geometry.attributes.position.count / 3,
        // RGBA8 512x512 including the mip chain; driver overhead is not included.
        textureBytes: diagnostics.textureReady ? Math.ceil(512 * 512 * 4 * 4 / 3) : 0 }) };
}
