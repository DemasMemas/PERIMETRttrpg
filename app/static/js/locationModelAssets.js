import * as THREE from 'three';

const modelCache = new Map();
const sharedGeometries = new WeakSet();
const mounts = new WeakMap();
const MAX_UNUSED_MODELS = 4;
const FAILED_RETRY_DELAY_MS = 3000;
let loaderPromise = null;

function getLoader() {
    if (!loaderPromise) {
        loaderPromise = import('three/addons/loaders/GLTFLoader.js')
            .then(({ GLTFLoader }) => new GLTFLoader())
            .catch(error => {
                loaderPromise = null;
                throw error;
            });
    }
    return loaderPromise;
}

function materialTextures(material) {
    return Object.values(material || {}).filter(value => value?.isTexture);
}

function disposeCachedScene(root) {
    const resources = new Set();
    root.traverse(child => {
        if (child.geometry) resources.add(child.geometry);
        for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
            if (!material) continue;
            resources.add(material);
            materialTextures(material).forEach(texture => resources.add(texture));
        }
    });
    resources.forEach(resource => resource.dispose());
}

function evictEntry(entry) {
    if (entry.refs || entry.loading || modelCache.get(entry.path) !== entry) return;
    modelCache.delete(entry.path);
    if (entry.scene) disposeCachedScene(entry.scene);
}

function trimCache(force = false) {
    const unused = [...modelCache.values()].filter(entry => !entry.refs);
    const excess = force ? unused.length : Math.max(0, unused.length - MAX_UNUSED_MODELS);
    unused.slice(0, excess).forEach(entry => {
        entry.evictWhenReady = true;
        evictEntry(entry);
    });
}

function getEntry(path) {
    let entry = modelCache.get(path);
    if (entry?.failedAt && Date.now() - entry.failedAt >= FAILED_RETRY_DELAY_MS) {
        modelCache.delete(path);
        entry = null;
    }
    if (entry) return entry;
    entry = { path, refs: 0, loading: true, scene: null, evictWhenReady: false };
    modelCache.set(path, entry);
    entry.promise = getLoader()
        .then(loader => new Promise((resolve, reject) => loader.load(path, resolve, undefined, reject)))
        .then(gltf => {
            if (!gltf.scene) throw new Error(`GLB has no scene: ${path}`);
            entry.scene = gltf.scene;
            gltf.scene.animations = gltf.animations || [];
            gltf.scene.traverse(child => {
                if (child.geometry) sharedGeometries.add(child.geometry);
            });
            return gltf.scene;
        })
        .catch(error => {
            entry.failedAt = Date.now();
            throw error;
        })
        .finally(() => {
            entry.loading = false;
            if (!entry.refs && entry.evictWhenReady) evictEntry(entry);
        });
    return entry;
}

export function cloneSkeletons(source, model) {
    const clones = new Map();
    const collect = (original, clone) => {
        clones.set(original, clone);
        original.children.forEach((child, index) => collect(child, clone.children[index]));
    };
    collect(source, model);
    const skeletons = [];
    for (const [original, clone] of clones) {
        if (!original.isSkinnedMesh) continue;
        const sourceSkeleton = original.skeleton;
        let entry = skeletons.find(({ source }) => source.bones.length === sourceSkeleton.bones.length &&
            source.bones.every((bone, index) => bone === sourceSkeleton.bones[index] &&
                source.boneInverses[index].equals(sourceSkeleton.boneInverses[index])));
        if (!entry) {
            entry = { source: sourceSkeleton, clone: new THREE.Skeleton(
                sourceSkeleton.bones.map(bone => clones.get(bone)), sourceSkeleton.boneInverses) };
            skeletons.push(entry);
        }
        clone.bind(entry.clone, original.bindMatrix);
        // Границы меша описывают позу привязки, а персонаж может лечь.
        clone.frustumCulled = false;
    }
    return skeletons.map(entry => entry.clone);
}

