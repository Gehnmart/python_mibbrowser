# MIB handling

## Where MIBs live

- `mibs-src/` — ASN.1 source files. Initial set is public IETF MIBs
  redistributed from the iReasoning MIB Browser bundle. Drop your own
  `.mib` files here (or use `File → Load MIB…`) and they compile on next
  rebuild.
- `~/.local/share/pymibbrowser/compiled_mibs/*.json` — pysmi output.
  Safe to delete; regenerated on rebuild.
- `~/.config/pymibbrowser/settings.json` — which modules are enabled,
  whether to fetch deps from the internet.

## First-run compilation

At first startup, if `compiled_mibs/` is empty, pysmi compiles every file
in `mibs-src/` into JSON (~30-60 s for 73 modules). `fetch_missing_from_net`
is **off** by default: if a MIB imports a symbol from a module you don't
have locally, that module is marked `missing` and dependent modules may
fail. Turn on the checkbox in `File → Rebuild MIB cache` or `File →
Load MIB…` if you need the http fallback to mibs.pysnmp.com.

## Loading a vendor MIB

1. `File → Load MIB…`
2. Pick the `.mib` (or multiple files).
3. The dialog copies your file into `mibs-src/` and auto-detects extra
   source directories:
   - the file's own parent directory
   - if the grand-parent holds **≥ 2** sibling directories that each
     contain MIB files, each sibling directory is added as a separate
     source — so vendor repos laid out as
     `~/Repos/snmp-mibs/{wlc,cpe,eltexLtd,…}/` resolve cross-directory
     IMPORTS without you clicking `Add source directory…` for each one.
4. **Compile** runs with a per-module progress bar; press `Close` when
   done.

### When compilation fails

Typical causes:

- `no symbol X in module Y` — pysmi picked an older copy of Y that lacks
  the symbol. Click `Add source directory…` and point to the subdir
  with the correct Y, then `Compile` again.
- `missing` status — dependency not in any source dir. Options:
  - Add the dir that has it with `Add source directory…`
  - Or enable `Fetch missing deps from mibs.pysnmp.com` (top-right
    checkbox).
- `failed` status — pysmi parser error. Check the file log for the
  underlying exception (often a nonstandard ASN.1 construct). Mention
  this to pysmi upstream or fix the MIB's syntax.

## Module management

`File → MIB Modules…` gives you per-module enable/disable so the tree
only shows what you care about.

- **Only vendor (no RFC/SNMPv2)** preset hides standard stuff but keeps
  framework stubs (`SNMPv2-SMI`, `SNMPv2-TC`, `SNMPv2-CONF`,
  `INET-ADDRESS-MIB`, `SNMP-FRAMEWORK-MIB`, …) so textual conventions
  still resolve in your vendor MIB.
- **Unload selected** deletes the compiled JSON for those modules —
  useful to reclaim disk or force a re-compile. The source MIB in
  `mibs-src/` is not touched.

Click any module to see its MODULE-IDENTITY info, imports, dependents,
and definition counts. Framework stubs (SNMPv2-SMI, SNMPv2-TC, …) aren't
files but show a short blurb + dependents list.

## Bundled MIBs

The `mibs-src/` subdirectory ships with the public MIBs needed to cover
basic TCP/IP management. The main ones:

- `SNMPv2-MIB` (RFC 3418), `SNMPv2-TC`, `SNMPv2-CONF` — core SNMPv2 defs
- `RFC1213-MIB` (MIB-II), `RFC1155-SMI`, `RFC-1212`, `RFC-1215` — SMIv1
- `IF-MIB` (RFC 2863), `INET-ADDRESS-MIB` (RFC 4001), `IP-MIB`,
  `TCP-MIB`, `UDP-MIB`
- `HOST-RESOURCES-MIB` (RFC 2790) — disks, processes, software
- `BRIDGE-MIB`, `Q-BRIDGE-MIB`, `ENTITY-MIB`, `RMON2-MIB`, `DISMAN-*`,
  `NET-SNMP-*`, `UCD-*`
- A few vendor samples (CISCO-SMI-V1SMI, CISCO-PROCESS-MIB, CISCO-LWAPP-*)
  redistributed from the iReasoning bundle

These are all IETF RFCs (public domain) or vendor MIBs the vendors
publish for interoperability. The redistribution is unrestricted but
not endorsed by the vendors.

If you need more, either drop the files into `mibs-src/` and rebuild, or
use `File → Load MIB…` (recommended).
