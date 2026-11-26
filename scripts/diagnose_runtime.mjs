// Run 1 only: read the three diagnostic GLBs for each species, never rewrite them.
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import { chromium } from 'playwright-core';
import validator from 'gltf-validator';

const species = ['TRex','Triceratops','Stegosaurus','Velociraptor','Apatosaurus','Parasaurolophus'];
const root = path.resolve(process.argv[2] ?? 'diagnostics');
const variants = ['raw','wrapped','limited'];
const out = { threeVersion: '0.187.0', species: {}, errors: [] };
const types = { '.js':'text/javascript', '.glb':'model/gltf-binary', '.json':'application/json' };
const vendor = path.resolve('node_modules/three');
const server = http.createServer(async (req,res) => {
  try {
    const u = new URL(req.url,'http://localhost');
    if (u.pathname === '/') {
      res.setHeader('content-type','text/html');
      return res.end('<!doctype html><meta charset="utf-8"><script type="importmap">{"imports":{"three":"/three/build/three.module.js","three/addons/":"/three/examples/jsm/"}}</script>');
    }
    const isVendor = u.pathname.startsWith('/three/');
    const base = isVendor ? vendor : root;
    const rel = decodeURIComponent(u.pathname.slice(isVendor ? 7 : 1));
    const file = path.resolve(base,rel);
    if (!file.startsWith(base+path.sep)) {res.writeHead(403);return res.end();}
    res.setHeader('content-type',types[path.extname(file)]??'application/octet-stream');
    res.end(await fs.readFile(file));
  } catch {res.writeHead(404);res.end();}
});
await fs.mkdir(root,{recursive:true});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
const persist = async () => fs.writeFile(path.join(root,'runtime.json'),JSON.stringify(out,null,2)+'\n');
try {
  browser = await chromium.launch({headless:true,args:['--enable-webgl','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page = await browser.newPage();
  const browserErrors = [];
  page.on('pageerror',e=>browserErrors.push(String(e)));
  page.on('console',m=>{if(m.type()==='error') browserErrors.push(m.text());});
  await page.goto(base);
  for (const name of species) {
    out.species[name] = {};
    let sourceReport;
    try { sourceReport = JSON.parse(await fs.readFile(path.join(root,name,'blender.json'),'utf8')); } catch(e) {out.errors.push({name,stage:'blenderReport',error:String(e)});}
    for (const variant of variants) {
      const entry = out.species[name][variant] = {};
      try {
        const bytes = await fs.readFile(path.join(root,name,variant+'.glb'));
        entry.validator = await validator.validateBytes(new Uint8Array(bytes), {uri:`${name}/${variant}.glb`,maxIssues:0});
        if (entry.validator.issues.numErrors) out.errors.push({name,variant,stage:'validator',count:entry.validator.issues.numErrors});
        const offset = browserErrors.length;
        entry.runtime = await page.evaluate(async ({url,variant})=>{
          const THREE = await import('three');
          const {GLTFLoader} = await import('three/addons/loaders/GLTFLoader.js');
          const {clone} = await import('three/addons/utils/SkeletonUtils.js');
          const gltf = await new GLTFLoader().loadAsync(url);
          const obj = clone(gltf.scene);
          const meshes = []; const bones = []; const materials = new Map();
          obj.traverse(o=>{if(o.isMesh) meshes.push(o);if(o.isBone) bones.push(o);});
          const issues = [];
          if(!meshes.length || !meshes.some(m=>m.isSkinnedMesh)) issues.push('Missing skinned mesh');
          function poseRest() {for(const m of meshes) if(m.isSkinnedMesh) m.pose();obj.updateMatrixWorld(true);}
          function sample(label) {
            obj.updateMatrixWorld(true);
            for(const m of meshes) if(m.isSkinnedMesh) m.skeleton.update();
            const all = new THREE.Box3(); const p = new THREE.Vector3(); let bad=0, verts=0;
            const perMesh = [];
            for(const m of meshes) {
              const pos=m.geometry.getAttribute('position'); const b=new THREE.Box3();
              for(let i=0;i<pos.count;i++) {
                m.getVertexPosition(i,p); p.applyMatrix4(m.matrixWorld); verts++;
                if(![p.x,p.y,p.z].every(Number.isFinite)){bad++;continue;}
                b.expandByPoint(p);all.expandByPoint(p);
              }
              perMesh.push({name:m.name,vertices:pos.count,lo:b.min.toArray(),hi:b.max.toArray()});
            }
            const box = new THREE.Box3().setFromObject(obj,true);
            const dims=all.getSize(new THREE.Vector3());
            const heads=bones.filter(b=>b.name.toLowerCase().includes('head'));
            const tails=bones.filter(b=>b.name.toLowerCase().includes('tail'));
            const avg = a=>a.reduce((s,b)=>s.add(b.getWorldPosition(new THREE.Vector3())),new THREE.Vector3()).divideScalar(a.length);
            const forward = heads.length&&tails.length ? avg(heads).sub(avg(tails)).normalize().toArray() : null;
            return {label,lo:all.min.toArray(),hi:all.max.toArray(),dimensionsXYZ:dims.toArray(),
              box3:{lo:box.min.toArray(),hi:box.max.toArray()},vertices:verts,nonFiniteVertices:bad,perMesh,
              forwardHeadTailInference:forward,positiveZInference:forward ? forward[2]>0.9 : null};
          }
          poseRest(); const rest=sample('rest');
          const skin=[]; let triangles=0, primitives=0;
          for(const m of meshes) {
            const g=m.geometry, n=g.getAttribute('position').count;
            const count=g.index?.count??n; triangles+=count/3;
            primitives+=g.groups.length||1;
            const mats=Array.isArray(m.material)?m.material:[m.material];
            for(const mat of mats) if(mat) materials.set(mat.uuid,{name:mat.name,color:mat.color?.toArray()??null,
              texture:!!mat.map,roughness:mat.roughness,metalness:mat.metalness});
            const attrs={};
            for(const key of ['normal','uv','uv1']) {const a=g.getAttribute(key);if(a){let nonFinite=0;for(let i=0;i<a.count;i++)for(let j=0;j<a.itemSize;j++)if(!Number.isFinite(a.getComponent(i,j)))nonFinite++;attrs[key]={count:a.count,nonFinite};if(nonFinite)issues.push(`${m.name}: nonfinite ${key}`);}}
            if(m.isSkinnedMesh){const w=g.getAttribute('skinWeight'),j=g.getAttribute('skinIndex');let zero=0,invalid=0,maxInfluences=0,maxSumError=0;
              if(!w||!j){issues.push(`${m.name}: missing skin attributes`);continue;}
              for(let i=0;i<w.count;i++){let sum=0,influences=0;for(let k=0;k<w.itemSize;k++){const v=w.getComponent(i,k),idx=j.getComponent(i,k);sum+=v;if(v>0)influences++;if(!Number.isFinite(v)||v<0||!Number.isInteger(idx)||idx<0||idx>=m.skeleton.bones.length)invalid++;}if(!sum)zero++;maxInfluences=Math.max(maxInfluences,influences);maxSumError=Math.max(maxSumError,Math.abs(sum-1));}
              skin.push({mesh:m.name,itemSize:w.itemSize,maxInfluences,maxSumError,zero,invalid,attributes:attrs});
              if(w.itemSize!==4||maxInfluences>4||maxSumError>0.001||zero||invalid)issues.push(`${m.name}: skin weight check failed`);
            }else skin.push({mesh:m.name,unskinned:true,attributes:attrs});
          }
          const frames=[];
          // Two frames for every clip, fresh clone for each clip, to prevent bleed-through.
          for(const clip of gltf.animations){
            // Restore the bind pose and reset transforms by using a separate clone.
            const animated=clone(gltf.scene); const mixer=new THREE.AnimationMixer(animated);
            const action=mixer.clipAction(clip);action.setLoop(THREE.LoopOnce,1);action.clampWhenFinished=true;action.play();
            for(const fraction of [0.25,0.75]){
              mixer.setTime(clip.duration*fraction);animated.updateMatrixWorld(true);
              let bad=0,vertices=0;const bb=new THREE.Box3();const v=new THREE.Vector3();
              animated.traverse(m=>{if(!m.isMesh)return;if(m.isSkinnedMesh)m.skeleton.update();const a=m.geometry.getAttribute('position');for(let i=0;i<a.count;i++){m.getVertexPosition(i,v);v.applyMatrix4(m.matrixWorld);vertices++;if(![v.x,v.y,v.z].every(Number.isFinite)){bad++;continue;}bb.expandByPoint(v);}});
              const dims=bb.getSize(new THREE.Vector3()); const ref=new THREE.Vector3(...rest.dimensionsXYZ);
              // Screening heuristic, not proof: large extent growth may also be legitimate motion.
              const extentRatio=dims.length()/Math.max(ref.length(),1e-9);
              frames.push({clip:clip.name,time:clip.duration*fraction,lo:bb.min.toArray(),hi:bb.max.toArray(),dimensionsXYZ:dims.toArray(),vertices,nonFiniteVertices:bad,extentRatio,explosionSuspect:extentRatio>4});
              if(bad||extentRatio>4)issues.push(`${clip.name}: nonfinite or large deformation`);
            }mixer.stopAllAction();mixer.uncacheRoot(animated);
          }
          if(!gltf.animations.length)issues.push('No clips');
          if(rest.nonFiniteVertices||!rest.vertices)issues.push('Invalid rest geometry');
          if(Math.abs(rest.lo[1])>0.05)issues.push('Rest ground exceeds diagnostic runtime tolerance 0.05 native units');
          // Direction inference and all-black/white are diagnostic flags, not proof of appearance.
          const mats=[...materials.values()];
          const monochromeSuspect=mats.length>0&&mats.every(m=>!m.texture&&m.color&&(m.color.every(x=>x<0.02)||m.color.every(x=>x>0.98)));
          if(monochromeSuspect)issues.push('All untextured materials near black or white; review source');
          // One WebGL context for the entire page; browser.close() releases it.
          const renderer=window.__diagRenderer ??= new THREE.WebGLRenderer({antialias:false});
          renderer.setSize(128,128);
          const scene=new THREE.Scene();scene.add(obj,new THREE.HemisphereLight(0xffffff,0x777777,2));
          const camera=new THREE.PerspectiveCamera(45,1,0.001,Math.max(...rest.dimensionsXYZ)*100+1);
          const center=new THREE.Box3().setFromObject(obj,true).getCenter(new THREE.Vector3());const radius=Math.max(...rest.dimensionsXYZ,1);
          camera.position.copy(center).add(new THREE.Vector3(radius,radius,radius));camera.lookAt(center);renderer.render(scene,camera);
          const render={calls:renderer.info.render.calls,triangles:renderer.info.render.triangles};
          return {variant,threeRevision:THREE.REVISION,rest,skin,materials:mats,monochromeSuspect,triangles,primitives,render,
            clips:gltf.animations.map(c=>({name:c.name,duration:c.duration})),frames,issues};
        },{url:`${base}/${name}/${variant}.glb`,variant});
        entry.browserErrors=browserErrors.slice(offset);
        entry.sourceActions=sourceReport?.sourceActions??null;
        if (entry.sourceActions) {
          const clips=entry.runtime.clips.map(c=>c.name);
          entry.missingSourceActions=entry.sourceActions.filter(a=>!clips.includes(a));
          if(entry.missingSourceActions.length) out.errors.push({name,variant,stage:'actionCoverage',missing:entry.missingSourceActions});
        }
        if(entry.runtime.issues.length||entry.browserErrors.length)out.errors.push({name,variant,stage:'runtime',issues:entry.runtime.issues,browserErrors:entry.browserErrors});
      }catch(e){entry.error=String(e.stack??e);out.errors.push({name,variant,error:entry.error});}
      await persist();
    }
    // Compare raw and limited exports: differences flag areas for independent QA.
    const a=out.species[name].raw?.runtime, b=out.species[name].limited?.runtime;
    if(a&&b){
      const sourceMeshes=sourceReport?.sourceRestPose?.REST?.objects?.filter(o=>o.type==='MESH')??[];
      out.species[name].comparison={sourceTriangles:sourceMeshes.reduce((n,o)=>n+o.mesh.triangles,0),rawTriangles:a.triangles,limitedTriangles:b.triangles,
        sourceMeshNames:sourceMeshes.map(o=>o.mesh.meshName),
        sampledBoundsDifferences:a.frames.map(f=>{const g=b.frames.find(x=>x.clip===f.clip&&x.time===f.time);return {clip:f.clip,time:f.time,loDelta:g?g.lo.map((v,i)=>v-f.lo[i]):null,hiDelta:g?g.hi.map((v,i)=>v-f.hi[i]):null};})};
      await persist();
    }
  }
}catch(e){out.errors.push({stage:'runtimeSetup',error:String(e.stack??e)});}
finally{await browser?.close();server.close();await persist();}
if(out.errors.length)process.exitCode=1;
