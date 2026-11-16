import random

from mispql import lcd, spaceimpact
from mispql.spaceimpact import BOSS, SHIP, Enemy, Game


def quiet():
    """A game past its level banner, with no enemy due for a while."""
    g = Game(random.Random(0))
    g.banner, g.waiting = 0, 99
    return g


def test_the_ship_stays_on_screen():
    g = quiet()
    for _ in range(50):
        g.move(-1, -1)
    assert (g.x, g.y) == (0, 0)
    for _ in range(50):
        g.move(1, 1)
    assert (g.x, g.y) == (lcd.WIDTH // 2, lcd.HEIGHT - len(SHIP))


def test_fire_waits_and_keeps_to_a_few_shots():
    g = quiet()
    g.fire()
    g.fire()
    assert len(g.shots) == 1
    for _ in range(12):
        g.fire()
        g.step()
    assert 1 < len(g.shots) <= spaceimpact.SHOTS


def test_a_shot_brings_an_enemy_down():
    g = quiet()
    g.enemies = [Enemy('drone', 20, 10, 1)]
    g.shots = [[16, 12]]
    g.step()
    assert (g.enemies, g.score, len(g.booms)) == ([], 5, 1)


def test_a_hit_costs_a_life_and_the_ship_blinks():
    g = quiet()
    g.enemies = [Enemy('fighter', g.x, g.y, 1)]
    g.step()
    assert (g.lives, g.blink) == (2, spaceimpact.BLINK)
    g.enemies = [Enemy('crab', g.x, g.y, 2)]
    g.step()
    assert g.lives == 2
    g.blink, g.lives = 0, 1
    g.enemy_shots = [[g.x + 4, g.y + 2, 0]]
    g.step()
    assert g.over


def test_the_boss_closes_the_level():
    g = quiet()
    g.waiting = 0
    g.step()
    assert [enemy.kind for enemy in g.enemies] == ['boss']
    g.enemies[0].hp = 1
    g.shots = [[lcd.WIDTH - 4, (lcd.HEIGHT - len(BOSS)) // 2 + 5]]
    g.step()
    assert (g.enemies, g.level, g.notice, g.score) == ([], 2, 'Level 2', 50)


def test_screen_fits_the_terminal():
    g = Game(random.Random(0))
    lines = ''.join(text for _, text in lcd.render(g, 0, False, 80, 24)).split('\n')
    assert len(lines) <= 24 and max(map(len, lines)) <= 80
    assert lines[1].startswith(' ♥♥♥') and any('Level 1' in line for line in lines)
