const el=id=>document.getElementById(id);
let busy=false;
function setBusy(value){busy=value;for(const id of ['download','inspect','url','quality','paste'])el(id).disabled=value;}
function showError(message){el('error').textContent=message;el('error').hidden=false;}
function input(){return {url:el('url').value.trim(),height:Number(el('quality').value)};}
async function request(path,body){
  const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  let data;try{data=await response.json();}catch{throw new Error('The server is unavailable. Refresh and sign in again.');}
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'The request could not be completed.');
  return data;
}
el('url').addEventListener('input',()=>{el('details').hidden=true;});
el('paste').addEventListener('click',async()=>{try{el('url').value=await navigator.clipboard.readText();el('url').dispatchEvent(new Event('input'));el('url').focus();}catch{showError('Use Ctrl+V, or press and hold the URL field to paste.');}});
el('inspect').addEventListener('click',async()=>{
  if(busy||!el('form').reportValidity())return;
  setBusy(true);el('error').hidden=true;el('inspect').textContent='Checking video…';
  try{const data=await request('/api/inspect',input());el('source').textContent=data.source;el('title').textContent=data.title;
    const duration=data.duration?`${Math.round(data.duration/60)} min`:'Duration unavailable';
    el('metadata').textContent=`${duration} · ${data.heights?.length?'Available: '+data.heights.map(h=>h+'p').join(', '):'Source quality'}`;
    el('details').hidden=false;
  }catch(e){showError(e.message);}finally{setBusy(false);el('inspect').textContent='Check video details first ↗';}
});
function status(job){el('status').hidden=false;el('message').textContent=job.message;el('percent').textContent=job.percent==null?'':`${job.percent}%`;if(job.percent==null)el('progress').removeAttribute('value');else el('progress').value=job.percent;}
async function watch(id){
  let failures=0;
  for(;;){let job;try{job=await request(`/api/jobs/${encodeURIComponent(id)}`);failures=0;}catch(e){if(++failures>=4)throw new Error(e.message+' Refresh to reconnect.');await new Promise(r=>setTimeout(r,2000));continue;}
    status(job);
    if(job.status==='error'){sessionStorage.removeItem('clipdock-job');throw new Error(job.message);}
    if(job.status==='ready'){el('save').href=`/files/${encodeURIComponent(id)}`;el('save').hidden=false;el('note').textContent='Save within one hour. Starting more downloads can remove the oldest file.';sessionStorage.removeItem('clipdock-job');return;}
    await new Promise(r=>setTimeout(r,1200));
  }
}
el('form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;setBusy(true);el('error').hidden=true;el('save').hidden=true;
  el('progress').hidden=false;el('note').textContent='Progress may restart for separate audio and video tracks.';status({message:'Starting your download…',percent:null});
  try{const job=await request('/api/jobs',input());sessionStorage.setItem('clipdock-job',job.id);await watch(job.id);}
  catch(e){showError(e.message);el('progress').hidden=true;el('message').textContent='Download could not be completed.';}
  finally{setBusy(false);}
});
const existing=sessionStorage.getItem('clipdock-job');if(existing){setBusy(true);watch(existing).catch(e=>{sessionStorage.removeItem('clipdock-job');showError(e.message);}).finally(()=>setBusy(false));}
