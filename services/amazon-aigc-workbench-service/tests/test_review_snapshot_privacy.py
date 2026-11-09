import pytest
from psycopg.types.json import Jsonb
from test_workflow import pool

from amazon_aigc.domain import detail, task_command
from amazon_aigc.workbench_views import item_rows


@pytest.mark.parametrize("role", ["operator", "artist", "art_lead"])
def test_editor_responses_exclude_selection_financial_snapshot(db, setup, role):
    users, item, _ = setup
    task = pool(db, setup)
    source = {"link": "https://detail.1688.com/offer/123456789.html", "text": "货源"}
    snapshot = {
        "记录序号": [{"text": "R-1046", "type": "text"}],
        "1688货源": source,
        "采购单价": 17.39,
        "预估利润": 4.12,
        "future_private_field": {"confidential": True},
        "提交运营": [{"id": "private-person", "mobile": "private-phone"}],
    }
    with db.connection() as c:
        task_command(c, users["artist"], task["id"], "claim", task["revision"], {})
        c.execute("UPDATE work_items SET review_snapshot=%s WHERE id=%s", (Jsonb(snapshot), item))
        result = detail(c, users[role], item)
        assert result["item"]["review_snapshot"] == {"1688货源": source}
        assert result["item"]["task_number"] == "R-1046"
        rows = item_rows(c, users[role])
        row = next(row for row in rows if row["id"] == str(item))
        assert "review_snapshot" not in row
        assert row["task_number"] == "R-1046"
        assert row["owner_name"] == "operator"
        stored = c.execute("SELECT review_snapshot FROM work_items WHERE id=%s", (item,)).fetchone()
        assert stored["review_snapshot"] == snapshot
