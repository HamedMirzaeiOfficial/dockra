export const BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export const TOKEN_KEY = "dockra_token";

export function getToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const memory = new Map<string, unknown>();

export function cached<T>(path: string): T | undefined {
  return memory.get(path) as T | undefined;
}

export function clearCache() {
  memory.clear();
}

// Carries the HTTP status and parsed body so callers can read extra fields (otp_required, retry_after).
export class ApiError extends Error {
  status: number;
  data: Record<string, unknown> | null;
  constructor(message: string, status: number, data: Record<string, unknown> | null) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const token = getToken();
  // A stale token must not break login itself.
  if (token && !path.includes("/auth/login")) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401 && !path.includes("/auth/login")) {
    memory.clear();
    localStorage.removeItem(TOKEN_KEY);
    window.location.href = "/login";
    throw new Error("Please sign in again");
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    if (res.ok) throw new ApiError("Invalid response from the server", res.status, null);
  }
  if (!res.ok) {
    const detail = data?.detail;
    // A proxy or crashed API answers with an HTML page; say what happened instead of a parse error.
    const fallback = res.status >= 500 ? `Server error (HTTP ${res.status}); check the api service logs` : `Request failed (HTTP ${res.status})`;
    throw new ApiError(typeof detail === "string" ? detail : fallback, res.status, data);
  }
  if (!init.method || init.method === "GET") memory.set(path, data);
  return data as T;
}

// Binary responses (backups, disk files) need the bearer token, so a plain <a href> won't do.
export async function download(path: string, fallback = "download") {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(typeof data?.detail === "string" ? data.detail : "Download failed");
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? decodeURIComponent(match[1]) : fallback;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Large raw uploads (backups, archives) with progress; fetch() cannot report upload progress.
export function upload<T>(path: string, body: Blob, onProgress?: (fraction: number) => void, method = "POST"): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, `${BASE}${path}`);
    for (const [key, value] of Object.entries(authHeaders())) xhr.setRequestHeader(key, value);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let data = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(typeof data?.detail === "string" ? data.detail : `Upload failed (HTTP ${xhr.status})`, xhr.status, data));
    };
    xhr.onerror = () => reject(new Error("Connection lost; please try again"));
    xhr.send(body);
  });
}

const PIECE = 4 << 20;

// One long request dies at whatever proxy/CDN timeout sits in front of the API; small pieces each finish
// quickly, and a dropped piece is retried instead of restarting the whole file. Returns the upload id, which
// the real endpoint (database import/restore, disk import/upload) takes as `upload`.
export async function uploadInPieces(body: Blob, onProgress?: (fraction: number) => void): Promise<string> {
  const path = "/api/uploads";
  const { upload: id } = await api<{ upload: string }>(path, { method: "POST", body: "{}" });
  let offset = 0;
  let failures = 0;
  while (offset < body.size) {
    const piece = body.slice(offset, offset + PIECE);
    const start = offset;
    try {
      const res = await upload<{ size: number }>(`${path}/${id}?offset=${start}`, piece, (f) => onProgress?.((start + f * piece.size) / body.size), "PUT");
      offset = res.size;
      failures = 0;
    } catch (err) {
      // The piece arrived but its answer was lost: the server says where to continue.
      if (err instanceof ApiError && err.status === 409 && typeof err.data?.size === "number") {
        offset = err.data.size;
        continue;
      }
      const retryable = !(err instanceof ApiError) || err.status >= 500 || err.status === 408 || err.status === 429;
      if (!retryable || ++failures > 10) throw err;
      await new Promise((resolve) => setTimeout(resolve, Math.min(30000, 1000 * 2 ** failures)));
    }
  }
  onProgress?.(1);
  return id;
}

// SQL dumps shrink several times with gzip; the server accepts both. Already-compressed formats are sent as is.
export async function gzipForUpload(file: File): Promise<Blob> {
  const head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
  const magic = String.fromCharCode(...head);
  const compressed = (head[0] === 0x1f && head[1] === 0x8b) || magic === "PGDMP" || magic === "REDIS";
  if (compressed || typeof CompressionStream === "undefined" || file.size < 1 << 20) return file;
  return new Response(file.stream().pipeThrough(new CompressionStream("gzip"))).blob();
}
