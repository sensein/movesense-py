// Movensense Data Browser — minimal SPA
// TOKEN and apiFetch are defined in the global init block (index.html)
const $ = (id) => document.getElementById(id);
const content = $('content');
const breadcrumb = $('breadcrumb');
const subtitle = $('subtitle');

function setBreadcrumb(items) {
  breadcrumb.innerHTML = items.map((item, i) =>
    i < items.length - 1
      ? `<a onclick="${item.action}">${item.label}</a> /`
      : `<span>${item.label}</span>`
  ).join(' ');
}

// --- Views ---

async function showDevices() {
  subtitle.textContent = 'Devices';
  setBreadcrumb([{ label: 'Devices' }]);
  try {
    const data = await apiFetch('/devices');
    if (!data.devices.length) {
      content.innerHTML = '<div class="empty">No data collected yet. Run <code>movesense fetch</code> first.</div>';
      return;
    }
    content.innerHTML = data.devices.map(d => `
      <div class="card" onclick="showTimeline('${d.serial}')">
        <h3>${d.serial}</h3>
        <div class="meta">${d.date_count} collection date${d.date_count !== 1 ? 's' : ''}</div>
      </div>
    `).join('');
  } catch (e) { if (e.message !== '401') content.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function showTimeline(serial) {
  subtitle.textContent = serial;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial }
  ]);

  content.innerHTML = `
    <div>
      <div id="timeline-bar-container" style="margin-bottom:0.75rem;"></div>
      <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.5rem;flex-wrap:wrap;">
        <div id="data-picker" style="font-size:0.8rem;"></div>
        <div style="margin-left:auto;">
          <button onclick="if(dataChart&&dataChart._plots)dataChart._plots.forEach(p=>p.setScale('x',{min:dataChart._data[0][0],max:dataChart._data[0][dataChart._data[0].length-1]}))" style="font-size:0.75rem;">Reset Zoom</button>
          <button onclick="dataChart&&dataChart.captureScreenshot()" style="font-size:0.75rem;">📷</button>
        </div>
      </div>
      <div id="data-chart" style="width:100%;"></div>
    </div>`;

  // Load sessions and build timeline
  try {
    const sessionsResp = await apiFetch(`/devices/${serial}/sessions`);
    const sessions = sessionsResp.sessions || [];

    // Build timeline bar
    if (typeof TimelineBar !== 'undefined') {
      const timelineBar = new TimelineBar('timeline-bar-container', {
        onRangeSelect: async (startUs, endUs) => {
          await loadTimelineRange(serial, startUs, endUs);
        }
      });
      timelineBar.sessions = sessions;
      if (sessions.length > 0) {
        timelineBar._minUs = Math.min(...sessions.map(s => s.start_utc_us || 0).filter(v => v > 0));
        timelineBar._maxUs = Math.max(...sessions.map(s => s.end_utc_us || s.start_utc_us || 0).filter(v => v > 0));
      }
      timelineBar.draw();
    }

    // Build channel picker from union of all session channels
    const allChannels = {};
    for (const s of sessions) {
      for (const [name, meta] of Object.entries(s.channels || {})) {
        if (!allChannels[name]) allChannels[name] = { name, rate_hz: meta.rate_hz, unit: meta.unit || '', session_count: 0 };
        allChannels[name].session_count++;
      }
    }

    if (typeof ChannelPicker !== 'undefined') {
      channelPicker = new ChannelPicker('data-picker', {
        onToggle: () => { if (_lastTimelineRange) loadTimelineRange(serial, _lastTimelineRange[0], _lastTimelineRange[1]); }
      });
      window.channelPicker = channelPicker;
      channelPicker.setChannels(Object.values(allChannels));
    }

    // Auto-load first session if available
    if (sessions.length > 0) {
      const first = sessions[0];
      const startUs = first.start_utc_us || 0;
      const endUs = first.end_utc_us || startUs;
      if (startUs > 0) {
        await loadTimelineRange(serial, startUs, endUs);
      } else {
        // No UTC — fall back to legacy per-session view
        await loadLegacySession(serial, sessions[0]);
      }
    }
  } catch (e) { content.innerHTML += `<div class="error">${e.message}</div>`; }
}

