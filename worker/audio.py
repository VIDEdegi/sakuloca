"""Bounded WAV/MP3/FLAC windows; sources are read only.

PCM WAV keeps the original byte-exact path. Compressed audio is decoded
sequentially with at most one window plus one small block in memory.
"""
from dataclasses import dataclass
import io
from pathlib import Path
import wave

EXTENSIONS = {'.wav', '.mp3', '.flac'}
WINDOW_MS = 30_000
OVERLAP_MS = 5_000
MAX_DURATION_MS = 24 * 60 * 60 * 1000
PREVIEW_CONTEXT_MS = 5_000
MAX_PREVIEW_MS = WINDOW_MS + PREVIEW_CONTEXT_MS * 2
PREPROCESS = 'pcm16-native-rate-miniaudio1.71-window30000-overlap5000-skip-digital-zero-overflow-error-v2'


@dataclass(frozen=True)
class WaveInfo:
    frames: int
    rate: int
    channels: int
    width: int

    @property
    def duration_ms(self):
        return (self.frames * 1000 + self.rate - 1) // self.rate

    def windows(self):
        length = self.rate * WINDOW_MS // 1000
        step = self.rate * (WINDOW_MS - OVERLAP_MS) // 1000
        start = 0
        while start < self.frames:
            end = min(start + length, self.frames)
            yield start, end, start * 1000 // self.rate, (end * 1000 + self.rate - 1) // self.rate
            if end == self.frames:
                break
            start += step


def inspect_wave(path: Path):
    if path.suffix.lower() in ('.mp3', '.flac'):
        import miniaudio
        try:
            decoded = miniaudio.get_file_info(str(path))
            info = WaveInfo(decoded.num_frames, decoded.sample_rate, decoded.nchannels, 2)
            _validate(info)
            return info
        except miniaudio.DecodeError as exc:
            raise ValueError('音声を読み込めません。MP3またはFLACの内容を確認してください') from exc
    try:
        with wave.open(str(path), 'rb') as source:
            info = WaveInfo(source.getnframes(), source.getframerate(),
                            source.getnchannels(), source.getsampwidth())
            if source.getcomptype() != 'NONE' or info.width != 2:
                raise ValueError('音声は非圧縮PCM 16bitのWAVに対応しています')
            _validate(info)
            return info
    except (wave.Error, EOFError) as exc:
        raise ValueError('WAVを読み込めません。非圧縮PCM 16bitのWAVを選択してください') from exc


def _validate(info):
    if info.channels not in (1, 2) or not 8000 <= info.rate <= 96000:
        raise ValueError('音声はmono/stereo、8〜96kHzに対応しています')
    if info.frames <= 0:
        raise ValueError('音声ファイルが空です')
    if info.duration_ms > MAX_DURATION_MS:
        raise ValueError('音声は1ファイル24時間まで対応しています')


def _wave_bytes(info, pcm, frames):
    if len(pcm) != frames * info.channels * info.width:
        raise ValueError('音声データが途中で切れています')
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setparams((info.channels, info.width, info.rate, 0, 'NONE', 'not compressed'))
        target.writeframes(pcm)
    # Only exact digital zero is skipped. Quiet sounds remain searchable.
    return output.getvalue(), not any(pcm)


class WindowReader:
    def __init__(self, path: Path, info=None):
        self.path = path
        self.info = info or inspect_wave(path)
        self.stream = None
        self.buffer = bytearray()
        self.start = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.buffer = bytearray()

    def read(self, start_frame, end_frame, *, max_ms=WINDOW_MS):
        info = self.info
        if max_ms not in (WINDOW_MS, MAX_PREVIEW_MS) or not 0 <= start_frame < end_frame <= info.frames or end_frame - start_frame > info.rate * max_ms // 1000:
            raise ValueError('音声区間が範囲外です')
        if self.path.suffix.lower() == '.wav':
            with wave.open(str(self.path), 'rb') as source:
                source.setpos(start_frame)
                pcm = source.readframes(end_frame - start_frame)
        else:
            import miniaudio
            stride = info.channels * info.width
            try:
                if self.stream is None or start_frame < self.start or start_frame > self.start + len(self.buffer) // stride:
                    self.close()
                    decoder = miniaudio.mp3_stream_file if self.path.suffix.lower() == '.mp3' else miniaudio.flac_stream_file
                    self.stream = decoder(str(self.path), frames_to_read=4096, seek_frame=start_frame)
                    self.start = start_frame
                del self.buffer[:(start_frame - self.start) * stride]
                self.start = start_frame
                needed = (end_frame - start_frame) * stride
                while len(self.buffer) < needed:
                    block = next(self.stream, None)
                    if block is None:
                        break
                    self.buffer.extend(block.tobytes())
                pcm = self.buffer[:needed]
            except miniaudio.DecodeError as exc:
                self.close()
                raise ValueError('音声区間をデコードできません。ファイルの内容を確認してください') from exc
        return _wave_bytes(info, pcm, end_frame - start_frame)


def read_window(path: Path, start_frame: int, end_frame: int):
    with WindowReader(path) as reader:
        return reader.read(start_frame, end_frame)
