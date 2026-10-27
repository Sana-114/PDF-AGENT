"""Prepare local narration, precisely aligned captions and an explicit status card."""
import argparse
import json
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=REPO / 'backend/tmp/subtitled-demo-2026-10-26')
parser.add_argument('--spec', type=Path, default=REPO / 'scripts/demo_narration_20261026.json')
args = parser.parse_args()
ROOT = args.root.resolve()
assert ROOT.is_relative_to(REPO / 'backend/tmp')
assert not (ROOT / 'timeline.json').exists()
spec = json.loads(args.spec.read_text(encoding='utf-8'))
result = []
for scene in spec:
    entries, cursor, frames, params = [], 1.0, [], None
    for index, line in enumerate(scene['lines']):
        with wave.open(str(ROOT / 'voice' / f"{scene['id']}-{index}.wav"), 'rb') as source:
            current = source.getparams()
            if params is None:
                params = current
                frames.append(bytes(round(current.framerate * cursor) * current.nchannels * current.sampwidth))
            assert current[:3] == params[:3]
            samples = source.readframes(current.nframes)
            seconds = current.nframes / current.framerate
        frames.append(samples)
        entries.append({'start': cursor, 'end': cursor + seconds, 'text': line})
        frames.append(bytes(round(current.framerate * .6) * current.nchannels * current.sampwidth))
        cursor += seconds + .6
    duration = max(34, cursor + 1.5)
    frames.append(bytes(round(params.framerate * (duration - cursor)) * params.nchannels * params.sampwidth))
    audio = ROOT / f"{scene['id']}.wav"
    assert not audio.exists(), 'Never replace an earlier narration take'
    with wave.open(str(audio), 'wb') as target:
        target.setparams(params)
        target.writeframes(b''.join(frames))
    result.append({**scene, 'duration': duration, 'captions': entries, 'audio': str(audio)})
(ROOT / 'timeline.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
(ROOT / 'status-card.html').write_text('''<!doctype html><meta charset="utf-8"><title>演示说明</title>
<style>body{margin:0;background:#f4f3ef;color:#202620;font-family:"Microsoft YaHei",sans-serif;padding:85px 150px}h1{font-size:48px}p,li{font-size:29px;line-height:1.9}.tag{font-size:22px;color:#637063}section{border-top:2px solid #bec4bb;margin-top:35px;padding-top:20px}small{font-size:22px}</style>
<div class="tag">PaperPilot · 本地演示</div><h1>从阅读论文，到整理研究材料</h1>
<p>原文阅读与问答 · 中英对照<br>引用图谱 · 研究方向 · 数据作图</p>
<section><p>演示使用公开论文、已保存的问答与译文。<br>CSV 为合成演示数据。</p></section>
<section><p>提交前：补录上传与在线检索，完成译文审校和公网部署。</p></section>
<small>项目代码　github.com/Sana-114/PDF-AGENT<br>谢谢观看</small>''', encoding='utf-8')
print(json.dumps({'scenes': len(result), 'seconds': sum(s['duration'] for s in result)}, ensure_ascii=False))
