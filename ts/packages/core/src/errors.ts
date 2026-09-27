export type ErrorCode =
  | "unauthenticated" | "token_expired" | "invalid_ticket"
  | "not_entitled_tier" | "not_entitled_symbol" | "attestation_required"
  | "rate_limited" | "quota_exceeded" | "invalid_params" | "not_found"
  | "range_capped" | "partial_data" | "internal" | "unknown";

export class ITMError extends Error {
  readonly status: number;
  readonly code: ErrorCode;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;
  readonly retryAfterSeconds?: number;

  constructor(options: {
    status: number;
    code: ErrorCode;
    message: string;
    details?: Record<string, unknown>;
    requestId?: string;
    retryAfterSeconds?: number;
  }) {
    super(options.message);
    this.name = "ITMError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.requestId = options.requestId;
    this.retryAfterSeconds = options.retryAfterSeconds;
  }

  static async fromResponse(response: Response): Promise<ITMError> {
    let body: {
      error?: {
        code?: ErrorCode;
        message?: string;
        details?: Record<string, unknown> | null;
      };
    } = {};
    try {
      body = await response.json() as typeof body;
    } catch {
      // Upstream failures still receive one stable SDK error shape.
    }
    const retryAfter = response.headers.get("retry-after");
    return new ITMError({
      status: response.status,
      code: body.error?.code ?? "unknown",
      message: body.error?.message || response.statusText || "request failed",
      details: body.error?.details ?? undefined,
      requestId: response.headers.get("x-request-id") ?? undefined,
      retryAfterSeconds:
        retryAfter !== null && Number.isFinite(Number(retryAfter))
          ? Number(retryAfter)
          : undefined,
    });
  }
}
