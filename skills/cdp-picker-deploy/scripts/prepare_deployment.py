#!/usr/bin/env python3
"""Generate Windows launchers from explicit paths; never execute their targets."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


PATH_ENVIRONMENT = {
    "CDP_PICKER_DESKTOP_EXE": "file",
    "CDP_PICKER_DESKTOP_INDEX": "file",
    "CDP_PICKER_CLI_DATABASE": "file",
    "ZCODE_DATA_BASE_DIR": "directory",
    "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": "file",
    "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": "file",
    "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE": "file",
    "TEMP": "directory",
    "TMP": "directory",
}
OUTPUT_NAMES = ("zcode-backend.cmd", "Start-Picker.cmd", "Check-Backend.cmd")


def _safe_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " must be a nonempty string")
    # The current wrapper parser treats percent signs as variable references.
    # Double quotes/newlines cannot be represented by these quoted CMD fields.
    if any(char in value for char in ('"', '%', '\r', '\n', '\0')):
        raise ValueError(field + " contains unsupported CMD path characters")
    return value


def _existing(value, field, *, directory=False):
    path = Path(_safe_text(value, field)).expanduser()
    if not path.is_absolute():
        raise ValueError(field + " must be an absolute path")
    path = path.resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(field + " does not point to an existing " + ("directory" if directory else "file"))
    if directory and path.parent == path:
        raise ValueError(field + " must not be a filesystem root")
    _safe_text(str(path), field)
    return path


def _endpoint(value):
    value = _safe_text(value, "connection.cdpEndpoint")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("CDP endpoint must be a plain loopback HTTP endpoint")
    host = parsed.hostname
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        port = parsed.port
    except (ValueError, TypeError):
        raise ValueError("CDP endpoint must have a valid loopback host and explicit port") from None
    if not loopback or port is None or not 1 <= port <= 65535:
        raise ValueError("CDP endpoint must have a loopback host and explicit port")
    return value.rstrip("/")


def render_launchers(record):
    if not isinstance(record, dict) or type(record.get("schemaVersion")) is not int or record["schemaVersion"] != 1:
        raise ValueError("record schemaVersion must be 1")
    paths = record.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("record.paths must be an object")
    picker = _existing(paths.get("pickerExe"), "paths.pickerExe")
    node = _existing(paths.get("nodeExe"), "paths.nodeExe")
    entry = _existing(paths.get("cliEntryScript"), "paths.cliEntryScript")
    workspace = _existing(paths.get("workspaceDir"), "paths.workspaceDir", directory=True)
    if picker.suffix.lower() != ".exe" or node.name.lower() != "node.exe":
        raise ValueError("this Windows Node-bundle template requires picker.exe and node.exe")
    if entry.suffix.lower() not in {".cjs", ".mjs", ".js"} or "zcode" not in str(entry).lower():
        raise ValueError("CLI entry must be an actual zcode cjs/mjs/js path")
    connection = record.get("connection", {})
    if not isinstance(connection, dict):
        raise ValueError("record.connection must be an object")
    endpoint = _endpoint(connection.get("cdpEndpoint", "http://127.0.0.1:9222"))
    environment = record.get("pathEnvironment", {})
    if not isinstance(environment, dict) or set(environment) - PATH_ENVIRONMENT.keys():
        raise ValueError("pathEnvironment accepts only the documented path-variable allowlist")
    assignments = []
    for name, kind in PATH_ENVIRONMENT.items():
        value = environment.get(name)
        if value is None:
            continue
        path = _existing(value, "pathEnvironment." + name, directory=kind == "directory")
        assignments.append('set "' + name + '=' + str(path) + '"')
    common = ["@echo off", "chcp 65001 >nul", "setlocal DisableDelayedExpansion", *assignments]
    arguments = (' --workspace "' + str(workspace) + '" --endpoint "' + endpoint
                 + '" --zcode-cli "%~dp0zcode-backend.cmd"')
    launch = ' /D "' + str(picker.parent) + '" "' + str(picker) + '"' + arguments
    files = {
        "zcode-backend.cmd": ['@echo off', '"' + str(node) + '" "' + str(entry) + '" %*'],
        "Start-Picker.cmd": [*common, 'start ""' + launch],
        "Check-Backend.cmd": [*common, 'start "" /wait' + launch
                              + ' --backend-check "%~dp0backend-report.json"', 'exit /b %errorlevel%'],
    }
    return {name: ("\r\n".join(lines) + "\r\n").encode("utf-8") for name, lines in files.items()}


def generate(record_path, output_dir, *, overwrite=False):
    record = json.loads(Path(record_path).read_text(encoding="utf-8-sig"))
    files = render_launchers(record)
    output = Path(_safe_text(str(output_dir), "output")).expanduser()
    if not output.is_absolute():
        raise ValueError("output must be an absolute directory path")
    output = output.resolve()
    if output.parent == output:
        raise ValueError("output must not be a filesystem root")
    if output.exists() and not output.is_dir():
        raise ValueError("output is not a directory")
    for name in OUTPUT_NAMES:
        target = output / name
        if target.is_symlink() or (target.exists() and (not overwrite or not target.is_file())):
            raise ValueError("refusing to replace " + name + "; use a new output directory or explicit --overwrite for generated files")
    output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        descriptor, temporary = tempfile.mkstemp(prefix=".deployment-", suffix=".tmp", dir=output)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return [str(output / name) for name in OUTPUT_NAMES]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path, help="Explicit deployment-record JSON; never automatically read by Picker")
    parser.add_argument("--output", required=True, type=Path, help="Absolute directory for three generated CMD files")
    parser.add_argument("--overwrite", action="store_true", help="Replace these three generated files only")
    arguments = parser.parse_args()
    try:
        paths = generate(arguments.record, arguments.output, overwrite=arguments.overwrite)
    except (OSError, ValueError) as error:
        parser.exit(2, "Generation failed: " + str(error) + "\n")
    print(json.dumps({"generated": paths, "executed": False, "environmentModified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
