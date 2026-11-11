# 01 — Plan Mode: уточнение архитектуры без кода

Прочитай PRD.md, DESIGN.md, USER_FLOW.md, CODEX_PLAN.md, README_HANDOFF.md и последние четыре пользовательских референса в docs/reference. Сверь с результатами 00_environment_audit.

**Не пиши код.** Составь короткий пошаговый план для ИДЗ1 до 11 ноября и до 19 ноября. Выдели обязательный end-to-end user flow. Зафиксируй техстек: React/Vite/shadcn UI, Milkdown Crepe WYSIWYG + CodeMirror Markdown source, FastAPI+SQLite+single worker+existing konspekt adapter. Никакого самописного редактора.

Учитывай: Quartz — static-site generator; для privacy/multi-user dashboard нужна динамическая часть. На MVP 3-column Quartz-like reader в React; отдельно в backlog экспорт публичных заметок в fork Quartz, после аудита имеющегося hse-notes. Нельзя публично размещать mock-auth/private data. Укажи, какие части Quartz реально переиспользуем, не выдавай CSS-копию за реальный Quartz.

Представь:
1. Минимальный список экранов, переходов и 3–5 функциональных блоков.
2. Техническую структуру файлов и модели данных.
3. Риски Milkdown Markdown round-trip и ассетов/KaTeX.
4. Риски вызова Bash-функции в non-interactive backend.
5. Три ручные приемочные проверки и критерии остановки.
6. Что будет Mock к 11 ноября, что должно стать реальным к 19-му, а что переносится после ИДЗ.

Сохрани план в новый файл `docs/plan-2026-11-10.md` только после моего одобрения. Ничего не устанавливай до одобрения.
