'use strict';
// Each partition has its own scale and measured free space.
const mib = 1048576;
const validBytes = n => Number.isSafeInteger(n) && n >= 0;
const validMount = m => m && m.total > 0 && [m.total,m.used,m.available,m.reserved].every(validBytes) && m.used+m.available+m.reserved === m.total;
const slice = (key,label,value,estimate=false,detail='') => ({key,label,value,estimate,detail});
const historyFolders = new Set(['community-lab','echo-field-test','echo-field-xl-test','echo-field-xl-prototype-tests','mala-v2-ui-test','oscilloscope-split-phase-ui-test']);
const audioGroups = [['reverbs','reverb'],['cabs','cabinet'],['sample_box','samples']];

function partitionUsage(mount, groups, labels, usedLabel, freeLabel) {
  const measured=Object.values(groups).reduce((sum,n)=>sum+n,0);
  const categorized=mount.used>0 && measured>0;
  let slices=[slice('used',usedLabel,mount.used)];
  if(categorized) {
    if(measured<mount.used) groups.other+=mount.used-measured;
    const weights=Object.entries(groups).filter(([,n])=>n>0);
    const sum=Math.max(measured,mount.used);
    let remainder=mount.used;
    slices=weights.map(([key,n],i)=>{
      const amount=i===weights.length-1?remainder:Math.min(remainder,Math.round(n*mount.used/sum));
      remainder-=amount;
      return slice(key,labels[key],amount,true);
    });
  }
  slices.push(slice('reserved','Filesystem reserve',mount.reserved),slice('free',freeLabel,mount.available));
  return {total:mount.total,categorized,slices};
}

function systemUsage(system, scan) {
  if(!validMount(system)) return null;
  const labels={os:'OS',system_data:'System Data',modules:'Modules',other:'Other System Files'};
  const groups={};
  for(const key of Object.keys(labels)) {
    const bytes=scan?.state==='ready' && scan.groups?.[key];
    groups[key]=validBytes(bytes)?bytes:0;
  }
  return partitionUsage(system,groups,labels,'Used system space','Free for system updates and modules');
}

function dataUsage(data,scan) {
  if (!validMount(data)) return null;
  const groups={history:0,presets:0,reverb:0,cabinet:0,samples:0,audio:0,other:0};
  const labels={history:'Updates & Recovery',presets:'Presets',reverb:'Reverb IRs',cabinet:'Cabinet IRs',
    samples:'Samples',audio:'Audio assets',other:'Other Files'};
  if (scan?.state === 'ready' && Array.isArray(scan.rows)) {
    for (const row of scan.rows) {
      if (!row || typeof row.name !== 'string' || !validBytes(row.bytes)) continue;
      const name=row.name;
      if(name==='audio' && scan.audio) {
        let remaining=row.bytes;
        for(const [folder,key] of audioGroups) {
          const amount=scan.audio[folder];
          if(validBytes(amount)) {
            const measured=Math.min(remaining,amount);
            groups[key]+=measured;
            remaining-=measured;
          }
        }
        groups.other+=remaining;
      } else {
        const key=historyFolders.has(name)?'history':name==='audio'?'audio':name==='presets'||name.startsWith('hector-presets-')?'presets':'other';
        groups[key]+=row.bytes;
      }
    }
  }
  return partitionUsage(data,groups,labels,'Used','Free for presets and audio');
}

if (typeof module === 'object' && module.exports) {
  module.exports = {systemUsage,dataUsage};
} else {
const $ = id => document.getElementById(id);
let csrf = '', presets = [], pageSize = 8, presetDateOrder = 0, info = {}, toastTimer, polling, storageData, scanRequest = false;
const size = n => {if(n >= 1073741824) return [(n / 1073741824).toFixed(1),'GiB']; if(n >= 1048576) return [(n / 1048576).toFixed(1),'MiB']; if(n >= 1024) return [(n / 1024).toFixed(1),'KiB']; return [String(n),'B'];};
const bytes = n => size(n).join(' ');
const node = (tag, text, className) => {const e=document.createElement(tag);if(text !== undefined)e.textContent=text;if(className)e.className=className;return e;};
function toast(text, error=false, persistent=false){ clearTimeout(toastTimer);$('toast').textContent=text;$('toast').className=error?'error':'';$('toast').hidden=false;if(!persistent)toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:6500); }
async function request(path, options={}){ const headers={...options.headers};if(options.method==='POST')headers['X-Hector-CSRF']=csrf;const response=await fetch(path,{...options,headers,credentials:'same-origin'});if(!response.ok){let message='The request could not finish.';try{message=(await response.json()).error||message;}catch{}if(response.status===401){if(deviceReconnect.current)deviceReconnect.connectionLost();else locked();}const error=new Error(message);error.status=response.status;throw error;}return response;}
function updateManagerLock(isLocked){const b=$('logout');b.hidden=false;b.dataset.locked=String(isLocked);$('manager-lock-label').textContent=isLocked?'Unlock Manager':'Lock Manager';$('manager-lock-icon').setAttribute('href',isLocked?'#icon-lock':'#icon-unlock');b.setAttribute('aria-label',isLocked?'Manager locked. Enter password to unlock.':'Manager unlocked. Lock manager.');}
function locked(){stopDesktop(false);csrf='';storageData=null;clearTimeout(polling);$('login').hidden=false;$('dashboard').hidden=true;updateManagerLock(true);$('refresh').hidden=true;showWorkspace();}
async function unlock(password){const r=await request('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});csrf=(await r.json()).csrf;await load();}
async function load(){const [s,p]=await Promise.all([request('/api/storage').then(r=>r.json()),request('/api/presets').then(r=>r.json())]);presets=p.presets;$('login').hidden=true;$('dashboard').hidden=false;updateManagerLock(false);$('refresh').hidden=false;renderStorage(s);renderPresets();showWorkspace();if(!info.preview && (s.breakdown.state==='idle' || s.breakdown.system?.state==='idle' || (s.breakdown.state==='ready' && s.updated-s.breakdown.updated>300))) scanStorage();}
function drawUsage(model, bar, legend) {
  bar.replaceChildren();legend.replaceChildren();
  for (const part of model.slices) {
    if (!part.value) continue;
    const value=(part.estimate?'≈ ':'')+bytes(part.value);
    const segment=node('span',undefined,'usage-segment storage-'+part.key);
    segment.style.width=(part.value/model.total*100)+'%';
    segment.title=part.label+': '+value+(part.detail?' · '+part.detail:'');
    bar.append(segment);
    const li=node('li',undefined,'usage-key');
    const swatch=node('i',undefined,'usage-swatch storage-'+part.key);swatch.setAttribute('aria-hidden','true');
    const label=node('span',part.label,'usage-label');
    if(part.detail) label.title=part.detail;
    li.append(swatch,label,node('strong',value,'usage-amount'));legend.append(li);
  }
}
function renderUsage(data,primary,system) {
  const hasGeometry=validBytes(data.geometry?.card_bytes) && data.geometry.card_bytes>0;
  $('card-capacity').textContent=hasGeometry?bytes(data.geometry.card_bytes)+' SD card':'';
  const systemChart=systemUsage(system,data.breakdown?.system);
  $('system-usage').hidden=!systemChart;
  if(systemChart) {
    $('system-capacity').textContent=bytes(system.total)+' usable';
    drawUsage(systemChart,$('system-bar'),$('system-legend'));
  }
  const writable=dataUsage(primary,data.breakdown);
  $('data-usage').hidden=!writable;
  $('free-for-saves').hidden=!writable;
  if(writable) {
    $('data-available').textContent=bytes(primary.available);
    $('data-capacity').textContent=bytes(primary.total)+' total';
    $('data-health').hidden=primary.available>=data.reserve+8*mib;
    drawUsage(writable,$('data-usage-bar'),$('data-legend'));
  }
  const state=data.breakdown?.state;
  const systemState=data.breakdown?.system?.state;
  $('usage-scale').hidden=!systemChart||!writable;
  $('data-note').textContent=state==='running'||systemState==='running'?'Measuring storage categories…':
    systemChart?.categorized||writable?.categorized?'≈ marks category estimates based on measured file blocks. Shared files and filesystem bookkeeping can affect the split.':
    'Inspect folders to see what is using space.';
}
function renderStorage(data) {
  storageData=data;
  const primary=data.mounts.find(m=>m.kind==='data'),system=data.mounts.find(m=>m.kind==='system');
  renderUsage(data,primary,system);
  const cards=$('storage-cards');cards.replaceChildren();
  for(const kind of ['system','flash','memory']) {
    const m=data.mounts.find(r=>r.kind===kind);if(!validMount(m))continue;
    const c=node('article',undefined,'storage-card'),top=node('div',undefined,'card-top');
    top.append(node('span',kind==='system'?'▣':kind==='flash'?'▱':'▥','card-icon'),node('span',m.readonly?'READ ONLY':kind==='memory'?'RAM · TEMPORARY':'INTERNAL FLASH','card-type'));
    const n=node('div',undefined,'card-numbers');n.append(node('span',bytes(m.available)),node('small','free of '+bytes(m.total)));
    const meter=node('div',undefined,'card-meter'),fill=node('span');fill.style.width=(m.used/m.total*100)+'%';meter.append(fill);
    const caption=node('div',undefined,'card-caption');caption.append(node('span',m.path),node('span',m.filesystem.toUpperCase()));
    c.append(top,node('h3',m.name),n,meter,caption);cards.append(c);
  }
  $('last-updated').textContent='Updated '+new Date(data.updated*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  $('preset-location').textContent='Current preset location: '+data.preset_root;
  const details=$('geometry-details');details.replaceChildren();
  const g=data.geometry;
  if(g) for(const [name,value] of [['Physical SD card',g.card_bytes],['Space outside current partitions',g.unpartitioned_bytes],['System partition',g.system_partition_bytes],['Data partition',g.data_partition_bytes]]) {
    const row=node('div',undefined,'geometry-row');row.append(node('span',name),node('span',bytes(value)));details.append(row);
  }
  renderBreakdown(data.breakdown);
}
function renderPresets() {
  const term=$('search').value.toLowerCase(), bank=$('bank-filter').value;
  const matches=presets.filter(p=>(bank==='all'||p.bank===bank)&&(p.name+' '+p.title).toLowerCase().includes(term));
  if(presetDateOrder) matches.sort((a,b)=>presetDateOrder*(a.modified-b.modified));
  $('preset-sort-direction').textContent=presetDateOrder<0?'↓':presetDateOrder>0?'↑':'↕';
  $('preset-sort-date').setAttribute('aria-label',presetDateOrder<0?'Saved date, newest first. Sort oldest first.':
    presetDateOrder>0?'Saved date, oldest first. Sort newest first.':'Sort by saved date, newest first.');
  $('preset-count').textContent=presets.length;
  $('results-label').textContent=matches.length+' saved presets';
  $('empty').hidden=matches.length>0;
  const list=$('preset-rows');
  list.replaceChildren();
  for(const p of matches.slice(0,pageSize)) {
    const row=node('div',undefined,'preset-row');
    const title=node('div',undefined,'preset-title');
    const text=node('div',undefined,'preset-text');
    text.append(node('strong',p.title),node('small',p.name));
    title.append(node('span','▤','preset-icon'),text);
    const date=node('span',new Date(p.modified*1000).toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'}),'preset-date');
    const actions=node('div',undefined,'file-actions');
    const download=node('button','Download','download-button file-action-button');
    download.type='button';
    download.setAttribute('aria-label','Download '+p.title);
    download.title='Download complete preset ZIP';
    download.addEventListener('click',()=>downloadArchive(p,download));
    actions.append(download);
    if(p.revision && p.name!=='Empty.ingen' && p.name!=='Default_Preset.ingen') {
      actions.append(deleteButton(p.title,'/api/presets/delete',{bank:p.bank,name:p.name,revision:p.revision},'preset'));
    }
    row.append(title,node('span',p.bank==='beebo'?'Beebo':'Digit','bank-tag'),date,actions);
    list.append(row);
  }
  $('load-more').hidden=matches.length<=pageSize;
  $('load-more').textContent='Show more presets ('+(matches.length-pageSize)+' remaining) ↓';
}

let pendingDeletion, audioFiles=[], audioFolders=[], audioWritable=false, audioPageSize=36, audioRequest=0;
const audioLabels={reverb:'Reverb IRs',cabinet:'Cabinet IRs',samples:'Samples'};
const audioFormat=library=>library==='samples'?'Sample Box accepts mono and stereo PCM or float WAVs.':'Hector’s IR modules use 48 kHz WAVs.';

function deleteButton(label,endpoint,body,kind) {
  const button=node('button','Delete','delete-button file-action-button');
  button.type='button';
  button.setAttribute('aria-label','Delete '+label);
  button.addEventListener('click',()=>{
    pendingDeletion={endpoint,body,kind};
    $('delete-title').textContent=kind==='preset'?'Delete this preset?':'Delete this audio file?';
    $('delete-name').textContent=label;
    $('delete-note').textContent=kind==='preset'?'This removes the saved preset from Hector.':'This removes the WAV from Hector. Save or close any patch currently using it first.';
    $('delete-error').textContent='';
    $('delete-dialog').showModal();
    $('delete-cancel').focus();
  });
  return button;
}

$('delete-cancel').addEventListener('click',()=>$('delete-dialog').close());
$('delete-dialog').addEventListener('cancel',event=>{if($('delete-submit').disabled) event.preventDefault();});
$('delete-dialog').addEventListener('close',()=>{pendingDeletion=null;});
$('delete-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const change=pendingDeletion;
  if(!change || $('delete-submit').disabled) return;
  $('delete-submit').disabled=$('delete-cancel').disabled=true;
  $('delete-error').textContent='';
  try {
    const response=await request(change.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...change.body,confirmed:true})});
    const result=await response.json();
    $('delete-dialog').close();
    toast(result.cleanup_pending?'The preset was removed. Cleanup is pending.':'Deleted from Hector.',Boolean(result.cleanup_pending));
    if(change.kind==='preset') await refreshPresets();
    else await loadAudio();
    await refreshStorage();
  } catch(error) {
    if($('delete-dialog').open) $('delete-error').textContent=error.message;
    else toast(error.message,true);
  } finally {
    $('delete-submit').disabled=$('delete-cancel').disabled=false;
  }
});

