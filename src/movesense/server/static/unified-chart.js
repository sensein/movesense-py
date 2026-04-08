/**
 * UnifiedChart: stacked multi-channel display with shared X-axis.
 * Replaces channel-viewer.js (Data Browser) and stream.js (Live Stream).
 *
 * Architecture: single uPlot instance with multiple Y-axes (one per channel row),
 * shared X-axis at bottom. Supports static mode (timeline API) and live mode (WebSocket).
 */

const UC_COLORS = ['#ef4444','#22c55e','#3b82f6','#f59e0b','#8b5cf6','#06b6d4','#ec4899','#14b8a6','#64748b',
                   '#dc2626','#16a34a','#2563eb','#d97706','#7c3aed','#0891b2','#db2777','#0d9488','#475569'];
const UC_AXIS_LABELS = { 3: ['x','y','z'], 6: ['Acc-x','Acc-y','Acc-z','Gyro-x','Gyro-y','Gyro-z'],
  9: ['Acc-x','Acc-y','Acc-z','Gyro-x','Gyro-y','Gyro-z','Mag-x','Mag-y','Mag-z'] };

class UnifiedChart {
  /**
   * @param {string} containerId - DOM element ID for the chart container
   * @param {object} options - { mode: 'static'|'live', windowSeconds: 10, onZoomChange: fn }
   */
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    this.mode = options.mode || 'static';
    this.windowSeconds = options.windowSeconds || 10;
    this.onZoomChange = options.onZoomChange || null;

    this._plot = null;
    this._channels = [];      // [{name, unit, axes, seriesIndices, yAxisIdx, visible, axisVisible[]}]
    this._data = [[]];        // uPlot data: [time, series1, series2, ...]
    this._seriesConfig = [];  // uPlot series config
    this._axesConfig = [];    // uPlot axes config
    this._paused = false;
    this._zoomRange = null;   // [startS, endS] or null
    this._gaps = [];          // [{startS, endS}] for gray gap regions
    this._colorIdx = 0;

