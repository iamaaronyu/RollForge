-- 仅用于固定 Embed 的 local-dev-team：Harbor 0.24.0 创建 Sandbox 时申请 24 小时。
-- team_limits 是只读视图；在 project_limits 保存团队覆盖值。
-- 不修改全局 tier；时长 24 小时、默认空闲磁盘 2048 MiB，其余限额保持。
INSERT INTO project_limits (
    team_id, max_length_hours, concurrent_sandboxes, concurrent_template_builds,
    max_vcpu, max_ram_mb, disk_mb, events_ttl_days, default_free_disk_size_mb,
    max_disk_size_mb, max_free_disk_size_mb
)
SELECT teams.id, 24, limits.concurrent_sandboxes, limits.concurrent_template_builds,
       limits.max_vcpu, limits.max_ram_mb, 2048, limits.events_ttl_days,
       2048, limits.max_disk_size_mb, limits.max_free_disk_size_mb
FROM teams JOIN team_limits AS limits ON limits.id = teams.id
WHERE teams.slug = 'local-dev-team'
ON CONFLICT (team_id) DO UPDATE
SET max_length_hours = EXCLUDED.max_length_hours,
    disk_mb = EXCLUDED.disk_mb,
    default_free_disk_size_mb = EXCLUDED.default_free_disk_size_mb,
    updated_at = now()
RETURNING max_length_hours, default_free_disk_size_mb;
