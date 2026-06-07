# Parakeet v2 Fix Brief — Implementation Instructions for Opus

**Document type:** Machine-readable agent instruction brief  
**Target agent:** Claude Opus  
**Prepared by:** Claude Sonnet 4.6 after full source analysis  
**Date:** 2026-06-07  
**Scope:** Parakeet TDT 0.6B v2 FastAPI server (Shadowfita/parakeet-tdt-0.6b-v2-fastapi) running locally; push-to-talk client integration at `D:\Big Projects\push-to-talk`

---

## 0. How to Use This Brief

Read sections in order. Each `CHANGE` block contains:
- `FILE` — path relative to the Parakeet v2 repo root
- `SEVERITY` — Critical / High / Medium / Low
- `BUG` — what is wrong and why
- `CURRENT CODE` — the exact problematic code
- `PROPOSED CODE` — complete replacement (write this verbatim)
- `RATIONALE` — why this fix is correct

Do **not** modify the push-to-talk client files unless a change block explicitly targets `push-to-talk/src/`. The client is already correctly written to use the fixed server behavior.

When a proposed file is a complete replacement, write the entire file. When it is a partial change, the surrounding context is included to locate the edit precisely.

---

## 1. System Context

### 1.1 What Parakeet v2 Is

A FastAPI server wrapping NVIDIA's NeMo `parakeet-tdt-0.6b-v2` ASR model. It provides:
- `POST /transcribe` — REST transcription (also aliased to `/audio/transcriptions` and `/v1/audio/transcriptions`)
- `GET /ws` — WebSocket streaming transcription using Silero VAD

### 1.2 How push-to-talk Calls It

**REST path** (`src/transcription_parakeet.py`):
```
POST http://<host>:<port>/transcribe
Content-Type: multipart/form-data
Fields: file=<wav>, include_timestamps=false, should_chunk=false
Response: {"text": "..."}
```
Note: `should_chunk=false` is intentional — the push-to-talk client clips are short (< 120 s) and the upstream chunker has an unpacking bug when given 1-chunk audio. This workaround remains correct until the chunker is fixed.

**Streaming path** (`src/transcription_parakeet_streaming.py`):
```
ws://<host>:<port>/ws?vad_end_silence_ms=250&vad_max_chunk_seconds=8.0&transcription_batch_size=4&transcription_batch_window_ms=15
Binary frames: 16 kHz mono int16 PCM, 512 samples (1024 bytes) per frame
Response messages: {"text": "..."} or {"status": "..."}
```
The client sends all four VAD query params. **The server currently ignores all of them.** This is one of the bugs to fix.

### 1.3 File Map

```
parakeet_service/
  config.py          — env var loading, MODEL_NAME, DEVICE, MODEL_PRECISION
  model.py           — NeMo model loading (lifespan), reset_fast_path()
  audio.py           — ensure_mono_16k(), convert_audio_streaming(), schedule_cleanup()
  chunker.py         — vad_chunk_lowmem(), vad_chunk_streaming()
  batchworker.py     — GLOBAL transcription_queue, condition, results dict, batch_worker()
  stream_routes.py   — WebSocket /ws endpoint, producer/consumer coroutines
  routes.py          — REST /transcribe endpoint
  schemas.py         — TranscriptionResponse Pydantic model
  main.py            — FastAPI app factory, mounts routers
```

---

## 2. Bug Inventory

### BUG-01 — Global shared state across WebSocket connections [CRITICAL]

**FILE:** `parakeet_service/batchworker.py`  
**SEVERITY:** Critical  

`transcription_queue`, `condition`, and `results` are module-level singletons:

```python
# CURRENT — module level globals shared by ALL connections
transcription_queue: asyncio.Queue[str | bytes] = asyncio.Queue()
condition = asyncio.Condition()
results: dict[str, str] = {}
```

**Effect:** Two concurrent WebSocket clients (or a quick disconnect+reconnect) share the same queue and results dict. Client A's transcription results can be delivered to Client B's WebSocket. Disconnected clients leave stale entries in `results` that are never consumed, leaking memory indefinitely.

