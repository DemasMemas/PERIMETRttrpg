import * as THREE from 'three';
import { createSurfaceTexture } from './locationTerrainDetail.js';

export const WATER_PROFILE_COLOR_GLSL = `
float neutralWaterWeight(vec4 context){
    float amount = dot(context,vec4(1.0));
    return smoothstep(.035,.21,amount)*(1.0-smoothstep(.30,.67,amount));
}
vec3 waterProfileColor(vec4 context,float sediment,float floc,float rust){
    vec3 appearance = vec3(0.105,0.225,0.275);
    appearance = mix(appearance,vec3(.135,.178,.181),neutralWaterWeight(context)*.83);
    appearance = mix(appearance,vec3(.37,.295,.168),context.r*(0.67+0.24*sediment));
    appearance = mix(appearance,vec3(0.16,0.135,0.095),context.g*(0.85+0.10*floc));
    appearance = mix(appearance,vec3(.167,.198,.221),context.b*.90);
    appearance = mix(appearance,mix(vec3(.075,.080,.067),vec3(.218,.117,.062),rust*.68),
        smoothstep(.0,.72,context.a)*.92);
    return appearance;
}
`;

export function createWaterTexture() {
    const texture=createSurfaceTexture(THREE,'water'),canvas=texture.image,ctx=canvas.getContext('2d'),pixels=ctx.getImageData(0,0,canvas.width,canvas.height);
    // Original RGB channels share one wave/defect signal. Preserve R/B in A/B
    // and reconstruct G between them; use RG for a tileable ripple normal.
    // Thus colour and normals share the same existing 128² mipmapped texture.
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
        const i=(y*canvas.width+x)*4,u=x/canvas.width*Math.PI*2,v=y/canvas.height*Math.PI*2,
            a=Math.cos(u*3+Math.sin(v*2)*.6),b=Math.cos(v*5-u*2);
        pixels.data[i+3]=pixels.data[i];
        pixels.data[i]=Math.round(127.5+(3*a-.8*b)/4*127.5);
        pixels.data[i+1]=Math.round(127.5+(1.2*a*Math.cos(v*2)+2*b)/4*127.5);
    }
    ctx.putImageData(pixels,0,0);texture.colorSpace=THREE.LinearSRGBColorSpace;texture.needsUpdate=true;
    return texture;
}

