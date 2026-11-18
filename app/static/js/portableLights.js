import { Server } from './api.js';
import { rememberEquipmentChoice } from './equipmentChoices.js';

const glowColors = [
    ['#dcf0aa', 'Зелёный'], ['#f9666b', 'Красный'], ['#68beff', 'Голубой'], ['#ffd265', 'Жёлтый'],
];

export function activeGlowLabel(state) {
    const seconds = Number(state?.remaining_seconds);
    if (!state?.activated || !Number.isFinite(seconds) || seconds <= 0) return '';
    const color = glowColors.find(([value]) => value === state.color)?.[1] || glowColors[0][1];
    return `Осталось гореть ещё ${Math.ceil(seconds / 60)} мин · цвет: ${color.toLowerCase()}`;
}

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
        if (light.kind==='glow' && !light.activated && light.mount_available!==false) {
            const color=document.createElement('select');
            color.className='form-control';
            color.setAttribute('aria-label', `Цвет свечения: ${light.name}`);
            for (const [value,label] of glowColors) color.add(new Option(label,value));
            rememberEquipmentChoice(color,`${characterId}:glow-color:${row.dataset.lightId}`);
            const cost=light.binding?.mount==='belt'?' · 1 ОД':' · 1 ОД + достать';
            const ignite=button(`Активировать${active?cost:''}`,'ignite');
            ignite.onclick=()=>operate({operation:'ignite',path:light.path,source_id:light.id??null,color:color.value});
            optionRow(color,ignite);
        }
        if (light.binding) {
            if(light.kind!=='glow')button(`${light.binding.on?'Выключить':'Включить'}${active?' · 1 ОД':''}`,'toggle',{},!light.binding.on && (light.uses_left<=0||light.mount_available===false));
            if (!['weapon','nvg'].includes(light.binding.mount)) button(`Убрать${active?' · 1 ОД':''}`,'stow');
            if(light.binding.mount==='hand'&&light.belt_mount)button(`На пояс${active?' · 3 ОД':''}`,'prepare',{mount:'belt'});
        } else {
            if(light.kind==='glow'){
                if(light.remaining_seconds>0)button('Взять горящий источник','prepare',{mount:'hand'});
                if(light.belt_mount&&(!light.activated||light.remaining_seconds>0))button(`На пояс${active?' · 3 ОД':''}`,'prepare',{mount:'belt'});
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
    const emitterGeometry = new WeakMap();
    let emitterPoint, emitterDirection, airGeometry, airMaterial, airDirection, airUp;
    const extraCapacity=24;
    let extraSources=[],extraUniforms=null,extraVector=null,lastSelection=null;
    let keepSpotSlots = false, textureBudget = 0, seasonalTerrain = false;
    let renderContext = null, warmingLayout = null, materialVersion = 0;
    let shadowViewProjection, shadowViewSample, shadowViewFrustum, shadowInfluence;
    const warmedLayouts = new Set();
    // Трубки лежат вплотную к полу или ткани, поэтому спад вблизи мягче, чем у точечной лампы.
    const glowDecay = .4;
    const lightIntensity = source => source.radial ? source.ignition_aura ? 5 : 3.5 : 11.5;
    const shadowShift = .012;
    const beamPenumbra = .72;
    const beamAngle = source => Math.atan((source.end_width || 5) / (2 * (source.range || 20)));
    const beamFalloff = `
        float portableBeamFalloff(float fraction) {
            return (1.0 - smoothstep(0.88, 1.0, fraction)) * mix(1.0, 0.5, clamp(fraction, 0.0, 1.0));
        }`;
    const beamShadowBias = `
        float portableBeamDepthBias(float bias, float distanceAlongBeam) {
            // В перспективной карте один и тот же сдвиг глубины растёт в метрах с расстоянием.
            return bias / max(1.0, distanceAlongBeam * distanceAlongBeam);
        }`;
    const beamSurfaceFalloff = `
        float portableBeamSurfaceFalloff(float fraction) {
            // На дальней части пучка свет падает на пол под малым углом.
            return mix(0.32, 1.05, smoothstep(0.28, 0.75, fraction)) * portableBeamFalloff(fraction);
        }`;
    const terrainBeamLighting = `
        vec3 portableTerrainBeamNormal(vec3 detailed, vec3 surface, inout IncidentLight light) {
            vec3 up = viewMatrix[1].xyz;
            vec3 reference = normalize(mix(surface, up, smoothstep(0.98, 0.999, dot(surface, up))));
            float incidence = dot(reference, light.direction);
            float grazing = (1.0 - smoothstep(0.025, 0.09, incidence)) * smoothstep(0.0, 0.012, incidence);
            light.color *= 1.0 + 2.2 * grazing;
            return normalize(mix(detailed, reference, 0.96 * grazing));
        }`;
    const glowFalloff = `
        float portableGlowFalloff(float distanceToLight, float range) {
            float fade = 1.0 - smoothstep(0.65, 1.0, distanceToLight / range);
            return 0.62 * fade / (1.0 + 0.28 * distanceToLight * distanceToLight);
        }`;
    const previousBeforeRender=scene.onBeforeRender;
    const beforeRender=function(renderer,renderScene,camera,...rest){
        previousBeforeRender?.call(this,renderer,renderScene,camera,...rest);
        for (let i=0;i<extraSources.length;i++) {
            const entry=extraSources[i],{source}=entry;
            const {x,y,z,targetX,targetY,targetZ}=sourcePose(entry);
            extraVector.set(x,y,z).applyMatrix4(camera.matrixWorldInverse);
            extraUniforms.portableExtraPosition.value[i].set(extraVector.x,extraVector.y,extraVector.z,source.range||20);
            const mode = source.radial ? source.ignition_aura ? 0 : -1 : 1;
            if (mode > 0) extraVector.set(x-targetX,y-targetY,z-targetZ).transformDirection(camera.matrixWorldInverse);
            extraUniforms.portableExtraDirection.value[i].set(extraVector.x,extraVector.y,extraVector.z,mode);
        }
    };
    function attachBeamMaterial(material) {
        if(!material || !(material.isMeshStandardMaterial||material.isMeshPhongMaterial||material.isMeshLambertMaterial))return;
        const current=materials.get(material);
        if(current?.hook===material.onBeforeCompile)return;
        const previous=material.onBeforeCompile,previousKey=material.customProgramCacheKey;
        const previousBatch=material.userData.portableLightBeam;
        const hook=(shader,renderer)=>{
            previous?.call(material,shader,renderer);
            Object.assign(shader.uniforms,extraUniforms);
            const terrainLighting = shader.fragmentShader.includes('vTerrainMaterialNormal');
            for(const stage of ['vertexShader','fragmentShader']){
                if(!shader[stage]?.includes('#include <lights_pars_begin>'))continue;
                const chunk=THREE.ShaderChunk.lights_pars_begin.replace(
                    'light.color *= getDistanceAttenuation( lightDistance, spotLight.distance, spotLight.decay );',
                    `light.color *= spotLight.decay < 0.0
                        ? portableBeamSurfaceFalloff(lightDistance * angleCos / spotLight.distance)
                        : getDistanceAttenuation(lightDistance, spotLight.distance, spotLight.decay);`)
                    .replace('light.color *= getDistanceAttenuation( lightDistance, pointLight.distance, pointLight.decay );',
                        `light.color *= pointLight.decay == ${glowDecay} ? portableGlowFalloff(lightDistance, pointLight.distance)
                            : getDistanceAttenuation(lightDistance, pointLight.distance, pointLight.decay);`);
                shader[stage]=shader[stage].replace('#include <lights_pars_begin>',
                    beamFalloff + beamSurfaceFalloff + glowFalloff + chunk
                    + (terrainLighting && stage === 'fragmentShader' ? terrainBeamLighting : ''));
            }
            // За узкой границей луча не нужны ни выборка мягкой тени, ни BRDF.
            const begin=THREE.ShaderChunk.lights_fragment_begin;
            const call=begin.includes('getSpotLightInfo(')
                ? 'getSpotLightInfo( spotLight, geometryPosition, directLight );'
                : 'getSpotDirectLightIrradiance( spotLight, geometry, directLight );';
            const start=begin.indexOf(call),end=begin.indexOf('\n\t}',start);
            if(start>=0&&end>start){
                const evaluateDirect=begin.match(/RE_Direct\( directLight,[^;\n]+;/)?.[0];
                const modernLights=begin.includes('geometryPosition');
                let spotBlock=begin.slice(start+call.length,end);
                let evaluateExtra = evaluateDirect?.replace('directLight', 'extraLight');
                if (evaluateDirect && terrainLighting && modernLights) {
                    // Микрорельеф текстуры не должен полностью гасить луч, идущий вдоль земли.
                    const terrainDirect = evaluateDirect.replace('geometryNormal', 'beamNormal');
                    spotBlock = spotBlock.replace(evaluateDirect, `
                        vec3 beamNormal = spotLight.decay < 0.0
                            ? portableTerrainBeamNormal(geometryNormal, nonPerturbedNormal, directLight) : geometryNormal;
                        ${terrainDirect}`);
                    evaluateExtra = `
                        vec3 beamNormal = orientation.w > 0.5
                            ? portableTerrainBeamNormal(geometryNormal, nonPerturbedNormal, extraLight) : geometryNormal;
                        ${terrainDirect.replace('directLight', 'extraLight')}`;
                }
                let directLights=begin.slice(0,start)+'if (any(notEqual(spotLight.color, vec3(0.0)))) {\n'
                    +call+'\n if (directLight.visible) {'+spotBlock+'\n }}'+begin.slice(end);
                // У светящейся трубки мягкая полутень. Остальные точечные источники не меняются.
                directLights = directLights.replace(
                    /getPointShadow\( pointShadowMap\[ i \],[^\n]*? \)(?= :)/,
                    sample => `mix(${sample}, 1.0, pointLight.decay == ${glowDecay} ? 0.18 : 0.0)`);
                if(shader.fragmentShader.includes('#include <shadowmap_pars_fragment>')
                    && !shader.fragmentShader.includes('float portableSpotShadow(')){
                    // Четыре выборки глубины сглаживают тень каждого прожектора. Полное ядро PCFSoft
                    // для всех прожекторов разворачивается ANGLE в большой фрагментный шейдер.
                    shader.fragmentShader=shader.fragmentShader.replace('#include <shadowmap_pars_fragment>', `
                        #include <shadowmap_pars_fragment>
                        #if defined( USE_SHADOWMAP ) && NUM_SPOT_LIGHT_SHADOWS > 0
                        ${beamShadowBias}
                        float portableSpotShadow(sampler2D depthMap, vec2 size, float bias, vec4 projected, bool portable) {
                            vec3 coord = projected.xyz / projected.w;
                            float depth = coord.z + (portable ? portableBeamDepthBias(bias, projected.w) : bias);
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
                        'portableSpotShadow( spotShadowMap[ i ], $1.shadowMapSize, $1.shadowBias, $2, spotLight.decay < 0.0 )');
                }
                if(evaluateDirect && shader.fragmentShader.includes('#include <lights_fragment_begin>')){
                    const geometry=modernLights?'geometryPosition':'geometry.position';
                    const distanceAttenuation=modernLights?'getDistanceAttenuation':'punctualLightIntensityToIrradianceFactor';
                    shader.fragmentShader=shader.fragmentShader.replace('void main() {',`
                        uniform int portableExtraCount;
                        uniform vec4 portableExtraPosition[${extraCapacity}];
                        uniform vec4 portableExtraDirection[${extraCapacity}];
                        uniform vec4 portableExtraColor[${extraCapacity}];
                        uniform vec2 portableExtraCone[${extraCapacity}];
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
                            if (orientation.w > 0.5) {
                                vec2 cone = portableExtraCone[extraIndex];
                                float angleCos = dot(ray, orientation.xyz);
                                attenuation = smoothstep(cone.x, cone.y, angleCos)
                                    * portableBeamSurfaceFalloff(d * angleCos / emitter.w);
                            } else {
                                attenuation = orientation.w < 0.0 ? portableGlowFalloff(d, emitter.w)
                                    : ${distanceAttenuation}(d, emitter.w, 1.0);
                            }
                            if (attenuation < 0.0001) continue;
                            IncidentLight extraLight;
                            extraLight.direction = ray;
                            extraLight.color = portableExtraColor[extraIndex].rgb * attenuation;
                            #ifdef LEGACY_LIGHTS
                                extraLight.color *= PI;
                            #endif
                            extraLight.visible = true;
                            ${evaluateExtra}
                        }
                        #endif`;
                }
                shader.fragmentShader=shader.fragmentShader.replace('#include <lights_fragment_begin>',directLights);
            }
        };
        material.onBeforeCompile=hook;
        material.customProgramCacheKey=()=>`${previousKey?.call(material)||''}:portable-beam-v17`;
        // Профиль и uniform-объекты общие для сцены: объединение её статических
        // деталей безопасно и при выключенных, и при включённых фонарях.
        material.userData.portableLightBeam={batchKey:`portable-beam-v17:${scene.id}`,
            compile:hook,previousCompile:previous};
        materials.set(material,{hook,previous,previousKey,previousBatch});material.needsUpdate=true;
        materialVersion += 1;
    }
    function attach(root) {
        if(!extraUniforms||!root)return;
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
        entry.active=false;entry.light.intensity=0;entry.light.visible=keepSpotSlots;entry.emitter=null;
        entry.light.shadow.portableOwner = null;
        entry.shadowPose = null;
        if (entry.air) entry.air.visible = false;
        entry.light.position.set(0,-10000,0);entry.light.target.position.set(0,-10000,1);
        entry.light.target.updateMatrixWorld();entry.light.shadow.needsUpdate=false;
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
        extraVector=new THREE.Vector3();
        extraUniforms={portableExtraCount:{value:0},
            portableExtraCone:{value:Array.from({length:extraCapacity},()=>new THREE.Vector2())}};
        for(const name of ['Position','Direction','Color']){
            extraUniforms['portableExtra'+name]={value:Array.from({length:extraCapacity},()=>new THREE.Vector4())};
        }
        scene.onBeforeRender=beforeRender;
        for(let index=0;index<4;index++){
            // Отрицательный decay включает общий профиль затухания видимого луча.
            const light=new THREE.SpotLight('#fff4d8',0,20,beamAngle({}),beamPenumbra,-1);
            shadowSettings(light);
            scene.add(light,light.target);
            const entry={light,source:null,active:false};idle(entry);spotPool.push(entry);
        }
        attach(scene);
    }
    function dispose(entry) {
        scene.remove(entry.light);
        if(entry.light.target)scene.remove(entry.light.target);
        if (entry.air) scene.remove(entry.air);
        entry.light.shadow.map?.dispose();entry.light.shadow.mapPass?.dispose();
    }
    function findEmitter(entry) {
        const key = entry.source.visual_model;
        const rig = entry.modelEntry?.humanAppearanceModel;
        if (!key || !rig) return null;
        const signature = rig.userData.characterAppearanceSignature;
        const cached = entry.emitter;
        if (cached?.rig === rig && cached.key === key && cached.signature === signature) return cached.mesh;
        const part = rig.getObjectByName('belt_gear_device_' + key) || rig.getObjectByName('gear_device_' + key);
        let mesh = null;
        part?.traverse(child => {
            if (child.isMesh && child.visible && child.material.name === 'device_' + key + '_emitter') mesh = child;
        });
        entry.emitter = { rig, key, signature, mesh };
        return mesh;
    }

    function emitterPosition(entry, axis = null) {
        const mesh = entry.source.groundEmitter || findEmitter(entry);
        if (!mesh) return null;
        const { geometry } = mesh;
        let local = emitterGeometry.get(geometry);
        if (!local) {
            if (!geometry.boundingBox) geometry.computeBoundingBox();
            local = geometry.boundingBox.getCenter(new THREE.Vector3());
            emitterGeometry.set(geometry, local);
        }
        emitterPoint ??= new THREE.Vector3();
        emitterPoint.copy(local);
        if (!mesh.isSkinnedMesh) {
            mesh.updateWorldMatrix(true, false);
            if (axis) axis.set(0, 0, 1).transformDirection(mesh.matrixWorld);
            return emitterPoint.applyMatrix4(mesh.matrixWorld);
        }
        // Светящиеся детали привязаны целиком к одной кости кисти, головы или корпуса.
        const index = geometry.getAttribute('skinIndex').getX(0);
        const bone = mesh.skeleton.bones[index];
        bone.updateWorldMatrix(true, false);
        if (axis) axis.set(0, 0, 1).transformDirection(mesh.bindMatrix)
            .transformDirection(mesh.skeleton.boneInverses[index]).transformDirection(bone.matrixWorld);
        return emitterPoint.applyMatrix4(mesh.bindMatrix)
            .applyMatrix4(mesh.skeleton.boneInverses[index]).applyMatrix4(bone.matrixWorld);
    }

    function sourcePose(entry) {
        const {source,character,model,modelEntry} = entry;
        const posture=modelEntry?.posture ?? character?.posture ?? 'standing';
        if (source.radial && !model) return emitterPosition(entry) ?? source;
        const fx=Number(modelEntry?.facingX??character?.facing_x)||0;
        const fz=Number(modelEntry?.facingY??character?.facing_y)||(fx?0:1);
        const length=Math.hypot(fx,fz);
        const yaw = modelEntry?.positionAnimation?.followPathFacing ? model.rotation.y : null;
        const dx = yaw === null ? fx / length : Math.sin(yaw);
        const dz = yaw === null ? fz / length : Math.cos(yaw);
        const handheld = source.visual_model === 'flashlight_hand' && !source.radial;
        const axis = handheld ? (emitterDirection ??= new THREE.Vector3()) : null;
        const emitter = emitterPosition(entry, axis);
        if (source.radial) {
            if (emitter) return emitter;
            const belt = source.mount === 'belt';
            const heights = belt ? { standing: 1, sitting: .65, prone: .22 } : { standing: .92, sitting: .66, prone: .24 };
            const height = source.ignition_aura ? source.mount_height ?? (heights[posture] ?? heights.standing)
                : heights[posture] ?? heights.standing;
            const forward = source.ignition_aura ? 0 : posture === 'prone' ? .68 : .45;
            const side = source.ignition_aura ? 0 : belt ? .18 : .35;
            const x = model.position.x + forward * dx + side * dz;
            const y = model.position.y + height;
            const z = model.position.z + forward * dz - side * dx;
            return { x, y, z, targetX: x + dx, targetY: y, targetZ: z + dz };
        }
        const height=({head:{standing:1.65,sitting:1.15,prone:.32},belt:{standing:1,sitting:.65,prone:.22}}[source.mount]?.[posture]
            ??{standing:1.2,sitting:.8,prone:.28}[posture]??1.2);
        const offset=posture==='prone'?({head:.80,belt:.42}[source.mount]??.74):.35;
        const x=emitter?.x ?? model.position.x+offset*dx;
        const y=emitter?.y ?? model.position.y+height;
        const z=emitter?.z ?? model.position.z+offset*dz;
        const range=source.range||20;
        if (emitter && axis) return { x, y, z,
            targetX: x + range * axis.x, targetY: y + range * axis.y, targetZ: z + range * axis.z };
        const aimsAtGround = source.mount === 'head' || source.mount === 'hand';
        const drop = aimsAtGround ? range * Math.max(.06, y - model.position.y) / 20 : height;
        return {x,y,z,targetX:x+range*dx,targetY:y-drop,targetZ:z+range*dz};
    }
    function markShadowDirty(entry) {
        if (!entry.light.castShadow) return;
        entry.light.shadow.needsUpdate = true;
    }

    function updateAirBeam(entry) {
        const { light, model } = entry;
        const visible = light.intensity > 0 && model?.visible && entry.emitter?.mesh;
        if (visible && !entry.air) {
            airGeometry ??= new THREE.CylinderGeometry(1, 0, 1, 32).translate(0, .5, 0);
            airMaterial ??= new THREE.ShaderMaterial({
                transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
                uniforms: {
                    occluders: { value: null }, hasOccluders: { value: false },
                    shadowProjection: { value: new THREE.Matrix4() }, shadowBias: { value: 0 }, beamColor: { value: new THREE.Color() },
                    eyeInCone: { value: new THREE.Vector3() }, beamSize: { value: new THREE.Vector3() },
                },
                vertexShader: `
                    varying vec3 entryPoint;
                    void main() {
                        entryPoint = position;
                        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
                    }`,
                fragmentShader: `
                    #include <packing>
                    uniform sampler2D occluders;
                    uniform bool hasOccluders;
                    uniform mat4 shadowProjection;
                    uniform float shadowBias;
                    uniform vec3 beamColor;
                    uniform vec3 eyeInCone, beamSize;
                    varying vec3 entryPoint;
                    ${beamFalloff}
                    ${beamShadowBias}
                    float densityAt(vec3 point) {
                        if (hasOccluders) {
                            vec4 shadowPosition = shadowProjection * vec4(point, 1.0);
                            if (shadowPosition.w > 0.0) {
                                vec3 coord = shadowPosition.xyz / shadowPosition.w;
                                if (all(greaterThanEqual(coord.xy, vec2(0.0))) && all(lessThanEqual(coord.xy, vec2(1.0)))) {
                                    float depth = unpackRGBAToDepth(texture2D(occluders, coord.xy));
                                    if (coord.z + portableBeamDepthBias(shadowBias, shadowPosition.w) > depth) return 0.0;
                                }
                            }
                        }
                        float distanceAlong = point.y;
                        float edge = 1.0 - smoothstep(${1 - beamPenumbra}, 1.0, length(point.xz) / max(distanceAlong, 0.001));
                        return edge * smoothstep(0.0, 0.015, distanceAlong) * portableBeamFalloff(distanceAlong);
                    }
                    void main() {
                        // Толщина светящегося воздуха вдоль взгляда, включая вид сзади вдоль оси.
                        vec3 ray = normalize(entryPoint - eyeInCone);
                        float cap = ray.y > 0.00001 ? (1.0 - entryPoint.y) / ray.y
                            : ray.y < -0.00001 ? -entryPoint.y / ray.y : 4.0;
                        float a = dot(ray.xz, ray.xz) - ray.y * ray.y;
                        float b = dot(entryPoint.xz, ray.xz) - entryPoint.y * ray.y;
                        float c = dot(entryPoint.xz, entryPoint.xz) - entryPoint.y * entryPoint.y;
                        float exitDistance = cap;
                        if (a > 0.00001) {
                            exitDistance = min(cap, (-b + sqrt(max(0.0, b * b - a * c))) / a);
                        }
                        vec3 span = ray * max(0.0, exitDistance);
                        float density = 0.0;
                        for (int index = 0; index < 5; index++) {
                            density += densityAt(entryPoint + span * ((float(index) + 0.5) / 5.0));
                        }
                        float opacity = 0.23 * (1.0 - exp(-0.32 * length(span * beamSize) * density));
                        gl_FragColor = vec4(beamColor, opacity);
                        #include <colorspace_fragment>
                    }`,
            });
            entry.air = new THREE.Mesh(airGeometry, airMaterial);
            entry.air.name = 'portable_flashlight_beam';
            // Берём карту того же фонаря после обновления теней в текущем кадре.
            const inverse = new THREE.Matrix4();
            entry.air.onBeforeRender = (renderer, _scene, camera) => {
                const { uniforms } = airMaterial;
                const { matrixWorld, scale } = entry.air;
                inverse.copy(matrixWorld).invert();
                uniforms.occluders.value = light.shadow.map?.texture ?? null;
                uniforms.hasOccluders.value = renderer.shadowMap.enabled && Boolean(uniforms.occluders.value);
                uniforms.shadowProjection.value.multiplyMatrices(light.shadow.matrix, matrixWorld);
                uniforms.shadowBias.value = light.shadow.bias;
                uniforms.beamColor.value.copy(light.color);
                uniforms.eyeInCone.value.setFromMatrixPosition(camera.matrixWorld).applyMatrix4(inverse);
                uniforms.beamSize.value.copy(scale);
                airMaterial.uniformsNeedUpdate = true;
            };
            scene.add(entry.air);
        }
        if (!entry.air) return;
        entry.air.visible = Boolean(visible);
        if (!visible) return;
        airDirection ??= new THREE.Vector3();
        airUp ??= new THREE.Vector3(0, 1, 0);
        airDirection.subVectors(light.target.position, light.position).normalize();
        const reach = light.distance;
        const width = Math.tan(light.angle) * reach;
        entry.air.position.copy(light.position);
        entry.air.quaternion.setFromUnitVectors(airUp, airDirection);
        entry.air.scale.set(width, reach, width);
    }

    function updateLightPosition(entry) {
        const {light,source,modelEntry}=entry;
        const {x,y,z,targetX,targetY,targetZ}=sourcePose(entry);
        const position=light.position,target=light.target?.position;
        const followsHand = source.radial && source.mount === 'hand';
        const revision = followsHand ? modelEntry?.humanAppearanceModel?.userData.shadowPoseRevision : undefined;
        const holderPoseChanged = revision !== entry.shadowPoseRevision;
        entry.shadowPoseRevision = revision;
        if(position.x===x&&position.y===y&&position.z===z
            &&(!target||target.x===targetX&&target.y===targetY&&target.z===targetZ)){
            if (entry.air && entry.air.visible !== entry.model?.visible) updateAirBeam(entry);
            if (holderPoseChanged) markShadowDirty(entry);
            return;
        }
        position.set(x,y,z);
        if(target){target.set(targetX,targetY,targetZ);light.target.updateMatrixWorld();}
        if (target) updateAirBeam(entry);
        if (!light.castShadow) return;
        const previous = entry.shadowPose;
        const pose = target ? [x,y,z,targetX,targetY,targetZ] : [x,y,z];
        if (followsHand || !previous || pose.some((value, i) => Math.abs(value - previous[i]) >= shadowShift)) {
            entry.shadowPose = pose;
            markShadowDirty(entry);
        }
    }

    function setExtraSources(sources) {
        extraSources = sources.slice(0, extraCapacity);
        if (extraSources.length) prepare();
        if (extraUniforms) extraUniforms.portableExtraCount.value = extraSources.length;
        for (let i = 0; i < extraSources.length; i++) {
            const entry = extraSources[i];
            const { source } = entry;
            const color = new THREE.Color(source.radial ? (source.ignition_aura ? '#ffab64' : source.color || '#dcf0aa') : '#fff4d8');
            color.multiplyScalar(lightIntensity(source));
            extraUniforms.portableExtraColor.value[i].set(color.r, color.g, color.b, 0);
            const angle = beamAngle(source);
            extraUniforms.portableExtraCone.value[i].set(Math.cos(angle), Math.cos(angle * (1 - beamPenumbra)));
        }
    }

    function configureSpot(entry, definition) {
        const {light} = entry;
        const {source} = definition;
        light.angle = beamAngle(source);
        light.distance = source.range || 20;
        light.color.set('#fff4d8');
        light.shadow.needsUpdate = true;
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
            entry.light.intensity = definition ? lightIntensity(entry.source) : 0;
            if (definition) updateLightPosition(entry);
            else if (entry.air) entry.air.visible = false;
        }
        setExtraSources(wanted.filter(({ source }) => !lights.has(source.id)));
    }

    function currentLayoutKey() {
        const points = [...lights.values()].filter(({ light }) => light.isPointLight);
        return `${spotPool.filter(({ light }) => light.visible).length}:${points.length}:${points.filter(({ light }) => light.castShadow).length}`;
    }

    function updateShadowView(camera) {
        if (!camera) return false;
        camera.updateWorldMatrix(true, false);
        shadowViewProjection ??= new THREE.Matrix4();
        shadowViewSample ??= new THREE.Matrix4();
        shadowViewFrustum ??= new THREE.Frustum();
        shadowViewSample.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
        if (shadowViewProjection.equals(shadowViewSample)) return false;
        shadowViewProjection.copy(shadowViewSample);
        shadowViewFrustum.setFromProjectionMatrix(shadowViewProjection);
        return true;
    }

    function radialShadowPriority(entry) {
        shadowInfluence ??= new THREE.Sphere();
        const active = lights.get(entry.source.id);
        const { x, y, z } = sourcePose(entry);
        const hasShadow = Boolean(active?.light.castShadow);
        shadowInfluence.center.set(x, y, z);
        shadowInfluence.radius = (entry.source.range || 5) + (hasShadow ? .25 : 0);
        if (!shadowViewFrustum.intersectsSphere(shadowInfluence)) return 2;
        // Удерживаем карту, пока в кадре есть часть освещённой области.
        return hasShadow ? 0 : 1;
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
                    lastSelection.groundSources, camera, lastSelection.focus);
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
        update(characters,models,groundSources=[],camera=null,focus=camera?.position) {
            lastSelection={characters,models,groundSources,focus,x:focus?.x,z:focus?.z};
            const wanted=[];
            for (const character of characters || []) {
                const modelEntry=models.get(character.character_id),model=modelEntry?.model;
                if (!model?.visible) continue;
                for (const source of character.portable_lights || []) wanted.push({source,character,model,modelEntry});
            }
            for(const source of groundSources||[])wanted.push({source,character:null,model:null,modelEntry:null});
            if (focus) {
                // При вращении центр обзора остаётся на месте. Небольшой перевес текущих
                // источников удерживает тени от смены мест на границе при панорамировании.
                const distance = ({source,model}) => Math.hypot(focus.x - (model?.position.x ?? source.x),
                    focus.z - (model?.position.z ?? source.z)) - (lights.get(source.id)?.light.castShadow ? 1.5 : 0);
                wanted.sort((a,b) => distance(a) - distance(b));
            }
            // Ближайшие направленные источники используют общие места для карт теней.
            const spots=wanted.filter(entry=>!entry.source.radial).slice(0,4);
            const radialCandidates=wanted.filter(entry=>entry.source.radial);
            const radialSlots=4-spots.length;
            lastSelection.radialContest=radialSlots>0 && radialCandidates.length>radialSlots;
            if (camera && lastSelection.radialContest) {
                updateShadowView(camera);
                for (const entry of radialCandidates) entry.shadowPriority = radialShadowPriority(entry);
                radialCandidates.sort((left, right) => left.shadowPriority - right.shadowPriority);
            }
            const radial=radialCandidates.slice(0,4);
            const primary=[...spots,...radial];
            // Ближайшие точечные источники используют оставшиеся места для теней.
            const radialShadows = new Set(radial
                .slice(0, radialSlots)
                .map(({ source }) => source.id));
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
                            lastSelection.groundSources, renderContext.camera, lastSelection.focus);
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
                    if(source.radial){
                        const light = new THREE.PointLight(
                            source.ignition_aura ? '#ffab64' : source.color || '#dcf0aa',
                            lightIntensity(source), source.range || 5, source.ignition_aura ? 1 : glowDecay);
                        shadowSettings(light);
                        if (!source.ignition_aura) {
                            light.shadow.mapSize.set(256, 256);
                            light.shadow.camera.near = .04;
                            light.shadow.normalBias = .008;
                            light.shadow.radius = 4;
                        }
                        light.castShadow = false;
                        scene.add(light);entry={light,source};
                    }else{
                        prepare();entry=spotPool.find(slot=>!slot.active);
                        entry.active=true;entry.light.visible=true;
                        configureSpot(entry, definition);
                    }
                    lights.set(source.id,entry);
                }
                Object.assign(entry,definition);
                entry.light.intensity = lightIntensity(source);
                if (entry.light.isSpotLight) entry.light.shadow.portableOwner = entry.model || null;
                if(entry.light.isPointLight){
                    const near = source.ignition_aura ? .08 : entry.model ? .14 : .04;
                    if (entry.light.shadow.camera.near !== near) {
                        entry.light.shadow.camera.near = near;
                        entry.light.shadow.camera.updateProjectionMatrix?.();
                        markShadowDirty(entry);
                    }
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
        updatePositions(camera,focus=camera?.position) {
            if (lastSelection) {
                const dx=focus ? focus.x-lastSelection.x : 0,dz=focus ? focus.z-lastSelection.z : 0;
                const anchorMoved=lights.size+extraSources.length>4 && dx*dx+dz*dz>=9;
                const viewChanged=lastSelection.radialContest && updateShadowView(camera);
                if (anchorMoved || viewChanged) {
                    this.update(lastSelection.characters,lastSelection.models,lastSelection.groundSources,camera,focus);
                }
            }
            for (const entry of lights.values()) {
                if (entry.light.intensity <= 0) continue;
                updateLightPosition(entry);
            }
        },
        dirty(from,to=from) {
            for (const entry of lights.values()) {
                const {light} = entry;
                if (!light.castShadow || light.intensity === 0) continue;
                const reach=(light.distance+1)**2;
                if (!from || light.position.distanceToSquared(from)<=reach || light.position.distanceToSquared(to)<=reach) {
                    markShadowDirty(entry);
                }
            }
        },
        dispose() {
            for (const entry of lights.values())if(!entry.light.isSpotLight)dispose(entry);lights.clear();
            for(const entry of spotPool)dispose(entry);spotPool.length=0;
            airGeometry?.dispose();airMaterial?.dispose();
            airGeometry=airMaterial=null;
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
