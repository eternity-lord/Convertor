# EDIUS Ultimate Render Booster v7

A Windows-first **PySide6 desktop application** for repairing EDIUS/Voukoder MP4 exports, watching HQX folders, and producing verified constant-frame-rate, TV-compatible files. Original application / credit: **Mohammad Irani**.

![Actual Qt dashboard](docs/dashboard.png)

The original `mp4_profile_fixer_102502.txt`, design reference JPEG and implementation prompt are preserved unchanged. The v7 application does **not** import or execute the old Tkinter app.

## Run on Windows

Requirements: Windows 10/11 x64, Python **3.10–3.13**, FFmpeg **6+** and ffprobe from the **same build**. Intel graphics drivers are needed only for QSV; CPU conversion works without Intel hardware. Use a build containing `libx264`, `aac`, `fps`, `scale`, and optionally QSV encoders/filters.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

Put `ffmpeg.exe` and `ffprobe.exe` next to `main.py` (or next to the packaged executable), or put them on `PATH`. Missing tools appear in the application status and Logs, not as a startup crash. No video binaries are committed to Git.

Launch normally. System tuning is **not** applied on startup. Use **Settings → Restart as administrator** only when opting into Windows tweaks. UAC cancellation leaves the application running.

### First conversion

1. Open **MP4 Profile Fixer**, choose a folder, select a TV preset.
2. **Analyze files** probes each MP4 recursively and shows detected cadence. Mixed folders are explicitly reported. **Auto** uses each file's own cadence; choose a fixed rate to force the entire batch to that exact rational rate.
3. Select output, audio, overwrite and FAT32 policies. Default output: `_TV/name_TV.mp4`; originals are preserved.
4. **Start Fix** analyzes waiting files before dispatch so queue ETA has duration information. The Encoding window opens automatically. Close it to hide it; reopen it from the sidebar or tray.
5. Only a **verified** AVC output receives **TV-Safe ✓**. Inspect Logs → Details for commands / failures. A failed GPU encode or output verification receives **one software retry**.

Universal TV caps Auto to ≤1080p30; Samsung/LG/Sony permits 1080p60; 4K permits 2160p30. **Explicit Frame Rate selection wins over preset FPS caps**. A separate explicit FPS cap is still honored. 29.97 means `30000/1001`, not 30. Presets never promise playback on every TV model.

**HQX Converter** recursively watches AVI/MOV/MKV. Files must have unchanged path, size and modification time for six seconds before queuing. Watching does not block while files are being written. H.264 is the default. HEVC/AV1 are selectable only when the corresponding QSV encoder is available; those outputs are labeled **verified**, not universally TV-safe. The 4K preset also offers a hardware-gated HEVC option with an older-TV warning. Software fallback deliberately returns to AVC.

## What is implemented

- **CFR engine:** three bounded packet samples before conversion; histogram-based cadence; exact standard rational rates; drop/duplicate, blend and motion interpolation; progressive / keep-interlaced / bob options. Output uses `fps` or capability-gated `vpp_qsv`, `-fps_mode cfr`, regenerated PTS, rate-based **video and movie timescales**, and negative-timestamp avoidance. Movie timescale prevents sub-millisecond negative DTS at 29.97 from MP4 edit-list rounding.
- **Post-encode verification:** streams/format plus a **whole-file streaming packet scan** with a 64-packet reorder heap. Checks exact average/nominal/selected rate, timestamp cadence, negative PTS/DTS, profile, pixel format, level floor, dimensions/SAR, color tags, audio format/duration, extra streams, average bitrate ceiling and MP4 faststart. Timestamp data is not accumulated in RAM.
- **TV policy:** AVC 8-bit 4:2:0, `avc1`, safe profile/auto-raised level, even dimensions, square pixels, closed two-second GOP command, 2 B-frames, 3 references, bounded VBV, limited BT.709/SD SMPTE170M tags, AAC-LC stereo 48 kHz, metadata/chapters removed. Missing audio gets silence. Copy / all-audio are explicit options and can trigger a compatibility retry.
- **Audio:** offset-preserving timestamp normalization, asynchronous resampling, padding and an integral-frame output duration. Audio/video **duration** difference must be <40 ms. This is a timeline check, not lip-sync/content analysis.
- **QSV:** binary-keyed seven-day capability cache, encoder/hwaccel discovery, sequential one-second AVC/HEVC/AV1 tests with low-power off/on, real AVC GPU decode/VPP test, individual optional-flag tests. CPU decode → NV12 upload → QSV for unsupported sources. Fully hardware-resident scaling/rate conversion only when the real probe passed. Fallback is libx264 medium.
- **Queue/progress:** 0.5-second refresh, found/waiting/active/done/failed/skipped counts, source size, per-job media information and progress, frame counts, elapsed, EMA speed/ETA, overall throughput ETA, predicted size, background source preview, source/output summary, pipeline badge, real-data sparklines, waiting-row drag/reorder, context actions, pause/resume process trees, cancellation, retry and output-folder launchers.
- **Concurrency:** Auto 2 on available QSV / 1 on CPU, manual 1–4, low-memory and thermal dispatch limits. A measured serial baseline can reduce future dispatch when parallel per-job throughput falls below 80%. Active jobs are allowed to drain; jobs are not killed to enforce a lower concurrency limit.
- **Atomic outputs:** unique `.part.mp4` files; optional scratch-to-destination staging; rename only after verification. Custom-folder name collisions are reserved/renamed before parallel dispatch.
- **FAT32:** size estimate warning, bitrate-to-fit, or closed-GOP stream-copy segments. Split parts are size-checked and reverified; the verified master is retained. Estimates are not exact file-size guarantees. Use exFAT/NTFS for long/high-bitrate programs.
- **Windows tuning:** explicit toggles for temporary cloned power plan, core parking, process priority, thermal guard, timer resolution, visual effects, selected services, reversible background-app suspension, BorisFX GPU integration, optional ImDisk RAM disk, optional Defender folder exclusion. Write-ahead journal and restoration on exit / next startup. Service startup modes are never modified, and existing Defender exclusions are preserved. **No arbitrary working-set trimming and no app killing.**
- **UI:** custom-painted rounded Qt shell, mauve gradients, glass cards, pills, custom animated toggle switches, chart, dashboard, Fixer, HQX, advanced QSV, Tweaks, Logs, Settings, Support, and Encoding window. Font files and OFL licenses are bundled. Dark non-native Qt file dialogs are used.
- **Logs:** bounded structured records, level/search/file-name search filters, expandable command/stderr details, clipboard, JSON/text export, clear, auto-scroll. Technical details are capped rather than accumulated for hours.
- **Settings:** `%APPDATA%\EdiusBooster\settings.json`; folders, conversion options, language, scratch, parallelism, encoder preset, advanced flags and main geometry. EN/فارسی through `app/i18n.py`; Persian navigation/core controls and RTL. Original Persian literals are retained in `assets/legacy_fa.json`, including legacy wording no longer accurate for v7; those obsolete claims are not shown as current behavior.

