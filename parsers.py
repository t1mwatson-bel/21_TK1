import re

from datetime import datetime

from config import (
    CARD_VALUES,
    SUITS,
    SUIT_ALIASES,
    DIGIT_RANKS,
    GAME_CYCLE,
    TARGET_OFFSET,
    MOSCOW_TZ,
)


# =====================================================================
# РЕГУЛЯРКА ДЛЯ КАРТ: 10 / 2-9 / A J Q K + масть
# =====================================================================

CARD_RE = re.compile(
    r"(10|[2-9AJQK])\s*"
    r"(\u2660|\u2663|\u2666|\u2665)"
    r"\ufe0f?"
)


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
        "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "J", "Q", "K", "A",
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
# ПАРСИНГ КАРТ ИЗ ТЕКСТА
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
        result.append({"rank": rank, "suit": suit})
    return result


# =====================================================================
# ПАРСИНГ ИГРОВОГО СООБЩЕНИЯ
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

    return {
        "game_number": game_number,
        "game_id": game_id,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "is_draw": is_draw,
        "is_ochko": is_ochko,
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
    print(f"👤 P: {game['player_score']} ({cards_to_text(player)})", flush=True)
    print(f"🎰 D: {game['dealer_score']} ({cards_to_text(dealer)})", flush=True)

    if game.get("is_draw"):
        print("🔰 #X — НИЧЬЯ", flush=True)
    if game.get("is_ochko"):
        print("⭕ #O — ОЧКО", flush=True)

    print("────────────────────────────────────", flush=True)


# =====================================================================
# СДВИГ НОМЕРА ИГРЫ
# =====================================================================

def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % GAME_CYCLE) + 1


# =====================================================================
# ТРИГГЕР: 2 КАРТЫ ДИЛЕРА, ПЕРВАЯ ЦИФРА 2..9 С УЧЕТОМ ЧЕТНОСТИ
# =====================================================================

def find_trigger(game):
    """
    Ищем триггер.
    Условия:
      - у дилера ровно 2 карты
      - берем первую карту-цифру 2..9
      - ЧЕТНОСТЬ ИГРЫ И ЦИФРЫ ДОЛЖНА СОВПАДАТЬ!
    """
    dealer = game.get("dealer_cards", [])

    if len(dealer) != 2:
        return None

    current_game = int(game["game_number"])
    game_is_even = (current_game % 2 == 0)  # Четная ли игра?

    trigger_card = None

    for card in dealer:
        rank = normalize_rank(card.get("rank"))
        if rank in DIGIT_RANKS:
            digit = int(rank)
            # Проверка на совпадение четности
            if (digit % 2 == 0) == game_is_even:
                trigger_card = card
                break

    if not trigger_card:
        return None

    digit = int(normalize_rank(trigger_card.get("rank")))
    predicted_suit = normalize_suit(trigger_card.get("suit"))

    if not predicted_suit:
        return None

    # Ближайшая игра СТРОГО больше текущей с концом = digit
    target_game = current_game + ((digit - current_game) % 10)
    if target_game == current_game:
        target_game += 10

    return {
        "trigger_card":   card_to_text(trigger_card),
        "current_game":   current_game,
        "target_game":    target_game,
        "dogon":          target_game - current_game,
        "predicted_suit": predicted_suit,
    }


# =====================================================================
# ПОИСК МАСТИ У ИГРОКА
# =====================================================================

def find_suit_in_player(game, suit):
    target = normalize_suit(suit)
    if not target:
        return None
    for card in game.get("player_cards", []):
        if normalize_suit(card.get("suit")) == target:
            return card_to_text(card)
    return None