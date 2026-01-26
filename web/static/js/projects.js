class Toast {
    static container = null;
    static queue = [];

    static init() {
        if (!Toast.container) {
            Toast.container = document.createElement('div');
            Toast.container.className = 'toast-container';
            document.body.appendChild(Toast.container);
        }
    }

    static show(type, title, message, duration = 5000) {
        Toast.init();

        const icons = {
            success: '✓',
            error: '✕',
            warning: '⚠',
            info: 'ℹ'
        };

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <div class="toast-content">
                <div class="toast-title">${Toast.escapeHtml(title)}</div>
                ${message ? `<div class="toast-message">${Toast.escapeHtml(message)}</div>` : ''}
            </div>
            <button class="toast-close" onclick="this.parentElement.remove()">×</button>
        `;

        Toast.container.appendChild(toast);

        if (duration > 0) {
            setTimeout(() => {
                toast.classList.add('toast-hiding');
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }

        return toast;
    }

    static success(title, message, duration) {
        return Toast.show('success', title, message, duration);
    }

    static error(title, message, duration = 8000) {
        return Toast.show('error', title, message, duration);
    }

    static warning(title, message, duration) {
        return Toast.show('warning', title, message, duration);
    }

    static info(title, message, duration) {
        return Toast.show('info', title, message, duration);
    }

    static escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

class ProjectsManager {
    constructor() {
        this.projects = new Map();
        this.activeProjectId = null;
        this.issueFilter = 'all';

        this.init();
    }

    async init() {
        await this.loadProjects();
        this.render();
    }

    async loadProjects() {
        try {
            const response = await fetch('/api/projects');
            const data = await response.json();

            this.projects.clear();
            data.projects.forEach(project => {
                this.projects.set(project.id, project);
            });
        } catch (e) {
            console.error('Failed to load projects:', e);
        }
    }

    render() {
        this.renderProjectsList();

        if (this.activeProjectId) {
            this.renderProjectDetail(this.activeProjectId);
        }
    }

    renderProjectsList() {
        const grid = document.getElementById('projects-grid');
        const countEl = document.getElementById('project-count');

        const projects = Array.from(this.projects.values());
        countEl.textContent = projects.length;

        if (projects.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" id="empty-projects">
                    <div class="empty-state-icon">📦</div>
                    <p>No projects yet</p>
                    <p style="margin-top: 8px; font-size: 12px;">Link a GitHub repo to get started</p>
                </div>
            `;
            return;
        }

        grid.innerHTML = projects.map(project => this.createProjectCard(project)).join('');
    }

    createProjectCard(project) {
        const isActive = project.id === this.activeProjectId;
        const statusClass = project.status || 'idle';

        return `
            <div class="project-card ${isActive ? 'active' : ''}" onclick="projectsManager.selectProject(${project.id})">
                <div class="project-card-header">
                    <span class="project-card-name">
                        <span>📦</span>
                        ${this.escapeHtml(project.name)}
                    </span>
                    <span class="project-status ${statusClass}">${statusClass}</span>
                </div>
                <div class="project-card-repo">
                    <a href="https://github.com/${project.github_repo}" target="_blank" onclick="event.stopPropagation()">
                        ${this.escapeHtml(project.github_repo)}
                    </a>
                </div>
                <div class="project-card-stats">
                    <span><span class="stat-icon">🔄</span> ${project.last_sync ? 'Synced' : 'Never synced'}</span>
                    <span><span class="stat-icon">${project.has_token ? '🔑' : '⚠️'}</span> ${project.has_token ? 'Token set' : 'No token'}</span>
                </div>
                <div class="project-card-actions" onclick="event.stopPropagation()">
                    <button class="btn btn-small" onclick="projectsManager.syncProject(${project.id})" title="Sync Issues">🔄 Sync</button>
                    ${project.status === 'running' ?
                        `<button class="btn btn-small btn-danger" onclick="projectsManager.stopAutomation(${project.id})" title="Stop">⏹</button>` :
                        `<button class="btn btn-small btn-primary" onclick="projectsManager.startAutomation(${project.id})" title="Start">▶</button>`
                    }
                    <button class="btn btn-small" onclick="projectsManager.editProject(${project.id})" title="Settings">⚙️</button>
                    <button class="btn btn-small btn-danger" onclick="projectsManager.deleteProject(${project.id})" title="Delete">🗑️</button>
                </div>
            </div>
        `;
    }

    async selectProject(projectId) {
        this.activeProjectId = projectId;
        this.render();
        await this.loadProjectIssues(projectId);
    }

    async renderProjectDetail(projectId) {
        const project = this.projects.get(projectId);
        if (!project) return;

        const detail = document.getElementById('project-detail');
        if (!detail) return;

        detail.innerHTML = `
            <div class="detail-header">
                <div class="detail-header-top">
                    <div class="detail-title">
                        <span>📦</span>
                        ${this.escapeHtml(project.name)}
                        <span class="project-status ${project.status || 'idle'}">${project.status || 'idle'}</span>
                    </div>
                    <div class="detail-actions">
                        <button class="btn btn-small" onclick="projectsManager.syncProject(${project.id})">
                            🔄 Sync Issues
                        </button>
                        ${project.status === 'running' ?
                            `<button class="btn btn-small btn-danger" onclick="projectsManager.stopAutomation(${project.id})">
                                ⏹ Stop
                            </button>` :
                            `<button class="btn btn-small btn-primary" onclick="projectsManager.startAutomation(${project.id})">
                                ▶ Start Automation
                            </button>`
                        }
                        <button class="btn btn-small" onclick="projectsManager.editProject(${project.id})">
                            ⚙️ Settings
                        </button>
                        <button class="btn btn-small btn-danger" onclick="projectsManager.deleteProject(${project.id})">
                            🗑️
                        </button>
                    </div>
                </div>
                <div class="detail-meta">
                    <span>
                        <a href="https://github.com/${project.github_repo}" target="_blank">
                            github.com/${project.github_repo}
                        </a>
                    </span>
                    <span>Branch: ${project.default_branch}</span>
                    <span>Max concurrent: ${project.max_concurrent}</span>
                    ${project.last_sync ? `<span>Last sync: ${this.formatDate(project.last_sync)}</span>` : ''}
                </div>
            </div>

            <!-- Git Repository Status -->
            <div class="git-status-bar" id="git-status-bar">
                <div class="git-status-info">
                    <span class="git-icon">📁</span>
                    <span id="git-status-message">Checking repository...</span>
                </div>
                <div class="git-status-actions" id="git-status-actions">
                    <!-- Buttons loaded dynamically based on status -->
                </div>
            </div>

            <div class="automation-controls" id="automation-controls">
                <div class="automation-status">
                    <span class="automation-dot ${project.status === 'running' ? 'running' : ''}"></span>
                    <span>Automation ${project.status === 'running' ? 'Running' : 'Stopped'}</span>
                </div>
                <div class="automation-stats" id="automation-stats">
                    <!-- Stats loaded dynamically -->
                </div>
            </div>

            <div class="detail-content">
                <!-- Activity Log -->
                <div class="activity-log-section">
                    <div class="section-header">
                        <h3>Activity Log</h3>
                        <button class="btn btn-small" onclick="projectsManager.loadAutomationLogs(${project.id})">🔄 Refresh</button>
                    </div>
                    <div class="automation-logs" id="automation-logs">
                        <div class="log-entry log-empty">Loading logs...</div>
                    </div>
                </div>

                <!-- Issues List -->
                <div class="issues-container">
                    <div class="issues-header">
                        <h3>Issues</h3>
                        <div class="issues-filters">
                            <button class="filter-btn ${this.issueFilter === 'all' ? 'active' : ''}" onclick="projectsManager.filterIssues('all')">All</button>
                            <button class="filter-btn ${this.issueFilter === 'pending' ? 'active' : ''}" onclick="projectsManager.filterIssues('pending')">Pending</button>
                            <button class="filter-btn ${this.issueFilter === 'in_progress' ? 'active' : ''}" onclick="projectsManager.filterIssues('in_progress')">In Progress</button>
                            <button class="filter-btn ${this.issueFilter === 'pr_created' ? 'active' : ''}" onclick="projectsManager.filterIssues('pr_created')">PR Created</button>
                            <button class="filter-btn ${this.issueFilter === 'failed' ? 'active' : ''}" onclick="projectsManager.filterIssues('failed')">Failed</button>
                        </div>
                    </div>
                    <div class="issues-grid" id="issues-grid">
                        <div class="loading">Loading issues...</div>
                    </div>
                </div>
            </div>
        `;

        // Load git status, automation status, and start log polling
        this.loadGitStatus(projectId);
        this.loadAutomationStatus(projectId);
        this.startLogPolling(projectId);
    }

    async loadGitStatus(projectId) {
        const messageEl = document.getElementById('git-status-message');
        const actionsEl = document.getElementById('git-status-actions');
        const barEl = document.getElementById('git-status-bar');

        if (!messageEl || !actionsEl) return;

        try {
            const response = await fetch(`/api/projects/${projectId}/git/status`);
            const data = await response.json();

            // Update status bar color based on status
            barEl.className = 'git-status-bar git-status-' + data.status;

            // Build message with details
            let message = data.message;
            if (data.is_git_repo) {
                if (data.current_branch) {
                    message += ` (on ${data.current_branch})`;
                }
                if (data.ahead_behind) {
                    if (data.ahead_behind.ahead > 0) {
                        message += ` ↑${data.ahead_behind.ahead}`;
                    }
                    if (data.ahead_behind.behind > 0) {
                        message += ` ↓${data.ahead_behind.behind}`;
                    }
                }
                if (data.is_clean === false) {
                    message += ' (uncommitted changes)';
                }
            }
            messageEl.textContent = message;

            // Build action buttons based on status
            let actions = '';
            switch (data.status) {
                case 'not_configured':
                    actions = `<button class="btn btn-small" onclick="projectsManager.editProject(${projectId})">⚙️ Configure</button>`;
                    break;
                case 'missing':
                case 'not_initialized':
                    actions = `
                        <button class="btn btn-small btn-primary" onclick="projectsManager.setupGitRepo(${projectId})">📥 Clone Repository</button>
                    `;
                    break;
                case 'wrong_remote':
                    actions = `
                        <span class="git-warning">⚠️ Remote mismatch</span>
                        <button class="btn btn-small" onclick="projectsManager.loadGitStatus(${projectId})">🔄 Refresh</button>
                    `;
                    break;
                case 'ready':
                    actions = `
                        <button class="btn btn-small" onclick="projectsManager.pullGitRepo(${projectId})">📥 Pull Latest</button>
                        <button class="btn btn-small" onclick="projectsManager.loadGitStatus(${projectId})">🔄 Refresh</button>
                    `;
                    break;
                default:
                    actions = `<button class="btn btn-small" onclick="projectsManager.loadGitStatus(${projectId})">🔄 Refresh</button>`;
            }
            actionsEl.innerHTML = actions;

        } catch (e) {
            console.error('Failed to load git status:', e);
            messageEl.textContent = 'Failed to check repository status';
            actionsEl.innerHTML = `<button class="btn btn-small" onclick="projectsManager.loadGitStatus(${projectId})">🔄 Retry</button>`;
        }
    }

    async setupGitRepo(projectId) {
        const actionsEl = document.getElementById('git-status-actions');
        const messageEl = document.getElementById('git-status-message');

        if (actionsEl) {
            actionsEl.innerHTML = '<span class="git-loading">⏳ Cloning repository...</span>';
        }
        if (messageEl) {
            messageEl.textContent = 'Cloning repository, please wait...';
        }

        try {
            const response = await fetch(`/api/projects/${projectId}/git/setup`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                Toast.success('Repository Ready', `Repository ${data.action} successfully!`);
                await this.loadGitStatus(projectId);
            } else {
                let errorMsg = data.message;
                if (data.hint) {
                    errorMsg += ' - ' + data.hint;
                }

                if (data.action === 'clone' && (data.message.includes('Token') || data.message.includes('permission'))) {
                    Toast.warning('Token Issue', errorMsg + '. Click Settings to update the token.');
                } else {
                    Toast.error('Repository Setup Failed', errorMsg);
                }
                await this.loadGitStatus(projectId);
            }
        } catch (e) {
            console.error('Failed to setup git repo:', e);
            Toast.error('Setup Failed', 'Failed to setup repository');
            await this.loadGitStatus(projectId);
        }
    }

    async pullGitRepo(projectId) {
        const actionsEl = document.getElementById('git-status-actions');

        if (actionsEl) {
            actionsEl.innerHTML = '<span class="git-loading">⏳ Pulling latest...</span>';
        }

        try {
            const response = await fetch(`/api/projects/${projectId}/git/pull`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                Toast.success('Pull Complete', 'Repository updated successfully!');
            } else {
                Toast.error('Pull Failed', data.message);
            }
            await this.loadGitStatus(projectId);
        } catch (e) {
            console.error('Failed to pull git repo:', e);
            Toast.error('Pull Failed', 'Failed to pull latest changes');
            await this.loadGitStatus(projectId);
        }
    }

    async loadProjectIssues(projectId) {
        const grid = document.getElementById('issues-grid');
        if (!grid) return;

        try {
            const response = await fetch(`/api/projects/${projectId}/issues`);
            const data = await response.json();

            let issues = data.issue_sessions || [];

            // Apply filter
            if (this.issueFilter !== 'all') {
                issues = issues.filter(i => i.status === this.issueFilter);
            }

            if (issues.length === 0) {
                grid.innerHTML = `
                    <div class="issues-empty">
                        <p>No issues found</p>
                        <p style="margin-top: 8px; font-size: 12px;">
                            ${this.issueFilter === 'all' ? 'Click "Sync Issues" to fetch from GitHub' : 'No issues match this filter'}
                        </p>
                    </div>
                `;
                return;
            }

            grid.innerHTML = issues.map(issue => this.createIssueCard(issue)).join('');

        } catch (e) {
            console.error('Failed to load issues:', e);
            grid.innerHTML = '<div class="issues-empty">Failed to load issues</div>';
        }
    }

    createIssueCard(issue) {
        const statusLabels = {
            'pending': 'Pending',
            'queued': 'Queued',
            'in_progress': 'In Progress',
            'verifying': 'Verifying',
            'verification_failed': 'Verification Failed',
            'pr_created': 'PR Created',
            'completed': 'Completed',
            'failed': 'Failed',
            'skipped': 'Skipped'
        };

        const labels = (issue.github_issue_labels || []).slice(0, 3).map(l =>
            `<span class="issue-label">${this.escapeHtml(l)}</span>`
        ).join('');

        const prLink = issue.pr_url ?
            `<a href="${issue.pr_url}" target="_blank" class="pr-link">
                🔗 PR #${issue.pr_number}
            </a>` : '';

        const actions = [];
        if (issue.status === 'pending' || issue.status === 'failed') {
            actions.push(`<button class="btn btn-small btn-primary" onclick="event.stopPropagation(); projectsManager.startIssue(${issue.id})">▶ Start</button>`);
        }
        if (issue.status === 'failed') {
            actions.push(`<button class="btn btn-small" onclick="event.stopPropagation(); projectsManager.retryIssue(${issue.id})">🔄 Retry</button>`);
        }
        if (issue.status !== 'skipped' && issue.status !== 'completed' && issue.status !== 'pr_created') {
            actions.push(`<button class="btn btn-small" onclick="event.stopPropagation(); projectsManager.skipIssue(${issue.id})">⏭ Skip</button>`);
        }

        return `
            <div class="issue-card" onclick="projectsManager.showIssueDetail(${issue.id})">
                <div class="issue-card-header">
                    <span class="issue-card-title">
                        <span class="issue-number">#${issue.github_issue_number}</span>
                        ${this.escapeHtml(issue.github_issue_title)}
                    </span>
                    <span class="issue-status ${issue.status}">${statusLabels[issue.status] || issue.status}</span>
                </div>
                ${labels ? `<div class="issue-card-labels">${labels}</div>` : ''}
                <div class="issue-card-footer">
                    <span>
                        ${issue.attempts > 0 ? `Attempt ${issue.attempts}/${issue.max_attempts}` : ''}
                        ${issue.last_error ? ` • ${this.escapeHtml(issue.last_error.slice(0, 50))}` : ''}
                    </span>
                    <div class="issue-card-actions">
                        ${prLink}
                        ${actions.join('')}
                    </div>
                </div>
            </div>
        `;
    }

    filterIssues(filter) {
        this.issueFilter = filter;

        // Update button states
        document.querySelectorAll('.issues-filters .filter-btn').forEach(btn => {
            btn.classList.toggle('active', btn.textContent.toLowerCase().replace(' ', '_') === filter || (filter === 'all' && btn.textContent === 'All'));
        });

        if (this.activeProjectId) {
            this.loadProjectIssues(this.activeProjectId);
        }
    }

    async syncProject(projectId, btn = null) {
        // Prevent double-clicks
        if (this._syncing) return;
        this._syncing = true;

        // Get button if not passed - use window.event for legacy onclick handlers
        if (!btn && typeof window !== 'undefined' && window.event && window.event.target) {
            btn = window.event.target;
        }

        const originalText = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = '⏳';
        }

        try {
            const response = await fetch(`/api/projects/${projectId}/sync`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                Toast.success('Sync Complete', `Synced ${data.synced} issues (${data.created} new)`);
                // Reload project data (don't let errors here show as sync failure)
                try {
                    await this.loadProjects();
                    this.render();
                    if (this.activeProjectId === projectId) {
                        await this.loadProjectIssues(projectId);
                    }
                } catch (reloadError) {
                    console.error('Error reloading after sync:', reloadError);
                }
            } else {
                Toast.error('Sync Failed', data.detail || 'Unknown error');
            }
        } catch (e) {
            console.error('Sync failed:', e);
            Toast.error('Sync Failed', e.message);
        } finally {
            this._syncing = false;
            if (btn) {
                btn.disabled = false;
                btn.textContent = originalText || '🔄 Sync';
            }
        }
    }

    async startAutomation(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}/automation/start`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                const project = this.projects.get(projectId);
                if (project) {
                    project.status = 'running';
                    this.render();
                    if (this.activeProjectId === projectId) {
                        this.renderProjectDetail(projectId);
                    }
                }
            } else {
                Toast.error('Start Failed', data.detail || 'Unknown error');
            }
        } catch (e) {
            console.error('Failed to start automation:', e);
            Toast.error('Start Failed', 'Failed to start automation');
        }
    }

    async stopAutomation(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}/automation/stop`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                const project = this.projects.get(projectId);
                if (project) {
                    project.status = 'idle';
                    this.render();
                    if (this.activeProjectId === projectId) {
                        this.renderProjectDetail(projectId);
                    }
                }
            } else {
                Toast.error('Stop Failed', data.detail || 'Unknown error');
            }
        } catch (e) {
            console.error('Failed to stop automation:', e);
            Toast.error('Stop Failed', 'Failed to stop automation');
        }
    }

    async loadAutomationStatus(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}/automation/status`);
            const data = await response.json();

            const statsEl = document.getElementById('automation-stats');
            if (statsEl && data.automation) {
                const auto = data.automation;
                statsEl.innerHTML = `
                    <span>Processed: ${auto.issues_processed || 0}</span>
                    <span>Completed: ${auto.issues_completed || 0}</span>
                    <span>Failed: ${auto.issues_failed || 0}</span>
                `;
            }

            // Also load logs
            await this.loadAutomationLogs(projectId);
        } catch (e) {
            console.error('Failed to load automation status:', e);
        }
    }

    async loadAutomationLogs(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}/automation/logs?limit=30`);
            const data = await response.json();

            const logsEl = document.getElementById('automation-logs');
            if (logsEl && data.logs) {
                if (data.logs.length === 0) {
                    logsEl.innerHTML = '<div class="log-entry log-empty">No activity yet. Start automation to see logs.</div>';
                } else {
                    logsEl.innerHTML = data.logs.map(log => {
                        const time = new Date(log.timestamp).toLocaleTimeString();
                        const levelClass = log.level === 'error' ? 'log-error' : (log.level === 'warn' ? 'log-warn' : 'log-info');
                        return `<div class="log-entry ${levelClass}"><span class="log-time">${time}</span> ${this.escapeHtml(log.message)}</div>`;
                    }).join('');
                    // Auto-scroll to bottom
                    logsEl.scrollTop = logsEl.scrollHeight;
                }
            }
        } catch (e) {
            console.error('Failed to load automation logs:', e);
        }
    }

    startLogPolling(projectId) {
        // Clear any existing interval
        if (this._logPollInterval) {
            clearInterval(this._logPollInterval);
        }
        // Poll for logs every 5 seconds
        this._logPollInterval = setInterval(() => {
            if (this.activeProjectId === projectId) {
                this.loadAutomationLogs(projectId);
            }
        }, 5000);
    }

    stopLogPolling() {
        if (this._logPollInterval) {
            clearInterval(this._logPollInterval);
            this._logPollInterval = null;
        }
    }

    async startIssue(issueId) {
        try {
            const response = await fetch(`/api/issue-sessions/${issueId}/start`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                Toast.success('Issue Started', 'Session started for this issue');
                await this.loadProjectIssues(this.activeProjectId);
            } else {
                Toast.error('Start Failed', data.detail || 'Unknown error');
            }
        } catch (e) {
            console.error('Failed to start issue:', e);
            Toast.error('Start Failed', 'Failed to start issue session');
        }
    }

    async retryIssue(issueId) {
        try {
            const response = await fetch(`/api/issue-sessions/${issueId}/retry`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                await this.loadProjectIssues(this.activeProjectId);
            }
        } catch (e) {
            console.error('Failed to retry issue:', e);
        }
    }

    async skipIssue(issueId) {
        if (!confirm('Are you sure you want to skip this issue?')) return;

        try {
            const response = await fetch(`/api/issue-sessions/${issueId}/skip`, { method: 'POST' });
            const data = await response.json();

            if (data.success) {
                await this.loadProjectIssues(this.activeProjectId);
            }
        } catch (e) {
            console.error('Failed to skip issue:', e);
        }
    }

    async showIssueDetail(issueId) {
        try {
            const response = await fetch(`/api/issue-sessions/${issueId}`);
            const data = await response.json();
            const issue = data.issue_session;

            const content = document.getElementById('issue-detail-content');
            content.innerHTML = `
                <div class="issue-detail-header">
                    <div class="issue-detail-title">
                        <span class="issue-number">#${issue.github_issue_number}</span>
                        ${this.escapeHtml(issue.github_issue_title)}
                    </div>
                    <div class="issue-detail-meta">
                        <span class="issue-status ${issue.status}">${issue.status}</span>
                        ${issue.github_issue_url ? `<a href="${issue.github_issue_url}" target="_blank">View on GitHub</a>` : ''}
                        ${issue.pr_url ? `<a href="${issue.pr_url}" target="_blank">View PR #${issue.pr_number}</a>` : ''}
                    </div>
                </div>

                <div class="issue-detail-body">${this.escapeHtml(issue.github_issue_body || 'No description')}</div>

                ${issue.last_error ? `
                    <div style="background: rgba(248, 81, 73, 0.1); padding: 12px; border-radius: 6px; margin-bottom: 16px;">
                        <strong style="color: var(--accent-red);">Last Error:</strong>
                        <p style="margin-top: 8px;">${this.escapeHtml(issue.last_error)}</p>
                    </div>
                ` : ''}

                ${issue.verification_results && issue.verification_results.length > 0 ? `
                    <div class="verification-results">
                        <h4>Verification Results</h4>
                        ${issue.verification_results.map(r => `
                            <div class="verification-item">
                                <span class="verification-icon">${r.passed ? '✅' : '❌'}</span>
                                <span>${r.check_type}</span>
                                <span style="margin-left: auto; color: var(--text-secondary);">${r.duration_ms}ms</span>
                            </div>
                            ${r.output ? `<div class="verification-output">${this.escapeHtml(r.output)}</div>` : ''}
                        `).join('')}
                    </div>
                ` : ''}
            `;

            document.getElementById('issue-detail-modal').classList.add('open');

        } catch (e) {
            console.error('Failed to load issue detail:', e);
        }
    }

    editProject(projectId) {
        const project = this.projects.get(projectId);
        if (!project) return;

        // Populate the form
        document.getElementById('edit-project-id').value = project.id;
        document.getElementById('edit-project-name').value = project.name;
        document.getElementById('edit-github-repo').value = project.github_repo;
        document.getElementById('edit-github-token').value = ''; // Don't show existing token
        document.getElementById('edit-working-dir').value = project.working_dir || '';
        document.getElementById('edit-default-branch').value = project.default_branch || 'main';
        document.getElementById('edit-max-concurrent').value = project.max_concurrent || 1;
        document.getElementById('edit-lint-command').value = project.lint_command || '';
        document.getElementById('edit-test-command').value = project.test_command || '';
        document.getElementById('edit-build-command').value = project.build_command || '';
        document.getElementById('edit-auto-sync').checked = project.auto_sync;
        document.getElementById('edit-auto-start').checked = project.auto_start;

        // Issue filters
        const filters = project.issue_filter || {};
        document.getElementById('edit-filter-labels').value = (filters.labels || []).join(', ');
        document.getElementById('edit-filter-exclude').value = (filters.exclude_labels || []).join(', ');

        // LLM Provider settings
        document.getElementById('edit-llm-provider').value = project.llm_provider || 'claude_code';
        document.getElementById('edit-llm-api-url').value = project.llm_api_url || '';
        document.getElementById('edit-llm-model').value = project.llm_model || '';
        document.getElementById('edit-llm-api-key').value = ''; // Don't show existing key
        document.getElementById('edit-llm-context-length').value = project.llm_context_length || 8192;
        document.getElementById('edit-llm-temperature').value = project.llm_temperature || 0.1;

        // Toggle LLM settings visibility
        toggleLlmSettings('edit');

        // Show modal
        document.getElementById('edit-project-modal').classList.add('open');
    }

    async deleteProject(projectId) {
        if (!confirm('Are you sure you want to delete this project? This cannot be undone.')) return;

        try {
            const response = await fetch(`/api/projects/${projectId}`, { method: 'DELETE' });
            const data = await response.json();

            if (data.success) {
                this.projects.delete(projectId);
                if (this.activeProjectId === projectId) {
                    this.activeProjectId = null;
                    document.getElementById('project-detail').innerHTML = `
                        <div class="detail-placeholder">
                            <div class="placeholder-icon">👈</div>
                            <p>Select a project to view its issues</p>
                        </div>
                    `;
                }
                this.render();
            }
        } catch (e) {
            console.error('Failed to delete project:', e);
            Toast.error('Delete Failed', 'Failed to delete project');
        }
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    formatDate(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diff = now - date;

        if (diff < 60000) return 'just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        return date.toLocaleDateString();
    }
}

// Initialize
const projectsManager = new ProjectsManager();

// Modal functions
async function showCreateProjectModal() {
    document.getElementById('create-project-modal').classList.add('open');

    // Auto-fill working directory and repo from server's current directory
    try {
        const response = await fetch('/api/server/info');
        const data = await response.json();

        // Auto-fill working directory
        const workingDirInput = document.getElementById('working-dir');
        if (workingDirInput && data.working_directory && !workingDirInput.value) {
            workingDirInput.value = data.working_directory;

            // Show a hint that it was auto-filled
            const hint = workingDirInput.nextElementSibling;
            if (hint && hint.classList.contains('form-hint')) {
                hint.innerHTML = `Auto-filled from server directory ${data.is_git_repo ? '(git repo detected)' : ''}`;
            }
        }

        // Auto-fill GitHub repo if detected
        const repoInput = document.getElementById('github-repo');
        if (repoInput && data.detected_repo && !repoInput.value) {
            repoInput.value = data.detected_repo;
        }

        // Auto-fill project name from directory name
        const nameInput = document.getElementById('project-name');
        if (nameInput && data.working_directory && !nameInput.value) {
            const dirName = data.working_directory.split('/').pop() || data.working_directory.split('\\').pop();
            if (dirName) {
                // Convert kebab-case or snake_case to Title Case
                nameInput.value = dirName
                    .replace(/[-_]/g, ' ')
                    .replace(/\b\w/g, c => c.toUpperCase());
            }
        }
    } catch (e) {
        console.log('Could not fetch server info for auto-fill:', e);
    }

    document.getElementById('project-name').focus();
}

function closeCreateProjectModal() {
    document.getElementById('create-project-modal').classList.remove('open');
    document.getElementById('create-project-form').reset();
}

function closeIssueDetailModal() {
    document.getElementById('issue-detail-modal').classList.remove('open');
}

async function handleCreateProject(event) {
    event.preventDefault();

    const labels = document.getElementById('filter-labels').value;
    const excludeLabels = document.getElementById('filter-exclude').value;

    const projectData = {
        name: document.getElementById('project-name').value,
        github_repo: document.getElementById('github-repo').value,
        github_token: document.getElementById('github-token').value,
        working_dir: document.getElementById('working-dir').value,
        default_branch: document.getElementById('default-branch').value,
        max_concurrent: parseInt(document.getElementById('max-concurrent').value) || 1,
        lint_command: document.getElementById('lint-command').value,
        test_command: document.getElementById('test-command').value,
        build_command: document.getElementById('build-command').value,
        auto_sync: document.getElementById('auto-sync').checked,
        auto_start: document.getElementById('auto-start').checked,
        issue_filter: {
            labels: labels ? labels.split(',').map(l => l.trim()) : [],
            exclude_labels: excludeLabels ? excludeLabels.split(',').map(l => l.trim()) : []
        },
        // LLM Provider settings
        llm_provider: document.getElementById('llm-provider').value,
        llm_api_url: document.getElementById('llm-api-url').value,
        llm_model: document.getElementById('llm-model').value,
        llm_api_key: document.getElementById('llm-api-key').value,
        llm_context_length: parseInt(document.getElementById('llm-context-length').value) || 8192,
        llm_temperature: parseFloat(document.getElementById('llm-temperature').value) || 0.1
    };

    try {
        const response = await fetch('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(projectData)
        });

        const data = await response.json();

        if (data.success) {
            closeCreateProjectModal();
            projectsManager.projects.set(data.project.id, data.project);
            projectsManager.render();
            projectsManager.selectProject(data.project.id);
            Toast.success('Project Created', `${data.project.name} has been created`);
        } else {
            Toast.error('Create Failed', data.detail || 'Unknown error');
        }
    } catch (e) {
        console.error('Failed to create project:', e);
        Toast.error('Create Failed', 'Failed to create project');
    }
}

// Close modals on escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeCreateProjectModal();
        closeIssueDetailModal();
        closeEditProjectModal();
    }
});

// Close modals on outside click
document.getElementById('create-project-modal').addEventListener('click', (e) => {
    if (e.target.id === 'create-project-modal') {
        closeCreateProjectModal();
    }
});

document.getElementById('issue-detail-modal').addEventListener('click', (e) => {
    if (e.target.id === 'issue-detail-modal') {
        closeIssueDetailModal();
    }
});

document.getElementById('edit-project-modal').addEventListener('click', (e) => {
    if (e.target.id === 'edit-project-modal') {
        closeEditProjectModal();
    }
});

function closeEditProjectModal() {
    document.getElementById('edit-project-modal').classList.remove('open');
    document.getElementById('edit-project-form').reset();
}

async function handleEditProject(event) {
    event.preventDefault();

    const projectId = parseInt(document.getElementById('edit-project-id').value);
    const labels = document.getElementById('edit-filter-labels').value;
    const excludeLabels = document.getElementById('edit-filter-exclude').value;
    const newToken = document.getElementById('edit-github-token').value;
    const newLlmApiKey = document.getElementById('edit-llm-api-key').value;

    const updateData = {
        name: document.getElementById('edit-project-name').value,
        working_dir: document.getElementById('edit-working-dir').value,
        default_branch: document.getElementById('edit-default-branch').value,
        max_concurrent: parseInt(document.getElementById('edit-max-concurrent').value) || 1,
        lint_command: document.getElementById('edit-lint-command').value,
        test_command: document.getElementById('edit-test-command').value,
        build_command: document.getElementById('edit-build-command').value,
        auto_sync: document.getElementById('edit-auto-sync').checked,
        auto_start: document.getElementById('edit-auto-start').checked,
        issue_filter: {
            labels: labels ? labels.split(',').map(l => l.trim()) : [],
            exclude_labels: excludeLabels ? excludeLabels.split(',').map(l => l.trim()) : []
        },
        // LLM Provider settings (always included)
        llm_provider: document.getElementById('edit-llm-provider').value,
        llm_api_url: document.getElementById('edit-llm-api-url').value,
        llm_model: document.getElementById('edit-llm-model').value,
        llm_context_length: parseInt(document.getElementById('edit-llm-context-length').value) || 8192,
        llm_temperature: parseFloat(document.getElementById('edit-llm-temperature').value) || 0.1
    };

    // Only include token if a new one was entered
    if (newToken) {
        updateData.github_token = newToken;
    }

    // Only include LLM API key if a new one was entered
    if (newLlmApiKey) {
        updateData.llm_api_key = newLlmApiKey;
    }

    try {
        const response = await fetch(`/api/projects/${projectId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateData)
        });

        const data = await response.json();

        if (data.success) {
            closeEditProjectModal();
            projectsManager.projects.set(data.project.id, data.project);
            projectsManager.render();
            projectsManager.renderProjectDetail(projectId);
            Toast.success('Project Updated', 'Settings saved successfully');
        } else {
            Toast.error('Update Failed', data.detail || 'Unknown error');
        }
    } catch (e) {
        console.error('Failed to update project:', e);
        Toast.error('Update Failed', 'Failed to update project');
    }
}