async function refreshPresets() {
  presets=(await (await request('/api/presets')).json()).presets;
  renderPresets();
}

async function refreshStorage() {
  renderStorage(await (await request('/api/storage')).json());
  if(!info.preview) scanStorage();
}

function audioURL(path,library,fields={}) {
  return '/api/audio'+path+'?'+new URLSearchParams({library,...fields});
}

function folderOptions(select,folders,all=false) {
  const old=select.value;
  select.replaceChildren();
  if(all) select.add(new Option('All folders','*'));
  for(const folder of folders) select.add(new Option(folder||'Library folder',folder));
  if(Array.from(select.options).some(option=>option.value===old)) select.value=old;
}

function renderAudio() {
  const folder=$('audio-folder').value;
  const library=$('audio-kind').value;
  const files=audioFiles.filter(file=>folder==='*'||file.path.slice(0,Math.max(0,file.path.lastIndexOf('/')))===folder);
  const target=$('audio-rows');
  target.replaceChildren();
  $('audio-count').textContent=files.length+' WAV files';
  $('audio-empty').hidden=files.length>0;
  $('audio-empty').textContent='No WAV files in this folder.';
  $('audio-more').hidden=files.length<=audioPageSize;
  for(const file of files.slice(0,audioPageSize)) {
    const row=node('div',undefined,'audio-row');
    const title=node('div');
    const name=file.path.split('/').at(-1);
    const subfolder=file.path.slice(0,Math.max(0,file.path.lastIndexOf('/')));
    title.append(node('strong',name));
    if(subfolder) title.append(node('small',subfolder));
    const actions=node('div',undefined,'file-actions');
    const download=node('a','Download','download-button file-action-button');
    download.href=audioURL('/download',library,{path:file.path});
    download.download=name;
    download.setAttribute('aria-label','Download '+file.path);
    actions.append(download);
    if(audioWritable) actions.append(deleteButton(file.path,audioURL('/delete',library),{path:file.path,revision:file.revision},'audio'));
    row.append(title,node('span',bytes(file.bytes),'audio-size'),actions);
    target.append(row);
  }
}

async function loadAudio() {
  const library=$('audio-kind').value;
  const run=++audioRequest;
  $('audio-format').textContent=audioFormat(library);
  $('audio-upload-open').disabled=true;
  $('audio-rows').replaceChildren();
  $('audio-empty').hidden=false;
  $('audio-empty').textContent='Reading the library…';
  $('audio-count').textContent='';
  try {
    const list=await (await request(audioURL('',library))).json();
    if(run!==audioRequest) return;
    audioFiles=list.files;
    audioFolders=list.folders;
    audioWritable=Boolean(list.writable);
    folderOptions($('audio-folder'),audioFolders,true);
    $('audio-upload-open').disabled=!audioWritable;
    renderAudio();
    if(!list.available) $('audio-empty').textContent='This library folder is unavailable on Hector.';
    else if(!audioWritable) $('audio-format').textContent='This library is available for browsing and downloading.';
  } catch(error) {
    if(run!==audioRequest) return;
    audioFiles=[];
    audioFolders=[];
    audioWritable=false;
    folderOptions($('audio-folder'),[],true);
    $('audio-empty').textContent=error.message;
  }
}

let moduleState, moduleReview, modulesBusy=false, moduleLoad=0;
let restartRevision, restartSubmitted=false;
const deviceReconnect=new HectorReconnect({
  storage:sessionStorage,
  async readInfo() {
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),4000);
    try {
      const response=await fetch('/api/info',{cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('Not ready');
      return await response.json();
    } finally {clearTimeout(timer);}
  },
  waiting(restarting) {
    for(const dialog of document.querySelectorAll('dialog[open]')) {
      if(dialog.id!=='reconnect-dialog')dialog.close();
    }
    $('reconnect-title').textContent=restarting?'Hector is restarting':'Checking Hector’s connection';
    $('reconnect-note').textContent=restarting?'Please wait. The sign-in screen will return when Hector is back online.':
      'Poly / View will check whether Hector is online.';
    $('modules-message').textContent=$('reconnect-title').textContent;
    if(!$('reconnect-dialog').open)$('reconnect-dialog').showModal();
  },
  ready(latest) {
    clearTimeout(installPoll);clearTimeout(installDismiss);moduleLoad++;
    moduleState=null;installBatch=null;moduleSelection.clear();
    restartSubmitted=false;nativeSubmitted=false;modulesBusy=false;
    info=latest;$('installed-version').textContent='v'+info.version;
    $('modules-message').textContent='';$('login-error').textContent='';
    $('password').value='';
    $('reconnect-dialog').close();locked();
    $('login-status').textContent='Hector is back online. Sign in to continue.';
    $('password').focus();
  },
  uncertain() {
    $('reconnect-note').textContent='Hector has not reconnected yet. Return to sign-in to check the connection again.';
  }
});
$('reconnect-dialog').addEventListener('cancel',event=>event.preventDefault());
$('reconnect-return').addEventListener('click',()=>{
  deviceReconnect.stop();
  $('reconnect-dialog').close();
  $('modules-message').textContent='';
  locked();
  $('login-status').textContent='Sign in when Hector is online.';
  $('password').focus();
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)deviceReconnect.checkNow();});
window.addEventListener('pageshow',()=>deviceReconnect.checkNow());
window.addEventListener('online',()=>deviceReconnect.checkNow());
function failedRestartRequest(error, dialogId, errorId) {
  deviceReconnect.stop();$('reconnect-dialog').close();
  const message=moduleConnectionError(error);
  $('modules-message').textContent=message;
  if(error.status===401) {
    for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
    locked();$('login-status').textContent='Sign in to continue.';
  } else {
    $(errorId).textContent=message;
    if(!$(dialogId).open)$(dialogId).showModal();
  }
}

function moduleConnectionError(error) {
  if(error instanceof TypeError||error.name==='AbortError') {
    if(deviceReconnect.current) {deviceReconnect.connectionLost();return '';}
    return 'Could not reach Hector. Refresh to reconnect.';
  }
  return error.message;
}

let nativeAction, nativeRevision, nativeSubmitted=false;
let installBatch, installPoll, installDismiss, completedProgressBatch;
const nativeBatchKey='aetherscope-test13';
const moduleSelection=new Set();
const moduleAction={install:'Install',remove:'Remove',discard:'Discard',undo:'Undo change'};
const moduleStatus={install:'Install pending restart',remove:'Removal pending restart',installed:'Installed'};

