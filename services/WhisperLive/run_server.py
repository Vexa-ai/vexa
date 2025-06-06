import argparse
import os

from whisper_live import settings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p',
                        type=int,
                        # port=9090, #GPU version
                        default=9092, #CPU version
                        help="Websocket port to run the server on.")
    parser.add_argument('--backend', '-b',
                        type=str,
                        default='faster_whisper',
                        help='Backends from ["tensorrt", "faster_whisper"]')
    parser.add_argument('--faster_whisper_custom_model_path', '-fw',
                        type=str, default=None,
                        help="Custom Faster Whisper Model")
    parser.add_argument('--trt_model_path', '-trt',
                        type=str,
                        default=None,
                        help='Whisper TensorRT model path')
    parser.add_argument('--trt_multilingual', '-m',
                        action="store_true",
                        help='Boolean only for TensorRT model. True if multilingual.')
    parser.add_argument('--omp_num_threads', '-omp',
                        type=int,
                        default=1,
                        help="Number of threads to use for OpenMP")
    parser.add_argument('--no_single_model', '-nsm',
                        action='store_true',
                        help='Set this if every connection should instantiate its own model. Only relevant for custom model, passed using -trt or -fw.')
    
    # Audio buffer settings
    parser.add_argument('--max_buffer_s', type=float, default=settings.MAX_BUFFER_S)
    parser.add_argument('--discard_buffer_s', type=float, default=settings.DISCARD_BUFFER_S)

    # Forced audio clipping settings
    parser.add_argument('--clip_if_no_segment_s', type=float, default=settings.CLIP_IF_NO_SEGMENT_S)
    parser.add_argument('--clip_retain_s', type=float, default=settings.CLIP_RETAIN_S)

    # Minimum audio for transcription
    parser.add_argument('--min_audio_s', type=float, default=settings.MIN_AUDIO_S)

    # VAD settings
    parser.add_argument('--vad_onset', type=float, default=settings.VAD_ONSET)
    parser.add_argument('--vad_no_speech_thresh', type=float, default=settings.VAD_NO_SPEECH_THRESH)

    # Transcription output management
    parser.add_argument('--same_output_threshold', type=int, default=settings.SAME_OUTPUT_THRESHOLD)
    parser.add_argument('--show_prev_out_thresh_s', type=float, default=settings.SHOW_PREV_OUT_THRESH_S)
    parser.add_argument('--add_pause_thresh_s', type=float, default=settings.ADD_PAUSE_THRESH_S)

    args = parser.parse_args()

    if args.backend == "tensorrt":
        if args.trt_model_path is None:
            raise ValueError("Please Provide a valid tensorrt model path")

    if "OMP_NUM_THREADS" not in os.environ:
        os.environ["OMP_NUM_THREADS"] = str(args.omp_num_threads)

    from whisper_live.server import TranscriptionServer
    server = TranscriptionServer()
    server.run(
        "0.0.0.0",
        port=args.port,
        backend=args.backend,
        faster_whisper_custom_model_path=args.faster_whisper_custom_model_path,
        whisper_tensorrt_path=args.trt_model_path,
        trt_multilingual=args.trt_multilingual,
        single_model=not args.no_single_model,
        server_options={
            "max_buffer_s": args.max_buffer_s,
            "discard_buffer_s": args.discard_buffer_s,
            "clip_if_no_segment_s": args.clip_if_no_segment_s,
            "clip_retain_s": args.clip_retain_s,
            "min_audio_s": args.min_audio_s,
            "vad_onset": args.vad_onset,
            "vad_no_speech_thresh": args.vad_no_speech_thresh,
            "same_output_threshold": args.same_output_threshold,
            "show_prev_out_thresh_s": args.show_prev_out_thresh_s,
            "add_pause_thresh_s": args.add_pause_thresh_s,
        }
    )
