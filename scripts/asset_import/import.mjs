import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, copyFile, stat, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { NodeIO } from '@gltf-transform/core';
import { KHRONOS_EXTENSIONS } from '@gltf-transform/extensions';
import { dedup, prune, textureCompress } from '@gltf-transform/functions';
import sharp from 'sharp';
import { filterFenceComponents } from './fenceGeometry.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const sourceRoot = path.join(root, 'assets', 'sources', 'polyhaven');
const modelRoot = path.join(root, 'app', 'static', 'models', 'art');
const materialRoot = path.join(root, 'app', 'static', 'materials', 'art');
const catalogPath = path.join(root, 'app', 'static', 'js', 'locationImportedAssets.js');
const editorCatalogPath = path.join(root, 'app', 'static', 'js', 'locationAssetCatalog.js');
const manifestPath = path.join(here, 'assets.json');
const statePath = path.join(here, 'import-state.json');
const allowedExtensions = new Set(['KHR_texture_transform', 'KHR_materials_unlit']);
const io = new NodeIO().registerExtensions(KHRONOS_EXTENSIONS);

function hash(data) {
    return createHash('sha256').update(data).digest('hex');
}

async function fileHash(file) {
    return hash(await readFile(file));
}

async function exists(file) {
    try { await stat(file); return true; } catch { return false; }
}

function safeId(value) {
    if (!/^[a-zA-Z0-9_-]+$/.test(value)) throw new Error(`Invalid asset id: ${value}`);
    return value;
}