// ==================== LLM Settings Functions ====================

const LLM_DEFAULT_URLS = {
    'ollama': 'http://localhost:11434',
    'lm_studio': 'http://localhost:1234/v1',
    'openrouter': 'https://openrouter.ai/api/v1'
};

const LLM_URL_HINTS = {
    'ollama': 'Default for Ollama: http://localhost:11434',
    'lm_studio': 'Default for LM Studio: http://localhost:1234/v1',
    'openrouter': 'OpenRouter API: https://openrouter.ai/api/v1'
};

function toggleLlmSettings(formType) {
    const prefix = formType === 'edit' ? 'edit-' : '';
    const provider = document.getElementById(`${prefix}llm-provider`).value;
    const settingsDiv = document.getElementById(`llm-settings-${formType}`);
    const apiKeyGroup = document.getElementById(`${prefix}llm-api-key-group`);
    const urlHint = document.getElementById(`${prefix}llm-api-url-hint`);
    const apiUrlInput = document.getElementById(`${prefix}llm-api-url`);

    if (provider === 'claude_code') {
        settingsDiv.style.display = 'none';
    } else {
        settingsDiv.style.display = 'block';

        // Show/hide API key based on provider
        if (apiKeyGroup) {
            apiKeyGroup.style.display = provider === 'openrouter' ? 'block' : 'none';
        }

        // Update URL hint
        if (urlHint) {
            urlHint.textContent = LLM_URL_HINTS[provider] || '';
        }

        // Set default URL if empty
        if (apiUrlInput && !apiUrlInput.value) {
            apiUrlInput.value = LLM_DEFAULT_URLS[provider] || '';
        }
    }
}

