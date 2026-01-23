# UltraClaude

<p align="center">
  <img src="docs/images/logo.png" alt="UltraClaude Logo" width="120">
</p>

<p align="center">
  <strong>Multi-Session Claude Code Manager with GitHub Integration & Multi-LLM Workflows</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#installation">Installation</a> •
  <a href="#multi-llm-workflows">Multi-LLM Workflows</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#local-llm-support">Local LLM Support</a>
</p>

---

## Overview

UltraClaude is a powerful web-based interface for managing multiple Claude Code sessions with seamless GitHub integration and multi-LLM workflow orchestration. It automates the process of working on GitHub issues by creating isolated sessions, managing branches, and generating pull requests automatically.

### Key Capabilities

- **Multi-Session Management**: Run and monitor multiple Claude Code sessions simultaneously
- **Multi-LLM Workflow Pipeline**: Orchestrate Gemini, Claude, GPT-4, Ollama, and LM Studio in collaborative code review and implementation workflows
- **GitHub Issue Automation**: Automatically fetch issues, create branches, and submit PRs
- **Real-time Monitoring**: WebSocket-based live output streaming
- **Session Queuing**: Queue sessions to run sequentially with parent-child dependencies
- **Persistent State**: SQLite-backed persistence survives server restarts
- **Local LLM Support**: Use Ollama, LM Studio, or OpenRouter as alternatives to Claude Code

## Quick Start

### One-Line Install (Linux/macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/spfcraze/ultraclaude/main/install.sh | bash
```

### Manual Install

```bash
# Clone the repository
git clone https://github.com/yourusername/ultraclaude.git
cd ultraclaude

# Run the install script
./install.sh

# Or manually:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start the server
./start.sh
# Or: source venv/bin/activate && python -m uvicorn src.server:app --host 0.0.0.0 --port 8420
```

Open http://localhost:8420 in your browser.

## Features

### Session Management
- Create, start, stop, and monitor Claude Code sessions
- Real-time terminal output via WebSocket
- Session status tracking (running, completed, failed, queued)
- Parent-child session dependencies for sequential workflows
- Kanban board view for visual session management

### Multi-LLM Workflow Pipeline
- **Phase-based workflows**: Chain multiple AI models for analysis → implementation → review
- **Provider support**: Gemini, OpenAI/GPT-4, Claude, Ollama, LM Studio
- **Parallel execution**: Run independent phases concurrently
- **Budget tracking**: Monitor token usage and costs across providers
- **Artifact management**: Pass outputs between phases automatically
- **Iteration support**: Auto-retry failed phases with configurable limits

### GitHub Integration
- Link projects to GitHub repositories
- Automatic issue synchronization with filtering
- Branch creation per issue (e.g., `fix/issue-123`)
- Automated PR creation with proper formatting
- Token-based authentication for private repositories

### Automation Engine
- Start automation to process issues automatically
- Configurable verification commands (lint, test, build)
- Retry logic for failed sessions
- Activity logging for debugging

### Persistence
All state survives server restarts:
- Sessions reconnect to running tmux processes
- Workflow executions pause and can be resumed
- Projects, issues, and templates stored in SQLite

## Installation

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed setup instructions.

### Prerequisites

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.10+ | Core application |
| tmux | 3.0+ | Claude Code session management |
| Git | 2.0+ | Repository operations |
| Node.js | 18+ | Claude Code CLI (optional) |

### System Requirements

- **OS**: Linux, macOS, or Windows with WSL2
- **RAM**: 4GB minimum (8GB+ recommended for local LLMs)
- **Disk**: 500MB for application + space for repositories

## Multi-LLM Workflows

UltraClaude's workflow system lets you orchestrate multiple AI models in a pipeline for code analysis, implementation, and review.

### Example: Code Review Pipeline

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Gemini    │───▶│   Claude    │───▶│   GPT-4     │
│  (Analyze)  │    │ (Implement) │    │  (Review)   │
└─────────────┘    └─────────────┘    └─────────────┘
```

### Creating a Workflow

1. Navigate to the **Workflows** page
2. Click **New Template**
3. Add phases with different providers:
   - **Analysis Phase**: Use Gemini for fast initial analysis
   - **Implementation Phase**: Use Claude for code generation
   - **Review Phase**: Use GPT-4 for final review

### Triggering Reviews from Sessions

