from app.core.hardware.matrix import HardwareMatrix, GpuProfile, EncoderCapabilities
from app.core.hardware.flagmap import mode_candidates


def encoder(vendor, name='h264', speed=2, device=None):
    suffix = {'Intel':'qsv', 'NVIDIA':'nvenc', 'AMD':'amf', 'CPU':''}[vendor]
    full = name + '_' + suffix if suffix else {'h264':'libx264','hevc':'libx265','av1':'libsvtav1'}[name]
    gpu_id = device or vendor.lower()
    cap = EncoderCapabilities(gpu_id + '/' + full, gpu_id, full, vendor, name, available=True, reason='', speed=speed)
    cap.help_options = ['preset', 'profile', 'level', 'look_ahead', 'rc', 'cq', 'crf', 'quality', 'qp_i', 'qp_p', 'gpu']
    cap.modes = mode_candidates(full, vendor, cap.help_options)
    cap.samples = [dict(width=1920,height=1080,fps=60,bit_depth=8,chroma='420')]
    cap.tested_values = {'preset':['p1','p4','p6','p7'] if vendor == 'NVIDIA' else ['veryfast','medium','slow','veryslow'], 'bf':['0','2']}
    if vendor == 'Intel':
        cap.device_args = ['-init_hw_device','qsv=hw,child_device_type=dxva2','-filter_hw_device','hw']
        cap.features['haswell'] = True
    if vendor == 'NVIDIA':
        cap.device_args = ['-init_hw_device','cuda=hw:0','-filter_hw_device','hw']
    return cap


def matrix(*vendors):
    caps=[encoder(vendor) for vendor in vendors]
    return HardwareMatrix(gpus=[GpuProfile(c.gpu_id,c.vendor,c.vendor) for c in caps], encoders=caps)
