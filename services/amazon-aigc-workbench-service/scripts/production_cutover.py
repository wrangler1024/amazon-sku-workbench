"""Deploy-time migration: default dry-run; requires explicit production configuration."""
import argparse
import json
from pathlib import Path

from amazon_aigc.config import settings
from amazon_aigc.db import Database
from amazon_aigc.legacy import migrate

p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
s=settings();db=Database(s.database_url);db.migrate()
rows=[json.loads(line) for line in Path(a.snapshot).read_text().splitlines() if line]
if len({r['record_id'] for r in rows}) != len(rows):raise ValueError('Duplicate source records')
with db.connection() as c:
 result=migrate(c,s,rows,commit=a.apply)
print(json.dumps({'applied':a.apply,'records':len(rows),'cohorts':result}))
db.close()