function argumentValue(args, flag) {
    const index = args.indexOf(flag);
    if (index < 0) return null;
    if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing value after ${flag}`);
    return args[index + 1];
}

function positive(value, flag) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) throw new Error(`${flag} must be a positive number`);
    return number;
}

async function updateManifestFromArgs(manifest, args) {
    const add = argumentValue(args, '--add');
    const addMaterial = argumentValue(args, '--add-material');
    const set = argumentValue(args, '--set');
    if ([add, addMaterial, set].filter(Boolean).length > 1) {
        throw new Error('Use only one of --add, --add-material or --set');
    }
    if (!add && !addMaterial && !set) return;
    let asset;
    if (add || addMaterial) {
        const id = safeId(argumentValue(args, '--id') || addMaterial || '');
        if (manifest.models.concat(manifest.materials).some(item => item.id === id)) {
            throw new Error(`Asset id already exists: ${id}`);
        }
        const baseType = argumentValue(args, '--base-type');
        const allowed = add ? ['box', 'barrel', 'fence'] : ['wall', 'floor'];
        if (!allowed.includes(baseType)) throw new Error(`--base-type must be ${allowed.join(', ')}`);
        const defaults = { box: [0.8, 0.8, 0.7], barrel: [0.65, 0.65, 0.9],
            fence: [2, 0.15, 1.2], wall: [2, 0.2, 2.4], floor: [2, 2, 0.12] };
        asset = { id, label: argumentValue(args, '--label') || id, baseType,
            dimensions: defaults[baseType], license: argumentValue(args, '--license') || 'User supplied' };
        if (add) {
            const file = path.resolve(root, add);
            if (!file.startsWith(root + path.sep) || !await exists(file)
                || !/\.(glb|gltf)$/i.test(file)) {
                throw new Error(`Place a GLB/glTF file inside the repository first: ${add}`);
            }
            asset.source = { path: path.relative(root, file).replaceAll('\\', '/') };
            await fetchSource(asset, 'model', true);
            asset.pivot = 'bottom-center';
            asset.orientation = 0;
            asset.groundClearance = 0.008;
            manifest.models.push(asset);
        } else {
            const maps = {};
            for (const slot of ['color', 'normal', 'arm']) {
                const value = argumentValue(args, `--${slot}`);
                const file = path.resolve(root, value || '');
                if (!value || !file.startsWith(root + path.sep) || !await exists(file)
                    || !/\.(jpg|jpeg|png)$/i.test(file)) {
                    throw new Error(`--${slot} must name an existing JPEG/PNG inside the repository`);
                }
                maps[slot] = path.relative(root, file).replaceAll('\\', '/');
            }
            asset.source = { maps };
            manifest.materials.push(asset);
        }
    } else {
        asset = manifest.models.find(item => item.id === set);
        if (!asset) throw new Error(`Unknown model id: ${set}`);
    }
    const axisFlags = ['--width', '--depth', '--height'];
    for (let index = 0; index < axisFlags.length; index++) {
        const value = argumentValue(args, axisFlags[index]);
        if (value !== null) asset.dimensions[index] = positive(value, axisFlags[index]);
    }
    const angle = argumentValue(args, '--orientation-deg');
    if (angle !== null) {
        if (!Number.isFinite(Number(angle))) throw new Error('--orientation-deg must be a number');
        asset.orientation = Number(angle) * Math.PI / 180;
    }
    const pivot = argumentValue(args, '--pivot');
    if (pivot) {
        if (!['bottom-center', 'center'].includes(pivot)) throw new Error('--pivot must be bottom-center or center');
        asset.pivot = pivot;
    }
    const clearance = argumentValue(args, '--ground-clearance');
    if (clearance !== null) {
        const value = Number(clearance);
        if (!Number.isFinite(value) || value < 0) throw new Error('--ground-clearance must be non-negative');
        asset.groundClearance = value;
    }
    const label = argumentValue(args, '--label');
    if (label) asset.label = label;
    await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
    console.log(`manifest updated: ${asset.id}`);
}

async function request(url) {
    const parsed = new URL(url);
    if (!['api.polyhaven.com', 'dl.polyhaven.org'].includes(parsed.hostname) || parsed.protocol !== 'https:') {
        throw new Error(`Download host is not trusted: ${url}`);
    }
    const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
    return Buffer.from(await response.arrayBuffer());
}

async function apiJson(url) {
    return JSON.parse((await request(url)).toString('utf8'));
}

async function download(entry, target) {
    if (!entry?.url || !entry?.md5 || !Number.isFinite(entry.size)) {
        throw new Error(`Poly Haven did not provide a verifiable download for ${target}`);
    }
    if (entry.size > 20 * 1024 * 1024) throw new Error(`File exceeds the 20 MB pilot limit: ${target}`);
    if (await exists(target)) {
        const old = await readFile(target);
        if (createHash('md5').update(old).digest('hex') === entry.md5) return false;
    }
    const bytes = await request(entry.url);
    if (bytes.length !== entry.size || createHash('md5').update(bytes).digest('hex') !== entry.md5) {
        throw new Error(`Checksum/size mismatch: ${entry.url}`);
    }
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, bytes);
    return true;
}

async function verifyCached(entry, target) {
    if (!await exists(target)) throw new Error(`Offline source missing: ${target}`);
    const bytes = await readFile(target);
    if (bytes.length !== entry.size || createHash('md5').update(bytes).digest('hex') !== entry.md5) {
        throw new Error(`Offline source is corrupt: ${target}; run online to repair it`);
    }
}

async function fetchSource(asset, type, offline = false) {
    if (asset.source.maps) {
        if (type !== 'material') throw new Error(`Use source.path for models: ${asset.id}`);
        const maps = {};
        for (const slot of ['color', 'normal', 'arm']) {
            const file = path.resolve(root, asset.source.maps[slot] || '');
            if (!file.startsWith(root + path.sep) || !await exists(file)) {
                throw new Error(`Local ${slot} texture missing: ${file}`);
            }
            maps[slot] = file;
        }
        return { maps, sourceUrl: null, license: asset.license || 'User supplied; verify before redistribution' };
    }
    if (asset.source.path) {
        const file = path.resolve(root, asset.source.path);
        if (!file.startsWith(root + path.sep) || !await exists(file)) throw new Error(`Source file missing: ${file}`);
        const json = await sourceJson(file);
        const dependencies = [...(json.buffers || []), ...(json.images || [])]
            .map(item => item.uri).filter(uri => uri && !uri.startsWith('data:'));
        for (const dependency of dependencies) {
            const dest = path.resolve(path.dirname(file), dependency);
            if (!dest.startsWith(path.dirname(file) + path.sep) || !await exists(dest)) {
                throw new Error(`Local glTF dependency missing or unsafe: ${dependency}`);
            }
        }
        return { file, sourceUrl: null,
            license: asset.license || 'User supplied; verify before redistribution', dependencies };
    }
    if (asset.source.provider !== 'polyhaven') throw new Error(`Unsupported source provider for ${asset.id}`);
    const id = safeId(asset.source.id);
    const resolution = asset.source.resolution || '1k';
    if (!['1k', '2k'].includes(resolution)) throw new Error(`Use 1k or 2k resolution: ${asset.id}`);
    const dir = path.join(sourceRoot, id, resolution);
    const apiCache = path.join(dir, 'files-api.json');
    let files;
    if (offline) {
        if (!await exists(apiCache)) throw new Error(`No cached Poly Haven manifest for ${id}; run once online`);
        files = JSON.parse(await readFile(apiCache, 'utf8'));
    } else {
        files = await apiJson(`https://api.polyhaven.com/files/${id}`);
        await mkdir(dir, { recursive: true });
        await writeFile(apiCache, JSON.stringify(files) + '\n');
    }
    const sourceUrl = `https://polyhaven.com/a/${id}`;
    if (type === 'model') {
        const gltf = files.gltf?.[resolution]?.gltf;
        if (!gltf) throw new Error(`No ${resolution} glTF at ${sourceUrl}`);
        const file = path.join(dir, `${id}_${resolution}.gltf`);
        if (offline) await verifyCached(gltf, file);
        else await download(gltf, file);
        for (const [name, dependency] of Object.entries(gltf.include || {})) {
            const dest = path.resolve(dir, name);
            if (!dest.startsWith(dir + path.sep)) throw new Error(`Unsafe dependency path: ${name}`);
            if (offline) await verifyCached(dependency, dest);
            else await download(dependency, dest);
        }
        return { file, sourceUrl, license: 'CC0', dependencies: Object.keys(gltf.include || {}) };
    }
    const maps = {};
    for (const [slot, apiName] of Object.entries({ color: 'Diffuse', normal: 'nor_gl', arm: 'arm' })) {
        const entry = files[apiName]?.[resolution]?.jpg || files[apiName]?.[resolution]?.png;
        if (!entry) throw new Error(`Missing ${apiName} ${resolution} PBR map at ${sourceUrl}`);
        const extension = entry.url.endsWith('.png') ? '.png' : '.jpg';
        maps[slot] = path.join(dir, `${id}_${slot}${extension}`);
        if (offline) await verifyCached(entry, maps[slot]);
        else await download(entry, maps[slot]);
    }
    return { maps, sourceUrl, license: 'CC0' };
}

