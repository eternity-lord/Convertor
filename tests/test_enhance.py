"""Pure phase-two policy/chain regressions; no Qt, driver or FFmpeg dependency."""
from dataclasses import replace
import pytest
from app.core.enhance.model import settings_from, HONEST_TOOLTIP
from app.core.enhance.planning import headroom, estimate_speed
from app.core.enhance.integrate import split_filters
from app.core.enhance.probe import VIDEO_TESTS, AUDIO_TESTS, probe_filters
from app.core.pipeline import build_command, _build_base_command
from app.core.tv_rules import Options, choose_target
from app.core.process import ToolError
from hardware_support import matrix
from test_rules import media


def proof():
    return {k:dict(available=True,speed=100,width=320,height=180,fps=30) for k in (*VIDEO_TESTS,*AUDIO_TESTS)}


def caps(vendor=None):
    if vendor:
        m=matrix(vendor,'CPU') if vendor!='CPU' else matrix('CPU')
        m.inventory['enhancement']=proof()
        return {'matrix':m.to_dict()}
    return {'enhancement':proof()}


def test_defaults_off_are_byte_for_byte_sdr_noop():
    opts=Options();source=media()
    for c in ({},caps(),caps('Intel'),caps('CPU')):
        current=build_command(source,opts,'out.mp4',c,executable='ffmpeg')
        previous=_build_base_command(source,opts,'out.mp4',c,executable='ffmpeg')
        assert current.command==previous.command
        assert not current.enhancement
    assert settings_from().level=='Off'
    assert 'cannot create real detail' in HONEST_TOOLTIP


@pytest.mark.parametrize('data', [{'level':'Magic'}, {'level':'Custom','sharpen':100},
    {'gamma':float('nan')},{'contrast':2},{'denoise':-1},{'upscale':'8K'},{'ai_scale':3},
    {'input_range':'secret'},{'color':'auto-bad'},{'denoiser':'unknown'}])
def test_invalid_settings_fail_plainly(data):
    with pytest.raises(ValueError):settings_from(data)


@pytest.mark.parametrize('level',['Low','Balanced','Excellent','Custom'])
def test_presets_keep_dimensions_fps_raise_quality_headroom(level):
    source=media('30000/1001');opts=Options(enhance={'level':level})
    enhanced=build_command(source,opts,'out.mp4',caps(),software=True,executable='ffmpeg')
    original=_build_base_command(source,Options(),'out.mp4',{},software=True,executable='ffmpeg')
    assert (enhanced.target.width,enhanced.target.height,enhanced.target.fps)==(original.target.width,original.target.height,original.target.fps)
    assert enhanced.target.bitrate>=original.target.bitrate
    assert enhanced.target.bitrate<=enhanced.target.maxrate
    assert enhanced.command[enhanced.command.index('-r')+1]=='30000/1001'
    assert enhanced.enhancement['level']==level
    assert 'hqdn3d=' in enhanced.enhancement['video_chain']
    assert 'Enhance '+level in enhanced.encoding_status
    assert 'nlmeans=' not in enhanced.enhancement['video_chain']  # Safe mode default


def test_excellent_bitrate_floor_never_silently_overrides_tv_or_fat32():
    source=media();source.video['bit_rate']='19000000'
    adjusted,notes=headroom(source,Options(enhance={'level':'Excellent'},rc='ICQ'))
    assert adjusted.bitrate>=19 and adjusted.rc=='VBR'
    assert any('VBR' in n for n in notes)
    source.video['bit_rate']='25000000'
    with pytest.raises(ValueError,match='No silent reduction'):
        headroom(source,Options(enhance={'level':'Excellent'}))
    source.video['bit_rate']='15000000';source.video['duration']='7200'
    with pytest.raises(ValueError,match='No silent reduction'):
        headroom(source,Options(enhance={'level':'Excellent'},fat32='Lower bitrate to fit'))


