from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_unified_artwork import image
from test_workflow import FACTS, LISTING, prepare

from amazon_aigc.domain import detail, idempotent, task_command, write_document
from amazon_aigc.market_editor import TARGETS, confirm, save, set_mode
from amazon_aigc.publication import image_receipt, publish_listing, withdraw_registration
from amazon_aigc.schemas import DocumentWrite
from amazon_aigc.workbench_query import workbench_page

BRIEF = {"product": "两个合成托盘", "selling_points": "大小搭配", "visual_focus": "实际包含物", "plans": [{"purpose": "白底主图", "composition": "两个托盘纯白底", "text": ""}, {"purpose": "材质", "composition": "细节", "text": "真实材质"}]}
TEXTS = {s: ["", "Material " + s] for s in TARGETS}


def plan(db, setup, source="DE", sites=None):
    users, item, _ = setup
    with db.connection() as c:
        c.execute("UPDATE work_items SET site=%s WHERE id=%s", (source, item))
    prepare(db, users["operator"], item, "facts", FACTS)
    with db.connection() as c:
        d = detail(c, users["operator"], item)
        return set_mode(c, users["operator"], item, d["document"]["revision"], {"mode": "pan_eu", "targets": sites or list(TARGETS)})


def confirmed(db, setup):
    users, item, _ = setup
    d = plan(db, setup)
    with db.connection() as c:
        return confirm(c, users["operator"], item, d["document"]["revision"], {"kind": "brief", "targets": list(TARGETS), "shared_brief": BRIEF, "image_texts": TEXTS, "acknowledged": True})


def cmd(db, setup, task, action, site=None, actor="artist", **payload):
    users, item, _ = setup
    with db.connection() as c:
        d = detail(c, users["operator"], item)
        t = next(t for t in d["tasks"] if t["id"] == task)
        if site:
            m = next(m for m in d["task_markets"] if m["task_id"] == task and m["site"] == site)
            payload.update(site=site, market_revision=m["revision"])
        return task_command(c, users[actor], task, action, t["revision"], payload)


@pytest.mark.parametrize("site", ["DE", "FR", "ES"])
def test_explicit_plan_and_source_history_preserved(db, setup, site):
    users, item, _ = setup
    prepare(db, users["operator"], item, "facts", FACTS)
    before = prepare(db, users["operator"], item, "listing", LISTING)
    d = plan(db, setup, site)
    assert d["item"]["site"] == site and d["item"]["editor_mode"] == "pan_eu"
    assert d["document"]["body"]["listing"] == before["document"]["body"]["listing"]
    with db.connection() as c:
        result = set_mode(c, users["operator"], item, d["document"]["revision"], {"mode": "single"})
        assert result["document"]["listing_version"] == before["document"]["listing_version"]
        assert result["market_plan"]


@pytest.mark.parametrize("site", ["US", "UK", "CA", "AU", "MX", "AE", "SA", "JP", "IT", "NL"])
def test_other_sources_cannot_enable_plan(db, setup, site):
    users, item, _ = setup
    with pytest.raises(HTTPException), db.connection() as c:
        c.execute("UPDATE work_items SET site=%s WHERE id=%s", (site, item))
        set_mode(c, users["operator"], item, 0, {"mode": "pan_eu"})


