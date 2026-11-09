"""Read models for the unified artwork and publication pages."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .domain import active_artist, can_read, editing_status, public_item, serial


def item_rows(c, user):
    rows = c.execute("""
      SELECT w.*,u.name AS owner_name,
        CASE WHEN coalesce(w.review_snapshot->'产品图片'->0->>'file_token','')<>''
          THEN (SELECT a.id FROM assets a WHERE a.work_item_id=w.id
            AND a.source_url='feishu-product-image:' || (w.review_snapshot->'产品图片'->0->>'file_token')
            ORDER BY a.created_at LIMIT 1)
          ELSE (SELECT a.id FROM assets a WHERE a.work_item_id=w.id
            AND (a.source_url IS NULL OR a.source_url NOT LIKE 'feishu-product-image:%%')
            ORDER BY a.created_at LIMIT 1)
        END AS thumbnail_id,
        d.id AS document_id,d.revision,d.facts_version,d.listing_version,d.brief_version,
        d.body->'legacy_edit_request' AS legacy_edit_request,
        (SELECT count(*) FROM publication_records p WHERE p.document_id=d.id AND p.target_id=d.publication_target_id AND p.withdrawn_at IS NULL) AS publication_count,
        p.listing_version AS published_version, to_jsonb(p) AS publication,to_jsonb(pt) AS publication_target,
        to_jsonb(ir) AS image_registration,
        (SELECT count(*) FROM image_receipts r JOIN artwork_deliveries xd ON xd.id=r.delivery_id JOIN artwork_tasks xt ON xt.id=xd.task_id WHERE xt.document_id=d.id) AS image_history_count,
        (SELECT count(*) FROM image_receipts r JOIN artwork_deliveries xd ON xd.id=r.delivery_id JOIN artwork_tasks xt ON xt.id=xd.task_id WHERE xt.document_id=d.id AND r.withdrawn_at IS NULL) AS image_previous_count,
        to_jsonb(t) AS task, to_jsonb(ad) AS delivery,
        (ir.delivery_id=ad.id AND ad.result='accepted' AND t.state='accepted') AS images_uploaded
      FROM work_items w JOIN content_documents d ON d.work_item_id=w.id
      LEFT JOIN users u ON u.id=w.owner_id
      LEFT JOIN publication_targets pt ON pt.id=d.publication_target_id
      LEFT JOIN LATERAL(SELECT * FROM publication_records WHERE document_id=d.id AND target_id=pt.id AND withdrawn_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 1)p ON true
      LEFT JOIN LATERAL(SELECT r.* FROM image_receipts r JOIN artwork_deliveries xd ON xd.id=r.delivery_id JOIN artwork_tasks xt ON xt.id=xd.task_id WHERE xt.document_id=d.id AND r.invalidated_at IS NULL AND r.withdrawn_at IS NULL ORDER BY r.created_at DESC,r.id DESC LIMIT 1)ir ON true
      LEFT JOIN LATERAL(SELECT * FROM artwork_tasks WHERE document_id=d.id ORDER BY batch DESC LIMIT 1)t ON true
      LEFT JOIN artwork_deliveries ad ON ad.id=t.current_delivery_id
      ORDER BY w.updated_at DESC,w.id
    """).fetchall()
    return serial([{**public_item(r), "editing": editing_status(r, r["task"])} for r in rows if can_read(c, user, r)])


def artist_rows(c):
    rows = c.execute("""
      SELECT u.*,count(t.id) FILTER(WHERE t.state='waiting') AS waiting,
        count(t.id) FILTER(WHERE t.state='working') AS working,
        count(t.id) FILTER(WHERE t.state='pending') AS pending,
        count(t.id) FILTER(WHERE t.state='revision') AS revision,
        count(t.id) FILTER(WHERE t.state='accepted') AS accepted,
        min(t.started_at) AS first_started_at,
        avg(CASE WHEN t.first_review_result='accepted' THEN 1.0 WHEN t.first_review_result='rejected' THEN 0.0 END) AS first_pass_rate
      FROM users u LEFT JOIN artwork_tasks t ON t.assignee_id=u.id
      WHERE 'artist'=ANY(u.roles) OR 'art_lead'=ANY(u.roles)
      GROUP BY u.id ORDER BY ('art_lead'=ANY(u.roles)) DESC,u.created_at,u.id
    """).fetchall()
    baseline = {r["artist_id"]: r for r in c.execute("SELECT artist_id,count(*) FILTER(WHERE cohort='history') AS accepted,min(first_started_at) AS first_started_at FROM legacy_migration_records WHERE artist_id IS NOT NULL GROUP BY artist_id").fetchall()}
    for r in rows:
        b = baseline.get(r["id"])
        if b:
            r["accepted"] += b["accepted"]
            dates = [v for v in (r["first_started_at"], b["first_started_at"]) if v]
            r["first_started_at"] = min(dates) if dates else None
    # Historical verdict wins for the same taken-over batch; later new batches
    # are independent work. Do not count a re-accepted repair as a new first pass.
    rates = {r["artist_id"]: r for r in c.execute("""
      WITH decisions AS (
        SELECT artist_id,result FROM legacy_first_reviews
        UNION ALL
        SELECT t.assignee_id,t.first_review_result FROM artwork_tasks t
        JOIN content_documents d ON d.id=t.document_id
        WHERE t.first_review_result IN ('accepted','rejected') AND NOT (
          t.legacy_takeover AND EXISTS (
            SELECT 1 FROM legacy_migration_records l JOIN legacy_first_reviews f
              ON (f.base_id,f.table_id,f.record_id)=(l.base_id,l.table_id,l.record_id)
            WHERE l.work_item_id=d.work_item_id AND f.artist_id=t.assignee_id
          )
        )
      ) SELECT artist_id,count(*) AS reviewed,count(*) FILTER(WHERE result='accepted') AS passed
        FROM decisions GROUP BY artist_id
    """).fetchall()}
    for r in rows:
        rate = rates.get(r["id"])
        r["first_pass_rate"] = rate["passed"] / rate["reviewed"] if rate else None
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    for r in rows:
        first = r["first_started_at"]
        r["drawing_days"] = (today - first.astimezone(ZoneInfo("Asia/Shanghai")).date()).days + 1 if first else None
        if r["drawing_days"] is not None and r["drawing_days"] < 1:
            r["drawing_days"] = None
        r["eligible"] = bool(active_artist(r))
    return serial(rows)
