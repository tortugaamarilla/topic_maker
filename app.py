import streamlit as st
import re
import os
import io
import base64
import time
from PIL import Image
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import anthropic

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
    st.session_state.selected_model = "Claude Opus 4"
if 'show_timestamps' not in st.session_state:
    st.session_state.show_timestamps = False
if 'synopsis_orig' not in st.session_state:
    st.session_state.synopsis_orig = ""
if 'synopsis_red' not in st.session_state:
    st.session_state.synopsis_red = ""

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

# Функция для получения транскрипции видео
def get_video_transcript(video_id):
    try:
        # Создаем экземпляр API
        api = YouTubeTranscriptApi()
        
        # Получаем транскрипцию
        transcript_data = api.fetch(video_id)
        
        # Собираем текст транскрипции в двух форматах
        if transcript_data:
            # transcript_data - это объект FetchedTranscript, который можно итерировать
            # Каждый элемент имеет атрибуты: text, start, duration
            
            # Версия без временных меток
            full_text = '\n'.join([str(entry.text) for entry in transcript_data])
            
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
                start_time = entry.start
                text = str(entry.text)
                text_with_timestamps.append(f"[{format_time(start_time)}] {text}")
            
            full_text_with_timestamps = '\n'.join(text_with_timestamps)
            
            return full_text, full_text_with_timestamps
        else:
            return "Транскрипция недоступна для этого видео", "Транскрипция недоступна для этого видео"
            
    except Exception as e:
        # Обработка различных типов ошибок
        error_str = str(e)
        if "no element found" in error_str.lower() or "xml" in error_str.lower():
            return "Транскрипция недоступна для этого видео", "Транскрипция недоступна для этого видео"
        else:
            error_msg = f"Не удалось получить транскрипцию: {error_str[:200]}"
            return error_msg, error_msg

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
            model="claude-3-haiku-20240307",  # Используем Haiku для обработки изображений
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

# Функция для выбора модели Claude
def get_claude_model():
    model_mapping = {
        "Claude Opus 4": "claude-3-opus-20240229",
        "Claude Sonnet 4.5": "claude-3-5-sonnet-20241022",
        "Claude Opus 4.5": "claude-3-opus-20240229",  # Временно используем Opus 3
        "Claude Sonnet 4.1": "claude-3-sonnet-20240229"
    }
    return model_mapping[st.session_state.selected_model]

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
        max_retries = 3
        retry_delay = 30  # начальная задержка в секундах
        
        for attempt in range(max_retries):
            try:
                # Отправляем запрос к Claude
                message = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=4000,
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ]
                )
                
                return message.content[0].text, None
                
            except anthropic.RateLimitError as e:
                if attempt < max_retries - 1:
                    # Показываем сообщение о повторной попытке
                    wait_time = retry_delay * (attempt + 1)
                    st.warning(f"⏳ Превышен лимит запросов API. Ожидание {wait_time} секунд перед повторной попыткой...")
                    time.sleep(wait_time)
                else:
                    return None, f"Превышен лимит запросов API. Пожалуйста, подождите несколько минут и попробуйте снова."
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (attempt + 1)
                        st.warning(f"⏳ Превышен лимит запросов API. Ожидание {wait_time} секунд перед повторной попыткой...")
                        time.sleep(wait_time)
                    else:
                        return None, f"Превышен лимит запросов API. Пожалуйста, подождите несколько минут и попробуйте снова."
                else:
                    return None, f"Ошибка при создании синопсиса: {error_str}"
                    
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
        max_retries = 3
        retry_delay = 30  # начальная задержка в секундах
        
        for attempt in range(max_retries):
            try:
                # Отправляем запрос к Claude
                message = client.messages.create(
                    model=get_claude_model(),
                    max_tokens=4000,
                    system=prompt_text,
                    messages=[
                        {
                            "role": "user",
                            "content": synopsis_orig
                        }
                    ]
                )
                
                return message.content[0].text, None
                
            except anthropic.RateLimitError as e:
                if attempt < max_retries - 1:
                    # Показываем сообщение о повторной попытке
                    wait_time = retry_delay * (attempt + 1)
                    st.warning(f"⏳ Превышен лимит запросов API. Ожидание {wait_time} секунд перед повторной попыткой...")
                    time.sleep(wait_time)
                else:
                    return None, f"Превышен лимит запросов API. Пожалуйста, подождите несколько минут и попробуйте снова."
            except Exception as e:
                error_str = str(e)
                if "rate_limit" in error_str.lower() or "429" in error_str:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (attempt + 1)
                        st.warning(f"⏳ Превышен лимит запросов API. Ожидание {wait_time} секунд перед повторной попыткой...")
                        time.sleep(wait_time)
                    else:
                        return None, f"Превышен лимит запросов API. Пожалуйста, подождите несколько минут и попробуйте снова."
                else:
                    return None, f"Ошибка при создании измененного синопсиса: {error_str}"
                    
    except Exception as e:
        return None, f"Ошибка при создании измененного синопсиса: {str(e)}"

# Заголовок приложения
st.title("🎬 Topic Maker")