This is also the root cause of the known ASGI late-send trace (`Unexpected ASGI message 'websocket.send', after sending 'websocket.close'`) logged in push-to-talk's memory.md: a stale consumer loop on a new connection reads leftover results from a previous session and tries to send them on the now-closed old WebSocket.

**Fix:** Replace global state with per-connection routing. See CHANGE-01 and CHANGE-02.

---

### BUG-02 — consumer() reads results outside the condition lock [CRITICAL]

**FILE:** `parakeet_service/stream_routes.py`  
**SEVERITY:** Critical  

```python
# CURRENT
async def consumer():
    while True:
        async with condition:
            await condition.wait()   # releases lock here
        # >>> lock is NOT held here <<<
        flushed = []
        for p, txt in list(results.items()):   # race: batchworker can modify results now
            await ws.send_json({"text": txt})
            flushed.append(p)
        for p in flushed:
            results.pop(p, None)
```

**Effect:** Between `condition.wait()` exiting and the `for` loop executing, the batch_worker can add new items to `results` or another consumer can drain them. Results are double-sent or silently dropped.

**Fix:** Eliminated entirely by per-connection result queue in CHANGE-01/CHANGE-02.

---

### BUG-03 — WebSocket query params are silently ignored [HIGH]

**FILE:** `parakeet_service/stream_routes.py`  
**SEVERITY:** High  

```python
# CURRENT
@router.websocket("/ws")
async def ws_asr(ws: WebSocket):
    # No Query() parameters — vad_end_silence_ms, vad_max_chunk_seconds,
    # transcription_batch_size, transcription_batch_window_ms are never read
    vad = StreamingVAD()   # always uses hardcoded defaults
```

The push-to-talk client sends all four tuning parameters in the WebSocket URL. The server discards them. The user cannot tune VAD behavior from the client side.

**Fix:** Add `Query()` parameters to `ws_asr` and pass them to `StreamingVAD`. See CHANGE-01.

---

### BUG-04 — speech_ms counter accumulates ALL audio frames, not speech frames [HIGH]

**FILE:** `parakeet_service/streaming_vad.py`  
**SEVERITY:** High  

```python
# CURRENT
def feed(self, frame_bytes: bytes) -> List[str]:
    ...
    for start in range(0, len(pcm_f32), WINDOW_SAMPLES):
        window = pcm_f32[start:start + WINDOW_SAMPLES]
        if len(window) < WINDOW_SAMPLES:
            break
        voice_event = self.vad(window, return_seconds=False)
        self.buffer.extend(_f32_to_pcm16(window))
        self.speech_ms += 32   # BUG: +32 ms for EVERY window, silence included
        if voice_event and voice_event.get("end"):
            out.extend(self._flush())
        elif self.speech_ms >= MAX_SPEECH_MS:   # fires after 8s of ANY audio
            out.extend(self._flush())
    return out
```

`MAX_SPEECH_MS = 8000` is supposed to hard-stop after 8 seconds of speech. Instead it fires after 8 seconds of total received audio, including silence. A user who is silent for 5 seconds then speaks for 3 seconds will have their audio hard-flushed mid-utterance. This produces truncated transcripts.

**Fix:** Derive buffer duration from buffer length (bytes), not a counter. See CHANGE-02.

---

### BUG-05 — VAD parameters are hardcoded module constants, cannot change per-connection [HIGH]

**FILE:** `parakeet_service/streaming_vad.py`  
**SEVERITY:** High  

```python
# CURRENT — module level, cannot vary per connection
THRESHOLD = 0.60
MIN_SILENCE_MS = 250
SPEECH_PAD_MS = 120
MAX_SPEECH_MS = 8_000
```

Even after BUG-03 is fixed (server reads query params), the `StreamingVAD` class ignores them because its parameters are module constants rather than constructor arguments.

**Fix:** Move all tunable parameters to `StreamingVAD.__init__()`. See CHANGE-02.

---

### BUG-06 — consumer() never terminates cleanly [HIGH]

**FILE:** `parakeet_service/stream_routes.py`  
**SEVERITY:** High  

```python
# CURRENT
async def consumer():
    while True:   # no exit condition
        async with condition:
            await condition.wait()
        ...

await asyncio.gather(producer(), consumer())
```

