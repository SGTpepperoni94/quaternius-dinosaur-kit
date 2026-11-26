// Baselines: E-031 raw archive, sourceRestPose.REST and raw Idle 25%/75%.
const baseline = {
  "TRex": {
    "rest": [
      7.953674554824829,
      15.22499865014106,
      31.029576301574707
    ],
    "idle": [
      [
        7.956952893037034,
        15.212032424053687,
        30.966586205808888
      ],
      [
        7.956952893037034,
        15.212032424053687,
        31.292101611537618
      ]
    ]
  },
  "Triceratops": {
    "rest": [
      5.8441033363342285,
      9.432513415813446,
      20.34058666229248
    ],
    "idle": [
      [
        5.8479157017773815,
        8.931850101350458,
        20.81351237752628
      ],
      [
        5.847908080622624,
        8.949552276598654,
        20.844343840560516
      ]
    ]
  },
  "Stegosaurus": {
    "rest": [
      4.706379413604736,
      10.616360798478127,
      23.05349349975586
    ],
    "idle": [
      [
        5.127901590140745,
        10.404315752063427,
        23.224973469146033
      ],
      [
        5.127906641712578,
        10.404505692372695,
        23.210996508365255
      ]
    ]
  },
  "Velociraptor": {
    "rest": [
      2.635786771774292,
      5.513809580355883,
      13.27630615234375
    ],
    "idle": [
      [
        2.6359838074787554,
        5.1354986973858106,
        13.498848853724237
      ],
      [
        2.6359838074787554,
        5.135498708715214,
        13.586856042960907
      ]
    ]
  },
  "Apatosaurus": {
    "rest": [
      5.613175868988037,
      11.678450345993042,
      49.737382888793945
    ],
    "idle": [
      [
        5.72988986308376,
        11.152069140200373,
        49.32448024233203
      ],
      [
        5.729891598793329,
        11.152427537343064,
        49.21599684633169
      ]
    ]
  },
  "Parasaurolophus": {
    "rest": [
      1.9503475427627563,
      5.9794716108590364,
      12.481130123138428
    ],
    "idle": [
      [
        1.9583695837844637,
        5.673462268603985,
        12.798583895490594
      ],
      [
        1.9583695837844637,
        5.653820386785081,
        12.804526159863098
      ]
    ]
  }
};
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {chromium} from 'playwright-core';
import validator from 'gltf-validator';
const root=path.resolve(process.argv[2]??'diagnostics');
const vendor=path.resolve('node_modules/three');
const report={species:{},errors:[]};
const models={};
const check=(ok,msg)=>{if(!ok)throw new Error(msg);};
const near=(a,b,t)=>a.length===3&&b.length===3&&a.every((v,i)=>Number.isFinite(v)&&b[i]>0&&Math.abs(v-b[i])<=t*b[i]);
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost');
    if(url.pathname==='/'){res.setHeader('Content-Type','text/html');return res.end('<!doctype html><script type="importmap">{"imports":{"three":"/three/build/three.module.js","three/addons/":"/three/examples/jsm/"}}</script>');}
    const isVendor=url.pathname.startsWith('/three/');
    const base=isVendor?vendor:root;
    const file=path.resolve(base,decodeURIComponent(url.pathname.slice(isVendor?7:1)));
    if(!file.startsWith(base+path.sep)){res.writeHead(403);return res.end();}
    res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':'model/gltf-binary');res.end(await fs.readFile(file));
  }catch{res.writeHead(404);res.end();}
});
await fs.mkdir(root,{recursive:true});
await fs.rm(path.join(root,'manifest.json'),{force:true});
const persist=()=>fs.writeFile(path.join(root,'runtime.json'),JSON.stringify(report,null,2)+'\n');
let browser;
try{
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const base=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({headless:true,args:['--enable-webgl','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage();const browserErrors=[];
  page.on('pageerror',e=>browserErrors.push(String(e)));page.on('console',m=>{if(m.type()==='error')browserErrors.push(m.text());});
  await page.goto(base);
  for(const [name,ref] of Object.entries(baseline)){
    const entry=report.species[name]={};const offset=browserErrors.length;
    try{
      const file=path.join(root,'models',name+'.glb');const bytes=await fs.readFile(file);
      const meta=JSON.parse(await fs.readFile(file.replace(/\.glb$/,'.json'),'utf8'));
      entry.validator=await validator.validateBytes(new Uint8Array(bytes),{uri:name+'.glb',maxIssues:0});
      const result=entry.runtime=await page.evaluate(async({url,name})=>{
        const THREE=await import('three');const {GLTFLoader}=await import('three/addons/loaders/GLTFLoader.js');const {clone}=await import('three/addons/utils/SkeletonUtils.js');
        const gltf=await new GLTFLoader().loadAsync(url);const obj=clone(gltf.scene);const issues=[];
        const meshes=[];obj.traverse(o=>{if(o.isMesh)meshes.push(o);});
        if(!meshes.length||meshes.some(m=>!m.isSkinnedMesh))issues.push('Missing or unexpected unskinned mesh');
        function sample(o){
          o.updateMatrixWorld(true);const box=new THREE.Box3();const p=new THREE.Vector3();let count=0,bad=0;
          o.traverse(m=>{if(!m.isMesh)return;if(m.isSkinnedMesh)m.skeleton.update();const a=m.geometry.getAttribute('position');for(let i=0;i<a.count;i++){m.getVertexPosition(i,p);p.applyMatrix4(m.matrixWorld);count++;if(!p.toArray().every(Number.isFinite)){bad++;continue;}box.expandByPoint(p);}});
          const dims=box.getSize(new THREE.Vector3()).toArray();if(!count||bad||dims.some(x=>!Number.isFinite(x)||x<=0))issues.push('Invalid bounds');
          return {lo:box.min.toArray(),hi:box.max.toArray(),dimensions:dims,vertices:count,bad};
        }
        for(const m of meshes)if(m.isSkinnedMesh)m.pose();const rest=sample(obj);
        const mats=new Set();let triangles=0;
        for(const m of meshes){
          const g=m.geometry;triangles+=(g.index?.count??g.getAttribute('position').count)/3;
          for(const mat of Array.isArray(m.material)?m.material:[m.material])mats.add(mat);
          const w=g.getAttribute('skinWeight'),j=g.getAttribute('skinIndex');
          if(!m.isSkinnedMesh||!w||!j||w.itemSize!==4||j.itemSize!==4||w.count!==j.count||w.count!==g.getAttribute('position').count){issues.push('Invalid skin attributes');continue;}
          for(let i=0;i<w.count;i++){let sum=0;for(let k=0;k<4;k++){const x=w.getComponent(i,k),idx=j.getComponent(i,k);sum+=x;if(!Number.isFinite(x)||x<0||x>1||!Number.isInteger(idx)||idx<0||idx>=m.skeleton.bones.length)issues.push('Invalid skin component');}if(Math.abs(sum-1)>.001)issues.push('Invalid weight sum');}
        }
        const materials=[...mats].map(m=>({name:m.name,color:m.color?.toArray(),metalness:m.metalness,roughness:m.roughness,pbr:!!m.isMeshStandardMaterial}));
        for(const m of materials)if(!m.pbr||!m.color?.every(Number.isFinite)||![m.metalness,m.roughness].every(x=>Number.isFinite(x)&&x>=0&&x<=1)|| (m.color.every(x=>Math.abs(x-1)<1e-6)&&Math.abs(m.metalness-1)<1e-6&&Math.abs(m.roughness-1)<1e-6))issues.push('Invalid PBR material: '+m.name);
        const frames=[];const clips=gltf.animations.map(c=>({name:c.name,duration:c.duration}));
        for(const clip of gltf.animations){
          if(!Number.isFinite(clip.duration)||clip.duration<=0){issues.push('Invalid clip duration');continue;}
          const animated=clone(gltf.scene);const mixer=new THREE.AnimationMixer(animated);mixer.clipAction(clip).setLoop(THREE.LoopOnce,1).play();
          for(const fraction of [.25,.75]){mixer.setTime(clip.duration*fraction);const b=sample(animated);frames.push({clip:clip.name,fraction,...b,extentRatio:Math.hypot(...b.dimensions)/Math.hypot(...rest.dimensions)});}
          mixer.stopAllAction();mixer.uncacheRoot(animated);
        }
        const renderer=window.__run2Renderer??=new THREE.WebGLRenderer({antialias:false});renderer.setSize(128,128);const scene=new THREE.Scene();scene.add(obj,new THREE.HemisphereLight(0xffffff,0x777777,2));const center=new THREE.Vector3(...rest.lo).add(new THREE.Vector3(...rest.hi)).multiplyScalar(.5);const radius=Math.max(...rest.dimensions);const camera=new THREE.PerspectiveCamera(45,1,.001,radius*100+1);camera.position.copy(center).addScalar(radius*2);camera.lookAt(center);renderer.render(scene,camera);if(!renderer.info.render.calls)issues.push('No draw calls');
        return {rest,frames,clips,materials,triangles,issues,threeRevision:THREE.REVISION};
      },{url:`${base}/models/${name}.glb`,name});
      entry.browserErrors=browserErrors.slice(offset);
      check(entry.validator.issues.numErrors===0&&entry.validator.issues.numWarnings===0,'Validator errors or warnings');
      check(!result.issues.length&&!entry.browserErrors.length,'Runtime issues');
      check(Math.abs(result.rest.lo[1])<=.001,'Grounding');
      check(near(result.rest.dimensions,ref.rest,.02),'Native REST dimensions');
      check(near(result.rest.dimensions,meta.nativeWidthHeightDepth,.02),'Blender/runtime dimension disagreement');
      const expected=['Attack','Death','Idle','Jump','Run','Walk'].map(x=>name+'_'+x).sort();
      check(JSON.stringify(result.clips.map(x=>x.name).sort())===JSON.stringify(expected),'Clip coverage');
      check(result.frames.length===12,'Frame coverage');
      const idle=result.frames.filter(f=>f.clip===name+'_Idle');
      check(idle.length===2,'Idle frame coverage');
      for(let i=0;i<2;i++){check(idle[i].extentRatio>=.85&&idle[i].extentRatio<=1.15,'Idle extent ratio');check(near(idle[i].dimensions,ref.idle[i],.05),'Native Idle dimensions');}
      check(result.triangles===meta.triangleCount,'Triangle count disagreement');
      models[name]={...meta,nativeWidthHeightDepth:result.rest.dimensions,animations:expected,sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length};
    }catch(e){report.errors.push({species:name,error:String(e.stack??e)});}
    await persist();
  }
}catch(e){report.errors.push({error:String(e.stack??e)});}
finally{await browser?.close();server.close();await persist();}
if(report.errors.length||Object.keys(models).length!==6)process.exitCode=1;
else await fs.writeFile(path.join(root,'manifest.json'),JSON.stringify({scaling:'native-quaternius',models},null,2)+'\n');
