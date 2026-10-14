from fractions import Fraction
from pathlib import Path
import pytest
from app.core.ffprobe import Media, Cadence, rate, snap, analyze_timestamps
from app.core.tv_rules import Options, auto_level, choose_target, timescale, verify, faststart_ok
from app.core.pipeline import build_command, output_path


def media(fps='25', width=1920, height=1080, audio=True):
    return Media(Path('input فارسی.mp4'), [dict(codec_type='video', codec_name='h264',
        width=width, height=height, pix_fmt='yuv420p', r_frame_rate=fps,
        avg_frame_rate=fps, duration='2', nb_frames='50', field_order='progressive',
        sample_aspect_ratio='1:1'), *([dict(codec_type='audio', codec_name='aac',
        profile='LC', sample_rate='48000', channels=2, duration='2')] if audio else [])],
        {'duration':'2'}, Cadence(rate(fps), samples=49))


@pytest.mark.parametrize('width,height,fps,level',[(1920,1080,30,'4.1'),(1920,1080,50,'4.2'),
    (1920,1080,60,'4.2'), (3840,2160,30,'5.1'),(3840,2160,60,'5.2'),(1280,720,25,'3.1')])
def test_level(width,height,fps,level):
    assert auto_level(width,height,fps) == level


@pytest.mark.parametrize('text,expected',[('29.97','30000/1001'),('23.976','24000/1001'),('59.94','60000/1001'),('25','25')])
def test_rational_rates(text,expected):
    assert rate(text) == Fraction(expected)


def test_jitter_clusters():
    pts=[0]
    for i in range(100):
        pts.append(pts[-1]+(.04 if i%3 else 1/29.97))
    result=analyze_timestamps([pts],rate('50'),rate('27.4'))
    assert result.unstable
    assert result.dominant == 25


def test_b_frame_packet_order_and_timebase_quantisation():
    pts=[round(i/float(rate('29.97')),3) for i in range(1000)]
    pts[1],pts[3]=pts[3],pts[1]
    result=analyze_timestamps([pts],rate('29.97'),rate('29.97'))
    assert not result.unstable
    assert result.dominant == rate('29.97')


def test_duplicates_negative_pts():
    result=analyze_timestamps([[-.04,0,.04,.04,.08]],rate('25'),rate('25'))
    assert result.duplicate and result.negative and result.unstable


def test_no_rounding_and_per_file_auto():
    assert snap('27.4') == 25
    opts=Options()
    assert choose_target(media('30000/1001'),opts).fps==rate('29.97')
    assert choose_target(media('25'),opts).fps==25


def test_fixed_fps_wins_and_level_raised():
    target=choose_target(media('50'),Options(fps='50',level='4.1'))
    assert target.fps==50
    assert target.level=='4.2'
    assert target.notes


def test_odd_dimensions_and_caps():
    target=choose_target(media('25',1919,1079),Options(maxrate=100,bufsize=100))
    assert target.width%2==target.height%2==0
    assert target.maxrate==20 and target.bufsize<=40


def test_invalid_custom_rate():
    with pytest.raises(ValueError):
        choose_target(media(),Options(fps='0'))


def test_avc_level_limit():
    with pytest.raises(ValueError):
        auto_level(7680,4320,120)


def test_timescales():
    assert timescale(rate('25'))==25000
    assert timescale(rate('29.97'))==30000
    assert timescale(rate('23.976'))==24000


def test_cpu_command():
    plan=build_command(media(),Options(),Path('out.mp4'),software=True,executable='ffmpeg')
    c=plan.command
    assert '-vsync' not in c
    assert c[c.index('-fps_mode')+1]=='cfr'
    assert c[c.index('-c:v')+1]=='libx264'
    assert c[c.index('-c:a')+1]=='aac'
    assert c[c.index('-map')+1]=='0:v:0'
    assert '0' not in [c[i+1] for i,x in enumerate(c) if x=='-map']
    assert 'fps=fps=25:round=near' in c[c.index('-vf')+1]
    assert '-sn' in c and '-dn' in c and '+faststart' in c
    assert 'aresample=async=1:first_pts=0' in c[c.index('-af')+1]


def test_gpu_hybrid_and_full():
    caps={'codecs':{'h264_qsv':{'available':True,'low_power':1}},'decode':False}
    plan=build_command(media(),Options(),Path('out.mp4'),caps,executable='ffmpeg')
    assert plan.pipeline=='Hybrid'
    assert 'hwupload' in plan.command[plan.command.index('-vf')+1]
    caps.update(decode=True,vpp_qsv=True,vpp_framerate=True)
    plan=build_command(media(),Options(),Path('out.mp4'),caps,executable='ffmpeg')
    assert plan.pipeline=='GPU full'
    assert 'framerate=25' in plan.command[plan.command.index('-vf')+1]


def test_silent_audio_and_paths():
    plan=build_command(media(audio=False),Options(),Path('out.mp4'),executable='ffmpeg')
    assert 'anullsrc=r=48000:cl=stereo' in plan.command
    assert '1:a:0' in plan.command
    assert 'input فارسی.mp4' in plan.command


def test_output_safety(tmp_path):
    source=tmp_path/'x.mp4'
    source.touch()
    with pytest.raises(ValueError):
        output_path(source,Options(suffix=''))
    with pytest.raises(ValueError):
        output_path(source,Options(suffix='../evil'))
    assert output_path(source,Options()).name=='x_TV.mp4'


def test_faststart_headers(tmp_path):
    import struct
    path=tmp_path/'test.mp4'
    path.write_bytes(struct.pack('>I4s',8,b'ftyp')+struct.pack('>I4s',8,b'moov'))
    assert faststart_ok(path)
    path.write_bytes(struct.pack('>I4s',8,b'mdat')+struct.pack('>I4s',8,b'moov'))
    assert not faststart_ok(path)


def test_fat32_fit():
    m=media(); m.video['duration']='7200'
    target=choose_target(m,Options(fat32='Lower bitrate to fit'))
    assert target.bitrate < 5


def test_bad_media_rejected():
    m=media(); m.video.update(pix_fmt='yuv422p10le',level=41,codec_tag_string='avc3')
    violations=verify(m)
    assert 'Not 8-bit 4:2:0' in violations
    assert 'Requires H.264 / avc1' in violations


@pytest.mark.parametrize('codec,tag',[('hevc_qsv','hvc1'),('av1_qsv','av01')])
def test_modern_hqx_encoders(codec,tag):
    caps={'codecs':{codec:{'available':True}}}
    plan=build_command(media(),Options(codec=codec),Path('out.mp4'),caps,executable='ffmpeg')
    assert plan.command[plan.command.index('-c:v')+1]==codec
    assert plan.command[plan.command.index('-tag:v')+1]==tag
    assert plan.target.profile=='main'
    if codec=='av1_qsv':
        assert '-level:v' not in plan.command
    fallback=build_command(media(),Options(codec=codec),Path('out.mp4'),caps,software=True,executable='ffmpeg')
    assert fallback.codec=='h264'