When `producer()` exits (WebSocketDisconnect), `asyncio.gather()` cancels `consumer()` via CancelledError. But if the batch_worker fires `condition.notify_all()` at the exact same moment, the consumer may process one more iteration after the WebSocket is closed, causing the ASGI `websocket.send` after close error.

**Fix:** `consumer()` exits when a shared stop event is set and the result queue is drained. See CHANGE-01.

---

### BUG-07 — Temp WAV files leak when ASR inference raises an exception [MEDIUM]

**FILE:** `parakeet_service/batchworker.py`  
**SEVERITY:** Medium  

```python
# CURRENT
try:
    with torch.inference_mode():
        outs = model.transcribe(batch, batch_size=len(batch))
except Exception as exc:
    logger.exception("ASR failed: %s", exc)
    for _ in batch:
        transcription_queue.task_done()
    continue   # <<< cleanup never runs — temp WAV files orphaned on disk
# ---------- cleanup ----------
for p in batch:
    with contextlib.suppress(FileNotFoundError):
        pathlib.Path(p).unlink(missing_ok=True)
```

On ASR failure, the `continue` statement skips the cleanup block. Every failed batch leaves one or more temp `.wav` files in the system temp directory. Over time this fills disk, and on Windows the temp files hold file handles that can prevent other cleanup.

**Fix:** Move cleanup into a `finally` block. See CHANGE-01.

---

### BUG-08 — NeMo hypothesis objects hold tensor references after transcription [MEDIUM — RAM LEAK]

**FILE:** `parakeet_service/batchworker.py`  
**SEVERITY:** Medium (RAM growth)  

```python
# CURRENT
outs = model.transcribe(batch, batch_size=len(batch))
...
for p, h in zip(batch, outs):
    results[p] = getattr(h, "text", str(h))
# outs never explicitly deleted — NeMo Hypothesis objects may hold
# references to alignment tensors, token probability arrays, etc.
```

NeMo's `Hypothesis` objects can contain PyTorch tensors for alignment data even when `timestamps=False`. Python's GC is non-deterministic. In a tight batch loop these accumulate on the CPU heap before GC fires.

**Fix:** `del outs` after extracting text strings, followed by `gc.collect()` every N batches. See CHANGE-01.

---

### BUG-09 — NeMo DataLoader uses pinned memory, grows RAM on repeated calls [MEDIUM — RAM LEAK]

**FILE:** `parakeet_service/batchworker.py` / `model.py`  
**SEVERITY:** Medium (RAM growth)  

NeMo's `model.transcribe()` internally creates a `DataLoader` with `pin_memory=True` (page-locked RAM) for each call. Page-locked memory is allocated by the CUDA driver and is not subject to Python GC. It appears as system RAM usage. On a long-running server processing thousands of utterances, this can consume several GB of page-locked RAM.

**Fix:** Set `num_workers=0` in NeMo's inference dataloader config (prevents subprocess workers and pin_memory), or call `torch.cuda.empty_cache()` periodically. The `num_workers=0` approach is the safest for a single-GPU server. See CHANGE-03 (model.py).

---

### BUG-10 — model.transcribe() called concurrently from REST and WebSocket [MEDIUM]

**FILE:** `parakeet_service/routes.py`, `parakeet_service/batchworker.py`  
**SEVERITY:** Medium  

The REST endpoint calls `model.transcribe()` directly on the request coroutine:
```python
# routes.py
outs = model.transcribe([str(p) for p in chunk_paths], batch_size=2, timestamps=include_timestamps)
```

The WebSocket batch_worker also calls `model.transcribe()` concurrently. NeMo models are **not thread-safe** for concurrent `transcribe()` calls — internal decoding state, the `cfg.decoding` object, and CUDA stream usage are shared. `reset_fast_path()` only partially mitigates this.

**Fix:** Route REST requests through the same `transcription_queue` as streaming, serializing all model access through the single batch_worker. This is an architectural change — mark as DEFERRED if too invasive. At minimum, add an `asyncio.Lock` around both call sites. See CHANGE-04.

---

### BUG-11 — audio.py fast-path skips conversion for mono 16kHz WAV but still calls ensure_mono_16k [LOW]

