# Round 3 · Phase 1 — Universal hardware engine

Phases 2–4 are **not implemented by this change**. The TV/HQX MP4 workflow remains
H.264 / HEVC / AV1; the matrix also inventories VP9, MPEG-2, MJPEG and software AV1
encoders for future workflows. It does not pretend those are TV-safe AVC outputs.

## Architecture and evidence

- `core/hardware/detect.py`: WMI JSON inventory (PCI vendor/device, driver, reported
  RAM), optional `nvidia-smi`, Linux read-only DRM inventory, active FFmpeg version,
  build flags, encoders/decoders/hwaccels/filters/muxers/demuxers/pixel formats.
- `matrix.py`: JSON-serializable GPU profiles and encoder evidence. Unknown upper
  limits / HDR are `None`, not inferred from product names. WMI AdapterRAM can be
  truncated by Windows; the UI labels it as reported memory.
- `probe.py`: sequential worker tests, one-second synthetic encodes, **10-second
  subprocess timeout per test**, cancellation, exact commands, diagnostics and
  FFmpeg-reported speed. Full scans can take several minutes; progress and Cancel
  are available on Hardware. No probe runs in the GUI thread.
- Cache: FFmpeg + FFprobe paths/stat/version, GPU/driver inventory, CUDA visibility,
  probe revision and seven-day expiry. Failed/partial cancelled scans are not
  saved. Re-scan bypasses cache. No source media is used by startup probes.
- QSV: DXVA2 → D3D11VA → plain → `qsv=hw:hw_any`; first working encode device per
  encoder is retained. HD 4600/Haswell has no low_power probes/flags, no HEVC/AV1
  encode, no ten-bit encode, and only eight-bit H.264 hardware decode in jobs.
  Other Intel generations can test low_power values, but no failed value is emitted.
- Encoder features are gated by both `-h encoder=...` and actual successful tests.
  Rate-control modes, presets, B-frames, optional controls and pixel formats keep
  their own evidence. The mapper emits tested values, not just known option names.
  An exact-source/flags 0.2-second MP4 hardware preflight runs before each hardware
  attempt. Its command and stderr are retained alongside the actual encode attempt.
- Encoder tests establish eight-bit 1080p60 and, independently, UHD30 when they
  pass. Ten-bit / 4:2:2 / 4:4:4 tests are independent. **These are tested samples,
  not maximum resolution/fps guarantees.** Hardware outside these proven envelopes
  is not selected; another tested device or software encoder is used, visibly,
  without reducing the requested output dimensions/rate/codec.
- Real compressed H.264/HEVC/VP9/AV1/MPEG-2 samples (eight-/ten-bit when a sample
  producer works) exercise QSV/CUDA and listed generic Windows/Vulkan decode.
  No producer means **untested**, not supported. For QSV/CUDA, GPU filters require
  a separate complete decode→filter→encode test for the selected encoder.

## Deliberate limits, not hidden promises

- WMI is not a DXGI ordinal API. Intel/AMD default-runtime devices are explicitly
  labelled; **physical affinity is not verified**. Several same-vendor Intel/AMD
  adapters without a reliable binding are grouped in a runtime-selected slot,
  not falsely presented as separately routable devices. NVIDIA uses tested runtime
  ordinals when `nvidia-smi` is present; CUDA_VISIBLE_DEVICES can remap identities.
  Different vendors and separately tested NVIDIA ordinals have separate slots.
- Generic D3D11VA/DXVA2/D3D12VA/Vulkan decode tests appear in the evidence, but are
  not assigned to a QSV/CUDA/AMF encoder without same-device transfer proof. AMF
  uses explicit NV12 CPU frames and its internal upload, rather than an invented
  unsupported hardware-upload command. The UI/logs say CPU decode/filters.
- Ten-bit / 4:2:2 / ProRes / DNx sources intentionally use CPU decode. TV output
  remains eight-bit 4:2:0. No untested HDR processing or HDR preservation is claimed.
- Synthetic speed ranks proven hardware encoders; it is **not** a prediction for
  deinterlacing/interpolation or high-resolution real input. Hardware is preferred
  before CPU even if a tiny CPU synthetic test reports a higher speed.
- Quality maps to tested QSV ICQ, NVENC CQ, AMF CQP or CPU CRF/QP. Unsupported
  requests visibly substitute tested VBR. AMF CQP is intentional explicit QP mode,
  not the accidental default-CQP warning addressed in the previous round.
- Preset mappings are Fast / Balanced / High Quality / Max Quality. The per-job
  Global choice retains the existing QSV Advanced preset setting and translates it
  into that shared model for every vendor. If a desired
  preset was not proven, a tested preset is substituted and logged. Failed flags
  are greyed in Hardware evidence; unavailable vendor/codec/RC choices are disabled.
- Custom tool paths apply only while the queue and scan are idle. Missing/invalid
  selected tools are errors, never silently replaced by PATH executables.
