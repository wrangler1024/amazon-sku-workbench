"""Fail before commit/CI if private configuration or credential shapes enter Git."""
from pathlib import Path
import re
import subprocess
root=Path(__file__).resolve().parents[1]
result=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True,text=True,check=True)
files=[root/p for p in result.stdout.split('\0') if p]
rules={
 'private key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'provider token':r'\b(?:sk-proj-|sk-ant-|ghp_|github_pat_)[a-zA-Z0-9_-]{20,}',
 'employee identifier':r'\b(?:ou_|on_)[a-zA-Z0-9]{20,}\b',
 'real Feishu app':r'\bcli_(?!0{15}[1-9]\b)[a-f0-9]{16}\b',
 'real Feishu block':r'\bblk_(?!0{23}[1-9]\b)[a-f0-9]{24}\b',
 'local user path':r'/Users/[a-zA-Z][^/\s]+/',
 'tenant link':r'https://(?!example\.feishu\.cn)(?!open\.feishu\.cn)(?!accounts\.feishu\.cn)[\w-]+\.feishu\.cn/(?:base|docx|wiki)/',
}
issues=[]
for p in files:
 rel=p.relative_to(root)
 if p.is_symlink():issues.append((str(rel),'symlink'));continue
 if any(x in rel.parts for x in ('evidence','backups','credentials','node_modules','.venv','.local','var')):issues.append((str(rel),'private/generated directory'))
 if p.name.startswith('.env') and p.name!='.env.example':issues.append((str(rel),'private environment file'))
 if p.suffix in ('.pem','.p12','.pfx','.dump','.ndjson','.sqlite','.sqlite3','.db','.eml','.mbox'):issues.append((str(rel),'private artifact type'))
 if re.search(r'\.(?:sqlite3?|db)-(?:wal|shm|journal)$',p.name):issues.append((str(rel),'private database journal'))
 try:s=p.read_text()
 except UnicodeDecodeError:continue
 for label,pattern in rules.items():
  if re.search(pattern,s):issues.append((str(rel),label))
if issues:
 for path,label in issues:print(path+': '+label)
 raise SystemExit(1)
print(f'Public boundary check passed for {len(files)} tracked files')
