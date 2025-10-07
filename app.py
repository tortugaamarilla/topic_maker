import streamlit as st
import streamlit.components.v1 as components
import re
import os
import io
import base64
import time
import ssl
import json
import logging
from typing import Tuple, Optional, Dict, List, Any
from PIL import Image
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import anthropic
import yt_dlp
from datetime import datetime
import random

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Для лучшей совместимости с yt-dlp
ssl._create_default_https_context = ssl._create_unverified_context


class ProxyManager:
    """Менеджер для управления ротацией прокси-серверов"""
    
    def __init__(self, config_file: str = "proxy_config.json"):
        self.config_file = config_file
        self.proxies = []
        self.current_index = 0
        self.max_retries_per_proxy = 2
        self.request_timeout = 30
        self.last_successful_proxy = None
        self.proxy_attempts = {}  # Счетчик попыток для каждого прокси
        self.load_config()
        
    def load_config(self):
        """Загружает конфигурацию прокси из файла"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    proxy_strings = config.get('proxies', [])
                    self.max_retries_per_proxy = config.get('max_retries_per_proxy', 2)
                    self.request_timeout = config.get('request_timeout', 30)
                    
                    # Парсим прокси из строкового формата host:port:username:password
                    self.proxies = []
                    for proxy_str in proxy_strings:
                        if isinstance(proxy_str, str) and ':' in proxy_str:
                            parts = proxy_str.strip().split(':')
                            if len(parts) == 4:
                                self.proxies.append({
                                    "host": parts[0],
                                    "port": int(parts[1]),
                                    "username": parts[2],
                                    "password": parts[3]
                                })
                            else:
                                logger.warning(f"Неверный формат прокси: {proxy_str}")
                        elif isinstance(proxy_str, dict):
                            # Поддержка старого формата для обратной совместимости
                            self.proxies.append(proxy_str)
                    
                    # Инициализируем счетчики попыток
                    for i in range(len(self.proxies)):
                        self.proxy_attempts[i] = 0
                    logger.info(f"Загружено {len(self.proxies)} прокси-серверов из {self.config_file}")
            else:
                # Fallback к старому прокси если нет конфига
                self.proxies = [{
                    "host": "185.76.11.214",
                    "port": 80,
                    "username": "krdxwmej-18",
                    "password": "r0ajol0cnax6"
                }]
                logger.warning(f"Файл конфигурации {self.config_file} не найден, используется fallback прокси")
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации прокси: {e}")
            # Fallback к старому прокси в случае ошибки
            self.proxies = [{
                "host": "185.76.11.214",
                "port": 80,
                "username": "krdxwmej-18",
                "password": "r0ajol0cnax6"
            }]
    
    def get_proxy_url(self, index: int = None) -> str:
        """Возвращает URL прокси по индексу"""
        if not self.proxies:
            return None
        
        if index is None:
            index = self.current_index
            
        if 0 <= index < len(self.proxies):
            proxy = self.proxies[index]
            return f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
        return None
    
    def get_proxy_info(self, index: int = None) -> str:
        """Возвращает информацию о прокси для отображения"""
        if not self.proxies:
            return "No proxy"
        
        if index is None:
            index = self.current_index
            
        if 0 <= index < len(self.proxies):
            proxy = self.proxies[index]
            return f"{proxy['host']}:{proxy['port']} (user: {proxy['username'][:8]}...)"
        return "Unknown proxy"
    
    def get_next_proxy(self) -> Tuple[str, int]:
        """Возвращает следующий прокси и его индекс"""
        if not self.proxies:
            return None, -1
        
        # Переходим к следующему прокси
        self.current_index = (self.current_index + 1) % len(self.proxies)
        return self.get_proxy_url(), self.current_index
    
    def reset_proxy_attempts(self, index: int):
        """Сбрасывает счетчик попыток для прокси"""
        if index in self.proxy_attempts:
            self.proxy_attempts[index] = 0
    
    def increment_proxy_attempts(self, index: int) -> int:
        """Увеличивает счетчик попыток для прокси и возвращает текущее количество"""
        if index not in self.proxy_attempts:
            self.proxy_attempts[index] = 0
        self.proxy_attempts[index] += 1
        return self.proxy_attempts[index]
    
    def should_try_proxy(self, index: int) -> bool:
        """Проверяет, стоит ли пробовать данный прокси"""
        return self.proxy_attempts.get(index, 0) < self.max_retries_per_proxy
    
    def mark_successful(self, index: int):
        """Отмечает прокси как успешно использованный"""
        self.last_successful_proxy = index
        self.reset_proxy_attempts(index)
        if 'proxy_success_info' not in st.session_state:
            st.session_state.proxy_success_info = {}
        st.session_state.proxy_success_info['last_successful_index'] = index
        st.session_state.proxy_success_info['last_successful_info'] = self.get_proxy_info(index)
        st.session_state.proxy_success_info['timestamp'] = datetime.now().isoformat()
        logger.info(f"Прокси {self.get_proxy_info(index)} успешно использован")
    
    def get_all_proxies(self) -> List[Tuple[str, int]]:
        """Возвращает список всех прокси с их индексами в случайном порядке"""
        # Создаем список индексов и перемешиваем их
        indices = list(range(len(self.proxies)))
        random.shuffle(indices)
        return [(self.get_proxy_url(i), i) for i in indices]
    
    def shuffle_proxies(self):
        """Перемешивает список прокси для рандомизации"""
        if len(self.proxies) > 1:
            random.shuffle(self.proxies)
            # Сбрасываем счетчики после перемешивания
            self.proxy_attempts = {i: 0 for i in range(len(self.proxies))}
            logger.info("Список прокси перемешан")


# Создаем глобальный экземпляр ProxyManager
proxy_manager = ProxyManager()

# Старые функции для совместимости (будут удалены после рефакторинга)
def _get_proxy_url():
    """Совместимость со старым кодом"""
    return proxy_manager.get_proxy_url()

# Настройка страницы
st.set_page_config(
    page_title="Topic Maker",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Инициализация session_state
if 'video_id' not in st.session_state:
    st.session_state.video_id = None
if 'video_title' not in st.session_state:
    st.session_state.video_title = ""
if 'thumbnail_text' not in st.session_state:
    st.session_state.thumbnail_text = ""
if 'transcript' not in st.session_state:
    st.session_state.transcript = ""
if 'transcript_with_timestamps' not in st.session_state:
    st.session_state.transcript_with_timestamps = ""
if 'selected_model' not in st.session_state:
    st.session_state.selected_model = "Claude Sonnet 4.5"
if 'selected_model_preview' not in st.session_state:
    st.session_state.selected_model_preview = "Claude Sonnet 4.5"
if 'show_timestamps' not in st.session_state:
    st.session_state.show_timestamps = False
if 'synopsis_orig' not in st.session_state:
    st.session_state.synopsis_orig = ""
if 'synopsis_red' not in st.session_state:
    st.session_state.synopsis_red = ""
if 'need_rerun' not in st.session_state:
    st.session_state.need_rerun = False
# Хранение истории API запросов
if 'api_history_synopsis_orig' not in st.session_state:
    st.session_state.api_history_synopsis_orig = {}
if 'api_history_synopsis_red' not in st.session_state:
    st.session_state.api_history_synopsis_red = {}
if 'api_history_annotation_orig' not in st.session_state:
    st.session_state.api_history_annotation_orig = {}
if 'api_history_scenario' not in st.session_state:
    st.session_state.api_history_scenario = {}
# Поля для хранения аннотаций
if 'annotation_orig' not in st.session_state:
    st.session_state.annotation_orig = ""
# Поле для хранения сценария
if 'scenario' not in st.session_state:
    st.session_state.scenario = ""
# Поле для хранения температуры
if 'temperature' not in st.session_state:
    st.session_state.temperature = 0.7
if 'use_proxy' not in st.session_state:
    st.session_state.use_proxy = True
if 'transcript_method' not in st.session_state:
    st.session_state.transcript_method = "yt-dlp"
if 'subtitle_language' not in st.session_state:
    st.session_state.subtitle_language = "en"  # По умолчанию английский
# Поля для хранения саммари
if 'summary' not in st.session_state:
    st.session_state.summary = ""
if 'api_history_summary' not in st.session_state:
    st.session_state.api_history_summary = {}
# Поля для хранения комментариев
if 'comment_on_video' not in st.session_state:
    st.session_state.comment_on_video = ""
if 'api_history_comment_on_video' not in st.session_state:
    st.session_state.api_history_comment_on_video = {}
if 'reply_to_comment' not in st.session_state:
    st.session_state.reply_to_comment = ""
if 'api_history_reply_to_comment' not in st.session_state:
    st.session_state.api_history_reply_to_comment = {}
# Информация о прокси и попытках получения транскрипции
if 'proxy_success_info' not in st.session_state:
    st.session_state.proxy_success_info = {}
if 'transcript_attempts_log' not in st.session_state:
    st.session_state.transcript_attempts_log = []

# Функция для извлечения ID видео из URL YouTube
def extract_video_id(url):
    if not url:
        return None
    
    # Если пользователь ввел только ID видео
    if len(url) == 11 and re.match(r'^[A-Za-z0-9_-]{11}$', url):
        return url
    
    # Регулярное выражение для извлечения ID из различных форматов URL YouTube
    youtube_regex = (
        r'(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/\s]{11})'
    )
    match = re.search(youtube_regex, url)
    if match:
        return match.group(1)
    return None

# Функция для получения заголовка видео
def get_video_title(video_id):
    api_keys = []
    # Получение API ключей из секретов
    i = 1
    while True:
        key_name = f"YOUTUBE_API_KEY_{i}"
        try:
            if key_name in st.secrets:
                api_keys.append(st.secrets[key_name])
                i += 1
            else:
                break
        except:
            break
    
    if not api_keys:
        return f"Видео ID: {video_id}"
    
    # Пробуем каждый ключ по очереди
    for api_key in api_keys:
        try:
            youtube = build('youtube', 'v3', developerKey=api_key)
            request = youtube.videos().list(
                part="snippet",
                id=video_id
            )
            response = request.execute()
            
            if response['items']:
                return response['items'][0]['snippet']['title']
            else:
                return "Видео не найдено"
        except HttpError as e:
            if "quota" in str(e).lower():
                continue  # Пробуем следующий ключ, если превышена квота
            else:
                return f"Ошибка: {str(e)[:100]}"
    
    return "Все API ключи исчерпали квоту"

# Функция для получения транскрипции видео через yt-dlp
def get_video_transcript_ytdlp(video_id: str) -> Tuple[str, str]:
    """
    Получает транскрипцию видео через yt-dlp с ротацией прокси
    Возвращает: (текст_без_временных_меток, текст_с_временными_метками)
    """
    
    # Получаем выбранный язык
    selected_lang = st.session_state.get('subtitle_language', 'en')
    
    # Очищаем лог попыток для новой транскрипции
    st.session_state.transcript_attempts_log = []
    
    # Базовые опции yt-dlp
    base_ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        # Критически важные опции для субтитров
        'writesubtitles': False,  # Не сохраняем файлы
        'writeautomaticsub': False,  # Не сохраняем файлы
        'subtitlesformat': 'json3/srv1/srv2/srv3/ttml/vtt',
        'subtitleslangs': [selected_lang],  # Используем только выбранный язык
        # Дополнительные опции для надежности
        'nocheckcertificate': True,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.youtube.com/',
    }
    
    # Если прокси отключены, пробуем напрямую
    if not st.session_state.get('use_proxy', False):
        logger.info("Попытка получить транскрипцию без прокси")
        st.session_state.transcript_attempts_log.append({
            'proxy': 'Direct connection (no proxy)',
            'attempt': 1,
            'timestamp': datetime.now().isoformat()
        })
        
        with yt_dlp.YoutubeDL(base_ydl_opts) as ydl:
            try:
                info = ydl.extract_info(
                    f'https://www.youtube.com/watch?v={video_id}', 
                    download=False
                )
                result = _process_subtitles_info(info, selected_lang)
                if result[0] and not result[0].startswith("Субтитры на") and not result[0].startswith("Не удалось"):
                    st.session_state.transcript_attempts_log[-1]['success'] = True
                    logger.info("Транскрипция успешно получена без прокси")
                    return result
            except Exception as e:
                st.session_state.transcript_attempts_log[-1]['error'] = str(e)
                logger.warning(f"Ошибка получения транскрипции без прокси: {e}")
    
    # Пробуем с прокси из списка
    all_proxies = proxy_manager.get_all_proxies()
    total_attempts = 0
    max_total_attempts = len(all_proxies) * proxy_manager.max_retries_per_proxy
    
    for proxy_round in range(proxy_manager.max_retries_per_proxy):
        for proxy_url, proxy_index in all_proxies:
            if not proxy_manager.should_try_proxy(proxy_index):
                continue
                
            total_attempts += 1
            proxy_info = proxy_manager.get_proxy_info(proxy_index)
            
            logger.info(f"Попытка {total_attempts}/{max_total_attempts} с прокси: {proxy_info}")
            
            # Записываем попытку в лог
            attempt_log = {
                'proxy': proxy_info,
                'attempt': total_attempts,
                'timestamp': datetime.now().isoformat(),
                'proxy_index': proxy_index
            }
            st.session_state.transcript_attempts_log.append(attempt_log)
            
            # Копируем опции и добавляем прокси
            ydl_opts = base_ydl_opts.copy()
            ydl_opts['proxy'] = proxy_url
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    # Извлекаем информацию о видео
                    info = ydl.extract_info(
                        f'https://www.youtube.com/watch?v={video_id}', 
                        download=False
                    )
                    
                    result = _process_subtitles_info(info, selected_lang)
                    
                    # Проверяем, успешно ли получены субтитры
                    if result[0] and not result[0].startswith("Субтитры на") and not result[0].startswith("Не удалось"):
                        # Успех! Отмечаем прокси как успешный
                        proxy_manager.mark_successful(proxy_index)
                        st.session_state.transcript_attempts_log[-1]['success'] = True
                        logger.info(f"Транскрипция успешно получена с прокси {proxy_info} (попытка {total_attempts})")
                        return result
                    else:
                        st.session_state.transcript_attempts_log[-1]['error'] = "Субтитры недоступны"
                        
                except Exception as e:
                    error_msg = str(e)
                    st.session_state.transcript_attempts_log[-1]['error'] = error_msg
                    logger.warning(f"Ошибка с прокси {proxy_info}: {error_msg}")
                    
                    # Увеличиваем счетчик попыток для этого прокси
                    proxy_manager.increment_proxy_attempts(proxy_index)
                    
                    # Если это ошибка блокировки, пробуем следующий прокси
                    if "HTTP Error 429" in error_msg or "too many requests" in error_msg.lower():
                        logger.info(f"Прокси {proxy_info} заблокирован, переходим к следующему")
                        continue
    
    # Если все попытки исчерпаны
    logger.error(f"Не удалось получить транскрипцию после {total_attempts} попыток")
    lang_names = {'en': 'английском', 'es': 'испанском', 'pt': 'португальском', 'ru': 'русском'}
    lang_name = lang_names.get(selected_lang, selected_lang)
    return f"Не удалось получить транскрипцию после {total_attempts} попыток", f"Не удалось получить транскрипцию после {total_attempts} попыток"


def _process_subtitles_info(info: dict, selected_lang: str) -> Tuple[str, str]:
    """Обрабатывает информацию о субтитрах из yt-dlp"""
    try:
        # Приоритет: 1) обычные субтитры 2) автоматические
        subtitles = info.get('subtitles', {})
        automatic_captions = info.get('automatic_captions', {})
        
        # Объединяем оба источника
        all_subs = {**automatic_captions, **subtitles}
        
        # Сначала пытаемся получить субтитры на выбранном языке
        if selected_lang in all_subs and all_subs[selected_lang]:
            # Берем первый доступный формат
            for sub_format in all_subs[selected_lang]:
                if sub_format.get('url'):
                    return _fetch_and_parse_subtitles(
                        sub_format['url'], 
                        sub_format.get('ext', 'json3')
                    )
        
        # Если не нашли субтитры на выбранном языке
        lang_names = {'en': 'английском', 'es': 'испанском', 'pt': 'португальском', 'ru': 'русском'}
        lang_name = lang_names.get(selected_lang, selected_lang)
        return f"Субтитры на {lang_name} языке недоступны для этого видео", f"Субтитры на {lang_name} языке недоступны для этого видео"
    except Exception as e:
        logger.error(f"Ошибка обработки субтитров: {e}")
        return "Не удалось обработать субтитры", "Не удалось обработать субтитры"


def _fetch_and_parse_subtitles(url: str, format_type: str) -> Tuple[str, str]:
    """Загружает и парсит субтитры с использованием текущего прокси из ProxyManager"""
    try:
        # Используем прокси если включено
        proxies = {}
        if st.session_state.get('use_proxy', False):
            # Получаем текущий прокси из ProxyManager
            proxy_url = proxy_manager.get_proxy_url()
            if proxy_url:
                proxies = {'http': proxy_url, 'https': proxy_url}
                logger.info(f"Загрузка субтитров через прокси: {proxy_manager.get_proxy_info()}")
            else:
                logger.warning("ProxyManager не вернул прокси URL")
        else:
            logger.info("Загрузка субтитров без прокси")
        
        response = requests.get(url, proxies=proxies, timeout=proxy_manager.request_timeout)
        response.raise_for_status()
        
        # Парсим в зависимости от формата
        if 'json' in format_type:
            return _parse_json_subtitles(response.text)
        elif 'vtt' in format_type:
            return _parse_vtt_subtitles(response.text)
        else:
            # Пытаемся как JSON
            return _parse_json_subtitles(response.text)
            
    except requests.exceptions.ProxyError as e:
        logger.error(f"Ошибка прокси при загрузке субтитров: {e}")
        return "Ошибка прокси при загрузке субтитров", "Ошибка прокси при загрузке субтитров"
    except requests.exceptions.Timeout as e:
        logger.error(f"Таймаут при загрузке субтитров: {e}")
        return "Таймаут при загрузке субтитров", "Таймаут при загрузке субтитров"
    except Exception as e:
        logger.error(f"Ошибка загрузки субтитров: {e}")
        return "Ошибка загрузки субтитров", "Ошибка загрузки субтитров"


def _parse_json_subtitles(content: str) -> Tuple[str, str]:
    """Парсит JSON3 формат субтитров YouTube"""
    try:
        data = json.loads(content)
        
        full_text = []
        text_with_timestamps = []
        
        for event in data.get('events', []):
            if 'segs' not in event:
                continue
                
            # Собираем текст из сегментов
            text = ''.join([seg.get('utf8', '') for seg in event['segs'] if 'utf8' in seg])
            text = text.strip()
            
            if text:
                full_text.append(text)
                
                # Добавляем временную метку
                start_ms = event.get('tStartMs', 0)
                start_seconds = start_ms / 1000
                time_str = _format_time(start_seconds)
                text_with_timestamps.append(f"[{time_str}] {text}")
        
        return '\n'.join(full_text), '\n'.join(text_with_timestamps)
        
    except json.JSONDecodeError:
        # Если не JSON, пробуем как plain text
        lines = content.split('\n')
        return '\n'.join(lines), '\n'.join(lines)


def _parse_vtt_subtitles(content: str) -> Tuple[str, str]:
    """Парсит VTT формат субтитров"""
    lines = content.split('\n')
    
    full_text = []
    text_with_timestamps = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Ищем временные метки (00:00:00.000 --> 00:00:00.000)
        if '-->' in line:
            time_match = re.match(r'(\d{2}:\d{2}:\d{2})', line)
            if time_match:
                timestamp = time_match.group(1)
                
                # Следующие строки - текст
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip() and '-->' not in lines[i]:
                    text_lines.append(lines[i].strip())
                    i += 1
                
                if text_lines:
                    text = ' '.join(text_lines)
                    full_text.append(text)
                    text_with_timestamps.append(f"[{timestamp}] {text}")
                continue
        i += 1
    
    return '\n'.join(full_text), '\n'.join(text_with_timestamps)


def _format_time(seconds: float) -> str:
    """Форматирует время в MM:SS или HH:MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


