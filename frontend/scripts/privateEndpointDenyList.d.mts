/**
 * Type declaration for scripts/privateEndpointDenyList.mjs (the README-style
 * adjacent `foo.mjs` + `foo.d.mts` pattern). The runtime module is a plain JS
 * module shared by the stdlib-only scanner and the vitest suite; this
 * declaration gives the STATIC import in phase24PrivateEndpointGuard.test.ts
 * real types. Values are pinned at runtime by
 * phase24BundleScanHygiene.test.ts (the deny-list module checks), so this
 * file cannot silently drift from the module it declares.
 */
export declare const LOCALHOST_OLLAMA: string;
export declare const SAME_ORIGIN_ROOT: string;
export declare const LOCALHOST_HTTP_PATTERNS: readonly RegExp[];
export declare const PRIVATE_HOST_PATTERNS: readonly RegExp[];
export declare const PROVIDER_PORT_PATTERNS: readonly RegExp[];
export declare const LOCALHOST_OLLAMA_PATTERNS: readonly RegExp[];
export declare const DENY_PATTERNS: readonly RegExp[];