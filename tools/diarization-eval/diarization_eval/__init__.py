"""Public entry points for fixture generation, RTTM loading, and DER evaluation."""


def main(argv=None, *, candidates_dir=None):
    from .evaluate import main as entry
    return entry(argv, candidates_dir=candidates_dir)


def read_rttm(path, file_name):
    from .evaluate import read_rttm as read
    return read(path, file_name)


def score(reference, hypothesis, *, collar=0.0, skip_overlap=False):
    """Return pyannote's detailed single-file metric, without rounding."""
    from pyannote.metrics.diarization import DiarizationErrorRate
    return DiarizationErrorRate(collar=collar, skip_overlap=skip_overlap)(
        reference, hypothesis, detailed=True
    )


def generate_fixtures(out):
    from .synth import generate_fixtures as generate
    return generate(out)


__all__ = ["main", "read_rttm", "score", "generate_fixtures"]
