import { Server } from './api.js';
import { rememberEquipmentChoice } from './equipmentChoices.js';

export function usesNightVision(data) {
    if (data?._artifact_profile?.blocked_devices?.electronics?.length) return false;
    const helmet=data?.equipment?.helmet;
    if(helmet?.protectionDisabled)return false;
    const modules=(helmet?.installedModules||[]).filter(module=>!module.protectionDisabled);
    const all=[...modules,...modules.filter(module=>module.slotType==='visor').flatMap(visor=>visor.installedModules||[])];
    return all.some(module=>!module.protectionDisabled&&(module.slotType==='nvg' || module.attributes?.night_vision)
        && module.active!==false && module.enabled!==false && !module.raised && (module.lightPower?.uses_left??10)>0);
}

export async function renderPortableLights(container,characterId,data,operate,isCurrent=()=>true,canEdit=true) {
    const result=await Server.getPortableLights(characterId);
    if (!isCurrent() || !result.lights.length) return;
    const root=document.createElement('section');root.className='loadout-group portable-lights';
    const heading=document.createElement('h4');heading.textContent='Приборы, свет и ПНВ';root.append(heading);
    const active=window.locationCombatState?.status==='active';
    const ownTurn=canEdit && (!active || window.locationCombatState?.current_character?.character_id===characterId);
    for (const light of result.lights) {
        const row=document.createElement('div');row.className='portable-light-row';
        row.dataset.lightId=light.id || JSON.stringify(light.path);
        const caption=document.createElement('strong');
        const mount=light.mount_available===false?'крепление недоступно':{hand:'в руке',head:'на голове',belt:'на поясе',taped:'на оружии',weapon:'модуль оружия'}[light.binding?.mount];
        caption.textContent=light.kind==='pda'
            ? `${light.name} · ${Number(light.pda_state?.charge_percent??100).toFixed(1)}% · ${light.pda_state?.on?'включён':'выключен'}`
            : light.kind==='glow'
            ? `${light.name} · ${light.activated?`${Math.ceil((light.remaining_seconds||0)/60)} мин осталось`:'не активирована'}${mount?` · ${mount}`:''}`
            : `${light.name} · ${light.uses_left}/10${mount?` · ${mount}`:''}${light.binding?` · ${light.binding.on?'включён':'выключен'}`:''}`;
        row.append(caption);
        if (light.blocked_reason) {caption.textContent+=light.burned?' · сгорел':' · подавлен артефактом';caption.title=light.blocked_reason;}
        const button=(text,operation,extra={},disabled=false)=>{
            const control=document.createElement('button');control.type='button';control.className='btn btn-sm btn-secondary';
            control.textContent=text;control.disabled=disabled || !ownTurn || Boolean(light.burned&&!['stow','pda_stow'].includes(operation))
                || Boolean(light.blocked_reason && ['toggle','pda_toggle'].includes(operation) && !(light.kind==='pda'?light.pda_state?.on:light.binding?.on));
            control.dataset.lightOperation=operation;
            control.onclick=()=>operate({operation,path:light.path,source_id:light.id??null,...extra});row.append(control);
            return control;
        };
        const optionRow=(select,control)=>{
            const options=document.createElement('div');options.className='portable-light-options';
            options.append(select,control);row.append(options);
        };
        if(light.kind==='pda'){
            button(`${light.pda_state?.on?'Выключить':'Включить'}${active?' · 1 ОД':''}`,
                'pda_toggle',{},!light.pda_state?.on && (light.pda_state?.charge_percent??100)<=0);
            if(!light.burned&&result.batteries.length){
                const choices=document.createElement('select');choices.className='form-control';
                choices.setAttribute('aria-label','Батарейки для КПК');
                result.batteries.filter(battery=>battery.standard_fit ?? battery.name==='Батарейки').forEach(battery=>{
                    const option=document.createElement('option');option.value=JSON.stringify(battery.path);
                    option.textContent=battery.charge == null ? battery.name : `${battery.name} · ${battery.charge.toFixed(1)}%`;
                    choices.append(option);
                });
                if(choices.options.length){
                    const replace=button(`Заменить батарейку${active?' · 3 ОД + достать':''}`,'pda_battery');
                    replace.onclick=()=>operate({operation:'pda_battery',path:light.path,
                        source_id:light.id??null,battery_path:JSON.parse(choices.value)});
                    optionRow(choices,replace);
                }
            }
            root.append(row);continue;
        }
        if (light.binding) {
            if(light.kind!=='glow')button(`${light.binding.on?'Выключить':'Включить'}${active?' · 1 ОД':''}`,'toggle',{},!light.binding.on && (light.uses_left<=0||light.mount_available===false));
            if (!['weapon','nvg'].includes(light.binding.mount)) button(`Убрать${active?' · 1 ОД':''}`,'stow');
            if(light.binding.mount==='hand'&&light.belt_mount)button(`На пояс${active?' · 3 ОД':''}`,'prepare',{mount:'belt'});
        } else {
            if(light.kind==='glow'){
                if(!light.activated)button(`Активировать${active?' · 1 ОД + достать':''}`,'ignite');
                else if(light.remaining_seconds>0)button('Взять горящий источник','prepare',{mount:'hand'});
                if(light.belt_mount&&light.activated&&light.remaining_seconds>0)button(`На пояс${active?' · 3 ОД':''}`,'prepare',{mount:'belt'});
                root.append(row);continue;
            }
            const mount=light.kind==='head'||light.visor_mount?'head':light.kind==='module'?'weapon':'hand';
            button(mount==='head'?'Надеть':mount==='weapon'?'Подготовить модуль':'Взять в руку','prepare',
                {mount,...(mount==='weapon'?{weapon_index:light.path[1]}:{})});
            if(light.belt_mount)button(`На пояс${active?' · 3 ОД':''}`,'prepare',{mount:'belt'});
            if (!light.visor_mount && !['head','module'].includes(light.kind) && (data.weapons || []).length) {
                const weapons=document.createElement('select');weapons.className='form-control';weapons.setAttribute('aria-label','Оружие для фонаря');
                (data.weapons || []).forEach((weapon,index)=>{
                    const option=document.createElement('option');option.value=index;option.textContent=weapon.name || weapon.model || `Оружие ${index+1}`;weapons.append(option);
                });
                rememberEquipmentChoice(weapons,`${characterId}:light-weapon:${row.dataset.lightId}`);
                const taped=document.createElement('button');taped.type='button';taped.className='btn btn-sm btn-secondary';
                taped.textContent=`Закрепить на оружии${active?' · 1 ОД + достать':''}`;taped.disabled=!ownTurn;taped.onclick=()=>operate({operation:'prepare',path:light.path,source_id:light.id??null,mount:'taped',weapon_index:Number(weapons.value)});optionRow(weapons,taped);
            }
        }
        if (!light.burned && result.batteries.length && light.kind!=='glow') {
            const choices=document.createElement('select');choices.className='form-control';choices.setAttribute('aria-label','Батарейка для фонаря');
            result.batteries.forEach(battery=>{const option=document.createElement('option');option.value=JSON.stringify(battery.path);
                option.textContent=battery.charge == null ? battery.name : `${battery.name} · ${battery.charge.toFixed(1)}%`;choices.append(option)});
            rememberEquipmentChoice(choices,`${characterId}:light-battery:${row.dataset.lightId}`);
            const replace=document.createElement('button');replace.type='button';replace.className='btn btn-sm btn-secondary';replace.textContent=`Заменить батарейку${active?' · 1 ОД + достать':''}`;replace.disabled=!ownTurn;
            replace.onclick=()=>operate({operation:'battery',path:light.path,source_id:light.id??null,battery_path:JSON.parse(choices.value)});optionRow(choices,replace);
        }
        if (!canEdit) row.querySelectorAll('button,select').forEach(control=>{control.disabled=true});
        root.append(row);
    }
    container.append(root);
}

