"""CLI entry point for Movesense device management."""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .sensor import SensorCommand, DL_STATES

DEFAULT_DATA_DIR = Path.home() / "dbp" / "data" / "movesense"


def _load_env_serial() -> str | None:
    """Load MSN from .env file if available."""
    for env_path in [Path.cwd() / ".env", Path.cwd().parent / ".env"]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("MSN="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get("MSN")


def _resolve_serials(serial_numbers: tuple[str, ...]) -> list[str]:
    """Get serial numbers from args or .env."""
    if serial_numbers:
        return list(serial_numbers)
    env_serial = _load_env_serial()
    if env_serial:
        return [env_serial]
    click.echo("Error: No serial numbers provided. Use -s or set MSN in .env", err=True)
    sys.exit(1)


def _output_dir(data_dir: str, serial: str) -> Path:
    """Resolve output directory for a device, creating it if needed."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = Path(data_dir) / serial / date_str
    out.mkdir(parents=True, exist_ok=True)
    return out


def _run(coro):
    """Run an async coroutine."""
    return asyncio.run(coro)


# --- Async device operations ---

async def _status(serial: str) -> dict:
    try:
        async with SensorCommand(serial) as sensor:
            status = await sensor.get_status()
            status.update(await sensor.get_battery_level())
            return status
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _configure(serial: str, paths: list[str]) -> dict:
    try:
        async with SensorCommand(serial) as sensor:
            if "/Time/Detailed" not in paths:
                paths.append("/Time/Detailed")
            config_data = bytearray()
            for path in paths:
                config_data.extend(path.encode("utf-8") + b"\0")
            return await sensor.configure_device(config_data)
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _start(serial: str) -> dict:
    try:
        async with SensorCommand(serial) as sensor:
            status = await sensor.get_status()
            if status.get("dlstate") == 3:
                click.echo(f"Device {serial}: already logging")
                return {"success": True, "already_logging": True}
            return await sensor.start_logging()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _stop(serial: str) -> dict:
    try:
        async with SensorCommand(serial) as sensor:
            stop_result = await sensor.stop_logging()
            if not stop_result.get("success"):
                return stop_result
            boot_result = await sensor.set_system_mode(5)
            boot_status = boot_result.get("status_code", 0)
            if boot_status in [200, 202]:
                boot_result["success"] = True
            await sensor.disconnect()
            await asyncio.sleep(4)
            return boot_result
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _fetch(serial: str, output_dir: Path, edf: bool = False) -> dict:
    """Fetch logs from device, convert to Zarr v3 with content-addressed dedup.

    Pipeline:
    1. Download SBEM from device
    2. Compute SHA-256 → check blob store → skip if duplicate
    3. Store blob, convert SBEM → JSON → Zarr (session group in device store)
    4. Write provenance record
    5. Delete intermediate JSON/CSV on success (keep SBEM blob + Zarr)
    """
    import subprocess
    from .json2zarr import convert_json_to_zarr
    from .storage import BlobStore, DeviceStore, ProvLog

    sbem2json_path = Path(__file__).parent.parent.parent / "sbem2json"
    if not sbem2json_path.exists():
        return {"success": False, "error": f"sbem2json not found at {sbem2json_path}"}

    # Device directory for blob store + prov log
    device_dir = Path(output_dir).parent  # output_dir is {data_dir}/{serial}/{date}
    # If output_dir is {data_dir}/{serial}/{date}, device_dir should be {data_dir}/{serial}
    # But serial might not be the parent name if called differently. Use data_dir/serial.
    data_dir = Path(output_dir).parent.parent
    device_dir = data_dir / serial
    device_dir.mkdir(parents=True, exist_ok=True)

    blob_store = BlobStore(device_dir)
    prov = ProvLog(device_dir)
    device_store = DeviceStore(device_dir)
    device_store.open()

    try:
        async with SensorCommand(serial, set_time=False) as sensor:
            status = await sensor.get_status()
            if status.get("dlstate") == 3:
                return {
                    "success": False,
                    "error": "Device is currently logging. Stop logging first (`movesense stop`), then fetch.",
                }

            logs = await sensor.get_log_list()
            entries = logs.get("entries", []) if logs.get("success") else []

            if not entries:
                log_id = 1
                while True:
                    result = await sensor.fetch_data(
                        log_id=log_id,
                        output_file=str(output_dir / f"Movesense_log_{log_id}_{serial}.sbem"),
                    )
                    if result.get("success"):
                        entries.append({"id": log_id, "size": result.get("size", 0)})
                        log_id += 1
                    else:
                        break

            fetched_files = []
            for entry in entries:
                log_id = entry["id"]
                sbem_file = output_dir / f"Movesense_log_{log_id}_{serial}.sbem"
                json_file = output_dir / f"Movesense_log_{log_id}_{serial}.json"
                # Legacy paths (for backward compat during transition)
                zarr_path = output_dir / f"Movesense_log_{log_id}_{serial}.zarr"

                # Step 1: Fetch SBEM from device if not already on disk
                if not sbem_file.exists() or sbem_file.stat().st_size == 0:
                    click.echo(f"  Fetching log {log_id}...")
                    result = await sensor.fetch_data(log_id=log_id, output_file=str(sbem_file))
                    if not result.get("success"):
                        click.echo(f"  Failed to fetch log {log_id}: {result.get('error')}", err=True)
                        continue

                # Step 2: Compute hash and check for duplicates
                blob_hash = blob_store.store(sbem_file)

                if prov.has_hash(blob_hash):
                    existing = prov.find_by_hash(blob_hash)
                    click.echo(f"  Log {log_id}: already processed (hash: {blob_hash[:12]}..., session {existing.get('session_index', '?')})")
                    fetched_files.append(str(sbem_file))
                    continue

                # Step 3: Convert SBEM → JSON
                click.echo(f"  Converting log {log_id}: SBEM → JSON")
                proc = subprocess.run(
                    [str(sbem2json_path), "--sbem2json", str(sbem_file), "--output", str(json_file)],
                    capture_output=True, text=True,
                )
                if proc.returncode != 0:
                    click.echo(f"  sbem2json failed for log {log_id}: {proc.stderr}", err=True)
                    prov.record(blob_hash, sbem_file.name, serial, log_id, -1, [], "error", sbem_file.stat().st_size)
                    continue

                # Step 4: Convert JSON → session group in DeviceStore
                session_idx = device_store.next_session_index()
                session_group = device_store.add_session(session_idx)
                click.echo(f"  Converting log {log_id}: JSON → Zarr session {session_idx}")
                convert_json_to_zarr(
                    json_file, None,
                    device_serial=serial,
                    session_group=session_group,
                    source_blob_hash=blob_hash,
                )

                # Step 5: Update sessions index with channel metadata
                channels_meta = dict(session_group.attrs.get("channels", {}))
                ts_mapping = dict(session_group.attrs.get("timestamp_mapping", {}))

                # Compute UTC start/end from timestamp mapping + channel data
                summary = {
                    "channels": channels_meta,
                }
                if ts_mapping:
                    summary["start_utc_us"] = ts_mapping.get("utc_time_us", 0)
                    # Estimate duration from highest-rate channel
                    max_samples = max((m.get("samples", 0) for m in channels_meta.values()), default=0)
                    max_rate = max((m.get("rate_hz", 1) for m in channels_meta.values()), default=1)
                    duration_s = max_samples / max_rate if max_rate > 0 else 0
                    summary["duration_seconds"] = round(duration_s, 3)
                    summary["end_utc_us"] = summary["start_utc_us"] + int(duration_s * 1_000_000)
                    # ISO strings with µs precision
                    from datetime import datetime, timezone
                    start_dt = datetime.fromtimestamp(summary["start_utc_us"] / 1_000_000, tz=timezone.utc)
                    end_dt = datetime.fromtimestamp(summary["end_utc_us"] / 1_000_000, tz=timezone.utc)
                    summary["start_utc"] = start_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    summary["end_utc"] = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

                device_store.update_sessions_index(session_idx, summary)

                # Step 6: Write provenance record
                prov.record(
                    blob_hash, sbem_file.name, serial, log_id,
                    session_index=session_idx,
                    channels=list(channels_meta.keys()),
                    status="ok",
                    file_size_bytes=sbem_file.stat().st_size,
                )
                click.echo(f"  Stored blob {blob_hash[:12]}... → session {session_idx}")

                # Step 7: Clean up intermediates (keep SBEM blob + Zarr only)
                # Verify Zarr group was written correctly before deleting
                if session_group and len(list(channels_meta.keys())) > 0:
                    if json_file.exists():
                        json_file.unlink()
                    for csv in output_dir.glob(f"Movesense_log_{log_id}_{serial}*.csv"):
                        csv.unlink()
                    click.echo(f"  Cleaned up intermediates")

                fetched_files.append(str(sbem_file))

            device_store.close()
            await sensor.set_system_mode(5)
            return {"success": True, "files": fetched_files, "output_dir": str(output_dir)}

    except Exception as e:
        device_store.close()
        return {"success": False, "error": str(e)}


async def _live(serial: str, paths: list[str], duration: int) -> dict:
    """Subscribe to live data streams and print values."""
    from .sensor import GSP_RESP_DATA, DataView
    try:
        async with SensorCommand(serial) as sensor:
            # Subscribe to each path with unique reference IDs
            refs = {}
            for i, path in enumerate(paths):
                ref = 10 + i
                result = await sensor.subscribe_to_resource(path, reference=ref)
                if result.get("success"):
                    refs[ref] = path
                    click.echo(f"  Subscribed to {path} (ref={ref})")
                else:
                    click.echo(f"  Failed to subscribe to {path}: {result.get('error')}", err=True)

            if not refs:
                return {"success": False, "error": "No subscriptions succeeded"}

            # Stream data for the specified duration
            import time
            end_time = time.time() + duration
            sample_count = 0

            click.echo(f"  Streaming for {duration}s... (Ctrl+C to stop)")
            try:
                while time.time() < end_time:
                    try:
                        response = await asyncio.wait_for(
                            sensor.data_queue.get(), timeout=2.0
                        )
                        resp_code = response.get("response_code")
                        ref = response.get("reference")
                        path = refs.get(ref, f"ref={ref}")

                        if resp_code in [2, 3]:  # GSP_RESP_DATA, GSP_RESP_DATA_PART2
                            payload = response.get("data_payload", b"")
                            sample_count += 1
                            if len(payload) > 0:
                                click.echo(f"  [{path}] {len(payload)} bytes")
                    except asyncio.TimeoutError:
                        continue
            except KeyboardInterrupt:
                click.echo("\n  Interrupted by user")

            # Unsubscribe with exponential backoff
            for ref in refs:
                path = refs[ref]
                max_attempts = 3
                for attempt in range(max_attempts):
                    try:
                        while not sensor.data_queue.empty():
                            await sensor.data_queue.get()
                        await sensor.unsubscribe_from_resource(ref)
                        click.echo(f"  Unsubscribed from {path}")
                        break
                    except Exception as e:
                        backoff = 2 ** attempt  # 1s, 2s, 4s
                        if attempt < max_attempts - 1:
                            click.echo(f"  Unsubscribe failed for {path} (attempt {attempt + 1}/{max_attempts}): {e}. Retrying in {backoff}s...", err=True)
                            await asyncio.sleep(backoff)
                        else:
                            click.echo(f"  Error: could not unsubscribe from {path} after {max_attempts} attempts: {e}", err=True)

            return {"success": True, "samples": sample_count}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def _erase(serial: str) -> dict:
    try:
        async with SensorCommand(serial, set_time=False) as sensor:
            return await sensor.erase_memory()
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Click CLI ---

@click.group()
@click.option("-V", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx, verbose):
    """Movesense BLE sensor device management."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


# Common options for all device commands
_serial_option = click.option("-s", "--serial_numbers", multiple=True, help="Device serial(s). Defaults to MSN from .env")
_data_dir_option = click.option("--data-dir", default=str(DEFAULT_DATA_DIR), show_default=True, help="Data output directory")


@cli.command()
@_serial_option
def status(serial_numbers):
    """Check device status."""
    for serial in _resolve_serials(serial_numbers):
        click.echo(f"Connecting to {serial}...")
        result = _run(_status(serial))
        if result.get("success", False) or "protocol_version" in result:
            click.echo(f"Device {serial}: OK")
            click.echo(f"  Serial number: {result.get('serial_number', 'Unknown')}")
            click.echo(f"  Product name: {result.get('product_name', 'Unknown')}")
            click.echo(f"  App version: {result.get('app_version', 'Unknown')}")
            click.echo(f"  Battery: {result.get('battery_level', 'Unknown')}%")
            click.echo(f"  DataLogger state: {DL_STATES.get(result.get('dlstate', 1), 'Unknown')}")
        else:
            click.echo(f"Device {serial} error: {result.get('error', 'Unknown')}", err=True)


@cli.command()
@_serial_option
@click.argument("paths", nargs=-1, required=True)
def config(serial_numbers, paths):
    """Configure measurement paths.

    Pass paths as arguments: movesense config /Meas/Ecg/200/mV /Meas/Acc/52
    """
    for serial in _resolve_serials(serial_numbers):
        click.echo(f"Configuring {serial}...")
        result = _run(_configure(serial, list(paths)))
        if result.get("success"):
            click.echo(f"Device {serial}: configured")
        else:
            click.echo(f"Device {serial} error: {result.get('error')}", err=True)


@cli.command()
@_serial_option
def start(serial_numbers):
    """Start logging."""
    for serial in _resolve_serials(serial_numbers):
        click.echo(f"Starting logging on {serial}...")
        result = _run(_start(serial))
        if result.get("success"):
            msg = "already logging" if result.get("already_logging") else "logging started"
            click.echo(f"Device {serial}: {msg}")
        else:
            click.echo(f"Device {serial} error: {result.get('error')}", err=True)


@cli.command()
@_serial_option
def stop(serial_numbers):
    """Stop logging."""
    for serial in _resolve_serials(serial_numbers):
        click.echo(f"Stopping logging on {serial}...")
        result = _run(_stop(serial))
        if result.get("success"):
            click.echo(f"Device {serial}: logging stopped")
        else:
            click.echo(f"Device {serial} error: {result.get('error')}", err=True)


@cli.command()
@_serial_option
@_data_dir_option
@click.option("--edf", is_flag=True, help="Also export EDF+ format")
def fetch(serial_numbers, data_dir, edf):
    """Fetch and convert data."""
    for serial in _resolve_serials(serial_numbers):
        out = _output_dir(data_dir, serial)
        click.echo(f"Fetching data from {serial} → {out}")
        result = _run(_fetch(serial, out, edf=edf))
        if result.get("success"):
            click.echo(f"Device {serial}: {len(result.get('files', []))} logs fetched to {out}")
        else:
            click.echo(f"Device {serial} error: {result.get('error')}", err=True)


@cli.command()
@_serial_option
@click.option("--force", is_flag=True, help="Skip confirmation")
def erase(serial_numbers, force):
    """Erase device memory."""
    for serial in _resolve_serials(serial_numbers):
        if not force:
            if not click.confirm(f"Erase ALL data from {serial}?"):
                click.echo(f"Erase cancelled for {serial}")
                continue
        click.echo(f"Erasing memory on {serial}...")
        result = _run(_erase(serial))
        if result.get("success"):
            click.echo(f"Device {serial}: memory erased")
        else:
            click.echo(f"Device {serial} error: {result.get('error')}", err=True)


@cli.command()
@_serial_option
@click.argument("paths", nargs=-1)
@click.option("-d", "--duration", default=10, show_default=True, help="Seconds to stream")
def live(serial_numbers, paths, duration):
    """Stream live data from device.

    Subscribe to measurement paths and print incoming data for the specified duration.
    Works while device is logging to flash.

    If no paths given, defaults to ECG at 200Hz.

    \b
    Examples:
      movesense live /Meas/Ecg/200/mV
      movesense live /Meas/Ecg/200/mV /Meas/Acc/52 /Meas/Temp
      movesense live -d 30 /Meas/Acc/104
    """
    if not paths:
        paths = ("/Meas/Ecg/200/mV",)
    for serial in _resolve_serials(serial_numbers):
        click.echo(f"Streaming live data from {serial}...")
        result = _run(_live(serial, list(paths), duration))
        if result.get("success"):
            click.echo(f"Device {serial}: {result.get('samples', 0)} samples received")
        else:
            click.echo(f"Device {serial} error: {result.get('error')}", err=True)


@cli.command()
@_serial_option
@_data_dir_option
def migrate(serial_numbers, data_dir):
    """Migrate old per-session Zarr stores to single DeviceStore layout.

    Scans {serial}/{date}/*.zarr directories, creates {serial}/data.zarr
    with session groups, and moves SBEM files to the blob store.
    Original files are preserved until you manually delete them.
    """
    import re
    from .storage import BlobStore, DeviceStore, ProvLog
    from .json2zarr import convert_json_to_zarr

    data_path = Path(data_dir)
    for serial in _resolve_serials(serial_numbers):
        serial_dir = data_path / serial
        if not serial_dir.exists():
            click.echo(f"No data directory for {serial}")
            continue

        store_path = serial_dir / "data.zarr"
        if store_path.exists():
            click.echo(f"{serial}: data.zarr already exists, skipping")
            continue

        blob_store = BlobStore(serial_dir)
        prov = ProvLog(serial_dir)
        device_store = DeviceStore(serial_dir)
        device_store.open()

        log_pattern = re.compile(r"Movesense_log_(\d+)_(.+)\.zarr$")
        migrated = 0

        for date_dir in sorted(serial_dir.iterdir()):
            if not date_dir.is_dir() or not re.match(r"\d{4}-\d{2}-\d{2}$", date_dir.name):
                continue
            for zarr_dir in sorted(date_dir.iterdir()):
                m = log_pattern.match(zarr_dir.name)
                if not m:
                    continue
                log_id = int(m.group(1))

                # Check for corresponding SBEM
                sbem_file = date_dir / zarr_dir.name.replace(".zarr", ".sbem")
                if sbem_file.exists() and sbem_file.stat().st_size > 0:
                    blob_hash = blob_store.store(sbem_file)
                else:
                    blob_hash = ""

                # Check for JSON to re-convert
                json_file = date_dir / zarr_dir.name.replace(".zarr", ".json")
                session_idx = device_store.next_session_index()

                if json_file.exists():
                    group = device_store.add_session(session_idx)
                    convert_json_to_zarr(json_file, None, device_serial=serial,
                                        session_group=group, source_blob_hash=blob_hash)
                    channels_meta = dict(group.attrs.get("channels", {}))
                    device_store.update_sessions_index(session_idx, {
                        "channels": channels_meta, "start_utc": f"{date_dir.name}T00:00:00.000000Z",
                    })
                else:
                    # Copy Zarr data directly (no JSON available)
                    import zarr
                    old_store = zarr.open_group(str(zarr_dir), mode="r")
                    group = device_store.add_session(session_idx, dict(old_store.attrs))
                    for ch_name in old_store:
                        zarr.copy(old_store[ch_name], group, name=ch_name)
                    device_store.update_sessions_index(session_idx, {
                        "channels": {}, "start_utc": f"{date_dir.name}T00:00:00.000000Z",
                    })

                if blob_hash:
                    prov.record(blob_hash, sbem_file.name if sbem_file.exists() else "", serial, log_id,
                                session_idx, [], "migrated")

                migrated += 1
                click.echo(f"  Migrated log {log_id} ({date_dir.name}) → session {session_idx}")

        device_store.close()
        click.echo(f"{serial}: migrated {migrated} sessions to data.zarr")


@cli.command(name="rebuild-prov")
@_serial_option
@_data_dir_option
def rebuild_prov(serial_numbers, data_dir):
    """Rebuild provenance log by scanning blob store."""
    from .storage import BlobStore, ProvLog, content_hash

    data_path = Path(data_dir)
    for serial in _resolve_serials(serial_numbers):
        serial_dir = data_path / serial
        blob_store = BlobStore(serial_dir)
        prov = ProvLog(serial_dir)

        hashes = blob_store.rebuild_index()
        added = 0
        for h in hashes:
            if not prov.has_hash(h):
                prov.record(h, "", serial, 0, -1, [], "rebuilt", 0)
                added += 1

        click.echo(f"{serial}: {len(hashes)} blobs found, {added} new prov records added")


@cli.command()
@_data_dir_option
@click.option("--port", default=8585, show_default=True, help="Server port")
@click.option("--host", default="127.0.0.1", show_default=True, help="Server host")
def serve(data_dir, port, host):
    """Start the data server for browsing collected sensor data.

    Exposes a REST API and browser UI at http://{host}:{port}.
    Requires collected data in the data directory (from `movesense fetch`).
    """
    from pathlib import Path

    import uvicorn

    from .server.app import create_app

    data_path = Path(data_dir)
    app = create_app(data_path)

    token = app.state.token
    device_count = len(app.state.scanner.devices)
    session_count = sum(
        len(sessions)
        for dates in app.state.scanner._index.values()
        for sessions in dates.values()
    )

    click.echo("Movensense Data Server")
    click.echo(f"  URL:     http://{host}:{port}")
    click.echo(f"  Token:   {token}")
    click.echo(f"  Data:    {data_path}")
    click.echo(f"  Devices: {device_count}, Sessions: {session_count}")
    click.echo()
    click.echo(f"Open in browser: http://{host}:{port}/?token={token}")
    click.echo()

    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    cli()


if __name__ == "__main__":
    main()
