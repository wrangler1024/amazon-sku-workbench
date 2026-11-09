from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Explicit server-owned target allowlist, loaded once at process startup.
# Defaults are synthetic fixtures and cannot address an existing tenant.
import json
import os

_DEFAULT_TARGETS = {
    "integration": {"base_id": "EXAMPLE_INTEGRATION_BASE", "table_id": "tblExampleIntegration"},
    "production": {
        "base_id": "EXAMPLE_PRODUCTION_BASE", "table_id": "tblExampleProduction",
        "app_id": "cli_0000000000000002",
        "plugin_origin": "https://blk_000000000000000000000002.feishupkg.com",
        "personnel_table_id": "tblExamplePeople",
    },
}
_target_file = os.environ.get("AIGC_TARGETS_FILE")
APPROVED_TARGETS = json.loads(Path(_target_file).read_text()) if _target_file else _DEFAULT_TARGETS
for _name in ("integration", "production"):
    if not all(isinstance(APPROVED_TARGETS[_name].get(k), str) and APPROVED_TARGETS[_name][k]
               for k in ("base_id", "table_id")):
        raise ValueError("Target allowlist needs explicit Base and table identifiers")
TEST_BASE = APPROVED_TARGETS["integration"]["base_id"]
TEST_TABLE = APPROVED_TARGETS["integration"]["table_id"]
PRODUCTION_BASE = APPROVED_TARGETS["production"]["base_id"]
PRODUCTION_TABLE = APPROVED_TARGETS["production"]["table_id"]
TARGETS = {"test": (TEST_BASE, TEST_TABLE), "integration": (TEST_BASE, TEST_TABLE),
           "production": (PRODUCTION_BASE, PRODUCTION_TABLE)}


def valid_target(environment, base_id, table_id):
    return TARGETS.get(environment) == (base_id, table_id)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIGC_", extra="ignore")
    database_url: str = ""
    environment: str = "integration"
    base_id: str = TEST_BASE
    sku_table_id: str = TEST_TABLE
    tenant: str = "example"
    feishu_base_origin: str = "https://example.feishu.cn"
    storage_root: Path = Path("runtime/assets")
    feishu_mode: str = "cli"
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_user_token: str = ""
    public_url: str = "http://127.0.0.1:8798"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com"
    openai_model: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    model_enabled: bool = False
    model_daily_limit: int = Field(default=0, ge=0, le=10000)  # 0 means unlimited.
    model_concurrency: int = Field(default=1, ge=1, le=8)
    source_concurrency: int = Field(default=1, ge=1, le=4)
    model_user_concurrency: int = Field(default=2, ge=1, le=8)
    model_total_daily_limit: int = Field(default=0, ge=0, le=10000)
    capacity_team_size: int = Field(default=20, ge=1, le=100)
    render_enabled: bool = True
    sync_enabled: bool = True
    base_login_enabled: bool = False
    personnel_table_id: str = ""
    production_enabled: bool = False
    base_auth_table: str = "tblExampleProofTest"
    base_auth_fields: dict[str, tuple[str, int]] = {
        "校验摘要": ("fldExampleProof", 1), "创建人": ("fldExample17e46ecf", 1003),
        "创建时间": ("fldExample15cc4591", 1001), "更新人": ("fldExample82574217", 1004),
        "更新时间": ("fldExample40c2110c", 1002),
    }
    base_parent_origin: str = "https://blk_000000000000000000000001.feishupkg.com"
    base_view_id: str = "vewExampleTest"
    session_hours: int = 8
    web_root: Path = Path("../../apps/editor/dist")

    @model_validator(mode="after")
    def guard(self):
        if not valid_target(self.environment, self.base_id, self.sku_table_id):
            raise ValueError("Unknown or mismatched Base target refused")
        if self.environment == "production":
            approved = APPROVED_TARGETS["production"]
            if (self.feishu_app_id, self.base_parent_origin, self.personnel_table_id) != (
                approved.get("app_id"), approved.get("plugin_origin"), approved.get("personnel_table_id")
            ):
                raise ValueError("Production app, plugin origin and role authority must match approved targets")
            if not self.production_enabled or self.feishu_mode != "openapi":
                raise ValueError("Production requires explicit enablement and app transport")
            if self.base_auth_table == "tblExampleProofTest" or self.base_view_id == "vewExampleTest":
                raise ValueError("Production identity table and view must be configured separately")
            if not self.public_url.startswith("https://") or "workbench-test" in self.public_url:
                raise ValueError("Production requires its own HTTPS service")
        import re
        if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.(feishu\.cn|larksuite\.com)", self.feishu_base_origin):
            raise ValueError("Feishu Base origin must be an exact tenant HTTPS origin")
        if not re.fullmatch(r"https://blk_[a-f0-9]+\.feishupkg\.com", self.base_parent_origin):
            raise ValueError("Plugin origin must be an exact Feishu block origin")
        if not self.database_url.startswith("postgresql://"):
            raise ValueError("AIGC_DATABASE_URL must use PostgreSQL")
        if self.feishu_mode not in ("cli", "openapi"):
            raise ValueError("Unknown Feishu transport")
        if not self.openai_base_url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise ValueError("AIGC_OPENAI_BASE_URL must use HTTPS outside local loopback tests")
        return self


def settings():
    import os

    # DATABASE_URL compatibility for standard database deployments.
    return Settings(database_url=os.environ.get("AIGC_DATABASE_URL", os.environ.get("DATABASE_URL", "")))
