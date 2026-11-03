// Одинаковые камеры и сохранённая копия Припяти; только собственный Chrome.
const fs=require('node:fs'),path=require('node:path');
module.exports=async({page,safeSession,boars})=>{
    const result=await page.evaluate(`(async()=>{
        const g=L.sceneGraphicsForTest(),models=${JSON.stringify(boars.map(b=>b.id))}.map(id=>g.characters.get(id).model),visible=models.map(m=>m.visible);
        const stats=values=>{if(!values.length)return null;values.sort((a,b)=>a-b);return{median:values[Math.floor(values.length*.5)],p95:values[Math.floor(values.length*.95)],mean:values.reduce((a,b)=>a+b,0)/values.length}};
        const wait=()=>new Promise(requestAnimationFrame),output=[];
        const gl=g.renderer.getContext(),ext=g.renderer.capabilities.isWebGL2&&gl.getExtension('EXT_disjoint_timer_query_webgl2');
        try{for(const view of [{name:'close',position:[46,5,50],target:[42,1.5,44.5]},{name:'tactical',position:[50,14,58],target:[42,1,44.5]}]){
            g.controls.target.set(...view.target);g.camera.position.set(...view.position);g.controls.update();
            for(const count of [0,1,2,0]){
                const setVisibility=()=>models.forEach((m,i)=>m.visible=i<count);
                for(let i=0;i<35;i++){setVisibility();await wait();}
                const frames=[],cpu=[],gpu=[],queries=[],calls=[],triangles=[];let last;
                const render=g.renderer.render;
                g.renderer.render=function(...args){setVisibility();const at=performance.now(),q=ext&&gl.createQuery();if(q)gl.beginQuery(ext.TIME_ELAPSED_EXT,q);
                    try{return render.apply(this,args)}finally{if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);queries.push(q);}cpu.push(performance.now()-at);calls.push(this.info.render.calls);triangles.push(this.info.render.triangles)}};
                try{for(let i=0;i<150;i++)await new Promise(resolve=>requestAnimationFrame(t=>{if(last)frames.push(t-last);last=t;resolve()}));}
                finally{g.renderer.render=render;}
                if(ext){for(let i=0;i<10;i++)await wait();for(const q of queries){if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)&&!gl.getParameter(ext.GPU_DISJOINT_EXT))gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q);}}
                output.push({view:view.name,count,frameMs:stats(frames),renderCpuMs:stats(cpu),gpuMs:stats(gpu),drawCalls:stats(calls),triangles:stats(triangles)});
            }
        }}finally{models.forEach((m,i)=>m.visible=visible[i]);}
        return output;
    })()`);
    fs.writeFileSync(path.join(safeSession.artifacts,'boar-performance.json'),JSON.stringify(result,null,2));
    console.log('BOAR_PERFORMANCE '+JSON.stringify(result));
};
