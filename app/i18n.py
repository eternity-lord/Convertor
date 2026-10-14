"""All user-facing translations go through this module; runtime UI rebuild enables RTL."""
LANGUAGE = 'en'
FA = {
    'Dashboard': 'داشبورد', 'MP4 Profile Fixer': 'اصلاح فایل MP4',
    'HQX Converter': 'تبدیل HQX', 'QSV Advanced': 'تنظیمات پیشرفته QSV',
    'System Tweaks': 'بهینه‌سازی سیستم', 'Logs': 'گزارش‌ها', 'Settings': 'تنظیمات',
    'Support': 'پشتیبانی', 'Encoding queue': 'صف تبدیل', 'Welcome,': 'خوش آمدی،',
    'Your studio. Faster, safer, beautifully in sync.': 'استودیوی شما؛ سریع‌تر، سازگارتر و هماهنگ.',
    'Total converted today': 'مجموع تبدیل امروز', 'TV-Compatibility Check': 'بررسی سازگاری تلویزیون',
    'Decisions powered by precision': 'تصمیم‌گیری با دقت',
    'One clean frame rate. Every screen.': 'یک نرخ فریم ثابت، برای همهٔ نمایشگرها.',
    'Analyze & Fix MP4s': 'بررسی و اصلاح MP4', 'Live queue': 'صف زنده',
    'Studio performance': 'عملکرد استودیو', 'Encoding performance': 'عملکرد تبدیل',
    'Avg speed': 'سرعت میانگین', 'GPU load': 'بار گرافیک', 'CPU load': 'بار پردازنده',
    'Space saved': 'فضای ذخیره‌شده', 'Browse': 'انتخاب پوشه', 'Add folder': 'افزودن پوشه',
    'Analyze files': 'بررسی فایل‌ها', 'Start Fix': 'شروع اصلاح', 'Stop all': 'توقف همه',
    'Start Watching': 'شروع پایش', 'Stop Watching': 'توقف پایش', 'Frame Rate': 'نرخ فریم',
    'Auto': 'خودکار', 'Custom': 'سفارشی', 'Folder': 'پوشه', 'TV preset': 'حالت تلویزیون',
    'Output': 'خروجی', 'Audio': 'صدا', 'Video': 'ویدیو', 'Profile': 'پروفایل',
    'Level': 'سطح', 'Bitrate (Mbps)': 'بیت‌ریت (مگابیت)', 'Maxrate (Mbps)': 'حداکثر بیت‌ریت',
    'Buffer (Mbps)': 'بافر (مگابیت)', 'Rate control': 'کنترل بیت‌ریت', 'Quality': 'کیفیت',
    'Conversion method': 'روش تبدیل', 'Resolution cap': 'محدودیت رزولوشن', 'FPS cap': 'سقف نرخ فریم',
    'Deinterlace': 'حذف خطوط درهم‌بافته', 'Double rate': 'نرخ دوبرابر',
    'Test with TV simulation (verify only)': 'آزمایش سازگاری تلویزیون (بدون تبدیل)',
    'Skip already TV-safe files': 'رد کردن فایل‌های سازگار',
    'Delete source only after verified success': 'حذف فایل اصلی فقط پس از تأیید موفقیت',
    'Suffix': 'پسوند نام', 'Overwrite policy': 'سیاست جایگزینی', 'FAT32 guard': 'محافظ FAT32',
    'Save settings': 'ذخیره تنظیمات', 'Search files / filter logs': 'جست‌وجوی فایل و گزارش',
    'Safe mode': 'حالت ایمن', 'Performance mode': 'حالت کارایی',
    'High priority (opt-in)': 'اولویت بالا (اختیاری)', 'Warning': 'هشدار',
    'Unverified': 'تأییدنشده', 'Blocked': 'مسدود', 'Skip analysis': 'رد کردن تحلیل',
    'Reading streams…': 'خواندن مشخصات…', 'Checking frame timing…': 'بررسی زمان‌بندی فریم…',
    'Clear finished': 'پاک کردن تمام‌شده‌ها',
    'All': 'همه', 'Info': 'اطلاعات', 'Warn': 'هشدار', 'Error': 'خطا', 'Details': 'جزئیات',
    'Copy': 'کپی', 'Clear': 'پاک کردن', 'Export': 'خروجی گزارش', 'Auto-scroll': 'پیمایش خودکار',
    'Pause': 'مکث', 'Resume': 'ادامه', 'Stop current': 'توقف فایل جاری', 'Skip file': 'رد کردن فایل',
    'Open output folder': 'باز کردن پوشه خروجی', 'Always on top': 'همیشه روی پنجره‌ها',
    'Auto-close when finished': 'بستن پس از اتمام', 'When finished': 'پس از اتمام',
    'Do nothing': 'هیچ کاری نکن', 'Sleep': 'خواب', 'Shut down': 'خاموش کردن',
    'Restart as administrator': 'اجرای مجدد با دسترسی مدیر', 'Show monitor': 'نمایش مانیتور',
    'Apply selected tweaks': 'اعمال بهینه‌سازی‌های انتخابی', 'Restore originals': 'بازگردانی تنظیمات اصلی',
    'Waiting': 'در انتظار', 'Analyzing': 'در حال بررسی', 'Encoding': 'در حال تبدیل',
    'Verifying': 'در حال تأیید', 'Paused': 'متوقف موقت', 'Failed': 'ناموفق', 'Skipped': 'رد شده',
    'Done': 'تمام‌شده', 'TV-Safe ✓': 'سازگار با تلویزیون ✓', 'Remove': 'حذف از صف',
    'Move to top': 'انتقال به ابتدای صف', 'Retry': 'تلاش مجدد', 'Open folder': 'باز کردن پوشه',
    'Show ffmpeg command': 'نمایش فرمان FFmpeg', 'No files yet': 'هنوز فایلی اضافه نشده',
    'Found': 'یافت‌شده', 'Elapsed': 'سپری‌شده', 'Remaining': 'باقی‌مانده', 'Finish': 'پایان',
    'Queue size': 'حجم صف', 'Ready': 'آماده', 'Encoder preset': 'پیش‌تنظیم انکودر',
}


def set_language(value):
    global LANGUAGE
    LANGUAGE = 'fa' if value == 'fa' else 'en'


def tr(text):
    return FA.get(text, text) if LANGUAGE == 'fa' else text
