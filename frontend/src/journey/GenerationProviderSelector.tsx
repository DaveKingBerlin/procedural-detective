import type {
  GenerationCapabilitiesResponse,
  GenerationProviderId,
  OllamaTransportId,
} from "../api/types";
import {
  FAKE_PROVIDER_SUBTITLE,
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
 *     an endpoint, API key or configuration value (§1.3);
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

  const ollamaOffer = offers.find((offer) => offer.id === "ollama");
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
      const transport =
        selection.ollamaTransport ?? getOllamaTransport() ?? defaultOllamaTransport(ollamaOffer);
      onChange({
        generationProvider: "ollama",
        ollamaTransport: transport,
        ollamaModel:
          selection.ollamaModel !== "" ? selection.ollamaModel : (ollamaOffer?.defaultModel ?? ""),
      });
    } else {
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
    </div>
  );
}