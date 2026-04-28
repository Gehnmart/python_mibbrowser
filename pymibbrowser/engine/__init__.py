"""Pure execution engine — the brain of pymibbrowser.

This package contains:
  * data model — Agent, VarBind (no vendor types);
  * AST       — Get / GetNext / Set / Sleep / Save / If / Unknown / Script;
  * ports     — Protocols every adapter must implement (SnmpTransport, Clock,
                Resolver, Logger, OutputSink);
  * parser    — text → AST (pure);
  * runner    — execute(script, context) — every side effect flows through a
                port, no globals, no library imports beyond stdlib + typing.

It does not import anything outside the standard library. Adapters live in
``pymibbrowser.core`` (pysnmp, pysmi, sockets, files) and are wired up by the
caller, not by the engine.
"""