# Функция для получения транскрипции видео (оригинальная через YouTubeTranscriptApi)
def get_video_transcript_api(video_id):
    """Получает транскрипцию через YouTubeTranscriptApi с ротацией прокси"""
    
    # Получаем выбранный язык
    selected_lang = st.session_state.get('subtitle_language', 'en')
    
    # Очищаем лог попыток для новой транскрипции
    st.session_state.transcript_attempts_log = []
    
    # Функция для попытки получить транскрипцию с заданными прокси
    def try_get_transcript(proxies_dict=None, proxy_info="Direct connection"):
        try:
            # Создаем экземпляр API
            api = YouTubeTranscriptApi()
            transcript_data = None
            
            # Пробуем получить транскрипцию на выбранном языке
            try:
                if proxies_dict:
                    transcript_data = YouTubeTranscriptApi.get_transcript(
                        video_id,
                        languages=[selected_lang],
                        proxies=proxies_dict
                    )
                else:
                    transcript_data = api.fetch(video_id, languages=[selected_lang])
            except:
                transcript_data = None
            
            # Если не получилось на выбранном языке, пробуем получить автоматические субтитры
            if not transcript_data:
                try:
                    if proxies_dict:
                        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, proxies=proxies_dict)
                    else:
                        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    
                    # Ищем субтитры на выбранном языке
                    for transcript in transcript_list:
                        if transcript.language_code == selected_lang:
                            if proxies_dict:
                                transcript_data = transcript.fetch(proxies=proxies_dict)
                            else:
                                transcript_data = transcript.fetch()
                            break
                except:
                    pass
            
            return transcript_data
            
        except Exception as e:
            logger.warning(f"Ошибка с {proxy_info}: {str(e)}")
            return None
    
    # Если прокси отключены, пробуем напрямую
    if not st.session_state.get('use_proxy', False):
        logger.info("Попытка получить транскрипцию через API без прокси")
        st.session_state.transcript_attempts_log.append({
            'proxy': 'Direct connection (no proxy)',
            'attempt': 1,
            'timestamp': datetime.now().isoformat()
        })
        
        transcript_data = try_get_transcript(None, "Direct connection")
        if transcript_data:
            st.session_state.transcript_attempts_log[-1]['success'] = True
            return _format_transcript_data(transcript_data)
        else:
            st.session_state.transcript_attempts_log[-1]['error'] = "Не удалось получить транскрипцию"
    
    # Пробуем с прокси из списка с ротацией
    all_proxies = proxy_manager.get_all_proxies()
    total_attempts = 0
    max_total_attempts = len(all_proxies) * proxy_manager.max_retries_per_proxy
    
    # Сохраняем текущее окружение прокси
    old_env_proxies = {
        'HTTP_PROXY': os.environ.get('HTTP_PROXY'),
        'HTTPS_PROXY': os.environ.get('HTTPS_PROXY'),
        'http_proxy': os.environ.get('http_proxy'),
        'https_proxy': os.environ.get('https_proxy'),
        'ALL_PROXY': os.environ.get('ALL_PROXY'),
        'all_proxy': os.environ.get('all_proxy')
    }
    
    try:
        for proxy_round in range(proxy_manager.max_retries_per_proxy):
            for proxy_url, proxy_index in all_proxies:
                if not proxy_manager.should_try_proxy(proxy_index):
                    continue
                
                total_attempts += 1
                proxy_info = proxy_manager.get_proxy_info(proxy_index)
                
                logger.info(f"API попытка {total_attempts}/{max_total_attempts} с прокси: {proxy_info}")
                
                # Записываем попытку в лог
                attempt_log = {
                    'proxy': proxy_info,
                    'attempt': total_attempts,
                    'timestamp': datetime.now().isoformat(),
                    'proxy_index': proxy_index
                }
                st.session_state.transcript_attempts_log.append(attempt_log)
                
                # Устанавливаем переменные окружения для прокси
                os.environ['HTTP_PROXY'] = proxy_url
                os.environ['HTTPS_PROXY'] = proxy_url
                os.environ['http_proxy'] = proxy_url
                os.environ['https_proxy'] = proxy_url
                os.environ['ALL_PROXY'] = proxy_url
                os.environ['all_proxy'] = proxy_url
                
                # Также создаем словарь прокси для явной передачи
                proxies_dict = {"http": proxy_url, "https": proxy_url}
                
                transcript_data = try_get_transcript(proxies_dict, proxy_info)
                
                if transcript_data:
                    # Успех! Отмечаем прокси как успешный
                    proxy_manager.mark_successful(proxy_index)
                    st.session_state.transcript_attempts_log[-1]['success'] = True
                    logger.info(f"Транскрипция успешно получена через API с прокси {proxy_info} (попытка {total_attempts})")
                    return _format_transcript_data(transcript_data)
                else:
                    st.session_state.transcript_attempts_log[-1]['error'] = "Не удалось получить транскрипцию"
                    proxy_manager.increment_proxy_attempts(proxy_index)
    
    finally:
        # Восстанавливаем окружение прокси
        for key, value in old_env_proxies.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    
    # Если все попытки исчерпаны
    logger.error(f"Не удалось получить транскрипцию через API после {total_attempts} попыток")
    lang_names = {'en': 'английском', 'es': 'испанском', 'pt': 'португальском', 'ru': 'русском'}
    lang_name = lang_names.get(selected_lang, selected_lang)
    return f"Не удалось получить транскрипцию после {total_attempts} попыток", f"Не удалось получить транскрипцию после {total_attempts} попыток"


