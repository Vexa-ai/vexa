import os
from typing import Literal
#The HF_HOME environment variable configures the local storage location for the Hugging Face library. It dictates where the library stores data, including the user's token and the cache for downloaded models and datasets
os.environ['HF_HOME'] = '.'
from faster_whisper import WhisperModel

model_size:Literal["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3", "large", "distil-small", "distil-medium", "distil-large"] = "tiny"
device:Literal["cpu", "cuda"] = "cpu" #"cuda"
compute_type:Literal["int8", "float16", "default"] = "default" #"float16"

model = WhisperModel(model_size, device=device, compute_type=compute_type)

# segments, _ = model.transcribe("audio.mp3", language="en", task="transcribe")

# for segment in segments:
#     print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))