function makeInstance(source, asset, dimensions, mount) {
    const nodeName = asset.visual.modelNode;
    const root = nodeName ? source.getObjectByName(nodeName) : source;
    if (!root) throw new Error(`GLB has no node ${nodeName}: ${asset.visual.modelPath}`);
    const model = root.clone(true);
    if (asset.visual.authoredHeight) mount.skeletons = cloneSkeletons(root, model);
    const instanceMaterials = new Map();
    model.traverse(child => {
        if (!child.isMesh) return;
        const cloneMaterial = material => {
            if (!instanceMaterials.has(material)) instanceMaterials.set(material, material.clone());
            return instanceMaterials.get(material);
        };
        child.material = Array.isArray(child.material)
            ? child.material.map(cloneMaterial) : cloneMaterial(child.material);
        if (asset.visual.shadowExcludedMaterials?.length) {
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            child.userData = { ...child.userData, importedNoShadow: materials.some(material =>
                asset.visual.shadowExcludedMaterials.includes(material.name)) };
        }
    });
    const visual = asset.visual;
    model.rotation.y = Number(visual.orientation || 0);
    const oriented = new THREE.Group();
    oriented.add(model);
    const container = new THREE.Group();
    container.add(oriented);
    container.animations = source.animations;
    if (visual.authoredHeight) {
        oriented.scale.setScalar(dimensions.height / visual.authoredHeight);
        return container;
    }
    oriented.updateMatrixWorld(true);
    const boundsRoot = visual.boundsNode ? model.getObjectByName(visual.boundsNode) || oriented : oriented;
    const bounds = new THREE.Box3().setFromObject(boundsRoot, Boolean(visual.groundContactRatio));
    const size = bounds.getSize(new THREE.Vector3());
    if (size.x <= 0 || size.y <= 0 || size.z <= 0) throw new Error(`GLB has empty bounds: ${visual.modelPath}`);
    const base = asset.dimensions || [1, 1, 1];
    const display = visual.visualDimensions || base;
    const width = dimensions.width * display[0] / base[0];
    const depth = dimensions.depth * display[1] / base[1];
    const height = dimensions.height * display[2] / base[2];
    if (visual.uniformScale) oriented.scale.setScalar(height / size.y);
    else oriented.scale.set(width / size.x, height / size.y, depth / size.z);
    const center = bounds.getCenter(new THREE.Vector3());
    const groundContact = bounds.min.y + size.y * (visual.groundContactRatio || 0);
    oriented.position.set(-center.x * oriented.scale.x,
        -groundContact * oriented.scale.y + Number(visual.groundClearance || 0),
        -center.z * oriented.scale.z);
    if (visual.pivot === 'center') oriented.position.y -= height / 2;
    return container;
}

export function mountLocationModel(group, asset, dimensions, onReady, onError) {
    const path = asset?.visual?.modelPath;
    if (asset?.visual?.renderMode !== 'glb' || !path) return false;
    const entry = getEntry(path);
    entry.refs += 1;
    entry.evictWhenReady = false;
    const mount = { active: true, entry };
    mounts.set(group, mount);
    const attach = source => {
        if (!mount.active) return;
        const instance = makeInstance(source, asset, dimensions, mount);
        if (!mount.active) return;
        onReady(instance);
    };
    const fail = error => {
        if (!mount.active) return;
        onError?.(error);
    };
    if (entry.scene) {
        try { attach(entry.scene); } catch (error) { fail(error); }
    } else {
        entry.promise.then(attach).catch(fail);
    }
    return true;
}

export function releaseLocationModel(group) {
    const mount = mounts.get(group);
    if (!mount || !mount.active) return;
    mount.active = false;
    mount.skeletons?.forEach(skeleton => skeleton.dispose?.());
    mounts.delete(group);
    mount.entry.refs -= 1;
    trimCache();
}

export function isSharedLocationModelGeometry(geometry) {
    return sharedGeometries.has(geometry);
}

export function clearUnusedLocationModels() {
    trimCache(true);
}
