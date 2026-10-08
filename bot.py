import os
import sys
import re
import json
import time

from datetime import datetime, time as dtime, timedelta

from config import (
    BOT_TOKEN,
    CHANNEL_PROGNOZ,
    CHANNEL_STATS,
    PREDICTIONS_FILE,
    POLL_INTERVAL,
    DOGON_GAMES,
    CLEANUP_HOUR,
    CLEANUP_MINUTE,
    MAX_GAMES_CACHE,
    MAX_PREDICTIONS_STORED,
    MOSCOW_TZ,
)

from parsers import (
    parse_game_message,
    log_game,
    add_game_offset,
    find_trigger_v2,
    build_prediction_v2,
    get_first_player_rank,
    get_first_player_suit,
    find_card_in_game,
    normalize_suit,
    card_to_text,
    cards_to_text,
)

from coefs import get_dealer_cf, get_player_cf

from telegram_api import (
    delete_webhook,
    telegram_send,
    telegram_edit,
    telegram_delete,
    process_telegram_updates,
    load_offset,
)

from bank import (
    load_bank,
    save_bank,
    get_current_bet,
    bet_for_dogon,
    apply_dogon_bet,
    apply_win,
    apply_lose,
    apply_return,
)


# =====================================================================
# ПРОВЕРКА ENV
# =====================================================================

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True)
    sys.exit(1)

if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан", flush=True)
    sys.exit(1)


# =====================================================================
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# =====================================================================

games_cache = {}
predictions = []

last_cleanup_date = None


# =====================================================================
# ТАЙМАУТ
# =====================================================================

PREDICTION_TIMEOUT_MINUTES = 20


# =====================================================================
# ОЧИСТКА
# =====================================================================

def should_cleanup_now(now=None):

    global last_cleanup_date

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    if last_cleanup_date == now.date():
        return False

    cleanup_time = dtime(CLEANUP_HOUR, CLEANUP_MINUTE)

    if now.time() >= cleanup_time:
        return True

    return False


def cleanup_nightly():

    global games_cache
    global predictions
    global last_cleanup_date

    now = datetime.now(MOSCOW_TZ)

    print("", flush=True)
    print("🧹 НОЧНАЯ ОЧИСТКА (03:00)", flush=True)

    games_count = len(games_cache)
    games_cache.clear()

    print(f"   🗑️ games_cache: удалено {games_count}", flush=True)

    last_cleanup_date = now.date()

    print("✅ Ночная очистка завершена", flush=True)
    print("", flush=True)


# =====================================================================
# ПРОГНОЗЫ — LOAD / SAVE
# =====================================================================

def load_predictions():

    global predictions

    try:

        if not os.path.exists(PREDICTIONS_FILE):
            predictions = []
            return

        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        predictions = data if isinstance(data, list) else []

    except Exception as e:

        print(f"⚠️ Ошибка чтения {PREDICTIONS_FILE}: {e}", flush=True)
        predictions = []


def save_predictions():

    try:

        tmp = PREDICTIONS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:

        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


# =====================================================================
# АКТИВНЫЙ ПРОГНОЗ
# =====================================================================

def has_active_prediction():

    for prediction in predictions:

        if prediction.get("status") == "pending":
            return True

    return False


# =====================================================================
# ФОРМАТ СООБЩЕНИЙ
# =====================================================================

def make_prediction_message(prediction):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    return f"🎯 Игра: <b>#N{target}</b>: {card}"


