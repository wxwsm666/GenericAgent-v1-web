#!/usr/bin/env python3
"""Shared voice/audio transcription module for WeChat/Feishu/DingTalk bots.
Uses Whisper API (OpenAI-compatible) for speech-to-text.
"""
import os, subprocess, tempfile, hashlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_DIR = os.path.join(PROJECT_ROOT, 'temp')
os.makedirs(TEMP_DIR, exist_ok=True)

# Audio format support
AUDIO_EXTS = {'.silk', '.amr', '.opus', '.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma', '.ape', '.m4r'}

def _load_whisper_config():
    """Load Whisper API config from mykey.py. Returns (api_base, api_key, model)."""
    sys.path.insert(0, PROJECT_ROOT)
    try:
        from mykey import mykeys
    except ImportError:
        from mykey_template import mykeys
    cfg = mykeys if isinstance(mykeys, dict) else {}
    return (
        str(cfg.get('whisper_api_base', 'https://api.openai.com') or 'https://api.openai.com').rstrip('/'),
        str(cfg.get('whisper_api_key', '') or ''),
        str(cfg.get('whisper_model', 'whisper-1') or 'whisper-1'),
    )


def _find_ffmpeg():
    """Locate ffmpeg binary."""
    paths = []
    if os.name == 'nt':
        paths = ['ffmpeg.exe', os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'ffmpeg', 'bin', 'ffmpeg.exe')]
    else:
        paths = ['ffmpeg', '/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg']
    for p in paths:
        try:
            subprocess.run([p, '-version'], capture_output=True, check=True)
            return p
        except Exception:
            continue
    return 'ffmpeg'  # hope it's in PATH


def _convert_to_wav(input_path, sample_rate=16000):
    """Convert audio file to 16kHz mono WAV. Returns path to WAV file."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext == '.wav':
        # Check if already 16kHz mono
        try:
            probe = subprocess.run(
                [_find_ffmpeg(), '-i', input_path],
                capture_output=True, text=True, timeout=10)
            if '16000 Hz' in (probe.stderr or '') and 'mono' in (probe.stderr or '').lower():
                return input_path
        except Exception:
            pass

    output = os.path.join(TEMP_DIR, f'asr_{hashlib.md5(input_path.encode()).hexdigest()[:8]}.wav')
    cmd = [_find_ffmpeg(), '-y', '-i', input_path, '-ar', str(sample_rate), '-ac', '1', '-f', 'wav', output]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f'ffmpeg conversion failed: {result.stderr[:300]}')
    return output


def transcribe(file_path, language=None):
    """Transcribe audio file using Whisper API.

    Args:
        file_path: Path to audio file
        language: Optional language hint (e.g., 'zh', 'en')

    Returns:
        Transcribed text string, or None on failure.
    """
    import requests

    wav_path = _convert_to_wav(file_path)
    api_base, api_key, model = _load_whisper_config()

    if not api_key:
        print('[ASR] No whisper_api_key configured, skipping transcription')
        return None

    url = f'{api_base}/v1/audio/transcriptions'
    headers = {'Authorization': f'Bearer {api_key}'}
    data = {'model': model}
    if language:
        data['language'] = language

    try:
        with open(wav_path, 'rb') as f:
            files = {'file': (os.path.basename(wav_path), f, 'audio/wav')}
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=60)
        if resp.status_code == 200:
            result = resp.json()
            text = (result.get('text') or '').strip()
            print(f'[ASR] Transcribed: {text[:80]}...' if len(text) > 80 else f'[ASR] Transcribed: {text}')
            return text
        else:
            print(f'[ASR] API error {resp.status_code}: {resp.text[:300]}')
            return None
    except Exception as e:
        print(f'[ASR] Error: {e}')
        return None
    finally:
        # Clean up temp WAV if we created one
        if wav_path != file_path and os.path.isfile(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Usage: python voice_asr.py <audio_file>')
        sys.exit(1)
    text = transcribe(sys.argv[1])
    if text:
        print(f'Result: {text}')
    else:
        print('Transcription failed.')
