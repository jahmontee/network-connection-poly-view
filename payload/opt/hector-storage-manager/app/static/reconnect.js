'use strict';
// Poll local readiness only. Never repeat an install/remove/restart request.
class HectorReconnect {
  constructor({readInfo, waiting, ready, uncertain,
               schedule=(callback, delay)=>setTimeout(callback, delay), cancel=timer=>clearTimeout(timer),
               now=Date.now, storage=null}) {
    Object.assign(this,{readInfo,waiting,ready,uncertain,schedule,cancel,now,storage});
    this.current=null; this.timer=null; this.generation=0; this.inFlight=null;
  }
  arm(boot, started=this.now()) {
    if(this.current||typeof boot!=='string'||!(/^[a-f0-9-]{36}$/.test(boot))) return;
    this.current={boot,started};
    try {this.storage?.setItem('poly-view-restart',JSON.stringify(this.current));}catch{}
    const generation=++this.generation;
    this.timer=this.schedule(()=>this.poll(generation),1500);
  }
  resume() {
    try {
      const value=JSON.parse(this.storage?.getItem('poly-view-restart')||'null');
      if(value&&typeof value.boot==='string'&&Number.isFinite(value.started)&&
         this.now()-value.started>=0&&this.now()-value.started<600000) {
        this.arm(value.boot,value.started);
        if(this.current) {this.connectionLost();return true;}
      }
    }catch{}
    return false;
  }
  connectionLost(restarting=false) {
    if(!this.current)return;
    this.current.restarting=Boolean(this.current.restarting||restarting);
    this.waiting(this.current.restarting);
  }
  checkNow() {
    if(this.current&&this.inFlight!==this.generation) {
      this.cancel(this.timer);
      return this.poll(this.generation);
    }
  }
  stop() {
    this.cancel(this.timer);this.timer=null;this.current=null;this.generation++;
    try {this.storage?.removeItem('poly-view-restart');}catch{}
  }
  async poll(generation) {
    if(!this.current||generation!==this.generation||this.inFlight===generation)return;
    this.inFlight=generation;
    let info;
    try {
      info=await this.readInfo();
    }catch {
      if(this.current&&generation===this.generation)this.connectionLost();
    }finally {
      if(this.inFlight===generation)this.inFlight=null;
    }
    if(!this.current||generation!==this.generation)return;
    const elapsed=this.now()-this.current.started;
    if(typeof info?.boot_id==='string'&&/^[a-f0-9-]{36}$/.test(info.boot_id)) {
      if(info.boot_id!==this.current.boot) {
        this.stop();
        this.ready(info);
        return;
      }
    }
    if(elapsed>600000) {
      this.stop();this.uncertain();return;
    }
    this.timer=this.schedule(()=>this.poll(generation),1500);
  }
}
if(typeof module==='object'&&module.exports)module.exports={HectorReconnect};
else globalThis.HectorReconnect=HectorReconnect;
