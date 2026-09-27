/**
 * Client defaults, defined once for the TypeScript package.
 *
 * `DEFAULT_BASE_URL` is the only default API origin in this package. Pass
 * `baseUrl` to use another origin. Once the pinned OpenAPI document declares
 * `servers`, this value must equal `servers[0].url`; the repository's
 * public-boundary check enforces that.
 */
export const DEFAULT_BASE_URL = "https://api.itmatrixhq.com";
