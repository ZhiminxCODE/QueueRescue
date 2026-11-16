"""Copy the optional local browser fixture into an existing ignored local build."""
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
build = root / 'dist'
if not (build / 'index.html').exists() or json.loads((build / 'connect-origins.json').read_text()) != []:
    raise SystemExit('First create a local-mode frontend build. AWS-mode builds cannot run this fixture.')
html = (build / 'index.html').read_text()
html = html.replace('<body>', '<body>' + (root / 'tools/browser-benchmark.html').read_text())
html = html.replace('</body>', '<script type="module" src="/browser-benchmark.mjs"></script></body>')
(build / 'browser-benchmark.html').write_text(html)
shutil.copyfile(root / 'tools/browser-benchmark.mjs', build / 'browser-benchmark.mjs')
print('Local fixture ready at /browser-benchmark.html. Use a fresh --seed 10000 database and --consumer-disabled.')
