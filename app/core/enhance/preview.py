"""Async-callable preview service. Uses the final encoder plan's EXACT video filter chain."""
import os
import tempfile
import time
import uuid
from pathlib import Path
from dataclasses import dataclass
from .ai import ai_preprocess
from .planning import is_hdr
from ..pipeline import build_command
from ..process import capture, tool, ProbeSkipped


@dataclass
class PreviewResult:
    before: bytes
    after: bytes
    plan: object
    commands: list
    clip: str = ''
    elapsed: float = 0


def frame_command(plan, timestamp, output, enhanced=True, source=None):
    command = list(plan.command)
    i = command.index('-i')
    prefix = command[:i]
    if not enhanced:
        prefix = [tool('ffmpeg'), '-nostdin', '-v', 'warning', '-y', '-threads', '2', '-filter_threads', '2']
    source = source or command[i+1]
    vf = command[command.index('-vf')+1] if enhanced else 'null'
    if enhanced and (plan.pipeline == 'GPU full' or 'hwupload=' in vf):
        vf += ',hwdownload,format=nv12'
    # Display resize is appended AFTER the exact chain, never substituted for it.
    vf += r',scale=w=min(960\,iw):h=-2:flags=bilinear,format=rgb24'
    return prefix + ['-ss', str(timestamp), '-i', str(source), '-map', '0:v:0', '-an', '-sn', '-dn',
                     '-vf', vf, '-frames:v', '1', '-c:v', 'png', '-threads', '2', '-update', '1', str(output)]


def render_preview(media, options, caps, timestamp=0, cancel=None, clip_output=None):
    if not 0 <= timestamp < max(.001, media.duration):
        raise ValueError('Preview timestamp must be inside the source duration')
    if clip_output and Path(clip_output).resolve() == Path(media.path).resolve():
        raise ValueError('Preview clip cannot replace the source')
    begin = time.monotonic()
    commands = []
    with tempfile.TemporaryDirectory(prefix='edius-enhance-preview-') as folder:
        folder = Path(folder)
        prepared, ai_notes = ai_preprocess(media, options, folder, cancel, start=timestamp,
                                           duration=min(3, media.duration-timestamp))
        after_timestamp = 0 if ai_notes else timestamp
        plan = build_command(prepared, options, folder/'plan.mp4', caps)
        before = frame_command(plan, timestamp, folder/'before.png', False, media.path)
        after = frame_command(plan, after_timestamp, folder/'after.png')
        for command in (before, after):
            commands.append(command)
            capture(command, timeout=30, cancel=cancel)
        if is_hdr(media):
            note = 'HDR Before is an SDR PNG approximation, not an HDR reference display; assess the SDR output in a trusted player'
            plan.enhancement.setdefault('preview_notes', []).append(note)
            plan.encoding_status += ' · ' + note
        if ai_notes:
            plan.enhancement['external_ai'] = ai_notes
            plan.encoding_status += ' · ' + '; '.join(ai_notes[:2])
        clip = ''
        if clip_output:
            destination = Path(clip_output)
            if destination.exists():
                raise ValueError('Preview clip destination already exists; choose a new filename')
            destination.parent.mkdir(parents=True, exist_ok=True)
            part = destination.with_name(destination.stem + '.' + uuid.uuid4().hex[:8] + '.part.mp4')
            try:
                cmd = list(plan.command)
                i = cmd.index('-i')
                cmd[i:i] = ['-ss', str(after_timestamp)]
                if '-t' in cmd:
                    cmd[cmd.index('-t')+1] = str(min(3, media.duration-timestamp))
                else:
                    cmd[-1:-1] = ['-t', str(min(3, media.duration-timestamp))]
                cmd[-1] = str(part)
                commands.append(cmd)
                capture(cmd, timeout=120, cancel=cancel)
                if not part.is_file() or not part.stat().st_size:
                    raise ValueError('No preview clip was produced')
                if cancel is not None and cancel.is_set():
                    raise ProbeSkipped('Preview cancelled')
                if destination.exists():
                    raise ValueError('Preview destination was created during rendering; it was not overwritten')
                os.replace(part, destination)
                clip = str(destination)
            finally:
                part.unlink(missing_ok=True)
        if cancel is not None and cancel.is_set():
            raise ProbeSkipped('Preview cancelled')
        return PreviewResult((folder/'before.png').read_bytes(), (folder/'after.png').read_bytes(),
                             plan, commands, clip, time.monotonic()-begin)