async function fetchModels(formType) {
    const prefix = formType === 'edit' ? 'edit-' : '';
    const provider = document.getElementById(`${prefix}llm-provider`).value;
    const apiUrl = document.getElementById(`${prefix}llm-api-url`).value;
    const apiKey = document.getElementById(`${prefix}llm-api-key`)?.value || '';
    const modelInput = document.getElementById(`${prefix}llm-model`);
    const resultSpan = document.getElementById(`llm-test-result-${formType}`);

    if (provider === 'claude_code') {
        if (resultSpan) resultSpan.textContent = 'Claude Code does not need model selection';
        return;
    }

    if (resultSpan) resultSpan.textContent = 'Fetching models...';

    try {
        let url;
        if (provider === 'ollama') {
            url = `/api/llm/ollama/models?api_url=${encodeURIComponent(apiUrl || LLM_DEFAULT_URLS.ollama)}`;
        } else if (provider === 'lm_studio') {
            url = `/api/llm/lmstudio/models?api_url=${encodeURIComponent(apiUrl || LLM_DEFAULT_URLS.lm_studio)}`;
        } else if (provider === 'openrouter') {
            if (!apiKey) {
                if (resultSpan) resultSpan.textContent = 'API key required for OpenRouter';
                return;
            }
            url = `/api/llm/openrouter/models?api_key=${encodeURIComponent(apiKey)}`;
        }

        const response = await fetch(url);
        const data = await response.json();

        if (data.success) {
            const models = data.models || [];
            if (models.length === 0) {
                if (resultSpan) resultSpan.textContent = 'No models found';
                return;
            }

            // Create a dropdown for model selection
            const modelNames = models.map(m => m.name || m.id);
            const selectedModel = prompt(
                `Available models (${models.length}):\n\n` +
                modelNames.slice(0, 20).join('\n') +
                (modelNames.length > 20 ? `\n... and ${modelNames.length - 20} more` : '') +
                '\n\nEnter model name:',
                modelNames[0]
            );

            if (selectedModel) {
                modelInput.value = selectedModel;
                if (resultSpan) resultSpan.textContent = `Selected: ${selectedModel}`;
            }
        } else {
            if (resultSpan) resultSpan.textContent = data.error || 'Failed to fetch models';
        }
    } catch (e) {
        console.error('Failed to fetch models:', e);
        if (resultSpan) resultSpan.textContent = 'Error fetching models: ' + e.message;
    }
}

