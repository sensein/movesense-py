/**
 * TimelineBar: horizontal overview of recording sessions with gap markers.
 * Renders session blocks as colored rectangles on a canvas, gaps as gray.
 * Click/drag to select a time range → triggers onRangeSelect callback.
 */

class TimelineBar {
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    this.onRangeSelect = options.onRangeSelect || null;
    this.sessions = [];
    this._canvas = null;
    this._ctx = null;
    this._minUs = 0;
    this._maxUs = 0;
    this._highlightStart = null;
    this._highlightEnd = null;
    this._dragStart = null;
  }

  async load(serial) {
    try {
      const resp = await apiFetch(`/devices/${serial}/sessions`);
      this.sessions = resp.sessions || [];
      if (this.sessions.length > 0) {
        this._minUs = Math.min(...this.sessions.map(s => s.start_utc_us || 0));
        this._maxUs = Math.max(...this.sessions.map(s => s.end_utc_us || s.start_utc_us || 0));
      }
      this.draw();
    } catch (e) {
      this.container.innerHTML = `<div style="color:#999;font-size:0.8rem">No sessions found</div>`;
    }
  }

  draw() {
    if (!this.sessions.length) {
      this.container.innerHTML = '<div style="color:#999;font-size:0.8rem">No recording sessions</div>';
      return;
    }

    // Create canvas
    this.container.innerHTML = '';
    const width = this.container.clientWidth || 800;
    const height = 48;
    this._canvas = document.createElement('canvas');
    this._canvas.width = width;
    this._canvas.height = height;
    this._canvas.style.width = '100%';
    this._canvas.style.height = height + 'px';
    this._canvas.style.cursor = 'pointer';
    this._canvas.style.borderRadius = '4px';
    this._canvas.style.border = '1px solid #e5e7eb';
    this.container.appendChild(this._canvas);
    this._ctx = this._canvas.getContext('2d');

    const range = this._maxUs - this._minUs || 1;
    const ctx = this._ctx;

    // Background
    ctx.fillStyle = '#f9fafb';
    ctx.fillRect(0, 0, width, height);

    // Session blocks
    const colors = ['#3b82f6', '#22c55e', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4'];
    for (let i = 0; i < this.sessions.length; i++) {
      const s = this.sessions[i];
      const startUs = s.start_utc_us || 0;
      const endUs = s.end_utc_us || startUs;
      const x = ((startUs - this._minUs) / range) * width;
      const w = Math.max(2, ((endUs - startUs) / range) * width);
      ctx.fillStyle = colors[i % colors.length];
      ctx.fillRect(x, 4, w, height - 8);

      // Session label
      if (w > 30) {
        ctx.fillStyle = '#fff';
        ctx.font = '10px sans-serif';
        ctx.fillText(`${i}`, x + 4, height / 2 + 3);
      }
    }

    // Highlight region
    if (this._highlightStart != null && this._highlightEnd != null) {
      const hx = ((this._highlightStart - this._minUs) / range) * width;
      const hw = ((this._highlightEnd - this._highlightStart) / range) * width;
      ctx.strokeStyle = '#ef4444';
      ctx.lineWidth = 2;
      ctx.strokeRect(hx, 1, hw, height - 2);
    }

    // Time labels
    ctx.fillStyle = '#666';
    ctx.font = '9px sans-serif';
    const startDate = new Date(this._minUs / 1000);
    const endDate = new Date(this._maxUs / 1000);
    ctx.fillText(startDate.toLocaleDateString() + ' ' + startDate.toLocaleTimeString(), 4, height - 2);
    const endLabel = endDate.toLocaleDateString() + ' ' + endDate.toLocaleTimeString();
    ctx.fillText(endLabel, width - ctx.measureText(endLabel).width - 4, height - 2);

    // Click handler
    this._canvas.onclick = (e) => {
      const rect = this._canvas.getBoundingClientRect();
      const xPct = (e.clientX - rect.left) / rect.width;
      const clickUs = this._minUs + xPct * (this._maxUs - this._minUs);

      // Find clicked session
      for (const s of this.sessions) {
        const sStart = s.start_utc_us || 0;
        const sEnd = s.end_utc_us || sStart;
        if (clickUs >= sStart && clickUs <= sEnd) {
          this.highlight(sStart, sEnd);
          if (this.onRangeSelect) this.onRangeSelect(sStart, sEnd);
          return;
        }
      }
    };
  }

  zoomTo(startUs, endUs) {
    this.highlight(startUs, endUs);
  }

  highlight(startUs, endUs) {
    this._highlightStart = startUs;
    this._highlightEnd = endUs;
    this.draw();
  }
}

window.TimelineBar = TimelineBar;
