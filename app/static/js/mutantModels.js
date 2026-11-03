import * as THREE from 'three';
import { mountLocationModel } from './locationModelAssets.js';

const BOAR_ASSET={dimensions:[1.05,2.12,1.1],visual:{renderMode:'glb',modelPath:'/static/models/mutants/boar/sekach-game.glb'}};

function importedBoar(outputEncoding){
    const group=new THREE.Group(),placeholder=quadruped('Кабан');
    group.userData={isCharacter:true,mutantModel:true,labelHeight:1.4};
    group.add(placeholder);
    mountLocationModel(group,BOAR_ASSET,{width:1.05,depth:2.12,height:1.1},model=>{
        group.remove(placeholder);
        placeholder.traverse(child=>{child.geometry?.dispose();if(child.material)child.material.dispose();});
        model.traverse(child=>{
            if(!child.isMesh)return;
            child.castShadow=true;child.receiveShadow=true;
            for(const material of Array.isArray(child.material)?child.material:[child.material]){
                // Как у растительности: цветовая карта должна соответствовать выводу сцены.
                // Иначе sRGB декодируется, но обратно не кодируется, и шкура чернеет.
                if(material?.map && material.map.encoding!==outputEncoding){
                    material.map.encoding=outputEncoding;material.map.needsUpdate=true;material.needsUpdate=true;
                }
            }
        });
        group.add(model);group.userData.importedModelReady=true;
        group.dispatchEvent({type:'model-ready'});
    },error=>{group.userData.importedModelError=error.message;});
    return group;
}

export const ALL_MUTANT_TYPES = [
    'Слепыш', 'Собака', 'Лжепёс', 'Кот', 'Кабан', 'Туша', 'Электроворона',
    'Болотник', 'Топотун', 'Одичалый', 'Кровопийца', 'Жмурка', 'Колобок',
    'Псионик', 'Зомбированный', 'Аномальная душа',
];

export const MODELED_MUTANT_TYPES = [
    'Слепыш', 'Собака', 'Лжепёс', 'Кот', 'Кабан', 'Туша',
    'Электроворона', 'Одичалый', 'Кровопийца', 'Зомбированный',
];

function addPart(group, geometry, color, position, scale = null, rotation = null) {
    const mesh = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ color }));
    mesh.position.set(...position);
    if (scale) mesh.scale.set(...scale);
    if (rotation) mesh.rotation.set(...rotation);
    mesh.castShadow = true;
    group.add(mesh);
    return mesh;
}

function ellipsoid(group, color, position, scale) {
    return addPart(group, new THREE.SphereGeometry(0.5, 10, 7), color, position, scale);
}

function cone(group, color, position, radius, height, rotation = null) {
    return addPart(group, new THREE.ConeGeometry(radius, height, 6), color,
        position, null, rotation);
}

function mergeParts(group) {
    group.updateMatrixWorld(true);
    const positions = [];
    const normals = [];
    const colors = [];
    for (const part of [...group.children]) {
        const geometry = part.geometry.index ? part.geometry.toNonIndexed() : part.geometry.clone();
        geometry.applyMatrix4(part.matrix);
        const position = geometry.getAttribute('position');
        const normal = geometry.getAttribute('normal');
        const tint = part.material.color;
        for (let index = 0; index < position.count; index++) {
            positions.push(position.getX(index), position.getY(index), position.getZ(index));
            normals.push(normal.getX(index), normal.getY(index), normal.getZ(index));
            colors.push(tint.r, tint.g, tint.b);
        }
        geometry.dispose();
        part.geometry.dispose();
        part.material.dispose();
        group.remove(part);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const mesh = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ vertexColors: true }));
    mesh.castShadow = true;
    group.add(mesh);
    return group;
}

