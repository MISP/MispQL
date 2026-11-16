import random

from mispql import lcd, snake
from mispql.snake import LEFT, RIGHT, UP, Game


def game(cells, heading=RIGHT, food=(0, 8)):
    g = Game(random.Random(0))
    g.snake, g.heading, g.food = list(cells), heading, food
    return g


def test_moves_and_wraps():
    g = game([(snake.WIDTH - 1, 4), (snake.WIDTH - 2, 4)])
    g.step()
    assert g.snake == [(0, 4), (snake.WIDTH - 1, 4)]


def test_turns_are_queued_and_reversing_is_ignored():
    g = game([(5, 4), (4, 4)])
    for direction in LEFT, UP, LEFT:
        g.turn(direction)
    assert g.turns == [UP, LEFT]
    g.step()
    g.step()
    assert g.snake[0] == (4, 3)


def test_food_scores_and_grows():
    g = game([(5, 4), (4, 4)], food=(6, 4))
    g.step()
    assert g.score == snake.FOOD_POINTS and g.food not in g.snake
    g.step()
    assert len(g.snake) == 3


def test_biting_yourself_ends_the_game_but_the_tail_moves_away():
    g = game([(5, 4), (5, 5), (4, 5), (4, 4)], heading=LEFT)
    g.step()
    assert not g.over
    g = game([(5, 4), (5, 5), (4, 5), (4, 4), (3, 4)], heading=LEFT)
    g.step()
    assert g.over


def test_a_creature_comes_every_few_foods_and_leaves():
    g = game([(5, 4), (4, 4)], food=(6, 4))
    g.eaten = snake.BONUS_EVERY - 1
    g.step()
    assert g.bonus and g.bonus_left == snake.BONUS_TICKS
    g.bonus, g.food = ((7, 4), 0), (0, 8)
    g.step()
    assert g.bonus is None and g.score == snake.FOOD_POINTS + snake.BONUS_TICKS
    g.bonus, g.bonus_left = ((0, 0), 1), 2
    g.step()
    g.step()
    assert g.bonus is None and g.score == snake.FOOD_POINTS + snake.BONUS_TICKS


def test_screen_fits_the_terminal():
    assert snake.WIDTH * snake.CELL + 2 * snake.MARGIN == lcd.WIDTH
    assert snake.HEIGHT * snake.CELL + 2 * snake.MARGIN == lcd.HEIGHT
    g = game([(0, 0), (snake.WIDTH - 1, 0), (snake.WIDTH - 1, snake.HEIGHT - 1)], food=(3, 3))
    g.bonus, g.bonus_left, g.over = ((5, 5), 2), 7, True
    lines = ''.join(text for _, text in lcd.render(g, 10, False, 80, 24)).split('\n')
    assert len(lines) <= 24 and max(map(len, lines)) <= 80
    assert any('Game over!' in line for line in lines) and '   7 ' in lines[1]
    assert 'Snake II needs a' in lcd.render(g, 10, False, 79, 24)[0][1]