async function testLlmConnection(formType) {
    const prefix = formType === 'edit' ? 'edit-' : '';
    const provider = document.getElementById(`${prefix}llm-provider`).value;
    const apiUrl = document.getElementById(`${prefix}llm-api-url`).value;
    const apiKey = document.getElementById(`${prefix}llm-api-key`)?.value || '';
    const model = document.getElementById(`${prefix}llm-model`).value;
    const resultSpan = document.getElementById(`llm-test-result-${formType}`);

    if (provider === 'claude_code') {
        if (resultSpan) resultSpan.textContent = 'Claude Code uses tmux - no connection test needed';
        return;
    }

    if (resultSpan) {
        resultSpan.textContent = 'Testing connection...';
        resultSpan.style.color = '';
    }

    try {
        const response = await fetch('/api/llm/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: provider,
                api_url: apiUrl || LLM_DEFAULT_URLS[provider],
                api_key: apiKey,
                model_name: model
            })
        });

        const data = await response.json();

        if (resultSpan) {
            if (data.success) {
                resultSpan.textContent = data.message || 'Connection successful!';
                resultSpan.style.color = 'var(--accent-green, #3fb950)';
            } else {
                resultSpan.textContent = data.message || 'Connection failed';
                resultSpan.style.color = 'var(--accent-red, #f85149)';
            }
        }
    } catch (e) {
        console.error('Failed to test connection:', e);
        if (resultSpan) {
            resultSpan.textContent = 'Error: ' + e.message;
            resultSpan.style.color = 'var(--accent-red, #f85149)';
        }
    }
}

