// static/js/api.js
import { getErrorMessage } from './utils.js';
import { createCharacterSaver, getCharacterBase, rememberCharacterSnapshots } from './characterPersistence.js';

const token = localStorage.getItem('access_token');

async function requestJson(url, options = {}) {
    const headers = {
        'Authorization': `Bearer ${token}`,
        ...options.headers,
    };
    const response = await fetch(url, { ...options, headers });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const error = new Error(getErrorMessage(data) || `HTTP error ${response.status}`);
        error.status = response.status;
        throw error;
    }
    if (response.status === 204) return null;
    const data = await response.json();
    rememberCharacterSnapshots(data);
    return data;
}

function apiFetch(url,options={}) {
    const match=String(url).match(/^\/lobbies\/(\d+)\/(?:chunks\/(?:batch|\d+\/\d+\/tile\/\d+\/\d+)|world-objects\/edit|world-roads\/(?:build|connect)|water-(?:profile|level))$/);
    return match&&options.method&&options.method!=='GET'&&globalThis.window?.runWorldEditorRequest
        ? window.runWorldEditorRequest(Number(match[1]),()=>requestJson(url,options)) : requestJson(url,options);
}

const characterSaver = createCharacterSaver((characterId, updates) => apiFetch(`/lobbies/characters/${characterId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
}), getCharacterBase);

export const Server = {
    getNpcGenerationConfig: lobbyId => apiFetch(`/lobbies/${lobbyId}/npc-generation-config`),
    getWorldFactions: lobbyId => apiFetch(`/lobbies/${lobbyId}/world-factions`),
    saveWorldFactions: (lobbyId,payload) => apiFetch(`/lobbies/${lobbyId}/world-factions`,{
        method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    assignWorldFaction: (lobbyId,characterId,payload) => apiFetch(`/lobbies/${lobbyId}/world-factions/characters/${characterId}`,{
        method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    saveNpcGenerationConfig: (lobbyId,payload) => apiFetch(`/lobbies/${lobbyId}/npc-generation-config`,{
        method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    previewNpcName: (lobbyId,exclude) => apiFetch(`/lobbies/${lobbyId}/npc-generation-config/name-preview`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exclude})}),
    createGeneratedNpc: (lobbyId,payload) => apiFetch(`/lobbies/${lobbyId}/npc-generation-config/create`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getTimePlans: lobbyId => apiFetch(`/lobbies/${lobbyId}/time-plans`),
    createTimePlanRequest: (lobbyId,payload) => apiFetch(`/lobbies/${lobbyId}/time-plans`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    saveTimePlan: (lobbyId,planId,payload) => apiFetch(`/lobbies/${lobbyId}/time-plans/${planId}`,{
        method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    reviewTimePlan: (lobbyId,planId,payload) => apiFetch(`/lobbies/${lobbyId}/time-plans/${planId}/review`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    rollTimePlan: (lobbyId,planId,payload) => apiFetch(`/lobbies/${lobbyId}/time-plans/${planId}/roll`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getCrafting: characterId => apiFetch(`/lobbies/characters/${characterId}/crafting`),
    getPdaAccount: (characterId,selection={}) => apiFetch(`/lobbies/characters/${characterId}/pda-account?${new URLSearchParams({
        ...(selection.item_path?{item_path:JSON.stringify(selection.item_path)}:{}),...(selection.item_id?{item_id:selection.item_id}:{})})}`),
    getPdaProximity: (characterId,lobbyId,locationId) => apiFetch(`/lobbies/characters/${characterId}/pda-proximity?${new URLSearchParams({
        lobby_id:lobbyId,...(locationId?{location_id:locationId}:{})})}`),
    getPdaAccess: (characterId,selection) => apiFetch(`/lobbies/characters/${characterId}/pda-access?${new URLSearchParams({
        item_path:JSON.stringify(selection.item_path),item_id:selection.item_id,...(selection.session_id?{session_id:selection.session_id}:{})})}`),
    changePdaAccess: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/pda-access`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getControllers: (characterId,selection={}) => apiFetch(`/lobbies/characters/${characterId}/controllers?${new URLSearchParams({
        ...(selection.item_path?{item_path:JSON.stringify(selection.item_path)}:{}),...(selection.item_id?{item_id:selection.item_id}:{}),
        ...(selection.location_id?{location_id:selection.location_id}:{})})}`),
    operateController: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/controllers`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    activateArtifactSpecial: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/artifact-special`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getDetonations: lobbyId => apiFetch(`/lobbies/${lobbyId}/detonations`),
    resolveDetonation: (lobbyId,eventId,payload) => apiFetch(`/lobbies/${lobbyId}/detonations/${encodeURIComponent(eventId)}`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    changePdaAccount: (characterId, operation, payload = {}) => apiFetch(`/lobbies/characters/${characterId}/pda-account/${operation}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }),
    craft: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/crafting`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getCraftingRequests: lobbyId => apiFetch(`/lobbies/${lobbyId}/crafting-requests`),
    sterilizeGloves: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/sterilize-gloves`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    useSparePlates: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/spare-plates`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    rechargeLiquidCrystal: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/liquid-crystal-recharge`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    rechargeNeckBrace: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/neck-brace-recharge`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getActionJournal: lobbyId => apiFetch(`/lobbies/${lobbyId}/action-journal`),
    changeWeaponPair: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/weapon-pair`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getPortableLights: characterId => apiFetch(`/lobbies/characters/${characterId}/portable-lights`),
    getPdaNetwork: (characterId,selection) => apiFetch(`/lobbies/characters/${characterId}/pda-network?${new URLSearchParams({item_path:JSON.stringify(selection.item_path),
        ...(selection.item_id?{item_id:selection.item_id}:{}),...(selection.location_id?{location_id:selection.location_id}:{})})}`),
    changePdaNetwork: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/pda-network`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getRadiationAccess: (lobbyId,characterId,locationId) => apiFetch(`/lobbies/${lobbyId}/radiation-access?character_id=${encodeURIComponent(characterId)}${locationId?'&location_id='+encodeURIComponent(locationId):''}`),
    operateProsthesis: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/prostheses`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    operatePortableLight: (characterId,payload) => apiFetch(`/lobbies/characters/${characterId}/portable-lights`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),
    getExtinguishingOptions: (lobbyId,locationId,actorId) => apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/extinguishing-options/${actorId}`),
    async awardCharacterSession(lobbyId,characterId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/session-reward`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    },
    async characterCreationRules(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/character-creation`);
    },
    async previewCharacterCreation(lobbyId,name,plan,options={}) {
        return apiFetch(`/lobbies/${lobbyId}/character-creation`,{...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,plan})});
    },
    async settleCharacterHistory(lobbyId,characterId,choices){
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/creation-history`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({choices})});
    },
    async confirmCharacterHistoryMonth(lobbyId,characterId,month,choice){
        return apiFetch('/lobbies/'+lobbyId+'/characters/'+characterId+'/creation-history',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({month,choice})});
    },
    async applyWorldEditorChange(lobbyId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-editor/changes`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    },
    async setWorldWaterProfile(lobbyId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/water-profile`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    },
    async setWorldWaterLevel(lobbyId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/water-level`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    },
    async prepareDeferredConsumable(lobbyId, locationId, actionId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/deferred-consumable/${encodeURIComponent(actionId)}`);
    },
    async loadAmmunition(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/ammunition`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },

    async installWeaponMagazine(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/magazine`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    async cancelDeferredCombatAction(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/deferred-action/cancel`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    isCharacterSaving: characterSaver.isSaving,
    isOwnCharacterSave: characterSaver.isOwnSave,
    waitForCharacterSave: characterSaver.whenIdle,

    characterUpdatePayload(characterId, data) {
        return { data, _base_data: getCharacterBase(characterId, data) };
    },

    async transferLocationItem(lobbyId, objectId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/objects/${objectId}/transfer`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },

    async dropLocationItem(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/drop-item`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },

    async operateStation(lobbyId,locationId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/stations`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
    },
    async operateCraftedCover(lobbyId,locationId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/crafted-covers`,{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    },
    async operateTrap(lobbyId,locationId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/traps`,{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    },

    async finishSheetTreatment(targetId, payload) {
        return apiFetch(`/lobbies/characters/${targetId}/treatment`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    async finishConsumableUse(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/consumable`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    async applyMedicalProcedure(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/medical-procedure`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    async operateAutoInjector(characterId,payload) {
        return apiFetch(`/lobbies/characters/${characterId}/auto-injector`,{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
    },
    async operateBeltAttachment(characterId,payload) {
        return apiFetch(`/lobbies/characters/${characterId}/belt-attachment`,{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
        });
    },
    async getLocationObject(lobbyId,objectId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/objects/${objectId}`);
    },
    async attemptObjectLock(lobbyId,objectId,payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/objects/${objectId}/lock`,{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
    },
    async performSocialCheck(characterId,payload) {
        return apiFetch(`/lobbies/characters/${characterId}/social-check`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
    },
    async inspectTrueSight(lobbyId,locationId,characterId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/true-sight/${characterId}`);
    },
    async getLocationScoutPoint(characterId){return apiFetch(`/lobbies/characters/${characterId}/location-scout`);},
    async setLocationScoutPoint(characterId,point){return apiFetch(`/lobbies/characters/${characterId}/location-scout`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(point)});},
    async scoutFromPoint(characterId,instrument){return apiFetch(`/lobbies/characters/${characterId}/location-scout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(instrument)});},
    async performSkillCheck(characterId,payload) {
        return apiFetch(`/lobbies/characters/${characterId}/skill-check`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
    },
    async applyGeneralConsumable(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/general-consumable`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    // ----- Лобби (комнаты) -----
    async getLobbyInfo(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}`);
    },

    async updateLobbyTime(lobbyId, gameDay, gameTimeMinutes) {
        return apiFetch(`/lobbies/${lobbyId}/time`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game_day: gameDay, game_time_minutes: gameTimeMinutes }),
        });
    },

    async startLobbyRest(lobbyId, type, characterIds) {
        return apiFetch(`/lobbies/${lobbyId}/rest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type, character_ids: characterIds }),
        });
    },

    async updateTimeActiveCharacters(lobbyId, characterIds) {
        return apiFetch(`/lobbies/${lobbyId}/characters/time-active`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ character_ids: characterIds }),
        });
    },

    async repairCharacterEquipment(characterId, toolPath, targetPath) {
        return apiFetch(`/lobbies/characters/${characterId}/repair-equipment`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool_path: toolPath, target_path: targetPath }),
        });
    },

    async getWorldGroups(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups`);
    },

    async getWorldRules(lobbyId, options = {}) {
        return apiFetch(`/lobbies/${lobbyId}/world-rules`, options);
    },

    async createMutant(lobbyId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/mutants`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async useWorldAnomalyField(lobbyId, groupId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups/${groupId}/anomaly-field`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async createWorldGroup(lobbyId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async moveWorldGroup(lobbyId, groupId, tileX, tileY) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups/${groupId}/move`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tile_x: tileX, tile_y: tileY }),
        });
    },

    async waitWorldGroup(lobbyId, groupId) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups/${groupId}/wait`, {
            method: 'POST',
        });
    },

    async updateWorldGroupTurnActivity(lobbyId, groupId, active) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups/${groupId}/turn-active`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active }),
        });
    },

    async updateWorldGroupMembers(lobbyId, groupId, characterIds) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups/${groupId}/members`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ character_ids: characterIds }),
        });
    },

    async createWorldMapEvent(lobbyId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-map-events`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async deleteWorldMapEvent(lobbyId, eventId) {
        return apiFetch(`/lobbies/${lobbyId}/world-map-events/${eventId}`, {
            method: 'DELETE',
        });
    },

    async deleteWorldGroup(lobbyId, groupId) {
        return apiFetch(`/lobbies/${lobbyId}/world-groups/${groupId}`, {
            method: 'DELETE',
        });
    },

    async resolveWorldTravelEvent(lobbyId, eventId, decision) {
        return apiFetch(`/lobbies/${lobbyId}/world-events/${eventId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision }),
        });
    },

    async createLobby(name, mapType, chunksWidth, chunksHeight, importData = null, factionSource = 'custom') {
        // Для импорта используется FormData, для обычного — JSON
        if (mapType === 'imported' && importData) {
            const formData = new FormData();
            formData.append('name', name);
            formData.append('map_type', 'imported');
            formData.append('faction_source', factionSource);
            formData.append('map_file', new Blob([JSON.stringify(importData)], { type: 'application/json' }), 'map.json');
            const response = await fetch('/lobbies/', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: formData,
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(getErrorMessage(data) || 'Failed to create lobby');
            }
            return response.json();
        } else {
            return apiFetch('/lobbies/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, map_type: mapType, chunks_width: chunksWidth,
                    chunks_height: chunksHeight, faction_source: factionSource }),
            });
        }
    },

    async listLobbies() {
        return apiFetch('/lobbies/');
    },

    async joinLobby(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/join`, { method: 'POST' });
    },

    async leaveLobby(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/leave`, { method: 'POST' });
    },

    async deleteLobby(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}`, { method: 'DELETE' });
    },

    async joinByCode(code) {
        return apiFetch('/lobbies/join_by_code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
    },

    async getMyLobbies(limit, offset) {
        const params = new URLSearchParams();
        if (limit !== undefined) params.set('limit', limit);
        if (offset !== undefined) params.set('offset', offset);
        return apiFetch(`/lobbies/my?${params}`);
    },

    async getJoinedLobbies(limit, offset) {
        const params = new URLSearchParams();
        if (limit !== undefined) params.set('limit', limit);
        if (offset !== undefined) params.set('offset', offset);
        return apiFetch(`/lobbies/joined?${params}`);
    },

    // ----- Участники и баны -----
    async banUser(lobbyId, userId) {
        return apiFetch(`/lobbies/${lobbyId}/ban/${userId}`, { method: 'POST' });
    },

    async unbanUser(lobbyId, userId) {
        return apiFetch(`/lobbies/${lobbyId}/unban/${userId}`, { method: 'POST' });
    },

    async getBannedList(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/banned`);
    },

    // ----- Персонажи -----
    async getLobbyCharacters(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/characters`);
    },

    async createLobbyCharacter(lobbyId, name, data = {}) {
        return apiFetch(`/lobbies/${lobbyId}/characters`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, data }),
        });
    },

    async getCharacter(characterId) {
        return apiFetch(`/lobbies/characters/${characterId}`);
    },

    async updateCharacter(characterId, updates) {
        return characterSaver.save(characterId, updates);
    },

    async deleteCharacter(characterId) {
        return apiFetch(`/lobbies/characters/${characterId}`, { method: 'DELETE' });
    },

    async setCharacterVisibility(characterId, visibleTo, editableTo = []) {
        return apiFetch(`/lobbies/characters/${characterId}/visibility`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ visible_to: visibleTo, editable_to: editableTo }),
        });
    },

    // ----- Карта -----
    async getChunks(lobbyId, minX, maxX, minY, maxY, options = {}) {
        return apiFetch(`/lobbies/${lobbyId}/chunks?min_chunk_x=${minX}&max_chunk_x=${maxX}&min_chunk_y=${minY}&max_chunk_y=${maxY}`, options);
    },

    async updateTile(lobbyId, chunkX, chunkY, tileX, tileY, updates) {
        return apiFetch(`/lobbies/${lobbyId}/chunks/${chunkX}/${chunkY}/tile/${tileX}/${tileY}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates),
        });
    },

    async batchUpdateTiles(lobbyId, updates) {
        return apiFetch(`/lobbies/${lobbyId}/chunks/batch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates),
        });
    },

    async previewWorldRoad(lobbyId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-roads/preview`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    async buildWorldRoad(lobbyId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-roads/build`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
    async connectWorldRoads(lobbyId, first, second) {
        return apiFetch(`/lobbies/${lobbyId}/world-roads/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ first, second }),
        });
    },

    async editWorldObject(lobbyId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/world-objects/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async exportMap(lobbyId) {
        const response = await fetch(`/lobbies/${lobbyId}/export`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(getErrorMessage(data) || 'Export failed');
        }
        return response.blob();
    },

    // ----- Погода -----
    async updateWeather(lobbyId, settings) {
        return apiFetch(`/lobbies/${lobbyId}/weather`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });
    },

    // ----- Шаблоны предметов -----
    async getLobbyTemplates(lobbyId, category, subcategory = null) {
        const params = new URLSearchParams({ category });
        if (subcategory) params.set('subcategory', subcategory);
        return apiFetch(`/lobbies/${lobbyId}/templates?${params}`);
    },

    async createLobbyTemplate(lobbyId, templateData) {
        return apiFetch(`/lobbies/${lobbyId}/templates`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(templateData),
        });
    },

    async updateLobbyTemplate(lobbyId, templateId, templateData) {
        return apiFetch(`/lobbies/${lobbyId}/templates/${templateId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(templateData),
        });
    },

    async deleteLobbyTemplate(lobbyId, templateId) {
        const token = localStorage.getItem('access_token');
        const response = await fetch(`/lobbies/${lobbyId}/templates/${templateId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(getErrorMessage(data) || `HTTP error ${response.status}`);
        }
        // При успехе возвращаем true или ничего, т.к. статус 204 No Content
        return true;
    },

    // ----- Аутентификация (если нужно) -----
    async register(username, email, password) {
        return apiFetch('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, email, password }),
        });
    },

    async login(username, password) {
        return apiFetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
    },

    async getProfile() {
        return apiFetch('/auth/profile');
    },

    async createLocation(lobbyId, locationData) {
        return apiFetch(`/lobbies/${lobbyId}/locations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(locationData)
        });
    },

    async getLocations(lobbyId) {
        return apiFetch(`/lobbies/${lobbyId}/locations`);
    },

    async getLocationDetail(lobbyId, locationId, options = {}) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}`, options);
    },

    async updateLocation(lobbyId, locationId, updates) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        });
    },

    async deleteLocation(lobbyId, locationId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}`, { method: 'DELETE' });
    },

    async changeCharacterEquipment(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/equipment-action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async changeCharacterEquipmentModule(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/equipment-module`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async registerCharacterAddictionExposure(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/addictions/exposure`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async checkCharacterWithdrawal(characterId, addictionKey) {
        return apiFetch(`/lobbies/characters/${characterId}/addictions/${encodeURIComponent(addictionKey)}/check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
    },

    async inspectLocationCharacter(lobbyId, locationId, characterId, actorLocationCharacterId) {
        const params = new URLSearchParams({
            actor_location_character_id: actorLocationCharacterId,
        });
        return apiFetch(
            `/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/interaction?${params}`
        );
    },

    async transportLocationBody(lobbyId, locationId, characterId, actorLocationCharacterId, mode) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/body-transport`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({actor_location_character_id: actorLocationCharacterId, mode}),
        });
    },

    async setLocationCharacterHidden(lobbyId, locationId, characterId, gmHidden) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/gm-hidden`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gm_hidden: gmHidden }),
        });
    },

    async lootLocationCharacter(lobbyId, locationId, characterId, payload) {
        return apiFetch(
            `/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/loot`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }
        );
    },

    async butcherLocationMutant(lobbyId, locationId, characterId, payload) {
        return apiFetch(
            `/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/butchering`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }
        );
    },

    async treatLocationCharacter(lobbyId, locationId, characterId, payload) {
        return apiFetch(
            `/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/treatment`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }
        );
    },

    async npcLootPool(lobbyId, characterId, payload, generate = false) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/loot-pool`, {
            method: generate ? 'POST' : 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
        });
    },

    async configureNpcMerchant(lobbyId, characterId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/merchant`, {
            method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
        });
    },

    async professionalRepair(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/professional-repair`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
    },
    async professionalModification(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/professional-modification`, {
            method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
        });
    },

    async createCharacterInteraction(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/character-interactions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async respondCharacterInteraction(lobbyId, requestId, decision) {
        return apiFetch(`/lobbies/${lobbyId}/character-interactions/${requestId}/response`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision }),
        });
    },

    async cancelCharacterInteraction(lobbyId, requestId) {
        return apiFetch(`/lobbies/${lobbyId}/character-interactions/${requestId}`, {
            method: 'DELETE',
        });
    },

    async startCharacterTreatment(lobbyId, requestId, pendingActionId = null) {
        return apiFetch(`/lobbies/${lobbyId}/character-interactions/${requestId}/progress`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pending_action_id: pendingActionId }),
        });
    },

    async editLocationObjects(lobbyId, locationId, change) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/object-edits`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change),
        });
    },
    async createLocationObject(lobbyId, locationId, objectData) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/objects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(objectData)
        });
    },

    async changeLocationObjects(lobbyId, locationId, data, method = 'POST') {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/objects/batch`, {
            method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
        });
    },

    async deleteLocationObject(lobbyId, objectId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/objects/${objectId}`, {
            method: 'DELETE'
        });
    },

    async updateLocationObject(lobbyId, objectId, updates) {
        return apiFetch(`/lobbies/${lobbyId}/locations/objects/${objectId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        });
    },

    async getLocationCombatState(lobbyId, locationId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat`);
    },

    async getLocationTeams(lobbyId, locationId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/teams`);
    },

    async updateLocationTeams(lobbyId, locationId, assignments) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/teams`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assignments }),
        });
    },

    async startLocationCombat(
        lobbyId,
        locationId,
        locationCharacterIds = null,
        initiatorLocationCharacterId = null,
        initiatorFirst = true,
    ) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                location_character_ids: locationCharacterIds,
                initiator_location_character_id: initiatorLocationCharacterId,
                initiator_first: initiatorFirst,
            }),
        });
    },

    async requestLocationCombatStart(lobbyId, locationId, actorLocationCharacterId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/start-requests`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ actor_location_character_id: actorLocationCharacterId }),
        });
    },

    async getLocationCombatStartRequests(lobbyId, locationId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/start-requests`);
    },

    async respondLocationCombatStartRequest(
        lobbyId, locationId, requestId, decision, locationCharacterIds = null, initiatorFirst = true,
    ) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/start-requests/${requestId}/response`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                decision,
                location_character_ids: locationCharacterIds,
                initiator_first: initiatorFirst,
            }),
        });
    },

    async endLocationCombat(lobbyId, locationId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/end`, {
            method: 'POST',
        });
    },

    async endLocationCombatTurn(lobbyId, locationId, locationCharacterId = null) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/end_turn`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ location_character_id: locationCharacterId }),
        });
    },

    async decideExplosionTarget(lobbyId, locationId, eventId, actorId, choice) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/explosions/${eventId}/targets/${actorId}`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({choice}),
        });
    },

    async removeLocationCombatParticipant(lobbyId, locationId, locationCharacterId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/participants/${locationCharacterId}`, {
            method: 'DELETE',
        });
    },

    async applyLocationGmEvent(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/gm-events`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async resolveLocationStressEffect(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/stress-effects/resolve`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },

    async adjustLocationCharacterStress(lobbyId, locationId, characterId, amount) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/characters/${characterId}/stress`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount }),
        });
    },

    async adjustCharacterStress(lobbyId, characterId, amount) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/stress`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount }),
        });
    },

    async healCharacterFully(lobbyId, characterId) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/heal`, {
            method: 'POST',
        });
    },

    async restorePlayerCharacter(lobbyId, characterId) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/restore-player`, {method:'POST'});
    },

    async removeCharacterAddiction(lobbyId, characterId, addictionKey) {
        return apiFetch(`/lobbies/${lobbyId}/characters/${characterId}/addictions/${encodeURIComponent(addictionKey)}`, {
            method: 'DELETE',
        });
    },

    async addLocationCombatParticipant(lobbyId, locationId, locationCharacterId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/participants/${locationCharacterId}`, {
            method: 'POST',
        });
    },

    async reserveLocationCombatReaction(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/reaction/reserve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async requestLocationCombatReaction(lobbyId, locationId, locationCharacterId) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/reaction/request`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ location_character_id: locationCharacterId }),
        });
    },

    async resolveLocationCombatReaction(lobbyId, locationId, approve) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/reaction/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approve }),
        });
    },

    async spendLocationCombatResources(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/spend`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async resolveLocationOpportunityAttack(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/opportunity-attack`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async adjustLocationCombatResources(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/adjust`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async performLocationCombatAction(lobbyId, locationId, payload) {
        return apiFetch(`/lobbies/${lobbyId}/locations/${locationId}/combat/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    },

    async changeWeaponGrip(characterId, payload) {
        return apiFetch(`/lobbies/characters/${characterId}/weapon-grip`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
    },
};
