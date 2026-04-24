"""
Run an iReasoning-style SNMP script file. See docs/help.html for the format.

Supported commands: get, getnext, set, if, sleep, save, comments.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Optional

from . import snmp_ops
from .config import Agent
from .mib_loader import MibTree


class ScriptError(Exception):
    pass


def _parse_host(spec: str, default_port: int) -> tuple[str, int]:
    if ":" in spec:
        host, port = spec.rsplit(":", 1)
        return host, int(port)
    return spec, default_port


def run(path: str, agent: Agent, tree: MibTree,
        logger: Optional[Callable[[str], None]] = None) -> None:
    def log(msg: str) -> None:
        if logger is not None:
            logger(msg)
        else:
            print(msg)

    last_result = None
    last_error = 0
    save_path: Optional[Path] = None
    save_buffer: list[str] = []

    def _save_line(s: str) -> None:
        save_buffer.append(s)

    def _flush_save() -> None:
        if save_path is None or not save_buffer:
            return
        p = save_path
        i = 0
        while p.exists():
            i += 1
            p = save_path.with_suffix(save_path.suffix + f".{i}")
        p.write_text("\n".join(save_buffer), encoding="utf-8")
        log(f"saved {len(save_buffer)} line(s) to {p}")

    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        op = parts[0].lower()

        # Conditional: `if $ <op> <val> <action> <arg>` or `if $ err <action> <arg>`
        if op == "if":
            if last_result is None and last_error == 0:
                continue
            m = re.match(r"if\s+\$\s+(err|>|<|>=|<=|!=|=)\s*(\S*)\s+(\w+)(?:\s+(.+))?", line)
            if not m:
                log(f"skip: invalid if: {line}")
                continue
            pred, operand, action, arg = m.groups()
            ok = False
            if pred == "err":
                ok = last_error != 0
            else:
                try:
                    cur = float(last_result) if last_result is not None else None
                    val = float(operand) if operand else None
                except (TypeError, ValueError):
                    cur = val = None
                if cur is not None and val is not None:
                    ok = {"<": cur < val, ">": cur > val,
                          "<=": cur <= val, ">=": cur >= val,
                          "=": cur == val, "!=": cur != val}[pred]
            if ok:
                if action == "sound":
                    # Stdlib "bell" — a cheap notification
                    print("\a", end="", flush=True)
                elif action == "email":
                    log(f"[email action → {arg}] (SMTP not configured, skipped)")
                elif action == "sleep":
                    try:
                        time.sleep(float(arg))
                    except Exception:
                        pass
            continue

        if op == "sleep":
            try:
                time.sleep(float(parts[1]))
            except Exception as exc:
                log(f"bad sleep: {exc}")
            continue

        if op == "save":
            save_path = Path(" ".join(parts[1:])).expanduser()
            continue

        if op in ("get", "getnext"):
            host_spec, *oids = parts[1:]
            host, port = _parse_host(host_spec, agent.port)
            ag = Agent(**{**vars(agent), "host": host, "port": port})
            resolved = []
            for o in oids:
                t = tree.resolve_name(o)
                if t is None:
                    log(f"unresolved OID: {o}")
                    last_error = 1
                    continue
                resolved.append(t)
            if not resolved:
                continue
            fn = snmp_ops.op_get if op == "get" else snmp_ops.op_next
            try:
                vbs = fn(ag, resolved)
                last_error = 0
            except Exception as exc:
                log(f"{op} {host_spec}: {exc}")
                last_error = 1
                continue
            for vb in vbs:
                ln = f"{'.' + '.'.join(map(str, vb.oid))}\t{vb.type_name}\t{vb.display_value}"
                log(ln)
                _save_line(ln)
            if vbs:
                last_result = vbs[-1].display_value
            continue

        if op == "set":
            # set host oid type val [oid type val ...]
            host_spec, *rest = parts[1:]
            host, port = _parse_host(host_spec, agent.port)
            ag = Agent(**{**vars(agent), "host": host, "port": port})
            pairs = []
            idx = 0
            while idx + 2 < len(rest):
                oid = rest[idx]; type_tag = rest[idx + 1]; val = rest[idx + 2]
                t = tree.resolve_name(oid)
                if t is None:
                    log(f"unresolved OID: {oid}")
                    last_error = 1
                    idx += 3
                    continue
                try:
                    pairs.append((t, snmp_ops.build_set_value(type_tag, val)))
                except Exception as exc:
                    log(f"bad set value: {exc}")
                    last_error = 1
                idx += 3
            if not pairs:
                continue
            try:
                vbs = snmp_ops.op_set(ag, pairs)
                last_error = 0
                for vb in vbs:
                    ln = f"{'.' + '.'.join(map(str, vb.oid))}\t{vb.type_name}\t{vb.display_value}"
                    log(ln); _save_line(ln)
                if vbs:
                    last_result = vbs[-1].display_value
            except Exception as exc:
                log(f"set: {exc}")
                last_error = 1
            continue

        log(f"unknown command: {line}")

    _flush_save()
