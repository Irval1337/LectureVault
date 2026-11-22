# LectureVault — архитектура и структура данных

Актуально на 22 ноября 2026 года. Схема сверена с `backend/db.py`, клиентские типы — с `web/src/data/model.ts` и `web/src/data/store.ts`.

## 1. Компоненты

- **Web:** React + TypeScript + Vite. Интерфейс библиотеки, регистрации, редактора Milkdown Crepe/CodeMirror и Quartz-ридера. Обращается к серверу по `/api`.
- **API:** FastAPI (`backend/api.py`, `backend/auth.py`). Управляет сессиями, категориями, документами, загрузками, заданиями и правами доступа.
- **Хранилище:** SQLite в режиме WAL (`backend/db.py`), папки с вложениями и заданиями в Docker volumes. В рабочем интерфейсе серверная БД, а не `localStorage`, — источник истины для библиотеки.
- **Worker:** `backend/worker.py`; выполняет стадии генерации и сохраняет результаты. **Media** обрабатывает локальное видео и PDF с помощью ffmpeg, Whisper и Poppler; **Providers** работает с подключенными AI-моделями; **Mailer** отправляет служебные письма (локально — Mailpit).
- **Docker Compose:** отдельные контейнеры `web`, `api`, `worker`, `media`, `providers`, `mailer` и локальный `mailpit`. Для публикации подготовлен отдельный HTTPS-overlay, но публичный домен и внешний SMTP не настроены.

## 2. Основная схема данных (SQLite)

Реальное определение таблиц и миграции: `backend/db.py`, функция `initialize()`.

| Таблица | Основные поля | Назначение |
| --- | --- | --- |
| `users` | `id` (UUID), `email`, `name`, `password_hash`, `verified`, `created_at`, `disabled` | Учетные записи. Пароли хранятся в виде Argon2id-хешей. |
| `sessions` | `digest`, `user_id`, `expires`, `created_at`, `last_seen` | Вход в приложение. В браузере — HttpOnly-cookie; в БД — хеш значения сессии. |
| `auth_tokens` | `digest`, `user_id`, `purpose`, `expires` | Одноразовое подтверждение email и восстановление пароля. |
| `courses` | `id` (UUID), `title`, `workspace`, `owner_id`, `created_at` | Корневая категория (историческое название `Course`); `workspace` = `personal` / `shared`. |
| `members` | `course_id`, `user_id`, `role` | Совместный доступ; `role` = `editor` / `viewer`. Роль `owner` определяется по `courses.owner_id`. |
| `course_invites` | `course_id`, `digest`, `role`, `expires` | Ограниченные по времени ссылки-приглашения. |
| `category_folders` | `course_id`, `path`, `icon` | Вложенные категории, включая пустые. Иерархия задается строковым путём (`path`). |
| `course_icons` | `course_id`, `icon` | Значки корневых категорий. |
| `notes` | `id` (UUID), `course_id`, `title`, `markdown`, `origin`, `revision`, `created_at`, `updated_at`, `job_id`, `is_mock` | Ручные и сгенерированные документы в одном формате; `markdown` — канонический текст, `revision` используется для защиты от конфликтов. |
| `note_categories` | `note_id`, `path` | Положение документа во вложенной категории. |
| `jobs` | `id` (UUID), `course_id`, `created_by`, `payload`, `status`, `created_at`, `started_at`, `completed_at`, `attempt`, `note_id`, `error`, `error_code` | Очередь генерации. `payload` — JSON с параметрами источника, модели и результата. |
| `assets` | `id`, `course_id`, `note_id`, `job_id`, `name`, `mime`, `path`, `size`, `sha256` | Метаданные видео, PDF и изображений. Само содержимое — в файловых каталогах Docker volumes. |
| `media_results` | `course_id`, `digest`, `operation`, `variant`, `version`, `result`, `touched` | Кэш промежуточной обработки видео/PDF, изолированный по корневой категории. |
| `provider_links` | `user_id` | Состояние подключений провайдера; секреты и CLI-профили сохраняются отдельно от этой таблицы. |
| `imports` | `user_id`, `digest` | Идемпотентный перенос данных из старого клиентского прототипа. |
| `mail_outbox` | `id`, `recipient`, `payload`, `created_at`, `sent_at`, `attempts`, `next_attempt`, `claimed_at` | Очередь служебных писем. |
| `rate_limits` | `key`, `window`, `count`, `expires` | Ограничение частоты запросов и попыток входа. |

