# LectureVault — безопасность и эксплуатация

Актуально на 20 ноября 2026. Настоящие аккаунты заменили учебный вход. Подготовлены локальный Compose и production overlay; фактическая публикация, DNS и внешняя почта ещё не настроены владельцем.

## Границы доверия

- WebUID101; API/worker/mailer/media/providersUID10001, read-only rootfs, cap_drop ALL, no-new-privileges, лимиты памяти/CPU/PIDs/tmpfs/logs. Docker socket, privileged, host home, личный vault и host CLI profiles не подключаются.
- Media: отдельная internal-сеть processing, только jobs и собственный RPC secret, без интернета, AI-профилей и базы. ffmpeg/Whisper/Poppler запускаются с Landlock-доступом только к одному заданию, системным бинарникам/моделям и временным файлам.
- Providers: отдельный broker с UUID-профилями; официальный CLI запускается через Linux Landlock с доступом только к собственным home/work/tmp, системным бинарникам/CA. Не может читать другой профиль, broker encryption key, RPC secret или глобальный `/proc`. ABI3+ проверяется при старте, unsupported kernel отказывает. Broker остаётся доверенным компонентом; это не изоляция от администратора сервера или компрометации самого broker.
- Worker не имеет auth/SMTP secret или CLI homes; передаёт только ограниченный текст своей задачи. API/mailer имеют собственный auth volume; RPC secrets разделены по сервисам. Mailer имеет SMTP egress; media egress не имеет.
- Все CLI argv статические, model/provider/effort из server allowlist, prompt через stdin как данные. Shell/eval/произвольных флагов нет. Codex tools отключены ограниченным каталогом, собранным из метаданных CLI, и фиксированными флагами, Claude — tools/MCP/settings. Prompt injection не даёт прав на shell/чужие файлы/публикацию.

Broker шифрует адрес и ключ подключения пользователя. Для новых заданий использует Chat Completions и фиксирует выбранный Base URL на момент отправки. Перед запросом проверяет DNS-адреса, соединяется с проверенным IP, сохраняет исходные Host и TLS SNI, отключает редиректы и прокси окружения, ограничивает размер ответа и время ожидания. Для внешних серверов требуется HTTPS. Точные приватные базы из `LV_LOCAL_MODEL_BASES` могут использовать HTTP; оператор выбирает, какие локальные модели доступны пользователям. Политика хранения отправленных материалов определяется выбранным провайдером.

Для каждой страницы PDF API сначала проверяет членство в курсе. Media возвращает JPEG с длинной стороной до 1250 px. Одновременно работает один рендер страниц, в памяти media остаётся до 12 изображений; новые файлы не добавляются в базу. Сведения о проценте распознавания берутся из вывода Whisper.

## Аккаунты и права

Argon2id64МБ, ограничение параллельного hashing; пароль15–128символов. Email нормализуется, подтверждение24ч и восстановление30мин единоразовые, digest в DB, письмо в outbox зашифровано; успешная отправка стирает payload. Ссылки во fragment URL, не query; API access logs отключены, чувствительный ввод не включается в validation errors. Persistent rate limits по IP/email/action.

Opaque session cookie: HttpOnly/SameSite=Strict, в production Secure и __Host prefix без Domain;8ч максимум/2ч бездействия. Пароль/reset/logout-all отзывают сессии. Чувствительные подключения требуют пароль после15мин. Exact Origin/TrustedHost + custom CSRF header; только trusted ingress перезаписывает client IP. API порт наружу не опубликован. TLS/CSP/nosniff/no-referrer защищают браузерную границу. Клиент не получает CLI access/refresh tokens; личный ключ Anthropic или OpenAI вводится по HTTPS, хранится зашифрованным на сервере и не возвращается API/localStorage.

Каждый курс/Note/job/asset/export проверяет caller и membership. Owner управляет курсом/ролями; editor пишет документы, viewer только читает/экспортирует. Общий курс не означает публичность и не добавляет людей автоматически. Управление job принадлежит отправителю. Revision409 не перезаписывает черновик. API сериализует изменение подключения и удаление аккаунта; прерванное удаление можно повторить после перезапуска. Публичных/unlisted страниц нет.

## Недоверенные файлы и лимиты