# Боковая панель для настроек
with st.sidebar:
    st.header("⚙️ Настройки")
    st.session_state.selected_model = st.selectbox(
        "Выберите модель Claude:",
        ["Claude Opus 4", "Claude Sonnet 4.5", "Claude Opus 4.5", "Claude Sonnet 4.1"],
        index=0
    )
    st.info(f"Текущая модель: {st.session_state.selected_model}")
    
    # Информация о лимитах API
    with st.expander("ℹ️ О лимитах API"):
        st.write("""
        **Важно:** API Claude имеет ограничения по скорости запросов.
        
        Если вы видите ошибку о превышении лимита:
        - Приложение автоматически повторит попытку через 30-90 секунд
        - Если ошибка повторяется, подождите 2-3 минуты перед новой попыткой
        - Используйте более легкие модели (Sonnet вместо Opus) для меньшего потребления токенов
        """)
    
    # Отладочная информация
    with st.expander("🔍 Debug Info"):
        st.write("Session State:")
        st.write(f"- video_id: {st.session_state.get('video_id', 'None')}")
        st.write(f"- video_title length: {len(st.session_state.get('video_title', ''))}")
        st.write(f"- thumbnail_text length: {len(st.session_state.get('thumbnail_text', ''))}")
        st.write(f"- transcript length: {len(st.session_state.get('transcript', ''))}")
        
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
st.markdown("### 📹 Введите ссылку на YouTube видео или ID видео")

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
st.markdown("### 📊 Данные референса")

# Создаем контейнер для полей данных
data_container = st.container()

with data_container:
    col1, col2, col3 = st.columns(3)
    
    with col1:
        # Отображаем текущее значение из session_state
        current_title = st.session_state.get('video_title', '')
        st.text_area(
            "**📝 Заголовок видео**",
            value=current_title,
            height=200,  # Увеличено с 100 до 200
            disabled=False,  # Делаем поле редактируемым
            key=f"title_display_{hash(current_title)}"  # Уникальный ключ на основе контента
        )
    
    with col2:
        current_thumbnail = st.session_state.get('thumbnail_text', '')
        st.text_area(
            "**🖼️ Текст с превью**",
            value=current_thumbnail,
            height=200,  # Увеличено с 100 до 200
            disabled=False,  # Делаем поле редактируемым
            key=f"thumbnail_display_{hash(current_thumbnail)}"
        )
    
    with col3:
        # Чекбокс для временных меток
        show_timestamps = st.checkbox(
            "Сохранять временные метки",
            value=st.session_state.show_timestamps,
            key="timestamps_checkbox"
        )
        st.session_state.show_timestamps = show_timestamps
        
        # Выбираем какую версию транскрипции показывать
        if show_timestamps:
            current_transcript = st.session_state.get('transcript_with_timestamps', '')
        else:
            current_transcript = st.session_state.get('transcript', '')
        
        st.text_area(
            "**📄 Транскрипция видео референса**",
            value=current_transcript,
            height=200,  # Увеличено с 100 до 200
            disabled=False,  # Делаем поле редактируемым
            key=f"transcript_display_{hash(current_transcript)}_{show_timestamps}"
        )

# Секция аннотаций
st.markdown("---")
st.markdown("### 📝 Аннотации")

col1, col2 = st.columns(2)

with col1:
    annotation_orig = st.text_area(
        "**Аннотация референса**",
        height=200,
        key="annotation_orig"
    )
    if st.button("🔨 Создать", key="create_annotation_orig"):
        if not st.session_state.video_id:
            st.warning("⚠️ Сначала получите данные о видео")
        else:
            st.info("🚧 Функция в разработке")

with col2:
    annotation_red = st.text_area(
        "**Аннотация изменённая**",
        height=200,
        key="annotation_red"
    )
    if st.button("🔨 Создать", key="create_annotation_red"):
        if not st.session_state.video_id:
            st.warning("⚠️ Сначала получите данные о видео")
        else:
            st.info("🚧 Функция в разработке")

# Секция синопсисов
st.markdown("---")
st.markdown("### 📚 Синопсисы")

col1, col2 = st.columns(2)

with col1:
    # Отображаем текущее значение синопсиса из session_state
    current_synopsis_orig = st.session_state.get('synopsis_orig', '')
    synopsis_orig_input = st.text_area(
        "**Синопсис референса**",
        value=current_synopsis_orig,
        height=200,
        key="synopsis_orig_text_area"
    )
    
    if st.button("🔨 Создать", key="create_synopsis_orig"):
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
                                st.success("✅ Синопсис референса создан")
                                st.rerun()
        else:
            # Есть транскрипция - создаем синопсис
            with st.spinner("🤖 Создаю синопсис референса..."):
                synopsis, error = create_synopsis_orig()
                if error:
                    st.error(f"❌ {error}")
                else:
                    st.session_state.synopsis_orig = synopsis
                    st.success("✅ Синопсис референса создан")
                    st.rerun()

with col2:
    # Отображаем текущее значение измененного синопсиса из session_state
    current_synopsis_red = st.session_state.get('synopsis_red', '')
    synopsis_red_input = st.text_area(
        "**Синопсис изменённый**",
        value=current_synopsis_red,
        height=200,
        key="synopsis_red_text_area"
    )
    
    if st.button("🔨 Создать", key="create_synopsis_red"):
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
                                            st.success("✅ Синопсис изменённый создан")
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
                                st.success("✅ Синопсис изменённый создан")
                                st.rerun()
        else:
            # Если есть оригинальный синопсис, создаем измененный
            with st.spinner("🤖 Создаю изменённый синопсис..."):
                synopsis_red, error = create_synopsis_red(st.session_state.synopsis_orig)
                if error:
                    st.error(f"❌ {error}")
                else:
                    st.session_state.synopsis_red = synopsis_red
                    st.success("✅ Синопсис изменённый создан")
                    st.rerun()

# Секция сценария
st.markdown("---")
st.markdown("### 🎭 Сценарий")

scenario = st.text_area(
    "**Сценарий по транскрипции изменённый**",
    height=300,
    key="scenario"
)
if st.button("🔨 Создать", key="create_scenario"):
    if not st.session_state.video_id:
        st.warning("⚠️ Сначала получите данные о видео")
    else:
        st.info("🚧 Функция в разработке")

# Footer с информацией
st.markdown("---")
if st.session_state.video_id:
    st.info(f"📌 Текущее видео ID: {st.session_state.video_id}")