import json
import os
import subprocess
import sys


def test_server_owned_target_file_is_used_and_mismatches_fail_closed(tmp_path):
    config = {
        'integration': {'base_id': 'CUSTOM_TEST', 'table_id': 'tblCustomTest'},
        'production': {'base_id': 'CUSTOM_PROD', 'table_id': 'tblCustomProd',
                       'app_id': 'cli_0000000000000003',
                       'plugin_origin': 'https://blk_000000000000000000000003.feishupkg.com',
                       'personnel_table_id': 'tblCustomPeople'},
    }
    path = tmp_path/'targets.local.json'
    path.write_text(json.dumps(config))
    code = '''
from amazon_aigc.config import Settings, valid_target
assert valid_target('integration', 'CUSTOM_TEST', 'tblCustomTest')
assert not valid_target('integration', 'CUSTOM_PROD', 'tblCustomProd')
s=Settings(database_url='postgresql://unused/test',base_id='CUSTOM_TEST',sku_table_id='tblCustomTest')
assert s.base_id=='CUSTOM_TEST'
try:
 Settings(database_url='postgresql://unused/test',base_id='CUSTOM_PROD',sku_table_id='tblCustomProd')
except ValueError: pass
else: raise AssertionError('cross environment accepted')
'''
    result = subprocess.run([sys.executable,'-c',code],env={**os.environ,'AIGC_TARGETS_FILE':str(path)},capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


def test_invalid_target_file_refuses_startup(tmp_path):
    path=tmp_path/'targets.local.json'
    path.write_text('{"integration":{"base_id":""},"production":{}}')
    result=subprocess.run([sys.executable,'-c','import amazon_aigc.config'],env={**os.environ,'AIGC_TARGETS_FILE':str(path)},capture_output=True,text=True)
    assert result.returncode != 0 and 'Target allowlist' in result.stderr
