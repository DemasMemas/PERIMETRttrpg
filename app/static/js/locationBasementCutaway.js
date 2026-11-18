// View-only terrain cutaway, composed with the existing terrain, water,
// vegetation and lighting shaders. No terrain geometry or saved data is edited.
export function cutawayContains(footprints, x, z) {
    return footprints.some(p => {
        const dx = x - p.x, dz = z - p.z;
        return Math.abs(dx * p.axis.x + dz * p.axis.z) <= p.width / 2
            && Math.abs(dx * p.normal.x + dz * p.normal.z) <= p.depth / 2;
    });
}

export function createBasementCutaway(THREE) {
    // Analytic rectangles need no additional texture unit: terrain already uses
    // the full sampler budget on some WebGL implementations.
    let capacity = 1;
    const uniforms = {
        basementRects: { value: [new THREE.Vector4()] },
        basementAxes: { value: [new THREE.Vector2()] },
        basementRectCount: { value: 0 },
    };
    const materials = new Set();
    const hooks = new WeakMap();
    const shadowMaterials = new Set();
    let signature = '';
    function attach(material) {
        if (!material || hooks.get(material) === material.onBeforeCompile) return;
        materials.add(material);
        const before = material.onBeforeCompile;
        const originalKey = material.customProgramCacheKey();
        material.customProgramCacheKey = () => `${originalKey}|basement-cutaway-v3:${capacity}`;
        material.onBeforeCompile = function(shader, renderer) {
            before.call(this, shader, renderer);
            Object.assign(shader.uniforms, uniforms);
            // Lighting may wrap this callback and later restore an earlier one.
            // Compose again when needed without declaring the cutaway twice.
            if (shader.vertexShader.includes('varying vec2 vBasementWorldXZ;')) return;
            shader.vertexShader = shader.vertexShader
                .replace('#include <common>', '#include <common>\nvarying vec2 vBasementWorldXZ;')
                .replace('#include <project_vertex>', `#include <project_vertex>
                    vec4 basementWorld = vec4(transformed, 1.0);
                    #ifdef USE_INSTANCING
                        basementWorld = instanceMatrix * basementWorld;
                    #endif
                    vBasementWorldXZ = (modelMatrix * basementWorld).xz;`);
            shader.fragmentShader = shader.fragmentShader
                .replace('#include <common>', `#include <common>
                    varying vec2 vBasementWorldXZ;
                    uniform vec4 basementRects[${capacity}];
                    uniform vec2 basementAxes[${capacity}];
                    uniform int basementRectCount;`)
                .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
                    for (int basementIndex = 0; basementIndex < ${capacity}; basementIndex++) {
                        if (basementIndex >= basementRectCount) break;
                        vec4 basementRect = basementRects[basementIndex];
                        vec2 basementAxis = basementAxes[basementIndex];
                        vec2 basementDelta = vBasementWorldXZ - basementRect.xy;
                        // Tile sides can lie exactly on the opening boundary.
                        // A small tolerance avoids retaining noisy coplanar fragments.
                        if (abs(dot(basementDelta, basementAxis)) <= basementRect.z + 0.002
                            && abs(dot(basementDelta, vec2(-basementAxis.y, basementAxis.x))) <= basementRect.w + 0.002) discard;
                    }`);
        };
        hooks.set(material, material.onBeforeCompile);
        material.needsUpdate = true;
    }
    return {
        attachMaterial: attach,
        update(footprints) {
            const next = JSON.stringify(footprints.map(p => [p.x,p.z,p.width,p.depth,p.axis.x,p.axis.z]));
            if (next === signature) return false;
            signature = next;
            if (footprints.length > capacity) {
                capacity = 2 ** Math.ceil(Math.log2(footprints.length));
                uniforms.basementRects.value = Array.from({length:capacity},()=>new THREE.Vector4());
                uniforms.basementAxes.value = Array.from({length:capacity},()=>new THREE.Vector2());
                materials.forEach(material => { material.needsUpdate = true; });
            }
            uniforms.basementRectCount.value = footprints.length;
            footprints.forEach((p,i) => {
                uniforms.basementRects.value[i].set(p.x,p.z,p.width/2,p.depth/2);
                uniforms.basementAxes.value[i].set(p.axis.x,p.axis.z);
            });
            return true;
        },
        attach(root) {
            root?.traverse(child => {
                for (const material of Array.isArray(child.material) ? child.material : [child.material]) attach(material);
                if (child.isMesh && child.castShadow) {
                    if (!child.customDepthMaterial) {
                        const source = Array.isArray(child.material) ? child.material[0] : child.material;
                        child.customDepthMaterial = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking,
                            map:source?.map, alphaMap:source?.alphaMap, alphaTest:source?.alphaTest || 0, side:source?.side });
                        shadowMaterials.add(child.customDepthMaterial);
                    }
                    if (!child.customDistanceMaterial) {
                        child.customDistanceMaterial = new THREE.MeshDistanceMaterial();
                        shadowMaterials.add(child.customDistanceMaterial);
                    }
                    attach(child.customDepthMaterial); attach(child.customDistanceMaterial);
                }
            });
        },
        dispose() { shadowMaterials.forEach(material => material.dispose()); materials.clear(); },
    };
}
