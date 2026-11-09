import copy
import json
from pathlib import Path

import pytest
from test_workflow import FACTS, prepare

from amazon_aigc.domain import publish_brief, write_document
from amazon_aigc.manual_brief import ManualBrief, normalize_manual_brief
from amazon_aigc.schemas import Brief, DocumentWrite
from amazon_aigc.sync import projection


def manual():
    return {'product':'整段图需：主图展示本品，副图展示用途、尺寸与细节。','selling_points':'用途与数量','visual_focus':'实际销售组合','image_count':7,
            'plans':[{'purpose':'白底主图' if i==0 else f'副图 {i}','composition':'','text':''} for i in range(7)]}


def test_placeholder_confirmation_publishes_original_brief_and_count(db,setup):
    users,item,_=setup;u=users['operator'];d=prepare(db,u,item,'facts',FACTS);value=manual();original=copy.deepcopy(value)
    with db.connection() as c:
        d=write_document(c,u,item,DocumentWrite(revision=d['document']['revision'],section='brief',body=value))
        assert len(d['document']['body']['brief']['plans'])==7
        d=write_document(c,u,item,DocumentWrite(revision=d['document']['revision'],section='brief',body=value,confirm=True))
        saved=d['document']['body']['brief'];assert saved['plans']==[] and saved['image_count']==7
        assert saved['product']==value['product'] and value==original
        d=publish_brief(c,u,item,d['document']['revision'],{})
        assert len(d['tasks'])==1 and d['tasks'][0]['state']=='pool'
        fields=projection(c,item);assert fields['计划制图数量']==7
        assert value['product'] in str(fields['制图需求说明'])


@pytest.mark.parametrize('edited',[{'purpose':'细节特写'},{'text':'Dimensions'},{'composition':'实际构图'}])
def test_partly_edited_cards_are_not_discarded(db,setup,edited):
    users,item,_=setup;u=users['operator'];d=prepare(db,u,item,'facts',FACTS);value=manual();value['plans'][2].update(edited)
    assert normalize_manual_brief(value) is value
    with db.connection() as c:
        d=write_document(c,u,item,DocumentWrite(revision=d['document']['revision'],section='brief',body=value,confirm=True))
        assert d['document']['body']['brief']['plans']==value['plans']
        d=publish_brief(c,u,item,d['document']['revision'],{})
        assert d['tasks'][0]['state']=='pool'


def test_manual_normalization_does_not_bypass_required_content_or_count():
    raw=manual();raw['product']='';
    with pytest.raises(ValueError):Brief.model_validate(normalize_manual_brief(raw))
    raw=manual();raw['image_count']=0
    with pytest.raises(ValueError):Brief.model_validate(normalize_manual_brief(raw))
    with pytest.raises(ValueError):Brief.model_validate(manual())  # direct model validation remains strict
    raw=manual();raw['plans'][0]['unexpected']='must not disappear'
    assert normalize_manual_brief(raw) is raw


def test_manual_payload_preserved_and_valid_after_normalization():
    # Synthetic regression fixture; never sourced from a live product.
    path=Path(__file__).parent/'fixtures'/'manual_brief.json'
    value=json.loads(path.read_text());before=copy.deepcopy(value)
    with pytest.raises(ValueError) as exc:Brief.model_validate(value)
    assert '7 validation errors' in str(exc.value)
    result=Brief.model_validate(normalize_manual_brief(value)).model_dump()
    assert value==before and result['plans']==[] and result['image_count']==7
    assert all(result[k]==v for k,v in before.items() if k not in ('plans','image_count'))


def test_optional_card_fields_and_all_cards_removed(db,setup):
    users,item,_=setup;u=users['operator'];d=prepare(db,u,item,'facts',FACTS)
    value={**manual(),'plans':[{'composition':'保留这段画面建议'},{'text':'Optional text'},{}]}
    with db.connection() as c:
        d=write_document(c,u,item,DocumentWrite(revision=d['document']['revision'],section='brief',body=value,confirm=True))
        saved=d['document']['body']['brief'];assert saved['plans'][0]['purpose']=='' and saved['plans'][1]['text']=='Optional text'
        original=d['document']['brief_version']
        d=write_document(c,u,item,DocumentWrite(revision=d['document']['revision'],section='brief',body={**saved,'plans':[],'image_count':7},confirm=True))
        assert d['document']['body']['brief']['plans']==[] and d['document']['body']['brief']['image_count']==7
        old=c.execute('SELECT body FROM content_versions WHERE id=%s',(original,)).fetchone()['body']
        assert len(old['plans'])==3 and old['plans'][0]['composition']=='保留这段画面建议'
        d=publish_brief(c,u,item,d['document']['revision'],{})
        assert d['tasks'][0]['state']=='pool'
    with pytest.raises(ValueError):ManualBrief.model_validate({**manual(),'plans':[{'purpose':'x'*201}]})
