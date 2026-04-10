# Инструкция для Codex: интеграция с Booking Cars API

Этот файл предназначен для другого проекта, который должен подключаться к сервису `booking_cars` по API.

## Назначение

- Использовать этот сервис как внешний источник данных.
- Не менять сервер `booking_cars`, если задача только про интеграцию.
- Работать через HTTP API и session cookie.

## Базовые правила

- Сначала выполнять логин через `POST /login`.
- После логина использовать cookie сессии во всех следующих запросах.
- Если сервер вернул `401`, нужно повторно выполнить логин и повторить запрос один раз.
- Если сервер вернул `403`, у пользователя недостаточно прав.
- Не логировать пароль, cookie и секреты.
- Все URL, логин и пароль брать из переменных окружения.

## Переменные окружения

- `BOOKING_CARS_BASE_URL`
- `BOOKING_CARS_USERNAME`
- `BOOKING_CARS_PASSWORD`
- `BOOKING_CARS_TIMEOUT`

Пример:

```env
BOOKING_CARS_BASE_URL=http://localhost:5000
BOOKING_CARS_USERNAME=admin
BOOKING_CARS_PASSWORD=secret
BOOKING_CARS_TIMEOUT=10
```

## Аутентификация

### Логин

Запрос:

- `POST /login`
- `Content-Type: application/x-www-form-urlencoded`

Поля:

- `username`
- `password`

Важно:

- успешный логин возвращает redirect/HTML, а не JSON;
- признак успеха: HTTP `200` или `302` и сохранённая session cookie;
- нужен HTTP client с поддержкой cookie jar/session.

## Dashboard API

### Endpoint

- `GET /api/snapshot`

Требует:

- активную session cookie

### Что возвращает

API дашборда возвращает:

- общую сводку по автопарку;
- количество активных машин на текущий момент;
- количество выдач и возвратов на сегодня;
- количество выдач и возвратов на завтра;
- полные списки выдач и возвратов на сегодня;
- полные списки выдач и возвратов на завтра;
- состояние по страховкам, техосмотру и ТО.

### Ключевые поля ответа

- `date`
- `cars_count`
- `bookings_count`
- `active_today_count`
- `checkouts_today_count`
- `returns_today_count`
- `checkouts_tomorrow_count`
- `returns_tomorrow_count`
- `insurance_state`
- `insurance_note`
- `today`
- `tomorrow`

### Структура `today` и `tomorrow`

Каждый блок содержит:

- `date`
- `checkouts_count`
- `returns_count`
- `checkouts`
- `returns`

### Структура элемента в `checkouts` и `returns`

```json
{
  "id": 15,
  "client": "Ivan",
  "time": "14:30",
  "booking_time": "14:30",
  "car_id": 6,
  "car_name": "Tesla 3",
  "status": "reserved",
  "start": "2026-04-10T14:30:00",
  "end": "2026-04-12T12:00:00"
}
```

### Как использовать в интеграции

Для UI, уведомлений, расписания и синхронизации использовать:

- `today.checkouts`
- `today.returns`
- `tomorrow.checkouts`
- `tomorrow.returns`

Рекомендации:

- основное поле времени для отображения: `time`;
- название машины брать из `car_name`;
- не рассчитывать выдачи/возвраты самостоятельно, если они уже пришли из `/api/snapshot`;
- старые плоские поля `checkouts_today`, `returns_today`, `checkouts_tomorrow`, `returns_tomorrow` считать legacy-совместимостью.

## TODO API

### Получить список задач

Запрос:

- `GET /api/todo`

Ответ:

```json
{
  "items": [
    {
      "id": 1,
      "text": "Позвонить клиенту",
      "status": "remind",
      "created_by": "admin",
      "created_at": "2026-04-10T09:20:00"
    }
  ]
}
```

### Создать задачу

Запрос:

- `POST /api/todo`
- `Content-Type: application/json`

Тело:

```json
{
  "text": "Позвонить клиенту",
  "status": "remind"
}
```

Допустимые значения `status`:

- `remind`
- `dont_forget`
- `other`

Успешный ответ:

- HTTP `201`

### Обновить статус задачи

Запрос:

- `PATCH /api/todo/<task_id>`
- `Content-Type: application/json`

Тело:

```json
{
  "status": "dont_forget"
}
```

## Ошибки API

Нужно явно обрабатывать:

- `401 {"error":"unauthorized"}`
- `403 {"error":"forbidden"}`
- `404 {"error":"todo_not_found"}`
- `400 {"error":"invalid_json"}`
- `400 {"error":"missing_text"}`
- `400 {"error":"invalid_status"}`

Поведение:

- при `401` выполнить повторный логин и повторить запрос один раз;
- при `403` не повторять запрос бесконечно;
- при `400/404` пробрасывать понятную ошибку в вызывающий код.

## Требования к реализации клиента

При написании клиента:

- вынести интеграцию в отдельный модуль;
- использовать один переиспользуемый session-aware client;
- добавить таймауты на все запросы;
- добавить retry только для сетевых ошибок и `401`;
- не хардкодить URL, логин и пароль;
- покрыть клиент минимальными тестами.

Рекомендуемые методы клиента:

- `login()`
- `get_snapshot()`
- `list_todos()`
- `create_todo(text, status)`
- `update_todo_status(task_id, status)`

## Ожидаемая архитектура

Предпочтительно:

1. Конфиг из env.
2. HTTP client с cookie jar.
3. Логин при старте или перед первым запросом.
4. Отдельный слой API-клиента.
5. Отдельный слой адаптации под бизнес-логику проекта.

## Минимальный контракт клиента

Клиент должен уметь отдавать вызывающему коду:

- `snapshot.today.checkouts`
- `snapshot.today.returns`
- `snapshot.tomorrow.checkouts`
- `snapshot.tomorrow.returns`
- `todos.items`

Если проекту нужен внутренний DTO, разрешено преобразовывать ответ сервера, но поля ниже должны сохраняться:

- `car_name`
- `time`
- `client`
- `status`
- `start`
- `end`

## Пример клиента на Python

```python
import requests


class BookingCarsClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()

    def login(self):
        response = self.session.post(
            f"{self.base_url}/login",
            data={
                "username": self.username,
                "password": self.password,
            },
            timeout=self.timeout,
            allow_redirects=False,
        )
        if response.status_code not in (200, 302):
            raise RuntimeError(f"Login failed: {response.status_code}")

    def _get(self, path: str):
        response = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
        if response.status_code == 401:
            self.login()
            response = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _request_json(self, method: str, path: str, payload: dict):
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code == 401:
            self.login()
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout,
            )
        response.raise_for_status()
        return response.json()

    def get_snapshot(self):
        return self._get("/api/snapshot")

    def list_todos(self):
        return self._get("/api/todo")

    def create_todo(self, text: str, status: str):
        return self._request_json("POST", "/api/todo", {"text": text, "status": status})

    def update_todo_status(self, task_id: int, status: str):
        return self._request_json("PATCH", f"/api/todo/{task_id}", {"status": status})
```

## Что не делать

- Не использовать HTML-страницы вместо API.
- Не парсить интерфейс, если есть `/api/snapshot` и `/api/todo`.
- Не хардкодить cookie.
- Не дублировать на клиенте серверную логику расчёта выдач и возвратов.
- Не логировать секреты.