export function createWaterMaterial({ world = false, profileLayer = null, reflection = null,
    mudOverlay = false, hasShallow = false, hasShore = false } = {}) {
    const waterTexture = createWaterTexture();
    const material = new THREE.MeshStandardMaterial({
        map: waterTexture,
        color: 0xb8c3c7,
        roughness: mudOverlay ? .4 : .65,
        metalness: 0,
        emissive: 0x04090d,
        transparent: mudOverlay || hasShallow,
        // Поверхность перекрывает пол сцены под открытой водой. Дно мелководья
        // отрисовывается первым и остаётся видно через полупрозрачные края.
        depthWrite: !mudOverlay,
        vertexColors: mudOverlay,
        opacity: mudOverlay ? .88 : 1,
    });
    material.extensions = { derivatives: true };
    material.customProgramCacheKey = () => `world-aligned-water-v10:${world}:${!!profileLayer}:${!!reflection}:${mudOverlay}:${hasShallow}:${hasShore}`;
    material.onBeforeCompile = shader => {
        if(profileLayer)Object.assign(shader.uniforms,profileLayer.uniforms);
        if (reflection) Object.assign(shader.uniforms, reflection.uniforms);
        shader.uniforms.waterPatternOffset = { value: waterTexture.offset };
        shader.uniforms.waterRipple = { value: new THREE.Vector2(world ? 2 : 0.5, world ? 0.049 : 0.063) };
        shader.vertexShader = shader.vertexShader
            .replace('#include <common>', `#include <common>
                varying vec2 vWaterWorldXZ;
                ${hasShallow ? 'attribute vec2 waterDepth; varying vec2 vWaterDepth;' : ''}
                ${hasShore ? 'attribute float waterMargin; varying float vWaterMargin;' : ''}
                ${reflection ? 'uniform mat4 waterReflectionMatrix; varying vec4 vWaterReflection; varying float vWaterY;' : ''}`)
            .replace('#include <project_vertex>', `
                vec4 waterWorld = vec4(transformed, 1.0);
                #ifdef USE_INSTANCING
                    waterWorld = instanceMatrix * waterWorld;
                #endif
                vWaterWorldXZ = (modelMatrix * waterWorld).xz;
                ${hasShallow ? 'vWaterDepth = waterDepth;' : ''}
                ${hasShore ? 'vWaterMargin = waterMargin;' : ''}
                ${reflection ? `vec4 reflectionWorld = modelMatrix * waterWorld;
                vWaterReflection = waterReflectionMatrix * reflectionWorld; vWaterY = reflectionWorld.y;` : ''}
                #include <project_vertex>
            `);
        shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>
                varying vec2 vWaterWorldXZ;
                uniform vec2 waterPatternOffset;
                uniform vec2 waterRipple;
                ${hasShallow ? 'varying vec2 vWaterDepth;' : ''}
                ${hasShore ? 'varying float vWaterMargin;' : ''}
                ${reflection ? `uniform sampler2D waterReflectionMap;
                uniform float waterReflectionReady;
                uniform float waterReflectionLevel;
                uniform vec2 waterReflectionTexel;
                varying vec4 vWaterReflection;
                varying float vWaterY;` : ''}
                ${profileLayer ? `uniform sampler2D waterProfiles;
                uniform vec2 waterProfilesSize;
                vec4 waterContext;
                float waterFloc;
                ${WATER_PROFILE_COLOR_GLSL}` : ''}
            `)
            .replace('#include <map_fragment>', `
                #ifdef USE_MAP
                    vec4 waterSample = texture2D(map, vWaterWorldXZ * ${world ? '0.16' : '0.36'} + waterPatternOffset);
                    vec3 waterColor = vec3(waterSample.a,mix(waterSample.a,waterSample.b,52.0/69.0),waterSample.b);
                    ${profileLayer ? `
                    waterContext = texture2D(waterProfiles, vWaterWorldXZ / waterProfilesSize);
                    float waveSignal=clamp((waterSample.a-55.0/255.0)*20.0,-1.0,1.0);
                    float sediment = smoothstep(-0.35,0.45,waveSignal);
                    waterFloc = smoothstep(0.25,0.70,waterSample.r*0.55+waterSample.g*0.45);
                    float rustStreak = smoothstep(0.38,0.68,waterSample.r)
                        * (0.35+0.65*sediment);
                    // Profile radiance is already in the renderer working (linear) space.
                    vec3 appearance = waterProfileColor(waterContext,sediment,waterFloc,rustStreak);
                    float shore = ${hasShore ? '1.0-smoothstep(.0,.92,vWaterMargin)' : '0.0'};
                    vec2 p = vWaterWorldXZ * ${world ? '0.24' : '1.0'};
                    float sand = smoothstep(.18,.82,waterContext.r)*(1.0-waterContext.a);
                    float mineral = smoothstep(.17,.82,waterContext.b)*(1.0-waterContext.a);
                    float industrial = smoothstep(.10,.68,waterContext.a);
                    // Полосы песка лежат на дне. Производные гасят рябь при отдалении камеры.
                    float sandPhase = dot(p,vec2(14.0,20.0)) + sin(p.x*3.4+p.y*.72)*1.8;
                    float sandRidges = sin(sandPhase) * (1.0-smoothstep(.6,2.6,fwidth(sandPhase)));
                    appearance = mix(appearance,vec3(.43,.35,.215)*(1.0+.07*sandRidges),
                        sand*(.22+.30*shore));
                    float chalk = smoothstep(.54,.91,sin(p.x*4.1+p.y*2.8+sin(p.y*3.2))*.5+.5);
                    appearance = mix(appearance,vec3(.30,.34,.348),mineral*(.09+.27*shore)*chalk);
                    float oilyBand = .5+.29*sin(p.x*2.4+p.y*1.1+sin(p.y*2.8-p.x*.61)*1.9)
                        +.16*sin(p.x*1.3-p.y*3.8+sin(p.x*2.1)*1.4);
                    appearance = mix(appearance, mix(vec3(.078,.099,.084),vec3(.171,.099,.063),oilyBand),
                        industrial*(.20+.26*shore));
                    ${hasShallow ? `
                    float shoal = smoothstep(.08,.96,vWaterDepth.x);
                    appearance = mix(appearance, vec3(.222,.192,.128), shoal*.67);
                    ` : ''}
                    float cleanWave = 1.0 + waveSignal * mix(.12,.036,
                        max(waterContext.g,max(industrial,.55*mineral)));
                    waterColor = appearance * cleanWave;
                    ` : ''}
                    ${profileLayer ? 'diffuseColor *= vec4(waterColor,1.0);' : `
                    diffuseColor.rgb *= mix(pow((waterColor + 0.055) / 1.055, vec3(2.4)),
                        waterColor / 12.92, vec3(lessThanEqual(waterColor, vec3(0.04045))));
                    `}
                #endif
                ${hasShallow ? 'diffuseColor.a *= vWaterDepth.y; if(diffuseColor.a < .006) discard;' : ''}
            `)
            .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
                ${hasShallow ? 'normal = normalize(mix(normal, (viewMatrix * vec4(0.,1.,0.,0.)).xyz, smoothstep(.0,.32,vWaterDepth.x)));' : ''}
                vec2 rippleUv = vWaterWorldXZ * waterRipple.x;
                vec2 slope = texture2D(map, rippleUv + waterPatternOffset * 4.0).rg * 2.0 - 1.0;
                vec2 crossWave = texture2D(map, vec2(-rippleUv.y, rippleUv.x) * 0.61
                    - waterPatternOffset * 2.5).rg * 2.0 - 1.0;
                slope = slope * 0.72 + vec2(crossWave.y, -crossWave.x) * 0.28;
                normal = normalize(normal + (viewMatrix * vec4(-slope.x,0.0,-slope.y,0.0)).xyz
                    * waterRipple.y ${profileLayer ? '* (1.0 - .40*waterContext.a - .12*waterContext.r)' : ''});
            `)
            .replace('#include <opaque_fragment>', `
                // Блик неба усиливается под низким углом, муть сохраняет собственный цвет.
                float waterView = clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0);
                float waterFresnel = 0.025 + 0.42 * pow(1.0 - waterView, 5.0);
                float waterClarity = ${profileLayer ? '1.0 - dot(waterContext, vec4(0.25, 0.55, 0.2, 0.28))' : '1.0'};
                float waterDaylight = clamp(max(max(ambientLightColor.r, ambientLightColor.g),
                    ambientLightColor.b) * 0.75, 0.03, 1.0);
                vec3 waterSky = mix(vec3(0.025, 0.047, 0.080), vec3(0.40, 0.55, 0.66), waterDaylight);
                ${profileLayer && !world ? 'waterSky = mix(waterSky, vec3(.41,.43,.36)*waterDaylight, waterContext.g*.76);' : ''}
                ${profileLayer ? `
                float oilView = pow(1.0 - waterView, 2.0);
                waterSky = mix(waterSky,mix(vec3(.23,.31,.28),vec3(.34,.26,.25),
                    .5 + .5*sin(vWaterWorldXZ.x*1.8+vWaterWorldXZ.y*.72+waterView*4.0))*waterDaylight,
                    waterContext.a * oilView);
                ` : ''}
                ${reflection ? `
                float reflectionHeight = abs(vWaterY - waterReflectionLevel);
                if (waterReflectionReady > 0.5 && reflectionHeight < ${hasShallow ? '.14' : '.035'}
                    && vWaterReflection.w>0.0) {
                    vec2 reflectionUv = vWaterReflection.xy / vWaterReflection.w * .5 + .5 + slope*.0018;
                    vec2 border = min(reflectionUv, 1.0-reflectionUv);
                    float coverage = smoothstep(0.0, .025, min(border.x, border.y));
                    vec2 blur = waterReflectionTexel * .65;
                    vec3 reflected = (
                        texture2D(waterReflectionMap, reflectionUv + blur).rgb
                      + texture2D(waterReflectionMap, reflectionUv - blur).rgb
                      + texture2D(waterReflectionMap, reflectionUv + vec2(blur.x,-blur.y)).rgb
                      + texture2D(waterReflectionMap, reflectionUv + vec2(-blur.x,blur.y)).rgb) * .25;
                    outgoingLight = mix(outgoingLight, reflected*.62, coverage * (.28 + .32*pow(1.0-waterView,3.0))
                        ${hasShallow ? '* (1.0 - vWaterDepth.x*.86) * (1.0 - smoothstep(.012,.14,reflectionHeight))' : ''});
                }
                ` : ''}
                outgoingLight = mix(outgoingLight, waterSky, waterFresnel * max(0.25, waterClarity));
                #include <opaque_fragment>
            `);
        if(profileLayer)shader.fragmentShader=shader.fragmentShader
            .replace('#include <roughnessmap_fragment>',`#include <roughnessmap_fragment>
                roughnessFactor=clamp(roughnessFactor+waterContext.r*.12+waterContext.b*.095
                    +waterContext.g*(.10+waterFloc*.08)-waterContext.a*.18,${mudOverlay ? '.40,.68' : '.47,.95'});`);
    };
    return material;
}
