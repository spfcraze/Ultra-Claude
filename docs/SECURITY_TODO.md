# UltraClaude Security Hardening TODO

**Audit Date:** 2026-01-25
**Target:** VPS/Production Deployment Security
**Version:** v0.3.0

This document outlines security issues identified during the security audit and the tasks required to fix them for safe VPS deployment.

---

## CRITICAL Priority (Must Fix Before Production)

### 1. [x] Add Authentication System -- IMPLEMENTED
**Location:** `src/auth.py`, `src/server.py`
**Files Created/Modified:**
- Created `src/auth.py` - JWT-based authentication with user management
- Updated `src/server.py` - Added auth endpoints and middleware integration

**What was implemented:**
- [x] JWT-based authentication (python-jose)
- [x] Login/register/refresh token endpoints
- [x] User management (create, change password)
- [x] `get_current_user` dependency for endpoint protection
- [x] Auth enable/disable toggle (ULTRACLAUDE_AUTH_ENABLED env var)
- [x] Secure password hashing with salt (SHA-256)
- [x] Protected settings endpoint (jwt_secret, auth_users hidden)

**Endpoints added:**
- `GET /api/auth/status` - Check auth state
- `POST /api/auth/setup` - Create first admin user
- `POST /api/auth/login` - Get access/refresh tokens
- `POST /api/auth/refresh` - Refresh access token
- `GET /api/auth/me` - Current user info
- `POST /api/auth/change-password` - Update password
- `POST /api/auth/enable` / `POST /api/auth/disable` - Toggle auth

### 2. [x] Add Rate Limiting -- IMPLEMENTED
**Location:** `src/security.py`, `src/server.py`
**Files Created/Modified:**
- Created `src/security.py` - Rate limiter, security headers, path validation
- Updated `src/server.py` - Added RateLimitMiddleware

**What was implemented:**
- [x] In-memory rate limiter with per-IP tracking
- [x] Rate limit middleware applied to ALL endpoints
- [x] Category-based limits: default (100/min), auth (10/min), sensitive (30/min)
- [x] IP blocking after 3 violations (5 minute block)
- [x] X-RateLimit-* response headers
- [x] X-Forwarded-For support for proxy deployments

### 3. [x] Fix Credential Storage - Encrypt Secrets -- IMPLEMENTED
**Location:** `src/crypto.py`
**Files Created/Modified:**
- Created `src/crypto.py` - Fernet symmetric encryption module

**What was implemented:**
- [x] Fernet symmetric encryption for all sensitive fields
- [x] Encryption key management (env var, file, or auto-generated)
- [x] Key file stored with 0600 permissions
- [x] `encrypt_if_needed()` to avoid double-encryption
- [x] `decrypt_or_return()` for backward compatibility with legacy plaintext data
- [x] JWT secret stored encrypted in database
- [x] `ULTRACLAUDE_ENCRYPTION_KEY` env var support

### 4. [x] Remove Plaintext Token Temp Files -- IMPLEMENTED
**Location:** `src/git_credentials.py`, `src/server.py`
**Files Created/Modified:**
- Created `src/git_credentials.py` - Secure credential helper context manager
- Updated `src/server.py` - Replaced all 3 insecure temp file patterns

**What was implemented:**
- [x] Created `secure_credential_helper()` context manager with guaranteed cleanup
- [x] Private temp directory with 0700 permissions
- [x] File created with `os.open()` + `O_EXCL` for atomic creation at 0700
- [x] File content overwritten with zeros before deletion (anti-recovery)
- [x] Guaranteed cleanup via `finally` block in context manager
- [x] Replaced all 3 NamedTemporaryFile(delete=False) patterns in server.py

---

## HIGH Priority (Fix Before Public Access)

### 5. [x] Fix SQL Injection Vulnerabilities -- IMPLEMENTED
**Location:** `src/database.py`
**Files Modified:**
- Updated `src/database.py` - Added field whitelist validation to all update methods

