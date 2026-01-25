# UltraClaude

<div align="center">

![Version](https://img.shields.io/badge/version-0.2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-green.svg)
![License](https://img.shields.io/badge/license-MIT-purple.svg)

**Multi-session Claude Code manager with GitHub Issues integration**

Automatically works on GitHub issues, verifies fixes, and creates pull requests.

[Features](#-features) • [Screenshots](#-screenshots) • [Installation](#-installation) • [Quick Start](#-quick-start) • [Documentation](#-documentation)

</div>

---

<div align="center">

![UltraClaude Dashboard](docs/screenshots/hero.png)

</div>

---

## 📸 Screenshots

### Sessions Dashboard
Manage multiple Claude Code sessions with real-time terminal output, status tracking, and quick actions.

![Sessions Dashboard](docs/screenshots/sessions-dashboard.png)

### Kanban Board View
Visualize your workflow with drag-and-drop session management across status columns.

![Kanban Board](docs/screenshots/kanban-board.png)

<details>
<summary><b>View more screenshots</b></summary>

### Projects Management
![Projects](docs/screenshots/projects.png)
*Configure GitHub repositories with auto-sync and verification commands*

### Issues Automation
![Issues](docs/screenshots/issues.png)
*Automated issue processing with complexity scoring*

### New Session with Auto-Fill
![New Session Modal](docs/screenshots/new-session-modal.png)
*Working directory and session name auto-populate from your current location*

### One-Click Updates
![Update System](docs/screenshots/update-modal.png)
*Update notifications with data preservation - your configs and databases are never overwritten*

</details>

---

## ✨ Features

### 🤖 GitHub Issue Automation
- **Auto-sync Issues**: Pulls issues from your GitHub repositories
- **Complexity Scoring**: Analyzes issues to flag complex ones for human review
- **Automated Assignment**: Claude automatically starts working on queued issues
- **PR Generation**: Creates pull requests with proper descriptions when work is complete

### 📺 Multi-Session Management
- **Concurrent Sessions**: Run multiple Claude Code sessions simultaneously
- **Parent/Child Dependencies**: Queue sessions to start after others complete
- **Real-time Output**: WebSocket-powered live terminal output
- **Session Recovery**: Automatically resumes interrupted sessions on restart

### ✅ Verification Pipeline
- **Lint Checks**: Runs your linter before accepting changes
- **Test Execution**: Ensures all tests pass
- **Build Verification**: Confirms the project builds successfully
- **Auto-Retry**: Retries failed attempts with error feedback

### 🎯 Smart Dashboard
- **List & Kanban Views**: Switch between list and board layouts
- **Right-Click Context Menu**: Quick actions on any session
- **Status Filtering**: Filter by running, completed, needs attention
- **Auto-Fill Forms**: Working directory auto-populates from server location

### 🔄 One-Click Updates
- **Update Notifications**: See when new versions are available
- **Download-based Updates**: Works without git installation
- **Data Preservation**: Your databases, configs, and sessions are never overwritten
- **Automatic Backups**: Creates backup before updating

---

## 🖥️ Dashboard Views

### Sessions Tab
| Feature | Description |
|---------|-------------|
| **Session Cards** | Shows status, working directory, and output preview |
| **Terminal Panel** | Real-time output with ANSI color support |
| **Quick Actions** | Start, stop, complete, or delete sessions |
| **Context Menu** | Right-click for parent/child management |

### Projects Tab
| Feature | Description |
|---------|-------------|
| **GitHub Integration** | Connect repos with personal access tokens |
| **Auto-Fill** | Detects current directory and git remote |
| **Verification Config** | Set lint, test, and build commands |
| **Issue Sync** | Pull issues with one click |

### Issues Tab
| Feature | Description |
|---------|-------------|
| **Issue List** | All synced issues with status indicators |
| **Complexity Scores** | AI-powered difficulty assessment |
| **Batch Actions** | Start, skip, or retry multiple issues |
| **Progress Tracking** | See verification status and attempts |

---

## 🚀 Installation

### Prerequisites
- Python 3.10+
- tmux (for session management)
- Claude Code CLI installed and authenticated

### Quick Install

```bash
# Clone the repository
git clone https://github.com/spfcraze/Ultra-Claude.git
cd Ultra-Claude

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the server
python main.py start
```

### Using the Install Script

```bash
chmod +x install.sh
./install.sh
```

---

## 🎮 Quick Start

### 1. Start the Server

```bash
python main.py start
# Server runs at http://localhost:8420
```

### 2. Add a Project

1. Open http://localhost:8420/projects
2. Click **"+ New Project"** (auto-fills current directory!)
3. Enter your GitHub token ([create one here](https://github.com/settings/tokens))
4. Click **Create Project**

### 3. Sync Issues

1. Click **"Sync Issues"** on your project card
2. Issues appear in the **Issues** tab
3. Click **"Start"** to have Claude work on an issue

### 4. Monitor Progress

- Watch real-time output in the **Sessions** tab
- Claude types `/complete` when done
- Verification runs automatically
- PR is created if all checks pass!

---

## ⚙️ Configuration

### GitHub Token Permissions

Create a token at https://github.com/settings/tokens with:

| Scope | Required For |
|-------|--------------|
| `repo` | Full access to private repositories |
| `public_repo` | Public repositories only |

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ULTRACLAUDE_HOST` | Server bind address | `127.0.0.1` |
| `ULTRACLAUDE_PORT` | Server port | `8420` |
| `ULTRACLAUDE_USE_SQLITE` | Use SQLite database | `1` |
| `ULTRACLAUDE_LOG_LEVEL` | Log level | `INFO` |

### Verification Commands

Set these per-project to enable the verification pipeline:

```yaml
lint_command: "npm run lint"      # or "ruff check ."
test_command: "npm test"          # or "pytest"
build_command: "npm run build"    # or "python -m build"
```

---

## 🔧 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      UltraClaude                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐    ┌──────────────┐    ┌─────────────────┐    │
│  │ GitHub  │───▶│ Issue Syncer │───▶│ Complexity      │    │
│  │ API     │    │              │    │ Analyzer        │    │
│  └─────────┘    └──────────────┘    └────────┬────────┘    │
│                                               │             │
│  ┌─────────────────────────────────────────────▼──────────┐ │
│  │                   Session Manager                      │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐             │ │
│  │  │ tmux     │  │ tmux     │  │ tmux     │  ...        │ │
│  │  │ Session 1│  │ Session 2│  │ Session 3│             │ │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘             │ │
│  │       │             │             │                    │ │
│  │       └─────────────┴─────────────┘                    │ │
│  │                     │                                  │ │
│  │           ┌─────────▼─────────┐                        │ │
│  │           │ Completion        │                        │ │
│  │           │ Detection         │                        │ │
│  │           │ (/complete)       │                        │ │
│  │           └─────────┬─────────┘                        │ │
│  └─────────────────────┼──────────────────────────────────┘ │
│                        │                                    │
│  ┌─────────────────────▼──────────────────────────────────┐ │
│  │              Verification Pipeline                      │ │
│  │  ┌────────┐    ┌────────┐    ┌────────┐               │ │
│  │  │  Lint  │───▶│  Test  │───▶│ Build  │               │ │
│  │  └────────┘    └────────┘    └────────┘               │ │
│  └─────────────────────┬──────────────────────────────────┘ │
│                        │                                    │
│                        ▼                                    │
│              ┌─────────────────┐                           │
│              │   PR Creator    │───▶ GitHub PR             │
│              └─────────────────┘                           │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  FastAPI Server  │  WebSocket  │  Dashboard UI             │
└─────────────────────────────────────────────────────────────┘
```

---

## 📖 Documentation

### Issue Session Status Flow

```
PENDING ──▶ QUEUED ──▶ IN_PROGRESS ──▶ VERIFYING ──▶ PR_CREATED ──▶ COMPLETED
                           │               │
                           ▼               ▼
                      NEEDS_REVIEW      FAILED
                                          │
                                          ▼
                                    (retry up to 3x)
```

### Completion Detection

Claude signals completion by typing:
- `/complete` - Standard completion
- `/done` - Alternative trigger

This triggers the verification pipeline automatically.

### API Reference

<details>
<summary><b>Sessions API</b></summary>

```http
GET    /api/sessions              # List all sessions
POST   /api/sessions              # Create new session
GET    /api/sessions/{id}         # Get session details
POST   /api/sessions/{id}/input   # Send input to session
POST   /api/sessions/{id}/stop    # Stop session
DELETE /api/sessions/{id}         # Delete session
POST   /api/sessions/{id}/complete # Mark completed
```
</details>

<details>
<summary><b>Projects API</b></summary>

```http
GET    /api/projects              # List projects
POST   /api/projects              # Create project
GET    /api/projects/{id}         # Get project
PUT    /api/projects/{id}         # Update project
DELETE /api/projects/{id}         # Delete project
POST   /api/projects/{id}/sync    # Sync issues from GitHub
```
</details>

<details>
<summary><b>Issue Sessions API</b></summary>

```http
GET  /api/issue-sessions              # List issue sessions
GET  /api/issue-sessions/{id}         # Get issue session
POST /api/issue-sessions/{id}/start   # Start working on issue
POST /api/issue-sessions/{id}/retry   # Retry failed issue
POST /api/issue-sessions/{id}/skip    # Skip issue
```
</details>

<details>
<summary><b>WebSocket</b></summary>

```
WS /ws
```

Real-time events:
- `init` - Initial session list
- `output` - Session output update
- `status` - Status change
- `session_created` - New session created
</details>

---

## 🖥️ Production Deployment

### Using systemd

```bash
# Install service
sudo cp deploy/ultraclaude.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ultraclaude
sudo systemctl start ultraclaude

# View logs
sudo journalctl -u ultraclaude -f
```

### Or use the install script:

```bash
sudo ./deploy/install.sh
```

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test
pytest tests/test_complexity_analyzer.py -v
```

---

## 📁 Project Structure

```
ultraclaude/
├── src/
│   ├── automation.py       # AutomationController, VerificationRunner
│   ├── session_manager.py  # Tmux session management
│   ├── models.py           # Data models
│   ├── github_client.py    # GitHub API client
│   ├── server.py           # FastAPI server
│   └── updater.py          # Update system
├── web/
│   ├── templates/          # HTML templates
│   └── static/
│       ├── css/            # Stylesheets
│       └── js/             # JavaScript
├── tests/                  # Unit tests
├── deploy/                 # Deployment files
├── docs/
│   └── screenshots/        # README images
├── main.py                 # CLI entry point
└── requirements.txt
```

---

## 🆕 What's New in v0.2.0

- **Download-based Updates** - Update without git installed
- **Auto-fill Forms** - Working directory auto-populates
- **Right-click Context Menu** - Quick actions on sessions
- **Data Preservation** - Updates never overwrite your data
- **Delete Sessions** - Remove sessions from dashboard

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

---

<div align="center">

**[⬆ Back to Top](#ultraclaude)**

Made with ❤️ for the Claude Code community

</div>
