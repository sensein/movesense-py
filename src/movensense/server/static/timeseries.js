// Multi-scale synchronized time series viewer using uPlot
class TimeSeriesViewer {
  constructor(containerId, apiFetch) {
    this.container = document.getElementById(containerId);
    this.apiFetch = apiFetch;
    this.plots = [];     // uPlot instances
    this.channels = [];  // channel metadata
    this.session = null; // {serial, date, logId}
    this.viewRange = null; // [startSec, endSec] or null for full
    this._syncing = false;
  }

  async load(serial, date, logId) {
    this.session = { serial, date, logId };
    this.container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted)">Loading channels...</div>';

    try {
      const meta = await this.apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels`);
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

    // Controls
    const controls = document.createElement('div');
    controls.className = 'stream-controls';
    controls.innerHTML = `
      <button onclick="tsViewer.resetZoom()">Reset Zoom</button>
      <span style="font-size:0.8rem;color:var(--muted)" id="ts-range-label">Full recording</span>
    `;
    this.container.appendChild(controls);

    // Create a chart for each channel
    for (const ch of this.channels) {
      const wrapper = document.createElement('div');
      wrapper.className = 'chart-wrapper';
      wrapper.style.marginBottom = '0.5rem';

      const label = document.createElement('div');
      label.style.cssText = 'font-size:0.8rem;color:var(--muted);display:flex;justify-content:space-between;';
      label.innerHTML = `<span><strong>${ch.name}</strong> ${ch.sensor_type || ''}</span><span>${ch.sampling_rate_hz || '?'}Hz | ${ch.sample_count} samples</span>`;
      wrapper.appendChild(label);

      const chartEl = document.createElement('div');
      chartEl.id = `ts-${ch.name}`;
      wrapper.appendChild(chartEl);

      const valueLabel = document.createElement('div');
      valueLabel.id = `ts-val-${ch.name}`;
      valueLabel.style.cssText = 'font-size:0.75rem;color:var(--muted);height:1rem;';
      wrapper.appendChild(valueLabel);

      this.container.appendChild(wrapper);
    }

    // Load data for all channels
    await this._loadData();
  }

  async _loadData() {
    const { serial, date, logId } = this.session;

    for (let i = 0; i < this.channels.length; i++) {
      const ch = this.channels[i];
      const el = document.getElementById(`ts-${ch.name}`);
      if (!el) continue;

      const width = el.parentElement.clientWidth || 800;
      const buckets = Math.min(width * 2, 2000); // 2 points per pixel

      let url = `/devices/${serial}/dates/${date}/sessions/${logId}/channels/${ch.name}/downsample?buckets=${buckets}`;
      if (this.viewRange) {
        url += `&start=${this.viewRange[0]}&end=${this.viewRange[1]}`;
      }

      try {
        const ds = await this.apiFetch(url);
        this._createChart(el, ch, ds, i);
      } catch (e) {
        el.innerHTML = `<div class="error">${e.message}</div>`;
      }
    }
  }

  _createChart(el, ch, ds, index) {
    el.innerHTML = '';
    const width = el.parentElement.clientWidth || 800;
    const height = 150;
    const data = ds.data;

    let series = [{ label: 'Time' }];
    let plotData = [data.time];

    if (data.values) {
      // Raw data mode
      if (Array.isArray(data.values[0])) {
        // Multi-axis raw
        const cols = ds.columns || ['x', 'y', 'z'];
        for (let c = 0; c < cols.length; c++) {
          series.push({ label: cols[c], stroke: ['#ef4444', '#22c55e', '#3b82f6'][c] || '#888' });
          plotData.push(data.values.map(r => r[c]));
        }
      } else {
        series.push({ label: ch.name, stroke: '#2563eb' });
        plotData.push(data.values);
      }
    } else if (data.min) {
      // Downsampled 1D — show as min/max band + mean line
      series.push({ label: 'mean', stroke: '#2563eb', width: 1 });
      plotData.push(data.mean);
      // We could show min/max as a band but uPlot doesn't have native bands,
      // so show mean only for now (future: custom drawing)
    } else if (ds.columns) {
      // Downsampled multi-axis — show means
      const cols = ds.columns;
      for (const col of cols) {
        series.push({ label: col, stroke: col === 'x' ? '#ef4444' : col === 'y' ? '#22c55e' : '#3b82f6' });
        plotData.push(data[`${col}_mean`]);
      }
    }

    const self = this;
    const opts = {
      width,
      height,
      series,
      scales: { x: { time: false } },
      axes: [
        { label: 'Time (s)', size: 40 },
        { label: ch.unit || '', size: 50 },
      ],
      cursor: {
        lock: true,
        sync: { key: 'timeseries-sync', setSeries: false },
      },
      hooks: {
        setSelect: [
          (u) => {
            if (self._syncing) return;
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
      const plot = new uPlot(opts, plotData, el);
      this.plots.push(plot);

      // Crosshair value display
      plot.over.addEventListener('mousemove', (e) => {
        const valEl = document.getElementById(`ts-val-${ch.name}`);
        if (!valEl) return;
        const idx = plot.cursor.idx;
        if (idx != null && plotData[1] && plotData[1][idx] != null) {
          const t = plotData[0][idx].toFixed(3);
          const v = plotData[1][idx].toFixed(4);
          valEl.textContent = `t=${t}s  value=${v}`;
        }
      });
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
