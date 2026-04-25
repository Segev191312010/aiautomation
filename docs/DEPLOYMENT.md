# Deployment Guide

## Overview

This guide covers deploying the trading platform to production environments. The platform consists of a FastAPI backend and a React frontend that can be deployed together or separately.

## Prerequisites

### System Requirements
- **OS**: Linux (Ubuntu 20.04+ recommended), macOS, or Windows Server 2019+
- **CPU**: 4+ cores recommended
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 50GB SSD minimum
- **Network**: Stable internet connection for IBKR API

### Software Requirements
- Python 3.11+
- Node.js 18+
- SQLite 3.35+ (or PostgreSQL for production)
- Nginx or Apache (for reverse proxy)
- SSL certificate (Let's Encrypt recommended)

## Deployment Options

### Option 1: Docker Deployment (Recommended)

#### Dockerfile

```dockerfile
# Backend stage
FROM python:3.11-slim as backend

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Frontend build stage
FROM node:18-alpine as frontend-build

WORKDIR /app

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libsqlite3-0 \
    nginx \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Copy backend
COPY --from=backend /app /app/backend
COPY --from=backend /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=backend /usr/local/bin /usr/local/bin

# Copy frontend build
COPY --from=frontend-build /app/dist /var/www/html

# Copy nginx config
COPY deployment/nginx.conf /etc/nginx/nginx.conf

# Copy supervisor config
COPY deployment/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create data directory
RUN mkdir -p /data && chmod 777 /data

# Expose ports
EXPOSE 80 443 8000

# Start services
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
```

#### docker-compose.yml

```yaml
version: '3.8'

services:
  trading-platform:
    build: .
    container_name: trading-platform
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "8000:8000"
    volumes:
      - ./data:/data
      - ./logs:/var/log
      - ./ssl:/etc/nginx/ssl:ro
    environment:
      - DB_PATH=/data/trading.db
      - JWT_SECRET=${JWT_SECRET}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - FRONTEND_ORIGIN=${FRONTEND_ORIGIN}
      - IBKR_HOST=${IBKR_HOST:-host.docker.internal}
      - IBKR_PORT=${IBKR_PORT:-7497}
      - SIM_MODE=${SIM_MODE:-false}
      - IS_PAPER=${IS_PAPER:-true}
    networks:
      - trading-network

  # Optional: PostgreSQL for production
  postgres:
    image: postgres:15-alpine
    container_name: trading-db
    restart: unless-stopped
    environment:
      - POSTGRES_USER=trading
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=trading
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - trading-network
    profiles:
      - postgres

volumes:
  postgres-data:

networks:
  trading-network:
    driver: bridge
```

#### Nginx Configuration

```nginx
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;

    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/rss+xml application/atom+xml image/svg+xml;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=ws:10m rate=100r/s;

    # Upstream for backend
    upstream backend {
        server 127.0.0.1:8000;
        keepalive 32;
    }

    # HTTP to HTTPS redirect
    server {
        listen 80;
        server_name _;
        return 301 https://$host$request_uri;
    }

    # HTTPS server
    server {
        listen 443 ssl http2;
        server_name your-domain.com;

        # SSL configuration
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;

        # Security headers
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;

        # Frontend static files
        location / {
            root /var/www/html;
            index index.html;
            try_files $uri $uri/ /index.html;
            expires 1h;
            add_header Cache-Control "public, immutable";
        }

        # API and WebSocket
        location /api/ {
            limit_req zone=api burst=20 nodelay;
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 86400;
        }

        # WebSocket endpoint
        location /ws {
            limit_req zone=ws burst=100 nodelay;
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 86400;
        }

        # Health check
        location /health {
            proxy_pass http://backend/health;
            access_log off;
        }
    }
}
```

#### Supervisor Configuration

```ini
[supervisord]
nodaemon=true
user=root
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid

[program:backend]
command=uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
 directory=/app/backend
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/backend.log
stderr_logfile=/var/log/backend.error.log
environment=PYTHONUNBUFFERED=1

[program:nginx]
command=/usr/sbin/nginx -g 'daemon off;'
autostart=true
autorestart=true
stdout_logfile=/var/log/nginx.log
stderr_logfile=/var/log/nginx.error.log
```

### Option 2: Manual Deployment

#### Backend Setup

```bash
# Create user
sudo useradd -r -s /bin/false trading

# Create directories
sudo mkdir -p /opt/trading/backend
sudo mkdir -p /opt/trading/data
sudo mkdir -p /opt/trading/logs
sudo mkdir -p /opt/trading/venv

# Set permissions
sudo chown -R trading:trading /opt/trading

# Install Python dependencies
sudo -u trading python3.11 -m venv /opt/trading/venv
sudo -u trading /opt/trading/venv/bin/pip install -r requirements.txt

# Copy backend code
sudo cp -r backend/* /opt/trading/backend/
sudo chown -R trading:trading /opt/trading/backend

# Create systemd service
sudo tee /etc/systemd/system/trading-backend.service << EOF
[Unit]
Description=Trading Platform Backend
After=network.target

[Service]
Type=simple
User=trading
Group=trading
WorkingDirectory=/opt/trading/backend
Environment=PATH=/opt/trading/venv/bin
Environment=DB_PATH=/opt/trading/data/trading.db
Environment=JWT_SECRET=your-secret-key
Environment=SIM_MODE=false
Environment=IS_PAPER=true
ExecStart=/opt/trading/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Start service
sudo systemctl daemon-reload
sudo systemctl enable trading-backend
sudo systemctl start trading-backend
```

#### Frontend Setup

```bash
# Build frontend
cd frontend
npm ci
npm run build

# Copy to web server
sudo cp -r dist/* /var/www/html/
sudo chown -R www-data:www-data /var/www/html
```

## Environment Configuration

### Production Environment Variables

```bash
# Required
export DB_PATH=/data/trading.db
export JWT_SECRET=your-256-bit-secret-key-here
export FRONTEND_ORIGIN=https://your-domain.com

# Trading Configuration
export SIM_MODE=false
export IS_PAPER=true
export AUTOPILOT_MODE=OFF
export BOT_ENABLED=false
export BOT_INTERVAL_SECONDS=60

# IBKR Connection
export IBKR_HOST=127.0.0.1
export IBKR_PORT=7497  # 7496 for live, 7497 for paper
export IBKR_CLIENT_ID=1

# Risk Limits
export MAX_POSITIONS=10
export MAX_POSITION_PCT=0.20
export MAX_SECTOR_PCT=0.30
export MAX_TOTAL_DRAWDOWN=0.10

# AI Features (optional)
export ANTHROPIC_API_KEY=your-api-key
export AI_AUTONOMY_ENABLED=false
export AI_SHADOW_MODE=true

# Logging
export LOG_LEVEL=INFO
export LOG_FILE=/var/log/trading/app.log

# Security
export CORS_ORIGINS=https://your-domain.com
export RATE_LIMIT_ENABLED=true
```

## SSL/TLS Setup

### Let's Encrypt with Certbot

```bash
# Install Certbot
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d your-domain.com -d www.your-domain.com

# Auto-renewal
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer
```

### Manual SSL Setup

```bash
# Generate private key
openssl genrsa -out /etc/nginx/ssl/key.pem 4096

# Generate CSR
openssl req -new -key /etc/nginx/ssl/key.pem -out /etc/nginx/ssl/csr.pem \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=your-domain.com"

# Generate self-signed certificate (for testing)
openssl x509 -req -days 365 -in /etc/nginx/ssl/csr.pem \
    -signkey /etc/nginx/ssl/key.pem -out /etc/nginx/ssl/cert.pem
```

## Database Migration

### SQLite to PostgreSQL

```python
# migration_script.py
import sqlite3
import psycopg2
from psycopg2.extras import execute_values

def migrate_sqlite_to_postgres(sqlite_path, postgres_url):
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_cur = sqlite_conn.cursor()
    
    # Connect to PostgreSQL
    pg_conn = psycopg2.connect(postgres_url)
    pg_cur = pg_conn.cursor()
    
    # Get table names
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in sqlite_cur.fetchall()]
    
    for table in tables:
        print(f"Migrating table: {table}")
        
        # Get schema
        sqlite_cur.execute(f"PRAGMA table_info({table})")
        columns = sqlite_cur.fetchall()
        
        # Create table in PostgreSQL
        col_defs = []
        for col in columns:
            name = col[1]
            sqlite_type = col[2].upper()
            
            # Map SQLite types to PostgreSQL
            if 'INTEGER' in sqlite_type:
                pg_type = 'INTEGER'
            elif 'REAL' in sqlite_type or 'FLOAT' in sqlite_type:
                pg_type = 'REAL'
            elif 'TEXT' in sqlite_type or 'CHAR' in sqlite_type:
                pg_type = 'TEXT'
            elif 'BLOB' in sqlite_type:
                pg_type = 'BYTEA'
            else:
                pg_type = 'TEXT'
            
            col_defs.append(f"{name} {pg_type}")
        
        create_sql = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_defs)})"
        pg_cur.execute(create_sql)
        
        # Copy data
        sqlite_cur.execute(f"SELECT * FROM {table}")
        rows = sqlite_cur.fetchall()
        
        if rows:
            col_names = [col[1] for col in columns]
            placeholders = ','.join(['%s'] * len(col_names))
            insert_sql = f"INSERT INTO {table} ({','.join(col_names)}) VALUES ({placeholders})"
            pg_cur.executemany(insert_sql, rows)
        
        pg_conn.commit()
    
    sqlite_conn.close()
    pg_conn.close()
    print("Migration complete!")

# Run migration
migrate_sqlite_to_postgres(
    '/opt/trading/data/trading.db',
    'postgresql://user:pass@localhost/trading'
)
```

## Monitoring and Logging

### Prometheus Metrics

```python
# metrics.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Define metrics
TRADE_COUNTER = Counter('trades_total', 'Total trades', ['symbol', 'action'])
ORDER_LATENCY = Histogram('order_latency_seconds', 'Order execution latency')
POSITION_GAUGE = Gauge('open_positions', 'Number of open positions')
EQUITY_GAUGE = Gauge('account_equity', 'Account equity in USD')

# Start metrics server
start_http_server(9090)
```

### Log Rotation

```bash
# /etc/logrotate.d/trading
/opt/trading/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 trading trading
    sharedscripts
    postrotate
        systemctl reload trading-backend
    endscript
}
```

### Health Checks

```bash
# Add to crontab
*/5 * * * * curl -f http://localhost:8000/health || systemctl restart trading-backend
```

## Backup Strategy

### Database Backup

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/opt/trading/backups"
DB_PATH="/opt/trading/data/trading.db"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory
mkdir -p $BACKUP_DIR

# Backup SQLite database
sqlite3 $DB_PATH ".backup '$BACKUP_DIR/trading_$DATE.db'"

# Compress backup
gzip $BACKUP_DIR/trading_$DATE.db

# Keep only last 30 days
find $BACKUP_DIR -name "trading_*.db.gz" -mtime +30 -delete

# Sync to S3 (optional)
aws s3 sync $BACKUP_DIR s3://your-bucket/trading-backups/
```

### Configuration Backup

```bash
#!/bin/bash
# config_backup.sh

tar -czf /opt/trading/backups/config_$(date +%Y%m%d).tar.gz \
    /opt/trading/backend/.env \
    /etc/nginx/nginx.conf \
    /etc/systemd/system/trading-backend.service
```

## Security Hardening

### Firewall Configuration

```bash
# UFW configuration
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw allow 8000/tcp  # Backend (if exposed)
sudo ufw enable
```

### Fail2Ban Configuration

```ini
# /etc/fail2ban/jail.local
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[nginx-auth]
enabled = true
filter = nginx-auth
action = iptables-multiport[name=NoAuthFailures, port="http,https"]
logpath = /var/log/nginx/error.log

[nginx-login]
enabled = true
filter = nginx-login
action = iptables-multiport[name=NoLoginFailures, port="http,https"]
logpath = /var/log/nginx/access.log
```

## Troubleshooting

### Common Issues

**Backend won't start:**
```bash
# Check logs
sudo journalctl -u trading-backend -f

# Verify environment variables
sudo cat /opt/trading/backend/.env

# Check port availability
sudo netstat -tlnp | grep 8000
```

**Database locked:**
```bash
# Check for locks
lsof /opt/trading/data/trading.db

# Enable WAL mode
sqlite3 /opt/trading/data/trading.db "PRAGMA journal_mode=WAL;"
```

**WebSocket connection fails:**
```bash
# Check nginx error logs
sudo tail -f /var/log/nginx/error.log

# Verify WebSocket headers
sudo nginx -T | grep -A 10 "location /ws"
```

### Performance Tuning

```bash
# Increase file descriptor limits
sudo tee -a /etc/security/limits.conf << EOF
trading soft nofile 65536
trading hard nofile 65536
EOF

# Kernel tuning for high connections
sudo tee -a /etc/sysctl.conf << EOF
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
EOF

sudo sysctl -p
```

## Rollback Procedure

```bash
# Quick rollback script
#!/bin/bash

# Stop services
sudo systemctl stop trading-backend
sudo systemctl stop nginx

# Restore from backup
BACKUP_FILE="/opt/trading/backups/trading_$(date +%Y%m%d).db.gz"
gunzip -c $BACKUP_FILE > /opt/trading/data/trading.db

# Restore previous version
cd /opt/trading/backend
git checkout previous-version

# Restart services
sudo systemctl start trading-backend
sudo systemctl start nginx

echo "Rollback complete!"
```
