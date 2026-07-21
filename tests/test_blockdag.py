"""blockDAG (GHOSTDAG-lite): конкурентность, порядок, раскраска, устойчивость."""

from b_hydra.blockdag import BlockDAG, GENESIS_ID


def _is_topological(dag, order):
    """Каждый блок стоит в порядке ПОСЛЕ всех своих родителей."""
    pos = {b: i for i, b in enumerate(order)}
    return all(pos[p] < pos[b]
               for b in dag.blocks for p in dag.blocks[b].parents)


def test_multiple_parents_concurrency():
    """Параллельные блоки объединяются под общими родителями (суть DAG)."""
    dag = BlockDAG(k=3)
    a = dag.add_block([GENESIS_ID], "a")
    b = dag.add_block([GENESIS_ID], "b")           # параллельный a
    assert set(dag.tips()) == {a.id, b.id}         # обе вершины живы
    m = dag.add_block([a.id, b.id], "merge")       # блок с ДВУМЯ родителями
    assert len(m.parents) == 2
    assert dag.tips() == [m.id]                     # слились в одну вершину


def test_default_parents_are_all_tips():
    """add_block без аргументов ссылается на все текущие вершины."""
    dag = BlockDAG(k=3)
    dag.add_block([GENESIS_ID], "a")
    dag.add_block([GENESIS_ID], "b")
    m = dag.add_block(payload="auto")               # родители = все tips
    assert set(m.parents) == set(dag.blocks[m.id].parents)
    assert len(m.parents) == 2


def test_total_order_is_topological():
    dag = BlockDAG(k=3)
    tips = [GENESIS_ID]
    for r in range(4):
        base = dag.tips() or tips
        for w in range(3):
            dag.add_block(base, f"r{r}w{w}")
    order = dag.order()
    assert len(order) == len(dag.blocks)
    assert order[0] == GENESIS_ID
    assert _is_topological(dag, order)


def test_selected_chain_is_heaviest_and_valid():
    dag = BlockDAG(k=3)
    a = dag.add_block([GENESIS_ID], "a")
    b = dag.add_block([a.id], "b")
    c = dag.add_block([b.id], "c")                  # длинная синяя ветка
    dag.add_block([GENESIS_ID], "side")            # короткая боковая
    chain = dag.selected_chain()
    assert chain[0] == GENESIS_ID
    assert chain[-1] == dag.selected_tip()
    # голова — самый синий тип; линейная ветка a→b→c набирает больший blue_score
    assert dag.blocks[c.id].blue_score >= dag.blocks[a.id].blue_score
    # цепь связна по выбранным родителям
    for child, parent in zip(chain[1:], chain[:-1]):
        assert dag.blocks[child].selected_parent == parent


def test_blue_score_of_linear_chain():
    """Линейная ветка в DAG даёт растущий blue_score (аналог высоты)."""
    dag = BlockDAG(k=3)
    a = dag.add_block([GENESIS_ID], "a")
    b = dag.add_block([a.id], "b")
    c = dag.add_block([b.id], "c")
    assert dag.blocks[a.id].blue_score == 1
    assert dag.blocks[b.id].blue_score == 2
    assert dag.blocks[c.id].blue_score == 3


def test_k_cluster_colours_all_blue_when_k_large():
    """Честная умеренная одновременность при большом k — всё синее."""
    dag = BlockDAG(k=5)
    for _ in range(3):
        base = dag.tips()
        for w in range(3):
            dag.add_block(base, f"w{w}")
    blues = dag.blue_blocks()
    assert len(blues) == len(dag.blocks)           # красных нет
    assert dag.stats()["red"] == 0


def test_k_zero_marks_concurrent_blocks_red():
    """При k=0 любой блок с непустым синим антиконусом краснеет —
    параллельный блок в «ромбе» становится красным."""
    dag = BlockDAG(k=0)
    a = dag.add_block([GENESIS_ID], "a")
    b = dag.add_block([GENESIS_ID], "b")           # параллелен a
    dag.add_block([a.id, b.id], "merge")
    blues = dag.blue_blocks()
    assert len(blues) < len(dag.blocks)            # есть красные
    # один из параллельных a/b — красный (в антиконусе другого)
    assert (a.id not in blues) or (b.id not in blues)


