"""Role permissions across real HTTP sessions, admission and queued notifications."""
import json
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import Mock
from store_mail_web.config import Settings
from store_mail_web.crypto import generate_master_key
from store_mail_web.service import Service, ApiError
from store_mail_web.http_api import start_http_server, ROUTES, ROUTE_PERMISSIONS
from store_mail_web.models import FetchedMail, ClassifiedMail
from store_mail_web.roles import DEFAULT_PERMISSIONS, PERMISSIONS, CORE_ADMIN


class RoleAccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(secret_key=generate_master_key(),admin_user='root-admin',admin_password='test-password',
            db_path=Path(self.tmp.name)/'roles.sqlite3',feishu_app_id='cli_test',feishu_app_secret='test-secret',
            feishu_tenant_key='test-tenant',public_url='https://mail.test.example',mark_seen_on_open=False)
        self.s=Service.build(self.settings);self.s.feishu.member_active=Mock(return_value=True)
        self.root=self.s.store.get_user_by_username('root-admin')
        self.a=self.s.create_account({'display_name':'A','email':'a@example.invalid','auth_code':'TEST'},self.root)['id']
        self.b=self.s.create_account({'display_name':'B','email':'b@example.invalid','auth_code':'TEST'},self.root)['id']
        self.users={}
        for role in DEFAULT_PERMISSIONS:
            d=self.s.create_user({'username':role,'full_name':role,'password':'test-password','role':role,'account_ids':[self.a]},self.root)
            self.users[role]=self.s.store.get_user(d['id'])
        root=Path(__file__).resolve().parents[1]
        self.server=start_http_server('127.0.0.1',0,self.s,root/'src/store_mail_web/static',root/'openapi.yaml')
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.s.store.close();self.tmp.cleanup()

    def http(self,method,path,viewer=None,body=None,token=None):
        token=token or self.s._issue_session(viewer or self.root)['token']
        headers={'Cookie':'smw_session='+token}
        if body is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(self.url+path,method=method,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def set_permissions(self,role,permissions,viewer=None):
        current=next(r for r in self.s.store.role_definitions() if r['role']==role)
        return self.s.update_role(role,{'permissions':sorted(permissions),'version':current['version']},viewer or self.root)

    def add_mail(self,aid=None,uid=1,subject='Ordinary email'):
        mail=FetchedMail(uid,str(uid),subject,'Amazon','notice@amazon.com','a@example.invalid',None,int(time.time())+uid,100,True,subject,None,subject)
        self.s.store.upsert_message(aid or self.a,mail,ClassifiedMail(True,'其他','低'))
        self.s.assess_synced_message(aid or self.a,uid)
        return self.s.store.risk_message_by_uid(aid or self.a,uid)['id']

    def test_default_roles_and_operations_scope(self):
        for role,u in self.users.items():
            self.assertEqual(self.s.permissions_for(u),DEFAULT_PERMISSIONS[role])
            visible=self.s.accounts_view(u)
            self.assertEqual(len(visible),2 if role=='admin' else 1)
        for role in ['operator','supervisor','director']:
            for path in ['/api/web/users','/api/web/admissions','/api/web/roles','/api/web/notify-config','/api/web/api-keys']:
                with self.subTest(role=role,path=path):self.assertEqual(self.http('GET',path,self.users[role])[0],403)

    def test_database_admin_login_approval_role_and_mailbox_management(self):
        status,data=self.http('POST','/api/auth/login',body={'username':'admin','password':'test-password'})
        self.assertEqual(status,200);self.assertEqual(data['user']['role'],'admin');self.assertFalse(data['user']['is_superadmin'])
        manager=self.users['admin']
        i=self.s.store.touch_identity('cli_test',{'name':'新同事','open_id':'ou_new','tenant_key':'test-tenant'})
        self.s.store.request_admission(i['id'],'负责邮箱 B')
        self.assertEqual(self.http('GET','/api/web/admissions',manager)[0],200)
        status,_=self.http('POST',f"/api/web/admissions/{i['id']}/review",manager,{'decision':'approve','role':'supervisor','account_ids':[self.b]})
        self.assertEqual(status,200)
        user=self.s.store.get_user(self.s.store.identity(i['id'])['user_id'])
        self.assertEqual((user.role,user.account_ids),('supervisor',(self.b,)))
        status,_=self.http('PUT',f'/api/web/users/{user.id}',manager,{'role':'director','account_ids':[self.a]})
        self.assertEqual(status,200)
        self.assertEqual(self.s.store.get_user(user.id).role,'director')
        self.assertEqual(self.s.store.get_user(user.id).account_ids,(self.a,))
        self.assertGreaterEqual(self.s.store._conn.execute('SELECT count(*) FROM access_audit').fetchone()[0],2)

    def test_admin_cannot_modify_or_bind_protected_superadmin(self):
        manager=self.users['admin']
        for payload in [{'full_name':'changed'},{'account_ids':[self.a]},{'role':'operator'},{'enabled':False},{'password':'new-password'}]:
            self.assertIn(self.http('PUT',f'/api/web/users/{self.root.id}',manager,payload)[0],(400,403))
        self.assertEqual(self.http('DELETE',f'/api/web/users/{self.root.id}',manager)[0],400)
        i=self.s.store.touch_identity('cli_test',{'name':'申请人','open_id':'ou_join','tenant_key':'test-tenant'})
        self.s.store.request_admission(i['id'],'')
        self.assertEqual(self.http('POST',f"/api/web/admissions/{i['id']}/review",manager,{'decision':'approve','existing_user_id':self.root.id})[0],403)
        self.assertEqual(self.s.store.get_user(self.root.id).role,'admin')

    def test_permission_editor_validates_dependencies_and_version(self):
        manager=self.users['admin']
        self.assertEqual(self.http('GET','/api/web/roles',manager)[0],200)
        self.set_permissions('operator',{'mail.read'},manager)
        token=self.s._issue_session(self.users['operator'])['token']
        self.assertEqual(self.http('GET','/api/web/dashboard',token=token)[0],403)
        self.assertEqual(self.http('GET','/api/web/messages',token=token)[0],200)
        for role,perms in [('operator',{'mail.read','roles.manage'}),('supervisor',{'events.assign'}),('admin',set())]:
            with self.assertRaises(ApiError):self.set_permissions(role,perms,manager)
        current=next(r for r in self.s.store.role_definitions() if r['role']=='operator')
        with self.assertRaises(ApiError) as cm:
            self.s.update_role('operator',{'permissions':['mail.read'],'version':current['version']-1},manager)
        self.assertEqual(cm.exception.status,409)

    def test_removed_permission_denied_using_same_session_and_superadmin_recovers(self):
        manager=self.users['admin'];token=self.s._issue_session(manager)['token']
        self.assertEqual(self.http('GET','/api/web/api-keys',token=token)[0],200)
        self.set_permissions('admin',DEFAULT_PERMISSIONS['admin']-{'api_keys.manage'})
        self.assertEqual(self.http('GET','/api/web/api-keys',token=token)[0],403)
        self.assertEqual(self.http('GET','/api/web/roles',token=token)[0],200)
        self.assertEqual(self.http('GET','/api/web/api-keys',self.root)[0],200)
        self.assertEqual(self.s.permissions_for(self.root),set(PERMISSIONS))

    def test_role_change_revokes_old_session_and_keeps_mailbox_grants(self):
        manager=self.users['admin'];token=self.s._issue_session(manager)['token']
        status,_=self.http('PUT',f'/api/web/users/{manager.id}',self.root,{'role':'operator'})
        self.assertEqual(status,200)
        self.assertEqual(self.s.store.get_user(manager.id).account_ids,(self.a,))
        self.assertEqual(self.http('GET','/api/web/users',token=token)[0],401)
        fresh=self.s.login('admin','test-password')
        self.assertEqual(fresh['user']['role'],'operator')
        self.assertEqual(self.http('GET','/api/web/users',token=fresh['token'])[0],403)

    def test_disabled_admin_is_denied_and_cannot_self_demote(self):
        manager=self.users['admin'];token=self.s._issue_session(manager)['token']
        self.assertEqual(self.http('PUT',f'/api/web/users/{manager.id}',manager,{'role':'operator'})[0],403)
        self.assertEqual(self.http('PUT',f'/api/web/users/{manager.id}',manager,{'enabled':False})[0],403)
        self.s.update_user(manager.id,{'enabled':False},self.root)
        self.assertEqual(self.http('GET','/api/web/users',token=token)[0],401)
        with self.assertRaises(ApiError):self.s.login('admin','test-password')
        self.s.update_user(manager.id,{'enabled':True},self.root)
        self.assertEqual(self.http('GET','/api/web/users',token=token)[0],401)

    def test_supervisor_assignment_and_director_receipt_are_scoped(self):
        mid=self.add_mail(subject='Your seller account is at risk of deactivation')
        # Resolve using the same production dashboard query, independent of event schema IDs.
        eid=self.s.dashboard(self.root)['queue'][0]['id']
        operator=self.users['operator'];supervisor=self.users['supervisor'];director=self.users['director']
        self.assertEqual(self.http('POST',f'/api/web/risk-events/{eid}/update',operator,{'owner_user_id':director.id})[0],403)
        self.assertEqual(self.http('POST',f'/api/web/risk-events/{eid}/update',supervisor,{'owner_user_id':director.id})[0],200)
        self.add_mail(self.b,uid=2,subject='Your seller account is at risk of deactivation')
        other=next(e['id'] for e in self.s.dashboard(self.root)['queue'] if e['account_id']==self.b)
        self.assertEqual(self.http('POST',f'/api/web/risk-events/{other}/update',supervisor,{'owner_user_id':director.id})[0],404)
        self.add_mail(uid=3,subject='Payment has been initiated: USD 100.00')
        payout=self.s.dashboard(self.root)['finance'][0]['id']
        body={'bank_receipt_status':'已核对到账','note':'测试银行核对依据'}
        self.assertEqual(self.http('POST',f'/api/web/risk-events/{payout}/update',supervisor,body)[0],403)
        self.assertEqual(self.http('POST',f'/api/web/risk-events/{payout}/update',director,body)[0],200)

    def test_new_grants_and_permission_removal_control_notification(self):
        u=self.users['operator'];i=self.s.store.touch_identity('cli_test',{'name':'运营','open_id':'ou_ops','tenant_key':'test-tenant'})
        self.s.store.bind_identity(i['id'],u.id,self.root.id)
        self.s.direct_notifier.set_enabled(True);self.add_mail()
        row=self.s.store._conn.execute('SELECT * FROM notification_outbox WHERE user_id=?',(u.id,)).fetchone()
        self.assertEqual(row['status'],'pending')
        self.set_permissions('operator',DEFAULT_PERMISSIONS['operator']-{'notifications.receive'})
        self.assertIsNone(self.s.store.notification_context(dict(row)))
        self.add_mail(uid=2)
        self.assertEqual(self.s.store._conn.execute('SELECT count(*) FROM notification_outbox WHERE user_id=?',(u.id,)).fetchone()[0],1)

    def test_legacy_role_migration_preserves_existing_custom_role_settings(self):
        self.set_permissions('operator',{'mail.read'})
        self.s.store._conn.execute("UPDATE users SET role='user' WHERE id=?",(self.users['operator'].id,));self.s.store._conn.commit()
        # Open a second Store against the same DB to exercise the startup migration.
        from store_mail_web.state import Store
        from store_mail_web.crypto import SecretBox
        second=Store(self.settings.db_path,SecretBox(self.settings.secret_key))
        self.addCleanup(second.close)
        self.assertEqual(second.get_user(self.users['operator'].id).role,'operator')
        self.assertEqual(second.role_permissions('operator'),{'mail.read'})

    def test_business_routes_are_explicitly_gated(self):
        exceptions={'web_accounts_list','web_sync_status'}
        missing={name for _,_,auth,name in ROUTES if auth in ('admin','session') and name not in exceptions
                 and name not in ('me','feishu_bind') and name not in ROUTE_PERMISSIONS}
        self.assertEqual(missing,set())

    def test_superadmin_notification_capability_is_not_removed_by_admin_role_edit(self):
        self.set_permissions('admin',DEFAULT_PERMISSIONS['admin']-{'notifications.receive'})
        self.assertIn('notifications.receive',self.s.store.recipient_permissions(self.root.id,'admin'))
        self.assertNotIn('notifications.receive',self.s.store.recipient_permissions(self.users['admin'].id,'admin'))
