# PKM Bridge Server - Docker Deployment Guide

This guide covers deploying the PKM Bridge Server using Docker Compose on a Proxmox server.

## Prerequisites

- Docker Engine (20.10+)
- Docker Compose (v2.0+)
- Traefik reverse proxy (already running with external `traefik-net` network)
- Access to your org-agenda and Logseq directories
- Anthropic API key

## Quick Start

1. **Clone/copy the repository to your Proxmox server**
2. **Create `.env` file** (see Configuration below)
3. **Update domain in docker-compose.yml** (see Traefik Configuration below)
4. **Build and run**: `docker-compose up -d`
5. **Access via your domain** (e.g., https://pkm.yourdomain.com)

## Local Testing (Without Traefik)

For testing on your local machine without Traefik:

```bash
# Copy the override file
cp docker-compose.override.yml.example docker-compose.override.yml

# Create .env file
cp .env.example .env
# Edit .env with your settings (use local paths)

# Build and run
docker-compose up -d

# Access directly at
open http://localhost:8000
```

The override file:
- Exposes port 8000 directly (no Traefik needed)
- Uses default bridge network
- Removes Traefik labels

**Note:** `docker-compose.override.yml` is gitignored and won't be deployed to production.

## Configuration

### 1. Create Environment File

Copy the template and fill in your values:

```bash
cp .env.example .env
chmod 600 .env  # Secure the file
nano .env
```

**Required variables:**

```bash
# API Key (get from https://console.anthropic.com/settings/keys)
ANTHROPIC_API_KEY=sk-ant-xxxxx...

# Paths to your notes (absolute paths on Proxmox host)
ORG_DIR=/path/to/your/org-agenda
LOGSEQ_DIR=/path/to/your/Logseq  # or leave blank if not using

# Authentication
AUTH_ENABLED=true
JWT_SECRET=<generate-this>
PASSWORD_HASH=<generate-this>
```

### 2. Generate Secrets

**JWT Secret:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Password Hash:**
```bash
# Install bcrypt if needed
pip install bcrypt

# Generate hash (replace 'your-secure-password' with your actual password)
python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-secure-password', bcrypt.gensalt(12)).decode())"
```

Store your plaintext password in a password manager - you'll need it to log in!

### 3. Verify Volume Mounts

Ensure the directories exist and are accessible:

```bash
# Check directories exist
ls -la /path/to/your/org-agenda
ls -la /path/to/your/Logseq

# Container runs as UID 1000, ensure permissions allow access
# Option 1: Make user 1000 owner
chown -R 1000:1000 /path/to/your/org-agenda

# Option 2: Make group-writable
chmod -R 775 /path/to/your/org-agenda
```

**Important:** org-agenda needs **write** access (for journal tool), Logseq only needs **read** access.

### 4. Configure Traefik Domain

Edit `docker-compose.yml` and update the Traefik labels with your domain:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.pkm-bridge.rule=Host(`pkm.yourdomain.com`)||Host(`pkm`)"
  - "traefik.http.services.pkm-bridge.loadbalancer.server.port=8000"
```

Replace `pkm.yourdomain.com` with your actual domain. The `||Host(\`pkm\`)` part allows access via hostname for local network.

**Example:**
```yaml
- "traefik.http.routers.pkm-bridge.rule=Host(`pkm.oberbrunner.com`)||Host(`pkm`)"
```

## Deployment

### Build and Start

```bash
# From project directory
docker-compose build
docker-compose up -d
```

The build process:
1. Builds frontend with Astro + Tailwind (using bun)
2. Builds backend Python image with Flask
3. Installs system dependencies (ripgrep, fd, git, emacs)

**Build time:** ~5-10 minutes on first build

### Verify Deployment

```bash
# Check container is running
docker-compose ps

# Should show:
# NAME                    STATUS          PORTS
# pkm-bridge-server       Up X minutes    (no ports - using Traefik)

# Check health endpoint via Traefik
curl https://pkm.yourdomain.com/health
# Should return: {"status": "ok"}

# Or test container directly (uncomment ports in docker-compose.yml first)
# curl http://localhost:8000/health

# View logs
docker-compose logs -f

# Should see:
# - System prompt loaded
# - Tools registered: search_notes, list_files, execute_shell, add_journal_note
# - Running on http://0.0.0.0:8000
```

### Test Login

```bash
# Get a token via Traefik (replace with your password and domain)
curl -X POST https://pkm.yourdomain.com/login \
  -H "Content-Type: application/json" \
  -d '{"password": "your-password"}'

# Should return: {"token": "eyJ..."}
```

## docker-compose.yml Overview

### Traefik Integration

The service uses Traefik for all external access - **no ports are directly exposed**:

```yaml
# No ports exposed - Traefik handles all external access
# Uncomment if you need direct access for debugging:
# ports:
#   - "127.0.0.1:8000:8000"
```

Traefik routes requests based on the Host rule:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.pkm-bridge.rule=Host(`pkm.yourdomain.com`)||Host(`pkm`)"
  - "traefik.http.services.pkm-bridge.loadbalancer.server.port=8000"
```

The service connects to Traefik via the external `traefik-net` network.

**For debugging:** Uncomment the ports section in docker-compose.yml to access the service directly at `localhost:8000`

### Volume Mounts

```yaml
volumes:
  - ${ORG_DIR}:/data/org-agenda:rw   # Read/write
  - ${LOGSEQ_DIR}:/data/logseq:ro    # Read-only
```

Inside container, tools access:
- `/data/org-agenda` - Your org-mode files
- `/data/logseq` - Your Logseq notes

### Resource Limits

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
    reservations:
      cpus: '0.5'
      memory: 512M
```

Adjust based on your usage:
- **Light use** (personal, occasional): 1 CPU, 1GB memory
- **Moderate use** (frequent queries): Current settings (2 CPU, 2GB)
- **Heavy use** (many concurrent users): 4 CPU, 4GB+

Monitor with: `docker stats pkm-bridge-server`

### Health Check

```yaml
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3
```

Docker automatically checks `/health` endpoint every 30 seconds.

### Logging

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

Logs rotate automatically (max 30MB total per container).

## Maintenance

### View Logs

```bash
# Real-time logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100

# Logs since 1 hour ago
docker-compose logs --since=1h
```

### Restart Service

```bash
docker-compose restart
```

### Stop Service

```bash
docker-compose down
```

### Update Application

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose build
docker-compose up -d

# Verify
docker-compose logs -f
curl http://localhost:8000/health
```

### Resource Monitoring

```bash
# View resource usage
docker stats pkm-bridge-server

# Shows:
# - CPU %
# - Memory usage / limit
# - Network I/O
# - Block I/O
```

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker-compose logs
```

**Common issues:**

1. **`.env` file missing or malformed**
   ```bash
   cat .env  # Verify contents
   ```

2. **Volume mount paths don't exist**
   ```bash
   ls -la /path/to/org-agenda  # Check path from .env
   ```

3. **Port 8000 already in use**
   ```bash
   lsof -i :8000  # See what's using the port
   ```

4. **Permission denied on volume mounts**
   ```bash
   # Check permissions
   ls -ld /path/to/org-agenda

   # Fix if needed
   chmod 775 /path/to/org-agenda
   ```

### Authentication Issues

**Test password hash:**
```bash
python3 -c "import bcrypt; print(bcrypt.checkpw(b'your-password', b'\$2b\$12\$your-hash-here'))"
# Should print: True
```

**Verify JWT_SECRET is set:**
```bash
docker-compose exec pkm-bridge printenv | grep JWT_SECRET
```

### Search Returning No Results

**Verify volume mounts inside container:**
```bash
docker-compose exec pkm-bridge ls -la /data/org-agenda
docker-compose exec pkm-bridge ls -la /data/logseq
```

**Test ripgrep directly:**
```bash
docker-compose exec pkm-bridge rg -i "test" /data/org-agenda
```

### High Memory Usage

```bash
# Check usage
docker stats pkm-bridge-server

# If consistently > 1.5GB, restart
docker-compose restart

# If problem persists, increase limit in docker-compose.yml
```

### Can't Access via Traefik

**Check if container is running:**
```bash
docker-compose ps
docker-compose logs -f
```

**Verify Traefik can reach the container:**
```bash
# Check both containers are on traefik-net network
docker network inspect traefik-net

# Should show both traefik and pkm-bridge-server containers
```

**Common issues:**
- Domain not configured in Traefik labels → Update docker-compose.yml
- traefik-net network doesn't exist → Check Traefik is running
- DNS not pointing to your server → Verify DNS records
- Traefik SSL certificate issue → Check Traefik logs

**Test container directly (for debugging):**
```bash
# Uncomment ports in docker-compose.yml, then:
docker-compose restart
curl http://localhost:8000/health
```

## Backup

### What to Backup

**Critical:**
1. `.env` file (contains secrets)
2. `org-agenda` directory (modified by journal tool)
3. `docker-compose.yml` (if customized)

**Not critical:**
- Docker images (can rebuild)
- Frontend build artifacts (rebuilt from source)
- Logseq directory (assuming backed up elsewhere)

### Simple Backup Script

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/mnt/backups/pkm-bridge"
DATE=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR/$DATE"

# Backup .env (encrypted)
gpg --encrypt --recipient your@email.com \
  -o "$BACKUP_DIR/$DATE/env.gpg" .env

# Backup org-agenda
rsync -a /path/to/org-agenda/ "$BACKUP_DIR/$DATE/org-agenda/"

# Keep only last 30 days
find "$BACKUP_DIR" -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "Backup completed: $DATE"
```

**Schedule with cron:**
```bash
# Daily at 2 AM
0 2 * * * /opt/pkm-bridge/backup.sh >> /var/log/pkm-backup.log 2>&1
```

### Recovery

```bash
# Stop container
docker-compose down

# Restore .env
gpg --decrypt /mnt/backups/pkm-bridge/YYYYMMDD/env.gpg > .env

# Restore org-agenda
rsync -a /mnt/backups/pkm-bridge/YYYYMMDD/org-agenda/ /path/to/org-agenda/

# Restart
docker-compose up -d
```

## Security Notes

1. **Never commit `.env` to version control** - it contains secrets
2. **Secure the .env file**: `chmod 600 .env`
3. **Use strong password** - stored as bcrypt hash
4. **JWT secret should be random** - at least 64 characters
5. **Firewall port 8000** if not using localhost binding
6. **Keep Docker and base images updated**
7. **Monitor logs** for suspicious activity

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✅ Yes | - | Your Anthropic API key |
| `MODEL` | No | `claude-haiku-4-5` | Default model for new sessions |
| `ORG_DIR` | ✅ Yes | - | Host path to org-agenda directory |
| `LOGSEQ_DIR` | No | - | Host path to Logseq directory |
| `AUTH_ENABLED` | No | `true` | Enable JWT authentication |
| `JWT_SECRET` | ✅ Yes* | - | Secret for signing JWT tokens (*if auth enabled) |
| `PASSWORD_HASH` | ✅ Yes* | - | Bcrypt hash of login password (*if auth enabled) |
| `TOKEN_EXPIRY_HOURS` | No | `168` | JWT token validity (hours) |
| `PORT` | No | `8000` | Container internal port |
| `HOST` | No | `0.0.0.0` | Container internal host binding |
| `DEBUG` | No | `false` | Enable debug mode (dev only) |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `ALLOWED_COMMANDS` | No | See .env.example | Whitelist for execute_shell tool |

## Quick Reference

```bash
# Start
docker-compose up -d

# Stop
docker-compose down

# Restart
docker-compose restart

# Logs
docker-compose logs -f

# Rebuild
docker-compose build

# Update
git pull && docker-compose build && docker-compose up -d

# Health check
curl http://localhost:8000/health

# Resource usage
docker stats pkm-bridge-server

# Shell access
docker-compose exec pkm-bridge /bin/bash

# Test search tool
docker-compose exec pkm-bridge python -c "from pkm_bridge.tools.search_notes import SearchNotesTool; import logging; tool = SearchNotesTool(logging.getLogger(), '/data/org-agenda', '/data/logseq'); print(tool.execute({'pattern': 'test'}))"
```

## Next Steps

After deploying with docker-compose:

1. ✅ Verify container is running: `docker-compose ps`
2. ✅ Check Traefik routing: `docker network inspect traefik-net`
3. ✅ Test health endpoint: `curl https://pkm.yourdomain.com/health`
4. ✅ Test login via browser at your configured domain
5. ✅ Test all tools (search, list, shell, journal)
6. ✅ Set up backups (see Backup section)
7. ✅ Monitor resource usage and adjust limits if needed: `docker stats pkm-bridge-server`

## Support

- Check logs: `docker-compose logs -f`
- Test health: `curl http://localhost:8000/health`
- View resources: `docker stats pkm-bridge-server`
- Container shell: `docker-compose exec pkm-bridge /bin/bash`
