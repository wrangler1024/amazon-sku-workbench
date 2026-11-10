"""Xynigo 邮箱客户端命令行入口。"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import Settings
from .crypto import CryptoError, SecretBox, generate_master_key, hash_password
from .http_api import start_http_server
from .service import Service

logger = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="store-mail-web",
        description="Xynigo 邮箱客户端:多店铺 163 邮箱聚合、风险与资金邮件监控及 Agent API",
    )
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 Web 服务(含后台同步调度)")
    serve.add_argument("--host", default=None, help="覆盖监听地址(默认取环境变量)")
    serve.add_argument("--port", type=int, default=None, help="覆盖监听端口")
    serve.add_argument("--no-scheduler", action="store_true",
                       help="不启动后台同步(调试用)")

    doctor = sub.add_parser("doctor", help="检查本机运行条件")

    sync = sub.add_parser("sync", help="手动同步账号")
    sync.add_argument("--account", default=None,
                      help="账号标识(id/邮箱/店铺名);缺省=全部启用账号")
    sync.add_argument("--force", action="store_true", help="忽略手动同步最小间隔")

    sub.add_parser("reclassify", help="对已入库邮件重跑分类与金额识别(不拉信)")

    sub.add_parser("genkey", help="生成主密钥(STORE_MAIL_WEB_SECRET_KEY)")

    pw = sub.add_parser("hash-password", help="生成管理员密码 scrypt 哈希")
    pw.add_argument("--password", default=None, help="明文密码(缺省交互输入)")

    export = sub.add_parser("export-openapi", help="导出 Agent API 的 OpenAPI 规范")
    export.add_argument("-o", "--output", type=Path, default=None,
                        help="写入文件(缺省打印到标准输出)")
    return root


def run_serve(args, settings: Settings) -> int:
    service = Service.build(settings)
    host = args.host or settings.host
    port = args.port or settings.port
    service.start_background_workers()
    if not args.no_scheduler:
        service.engine.start_scheduler()
    static_dir = Path(__file__).resolve().parent / "static"
    openapi_path = Path(__file__).resolve().parent / "openapi.yaml"
    server = start_http_server(host, port, service, static_dir, openapi_path)
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    logger.info(
        "Xynigo 邮箱客户端 v%s 已启动: http://%s:%s (调度器=%s) 健康检查=/healthz "
        "Agent API 文档=/api/openapi.yaml",
        __version__, host, port,
        "运行" if service.engine.scheduler_running() else "关闭",
    )
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        service.engine.stop_scheduler()
        service.stop_background_workers()
        service.store.close()
    logger.info("已退出")
    return 0


def run_reclassify(settings: Settings) -> int:
    service = Service.build(settings)
    try:
        result = service.reclassify()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        service.store.close()


def run_doctor(settings: Settings) -> int:
    checks: dict[str, object] = {}
    checks["python"] = {"ok": sys.version_info >= (3, 9), "version": sys.version}

    try:
        import cryptography  # noqa: F401
        checks["cryptography"] = {"ok": True}
    except ImportError:
        checks["cryptography"] = {
            "ok": False,
            "error": "未安装 cryptography(授权码加密必需)",
            "hint": "pip install cryptography 或 apt install python3-cryptography",
        }

    box_ok = False
    if settings.secret_key:
        try:
            SecretBox(settings.secret_key)
            checks["secret_key"] = {"ok": True, "configured": True}
            box_ok = True
        except CryptoError as exc:
            checks["secret_key"] = {"ok": False, "error": str(exc)}
    else:
        checks["secret_key"] = {
            "ok": False,
            "error": "未配置 STORE_MAIL_WEB_SECRET_KEY(store-mail-web genkey 生成)",
        }

    checks["admin_password"] = {
        "ok": settings.admin_password_configured(),
        "mode": "hash" if settings.admin_password_hash else (
            "plain" if settings.admin_password else "missing"),
    }
    checks["imap"] = {
        "ok": bool(settings.imap_host),
        "host": settings.imap_host,
        "port": settings.imap_port,
        "timeout": settings.imap_timeout,
    }
    checks["sync"] = {
        "ok": settings.sync_interval_seconds >= 60,
        "interval_seconds": settings.sync_interval_seconds,
        "jitter_seconds": settings.sync_jitter_seconds,
        "initial_days": settings.initial_days,
        "initial_limit": settings.initial_limit,
        "note": "interval < 60 秒有触发 163 风控风险" if settings.sync_interval_seconds < 60 else None,
    }

    if box_ok:
        service = Service.build(settings)
        try:
            accounts = service.store.list_accounts()
            keys = service.store.list_api_keys()
            checks["database"] = {
                "ok": True,
                "path": str(settings.db_path),
                "accounts": len(accounts),
                "enabled_accounts": sum(1 for a in accounts if a.enabled),
                "messages": service.store.message_count(),
                "api_keys": len(keys),
                "auth_error_accounts": [
                    a.email for a in accounts
                    if a.last_sync_status == "auth_error"
                ],
            }
        finally:
            service.store.close()

    ok = all(
        bool(v.get("ok")) for v in checks.values() if isinstance(v, dict)
    )
    print(json.dumps({"ok": ok, "version": __version__, "checks": checks},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


def run_sync(args, settings: Settings) -> int:
    from .service import ApiError, Service

    service = Service.build(settings)
    try:
        if args.account:
            try:
                account = service.resolve_account(args.account)
            except ApiError as exc:
                print(json.dumps({"ok": False, "error": exc.message},
                                 ensure_ascii=False))
                return 1
            results = [service.engine.sync_account(
                account.id, manual=True, force=args.force)]
        else:
            results = service.engine.sync_all(manual=True, force=args.force)
        payload = [{
            "account_id": r.account_id,
            "email": r.email,
            "status": r.status,
            "new_messages": r.new_messages,
            "fetched": r.fetched,
            "duration_ms": r.duration_ms,
            "error": r.error,
        } for r in results]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if any(r.status in {"error", "auth_error"} for r in results) else 0
    finally:
        service.store.close()


def run_export_openapi(args) -> int:
    path = Path(__file__).resolve().parent / "openapi.yaml"
    if not path.is_file():
        print(f"openapi.yaml 不存在: {path}", file=sys.stderr)
        return 1
    if args.output:
        args.output.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()

    if args.command == "serve":
        return run_serve(args, settings)
    if args.command == "doctor":
        return run_doctor(settings)
    if args.command == "sync":
        return run_sync(args, settings)
    if args.command == "reclassify":
        return run_reclassify(settings)
    if args.command == "genkey":
        print(generate_master_key())
        return 0
    if args.command == "hash-password":
        password = args.password or getpass.getpass("请输入管理密码: ")
        print(hash_password(password))
        return 0
    if args.command == "export-openapi":
        return run_export_openapi(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
