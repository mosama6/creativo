export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type GenerationStatus =
  | "rejected"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type Generation = {
  id: string;
  status: GenerationStatus;
  type: "image" | "video";
  mode: string;
  model: string;
  prompt: string;
  prompt_final: string | null;
  parameters: Record<string, unknown>;
  credit_price: number;
  failure_code: string | null;
  failure_message: string | null;
  output: { index: number; content_type: string; byte_size: number; gpu_millis: number } | null;
  outputs: { index: number; content_type: string; byte_size: number; gpu_millis: number }[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type Model = {
  id: string;
  display_name: string;
  provider: string;
  modality: string;
  enabled: boolean;
  status: string;
  summary: string;
  version: {
    credit_cost: number;
    pricing_status: string;
    capabilities: {
      aspect_ratios?: string[];
      resolutions?: string[];
      text_to_image?: boolean;
      image_to_image?: boolean;
      supports_batching?: boolean;
      max_batch_size?: number;
    };
  } | null;
};

export type Credits = { available: number; reserved: number };

export type Transaction = {
  id: string;
  type: string;
  amount: number;
  available_after: number;
  reserved_after: number;
  description: string;
  generation_id: string | null;
  created_at: string;
};

export type User = {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  created_at: string;
};

export type Asset = {
  id: string;
  content_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  created_at: string;
};

export class ApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

type Options = {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  headers?: HeadersInit;
};

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-Creativo-Client", "web");
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? (options.json ? "POST" : "GET"),
    headers,
    credentials: "include",
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = payload.error ?? {};
    throw new ApiError(
      response.status,
      error.code ?? "request_failed",
      error.message ?? "Request failed.",
      error.details ?? {},
    );
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const type = response.headers.get("content-type") ?? "";
  if (type.includes("application/json")) {
    return response.json() as Promise<T>;
  }
  return undefined as T;
}

export function newIdempotencyKey() {
  return `web_${crypto.randomUUID().replace(/-/g, "")}`;
}
