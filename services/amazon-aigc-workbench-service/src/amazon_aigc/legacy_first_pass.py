"""Replay frozen pre-cutover review evidence; never infer first pass from acceptance.

Matches the audited sku_workload_directory first-pass model. Imported outcomes
are keyed by original record and artist, not by current record status.
"""

import hashlib
import json
from datetime import datetime

from .legacy import value

ALIASES = {'用户152996': '示例美工甲', '那个谁': '示例美工丁', '待开始': '待制图'}


def text(v):
    if isinstance(v, list):
        return '|'.join(text(x) for x in v)
    if isinstance(v, dict):
        return text(v.get('name', v.get('text', v.get('value', ''))))
    s = '' if v is None else str(v)
    return ALIASES.get(s, s)


def replay(history, snapshot, cutoff):
    fields, first, owners = {}, {}, set()
    issues = set() if history.get('complete') else {'history_incomplete'}
    pending = None
    events = {e['rev']: e for e in history.get('events', []) if e['create_time'] <= cutoff}
    for event in sorted(events.values(), key=lambda e: e['rev']):
        changes = event.get('field_changes') or []
        for change in changes:
            key = change['field_name']
            if key not in fields:
                fields[key] = change.get('before')
            elif text(fields[key]) != text(change.get('before')):
                issues.add('history_discontinuity')
        for change in changes:
            fields[change['field_name']] = change.get('after')
        assigned, retained = text(fields.get('美工')), text(fields.get('美工留存'))
        owner = retained or assigned
        conflict = assigned and retained and assigned != retained
        for change in changes:
            if change['field_name'] != '制图进度留存':
                continue
            before, after = text(change.get('before')), text(change.get('after'))
            if before == after:
                continue
            if after in ('待验收', '待返修', '已验收'):
                if owner:
                    owners.add(owner)
                if event['operator'] != '多维表格助手':
                    issues.add('manual_review_phase')
                if not owner or conflict:
                    issues.add('ambiguous_owner')
            if event['operator'] == '多维表格助手' and after == '待验收' and before in ('制图中', '待返修'):
                pending = (owner, event['rev'])
            elif event['operator'] == '多维表格助手' and before == '待验收' and after in ('已验收', '待返修'):
                if not pending or pending[0] != owner or pending[1] >= event['rev']:
                    issues.add('review_without_delivery')
                else:
                    first.setdefault(owner, {'result': 'accepted' if after == '已验收' else 'rejected', 'review_rev': event['rev'], 'review_time': event['create_time']})
                pending = None
            elif after in ('待分配', '待制图') or (before == '待验收' and after != '待验收'):
                pending = None
    if '制图进度留存' in fields and text(fields['制图进度留存']) != text(snapshot.get('制图进度留存')):
        issues.add('snapshot_mismatch')
    if value(snapshot.get('制图状态')) == '已验收' and not first:
        issues.add('acceptance_without_history')
    return first, sorted(issues)


def prepare(checkpoint, migrations, users, identities):
    if checkpoint.get('version') != 1 or checkpoint['lastGood']['syncStatus'] != 'complete':
        raise ValueError('A complete verified checkpoint is required')
    by_subject = {r['subject']: str(r['user_id']) for r in identities}
    names = {}
    # Corroborate the roster name with an actual immutable Base person ID.
    for row in migrations:
        for key in ('美工', '美工留存'):
            for member in row['snapshot'].get(key) or []:
                uid = by_subject.get(member.get('id'))
                if uid:
                    names.setdefault(text(member.get('name')), set()).add(uid)
    allowed = {str(u['id']): u for u in users}
    results, excluded = [], []
    for row in migrations:
        history = checkpoint['records'].get(row['record_id'])
        if not history:
            continue
        cutoff = datetime.fromisoformat(str(row['captured_at'])).timestamp()
        first, issues = replay(history, row['snapshot'], cutoff)
        if issues:
            excluded.append({'record_id': row['record_id'], 'issues': issues})
            continue
        for name, result in first.items():
            ids = names.get(name, set())
            if len(ids) != 1:
                continue
            uid = next(iter(ids))
            if uid not in allowed or text(allowed[uid]['name']) != name:
                continue
            results.append({'base_id': row['base_id'], 'table_id': row['table_id'], 'record_id': row['record_id'], 'artist_id': uid, 'artist_name': name, **result})
    return {'records': results, 'excluded': excluded, 'checkpoint_revision': checkpoint['lastGood']['snapshotRevision'], 'checked_at': checkpoint['lastGood']['checkedAt']}


def import_checkpoint(c, checkpoint, commit=False):
    migrations = c.execute('SELECT * FROM legacy_migration_records').fetchall()
    users = c.execute("SELECT id,name FROM users WHERE 'artist'=ANY(roles) OR 'art_lead'=ANY(roles)").fetchall()
    identities = c.execute('SELECT subject,user_id FROM external_identities').fetchall()
    report = prepare(checkpoint, migrations, users, identities)
    digest = hashlib.sha256(json.dumps(checkpoint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if commit:
        for r in report['records']:
            c.execute('''INSERT INTO legacy_first_reviews(base_id,table_id,record_id,artist_id,result,review_rev,review_time,source_sha256)
              VALUES(%s,%s,%s,%s,%s,%s,to_timestamp(%s),%s) ON CONFLICT DO NOTHING''',
              (r['base_id'], r['table_id'], r['record_id'], r['artist_id'], r['result'], r['review_rev'], r['review_time'], digest))
            saved = c.execute('SELECT result,review_rev FROM legacy_first_reviews WHERE base_id=%s AND table_id=%s AND record_id=%s AND artist_id=%s',
                              (r['base_id'], r['table_id'], r['record_id'], r['artist_id'])).fetchone()
            if saved != {'result': r['result'], 'review_rev': r['review_rev']}:
                raise ValueError('Existing historical verdict conflicts with checkpoint')
    return {**report, 'source_sha256': digest}