    // Live mode timing
    this._wallStartMs = null;
    this._channelOrigins = {}; // channel → {firstTs, wallOffset, scale}
  }

  // --- Static mode ---

  /**
   * Load timeline API segments (static mode).
   * @param {Array} segments - [{session_index, start_utc_us, data: {time, values|x,y,z,...}, rate_hz}, {type:'gap',...}]
   * @param {Array} channelNames - which channels are being loaded
   */
  loadSegments(segments, channelNames) {
    this._channels = [];
    this._gaps = [];
    this._colorIdx = 0;

    // Collect data and gaps
    const channelData = {}; // name → {time:[], series:[[],...]}
    let timeOffset = 0;

    for (const seg of segments) {
      if (seg.type === 'gap') {
        const gapStart = timeOffset;
        timeOffset += seg.duration_seconds || 0;
        this._gaps.push({ startS: gapStart, endS: timeOffset });
        continue;
      }

      const data = seg.data;
      if (!data || !data.time || data.time.length === 0) continue;

      const segDuration = data.time[data.time.length - 1] - data.time[0];
      const channelName = seg.channel || channelNames[0] || 'data';

      if (!channelData[channelName]) {
        channelData[channelName] = { time: [], values: [] };
      }

      // Offset times to create continuous axis
      for (let i = 0; i < data.time.length; i++) {
        channelData[channelName].time.push(data.time[i] + timeOffset);
      }

      // Collect values
      if (data.values) {
        channelData[channelName].values.push(...data.values);
        channelData[channelName].axes = 1;
      } else if (data.columns) {
        if (!channelData[channelName].colData) channelData[channelName].colData = {};
        for (const col of data.columns) {
          if (!channelData[channelName].colData[col]) channelData[channelName].colData[col] = [];
          channelData[channelName].colData[col].push(...(data[col] || []));
        }
        channelData[channelName].axes = data.columns.length;
        channelData[channelName].columns = data.columns;
      }

      timeOffset += segDuration;
    }

    // Build unified time array + channel series
    this._buildFromChannelData(channelData);
    this._render();
  }

  // --- Live mode ---

  /**
   * Add live data for a channel.
   * @param {string} channel - channel path
   * @param {Array} values - flat array or array of arrays
   * @param {number} tSeconds - seconds since stream start (server-converted)
   * @param {string} unit - unit string
   * @param {number} axes - number of axes (1, 3, 6, 9)
   */
  addData(channel, values, tSeconds, unit, axes) {
    if (this._paused) return;
    if (!values || values.length === 0) return;

    const numAxes = (axes && axes > 1) ? axes : (Array.isArray(values[0]) ? values[0].length : 1);

    // IMU6/9: split into sub-channels
    if (numAxes >= 6 && Array.isArray(values[0])) {
      const subs = numAxes === 9
        ? [{sfx:' Acc',u:'m/s²',s:[0,3]},{sfx:' Gyro',u:'dps',s:[3,6]},{sfx:' Mag',u:'µT',s:[6,9]}]
        : [{sfx:' Acc',u:'m/s²',s:[0,3]},{sfx:' Gyro',u:'dps',s:[3,6]}];
      for (const sub of subs) {
        this.addData(channel+sub.sfx, values.map(r => r.slice(sub.s[0], sub.s[1])), tSeconds, sub.u, 3);
      }
      return;
    }

    // Find or create channel entry
    let chIdx = this._channels.findIndex(c => c.name === channel);
    if (chIdx === -1) {
      this._addChannel(channel, unit || '', numAxes);
      chIdx = this._channels.length - 1;
    }

    const ch = this._channels[chIdx];
    const rate = this._estimateRate(channel);
    const dt = 1 / rate;
    const t0 = tSeconds;

    // Append data
    if (numAxes > 1 && Array.isArray(values[0])) {
      for (let s = 0; s < values.length; s++) {
        const row = values[s];
        this._data[0].push(t0 + s * dt);
        for (let a = 0; a < ch.seriesIndices.length; a++) {
          const si = ch.seriesIndices[a];
          if (a < row.length) this._data[si].push(row[a]);
          else this._data[si].push(0);
        }
        // Fill zeros for other channels' series
        for (const other of this._channels) {
          if (other.name === channel) continue;
          for (const si of other.seriesIndices) {
            this._data[si].push(null);
          }
        }
      }
    } else {
      for (let s = 0; s < values.length; s++) {
        this._data[0].push(t0 + s * dt);
        this._data[ch.seriesIndices[0]].push(values[s]);
        // Null for other channels
        for (const other of this._channels) {
          if (other.name === channel) continue;
          for (const si of other.seriesIndices) {
            this._data[si].push(null);
          }
        }
      }
    }

    // Trim to window
    if (this._data[0].length > 0) {
      const latest = this._data[0][this._data[0].length - 1];
      const cutoff = latest - this.windowSeconds;
      let trimIdx = 0;
      while (trimIdx < this._data[0].length && this._data[0][trimIdx] < cutoff) trimIdx++;
      if (trimIdx > 0) {
        for (let i = 0; i < this._data.length; i++) {
          this._data[i] = this._data[i].slice(trimIdx);
        }
      }
    }

    // Update chart
    if (this._plot) {
      this._plot.setData(this._data);
    } else {
      this._render();
    }
  }

  // --- Common methods ---

  setChannelVisible(channel, show) {
    const ch = this._channels.find(c => c.name === channel);
    if (ch) {
      ch.visible = show;
      this._render(); // Recreate with updated axes
    }
  }

  setAxisVisibility(channel, axisIndex, show) {
    const ch = this._channels.find(c => c.name === channel);
    if (ch && axisIndex < ch.axisVisible.length) {
      ch.axisVisible[axisIndex] = show;
      if (this._plot) {
        const seriesIdx = ch.seriesIndices[axisIndex];
        this._plot.setSeries(seriesIdx, { show });
      }
    }
  }

  setWindow(seconds) {
    this.windowSeconds = seconds;
  }

  pause() { this._paused = true; }
  resume() { this._paused = false; }

  async captureScreenshot() {
    if (!this._plot) return null;
    const canvas = this._plot.ctx.canvas;
    return new Promise(resolve => {
      canvas.toBlob(blob => {
        if (blob) {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `movesense-chart-${new Date().toISOString().slice(0,19)}.png`;
          a.click();
          URL.revokeObjectURL(url);
        }
        resolve(blob);
      }, 'image/png');
    });
  }

  resize() {
    if (this._plot && this.container) {
      this._plot.setSize({ width: this.container.clientWidth, height: this._calcHeight() });
    }
  }

  clear() {
    if (this._plot) { this._plot.destroy(); this._plot = null; }
    this.container.innerHTML = '';
    this._channels = [];
    this._data = [[]];
    this._gaps = [];
    this._colorIdx = 0;
    this._channelOrigins = {};
    this._wallStartMs = null;
  }

  // --- Internal ---

  _addChannel(name, unit, numAxes) {
    const labels = UC_AXIS_LABELS[numAxes] || (numAxes === 1 ? [name.split('/').pop() || name] : Array.from({length: numAxes}, (_, i) => `ch${i}`));
    const seriesIndices = [];
    const axisVisible = [];

    for (let a = 0; a < numAxes; a++) {
      this._data.push([]);
      seriesIndices.push(this._data.length - 1);
      axisVisible.push(true);
    }

    this._channels.push({
      name, unit, axes: numAxes, labels,
      seriesIndices, axisVisible,
      visible: true,
      yAxisIdx: -1, // set during render
      color: UC_COLORS[this._colorIdx % UC_COLORS.length],
    });
    this._colorIdx++;
  }

  _buildFromChannelData(channelData) {
    this._channels = [];
    this._data = [[]];
    this._colorIdx = 0;

    // Merge all time points
    let allTimes = new Set();
    for (const [name, cd] of Object.entries(channelData)) {
      for (const t of cd.time) allTimes.add(t);
    }
    const sortedTimes = [...allTimes].sort((a, b) => a - b);
    this._data[0] = sortedTimes;

    // Build channel series
    for (const [name, cd] of Object.entries(channelData)) {
      const numAxes = cd.axes || 1;
      const labels = UC_AXIS_LABELS[numAxes] || (numAxes === 1 ? [name.split('/').pop() || name] : cd.columns || ['ch0','ch1','ch2']);
      const seriesIndices = [];
      const axisVisible = [];

      // Create time→index lookup for this channel
      const timeToIdx = new Map();
      for (let i = 0; i < cd.time.length; i++) timeToIdx.set(cd.time[i], i);

      for (let a = 0; a < numAxes; a++) {
        const series = new Array(sortedTimes.length).fill(null);
        for (let ti = 0; ti < sortedTimes.length; ti++) {
          const srcIdx = timeToIdx.get(sortedTimes[ti]);
          if (srcIdx !== undefined) {
            if (numAxes === 1 && cd.values) {
              series[ti] = cd.values[srcIdx];
            } else if (cd.colData) {
              const col = (cd.columns || [])[a];
              if (col && cd.colData[col]) series[ti] = cd.colData[col][srcIdx];
            }
          }
        }
        this._data.push(series);
        seriesIndices.push(this._data.length - 1);
        axisVisible.push(true);
      }

      this._channels.push({
        name, unit: cd.unit || '', axes: numAxes, labels,
        seriesIndices, axisVisible,
        visible: true, yAxisIdx: -1,
        color: UC_COLORS[this._colorIdx % UC_COLORS.length],
      });
      this._colorIdx++;
    }
  }

  _calcHeight() {
    const visibleCount = this._channels.filter(c => c.visible).length;
    return Math.max(200, Math.min(160 * visibleCount + 40, 800));
  }

  _estimateRate(channel) {
    const ch = channel.toLowerCase();
    const m = channel.match(/\/(\d+)/);
    if (m) return parseInt(m[1]);
    if (ch.includes('ecg')) return 200;
    if (ch.includes('imu') || ch.includes('acc') || ch.includes('gyro') || ch.includes('magn')) return 52;
    if (ch.includes('hr')) return 1;
    if (ch.includes('temp')) return 1;
    return 10;
  }

  _render() {
    if (this._plot) { this._plot.destroy(); this._plot = null; }
    this.container.innerHTML = '';

    const visibleChannels = this._channels.filter(c => c.visible);
    if (visibleChannels.length === 0 || this._data[0].length === 0) {
      this.container.innerHTML = '<div style="padding:2rem;text-align:center;color:#999">No data</div>';
      return;
    }

    const width = this.container.clientWidth || 800;
    const height = this._calcHeight();
    const self = this;

    // Build series config
    const series = [{ label: 'Time (s)' }];
    const scales = { x: { time: false } };
    const axes = [{ stroke: '#333', grid: { stroke: '#eee' }, size: 30, font: '10px sans-serif', label: 'Time (s)' }];

    // Assign Y-axes: one per visible channel, stacked via scale names
    let yAxisCount = 0;
    for (const ch of visibleChannels) {
      const scaleName = `y_${ch.name.replace(/[^a-zA-Z0-9]/g, '_')}`;
      ch.yAxisIdx = yAxisCount + 1; // axes[0] is X
      scales[scaleName] = { auto: true };

      axes.push({
        scale: scaleName,
        stroke: ch.color,
        grid: { stroke: yAxisCount === 0 ? '#f0f0f0' : 'transparent' }, // Only first channel gets grid
        size: 50,
        font: '9px sans-serif',
        label: `${ch.name.split('/').pop()} ${ch.unit ? '(' + ch.unit + ')' : ''}`,
        labelFont: '9px sans-serif',
        labelSize: 12,
      });

      for (let a = 0; a < ch.axes; a++) {
        const si = ch.seriesIndices[a];
        const colorBase = UC_COLORS[(this._channels.indexOf(ch) * 3 + a) % UC_COLORS.length];
        series[si] = {
          label: ch.labels[a],
          scale: scaleName,
          stroke: colorBase,
          width: 1,
          show: ch.axisVisible[a],
          spanGaps: true,
        };
      }

      yAxisCount++;
    }

    // Fill missing series slots
    for (let i = 1; i < this._data.length; i++) {
      if (!series[i]) {
        series[i] = { show: false, label: '', scale: 'y_hidden' };
      }
    }
    if (!scales['y_hidden']) scales['y_hidden'] = { auto: true };

    const opts = {
      width, height, series, scales, axes,
      cursor: { drag: { x: false, y: false } },
      select: { show: true },
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width < 5) return;
            const left = u.posToVal(u.select.left, 'x');
            const right = u.posToVal(u.select.left + u.select.width, 'x');
            if (right - left > 0.001) {
              self._zoomRange = [left, right];
              u.setScale('x', { min: left, max: right });
              if (self.onZoomChange) self.onZoomChange(left, right);
            }
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
          }
        ],
        drawClear: [
          (u) => {
            // Draw gap regions as gray bands
            if (self._gaps.length === 0) return;
            const ctx = u.ctx;
            const xMin = u.scales.x.min;
            const xMax = u.scales.x.max;
            for (const gap of self._gaps) {
              if (gap.endS < xMin || gap.startS > xMax) continue;
              const left = Math.max(u.valToPos(gap.startS, 'x'), u.bbox.left);
              const right = Math.min(u.valToPos(gap.endS, 'x'), u.bbox.left + u.bbox.width);
              if (right > left) {
                ctx.fillStyle = 'rgba(0,0,0,0.06)';
                ctx.fillRect(left, u.bbox.top, right - left, u.bbox.height);
              }
            }
          }
        ],
      },
      legend: { show: true },
    };

    // Scroll wheel zoom
    const el = document.createElement('div');
    this.container.appendChild(el);

    this._plot = new uPlot(opts, this._data, el);

    el.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const xPct = (e.clientX - rect.left) / rect.width;
      const xMin = self._plot.scales.x.min;
      const xMax = self._plot.scales.x.max;
      const range = xMax - xMin;
      const factor = e.deltaY > 0 ? 1.3 : 0.7;
      const newRange = Math.max(0.1, range * factor);
      const center = xMin + range * xPct;
      const newMin = center - newRange * xPct;
      const newMax = center + newRange * (1 - xPct);
      self._plot.setScale('x', { min: newMin, max: newMax });
      self._zoomRange = [newMin, newMax];
      if (self.onZoomChange) self.onZoomChange(newMin, newMax);
    }, { passive: false });

    // Legend click-to-toggle
    const legendItems = el.querySelectorAll('.u-legend .u-series');
    legendItems.forEach((item, idx) => {
      if (idx === 0) return; // skip time series
      item.style.cursor = 'pointer';
      item.addEventListener('click', () => {
        const current = self._plot.series[idx].show;
        self._plot.setSeries(idx, { show: !current });
      });
    });
  }
}

window.UnifiedChart = UnifiedChart;
