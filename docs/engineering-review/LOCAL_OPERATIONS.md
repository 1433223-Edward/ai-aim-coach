# 本地运行与安全恢复

适用范围：本地、单用户 Demo；不是公网部署说明。Python 3.12、Node 22+。从仓库根目录执行。

## 安装与检查

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-runtime.txt
python -B scripts/run_checks.py
# 只有需要既有 Qwen 功能时才安装并配置 Key：
python -m pip install -r requirements-agent.txt
python -m pip check
```

锁文件来自本轮全新 Windows Python 3.12 环境的实测解析；保留原 requirements.txt，不把开发者完整环境当作最小运行依赖。Linux CI 配置已存在但本轮未远程执行。

配置均由进程环境提供：`AIMCOACH_DB_PATH` 默认仓库 data/aimcoach.db；`AIMCOACH_MODEL` 默认 qwen3.8-flash；`AIMCOACH_MODEL_BASE_URL` 默认既有 DashScope compatible-mode/v1 HTTPS endpoint；`DASHSCOPE_API_KEY` 仅 Agent 需要。只将 Key 提供给可信 endpoint，建议 HTTPS；不要打印 Key。

从任意目录调用仓库的 run.ps1 会临时切换到脚本所在目录。默认只监听 127.0.0.1:8000。Key 缺失不会阻止训练、保存及分析。

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

storage=ready 表示只读访问所需两张表成功，不代表磁盘剩余空间或下一次写入保证成功。agent=configured_not_probed 只表示 Key、SDK 与配置可用，未发起模型请求；不保证 Key 有效、配额或网络正常。unavailable 时仍可训练。storage 不可用时 HTTP 503。

## 隔离 Demo 与真实 HTTP 检查

```powershell
# 自动创建临时合成 DB、启动两次本地服务、检查 HTTP，然后停止并清理临时目录：
python -B scripts/check_local_startup.py

# 人工验收使用新的、明确命名的合成 DB；先不把它设置为配置库。
Remove-Item Env:AIMCOACH_DB_PATH -ErrorAction SilentlyContinue
python -B -m scripts.seed_demo --db "$env:TEMP\aimcoach-acceptance-demo.db"
$env:AIMCOACH_DB_PATH = "$env:TEMP\aimcoach-acceptance-demo.db"
.\run.ps1
```

若 Demo 目标已存在且不属于已标记合成数据，seed 会拒绝。更换新文件名；不要删除训练库来让命令通过。合成 Demo 已完成九轮，点击训练区可显式创建新实验。

## 备份和恢复

脚本使用 SQLite backup API，包含已提交的 WAL 内容；源以只读连接打开。目标必须不存在，任何已有目标都拒绝覆盖。备份包含原始 Session，应作为私人训练资料保存。

```powershell
python -B scripts/backup_database.py --source "$env:TEMP\aimcoach-acceptance-demo.db" --destination "$env:TEMP\aimcoach-demo-backup.db"
# 恢复到另一新文件。先停止服务，保留旧库，然后切换配置；不覆盖旧训练库。
python -B scripts/backup_database.py --source "$env:TEMP\aimcoach-demo-backup.db" --destination "$env:TEMP\aimcoach-demo-restored.db"
$env:AIMCOACH_DB_PATH = "$env:TEMP\aimcoach-demo-restored.db"
.\run.ps1
```

脚本执行 integrity_check 并返回 Session 行数。回归测试另外逐行比较两张表及原始 raw_data；SQLite 文件页布局可能不同，不要求备份文件字节 hash 相同。源文件 hash 不变已在隔离数据库测试，真实库未运行备份或写入。

## 边界

不开放公网；没有用户隔离、TLS 网关和全局并发成本控制。取消前端等待不会保证已发往供应商的请求立即取消，已有 runtime 预算继续生效。客户端无存储容量时只能保留内存并提示导出；不可保证强制关闭后的恢复。冲突 pending 数据应先导出交给维护者，不能自动丢弃。
