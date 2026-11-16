"""Space Impact, as on the Nokia 3310: the arrows fly the ship, space fires, x fires the special
weapon, q leaves."""
import math
import random
from dataclasses import dataclass

from mispql import lcd

TICK = 0.06  # seconds per frame
TOP, H = 6, 36  # pixels: the status bar, then the playfield under it
LIVES, MAX_LIVES = 3, 5
STEP = 2  # pixels per key press; a held key repeats
SHOTS, COOLDOWN = 4, 4  # the ship's shots in flight at once; frames between two
BLINK = 40  # frames the ship cannot be hit after a hit
BANNER = 40  # frames "Level n" shows before the wave comes
DROP_EVERY = 6  # kills between two pick-ups
WEAPONS = {'missile': 3, 'laser': 2, 'bomb': 2}  # what a pick-up gives
BEAM, FUSE, BLAST = 8, 18, (14, 10)  # frames a laser lasts; frames to a bomb going off; its reach

SHIP = (
    '#..........',
    '##...###...',
    '.#.######..',
    '..#########',
    '.#.######..',
    '##...###...',
    '#..........',
)
SHOT, ENEMY_SHOT, MISSILE, BOMB = ('###',), ('##',), ('##..', '####', '##..'), ('.##.', '####', '####', '.##.')
BOOM = ('#...#', '.#.#.', '..#..', '.#.#.', '#...#')
BIG_BOOM = ('#...#...#', '.#..#..#.', '..#.#.#..', '...###...', '#########', '...###...', '..#.#.#..',
            '.#..#..#.', '#...#...#')
HEART = ('##.##', '#####', '.###.', '..#..')
BOX = ('#######',) + ('#.....#',) * 5 + ('#######',)
ICONS = {
    'missile': ('#....', '####.', '#####', '####.', '#....'),
    'laser': ('#....', '#####', '#....', '#####', '#....'),
    'bomb': ('.###.', '#####', '##.##', '#####', '.###.'),
    'heart': HEART + ('.....',),
}
DIGITS = (
    ('###', '#.#', '#.#', '#.#', '###'), ('.#.', '##.', '.#.', '.#.', '###'), ('###', '..#', '###', '#..', '###'),
    ('###', '..#', '###', '..#', '###'), ('#.#', '#.#', '###', '..#', '..#'), ('###', '#..', '###', '..#', '###'),
    ('###', '#..', '###', '#.#', '###'), ('###', '..#', '..#', '..#', '..#'), ('###', '#.#', '###', '#.#', '###'),
    ('###', '#.#', '###', '..#', '###'),
)
KINDS = {  # sprite, hit points, points
    'drone': (('..##..', '.####.', '##..##', '.####.', '..##..'), 1, 5),
    'fighter': (('....###', '..####.', '#######', '..####.', '....###'), 1, 10),
    'crab': (('.#...#.', '..###..', '.#####.', '##.#.##', '.#####.', '#.#.#.#'), 2, 15),
}
BOSSES = {  # sprite, hit points; one boss closes each level, in this order
    'saucer': ((
        '.......######.......',
        '....############....',
        '..####..####..####..',
        '####################',
        '#.#.#.#.#.#.#.#.#.#.',
        '.##################.',
        '...##############...',
        '.....##..##..##.....',
        '....##...##...##....',
    ), 15),
    'serpent': ((
        '....#.#.#.#.....',
        '...#########....',
        '..###.#######...',
        '.###########.##.',
        '#####........###',
        '.####.........##',
        '#####.........##',
        '.###########.##.',
        '..#########.##..',
        '...#######.##...',
        '.....#.#.###....',
        '.........##.....',
    ), 20),
    'skull': ((
        '...########...',
        '.############.',
        '##############',
        '###...##...###',
        '##....##....##',
        '###...##...###',
        '##############',
        '######..######',
        '.############.',
        '..#.#.#.#.#.#.',
        '..##########..',
        '..#.#.#.#.#...',
    ), 25),
    'eye': ((
        '....######....',
        '..##......##..',
        '.#..........#.',
        '#....####....#',
        '#...######...#',
        '#..###..###..#',
        '#..##....##..#',
        '#..###..###..#',
        '#...######...#',
        '#....####....#',
        '.#..........#.',
        '..##......##..',
        '....######....',
    ), 30),
}
BOSS_ORDER = tuple(BOSSES)


