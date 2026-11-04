const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

test('Именованные импорты листа разрешаются настоящими экспортами без выполнения игрового кода',async()=>{
    const context=vm.createContext({}),cache=new Map();
    function imports(source){
        return [...source.matchAll(/(?:import|export)\s*(?:[A-Za-z_$][\w$]*\s*,\s*)?\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]/g)]
            .map(match=>({specifier:match[2],names:match[1].replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g,'').split(',').map(text=>text.trim().split(/\s+as\s+/)[0]).filter(Boolean)}));
    }
    async function exportsOf(file){
        if(cache.has(file))return cache.get(file);
        const source=fs.readFileSync(file,'utf8'),module=new vm.SourceTextModule(source,{context,identifier:file});
        const names=[...new Set(['default',...imports(source).flatMap(item=>item.names)])];
        await module.link(()=>new vm.SyntheticModule(names,()=>{},{context}));
        const result={names:new Set(Object.getOwnPropertyNames(module.namespace)),source};cache.set(file,result);return result;
    }
    const visited=new Set();
    async function verify(file){
        if(visited.has(file))return;visited.add(file);
        const actual=await exportsOf(file);
        for(const entry of imports(actual.source)){
            if(!entry.specifier.startsWith('.'))continue;
            const dependency=path.resolve(path.dirname(file),entry.specifier),provided=await exportsOf(dependency);
            for(const name of entry.names)assert.ok(provided.names.has(name),`${path.basename(file)}: ${name} отсутствует в ${entry.specifier}`);
            await verify(dependency);
        }
    }
    await verify(path.resolve(__dirname,'../app/static/js/characterSheet.js'));
    assert.ok(visited.size>10);
});
