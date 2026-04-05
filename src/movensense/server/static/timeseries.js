// Multi-scale synchronized time series viewer using uPlot
class TimeSeriesViewer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.plots = [];
    this.channels = [];
    this.session = null;
    this.viewRange = null;
    this._syncing = false;
  }

  async load(serial, date, logId) {
    this.session = { serial, date, logId };
    this.container.innerHTML = '<div style="text-align:center;padding:2rem;color:#999">Loading channels...</div>';
    try {
      const meta = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels`);
      this.channels = meta.channels;
      this.viewRange = null;
      await this._renderAll();
    } catch (e) {
      this.container.innerHTML = `<div class="error">${e.message}</div>`;
    }
  }

  async _renderAll() {
    this.container.innerHTML = '';
    this.plots = [];

    const controls = document.createElement('div');
    controls.className = 'stream-controls';
    controls.innerHTML = `
      <button onclick="window.tsViewer.resetZoom()">Reset Zoom</button>
      <span style="font-size:0.8rem;color:#999" id="ts-range-label">Full recording</span>
    `;
    this.container.appendChild(controls);

    for (const ch of this.channels) {
      const wrapper = document.createElement('div');
      wrapper.className = 'chart-wrapper';
      wrapper.style.marginBottom = '0.5rem';

      const label = document.createElement('div');
      label.style.cssText = 'font-size:0.8rem;color:#666;display:flex;justify-content:space-between;';
      label.innerHTML = `<span><strong>${ch.name}</strong> ${ch.sensor_type || ''}</span><span>${ch.sampling_rate_hz || '?'}Hz | ${ch.sample_count} samples</span>`;
      wrapper.appendChild(label);

      const chartEl = document.createElement('div');
      chartEl.id = `ts-${ch.name}`;
      wrapper.appendChild(chartEl);

      const valueLabel = document.createElement('div');
      valueLabel.id = `ts-val-${ch.name}`;
      valueLabel.style.cssText = 'font-size:0.75rem;color:#999;height:1.2rem;';
      wrapper.appendChild(valueLabel);

      this.container.appendChild(wrapper);
    }

    await this._loadData();
  }

  async _loadData() {
    const { serial, date, logId } = this.session;

    for (let i = 0; i < this.channels.length; i++) {
      const ch = this.channels[i];
      const el = document.getElementById(`ts-${ch.name}`);
      if (!el) continue;

      const width = el.parentElement.clientWidth || 800;
      // Request enough buckets for full resolution when zoomed in
      let buckets = Math.min(width * 2, 2000);
      if (this.viewRange && ch.sampling_rate_hz) {
        const visibleDuration = this.viewRange[1] - this.viewRange[0];
        const nativeSamples = Math.ceil(visibleDuration * ch.sampling_rate_hz);
        // If native samples fit in 10K, request raw resolution
        buckets = Math.min(nativeSamples, 10000);
      }

      let url = `/devices/${serial}/dates/${date}/sessions/${logId}/channels/${ch.name}/downsample?buckets=${buckets}`;
      if (this.viewRange) {
        url += `&start=${this.viewRange[0]}&end=${this.viewRange[1]}`;
      }

      try {
        const ds = await apiFetch(url);
        this._createChart(el, ch, ds);
      } catch (e) {
        el.innerHTML = `<div class="error">${e.message}</div>`;
      }
    }
  }

  _createChart(el, ch, ds) {
    el.innerHTML = '';
    const width = el.parentElement.clientWidth || 800;
    const height = 160;
    const data = ds.data;

    if (!data || !data.time || data.time.length === 0) {
      el.innerHTML = '<div style="padding:1rem;color:#999">No data</div>';
      return;
    }

    const timeArr = data.time;
    let series = [{}]; // first series = x axis (no label needed)
    let plotData = [timeArr];

    const colors = ['#ef4444', '#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#14b8a6', '#a855f7'];

    if (ds.columns && ds.columns.length > 0) {
      // Multi-axis data (raw or downsampled)
      for (let c = 0; c < ds.columns.length; c++) {
        const col = ds.columns[c];
        // Try raw column first (e.g., data.x), then downsampled (e.g., data.x_mean)
        const arr = data[col] || data[`${col}_mean`];
        if (arr && arr.length === timeArr.length) {
          series.push({ label: col, stroke: colors[c % colors.length], width: 1 });
          plotData.push(arr);
        }
      }
    } else if (data.values) {
      // 1D raw data
      series.push({ label: ch.name, stroke: '#2563eb', width: 1 });
      plotData.push(data.values);
    } else if (data.mean) {
      // 1D downsampled
      series.push({ label: ch.name, stroke: '#2563eb', width: 1 });
      plotData.push(data.mean);
    }

    // Validate: all arrays must be same length
    const len = plotData[0].length;
    for (let s = 1; s < plotData.length; s++) {
      if (!plotData[s] || plotData[s].length !== len) {
        el.innerHTML = '<div style="padding:1rem;color:#999">Data length mismatch</div>';
        return;
      }
    }

    if (plotData.length < 2) {
      el.innerHTML = '<div style="padding:1rem;color:#999">No plottable data</div>';
      return;
    }

    const self = this;
    const opts = {
      width,
      height,
      series,
      scales: { x: { time: false } },
      axes: [
        { stroke: '#333', grid: { stroke: '#eee' }, size: 40 },
        { stroke: '#333', grid: { stroke: '#eee' }, size: 55, label: ch.unit || '' },
      ],
      cursor: {
        sync: { key: 'ts-sync', setSeries: false },
        drag: { x: true, y: false, setScale: false },
      },
      select: { show: true },
      hooks: {
        setSelect: [
          (u) => {
            if (self._syncing) return;
            if (u.select.width < 5) return; // ignore tiny drags
            const left = u.posToVal(u.select.left, 'x');
            const right = u.posToVal(u.select.left + u.select.width, 'x');
            if (right - left > 0.001) {
              self._syncing = true;
              self.viewRange = [left, right];
              self._updateRangeLabel();
              self._loadData().then(() => { self._syncing = false; });
            }
          }
        ],
      },
    };

    if (typeof uPlot !== 'undefined') {
      try {
        const plot = new uPlot(opts, plotData, el);
        this.plots.push(plot);
      } catch (e) {
        console.error(`Chart error for ${ch.name}:`, e);
        el.innerHTML = `<div style="padding:1rem;color:#c00">Chart error: ${e.message}</div>`;
      }
    }
  }

  async resetZoom() {
    this.viewRange = null;
    this._updateRangeLabel();
    await this._loadData();
  }

  _updateRangeLabel() {
    const el = document.getElementById('ts-range-label');
    if (!el) return;
    if (this.viewRange) {
      el.textContent = `${this.viewRange[0].toFixed(2)}s — ${this.viewRange[1].toFixed(2)}s`;
    } else {
      el.textContent = 'Full recording';
    }
  }
}

window.TimeSeriesViewer = TimeSeriesViewer;
