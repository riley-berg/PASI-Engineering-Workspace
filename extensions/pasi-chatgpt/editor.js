(() => {
  "use strict";
  const id = new URLSearchParams(location.search).get("id");
  const TYPES = {
    source: "pasi.userscript.source.get",
    save: "pasi.userscript.source.save",
    vcsConfig: "pasi.userscript.vcs.config",
    vcsFetch: "pasi.userscript.vcs.fetch",
    vcsPull: "pasi.userscript.vcs.pull",
    vcsPush: "pasi.userscript.vcs.push",
  };
  const $ = (id) => document.getElementById(id);
  let remoteSource = "";
  function send(type,payload={}) {
    return new Promise((resolve,reject)=>{
      chrome.runtime.sendMessage({type,...payload},response=>{
        const error=chrome.runtime.lastError;
        if(error)return reject(new Error(error.message));
        if(!response?.ok)return reject(new Error(response?.error||"PASI request failed"));
        resolve(response);
      });
    });
  }
  function status(message,error=false){$("status").textContent=message;$("status").style.color=error?"#b91c1c":"";}
  function renderDiff(left,right){
    const rows=globalThis.PASIUserScriptDiff.compare(left,right);
    $("diff").replaceChildren();
    for(const row of rows){
      const div=document.createElement("div");
      div.className=row.type;
      div.textContent=(row.type==="add"?"+ " : row.type==="remove"?"- ":"  ")+(row.type==="remove"?row.left:row.right);
      $("diff").append(div);
    }
  }
  async function load(){
    if(!id){status("Missing script id",true);return;}
    const result=await send(TYPES.source,{id});
    $("title").textContent=result.id;
    $("meta").textContent=`revision ${result.revision} · ${result.typeScript?"TypeScript":"JavaScript"}`;
    $("source").value=result.source;
    $("typescript").checked=result.typeScript;
    const config=await chrome.runtime.sendMessage({type:"pasi.userscript.sync.status"}).catch(()=>null);
    void config;
  }
  $("save").onclick=async()=>{
    try{
      const result=await send(TYPES.save,{id,source:$("source").value,typeScript:$("typescript").checked});
      $("meta").textContent=`revision ${result.script?.revision||""} · ${$("typescript").checked?"TypeScript":"JavaScript"}`;
      status("Saved.");
    }catch(e){status(String(e.message||e),true);}
  };
  $("save-vcs").onclick=async()=>{
    try{
      const result=await send(TYPES.vcsConfig,{config:{
        provider:$("provider").value,owner:$("owner").value,repo:$("repo").value,path:$("path").value,branch:$("branch").value,baseUrl:$("baseUrl").value
      },token:$("token").value});
      $("vcs-status").textContent=result.config?"Repository settings saved.":"";
      $("token").value="";
    }catch(e){status(String(e.message||e),true);}
  };
  $("pull").onclick=async()=>{
    try{
      const result=await send(TYPES.vcsFetch,{id});
      remoteSource=result.remote.source||"";
      renderDiff($("source").value,remoteSource);
      $("apply").disabled=false;
      status("Remote copy loaded for comparison.");
    }catch(e){status(String(e.message||e),true);}
  };
  $("apply").onclick=async()=>{
    try{
      if(!confirm("Replace the local authoring source with the remote copy?"))return;
      await send(TYPES.vcsPull,{id});
      await load();
      $("apply").disabled=true;
      status("Remote copy applied.");
    }catch(e){status(String(e.message||e),true);}
  };
  $("push").onclick=async()=>{
    try{
      const message=prompt("Commit message","Update userscript");
      if(message===null)return;
      await send(TYPES.save,{id,source:$("source").value,typeScript:$("typescript").checked});
      const result=await send(TYPES.vcsPush,{id,message});
      status("Pushed "+(result.remote?.commit||result.remote?.sha||"remote update")+"." );
    }catch(e){status(String(e.message||e),true);}
  };
  load().catch(e=>status(String(e.message||e),true));
})();
