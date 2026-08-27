# PROJECT PLAYBOOK — Telegram-бот + Mini App + PostgreSQL + Railway

Свод граблей и готовых решений из проекта FitBot. Читать ПЕРЕД стартом нового
похожего проекта, чтобы не наступать на те же ошибки повторно. Формат: симптом → причина
→ готовое решение. Все конфиги ниже — проверенные и рабочие.

---

## 0. Проверенный стек (копировать как есть)

`requirements.txt`:
```
aiogram>=3.15                 # НЕ 3.13 — конфликт pydantic (см. §1.1)
openai>=1.0.0                 # клиент для агрегатора (polza.ai / любой OpenAI-совместимый)
pydantic>=2.9.0
pydantic-settings>=2.0
python-dotenv>=1.0
sqlalchemy[asyncio]>=2.0
asyncpg>=0.29
fastapi>=0.111.0
uvicorn[standard]>=0.29.0     # [standard] даёт uvloop/httptools на Linux
aiofiles>=23.0
orjson>=3.9
```

`Dockerfile` (использовать Dockerfile, НЕ nixpacks — см. §2.1):
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

Архитектурное решение, которое хорошо себя показало: **бот и Mini App API в одном
процессе** — `bot.py` через `asyncio.gather` поднимает и aiogram polling, и uvicorn.
Один сервис на Railway, общий пул соединений к БД.

---

## 1. Зависимости

### 1.1 pydantic version conflict
**Симптом:** при установке конфликт — `aiogram 3.13` требует `pydantic<2.9`, а `fastapi`
требует `pydantic>=2.9`.
**Фикс:** `aiogram>=3.15` (поддерживает pydantic 2.9+). Всегда ставить aiogram ≥3.15.

---

## 2. Railway / деплой

### 2.1 Nixpacks не находит pip
**Симптом:** `pip: command not found`, потом с `python3 -m pip` → `No module named pip`.
**Причина:** nixpacks капризен с окружением Python.
**Фикс:** НЕ использовать nixpacks. Класть `Dockerfile` (см. §0) — Railway соберёт по нему
предсказуемо. Удалить `nixpacks.toml`, если остался.

### 2.2 DATABASE_URL: неправильный драйвер
**Симптом:** `Could not parse SQLAlchemy URL` или ошибки sync-драйвера.
**Причина:** Railway даёт `postgresql://...`, а SQLAlchemy async нужен `postgresql+asyncpg://`.
**Фикс:** в `config.py` конвертировать:
```python
@property
def async_database_url(self) -> str:
    url = self.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url
```

### 2.3 Переменная-ссылка `${{Postgres.DATABASE_URL}}` не резолвится
**Симптом:** в переменную попадает буквальный текст `${{Postgres.DATABASE_URL}}` →
`Could not parse SQLAlchemy URL`.
**Причина:** сервис базы назван НЕ ровно `Postgres`, ссылка не подставляется.
**Фикс:** скопировать реальное значение `DATABASE_URL` из сервиса базы (вкладка Variables,
раскрыть глазом) и вставить в переменную сервиса приложения напрямую.

### 2.4 Внутренний адрес базы не резолвится
**Симптом:** `socket.gaierror: [Errno -2] Name or service not known` для хоста
`postgres.railway.internal`, даже с ретраями.
**Причина:** приватная сеть Railway часто не поднимается (особенно если сервис базы создан
давно / приватная сеть не активна).
**Фикс:** использовать ПУБЛИЧНЫЙ `DATABASE_URL` (хост `...proxy.rlwy.net`). Разница в
скорости почти вся съедается пулом соединений (см. §3.1) — внутренний адрес не стоит нервов.

### 2.5 WEBAPP_URL без https://
**Симптом:** бот падает на `/start`: `Bad Request: ... Web App URL is invalid: Only HTTPS
links are allowed`.
**Фикс:** URL Mini App ОБЯЗАТЕЛЬНО с `https://` и, как правило, с путём `/app`. Валидировать
формат перед `WebAppInfo(url=...)`.

### 2.6 Переменные окружения (чек-лист Railway)
`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY` (или ключ агрегатора), `DATABASE_URL` (из плагина
Postgres), `WEBAPP_URL` (публичный домен + `/app`, заполнить ПОСЛЕ первого деплоя).

---

## 3. База данных (SQLAlchemy async + asyncpg)

