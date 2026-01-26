const browserApp = {
    sessions: [],
    selectedSession: null,
    activePanel: 'console',
    ws: null,
    pollInterval: null,

    consoleLogs: [],
    networkLogs: [],
    screenshots: [],
    actions: [],

    async init() {
        await this.refreshSessions();
        this.startPolling();
    },

    // ==================== Sessions ====================

    async refreshSessions() {
        try {
            const response = await fetch('/api/browser/sessions');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            this.sessions = data.sessions || [];
            this.renderSessionList();
            this.updateStats();
        } catch (error) {
            console.error('Failed to load browser sessions:', error);
        }
    },

    renderSessionList() {
        const container = document.getElementById('session-list');

        if (this.sessions.length === 0) {
            container.innerHTML = `
                <div class="no-sessions">
                    <div class="no-sessions-icon">&#127760;</div>
                    <p>No browser sessions</p>
                </div>
            `;
            return;
        }

        container.innerHTML = this.sessions.map(s => {
            const isActive = this.selectedSession?.id === s.id;
            const url = s.current_url || '';
            const shortUrl = url.length > 35 ? url.substring(0, 35) + '...' : url;
            return `
                <div class="session-card ${isActive ? 'active' : ''}"
                     onclick="browserApp.selectSession('${this.esc(s.id)}')">
                    <div class="session-card-header">
                        <span class="session-card-name">${this.esc(s.name || s.id.substring(0, 8))}</span>
                        <span class="session-card-status session-status-${s.status}">${s.status}</span>
                    </div>
                    ${url ? `<div class="session-card-url" title="${this.esc(url)}">${this.esc(shortUrl)}</div>` : ''}
                    <div class="session-card-meta">
                        <span>${s.browser_type || 'chromium'}</span>
                        <span>${s.config?.viewport_width || 1280}x${s.config?.viewport_height || 720}</span>
                    </div>
                </div>
            `;
        }).join('');
    },

    async selectSession(sessionId) {
        try {
            const response = await fetch(`/api/browser/sessions/${sessionId}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            this.selectedSession = data.session;

            this.renderSessionList();
            this.enableControls(true);

            if (this.selectedSession.current_url) {
                document.getElementById('url-input').value = this.selectedSession.current_url;
            } else {
                document.getElementById('url-input').value = '';
            }

            await this.refreshPanels();
            this.connectWebSocket(sessionId);
        } catch (error) {
            console.error('Failed to select session:', error);
            this.showToast('Failed to load session', 'error');
        }
    },

    enableControls(enabled) {
        document.getElementById('url-input').disabled = !enabled;
        document.getElementById('btn-go').disabled = !enabled;
        document.getElementById('btn-screenshot').disabled = !enabled;
        document.getElementById('btn-interact').disabled = !enabled;
    },

    openNewSessionModal() {
        document.getElementById('new-session-modal').classList.add('open');
    },

    closeNewSessionModal() {
        document.getElementById('new-session-modal').classList.remove('open');
    },

    async createSession() {
        const name = document.getElementById('session-name').value.trim();
        const browserType = document.getElementById('browser-type').value;
        const width = parseInt(document.getElementById('viewport-width').value) || 1280;
        const height = parseInt(document.getElementById('viewport-height').value) || 720;
        const headless = document.getElementById('session-headless').checked;

        try {
            const response = await fetch('/api/browser/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name || null,
                    browser_type: browserType,
                    viewport_width: width,
                    viewport_height: height,
                    headless: headless
                })
            });

            const data = await response.json();

            if (data.success) {
                this.closeNewSessionModal();
                await this.refreshSessions();
                await this.selectSession(data.session.id);
                this.showToast('Browser session created', 'success');
            } else {
                this.showToast('Failed to create session: ' + (data.detail || 'Unknown error'), 'error');
            }
        } catch (error) {
            console.error('Failed to create session:', error);
            this.showToast('Failed to create session', 'error');
        }
    },

    async closeSession(sessionId) {
        const id = sessionId || this.selectedSession?.id;
        if (!id) return;

        try {
            await fetch(`/api/browser/sessions/${id}`, { method: 'DELETE' });
            if (this.selectedSession?.id === id) {
                this.selectedSession = null;
                this.enableControls(false);
                this.clearPanels();
                this.showPlaceholder();
            }
            await this.refreshSessions();
            this.showToast('Session closed', 'success');
        } catch (error) {
            console.error('Failed to close session:', error);
        }
    },

    // ==================== Navigation ====================

    async navigate() {
        if (!this.selectedSession) return;

        let url = document.getElementById('url-input').value.trim();
        if (!url) return;

        if (!url.match(/^https?:\/\//)) {
            url = 'https://' + url;
            document.getElementById('url-input').value = url;
        }

        this.showViewportLoading();

        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/navigate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });

            const data = await response.json();

            if (data.success) {
                await this.takeScreenshot();
                await this.refreshPanels();
            } else {
                this.showToast('Navigation failed: ' + (data.action?.error || 'Unknown error'), 'error');
                this.showPlaceholder();
            }
        } catch (error) {
            console.error('Navigation failed:', error);
            this.showToast('Navigation failed', 'error');
            this.showPlaceholder();
        }
    },

    // ==================== Screenshot ====================

    async takeScreenshot() {
        if (!this.selectedSession) return;

        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/screenshot`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });

            const data = await response.json();

            if (data.success && data.screenshot) {
                const imgPath = data.screenshot.file_path || data.screenshot.url;
                if (imgPath) {
                    this.showScreenshot(imgPath);
                }
                await this.refreshScreenshots();
            }
        } catch (error) {
            console.error('Screenshot failed:', error);
        }
    },

    showScreenshot(path) {
        const viewport = document.getElementById('viewport-area');
        const staticPath = path.includes('/static/') ? path : '/static/screenshots/' + path.split('/').pop();
        viewport.innerHTML = `<img class="viewport-screenshot" src="${this.esc(staticPath)}?t=${Date.now()}" alt="Browser screenshot">`;
    },

    showPlaceholder() {
        document.getElementById('viewport-area').innerHTML = `
            <div class="viewport-placeholder" id="viewport-placeholder">
                <div class="viewport-placeholder-icon">&#127760;</div>
                <p>${this.selectedSession ? 'Navigate to a URL to see content' : 'No browser session selected'}</p>
                <p class="hint">${this.selectedSession ? 'Enter a URL above and press Enter' : 'Create a new session to get started'}</p>
            </div>
        `;
    },

    showViewportLoading() {
        document.getElementById('viewport-area').innerHTML = `
            <div class="viewport-loading">
                <div class="spinner"></div>
                <span>Loading page...</span>
            </div>
        `;
    },

    // ==================== Interact Modal ====================

    openInteractModal() {
        if (!this.selectedSession) return;
        document.getElementById('interact-modal').classList.add('open');
    },

    closeInteractModal() {
        document.getElementById('interact-modal').classList.remove('open');
    },

    switchInteractTab(tab) {
        document.querySelectorAll('.interact-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.interact-panel').forEach(p => p.classList.remove('active'));
        document.querySelector(`.interact-tab[onclick*="${tab}"]`).classList.add('active');
        document.getElementById(`interact-${tab}`).classList.add('active');
    },

    async doClick() {
        if (!this.selectedSession) return;
        const selector = document.getElementById('click-selector').value.trim();
        if (!selector) return;

        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/click`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selector })
            });
            const data = await response.json();
            if (data.success) {
                this.showToast('Clicked element', 'success');
                await this.takeScreenshot();
            } else {
                this.showToast('Click failed: ' + (data.action?.error || 'Unknown'), 'error');
            }
        } catch (error) {
            this.showToast('Click failed', 'error');
        }
    },

    async doType() {
        if (!this.selectedSession) return;
        const selector = document.getElementById('type-selector').value.trim();
        const text = document.getElementById('type-text').value;
        if (!selector || !text) return;

        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/type`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selector, text })
            });
            const data = await response.json();
            if (data.success) {
                this.showToast('Text typed', 'success');
                await this.takeScreenshot();
            } else {
                this.showToast('Type failed: ' + (data.action?.error || 'Unknown'), 'error');
            }
        } catch (error) {
            this.showToast('Type failed', 'error');
        }
    },

    async doEvaluate() {
        if (!this.selectedSession) return;
        const expression = document.getElementById('eval-expression').value.trim();
        if (!expression) return;

        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/evaluate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ expression })
            });
            const data = await response.json();
            const resultDiv = document.getElementById('eval-result');
            const resultContent = document.getElementById('eval-result-content');

            resultDiv.style.display = 'block';
            if (data.success) {
                resultContent.textContent = JSON.stringify(data.action?.result, null, 2) || 'undefined';
                resultContent.style.color = 'var(--accent-green)';
            } else {
                resultContent.textContent = data.action?.error || 'Evaluation failed';
                resultContent.style.color = 'var(--accent-red)';
            }
        } catch (error) {
            this.showToast('Evaluate failed', 'error');
        }
    },

    async doWait() {
        if (!this.selectedSession) return;
        const selector = document.getElementById('wait-selector').value.trim();
        const state = document.getElementById('wait-state').value;
        if (!selector) return;

        this.showToast('Waiting for element...', 'info');
        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/wait`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selector, state })
            });
            const data = await response.json();
            if (data.success) {
                this.showToast('Element found', 'success');
                await this.takeScreenshot();
            } else {
                this.showToast('Wait failed: ' + (data.action?.error || 'Timeout'), 'error');
            }
        } catch (error) {
            this.showToast('Wait failed', 'error');
        }
    },

    async doExtract(type) {
        if (!this.selectedSession) return;
        const selector = document.getElementById('extract-selector').value.trim();

        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/extract/${type}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selector: selector || null })
            });
            const data = await response.json();
            const resultDiv = document.getElementById('extract-result');
            const resultContent = document.getElementById('extract-result-content');

            resultDiv.style.display = 'block';
            if (data.success) {
                resultContent.textContent = data.action?.result || '';
                resultContent.style.color = 'var(--text-primary)';
            } else {
                resultContent.textContent = data.action?.error || 'Extraction failed';
                resultContent.style.color = 'var(--accent-red)';
            }
        } catch (error) {
            this.showToast('Extract failed', 'error');
        }
    },

    // ==================== Panel Management ====================

    switchPanel(panel) {
        this.activePanel = panel;
        document.querySelectorAll('.panel-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.panel === panel);
        });
        document.querySelectorAll('.panel-content').forEach(p => p.classList.remove('active'));
        document.getElementById(`panel-${panel}`).classList.add('active');
    },

    async refreshPanels() {
        if (!this.selectedSession) return;
        await Promise.all([
            this.refreshConsole(),
            this.refreshNetwork(),
            this.refreshScreenshots(),
            this.refreshActions()
        ]);
    },

    clearPanels() {
        this.consoleLogs = [];
        this.networkLogs = [];
        this.screenshots = [];
        this.actions = [];
        ['console', 'network', 'screenshots', 'actions'].forEach(panel => {
            document.getElementById(`${panel}-count`).textContent = '0';
        });
        document.getElementById('panel-console').innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#128196;</div><p>No console logs yet</p></div>';
        document.getElementById('panel-network').innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#127760;</div><p>No network activity yet</p></div>';
        document.getElementById('panel-screenshots').innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#128247;</div><p>No screenshots taken yet</p></div>';
        document.getElementById('panel-actions').innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#9889;</div><p>No actions performed yet</p></div>';
    },

    async refreshConsole() {
        if (!this.selectedSession) return;
        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/console?limit=200`);
            const data = await response.json();
            this.consoleLogs = data.logs || [];
            this.renderConsole();
        } catch (error) {
            console.error('Failed to load console logs:', error);
        }
    },

    renderConsole() {
        const container = document.getElementById('panel-console');
        const count = document.getElementById('console-count');
        count.textContent = this.consoleLogs.length;

        if (this.consoleLogs.length === 0) {
            container.innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#128196;</div><p>No console logs yet</p></div>';
            return;
        }

        container.innerHTML = this.consoleLogs.map(log => {
            const level = log.level || 'log';
            const time = this.formatTime(log.timestamp);
            return `
                <div class="console-entry level-${level}">
                    <span class="console-time">${time}</span>
                    <span class="console-level console-level-${level}">${level}</span>
                    <span class="console-message">${this.esc(log.message || '')}</span>
                </div>
            `;
        }).join('');
    },

    async refreshNetwork() {
        if (!this.selectedSession) return;
        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/network?limit=200`);
            const data = await response.json();
            this.networkLogs = data.logs || [];
            this.renderNetwork();
        } catch (error) {
            console.error('Failed to load network logs:', error);
        }
    },

    renderNetwork() {
        const container = document.getElementById('panel-network');
        const count = document.getElementById('network-count');
        count.textContent = this.networkLogs.length;

        if (this.networkLogs.length === 0) {
            container.innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#127760;</div><p>No network activity yet</p></div>';
            return;
        }

        container.innerHTML = this.networkLogs.map(log => {
            const method = log.method || 'GET';
            const status = log.status || '';
            const statusClass = this.getNetworkStatusClass(status);
            const url = log.url || '';
            const shortUrl = url.length > 80 ? url.substring(0, 80) + '...' : url;
            const resType = log.resource_type || '';
            const time = this.formatTime(log.timestamp);

            return `
                <div class="network-entry">
                    <span class="network-method">${method}</span>
                    <span class="network-status ${statusClass}">${status}</span>
                    <span class="network-url" title="${this.esc(url)}">${this.esc(shortUrl)}</span>
                    <span class="network-type">${resType}</span>
                    <span class="network-time">${time}</span>
                </div>
            `;
        }).join('');
    },

    getNetworkStatusClass(status) {
        if (!status) return '';
        const s = parseInt(status);
        if (s >= 200 && s < 300) return 'network-status-2xx';
        if (s >= 300 && s < 400) return 'network-status-3xx';
        if (s >= 400 && s < 500) return 'network-status-4xx';
        if (s >= 500) return 'network-status-5xx';
        return 'network-status-fail';
    },

    async refreshScreenshots() {
        if (!this.selectedSession) return;
        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/screenshots`);
            const data = await response.json();
            this.screenshots = data.screenshots || [];
            this.renderScreenshots();
        } catch (error) {
            console.error('Failed to load screenshots:', error);
        }
    },

    renderScreenshots() {
        const container = document.getElementById('panel-screenshots');
        const count = document.getElementById('screenshots-count');
        count.textContent = this.screenshots.length;

        if (this.screenshots.length === 0) {
            container.innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#128247;</div><p>No screenshots taken yet</p></div>';
            return;
        }

        container.innerHTML = `<div class="screenshot-gallery">
            ${this.screenshots.map(ss => {
                const path = ss.file_path || ss.url || '';
                const staticPath = path.includes('/static/') ? path : '/static/screenshots/' + path.split('/').pop();
                const time = this.formatTime(ss.timestamp);
                const page = ss.page_url || '';
                const shortPage = page.length > 30 ? page.substring(0, 30) + '...' : page;
                return `
                    <div class="screenshot-thumb" onclick="browserApp.previewScreenshot('${this.esc(staticPath)}', '${this.esc(page)}')">
                        <img src="${this.esc(staticPath)}?t=${Date.now()}" alt="Screenshot">
                        <div class="screenshot-thumb-meta">
                            ${time} - ${this.esc(shortPage)}
                        </div>
                    </div>
                `;
            }).join('')}
        </div>`;
    },

    previewScreenshot(path, url) {
        document.getElementById('screenshot-modal-title').textContent = url || 'Screenshot';
        document.getElementById('screenshot-modal-img').src = path + '?t=' + Date.now();
        document.getElementById('screenshot-modal').classList.add('open');
    },

    closeScreenshotModal() {
        document.getElementById('screenshot-modal').classList.remove('open');
    },

    async refreshActions() {
        if (!this.selectedSession) return;
        try {
            const response = await fetch(`/api/browser/sessions/${this.selectedSession.id}/history?limit=100`);
            const data = await response.json();
            this.actions = data.actions || [];
            this.renderActions();
        } catch (error) {
            console.error('Failed to load actions:', error);
        }
    },

    renderActions() {
        const container = document.getElementById('panel-actions');
        const count = document.getElementById('actions-count');
        count.textContent = this.actions.length;

        if (this.actions.length === 0) {
            container.innerHTML = '<div class="panel-empty"><div class="panel-empty-icon">&#9889;</div><p>No actions performed yet</p></div>';
            return;
        }

        container.innerHTML = this.actions.map(action => {
            const type = action.action_type || action.type || 'unknown';
            const detail = action.selector || action.url || action.expression || '';
            const shortDetail = detail.length > 60 ? detail.substring(0, 60) + '...' : detail;
            const hasError = action.error;
            const duration = action.duration_ms ? `${action.duration_ms}ms` : '';

            return `
                <div class="action-entry">
                    <span class="action-type">${type}</span>
                    <span class="action-detail" title="${this.esc(detail)}">${this.esc(shortDetail)}</span>
                    <span class="action-status ${hasError ? 'action-error' : 'action-success'}">${hasError ? 'FAIL' : 'OK'}</span>
                    <span class="action-duration">${duration}</span>
                </div>
            `;
        }).join('');
    },

    // ==================== WebSocket ====================

    connectWebSocket(sessionId) {
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        try {
            this.ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleWsMessage(data);
            };

            this.ws.onerror = () => {};
            this.ws.onclose = () => {
                this.ws = null;
            };
        } catch (e) {
            console.error('WebSocket connection failed:', e);
        }
    },

    handleWsMessage(data) {
        if (!this.selectedSession) return;

        if (data.type === 'browser_status' && data.session_id === this.selectedSession.id) {
            this.selectedSession.status = data.status;
            this.renderSessionList();
        }

        if (data.type === 'browser_action' && data.session_id === this.selectedSession.id) {
            this.refreshPanels();
        }
    },

    // ==================== Polling ====================

    startPolling() {
        this.pollInterval = setInterval(() => {
            if (this.selectedSession) {
                this.refreshPanels();
            }
            this.refreshSessions();
        }, 5000);
    },

    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    },

    // ==================== Stats ====================

    updateStats() {
        const total = this.sessions.length;
        const active = this.sessions.filter(s => s.status !== 'closed' && s.status !== 'error').length;
        const statsEl = document.getElementById('browser-stats');
        if (total === 0) {
            statsEl.textContent = 'No sessions';
        } else {
            statsEl.textContent = `${total} session${total !== 1 ? 's' : ''} (${active} active)`;
        }
    },

    // ==================== Toast ====================

    showToast(message, type = 'info') {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => toast.classList.add('show'), 10);
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    },

    // ==================== Utilities ====================

    formatTime(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    },

    esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
};

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
    }
});

document.addEventListener('DOMContentLoaded', () => {
    browserApp.init();
});
