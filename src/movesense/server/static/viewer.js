/**
 * viewer.js — Thin WebSocket client + stacked uPlot renderer.
 * All data comes from server via WebSocket. No REST calls for chart data.
 * Target: <500 lines.
 */

const VC_COLORS = [
  '#ef4444','#22c55e','#3b82f6','#f59e0b','#8b5cf6','#06b6d4','#ec4899','#14b8a6','#64748b',
  '#dc2626','#16a34a','#2563eb','#d97706','#7c3aed','#0891b2','#db2777','#0d9488','#475569',
  '#b91c1c','#15803d','#1d4ed8','#b45309','#6d28d9','#0e7490','#be185d','#0f766e','#334155',
];

// --- ViewerClient: WebSocket connection + message routing ---

class ViewerClient {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.onMetadata = null;
    this.onData = null;
    this.onStatus = null;
    this.onError = null;
    this._buffer = {};  // prefetch buffer: key → data packet
  }

  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'metadata' && this.onMetadata) this.onMetadata(msg);
        else if (msg.type === 'data') {
          if (msg.prefetch) {
            const key = `${msg.channel}:${msg.time[0]}:${msg.time[msg.time.length-1]}`;
            this._buffer[key] = msg;
          } else if (this.onData) {
            this.onData(msg);
          }
        }
        else if (msg.type === 'status' && this.onStatus) this.onStatus(msg);
        else if (msg.type === 'error' && this.onError) this.onError(msg.message);
      } catch (err) { /* skip malformed */ }
    };
    this.ws.onclose = () => { if (this.onStatus) this.onStatus({ state: 'disconnected' }); };
    this.ws.onerror = () => { if (this.onError) this.onError('WebSocket connection failed'); };
  }

  send(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  selectDevice(serial) { this.send({ type: 'connect', serial }); }
  setView(startUs, endUs, widthPx) { this.send({ type: 'view', start_us: startUs, end_us: endUs, width_px: widthPx }); }
  subscribe(channels) { this.send({ type: 'subscribe', channels }); }
  startStream(serial, channels) { this.send({ type: 'stream', action: 'start', serial, channels }); }
  stopStream() { this.send({ type: 'stream', action: 'stop' }); }

  checkBuffer(channel, startTime, endTime) {
    for (const [key, pkt] of Object.entries(this._buffer)) {
      if (pkt.channel === channel && pkt.time[0] <= startTime && pkt.time[pkt.time.length-1] >= endTime) {
        return pkt;
      }
    }
    return null;
  }

  clearBuffer() { this._buffer = {}; }
}

// --- ChartRenderer: stacked uPlot rows ---

