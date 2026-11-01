// Display cutaways affect the main camera only. Original roots still own
// interaction, fog and edits; temporarily restore their physical shadow form.
export function createLocationShadowPass(renderer,roots,withOriginals=draw=>draw()) {
    const original=renderer.shadowMap.render;
    function run(draw){
        const changes=[];
        const set=(o,key,value)=>{if(o[key]!==value){changes.push([o,key,o[key]]);o[key]=value;}};
        for(const root of roots()){
            const visible=root.userData?.physicalShadowVisible;
            if(visible===undefined)continue;
            set(root,'visible',visible);
            if(!visible)continue;
            for(const child of root.userData.physicalShadowMeshes||[]){
                const base=child.userData?.physicalShadow;
                if(!base)continue;
                set(child,'visible',base.visible);set(child,'castShadow',base.castShadow);
                for(const material of Array.isArray(child.material)?child.material:[child.material])if(material){set(material,'opacity',base.opacity);set(material,'side',base.side);}
            }
        }
        try{return draw();}finally{for(let i=changes.length-1;i>=0;i--){const [o,key,value]=changes[i];o[key]=value;}}
    }
    renderer.shadowMap.render=function(...args){return withOriginals(()=>run(()=>original.apply(this,args)));};
    return{run,dispose(){renderer.shadowMap.render=original;}};
}

// The sun and its shadow camera are fixed in the location. Reuse their map
// until a caster (including its parents), geometry or light actually changes.
// Local lamp shadows retain their own existing update policy.
export function createLocationShadowCache(light) {
    const shadow=light.shadow,previousAutoUpdate=shadow.autoUpdate;
    let records=new WeakMap(),frame=0,lastCount=-1;
    shadow.autoUpdate=false;
    shadow.needsUpdate=true;
    function update(scene,renderer) {
        if(!renderer.shadowMap.enabled)return;
        frame++;
        let dirty=Boolean(renderer.shadowMap.needsUpdate),count=0;
        function record(node) {
            if(!node)return;
            let old=records.get(node);
            if(old?.frame===frame)return;
            const p=node.position,q=node.quaternion,s=node.scale,g=node.geometry;
            const values=[p?.x,p?.y,p?.z,q?.x,q?.y,q?.z,q?.w,s?.x,s?.y,s?.z,
                node.visible,node.castShadow,node.layers?.mask,g?.id,g?.attributes?.position?.version,
                g?.attributes?.normal?.version,g?.index?.version,node.instanceMatrix?.version,g?.drawRange?.start,g?.drawRange?.count];
            if(!old||old.frame!==frame-1||values.some((v,i)=>v!==old.values[i]))dirty=true;
            if(!old){old={};records.set(node,old);}
            old.frame=frame;old.values=values;
        }
        scene.traverseVisible(node=>{
            if(!node.isMesh||!node.castShadow)return;
            count++;
            // Skeletal/morph/custom depth animation can change without a local
            // transform; those scenes keep the ordinary per-frame shadow pass.
            if(node.isSkinnedMesh||node.morphTargetInfluences?.length||node.customDepthMaterial)dirty=true;
            for(let item=node;item;item=item.parent)record(item);
            for(const material of Array.isArray(node.material)?node.material:[node.material]){
                if(!material)continue;
                let old=records.get(material);
                // Один материал часто используется десятками частей модели.
                // В пределах обхода его состояние достаточно сравнить один раз.
                if(old?.frame===frame)continue;
                const values=[material.version,material.visible,material.opacity,material.alphaTest,
                    material.map?.version,material.alphaMap?.version,material.displacementMap?.version,
                    material.displacementScale,material.displacementBias];
                if(!old||(old.frame!==frame&&old.frame!==frame-1)||values.some((v,i)=>v!==old.values[i]))dirty=true;
                if(!old){old={};records.set(material,old);}
                old.frame=frame;old.values=values;
            }
        });
        record(light);record(light.target);
        const camera=shadow.camera;
        const projection=[camera.left,camera.right,camera.top,camera.bottom,camera.near,camera.far,
            shadow.mapSize.x,shadow.mapSize.y,light.visible,light.castShadow];
        if(!update.projection||projection.some((v,i)=>v!==update.projection[i]))dirty=true;
        update.projection=projection;
        if(count!==lastCount)dirty=true;
        lastCount=count;
        if(dirty)shadow.needsUpdate=true;
    }
    return {update,dispose(){shadow.autoUpdate=previousAutoUpdate;records=new WeakMap();}};
}
