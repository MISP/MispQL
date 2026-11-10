class MispQLError(Exception):
    """An error at a place in the statement text, given as character offsets."""

    def __init__(self, message, start, end=None, at_end=False):
        super().__init__(message)
        self.message = message
        self.start = start
        self.end = start + 1 if end is None else end
        # The text ran out (open string, missing value): more input may complete it.
        self.at_end = at_end

    def render(self, text):
        line, column = position(text, self.start)
        line_start = self.start - column + 1
        line_end = text.find('\n', self.start)
        if line_end == -1:
            line_end = len(text)
        prefix = ''.join('\t' if c == '\t' else ' ' for c in text[line_start:self.start])
        width = max(1, min(self.end, line_end) - self.start)
        return (
            f'ERROR {line}:{column}: {self.message}\n'
            f'{text[line_start:line_end]}\n'
            f'{prefix}{"^" * width}'
        )


def position(text, offset):
    """1-based line and column of a character offset."""
    line = text.count('\n', 0, offset) + 1
    column = offset - (text.rfind('\n', 0, offset) + 1) + 1
    return line, column
