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

class Autowrkers {
    constructor() {
        this.sessions = new Map();
        this.activeSessionId = null;
        this.ws = null;
        this.outputBuffers = new Map();
        this.currentFilter = 'all';
        this.currentView = 'list';
        this.contextMenuSessionId = null;
        this.draggedSessionId = null;
        this.terminalVisible = true;
        this.autoScroll = true;
        this.streamingTimeout = null;
        this.lastOutputTime = new Map();
        this.hasRenderedOnce = false;
        this.reviewTemplates = [];
        this.wsReconnectAttempts = 0;
        this.wsReconnectTimeout = null;
        this.maxWsReconnectAttempts = 5;

        this.init();
    }

    init() {
        this.connectWebSocket();
        this.setupEventListeners();
        this.setupContextMenu();
        this.setupDragAndDrop();
        this.setupTerminalScroll();
    }

    connectWebSocket() {
        if (this.wsReconnectTimeout) {
            clearTimeout(this.wsReconnectTimeout);
            this.wsReconnectTimeout = null;
        }
        
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
        }
        
        this.wsReconnectAttempts = 0;
        this._connectWebSocket();
    }
    
    _connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.wsReconnectAttempts = 0;
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
            }
        };

        this.ws.onclose = () => {
            if (this.wsReconnectAttempts < this.maxWsReconnectAttempts) {
                this.wsReconnectAttempts++;
                const delay = Math.min(1000 * Math.pow(2, this.wsReconnectAttempts), 30000);
                console.log(`WebSocket closed, reconnecting in ${delay}ms (attempt ${this.wsReconnectAttempts}/${this.maxWsReconnectAttempts})`);
                this.wsReconnectTimeout = setTimeout(() => this._connectWebSocket(), delay);
            } else {
                console.error('WebSocket max reconnect attempts reached');
                Toast.error('Connection Lost', 'Unable to reconnect. Please refresh the page.');
            }
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    handleMessage(data) {
        switch (data.type) {
            case 'init':
                this.initSessions(data.sessions);
                break;
            case 'output':
                this.handleOutput(data.session_id, data.data);
                break;
            case 'status':
                this.handleStatusChange(data.session_id, data.status, data.session);
                break;
            case 'session_created':
                this.handleSessionCreated(data.session);
                break;
            case 'error':
                this.handleError(data.message);
                break;
        }
    }

    handleError(message) {
        console.error('Server error:', message);
        // Offer to create missing working directory
        if (message.includes('Working directory does not exist:') && this._pendingCreate) {
            const dir = message.split(': ').slice(1).join(': ');
            if (confirm(`Directory does not exist:\n${dir}\n\nCreate it?`)) {
                const { name, workingDir } = this._pendingCreate;
                this._pendingCreate = null;
                this.createSession(name, workingDir, true);
                return;
            }
            this._pendingCreate = null;
        }
        Toast.error('Error', message);
    }

    handleSessionCreated(session) {
        console.log('New session created:', session);
        this.sessions.set(session.id, session);
        this.outputBuffers.set(session.id, []);
        this.renderSessions();
        this.updateStats();
        Toast.success('Session Created', `${session.name} (#${session.id}) is starting...`);
    }

    initSessions(sessions) {
        this.sessions.clear();
        sessions.forEach(session => {
            this.sessions.set(session.id, session);
            this.outputBuffers.set(session.id, []);
        });
        this.renderSessions();
        this.updateStats();
    }

    handleOutput(sessionId, data) {
        this.outputBuffers.set(sessionId, [data]);
        this.lastOutputTime.set(sessionId, Date.now());

        if (sessionId === this.activeSessionId) {
            this.replaceTerminalContent(data);
            this.showStreamingIndicator();
            this.flashTerminal();
        }

        const session = this.sessions.get(sessionId);
        if (session) {
            session.last_output = data.slice(-200);
            this.updateSessionCard(sessionId);
        }
    }

    showStreamingIndicator() {
        const indicator = document.getElementById('streaming-indicator');
        const timeEl = document.getElementById('streaming-time');
        
        if (indicator) {
            indicator.classList.add('active');
        }
        
        if (timeEl) {
            const now = new Date();
            timeEl.textContent = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
        }

        if (this.streamingTimeout) {
            clearTimeout(this.streamingTimeout);
        }

        this.streamingTimeout = setTimeout(() => {
            if (indicator) {
                indicator.classList.remove('active');
            }
        }, 3000);
    }

    flashTerminal() {
        const output = document.getElementById('terminal-output');
        if (output && !this.autoScroll && this.hasRenderedOnce) {
            output.classList.remove('flash');
            void output.offsetWidth;
            output.classList.add('flash');
        }
        this.hasRenderedOnce = true;
    }

    setupTerminalScroll() {
        const output = document.getElementById('terminal-output');
        if (!output) return;

        output.addEventListener('scroll', () => {
            if (!this.autoScroll) {
                const isNearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 100;
                const indicator = document.getElementById('new-output-indicator');
                if (indicator) {
                    indicator.classList.toggle('visible', !isNearBottom && this.activeSessionId);
                }
            }
        });
    }

    toggleAutoScroll() {
        this.autoScroll = !this.autoScroll;
        const btn = document.getElementById('autoscroll-btn');
        const indicator = document.getElementById('new-output-indicator');

        if (btn) {
            btn.classList.toggle('disabled', !this.autoScroll);
            btn.title = this.autoScroll ? 'Auto-scroll enabled' : 'Auto-scroll disabled';
        }

        if (this.autoScroll) {
            this.scrollToBottom();
            if (indicator) indicator.classList.remove('visible');
        }
    }

    scrollToBottom() {
        const output = document.getElementById('terminal-output');
        if (output) {
            output.scrollTop = output.scrollHeight;
        }
        const indicator = document.getElementById('new-output-indicator');
        if (indicator) {
            indicator.classList.remove('visible');
        }
    }

    handleStatusChange(sessionId, status, sessionData) {
        if (sessionData) {
            this.sessions.set(sessionId, sessionData);
        } else {
            const session = this.sessions.get(sessionId);
            if (session) {
                session.status = status;
            }
        }
        this.updateSessionCard(sessionId);
        this.updateStats();

        // Show notification for needs_attention
        if (status === 'needs_attention') {
            this.showNotification(sessionId);
        }
    }

    showNotification(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) return;

        if (Notification.permission === 'granted') {
            new Notification(`Autowrkers - ${session.name}`, {
                body: 'Session needs your attention',
                icon: '/static/favicon.svg'
            });
        } else if (Notification.permission !== 'denied') {
            Notification.requestPermission();
        }
    }

    renderSessions() {
        const grid = document.getElementById('sessions-grid');
        grid.innerHTML = '';

        const sessionsArray = Array.from(this.sessions.values());

        // Update filter counts
        this.updateFilterCounts(sessionsArray);

        const filtered = this.filterSessions(sessionsArray);

        if (filtered.length === 0) {
            const msg = this.currentFilter === 'all' ? 'No sessions yet' : `No ${this.currentFilter.replace('_', ' ')} sessions`;
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">📭</div>
                    <p>${msg}</p>
                    <p style="margin-top: 8px; font-size: 12px;">Click "+ New Session" to start</p>
                </div>
            `;
            return;
        }

        // Group sessions: parents first, then their children
        const parentSessions = filtered.filter(s => !s.parent_id);
        const childrenByParent = new Map();

        filtered.forEach(session => {
            if (session.parent_id) {
                if (!childrenByParent.has(session.parent_id)) {
                    childrenByParent.set(session.parent_id, []);
                }
                childrenByParent.get(session.parent_id).push(session);
            }
        });

        // Render parent sessions with their children nested below
        parentSessions.forEach(parent => {
            const group = this.createSessionGroup(parent, childrenByParent.get(parent.id) || []);
            grid.appendChild(group);
        });

        // Render orphan children (parent not in filtered list)
        filtered.forEach(session => {
            if (session.parent_id && !parentSessions.find(p => p.id === session.parent_id)) {
                grid.appendChild(this.createSessionCard(session, true));
            }
        });
    }

    createSessionGroup(parent, children) {
        const group = document.createElement('div');
        group.className = 'session-group';

        // Add parent card
        group.appendChild(this.createSessionCard(parent, false));

        // Add children container if there are children
        if (children.length > 0) {
            const childrenContainer = document.createElement('div');
            childrenContainer.className = 'session-children';

            children.forEach((child, index) => {
                const isLast = index === children.length - 1;
                childrenContainer.appendChild(this.createSessionCard(child, true, isLast));
            });

            group.appendChild(childrenContainer);
        }

        return group;
    }

    filterSessions(sessions) {
        if (this.currentFilter === 'all') return sessions;
        if (this.currentFilter === 'stopped') {
            return sessions.filter(s => s.status === 'stopped' || s.status === 'error' || s.status === 'completed');
        }
        return sessions.filter(s => s.status === this.currentFilter);
    }

    updateFilterCounts(sessions) {
        const counts = { all: sessions.length, running: 0, needs_attention: 0, stopped: 0 };
        sessions.forEach(s => {
            if (s.status === 'running') counts.running++;
            else if (s.status === 'needs_attention') counts.needs_attention++;
            else if (s.status === 'stopped' || s.status === 'error' || s.status === 'completed') counts.stopped++;
        });
        const el = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
        el('filter-count-all', counts.all);
        el('filter-count-running', counts.running);
        el('filter-count-attention', counts.needs_attention);
        el('filter-count-stopped', counts.stopped);
    }

    createSessionCard(session, isChild = false, isLast = false) {
        const card = document.createElement('div');
        const childClass = isChild ? 'session-card-child' : '';
        const lastChildClass = isChild && isLast ? 'last-child' : '';
        const statusCardClass = `status-${session.status}-card`;
        card.className = `session-card ${childClass} ${lastChildClass} ${statusCardClass} ${session.status === 'needs_attention' ? 'needs-attention' : ''} ${session.status === 'queued' ? 'queued' : ''} ${session.id === this.activeSessionId ? 'active' : ''}`;
        card.dataset.sessionId = session.id;
        card.onclick = () => this.selectSession(session.id);

        // Right-click context menu (same as Kanban cards)
        card.oncontextmenu = (e) => {
            e.preventDefault();
            this.showContextMenu(e.clientX, e.clientY, session.id);
        };

        const statusLabel = {
            'running': 'Running',
            'needs_attention': 'Attention',
            'stopped': 'Stopped',
            'error': 'Error',
            'starting': 'Starting',
            'queued': 'Queued',
            'completed': 'Done'
        }[session.status] || session.status;

        const childIndicator = isChild ? '<span class="child-indicator">↳</span>' : '';
        const parentInfo = isChild && session.parent_id ? `<span class="parent-info">child of #${session.parent_id}</span>` : '';
        const timeAgo = session.created_at ? this.timeAgo(session.created_at) : '';

        card.innerHTML = `
            <div class="session-card-header">
                <span class="session-name">
                    ${childIndicator}
                    <span class="session-id">#${session.id}</span>
                    ${this.escapeHtml(session.name)}
                    ${session.status === 'needs_attention' ? '<span class="notification-dot"></span>' : ''}
                </span>
                <span class="status-badge status-${this.escapeHtml(session.status)}">${this.escapeHtml(statusLabel)}</span>
            </div>
            <div class="session-meta">
                <span class="session-meta-path">${this.escapeHtml(this.truncatePath(session.working_dir))}</span>
                ${timeAgo ? `<span class="session-meta-sep">&middot;</span><span class="session-meta-time">${timeAgo}</span>` : ''}
                ${parentInfo}
            </div>
            ${session.last_output ? `<div class="session-preview">${this.escapeHtml(session.last_output.slice(-120))}</div>` : ''}
        `;

        return card;
    }

    timeAgo(dateStr) {
        try {
            const date = new Date(dateStr);
            const now = new Date();
            const diffMs = now - date;
            const diffS = Math.floor(diffMs / 1000);
            if (diffS < 60) return 'just now';
            const diffM = Math.floor(diffS / 60);
            if (diffM < 60) return `${diffM}m ago`;
            const diffH = Math.floor(diffM / 60);
            if (diffH < 24) return `${diffH}h ago`;
            const diffD = Math.floor(diffH / 24);
            if (diffD < 7) return `${diffD}d ago`;
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        } catch {
            return '';
        }
    }

    updateSessionCard(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) return;

        const existingCard = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (existingCard) {
            const newCard = this.createSessionCard(session);
            existingCard.replaceWith(newCard);
        } else {
            this.renderSessions();
        }
    }

    async selectSession(sessionId) {
        this.activeSessionId = sessionId;
        const session = this.sessions.get(sessionId);

        document.querySelectorAll('.session-card').forEach(card => {
            card.classList.remove('active');
        });
        const activeCard = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (activeCard) {
            activeCard.classList.add('active');
        }

        document.getElementById('active-session-title').textContent =
            session ? `${session.name} (#${session.id})` : 'Select a session';

        const indicator = document.getElementById('streaming-indicator');
        const newOutputIndicator = document.getElementById('new-output-indicator');
        if (indicator) indicator.classList.remove('active');
        if (newOutputIndicator) newOutputIndicator.classList.remove('visible');

        const isRunning = session && (session.status === 'running' || session.status === 'starting');
        const lastOutput = this.lastOutputTime.get(sessionId);
        const recentlyActive = lastOutput && (Date.now() - lastOutput < 5000);
        if (indicator && isRunning && recentlyActive) {
            indicator.classList.add('active');
        }

        const output = document.getElementById('terminal-output');
        output.innerHTML = '<div class="placeholder">Loading output...</div>';

        try {
            const response = await fetch(`/api/sessions/${sessionId}/output`);
            const data = await response.json();

            if (data.output) {
                this.outputBuffers.set(sessionId, [data.output]);
                output.innerHTML = `<div class="terminal-content">${this.ansiToHtml(data.output)}</div>`;
            } else {
                output.innerHTML = '<div class="placeholder">No output yet...</div>';
            }
        } catch (e) {
            console.error('Failed to load session output:', e);
            const buffer = this.outputBuffers.get(sessionId) || [];
            output.innerHTML = buffer.length > 0
                ? `<div class="terminal-content">${this.ansiToHtml(buffer.join(''))}</div>`
                : '<div class="placeholder">No output yet...</div>';
        }

        if (this.autoScroll) {
            output.scrollTop = output.scrollHeight;
        }

        document.getElementById('terminal-input').focus();
    }

    appendToTerminal(data) {
        const output = document.getElementById('terminal-output');
        const placeholder = output.querySelector('.placeholder');

        if (placeholder) {
            output.innerHTML = '';
        }

        let container = output.querySelector('.terminal-content');
        if (!container) {
            container = document.createElement('div');
            container.className = 'terminal-content';
            output.appendChild(container);
        }

        // Append new ANSI-parsed content
        container.innerHTML += this.ansiToHtml(data);
        output.scrollTop = output.scrollHeight;
    }

    replaceTerminalContent(data) {
        const output = document.getElementById('terminal-output');
        const wasAtBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 100;
        
        output.innerHTML = `<div class="terminal-content">${this.ansiToHtml(data)}</div>`;
        
        this.updateOutputStats(data);
        
        if (this.autoScroll || wasAtBottom) {
            output.scrollTop = output.scrollHeight;
        } else {
            const indicator = document.getElementById('new-output-indicator');
            if (indicator) indicator.classList.add('visible');
        }
    }

    updateOutputStats(data) {
        const statsEl = document.getElementById('output-stats');
        if (!statsEl || !data) {
            if (statsEl) statsEl.textContent = '';
            return;
        }
        const lines = data.split('\n').length;
        const bytes = new Blob([data]).size;
        const kbSize = (bytes / 1024).toFixed(1);
        statsEl.textContent = `${lines} lines · ${kbSize} KB`;
    }

    clearTerminal() {
        if (this.activeSessionId) {
            this.outputBuffers.set(this.activeSessionId, []);
            document.getElementById('terminal-output').innerHTML =
                '<div class="placeholder">Terminal cleared</div>';
        }
    }

    toggleTerminal() {
        this.terminalVisible = !this.terminalVisible;
        const terminalPanel = document.getElementById('terminal-panel');
        const showBtn = document.getElementById('terminal-show-btn');
        const toggleBtn = document.getElementById('terminal-toggle');

        if (this.terminalVisible) {
            terminalPanel.classList.remove('hidden');
            showBtn.classList.remove('visible');
            toggleBtn.innerHTML = '◀';
            toggleBtn.title = 'Hide Terminal';
        } else {
            terminalPanel.classList.add('hidden');
            showBtn.classList.add('visible');
            toggleBtn.innerHTML = '▶';
            toggleBtn.title = 'Show Terminal';
        }
    }

    updateStats() {
        const total = this.sessions.size;
        const attention = Array.from(this.sessions.values())
            .filter(s => s.status === 'needs_attention').length;

        document.getElementById('session-count').textContent =
            `${total} session${total !== 1 ? 's' : ''}`;
        document.getElementById('attention-count').textContent =
            `${attention} need${attention === 1 ? 's' : ''} attention`;
    }

    setupEventListeners() {
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.onclick = () => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentFilter = btn.dataset.filter;
                this.renderSessions();
            };
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'End' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                this.scrollToBottom();
                if (!this.autoScroll) this.toggleAutoScroll();
            }
            if (e.key === 'End' && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
                const activeEl = document.activeElement;
                const isInputFocused = activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA';
                if (!isInputFocused) {
                    e.preventDefault();
                    this.scrollToBottom();
                }
            }
        });

        // Request notification permission
        if (Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }

    sendInput(text) {
        if (!this.activeSessionId) {
            console.warn('No active session selected');
            return;
        }

        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            console.error('WebSocket not connected');
            return;
        }

        const input = text !== undefined ? text : document.getElementById('terminal-input').value;

        this.ws.send(JSON.stringify({
            type: 'input',
            session_id: this.activeSessionId,
            data: input + '\r'
        }));

        document.getElementById('terminal-input').value = '';
    }

    createSession(name, workingDir, createDir = false) {
        this._pendingCreate = { name, workingDir };
        this.ws.send(JSON.stringify({
            type: 'create',
            name: name || undefined,
            working_dir: workingDir || undefined,
            create_dir: createDir || undefined
        }));
    }

    stopSession(sessionId) {
        this.ws.send(JSON.stringify({
            type: 'stop',
            session_id: sessionId
        }));
    }

    truncatePath(path) {
        if (!path) return '';
        if (path.length <= 30) return path;
        return '...' + path.slice(-27);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Parse ANSI escape codes and convert to HTML
    ansiToHtml(text) {
        // Remove cursor/mode control sequences
        text = text.replace(/\x1b\[\?[0-9;]*[hlsc]/g, '');
        text = text.replace(/\x1b\[[0-9]*[ABCDEFGJKST]/g, '');
        text = text.replace(/\x1b\[[0-9;]*[Hf]/g, '');
        text = text.replace(/\x1b\]0;[^\x07]*\x07/g, ''); // Window title
        text = text.replace(/\x1b\[\?2026[hl]/g, '');

        // ANSI color map
        const colors = {
            30: '#1e1e1e', 31: '#f85149', 32: '#3fb950', 33: '#d29922',
            34: '#58a6ff', 35: '#a371f7', 36: '#39c5cf', 37: '#e6edf3',
            90: '#8b949e', 91: '#ff7b72', 92: '#7ee787', 93: '#e3b341',
            94: '#79c0ff', 95: '#d2a8ff', 96: '#56d4dd', 97: '#ffffff'
        };

        // 256 color approximation for common colors
        const color256 = (n) => {
            if (n < 16) {
                const basic = ['#000','#800','#080','#880','#008','#808','#088','#ccc',
                              '#888','#f00','#0f0','#ff0','#00f','#f0f','#0ff','#fff'];
                return basic[n];
            }
            if (n >= 232) return `rgb(${(n-232)*10+8},${(n-232)*10+8},${(n-232)*10+8})`;
            n -= 16;
            const r = Math.floor(n/36) * 51;
            const g = Math.floor((n%36)/6) * 51;
            const b = (n%6) * 51;
            return `rgb(${r},${g},${b})`;
        };

        let result = '';
        let currentStyle = {};
        let i = 0;

        while (i < text.length) {
            if (text[i] === '\x1b' && i + 1 < text.length && text[i+1] === '[') {
                // Parse ANSI sequence
                let j = i + 2;
                while (j < text.length && !/[a-zA-Z]/.test(text[j])) j++;
                if (j >= text.length) { i++; continue; } // Incomplete sequence
                const code = text.slice(i+2, j);
                const cmd = text[j];

                if (cmd === 'm') {
                    const parts = code.split(';').map(Number);
                    for (let k = 0; k < parts.length; k++) {
                        const p = parts[k];
                        if (p === 0) currentStyle = {};
                        else if (p === 1) currentStyle.bold = true;
                        else if (p === 2) currentStyle.dim = true;
                        else if (p === 22) { currentStyle.bold = false; currentStyle.dim = false; }
                        else if (p === 7) currentStyle.inverse = true;
                        else if (p === 27) currentStyle.inverse = false;
                        else if (p >= 30 && p <= 37) currentStyle.fg = colors[p];
                        else if (p >= 90 && p <= 97) currentStyle.fg = colors[p];
                        else if (p === 39) delete currentStyle.fg;
                        else if (p === 38 && k + 2 < parts.length && parts[k+1] === 5) { currentStyle.fg = color256(parts[k+2]); k += 2; }
                        else if (p >= 40 && p <= 47) currentStyle.bg = colors[p-10];
                        else if (p === 49) delete currentStyle.bg;
                    }
                }
                i = j + 1;
            } else if (text[i] === '\r') {
                i++;
            } else if (text[i] === '\n') {
                result += '<br>';
                i++;
            } else {
                // Build style string
                let style = '';
                if (currentStyle.fg) style += `color:${currentStyle.fg};`;
                if (currentStyle.bg) style += `background:${currentStyle.bg};`;
                if (currentStyle.bold) style += 'font-weight:bold;';
                if (currentStyle.dim) style += 'opacity:0.6;';

                const char = this.escapeHtml(text[i]);
                if (style) {
                    result += `<span style="${style}">${char}</span>`;
                } else {
                    result += char;
                }
                i++;
            }
        }
        return result;
    }

    // ============== VIEW TOGGLE ==============

    setView(view) {
        this.currentView = view;

        // Update toggle buttons
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });

        // Show/hide views
        const listView = document.getElementById('list-view');
        const kanbanView = document.getElementById('kanban-view');

        if (view === 'list') {
            listView.style.display = 'flex';
            kanbanView.style.display = 'none';
            this.renderSessions();
        } else {
            listView.style.display = 'none';
            kanbanView.style.display = 'flex';
            this.renderKanban();
        }
    }

    // ============== KANBAN BOARD ==============

    renderKanban() {
        const statuses = ['queued', 'running', 'needs_attention', 'completed'];
        const sessionsArray = Array.from(this.sessions.values());

        // Also include stopped/error in completed column
        const statusMap = {
            'queued': 'queued',
            'starting': 'running',
            'running': 'running',
            'needs_attention': 'needs_attention',
            'completed': 'completed',
            'stopped': 'completed',
            'error': 'completed'
        };

        // Group sessions by status, separating parents and children
        const columns = {};
        statuses.forEach(s => columns[s] = { parents: [], children: new Map() });

        sessionsArray.forEach(session => {
            const column = statusMap[session.status] || 'running';
            if (session.parent_id) {
                // It's a child - group under parent
                if (!columns[column].children.has(session.parent_id)) {
                    columns[column].children.set(session.parent_id, []);
                }
                columns[column].children.get(session.parent_id).push(session);
            } else {
                columns[column].parents.push(session);
            }
        });

        // Render each column
        statuses.forEach(status => {
            const container = document.getElementById(`kanban-${status}`);
            const countEl = document.getElementById(`kanban-count-${status}`);
            container.innerHTML = '';

            const { parents, children } = columns[status];
            const totalCount = parents.length + Array.from(children.values()).flat().length;
            countEl.textContent = totalCount;

            // Render parent cards with their children
            parents.forEach(parent => {
                const parentChildren = children.get(parent.id) || [];
                container.appendChild(this.createKanbanCard(parent, parentChildren));
            });

            // Render orphan children (parent in different column)
            children.forEach((childList, parentId) => {
                if (!parents.find(p => p.id === parentId)) {
                    childList.forEach(child => {
                        container.appendChild(this.createKanbanCard(child, [], true));
                    });
                }
            });
        });
    }

    createKanbanCard(session, children = [], isOrphan = false) {
        const card = document.createElement('div');
        card.className = `kanban-card ${session.id === this.activeSessionId ? 'active' : ''}`;
        card.dataset.sessionId = session.id;
        card.draggable = true;

        // Click to select
        card.onclick = (e) => {
            if (!e.target.closest('.kanban-child-card')) {
                this.selectSession(session.id);
            }
        };

        // Right-click for context menu
        card.oncontextmenu = (e) => {
            e.preventDefault();
            this.showContextMenu(e.clientX, e.clientY, session.id);
        };

        const statusBadge = this.getStatusBadge(session.status);
        const outputSnippet = session.last_output
            ? this.stripAnsi(session.last_output).slice(-80).trim()
            : '';

        let childrenHtml = '';
        if (children.length > 0) {
            childrenHtml = `
                <div class="kanban-card-children">
                    ${children.map(child => `
                        <div class="kanban-child-card" data-session-id="${child.id}"
                             onclick="app.selectSession(${child.id})"
                             oncontextmenu="event.preventDefault(); app.showContextMenu(event.clientX, event.clientY, ${child.id})">
                            <div class="kanban-card-name">
                                <span class="kanban-card-id">#${child.id}</span>
                                ${this.escapeHtml(child.name)}
                            </div>
                            ${this.getStatusBadge(child.status)}
                        </div>
                    `).join('')}
                </div>
            `;
        }

        const orphanLabel = isOrphan && session.parent_id
            ? `<span class="parent-info">child of #${session.parent_id}</span>`
            : '';

        card.innerHTML = `
            <div class="kanban-card-header">
                <div class="kanban-card-name">
                    <span class="kanban-card-id">#${session.id}</span>
                    ${this.escapeHtml(session.name)}
                    ${orphanLabel}
                </div>
                ${statusBadge}
            </div>
            ${outputSnippet ? `<div class="kanban-card-output">${this.escapeHtml(outputSnippet)}</div>` : ''}
            ${childrenHtml}
        `;

        return card;
    }

    getStatusBadge(status) {
        const labels = {
            'running': 'Running',
            'needs_attention': 'Attention',
            'stopped': 'Stopped',
            'error': 'Error',
            'starting': 'Starting',
            'queued': 'Queued',
            'completed': 'Completed'
        };
        return `<span class="status-badge status-${status}">${labels[status] || status}</span>`;
    }

    stripAnsi(text) {
        return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '');
    }

    // ============== CONTEXT MENU ==============

    setupContextMenu() {
        // Close context menu on click elsewhere
        document.addEventListener('click', () => this.hideContextMenu());
        document.addEventListener('contextmenu', (e) => {
            if (!e.target.closest('.kanban-card') && !e.target.closest('.session-card')) {
                this.hideContextMenu();
            }
        });
    }

    showContextMenu(x, y, sessionId) {
        this.contextMenuSessionId = sessionId;
        const menu = document.getElementById('context-menu');
        menu.style.left = x + 'px';
        menu.style.top = y + 'px';
        menu.classList.add('open');

        // Adjust if off-screen
        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) {
            menu.style.left = (x - rect.width) + 'px';
        }
        if (rect.bottom > window.innerHeight) {
            menu.style.top = (y - rect.height) + 'px';
        }
    }

    hideContextMenu() {
        document.getElementById('context-menu').classList.remove('open');
    }

    contextMenuSetParent() {
        this.hideContextMenu();
        this.showParentModal(this.contextMenuSessionId);
    }

    contextMenuRemoveParent() {
        this.hideContextMenu();
        this.updateSessionParent(this.contextMenuSessionId, null);
    }

    contextMenuComplete() {
        this.hideContextMenu();
        this.completeSession(this.contextMenuSessionId);
    }

    contextMenuStop() {
        this.hideContextMenu();
        if (confirm('Are you sure you want to stop this session?')) {
            this.stopSession(this.contextMenuSessionId);
        }
    }

    contextMenuDelete() {
        this.hideContextMenu();
        if (confirm('Are you sure you want to DELETE this session? This cannot be undone.')) {
            this.deleteSession(this.contextMenuSessionId);
        }
    }

    async deleteSession(sessionId) {
        try {
            const response = await fetch(`/api/sessions/${sessionId}`, {
                method: 'DELETE'
            });
            const data = await response.json();

            if (data.success) {
                // Remove from local state
                this.sessions.delete(sessionId);
                this.outputBuffers.delete(sessionId);
                this.lastOutputTime.delete(sessionId);

                // Clear active session if it was the deleted one
                if (this.activeSessionId === sessionId) {
                    this.activeSessionId = null;
                    document.getElementById('active-session-title').textContent = 'Select a session';
                    document.getElementById('terminal-output').innerHTML =
                        '<div class="placeholder">Click on a session card to view its output</div>';
                }

                // Re-render
                this.renderCurrentView();
                this.updateStats();
                Toast.success('Session Deleted', `Session #${sessionId} has been deleted`);
            } else {
                Toast.error('Delete Failed', data.detail || 'Could not delete session');
            }
        } catch (e) {
            console.error('Failed to delete session:', e);
            Toast.error('Delete Failed', 'Could not delete session');
        }
    }

    // ============== PARENT SELECTION MODAL ==============

    showParentModal(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) return;

        const options = document.getElementById('parent-options');
        const eligibleParents = Array.from(this.sessions.values())
            .filter(s => s.id !== sessionId && s.parent_id !== sessionId);

        if (eligibleParents.length === 0) {
            options.innerHTML = '<p style="color: var(--text-secondary);">No other sessions available as parents.</p>';
        } else {
            options.innerHTML = eligibleParents.map(parent => `
                <div class="parent-option" onclick="app.selectParent(${sessionId}, ${parent.id})">
                    <span class="parent-option-id">#${parent.id}</span>
                    <span class="parent-option-name">${this.escapeHtml(parent.name)}</span>
                    ${this.getStatusBadge(parent.status)}
                </div>
            `).join('');
        }

        document.getElementById('parent-select-modal').classList.add('open');
    }

    closeParentModal() {
        document.getElementById('parent-select-modal').classList.remove('open');
    }

    selectParent(childId, parentId) {
        this.closeParentModal();
        this.updateSessionParent(childId, parentId);
    }

    async updateSessionParent(sessionId, parentId) {
        try {
            const response = await fetch(`/api/sessions/${sessionId}/parent`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent_id: parentId })
            });
            const data = await response.json();
            if (data.success) {
                // Update local session
                const session = this.sessions.get(sessionId);
                if (session) {
                    session.parent_id = parentId;
                    this.renderCurrentView();
                }
            }
        } catch (e) {
            console.error('Failed to update parent:', e);
        }
    }

    async completeSession(sessionId) {
        try {
            await fetch(`/api/sessions/${sessionId}/complete`, { method: 'POST' });
        } catch (e) {
            console.error('Failed to complete session:', e);
        }
    }

    renderCurrentView() {
        if (this.currentView === 'kanban') {
            this.renderKanban();
        } else {
            this.renderSessions();
        }
    }

    // ============== DRAG AND DROP ==============

    setupDragAndDrop() {
        document.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.kanban-card');
            if (card) {
                this.draggedSessionId = parseInt(card.dataset.sessionId);
                card.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            }
        });

        document.addEventListener('dragend', (e) => {
            const card = e.target.closest('.kanban-card');
            if (card) {
                card.classList.remove('dragging');
                this.draggedSessionId = null;
            }
            document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            document.querySelectorAll('.column-drag-over').forEach(el => el.classList.remove('column-drag-over'));
        });

        document.addEventListener('dragover', (e) => {
            e.preventDefault();

            // Check if dragging over a card (for parent-child)
            const card = e.target.closest('.kanban-card');
            if (card && this.draggedSessionId && parseInt(card.dataset.sessionId) !== this.draggedSessionId) {
                card.classList.add('drag-over');
                return;
            }

            // Check if dragging over a column (for status change)
            const column = e.target.closest('.kanban-column');
            if (column && this.draggedSessionId) {
                // Remove drag-over from other columns
                document.querySelectorAll('.kanban-column').forEach(c => c.classList.remove('column-drag-over'));
                column.classList.add('column-drag-over');
            }
        });

        document.addEventListener('dragleave', (e) => {
            const card = e.target.closest('.kanban-card');
            if (card) {
                card.classList.remove('drag-over');
            }

            // Check if leaving a column
            const column = e.target.closest('.kanban-column');
            if (column && e.relatedTarget && !column.contains(e.relatedTarget)) {
                column.classList.remove('column-drag-over');
            }
        });

        document.addEventListener('drop', (e) => {
            e.preventDefault();

            // Check if dropping on a card (parent-child relationship)
            const targetCard = e.target.closest('.kanban-card');
            if (targetCard && this.draggedSessionId) {
                const targetId = parseInt(targetCard.dataset.sessionId);
                if (targetId !== this.draggedSessionId) {
                    // Set dragged session as child of target
                    this.updateSessionParent(this.draggedSessionId, targetId);
                }
            } else {
                // Check if dropping on a column (status change)
                const column = e.target.closest('.kanban-column');
                if (column && this.draggedSessionId) {
                    const targetStatus = column.dataset.status;
                    this.moveSessionToStatus(this.draggedSessionId, targetStatus);
                }
            }

            document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
            document.querySelectorAll('.column-drag-over').forEach(el => el.classList.remove('column-drag-over'));
        });
    }

    async moveSessionToStatus(sessionId, targetStatus) {
        const session = this.sessions.get(sessionId);
        if (!session) return;

        // Map target column to action
        switch (targetStatus) {
            case 'completed':
                // Mark as completed
                await this.completeSession(sessionId);
                break;
            case 'running':
                // If queued, we can't manually start it (depends on parent)
                // If stopped/completed, we'd need to restart - not supported yet
                if (session.status === 'queued') {
                    Toast.info('Queued Session', 'Queued sessions will start automatically when their parent completes.');
                } else if (session.status === 'stopped' || session.status === 'completed') {
                    Toast.warning('Cannot Restart', 'Cannot restart stopped/completed sessions. Create a new session instead.');
                }
                break;
            case 'queued':
                Toast.info('Queue Session', 'To queue a session, set it as a child of a running session using the context menu.');
                break;
            case 'needs_attention':
                Toast.info('Automatic Status', 'Sessions are marked "Needs Attention" automatically when they require input.');
                break;
        }
    }

    async openReviewModal() {
        if (!this.activeSessionId) {
            Toast.warning('No Session', 'Select a session first');
            return;
        }
        document.getElementById('review-modal').classList.add('open');
        await this.loadReviewTemplates();
    }

    closeReviewModal() {
        document.getElementById('review-modal').classList.remove('open');
    }

    async loadReviewTemplates() {
        try {
            const response = await fetch('/api/workflow/templates');
            const data = await response.json();
            this.reviewTemplates = data.templates || [];

            const select = document.getElementById('review-template');
            if (this.reviewTemplates.length === 0) {
                select.innerHTML = '<option value="">No templates available</option>';
                document.getElementById('review-phases').innerHTML =
                    '<span style="color: var(--text-secondary)">Create templates on the Workflows page</span>';
                return;
            }

            select.innerHTML = this.reviewTemplates.map(t =>
                `<option value="${t.id}" ${t.is_default ? 'selected' : ''}>${this.escapeHtml(t.name)}</option>`
            ).join('');

            const defaultTemplate = this.reviewTemplates.find(t => t.is_default) || this.reviewTemplates[0];
            if (defaultTemplate) {
                this.updateReviewPreview(defaultTemplate);
            }

            select.onchange = () => {
                const template = this.reviewTemplates.find(t => t.id === select.value);
                if (template) this.updateReviewPreview(template);
            };
        } catch (error) {
            console.error('Failed to load templates:', error);
            document.getElementById('review-template').innerHTML = '<option value="">Error loading templates</option>';
        }
    }

    updateReviewPreview(template) {
        const container = document.getElementById('review-phases');
        if (!template.phases || template.phases.length === 0) {
            container.innerHTML = '<span style="color: var(--text-secondary)">No phases configured</span>';
            return;
        }
        container.innerHTML = template.phases.map(p => {
            const providerType = p.provider_config?.provider_type || 'claude';
            const providerClass = providerType.replace('_', '');
            return `<div class="review-phase-badge">
                <span class="review-phase-provider ${providerClass}">${providerType}</span>
                <span>${this.escapeHtml(p.name)}</span>
            </div>`;
        }).join('');
    }

    async startReview() {
        const session = this.sessions.get(this.activeSessionId);
        if (!session) return;

        const templateId = document.getElementById('review-template').value;
        if (!templateId) {
            Toast.warning('No Template', 'Select a review template first');
            return;
        }

        const focus = document.getElementById('review-task').value.trim();

        const taskDescription = focus
            ? `Review session "${session.name}": ${focus}`
            : `Review code changes from session "${session.name}"`;

        try {
            const response = await fetch('/api/workflow/executions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_description: taskDescription,
                    project_path: session.working_dir || '',
                    template_id: templateId,
                    session_id: session.id
                })
            });

            const data = await response.json();
            if (data.success && data.execution) {
                await fetch(`/api/workflow/executions/${data.execution.id}/run`, { method: 'POST' });
                this.closeReviewModal();
                Toast.success('Review Started', 'Multi-LLM review workflow started');
                document.getElementById('review-task').value = '';
            } else {
                Toast.error('Failed', data.error || 'Could not create review workflow');
            }
        } catch (error) {
            console.error('Failed to start review:', error);
            Toast.error('Failed', 'Could not start review workflow');
        }
    }
}