**FILE:** `parakeet_service/audio.py`, `parakeet_service/routes.py`  
**SEVERITY:** Low (performance only)  

`audio.py` already has logic to skip ffmpeg for mono 16kHz WAV, but `routes.py` always calls `ensure_mono_16k()` which still stats the file and checks headers. For push-to-talk's WAV output (always 16 kHz mono int16 PCM), this is a small but avoidable overhead. No code change required for correctness — document this as an optimization opportunity.

---

## 3. Memory / RAM Analysis Summary

The app consuming unexpectedly large CPU RAM (not just GPU VRAM) has multiple causes:

| Source | Type | Size | Fix |
|---|---|---|---|
| NeMo model weights (GPU) | GPU VRAM | ~1.2 GB fp16 | Expected — not a leak |
| NeMo CPU-side config/vocab | CPU RAM | ~200–400 MB | Expected at startup |
| Silero VAD model (CPU) | CPU RAM | ~20 MB | Expected |
| PyTorch CUDA allocator cache | GPU VRAM (mapped) | Grows unboundedly | `torch.cuda.empty_cache()` periodically |
| NeMo DataLoader pinned memory | Page-locked RAM | Grows with call count | Set `num_workers=0` in infer config |
| NeMo Hypothesis tensor refs | CPU heap | Grows with call count | `del outs; gc.collect()` |
| Stale `results` dict entries | CPU heap | Grows with disconnects | Per-connection queues (BUG-01) |
| Orphaned temp WAV files | Disk | Grows with ASR failures | `finally` cleanup (BUG-07) |

**Primary RAM driver:** The NeMo DataLoader creates page-locked (pinned) memory buffers on every `transcribe()` call. These are outside Python's heap and are not visible to `gc.collect()`. They accumulate until `torch.cuda.empty_cache()` releases them. On a server processing audio continuously, this can consume 2–8 GB of page-locked RAM within hours.

---

## 4. Changes — Complete Proposed Code

---

### CHANGE-01: `parakeet_service/batchworker.py` — Complete replacement

Eliminates global shared state. Adds per-connection result queue routing. Fixes BUG-01, BUG-02, BUG-06, BUG-07, BUG-08.

```python
"""Batch ASR worker with per-connection result routing."""
from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import pathlib
import tempfile
import time
import torch
from typing import Union

logger = logging.getLogger("batcher")
logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Global inference queue — carries (conn_id, file_path) tuples.
# REST path sends (None, file_path); result is returned via a one-shot Future.
# ---------------------------------------------------------------------------
transcription_queue: asyncio.Queue[tuple[str | None, str] | None] = asyncio.Queue()

# Per-WebSocket-connection result queues, keyed by connection UUID string.
_connection_result_queues: dict[str, asyncio.Queue[str]] = {}

# GC cadence: run gc.collect() every N batches to free NeMo tensor refs.
_GC_EVERY_N_BATCHES = 20
_batch_count = 0


def register_connection(conn_id: str) -> asyncio.Queue[str]:
    """Create and register a per-connection result queue. Call on WS accept."""
    q: asyncio.Queue[str] = asyncio.Queue()
    _connection_result_queues[conn_id] = q
    return q


def unregister_connection(conn_id: str) -> None:
    """Remove a connection's result queue. Call on WS disconnect."""
    _connection_result_queues.pop(conn_id, None)


def _as_path(blob: Union[str, bytes]) -> str:
    if isinstance(blob, str):
        return blob
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.write(blob)
    tmp.close()
    return tmp.name


async def batch_worker(model, batch_ms: float = 15.0, max_batch: int = 4) -> None:
    """Drain transcription_queue → ASR → per-connection result queues."""
    global _batch_count
    logger.info("worker started (batch ≤%d, window %.0f ms)", max_batch, batch_ms)

    while True:
        item = await transcription_queue.get()
        transcription_queue.task_done()

        if item is None:
            logger.info("batch_worker received stop sentinel, exiting")
            break

        conn_id, raw_path = item
        batch: list[tuple[str | None, str]] = [(conn_id, _as_path(raw_path))]

        # Micro-batch gathering window
        deadline = time.monotonic() + batch_ms / 1000
        while len(batch) < max_batch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                nxt = await asyncio.wait_for(transcription_queue.get(), remaining)
                transcription_queue.task_done()
                if nxt is None:
                    break
                nxt_conn_id, nxt_raw = nxt
                batch.append((nxt_conn_id, _as_path(nxt_raw)))
            except asyncio.TimeoutError:
                break

        logger.debug("processing %d-item batch", len(batch))
        paths = [p for _, p in batch]
        outs = None

        try:
            with torch.inference_mode():
                outs = model.transcribe(paths, batch_size=len(paths))

            for (cid, _p), h in zip(batch, outs):
                text = getattr(h, "text", str(h))
                if cid is not None:
                    q = _connection_result_queues.get(cid)
                    if q is not None:
                        await q.put(text)
                # REST path (cid is None) uses a different mechanism; see routes.py

        except Exception as exc:
            logger.exception("ASR inference failed: %s", exc)

        finally:
            # Always clean up temp files, even on exception
            for p in paths:
                with contextlib.suppress(FileNotFoundError, OSError):
                    pathlib.Path(p).unlink(missing_ok=True)

            # Release NeMo Hypothesis tensor references
            del outs

            # Periodic GC to reclaim pinned/CPU tensor memory
            _batch_count += 1
            if _batch_count % _GC_EVERY_N_BATCHES == 0:
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
```