function assertCompatible(json, label) {
    const unsupported = (json.extensionsRequired || []).filter(name => !allowedExtensions.has(name));
    if (unsupported.length) throw new Error(`${label}: unsupported required glTF extensions: ${unsupported.join(', ')}`);
    for (const image of json.images || []) {
        if (image.uri && !/\.(jpg|jpeg|png)$/i.test(image.uri)) {
            throw new Error(`${label}: use JPEG or PNG, not ${image.uri}`);
        }
        if (image.mimeType && !['image/jpeg', 'image/png'].includes(image.mimeType)) {
            throw new Error(`${label}: unsupported image type ${image.mimeType}`);
        }
    }
}

async function sourceJson(file) {
    const bytes = await readFile(file);
    if (file.toLowerCase().endsWith('.gltf')) return JSON.parse(bytes.toString('utf8'));
    if (bytes.toString('ascii', 0, 4) !== 'glTF') throw new Error(`Not a GLB: ${file}`);
    const size = bytes.readUInt32LE(12);
    if (bytes.toString('ascii', 16, 20) !== 'JSON') throw new Error(`GLB JSON chunk missing: ${file}`);
    return JSON.parse(bytes.subarray(20, 20 + size).toString('utf8'));
}

function sceneStats(doc) {
    const meshes = doc.getRoot().listMeshes();
    let triangles = 0;
    let primitives = 0;
    for (const mesh of meshes) for (const primitive of mesh.listPrimitives()) {
        primitives++;
        const count = primitive.getIndices()?.getCount() || primitive.getAttribute('POSITION')?.getCount() || 0;
        triangles += Math.floor(count / 3);
    }
    return { meshes: meshes.length, primitives, triangles,
        materials: doc.getRoot().listMaterials().length,
        textures: doc.getRoot().listTextures().length };
}

async function restoreCutoutAlpha(doc, materialName) {
    const material = doc.getRoot().listMaterials().find(item => item.getName() === materialName);
    const texture = material?.getBaseColorTexture();
    if (!texture?.getImage()) throw new Error(`Cutout material has no color texture: ${materialName}`);
    const { data, info } = await sharp(texture.getImage()).removeAlpha().raw().toBuffer({ resolveWithObject: true });
    const rgba = Buffer.alloc(info.width * info.height * 4);
    for (let pixel = 0; pixel < info.width * info.height; pixel++) {
        const source = pixel * info.channels;
        const target = pixel * 4;
        rgba[target] = data[source];
        rgba[target + 1] = data[source + 1];
        rgba[target + 2] = data[source + 2];
        // The source JPEG contains black holes, not alpha. Keep the metal strands opaque.
        rgba[target + 3] = Math.max(data[source], data[source + 1], data[source + 2]) >= 24 ? 255 : 0;
    }
    texture.setImage(await sharp(rgba, { raw: { width: info.width, height: info.height, channels: 4 } })
        .png().toBuffer()).setMimeType('image/png');
    material.setAlphaMode('BLEND').setDoubleSided(true);
}