class ChartRenderer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this._plots = [];
    this._channels = {};  // name → {data: {time, values}, axes, unit}
    this._syncKey = 'vr-' + Math.random().toString(36).slice(2, 6);
    this.onZoom = null;
  }

  update(packet) {
    const ch = packet.channel;
    const isMulti = Array.isArray(packet.values[0]);
    this._channels[ch] = {
      time: packet.time,
      values: packet.values,
      axes: isMulti ? packet.values[0].length : 1,
      unit: packet.unit || '',
      source: packet.source,
    };
    this._render();
  }

  clear() {
    this._plots.forEach(p => p.destroy());
    this._plots = [];
    this._channels = {};
    if (this.container) this.container.innerHTML = '';
  }

  captureScreenshot() {
    const canvases = this._plots.map(p => p.ctx.canvas);
    if (!canvases.length) return;
    const totalH = canvases.reduce((h, c) => h + c.height, 0);
    const maxW = Math.max(...canvases.map(c => c.width));
    const off = document.createElement('canvas');
    off.width = maxW; off.height = totalH;
    const ctx = off.getContext('2d');
    let y = 0;
    for (const c of canvases) { ctx.drawImage(c, 0, y); y += c.height; }
    off.toBlob(blob => {
      if (!blob) return;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `movesense-${new Date().toISOString().slice(0,19)}.png`;
      a.click(); URL.revokeObjectURL(a.href);
    }, 'image/png');
  }

  _render() {
    this._plots.forEach(p => p.destroy());
    this._plots = [];
    this.container.innerHTML = '';

    const chNames = Object.keys(this._channels);
    if (!chNames.length) {
      this.container.innerHTML = '<div style="padding:2rem;text-align:center;color:#999">No data</div>';
      return;
    }

    const width = this.container.clientWidth || 800;
    const rowH = Math.max(80, Math.min(150, 550 / chNames.length));
    const self = this;

    chNames.forEach((name, idx) => {
      const ch = this._channels[name];
      const isLast = idx === chNames.length - 1;
      const el = document.createElement('div');
      el.style.marginBottom = isLast ? '0' : '-1px';
      this.container.appendChild(el);

      // Build series
      const series = [{}];
      const plotData = [new Float64Array(ch.time)];
      const axisLabels = ch.axes === 3 ? ['x','y','z'] :
        ch.axes === 9 ? ['Ax','Ay','Az','Gx','Gy','Gz','Mx','My','Mz'] :
        [name.split('/').pop() || name];

      if (ch.axes > 1 && Array.isArray(ch.values[0])) {
        for (let a = 0; a < ch.axes; a++) {
          series.push({ label: axisLabels[a], stroke: VC_COLORS[(idx*3+a) % VC_COLORS.length], width: 1, spanGaps: true });
          plotData.push(new Float32Array(ch.values.map(r => r[a])));
        }
      } else {
        series.push({ label: axisLabels[0], stroke: VC_COLORS[idx % VC_COLORS.length], width: 1, spanGaps: true });
        plotData.push(new Float32Array(ch.values));
      }

      const shortName = name.split('/').pop() || name;
      const opts = {
        width, height: isLast ? rowH + 28 : rowH,
        series,
        scales: { x: { time: false }, y: { auto: true } },
        axes: [
          { stroke: '#333', grid: { stroke: '#eee' }, size: isLast ? 26 : 0, font: '9px sans-serif',
            label: isLast ? 'Time (s)' : '', show: isLast, ticks: { show: isLast } },
          { stroke: VC_COLORS[idx % VC_COLORS.length], grid: { stroke: '#f5f5f5' }, size: 45,
            font: '9px sans-serif', label: `${shortName} ${ch.unit ? '('+ch.unit+')' : ''}`, labelFont: '9px sans-serif', labelSize: 10 },
        ],
        cursor: { sync: { key: self._syncKey, setSeries: false }, drag: { x: false, y: false } },
        select: { show: true },
        legend: { show: false },
        hooks: {
          setSelect: [(u) => {
            if (u.select.width >= 5) {
              const left = u.posToVal(u.select.left, 'x');
              const right = u.posToVal(u.select.left + u.select.width, 'x');
              if (right - left > 0.001) {
                self._plots.forEach(p => p.setScale('x', { min: left, max: right }));
                if (self.onZoom) self.onZoom(left, right);
              }
            }
            if (u.select.height >= 5) {
              const yT = u.posToVal(u.select.top, 'y');
              const yB = u.posToVal(u.select.top + u.select.height, 'y');
              u.setScale('y', { min: Math.min(yT, yB), max: Math.max(yT, yB) });
            }
            u.setSelect({ left:0, top:0, width:0, height:0 }, false);
          }],
        },
      };

      const plot = new uPlot(opts, plotData, el);
      this._plots.push(plot);

      // Scroll wheel X-zoom (synced)
      el.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = el.getBoundingClientRect();
        const xPct = (e.clientX - rect.left) / rect.width;
        const xMin = plot.scales.x.min, xMax = plot.scales.x.max;
        const range = xMax - xMin;
        const factor = e.deltaY > 0 ? 1.3 : 0.7;
        const nr = Math.max(0.1, range * factor);
        const c = xMin + range * xPct;
        self._plots.forEach(p => p.setScale('x', { min: c - nr * xPct, max: c + nr * (1 - xPct) }));
        if (self.onZoom) self.onZoom(c - nr * xPct, c + nr * (1 - xPct));
      }, { passive: false });

      // Double-click Y reset
      el.addEventListener('dblclick', (e) => {
        if (e.clientX - el.getBoundingClientRect().left < 50) plot.setScale('y', { auto: true });
      });

      // Legend click (toggle series)
      el.querySelectorAll('.u-legend .u-series').forEach((item, si) => {
        if (si === 0) return;
        item.style.cursor = 'pointer';
        item.addEventListener('click', () => plot.setSeries(si, { show: !plot.series[si].show }));
      });
    });
  }

  resize() {
    const w = this.container.clientWidth || 800;
    this._plots.forEach(p => p.setSize({ width: w, height: p.height }));
  }
}

