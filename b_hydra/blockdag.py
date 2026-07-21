"""
blockdag.py — экспериментальный blockDAG (GHOSTDAG-lite) для B-hydra.

ОТДЕЛЬНЫЙ экспериментальный модуль РЯДОМ с линейной цепочкой (`blockchain.py`),
а НЕ замена ей. Рабочий консенсус (PoW, UTXO, ECDSA/гибрид) не меняется.

Зачем DAG. В линейной цепочке два блока, найденных почти одновременно,
конфликтуют — один осиротеет, его работа пропадёт. В blockDAG блок ссылается
на НЕСКОЛЬКО родителей (все текущие вершины-tips), поэтому параллельные блоки
все попадают в структуру — растёт число блоков в секунду в сетевом окружении.
Саму крипту (хеш/подпись) DAG не ускоряет — только пропускную способность.

GHOSTDAG-lite. Чтобы из DAG получить ОДИН порядок транзакций, блоки красятся:
  * СИНИЕ — хорошо связанные с основой (у блока в антиконусе ≤ k синих);
  * КРАСНЫЕ — аномальные (например, придержанные атакующим): их антиконус
    к синему набору больше k. Красные остаются в DAG, но идут в порядке позже.
«Выбранный родитель» блока — самый «синий» (макс. blue_score); цепочка
выбранных родителей — аналог самой тяжёлой цепи. blue_score = число синих
предков — по нему выбирается голова и разрешаются развилки (как total_work).

Упрощения относительно полного GHOSTDAG (Kaspa) отмечены как «lite»: порядок
внутри уровня — детерминированная топологическая сортировка (синие раньше
красных, затем по blue_score и id). Этого достаточно для учебной демонстрации
структуры и коллективной устойчивости; байт-совместимость с Kaspa не заявляется.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing
from .blockchain import genesis_target_for
from .merkle import merkle_root

GENESIS_ID = "genesis"
CHECKPOINT_GENESIS = "0" * 128       # prev_hash первой контрольной точки


class DagBlock:
    """Узел DAG: id, несколько родителей и полезная нагрузка (например, tx)."""

    def __init__(self, block_id: str, parents, payload=None):
        self.id = block_id
        self.parents = list(parents)      # id родителей (0..N)
        self.payload = payload
        # Заполняется GHOSTDAG при добавлении:
        self.selected_parent = None       # самый «синий» родитель
        self.blue_score = 0               # число синих предков
        self.mergeset_blues = []          # синие в мержсете (+ выбранный родитель)
        self.mergeset_reds = []           # красные в мержсете

    def __repr__(self):
        return f"<DagBlock {self.id[:10]} parents={len(self.parents)} blue={self.blue_score}>"


class BlockDAG:
    """blockDAG с упорядочиванием GHOSTDAG-lite.

    k — параметр устойчивости (допустимый размер антиконуса синего блока).
    Чем больше k, тем шире «честная» одновременность считается нормальной.
    """

    def __init__(self, k: int = 3, genesis_id: str = GENESIS_ID):
        self.k = k
        self.genesis_id = genesis_id       # якорь: обычный генезис или хеш чекпойнта
        self.blocks: dict[str, DagBlock] = {}
        self._past: dict[str, frozenset] = {}
        self._referenced: set = set()      # id, на которые кто-то ссылается
        self._counter = 0
        genesis = DagBlock(genesis_id, [], payload="genesis")
        genesis.blue_score = 0
        genesis.mergeset_blues = [genesis_id]
        self.blocks[genesis_id] = genesis
        self._past[genesis_id] = frozenset()

    # --- Построение ------------------------------------------------------
    def tips(self):
        """Текущие вершины DAG: блоки, на которые ещё никто не сослался."""
        return [b for b in self.blocks if b not in self._referenced]

    def _new_id(self, parents, payload) -> str:
        """Контент-адресуемый id блока (наш SHA-512 от родителей и нагрузки)."""
        self._counter += 1
        material = "|".join(sorted(parents)) + f"|{payload}|{self._counter}"
        return hashing.sha512(material)[:32]

    def add_block(self, parents=None, payload=None) -> DagBlock:
        """Добавляет блок. По умолчанию ссылается на ВСЕ текущие вершины —
        так параллельно найденные блоки объединяются (в этом суть DAG)."""
        if parents is None:
            parents = self.tips()
        parents = list(parents)
        for p in parents:
            if p not in self.blocks:
                raise ValueError(f"неизвестный родитель: {p}")
        block = DagBlock(self._new_id(parents, payload), parents, payload)
        self._past[block.id] = frozenset(self._compute_past(parents))
        self.blocks[block.id] = block
        self._referenced.update(parents)
        self._color(block)
        return block

    def _compute_past(self, parents) -> set:
        """Все предки (транзитивно через родителей)."""
        past, stack = set(), list(parents)
        while stack:
            p = stack.pop()
            if p in past:
                continue
            past.add(p)
            stack.extend(self.blocks[p].parents)
        return past

    # --- Отношения в DAG -------------------------------------------------
    def _in_past(self, x: str, y: str) -> bool:
        return x in self._past[y]

    def _in_anticone(self, x: str, y: str) -> bool:
        """x в антиконусе y: не предок, не потомок и не сам y."""
        return x != y and not self._in_past(x, y) and not self._in_past(y, x)

    def _mergeset(self, block: DagBlock):
        """Блоки, которые block «сливает»: его прошлое минус прошлое выбранного
        родителя (и сам выбранный родитель), в топологическом порядке."""
        sp = block.selected_parent
        exclude = set(self._past[sp]) | {sp}
        merge = [b for b in self._past[block.id] if b not in exclude]
        # топологический порядок: меньше предков — раньше, затем по id
        merge.sort(key=lambda b: (len(self._past[b]), b))
        return merge

    # --- GHOSTDAG: раскраска и blue_score --------------------------------
    def _blues_including(self, bid: str) -> set:
        """Все синие блоки в прошлом bid, включая сам bid (по цепи выбранных
        родителей и их мержсет-синим). Против этого набора проверяется
        антиконус кандидата — иначе аномальный блок нельзя отличить от синего."""
        blues, node = {bid}, bid
        while node is not None:
            blk = self.blocks[node]
            blues.update(blk.mergeset_blues)
            node = blk.selected_parent
        return blues

    def _color(self, block: DagBlock) -> None:
        if not block.parents:
            block.blue_score = 0
            block.mergeset_blues = [block.id]
            return
        # Выбранный родитель — максимальный blue_score (tie-break по id).
        block.selected_parent = max(
            block.parents, key=lambda p: (self.blocks[p].blue_score, p))
        sp = block.selected_parent
        # Полный синий набор прошлого выбранного родителя (растёт по мержсету).
        check_set = self._blues_including(sp)
        mergeset_blues, reds = [], []
        for cand in self._mergeset(block):
            if cand == sp:
                continue
            if self._is_blue_candidate(cand, check_set):
                mergeset_blues.append(cand)
                check_set.add(cand)
            else:
                reds.append(cand)
        block.mergeset_blues = [sp] + mergeset_blues
        block.mergeset_reds = reds
        # blue_score = синие предки = blue_score(sp) + сам sp + новые синие
        block.blue_score = self.blocks[sp].blue_score + 1 + len(mergeset_blues)

    def _blue_anticone_size(self, b: str, blues) -> int:
        return sum(1 for x in blues if self._in_anticone(x, b))

    def _is_blue_candidate(self, cand: str, blues) -> bool:
        """k-кластерное правило: кандидат синий, если у него в антиконусе не
        более k синих И добавление не выводит ни один синий блок за предел k."""
        anticone = 0
        for b in blues:
            if self._in_anticone(cand, b):
                anticone += 1
                if anticone > self.k:
                    return False
                if self._blue_anticone_size(b, blues) + 1 > self.k:
                    return False
        return True

    # --- Виртуальная вершина и порядок -----------------------------------
    def _virtual(self) -> DagBlock:
        """Виртуальный блок над всеми вершинами — задаёт голову и раскраску DAG."""
        v = DagBlock("virtual", self.tips())
        self._past["virtual"] = frozenset(self._compute_past(v.parents))
        self._color(v)
        del self._past["virtual"]
        return v

    def selected_tip(self) -> str:
        """Голова DAG — вершина с максимальным blue_score (как самая тяжёлая цепь)."""
        return max(self.tips(), key=lambda p: (self.blocks[p].blue_score, p))

    def selected_chain(self):
        """Цепочка выбранных родителей от генезиса до головы (аналог main chain)."""
        chain, node = [], self.selected_tip()
        while node is not None:
            chain.append(node)
            node = self.blocks[node].selected_parent
        return list(reversed(chain))

    def blue_blocks(self) -> set:
        """Множество синих блоков всего DAG (по раскраске виртуальной вершины)."""
        blues, node = set(), self._virtual()
        while node is not None:
            blues.update(node.mergeset_blues)
            sp = node.selected_parent
            node = self.blocks.get(sp) if sp else None
        blues.discard("virtual")
        return blues

    def order(self):
        """Тотальный порядок блоков (консенсус-упорядочивание GHOSTDAG-lite).

        Топологическая сортировка: блок выходит только после всех родителей;
        среди готовых синие идут раньше красных, затем по blue_score и id.
        Возвращает список id — по нему приложение линеаризует транзакции."""
        blues = self.blue_blocks()
        indeg = {b: 0 for b in self.blocks}
        children = {b: [] for b in self.blocks}
        for b, blk in self.blocks.items():
            for p in blk.parents:
                children[p].append(b)
                indeg[b] += 1

        def key(bid):
            return (0 if bid in blues else 1, self.blocks[bid].blue_score, bid)

        ready = sorted([b for b in self.blocks if indeg[b] == 0], key=key)
        order = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for ch in children[cur]:
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    # вставляем с сохранением приоритета
                    ready.append(ch)
                    ready.sort(key=key)
        return order

    # --- Метрики ---------------------------------------------------------
    def stats(self) -> dict:
        blues = self.blue_blocks()
        return {
            "blocks": len(self.blocks),
            "tips": len(self.tips()),
            "blue": len(blues),
            "red": len(self.blocks) - len(blues),
            "selected_tip_blue_score": self.blocks[self.selected_tip()].blue_score,
            "k": self.k,
        }


class Checkpoint:
    """Контрольная точка: линейный сегмент цепочки, запечатанный PoW.

    Когда майнер находит нонс, текущий DAG упорядочивается GHOSTDAG'ом и
    «схлопывается» в эту точку. Цепочка контрольных точек (prev_hash → hash,
    подтверждённая PoW) — линейный блокчейн из DAG-пачек.
    """

    def __init__(self, index, prev_hash, blocks, difficulty, miner):
        self.index = index
        self.prev_hash = prev_hash
        self.blocks = blocks               # [(block_id, payload)] в порядке GHOSTDAG
        self.difficulty = difficulty
        self.target = genesis_target_for(difficulty)
        self.miner = miner
        # Меркл-корень порядка — фиксирует ровно эту линеаризацию DAG.
        self.order_root = merkle_root([bid for bid, _ in blocks] or ["empty"])
        self.nonce = 0
        self.attempts = 0
        self.hash = None

    def _header(self, nonce):
        return (f"{self.index}{self.prev_hash}{self.order_root}"
                f"{self.miner}{nonce}")

    def mine(self):
        """Perebor nonce, пока хеш заголовка (как число) не станет ≤ target.

        Это и есть тот самый «майнер нашёл нонс» — момент финализации DAG."""
        nonce = 0
        while True:
            h = hashing.sha512(self._header(nonce))
            self.attempts = nonce + 1
            if int(h, 16) <= self.target:
                self.nonce = nonce
                self.hash = h
                return self
            nonce += 1

    def is_sealed(self) -> bool:
        """PoW корректен и заголовок соответствует хешу."""
        return (self.hash is not None
                and self.hash == hashing.sha512(self._header(self.nonce))
                and int(self.hash, 16) <= self.target)


class HybridDagChain:
    """Гибрид: блоки живут в DAG, а PoW-нонс финализирует их в линейную цепь.

    Между контрольными точками блоки накапливаются в DAG (параллельно, без
    сирот). `finalize()` = «майнер нашёл нонс»: текущий DAG упорядочивается,
    запечатывается контрольной точкой и схлопывается — новый DAG продолжает
    строиться от хеша этой точки. Так пропускная способность DAG сочетается с
    линейным, PoW-подтверждённым порядком.
    """

    def __init__(self, k: int = 3, difficulty: int = 2):
        self.k = k
        self.difficulty = difficulty
        self.checkpoints: list[Checkpoint] = []
        self.dag = BlockDAG(k=k)           # текущий накопительный DAG

    @property
    def height(self) -> int:
        return len(self.checkpoints)

    @property
    def pending(self) -> int:
        """Блоков в DAG сверх якоря (ждут финализации)."""
        return len(self.dag.blocks) - 1

    def add_block(self, payload=None, parents=None) -> DagBlock:
        """Добавляет блок в текущий (накопительный) DAG."""
        return self.dag.add_block(parents, payload)

    def finalize(self, miner: str = "miner") -> Checkpoint:
        """«Майнер нашёл нонс»: упорядочивает DAG и запечатывает контрольную
        точку PoW, затем схлопывает DAG (новый якорь — хеш точки)."""
        anchor = self.dag.genesis_id
        ordered = [(bid, self.dag.blocks[bid].payload)
                   for bid in self.dag.order() if bid != anchor]
        prev = self.checkpoints[-1].hash if self.checkpoints else CHECKPOINT_GENESIS
        cp = Checkpoint(self.height, prev, ordered, self.difficulty, miner)
        cp.mine()
        self.checkpoints.append(cp)
        # Схлопываем DAG: новый якорь — хеш контрольной точки.
        self.dag = BlockDAG(k=self.k, genesis_id=cp.hash)
        return cp

    def linear_order(self):
        """Полный линейный порядок блоков по всем финализированным точкам."""
        order = []
        for cp in self.checkpoints:
            order.extend(bid for bid, _ in cp.blocks)
        return order

    def is_valid(self) -> bool:
        """Цепочка контрольных точек связна (prev_hash) и запечатана PoW."""
        prev = CHECKPOINT_GENESIS
        for i, cp in enumerate(self.checkpoints):
            if cp.index != i or cp.prev_hash != prev or not cp.is_sealed():
                return False
            prev = cp.hash
        return True

    def stats(self) -> dict:
        return {
            "checkpoints": self.height,
            "pending_dag": self.pending,
            "finalized_blocks": sum(len(cp.blocks) for cp in self.checkpoints),
            "difficulty": self.difficulty,
        }


if __name__ == "__main__":
    # Демонстрация: параллельные блоки, которые линейная цепь осиротила бы.
    dag = BlockDAG(k=3)
    tip = GENESIS_ID
    total_linear = 1        # линейная цепь приняла бы по 1 блоку за раунд
    rounds, width = 5, 3
    for r in range(rounds):
        base = dag.tips()
        # width параллельных блоков за раунд — все ссылаются на текущие вершины
        for w in range(width):
            dag.add_block(parents=base, payload=f"round{r}-block{w}")
        total_linear += 1   # линейная приняла бы лишь 1 из width

    s = dag.stats()
    print("blockDAG (GHOSTDAG-lite) демо")
    print(f"  раундов: {rounds} × {width} параллельных блоков")
    print(f"  блоков в DAG    : {s['blocks']} (синих {s['blue']}, красных {s['red']})")
    print(f"  приняла бы линейная цепь: ~{total_linear}")
    print(f"  ускорение по блокам/раунд: ×{width}")
    print(f"  голова (blue_score): {s['selected_tip_blue_score']}")
    print(f"  длина порядка: {len(dag.order())} (тотальный порядок построен)")

    # Гибрид: DAG накапливает блоки, PoW-нонс финализирует их в линейную цепь.
    print("\nГибрид «DAG → PoW → линейная цепь»")
    chain = HybridDagChain(k=3, difficulty=2)
    for cp_round in range(3):
        base = chain.dag.tips()
        for w in range(width):                 # параллельные блоки в DAG
            chain.add_block(payload=f"cp{cp_round}-b{w}", parents=base)
        cp = chain.finalize(miner="BHYminer")  # «майнер нашёл нонс» → чекпойнт
        print(f"  чекпойнт #{cp.index}: {cp.pending if False else len(cp.blocks)} "
              f"блоков DAG запечатаны | nonce={cp.nonce} "
              f"(перебор {cp.attempts}) | hash {cp.hash[:12]}…")
    st = chain.stats()
    print(f"  линейная цепь: {st['checkpoints']} чекпойнтов, "
          f"{st['finalized_blocks']} блоков всего | валидна: {chain.is_valid()}")