### Отношения и разделение пользователей

- `users (1) → (N) courses`: пользователь владеет собственными корневыми категориями через `owner_id`.
- `users (N) ↔ (N) courses` через `members`: другой подтвержденный пользователь получает только явно выданный доступ.
- `courses (1) → (N) notes`, `courses (1) → (N) jobs`, `courses (1) → (N) assets`.
- `notes (1) → (0..1) note_categories`: отсутствие записи означает корень категории; вложенность хранится в `path`.
- `jobs (0..1) → (1) notes` после успешной генерации: `jobs.note_id` указывает на итоговую заметку, а `notes.job_id` — на исходное задание.
- Каждая операция API проверяет пользователя и его роль в корневой категории. `viewer` может читать, `editor` — редактировать, `owner` — управлять доступом. Прямые запросы к вложениям также проверяют права.

## 3. Структура клиентских данных

Типы `Course`, `Note`, `Job`, `AppState` объявлены в `web/src/data/model.ts`.

- `Course`: `id`, `title`, `workspace`, `createdAt`, `ownerId`, `role`, список подкатегорий/значков.
- `Note`: `id`, `courseId`, `title`, `markdown`, `origin`, `revision`, `jobId`, `createdAt`, `updatedAt`, `category`.
- `Job`: `id`, `courseId`, `createdBy`, `status`, `attempt`, `noteId`, параметры источников/генерации и метаданные стадий. Статусы: `queued` → `acquiring` → `transcribing` → `preparing` → `generating` → `ready`; возможны `failed` и `cancelled`.
- `AppState`: `version`, `courses[]`, `notes[]`, `jobs[]`, `expandedCourses[]`, `readerTheme`. Приложение получает актуальные `courses/notes/jobs` через `/api/state`, содержимое заметки загружается отдельно по `/api/notes/{id}`.

Браузерный `localStorage` служит для **черновиков и настроек**, а не для серверных данных: `lecturevault.ui.<userId>`, `lecturevault.note-draft.<userId>.<noteId>`, `lecturevault.generation-draft.<userId>` и др. Тема также хранится в ключе `lecturevault.theme`. Устаревший `lecturevault.library.v1` используется только как источник **явного** импорта. Cookie сессии недоступна из JavaScript.

## 4. Сохранение и восстановление

- При создании/редактировании заметки API сохраняет Markdown в SQLite; счетчик `revision` позволяет вернуть **409 Conflict**, если документ параллельно изменился. Локальный черновик при этом не перезаписывается.
- После обновления страницы и повторного входа данные перечитываются из SQLite. Файлы и SQLite хранятся в именованных томах Docker (`data`, `jobs`, `profiles` и отдельных secret-volumes) и сохраняются после обычного `docker compose down` **без** `--volumes`.
- Файлы по отдельности не копируются в архив исходников; Docker volumes не входят в сдачу.
- Новая регистрация создаёт пустую библиотеку; предустановленных демопользователей в рабочей конфигурации нет.
- Для локальной проверки email используется Mailpit. Реальная генерация зависит от отдельного подключения Codex/внешнего API; без такого подключения основной сценарий ручной заметки продолжает работать.

## 5. Основной сценарий ИДЗ-1

Регистрация → подтверждение письма в локальном Mailpit → вход → создание категории → создание **ручной** заметки → правка и сохранение Markdown → открытие в Quartz-ридере → экспорт `.md` → перезагрузка страницы → повторное открытие сохраненного текста. Дополнительные сценарии: приглашение пользователя с ролью, очередь генерации из видео/PDF.

Подробнее: [PRD.md](PRD.md), [DESIGN.md](DESIGN.md), [USER_FLOW.md](USER_FLOW.md), [README.md](README.md).