async function importModel(asset, state, force, offline) {
    const source = await fetchSource(asset, 'model', offline);
    const configHash = hash(JSON.stringify(asset));
    const inputs = [source.file, ...(source.dependencies || []).map(name => path.join(path.dirname(source.file), name))];
    const inputHash = hash((await Promise.all(inputs.map(fileHash))).join(':') + configHash);
    const output = path.join(modelRoot, `${safeId(asset.id)}.glb`);
    const metadata = path.join(modelRoot, `${asset.id}.json`);
    if (!force && state[asset.id] === inputHash && await exists(output) && await exists(metadata)) {
        console.log(`unchanged: ${asset.id}`);
        return JSON.parse(await readFile(metadata, 'utf8'));
    }
    assertCompatible(await sourceJson(source.file), asset.id);
    const doc = await io.read(source.file);
    if (asset.keepNodes?.length) {
        const wanted = new Set(asset.keepNodes);
        for (const scene of doc.getRoot().listScenes()) {
            for (const node of scene.listChildren()) if (!wanted.has(node.getName())) scene.removeChild(node);
        }
        const kept = doc.getRoot().listScenes().flatMap(scene => scene.listChildren());
        if (!kept.length) throw new Error(`${asset.id}: keepNodes did not match any scene nodes`);
    }
    for (const instance of asset.nodeInstances || []) {
        const sourceNode = doc.getRoot().listNodes().find(node => node.getName() === instance.sourceNode);
        if (!sourceNode?.getMesh()) throw new Error(`Missing assembly mesh: ${instance.sourceNode}`);
        const node = doc.createNode(instance.name).setMesh(sourceNode.getMesh()).setTranslation(instance.translation);
        doc.getRoot().listScenes()[0].addChild(node);
    }
    await doc.transform(prune({ keepExtras: true }));
    const removedFenceComponents = asset.stripFenceBraces
        ? filterFenceComponents(doc, asset.stripFenceBraces) : 0;
    if (asset.cutoutMaterial) await restoreCutoutAlpha(doc, asset.cutoutMaterial);
    await doc.transform(prune({ keepExtras: true }), dedup());
    if (asset.maxTextureSize) {
        await doc.transform(textureCompress({ encoder: sharp,
            resize: [asset.maxTextureSize, asset.maxTextureSize] }));
    }
    const stats = sceneStats(doc);
    if (!stats.meshes || !stats.triangles || !stats.materials) {
        throw new Error(`${asset.id}: scene has no usable geometry/materials`);
    }
    await mkdir(modelRoot, { recursive: true });
    const temp = `${output}.tmp.glb`;
    await io.write(temp, doc);
    const packed = await sourceJson(temp);
    assertCompatible(packed, asset.id);
    if ((packed.images || []).some(image => image.uri) || (packed.buffers || []).some(buffer => buffer.uri)) {
        throw new Error(`${asset.id}: output GLB has missing external resources`);
    }
    await copyFile(temp, output);
    await unlink(temp);
    const result = { id: asset.id, type: 'model', sourceUrl: source.sourceUrl,
        license: source.license, sourceFile: path.relative(root, source.file).replaceAll('\\', '/'),
        output: `/static/models/art/${asset.id}.glb`, bytes: (await stat(output)).size,
        sha256: await fileHash(output), ...stats, extensionsRequired: packed.extensionsRequired || [],
        orientation: asset.orientation || 0, pivot: asset.pivot || 'bottom-center',
        groundClearance: asset.groundClearance || 0, dimensions: asset.dimensions,
        ...(asset.stripFenceBraces ? { removedFenceComponents, assembly: asset.nodeInstances } : {}) };
    await writeFile(metadata, JSON.stringify(result, null, 2) + '\n');
    state[asset.id] = inputHash;
    console.log(`imported: ${asset.id} ${Math.round(result.bytes / 1024)} KB, ${stats.triangles} tris, ${stats.materials} materials`);
    return result;
}

async function importMaterial(asset, state, force, offline) {
    const source = await fetchSource(asset, 'material', offline);
    const inputHash = hash((await Promise.all(Object.values(source.maps).map(fileHash))).join(':') + JSON.stringify(asset));
    const dir = path.join(materialRoot, safeId(asset.id));
    const metaPath = path.join(dir, 'material.json');
    if (!force && state[asset.id] === inputHash && await exists(metaPath)) {
        console.log(`unchanged: ${asset.id}`);
        return JSON.parse(await readFile(metaPath, 'utf8'));
    }
    await mkdir(dir, { recursive: true });
    const maps = {};
    for (const [slot, file] of Object.entries(source.maps)) {
        const dest = path.join(dir, `${slot}${path.extname(file)}`);
        await copyFile(file, dest);
        maps[slot] = `/static/materials/art/${asset.id}/${path.basename(dest)}`;
    }
    const result = { id: asset.id, type: 'material', label: asset.label,
        sourceUrl: source.sourceUrl, license: source.license, maps,
        sha256: inputHash };
    await writeFile(metaPath, JSON.stringify(result, null, 2) + '\n');
    state[asset.id] = inputHash;
    console.log(`imported: ${asset.id} PBR color + normal + ARM`);
    return result;
}