def _format_transcript_data(transcript_data):
    """Форматирует данные транскрипции в два формата: с и без временных меток"""
    if transcript_data:
        # Унификация доступа к полям для двух вариантов (list[dict] и FetchedTranscript)
        def _get_text(entry):
            try:
                return str(entry.get('text', ''))
            except Exception:
                return str(getattr(entry, 'text', ''))
        def _get_start(entry):
            try:
                return float(entry.get('start', 0))
            except Exception:
                return float(getattr(entry, 'start', 0))
        
        # Версия без временных меток
        full_text = '\n'.join([_get_text(entry) for entry in transcript_data])
        
        # Версия с временными метками
        def format_time(seconds):
            """Форматирует время в формат MM:SS или HH:MM:SS"""
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            if hours > 0:
                return f"{hours:02d}:{minutes:02d}:{secs:02d}"
            else:
                return f"{minutes:02d}:{secs:02d}"
        
        text_with_timestamps = []
        for entry in transcript_data:
            start_time = _get_start(entry)
            text = _get_text(entry)
            text_with_timestamps.append(f"[{format_time(start_time)}] {text}")
        
        full_text_with_timestamps = '\n'.join(text_with_timestamps)
        
        return full_text, full_text_with_timestamps
    else:
        # Если не нашли субтитры
        selected_lang = st.session_state.get('subtitle_language', 'en')
        lang_names = {'en': 'английском', 'es': 'испанском', 'pt': 'португальском', 'ru': 'русском'}
        lang_name = lang_names.get(selected_lang, selected_lang)
        return f"Субтитры на {lang_name} языке недоступны для этого видео", f"Субтитры на {lang_name} языке недоступны для этого видео"


# Основная функция для получения транскрипции (выбирает метод)
def get_video_transcript(video_id):
    """Получает транскрипцию видео используя выбранный метод"""
    if st.session_state.get('transcript_method') == 'yt-dlp':
        return get_video_transcript_ytdlp(video_id)
    else:
        return get_video_transcript_api(video_id)

# Функция для получения текста с превью через Claude API
def get_thumbnail_text(video_id):
    try:
        # Получаем URL превью
        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
        response = requests.get(thumbnail_url)
        
        # Если maxresdefault не доступен, пробуем hqdefault
        if response.status_code != 200:
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            response = requests.get(thumbnail_url)
            
        if response.status_code != 200:
            return "Не удалось получить превью видео"
        
        # Открываем изображение
        image = Image.open(io.BytesIO(response.content))
        
        # Конвертируем изображение в base64
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        # Загружаем промпт для обработки изображения
        try:
            with open("prompt_get_thumbnail_text.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return "Не найден файл prompt_get_thumbnail_text.txt"
        
        # Инициализируем клиент Claude
        if "ANTHROPIC_API_KEY" not in st.secrets:
            # Отладочная информация о доступных секретах
            available_keys = list(st.secrets.keys()) if hasattr(st.secrets, 'keys') else []
            return f"API ключ Anthropic не найден. Доступные ключи: {available_keys}"
        
        # Получаем ключ и проверяем его формат
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        if not api_key or not api_key.startswith("sk-"):
            return f"Неверный формат API ключа Anthropic (должен начинаться с 'sk-')"
        
        # Создаем клиент только с API ключом, избегая дополнительных параметров от Streamlit
        import anthropic as anthropic_module
        client = anthropic_module.Anthropic(api_key=api_key)
        
        # Отправляем запрос к Claude
        message = client.messages.create(
            model=get_claude_model_preview(),  # Используем выбранную модель для обработки изображений
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt_text
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_base64
                            }
                        }
                    ]
                }
            ]
        )
        
        return message.content[0].text
    except Exception as e:
        # Более подробная информация об ошибке
        error_msg = f"Ошибка при обработке превью: {str(e)}"
        if "api_key" in str(e).lower():
            error_msg = "Проблема с API ключом Anthropic. Проверьте правильность ключа в секретах."
        return error_msg

# Функция для выбора основной модели Claude
def get_claude_model():
    model_mapping = {
        "Claude Opus 4.1": "claude-opus-4-1-20250805",
        "Claude Opus 4": "claude-opus-4-20250514",
        "Claude Sonnet 4.5": "claude-sonnet-4-5-20250929",
        "Claude Sonnet 4": "claude-sonnet-4-20250514",
        "Claude Sonnet 3.7": "claude-3-7-sonnet-20250219"
    }
    return model_mapping[st.session_state.selected_model]

# Функция для выбора модели Claude для превью
def get_claude_model_preview():
    model_mapping = {
        "Claude Opus 4.1": "claude-opus-4-1-20250805",
        "Claude Opus 4": "claude-opus-4-20250514",
        "Claude Sonnet 4.5": "claude-sonnet-4-5-20250929",
        "Claude Sonnet 4": "claude-sonnet-4-20250514",
        "Claude Sonnet 3.7": "claude-3-7-sonnet-20250219"
    }
    return model_mapping[st.session_state.selected_model_preview]

