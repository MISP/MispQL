"""Snake II, as on the Nokia 3310: the arrows steer, q leaves."""
import random

from mispql import lcd

WIDTH, HEIGHT = 19, 9  # cells; the edges wrap
CELL, MARGIN = 4, 2  # pixels: the field and its border fill the screen
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


class Game:
    title, notice = 'Snake II', None

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

    def status(self):
        return f' {self.score:04d}', f'{self.bonus_left:>3} ' if self.bonus else ''

    def draw(self, canvas):
        for edge in ((0, 0, lcd.WIDTH, 1), (0, lcd.HEIGHT - 1, lcd.WIDTH, 1), (0, 0, 1, lcd.HEIGHT),
                     (lcd.WIDTH - 1, 0, 1, lcd.HEIGHT)):
            canvas.fill(*edge)
        if self.food:
            canvas.draw(*_origin(self.food), FOOD)
        if self.bonus:
            canvas.draw(*_origin(self.bonus[0]), CREATURES[self.bonus[1]])
        canvas.draw(*_origin(self.snake[0]), _facing(HEAD, self.heading))
        for i, (x, y) in enumerate(self.snake[1:], 1):
            ox, oy = _origin((x, y))
            canvas.fill(ox + 1, oy + 1, 2, 2)
            for nx, ny in self.snake[i - 1:i + 2:2]:
                dx, dy = (nx - x + 1) % WIDTH - 1, (ny - y + 1) % HEIGHT - 1
                if dx:
                    canvas.fill(ox + (3 if dx > 0 else 0), oy + 1, 1, 2)
                else:
                    canvas.fill(ox + 1, oy + (3 if dy > 0 else 0), 2, 1)


def _origin(cell):
    return MARGIN + CELL * cell[0], MARGIN + CELL * cell[1]


def _facing(sprite, heading):
    """`sprite` faces right; turn it to face `heading`."""
    if heading == LEFT:
        return [row[::-1] for row in sprite]
    if heading == UP:
        return [''.join(column) for column in zip(*sprite)][::-1]
    if heading == DOWN:
        return [''.join(column)[::-1] for column in zip(*sprite)]
    return sprite


def play(high_score_file):
    """Play full screen until q; the best score is kept in `high_score_file`."""
    turns = {'up': UP, 'down': DOWN, 'left': LEFT, 'right': RIGHT}
    lcd.play(Game, {key: lambda game, d=d: game.turn(d) for key, d in turns.items()}, high_score_file, TICK)
