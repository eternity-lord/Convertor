# BUG-FIX ROUND 1 — results

## Scope / availability

**`Khanum 1.mp4` and `Agha 1.mp4` are not present in the supplied repository or workspace.** They cannot truthfully be reported as rerun. Instead, real FFmpeg runs were performed on clearly identified synthetic **1920×1080 / 60000/1001 CFR** stand-ins with deliberately unsafe audio. The original files are still needed for the final camera-footage / TV acceptance check.

## Actual single-job GUI rerun

Environment: Linux x86_64, two logical cores, FFmpeg/ffprobe 6.0-static, real PySide6 raster/offscreen UI. CPU fallback, Safe mode, NORMAL priority, two encode threads, two filter threads, one job. No Intel iGPU is available here.

| Synthetic source | Source audio | Analysis | Output video | Output audio | Result |
|---|---|---:|---|---|---|
| `synthetic_Khanum_1080p5994.mp4` | AAC-LC, **6 channels, 96 kHz** | **0.0229 s** | H.264 High **4.2**, yuv420p, avc1, 1080p **59.94 CFR** | **AAC-LC, stereo, 48 kHz** | **TV-Safe ✓** |
| `synthetic_Agha_1080p5994.mp4` | **AC3, 6 channels, 48 kHz** | **0.0225 s** | H.264 High **4.2**, yuv420p, avc1, 1080p **59.94 CFR** | **AAC-LC, stereo, 48 kHz** | **TV-Safe ✓** |

Both runs intentionally used a **legacy `audio=Copy` option** to reproduce the bypass risk. The rebuilt command still used AAC-LC, 48 kHz, two channels and 192k; no audio copy was permitted.

Actual output fields for both:

```text
video codec_name     h264
video profile        High
video level          42 (= H.264 4.2)
video pix_fmt        yuv420p
video codec_tag      avc1
video width/height   1920 / 1080
r_frame_rate         60000/1001
avg_frame_rate       60000/1001
video start_time     0.033367 s
video duration       2.002000 s

audio codec_name     aac
audio profile        LC
audio channels       2
audio sample_rate    48000
audio start_time     0.012042 s
audio duration       2.023333 s

absolute start delta     21.325 ms (<40 ms)
absolute duration delta  21.333 ms (<40 ms)
verification violations  none
```

The UI ran a 100 ms heartbeat throughout conversion. **24 heartbeat ticks**, largest observed gap **104.25 ms**; no blocked UI interval occurred in this short run. Sampled peak RSS of the complete app/child-process tree was **600.8 MiB**. Shared pages may be counted repeatedly. These short two-second fixtures validate format and code paths, not long-film throughput, target Windows responsiveness or a maximum-memory bound.

Full commands, bounded stderr tails and source/output values are in [ROUND1_RESULTS.json](ROUND1_RESULTS.json). Synthetic videos and test-native libraries are in the ignored local cache, not Git.

## Changes covered

### A — Verification / output retention

- TV audio always AAC-LC stereo 48 kHz / 192k; legacy Copy cannot bypass it.
- Legal negative DTS is not judged. Stream start time / audio-video offset are checked instead.
- 59.94 is preserved by default; 1080p59.94/60 computes legal Level 4.2 per file.
- Only timing violations receive a single `-bf 0` software retry, retaining the first successful encode until completion.
- Incompatible / unverified outputs are kept and committed with a yellow Warning. Source deletion is disabled on warnings or unverified input analysis.
- Encoder failure reasons include exit code / stderr or missing output. Configuration/probe blockers use a separate reason-bearing Blocked status.
- Details contain exact commands, stderr and actual output ffprobe values, including failed retry diagnostics.

### B — Probe latency

- Whole-file packet scan removed completely, including post-encode inspection.
- First ~20 seconds plus three ~3-second middle/late/end windows; PTS only.
- Every ffprobe call has a 15-second deadline; a timed-out sample aborts remaining samples, marks Unverified and conversion can proceed.
- If metadata itself is unavailable, conservative geometry / explicitly selected rate (or 25 fps if unknown Auto) are used with a warning, not a fabricated successful analysis.
- Both pipes are drained in separate threads with bounded capture; probes run off the GUI thread.
- Live step text, per-row/context-menu Skip analysis with cancellation, clear missing-executable message.

### C — PC safety

- Safe-mode migration, single-job default, NORMAL priority, High priority opt-in.
- Reserved logical CPUs where possible, bounded safe-mode threads, filter threads 2, async depth 4, extra GPU upload frames 16.
- Free RAM <3 GiB forces one job and warns; sustained RAM >90% or CPU >95% for ten seconds pauses jobs and notifies. Explicit Resume is required. Guards remain in Performance mode.
- Power plan and core parking only change when their own checkboxes are selected. Crash recovery marker is kept; an opt-in priority guard no longer silently raises FFmpeg priority.
- Safe/Performance controls are mutually exclusive; multi-job / high-priority / performance choices show a warning. Existing encodes retain their launch policy.
- Encoding rendering throttled to at most two refreshes per second. Slow WMI temperature queries are isolated from the fast CPU/RAM guard, and a stopped/restored guard cannot race to reapply priority changes.

## Automated regression suite

Latest full suite: **81 passed, zero failed / skipped**, with real Qt and FFmpeg available. Includes the original eight standard-rate conversions and:

- Both actual 1080p59.94 / unsafe-audio cases above.
- Forced compatibility violations: yellow output retained, original retained even with deletion selected.
- A failed `-bf 0` timing retry retains the first successful output and both exact commands.
- Output ffprobe timeout becomes Warning; missing/nonempty output remains a reason-bearing Failed.
- Metadata/sample timeouts, PTS-only bounded windows / 15s deadlines, immediate analysis cancellation, missing ffprobe message.
- NORMAL Windows spawn flags versus explicit High (mocked; no Windows changes executed).
- Safe thread / affinity / frame limits, sustained overload thresholds, automatic queue pause/Resume, low-RAM one-job enforcement, settings migration and mode toggles.
- Power/parking remain untouched when only thermal/priority options are selected; write-ahead power-plan marker and next-start restoration are tested with mocked Windows commands.

**Still untested:** the actual two user files, real Windows Intel QSV / thermal/priority behavior, long-file/network-disk probe latency, Windows native notifications, physical TV playback. There is no claim that those hardware-specific acceptance items have already passed.
