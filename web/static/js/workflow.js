const workflowApp = {
    executions: [],
    templates: [],
    selectedExecution: null,
    selectedTemplatePhases: null,
    ws: null,
    currentFilter: 'all',

    providers: {},
    providerModels: {},
    oauthStatus: {},
    editingTemplate: null,
    editingPhases: [],

    pendingApproval: null,
    approvalCountdownInterval: null,
    isLoading: false,
    wsReconnectAttempts: 0,
    wsReconnectTimeout: null,
    maxWsReconnectAttempts: 5,

    todos: [],
    todoProgress: null,

    contextMenuExecutionId: null,

    async init() {
        this.requestNotificationPermission();
        this.setupKeyboardHandlers();
        this.setupContextMenu();
        await this.loadTemplates();
        await this.loadExecutions();
        this.updateStats();
    },
    
    setupKeyboardHandlers() {
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeAllModals();
            }
            if (e.key === 'n' && !this.isInputFocused() && !this.hasOpenModal()) {
                e.preventDefault();
                this.openNewWorkflowModal();
            }
            if (e.key === 't' && !this.isInputFocused() && !this.hasOpenModal()) {
                e.preventDefault();
                this.openTemplateModal();
            }
            if (e.key === 'r' && !this.isInputFocused() && !this.hasOpenModal() && this.selectedExecution?.status === 'pending') {
                e.preventDefault();
                this.runExecution(this.selectedExecution.id);
            }
        });
    },
    
    isInputFocused() {
        const active = document.activeElement;
        return active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT');
    },

    hasOpenModal() {
        return document.querySelector('.modal.open') !== null;
    },

    // ==================== Context Menu ====================

    setupContextMenu() {
        document.addEventListener('click', () => this.hideContextMenu());
        document.addEventListener('contextmenu', (e) => {
            if (!e.target.closest('.execution-card')) {
                this.hideContextMenu();
            }
        });
    },

    showContextMenu(x, y, executionId) {
        this.contextMenuExecutionId = executionId;
        const menu = document.getElementById('workflow-context-menu');
        menu.style.left = x + 'px';
        menu.style.top = y + 'px';
        menu.classList.add('open');

        // Update menu items based on execution status
        const execution = this.executions.find(e => e.id === executionId);
        const cancelItem = menu.querySelector('.context-menu-item:nth-child(4)');
        if (execution && execution.status === 'running') {
            cancelItem.style.display = 'flex';
        } else {
            cancelItem.style.display = 'none';
        }

        // Adjust if off-screen
        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) {
            menu.style.left = (x - rect.width) + 'px';
        }
        if (rect.bottom > window.innerHeight) {
            menu.style.top = (y - rect.height) + 'px';
        }
    },

    hideContextMenu() {
        const menu = document.getElementById('workflow-context-menu');
        if (menu) menu.classList.remove('open');
    },

    contextMenuViewArtifacts() {
        this.hideContextMenu();
        if (this.contextMenuExecutionId) {
            this.showArtifacts(this.contextMenuExecutionId);
        }
    },

    async contextMenuDuplicate() {
        this.hideContextMenu();
        const execution = this.executions.find(e => e.id === this.contextMenuExecutionId);
        if (!execution) return;

        // Pre-fill the new workflow modal with the same task
        document.getElementById('workflow-task').value = execution.task_description || '';
        document.getElementById('workflow-path').value = execution.project_path || '';
        this.openNewWorkflowModal();
    },

    contextMenuCancel() {
        this.hideContextMenu();
        if (this.contextMenuExecutionId) {
            this.cancelExecution(this.contextMenuExecutionId);
        }
    },

    contextMenuDelete() {
        this.hideContextMenu();
        if (confirm('Are you sure you want to DELETE this workflow? This cannot be undone.')) {
            this.deleteExecution(this.contextMenuExecutionId);
        }
    },

    async deleteExecution(executionId) {
        this.setLoading(true);
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || `HTTP ${response.status}`);
            }

            // Remove from local state
            this.executions = this.executions.filter(e => e.id !== executionId);

            // Clear selected if it was deleted
            if (this.selectedExecution?.id === executionId) {
                this.selectedExecution = null;
                document.getElementById('pipeline-title').textContent = 'Select a workflow';
                document.getElementById('pipeline-status').textContent = '';
                document.getElementById('pipeline-actions').innerHTML = '';
                document.getElementById('pipeline-view').innerHTML = `
                    <div class="pipeline-placeholder">
                        <div class="placeholder-icon">⚙</div>
                        <p>Select a workflow execution to view its pipeline</p>
                        <p class="placeholder-hint">or create a new workflow to get started</p>
                    </div>
                `;
            }

            this.renderExecutionList();
            this.updateStats();
            this.showToast('Workflow deleted', 'success');
        } catch (error) {
            console.error('Failed to delete execution:', error);
            this.showToast(`Failed to delete: ${error.message}`, 'error');
        } finally {
            this.setLoading(false);
        }
    },
    
    closeAllModals() {
        document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
        this.hideApprovalBanner();
    },
    
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
    
    setLoading(loading) {
        this.isLoading = loading;
        document.body.classList.toggle('loading', loading);
    },

    async loadTemplates() {
        try {
            const response = await fetch('/api/workflow/templates');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            this.templates = data.templates || [];
            this.populateTemplateSelect();
        } catch (error) {
            console.error('Failed to load templates:', error);
            this.showToast('Failed to load templates', 'error');
        }
    },

    async loadExecutions() {
        try {
            const response = await fetch('/api/workflow/executions?limit=100');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            this.executions = data.executions || [];
            this.renderExecutionList();
        } catch (error) {
            console.error('Failed to load executions:', error);
            this.showToast('Failed to load executions', 'error');
        }
    },

    populateTemplateSelect() {
        const select = document.getElementById('workflow-template');
        select.innerHTML = '<option value="">Select a template...</option>';
        
        this.templates.forEach(template => {
            const option = document.createElement('option');
            option.value = template.id;
            option.textContent = template.name;
            if (template.is_default) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    },

    renderExecutionList() {
        const container = document.getElementById('execution-list');
        const filtered = this.filterExecutionList(this.executions);
        
        if (filtered.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">⚙</div>
                    <p>No workflows yet</p>
                </div>
            `;
            return;
        }

        container.innerHTML = filtered.map(exec => {
            const safeDesc = this.escapeHtml(exec.task_description || 'Workflow');
            const displayStatus = this.selectedExecution?.id === exec.id
                ? (this.selectedExecution.status || exec.status)
                : exec.status;
            return `
            <div class="execution-card ${this.selectedExecution?.id === exec.id ? 'active' : ''}"
                 onclick="workflowApp.selectExecution('${this.escapeHtml(exec.id)}')"
                 oncontextmenu="event.preventDefault(); workflowApp.showContextMenu(event.clientX, event.clientY, '${this.escapeHtml(exec.id)}')"
                 data-execution-id="${this.escapeHtml(exec.id)}">
                <div class="execution-card-header">
                    <span class="execution-card-title" title="${safeDesc}">
                        ${this.truncate(safeDesc, 25)}
                    </span>
                    <span class="execution-card-status status-${displayStatus}">${displayStatus}</span>
                </div>
                ${this.renderMiniPipeline(exec)}
                <div class="execution-card-meta">
                    <span>${this.formatDate(exec.created_at)}</span>
                    <span>${exec.phases_completed || 0}/${exec.phases_total || 0} phases</span>
                </div>
            </div>
        `}).join('');
    },

    filterExecutionList(executions) {
        if (this.currentFilter === 'all') return executions;
        if (this.currentFilter === 'running') {
            return executions.filter(e => e.status === 'running' || e.status === 'pending');
        }
        if (this.currentFilter === 'completed') {
            return executions.filter(e => e.status === 'completed' || e.status === 'failed');
        }
        return executions;
    },

    filterExecutions(filter) {
        this.currentFilter = filter;
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.filter === filter);
        });
        this.renderExecutionList();
    },

    async selectExecution(executionId) {
        this.setLoading(true);
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            this.selectedExecution = data.execution;
            
            const idx = this.executions.findIndex(e => e.id === executionId);
            if (idx !== -1) {
                this.executions[idx] = { ...this.executions[idx], status: data.execution.status };
            }
            
            this.renderExecutionList();
            this.renderPipeline(data.execution, data.artifacts, data.template_phases);
            this.renderBudget(data.budget);
            this.connectWebSocket(executionId);
        } catch (error) {
            console.error('Failed to load execution:', error);
            this.showToast('Failed to load execution details', 'error');
        } finally {
            this.setLoading(false);
        }
    },

    renderPipeline(execution, artifacts, templatePhases = null) {
        const header = document.getElementById('pipeline-header');
        const view = document.getElementById('pipeline-view');
        
        document.getElementById('pipeline-title').textContent = 
            this.truncate(execution.task_description || 'Workflow', 50);
        
        const statusEl = document.getElementById('pipeline-status');
        statusEl.className = `pipeline-status status-${execution.status}`;
        statusEl.textContent = execution.status;

        const actionsEl = document.getElementById('pipeline-actions');
        actionsEl.innerHTML = this.renderActions(execution);

        const hasExecutedPhases = execution.phase_executions && execution.phase_executions.length > 0;
        
        if (!hasExecutedPhases && templatePhases && templatePhases.length > 0) {
            const pendingPhases = templatePhases.map((p, i) => ({
                id: p.id,
                phase_id: p.id,
                phase_name: p.name,
                phase_role: p.role,
                status: 'pending',
                provider_used: p.provider_config?.provider_type,
                model_used: p.provider_config?.model_name,
                order: p.order || i
            }));
            const phases = this.groupPhases(pendingPhases);
            view.innerHTML = `<div class="pipeline-container">${phases}</div>`;
            return;
        }
        
        if (!hasExecutedPhases) {
            view.innerHTML = `
                <div class="pipeline-placeholder">
                    <div class="placeholder-icon">⚙</div>
                    <p>No phases configured</p>
                </div>
            `;
            return;
        }

        const phases = this.groupPhases(execution.phase_executions);
        view.innerHTML = `<div class="pipeline-container">${phases}</div>`;
    },

    groupPhases(phases) {
        const sorted = [...phases].sort((a, b) => (a.order || 0) - (b.order || 0));
        let html = '';
        let i = 0;

        while (i < sorted.length) {
            const phase = sorted[i];
            const parallel = sorted.filter(p => p.parallel_with === phase.phase_id);
            
            if (parallel.length > 0) {
                html += `<div class="pipeline-phase">
                    <div class="parallel-group">
                        ${this.renderPhaseCard(phase)}
                        ${parallel.map(p => this.renderPhaseCard(p)).join('')}
                    </div>
                    ${i < sorted.length - parallel.length - 1 ? '<div class="phase-connector"></div>' : ''}
                </div>`;
                i += parallel.length + 1;
            } else if (!sorted.some(p => p.parallel_with === phase.phase_id)) {
                html += `<div class="pipeline-phase">
                    ${this.renderPhaseCard(phase)}
                    ${i < sorted.length - 1 ? '<div class="phase-connector"></div>' : ''}
                </div>`;
                i++;
            } else {
                i++;
            }
        }

        return html;
    },

    renderPhaseCard(phase) {
        const providerType = phase.provider_used || phase.provider_type || 'claude';
        const modelName = phase.model_used || phase.model_name || '';
        const statusIcon = this.getStatusIcon(phase.status);
        const duration = phase.duration_seconds 
            ? this.formatDuration(phase.duration_seconds) 
            : '--:--';

        return `
            <div class="phase-card phase-${phase.status}" onclick="workflowApp.showPhaseDetails('${phase.id}')">
                <div class="phase-header">
                    <span class="phase-name">${phase.phase_name || phase.name}</span>
                    <span class="phase-status-icon">${statusIcon}</span>
                </div>
                <div class="phase-provider">
                    <span class="provider-badge provider-${providerType}">${providerType}</span>
                    <span>${modelName}</span>
                </div>
                <div class="phase-meta">
                    <span>${phase.phase_role || phase.role || ''}</span>
                    <span class="phase-duration">${duration}</span>
                </div>
            </div>
        `;
    },

    renderActions(execution) {
        const actions = [];
        
        if (execution.status === 'pending') {
            actions.push(`<button class="btn btn-primary" onclick="workflowApp.runExecution('${execution.id}')">Run</button>`);
        }
        if (execution.status === 'running') {
            actions.push(`<button class="btn btn-danger" onclick="workflowApp.cancelExecution('${execution.id}')">Cancel</button>`);
        }
        if (execution.status === 'paused') {
            actions.push(`<button class="btn btn-primary" onclick="workflowApp.resumeExecution('${execution.id}')">Resume</button>`);
            actions.push(`<button class="btn btn-danger" onclick="workflowApp.cancelExecution('${execution.id}')">Cancel</button>`);
        }
        if (execution.status === 'awaiting_approval') {
            actions.push(`<button class="btn btn-danger" onclick="workflowApp.cancelExecution('${execution.id}')">Cancel</button>`);
        }
        if (['completed', 'failed', 'cancelled'].includes(execution.status)) {
            actions.push(`<button class="btn" onclick="workflowApp.showArtifacts('${execution.id}')">View Artifacts</button>`);
        }

        return actions.join('');
    },

    renderBudget(budget) {
        if (!budget) return;
        
        const fill = document.getElementById('budget-fill');
        const text = document.getElementById('budget-text');
        
        const used = budget.total_cost || 0;
        const limit = budget.limit || 0;
        const percent = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
        
        fill.style.width = limit > 0 ? `${percent}%` : '0%';
        fill.className = 'budget-fill';
        if (percent > 80) fill.classList.add('danger');
        else if (percent > 50) fill.classList.add('warning');
        
        text.textContent = limit > 0 
            ? `$${used.toFixed(2)} / $${limit.toFixed(2)}`
            : `$${used.toFixed(2)}`;
    },

    renderTodos() {
        let container = document.getElementById('todo-panel');
        
        if (!container) {
            const pipelineView = document.getElementById('pipeline-view');
            if (!pipelineView) return;
            
            container = document.createElement('div');
            container.id = 'todo-panel';
            container.className = 'todo-panel';
            pipelineView.parentElement.insertBefore(container, pipelineView);
        }
        
        if (!this.todos || this.todos.length === 0) {
            container.style.display = 'none';
            return;
        }
        
        container.style.display = 'block';
        
        const progress = this.todoProgress || {};
        const percent = progress.percent || 0;
        const completed = progress.completed || 0;
        const total = progress.total || this.todos.length;
        const inProgress = progress.in_progress || 0;
        
        const todoItems = this.todos.map(todo => {
            const statusClass = `todo-${todo.status}`;
            const statusIcon = this.getTodoStatusIcon(todo.status);
            const priorityClass = `priority-${todo.priority}`;
            
            return `
                <div class="todo-item ${statusClass} ${priorityClass}">
                    <span class="todo-status-icon">${statusIcon}</span>
                    <span class="todo-content">${this.escapeHtml(todo.content)}</span>
                    <span class="todo-priority">${todo.priority}</span>
                </div>
            `;
        }).join('');
        
        container.innerHTML = `
            <div class="todo-header">
                <div class="todo-title">
                    <span class="todo-icon">📋</span>
                    <span>Tasks</span>
                    <span class="todo-count">${completed}/${total}</span>
                </div>
                <div class="todo-progress-bar">
                    <div class="todo-progress-fill" style="width: ${percent}%"></div>
                </div>
                ${inProgress > 0 ? `<span class="todo-in-progress">${inProgress} in progress</span>` : ''}
            </div>
            <div class="todo-list">
                ${todoItems}
            </div>
        `;
    },
    
    getTodoStatusIcon(status) {
        const icons = {
            pending: '○',
            in_progress: '◐',
            completed: '●',
            cancelled: '◌'
        };
        return icons[status] || '○';
    },

    async runExecution(executionId) {
        this.setLoading(true);
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}/run`, { method: 'POST' });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || `HTTP ${response.status}`);
            }
            this.showToast('Workflow started', 'success');
            await this.selectExecution(executionId);
        } catch (error) {
            console.error('Failed to run execution:', error);
            this.showToast(`Failed to start workflow: ${error.message}`, 'error');
        } finally {
            this.setLoading(false);
        }
    },

    async cancelExecution(executionId) {
        if (!confirm('Cancel this workflow execution?')) return;
        
        this.setLoading(true);
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}/cancel`, { method: 'POST' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            this.showToast('Workflow cancelled', 'success');
            await this.selectExecution(executionId);
            await this.loadExecutions();
        } catch (error) {
            console.error('Failed to cancel execution:', error);
            this.showToast('Failed to cancel workflow', 'error');
        } finally {
            this.setLoading(false);
        }
    },

    async resumeExecution(executionId) {
        this.setLoading(true);
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}/resume`, { method: 'POST' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            this.showToast('Workflow resumed', 'success');
            await this.selectExecution(executionId);
        } catch (error) {
            console.error('Failed to resume execution:', error);
            this.showToast('Failed to resume workflow', 'error');
        } finally {
            this.setLoading(false);
        }
    },

    connectWebSocket(executionId) {
        if (this.wsReconnectTimeout) {
            clearTimeout(this.wsReconnectTimeout);
            this.wsReconnectTimeout = null;
        }
        
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
        }
        
        this.wsReconnectAttempts = 0;
        this._connectWebSocket(executionId);
    },
    
    _connectWebSocket(executionId) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${window.location.host}/api/workflow/ws/${executionId}`);

        this.ws.onopen = () => {
            this.wsReconnectAttempts = 0;
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleWebSocketMessage(data);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
        
        this.ws.onclose = (event) => {
            this.hideApprovalBanner();
            
            if (this.selectedExecution?.id === executionId && 
                this.wsReconnectAttempts < this.maxWsReconnectAttempts) {
                this.wsReconnectAttempts++;
                const delay = Math.min(1000 * Math.pow(2, this.wsReconnectAttempts), 30000);
                console.log(`WebSocket closed, reconnecting in ${delay}ms (attempt ${this.wsReconnectAttempts})`);
                this.wsReconnectTimeout = setTimeout(() => {
                    this._connectWebSocket(executionId);
                }, delay);
            }
        };
    },

    handleWebSocketMessage(data) {
        if (data.type === 'phase_update' || data.type === 'execution_update' || 
            data.type === 'status_update' || data.type === 'phase_complete') {
            this.selectExecution(this.selectedExecution.id);
            if (data.type === 'status_update' && data.status) {
                const idx = this.executions.findIndex(e => e.id === this.selectedExecution?.id);
                if (idx !== -1) {
                    this.executions[idx].status = data.status;
                    this.renderExecutionList();
                    this.updateStats();
                }
            }
        }
        if (data.type === 'init') {
            this.renderPipeline(data.execution, [], data.template_phases);
            if (data.pending_approval) {
                this.showApprovalBanner(data.pending_approval.message, data.pending_approval.timeout_seconds);
            }
            if (data.todos) {
                this.todos = data.todos;
                this.todoProgress = data.todo_progress || null;
                this.renderTodos();
            }
        }
        if (data.type === 'approval_needed') {
            this.showApprovalBanner(data.message, data.timeout_seconds);
            this.showBrowserNotification('Approval Required', data.message);
        }
        if (data.type === 'approval_resolved') {
            this.hideApprovalBanner();
            this.selectExecution(this.selectedExecution.id);
        }
        if (data.type === 'budget_update' && data.budget) {
            this.renderBudget(data.budget);
        }
        if (data.type === 'todo_update') {
            this.todos = data.todos || [];
            this.todoProgress = data.progress || null;
            this.renderTodos();
        }
    },
    
    showApprovalBanner(message, timeoutSeconds = 300) {
        this.pendingApproval = { message, timeoutSeconds, startedAt: Date.now() };
        
        if (this.approvalCountdownInterval) {
            clearInterval(this.approvalCountdownInterval);
        }
        
        let banner = document.getElementById('approval-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'approval-banner';
            banner.className = 'approval-banner';
            
            const pipelineHeader = document.getElementById('pipeline-header');
            if (pipelineHeader) {
                pipelineHeader.insertAdjacentElement('afterend', banner);
            }
        }
        
        const updateBanner = () => {
            const elapsed = (Date.now() - this.pendingApproval.startedAt) / 1000;
            const remaining = Math.max(0, timeoutSeconds - elapsed);
            const mins = Math.floor(remaining / 60);
            const secs = Math.floor(remaining % 60);
            const timeDisplay = remaining > 0 ? `${mins}:${secs.toString().padStart(2, '0')}` : 'Expired';
            
            banner.innerHTML = `
                <div class="approval-banner-content">
                    <div class="approval-icon">⚠️</div>
                    <div class="approval-message">
                        <strong>Approval Required</strong>
                        <span class="approval-timer">${timeDisplay}</span>
                        <p>${this.escapeHtml(message)}</p>
                    </div>
                    <div class="approval-actions">
                        <button class="btn btn-primary" onclick="workflowApp.respondToApproval(true)">Approve</button>
                        <button class="btn btn-danger" onclick="workflowApp.respondToApproval(false)">Reject</button>
                    </div>
                </div>
            `;
            
            if (remaining <= 0) {
                this.showToast('Approval request timed out', 'warning');
                clearInterval(this.approvalCountdownInterval);
                this.hideApprovalBanner();
            }
        };
        
        updateBanner();
        this.approvalCountdownInterval = setInterval(updateBanner, 1000);
        banner.style.display = 'flex';
    },
    
    hideApprovalBanner() {
        this.pendingApproval = null;
        if (this.approvalCountdownInterval) {
            clearInterval(this.approvalCountdownInterval);
            this.approvalCountdownInterval = null;
        }
        const banner = document.getElementById('approval-banner');
        if (banner) {
            banner.style.display = 'none';
        }
    },
    
    requestNotificationPermission() {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
    },
    
    showBrowserNotification(title, body) {
        if (!('Notification' in window)) return;
        if (Notification.permission !== 'granted') return;
        if (document.hasFocus()) return;
        
        const notification = new Notification(title, {
            body: body.substring(0, 100),
            icon: '/static/favicon.ico',
            tag: 'workflow-approval',
            requireInteraction: true
        });
        
        notification.onclick = () => {
            window.focus();
            notification.close();
        };
    },
    
    async respondToApproval(approved) {
        if (!this.selectedExecution) return;
        
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'approve',
                approved: approved
            }));
        } else {
            const endpoint = approved ? 'approve' : 'reject';
            try {
                await fetch(`/api/workflow/executions/${this.selectedExecution.id}/${endpoint}`, {
                    method: 'POST'
                });
            } catch (error) {
                console.error('Failed to respond to approval:', error);
                alert('Failed to respond to approval request');
                return;
            }
        }
        
        this.hideApprovalBanner();
    },

    showPhaseDetails(phaseId) {
        const phase = this.selectedExecution?.phase_executions?.find(p => p.id === phaseId);
        if (!phase) return;

        const detailsPanel = document.getElementById('workflow-details');
        const content = document.getElementById('details-content');
        
        const providerUsed = phase.provider_used || phase.provider_type || 'N/A';
        const modelUsed = phase.model_used || phase.model_name || 'N/A';
        const hasTokens = phase.tokens_input || phase.tokens_output;
        
        content.innerHTML = `
            <div class="detail-section">
                <div class="detail-section-title">Phase Info</div>
                <div class="detail-row">
                    <span class="detail-label">Name</span>
                    <span class="detail-value">${phase.phase_name || phase.name}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Role</span>
                    <span class="detail-value">${phase.phase_role || phase.role || 'N/A'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Status</span>
                    <span class="detail-value">${phase.status}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Provider</span>
                    <span class="detail-value">${providerUsed}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Model</span>
                    <span class="detail-value">${modelUsed}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Iteration</span>
                    <span class="detail-value">${phase.iteration || 1}</span>
                </div>
            </div>
            ${hasTokens ? `
            <div class="detail-section">
                <div class="detail-section-title">Usage</div>
                <div class="detail-row">
                    <span class="detail-label">Input Tokens</span>
                    <span class="detail-value">${phase.tokens_input || 0}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Output Tokens</span>
                    <span class="detail-value">${phase.tokens_output || 0}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Cost</span>
                    <span class="detail-value">$${(phase.cost_usd || 0).toFixed(4)}</span>
                </div>
            </div>` : ''}
            ${phase.started_at ? `
            <div class="detail-section">
                <div class="detail-section-title">Timing</div>
                <div class="detail-row">
                    <span class="detail-label">Started</span>
                    <span class="detail-value">${this.formatDate(phase.started_at)}</span>
                </div>
                ${phase.completed_at ? `
                <div class="detail-row">
                    <span class="detail-label">Completed</span>
                    <span class="detail-value">${this.formatDate(phase.completed_at)}</span>
                </div>` : ''}
            </div>` : ''}
            ${phase.error_message ? `
            <div class="detail-section">
                <div class="detail-section-title">Error</div>
                <pre style="color: var(--accent-red); font-size: 12px; white-space: pre-wrap;">${phase.error_message}</pre>
            </div>` : ''}
        `;

        detailsPanel.classList.add('open');
    },

    closeDetails() {
        document.getElementById('workflow-details').classList.remove('open');
    },

    async showArtifacts(executionId) {
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}/artifacts`);
            const data = await response.json();
            
            const detailsPanel = document.getElementById('workflow-details');
            const content = document.getElementById('details-content');
            
            if (!data.artifacts || data.artifacts.length === 0) {
                content.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">📄</div>
                        <p>No artifacts</p>
                    </div>
                `;
            } else {
                content.innerHTML = `
                    <div class="detail-section">
                        <div class="detail-section-title">Artifacts (${data.artifacts.length})</div>
                        <div class="artifact-list">
                            ${data.artifacts.map(a => `
                                <div class="artifact-item" onclick="workflowApp.viewArtifact('${a.id}')">
                                    <div class="artifact-name">${a.name || a.artifact_type}</div>
                                    <div class="artifact-type">${a.artifact_type} - ${a.phase_name || ''}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            detailsPanel.classList.add('open');
        } catch (error) {
            console.error('Failed to load artifacts:', error);
        }
    },

    async viewArtifact(artifactId) {
        try {
            const response = await fetch(`/api/workflow/artifacts/${artifactId}/content`);
            const data = await response.json();
            
            document.getElementById('artifact-title').textContent = `Artifact: ${artifactId}`;
            document.getElementById('artifact-content').innerHTML = `<pre>${this.escapeHtml(data.content || '')}</pre>`;
            document.getElementById('artifact-modal').classList.add('open');
        } catch (error) {
            console.error('Failed to load artifact:', error);
        }
    },

    closeArtifactModal() {
        document.getElementById('artifact-modal').classList.remove('open');
    },

    openNewWorkflowModal() {
        document.getElementById('new-workflow-modal').classList.add('open');
    },

    closeNewWorkflowModal() {
        document.getElementById('new-workflow-modal').classList.remove('open');
    },

    async createWorkflow() {
        const task = document.getElementById('workflow-task').value.trim();
        const path = document.getElementById('workflow-path').value.trim();
        const templateId = document.getElementById('workflow-template').value;
        const budget = document.getElementById('workflow-budget').value;
        const interactive = document.getElementById('workflow-interactive').checked;

        if (!task) {
            alert('Please enter a task description');
            return;
        }

        try {
            const response = await fetch('/api/workflow/executions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_description: task,
                    project_path: path,
                    template_id: templateId || null,
                    budget_limit: budget ? parseFloat(budget) : null,
                    interactive_mode: interactive
                })
            });

            const data = await response.json();
            
            if (data.success) {
                this.closeNewWorkflowModal();
                await this.loadExecutions();
                await this.selectExecution(data.execution.id);
                
                await fetch(`/api/workflow/executions/${data.execution.id}/run`, { method: 'POST' });
                await this.selectExecution(data.execution.id);
            } else {
                alert('Failed to create workflow: ' + (data.detail || 'Unknown error'));
            }
        } catch (error) {
            console.error('Failed to create workflow:', error);
            alert('Failed to create workflow');
        }
    },

    updateStats() {
        const total = this.executions.length;
        const running = this.executions.filter(e => e.status === 'running').length;
        
        document.getElementById('workflow-count').textContent = `${total} workflow${total !== 1 ? 's' : ''}`;
        document.getElementById('running-count').textContent = `${running} running`;
    },

    getStatusIcon(status) {
        const icons = {
            pending: '○',
            running: '◐',
            completed: '●',
            failed: '✕',
            skipped: '−',
            cancelled: '◌'
        };
        return icons[status] || '○';
    },

    formatDate(dateStr) {
        if (!dateStr) return '';
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    },

    formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    },

    truncate(str, len) {
        if (!str) return '';
        return str.length > len ? str.substring(0, len) + '...' : str;
    },

    renderMiniPipeline(exec) {
        const total = exec.phases_total || 0;
        const completed = exec.phases_completed || 0;
        if (total === 0) return '';
        
        const dots = [];
        for (let i = 0; i < Math.min(total, 8); i++) {
            let status = 'pending';
            if (i < completed) status = 'completed';
            else if (i === completed && exec.status === 'running') status = 'running';
            else if (exec.status === 'failed' && i === completed) status = 'failed';
            dots.push(`<span class="mini-phase mini-phase-${status}"></span>`);
        }
        if (total > 8) {
            dots.push(`<span class="mini-phase-more">+${total - 8}</span>`);
        }
        return `<div class="mini-pipeline">${dots.join('')}</div>`;
    },

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    currentBrowsePath: '~',

    async openBrowseModal() {
        document.getElementById('browse-modal').classList.add('open');
        await this.browseTo('~');
    },

    closeBrowseModal() {
        document.getElementById('browse-modal').classList.remove('open');
    },

    async browseTo(path) {
        try {
            const response = await fetch(`/api/browse-dirs?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            
            if (data.error) {
                console.error('Browse error:', data.error);
            }
            
            this.currentBrowsePath = data.path;
            document.getElementById('browse-current-path').textContent = data.path;
            
            const list = document.getElementById('browse-list');
            
            if (data.dirs.length === 0) {
                list.innerHTML = '<div class="browse-empty">No subdirectories</div>';
                return;
            }
            
            list.innerHTML = data.dirs.map((dir, index) => `
                <div class="browse-item" data-path-index="${index}">
                    <span class="browse-item-icon">${dir.is_git ? '📁' : '📂'}</span>
                    <span class="browse-item-name">${this.escapeHtml(dir.name)}</span>
                    ${dir.is_git ? '<span class="browse-item-git">git</span>' : ''}
                </div>
            `).join('');
            
            this._browseDirs = data.dirs;
            list.querySelectorAll('.browse-item').forEach(item => {
                item.addEventListener('dblclick', () => {
                    const idx = parseInt(item.dataset.pathIndex);
                    if (this._browseDirs[idx]) {
                        this.browseTo(this._browseDirs[idx].path);
                    }
                });
            });
        } catch (error) {
            console.error('Failed to browse:', error);
        }
    },

    async browseUp() {
        const response = await fetch(`/api/browse-dirs?path=${encodeURIComponent(this.currentBrowsePath)}`);
        const data = await response.json();
        if (data.parent) {
            await this.browseTo(data.parent);
        }
    },

    selectCurrentPath() {
        document.getElementById('workflow-path').value = this.currentBrowsePath;
        this.closeBrowseModal();
    },

    async openTemplateModal() {
        document.getElementById('template-modal').classList.add('open');
        await this.loadProviders();
        this.renderTemplateList();
    },

    closeTemplateModal() {
        document.getElementById('template-modal').classList.remove('open');
        this.editingTemplate = null;
        this.editingPhases = [];
    },

    async loadProviders() {
        try {
            const response = await fetch('/api/workflow/providers');
            const data = await response.json();
            this.providers = data.providers || {};
            
            const localResponse = await fetch('/api/workflow/providers/detect');
            const localData = await localResponse.json();
            
            if (localData.ollama?.available) {
                this.providers.ollama = { available: true, models_count: localData.ollama.models?.length || 0 };
                this.providerModels.ollama = localData.ollama.models || [];
            }
            if (localData.lm_studio?.available) {
                this.providers.lm_studio = { available: true, models_count: localData.lm_studio.models?.length || 0 };
                this.providerModels.lm_studio = localData.lm_studio.models || [];
            }
            
            const oauthResponse = await fetch('/api/workflow/oauth/status');
            const oauthData = await oauthResponse.json();
            this.oauthStatus = oauthData.providers || {};
            
            if (this.oauthStatus.google?.status === 'connected') {
                this.providers.gemini_oauth = { available: true, oauth: true };
            } else {
                this.providers.gemini_oauth = { available: false, oauth: true, status: this.oauthStatus.google?.status || 'not_configured' };
            }
        } catch (error) {
            console.error('Failed to load providers:', error);
        }
    },

    async loadModelsForProvider(providerType) {
        if (this.providerModels[providerType]) {
            return this.providerModels[providerType];
        }
        
        try {
            const response = await fetch(`/api/workflow/providers/${providerType}/models`);
            const data = await response.json();
            this.providerModels[providerType] = data.models || [];
            return this.providerModels[providerType];
        } catch (error) {
            console.error(`Failed to load models for ${providerType}:`, error);
            return [];
        }
    },

    renderTemplateList() {
        const container = document.getElementById('template-list');
        
        if (this.templates.length === 0) {
            container.innerHTML = '<div class="template-list-empty">No templates yet</div>';
            return;
        }
        
        container.innerHTML = this.templates.map(t => `
            <div class="template-list-item ${this.editingTemplate?.id === t.id ? 'active' : ''}" 
                 onclick="workflowApp.editTemplate('${t.id}')">
                <div class="template-list-name">${t.name}</div>
                <div class="template-list-meta">${t.phases?.length || 0} phases ${t.is_default ? '★' : ''}</div>
            </div>
        `).join('');
    },

    newTemplate() {
        this.editingTemplate = {
            id: null,
            name: '',
            description: '',
            max_iterations: 3,
            phases: []
        };
        this.editingPhases = [];
        this.renderTemplateForm();
    },

    async editTemplate(templateId) {
        const template = this.templates.find(t => t.id === templateId);
        if (!template) return;
        
        this.editingTemplate = { ...template };
        this.editingPhases = (template.phases || []).map((p, i) => ({
            name: p.name || `Phase ${i + 1}`,
            provider_type: p.provider_config?.provider_type || p.provider_type || '',
            model_name: p.provider_config?.model_name || p.model_name || '',
            role: p.role || 'analyzer',
            temperature: p.provider_config?.temperature || 0.1,
            output_type: p.output_artifact_type || 'custom',
            can_iterate: p.can_iterate || false,
            prompt_template: p.prompt_template || ''
        }));
        
        this.renderTemplateForm();
        this.renderTemplateList();
    },

    renderTemplateForm() {
        const container = document.getElementById('template-editor-main');
        const templateHTML = document.getElementById('template-form-template').innerHTML;
        container.innerHTML = templateHTML;
        
        document.getElementById('template-name').value = this.editingTemplate.name || '';
        document.getElementById('template-description').value = this.editingTemplate.description || '';
        document.getElementById('template-iterations').value = this.editingTemplate.max_iterations || 3;
        
        const deleteBtn = document.getElementById('delete-template-btn');
        if (this.editingTemplate.id) {
            deleteBtn.style.display = 'block';
        }
        
        this.renderPhasesList();
    },

    renderPhasesList() {
        const container = document.getElementById('phases-list');
        
        if (this.editingPhases.length === 0) {
            container.innerHTML = '<div class="phases-empty">No phases yet. Add a phase to get started.</div>';
            return;
        }
        
        container.innerHTML = '';
        
        this.editingPhases.forEach((phase, index) => {
            const phaseHTML = document.getElementById('phase-card-template').innerHTML;
            const div = document.createElement('div');
            div.innerHTML = phaseHTML;
            const card = div.firstElementChild;
            
            card.dataset.phaseIndex = index;
            card.querySelector('.phase-order-badge').textContent = `#${index + 1}`;
            card.querySelector('.phase-name-input').value = phase.name || '';
            card.querySelector('.phase-role').value = phase.role || 'analyzer';
            card.querySelector('.phase-temperature').value = phase.temperature || 0.1;
            card.querySelector('.phase-output-type').value = phase.output_type || 'custom';
            card.querySelector('.phase-can-iterate').checked = phase.can_iterate || false;
            card.querySelector('.phase-prompt').value = phase.prompt_template || '';
            
            this.populateProviderSelect(card.querySelector('.phase-provider'), phase.provider_type);
            
            if (phase.provider_type) {
                this.populateModelSelect(card.querySelector('.phase-model'), phase.provider_type, phase.model_name);
            }
            
            container.appendChild(card);
        });
    },

    populateProviderSelect(select, selectedValue) {
        select.innerHTML = '<option value="">Select provider...</option>';
        
        const providerLabels = {
            claude_code: 'Claude Code (CLI)',
            gemini_sdk: 'Gemini (API Key)',
            gemini_oauth: 'Gemini (OAuth)',
            openai: 'OpenAI',
            openrouter: 'OpenRouter',
            ollama: 'Ollama (Local)',
            lm_studio: 'LM Studio (Local)'
        };
        
        const providerOrder = ['claude_code', 'gemini_sdk', 'gemini_oauth', 'openai', 'openrouter', 'ollama', 'lm_studio'];
        
        providerOrder.forEach(provider => {
            const info = this.providers[provider];
            const option = document.createElement('option');
            option.value = provider;
            option.textContent = providerLabels[provider] || provider;
            
            if (info && !info.available && provider !== 'claude_code') {
                option.disabled = true;
                if (info.oauth) {
                    option.textContent += ' (not authenticated)';
                } else {
                    option.textContent += ' (not configured)';
                }
            }
            
            if (provider === selectedValue) {
                option.selected = true;
            }
            
            select.appendChild(option);
        });
    },

    async populateModelSelect(select, providerType, selectedValue) {
        select.innerHTML = '<option value="">Loading models...</option>';
        
        if (providerType === 'claude_code') {
            select.innerHTML = '<option value="claude-code">Claude Code (via CLI)</option>';
            return;
        }
        
        const models = await this.loadModelsForProvider(providerType);
        
        select.innerHTML = '<option value="">Select model...</option>';
        
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model.model_id || model.model_name;
            option.textContent = model.model_name || model.model_id;
            
            if (model.cost_input_per_1k) {
                option.textContent += ` ($${model.cost_input_per_1k}/1k)`;
            }
            
            if ((model.model_id || model.model_name) === selectedValue) {
                option.selected = true;
            }
            
            select.appendChild(option);
        });
    },

    async onProviderChange(selectElement) {
        const card = selectElement.closest('.phase-edit-card');
        const modelSelect = card.querySelector('.phase-model');
        const providerType = selectElement.value;
        
        if (!providerType) {
            modelSelect.innerHTML = '<option value="">Select model...</option>';
            return;
        }
        
        await this.populateModelSelect(modelSelect, providerType, '');
    },

    addPhase() {
        this.editingPhases.push({
            name: `Phase ${this.editingPhases.length + 1}`,
            provider_type: '',
            model_name: '',
            role: 'analyzer',
            temperature: 0.1,
            output_type: 'custom',
            can_iterate: false,
            prompt_template: ''
        });
        this.renderPhasesList();
    },

    removePhase(button) {
        const card = button.closest('.phase-edit-card');
        const index = parseInt(card.dataset.phaseIndex);
        this.editingPhases.splice(index, 1);
        this.renderPhasesList();
    },

    movePhaseUp(button) {
        const card = button.closest('.phase-edit-card');
        const index = parseInt(card.dataset.phaseIndex);
        if (index === 0) return;
        
        this.collectPhaseData();
        [this.editingPhases[index], this.editingPhases[index - 1]] = 
            [this.editingPhases[index - 1], this.editingPhases[index]];
        this.renderPhasesList();
    },

    movePhaseDown(button) {
        const card = button.closest('.phase-edit-card');
        const index = parseInt(card.dataset.phaseIndex);
        if (index >= this.editingPhases.length - 1) return;
        
        this.collectPhaseData();
        [this.editingPhases[index], this.editingPhases[index + 1]] = 
            [this.editingPhases[index + 1], this.editingPhases[index]];
        this.renderPhasesList();
    },

    collectPhaseData() {
        const cards = document.querySelectorAll('.phase-edit-card');
        cards.forEach((card, index) => {
            this.editingPhases[index] = {
                name: card.querySelector('.phase-name-input').value,
                provider_type: card.querySelector('.phase-provider').value,
                model_name: card.querySelector('.phase-model').value,
                role: card.querySelector('.phase-role').value,
                temperature: parseFloat(card.querySelector('.phase-temperature').value) || 0.1,
                output_type: card.querySelector('.phase-output-type').value,
                can_iterate: card.querySelector('.phase-can-iterate').checked,
                prompt_template: card.querySelector('.phase-prompt').value
            };
        });
    },

    async saveTemplate() {
        const name = document.getElementById('template-name').value.trim();
        const description = document.getElementById('template-description').value.trim();
        const maxIterations = parseInt(document.getElementById('template-iterations').value) || 3;
        
        if (!name) {
            alert('Please enter a template name');
            return;
        }
        
        this.collectPhaseData();
        
        if (this.editingPhases.length === 0) {
            alert('Please add at least one phase');
            return;
        }
        
        for (let i = 0; i < this.editingPhases.length; i++) {
            const phase = this.editingPhases[i];
            if (!phase.provider_type) {
                alert(`Please select a provider for Phase ${i + 1}`);
                return;
            }
            if (!phase.model_name && phase.provider_type !== 'claude_code') {
                alert(`Please select a model for Phase ${i + 1}`);
                return;
            }
        }
        
        const templateData = {
            name,
            description,
            max_iterations: maxIterations,
            phases: this.editingPhases.map((p, i) => ({
                name: p.name || `Phase ${i + 1}`,
                role: p.role,
                provider_config: {
                    provider_type: p.provider_type,
                    model_name: p.model_name || (p.provider_type === 'claude_code' ? 'claude-code' : ''),
                    temperature: p.temperature
                },
                prompt_template: p.prompt_template,
                output_artifact_type: p.output_type,
                can_iterate: p.can_iterate,
                order: i
            }))
        };
        
        try {
            let response;
            if (this.editingTemplate.id) {
                response = await fetch(`/api/workflow/templates/${this.editingTemplate.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(templateData)
                });
            } else {
                response = await fetch('/api/workflow/templates', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(templateData)
                });
            }
            
            const data = await response.json();
            
            if (data.success || data.template_id) {
                await this.loadTemplates();
                this.renderTemplateList();
                this.populateTemplateSelect();
                
                if (data.template_id || data.template?.id) {
                    this.editTemplate(data.template_id || data.template.id);
                }
                
                alert('Template saved successfully!');
            } else {
                alert('Failed to save template: ' + (data.detail || 'Unknown error'));
            }
        } catch (error) {
            console.error('Failed to save template:', error);
            alert('Failed to save template');
        }
    },

    async deleteCurrentTemplate() {
        if (!this.editingTemplate?.id) return;
        
        if (!confirm(`Are you sure you want to delete "${this.editingTemplate.name}"?`)) {
            return;
        }
        
        try {
            const response = await fetch(`/api/workflow/templates/${this.editingTemplate.id}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                await this.loadTemplates();
                this.editingTemplate = null;
                this.editingPhases = [];
                this.renderTemplateList();
                this.populateTemplateSelect();
                
                document.getElementById('template-editor-main').innerHTML = `
                    <div class="template-placeholder">
                        <div class="placeholder-icon">📋</div>
                        <p>Select a template to edit or create a new one</p>
                    </div>
                `;
            } else {
                alert('Failed to delete template');
            }
        } catch (error) {
            console.error('Failed to delete template:', error);
            alert('Failed to delete template');
        }
    },

    openOAuthModal() {
        document.getElementById('oauth-modal').classList.add('open');
        this.refreshOAuthStatus();
    },

    closeOAuthModal() {
        document.getElementById('oauth-modal').classList.remove('open');
    },

    async refreshOAuthStatus() {
        try {
            const response = await fetch('/api/workflow/oauth/status');
            const data = await response.json();
            this.oauthStatus = data.providers || {};
            this.updateOAuthUI();
        } catch (error) {
            console.error('Failed to refresh OAuth status:', error);
        }
    },

    updateOAuthUI() {
        const googleStatus = this.oauthStatus.google || {};
        const statusBadge = document.getElementById('oauth-google-status');
        const configStatus = document.getElementById('oauth-google-config-status');
        const configDeleteBtn = document.getElementById('oauth-google-config-delete');
        const connectBtn = document.getElementById('oauth-google-connect-btn');
        const disconnectBtn = document.getElementById('oauth-google-disconnect-btn');
        const accountInfo = document.getElementById('oauth-google-account');
        const emailSpan = document.getElementById('oauth-google-email');

        const hasConfig = googleStatus.has_client_config;
        const status = googleStatus.status || 'not_configured';

        if (status === 'connected') {
            statusBadge.textContent = 'Connected';
            statusBadge.className = 'oauth-status-badge status-connected';
        } else if (status === 'expired') {
            statusBadge.textContent = 'Expired';
            statusBadge.className = 'oauth-status-badge status-expired';
        } else if (hasConfig) {
            statusBadge.textContent = 'Not Connected';
            statusBadge.className = 'oauth-status-badge status-pending';
        } else {
            statusBadge.textContent = 'Not Configured';
            statusBadge.className = 'oauth-status-badge status-not-configured';
        }

        if (hasConfig) {
            configStatus.innerHTML = '<span class="oauth-config-icon">●</span><span>Client config uploaded</span>';
            configStatus.classList.add('configured');
            configDeleteBtn.style.display = 'inline-block';
            connectBtn.disabled = false;
        } else {
            configStatus.innerHTML = '<span class="oauth-config-icon">○</span><span>No client config uploaded</span>';
            configStatus.classList.remove('configured');
            configDeleteBtn.style.display = 'none';
            connectBtn.disabled = true;
        }

        if (status === 'connected') {
            connectBtn.style.display = 'none';
            disconnectBtn.style.display = 'inline-block';
            accountInfo.style.display = 'flex';
            emailSpan.textContent = googleStatus.email || 'Connected';
        } else {
            connectBtn.style.display = 'inline-block';
            disconnectBtn.style.display = 'none';
            accountInfo.style.display = 'none';
        }
    },

    async uploadOAuthConfig(provider, input) {
        const file = input.files[0];
        if (!file) return;

        try {
            const content = await file.text();
            const config = JSON.parse(content);
            
            const response = await fetch(`/api/workflow/oauth/${provider}/client-config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ config })
            });

            const data = await response.json();
            
            if (data.success) {
                await this.refreshOAuthStatus();
                await this.loadProviders();
            } else {
                alert('Failed to upload config: ' + (data.detail || 'Invalid config file'));
            }
        } catch (error) {
            console.error('Failed to upload OAuth config:', error);
            alert('Failed to parse config file. Make sure it\'s a valid JSON file.');
        }
        
        input.value = '';
    },

    async deleteOAuthConfig(provider) {
        if (!confirm('Delete OAuth client config? This will also disconnect your account.')) {
            return;
        }

        try {
            await fetch(`/api/workflow/oauth/${provider}/revoke`, { method: 'DELETE' });
            await fetch(`/api/workflow/oauth/${provider}/client-config`, { method: 'DELETE' });
            await this.refreshOAuthStatus();
            await this.loadProviders();
        } catch (error) {
            console.error('Failed to delete OAuth config:', error);
        }
    },

    async startOAuthFlow(provider) {
        const connectBtn = document.getElementById(`oauth-${provider}-connect-btn`);
        const originalText = connectBtn.textContent;
        connectBtn.textContent = 'Opening browser...';
        connectBtn.disabled = true;

        try {
            const response = await fetch(`/api/workflow/oauth/${provider}/start`, {
                method: 'POST'
            });

            const data = await response.json();
            
            if (data.success) {
                connectBtn.textContent = 'Waiting for authorization...';
                
                const checkAuth = setInterval(async () => {
                    await this.refreshOAuthStatus();
                    const status = this.oauthStatus[provider]?.status;
                    if (status === 'connected') {
                        clearInterval(checkAuth);
                        await this.loadProviders();
                        alert('Successfully connected!');
                    }
                }, 2000);

                setTimeout(() => {
                    clearInterval(checkAuth);
                    this.updateOAuthUI();
                }, 120000);
            } else {
                alert('Failed to start OAuth flow: ' + (data.detail || 'Unknown error'));
                connectBtn.textContent = originalText;
                connectBtn.disabled = false;
            }
        } catch (error) {
            console.error('Failed to start OAuth flow:', error);
            alert('Failed to start OAuth flow');
            connectBtn.textContent = originalText;
            connectBtn.disabled = false;
        }
    },

    async revokeOAuth(provider) {
        if (!confirm('Disconnect your account? You can reconnect later.')) {
            return;
        }

        try {
            const response = await fetch(`/api/workflow/oauth/${provider}/revoke`, {
                method: 'DELETE'
            });

            if (response.ok) {
                await this.refreshOAuthStatus();
                await this.loadProviders();
            } else {
                alert('Failed to disconnect');
            }
        } catch (error) {
            console.error('Failed to revoke OAuth:', error);
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    workflowApp.init();
});