function moduleControls() {
  const active=batchActive();
  const selected=moduleState?.queue.filter(pkg=>moduleSelection.has(pkg.key))||[];
  const native=nativePackage(moduleState?.native_release);
  if(native&&moduleSelection.has(native.key)) selected.push(native);
  const updates=selected.filter(pkg=>pkg.previous_version).length;
  $('module-count').textContent=moduleSelection.size+' selected';
  const apply=updates===0?'Install':updates===selected.length?'Update':'Install / Update';
  $('modules-install').textContent=apply+' selected'+(moduleSelection.size?' ('+moduleSelection.size+')':'');
  $('modules-install').disabled=modulesBusy||active||!moduleState?.ready||!moduleSelection.size||selected.some(pkg=>pkg.invalid)||!info.module_restart;
  $('module-files').disabled=modulesBusy||active||!moduleState;
  $('modules-refresh').disabled=modulesBusy;
  $('modules-restart-open').hidden=!info.module_restart;
  $('modules-restart-open').disabled=modulesBusy||active||!moduleState||restartSubmitted||moduleState.native_release?.state==='working';
  for(const id of ['module-queue','native-release-queue']) $(id).inert=active;
}

function nativePackage(release) {
  if(release?.staged&&(['ready','failed','rolled-back'].includes(release.state)||
    (release.signed&&['installed','removed'].includes(release.state)))) {
    return {key:nativeBatchKey,name:'AetherScope',version:release.version||'test.13',
      previous_version:release.previous_version,native:true};
  }
}

function batchActive() {
  return Boolean(installBatch && !['installed','failed'].includes(installBatch.phase));
}

const batchLabels={queued:'Queued',checking:'Checking package',preparing:'Preparing installation',ready:'Ready for restart',applying:'Applying files',
  verifying:'Verifying installation',installed:'Installed',failed:'Needs attention'};

function renderInstallBatch(batch) {
  const wasActive=installBatch&&batch&&installBatch.id===batch.id&&!['installed','failed'].includes(installBatch.phase);
  installBatch=batch;
  if(batch&& !['installed','failed'].includes(batch.phase))deviceReconnect.arm(batch.boot||info.boot_id);
  if(batch?.phase==='restarting')deviceReconnect.connectionLost(true);
  if(batch?.phase==='failed') {deviceReconnect.stop();$('reconnect-dialog').close();}
  const panel=$('module-install-progress');
  if(batch?.phase==='installed') {
    if(wasActive) {
      completedProgressBatch=batch.id;
      panel.hidden=false;
      clearTimeout(installDismiss);
      installDismiss=setTimeout(()=>{
        if(installBatch?.id===batch.id&&installBatch.phase==='installed') panel.hidden=true;
      },2200);
    } else if(completedProgressBatch!==batch.id||panel.hidden) {
      panel.hidden=true;
      return;
    }
  } else {
    clearTimeout(installDismiss);
    completedProgressBatch=undefined;
    panel.hidden=!batch;
  }
  if(!batch) return;
  $('module-progress-summary').textContent=batch.platform?'Network Connection / Poly / View':batch.modules.length+(batch.modules.length===1?' module':' modules');
  const phase={checking:batch.platform?'Checking app update':'Checking packages',ready:'Preparing to restart',installing:'Installing modules',
    restarting:'Restarting Hector automatically',installed:'Hector is back online',failed:'Installation needs attention'}[batch.phase];
  const note=batch.error||(batch.platform&&batch.phase==='checking'?'Checking the signed release and current installation. No files have been changed yet.':batch.phase==='installed'?(batch.platform?'Poly / View is updated. Your settings and installed modules have been retained.':'The modules are available in the module picker.'):
    batch.phase==='restarting'?'Waiting for Hector to come back online.':
    'Hector will restart automatically when the batch is ready.');
  const banner=$('module-progress-phase');
  if(banner.dataset.phase!==phase||banner.dataset.note!==note) {
    banner.dataset.phase=phase;
    banner.dataset.note=note;
    banner.replaceChildren(node('strong',phase),node('p',note));
  }
  $('module-progress-rows').replaceChildren(...batch.modules.map(pkg=>{
    const row=node('li',undefined,'module-row module-progress-row');
    const title=node('strong',pkg.name);
    if(pkg.version) title.append(node('small',pkg.version));
    const status=node('span',batchLabels[pkg.status],'module-status');
    status.dataset.status=pkg.status;
    row.append(title,status);
    const fraction=pkg.status==='installed'?100:Math.min(95,Math.max(0,pkg.percent??
      (pkg.status==='ready'?95:pkg.total?Math.round(85*pkg.applied/pkg.total):0)));
    const detail=node('div',undefined,'module-progress-detail');
    const segments=node('div',undefined,'module-progress-segments');
    segments.setAttribute('role','progressbar');
    segments.setAttribute('aria-label',pkg.name+' installation progress');
    segments.setAttribute('aria-valuemin','0');
    segments.setAttribute('aria-valuemax','100');
    segments.setAttribute('aria-valuenow',String(fraction));
    const completed=Math.floor(12*fraction/100);
    for(let index=0;index<12;index++) {
      const segment=node('span',undefined,'module-progress-segment');
      if(index<completed) segment.classList.add('complete');
      else if(index===completed&&batchActive()) segment.classList.add('active');
      segments.append(segment);
    }
    detail.append(segments,node('strong',fraction+'%','module-progress-percent'));
    row.append(detail);
    if(pkg.message) row.append(node('small',pkg.message,'module-progress-message'));
    if(pkg.total) {
      const measure=pkg.key===nativeBatchKey?'applied':'checked';
      const count=pkg.applied+' of '+pkg.total+(pkg.total===1?' file ':' files ')+measure;
      row.append(node('small',count,'module-progress-message'));
    }
    return row;
  }));
}

function watchInstallBatch() {
  clearTimeout(installPoll);
  if(!batchActive()||location.hash!=='#module-manager') return;
  installPoll=setTimeout(async()=>{
    try {
      const {batch}=await (await request('/api/modules/batch')).json();
      renderInstallBatch(batch);
      moduleControls();
      if(!batchActive()) await loadModules();
    } catch(error) {
      if(!csrf) return;
      // Keep the last confirmed phase visible during the connection gap.
    }
    watchInstallBatch();
  },1600);
}

let nativeOpenGroups;
function renderNativeModules() {
  const entries=moduleState?.native_inventory;
  const search=$('native-module-search');
  const empty=$('native-module-empty');
  search.disabled=!entries;
  $('native-module-groups').hidden=!entries;
  if(!entries) {
    $('native-module-count').textContent='Hector’s native catalog is unavailable.';
    empty.hidden=true;
    return;
  }
  const term=search.value.trim().toLowerCase();
  const matches=entries.filter(entry=>(entry.name+' '+entry.id).toLowerCase().includes(term));
  $('native-module-count').textContent=term?matches.length+' of '+entries.length+' entries':entries.length+' catalog entries';
  empty.hidden=!term||matches.length>0;
  if(term && !nativeOpenGroups) {
    nativeOpenGroups=new Set(Array.from(document.querySelectorAll('.native-module-group[open]'), group=>group.id));
  }
  const sections=[
    ['native-community',entry=>entry.origin==='community'],
    ['native-stock',entry=>entry.origin==='stock'],
    ['native-protected',entry=>!['community','stock'].includes(entry.origin)],
  ];
  for(const [id,inGroup] of sections) {
    const group=$(id);
    const rows=matches.filter(inGroup);
    group.hidden=Boolean(term)&&!rows.length;
    if(nativeOpenGroups) group.open=term?rows.length>0:nativeOpenGroups.has(id);
    group.querySelector('.native-module-total').textContent='('+rows.length+')';
    group.querySelector('ul').replaceChildren(...rows.map(entry=>{
      const row=node('li',undefined,'module-row');
      const title=node('span');
      const preview=moduleState.community_removal_preview;
      const pkg=entry.origin==='community' && (moduleState.community_removal?.find(item=>item.members.includes(entry.id))
        || (preview?.id===entry.id && preview));
      title.append(node('strong',pkg?.members?.length>1?entry.name:(pkg?.name || entry.name)));
      if(!entry.in_picker) title.append(node('small','Kept for older presets'));
      if(pkg) {
        const pending=pkg.status==='remove';
        const action=pending?'undo':'remove';
        const actions=node('div',undefined,'file-actions');
        const button=node('button',pending?'Undo change':'Remove','delete-button');
        button.type='button';
        button.setAttribute('aria-label',moduleAction[action]+' '+pkg.name);
        button.addEventListener('click',()=>reviewModules(action,[pkg]));
        if(pending) actions.append(node('span','Removal pending restart','module-status review'));
        actions.append(button);
        row.append(title,actions);
        return row;
      }
      const review=entry.origin==='community';
      const status=review?'Removal review needed':entry.origin==='device'?'Device utility · protected':'Protected';
      row.append(title,node('span',status,review?'module-status review':'module-status'));
      return row;
    }));
    if(!rows.length)group.querySelector('ul').append(node('li','No modules in this group.','empty'));
  }
  if(!term) nativeOpenGroups=undefined;
}
$('native-module-search').addEventListener('input',renderNativeModules);

function renderModules(state) {
  if(state.native_release?.state==='working'&&moduleState?.native_release?.restoring) {
    state.native_release.restoring=true;
  }
  moduleState=state;
  renderInstallBatch(state.install_batch);
  const native=nativePackage(state.native_release);
  for(const key of moduleSelection) if(key!==native?.key&&!state.queue.some(pkg=>pkg.key===key)) moduleSelection.delete(key);
  const warning=$('modules-restart');
  warning.hidden=state.ready&&!state.restart_required&&!state.activation_error;
  warning.textContent=state.activation_error?'Module changes held. '+state.activation_error:
    state.restart_required?'Restart required. Save your current patch before restarting Hector.':
    'Restart Hector to finish enabling the native module catalog.';
  const button=(action,pkg,style,disabled=false)=>{
    const label=action==='install'&&pkg.previous_version?'Update':moduleAction[action];
    const control=node('button',label,style);
    control.type='button';
    control.disabled=disabled;
    control.setAttribute('aria-label',label+' '+pkg.name);
    control.addEventListener('click',()=>reviewModules(action,[pkg]));
    return control;
  };
  function renderRows(list,queued) {
    const rows=list.map(pkg=>{
      const row=node('li',undefined,'module-row');
      const label=node(queued?'label':'div',undefined,'module-description');
      if(queued) {
        const checkbox=node('input');
        checkbox.type='checkbox';
        checkbox.disabled=Boolean(pkg.invalid);
        checkbox.checked=moduleSelection.has(pkg.key);
        checkbox.setAttribute('aria-label','Select '+pkg.name);
        checkbox.addEventListener('change',()=>{
          if(checkbox.checked) moduleSelection.add(pkg.key);
          else moduleSelection.delete(pkg.key);
          moduleControls();
        });
        label.append(checkbox);
      }
      const title=node('span');
      const version=pkg.previous_version?pkg.previous_version+' → '+pkg.version:pkg.version;
      title.append(node('strong',pkg.name),node('small',version+' · '+bytes(pkg.size)+(queued?' · '+pkg.file:'')));
      label.append(title);
      row.append(label);
      const actions=node('div',undefined,'file-actions');
      if(queued) {
        actions.append(button('install',pkg,'button install',pkg.invalid||!state.ready||!info.module_restart),button('discard',pkg,'delete-button'));
      } else {
        actions.append(node('span',moduleStatus[pkg.status],'muted'));
        if(pkg.platform) actions.append(node('span','App update supported','module-status'));
        else if(pkg.status==='installed') actions.append(button('remove',pkg,'delete-button',!state.ready));
        else actions.append(button('undo',pkg,'delete-button',!state.ready));
      }
      row.append(actions);
      return row;
    });
    return rows;
  }
  const release=state.native_release;
  const nativeRow=nativeReleaseRow(release);
  const nativeQueue=nativeRow&&!nativeRow.managed;
  $('native-release-queue').hidden=!nativeQueue;
  $('native-release-queue').replaceChildren(...(nativeQueue?[nativeRow.row]:[]));
  const queue=renderRows(state.queue,true);
  if(!queue.length&&!nativeQueue) queue.push(node('li','No release packages in the queue.','module-row muted'));
  $('module-queue').replaceChildren(...queue);
  const installed=renderRows(state.installed,false);
  if(nativeRow?.managed) installed.unshift(nativeRow.row);
  else if(release?.signed&&release.state==='installed') {
    const current=nativeReleaseRow({...release,signed:false,staged:false,version:release.installed_version||'test.13'});
    if(current) installed.unshift(current.row);
  }
  if(!installed.length) installed.push(node('li','No Poly / View modules installed yet.','module-row muted'));
  $('module-installed').replaceChildren(...installed);
  renderNativeModules();
  moduleControls();
  watchInstallBatch();
}