async function main() {
    const argv = process.argv.slice(2);
    const args = new Set(argv);
    if (args.has('--help')) {
        console.log('Usage: npm run import -- [--offline] [--force]');
        console.log('Add local model: --add assets/sources/local/model.glb --id my_model --base-type box [--label Name]');
        console.log('Fix defaults: --set my_model --width 0.8 --depth 0.8 --height 0.7 --orientation-deg 90');
        console.log('Add PBR: --add-material my_mat --base-type wall --color file.jpg --normal file.jpg --arm file.jpg');
        console.log('Poly Haven downloads are verified with MD5 and cached under assets/sources/.');
        return;
    }
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
    await updateManifestFromArgs(manifest, argv);
    const state = await exists(statePath) ? JSON.parse(await readFile(statePath, 'utf8')) : {};
    const models = [];
    const materials = [];
    const errors = [];
    for (const asset of manifest.models || []) {
        if (args.has('--materials')) continue;
        try { models.push({ asset, meta: await importModel(asset, state, args.has('--force'), args.has('--offline')) }); }
        catch (error) { errors.push(`${asset.id}: ${error.message}`); }
    }
    for (const asset of manifest.materials || []) {
        if (args.has('--models')) continue;
        try { materials.push({ asset, meta: await importMaterial(asset, state, args.has('--force'), args.has('--offline')) }); }
        catch (error) { errors.push(`${asset.id}: ${error.message}`); }
    }
    await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
    if (!args.has('--models') && !args.has('--materials') && !errors.length) {
        const entries = models.map(({ asset, meta }) => ({ id: asset.id, label: asset.label,
            baseType: asset.baseType, kind: 'structure', group: 'Предметы и ограждения',
            ...(asset.artStatus ? { artStatus: asset.artStatus } : {}),
            icon: { box: '▣', barrel: '◉', fence: '╫' }[asset.baseType],
            color: { box: '#8b6b4f', barrel: '#8a6444', fence: '#8b7458' }[asset.baseType],
            dimensions: asset.dimensions,
            visual: { renderMode: 'glb', modelPath: meta.output,
                ...(asset.shadowExcludedMaterials ? { shadowExcludedMaterials: asset.shadowExcludedMaterials } : {}),
                pivot: meta.pivot, orientation: meta.orientation, groundClearance: meta.groundClearance,
                source: meta.sourceUrl, license: meta.license } }));
        entries.push(...materials.map(({ asset, meta }) => ({ id: `art_${asset.id}`,
            label: asset.label, baseType: asset.baseType, kind: 'structure', group: 'Поверхности',
            icon: asset.baseType === 'wall' ? '▥' : '▦',
            color: { wall: '#80786c', floor: '#776d5d' }[asset.baseType],
            dimensions: asset.dimensions,
            visual: { renderMode: 'pbr', maps: meta.maps, source: meta.sourceUrl, license: meta.license } })));
        await writeFile(catalogPath, `// Generated by scripts/asset_import/import.mjs. Edit assets.json, not this file.\nexport const IMPORTED_LOCATION_ASSETS = Object.freeze(${JSON.stringify(entries, null, 4)});\n`);
        const editorCatalog = await readFile(editorCatalogPath, 'utf8');
        const begin = '    // BEGIN IMPORTED ASSETS: generated by scripts/asset_import/import.mjs';
        const end = '    // END IMPORTED ASSETS';
        const first = editorCatalog.indexOf(begin);
        const last = editorCatalog.indexOf(end);
        if (first < 0 || last <= first) throw new Error('Imported asset markers missing from locationAssetCatalog.js');
        const inline = entries.map(item => `    ${JSON.stringify(item)},`).join('\n');
        await writeFile(editorCatalogPath, editorCatalog.slice(0, first + begin.length)
            + `\n${inline}\n` + editorCatalog.slice(last));
    }
    if (errors.length) {
        for (const error of errors) console.error(`ERROR ${error}`);
        process.exitCode = 1;
    }
    console.log(`Models: ${models.length}; materials: ${materials.length}; errors: ${errors.length}`);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
