import json
import re
import tomllib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
v=json.loads((root/'versions.json').read_text())
assert json.loads((root/'package.json').read_text())['version']==v['bundle']
for name,c in v['components'].items():
 p=root/c['path']
 if name=='api':
  actual=tomllib.loads((p/'pyproject.toml').read_text())['project']['version']
  assert re.search(r'__version__\s*=\s*["\x27]'+re.escape(actual),(p/'src/amazon_aigc/__init__.py').read_text())
 else:actual=json.loads((p/'package.json').read_text())['version']
 assert actual==c['version'],(name,actual,c['version'])
 assert c['tag']==f'{name}/v{actual}'
 if name=='fbacc':
  assert re.search(r'export const VERSION\s*=\s*["\x27]'+re.escape(actual),(p/'src/index.mjs').read_text())
  assert (p/'infra/rates/VERSION').read_text().strip()==c['monitor_version']
  assert json.loads((p/'rates'/f"{c['rate_snapshot']}.json").read_text())['version']==c['rate_snapshot']
print('Component versions match versions.json')
