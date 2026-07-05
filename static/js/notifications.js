const CareNotifications = (() => {
  let knownIds = new Set();
  let unreadCount = 0;
  let socket = null;

  function requestBrowserPermission() {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'default') {
      Notification.requestPermission().then(p => {
        if (p === 'granted') showToast('Browser notifications enabled! 🔔', 'success');
      });
    }
  }

  function showBrowserNotification(title, body, notifId) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const notif = new Notification(title, {
      body,
      icon: '/static/favicon.ico',
      badge: '/static/favicon.ico',
      tag: `caresync-${notifId}`,
      requireInteraction: true
    });
    notif.onclick = () => {
      window.focus();
      markRead(notifId);
      notif.close();
    };
  }

  function playSound() {
    const audio = document.getElementById('notification-sound');
    if (audio) {
      audio.play().catch(e => console.log('Audio play blocked:', e));
    }
  }

  function showToast(message, type = 'info', notifId = null, notifType = '') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const icons = {
      'Medicine Reminder': '💊',
      'Repeat Reminder':   '⚠️',
      'Missed Dose':       '❌',
      'SOS Emergency':     '🚨',
      'success': '✅',
      'info':    'ℹ️',
      'warning': '⚠️',
      'danger':  '🚨'
    };
    const icon = icons[notifType] || icons[type] || 'ℹ️';

    const colors = {
      'Medicine Reminder': '#667eea',
      'Repeat Reminder':   '#fd7e14',
      'Missed Dose':       '#dc3545',
      'SOS Emergency':     '#dc3545',
      'success': '#28a745',
      'info':    '#17a2b8',
      'warning': '#fd7e14',
      'danger':  '#dc3545'
    };
    const color = colors[notifType] || colors[type] || '#667eea';

    const toast = document.createElement('div');
    toast.className = 'cs-toast';
    toast.style.cssText = `
      background: #fff;
      border-left: 4px solid ${color};
      border-radius: 10px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.18);
      padding: 14px 18px;
      margin-bottom: 10px;
      display: flex;
      align-items: flex-start;
      gap: 12px;
      max-width: 360px;
      animation: slideInRight 0.4s ease;
      position: relative;
    `;
    toast.innerHTML = `
      <span style="font-size:22px;flex-shrink:0;">${icon}</span>
      <div style="flex:1;min-width:0;">
        <div style="font-weight:700;color:#333;font-size:14px;margin-bottom:3px;">${notifType || 'Notification'}</div>
        <div style="color:#555;font-size:13px;line-height:1.4;word-break:break-word;">${message}</div>
        ${notifId ? `
        <div style="margin-top:10px;display:flex;gap:8px;">
          <button onclick="CareNotifications.markRead(${notifId});this.closest('.cs-toast').remove();"
            style="background:${color};color:#fff;border:none;border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;font-weight:600;">
            Mark Read ✓
          </button>
        </div>` : ''}
      </div>
      <button onclick="this.closest('.cs-toast').remove();"
        style="background:none;border:none;color:#aaa;font-size:18px;cursor:pointer;flex-shrink:0;line-height:1;">×</button>
    `;

    container.appendChild(toast);
    
    // Auto-remove is intentionally omitted so notifications persist until acted upon
  }

  function updateBadge(count) {
    unreadCount = count;
    const badge   = document.getElementById('notif-badge');
    const countEl = document.getElementById('notif-count');
    if (badge) {
      badge.style.display = count > 0 ? 'flex' : 'none';
      badge.textContent   = count > 99 ? '99+' : count;
    }
    if (countEl) countEl.textContent = count > 0 ? `${count} unread` : 'All caught up!';

    if (count > 0) {
      document.title = `(${count}) ${document.title.replace(/^\(\d+\) /, '')}`;
    } else {
      document.title = document.title.replace(/^\(\d+\) /, '');
    }
  }

  function renderNotificationList(notifications) {
    const list = document.getElementById('notif-list');
    if (!list) return;

    if (notifications.length === 0) {
      list.innerHTML = `<div style="padding:24px;text-align:center;color:#aaa;">
        <div style="font-size:32px;margin-bottom:8px;">🔔</div>
        <div>No notifications yet</div>
      </div>`;
      return;
    }

    list.innerHTML = notifications.slice(0, 20).map(n => {
      const icons = {
        'Medicine Reminder': '💊',
        'Repeat Reminder':   '⚠️',
        'Missed Dose':       '❌',
        'SOS Emergency':     '🚨'
      };
      const icon        = icons[n.type] || '🔔';
      const unreadStyle = n.is_read ? '' : 'background:#f0f4ff;';
      const time        = new Date(n.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return `
        <div class="notif-item" style="${unreadStyle}padding:12px 16px;border-bottom:1px solid #f0f0f0;cursor:pointer;"
             onclick="CareNotifications.markRead(${n.id});this.style.background='#fff';">
          <div style="display:flex;align-items:flex-start;gap:10px;">
            <span style="font-size:20px;flex-shrink:0;">${icon}</span>
            <div style="flex:1;min-width:0;">
              <div style="font-weight:${n.is_read ? '400' : '700'};color:#333;font-size:13px;">${n.type}</div>
              <div style="color:#666;font-size:12px;margin-top:2px;word-break:break-word;">${n.message}</div>
              <div style="color:#aaa;font-size:11px;margin-top:4px;">${time}</div>
            </div>
            ${!n.is_read ? `<span style="width:8px;height:8px;background:#667eea;border-radius:50%;flex-shrink:0;margin-top:4px;"></span>` : ''}
          </div>
        </div>`;
    }).join('');
  }

  async function fetchInitial() {
    try {
      const res = await fetch('/api/notifications', { credentials: 'same-origin' });
      if (!res.ok) return;
      const data = await res.json();
      updateBadge(data.unread_count);
      renderNotificationList(data.notifications);
      data.notifications.forEach(n => knownIds.add(n.id));
    } catch (e) {}
  }

  async function markRead(alertId) {
    try {
      await fetch(`/api/notifications/read/${alertId}`, { method: 'POST', credentials: 'same-origin' });
      knownIds.add(alertId);
      await fetchInitial();
    } catch (e) {}
  }

  async function markAllRead() {
    try {
      await fetch('/api/notifications/read_all', { method: 'POST', credentials: 'same-origin' });
      await fetchInitial();
      showToast('All notifications marked as read', 'success');
    } catch (e) {}
  }

  function togglePanel() {
    const panel = document.getElementById('notif-panel');
    if (!panel) return;
    const isOpen = panel.style.display === 'block';
    panel.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) fetchInitial();
  }

  function initSocket() {
    if (typeof io !== 'undefined') {
        socket = io();
        socket.on('new_notification', (data) => {
            if (!knownIds.has(data.id)) {
                knownIds.add(data.id);
                playSound();
                showBrowserNotification(`CareSync: ${data.type}`, data.message, data.id);
                showToast(data.message, 'info', data.id, data.type);
                fetchInitial();
            }
        });
        
        socket.on('dashboard_update', (data) => {
            document.dispatchEvent(new CustomEvent('caresync:dashboard_update', { detail: data }));
        });
        
        socket.on('status_update', (data) => {
            document.dispatchEvent(new CustomEvent('caresync:status_update', { detail: data }));
        });
    }
  }

  function init() {
    requestBrowserPermission();
    fetchInitial();
    initSocket();

    // Close panel on outside click
    document.addEventListener('click', (e) => {
      const panel = document.getElementById('notif-panel');
      const bell  = document.getElementById('notif-bell');
      if (panel && bell && !panel.contains(e.target) && !bell.contains(e.target)) {
        panel.style.display = 'none';
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return { markRead, markAllRead, togglePanel };
})();
