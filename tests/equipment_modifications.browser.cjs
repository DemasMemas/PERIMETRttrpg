// Короткая проверка специальных модификаций без повторения всех настольных форм.
const assert=require('node:assert/strict');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.ok(['equipment','lights','effects'].includes(process.env.VTT_SAFE_SCENARIO));assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const npc=await request(api+'/characters',{name:'Мастер снаряжения',data:{is_npc:true,inventory:{money:0}}},token);
    await request(api+'/characters/'+npc.id+'/merchant',{character_revision:npc.data._revision,enabled:false,
        repair_enabled:true,modification_enabled:true,workbenches:['Станок Бронника'],prices:{}},token,'PATCH');
    await request(loc+'/spawn_character',{character_id:npc.id,tile_x:46,tile_y:49},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Снаряжение на копии Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять готова');
    const offered=await request(api+'/templates',null,token,'GET');
    if(process.env.VTT_SAFE_SCENARIO==='lights'){
        await require('./flashlight_nearfield.browser.cjs')({page,request,token,lobby,offered,loc,safeSession});return;
    }
    if(process.env.VTT_SAFE_SCENARIO==='effects'){
        await require('./water_component.browser.cjs')({page,request,token,lobby,offered,loc,safeSession});
        await require('./pepper_playtest.browser.cjs')({page,request,token,lobby,loc,safeSession});
        return;
    }
    await require('./spare_plates.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./medical_pouches.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./armor_plate_mounts.browser.cjs')({page,request,token,lobby,offered,loc,safeSession});
    await require('./exoskeleton_power_station.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./belt_devices.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./belt_quick_mounts.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./belt_light.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./belt_attachments.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./belt_artifact.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
    await require('./review_screen.browser.cjs')({page,safeSession});
};
