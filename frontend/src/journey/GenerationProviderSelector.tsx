import { useState } from "react";
import type {
  GenerationCapabilitiesResponse,
  GenerationProviderId,
  OllamaTransportId,
} from "../api/types";
import {
  FAKE_PROVIDER_SUBTITLE,
  FRONTIER_API_KEY_LABEL,
  FRONTIER_COST_ACKNOWLEDGEMENT_COPY,
  FRONTIER_KEY_HIDE_LABEL,
  FRONTIER_KEY_SHOW_LABEL,
  FRONTIER_PRIVACY_COPY,
  FRONTIER_PROVIDER_LABEL,
  FRONTIER_PROVIDER_PLACEHOLDER,
  MODEL_INPUT_LABEL,
  OLLAMA_TRANSPORT_BRIDGE_LABEL,
  OLLAMA_TRANSPORT_HEADING,
  OLLAMA_TRANSPORT_SERVER_LABEL,
  PROVIDER_UNAVAILABLE_LABEL,
  buildProviderOffers,
  defaultOllamaTransport,
  getOllamaTransport,
  hasGenerationProviderOffer,
  providerReasonLabel,
  sanitizeFrontierApiKey,
  type GenerationProviderSelection,
} from "./generationProvider";

/**
 * Phase 25 — compact browser-side AI-provider selector (rendered on /new
 * inside the generation controls).
 *
 * Renders the KNOWN providers from the PARSED capability DTO
 * (get /api/v1/generation-capabilities `providers[]` + `defaultProvider`):
 *   - Demo / Fake  — "No external AI request" (deterministic, no LLM call);
 *   - Local Ollama — transport (Server / Direct vs My device via Bridge) + a
 *     model TEXT input whose initial value is the server-CONFIGURED default
 *     model (model names are NEVER hard-coded here — the user may replace
 *     the value with any valid Ollama model string, §2);
 *   - Frontier     — server-configured; shown disabled with its safe reason
 *     when not configured.
 * Phase 30 — when the backend reports the frontier offer AVAILABLE, Frontier
 * is selectable and the ACTIVE frontier selection reveals the BYOK panel
 * (§5): a provider dropdown sourced from the server's trusted catalog
 * (`providers[]` ids+labels only — §9, no URL/endpoint anywhere), an API-key
 * password input with a Show/Hide toggle, a Model input and a cost-
 * acknowledgement checkbox. The API key is MEMORY-ONLY (component state +
 * in-memory JourneyParams): it is NEVER persisted to sessionStorage/localStorage,
 * is cleared when the Frontier provider changes (each provider owns a
 * different key) and is cleared (dropped) when the user switches away from
 * Frontier (§15/§24). The generation submit is gated until provider + key +
 * model + cost ack are all present (`isFrontierSubmitReady` in
 * src/journey/generationProvider.ts).
 * Unavailable providers stay VISIBLE but DISABLED with the safe reason; the
 * current/default provider is preselected (server `defaultProvider`, a valid
 * sessionStorage choice — resolved by src/journey/generationProvider.ts —
 * or the first still-available provider).
 *
 * The component is CONTROLLED: the parent owns the selection state and the
 * sessionStorage persistence (`onChange`). `disabled` (Phase 25 §10.2) locks
 * every control while a case generation is being submitted.
 *
 * Hard guarantees:
 *   - only labels/models/reasons that survived the trust-boundary sanitizer
 *     are rendered — no secret, URL, IP, credential or raw exception text can
 *     reach the DOM (§1.4 / §2);
 *   - the browser only ever supplies logical ids and a model STRING — never
 *     an endpoint (the Frontier provider dropdown emits a catalog id, never
 *     a URL, §6);
 *   - the Frontier API key is stored ONLY in component/in-memory state: it is
 *     never persisted by anything this component calls (§15/§24);
 *   - when the additive provider offer is ABSENT (older server) this
 *     component renders NOTHING and the /new page stays byte-identical.
 */
export interface GenerationProviderSelectorProps {
  /** The PARSED capability DTO (the route's own allowlist-parsed value). */
  capabilities: GenerationCapabilitiesResponse | null;
  /** The resolved browser selection (null while the route is still loading). */
  selection: GenerationProviderSelection | null;
  /** Phase 25 §10.2 — lock every control while a generation is submitting. */
  disabled?: boolean;
  /** Parent callback: persist + restage the new selection. */
  onChange: (selection: GenerationProviderSelection) => void;
}

