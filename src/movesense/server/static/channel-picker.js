/**
 * ChannelPicker: responsive channel selector with checkboxes.
 * Sidebar mode (>1200px) or collapsible accordion (<800px).
 */

class ChannelPicker {
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    this.onToggle = options.onToggle || null;
    this._channels = [];  // [{name, rate_hz, unit, session_count, selected}]
    this._selected = new Set();
  }

  /**
   * Set available channels from sessions index.
   * @param {Array} channels - [{name, rate_hz, unit, session_count}]
   */
  setChannels(channels) {
    this._channels = channels.map(c => ({ ...c, selected: true }));
    this._selected = new Set(channels.map(c => c.name));
    this._render();
  }

  getSelected() {
    return [...this._selected];
  }

  setSelected(names) {
    this._selected = new Set(names);
    this._channels.forEach(c => c.selected = this._selected.has(c.name));
    this._render();
  }

  _render() {
    if (!this.container) return;

    let html = '<div class="channel-picker" style="display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;">';
    html += '<span style="font-size:0.8rem;font-weight:600;">Channels:</span>';
    html += '<button onclick="channelPicker.selectAll()" style="font-size:0.7rem;padding:1px 5px;">All</button>';
    html += '<button onclick="channelPicker.selectNone()" style="font-size:0.7rem;padding:1px 5px;">None</button>';

    for (const ch of this._channels) {
      const checked = this._selected.has(ch.name) ? 'checked' : '';
      const rate = ch.rate_hz ? ` ${Math.round(ch.rate_hz)}Hz` : '';
      html += `<label style="font-size:0.8rem;cursor:pointer;white-space:nowrap;">
        <input type="checkbox" ${checked} onchange="channelPicker._toggle('${ch.name}', this.checked)">
        ${ch.name.split('/').pop() || ch.name}<span style="color:#999;font-size:0.7rem">${rate}</span>
      </label>`;
    }

    html += '</div>';
    this.container.innerHTML = html;
  }

  _toggle(name, checked) {
    if (checked) this._selected.add(name);
    else this._selected.delete(name);
    const ch = this._channels.find(c => c.name === name);
    if (ch) ch.selected = checked;
    if (this.onToggle) this.onToggle(name, checked);
  }

  selectAll() {
    this._channels.forEach(c => { c.selected = true; this._selected.add(c.name); });
    this._render();
    if (this.onToggle) this.onToggle(null, true); // null = all changed
  }

  selectNone() {
    this._channels.forEach(c => { c.selected = false; });
    this._selected.clear();
    this._render();
    if (this.onToggle) this.onToggle(null, false);
  }
}

window.ChannelPicker = ChannelPicker;
