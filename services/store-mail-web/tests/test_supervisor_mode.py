import unittest
import test_roles as fixtures
from store_mail_web.classifier import classify
from store_mail_web.direct_notify import mail_text
from store_mail_web.errors import ApiError

class PilotTest(unittest.TestCase):
    setUp=fixtures.RoleAccessTest.setUp
    tearDown=fixtures.RoleAccessTest.tearDown
    http=fixtures.RoleAccessTest.http
    def test_existing_session_and_new_login_gate(self):
        operator=self.users['operator'];token=self.s._issue_session(operator)['token']
        self.s.store.set_setting('supervisor_access_only','1')
        self.assertEqual(self.http('GET','/api/web/messages',token=token)[0],403)
        with self.assertRaises(ApiError):self.s._issue_session(operator)
        for role in ['supervisor','director','admin']:
            self.assertEqual(self.http('GET','/api/web/messages',self.users[role])[0],200)
        self.assertEqual(self.s.store.recipient_permissions(operator.id,'operator'),set())
        self.assertIn('notifications.receive',self.s.store.recipient_permissions(self.users['supervisor'].id,'supervisor'))
    def test_station_routes_removed(self):
        for method,path,body in [('GET','/api/web/marketplaces',None),('POST','/api/web/marketplaces/scan',{}),('PUT','/api/web/marketplaces/1',{})]:
            self.assertEqual(self.http(method,path,body=body)[0],404)

class ClassificationTest(unittest.TestCase):
    def test_business_subject_over_boilerplate(self):
        cases=[('Refund Initiated for Order 123','Amazon Payments newsletter','客户'),
          ('FBA reimbursement notification','Fulfillment Center Customer Service','回款'),
          ('您的上季度的B2B业绩报告已出炉。','客户 买家 付款','广告'),
          ('GB - Product Group Approval Request for You need approval to list','unsubscribe','告警'),
          ('免费直播：黑五广告提效','客户服务','广告')]
        for subject,body,expected in cases:
            self.assertEqual(classify(subject,'notice@amazon.com',body)[0],expected)
    def test_notification_category_and_security(self):
        base={'id':1,'subject':'Your verification code AB1234','display_name':'示例店铺','email':'test@example.invalid','received_at':'2026-11-05','category':'告警','priority':'高'}
        text=mail_text(base,'https://mail.test.example')
        self.assertNotIn('AB1234',text)
        self.assertIn('分类：告警',text);self.assertIn('优先关注',text)
        self.assertIn('/#/message/1',text);self.assertIn('转告本组运营',text)
