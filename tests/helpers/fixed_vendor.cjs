// Фиксированный cache: только проверенные байты пяти существующих файлов.
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const VENDOR_ROOT=path.resolve(__dirname,'../../tmp/browser-vendor');
const MANIFEST=Object.freeze([
 ['https://cdnjs.cloudflare.com','/ajax/libs/socket.io/4.0.1/socket.io.js','socket.io.js','CBA65C55EC52D8013FE72905CB586ABA2F285C71E4297935083138C78DD0B03B'],
 ['https://unpkg.com','/three@0.128.0/build/three.module.js','three.module.js','AF527C374B56B8688737A42D7FCEA7CB8AAEB57A4E3C6DA98B4DFFD55BCC3514'],
 ['https://unpkg.com','/three@0.128.0/examples/jsm/controls/OrbitControls.js','OrbitControls.js','41E4A12F6DBDD54B1693322343899F5007BBC7B50768DC0DA8800237EA112317'],
 ['https://unpkg.com','/three@0.128.0/examples/jsm/renderers/CSS2DRenderer.js','CSS2DRenderer.js','465AE66889F6365EF51A31DE6B2712D876882C30E8D891B00A9F11EA2652B700'],
 ['https://unpkg.com','/three@0.128.0/examples/jsm/loaders/GLTFLoader.js','GLTFLoader.js','943A0D68F63C3F7FB04398D8B125CDC09C1A05AF9D5F40B68A2E65C42A78F5D3'],
].map(([origin,pathname,file,sha256])=>Object.freeze({origin,pathname,file,sha256})));
function loadFixedVendor(io=fs) {
    if(io.lstatSync(VENDOR_ROOT).isSymbolicLink()||io.realpathSync(VENDOR_ROOT)!==VENDOR_ROOT)throw Error('Vendor-каталог перенаправлен');
    const bodies=new Map();
    for(const item of MANIFEST){
        const file=path.join(VENDOR_ROOT,item.file),stat=io.lstatSync(file);
        if(stat.isSymbolicLink()||!stat.isFile()||io.realpathSync(file)!==file)throw Error('Vendor: требуется обычный фиксированный файл без перенаправления');
        const fd=io.openSync(file,fs.constants.O_RDONLY);
        try {
            const opened=io.fstatSync(fd);
            if(!opened.isFile()||opened.dev!==stat.dev||opened.ino!==stat.ino)throw Error('Vendor-файл подменён при открытии');
            const bytes=io.readFileSync(fd);
            if(io.realpathSync(file)!==file||createHash('sha256').update(bytes).digest('hex').toUpperCase()!==item.sha256)throw Error('Vendor: SHA256 не совпадает');
            bodies.set(item.origin+item.pathname,bytes.toString('base64'));
        } finally {io.closeSync(fd);}
    }
    // Наружу выдаются только неизменяемые строки, не общий Buffer и не путь.
    return url=>bodies.get(url)??null;
}
function isPageTarget(url){return new URL(url).pathname.startsWith('/devtools/page/');}
function mergedPatterns(patterns=[]) {
    const required=['https://unpkg.com/*','https://cdnjs.cloudflare.com/*','*/static/js/*'].map(urlPattern=>({urlPattern,requestStage:'Request'}));
    const all=[...patterns,...required];
    return all.filter((item,index)=>all.findIndex(other=>JSON.stringify(other)===JSON.stringify(item))===index);
}
function createInterceptionPolicy(vendor,{getBase,getCurrentModule}) {
    const pendingTargets=new Map();
    return {
        wrap(original){return async(method,args={})=>{
            if(method==='Fetch.disable')throw Error('Нельзя отключать безопасный vendor interception');
            if(method==='Fetch.enable')args={...args,patterns:mergedPatterns(args.patterns)};
            let destination;
            if(method==='Target.createTarget'&&args.url&&args.url!=='about:blank'){
                const url=new URL(args.url);
                if(url.origin!==getBase())throw Error('Чужой адрес нового CDP-target');
                destination=args.url;args={...args,url:'about:blank'};
            }
            const result=await original(method,args);
            if(destination)pendingTargets.set(result.targetId,destination);
            return result;
        };},
        async prepare(client,url){
            if(!isPageTarget(url))return;
            await client.call('Fetch.enable',{patterns:[]});
            const id=new URL(url).pathname.split('/').pop(),destination=pendingTargets.get(id);
            if(destination){
                pendingTargets.delete(id);
                await client.call('Page.navigate',{url:destination});
                await client.until('location.origin === '+JSON.stringify(getBase()),'safe target origin');
            }
        },
        async paused(params,call){
            const raw=params.request.url;
            let body=vendor(raw);
            if(body===null&&raw===getBase()+'/static/js/locationScene.js')body=getCurrentModule();
            if(body!==null){
                return call('Fetch.fulfillRequest',{requestId:params.requestId,responseCode:200,
                    responseHeaders:[{name:'Content-Type',value:'application/javascript'},{name:'Access-Control-Allow-Origin',value:'*'}],body});
            }
            const origin=new URL(raw).origin;
            if(MANIFEST.some(item=>item.origin===origin))return call('Fetch.failRequest',{requestId:params.requestId,errorReason:'BlockedByClient'});
            return call('Fetch.continueRequest',{requestId:params.requestId});
        }
    };
}
module.exports={MANIFEST,VENDOR_ROOT,loadFixedVendor,isPageTarget,mergedPatterns,createInterceptionPolicy};