def test_high_precision_dither_happens_after_processing_before_upload():
    source=media()
    plan=build_command(source,Options(enhance={'level':'Balanced'}),'out.mp4',caps('Intel'),executable='ffmpeg')
    chain=plan.enhancement['video_chain']
    assert chain.index('format=yuv420p10le')<chain.index('hqdn3d=')<chain.index('zscale=dither=error_diffusion')<chain.index('hwupload=')
    assert chain.endswith('format=nv12,hwupload=extra_hw_frames=16')
    assert '-low_power' not in plan.command
    assert plan.command[plan.command.index('-init_hw_device')+1]=='qsv=hw,child_device_type=dxva2'
    assert plan.command[plan.command.index('-c:v')+1]=='h264_qsv'
    assert 'CPU enhancement' in plan.encoding_status


def test_proven_gpu_low_recipe_retains_full_qsv_path_but_balanced_is_hybrid():
    c=caps('Intel');m=c['matrix'];cap=m['encoders'][0]
    cap['decoders']=[dict(available=True,codec='h264',bit_depth=8,width=1920,height=1080,fps=60,same_device=True,
        backend='qsv',args=['-hwaccel','qsv','-hwaccel_device','hw','-hwaccel_output_format','qsv'])]
    cap['gpu_filters']=dict(available=True,width=1920,height=1080,fps=60)
    cap['features']['enhance']={'low':dict(available=True,filter='vpp_qsv=denoise=2:detail=3',speed=3,width=1920,height=1080,fps=60)}
    plan=build_command(media(),Options(enhance={'level':'Low'}),'out.mp4',c,executable='ffmpeg')
    assert plan.pipeline=='GPU full'
    assert 'vpp_qsv=denoise=2:detail=3' in plan.enhancement['video_chain']
    assert 'hwdownload' not in plan.enhancement['video_chain']
    assert 'hqdn3d' not in plan.enhancement['video_chain']
    hybrid=build_command(media(),Options(enhance={'level':'Balanced'}),'out.mp4',c,executable='ffmpeg')
    assert hybrid.pipeline=='Hybrid'
    assert 'hwdownload,format=nv12' in hybrid.enhancement['video_chain']
    assert 'CPU enhancement' in hybrid.encoding_status


def test_failed_filters_are_omitted_with_reasons_not_assumed_from_listing():
    c=caps();c['enhancement']['hqdn3d']={'available':False,'reason':'runtime failed'}
    c['enhancement']['unsharp']={'available':False,'reason':'missing'}
    plan=build_command(media(),Options(enhance={'level':'Low'}),'out.mp4',c,software=True,executable='ffmpeg')
    assert 'hqdn3d=' not in plan.enhancement['video_chain']
    assert 'unsharp=' not in plan.enhancement['video_chain']
    assert 'runtime failed' in plan.encoding_status


@pytest.mark.parametrize('transfer',['smpte2084','arib-std-b67'])
def test_hdr_requires_real_conversion_and_dither_not_just_retagging(transfer):
    source=media();source.video.update(pix_fmt='yuv420p10le',color_transfer=transfer,color_space='bt2020nc')
    opts=Options(enhance={'level':'Balanced'})
    plan=build_command(source,opts,'out.mp4',caps(),software=True,executable='ffmpeg')
    chain=plan.enhancement['video_chain']
    assert f'transferin={transfer}' in chain
    assert 'format=gbrpf32le,tonemap=' in chain
    assert 'sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA' in chain
    assert 'dither=error_diffusion' in chain and 'format=yuv420p' in chain
    with pytest.raises(ValueError,match='HDR source requires'):
        build_command(source,Options(),'out.mp4',caps(),software=True,executable='ffmpeg')
    c=caps();c['enhancement']['hdr_tonemap']={'available':False,'reason':'zscale missing'}
    with pytest.raises(ValueError,match='zscale missing'):
        build_command(source,opts,'out.mp4',c,software=True,executable='ffmpeg')


def test_full_range_and_matrix_are_converted_and_custom_retag_is_blocked():
    source=media();source.video.update(color_range='pc',color_space='bt709',color_primaries='bt709',color_transfer='bt709')
    plan=build_command(source,Options(enhance={'level':'Low'}),'out.mp4',caps(),software=True,executable='ffmpeg')
    assert 'rangein=full' in plan.enhancement['video_chain']
    with pytest.raises(ValueError,match='arbitrary retagging'):
        build_command(source,Options(enhance={'level':'Custom','color':'bt2020'}),'out.mp4',caps(),software=True,executable='ffmpeg')