def test_batch_confirmation_atomic_idempotent_and_drafts_not_confirmation(db, setup):
    users, item, _ = setup
    d = plan(db, setup)
    revision = d["document"]["revision"]
    bad = {"kind": "listing", "targets": list(TARGETS), "acknowledged": True, "listings": {s: LISTING for s in TARGETS}}
    bad["listings"]["NL"] = {**LISTING, "title": ""}
    with pytest.raises(HTTPException), db.connection() as c:
        confirm(c, users["operator"], item, revision, bad)
    with db.connection() as c:
        assert not c.execute("SELECT 1 FROM market_contents WHERE document_id=%s AND listing_version IS NOT NULL", (setup[2],)).fetchone()
        draft = save(c, users["operator"], item, revision, {"listings": {s: LISTING for s in TARGETS}})
        assert draft["market_editing"]["listing"]
        request = {**bad, "listings": {s: LISTING for s in TARGETS}}
        key = str(uuid4())
        operation = lambda: confirm(c, users["operator"], item, draft["document"]["revision"], request)
        result = idempotent(c, users["operator"], key, request, operation)
        assert idempotent(c, users["operator"], key, request, operation) == result
        again = confirm(c, users["operator"], item, result["document"]["revision"], request)
        assert again["document"]["revision"] == result["document"]["revision"]
        assert not result["market_editing"]["listing"]
        old = result["market_plan"]["contents"][0]["listing_version"]
        saved = save(c, users["operator"], item, result["document"]["revision"], {"listings": {"DE": {**LISTING, "title": "New draft"}}})
        assert saved["market_editing"]["listing"]
        assert saved["market_plan"]["contents"][0]["listing_version"] == old
        with pytest.raises(HTTPException):
            write_document(c, users["operator"], item, DocumentWrite(revision=saved["document"]["revision"], section="listing", body=LISTING))


def test_market_full_kits_returns_download_and_queued_revisions(db, setup):
    users, item, _ = setup
    d = confirmed(db, setup)
    t = d["tasks"][0]["id"]
    assert len(d["tasks"]) == 1 and len(d["task_markets"]) == 5
    cmd(db, setup, t, "claim")
    cmd(db, setup, t, "start")
    ids = [image(db, item, users["artist"], n) for n in range(2)]
    with pytest.raises(HTTPException):
        cmd(db, setup, t, "deliver", "FR", asset_ids=ids[:1])
    d = cmd(db, setup, t, "deliver", "FR", asset_ids=ids)
    assert d["tasks"][0]["current_delivery_id"] is None
    with db.connection() as c:
        rows = workbench_page(c, users["operator"], tab="review", state="pending")["items"]
        assert str(item) in [r["id"] for r in rows]
    cmd(db, setup, t, "review", "FR", actor="operator", result="rejected", image_notes={ids[1]: "翻译修正"})
    d = cmd(db, setup, t, "deliver", "FR", asset_ids=ids)
    assert d["deliveries"][0]["market_version"] == 2
    cmd(db, setup, t, "review", "FR", actor="operator", result="accepted")
    with db.connection() as c:
        r = detail(c, users["operator"], item)
        fd = next(x for x in r["deliveries"] if x["marketplace"] == "FR" and x["result"] == "accepted")
        r = image_receipt(c, users["operator"], item, r["document"]["revision"], {"site": "FR", "delivery_id": fd["id"]})
        assert r["image_receipts"][0]["marketplace"] == "FR"
        r = confirm(c, users["operator"], item, r["document"]["revision"], {"kind": "brief", "targets": list(TARGETS), "shared_brief": {**BRIEF, "requirements": "修改布局"}, "image_texts": TEXTS, "acknowledged": True})
        assert len(r["tasks"]) == 1 and len(r["market_plan"]["queued"]) == 1
        assert r["tasks"][0]["brief_version"] != r["market_plan"]["shared_version"]
    for site in ("DE", "ES", "IT", "NL"):
        cmd(db, setup, t, "deliver", site, asset_ids=ids)
        d = cmd(db, setup, t, "review", site, actor="operator", result="accepted")
    assert len(d["tasks"]) == 2 and d["tasks"][0]["state"] == "pool" and d["tasks"][1]["state"] == "accepted"
    assert not d["market_plan"]["queued"]
    with pytest.raises(HTTPException):
        cmd(db, setup, t, "cancel-review", "FR", actor="operator")