Видео512МБ/2ч, PDF50МБ/100стр, PNG/JPEG/WebP10МБ; SVG upload/unsafe HTML/внешние embeds запрещены. UUID-имена, signature checks, path confinement/symlink checks, ограничения вывода/страниц/времени, no-root парсеры. До парсинга multipart API проверяет авторизацию, Nginx ограничивает тело. На владельца курса1ГБ assets/5000Note/25МБ Markdown/100курсов, всего10ГБ assets, на отправителя10активных/30новыхjobs в сутки. Финальные квоты перепроверяются транзакцией после upload. Видео/PDF не исполняются. Реальные URL/yt-dlp выключены: SSRF-путь не открыт, обхода через пользовательский endpoint нет.

Транскрипт140000символов/PDF40000 не обрезаются молча. Все subprocess имеют timeout/process-group kill и bounded output. CPU Whisper tiny зафиксирован revision/SHA-256; GPU runtime отсутствует и не меняется. Качество длинных записей, OCR, кодеки всех возможных файлов и CUDA не гарантируются.

## Локальный запуск

`docker compose up -d --build` → localhost5180; Mailpit localhost8026 получает только локальные письма. Разработка — localhost5173 после Compose. Порты bind127.0.0.1. Новый namespace lecturevault-service сохраняет старые учебные volumes lecturevault отдельно. Они не удаляются/не копируются в новые аккаунты. `docker compose down` сохраняет данные; `down --volumes` для рабочих volumes не использовать.

Dev/test отличаются от production: HTTP cookie без Secure допустим только локально, Mailpit не доставляет внешнюю почту. Mock-job принимается исключительно в LV_ENV=test; development/production его отвергают. `/demo`, seeded accounts/courses и mock-переключатели удалены из рабочего приложения. Существующий browser localStorage не очищается; явный import сохраняет оригинал.

## Production с доменом и SMTP

Нужны Linux Landlock ABI3+, Docker Compose2.24.4+, достаточный диск/RAM, домен с A/AAAA на сервер, открытые80/443, SMTP с TLS и адрес поддержки. Проверить доступность своего AI-провайдера из региона хостинга. У владельца пока нет домена/SMTP; следующие команды — инструкция для их последующей настройки.

1. Скопировать `.env.example` в приватный `.env.production`, заполнить LV_DOMAIN, LV_ACME_EMAIL, LV_SUPPORT_EMAIL, LV_SMTP_HOST/PORT/TLS/USER и подтверждённый LV_MAIL_FROM. Для587 — starttls, для465 — ssl. Настроить SPF/DKIM/DMARC у почтового провайдера. Файл исключён из Git; пароли/CLI credentials в него не записывать.
2. Собрать конфигурацию и инициализировать SMTP secret от UID10001:

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml config --quiet
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml build
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml run --rm -it --no-deps api python -m backend.manage smtp-password
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml run --rm --no-deps api python -m backend.manage check-config
```

Пароль вводится скрыто и записывается mode0600 в отдельный secret-auth volume. Он не появляется в аргументах, env, Git или browser. `check-config` не заявляет проверку сетевой доступности/доставки SMTP.

3. После DNS/SMTP и проверки конкретной конфигурации запустить:

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.prod.yaml up -d --build
```

Production overlay убирает web loopback publish, не запускает Mailpit и публикует только Caddy80/443. Caddy получает настоящий ACME-сертификат и хранит его в persistent TLS volumes. API отказывает без HTTPS canonical origin, email verification, корректных sender/support/TLS SMTP и SMTP secret. Порт1455 не используется. Не выдавать dev-инстанс наружу вместо production overlay.

4. Фактически проверить с внешнего браузера: доверенный сертификат/HTTP→HTTPS, registration/verification, реальное письмо во входящих, recovery, Secure cookie, две независимые учётные записи/ACL, ручную Note/save/reload/export; затем собственный Codex login/короткую генерацию. Проверить логи на delivery_failed, запас диска и сохранность volumes после restart. Локальный test certificate не заменяет эту проверку.

Домен, SMTP, почтовая репутация, актуальные юридические тексты оператора и политика хранения/резервного копирования зависят от места публикации. В интерфейсе есть фактическое описание данных и контакт поддержки из конфигурации; соответствие произвольной юрисдикции не объявляется автоматически.

## Резервные копии и восстановление

CLI `python -m backend.manage backup --output PATH` и `restore --input PATH` запускается от UID10001. Backup включает SQLite snapshot, assets и jobs; sessions, одноразовые ссылки и outbox удаляются из самого архивного файла SQLite. В копии также удаляются счётчики запросов и признаки подключений, пароль заменяется неиспользуемым хешем. Архивы прежних версий могут содержать старые записи сессий, хеши паролей и зашифрованный outbox. Храните их как приватные данные. CLI profiles/SMTP/encryption keys не включаются. Храните архив с правами 0600 на зашифрованном диске вне Git и публичного webroot.

