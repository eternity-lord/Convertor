import json
import threading
from dataclasses import replace
from pathlib import Path
import pytest
from app.core.hardware.matrix import HardwareMatrix
from app.core.hardware.select import Request, choose_pipeline, rank_encoders, distribute_devices
from app.core.hardware.flagmap import QualityModel, mapped_flags, mode_candidates
from app.core.hardware.errors import classify_error
from app.core.hardware.detect import gpu_from_wmi, parse_listing, fingerprint, generation_of
from app.core.hardware.runtime import DeviceSlots
from app.core.process import ToolError, ProbeSkipped
from app.core.tv_rules import Options
from app.core.pipeline import build_command
from hardware_support import encoder, matrix
from test_rules import media


@pytest.mark.parametrize('vendors,expected', [
    (['Intel','CPU'],'Intel'), (['Intel','NVIDIA','CPU'],'NVIDIA'),
    (['AMD','CPU'],'AMD'), (['NVIDIA','CPU'],'NVIDIA'), (['CPU'],'CPU')])
def test_selection_required_pc_matrices(vendors, expected):
    m=matrix(*vendors)
    for c in m.encoders:
        c.speed={'Intel':1.2,'NVIDIA':3.4,'AMD':2.5,'CPU':10}[c.vendor]
    pick=choose_pipeline(m,Request())
    assert pick.encoder.vendor==expected
    assert pick.encoder.available
    assert 'CPU fallback' in pick.description if expected=='CPU' else 'CPU decode' in pick.description


def test_ffmpeg_without_nvenc_and_preferred_gpu_unavailable():
    m=matrix('NVIDIA','Intel','CPU')
    m.encoders[0].available=False
    m.encoders[0].reason='Active FFmpeg lacks h264_nvenc'
    choice=choose_pipeline(m,Request(device='NVIDIA'))
    assert choice.encoder.vendor=='Intel'
    assert 'NVIDIA unavailable' in choice.description
    assert choose_pipeline(m,Request(device='CPU')).encoder.vendor=='CPU'


def test_no_silent_codec_resolution_or_bit_depth_loss():
    m=matrix('Intel','CPU')
    with pytest.raises(ValueError,match='preserve'):
        choose_pipeline(m,Request(codec='hevc'))
    m.encoders.append(encoder('CPU','hevc'))
    assert choose_pipeline(m,Request(codec='hevc')).encoder.name=='libx265'
    with pytest.raises(ValueError):
        choose_pipeline(m,Request(bit_depth=10))
    choice=choose_pipeline(m,Request(width=3840,height=2160,fps=60))
    assert choice.encoder.vendor=='CPU'
    assert 'not proven' in choice.description
    assert choice.encoder.max_resolution is None and choice.encoder.hdr is None


def test_haswell_rejects_stale_modern_capability():
    cap=encoder('Intel','hevc')
    m=HardwareMatrix(encoders=[cap,encoder('CPU','hevc')])
    assert choose_pipeline(m,Request(codec='hevc')).encoder.vendor=='CPU'


@pytest.mark.parametrize('fmt,codec', [('yuv420p10le','h264'),('yuv422p','h264'),
                                       ('yuv422p10le','prores'),('yuv422p10le','dnxhd')])
def test_cpu_decode_rules_even_when_gpu_has_decoder(fmt,codec):
    m=matrix('NVIDIA','CPU')
    cap=m.encoders[0]
    cap.decoders=[dict(available=True,backend='cuda',codec=codec,bit_depth=8,
                       width=1920,height=1080,fps=60,same_device=True,args=[])]
    choice=choose_pipeline(m,Request(source_codec=codec,source_format=fmt))
    assert choice.encoder.hardware and choice.decoder is None
    assert not choice.gpu_filters


def test_same_device_decode_and_gpu_filters_require_proof():
    m=matrix('Intel')
    cap=m.encoders[0]
    dec=dict(available=True,backend='qsv',codec='h264',bit_depth=8,width=1920,height=1080,
             fps=60,same_device=True,args=['-hwaccel','qsv','-hwaccel_device','hw','-hwaccel_output_format','qsv'])
    cap.decoders=[dec]
    cap.gpu_filters={'available':True,'width':1920,'height':1080,'fps':60}
    assert choose_pipeline(m,Request()).gpu_filters
    assert not choose_pipeline(m,Request(gpu_filter_eligible=False)).gpu_filters
    dec['same_device']=False
    assert choose_pipeline(m,Request()).decoder is None
    dec['same_device']=True
    dec['available']=False
    assert choose_pipeline(m,Request()).decoder is None


def test_exclusion_and_multi_gpu_distribution():
    m=matrix('Intel','NVIDIA','CPU')
    assert choose_pipeline(m,Request(),[m.encoders[0].id]).encoder.vendor=='NVIDIA'
    assert distribute_devices(m,'h264',{'intel':1},True)=='nvidia'
    assert distribute_devices(m,'h264',{'intel':1,'nvidia':1},True) is None
    assert distribute_devices(m,'h264',{'intel':1,'nvidia':1},False) in ('intel','nvidia')


@pytest.mark.parametrize('vendor,rc,flag,value', [('Intel','Quality','-global_quality','23'),
    ('NVIDIA','CQ','-cq','23'),('NVIDIA','VBR','-preset','p4'),('AMD','VBR','-rc','vbr_peak'),
    ('CPU','CQ','-crf','23')])
