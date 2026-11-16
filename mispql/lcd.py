"""A Nokia 3310 screen in the terminal, and the loop that runs a game on it."""
import asyncio

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

WIDTH, HEIGHT = 80, 40  # pixels; a character is one pixel wide and two high
HALF = {(False, False): ' ', (True, False): '▀', (False, True): '▄', (True, True): '█'}
STYLE = Style.from_dict({'lcd': 'bg:#c7f0d8 #43523d'})


class Canvas:
    def __init__(self, height=HEIGHT):
        self.height = height
        self.px = [[False] * WIDTH for _ in range(height)]

    def fill(self, x, y, w, h):
        x0, x1 = max(x, 0), min(x + w, WIDTH)
        for row in self.px[max(y, 0):max(min(y + h, self.height), 0)]:
            row[x0:x1] = [True] * max(x1 - x0, 0)

    def draw(self, x, y, sprite):
        """`sprite` is rows of '#' (on) and '.' (off); what falls outside the screen is cut."""
        for dy, line in enumerate(sprite):
            if 0 <= y + dy < self.height:
                for dx, c in enumerate(line):
                    if c == '#' and 0 <= x + dx < WIDTH:
                        self.px[y + dy][x + dx] = True

    def lines(self):
        return [''.join(HALF[pair] for pair in zip(self.px[y], self.px[y + 1])) for y in range(0, self.height, 2)]


def rows_needed(game):
    """A game draws on a canvas `game.height` pixels high (HEIGHT if it does not say), under a
    line of text from `game.status()` if it has one."""
    return getattr(game, 'height', HEIGHT) // 2 + hasattr(game, 'status')


def render(game, high, best, columns, rows):
    """The whole screen for `game`, centred, as prompt_toolkit formatted text."""
    if columns < WIDTH or rows < rows_needed(game):
        return [('', f'{game.title} needs a {WIDTH}x{rows_needed(game)} terminal. q leaves.')]
    canvas = Canvas(getattr(game, 'height', HEIGHT))
    game.draw(canvas)
    lines = canvas.lines()
    if game.over:
        message = ['', 'Game over!', f'Score {game.score}',
                   'New high score!' if best else f'High score {high}', '', 'space: again, q: leave', '']
    else:
        message = game.notice and ['', game.notice, '']
    if message:
        width = max(map(len, message)) + 4
        start, top = (WIDTH - width) // 2, (len(lines) - len(message)) // 2
        for k, text in enumerate(message):
            line = lines[top + k]
            lines[top + k] = line[:start] + text.center(width) + line[start + width:]
    if hasattr(game, 'status'):
        left, right = game.status()
        lines.insert(0, left.ljust(WIDTH - len(right)) + right)
    pad = ' ' * ((columns - WIDTH) // 2)
    text = [('', '\n' * ((rows - len(lines)) // 2))]
    for line in lines:
        text += [('', pad), ('class:lcd', line), ('', '\n')]
    return text[:-1]


def play(new_game, keys, high_score_file, tick):
    """Play games from `new_game()` full screen until q. `keys` maps key names to actions on the
    game; space starts a new game once one is over. The best score is kept in `high_score_file`."""
    game = new_game()
    high = _read_high(high_score_file)
    best = False

    def fits(size):
        return size.columns >= WIDTH and size.rows >= rows_needed(game)

    async def ticking():
        nonlocal high, best
        while True:
            await asyncio.sleep(tick)
            if not game.over and fits(app.output.get_size()):
                game.step()
                if game.over and game.score > high:
                    high, best = game.score, True
                    _write_high(high_score_file, high)
                app.invalidate()

    bindings = KeyBindings()
    for key, action in keys.items():
        if key != ' ':
            bindings.add(key)(lambda event, action=action: None if game.over else action(game))

    @bindings.add('q')
    @bindings.add('Q')
    @bindings.add('c-c')
    def leave(event):
        event.app.exit()

    @bindings.add(' ')
    def space(event):
        nonlocal game, best
        if game.over:
            game, best = new_game(), False
        elif ' ' in keys:
            keys[' '](game)

    def text():
        size = app.output.get_size()
        return render(game, high, best, size.columns, size.rows)

    app = Application(layout=Layout(Window(FormattedTextControl(text, show_cursor=False))),
                      key_bindings=bindings, style=STYLE, full_screen=True)
    app.run(pre_run=lambda: app.create_background_task(ticking()))


def _read_high(path):
    try:
        return int(path.read_text())
    except (OSError, ValueError):
        return 0


def _write_high(path, score):
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(f'{score}\n')
    except OSError:
        pass