let _lastTimelineRange = null;

async function loadTimelineRange(serial, startUs, endUs) {
  _lastTimelineRange = [startUs, endUs];
  if (!dataChart) {
    dataChart = new UnifiedChart('data-chart', { mode: 'static' });
  }
  dataChart.clear();

  const selected = channelPicker ? channelPicker.getSelected() : [];
  if (!selected.length) return;

  // Convert µs to ISO for timeline API
  const startISO = new Date(startUs / 1000).toISOString();
  const endISO = new Date(endUs / 1000).toISOString();

  const channelData = {};
  await Promise.all(selected.map(async (chName) => {
    try {
      const resp = await apiFetch(`/devices/${serial}/timeline?start=${startISO}&end=${endISO}&channel=${chName}&buckets=2000`);
      if (resp.segments) {
        // Merge segments into channel data
        for (const seg of resp.segments) {
          if (seg.type === 'gap') continue;
          if (seg.data && seg.data.time && seg.data.time.length > 0) {
            if (!channelData[chName]) {
              channelData[chName] = { time: [], axes: 1, unit: '' };
            }
            channelData[chName].time.push(...seg.data.time);
            if (seg.data.values) {
              if (!channelData[chName].values) channelData[chName].values = [];
              channelData[chName].values.push(...seg.data.values);
            } else if (seg.data.columns) {
              channelData[chName].axes = seg.data.columns.length;
              channelData[chName].columns = seg.data.columns;
              if (!channelData[chName].colData) channelData[chName].colData = {};
              for (const col of seg.data.columns) {
                if (!channelData[chName].colData[col]) channelData[chName].colData[col] = [];
                channelData[chName].colData[col].push(...(seg.data[col] || []));
              }
            }
          }
        }
      }
    } catch (e) { /* skip */ }
  }));

  if (Object.keys(channelData).length > 0) {
    dataChart._buildFromChannelData(channelData);
    dataChart._render();
  }
}

async function loadLegacySession(serial, session) {
  // Fallback for sessions without UTC — use downsample API
  const idx = session.index;
  // Find date from scanner
  try {
    const dates = await apiFetch(`/devices/${serial}/dates`);
    for (const date of dates.dates || []) {
      const sessResp = await apiFetch(`/devices/${serial}/dates/${date}/sessions`);
      const match = (sessResp.sessions || []).find(s => s.log_id === idx);
      if (match) {
        await loadSessionFromDownsample(serial, date, idx, match.channels);
        return;
      }
    }
  } catch (e) { /* fallback failed */ }
}

async function loadSessionFromDownsample(serial, date, logId, channelNames) {
  if (!dataChart) {
    dataChart = new UnifiedChart('data-chart', { mode: 'static' });
  }
  dataChart.clear();

  const selected = channelPicker ? channelPicker.getSelected().filter(c => channelNames.includes(c)) : channelNames;
  const channelData = {};

  await Promise.all(selected.map(async (chName) => {
    try {
      const ds = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels/${chName}/downsample?buckets=2000`);
      if (ds.data && ds.data.time && ds.data.time.length > 0) {
        channelData[chName] = { time: ds.data.time, axes: 1, unit: ds.unit || '' };
        if (ds.columns && ds.columns.length > 0) {
          channelData[chName].axes = ds.columns.length;
          channelData[chName].columns = ds.columns;
          channelData[chName].colData = {};
          for (const col of ds.columns) {
            const arr = ds.data[col] || ds.data[`${col}_mean`];
            if (arr) channelData[chName].colData[col] = arr;
          }
        } else if (ds.data.values) {
          channelData[chName].values = ds.data.values;
        } else if (ds.data.mean) {
          channelData[chName].values = ds.data.mean;
        }
      }
    } catch (e) { /* skip */ }
  }));

  if (Object.keys(channelData).length > 0) {
    dataChart._buildFromChannelData(channelData);
    dataChart._render();
  }
}

let dataChart = null;
let channelPicker = null;

// --- Init ---
if (!TOKEN) {
  content.innerHTML = '<div class="error">No token provided. Start the server and use the URL shown in the terminal.</div>';
  subtitle.textContent = 'Authentication required';
} else {
  showDevices();
}
