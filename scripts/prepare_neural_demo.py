"""Synthesize whole scene paragraphs; retain provider sentence timing for captions.

Only the public narration is sent to Edge TTS. Requires edge-tts in an isolated
media environment; this is not a dependency of the application.
"""
import argparse
import asyncio
import json
from pathlib import Path

import edge_tts


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--proxy")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    timeline = []
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    for scene in spec:
        audio = args.root / f"{scene['id']}.mp3"
        metadata = args.root / f"{scene['id']}-speech.json"
        if not metadata.exists():
            if audio.exists():
                raise RuntimeError(f"Incomplete audio must be inspected, not overwritten: {audio}")
            captions = []
            speech = edge_tts.Communicate(scene["text"], "zh-CN-XiaoxiaoNeural",
                                          rate="-4%", proxy=args.proxy)
            with audio.open("wb") as stream:
                async for chunk in speech.stream():
                    if chunk["type"] == "audio":
                        stream.write(chunk["data"])
                    elif chunk["type"] == "SentenceBoundary":
                        captions.append({"start": chunk["offset"] / 10_000_000,
                                         "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                                         "text": chunk["text"]})
            if not captions or audio.stat().st_size == 0:
                raise RuntimeError("No audio or sentence timestamps returned")
            metadata.write_text(json.dumps(captions, ensure_ascii=False), encoding="utf-8")
        captions = json.loads(metadata.read_text(encoding="utf-8"))
        # The service can overlap adjacent sentence timestamps by ~50 ms.
        for index in range(len(captions) - 1):
            captions[index]["end"] = min(captions[index]["end"], captions[index + 1]["start"])
        timeline.append({"id": scene["id"], "title": scene["title"], "audio": str(audio.resolve()),
                         "duration": max(scene["minimum_seconds"], captions[-1]["end"] + 1),
                         "captions": captions})
        print(scene["id"], round(timeline[-1]["duration"], 2), flush=True)
    (args.root / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
