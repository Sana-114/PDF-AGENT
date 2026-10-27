"""Inspect an encoded demo without changing its audio, captions or footage."""
import argparse
import json
import re
import subprocess
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args()
report_file = args.report.resolve()
assert report_file.is_relative_to(repo / 'backend/tmp')
report = json.loads(report_file.read_text(encoding='utf-8'))
output = report_file.parent / 'media-check'
output.mkdir()  # Do not overwrite previous evidence.
ffmpeg = repo / 'backend/tmp/recording-media-deps/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
video = Path(report['video'])
assert video.is_relative_to(repo / 'output/video')
frames = []
for chapter in report['chapters']:
    target = output / (chapter['scene'] + '.png')
    time = chapter['start'] + min(22, chapter['duration'] / 2)
    subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-n', '-ss', str(time),
                    '-i', str(video), '-frames:v', '1', str(target)], check=True)
    frames.append({'scene': chapter['scene'], 'time': time, 'file': str(target)})
with (output / 'audio.log').open('w', encoding='utf-8') as log:
    subprocess.run([str(ffmpeg), '-nostdin', '-hide_banner', '-i', str(video),
                    '-af', 'volumedetect', '-vn', '-sn', '-f', 'null', '-'],
                   stdout=log, stderr=subprocess.STDOUT, check=True)
log_text = (output / 'audio.log').read_text(encoding='utf-8')
mean = float(re.search(r'mean_volume: ([\d.-]+) dB', log_text)[1])
peak = float(re.search(r'max_volume: ([\d.-]+) dB', log_text)[1])
assert -45 < mean < -5 and peak <= 0
srt_text = Path(report['srt']).read_text(encoding='utf-8-sig')
count = len(re.findall(r'^\d+\s*$', srt_text, re.M))
assert count >= 30
summary = {'frames': frames, 'subtitle_entries': count, 'mean_db': mean, 'peak_db': peak,
           'full_audio_decoded': True, 'human_full_listening_review': False,
           'status': 'technical_checks_passed_pending_visual_review'}
(output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'subtitle_entries': count, 'mean_db': mean, 'peak_db': peak, 'frames': len(frames)}))
