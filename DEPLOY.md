# 量化交易平台 - 生产部署手册

## 环境要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Docker | 20.10+ | 容器运行时 |
| Docker Compose | v2.x 或 docker-compose 1.29+ | 多容器编排 |
| Node.js | 18+ | 前端构建（可本地构建后上传） |
| 操作系统 | Linux (Ubuntu 20.04+ / CentOS 7+) | 推荐 Ubuntu 22.04 LTS |

**推荐配置**：2核4G + 50G磁盘（用于 PostgreSQL 持久化存储和日志）

---

## 〇、部署前置操作（在服务器上执行）

以下步骤必须在你的服务器上直接操作，不可在开发机上执行。

### 步骤 1：安装 Docker

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | bash
apt install docker-compose-plugin

# CentOS / RHEL
yum install -y docker
systemctl enable docker && systemctl start docker
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
```

验证：

```bash
docker --version
docker compose version
```

### 步骤 2：同步代码到服务器

```bash
# 方式A：git clone
git clone <仓库地址> /opt/quant-trade
cd /opt/quant-trade

# 方式B：rsync（从开发机上传）
# rsync -avz --exclude 'node_modules' --exclude 'venv' --exclude '*.db' ./ root@<服务器IP>:/opt/quant-trade/
```

### 步骤 3：生成密钥

**以下密钥必须在服务器上重新生成，不要复用开发环境的。**

```bash
cd /opt/quant-trade
cp backend/.env.example backend/.env

# 生成 SECRET_KEY
echo "SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")"

# 生成 POSTGRES_PASSWORD
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"

# 生成 REDIS_PASSWORD
echo "REDIS_PASSWORD=$(openssl rand -hex 16)"
```

把上面三个命令的输出填入 `backend/.env` 对应字段。

### 步骤 4：配置 SSL 证书

**有域名（推荐）：**

```bash
apt install certbot -y
certbot certonly --standalone -d your-domain.com
cp /etc/letsencrypt/live/your-domain.com/fullchain.pem certs/nginx.crt
cp /etc/letsencrypt/live/your-domain.com/privkey.pem certs/nginx.key
```

然后修改 `nginx/nginx.conf`，将两处 `server_name localhost` 改为你的域名。

**无域名（测试用）：**

```bash
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/nginx.key \
  -out certs/nginx.crt \
  -subj "/CN=<服务器公网IP>"
```

### 步骤 5：修改 nginx server_name

编辑 `nginx/nginx.conf`，第17行和第24行：

```nginx
server_name your-domain.com;   # 替换 localhost
```

### 步骤 6：部署

```bash
bash scripts/deploy.sh
```

### 步骤 7：部署后安全检查

部署完成后，在服务器上按以下顺序逐项执行：

```bash
# 1. 验证所有容器正常运行
docker compose ps
# 应该看到 6 个容器全部 healthy/up

# 2. 验证 HTTPS 访问
curl -k https://localhost/api/v1/health
# 应返回 {"status":"ok"}

# 3. 登录前端，修改默认密码
# 用户名 admin，密码 admin123 → 立即修改！

# 4. 配置防火墙（UFW 示例）
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw deny 5432/tcp
ufw deny 6379/tcp
ufw deny 8000/tcp
ufw enable

# 5. 固定 CORS 域名
# 编辑 backend/.env：CORS_ORIGINS=https://你的域名
# 修改后重启：docker compose restart backend
```

### 操作清单速查

| # | 操作 | 在哪执行 | 必须？ |
|---|------|----------|--------|
| 1 | 安装 Docker | 服务器 | 是 |
| 2 | 同步代码 | 开发机→服务器 | 是 |
| 3 | 重新生成 SECRET_KEY / DB密码 / Redis密码 | 服务器 | 是 |
| 4 | SSL 证书 + 修改 server_name | 服务器 | 是 |
| 5 | `bash scripts/deploy.sh` | 服务器 | 是 |
| 6 | 改默认用户密码 | 浏览器 | 是 |
| 7 | 防火墙 + 固定 CORS | 服务器 | 是 |

---

## 一、快速部署

### 1. 获取代码

```bash
git clone <仓库地址> quant-trade
cd quant-trade
```

### 2. 配置环境变量

```bash
# 从模板创建 .env
cp backend/.env.example backend/.env

# 编辑 .env，将 <CHANGE_ME> 替换为实际值
vim backend/.env
```

**必须修改的字段**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `SECRET_KEY` | JWT 签名密钥 | 用 `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` 生成 |
| `POSTGRES_PASSWORD` | 数据库密码 | 至少16位随机字符串 |
| `REDIS_PASSWORD` | Redis 密码 | 至少16位随机字符串 |

### 3. 配置 SSL 证书

**方案A：自签名证书（测试）**

```bash
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/nginx.key \
  -out certs/nginx.crt \
  -subj "/C=CN/ST=Shanghai/L=Shanghai/O=QuantTrade/CN=<你的服务器IP>"