**What was implemented:**
- [x] Field name whitelists for all tables (ALLOWED_PROJECT_FIELDS, etc.)
- [x] `_validate_field()` function to check field names before SQL construction
- [x] Applied to: update_project, update_issue_session, update_workflow_template, update_workflow_execution, update_phase_execution, update_artifact
- [x] All parameterized queries already used for values (no change needed)

### 6. [x] Fix Path Traversal Vulnerability -- IMPLEMENTED
**Location:** `src/security.py`, `src/server.py`
**Files Created/Modified:**
- Created `src/security.py` - `validate_path()`, `is_safe_path()` functions
- Updated `src/server.py` - Applied to browse-dirs endpoint

**What was implemented:**
- [x] Path validation with `os.path.realpath()` resolution
- [x] Whitelist of allowed base directories (home dir, /tmp, /var/tmp)
- [x] Applied to browse-dirs endpoint
- [x] Blocks access to /etc, /root, system directories
- [x] Logging of path traversal attempts

### 7. [x] Secure Default Network Binding -- IMPLEMENTED
**Location:** `src/server.py`

**What was implemented:**
- [x] Default binding changed from `0.0.0.0` to `127.0.0.1`
- [x] Warning logged if binding to 0.0.0.0 without auth enabled
- [x] Documentation in docstring about security implications

---

## MEDIUM Priority (Recommended Improvements)

### 8. [x] Enforce Webhook Signature Verification -- IMPLEMENTED
**Location:** `src/webhooks.py`
**Files Modified:**
- Updated `src/webhooks.py` - Added security logging for signature events

**What was implemented:**
- [x] Log warnings when webhooks received without signature verification configured
- [x] Log failed signature verification attempts with source IP
- [x] Enhanced logging includes X-Forwarded-For and X-Real-IP headers
- Note: Verification remains conditional on secret being configured (by design - not all
  setups use GitHub webhooks), but now prominently warns when no secret is set

### 9. [x] Add Security Headers -- IMPLEMENTED
**Location:** `src/security.py`, `src/server.py`
**Files Created/Modified:**
- Created `src/security.py` - SecurityHeadersMiddleware
- Updated `src/server.py` - Added middleware

**What was implemented:**
- [x] X-Content-Type-Options: nosniff
- [x] X-Frame-Options: DENY
- [x] X-XSS-Protection: 1; mode=block
- [x] Referrer-Policy: strict-origin-when-cross-origin
- [x] Content-Security-Policy (self + unsafe-inline for UI, ws: for WebSocket)
- [x] Permissions-Policy (restrict camera, microphone, etc.)

### 10. [x] Add Input Validation -- IMPLEMENTED
**Location:** `src/server.py`, `src/workflow/api.py`
**Files Modified:**
- Updated `src/server.py` - Added `Field()` constraints to all Pydantic models
- Updated `src/workflow/api.py` - Added `Field()` constraints to all Pydantic models

**What was implemented:**
- [x] Added `max_length` to all string fields across all request models
- [x] Added `min_length` to required fields (names, passwords)
- [x] Added `ge`/`le` constraints to numeric fields (port, max_concurrent, temperature, budget)
- [x] Added `max_length` to list fields (labels, events)
- [x] Models updated: ProjectCreate, ProjectUpdate, LLMTestRequest, LoginRequest,
      RegisterRequest, ChangePasswordRequest, ScheduledTaskCreate, WebhookConfigUpdate,
      NotificationConfigCreate, SettingUpdate, WorkflowCreateRequest, TemplateCreateRequest,
      ProviderKeysRequest

### 11. [x] Add Audit Logging -- IMPLEMENTED
**Location:** `src/audit.py`, `src/server.py`
**Files Created/Modified:**
- Created `src/audit.py` - Structured audit logging module
- Updated `src/server.py` - Integrated audit logging into auth endpoints and added audit API

