"""Actionable bilingual messages; raw diagnostics always remain in job Details."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class HardwareError:
    code: str
    message: str
    hint: str
    message_fa: str
    hint_fa: str

    def describe(self, language='en'):
        return (self.message_fa + ' — ' + self.hint_fa if language == 'fa' else self.message + ' — ' + self.hint)


RULES = [
    (r'MFX.*-9|MFX_ERR_NOT_FOUND|Error initializing.*MFX', 'mfx_session',
     'Intel QSV session could not start', 'Try DXVA2, update the Intel driver, and enable the iGPU in BIOS',
     'نشست QSV اینتل باز نشد', 'DXVA2 را امتحان کنید، درایور اینتل را به‌روز و گرافیک داخلی را در BIOS فعال کنید'),
    (r'Cannot load (?:nvcuda|libcuda)|CUDA_ERROR_NO_DEVICE|CUDA_ERROR_SYSTEM_DRIVER_MISMATCH', 'cuda_driver',
     'NVIDIA CUDA driver is unavailable', 'Install the NVIDIA display driver or select Intel / CPU',
     'درایور CUDA انویدیا در دسترس نیست', 'درایور انویدیا را نصب کنید یا اینتل / CPU را انتخاب کنید'),
    (r'too many.*sessions|session.*limit|OpenEncodeSessionEx.*(?:out of memory|10)|NV_ENC_ERR_OUT_OF_MEMORY', 'sessions',
     'Hardware encoder sessions are exhausted', 'Close recording/streaming apps and reduce parallel jobs',
     'ظرفیت نشست‌های انکودر پر شده است', 'برنامه‌های ضبط و استریم را ببندید و تعداد کارهای هم‌زمان را کم کنید'),
    (r'OpenEncodeSessionEx failed', 'nvenc_session',
     'NVIDIA could not open an encoding session', 'Update the driver, close recording apps, or use another GPU',
     'نشست تبدیل انویدیا باز نشد', 'درایور را به‌روز کنید، برنامه ضبط را ببندید یا GPU دیگری انتخاب کنید'),
    (r'No capable devices found|No NVENC capable devices', 'no_device',
     'No compatible hardware encoder was found', 'Check the selected GPU and driver; use another GPU or CPU',
     'انکودر سخت‌افزاری سازگار پیدا نشد', 'انتخاب GPU و درایور را بررسی یا از GPU دیگر / CPU استفاده کنید'),
    (r'AMF.*(?:DLL|library|runtime).*(?:not found|failed|load)|(?:amfrt64|amfrt32)', 'amf_driver',
     'AMD AMF runtime is missing', 'Install the AMD driver or select another GPU / CPU',
     'کتابخانه AMF پیدا نشد', 'درایور AMD را نصب کنید یا GPU دیگر / CPU را انتخاب کنید'),
    (r'out of memory|cannot allocate memory|not enough memory|insufficient.*memory', 'memory',
     'Not enough system or GPU memory', 'Use one job, keep Safe mode on, and close other GPU apps',
     'حافظه سیستم یا گرافیک کافی نیست', 'حالت امن و یک کار هم‌زمان را انتخاب و برنامه‌های دیگر را ببندید'),
    (r'Unknown encoder|Encoder .* not found', 'missing_encoder',
     'This FFmpeg build lacks the selected encoder', 'Select another encoder or choose a compatible FFmpeg build',
     'این نسخه FFmpeg انکودر انتخاب‌شده را ندارد', 'انکودر دیگر یا نسخه سازگار FFmpeg را انتخاب کنید'),
    (r'Tool timed out', 'timeout', 'Hardware test exceeded 10 seconds',
     'The test was stopped; try a faster preset, another device, or update the driver',
     'آزمون سخت‌افزار بیش از ۱۰ ثانیه طول کشید', 'آزمون متوقف شد؛ حالت سریع‌تر، دستگاه دیگر یا درایور تازه را امتحان کنید'),
]


def classify_error(stderr):
    for pattern, *fields in RULES:
        if re.search(pattern, str(stderr), re.I | re.S):
            return HardwareError(*fields)
    return HardwareError('hardware_failure', 'The selected pipeline failed',
                         'See Details for the exact command; try another GPU or CPU',
                         'مسیر تبدیل انتخاب‌شده ناموفق بود', 'فرمان دقیق در جزئیات است؛ GPU دیگر یا CPU را امتحان کنید')
