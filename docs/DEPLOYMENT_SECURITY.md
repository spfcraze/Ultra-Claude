# UltraClaude Deployment Security Guide

**Version:** v0.3.0
**Last Updated:** 2026-01-25

---

## Quick Start Checklist

Before deploying UltraClaude on a VPS or any network-accessible server, complete these steps:

- [ ] Enable authentication (`ULTRACLAUDE_AUTH_ENABLED=true`)
- [ ] Set up the admin account via `/api/auth/setup`
- [ ] Configure a strong JWT secret (`ULTRACLAUDE_JWT_SECRET`)
- [ ] Set an encryption key (`ULTRACLAUDE_ENCRYPTION_KEY`)
- [ ] Configure a firewall (allow only ports 22, 80/443, 8420)
- [ ] Set up HTTPS (SSL certificate + key)
- [ ] Restrict CORS origins (`ULTRACLAUDE_CORS_ORIGINS`)
- [ ] Configure webhook secrets for all GitHub integrations
- [ ] Review audit logs periodically (`/api/audit/log`)
- [ ] Pin dependencies and run `pip-audit` before deploying

---

## Environment Variables Reference

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `ULTRACLAUDE_AUTH_ENABLED` | Enable JWT authentication | `false` | **Yes** for production |
| `ULTRACLAUDE_JWT_SECRET` | JWT signing secret | Auto-generated | Recommended |
| `ULTRACLAUDE_ENCRYPTION_KEY` | Fernet key for credential encryption | Auto-generated from file | Recommended |
| `ULTRACLAUDE_SSL_CERTFILE` | Path to SSL certificate (PEM) | None | For HTTPS |
| `ULTRACLAUDE_SSL_KEYFILE` | Path to SSL private key (PEM) | None | For HTTPS |
| `ULTRACLAUDE_HTTPS_REDIRECT` | Redirect HTTP to HTTPS | `false` | For HTTPS |
| `ULTRACLAUDE_CORS_ORIGINS` | Comma-separated allowed origins | localhost only | For external access |

---

## 1. Authentication

Authentication is **disabled by default** for backward compatibility. You **must** enable it before exposing the server to any network.

### Enable Authentication

```bash
export ULTRACLAUDE_AUTH_ENABLED=true
```

### Create Admin Account

```bash
curl -X POST http://localhost:8420/api/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-strong-password"}'
```

Requirements:
- Username: 3-64 characters
- Password: 1-256 characters (use a strong password)

### Login

```bash
curl -X POST http://localhost:8420/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password"}'
```

Returns `access_token` (30 min) and `refresh_token` (7 days).

### Use Token

```bash
curl http://localhost:8420/api/projects \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### JWT Secret

By default, a random JWT secret is generated and stored encrypted in the database. For multi-instance deployments, set a shared secret:

```bash
export ULTRACLAUDE_JWT_SECRET="your-256-bit-secret-here"
```

---

## 2. HTTPS / SSL

### Option A: Direct SSL (Simple)

Provide certificate and key files directly to UltraClaude:

```bash
export ULTRACLAUDE_SSL_CERTFILE=/etc/letsencrypt/live/yourdomain.com/fullchain.pem
export ULTRACLAUDE_SSL_KEYFILE=/etc/letsencrypt/live/yourdomain.com/privkey.pem
```

Or via CLI:
```bash
ultraclaude serve --ssl-certfile /path/to/cert.pem --ssl-keyfile /path/to/key.pem
```

### Option B: Reverse Proxy (Recommended for Production)

Use nginx or Caddy as a reverse proxy to handle SSL termination:

**nginx example:**
```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8420;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}
```

**Caddy example** (auto-SSL with Let's Encrypt):
```
yourdomain.com {
    reverse_proxy 127.0.0.1:8420
}
```

### Let's Encrypt Setup

```bash
apt install certbot
certbot certonly --standalone -d yourdomain.com
# Certs will be at /etc/letsencrypt/live/yourdomain.com/
```

### HTTP to HTTPS Redirect

When using direct SSL (not a reverse proxy), enable automatic redirect:

```bash
export ULTRACLAUDE_HTTPS_REDIRECT=true
```

---

## 3. CORS Configuration

By default, CORS is restricted to localhost origins only. For production with a separate frontend or external access:

```bash
# Single origin
export ULTRACLAUDE_CORS_ORIGINS=https://yourdomain.com

# Multiple origins
export ULTRACLAUDE_CORS_ORIGINS=https://app.example.com,https://admin.example.com

# Allow all (NOT recommended for production)
export ULTRACLAUDE_CORS_ORIGINS=*
```

Allowed methods: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`
Allowed headers: `Authorization`, `Content-Type`

When using wildcard (`*`), credentials (cookies, auth headers) are not supported per the CORS specification.

---

## 4. Network Binding

The server defaults to `127.0.0.1` (localhost only). To accept external connections:

```bash
ultraclaude serve --host 0.0.0.0
```

**Warning**: If you bind to `0.0.0.0` without authentication enabled, a warning will be logged. Always enable auth before exposing the server.

---

## 5. Rate Limiting

Rate limiting is always active and cannot be disabled. Limits per IP:

