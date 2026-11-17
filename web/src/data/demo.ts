import markdown from '../../../fixtures/workspace-reader.md?raw'
import { initialState, type AppState, type Note } from './model'

// Read-only examples for /demo. Never merged into the user's localStorage library.
export function demoState(): AppState {
  const createdAt = new Date('2026-11-10T09:00:00+03:00').getTime()
  const courses: AppState['courses'] = [
    { id: 'distributed', title: 'Распределённые системы', workspace: 'personal', createdAt },
    { id: 'databases', title: 'Разработка СУБД', workspace: 'personal', createdAt },
    { id: 'gpu', title: 'Вычисления на GPU', workspace: 'personal', createdAt },
  ]
  const samples = [
    ['replication', 'distributed', 'Репликация и консистентность', markdown],
    ['clocks', 'distributed', 'Логические часы и порядок событий', '## Отношение happens-before\n\nЧасы Лэмпорта упорядочивают причинно связанные события. Обратное неверно: порядок меток не доказывает причинность.\n\n## Обновление часов\n\nПри получении сообщения: $C := \\max(C, t) + 1$.\n\n## Векторные часы\n\nВектор позволяет обнаружить конкурентные события, когда ни один вектор не предшествует другому.'],
    ['consensus', 'distributed', 'Консенсус: лидер и большинство', '## Выбор лидера\n\nКаждая эпоха имеет уникальный номер. Голосование большинства препятствует появлению двух лидеров в одной эпохе.\n\n## Фиксация журнала\n\nЗапись считается зафиксированной после подтверждения большинства при соблюдении правил протокола.'],
    ['buffer', 'databases', 'Буферный пул и вытеснение страниц', '## Страница в памяти\n\nБуферный пул хранит страницы с диска. Дескриптор содержит номер страницы, dirty flag и счётчик закреплений.\n\n## Политика вытеснения\n\nLRU приближает рабочий набор, но последовательное сканирование может вытеснить полезные страницы.\n\n```text\npin(page) -> read/update -> unpin(page)\n```'],
    ['wal', 'databases', 'WAL и восстановление после сбоя', '## Write-ahead logging\n\nИзменение журнала сохраняется раньше изменённой страницы.\n\n## Восстановление\n\nАнализ, повтор зафиксированных изменений и откат незавершённых транзакций.'],
    ['index', 'databases', 'B+ дерево: поиск и вставка', '## Структура индекса\n\nВнутренние узлы содержат разделители, листья — ключи и ссылки на записи.\n\n## Разделение узла\n\nПри переполнении узел делится, разделитель поднимается в родителя. Высота растёт логарифмически.'],
    ['architecture', 'gpu', 'Архитектура GPU и модель SIMT', '## Потоки и варпы\n\nОдин варп выполняет общую инструкцию над разными данными. Расхождение ветвей снижает эффективный параллелизм.\n\n## Иерархия памяти\n\nРегистры, shared memory, кеши и глобальная память различаются по задержке и области видимости.'],
    ['memory', 'gpu', 'Доступ к памяти и coalescing', '## Объединённые обращения\n\nСоседние потоки обращаются к соседним адресам, чтобы уменьшить число транзакций памяти.\n\n## Пропускная способность\n\nПри оценке ядра сравнивают объём переданных байтов и число операций.'],
  ]
  const notes: Note[] = samples.map(([id, courseId, title, content], index) => ({ id, courseId, title, markdown: content.startsWith('# ') ? content : `# ${title}\n\n${content}`, jobId: `example-${id}`, createdAt: createdAt - index * 3600000 }))
  return { ...initialState(), courses, notes, expandedCourses: courses.map(c => c.id), jobs: notes.map(note => ({ id: note.jobId!, courseId: note.courseId, title: note.title, sourceType: 'file', sourceName: `${note.title}.mp4`, pdfName: note.id === 'replication' ? 'Репликация — слайды.pdf' : undefined, preset: 'lecture', length: 'detailed', instructions: '', timestamps: false, status: 'ready', createdAt: note.createdAt, completedAt: note.createdAt, attempt: 0, noteId: note.id })) }
}
