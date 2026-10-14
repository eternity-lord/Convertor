# AGENT PROMPT — EDIUS Ultimate Render Booster v7 (Full Redesign + MP4 Fixer Overhaul)

You are a senior Windows desktop engineer (Python, Qt, FFmpeg, Intel QSV) and a meticulous UI engineer.
Attached to this message:
1. `mp4_profile_fixer_102502.txt` — the full source of my current app (single-file Tkinter, v6.4).
2. `273692.jpg` — the **visual reference** ("Helios Investments" dashboard). My app's whole UI must be rebuilt in this exact design language.

Read the whole source first. Keep every existing feature working (System Tweaks, HQX Converter, QSV Advanced, MP4 Profile Fixer, Log, overlay monitor, guards, admin elevation). Then do the 4 jobs below. Deliver complete, runnable code (multi-file project is fine), not snippets.

---

## JOB 1 — FIX THE MP4 PROFILE FIXER (two real bugs)

### Bug A: Mixed-camera footage → output has an unstable / double frame rate → TVs refuse the file
Real scenario: in EDIUS I edit footage from TWO cameras recorded at DIFFERENT frame rates (e.g. 25p + 29.97p, or 30p + 50p/60p). In EDIUS I export with ONE project frame rate, so the export "looks" like a single-fps file. But the exported MP4 (EDIUS / Voukoder Pro) often still has inconsistent timing: variable frame rate (VFR), irregular PTS/DTS, mismatched `r_frame_rate` vs `avg_frame_rate` (e.g. container says 50 but average is 25 / 29.97 vs 30), duplicated/dropped-frame cadence from the mixed sources. Most TVs reject or stutter on that. The Fixer must **force ONE clean constant frame rate (CFR) for the whole file**.

Required fix — **"Unify Frame Rate" engine**:
- Before every file run `ffprobe` (streams + format, plus a packet-timestamp scan: `-select_streams v:0 -show_entries packet=pts_time,dts_time -of csv` on the whole file or a fast sampled scan). Compute: `r_frame_rate`, `avg_frame_rate`, `nb_frames/duration`, the histogram of frame-to-frame PTS deltas, and the dominant cadence. Flag the file as **VFR / unstable** if deltas have more than one significant cluster, if avg and r_frame_rate differ by >0.5%, or if PTS jitter exceeds a small tolerance.
- UI card "Frame Rate" with segmented pills: **Auto (recommended) · 23.976 · 24 · 25 · 29.97 · 30 · 50 · 59.94 · 60 · Custom**.
  - **Auto** = pick the dominant/nominal cadence of the file (from the delta histogram, snapped to the nearest standard rational rate: 24000/1001, 24, 25, 30000/1001, 30, 50, 60000/1001, 60). Never "round" 29.97 to 30 unless the user chooses 30.
  - Show detected values as badges before conversion: "Detected: VFR · avg 27.4 fps · container 50 fps → will be forced to 25 CFR".
- Apply the chosen rate as true CFR:
  - GPU path: `vpp_qsv=framerate=<num/den>` (if supported by the probed ffmpeg build), else CPU `fps=<num/den>:round=near` before upload.
  - Always `-fps_mode cfr -r <num/den>` on the output, `-fflags +genpts`, `-avoid_negative_ts make_zero`, `-video_track_timescale` = rate numerator-based (25000 for 25, 30000 for 29.97/30, 60000 for 59.94/60, 24000 for 23.976), and write consistent `avg_frame_rate == r_frame_rate` in the result.
  - Replace the deprecated `-vsync` everywhere.
- **Conversion method option** (when source cadence ≠ target): *Drop/Duplicate frames* (default, fastest, no artifacts) · *Frame blend* (`tmix`/`minterpolate=mi_mode=blend`, smoother, CPU) · *Motion interpolation* (`minterpolate=mi_mode=mci`, slow, warn about artifacts). If the target rate is an integer multiple of the source (25→50) prefer clean duplication.
- Keep audio in sync: audio is re-encoded to 48 kHz AAC with `-af aresample=async=1:first_pts=0`; if the file has a start-time offset between audio and video, fix it. Verify at the end that |audio_duration − video_duration| < 40 ms.
- Interlaced detection (`field_order`): option Keep / Deinterlace (default); deinterlace keeps the frame rate unless "double rate" is chosen.
- Handle each file with its OWN probe results; never apply one fps to a whole folder unless the user picks a fixed rate in the UI (then every file is forced to that exact CFR). If files in the folder differ, show "Mixed frame rates detected across files" and offer "Unify all files to: [rate]".
- **Post-convert verification** (ffprobe on the output): must be CFR (delta histogram has one cluster), `avg_frame_rate == r_frame_rate == chosen rate`, no negative timestamps, one video + one audio stream. Show a "CFR ✓ 25.000 fps" badge in the queue; if it fails, retry once with the software path (`libx264` + `fps` filter) and log it.