# Функция для получения максимального количества токенов для модели
def get_max_tokens():
    """Возвращает максимальное количество токенов для выбранной модели"""
    # Обновленные лимиты для новых моделей
    if st.session_state.selected_model in ["Claude Opus 4.1", "Claude Opus 4"]:
        return 32000  # 32K токенов
    elif st.session_state.selected_model in ["Claude Sonnet 4.5", "Claude Sonnet 4"]:
        return 64000  # 64K токенов
    else:  # Claude Sonnet 3.7
        return 64000  # 64K токенов

# Функция для получения максимального количества токенов для модели превью
def get_max_tokens_preview():
    """Возвращает максимальное количество токенов для выбранной модели превью"""
    # Обновленные лимиты для новых моделей
    if st.session_state.selected_model_preview in ["Claude Opus 4.1", "Claude Opus 4"]:
        return 32000  # 32K токенов
    elif st.session_state.selected_model_preview in ["Claude Sonnet 4.5", "Claude Sonnet 4"]:
        return 64000  # 64K токенов
    else:  # Claude Sonnet 3.7
        return 64000  # 64K токенов

# Функция для получения информации о модели
def get_model_info():
    """Возвращает информацию о максимальных размерах окон и стоимости для выбранной модели"""
    model_info = {
        "Claude Opus 4.1": {
            "input_window": "200K",
            "output_window": "32K",
            "input_cost": "$15",
            "output_cost": "$75"
        },
        "Claude Opus 4": {
            "input_window": "200K",
            "output_window": "32K", 
            "input_cost": "$15",
            "output_cost": "$75"
        },
        "Claude Sonnet 4.5": {
            "input_window": "200K",
            "output_window": "64K",
            "input_cost": "$3",
            "output_cost": "$15"
        },
        "Claude Sonnet 4": {
            "input_window": "200K",
            "output_window": "64K",
            "input_cost": "$3",
            "output_cost": "$15"
        },
        "Claude Sonnet 3.7": {
            "input_window": "200K",
            "output_window": "64K",
            "input_cost": "$3",
            "output_cost": "$15"
        }
    }
    return model_info.get(st.session_state.selected_model, {})

# Функция для создания синопсиса референса
def create_synopsis_orig():
    """Создает синопсис на основе транскрипции видео"""
    try:
        # Проверяем наличие транскрипции
        transcript = st.session_state.get('transcript', '')
        if not transcript:
            return None, "Нет транскрипции для создания синопсиса"
        
        # Загружаем промпт
        try:
            with open("prompt_synopsis_orig.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_synopsis_orig.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5  # Увеличиваем количество попыток
        base_delay = 10  # Начинаем с меньшей задержки
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))  # 10, 20, 40, 80 секунд
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с правильным лимитом токенов и streaming
                stream = client.messages.create(
                    model=get_claude_model(),  # Используем модель, выбранную пользователем
                    max_tokens=get_max_tokens(),  # Используем правильный лимит для модели
                    temperature=st.session_state.get('temperature', 0.7),  # Используем температуру из настроек
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ],
                    stream=True  # Используем streaming для больших запросов
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получен синопсис длиной {len(result)} символов")
                
                # Сохраняем историю запроса для синопсиса референса
                st.session_state.api_history_synopsis_orig = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': transcript[:500] + "..." if len(transcript) > 500 else transcript,
                        'full_system_prompt': prompt_text,
                        'full_user_message': transcript
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    # Специфичная ошибка превышения входных токенов
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Текст слишком большой. Попробуйте использовать видео с меньшей транскрипцией или подождите несколько минут."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании синопсиса: {str(e)}"

# Функция для создания измененного синопсиса
def create_synopsis_red(synopsis_orig):
    """Создает измененный синопсис на основе оригинального синопсиса"""
    try:
        # Проверяем наличие оригинального синопсиса
        if not synopsis_orig:
            return None, "Нет оригинального синопсиса для изменения"
        
        # Загружаем промпт
        try:
            with open("prompt_synopsis_red.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_synopsis_red.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5  # Увеличиваем количество попыток
        base_delay = 10  # Начинаем с меньшей задержки
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))  # 10, 20, 40, 80 секунд
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model(),  # Используем модель, выбранную пользователем
                    max_tokens=get_max_tokens(),  # Используем правильный лимит для модели
                    temperature=st.session_state.get('temperature', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": synopsis_orig
                        }
                    ],
                    stream=True  # Используем streaming для больших запросов
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получен синопсис длиной {len(result)} символов")
                
                # Сохраняем историю запроса для измененного синопсиса
                st.session_state.api_history_synopsis_red = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': synopsis_orig[:500] + "..." if len(synopsis_orig) > 500 else synopsis_orig,
                        'full_system_prompt': prompt_text,
                        'full_user_message': synopsis_orig
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Синопсис слишком большой. Подождите несколько минут и попробуйте снова."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании измененного синопсиса: {str(e)}"