// ==================== Webhook Settings Functions ====================

// Toggle webhook settings visibility when checkbox is clicked
document.addEventListener('DOMContentLoaded', () => {
    const webhookCheckbox = document.getElementById('edit-webhook-enabled');
    if (webhookCheckbox) {
        webhookCheckbox.addEventListener('change', toggleWebhookSettings);
    }
});

function toggleWebhookSettings() {
    const enabled = document.getElementById('edit-webhook-enabled').checked;
    const settingsDiv = document.getElementById('webhook-settings-edit');
    if (settingsDiv) {
        settingsDiv.style.display = enabled ? 'block' : 'none';
    }
}

// Load webhook configuration when editing a project
async function loadWebhookConfig(projectId) {
    try {
        const response = await fetch(`/api/projects/${projectId}/webhooks`);
        const data = await response.json();
        const config = data.config || {};

        document.getElementById('edit-webhook-enabled').checked = config.enabled || false;
        document.getElementById('edit-webhook-secret').value = ''; // Don't show existing secret
        document.getElementById('edit-webhook-auto-queue').checked = config.auto_queue_issues !== false;
        document.getElementById('edit-webhook-trigger-labels').value = (config.trigger_labels || []).join(', ');
        document.getElementById('edit-webhook-ignore-labels').value = (config.ignore_labels || []).join(', ');

        toggleWebhookSettings();
    } catch (e) {
        console.error('Failed to load webhook config:', e);
    }
}