function quadruped(type) {
    const group = new THREE.Group();
    const small = type === 'Слепыш' || type === 'Кот';
    const boar = type === 'Кабан' || type === 'Туша';
    const large = type === 'Лжепёс' || type === 'Туша';
    const hide = type === 'Слепыш' ? 0x746b60 : type === 'Кот' ? 0x75694e
        : boar ? 0x654d3b : type === 'Лжепёс' ? 0x564a44 : 0x817564;
    const bodyY = small ? 0.32 : large ? 0.6 : 0.48;
    const height = small ? 0.4 : large ? 0.7 : 0.55;
    ellipsoid(group, hide, [0, bodyY, -0.08], [small ? 0.63 : 0.9, height, boar ? 1.55 : 1.38]);
    const head = ellipsoid(group, hide, [0, bodyY + height * 0.12, 0.49],
        [small ? 0.42 : 0.59, small ? 0.4 : 0.54, boar ? 0.7 : 0.56]);
    for (const x of [-0.27, 0.27]) for (const z of [-0.47, 0.4]) {
        ellipsoid(group, hide, [x * (small ? 0.65 : 1), bodyY * 0.46, z],
            [small ? 0.12 : 0.19, bodyY * 0.95, small ? 0.16 : 0.2]);
    }
    if (boar) {
        ellipsoid(group, 0x9c7668, [0, bodyY + 0.03, 0.8], [0.45, 0.34, 0.28]);
        for (const x of [-0.23, 0.23]) cone(group, 0xe3d1aa,
            [x, bodyY - 0.01, 0.75], 0.07, 0.27, [0.45, 0, x > 0 ? -0.3 : 0.3]);
    } else {
        ellipsoid(group, hide, [0, bodyY - 0.06, 0.78], [0.3, 0.21, 0.39]);
        if (type === 'Слепыш') {
            for (const x of [-0.2, 0.2]) ellipsoid(group, 0xb18d85,
                [x, bodyY + 0.18, 0.51], [0.22, 0.26, 0.09]);
        } else {
            for (const x of [-0.22, 0.22]) cone(group, hide,
                [x, bodyY + 0.41, 0.43], small ? 0.11 : 0.16, small ? 0.28 : 0.35);
            for (const x of [-0.19, 0.19]) ellipsoid(group, 0xd0bb84,
                [x, bodyY + 0.15, 0.84], [0.07, 0.06, 0.045]);
        }
    }
    if (type !== 'Туша') cone(group, hide, [0, bodyY + 0.1, -0.91],
        small ? 0.07 : 0.11, small ? 0.38 : 0.55, [Math.PI / 2.8, 0, 0]);
    head.userData.mutantHead = true;
    group.userData.isCharacter = true;
    group.userData.mutantModel = true;
    group.userData.labelHeight = small ? 1.05 : large ? 1.5 : 1.3;
    return mergeParts(group);
}

function crow() {
    const group = new THREE.Group();
    ellipsoid(group, 0x282d32, [0, 0.55, 0], [0.44, 0.4, 0.76]);
    ellipsoid(group, 0x22272a, [0, 0.78, 0.31], [0.35, 0.34, 0.37]);
    cone(group, 0x8c9392, [0, 0.75, 0.61], 0.13, 0.38, [Math.PI / 2, 0, 0]);
    for (const side of [-1, 1]) {
        ellipsoid(group, 0x333d44, [side * 0.4, 0.58, -0.1], [0.65, 0.08, 0.43]);
        ellipsoid(group, 0xa2eff0, [side * 0.2, 0.84, 0.46], [0.055, 0.055, 0.035]);
        addPart(group, new THREE.CylinderGeometry(0.025, 0.025, 0.3, 5),
            0x616761, [side * 0.18, 0.18, -0.05]);
    }
    group.userData.isCharacter = true;
    group.userData.mutantModel = true;
    group.userData.labelHeight = 1.3;
    return mergeParts(group);
}

function humanoid(type) {
    const group = new THREE.Group();
    const bloodsucker = type === 'Кровопийца';
    const color = bloodsucker ? 0x6c6862 : type === 'Одичалый' ? 0x786c56 : 0x657064;
    ellipsoid(group, color, [0, 0.67, 0], [0.66, 0.86, 0.4]);
    ellipsoid(group, bloodsucker ? 0x8a7770 : 0x9a9180, [0, 1.16, 0.08], [0.39, 0.42, 0.35]);
    for (const side of [-1, 1]) {
        ellipsoid(group, color, [side * 0.43, 0.72, 0.02], [0.22, 0.72, 0.24]);
        ellipsoid(group, color, [side * 0.17, 0.24, 0], [0.23, 0.48, 0.26]);
        if (bloodsucker) {
            for (const z of [0.25, 0.39]) cone(group, 0x9c7d74,
                [side * (z + 0.01), 0.97 - z * 0.5, 0.39], 0.055, 0.32,
                [Math.PI / 2.7, 0, side * 0.2]);
        }
    }
    group.userData.isCharacter = true;
    group.userData.mutantModel = true;
    group.userData.labelHeight = 1.7;
    return mergeParts(group);
}

export function createMutantModel(type, outputEncoding = THREE.LinearEncoding) {
    if (!MODELED_MUTANT_TYPES.includes(type)) return null;
    if(type==='Кабан')return importedBoar(outputEncoding);
    if (type === 'Электроворона') return crow();
    if (['Кровопийца', 'Зомбированный', 'Одичалый'].includes(type)) return humanoid(type);
    return quadruped(type);
}