def test_explicit_upscale_keeps_aspect_never_changes_fps_or_exceeds_tv_cap():
    source=media('24000/1001',1280,720)
    opts=Options(enhance={'level':'Low','upscale':'1080p'})
    target=choose_target(source,opts)
    assert (target.width,target.height,target.fps)==(1920,1080,source.fps)
    assert any('not AI' in n for n in target.notes)
    with pytest.raises(ValueError,match='cap'):
        choose_target(source,replace(opts,enhance={'level':'Low','upscale':'4K'}))
    unknown=media();unknown.metadata_verified=False
    with pytest.raises(ValueError,match='verified source'):
        choose_target(unknown,opts)


def test_audio_enhance_does_not_remove_repair_resample_or_padding():
    opts=Options(audio='Keep all',enhance={'level':'Low','loudnorm':True,'audio_cleanup':True})
    plan=build_command(media(),opts,'out.mp4',caps(),software=True,executable='ffmpeg')
    c=plan.command;af=c[c.index('-filter:a:0')+1]
    assert af.index('asetpts=')<af.index('loudnorm=')<af.index('aresample=')<af.index('apad')
    assert 'highpass=f=70' in af and 'afftdn=nf=-25' in af
    assert c[c.index('-ar')+1]=='48000' and c[c.index('-ac')+1]=='2'


def test_unknown_speed_is_not_fabricated_and_weak_cpu_skips_heavy_filter():
    assert estimate_speed([{}],choose_target(media(),Options())) is None
    c=caps();c['enhancement']['nlmeans']['speed']=.001
    plan=build_command(media(),Options(safe_mode=False,enhance={'level':'Excellent'}),'out.mp4',c,software=True,executable='ffmpeg')
    assert 'nlmeans=' not in plan.enhancement['video_chain']
    assert 'nlmeans skipped' in plan.encoding_status
    assert plan.enhancement['estimated_speed']>0


def test_expression_aware_filter_split_preserves_quoted_min_and_fps():
    text="setpts=PTS-STARTPTS,scale=w='min(iw,1920)':h='min(ih,1080)',fps=fps=25:round=near,format=yuv420p"
    parts=split_filters(text)
    assert len(parts)==4 and "min(iw,1920)" in parts[1]


def test_filter_probe_rejects_listing_only_runtime_failure_and_honors_timeout():
    calls=[]
    def run(command,**kwargs):
        assert kwargs['timeout']==10
        calls.append(command)
        if 'hqdn3d' in command[command.index('-vf')+1]:raise ToolError('unsupported runtime')
        return 'speed=2.5x\n'
    inventory={'filters':['hqdn3d','unsharp']}
    result=probe_filters('ffmpeg',inventory,run)
    assert not result['hqdn3d']['available'] and 'runtime' in result['hqdn3d']['reason']
    assert result['unsharp']['available'] and result['unsharp']['speed']==2.5
    assert not result['hdr_tonemap']['available']
    assert not result['nlmeans_opencl']['available']


def test_interlaced_enhancement_needs_explicit_deinterlace_and_ninebit_requires_dither():
    source=media();source.video['field_order']='tt'
    with pytest.raises(ValueError,match='explicit deinterlacing'):
        build_command(source,Options(deinterlace=False,enhance={'level':'Balanced'}),'out.mp4',caps(),software=True)
    source.video.update(field_order='progressive',pix_fmt='yuv420p9le')
    c=caps();c['enhancement']['dither']={'available':False,'reason':'missing zscale'}
    with pytest.raises(ValueError,match='missing zscale'):
        build_command(source,Options(enhance={'level':'Low'}),'out.mp4',c,software=True)


def test_differing_primaries_or_transfer_always_convert_not_just_retag():
    source=media();source.video.update(color_space='bt709',color_range='tv',color_primaries='bt2020',color_transfer='gamma28')
    plan=build_command(source,Options(enhance={'level':'Low'}),'out.mp4',caps(),software=True)
    assert 'primariesin=bt2020:transferin=bt470bg' in plan.enhancement['video_chain']


def test_boolean_settings_cannot_enable_external_executable_by_truthy_string():
    with pytest.raises(ValueError,match='explicit boolean'):
        settings_from({'ai_enabled':'false'})