def test_market_publication_targets_do_not_mix_languages(db, setup):
    users, item, _ = setup
    d = plan(db, setup)
    with db.connection() as c:
        d = confirm(c, users["operator"], item, d["document"]["revision"], {"kind": "listing", "targets": ["FR", "IT"], "listings": {"FR": LISTING, "IT": {**LISTING, "title": "Italian draft"}}, "acknowledged": True})
        for site in ("FR", "IT"):
            d = publish_listing(c, users["operator"], item, d["document"]["revision"], {"site": site, "msku": "SAME-SKU"})
        assert len(d["publications"]) == 2 and len(d["publication_targets"]) == 2
        assert d["document"]["publication_target_id"] is None
        it = next(t for t in d["publication_targets"] if t["marketplace"] == "IT")
        reg = next(p for p in d["publications"] if p["target_id"] == it["id"])
        d = withdraw_registration(c, users["operator"], item, d["document"]["revision"], {"site": "IT", "kind": "copy", "registration_id": reg["id"], "reason": "合成撤回"})
        assert sum(p["withdrawn_at"] is None for p in d["publications"]) == 1


def test_target_list_and_cross_user_commands_are_server_scoped(db, setup):
    users, item, _ = setup
    d = plan(db, setup, sites=["DE"])
    for value in [["DE", "DE"], ["UK"], "DE", [1]]:
        with pytest.raises(HTTPException), db.connection() as c:
            save(c, users["operator"], item, d["document"]["revision"], {"targets": value})
    with pytest.raises(HTTPException), db.connection() as c:
        save(c, users["other"], item, d["document"]["revision"], {"targets": ["DE"]})
    with pytest.raises(HTTPException), db.connection() as c:
        confirm(c, users["operator"], item, d["document"]["revision"], {"kind": "listing", "targets": ["NL"], "listings": {"NL": LISTING}, "acknowledged": True})
    with pytest.raises(HTTPException), db.connection() as c:
        confirm(c, users["operator"], item, d["document"]["revision"], {"kind": "listing", "targets": ["DE"], "listings": {"DE": LISTING}})


def test_withdrawn_common_brief_can_be_resubmitted_without_losing_version(db, setup):
    users, item, _ = setup
    d = confirmed(db, setup)
    original = d["market_plan"]["shared_version"]
    d = cmd(db, setup, d["tasks"][0]["id"], "withdraw", actor="operator")
    assert d["market_editing"]["brief"]
    with db.connection() as c:
        d = confirm(c, users["operator"], item, d["document"]["revision"], {"kind": "brief", "targets": list(TARGETS), "shared_brief": BRIEF, "image_texts": TEXTS, "acknowledged": True})
        assert len(d["tasks"]) == 2 and d["tasks"][0]["state"] == "pool"
        assert d["market_plan"]["shared_version"] == original
        assert not d["market_editing"]["brief"]


def test_review_rows_and_badges_count_independent_target_delivery(db, setup):
    from amazon_aigc.workbench_query import workbench_summary
    users, item, _ = setup
    d = confirmed(db, setup);t = d["tasks"][0]["id"]
    cmd(db, setup, t, "claim");cmd(db, setup, t, "start")
    ids = [image(db, item, users["artist"], n) for n in range(2)]
    for s in ("IT", "NL"):
        cmd(db, setup, t, "deliver", s, asset_ids=ids)
    with db.connection() as c:
        page = workbench_page(c, users["operator"], tab="review", q=str(item), state="pending")
        # The fixture's code is its UUID, and both targets retain that source product.
        assert {r["site"] for r in page["items"]} == {"IT", "NL"}
        assert all(r["review_site"] == "DE" and r["marketplace"] == r["site"] for r in page["items"])
        assert workbench_summary(c, users["operator"])["badges"]["review"] >= 2


def test_copy_and_brief_generation_targets_are_independent(db, setup):
    users, item, _ = setup
    d = plan(db, setup)
    with db.connection() as c:
        d = save(c, users["operator"], item, d["document"]["revision"], {"generation_targets": {"listing": ["DE", "FR"], "brief": ["IT", "NL"]}})
        assert d["item"]["editor_targets"] == list(TARGETS)
        assert d["market_plan"]["generation_targets"] == {"listing": ["DE", "FR"], "brief": ["IT", "NL"]}