**What was implemented:**
- [x] AuditLogger singleton with file-based structured JSON logging
- [x] Log all auth attempts (login success/failure, setup, password change)
- [x] Log source IP addresses (with X-Forwarded-For support)
- [x] Automatic log rotation (keeps last 5000 entries when exceeding 10000)
- [x] Audit log file at `~/.ultraclaude/audit.log`
- [x] API endpoints: `GET /api/audit/log`, `GET /api/audit/failed-logins`
- [x] AuditEventType enum covering auth, session, project, workflow, webhook, security events

---

## LOW Priority (Best Practices)

### 12. [x] Add HTTPS Support -- IMPLEMENTED
**Location:** `src/server.py`, `src/cli.py`, `src/security.py`
**Files Modified:**
- Updated `src/server.py` - Added `ssl_certfile`/`ssl_keyfile` params to `run_server()`
- Updated `src/cli.py` - Added `--ssl-certfile`/`--ssl-keyfile` CLI options
- Updated `src/security.py` - Added `HTTPSRedirectMiddleware`

**What was implemented:**
- [x] SSL/TLS configuration via args or `ULTRACLAUDE_SSL_CERTFILE`/`ULTRACLAUDE_SSL_KEYFILE` env vars
- [x] HTTP to HTTPS redirect middleware (`ULTRACLAUDE_HTTPS_REDIRECT=true`)
- [x] Certificate file validation before starting
- [x] Documented HTTPS setup with Let's Encrypt, nginx, and Caddy in `docs/DEPLOYMENT_SECURITY.md`

### 13. [x] Implement CORS Properly -- IMPLEMENTED
**Location:** `src/server.py`, `src/security.py`
**Files Modified:**
- Updated `src/server.py` - Added `CORSMiddleware` with configurable origins
- Updated `src/security.py` - Added `get_cors_origins()` function

**What was implemented:**
- [x] `CORSMiddleware` with configurable origins via `ULTRACLAUDE_CORS_ORIGINS` env var
- [x] Default restricted to localhost origins only
- [x] Support for comma-separated multiple origins
- [x] Wildcard (`*`) support with credential restriction per CORS spec
- [x] Restricted to specific methods (`GET`, `POST`, `PUT`, `DELETE`, `PATCH`) and headers

### 14. [x] Add Security Documentation -- IMPLEMENTED
**Location:** `docs/DEPLOYMENT_SECURITY.md`
**Files Created:**
- Created `docs/DEPLOYMENT_SECURITY.md` - Comprehensive deployment security guide

**What was implemented:**
- [x] Quick start deployment checklist
- [x] Full environment variables reference table
- [x] Authentication setup guide
- [x] HTTPS/SSL setup (direct + reverse proxy with nginx/Caddy examples)
- [x] Let's Encrypt integration instructions
- [x] CORS configuration guide
- [x] Rate limiting documentation
- [x] Credential encryption guide
- [x] Webhook security guide
- [x] Audit logging guide
- [x] Firewall configuration (UFW + iptables)
- [x] Security headers reference
- [x] Dependency security procedures
- [x] Incident response procedures with credential rotation
- [x] Example production configuration with systemd service

### 15. [x] Dependency Security -- IMPLEMENTED
**Location:** `requirements.txt`
**Files Modified:**
- Updated `requirements.txt` - Pinned all dependency versions to exact versions

**What was implemented:**
- [x] All 20 dependencies pinned to exact versions (`==`)
- [x] `pip-audit` installed and run - 1 known vulnerability in `ecdsa` (CVE-2024-23342,
  timing side-channel) mitigated by using `cryptography` backend via `python-jose[cryptography]`
- [x] Dependency audit procedure documented in `docs/DEPLOYMENT_SECURITY.md`
- [ ] Set up automated dependency updates (Dependabot) - requires GitHub Actions setup

---

## Summary of Changes Made

