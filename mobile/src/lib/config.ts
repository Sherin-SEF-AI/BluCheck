import Constants from "expo-constants";

// Default points at the live HTTPS API (CloudFront in front of the ALB). A build-time
// API_BASE_URL (via app.config extra) overrides it for other environments.
const DEFAULT_API_BASE_URL = "https://d1sc0mm026oa3r.cloudfront.net";

export const API_BASE_URL: string =
  (Constants.expoConfig?.extra?.apiBaseUrl as string) || DEFAULT_API_BASE_URL;

// Multipart part size. Must match server-side part sizing expectations.
export const PART_SIZE = 8 * 1024 * 1024; // 8 MiB
export const MAX_PART_RETRIES = 6;