```

**方案B：Let's Encrypt 免费证书（生产推荐）**

```bash
# 安装 certbot
apt install certbot -y

# 申请证书
certbot certonly --standalone -d your-domain.com

# 复制到项目
cp /etc/letsencrypt/live/your-domain.com/fullchain.pem certs/nginx.crt
cp /etc/letsencrypt/live/your-domain.com/privkey.pem certs/nginx.key
```

同时修改 `nginx/nginx.conf` 中的 `server_name` 为你的域名。

### 4. 一键部署

```bash
bash scripts/deploy.sh
```

脚本自动执行：环境检查 → 前端构建 → 停止旧服务 → 启动所有容器 → 数据库迁移 → 种子数据

---

## 二、首次部署后的手动步骤

### 1. 初始化种子数据（如果自动执行失败）

```bash
docker compose exec backend python scripts/seed_data.py
```

创建默认用户：
- 管理员：`admin` / `admin123`
- 交易员：`trader` / `trader123`

**首次登录后务必修改密码。**

### 2. 拉取市场数据

```bash
# 初始化行情数据（K线 + 实时报价）
docker compose exec backend python scripts/init_market_data.py
```

### 3. 验证服务状态

```bash
# 检查所有容器运行状态
docker compose ps

# 检查健康端点
curl -k https://localhost/api/v1/health

