"""
metrics.py — lightweight in-memory metrics tracker

Tracks:
  - jobs_done          total completed jobs
  - jobs_failed        total failed jobs
  - latencies_ms       list of per-job latencies (capped at last 100)
  - bytes_sent         total bytes sent to peers
  - bytes_recv         total bytes received from peers
  - cpu_percent        current CPU usage (sampled on demand)
  - start_time         when this peer started

All values exposed as a plain dict via snapshot() for the Flask UI.
"""

import time
import threading
import logging

log = logging.getLogger('metrics')

_LATENCY_WINDOW = 100   # keep last N latency samples


class Metrics:
    def __init__(self):
        self._lock        = threading.Lock()
        self.start_time   = time.time()
        self.jobs_done    = 0
        self.jobs_failed  = 0
        self._latencies   = []   # ms, capped at _LATENCY_WINDOW
        self._tls_times   = []   # TLS handshake durations in ms, capped at _LATENCY_WINDOW
        self._size_lat    = []   # (file_size_bytes, latency_ms) pairs, capped at _LATENCY_WINDOW
        self.bytes_sent   = 0
        self.bytes_recv   = 0

    # ------------------------------------------------------------------
    # Record events
    # ------------------------------------------------------------------

    def record_job_done(self, latency_ms: float, bytes_sent: int = 0,
                        file_size_bytes: int = 0):
        with self._lock:
            self.jobs_done += 1
            self._latencies.append(latency_ms)
            if len(self._latencies) > _LATENCY_WINDOW:
                self._latencies.pop(0)
            if file_size_bytes > 0:
                self._size_lat.append((file_size_bytes, latency_ms))
                if len(self._size_lat) > _LATENCY_WINDOW:
                    self._size_lat.pop(0)
            self.bytes_sent += bytes_sent

    def record_tls_handshake(self, duration_ms: float):
        """Record the duration of a TLS handshake for overhead analysis."""
        with self._lock:
            self._tls_times.append(duration_ms)
            if len(self._tls_times) > _LATENCY_WINDOW:
                self._tls_times.pop(0)

    def record_job_failed(self):
        with self._lock:
            self.jobs_failed += 1

    def add_bytes_recv(self, n: int):
        with self._lock:
            self.bytes_recv += n

    # ------------------------------------------------------------------
    # Read values
    # ------------------------------------------------------------------

    def throughput_per_min(self) -> float:
        """Jobs completed per minute since start."""
        elapsed_min = (time.time() - self.start_time) / 60
        if elapsed_min < 0.01:
            return 0.0
        with self._lock:
            return round(self.jobs_done / elapsed_min, 2)

    def cpu_percent(self) -> float:
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1)
        except Exception:
            return 0.0

    def uptime_seconds(self) -> int:
        return int(time.time() - self.start_time)

    def snapshot(self) -> dict:
        """Return all metrics as a JSON-serialisable dict for the UI."""
        with self._lock:
            latencies_copy = list(self._latencies)
            tls_copy       = list(self._tls_times)
            size_lat_copy  = list(self._size_lat)
            jobs_done      = self.jobs_done
            jobs_failed    = self.jobs_failed
            bytes_sent     = self.bytes_sent
            bytes_recv     = self.bytes_recv

        avg_lat = (sum(latencies_copy) / len(latencies_copy)) if latencies_copy else 0.0
        min_lat = min(latencies_copy) if latencies_copy else 0.0
        max_lat = max(latencies_copy) if latencies_copy else 0.0
        avg_tls = (sum(tls_copy) / len(tls_copy)) if tls_copy else None

        return {
            'jobs_done'          : jobs_done,
            'jobs_failed'        : jobs_failed,
            'avg_latency_ms'     : round(avg_lat, 1),
            'min_latency_ms'     : round(min_lat, 1),
            'max_latency_ms'     : round(max_lat, 1),
            'throughput_per_min' : self.throughput_per_min(),
            'bytes_sent'         : bytes_sent,
            'bytes_recv'         : bytes_recv,
            'bytes_sent_mb'      : round(bytes_sent / 1_048_576, 2),
            'bytes_recv_mb'      : round(bytes_recv / 1_048_576, 2),
            'cpu_percent'        : self.cpu_percent(),
            'uptime_seconds'     : self.uptime_seconds(),
            # last 20 latency samples for the sparkline chart
            'latency_history'    : [round(x, 1) for x in latencies_copy[-20:]],
            # TLS overhead: avg handshake time in ms (None if TLS never used)
            'tls_handshake_avg_ms': round(avg_tls, 1) if avg_tls is not None else None,
            'tls_handshake_count' : len(tls_copy),
            # file-size vs latency scatter data (last 20 jobs)
            'size_latency_samples': [
                {'size_kb': round(s / 1024, 1), 'latency_ms': round(l, 1)}
                for s, l in size_lat_copy[-20:]
            ],
        }


# ---------------------------------------------------------------------------
# Quick standalone test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    m = Metrics()

    m.record_job_done(latency_ms=1200, bytes_sent=512_000)
    m.record_job_done(latency_ms=800,  bytes_sent=256_000)
    m.record_job_done(latency_ms=950,  bytes_sent=128_000)
    m.record_job_failed()
    m.add_bytes_recv(1_000_000)

    s = m.snapshot()
    for k, v in s.items():
        print(f"  {k:<22} {v}")

    assert s['jobs_done']    == 3
    assert s['jobs_failed']  == 1
    assert s['bytes_recv_mb'] == round(1_000_000 / 1_048_576, 2)
    assert 0 < s['avg_latency_ms'] < 1300

    print("\nmetrics.py self-test passed.")
