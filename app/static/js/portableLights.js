import { Server } from './api.js';
import { rememberEquipmentChoice } from './equipmentChoices.js';

const glowColors = [
    ['#dcf0aa', 'Зелёный'], ['#f9666b', 'Красный'], ['#68beff', 'Голубой'], ['#ffd265', 'Жёлтый'],
];

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
                if(!light.activated){
                    const color = document.createElement('select');
                    color.className = 'form-control';
                    color.setAttribute('aria-label', `Цвет свечения: ${light.name}`);
                    for (const [value, label] of glowColors) color.add(new Option(label, value));
                    rememberEquipmentChoice(color, `${characterId}:glow-color:${row.dataset.lightId}`);
                    const ignite = button(`Активировать${active?' · 1 ОД + достать':''}`, 'ignite');
                    ignite.onclick = () => operate({ operation:'ignite', path:light.path, source_id:light.id ?? null, color:color.value });
                    optionRow(color, ignite);
                }
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
    const extraCapacity=24;
    let extraSources=[],extraUniforms=null,extraVector=null,lastSelection=null;
    let keepSpotSlots = false, textureBudget = 0, seasonalTerrain = false;
    let beamShapes=null,beamUp=null,beamForwards=null;
    let renderContext = null, warmingLayout = null, materialVersion = 0;
    const warmedLayouts = new Set();
    const isCarriedGlow = ({source, model}) => Boolean(model && source.radial && !source.ignition_aura);
    const previousBeforeRender=scene.onBeforeRender;
    const beforeRender=function(renderer,renderScene,camera,...rest){
        previousBeforeRender?.call(this,renderer,renderScene,camera,...rest);
        if(!beamShapes)return;
        beamUp.set(0,1,0).transformDirection(camera.matrixWorldInverse);
        for (let i=0;i<spotPool.length;i++) {
            const {light}=spotPool[i];
            beamForwards[i].subVectors(light.position,light.target.position);
            beamForwards[i].y=0;
            beamForwards[i].transformDirection(camera.matrixWorldInverse);
        }
        for (let i=0;i<extraSources.length;i++) {
            const entry=extraSources[i],{source}=entry;
            const {x,y,z,targetX,targetY,targetZ}=sourcePose(entry);
            extraVector.set(x,y,z).applyMatrix4(camera.matrixWorldInverse);
            extraUniforms.portableExtraPosition.value[i].set(extraVector.x,extraVector.y,extraVector.z,source.range||20);
            const mode = isCarriedGlow(entry) ? 2 : Number(!source.radial);
            if (mode) extraVector.set(x-targetX,y-targetY,z-targetZ).transformDirection(camera.matrixWorldInverse);
            extraUniforms.portableExtraDirection.value[i].set(extraVector.x,extraVector.y,extraVector.z,mode);
        }
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
            shader.uniforms.portableBeamForwards={value:beamForwards};
            Object.assign(shader.uniforms,extraUniforms);
            for(const stage of ['vertexShader','fragmentShader']){
                if(!shader[stage]?.includes('#include <lights_pars_begin>'))continue;
                const chunk=THREE.ShaderChunk.lights_pars_begin.replace(
                    'light.color = spotLight.color * spotAttenuation;',
                    'light.color = spotLight.color * spotAttenuation * portableBeamMask(spotLight.decay, light.direction, spotLight.direction, lightDistance);');
                shader[stage]=shader[stage].replace('#include <lights_pars_begin>',`
                    uniform vec4 portableBeamShapes[4];
                    uniform vec3 portableBeamUp;
                    uniform vec3 portableBeamForwards[4];
                    float portableGlowSector(vec2 edge, vec3 ray, vec3 direction) {
                        vec3 horizontal = ray - portableBeamUp * dot(ray, portableBeamUp);
                        float lengthSquared = dot(horizontal, horizontal);
                        float angle = lengthSquared < 0.0000001 ? 1.0
                            : dot(horizontal, direction) * inversesqrt(lengthSquared);
                        return smoothstep(edge.x, edge.y, angle);
                    }
                    float portableBeamStrength(vec4 shape, vec3 ray, vec3 direction, vec3 beamForward, float lightDistance) {
                        vec3 up = portableBeamUp;
                            float vertical = dot(ray, up);
                            float flatLengthSquared = max(0.0, dot(ray, ray) - vertical * vertical);
                            if (flatLengthSquared < 0.0000000001) return 1.0;
                            float along = dot(ray, beamForward);
                            float horizontal = smoothstep(shape.x, shape.y, along * inversesqrt(flatLengthSquared));
                            // Расширяется только нижняя часть; верх луча остаётся прежним.
                            if (vertical < dot(direction, up)) {
                                horizontal *= smoothstep(shape.x, shape.y, dot(ray, direction));
                            }
                            float forward = max(0.0, along * lightDistance);
                            float farEdge = shape.z;
                            // Последние полметра образуют округлый конец: центр дальше краёв.
                            if (forward > shape.z - 0.5) {
                                float side = lightDistance * sqrt(max(0.0, flatLengthSquared - along * along));
                                float sideRatio = min(1.0, side / shape.w);
                                farEdge -= 0.5 * (1.0 - sqrt(1.0 - sideRatio * sideRatio));
                            }
                            float reach = 1.0 - smoothstep(farEdge - 0.15, farEdge, forward);
                            // Не гасим последние метры линейным затуханием Three.js.
                            return horizontal * reach * mix(1.0, 0.2, min(1.0, forward / shape.z));
                    }
                    float portableBeamMask(float marker, vec3 ray, vec3 direction, float lightDistance) {
                        if (marker >= 0.0) return 1.0;
                        int beam = int(-marker - 1.0);
                        vec4 shape = portableBeamShapes[beam];
                        if (shape.w < 0.0) {
                            return portableGlowSector(shape.xy, ray, portableBeamForwards[beam])
                                * max(0.0, 1.0 - lightDistance / shape.z);
                        }
                        return portableBeamStrength(shape, ray, direction, portableBeamForwards[beam], lightDistance);
                    }
                    ${chunk}`);
            }
            // За узкой границей луча не нужны ни выборка мягкой тени, ни BRDF.
            const begin=THREE.ShaderChunk.lights_fragment_begin;
            const call=begin.includes('getSpotLightInfo(')
                ? 'getSpotLightInfo( spotLight, geometryPosition, directLight );'
                : 'getSpotDirectLightIrradiance( spotLight, geometry, directLight );';
            const start=begin.indexOf(call),end=begin.indexOf('\n\t}',start);
            if(start>=0&&end>start){
                let directLights=begin.slice(0,start)+'if (any(notEqual(spotLight.color, vec3(0.0)))) {\n'
                    +call+'\n if (directLight.visible) {'+begin.slice(start+call.length,end)+'\n }}'+begin.slice(end);
                if(shader.fragmentShader.includes('#include <shadowmap_pars_fragment>')
                    && !shader.fragmentShader.includes('float portableSpotShadow(')){
                    // Четыре выборки глубины сглаживают тень каждого прожектора. Полное ядро PCFSoft
                    // для всех прожекторов разворачивается ANGLE в большой фрагментный шейдер.
                    shader.fragmentShader=shader.fragmentShader.replace('#include <shadowmap_pars_fragment>', `
                        #include <shadowmap_pars_fragment>
                        #if defined( USE_SHADOWMAP ) && NUM_SPOT_LIGHT_SHADOWS > 0
                        float portableSpotShadow(sampler2D depthMap, vec2 size, float bias, vec4 projected) {
                            vec3 coord = projected.xyz / projected.w;
                            float depth = coord.z + bias;
                            if (any(lessThan(coord.xy, vec2(0.0))) || any(greaterThan(coord.xy, vec2(1.0))) || depth > 1.0) return 1.0;
                            vec2 pixel = coord.xy * size - 0.5;
                            vec2 texel = 1.0 / size;
                            vec2 uv = (floor(pixel) + 0.5) * texel;
                            vec2 weight = fract(pixel);
                            float bottom = mix(texture2DCompare(depthMap, uv, depth),
                                texture2DCompare(depthMap, uv + vec2(texel.x, 0.0), depth), weight.x);
                            float top = mix(texture2DCompare(depthMap, uv + vec2(0.0, texel.y), depth),
                                texture2DCompare(depthMap, uv + texel, depth), weight.x);
                            return mix(bottom, top, weight.y);
                        }
                        #endif`);
                    directLights=directLights.replace(
                        /getShadow\( spotShadowMap\[ i \], (\w+)\.shadowMapSize, \1\.shadowBias, \1\.shadowRadius, (vSpot\w+Coord\[ i \]) \)/g,
                        'portableSpotShadow( spotShadowMap[ i ], $1.shadowMapSize, $1.shadowBias, $2 )');
                }
                const evaluateDirect=begin.match(/RE_Direct\( directLight,[^;\n]+;/)?.[0];
                if(evaluateDirect && shader.fragmentShader.includes('#include <lights_fragment_begin>')){
                    const modernLights=begin.includes('geometryPosition');
                    const geometry=modernLights?'geometryPosition':'geometry.position';
                    const distanceAttenuation=modernLights?'getDistanceAttenuation':'punctualLightIntensityToIrradianceFactor';
                    shader.fragmentShader=shader.fragmentShader.replace('void main() {',`
                        uniform int portableExtraCount;
                        uniform vec4 portableExtraPosition[${extraCapacity}];
                        uniform vec4 portableExtraDirection[${extraCapacity}];
                        uniform vec4 portableExtraColor[${extraCapacity}];
                        uniform vec4 portableExtraShape[${extraCapacity}];
                        void main() {`);
                    directLights+=`
                        #ifdef RE_Direct
                        #if __VERSION__ >= 300
                        for (int extraIndex=0; extraIndex<portableExtraCount; extraIndex++) {
                        #else
                        for (int extraIndex=0; extraIndex<${extraCapacity}; extraIndex++) {
                            if (extraIndex>=portableExtraCount) break;
                        #endif
                            vec4 emitter = portableExtraPosition[extraIndex];
                            vec3 delta = emitter.xyz - ${geometry};
                            float d = length(delta);
                            if (d < 0.001) continue;
                            vec3 ray = delta / d;
                            vec4 orientation = portableExtraDirection[extraIndex];
                            float attenuation;
                            if (orientation.w > 1.5) {
                                attenuation = portableGlowSector(portableExtraShape[extraIndex].xy, ray, orientation.xyz)
                                    * ${distanceAttenuation}(d, emitter.w, 1.0);
                            } else if (orientation.w > 0.5) {
                                if (d > emitter.w + 1.0) continue;
                                vec3 forward = normalize(orientation.xyz - portableBeamUp * dot(orientation.xyz, portableBeamUp));
                                attenuation = smoothstep(0.5, 0.809, dot(ray, orientation.xyz))
                                    * portableBeamStrength(portableExtraShape[extraIndex], ray, orientation.xyz, forward, d);
                            } else {
                                attenuation = ${distanceAttenuation}(d, emitter.w, 1.0);
                            }
                            if (attenuation < 0.0001) continue;
                            IncidentLight extraLight;
                            extraLight.direction = ray;
                            extraLight.color = portableExtraColor[extraIndex].rgb * attenuation;
                            #ifdef LEGACY_LIGHTS
                                extraLight.color *= PI;
                            #endif
                            extraLight.visible = true;
                            ${evaluateDirect.replace('directLight','extraLight')}
                        }
                        #endif`;
                }
                shader.fragmentShader=shader.fragmentShader.replace('#include <lights_fragment_begin>',directLights);
            }
        };
        material.onBeforeCompile=hook;
        material.customProgramCacheKey=()=>`${previousKey?.call(material)||''}:portable-beam-v9`;
        // Профиль и uniform-объекты общие для сцены: объединение её статических
        // деталей безопасно и при выключенных, и при включённых фонарях.
        material.userData.portableLightBeam={batchKey:`portable-beam-v9:${scene.id}`,
            compile:hook,previousCompile:previous};
        materials.set(material,{hook,previous,previousKey,previousBatch});material.needsUpdate=true;
        materialVersion += 1;
    }
    function beamShadow(light,horizontalHalfAngle,verticalAngle=light.angle) {
        // Камера тени по умолчанию квадратная. Узкая горизонтальная
        // проекция оставляет прежнее разрешение поперёк луча без новых источников.
        const baseUpdate=Object.getPrototypeOf(Object.getPrototypeOf(light.shadow)).updateMatrices;
        light.shadow.updateMatrices=function(source){
            this.camera.fov=2*verticalAngle*180/Math.PI;
            this.camera.aspect=Math.tan(horizontalHalfAngle)/Math.tan(verticalAngle);
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
        entry.active=false;entry.light.intensity=0;entry.light.visible=keepSpotSlots;
        entry.light.position.set(0,-10000,0);entry.light.target.position.set(0,-10000,1);
        entry.light.target.updateMatrixWorld();entry.light.shadow.needsUpdate=false;
        entry.light.shadow.portableOwner = null;
    }
    function refreshSpotSlots() {
        const hasRadialLight = [...lights.values()].some(({light}) => light.isPointLight);
        const keep = keepSpots(hasRadialLight);
        if (keep === keepSpotSlots) return;
        keepSpotSlots = keep;
        for (const {light, active} of spotPool) light.visible = keepSpotSlots || active;
    }
    function keepSpots(hasRadialLight) {
        // Подготавливаем один вариант для всех четырёх мест при входе.
        // Иначе первые включения последовательно компилируют четыре набора материалов.
        return textureBudget >= 16 && (!seasonalTerrain || textureBudget >= 32) && !hasRadialLight;
    }
    function prepare() {
        if(spotPool.length)return;
        beamShapes=Array.from({length:4},()=>new THREE.Vector4());beamUp=new THREE.Vector3(0,1,0);
        beamForwards=Array.from({length:4},()=>new THREE.Vector3(0,0,1));
        extraVector=new THREE.Vector3();
        extraUniforms={portableExtraCount:{value:0}};
        for(const name of ['Position','Direction','Color','Shape']){
            extraUniforms['portableExtra'+name]={value:Array.from({length:extraCapacity},()=>new THREE.Vector4())};
        }
        scene.onBeforeRender=beforeRender;
        for(let index=0;index<4;index++){
            // В режиме прежнего освещения decay <= 0 отключает штатное затухание. Отрицательный
            // номер связывает собственный луч с профилем без поиска в каждом пикселе.
            const light=new THREE.SpotLight('#fff4d8',0,20,verticalHalfAngle,.4,-index-1);
            shadowSettings(light);beamShadow(light,Math.atan(2.5/20));
            scene.add(light,light.target);
            const entry={light,source:null,active:false};idle(entry);spotPool.push(entry);
        }
        attach(scene);
    }
    function dispose(entry) {
        scene.remove(entry.light);
        if(entry.light.target)scene.remove(entry.light.target);
        entry.light.shadow.map?.dispose();entry.light.shadow.mapPass?.dispose();
    }
    function sourcePose({source,character,model,modelEntry}) {
        const posture=modelEntry?.posture ?? character?.posture ?? 'standing';
        if (source.radial && !model) return source;
        const fx=Number(modelEntry?.facingX??character?.facing_x)||0;
        const fz=Number(modelEntry?.facingY??character?.facing_y)||(fx?0:1);
        const length=Math.hypot(fx,fz);
        const yaw = modelEntry?.positionAnimation?.followPathFacing ? model.rotation.y : null;
        const dx = yaw === null ? fx / length : Math.sin(yaw);
        const dz = yaw === null ? fz / length : Math.cos(yaw);
        if (source.radial) {
            const belt = source.mount === 'belt';
            const heights = belt ? { standing: 1, sitting: .65, prone: .22 } : { standing: .92, sitting: .66, prone: .24 };
            const height = source.ignition_aura ? source.mount_height ?? (heights[posture] ?? heights.standing)
                : heights[posture] ?? heights.standing;
            const forward = source.ignition_aura ? 0 : posture === 'prone' ? .68 : .35;
            const x = model.position.x + forward * dx;
            const y = model.position.y + height;
            const z = model.position.z + forward * dz;
            return { x, y, z, targetX: x + dx, targetY: y, targetZ: z + dz };
        }
        const height=({head:{standing:1.65,sitting:1.15,prone:.32},belt:{standing:1,sitting:.65,prone:.22}}[source.mount]?.[posture]
            ??{standing:1.2,sitting:.8,prone:.28}[posture]??1.2);
        const offset=posture==='prone'?({head:.80,belt:.42}[source.mount]??.74):.35;
        const x=model.position.x+offset*dx,z=model.position.z+offset*dz;
        const range=source.range||20;
        return {x,y:model.position.y+height,z,targetX:x+range*dx,targetY:model.position.y,targetZ:z+range*dz};
    }
    function updateLightPosition(entry) {
        const {light}=entry;
        if (light.isSpotLight) {
            const owner = isCarriedGlow(entry) ? entry.model : null;
            if (light.shadow.portableOwner !== owner) {
                light.shadow.portableOwner = owner;
                light.shadow.needsUpdate = true;
            }
        }
        const {x,y,z,targetX,targetY,targetZ}=sourcePose(entry);
        const position=light.position,target=light.target?.position;
        if(position.x===x&&position.y===y&&position.z===z
            &&(!target||target.x===targetX&&target.y===targetY&&target.z===targetZ))return;
        position.set(x,y,z);
        if(target){target.set(targetX,targetY,targetZ);light.target.updateMatrixWorld();}
        if(light.castShadow)light.shadow.needsUpdate=true;
    }

    function setExtraSources(sources) {
        extraSources = sources.slice(0, extraCapacity);
        if (extraSources.length) prepare();
        if (extraUniforms) extraUniforms.portableExtraCount.value = extraSources.length;
        for (let i = 0; i < extraSources.length; i++) {
            const entry = extraSources[i];
            const { source } = entry;
            const color = new THREE.Color(source.radial ? (source.ignition_aura ? '#ffab64' : source.color || '#dcf0aa') : '#fff4d8');
            color.multiplyScalar(source.radial ? (source.ignition_aura ? 5 : 12) : 11.5);
            extraUniforms.portableExtraColor.value[i].set(color.r, color.g, color.b, 0);
            const shape = extraUniforms.portableExtraShape.value[i];
            if (isCarriedGlow(entry)) {
                const halfAngle = (source.arc_degrees || 150) * Math.PI / 360;
                shape.set(Math.cos(halfAngle), Math.cos(halfAngle - Math.PI / 15), 0, 0);
            } else {
                const range = source.range || 20, halfWidth = (source.end_width || 5) / 2;
                const angle = Math.atan(halfWidth / range);
                shape.set(Math.cos(angle), Math.cos(angle * .6), range, halfWidth);
            }
        }
    }

    function configureSpot(entry, definition) {
        const {light} = entry;
        const {source} = definition;
        const carriedGlow = isCarriedGlow(definition);
        const range = source.range || 20;
        const halfWidth = (source.end_width || 5) / 2;
        const angle = carriedGlow ? (source.arc_degrees || 150) * Math.PI / 360 : Math.atan(halfWidth / range);
        light.angle = carriedGlow ? 88 * Math.PI / 180 : verticalHalfAngle;
        light.penumbra = carriedGlow ? .02 : .4;
        light.distance = range;
        light.color.set(carriedGlow ? source.color || '#dcf0aa' : '#fff4d8');
        light.shadow.needsUpdate = true;
        beamShapes[-light.decay - 1].set(Math.cos(angle),
            Math.cos(carriedGlow ? angle - Math.PI / 15 : angle * .6), range, carriedGlow ? -1 : halfWidth);
        beamShadow(light, angle, carriedGlow ? 80 * Math.PI / 180 : verticalHalfAngle);
    }

    function warmLightLayout(layout, key, onReady) {
        if (warmingLayout || !renderContext?.ready) return;
        const context = renderContext;
        warmingLayout = key;
        // Three.js выбирает вариант материала по количеству источников и карт теней.
        // Пока драйвер компилирует новый вариант, основной кадр сохраняет прежнюю конфигурацию.
        const target = new THREE.Scene();
        target.fog = scene.fog;
        target.environment = scene.environment;
        const oldLights = new Set([...spotPool, ...lights.values()].map(entry => entry.light));
        scene.traverseVisible(light => {
            if (light.isLight && !oldLights.has(light)) target.add(light.clone());
        });
        for (let i = 0; i < layout.spots; i++) {
            const light = new THREE.SpotLight();
            light.castShadow = true;
            target.add(light);
        }
        for (let i = 0; i < layout.points; i++) {
            const light = new THREE.PointLight();
            light.castShadow = i < layout.pointShadows;
            target.add(light);
        }
        const materialsInScene = new THREE.Group();
        materialsInScene.traverse = visit => scene.traverse(object => {
            if (object.material) visit(object);
        });
        Promise.resolve().then(() => {
            if (renderContext === context) return context.renderer.compileAsync(materialsInScene, context.camera, target);
        }).catch(error => {
            console.warn('Не удалось подготовить вариант динамического света', error);
        }).finally(() => {
            if (renderContext !== context) return;
            warmedLayouts.add(key);
            warmingLayout = null;
            onReady();
        });
    }

    function applyTemporaryLights(wanted) {
        const byId = new Map(wanted.map(definition => [definition.source.id, definition]));
        for (const [id, entry] of lights) {
            const definition = byId.get(id);
            if (definition) Object.assign(entry, definition);
            entry.light.intensity = definition ? (entry.source.radial ? (entry.source.ignition_aura ? 5 : 12) : 11.5) : 0;
            if (definition) updateLightPosition(entry);
        }
        setExtraSources(wanted.filter(({ source }) => !lights.has(source.id)));
    }

    function currentLayoutKey() {
        const points = [...lights.values()].filter(({ light }) => light.isPointLight);
        return `${spotPool.filter(({ light }) => light.visible).length}:${points.length}:${points.filter(({ light }) => light.castShadow).length}`;
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
            const firstAsyncWarm = !renderContext && typeof renderer.compileAsync === 'function'
                && renderer.extensions?.has?.('KHR_parallel_shader_compile');
            if (firstAsyncWarm) {
                renderContext = { renderer, camera, ready: false };
                // Начальный кадр использует те же источники, пока карты теней готовятся.
                for (const entry of lights.values()) {
                    if (entry.light.isSpotLight) idle(entry);
                    else dispose(entry);
                }
                lights.clear();
            }
            refreshSpotSlots();
            const applyLastSelection = () => {
                if (lastSelection) this.update(lastSelection.characters, lastSelection.models,
                    lastSelection.groundSources, camera);
            };
            if (firstAsyncWarm) applyLastSelection();
            const compile = renderer.compileAsync || renderer.compile;
            if (!compile) return Promise.resolve();
            const context = renderContext;
            return Promise.resolve(compile.call(renderer, scene, camera)).finally(() => {
                if (!firstAsyncWarm || renderContext !== context) return;
                context.ready = true;
                applyLastSelection();
            });
        },
        update(characters,models,groundSources=[],camera=null) {
            lastSelection={characters,models,groundSources,x:camera?.position.x,z:camera?.position.z};
            const wanted=[];
            for (const character of characters || []) {
                const modelEntry=models.get(character.character_id),model=modelEntry?.model;
                if (!model?.visible) continue;
                for (const source of character.portable_lights || []) wanted.push({source,character,model,modelEntry});
            }
            for(const source of groundSources||[])wanted.push({source,character:null,model:null,modelEntry:null});
            if (camera) {
                const distance = ({source,model}) => (camera.position.x - (model?.position.x ?? source.x)) ** 2
                    + (camera.position.z - (model?.position.z ?? source.z)) ** 2;
                wanted.sort((a,b) => distance(a) - distance(b));
            }
            // Ближайшие направленные источники используют общие места для карт теней.
            const spots=wanted.filter(entry=>!entry.source.radial||isCarriedGlow(entry)).slice(0,4);
            const radial=wanted.filter(entry=>entry.source.radial&&!isCarriedGlow(entry)).slice(0,4);
            const primary=[...spots,...radial];
            const radialShadows=new Set(radial.slice(0,4-spots.length).map(({source})=>source.id));
            const selected=new Set(primary.map(({source})=>source.id));
            const layout = { spots: keepSpots(radial.length > 0) ? 4 : spots.length, points: radial.length, pointShadows: radialShadows.size };
            const currentLayout = currentLayoutKey();
            const requestedLayout = `${layout.spots}:${layout.points}:${layout.pointShadows}`;
            const layoutKey = `${materialVersion}:${requestedLayout}`;
            if (renderContext && currentLayout !== requestedLayout) {
                warmedLayouts.add(`${materialVersion}:${currentLayout}`);
                if (!warmedLayouts.has(layoutKey)) {
                    applyTemporaryLights(wanted);
                    warmLightLayout(layout, layoutKey, () => {
                        if (lastSelection) this.update(lastSelection.characters, lastSelection.models,
                            lastSelection.groundSources, renderContext.camera);
                    });
                    return;
                }
            }
            setExtraSources(wanted.filter(({source})=>!selected.has(source.id)));
            for (const [key,entry] of lights) if (!selected.has(key)) {if(entry.light.isSpotLight)idle(entry);else dispose(entry);lights.delete(key)}
            for (const definition of primary) {
                const {source}=definition;
                let entry=lights.get(source.id);
                if (!entry) {
                    if(source.radial && !isCarriedGlow(definition)){
                        const light=source.ignition_aura
                            ? new THREE.PointLight('#ffab64',5,source.range,1)
                            : new THREE.PointLight(source.color || '#dcf0aa',12,source.range||5,1);
                        shadowSettings(light);
                        scene.add(light);entry={light,source};
                    }else{
                        prepare();entry=spotPool.find(slot=>!slot.active);
                        entry.active=true;entry.light.visible=true;
                        configureSpot(entry, definition);
                    }
                    lights.set(source.id,entry);
                }
                Object.assign(entry,definition);
                entry.light.intensity = source.radial ? (source.ignition_aura ? 5 : 12) : 11.5;
                if(entry.light.isPointLight){
                    const castShadow=radialShadows.has(source.id);
                    if(entry.light.castShadow!==castShadow){
                        entry.light.castShadow=castShadow;
                        entry.light.shadow.needsUpdate=castShadow;
                    }
                }
                updateLightPosition(entry);
            }
            refreshSpotSlots();
        },
        updatePositions(camera) {
            if (camera && lastSelection && lights.size+extraSources.length>4) {
                const dx=camera.position.x-lastSelection.x,dz=camera.position.z-lastSelection.z;
                if (dx*dx+dz*dz>=9) this.update(lastSelection.characters,lastSelection.models,lastSelection.groundSources,camera);
            }
            for (const entry of lights.values()) if (entry.light.intensity > 0) updateLightPosition(entry);
        },
        dirty(from,to=from) {
            for (const {light} of lights.values()) {
                if (!light.castShadow || light.intensity === 0) continue;
                const reach=(light.distance+1)**2;
                if (!from || light.position.distanceToSquared(from)<=reach || light.position.distanceToSquared(to)<=reach) {
                    light.shadow.needsUpdate=true;
                }
            }
        },
        dispose() {
            for (const entry of lights.values())if(!entry.light.isSpotLight)dispose(entry);lights.clear();
            for(const entry of spotPool)dispose(entry);spotPool.length=0;
            extraSources=[];extraUniforms=null;extraVector=null;lastSelection=null;renderContext=null;warmingLayout=null;
            warmedLayouts.clear();
            for(const material of materials.keys())detachMaterial(material);
            materials.clear();
            if(scene.onBeforeRender===beforeRender)scene.onBeforeRender=previousBeforeRender;
        },
        get count(){return this.diagnostics.lights},
        get diagnostics(){
            const sources=[...[...lights.values()].filter(({light})=>light.intensity>0),...extraSources];
            const radial=sources.filter(({source})=>source.radial).length;
            return {lights:sources.length,spots:sources.length-radial,radial,overflow:extraSources.length,
                shadows:[...lights.values()].filter(({light})=>light.castShadow&&light.intensity>0).length};
        },
    };
}
