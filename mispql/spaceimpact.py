"""Space Impact, as on the Nokia 3310: the arrows fly the ship, space fires, q leaves."""
import math
import random
from dataclasses import dataclass

from mispql import lcd

TICK = 0.06  # seconds per frame
LIVES = 3
STEP = 2  # pixels per key press; a held key repeats
SHOTS, COOLDOWN = 4, 4  # the ship's shots in flight at once; frames between two
BLINK = 40  # frames the ship cannot be hit after a hit
BANNER = 40  # frames "Level n" shows before the wave comes

SHIP = ('##......', '.####...', '.#######', '.####...', '##......')
SHOT, ENEMY_SHOT = ('###',), ('##',)
BOOM = ('#...#', '.#.#.', '..#..', '.#.#.', '#...#')
BOSS = (
    '......####....',
    '....########..',
    '..###########.',
    '.###..###..###',
    '##############',
    '#.##########..',
    '##############',
    '.###..###..###',
    '..###########.',
    '....########..',
    '......####....',
)
KINDS = {  # sprite, hit points, points
    'drone': (('..##..', '.####.', '##..##', '.####.', '..##..'), 1, 5),
    'fighter': (('....###', '..####.', '#######', '..####.', '....###'), 1, 10),
    'crab': (('.#...#.', '..###..', '.#####.', '##.#.##', '.#####.', '#.#.#.#'), 2, 15),
    'boss': (BOSS, None, 0),
}


@dataclass
class Enemy:
    kind: str
    x: float
    y: float
    hp: int
    age: int = 0