- The PyInstaller spec retains bundled assets/tools and requests `uac_admin=True`.
  Help assets from Phase 4 will already be included by the existing assets rule.

## Queue / recovery

Global and per-page/per-queued-item choices: Global / Auto / vendor / tested runtime
slot / CPU. Target codec is preserved when changing vendors or using software.

Safe defaults remain one global job, NORMAL priority and resource guards. Opt-in
Use all GPUs distributes work under the chosen global limit; it does not increase
that limit. In Safe mode each tested device slot has at most one active session,
including attempts that reach another GPU through fallback. CPU is serial. A
thread-safe session guard supplements the scheduler so two fallback workers cannot
overbook the same device.

`If hardware fails` supports Ask / Use next / Use CPU / Stop. Ask is an asynchronous
GUI dialog backed by a cancellable worker event. Use next excludes failed encoders;
a source-specific decode/filter failure may first retry the same encoder with CPU
decode and NV12 upload. Attempts are bounded. Error explanations have English and
Persian messages/hints; exact commands and raw diagnostics remain in Details.
The Encoding window, Logs and preview tooltip identify the actual pipeline and
any substitution. Verification, timing repair, source preservation and atomic
output publication continue to use the existing TV-safe engine.

## Verification performed in the development sandbox

- Real FFmpeg 7.0.2 / FFprobe CPU conversions, including Unicode filenames, CFR,
  AAC/faststart verification, HEVC target preservation, missing-device preflight
  and CPU recovery.
- CPU-only inventory/probe run; a slow test that exceeded ten seconds was rejected
  instead of being marked supported.
- Mocked Haswell-only, Intel+NVIDIA, AMD-only, NVIDIA-only, CPU-only and missing-NVENC
  matrices; all QSV init fallback steps; failed ICQ/AQ flags; decoder evidence;
  multi-GPU dispatch/session limits; cache invalidation; Ask/next/CPU/Stop recovery.
- Native Windows Intel/NVIDIA/AMD execution is **not available in the sandbox**.
  The manual checks below are required before claiming hardware acceptance.
- Native Qt GUI tests require libGL/EGL/xkbcommon/dbus. The CI workflow installs
  these; a headless sandbox without them skips the GUI module, not the core tests.

## PC acceptance checklist (HD 4600 + NVIDIA)

1. Start with matching FFmpeg/FFprobe 7.x. Open **Hardware → Re-scan hardware**.
   Confirm responsive navigation, scan progress, cancellation and completion.
2. Check both physical GPU names, driver versions and PCI IDs. On Intel's card,
   `h264_qsv` must have ✓ and a measured speed; Details must show
   `qsv=hw,child_device_type=dxva2`. HEVC/AV1/ten-bit/low_power must not be enabled
   for Haswell. Settings must show the active binary/version/build flags/device.
3. On NVIDIA's card, NVENC is usable **only if its actual encode succeeds**.
   A build without NVENC or a driver failure must give a greyed choice and reason.
   Not every GeForce model has NVENC; a listing alone is insufficient.
4. Set global Hardware=Intel; Fixer Hardware=Global; encode an eight-bit H.264 file.
   Expect Intel QSV (DXVA2), QSV decode only when the corresponding real decode
   test passed, and explicit NV12 VPP or download/filter/upload. Inspect Details.
5. Repeat with ten-bit H.264, HEVC, and HQX/ProRes input. Expect CPU decode → NV12
   upload → H.264 QSV, **not** full CPU encode. Check no low_power flag occurs.
6. Select NVIDIA explicitly (if ✓); encode H.264 and HEVC. Expect NVENC and CUDA
   decode/GPU filters only when proven. Compare CFR, dimensions, AAC and TV
   verification with Intel/CPU runs. Try each quality/speed + VBR/CBR/Quality mode.
7. Select CPU. Confirm `CPU libx264` and a successful verified result without any
   hardware initialization. If HEVC target is chosen, fallback must stay HEVC.
8. To exercise recovery safely, finish the scan, then make the selected GPU busy
   with another app or disable it **before the next queued attempt**, not during
   source writing. Check Ask → next/CPU/Stop separately, and exact error/hint in
   Logs. Stop/cancel while Ask is open must not hang. Never remove source media.
9. Opt into parallel=2, keep Safe mode ON, enable Use all tested GPUs, queue several
   files. Expect at most one session per runtime slot and no more than two global
   jobs. Return parallel=1 after testing. Per-item Hardware override is available
   from the Encoding queue context menu before starting / retrying a job.
10. Choose a second FFmpeg/FFprobe pair via Settings → Apply tool paths + re-scan.
    Confirm inventory/cache changes. A deliberately nonexistent path must show an
    error and block conversion, not silently use bundled tools. Empty paths restore
    bundled/PATH resolution. Reopen the app: the unchanged fingerprint should use
    cached tests; Re-scan must run tests again.
