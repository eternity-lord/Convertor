"""Real CPU/FFmpeg quality, HDR, audio, preview, external-hook and TV regressions."""
import json
import os
import shutil
import threading
from dataclasses import replace
from pathlib import Path
import pytest
from app.core.enhance.probe import probe_filters
from app.core.enhance.preview import render_preview
from app.core.enhance.ai import ai_preprocess
from app.core.hardware.detect import detect_ffmpeg
from app.core.process import tool, capture, ProbeSkipped
from app.core.ffprobe import probe, rate
from app.core.tv_rules import Options, verify, faststart_ok
from app.core.pipeline import build_command
from app.core.queue import Job
from app.core.worker import EncodeWorker

pytestmark=pytest.mark.skipif(not(shutil.which('ffmpeg') and shutil.which('ffprobe')),reason='FFmpeg/FFprobe needed')


@pytest.fixture(scope='module')
def enhancement_proof():
    return probe_filters(tool('ffmpeg'),detect_ffmpeg(tool('ffmpeg'),tool('ffprobe')))


def source(path, hdr=False, duration=1, fps='30000/1001'):
    video='libx265' if hdr else 'libx264'
    args=['ffmpeg','-v','error','-nostdin','-y','-threads','2','-filter_threads','2','-f','lavfi','-i',
          f'testsrc2=size=320x180:rate={fps}:duration={duration}','-f','lavfi','-i',
          f'sine=frequency=440:sample_rate=48000:duration={duration}','-c:v',video,'-preset','ultrafast','-threads','2']
    if hdr:
        args+=['-x265-params','pools=2:frame-threads=2:hdr10=1:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400','-pix_fmt','yuv420p10le','-color_primaries','bt2020',
               '-color_trc','smpte2084','-colorspace','bt2020nc','-color_range','tv']
    else:args+=['-pix_fmt','yuv420p']
    capture(args+['-c:a','aac',str(path)],timeout=30)
    return probe(path)


@pytest.mark.parametrize('level',['Off','Low','Balanced','Excellent','Custom'])
def test_actual_enhancement_keeps_tv_safety_and_rational_cfr(tmp_path,enhancement_proof,level):
    media=source(tmp_path/'ورودی با فاصله.mp4')
    output=tmp_path/'result.mp4'
    settings={'level':level,'loudnorm':True,'audio_cleanup':True}
    plan=build_command(media,Options(enhance=settings),output,{'enhancement':enhancement_proof},software=True)
    capture(plan.command,timeout=30)
    result=probe(output)
    assert not verify(result,plan.target),verify(result,plan.target)
    assert result.fps==rate('30000/1001')
    assert (result.video['width'],result.video['height'])==(320,180)
    assert faststart_ok(output) and media.path.exists()
    if level!='Off':
        assert plan.enhancement['estimated_speed']>0
        assert 'hqdn3d' in plan.enhancement['video_chain']


def test_actual_hdr_to_sdr_has_real_float_conversion_and_correct_tags(tmp_path,enhancement_proof):
    if not enhancement_proof['hdr_tonemap']['available']:
        pytest.skip('Build lacks proved HDR tonemap: '+enhancement_proof['hdr_tonemap']['reason'])
    media=source(tmp_path/'HDR10.mp4',hdr=True)
    assert media.video['pix_fmt']=='yuv420p10le'
    def frame_sides(path):
        data=json.loads(capture([tool('ffprobe'),'-v','error','-read_intervals','0%+#3','-select_streams','v:0','-show_frames','-of','json',str(path)]))
        return [s for frame in data.get('frames',[]) for s in frame.get('side_data_list',[])]
    assert any(s.get('side_data_type')=='Mastering display metadata' for s in frame_sides(media.path))
    opts=Options(enhance={'level':'Balanced'})
    plan=build_command(media,opts,tmp_path/'SDR.mp4',{'enhancement':enhancement_proof},software=True)
    capture(plan.command,timeout=30)
    result=probe(tmp_path/'SDR.mp4')
    assert not verify(result,plan.target),verify(result,plan.target)
    assert result.video['color_transfer']=='smpte170m'
    assert result.video['pix_fmt']=='yuv420p'
    assert not any(s.get('side_data_type') in ('Mastering display metadata','Content light level metadata') for s in frame_sides(result.path))


