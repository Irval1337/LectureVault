export class ApiError extends Error {
  constructor(message: string, public status: number, public field?: string, public code?: string) { super(message) }
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, { credentials: 'same-origin', ...options, headers: { 'X-LectureVault': '1', ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers } })
  } catch { throw new ApiError('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.', 0) }
  if (!response.ok) {
    const result = await response.json().catch(() => ({}))
    const detail = result.detail
    throw new ApiError(typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : response.status === 413 ? 'Файл превышает допустимый размер.' : 'Не удалось выполнить запрос. Проверьте введённые данные.', response.status, typeof detail?.field === 'string' ? detail.field : undefined, detail?.code)
  }
  return response.json()
}

export function uploadJob(body: FormData, progress: (percent: number) => void): Promise<{id: string}> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', '/api/jobs')
    request.responseType = 'json'
    request.setRequestHeader('X-LectureVault', '1')
    request.upload.onprogress = e => { if (e.lengthComputable) progress(Math.floor(100 * e.loaded / e.total)) }
    request.onload = () => {
      if (request.status === 201 && request.response?.id) return resolve(request.response)
      const detail = request.response?.detail
      reject(new ApiError(detail?.message || (typeof detail === 'string' ? detail : 'Не удалось загрузить материалы.'), request.status, detail?.field, detail?.code))
    }
    request.onerror = () => reject(new ApiError('Соединение прервалось. Повторите загрузку.', 0))
    request.send(body)
  })
}