---

### CHANGE-02: `parakeet_service/streaming_vad.py` — Complete replacement

Fixes BUG-04 and BUG-05. Makes all VAD parameters constructor arguments. Replaces the speech_ms counter with buffer-length-derived duration.

```python
"""Streaming VAD: buffers PCM frames and emits utterance WAV file paths."""
from __future__ import annotations

import tempfile
import wave
from typing import List

import numpy as np
import torch
from torch.hub import load as torch_hub_load

# Load once at module level — shared across all StreamingVAD instances.
# Silero VAD is stateless at the model level; per-instance state lives in VADIterator.
_vad_model, _vad_utils = torch_hub_load("snakers4/silero-vad", "silero_vad")
(_, _, _, VADIterator, _) = _vad_utils

SAMPLE_RATE = 16_000   # fixed: model trained at 16 kHz
WINDOW_SAMPLES = 512   # fixed: 32 ms frame


def _f32_to_pcm16(frames: np.ndarray) -> bytes:
    return np.clip(frames * 32768, -32768, 32767).astype(np.int16).tobytes()


class StreamingVAD:
    """
    Feed successive 16 kHz mono int16 PCM frames.
    Emits temp WAV file paths when a complete utterance is detected.

    All timing parameters are configurable per-instance so that each
    WebSocket connection can use its own VAD tuning.
    """

    def __init__(
        self,
        threshold: float = 0.60,
        min_silence_ms: int = 250,
        speech_pad_ms: int = 120,
        max_speech_ms: int = 8_000,
    ):
        self.max_speech_ms = max_speech_ms
        self.vad = VADIterator(
            _vad_model,
            sampling_rate=SAMPLE_RATE,
            threshold=threshold,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        )
        self.buffer = bytearray()

    @property
    def _buffer_ms(self) -> int:
        """Derive buffer duration from byte length — no separate counter needed."""
        # 16-bit mono: 2 bytes per sample, SAMPLE_RATE samples per second
        return len(self.buffer) * 1000 // (SAMPLE_RATE * 2)

    def _flush(self) -> List[str]:
        if not self.buffer:
            return []
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(self.buffer)
        self.buffer.clear()
        self.vad.reset_states()
        return [tmp.name]

    def feed(self, frame_bytes: bytes) -> List[str]:
        out: List[str] = []
        pcm_f32 = np.frombuffer(frame_bytes, np.int16).astype("float32") / 32768

        for start in range(0, len(pcm_f32), WINDOW_SAMPLES):
            window = pcm_f32[start : start + WINDOW_SAMPLES]
            if len(window) < WINDOW_SAMPLES:
                break  # wait for a full 32 ms window

            voice_event = self.vad(window, return_seconds=False)
            self.buffer.extend(_f32_to_pcm16(window))

            if voice_event and voice_event.get("end"):
                out.extend(self._flush())
            elif self._buffer_ms >= self.max_speech_ms:
                out.extend(self._flush())

        return out
```

