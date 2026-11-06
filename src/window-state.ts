import {availableMonitors,getCurrentWindow,LogicalPosition,LogicalSize} from '@tauri-apps/api/window';

const KEY='sakuloca.window.v1';
type Saved={width:number;height:number;x?:number;y?:number};

/**
 * Restore the last usable size/position and persist later changes locally.
 * The position is only restored when it still overlaps a connected monitor.
 */
export async function initWindowState(){
  const window=getCurrentWindow();
  try{
    const saved=JSON.parse(localStorage.getItem(KEY)||'null') as Saved|null;
    if(saved&&Number.isFinite(saved.width)&&Number.isFinite(saved.height)){
      const width=Math.max(860,saved.width),height=Math.max(620,saved.height);
      if(Number.isFinite(saved.x)&&Number.isFinite(saved.y)&&await visible(saved.x!,saved.y!,width,height)){
        await window.setPosition(new LogicalPosition(saved.x!,saved.y!));
      }
      await window.setSize(new LogicalSize(width,height));
    }
  }catch{
    // A monitor may have been removed or the capability may be unavailable.
    // Keep the configured default instead of failing startup.
  }
  let timer:ReturnType<typeof setTimeout>|undefined;
  const save=()=>{
    clearTimeout(timer);
    timer=setTimeout(async()=>{
      try{
        if(await window.isMaximized()||await window.isMinimized())return;
        const [factor,size,position]=await Promise.all([window.scaleFactor(),window.innerSize(),window.outerPosition()]);
        const value:Saved={
          width:Math.round(size.width/factor),
          height:Math.round(size.height/factor),
          x:Math.round(position.x/factor),
          y:Math.round(position.y/factor),
        };
        localStorage.setItem(KEY,JSON.stringify(value));
      }catch{
        // Resizing during shutdown can fail; the next run keeps the last value.
      }
    },400);
  };
  const unlisten=[await window.onResized(save),await window.onMoved(save)];
  return ()=>{clearTimeout(timer);for(const stop of unlisten)stop()};
}

/** True when at least a title bar sized part of the saved rectangle is on screen. */
async function visible(x:number,y:number,width:number,height:number){
  try{
    const monitors=await availableMonitors();
    if(!monitors.length)return true;
    return monitors.some(monitor=>{
      const left=monitor.workArea.position.x/monitor.scaleFactor;
      const top=monitor.workArea.position.y/monitor.scaleFactor;
      const right=left+monitor.workArea.size.width/monitor.scaleFactor;
      const bottom=top+monitor.workArea.size.height/monitor.scaleFactor;
      const overlapX=Math.min(x+width,right)-Math.max(x,left);
      const overlapY=Math.min(y+height,bottom)-Math.max(y,top);
      return overlapX>=120&&overlapY>=40;
    });
  }catch{
    return true;
  }
}
