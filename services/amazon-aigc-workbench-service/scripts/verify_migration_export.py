"""Check a portable export without relying on a live Feishu connection."""
import argparse
import hashlib
import json
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args();root=Path(args.directory)
m=json.loads((root/'manifest.json').read_text());counts={}
for name,meta in m['tables'].items():
 p=root/(name+'.ndjson');data=p.read_bytes()
 if hashlib.sha256(data).hexdigest()!=meta['sha256']:raise ValueError('Table checksum mismatch: '+name)
 rows=[json.loads(l) for l in data.decode().splitlines() if l]
 if len(rows)!=meta['rows']:raise ValueError('Row count mismatch: '+name)
 counts[name]=len(rows)
for key,digest in m['files'].items():
 path=(root/'files'/key).resolve()
 if not path.is_relative_to((root/'files').resolve()):raise ValueError('Invalid asset path')
 if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Asset checksum mismatch')
print(json.dumps({'verified':True,'tables':counts,'assets':len(m['files'])}))
