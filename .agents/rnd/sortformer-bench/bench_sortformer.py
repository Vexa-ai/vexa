#!/usr/bin/env python3
"""CPU benchmark for NVIDIA Streaming Sortformer (diar_streaming_sortformer_4spk-v2).
Forces CPU, caps threads to simulate the bot, measures RTF + peak RSS, dumps
diarized segments for scoring vs Deepgram. RTF figures on the model card are
GPU; this measures the unknown: CPU real-time feasibility in the bot.
"""
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""   # hard-disable the host 4090 — CPU only
import sys, time, json, resource, argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--latency", default="low", choices=["ull", "low", "high", "vhigh"])
    a = ap.parse_args()

    os.environ["OMP_NUM_THREADS"] = str(a.threads)
    os.environ["MKL_NUM_THREADS"] = str(a.threads)
    import torch
    torch.set_num_threads(a.threads)

    import soundfile as sf
    info = sf.info(a.wav); dur = info.frames / info.samplerate

    from nemo.collections.asr.models import SortformerEncLabelModel
    t0 = time.time()
    model_id = os.environ.get("SORTFORMER_MODEL", "nvidia/diar_streaming_sortformer_4spk-v2.1")
    m = SortformerEncLabelModel.from_pretrained(model_id, map_location="cpu")
    m.eval()
    load_s = time.time() - t0

    # (chunk_len, chunk_right_context, fifo_len, spkcache_update_period, spkcache_len)
    cfgs = {
        "ull":   (3,   1,  188, 144, 188),  # 0.32s latency, GPU RTF 0.180
        "low":   (6,   7,  188, 144, 188),  # 1.04s latency, GPU RTF 0.093
        "high":  (124, 1,  124, 124, 188),  # 10.0s latency, GPU RTF 0.005
        "vhigh": (340, 40, 40,  300, 188),  # 30.4s latency, GPU RTF 0.002
    }
    sm = m.sortformer_modules
    cl, crc, fl, sup, scl = cfgs[a.latency]
    sm.chunk_len = cl; sm.chunk_right_context = crc; sm.fifo_len = fl
    sm.spkcache_update_period = sup; sm.spkcache_len = scl
    try:
        sm._check_streaming_parameters()
    except Exception as e:
        print(f"[warn] _check_streaming_parameters: {e}")

    # warm-up on 5s so the timed run reflects steady-state, not lazy init
    import numpy as np
    try:
        m.diarize(audio=[np.zeros(16000 * 5, dtype=np.float32)], batch_size=1)
    except Exception as e:
        print(f"[warn] warmup skipped: {e}")

    t1 = time.time()
    segs = m.diarize(audio=[a.wav], batch_size=1)
    wall = time.time() - t1
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB->MB on Linux

    out = [str(s) for s in segs[0]]
    json.dump({"wav": a.wav, "latency": a.latency, "threads": a.threads, "durationS": round(dur, 1),
               "wall_s": round(wall, 2), "rtf": round(wall / dur, 4), "load_s": round(load_s, 1),
               "peak_rss_mb": round(rss_mb), "n_segments": len(out), "segments": out},
              open(a.out, "w"), indent=2)
    print(f"RESULT latency={a.latency} threads={a.threads} dur={dur:.0f}s "
          f"wall={wall:.1f}s RTF={wall/dur:.3f} peakRSS={rss_mb:.0f}MB load={load_s:.1f}s segs={len(out)}")

main()
