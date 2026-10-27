"""Assemble accepted real captures, Mandarin audio and burned-in/SRT captions."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'backend/tmp/subtitled-demo-2026-10-26'
FFMPEG = REPO / 'backend/tmp/recording-media-deps/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'


def timestamp(seconds, srt=False):
    milliseconds = round(seconds * 1000)
    h, remainder = divmod(milliseconds, 3600000)
    m, remainder = divmod(remainder, 60000)
    s, ms = divmod(remainder, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}' if srt else f'{h}:{m:02}:{s:02}.{ms // 10:02}'


def run(args, log):
    with log.open('w', encoding='utf-8') as stream:
        subprocess.run([str(FFMPEG), '-hide_banner', '-nostdin', '-n', *args],
                       cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)


def wrap_caption(text):
    lines, current = [], ''
    # Keep English identifiers intact and avoid punctuation at the next line's start.
    for token in re.findall(r'[A-Za-z0-9][A-Za-z0-9.\-/]*|.', text):
        if len(current) + len(token) > 46 and token not in '，。；：、！？,.!?;:':
            lines.append(current.rstrip())
            current = ''
        current += token
    if current:
        lines.append(current.strip())
    return '\\N'.join(lines)


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--label', default='review-v1')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--date', default='2026-10-26')
    args = parser.parse_args()
    assert args.label.replace('-', '').isalnum()
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', args.date)
    ROOT = args.root.resolve()
    assert ROOT.is_relative_to(REPO / 'backend/tmp')
    timeline = json.loads((ROOT / 'timeline.json').read_text(encoding='utf-8'))
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    output = ROOT / args.label
    output.mkdir()  # Never overwrite a previous encoded take.
    subtitles, chapters, offset, segments = [], [], 0.0, []
    for scene in timeline:
        report_file = ROOT / selection[scene['id']] / scene['id'] / 'capture.json'
        report = json.loads(report_file.read_text(encoding='utf-8'))
        assert report['status'] == 'captured_pending_media_review' and not report['errors']
        duration = scene['duration']
        # Trim initial navigation only when explicitly measured. Never cut a
        # required successful action to force a convenient narration length.
        start = min(report.get('ui_ready_seconds', 0), max(0, report['action_seconds'] - duration + 2))
        duration = max(duration, report['action_seconds'] - start + 1.5)
        chapters.append({'scene': scene['id'], 'start': offset, 'duration': duration,
                         'capture': str(report_file), 'trim_initial_loading_seconds': start})
        ass = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Microsoft YaHei,30,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00101010,0,0,0,0,100,100,0,0,1,0,0,2,40,40,14,1
Style: Chapter,Microsoft YaHei,22,&H00C3D5CA,&H00FFFFFF,&H00101010,&H00101010,0,0,0,0,100,100,0,0,1,0,0,7,24,24,967,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
        ass += f"Dialogue: 0,0:00:00.00,{timestamp(duration)},Chapter,,0,0,0,,{scene['title']} · 本地审阅版\n"
        for c in scene['captions']:
            text = c['text']
            wrapped = wrap_caption(text)
            ass += f"Dialogue: 0,{timestamp(c['start'])},{timestamp(c['end'])},Caption,,0,0,0,,{wrapped}\n"
            subtitles.append(f"{len(subtitles)+1}\n{timestamp(offset+c['start'],True)} --> {timestamp(offset+c['end'],True)}\n{text}\n")
        ass_path = output / (scene['id'] + '.ass')
        ass_path.write_text(ass, encoding='utf-8-sig')
        encoded = output / (scene['id'] + '.mp4')
        filters = ("tpad=stop_mode=clone:stop_duration=600,"
                   f"pad=1920:1080:0:0:color=0x101510,setsar=1,ass={args.label}/{scene['id']}.ass")
        run(['-ss',str(start),'-i',report['video'],'-i',scene['audio'],
             '-vf',filters,'-af',f"atempo={scene.get('audio_speed',1)},apad",'-t',str(duration),
             '-r','25','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','160k','-movflags','+faststart',str(encoded)], output/(scene['id']+'.log'))
        segments.append(encoded)
        offset += duration
        print(scene['id'], round(duration, 2), flush=True)
    concat = output / 'concat.txt'
    concat.write_text(''.join(f"file '{p.as_posix()}'\n" for p in segments), encoding='utf-8')
    final = REPO / 'output/video' / f'paperpilot-subtitled-{args.label}-{args.date}.mp4'
    run(['-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(final)],output/'concat.log')
    srt = final.with_suffix('.srt')
    assert not srt.exists()
    srt.write_text('\n'.join(subtitles),encoding='utf-8-sig')
    run(['-v','error','-i',str(final),'-f','null','-'],output/'full-decode.log')
    report = {'status':'encoded_decode_passed_pending_visual_audio_review','duration_seconds':offset,
              'target_duration_passed':300 <= offset <= 480,'width':1920,'height':1080,
              'synthetic_mandarin_voice':True,'burned_subtitles':True,'srt':str(srt),
              'chapters':chapters,'video':str(final),'sha256':hashlib.sha256(final.read_bytes()).hexdigest()}
    (output/'media-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
