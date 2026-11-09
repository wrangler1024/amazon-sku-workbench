from test_workflow import pool

from amazon_aigc.domain import detail, editing_status, reopen_edit
from amazon_aigc.workbench_views import item_rows


def test_completed_legacy_does_not_restart_editing_or_fabricate_listing(db, setup):
    users, item, doc = setup
    t = pool(db, setup)
    with db.connection() as c:
        c.execute("UPDATE artwork_tasks SET state='accepted',legacy_takeover=true WHERE id=%s", (t["id"],))
        c.execute(
            "UPDATE content_documents SET facts_version=NULL,listing_version=NULL,brief_version=NULL WHERE id=%s",
            (doc,),
        )
        before = detail(c, users["operator"], item)
        row = next(r for r in item_rows(c, users["operator"]) if r["id"] == str(item))
        assert not row["editing"]["pending"] and not before["editing"]["pending"]
        assert before["document"]["listing_version"] is None
        reopened = reopen_edit(c, users["operator"], item, before["document"]["revision"], {"section": "listing"})
        request = reopened["document"]["body"]["legacy_edit_request"]
        assert request == {"task_id": t["id"], "sections": ["facts", "listing"]}
        assert reopened["document"]["revision"] == before["document"]["revision"] + 1
        assert reopened["editing"]["todos"] == [{"stage": "facts", "label": "待核对资料"}, {"stage": "listing", "label": "待确认文案"}]
        assert reopened["tasks"] == before["tasks"] and reopened["versions"] == before["versions"]
        row = next(r for r in item_rows(c, users["operator"]) if r["id"] == str(item))
        assert row["legacy_edit_request"] == request
        assert row["editing"] == reopened["editing"]
        # Listing/facts work does not restart an accepted artwork brief.
        d = {**reopened["document"], "facts_version": "confirmed"}
        assert editing_status(d, reopened["tasks"][0])["todos"] == [{"stage": "listing", "label": "待确认文案"}]
        changed = reopen_edit(c, users["operator"], item, reopened["document"]["revision"], {"section": "facts"})
        assert changed["document"]["body"]["legacy_edit_request"]["sections"] == ["brief", "facts", "listing"]
