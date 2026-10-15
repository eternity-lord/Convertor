"""Serializable evidence, not a table of advertised vendor capabilities."""
from dataclasses import dataclass, field, asdict

SCHEMA = 1
ENCODERS = {
    'Intel': ['h264_qsv', 'hevc_qsv', 'av1_qsv', 'vp9_qsv', 'mpeg2_qsv', 'mjpeg_qsv'],
    'NVIDIA': ['h264_nvenc', 'hevc_nvenc', 'av1_nvenc'],
    'AMD': ['h264_amf', 'hevc_amf', 'av1_amf'],
    'Media Foundation': ['h264_mf', 'hevc_mf'],
    'CPU': ['libx264', 'libx265', 'libsvtav1', 'libaom-av1', 'libvpx-vp9', 'librav1e'],
}
CODECS = dict(zip(ENCODERS['CPU'], ['h264', 'hevc', 'av1', 'av1', 'vp9', 'av1']))
CODECS.update({name: name.split('_')[0] for names in ENCODERS.values() for name in names if name not in CODECS})


@dataclass
class GpuProfile:
    id: str
    name: str
    vendor: str
    vendor_id: str = ''
    device_id: str = ''
    driver: str = ''
    ram: int | None = None
    generation: str = 'Unknown'
    ordinal: int | None = None
    affinity: str = 'Runtime default; physical adapter binding not verified'


@dataclass
class EncoderCapabilities:
    id: str
    gpu_id: str
    name: str
    vendor: str
    codec: str
    available: bool = False
    reason: str = 'Not tested'
    speed: float | None = None
    device_args: list = field(default_factory=list)
    encoder_args: list = field(default_factory=list)
    help_options: list = field(default_factory=list)
    advertised_formats: list = field(default_factory=list)
    # Each mode contains exact, successfully tested flags. No inferred controls.
    modes: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    tested_values: dict = field(default_factory=dict)
    samples: list = field(default_factory=list)
    decoders: list = field(default_factory=list)
    gpu_filters: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)
    max_resolution: list | None = None  # Unknown unless a limit is actually established.
    max_fps: float | None = None
    hdr: bool | None = None

    @property
    def hardware(self):
        return self.vendor != 'CPU'

    def to_dict(self):
        return asdict(self)


@dataclass
class HardwareMatrix:
    gpus: list = field(default_factory=list)
    encoders: list = field(default_factory=list)
    inventory: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    fingerprint: str = ''
    timestamp: float = 0
    schema: int = SCHEMA

    def to_dict(self):
        return dict(gpus=[asdict(g) for g in self.gpus], encoders=[c.to_dict() for c in self.encoders],
                    inventory=self.inventory, errors=self.errors, fingerprint=self.fingerprint,
                    timestamp=self.timestamp, schema=self.schema)

    @classmethod
    def from_dict(cls, data):
        if data.get('schema') != SCHEMA:
            raise ValueError('Hardware cache schema changed')
        return cls(gpus=[GpuProfile(**g) for g in data['gpus']],
                   encoders=[EncoderCapabilities(**c) for c in data['encoders']],
                   **{k: data[k] for k in ('inventory', 'errors', 'fingerprint', 'timestamp', 'schema')})
