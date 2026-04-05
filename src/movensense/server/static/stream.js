// Live streaming WebSocket client + chart manager
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
        else if (msg.type === 'status') this.onStatus(msg);
        else if (msg.type === 'error') this.onError(msg.message);
        else if (msg.type === 'device_info') this.onStatus(msg);
      } catch (err) {
        // Skip malformed JSON (e.g., NaN values from BLE)
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

  startStream(serial, channels) {
    this.send({ type: 'start', serial, channels });
  }

  stopStream() {
    this.send({ type: 'stop' });
  }

  disconnect() {
    if (this.ws) this.ws.close();
  }
}

// --- Chart Manager using uPlot ---

class ChartManager {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.charts = {};
    this.windowSeconds = 10;
    this.maxPoints = {};
    this._paused = false;
  }

  setWindow(seconds) {
    this.windowSeconds = seconds;
  }

  addData(channel, values, samplingRate) {
    if (this._paused) return;
    if (!this.charts[channel]) {
      this._createChart(channel, samplingRate || 200);
    }
    const chart = this.charts[channel];
    const dt = 1 / (samplingRate || 200);

    // Append new values
    if (Array.isArray(values[0])) {
      // Multi-axis
      for (const row of values) {
        const t = chart.data[0].length > 0 ? chart.data[0][chart.data[0].length - 1] + dt : 0;
        chart.data[0].push(t);
        for (let axis = 0; axis < row.length; axis++) {
          chart.data[axis + 1].push(row[axis]);
        }
      }
    } else {
      for (const val of values) {
        const t = chart.data[0].length > 0 ? chart.data[0][chart.data[0].length - 1] + dt : 0;
        chart.data[0].push(t);
        chart.data[1].push(val);
      }
    }

    // Trim to window
    const maxPts = Math.ceil(this.windowSeconds * (samplingRate || 200));
    for (let i = 0; i < chart.data.length; i++) {
      if (chart.data[i].length > maxPts) {
        chart.data[i] = chart.data[i].slice(-maxPts);
      }
    }

    // Update chart
    if (chart.plot) {
      chart.plot.setData(chart.data);
    }
  }

  _createChart(channel, samplingRate) {
    const wrapper = document.createElement('div');
    wrapper.className = 'chart-wrapper';
    wrapper.innerHTML = `<h3>${channel}</h3><div id="chart-${channel}" style="width:100%;height:200px;"></div>`;
    this.container.appendChild(wrapper);

    const el = document.getElementById(`chart-${channel}`);
    const width = el.clientWidth || 800;

    // Detect if multi-axis from channel name
    const isMultiAxis = /acc|gyro|imu|magn/i.test(channel);
    const series = [{ label: 'Time' }];
    const data = [[]];

    if (isMultiAxis) {
      for (const axis of ['x', 'y', 'z']) {
        series.push({ label: axis, stroke: axis === 'x' ? '#ef4444' : axis === 'y' ? '#22c55e' : '#3b82f6' });
        data.push([]);
      }
    } else {
      series.push({ label: channel, stroke: '#2563eb' });
      data.push([]);
    }

    const opts = {
      width,
      height: 200,
      series,
      scales: { x: { time: false } },
      axes: [{ label: 'Time (s)' }, { label: channel }],
    };

    let plot = null;
    if (typeof uPlot !== 'undefined') {
      plot = new uPlot(opts, data, el);
    }

    this.charts[channel] = { plot, data, opts, el };
  }

  clear() {
    this.container.innerHTML = '';
    this.charts = {};
  }
}

// Export for use in app.js
window.StreamClient = StreamClient;
window.ChartManager = ChartManager;