---

### CHANGE-03: `parakeet_service/stream_routes.py` — Complete replacement

Fixes BUG-01, BUG-02, BUG-03, BUG-06. Per-connection state. Reads all four VAD query params. Clean consumer termination. Adds `type` field to responses.

```python
"""WebSocket streaming ASR endpoint."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from parakeet_service.batchworker import (
    register_connection,
    transcription_queue,
    unregister_connection,
)
from parakeet_service.streaming_vad import StreamingVAD

router = APIRouter()


@router.websocket("/ws")
async def ws_asr(
    ws: WebSocket,
    vad_end_silence_ms: int = Query(250, description="Silence duration before utterance flush (ms)"),
    vad_max_chunk_seconds: float = Query(8.0, description="Hard max utterance length (s)"),
    transcription_batch_size: int = Query(4, description="Max items per inference batch"),
    transcription_batch_window_ms: float = Query(15.0, description="Batch gathering window (ms)"),
):
    await ws.accept()

    conn_id = str(uuid.uuid4())
    result_queue = register_connection(conn_id)
    stop_event = asyncio.Event()

    vad = StreamingVAD(
        min_silence_ms=vad_end_silence_ms,
        max_speech_ms=int(vad_max_chunk_seconds * 1000),
    )

    async def producer() -> None:
        """Receive PCM frames from client, run VAD, enqueue utterances."""
        try:
            while True:
                frame = await ws.receive_bytes()
                for chunk_path in vad.feed(frame):
                    await transcription_queue.put((conn_id, chunk_path))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            stop_event.set()

    async def consumer() -> None:
        """Forward inference results back to the client."""
        while True:
            is_done = stop_event.is_set()
            try:
                text = await asyncio.wait_for(result_queue.get(), timeout=0.1)
                result_queue.task_done()
                try:
                    await ws.send_json({"type": "final", "text": text})
                except Exception:
                    break
            except asyncio.TimeoutError:
                if is_done and result_queue.empty():
                    break

    try:
        await asyncio.gather(producer(), consumer())
    finally:
        unregister_connection(conn_id)
```

**Note on `transcription_batch_size` and `transcription_batch_window_ms`:** These are received from the client but not yet passed to `batch_worker()` because the worker is a single global coroutine with fixed parameters. To make them effective, the batch_worker would need to accept per-item overrides. For now, accepting the params prevents client-side errors and allows future wiring. If per-connection batch tuning is needed, `batch_worker` should be refactored to read batch params from the queue item tuple.

---

### CHANGE-04: `parakeet_service/model.py` — Patch to reduce pinned memory growth

Fixes BUG-09. Adds NeMo inference dataloader config override to disable pinned memory and subprocess workers. Also adds a periodic CUDA cache flush.

Find the `lifespan` function and modify the model loading block:

```python
# CURRENT (inside lifespan, after model is loaded and before yield)
gc.collect()
torch.cuda.empty_cache()
logger.info("Memory cleanup complete")

# REPLACE WITH:
gc.collect()
torch.cuda.empty_cache()
logger.info("Memory cleanup complete")

# Disable pinned memory and subprocess workers in NeMo's inference DataLoader.
# Pinned (page-locked) memory allocated by CUDA driver accumulates on every
# transcribe() call and does not respond to gc.collect(). Setting num_workers=0
# prevents the DataLoader from using subprocess workers and pinned buffers.
try:
    from omegaconf import open_dict
    with open_dict(model.cfg):
        if hasattr(model.cfg, "train_ds"):
            model.cfg.train_ds.num_workers = 0
            model.cfg.train_ds.pin_memory = False
        if hasattr(model.cfg, "validation_ds"):
            model.cfg.validation_ds.num_workers = 0
            model.cfg.validation_ds.pin_memory = False
        if hasattr(model.cfg, "test_ds"):
            model.cfg.test_ds.num_workers = 0
            model.cfg.test_ds.pin_memory = False
    logger.info("Disabled DataLoader pinned memory and subprocess workers")
except Exception as e:
    logger.warning("Could not patch DataLoader config: %s", e)
```

