"""Bounded, serializable enhancement options. Off is a real no-op for SDR."""
from dataclasses import dataclass, asdict, fields
import math

HONEST_TOOLTIP = ('Improves perceived quality (cleaner, sharper, better color). '
                  'It cannot create real detail that was never recorded.')
LEVELS = ('Off', 'Low', 'Balanced', 'Excellent', 'Custom')


@dataclass(frozen=True)
class EnhanceSettings:
    level: str = 'Off'
    denoise: float = 1.5
    denoiser: str = 'Auto'
    deband: float = .015
    sharpen: float = .25
    gamma: float = 1.005
    contrast: float = .995
    saturation: float = 1.0
    processing_10bit: bool = True
    color: str = 'Auto'
    input_range: str = 'Auto'
    upscale: str = 'Keep'
    scaler: str = 'lanczos'
    loudnorm: bool = False
    audio_cleanup: bool = False
    ai_enabled: bool = False
    ai_executable: str = ''
    ai_scale: int = 2

    def to_dict(self):
        return asdict(self)


PRESETS = {
    'Off': {},
    'Low': dict(denoise=1.0, deband=0, sharpen=.15, gamma=1, contrast=1, saturation=1, processing_10bit=False),
    'Balanced': dict(denoise=1.5, deband=.015, sharpen=.25, gamma=1.005, contrast=.995, saturation=1, processing_10bit=True),
    'Excellent': dict(denoise=2.0, deband=.02, sharpen=.35, gamma=1.01, contrast=.99, saturation=1, processing_10bit=True),
}


def settings_from(value=None):
    if isinstance(value, EnhanceSettings):
        settings = value
    else:
        value = value or {}
        allowed = {f.name for f in fields(EnhanceSettings)}
        settings = EnhanceSettings(**{k: v for k, v in value.items() if k in allowed})
    if settings.level not in LEVELS:
        raise ValueError('Unknown enhancement level; choose Off / Low / Balanced / Excellent / Custom')
    for key, minimum, maximum in [('denoise', 0, 4), ('deband', 0, .04), ('sharpen', 0, .6),
                                  ('gamma', 1, 1.02), ('contrast', .98, 1), ('saturation', .95, 1)]:
        v = getattr(settings, key)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not minimum <= v <= maximum:
            raise ValueError(f'Enhance {key}: use a finite value between {minimum} and {maximum}')
    for key in ('processing_10bit', 'loudnorm', 'audio_cleanup', 'ai_enabled'):
        if not isinstance(getattr(settings, key), bool):
            raise ValueError(f'Enhance {key}: use an explicit boolean')
    if not isinstance(settings.ai_executable, str):
        raise ValueError('External executable path must be text')
    if settings.denoiser not in ('Auto', 'hqdn3d', 'nlmeans'):
        raise ValueError('Unknown denoiser')
    if settings.color not in ('Auto', 'bt709', 'bt601', 'bt2020'):
        raise ValueError('Unknown color matrix')
    if settings.input_range not in ('Auto', 'limited', 'full'):
        raise ValueError('Unknown source color range')
    if settings.upscale not in ('Keep', '1080p', '4K') or settings.scaler not in ('lanczos', 'spline', 'bicubic'):
        raise ValueError('Choose a supported explicit scaling option')
    if settings.ai_scale not in (2, 4):
        raise ValueError('External AI scale must be 2 or 4')
    return settings


def effective(value=None):
    settings = settings_from(value)
    values = settings.to_dict()
    # Independent audio/upscale/color choices survive level changes; effect strengths are preset-owned.
    values.update(PRESETS.get(settings.level, {}))
    return EnhanceSettings(**values)


def enabled(options):
    return settings_from(getattr(options, 'enhance', None)).level != 'Off'