def test_mapping_only_tested_modes(vendor,rc,flag,value):
    cap=encoder(vendor)
    flags,notes,mode=mapped_flags(cap,QualityModel(rc=rc),{'made_up':1,'low_power':1})
    assert flags[flags.index(flag)+1]==value
    assert '-made_up' not in flags and '-low_power' not in flags
    assert notes


def test_unsupported_quality_control_visible_substitution_and_no_cqp_default():
    cap=encoder('Intel')
    del cap.modes['ICQ']
    flags,notes,mode=mapped_flags(cap,QualityModel(rc='ICQ'))
    assert mode=='VBR' and any('not proven' in n for n in notes)
    assert '-global_quality' not in flags
    assert all(f in flags for f in ('-b:v','-maxrate','-bufsize'))
    assert '-low_power' not in flags


def test_failed_or_unadvertised_preset_and_advanced_values_not_emitted():
    cap=encoder('NVIDIA')
    cap.tested_values['preset']=['p1']
    cap.tested_values['spatial-aq']=['0']
    cap.help_options+=['spatial-aq']
    flags,notes,_=mapped_flags(cap,QualityModel(speed='Max Quality'),{'spatial-aq':1})
    assert '-spatial-aq' not in flags
    assert flags[flags.index('-preset')+1]=='p1'
    assert any('p7' in n for n in notes)
    cap.help_options.remove('preset')
    flags,_,_=mapped_flags(cap,QualityModel())
    assert '-preset' not in flags


@pytest.mark.parametrize('vendor', ['Intel','NVIDIA','AMD','CPU'])
def test_integrated_command_preserves_tv_timing_and_vendor_flags(vendor):
    m=matrix(vendor)
    plan=build_command(media(),Options(),Path('out.mp4'),{'matrix':m.to_dict()},executable='ffmpeg')
    c=plan.command
    assert c[c.index('-c:v')+1]==m.encoders[0].name
    assert c[c.index('-fps_mode')+1]=='cfr'
    assert c[c.index('-c:a')+1]=='aac'
    assert '-low_power' not in c
    assert '-x264-params' not in c  # fixture did not advertise it
    vf=c[c.index('-vf')+1]
    assert 'format=nv12' in vf if vendor!='CPU' else 'format=yuv420p' in vf
    assert 'hwupload=extra_hw_frames=16' in vf if vendor in ('Intel','NVIDIA') else True
    if vendor=='NVIDIA':
        assert 'p4' in c and '-global_quality' not in c and 'qsv=hw' not in c
    if vendor=='Intel':
        assert 'qsv=hw,child_device_type=dxva2' in c
    if vendor=='AMD':
        assert '-preset' not in c and 'vbr_peak' in c
    assert plan.gpu_id==m.encoders[0].gpu_id


def test_matrix_serialization_and_wmi_metadata():
    m=matrix('Intel','NVIDIA','CPU')
    assert HardwareMatrix.from_dict(json.loads(json.dumps(m.to_dict()))).to_dict()==m.to_dict()
    g=gpu_from_wmi(dict(Name='Intel HD Graphics 4600',PNPDeviceID='PCI\\VEN_8086&DEV_0412',
                        DriverVersion='20.19',AdapterRAM=1024),0)
    assert (g.vendor,g.device_id,g.driver,g.ram,g.generation)==('Intel','0412','20.19',1024,'Haswell')
    assert generation_of('NVIDIA GeForce RTX 3060')=='Ampere'
    assert generation_of('AMD Radeon RX 6600')=='RDNA 2'
    assert parse_listing(' V....D h264_nvenc test\n V..... libx264 test\n ------\n','encoders')==['h264_nvenc','libx264']


def test_cache_fingerprint_changes_on_driver_or_binary(tmp_path):
    p=tmp_path/'ffmpeg';p.write_text('x')
    q=tmp_path/'ffprobe';q.write_text('y')
    inv={'ffmpeg':str(p),'ffprobe':str(q)}
    g=matrix('Intel').gpus
    first=fingerprint(inv,g)
    g[0].driver='new driver'
    assert fingerprint(inv,g)!=first
    second=fingerprint(inv,g)
    p.write_text('a changed binary')
    assert fingerprint(inv,g)!=second


@pytest.mark.parametrize('text,code', [('Error initializing MFX session: -9','mfx_session'),
    ('Cannot load nvcuda.dll','cuda_driver'),('OpenEncodeSessionEx failed: unsupported device','nvenc_session'),
    ('No capable devices found','no_device'),('AMF DLL not found','amf_driver'),
    ('out of memory','memory'),('too many encoding sessions','sessions')])
def test_error_translator(text,code):
    error=classify_error(text)
    assert error.code==code and error.hint and error.hint_fa
    assert error.describe('fa')!=error.describe('en')


def test_device_slot_guard_cancellation_and_release():
    slots=DeviceSlots()
    cancelled=threading.Event()
    with slots.claim('intel',1,threading.Event()):
        cancelled.set()
        with pytest.raises(ProbeSkipped):
            with slots.claim('intel',1,cancelled):
                pytest.fail('Must not acquire busy / cancelled device')
    assert slots.active['intel']==0
    with pytest.raises(RuntimeError):
        with slots.claim('nvidia',1,threading.Event()):
            raise RuntimeError('encode failed')
    assert slots.active['nvidia']==0