// Initialize app
const app = new Autowrkers();

// Global functions for HTML onclick handlers
async function createSession() {
    document.getElementById('new-session-modal').classList.add('open');

    // Auto-fill working directory from server's current directory
    try {
        const response = await fetch('/api/server/info');
        const data = await response.json();

        // Auto-fill working directory
        const dirInput = document.getElementById('session-dir');
        if (dirInput && data.working_directory && !dirInput.value) {
            dirInput.value = data.working_directory;

            // Update hint if present
            const hint = dirInput.nextElementSibling;
            if (hint && hint.classList.contains('form-hint')) {
                hint.innerHTML = `Auto-filled from server directory ${data.is_git_repo ? '(git repo detected)' : ''}`;
            }
        }

        // Auto-fill session name from directory name
        const nameInput = document.getElementById('session-name');
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

    document.getElementById('session-name').focus();
}

function closeModal() {
    document.getElementById('new-session-modal').classList.remove('open');
    document.getElementById('session-name').value = '';
    document.getElementById('session-dir').value = '';
}

function confirmCreateSession() {
    const name = document.getElementById('session-name').value;
    const dir = document.getElementById('session-dir').value;
    app.createSession(name, dir);
    closeModal();
}

function handleInput(event) {
    if (event.key === 'Enter') {
        app.sendInput();
    }
}

function sendInput() {
    app.sendInput();
}

function clearTerminal() {
    app.clearTerminal();
}

function stopActiveSession() {
    if (app.activeSessionId) {
        if (confirm('Are you sure you want to stop this session?')) {
            app.stopSession(app.activeSessionId);
        }
    }
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
        app.closeParentModal();
        app.closeReviewModal();
    }
});

document.getElementById('new-session-modal').addEventListener('click', (e) => {
    if (e.target.id === 'new-session-modal') {
        closeModal();
    }
});

document.getElementById('review-modal').addEventListener('click', (e) => {
    if (e.target.id === 'review-modal') {
        app.closeReviewModal();
    }
});
