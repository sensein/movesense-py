// Interactive Channel Viewer — channel selector + synchronized charts + analytics
// Requires: uPlot, apiFetch (from global init in index.html)

class ChannelViewer {
  constructor(selectorId, chartsId, statsId) {
    this.selectorEl = document.getElementById(selectorId);
    this.chartsEl = document.getElementById(chartsId);
    this.statsEl = document.getElementById(statsId);
    this.session = null;
    this.channels = [];     // all channel metadata
    this.visible = new Set(); // visible channel names
    this.plots = {};        // name → uPlot instance
    this.plotData = {};     // name → data arrays
    this.viewRange = null;  // [startS, endS] or null
    this._syncing = false;
    this._statsTimer = null;
  }

  async load(serial, date, logId) {
    this.session = { serial, date, logId };
    this.plots = {};
    this.plotData = {};
    this.viewRange = null;

    try {
      const meta = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels`);
      this.channels = meta.channels;
      // Default: all visible
      this.visible = new Set(this.channels.map(c => c.name));
      this._renderSelector();
      await this._renderCharts();
      this._requestStats();
    } catch (e) {
      this.chartsEl.innerHTML = `<div class="error">${e.message}</div>`;
    }
  }

  // --- Channel Selector ---

  _renderSelector() {
    // Group channels by sensor type
    const groups = {};
    for (const ch of this.channels) {
      const type = ch.sensor_type || ch.name;
      if (!groups[type]) groups[type] = [];
      groups[type].push(ch);
    }

    let html = '<div style="margin-bottom:0.5rem"><button onclick="channelViewer.selectAll()">All</button> <button onclick="channelViewer.selectNone()">None</button></div>';

    for (const [type, chs] of Object.entries(groups)) {
      const allChecked = chs.every(c => this.visible.has(c.name));
      html += `<div class="ch-group">
        <label class="ch-group-label">
          <input type="checkbox" ${allChecked ? 'checked' : ''} onchange="channelViewer.toggleGroup('${type}', this.checked)">
          <strong>${type}</strong> (${chs.length})
        </label>`;

      for (const ch of chs) {
        const checked = this.visible.has(ch.name) ? 'checked' : '';
        const rate = ch.sampling_rate_hz ? `${ch.sampling_rate_hz}Hz` : '';
        html += `<label class="ch-item">
          <input type="checkbox" ${checked} onchange="channelViewer.toggleChannel('${ch.name}', this.checked)">
          ${ch.name} <span style="color:#999;font-size:0.75rem">${rate}</span>
        </label>`;
      }
      html += '</div>';
    }

    this.selectorEl.innerHTML = html;
  }

  selectAll() { this.channels.forEach(c => this.visible.add(c.name)); this._update(); }
  selectNone() { this.visible.clear(); this._update(); }
  toggleGroup(type, on) {
    for (const ch of this.channels) {
      if ((ch.sensor_type || ch.name) === type) {
        on ? this.visible.add(ch.name) : this.visible.delete(ch.name);
      }
    }
    this._update();
  }
  toggleChannel(name, on) {
    on ? this.visible.add(name) : this.visible.delete(name);
    this._update();
  }

  async _update() {
    this._renderSelector();
    await this._renderCharts();
    this._requestStats();
  }

  // --- Charts ---

  async _renderCharts() {
    this.chartsEl.innerHTML = '';
    this.plots = {};

    // Controls bar
    const bar = document.createElement('div');
    bar.className = 'stream-controls';
    bar.innerHTML = `<button onclick="channelViewer.resetZoom()">Reset Zoom</button>
      <span style="font-size:0.8rem;color:#999" id="cv-range">Full recording</span>`;
    this.chartsEl.appendChild(bar);

    const visibleChannels = this.channels.filter(c => this.visible.has(c.name));
    if (visibleChannels.length === 0) {
      this.chartsEl.innerHTML += '<div style="padding:2rem;text-align:center;color:#999">No channels selected</div>';
      return;
    }

    for (const ch of visibleChannels) {
      const wrapper = document.createElement('div');
      wrapper.className = 'chart-wrapper';
      wrapper.style.marginBottom = '4px';
      wrapper.innerHTML = `
        <div style="font-size:0.75rem;color:#666;display:flex;justify-content:space-between;">
          <span><strong>${ch.name}</strong> ${ch.sensor_type || ''}</span>
          <span>${ch.sampling_rate_hz || '?'}Hz | ${ch.sample_count} samples | <span id="cv-val-${ch.name}" style="color:#2563eb"></span></span>
        </div>
        <div id="cv-chart-${ch.name}"></div>`;
      this.chartsEl.appendChild(wrapper);
    }

    // Load data for each visible channel
    for (const ch of visibleChannels) {
      await this._loadChannel(ch);
    }
  }

  async _loadChannel(ch) {
    const { serial, date, logId } = this.session;
    const el = document.getElementById(`cv-chart-${ch.name}`);
    if (!el) return;

    const width = el.parentElement.clientWidth || 800;
    let buckets = Math.min(width * 2, 2000);
    if (this.viewRange && ch.sampling_rate_hz) {
      const dur = this.viewRange[1] - this.viewRange[0];
      buckets = Math.min(Math.ceil(dur * ch.sampling_rate_hz), 10000);
    }

    // Check pre-fetch cache first
    const cacheKey = `${ch.name}:${this.viewRange ? this.viewRange[0].toFixed(3) : 'full'}:${this.viewRange ? this.viewRange[1].toFixed(3) : 'full'}`;
    if (this._prefetchCache && this._prefetchCache[cacheKey]) {
      this._createChart(el, ch, this._prefetchCache[cacheKey]);
      this._triggerPrefetch(ch, buckets);
      return;
    }

    let url = `/devices/${serial}/dates/${date}/sessions/${logId}/channels/${ch.name}/downsample?buckets=${buckets}`;
    if (this.viewRange) url += `&start=${this.viewRange[0]}&end=${this.viewRange[1]}`;

    try {
      const ds = await apiFetch(url);
      this._createChart(el, ch, ds);
      // Trigger pre-fetch of adjacent windows
      this._triggerPrefetch(ch, buckets);
    } catch (e) {
      el.innerHTML = `<div class="error">${e.message}</div>`;
    }
  }

  _triggerPrefetch(ch, buckets) {
    if (!this.viewRange || !this.session) return;
    if (!this._prefetchCache) this._prefetchCache = {};

    const { serial, date, logId } = this.session;
    const [vStart, vEnd] = this.viewRange;
    const windowSize = vEnd - vStart;

    // Pre-fetch: one window ahead and one behind
    const ranges = [
      [vStart - windowSize, vStart],  // behind
      [vEnd, vEnd + windowSize],      // ahead
    ];

    for (const [pStart, pEnd] of ranges) {
      if (pStart < 0) continue;
      const key = `${ch.name}:${pStart.toFixed(3)}:${pEnd.toFixed(3)}`;
      if (this._prefetchCache[key]) continue;

      // Use requestIdleCallback if available, else setTimeout
      const fetchFn = () => {
        const url = `/devices/${serial}/dates/${date}/sessions/${logId}/channels/${ch.name}/downsample?buckets=${buckets}&start=${pStart}&end=${pEnd}`;
        apiFetch(url).then(ds => {
          this._prefetchCache[key] = ds;
        }).catch(() => {}); // silent fail for pre-fetch
      };

      if (typeof requestIdleCallback !== 'undefined') {
        requestIdleCallback(fetchFn);
      } else {
        setTimeout(fetchFn, 100);
      }
    }
  }

  _invalidatePrefetch() {
    this._prefetchCache = {};
  }

  _createChart(el, ch, ds) {
    el.innerHTML = '';
    const width = el.parentElement.clientWidth || 800;
    const height = 140;
    const data = ds.data;

    if (!data || !data.time || data.time.length === 0) {
      el.innerHTML = '<div style="padding:0.5rem;color:#999;font-size:0.8rem">No data</div>';
      return;
    }

    const colors = ['#2563eb', '#ef4444', '#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#14b8a6'];
    let series = [{}];
    let plotData = [data.time];

    if (ds.columns && ds.columns.length > 0) {
      for (let c = 0; c < ds.columns.length; c++) {
        const col = ds.columns[c];
        const arr = data[col] || data[`${col}_mean`];
        if (arr && arr.length === data.time.length) {
          series.push({ label: col, stroke: colors[(c + 1) % colors.length], width: 1 });
          plotData.push(arr);
        }
      }
    } else if (data.values) {
      series.push({ label: ch.name, stroke: colors[0], width: 1 });
      plotData.push(data.values);
    } else if (data.mean) {
      series.push({ label: ch.name, stroke: colors[0], width: 1 });
      plotData.push(data.mean);
    }

    if (plotData.length < 2) {
      el.innerHTML = '<div style="padding:0.5rem;color:#999;font-size:0.8rem">No plottable data</div>';
      return;
    }

    // Validate lengths
    const len = plotData[0].length;
    for (let i = 1; i < plotData.length; i++) {
      if (!plotData[i] || plotData[i].length !== len) {
        el.innerHTML = '<div style="padding:0.5rem;color:#c00;font-size:0.8rem">Data length mismatch</div>';
        return;
      }
    }

    const self = this;
    const opts = {
      width,
      height,
      series,
      scales: {
        x: { time: false },
        y: { auto: true }, // Y auto-scales per chart independently
      },
      axes: [
        { stroke: '#333', grid: { stroke: '#eee' }, size: 35, font: '10px sans-serif' },
        { stroke: '#333', grid: { stroke: '#f5f5f5' }, size: 50, font: '10px sans-serif', label: ch.unit || '' },
      ],
      cursor: {
        sync: { key: 'cv-sync', setSeries: false },
        drag: { x: true, y: true, setScale: true }, // X and Y zoom; only X syncs
      },
      select: { show: true },
      hooks: {
        setSelect: [
          (u) => {
            if (self._syncing) return;
            if (u.select.width < 5) return;
            const left = u.posToVal(u.select.left, 'x');
            const right = u.posToVal(u.select.left + u.select.width, 'x');
            if (right - left > 0.0001) {
              // X zoom is synchronized — invalidate prefetch if zoom changed significantly
              self._syncing = true;
              const oldRange = self.viewRange ? self.viewRange[1] - self.viewRange[0] : Infinity;
              const newRange = right - left;
              if (oldRange / newRange > 2 || newRange / oldRange > 2) {
                self._invalidatePrefetch();
              }
              self.viewRange = [left, right];
              self._updateRangeLabel();
              self._renderCharts().then(() => {
                self._syncing = false;
                self._requestStats();
              });
            }
          }
        ],
      },
    };

    try {
      const plot = new uPlot(opts, plotData, el);
      this.plots[ch.name] = plot;
      this.plotData[ch.name] = plotData;

      // Crosshair value display
      plot.over.addEventListener('mousemove', () => {
        const idx = plot.cursor.idx;
        const valEl = document.getElementById(`cv-val-${ch.name}`);
        if (!valEl || idx == null) return;
        const t = plotData[0][idx];
        const vals = [];
        for (let s = 1; s < plotData.length; s++) {
          if (plotData[s][idx] != null) vals.push(plotData[s][idx].toFixed(3));
        }
        valEl.textContent = `t=${t.toFixed(3)}s ${vals.join(', ')}`;
      });

      // Scroll wheel zoom (X only, synchronized)
      el.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (self._syncing) return;
        const rect = el.getBoundingClientRect();
        const xPct = (e.clientX - rect.left) / rect.width;
        const [xMin, xMax] = [plot.scales.x.min, plot.scales.x.max];
        const range = xMax - xMin;
        const factor = e.deltaY > 0 ? 1.3 : 0.7; // zoom out / in
        const newRange = range * factor;
        const center = xMin + range * xPct;
        const newMin = Math.max(0, center - newRange * xPct);
        const newMax = center + newRange * (1 - xPct);

        self._syncing = true;
        self.viewRange = [newMin, newMax];
        self._updateRangeLabel();
        self._renderCharts().then(() => {
          self._syncing = false;
          self._requestStats();
        });
      }, { passive: false });

    } catch (e) {
      console.error(`Chart error for ${ch.name}:`, e);
      el.innerHTML = `<div style="padding:0.5rem;color:#c00;font-size:0.8rem">Chart error: ${e.message}</div>`;
    }
  }

  resetZoom() {
    this.viewRange = null;
    this._updateRangeLabel();
    this._renderCharts().then(() => this._requestStats());
  }

  _updateRangeLabel() {
    const el = document.getElementById('cv-range');
    if (!el) return;
    el.textContent = this.viewRange
      ? `${this.viewRange[0].toFixed(2)}s — ${this.viewRange[1].toFixed(2)}s`
      : 'Full recording';
  }

  // --- Analytics Info Box ---

  _requestStats() {
    if (this._statsTimer) clearTimeout(this._statsTimer);
    this._statsTimer = setTimeout(() => this._loadStats(), 500); // debounce
  }

  async _loadStats() {
    if (!this.session || !this.statsEl) return;
    const { serial, date, logId } = this.session;
    let url = `/devices/${serial}/dates/${date}/sessions/${logId}/window-stats?start=${this.viewRange ? this.viewRange[0] : 0}`;
    if (this.viewRange) url += `&end=${this.viewRange[1]}`;

    this.statsEl.innerHTML = '<span style="color:#999">Computing stats...</span>';
    try {
      const stats = await apiFetch(url);
      this._renderStats(stats);
    } catch (e) {
      this.statsEl.innerHTML = `<span style="color:#c00">${e.message}</span>`;
    }
  }

  _renderStats(stats) {
    let html = '<div class="stats-grid">';
    const duration = stats.end ? (stats.end - stats.start).toFixed(1) : '?';

    for (const [name, ch] of Object.entries(stats.channels)) {
      if (!this.visible.has(name)) continue;
      const isECG = name.toLowerCase().includes('ecg');
      const isACC = name.toLowerCase().includes('acc');
      const isIMU = name.toLowerCase().includes('imu');
      const isGyro = name.toLowerCase().includes('gyro');
      const isTemp = name.toLowerCase().includes('temp');
      const isHR = name.toLowerCase().includes('hr');

      html += `<div class="stats-card"><strong>${name}</strong>`;
      html += `<span style="float:right;font-size:0.7rem;color:#999">${duration}s | ${ch.sample_count} @ ${ch.sampling_rate_hz}Hz</span><br>`;

      // ECG bio-metrics
      if (isECG) {
        if (ch.hr_bpm) {
          html += `<div style="margin:0.25rem 0">❤️ <b>${ch.hr_bpm}</b> bpm`;
          if (ch.r_peak_count) html += ` (${ch.r_peak_count} beats)`;
          html += `</div>`;
        }
        if (ch.hrv_sdnn || ch.hrv_rmssd) {
          html += `<div style="font-size:0.8rem">HRV: `;
          if (ch.hrv_sdnn) html += `SDNN=${ch.hrv_sdnn}ms `;
          if (ch.hrv_rmssd) html += `RMSSD=${ch.hrv_rmssd}ms `;
          if (ch.hrv_pnn50 != null) html += `pNN50=${ch.hrv_pnn50}%`;
          html += `</div>`;
        }
        if (ch.sqi != null) {
          const sqiColor = ch.sqi_level === 'high' ? '#22c55e' : ch.sqi_level === 'medium' ? '#f59e0b' : '#ef4444';
          html += `<div style="font-size:0.8rem">Signal Quality: <span style="color:${sqiColor}"><b>${ch.sqi_level}</b></span> (${ch.sqi})</div>`;
        }
        if (!ch.hr_bpm && !ch.hrv_sdnn) {
          html += `<div style="font-size:0.8rem;color:#999">Insufficient data for HR/HRV (need ≥2 beats)</div>`;
        }
        if (ch.min != null) {
          html += `<div style="font-size:0.75rem;color:#666">Range: ${ch.min} — ${ch.max} mV | Mean: ${ch.mean} | σ: ${ch.std}</div>`;
        }
      }

      // Accelerometer / IMU bio-metrics
      else if (isACC || isIMU) {
        if (ch.activity_pct != null) {
          const actColor = ch.activity_pct > 50 ? '#22c55e' : '#3b82f6';
          html += `<div style="margin:0.25rem 0">🏃 Activity: <b style="color:${actColor}">${ch.activity_pct}%</b> active, ${(100 - ch.activity_pct).toFixed(0)}% rest</div>`;
        }
        if (ch.magnitude_mean) {
          html += `<div style="font-size:0.8rem">Magnitude: ${ch.magnitude_mean} (mean)</div>`;
        }
        // Show axis summaries with proper labels
        const axisLabels = isIMU
          ? {x:'Acc-X', y:'Acc-Y', z:'Acc-Z', a:'Gyro-X', b:'Gyro-Y', c:'Gyro-Z', d:'Mag-X', e:'Mag-Y', f:'Mag-Z'}
          : {x:'X', y:'Y', z:'Z'};
        const axes = Object.keys(axisLabels).filter(a => ch[`${a}_mean`] != null);
        if (axes.length > 0) {
          html += '<div style="font-size:0.75rem;color:#666">';
          for (const a of axes) {
            html += `${axisLabels[a]}: μ=${ch[`${a}_mean`]} σ=${ch[`${a}_std`]} `;
          }
          html += '</div>';
        }
      }

      // Temperature
      else if (isTemp) {
        if (ch.min != null) {
          html += `<div style="margin:0.25rem 0">🌡️ <b>${ch.mean}</b> (${ch.min} — ${ch.max})</div>`;
        }
      }

      // Heart Rate
      else if (isHR) {
        if (ch.mean != null) {
          html += `<div style="margin:0.25rem 0">❤️ <b>${ch.mean}</b> bpm (${ch.min} — ${ch.max})</div>`;
        }
      }

      // Generic fallback
      else {
        if (ch.min != null) {
          html += `<div style="font-size:0.8rem">Range: ${ch.min} — ${ch.max} | Mean: ${ch.mean} | σ: ${ch.std}</div>`;
        }
        if (ch.magnitude_mean) html += `<div style="font-size:0.8rem">Magnitude: ${ch.magnitude_mean}</div>`;
      }

      html += '</div>';
    }

    html += '</div>';
    this.statsEl.innerHTML = html;
  }
}

window.ChannelViewer = ChannelViewer;
