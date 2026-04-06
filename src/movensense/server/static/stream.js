// Live streaming WebSocket client + unified chart display
// TOKEN is defined in the global init block (index.html)

class StreamClient {
  constructor(onData, onStatus, onError) {
    this.ws = null;
    this.onData = onData;
    this.onStatus = onStatus;
    this.onError = onError;
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.ws = new WebSocket(`${proto}//${location.host}/ws/stream?token=${TOKEN}`);

    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'data') this.onData(msg);
        else if (msg.type === 'ack') this.onStatus(msg);
        else if (msg.type === 'status') this.onStatus(msg);
        else if (msg.type === 'error') this.onError(msg.message);
        else if (msg.type === 'device_info') this.onStatus(msg);
      } catch (err) {
        // Skip malformed JSON
      }
    };
    this.ws.onclose = () => this.onStatus({ type: 'status', state: 'disconnected' });
    this.ws.onerror = () => this.onError('WebSocket connection failed');
  }

  send(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  startStream(serial, channels) { this.send({ type: 'start', serial, channels }); }
  stopStream() { this.send({ type: 'stop' }); }
  disconnect() { if (this.ws) this.ws.close(); }
}

// --- Chart constants ---

const AXIS_COLORS = ['#ef4444', '#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#14b8a6', '#64748b'];
const AXIS_LABELS = {
  3: ['x', 'y', 'z'],
};

function estimateRate(channel) {
  const ch = channel.toLowerCase();
  const m = channel.match(/\/(\d+)/);
  if (m) return parseInt(m[1]);
  if (ch.includes('ecg')) return 200;
  if (ch.includes('imu') || ch.includes('acc') || ch.includes('gyro') || ch.includes('magn')) return 52;
  if (ch.includes('hr')) return 1;
  if (ch.includes('temp')) return 1;
  return 10;
}

// --- ChartManager ---

class ChartManager {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.charts = {};
    this.windowSeconds = 10;
    this._paused = false;