Остановить API/worker/mailer, чтобы файлы и DB не менялись. Пример для локального Compose (production — добавить env-file и оба compose-файла ко всем командам):

```bash
docker compose stop api worker mailer
docker compose run --rm -T --no-deps -e TMPDIR=/backups -v lecturevault-backups:/backups api python -m backend.manage backup --output /backups/snapshot.tar.gz
umask 077
docker compose run --rm -T --no-deps -v lecturevault-backups:/backups api cat /backups/snapshot.tar.gz > ./lecturevault-private-backup.tar.gz
docker compose start api worker mailer
```

Служебный backup volume содержит приватный архив; перенести копию в выбранное шифрованное хранилище и применять свою политику удаления. Имена snapshot не перезаписываются; для следующего выбирать новый PATH. `TMPDIR=/backups` нужен для больших архивов: обычный runtime tmpfs ограничен. Backup на том же диске не защищает от его потери.

Restore допускается **только в новый Compose project с пустыми data/jobs/profiles/auth volumes**, никогда поверх рабочей системы. Это обязательное условие: старые CLI profiles не должны пережить восстановление аккаунтов. Передать архив в доступный UID10001 `/backups` через отдельный scratch volume, запустить operator CLI с TMPDIR=/backups; он проверяет tar paths/links/devices/size, DB integrity, переписывает server-owned asset roots. Не выполнять распаковку недоверенного архива от root. Для небольших/больших архивов одинаковый способ без host mounts:

```bash
docker compose -p lecturevault-restored run --rm -T --no-deps -e TMPDIR=/backups -v lecturevault-restore-scratch:/backups api python -c 'import shutil,sys; from pathlib import Path; from backend.config import Settings; from backend.manage import restore; p=Path("/backups/input.tar.gz"); f=p.open("wb"); shutil.copyfileobj(sys.stdin.buffer,f); f.close(); restore(Settings(),p)' < ./lecturevault-private-backup.tar.gz
```

Не запускать API перед restore: он заполнит пустую базу. После restore настроить новый SMTP secret/домен, поднять новый project на свободном порту. Пользователи восстанавливают пароли через email и подключают AI заново. Незаконченные jobs становятся failed/RESTORED; готовые документы и вложения сохраняются. Старый project/volumes не удаляются. Процедуру регулярно проверять на изолированной копии.

## Проверки и ограничения доказательств

API-тесты проверяют реальные регистрации/пароли/сессии/ACL/quota/revision/backup. Browser suite создаёт свой process-specific Compose project, настоящие SMTP-письма и synthetic accounts; удаляет только собственные ресурсы. Queue mock разрешён лишь в test. Screenshots/traces в ignored test-results; медиа/DB/secrets/кэши не входят в исходники/build context.

`tests/run_https.py` проверяет production overlay с локальным CA/Caddy, настоящим SMTP AUTH+STARTTLS, __Host Secure cookie, email confirmation, manual Note/reload/logout и HSTS/CSP. Host CA не меняется, публичный ACME/доставляемость не проверяются. Новая Landlock media проверена реальным коротким JFK-фрагментом и синтетическим PDF подUID10001; CLI isolation — чтением своего и запретом соседнего профиля/secrets, запуском официальных CLI/device flow. После личного подтверждения пользователем нового зарегистрированного Codex-профиля проверена настоящая цепочка короткое видео+PDF→Whisper→Codex (`gpt-6-luna`,low) подUID10001/Landlock. Проверка использовала временную БД и каталог задания, рабочая библиотека не наполнялась; прежние credentials не передавались новому аккаунту. Claude API без действующего ключа не объявляется проверенным.

Нет обещания абсолютной безопасности или промышленного масштаба: один API-процесс, один worker, SQLite и доверенный CLI broker рассчитаны на небольшой экземпляр. Backups и наблюдение за mail/disk остаются обязанностью оператора; горизонтальное масштабирование и независимый внешний pentest не проводились. Настройки личных инструментов/host CLI не менялись, публикация и push автоматически не выполняются.

Проверка зависимостей 20 ноября 2026: `npm audit`, `npm audit --omit=dev` и `pip-audit` по зафиксированным backend-зависимостям не нашли известных уязвимостей. Ранее упомянутое предупреждение по picomatch было отозвано 18 ноября. [История advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf).
