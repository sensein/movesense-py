/**
 * viewer.js — Server-driven streaming viewer with ECharts.
 * All data comes from server via WebSocket. No REST calls for chart data.
 * ECharts handles multi-channel stacking, pan/zoom, and time axis natively.
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
    this._buffer = {};
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
        else if (['status','busy','busy_done','confirm','device_status','mode_changed'].includes(msg.type)) {
          if (this.onStatus) this.onStatus(msg);
        }
        else if (msg.type === 'error' && this.onError) this.onError(msg.message);
      } catch (err) {}
    };
    this.ws.onclose = () => { if (this.onStatus) this.onStatus({ state: 'disconnected' }); };
    this.ws.onerror = () => { if (this.onError) this.onError('WebSocket connection failed'); };
  }

  send(msg) { if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg)); }
  selectDevice(serial) { this.send({ type: 'connect', serial }); }
  setView(startUs, endUs, widthPx) { this.send({ type: 'view', start_us: startUs, end_us: endUs, width_px: widthPx }); }
  subscribe(channels) { this.send({ type: 'subscribe', channels }); }
  startStream(serial, channels) { this.send({ type: 'stream', action: 'start', serial, channels }); }
  stopStream() { this.send({ type: 'stream', action: 'stop' }); }
  clearBuffer() { this._buffer = {}; }
}

// --- ChartRenderer: ECharts with stacked grids + dataZoom ---

class ChartRenderer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this._chart = null;
    this._channels = {};  // name → {time, values, axes, unit, source}
    this.onViewChange = null;  // callback(startUtcS, endUtcS) when user pans/zooms
    this._debounceTimer = null;
  }

  update(packet) {
    const ch = packet.channel;
    const isMulti = Array.isArray(packet.values[0]) && packet.values[0] != null && packet.values[0].length > 1;
    const axes = isMulti ? packet.values[0].length : 1;

    if (packet.source === 'live') {
      if (!this._channels[ch]) {
        // First live packet for this channel
        this._channels[ch] = { time: [], values: [], axes, unit: packet.unit || '', source: 'live' };
      }
      const existing = this._channels[ch];
      existing.source = 'live';
      existing.time = existing.time.concat(packet.time);
      existing.values = existing.values.concat(packet.values);
      // Trim to liveWindowSeconds
      const windowS = this.liveWindowSeconds || 30;
      if (existing.time.length > 1) {
        const maxT = existing.time[existing.time.length - 1];
        const cutoff = maxT - windowS;
        let i = 0;
        while (i < existing.time.length && existing.time[i] < cutoff) i++;
        if (i > 0) { existing.time = existing.time.slice(i); existing.values = existing.values.slice(i); }
      }
    } else {
      this._channels[ch] = { time: packet.time, values: packet.values, axes, unit: packet.unit || '', source: packet.source };
    }

    // Throttle render: at most every 200ms for live, 80ms for stored
    const interval = packet.source === 'live' ? 200 : 80;
    if (!this._debounceTimer) {
      this._debounceTimer = setTimeout(() => {
        this._debounceTimer = null;
        this._render();
      }, interval);
    }
  }

  /** Switch to live mode: clear stored channels, prepare for live data */
  enterLiveMode(channels) {
    this._channels = {};
    this._liveMode = true;
    if (this._chart) { this._chart.dispose(); this._chart = null; }
    this.container.innerHTML = '<div style="padding:1rem;text-align:center;color:#999;font-size:0.8rem">Waiting for live data...</div>';
  }

  /** Switch to stored mode */
  enterStoredMode() {
    this._liveMode = false;
    // Stored data will arrive via update() and trigger render
  }

  clear() {
    this._channels = {};
    if (this._chart) { this._chart.dispose(); this._chart = null; }
    this.container.innerHTML = '';
  }

  setViewRange(startUtcS, endUtcS) {
    if (!this._chart) return;
    this._chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100,
      dataZoomIndex: [0, 1], startValue: startUtcS * 1000, endValue: endUtcS * 1000 });
  }

  captureScreenshot() {
    if (!this._chart) return;
    const url = this._chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
    const a = document.createElement('a');
    a.href = url;
    a.download = `movesense-${new Date().toISOString().slice(0,19)}.png`;
    a.click();
  }

  _render() {
    const chNames = Object.keys(this._channels);
    if (!chNames.length) {
      if (this._chart) { this._chart.dispose(); this._chart = null; }
      this.container.innerHTML = '<div style="padding:2rem;text-align:center;color:#999">No data</div>';
      return;
    }

    if (!this._chart) {
      this.container.innerHTML = '';
      this._chart = echarts.init(this.container, null, { height: Math.max(300, 130 * chNames.length + 80) });
      const self = this;
      this._chart.on('datazoom', function(params) {
        if (self._suppressZoomEvent) return;
        const opt = self._chart.getOption();
        const dz = opt.dataZoom[0];
        if (dz && self.onViewChange) {
          self.onViewChange(dz.startValue / 1000, dz.endValue / 1000);
        }
      });
      // Resize on window resize
      window.addEventListener('resize', () => { if (self._chart) self._chart.resize(); });
    }

    // Resize if channel count changed
    this._chart.resize({ height: Math.max(300, 130 * chNames.length + 80) });

    // Build ECharts option
    const grids = [];
    const xAxes = [];
    const yAxes = [];
    const series = [];
    const axisLabels = { 3: ['x','y','z'], 9: ['Ax','Ay','Az','Gx','Gy','Gz','Mx','My','Mz'] };

    const gridHeight = Math.max(60, Math.min(120, (this.container.clientHeight - 80) / chNames.length));
    let colorIdx = 0;

    // Compute global time range across ALL channels for aligned X axes
    let globalMinT = Infinity, globalMaxT = -Infinity;
    for (const ch of Object.values(this._channels)) {
      if (ch.time.length > 0) {
        const validTimes = ch.time.filter(t => t != null);
        if (validTimes.length > 0) {
          globalMinT = Math.min(globalMinT, validTimes[0] * 1000);
          globalMaxT = Math.max(globalMaxT, validTimes[validTimes.length - 1] * 1000);
        }
      }
    }

    chNames.forEach((name, idx) => {
      const ch = this._channels[name];
      const top = 10 + idx * (gridHeight + 20);

      grids.push({ left: 60, right: 20, top, height: gridHeight });

      xAxes.push({
        type: 'time',
        gridIndex: idx,
        min: globalMinT !== Infinity ? globalMinT : undefined,
        max: globalMaxT !== -Infinity ? globalMaxT : undefined,
        show: idx === chNames.length - 1,
        axisLabel: { show: idx === chNames.length - 1, fontSize: 9 },
        axisTick: { show: idx === chNames.length - 1 },
        splitLine: { show: true, lineStyle: { color: '#f0f0f0' } },
      });

      const shortName = name.split('/').pop() || name;
      yAxes.push({
        type: 'value',
        gridIndex: idx,
        name: `${shortName}\n${ch.unit ? '(' + ch.unit + ')' : ''}`,
        nameLocation: 'middle',
        nameGap: 40,
        nameTextStyle: { fontSize: 9, color: VC_COLORS[idx % VC_COLORS.length] },
        axisLabel: { fontSize: 8 },
        splitLine: { show: true, lineStyle: { color: '#f8f8f8' } },
      });

      // Build series data
      if (ch.axes > 1) {
        const labels = axisLabels[ch.axes] || Array.from({length: ch.axes}, (_, i) => `ch${i}`);
        for (let a = 0; a < ch.axes; a++) {
          const data = ch.time.map((t, i) => {
            const v = ch.values[i];
            if (v == null) return [t * 1000, null];
            return [t * 1000, Array.isArray(v) ? v[a] : v];
          });
          series.push({
            type: 'line', name: `${shortName} ${labels[a]}`,
            xAxisIndex: idx, yAxisIndex: idx,
            data, symbol: 'none', lineStyle: { width: 1 },
            color: VC_COLORS[(colorIdx + a) % VC_COLORS.length],
            connectNulls: false,
          });
        }
        colorIdx += ch.axes;
      } else {
        const data = ch.time.map((t, i) => [t * 1000, ch.values[i]]);  // ECharts time axis uses ms
        series.push({
          type: 'line', name: shortName,
          xAxisIndex: idx, yAxisIndex: idx,
          data, symbol: 'none', lineStyle: { width: 1 },
          color: VC_COLORS[colorIdx % VC_COLORS.length],
          connectNulls: false,
        });
        colorIdx++;
      }
    });

    // Add session block markers to the first channel row (visual context)
    if (this._sessions && this._sessions.length > 0) {
      const sessionColors = ['rgba(59,130,246,0.08)', 'rgba(34,197,94,0.08)', 'rgba(245,158,11,0.08)', 'rgba(139,92,246,0.08)', 'rgba(239,68,68,0.08)'];
      const markAreaData = this._sessions.filter(s => s.start_us > 0).map((s, i) => [{
        xAxis: s.start_us / 1000, // ms for ECharts
        itemStyle: { color: sessionColors[i % sessionColors.length] },
      }, {
        xAxis: (s.end_us || s.start_us + 60000000) / 1000,
      }]);
      if (series.length > 0 && markAreaData.length > 0) {
        series[0].markArea = { silent: true, data: markAreaData };
      }
    }

    // In live mode: set X-axis to sliding window, hide dataZoom
    if (this._liveMode) {
      let maxT = -Infinity;
      for (const ch of Object.values(this._channels)) {
        if (ch.time.length > 0) maxT = Math.max(maxT, ch.time[ch.time.length - 1] * 1000);
      }
      const windowMs = (this.liveWindowSeconds || 30) * 1000;
      if (maxT > -Infinity) {
        for (const xa of xAxes) {
          xa.min = maxT - windowMs;
          xa.max = maxT;
        }
      }
    }

    const option = {
      animation: false,
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      series,
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      dataZoom: this._liveMode ? [] : [
        { type: 'slider', xAxisIndex: xAxes.map((_, i) => i), bottom: 5, height: 25,
          showDataShadow: true, filterMode: 'none',
          labelFormatter: (val) => {
            const d = new Date(val);
            return d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' ' + d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
          }
        },
        { type: 'inside', xAxisIndex: xAxes.map((_, i) => i), filterMode: 'none' },
      ],
    };

    // Preserve dataZoom range across re-renders
    let savedZoom = null;
    const currentOpt = this._chart.getOption();
    if (currentOpt && currentOpt.dataZoom && currentOpt.dataZoom.length > 0) {
      const dz = currentOpt.dataZoom[0];
      if (dz && (dz.start !== 0 || dz.end !== 100)) {
        savedZoom = { startValue: dz.startValue, endValue: dz.endValue };
      }
    }

    this._chart.setOption(option, true);

    // Restore zoom state (suppress event to prevent loop)
    if (savedZoom) {
      this._suppressZoomEvent = true;
      this._chart.dispatchAction({
        type: 'dataZoom', dataZoomIndex: 0,
        startValue: savedZoom.startValue, endValue: savedZoom.endValue,
      });
      this._chart.dispatchAction({
        type: 'dataZoom', dataZoomIndex: 1,
        startValue: savedZoom.startValue, endValue: savedZoom.endValue,
      });
      setTimeout(() => { this._suppressZoomEvent = false; }, 100);
    }
  }
}