// Save webhook configuration when updating a project
async function saveWebhookConfig(projectId) {
    const enabled = document.getElementById('edit-webhook-enabled').checked;
    const secret = document.getElementById('edit-webhook-secret').value;
    const autoQueue = document.getElementById('edit-webhook-auto-queue').checked;
    const triggerLabels = document.getElementById('edit-webhook-trigger-labels').value;
    const ignoreLabels = document.getElementById('edit-webhook-ignore-labels').value;

    const webhookData = {
        enabled: enabled,
        auto_queue_issues: autoQueue,
        trigger_labels: triggerLabels ? triggerLabels.split(',').map(l => l.trim()) : [],
        ignore_labels: ignoreLabels ? ignoreLabels.split(',').map(l => l.trim()) : []
    };

    // Only include secret if a new one was entered
    if (secret) {
        webhookData.github_secret = secret;
    }

    try {
        await fetch(`/api/projects/${projectId}/webhooks`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(webhookData)
        });
    } catch (e) {
        console.error('Failed to save webhook config:', e);
    }
}

// Override editProject to also load webhook config
const originalEditProject = ProjectsManager.prototype.editProject;
ProjectsManager.prototype.editProject = function(projectId) {
    originalEditProject.call(this, projectId);
    loadWebhookConfig(projectId);
};