def make_result_message(prediction, result, found_card=None):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    if result == "win":

        where = found_card.get("where") if found_card else None

        if where == "dealer":
            where_text = " (дилер)"
        elif where == "player":
            where_text = " (игрок)"
        else:
            where_text = ""

        return f"🎯 Игра: <b>#N{target}</b>: {card} ✅{where_text}"

    elif result == "lose":
        return f"🎯 Игра: <b>#N{target}</b>: {card} ❌"

    elif result == "return":
        return f"🎯 Игра: <b>#N{target}</b>: {card} ♻️"

    return f"🎯 Игра: <b>#N{target}</b>: {card} ⚠️"


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(trigger_game):
    """
    Создаёт прогноз по новому алгоритму.

    trigger_game — игра, где первая карта игрока = J/Q/K/A.
    """

    if has_active_prediction():
        return None

    trigger = find_trigger_v2(trigger_game)

    if not trigger:
        return None

    trigger_number = trigger_game["game_number"]
    trigger_id = trigger_game.get("game_id")

    rank = trigger["rank"]

    # Ищем игру триггер − 3 для масти
    suit_game_number = add_game_offset(trigger_number, -3)
    suit_game = games_cache.get(suit_game_number)

    if not suit_game:
        print(
            f"⏳ Триггер #N{trigger_number} ({rank}) — "
            f"ждём игру #N{suit_game_number} для масти",
            flush=True,
        )
        return None

    prediction_data = build_prediction_v2(trigger_game, suit_game)

    if not prediction_data:
        print(
            f"⚠️ Триггер #N{trigger_number}: "
            f"не удалось построить прогноз",
            flush=True,
        )
        return None

    predicted_card = prediction_data["predicted_card"]
    predicted_rank = prediction_data["predicted_rank"]
    predicted_suit = prediction_data["predicted_suit"]
    target_offset = prediction_data["target_offset"]

    target_number = add_game_offset(trigger_number, target_offset)

    # Проверка дубля
    for old in predictions:

        if old.get("status") != "pending":
            continue

        if (
            old.get("target_number") == target_number
            and old.get("predicted_card") == predicted_card
        ):
            return None

    base_bet = get_current_bet()

    prediction = {

        "algorithm": "first_player_card_v2",

        "trigger_number": trigger_number,
        "trigger_game_id": trigger_id,
        "trigger_card": trigger["trigger_card"],

        "suit_game_number": suit_game_number,
        "suit_card": cards_to_text([suit_game["player_cards"][0]]) if suit_game.get("player_cards") else None,

        "predicted_rank": predicted_rank,
        "predicted_suit": predicted_suit,
        "predicted_card": predicted_card,

        "target_offset": target_offset,
        "target_number": target_number,

        "base_bet": base_bet,

        "status": "pending",
        "dogon": 0,

        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "sent_at": None,
        "closed_at": None,
        "message_id": None,
        "result_game": None,
        "found_card": None,
        "close_reason": None,
    }

    predictions.append(prediction)
    save_predictions()

    print("", flush=True)
    print("🔮 ПРОГНОЗ СОЗДАН", flush=True)
    print(f"📌 Триггер: #N{trigger_number}", flush=True)
    print(f"🃏 Первая карта: {trigger['trigger_card']}", flush=True)
    print(
        f"🎨 Масть из #N{suit_game_number}: {predicted_suit}",
        flush=True,
    )
    print(f"🎯 Прогноз: {predicted_card}", flush=True)
    print(f"🎯 Цель: #N{target_number}", flush=True)

    send_prediction(prediction)

    return prediction


# =====================================================================
# ОТПРАВКА ПРОГНОЗА
# =====================================================================

def send_prediction(prediction):

    message = make_prediction_message(prediction)

    message_id = telegram_send(message)

    if not message_id:

        print(
            f"❌ Не удалось отправить #N{prediction['target_number']}",
            flush=True,
        )

        return False

    prediction["message_id"] = message_id
    prediction["sent_at"] = datetime.now(MOSCOW_TZ).isoformat()

    # Списываем ставку за Д0
    apply_dogon_bet(prediction, 0)

    save_predictions()

    print(
        f"📤 ПРОГНОЗ ОТПРАВЛЕН: "
        f"#N{prediction['target_number']} "
        f"{prediction['predicted_card']}",
        flush=True,
    )

    return True


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def check_predictions():

    changed = False
    now = datetime.now(MOSCOW_TZ)

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get("target_number")

        if not target:
            continue

        # Таймаут
        sent_at_str = prediction.get("sent_at")

        if sent_at_str:

            try:

                sent_at = datetime.fromisoformat(sent_at_str)

                if (
                    now - sent_at
                    > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES)
                ):

                    prediction["status"] = "return"
                    prediction["close_reason"] = "timeout"
                    prediction["closed_at"] = now.isoformat()

                    telegram_edit(
                        prediction.get("message_id"),
                        make_result_message(prediction, "return"),
                    )

                    apply_return(prediction)

                    print(
                        f"♻️ ВОЗВРАТ #N{target} "
                        f"(timeout > {PREDICTION_TIMEOUT_MINUTES} мин)",
                        flush=True,
                    )

                    changed = True
                    continue

            except Exception:
                pass

        # Текущий догон
        current_dogon = prediction.get("dogon", 0)

        game_number = add_game_offset(target, current_dogon)
        game = games_cache.get(game_number)

        if not game:
            continue

        found = find_card_in_game(
            game,
            prediction["predicted_card"],
        )

        if found:

            prediction["status"] = "win"
            prediction["result_game"] = game_number
            prediction["found_card"] = found
            prediction["closed_at"] = now.isoformat()

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "win", found),
            )

            apply_win(prediction, current_dogon, found)

            print("", flush=True)
            print(f"✅ ПРОГНОЗ ЗАШЁЛ #N{target}", flush=True)
            print(
                f"🎯 Карта: {prediction['predicted_card']}",
                flush=True,
            )
            print(
                f"🃏 Найдена: {found['card']} ({found['where']})",
                flush=True,
            )
            print(f"🔄 Догон: Д{current_dogon}", flush=True)
            print(f"🎰 Игра: #N{game_number}", flush=True)

            changed = True
            continue

        # Не нашли — переходим на следующий догон
        if current_dogon < DOGON_GAMES:

            next_dogon = current_dogon + 1

            apply_lose(prediction, current_dogon)

            prediction["dogon"] = next_dogon

            print(
                f"❌ Д{current_dogon} проиграл, "
                f"переходим на Д{next_dogon}",
                flush=True,
            )

            changed = True

        else:

            apply_lose(prediction, current_dogon)

            prediction["status"] = "lose"
            prediction["closed_at"] = now.isoformat()

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "lose"),
            )

            print("", flush=True)
            print(f"❌ ПРОГНОЗ НЕ ЗАШЁЛ #N{target}", flush=True)

            changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА ИГР TELEGRAM
