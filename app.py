import streamlit as st
import re
import os
import io
import base64
import time
import ssl
import json
from typing import Tuple, Optional
from PIL import Image
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import anthropic
import yt_dlp

# Для лучшей совместимости с yt-dlp
ssl._create_default_https_context = ssl._create_unverified_context

# Параметры прокси для получения транскрипции
PROXY_HOST = "185.76.11.214"
PROXY_PORT = 80
PROXY_USERNAME = "krdxwmej-18"
PROXY_PASSWORD = "r0ajol0cnax6"

def _get_proxy_url():
    return f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"

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
    st.session_state.selected_model = "Claude Sonnet 3.7"
if 'selected_model_preview' not in st.session_state:
    st.session_state.selected_model_preview = "Claude Sonnet 3.7"
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
    Получает транскрипцию видео через yt-dlp
    Возвращает: (текст_без_временных_меток, текст_с_временными_метками)
    """
    
    # Получаем выбранный язык
    selected_lang = st.session_state.get('subtitle_language', 'en')
    
    ydl_opts = {
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
    
    # Добавляем прокси если включено
    if st.session_state.get('use_proxy', False):
        ydl_opts['proxy'] = _get_proxy_url()
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Извлекаем информацию о видео БЕЗ скачивания
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}', 
                download=False
            )
            
            # Приоритет: 1) обычные субтитры 2) автоматические
            subtitles = info.get('subtitles', {})
            automatic_captions = info.get('automatic_captions', {})
            
            # Объединяем оба источника
            all_subs = {**automatic_captions, **subtitles}
            
            # Получаем выбранный язык
            selected_lang = st.session_state.get('subtitle_language', 'en')
            
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
            print(f"yt-dlp error: {str(e)}")
            return "Не удалось получить транскрипцию", "Не удалось получить транскрипцию"


def _fetch_and_parse_subtitles(url: str, format_type: str) -> Tuple[str, str]:
    """Загружает и парсит субтитры"""
    try:
        # Используем прокси если нужно
        proxies = {}
        if st.session_state.get('use_proxy', False):
            proxy_url = _get_proxy_url()
            proxies = {'http': proxy_url, 'https': proxy_url}
        
        response = requests.get(url, proxies=proxies, timeout=30)
        response.raise_for_status()
        
        # Парсим в зависимости от формата
        if 'json' in format_type:
            return _parse_json_subtitles(response.text)
        elif 'vtt' in format_type:
            return _parse_vtt_subtitles(response.text)
        else:
            # Пытаемся как JSON
            return _parse_json_subtitles(response.text)
            
    except Exception as e:
        print(f"Subtitle fetch error: {str(e)}")
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
    # Включение прокси при необходимости (только на время запроса)
    proxy_url = None
    # Сохраняем текущее окружение прокси (верхний и нижний регистры)
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_http_proxy_l = os.environ.get('http_proxy')
    old_https_proxy_l = os.environ.get('https_proxy')
    old_all_proxy = os.environ.get('ALL_PROXY')
    old_all_proxy_l = os.environ.get('all_proxy')
    try:
        if st.session_state.get('use_proxy', False):
            proxy_url = _get_proxy_url()
            os.environ['HTTP_PROXY'] = proxy_url
            os.environ['HTTPS_PROXY'] = proxy_url
            os.environ['http_proxy'] = proxy_url
            os.environ['https_proxy'] = proxy_url
            os.environ['ALL_PROXY'] = proxy_url
            os.environ['all_proxy'] = proxy_url
        # Создаем экземпляр API
        api = YouTubeTranscriptApi()
        
        # Пробуем получить транскрипцию на выбранном языке
        transcript_data = None
        
        # Получаем выбранный язык
        selected_lang = st.session_state.get('subtitle_language', 'en')
        
        # Пробуем получить транскрипцию на выбранном языке
        try:
            if proxy_url:
                # При включенном прокси используем явную передачу прокси в библиотеку
                proxies = {"http": proxy_url, "https": proxy_url}
                transcript_data = YouTubeTranscriptApi.get_transcript(
                    video_id,
                    languages=[selected_lang],
                    proxies=proxies
                )
            else:
                # Пробуем с конкретным языком
                transcript_data = api.fetch(video_id, languages=[selected_lang])
        except:
            transcript_data = None
        
        # Если не получилось на выбранном языке, пробуем получить автоматические субтитры
        if not transcript_data:
            try:
                if proxy_url:
                    proxies = {"http": proxy_url, "https": proxy_url}
                    # Пробуем получить автоматические субтитры на выбранном языке
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, proxies=proxies)
                else:
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                
                # Ищем автоматические субтитры на выбранном языке
                for transcript in transcript_list:
                    if transcript.language_code == selected_lang:
                        if proxy_url:
                            transcript_data = transcript.fetch(proxies=proxies)
                        else:
                            transcript_data = transcript.fetch()
                        break
            except:
                pass
        
        # Собираем текст транскрипции в двух форматах
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
            # Если не нашли субтитры на выбранном языке
            lang_names = {'en': 'английском', 'es': 'испанском', 'pt': 'португальском', 'ru': 'русском'}
            lang_name = lang_names.get(selected_lang, selected_lang)
            return f"Субтитры на {lang_name} языке недоступны для этого видео", f"Субтитры на {lang_name} языке недоступны для этого видео"
            
    except Exception as e:
        # Обработка различных типов ошибок
        error_str = str(e)
        if "no element found" in error_str.lower() or "xml" in error_str.lower():
            lang_names = {'en': 'английском', 'es': 'испанском', 'pt': 'португальском', 'ru': 'русском'}
            lang_name = lang_names.get(st.session_state.get('subtitle_language', 'en'), st.session_state.get('subtitle_language', 'en'))
            return f"Субтитры на {lang_name} языке недоступны для этого видео", f"Субтитры на {lang_name} языке недоступны для этого видео"
        else:
            error_msg = f"Не удалось получить транскрипцию: {error_str[:200]}"
            return error_msg, error_msg
    finally:
        # Восстанавливаем окружение прокси
        if proxy_url is not None:
            # Восстановление верхнего регистра
            if old_http_proxy is None:
                os.environ.pop('HTTP_PROXY', None)
            else:
                os.environ['HTTP_PROXY'] = old_http_proxy
            if old_https_proxy is None:
                os.environ.pop('HTTPS_PROXY', None)
            else:
                os.environ['HTTPS_PROXY'] = old_https_proxy
            # Восстановление нижнего регистра
            if old_http_proxy_l is None:
                os.environ.pop('http_proxy', None)
            else:
                os.environ['http_proxy'] = old_http_proxy_l
            if old_https_proxy_l is None:
                os.environ.pop('https_proxy', None)
            else:
                os.environ['https_proxy'] = old_https_proxy_l
            if old_all_proxy is None:
                os.environ.pop('ALL_PROXY', None)
            else:
                os.environ['ALL_PROXY'] = old_all_proxy
            if old_all_proxy_l is None:
                os.environ.pop('all_proxy', None)
            else:
                os.environ['all_proxy'] = old_all_proxy_l


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
            prompt_text = "Опишите текст, который вы видите на этом изображении превью YouTube видео. Выпишите весь текст точно как он написан."
        
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
            with open("prompt scenario.txt", "r", encoding="utf-8") as file:
                prompt_text = file.read()
        except FileNotFoundError:
            return None, "Не найден файл prompt scenario.txt"
        
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
        index=4,  # По умолчанию Claude Sonnet 3.7
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
        index=4,  # По умолчанию Claude Sonnet 3.7
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
        st.write("Session State:")
        st.write(f"- video_id: {st.session_state.get('video_id', 'None')}")
        st.write(f"- video_title length: {len(st.session_state.get('video_title', ''))}")
        st.write(f"- thumbnail_text length: {len(st.session_state.get('thumbnail_text', ''))}")
        st.write(f"- transcript length: {len(st.session_state.get('transcript', ''))}")
        st.write(f"- synopsis_orig length: {len(st.session_state.get('synopsis_orig', ''))}")
        st.write(f"- synopsis_red length: {len(st.session_state.get('synopsis_red', ''))}")
        st.write(f"- selected_model: {st.session_state.get('selected_model', 'None')}")
        st.write(f"- selected_model_preview: {st.session_state.get('selected_model_preview', 'None')}")
        
        st.write("\nSecrets Status:")
        try:
            st.write(f"- Secrets available: {hasattr(st, 'secrets')}")
            if hasattr(st, 'secrets'):
                st.write(f"- Total secrets: {len(list(st.secrets.keys()))}")
                st.write(f"- ANTHROPIC_API_KEY: {'✅ Found' if 'ANTHROPIC_API_KEY' in st.secrets else '❌ Not found'}")
                st.write(f"- YouTube keys: {sum(1 for k in st.secrets.keys() if k.startswith('YOUTUBE_API_KEY_'))}")
        except Exception as e:
            st.write(f"- Error checking secrets: {e}")
        
        if st.button("🔄 Очистить данные"):
            st.session_state.video_id = None
            st.session_state.video_title = ""
            st.session_state.thumbnail_text = ""
            st.session_state.transcript = ""
            st.session_state.transcript_with_timestamps = ""
            st.session_state.synopsis_orig = ""
            st.session_state.synopsis_red = ""
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
    with col_field2:
        current_thumbnail = st.session_state.get('thumbnail_text', '')
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
    with col_field3:
        # Выбираем какую версию транскрипции показывать
        if show_timestamps:
            current_transcript = st.session_state.get('transcript_with_timestamps', '')
        else:
            current_transcript = st.session_state.get('transcript', '')
        
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

# Footer с информацией
st.markdown("---")
if st.session_state.video_id:
    st.info(f"📌 Текущее видео ID: {st.session_state.video_id}")