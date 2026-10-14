from app.core.ffprobe import Media, Cadence, rate


def sample_media(path):
    return Media(path, [dict(codec_type='video',codec_name='h264',pix_fmt='yuv420p',
        width=320,height=180,r_frame_rate='25/1',avg_frame_rate='25/1',duration='2',
        sample_aspect_ratio='1:1')], {'duration':'2'}, Cadence(rate('25'),samples=49))
