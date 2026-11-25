import sys, json, time
sys.path.insert(0, "tests")
sys.stdout.reconfigure(encoding="utf-8")
from ext_sheet_e2e import CDP, click, start_browser
proc, cdp = start_browser()
try:
    cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.topbar')", 25)
    cdp.js("location.hash='#admin/sheet'")
    cdp.pause(1200)
    print("lcards", cdp.js("document.querySelectorAll('.lcard').length"),
          "previewBtns", cdp.js("document.querySelectorAll('[data-sheet-preview]').length"))
    print("handler?", cdp.js("(()=>{const b=document.querySelector('[data-sheet-preview=\"B\"]');return b? String(b.onclick): 'no-btn';})()"))
    cdp.js("(()=>{const b=document.querySelector('[data-sheet-preview=\"B\"]'); if(b) b.click(); return true;})()")
    cdp.pause(2500)
    print("mask?", cdp.js("!!document.getElementById('mask')"), "promptfull?", cdp.js("!!document.querySelector('.promptfull')"))
    print("maskHTML", (cdp.js("(document.getElementById('mask')||{}).innerHTML||''") or "")[:300])
finally:
    cdp.close(); proc.terminate()