### New Files Created
| File | Purpose |
|------|---------|
| `src/auth.py` | JWT authentication, user management, middleware |
| `src/crypto.py` | Fernet encryption/decryption for credentials |
| `src/security.py` | Rate limiting, security headers, CORS, HTTPS redirect, path validation, input sanitization |
| `src/git_credentials.py` | Secure git credential helper with guaranteed cleanup |
| `src/audit.py` | Structured audit logging for security events |
| `docs/DEPLOYMENT_SECURITY.md` | Comprehensive deployment security guide |

### Files Modified
| File | Changes |
|------|---------|
| `src/server.py` | Security middleware, auth endpoints, audit endpoints, path validation, protected settings, secure default binding, Pydantic Field constraints, secure git credential usage, CORS middleware, HTTPS redirect, SSL support |
| `src/cli.py` | Added `--ssl-certfile` and `--ssl-keyfile` CLI options |
| `src/database.py` | Field whitelist validation for all dynamic update queries |
| `src/webhooks.py` | Security logging for signature verification events |
| `src/workflow/api.py` | Pydantic Field constraints on all request models |
| `requirements.txt` | Added `python-jose[cryptography]`, pinned all dependency versions |

### Environment Variables
| Variable | Purpose | Default |
|----------|---------|---------|
| `ULTRACLAUDE_AUTH_ENABLED` | Enable/disable authentication | `false` |
| `ULTRACLAUDE_JWT_SECRET` | JWT signing secret | Auto-generated |
| `ULTRACLAUDE_ENCRYPTION_KEY` | Credential encryption key | Auto-generated from file |
| `ULTRACLAUDE_SSL_CERTFILE` | Path to SSL certificate (PEM) | None |
| `ULTRACLAUDE_SSL_KEYFILE` | Path to SSL private key (PEM) | None |
| `ULTRACLAUDE_HTTPS_REDIRECT` | Redirect HTTP to HTTPS | `false` |
| `ULTRACLAUDE_CORS_ORIGINS` | Comma-separated allowed CORS origins | localhost only |

### Behavior Changes
- **Default bind address** changed from `0.0.0.0` to `127.0.0.1`
- **Rate limiting** is always active (100/min default, 10/min auth, 30/min sensitive)
- **Security headers** are always sent on all responses
- **CORS** restricted to localhost origins by default (configurable via env var)
- **Path browsing** restricted to user's home directory and /tmp
- **Settings API** no longer exposes jwt_secret or auth_users
- **Authentication** is disabled by default for backward compatibility
- **Git tokens** no longer linger on disk - secure context manager with zero-overwrite cleanup
- **Webhook warnings** logged when no signature verification is configured
- **Input validation** enforced on all Pydantic request models (length limits, range constraints)
- **Audit logging** tracks all auth events to `~/.ultraclaude/audit.log`
- **HTTPS** supported via SSL cert/key or reverse proxy
- **Dependencies** pinned to exact versions for reproducible builds

---

## Testing Checklist

Before deploying to production:

- [x] Import all modules without errors (139 routes)
- [x] All HTML pages load (200)
- [x] All existing API endpoints return correct status codes
- [x] Security headers present on all responses
- [x] Rate limit headers present on all responses
- [x] Rate limiting blocks excessive requests
- [x] Path traversal blocked (tested /etc, /etc/shadow)
- [x] Auth setup creates user and returns JWT tokens
- [x] Auth login returns valid JWT tokens
- [x] Protected settings return 403
- [x] Encryption/decryption round-trip verified
- [x] Legacy plaintext backward compatibility verified
- [x] Input validation rejects oversized strings (422)
- [x] Input validation rejects out-of-range numbers (422)
- [x] Audit log records login success and failure
- [x] Audit log API endpoints return structured data
- [x] Secure credential helper creates files with 700 permissions
- [x] Secure credential helper cleans up files after use
- [x] Webhook logging warns when no secret configured
- [ ] Run OWASP ZAP or similar security scanner
- [ ] Review firewall configuration
- [ ] Test HTTPS configuration