@dataclass
class Enemy:
    kind: str
    x: float
    y: float
    hp: int
    age: int = 0
    t: int = 0  # a boss's frames since it took up its place


class Game:
    title, height = 'Space Impact', TOP + H

    def __init__(self, rng=random):
        self.rng = rng
        self.x, self.y = 4, (H - len(SHIP)) // 2
        self.lives, self.score, self.over = LIVES, 0, False
        self.weapon, self.stock = None, 0
        self.shots, self.missiles, self.bombs, self.enemy_shots = [], [], [], []
        self.enemies, self.pickups, self.booms = [], [], []
        self.cooldown = self.blink = self.beam = self.clock = self.kills = 0
        self.start(1)

    def start(self, level):
        self.level, self.waiting, self.banner = level, 10 + 4 * level, BANNER

    @property
    def notice(self):
        return f'Level {self.level}' if self.banner else None

    def move(self, dx, dy):
        self.x = min(max(self.x + dx * STEP, 0), lcd.WIDTH // 2)
        self.y = min(max(self.y + dy * STEP, 0), H - len(SHIP))

    def fire(self):
        if not self.cooldown and len(self.shots) < SHOTS:
            self.shots.append([self.x + len(SHIP[0]), self.y + 3])
            self.cooldown = COOLDOWN

    def special(self):
        if not self.stock or self.cooldown or self.beam:
            return
        self.stock -= 1
        self.cooldown = 2 * COOLDOWN
        x, y = self.x + len(SHIP[0]), self.y + 3
        if self.weapon == 'missile':
            self.missiles.append([x, y - 1])
        elif self.weapon == 'laser':
            self.beam = BEAM
        else:
            self.bombs.append([x, y - 2, FUSE])

    def step(self):
        self.clock += 1
        self.cooldown, self.blink, self.banner = (max(n - 1, 0) for n in (self.cooldown, self.blink, self.banner))
        self.spawn()
        for shot in self.shots:
            shot[0] += 3
        for missile in self.missiles:
            missile[0] += 4
        for bomb in self.bombs:
            bomb[0] += 2
            bomb[2] -= 1
        for shot in self.enemy_shots:
            shot[0] += shot[2]
            shot[1] += shot[3]
        for pickup in self.pickups:
            pickup[0] -= 0.5
        for enemy in self.enemies:
            self.fly(enemy)
        self.booms = [[x, y, left - 1, sprite] for x, y, left, sprite in self.booms if left > 1]
        self.collide()
        self.shots = [shot for shot in self.shots if shot[0] < lcd.WIDTH]
        self.missiles = [missile for missile in self.missiles if missile[0] < lcd.WIDTH]
        self.bombs = [bomb for bomb in self.bombs if bomb[2] >= 0]
        self.enemy_shots = [shot for shot in self.enemy_shots if shot[0] > -3 and -1 < shot[1] < H]
        self.pickups = [pickup for pickup in self.pickups if pickup[0] > -len(BOX[0])]
        self.enemies = [enemy for enemy in self.enemies if enemy.hp > 0 and enemy.x > -len(_sprite(enemy)[0])]

    def spawn(self):
        if self.banner:
            return
        if self.waiting and self.clock % max(8, 26 - 2 * self.level) == 0:
            self.waiting -= 1
            kind = self.rng.choice(['drone'] * 3 + ['fighter'] * min(self.level, 3) + ['crab'] * min(self.level - 1, 3))
            sprite, hp, _ = KINDS[kind]
            self.enemies.append(Enemy(kind, lcd.WIDTH, self.rng.randrange(2, H - len(sprite) - 2), hp))
        elif not self.waiting and not self.enemies:
            kind = BOSS_ORDER[(self.level - 1) % len(BOSS_ORDER)]
            sprite, hp = BOSSES[kind]
            hp += 10 * ((self.level - 1) // len(BOSS_ORDER))
            self.enemies.append(Enemy(kind, lcd.WIDTH, (H - len(sprite)) / 2, hp))

    def fly(self, enemy):
        enemy.age += 1
        if enemy.kind in BOSSES:
            return self.fly_boss(enemy)
        speed = 0.5 + 0.1 * min(self.level, 6)
        if enemy.kind == 'drone':
            enemy.x -= speed
            enemy.y += math.cos(enemy.age / 5) * 0.8  # weaves about the line it came in on
        elif enemy.kind == 'fighter':
            enemy.x -= speed * 1.2
            if enemy.age % 45 == 20 and enemy.x < lcd.WIDTH - 7:
                self.enemy_shots.append([enemy.x, enemy.y + 2, -2, 0])
        else:
            enemy.x -= speed * 0.8
            enemy.y += min(max(self.y - enemy.y, -0.3), 0.3)

    def fly_boss(self, enemy):
        sprite = _sprite(enemy)
        w, h = len(sprite[0]), len(sprite)
        home, span = lcd.WIDTH - w - 2, H - h
        if enemy.x > home:
            enemy.x = max(enemy.x - 0.5, home)
            return
        enemy.t += 1
        t, middle = enemy.t, enemy.y + h / 2
        if enemy.kind == 'saucer':
            enemy.y = span / 2 * (1 + math.sin(t / 25))
            if t % 30 == 0:
                self.enemy_shots += [[enemy.x, middle, -2, dy] for dy in (-0.4, 0, 0.4)]
        elif enemy.kind == 'serpent':
            enemy.x = home - 4 * (1 - math.cos(t / 30))
            enemy.y = span / 2 * (1 + math.sin(t / 18))
            if t % 24 == 0:
                frames = max((enemy.x - self.x) / 2, 1)
                dy = min(max((self.y + 3 - middle) / frames, -1), 1)
                self.enemy_shots.append([enemy.x, middle, -2, dy])
        elif enemy.kind == 'skull':
            enemy.y = abs((t + span / 2) % (2 * span) - span)  # sweeps from edge to edge
            if t % 45 == 0:
                gap = self.rng.randrange(6)
                self.enemy_shots += [[enemy.x, 2 + 6 * k, -1.5, 0] for k in range(6) if k != gap]
        else:
            if t % 50 == 0:
                enemy.y = self.rng.randrange(0, span)
            if t % 12 == 0 and t % 50 > 10:
                self.enemy_shots.append([enemy.x, middle, -3, 0])

    def collide(self):
        for shot in self.shots:
            enemy = self.struck((shot[0], shot[1], 3, 1))
            if enemy:
                shot[0] = lcd.WIDTH  # spent
                self.damage(enemy, 1)
        for missile in self.missiles:
            enemy = self.struck((missile[0], missile[1], 4, 3))
            if enemy:
                missile[0] = lcd.WIDTH
                self.damage(enemy, 5)
        for bomb in self.bombs:
            if bomb[2] <= 0 or bomb[0] >= lcd.WIDTH - 6 or self.struck((bomb[0], bomb[1], 4, 4)):
                bomb[2] = -1  # gone off
                self.blast(bomb[0] + 2, bomb[1] + 2)
        if self.beam:
            beam = (self.x + len(SHIP[0]), self.y + 3, lcd.WIDTH, 1)
            for enemy in self.enemies:
                if enemy.hp > 0 and _hit(beam, _box(enemy)):
                    self.damage(enemy, 1)
            self.enemy_shots = [shot for shot in self.enemy_shots if not _hit(beam, (shot[0], shot[1], 2, 1))]
            self.beam -= 1
        for pickup in self.pickups:
            if _hit(self.hull(), (pickup[0], pickup[1], len(BOX[0]), len(BOX))):
                pickup[0] = -len(BOX[0])  # taken
                self.collect(pickup[2])
        if self.blink or self.over:
            return
        enemy = self.struck(self.hull())
        if enemy:
            if enemy.kind not in BOSSES:
                enemy.hp = 0  # gone, but not shot down: no points
                self.booms.append(_boom(enemy))
            return self.hit()
        for shot in self.enemy_shots:
            if _hit(self.hull(), (shot[0], shot[1], 2, 1)):
                shot[0] = -10  # spent
                return self.hit()

    def hull(self):
        """What a hit counts on: the ship without the tips of its fins."""
        return self.x + 2, self.y + 1, len(SHIP[0]) - 2, len(SHIP) - 2

    def struck(self, box):
        return next((enemy for enemy in self.enemies if enemy.hp > 0 and _hit(box, _box(enemy))), None)

    def damage(self, enemy, points):
        enemy.hp = max(enemy.hp - points, 0)
        if not enemy.hp:
            self.destroyed(enemy)

    def blast(self, x, y):
        area = (x - BLAST[0], y - BLAST[1], 2 * BLAST[0], 2 * BLAST[1])
        self.booms.append([x - 4, y - 4, 6, BIG_BOOM])
        for enemy in self.enemies:
            if enemy.hp > 0 and _hit(area, _box(enemy)):
                self.damage(enemy, 4)
        self.enemy_shots = [shot for shot in self.enemy_shots if not _hit(area, (shot[0], shot[1], 2, 1))]

    def destroyed(self, enemy):
        self.booms.append(_boom(enemy))
        sprite = _sprite(enemy)
        middle = enemy.x + len(sprite[0]) / 2, enemy.y + len(sprite) / 2
        if enemy.kind in BOSSES:
            self.score += 50 * self.level
            self.enemy_shots = []
            self.drop(middle, 'heart' if self.lives < MAX_LIVES else self.rng.choice(list(WEAPONS)))
            self.start(self.level + 1)
            return
        self.score += KINDS[enemy.kind][2]
        self.kills += 1
        if self.kills % DROP_EVERY == 0:
            heart = self.lives < MAX_LIVES and self.rng.random() < 0.25
            self.drop(middle, 'heart' if heart else self.rng.choice(list(WEAPONS)))

    def drop(self, middle, kind):
        self.pickups.append([middle[0] - 3, min(max(middle[1] - 3, 0), H - len(BOX)), kind])

    def collect(self, kind):
        if kind == 'heart':
            self.lives = min(self.lives + 1, MAX_LIVES)
        elif kind == self.weapon:
            self.stock = min(self.stock + WEAPONS[kind], 99)
        else:
            self.weapon, self.stock = kind, WEAPONS[kind]

    def hit(self):
        self.lives -= 1
        self.booms.append([self.x + 3, self.y + 1, 6, BOOM])
        self.blink = BLINK
        self.over = not self.lives

    def draw(self, canvas):
        for i in range(self.lives):
            canvas.draw(1 + 6 * i, 0, HEART)
        if self.weapon:
            canvas.draw(33, 0, ICONS[self.weapon])
            _number(canvas, 40, f'{self.stock:02d}')
        _number(canvas, lcd.WIDTH - 20, f'{self.score % 100000:05d}')

        def put(x, y, sprite):
            canvas.draw(round(x), TOP + round(y), sprite)
        if not self.over and (self.blink // 3) % 2 == 0:
            put(self.x, self.y, SHIP)
        if self.beam:
            canvas.fill(self.x + len(SHIP[0]), TOP + self.y + 3, lcd.WIDTH, 1)
        for x, y in self.shots:
            put(x, y, SHOT)
        for x, y in self.missiles:
            put(x, y, MISSILE)
        for x, y, _ in self.bombs:
            put(x, y, BOMB)
        for x, y, _, _ in self.enemy_shots:
            put(x, y, ENEMY_SHOT)
        for x, y, kind in self.pickups:
            put(x, y, BOX)
            put(x + 1, y + 1, ICONS[kind])
        for enemy in self.enemies:
            put(enemy.x, enemy.y, _sprite(enemy))
        for x, y, _, sprite in self.booms:
            put(x, y, sprite)


def _number(canvas, x, digits):
    for i, digit in enumerate(digits):
        canvas.draw(x + 4 * i, 0, DIGITS[int(digit)])


def _sprite(enemy):
    return BOSSES[enemy.kind][0] if enemy.kind in BOSSES else KINDS[enemy.kind][0]


def _boom(enemy):
    sprite, big = _sprite(enemy), enemy.kind in BOSSES
    boom = BIG_BOOM if big else BOOM
    return [round(enemy.x + (len(sprite[0]) - len(boom[0])) / 2), round(enemy.y + (len(sprite) - len(boom)) / 2), 6, boom]


def _box(enemy):
    sprite = _sprite(enemy)
    return enemy.x, enemy.y, len(sprite[0]), len(sprite)


def _hit(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def play(high_score_file):
    """Play full screen until q; the best score is kept in `high_score_file`."""
    moves = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}
    keys = {key: lambda game, d=d: game.move(*d) for key, d in moves.items()}
    lcd.play(Game, {**keys, ' ': Game.fire, 'x': Game.special, 'X': Game.special}, high_score_file, TICK)
