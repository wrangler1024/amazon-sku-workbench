from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from amazon_aigc.auth import new_session
from amazon_aigc.roster_sync import effective_rows, fresh, role_mapping, sync_roster


def test_role_compatibility_does_not_grant_artist_to_admin():
    assert role_mapping('管理员') == (['admin'],[])
    assert role_mapping('美工负责人') == (['art_lead','artist'],[])
    assert role_mapping('一部一组主管') == (['operator','supervisor'],['一部一组'])
    assert role_mapping('运营','运营二组') == (['operator'],['一部二组'])
    assert role_mapping('一部总监') == (['supervisor'],['一部一组','一部二组'])
    now=datetime.now(UTC)
    assert not effective_rows([{'启用状态':True,'角色':['管理员'],'成员':[{'id':'on_test'}],'生效日期':(now+timedelta(days=1)).isoformat()}],now)


def test_authoritative_add_change_disable_revoke_and_freshness(db):
    subject='on_'+uuid4().hex
    row={'启用状态':True,'角色':['一部一组运营'],'成员':[{'id':subject,'name':'测试人员'}]}
    class Gateway:
        s=SimpleNamespace(personnel_table_id='tblRoleSource',tenant='example')
        def records(self,_):return [row]
    def profile(g,s):return {'name':'测试人员','open_id':'ou_'+subject,'status':{'is_frozen':False,'is_resigned':False,'is_activated':True,'is_exited':False}}
    g=Gateway();sync_roster(db,g,profile)
    with db.connection() as c:
        u=c.execute("SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.subject=%s",(subject,)).fetchone()
        uid=u['id'];assert u['roles']==['operator'] and u['groups']==['一部一组'] and fresh(u)
        new_session(c,uid)
    row['角色']=['一部二组主管'];sync_roster(db,g,profile)
    with db.connection() as c:
        u=c.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
        assert u['groups']==['一部二组'] and 'supervisor' in u['roles']
        assert not c.execute('SELECT 1 FROM sessions WHERE user_id=%s',(uid,)).fetchone()
        new_session(c,uid)
        assert not fresh({**u,'roles_synced_at':datetime.now(UTC)-timedelta(seconds=121)})
    row['启用状态']=False;sync_roster(db,g,profile)
    with db.connection() as c:
        assert not c.execute('SELECT active FROM users WHERE id=%s',(uid,)).fetchone()['active']
        assert not c.execute('SELECT 1 FROM sessions WHERE user_id=%s',(uid,)).fetchone()


def test_unknown_roles_do_not_expand_review_access():
    now=datetime.now(UTC)
    subject='on_example'
    rows=[{'启用状态':True,'角色':[role],'成员':[{'id':subject,'name':'示例'}]} for role in ['管理员','一部一组主管','未知角色']]
    result=effective_rows(rows,now)[subject]
    assert result['roles']==['admin','operator','supervisor']
    assert result['groups']==['一部一组']
