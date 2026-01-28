// Settings Page JavaScript

// ==================== Tab Navigation ====================

function switchTab(tabName) {
    // Update nav items
    document.querySelectorAll('.settings-nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.settings-tab').forEach(tab => {
        tab.classList.toggle('active', tab.id === `tab-${tabName}`);
    });

    // Load data for the tab
    switch(tabName) {
        case 'daemon':
            loadDaemonStatus();
            break;
        case 'scheduler':
            loadSchedulerData();
            break;
        case 'notifications':
            loadNotifications();
            break;
        case 'webhooks':
            loadWebhooks();
            break;
        case 'telegram':
            loadTelegramStatus();
            break;
        case 'system':
            loadSystemInfo();
            break;
    }
}

// ==================== Daemon Mode ====================

async function loadDaemonStatus() {
    try {
        const response = await fetch('/api/daemon/status');
        const data = await response.json();

        updateDaemonUI(data);
        refreshDaemonLogs();
    } catch (error) {
        console.error('Error loading daemon status:', error);
    }
}

function updateDaemonUI(status) {
    // Update header status
    const headerDot = document.getElementById('daemon-status-dot');
    const headerText = document.getElementById('daemon-status-text');

    if (status.running) {
        headerDot.className = 'status-dot running';
        headerText.textContent = 'Running';
    } else if (status.installed) {
        headerDot.className = 'status-dot stopped';
        headerText.textContent = 'Stopped';
    } else {
        headerDot.className = 'status-dot';
        headerText.textContent = 'Not Installed';
    }

    // Update card details
    document.getElementById('daemon-platform').textContent = status.platform || '-';
    document.getElementById('daemon-service-type').textContent = status.service_type || '-';
    document.getElementById('daemon-installed').textContent = status.installed ? 'Yes' : 'No';
    document.getElementById('daemon-running').textContent = status.running ? 'Yes' : 'No';
    document.getElementById('daemon-uptime').textContent = status.uptime || '-';

    // Update badge
    const badge = document.getElementById('daemon-badge');
    if (status.running) {
        badge.textContent = 'Running';
        badge.className = 'status-badge running';
    } else if (status.installed) {
        badge.textContent = 'Stopped';
        badge.className = 'status-badge stopped';
    } else {
        badge.textContent = 'Not Installed';
        badge.className = 'status-badge not-installed';
    }

    // Update buttons
    document.getElementById('daemon-install-btn').disabled = status.installed;
    document.getElementById('daemon-uninstall-btn').disabled = !status.installed;
    document.getElementById('daemon-start-btn').disabled = !status.installed || status.running;
    document.getElementById('daemon-stop-btn').disabled = !status.running;
    document.getElementById('daemon-restart-btn').disabled = !status.running;
}

async function installDaemon() {
    if (!confirm('Install Autowrkers as a system service?')) return;

    try {
        const response = await fetch('/api/daemon/install', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            alert('Service installed successfully! You can now start it.');
        } else {
            alert('Error: ' + data.message);
        }
        loadDaemonStatus();
    } catch (error) {
        alert('Error installing service: ' + error.message);
    }
}

async function uninstallDaemon() {
    if (!confirm('Uninstall the Autowrkers service? This will stop 24/7 operation.')) return;

    try {
        const response = await fetch('/api/daemon/uninstall', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            alert('Service uninstalled successfully.');
        } else {
            alert('Error: ' + data.message);
        }
        loadDaemonStatus();
    } catch (error) {
        alert('Error uninstalling service: ' + error.message);
    }
}

async function startDaemon() {
    try {
        const response = await fetch('/api/daemon/start', { method: 'POST' });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + data.message);
        }
        loadDaemonStatus();
    } catch (error) {
        alert('Error starting service: ' + error.message);
    }
}

async function stopDaemon() {
    if (!confirm('Stop the Autowrkers service?')) return;

    try {
        const response = await fetch('/api/daemon/stop', { method: 'POST' });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + data.message);
        }
        loadDaemonStatus();
    } catch (error) {
        alert('Error stopping service: ' + error.message);
    }
}

async function restartDaemon() {
    try {
        const response = await fetch('/api/daemon/restart', { method: 'POST' });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + data.message);
        }
        loadDaemonStatus();
    } catch (error) {
        alert('Error restarting service: ' + error.message);
    }
}

async function refreshDaemonLogs() {
    try {
        const lines = document.getElementById('log-lines').value;
        const response = await fetch(`/api/daemon/logs?lines=${lines}`);
        const data = await response.json();

        document.getElementById('daemon-logs').textContent = data.logs || 'No logs available';
    } catch (error) {
        document.getElementById('daemon-logs').textContent = 'Error loading logs: ' + error.message;
    }
}

