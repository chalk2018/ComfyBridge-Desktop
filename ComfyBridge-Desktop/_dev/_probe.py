import json,sys
sys.path.insert(0,'_dev')
from ui_shot import start, open_cap, pick
proc, cdp = start(True)
try:
    cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')",25)
    open_cap(cdp,'sheet.scene'); pick(cdp,'[data-mode="i2i"]'); cdp.pause(700)
    print(json.dumps(cdp.js("""(()=>{
      const box=document.querySelector('.inputbox'), body=document.querySelector('.sheetbody');
      const bar=document.querySelector('.sheetbar'), seg=bar.querySelector('.seg');
      const cs=e=>{const s=getComputedStyle(e);return {display:s.display,alignItems:s.alignItems,flex:s.flex,minW:s.minWidth,w:s.width,maxW:s.maxWidth,ovx:s.overflowX,pos:s.position}};
      return {box:cs(box), body:cs(body), bar:cs(bar), seg:cs(seg),
        boxRect: box.getBoundingClientRect().width, bodyRect: body.getBoundingClientRect().width};
    })()"""), ensure_ascii=False, indent=1))
finally:
    proc.terminate()