    // Zoom state — persists across pause/resume
    this._zoomWindow = null;
    this._zoomEnd = null;
    this._syncing = false;
  }

  setWindow(seconds) {
    this.windowSeconds = seconds;
    this._zoomWindow = null;
    this._zoomEnd = null;
  }

  resetZoom() {
    this._zoomWindow = null;
    this._zoomEnd = null;
    for (const ch of Object.values(this.charts)) {
      ch.yZoom = null;
    }
    this._applyView();
    this._updateZoomLabel();
  }

  addData(channel, values, unit, axes, tSeconds) {
    // tSeconds: seconds since stream start (computed server-side)
    if (this._paused) return;
    if (!values || values.length === 0) return;

    const numAxes = (axes && axes > 1) ? axes
      : (Array.isArray(values[0]) ? values[0].length : 1);

    // IMU6/9: split into separate sub-charts for clarity
    if (numAxes >= 6 && Array.isArray(values[0])) {
      const subs = numAxes === 9
        ? [{ sfx: ' Acc', u: 'm/s²', s: [0, 3] }, { sfx: ' Gyro', u: 'dps', s: [3, 6] }, { sfx: ' Mag', u: 'µT', s: [6, 9] }]
        : [{ sfx: ' Acc', u: 'm/s²', s: [0, 3] }, { sfx: ' Gyro', u: 'dps', s: [3, 6] }];
      for (const sub of subs) {
        this.addData(channel + sub.sfx, values.map(r => r.slice(sub.s[0], sub.s[1])), sub.u, 3, tSeconds);
      }
      return;
    }

    if (!this.charts[channel]) {
      this._createChart(channel, numAxes, unit || '');
    }

    const chart = this.charts[channel];
    const rate = chart.rate;
    const numSeries = chart.data.length - 1;
    const dt = 1 / rate;

    // tSeconds is already in seconds since stream start (server-converted)
    const t0 = tSeconds;

    // Append samples
    if (numAxes > 1 && Array.isArray(values[0])) {
      for (let s = 0; s < values.length; s++) {
        const row = values[s];
        chart.data[0].push(t0 + s * dt);
        for (let a = 0; a < Math.min(row.length, numSeries); a++) {
          chart.data[a + 1].push(row[a]);
        }
        for (let a = row.length; a < numSeries; a++) {
          chart.data[a + 1].push(0);
        }
      }
    } else {
      for (let s = 0; s < values.length; s++) {
        chart.data[0].push(t0 + s * dt);
        chart.data[1].push(values[s]);
      }
    }

    // Trim buffer to windowSeconds
    const latestTime = chart.data[0][chart.data[0].length - 1];
    const cutoff = latestTime - this.windowSeconds;
    let trimIdx = 0;
    while (trimIdx < chart.data[0].length && chart.data[0][trimIdx] < cutoff) {
      trimIdx++;
    }
    if (trimIdx > 0) {
      for (let i = 0; i < chart.data.length; i++) {
        chart.data[i] = chart.data[i].slice(trimIdx);
      }
    }

    this._applyViewForChart(chart);
  }

  _applyViewForChart(chart) {
    if (!chart.plot || chart.data[0].length === 0) return;

    chart.plot.setData(chart.data);

    if (this._zoomWindow) {
      const dataMax = chart.data[0][chart.data[0].length - 1];
      const dataMin = chart.data[0][0];
      let viewEnd = (this._zoomEnd != null) ? this._zoomEnd : dataMax;
      let viewStart = viewEnd - this._zoomWindow;
      if (viewStart < dataMin) { viewStart = dataMin; viewEnd = viewStart + this._zoomWindow; }
      if (viewEnd > dataMax) { viewEnd = dataMax; viewStart = viewEnd - this._zoomWindow; }
      chart.plot.setScale('x', { min: viewStart, max: viewEnd });
    }

    // Per-chart Y zoom
    if (chart.yZoom) {
      chart.plot.setScale('y', { min: chart.yZoom.min, max: chart.yZoom.max });
    }
  }

  _applyView() {
    for (const ch of Object.values(this.charts)) {
      this._applyViewForChart(ch);
    }
  }

  _createChart(channel, numAxes, unit) {
    const wrapper = document.createElement('div');
    wrapper.className = 'chart-wrapper';
    wrapper.style.marginBottom = '4px';

    const chartId = 'stream-chart-' + channel.replace(/[^a-zA-Z0-9]/g, '_');
    const labels = AXIS_LABELS[numAxes] || (numAxes === 1 ? [channel.split('/').pop() || channel] : Array.from({length: numAxes}, (_, i) => `ch${i}`));

    const legendHtml = labels.map((l, i) =>
      `<span style="color:${AXIS_COLORS[i % AXIS_COLORS.length]};margin-right:0.5rem;font-size:0.75rem">■ ${l}</span>`
    ).join('');

    wrapper.innerHTML = `
      <div style="font-size:0.75rem;color:#666;display:flex;justify-content:space-between;align-items:center;">
        <span><strong>${channel}</strong> ${unit ? '(' + unit + ')' : ''}</span>
        <span id="val-${chartId}" style="color:#2563eb;font-size:0.7rem"></span>
      </div>
      <div id="${chartId}" style="width:100%;height:160px;"></div>
      <div style="text-align:center;margin-top:2px">${legendHtml}</div>`;
    this.container.appendChild(wrapper);

    const el = document.getElementById(chartId);
    const width = el.clientWidth || 800;
    const rate = estimateRate(channel);

    const series = [{ label: 'Time (s)' }];
    const data = [[]];
    for (let a = 0; a < numAxes; a++) {
      series.push({ label: labels[a], stroke: AXIS_COLORS[a % AXIS_COLORS.length], width: 1 });
      data.push([]);
    }

    const self = this;
    const opts = {
      width,
      height: 160,
      series,
      scales: {
        x: { time: false },
        y: { auto: true },
      },
      axes: [
        { stroke: '#333', grid: { stroke: '#eee' }, size: 35, font: '10px sans-serif', label: 'Time (s)' },
        { stroke: '#333', grid: { stroke: '#f5f5f5' }, size: 50, font: '10px sans-serif', label: unit || '' },
      ],
      cursor: {
        // No cursor sync — each streaming chart is independent
        // (uPlot sync bleeds legends/data across charts with different series)
        drag: { x: false, y: false },
      },
      select: { show: true },
      hooks: {
        setSelect: [
          (u) => {
            if (self._syncing) return;
            if (u.select.width < 5) return;
            self._syncing = true;

            // Synchronized X zoom
            const left = u.posToVal(u.select.left, 'x');
            const right = u.posToVal(u.select.left + u.select.width, 'x');
            if (right - left > 0.01) {
              self._zoomWindow = right - left;
              self._zoomEnd = right;
              if (!self._paused) self._zoomEnd = null;
            }

            // Per-chart Y zoom (only on THIS chart)
            if (u.select.height > 5) {
              const yTop = u.posToVal(u.select.top, 'y');
              const yBot = u.posToVal(u.select.top + u.select.height, 'y');
              const chartEntry = Object.values(self.charts).find(c => c.plot === u);
              if (chartEntry) {
                chartEntry.yZoom = { min: Math.min(yTop, yBot), max: Math.max(yTop, yBot) };
              }
            }

            self._applyView();
            self._updateZoomLabel();
            // Clear the selection rectangle
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
            self._syncing = false;
          }
        ],
      },
    };

    let plot = null;
    try {
      if (typeof uPlot !== 'undefined') {
        plot = new uPlot(opts, data, el);
      }
    } catch (e) {
      console.error(`Chart init error for ${channel}:`, e);
    }

    // Scroll-wheel zoom (X only, synchronized)
    el.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (self._syncing) return;
      self._syncing = true;

      const xMin = plot.scales.x.min;
      const xMax = plot.scales.x.max;
      const range = xMax - xMin;
      const rect = el.getBoundingClientRect();
      const xPct = (e.clientX - rect.left) / rect.width;
      const factor = e.deltaY > 0 ? 1.3 : 0.7;
      const newRange = Math.max(0.1, Math.min(self.windowSeconds, range * factor));
      const center = xMin + range * xPct;

      self._zoomWindow = newRange;
      self._zoomEnd = self._paused ? center + newRange * (1 - xPct) : null;

      self._applyView();
      self._updateZoomLabel();
      self._syncing = false;
    }, { passive: false });

    // Shift+drag to pan (when paused)
    let panStart = null;
    el.addEventListener('mousedown', (e) => {
      if (!self._paused || !self._zoomWindow) return;
      if (e.shiftKey || e.button === 1) {
        panStart = { x: e.clientX, xMin: plot.scales.x.min, xMax: plot.scales.x.max };
        e.preventDefault();
      }
    });
    el.addEventListener('mousemove', (e) => {
      if (!panStart) return;
      const dx = e.clientX - panStart.x;
      const valRange = panStart.xMax - panStart.xMin;
      const shift = -(dx / el.clientWidth) * valRange;
      self._zoomEnd = panStart.xMax + shift;
      self._applyView();
    });
    el.addEventListener('mouseup', () => { panStart = null; });
    el.addEventListener('mouseleave', () => { panStart = null; });

    this.charts[channel] = { plot, data, el, wrapper, rate, axes: numAxes, yZoom: null };
  }

  _updateZoomLabel() {
    const el = document.getElementById('stream-zoom-label');
    if (!el) return;
    if (this._zoomWindow) {
      el.textContent = `Zoom: ${this._zoomWindow.toFixed(1)}s window`;
      el.style.display = '';
    } else {
      el.textContent = '';
      el.style.display = 'none';
    }
    const btn = document.getElementById('btn-reset-zoom');
    if (btn) btn.style.display = this._zoomWindow ? '' : 'none';
  }

  clear() {
    this.container.innerHTML = '';
    this.charts = {};
    this._zoomWindow = null;
    this._zoomEnd = null;
  }
}

window.StreamClient = StreamClient;
window.ChartManager = ChartManager;