### Bug B: "Most TVs don't open the output"
Likely causes: output keeps 10-bit or 4:2:2 from Edius/Voukoder; level too low for resolution×fps (e.g. 1080p50/60 or 4K with Level 4.1 = invalid); `-map 0` copies extra streams (timecode/data/subtitle/attachment) that TVs choke on; `-c:a copy` keeps PCM / 5.1 / 96 kHz / odd audio; no `avc1` tag; huge bitrate/bufsize; open GOP; missing color tags; odd resolution; and files > 4 GB on FAT32 USB sticks.
Required fix — create a **TV Compatibility Engine**:
- `-map 0:v:0 -map 0:a:0?` only (first video + first audio). Add `-sn -dn -map_metadata -1 -map_chapters -1`. Optional checkbox "keep all audio tracks".
- Force `-pix_fmt nv12` on the QSV path / `yuv420p` on the CPU path (8-bit 4:2:0) and Profile ≤ High. Never output high10/high422/high444 in TV mode (grey those options out with a tooltip).
- **Auto level**: compute macroblocks from width×height and MB/s from fps, then pick the lowest legal H.264 level (3.0 … 5.2). Examples: 1080p30 → 4.1, 1080p50/60 → 4.2, 2160p30 → 5.1, 2160p60 → 5.2. If the user picks a level lower than required, auto-raise it and log why.
- Force even width/height (`scale` with `-2`), square pixels (`setsar=1`), and write `-tag:v avc1`.
- Closed GOP, GOP = 2 seconds (`-g round(fps*2)`), `-bf 2`, `-refs 3` for TV preset, `-strict` standard-compliant, no look-ahead extras that raise level/buffer.
- Bitrate guard: TV presets cap maxrate (default 20 Mbps for 1080p, 40 Mbps for 4K) and bufsize ≤ 2× maxrate.
- Color tags: `-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv` for HD (bt601/smpte170m for SD).
- Audio TV-safe: AAC-LC, 48 kHz, stereo (downmix 5.1 with `-ac 2`), 192–256 kbps, `-c:a aac`. If source is already AAC-LC ≤ 2ch 48 kHz you may copy; otherwise re-encode.
- `-movflags +faststart`, MP4 container only.
- **FAT32 guard**: if the estimated output > 3.9 GB show a warning and offer (a) lower bitrate to fit, (b) split into parts by segment, (c) ignore.
- **Post-convert verification**: after each file run ffprobe on the output and check every rule above; show a green "TV-Safe ✓" badge or a red list of violations. If verification fails, automatically retry once with the "Max Compatibility" preset and log it.
- **TV Presets** (dropdown, with one-line descriptions):
  1. Universal TV — Max Compatibility (H.264 High, 8-bit 4:2:0, ≤1080p30 → Main/High 4.1, AAC stereo 48k) ← default
  2. Old TV / USB 720p (Main@4.0 or Baseline@3.1, 1280×720, ≤30 fps, 6–8 Mbps, AAC 128k)
  3. Samsung / LG / Sony (webOS, Tizen, Android TV) — High@4.2, up to 1080p60
  4. 4K TV (H.264 High@5.1, 3840×2160 ≤30 fps; optional HEVC Main@5.1 toggle with a warning "older TVs can't play HEVC")
  5. Phone / Tablet share
  6. Custom (current manual Profile/Level/Bitrate controls stay available)
- Add a "Test with TV simulation" checkbox that runs the verification only (no encode) on already-converted files and reports compatibility.
- New options in the Fixer: resolution cap (keep / 1080p / 720p), fps cap (keep / 30 / 25), audio mode (TV-safe / copy / keep all), output folder (same folder / `_TV` subfolder / custom), filename suffix, overwrite policy, "skip files that are already TV-safe".

---

## JOB 2 — PERFORMANCE (Intel iGPU / QSV): faster and uses the whole machine