# Функция для создания вариантов текста превью
def create_thumbnail_variants(thumbnail_text, synopsis_red):
    """Создает варианты текста превью на основе текста с превью и измененного синопсиса"""
    try:
        # Проверяем наличие необходимых данных
        if not thumbnail_text:
            return None, "Нет текста с превью для генерации вариантов"
        if not synopsis_red:
            return None, "Нет измененного синопсиса для генерации вариантов"
        
        # Загружаем промпт
        try:
            with open("prompt_generate_thumbnail_texts.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_generate_thumbnail_texts.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Формируем сообщение для модели
                user_message = f"Thumbnail reference text:\n{thumbnail_text}\n\nStory synopsis:\n{synopsis_red}"
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model_preview(),  # Используем модель для превью
                    max_tokens=get_max_tokens_preview(),  # Используем правильный лимит для модели
                    temperature=st.session_state.get('temperature_preview', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": user_message
                        }
                    ],
                    stream=True  # Используем streaming для больших запросов
                )
                
                # Собираем результат из streaming response
                result = ""
                for chunk in stream:
                    if chunk.type == "content_block_delta":
                        result += chunk.delta.text
                
                # Сохраняем информацию о запросе в session_state
                st.session_state.api_history_thumbnail_variants = {
                    'request': {
                        'model': get_claude_model_preview(),
                        'max_tokens': get_max_tokens_preview(),
                        'temperature': st.session_state.get('temperature_preview', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': user_message[:500] + "..." if len(user_message) > 500 else user_message,
                        'full_system_prompt': prompt_text,
                        'full_user_message': user_message
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Данные слишком большие. Подождите несколько минут и попробуйте снова."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании вариантов текста превью: {str(e)}"

# Функция для создания аннотации референса
def create_annotation_orig():
    """Создает аннотацию на основе транскрипции видео"""
    try:
        # Проверяем наличие транскрипции
        transcript = st.session_state.get('transcript', '')
        if not transcript:
            return None, "Нет транскрипции для создания аннотации"
        
        # Загружаем промпт
        try:
            with open("prompt_annotation.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_annotation.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=get_max_tokens(),
                    temperature=st.session_state.get('temperature', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ],
                    stream=True
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получена аннотация длиной {len(result)} символов")
                
                # Сохраняем историю запроса для аннотации референса
                st.session_state.api_history_annotation_orig = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': transcript[:500] + "..." if len(transcript) > 500 else transcript,
                        'full_system_prompt': prompt_text,
                        'full_user_message': transcript
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Текст слишком большой. Попробуйте использовать видео с меньшей транскрипцией или подождите несколько минут."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании аннотации: {str(e)}"


# Функция для создания сценария
def create_scenario():
    """Создает сценарий на основе транскрипции видео"""
    try:
        # Проверяем наличие транскрипции
        transcript = st.session_state.get('transcript', '')
        if not transcript:
            return None, "Нет транскрипции для создания сценария"
        
        # Загружаем промпт
        try:
            with open("prompt_scenario.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_scenario.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=get_max_tokens(),
                    temperature=st.session_state.get('temperature', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ],
                    stream=True
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получен сценарий длиной {len(result)} символов")
                
                # Сохраняем историю запроса для сценария
                st.session_state.api_history_scenario = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': transcript[:500] + "..." if len(transcript) > 500 else transcript,
                        'full_system_prompt': prompt_text,
                        'full_user_message': transcript
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Текст слишком большой. Попробуйте использовать видео с меньшей транскрипцией или подождите несколько минут."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании сценария: {str(e)}"

# Функция для создания саммари
def create_summary():
    """Создает саммари на основе транскрипции видео"""
    try:
        # Проверяем наличие транскрипции
        transcript = st.session_state.get('transcript', '')
        if not transcript:
            return None, "Нет транскрипции для создания саммари"
        
        # Загружаем промпт
        try:
            with open("prompt_summary.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_summary.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=get_max_tokens(),
                    temperature=st.session_state.get('temperature', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ],
                    stream=True
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получено саммари длиной {len(result)} символов")
                
                # Сохраняем историю запроса для саммари
                st.session_state.api_history_summary = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': transcript[:500] + "..." if len(transcript) > 500 else transcript,
                        'full_system_prompt': prompt_text,
                        'full_user_message': transcript
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Текст слишком большой. Попробуйте использовать видео с меньшей транскрипцией или подождите несколько минут."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании саммари: {str(e)}"

# Функция для создания комментария по транскрипции
def create_comment_on_video():
    """Создает комментарий на основе транскрипции видео"""
    try:
        # Проверяем наличие транскрипции
        transcript = st.session_state.get('transcript_with_timestamps', '')
        if not transcript:
            return None, "Нет транскрипции для создания комментария"
        
        # Загружаем промпт
        try:
            with open("prompt_comment_on_video.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_comment_on_video.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=get_max_tokens(),
                    temperature=st.session_state.get('temperature', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ],
                    stream=True
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получен комментарий длиной {len(result)} символов")
                
                # Сохраняем историю запроса для комментария
                st.session_state.api_history_comment_on_video = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': transcript[:500] + "..." if len(transcript) > 500 else transcript,
                        'full_system_prompt': prompt_text,
                        'full_user_message': transcript
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Текст слишком большой. Попробуйте использовать видео с меньшей транскрипцией или подождите несколько минут."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании комментария: {str(e)}"

# Функция для копирования текста в буфер обмена с изменением иконки
def copy_button_with_char_count(text: str, key: str, in_header: bool = False):
    """
    Создает кнопку копирования с подсчётом символов
    
    Args:
        text: Текст для копирования
        key: Уникальный ключ для кнопки
        in_header: True если кнопка в заголовке, False если под полем
    """
    if not text:
        return
    
    # Подсчитываем количество символов
    char_count = len(text)
    
    # Генерируем уникальный ID для JavaScript
    js_id = f"copy_{key}_{hash(text) % 1000000}"
    
    # Создаём полноценный HTML документ
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{
                margin: 0;
                padding: 0;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            }}
            .container {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                width: 100%;
                padding: 0.25rem 0;
                margin: -0.5rem 0 0.5rem 0;
                height: 1.5rem;
            }}
            .text {{
                color: rgba(49, 51, 63, 0.6);
                font-size: 0.875rem;
                font-weight: 400;
                line-height: 1.5rem;
            }}
            .copy-btn {{
                background: none;
                border: none;
                padding: 2px 0.25rem 0 0.25rem;
                font-size: 1.25rem;
                cursor: pointer;
                outline: none;
                display: flex;
                align-items: center;
                height: 100%;
            }}
            .copy-btn:focus {{
                outline: none;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <span class="text">Символов: {str(char_count).replace(',', ' ')}</span>
            <button class="copy-btn" id="copyBtn" title="Копировать в буфер обмена">📋</button>
        </div>
        <script>
            const copyBtn = document.getElementById('copyBtn');
            const textToCopy = {json.dumps(text)};
            
            copyBtn.addEventListener('click', async function() {{
                try {{
                    await navigator.clipboard.writeText(textToCopy);
                    copyBtn.innerHTML = '✔️';
                    copyBtn.title = 'Скопировано!';
                    setTimeout(() => {{
                        copyBtn.innerHTML = '📋';
                        copyBtn.title = 'Копировать в буфер обмена';
                    }}, 2000);
                }} catch(err) {{
                    // Fallback для старых браузеров
                    const textArea = document.createElement('textarea');
                    textArea.value = textToCopy;
                    textArea.style.position = 'fixed';
                    textArea.style.left = '-999999px';
                    document.body.appendChild(textArea);
                    textArea.focus();
                    textArea.select();
                    try {{
                        document.execCommand('copy');
                        copyBtn.innerHTML = '✔️';
                        copyBtn.title = 'Скопировано!';
                        setTimeout(() => {{
                            copyBtn.innerHTML = '📋';
                            copyBtn.title = 'Копировать в буфер обмена';
                        }}, 2000);
                    }} catch(e) {{
                        console.error('Failed to copy');
                    }}
                    document.body.removeChild(textArea);
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    components.html(html_code, height=35)

# Функция для создания ответа на комментарий пользователя
def create_reply_to_comment(user_comment):
    """Создает ответ на комментарий пользователя на основе транскрипции видео"""
    try:
        # Проверяем наличие транскрипции
        transcript = st.session_state.get('transcript_with_timestamps', '')
        if not transcript:
            return None, "Нет транскрипции для создания ответа"
        
        # Проверяем наличие комментария пользователя
        if not user_comment:
            return None, "Нет комментария пользователя для ответа"
        
        # Загружаем промпт
        try:
            with open("prompt_reply_to_users_comment.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt_reply_to_users_comment.txt"
        
        # Проверяем наличие API ключа
        if "ANTHROPIC_API_KEY" not in st.secrets:
            return None, "API ключ Anthropic не найден в секретах"
        
        # Инициализируем клиент Claude
        api_key = str(st.secrets["ANTHROPIC_API_KEY"])
        client = anthropic.Anthropic(api_key=api_key)
        
        # Объединяем транскрипцию и комментарий пользователя
        combined_message = f"Video transcript:\n{transcript}\n\nUser's comment:\n{user_comment}"
        
        # Попытки отправки запроса с обработкой rate limit
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Используем экспоненциальную задержку между попытками
                if attempt > 0:
                    wait_time = base_delay * (2 ** (attempt - 1))
                    st.info(f"⏳ Попытка {attempt + 1}/{max_retries}. Ожидание {wait_time} секунд...")
                    time.sleep(wait_time)
                
                # Отправляем запрос к Claude с streaming
                stream = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=get_max_tokens(),
                    temperature=st.session_state.get('temperature', 0.7),
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": combined_message
                        }
                    ],
                    stream=True
                )
                
                # Собираем результат из streaming response
                result = ""
                for event in stream:
                    if event.type == "content_block_delta":
                        result += event.delta.text
                    elif event.type == "message_stop":
                        break
                print(f"DEBUG: Получен ответ на комментарий длиной {len(result)} символов")
                
                # Сохраняем историю запроса для ответа на комментарий
                st.session_state.api_history_reply_to_comment = {
                    'request': {
                        'model': get_claude_model(),
                        'max_tokens': get_max_tokens(),
                        'temperature': st.session_state.get('temperature', 0.7),
                        'system_prompt': prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
                        'user_message': combined_message[:500] + "..." if len(combined_message) > 500 else combined_message,
                        'full_system_prompt': prompt_text,
                        'full_user_message': combined_message
                    },
                    'response': result
                }
                
                return result, None
                
            except anthropic.RateLimitError as e:
                error_details = str(e)
                if "input tokens" in error_details.lower():
                    if attempt < max_retries - 1:
                        st.warning(f"⚠️ Превышен лимит входных токенов. Попытка {attempt + 2}/{max_retries} через некоторое время...")
                        continue
                    else:
                        return None, "Текст слишком большой. Попробуйте использовать видео с меньшей транскрипцией или подождите несколько минут."
                else:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов API. Пожалуйста, подождите 5-10 минут и попробуйте снова."
                        
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return None, "Превышен лимит запросов. Подождите 5-10 минут перед следующей попыткой."
                elif "timeout" in error_str.lower():
                    if attempt < max_retries - 1:
                        st.warning("⏱️ Таймаут запроса. Повторная попытка...")
                        continue
                    else:
                        return None, "Превышено время ожидания ответа. Попробуйте еще раз."
                else:
                    return None, f"Ошибка: {error_str[:200]}"
                    
    except Exception as e:
        return None, f"Ошибка при создании ответа на комментарий: {str(e)}"

# Проверяем, нужна ли перезагрузка страницы
if st.session_state.get('need_rerun', False):
    st.session_state.need_rerun = False
    st.rerun()

# Боковая панель для настроек
with st.sidebar:
    st.header("⚙️ Настройки")
    
    # Список доступных моделей
    available_models = ["Claude Opus 4.1", "Claude Opus 4", "Claude Sonnet 4.5", "Claude Sonnet 4", "Claude Sonnet 3.7"]
    
    # Выбор основной модели
    st.markdown("### Модель Claude осн.")
    st.session_state.selected_model =     st.selectbox(
        "Для синопсисов, аннотаций и сценариев:",
        available_models,
        index=2,  # По умолчанию Claude Sonnet 4.5
        key="main_model_select"
    )
    
    # Ползунок температуры
    temperature_value = st.slider(
        "Температура:",
        min_value=0.0,
        max_value=1.0,
        value=st.session_state.temperature,
        step=0.1,
        help="Контролирует случайность ответов. 0 - детерминированные ответы, 1 - максимальная креативность"
    )
    st.session_state.temperature = temperature_value
    
    # Отображение информации о модели
    model_info = get_model_info()
    if model_info:
        with st.container():
            st.markdown("**Максимум токенов:**")
            st.caption(f"Входящее окно: {model_info['input_window']}")
            st.caption(f"Выходящее окно: {model_info['output_window']}")
            st.caption(f"Стоимость за 1M токенов входящих: {model_info['input_cost']}")
            st.caption(f"Стоимость за 1M токенов выходящих: {model_info['output_cost']}")
    
    # Выбор модели для превью
    st.markdown("### Модель Claude для превью")
    st.session_state.selected_model_preview = st.selectbox(
        "Для анализа изображений превью:",
        available_models,
        index=2,  # По умолчанию Claude Sonnet 4.5
        key="preview_model_select"
    )
    
    # Выбор метода получения транскрипции
    st.markdown("### Метод получения транскрипции")
    st.session_state.transcript_method = st.selectbox(
        "Выберите метод:",
        ["YouTubeTranscriptApi", "yt-dlp"],
        index=0 if st.session_state.get('transcript_method') == "YouTubeTranscriptApi" else 1,
        key="transcript_method_select",
        help="YouTubeTranscriptApi - стандартный метод, yt-dlp - альтернативный метод с более широкими возможностями"
    )
    
    # Выбор языка субтитров
    st.markdown("### Язык субтитров")
    language_options = {
        "en": "🇬🇧 Английский",
        "es": "🇪🇸 Испанский", 
        "pt": "🇵🇹 Португальский",
        "ru": "🇷🇺 Русский"
    }
    st.session_state.subtitle_language = st.selectbox(
        "Выберите язык субтитров:",
        options=list(language_options.keys()),
        format_func=lambda x: language_options[x],
        index=list(language_options.keys()).index(st.session_state.get('subtitle_language', 'en')),
        key="subtitle_language_select",
        help="Выберите язык субтитров для получения транскрипции видео"
    )
    
    # Опция использования прокси для получения транскрипции
    st.session_state.use_proxy = st.checkbox(
        "Использовать прокси для транскрипции",
        value=st.session_state.use_proxy,
        key="use_proxy_checkbox"
    )
    
    
    # Отладочная информация
    with st.expander("🔍 Debug Info"):
        st.write("**Session State:**")
        st.write(f"- video_id: {st.session_state.get('video_id', 'None')}")
        st.write(f"- video_title length: {len(st.session_state.get('video_title', ''))}")
        st.write(f"- thumbnail_text length: {len(st.session_state.get('thumbnail_text', ''))}")
        st.write(f"- transcript length: {len(st.session_state.get('transcript', ''))}")
        st.write(f"- synopsis_orig length: {len(st.session_state.get('synopsis_orig', ''))}")
        st.write(f"- synopsis_red length: {len(st.session_state.get('synopsis_red', ''))}")
        st.write(f"- selected_model: {st.session_state.get('selected_model', 'None')}")
        st.write(f"- selected_model_preview: {st.session_state.get('selected_model_preview', 'None')}")
        st.write(f"- transcript_method: {st.session_state.get('transcript_method', 'None')}")
        st.write(f"- use_proxy: {st.session_state.get('use_proxy', False)}")
        st.write(f"- subtitle_language: {st.session_state.get('subtitle_language', 'None')}")
        
        st.write("\n**Proxy Configuration:**")
        st.write(f"- Total proxies loaded: {len(proxy_manager.proxies)}")
        st.write(f"- Current proxy index: {proxy_manager.current_index}")
        st.write(f"- Current proxy: {proxy_manager.get_proxy_info()}")
        st.write(f"- Max retries per proxy: {proxy_manager.max_retries_per_proxy}")
        st.write(f"- Request timeout: {proxy_manager.request_timeout} seconds")
        
        # Информация о последней успешной попытке
        if 'proxy_success_info' in st.session_state and st.session_state.proxy_success_info:
            st.write("\n**Last Successful Proxy:**")
            success_info = st.session_state.proxy_success_info
            st.write(f"- Proxy: {success_info.get('last_successful_info', 'N/A')}")
            st.write(f"- Timestamp: {success_info.get('timestamp', 'N/A')}")
        
        # Информация о попытках получения транскрипции
        if 'transcript_attempts_log' in st.session_state and st.session_state.transcript_attempts_log:
            st.write("\n**Transcript Attempts Log:**")
            for attempt in st.session_state.transcript_attempts_log[-5:]:  # Показываем последние 5 попыток
                status = "✅ Success" if attempt.get('success') else f"❌ Failed: {attempt.get('error', 'Unknown error')[:50]}"
                st.write(f"- Attempt {attempt.get('attempt', 'N/A')}: {attempt.get('proxy', 'N/A')} - {status}")
            
            # Статистика попыток
            total_attempts = len(st.session_state.transcript_attempts_log)
            successful_attempts = sum(1 for a in st.session_state.transcript_attempts_log if a.get('success'))
            st.write(f"\n**Statistics:** {successful_attempts}/{total_attempts} successful attempts")
        
        st.write("\n**Secrets Status:**")
        try:
            st.write(f"- Secrets available: {hasattr(st, 'secrets')}")
            if hasattr(st, 'secrets'):
                st.write(f"- Total secrets: {len(list(st.secrets.keys()))}")
                st.write(f"- ANTHROPIC_API_KEY: {'✅ Found' if 'ANTHROPIC_API_KEY' in st.secrets else '❌ Not found'}")
                st.write(f"- YouTube keys: {sum(1 for k in st.secrets.keys() if k.startswith('YOUTUBE_API_KEY_'))}")
        except Exception as e:
            st.write(f"- Error checking secrets: {e}")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Очистить данные"):
                st.session_state.video_id = None
                st.session_state.video_title = ""
                st.session_state.thumbnail_text = ""
                st.session_state.transcript = ""
                st.session_state.transcript_with_timestamps = ""
                st.session_state.synopsis_orig = ""
                st.session_state.synopsis_red = ""
                st.session_state.proxy_success_info = {}
                st.session_state.transcript_attempts_log = []
                st.rerun()
        
        with col2:
            if st.button("🔀 Перемешать прокси"):
                proxy_manager.shuffle_proxies()
                st.success("Список прокси перемешан!")
                st.rerun()

# Основной контент
# Форма для ввода данных
with st.form("video_form"):
    video_input = st.text_input(
        "URL или ID видео:",
        placeholder="https://www.youtube.com/watch?v=... или ID видео"
    )
    
    submitted = st.form_submit_button("🔍 Получить данные референса", type="primary")

# Обработка отправки формы
if submitted and video_input:
    video_id = extract_video_id(video_input)
    
    if not video_id:
        st.error("❌ Некорректная ссылка на видео или ID")
    else:
        # Сохраняем ID
        st.session_state.video_id = video_id
        
        # Создаем контейнер для сообщений о прогрессе
        progress_container = st.container()
        
        with progress_container:
            with st.spinner("📝 Получение заголовка видео..."):
                title = get_video_title(video_id)
                st.session_state.video_title = title if title else ""
                st.success(f"✅ Заголовок получен: {title[:50]}..." if title and len(title) > 50 else f"✅ Заголовок: {title}")
            
            with st.spinner("🖼️ Анализ превью видео..."):
                thumbnail_text = get_thumbnail_text(video_id)
                st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                st.success("✅ Текст с превью получен")
            
            with st.spinner("📄 Получение транскрипции..."):
                transcript, transcript_with_timestamps = get_video_transcript(video_id)
                st.session_state.transcript = transcript if transcript else ""
                st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                st.success("✅ Транскрипция получена")
            
            st.balloons()
            st.success(f"🎉 Все данные успешно загружены для видео ID: {video_id}")
            
            # Важно: перезагружаем страницу после получения данных
            st.rerun()

# Секция данных референса
st.markdown("---")
st.markdown("### Данные референса")

# Создаем контейнер для полей данных
data_container = st.container()

with data_container:
    # Заголовок видео - однострочное поле
    col_label1, col_field1 = st.columns([1, 4])
    with col_label1:
        st.markdown("**Заголовок видео:**")
    with col_field1:
        current_title = st.session_state.get('video_title', '')
        st.text_input(
            "Заголовок видео",  # Добавляем непустую метку
            value=current_title,
            disabled=False,  # Делаем поле редактируемым
            key=f"title_display_{hash(current_title)}",
            label_visibility="collapsed"  # Скрываем метку
        )
    
    # Текст с превью - уменьшенное многострочное поле
    col_label2, col_field2 = st.columns([1, 4])
    with col_label2:
        st.markdown("**Текст с превью:**")
        # Добавляем подсчёт и копирование под заголовком
        current_thumbnail = st.session_state.get('thumbnail_text', '')
        if current_thumbnail:
            copy_button_with_char_count(current_thumbnail, "thumbnail_text", in_header=True)
    with col_field2:
        st.text_area(
            "Текст с превью",  # Добавляем непустую метку
            value=current_thumbnail,
            height=50,  # Уменьшено в 4 раза (было 200)
            disabled=False,  # Делаем поле редактируемым
            key=f"thumbnail_display_{hash(current_thumbnail)}",
            label_visibility="collapsed"  # Скрываем метку
        )
    
    # Транскрипция видео - увеличенное поле
    col_label3, col_field3 = st.columns([1, 4])
    with col_label3:
        st.markdown("**Транскрипция видео референса:**")
        # Чекбокс для временных меток
        show_timestamps = st.checkbox(
            "Сохранять временные метки",
            value=st.session_state.show_timestamps,
            key="timestamps_checkbox"
        )
        st.session_state.show_timestamps = show_timestamps
        # Добавляем подсчёт и копирование под заголовком
        if show_timestamps:
            current_transcript = st.session_state.get('transcript_with_timestamps', '')
        else:
            current_transcript = st.session_state.get('transcript', '')
        if current_transcript:
            copy_button_with_char_count(current_transcript, f"transcript_{show_timestamps}", in_header=True)
    with col_field3:
        st.text_area(
            "Транскрипция",  # Добавляем непустую метку
            value=current_transcript,
            height=300,  # Увеличено в 1.5 раза (было 200)
            disabled=False,  # Делаем поле редактируемым
            key=f"transcript_display_{hash(current_transcript)}_{show_timestamps}",
            label_visibility="collapsed"  # Скрываем метку
        )

# Секция аннотаций
st.markdown("---")
st.markdown("### Аннотации")

# Аннотация референса - заголовок и кнопка в одной строке
col1_header, col1_btn = st.columns([4, 1])
with col1_header:
    st.markdown("**Аннотация референса**")
with col1_btn:
    create_annotation_orig_clicked = st.button("Создать", key="create_annotation_orig")

# Поле для отображения аннотации референса (показываем всегда, если есть данные)
if st.session_state.get('annotation_orig', ''):
    st.text_area(
        "Аннотация референса",
        value=st.session_state.annotation_orig,
        height=200,
        key="annotation_orig_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.annotation_orig, "annotation_orig")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_annotation_orig'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_annotation_orig
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_annot_orig", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_annot_orig", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_annot_orig", label_visibility="collapsed")

# Обработка нажатия кнопки создания аннотации референса
if create_annotation_orig_clicked:
    # Проверяем наличие транскрипции
    if not st.session_state.get('transcript', ''):
        # Если нет транскрипции, проверяем video_id
        if not st.session_state.video_id:
            st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
        else:
            # Есть video_id, но нет транскрипции - получаем все данные
            with st.spinner("📝 Получение данных о видео..."):
                # Получаем заголовок
                title = get_video_title(st.session_state.video_id)
                st.session_state.video_title = title if title else ""
                
                # Получаем текст с превью
                thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                
                # Получаем транскрипцию
                transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                st.session_state.transcript = transcript if transcript else ""
                st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                
                if not st.session_state.transcript:
                    st.error("❌ Не удалось получить транскрипцию видео")
                else:
                    st.success("✅ Данные о видео получены")
                    
                    # Теперь создаем аннотацию
                    with st.spinner("🤖 Создаю аннотацию референса..."):
                        annotation, error = create_annotation_orig()
                        if error:
                            st.error(f"❌ {error}")
                        else:
                            st.session_state.annotation_orig = annotation
                            st.success(f"✅ Аннотация референса создана ({len(annotation)} символов)")
                            st.rerun()
    else:
        # Есть транскрипция - создаем аннотацию
        with st.spinner("🤖 Создаю аннотацию референса..."):
            annotation, error = create_annotation_orig()
            if error:
                st.error(f"❌ {error}")
            else:
                st.session_state.annotation_orig = annotation
                st.success(f"✅ Аннотация референса создана ({len(annotation)} символов)")
                st.rerun()


# Секция синопсисов
st.markdown("---")
st.markdown("### Синопсисы")

# Синопсис референса - заголовок и кнопка в одной строке
col1_header, col1_btn = st.columns([4, 1])
with col1_header:
    st.markdown("**Синопсис референса**")
with col1_btn:
    create_synopsis_orig_clicked = st.button("Создать", key="create_synopsis_orig")

# Поле для отображения синопсиса референса (показываем всегда, если есть данные)
if st.session_state.get('synopsis_orig', ''):
    st.text_area(
        "Синопсис референса",
        value=st.session_state.synopsis_orig,
        height=400,
        key="synopsis_orig_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.synopsis_orig, "synopsis_orig")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_synopsis_orig'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_synopsis_orig
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_orig", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_orig", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_orig", label_visibility="collapsed")

# Обработка нажатия кнопки создания синопсиса референса
if create_synopsis_orig_clicked:
        # Проверяем наличие транскрипции
        if not st.session_state.get('transcript', ''):
            # Если нет транскрипции, проверяем video_id
            if not st.session_state.video_id:
                st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
            else:
                # Есть video_id, но нет транскрипции - получаем все данные
                with st.spinner("📝 Получение данных о видео..."):
                    # Получаем заголовок
                    title = get_video_title(st.session_state.video_id)
                    st.session_state.video_title = title if title else ""
                    
                    # Получаем текст с превью
                    thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                    st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                    
                    # Получаем транскрипцию
                    transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                    st.session_state.transcript = transcript if transcript else ""
                    st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                    
                    if not st.session_state.transcript:
                        st.error("❌ Не удалось получить транскрипцию видео")
                    else:
                        st.success("✅ Данные о видео получены")
                        
                        # Теперь создаем синопсис
                        with st.spinner("🤖 Создаю синопсис референса..."):
                            synopsis, error = create_synopsis_orig()
                            if error:
                                st.error(f"❌ {error}")
                            else:
                                st.session_state.synopsis_orig = synopsis
                                st.success(f"✅ Синопсис референса создан ({len(synopsis)} символов)")
                                st.rerun()
        else:
            # Есть транскрипция - создаем синопсис
            with st.spinner("🤖 Создаю синопсис референса..."):
                synopsis, error = create_synopsis_orig()
                if error:
                    st.error(f"❌ {error}")
                else:
                    st.session_state.synopsis_orig = synopsis
                    st.success(f"✅ Синопсис референса создан ({len(synopsis)} символов)")
                    st.rerun()

# Синопсис изменённый - заголовок и кнопка в одной строке
col2_header, col2_btn = st.columns([4, 1])
with col2_header:
    st.markdown("**Синопсис изменённый**")
with col2_btn:
    create_synopsis_red_clicked = st.button("Создать", key="create_synopsis_red")

# Поле для отображения синопсиса изменённого (показываем всегда, если есть данные)
if st.session_state.get('synopsis_red', ''):
    st.text_area(
        "Синопсис изменённый",
        value=st.session_state.synopsis_red,
        height=400,
        key="synopsis_red_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.synopsis_red, "synopsis_red")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_synopsis_red'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_synopsis_red
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_red", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_red", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_red", label_visibility="collapsed")

# Обработка нажатия кнопки создания синопсиса изменённого
if create_synopsis_red_clicked:
        # Проверяем наличие оригинального синопсиса
        if not st.session_state.get('synopsis_orig', ''):
            # Если нет оригинального синопсиса, проверяем транскрипцию
            if not st.session_state.get('transcript', ''):
                # Если нет транскрипции, проверяем video_id
                if not st.session_state.video_id:
                    st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
                else:
                    # Есть video_id, но нет транскрипции - получаем все данные
                    with st.spinner("📝 Получение данных о видео..."):
                        # Получаем заголовок
                        title = get_video_title(st.session_state.video_id)
                        st.session_state.video_title = title if title else ""
                        
                        # Получаем текст с превью
                        thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                        st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                        
                        # Получаем транскрипцию
                        transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                        st.session_state.transcript = transcript if transcript else ""
                        st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                        
                        if not st.session_state.transcript:
                            st.error("❌ Не удалось получить транскрипцию видео")
                        else:
                            st.success("✅ Данные о видео получены")
                            
                            # Теперь создаем синопсис референса
                            with st.spinner("🤖 Создаю синопсис референса..."):
                                synopsis_orig, error = create_synopsis_orig()
                                if error:
                                    st.error(f"❌ {error}")
                                else:
                                    st.session_state.synopsis_orig = synopsis_orig
                                    st.success("✅ Синопсис референса создан")
                                    
                                    # И создаем измененный синопсис
                                    with st.spinner("🤖 Создаю изменённый синопсис..."):
                                        synopsis_red, error = create_synopsis_red(synopsis_orig)
                                        if error:
                                            st.error(f"❌ {error}")
                                        else:
                                            st.session_state.synopsis_red = synopsis_red
                                            st.success(f"✅ Синопсис изменённый создан ({len(synopsis_red)} символов)")
                                            st.rerun()
            else:
                # Есть транскрипция, но нет оригинального синопсиса - создаем его
                with st.spinner("🤖 Создаю синопсис референса..."):
                    synopsis_orig, error = create_synopsis_orig()
                    if error:
                        st.error(f"❌ {error}")
                    else:
                        st.session_state.synopsis_orig = synopsis_orig
                        st.success("✅ Синопсис референса создан")
                        
                        # Теперь создаем измененный синопсис
                        with st.spinner("🤖 Создаю изменённый синопсис..."):
                            synopsis_red, error = create_synopsis_red(synopsis_orig)
                            if error:
                                st.error(f"❌ {error}")
                            else:
                                st.session_state.synopsis_red = synopsis_red
                                st.success(f"✅ Синопсис изменённый создан ({len(synopsis_red)} символов)")
                                st.rerun()
        else:
            # Если есть оригинальный синопсис, создаем измененный
            with st.spinner("🤖 Создаю изменённый синопсис..."):
                synopsis_red, error = create_synopsis_red(st.session_state.synopsis_orig)
                if error:
                    st.error(f"❌ {error}")
                else:
                    st.session_state.synopsis_red = synopsis_red
                    st.success(f"✅ Синопсис изменённый создан ({len(synopsis_red)} символов)")
                    st.rerun()

# Сгенерировать варианты текста превью - заголовок и кнопка в одной строке
col3_header, col3_btn = st.columns([4, 1])
with col3_header:
    st.markdown("**Сгенерировать варианты текста превью**")
with col3_btn:
    create_thumbnail_variants_clicked = st.button("Создать", key="create_thumbnail_variants")

# Поле для отображения вариантов текста превью (показываем всегда, если есть данные)
if st.session_state.get('thumbnail_variants', ''):
    st.text_area(
        "Варианты текста превью",
        value=st.session_state.thumbnail_variants,
        height=400,
        key="thumbnail_variants_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.thumbnail_variants, "thumbnail_variants")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_thumbnail_variants'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_thumbnail_variants
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_variants", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_variants", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_variants", label_visibility="collapsed")

# Обработка нажатия кнопки создания вариантов текста превью
if create_thumbnail_variants_clicked:
    # Проверяем наличие текста с превью
    if not st.session_state.get('thumbnail_text', ''):
        # Если нет текста с превью, проверяем video_id
        if not st.session_state.video_id:
            st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
        else:
            # Есть video_id, но нет текста с превью - получаем его
            with st.spinner("🖼️ Получение текста с превью..."):
                thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                
                if not st.session_state.thumbnail_text:
                    st.error("❌ Не удалось получить текст с превью")
                else:
                    st.success("✅ Текст с превью получен")
                    
                    # Проверяем наличие измененного синопсиса
                    if not st.session_state.get('synopsis_red', ''):
                        st.warning("⚠️ Сначала необходимо создать измененный синопсис")
                    else:
                        # Создаем варианты текста превью
                        with st.spinner("🤖 Генерирую варианты текста превью..."):
                            variants, error = create_thumbnail_variants(st.session_state.thumbnail_text, st.session_state.synopsis_red)
                            if error:
                                st.error(f"❌ {error}")
                            else:
                                st.session_state.thumbnail_variants = variants
                                st.success(f"✅ Варианты текста превью созданы")
                                st.rerun()
    elif not st.session_state.get('synopsis_red', ''):
        # Есть текст с превью, но нет измененного синопсиса
        st.warning("⚠️ Сначала необходимо создать измененный синопсис")
    else:
        # Есть все необходимые данные
        with st.spinner("🤖 Генерирую варианты текста превью..."):
            variants, error = create_thumbnail_variants(st.session_state.thumbnail_text, st.session_state.synopsis_red)
            if error:
                st.error(f"❌ {error}")
            else:
                st.session_state.thumbnail_variants = variants
                st.success(f"✅ Варианты текста превью созданы")
                st.rerun()

# Секция сценария
st.markdown("---")
st.markdown("### Сценарий")

# Сценарий - заголовок и кнопка в одной строке
col_header, col_btn = st.columns([4, 1])
with col_header:
    st.markdown("**Сценарий по транскрипции изменённый**")
with col_btn:
    create_scenario_clicked = st.button("Создать", key="create_scenario")

# Поле для отображения сценария (показываем всегда, если есть данные)
if st.session_state.get('scenario', ''):
    st.text_area(
        "Сценарий",
        value=st.session_state.scenario,
        height=500,
        key="scenario_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.scenario, "scenario")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_scenario'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_scenario
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_scenario", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_scenario", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_scenario", label_visibility="collapsed")

# Обработка нажатия кнопки создания сценария
if create_scenario_clicked:
    # Проверяем наличие транскрипции
    if not st.session_state.get('transcript', ''):
        # Если нет транскрипции, проверяем video_id
        if not st.session_state.video_id:
            st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
        else:
            # Есть video_id, но нет транскрипции - получаем все данные
            with st.spinner("📝 Получение данных о видео..."):
                # Получаем заголовок
                title = get_video_title(st.session_state.video_id)
                st.session_state.video_title = title if title else ""
                
                # Получаем текст с превью
                thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                
                # Получаем транскрипцию
                transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                st.session_state.transcript = transcript if transcript else ""
                st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                
                if not st.session_state.transcript:
                    st.error("❌ Не удалось получить транскрипцию видео")
                else:
                    st.success("✅ Данные о видео получены")
                    
                    # Теперь создаем сценарий
                    with st.spinner("🤖 Создаю сценарий..."):
                        scenario, error = create_scenario()
                        if error:
                            st.error(f"❌ {error}")
                        else:
                            st.session_state.scenario = scenario
                            st.success(f"✅ Сценарий создан ({len(scenario)} символов)")
                            st.rerun()
    else:
        # Есть транскрипция - создаем сценарий
        with st.spinner("🤖 Создаю сценарий..."):
            scenario, error = create_scenario()
            if error:
                st.error(f"❌ {error}")
            else:
                st.session_state.scenario = scenario
                st.success(f"✅ Сценарий создан ({len(scenario)} символов)")
                st.rerun()

# Секция саммари
st.markdown("---")
st.markdown("### Саммари")

# Создать саммари транскрипции - заголовок и кнопка в одной строке
col_header, col_btn = st.columns([4, 1])
with col_header:
    st.markdown("**Создать саммари транскрипции**")
with col_btn:
    create_summary_clicked = st.button("Создать", key="create_summary")

# Поле для отображения саммари (показываем всегда, если есть данные)
if st.session_state.get('summary', ''):
    st.text_area(
        "Саммари",
        value=st.session_state.summary,
        height=300,
        key="summary_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.summary, "summary")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_summary'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_summary
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_summary", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_summary", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_summary", label_visibility="collapsed")

# Обработка нажатия кнопки создания саммари
if create_summary_clicked:
    # Проверяем наличие транскрипции
    if not st.session_state.get('transcript', ''):
        # Если нет транскрипции, проверяем video_id
        if not st.session_state.video_id:
            st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
        else:
            # Есть video_id, но нет транскрипции - получаем все данные
            with st.spinner("📝 Получение данных о видео..."):
                # Получаем заголовок
                title = get_video_title(st.session_state.video_id)
                st.session_state.video_title = title if title else ""
                
                # Получаем текст с превью
                thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                
                # Получаем транскрипцию
                transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                st.session_state.transcript = transcript if transcript else ""
                st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                
                if not st.session_state.transcript:
                    st.error("❌ Не удалось получить транскрипцию видео")
                else:
                    st.success("✅ Данные о видео получены")
                    
                    # Теперь создаем саммари
                    with st.spinner("🤖 Создаю саммари..."):
                        summary, error = create_summary()
                        if error:
                            st.error(f"❌ {error}")
                        else:
                            st.session_state.summary = summary
                            st.success(f"✅ Саммари создано ({len(summary)} символов)")
                            st.rerun()
    else:
        # Есть транскрипция - создаем саммари
        with st.spinner("🤖 Создаю саммари..."):
            summary, error = create_summary()
            if error:
                st.error(f"❌ {error}")
            else:
                st.session_state.summary = summary
                st.success(f"✅ Саммари создано ({len(summary)} символов)")
                st.rerun()

# Секция комментариев
st.markdown("---")
st.markdown("### Комментарии")

# Комментарий по транскрипции - заголовок и кнопка в одной строке
col_header, col_btn = st.columns([4, 1])
with col_header:
    st.markdown("**Комментарий по транскрипции**")
with col_btn:
    create_comment_on_video_clicked = st.button("Создать", key="create_comment_on_video")

# Поле для отображения комментария (показываем всегда, если есть данные)
if st.session_state.get('comment_on_video', ''):
    st.text_area(
        "Комментарий по видео",
        value=st.session_state.comment_on_video,
        height=200,
        key="comment_on_video_display",
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.comment_on_video, "comment_on_video")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_comment_on_video'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_comment_on_video
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, key="full_system_prompt_comment", label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, key="full_user_message_comment", label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, key="full_response_comment", label_visibility="collapsed")

# Обработка нажатия кнопки создания комментария по транскрипции
if create_comment_on_video_clicked:
    # Проверяем наличие транскрипции
    if not st.session_state.get('transcript_with_timestamps', ''):
        # Если нет транскрипции, проверяем video_id
        if not st.session_state.video_id:
            st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
        else:
            # Есть video_id, но нет транскрипции - получаем все данные
            with st.spinner("📝 Получение данных о видео..."):
                # Получаем заголовок
                title = get_video_title(st.session_state.video_id)
                st.session_state.video_title = title if title else ""
                
                # Получаем текст с превью
                thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                
                # Получаем транскрипцию
                transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                st.session_state.transcript = transcript if transcript else ""
                st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                
                if not st.session_state.transcript_with_timestamps:
                    st.error("❌ Не удалось получить транскрипцию видео")
                else:
                    st.success("✅ Данные о видео получены")
                    
                    # Теперь создаем комментарий
                    with st.spinner("🤖 Создаю комментарий..."):
                        comment, error = create_comment_on_video()
                        if error:
                            st.error(f"❌ {error}")
                        else:
                            st.session_state.comment_on_video = comment
                            st.success(f"✅ Комментарий создан ({len(comment)} символов)")
                            st.rerun()
    else:
        # Есть транскрипция - создаем комментарий
        with st.spinner("🤖 Создаю комментарий..."):
            comment, error = create_comment_on_video()
            if error:
                st.error(f"❌ {error}")
            else:
                st.session_state.comment_on_video = comment
                st.success(f"✅ Комментарий создан ({len(comment)} символов)")
                st.rerun()

# Ответить на комментарий пользователя
st.markdown("**Ответить на комментарий пользователя**")

# Используем форму для правильной обработки ввода
with st.form("reply_to_comment_form"):
    # Поле для ввода комментария пользователя
    user_comment_input = st.text_area(
        "Комментарий пользователя",
        height=100,
        placeholder="Введите комментарий пользователя, на который нужно ответить"
    )
    
    # Кнопка создания ответа внутри формы
    create_reply_clicked = st.form_submit_button("Написать ответ на комментарий пользователя")

# Поле для отображения ответа (показываем всегда, если есть данные)
if st.session_state.get('reply_to_comment', ''):
    # Убираем key, чтобы значение обновлялось динамически
    st.text_area(
        "Ответ на комментарий",
        value=st.session_state.reply_to_comment,
        height=200,
        label_visibility="collapsed"
    )
    # Добавляем кнопку копирования под полем
    copy_button_with_char_count(st.session_state.reply_to_comment, "reply_to_comment")
    
    # Свёрнутый блок с информацией о запросе к API
    if st.session_state.get('api_history_reply_to_comment'):
        with st.expander("🔍 Детали запроса к LLM", expanded=False):
            api_data = st.session_state.api_history_reply_to_comment
            st.markdown("**Параметры запроса:**")
            st.code(f"""
Модель: {api_data['request']['model']}
Макс. токенов: {api_data['request']['max_tokens']}
Температура: {api_data['request']['temperature']}
""")
            st.markdown("**Системный промпт (начало):**")
            st.text(api_data['request']['system_prompt'])
            st.markdown("**Сообщение пользователя (начало):**")
            st.text(api_data['request']['user_message'])
            
            # Полные версии в отдельных вкладках
            tab1, tab2, tab3 = st.tabs(["Полный системный промпт", "Полное сообщение", "Ответ LLM"])
            with tab1:
                st.text_area("Системный промпт", value=api_data['request']['full_system_prompt'], height=300, label_visibility="collapsed")
            with tab2:
                st.text_area("Сообщение пользователя", value=api_data['request']['full_user_message'], height=300, label_visibility="collapsed")
            with tab3:
                st.text_area("Ответ модели", value=api_data['response'], height=300, label_visibility="collapsed")

# Обработка нажатия кнопки создания ответа на комментарий
if create_reply_clicked:
    # Используем значение напрямую из формы
    current_user_comment = user_comment_input
    
    # Проверяем наличие комментария пользователя
    if not current_user_comment:
        st.warning("⚠️ Пожалуйста, введите комментарий пользователя")
    else:
        # Проверяем наличие транскрипции
        if not st.session_state.get('transcript_with_timestamps', ''):
            # Если нет транскрипции, проверяем video_id
            if not st.session_state.video_id:
                st.warning("⚠️ Данные о видео не найдены. Пожалуйста, сначала введите ссылку на видео и нажмите 'Получить данные референса'")
            else:
                # Есть video_id, но нет транскрипции - получаем все данные
                with st.spinner("📝 Получение данных о видео..."):
                    # Получаем заголовок
                    title = get_video_title(st.session_state.video_id)
                    st.session_state.video_title = title if title else ""
                    
                    # Получаем текст с превью
                    thumbnail_text = get_thumbnail_text(st.session_state.video_id)
                    st.session_state.thumbnail_text = thumbnail_text if thumbnail_text else ""
                    
                    # Получаем транскрипцию
                    transcript, transcript_with_timestamps = get_video_transcript(st.session_state.video_id)
                    st.session_state.transcript = transcript if transcript else ""
                    st.session_state.transcript_with_timestamps = transcript_with_timestamps if transcript_with_timestamps else ""
                    
                    if not st.session_state.transcript_with_timestamps:
                        st.error("❌ Не удалось получить транскрипцию видео")
                    else:
                        st.success("✅ Данные о видео получены")
                        
                        # Теперь создаем ответ на комментарий
                        with st.spinner("🤖 Создаю ответ на комментарий..."):
                            reply, error = create_reply_to_comment(current_user_comment)
                            if error:
                                st.error(f"❌ {error}")
                            else:
                                # Очищаем старые кэшированные значения виджетов
                                keys_to_remove = [k for k in st.session_state.keys() if 'reply' in k.lower() and k != 'reply_to_comment' and k != 'api_history_reply_to_comment']
                                for key in keys_to_remove:
                                    del st.session_state[key]
                                
                                st.session_state.reply_to_comment = reply
                                st.success(f"✅ Ответ создан ({len(reply)} символов)")
                                st.rerun()
        else:
            # Есть транскрипция - создаем ответ
            with st.spinner("🤖 Создаю ответ на комментарий..."):
                reply, error = create_reply_to_comment(current_user_comment)
                if error:
                    st.error(f"❌ {error}")
                else:
                    # Очищаем старые кэшированные значения виджетов
                    keys_to_remove = [k for k in st.session_state.keys() if 'reply' in k.lower() and k != 'reply_to_comment' and k != 'api_history_reply_to_comment']
                    for key in keys_to_remove:
                        del st.session_state[key]
                    
                    st.session_state.reply_to_comment = reply
                    st.success(f"✅ Ответ создан ({len(reply)} символов)")
                    st.rerun()

# Footer с информацией
st.markdown("---")
if st.session_state.video_id:
    st.info(f"📌 Текущее видео ID: {st.session_state.video_id}")