const nativeReleaseLabels={install:'Install AetherScope',rollback:'Restore previous state',discard:'Discard upload'};
const nativeReleaseStatus={installed:'Installed',working:'Applying changes. Hector will restart automatically.',
  'rolled-back':'Previous state restored',removed:'AetherScope was removed after installation',
  failed:'Installation stopped','recovery-required':'Recovery required',unavailable:'Native installer unavailable'};

function nativeReleaseRow(release) {
  if(!release||(!release.staged&&!nativeReleaseStatus[release.state])||
    (batchActive()&&!installBatch.native&&release.state==='working')) return;
  const managed=(!release.signed&&['installed','recovery-required'].includes(release.state))||
    release.state==='recovery-required'||release.restoring;
  const row=node('li',undefined,'module-row');
  const selectable=nativePackage(release);
  const title=node(selectable?'label':'div',undefined,'module-description');
  if(selectable) {
    const checkbox=node('input');
    checkbox.type='checkbox';
    checkbox.checked=moduleSelection.has(selectable.key);
    checkbox.setAttribute('aria-label','Select AetherScope');
    checkbox.addEventListener('change',()=>{
      if(checkbox.checked) moduleSelection.add(selectable.key);
      else moduleSelection.delete(selectable.key);
      moduleControls();
    });
    title.append(checkbox);
  }
  const details=node('span');
  const version=release.version||release.installed_version||'test.13';
  const label=selectable&&release.previous_version?release.previous_version+' → '+version:
    selectable?release.file:nativeReleaseStatus[release.state]||release.file;
  details.append(node('strong','AetherScope '+version),node('small',label));
  if(release.message) details.append(node('small',release.message));
  title.append(details);
  const actions=node('div',undefined,'file-actions');
  const button=action=>{
    const control=node('button',nativeReleaseLabels[action],action==='discard'?'delete-button':'button secondary');
    control.type='button';
    control.addEventListener('click',()=>openNativeRelease(action));
    actions.append(control);
  };
  if(managed&&['installed','recovery-required'].includes(release.state)) button('rollback');
  if(selectable) {
    const install=node('button',selectable.previous_version?'Update AetherScope':'Install AetherScope','button install');
    install.type='button';
    install.disabled=!moduleState.ready||!info.module_restart;
    install.addEventListener('click',()=>reviewModules('install',[selectable]));
    actions.append(install);
  }
  if(!managed&&release.staged&&release.state!=='working') button('discard');
  row.append(title,actions);
  return {row,managed};
}

function openNativeRelease(action) {
  nativeAction=action;
  nativeRevision=moduleState.revision;
  $('native-release-title').textContent=nativeReleaseLabels[action]+'?';
  $('native-release-note').textContent=action==='discard'?'Discard the queued ZIP.':
    action==='install'?'Hector will verify the release and save a recovery copy, then restart automatically. Pause Link transport if it is playing.':
    'Hector will verify and restore the saved firmware and module state, then restart. Pause Link transport if it is playing.';
  $('native-release-save').hidden=action==='discard';
  $('native-release-saved').checked=false;
  $('native-release-saved').required=action!=='discard';
  $('native-release-error').textContent='';
  $('native-release-confirm').textContent=nativeReleaseLabels[action];
  $('native-release-dialog').showModal();
}

$('native-release-cancel').addEventListener('click',()=>$('native-release-dialog').close());
$('native-release-dialog').addEventListener('cancel',event=>{if(nativeSubmitted) event.preventDefault();});
$('native-release-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(nativeSubmitted) return;
  nativeSubmitted=true;
  if(nativeAction!=='discard')deviceReconnect.arm(info.boot_id);
  $('native-release-confirm').disabled=$('native-release-cancel').disabled=true;
  try {
    const reply=await request('/api/modules/native',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:nativeAction,revision:nativeRevision,confirmed:true,saved_patch:$('native-release-saved').checked})});
    const result=await reply.json();
    $('native-release-dialog').close();
    if(result.working) {
      moduleState.native_release.restoring=nativeAction==='rollback';
      moduleState.native_release.state='working';
      renderModules(moduleState);
      $('modules-message').textContent=nativeAction==='install'?
        'Installing AetherScope. Hector will restart automatically when installation finishes. Refresh after it comes back.':
        'Restoring the previous state. Hector will restart automatically. Refresh after it comes back.';
    } else renderModules(result);
  } catch(error) {
    if(deviceReconnect.current&&error instanceof TypeError) {deviceReconnect.connectionLost();return;}
    failedRestartRequest(error,'native-release-dialog','native-release-error');
  } finally {
    nativeSubmitted=false;
    $('native-release-confirm').disabled=$('native-release-cancel').disabled=false;
  }
});

async function loadModules() {
  const run=++moduleLoad;
  $('modules-message').textContent='Reading module library…';
  try {
    const state=await (await request('/api/modules')).json();
    if(run!==moduleLoad) return;
    renderModules(state);
    $('modules-message').textContent='';
  } catch(error) {
    if(run===moduleLoad) $('modules-message').textContent=moduleConnectionError(error);
  }
}

async function reviewModules(action,packages) {
  if(modulesBusy||batchActive()||!packages.length) return;
  const installing=action==='install';
  if(packages.some(pkg=>pkg.platform)&&packages.length!==1) {
    toast('Update Network Connection / Poly / View separately from audio modules.');return;
  }
  const review=moduleReview={action,keys:packages.filter(pkg=>!pkg.native).map(pkg=>pkg.key),revision:moduleState.revision};
  if(packages[0].platform)review.platform=packages[0].key;
  if(installing) review.native=packages.some(pkg=>pkg.native);
  const removing=action==='remove';
  const updates=installing?packages.filter(pkg=>pkg.previous_version).length:0;
  const apply=!updates?'Install':updates===packages.length?'Update':'Install / Update';
  const label=action==='undo'?'Undo pending change':
    installing?(packages.length===1?apply+' '+packages[0].name:apply+' '+packages.length+' packages'):
    removing?(packages.length===1?'Remove module':'Remove modules'):moduleAction[action]+' '+packages.length+(packages.length===1?' package':' packages');
  const confirm=$('module-review-confirm');
  const dialog=$('module-review');
  const dependency=$('module-review-dependency');
  const presets=$('module-review-presets');
  $('module-review-title').textContent=label+'?';
  confirm.textContent=label;
  confirm.className='button '+(installing?'install':'danger');
  confirm.disabled=removing;
  $('module-review-list').replaceChildren(...packages.map(pkg=>node('li',pkg.version?
    pkg.name+' · '+(pkg.previous_version?pkg.previous_version+' → ':'')+pkg.version:pkg.name)));
  const scope=$('module-review-scope');
  scope.textContent=removing?(packages[0].scope || ''):'';
  scope.hidden=!scope.textContent;
  $('module-review-note').textContent=action==='discard'?'This removes the uploaded package from the queue.':
    action==='undo'?'This cancels the selected change before Hector restarts.':
    installing?(review.platform?'Poly / View will disconnect briefly while the app updates. Hector restarts automatically; your settings and modules are retained. Pause Link transport if it is playing.':
      'Hector will check the selected packages and restart automatically.'+(review.native?' Pause Link transport if it is playing.':'')):
    'Restart required. Changes take effect after Hector restarts. Save your current patch before restarting.';
  $('module-review-save').hidden=!installing;
  $('module-review-saved').checked=false;
  $('module-review-saved').required=installing;
  $('module-review-error').textContent='';
  dependency.hidden=!removing;
  dependency.textContent=removing?'Checking saved presets…':'';
  presets.hidden=true;
  presets.replaceChildren();
  dialog.showModal();
  if(!removing) return;
  try {
    const impact=await (await request('/api/modules/removal-review',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keys:review.keys,revision:review.revision})})).json();
    if(moduleReview!==review||!dialog.open) return;
    const matches=[...new Set(Object.values(impact.references).flat())];
    dependency.textContent=matches.length?
      'These saved presets may open with missing modules after removal. Their preset files will stay on Hector.':
      'No references found in Hector’s saved preset banks.';
    presets.replaceChildren(...matches.map(path=>{
      const [bank,name]=path.split('/');
      const label=bank==='beebo'?'Hector / Beebo':'Digit';
      return node('li',name.replace(/\.ingen$/i,'').replaceAll('_',' ')+' · '+label);
    }));
    presets.hidden=!matches.length;
    if(matches.length) confirm.textContent='Remove anyway';
    review.review=impact.review;
    confirm.disabled=Boolean(impact.preview_only);
    if(impact.preview_only) {
      scope.textContent='This targets the Legacy registration. The current Sample Box uses the shared plugin and keeps its audio library.';
      scope.hidden=false;
      if(!matches.length) dependency.textContent='No references found in the example preset banks.';
      $('module-review-note').textContent='Preview only. Restart required to apply a removal on Hector.';
    }
  } catch(error) {
    if(moduleReview===review&&dialog.open) {
      dependency.hidden=true;
      if(deviceReconnect.current&&error instanceof TypeError) {deviceReconnect.connectionLost();return;}
    failedRestartRequest(error,'module-review','module-review-error');
    }
  }
}

