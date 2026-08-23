/**
 * ATTENDIFY API & UI UTILITIES
 * Modern REST client, auth guards, and notification manager.
 */

// Dynamically resolve API URL across local, Vercel, and cloud deployments
function resolveApiBaseUrl() {
    if (window.ATTENDIFY_CONFIG && window.ATTENDIFY_CONFIG.API_BASE_URL) {
        return window.ATTENDIFY_CONFIG.API_BASE_URL;
    }
    if (window.ENV_API_URL) {
        return window.ENV_API_URL;
    }
    const customUrl = localStorage.getItem('API_BASE_URL');
    if (customUrl) {
        return customUrl;
    }
    const isSeparateLocalPort = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') && 
                                window.location.port !== '5000' && 
                                window.location.port !== '';
    if (isSeparateLocalPort) {
        return 'http://localhost:5000/api';
    }
    return '/api';
}

const API_BASE_URL = resolveApiBaseUrl();


/**
 * Universal Authenticated API Request Wrapper
 */
async function apiRequest(endpoint, method = 'GET', body = null) {
    const token = localStorage.getItem('token');
    const headers = {
        'Content-Type': 'application/json',
    };
    if (token) {
        headers['x-access-token'] = token;
    }

    const options = {
        method,
        headers,
    };
    if (body && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
        options.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, options);
        
        if (response.status === 401) {
            // Token expired or unauthorized
            localStorage.clear();
            showToast('Session expired. Please log in again.', 'warning');
            setTimeout(() => {
                if (!window.location.pathname.endsWith('index.html') && !window.location.pathname.endsWith('/')) {
                    const depth = window.location.pathname.includes('/admin/') || window.location.pathname.includes('/student/') || window.location.pathname.includes('/teacher/') ? '../' : '';
                    window.location.href = `${depth}index.html`;
                }
            }, 1200);
            return { message: 'Unauthorized session' };
        }

        const data = await response.json();
        return data;
    } catch (error) {
        console.error('API request error:', error);
        return { message: 'Network connection error. Is backend running?' };
    }
}

/**
 * Modern Multi-Type Toast Notification System
 */
function showToast(message, type = 'success') {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const iconMap = {
        success: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>`,
        error: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f43f5e" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
        warning: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
        info: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#0ea5e9" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`
    };

    toast.innerHTML = `
        <div style="display: flex; align-items: center; gap: 0.75rem; flex: 1;">
            ${iconMap[type] || iconMap.info}
            <span style="font-size: 0.88rem; font-weight: 500;">${message}</span>
        </div>
        <button style="background: none; border: none; color: #94a3b8; cursor: pointer; padding: 0.25rem;" onclick="this.parentElement.remove()">✕</button>
    `;

    container.appendChild(toast);

    // Slide in
    setTimeout(() => toast.classList.add('show'), 50);

    // Auto dismiss
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 400);
    }, 3800);
}

/**
 * Authentication Guard
 */
function requireAuth(allowedRoles = ['admin']) {
    const token = localStorage.getItem('token');
    const role = localStorage.getItem('role');

    if (!token || !role || !allowedRoles.includes(role)) {
        const depth = window.location.pathname.includes('/admin/') || window.location.pathname.includes('/student/') || window.location.pathname.includes('/teacher/') ? '../' : '';
        window.location.href = `${depth}index.html`;
        return false;
    }

    // Populate user profile info in sidebar/header if elements exist
    const fullName = localStorage.getItem('full_name') || 'User';
    const nameEls = document.querySelectorAll('.user-name, #adminName, #teacherName, #studentName');
    nameEls.forEach(el => el.textContent = fullName);

    const avatarEls = document.querySelectorAll('.avatar, #adminAvatar, #teacherAvatar, #studentAvatar');
    avatarEls.forEach(el => el.textContent = fullName.charAt(0).toUpperCase());

    return true;
}

/**
 * Logout Helper
 */
function logout() {
    localStorage.clear();
    showToast('Logged out successfully', 'info');
    const depth = window.location.pathname.includes('/admin/') || window.location.pathname.includes('/student/') || window.location.pathname.includes('/teacher/') ? '../' : '';
    setTimeout(() => {
        window.location.href = `${depth}index.html`;
    }, 500);
}

/**
 * Play Audio Feedback on successful recognition
 */
function playSuccessChime() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15); // A5
        gain.gain.setValueAtTime(0.1, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.3);
    } catch (e) {
        // AudioContext not allowed or supported
    }
}