I designed this for integrated Intel graphics. Optimize the pipeline; be honest in comments about what is hardware-limited.
- At startup run a **capability probe**: `ffmpeg -hide_banner -encoders`, `-hwaccels`, then a 1-second synthetic test encode with `h264_qsv`, `hevc_qsv`, `av1_qsv`, with and without `-low_power 1`. Cache the result and disable unsupported options in the UI. Detect the iGPU generation if possible (wmic/WMI) and show "QSV ready ✓ / not available".
- **Full-GPU pipeline** when the source is 8-bit 4:2:0 H.264/HEVC:
  `-init_hw_device qsv=hw -hwaccel qsv -hwaccel_output_format qsv -c:v h264_qsv (decoder) -i IN -vf "vpp_qsv=format=nv12[:deinterlace=..][:w=..:h=..]" -c:v h264_qsv ...`
  so decode → scale/convert → encode never leaves GPU memory.
- **Hybrid pipeline** when GPU decode is impossible (10-bit, 4:2:2, ProRes, DNxHD, Cineform, HQX/avi/mov from Edius): CPU decode with all cores (`-threads 0`, `-filter_threads`) → `format=nv12,hwupload=extra_hw_frames=64` → QSV encode.
- If QSV fails at any point → automatic fallback to `libx264` (`-preset medium`, `-threads 0`) with the same TV rules, and log it.
- Tune QSV: `-async_depth` 4–8, `-low_power` auto (use whichever the probe showed faster), `-preset` default **medium/slow** (not `veryslow` — it wastes time on QSV with no visible TV benefit; keep `veryslow` selectable), `-look_ahead` only where supported, `-extbrc`/`-b_strategy` only if probe passed.
- **Parallel jobs**: run N ffmpeg jobs at once (default auto: 2 for iGPU, 1 for CPU fallback; user can set 1–4). Never exceed GPU capacity: if per-job speed drops below 0.8× of single-job speed in the first 20 s, reduce concurrency.
- Process management: launch ffmpeg with `HIGH_PRIORITY_CLASS`, set CPU affinity to all cores, ABOVE_NORMAL I/O priority. Optional temp-to-RAMDISK/NVMe scratch folder, then atomic rename to final name (write `name.part.mp4` → rename on success so TVs / Edius never see half-written files).
- Replace `subprocess.run(capture_output=True)` with `subprocess.Popen(..., stdout=PIPE, stderr=PIPE)` + `-progress pipe:1 -nostats -loglevel warning`, and read both pipes in separate threads (the current code can deadlock when stderr fills up and gives zero progress info).
- Keep the existing system tweaks (power plan, core parking, priority guard, thermal guard) but make them toggles with clear descriptions; the thermal guard must also lower ffmpeg priority / concurrency when CPU > threshold. Do not kill user apps or stop services unless the checkbox is explicitly on, and always restore on exit (also on crash — use `atexit` + a restore-on-next-start marker file).
- Fix known issues in the old code: `-vsync` (deprecated), `tk`/`ttk` imported only under `__main__`, HQX scan only checks top-level and `processed` is name-only (use path + size + mtime), `_is_stable` blocks the scan thread, Fixer only globs `*.mp4` non-recursively and skips only `_fixed` names.

---

## JOB 3 — COMPLETE UI REDESIGN to match `273692.jpg` EXACTLY

**Tech:** rebuild the UI in **PySide6 (Qt Widgets)**. Tkinter cannot do this look. Use QSS + custom-painted widgets (`QPainter`, `QLinearGradient`, `QRadialGradient`, `QPainterPath`, `QGraphicsDropShadowEffect`). Multi-file project (`app/ui/…`, `app/core/…`, `app/theme.py`). Keep UAC elevation. Package with PyInstaller (give the spec/command). Use a frameless rounded main window with a custom title bar (minimize / maximize / close styled to match), drag-to-move, resizable, min size 1100×720, high-DPI aware.

**Look at the image and reproduce it pixel-faithfully — buttons, pills, cards, spacing, glow, radius, typography.** Reference values sampled from the image (use these as theme tokens in `theme.py`):