def test_preview_exact_chain_frames_clip_and_cancellation(tmp_path,enhancement_proof):
    media=source(tmp_path/'source.mp4',duration=4,fps='25')
    opts=Options(enhance={'level':'Balanced'})
    result=render_preview(media,opts,{'enhancement':enhancement_proof},.5,clip_output=tmp_path/'clip.mp4')
    assert result.before.startswith(b'\x89PNG') and result.after.startswith(b'\x89PNG')
    assert result.before!=result.after
    chain=result.plan.enhancement['video_chain']
    after=result.commands[1]
    assert after[after.index('-vf')+1].startswith(chain+',scale=')
    assert result.commands[-1][result.commands[-1].index('-vf')+1]==chain
    clip=probe(tmp_path/'clip.mp4')
    assert 2.9<=clip.duration<=3.1 and not verify(clip,result.plan.target)
    assert media.path.exists() and not list(tmp_path.glob('*.part.mp4'))
    stop=threading.Event();stop.set()
    with pytest.raises(ProbeSkipped):
        render_preview(media,opts,{'enhancement':enhancement_proof},0,stop)
    with pytest.raises(ValueError,match='replace the source'):
        render_preview(media,opts,{'enhancement':enhancement_proof},0,clip_output=media.path)


def test_worker_enhancement_does_not_skip_safe_and_logs_exact_filters(tmp_path,enhancement_proof):
    media=source(tmp_path/'source.mp4',fps='25')
    # First make a truly safe source, so Skip-safe would normally skip it.
    initial=build_command(media,Options(),tmp_path/'safe.mp4',software=True)
    capture(initial.command)
    media=probe(tmp_path/'safe.mp4');assert not verify(media)
    opts=Options(output_mode='Same folder',skip_safe=True,enhance={'level':'Balanced'})
    job=Job(media.path,opts);job.media=media;events=[]
    EncodeWorker(job,opts,{'enhancement':enhancement_proof},lambda _,e:events.append(e)).run()
    assert events[-1]['status']=='TV-Safe ✓',events[-1]
    assert events[-1]['enhancement']['level']=='Balanced'
    assert 'hqdn3d' in events[-1]['details'] and 'Exact-job preflight' in events[-1]['details']
    assert events[-1]['enhancement']['estimated_speed']>0
    assert media.path.exists()


def test_external_ai_preflight_rejects_unconsented_missing_or_unsafe_input(tmp_path,enhancement_proof):
    media=source(tmp_path/'source.mp4',fps='25')
    opts=Options(enhance={'level':'Off','ai_enabled':True,'ai_executable':'missing'})
    assert ai_preprocess(media,opts,tmp_path/'scratch')[0] is media
    opts=replace(opts,enhance={'level':'Low','ai_enabled':True,'ai_executable':'missing','upscale':'1080p'})
    with pytest.raises(ValueError,match='executable is missing'):
        ai_preprocess(media,opts,tmp_path/'scratch')
    bad=tmp_path/'placeholder';bad.touch()
    opts=replace(opts,enhance={'level':'Low','ai_enabled':True,'ai_executable':str(bad),'upscale':'1080p'})
    media.video['pix_fmt']='yuv420p10le'
    with pytest.raises(ValueError,match='8-bit SDR'):
        ai_preprocess(media,opts,tmp_path/'scratch')


