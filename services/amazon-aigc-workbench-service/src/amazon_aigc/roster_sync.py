"""Use the review plugin's personnel table as the only production role authority."""
import re
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from .feishu import user_profile, value

GROUPS = ['一部一组','一部二组','二部一组','二部二组']
LEGACY = dict(zip(['运营一组','运营二组','运营三组','运营四组'], GROUPS, strict=True))


def role_mapping(text, group=''):
    if text == '管理员': return ['admin'], []
    if text == '美工': return ['artist'], []
    if text == '美工负责人': return ['art_lead','artist'], []
    if text in ['运营','运营主管']:
        group=LEGACY.get(group,group)
        return (['operator','supervisor'] if text=='运营主管' else ['operator'],[group]) if group in GROUPS else ([],[])
    m=re.fullmatch(r'([一二]部[一二]组)(运营|主管|总监)',text)
    if m:return (['operator'] if m[2]=='运营' else ['operator','supervisor']),[m[1]]
    if re.fullmatch('[一二]部总监',text):return ['supervisor'],[g for g in GROUPS if g.startswith(text[:2])]
    return [],[]


def effective_rows(rows, now=None):
    now=now or datetime.now(UTC)
    merged={}
    for row in rows:
        if not row.get('启用状态'):continue
        starts=row.get('生效日期')
        if starts:
            from .legacy import stamp
            if stamp(starts)>now:continue
        roles,groups=role_mapping(value(row.get('角色')),value(row.get('所属小组')))
        if not roles:continue
        members=row.get('成员') or []
        if not isinstance(members,list):continue
        for member in members:
            subject=member.get('id','')
            if not subject.startswith('on_'):raise ValueError('Personnel table must use union identities')
            p=merged.setdefault(subject,{'roles':set(),'groups':set(),'name':member.get('name') or value(row.get('配置名称')).split('（')[0]})
            p['roles'].update(roles);p['groups'].update(groups)
    return {k:{'name':v['name'],'roles':sorted(v['roles']),'groups':sorted(v['groups'])} for k,v in merged.items()}


def sync_roster(db,gateway,profile_reader=user_profile):
    if not gateway.s.personnel_table_id:return {'managed':False}
    rows=gateway.records(gateway.s.personnel_table_id)
    roster=effective_rows(rows)
    now=datetime.now(UTC)
    with db.connection() as c:
        if not c.execute('SELECT pg_try_advisory_xact_lock(58017009) AS locked').fetchone()['locked']:return {'busy':True}
        existing={r['subject']:r for r in c.execute("SELECT u.*,e.subject FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider='feishu_union' AND e.tenant=%s",(gateway.s.tenant,)).fetchall()}
        changes=0
        for subject,old in existing.items():
            if subject not in roster:
                c.execute("UPDATE users SET roles='{}',groups='{}',active=false,roles_managed=true,roles_synced_at=now() WHERE id=%s",(old['id'],))
                c.execute('DELETE FROM sessions WHERE user_id=%s',(old['id'],))
        for subject,p in roster.items():
            old=existing.get(subject);uid=old['id'] if old else uuid4()
            active=bool(old and old['active']);verified=old['verified_at'] if old and old.get('roles_managed') else None
            internal=bool(old and old['internal']);openid=None
            if not verified or (now-verified).total_seconds()>=300 or not active:
                try:
                    profile=profile_reader(gateway,subject)
                    status=profile.get('status',{})
                    needed={'is_frozen','is_resigned','is_activated','is_exited'}
                    if not needed.issubset(status):raise ValueError('Employee status incomplete')
                    active=bool(status['is_activated'] and not any(status[k] for k in needed-{'is_activated'}))
                    internal=True  # contact API within this tenant; external contacts are never returned here.
                    verified=now;openid=profile.get('open_id');p['name']=profile.get('name') or p['name']
                except Exception:  # noqa: BLE001 -- do not elevate on an incomplete directory response
                    if not verified or (now-verified).total_seconds()>600:active=False
            changed=not old or sorted(old['roles'])!=p['roles'] or sorted(old['groups'])!=p['groups'] or old['active']!=active
            c.execute('INSERT INTO users(id,name,roles,groups,active,internal,verified_at,roles_managed,roles_synced_at) VALUES(%s,%s,%s,%s,%s,%s,%s,true,now()) ON CONFLICT(id) DO UPDATE SET name=excluded.name,roles=excluded.roles,groups=excluded.groups,active=excluded.active,internal=excluded.internal,verified_at=excluded.verified_at,roles_managed=true,roles_synced_at=now()',
                      (uid,p['name'],p['roles'],p['groups'],active,internal,verified))
            c.execute("INSERT INTO external_identities VALUES('feishu_union',%s,%s,%s) ON CONFLICT DO NOTHING",(gateway.s.tenant,subject,uid))
            if openid:c.execute("INSERT INTO external_identities VALUES('feishu',%s,%s,%s) ON CONFLICT DO NOTHING",(gateway.s.tenant,openid,uid))
            if changed:
                changes+=1;c.execute('DELETE FROM sessions WHERE user_id=%s',(uid,))
                c.execute("INSERT INTO audit_events(action,detail) VALUES('identity.sync',%s)",(Jsonb({'user_id':str(uid),'roles':p['roles'],'groups':p['groups'],'active':active}),))
    return {'managed':True,'people':len(roster),'changed':changes}


def fresh(user,now=None):
    if not user.get('roles_managed'):return True
    now=now or datetime.now(UTC)
    return bool(user.get('roles_synced_at') and (now-user['roles_synced_at']).total_seconds()<=120 and user.get('verified_at') and (now-user['verified_at']).total_seconds()<=600)
