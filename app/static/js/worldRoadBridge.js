import * as THREE from 'three';
import { buildWorldBridgeLayout } from './worldBridgeLayout.js';

let resources=null,loading=null;
export function loadWorldRoadBridge() {
    if(loading)return loading;
    loading=import('three/addons/loaders/GLTFLoader.js')
        .then(({GLTFLoader})=>new Promise((resolve,reject)=>new GLTFLoader().load('/static/models/art/modular_road_bridge.glb',resolve,undefined,reject)))
        .then(gltf=>{
            const parts=[];
            gltf.scene.traverse(m=>{if(m.isMesh)parts.push({name:m.name,geometry:m.geometry,material:m.material});});
            const anchors=gltf.parser.json.asset.extras.pierAnchors;
            if(parts.length!==5||anchors.length!==2)throw new Error('Unexpected Modular Road Bridge parts');
            // The world renderer currently outputs linear values directly.
            // Keep glTF's sRGB input decoding and encode only this asset's output,
            // rather than changing lighting/colour handling of the whole map.
            for(const material of new Set(parts.map(p=>p.material))){
                material.onBeforeCompile=(shader,renderer)=>{
                    if(renderer.outputColorSpace===THREE.LinearSRGBColorSpace)shader.fragmentShader=shader.fragmentShader.replace(
                        '#include <colorspace_fragment>','#include <colorspace_fragment>\ngl_FragColor = sRGBTransferOETF(gl_FragColor);');
                };
                material.customProgramCacheKey=()=> 'world-bridge-srgb-output-v1';
            }
            resources={parts,anchors,preview:new Map()};
            return resources;
        }).catch(error=>{loading=null;throw error;});
    return loading;
}

export function createWorldRoadBridgeMeshes(options,preview=false) {
    if(!resources)return [];
    const sections=buildWorldBridgeLayout(options);
    if(!sections.length)return [];
    const deck=[],piers=[],pierOwners=[];
    const dummy=new THREE.Object3D(),xAxis=new THREE.Vector3(),yAxis=new THREE.Vector3(),zAxis=new THREE.Vector3();
    const basis=new THREE.Matrix4();
    for(const section of sections){
        const {position:p,direction:d,scale:s}=section;
        xAxis.set(d.x,d.y,d.z);
        zAxis.set(-d.z,0,d.x).normalize();
        yAxis.crossVectors(zAxis,xAxis).normalize();
        basis.makeBasis(xAxis,yAxis,zAxis);
        dummy.position.set(p.x,p.y,p.z);dummy.quaternion.setFromRotationMatrix(basis);dummy.scale.set(section.length,s,s);dummy.updateMatrix();
        deck.push(dummy.matrix.clone());
        const rotation=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),-Math.atan2(d.z,d.x));
        for(const anchor of resources.anchors){
            const top=new THREE.Vector3(...anchor).applyMatrix4(dummy.matrix);
            const tile=options.getTile(Math.floor(top.x),Math.floor(top.z));
            // The current water renderer has no bathymetry. Extend piers below
            // the water to the existing world foundation, without changing it.
            const bottom=tile?.terrain==='water'?Math.min(0,Number(tile.height)-.1):options.sampleHeight(top.x,top.z)-.02;
            const height=top.y-bottom;
            if(height>.005){piers.push(new THREE.Matrix4().compose(top,rotation,new THREE.Vector3(s,height,s)));pierOwners.push(section.owner);}
        }
    }
    return resources.parts.map(part=>{
        let material=part.material;
        if(preview){
            if(!resources.preview.has(material)){
                const copy=material.clone();copy.transparent=true;copy.opacity=.75;copy.depthWrite=false;copy.depthTest=false;
                copy.onBeforeCompile=material.onBeforeCompile;copy.customProgramCacheKey=material.customProgramCacheKey;
                resources.preview.set(material,copy);
            }
            material=resources.preview.get(material);
        }
        const matrices=part.name==='pier'?piers:deck;
        if(!matrices.length)return null;
        const mesh=new THREE.InstancedMesh(part.geometry,material,matrices.length);
        matrices.forEach((matrix,i)=>mesh.setMatrixAt(i,matrix));mesh.instanceMatrix.needsUpdate=true;
        mesh.frustumCulled=false;mesh.castShadow=false;mesh.receiveShadow=true;
        mesh.userData.worldBridgeAsset=true;mesh.userData.bridgePart=part.name;
        mesh.userData.bridgeOwners=part.name==='pier'?pierOwners:sections.map(s=>s.owner);
        mesh.userData.bridgeOwners.forEach((owner,i)=>{
            const color=options.getTile(owner.x,owner.z)?.objects?.find(o=>o.type==='road')?.color;
            mesh.setColorAt(i,new THREE.Color(color&&color!=='#756f61'?color:'#ffffff'));
        });
        mesh.instanceColor.needsUpdate=true;
        return mesh;
    }).filter(Boolean);
}