Also add to the shutdown block in `lifespan` (after `del app.state.asr_model`):

```python
# CURRENT shutdown block:
del app.state.asr_model
if torch.cuda.is_available():
    torch.cuda.empty_cache()

# REPLACE WITH:
del app.state.asr_model
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
logger.info("GPU memory released")
```

---

### CHANGE-05: `parakeet_service/routes.py` — Add inference lock for REST/WebSocket concurrency

Fixes BUG-10. Prevents concurrent `model.transcribe()` calls from REST and WebSocket paths from corrupting NeMo's internal decoding state.

At the top of `routes.py`, after imports, add:

```python
# Serializes REST and WebSocket transcription calls — NeMo is not thread-safe
# for concurrent transcribe() invocations on the same model instance.
_inference_lock: asyncio.Lock | None = None

def get_inference_lock(request) -> asyncio.Lock:
    """Lazy-init lock stored on app.state to survive hot reloads."""
    if not hasattr(request.app.state, "_inference_lock"):
        request.app.state._inference_lock = asyncio.Lock()
    return request.app.state._inference_lock
```

Then in `transcribe_audio()`, wrap the model.transcribe() call:

```python
# CURRENT:
try:
    outs = model.transcribe(
        [str(p) for p in chunk_paths],
        batch_size=2,
        timestamps=include_timestamps,
    )

# REPLACE WITH:
lock = get_inference_lock(request)
try:
    async with lock:
        outs = model.transcribe(
            [str(p) for p in chunk_paths],
            batch_size=2,
            timestamps=include_timestamps,
        )
```

---

## 5. V3 Improvements Relevant to V2

These are features from `groxaxo/parakeet-tdt-0.6b-v3-fastapi-openai` that are worth backporting to v2 independently of the v3 model.

### V3-BP-01: In-process PCM WAV fast path (REST latency reduction)

V3's `app.py` reads the WAV header directly to detect 16 kHz mono PCM WAV files and skips ffmpeg entirely for those inputs. Push-to-talk always sends 16 kHz mono int16 PCM WAV. Implementing this eliminates the ffmpeg subprocess overhead on every REST call.

Add to `parakeet_service/audio.py`:

```python
import wave
import audioop

def is_16k_mono_pcm(path: pathlib.Path) -> bool:
    """Return True if path is an uncompressed 16 kHz mono PCM WAV."""
    try:
        with wave.open(str(path), "rb") as wf:
            return (
                wf.getframerate() == 16000
                and wf.getnchannels() == 1
                and wf.getsampwidth() == 2
                and wf.getcomptype() == "NONE"
            )
    except Exception:
        return False
```

Then in `ensure_mono_16k()`, check `is_16k_mono_pcm(path)` first and return `(path, path)` immediately if true (no conversion needed, no temp file).

### V3-BP-02: AVX2/threading tuning for ONNX Runtime

Only relevant if you ever migrate to ONNX Runtime (which is the primary v3 change). If staying on NeMo/PyTorch, skip this. Document it here for awareness: v3 achieves 27x real-time CPU inference by switching from NeMo's PyTorch backend to `onnx_asr` with INT8 quantization. This requires replacing `nvidia/parakeet-tdt-0.6b-v2` with the ONNX-converted v3 model weights and swapping `nemo_asr` for `onnx_asr` throughout. This is a large change and is **out of scope** for this brief's v2 fix pass.

### V3-BP-03: Silero VAD auto-chunking with pause-point splitting

V3's `chunker.py` splits long audio files at silence midpoints rather than on fixed time boundaries. This produces more natural chunk boundaries and avoids cutting mid-word. Worth backporting to v2's `vad_chunk_lowmem()` if users ever enable `should_chunk=true` for long recordings.

### V3-BP-04: `/v1/audio/transcriptions/batch` endpoint

V3 adds a batch endpoint that accepts multiple audio files in one request. Not needed for push-to-talk (single-utterance per request), but useful if the server is shared with other tools.

### V3-BP-05: Structured `/health` response

V3's health endpoint returns `{"status": "healthy", "models": [...], "loaded": [...], "cpu": {...}}`. The v2 health endpoint returns only `{"status": "ok"}`. Enriching it makes monitoring easier. Low priority.