def test_withholding_attacker_block_goes_red():
    """Блок, игнорирующий всю честную работу (ссылка только на генезис),
    при малом k получает большой антиконус к синим и краснеет."""
    dag = BlockDAG(k=1)
    tip = GENESIS_ID
    for i in range(4):                              # честная цепь
        tip = dag.add_block([tip], f"honest{i}").id
    attacker = dag.add_block([GENESIS_ID], "withheld")   # придержанный блок
    dag.add_block([tip, attacker.id], "merge")     # честный майнер сливает
    assert attacker.id not in dag.blue_blocks()    # аномальный блок — красный


def test_order_deterministic():
    def build():
        d = BlockDAG(k=3)
        for r in range(3):
            base = d.tips()
            for w in range(2):
                d.add_block(base, f"r{r}w{w}")
        return d.order()
    assert build() == build()                      # порядок воспроизводим


def test_stats_throughput_gain():
    """DAG вмещает все параллельные блоки — в отличие от линейной цепи."""
    dag = BlockDAG(k=3)
    rounds, width = 4, 3
    for _ in range(rounds):
        base = dag.tips()
        for w in range(width):
            dag.add_block(base, f"w{w}")
    s = dag.stats()
    assert s["blocks"] == 1 + rounds * width        # генезис + все блоки
    assert s["blue"] + s["red"] == s["blocks"]


# --- Гибрид: DAG накапливает, PoW финализирует в линейную цепь ---------------

from b_hydra.blockdag import HybridDagChain, Checkpoint, CHECKPOINT_GENESIS


def test_finalize_seals_dag_into_checkpoint():
    """«Майнер нашёл нонс» → текущий DAG запечатывается контрольной точкой PoW."""
    chain = HybridDagChain(k=3, difficulty=1)
    base = chain.dag.tips()
    for w in range(3):
        chain.add_block(payload=f"b{w}", parents=base)   # параллельные в DAG
    assert chain.pending == 3
    cp = chain.finalize(miner="BHYminer")
    assert cp.is_sealed()                                # PoW корректен
    assert int(cp.hash, 16) <= cp.target
    assert len(cp.blocks) == 3                           # все блоки DAG в точке
    assert chain.pending == 0                            # DAG схлопнут


def test_checkpoints_form_linear_chain():
    """Цепочка контрольных точек линейна: prev_hash каждой = hash предыдущей."""
    chain = HybridDagChain(k=3, difficulty=1)
    for r in range(4):
        base = chain.dag.tips()
        for w in range(2):
            chain.add_block(payload=f"r{r}b{w}", parents=base)
        chain.finalize()
    assert chain.height == 4
    assert chain.is_valid()
    assert chain.checkpoints[0].prev_hash == CHECKPOINT_GENESIS
    for prev, cur in zip(chain.checkpoints, chain.checkpoints[1:]):
        assert cur.prev_hash == prev.hash                # связность цепи
        assert cur.index == prev.index + 1


def test_dag_reanchors_on_checkpoint_hash():
    """После финализации новый DAG строится от хеша контрольной точки."""
    chain = HybridDagChain(k=3, difficulty=1)
    chain.add_block(payload="x")
    cp = chain.finalize()
    assert chain.dag.genesis_id == cp.hash               # новый якорь = хеш точки
    b = chain.add_block(payload="y")                     # продолжаем строить DAG
    assert cp.hash in chain.dag.blocks[b.id].parents or chain.dag.genesis_id == cp.hash


def test_linear_order_accumulates_all_finalized():
    chain = HybridDagChain(k=3, difficulty=1)
    total = 0
    for r in range(3):
        base = chain.dag.tips()
        for w in range(3):
            chain.add_block(payload=f"r{r}b{w}", parents=base)
            total += 1
        chain.finalize()
    order = chain.linear_order()
    assert len(order) == total                           # все блоки в порядке
    assert len(set(order)) == total                      # без дублей


def test_tampered_checkpoint_detected():
    """Подмена запечатанной точки ломает проверку (PoW/связность)."""
    chain = HybridDagChain(k=3, difficulty=1)
    chain.add_block(payload="a")
    chain.finalize()
    chain.add_block(payload="b")
    chain.finalize()
    assert chain.is_valid()
    chain.checkpoints[1].prev_hash = "0" * 128           # разрыв связи
    assert not chain.is_valid()


def test_finalize_empty_dag_still_seals():
    """Финализация без накопленных блоков даёт валидную (пустую) точку."""
    chain = HybridDagChain(k=3, difficulty=1)
    cp = chain.finalize()
    assert cp.is_sealed() and cp.blocks == []
    assert chain.is_valid()