class Game:
    title = 'Space Impact'

    def __init__(self, rng=random):
        self.rng = rng
        self.x, self.y = 4, (lcd.HEIGHT - len(SHIP)) // 2
        self.lives, self.score, self.over = LIVES, 0, False
        self.shots, self.enemy_shots, self.enemies, self.booms = [], [], [], []
        self.cooldown = self.blink = self.clock = 0
        self.start(1)

    def start(self, level):
        self.level, self.waiting, self.banner = level, 10 + 4 * level, BANNER

    @property
    def notice(self):
        return f'Level {self.level}' if self.banner else None

    def status(self):
        return f' {"♥" * self.lives}', f'{self.score:05d} '

    def move(self, dx, dy):
        self.x = min(max(self.x + dx * STEP, 0), lcd.WIDTH // 2)
        self.y = min(max(self.y + dy * STEP, 0), lcd.HEIGHT - len(SHIP))

    def fire(self):
        if not self.cooldown and len(self.shots) < SHOTS:
            self.shots.append([self.x + len(SHIP[0]), self.y + 2])
            self.cooldown = COOLDOWN

    def step(self):
        self.clock += 1
        self.cooldown, self.blink, self.banner = (max(n - 1, 0) for n in (self.cooldown, self.blink, self.banner))
        self.spawn()
        for shot in self.shots:
            shot[0] += 3
        for shot in self.enemy_shots:
            shot[0] -= 2
            shot[1] += shot[2]
        for enemy in self.enemies:
            self.fly(enemy)
        self.booms = [[x, y, left - 1] for x, y, left in self.booms if left > 1]
        self.collide()
        self.shots = [shot for shot in self.shots if shot[0] < lcd.WIDTH]
        self.enemy_shots = [shot for shot in self.enemy_shots if shot[0] > -2 and -1 < shot[1] < lcd.HEIGHT]
        self.enemies = [enemy for enemy in self.enemies if enemy.hp > 0 and enemy.x > -len(_sprite(enemy)[0])]

    def spawn(self):
        if self.banner:
            return
        if self.waiting and self.clock % max(8, 26 - 2 * self.level) == 0:
            self.waiting -= 1
            kind = self.rng.choice(['drone'] * 3 + ['fighter'] * min(self.level, 3) + ['crab'] * min(self.level - 1, 3))
            sprite, hp, _ = KINDS[kind]
            self.enemies.append(Enemy(kind, lcd.WIDTH, self.rng.randrange(4, lcd.HEIGHT - len(sprite) - 4), hp))
        elif not self.waiting and not self.enemies:
            self.enemies.append(Enemy('boss', lcd.WIDTH, (lcd.HEIGHT - len(BOSS)) // 2, 10 + 5 * self.level))

    def fly(self, enemy):
        enemy.age += 1
        speed = 0.5 + 0.1 * min(self.level, 6)
        sprite = _sprite(enemy)
        if enemy.kind == 'boss':
            enemy.x = max(enemy.x - 0.5, lcd.WIDTH - len(BOSS[0]) - 2)
            enemy.y = (lcd.HEIGHT - len(BOSS)) / 2 * (1 + math.sin(enemy.age / 25))
            if enemy.age % 30 == 0:
                self.enemy_shots += [[enemy.x, enemy.y + len(BOSS) // 2, dy] for dy in (-0.4, 0, 0.4)]
        elif enemy.kind == 'drone':
            enemy.x -= speed
            enemy.y += math.cos(enemy.age / 5) * 0.8  # weaves about the line it came in on
        elif enemy.kind == 'fighter':
            enemy.x -= speed * 1.2
            if enemy.age % 45 == 20 and enemy.x < lcd.WIDTH - len(sprite[0]):
                self.enemy_shots.append([enemy.x, enemy.y + 2, 0])
        else:
            enemy.x -= speed * 0.8
            enemy.y += min(max(self.y - enemy.y, -0.3), 0.3)

    def collide(self):
        for shot in self.shots:
            for enemy in self.enemies:
                if enemy.hp > 0 and _hit((shot[0], shot[1], 3, 1), _box(enemy)):
                    shot[0] = lcd.WIDTH  # spent
                    enemy.hp -= 1
                    if not enemy.hp:
                        self.destroyed(enemy)
                    break
        if self.blink or self.over:
            return
        ship = (self.x, self.y, len(SHIP[0]), len(SHIP))
        for enemy in self.enemies:
            if enemy.hp > 0 and _hit(ship, _box(enemy)):
                if enemy.kind != 'boss':
                    enemy.hp = 0
                    self.boom(enemy)
                return self.hit()
        for shot in self.enemy_shots:
            if _hit(ship, (shot[0], shot[1], 2, 1)):
                shot[0] = -10  # spent
                return self.hit()

    def destroyed(self, enemy):
        self.boom(enemy)
        if enemy.kind == 'boss':
            self.score += 50 * self.level
            self.enemy_shots = []
            self.start(self.level + 1)
        else:
            self.score += KINDS[enemy.kind][2]

    def boom(self, enemy):
        sprite = _sprite(enemy)
        self.booms.append([round(enemy.x) + len(sprite[0]) // 2 - 2, round(enemy.y) + len(sprite) // 2 - 2, 6])

    def hit(self):
        self.lives -= 1
        self.booms.append([self.x + 2, self.y, 6])
        self.blink = BLINK
        self.over = not self.lives

    def draw(self, canvas):
        if not self.over and (self.blink // 3) % 2 == 0:
            canvas.draw(self.x, self.y, SHIP)
        for x, y in self.shots:
            canvas.draw(round(x), round(y), SHOT)
        for x, y, _ in self.enemy_shots:
            canvas.draw(round(x), round(y), ENEMY_SHOT)
        for enemy in self.enemies:
            canvas.draw(round(enemy.x), round(enemy.y), _sprite(enemy))
        for x, y, _ in self.booms:
            canvas.draw(x, y, BOOM)


def _sprite(enemy):
    return KINDS[enemy.kind][0]


def _box(enemy):
    sprite = _sprite(enemy)
    return enemy.x, enemy.y, len(sprite[0]), len(sprite)


def _hit(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def play(high_score_file):
    """Play full screen until q; the best score is kept in `high_score_file`."""
    moves = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}
    keys = {key: lambda game, d=d: game.move(*d) for key, d in moves.items()}
    lcd.play(Game, {**keys, ' ': Game.fire}, high_score_file, TICK)
