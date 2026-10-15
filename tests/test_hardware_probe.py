"""Mock full scans, including missing builds/drivers, 10s bounds and cache invalidation."""
import json
import threading
import pytest
from app.core.hardware import probe as probing
from app.core.hardware.detect import detect_gpus
from app.core.hardware.matrix import GpuProfile
from app.core.process import ToolError, ProbeSkipped


@pytest.mark.parametrize('working', ['qsv=hw,child_device_type=dxva2',
    'qsv=hw,child_device_type=d3d11va', 'qsv=hw', 'qsv=hw:hw_any', None])
def test_full_scan_proves_controls_decode_device_order_and_cache(tmp_path, monkeypatch, working):
    binary=tmp_path/'ffmpeg';binary.touch()
    probe=tmp_path/'ffprobe';probe.touch()
    monkeypatch.setattr(probing,'tool',lambda name:str(binary if name=='ffmpeg' else probe))
    monkeypatch.setattr(probing,'data_dir',lambda:tmp_path)
    gpus=[GpuProfile('intel','Intel HD Graphics 4600','Intel',generation='Haswell'),
          GpuProfile('nv0','NVIDIA GeForce RTX 3060','NVIDIA',ordinal=0),
          GpuProfile('cpu','CPU','CPU')]
    monkeypatch.setattr(probing,'detect_gpus',lambda run:(gpus,[]))
    inventory=dict(ffmpeg=str(binary),ffprobe=str(probe),version='ffmpeg version 7.x',raw={'version':'7.x'},
                   encoders=['h264_qsv','hevc_qsv','h264_nvenc','libx264'],
                   hwaccels=['qsv','cuda','d3d11va','dxva2','d3d12va','vulkan'],
                   filters=['vpp_qsv','scale_cuda'],errors=[])
    monkeypatch.setattr(probing,'detect_ffmpeg',lambda *args:json.loads(json.dumps(inventory)))
    calls=[]
    def run(cmd, **kw):
        assert kw['timeout']==10
        calls.append(cmd)
        if '-h' in cmd:
            return ('Supported pixel formats: nv12 p010le yuv420p yuv420p10le\n'
                    ' -preset <int> preset\n -profile <int> profile\n -level <int> level\n'
                    ' -look_ahead <boolean> control\n -low_power <int> low power\n'
                    ' -rc <int> rate\n -cq <float> quality\n -gpu <int> ordinal\n'
                    ' -spatial-aq <boolean> aq\n -temporal-aq <boolean> aq\n -rc-lookahead <int> la\n')
        if '-init_hw_device' in cmd:
            device=cmd[cmd.index('-init_hw_device')+1]
            if device.startswith('qsv='):
                assert '-low_power' not in cmd  # Haswell exception applies to ALL tests.
                if device!=working:
                    raise ToolError('Error initializing MFX session: -9')
        if '-c:v' in cmd:
            codec=cmd[cmd.index('-c:v')+1]
            assert codec!='hevc_qsv'  # Haswell rejected BEFORE launch.
        if '-spatial-aq' in cmd or '-global_quality' in cmd:
            raise ToolError('Control unsupported')
        return 'frame=60\nspeed=3.25x\nprogress=end\n'
    monkeypatch.setattr(probing,'capture',run)
    result=probing.scan_hardware(run=run)
    intel=next(c for c in result.encoders if c.name=='h264_qsv')
    nv=next(c for c in result.encoders if c.name=='h264_nvenc')
    assert intel.available==(working is not None)
    if working:
        assert intel.device_args[1]==working
        assert 'ICQ' not in intel.modes
        assert any(d['available'] and d['backend']=='qsv' for d in intel.decoders)
        assert intel.gpu_filters['available']
    assert nv.available
    assert len(nv.tested_values['preset'])==7
    assert 'spatial-aq' not in nv.tested_values
    assert nv.tested_values['temporal-aq']==['0','1']
    assert nv.gpu_filters['available'] and any(d['available'] for d in nv.decoders)
    assert nv.max_resolution is None and nv.max_fps is None and nv.hdr is None
    failed=next(c for c in result.encoders if c.name=='av1_nvenc')
    assert not failed.available and 'lacks' in failed.reason
    assert any(d.get('backend')=='vulkan' for d in result.inventory['decode_tests'])
    before=len(calls)
    cached=probing.scan_hardware(run=run)
    assert cached.fingerprint==result.fingerprint and len(calls)==before
    gpus[0].driver='new driver'
    probing.scan_hardware(run=run)
    assert len(calls)>before


def test_help_parser_hyphenated_options():
    options, formats=probing.parse_encoder_help('Supported pixel formats: nv12 p010le\n'
        ' -rc-lookahead <int> lookahead\n -spatial-aq <boolean> aq\n -x265-params <string> params\n')
    assert set(options)=={'rc-lookahead','spatial-aq','x265-params'}
    assert formats==['nv12','p010le']


def test_windows_wmi_enumeration_not_assumed_from_encoder_name(monkeypatch):
    import app.core.hardware.detect as detect
    monkeypatch.setattr(detect.shutil,'which',lambda _:None)
    def run(cmd, **kwargs):
        assert 'Win32_VideoController' in cmd[-1]
        return json.dumps([dict(Name='Intel HD Graphics 4600',PNPDeviceID='PCI\\VEN_8086&DEV_0412',
                                DriverVersion='20.19.15',AdapterRAM=1024),
                           dict(Name='NVIDIA GeForce GTX 1060',PNPDeviceID='PCI\\VEN_10DE&DEV_1C03',
                                DriverVersion='31.0',AdapterRAM=2048)])
    gpus,errors=detect_gpus(run,platform='nt')
    assert not errors and [g.vendor for g in gpus]==['Intel','NVIDIA','CPU']
    assert gpus[0].generation=='Haswell' and gpus[1].generation=='Pascal'


def test_scan_cancel_never_caches_a_partial_success(tmp_path,monkeypatch):
    binary=tmp_path/'tool';binary.touch()
    monkeypatch.setattr(probing,'tool',lambda _:str(binary))
    monkeypatch.setattr(probing,'data_dir',lambda:tmp_path)
    monkeypatch.setattr(probing,'detect_gpus',lambda run:([GpuProfile('cpu','CPU','CPU')],[]))
    monkeypatch.setattr(probing,'detect_ffmpeg',lambda *a:dict(ffmpeg=str(binary),ffprobe=str(binary),errors=[],
                        encoders=['libx264'],filters=[],hwaccels=[]))
    cancel=threading.Event();cancel.set()
    with pytest.raises(ProbeSkipped):
        probing.scan_hardware(cancel=cancel,run=lambda *a,**kw:'')
    assert not (tmp_path/'hardware-matrix.json').exists()
