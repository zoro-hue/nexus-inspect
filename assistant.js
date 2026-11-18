/**
 * NEXUS INSPECT — Nexus AI Context-Aware Floating Assistant
 */
(function() {
  'use strict';

  const state = {
    isOpen: false,
    isLoading: false,
    history: [],
    currentRoute: 'home',
    analysisId: null,
    findingId: null,
  };

  // Simple Markdown-to-HTML parser for assistant replies
  function renderMarkdown(md) {
    if (!md) return '';
    let html = md
      // Escape HTML tags to prevent XSS
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Code blocks ```code```
    html = html.replace(/```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```/g, (match, code) => {
      return `<pre><code>${code.trim()}</code></pre>`;
    });

    // Inline code `code`
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Headers ### Title
    html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h3>$1</h3>');
    html = html.replace(/^# (.*$)/gim, '<h3>$1</h3>');

    // Alerts > [!IMPORTANT] or > [!TIP]
    html = html.replace(/^&gt; \[\!(IMPORTANT|WARNING|CAUTION)\]\s*([\s\S]*?)(?=(?:\n\n|\n[^\&]|$))/gim, '<blockquote class="alert-important">$2</blockquote>');
    html = html.replace(/^&gt; \[\!(NOTE|TIP)\]\s*([\s\S]*?)(?=(?:\n\n|\n[^\&]|$))/gim, '<blockquote class="alert-note">$2</blockquote>');
    html = html.replace(/^&gt; (.*$)/gim, '<blockquote>$1</blockquote>');

    // Bold **text**
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Italic *text*
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

    // Bullet lists - item
    html = html.replace(/^\s*-\s+(.*$)/gim, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/gms, '<ul>$1</ul>');
    // Numbered lists 1. item
    html = html.replace(/^\s*\d+\.\s+(.*$)/gim, '<li>$1</li>');

    // Paragraphs
    const paras = html.split(/\n\n+/);
    html = paras.map(p => {
      p = p.trim();
      if (!p) return '';
      if (p.startsWith('<h') || p.startsWith('<pre') || p.startsWith('<ul>') || p.startsWith('<block')) {
        return p;
      }
      return `<p>${p.replace(/\n/g, '<br>')}</p>`;
    }).join('');

    return html;
  }

  function getContext() {
    const hash = (location.hash || '').replace('#', '').split('?')[0];
    const parts = hash.split('/');
    const route = parts[0] || 'home';
    let aid = localStorage.getItem('nexus.analysis') || null;
    let fid = null;

    // Check if drawer is open for a finding
    const drawer = document.getElementById('drawer');
    if (drawer && drawer.classList.contains('open')) {
      const activeF = document.querySelector('.finding.active, .finding[data-fid]');
      if (activeF) fid = activeF.dataset.fid || null;
    }

    return { route, analysisId: aid, findingId: fid };
  }

  function updateContextBadge() {
    const badge = document.getElementById('nexusAiBadge');
    if (!badge) return;
    const ctx = getContext();
    state.currentRoute = ctx.route;
    state.analysisId = ctx.analysisId;
    state.findingId = ctx.findingId;

    let label = ctx.route.toUpperCase();
    if (label === 'HOME' || label === '') label = 'OVERVIEW';
    if (ctx.findingId) label = 'FINDING';
    badge.textContent = label;
  }

  async function fetchSuggestions(route, aid) {
    try {
      const q = new URLSearchParams({ route: route || 'home' });
      if (aid) q.set('analysisId', aid);
      const res = await fetch(`/api/ai/assistant/suggestions?${q}`);
      if (res.ok) {
        const data = await res.json();
        return data.suggestions || [];
      }
    } catch (_) {}
    return [
      "What rules does NEXUS check?",
      "How does AI slop detection work?",
      "What is loose-end discovery?",
      "How do I analyze a repository?",
    ];
  }

  function renderSuggestions(chips) {
    const box = document.getElementById('nexusAiSuggestions');
    if (!box) return;
    if (!chips || !chips.length) {
      box.innerHTML = '';
      return;
    }
    box.innerHTML = `
      <div class="ai-suggestions-box">
        <span class="ai-suggestions-label">Suggested queries</span>
        <div class="ai-chips">
          ${chips.map(c => `<button type="button" class="ai-chip">${escapeHtml(c)}</button>`).join('')}
        </div>
      </div>
    `;
    box.querySelectorAll('.ai-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        sendMessage(btn.textContent.trim());
      });
    });
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function scrollToBottom() {
    const msgs = document.getElementById('nexusAiMessages');
    if (msgs) {
      msgs.scrollTop = msgs.scrollHeight;
    }
  }

  function appendMessage(role, content, notice) {
    const msgs = document.getElementById('nexusAiMessages');
    if (!msgs) return;
    const row = document.createElement('div');
    row.className = `ai-msg ai-msg-${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'ai-bubble';

    if (role === 'user') {
      bubble.textContent = content;
    } else {
      bubble.innerHTML = renderMarkdown(content);
      if (notice) {
        const notEl = document.createElement('div');
        notEl.className = 'notice-text';
        notEl.textContent = notice;
        bubble.appendChild(notEl);
      }
    }

    row.appendChild(bubble);
    msgs.appendChild(row);
    scrollToBottom();
  }

  function showTyping() {
    removeTyping();
    const msgs = document.getElementById('nexusAiMessages');
    if (!msgs) return;
    const row = document.createElement('div');
    row.id = 'nexusAiTyping';
    row.className = 'ai-msg ai-msg-assistant';
    row.innerHTML = `
      <div class="ai-bubble">
        <div class="ai-typing">
          <span class="ai-typing-dot"></span>
          <span class="ai-typing-dot"></span>
          <span class="ai-typing-dot"></span>
        </div>
      </div>
    `;
    msgs.appendChild(row);
    scrollToBottom();
  }

  function removeTyping() {
    const el = document.getElementById('nexusAiTyping');
    if (el) el.remove();
  }

  async function sendMessage(text) {
    const input = document.getElementById('nexusAiInput');
    const sendBtn = document.getElementById('nexusAiSend');
    const clean = (text || (input ? input.value : '')).trim();
    if (!clean || state.isLoading) return;

    if (input) {
      input.value = '';
      input.style.height = 'auto';
    }
    if (sendBtn) sendBtn.disabled = true;

    appendMessage('user', clean);
    state.isLoading = true;
    showTyping();

    const ctx = getContext();
    try {
      const res = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: clean,
          context: ctx,
        }),
      });

      removeTyping();
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        appendMessage('assistant', `Error: ${err.message || 'Unable to process query. Please try again.'}`);
      } else {
        const data = await res.json();
        appendMessage('assistant', data.reply, data.notice);
        if (data.suggestedQuestions) {
          renderSuggestions(data.suggestedQuestions);
        }
      }
    } catch (err) {
      removeTyping();
      appendMessage('assistant', 'Network error connecting to Nexus AI. Please check your connection and try again.');
    } finally {
      state.isLoading = false;
      if (input) input.focus();
    }
  }

  function togglePanel(open) {
    state.isOpen = typeof open === 'boolean' ? open : !state.isOpen;
    const panel = document.getElementById('nexusAiPanel');
    const capsule = document.getElementById('nexusAiCapsule');
    if (!panel || !capsule) return;

    if (state.isOpen) {
      panel.classList.remove('collapsed');
      panel.classList.remove('opening');
      void panel.offsetWidth; // restart the open animation on every open
      panel.classList.add('opening');
      capsule.classList.add('panel-open');
      capsule.setAttribute('aria-expanded', 'true');
      updateContextBadge();
      const input = document.getElementById('nexusAiInput');
      if (input) input.focus();
      scrollToBottom();
    } else {
      panel.classList.add('collapsed');
      panel.classList.remove('opening');
      capsule.classList.remove('panel-open');
      capsule.setAttribute('aria-expanded', 'false');
    }
  }

  function initUI() {
    if (document.getElementById('nexusAiCapsule')) return;

    // 1. Create floating capsule
    const capsule = document.createElement('button');
    capsule.id = 'nexusAiCapsule';
    capsule.type = 'button';
    capsule.setAttribute('aria-label', 'Open Nexus AI assistant');
    capsule.setAttribute('aria-expanded', 'false');
    capsule.innerHTML = `
      <span class="ai-capsule-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
        </svg>
      </span>
      <span class="ai-capsule-label">Ask Nexus</span>
      <span class="ai-capsule-dot"></span>
    `;

    // 2. Create conversational panel
    const panel = document.createElement('aside');
    panel.id = 'nexusAiPanel';
    panel.className = 'collapsed';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Nexus AI Assistant');
    panel.innerHTML = `
      <div class="ai-header">
        <div class="ai-header-brand">
          <span class="ai-capsule-icon" style="width:18px;height:18px;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
            </svg>
          </span>
          <span class="ai-header-title">Nexus AI</span>
          <span class="ai-header-badge" id="nexusAiBadge">HOME</span>
        </div>
        <button type="button" class="ai-header-close" id="nexusAiClose" aria-label="Close Nexus AI">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18"/>
          </svg>
        </button>
      </div>

      <div class="ai-messages" id="nexusAiMessages">
        <div class="ai-msg ai-msg-assistant">
          <div class="ai-bubble">
            <h3>Welcome to Nexus AI</h3>
            <p>I am your context-aware repository investigation assistant. Ask me questions about security intelligence, AI slop, coding gaps, loose ends, or remediation guidance.</p>
          </div>
        </div>
        <div id="nexusAiSuggestions"></div>
      </div>

      <form class="ai-form" id="nexusAiForm">
        <div class="ai-input-wrap">
          <textarea class="ai-input" id="nexusAiInput" rows="1" placeholder="Ask Nexus about this repo or rules..." aria-label="Ask Nexus AI"></textarea>
        </div>
        <button type="submit" class="ai-send-btn" id="nexusAiSend" aria-label="Send query" disabled>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </form>
    `;

    document.body.appendChild(capsule);
    document.body.appendChild(panel);

    // Event listeners
    capsule.addEventListener('click', (e) => {
      e.stopPropagation();
      togglePanel();
    });

    document.getElementById('nexusAiClose').addEventListener('click', (e) => {
      e.stopPropagation();
      togglePanel(false);
    });

    const form = document.getElementById('nexusAiForm');
    const input = document.getElementById('nexusAiInput');
    const sendBtn = document.getElementById('nexusAiSend');

    input.addEventListener('input', () => {
      sendBtn.disabled = !input.value.trim() || state.isLoading;
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 84) + 'px';
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      sendMessage();
    });

    // Close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && state.isOpen) {
        togglePanel(false);
      }
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
      if (state.isOpen && !panel.contains(e.target) && !capsule.contains(e.target)) {
        togglePanel(false);
      }
    });

    // Update suggestions and badge on route changes
    window.addEventListener('hashchange', () => {
      updateContextBadge();
      const ctx = getContext();
      fetchSuggestions(ctx.route, ctx.analysisId).then(renderSuggestions);
    });

    // Load initial suggestions
    const initialCtx = getContext();
    fetchSuggestions(initialCtx.route, initialCtx.analysisId).then(renderSuggestions);
  }

  // Auto initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
  } else {
    initUI();
  }

  window.nexusAi = {
    toggle: togglePanel,
    send: sendMessage,
    getState: () => ({ ...state }),
  };
})();