# =====================================================================

def on_game_message(game_number, text, is_edited):

    game = parse_game_message(text)

    if not game:
        # Не удалось распарсить (например, "Ожидание") — пропускаем
        return

    # Обновляем/добавляем игру в кэш
    is_new = game_number not in games_cache
    games_cache[game_number] = game

    if is_new:
        log_game(game)

    # Пытаемся создать прогноз (только для финализированных игр)
    if is_new:
        create_prediction(game)


# =====================================================================
# ОЧИСТКА
# =====================================================================

def cleanup_games_cache():

    if len(games_cache) <= MAX_GAMES_CACHE:
        return

    items = sorted(
        games_cache.items(),
        key=lambda kv: kv[1].get("received_at", ""),
    )

    for number, _ in items[:-MAX_GAMES_CACHE]:
        del games_cache[number]


def cleanup_predictions():

    global predictions

    if len(predictions) > MAX_PREDICTIONS_STORED:

        predictions = predictions[-MAX_PREDICTIONS_STORED:]
        save_predictions()


# =====================================================================
# ГЛАВНЫЙ ЦИКЛ
# =====================================================================

def main():

    global predictions

    print("", flush=True)
    print("==================================================", flush=True)
    print("🚀 CYBER 21 — FIRST CARD PREDICTOR v2", flush=True)
    print("==================================================", flush=True)
    print("📡 Игры: CHANNEL_STATS", flush=True)
    print("📤 Прогнозы: CHANNEL_PROGNOZ", flush=True)
    print("🎯 Алгоритм: первая карта игрока J/Q/K/A", flush=True)
    print("🎯 Масть: от игры триггер − 3", flush=True)
    print("🎯 Сдвиг: +2 (фиксированный)", flush=True)
    print("💸 Ставок на прогноз: 2 (игрок + дилер)", flush=True)
    print(f"🔄 Догоны: Д0..Д{DOGON_GAMES}", flush=True)
    print(f"⏰ Таймаут → возврат: {PREDICTION_TIMEOUT_MINUTES} мин", flush=True)
    print("==================================================", flush=True)

    delete_webhook()
    load_bank()
    load_predictions()

    offset = load_offset()

    print(f"📌 Telegram offset: {offset}", flush=True)
    print(f"📊 Загружено прогнозов: {len(predictions)}", flush=True)
    print("==================================================", flush=True)
    print("🟢 БОТ ГОТОВ", flush=True)
    print("==================================================", flush=True)

    while True:

        try:

            if should_cleanup_now():
                cleanup_nightly()

            offset = process_telegram_updates(
                offset,
                on_game_message,
            )

            check_predictions()
            cleanup_games_cache()
            cleanup_predictions()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:

            print("\n🛑 Бот остановлен", flush=True)
            break

        except Exception as e:

            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(3)


# =====================================================================
# СТАРТ
# =====================================================================

if __name__ == "__main__":
    import threading
    import time

    threading.Thread(
        target=main,
        daemon=True,
    ).start()

    from web_server import start_web_server
    start_web_server()

    while True:
        time.sleep(60)