### 3.1 Всегда настраивать пул соединений
**Причина:** без пула каждый запрос платит TCP+TLS-хендшейк к базе (через публичный прокси —
особенно дорого).
**Эффект пула:** повторные запросы 277мс → 0-16мс (проверено).
```python
engine = create_async_engine(
    settings.async_database_url, echo=False,
    pool_size=10, max_overflow=5, pool_pre_ping=True, pool_recycle=1800,
    pool_timeout=30, connect_args={"timeout": 10, "command_timeout": 20},
)
```
`pool_pre_ping` спасает от «протухших» соединений (Railway рвёт простаивающие),
`pool_recycle` обновляет до таймаута.

### 3.2 create_all НЕ добавляет колонки в существующие таблицы
**Симптом:** после добавления поля в модель — `asyncpg.exceptions.UndefinedColumnError`.
**Причина:** `Base.metadata.create_all` создаёт только отсутствующие ТАБЛИЦЫ, но никогда не
делает ALTER существующих.
**Фикс:** в `init_db` идемпотентно добавлять новые колонки:
```python
_ADD_COLUMNS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS target_weight_kg DOUBLE PRECISION",
)
# в init_db, внутри engine.begin(): for stmt in _ADD_COLUMNS: await conn.execute(text(stmt))
```
Новые колонки делать nullable, чтобы старые записи не падали.

### 3.3 init_db с ретраями
**Причина:** сеть/DNS базы могут быть не готовы в первые секунды после старта контейнера.
**Фикс:** обернуть `create_all` в retry-with-backoff (5 попыток × 1.5с).

### 3.4 FK-колонки в Postgres не индексируются автоматически
**Фикс:** явно создавать составные индексы под реальные запросы:
```python
"CREATE INDEX IF NOT EXISTS ix_diary_items_user_date ON diary_items (user_id, date)"
```

### 3.5 Медленный эндпоинт с несколькими запросами
**Фикс:** объединять запросы (один `date >= since` вместо «сегодня» + «история») и
запускать независимые чтения параллельно через `asyncio.gather`, каждый на своей сессии.

---

## 4. Тестирование async + asyncpg (важный подводный камень)

### 4.1 «Event loop is closed» в тестах
**Симптом:** первый запрос через Starlette `TestClient` проходит, последующие падают с
`RuntimeError: Event loop is closed` / `ConnectionDoesNotExistError`.
**Причина:** `TestClient` создаёт НОВЫЙ event loop на каждый запрос, а соединения asyncpg
в пуле привязаны к loop, на котором создались. Это АРТЕФАКТ ТЕСТА, не баг кода — в проде
всё крутится на одном постоянном loop uvicorn.
**Фикс для тестов:** многошаговые сценарии гонять на ОДНОМ loop через httpx:
```python
import asyncio
from httpx import ASGITransport, AsyncClient
async def main():
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as c:
        await c.get(...); await c.post(...)   # все на одном loop → пул работает
    from db.database import engine; await engine.dispose()
asyncio.run(main())
```
Чистую бизнес-логику (расчёты) выносить в pure-функции и покрывать обычными юнит-тестами
без БД/сети.

---

## 5. Telegram Mini App (фронтенд)

### 5.1 Относительные пути к статике → 404 → мёртвое приложение
**Симптом:** Mini App показывает «сырой» шаблон, кнопки не нажимаются (в атрибутах виден
литерал `{{ ... }}`).
**Причина:** `./support.js` резолвится в `/support.js`, а сервер отдаёт статику только по
`/static/...` → 404 → фреймворк не инициализируется.
**Фикс:** в HTML ссылаться на статику АБСОЛЮТНО: `/static/support.js`. Проверять локально
через реальную отдачу сервером, а не открытием файла (`file://` маскирует проблему).

### 5.2 React с CDN — не грузится (РФ)
**Причина:** unpkg/CDN блокируются/тормозят.
**Фикс:** класть React (и любые фронт-зависимости) ЛОКАЛЬно в проект, отдавать со своего
сервера. Никаких CDN, особенно для RU-аудитории.

### 5.3 Telegram агрессивно кэширует Mini App
**Симптом:** после деплоя видна старая версия, даже с `Cache-Control: no-store`.
**Причина:** Telegram держит WebView в памяти и переоткрывает его без нового запроса.
**Фикс:** версионировать URL при КАЖДОМ старте:
- в `bot.py` к `WEBAPP_URL` для кнопки-меню: `f"{url}?v={int(time.time())}"`;
- в `api/main.py` дописывать `?v=<start>` к тегам `<script src="/static/...">` внутри HTML
  (читать index.html в память один раз, regex-ом проставлять версию).