export function GenerationProviderSelector({
  capabilities,
  selection,
  disabled = false,
  onChange,
}: GenerationProviderSelectorProps) {
  if (!hasGenerationProviderOffer(capabilities)) return null;
  const offers = buildProviderOffers(capabilities);
  if (offers.length === 0) return null;

  // Phase 30 — the Show/Hide toggle for the Frontier API-key field. The key
  // value itself lives in the PARENT's memory-only selection state; this local
  // boolean only controls whether the password input is redacted on screen.
  const [showKey, setShowKey] = useState(false);

  const ollamaOffer = offers.find((offer) => offer.id === "ollama");
  const frontierOffer = offers.find((offer) => offer.id === "frontier");
  const firstAvailable = offers.find((offer) => offer.available);
  // While the parent's effect has not materialized a resolved selection (or in
  // any null-selection render), the DEFAULT provider (when still available) is
  // preselected — then the first still-available provider, then deterministic
  // fake. This mirrors resolveProviderSelection's fallbacks (§10).
  const defaultProviderId = capabilities?.defaultProvider ?? null;
  const defaultAvailable =
    defaultProviderId !== null && offers.some((offer) => offer.id === defaultProviderId && offer.available);
  const currentId =
    selection?.generationProvider ??
    (defaultAvailable ? (defaultProviderId as GenerationProviderId) : firstAvailable?.id ?? "fake");

  const selectProvider = (id: GenerationProviderId) => {
    if (disabled || selection === null) return;
    if (id === "ollama") {
      // Phase 26C1 §2/§7/§8 — the transport is NEVER recomputed from
      // availability once a choice exists: keep the current selection's
      // transport, else restore the persisted preference (Fake -> Ollama
      // restores the intended transport), else the deterministic no-
      // preference default. Availability is display-only.
      //
      // INFONOTE A1a (documented fail-closed path): with BOTH transports
      // unavailable and no stored choice the deterministic default resolves
      // to `ollamaTransport: null` — the selector then provisions Ollama
      // WITHOUT a transport. This is NOT a silent fallback: the serialized
      // POST omits the transport, the backend answers 400
      // INVALID_GENERATION_PROVIDER, and the user sees the frozen safe
      // `invalidGenerationProvider` copy on submit (src/journey/demoFlow.ts).
      const transport =
        selection.ollamaTransport ?? getOllamaTransport() ?? defaultOllamaTransport(ollamaOffer);
      onChange({
        generationProvider: "ollama",
        ollamaTransport: transport,
        ollamaModel:
          selection.ollamaModel !== "" ? selection.ollamaModel : (ollamaOffer?.defaultModel ?? ""),
      });
    } else {
      // Phase 30 §24 — a NON-frontier selection carries NO frontier fields on
      // purpose: the memory-only API key and the cost acknowledgement are
      // DROPPED from state (switching away clears the secret) and the
      // non-secret frontier provider/model preferences stay inert in storage.
      onChange({ generationProvider: id, ollamaTransport: null, ollamaModel: "" });
    }
  };

  const selectTransport = (transport: OllamaTransportId) => {
    if (disabled || selection === null) return;
    onChange({ ...selection, ollamaTransport: transport });
  };

  const onModelChange = (model: string) => {
    if (disabled || selection === null) return;
    onChange({ ...selection, ollamaModel: model });
  };

  // Phase 30 — Frontier BYOK handlers. The KEY is the only sensitive piece:
  // it is memory-only, sanitized on input (CR/LF + control chars stripped),
  // cleared when the provider changes (a key belongs to ONE provider account)
  // and dropped entirely when the user switches away from Frontier.
  const selectFrontierProvider = (frontierProviderId: string) => {
    if (disabled || selection === null) return;
    if (frontierProviderId === selection.frontierProviderId) return;
    // Documented decision (§24): switching the Frontier PROVIDER clears the
    // re-entered API key (each provider account owns a different key); the
    // non-secret model preference and the generic cost acknowledgement are
    // preserved — only the key is sensitive here.
    onChange({ ...selection, frontierProviderId, frontierApiKey: "" });
  };

  const onFrontierKeyChange = (key: string) => {
    if (disabled || selection === null) return;
    onChange({ ...selection, frontierApiKey: sanitizeFrontierApiKey(key) });
  };

  const onFrontierModelChange = (frontierModel: string) => {
    if (disabled || selection === null) return;
    onChange({ ...selection, frontierModel });
  };

  const onFrontierAckChange = (frontierAck: boolean) => {
    if (disabled || selection === null) return;
    onChange({ ...selection, frontierAck });
  };

  // Phase 26C1 §5/§7 — the visible transport IS the selection's transport and
  // nothing else: a null selection means NO transport radio is checked (the
  // serialized payload omits the transport on that documented path), so the
  // visible control can never diverge from what will be posted.
  const currentTransport: OllamaTransportId | null = selection?.ollamaTransport ?? null;

  const ollamaActive = currentId === "ollama" && ollamaOffer !== undefined;
  const serverTransport = ollamaOffer?.transports.server;
  const bridgeTransport = ollamaOffer?.transports.bridge;
  const showBridgeControls = currentTransport === "bridge";
  const bridgeConnected = bridgeTransport?.connected === true;

  // Phase 30 — the BYOK Frontier panel is revealed when Frontier is the ACTIVE
  // provider AND the offer exists (it is offered/selectable per the catalog).
  const frontierActive = currentId === "frontier" && frontierOffer !== undefined;

  return (
    <div className="generation-provider" data-testid="generation-provider-selector">
      <p className="generation-provider-heading">AI Provider</p>
      {offers.map((offer) => {
        const disabledForOffer = disabled || !offer.available;
        const reason =
          providerReasonLabel(offer.reason) ??
          (offer.available ? null : PROVIDER_UNAVAILABLE_LABEL);
        return (
          <label
            key={offer.id}
            className={`provider-option${currentId === offer.id ? " provider-option--active" : ""}`}
            data-testid={`generation-provider-${offer.id}`}
          >
            <input
              type="radio"
              name="generation-provider"
              value={offer.id}
              checked={currentId === offer.id}
              disabled={disabledForOffer}
              onChange={() => selectProvider(offer.id)}
            />
            <span className="provider-option-label">{offer.label}</span>
            {offer.id === "fake" && <span className="provider-option-subtitle">{FAKE_PROVIDER_SUBTITLE}</span>}
            {!offer.available && reason !== null && (
              <span className="provider-option-reason" data-testid={`generation-provider-${offer.id}-reason`}>
                {reason}
              </span>
            )}
          </label>
        );
      })}

      {ollamaActive && (
        <div className="generation-ollama" data-testid="generation-ollama-controls">
          <p className="generation-ollama-transport-heading">{OLLAMA_TRANSPORT_HEADING}</p>
          {serverTransport !== undefined && (
            <label
              className="provider-option provider-option--transport"
              data-testid="generation-ollama-transport-server"
            >
              <input
                type="radio"
                name="generation-ollama-transport"
                value="server"
                checked={currentTransport === "server"}
                disabled={disabled || !serverTransport.available}
                onChange={() => selectTransport("server")}
              />
              <span className="provider-option-label">{OLLAMA_TRANSPORT_SERVER_LABEL}</span>
              {/* Phase 26C1 §5 — availability is DISPLAY-ONLY, shown independently
                  of the (authoritative) selected transport. */}
              <span
                className="provider-option-availability"
                data-testid="generation-ollama-transport-server-availability"
              >
                {serverTransport.available
                  ? "Available"
                  : (providerReasonLabel(serverTransport.reason) ?? PROVIDER_UNAVAILABLE_LABEL)}
              </span>
            </label>
          )}
          {bridgeTransport !== undefined && (
            <label
              className="provider-option provider-option--transport"
              data-testid="generation-ollama-transport-bridge"
            >
              <input
                type="radio"
                name="generation-ollama-transport"
                value="bridge"
                checked={currentTransport === "bridge"}
                disabled={disabled || !bridgeTransport.available}
                onChange={() => selectTransport("bridge")}
              />
              <span className="provider-option-label">{OLLAMA_TRANSPORT_BRIDGE_LABEL}</span>
              {/* Phase 26C1 §5 — availability is DISPLAY-ONLY, shown independently
                  of the (authoritative) selected transport. */}
              <span
                className="provider-option-availability"
                data-testid="generation-ollama-transport-bridge-availability"
              >
                {bridgeTransport.connected === true
                  ? "Connected"
                  : bridgeTransport.available
                    ? "Not connected"
                    : (providerReasonLabel(bridgeTransport.reason) ?? PROVIDER_UNAVAILABLE_LABEL)}
              </span>
            </label>
          )}
          {showBridgeControls && (
            <p
              className="generation-ollama-bridge-status"
              data-testid="generation-ollama-bridge-status"
              role="status"
            >
              {bridgeConnected ? "Bridge status: Connected" : "Bridge status: Not connected"}
            </p>
          )}
          <label className="generation-ollama-model" htmlFor="generation-ollama-model-input">
            {MODEL_INPUT_LABEL}
          </label>
          <input
            id="generation-ollama-model-input"
            data-testid="generation-ollama-model-input"
            type="text"
            maxLength={256}
            autoComplete="off"
            spellCheck={false}
            value={selection?.ollamaModel ?? (ollamaOffer?.defaultModel ?? "")}
            disabled={disabled}
            onChange={(event) => onModelChange(event.target.value)}
          />
        </div>
      )}

      {/* Phase 30 — BYOK Frontier panel (§5). Shown ONLY while Frontier is the
          ACTIVE provider. Every input is CONTROLLED by the memory-only parent
          selection: the provider dropdown carries the SERVER-CATALOG ids/labels
          (no URL/endpoint anywhere, §9), the API key is a password field with a
          Show/Hide toggle and is NEVER persisted (§15/§24), the model is plain
          text, and the cost acknowledgement is the §25 UI consent. */}
      {frontierActive && (
        <div className="generation-frontier" data-testid="generation-frontier-controls">
          <p
            className="generation-frontier-privacy"
            data-testid="generation-frontier-privacy-note"
          >
            {FRONTIER_PRIVACY_COPY}
          </p>
          <label className="generation-frontier-provider-label" htmlFor="generation-frontier-provider-select">
            {FRONTIER_PROVIDER_LABEL}
          </label>
          <select
            id="generation-frontier-provider-select"
            data-testid="generation-frontier-provider-select"
            value={selection?.frontierProviderId ?? ""}
            disabled={disabled}
            onChange={(event) => selectFrontierProvider(event.target.value)}
          >
            <option value="" disabled>
              {FRONTIER_PROVIDER_PLACEHOLDER}
            </option>
            {frontierOffer.providers.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
          <label className="generation-frontier-key-label" htmlFor="generation-frontier-key-input">
            {FRONTIER_API_KEY_LABEL}
          </label>
          <input
            id="generation-frontier-key-input"
            data-testid="generation-frontier-key-input"
            type={showKey ? "text" : "password"}
            maxLength={512}
            autoComplete="new-password"
            spellCheck={false}
            value={selection?.frontierApiKey ?? ""}
            disabled={disabled}
            onChange={(event) => onFrontierKeyChange(event.target.value)}
          />
          <button
            type="button"
            className="generation-frontier-key-toggle"
            data-testid="generation-frontier-key-toggle"
            disabled={disabled}
            onClick={() => setShowKey((current: boolean) => !current)}
          >
            {showKey ? FRONTIER_KEY_HIDE_LABEL : FRONTIER_KEY_SHOW_LABEL}
          </button>
          <label className="generation-frontier-model-label" htmlFor="generation-frontier-model-input">
            {MODEL_INPUT_LABEL}
          </label>
          <input
            id="generation-frontier-model-input"
            data-testid="generation-frontier-model-input"
            type="text"
            maxLength={256}
            autoComplete="off"
            spellCheck={false}
            value={selection?.frontierModel ?? ""}
            disabled={disabled}
            onChange={(event) => onFrontierModelChange(event.target.value)}
          />
          <label
            className="generation-frontier-ack"
            data-testid="generation-frontier-ack"
          >
            <input
              type="checkbox"
              name="generation-frontier-cost-ack"
              data-testid="generation-frontier-ack-checkbox"
              checked={selection?.frontierAck === true}
              disabled={disabled}
              onChange={(event) => onFrontierAckChange(event.target.checked)}
            />
            <span className="generation-frontier-ack-label">{FRONTIER_COST_ACKNOWLEDGEMENT_COPY}</span>
          </label>
        </div>
      )}
    </div>
  );
}