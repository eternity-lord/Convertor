# Validation report — 2026-10-14

## Executed in the development sandbox

- Python 3.11, Linux x86_64, PySide6/Qt 6.12.0, FFmpeg/ffprobe 6.0-static.
- Real Qt raster/offscreen rendering, not mocked Qt widgets. Native libraries were supplied in the sandbox's ignored local cache; they are **not** shipped in the project.
- Final result: **52 passed, 0 failed, 0 skipped** (`python -m pytest -q`, native Qt libraries and FFmpeg on the test environment's paths).
- Measured 52-test suite wall time: **13.04 seconds** including interpreter startup.
- Sampled peak RSS of the **entire test process tree**, including child FFmpeg/ffprobe: **163.4 MiB**. Sampled every 25 ms with psutil; shared pages can be counted more than once. This is a small-fixture development measurement, **not** a maximum-memory guarantee for actual 1080p/4K jobs.
- `python -m compileall -q app main.py` succeeded.
- FFmpeg binaries, temporary native libraries, virtual environment, test outputs and package downloads are excluded from Git.

## Coverage

### Pure policy and command tests

- H.264 levels for 1080p30, 1080p50/60, 2160p30/60, 720p25; reject limits above 5.2.
- Preserve 24000/1001, 30000/1001 and 60000/1001, including coarse timestamp timebases.
- Mixed cadence / nominal-average mismatch, duplicate timestamps, negative timestamps, B-frame packet order.
- Per-file Auto, explicit fixed rate, level auto-raising, odd dimensions, bitrate/VBV and FAT32 budget guards.
- Stream mapping, no deprecated `-vsync`, CFR/audio filters, software/full-GPU/hybrid command construction, Unicode paths.
- HEVC/AV1 capability-gated command building and AVC software fallback. These are **command tests**, not actual Intel hardware encodes.
- Faststart atom ordering, unsafe filename suffix rejection, output naming.
- Concurrent stderr drainage, bounded capture and timeout handling.

### Real FFmpeg tests (small generated media, 320×180)

- CPU CFR output at **all eight standard frame rates**; exact `r_frame_rate == avg_frame_rate == selected rate`, stream/audio/color policy and faststart checks.
- Deliberately VFR video created by changing the selection cadence halfway through the source; converted to fixed 25 CFR.
- 10-bit 4:2:2 AVC input through the complete worker → verified 8-bit 4:2:0 AVC output. Source retained; temporary output removed.
- Simulated advertised QSV availability with no physical device → a real QSV initialization failure → exactly one successful software retry.
- Audio starts approximately 250 ms after video; output audio/video duration check passes after offset-aware resampling.
- Full-file packet verification, including negative DTS checking at 29.97. An actual edit-list/movie-timescale rounding defect was found and fixed during this test.
- Interlaced source with both keep-interlaced and deinterlace paths, including output field-order inspection.
- Live FFmpeg process-tree pause/resume and stop/cancellation, with source retention and temporary-file cleanup. These control tests ran on Linux, not Windows.
- Recursive watch-folder stability and path/size/mtime identity, including same-named files in separate folders and a changed source.

### Qt tests

- Construct/show all eight pages; switch EN → فارسی → EN without replacing the queue.
- Construct/show Encoding window and operate segmented controls / keyboard toggle.
- Reserve distinct outputs for same-named sources sent to a common custom folder with two concurrent jobs.
- End-to-end GUI action: scan folder → per-file background preflight → queue dispatch → real encode → verification → automatic Encoding window → daily successful-file counter.
- Actual dashboard capture: [dashboard.png](dashboard.png). Telemetry is real; no fabricated conversion data/chart was added for the screenshot.

## Requested acceptance checklist: honest status

| Item | Status / evidence | Still requires |
|---|---|---|
| 1. Mixed-camera export → true CFR / TV playback | Synthetic VFR fixture passes cadence and exact-rate verification. All eight standard rates tested. | Actual EDIUS/Voukoder mixed-camera samples, visual cadence judgement and physical television playback. Timestamp tests do not prove “no stutter” or perceptual lip sync. |
| 2. 10-bit/4:2:2 → 8-bit/4:2:0, safe audio/container | Real 10-bit/4:2:2 CPU conversion, full worker, mapping, audio, color and faststart checks pass. | Actual EDIUS formats/HQX codecs and target TV firmware. No exhaustive AVC HRD/slice conformance validator is included. |
| 3. Correct 1080p50 and 4K30 levels | Pure policy tests: 4.2 and 5.1 respectively; lower requested level is raised. | Real high-resolution hardware tests; large fixtures were deliberately avoided for memory safety. |
| 4. QSV unavailable → libx264 | Actual failing hardware initialization and automatic software retry pass. | Genuine Intel full-GPU/hybrid throughput and supported driver combinations. |
| 5. Automatic progress / controls | Real GUI queue flow and auto-open tested; live fields, ETA, pause/resume and cancellation implemented. | Manual Windows process-tree pause/stop, tray, shutdown/sleep and long/parallel render soak tests. Current pane shows one selected job, not multiple stacked preview panes. |
| 6. Design language | Actual Qt render reviewed against supplied reference; fonts, rounded shell/cards, mauve pills, glow and chart included. | Not claimed pixel-identical. Manual Windows high-DPI/accessibility/RTL polish remains. Some technical descriptions remain English. |
| 7. Structured logs | Bounded card model, filters, expandable details, clipboard and exports implemented; Qt page smoke test passes. | Manual Windows clipboard/export workflow. Combined text search serves as file filter; stderr is a bounded tail, not unbounded history. |
| 8. Old features | Watch converter, Fixer, QSV controls, system tuning, guards, monitor and elevation ported. Risky process killing/working-set trimming intentionally replaced with reversible suspension/bounded scheduling. | Windows-only tuning/recovery and ImDisk/Defender/service tests. HEVC/AV1 hardware paths require real devices. See README's explicit limits. |

## Not claimed as tested or complete

- Physical TV / Intel iGPU operation or benchmark gains, driver-specific optimal concurrency, exact GPU generation or GPU load reporting.
- Exhaustive H.264 SPS/slice/HRD validation; bitstream GOP closure and VBV are set via encoder options rather than independently certified.
- Multi-gigabyte FAT32 splitting on actual removable media; full segment verification is implemented but large-file tests were not run here.
- Windows power-plan, service, BorisFX/registry, Defender, UAC and recovery behavior. Those cannot be safely exercised on a Linux machine.
- Per-row queue thumbnails, multiple simultaneous live preview cards, a separate per-job log dropdown, custom completion audio, native modern Windows toast integration, or automatic taskbar-minimize-to-tray behavior.
- Fully translated technical diagnostics and literal pixel-perfect reproduction of the dashboard image.

These limits are deliberate disclosures, not assertions that hardware-specific acceptance has already passed. Do not delete irreplaceable originals until a representative result has been checked on the actual playback device.