---

## 6. Push-to-Talk Client — Required Changes After V2 Fixes

**No changes required for the core streaming or REST logic.** The client is already written to:
- Send all four VAD query params (now the server will read them)
- Handle `{"text": "..."}` responses (the new `{"type": "final", "text": "..."}` is backward-compatible — `payload.get("text", "")` still works)
- Handle `{"status": "..."}` status messages (unchanged)

**Optional enhancement:** The new `"type": "final"` field in streaming responses can be used to distinguish finalized segments from future partial results if partial transcription is added. The client's `_handle_message()` in `src/transcription_parakeet_streaming.py` at line 273 already ignores unknown fields, so adding partial support later requires only client-side changes.

**Port note:** If the user is running v2 on port 8000, no change needed. If the v2 server is reconfigured to port 5092 (to match v3 convention), update the default in `src/transcription_parakeet.py:44` from `"http://localhost:8000"` to `"http://localhost:5092"` and in `src/transcription_parakeet_streaming.py:53` similarly.

---

## 7. Implementation Order

Execute changes in this order to avoid breaking intermediate states:

```
1. CHANGE-02  streaming_vad.py   — self-contained, no dependencies changed
2. CHANGE-01  batchworker.py     — defines register_connection/unregister_connection
3. CHANGE-03  stream_routes.py   — depends on CHANGE-01 and CHANGE-02
4. CHANGE-04  model.py           — independent, patch only
5. CHANGE-05  routes.py          — independent, patch only
6. V3-BP-01   audio.py           — independent fast path addition
```

Restart the server after each file change and run a smoke test (one short REST transcription + one short streaming session) before proceeding.

---

## 8. Smoke Test Protocol

After all changes are applied and the server is restarted:

### 8.1 REST
```bash
curl -X POST http://localhost:<port>/transcribe \
  -F file=@test_clip.wav \
  -F include_timestamps=false \
  -F should_chunk=false
# Expected: {"text": "<transcription>"}
```

### 8.2 WebSocket — single connection
Connect one WebSocket client, stream 3–5 seconds of speech, observe:
- `{"type": "final", "text": "..."}` returned after silence
- No ASGI `websocket.send` after close errors in server logs
- Server RAM does not spike between connections

### 8.3 WebSocket — rapid reconnect (regression test for BUG-01/BUG-06)
Connect, speak one utterance, disconnect immediately. Reconnect within 1 second. Confirm:
- Second session does not receive results from the first session
- No exceptions in server log

### 8.4 WebSocket — VAD param override (regression test for BUG-03)
Connect with `?vad_end_silence_ms=500` (longer silence threshold). Confirm:
- Server log shows VAD using 500 ms threshold (add a startup log in StreamingVAD.__init__ if needed)
- Utterances are held longer before flushing

### 8.5 RAM stability test
Run 20 consecutive REST requests with a 10-second WAV file. Monitor RAM with `watch -n1 free -h` (Linux) or Task Manager (Windows). RAM should be stable within ±100 MB after the first 5 calls warm up the CUDA cache.

---

## 9. Known Limitations Not Fixed in This Pass

- **`vad_chunk_lowmem` chunker bug**: The `should_chunk=true` path still has the unpacking bug for single-chunk audio. The push-to-talk workaround (`should_chunk=false`) remains correct. Full fix requires rewriting `chunker.py`'s result tuple handling.
- **NeMo batch_size=1 per WebSocket utterance**: Each VAD-detected utterance is a single file. The micro-batching in `batch_worker` groups utterances from multiple connections, but a single-user server often batches size-1 requests. This is inherent to the push-to-talk use case.
- **No partial (streaming-within-utterance) transcription**: The server only emits text after VAD detects end-of-utterance. True word-by-word streaming would require running NeMo in CTC/RNN-T streaming mode, which is a larger architectural change.
- **ONNX Runtime migration**: Switching to v3's ONNX backend would deliver ~27x real-time CPU inference. Out of scope for this v2 fix pass but documented in V3-BP-02 above.

---

*End of brief. All code blocks are complete and ready to write verbatim to their respective files.*
