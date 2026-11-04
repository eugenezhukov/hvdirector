// Thin fetch wrapper: JSON, CSRF header, uniform errors.

let csrfToken = ''
let onUnauthorized = null

export function setCsrf(token) {
  csrfToken = token || ''
}

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

export class ApiError extends Error {
  constructor(status, detail) {
    super((detail && detail.message) || `HTTP ${status}`)
    this.status = status
    this.code = (detail && detail.code) || 'http_error'
    this.params = (detail && detail.params) || {}
  }
}

async function request(method, url, body, opts = {}) {
  const headers = { Accept: 'application/json' }
  if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = csrfToken
  let payload
  if (body instanceof Blob || body instanceof ArrayBuffer) {
    headers['Content-Type'] = 'application/octet-stream'
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let resp
  try {
    resp = await fetch(url, { method, headers, body: payload, credentials: 'same-origin', signal: opts.signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError(0, { code: 'network_error', message: e.message })
  }
  let data = null
  const text = await resp.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: { code: 'http_error', message: text.slice(0, 200) } }
    }
  }
  if (!resp.ok) {
    const detail = data && data.detail
    const err = new ApiError(resp.status, typeof detail === 'object' ? detail : { message: String(detail || '') })
    if (resp.status === 401 && onUnauthorized && !opts.noAuthRedirect) onUnauthorized(err)
    throw err
  }
  return data
}

export const api = {
  get: (url, opts) => request('GET', url, undefined, opts),
  post: (url, body, opts) => request('POST', url, body === undefined ? {} : body, opts),
  put: (url, body, opts) => request('PUT', url, body, opts),
  patch: (url, body, opts) => request('PATCH', url, body, opts),
  del: (url, opts) => request('DELETE', url, undefined, opts),
}