def test_external_ai_hook_with_explicit_non_ai_test_double(tmp_path,enhancement_proof):
    if os.name=='nt':pytest.skip('POSIX executable test double; real user-provided ncnn is PC-tested separately')
    import sys
    media=source(tmp_path/'source.mp4',duration=.4,fps='25')
    exe=tmp_path/'fake upscaler'
    exe.write_text('#!'+sys.executable+'\n'+'''import argparse,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('-i');p.add_argument('-o');p.add_argument('-s');p.add_argument('-f');a=p.parse_args()
subprocess.run(['''+repr(tool('ffmpeg'))+''', '-nostdin','-v','error','-threads','2','-filter_threads','2','-y','-framerate','25','-start_number','1','-i',str(Path(a.i)/'%08d.png'),'-vf','scale=iw*'+a.s+':ih*'+a.s,'-threads','2',str(Path(a.o)/'%08d.png')],check=True)
''')
    exe.chmod(0o755)
    opts=Options(enhance={'level':'Low','ai_enabled':True,'ai_executable':str(exe),'ai_scale':2,'upscale':'1080p'})
    scratch=tmp_path/'scratch'
    enhanced,notes=ai_preprocess(media,opts,scratch)
    assert (enhanced.video['width'],enhanced.video['height'])==(640,360)
    assert enhanced.fps==media.fps
    assert 'not a bundled/verified AI model' in notes[0]
    # The exact RGB full-range intermediate is color-converted, not silently treated as YUV limited.
    plan=build_command(enhanced,opts,tmp_path/'result.mp4',{'enhancement':enhancement_proof},software=True)
    assert 'matrixin=gbr' in plan.enhancement['video_chain']
    capture(plan.command,timeout=30)
    assert not verify(probe(tmp_path/'result.mp4'),plan.target)
    assert media.path.exists()


@pytest.mark.parametrize('original_rate',['900000',None])
def test_excellent_below_source_or_unknown_floor_never_authorizes_deletion(tmp_path,enhancement_proof,monkeypatch,original_rate):
    media=source(tmp_path/'source.mp4',fps='25')
    media.video['bit_rate']=original_rate
    opts=Options(output_mode='Same folder',skip_safe=False,delete_source=True,enhance={'level':'Excellent'})
    job=Job(media.path,opts);job.media=media;events=[]
    inspect=EncodeWorker._inspect
    def low_rate(self,*args):
        result,errors,values=inspect(self,*args)
        result.video['bit_rate']='100000'
        return result,errors,values
    monkeypatch.setattr(EncodeWorker,'_inspect',low_rate)
    EncodeWorker(job,opts,{'enhancement':enhancement_proof},lambda _,e:events.append(e)).run()
    assert events[-1]['status']=='Warning',events[-1]
    assert 'Excellent actual bitrate floor not verified' in events[-1]['reason']
    assert media.path.exists() and Path(events[-1]['output']).exists()


def test_real_full_range_sdr_and_primaries_conversion(tmp_path,enhancement_proof):
    media=source(tmp_path/'full-range.mp4',fps='25')
    # Deliberate source metadata override tests the actual requested pixel transform.
    media.video.update(color_range='pc',color_space='bt709',color_primaries='bt709',color_transfer='bt709')
    opts=Options(enhance={'level':'Low'})
    plan=build_command(media,opts,tmp_path/'limited.mp4',{'enhancement':enhancement_proof},software=True)
    capture(plan.command,timeout=30)
    result=probe(tmp_path/'limited.mp4')
    assert result.video['color_range']=='tv' and result.video['color_space']=='smpte170m'
    assert result.video['chroma_location']=='left'
    assert not verify(result,plan.target)


def test_actual_untagged_rgb_input_is_converted_not_invalid_gbr_primaries(tmp_path,enhancement_proof):
    path=tmp_path/'RGB.mkv'
    capture(['ffmpeg','-v','error','-nostdin','-y','-filter_threads','2','-f','lavfi','-i',
             'testsrc2=size=320x180:rate=25:duration=0.4','-c:v','ffv1','-pix_fmt','bgr0','-threads','2',str(path)])
    media=probe(path)
    assert media.video['pix_fmt']=='bgr0'
    opts=Options(enhance={'level':'Low'})
    plan=build_command(media,opts,tmp_path/'rgb-to-tv.mp4',{'enhancement':enhancement_proof},software=True)
    assert 'matrixin=gbr:primariesin=smpte170m' in plan.enhancement['video_chain']
    capture(plan.command,timeout=30)
    assert not verify(probe(tmp_path/'rgb-to-tv.mp4'),plan.target)
