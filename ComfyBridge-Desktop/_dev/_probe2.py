import json,sys
sys.path.insert(0,'_dev')
from ui_shot import start, open_cap, pick
proc, cdp = start(True)
try:
    cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')",25)
    open_cap(cdp,'sheet.scene'); pick(cdp,'[data-mode="i2i"]'); cdp.pause(700)
    print(json.dumps(cdp.js("""(()=>{
      const rows=[];
      let e=document.querySelector('.inputbox');
      while(e && e!==document.body){
        const s=getComputedStyle(e);
        rows.push({cls:e.className, w:Math.round(e.getBoundingClientRect().width), disp:s.display,
          dir:s.flexDirection, align:s.alignItems, bs:s.boxSizing, minw:s.minWidth, maxw:s.maxWidth,
          pad:s.paddingLeft+'/'+s.paddingRight, mar:s.marginLeft+'/'+s.marginRight, ws:s.whiteSpace});
        e=e.parentElement;
      }
      return rows;
    })()"""), ensure_ascii=False, indent=1))
finally:
    proc.terminate()