// ==================== Scheduler ====================

async function loadSchedulerData() {
    try {
        const response = await fetch('/api/scheduler/status');
        const data = await response.json();

        // Update status
        const statusDot = document.getElementById('scheduler-status-dot');
        const statusText = document.getElementById('scheduler-status-text');

        if (data.running) {
            statusDot.className = 'status-dot running';
            statusText.textContent = 'Running';
        } else {
            statusDot.className = 'status-dot stopped';
            statusText.textContent = 'Stopped';
        }

        document.getElementById('active-task-count').textContent = `${data.active_tasks} active tasks`;

        // Render tasks
        renderTasks(data.tasks || []);

        // Load projects for the dropdown
        loadProjectsForDropdown();
    } catch (error) {
        console.error('Error loading scheduler data:', error);
    }
}

function renderTasks(tasks) {
    const container = document.getElementById('tasks-list');

    if (tasks.length === 0) {
        container.innerHTML = '<div class="empty-state-small">No scheduled tasks</div>';
        return;
    }

    container.innerHTML = tasks.map(task => `
        <div class="task-card ${task.enabled ? '' : 'disabled'} ${task.id.startsWith('global_') ? 'global' : ''}">
            <div class="task-info">
                <div class="task-name">${escapeHtml(task.name)}</div>
                <div class="task-details">
                    <span>Type: ${task.task_type}</span>
                    <span>Schedule: ${escapeHtml(task.schedule)}</span>
                    ${task.last_run ? `<span>Last run: ${formatDate(task.last_run)}</span>` : ''}
                    ${task.next_run ? `<span>Next run: ${formatDate(task.next_run)}</span>` : ''}
                </div>
            </div>
            <div class="task-actions">
                <button class="btn btn-small" onclick="runTaskNow('${task.id}')">Run Now</button>
                ${task.enabled
                    ? `<button class="btn btn-small" onclick="toggleTask('${task.id}', false)">Disable</button>`
                    : `<button class="btn btn-small btn-primary" onclick="toggleTask('${task.id}', true)">Enable</button>`
                }
                ${!task.id.startsWith('global_')
                    ? `<button class="btn btn-small btn-danger" onclick="deleteTask('${task.id}')">Delete</button>`
                    : ''
                }
            </div>
        </div>
    `).join('');
}

