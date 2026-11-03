const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('secondary actions wait for short release, never fire on native menu, drag, hold or cancellation',()=>{
    const listeners=new Map(),canvasListeners=new Map(),events=[];
    const target=map=>({addEventListener(type,fn){map.set(type,fn);},removeEventListener(type){map.delete(type);}});
    const canvas={...target(canvasListeners),contains:node=>node===canvas,dispatchEvent:event=>events.push(event)};
    const ctx=vm.createContext({window:target(listeners),MouseEvent:class{constructor(type,options){Object.assign(this,{type},options);}},Date});
    const source=fs.readFileSync('app/static/js/locationEditorPointer.js','utf8').replaceAll('export ','');
    vm.runInContext(source,ctx);const cleanup=ctx.installSecondaryClick(canvas);
    const event=(timeStamp,extra={})=>({target:canvas,button:2,pointerId:1,clientX:10,clientY:10,timeStamp,...extra});
    listeners.get('pointerdown')(event(0));let prevented=false;listeners.get('contextmenu')({target:canvas,preventDefault(){prevented=true;}});
    assert.equal(prevented,true);assert.equal(events.length,0);
    listeners.get('pointermove')(event(20,{clientX:20}));listeners.get('pointerup')(event(50));assert.equal(events.length,0);
    listeners.get('pointerdown')(event(100));listeners.get('pointerup')(event(500));assert.equal(events.length,0);
    listeners.get('pointerdown')(event(1000));listeners.get('pointerup')(event(1050));assert.equal(events.length,1);assert.equal(events[0].type,'secondaryclick');
    listeners.get('pointerdown')(event(2000));listeners.get('pointercancel')();listeners.get('pointerup')(event(2050));assert.equal(events.length,1);
    cleanup();assert.equal(listeners.size,0);assert.equal(canvasListeners.size,0);
});
