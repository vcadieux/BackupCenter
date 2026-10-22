'use strict';
(()=>{
 const $=s=>document.querySelector(s),t=BackupCenterI18n.t;
 let terminal=null,fit=null,id=null,offset=0,pollTimer=null,inputTimer=null,inputBuffer='',inputChain=Promise.resolve(),opening=false,epoch=0,observer=null;
 const request=async(path,data)=>{
  const options={credentials:'same-origin',headers:{'X-BackupCenter-Language':BackupCenterI18n.language}};
  if(data!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.headers['X-CSRF-Token']=state.csrf;options.body=JSON.stringify(data);}
  const r=await fetch('/api/'+path,options);if(!r.ok){let message=t('Connexion au terminal interrompue. Fermez puis rouvrez le terminal.');try{const value=await r.json();if(typeof value.error==='string')message=value.error.slice(0,1000);}catch{}const error=Error(message);error.status=r.status;throw error;}
  return r.json();
 };
 function send(data){
  const current=id;if(!current)return;
  inputChain=inputChain.then(()=>request('terminals/'+current+'/input',data)).catch(error=>{stop();toast(error.message,true);});
 }
 function flush(){clearTimeout(inputTimer);if(inputBuffer){const data=inputBuffer;inputBuffer='';send({data});}}
 function stop(){clearTimeout(pollTimer);clearTimeout(inputTimer);inputBuffer='';if(terminal)terminal.options.disableStdin=true;}
 async function poll(currentEpoch){
  if(!id || currentEpoch!==epoch)return;
  try{
   const result=await request('terminals/'+id+'?offset='+offset);
   if(currentEpoch!==epoch)return;
   if(result.dropped)terminal.writeln(t('Une partie de la sortie a été écartée (limite mémoire).'));
   const bytes=Uint8Array.from(atob(result.data),x=>x.charCodeAt(0));if(bytes.length)terminal.write(bytes);offset=result.offset;
   if(result.closed){stop();$('#terminal-status').textContent=t('Terminal fermé. Les tâches détachées sur le serveur peuvent continuer.');return;}
   pollTimer=setTimeout(()=>poll(currentEpoch),250);
  }catch(error){if(currentEpoch===epoch){stop();if(error.status===401){await close();$('#terminal-dialog').close();await refresh();}toast(error.message,true);}}
 }
 async function close(){
  const current=id;id=null;++epoch;stop();if(observer){observer.disconnect();observer=null;}
  if(terminal){terminal.dispose();terminal=null;}$('#terminal-screen').replaceChildren();$('#terminal-status').textContent='';
  if(current)try{await request('terminals/'+current+'/close',{});}catch{}
 }
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-terminal]');if(!button || opening)return;
  action(async()=>{
   opening=true;
   try{
    await close();const server=state.servers.find(x=>x.id===button.dataset.terminal);
    $('#terminal-title').textContent=t('Terminal SSH')+' · '+server.name;$('#terminal-status').textContent=t('Connexion manuelle : utilisateur puis mot de passe du VPS.');
    $('#terminal-dialog').showModal();const currentEpoch=++epoch;
    terminal=new Terminal({cursorBlink:true,scrollback:1500,fontSize:13,allowProposedApi:false,theme:{background:'#101e2c',foreground:'#e8f0e8'},linkHandler:{activate:()=>{}}});
    terminal.parser.registerOscHandler(52,()=>true);
    fit=new FitAddon.FitAddon();terminal.loadAddon(fit);terminal.open($('#terminal-screen'));fit.fit();
    const result=await request('servers/'+server.id+'/terminal',{version:server.version,cols:terminal.cols,rows:terminal.rows});
    if(currentEpoch!==epoch){await request('terminals/'+result.id+'/close',{});return;}
    id=result.id;offset=0;inputChain=Promise.resolve();
    terminal.onData(data=>{inputBuffer+=data;if(inputBuffer.length>=2048)flush();else{clearTimeout(inputTimer);inputTimer=setTimeout(flush,30);}});
    terminal.onResize(size=>{if(id)send(size);});
    observer=new ResizeObserver(()=>{if(terminal && $('#terminal-dialog').open)fit.fit();});observer.observe($('#terminal-screen'));
    terminal.focus();poll(currentEpoch);
   }catch(error){stop();toast(error.message,true);}finally{opening=false;}
  },button);
 });
 $('#terminal-disconnect').addEventListener('click',()=>action(async()=>{await close();$('#terminal-dialog').close();}));
 $('#terminal-dialog').addEventListener('close',()=>{close();});
 window.addEventListener('pagehide',()=>{
  if(id)fetch('/api/terminals/'+id+'/close',{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:'{}'}).catch(()=>{});
 });
})();