async function loadProjectsForDropdown() {
    try {
        const response = await fetch('/api/projects');
        const data = await response.json();

        const select = document.getElementById('task-project');
        select.innerHTML = '<option value="">All Projects</option>';

        (data.projects || []).forEach(project => {
            const option = document.createElement('option');
            option.value = project.id;
            option.textContent = project.name;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading projects:', error);
    }
}

async function handleAddTask(event) {
    event.preventDefault();

    const taskData = {
        name: document.getElementById('task-name').value,
        task_type: document.getElementById('task-type').value,
        schedule: document.getElementById('task-schedule').value,
        enabled: true,
        project_id: document.getElementById('task-project').value || null,
    };

    try {
        const response = await fetch('/api/scheduler/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(taskData)
        });

        const data = await response.json();

        if (data.success) {
            document.getElementById('add-task-form').reset();
            loadSchedulerData();
        } else {
            alert('Error: ' + (data.detail || 'Failed to create task'));
        }
    } catch (error) {
        alert('Error creating task: ' + error.message);
    }
}

async function runTaskNow(taskId) {
    try {
        const response = await fetch(`/api/scheduler/tasks/${taskId}/run`, { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            alert('Task triggered!');
        } else {
            alert('Error: ' + (data.detail || 'Failed to run task'));
        }
    } catch (error) {
        alert('Error running task: ' + error.message);
    }
}

async function toggleTask(taskId, enable) {
    try {
        const endpoint = enable ? 'enable' : 'disable';
        const response = await fetch(`/api/scheduler/tasks/${taskId}/${endpoint}`, { method: 'PUT' });
        const data = await response.json();

        if (data.success) {
            loadSchedulerData();
        } else {
            alert('Error: ' + (data.detail || 'Failed to toggle task'));
        }
    } catch (error) {
        alert('Error toggling task: ' + error.message);
    }
}

async function deleteTask(taskId) {
    if (!confirm('Delete this task?')) return;

    try {
        const response = await fetch(`/api/scheduler/tasks/${taskId}`, { method: 'DELETE' });
        const data = await response.json();

        if (data.success) {
            loadSchedulerData();
        } else {
            alert('Error: ' + (data.detail || 'Failed to delete task'));
        }
    } catch (error) {
        alert('Error deleting task: ' + error.message);
    }
}

// ==================== Notifications ====================

async function loadNotifications() {
    try {
        const [configsRes, logRes] = await Promise.all([
            fetch('/api/notifications/configs'),
            fetch('/api/notifications/log?limit=50')
        ]);

        const configs = await configsRes.json();
        const log = await logRes.json();

        renderNotificationConfigs(configs.configs || []);
        renderNotificationLog(log.notifications || []);
    } catch (error) {
        console.error('Error loading notifications:', error);
    }
}

function renderNotificationConfigs(configs) {
    const container = document.getElementById('notification-configs');

    if (configs.length === 0) {
        container.innerHTML = '<div class="empty-state-small">No notification channels configured</div>';
        return;
    }

    const channelIcons = {
        discord: '💬',
        slack: '💼',
        telegram: '✈️',
        email: '📧',
        desktop: '🖥️'
    };

    container.innerHTML = configs.map(config => `
        <div class="notification-card ${config.enabled ? '' : 'disabled'}">
            <div class="notification-header">
                <div class="notification-title">
                    <div class="channel-icon ${config.channel}">${channelIcons[config.channel] || '🔔'}</div>
                    <div>
                        <div class="notification-name">${escapeHtml(config.name)}</div>
                        <div class="notification-channel">${config.channel}</div>
                    </div>
                </div>
                <span class="status-badge ${config.enabled ? 'running' : 'stopped'}">
                    ${config.enabled ? 'Active' : 'Disabled'}
                </span>
            </div>
            <div class="notification-events">
                ${(config.events || []).map(e => `<span class="event-tag">${e}</span>`).join('')}
            </div>
            <div class="notification-actions">
                <button class="btn btn-small" onclick="testNotification('${config.id}')">Test</button>
                <button class="btn btn-small btn-danger" onclick="deleteNotification('${config.id}')">Delete</button>
            </div>
        </div>
    `).join('');
}

function renderNotificationLog(notifications) {
    const container = document.getElementById('notification-log');

    if (notifications.length === 0) {
        container.innerHTML = '<div class="empty-state-small">No notifications sent yet</div>';
        return;
    }

    container.innerHTML = notifications.map(n => `
        <div class="log-entry">
            <div class="log-info">
                <span class="event-tag">${n.event || 'unknown'}</span>
                <span>${escapeHtml(n.title || '')}</span>
            </div>
            <span class="log-time">${formatDate(n.created_at)}</span>
        </div>
    `).join('');
}

function showAddNotificationModal() {
    document.getElementById('add-notification-modal').classList.add('active');
}

function closeAddNotificationModal() {
    document.getElementById('add-notification-modal').classList.remove('active');
    document.getElementById('add-notification-form').reset();
    toggleNotificationSettings();
}

function toggleNotificationSettings() {
    const channel = document.getElementById('notif-channel').value;

    document.getElementById('webhook-settings').style.display =
        (channel === 'discord' || channel === 'slack') ? 'block' : 'none';
    document.getElementById('telegram-settings').style.display =
        channel === 'telegram' ? 'block' : 'none';
    document.getElementById('email-settings').style.display =
        channel === 'email' ? 'block' : 'none';

    // Update hint text
    if (channel === 'discord') {
        document.getElementById('webhook-hint').textContent = 'Create a webhook in your Discord server settings';
    } else if (channel === 'slack') {
        document.getElementById('webhook-hint').textContent = 'Create an Incoming Webhook in your Slack workspace';
    }
}

async function handleAddNotification(event) {
    event.preventDefault();

    const channel = document.getElementById('notif-channel').value;
    const events = Array.from(document.querySelectorAll('input[name="notif-events"]:checked'))
        .map(cb => cb.value);

    const configData = {
        name: document.getElementById('notif-name').value,
        channel: channel,
        enabled: true,
        events: events,
    };

    if (channel === 'discord' || channel === 'slack') {
        configData.webhook_url = document.getElementById('notif-webhook-url').value;
    } else if (channel === 'telegram') {
        configData.bot_token = document.getElementById('notif-bot-token').value;
        configData.chat_id = document.getElementById('notif-chat-id').value;
    } else if (channel === 'email') {
        configData.smtp_host = document.getElementById('notif-smtp-host').value;
        configData.smtp_port = parseInt(document.getElementById('notif-smtp-port').value);
        configData.smtp_user = document.getElementById('notif-smtp-user').value;
        configData.smtp_password = document.getElementById('notif-smtp-password').value;
        configData.smtp_from = document.getElementById('notif-smtp-from').value;
        configData.smtp_to = document.getElementById('notif-smtp-to').value.split(',').map(s => s.trim());
        configData.smtp_use_tls = document.getElementById('notif-smtp-tls').checked;
    }

    try {
        const response = await fetch('/api/notifications/configs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData)
        });

        const data = await response.json();

        if (data.success) {
            closeAddNotificationModal();
            loadNotifications();
        } else {
            alert('Error: ' + (data.detail || 'Failed to create notification'));
        }
    } catch (error) {
        alert('Error creating notification: ' + error.message);
    }
}

async function testNotification(configId) {
    try {
        const response = await fetch(`/api/notifications/configs/${configId}/test`, { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            alert('Test notification sent!');
        } else {
            alert('Failed to send test notification');
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

async function deleteNotification(configId) {
    if (!confirm('Delete this notification channel?')) return;

    try {
        const response = await fetch(`/api/notifications/configs/${configId}`, { method: 'DELETE' });
        const data = await response.json();

        if (data.success) {
            loadNotifications();
        } else {
            alert('Error: ' + (data.detail || 'Failed to delete notification'));
        }
    } catch (error) {
        alert('Error deleting notification: ' + error.message);
    }
}

// ==================== Webhooks ====================

async function loadWebhooks() {
    try {
        const response = await fetch('/api/webhooks/status');
        const data = await response.json();

        document.getElementById('webhook-enabled-count').textContent = data.enabled_projects || 0;
        document.getElementById('webhook-event-count').textContent = data.total_events || 0;

        // Set webhook URL
        const baseUrl = window.location.origin;
        document.getElementById('webhook-url').textContent = `${baseUrl}/webhooks/github`;

        // Render recent events
        renderWebhookEvents(data.recent_events || []);
    } catch (error) {
        console.error('Error loading webhooks:', error);
    }
}

function renderWebhookEvents(events) {
    const container = document.getElementById('webhook-events-list');

    if (events.length === 0) {
        container.innerHTML = '<div class="empty-state-small">No webhook events received yet</div>';
        return;
    }

    container.innerHTML = events.map(event => `
        <div class="event-item">
            <div>
                <span class="event-type">${event.event_type || 'unknown'}</span>
                <span class="event-source">${event.source || ''}</span>
            </div>
            <span class="event-time">${formatDate(event.created_at)}</span>
        </div>
    `).join('');
}

function copyWebhookUrl() {
    const url = document.getElementById('webhook-url').textContent;
    navigator.clipboard.writeText(url).then(() => {
        alert('Webhook URL copied to clipboard!');
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
}

// ==================== Telegram Bot ====================

async function loadTelegramStatus() {
    try {
        const [statusRes, configRes] = await Promise.all([
            fetch('/api/telegram/status'),
            fetch('/api/telegram/config')
        ]);

        const status = await statusRes.json();
        const config = await configRes.json();

        updateTelegramUI(status, config);
    } catch (error) {
        console.error('Error loading Telegram status:', error);
    }
}

function updateTelegramUI(status, config) {
    // Update status card
    const badge = document.getElementById('telegram-badge');
    if (status.running) {
        badge.textContent = 'Running';
        badge.className = 'status-badge running';
    } else {
        badge.textContent = 'Stopped';
        badge.className = 'status-badge stopped';
    }

    document.getElementById('telegram-running').textContent = status.running ? 'Yes' : 'No';
    document.getElementById('telegram-username').textContent = status.username ? '@' + status.username : '-';
    document.getElementById('telegram-started-at').textContent = status.started_at ? formatDate(status.started_at) : '-';
    document.getElementById('telegram-allowed-users').textContent = status.allowed_users || 0;
    document.getElementById('telegram-subscribed-chats').textContent = status.subscribed_chats || 0;

    // Update buttons
    document.getElementById('telegram-start-btn').disabled = status.running;
    document.getElementById('telegram-stop-btn').disabled = !status.running;

    // Update config form
    if (config.bot_token) {
        document.getElementById('tg-bot-token').placeholder = config.bot_token;
    }
    if (config.allowed_user_ids && config.allowed_user_ids.length > 0) {
        document.getElementById('tg-allowed-users').value = config.allowed_user_ids.join(', ');
    }
    document.getElementById('tg-push-session-status').checked = config.push_session_status !== false;
    document.getElementById('tg-push-automation-events').checked = config.push_automation_events !== false;
    document.getElementById('tg-push-session-output').checked = config.push_session_output === true;
    if (config.output_max_lines) {
        document.getElementById('tg-output-max-lines').value = config.output_max_lines;
    }
}

async function startTelegramBot() {
    const tokenInput = document.getElementById('tg-bot-token');
    const token = tokenInput.value;
    const usersStr = document.getElementById('tg-allowed-users').value;

    const allowedUsers = usersStr
        ? usersStr.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
        : [];

    const body = { allowed_user_ids: allowedUsers };
    if (token) {
        body.bot_token = token;
    }

    try {
        const response = await fetch('/api/telegram/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await response.json();

        if (data.success) {
            tokenInput.value = '';
            loadTelegramStatus();
        } else {
            alert('Error starting bot: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        alert('Error starting bot: ' + error.message);
    }
}

async function stopTelegramBot() {
    if (!confirm('Stop the Telegram bot?')) return;

    try {
        const response = await fetch('/api/telegram/stop', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            loadTelegramStatus();
        } else {
            alert('Error stopping bot: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        alert('Error stopping bot: ' + error.message);
    }
}

async function saveTelegramConfig(event) {
    event.preventDefault();

    const token = document.getElementById('tg-bot-token').value;
    const usersStr = document.getElementById('tg-allowed-users').value;
    const allowedUsers = usersStr
        ? usersStr.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
        : [];

    const configData = {
        allowed_user_ids: allowedUsers,
        push_session_status: document.getElementById('tg-push-session-status').checked,
        push_automation_events: document.getElementById('tg-push-automation-events').checked,
        push_session_output: document.getElementById('tg-push-session-output').checked,
        output_max_lines: parseInt(document.getElementById('tg-output-max-lines').value) || 20,
    };

    // Only include token if user entered a new one
    if (token) {
        configData.bot_token = token;
    }

    try {
        const response = await fetch('/api/telegram/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData)
        });

        const data = await response.json();

        if (data.success) {
            alert('Telegram configuration saved.');
            document.getElementById('tg-bot-token').value = '';
            loadTelegramStatus();
        } else {
            alert('Error saving config: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        alert('Error saving config: ' + error.message);
    }
}

// ==================== System ====================

async function loadSystemInfo() {
    try {
        const [versionRes, serverRes, healthRes] = await Promise.all([
            fetch('/api/version'),
            fetch('/api/server/info'),
            fetch('/health')
        ]);

        const version = await versionRes.json();
        const server = await serverRes.json();
        const health = await healthRes.json();

        document.getElementById('sys-version').textContent = version.version || '-';
        document.getElementById('sys-working-dir').textContent = server.working_directory || '-';

        renderHealthGrid(health.components || {});
    } catch (error) {
        console.error('Error loading system info:', error);
    }
}

function renderHealthGrid(components) {
    const container = document.getElementById('health-grid');

    container.innerHTML = Object.entries(components).map(([name, info]) => `
        <div class="health-item ${info.status || 'unknown'}">
            <span>${name}</span>
            <span class="status-badge ${info.status === 'ok' ? 'running' : 'stopped'}">
                ${info.status || 'unknown'}
            </span>
        </div>
    `).join('');
}

async function refreshHealth() {
    loadSystemInfo();
}

async function checkForUpdates() {
    const container = document.getElementById('update-status');
    container.innerHTML = '<p>Checking for updates...</p>';

    try {
        const response = await fetch('/api/update/check');
        const data = await response.json();

        if (data.update && data.update.update_available) {
            container.innerHTML = `
                <p><strong>Update available!</strong></p>
                <p>Current: ${data.update.current_version} → New: ${data.update.latest_version}</p>
                <button class="btn btn-primary" onclick="installUpdate()">Install Update</button>
            `;
        } else {
            container.innerHTML = `<p>You are running the latest version (${data.update?.current_version || '-'})</p>`;
        }
    } catch (error) {
        container.innerHTML = `<p>Error checking for updates: ${error.message}</p>`;
    }
}

async function installUpdate() {
    if (!confirm('Install the update? Autowrkers will restart.')) return;

    try {
        const response = await fetch('/api/update/install', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            alert('Update installed! Please restart Autowrkers.');
        } else {
            alert('Error: ' + (data.error || 'Failed to install update'));
        }
    } catch (error) {
        alert('Error installing update: ' + error.message);
    }
}

// ==================== Utilities ====================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleString();
    } catch {
        return dateStr;
    }
}

// ==================== Initialize ====================

document.addEventListener('DOMContentLoaded', () => {
    // Load initial data for daemon tab (default)
    loadDaemonStatus();

    // Close modals on outside click
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('active');
            }
        });
    });
});
