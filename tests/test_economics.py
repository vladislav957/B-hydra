"""Тесты экономики: награда за блок, халвинг, потолок эмиссии."""

from b_hydra.blockchain import (
    Blockchain, BLOCK_TIME_SECONDS, HALVING_INTERVAL, MAX_SUPPLY,
    MINING_END_HEIGHT, SECONDS_PER_YEAR, TARGET_END_YEAR,
)
from b_hydra.economics import (
    block_reward, emission_schedule, halving_years, mining_end_year,
    total_supply_after, year_of_height,
)


def test_initial_reward():
    assert block_reward(0) == 50.0


def test_halving():
    assert block_reward(HALVING_INTERVAL) == 25.0
    assert block_reward(HALVING_INTERVAL * 2) == 12.5


def test_reward_zero_after_many_halvings():
    assert block_reward(HALVING_INTERVAL * 64) == 0.0


def test_total_emission_hits_cap():
    total = sum(emission for _, _, emission in emission_schedule(64))
    assert round(total) == MAX_SUPPLY     # 31 000 000 BHY


def test_supply_capped():
    assert total_supply_after(10 ** 9) <= MAX_SUPPLY


def test_reward_halves_at_interval():
    # Награда строго 50 и делится пополам на границе интервала халвинга.
    assert block_reward(HALVING_INTERVAL - 1) == 50.0
    assert block_reward(HALVING_INTERVAL) == 25.0


def test_mining_ends_around_target_year():
    # Майнеры получают награду примерно до TARGET_END_YEAR (~3000).
    assert round(mining_end_year()) == TARGET_END_YEAR == 3000


def test_consensus_and_economics_reward_agree():
    """Консенсусный Blockchain.block_reward и модульный economics.block_reward
    обязаны совпадать на всех границах эпох — иначе разъедется эмиссия и
    проверка coinbase начнёт отвергать честные блоки (или пропускать печать)."""
    bc = Blockchain(difficulty=1)
    for era in range(0, 66):
        for offset in (-1, 0, 1):
            height = era * HALVING_INTERVAL + offset
            if height >= 0:
                assert bc.block_reward(height) == block_reward(height)


def test_exact_total_emission():
    """Полная эмиссия по расписанию — фиксированное значение (недобор до
    круглых 31M — следствие округления до 1e-8, как у Bitcoin ~20.99999M)."""
    total = sum(block_reward(era * HALVING_INTERVAL) * HALVING_INTERVAL
                for era in range(64))
    assert round(total, 8) == 30_999_999.9969
    assert total < MAX_SUPPLY                       # потолок никогда не превышен


def test_emission_ends_at_expected_height():
    """34 халвинга до обнуления награды → конец эмиссии на этой высоте."""
    assert MINING_END_HEIGHT == 34 * HALVING_INTERVAL == 10_540_000
    assert block_reward(MINING_END_HEIGHT) == 0.0
    assert block_reward(MINING_END_HEIGHT - HALVING_INTERVAL) > 0.0


def test_mined_supply_matches_schedule():
    """Фактически начеканенное = сумме запланированных наград (майнер берёт
    ровно награду блока; комиссии — переработка старых монет, не эмиссия)."""
    from b_hydra.node import BHydraNode
    from b_hydra.wallet import generate_wallet
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    for _ in range(5):
        node.mine_pending(miner.address)
    scheduled = sum(node.blockchain.block_reward(b.index)
                    for b in node.blockchain.chain[1:])
    assert node.get_balance(miner.address) == scheduled == node.blockchain.total_supply


def test_halving_period_in_years_is_pinned():
    """Халвинг раз в ~29 лет, а не «раз в 4 года».

    Четыре года — цифра Bitcoin, и к нашим параметрам она отношения не имеет:
    у нас 310 000 блоков по ~48,6 мин, то есть почти 29 лет. Число попадает
    в белую книгу и в описания сети, поэтому закрепляем его тестом — иначе
    документы и код разъезжаются молча, как уже случилось.
    """
    years = halving_years()
    assert 28.0 < years < 29.5, years
    assert round(years) == 29
    # Оно обязано быть СЛЕДСТВИЕМ констант, а не отдельным числом.
    assert years == HALVING_INTERVAL * BLOCK_TIME_SECONDS / SECONDS_PER_YEAR


def test_halving_is_a_rule_about_height_not_about_time():
    """Правило консенсуса — по ВЫСОТЕ; годы лишь следствие целевого времени.

    Если бы халвинг считался по календарю, узел с неверными часами насчитал бы
    другую награду и отверг бы честный блок. Награда обязана зависеть ТОЛЬКО
    от высоты.
    """
    import inspect

    from b_hydra import economics

    source = inspect.getsource(economics.block_reward)
    for forbidden in ("time", "year", "GENESIS_YEAR", "SECONDS_PER_YEAR"):
        assert forbidden not in source, forbidden
    # Награда на одной высоте всегда одна и та же, сколько ни спрашивай.
    assert block_reward(HALVING_INTERVAL) == block_reward(HALVING_INTERVAL)


def test_first_halvings_land_on_the_expected_years():
    """Календарь эпох — то, что идёт в белую книгу таблицей."""
    assert round(year_of_height(0)) == 2026
    assert round(year_of_height(HALVING_INTERVAL)) == 2055
    assert round(year_of_height(HALVING_INTERVAL * 2)) == 2083


def test_whitepaper_numbers_match_the_code():
    """Числа в ECONOMICS.md обязаны совпадать с константами.

    Документ существует ровно для того, чтобы его переносили в белую книгу, —
    значит, ошибка в нём расходится дальше кода. «Каждые 4 года» жило в белой
    книге именно так: написали один раз, а проверить было нечем.
    """
    import os
    import re

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "ECONOMICS.md")
    text = open(path, encoding="utf-8").read()

    assert f"{HALVING_INTERVAL:,}".replace(",", " ") in text        # 310 000
    assert f"{MAX_SUPPLY:,}".replace(",", " ") in text              # 31 000 000
    assert f"{MINING_END_HEIGHT:,}".replace(",", " ") in text       # 10 540 000
    assert str(TARGET_END_YEAR) in text
    assert f"{halving_years():.1f}".replace(".", ",") in text       # 28,6 года
    assert f"{BLOCK_TIME_SECONDS / 60:.1f}".replace(".", ",") in text  # 48,6 мин
    assert str(round(BLOCK_TIME_SECONDS)) in text                   # 2916 с
    assert str(MINING_END_HEIGHT // HALVING_INTERVAL) in text       # 34 халвинга
    # Календарь эпох из таблицы.
    for era in range(4):
        assert str(round(year_of_height(era * HALVING_INTERVAL))) in text
    # И главное: «4 года» из старой редакции остались только как ОШИБКА,
    # то есть в таблице исправлений, а не как утверждение о нашей сети.
    for line in text.splitlines():
        if "4 года" in line:
            assert "Bitcoin" in line or "Было" in line, line
    assert re.search(r"по высоте", text, re.I)