# 查看各服务日志
docker compose logs -f backend
docker compose logs -f celery-worker
```

---

## 三、服务架构

```
                    HTTPS (:443)
                        │
                    ┌───┴───┐
                    │ Nginx │  ← 静态文件 + API 反向代理 + SSL 终止
                    └───┬───┘
                        │
                ┌───────┴────────┐
                │                │
           /api/*           /* (SPA)
                │                │
        ┌───────┴───────┐  静态文件
        │   FastAPI      │
        │   (backend)    │  ← :8000
        └───┬───┬───┬───┘
            │   │   │
    ┌───────┘   │   └───────┐
    │           │           │
┌───┴───┐ ┌────┴────┐ ┌───┴──────────┐
│PostgreSQL│ Redis  │ │ Celery Worker │  ← 异步任务
│  :5432  │ :6379  │ │ Celery Beat   │  ← 定时调度
└─────────┘ └────────┘ └──────────────┘
```

**6个容器**：

| 容器 | 端口 | 职责 |
|------|------|------|
| `quant-nginx` | 80, 443 | HTTPS 终止、静态文件、反向代理 |
| `quant-backend` | 8000 | FastAPI REST API + WebSocket |
| `quant-postgres` | 5432 | 持久化数据（用户、K线、策略、订单） |
| `quant-redis` | 6379 | 缓存 + Celery 消息队列 |
| `quant-celery-worker` | - | 异步任务执行（行情、策略、信号） |
| `quant-celery-beat` | - | 定时任务调度器 |

---

## 四、常用运维命令

### 服务管理

```bash
docker compose up -d            # 启动所有服务
docker compose down             # 停止所有服务
docker compose restart backend  # 重启单个服务
docker compose stop celery-worker  # 暂停单个服务
docker compose up -d --build    # 重新构建并启动
```

### 日志查看

```bash
docker compose logs -f backend          # 跟踪后端日志
docker compose logs -f celery-worker    # 跟踪 Celery 日志
docker compose logs --tail=100 backend  # 最近100行
docker compose logs --since=10m         # 最近10分钟
```

### 数据库操作

```bash
# 进入 PostgreSQL
docker compose exec postgres psql -U quant_user -d quant_trade

# 导出数据库
docker compose exec postgres pg_dump -U quant_user quant_trade > backup.sql

# 恢复数据库
docker compose exec -T postgres psql -U quant_user quant_trade < backup.sql

# 创建新迁移
docker compose exec backend alembic revision --autogenerate -m "描述"

# 执行迁移
docker compose exec backend alembic upgrade head

# 回滚迁移
docker compose exec backend alembic downgrade -1
```

### Redis 操作

```bash
# 进入 Redis CLI
docker compose exec redis redis-cli

# 清空缓存（不影响 Celery 队列）
docker compose exec redis redis-cli FLUSHDB
```

---

## 五、配置详解

### 行情数据源

`MARKET_DATA_PROVIDER` 可选值：

| 值 | 说明 | 适用场景 |
|----|------|----------|
| `sina` | 新浪财经（免费、实时） | A股行情 |
| `eastmoney` | 东方财富（免费、批量） | 全A股扫描 |
| `eastmoney_skills` | 东方财富妙想 API | 需要基本面的策略 |
| `akshare` | 开源数据接口 | 备选方案 |

### 交易模式

`ORDER_EXECUTION_MODE` 可选值：

| 值 | 说明 |
|----|------|
| `sandbox` | 模拟交易（默认，安全） |
| `eastmoney` | 东方财富实盘（需在 Windows 上运行 `eastmoney_agent.py`） |

### 数据库双模式

系统根据 `DEBUG` 自动切换：

```
DEBUG=true  → SQLite (quant_trade.db)     开发/测试
DEBUG=false → PostgreSQL                   生产
```

---

## 六、安全加固清单

部署后按以下清单逐项确认：

- [ ] `DEBUG=false`（必须）
- [ ] `SECRET_KEY` 已更换为强随机串
- [ ] PostgreSQL 密码不为默认值
- [ ] Redis 已设置密码
- [ ] `CORS_ORIGINS` 已指定具体域名
- [ ] `DATA_AUTHENTICITY_STRICT=true`（禁止降级到 mock）
- [ ] SSL 证书为正式 CA 签发（非自签名）
- [ ] 443 端口可正常访问 HTTPS
- [ ] 防火墙仅开放 443（和 80 用于重定向），关闭 5432/6379/8000 的公网访问
- [ ] 首次登录后修改默认用户密码
- [ ] 定期备份 PostgreSQL 数据

### 防火墙配置（UFW 示例）

```bash
ufw allow 22/tcp      # SSH
ufw allow 80/tcp      # HTTP（重定向到 HTTPS）
ufw allow 443/tcp     # HTTPS
ufw deny 5432/tcp     # PostgreSQL（禁止公网访问）
ufw deny 6379/tcp     # Redis（禁止公网访问）
ufw deny 8000/tcp     # Backend（禁止公网访问）
ufw enable
```

---

## 七、备份策略

### 数据库备份

```bash
# 每日备份脚本，加入 crontab
cat > /opt/scripts/backup-db.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/quant"
mkdir -p "$BACKUP_DIR"
cd /path/to/quant-trade
docker compose exec -T postgres pg_dump -U quant_user quant_trade \
  | gzip > "$BACKUP_DIR/quant_$(date +%Y%m%d_%H%M).sql.gz"
# 保留最近 30 天的备份
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete
EOF

# 每天凌晨 3 点执行
echo "0 3 * * * bash /opt/scripts/backup-db.sh" | crontab -
```

### 完整备份

```bash
# 备份 docker-compose 配置、certs 和自定义策略
tar czf quant-backup-$(date +%Y%m%d).tar.gz \
  docker-compose.yml \
  backend/.env \
  certs/ \
  nginx/nginx.conf
```

---

## 八、故障恢复

### 服务不健康

```bash
# 查看具体哪个容器出问题
docker compose ps

# 查看出问题容器的日志
docker compose logs <容器名> --tail=50

# 重启问题容器
docker compose restart <容器名>

# 全部重建
docker compose down && docker compose up -d --build
```

### 数据库连接失败

```bash
# 检查 PostgreSQL 是否健康
docker compose exec postgres pg_isready -U quant_user -d quant_trade

# 重启 PostgreSQL
docker compose restart postgres
```

### 行情数据异常

```bash
# 强制刷新K线
docker compose exec backend python scripts/fetch_missing_klines.py

# 切换数据源：修改 .env 中 MARKET_DATA_PROVIDER 为备选源后重启
docker compose restart backend celery-worker
```

### 回滚

```bash
# 回滚数据库迁移
docker compose exec backend alembic downgrade -1

# 从备份恢复
gunzip -c backup_20260101.sql.gz | docker compose exec -T postgres psql -U quant_user quant_trade

# 回滚到之前的镜像
docker compose down
# 修改 docker-compose.yml 中的镜像标签后
docker compose up -d
```

---

## 九、扩展说明

### 前端无 Docker 部署

如果前端托管在独立的 CDN 或静态服务器上：

```bash
cd frontend
npm install
npm run build
# dist/ 目录即静态文件，上传到 CDN/静态服务器
# 修改 nginx/nginx.conf 中的 proxy_pass 指向实际后端地址
```

### 横向扩展

```bash
# 增加 Celery Worker 数量
docker compose up -d --scale celery-worker=3
```

### 监控建议

- 容器资源：`docker stats`
- 应用日志：配置 `LOG_LEVEL=INFO`，可接入 ELK/Loki
- 数据库性能：`docker compose exec postgres psql -U quant_user -d quant_trade -c "SELECT * FROM pg_stat_activity"`
- 可用性：定期 `curl -k https://localhost/api/v1/health`
