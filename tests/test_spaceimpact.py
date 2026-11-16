import random

import pytest

from mispql import lcd, spaceimpact
from mispql.spaceimpact import BOSS_ORDER, BOSSES, DROP_EVERY, H, SHIP, WEAPONS, Enemy, Game


def quiet():
    """A game that sends no enemies: the level banner never ends."""
    g = Game(random.Random(0))
    g.banner = 10 ** 6
    return g


def test_sprites_are_rectangles():
    sprites = [SHIP, *spaceimpact.ROCKS, spaceimpact.BIG_BOOM, spaceimpact.BOX, *spaceimpact.ICONS.values(), *spaceimpact.DIGITS,
               *(kind[0] for kind in spaceimpact.KINDS.values()), *(boss[0] for boss in BOSSES.values())]
    for sprite in sprites:
        assert len({len(row) for row in sprite}) == 1, sprite
    assert all(len(boss[0]) <= H for boss in BOSSES.values())


def test_the_ship_stays_on_screen():
    g = quiet()
    for _ in range(50):
        g.move(-1, -1)
    assert (g.x, g.y) == (0, 0)
    for _ in range(50):
        g.move(1, 1)
    assert (g.x, g.y) == (lcd.WIDTH // 2, H - len(SHIP))


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
    assert (g.lives, g.blink, g.score) == (2, spaceimpact.BLINK, 0)
    g.enemies = [Enemy('crab', g.x, g.y, 2)]
    g.step()
    assert g.lives == 2
    g.blink, g.lives = 0, 1
    g.enemy_shots = [[g.x + 6, g.y + 3, -2, 0]]
    g.step()
    assert g.over


def test_every_few_kills_leave_a_pick_up():
    g = quiet()
    g.kills = DROP_EVERY - 1
    g.enemies = [Enemy('drone', 20, 10, 1)]
    g.shots = [[16, 12]]
    g.step()
    assert len(g.pickups) == 1
    g.pickups = [[g.x + 2, g.y, 'laser'], [g.x + 2, g.y, 'laser'], [g.x + 2, g.y, 'heart']]
    g.step()
    assert (g.weapon, g.stock, g.lives, g.pickups) == ('laser', 2 * WEAPONS['laser'], 4, [])


def test_special_weapons():
    g = quiet()
    g.special()
    assert (g.missiles, g.beam, g.bombs) == ([], 0, [])  # nothing to fire yet
    g.weapon, g.stock = 'missile', 1
    g.enemies = [Enemy('crab', 30, g.y, 2)]
    g.special()
    for _ in range(6):
        g.step()
    assert (g.stock, g.enemies, g.score) == (0, [], 15)
    g.weapon, g.stock, g.cooldown = 'laser', 1, 0
    g.enemies = [Enemy('crab', 30, g.y, 2), Enemy('crab', 60, g.y, 2)]
    g.special()
    for _ in range(spaceimpact.BEAM):
        g.step()
    assert g.enemies == []
    g.weapon, g.stock, g.cooldown = 'bomb', 1, 0
    g.enemies = [Enemy('crab', g.x + 30, g.y - 4, 2), Enemy('crab', g.x + 34, g.y + 6, 2)]
    g.special()
    for _ in range(spaceimpact.FUSE):
        g.step()
    assert g.enemies == []


@pytest.mark.parametrize('level', range(1, 6))
def test_each_level_ends_with_its_boss(level):
    g = quiet()
    g.start(level)
    g.banner, g.waiting = 0, 0
    g.step()
    assert [enemy.kind for enemy in g.enemies] == [BOSS_ORDER[(level - 1) % len(BOSS_ORDER)]]
    fired = 0
    for _ in range(300):
        g.blink = 99  # untouchable: only the boss's moves are tested
        g.step()
        fired = max(fired, len(g.enemy_shots))
    assert fired and g.enemies[0].x < lcd.WIDTH
    g.enemies[0].hp = 1
    g.missiles = [[g.enemies[0].x - 4, g.enemies[0].y + 2]]
    g.step()
    assert (g.level, g.notice) == (level + 1, f'Level {level + 1}')
    assert g.pickups[0][2] == 'heart'


def ground(g):
    canvas = lcd.Canvas(g.height)
    g.draw_ground(canvas)
    return {(x, y) for y, row in enumerate(canvas.px) for x, on in enumerate(row) if on}


def test_the_ground_scrolls_and_does_no_harm():
    g = quiet()
    g.y = H - len(SHIP)  # down among the hills
    before = ground(g)
    assert before and min(y for _, y in before) >= spaceimpact.TOP + H - 15
    g.step()
    g.step()  # one pixel
    after = ground(g)
    assert {(x, y) for x, y in after if x < lcd.WIDTH - 1} == {(x - 1, y) for x, y in before if x > 0}
    for _ in range(1000):
        g.step()
    assert g.lives == spaceimpact.LIVES and len(g.ridge) < 400


def test_status_bar_is_pixels():
    g = Game(random.Random(0))
    g.weapon, g.stock, g.score = 'bomb', 7, 120
    lines = ''.join(text for _, text in lcd.render(g, 0, False, 80, 24)).split('\n')
    assert len(lines) <= 24 and max(map(len, lines)) <= 80
    assert '♥' not in ''.join(lines) and set(lines[1]) <= set(' ▀▄█')
    assert any('Level 1' in line for line in lines)
    assert 'needs a 80x21' in lcd.render(g, 0, False, 80, 20)[0][1]