// Override handleEditProject to also save webhook config
const originalHandleEditProject = handleEditProject;
handleEditProject = async function(event) {
    event.preventDefault();
    const projectId = parseInt(document.getElementById('edit-project-id').value);

    // Save webhook config first
    await saveWebhookConfig(projectId);

    // Then save the project (call original handler's logic directly)
    const labels = document.getElementById('edit-filter-labels').value;
    const excludeLabels = document.getElementById('edit-filter-exclude').value;
    const newToken = document.getElementById('edit-github-token').value;
    const newLlmApiKey = document.getElementById('edit-llm-api-key').value;

    const updateData = {
        name: document.getElementById('edit-project-name').value,
        working_dir: document.getElementById('edit-working-dir').value,
        default_branch: document.getElementById('edit-default-branch').value,
        max_concurrent: parseInt(document.getElementById('edit-max-concurrent').value) || 1,
        lint_command: document.getElementById('edit-lint-command').value,
        test_command: document.getElementById('edit-test-command').value,
        build_command: document.getElementById('edit-build-command').value,
        auto_sync: document.getElementById('edit-auto-sync').checked,
        auto_start: document.getElementById('edit-auto-start').checked,
        issue_filter: {
            labels: labels ? labels.split(',').map(l => l.trim()) : [],
            exclude_labels: excludeLabels ? excludeLabels.split(',').map(l => l.trim()) : []
        },
        llm_provider: document.getElementById('edit-llm-provider').value,
        llm_api_url: document.getElementById('edit-llm-api-url').value,
        llm_model: document.getElementById('edit-llm-model').value,
        llm_context_length: parseInt(document.getElementById('edit-llm-context-length').value) || 8192,
        llm_temperature: parseFloat(document.getElementById('edit-llm-temperature').value) || 0.1
    };

    if (newToken) {
        updateData.github_token = newToken;
    }
    if (newLlmApiKey) {
        updateData.llm_api_key = newLlmApiKey;
    }

    try {
        const response = await fetch(`/api/projects/${projectId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateData)
        });

        const data = await response.json();

        if (data.success) {
            closeEditProjectModal();
            projectsManager.projects.set(data.project.id, data.project);
            projectsManager.render();
            projectsManager.renderProjectDetail(projectId);
            Toast.success('Project Updated', 'Settings saved successfully');
        } else {
            Toast.error('Update Failed', data.detail || 'Unknown error');
        }
    } catch (e) {
        console.error('Failed to update project:', e);
        Toast.error('Update Failed', 'Failed to update project');
    }
};
