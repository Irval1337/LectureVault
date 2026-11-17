export class ApiError extends Error {
  constructor(message: string, public status: number, public field?: string) { super(message) }
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, { credentials: 'same-origin', ...options, headers: { 'X-LectureVault': '1', ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers } })
  } catch { throw new ApiError('Сервер недоступен. Проверьте локальный запуск; ваши данные не удалены.', 0) }
  if (!response.ok) {
    const result = await response.json().catch(() => ({}))
    const detail = result.detail
    throw new ApiError(typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : response.status === 413 ? 'Файл превышает допустимый размер.' : 'Не удалось выполнить запрос. Проверьте введённые данные.', response.status, typeof detail?.field === 'string' ? detail.field : undefined)
  }
  return response.json()
}
