import {Server} from './api.js';
import {showNotification} from './utils.js';

let audio=null,pendingSound=false,lastTone=0,activeWatcher=null;
export async function testPdaSignal(shouldPlay=()=>true){
    const Context=window.AudioContext||window.webkitAudioContext;
    if(!Context)return false;
    audio ||= new Context();
    try{
        await audio.resume();if(audio.state!=='running'||!shouldPlay())return false;
        if(Date.now()-lastTone<350)return true;
        const oscillator=audio.createOscillator(),gain=audio.createGain(),now=audio.currentTime;
        oscillator.type='sine';oscillator.frequency.setValueAtTime(1250,now);
        gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(.035,now+.015);gain.gain.linearRampToValueAtTime(0,now+.15);
        oscillator.connect(gain);gain.connect(audio.destination);oscillator.start(now);oscillator.stop(now+.16);lastTone=Date.now();
        return true;
    }catch{return false;}
}

export function proximityDelta(previous,snapshot){
    const seen=new Set(),arrivals=[];
    for(const device of snapshot.devices||[])for(const signal of device.signals||[]){
        const key=`${device.id}:${signal.id}`;seen.add(key);
        if(!previous.has(key))arrivals.push({key,sound:device.sound!==false,login:signal.login});
    }
    return {seen,arrivals};
}

export function initPdaProximity(lobbyId,socket){
    activeWatcher?.close();
    let busy=false,closed=false,currentKey='',seen=new Set(),timer=null,queued=null,lastRefresh=0;
    const subject=()=>({characterId:window.isLocationActive?(window.currentLocationCharacterId||window.currentCharacterId):window.currentCharacterId,
        locationId:window.isLocationActive?window.currentLocationId:null});
    const keyFor=s=>`pda-proximity:${localStorage.getItem('user_id')}:${lobbyId}:${s.characterId}`;
    async function refresh(){
        if(closed||busy||document.hidden)return;
        const selected=subject();if(!selected.characterId)return;
        const key=keyFor(selected);
        if(key!==currentKey){
            currentKey=key;pendingSound=false;
            try{seen=new Set(JSON.parse(sessionStorage.getItem(key)||'[]'));}catch{seen=new Set();}
        }
        busy=true;lastRefresh=Date.now();
        try{
            const snapshot=await Server.getPdaProximity(selected.characterId,lobbyId,selected.locationId);
            const now=subject();if(closed||key!==keyFor(now)||now.locationId!==selected.locationId)return;
            const delta=proximityDelta(seen,snapshot);seen=delta.seen;
            try{sessionStorage.setItem(key,JSON.stringify([...seen].slice(-1000)));}catch{}
            if(!(snapshot.devices||[]).some(device=>device.sound&&device.signals?.length))pendingSound=false;
            if(delta.arrivals.length){
                const audible=delta.arrivals.some(entry=>entry.sound);
                showNotification(delta.arrivals.length===1?'КПК: обнаружен незнакомый сигнал':`КПК: обнаружено незнакомых сигналов — ${delta.arrivals.length}`,'system');
                if(audible){pendingSound=true;void testPdaSignal(()=>pendingSound).then(played=>{if(played)pendingSound=false;});}
                window.dispatchEvent(new CustomEvent('pda-proximity-alert',{detail:{count:delta.arrivals.length,sound:audible}}));
            }
        }catch{/* Потеря связи с сервером не создаёт сигналов приближения. */}
        finally{busy=false;}
    }
    const wake=()=>{
        if(closed)return;
        const delay=Math.max(0,1000-(Date.now()-lastRefresh));
        if(delay){queued ||= setTimeout(()=>{queued=null;void refresh();},delay);}else void refresh();
    };
    const unlock=()=>{if(pendingSound)void testPdaSignal(()=>pendingSound).then(played=>{if(played)pendingSound=false;});};
    const events=['pda-network-updated','pda-game-time-updated','character-item-operation-finished','focus'];
    for(const name of events)window.addEventListener(name,wake);
    document.addEventListener('visibilitychange',wake);document.addEventListener('pointerdown',unlock);
    const socketEvents=['character_moved','world_group_updated','character_spawned'];
    for(const name of socketEvents)socket?.on(name,wake);
    timer=setInterval(wake,5000);wake();
    const close=()=>{
        closed=true;clearInterval(timer);clearTimeout(queued);for(const name of events)window.removeEventListener(name,wake);
        document.removeEventListener('visibilitychange',wake);document.removeEventListener('pointerdown',unlock);
        for(const name of socketEvents)socket?.off(name,wake);
        window.removeEventListener('pagehide',close);
    };
    window.addEventListener('pagehide',close,{once:true});
    activeWatcher={refresh,close};return activeWatcher;
}