| Category | Limit | Endpoints |
|----------|-------|-----------|
| Default | 100/min | All endpoints |
| Auth | 10/min | `/api/auth/login`, `/api/auth/register`, `/api/auth/refresh` |
| Sensitive | 30/min | `/api/sessions`, `/api/projects`, `/api/workflow/execute`, `/api/daemon/install` |
| WebSocket | 10/min | WebSocket connections |

After 3 rate limit violations, the IP is blocked for 5 minutes.

Rate limit headers are included in every response:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining in window
- `X-RateLimit-Reset`: Window duration in seconds

---

## 6. Credential Encryption

All sensitive fields (API keys, tokens, secrets) are encrypted at rest using Fernet symmetric encryption.

### Encryption Key Management

The encryption key is resolved in this order:
1. `ULTRACLAUDE_ENCRYPTION_KEY` environment variable
2. Key file at `~/.ultraclaude/.encryption_key` (auto-created with 0600 permissions)
3. Auto-generated and saved to key file

**For production**, set the key explicitly and back it up:
```bash
# Generate a key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set it
export ULTRACLAUDE_ENCRYPTION_KEY="your-generated-key"
```

**Important**: If you lose the encryption key, all encrypted credentials will be unreadable and must be re-entered.

---

## 7. Webhook Security

When using GitHub webhooks, always configure a webhook secret:

1. Set a secret in your GitHub webhook configuration
2. Configure the same secret in UltraClaude's project webhook settings

Without a secret, webhooks are accepted without signature verification and a warning is logged.

Failed signature verification attempts are logged with the source IP.

---

## 8. Audit Logging

All security-relevant events are logged to `~/.ultraclaude/audit.log` as JSON:

### Events Tracked
- Authentication: login success/failure, setup, password changes
- Sessions: create, stop, delete
- Projects: create, update, delete
- Workflows: create, cancel, delete
- Webhooks: received, signature failures
- Security: rate limits, path traversal attempts, protected setting access

### Viewing Audit Logs

```bash
# Recent events
curl http://localhost:8420/api/audit/log?limit=50 \
  -H "Authorization: Bearer YOUR_TOKEN"

# Failed login attempts
curl http://localhost:8420/api/audit/failed-logins \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Log rotation occurs automatically when exceeding 10,000 entries (keeps the most recent 5,000).

---

## 9. Firewall Configuration

### UFW (Ubuntu)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp        # SSH
ufw allow 80/tcp        # HTTP (for redirect)
ufw allow 443/tcp       # HTTPS
ufw allow 8420/tcp      # UltraClaude (if not behind proxy)
ufw enable
```

### iptables

```bash
iptables -A INPUT -p tcp --dport 22 -j ACCEPT
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -A INPUT -p tcp --dport 8420 -j ACCEPT
iptables -A INPUT -j DROP
```

---

## 10. Security Headers

The following headers are automatically added to all responses:

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `X-XSS-Protection` | `1; mode=block` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline'; ...` |
| `Permissions-Policy` | Restricts camera, microphone, geolocation, etc. |

---

## 11. Dependency Security

### Pin Dependencies

Use exact versions in `requirements.txt` for reproducible builds.

### Audit Dependencies

```bash
# Install pip-audit
pip install pip-audit

# Run audit
pip-audit -r requirements.txt
```

### Automated Updates

Consider using Dependabot or Renovate for automated dependency update PRs.

---

## 12. Incident Response

### Signs of Compromise
- Unusual entries in audit log (`~/.ultraclaude/audit.log`)
- Multiple failed login attempts from unknown IPs
- Path traversal attempts in logs
- Unexpected rate limit blocks

### Response Steps
1. **Contain**: Stop the server (`kill $(pgrep -f ultraclaude)`)
2. **Investigate**: Review audit logs and system logs
3. **Rotate**: Change all credentials (JWT secret, encryption key, API tokens)
4. **Patch**: Update dependencies and apply fixes
5. **Restore**: Restart with fresh credentials

### Rotate Credentials

```bash
# Generate new JWT secret
export ULTRACLAUDE_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# Generate new encryption key
export ULTRACLAUDE_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Note: After rotating the encryption key, all previously encrypted
# credentials must be re-entered through the UI or API.
```

---

## Example Production Configuration

```bash
# /etc/ultraclaude/env (or systemd EnvironmentFile)
ULTRACLAUDE_AUTH_ENABLED=true
ULTRACLAUDE_JWT_SECRET=your-secret-here
ULTRACLAUDE_ENCRYPTION_KEY=your-fernet-key-here
ULTRACLAUDE_CORS_ORIGINS=https://yourdomain.com
ULTRACLAUDE_SSL_CERTFILE=/etc/letsencrypt/live/yourdomain.com/fullchain.pem
ULTRACLAUDE_SSL_KEYFILE=/etc/letsencrypt/live/yourdomain.com/privkey.pem
ULTRACLAUDE_HTTPS_REDIRECT=true
```

### Systemd Service

```ini
[Unit]
Description=UltraClaude Server
After=network.target

[Service]
Type=simple
User=ultraclaude
WorkingDirectory=/opt/ultraclaude
EnvironmentFile=/etc/ultraclaude/env
ExecStart=/opt/ultraclaude/venv/bin/python -m src.cli serve --host 0.0.0.0 --port 8420
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
