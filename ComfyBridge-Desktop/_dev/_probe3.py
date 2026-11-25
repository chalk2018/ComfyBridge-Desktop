import json,sys
sys.path.insert(0,'_dev')
from ui_shot import start, pick
proc, cdp = start(True)
try:
    cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')",25)
    pick(cdp,'.topbar [data-nav="admin"]'); cdp.wait("UI.route==='admin'",20)
    pick(cdp,'[data-grades-cap="sheet.scene"]')
    cdp.wait("!!document.querySelector('[data-combo-mode]')",20); cdp.pause(900)
    print(json.dumps(cdp.js("""(()=>{
      const out=[];
      document.querySelectorAll('*').forEach(e=>{
        const r=e.getBoundingClientRect();
        if(r.width>0 && (r.right>392 || e.scrollWidth-e.clientWidth>4)){
          out.push({tag:e.tagName.toLowerCase(), cls:String(e.className).slice(0,60),
            right:Math.round(r.right), w:Math.round(r.width), sw:e.scrollWidth, cw:e.clientWidth});
        }
      });
      return {docW:document.documentElement.scrollWidth, cw:document.documentElement.clientWidth,
              offenders: out.slice(0,30)};
    })()"""), ensure_ascii=False, indent=1))
finally:
    proc.terminate()