// --- ControlPanel: built from server metadata ---

class ControlPanel {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.metadata = null;
    this.selectedChannels = new Set();
    this.onChannelToggle = null;
    this.onStreamControl = null;
  }

  buildFromMetadata(meta) {
    this.metadata = meta;
    this.selectedChannels = new Set(meta.channels.map(c => c.name));
    this._render();
  }

  getSelected() { return [...this.selectedChannels]; }

  _render() {
    if (!this.container || !this.metadata) return;
    const m = this.metadata;
    const dev = m.device || {};

    let html = `<div style="font-size:0.85rem;margin-bottom:0.5rem;">
      <strong>${m.serial}</strong> ${dev.firmware ? 'v'+dev.firmware : ''} ${dev.battery != null ? '| 🔋'+dev.battery+'%' : ''}
      <span style="color:${m.state === 'streaming' ? '#22c55e' : m.state === 'logging' ? '#ef4444' : '#999'};margin-left:0.5rem;">● ${m.state}</span>
    </div>`;

    // Session overview bar
    if (m.sessions && m.sessions.length > 0) {
      html += `<div style="font-size:0.7rem;color:#999;margin-bottom:0.25rem;">${m.sessions.length} sessions</div>`;
    }

    // Channel picker
    html += '<div style="display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center;margin-bottom:0.5rem;">';
    html += '<span style="font-size:0.8rem;font-weight:600;">Channels:</span>';
    for (const ch of m.channels) {
      const checked = this.selectedChannels.has(ch.name) ? 'checked' : '';
      const rate = ch.rate_hz ? ` ${Math.round(ch.rate_hz)}Hz` : '';
      html += `<label style="font-size:0.8rem;cursor:pointer;white-space:nowrap;">
        <input type="checkbox" ${checked} onchange="controlPanel._toggle('${ch.name}', this.checked)">
        ${ch.name.split('/').pop()}${rate ? '<span style=\"color:#999;font-size:0.7rem\">'+rate+'</span>' : ''}
      </label>`;
    }
    html += '</div>';

    // Controls
    html += '<div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap;">';
    html += '<button onclick="chartRenderer.captureScreenshot()" style="font-size:0.75rem;">📷</button>';
    html += '<button onclick="resetZoom()" style="font-size:0.75rem;">Reset Zoom</button>';
    html += '</div>';

    this.container.innerHTML = html;
  }

  _toggle(name, checked) {
    if (checked) this.selectedChannels.add(name);
    else this.selectedChannels.delete(name);
    if (this.onChannelToggle) this.onChannelToggle(this.getSelected());
  }

  updateStatus(status) {
    // Update state indicator
    const stateEl = this.container?.querySelector('span[style*="margin-left"]');
    if (stateEl) {
      stateEl.textContent = `● ${status.state || 'unknown'}`;
    }
  }
}

// Exports
window.ViewerClient = ViewerClient;
window.ChartRenderer = ChartRenderer;
window.ControlPanel = ControlPanel;
