import re

from datetime import datetime

from config import (
    CARD_VALUES,
    SUITS,
    SUIT_ALIASES,
    GAME_CYCLE,
    MOSCOW_TZ,
)


# =====================================================================
# РЕГУЛЯРКА ДЛЯ КАРТ
# =====================================================================

CARD_RE = re.compile(
    r"(10|[2-9AJQKA])"
    r"(♠|♣|♦|♥)"
    r"\ufe0f?"
)


# =====================================================================
# РАНГИ ДЛЯ ТРИГГЕРА (только старшие)
# =====================================================================

TRIGGER_RANKS = {"J", "Q", "K", "A"}


# =====================================================================
# НОРМАЛИЗАЦИЯ
# =====================================================================

def normalize_suit(suit):
    if suit is None:
        return None

    value = str(suit).strip()

    if value in SUIT_ALIASES:
        return SUIT_ALIASES[value]

    value = value.replace("\ufe0f", "")

    return SUITS.get(value)


def normalize_rank(rank):
    if rank is None:
        return None

    rank = str(rank).strip().upper()

    if rank == "А":
        rank = "A"

    if rank in {
        "2", "3", "4", "5",
        "6", "7", "8", "9",
        "10", "J", "Q", "K", "A",
    }:
        return rank

    return None


# =====================================================================
# КАРТЫ → ТЕКСТ
# =====================================================================

def card_to_text(card):
    if not card:
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}"


def cards_to_text(cards):
    result = []

    for card in cards:
        value = card_to_text(card)

        if value:
            result.append(value)

    return " ".join(result)


# =====================================================================
# СЧЁТ CYBER 21
# =====================================================================

def cyber21_score(cards):
    total = 0

    for card in cards:
        rank = normalize_rank(card.get("rank"))

        if rank in CARD_VALUES:
            total += CARD_VALUES[rank]

    return total


# =====================================================================
# ПАРСИНГ КАРТ
# =====================================================================

def parse_cards(text):
    result = []

    if not text:
        return result

    for match in CARD_RE.finditer(text):

        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": suit,
        })

    return result


# =====================================================================
# ПАРСИНГ ИГРЫ
# =====================================================================

def parse_game_message(text):
    if not text:
        return None

    number_match = re.search(r"#N(\d+)", text)

    if not number_match:
        return None

    game_number = int(number_match.group(1))

    groups = re.findall(r"\(([^()]*)\)", text)

    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    if not player_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    is_draw = bool(re.search(r"#X\b", text))
    is_ochko = bool(re.search(r"#O\b", text))
    is_finished = bool(re.search(r"✅", text))
    is_r = bool(re.search(r"#R\b", text))

    return {
        "game_number": game_number,
        "game_id": game_id,

        "player_cards": player_cards,
        "dealer_cards": dealer_cards,

        "player_score": player_score,
        "dealer_score": dealer_score,

        "is_draw": is_draw,
        "is_ochko": is_ochko,
        "is_finished": is_finished,
        "is_r": is_r,

        "raw_text": text,

        "received_at": datetime.now(MOSCOW_TZ).isoformat(),
    }


# =====================================================================
# ЛОГ ИГРЫ
# =====================================================================

def log_game(game):

    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    print("", flush=True)
    print("────────────────────────────────────", flush=True)
    print(f"🎮 ИГРА #N{game['game_number']}", flush=True)
    print(
        f"👤 P: {game['player_score']} "
        f"({cards_to_text(player)})",
        flush=True,
    )
    print(
        f"🎰 D: {game['dealer_score']} "
        f"({cards_to_text(dealer)})",
        flush=True,
    )

    if game.get("is_draw"):
        print("🔰 #X — НИЧЬЯ", flush=True)

    if game.get("is_ochko"):
        print("⭕ #O — ОЧКО", flush=True)

    if game.get("is_r"):
        print("⚠️ #R — АНОМАЛИЯ", flush=True)

    if game.get("is_finished"):
        print("✅ — ИГРА ЗАВЕРШЕНА", flush=True)

    print("────────────────────────────────────", flush=True)


# =====================================================================
# СДВИГ НОМЕРА ИГРЫ
# =====================================================================

def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % GAME_CYCLE) + 1


# =====================================================================
# ПОЛУЧЕНИЕ КАРТ ИГРОКА
# =====================================================================

def get_first_player_card(game):
    player = game.get("player_cards", [])
    if not player:
        return None
    return player[0]


def get_first_player_rank(game):
    card = get_first_player_card(game)
    if not card:
        return None
    return normalize_rank(card.get("rank"))


def get_last_player_card(game):
    player = game.get("player_cards", [])
    if not player:
        return None
    return player[-1]


def get_last_player_rank(game):
    card = get_last_player_card(game)
    if not card:
        return None
    return normalize_rank(card.get("rank"))


# =====================================================================
# ТРИГГЕР 21 КЛАССИК
# =====================================================================

TRIGGER_FIRST_RANKS = {"6", "7", "8", "9"}   # первая карта игрока
TRIGGER_LAST_RANKS  = {"J", "Q", "K", "A"}   # последняя карта игрока

# Очки старших карт — ТОЛЬКО для расчёта сдвига
LAST_RANK_POINTS = {
    "J": 2,
    "Q": 3,
    "K": 4,
    "A": 11,
}


def find_trigger_21_classic(game):
    """
    Триггер 21 КЛАССИК.

    Условия:
        1. Игра завершена (✅).
        2. Первая карта игрока — 6, 7, 8 или 9.
        3. Последняя карта игрока — J, Q, K или A.

    Сдвиг:
        offset      = LAST_RANK_POINTS[последняя_карта] + 1
        target_game = game_number + offset

    Прогноз:
        На целевой игре у ИГРОКА (в любой позиции д0–д3)
        должен встретиться ранг ПЕРВОЙ карты триггера.
    """

    # 1. Игра завершена
    if not game.get("is_finished"):
        return None

    player = game.get("player_cards", [])

    if not player:
        return None

    # 2. Первая карта — 6..9
    first_rank = normalize_rank(player[0].get("rank"))

    if first_rank not in TRIGGER_FIRST_RANKS:
        return None

    # 3. Последняя карта — J/Q/K/A
    last_rank = normalize_rank(player[-1].get("rank"))

    if last_rank not in TRIGGER_LAST_RANKS:
        return None

    # Сдвиг
    offset = LAST_RANK_POINTS[last_rank] + 1

    return {
        "trigger_card": card_to_text(player[0]),
        "last_card":    card_to_text(player[-1]),

        "predicted_rank": first_rank,

        "target_offset": offset,
        "target_game":   add_game_offset(
            game["game_number"],
            offset,
        ),
    }


# =====================================================================
# ПОИСК РАНГА У ИГРОКА (результат прогноза)
# =====================================================================

def find_rank_in_player(game, target_rank):
    """
    Ищет карту с нужным РАНГОМ (любая масть)
    ТОЛЬКО у игрока — в любой позиции (д0, д1, д2, д3).
    """

    if not target_rank:
        return None

    target_rank = normalize_rank(target_rank)

    if not target_rank:
        return None

    for index, card in enumerate(game.get("player_cards", [])):

        rank = normalize_rank(card.get("rank"))

        if rank == target_rank:
            return {
                "card":  card_to_text(card),
                "where": "player",
                "index": index,   # 0 = д0, 1 = д1, ...
            }

    return None