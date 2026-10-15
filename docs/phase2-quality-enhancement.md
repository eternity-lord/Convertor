# Round 3 — Phase 2: Quality Enhancement

Scope: reusable `app/core/enhance/` planning, real runtime probes, worker integration,
and the shared Fixer/HQX quality card and split preview. The Universal Converter
page (Phase 3), full localization and help/presets work (Phase 4) are **not** included.

## Controls and promises

**Off · Low · Balanced ★ · Excellent · Custom**, default **Off on both pages**.
Enhancement can make a picture look cleaner/sharper or adjust color; it cannot
recover real detail that was never recorded. Sharpening, denoising, interpolation
and external AI can all introduce artifacts. Compare before/after and keep originals.

- Off preserves the previous **SDR command**, without added filters or headroom changes.
  HDR→SDR cannot safely be achieved by retagging; HDR with Off blocks with an explanation.
- Low: light denoise and luma-only sharpening; optional tested same-device QSV
  denoise/detail or CUDA bilateral recipe. GPU use is never inferred from filter names.
- Balanced: conservative denoise, deband, sharpening and color adjustment; prefers
  proven 10-bit CPU processing with error-diffusion quantization to TV 8-bit 4:2:0.
- Excellent: more encode headroom/slower supported preset and somewhat stronger
  effects. Safe mode, Haswell, or insufficient measured performance avoid `nlmeans`.
- Custom: bounded per-effect sliders, denoiser, processing depth, source-range override,
  color conversion choice and scaler. Arbitrary strengths use CPU filters rather than
  pretending an untested GPU combination is supported.
- Audio: opt-in one-pass `loudnorm` (`I=-16:TP=-1.5:LRA=11`), and optional light
  `highpass=f=70`, `lowpass=f=16000`, `afftdn=nf=-25`. Timestamp repair, async
  resampling, padding and final AAC-LC stereo 48 kHz / 192 kbps remain intact.
  One-pass normalization is a target, **not** measured two-pass R128 compliance.

Strength limits are deliberately gentle; they are **not** clipping detection,
automatic recovery of crushed blacks, halo detection, or restoration guarantees.

### Bitrate and Excellent

Enhancement raises the requested bitrate budget / reduces CQ and selects only a
supported speed preset. **Existing TV and FAT32 caps remain in force in these TV
workflows**. No Universal Converter export policy is implemented here.

Excellent compares the source **video-stream** bitrate (not container bitrate)
with the cap. An impossible floor blocks with an explicit reason; it never silently
reduces the requested source budget. CQ/ICQ/CRF/CQP switch explicitly to VBR for
this policy. **VBR does not guarantee an actual bitrate floor**, especially for
simple/compressible content. Final measured bitrate below source, or an unknown
source/output rate, produces **Warning**, retains the encoded output for inspection,
and **prohibits source deletion**. It must not be described as successful floor compliance.

### Color, depth and dimensions

- zscale performs actual matrix / primaries / transfer / full→limited conversion
  where source metadata differs from the SD/HD TV target. Frame tags agree with
  encoder/container tags. Incomplete SDR tags are reported as an inference, with a
  Custom source-range override available; bad source tags cannot be magically detected.
- HDR PQ/HLG uses a proven float RGB linear-light `zscale → tonemap(mobius) → zscale`
  graph, 100-nit reference, conversion to SDR, and removal of mastering-display,
  content-light and HDR10+ frame side data. It is not a creative mastering match.
- High-depth input requires a proven dithering path. Tested chroma normalization
  sets left siting; the output location is not claimed when conversion was not applied.
- TV output remains legal 8-bit 4:2:0, limited range, and existing profile/level/CFR
  validation remains mandatory. Resolution/FPS changes are still controlled by the
  existing TV preset/caps or explicit user selections.
- **Upscale to… Keep / 1080p / 4K** is explicit aspect-preserving interpolation,
  not AI. It does not silently change FPS, exceed the selected TV/resolution cap,
  or guess unverified source geometry. Interlaced enhancement requires explicitly
  enabled deinterlacing rather than silently blending fields.