$('modules-refresh').addEventListener('click',()=>{loadModules();if(managerTab==='repositories')loadRepositories();});
const restartDialog=$('modules-restart-dialog');
$('modules-restart-open').addEventListener('click',()=>{
  restartRevision=moduleState.revision;
  $('modules-restart-note').textContent=moduleState.restart_required?'This applies all pending module changes together.':'';
  $('modules-restart-error').textContent='';
  restartDialog.showModal();
});
$('modules-restart-cancel').addEventListener('click',()=>restartDialog.close());
restartDialog.addEventListener('cancel',event=>{if(restartSubmitted) event.preventDefault();});
$('modules-restart-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(restartSubmitted) return;
  restartSubmitted=true;
  deviceReconnect.arm(info.boot_id);
  $('modules-restart-cancel').disabled=$('modules-restart-confirm').disabled=true;
  try {
    await request('/api/modules/restart',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({revision:restartRevision,confirmed:true})});
    restartDialog.close();
    deviceReconnect.connectionLost(true);
  } catch(error) {
    restartSubmitted=false;
    if(error instanceof TypeError) {deviceReconnect.connectionLost();return;}
    failedRestartRequest(error,'modules-restart-dialog','modules-restart-error');
    $('modules-restart-cancel').disabled=$('modules-restart-confirm').disabled=false;
  }
  moduleControls();
});
$('modules-install').addEventListener('click',()=>{
  const selected=moduleState.queue.filter(pkg=>moduleSelection.has(pkg.key));
  const native=nativePackage(moduleState.native_release);
  if(native&&moduleSelection.has(native.key)) selected.push(native);
  reviewModules('install',selected);
});
$('module-review-cancel').addEventListener('click',()=>$('module-review').close());
$('module-review').addEventListener('cancel',event=>{if(modulesBusy) event.preventDefault();});
$('module-files').addEventListener('change',async event=>{
  const input=event.target;
  const files=Array.from(input.files);
  input.value='';
  if(!files.length||modulesBusy||batchActive()) return;
  modulesBusy=true;
  moduleControls();
  const errors=[];
  let added=0;
  try {
    for(const file of files) {
      $('modules-message').textContent='Checking '+file.name+'…';
      try {
        const state=await (await request('/api/modules/upload?'+new URLSearchParams({name:file.name}),
          {method:'POST',headers:{'Content-Type':'application/zip'},body:file})).json();
        for(const pkg of state.queue) if(pkg.file===file.name) moduleSelection.add(pkg.key);
        const native=nativePackage(state.native_release);
        if(native&&state.native_release.file===file.name) moduleSelection.add(native.key);
        renderModules(state);
        added++;
      } catch(error) {
        errors.push(file.name+' — '+error.message);
        if(!csrf) break;
      }
    }
    $('modules-message').textContent=[added?added+' added to the queue.':'',...errors].filter(Boolean).join(' ');
  } finally {
    modulesBusy=false;
    moduleControls();
  }
});
$('module-review-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(modulesBusy||batchActive()||$('module-review-confirm').disabled) return;
  modulesBusy=true;
  $('module-review-cancel').disabled=$('module-review-confirm').disabled=true;
  try {
    const installing=moduleReview.action==='install';
    if(installing)deviceReconnect.arm(info.boot_id);
    const fields={...moduleReview,confirmed:true};
    if(installing) fields.saved_patch=$('module-review-saved').checked;
    if(moduleReview.platform)fields.key=moduleReview.platform;
    const result=await (await request(moduleReview.platform?'/api/modules/platform':installing?'/api/modules/batch':'/api/modules/change',
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)})).json();
    if(installing) {
      moduleSelection.clear();
      renderInstallBatch(result.batch);
      $('modules-message').textContent='';
      watchInstallBatch();
    } else renderModules(result);
    $('module-review').close();
    if(!installing) $('module-files').focus();
  } catch(error) {
    if(deviceReconnect.current&&error instanceof TypeError) {deviceReconnect.connectionLost();return;}
    failedRestartRequest(error,'module-review','module-review-error');
  } finally {
    modulesBusy=false;
    moduleControls();
    $('module-review-cancel').disabled=$('module-review-confirm').disabled=false;
  }
});

$('audio-kind').addEventListener('change',()=>{
  $('audio-folder').value='*';
  audioPageSize=36;
  loadAudio();
});
$('audio-folder').addEventListener('change',()=>{audioPageSize=36;renderAudio();});
$('audio-refresh').addEventListener('click',loadAudio);
$('audio-more').addEventListener('click',()=>{audioPageSize+=36;renderAudio();});
$('audio-upload-open').addEventListener('click',()=>{
  $('audio-upload-form').reset();
  folderOptions($('audio-upload-folder'),audioFolders);
  $('audio-upload-folder').value=$('audio-folder').value==='*'?'':$('audio-folder').value;
  const library=$('audio-kind').value;
  $('audio-upload-note').textContent=audioLabels[library]+' · '+audioFormat(library)+' Up to 16 MiB per file.';
  $('audio-upload-error').textContent='';
  $('audio-upload-dialog').showModal();
});
$('audio-upload-cancel').addEventListener('click',()=>$('audio-upload-dialog').close());
$('audio-upload-dialog').addEventListener('cancel',event=>{if($('audio-upload-submit').disabled) event.preventDefault();});
$('audio-upload-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if($('audio-upload-submit').disabled) return;
  const file=$('audio-file').files[0];
  if(!file) return;
  $('audio-upload-submit').disabled=$('audio-upload-cancel').disabled=true;
  $('audio-upload-error').textContent='';
  try {
    await request(audioURL('/upload',$('audio-kind').value,{folder:$('audio-upload-folder').value,name:file.name}),{
      method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file
    });
    $('audio-upload-dialog').close();
    toast(file.name+' uploaded to Hector.');
    await loadAudio();
    await refreshStorage();
  } catch(error) {
    if($('audio-upload-dialog').open) $('audio-upload-error').textContent=error.message;
    else toast(error.message,true);
  } finally {
    $('audio-upload-submit').disabled=$('audio-upload-cancel').disabled=false;
  }
});

async function downloadArchive(p,button){button.disabled=true;const original=button.textContent;toast(p?'Preparing '+p.title+'…':'Preparing your saved preset library…',false,true);try{const query=p?'?'+new URLSearchParams({bank:p.bank,name:p.name}):'';const response=await request('/api/export'+query);const blob=await response.blob(),url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download=p?p.name+'.zip':'hector-presets-'+new Date().toISOString().slice(0,10)+'.zip';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('Download sent to your browser. Keep the ZIP somewhere safe.');}catch(e){toast(e.message,true);}finally{button.disabled=false;button.textContent=original;}}
const labels=name=>name==='community-lab'?'Community installs & rollback':name.startsWith('hector-presets-')?'Saved presets':name.startsWith('hector-pedal_state-')?'Device settings':name==='audio'?'Audio assets':name.startsWith('.original-')?'Preserved original storage':name;
function renderBreakdown(data) {
  const target=$('breakdown-rows');target.replaceChildren();clearTimeout(polling);
  $('scan').disabled=scanRequest||data.state==='running'||data.system?.state==='running';
  if(data.state==='running'||data.system?.state==='running') {
    target.append(node('p','Inspecting folders at low priority. This may take a moment…','empty'));
    polling=setTimeout(async()=>{try{const r=await request('/api/storage');const snapshot=await r.json();if(storageData)renderStorage(snapshot);}catch(e){toast(e.message,true);}},2000);
    return;
  }
  if(data.state!=='ready') {target.append(node('p',data.message||'Run a folder inspection to see what is using this space.','empty'));return;}
  const max=Math.max(1,...data.rows.map(r=>r.bytes));
  for(const folder of data.rows.filter(r=>r.bytes>0).slice(0,12)) {
    const row=node('div',undefined,'folder-row'),name=node('div',undefined,'folder-name'),bar=node('div',undefined,'folder-meter'),fill=node('i');
    name.title=folder.name;name.append(node('span','▰'),document.createTextNode(labels(folder.name)));fill.style.width=folder.bytes/max*100+'%';bar.append(fill);
    row.append(name,bar,node('span',bytes(folder.bytes),'folder-size'));target.append(row);
  }
}
async function scanStorage() {
  if(scanRequest||!storageData||storageData.breakdown.state==='running'||storageData.breakdown.system?.state==='running')return;
  scanRequest=true;$('scan').disabled=true;
  try {
    const breakdown=await(await request('/api/storage/scan',{method:'POST'})).json();
    if(storageData)renderStorage({...storageData,breakdown});
  } catch(e){toast(e.message,true);}
  finally{scanRequest=false;$('scan').disabled=storageData?.breakdown.state==='running'||storageData?.breakdown.system?.state==='running';}
}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;$('login-error').textContent='';try{$('login-status').textContent='';await unlock($('password').value);$('password').value='';}catch(err){$('login-error').textContent=err.message;}finally{b.disabled=false;}});
$('preview-login').addEventListener('click',()=>unlock('hector-preview').catch(e=>toast(e.message,true)));
$('refresh').addEventListener('click',async()=>{try{await load();toast('Storage and presets refreshed.');}catch(e){toast(e.message,true);}});
$('logout').addEventListener('click',async()=>{if($('logout').dataset.locked==='true'){$('login').scrollIntoView({block:'center'});$('password').focus();return;}try{await request('/api/logout',{method:'POST'});locked();}catch(e){toast(e.message,true);}});
$('search').addEventListener('input',()=>{pageSize=8;renderPresets();});$('bank-filter').addEventListener('change',()=>{pageSize=8;renderPresets();});$('load-more').addEventListener('click',()=>{pageSize+=20;renderPresets();});
$('preset-sort-date').addEventListener('click',()=>{
  presetDateOrder=presetDateOrder<0?1:-1;
  pageSize=8;
  renderPresets();
});
$('refresh-presets').addEventListener('click',async()=>{
  try {
    await refreshPresets();
    toast('Preset library refreshed.');
  } catch(error) {
    toast(error.message,true);
  }
});
$('backup').addEventListener('click',()=>downloadArchive(null,$('backup')));
$('scan').addEventListener('click',scanStorage);
$('show-upload').addEventListener('click',()=>{$('upload-form').reset();$('file-name').textContent='Choose a preset ZIP';$('upload-error').textContent='';$('upload-dialog').showModal();});
for(const b of document.querySelectorAll('.dialog-close,.dialog-cancel'))b.addEventListener('click',()=>{$('upload-dialog').close();});
$('upload-file').addEventListener('change',()=>{$('file-name').textContent=$('upload-file').files[0]?.name||'Choose a preset ZIP';});
$('upload-dialog').addEventListener('cancel',e=>{if($('upload-submit').disabled)e.preventDefault();});
$('dropzone').addEventListener('dragover',e=>e.preventDefault());$('dropzone').addEventListener('drop',e=>{e.preventDefault();if(e.dataTransfer.files.length===1){$('upload-file').files=e.dataTransfer.files;$('file-name').textContent=e.dataTransfer.files[0].name;}});
$('upload-form').addEventListener('submit',async e=>{e.preventDefault();const f=$('upload-file').files[0];if(!f)return;if(f.size>(info.upload_limit||16777216)){$('upload-error').textContent='Choose a ZIP smaller than 16 MiB.';return;}$('upload-submit').disabled=true;for(const b of document.querySelectorAll('.dialog-close,.dialog-cancel'))b.disabled=true;try{const r=await request('/api/upload?'+new URLSearchParams({bank:$('upload-bank').value}),{method:'POST',headers:{'Content-Type':'application/zip'},body:f});const result=await r.json();$('upload-dialog').close();await load();toast(result.name+' uploaded. Reopen Hector’s preset browser to find it.');}catch(err){$('upload-error').textContent=err.message;}finally{$('upload-submit').disabled=false;for(const b of document.querySelectorAll('.dialog-close,.dialog-cancel'))b.disabled=false;}});
const desktopCanvas = $('desktop-screen');
const desktopPanel = $('desktop-panel');
const desktopShiftButtons=[$('desktop-shift'),$('desktop-shift-right')];
const desktopContext = desktopCanvas.getContext('2d', {alpha:false});
let desktopRun = 0, desktopViewer = '', desktopControl = false, desktopConnecting = false;
let desktopTimer, pointerTimer, pointerId = null, lastPoint, pointerQueue = [], draining = false;
let shiftGesture = false, shiftPoint = null, holdRequested = false, holdTimer;
let shiftTouchOrigin=null, shiftAlreadyHeld=false;
let onscreenShift = false;
let desktopFailures=0, desktopStalled=true;
let desktopRecoveryUntil=0;