Плюс отдавать сам HTML с `Cache-Control: no-store`.
Для проверки нового: ПОЛНОСТЬЮ закрыть Telegram (смахнуть), затем открыть заново.

### 5.4 localStorage общий для всех аккаунтов на устройстве → утечка данных
**Симптом:** второй Telegram-аккаунт на том же телефоне видит данные первого.
**Фикс:** ключ хранилища привязывать к Telegram user id:
`'app_state_' + tg.initDataUnsafe.user.id`.

### 5.5 Мелькание не того экрана при загрузке
**Симптом:** зарегистрированному пользователю на старте мелькает онбординг.
**Фикс:** экран-сплэш (спиннер) как дефолт, мгновенная гидратация из кэша, а сервер —
источник правды: `GET /state` решает, что показать. Не флешить онбординг, пока идёт запрос.

### 5.6 Кнопка «назад» в оверлеях перекрыта шапкой Telegram
**Фикс:** использовать нативную `tg.BackButton` (show при открытии оверлея, hide при
закрытии, `onClick` закрывает верхний оверлей). Всегда нажимается.

### 5.7 Авторизация Mini App
Проверять подпись `initData` (HMAC-SHA256 с ключом `WebAppData` от токена бота) на сервере,
на каждый API-запрос. Заголовок `X-Telegram-Init-Data`.

---

## 6. Производительность API

- `GZipMiddleware(minimum_size=500)` — сжимает JS-бандл и JSON (−60-75% трафика).
- `default_response_class=ORJSONResponse` — быстрее сериализация.
- Версионированные ассеты можно кэшировать надолго; сам HTML — `no-store`.

---

## 7. AI / vision (через OpenAI-совместимый агрегатор)

- Клиент: `AsyncOpenAI(api_key=..., base_url="https://polza.ai/api/v1")`,
  модель `google/gemini-2.5-flash-lite` (дёшево/быстро для vision).
- Ответы модели часто в ```json ...``` — снимать markdown-обёртку перед `json.loads`.
- Оборачивать вызов в retry (2 попытки): модель иногда отдаёт кривой JSON.
- Клиент создавать лениво и переиспользовать (внутренний httpx-пул).

---

## 8. Git / Windows / секреты

### 8.1 git push: TLS handshake (РФ)
**Симптом:** `schannel: failed to receive handshake, SSL/TLS connection failed`.
**Фикс:** `git config --global http.sslBackend openssl`; при необходимости — VPN. Часто
сбой разовый — помогает повтор.

### 8.2 Секреты
`.env` — в `.gitignore` ВСЕГДА. Токены/ключи/DATABASE_URL никогда не коммитить. Держать
`.env.example` со всеми ключами-заглушками (включая DATABASE_URL и WEBAPP_URL, а не только
токены — иначе новый разработчик не поймёт, что нужно).

### 8.3 PowerShell (Windows)
- Фоновый процесс: `Start-Process`, а не `&`.
- Пути: forward slashes ок; для нативных exe с stderr — не редиректить `2>&1`.
- Пользователь работает с Railway/GitHub через веб-интерфейс — команды давать готовыми и по
  шагам, без CLI-жаргона.

---

## 9. Быстрый чек-лист старта нового проекта

1. `requirements.txt` + `Dockerfile` из §0 (без nixpacks).
2. `config.py` с конвертацией `async_database_url` (§2.2).
3. `db/database.py`: пул (§3.1) + `init_db` с ретраями (§3.3), `ALTER IF NOT EXISTS` (§3.2),
   составные индексы (§3.4).
4. Mini App: статика по `/static/...` (§5.1), React локально (§5.2), версионирование
   ассетов (§5.3), ключ localStorage по user id (§5.4), сплэш+гидратация (§5.5),
   `tg.BackButton` (§5.6), проверка `initData` (§5.7).
5. API: GZip + ORJSON (§6).
6. Railway: Dockerfile-деплой, публичный DATABASE_URL, все env-переменные (§2.6),
   `WEBAPP_URL` с https и `/app` (§2.5).
7. `.env` в `.gitignore`, полный `.env.example` (§8.2).
8. Логику расчётов — в pure-функции + юнит-тесты; интеграцию — httpx на одном loop (§4.1).