// --- ControlPanel: built from server metadata ---

class ControlPanel {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.metadata = null;
    this.selectedChannels = new Set();
    this.onChannelToggle = null;
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

    if (m.sessions && m.sessions.length > 0) {
      html += `<div style="font-size:0.7rem;color:#999;margin-bottom:0.25rem;">${m.sessions.length} sessions</div>`;
    }

    html += '<div style="display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center;margin-bottom:0.5rem;">';
    html += '<span style="font-size:0.8rem;font-weight:600;">Channels:</span>';
    for (const ch of m.channels) {
      const checked = this.selectedChannels.has(ch.name) ? 'checked' : '';
      const rate = ch.rate_hz ? ` ${Math.round(ch.rate_hz)}Hz` : '';
      html += `<label style="font-size:0.8rem;cursor:pointer;white-space:nowrap;">
        <input type="checkbox" ${checked} onchange="controlPanel._toggle('${ch.name}', this.checked)">
        ${ch.name.split('/').pop()}${rate ? '<span style="color:#999;font-size:0.7rem">'+rate+'</span>' : ''}
      </label>`;
    }
    html += '</div>';

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
    const el = this.container?.querySelector('span[style*="margin-left"]');
    if (el) el.textContent = `● ${status.state || 'unknown'}`;
  }
}

window.ViewerClient = ViewerClient;
window.ChartRenderer = ChartRenderer;
window.ControlPanel = ControlPanel;
