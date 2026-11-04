import * as THREE from 'three';
import { getMapSceneContext, sameMapScope } from './mapSceneContext.js';
import { openCreateMarkerModal, completeMarkerTilePick } from './markers.js';
import { isTypingTarget } from './cameraPan.js';
import { showNotification } from './utils.js';

export function mapDistance(from, to, local = false) {
    return Math.hypot(to.x - from.x, to.z - from.z, local ? to.y - from.y : 0);
}

export function diceCount(value) {
    return Math.max(1, Math.min(100, Math.floor(Number(value)) || 1));
}

export function initCommonMapTools(socket) {
    const panel = document.getElementById('panel-common-tools');
    if (!panel || panel.dataset.initialized === 'true') return;
    panel.dataset.initialized = 'true';
    const output = document.getElementById('common-map-result');
    const dice = document.getElementById('common-dice-form');
    const count = document.getElementById('common-dice-count');
    let mode = null, start = null, line = null, frame = null, lastPointer = null;
    let suppressClickUntil = 0;
    let consumedPointer = null;
    const pings = new Map();
    const measurements=new Map();
    const measurementData = new Map();
    let sharedMeasure=null, measureTimer=null, pendingMeasure=null, lastMeasureAt=-Infinity;
    const sendMeasure=payload=>{
        if(!socket.connected)return;
        socket.emit('map_measure',payload,result=>{if(result&&!result.ok)showNotification(result.error,'error');});
    };
    const publishMeasure=(from,to,final)=>{
        const context=getMapSceneContext();
        const encode=p=>({x:p.x,y:p.y,z:p.z,...(context.locationId?{floor_level:p.floor_level??context.level}:{})});
        sharedMeasure={from:encode(from),to:encode(to),final,...(context.locationId?{location_id:context.locationId}:{})};
        pendingMeasure=sharedMeasure;
        if(final){clearTimeout(measureTimer);measureTimer=null;lastMeasureAt=performance.now();sendMeasure(pendingMeasure);pendingMeasure=null;}
        else if(measureTimer===null)measureTimer=setTimeout(()=>{
            measureTimer=null;lastMeasureAt=performance.now();if(pendingMeasure)sendMeasure(pendingMeasure);pendingMeasure=null;
        },Math.max(0,100-(performance.now()-lastMeasureAt)));
    };
    const disposeMeasure=mesh=>{
        mesh.parent?.remove(mesh);mesh.geometry.dispose();mesh.material.dispose();
        for(const child of mesh.children){child.material.map?.dispose();child.material.dispose();}
    };
    const sync = () => {
        panel.querySelectorAll('[data-common-map-tool]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.commonMapTool === mode));
        });
    };
    const cancel = () => {
        mode = null; window.commonMapToolMode = null; start = null;
        if (frame !== null) cancelAnimationFrame(frame);
        frame = null;
        if (line) { disposeMeasure(line); line = null; }
        clearTimeout(measureTimer);measureTimer=null;pendingMeasure=null;
        if(sharedMeasure)sendMeasure({clear:true});sharedMeasure=null;
        dice.hidden = true;
        output.textContent = ''; sync();
    };
    window.cancelCommonMapTool = cancel;
    const available = () => mode && getMapSceneContext();
    const onCanvas = event => event.target === getMapSceneContext()?.canvas;
    const resolvePoint = (point, context) => ({...point, y: context.locationId
        ? context.surface(point.x, point.z, point.floor_level ?? 0) ?? point.y : point.y});
    const makeMeasure = (color='#f1d878') => {
            const context=getMapSceneContext();
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(6), 3));
            const mesh = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, depthTest: false, depthWrite: false, fog: false }));
            mesh.frustumCulled = false; mesh.renderOrder = 1000; mesh.userData.commonMapMeasure = true;
            const labelCanvas=document.createElement('canvas');labelCanvas.width=256;labelCanvas.height=48;
            const texture=new THREE.CanvasTexture(labelCanvas);
            const label=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false,depthWrite:false,fog:false,sizeAttenuation:false}));
            label.scale.set(.12,.023,1);label.renderOrder=1001;mesh.add(label);mesh.userData.mapContext=context;context.scene.add(mesh);return mesh;
    };
    const updateMeasure=(mesh,from,point)=>{
        const context=mesh.userData.mapContext;
        mesh.userData.ends=[from,point];
        mesh.visible=!context.locationId||context.visibleLevel(from.floor_level)||context.visibleLevel(point.floor_level);
        from=resolvePoint(from,context);point=resolvePoint(point,context);
        const positions = mesh.geometry.getAttribute('position');
        positions.setXYZ(0, from.x, from.y + 0.15, from.z);
        positions.setXYZ(1, point.x, point.y + 0.15, point.z); positions.needsUpdate = true;
        const distance=mapDistance(from,point,Boolean(context.locationId)).toFixed(2),label=mesh.children[0],canvas=label.material.map.image,ctx=canvas.getContext('2d');
        if(mesh.userData.distance!==distance){mesh.userData.distance=distance;ctx.clearRect(0,0,256,48);ctx.fillStyle='#151b16';ctx.fillRect(0,0,256,48);ctx.fillStyle='#f1d878';ctx.font='bold 26px sans-serif';ctx.textAlign='center';ctx.fillText(distance+' '+context.unit,128,33);label.material.map.needsUpdate=true;mesh.userData.distance=distance;}
        label.position.set((from.x+point.x)/2,(from.y+point.y)/2+.35,(from.z+point.z)/2);
    };
    const drawMeasure = (point,final=false) => {
        if(!line)line=makeMeasure();
        updateMeasure(line,start,point);publishMeasure(start,point,final);
        const context=getMapSceneContext();
        output.textContent = `${mapDistance(resolvePoint(start,context), resolvePoint(point,context), Boolean(context.locationId)).toFixed(2)} ${context.unit} по прямой${context.locationId ? ' (с учётом высоты)' : ''}`;
    };
    panel.addEventListener('click', event => {
        const button = event.target.closest('[data-common-map-tool]');
        if (!button) return;
        const context=getMapSceneContext();
        if (!context) return;
        const next = button.dataset.commonMapTool;
        const previous = mode;
        cancel();
        if (previous === next) return;
        window.clearDirectWorldObjectSelection?.();
        window.cancelWorldTravelSelection?.();
        if (context.locationId && next !== 'dice' && context.prepareTool?.() === false) {
            showNotification('Сначала завершите текущее редактирование или перемещение.', 'system'); return;
        }
        if(context.locationId)document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();
        mode = next; window.commonMapToolMode = next; sync();
        if (next === 'dice') { dice.hidden = false; return; }
        output.textContent = next === 'measure' ? 'Две точки — расстояние. Следующий клик — новое измерение. ПКМ/Esc — выход.' : 'Укажите клетку на карте. ПКМ/Esc — выход.';
    });
    count.addEventListener('change', () => { count.value = diceCount(count.value); });
    dice.addEventListener('click', event => {
        const adjust = event.target.closest('[data-dice-count]');
        if (adjust) { count.value = diceCount(diceCount(count.value) + Number(adjust.dataset.diceCount)); return; }
        const button = event.target.closest('[data-dice-sides]');
        if (!button) return;
        count.value = diceCount(count.value);
        const expression = `${count.value}d${button.dataset.diceSides}`;
        const chat = document.getElementById('panel-chat');
        if (chat.hidden || chat.dataset?.chatView === 'actions') document.getElementById('chat-toggle').click();
        if (chat.classList.contains('collapsed')) chat.querySelector('.toggle-btn').click();
        socket.emit('send_message', { token: localStorage.getItem('access_token'), lobby_id: window.currentLobbyId, message: `/roll ${expression}` });
    });
    const consume = event => { event.preventDefault(); event.stopImmediatePropagation(); };
    window.addEventListener('pointerdown', event => {
        if (!available() || mode === 'dice' || !onCanvas(event) || event.button!==0) return;
        consume(event); consumedPointer = event.pointerId; suppressClickUntil = performance.now() + 500;
        const point = getMapSceneContext().point(event.clientX, event.clientY);
        if (!point) return;
        if (completeMarkerTilePick(point)) return;
        if (mode === 'measure') {
            if (!start) { start = Object.assign(point.clone(), {floor_level:point.floor_level??0}); drawMeasure(point); }
            else { drawMeasure(point,true); start = null; }
        } else if (mode === 'marker') {
            openCreateMarkerModal({ x: point.x, y: point.y + (getMapSceneContext().locationId ? 0 : 0.8), z: point.z });
        } else if (mode === 'ping') {
            const context=getMapSceneContext();
            const payload = { ...(context.locationId?{location_id:context.locationId,position:{x:point.x,y:point.y,z:point.z},floor_level:context.level}:{}), lobby_id: window.currentLobbyId, tile_x: Math.floor(point.x), tile_y: Math.floor(point.z), user_id: Number(localStorage.getItem('user_id')) };
            showPing(payload);
            const timeout = setTimeout(() => showNotification('Сервер не ответил на пинг. Перезапустите Flask и обновите страницу.', 'error'), 4000);
            socket.emit('map_ping', payload, result => {
                clearTimeout(timeout);
                if (result && !result.ok) showNotification(result.error, 'error');
            });
        }
    }, { capture: true });
    window.addEventListener('pointermove', event => {
        if (!available() || mode === 'dice' || !onCanvas(event) || (event.buttons&2)) return;
        consume(event);
        if (!start) return;
        lastPointer = { x: event.clientX, y: event.clientY };
        if (frame !== null) return;
        frame = requestAnimationFrame(() => {
            frame = null;
            if (!start || !available()) return;
            const point = getMapSceneContext().point(lastPointer.x, lastPointer.y);
            if (point) drawMeasure(point);
        });
    }, { capture: true });
    window.addEventListener('pointerup', event => {
        if (event.pointerId !== consumedPointer) return;
        consumedPointer = null; suppressClickUntil = performance.now() + 350; consume(event);
    }, { capture: true });
    window.addEventListener('pointercancel', () => { consumedPointer = null; cancel(); }, { capture: true });
    window.addEventListener('blur', () => {
        consumedPointer = null;
        if(getMapSceneContext()?.locationId && sharedMeasure?.final && !start){
            mode=null;window.commonMapToolMode=null;sync();return;
        }
        cancel();
    });
    for (const type of ['click', 'dblclick']) {
        window.addEventListener(type, event => {
            if (onCanvas(event) && performance.now() < suppressClickUntil) consume(event);
        }, { capture: true });
    }
    window.addEventListener('contextmenu', event => {
        if (onCanvas(event) && performance.now() < suppressClickUntil) consume(event);
    }, { capture: true });
    window.addEventListener('secondaryclick',event=>{
        if(available()&&mode!=='dice'&&onCanvas(event)){consume(event);cancel();}
    },{capture:true});
    window.addEventListener('keydown', event => {
        if (isTypingTarget(event.target) || (!mode && !line)) return;
        if (event.key === 'Escape') {
            for(const id of ['marker-create-modal','marker-edit-modal']) {
                const modal=document.getElementById(id);if(modal?.style.display==='flex')modal.style.display='none';
            }
            consume(event); cancel(); return;
        }
        if (mode !== 'dice' && /^(Key[GTRQBEHV]|Digit[1-8]|Delete)$/.test(event.code)) { cancel(); }
    }, { capture: true });
    // A screen-sized sprite stays visible from the normal world overview camera,
    // independently of terrain fog, perspective and zoom.
    const showPing = payload => {
        const context=getMapSceneContext();
        if (Number(payload.lobby_id) !== Number(window.currentLobbyId) || !sameMapScope(payload,context)) return;
        const old = pings.get(payload.user_id);
        if (old) { clearTimeout(old.timer); old.mesh.parent?.remove(old.mesh); old.mesh.material.map.dispose(); old.mesh.material.dispose(); }
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 128;
        const drawing = canvas.getContext('2d');
        drawing.strokeStyle = '#171b10'; drawing.lineWidth = 14;
        drawing.beginPath(); drawing.arc(64, 64, 39, 0, Math.PI * 2); drawing.stroke();
        drawing.strokeStyle = '#ffe27c'; drawing.lineWidth = 6; drawing.stroke();
        drawing.beginPath(); drawing.moveTo(64, 6); drawing.lineTo(64, 42); drawing.moveTo(64, 86); drawing.lineTo(64, 122);
        drawing.moveTo(6, 64); drawing.lineTo(42, 64); drawing.moveTo(86, 64); drawing.lineTo(122, 64); drawing.stroke();
        const texture = new THREE.CanvasTexture(canvas);
        const mesh = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, depthWrite: false, fog: false, sizeAttenuation: false }));
        mesh.scale.set(0.055, 0.055, 1);
        const position=context.locationId?resolvePoint({...payload.position,floor_level:payload.floor_level},context):{x:payload.tile_x+.5,z:payload.tile_y+.5,y:context.surface(payload.tile_x+.5,payload.tile_y+.5)};
        mesh.position.set(position.x,position.y+.16,position.z);
        mesh.visible=context.visibleLevel(payload.floor_level);
        mesh.renderOrder = 1001; mesh.userData.commonMapPing = payload; context.scene.add(mesh);
        const timer = setTimeout(() => { mesh.parent?.remove(mesh); mesh.material.map.dispose(); mesh.material.dispose(); pings.delete(payload.user_id); }, 3500);
        pings.set(payload.user_id, { mesh, timer });
    };
    socket.on('map_ping', showPing);
    const showSharedMeasure=payload=>{
        if(Number(payload.lobby_id)!==Number(window.currentLobbyId)||payload.id===socket.id)return;
        if(payload.clear)measurementData.delete(payload.id);else measurementData.set(payload.id,payload);
        let mesh=measurements.get(payload.id);
        if (!sameMapScope(payload)) {if(mesh){disposeMeasure(mesh);measurements.delete(payload.id);}return;}
        if(payload.clear){if(mesh){disposeMeasure(mesh);measurements.delete(payload.id);}return;}
        if(!mesh){mesh=makeMeasure('#8cdef0');measurements.set(payload.id,mesh);}
        mesh.userData.sharedMapMeasure=payload;updateMeasure(mesh,payload.from,payload.to);
    };
    socket.on('map_measure',showSharedMeasure);
    socket.on('map_measure_snapshot',items=>{for(const mesh of measurements.values())disposeMeasure(mesh);measurements.clear();measurementData.clear();for(const item of items)showSharedMeasure(item);});
    socket.on('disconnect',()=>{for(const mesh of measurements.values())disposeMeasure(mesh);measurements.clear();measurementData.clear();});
    window.addEventListener('map-scene-changing',()=>{
        cancel();for(const mesh of measurements.values())disposeMeasure(mesh);measurements.clear();
        for(const {mesh,timer} of pings.values()){clearTimeout(timer);mesh.parent?.remove(mesh);mesh.material.map.dispose();mesh.material.dispose();}pings.clear();
    });
    window.addEventListener('map-scene-changed',()=>{for(const data of measurementData.values())showSharedMeasure(data);});
    window.addEventListener('map-surface-changed',()=>{
        if(line&&line.userData.ends){updateMeasure(line,...line.userData.ends);output.textContent=`${line.userData.distance} ${line.userData.mapContext.unit} по прямой`;}
        for(const mesh of measurements.values())updateMeasure(mesh,...mesh.userData.ends);
        for(const {mesh} of pings.values()){const p=mesh.userData.commonMapPing,context=getMapSceneContext();if(!context?.locationId)continue;const point=resolvePoint({...p.position,floor_level:p.floor_level},context);mesh.position.set(point.x,point.y+.16,point.z);mesh.visible=context.visibleLevel(p.floor_level);}
    });
    window.addEventListener('mousemove',event=>{if(available()&&mode!=='dice'&&onCanvas(event)&&!(event.buttons&2))consume(event);},{capture:true});
    socket.on('authenticated',()=>{if(sharedMeasure)sendMeasure(sharedMeasure);});
}
