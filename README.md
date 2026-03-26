# P2P File Conversion Network

A distributed peer-to-peer file conversion system built with **raw Python sockets** and **TLS encryption**. Peers on the same LAN discover each other automatically and offload conversion jobs to the peer with the most available capacity.

> CN Mini Project — Team 18 | Socket Programming (Jackfruit)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture](#2-architecture)
3. [Protocol Design](#3-protocol-design)
4. [Core Implementation](#4-core-implementation)
5. [Features](#5-features)
6. [Project Structure](#6-project-structure)
7. [Setup & Usage](#7-setup--usage)
8. [Libraries Used](#8-libraries-used)
9. [Performance Evaluation](#9-performance-evaluation)
10. [Optimizations & Edge Case Handling](#10-optimizations--edge-case-handling)
11. [Deliverable Answers](#11-deliverable-answers)

---

## 1. Problem Statement

File conversion tools (FFmpeg, LibreOffice, ImageMagick) are CPU-heavy. On a single machine, converting a large video while already running other tasks is slow. On a LAN with multiple machines, spare CPU cycles on neighbouring computers go unused.

**Goal:** Build a distributed system where any peer can offload a conversion job to a less-loaded peer on the same network, receive the result back automatically, with all traffic encrypted.

**Objectives:**
- Automatic peer discovery — no manual IP entry
- Intelligent load-based job routing
- TLS-encrypted job transfers (file data + control messages)
- Fault tolerance — if a peer goes down, try the next one, then fall back to local
- Support concurrent jobs from multiple clients

---

## 2. Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────┐
│                     Each Peer Node                      │
│                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐   │
│  │Discovery │   │  Server  │   │     Client       │   │
│  │mDNS+UDP  │   │TCP+TLS   │   │  TCP+TLS         │   │
│  └──────────┘   └──────────┘   └──────────────────┘   │
│        │               │                │              │
│  ┌─────▼───────────────▼────────────────▼──────────┐  │
│  │                  peer.py (UI)                    │  │
│  │  Flask Web UI  |  Job queue  |  Metrics          │  │
│  └──────────────────────────────────────────────────┘  │
│                        │                               │
│              ┌─────────▼──────────┐                   │
│              │   Converter        │                   │
│              │  FFmpeg / Pillow / │                   │
│              │  LibreOffice / COM │                   │
│              └────────────────────┘                   │
└─────────────────────────────────────────────────────────┘
         ↕ TCP/TLS                   ↕ TCP/TLS
┌─────────────────┐         ┌─────────────────┐
│     Peer B      │         │     Peer C      │
│  (accepting     │         │  (idle, takes   │
│   your job)     │         │   overflow)     │
└─────────────────┘         └─────────────────┘
```

### Architecture Type

**Fully distributed P2P** — every node is simultaneously a server (accepts jobs) and a client (submits jobs). There is no central coordinator. Each peer independently decides whether to accept or reject incoming jobs based on its own load.

### Components

| Component | File | Role |
|---|---|---|
| Discovery | `core/discovery.py` | mDNS registration + UDP beacon scanner + heartbeat monitor |
| Server | `core/server.py` | TCP server accepting incoming conversion jobs |
| Client | `core/client.py` | TCP client submitting jobs to remote peers |
| Protocol | `core/protocol.py` | Wire format: length-prefixed JSON + binary file transfer |
| Converter | `core/converter.py` | Conversion engine — routes to FFmpeg / Pillow / LibreOffice |
| Metrics | `core/metrics.py` (UI only) | Latency, throughput, bandwidth, TLS overhead tracking |
| UI | `peer.py` | Flask web dashboard (UI) or Tkinter desktop app (v1Basic) |

---

## 3. Protocol Design

### Wire Format

All control messages use **length-prefixed JSON**:
```
[ 4 bytes: uint32 big-endian length ][ JSON body ]
```

All file transfers use **length-prefixed binary**:
```
[ 8 bytes: uint64 big-endian length ][ raw file bytes ]
```

The 4-byte header ensures exact framing on a TCP stream (which has no message boundaries).

### Message Flow

```
Client                              Server
  │                                   │
  │──── HELLO (peer_id, name) ───────►│
  │◄─── HELLO (peer_id, cpu_load) ────│  ← cpu_load used for peer ranking
  │                                   │
  │──── JOB_REQUEST (fmt, size) ─────►│
  │◄─── JOB_OFFER   (cpu, queue) ─────│  (or JOB_REJECT if too busy)
  │                                   │
  │──── [file bytes] ────────────────►│
  │                                   │  ← convert here
  │◄─── JOB_DONE (output filename) ───│
  │◄─── [file bytes] ─────────────────│
  │                                   │
  │──── BYE ─────────────────────────►│
```

### Message Types

| Message | Direction | Key Fields |
|---|---|---|
| `HELLO` | Both | `peer_id`, `peer_name`, `port`, `cpu_load`, `active_jobs` |
| `JOB_REQUEST` | C→S | `job_id`, `input_format`, `output_format`, `filename`, `file_size`, `use_gpu` |
| `JOB_OFFER` | S→C | `job_id`, `cpu_load`, `queue_len` |
| `JOB_REJECT` | S→C | `job_id`, `reason` |
| `JOB_DONE` | S→C | `job_id`, `output_filename`, `file_size` |
| `JOB_ERROR` | S→C | `job_id`, `reason` |
| `BYE` | C→S | `peer_id` |

---

## 4. Core Implementation

### Socket Usage (explicit, low-level)

```python
# server.py — socket creation, binding, listening
self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
self._sock.bind((self.host, self.port))
self._sock.listen(20)
conn, addr = self._sock.accept()   # blocks until client connects

# client.py — connect to peer
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(CONNECT_TIMEOUT)
sock.connect((host, port))
```

### TLS (SSL/TLS wraps the raw TCP socket)

```python
# server.py — wrap accepted connection with TLS
conn = ssl_context.wrap_socket(conn, server_side=True)

# client.py — wrap outgoing connection with TLS
sock = ssl_context.wrap_socket(sock, server_hostname=host)
```

TLS certificates are auto-generated on first run using the `cryptography` library (RSA 2048-bit, self-signed X.509, 10-year validity). `CERT_NONE` mode is used — we need encryption on a LAN, not CA-based identity.

### TCP Keepalive (dead peer detection)

```python
sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,  30)   # probe after 30s idle
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)   # every 10s
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,   3)    # give up after 3 misses
```

### Concurrency

- **Server:** one daemon thread per accepted connection (`threading.Thread`)
- **Conversion jobs:** one daemon thread per job
- **Discovery:** two daemon threads (UDP listen + UDP scan/keepalive) + mDNS thread
- **UI:** Flask runs in main thread; background threads post updates via `ACTIVITY_LOG` deque
- **Peer probing:** `concurrent.futures.ThreadPoolExecutor` probes all peers in parallel before job submission

---

## 5. Features

### Peer Discovery (two-layer)

1. **mDNS (Zeroconf)** — same subnet automatic discovery via `_p2pconvert._tcp.local.`
2. **UDP Beacon Scanner** — broadcasts to all IPs on the local subnet; handles cross-subnet and cases where mDNS multicast is blocked

### Heartbeat & Stale Peer Eviction

- Every peer sends UDP beacons every 15 seconds as a keepalive
- Background monitor checks every 20s; peers silent for >120s are evicted
- mDNS `Removed` events are cross-checked against recent UDP activity — spurious removals (common on WiFi) are ignored

### Intelligent Peer Selection

Before submitting a job, the client probes all known peers **in parallel**. Peers are ranked by:
1. Fewest active jobs (primary)
2. Lowest CPU load (tiebreaker)

The freest peer is tried first. On failure, the next peer is tried. If all peers fail or reject, the job runs locally.

### Server-Side Admission Control

A peer rejects incoming jobs if:
- Active jobs ≥ 3 (`MAX_CONCURRENT_JOBS`)
- CPU ≥ 90% (`CPU_REJECT_THRESHOLD`)
- RAM ≥ 90% (`RAM_REJECT_THRESHOLD`)
- Conversion tool not available for the requested format

### Supported Conversions

| Category | Formats |
|---|---|
| Images | PNG, JPEG, BMP, WEBP, GIF, TIFF (via Pillow) |
| Video | MP4, AVI, MOV, MKV, WEBM (via FFmpeg) |
| Audio | MP3, WAV, AAC, FLAC, OGG (via FFmpeg) |
| Documents | DOCX→PDF (via LibreOffice or docx2pdf) |
| Presentations | PPTX→PDF (via LibreOffice → PowerPoint COM → python-pptx fallback chain) |
| PDF | Combine multiple PDFs into one (via pypdf) |

### GPU Acceleration

NVENC / AMF / QSV encoders auto-detected on startup. GPU preference travels with the job — the sender's GPU setting is applied on the remote peer. If the remote peer has no GPU, it silently uses CPU.

### Security

- All TCP job traffic (control + file bytes) is TLS-encrypted
- Certificates auto-generated on first run; shared across both versions via `certs/`
- Path traversal attack prevention: filenames sanitised with `Path(filename).name` before saving
- UDP discovery packets are plaintext metadata only (no file data, no user content)

---

## 6. Project Structure

```
P2P_File_Converter/
├── README.md
├── start.bat                    # Quick-start for UI version (Windows)
├── .gitignore
├── certs/
│   ├── gen_cert.py              # Manual cert generation utility
│   ├── peer.crt                 # Auto-generated TLS cert (gitignored)
│   └── peer.key                 # Auto-generated TLS private key (gitignored)
│
├── UI/                          # Flask web dashboard version
│   ├── peer.py                  # Entry point — Flask app + job orchestration
│   ├── requirements.txt
│   ├── setup.bat / setup.sh
│   ├── core/
│   │   ├── client.py            # TCP client — submits jobs to peers
│   │   ├── converter.py         # Conversion engine
│   │   ├── discovery.py         # mDNS + UDP beacon + heartbeat
│   │   ├── metrics.py           # Performance metrics tracker
│   │   ├── protocol.py          # Wire format (framing + send/recv)
│   │   └── server.py            # TCP server — accepts incoming jobs
│   └── ui/
│       ├── templates/index.html # Web UI
│       └── static/
│           ├── app.js
│           └── style.css
│
└── v1Basic/                     # Tkinter desktop version
    ├── peer.py                  # Entry point — Tkinter UI
    ├── requirements.txt
    ├── run.bat
    ├── core/                    # Same structure as UI/core/
    ├── PART1_DISCOVERY.md       # Study guide: discovery.py
    ├── PART2_PROTOCOL.md        # Study guide: protocol.py, server.py, client.py
    └── PART3_APPLICATION.md     # Study guide: peer.py, converter.py
```

---

## 7. Setup & Usage

### Prerequisites

- Python 3.10+
- FFmpeg in PATH — [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
- LibreOffice in PATH — optional, for DOCX/PPTX → PDF
- All other dependencies installed via `pip`

### Install

```bash
# UI version (recommended)
cd UI
pip install -r requirements.txt

# Basic Tkinter version
cd v1Basic
pip install -r requirements.txt
```

### Run (UI version)

```bash
# From the root directory (Windows):
start.bat --name Alice

# Or manually:
cd UI
python peer.py --name Alice --ui-port 8080
```

Opens `http://localhost:8080` automatically. Run the same command on other machines on your LAN with different `--name` values to form a peer network.

### Run (v1Basic Tkinter version)

```bash
cd v1Basic
python peer.py --name Alice
# or
run.bat --name Alice
```

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `--name` | hostname | Display name for this peer |
| `--port` | auto | TCP server port (0 = OS picks a free port) |
| `--ui-port` | 8080 | Web UI port (UI version only) |

### TLS Certificates

Certificates are auto-generated in `certs/` on first run. No manual setup needed. To regenerate manually:

```bash
python certs/gen_cert.py
```

---

## 8. Libraries Used

### Third-Party

| Library | Purpose |
|---|---|
| `zeroconf` | mDNS peer registration and discovery |
| `psutil` | CPU % and RAM % for job admission control |
| `pillow` | Image format conversion |
| `pypdf` | PDF combining |
| `docx2pdf` | DOCX → PDF via Microsoft Word (Windows) |
| `cryptography` | Auto-generate self-signed TLS certificates |
| `imageio-ffmpeg` | Bundles FFmpeg as Python package (fallback if not in PATH) |
| `comtypes` | PowerPoint COM automation for PPTX → PDF |
| `python-pptx` | Pure-Python PPTX reading (last-resort PDF fallback) |
| `reportlab` | Pure-Python PDF writer (used with python-pptx) |
| `flask` | Web server for the UI version |

### Standard Library (no install needed)

| Module | Purpose |
|---|---|
| `socket` | Raw TCP socket creation, binding, listening, connecting |
| `ssl` | TLS wrapping of TCP sockets |
| `threading` | Daemon threads for server, discovery, conversion |
| `concurrent.futures` | Parallel peer probing before job submission |
| `struct` | Pack/unpack 4-byte and 8-byte length headers |
| `json` | Serialize/deserialize control messages |
| `uuid` | Unique peer IDs and job IDs |
| `subprocess` | Spawn FFmpeg for audio/video conversion |
| `queue` | Thread-safe UI update queues (v1Basic) |
| `tkinter` | Desktop UI (v1Basic) |

---

## 9. Performance Evaluation

Performance metrics are tracked in real-time and exposed at `/api/status`. The **Network Stats** tab in the web UI shows all values live.

### Metrics Tracked

| Metric | How Measured | Where |
|---|---|---|
| Conversion Latency | `time.perf_counter()` at socket send → receive | `client.py`, `server.py` |
| Min / Avg / Max Latency | Running window of last 100 jobs | `metrics.py` |
| Throughput | `jobs_done / elapsed_minutes` | `metrics.py` |
| CPU Utilization | `psutil.cpu_percent()` sampled per job | `server.py`, `metrics.py` |
| Bandwidth (sent/recv) | Byte counters at socket level | `server.py`, `metrics.py` |
| TLS Handshake Overhead | `time.perf_counter()` around `wrap_socket()` | `server.py` → `metrics.py` |
| File Size vs Latency | `(input_size_kb, latency_ms)` pairs stored | `server.py`, `peer.py` |
| Peer Scalability | Latency history across sessions with 2/3/4 peers | `metrics.py` |

### Benchmark Procedure

**Conversion Latency & File Size:**
1. Convert a 1 MB, 10 MB, and 100 MB file
2. Check `size_latency_samples` in `/api/status` — each entry has `size_kb` and `latency_ms`

**TLS Overhead:**
1. Run a conversion with TLS enabled — note `tls_handshake_avg_ms` from `/api/status`
2. Toggle TLS off in the Network Stats tab
3. Run the same conversion — compare total latency
4. TLS adds ~1–5 ms per connection for handshake; bulk transfer overhead is negligible (AES-GCM is hardware-accelerated)

**Peer Scalability:**
1. Start with 2 peers — run 5 conversions, record avg latency
2. Add a 3rd peer — repeat
3. Add a 4th peer — repeat
4. Throughput increases linearly with peer count since jobs distribute across nodes

**Fault Tolerance:**
1. Start 3 peers — offload a job from P1 to P2
2. Kill P2 mid-transfer — P1 catches the `ConnectionError`, logs "trying next peer..."
3. P1 tries P3, or falls back to local
4. Recovery time = time to detect failure + retry (~10s with TCP keepalive timeout)

---

## 10. Optimizations & Edge Case Handling

### Edge Cases Handled

| Scenario | Handling |
|---|---|
| Peer crashes mid-job | `ConnectionError` caught in `_run_job`; tries next peer then local |
| SSL handshake never completes | 10s timeout on `wrap_socket`; connection closed, loop continues |
| Non-TLS client connects to TLS server | `SSLError` caught immediately; logged and closed |
| Empty file uploaded | Checked after save — returns `400 Uploaded file is empty` |
| Malicious filename (`../../etc/passwd`) | `Path(filename).name` strips all path components before saving |
| No JSON body in API request | `(request.json or {}).get(...)` guards all toggle endpoints |
| All peers busy/down | Falls back to local conversion automatically |
| mDNS spurious peer removal (WiFi) | Cross-checked against UDP last-seen timestamp; ignored if seen within 45s |
| Peer silent but not crashed | Heartbeat monitor evicts after 120s of no UDP beacons |
| Duplicate peer discovery | `if peer_id in self._peers: return` prevents duplicate entries |

### Key Optimizations

- **Parallel peer probing** — all peers probed simultaneously via `ThreadPoolExecutor`, sorted by load before first job attempt
- **Smart keepalive** — UDP keepalive unicasts directly to known peers (more reliable than broadcast-only on WiFi)
- **GPU preference propagation** — sender's GPU setting travels with `JOB_REQUEST`; no need for all peers to configure GPU
- **Three-tier PPTX→PDF fallback** — LibreOffice → PowerPoint COM (Windows) → python-pptx + reportlab (pure Python)
- **TCP keepalive at OS level** — `SO_KEEPALIVE` detects dead connections without application-level ping

---

## 11. Deliverable Answers

### Deliverable 1

#### Component 1 — Problem Definition & Architecture (6 marks)

**Problem:** Distributed file conversion over a LAN — offload CPU-heavy jobs to idle peers.

**Architecture:** Fully distributed P2P. Every node is both client and server. No central server. mDNS + UDP for discovery, TCP/TLS for job transfers.

**System components:** Discovery → Server → Client → Protocol → Converter → UI (see Section 2).

**Communication flow:** mDNS/UDP discovers peers → TCP HELLO handshake → JOB_REQUEST/OFFER → binary file transfer → result returned. All over TLS.

**Diagrams:** See Section 2 (architecture diagram) and Section 3 (message flow diagram).

---

#### Component 2 — Core Implementation (8 marks)

Explicit low-level socket usage throughout — no framework abstracts socket behaviour.

| Concept | File | Line | Code |
|---|---|---|---|
| Socket creation | `core/server.py` | ~64 | `socket.socket(AF_INET, SOCK_STREAM)` |
| Bind | `core/server.py` | ~66 | `.bind((host, port))` |
| Listen | `core/server.py` | ~67 | `.listen(20)` |
| Accept | `core/server.py` | ~94 | `.accept()` → `conn, addr` |
| Connect (client) | `core/client.py` | ~205 | `.connect((host, port))` |
| TLS wrap (server) | `core/server.py` | ~114 | `ssl_context.wrap_socket(conn, server_side=True)` |
| TLS wrap (client) | `core/client.py` | ~209 | `ssl_context.wrap_socket(sock, server_hostname=host)` |
| Manual framing | `core/protocol.py` | | `struct.pack('>I', len(body))` — 4-byte length prefix |
| Stream reassembly | `core/protocol.py` | | `_recv_exact(sock, n)` — loop until exactly n bytes read |
| TCP keepalive | `core/server.py` | ~100 | `SO_KEEPALIVE`, `TCP_KEEPIDLE/INTVL/CNT` |

**Multiple concurrent clients:** `threading.Thread` per accepted connection; up to `MAX_CONCURRENT_JOBS = 3` active at once before rejecting.

---

#### Component 3 — Feature Implementation, Deliverable 1 (8 marks)

- ✅ Core P2P conversion working (discover → offload → receive result)
- ✅ Multiple concurrent clients (thread-per-connection, admission control)
- ✅ SSL/TLS on all job traffic — `ssl.SSLContext.wrap_socket()` on every TCP connection
- ✅ Functional correctness demonstrable (run `python peer.py --name X` on 2+ machines)

**Note on UDP discovery:** UDP beacon packets are plaintext metadata (peer name, IP, port only — no file data). All actual job content travels over TLS-protected TCP.

---

### Deliverable 2

#### Component 4 — Performance Evaluation (7 marks)

All metrics measured and exposed live at `/api/status` (JSON) and in the Network Stats tab of the web UI.

| Parameter | Measurement Method | API Field |
|---|---|---|
| Conversion Latency | `perf_counter()` at job start/end | `avg_latency_ms`, `min_latency_ms`, `max_latency_ms` |
| Throughput | `jobs_done / elapsed_minutes` | `throughput_per_min` |
| CPU Utilization | `psutil.cpu_percent()` per job | `cpu_percent` |
| Bandwidth Usage | Byte counters at socket level | `bytes_sent_mb`, `bytes_recv_mb` |
| TLS Overhead | `perf_counter()` around `wrap_socket()` | `tls_handshake_avg_ms` |
| File Size vs Latency | `(input_size_kb, latency_ms)` pairs | `size_latency_samples` |
| Peer Scalability | Latency history across 2/3/4 peer sessions | `latency_history` |
| Fault Tolerance | Automatic peer fallback; recovery logged | Activity log |

**How to benchmark TLS overhead:**
1. Note `tls_handshake_avg_ms` with TLS on
2. Toggle TLS off in Network Stats tab
3. Run same file — compare job latency
4. Expected: ~2–8 ms overhead per connection (RSA handshake), negligible on bulk transfer (AES-GCM is hardware-accelerated on modern CPUs)

---

#### Component 5 — Optimization & Fixes (5 marks)

| Fix | File | Description |
|---|---|---|
| SSL handshake timeout | `core/server.py` | `conn.settimeout(10)` before `wrap_socket` — prevents thread hang on non-TLS client |
| Path traversal | `core/server.py`, `peer.py` | `Path(filename).name` strips directory separators from attacker-controlled input |
| Null request body | `peer.py` | `(request.json or {}).get(...)` guards all API toggle endpoints |
| Empty file upload | `peer.py` | `save_path.stat().st_size == 0` check rejects empty uploads immediately |
| Spurious mDNS disconnect | `core/discovery.py` | mDNS `Removed` ignored if peer seen via UDP in last 45s (WiFi fix) |
| Smart UDP keepalive | `core/discovery.py` | Unicast to known peers + broadcast, every 15s — reliable on WiFi APs that throttle multicast |
| CPU-load peer routing | `peer.py` | Parallel probes + sort before job submission — jobs go to the freest peer, not just the first discovered |
| GPU preference propagation | `core/client.py`, `core/server.py` | `use_gpu` field in `JOB_REQUEST` — sender's preference applied on remote peer |

---

#### Component 6 — Final Demo & GitHub (6 marks)

**Demo steps:**
1. Start 3 peers on different machines: `python peer.py --name Alice/Bob/Charlie`
2. Peers auto-discover — check Network Stats tab for connected peers
3. Upload a file on Alice's UI — observe job routing to freest peer in activity log
4. Kill Bob mid-conversion — observe automatic failover to Charlie or local
5. Toggle TLS off, run a conversion, compare latency in Network Stats
6. Check `/api/status` for live JSON metrics

**Repository:** All source code, documentation, and setup scripts committed to GitHub.
