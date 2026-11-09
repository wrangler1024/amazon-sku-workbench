"""Import audited pre-cutover verdicts; defaults to a read-only plan."""
import argparse
import json
from pathlib import Path

from amazon_aigc.config import settings
from amazon_aigc.db import Database
from amazon_aigc.legacy_first_pass import import_checkpoint

parser = argparse.ArgumentParser()
parser.add_argument('checkpoint', type=Path)
parser.add_argument('--apply', action='store_true')
a = parser.parse_args()
s = settings()
if s.environment != 'production':
    raise SystemExit('Historical production baseline requires the production environment')
db = Database(s.database_url)
try:
    with db.connection() as c:
        result = import_checkpoint(c, json.loads(a.checkpoint.read_text()), commit=a.apply)
        counts = {}
        for row in result['records']:
            count = counts.setdefault(row['artist_name'], {'passed': 0, 'reviewed': 0})
            count['reviewed'] += 1
            count['passed'] += row['result'] == 'accepted'
        print(json.dumps({'applied': a.apply, 'rows': len(result['records']), 'artists': counts, 'excluded': len(result['excluded']), 'source_sha256': result['source_sha256']}, ensure_ascii=False))
finally:
    db.close()
