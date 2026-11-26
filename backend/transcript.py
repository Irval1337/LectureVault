"""Bounds and cleanup for speech transcription."""
import re

MAX_VIDEO_SECONDS = 3 * 3600
AUDIO_NORMALIZATION_TIMEOUT = 450
SPEECH_RECOGNITION_TIMEOUT = 5400


def normalized(line):
    return ' '.join(re.findall(r'\w+', line.casefold().replace('ё', 'е')))


def clean_transcript(text):
    lines = []
    repetitions = []
    previous = ''

    def flush():
        if len(repetitions) >= 4:
            lines.append(repetitions[0])
        else:
            lines.extend(repetitions)
        repetitions.clear()

    for line in text.splitlines(keepends=True):
        phrase = normalized(line)
        if (phrase.startswith(('редактор субтитров а синецк', 'редактор субтитров а сухиашвили',
                               'корректор а сухиашвили'))
                and (len(phrase.split()) <= 12 or phrase.count('корректор') > 1)):
            continue
        if phrase and phrase == previous:
            repetitions.append(line)
        else:
            flush()
            repetitions.append(line)
            previous = phrase
    flush()
    return ''.join(lines)
