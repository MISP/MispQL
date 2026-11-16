"""Snake II, as on the Nokia 3310: the arrows steer, q leaves."""
import asyncio
import random

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

WIDTH, HEIGHT = 19, 9  # cells; the edges wrap
CELL, MARGIN = 4, 2  # pixels; a character is one pixel wide and two high
PW, PH = WIDTH * CELL + 2 * MARGIN, HEIGHT * CELL + 2 * MARGIN
TICK = 0.12  # seconds per move
FOOD_POINTS = 5
BONUS_EVERY = 5  # foods
BONUS_TICKS = 30  # moves a creature stays; what is left is what it scores
UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)

FOOD = ('..#.', '.#.#', '..#.', '....')
HEAD = ('.##.', '##.#', '####', '.##.')  # facing right
CREATURES = (
    ('#..##..#', '.######.', '##.##.##', '.#....#.'),
    ('.######.', '##.##.##', '########', '#.#..#.#'),
    ('..##....', '.####.##', '########', '.#.#..#.'),
)
HALF = {(False, False): ' ', (True, False): '▀', (False, True): '▄', (True, True): '█'}
STYLE = Style.from_dict({'lcd': 'bg:#c7f0d8 #43523d'})


class Game:
    def __init__(self, rng=random):
        self.rng = rng
        self.snake = [(x, HEIGHT // 2) for x in range(6, 1, -1)]  # head first
        self.heading, self.turns = RIGHT, []
        self.grow = self.score = self.eaten = self.bonus_left = 0
        self.food = self.bonus = None  # bonus: ((x, y), creature), two cells wide
        self.over = False
        self.food = self.free_cell()

    def turn(self, direction):
        last = self.turns[-1] if self.turns else self.heading
        if len(self.turns) < 3 and direction not in (last, (-last[0], -last[1])):
            self.turns.append(direction)

    def step(self):
        if self.turns:
            self.heading = self.turns.pop(0)
        x, y = self.snake[0]
        head = ((x + self.heading[0]) % WIDTH, (y + self.heading[1]) % HEIGHT)
        # The tail moves on unless the snake is growing.
        if head in (self.snake if self.grow else self.snake[:-1]):
            self.over = True
            return
        self.snake.insert(0, head)
        if self.grow:
            self.grow -= 1
        else:
            self.snake.pop()
        if head in self.bonus_cells():
            self.score += self.bonus_left
            self.bonus = None
        elif self.bonus:
            self.bonus_left -= 1
            if not self.bonus_left:
                self.bonus = None
        if head == self.food:
            self.score += FOOD_POINTS
            self.grow += 1
            self.eaten += 1
            self.food = self.free_cell()
            if self.eaten % BONUS_EVERY == 0 and not self.bonus:
                spot = self.free_cell(2)
                if spot:
                    self.bonus = (spot, self.rng.randrange(len(CREATURES)))
                    self.bonus_left = BONUS_TICKS

    def bonus_cells(self):
        if not self.bonus:
            return ()
        (x, y), _ = self.bonus
        return (x, y), (x + 1, y)

    def free_cell(self, width=1):
        taken = {*self.snake, self.food, *self.bonus_cells()}
        free = [(x, y) for y in range(HEIGHT) for x in range(WIDTH - width + 1)
                if not taken.intersection((x + i, y) for i in range(width))]
        return self.rng.choice(free) if free else None


def field(game):
    """The play field as lines of half-block characters, two pixel rows to a line."""
    px = [[x in (0, PW - 1) or y in (0, PH - 1) for x in range(PW)] for y in range(PH)]

    def fill(x, y, w, h):
        for row in px[y:y + h]:
            row[x:x + w] = [True] * w

    def draw(cell, sprite):
        ox, oy = MARGIN + CELL * cell[0], MARGIN + CELL * cell[1]
        for dy, line in enumerate(sprite):
            for dx, c in enumerate(line):
                if c == '#':
                    px[oy + dy][ox + dx] = True

    if game.food:
        draw(game.food, FOOD)
    if game.bonus:
        draw(game.bonus[0], CREATURES[game.bonus[1]])
    draw(game.snake[0], _facing(HEAD, game.heading))
    for i, (x, y) in enumerate(game.snake[1:], 1):
        ox, oy = MARGIN + CELL * x, MARGIN + CELL * y
        fill(ox + 1, oy + 1, 2, 2)
        for nx, ny in game.snake[i - 1:i + 2:2]:
            dx, dy = (nx - x + 1) % WIDTH - 1, (ny - y + 1) % HEIGHT - 1
            if dx:
                fill(ox + (3 if dx > 0 else 0), oy + 1, 1, 2)
            else:
                fill(ox + 1, oy + (3 if dy > 0 else 0), 2, 1)
    return [''.join(HALF[pair] for pair in zip(px[y], px[y + 1])) for y in range(0, PH, 2)]


def _facing(sprite, heading):
    """`sprite` faces right; turn it to face `heading`."""
    if heading == LEFT:
        return [row[::-1] for row in sprite]
    if heading == UP:
        return [''.join(column) for column in zip(*sprite)][::-1]
    if heading == DOWN:
        return [''.join(column)[::-1] for column in zip(*sprite)]
    return sprite


def screen(game, high, best, columns, rows):
    """The whole screen, centred, as prompt_toolkit formatted text."""
    if columns < PW or rows <= PH // 2:
        return [('', f'Snake II needs a {PW}x{PH // 2 + 1} terminal. q leaves.')]
    lines = field(game)
    if game.over:
        message = ['', 'Game over!', f'Score {game.score}',
                   'New high score!' if best else f'High score {high}', '', 'space: again, q: leave', '']
        width = max(map(len, message)) + 4
        start, top = (PW - width) // 2, (len(lines) - len(message)) // 2
        for k, text in enumerate(message):
            line = lines[top + k]
            lines[top + k] = line[:start] + text.center(width) + line[start + width:]
    bonus = f'{game.bonus_left:>3} ' if game.bonus else ''
    lines.insert(0, f' {game.score:04d}'.ljust(PW - len(bonus)) + bonus)
    left = ' ' * ((columns - PW) // 2)
    text = [('', '\n' * ((rows - len(lines)) // 2))]
    for line in lines:
        text += [('', left), ('class:lcd', line), ('', '\n')]
    return text[:-1]


def play(high_score_file):
    """Play full screen until q; the best score is kept in `high_score_file`."""
    game = Game()
    high = _read_high(high_score_file)
    best = False

    async def tick():
        nonlocal high, best
        while True:
            await asyncio.sleep(TICK)
            size = app.output.get_size()
            if not game.over and size.columns >= PW and size.rows > PH // 2:
                game.step()
                if game.over and game.score > high:
                    high, best = game.score, True
                    _write_high(high_score_file, high)
                app.invalidate()

    keys = KeyBindings()
    for key, direction in ('up', UP), ('down', DOWN), ('left', LEFT), ('right', RIGHT):
        keys.add(key)(lambda event, direction=direction: game.turn(direction))

    @keys.add('q')
    @keys.add('Q')
    @keys.add('c-c')
    def leave(event):
        event.app.exit()

    @keys.add(' ')
    def again(event):
        nonlocal game, best
        if game.over:
            game, best = Game(), False

    def text():
        size = app.output.get_size()
        return screen(game, high, best, size.columns, size.rows)

    app = Application(layout=Layout(Window(FormattedTextControl(text, show_cursor=False))),
                      key_bindings=keys, style=STYLE, full_screen=True)
    app.run(pre_run=lambda: app.create_background_task(tick()))


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
