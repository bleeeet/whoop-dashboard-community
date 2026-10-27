-- WHOOP 私人健康网站的持久化表（CloudBase PostgreSQL）。只需执行一次。
-- 网站容器用服务端 API Key（CLOUDBASE_APIKEY，管理员权限）读写这张表。
-- 权限：在 CloudBase「数据库 → 权限」里设为仅管理端可读写，不对匿名用户或登录用户开放。
CREATE TABLE IF NOT EXISTS whoop_state (
    name       text        PRIMARY KEY,
    body       text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
