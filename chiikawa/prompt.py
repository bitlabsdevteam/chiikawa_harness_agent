"""A small POSIX line editor with immediate slash-command suggestions.

No runtime dependencies. Non-TTY and dumb terminals keep Python's plain input.
Terminal settings and bracketed paste are restored even on interruption.
"""

import codecs
import os
import select
import shutil
import sys
import unicodedata


def width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def clip(text, columns):
    result = ''
    for char in text:
        if width(result + char) > columns:
            break
        result += char
    return result


class Prompt:
    def __init__(self, candidates):
        self.candidates = candidates
        self.history = []

    def read(self, label='chiikawa> '):
        if not (sys.stdin.isatty() and sys.stdout.isatty()) or os.environ.get('TERM') == 'dumb':
            return input(label)
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        text, cursor, selected, rows = '', 0, 0, 0
        hidden, navigating, pasting = False, False, False
        history_index, draft = len(self.history), ''
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = bytearray()
        color = 'NO_COLOR' not in os.environ

        def green(value):
            return f'\033[32m{value}\033[0m' if color and value else value

        def write(value):
            sys.stdout.write(value)
            sys.stdout.flush()

        def render(options):
            nonlocal rows
            columns, height = shutil.get_terminal_size((80, 24))
            columns = max(4, columns - 1)  # Avoid the terminal's automatic line wrap.
            prompt = clip(label, min(len(label), columns // 2))
            available = columns - width(prompt)
            start = cursor
            while start > 0 and width(text[start - 1:cursor]) < available:
                start -= 1
            visible = clip(text[start:], available)
            write('\r\033[J' + prompt + (green(visible) if history_index < len(self.history) else visible))
            shown = options[:max(0, min(9, height - 3))]
            offset = max(0, selected - len(shown) + 1) if shown else 0
            shown = options[offset:offset + len(shown)]
            for index, (_, description) in enumerate(shown, offset):
                description = ''.join(c if c.isprintable() else ' ' for c in description)
                row = clip(('› ' if index == selected else '  ') + description, columns)
                write('\r\n' + (green(row) if index == selected else row))
            if shown:
                write('\r\n' + clip('↑/↓ select · Tab complete · Enter choose · Esc dismiss', columns))
            elif text.startswith('/') and not hidden:
                hint = 'Type a model ID, then Enter' if text.startswith('/model ') else 'No matching commands · /help lists commands'
                write('\r\n' + clip(hint, columns))
            rows = len(shown) + (1 if shown or (text.startswith('/') and not hidden) else 0)
            if rows:
                write(f'\033[{rows}A')
            write('\r')
            position = width(prompt + text[start:cursor])
            if position:
                write(f'\033[{position}C')

        def key():
            raw = bytes([pending.pop(0)]) if pending else os.read(fd, 1)
            if not raw:
                raise EOFError
            if raw != b'\x1b':
                return decoder.decode(raw)
            sequence = raw
            while select.select([fd], [], [], .03)[0]:
                sequence += os.read(fd, 1)
                if len(sequence) == 2 and sequence[-1:] not in (b'[', b'O'):
                    pending.extend(sequence[1:])
                    return '\x1b'
                if len(sequence) > 2 and 64 <= sequence[-1] <= 126:
                    break
                if len(sequence) > 20:
                    break
            return sequence.decode('ascii', errors='ignore')

        try:
            # Preserve typeahead entered between prompts; TCSAFLUSH would discard it.
            tty.setcbreak(fd, termios.TCSANOW)
            write('\033[?2004h')
            while True:
                options = [] if hidden or pasting else self.candidates(text)
                selected = min(selected, max(0, len(options) - 1))
                render(options)
                char = key()
                if char == '\x1b[200~':
                    pasting = True
                    continue
                if char == '\x1b[201~':
                    pasting = False
                    continue
                if pasting:
                    # Paste cannot submit commands or execute embedded terminal escapes.
                    chunk = ''.join(c if c.isprintable() else ' ' for c in char if c != '\x1b')
                    text = text[:cursor] + chunk + text[cursor:]
                    cursor += len(chunk)
                    continue
                if char in ('\r', '\n', '\t'):
                    if options and (char == '\t' or navigating or text in ('/', '/model', '/provider', '/provider/')
                                    or not any(text == item[0] for item in options)):
                        text = options[selected][0]
                        if text in ('/model', '/provider'):
                            text += ' '
                        cursor, selected, navigating = len(text), 0, False
                        if char == '\t' or text.endswith(' '):
                            continue
                    if char == '\t':
                        continue
                    write('\r\033[J' + label + green(text) + '\r\n')
                    if text and (not self.history or self.history[-1] != text):
                        self.history.append(text)
                    return text
                if char == '\x03':
                    raise KeyboardInterrupt
                if char == '\x04':
                    if not text:
                        raise EOFError
                    text = text[:cursor] + text[cursor + 1:]
                elif char in ('\x7f', '\x08'):
                    if cursor:
                        text, cursor = text[:cursor - 1] + text[cursor:], cursor - 1
                elif char == '\x1b[3~':
                    text = text[:cursor] + text[cursor + 1:]
                elif char in ('\x1b[A', '\x1b[B'):
                    step = -1 if char.endswith('A') else 1
                    if options:
                        selected = (selected + step) % len(options)
                        navigating = True
                        continue
                    if history_index == len(self.history):
                        draft = text
                    history_index = max(0, min(len(self.history), history_index + step))
                    text = self.history[history_index] if history_index < len(self.history) else draft
                    cursor = len(text)
                elif char == '\x1b[D':
                    cursor = max(0, cursor - 1)
                elif char == '\x1b[C':
                    cursor = min(len(text), cursor + 1)
                elif char in ('\x01', '\x1b[H', '\x1bOH', '\x1b[1~'):
                    cursor = 0
                elif char in ('\x05', '\x1b[F', '\x1bOF', '\x1b[4~'):
                    cursor = len(text)
                elif char == '\x15':
                    text, cursor = text[cursor:], 0
                elif char == '\x0b':
                    text = text[:cursor]
                elif char == '\x17':
                    start = cursor
                    while start and text[start - 1].isspace():
                        start -= 1
                    while start and not text[start - 1].isspace():
                        start -= 1
                    text, cursor = text[:start] + text[cursor:], start
                elif char == '\x1b':
                    hidden = True
                    continue
                elif char and all(c.isprintable() for c in char):
                    text = text[:cursor] + char + text[cursor:]
                    cursor += len(char)
                else:
                    continue
                hidden, selected, navigating = False, 0, False
        finally:
            try:
                write('\r\033[J\033[?2004l')
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