## Safety and practical limits

- **TV simulation is a rule checker, not a physical TV emulator.** AVC GOP closure / reference limits / VBV are set in the command; the verifier does not independently parse every SPS/slice/HRD bitstream rule. Packet cadence is checked across the complete output, but repeated visual frames and aesthetic motion judder cannot be disproved by timestamp analysis.
- New color tags do **not** perform HDR→SDR tone mapping. HDR material needs a proper graded/tone-mapped SDR source; relabeling HDR as BT.709 is not a color-managed HDR workflow.
- HEVC / AV1 hardware paths, actual Intel adapter behavior, Windows UAC/power/service restoration, thermal sensors, notifications and ImDisk need testing on the target PC. GPU utilization is **N/A**, not an invented percentage. Adapter name is detected where Windows exposes it; exact architectural generation is not inferred from branding.
- Notification uses Qt's Windows tray notification API (OS presentation varies); completion uses the system beep, not a bundled custom sound. The Encoding window is reopenable from tray/sidebar; closing hides it. Minimization uses the normal taskbar behavior.
- Progress shows **one selected active job** plus all concurrent jobs in the table, not a separate preview pane for every active job. Queue rows use source metadata rather than per-file thumbnails. UI is a close interpretation of the supplied design, not a claim of pixel-identical screenshots at every DPI.
- Navigation/core controls are translated; some technical descriptions/diagnostics remain English. The log file filter is the combined text search (including file name), not a separate per-job dropdown.
- Probe metadata has a bounded capture size and timeouts; pre-analysis samples at most 1,536 video packets total. Full verification keeps histograms (up to 4096 bins) and a 64-entry reorder heap. A malicious/malformed stream or insufficient metadata is rejected rather than guessed safe.
- Queue capped at **5000 jobs**, log model **400 records**, visible log cards **100**, chart history **1800 points**, FFmpeg stderr tails **200 bounded lines**. FFmpeg surfaces still consume real memory: 4K motion interpolation or several GPU sessions can be expensive. On low-memory PCs use one job, drop/duplicate, no RAM disk, and a 1080p cap. No implementation can guarantee immunity to driver/OS out-of-memory errors.
- Crash recovery runs when the app next starts with the necessary permissions. It cannot restore settings while the PC is powered off, recover destroyed unsaved work, or guarantee success if Windows denies changes. Pending recovery remains on disk for another attempt.
- Choosing **Sleep / Shut down** in Encoding is explicit opt-in. It is ignored if files failed or folder watch is active. Shutdown has a 60-second grace period; `shutdown /a` cancels it. Originals are deleted only if the explicit deletion toggle is on and verification succeeded.

## Tests

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

On Linux/headless: `QT_QPA_PLATFORM=offscreen python -m pytest -q`. Native Qt EGL/GL, xkbcommon and D-Bus libraries must be installed for the GUI smoke tests. Integration tests require FFmpeg/ffprobe on PATH; missing-tool tests are skipped and explicitly reported. CI runs on Ubuntu 24.04.

See [validation and acceptance status](docs/VALIDATION.md) for exactly what was exercised and what still requires real hardware.

## Package on Windows

Build on Windows, not Linux. Install dev requirements and put the desired FFmpeg executables in the project root if they should be bundled:

```powershell
.\.venv\Scripts\python -m PyInstaller --clean --noconfirm EdiusBooster.spec
# Run dist\EdiusBooster\EdiusBooster.exe
```

The one-folder spec includes fonts/icons and any local FFmpeg/ffprobe executables. Without bundled executables users can put them beside the EXE or on PATH. The EXE does not require admin by default. Respect your FFmpeg build's GPL/LGPL redistribution obligations and Intel driver licensing. Poppins and Vazirmatn use their included SIL Open Font Licenses.

## Layout

```text
main.py
app/i18n.py
app/core/{process,settings,ffprobe,tv_rules,pipeline,capabilities,queue,worker,system_tweaks}.py
app/ui/{theme,dialogs,main_window,encoding_window,log_view}.py
app/ui/widgets/common.py
app/ui/pages/{dashboard,fixer,tools}.py
assets/{fonts,icons,legacy_fa.json}
tests/                         # pure rules, commands, subprocess safety, real encodes, Qt smoke
EdiusBooster.spec
```