function updateDesktopStatusVisibility() {
  $('desktop-status-row').hidden=$('desktop-status').hidden&&$('desktop-held').hidden;
}

function setDesktopStatus(message='') {
  const status=$('desktop-status');
  status.textContent=message;
  status.hidden=!message;
  updateDesktopStatusVisibility();
}

function desktopPost(action, fields={}, viewer=desktopViewer, keepalive=false) {
  return request('/api/desktop', {
    method:'POST', headers:{'Content-Type':'application/json','X-Hector-Desktop':viewer},
    body:JSON.stringify({action,...fields}), keepalive
  });
}

function displayDesktopMode() {
  $('desktop-connect').hidden=Boolean(desktopViewer);
  $('desktop-connect').disabled=desktopConnecting;
  $('desktop-control').hidden=$('desktop-disconnect').hidden=$('desktop-fullscreen').hidden=!desktopViewer;
  $('desktop-actions').classList.toggle('is-connected',Boolean(desktopViewer));
  $('desktop-display').hidden=!desktopViewer;
  $('desktop-control').textContent=desktopControl?'Return to View Only':'Take Control';
  $('desktop-mode').textContent=desktopViewer?(desktopStalled?'RECONNECTING':desktopControl?'IN CONTROL':'VIEW ONLY'):'DISCONNECTED';
  $('desktop-hold-hint').hidden=!desktopControl;
  for(const button of desktopShiftButtons) {
    button.hidden=!desktopViewer;
    button.disabled=!desktopControl||desktopStalled;
  }
  desktopCanvas.toggleAttribute('data-control',desktopControl&&!desktopStalled);
}

function showDesktopHold(held) {
  $('desktop-held').hidden=!held;
  if(held||desktopViewer&&desktopControl&&!desktopStalled) setDesktopStatus();
  else updateDesktopStatusVisibility();
  clearInterval(holdTimer);
  if(held) holdTimer=setInterval(()=>{
    if(!draining) queueDesktopInput('hold-renew');
  },750);
}

function clearDesktopHold() {
  holdRequested=false;
  shiftPoint=null;
  showDesktopHold(false);
}

function cancelDesktopHold() {
  if(holdRequested) queueDesktopInput('hold-cancel');
  clearDesktopHold();
}

function setOnscreenShift(active) {
  onscreenShift=active;
  for(const button of desktopShiftButtons) button.setAttribute('aria-pressed',String(active));
}

function cancelOnscreenShift() {
  setOnscreenShift(false);
  cancelDesktopHold();
}

function nativeFullscreen() {
  return (document.fullscreenElement||document.webkitFullscreenElement)===desktopPanel;
}

function desktopFullscreenState(fallback=desktopPanel.classList.contains('desktop-viewport')) {
  const native=nativeFullscreen();
  fallback=fallback&&!native;
  desktopPanel.classList.toggle('desktop-viewport',fallback);
  desktopPanel.classList.toggle('desktop-expanded',native||fallback);
  document.body.classList.toggle('desktop-viewport-open',fallback);
  $('desktop-fullscreen').textContent=native||fallback?'Exit full screen':'Full screen';
}

async function exitDesktopFullscreen() {
  desktopFullscreenState(false);
  if(nativeFullscreen()) {
    try { await (document.exitFullscreen||document.webkitExitFullscreen).call(document); }
    catch { desktopFullscreenState(false); }
  }
}

async function toggleDesktopFullscreen() {
  if(desktopPanel.classList.contains('desktop-expanded')) {
    await exitDesktopFullscreen();
    return;
  }
  const requestFull=desktopPanel.requestFullscreen||desktopPanel.webkitRequestFullscreen;
  if(requestFull && document.fullscreenEnabled!==false) {
    try { await requestFull.call(desktopPanel); }
    catch { /* Fill the viewport when the browser declines fullscreen. */ }
  }
  desktopFullscreenState(Boolean(desktopViewer));
}

function stopDesktop(notify=true, message='Viewer disconnected.') {
  const viewer=desktopViewer;
  desktopRun++;
  desktopConnecting=false;
  desktopViewer='';
  desktopControl=false;
  desktopFailures=0;
  desktopRecoveryUntil=0;
  desktopStalled=true;
  pointerId=null;
  pointerQueue=[];
  setOnscreenShift(false);
  clearDesktopHold();
  clearTimeout(desktopTimer);
  clearInterval(pointerTimer);
  desktopContext.clearRect(0,0,1280,720);
  exitDesktopFullscreen();
  setDesktopStatus(message);
  displayDesktopMode();
  if(viewer && notify && csrf) desktopPost('close',{},viewer,true).catch(()=>{});
}

async function desktopFrame(run) {
  if(run!==desktopRun || !desktopViewer) return;
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),4500);
  try {
    const response=await request('/api/desktop/frame',{headers:{'X-Hector-Desktop':desktopViewer},signal:controller.signal});
    const receivedAt=performance.now();
    const wait=Number(response.headers.get('X-Hector-Next-Frame-Ms') ?? 180);
    if(response.status!==204) {
      const frame=await createImageBitmap(await response.blob());
      if(run===desktopRun) {
        desktopContext.drawImage(frame,0,0,1280,720);
        const recovering=desktopStalled;
        desktopStalled=false;
        desktopFailures=0;
        desktopRecoveryUntil=0;
        if(recovering) {
          setDesktopStatus();
          displayDesktopMode();
        }
      }
      frame.close();
    }
    const delay=Math.max(16,wait-(performance.now()-receivedAt));
    if(run===desktopRun) desktopTimer=setTimeout(()=>desktopFrame(run),delay);
  } catch(error) {
    if(run!==desktopRun) return;
    const transient=!error.status||error.status>=500;
    if(transient&&!desktopRecoveryUntil) desktopRecoveryUntil=performance.now()+15000;
    if(transient&&performance.now()<desktopRecoveryUntil) {
      desktopFailures++;
      desktopStalled=true;
      clearInterval(pointerTimer);
      pointerId=null;
      pointerQueue=[];
      cancelOnscreenShift();
      if(desktopControl) desktopPost('release-input').catch(()=>{});
      setDesktopStatus('Reconnecting to Hector’s screen…');
      displayDesktopMode();
      desktopTimer=setTimeout(()=>desktopFrame(run),Math.min(1500,250*desktopFailures));
    } else stopDesktop(true,error.message);
  } finally {
    clearTimeout(timeout);
  }
}

async function connectDesktop() {
  if(desktopViewer || desktopConnecting || !csrf || $('remote-desktop').hidden || document.hidden) return;
  const run=++desktopRun;
  desktopConnecting=true;
  displayDesktopMode();
  setDesktopStatus('Connecting to Hector…');
  try {
    const {viewer}=await (await desktopPost('open')).json();
    if(run!==desktopRun) {
      if(csrf) desktopPost('close',{},viewer,true).catch(()=>{});
      return;
    }
    desktopViewer=viewer;
    setDesktopStatus();
    desktopFrame(run);
  } catch(error) {
    if(run===desktopRun) setDesktopStatus(error.message);
  } finally {
    if(run===desktopRun) {
      desktopConnecting=false;
      displayDesktopMode();
    }
  }
}

function showWorkspace() {
  const route=location.hash || '#overview';
  const navRoute=route==='#breakdown'?'#overview':route;
  const signedIn=!$('dashboard').hidden;
  const remote=route==='#remote-desktop' && signedIn;
  const presetsPage=route==='#presets' && signedIn;
  const audioPage=route==='#audio-library' && signedIn;
  const modulePage=route==='#module-manager' && signedIn && info.modules;
  const revealDetails=route==='#breakdown' && signedIn && $('storage-view').hidden;
  const enterPresets=presetsPage && $('presets').hidden;
  const enterAudio=audioPage && $('audio-library').hidden;
  const enterModules=modulePage && $('module-manager').hidden;
  $('storage-view').hidden=remote||presetsPage||audioPage||modulePage;
  $('storage-heading').hidden=!signedIn||$('storage-view').hidden;
  $('remote-desktop').hidden=!remote;
  $('presets').hidden=!presetsPage;
  $('audio-library').hidden=!audioPage;
  $('module-manager').hidden=!modulePage;
  if(enterAudio) { window.scrollTo(0,0); loadAudio(); }
  if(enterModules) { window.scrollTo(0,0); loadModules(); }
  if(enterPresets) window.scrollTo(0,0);
  if(revealDetails) $('breakdown').scrollIntoView();
  document.querySelectorAll('.nav-link').forEach(link=>{
    const active=signedIn&&link.hash===navRoute;
    link.classList.toggle('active',active);
    if(active) link.setAttribute('aria-current','page');
    else link.removeAttribute('aria-current');
  });
  if(remote) connectDesktop();
  else if(desktopViewer || desktopConnecting) stopDesktop();
}

