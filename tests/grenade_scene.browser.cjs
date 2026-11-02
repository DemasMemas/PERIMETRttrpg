// Та же геометрия полигона в изолированной БД, обычная камера и настоящий GPU.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    const root=path.resolve(__dirname,'..');
    const layout=JSON.parse(execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-I','-B','-c',
        'import sys,json;sys.path.insert(0,sys.argv[1]);from scripts.seed_grenade_test_location import scene_layout,SCENE_NAME,DESCRIPTION;tiles,objects=scene_layout();print(json.dumps(dict(name=SCENE_NAME,description=DESCRIPTION,tiles_data=tiles,objects=objects)))',root],
        {cwd:root,windowsHide:true,encoding:'utf8',maxBuffer:8*1024*1024}));
    const loc=await request(`/lobbies/${lobby.id}/locations`,{...layout,objects:undefined,type:'battle',
        grid_width:96,grid_height:96,world_tile_x:9,world_tile_z:9,spawn_points:[{x:80,y:72}],
        lighting:{mode:'manual',ambient_intensity:.65,directional_intensity:.8,directional_elevation:58,directional_azimuth:50}},token);
    await request(`/lobbies/${lobby.id}/locations/${loc.id}/objects/batch`,{objects:layout.objects},token);
    await page.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
    await page.until('typeof enterLocation==="function"&&window.socket?.connected','комната готова для полигона');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${loc.id})})()`);
    await page.until('window.locationControls&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','полигон загружен');
    await page.evaluate('L.setLocationEditMode(false);locationControls.enableDamping=false;locationControls.target.set(48.5,1,48.5);locationControls.object.position.set(70,85,115);locationControls.update()');
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    fs.writeFileSync(path.join(safeSession.artifacts,'grenade-scene-overview.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await page.evaluate('locationControls.target.set(80.5,2,48.5);locationControls.object.position.set(89,15,63);locationControls.update();L.setLocationViewLevel(1)');
    const roof=await page.evaluate('(()=>{const r=document.getElementById("location-roof-btn").getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()');
    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...roof,button:'left',buttons:1,clickCount:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...roof,button:'left',buttons:0,clickCount:1});
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    assert.equal(await page.evaluate('L.getLocationViewLevel()'),1);
    fs.writeFileSync(path.join(safeSession.artifacts,'grenade-scene-storeys.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Полигон гранат: геометрия новой сцены загружена, общая камера и второй этаж сняты для проверки.');
};
