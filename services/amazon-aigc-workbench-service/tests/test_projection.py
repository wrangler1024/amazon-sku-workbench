from types import SimpleNamespace

from test_workflow import pool

from amazon_aigc.domain import task_command
from amazon_aigc.sync import projection


def test_artist_projection_uses_correct_identity_namespace(db, setup):
    users, item, _ = setup
    task = pool(db, setup)
    with db.connection() as c:
        for provider, subject in [("feishu", "ou_legacy_test"), ("feishu_union", "on_union_test")]:
            c.execute(
                "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES(%s,%s,%s,%s)",
                (provider, "test", subject, users["artist"]["id"]),
            )
        task_command(c, users["artist"], task["id"], "claim", 0, {})
        fields = projection(c, item, SimpleNamespace(s=SimpleNamespace(feishu_mode="openapi")))
        assert fields["美工"] == [{"id": "on_union_test"}]
        assert fields["制图状态"] == ["待制图"]
        assert isinstance(fields["制图派单时间"], int)
        assert fields["计划制图数量"] == 1
        assert len(fields["编辑资料"]) < 1000
