import {Server} from './api.js';

let currentKey='',allowed=false,pending=false,generation=0,expires=0;
export function invalidateRadiationReadings(){generation++;currentKey='';allowed=false;pending=false;expires=0;}
export function radiationTooltipLine(value){
    if(window.isGM&&!window.locationScoutView)return `Радиация: ${Number(value)||0}<br>`;
    const characterId=window.isLocationActive?(window.currentLocationCharacterId||window.currentCharacterId):window.currentCharacterId;
    const locationId=window.isLocationActive?window.currentLocationId:null;
    const lobbyId=window.currentLobbyId,key=`${lobbyId}:${locationId}:${characterId}`;
    if(key!==currentKey){invalidateRadiationReadings();currentKey=key;}
    if(Date.now()>expires&&!pending&&lobbyId&&characterId){
        pending=true;const requestGeneration=generation;
        Server.getRadiationAccess(lobbyId,characterId,locationId).then(result=>{
            if(requestGeneration===generation){allowed=result.can_read===true;expires=Date.now()+5000;}
        }).catch(()=>{if(requestGeneration===generation){allowed=false;expires=Date.now()+5000;}})
            .finally(()=>{if(requestGeneration===generation)pending=false;});
    }
    return allowed?`Радиация: ${Number(value)||0}<br>`:'';
}
window.addEventListener('pda-game-time-updated',invalidateRadiationReadings);
window.addEventListener('character-item-operation-finished',invalidateRadiationReadings);
