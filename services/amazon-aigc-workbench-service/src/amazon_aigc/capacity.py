"""Team limits and fair claims, serialized only for the short claim transaction."""

DAY_START = "(date_trunc('day',now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')"


def daily_usage(c):
    return c.execute(
        "SELECT count(*) AS total, count(*) FILTER(WHERE payload->>'provider'='openai') AS openai, "
        "count(*) FILTER(WHERE payload->>'provider'='deepseek') AS deepseek "
        "FROM jobs WHERE kind='model' AND created_at>=" + DAY_START
    ).fetchone()


def status(c, settings):
    active = {
        r["kind"]: r["n"]
        for r in c.execute("SELECT kind,count(*) AS n FROM jobs WHERE state='running' GROUP BY kind").fetchall()
    }
    return {
        "team_size": settings.capacity_team_size,
        "model_concurrency": settings.model_concurrency,
        "source_concurrency": settings.source_concurrency,
        "per_operator_concurrency": settings.model_user_concurrency,
        "model_daily_total": settings.model_total_daily_limit,
        "timezone": "Asia/Shanghai",
        "used_today": daily_usage(c),
        "running": active,
    }


def claim(c, settings, kind):
    limit = settings.model_concurrency if kind == "model" else settings.source_concurrency
    # Guard both company and per-operator counts across threads/processes.
    c.execute("SELECT pg_advisory_xact_lock(%s)", (58017008 if kind == "model" else 58017009,))
    count = c.execute("SELECT count(*) AS n FROM jobs WHERE kind=%s AND state='running'", (kind,)).fetchone()["n"]
    if count >= limit:
        return None
    return c.execute(
        "SELECT j.* FROM jobs j LEFT JOIN LATERAL ("
        " SELECT count(*) AS active FROM jobs r"
        " WHERE r.kind=j.kind AND r.actor_id=j.actor_id AND r.state='running'"
        ") a ON true LEFT JOIN LATERAL ("
        " SELECT h.started_at AS last_started FROM jobs h"
        " WHERE h.kind=j.kind AND h.actor_id=j.actor_id AND h.started_at IS NOT NULL"
        " ORDER BY h.started_at DESC LIMIT 1"
        ") h ON true "
        "WHERE j.kind=%s AND j.state='queued' AND j.run_after<=now() "
        "AND (%s<>'model' OR coalesce(a.active,0)<%s) "
        "ORDER BY coalesce(a.active,0),h.last_started ASC NULLS FIRST,j.created_at,j.id "
        "FOR UPDATE OF j SKIP LOCKED LIMIT 1",
        (kind, kind, settings.model_user_concurrency),
    ).fetchone()