1. Select a session on the **Sessions** page
2. Click the **Review** button
3. Choose a workflow template
4. Optionally specify what to focus on
5. Click **Start Review**

See [docs/WORKFLOWS.md](docs/WORKFLOWS.md) for detailed workflow documentation.

## Configuration

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for detailed configuration options.

### GitHub Token Setup

1. Go to [GitHub Settings > Developer Settings > Personal Access Tokens](https://github.com/settings/tokens)
2. Generate a new token with scopes:
   - `repo` - Full control of private repositories
   - `workflow` - Update GitHub Action workflows (optional)
3. Add the token to your project settings in UltraClaude

### Project Configuration

1. Click "New Project" in UltraClaude
2. Enter:
   - **Project Name**: Display name for your project
   - **GitHub Repository**: `owner/repo` format
   - **GitHub Token**: Your personal access token
   - **Working Directory**: Local path for git clone
   - **Default Branch**: Usually `main` or `master`

## Local LLM Support

UltraClaude supports alternative LLM providers for users who want to use local models or different cloud APIs.

### Supported Providers

| Provider | Type | Setup |
|----------|------|-------|
| Claude Code | Cloud (Anthropic) | Default, requires Claude CLI |
| Gemini | Cloud (Google) | API key required |
| OpenAI/GPT-4 | Cloud (OpenAI) | API key required |
| Ollama | Local | Free, runs on your machine |
| LM Studio | Local | Free, GUI for local models |
| OpenRouter | Cloud (Multi) | API key, access to many models |

### Ollama Setup

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3.2:latest

# Start Ollama (runs automatically on install)
ollama serve
```

In UltraClaude project settings:
- Select "Ollama" as the LLM provider
- API URL: `http://localhost:11434`
- Model: `llama3.2:latest`

### LM Studio Setup

1. Install [LM Studio](https://lmstudio.ai)
2. Download a model from the Discover tab
3. Start the local server (Server tab)
4. In UltraClaude: Select "LM Studio", API URL: `http://localhost:1234/v1`

## API Reference

### REST Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sessions` | List all sessions |
| POST | `/api/sessions` | Create new session |
| GET | `/api/projects` | List all projects |
| POST | `/api/projects/{id}/sync` | Sync issues from GitHub |
| GET | `/api/workflow/templates` | List workflow templates |
| POST | `/api/workflow/executions` | Create workflow execution |
| POST | `/api/workflow/executions/{id}/run` | Run workflow |

### WebSocket

Connect to `/ws` for real-time updates:

```javascript
const ws = new WebSocket('ws://localhost:8420/ws');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // Handle: init, output, status, session_created, workflow_status
};
```

## Architecture

```
ultraclaude/
├── src/
│   ├── server.py           # FastAPI application
│   ├── session_manager.py  # Session lifecycle management
│   ├── database.py         # SQLite persistence layer
│   ├── models.py           # Data models
│   ├── automation.py       # GitHub automation engine
│   ├── github_client.py    # GitHub API client
│   ├── llm_provider.py     # LLM provider abstraction
│   └── workflow/           # Multi-LLM workflow system
│       ├── engine.py       # Workflow orchestrator
│       ├── phase_runner.py # Phase execution
│       ├── providers/      # LLM provider implementations
│       └── api.py          # Workflow REST API
├── web/
│   ├── templates/          # Jinja2 HTML templates
│   └── static/             # CSS and JavaScript
├── tests/                  # Test suite
├── install.sh              # Installation script
├── start.sh                # Server start script
└── requirements.txt        # Python dependencies
```

## Data Storage

UltraClaude stores data in `~/.ultraclaude/`:

```
~/.ultraclaude/
├── ultraclaude.db      # SQLite database (main storage)
├── sessions.json       # Claude Code session state
└── .encryption_key     # API key encryption key
```

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues.

### Quick Fixes

**"Cannot connect to Ollama"**
```bash
# Ensure Ollama is running
ollama serve
```

**"tmux session not found"**
```bash
# Install tmux
sudo apt install tmux  # Linux
brew install tmux      # macOS
```

**"Port 8420 in use"**
```bash
# Use different port
python -m uvicorn src.server:app --port 8421
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude and Claude Code
- [FastAPI](https://fastapi.tiangolo.com) for the web framework
- [Google](https://ai.google.dev) for Gemini API
- [OpenAI](https://openai.com) for GPT-4 API
- [Ollama](https://ollama.ai) for local LLM support