async function queueDesktopInput(action, fields={}, transition=true) {
  if(!desktopControl) return;
  if(!transition && pointerQueue.length && !pointerQueue.at(-1).transition) pointerQueue.pop();
  const previous=pointerQueue.at(-1);
  if(action==='key'&&fields.key==='text'&&previous?.action==='key'&&previous.fields.key==='text'&&
      Array.from(previous.fields.text+fields.text).length<=32) previous.fields.text+=fields.text;
  else pointerQueue.push({action,fields,transition});
  if(draining) return;
  draining=true;
  try {
    while(pointerQueue.length && desktopControl) {
      const run=desktopRun;
      const input=pointerQueue.shift();
      try {
        const response=await desktopPost(input.action,input.fields);
        if(['shift-click','hold-start','hold-apply','hold-renew'].includes(input.action)) {
          const {held}=await response.json();
          if(run===desktopRun && desktopControl && holdRequested) {
            if(held) showDesktopHold(true);
            else {
              clearDesktopHold();
              if(input.action==='hold-start') {
                pointerQueue=pointerQueue.filter(item=>!['hold-apply','hold-renew'].includes(item.action));
                setDesktopStatus('A hold could not start here. Return to the patch canvas and tap a module or input label.');
              } else setOnscreenShift(false);
            }
          }
        }
      } catch(error) {
        if(run!==desktopRun||!desktopControl) continue;
        if(!error.status||error.status>=500) {
          pointerQueue=[];
          pointerId=null;
          clearInterval(pointerTimer);
          setOnscreenShift(false);
          clearDesktopHold();
          desktopPost('release-input').catch(()=>{});
          setDesktopStatus('Touch input was interrupted. Please try the gesture again.');
        } else stopDesktop(true,error.message);
      }
    }
  } finally {
    draining=false;
  }
}

function screenPoint(event,down) {
  const rect=desktopCanvas.getBoundingClientRect();
  const scale=Math.min(rect.width/1280,rect.height/720);
  return {
    x:Math.max(0,Math.min(1279,Math.floor((event.clientX-rect.left-(rect.width-1280*scale)/2)/scale))),
    y:Math.max(0,Math.min(719,Math.floor((event.clientY-rect.top-(rect.height-720*scale)/2)/scale))),
    down
  };
}

function insideDesktopScreen(event) {
  const rect=desktopCanvas.getBoundingClientRect();
  const scale=Math.min(rect.width/1280,rect.height/720);
  const x=event.clientX-rect.left-(rect.width-1280*scale)/2;
  const y=event.clientY-rect.top-(rect.height-720*scale)/2;
  return x>=0&&y>=0&&x<1280*scale&&y<720*scale;
}

desktopCanvas.addEventListener('pointerdown',event=>{
  if(!desktopControl || desktopStalled || pointerId!==null || event.button!==0) return;
  event.preventDefault();
  desktopCanvas.focus({preventScroll:true});
  try { desktopCanvas.setPointerCapture(event.pointerId); } catch { /* Touch capture may already be managed by the browser. */ }
  pointerId=event.pointerId;
  shiftGesture=event.shiftKey||onscreenShift;
  if(shiftGesture) {
    shiftPoint=screenPoint(event,true);
    shiftTouchOrigin={x:event.clientX,y:event.clientY};
    shiftAlreadyHeld=holdRequested;
    if(!shiftAlreadyHeld) {
      holdRequested=true;
      queueDesktopInput('hold-start',{x:shiftPoint.x,y:shiftPoint.y});
    }
    return;
  }
  cancelDesktopHold();
  lastPoint=screenPoint(event,true);
  queueDesktopInput('pointer',lastPoint);
  pointerTimer=setInterval(()=>queueDesktopInput('pointer',lastPoint,false),400);
});
desktopCanvas.addEventListener('pointermove',event=>{
  if(event.pointerId!==pointerId || shiftGesture) return;
  lastPoint=screenPoint(event,true);
  queueDesktopInput('pointer',lastPoint,false);
});
for(const name of ['pointerup','pointercancel']) {
  window.addEventListener(name,event=>{
    if(event.pointerId!==pointerId) return;
    pointerId=null;
    if(shiftGesture) {
      shiftGesture=false;
      const start=shiftPoint;
      const origin=shiftTouchOrigin;
      shiftPoint=null;
      shiftTouchOrigin=null;
      if(name==='pointerup' && (event.shiftKey||onscreenShift) && start && holdRequested && insideDesktopScreen(event)) {
        const {x,y}=screenPoint(event,false);
        const dragged=origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>24;
        if(shiftAlreadyHeld||dragged) {
          queueDesktopInput('hold-apply',{x,y});
        }
      } else if(holdRequested) cancelDesktopHold();
      return;
    }
    clearInterval(pointerTimer);
    queueDesktopInput('pointer',{...lastPoint,down:false});
  });
}
$('desktop-connect').addEventListener('click',connectDesktop);
$('desktop-disconnect').addEventListener('click',()=>stopDesktop());
for(const button of desktopShiftButtons) button.addEventListener('click',()=>{
  if(onscreenShift) cancelOnscreenShift();
  else if(desktopControl) setOnscreenShift(true);
});
$('desktop-fullscreen').addEventListener('click',toggleDesktopFullscreen);
for(const event of ['fullscreenchange','webkitfullscreenchange']) {
  document.addEventListener(event,()=>desktopFullscreenState());
}
$('desktop-control').addEventListener('click',async()=>{
  const run=desktopRun, button=$('desktop-control');
  button.disabled=true;
  try {
    await desktopPost(desktopControl?'view':'take');
    if(run!==desktopRun) return;
    desktopControl=!desktopControl;
    setDesktopStatus();
    if(!desktopControl) {
      clearInterval(pointerTimer);
      pointerId=null;
      pointerQueue=[];
      setOnscreenShift(false);
      clearDesktopHold();
    }
    displayDesktopMode();
    if(desktopControl) desktopCanvas.focus({preventScroll:true});
  } catch(error) {
    if(run===desktopRun) stopDesktop(true,error.message);
  } finally {
    button.disabled=false;
  }
});
const desktopEditingKeys=new Set(['Backspace','Delete','Enter','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','Tab','Escape']);
function desktopTyping() {
  return desktopControl&&!desktopStalled&&desktopViewer&&document.activeElement===desktopCanvas&&!document.hidden;
}
function desktopText(text) {
  const characters=Array.from(text.replace(/[\r\n\t]/g,' ').replace(/[\u0000-\u001f\u007f]/g,''));
  for(let offset=0;offset<characters.length;offset+=32) {
    queueDesktopInput('key',{key:'text',text:characters.slice(offset,offset+32).join('')});
  }
}
desktopCanvas.addEventListener('keydown',event=>{
  if(!desktopTyping()||event.isComposing||event.altKey) return;
  if(event.ctrlKey||event.metaKey) {
    if(event.key.toLowerCase()==='a') {
      event.preventDefault();
      queueDesktopInput('key',{key:'SelectAll'});
    }
    return;
  }
  if(event.key==='Escape'&&(nativeFullscreen()||desktopPanel.classList.contains('desktop-viewport'))) {
    event.preventDefault();
    exitDesktopFullscreen();
    return;
  }
  if(desktopEditingKeys.has(event.key)) {
    event.preventDefault();
    queueDesktopInput('key',{key:event.key,shift:event.shiftKey});
  } else if(Array.from(event.key).length===1) {
    event.preventDefault();
    desktopText(event.key);
  }
});
desktopCanvas.addEventListener('paste',event=>{
  if(!desktopTyping()) return;
  const text=event.clipboardData?.getData('text/plain');
  if(text) {
    event.preventDefault();
    desktopText(text);
  }
});
desktopCanvas.addEventListener('compositionend',event=>{
  if(desktopTyping()&&event.data) desktopText(event.data);
});
window.addEventListener('keyup',event=>{
  if(event.key==='Shift' && !event.shiftKey && !onscreenShift) cancelDesktopHold();
  if(event.key==='Escape' && desktopPanel.classList.contains('desktop-viewport')) exitDesktopFullscreen();
});
window.addEventListener('blur',()=>{
  if(pointerId!==null&&!shiftGesture&&lastPoint) queueDesktopInput('pointer',{...lastPoint,down:false});
  pointerId=null;
  shiftGesture=false;
  shiftTouchOrigin=null;
  clearInterval(pointerTimer);
  cancelOnscreenShift();
});
window.addEventListener('hashchange',showWorkspace);
window.addEventListener('pagehide',()=>stopDesktop());
document.addEventListener('visibilitychange',()=>{
  if(document.hidden && (desktopViewer || desktopConnecting)) stopDesktop();
});
let managerTab='installed',repositoryState={repositories:[],creators:[]},repositoryTimer,repositoryRun=0;
let openRepositoryModule='',repositoryQueueBusy=false;
function setManagerTab(tab) {
  if(!['installed','repositories','releases'].includes(tab))return;
  managerTab=tab;
  for(const name of ['installed','repositories','releases']) {
    $('manager-'+name+'-view').hidden=name!==tab;
    $('manager-tab-'+name).setAttribute('aria-selected',String(name===tab));
  }
  if(tab==='repositories')loadRepositories();
  else clearTimeout(repositoryTimer);
}
for(const button of document.querySelectorAll('[data-manager-tab]'))button.addEventListener('click',()=>setManagerTab(button.dataset.managerTab));