| Token | Value |
|---|---|
| Page background (outside window) | `#050505` → `#130d0f` soft vignette |
| App window shell | gradient from `#5d535b` (top-left, frosted mauve-grey) to `#0f0d10` (center/bottom) with a 1px `rgba(255,255,255,0.08)` border, radius ≈ 28 px |
| Sidebar panel | `#0a0a0c`, radius ≈ 22 px |
| Cards | `#0a0a0c`–`#171113` base with a soft mauve radial glow in one corner (`#423441` → transparent), 1px border `rgba(255,255,255,0.07)`, radius ≈ 22 px |
| Accent (mauve / dusty pink) | `#71586e` (active pill fill), `#b19fad` (text on pill), `#cba8c6` (glow button), `#ffd3f4` (chart line / highlight) |
| Active pill gradient | `#8a6a85` → `#5f4a5c`, subtle top highlight |
| Text primary / secondary / muted | `#ffffff` / `#989697` / `#585359` |
| Positive (green %) | `#4ade80`-like soft green; negative: soft red-pink `#f87171` |
| Grid lines | `#181216` |

Typography: geometric sans like **Poppins / Plus Jakarta Sans / Urbanist** (bundle the font files) for Latin, **Vazirmatn** for Persian, with full RTL support for Persian strings. Big numbers semibold ~28–32 px, titles 15–16 px medium, captions 10–11 px muted.

**Components to reproduce (study the image for each):**
- **Sidebar**: black rounded panel, logo mark + app name at the top ("EDIUS Booster" with the logo style like "H Helios Investments"), nav items with line icons; the ACTIVE item is a wide pill with the mauve gradient + faint glow; Settings and Support pinned at the bottom.
- **Header**: "Welcome, **Mamad**" (name in mauve accent) + small grey subtitle; top-right: round icon buttons (bell, gear — black circles with thin border) and an avatar chip with name + subtitle.
- **Top tabs**: black pill buttons with thin border; active one filled mauve (like Market / **Wallet** / Tools). Use them as in-page tabs.
- **Search pill** with mic-style icon (use it as "Search files in queue / filter logs").
- **Cards**, **segmented pills** (Most Viewed / Gain / Lose style), **dropdown pills** ("6M ⌄"), **range pills** (1D 1W 1M 6M 1Y style, active = mauve), **mini tiles** (2×2 grid like "My Portfolio"), **glowing CTA button** (centered pill with radial halo, like "Explore AI Insights"), **chart card** with area chart (mauve gradient fill fading to transparent, thin pale-pink line, glowing point marker, dashed vertical guide, tooltip card, faint horizontal grid, axis labels).
- Every control (checkbox → pill toggle switch, combobox → pill dropdown, entry → dark rounded field with 1px border and focus glow, scrollbar → thin mauve, tooltip, context menu, message box, file dialog launcher) must follow this same language. No default Windows-looking widget may remain.
- Hover / pressed / disabled states and 120–180 ms eased animations (hover glow, tab switch fade, progress bar easing).

**Page mapping (reuse the dashboard layout of the image):**
1. **Dashboard** (home) — "Total Holding" card → *Total converted today (GB / files)*; "Decisions Powered by Data" card → *TV-Compatibility Check* with the glowing CTA "Analyze & Fix MP4s"; Watchlist card → *Live queue* (file, status chip, % in green); "My Portfolio" 2×2 tiles → *Avg speed (×), GPU load, CPU load, Space saved*; bottom "Portfolio Performance" area chart → *Encoding speed / CPU / GPU over time* with range pills.
2. **MP4 Profile Fixer** — folder picker card, TV preset selector (segmented pills), Profile/Level/Rate-control/Bitrate cards, audio card, output card, compatibility badges, big glowing "Start Fix" button, Stop button.
3. **HQX Converter** (watch folder), 4. **QSV Advanced** (searchable parameter grid as cards), 5. **System Tweaks** (toggle cards), 6. **Logs** (see below), 7. **Settings**, 8. **Support/About** (credit: Mohammad Irani, version, links).
- Top tabs and filters inside each page follow the same pill style.

**Logs page redesign (currently just green text lines):**
- Card list with timestamp, colored level chip (INFO / WARN / ERROR / DONE), humanized message (not raw ffmpeg spam), file name chip; expandable "Details" showing the full ffmpeg command and stderr in a monospaced block with a Copy button.
- Filters: segmented pills (All / Info / Warn / Error), search pill, per-job filter, Export (.txt/.json), Clear, auto-scroll toggle. Persian text must render correctly.

---

## JOB 4 — ENCODING PROGRESS WINDOW (exactly like Adobe Media Encoder, in my design)

When ANY conversion starts (Fixer, HQX, batch), automatically open a separate **Encoding window** (same frameless glass design; reopenable from the sidebar/tray; "always on top" toggle; minimize to tray; optional auto-close when finished). It must show, updating live (≥ 2 updates/s, UI thread never blocked — use Qt signals):