## Capability evidence and execution

Filter recipes run for one synthetic second with **10 seconds maximum per probe**.
Missing/failed effects are disabled with reasons or explicitly omitted in the plan;
unsafe color/HDR/depth substitution blocks. Same-device QSV/CUDA recipes are
independently tested, and the exact input/filter/encoder job is preflighted before
full encoding, including CPU enhancement jobs. Haswell restrictions from Phase 1
remain: no `low_power`, HEVC/AV1/10-bit GPU claims; CPU processing may be 10-bit
before dithering and NV12 upload to the 8-bit H.264 QSV encoder.

When CPU-only effects or precision/color work are needed, the UI/logs say **CPU
filters + hardware encode** rather than “GPU full.” OpenCL, libplacebo, CAS and
other listed-only alternatives stay unproven/disabled unless a complete recipe is
actually supported. GPU scaling does not falsely claim Lanczos quality.

Speed is a **rough measured-filter / baseline-hardware pixel-cost estimate**, not
a fabricated full-job benchmark. Disk, seeking, changed encoder speed presets,
unbenchmarked CPU encode and external AI are excluded. Unknown measurements stay
unknown; projected speed below 0.5× has a warning. Preview wall time is shown separately.

Every final filter and its parameters, estimate caveats, fallback notes, exact command,
preflight result and output probe are retained in Encoding/Logs details. Enhancement
bypasses “Skip already TV-safe” (otherwise requesting an effect would do nothing).

## Before / after preview

1. Finish/cancel the queue and hardware scan. Select settings and a local source in
   the quality card, then choose a timestamp in seconds.
2. **Preview before / after** runs in a background thread. Compare with the split
   slider (keyboard arrow keys work), then inspect **Preview details / exact filter chain**.
3. Optional **Render 3-second preview clip…** runs the final encoder command with
   the same enhancement chain. Choose a new MP4 name, never the source.
4. Cancel is available. A shared lock prevents parallel preview/queue/hardware-scan
   GPU-session conflicts; language rebuild and close cancel old preview workers.

Seeking is input-side `-ss`, not whole-film decoding. PNG output adds only a
960-pixel display resize/RGB conversion after the exact final video filter graph
(and a necessary hardware download for the image sink). Temporal effects start at
the seek point; a one-frame comparison cannot represent their entire-video history.
For HDR sources the untonemapped Before PNG is explicitly labeled an SDR approximation,
not an HDR reference display. The optional encoded clip is more representative, but neither is a full-film quality
or sustained-throughput benchmark. Previews never replace the source.

## Advanced opt-in external AI executable

No model/executable is bundled, downloaded, certified, or started implicitly.
User must choose a trusted compatible executable, enable the hook, and explicitly
select an upscale target. Supported command contract:

```text
YOUR_TOOL -i INPUT_PNG_DIRECTORY -o OUTPUT_PNG_DIRECTORY -s 2|4 -f png
```

The tool must preserve every numbered `%08d.png` filename, count, and exact 2×/4×
raster dimensions. A Real-ESRGAN/Waifu2x-ncnn-compatible CLI is intended, but different
CLI versions/model defaults may not match this contract. The user supplies its model
installation; compatibility and visual quality must be PC-tested. External AI may
invent detail and can be extremely slow.

For safety this hook accepts only verified **progressive CFR 8-bit SDR**, explicit
upscaling, at most 200,000 frames, and enough estimated scratch disk plus 0.5 GiB.
HDR/interlaced/high-depth/VFR/unverified inputs are rejected. Frames and a lossless
FFV1 RGB intermediate stay in temporary storage; source frame rate, sample aspect,
color information and audio are carried forward into the final TV pipeline. Original
video bitrate remains the budget reference, not the huge scratch-stream rate.
Cancellation/errors remove scratch; final verification and original-file protections
still apply. The automated executable test double is interpolation, **not an AI model**.

## Validation and remaining PC acceptance

Development machine, FFmpeg 7.0.2 / FFprobe 5.2:

- Full local suite: **193 passed**, two native-Qt test modules skipped because system
  GUI libraries are absent. Native Qt acceptance is also in GitHub Actions.
- Real Off/Low/Balanced/Excellent/Custom CPU encode, rational CFR, TV verification,
  Unicode paths, audio cleanup + normalization, and no accidental geometry/FPS change.
- Real synthetic tagged 10-bit PQ + mastering/content-light frame metadata → SDR,
  output depth/tags and removal of HDR frame side data verified.
- Real SDR full→limited / BT.709→SD color graph and chroma siting.
- Exact-chain before/after PNG and 3-second MP4, cancellation/source guards.
- Real enhanced worker bypasses skip-safe and records exact-job preflight; Excellent
  below/unknown bitrate tests prove Warning and source retention even with delete enabled.
- External-hook argument/frame validation and successful executable test double;
  pure tests cover settings, source/cap conflicts, full/hybrid mocked Intel paths,
  weak-CPU adaptation, and rejection of listing-only filter claims.

These are software/synthetic tests, **not physical Windows GPU or TV playback proof**.
Follow the checklist below before treating Phase 2 as accepted on your PC.

### Exact Windows PC checklist

1. On the session branch, install dev requirements and run:
   ```powershell
   .\.venv\Scripts\python -m pip install -r requirements-dev.txt
   .\.venv\Scripts\python -m pytest -q
   .\.venv\Scripts\python main.py
   ```
2. Leave **Safe ON, 1 job, NORMAL, Delete source OFF**. In Settings set the correct
   FFmpeg/FFprobe, then Hardware → force Re-scan. Save the matrix/probe details.
   Unproved controls must be grey with a reason, not advertised as acceleration.
3. Both Fixer/HQX should initially show Off. On SDR, compare the Off command to the
   previous workflow: no added enhancement filters/headroom. Confirm the original
   TV-safe/CFR, fixed-rate folder unify and HQX watcher flows still work.
4. With one noisy 720p/1080p clip, compare Low and Balanced at a scene with faces,
   fine texture, dark gradients and bright edges. Move the split and render the
   three-second clip. Check for plastic texture, halos, crushed shadows and banding.
   Cancel one preview and change language during another: UI stays responsive;
   no leftover FFmpeg, queue starts only after preview releases its lock.
5. Encode Balanced on HD4600/Haswell: logs should show real dxva2 (or next working
   backend), H.264 8-bit QSV if proved, CPU enhancement where required, NV12 upload16,
   no `low_power` and no fake HEVC/AV1/10-bit GPU support. Compare CPU fallback too.
   On NVIDIA/other Intel hardware, trust only recipes the local scan actually passed.
6. Check 23.976/25/29.97/50/59.94 sources and explicit fixed FPS. Verify output remains
   the selected exact rational, same resolution unless explicitly scaled/capped, legal
   profile/level, yuv420p, limited range, faststart, AAC-LC stereo 48 kHz and A/V drift
   below 40 ms. Inspect Output Probe and play on the target TV.
7. Use true 10-bit HDR PQ/HLG and known full/limited SDR/601/709 sources. Compare actual
   colors, clipping and gradients, not just tags. Missing zscale/tonemap must block HDR
   with an explanation, not produce washed-out retagged SDR.
8. Excellent: try a source video bitrate above the TV cap (must block), and a highly
   compressible source with budget inside cap (may return measured-floor Warning;
   output and source must both remain). Check an unknown source bitrate too.
9. Try explicit 720→1080 and 1080→4K under a compatible preset, then an incompatible
   cap (must explain/block). Keep should never initiate upscaling.
10. Optionally test a **trusted** separately installed ncnn tool on a few seconds of
    progressive CFR 8-bit SDR only. Watch disk use, cancel once, verify frame count,
    source cadence/audio/aspect and original retention. Do not mistake the automated
    interpolation stub for an AI visual-quality test.

Report failing source metadata, selected settings, hardware matrix, exact command,
stderr tail, output probe and a short before/after sample. Phase 3 stays deferred.
