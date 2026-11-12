// Отбрасываем только грани, целиком закрытые соседним фундаментом в этом чанке.
// Полные кубы и их индексы остаются в исходном InstancedMesh для редактора.
export function buildWorldGroundSides(THREE, ground, tileIndices, size) {
    const source = ground.geometry;
    const p = source.attributes.position, n = source.attributes.normal, uv = source.attributes.uv;
    const matrices = ground.instanceMatrix.array, colors = ground.instanceColor.array;
    const positions = [], normals = [], uvs = [], tints = [];
    for (let z = 0; z < size; z++) for (let x = 0; x < size; x++) {
        const instance = tileIndices[z * size + x];
        if (instance < 0) continue;
        const m = instance * 16;
        const bottom = matrices[m + 13] - matrices[m + 5] * .5;
        const top = matrices[m + 13] + matrices[m + 5] * .5;
        for (let i = 0; i < source.index.count; i += 3) {
            const first = source.index.getX(i), dx = n.getX(first), dz = n.getZ(first);
            const nx = x + dx, nz = z + dz;
            if (nx >= 0 && nx < size && nz >= 0 && nz < size) {
                const neighbor = tileIndices[nz * size + nx];
                if (neighbor >= 0) {
                    const other = neighbor * 16;
                    if (matrices[other + 13] - matrices[other + 5] * .5 <= bottom
                        && matrices[other + 13] + matrices[other + 5] * .5 >= top) continue;
                }
            }
            for (let corner = 0; corner < 3; corner++) {
                const v = source.index.getX(i + corner);
                positions.push(p.getX(v) * matrices[m] + matrices[m + 12],
                    p.getY(v) * matrices[m + 5] + matrices[m + 13],
                    p.getZ(v) * matrices[m + 10] + matrices[m + 14]);
                normals.push(n.getX(v), n.getY(v), n.getZ(v));
                uvs.push(uv.getX(v), uv.getY(v));
                tints.push(colors[instance * 3], colors[instance * 3 + 1], colors[instance * 3 + 2]);
            }
        }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(tints, 3));
    return geometry;
}
