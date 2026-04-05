// Movensense Data Browser — minimal SPA
const API = '/api';
const params = new URLSearchParams(window.location.search);
const TOKEN = params.get('token') || sessionStorage.getItem('ms_token') || '';
if (TOKEN) sessionStorage.setItem('ms_token', TOKEN);

const $ = (id) => document.getElementById(id);
const content = $('content');
const breadcrumb = $('breadcrumb');
const subtitle = $('subtitle');

async function apiFetch(path) {
  const resp = await fetch(API + path, {
    headers: { 'Authorization': `Bearer ${TOKEN}` }
  });
  if (resp.status === 401) {
    content.innerHTML = '<div class="error">Authentication failed. Check your token.</div>';
    throw new Error('401');
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

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
      content.innerHTML = '<div class="empty">No data collected yet. Run <code>movensense fetch</code> first.</div>';
      return;
    }
    content.innerHTML = data.devices.map(d => `
      <div class="card" onclick="showDates('${d.serial}')">
        <h3>${d.serial}</h3>
        <div class="meta">${d.date_count} collection date${d.date_count !== 1 ? 's' : ''}</div>
      </div>
    `).join('');
  } catch (e) { if (e.message !== '401') content.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function showDates(serial) {
  subtitle.textContent = serial;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial }
  ]);
  try {
    const data = await apiFetch(`/devices/${serial}/dates`);
    content.innerHTML = data.dates.map(d => `
      <div class="card" onclick="showSessions('${serial}', '${d}')">
        <h3>${d}</h3>
      </div>
    `).join('');
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function showSessions(serial, date) {
  subtitle.textContent = `${serial} / ${date}`;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date }
  ]);
  try {
    const data = await apiFetch(`/devices/${serial}/dates/${date}/sessions`);
    content.innerHTML = data.sessions.map(s => `
      <div class="card" onclick="showChannels('${serial}', '${date}', ${s.log_id})">
        <h3>Log ${s.log_id}</h3>
        <div class="meta">${s.channels.map(c => `<span class="badge">${c}</span>`).join('')}</div>
      </div>
    `).join('');
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

let tsViewer = null;

async function showChannels(serial, date, logId) {
  subtitle.textContent = `Log ${logId}`;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date, action: `showSessions('${serial}', '${date}')` },
    { label: `Log ${logId}` }
  ]);

  // Show time series viewer + channel table
  content.innerHTML = '<div id="ts-viewer"></div><hr style="margin:1.5rem 0;border-color:var(--border)"><div id="channel-table"></div>';

  // Load synchronized time series viewer
  if (typeof TimeSeriesViewer !== 'undefined') {
    tsViewer = new TimeSeriesViewer('ts-viewer', apiFetch);
    window.tsViewer = tsViewer;
    tsViewer.load(serial, date, logId);
  }

  // Also show channel metadata table
  try {
    const data = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels`);
    let html = '<table><thead><tr><th>Channel</th><th>Type</th><th>Rate</th><th>Unit</th><th>Samples</th><th>Shape</th></tr></thead><tbody>';
    for (const c of data.channels) {
      html += `<tr class="card" style="cursor:pointer" onclick="showChannelData('${serial}','${date}',${logId},'${c.name}')">
        <td><strong>${c.name}</strong></td>
        <td>${c.sensor_type || '-'}</td>
        <td>${c.sampling_rate_hz ? c.sampling_rate_hz + ' Hz' : '-'}</td>
        <td>${c.unit || '-'}</td>
        <td>${c.sample_count}</td>
        <td>${JSON.stringify(c.shape)}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    const tableEl = document.getElementById('channel-table');
    if (tableEl) tableEl.innerHTML = html;
  } catch (e) {}
}

async function showChannelData(serial, date, logId, channel) {
  subtitle.textContent = channel;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date, action: `showSessions('${serial}', '${date}')` },
    { label: `Log ${logId}`, action: `showChannels('${serial}','${date}',${logId})` },
    { label: channel }
  ]);
  try {
    const data = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels/${channel}/data?limit=1000`);
    const values = Array.isArray(data.data[0]) ? data.data.map(r => r[0]) : data.data;
    const min = Math.min(...values).toFixed(4);
    const max = Math.max(...values).toFixed(4);
    const mean = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(4);
    const duration = data.sampling_rate_hz ? (data.total_samples / data.sampling_rate_hz).toFixed(1) + 's' : '-';

    content.innerHTML = `
      <table>
        <tr><th>Total samples</th><td>${data.total_samples}</td></tr>
        <tr><th>Sampling rate</th><td>${data.sampling_rate_hz || '-'} Hz</td></tr>
        <tr><th>Duration</th><td>${duration}</td></tr>
        <tr><th>Unit</th><td>${data.unit || '-'}</td></tr>
        <tr><th>Min (first 1000)</th><td>${min}</td></tr>
        <tr><th>Max (first 1000)</th><td>${max}</td></tr>
        <tr><th>Mean (first 1000)</th><td>${mean}</td></tr>
        <tr><th>Showing</th><td>${data.offset}–${data.offset + values.length} of ${data.total_samples}</td></tr>
      </table>
    `;
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

// --- Init ---
if (!TOKEN) {
  content.innerHTML = '<div class="error">No token provided. Start the server and use the URL shown in the terminal.</div>';
  subtitle.textContent = 'Authentication required';
} else {
  showDevices();
}
