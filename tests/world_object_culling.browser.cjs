const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async ({ page, request, token, lobby, safeSession }) => {
    await page.evaluate(`(async()=>{
        window.cullingWorld=await import('/static/js/lobby3d.js');
        window.cullingThree=await import('three');
    })()`);
    const updates = [];
    for (let y = 0; y < 32; y++) {
        for (let x = 0; x < 32; x++) {
            updates.push({ chunk_x: 0, chunk_y: 0, tile_x: x, tile_y: y,
                updates: { terrain: 'grass', height: 1,
                    objects: x === 31 && y === 16 ? [{ type: 'tree', scale: 1 }] : [] } });
        }
    }
    await request(`/lobbies/${lobby.id}/chunks/batch`, updates, token);
    await page.until("cullingWorld.chunksMap.get('0,0')?.trees?.count===1", 'Дерево размещено');
    await page.evaluate(`(()=>{
        const {controls,camera}=cullingWorld;
        controls.enableDamping=false;
        controls.target.set(31.5,1,16.5);
        camera.position.set(31.5,8,24);
        controls.update();
    })()`);
    await page.until("cullingWorld.chunksMap.get('0,0')?.ground?.visible===true", 'Земля в кадре');
    await page.until("!!cullingWorld.chunksMap.get('0,0')?.groundSides", 'Внешние стенки грунта построены');
    const groundBefore = await page.evaluate(`(()=>{
        const {ground,groundSides}=cullingWorld.chunksMap.get('0,0');
        return {geometry:groundSides.geometry.uuid,hidden:!ground.material.visible,
            triangles:groundSides.geometry.attributes.position.count/3};
    })()`);
    assert.equal(groundBefore.hidden, true);
    assert.ok(groundBefore.triangles <= 256, 'Внутренние стенки плоского чанка не отправляются на GPU');
    await request(`/lobbies/${lobby.id}/chunks/batch`, [{ chunk_x: 0, chunk_y: 0, tile_x: 2, tile_y: 2,
        updates: { height: 2 } }], token);
    await page.until(`cullingWorld.chunksMap.get('0,0')?.groundSides?.geometry.uuid!==${JSON.stringify(groundBefore.geometry)}`,
        'Геометрия стенок обновилась после изменения высоты');
    const picked = await page.evaluate(`(()=>{
        const T=cullingThree,{ground,groundIndices,groundSides}=cullingWorld.chunksMap.get('0,0');
        const ray=new T.Raycaster(new T.Vector3(2.5,10,2.5),new T.Vector3(0,-1,0));
        const hits=ray.intersectObject(ground,false);
        return {hit:hits[0]?.instanceId,index:groundIndices[2*32+2],duplicates:ray.intersectObject(groundSides,false).length};
    })()`);
    assert.equal(picked.hit,picked.index,'Полные клетки сохраняют выбор после изменения высоты');
    assert.equal(picked.duplicates,0);
    await request(`/lobbies/${lobby.id}/chunks/batch`, [{ chunk_x: 0, chunk_y: 0, tile_x: 31, tile_y: 16,
        updates: { objects: [{ type: 'tree', scale: 8 }] } }], token);
    await page.until("cullingWorld.chunksMap.get('0,0').tilesData[16][31].objects[0].scale===8", 'Масштаб изменён редактором');
    await page.evaluate(`(()=>{
        const {controls,camera}=cullingWorld;
        controls.target.set(31.5,7,16.5);
        camera.position.set(26.5,7.5,16.5);
        controls.update();
    })()`);
    await page.until("cullingWorld.chunksMap.get('0,0')?.ground?.visible===false", 'Границы кадра обновлены');
    const result = await page.evaluate(`(()=>{
        const W=cullingWorld,T=cullingThree,chunk=W.chunksMap.get('0,0'),mesh=chunk.trees;
        const frustum=new T.Frustum().setFromProjectionMatrix(
            new T.Matrix4().multiplyMatrices(W.camera.projectionMatrix,W.camera.matrixWorldInverse));
        return {
            terrainInFrustum:frustum.intersectsBox(chunk.bounds),
            objectInFrustum:frustum.intersectsBox(mesh.boundingBox),
            chunkVisible:chunk.terrainVisible,treeVisible:mesh.visible,groundVisible:chunk.ground.visible,
            detailedTree:W.scene.children.some(m=>m.visible&&m.userData.worldTreeBatch?.detail==='full'),
            pickingMeshDrawn:mesh.material.visible,
            objectBounds:chunk.objectBounds,sphere:mesh.boundingSphere,
            glError:W.renderer.getContext().getError(),
        };
    })()`);
    assert.equal(result.terrainInFrustum, false);
    assert.equal(result.objectInFrustum, true);
    assert.equal(result.chunkVisible, true, 'Верх дерева остаётся видимым при выходе земли за край кадра');
    assert.equal(result.treeVisible, true);
    assert.equal(result.groundVisible, false, 'Земля за кадром сохраняет отсечение');
    assert.equal(result.detailedTree, true, 'Крупное дерево рисуется подробной моделью');
    assert.equal(result.pickingMeshDrawn, false, 'Редактор сохраняет исходный индекс дерева без повторной отрисовки');
    assert.equal(result.glError, 0);
    fs.writeFileSync(path.join(safeSession.artifacts, 'world-object-culling.json'), JSON.stringify(result, null, 2));
    console.log('PASS: границы дерева обновлены после редактора, верх виден, земля за кадром отсечена');
};
