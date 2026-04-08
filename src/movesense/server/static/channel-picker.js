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

    let html = '<div class="channel-picker">';
    html += '<div style="font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;">Channels</div>';
    html += '<div style="margin-bottom:0.5rem;font-size:0.7rem;">';
    html += '<button onclick="channelPicker.selectAll()" style="font-size:0.7rem;padding:2px 6px;">All</button> ';
    html += '<button onclick="channelPicker.selectNone()" style="font-size:0.7rem;padding:2px 6px;">None</button>';
    html += '</div>';

    for (const ch of this._channels) {
      const checked = this._selected.has(ch.name) ? 'checked' : '';
      const rate = ch.rate_hz ? `${ch.rate_hz}Hz` : '';
      const count = ch.session_count ? `${ch.session_count} sessions` : '';
      html += `<label class="ch-picker-item" style="display:block;font-size:0.8rem;padding:3px 0;cursor:pointer;">
        <input type="checkbox" ${checked} onchange="channelPicker._toggle('${ch.name}', this.checked)">
        ${ch.name.split('/').pop() || ch.name}
        <span style="color:#999;font-size:0.7rem">${rate} ${count}</span>
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