function versionOrder(a,b) {
  const tokens=value=>String(value).match(/\d+|[a-z]+/gi)||[];
  const x=tokens(a),y=tokens(b);
  for(let i=0;i<Math.max(x.length,y.length);i++) {
    const left=x[i],right=y[i];
    if(left===undefined)return 1;if(right===undefined)return -1;
    if(left===right)continue;
    const numbers=/^\d+$/.test(left)&&/^\d+$/.test(right);
    return numbers?Number(left)-Number(right):left.localeCompare(right);
  }
  return 0;
}
function repositoryButton(label,action,style='text-button') {
  const button=node('button',label,style);button.type='button';button.addEventListener('click',action);return button;
}
async function repositoryAction(action,fields,kind='repository') {
  return (await request((kind==='creator'?'/api/creators/':'/api/repositories/')+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)})).json();
}
function renderRepositories() {
  const repos=[...(repositoryState.creators||[]),...repositoryState.repositories];
  const openRepos=new Set(Array.from($('repository-connections').querySelectorAll('details[open]'),row=>row.dataset.repository));
  const connections=repos.map(repo=>{
    const details=node('details',undefined,'repository-connection');details.dataset.repository=repo.id;details.open=openRepos.has(repo.id)||(repo.default&&!$('repository-connections').dataset.initialized);if(repo.default)details.classList.add('creator-default');
    const summary=node('summary');const identity=node('span',undefined,'creator-label');identity.append(node('strong',repo.default?'Jahmontee':repo.slug));if(repo.default)identity.append(node('span','Default creator','creator-badge'));summary.append(identity,node('span',repo.status==='checking'?'Checking…':repo.status==='error'?'Check needed':repo.status==='connected'?'Not checked':repo.modules.length+' verified '+(repo.modules.length===1?'module':'modules'),'muted'));
    const body=node('div',undefined,'repository-connection-body');body.append(node('p',repo.url,'muted'));if(repo.default)body.append(node('p','Included with Poly / View. No setup needed.','creator-included'));if(repo.limited)body.append(node('p','The check reached its scan limit. Add a repository directly to check its releases individually.','creator-limit'));
    if(repo.status==='checking')body.append(node('p',repo.progress||'Checking releases…','muted'));
    if(repo.error)body.append(node('p',repo.error,'error'));
    if(repo.unsigned||repo.rejected)body.append(node('p',`${repo.unsigned||0} unsigned and ${repo.rejected||0} unsupported or unverified packages excluded.`,'muted'));
    const actions=node('div',undefined,'repository-actions');
    const check=repositoryButton('Check for updates',async()=>{
      check.disabled=true;
      try{repositoryState=await repositoryAction('check',{id:repo.id},repo.kind);renderRepositories();pollRepositories();}
      catch(error){$('repository-error').textContent=error.message;check.disabled=false;}
    });check.disabled=repo.status==='checking';
    actions.append(check);
    if(!repo.default)actions.append(repositoryButton(repo.kind==='creator'?'Remove creator':'Remove repository',async()=>{
      try{repositoryState=await repositoryAction('remove',{id:repo.id},repo.kind);renderRepositories();toast('Source removed from the list. Installed modules stay on Hector.');}
      catch(error){$('repository-error').textContent=error.message;}
    }));body.append(actions);details.append(summary,body);return details;
  });
  $('repository-connections').replaceChildren(...connections);$('repository-connections').dataset.initialized='true';
  if(!repos.length)$('repository-connections').append(node('p','No repositories saved.','empty'));
  $('repository-count').textContent=repos.length;
  $('repository-check-all').disabled=!repos.length||repos.some(repo=>repo.status==='checking');
  const grouped=new Map();
  for(const repo of repos)for(const module of repo.modules) {
    const key=module.publisher+':'+module.id;
    if(!grouped.has(key))grouped.set(key,{...module,versions:[]});
    const item=grouped.get(key);
    for(const release of module.versions)if(!item.versions.some(row=>row.sha256===release.sha256))item.versions.push({...release,repository:repo.id,sourceKind:repo.kind||'repository'});
  }
  const modules=Array.from(grouped.values()).sort((a,b)=>a.name.localeCompare(b.name));
  const includeBeta=$('repository-beta').checked;
  const rows=[];
  for(const module of modules) {
    const versions=module.versions.filter(row=>includeBeta||!row.beta).sort((a,b)=>versionOrder(b.version,a.version)||b.published.localeCompare(a.published));
    if(!versions.length)continue;
    const installed=[...(moduleState?.installed||[]),...(moduleState?.community_removal||[])].find(row=>row.id===module.id&&row.status==='installed');
    let selected=versions[0];
    const key=module.publisher+':'+module.id;
    const details=node('details',undefined,'repository-module');details.dataset.module=key;details.open=openRepositoryModule===key;
    const summary=node('summary');
    const name=node('strong',module.name);const releaseLabel=node('span',selected.version+(selected.beta?' · Beta':''),'repository-version');
    const status=node('span',installed?(installed.version===selected.version?'Installed':'Update available'):'Available','repository-status');
    if(installed&&installed.version!==selected.version)status.classList.add('update');
    summary.append(name,releaseLabel,status);
    const body=node('div',undefined,'repository-module-body');
    const verification=node('p','✓ Verified signature','repository-signature');body.append(verification);
    const versionLine=node('div',undefined,'repository-version-line');
    const installedValue=node('div');installedValue.append(node('small','INSTALLED'),node('strong',installed?.version||'Not installed'));
    const availableValue=node('div');const availableText=node('strong',selected.version);availableValue.append(node('small','AVAILABLE'),availableText);
    const action=repositoryButton('',async()=>{
      if(repositoryQueueBusy||modulesBusy||batchActive())return;
      repositoryQueueBusy=true;action.disabled=true;action.textContent='Checking and downloading…';
      try {
        const state=await repositoryAction('queue',{id:selected.repository,key:selected.key},selected.sourceKind);
        renderModules(state);setManagerTab('releases');
        toast('Verified package added to Release packages. Review it before installing.');
      } catch(error){$('repository-error').textContent=error.message;}
      finally{repositoryQueueBusy=false;updateAction();}
    },'button install');
    function updateAction() {
      const same=installed?.version===selected.version;
      const older=installed&&versionOrder(selected.version,installed.version)<0;
      action.disabled=repositoryQueueBusy||modulesBusy||batchActive()||same||older;
      action.textContent=same?'Installed':older?'Older release':'Review '+(installed?'update':'install');
      availableText.textContent=selected.version;
    }
    updateAction();versionLine.append(installedValue,availableValue,action);body.append(versionLine);
    body.append(node('p','Compatibility checked before installation · Presets retained · Restart required to apply changes','muted'));
    if(versions.length>1) {
      const history=node('details',undefined,'repository-version-history');history.append(node('summary','Other versions'));
      const label=node('label','Choose a release');const select=node('select');select.setAttribute('aria-label',module.name+' release');
      for(const release of versions)select.append(new Option(release.version+(release.beta?' · Beta':''),release.key));
      select.addEventListener('change',()=>{selected=versions.find(row=>row.key===select.value);updateAction();notesText.textContent=selected.notes||'No release notes provided.';});
      label.append(select);history.append(label);body.append(history);
    }
    const notes=node('details',undefined,'repository-release-notes');const notesText=node('p',selected.notes||'No release notes provided.','muted');notes.append(node('summary','Release notes'),notesText);body.append(notes);
    body.append(node('p',selected.repository_slug?'https://github.com/'+selected.repository_slug:repos.find(repo=>repo.id===selected.repository)?.url||'','repository-source'));
    details.append(summary,body);
    details.addEventListener('toggle',()=>{
      if(!details.isConnected)return;
      if(details.open){openRepositoryModule=key;for(const other of $('repository-modules').children)if(other!==details)other.open=false;}
      else if(openRepositoryModule===key)openRepositoryModule='';
      $('repository-collapse').hidden=!openRepositoryModule;
    });rows.push(details);
  }
  $('repository-modules').replaceChildren(...rows);
  $('repository-module-count').textContent=rows.length;
  $('repository-empty').hidden=!!rows.length;
  $('repository-empty').textContent=!repos.length?'Choose Check for updates to find verified modules from your creators and repositories.':
    repos.some(repo=>repo.status==='checking')?'Checking repository releases…':
    modules.length&&!includeBeta?'Only beta / test releases are available. Enable them to see the module list.':'No verified module packages found. Unsigned and unverified releases are excluded.';
  $('repository-collapse').hidden=!rows.some(row=>row.open);
}
function pollRepositories() {
  clearTimeout(repositoryTimer);
  if(managerTab==='repositories'&&location.hash==='#module-manager'&&!$('dashboard').hidden&&[...(repositoryState.creators||[]),...repositoryState.repositories].some(repo=>repo.status==='checking'))repositoryTimer=setTimeout(loadRepositories,1200);
}
async function loadRepositories() {
  const run=++repositoryRun;
  try{const state=await(await request('/api/repositories')).json();if(run!==repositoryRun)return;repositoryState=state;$('repository-error').textContent='';renderRepositories();pollRepositories();}
  catch(error){if(run===repositoryRun)$('repository-error').textContent=error.message;}
}
$('repository-beta').addEventListener('change',()=>{openRepositoryModule='';renderRepositories();});
$('repository-collapse').addEventListener('click',()=>{openRepositoryModule='';for(const row of $('repository-modules').children)row.open=false;$('repository-collapse').hidden=true;});
$('repository-add-open').addEventListener('click',()=>{$('repository-add-error').textContent='';$('repository-url').value='';$('repository-add-dialog').showModal();});
$('repository-add-cancel').addEventListener('click',()=>$('repository-add-dialog').close());
$('repository-check-all').addEventListener('click',async()=>{
  $('repository-check-all').disabled=true;
  const errors=[];
  for(const repo of [...(repositoryState.creators||[]),...repositoryState.repositories]) {
    if(repo.status==='checking')continue;
    try{await repositoryAction('check',{id:repo.id},repo.kind);}
    catch(error){errors.push(error.message);}
  }
  await loadRepositories();
  if(errors.length)$('repository-error').textContent=errors.join(' ');
});
$('creator-add-open').addEventListener('click',()=>{$('creator-add-error').textContent='';$('creator-url').value='';$('creator-add-dialog').showModal();});
$('creator-add-cancel').addEventListener('click',()=>$('creator-add-dialog').close());
$('creator-add-form').addEventListener('submit',async event=>{
  event.preventDefault();$('creator-add-submit').disabled=true;
  try{repositoryState=await repositoryAction('add',{url:$('creator-url').value},'creator');$('creator-add-dialog').close();renderRepositories();toast('Creator saved. Choose Check for updates when you want to contact GitHub.');}
  catch(error){$('creator-add-error').textContent=error.message;}
  finally{$('creator-add-submit').disabled=false;}
});
$('repository-add-form').addEventListener('submit',async event=>{
  event.preventDefault();$('repository-add-submit').disabled=true;
  try{repositoryState=await repositoryAction('add',{url:$('repository-url').value});$('repository-add-dialog').close();renderRepositories();toast('Repository saved. Choose Check for updates when you want to contact GitHub.');}
  catch(error){$('repository-add-error').textContent=error.message;}
  finally{$('repository-add-submit').disabled=false;}
});
deviceReconnect.resume();
(async()=>{try{info=await(await fetch('/api/info')).json();$('installed-version').textContent='v'+info.version;$('module-nav').hidden=!info.modules;if(info.preview){$('mode').textContent='LOCAL PREVIEW';$('preview-banner').hidden=false;$('preview-banner').textContent=info.preview_note;$('preview-login').hidden=false;}try{const r=await fetch('/api/session');if(r.ok){csrf=(await r.json()).csrf;await load();}else locked();}catch{locked();}}catch{locked();$('login-error').textContent='Could not reach the manager. Refresh to reconnect.';}})();
}
