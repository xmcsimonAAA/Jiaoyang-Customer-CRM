# 骄阳定增客户数据系统

这是一个与 MuskZoom 兼容、但业务数据独立存储的客户管理模块。开发环境默认使用 SQLite；生产环境通过 `DATABASE_URL` 使用 PostgreSQL，并通过 MuskZoom 的身份接口和一次性 SSO 接入。

## 已实现

- 复用 MuskZoom 用户、角色和团队；支持 HMAC 单点登录。
- 商务经理仅查看自己的客户，部门主管查看本组，管理员和开发者查看全部。
- 客户新建、编辑、跟进记录、归属变更及完整历史。
- 独立跟踪香港券商开户、定增意向、目标批次、资金到账和实际参与金额。
- 主管管理一至两个月滚动的定增批次，并查看批次闭环、流失和金额进度。
- 主管及以上人工分配、合并重复客户；合并不物理删除原记录。
- `.xlsx` / `.csv` 导入预览、自动列识别和重复联系方式拦截。
- 手机端提供固定快捷入口，可直接录入客户和填写每日跟进，提交后自动进入统一客户池。
- 客户导入、完整数据导出按账号独立授权。
- 审计日志和乐观锁，减少误覆盖。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --reload --port 8010
```

打开 `http://127.0.0.1:8010`。默认会尝试读取 `/Users/simon/Documents/new project/sales_qa.db`，也可以在环境变量中指定：

```bash
MUSKZOOM_DB_PATH=/path/to/sales_qa.db .venv/bin/uvicorn backend.main:app --port 8010
```

独立演示账号仅在显式设置 `CRM_DEMO_MODE=true` 时启用，严禁用于生产服务器。正式环境必须通过 MuskZoom 的身份接口和单点登录进入。

## 单点登录接入

MuskZoom 生成的 SSO token 可提交到 `POST /api/auth/sso`：`{"token": "<payload>.<signature>"}`。两边需配置同一个 `MUSKZOOM_SSO_SECRET`（兼容 MuskZoom 现有的 `PARTNER_CRM_SSO_SECRET`）。正式接入时，MuskZoom 只需增加入口和跳转，不需要合并客户业务数据库。

## 数据库策略

本地开发使用 SQLite，并启用 WAL、事务、外键和审计记录。线上使用 PostgreSQL，客户模块数据库不与聊天质检、报销或渠道业务表混用。可使用 `backend/scripts/migrate_sqlite_to_postgres.py` 将一个经过校验的 SQLite 快照迁移到空的 PostgreSQL 数据库。

生产部署、Cloudflare、Nginx、systemd、备份和 SSO 接入步骤见 [生产部署手册](docs/production-deployment.md)。

## 测试

```bash
.venv/bin/pytest -q
```