**Top summary bar:** files found in folder · waiting · encoding · done · failed · skipped · total queue size (GB) · overall progress % · overall elapsed · **overall time remaining** · estimated finish clock time.

**Queue list (like AME's queue panel):** one row per file: thumbnail, filename, source info (res · fps · codec · size), preset/profile chip, output path, status chip (Waiting / Analyzing / Encoding / Paused / Done / Failed / Skipped / TV-Safe ✓), per-file progress bar with %. Drag to reorder waiting items, right-click: Remove, Move to top, Retry, Open folder, Show ffmpeg command. Waiting files are clearly marked as "waiting".

**Current-encode panel (like AME's "Encoding" pane):** live preview frame (grab a frame of the source at the current timestamp every ~3 s with a cheap `-ss` seek; never slow the encode), big progress bar, `Encoding frame 1,234 of 18,000`, **elapsed**, **remaining**, **speed (×realtime)**, **fps**, **bitrate**, **output size so far + projected final size**, source vs output summary (res, fps, profile@level, pix_fmt, audio), pipeline badge (GPU full / Hybrid / CPU fallback), and a small live sparkline of speed using the same area-chart style as the reference image.

**Controls (glass pill buttons):** Pause / Resume (`psutil.Process.suspend()/resume()` on the ffmpeg process tree), Stop current, Stop all, Skip file, Open output folder, "Shut down / Sleep PC when finished" option, play a soft sound on finish + Windows toast notification.

**Data source:** parse `-progress pipe:1` (`frame`, `fps`, `out_time_us`, `total_size`, `bitrate`, `speed`, `progress`). Get total duration/frames from ffprobe BEFORE the encode. ETA = remaining_media_time / smoothed_speed (EMA), overall ETA = sum of remaining durations of the queue / smoothed throughput. Handle parallel jobs (several current-encode cards stacked or tabbed).

---

## ENGINEERING REQUIREMENTS
- Structure: `main.py`, `app/core/{ffprobe.py, pipeline.py, tv_rules.py, queue.py, worker.py, system_tweaks.py, capabilities.py}`, `app/ui/{theme.py, widgets/*, pages/*, encoding_window.py, log_view.py}`, `assets/{fonts,icons}`. Pure functions for the TV rules and level calculation, with unit tests (`pytest`) covering level selection, fps/VFR handling, and command building.
- `requirements.txt`, run instructions, PyInstaller build command, and where to put `ffmpeg.exe`/`ffprobe.exe` (auto-detect next to the exe, then PATH; show a friendly error card if missing).
- Never freeze the UI: all ffprobe/ffmpeg work in worker threads/`QProcess`; communicate by signals.
- Persist settings in `%APPDATA%\EdiusBooster\settings.json`, including last folders, presets, window geometry.
- Robust error handling: per-file failures never stop the queue; every failure shows the human reason + expandable raw ffmpeg stderr.
- Windows-safe paths (Unicode / Persian file names, spaces), long paths.
- Code comments in English; UI strings in English with a language switch (EN / فارسی) wired through a single `tr()` function; Persian strings I already have in the source must be kept.

## ACCEPTANCE CHECKLIST (verify each before you say you're done)
1. An EDIUS export that mixes two-camera footage (25p + 29.97p, or 30p + 50p) with unstable/VFR timing comes out as a single true CFR file at the chosen rate (verified: avg_frame_rate == r_frame_rate, one PTS-delta cluster), no stutter, no A/V drift, and it plays on a TV.
2. 10-bit / 4:2:2 Edius export → output is 8-bit 4:2:0, correct auto level, `avc1` tag, AAC stereo 48 kHz, one video + one audio stream, `+faststart`; verification badge shows TV-Safe ✓.
3. 1080p50 source never gets Level 4.1; 4K30 gets 5.1.
4. QSV unavailable → automatic libx264 fallback works.
5. Progress window opens by itself on start, shows found / waiting / done, elapsed, remaining, frame x of y, speed, size, and Pause/Stop work.
6. Every screen visually matches `273692.jpg` (colors, radii, pills, glow, active states, chart style). No default Tk/Windows widget visible.
7. Log page is card-based with filters and expandable ffmpeg details.
8. All old features still work.

Start by: (1) summarizing in 10 lines what you found in my code, (2) listing any assumptions, (3) then delivering the full project. Do not ask me questions unless something blocks you completely; make a sensible decision and note it.
