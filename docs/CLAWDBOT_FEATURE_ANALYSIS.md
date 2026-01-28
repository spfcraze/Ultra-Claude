# Clawdbot Feature Analysis for UltraClaude

## Executive Summary

After reviewing [clawdbot](https://github.com/clawdbot/clawdbot), this document identifies key features that could significantly improve UltraClaude's 24/7 automation capabilities.

**Priority Recommendations:**
1. **Daemon Mode** - Critical for 24/7 operation
2. **Webhook/Cron Triggers** - Essential for automation
3. **Multi-Channel Notifications** - Keep users informed
4. **Browser Automation** - Extend Claude's capabilities
5. **Docker Sandboxing** - Security for untrusted operations

---

## Feature Comparison Matrix

| Feature | Clawdbot | UltraClaude | Priority | Effort |
|---------|----------|-------------|----------|--------|
| Daemon/Service Mode | ✅ systemd/launchd | ❌ Manual start | **HIGH** | Medium |
| Cron Job Scheduler | ✅ Built-in | ❌ None | **HIGH** | Low |
| Webhook Triggers | ✅ Multiple channels | ❌ None | **HIGH** | Medium |
| Multi-Channel Notifications | ✅ 12+ platforms | ❌ None | **MEDIUM** | High |
| Browser Automation | ✅ Playwright CDP | ❌ None | **MEDIUM** | High |
| Docker Sandboxing | ✅ Per-session | ❌ None | **LOW** | High |
| WebSocket Gateway | ✅ Centralized | ✅ For UI only | - | - |
| Session Management | ✅ Multi-user | ✅ Multi-session | - | - |
| GitHub Integration | ❌ None | ✅ Full | - | - |
| PR Automation | ❌ None | ✅ Full | - | - |
| Issue Management | ❌ None | ✅ Full | - | - |

---

## Detailed Feature Analysis

### 1. Daemon Mode (HIGH PRIORITY)

**What Clawdbot Does:**
- `clawdbot onboard --install-daemon` installs as system service
- Uses systemd (Linux) or launchd (macOS) for auto-start
- Survives reboots, automatic restart on crash
- Clean shutdown handling

**What UltraClaude Needs:**
```python
# src/daemon.py - New file
class DaemonManager:
    """Manage UltraClaude as a system service"""

    def install_systemd(self):
        """Install systemd service unit"""

    def install_launchd(self):
        """Install macOS launchd plist"""

    def start(self):
        """Start the daemon"""

    def stop(self):
        """Stop the daemon"""

    def status(self):
        """Check daemon status"""

    def restart(self):
        """Restart the daemon"""
```

**Implementation Tasks:**
1. Create `deploy/ultraclaude.service` (already exists partially)
2. Create `deploy/com.ultraclaude.plist` for macOS
3. Add CLI commands: `python main.py daemon install|start|stop|status`
4. Add watchdog for automatic restart on crash
5. Implement graceful shutdown (save session state)

---

### 2. Cron Job Scheduler (HIGH PRIORITY)

**What Clawdbot Does:**
- Built-in cron system for scheduled tasks
- Integrates with sessions for context
- Supports various schedules (hourly, daily, custom)

**What UltraClaude Needs:**
```python
# src/scheduler.py - New file
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class TaskScheduler:
    """Schedule automated tasks"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    def add_issue_sync(self, project_id: int, cron: str):
        """Schedule automatic issue sync"""

    def add_session_cleanup(self, cron: str):
        """Schedule cleanup of old sessions"""

    def add_health_check(self, cron: str):
        """Schedule periodic health checks"""

    def add_custom_task(self, name: str, cron: str, callback):
        """Add custom scheduled task"""
```

**Suggested Scheduled Tasks:**
| Task | Default Schedule | Purpose |
|------|------------------|---------|
| Issue Sync | Every 15 minutes | Pull new issues from GitHub |
| Session Cleanup | Daily at 3 AM | Remove completed sessions >7 days old |
| Health Check | Every 5 minutes | Verify tmux sessions still running |
| Auto-Retry Failed | Every 30 minutes | Retry failed issue sessions |
| PR Status Check | Every 10 minutes | Update PR merge status |

**Implementation Tasks:**
1. Add `apscheduler` to requirements.txt
2. Create `src/scheduler.py`
3. Add scheduler config in database
4. Add UI for managing schedules
5. Add API endpoints: `/api/scheduler/*`

---

### 3. Webhook Triggers (HIGH PRIORITY)

**What Clawdbot Does:**
- Receives webhooks from multiple platforms
- Triggers actions based on webhook payloads
- Supports custom webhook paths

**What UltraClaude Needs:**
```python
# src/webhooks.py - New file
class WebhookHandler:
    """Handle incoming webhooks"""

    async def github_webhook(self, payload: dict):
        """Process GitHub webhooks"""
        event_type = payload.get("action")

        if event_type == "opened" and "issue" in payload:
            # Auto-queue new issue
            await self.queue_issue(payload["issue"])

        elif event_type == "closed" and "pull_request" in payload:
            # Mark session complete when PR merged
            await self.complete_pr_session(payload["pull_request"])

    async def custom_webhook(self, path: str, payload: dict):
        """Handle custom webhook triggers"""
```

**Webhook Events to Support:**
| Source | Event | Action |
|--------|-------|--------|
| GitHub | issue.opened | Auto-queue issue |
| GitHub | issue.labeled | Filter by label |
| GitHub | pull_request.merged | Mark complete |
| GitHub | push | Trigger rebuild/test |
| Custom | Any | Start session with payload |

**Implementation Tasks:**
1. Create `src/webhooks.py`
2. Add endpoints: `POST /webhooks/github`, `POST /webhooks/custom/{path}`
3. Add webhook secret verification
4. Add webhook configuration in project settings
5. Document webhook setup in README

---

### 4. Multi-Channel Notifications (MEDIUM PRIORITY)

**What Clawdbot Does:**
- Integrates with Discord, Slack, Telegram, etc.
- Sends notifications across platforms
- Supports rich formatting per platform

**What UltraClaude Needs:**
```python
# src/notifications.py - New file
class NotificationManager:
    """Send notifications across channels"""

    async def notify(self, event: str, data: dict):
        """Send notification to all configured channels"""

    async def send_discord(self, webhook_url: str, message: str):
        """Send Discord webhook notification"""

    async def send_slack(self, webhook_url: str, message: str):
        """Send Slack webhook notification"""

    async def send_email(self, to: str, subject: str, body: str):
        """Send email notification"""
```

**Notification Events:**
| Event | Message |
|-------|---------|
| issue.started | "Claude started working on #123: Fix login bug" |
| issue.completed | "Issue #123 completed, PR #456 created" |
| issue.failed | "Issue #123 failed verification (attempt 2/3)" |
| issue.needs_review | "Issue #123 flagged as complex - needs human review" |
| session.error | "Session 'fix-auth' encountered an error" |
| update.available | "UltraClaude v0.3.0 is available" |

**Implementation Tasks:**
1. Create `src/notifications.py`
2. Add notification settings to project config
3. Add Discord/Slack webhook inputs in UI
4. Add email SMTP configuration
5. Add notification preferences (which events to notify)

---

### 5. Browser Automation (MEDIUM PRIORITY)

**What Clawdbot Does:**
- Full Playwright integration via CDP
- Screenshot capture, page interaction
- Network monitoring, console logging
- Persistent browser sessions

**What UltraClaude Could Do:**
```python
# src/browser_tools.py - New file
class BrowserAutomation:
    """Browser automation for verification and testing"""

    async def capture_screenshot(self, url: str) -> Path:
        """Capture screenshot of a URL"""

    async def run_e2e_test(self, test_script: str) -> dict:
        """Run end-to-end test in browser"""

    async def verify_deployment(self, url: str, checks: list) -> bool:
        """Verify a deployment is working"""
```

**Use Cases for UltraClaude:**
1. Visual verification of UI changes
2. E2E test execution as part of verification pipeline
3. Screenshot comparison for regression testing
4. Live preview generation for PRs

**Implementation Tasks:**
1. Add `playwright` to requirements.txt
2. Create `src/browser_tools.py`
3. Add browser verification step to pipeline
4. Add screenshot comparison tool
5. Add UI preview generation

---

### 6. Docker Sandboxing (LOW PRIORITY)

**What Clawdbot Does:**
- Per-session Docker containers
- Tool access restrictions
- Isolated execution environment
- Resource limits

**What UltraClaude Could Do:**
```python
# src/sandbox.py - New file
class SandboxManager:
    """Manage sandboxed execution environments"""

    async def create_sandbox(self, session_id: int) -> str:
        """Create isolated Docker container for session"""

    async def execute_in_sandbox(self, container_id: str, command: str):
        """Execute command in sandboxed environment"""

    async def destroy_sandbox(self, container_id: str):
        """Clean up sandbox container"""
```

**Benefits:**
- Isolate untrusted code execution
- Prevent accidental system damage
- Resource limiting (CPU, memory)
- Clean environment for each session

**Implementation Tasks:**
1. Create `src/sandbox.py`
2. Add Docker SDK dependency
3. Create base container image
4. Add sandbox option to session creation
5. Integrate with verification pipeline

---

## Implementation Roadmap

### Phase 1: Core Automation (Weeks 1-2)
**Goal: Enable true 24/7 operation**

1. **Daemon Mode**
   - [ ] Create systemd service file
   - [ ] Create launchd plist
   - [ ] Add `daemon` CLI commands
   - [ ] Implement graceful shutdown
   - [ ] Add auto-restart on crash

2. **Cron Scheduler**
   - [ ] Add APScheduler dependency
   - [ ] Create scheduler module
   - [ ] Add default scheduled tasks
   - [ ] Add scheduler UI

### Phase 2: Event-Driven Automation (Weeks 3-4)
**Goal: React to external events automatically**

3. **Webhook Support**
   - [ ] Create webhook handler
   - [ ] Add GitHub webhook endpoint
   - [ ] Add custom webhook support
   - [ ] Add webhook configuration UI
   - [ ] Document webhook setup

4. **Notifications**
   - [ ] Create notification manager
   - [ ] Add Discord webhook support
   - [ ] Add Slack webhook support
   - [ ] Add notification settings UI

### Phase 3: Enhanced Capabilities (Weeks 5-6)
**Goal: Extend Claude's abilities**

5. **Browser Automation**
   - [ ] Add Playwright integration
   - [ ] Add screenshot capture
   - [ ] Add visual verification
   - [ ] Add E2E test support

6. **Documentation & Polish**
   - [ ] Update README with new features
   - [ ] Add configuration documentation
   - [ ] Add troubleshooting guide
   - [ ] Create video tutorials

---

## Quick Wins (Can Implement Now)

### 1. Health Check Endpoint
```python
@app.get("/api/health")
async def health_check():
    """Health check for monitoring tools"""
    return {
        "status": "healthy",
        "version": __version__,
        "sessions_active": len([s for s in manager.sessions.values()
                               if s.status == SessionStatus.RUNNING]),
        "uptime": time.time() - start_time
    }
```

### 2. Session Auto-Recovery
```python
async def recover_sessions():
    """Recover sessions that were running when server stopped"""
    for session in manager.sessions.values():
        if session.status == SessionStatus.RUNNING:
            # Check if tmux session still exists
            if not tmux_session_exists(session.tmux_session):
                session.status = SessionStatus.STOPPED
                # Optionally auto-restart
```

### 3. Graceful Shutdown Handler
```python
import signal

def handle_shutdown(signum, frame):
    """Save state before shutdown"""
    logger.info("Shutting down gracefully...")
    manager._save_sessions()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)
```

---

## Architecture Comparison

### Clawdbot Architecture
```
┌─────────────────────────────────────────────────┐
│                 WebSocket Gateway               │
│            (Central Control Plane)              │
├─────────────────────────────────────────────────┤
│  Channels    │    Agents    │    Tools         │
│  ─────────   │    ──────    │    ─────         │
│  Discord     │    Pi Agent  │    Browser       │
│  Slack       │    Claude    │    Web Search    │
│  Telegram    │    Custom    │    File Ops      │
│  WhatsApp    │              │    Sandbox       │
└─────────────────────────────────────────────────┘
```

### Proposed UltraClaude Architecture
```
┌─────────────────────────────────────────────────┐
│              UltraClaude Server                 │
│         (FastAPI + WebSocket + Scheduler)       │
├─────────────────────────────────────────────────┤
│  Triggers     │   Sessions   │   Outputs       │
│  ──────────   │   ────────   │   ───────       │
│  GitHub Hook  │   Claude     │   GitHub PR     │
│  Cron Jobs    │   Code CLI   │   Notifications │
│  Manual       │   (tmux)     │   Slack/Discord │
│  Webhooks     │              │   Email         │
├─────────────────────────────────────────────────┤
│  Verification Pipeline                          │
│  ─────────────────────                          │
│  Lint → Test → Build → (Browser) → PR          │
└─────────────────────────────────────────────────┘
```

---

## Conclusion

UltraClaude already has excellent GitHub integration and multi-session management. By adopting these features from clawdbot, we can transform it into a true 24/7 autonomous development system:

**Must Have (Phase 1):**
- Daemon mode for always-on operation
- Cron scheduler for automated tasks
- Webhook triggers for event-driven automation

**Nice to Have (Phase 2-3):**
- Multi-channel notifications
- Browser automation for visual verification
- Docker sandboxing for security

The key differentiator for UltraClaude should remain **GitHub-centric automation** - no other tool combines Claude Code with GitHub Issues and PR creation as seamlessly.