// Динамический свет не пересчитывает статическое поле сцены при каждом шаге.
export function createPortableLightRenderer(THREE,scene) {
    const lights=new Map();
    const materials=new Map(),spotPool=[];
    let keepSpotSlots = false, textureBudget = 0, seasonalTerrain = false;
    let beamShapes=null,beamUp=null,lensGeometry=null,lensMaterial=null;
    const previousBeforeRender=scene.onBeforeRender;
    const beforeRender=function(renderer,renderScene,camera,...rest){
        previousBeforeRender?.call(this,renderer,renderScene,camera,...rest);
        if(!beamShapes)return;
        beamUp.set(0,1,0).transformDirection(camera.matrixWorldInverse);
    };
    const verticalHalfAngle=Math.PI/3;
    // Узкая ширина по горизонтали сохраняется, по вертикали луч достигает
    // ближней земли. Обычный круглый конус пересекал её лишь через 13 метров.
    function attachBeamMaterial(material) {
        if(!material || !(material.isMeshStandardMaterial||material.isMeshPhongMaterial||material.isMeshLambertMaterial))return;
        const current=materials.get(material);
        if(current?.hook===material.onBeforeCompile)return;
        const previous=material.onBeforeCompile,previousKey=material.customProgramCacheKey;
        const previousBatch=material.userData.portableLightBeam;
        const hook=(shader,renderer)=>{
            previous?.call(material,shader,renderer);
            shader.uniforms.portableBeamShapes={value:beamShapes};
            shader.uniforms.portableBeamUp={value:beamUp};
            for(const stage of ['vertexShader','fragmentShader']){
                if(!shader[stage]?.includes('#include <lights_pars_begin>'))continue;
                const chunk=THREE.ShaderChunk.lights_pars_begin.replace(
                    'directLight.color *= spotEffect *',
                    'directLight.color *= portableBeamMask(spotLight.decay, directLight.direction, spotLight.direction, lightDistance) * spotEffect *')
                    .replace('directLight.visible = true;', 'directLight.visible = ( directLight.color != vec3( 0.0 ) );');
                shader[stage]=shader[stage].replace('#include <lights_pars_begin>',`
                    uniform vec4 portableBeamShapes[4];
                    uniform vec3 portableBeamUp;
                    float portableBeamMask(float marker, vec3 ray, vec3 direction, float lightDistance) {
                        if (marker >= 0.0) return 1.0;
                        vec3 up = portableBeamUp;
                        int beam = int(-marker - 1.0);
                            vec3 flatRay = ray - up * dot(ray, up);
                            vec3 flatDirection = direction - up * dot(direction, up);
                            float rayLength = length(flatRay), directionLength = length(flatDirection);
                            if (rayLength < 0.00001 || directionLength < 0.00001) return 1.0;
                            vec4 shape = portableBeamShapes[beam];
                            float along = dot(flatRay, flatDirection / directionLength);
                            float horizontal = smoothstep(shape.x, shape.y, along / rayLength);
                            // Расширяется только нижняя часть; верх луча остаётся прежним.
                            if (dot(ray, up) < dot(direction, up)) {
                                horizontal *= smoothstep(shape.x, shape.y, dot(ray, direction));
                            }
                            float forward = max(0.0, along * lightDistance);
                            float farEdge = shape.z;
                            // Последние полметра образуют округлый конец: центр дальше краёв.
                            if (forward > shape.z - 0.5) {
                                float side = lightDistance * sqrt(max(0.0, dot(flatRay, flatRay) - along * along));
                                float sideRatio = min(1.0, side / shape.w);
                                farEdge -= 0.5 * (1.0 - sqrt(1.0 - sideRatio * sideRatio));
                            }
                            float reach = 1.0 - smoothstep(farEdge - 0.15, farEdge, forward);
                            // Не гасим последние метры линейным затуханием Three.js.
                            return horizontal * reach * mix(1.0, 0.2, min(1.0, forward / shape.z));
                    }
                    ${chunk}`);
            }
            // За узкой границей луча не нужны ни выборка мягкой тени, ни BRDF.
            const begin=THREE.ShaderChunk.lights_fragment_begin;
            const call=begin.includes('getSpotLightInfo(')
                ? 'getSpotLightInfo( spotLight, geometryPosition, directLight );'
                : 'getSpotDirectLightIrradiance( spotLight, geometry, directLight );';
            const start=begin.indexOf(call),end=begin.indexOf('\n\t}',start);
            if(start>=0&&end>start)shader.fragmentShader=shader.fragmentShader.replace('#include <lights_fragment_begin>',
                begin.slice(0,start)+'if (any(notEqual(spotLight.color, vec3(0.0)))) {\n'
                +call+'\n if (directLight.visible) {'+begin.slice(start+call.length,end)+'\n }}'+begin.slice(end));
        };
        material.onBeforeCompile=hook;
        material.customProgramCacheKey=()=>`${previousKey?.call(material)||''}:portable-beam-v4`;
        // Профиль и uniform-объекты общие для сцены: объединение её статических
        // деталей безопасно и при выключенных, и при включённых фонарях.
        material.userData.portableLightBeam={batchKey:`portable-beam-v4:${scene.id}`,
            compile:hook,previousCompile:previous};
        materials.set(material,{hook,previous,previousKey,previousBatch});material.needsUpdate=true;
    }
    function beamShadow(light,horizontalHalfAngle) {
        // В Three r128 камера тени по умолчанию квадратная. Узкая горизонтальная
        // проекция оставляет прежнее разрешение поперёк луча без новых источников.
        const baseUpdate=Object.getPrototypeOf(Object.getPrototypeOf(light.shadow)).updateMatrices;
        light.shadow.updateMatrices=function(source){
            this.camera.fov=2*source.angle*180/Math.PI;
            this.camera.aspect=Math.tan(horizontalHalfAngle)/Math.tan(source.angle);
            this.camera.far=source.distance||this.camera.far;
            this.camera.updateProjectionMatrix();baseUpdate.call(this,source);
        };
    }
    function attach(root) {
        if(!beamShapes||!root)return;
        root.traverse(child=>{if(child.isMesh)for(const material of [].concat(child.material||[]))attachBeamMaterial(material);});
    }
    function detachMaterial(material){
        const state=materials.get(material);
        if(!state||material.onBeforeCompile!==state.hook)return false;
        material.onBeforeCompile=state.previous;material.customProgramCacheKey=state.previousKey;
        if(state.previousBatch)material.userData.portableLightBeam=state.previousBatch;
        else delete material.userData.portableLightBeam;
        materials.delete(material);material.needsUpdate=true;return true;
    }
    function shadowSettings(light) {
        light.castShadow=true;light.shadow.mapSize.set(512,512);light.shadow.camera.near=.08;
        light.shadow.bias=-.0002;light.shadow.normalBias=.015;light.shadow.autoUpdate=false;
    }
    function idle(entry) {
        // Сохраняем число прожекторов и теней в ключе программы. Нулевая
        // яркость отключает вклад в освещение, карта тени не обновляется.
        entry.active=false;entry.signature=null;entry.light.intensity=0;entry.light.visible=keepSpotSlots;
        entry.light.position.set(0,-10000,0);entry.light.target.position.set(0,-10000,1);
        entry.light.target.updateMatrixWorld();entry.light.shadow.needsUpdate=false;entry.lens.visible=false;
    }
    function refreshSpotSlots() {
        const hasRadialLight = [...lights.values()].some(({source}) => source.radial);
        const keep = textureBudget >= 16 && (!seasonalTerrain || textureBudget >= 32) && !hasRadialLight;
        if (keep === keepSpotSlots) return;
        keepSpotSlots = keep;
        for (const {light, active} of spotPool) light.visible = keepSpotSlots || active;
    }
    function prepare() {
        if(spotPool.length)return;
        beamShapes=Array.from({length:4},()=>new THREE.Vector4());beamUp=new THREE.Vector3(0,1,0);
        scene.onBeforeRender=beforeRender;
        lensGeometry=new THREE.SphereGeometry(.035,8,6);
        lensMaterial=new THREE.MeshBasicMaterial({color:'#fff4d8',transparent:true,opacity:.7,depthWrite:false,toneMapped:false});
        for(let index=0;index<4;index++){
            // В r128 decay <= 0 отключает штатное затухание. Отрицательный
            // номер связывает собственный луч с профилем без поиска в каждом пикселе.
            const light=new THREE.SpotLight('#fff4d8',0,20,verticalHalfAngle,.4,-index-1);
            shadowSettings(light);beamShadow(light,Math.atan(2.5/20));
            const lens=new THREE.Mesh(lensGeometry,lensMaterial);lens.userData.portableEmitter=true;
            scene.add(light,light.target,lens);
            const entry={light,lens,source:null,active:false};idle(entry);spotPool.push(entry);
        }
        attach(scene);
    }
    function dispose(entry) {
        scene.remove(entry.light);
        if(entry.lens)scene.remove(entry.lens);
        if(entry.light.target)scene.remove(entry.light.target);
        entry.light.shadow.map?.dispose();entry.light.shadow.mapPass?.dispose();
    }
    return {
        attach,attachMaterial:attachBeamMaterial,detachMaterial,prepare,
        useSeasonalTerrain() {
            seasonalTerrain = true;
            refreshSpotSlots();
        },
        warm(renderer,camera){
            prepare();attach(scene);
            // Снег, лёд и точечные источники используют дополнительные текстуры.
            // При нехватке слотов оставляем в шейдере только активные фонари.
            textureBudget = typeof renderer.compileAsync === 'function' ? renderer.capabilities?.maxTextures ?? 0 : 0;
            refreshSpotSlots();
            const compile = renderer.compileAsync || renderer.compile;
            if (!compile) return Promise.resolve();
            const normal = compile.call(renderer, scene, camera);
            if (keepSpotSlots || spotPool.some(entry => entry.active)) return Promise.resolve(normal);
            const slot = spotPool[0];
            slot.light.visible = true;
            try {
                return Promise.all([normal, compile.call(renderer, scene, camera)]);
            } finally {
                slot.light.visible = false;
            }
        },
        update(characters,models,groundSources=[]) {
            const wanted=[];
            for (const character of characters || []) {
                const modelEntry=models.get(character.character_id),model=modelEntry?.model;
                if (!model?.visible) continue;
                for (const source of character.portable_lights || []) wanted.push({source,character,model,modelEntry});
            }
            for(const source of groundSources||[])wanted.push({source,character:null,model:null,modelEntry:null});
            const selected=new Set(wanted.slice(0,4).map(({source})=>source.id));
            for (const [key,entry] of lights) if (!selected.has(key)) {if(entry.light.isSpotLight)idle(entry);else dispose(entry);lights.delete(key)}
            for (const {source,character,model,modelEntry} of wanted.slice(0,4)) {
                let entry=lights.get(source.id);
                if (!entry) {
                    if(source.radial){
                        const light=source.ignition_aura
                            ? new THREE.PointLight('#ffab64',5,source.range,1)
                            : new THREE.PointLight('#dcf0aa',12,source.range||5,1);
                        shadowSettings(light);
                        scene.add(light);entry={light,source};
                    }else{
                        prepare();entry=spotPool.find(slot=>!slot.active);
                        entry.active=true;entry.light.visible=true;entry.light.intensity=11.5;entry.lens.visible=true;
                        entry.light.distance=source.range||20;
                        const angle=Math.atan((source.end_width||5)/2/(source.range||20));
                        beamShapes[-entry.light.decay-1].set(Math.cos(angle),Math.cos(angle*.6),source.range||20,(source.end_width||5)/2);
                        beamShadow(entry.light,angle);
                    }
                    entry.source=source;lights.set(source.id,entry);
                }
                if(source.radial){
                    const height=source.mount==='belt'?({standing:1,sitting:.65,prone:.22}[character?.posture]??1):1.1;
                    const x=model?model.position.x:source.x,y=model?model.position.y+height:source.y,z=model?model.position.z:source.z;
                    const signature=[x,y,z].join(':');
                    if(entry.signature!==signature){entry.signature=signature;entry.light.position.set(x,y,z);entry.light.shadow.needsUpdate=true;}
                    continue;
                }
                const fx=Number(modelEntry.facingX??character.facing_x)||0,fz=Number(modelEntry.facingY??character.facing_y)||0,length=Math.hypot(fx,fz)||1;
                const height=source.mount_height??({head:{standing:1.65,sitting:1.15,prone:.32},belt:{standing:1,sitting:.65,prone:.22}}[source.mount]?.[character.posture]
                    ??{standing:1.2,sitting:.8,prone:.28}[character.posture]??1.2);
                const y=model.position.y+height;
                const signature=[model.position.x,y,model.position.z,fx,fz].join(':');
                if (entry.signature===signature) continue;
                entry.signature=signature;
                entry.light.position.set(model.position.x+.35*fx/length,y,model.position.z+.35*fz/length);
                entry.light.target.position.set(model.position.x+((source.range||20)+.35)*fx/length,model.position.y,model.position.z+((source.range||20)+.35)*fz/length);
                entry.light.target.updateMatrixWorld();entry.light.shadow.needsUpdate=true;
                entry.lens.position.copy(entry.light.position);
            }
            refreshSpotSlots();
        },
        dirty() { for (const {light} of lights.values()) light.shadow.needsUpdate=true; },
        dispose() {
            for (const entry of lights.values())if(!entry.light.isSpotLight)dispose(entry);lights.clear();
            for(const entry of spotPool)dispose(entry);spotPool.length=0;
            for(const material of materials.keys())detachMaterial(material);
            materials.clear();lensGeometry?.dispose();lensMaterial?.dispose();
            if(scene.onBeforeRender===beforeRender)scene.onBeforeRender=previousBeforeRender;
        },
        get count(){return lights.size},
    };
}
