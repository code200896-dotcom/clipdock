"""Real yt-dlp / FFmpeg pipeline against a generated local video fixture.

Only this test disables the outbound-network guard to reach its own loopback
fixture server. Production workers always enable that guard.
"""
import contextlib
import functools
import io
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import worker


class PipelineTest(unittest.TestCase):
    def test_real_video_inspection_download_and_playability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=green:s=160x90:r=10',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','1','-c:v','libx264','-c:a','aac',
                            '-pix_fmt','yuv420p',str(root / 'sample.mp4')],check=True)
            class Quiet(SimpleHTTPRequestHandler):
                def log_message(self, *_): pass
            server = ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=temp))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/sample.mp4'
                # URL standard-port restriction also remains active in production.
                with patch.object(worker,'protect_network'), patch.object(worker,'public_url',lambda url:url):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        worker.run({'mode':'inspect','url':url})
                    events = [json.loads(l) for l in output.getvalue().splitlines()]
                    self.assertEqual(events[-1]['kind'],'info')
                    target = root / 'output'
                    target.mkdir()
                    with contextlib.redirect_stdout(io.StringIO()):
                        worker.run({'mode':'download','url':url,'height':720,'directory':str(target)})
                file = next(target.glob('*.mp4'))
                self.assertGreater(file.stat().st_size,0)
                probe = subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type','-of','json',str(file)],
                                       check=True,capture_output=True,text=True)
                self.assertEqual({s['codec_type'] for s in json.loads(probe.stdout)['streams']},{'audio','video'})
                # Also exercise separate audio/video streams and real FFmpeg merging.
                for kind, filename, args in [('video','silent.mp4',['-an','-c:v','copy']),
                                              ('audio','audio.m4a',['-vn','-c:a','copy'])]:
                    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(root / 'sample.mp4'),
                                    *args,str(root / filename)],check=True)
                base = f'http://127.0.0.1:{server.server_port}'
                split = {'id':'split','title':'Separate streams','extractor':'generic','extractor_key':'Generic',
                         'webpage_url':url,'formats':[
                            {'format_id':'v','url':base+'/silent.mp4','ext':'mp4','protocol':'http','vcodec':'h264','acodec':'none','height':90,'width':160},
                            {'format_id':'a','url':base+'/audio.m4a','ext':'m4a','protocol':'http','vcodec':'none','acodec':'aac'}]}
                merged = root / 'merged'
                merged.mkdir()
                from yt_dlp import YoutubeDL
                with patch.object(worker,'protect_network'), patch.object(worker,'public_url',lambda url:url), \
                     patch.object(YoutubeDL,'extract_info',return_value=split), contextlib.redirect_stdout(io.StringIO()):
                    worker.run({'mode':'download','url':url,'height':720,'directory':str(merged)})
                final = [p for p in merged.iterdir() if p.suffix in ('.mp4','.mkv','.webm')]
                self.assertEqual(len(final),1, str(list(merged.iterdir())))
                probe = subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type','-of','json',str(final[0])],
                                       check=True,capture_output=True,text=True)
                self.assertEqual({s['codec_type'] for s in json.loads(probe.stdout)['streams']},{'audio','video'})
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__': unittest.main()
