import json, sys, time
sys.path.insert(0, '_dev')
from ui_shot import start
proc, cdp = start(True)
try:
    time.sleep(4)
    print('url', cdp.js("location.href"))
    print('ready', cdp.js("document.readyState"))
    print('root', cdp.js("document.querySelector('#root') && document.querySelector('#root').innerHTML.length"))
    print('UI', cdp.js("typeof UI"))
    print('err', cdp.js("window.__err || ''"))
    print('body', cdp.js("document.body.innerHTML.slice(0,200)"))
finally:
    proc.terminate()
